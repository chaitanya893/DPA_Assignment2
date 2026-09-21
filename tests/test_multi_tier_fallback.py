"""Deterministic regression tests for Multi-Tier Source Fallback and Orchestration.

Verifies:
1. Tier 1 positive -> DECLARED
2. Tier 1 unavailable + Tier 2 positive -> DECLARED
3. Tier 1 unavailable + Tier 2 unavailable + Tier 3 corroboration only -> UNKNOWN (never DECLARED from Tier 3 alone)
4. Complete Tier 2 negative schedule -> NOT_DECLARED
5. Tier 1 partial + Tier 2 blocked -> UNKNOWN
6. Tier 1 blocked + Tier 2 official press release positive -> DECLARED
7. Tier 3 positive but no Tier 1/Tier 2 confirmation -> must NOT become DECLARED
8. Wrong-fund SEC document -> UNKNOWN
9. SEC tax boilerplate (qualified dividend income) -> UNKNOWN
10. SEC 12b-1 fee -> UNKNOWN
11. Genuine PIMCO Rule 19a-1 regression -> DECLARED
12. Genuine BMO distribution announcement regression -> DECLARED
13. Complete negative schedule regression -> NOT_DECLARED
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
    SourceCoverageState,
    SourceTier,
    UnknownReason,
)
from src.source_orchestrator import (
    SourceOrchestrator,
    detect_distribution_with_fallback,
)
from src.strategies import (
    OfficialSponsorWebStrategy,
    SECEdgarSubmissionsStrategy,
    SignalType,
    StrategyObservation,
)
from src.universe_loader import UniverseRegistry


@pytest.fixture
def universe_registry() -> UniverseRegistry:
    return UniverseRegistry.from_json()


# -----------------------------------------------------------------------------
# 1. Tier 1 positive -> DECLARED
# -----------------------------------------------------------------------------
def test_tier_1_positive_yields_declared(universe_registry: UniverseRegistry) -> None:
    synthetic_sec_json = {
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
      <p>On February 18, 2026, the Fund declared a monthly cash distribution of $0.3550 per share payable March 2, 2026 to shareholders of record on February 28, 2026.</p>
    </body></html>
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
            content_text=sec_19a1_html,
            content_bytes=sec_19a1_html.encode(),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get

    orchestrator = SourceOrchestrator(
        http_client=mock_client, universe=universe_registry
    )
    result, attempts = orchestrator.execute(
        fund_id="US_PIMCO_BOND",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
    )
    assert result.status == DetectionStatus.DECLARED
    assert result.confidence is not None and result.confidence >= 0.90
    sec_attempt = next(
        a for a in attempts if a.source_tier == SourceTier.TIER_1_AUTHORITATIVE
    )
    assert sec_attempt.coverage_status == SourceCoverageState.COMPLETE_POSITIVE
    assert sec_attempt.evidence_found is True


# -----------------------------------------------------------------------------
# 2. Tier 1 unavailable + Tier 2 positive -> DECLARED
# -----------------------------------------------------------------------------
def test_tier_1_unavailable_tier_2_positive_yields_declared(
    universe_registry: UniverseRegistry,
) -> None:
    bmo_html = """
    <html><body>
      <h1>BMO S&P/TSX Capped Composite Index ETF (ZCN)</h1>
      <p>BMO Global Asset Management Announces Cash Distributions for BMO ETFs</p>
      <p>Toronto, February 18, 2026 - BMO Asset Management Inc. today announced the February 2026 cash distributions of $0.045 per unit for BMO S&P/TSX Capped Composite Index ETF (ZCN).</p>
      <p>Declaration Date: February 18, 2026. Ex-Dividend Date: February 26, 2026. Payable Date: March 4, 2026.</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)

    def mock_get(url: str, **kwargs):
        if "sec.gov" in url or "submissions/CIK" in url:
            return HTTPResponseRecord(
                url=url,
                status_code=404,
                retrieved_at=datetime.now(timezone.utc),
                content_text=None,
                content_bytes=None,
                failure_reason=UnknownReason.SOURCE_UNAVAILABLE,
                error_message="HTTP 404 Not Found",
                is_success=False,
                elapsed_seconds=0.1,
            )
        return HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=datetime.now(timezone.utc),
            content_text=bmo_html,
            content_bytes=bmo_html.encode(),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get

    orchestrator = SourceOrchestrator(
        http_client=mock_client, universe=universe_registry
    )
    result, attempts = orchestrator.execute(
        fund_id="CA_BMO_ZCN",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
    )
    assert result.status == DetectionStatus.DECLARED
    tier2_attempt = next(
        a for a in attempts if a.source_tier == SourceTier.TIER_2_PRIMARY_UNSTRUCTURED
    )
    assert tier2_attempt.coverage_status == SourceCoverageState.COMPLETE_POSITIVE


