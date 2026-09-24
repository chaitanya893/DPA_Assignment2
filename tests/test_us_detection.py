"""Schedule-Aware US Distribution Detection deterministic test suite.

Tests all 10 required domain scenarios:
1. Quarterly + off-cadence + complete verified schedule -> NOT_DECLARED
2. Quarterly + off-cadence + incomplete schedule -> UNKNOWN
3. Quarterly + on-cadence + real declaration evidence -> DECLARED
4. On-cadence + WAF/source unavailable -> UNKNOWN
5. Monthly fund + real declaration -> DECLARED
6. Monthly fund + insufficient evidence -> UNKNOWN
7. Annual fund + off-month + complete verified schedule -> NOT_DECLARED
8. Tier-3-only evidence -> final declaration नाही (UNKNOWN)
9. Irrelevant SEC filing -> UNKNOWN
10. Conflicting positive/negative evidence -> UNKNOWN
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.detector import detect_distribution, synthesize_detection_result
from src.http_client import HTTPClient, HTTPResponseRecord
from src.models import (
    DetectionStatus,
    Evidence,
    ExtractionRoute,
    SourceTier,
    UnknownReason,
)
from src.strategies import (
    CalendarExpectationStrategy,
    OfficialSponsorWebStrategy,
    SECEdgarSubmissionsStrategy,
    SignalType,
    StrategyObservation,
    Tier3CorroborationStrategy,
)
from src.universe_loader import UniverseRegistry


@pytest.fixture
def universe_registry() -> UniverseRegistry:
    return UniverseRegistry.from_json()


# -----------------------------------------------------------------------------
# 1. Quarterly + off-cadence + complete verified schedule -> NOT_DECLARED
# -----------------------------------------------------------------------------
def test_quarterly_off_cadence_complete_schedule_yields_not_declared(
    universe_registry: UniverseRegistry,
) -> None:
    """VOO (Quarterly: Mar/Jun/Sep/Dec) in Feb (off-cadence) with complete verified schedule."""
    annual_schedule_html = """
    <html><body>
      <h1>Vanguard S&P 500 ETF (VOO)</h1>
      <h2>2026 Distribution Schedule</h2>
      <table>
        <tr><th>Quarter</th><th>Declaration Date</th><th>Ex-Dividend Date</th><th>Payable Date</th></tr>
        <tr><td>Q1</td><td>2026-03-20</td><td>2026-03-23</td><td>2026-03-27</td></tr>
        <tr><td>Q2</td><td>2026-06-19</td><td>2026-06-22</td><td>2026-06-26</td></tr>
        <tr><td>Q3</td><td>2026-09-18</td><td>2026-09-21</td><td>2026-09-25</td></tr>
        <tr><td>Q4</td><td>2026-12-18</td><td>2026-12-21</td><td>2026-12-28</td></tr>
      </table>
      <p>Note: No distribution declared or scheduled for February 2026 (off-cadence).</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)

    def mock_get(url: str, **kwargs):
        if "submissions/CIK" in url:
            return HTTPResponseRecord(
                url=url,
                status_code=200,
                retrieved_at=datetime.now(timezone.utc),
                content_text=json.dumps(
                    {"filings": {"recent": {"form": [], "filingDate": []}}}
                ),
                content_bytes=b"{}",
                failure_reason=None,
                is_success=True,
                elapsed_seconds=0.1,
            )
        return HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=datetime.now(timezone.utc),
            content_text=annual_schedule_html,
            content_bytes=annual_schedule_html.encode(),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get

    cal_strat = CalendarExpectationStrategy(universe=universe_registry)
    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="US_VANGUARD_VOO",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[cal_strat, sponsor_strat],
    )
    assert result.status == DetectionStatus.NOT_DECLARED
    assert result.confidence is not None and result.confidence >= 0.85


