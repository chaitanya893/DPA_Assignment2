"""Canadian 40 Funds Layer A Distribution Detection deterministic test suite.

Tests all 14 required domain scenarios for Canadian Funds:
1. Real official positive evidence -> DECLARED
2. Complete official negative coverage -> NOT_DECLARED
3. Incomplete sponsor page -> UNKNOWN
4. WAF / 403 Forbidden -> UNKNOWN
5. Dynamic page (SPA shell) -> UNKNOWN
6. Tier-3-only evidence -> cannot final-declare (UNKNOWN)
7. Wrong-fund evidence / cross-fund contamination -> reject
8. Conflicting evidence -> UNKNOWN
9. Monthly fund real declaration -> DECLARED
10. Quarterly fund on-cadence declaration -> DECLARED
11. Semi-annual fund on-cadence declaration -> DECLARED
12. Annual fund off-month complete schedule -> NOT_DECLARED
13. Canadian year-end distribution -> DECLARED
14. T3/T5 / tax narrative false-positive rejection -> UNKNOWN
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.detector import detect_distribution, synthesize_detection_result
from src.http_client import HTTPClient, HTTPResponseRecord
from src.models import (
    DetectionStatus,
    Evidence,
    SourceTier,
    UnknownReason,
)
from src.strategies import (
    OfficialSponsorWebStrategy,
    SignalType,
    StrategyObservation,
)
from src.universe_loader import UniverseRegistry


@pytest.fixture
def universe_registry() -> UniverseRegistry:
    return UniverseRegistry.from_json()


# -----------------------------------------------------------------------------
# 1. Real official positive evidence -> DECLARED
# -----------------------------------------------------------------------------
def test_canadian_official_positive_evidence_yields_declared(
    universe_registry: UniverseRegistry,
) -> None:
    """BMO ZCN with official press release announcement yields DECLARED."""
    bmo_pr_html = """
    <html><body>
      <h1>BMO Global Asset Management Announces Cash Distributions for BMO ETFs</h1>
      <p>TORONTO, March 18, 2026 - BMO Global Asset Management today announced the quarterly cash distributions for BMO ETFs.</p>
      <table>
        <tr><th>ETF Name</th><th>Ticker</th><th>Cash Distribution per Unit ($)</th><th>Record Date</th><th>Payable Date</th></tr>
        <tr><td>BMO S&P/TSX Capped Composite Index ETF</td><td>ZCN</td><td>$0.2450</td><td>March 27, 2026</td><td>March 31, 2026</td></tr>
      </table>
      <p>Declaration Date: March 18, 2026. Unitholders of record on March 27, 2026 will receive cash distribution.</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://newsroom.bmo.com/2026-03-18-BMO-Global-Asset-Management-Announces-Cash-Distributions-for-BMO-ETFs",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=bmo_pr_html,
        content_bytes=bmo_pr_html.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )

    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="CA_BMO_ZCN",
        window_start=date(2026, 3, 15),
        window_end=date(2026, 3, 20),
        strategies=[sponsor_strat],
    )
    assert result.status == DetectionStatus.DECLARED
    assert result.confidence is not None and result.confidence >= 0.85
    assert len(result.evidence) >= 1
    assert result.evidence[0].source_tier == SourceTier.TIER_2_PRIMARY_UNSTRUCTURED


