"""Unit tests for the Real-Source Detection Workflow in Layer A Atomic Detector.

All mocked HTTP responses and test fixtures here are clearly labelled SYNTHETIC_TEST fixtures
for deterministic unit testing. No fabricated financial data is stored in the universe.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.detector import detect_distribution
from src.http_client import HTTPClient, HTTPResponseRecord
from src.models import (
    DetectionStatus,
    ExtractionRoute,
    SourceTier,
    UnknownReason,
)
from src.strategies import (
    CalendarExpectationStrategy,
    OfficialSponsorWebStrategy,
    SECEdgarSubmissionsStrategy,
)
from src.universe_loader import UniverseRegistry


@pytest.fixture
def universe_registry() -> UniverseRegistry:
    return UniverseRegistry.from_json()


def test_universe_registry_lookup(universe_registry: UniverseRegistry) -> None:
    """Verify universe registry lookup by fund_id and ticker."""
    vti = universe_registry.get_fund("US_VANGUARD_VTI")
    assert vti is not None
    assert vti.ticker == "VTI"
    assert vti.cik == "0000036405"
    assert vti.country == "US"
    assert vti.is_etf is True

    # Lookup by ticker
    voo = universe_registry.get_fund("VOO")
    assert voo is not None
    assert voo.fund_id == "US_VANGUARD_VOO"

    # Canadian lookup
    zag = universe_registry.get_fund("CA_BMO_ZAG")
    assert zag is not None
    assert zag.country == "CA"
    assert zag.ticker == "ZAG"


def test_sec_edgar_strategy_explicit_declaration_in_window(
    universe_registry: UniverseRegistry,
) -> None:
    """1. Explicit real declaration filing in window yields DECLARED with Tier 1 confidence."""
    synthetic_sec_json = {
        "filings": {
            "recent": {
                "form": ["497", "NPORT-P"],
                "filingDate": ["2026-03-15", "2026-02-28"],
                "accessionNumber": ["0001193125-26-000123", "0000036405-26-000456"],
                "primaryDocument": ["d123456d497.htm", "nport.htm"],
                "primaryDocDescription": [
                    "Notice of Dividend Distribution and Capital Gain",
                    "Monthly portfolio report",
                ],
            }
        }
    }
    synthetic_doc_html = """
    <html>
      <body>
        <h1>Vanguard Total Stock Market Index Fund (VTI)</h1>
        <p>Notice of Dividend Distribution: The Board of Trustees declared a quarterly distribution of $0.85 per share payable on 2026-03-25.</p>
      </body>
    </html>
    """

    mock_client = MagicMock(spec=HTTPClient)

    def mock_get(url: str, **kwargs):
        if "submissions/CIK" in url:
            return HTTPResponseRecord(
                url=url,
                status_code=200,
                retrieved_at=datetime.now(timezone.utc),
                content_text=json.dumps(synthetic_sec_json),
                content_bytes=json.dumps(synthetic_sec_json).encode(),
                failure_reason=None,
                is_success=True,
                elapsed_seconds=0.1,
            )
        return HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=datetime.now(timezone.utc),
            content_text=synthetic_doc_html,
            content_bytes=synthetic_doc_html.encode(),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get

    sec_strategy = SECEdgarSubmissionsStrategy(
        http_client=mock_client, universe=universe_registry
    )
    obs = sec_strategy.inspect(
        fund_id="US_VANGUARD_VTI",
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 31),
    )

    assert obs.has_declaration_in_window is True
    assert obs.suggested_route == ExtractionRoute.FILING
    assert len(obs.evidence) == 1
    assert obs.evidence[0].source_tier == SourceTier.TIER_1_AUTHORITATIVE
    assert obs.evidence[0].declaration_date_found == date(2026, 3, 15)

    # Verify synthesized detection result
    result = detect_distribution(
        fund_id="US_VANGUARD_VTI",
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 31),
        strategies=[sec_strategy],
    )
    assert result.status == DetectionStatus.DECLARED
    assert result.confidence == 1.0
    assert result.suggested_extraction_route == ExtractionRoute.FILING


def test_sec_edgar_strategy_rejects_leadership_announcement_filing(
    universe_registry: UniverseRegistry,
) -> None:
    """Form 497 with only leadership announcements does NOT produce positive declaration or full negative coverage."""
    synthetic_sec_json = {
        "filings": {
            "recent": {
                "form": ["497"],
                "filingDate": ["2026-02-11"],
                "accessionNumber": ["0001193125-26-045639"],
                "primaryDocument": ["f44014d1.htm"],
                "primaryDocDescription": ["497"],
            }
        }
    }
    leadership_html = """
    <html>
      <body>
        <h1>Leadership Announcements</h1>
        <p>Supplement Dated February 11, 2026. John Galloway resigned as Investment Stewardship Officer. David Hunt appointed trustee effective February 24, 2026.</p>
      </body>
    </html>
    """

    mock_client = MagicMock(spec=HTTPClient)

    def mock_get(url: str, **kwargs):
        if "submissions/CIK" in url:
            return HTTPResponseRecord(
                url=url,
                status_code=200,
                retrieved_at=datetime.now(timezone.utc),
                content_text=json.dumps(synthetic_sec_json),
                content_bytes=json.dumps(synthetic_sec_json).encode(),
                failure_reason=None,
                is_success=True,
                elapsed_seconds=0.1,
            )
        return HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=datetime.now(timezone.utc),
            content_text=leadership_html,
            content_bytes=leadership_html.encode(),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get

    sec_strategy = SECEdgarSubmissionsStrategy(
        http_client=mock_client, universe=universe_registry
    )
    obs = sec_strategy.inspect(
        fund_id="US_VANGUARD_VTI",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
    )

    assert obs.has_declaration_in_window is False
    assert obs.window_fully_covered is False
    assert obs.failure_reason == UnknownReason.INSUFFICIENT_EVIDENCE
    assert len(obs.evidence) == 1

    result = detect_distribution(
        fund_id="US_VANGUARD_VTI",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[sec_strategy],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.confidence is None


def test_sec_edgar_strategy_rejects_12b1_fee_table_filing(
    universe_registry: UniverseRegistry,
) -> None:
    """Form 497 with 12b-1 Distribution Fee in expense table does NOT count as a distribution declaration."""
    synthetic_sec_json = {
        "filings": {
            "recent": {
                "form": ["497"],
                "filingDate": ["2026-02-02"],
                "accessionNumber": ["0001193125-26-032833"],
                "primaryDocument": ["f43896d1.htm"],
                "primaryDocDescription": ["497"],
            }
        }
    }
    expense_table_html = """
    <html>
      <body>
        <h1>Vanguard Total Stock Market Index Fund</h1>
        <table>
          <tr><td>Management Fees</td><td>0.03%</td></tr>
          <tr><td>12b-1 Distribution Fee</td><td>None</td></tr>
          <tr><td>Other Expenses</td><td>0.00%</td></tr>
          <tr><td>Total Annual Fund Operating Expenses</td><td>0.03%</td></tr>
        </table>
      </body>
    </html>
    """

    mock_client = MagicMock(spec=HTTPClient)

    def mock_get(url: str, **kwargs):
        if "submissions/CIK" in url:
            return HTTPResponseRecord(
                url=url,
                status_code=200,
                retrieved_at=datetime.now(timezone.utc),
                content_text=json.dumps(synthetic_sec_json),
                content_bytes=json.dumps(synthetic_sec_json).encode(),
                failure_reason=None,
                is_success=True,
                elapsed_seconds=0.1,
            )
        return HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=datetime.now(timezone.utc),
            content_text=expense_table_html,
            content_bytes=expense_table_html.encode(),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get

    sec_strategy = SECEdgarSubmissionsStrategy(
        http_client=mock_client, universe=universe_registry
    )
    obs = sec_strategy.inspect(
        fund_id="US_VANGUARD_VTI",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
    )

    assert obs.has_declaration_in_window is False
    assert obs.window_fully_covered is False
    assert obs.failure_reason == UnknownReason.INSUFFICIENT_EVIDENCE
    assert len(obs.evidence) == 1

    result = detect_distribution(
        fund_id="US_VANGUARD_VTI",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[sec_strategy],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.confidence is None


def test_sec_edgar_strategy_incomplete_negative_coverage_returns_unknown(
    universe_registry: UniverseRegistry,
) -> None:
    """2. SEC submissions index with zero distribution filings does not claim complete negative proof -> UNKNOWN."""
    synthetic_sec_json = {
        "filings": {
            "recent": {
                "form": ["NPORT-P", "N-CSR"],
                "filingDate": ["2026-03-20", "2026-02-15"],
                "accessionNumber": ["0000036405-26-000100", "0000036405-26-000101"],
                "primaryDocument": ["nport.htm", "ncsr.htm"],
                "primaryDocDescription": ["Monthly report", "Annual report"],
            }
        }
    }

    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://data.sec.gov/submissions/CIK0000036405.json",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=json.dumps(synthetic_sec_json),
        content_bytes=json.dumps(synthetic_sec_json).encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )

    sec_strategy = SECEdgarSubmissionsStrategy(
        http_client=mock_client, universe=universe_registry
    )
    obs = sec_strategy.inspect(
        fund_id="US_VANGUARD_VTI",
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 31),
    )
    assert obs.has_declaration_in_window is False
    assert obs.window_fully_covered is False
    assert obs.failure_reason == UnknownReason.INSUFFICIENT_EVIDENCE

    result = detect_distribution(
        fund_id="US_VANGUARD_VTI",
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 31),
        strategies=[sec_strategy],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.status != DetectionStatus.NOT_DECLARED
    assert result.confidence is None
    assert len(result.evidence) >= 1


def test_sponsor_web_strategy_generic_page_no_schedule_table_yields_unknown(
    universe_registry: UniverseRegistry,
) -> None:
    """3 & 4. Sponsor page without an explicit static distribution schedule table cannot prove NOT_DECLARED -> UNKNOWN."""
    synthetic_html = """
    <html>
      <body>
        <h1>Vanguard Total Stock Market ETF (VTI)</h1>
        <p>VTI tracks the CRSP US Total Market Index. Asset Class: Large Blend.</p>
      </body>
    </html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://investor.vanguard.com/investment-products/etfs/profile/vti",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=synthetic_html,
        content_bytes=synthetic_html.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.2,
    )

    sponsor_strategy = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )
    obs = sponsor_strategy.inspect(
        fund_id="US_VANGUARD_VTI",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
    )
    assert obs.has_declaration_in_window is False
    assert obs.window_fully_covered is False
    assert obs.failure_reason == UnknownReason.INSUFFICIENT_EVIDENCE

    result = detect_distribution(
        fund_id="US_VANGUARD_VTI",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[sponsor_strategy],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.confidence is None


def test_sponsor_web_strategy_explicit_schedule_table_negative_coverage(
    universe_registry: UniverseRegistry,
) -> None:
    """6. Sponsor page with a verified static distribution schedule table confirming no distributions in window yields NOT_DECLARED."""
    synthetic_html = """
    <html>
      <body>
        <h1>BMO S&P/TSX Capped Composite Index ETF (ZCN)</h1>
        <h2>Distribution Schedule and History (2026)</h2>
        <table>
          <thead><tr><th>Ex-Dividend Date</th><th>Record Date</th><th>Payable Date</th><th>Distribution Per Share</th></tr></thead>
          <tbody>
            <tr><td>2026-01-15</td><td>2026-01-16</td><td>2026-01-23</td><td>$0.055</td></tr>
            <tr><td>2026-04-15</td><td>2026-04-16</td><td>2026-04-24</td><td>$0.058</td></tr>
          </tbody>
        </table>
      </body>
    </html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://www.bmogam.com/ca-en/investors/investment-solutions/etfs/bmo-sptsx-capped-composite-index-etf-zcn/",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=synthetic_html,
        content_bytes=synthetic_html.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.2,
    )

    sponsor_strategy = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )
    obs = sponsor_strategy.inspect(
        fund_id="CA_BMO_ZCN",
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 31),
    )
    assert obs.has_declaration_in_window is False
    assert obs.window_fully_covered is True

    result = detect_distribution(
        fund_id="CA_BMO_ZCN",
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 31),
        strategies=[sponsor_strategy],
    )
    assert result.status == DetectionStatus.NOT_DECLARED
    assert result.confidence == 0.85
    assert len(result.evidence) >= 1
    assert result.evidence[0].source_tier == SourceTier.TIER_2_PRIMARY_UNSTRUCTURED


def test_calendar_expectation_alone_strictly_unknown(
    universe_registry: UniverseRegistry,
) -> None:
    """7. Calendar expectation without declaration evidence MUST remain UNKNOWN, never NOT_DECLARED or DECLARED."""
    cal_strategy = CalendarExpectationStrategy()
    result = detect_distribution(
        fund_id="US_VANGUARD_BND",
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 31),
        strategies=[cal_strategy],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.status != DetectionStatus.DECLARED
    assert result.status != DetectionStatus.NOT_DECLARED
    assert result.confidence is None


def test_sec_edgar_strategy_http_failure_yields_unknown(
    universe_registry: UniverseRegistry,
) -> None:
    """7. SEC HTTP failure produces UNKNOWN with RETRIEVAL_FAILED."""
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://data.sec.gov/submissions/CIK0000036405.json",
        status_code=503,
        retrieved_at=datetime.now(timezone.utc),
        content_text=None,
        content_bytes=None,
        failure_reason=UnknownReason.SOURCE_UNAVAILABLE,
        is_success=False,
        elapsed_seconds=0.5,
        error_message="HTTP 503: Service Unavailable",
    )

    sec_strategy = SECEdgarSubmissionsStrategy(
        http_client=mock_client, universe=universe_registry
    )
    result = detect_distribution(
        fund_id="US_VANGUARD_VTI",
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 31),
        strategies=[sec_strategy],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.status != DetectionStatus.NOT_DECLARED
    assert result.confidence is None
    assert result.evidence[0].failure_reason == UnknownReason.SOURCE_UNAVAILABLE


def test_detector_conflicting_positive_and_negative_yields_unknown_conflicting(
    universe_registry: UniverseRegistry,
) -> None:
    """8. Conflicting positive declaration and confirmed negative coverage produces UNKNOWN (CONFLICTING_EVIDENCE)."""
    synthetic_sec_json = {
        "filings": {
            "recent": {
                "form": ["497"],
                "filingDate": ["2026-03-15"],
                "accessionNumber": ["0001193125-26-000123"],
                "primaryDocument": ["d123456d497.htm"],
                "primaryDocDescription": ["Notice of Dividend Distribution"],
            }
        }
    }
    synthetic_doc_html = "<p>Notice of Dividend Distribution: Declared distribution of $0.50 per share payable 2026-03-25.</p>"
    synthetic_negative_schedule_html = """
    <h2>Distribution Schedule (2026)</h2>
    <table><thead><tr><th>Ex-Dividend Date</th><th>Record Date</th><th>Payable Date</th><th>Distribution Per Share</th></tr></thead>
    <tbody><tr><td>2026-01-15</td><td>2026-01-16</td><td>2026-01-23</td><td>$0.055</td></tr></tbody></table>
    """

    mock_client = MagicMock(spec=HTTPClient)

    def mock_get(url: str, **kwargs):
        if "submissions/CIK" in url:
            return HTTPResponseRecord(
                url=url,
                status_code=200,
                retrieved_at=datetime.now(timezone.utc),
                content_text=json.dumps(synthetic_sec_json),
                content_bytes=json.dumps(synthetic_sec_json).encode(),
                failure_reason=None,
                is_success=True,
                elapsed_seconds=0.1,
            )
        if "Archives/edgar" in url:
            return HTTPResponseRecord(
                url=url,
                status_code=200,
                retrieved_at=datetime.now(timezone.utc),
                content_text=synthetic_doc_html,
                content_bytes=synthetic_doc_html.encode(),
                failure_reason=None,
                is_success=True,
                elapsed_seconds=0.1,
            )
        return HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=datetime.now(timezone.utc),
            content_text=synthetic_negative_schedule_html,
            content_bytes=synthetic_negative_schedule_html.encode(),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get

    sec_strategy = SECEdgarSubmissionsStrategy(
        http_client=mock_client, universe=universe_registry
    )
    sponsor_strategy = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="US_VANGUARD_VTI",
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 31),
        strategies=[sec_strategy, sponsor_strategy],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.confidence is None


def test_all_evidence_fields_present_and_valid(
    universe_registry: UniverseRegistry,
) -> None:
    """10. Verify all evidence fields are populated and adhere to schema."""
    synthetic_sec_json = {
        "filings": {
            "recent": {
                "form": ["497"],
                "filingDate": ["2026-03-10"],
                "accessionNumber": ["0001193125-26-000555"],
                "primaryDocument": ["doc497.htm"],
                "primaryDocDescription": ["Quarterly Dividend Declaration Notice"],
            }
        }
    }

    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://data.sec.gov/submissions/CIK0000036405.json",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=json.dumps(synthetic_sec_json),
        content_bytes=json.dumps(synthetic_sec_json).encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.15,
    )

    sec_strategy = SECEdgarSubmissionsStrategy(
        http_client=mock_client, universe=universe_registry
    )
    result = detect_distribution(
        fund_id="US_VANGUARD_VTI",
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 31),
        strategies=[sec_strategy],
    )

    assert len(result.evidence) > 0
    for ev in result.evidence:
        assert ev.source_id
        assert isinstance(ev.source_tier, SourceTier)
        assert ev.url.startswith("https://")
        assert isinstance(ev.retrieved_at, datetime)
        assert ev.snippet_or_locator


def test_sec_edgar_strategy_pdi_rule_19a1_positive(
    universe_registry: UniverseRegistry,
) -> None:
    """Regression test 1: PDI real Rule 19a-1 declaration logic yields DECLARED with Tier 1 confidence."""
    synthetic_sec_json = {
        "filings": {
            "recent": {
                "form": ["19A-1"],
                "filingDate": ["2026-02-02"],
                "accessionNumber": ["0001193125-26-012345"],
                "primaryDocument": ["notice19a.htm"],
                "primaryDocDescription": ["SECTION 19(A) NOTICE"],
            }
        }
    }
    synthetic_19a1_html = """
    <html>
      <body>
        <h1>NOTICE OF DISTRIBUTION UNDER SECTION 19(a)</h1>
        <p>PIMCO Active Bond ETF (BOND)</p>
        <p>The Fund has declared a monthly distribution of $0.2205 per share, payable on February 27, 2026 to shareholders of record on February 13, 2026. Ex-dividend date: February 12, 2026.</p>
        <p>Declared: 2026-02-02.</p>
        <p>Sources of distribution: Net Investment Income: $0.1500, Return of Capital: $0.0705.</p>
      </body>
    </html>
    """
    mock_client = MagicMock(spec=HTTPClient)

    def mock_get(url: str, **kwargs):
        if "submissions/CIK" in url:
            return HTTPResponseRecord(
                url=url,
                status_code=200,
                retrieved_at=datetime.now(timezone.utc),
                content_text=json.dumps(synthetic_sec_json),
                content_bytes=json.dumps(synthetic_sec_json).encode(),
                failure_reason=None,
                is_success=True,
                elapsed_seconds=0.1,
            )
        return HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=datetime.now(timezone.utc),
            content_text=synthetic_19a1_html,
            content_bytes=synthetic_19a1_html.encode(),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get
    sec_strategy = SECEdgarSubmissionsStrategy(
        http_client=mock_client, universe=universe_registry
    )
    obs = sec_strategy.inspect(
        fund_id="US_PIMCO_BOND",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
    )
    assert obs.has_declaration_in_window is True
    assert obs.suggested_route == ExtractionRoute.FILING
    assert len(obs.evidence) == 1
    assert obs.evidence[0].declaration_date_found == date(2026, 2, 2)

    result = detect_distribution(
        fund_id="US_PIMCO_BOND",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[sec_strategy],
    )
    assert result.status == DetectionStatus.DECLARED
    assert result.confidence == 1.0


def test_sponsor_web_strategy_zcn_press_release_positive(
    universe_registry: UniverseRegistry,
) -> None:
    """Regression test 2: ZCN real BMO distribution announcement yields DECLARED with Tier 2 confidence."""
    synthetic_press_release_html = """
    <html>
      <body>
        <h1>BMO Asset Management Announces Monthly Cash Distributions for BMO ETFs</h1>
        <p>TORONTO, February 18, 2026 - BMO Asset Management Inc. today announced the February 2026 cash distributions for BMO ETFs.</p>
        <p>Unitholders of record on February 26, 2026 will receive distributions payable on February 27, 2026. Ex-dividend date: February 25, 2026.</p>
        <p>Declared: 2026-02-18</p>
      </body>
    </html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://www.bmogam.com/ca-en/investors/investment-solutions/etfs/bmo-sptsx-capped-composite-index-etf-zcn/",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=synthetic_press_release_html,
        content_bytes=synthetic_press_release_html.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )

    sponsor_strategy = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )
    obs = sponsor_strategy.inspect(
        fund_id="CA_BMO_ZCN",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
    )
    assert obs.has_declaration_in_window is True
    assert len(obs.evidence) >= 1
    assert any(ev.declaration_date_found == date(2026, 2, 18) for ev in obs.evidence)

    result = detect_distribution(
        fund_id="CA_BMO_ZCN",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[sponsor_strategy],
    )
    assert result.status == DetectionStatus.DECLARED
    assert result.confidence == 0.90
