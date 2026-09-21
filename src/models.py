"""Core domain data models and enumerations for the Fund Distribution Detection Engine.

All structures adhere strictly to Python 3.11+ type annotations and deterministic validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class DetectionStatus(str, Enum):
    """Possible outcomes of the Layer A atomic detector."""

    DECLARED = "DECLARED"
    NOT_DECLARED = "NOT_DECLARED"
    UNKNOWN = "UNKNOWN"


class UnknownReason(str, Enum):
    """Explicit taxonomy of reasons for an UNKNOWN detection outcome."""

    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    RETRIEVAL_FAILED = "RETRIEVAL_FAILED"
    INCOMPLETE_SOURCE = "INCOMPLETE_SOURCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ExtractionRoute(str, Enum):
    """Recommended downstream processing routes for declared distributions."""

    API = "API"
    HTML_TABLE = "HTML_TABLE"
    PDF = "PDF"
    FILING = "FILING"
    MANUAL = "MANUAL"


class SourceTier(int, Enum):
    """Strict source authority hierarchy."""

    TIER_1_AUTHORITATIVE = (
        1  # SEC EDGAR filings (Rule 19a-1, N-PORT, N-CEN, 497), TMX/CDS notices
    )
    TIER_2_PRIMARY_UNSTRUCTURED = (
        2  # Official fund company distribution schedules & press releases
    )
    TIER_3_CORROBORATION = (
        3  # Market cross-check pages (corroboration only, never sole evidence)
    )


@dataclass(frozen=True)
class Evidence:
    """Auditable evidence unit supporting a detection decision."""

    source_id: str
    source_tier: SourceTier
    url: str
    retrieved_at: datetime
    snippet_or_locator: str
    declaration_date_found: date | None = None
    ex_date_found: date | None = None
    record_date_found: date | None = None
    payable_date_found: date | None = None
    failure_reason: UnknownReason | None = None

    def __post_init__(self) -> None:
        if not self.source_id or not isinstance(self.source_id, str):
            raise ValueError("Evidence must have a non-empty string source_id.")
        if not isinstance(self.source_tier, SourceTier):
            raise TypeError(
                f"Invalid source_tier: {self.source_tier}. Must be a valid SourceTier enum."
            )
        if not self.url or not isinstance(self.url, str):
            raise ValueError("Evidence must have a valid url.")
        if not isinstance(self.retrieved_at, datetime):
            raise TypeError("Evidence retrieved_at must be a valid datetime.")
        if not self.snippet_or_locator or not isinstance(self.snippet_or_locator, str):
            raise ValueError("Evidence must contain a descriptive snippet_or_locator.")


@dataclass
class DetectionResult:
    """Standardized result returned by detect_distribution for a fund and date window."""

    fund_id: str
    status: DetectionStatus
    confidence: float | None
    evidence: list[Evidence] = field(default_factory=list)
    suggested_extraction_route: ExtractionRoute | None = None
    window_start: date | None = None
    window_end: date | None = None

    def __post_init__(self) -> None:
        if not self.fund_id or not isinstance(self.fund_id, str):
            raise ValueError(
                "DetectionResult must have a valid non-empty string fund_id."
            )
        if not isinstance(self.status, DetectionStatus):
            raise TypeError(
                f"Invalid status: {self.status}. Must be a DetectionStatus enum."
            )
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"Confidence must be between 0.0 and 1.0 or None, got: {self.confidence}"
            )
        if self.suggested_extraction_route is not None and not isinstance(
            self.suggested_extraction_route, ExtractionRoute
        ):
            raise TypeError(
                f"Invalid suggested_extraction_route: {self.suggested_extraction_route}"
            )
        if (
            self.window_start
            and self.window_end
            and self.window_start > self.window_end
        ):
            raise ValueError(
                f"window_start ({self.window_start}) cannot be after window_end ({self.window_end})."
            )
        # Ensure that every result carries evidence
        if not self.evidence:
            raise ValueError("DetectionResult must carry at least one Evidence item.")


class ValidationStatus(str, Enum):
    """Validation states for discovered candidate sources."""

    VERIFIED_DIRECT_DISTRIBUTION = "VERIFIED_DIRECT_DISTRIBUTION"
    VERIFIED_COVERAGE_SOURCE = "VERIFIED_COVERAGE_SOURCE"
    SUPPORTING_FILING_INDEX = "SUPPORTING_FILING_INDEX"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    INCOMPLETE = "INCOMPLETE"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"
    CORROBORATION_ONLY = "CORROBORATION_ONLY"
    VERIFIED = "VERIFIED"


class SourceCoverageType(str, Enum):
    """Classification of source coverage over the target date window."""

    DIRECT_DECLARATION = "DIRECT_DECLARATION"
    EXHAUSTIVE_NEGATIVE = "EXHAUSTIVE_NEGATIVE"
    INCOMPLETE = "INCOMPLETE"
    UNAVAILABLE = "UNAVAILABLE"
    CORROBORATION_ONLY = "CORROBORATION_ONLY"


class SourceCoverageState(str, Enum):
    """Explicit coverage states for evaluated source attempts per Assignment 2 requirements."""

    COMPLETE_POSITIVE = "COMPLETE_POSITIVE"
    COMPLETE_NEGATIVE = "COMPLETE_NEGATIVE"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_RETRIEVED = "NOT_RETRIEVED"


@dataclass
class SourceAttemptRecord:
    """Record of an attempted source inspection during multi-tier fallback."""

    fund_id: str
    source_id: str
    source_tier: SourceTier
    url: str
    source_type: str
    retrieval_status: str
    retrieved_at: datetime | None = None
    coverage_status: SourceCoverageState = SourceCoverageState.NOT_RETRIEVED
    evidence_found: bool = False
    failure_reason: UnknownReason | None = None
    notes: str = ""


@dataclass
class SourceCandidate:
    """Standardized representation of a discovered and evaluated source candidate."""

    fund_id: str
    source_id: str
    source_tier: SourceTier
    source_role: str
    provider: str
    url: str
    source_type: str
    official_domain: str
    identifier_type: str | None = None
    identifier_value: str | None = None
    retrieval_status: str = "PENDING"
    http_status: int | None = None
    redirect_chain: list[str] = field(default_factory=list)
    content_type: str | None = None
    content_hash: str | None = None
    retrieved_at: datetime | None = None
    coverage_start: date | None = None
    coverage_end: date | None = None
    domain_verified: bool = False
    source_retrievable: bool = False
    identity_verified: bool = False
    content_usable: bool = False
    distribution_capable: bool = False
    coverage_verified: bool = False
    fund_identity_verified: bool = False
    class_or_series_verified: bool = False
    distribution_semantics_verified: bool = False
    direct_declaration_capable: bool = False
    coverage_complete: bool = False
    validation_status: ValidationStatus = ValidationStatus.UNAVAILABLE
    validation_reason: str = ""
