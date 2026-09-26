"""Unit tests for optional API URL support, safety buffer, RecordingHTTPClient caching, and RobotsPolicy.

All test fixtures use synthetic data and test doubles. No real network calls are made.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx

from src.detector import detect_distribution
from src.http_client import HTTPResponseRecord, RecordingHTTPClient, RobotsPolicy
from src.models import DetectionStatus, ExtractionRoute, UnknownReason
from src.rate_limiter import RateLimiter
from src.strategies import OfficialSponsorWebStrategy
from src.universe_loader import UniverseFund, UniverseRegistry


class SyntheticHTTPClient:
    """Mock client returning predetermined records."""

    def __init__(self, record_factory) -> None:
        self.record_factory = record_factory
        self.call_count = 0
        self.urls: list[str] = []

    def get(
        self, url: str, headers: dict | None = None, params: dict | None = None
    ) -> HTTPResponseRecord:
        self.call_count += 1
        self.urls.append(url)
        return self.record_factory(url)


def test_json_distribution_inside_window_declared_and_api_route() -> None:
    """JSON distribution with ex_date inside window results in DECLARED and API route."""
    fund = UniverseFund(
        fund_id="TEST_API_FUND",
        country="US",
        fund_name="Test API Fund",
        fund_type="ETF",
        fund_family="TestFamily",
        official_source_url="https://sponsor.example.com/fund",
        distribution_api_url="https://api.sponsor.example.com/distributions.json",
        ticker="TAPI",
    )
    registry = UniverseRegistry([fund])

    payload = json.dumps(
        [
            {
                "ticker": "TAPI",
                "ex_date": "2026-03-15",
                "record_date": "2026-03-16",
                "payable_date": "2026-03-20",
                "amount": 0.4523,
                "distribution_type": "Income",
            }
        ]
    )

    retrieved_at = datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc)

    def record_factory(url: str) -> HTTPResponseRecord:
        return HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=retrieved_at,
            content_text=payload,
            content_bytes=payload.encode("utf-8"),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.05,
            content_type="application/json",
        )

    mock_client = SyntheticHTTPClient(record_factory)
    strategy = OfficialSponsorWebStrategy(
        http_client=mock_client,
        universe=registry,
    )

    w_start = date(2026, 3, 1)
    w_end = date(2026, 3, 31)

    obs = strategy.inspect("TEST_API_FUND", w_start, w_end)
    assert obs.has_declaration_in_window is True
    assert obs.window_fully_covered is True
    assert obs.suggested_route == ExtractionRoute.API
    assert len(obs.evidence) == 1
    assert obs.evidence[0].ex_date_found == date(2026, 3, 15)

    # Full detection pipeline evaluation
    res = detect_distribution(
        fund_id="TEST_API_FUND",
        window_start=w_start,
        window_end=w_end,
        strategies=[strategy],
    )
    assert res.status == DetectionStatus.DECLARED
    assert res.suggested_extraction_route == ExtractionRoute.API


def test_json_negative_8_plus_days_after_window_not_declared() -> None:
    """JSON distribution list spanning history checked 8+ days after window -> NOT_DECLARED."""
    fund = UniverseFund(
        fund_id="TEST_NEG_FUND",
        country="US",
        fund_name="Test Negative Fund",
        fund_type="Mutual Fund",
        fund_family="TestFamily",
        official_source_url="https://sponsor.example.com/fund",
        distribution_api_url="https://api.sponsor.example.com/distributions.json",
        ticker="TNEG",
    )
    registry = UniverseRegistry([fund])

    # Has distributions before March 2026, but nothing inside March 2026
    payload = json.dumps(
        [
            {"ticker": "TNEG", "ex_date": "2026-01-15", "amount": 0.20},
            {"ticker": "TNEG", "ex_date": "2026-02-15", "amount": 0.20},
        ]
    )

    w_start = date(2026, 3, 1)
    w_end = date(2026, 3, 31)
    # Retrieved on 2026-04-09 (9 days after window_end -> >= 7 days buffer)
    retrieved_at = datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc)

    def record_factory(url: str) -> HTTPResponseRecord:
        return HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=retrieved_at,
            content_text=payload,
            content_bytes=payload.encode("utf-8"),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.05,
            content_type="application/json",
        )

    mock_client = SyntheticHTTPClient(record_factory)
    strategy = OfficialSponsorWebStrategy(
        http_client=mock_client,
        universe=registry,
    )

    obs = strategy.inspect("TEST_NEG_FUND", w_start, w_end)
    assert obs.has_declaration_in_window is False
    assert obs.window_fully_covered is True

    res = detect_distribution(
        fund_id="TEST_NEG_FUND",
        window_start=w_start,
        window_end=w_end,
        strategies=[strategy],
    )
    assert res.status == DetectionStatus.NOT_DECLARED


def test_json_negative_2_days_after_window_unknown() -> None:
    """JSON distribution checked only 2 days after window (< 7 day buffer) -> UNKNOWN."""
    fund = UniverseFund(
        fund_id="TEST_BUFFER_FUND",
        country="US",
        fund_name="Test Buffer Fund",
        fund_type="Mutual Fund",
        fund_family="TestFamily",
        official_source_url="https://sponsor.example.com/fund",
        distribution_api_url="https://api.sponsor.example.com/distributions.json",
        ticker="TBUF",
    )
    registry = UniverseRegistry([fund])

    # Has distributions before March 2026, nothing inside March 2026
    payload = json.dumps(
        [
            {"ticker": "TBUF", "ex_date": "2026-01-15", "amount": 0.20},
            {"ticker": "TBUF", "ex_date": "2026-02-15", "amount": 0.20},
        ]
    )

    w_start = date(2026, 3, 1)
    w_end = date(2026, 3, 31)
    # Retrieved on 2026-04-02 (2 days after window_end -> < 7 days buffer)
    retrieved_at = datetime(2026, 4, 2, 12, 0, tzinfo=timezone.utc)

    def record_factory(url: str) -> HTTPResponseRecord:
        return HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=retrieved_at,
            content_text=payload,
            content_bytes=payload.encode("utf-8"),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.05,
            content_type="application/json",
        )

    mock_client = SyntheticHTTPClient(record_factory)
    strategy = OfficialSponsorWebStrategy(
        http_client=mock_client,
        universe=registry,
    )

    obs = strategy.inspect("TEST_BUFFER_FUND", w_start, w_end)
    assert obs.has_declaration_in_window is False
    assert obs.window_fully_covered is False

    res = detect_distribution(
        fund_id="TEST_BUFFER_FUND",
        window_start=w_start,
        window_end=w_end,
        strategies=[strategy],
    )
    assert res.status == DetectionStatus.UNKNOWN


def test_recording_http_client_caching_and_retry_behavior() -> None:
    """RecordingHTTPClient caches permanent codes (200, 403) on 1st hit, and temporary failures after 2 attempts."""
    mock_inner = MagicMock()
    now = datetime.now(timezone.utc)

    # 1. Status 200 OK: cached immediately
    rec_200 = HTTPResponseRecord(
        url="https://test.example.com/200",
        status_code=200,
        retrieved_at=now,
        content_text="ok",
        content_bytes=b"ok",
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.01,
    )
    mock_inner.get.return_value = rec_200

    recorder = RecordingHTTPClient(inner=mock_inner)
    resp1 = recorder.get("https://test.example.com/200")
    resp2 = recorder.get("https://test.example.com/200")

    assert resp1 == rec_200
    assert resp2 == rec_200
    assert mock_inner.get.call_count == 1

    # 2. Status 403 Forbidden: cached immediately (permanent)
    mock_inner.reset_mock()
    rec_403 = HTTPResponseRecord(
        url="https://test.example.com/403",
        status_code=403,
        retrieved_at=now,
        content_text=None,
        content_bytes=None,
        failure_reason=UnknownReason.RETRIEVAL_FAILED,
        is_success=False,
        elapsed_seconds=0.01,
    )
    mock_inner.get.return_value = rec_403

    recorder = RecordingHTTPClient(inner=mock_inner)
    resp1 = recorder.get("https://test.example.com/403")
    resp2 = recorder.get("https://test.example.com/403")

    assert resp1 == rec_403
    assert resp2 == rec_403
    assert mock_inner.get.call_count == 1

    # 3. Timeout / temporary failure: retried once, then cached on second attempt
    mock_inner.reset_mock()
    rec_timeout = HTTPResponseRecord(
        url="https://test.example.com/timeout",
        status_code=None,
        retrieved_at=now,
        content_text=None,
        content_bytes=None,
        failure_reason=UnknownReason.RETRIEVAL_FAILED,
        is_success=False,
        elapsed_seconds=15.0,
        error_message="HTTP Timeout",
    )
    mock_inner.get.return_value = rec_timeout

    recorder = RecordingHTTPClient(inner=mock_inner)
    # Attempt 1: not cached yet
    t1 = recorder.get("https://test.example.com/timeout")
    assert t1 == rec_timeout
    assert mock_inner.get.call_count == 1
    assert "https://test.example.com/timeout" not in recorder._by_url

    # Attempt 2: retry occurs, and now cached
    t2 = recorder.get("https://test.example.com/timeout")
    assert t2 == rec_timeout
    assert mock_inner.get.call_count == 2
    assert "https://test.example.com/timeout" in recorder._by_url

    # Attempt 3: served from cache without calling inner client
    t3 = recorder.get("https://test.example.com/timeout")
    assert t3 == rec_timeout
    assert mock_inner.get.call_count == 2


def test_robots_policy_rules_cooldown_and_failures() -> None:
    """RobotsPolicy allows 404 (RFC 9309), cools down temporary failures, and enforces 3-failure cap."""
    limiter = RateLimiter()

    # 1. 404 on robots.txt -> allowed
    RobotsPolicy.clear()
    resp_404 = MagicMock(status_code=404, text="Not Found")
    with patch("httpx.Client.get", return_value=resp_404) as mock_get:
        allowed = RobotsPolicy.allowed(
            "https://test-404.example.com/page",
            "TestAgent/1.0 (ops@example.com)",
            5.0,
            limiter,
        )
        assert allowed is True
        assert mock_get.call_count == 1

    # 2. Cooldown on temporary timeout/failure
    RobotsPolicy.clear()
    with patch(
        "httpx.Client.get", side_effect=httpx.ConnectTimeout("Timeout")
    ) as mock_get:
        # First call fails and initiates cooldown
        allowed1 = RobotsPolicy.allowed(
            "https://test-timeout.example.com/page",
            "TestAgent/1.0 (ops@example.com)",
            5.0,
            limiter,
        )
        assert allowed1 is False
        assert mock_get.call_count == 1

        # Second call within cooldown window returns False without re-fetching
        allowed2 = RobotsPolicy.allowed(
            "https://test-timeout.example.com/page",
            "TestAgent/1.0 (ops@example.com)",
            5.0,
            limiter,
        )
        assert allowed2 is False
        assert mock_get.call_count == 1

    # 3. 3-failure cap permanently disallows host
    RobotsPolicy.clear()
    with patch(
        "httpx.Client.get", side_effect=httpx.ConnectError("Unreachable")
    ) as mock_get:
        host = "https://test-maxfail.example.com"
        # Simulate 3 sequential failure attempts past cooldown
        for _ in range(3):
            RobotsPolicy.allowed(
                f"{host}/page",
                "TestAgent/1.0 (ops@example.com)",
                5.0,
                limiter,
            )
            # Advance cache timestamp past cooldown for the next attempt
            with RobotsPolicy._lock:
                rp, _ = RobotsPolicy._cache[host]
                RobotsPolicy._cache[host] = (rp, 0.0)

        assert mock_get.call_count == 3
        assert RobotsPolicy._fail_count[host] == 3

        # 4th call: even with expired timestamp, fail_count >= 3 blocks request without network hit
        allowed_after_max = RobotsPolicy.allowed(
            f"{host}/page",
            "TestAgent/1.0 (ops@example.com)",
            5.0,
            limiter,
        )
        assert allowed_after_max is False
        assert mock_get.call_count == 3


def test_full_year_schedule_safety_buffer() -> None:
    """A published full-year schedule fetched 2 days after window is UNKNOWN, and 8 days after is NOT_DECLARED."""
    fund = UniverseFund(
        fund_id="TEST_SCHED_FUND",
        country="US",
        fund_name="Test Schedule Fund",
        fund_type="ETF",
        fund_family="TestFamily",
        official_source_url="https://sponsor.example.com/fund-schedule",
        ticker="TSCHED",
    )
    registry = UniverseRegistry([fund])

    # Table contains 2026 distribution dates in June and Dec (none in March, and earliest is June so spans_window is False)
    html_content = """
    <html>
      <body>
        <h1>2026 Distribution Schedule</h1>
        <table>
          <thead>
            <tr><th>Ticker</th><th>Ex-Date</th><th>Record Date</th><th>Payable Date</th><th>Amount</th></tr>
          </thead>
          <tbody>
            <tr><td>TSCHED</td><td>2026-06-15</td><td>2026-06-16</td><td>2026-06-20</td><td>$0.50</td></tr>
            <tr><td>TSCHED</td><td>2026-12-15</td><td>2026-12-16</td><td>2026-12-20</td><td>$0.50</td></tr>
          </tbody>
        </table>
      </body>
    </html>
    """

    w_start = date(2026, 3, 1)
    w_end = date(2026, 3, 31)

    # 1. Fetched 2 days after window_end -> UNKNOWN
    retrieved_2_days = datetime(2026, 4, 2, 12, 0, tzinfo=timezone.utc)
    mock_client_2 = SyntheticHTTPClient(
        lambda url: HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=retrieved_2_days,
            content_text=html_content,
            content_bytes=html_content.encode("utf-8"),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.05,
            content_type="text/html",
        )
    )
    strategy_2 = OfficialSponsorWebStrategy(
        http_client=mock_client_2,
        universe=registry,
    )
    obs_2 = strategy_2.inspect("TEST_SCHED_FUND", w_start, w_end)
    assert obs_2.window_fully_covered is False
    res_2 = detect_distribution(
        fund_id="TEST_SCHED_FUND",
        window_start=w_start,
        window_end=w_end,
        strategies=[strategy_2],
    )
    assert res_2.status == DetectionStatus.UNKNOWN

    # 2. Fetched 8 days after window_end -> NOT_DECLARED
    retrieved_8_days = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
    mock_client_8 = SyntheticHTTPClient(
        lambda url: HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=retrieved_8_days,
            content_text=html_content,
            content_bytes=html_content.encode("utf-8"),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.05,
            content_type="text/html",
        )
    )
    strategy_8 = OfficialSponsorWebStrategy(
        http_client=mock_client_8,
        universe=registry,
    )
    obs_8 = strategy_8.inspect("TEST_SCHED_FUND", w_start, w_end)
    assert obs_8.window_fully_covered is True
    res_8 = detect_distribution(
        fund_id="TEST_SCHED_FUND",
        window_start=w_start,
        window_end=w_end,
        strategies=[strategy_8],
    )
    assert res_8.status == DetectionStatus.NOT_DECLARED


def test_detection_report_counts_latest_window_and_all_executions(tmp_path) -> None:
    from src.database.connection import get_engine, init_db, session_scope
    from src.database.models import DetectionRun, FundMaster
    from src.reports.detection_report import build_report

    db_path = tmp_path / "test_report.db"
    db_url = f"sqlite:///{db_path}"
    engine = get_engine(db_url)
    init_db(engine)

    with session_scope(engine) as s:
        fund = FundMaster(
            fund_id="TEST_F1",
            fund_name="Test Fund 1",
            fund_family="Vanguard",
            country="US",
            fund_type="ETF",
        )
        s.add(fund)
        s.flush()

        # Older run: UNKNOWN
        run1 = DetectionRun(
            check_id="chk_1",
            run_id="run_old",
            fund_id="TEST_F1",
            window_start=date(2026, 1, 1),
            window_end=date(2026, 1, 31),
            status="UNKNOWN",
            unknown_reason="TIMEOUT",
            evidence_json="[]",
            http_requests=1,
            bytes_downloaded=100,
            duration_seconds=1.0,
            checked_at=datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc),
        )
        # Newer run: DECLARED
        run2 = DetectionRun(
            check_id="chk_2",
            run_id="run_new",
            fund_id="TEST_F1",
            window_start=date(2026, 1, 1),
            window_end=date(2026, 1, 31),
            status="DECLARED",
            route_taken="HTML_TABLE",
            evidence_json="[]",
            http_requests=2,
            bytes_downloaded=500,
            duration_seconds=1.5,
            checked_at=datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc),
        )
        s.add_all([run1, run2])

    rep = build_report(db_url)
    assert rep["checks"] == 1
    assert rep["total_check_executions"] == 2
    assert rep["status_counts"] == {"DECLARED": 1}
    assert rep["hit_rate_pct"] == 100.0
    assert rep["avg_http_requests_per_check"] == 1.5


def test_learn_frequency_offline() -> None:
    from src.sweep_scheduler import learn_frequency

    # 1. 12 monthly dates -> MONTHLY
    monthly_dates = [date(2025, m, 15) for m in range(1, 13)]
    assert learn_frequency(monthly_dates) == "MONTHLY"

    # 2. 8 quarterly dates -> QUARTERLY
    quarterly_dates = [
        date(2024, 3, 15),
        date(2024, 6, 15),
        date(2024, 9, 15),
        date(2024, 12, 15),
        date(2025, 3, 15),
        date(2025, 6, 15),
        date(2025, 9, 15),
        date(2025, 12, 15),
    ]
    assert learn_frequency(quarterly_dates) == "QUARTERLY"

    # 3. 3 dates (< 4 distinct months) -> None
    three_dates = [date(2025, 1, 15), date(2025, 2, 15), date(2025, 3, 15)]
    assert learn_frequency(three_dates) is None

    # 4. Monthly + extra December capital gain date -> MONTHLY
    monthly_plus_dec = monthly_dates + [date(2025, 12, 28)]
    assert learn_frequency(monthly_plus_dec) == "MONTHLY"


def test_decide_uses_learned_frequency_from_state_and_configured_fallback() -> None:
    from src.database.models import FundDetectionState
    from src.sweep_scheduler import SweepConfig, decide
    from src.universe_loader import UniverseFund

    fund = UniverseFund(
        fund_id="TEST_DECIDE_FUND",
        country="US",
        fund_name="Test Decide Fund",
        fund_type="ETF",
        fund_family="TestFamily",
        official_source_url="https://sponsor.example.com/fund",
        expected_frequency="QUARTERLY",  # configured
        is_monthly_payer=False,
    )

    today = date(2026, 9, 15)
    cfg = SweepConfig(gap_multiplier=1.5)

    # Case 1: State has learned expected_frequency="MONTHLY"
    # Gap is 50 days (> 1.5 * 30 = 45 days for MONTHLY, but <= 1.5 * 91 = 136.5 for QUARTERLY)
    state_learned = FundDetectionState(
        fund_id="TEST_DECIDE_FUND",
        expected_frequency="MONTHLY",
        consecutive_unknowns=0,
        last_confirmed_event_date=today - timedelta(days=50),
        last_checked_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
        backfill_completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    decision_learned = decide(fund, state_learned, today, cfg)
    assert decision_learned.mode == "LOOKBACK"
    assert any("(learned)" in r for r in decision_learned.reasons)
    assert any("MONTHLY" in r for r in decision_learned.reasons)

    # Case 2: State has no learned frequency (or empty) -> fallback to configured QUARTERLY
    # Gap is 50 days (not late for QUARTERLY 91 days: 50 <= 136.5) -> ROUTINE
    state_configured = FundDetectionState(
        fund_id="TEST_DECIDE_FUND",
        expected_frequency="",  # empty / fallback
        consecutive_unknowns=0,
        last_confirmed_event_date=today - timedelta(days=50),
        last_checked_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
        backfill_completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    decision_configured = decide(fund, state_configured, today, cfg)
    assert decision_configured.mode == "ROUTINE"

    # Case 3: When late with configured frequency -> reason mentions configured (confirmed by history or not enough history)
    state_configured_late = FundDetectionState(
        fund_id="TEST_DECIDE_FUND",
        expected_frequency="",
        consecutive_unknowns=0,
        last_confirmed_event_date=today - timedelta(days=140),
        last_checked_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
        backfill_completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    decision_configured_late = decide(fund, state_configured_late, today, cfg)
    assert decision_configured_late.mode == "LOOKBACK"
    assert any(
        "configured (confirmed by history or not enough history)" in r
        for r in decision_configured_late.reasons
    )
    assert any("QUARTERLY" in r for r in decision_configured_late.reasons)

    # Case 4: State expected_frequency equals configured frequency -> not labeled as "learned"
    state_same = FundDetectionState(
        fund_id="TEST_DECIDE_FUND",
        expected_frequency="QUARTERLY",
        consecutive_unknowns=0,
        last_confirmed_event_date=today - timedelta(days=140),
        last_checked_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
        backfill_completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    decision_same = decide(fund, state_same, today, cfg)
    assert decision_same.mode == "LOOKBACK"
    assert any(
        "configured (confirmed by history or not enough history)" in r
        for r in decision_same.reasons
    )


def test_vanguard_canada_th_scope_row_table_extraction() -> None:
    from src.parsers.html_table_parser import parse_html_distribution_tables

    html = """
    <table>
      <thead>
        <tr>
          <th>Type</th>
          <th>Ex-dividend date</th>
          <th>Record date</th>
          <th>Payment date</th>
          <th>Cash distribution per unit</th>
          <th>Reinvestment distribution per unit</th>
          <th>Total distribution per unit</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <th scope="row">Income</th>
          <td>Sep 18 2026</td>
          <td>Sep 18 2026</td>
          <td>Sep 25 2026</td>
          <td>$0.18622</td>
          <td>—</td>
          <td>$0.18622</td>
        </tr>
        <tr>
          <th scope="row">Income</th>
          <td>Aug 21 2026</td>
          <td>Aug 21 2026</td>
          <td>Aug 28 2026</td>
          <td>$0.21121</td>
          <td>—</td>
          <td>$0.21121</td>
        </tr>
        <tr>
          <th scope="row">Income</th>
          <td>Jul 17 2026</td>
          <td>Jul 17 2026</td>
          <td>Jul 24 2026</td>
          <td>$0.17681</td>
          <td>—</td>
          <td>$0.17681</td>
        </tr>
        <tr>
          <th scope="row">CGCA</th>
          <td>Dec 30 2025</td>
          <td>Dec 30 2025</td>
          <td>Jan 07 2026</td>
          <td>—</td>
          <td>$0.76462</td>
          <td>$0.76462</td>
        </tr>
      </tbody>
    </table>
    """

    events = parse_html_distribution_tables(
        html,
        fund_id="CA_VANGUARD_VDY",
        country="CA",
        source_url="https://www.vanguard.ca/en/investor/products/products-group/etfs/VDY",
        ticker="VDY",
    )

    assert len(events) == 4
    income_events = [e for e in events if e.distribution_type == "Income"]
    cgca_events = [e for e in events if e.distribution_type == "CGCA"]

    assert len(income_events) == 3
    assert [e.gross_amount for e in income_events] == [0.18622, 0.21121, 0.17681]
    assert [e.ex_date for e in income_events] == [
        date(2026, 9, 18),
        date(2026, 8, 21),
        date(2026, 7, 17),
    ]

    assert len(cgca_events) == 1
    assert cgca_events[0].gross_amount == 0.76462
    assert cgca_events[0].ex_date == date(2025, 12, 30)


def test_rbc_fund_data_parser_extraction() -> None:
    from src.parsers.rbc_fund_data_parser import parse_rbc_fund_data

    html = """
    <!DOCTYPE html>
    <html>
      <head><title>RCD ETF</title></head>
      <body>
        <script>
          const fundData = {
            "ticker": "RCD",
            "fundName": "RBC Canadian Discount Bond ETF",
            "distributions": {
              "2025": {
                "details": [
                  {
                    "cashDistr": 0.095,
                    "recordDate": "2025-11-21",
                    "totalDistr": 0.095,
                    "exDivDate": "2025-11-21",
                    "reInvDistr": null,
                    "payDate": "2025-11-28"
                  },
                  {
                    "cashDistr": 0.095,
                    "recordDate": "2025-12-30",
                    "totalDistr": 3.488,
                    "exDivDate": "2025-12-30",
                    "reInvDistr": 3.393,
                    "payDate": "2026-01-05"
                  }
                ]
              }
            }
          };
        </script>
      </body>
    </html>
    """

    # 1. Successful parse with normal month and reinvested December row
    events = parse_rbc_fund_data(
        html,
        fund_id="CA_RBC_RCD",
        ticker="RCD",
        source_url="https://www.rbcgam.com/en/ca/products/etfs/RCD/detail",
    )
    assert len(events) == 2

    # Normal month
    nov = events[0]
    assert nov.ex_date == date(2025, 11, 21)
    assert nov.record_date == date(2025, 11, 21)
    assert nov.payable_date == date(2025, 11, 28)
    assert nov.gross_amount == 0.095
    assert nov.currency == "CAD"
    assert nov.extraction_route == ExtractionRoute.API

    # December row: total 3.488 with reinvested note
    dec = events[1]
    assert dec.ex_date == date(2025, 12, 30)
    assert dec.record_date == date(2025, 12, 30)
    assert dec.payable_date == date(2026, 1, 5)
    assert dec.gross_amount == 3.488
    assert "reinvested (non-cash)" in dec.validation_notes

    # 2. Ticker mismatch -> returns []
    mismatch_events = parse_rbc_fund_data(
        html,
        fund_id="CA_RBC_OTHER",
        ticker="OTHER",
        source_url="https://www.rbcgam.com/en/ca/products/etfs/OTHER/detail",
    )
    assert mismatch_events == []