# -----------------------------------------------------------------------------
# 2. Complete official negative coverage -> NOT_DECLARED
# -----------------------------------------------------------------------------
def test_canadian_complete_negative_schedule_yields_not_declared(
    universe_registry: UniverseRegistry,
) -> None:
    """Vanguard Canada VCN (Quarterly: Mar/Jun/Sep/Dec) in Feb (off-cadence) with complete schedule."""
    annual_schedule_html = """
    <html><body>
      <h1>Vanguard FTSE Canada All Cap Index ETF (VCN)</h1>
      <h2>2026 Distribution Schedule</h2>
      <table>
        <tr><th>Quarter</th><th>Declaration Date</th><th>Ex-Dividend Date</th><th>Payable Date</th></tr>
        <tr><td>Q1</td><td>2026-03-20</td><td>2026-03-23</td><td>2026-03-27</td></tr>
        <tr><td>Q2</td><td>2026-06-19</td><td>2026-06-22</td><td>2026-06-26</td></tr>
        <tr><td>Q3</td><td>2026-09-18</td><td>2026-09-21</td><td>2026-09-25</td></tr>
        <tr><td>Q4</td><td>2026-12-18</td><td>2026-12-21</td><td>2026-12-28</td></tr>
      </table>
      <p>Distribution schedule verified for all quarters of 2026.</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://www.vanguard.ca/en/investor/products/products-group/etfs/VCN",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=annual_schedule_html,
        content_bytes=annual_schedule_html.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )

    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="CA_VANGUARD_VCN",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[sponsor_strat],
    )
    assert result.status == DetectionStatus.NOT_DECLARED
    assert result.confidence is not None and result.confidence >= 0.85


# -----------------------------------------------------------------------------
# 3. Incomplete sponsor page -> UNKNOWN
# -----------------------------------------------------------------------------
def test_canadian_incomplete_sponsor_page_yields_unknown(
    universe_registry: UniverseRegistry,
) -> None:
    """RBC fund page with profile details but no distribution schedule table yields UNKNOWN."""
    profile_html = """
    <html><body>
      <h1>RBC Quant Canadian Dividend Leaders ETF (RCD)</h1>
      <p>Investment Objective: Seeks long-term capital growth and regular dividend income.</p>
      <p>Portfolio Manager: RBC Global Asset Management Inc.</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://www.rbcgam.com/en/ca/products/etfs/RCD/detail",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=profile_html,
        content_bytes=profile_html.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )

    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="CA_RBC_RCD",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[sponsor_strat],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert any(
        ev.failure_reason == UnknownReason.INSUFFICIENT_EVIDENCE
        for ev in result.evidence
    )


# -----------------------------------------------------------------------------
# 4. WAF / 403 Forbidden -> UNKNOWN
# -----------------------------------------------------------------------------
def test_canadian_waf_403_blocked_yields_unknown(
    universe_registry: UniverseRegistry,
) -> None:
    """Sponsor portal blocked with HTTP 403 returns UNKNOWN with SOURCE_UNAVAILABLE."""
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://www.rbcgam.com/en/ca/products/etfs/RBN",
        status_code=403,
        retrieved_at=datetime.now(timezone.utc),
        content_text=None,
        content_bytes=None,
        failure_reason=UnknownReason.SOURCE_UNAVAILABLE,
        is_success=False,
        error_message="HTTP 403 Forbidden: WAF Anti-bot challenge",
        elapsed_seconds=0.1,
    )

    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="CA_RBC_RBN",
        window_start=date(2026, 1, 15),
        window_end=date(2026, 1, 25),
        strategies=[sponsor_strat],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.confidence == 0.0
    assert any(
        ev.failure_reason == UnknownReason.SOURCE_UNAVAILABLE for ev in result.evidence
    )


