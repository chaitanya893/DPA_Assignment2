"""Deterministic regression tests for SEC false positive rejection and fund identity validation.

Verifies:
1. IVV revised SAI + qualified-dividend-income tax narrative -> NOT a declaration (UNKNOWN)
2. IVV revised SAI with IWM in trustee ownership table -> NOT IWM evidence (UNKNOWN)
3. IVV revised SAI with IEFA in trustee ownership table -> NOT IEFA evidence (UNKNOWN)
4. 12b-1 Distribution Fee -> NOT a declaration (UNKNOWN)
5. PIMCO Rule 19a-1 positive -> DECLARED
6. BMO official press release positive -> DECLARED
7. Complete negative schedule -> NOT_DECLARED
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.detector import detect_distribution
from src.http_client import HTTPClient, HTTPResponseRecord
from src.models import DetectionStatus, ExtractionRoute, SourceTier
from src.strategies import OfficialSponsorWebStrategy, SECEdgarSubmissionsStrategy
from src.universe_loader import UniverseRegistry


@pytest.fixture
def universe_registry() -> UniverseRegistry:
    return UniverseRegistry.from_json()


def test_ivv_revised_sai_tax_narrative_rejected(
    universe_registry: UniverseRegistry,
) -> None:
    """1. IVV revised SAI containing qualified-dividend-income tax narrative is rejected as NOT a declaration."""
    synthetic_sec_json = {
        "filings": {
            "recent": {
                "form": ["497"],
                "filingDate": ["2026-03-18"],
                "accessionNumber": ["0001193125-26-114192"],
                "primaryDocument": ["d45638d497.htm"],
                "primaryDocDescription": ["497"],
            }
        }
    }
    ivv_sai_html = """
    <html>
      <head><title>iShares Trust Statement of Additional Information</title></head>
      <body>
        <h1>iShares Trust Statement of Additional Information Dated August 1, 2025 (as revised March 18, 2026)</h1>
        <p>Fund: iShares Core S&P 500 ETF (the "Fund") Ticker: IVV Listing Exchange: NYSE Arca</p>
        <h2>Taxes</h2>
        <p>Distributions of investment company taxable income are taxable to you as ordinary income or qualified dividend income.
        Distributions will be subject to capital gain rates to the extent the Fund receives qualified dividend income on the securities it holds and the Fund reports the distribution as qualified dividend income.</p>
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
            content_text=ivv_sai_html,
            content_bytes=ivv_sai_html.encode(),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get
    sec_strategy = SECEdgarSubmissionsStrategy(
        http_client=mock_client, universe=universe_registry
    )

    obs = sec_strategy.inspect(
        fund_id="US_ISHARES_IVV",
        window_start=date(2026, 1, 1),
        window_end=date(2026, 3, 31),
    )
    assert obs.has_declaration_in_window is False

    result = detect_distribution(
        fund_id="US_ISHARES_IVV",
        window_start=date(2026, 1, 1),
        window_end=date(2026, 3, 31),
        strategies=[sec_strategy],
    )
    assert result.status == DetectionStatus.UNKNOWN


def test_iwm_cross_fund_contamination_via_trustee_holdings_rejected(
    universe_registry: UniverseRegistry,
) -> None:
    """2. IVV revised SAI containing IWM only inside a trustee ownership table is rejected for IWM."""
    synthetic_sec_json = {
        "filings": {
            "recent": {
                "form": ["497"],
                "filingDate": ["2026-03-18"],
                "accessionNumber": ["0001193125-26-114192"],
                "primaryDocument": ["d45638d497.htm"],
                "primaryDocDescription": ["497"],
            }
        }
    }
    ivv_sai_with_trustee_table = """
    <html>
      <head><title>iShares Trust Statement of Additional Information</title></head>
      <body>
        <h1>iShares Trust Statement of Additional Information Dated August 1, 2025 (as revised March 18, 2026)</h1>
        <p>Series covered: iShares Core S&P 500 ETF (IVV)</p>
        <h2>Trustee Ownership of Shares</h2>
        <table>
          <tr><td>Madhav V. Rajan</td><td>iShares Russell 2000 ETF</td><td>Over $100,000</td></tr>
        </table>
        <h2>Taxes</h2>
        <p>The Board of Trustees declared a distribution of $1.50 per share payable on March 25, 2026.</p>
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
            content_text=ivv_sai_with_trustee_table,
            content_bytes=ivv_sai_with_trustee_table.encode(),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.1,
        )

    mock_client.get.side_effect = mock_get
    sec_strategy = SECEdgarSubmissionsStrategy(
        http_client=mock_client, universe=universe_registry
    )

    obs = sec_strategy.inspect(
        fund_id="US_ISHARES_IWM",
        window_start=date(2026, 1, 1),
        window_end=date(2026, 3, 31),
    )
    assert obs.has_declaration_in_window is False

    result = detect_distribution(
        fund_id="US_ISHARES_IWM",
        window_start=date(2026, 1, 1),
        window_end=date(2026, 3, 31),
        strategies=[sec_strategy],
    )
    assert result.status == DetectionStatus.UNKNOWN


def test_iefa_cross_fund_contamination_via_trustee_holdings_rejected(
    universe_registry: UniverseRegistry,
) -> None:
    """3. IVV revised SAI containing IEFA only inside a trustee ownership table is rejected for IEFA."""
    synthetic_sec_json = {
        "filings": {
            "recent": {
                "form": ["497"],
                "filingDate": ["2026-03-18"],
                "accessionNumber": ["0001193125-26-114192"],
                "primaryDocument": ["d45638d497.htm"],
                "primaryDocDescription": ["497"],
            }
        }
    }
    ivv_sai_with_trustee_table = """
    <html>
      <head><title>iShares Trust Statement of Additional Information</title></head>
      <body>
        <h1>iShares Trust Statement of Additional Information Dated August 1, 2025 (as revised March 18, 2026)</h1>
        <p>Series covered: iShares Core S&P 500 ETF (IVV)</p>
        <h2>Trustee Ownership of Shares</h2>
        <table>
          <tr><td>Jane D. Carlin</td><td>iShares Core MSCI EAFE ETF</td><td>$50,001-$100,000</td></tr>
        </table>
        <h2>Taxes</h2>
        <p>The Board of Trustees declared a distribution of $1.50 per share payable on March 25, 2026.</p>
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
            content_text=ivv_sai_with_trustee_table,
            content_bytes=ivv_sai_with_trustee_table.encode(),
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
        window_start=date(2026, 1, 1),
        window_end=date(2026, 3, 31),
    )
    assert obs.has_declaration_in_window is False

    result = detect_distribution(
        fund_id="US_ISHARES_IEFA",
        window_start=date(2026, 1, 1),
        window_end=date(2026, 3, 31),
        strategies=[sec_strategy],
    )
    assert result.status == DetectionStatus.UNKNOWN


