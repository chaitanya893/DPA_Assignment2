"""SQLAlchemy ORM Models for Fund Distribution Database.

Adheres strictly to Assignment 2 Phase 3 specifications:
- 9 core tables: fund_master, share_class, source_registry, crawl_log, raw_document,
  distribution_event, distribution_component, event_evidence, dq_flag.
- 3 operational tables added (justified in docs/schema_diagram.md):
  detection_run (every Layer A check + route taken), fund_detection_state (backfill and
  gap logic state per fund) and review_queue (figures that failed validation or could not
  be extracted automatically; they never reach distribution_event).
- Idempotency via UniqueConstraints.
- Versioning & Non-destructive Amendments.
- Cryptographic provenance tracking.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base declarative class for all database models."""

    pass


class FundMaster(Base):
    """1. fund_master: Master entity representing an investment fund."""

    __tablename__ = "fund_master"

    fund_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fund_name: Mapped[str] = mapped_column(String(255), nullable=False)
    fund_family: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    country: Mapped[str] = mapped_column(
        String(8), nullable=False, index=True
    )  # 'US' or 'CA'
    fund_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # 'ETF', 'MUTUAL_FUND', 'CLOSED_END_FUND'
    cik: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    sedar_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    inception_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    share_classes: Mapped[List["ShareClass"]] = relationship(
        "ShareClass", back_populates="fund", cascade="all, delete-orphan"
    )
    distribution_events: Mapped[List["DistributionEvent"]] = relationship(
        "DistributionEvent", back_populates="fund"
    )
    dq_flags: Mapped[List["DQFlag"]] = relationship("DQFlag", back_populates="fund")


class ShareClass(Base):
    """2. share_class: Share class identifiers, trading codes, and distribution cadence."""

    __tablename__ = "share_class"

    class_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fund_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("fund_master.fund_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ticker: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    cusip: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    isin: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    sec_series_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    sec_class_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    fundserv_code: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, index=True
    )
    currency: Mapped[str] = mapped_column(String(8), nullable=False)  # 'USD', 'CAD'
    expected_frequency: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # 'DAILY', 'MONTHLY', 'QUARTERLY', 'ANNUAL', etc.
    is_monthly_payer: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    is_etf: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    fund: Mapped["FundMaster"] = relationship(
        "FundMaster", back_populates="share_classes"
    )
    events: Mapped[List["DistributionEvent"]] = relationship(
        "DistributionEvent", back_populates="share_class"
    )


class SourceRegistry(Base):
    """3. source_registry: Registry of all source discovery and extraction endpoints."""

    __tablename__ = "source_registry"

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_tier: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True
    )  # 1, 2, 3
    url_pattern: Mapped[str] = mapped_column(String(512), nullable=False)
    parser_version: Mapped[str] = mapped_column(
        String(32), default="1.0.0", nullable=False
    )
    robots_status: Mapped[str] = mapped_column(
        String(32), default="ALLOWED", nullable=False
    )
    tos_status: Mapped[str] = mapped_column(
        String(32), default="COMPLIANT", nullable=False
    )
    last_success: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    crawl_logs: Mapped[List["CrawlLog"]] = relationship(
        "CrawlLog", back_populates="source"
    )
    raw_documents: Mapped[List["RawDocument"]] = relationship(
        "RawDocument", back_populates="source"
    )


class CrawlLog(Base):
    """4. crawl_log: Audit trail of every crawler attempt, hash, status, and duration."""

    __tablename__ = "crawl_log"

    log_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("source_registry.source_id"), nullable=False, index=True
    )
    fund_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("fund_master.fund_id"), nullable=True, index=True
    )
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    content_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # 'SUCCESS', 'BLOCKED_403', etc.
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    source: Mapped["SourceRegistry"] = relationship(
        "SourceRegistry", back_populates="crawl_logs"
    )


class RawDocument(Base):
    """5. raw_document: Immutable storage of raw source artifacts with cryptographic SHA-256."""

    __tablename__ = "raw_document"

    doc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sha256: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    source_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("source_registry.source_id"), nullable=False, index=True
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    content_type: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # 'text/html', 'application/pdf', etc.
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    storage_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    source: Mapped["SourceRegistry"] = relationship(
        "SourceRegistry", back_populates="raw_documents"
    )
    evidence_links: Mapped[List["EventEvidence"]] = relationship(
        "EventEvidence", back_populates="raw_document"
    )


