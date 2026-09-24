"""Database repository enforcing the Assignment 2 Phase 3 non-negotiables.

1. Idempotency. Natural key = (class_id, ex_date, distribution_category, estimated_or_final).
   Re-saving the same figures from the same source is a no-op.
2. Amendments. A restatement (new amount or new component split) from the same source,
   or an explicit amendment, appends version N+1 and marks version N superseded.
   Nothing is updated in place except the supersede pointer.
3. Provenance. An event can only be saved together with the RawDocument (real source bytes,
   sha256) it was extracted from. No document, no row.
4. Estimated and final coexist as separate rows (estimated_or_final is part of the key).
5. Cross-source disagreement is recorded, never resolved silently: the second source's
   amount is stored on its own evidence row and the caller gets CROSS_SOURCE_CONFLICT so it
   can raise a CROSS_SOURCE_VARIANCE flag.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models import (
    CrawlLog,
    DetectionRun,
    DistributionComponent,
    DistributionEvent,
    DQFlag,
    EventEvidence,
    FundDetectionState,
    FundMaster,
    RawDocument,
    ReviewQueue,
    ShareClass,
    SourceRegistry,
)
from src.models import ExtractedDistribution

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

INSERTED = "INSERTED"
UNCHANGED = "UNCHANGED"
AMENDED = "AMENDED"
CROSS_SOURCE_CONFLICT = "CROSS_SOURCE_CONFLICT"


class SaveResult(tuple):
    """(event, is_new) tuple that also carries ``outcome``.

    Unpacks like the old two-value return (``evt, is_new = repo.save_distribution_event(...)``).
    """

    outcome: str

    def __new__(
        cls, event: DistributionEvent, is_new: bool, outcome: str
    ) -> SaveResult:
        obj = super().__new__(cls, (event, is_new))
        obj.outcome = outcome
        return obj

    @property
    def event(self) -> DistributionEvent:
        return self[0]

    @property
    def is_new(self) -> bool:
        return self[1]


def raw_document_dir() -> Path:
    """Where raw source bytes are written (git-ignored). Override with RAW_DOCUMENT_DIR."""
    return Path(os.getenv("RAW_DOCUMENT_DIR", str(PROJECT_ROOT / "data" / "raw")))


def _component_signature(components: list[Any]) -> list[tuple[str, float]]:
    sig = []
    for c in components:
        ctype = (
            c.component_type.value
            if hasattr(c.component_type, "value")
            else str(c.component_type)
        )
        sig.append((ctype, round(float(c.amount), 6)))
    return sorted(sig)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if hasattr(obj, "value"):
        return obj.value
    if is_dataclass(obj):
        return asdict(obj)
    return str(obj)


def to_json(obj: Any) -> str:
    if is_dataclass(obj) and not isinstance(obj, type):
        obj = asdict(obj)
    return json.dumps(obj, default=_json_default, sort_keys=True)


class DistributionRepository:
    """Repository managing fund distribution persistence and invariants."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # 1. Fund master and share class
    # ------------------------------------------------------------------
    def upsert_fund(
        self,
        fund_id: str,
        fund_name: str,
        fund_family: str,
        country: str,
        fund_type: str = "ETF",
        cik: str | None = None,
        sedar_id: str | None = None,
        inception_date: date | None = None,
        status: str = "ACTIVE",
    ) -> FundMaster:
        fund = self.session.get(FundMaster, fund_id)
        if fund:
            fund.fund_name = fund_name
            fund.fund_family = fund_family
            fund.country = country
            fund.fund_type = fund_type
            fund.cik = cik
            fund.sedar_id = sedar_id
            fund.status = status
            if inception_date:
                fund.inception_date = inception_date
        else:
            fund = FundMaster(
                fund_id=fund_id,
                fund_name=fund_name,
                fund_family=fund_family,
                country=country,
                fund_type=fund_type,
                cik=cik,
                sedar_id=sedar_id,
                inception_date=inception_date,
                status=status,
            )
            self.session.add(fund)
        self.session.flush()
        return fund

    def upsert_share_class(
        self,
        class_id: str,
        fund_id: str,
        ticker: str | None = None,
        cusip: str | None = None,
        isin: str | None = None,
        sec_series_id: str | None = None,
        sec_class_id: str | None = None,
        fundserv_code: str | None = None,
        currency: str = "USD",
        expected_frequency: str = "QUARTERLY",
        is_monthly_payer: bool = False,
        is_etf: bool = True,
    ) -> ShareClass:
        sc = self.session.get(ShareClass, class_id)
        values = dict(
            ticker=ticker,
            cusip=cusip,
            isin=isin,
            sec_series_id=sec_series_id,
            sec_class_id=sec_class_id,
            fundserv_code=fundserv_code,
            currency=currency,
            expected_frequency=expected_frequency,
            is_monthly_payer=is_monthly_payer,
            is_etf=is_etf,
        )
        if sc:
            for k, v in values.items():
                setattr(sc, k, v)
        else:
            sc = ShareClass(class_id=class_id, fund_id=fund_id, **values)
            self.session.add(sc)
        self.session.flush()
        return sc

    # ------------------------------------------------------------------
    # 2. Source registry and crawl log
    # ------------------------------------------------------------------
    def upsert_source_registry(
        self,
        source_id: str,
        source_name: str,
        source_tier: int,
        url_pattern: str,
        parser_version: str = "1.0.0",
        robots_status: str = "CHECKED_PER_REQUEST",
        tos_status: str = "PENDING_REVIEW",
        is_active: bool = True,
    ) -> SourceRegistry:
        src = self.session.get(SourceRegistry, source_id)
        values = dict(
            source_name=source_name,
            source_tier=source_tier,
            url_pattern=url_pattern,
            parser_version=parser_version,
            robots_status=robots_status,
            tos_status=tos_status,
            is_active=is_active,
        )
        if src:
            for k, v in values.items():
                setattr(src, k, v)
        else:
            src = SourceRegistry(source_id=source_id, **values)
            self.session.add(src)
        self.session.flush()
        return src

    def mark_source_success(self, source_id: str, when: datetime) -> None:
        src = self.session.get(SourceRegistry, source_id)
        if src and (src.last_success is None or _aware(src.last_success) < when):
            src.last_success = when

    def log_crawl_attempt(
        self,
        source_id: str,
        target_url: str,
        outcome: str,
        fund_id: str | None = None,
        http_status: int | None = None,
        content_sha256: str | None = None,
        duration_seconds: float = 0.0,
        error_message: str | None = None,
        retrieved_at: datetime | None = None,
    ) -> CrawlLog:
        log = CrawlLog(
            log_id=f"log_{uuid.uuid4().hex[:16]}",
            source_id=source_id,
            fund_id=fund_id,
            target_url=target_url,
            retrieved_at=retrieved_at or datetime.now(timezone.utc),
            http_status=http_status,
            content_sha256=content_sha256,
            outcome=outcome,
            duration_seconds=duration_seconds,
            error_message=error_message,
        )
        self.session.add(log)
        self.session.flush()
        return log

    # ------------------------------------------------------------------
    # 3. Raw documents (immutable, keyed by sha256)
    # ------------------------------------------------------------------
    def store_raw_document(
        self,
        source_id: str,
        source_url: str,
        content_bytes: bytes,
        content_type: str = "text/html",
        raw_text: str | None = None,
        retrieved_at: datetime | None = None,
        write_file: bool = True,
    ) -> RawDocument:
        """Store the exact bytes a source returned. Identical bytes are stored once."""
        sha256 = hashlib.sha256(content_bytes).hexdigest()
        for obj in self.session.new:
            if isinstance(obj, RawDocument) and obj.sha256 == sha256:
                return obj
        existing = self.session.scalars(
            select(RawDocument).where(RawDocument.sha256 == sha256)
        ).first()
        if existing:
            return existing

        storage_path: str | None = None
        if write_file:
            folder = raw_document_dir()
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{sha256}.bin"
            if not path.exists():
                path.write_bytes(content_bytes)
            storage_path = str(path)

        is_text = (
            content_type.startswith("text")
            or "json" in content_type
            or "xml" in content_type
        )
        doc = RawDocument(
            doc_id=f"doc_{sha256[:16]}",
            sha256=sha256,
            source_id=source_id,
            source_url=source_url,
            retrieved_at=retrieved_at or datetime.now(timezone.utc),
            content_type=content_type,
            byte_size=len(content_bytes),
            raw_text=(
                raw_text
                if raw_text is not None
                else (
                    content_bytes.decode("utf-8", errors="replace") if is_text else None
                )
            ),
            storage_path=storage_path,
        )
        self.session.add(doc)
        self.session.flush()
        return doc

    # ------------------------------------------------------------------
    # 4. Distribution events
    # ------------------------------------------------------------------
    def save_distribution_event(
        self,
        extracted: ExtractedDistribution,
        class_id: str | None = None,
        is_amendment: bool = False,
        raw_document: RawDocument | None = None,
        confidence: float | None = None,
    ) -> SaveResult:
        """Save an extracted distribution with its source document.

        Returns a SaveResult (unpacks as ``event, is_new``) with ``outcome`` one of
        INSERTED, UNCHANGED, AMENDED, CROSS_SOURCE_CONFLICT.
        """
        if raw_document is None:
            raise ValueError(
                "Provenance required: every distribution_event must be saved with the RawDocument "
                "it was extracted from (PDF Phase 3: 'If it cannot be traced, it does not belong in the table')."
            )
        if extracted.gross_amount is None or float(extracted.gross_amount) <= 0:
            raise ValueError(
                "Refusing to store a non-positive gross amount; route it to review instead."
            )

        cid = class_id or f"{extracted.fund_id}_CLASS"
        est_final = "ESTIMATED" if extracted.is_estimated else "FINAL"
        category = extracted.distribution_category

        existing = list(
            self.session.scalars(
                select(DistributionEvent)
                .where(
                    DistributionEvent.class_id == cid,
                    DistributionEvent.ex_date == extracted.ex_date,
                    DistributionEvent.distribution_category == category,
                    DistributionEvent.estimated_or_final == est_final,
                )
                .order_by(DistributionEvent.version.desc())
            ).all()
        )

        if not existing:
            event = self._insert_event(extracted, cid, category, est_final, 1)
            self._add_components(event, extracted, reallocated=False)
            self._add_evidence(event, raw_document, extracted, confidence)
            self.session.flush()
            return SaveResult(event, True, INSERTED)

        latest = existing[0]
        old_components = list(
            self.session.scalars(
                select(DistributionComponent).where(
                    DistributionComponent.event_id == latest.event_id
                )
            ).all()
        )
        same_amount = (
            abs(float(latest.gross_amount) - float(extracted.gross_amount)) < 1e-6
        )
        same_components = _component_signature(old_components) == _component_signature(
            extracted.components
        )

        if same_amount and same_components and not is_amendment:
            # Same figures. If a second, independent source confirms them, keep that evidence too.
            self._add_evidence(latest, raw_document, extracted, confidence)
            self.session.flush()
            return SaveResult(latest, False, UNCHANGED)

        # Sources that asserted the figure currently on record. Only one of them changing its
        # own number is a restatement; a different source disagreeing is a conflict.
        latest_sources = {
            doc.source_url
            for doc, link in self.session.execute(
                select(RawDocument, EventEvidence)
                .join(EventEvidence, EventEvidence.doc_id == RawDocument.doc_id)
                .where(EventEvidence.event_id == latest.event_id)
            ).all()
            if link.reported_amount is None
            or abs(float(link.reported_amount) - float(latest.gross_amount)) < 1e-6
        }
        same_source = raw_document.source_url in latest_sources

        if not (is_amendment or same_source):
            # Two different sources disagree: record both, flag, never pick one silently.
            self._add_evidence(latest, raw_document, extracted, confidence)
            self.session.flush()
            return SaveResult(latest, False, CROSS_SOURCE_CONFLICT)

        # Genuine restatement: append version N+1, then supersede N.
        new_event = self._insert_event(
            extracted, cid, category, est_final, latest.version + 1
        )
        reallocated = same_amount and not same_components
        self._add_components(new_event, extracted, reallocated=reallocated)
        self._add_evidence(new_event, raw_document, extracted, confidence)
        self.session.flush()  # the new row must exist before anything points at it
        latest.is_superseded = True
        latest.superseded_by = new_event.event_id
        self.session.flush()
        return SaveResult(new_event, True, AMENDED)

    def _insert_event(
        self,
        extracted: ExtractedDistribution,
        class_id: str,
        category: str,
        est_final: str,
        version: int,
    ) -> DistributionEvent:
        event_id = f"evt_{class_id}_{extracted.ex_date.isoformat()}_{category.lower()}_{est_final.lower()}_v{version}"
        event = DistributionEvent(
            event_id=event_id,
            class_id=class_id,
            fund_id=extracted.fund_id,
            ex_date=extracted.ex_date,
            record_date=extracted.record_date,
            payable_date=extracted.payable_date,
            declaration_date=extracted.declaration_date,
            currency=extracted.currency,
            gross_amount=extracted.gross_amount,
            distribution_type=(extracted.distribution_type or "Income")[:128],
            distribution_category=category,
            components_reported=extracted.components_reported,
            source_tier=int(extracted.source_tier),
            estimated_or_final=est_final,
            version=version,
            is_superseded=False,
            extraction_route=extracted.extraction_route.value,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def _add_components(
        self,
        event: DistributionEvent,
        extracted: ExtractedDistribution,
        reallocated: bool,
    ) -> None:
        for idx, comp in enumerate(extracted.components, 1):
            ctype = (
                comp.component_type.value
                if hasattr(comp.component_type, "value")
                else str(comp.component_type)
            )
            self.session.add(
                DistributionComponent(
                    component_id=f"{event.event_id}_c{idx}",
                    event_id=event.event_id,
                    component_type=ctype,
                    component_name=comp.component_name[:128],
                    amount=comp.amount,
                    percentage=comp.percentage,
                    is_tax_reallocated=bool(
                        reallocated or getattr(comp, "is_tax_reallocated", False)
                    ),
                )
            )

    def _add_evidence(
        self,
        event: DistributionEvent,
        doc: RawDocument,
        extracted: ExtractedDistribution,
        confidence: float | None,
    ) -> EventEvidence | None:
        evidence_id = f"ev_{event.event_id}_{doc.doc_id}"
        if self.session.get(EventEvidence, evidence_id) is not None:
            return None
        for obj in self.session.new:
            if isinstance(obj, EventEvidence) and obj.evidence_id == evidence_id:
                return None
        ev = EventEvidence(
            evidence_id=evidence_id,
            event_id=event.event_id,
            doc_id=doc.doc_id,
            source_tier=int(extracted.source_tier),
            locator_or_snippet=(
                extracted.raw_doc_snippet
                or f"{extracted.extraction_route.value} extraction from {doc.source_url}"
            )[:2000],
            reported_amount=extracted.gross_amount,
            confidence=confidence,
        )
        self.session.add(ev)
        return ev

    # ------------------------------------------------------------------
    # 5. Data quality flags (idempotent)
    # ------------------------------------------------------------------
    def log_dq_flag(
        self,
        fund_id: str,
        rule_name: str,
        severity: str,
        message: str,
        event_id: str | None = None,
        resolution_status: str = "OPEN",
    ) -> DQFlag:
        """Record a validation flag. An identical open flag is never duplicated on re-run."""
        for obj in self.session.new:
            if (
                isinstance(obj, DQFlag)
                and obj.fund_id == fund_id
                and obj.event_id == event_id
                and obj.rule_name == rule_name
                and obj.message == message
            ):
                return obj
        existing = self.session.scalars(
            select(DQFlag).where(
                DQFlag.fund_id == fund_id,
                (
                    DQFlag.event_id == event_id
                    if event_id is not None
                    else DQFlag.event_id.is_(None)
                ),
                DQFlag.rule_name == rule_name,
                DQFlag.message == message,
                DQFlag.resolution_status == "OPEN",
            )
        ).first()
        if existing:
            return existing
        flag = DQFlag(
            flag_id=f"dq_{uuid.uuid4().hex[:16]}",
            event_id=event_id,
            fund_id=fund_id,
            rule_name=rule_name,
            severity=severity,
            message=message,
            resolution_status=resolution_status,
        )
        self.session.add(flag)
        return flag

    # ------------------------------------------------------------------
    # 6. Detection log, gap state, review queue
    # ------------------------------------------------------------------
    def log_detection_run(self, **fields: Any) -> DetectionRun:
        row = DetectionRun(
            check_id=fields.pop("check_id", f"chk_{uuid.uuid4().hex[:16]}"), **fields
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_detection_state(self, fund_id: str) -> FundDetectionState | None:
        return self.session.get(FundDetectionState, fund_id)

    def upsert_detection_state(self, fund_id: str, **fields: Any) -> FundDetectionState:
        state = self.session.get(FundDetectionState, fund_id)
        if state is None:
            state = FundDetectionState(fund_id=fund_id, **fields)
            self.session.add(state)
        else:
            for k, v in fields.items():
                setattr(state, k, v)
        self.session.flush()
        return state

    def enqueue_review(
        self,
        fund_id: str,
        reason: str,
        details: str,
        window_start: date | None = None,
        window_end: date | None = None,
        payload: Any = None,
        source_url: str | None = None,
        doc_id: str | None = None,
    ) -> ReviewQueue:
        """Queue an item for manual review. The same open item is not queued twice."""
        payload_json = to_json(payload) if payload is not None else None
        existing = self.session.scalars(
            select(ReviewQueue).where(
                ReviewQueue.fund_id == fund_id,
                ReviewQueue.reason == reason,
                ReviewQueue.window_start == window_start,
                ReviewQueue.window_end == window_end,
                ReviewQueue.details == details,
                ReviewQueue.status == "OPEN",
            )
        ).first()
        if existing:
            return existing
        item = ReviewQueue(
            review_id=f"rv_{uuid.uuid4().hex[:16]}",
            fund_id=fund_id,
            window_start=window_start,
            window_end=window_end,
            reason=reason,
            details=details,
            payload_json=payload_json,
            source_url=source_url,
            doc_id=doc_id,
        )
        self.session.add(item)
        self.session.flush()
        return item


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