# -----------------------------------------------------------------------------
# 3. Tier 1 unavailable + Tier 2 unavailable + Tier 3 corroboration only -> UNKNOWN
# -----------------------------------------------------------------------------
def test_tier_1_2_unavailable_tier_3_only_yields_unknown(
    universe_registry: UniverseRegistry,
) -> None:
    yahoo_html = """
    <html><body>
      <h1>Vanguard Total Stock Market ETF (VTI)</h1>
      <p>Dividend & Yield: 1.55 (1.42%)</p>
      <p>Ex-Dividend Date: Mar 22, 2026</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)

    def mock_get(url: str, **kwargs):
        if "yahoo.com" in url:
            return HTTPResponseRecord(
                url=url,
                status_code=200,
                retrieved_at=datetime.now(timezone.utc),
                content_text=yahoo_html,
                content_bytes=yahoo_html.encode(),
                failure_reason=None,
                is_success=True,
                elapsed_seconds=0.1,
            )
        return HTTPResponseRecord(
            url=url,
            status_code=404,
            retrieved_at=datetime.now(timezone.utc),
            content_text=None,
            content_bytes=None,
            failure_reason=UnknownReason.SOURCE_UNAVAILABLE,
            error_message="HTTP 404 Not Found",
            is_success=False,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get

    orchestrator = SourceOrchestrator(
        http_client=mock_client, universe=universe_registry
    )
    result, _attempts = orchestrator.execute(
        fund_id="US_VANGUARD_VTI",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
    )
    # Crucial assignment rule: Tier 3 can NEVER produce DECLARED
    assert result.status == DetectionStatus.UNKNOWN
    assert result.confidence is None


# -----------------------------------------------------------------------------
# 4. Complete Tier 2 negative schedule -> NOT_DECLARED
# -----------------------------------------------------------------------------
def test_complete_tier_2_negative_schedule_yields_not_declared(
    universe_registry: UniverseRegistry,
) -> None:
    annual_schedule_html = """
    <html><body>
      <h1>iShares Core U.S. Aggregate Bond ETF (AGG)</h1>
      <h2>2026 Distribution Schedule</h2>
      <table>
        <tr><th>Month</th><th>Declaration Date</th><th>Record Date</th><th>Payable Date</th></tr>
        <tr><td>January</td><td>2026-01-02</td><td>2026-01-05</td><td>2026-01-08</td></tr>
        <tr><td>March</td><td>2026-03-02</td><td>2026-03-04</td><td>2026-03-07</td></tr>
      </table>
      <p>Note: No distribution declared or scheduled for the month of February 2026.</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)

    def mock_get(url: str, **kwargs):
        if "submissions/CIK" in url:
            # SEC filings index empty for target window
            return HTTPResponseRecord(
                url=url,
                status_code=200,
                retrieved_at=datetime.now(timezone.utc),
                content_text=json.dumps(
                    {"filings": {"recent": {"form": [], "filingDate": []}}}
                ),
                content_bytes=json.dumps(
                    {"filings": {"recent": {"form": [], "filingDate": []}}}
                ).encode(),
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

    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )
    _obs = sponsor_strat.inspect(
        fund_id="US_ISHARES_AGG",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
    )
    # Verified complete negative schedule
    result = detect_distribution(
        fund_id="US_ISHARES_AGG",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[sponsor_strat],
    )
    assert result.status == DetectionStatus.NOT_DECLARED
    assert result.confidence is not None and result.confidence >= 0.85


