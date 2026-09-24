"""24-month lookback matrix: 12 flagship funds x 24 months (Jan 2023 - Dec 2024) = 288 cases.

Each fund's official page is replaced by a SYNTHETIC distribution table (offline test
fixture, not real data) with one row per expected pay month plus a December 2022 row so the
table demonstrably spans the whole period. For every month the real Layer A detector must:

- return DECLARED (confidence >= 0.85) in a pay month, with the table row as Tier 2 evidence;
- return NOT_DECLARED (confidence 0.85) in any other month, because the table covers it;
and Layer B must extract exactly the synthetic amount for pay months.

The previous version of this file asserted only that the status was one of the three enum
values, so all 288 cases passed whatever the detector did.
"""

from __future__ import annotations

import calendar
from datetime import date

import pytest

from src.detector import detect_distribution
from src.extractor import extract_distribution
from src.models import DetectionStatus, SourceTier
from src.strategies import CalendarExpectationStrategy, OfficialSponsorWebStrategy
from src.universe_loader import UniverseRegistry
from tests.fakes import StubHTTPClient

FLAGSHIP_FUNDS = [
    "US_VANGUARD_VTI",
    "US_VANGUARD_VOO",
    "US_VANGUARD_BND",
    "US_VANGUARD_VYM",
    "US_ISHARES_IVV",
    "US_ISHARES_AGG",
    "CA_BMO_ZCN",
    "CA_BMO_ZAG",
    "CA_BMO_ZEB",
    "CA_BMO_ZDV",
    "CA_VANGUARD_VCN",
    "CA_ISHARES_XIC",
]
MONTHS_24 = [(y, m) for y in (2023, 2024) for m in range(1, 13)]
assert len(FLAGSHIP_FUNDS) * len(MONTHS_24) == 288


def _is_pay_month(fund, year: int, month: int) -> bool:
    last = calendar.monthrange(year, month)[1]
    return CalendarExpectationStrategy.expected_in_window(
        fund, date(year, month, 1), date(year, month, last)
    )[0]


def _amount(year: int, month: int) -> float:
    return round(0.1 + month / 1000 + (year - 2023) / 100, 4)


def _synthetic_page(fund) -> str:
    rows = [
        "<tr><td>2022-12-20</td><td>2022-12-20</td><td>2022-12-28</td><td>$0.1000</td></tr>"
    ]
    for y, m in MONTHS_24:
        if _is_pay_month(fund, y, m):
            rows.append(
                f"<tr><td>{y}-{m:02d}-20</td><td>{y}-{m:02d}-20</td><td>{y}-{m:02d}-27</td><td>${_amount(y, m):.4f}</td></tr>"
            )
    return (
        f"<html><body><h1>{fund.fund_name} ({fund.ticker}) - SYNTHETIC TEST FIXTURE</h1><table>"
        "<tr><th>Ex-Dividend Date</th><th>Record Date</th><th>Payable Date</th><th>Amount per Unit</th></tr>"
        + "".join(rows)
        + "</table></body></html>"
    )


@pytest.fixture(scope="module")
def universe_registry() -> UniverseRegistry:
    return UniverseRegistry.from_json()


@pytest.fixture(scope="module")
def clients(universe_registry: UniverseRegistry) -> dict[str, StubHTTPClient]:
    out = {}
    for fid in FLAGSHIP_FUNDS:
        fund = universe_registry.get_fund(fid)
        out[fid] = StubHTTPClient({fund.official_source_url: _synthetic_page(fund)})
    return out


@pytest.mark.parametrize("year,month", MONTHS_24)
@pytest.mark.parametrize("fund_id", FLAGSHIP_FUNDS)
def test_24month_lookback_matrix_288(
    fund_id, year, month, universe_registry, clients
) -> None:
    fund = universe_registry.get_fund(fund_id)
    client = clients[fund_id]
    ws = date(year, month, 1)
    we = date(year, month, calendar.monthrange(year, month)[1])
    result = detect_distribution(
        fund_id,
        ws,
        we,
        strategies=[
            OfficialSponsorWebStrategy(http_client=client, universe=universe_registry)
        ],
        universe=universe_registry,
    )
    assert result.evidence, "every result must carry evidence"

    if _is_pay_month(fund, year, month):
        assert result.status == DetectionStatus.DECLARED
        assert result.confidence >= 0.85
        assert any(
            e.source_tier == SourceTier.TIER_2_PRIMARY_UNSTRUCTURED and e.ex_date_found
            for e in result.evidence
        )
        events = extract_distribution(
            result, http_client=client, universe=universe_registry
        )
        assert [(e.ex_date, e.gross_amount) for e in events] == [
            (date(year, month, 20), _amount(year, month))
        ]
        assert (
            events[0].components == []
        )  # the fixture publishes no tax split, so none is invented
    else:
        assert result.status == DetectionStatus.NOT_DECLARED
        assert result.confidence == 0.85
