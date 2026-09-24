"""Unit tests for Layer A Atomic Detector in src/detector.py.

All fixtures and values used here are purely SYNTHETIC / TEST values for unit testing.
No real or assumed fund/financial data is used.
"""

from datetime import date, datetime, timezone

import pytest

from src.detector import detect_distribution, synthesize_detection_result
from src.models import (
    DetectionStatus,
    Evidence,
    ExtractionRoute,
    SourceTier,
    UnknownReason,
)
from src.strategies import (
    BaseDetectionStrategy,
    CalendarExpectationStrategy,
    SignalType,
    StrategyObservation,
)


class SyntheticMockStrategy(BaseDetectionStrategy):
    """SYNTHETIC test strategy that returns a pre-configured observation."""

    def __init__(
        self,
        name: str = "SYNTHETIC_MOCK_STRATEGY",
        observation: StrategyObservation | None = None,
    ) -> None:
        super().__init__(name=name)
        self._observation = observation

    def set_observation(self, observation: StrategyObservation) -> None:
        self._observation = observation

    def inspect(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
    ) -> StrategyObservation:
        if self._observation is not None:
            return self._observation
        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.NO_DATA_OBSERVATION,
            has_declaration_in_window=False,
            window_fully_covered=False,
            failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
        )


def _make_synthetic_evidence(
    source_id: str = "SYNTHETIC_TEST_SRC",
    source_tier: SourceTier = SourceTier.TIER_1_AUTHORITATIVE,
    decl_date: date | None = None,
    failure_reason: UnknownReason | None = None,
) -> Evidence:
    """Helper to create a synthetic Evidence instance."""
    return Evidence(
        source_id=source_id,
        source_tier=source_tier,
        url="https://synthetic-test.example.org/item",
        retrieved_at=datetime.now(timezone.utc),
        snippet_or_locator="SYNTHETIC_LOCATOR",
        declaration_date_found=decl_date,
        failure_reason=failure_reason,
    )


def test_valid_declaration_inside_window() -> None:
    """A. Valid declaration inside window returns DECLARED with confidence and route."""
    decl_date = date(2026, 2, 15)
    ev = _make_synthetic_evidence(
        source_tier=SourceTier.TIER_1_AUTHORITATIVE,
        decl_date=decl_date,
    )
    obs = StrategyObservation(
        strategy_name="SYNTHETIC_TEST_STRATEGY",
        signal_type=SignalType.EVIDENCE_OBSERVATION,
        evidence=[ev],
        has_declaration_in_window=True,
        window_fully_covered=True,
        suggested_route=ExtractionRoute.FILING,
    )
    strategy = SyntheticMockStrategy(observation=obs)
    res = detect_distribution(
        fund_id="SYNTHETIC_TEST_FUND_001",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[strategy],
    )
    assert res.status == DetectionStatus.DECLARED
    assert res.confidence == 1.0
    assert res.suggested_extraction_route == ExtractionRoute.FILING
    assert len(res.evidence) >= 1
    assert res.evidence[0].declaration_date_found == decl_date


def test_declaration_exact_start_boundary() -> None:
    """B. Declaration date exactly on window_start returns DECLARED."""
    window_start = date(2026, 2, 1)
    ev = _make_synthetic_evidence(decl_date=window_start)
    obs = StrategyObservation(
        strategy_name="SYNTHETIC_TEST_STRATEGY",
        signal_type=SignalType.EVIDENCE_OBSERVATION,
        evidence=[ev],
        has_declaration_in_window=True,
    )
    strategy = SyntheticMockStrategy(observation=obs)
    res = detect_distribution(
        fund_id="SYNTHETIC_TEST_FUND_002",
        window_start=window_start,
        window_end=date(2026, 2, 28),
        strategies=[strategy],
    )
    assert res.status == DetectionStatus.DECLARED
    assert res.confidence == 1.0


def test_declaration_exact_end_boundary() -> None:
    """C. Declaration date exactly on window_end returns DECLARED."""
    window_end = date(2026, 2, 28)
    ev = _make_synthetic_evidence(decl_date=window_end)
    obs = StrategyObservation(
        strategy_name="SYNTHETIC_TEST_STRATEGY",
        signal_type=SignalType.EVIDENCE_OBSERVATION,
        evidence=[ev],
        has_declaration_in_window=True,
    )
    strategy = SyntheticMockStrategy(observation=obs)
    res = detect_distribution(
        fund_id="SYNTHETIC_TEST_FUND_003",
        window_start=date(2026, 2, 1),
        window_end=window_end,
        strategies=[strategy],
    )
    assert res.status == DetectionStatus.DECLARED
    assert res.confidence == 1.0


def test_declaration_outside_window_not_declared() -> None:
    """D. Declaration date outside window must NOT return DECLARED."""
    decl_date_before = date(2026, 1, 31)
    ev = _make_synthetic_evidence(decl_date=decl_date_before)
    obs = StrategyObservation(
        strategy_name="SYNTHETIC_TEST_STRATEGY",
        signal_type=SignalType.EVIDENCE_OBSERVATION,
        evidence=[ev],
        has_declaration_in_window=False,
        window_fully_covered=False,
    )
    strategy = SyntheticMockStrategy(observation=obs)
    res = detect_distribution(
        fund_id="SYNTHETIC_TEST_FUND_004",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[strategy],
    )
    assert res.status != DetectionStatus.DECLARED
    assert res.status == DetectionStatus.UNKNOWN
    assert res.confidence == 0.0


