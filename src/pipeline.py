"""End-to-end pipeline: Layer A -> Layer B -> validation gate -> database.

For one fund and one window (``DistributionPipeline.check``):

1. Layer A ``detect_distribution`` decides DECLARED / NOT_DECLARED / UNKNOWN.
2. Only DECLARED reaches Layer B, which walks the route decision tree over the documents the
   detector cited.
3. Every extracted figure passes the deterministic validators *before* it is stored.
   CRITICAL failures (bad date order, wrong currency, component sum mismatch...) go to
   ``review_queue`` and never reach ``distribution_event``. WARNINGs are stored and flagged.
4. Every HTTP response of the check is stored as a ``raw_document`` (exact bytes + sha256) and a
   ``crawl_log`` row; each event is linked to the document it came from (``event_evidence``).
5. The check itself (status, confidence, route taken, cost) is logged in ``detection_run`` and
   the fund's gap state (``fund_detection_state``) is updated.

All of it runs inside one transaction per check, so re-running the same day is idempotent.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import Engine

from src.database.connection import get_engine, init_db, session_scope
from src.database.models import RawDocument
from src.database.repository import (
    CROSS_SOURCE_CONFLICT,
    DistributionRepository,
    to_json,
)
from src.detector import detect_distribution
from src.extractor import ExtractionEngine
from src.http_client import HTTPClient, HTTPResponseRecord, RecordingHTTPClient
from src.logging_setup import correlation_scope, new_id
from src.models import (
    DetectionResult,
    DetectionStatus,
    ExtractedDistribution,
    ExtractionRoute,
)
from src.strategies import BaseDetectionStrategy
from src.universe_loader import UniverseFund, UniverseRegistry
from src.validators.dq_engine import DataQualityEngine

logger = logging.getLogger(__name__)

# Source registry rows (source_id, name, tier, url pattern). ToS decisions are recorded in
# docs/COMPLIANCE.md; until reviewed they stay PENDING_REVIEW.
SOURCE_REGISTRY = [
    (
        "sec_edgar_submissions_api",
        "SEC EDGAR Submissions API",
        1,
        "https://data.sec.gov/submissions/CIK{cik}.json",
    ),
    (
        "sec_edgar_filings",
        "SEC EDGAR filing documents (497, 19(a), 8-K)",
        1,
        "https://www.sec.gov/Archives/edgar/data/{cik}/...",
    ),
    (
        "sec_edgar_daily_index",
        "SEC EDGAR daily form index",
        1,
        "https://www.sec.gov/Archives/edgar/daily-index/{yyyy}/QTR{q}/form.{yyyymmdd}.idx",
    ),
    (
        "sedar_tmx_notices",
        "TMX / CDS distribution notices",
        1,
        "https://www.tmx.com/dividends/{ticker}",
    ),
    (
        "official_fund_sponsor_page",
        "Official fund sponsor distribution pages and releases",
        2,
        "{official_source_url}",
    ),
    (
        "public_market_data_crosscheck",
        "Public market data pages (corroboration only, disabled)",
        3,
        "https://finance.yahoo.com/quote/{ticker}",
    ),
]


def source_id_for_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    path = urlparse(url).path.lower()
    if host == "data.sec.gov":
        return "sec_edgar_submissions_api"
    if host.endswith("sec.gov"):
        return (
            "sec_edgar_daily_index" if "/daily-index/" in path else "sec_edgar_filings"
        )
    if host.endswith("tmx.com") or "cds" in host:
        return "sedar_tmx_notices"
    if "yahoo" in host or "marketwatch" in host:
        return "public_market_data_crosscheck"
    return "official_fund_sponsor_page"


def _content_type(rec: HTTPResponseRecord) -> str:
    if rec.content_type:
        return rec.content_type.split(";")[0].strip()
    body = rec.content_bytes or b""
    if body.startswith(b"%PDF-"):
        return "application/pdf"
    if body.lstrip()[:1] in (b"{", b"["):
        return "application/json"
    return "text/html"


@dataclass
class CheckSummary:
    fund_id: str
    window_start: date
    window_end: date
    status: str
    confidence: float | None
    route_taken: str | None = None
    events_inserted: int = 0
    events_unchanged: int = 0
    events_amended: int = 0
    cross_source_conflicts: int = 0
    sent_to_review: int = 0
    http_requests: int = 0
    duration_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)


class DistributionPipeline:
    """Runs checks and persists everything with provenance."""

    def __init__(
        self,
        engine: Engine | None = None,
        db_url: str | None = None,
        universe: UniverseRegistry | None = None,
        http_client: HTTPClient | None = None,
        strategies_factory=None,  # callable(http_client, universe) -> list[BaseDetectionStrategy]
        run_id: str | None = None,
    ) -> None:
        self.engine = engine or get_engine(db_url)
        init_db(self.engine)
        self.universe = universe or UniverseRegistry.from_json()
        self.http = (
            http_client
            if isinstance(http_client, RecordingHTTPClient)
            else RecordingHTTPClient(inner=http_client)
        )
        self.strategies_factory = strategies_factory
        self.extractor = ExtractionEngine(http_client=self.http, universe=self.universe)
        self.dq = DataQualityEngine()
        self.run_id = run_id or new_id("run")

    # ------------------------------------------------------------------
    def ensure_reference_data(self) -> dict[str, int]:
        """Upsert fund_master, share_class and source_registry from the universe."""
        with session_scope(self.engine) as session:
            repo = DistributionRepository(session)
            for src_id, name, tier, pattern in SOURCE_REGISTRY:
                repo.upsert_source_registry(src_id, name, tier, pattern)
            for f in self.universe.list_funds():
                repo.upsert_fund(
                    fund_id=f.fund_id,
                    fund_name=f.fund_name,
                    fund_family=f.fund_family,
                    country=f.country,
                    fund_type="ETF" if f.is_etf else "MUTUAL_FUND",
                    cik=f.cik,
                    sedar_id=f.sedar_id,
                )
                repo.upsert_share_class(
                    class_id=self.class_id(f),
                    fund_id=f.fund_id,
                    ticker=f.ticker,
                    sec_series_id=f.sec_series_id,
                    sec_class_id=f.sec_class_id,
                    fundserv_code=f.fundserv_code,
                    currency="CAD" if f.country == "CA" else "USD",
                    expected_frequency=f.expected_frequency
                    or ("MONTHLY" if f.is_monthly_payer else "QUARTERLY"),
                    is_monthly_payer=f.is_monthly_payer,
                    is_etf=f.is_etf,
                )
        n = len(self.universe.list_funds())
        return {
            "sources_registered": len(SOURCE_REGISTRY),
            "funds_registered": n,
            "share_classes_registered": n,
        }

    @staticmethod
    def class_id(fund: UniverseFund) -> str:
        return f"{fund.fund_id}_CLASS"

    # ------------------------------------------------------------------
    def check(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
        sweep_reason: str | None = None,
    ) -> CheckSummary:
        """Detect, extract, validate and store one fund x window."""
        fund = self.universe.get_fund(fund_id)
        if fund is None:
            raise ValueError(f"Unknown fund_id {fund_id}")

        with correlation_scope(run_id=self.run_id) as cid:
            t0 = time.monotonic()
            self.flush_http(
                fund.fund_id
            )  # persist any earlier fetches (e.g. sweep gate) first
            strategies: list[BaseDetectionStrategy] | None = (
                self.strategies_factory(self.http, self.universe)
                if self.strategies_factory
                else None
            )
            detection = detect_distribution(
                fund.fund_id,
                window_start,
                window_end,
                strategies=strategies,
                http_client=self.http,
                universe=self.universe,
            )
            outcome = (
                self.extractor.extract_with_routes(detection)
                if detection.status == DetectionStatus.DECLARED
                else None
            )
            records = self.http.drain()
            summary = CheckSummary(
                fund_id=fund.fund_id,
                window_start=window_start,
                window_end=window_end,
                status=detection.status.value,
                confidence=detection.confidence,
                http_requests=len(records),
            )
            logger.info(
                "Check %s %s..%s -> %s (confidence %s)",
                fund.fund_id,
                window_start,
                window_end,
                detection.status.value,
                detection.confidence,
            )

            with session_scope(self.engine) as session:
                repo = DistributionRepository(session)
                docs = self._persist_http(repo, fund, records)

                if outcome is not None:
                    summary.route_taken = outcome.route_taken.value
                    if outcome.needs_manual_review:
                        repo.enqueue_review(
                            fund_id=fund.fund_id,
                            reason="MANUAL_ROUTE",
                            details="Detected as DECLARED but no automated route extracted the figures: "
                            + "; ".join(
                                f"{a.url} -> {a.route.value} {a.note}"
                                for a in outcome.attempts
                            )[:1500],
                            window_start=window_start,
                            window_end=window_end,
                            payload=[e.__dict__ for e in detection.evidence],
                        )
                        summary.sent_to_review += 1
                    for extracted in outcome.events:
                        self._store_event(
                            repo,
                            fund,
                            detection,
                            extracted,
                            docs,
                            summary,
                            window_start,
                            window_end,
                        )

                summary.duration_seconds = round(time.monotonic() - t0, 3)
                repo.log_detection_run(
                    check_id=cid,
                    run_id=self.run_id,
                    fund_id=fund.fund_id,
                    window_start=window_start,
                    window_end=window_end,
                    status=detection.status.value,
                    confidence=detection.confidence,
                    unknown_reason=(
                        detection.unknown_reason.value
                        if detection.unknown_reason
                        else None
                    ),
                    suggested_route=(
                        detection.suggested_extraction_route.value
                        if detection.suggested_extraction_route
                        else None
                    ),
                    route_taken=summary.route_taken,
                    events_extracted=summary.events_inserted
                    + summary.events_amended
                    + summary.events_unchanged,
                    sent_to_review=summary.sent_to_review,
                    evidence_json=to_json([e.__dict__ for e in detection.evidence]),
                    http_requests=len(records),
                    bytes_downloaded=sum(len(r.content_bytes or b"") for r in records),
                    duration_seconds=summary.duration_seconds,
                    sweep_reason=(sweep_reason or "")[:255] or None,
                )
                self._update_state(
                    repo, fund, detection, outcome.events if outcome else []
                )
            return summary

    def flush_http(self, fund_id: str | None = None) -> int:
        """Persist fetches made outside a check (e.g. the sweep gate) to crawl_log/raw_document."""
        records = self.http.drain()
        if not records:
            return 0
        fund = self.universe.get_fund(fund_id) if fund_id else None
        with session_scope(self.engine) as session:
            self._persist_http(DistributionRepository(session), fund, records)
        return len(records)

    # ------------------------------------------------------------------
    def _persist_http(
        self,
        repo: DistributionRepository,
        fund: UniverseFund | None,
        records: list[HTTPResponseRecord],
    ) -> dict[str, RawDocument]:
        docs: dict[str, RawDocument] = {}
        for rec in records:
            src_id = source_id_for_url(rec.url)
            doc = None
            if rec.is_success and rec.content_bytes:
                doc = repo.store_raw_document(
                    source_id=src_id,
                    source_url=rec.url,
                    content_bytes=rec.content_bytes,
                    content_type=_content_type(rec),
                    retrieved_at=rec.retrieved_at,
                )
                docs[rec.url] = doc
                repo.mark_source_success(src_id, rec.retrieved_at)
            repo.log_crawl_attempt(
                source_id=src_id,
                target_url=rec.url,
                outcome=(
                    "SUCCESS"
                    if rec.is_success
                    else (rec.failure_reason.value if rec.failure_reason else "FAILED")
                ),
                fund_id=fund.fund_id if fund else None,
                http_status=rec.status_code,
                content_sha256=doc.sha256 if doc else None,
                duration_seconds=rec.elapsed_seconds,
                error_message=rec.error_message,
                retrieved_at=rec.retrieved_at,
            )
        return docs

    def _doc_for(
        self, repo: DistributionRepository, url: str, docs: dict[str, RawDocument]
    ) -> RawDocument | None:
        if url in docs:
            return docs[url]
        rec = self.http.response_for(
            url
        )  # fetched earlier in this run (cache hit this time)
        if rec is not None and rec.is_success and rec.content_bytes:
            doc = repo.store_raw_document(
                source_id=source_id_for_url(url),
                source_url=url,
                content_bytes=rec.content_bytes,
                content_type=_content_type(rec),
                retrieved_at=rec.retrieved_at,
            )
            docs[url] = doc
            return doc
        return None

    def _store_event(
        self,
        repo: DistributionRepository,
        fund: UniverseFund,
        detection: DetectionResult,
        extracted: ExtractedDistribution,
        docs: dict[str, RawDocument],
        summary: CheckSummary,
        window_start: date,
        window_end: date,
    ) -> None:
        doc = self._doc_for(repo, extracted.source_url, docs)
        context = {
            "country": fund.country,
            "share_class_currency": "CAD" if fund.country == "CA" else "USD",
            "fund_type": "ETF" if fund.is_etf else "MUTUAL_FUND",
        }
        results = self.dq.validate_event(extracted, context=context)
        blocking = self.dq.blocking_failures(results)
        if (
            doc is None
            or extracted.extraction_route == ExtractionRoute.MANUAL
            or blocking
        ):
            reason = (
                "NO_SOURCE_DOCUMENT"
                if doc is None
                else ("MANUAL_ROUTE" if not blocking else "VALIDATION_FAILED")
            )
            repo.enqueue_review(
                fund_id=fund.fund_id,
                reason=reason,
                details="; ".join(f"{r.rule_name}: {r.message}" for r in blocking)
                or reason,
                window_start=window_start,
                window_end=window_end,
                payload=extracted,
                source_url=extracted.source_url,
                doc_id=doc.doc_id if doc else None,
            )
            summary.sent_to_review += 1
            return

        saved = repo.save_distribution_event(
            extracted,
            class_id=self.class_id(fund),
            raw_document=doc,
            confidence=detection.confidence,
        )
        if saved.outcome == "INSERTED":
            summary.events_inserted += 1
        elif saved.outcome == "AMENDED":
            summary.events_amended += 1
        elif saved.outcome == CROSS_SOURCE_CONFLICT:
            summary.cross_source_conflicts += 1
            repo.log_dq_flag(
                fund_id=fund.fund_id,
                event_id=saved.event.event_id,
                rule_name="CROSS_SOURCE_VARIANCE",
                severity="WARNING",
                message=(
                    f"Stored {float(saved.event.gross_amount):.6f} but {extracted.source_url} reports "
                    f"{extracted.gross_amount:.6f}. Both kept in event_evidence; resolve by review."
                ),
            )
        else:
            summary.events_unchanged += 1
        for w in self.dq.warnings(results):
            repo.log_dq_flag(
                fund_id=fund.fund_id,
                event_id=saved.event.event_id,
                rule_name=w.rule_name,
                severity=w.severity.value,
                message=w.message,
            )

    def _update_state(
        self,
        repo: DistributionRepository,
        fund: UniverseFund,
        detection: DetectionResult,
        events: list[ExtractedDistribution],
    ) -> None:
        state = repo.get_detection_state(fund.fund_id)
        now = datetime.now(timezone.utc)
        last_confirmed = state.last_confirmed_event_date if state else None
        unknowns = state.consecutive_unknowns if state else 0
        if detection.status == DetectionStatus.DECLARED:
            dates = [e.ex_date for e in events if e.gross_amount > 0]
            if not dates:
                dates = [
                    d
                    for ev in detection.evidence
                    for d in (
                        ev.ex_date_found,
                        ev.declaration_date_found,
                        ev.published_date_found,
                    )
                    if d
                    and detection.window_start
                    and detection.window_end
                    and detection.window_start <= d <= detection.window_end
                ]
            if dates:
                newest = max(dates)
                last_confirmed = (
                    max(newest, last_confirmed) if last_confirmed else newest
                )
            unknowns = 0
        elif detection.status == DetectionStatus.NOT_DECLARED:
            unknowns = 0
        else:
            unknowns += 1
        repo.upsert_detection_state(
            fund.fund_id,
            expected_frequency=(
                fund.expected_frequency
                or ("MONTHLY" if fund.is_monthly_payer else "QUARTERLY")
            ).upper(),
            last_confirmed_event_date=last_confirmed,
            last_checked_at=now,
            consecutive_unknowns=unknowns,
            last_status=detection.status.value,
        )