class DistributionEvent(Base):
    """6. distribution_event: Master distribution event table with versioning and natural keys."""

    __tablename__ = "distribution_event"
    __table_args__ = (
        # Natural key: one row per share class, ex-date, distribution category and
        # estimated/final flag. The category keeps an income payment and a capital gain
        # paid on the same ex-date as two rows. Versions hold amendments.
        UniqueConstraint(
            "class_id",
            "ex_date",
            "distribution_category",
            "estimated_or_final",
            "version",
            name="uq_event_natural_key",
        ),
        Index("idx_event_class_ex", "class_id", "ex_date"),
    )

    event_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    class_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("share_class.class_id"), nullable=False
    )
    fund_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("fund_master.fund_id"), nullable=False, index=True
    )
    ex_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    record_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    payable_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    declaration_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)  # 'USD', 'CAD'
    gross_amount: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False)
    distribution_type: Mapped[str] = mapped_column(
        String(128), default="Income", nullable=False
    )
    distribution_category: Mapped[str] = mapped_column(
        String(32), default="INCOME", nullable=False
    )
    components_reported: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    source_tier: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    estimated_or_final: Mapped[str] = mapped_column(
        String(16), default="FINAL", nullable=False
    )  # 'ESTIMATED', 'FINAL'
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_superseded: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    superseded_by: Mapped[Optional[str]] = mapped_column(
        String(160), ForeignKey("distribution_event.event_id"), nullable=True
    )
    extraction_route: Mapped[str] = mapped_column(
        String(32), default="HTML_TABLE", nullable=False
    )  # 'API', 'HTML_TABLE', 'PDF', 'FILING', 'MANUAL'
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    share_class: Mapped["ShareClass"] = relationship(
        "ShareClass", back_populates="events"
    )
    fund: Mapped["FundMaster"] = relationship(
        "FundMaster", back_populates="distribution_events"
    )
    components: Mapped[List["DistributionComponent"]] = relationship(
        "DistributionComponent", back_populates="event", cascade="all, delete-orphan"
    )
    evidence: Mapped[List["EventEvidence"]] = relationship(
        "EventEvidence", back_populates="event", cascade="all, delete-orphan"
    )
    dq_flags: Mapped[List["DQFlag"]] = relationship(
        "DQFlag", back_populates="event", cascade="all, delete-orphan"
    )


class DistributionComponent(Base):
    """7. distribution_component: Granular child tax components for each distribution event."""

    __tablename__ = "distribution_component"

    component_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    event_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("distribution_event.event_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    component_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    component_name: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False)
    percentage: Mapped[Optional[float]] = mapped_column(Numeric(6, 3), nullable=True)
    is_tax_reallocated: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    event: Mapped["DistributionEvent"] = relationship(
        "DistributionEvent", back_populates="components"
    )


class EventEvidence(Base):
    """8. event_evidence: Provenance links connecting distribution events to raw documents."""

    __tablename__ = "event_evidence"

    evidence_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    event_id: Mapped[str] = mapped_column(
        String(160),
        ForeignKey("distribution_event.event_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    doc_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("raw_document.doc_id"), nullable=False, index=True
    )
    source_tier: Mapped[int] = mapped_column(Integer, nullable=False)  # 1, 2, 3
    locator_or_snippet: Mapped[str] = mapped_column(Text, nullable=False)
    # Amount this particular source reported. When two sources disagree both amounts are
    # kept here and a CROSS_SOURCE_VARIANCE flag is raised; neither is silently dropped.
    reported_amount: Mapped[Optional[float]] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    confidence: Mapped[Optional[float]] = mapped_column(Numeric(4, 3), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    event: Mapped["DistributionEvent"] = relationship(
        "DistributionEvent", back_populates="evidence"
    )
    raw_document: Mapped["RawDocument"] = relationship(
        "RawDocument", back_populates="evidence_links"
    )


class DQFlag(Base):
    """9. dq_flag: Data quality validation audit flags and resolution status."""

    __tablename__ = "dq_flag"

    flag_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[Optional[str]] = mapped_column(
        String(160),
        ForeignKey("distribution_event.event_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    fund_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("fund_master.fund_id"), nullable=False, index=True
    )
    rule_name: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(
        String(16), nullable=False, index=True
    )  # 'INFO', 'WARNING', 'CRITICAL'
    message: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_status: Mapped[str] = mapped_column(
        String(32), default="OPEN", nullable=False
    )  # 'OPEN', 'REVIEWED', 'SUPPRESSED', 'RESOLVED'
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    event: Mapped[Optional["DistributionEvent"]] = relationship(
        "DistributionEvent", back_populates="dq_flags"
    )
    fund: Mapped["FundMaster"] = relationship("FundMaster", back_populates="dq_flags")


class DetectionRun(Base):
    """10. detection_run: one row per Layer A check (fund x window).

    Records status, confidence, the recommended extraction route and the cost of the check,
    so hit rate, UNKNOWN rate, route mix and average cost per check can be reported.
    """

    __tablename__ = "detection_run"

    check_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    fund_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("fund_master.fund_id"), nullable=False, index=True
    )
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    confidence: Mapped[Optional[float]] = mapped_column(Numeric(4, 3), nullable=True)
    unknown_reason: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    suggested_route: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    route_taken: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    events_extracted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_to_review: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    http_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bytes_downloaded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sweep_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class FundDetectionState(Base):
    """11. fund_detection_state: backfill and gap logic state (PDF Phase 1)."""

    __tablename__ = "fund_detection_state"

    fund_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("fund_master.fund_id"), primary_key=True
    )
    expected_frequency: Mapped[str] = mapped_column(String(32), nullable=False)
    last_confirmed_event_date: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True
    )
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consecutive_unknowns: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    last_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    backfill_completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ReviewQueue(Base):
    """12. review_queue: items a human must look at before anything reaches the database.

    Holds MANUAL-route detections and extracted figures that failed a CRITICAL validation
    check (PDF: "Any figure that fails goes to review, not to the database").
    """

    __tablename__ = "review_queue"

    review_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fund_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("fund_master.fund_id"), nullable=False, index=True
    )
    window_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    window_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    doc_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("raw_document.doc_id"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), default="OPEN", nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