def test_calendar_expectation_alone_never_declared() -> None:
    """E. Calendar expectation strategy alone MUST NOT produce DECLARED or NOT_DECLARED."""
    cal_strategy = CalendarExpectationStrategy()
    res = detect_distribution(
        fund_id="SYNTHETIC_TEST_FUND_005",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[cal_strategy],
    )
    assert res.status == DetectionStatus.UNKNOWN
    assert res.confidence == 0.0
    assert len(res.evidence) >= 1


def test_incomplete_evidence_returns_unknown_with_reason() -> None:
    """F. Incomplete evidence returns UNKNOWN with INCOMPLETE_SOURCE."""
    ev = _make_synthetic_evidence(failure_reason=UnknownReason.INCOMPLETE_SOURCE)
    obs = StrategyObservation(
        strategy_name="SYNTHETIC_TEST_STRATEGY",
        signal_type=SignalType.NO_DATA_OBSERVATION,
        evidence=[ev],
        window_fully_covered=False,
        failure_reason=UnknownReason.INCOMPLETE_SOURCE,
    )
    strategy = SyntheticMockStrategy(observation=obs)
    res = detect_distribution(
        fund_id="SYNTHETIC_TEST_FUND_006",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[strategy],
    )
    assert res.status == DetectionStatus.UNKNOWN
    assert res.confidence == 0.0


def test_source_unavailable_returns_unknown() -> None:
    """G. Unavailable source returns UNKNOWN with SOURCE_UNAVAILABLE."""
    ev = _make_synthetic_evidence(failure_reason=UnknownReason.SOURCE_UNAVAILABLE)
    obs = StrategyObservation(
        strategy_name="SYNTHETIC_TEST_STRATEGY",
        signal_type=SignalType.NO_DATA_OBSERVATION,
        evidence=[ev],
        window_fully_covered=False,
        failure_reason=UnknownReason.SOURCE_UNAVAILABLE,
    )
    strategy = SyntheticMockStrategy(observation=obs)
    res = detect_distribution(
        fund_id="SYNTHETIC_TEST_FUND_007",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[strategy],
    )
    assert res.status == DetectionStatus.UNKNOWN
    assert res.confidence == 0.0


def test_conflicting_evidence_returns_unknown_conflicting() -> None:
    """H. Conflicting evidence (positive vs negative on full window) returns UNKNOWN."""
    ev_pos = _make_synthetic_evidence(decl_date=date(2026, 2, 10))
    ev_neg = _make_synthetic_evidence(decl_date=None)

    obs_pos = StrategyObservation(
        strategy_name="SYNTHETIC_SOURCE_A",
        signal_type=SignalType.EVIDENCE_OBSERVATION,
        evidence=[ev_pos],
        has_declaration_in_window=True,
    )
    obs_neg = StrategyObservation(
        strategy_name="SYNTHETIC_SOURCE_B",
        signal_type=SignalType.NO_DATA_OBSERVATION,
        evidence=[ev_neg],
        has_declaration_in_window=False,
        window_fully_covered=True,  # Claims complete coverage with no declaration
    )

    res = synthesize_detection_result(
        fund_id="SYNTHETIC_TEST_FUND_008",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        observations=[obs_pos, obs_neg],
    )
    assert res.status == DetectionStatus.UNKNOWN
    assert res.confidence == 0.0


def test_no_sufficient_negative_evidence_returns_unknown_not_not_declared() -> None:
    """I. Lack of declaration without demonstrably complete window coverage returns UNKNOWN, not NOT_DECLARED."""
    ev = _make_synthetic_evidence()
    obs = StrategyObservation(
        strategy_name="SYNTHETIC_TEST_STRATEGY",
        signal_type=SignalType.NO_DATA_OBSERVATION,
        evidence=[ev],
        has_declaration_in_window=False,
        window_fully_covered=False,  # Incomplete coverage
    )
    strategy = SyntheticMockStrategy(observation=obs)
    res = detect_distribution(
        fund_id="SYNTHETIC_TEST_FUND_009",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[strategy],
    )
    assert res.status == DetectionStatus.UNKNOWN
    assert res.status != DetectionStatus.NOT_DECLARED


def test_demonstrably_complete_negative_evidence_returns_not_declared() -> None:
    """M. Demonstrably complete authoritative coverage with no declaration returns NOT_DECLARED."""
    ev = _make_synthetic_evidence(source_tier=SourceTier.TIER_1_AUTHORITATIVE)
    obs = StrategyObservation(
        strategy_name="SYNTHETIC_TEST_STRATEGY",
        signal_type=SignalType.NO_DATA_OBSERVATION,
        evidence=[ev],
        has_declaration_in_window=False,
        window_fully_covered=True,  # Complete coverage
    )
    strategy = SyntheticMockStrategy(observation=obs)
    res = detect_distribution(
        fund_id="SYNTHETIC_TEST_FUND_010",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[strategy],
    )
    assert res.status == DetectionStatus.NOT_DECLARED
    assert res.confidence == 1.0


def test_invalid_date_window_raises_error() -> None:
    """J. Inverted date window (window_start > window_end) raises ValueError."""
    strategy = SyntheticMockStrategy()
    with pytest.raises(ValueError, match="cannot be after window_end"):
        detect_distribution(
            fund_id="SYNTHETIC_TEST_FUND_011",
            window_start=date(2026, 3, 1),
            window_end=date(2026, 2, 1),
            strategies=[strategy],
        )


def test_unconfigured_strategies_returns_unknown_with_evidence() -> None:
    """K. Result must always contain at least one Evidence item."""
    res = detect_distribution(
        fund_id="SYNTHETIC_TEST_FUND_012",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=None,
    )
    assert res.status == DetectionStatus.UNKNOWN
    assert len(res.evidence) >= 1
    assert isinstance(res.evidence[0], Evidence)