# -----------------------------------------------------------------------------
# 2. Quarterly + off-cadence + incomplete schedule -> UNKNOWN
# -----------------------------------------------------------------------------
def test_quarterly_off_cadence_incomplete_schedule_yields_unknown(
    universe_registry: UniverseRegistry,
) -> None:
    """VOO in Feb (off-cadence) where sponsor page is dynamic SPA without static schedule table."""
    dynamic_spa_html = """
    <html><body>
      <div id="root">Loading Vanguard interactive fund profile...</div>
      <script src="/app.js"></script>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)

    def mock_get(url: str, **kwargs):
        if "submissions/CIK" in url:
            return HTTPResponseRecord(
                url=url,
                status_code=200,
                retrieved_at=datetime.now(timezone.utc),
                content_text=json.dumps(
                    {"filings": {"recent": {"form": [], "filingDate": []}}}
                ),
                content_bytes=b"{}",
                failure_reason=None,
                is_success=True,
                elapsed_seconds=0.1,
            )
        return HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=datetime.now(timezone.utc),
            content_text=dynamic_spa_html,
            content_bytes=dynamic_spa_html.encode(),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get

    cal_strat = CalendarExpectationStrategy(universe=universe_registry)
    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="US_VANGUARD_VOO",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[cal_strat, sponsor_strat],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.confidence == 0.0
    assert any(
        ev.failure_reason == UnknownReason.INSUFFICIENT_EVIDENCE
        for ev in result.evidence
    )


# -----------------------------------------------------------------------------
# 3. Quarterly + on-cadence + real declaration evidence -> DECLARED
# -----------------------------------------------------------------------------
def test_quarterly_on_cadence_real_declaration_yields_declared(
    universe_registry: UniverseRegistry,
) -> None:
    """VOO in March (on-cadence) with real official declaration announcement."""
    official_pr_html = """
    <html><body>
      <h1>Vanguard S&P 500 ETF (VOO)</h1>
      <p>MALVERN, Pa. - Vanguard has announced quarterly distribution of $1.7850 per share payable March 27, 2026.</p>
      <p>Declaration Date: March 20, 2026. Record Date: March 23, 2026.</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://pressroom.vanguard.com/distributions",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=official_pr_html,
        content_bytes=official_pr_html.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )

    cal_strat = CalendarExpectationStrategy(universe=universe_registry)
    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="US_VANGUARD_VOO",
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 31),
        strategies=[cal_strat, sponsor_strat],
    )
    assert result.status == DetectionStatus.DECLARED
    assert result.confidence is not None and result.confidence >= 0.85
    # Announcement text (no distribution table) -> notice/filing-style extraction route.
    assert result.suggested_extraction_route == ExtractionRoute.FILING


# -----------------------------------------------------------------------------
# 4. On-cadence + WAF/source unavailable -> UNKNOWN
# -----------------------------------------------------------------------------
def test_on_cadence_waf_blocked_yields_unknown(
    universe_registry: UniverseRegistry,
) -> None:
    """VOO in March (on-cadence) when sources return WAF HTTP 403 Forbidden."""
    mock_client = MagicMock(spec=HTTPClient)

    def mock_get(url: str, **kwargs):
        if "submissions/CIK" in url:
            return HTTPResponseRecord(
                url=url,
                status_code=429,
                retrieved_at=datetime.now(timezone.utc),
                content_text=None,
                content_bytes=None,
                failure_reason=UnknownReason.RETRIEVAL_FAILED,
                error_message="HTTP 429 Too Many Requests",
                is_success=False,
                elapsed_seconds=0.1,
            )
        return HTTPResponseRecord(
            url=url,
            status_code=403,
            retrieved_at=datetime.now(timezone.utc),
            content_text="403 Forbidden - Cloudflare Security Challenge",
            content_bytes=b"403 Forbidden",
            failure_reason=UnknownReason.SOURCE_UNAVAILABLE,
            error_message="HTTP 403 Forbidden",
            is_success=False,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get

    cal_strat = CalendarExpectationStrategy(universe=universe_registry)
    sec_strat = SECEdgarSubmissionsStrategy(
        http_client=mock_client, universe=universe_registry
    )
    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="US_VANGUARD_VOO",
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 31),
        strategies=[cal_strat, sec_strat, sponsor_strat],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.confidence == 0.0