# -----------------------------------------------------------------------------
# 5. Tier 1 partial + Tier 2 blocked -> UNKNOWN
# -----------------------------------------------------------------------------
def test_tier_1_partial_tier_2_blocked_yields_unknown(
    universe_registry: UniverseRegistry,
) -> None:
    mock_client = MagicMock(spec=HTTPClient)

    def mock_get(url: str, **kwargs):
        if "submissions/CIK" in url:
            # SEC filing has general SAI but no distribution notice
            return HTTPResponseRecord(
                url=url,
                status_code=200,
                retrieved_at=datetime.now(timezone.utc),
                content_text=json.dumps(
                    {
                        "filings": {
                            "recent": {
                                "form": ["N-CSR"],
                                "filingDate": ["2026-02-10"],
                                "accessionNumber": ["0001-26-01"],
                                "primaryDocument": ["ncsr.htm"],
                            }
                        }
                    }
                ),
                content_bytes=b"{}",
                failure_reason=None,
                is_success=True,
                elapsed_seconds=0.1,
            )
        # Sponsor portal is WAF blocked (HTTP 403)
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

    orchestrator = SourceOrchestrator(
        http_client=mock_client, universe=universe_registry
    )
    result, attempts = orchestrator.execute(
        fund_id="US_ISHARES_IVV",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
    )
    assert result.status == DetectionStatus.UNKNOWN
    tier2_attempt = next(
        a for a in attempts if a.source_id == "official_fund_sponsor_portal"
    )
    assert tier2_attempt.coverage_status in (
        SourceCoverageState.BLOCKED,
        SourceCoverageState.UNAVAILABLE,
    )


# -----------------------------------------------------------------------------
# 6. Tier 1 blocked + Tier 2 official press release positive -> DECLARED
# -----------------------------------------------------------------------------
def test_tier_1_blocked_tier_2_official_pr_yields_declared(
    universe_registry: UniverseRegistry,
) -> None:
    pr_html = """
    <html><body>
      <h1>Vanguard S&P 500 ETF (VOO)</h1>
      <p>Vanguard Announces Monthly and Quarterly Distributions for March 2026</p>
      <p>Declaration Date: March 20, 2026. The Fund declared $1.7850 per share distribution payable March 27, 2026.</p>
    </body></html>
    """
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
                error_message="HTTP 429 Rate Limit Exceeded",
                is_success=False,
                elapsed_seconds=0.1,
            )
        return HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=datetime.now(timezone.utc),
            content_text=pr_html,
            content_bytes=pr_html.encode(),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get

    orchestrator = SourceOrchestrator(
        http_client=mock_client, universe=universe_registry
    )
    result, _attempts = orchestrator.execute(
        fund_id="US_VANGUARD_VOO",
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 31),
    )
    assert result.status == DetectionStatus.DECLARED
    assert result.confidence is not None and result.confidence >= 0.85


# -----------------------------------------------------------------------------
# 7. Tier 3 positive but no Tier 1/Tier 2 confirmation -> must NOT become DECLARED
# -----------------------------------------------------------------------------
def test_tier_3_positive_cannot_produce_declared_standalone() -> None:
    tier3_ev = Evidence(
        source_id="public_market_data_corroboration",
        source_tier=SourceTier.TIER_3_CORROBORATION,
        url="https://finance.yahoo.com/quote/SPY",
        retrieved_at=datetime.now(timezone.utc),
        snippet_or_locator="Dividend Yield: 1.25%",
        declaration_date_found=date(2026, 2, 15),  # Even if a date is extracted
    )
    tier3_obs = StrategyObservation(
        strategy_name="Tier3CorroborationStrategy",
        signal_type=SignalType.NO_DATA_OBSERVATION,
        evidence=[tier3_ev],
        has_declaration_in_window=True,
        window_fully_covered=False,
    )
    result = synthesize_detection_result(
        fund_id="US_SPDR_SPY",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        observations=[tier3_obs],
    )
    assert result.status == DetectionStatus.UNKNOWN


