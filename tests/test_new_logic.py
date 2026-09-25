"""Unit tests for optional API URL support, safety buffer, RecordingHTTPClient caching, and RobotsPolicy.

All test fixtures use synthetic data and test doubles. No real network calls are made.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
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
