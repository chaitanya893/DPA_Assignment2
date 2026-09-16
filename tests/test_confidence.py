"""Unit tests for deterministic confidence scoring logic in src/confidence.py.

All fixtures and values used here are purely SYNTHETIC / TEST values for unit testing.
No real or assumed fund/financial data is used.
"""

from datetime import date, datetime, timezone

import pytest

from src.confidence import compute_confidence
from src.models import DetectionStatus, Evidence, SourceTier


@pytest.fixture
def synthetic_evidence_tier1_explicit() -> Evidence:
    """SYNTHETIC fixture: Tier 1 authoritative with explicit declaration date."""
    return Evidence(
        source_id="SYNTHETIC_TEST_SOURCE_TIER1",
        source_tier=SourceTier.TIER_1_AUTHORITATIVE,
        url="https://synthetic-test.example.gov/filing",
        retrieved_at=datetime.now(timezone.utc),
        snippet_or_locator="SYNTHETIC_TEST_LOCATOR: explicit declaration found",
        declaration_date_found=date(2026, 1, 15),
    )


@pytest.fixture
def synthetic_evidence_tier2_explicit() -> Evidence:
    """SYNTHETIC fixture: Tier 2 primary unstructured with explicit declaration date."""
    return Evidence(
        source_id="SYNTHETIC_TEST_SOURCE_TIER2",
        source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
        url="https://synthetic-test.example.com/press-release",
        retrieved_at=datetime.now(timezone.utc),
        snippet_or_locator="SYNTHETIC_TEST_LOCATOR: declared on Jan 15",
        declaration_date_found=date(2026, 1, 15),
    )


@pytest.fixture
def synthetic_evidence_tier2_no_declaration_date() -> Evidence:
    """SYNTHETIC fixture: Tier 2 primary unstructured WITHOUT explicit declaration date."""
    return Evidence(
        source_id="SYNTHETIC_TEST_SOURCE_TIER2_NO_DECL",
        source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
        url="https://synthetic-test.example.com/schedule",
        retrieved_at=datetime.now(timezone.utc),
        snippet_or_locator="SYNTHETIC_TEST_LOCATOR: schedule table without explicit declaration date",
        declaration_date_found=None,
        ex_date_found=date(2026, 1, 20),
    )


@pytest.fixture
def synthetic_evidence_tier3_only() -> Evidence:
    """SYNTHETIC fixture: Tier 3 corroboration only."""
    return Evidence(
        source_id="SYNTHETIC_TEST_SOURCE_TIER3",
        source_tier=SourceTier.TIER_3_CORROBORATION,
        url="https://synthetic-test.example.org/market-check",
        retrieved_at=datetime.now(timezone.utc),
        snippet_or_locator="SYNTHETIC_TEST_LOCATOR: third party note",
    )


def test_confidence_for_unknown_is_none(
    synthetic_evidence_tier1_explicit: Evidence,
) -> None:
    """UNKNOWN status must always return None confidence."""
    conf = compute_confidence(
        DetectionStatus.UNKNOWN, [synthetic_evidence_tier1_explicit]
    )
    assert conf is None


def test_confidence_for_empty_evidence() -> None:
    """Empty evidence must return None confidence."""
    conf = compute_confidence(DetectionStatus.DECLARED, [])
    assert conf is None


def test_confidence_declared_tier1_explicit(
    synthetic_evidence_tier1_explicit: Evidence,
) -> None:
    """DECLARED + Tier 1 + explicit declaration evidence yields 1.0."""
    conf = compute_confidence(
        DetectionStatus.DECLARED, [synthetic_evidence_tier1_explicit]
    )
    assert conf == 1.0


def test_confidence_declared_tier2_explicit(
    synthetic_evidence_tier2_explicit: Evidence,
) -> None:
    """DECLARED + Tier 2 + explicit declaration evidence yields 0.90."""
    conf = compute_confidence(
        DetectionStatus.DECLARED, [synthetic_evidence_tier2_explicit]
    )
    assert conf == 0.90


def test_confidence_declared_tier2_without_declaration_date_is_none(
    synthetic_evidence_tier2_no_declaration_date: Evidence,
) -> None:
    """DECLARED + Tier 2 + no explicit declaration date MUST return None."""
    conf = compute_confidence(
        DetectionStatus.DECLARED,
        [synthetic_evidence_tier2_no_declaration_date],
    )
    assert conf is None


def test_confidence_declared_tier3_only_is_none(
    synthetic_evidence_tier3_only: Evidence,
) -> None:
    """DECLARED + Tier 3 only must return None."""
    conf = compute_confidence(DetectionStatus.DECLARED, [synthetic_evidence_tier3_only])
    assert conf is None


def test_confidence_not_declared_window_covered_tier1(
    synthetic_evidence_tier1_explicit: Evidence,
) -> None:
    """NOT_DECLARED + complete window coverage + Tier 1 yields 1.0."""
    conf = compute_confidence(
        DetectionStatus.NOT_DECLARED,
        [synthetic_evidence_tier1_explicit],
        window_covered=True,
    )
    assert conf == 1.0


def test_confidence_not_declared_window_covered_tier2(
    synthetic_evidence_tier2_explicit: Evidence,
) -> None:
    """NOT_DECLARED + complete window coverage + Tier 2 yields 0.85."""
    conf = compute_confidence(
        DetectionStatus.NOT_DECLARED,
        [synthetic_evidence_tier2_explicit],
        window_covered=True,
    )
    assert conf == 0.85


def test_confidence_not_declared_incomplete_window_is_none(
    synthetic_evidence_tier1_explicit: Evidence,
) -> None:
    """NOT_DECLARED with incomplete window coverage must return None."""
    conf = compute_confidence(
        DetectionStatus.NOT_DECLARED,
        [synthetic_evidence_tier1_explicit],
        window_covered=False,
    )
    assert conf is None


def test_confidence_not_declared_tier3_only_is_none(
    synthetic_evidence_tier3_only: Evidence,
) -> None:
    """NOT_DECLARED with Tier 3 only must return None."""
    conf = compute_confidence(
        DetectionStatus.NOT_DECLARED,
        [synthetic_evidence_tier3_only],
        window_covered=True,
    )
    assert conf is None
