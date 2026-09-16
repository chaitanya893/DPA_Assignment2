"""Unit tests for src/models.py data structures and validation."""

from datetime import date, datetime, timezone

import pytest

from src.models import (
    DetectionResult,
    DetectionStatus,
    Evidence,
    ExtractionRoute,
    SourceTier,
    UnknownReason,
)


def test_enums_completeness() -> None:
    """Verify all required enum constants exist and match specification."""
    # DetectionStatus
    assert set(DetectionStatus) == {
        DetectionStatus.DECLARED,
        DetectionStatus.NOT_DECLARED,
        DetectionStatus.UNKNOWN,
    }

    # UnknownReason taxonomy
    assert set(UnknownReason) == {
        UnknownReason.SOURCE_UNAVAILABLE,
        UnknownReason.RETRIEVAL_FAILED,
        UnknownReason.INCOMPLETE_SOURCE,
        UnknownReason.CONFLICTING_EVIDENCE,
        UnknownReason.INSUFFICIENT_EVIDENCE,
    }

    # ExtractionRoute
    assert set(ExtractionRoute) == {
        ExtractionRoute.API,
        ExtractionRoute.HTML_TABLE,
        ExtractionRoute.PDF,
        ExtractionRoute.FILING,
        ExtractionRoute.MANUAL,
    }

    # SourceTier
    assert SourceTier.TIER_1_AUTHORITATIVE == 1
    assert SourceTier.TIER_2_PRIMARY_UNSTRUCTURED == 2
    assert SourceTier.TIER_3_CORROBORATION == 3


def test_evidence_creation_valid() -> None:
    """Verify valid Evidence creation with all date fields."""
    now = datetime.now(timezone.utc)
    ev = Evidence(
        source_id="sec_edgar_19a1",
        source_tier=SourceTier.TIER_1_AUTHORITATIVE,
        url="https://www.sec.gov/Archives/edgar/data/sample.htm",
        retrieved_at=now,
        snippet_or_locator="Notice under Section 19(a)",
        declaration_date_found=date(2026, 3, 1),
        ex_date_found=date(2026, 3, 15),
        record_date_found=date(2026, 3, 16),
        payable_date_found=date(2026, 3, 20),
    )
    assert ev.source_id == "sec_edgar_19a1"
    assert ev.source_tier == SourceTier.TIER_1_AUTHORITATIVE
    assert ev.declaration_date_found == date(2026, 3, 1)
    assert ev.failure_reason is None


def test_evidence_validation_failures() -> None:
    """Verify Evidence validates mandatory parameters strictly."""
    now = datetime.now(timezone.utc)

    # Empty source_id
    with pytest.raises(ValueError, match="source_id"):
        Evidence(
            source_id="",
            source_tier=SourceTier.TIER_1_AUTHORITATIVE,
            url="https://example.com",
            retrieved_at=now,
            snippet_or_locator="snippet",
        )

    # Invalid source_tier
    with pytest.raises(TypeError, match="source_tier"):
        Evidence(
            source_id="test",
            source_tier="INVALID_TIER",  # type: ignore
            url="https://example.com",
            retrieved_at=now,
            snippet_or_locator="snippet",
        )

    # Empty URL
    with pytest.raises(ValueError, match="url"):
        Evidence(
            source_id="test",
            source_tier=SourceTier.TIER_1_AUTHORITATIVE,
            url="",
            retrieved_at=now,
            snippet_or_locator="snippet",
        )

    # Non-datetime retrieved_at
    with pytest.raises(TypeError, match="retrieved_at"):
        Evidence(
            source_id="test",
            source_tier=SourceTier.TIER_1_AUTHORITATIVE,
            url="https://example.com",
            retrieved_at="2026-03-01",  # type: ignore
            snippet_or_locator="snippet",
        )


def test_detection_result_valid_declared() -> None:
    """Verify valid DECLARED DetectionResult."""
    now = datetime.now(timezone.utc)
    ev = Evidence(
        source_id="sec_edgar",
        source_tier=SourceTier.TIER_1_AUTHORITATIVE,
        url="https://www.sec.gov/sample",
        retrieved_at=now,
        snippet_or_locator="Filing 497 text",
        declaration_date_found=date(2026, 3, 1),
    )
    result = DetectionResult(
        fund_id="TEST_FUND_001",
        status=DetectionStatus.DECLARED,
        confidence=1.0,
        evidence=[ev],
        suggested_extraction_route=ExtractionRoute.FILING,
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 31),
    )
    assert result.fund_id == "TEST_FUND_001"
    assert result.status == DetectionStatus.DECLARED
    assert result.confidence == 1.0
    assert len(result.evidence) == 1
    assert result.suggested_extraction_route == ExtractionRoute.FILING


def test_detection_result_valid_unknown() -> None:
    """Verify valid UNKNOWN DetectionResult with failure reason."""
    now = datetime.now(timezone.utc)
    ev = Evidence(
        source_id="fund_website",
        source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
        url="https://fundcompany.example.com/distributions",
        retrieved_at=now,
        snippet_or_locator="HTTP 503 Gateway Timeout",
        failure_reason=UnknownReason.SOURCE_UNAVAILABLE,
    )
    result = DetectionResult(
        fund_id="TEST_FUND_002",
        status=DetectionStatus.UNKNOWN,
        confidence=None,
        evidence=[ev],
        suggested_extraction_route=None,
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 31),
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.confidence is None
    assert result.evidence[0].failure_reason == UnknownReason.SOURCE_UNAVAILABLE


def test_detection_result_validation_failures() -> None:
    """Verify validation errors when constructing invalid DetectionResult."""
    now = datetime.now(timezone.utc)
    ev = Evidence(
        source_id="source",
        source_tier=SourceTier.TIER_1_AUTHORITATIVE,
        url="https://example.com",
        retrieved_at=now,
        snippet_or_locator="locator",
    )

    # Missing evidence
    with pytest.raises(ValueError, match="at least one Evidence"):
        DetectionResult(
            fund_id="FUND",
            status=DetectionStatus.DECLARED,
            confidence=1.0,
            evidence=[],
        )

    # Inverted date window
    with pytest.raises(ValueError, match=r"window_start .* cannot be after window_end"):
        DetectionResult(
            fund_id="FUND",
            status=DetectionStatus.DECLARED,
            confidence=1.0,
            evidence=[ev],
            window_start=date(2026, 4, 1),
            window_end=date(2026, 3, 1),
        )

    # Confidence out of bounds
    with pytest.raises(ValueError, match="Confidence must be between 0.0 and 1.0"):
        DetectionResult(
            fund_id="FUND",
            status=DetectionStatus.DECLARED,
            confidence=1.5,
            evidence=[ev],
        )