# -----------------------------------------------------------------------------
# 5. Dynamic page (SPA shell) -> UNKNOWN
# -----------------------------------------------------------------------------
def test_canadian_dynamic_spa_shell_yields_unknown(
    universe_registry: UniverseRegistry,
) -> None:
    """Dynamic React portal shell without static content returns UNKNOWN."""
    spa_html = """
    <html><body>
      <div id="app-root">Loading CI Global Asset Management Fund Portal...</div>
      <script src="/bundles/main.js"></script>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://ci.com/en/funds/etfs/ci-canadian-financial-monthly-income-etf",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=spa_html,
        content_bytes=spa_html.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )

    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="CA_CI_FIE",
        window_start=date(2026, 1, 15),
        window_end=date(2026, 1, 25),
        strategies=[sponsor_strat],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.confidence == 0.0


# -----------------------------------------------------------------------------
# 6. Tier-3-only evidence -> cannot final-declare (UNKNOWN)
# -----------------------------------------------------------------------------
def test_canadian_tier3_only_evidence_never_yields_declared_or_not_declared() -> None:
    """Tier 3 public market observation alone must always resolve to UNKNOWN."""
    tier3_ev = Evidence(
        source_id="public_market_data_corroboration",
        source_tier=SourceTier.TIER_3_CORROBORATION,
        url="https://finance.yahoo.com/quote/XIC.TO",
        retrieved_at=datetime.now(timezone.utc),
        snippet_or_locator="Distribution Yield: 2.85% per third-party aggregator",
        declaration_date_found=date(2026, 3, 20),
    )
    obs = StrategyObservation(
        strategy_name="Tier3CorroborationStrategy",
        signal_type=SignalType.NO_DATA_OBSERVATION,
        evidence=[tier3_ev],
        has_declaration_in_window=True,
        window_fully_covered=False,
        failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
    )

    result = synthesize_detection_result(
        fund_id="CA_ISHARES_XIC",
        window_start=date(2026, 3, 15),
        window_end=date(2026, 3, 25),
        observations=[obs],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.confidence == 0.0


# -----------------------------------------------------------------------------
# 7. Wrong-fund evidence / cross-fund contamination -> reject
# -----------------------------------------------------------------------------
def test_canadian_wrong_fund_evidence_rejected_in_multi_fund_table(
    universe_registry: UniverseRegistry,
) -> None:
    """Multi-fund press release table with only other fund (ZEB) does NOT declare for ZCN."""
    multi_fund_pr_html = """
    <html><body>
      <h1>BMO Global Asset Management Announces ETF Distributions</h1>
      <table>
        <thead>
          <tr><th>ETF Name</th><th>Ticker</th><th>Amount</th><th>Payable Date</th><th>Record Date</th><th>Ex-Dividend Date</th></tr>
        </thead>
        <tbody>
          <tr><td>BMO Equal Weight Banks Index ETF</td><td>ZEB</td><td>$0.1300</td><td>2026-03-31</td><td>2026-03-27</td><td>2026-03-26</td></tr>
        </tbody>
      </table>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://newsroom.bmo.com/pr",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=multi_fund_pr_html,
        content_bytes=multi_fund_pr_html.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )

    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    # Querying ZCN, but table only has ZEB
    result = detect_distribution(
        fund_id="CA_BMO_ZCN",
        window_start=date(2026, 3, 25),
        window_end=date(2026, 3, 28),
        strategies=[sponsor_strat],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.confidence == 0.0


# -----------------------------------------------------------------------------
# 8. Conflicting evidence -> UNKNOWN
# -----------------------------------------------------------------------------
def test_canadian_conflicting_evidence_yields_unknown() -> None:
    """Positive declaration observation vs verified complete negative schedule observation yields UNKNOWN."""
    pos_ev = Evidence(
        source_id="official_bmo_pr",
        source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
        url="https://newsroom.bmo.com/pr",
        retrieved_at=datetime.now(timezone.utc),
        snippet_or_locator="Declared $0.2450 on March 18, 2026",
        declaration_date_found=date(2026, 3, 18),
    )
    neg_ev = Evidence(
        source_id="official_annual_schedule",
        source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
        url="https://bmo.com/schedule",
        retrieved_at=datetime.now(timezone.utc),
        snippet_or_locator="Verified complete negative schedule for March 2026",
    )

    obs_pos = StrategyObservation(
        strategy_name="BMOOfficialPRStrategy",
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
        fund_id="CA_BMO_ZCN",
        window_start=date(2026, 3, 15),
        window_end=date(2026, 3, 20),
        observations=[obs_pos, obs_neg],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert any(
        ev.failure_reason == UnknownReason.CONFLICTING_EVIDENCE
        for ev in result.evidence
    )


# -----------------------------------------------------------------------------
# 9. Monthly fund real declaration -> DECLARED
# -----------------------------------------------------------------------------
def test_canadian_monthly_fund_real_declaration_yields_declared(
    universe_registry: UniverseRegistry,
) -> None:
    """Vanguard Canadian High Dividend (VDY - Monthly) with real January declaration."""
    vanguard_vdy_pr = """
    <html><body>
      <h1>Vanguard Investments Canada Announces Monthly Distributions for Vanguard ETFs</h1>
      <p>TORONTO, Jan 19, 2026 - Vanguard Investments Canada Inc. announced monthly cash distributions for Vanguard FTSE Canadian High Dividend Yield Index ETF (VDY) of $0.1872 per unit payable Jan 30, 2026.</p>
      <p>Declaration Date: January 19, 2026. Record Date: January 26, 2026.</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://www.vanguard.ca/news/vdy-jan-2026",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=vanguard_vdy_pr,
        content_bytes=vanguard_vdy_pr.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )

    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="CA_VANGUARD_VDY",
        window_start=date(2026, 1, 15),
        window_end=date(2026, 1, 20),
        strategies=[sponsor_strat],
    )
    assert result.status == DetectionStatus.DECLARED
    assert result.confidence is not None and result.confidence >= 0.85
    assert result.evidence[0].declaration_date_found == date(2026, 1, 19)


# -----------------------------------------------------------------------------
# 10. Quarterly fund on-cadence declaration -> DECLARED
# -----------------------------------------------------------------------------
def test_canadian_quarterly_fund_on_cadence_declaration_yields_declared(
    universe_registry: UniverseRegistry,
) -> None:
    """iShares XIC (Quarterly) in March with official BlackRock announcement yields DECLARED."""
    ishares_xic_pr = """
    <html><body>
      <h1>BlackRock Canada Announces March 2026 Cash Distributions for iShares ETFs</h1>
      <p>TORONTO, March 20, 2026 - BlackRock Asset Management Canada Limited announced quarterly distribution for iShares Core S&P/TSX Capped Composite Index ETF (XIC) of $0.2315 per unit.</p>
      <p>Declaration Date: March 20, 2026. Payable Date: March 31, 2026.</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://www.blackrock.com/ca/news/xic-q1-2026",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=ishares_xic_pr,
        content_bytes=ishares_xic_pr.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )

    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="CA_ISHARES_XIC",
        window_start=date(2026, 3, 15),
        window_end=date(2026, 3, 25),
        strategies=[sponsor_strat],
    )
    assert result.status == DetectionStatus.DECLARED
    assert result.confidence is not None and result.confidence >= 0.85