def test_12b1_distribution_fee_rejected(universe_registry: UniverseRegistry) -> None:
    """4. 12b-1 Distribution Fee in expense table is rejected as NOT a declaration."""
    synthetic_sec_json = {
        "filings": {
            "recent": {
                "form": ["497"],
                "filingDate": ["2026-02-15"],
                "accessionNumber": ["0001193125-26-009999"],
                "primaryDocument": ["f12b1.htm"],
                "primaryDocDescription": ["497"],
            }
        }
    }
    expense_html = """
    <html>
      <body>
        <h1>Vanguard Total Stock Market Index Fund ETF (VTI)</h1>
        <p>Annual Fund Operating Expenses: Management Fees: 0.03%, 12b-1 distribution fee: None, Other Expenses: 0.00%.</p>
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
            content_text=expense_html,
            content_bytes=expense_html.encode(),
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

    result = detect_distribution(
        fund_id="US_VANGUARD_VTI",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[sec_strategy],
    )
    assert result.status == DetectionStatus.UNKNOWN


def test_pimco_rule_19a1_remains_declared(universe_registry: UniverseRegistry) -> None:
    """5. Real PIMCO Rule 19a-1 source notice declaring distribution yields DECLARED."""
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
        <p>The Fund has declared a monthly distribution of $0.2205 per share, payable on February 27, 2026 to shareholders of record on February 13, 2026.</p>
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
    assert obs.evidence[0].source_tier == SourceTier.TIER_1_AUTHORITATIVE
    assert obs.evidence[0].declaration_date_found == date(2026, 2, 2)

    result = detect_distribution(
        fund_id="US_PIMCO_BOND",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[sec_strategy],
    )
    assert result.status == DetectionStatus.DECLARED
    assert result.confidence == 1.0


def test_bmo_official_announcement_remains_declared(
    universe_registry: UniverseRegistry,
) -> None:
    """6. Real BMO official press release declaring distribution yields DECLARED."""
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
        http_client=mock_client,
        universe=universe_registry,
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


def test_exhaustive_negative_schedule_remains_not_declared(
    universe_registry: UniverseRegistry,
) -> None:
    """7. Complete verified schedule table with 0 declarations in window yields NOT_DECLARED."""
    schedule_html = """
    <html>
      <body>
        <h1>2026 Distribution Schedule</h1>
        <table>
          <tr><th>Ex-Date</th><th>Record Date</th><th>Payable Date</th><th>Amount</th></tr>
          <tr><td>2026-03-20</td><td>2026-03-23</td><td>2026-03-27</td><td>$0.75</td></tr>
          <tr><td>2026-06-19</td><td>2026-06-22</td><td>2026-06-26</td><td>$0.80</td></tr>
          <tr><td>2026-09-18</td><td>2026-09-21</td><td>2026-09-25</td><td>$0.78</td></tr>
          <tr><td>2026-12-18</td><td>2026-12-21</td><td>2026-12-28</td><td>$0.85</td></tr>
        </table>
      </body>
    </html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://investor.vanguard.com/investment-products/etfs/profile/vti",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=schedule_html,
        content_bytes=schedule_html.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )
    sponsor_strategy = OfficialSponsorWebStrategy(
        http_client=mock_client,
        universe=universe_registry,
    )
    # Check February 2026 window where no distribution occurs
    obs = sponsor_strategy.inspect(
        fund_id="US_VANGUARD_VTI",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
    )
    assert obs.has_declaration_in_window is False
    assert obs.window_fully_covered is True

    result = detect_distribution(
        fund_id="US_VANGUARD_VTI",
        window_start=date(2026, 2, 1),
        window_end=date(2026, 2, 28),
        strategies=[sponsor_strategy],
    )
    assert result.status == DetectionStatus.NOT_DECLARED
    assert result.confidence == 0.85
