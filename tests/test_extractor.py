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
from src.universe_loader import UniverseRegistry


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


def test_extract_us_flagship_voo(universe_registry: UniverseRegistry) -> None:
    """VOO in Dec 2024 extracts $1.7385/share with US components."""
    detection = detect_distribution(
        fund_id="US_VANGUARD_VOO",
        window_start=date(2024, 12, 1),
        window_end=date(2024, 12, 31),
        universe=universe_registry,
    )
    assert detection.status == DetectionStatus.DECLARED

    events = extract_distribution(detection, universe=universe_registry)
    assert len(events) >= 1
    ev = events[0]
    assert ev.fund_id == "US_VANGUARD_VOO"
    assert ev.ticker == "VOO"
    assert ev.currency == "USD"
    assert ev.gross_amount == 1.7385
    assert ev.ex_date == date(2024, 12, 23)
    assert ev.payable_date == date(2024, 12, 26)
    assert len(ev.components) >= 1
    assert ev.components[0].component_type == USComponentType.ORDINARY_INCOME
    assert ev.components[0].amount == 1.7385


def test_extract_canadian_flagship_zcn(universe_registry: UniverseRegistry) -> None:
    """ZCN in Dec 2024 extracts $0.2300 CAD/unit with Canadian components."""
    detection = detect_distribution(
        fund_id="CA_BMO_ZCN",
        window_start=date(2024, 12, 1),
        window_end=date(2024, 12, 31),
        universe=universe_registry,
    )
    assert detection.status == DetectionStatus.DECLARED

    events = extract_distribution(detection, universe=universe_registry)
    assert len(events) >= 1
    ev = events[0]
    assert ev.fund_id == "CA_BMO_ZCN"
    assert ev.ticker == "ZCN"
    assert ev.currency == "CAD"
    assert ev.gross_amount == 0.2300
    assert ev.ex_date == date(2024, 12, 30)
    assert ev.payable_date == date(2025, 1, 6)
    assert len(ev.components) >= 1
    assert ev.components[0].component_type == CAComponentType.ELIGIBLE_DIVIDEND
    assert ev.components[0].amount == 0.2300


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
    nii_comp = next(c for c in ev.components if c.component_type == USComponentType.ORDINARY_INCOME)
    roc_comp = next(c for c in ev.components if c.component_type == USComponentType.RETURN_OF_CAPITAL)
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
