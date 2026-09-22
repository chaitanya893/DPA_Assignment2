"""Automated month-by-month cadence matrix test for Canadian 40 Funds (Jan-Dec).

Validates:
1. Every month (1 to 12) partitions the 40 Canadian funds into exact Expected and Not-Expected sets.
2. Monthly funds (28) are expected in all 12 months.
3. Quarterly funds (8) are expected only in {3, 6, 9, 12}.
4. Semi-Annual funds (2) are expected only in {6, 12}.
5. Annual funds (2) are expected only in {12}.
6. Off-cadence evaluation in CalendarExpectationStrategy emits OFF-CADENCE note without deciding status.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.strategies import CalendarExpectationStrategy, SignalType
from src.universe_loader import UniverseRegistry


@pytest.fixture
def universe_registry() -> UniverseRegistry:
    return UniverseRegistry.from_json()


MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


@pytest.mark.parametrize("month_num", range(1, 13))
def test_canadian_month_by_month_cadence_matrix(
    month_num: int, universe_registry: UniverseRegistry
) -> None:
    """Validate expected vs not-expected Canadian fund partition for each calendar month."""
    ca_funds = [f for f in universe_registry.list_funds() if f.country == "CA"]
    assert len(ca_funds) == 40

    cal_strat = CalendarExpectationStrategy(universe=universe_registry)
    window_start = date(2026, month_num, 1)
    # End date of month
    if month_num in {1, 3, 5, 7, 8, 10, 12}:
        window_end = date(2026, month_num, 31)
    elif month_num in {4, 6, 9, 11}:
        window_end = date(2026, month_num, 30)
    else:
        window_end = date(2026, 2, 28)

    expected_funds = []
    not_expected_funds = []

    for f in ca_funds:
        obs = cal_strat.inspect(f.fund_id, window_start, window_end)
        assert obs.signal_type == SignalType.TRIGGER_SIGNAL
        assert obs.has_declaration_in_window is False  # Never decides status alone

        if "ON-CADENCE" in obs.notes:
            expected_funds.append(f.ticker or f.fundserv_code)
        else:
            assert "OFF-CADENCE" in obs.notes
            not_expected_funds.append(f.ticker or f.fundserv_code)

    assert len(expected_funds) + len(not_expected_funds) == 40

    if month_num in {1, 2, 4, 5, 7, 8, 10, 11}:
        # 28 Monthly funds expected, 12 non-monthly not expected
        assert (
            len(expected_funds) == 28
        ), f"Month {MONTH_NAMES[month_num-1]} expected 28 funds, got {len(expected_funds)}"
        assert len(not_expected_funds) == 12
    elif month_num in {3, 9}:
        # 28 Monthly + 8 Quarterly = 36 expected, 4 not expected (2 Semi-Annual + 2 Annual)
        assert (
            len(expected_funds) == 36
        ), f"Month {MONTH_NAMES[month_num-1]} expected 36 funds, got {len(expected_funds)}"
        assert len(not_expected_funds) == 4
        assert set(not_expected_funds) == {"TDB900", "TDB902", "HXT", "HBB"}
    elif month_num == 6:
        # 28 Monthly + 8 Quarterly + 2 Semi-Annual = 38 expected, 2 Annual not expected
        assert (
            len(expected_funds) == 38
        ), f"Month {MONTH_NAMES[month_num-1]} expected 38 funds, got {len(expected_funds)}"
        assert len(not_expected_funds) == 2
        assert set(not_expected_funds) == {"HXT", "HBB"}
    elif month_num == 12:
        # All 40 Canadian funds expected in December (Year-End distribution month)
        assert (
            len(expected_funds) == 40
        ), f"Month December expected 40 funds, got {len(expected_funds)}"
        assert len(not_expected_funds) == 0
