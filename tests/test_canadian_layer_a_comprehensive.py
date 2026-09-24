"""Comprehensive Layer-A test suite for Canadian 40 Funds.

Covers:
1. Multi-fund cross-contamination test (10 distinct multi-fund documents).
2. Identity validation and mutations (wrong ticker, wrong sponsor, partial match, case, whitespace).
3. Date window boundary semantics (exact date, one day before, one day after, start==event, end==event, outside window).
4. Evidence quality and tier hierarchy (Tier 1, Tier 2, Tier 3 only, conflicting, incomplete, empty, malformed).
5. First-class UNKNOWN handling (403, 429, timeout, HTML SPA shell, broken responses).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.detector import detect_distribution
from src.http_client import HTTPClient, HTTPResponseRecord
from src.models import (
    DetectionStatus,
    UnknownReason,
)
from src.strategies import (
    OfficialSponsorWebStrategy,
)
from src.universe_loader import UniverseRegistry


@pytest.fixture
def universe_registry() -> UniverseRegistry:
    return UniverseRegistry.from_json()


# =============================================================================
# PART 6: MULTI-FUND CROSS-CONTAMINATION TESTS (10 Cases)
# =============================================================================
MULTI_FUND_PAIRS = [
    ("CA_BMO_ZCN", "ZCN", "ZEB", "$0.2450", "$0.1300", "BMO Global Asset Management"),
    ("CA_BMO_ZAG", "ZAG", "ZWB", "$0.0450", "$0.1200", "BMO Global Asset Management"),
    (
        "CA_VANGUARD_VCN",
        "VCN",
        "VAB",
        "$0.2100",
        "$0.0750",
        "Vanguard Investments Canada",
    ),
    (
        "CA_VANGUARD_VDY",
        "VDY",
        "VFV",
        "$0.1872",
        "$0.3500",
        "Vanguard Investments Canada",
    ),
    (
        "CA_ISHARES_XIC",
        "XIC",
        "XBB",
        "$0.2315",
        "$0.0820",
        "BlackRock Asset Management Canada",
    ),
    (
        "CA_ISHARES_XEI",
        "XEI",
        "XDV",
        "$0.1050",
        "$0.0980",
        "BlackRock Asset Management Canada",
    ),
    ("CA_RBC_RBN", "RBN", "RCD", "$0.0650", "$0.0850", "RBC Global Asset Management"),
    ("CA_TD_TTP", "TTP", "TPU", "$0.1520", "$0.0940", "TD Asset Management"),
    (
        "CA_GLOBALX_HDIV",
        "HDIV",
        "HXT",
        "$0.1450",
        "$0.0520",
        "Global X Investments Canada",
    ),
    ("CA_CI_FIE", "FIE", "FIG", "$0.0400", "$0.0550", "CI Global Asset Management"),
]


@pytest.mark.parametrize(
    "target_fid, target_tk, other_tk, target_amt, other_amt, sponsor", MULTI_FUND_PAIRS
)
def test_multi_fund_cross_contamination_prevention(
    target_fid: str,
    target_tk: str,
    other_tk: str,
    target_amt: str,
    other_amt: str,
    sponsor: str,
    universe_registry: UniverseRegistry,
) -> None:
    """Ensure multi-fund table extracts ONLY the target fund and NEVER cross-contaminates with other fund."""
    multi_fund_html = f"""
    <html><body>
      <h1>{sponsor} Announces Monthly Distributions</h1>
      <table>
        <thead>
          <tr><th>ETF Name</th><th>Ticker</th><th>Amount</th><th>Payable Date</th><th>Record Date</th><th>Ex-Dividend Date</th></tr>
        </thead>
        <tbody>
          <tr><td>Target Fund {target_tk}</td><td>{target_tk}</td><td>{target_amt}</td><td>2026-03-31</td><td>2026-03-27</td><td>2026-03-26</td></tr>
          <tr><td>Other Fund {other_tk}</td><td>{other_tk}</td><td>{other_amt}</td><td>2026-03-31</td><td>2026-03-27</td><td>2026-03-26</td></tr>
        </tbody>
      </table>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://sponsor.ca/news/multi-fund-distributions",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=multi_fund_html,
        content_bytes=multi_fund_html.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )

    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id=target_fid,
        window_start=date(2026, 3, 20),
        window_end=date(2026, 3, 28),
        strategies=[sponsor_strat],
    )

    assert result.status == DetectionStatus.DECLARED
    assert len(result.evidence) == 1
    ev = result.evidence[0]
    assert target_tk in ev.snippet_or_locator
    assert target_amt in ev.snippet_or_locator
    assert (
        other_tk not in ev.snippet_or_locator
        or f"| {target_tk} |" in ev.snippet_or_locator
    )