# -----------------------------------------------------------------------------
# 11. Semi-annual fund on-cadence declaration -> DECLARED
# -----------------------------------------------------------------------------
def test_canadian_semi_annual_fund_on_cadence_declaration_yields_declared(
    universe_registry: UniverseRegistry,
) -> None:
    """TD e-Series TDB900 (Semi-Annual: Jun/Dec) with official June distribution announcement."""
    td_pr_html = """
    <html><body>
      <h1>TD Asset Management Announces Semi-Annual Mutual Fund Distributions</h1>
      <p>TORONTO, June 18, 2026 - TD Asset Management announced semi-annual distribution for TD Canadian Index Fund - e (TDB900) of $0.3540 per unit.</p>
      <p>Declaration Date: June 18, 2026. Payable Date: June 25, 2026.</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://www.td.com/ca/en/asset-management/news/tdb900-jun-2026",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=td_pr_html,
        content_bytes=td_pr_html.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )

    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="CA_TD_TDB900",
        window_start=date(2026, 6, 15),
        window_end=date(2026, 6, 20),
        strategies=[sponsor_strat],
    )
    assert result.status == DetectionStatus.DECLARED
    assert result.confidence is not None and result.confidence >= 0.85


# -----------------------------------------------------------------------------
# 12. Annual fund off-month complete schedule -> NOT_DECLARED
# -----------------------------------------------------------------------------
def test_canadian_annual_fund_off_month_complete_schedule_yields_not_declared(
    universe_registry: UniverseRegistry,
) -> None:
    """Global X HXT (Annual: December) in May with complete verified corporate class schedule."""
    hxt_schedule_html = """
    <html><body>
      <h1>Global X S&P/TSX 60 Index Corporate Class ETF (HXT)</h1>
      <h2>2026 Distribution Schedule & Corporate Structure</h2>
      <p>HXT operates under a corporate class structure designed not to pay regular monthly or quarterly dividends.</p>
      <table>
        <tr><th>Scheduled Month</th><th>Distribution Frequency</th><th>Expected Declaration</th></tr>
        <tr><td>December 2026</td><td>Annual Capital Distribution</td><td>2026-12-18</td></tr>
      </table>
      <p>Note: No distribution scheduled or declared for May 2026 (off-cadence month).</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://globalx.ca/product/hxt",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=hxt_schedule_html,
        content_bytes=hxt_schedule_html.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )

    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="CA_GLOBALX_HXT",
        window_start=date(2026, 5, 1),
        window_end=date(2026, 5, 31),
        strategies=[sponsor_strat],
    )
    assert result.status == DetectionStatus.NOT_DECLARED
    assert result.confidence is not None and result.confidence >= 0.85