# -----------------------------------------------------------------------------
# 5. Monthly fund + real declaration -> DECLARED
# -----------------------------------------------------------------------------
def test_monthly_fund_real_declaration_yields_declared(
    universe_registry: UniverseRegistry,
) -> None:
    """Monthly fund (PIMCO BOND) with genuine Rule 19a-1 notice."""
    sec_19a1_json = {
        "filings": {
            "recent": {
                "form": ["497"],
                "filingDate": ["2026-02-18"],
                "accessionNumber": ["0001193125-26-000001"],
                "primaryDocument": ["d19a1.htm"],
                "primaryDocDescription": ["497"],
            }
        }
    }
    sec_19a1_html = """
    <html><body>
      <h1>PIMCO Active Bond Exchange-Traded Fund</h1>
      <p>Ticker: BOND CIK: 0001450011</p>
      <h2>Notice Pursuant to Rule 19a-1 under the Investment Company Act of 1940</h2>
      <p>On February 18, 2026, the Fund declared a monthly cash distribution of $0.3550 per share payable March 2, 2026.</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)

    def mock_get(url: str, **kwargs):
        if "submissions/CIK" in url:
            return HTTPResponseRecord(
                url=url,
                status_code=200,
                retrieved_at=datetime.now(timezone.utc),
                content_text=json.dumps(sec_19a1_json),
                content_bytes=json.dumps(sec_19a1_json).encode(),
                failure_reason=None,
                is_success=True,
                elapsed_seconds=0.1,
            )
        return HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=datetime.now(timezone.utc),
            content_text=sec_19a1_html,
            content_bytes=sec_19a1_html.encode(),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get

    cal_strat = CalendarExpectationStrategy(universe=universe_registry)
    sec_strat = SECEdgarSubmissionsStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="US_PIMCO_BOND",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[cal_strat, sec_strat],
    )
    assert result.status == DetectionStatus.DECLARED
    assert result.confidence is not None and result.confidence >= 0.90


# -----------------------------------------------------------------------------
# 6. Monthly fund + insufficient evidence -> UNKNOWN
# -----------------------------------------------------------------------------
def test_monthly_fund_insufficient_evidence_yields_unknown(
    universe_registry: UniverseRegistry,
) -> None:
    """Monthly fund (BND) where sources return HTTP 200 without positive filings or verified table."""
    mock_client = MagicMock(spec=HTTPClient)

    def mock_get(url: str, **kwargs):
        if "submissions/CIK" in url:
            return HTTPResponseRecord(
                url=url,
                status_code=200,
                retrieved_at=datetime.now(timezone.utc),
                content_text=json.dumps(
                    {"filings": {"recent": {"form": [], "filingDate": []}}}
                ),
                content_bytes=b"{}",
                failure_reason=None,
                is_success=True,
                elapsed_seconds=0.1,
            )
        return HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=datetime.now(timezone.utc),
            content_text="<html><body><h1>Vanguard Total Bond Market ETF</h1><p>General description.</p></body></html>",
            content_bytes=b"<html>...</html>",
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get

    cal_strat = CalendarExpectationStrategy(universe=universe_registry)
    sec_strat = SECEdgarSubmissionsStrategy(
        http_client=mock_client, universe=universe_registry
    )
    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="US_VANGUARD_BND",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[cal_strat, sec_strat, sponsor_strat],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.confidence == 0.0


# -----------------------------------------------------------------------------
# 7. Annual fund + off-month + complete verified schedule -> NOT_DECLARED
# -----------------------------------------------------------------------------
def test_annual_fund_off_month_complete_schedule_yields_not_declared(
    universe_registry: UniverseRegistry,
) -> None:
    """Annual fund (FZROX - Annual in Dec) inspected in June with complete verified schedule."""
    annual_schedule_html = """
    <html><body>
      <h1>Fidelity ZERO Total Market Index Fund (FZROX)</h1>
      <h2>2026 Distribution Schedule</h2>
      <table>
        <tr><th>Distribution Frequency</th><th>Scheduled Month</th><th>Declaration Date</th></tr>
        <tr><td>Annual</td><td>December</td><td>2026-12-18</td></tr>
      </table>
      <p>Note: No distribution declared or scheduled for the month of June 2026.</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)

    def mock_get(url: str, **kwargs):
        if "submissions/CIK" in url:
            return HTTPResponseRecord(
                url=url,
                status_code=200,
                retrieved_at=datetime.now(timezone.utc),
                content_text=json.dumps(
                    {"filings": {"recent": {"form": [], "filingDate": []}}}
                ),
                content_bytes=b"{}",
                failure_reason=None,
                is_success=True,
                elapsed_seconds=0.1,
            )
        return HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=datetime.now(timezone.utc),
            content_text=annual_schedule_html,
            content_bytes=annual_schedule_html.encode(),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get

    cal_strat = CalendarExpectationStrategy(universe=universe_registry)
    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="US_FIDELITY_FZROX",
        window_start=date(2026, 6, 1),
        window_end=date(2026, 6, 30),
        strategies=[cal_strat, sponsor_strat],
    )
    assert result.status == DetectionStatus.NOT_DECLARED
    assert result.confidence is not None and result.confidence >= 0.85