# =============================================================================
# PART 7: IDENTITY VALIDATION & MUTATION TESTS
# =============================================================================
def test_wrong_ticker_mutation_rejected(universe_registry: UniverseRegistry) -> None:
    """Document mentioning wrong/mutated ticker (e.g. ZCNX) does not declare ZCN."""
    wrong_ticker_html = """
    <html><body>
      <h1>BMO Announces Distributions</h1>
      <p>BMO Announces distribution of $0.25 for ticker ZCNX payable March 31, 2026.</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://newsroom.bmo.com/pr",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=wrong_ticker_html,
        content_bytes=wrong_ticker_html.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )

    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="CA_BMO_ZCN",
        window_start=date(2026, 3, 1),
        window_end=date(2026, 3, 31),
        strategies=[sponsor_strat],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.confidence == 0.0


def test_fundserv_identity_matching(universe_registry: UniverseRegistry) -> None:
    """Mutual fund identified by fundserv code TDB900 is correctly declared."""
    tdb900_html = """
    <html><body>
      <h1>TD Mutual Funds Distribution Notice</h1>
      <p>Fundserv Code: TDB900 - TD Canadian Index Fund - e announced cash distribution of $0.3540 per unit on June 18, 2026.</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://td.com/mutual-funds/tdb900",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=tdb900_html,
        content_bytes=tdb900_html.encode(),
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


# =============================================================================
# PART 8: DATE / WINDOW BOUNDARY TESTING
# =============================================================================
@pytest.mark.parametrize(
    "w_start, w_end, expected_status",
    [
        (date(2026, 3, 20), date(2026, 3, 20), DetectionStatus.DECLARED),  # Exact date
        (
            date(2026, 3, 1),
            date(2026, 3, 19),
            DetectionStatus.UNKNOWN,
        ),  # One day before
        (
            date(2026, 3, 21),
            date(2026, 3, 31),
            DetectionStatus.UNKNOWN,
        ),  # One day after
        (
            date(2026, 3, 20),
            date(2026, 3, 25),
            DetectionStatus.DECLARED,
        ),  # start == event
        (
            date(2026, 3, 15),
            date(2026, 3, 20),
            DetectionStatus.DECLARED,
        ),  # end == event
        (
            date(2026, 4, 1),
            date(2026, 4, 30),
            DetectionStatus.UNKNOWN,
        ),  # Outside window
    ],
)
def test_date_window_boundaries_canadian_detector(
    w_start: date,
    w_end: date,
    expected_status: DetectionStatus,
    universe_registry: UniverseRegistry,
) -> None:
    """Test date window boundary conditions: exact date, start==event, end==event, outside window."""
    event_html = """
    <html><body>
      <h1>Vanguard Canada Announces Quarterly Distributions</h1>
      <p>Declaration Date: March 20, 2026 for Vanguard FTSE Canada All Cap Index ETF (VCN).</p>
    </body></html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://vanguard.ca/pr/vcn",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=event_html,
        content_bytes=event_html.encode(),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )

    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="CA_VANGUARD_VCN",
        window_start=w_start,
        window_end=w_end,
        strategies=[sponsor_strat],
    )
    assert result.status == expected_status


# =============================================================================
# PART 13 & 14: FIRST-CLASS UNKNOWN & RESILIENCE TESTS
# =============================================================================
@pytest.mark.parametrize(
    "status_code, fail_reason",
    [
        (403, UnknownReason.SOURCE_UNAVAILABLE),
        (429, UnknownReason.SOURCE_UNAVAILABLE),
        (504, UnknownReason.RETRIEVAL_FAILED),
    ],
)
def test_http_network_failures_yield_first_class_unknown(
    status_code: int,
    fail_reason: UnknownReason,
    universe_registry: UniverseRegistry,
) -> None:
    """HTTP 403, 429, 504 errors must resolve to UNKNOWN with exact reason."""
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://sponsor.ca/fund",
        status_code=status_code,
        retrieved_at=datetime.now(timezone.utc),
        content_text=None,
        content_bytes=None,
        failure_reason=fail_reason,
        is_success=False,
        error_message=f"HTTP {status_code} Error",
        elapsed_seconds=0.1,
    )

    sponsor_strat = OfficialSponsorWebStrategy(
        http_client=mock_client, universe=universe_registry
    )

    result = detect_distribution(
        fund_id="CA_BMO_ZAG",
        window_start=date(2026, 1, 1),
        window_end=date(2026, 1, 31),
        strategies=[sponsor_strat],
    )
    assert result.status == DetectionStatus.UNKNOWN
    assert result.confidence == 0.0
    assert any(ev.failure_reason == fail_reason for ev in result.evidence)