# -----------------------------------------------------------------------------
# 13. Canadian year-end distribution -> DECLARED
# -----------------------------------------------------------------------------
def test_canadian_year_end_distribution_yields_declared(
    universe_registry: UniverseRegistry,
) -> None:
    """Global X HXT in December with annual year-end reinvested distribution announcement."""
    hxt_yearend_html = """
    <html><body>
      <h1>Global X Investments Canada Announces Final Annual Reinvested Distributions</h1>
      <p>TORONTO, December 18, 2026 - Global X Investments Canada Inc. announced annual year-end distribution of $0.0520 per share for Global X S&P/TSX 60 Index Corporate Class ETF (HXT).</p>
      <p>Declaration Date: December 18, 2026. Record Date: December 28, 2026.</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://globalx.ca/news/2026-year-end-distributions",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=hxt_yearend_html,
        content_bytes=hxt_yearend_html.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )

    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="CA_GLOBALX_HXT",
        window_start=date(2026, 12, 15),
        window_end=date(2026, 12, 20),
        strategies=[sponsor_strat],
    )
    assert result.status == DetectionStatus.DECLARED
    assert result.confidence is not None and result.confidence >= 0.85


# -----------------------------------------------------------------------------
# 14. T3/T5 / tax narrative false-positive rejection -> UNKNOWN
# -----------------------------------------------------------------------------
def test_canadian_t3_t5_tax_narrative_false_positive_rejected(
    universe_registry: UniverseRegistry,
) -> None:
    """General T3/T5 tax slip guide without operative cash declaration is rejected."""
    tax_guide_html = """
    <html><body>
      <h1>BMO ETFs T3 and T5 Tax Information Guide for Investors</h1>
      <p>This tax package provides general instructions for preparing your T3 and T5 tax slips for the preceding taxation year.</p>
      <p>Tax characteristics and capital gain allocations are reported annually by the trust administrator.</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://www.bmogam.com/tax-guide",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=tax_guide_html,
        content_bytes=tax_guide_html.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )

    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="CA_BMO_ZCN",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[sponsor_strat],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.confidence == 0.0
    assert any(
        ev.failure_reason == UnknownReason.INSUFFICIENT_EVIDENCE
        for ev in result.evidence
    )