# -----------------------------------------------------------------------------
# 8. Tier-3-only evidence -> final declaration नाही (UNKNOWN)
# -----------------------------------------------------------------------------
def test_tier3_only_evidence_never_yields_declared_or_not_declared() -> None:
    """Tier 3 observation alone can NEVER produce DECLARED or NOT_DECLARED."""
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://finance.yahoo.com/quote/VOO",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text="<html><body><h1>VOO</h1><p>Yield: 1.45% Dividend Rate: 1.78</p></body></html>",
        content_bytes=b"<html>...</html>",
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )

    tier3_strat = Tier3CorroborationStrategy(http_client=mock_client)
    result = detect_distribution(
        fund_id="US_VANGUARD_VOO",
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 31),
        strategies=[tier3_strat],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.confidence == 0.0


# -----------------------------------------------------------------------------
# 9. Irrelevant SEC filing -> UNKNOWN
# -----------------------------------------------------------------------------
def test_irrelevant_sec_filing_yields_unknown(
    universe_registry: UniverseRegistry,
) -> None:
    """SEC filing with 12b-1 fees or wrong fund title is rejected -> UNKNOWN."""
    fee_sec_json = {
        "filings": {
            "recent": {
                "form": ["497"],
                "filingDate": ["2026-02-10"],
                "accessionNumber": ["0001-26-77"],
                "primaryDocument": ["spy_fee.htm"],
                "primaryDocDescription": ["497"],
            }
        }
    }
    fee_html = """
    <html><body>
      <h1>SPDR S&P 500 ETF Trust</h1>
      <p>Ticker: SPY CIK: 0000884394</p>
      <h2>Distribution and Service (12b-1) Fees</h2>
      <p>The Trust has adopted a distribution plan under Rule 12b-1 allowing payment of fees.</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)

    def mock_get(url: str, **kwargs):
        if "submissions/CIK" in url:
            return HTTPResponseRecord(
                url=url,
                status_code=200,
                retrieved_at=datetime.now(timezone.utc),
                content_text=json.dumps(fee_sec_json),
                content_bytes=json.dumps(fee_sec_json).encode(),
                failure_reason=None,
                is_success=True,
                elapsed_seconds=0.1,
            )
        return HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=datetime.now(timezone.utc),
            content_text=fee_html,
            content_bytes=fee_html.encode(),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get

    cal_strat = CalendarExpectationStrategy(universe=universe_registry)
    sec_strat = SECEdgarSubmissionsStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="US_SPDR_SPY",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[cal_strat, sec_strat],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.confidence == 0.0


# -----------------------------------------------------------------------------
# 10. Conflicting positive/negative evidence -> UNKNOWN
# -----------------------------------------------------------------------------
def test_conflicting_positive_negative_evidence_yields_unknown() -> None:
    """Conflicting observations: positive declaration vs verified full-window negative -> UNKNOWN."""
    pos_ev = Evidence(
        source_id="official_pr",
        source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
        url="https://sponsor.com/pr",
        retrieved_at=datetime.now(timezone.utc),
        snippet_or_locator="Declared distribution $0.50",
        declaration_date_found=date(2026, 2, 15),
    )
    neg_ev = Evidence(
        source_id="official_annual_schedule",
        source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
        url="https://sponsor.com/schedule",
        retrieved_at=datetime.now(timezone.utc),
        snippet_or_locator="Verified complete negative schedule for Feb 2026",
    )

    obs_pos = StrategyObservation(
        strategy_name="OfficialPRStrategy",
        signal_type=SignalType.EVIDENCE_OBSERVATION,
        evidence=[pos_ev],
        has_declaration_in_window=True,
        window_fully_covered=True,
    )
    obs_neg = StrategyObservation(
        strategy_name="AnnualScheduleStrategy",
        signal_type=SignalType.NO_DATA_OBSERVATION,
        evidence=[neg_ev],
        has_declaration_in_window=False,
        window_fully_covered=True,
    )

    result = synthesize_detection_result(
        fund_id="US_VANGUARD_VOO",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        observations=[obs_pos, obs_neg],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.confidence == 0.0
    assert any(
        ev.failure_reason == UnknownReason.CONFLICTING_EVIDENCE
        for ev in result.evidence
    )


# -----------------------------------------------------------------------------
# 11. Vanguard VOO Official Distribution Table Parsing (e.g. March 2024)
# -----------------------------------------------------------------------------
def test_vanguard_voo_march_2024_distribution_table_parsing(
    universe_registry: UniverseRegistry,
) -> None:
    """Vanguard VOO Distribution History Table Parsing yields DECLARED with structured date fields."""
    vanguard_html = """
    <html><body>
      <h1>Vanguard 500 Index Fund (VOO)</h1>
      <h2>Distribution History</h2>
      <table>
        <thead>
          <tr>
            <th>Type</th>
            <th>$/Share</th>
            <th>Payable Date</th>
            <th>Record Date</th>
            <th>Ex-Dividend Date</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Income</td>
            <td>$1.7385</td>
            <td>12/26/2024</td>
            <td>12/23/2024</td>
            <td>12/23/2024</td>
          </tr>
          <tr>
            <td>Income</td>
            <td>$1.6386</td>
            <td>10/01/2024</td>
            <td>09/27/2024</td>
            <td>09/27/2024</td>
          </tr>
          <tr>
            <td>Income</td>
            <td>$1.7835</td>
            <td>07/02/2024</td>
            <td>06/28/2024</td>
            <td>06/28/2024</td>
          </tr>
          <tr>
            <td>Income</td>
            <td>$1.5429</td>
            <td>03/27/2024</td>
            <td>03/25/2024</td>
            <td>03/22/2024</td>
          </tr>
        </tbody>
      </table>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://advisors.vanguard.com/investments/products/voo/vanguard-sp-500-etf",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=vanguard_html,
        content_bytes=vanguard_html.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )

    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="US_VANGUARD_VOO",
        window_start=date(2024, 3, 15),
        window_end=date(2024, 3, 25),
        strategies=[sponsor_strat],
    )

    assert result.status == DetectionStatus.DECLARED
    assert result.confidence is not None and result.confidence >= 0.85
    assert result.suggested_extraction_route == ExtractionRoute.HTML_TABLE
    assert len(result.evidence) >= 1

    ev = result.evidence[0]
    assert ev.ex_date_found == date(2024, 3, 22)
    assert ev.record_date_found == date(2024, 3, 25)
    assert ev.payable_date_found == date(2024, 3, 27)
    assert "1.5429" in ev.snippet_or_locator
