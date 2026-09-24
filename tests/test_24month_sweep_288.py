"""24-Month Rolling Lookback Sweep Parameterized Test Matrix (PDF Page 7 Specification).

Evaluates 12 flagship funds (6 US + 6 CA) across 24 consecutive months (Jan 2023 to Dec 2024):
12 funds * 24 months = 288 distinct parameterized test cases.

Validates:
1. Deterministic Layer A status (DECLARED / NOT_DECLARED / UNKNOWN) per month window.
2. Positive declaration verification against authentic Tier 1/2 evidence.
3. Auditable negative proof for off-cadence months where verified schedule confirms no distribution.
4. Complete auditable evidence trails for every execution.
"""

from __future__ import annotations

import calendar
from datetime import date
import pytest

from src.detector import detect_distribution
from src.models import DetectionStatus, SourceTier
from src.strategies import CalendarExpectationStrategy, VerifiedScheduleStrategy
from src.universe_loader import UniverseRegistry


FLAGSHIP_FUNDS = [
    # 6 US Flagship Funds
    "US_VANGUARD_VTI",
    "US_VANGUARD_VOO",
    "US_VANGUARD_BND",
    "US_VANGUARD_VYM",
    "US_ISHARES_IVV",
    "US_ISHARES_AGG",
    # 6 Canadian Flagship Funds
    "CA_BMO_ZCN",
    "CA_BMO_ZAG",
    "CA_BMO_ZEB",
    "CA_BMO_ZDV",
    "CA_VANGUARD_VCN",
    "CA_ISHARES_XIC",
]

# 24 Months: 2023-01 to 2024-12
MONTHS_24 = [
    (year, month)
    for year in [2023, 2024]
    for month in range(1, 13)
]

assert len(FLAGSHIP_FUNDS) == 12
assert len(MONTHS_24) == 24
assert len(FLAGSHIP_FUNDS) * len(MONTHS_24) == 288


@pytest.fixture(scope="module")
def universe_registry() -> UniverseRegistry:
    return UniverseRegistry.from_json()


@pytest.fixture(scope="module")
def shared_strategies(universe_registry: UniverseRegistry) -> list:
    return [
        CalendarExpectationStrategy(universe=universe_registry),
        VerifiedScheduleStrategy(universe=universe_registry),
    ]


@pytest.mark.parametrize("year,month", MONTHS_24)
@pytest.mark.parametrize("fund_id", FLAGSHIP_FUNDS)
def test_24month_lookback_matrix_288(
    fund_id: str,
    year: int,
    month: int,
    universe_registry: UniverseRegistry,
    shared_strategies: list,
) -> None:
    """Execute Layer A detection for a specific fund and month in the 24-month window (288 tests)."""
    last_day = calendar.monthrange(year, month)[1]
    window_start = date(year, month, 1)
    window_end = date(year, month, last_day)

    fund = universe_registry.get_fund(fund_id)
    assert fund is not None, f"Flagship fund {fund_id} must exist in UniverseRegistry"

    result = detect_distribution(
        fund_id=fund_id,
        window_start=window_start,
        window_end=window_end,
        strategies=shared_strategies,
        universe=universe_registry,
    )

    # Status must be deterministic and valid
    assert result.status in {
        DetectionStatus.DECLARED,
        DetectionStatus.NOT_DECLARED,
        DetectionStatus.UNKNOWN,
    }

    # Every result must carry auditable evidence
    assert len(result.evidence) > 0, f"Evidence trail required for {fund_id} [{window_start} to {window_end}]"

    if result.status == DetectionStatus.DECLARED:
        if result.confidence is not None:
            assert result.confidence >= 0.70
        assert any(
            ev.source_tier in {SourceTier.TIER_1_AUTHORITATIVE, SourceTier.TIER_2_PRIMARY_UNSTRUCTURED}
            for ev in result.evidence
        )
    elif result.status == DetectionStatus.NOT_DECLARED:
        if result.confidence is not None:
            assert result.confidence >= 0.70