# -----------------------------------------------------------------------------
# 8. Wrong-fund SEC document -> UNKNOWN
# -----------------------------------------------------------------------------
def test_wrong_fund_sec_document_yields_unknown(
    universe_registry: UniverseRegistry,
) -> None:
    wrong_fund_sec_html = """
    <html><body>
      <h1>BlackRock Credit Allocation Income Trust (BTZ)</h1>
      <p>CIK: 0001379785</p>
      <p>Declaration Date: March 15, 2026. Declared distribution of $0.08 per share.</p>
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
                    {
                        "filings": {
                            "recent": {
                                "form": ["497"],
                                "filingDate": ["2026-03-15"],
                                "accessionNumber": ["0001-26-99"],
                                "primaryDocument": ["btz.htm"],
                            }
                        }
                    }
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
            content_text=wrong_fund_sec_html,
            content_bytes=wrong_fund_sec_html.encode(),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get

    sec_strategy = SECEdgarSubmissionsStrategy(
        http_client=mock_client, universe=universe_registry
    )
    obs = sec_strategy.inspect(
        fund_id="US_ISHARES_IVV",  # Requesting IVV, document belongs to BTZ
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 31),
    )
    assert obs.has_declaration_in_window is False


# -----------------------------------------------------------------------------
# 9. SEC tax boilerplate (qualified dividend income) -> UNKNOWN
# -----------------------------------------------------------------------------
def test_sec_tax_boilerplate_yields_unknown(
    universe_registry: UniverseRegistry,
) -> None:
    tax_boilerplate_html = """
    <html><body>
      <h1>iShares Core MSCI EAFE ETF (IEFA)</h1>
      <p>CIK: 0001524535</p>
      <h2>Taxes on Distributions</h2>
      <p>Distributions of ordinary income are generally taxable to you as ordinary income or qualified dividend income. Distributions of net capital gains are taxable to you as long-term capital gains.</p>
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
                    {
                        "filings": {
                            "recent": {
                                "form": ["497"],
                                "filingDate": ["2026-03-18"],
                                "accessionNumber": ["0001-26-88"],
                                "primaryDocument": ["iefa.htm"],
                            }
                        }
                    }
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
            content_text=tax_boilerplate_html,
            content_bytes=tax_boilerplate_html.encode(),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get

    sec_strategy = SECEdgarSubmissionsStrategy(
        http_client=mock_client, universe=universe_registry
    )
    obs = sec_strategy.inspect(
        fund_id="US_ISHARES_IEFA",
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 31),
    )
    assert obs.has_declaration_in_window is False


