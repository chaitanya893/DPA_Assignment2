"""Layer B extraction engine (PDF Assignment 2, Phase 2).

Only invoked for DECLARED results. For every source document the detector cited, the route
decision tree is applied in the PDF's order:

    structured feed / API?        -> parse directly          (ExtractionRoute.API)
    HTML table on the page?       -> HTML parse              (ExtractionRoute.HTML_TABLE)
    PDF or Excel schedule?        -> document extraction     (ExtractionRoute.PDF)
    data only inside a filing?    -> filing extraction       (ExtractionRoute.FILING)
    none of the above             -> queue for manual review (ExtractionRoute.MANUAL)

The engine never reads the project's own config files as a "source" and never invents
tax components: components are only returned when the document publishes them.
Every event carries the URL of the document it came from so the pipeline can link it to the
stored raw_document (provenance).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from src.http_client import HTTPClient, HTTPResponseRecord
from src.models import (
    DetectionResult,
    DetectionStatus,
    Evidence,
    ExtractedDistribution,
    ExtractionRoute,
    SourceTier,
)
from src.parsers.api_parser import parse_json_distributions
from src.parsers.filing_parser import parse_sec_rule_19a1_filing
from src.parsers.html_table_parser import parse_html_distribution_tables
from src.parsers.pdf_parser import (
    parse_excel_distribution_document,
    parse_pdf_distribution_document,
)
from src.strategies import _dates_in_window
from src.universe_loader import UniverseFund, UniverseRegistry

logger = logging.getLogger(__name__)


@dataclass
class ExtractionAttempt:
    url: str
    route: ExtractionRoute
    events: int
    note: str = ""


@dataclass
class ExtractionOutcome:
    events: list[ExtractedDistribution] = field(default_factory=list)
    attempts: list[ExtractionAttempt] = field(default_factory=list)

    @property
    def route_taken(self) -> ExtractionRoute:
        for a in self.attempts:
            if a.events:
                return a.route
        return ExtractionRoute.MANUAL

    @property
    def needs_manual_review(self) -> bool:
        return not self.events


def _is_json(rec: HTTPResponseRecord, url: str) -> bool:
    ct = (rec.content_type or "").lower()
    body = (rec.content_text or "").lstrip()[:1]
    return "json" in ct or url.lower().endswith(".json") or body in ("{", "[")


def _is_excel(rec: HTTPResponseRecord, url: str) -> bool:
    ct = (rec.content_type or "").lower()
    return (
        url.lower().endswith((".xlsx", ".xls")) or "spreadsheet" in ct or "excel" in ct
    )


def _is_pdf(rec: HTTPResponseRecord, url: str) -> bool:
    return bool(
        rec.content_bytes and rec.content_bytes.startswith(b"%PDF-")
    ) or url.lower().endswith(".pdf")


class ExtractionEngine:
    """Applies the route decision tree to the documents behind a DECLARED detection."""

    def __init__(
        self,
        http_client: HTTPClient | None = None,
        universe: UniverseRegistry | None = None,
    ) -> None:
        self.http_client = http_client or HTTPClient()
        self.universe = universe or UniverseRegistry.from_json()

    def extract_with_routes(self, detection: DetectionResult) -> ExtractionOutcome:
        if detection.status != DetectionStatus.DECLARED:
            raise ValueError(
                f"Layer B Extractor only accepts DECLARED detection results. Got: {detection.status.value}"
            )
        fund = self.universe.get_fund(detection.fund_id)
        if not fund:
            raise ValueError(
                f"Fund ID {detection.fund_id} not found in UniverseRegistry."
            )

        ws = detection.window_start
        we = detection.window_end
        cited: list[Evidence] = [
            e
            for e in detection.evidence
            if e.source_tier
            in (SourceTier.TIER_1_AUTHORITATIVE, SourceTier.TIER_2_PRIMARY_UNSTRUCTURED)
            and e.failure_reason is None
            and not e.url.startswith("internal://")
            and (ws is None or we is None or _dates_in_window(e, ws, we))
        ]
        cited.sort(key=lambda e: int(e.source_tier))

        outcome = ExtractionOutcome()
        seen_urls: set[str] = set()
        for ev in cited:
            if ev.url in seen_urls:
                continue
            seen_urls.add(ev.url)
            rec = self.http_client.get(ev.url)
            if not rec.is_success:
                outcome.attempts.append(
                    ExtractionAttempt(
                        ev.url,
                        ExtractionRoute.MANUAL,
                        0,
                        rec.error_message or "fetch failed",
                    )
                )
                continue
            route, events = self._route_document(
                rec, ev.url, fund, ev.source_tier, ws, we
            )
            outcome.attempts.append(ExtractionAttempt(ev.url, route, len(events)))
            outcome.events.extend(events)

        outcome.events = self._dedupe(outcome.events)
        return outcome

    def extract(self, detection: DetectionResult) -> list[ExtractedDistribution]:
        """Extracted events, or one MANUAL placeholder when no route could extract anything.

        The MANUAL placeholder has gross_amount 0.0 and validation_passed False; the pipeline
        sends it to review_queue and never to distribution_event.
        """
        outcome = self.extract_with_routes(detection)
        if outcome.events:
            return outcome.events
        fund = self.universe.get_fund(detection.fund_id)
        assert fund is not None
        anchor = next(
            (
                d
                for e in detection.evidence
                for d in (
                    e.ex_date_found,
                    e.declaration_date_found,
                    e.published_date_found,
                )
                if d is not None
            ),
            detection.window_start or date.today(),
        )
        logger.warning(
            "No automated route could extract %s; queuing for manual review.",
            fund.fund_id,
        )
        return [
            ExtractedDistribution(
                fund_id=fund.fund_id,
                country=fund.country,
                currency="CAD" if fund.country == "CA" else "USD",
                ticker=fund.ticker,
                fundserv_code=fund.fundserv_code,
                ex_date=anchor,
                gross_amount=0.0,
                distribution_type="MANUAL_REVIEW_REQUIRED",
                source_url=(
                    outcome.attempts[0].url
                    if outcome.attempts
                    else fund.official_source_url
                ),
                extraction_route=ExtractionRoute.MANUAL,
                retrieved_at=datetime.now(timezone.utc),
                raw_doc_snippet="; ".join(
                    f"{a.url}: {a.route.value} ({a.note or a.events})"
                    for a in outcome.attempts
                )[:500]
                or "No cited document could be fetched.",
                validation_passed=False,
                validation_notes="Queued for manual review per Assignment 2 route decision tree.",
            )
        ]

    def _route_document(
        self,
        rec: HTTPResponseRecord,
        url: str,
        fund: UniverseFund,
        tier: SourceTier,
        ws: date | None,
        we: date | None,
    ) -> tuple[ExtractionRoute, list[ExtractedDistribution]]:
        common = dict(
            fund_id=fund.fund_id,
            country=fund.country,
            source_url=url,
            ticker=fund.ticker,
            fundserv_code=fund.fundserv_code,
            window_start=ws,
            window_end=we,
            source_tier=tier,
        )
        # 1. Structured feed / API
        if _is_json(rec, url) and rec.content_text:
            events = parse_json_distributions(rec.content_text, **common)
            if events:
                return ExtractionRoute.API, events
        if rec.content_text and "data-vgn-funds-profile" in rec.content_text:
            from src.parsers.vanguard_profile_parser import parse_vanguard_profile

            events = parse_vanguard_profile(
                rec.content_text,
                fund_id=fund.fund_id,
                source_url=url,
                window_start=ws,
                window_end=we,
                source_tier=tier,
                ticker=fund.ticker,
            )
            if events:
                return ExtractionRoute.API, events
        if (
            rec.content_text
            and "const fundData" in rec.content_text
            and fund.fund_family == "RBC Global Asset Management"
        ):
            from src.parsers.rbc_fund_data_parser import parse_rbc_fund_data

            events = parse_rbc_fund_data(
                rec.content_text,
                fund_id=fund.fund_id,
                ticker=fund.ticker or "",
                source_url=url,
                window_start=ws,
                window_end=we,
                source_tier=tier,
            )
            if events:
                return ExtractionRoute.API, events
        # 2. HTML table
        if (
            rec.content_text
            and "<table" in rec.content_text.lower()
            and not _is_pdf(rec, url)
        ):
            events = parse_html_distribution_tables(rec.content_text, **common)
            if events:
                return ExtractionRoute.HTML_TABLE, events
        # 3. PDF or Excel schedule
        if _is_pdf(rec, url) and rec.content_bytes:
            events = parse_pdf_distribution_document(rec.content_bytes, **common)
            if events:
                return ExtractionRoute.PDF, events
        if _is_excel(rec, url) and rec.content_bytes:
            from src.parsers.spdr_distributions_parser import parse_spdr_distributions

            events = parse_spdr_distributions(
                rec.content_bytes,
                fund_id=fund.fund_id,
                ticker=fund.ticker,
                source_url=url,
                window_start=ws,
                window_end=we,
                source_tier=tier,
            )
            if events:
                return ExtractionRoute.PDF, events
            events = parse_excel_distribution_document(rec.content_bytes, **common)
            if events:
                return ExtractionRoute.PDF, events
        # 4. Filing / notice text (SEC filing, TMX/CDS notice, press release)
        if rec.content_text and not _is_pdf(rec, url):
            events = parse_sec_rule_19a1_filing(
                filing_text=rec.content_text,
                fund_id=fund.fund_id,
                filing_url=url,
                ticker=fund.ticker,
                country=fund.country,
                source_tier=tier,
            )
            events = [
                e
                for e in events
                if (ws is None or e.ex_date >= ws) and (we is None or e.ex_date <= we)
            ]
            if events:
                return ExtractionRoute.FILING, events
        return ExtractionRoute.MANUAL, []

    @staticmethod
    def _dedupe(events: list[ExtractedDistribution]) -> list[ExtractedDistribution]:
        """Drop exact duplicates of the same figure from the same URL. Different sources are kept
        so the pipeline can record both and flag any disagreement."""
        seen: set[tuple] = set()
        out: list[ExtractedDistribution] = []
        for e in events:
            key = (
                e.source_url,
                e.ex_date,
                e.distribution_category,
                e.is_estimated,
                round(e.gross_amount, 6),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(e)
        return out


def extract_distribution(
    detection_result: DetectionResult,
    http_client: HTTPClient | None = None,
    universe: UniverseRegistry | None = None,
) -> list[ExtractedDistribution]:
    """Top-level convenience function for Layer B extraction."""
    engine = ExtractionEngine(http_client=http_client, universe=universe)
    return engine.extract(detection_result)
