"""Unit and integration tests for Layer B High-Precision Extraction Engine."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.detector import detect_distribution
from src.extractor import extract_distribution
from src.models import (
    CAComponentType,
    DetectionResult,
    DetectionStatus,
    Evidence,
    ExtractionRoute,
    SourceTier,
    USComponentType,
)
from src.parsers.filing_parser import parse_sec_rule_19a1_filing
from src.parsers.html_table_parser import parse_html_distribution_tables
from src.strategies import OfficialSponsorWebStrategy
from src.universe_loader import UniverseRegistry
from tests.fakes import StubHTTPClient


@pytest.fixture
def universe_registry() -> UniverseRegistry:
    return UniverseRegistry.from_json()


def test_extraction_only_accepts_declared() -> None:
    """Layer B must reject NOT_DECLARED or UNKNOWN detection results."""
    dummy_ev = Evidence(
        source_id="test",
        source_tier=SourceTier.TIER_1_AUTHORITATIVE,
        url="http://test",
        retrieved_at=datetime.now(timezone.utc),
        snippet_or_locator="dummy",
    )
    res_unknown = DetectionResult(
        fund_id="US_VANGUARD_VOO",
        status=DetectionStatus.UNKNOWN,
        confidence=0.0,
        evidence=[dummy_ev],
    )
    with pytest.raises(ValueError, match="only accepts DECLARED"):
        extract_distribution(res_unknown)


VOO_PAGE = """<html><body><h1>Vanguard S&P 500 ETF (VOO) - distributions</h1>
<table>
<tr><th>Type</th><th>Ex-Dividend Date</th><th>Record Date</th><th>Payable Date</th><th>Cash Amount</th></tr>
<tr><td>Dividend</td><td>12/23/2024</td><td>12/23/2024</td><td>12/26/2024</td><td>$1.7385</td></tr>
<tr><td>Dividend</td><td>09/27/2024</td><td>09/27/2024</td><td>10/01/2024</td><td>$1.6386</td></tr>
</table></body></html>"""  # SYNTHETIC test fixture

ZCN_PAGE = """<html><body><h1>BMO S&P/TSX Capped Composite Index ETF (ZCN)</h1>
<table>
<tr><th>Ex-Date</th><th>Record Date</th><th>Payment Date</th><th>Distribution per Unit</th>
<th>Eligible Dividend</th><th>Capital Gains</th><th>Return of Capital</th></tr>
<tr><td>2024-12-30</td><td>2024-12-30</td><td>2025-01-06</td><td>0.2300</td><td>0.2000</td><td>0.0200</td><td>0.0100</td></tr>
</table></body></html>"""  # SYNTHETIC test fixture


def test_extract_us_flagship_voo(universe_registry: UniverseRegistry) -> None:
    """Extraction reads the page the detector cited; no tax component is invented."""
    fund = universe_registry.get_fund("US_VANGUARD_VOO")
    client = StubHTTPClient({fund.official_source_url: VOO_PAGE})
    detection = detect_distribution(
        fund_id="US_VANGUARD_VOO",
        window_start=date(2024, 12, 1),
        window_end=date(2024, 12, 31),
        strategies=[
            OfficialSponsorWebStrategy(http_client=client, universe=universe_registry)
        ],
        universe=universe_registry,
    )
    assert detection.status == DetectionStatus.DECLARED

    events = extract_distribution(
        detection, http_client=client, universe=universe_registry
    )
    assert len(events) == 1
    ev = events[0]
    assert (ev.ticker, ev.currency, ev.gross_amount) == ("VOO", "USD", 1.7385)
    assert ev.ex_date == date(2024, 12, 23)
    assert ev.payable_date == date(2024, 12, 26)
    assert ev.extraction_route == ExtractionRoute.HTML_TABLE
    assert ev.source_url == fund.official_source_url
    # The page publishes no tax breakdown, so none is stored (it used to be faked as 100% ordinary income).
    assert ev.components == []
    assert ev.components_reported is False


def test_extract_canadian_flagship_zcn(universe_registry: UniverseRegistry) -> None:
    """Published Canadian components are captured exactly as the page states them."""
    fund = universe_registry.get_fund("CA_BMO_ZCN")
    client = StubHTTPClient({fund.official_source_url: ZCN_PAGE})
    detection = detect_distribution(
        fund_id="CA_BMO_ZCN",
        window_start=date(2024, 12, 1),
        window_end=date(2024, 12, 31),
        strategies=[
            OfficialSponsorWebStrategy(http_client=client, universe=universe_registry)
        ],
        universe=universe_registry,
    )
    assert detection.status == DetectionStatus.DECLARED
    ev = extract_distribution(
        detection, http_client=client, universe=universe_registry
    )[0]
    assert (ev.currency, ev.gross_amount, ev.ex_date, ev.payable_date) == (
        "CAD",
        0.23,
        date(2024, 12, 30),
        date(2025, 1, 6),
    )
    comps = {c.component_type: c.amount for c in ev.components}
    assert comps == {
        CAComponentType.ELIGIBLE_DIVIDEND: 0.20,
        CAComponentType.CAPITAL_GAINS: 0.02,
        CAComponentType.RETURN_OF_CAPITAL: 0.01,
    }


def test_extractor_never_reads_config_schedules(
    universe_registry: UniverseRegistry,
) -> None:
    """Bug: when no route worked, figures were copied from config/*_distribution_schedules.json
    and labelled HTML_TABLE. Now the result is a MANUAL review placeholder."""
    fund = universe_registry.get_fund("US_VANGUARD_VOO")
    detection = DetectionResult(
        fund_id=fund.fund_id,
        status=DetectionStatus.DECLARED,
        confidence=0.9,
        evidence=[
            Evidence(
                source_id="official_fund_sponsor_page",
                source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                url=fund.official_source_url,
                retrieved_at=datetime.now(timezone.utc),
                snippet_or_locator="row",
                ex_date_found=date(2024, 12, 23),
            )
        ],
        suggested_extraction_route=ExtractionRoute.HTML_TABLE,
        window_start=date(2024, 12, 1),
        window_end=date(2024, 12, 31),
    )
    events = extract_distribution(
        detection, http_client=StubHTTPClient({}), universe=universe_registry
    )
    assert len(events) == 1
    assert events[0].extraction_route == ExtractionRoute.MANUAL
    assert events[0].gross_amount == 0.0 and events[0].validation_passed is False


def test_api_route_parses_json_feed(universe_registry: UniverseRegistry) -> None:
    """Route 1 (API) used to parse the JSON and then ignore it."""
    from src.parsers.api_parser import parse_json_distributions

    feed = '{"data": {"distributions": [{"exDate": "2024-12-23", "payDate": "2024-12-26", "amount": "1.7385"}]}}'
    events = parse_json_distributions(
        feed, "US_VANGUARD_VOO", "US", "https://api.test/voo.json"
    )
    assert len(events) == 1
    assert events[0].extraction_route == ExtractionRoute.API
    assert (events[0].ex_date, events[0].gross_amount) == (date(2024, 12, 23), 1.7385)


def test_parse_sec_rule_19a1_filing_roc() -> None:
    """SEC Form 19a-1 Return of Capital notice extraction."""
    filing_html = """
    <html><body>
      <h1>Section 19(a) Notice to Shareholders</h1>
      <p>Total Distribution per Share: $0.5000</p>
      <table>
        <tr><th>Source</th><th>Amount per Share</th></tr>
        <tr><td>Net Investment Income</td><td>$0.3500</td></tr>
        <tr><td>Return of Capital (ROC)</td><td>$0.1500</td></tr>
      </table>
      <p>Ex-Date: 2024-12-15. Record Date: 2024-12-16.</p>
    </body></html>
    """
    events = parse_sec_rule_19a1_filing(
        filing_text=filing_html,
        fund_id="US_ISHARES_HYG",
        filing_url="https://www.sec.gov/Archives/edgar/data/19a1.htm",
        ticker="HYG",
        filing_date=date(2024, 12, 10),
    )
    assert len(events) == 1
    ev = events[0]
    assert ev.gross_amount == 0.5000
    assert len(ev.components) == 2
    nii_comp = next(
        c for c in ev.components if c.component_type == USComponentType.ORDINARY_INCOME
    )
    roc_comp = next(
        c
        for c in ev.components
        if c.component_type == USComponentType.RETURN_OF_CAPITAL
    )
    assert nii_comp.amount == 0.3500
    assert roc_comp.amount == 0.1500


def test_parse_html_multi_column_table() -> None:
    """Parse HTML multi-column table with capital gains and dividends."""
    html = """
    <table>
      <thead>
        <tr><th>Ticker</th><th>Ex-Date</th><th>Record Date</th><th>Payable Date</th><th>Amount ($)</th></tr>
      </thead>
      <tbody>
        <tr><td>ZCN</td><td>2024-12-30</td><td>2024-12-31</td><td>2025-01-06</td><td>$0.2300</td></tr>
        <tr><td>ZAG</td><td>2024-12-30</td><td>2024-12-31</td><td>2025-01-06</td><td>$0.0450</td></tr>
      </tbody>
    </table>
    """
    events = parse_html_distribution_tables(
        html_content=html,
        fund_id="CA_BMO_ZCN",
        country="CA",
        source_url="https://newsroom.bmo.com/etf-distributions",
        ticker="ZCN",
    )
    assert len(events) == 1
    assert events[0].ticker == "ZCN"
    assert events[0].gross_amount == 0.2300
    assert events[0].ex_date == date(2024, 12, 30)