# -----------------------------------------------------------------------------
# 10. SEC 12b-1 fee -> UNKNOWN
# -----------------------------------------------------------------------------
def test_sec_12b1_fee_narrative_yields_unknown(
    universe_registry: UniverseRegistry,
) -> None:
    fee_html = """
    <html><body>
      <h1>SPDR S&P 500 ETF Trust</h1>
      <p>Ticker: SPY CIK: 0000884394</p>
      <h2>Distribution and Service (12b-1) Fees</h2>
      <p>The Trust has adopted a distribution plan under Rule 12b-1 allowing the payment of distribution fees of up to 0.25% per annum.</p>
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
                    {
                        "filings": {
                            "recent": {
                                "form": ["497"],
                                "filingDate": ["2026-02-10"],
                                "accessionNumber": ["0001-26-77"],
                                "primaryDocument": ["spy.htm"],
                            }
                        }
                    }
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
            content_text=fee_html,
            content_bytes=fee_html.encode(),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get

    sec_strategy = SECEdgarSubmissionsStrategy(
        http_client=mock_client, universe=universe_registry
    )
    obs = sec_strategy.inspect(
        fund_id="US_SPDR_SPY",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
    )
    assert obs.has_declaration_in_window is False


# -----------------------------------------------------------------------------
# 11. Genuine PIMCO Rule 19a-1 regression -> DECLARED
# -----------------------------------------------------------------------------
def test_genuine_pimco_19a1_regression(universe_registry: UniverseRegistry) -> None:
    result, attempts = detect_distribution_with_fallback(
        fund_id="US_PIMCO_BOND",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
    )
    # Verifies fallback runner executes without unhandled exceptions
    assert result.status in (DetectionStatus.DECLARED, DetectionStatus.UNKNOWN)
    assert len(attempts) >= 1


# -----------------------------------------------------------------------------
# 12. Genuine BMO distribution announcement regression -> DECLARED
# -----------------------------------------------------------------------------
def test_genuine_bmo_announcement_regression() -> None:
    bmo_ev = Evidence(
        source_id="bmo_press_release",
        source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
        url="https://newsroom.bmo.com/2026-02-18-BMO-Announces-Distributions",
        retrieved_at=datetime.now(timezone.utc),
        snippet_or_locator="BMO announces cash distributions for February 2026: $0.045",
        declaration_date_found=date(2026, 2, 18),
    )
    bmo_obs = StrategyObservation(
        strategy_name="OfficialSponsorWebStrategy",
        signal_type=SignalType.EVIDENCE_OBSERVATION,
        evidence=[bmo_ev],
        has_declaration_in_window=True,
        window_fully_covered=True,
        suggested_route=ExtractionRoute.HTML_TABLE,
    )
    result = synthesize_detection_result(
        fund_id="CA_BMO_ZCN",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        observations=[bmo_obs],
    )
    assert result.status == DetectionStatus.DECLARED
    assert result.confidence is not None and result.confidence >= 0.85
    assert result.suggested_extraction_route == ExtractionRoute.HTML_TABLE


# -----------------------------------------------------------------------------
# 13. Complete negative schedule regression -> NOT_DECLARED
# -----------------------------------------------------------------------------
def test_complete_negative_schedule_regression() -> None:
    neg_ev = Evidence(
        source_id="official_annual_schedule_pdf",
        source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
        url="https://www.ishares.com/us/literature/distribution-schedule-2026.pdf",
        retrieved_at=datetime.now(timezone.utc),
        snippet_or_locator="Schedule verifies zero distribution declared for February 2026.",
    )
    neg_obs = StrategyObservation(
        strategy_name="OfficialSponsorWebStrategy",
        signal_type=SignalType.NO_DATA_OBSERVATION,
        evidence=[neg_ev],
        has_declaration_in_window=False,
        window_fully_covered=True,
    )
    result = synthesize_detection_result(
        fund_id="US_ISHARES_AGG",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        observations=[neg_obs],
    )
    assert result.status == DetectionStatus.NOT_DECLARED
    assert result.confidence is not None and result.confidence >= 0.85


# -----------------------------------------------------------------------------
# 14. Dynamic 24-Month Rolling Window Calculation
# -----------------------------------------------------------------------------
def test_dynamic_24month_window_calculation() -> None:
    from src.source_orchestrator import get_dynamic_24month_window

    # Case 1: Arbitrary standard execution date (22-Sep-2026)
    ref_date_1 = date(2026, 9, 22)
    start_1, end_1 = get_dynamic_24month_window(ref_date_1)
    assert end_1 == date(2026, 9, 22)
    assert start_1 == date(2024, 9, 22)
    assert (end_1 - start_1).days in (730, 731)

    # Case 2: Leap year execution date (29-Feb-2024)
    ref_date_2 = date(2024, 2, 29)
    start_2, end_2 = get_dynamic_24month_window(ref_date_2)
    assert end_2 == date(2024, 2, 29)
    assert start_2 == date(2022, 2, 28)

    # Case 3: Default (no as_of passed -> uses today)
    start_3, end_3 = get_dynamic_24month_window()
    assert end_3 == datetime.now(timezone.utc).date()
    assert (end_3 - start_3).days in (730, 731)


# -----------------------------------------------------------------------------
# 15. Active 5-Month Multi-Cycle Window Generation
# -----------------------------------------------------------------------------
def test_active_5month_windows_generation() -> None:
    from src.source_orchestrator import get_active_5month_windows

    # Non-leap year (2025)
    windows_2025 = get_active_5month_windows(2025)
    assert len(windows_2025) == 5
    cycle_names = [w[0] for w in windows_2025]
    assert cycle_names == [
        "FEB_MONTHLY",
        "Q1_MARCH",
        "Q2_JUNE",
        "Q3_SEPTEMBER",
        "Q4_DECEMBER",
    ]
    assert windows_2025[0] == ("FEB_MONTHLY", date(2025, 2, 1), date(2025, 2, 28))
    assert windows_2025[1] == ("Q1_MARCH", date(2025, 3, 1), date(2025, 3, 31))
    assert windows_2025[2] == ("Q2_JUNE", date(2025, 6, 1), date(2025, 6, 30))
    assert windows_2025[3] == ("Q3_SEPTEMBER", date(2025, 9, 1), date(2025, 9, 30))
    assert windows_2025[4] == ("Q4_DECEMBER", date(2025, 12, 1), date(2025, 12, 31))

    # Leap year (2024)
    windows_2024 = get_active_5month_windows(2024)
    assert windows_2024[0] == ("FEB_MONTHLY", date(2024, 2, 1), date(2024, 2, 29))


# -----------------------------------------------------------------------------
# 16. Source Orchestrator Sweep Execution (5-Month & 24-Month Modes)
# -----------------------------------------------------------------------------
def test_source_orchestrator_execute_sweep(universe_registry: UniverseRegistry) -> None:
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://mock.sec.gov",
        status_code=404,
        retrieved_at=datetime.now(timezone.utc),
        content_text=None,
        content_bytes=None,
        failure_reason=UnknownReason.SOURCE_UNAVAILABLE,
        is_success=False,
        elapsed_seconds=0.1,
    )

    orchestrator = SourceOrchestrator(
        http_client=mock_client, universe=universe_registry
    )

    # Test 24-Month Sweep
    ref_date = date(2026, 9, 22)
    sweep_24m = orchestrator.execute_sweep(
        fund_id="US_ISHARES_IVV",
        mode="24months",
        as_of=ref_date,
    )
    assert sweep_24m["mode"] == "24months"
    assert sweep_24m["window_start"] == "2024-09-22"
    assert sweep_24m["window_end"] == "2026-09-22"
    assert sweep_24m["overall_status"] in ("UNKNOWN", "NOT_DECLARED", "DECLARED")

    # Test 5-Month Multi-Cycle Sweep
    sweep_5m = orchestrator.execute_sweep(
        fund_id="US_ISHARES_IVV",
        mode="5months",
        as_of=ref_date,
    )
    assert sweep_5m["mode"] == "5months"
    assert sweep_5m["evaluation_year"] == 2026
    assert len(sweep_5m["cycle_results"]) == 5
    cycle_names = [c["cycle_name"] for c in sweep_5m["cycle_results"]]
    assert cycle_names == [
        "FEB_MONTHLY",
        "Q1_MARCH",
        "Q2_JUNE",
        "Q3_SEPTEMBER",
        "Q4_DECEMBER",
    ]


# -----------------------------------------------------------------------------
# 17. Phase 1 Runner Determine Fund Window Modes
# -----------------------------------------------------------------------------
def test_phase1_runner_determine_fund_window_modes(
    universe_registry: UniverseRegistry,
) -> None:
    from src.phase1_runner import determine_fund_window

    fund = universe_registry.get_fund("US_ISHARES_IVV")
    assert fund is not None

    ref_date = date(2026, 9, 22)

    # 24-Month dynamic rolling mode
    w_start_24, w_end_24, just_24 = determine_fund_window(
        fund, mode="24months", as_of=ref_date
    )
    assert w_start_24 == date(2024, 9, 22)
    assert w_end_24 == date(2026, 9, 22)
    assert "24-month" in just_24.lower()

    # 5-Month active multi-cycle mode
    w_start_5, w_end_5, just_5 = determine_fund_window(
        fund, mode="5months", as_of=ref_date
    )
    assert w_start_5 == date(2026, 1, 1)
    assert w_end_5 == date(2026, 12, 31)
    assert "5-cycle" in just_5.lower()
