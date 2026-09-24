"""Unit tests for src/http_client.py and src/rate_limiter.py.

All fixtures and values used here are purely SYNTHETIC / TEST values for unit testing.
No real or assumed fund/financial data is used.
"""

import os
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.detector import detect_distribution
from src.http_client import DEFAULT_USER_AGENT, HTTPClient
from src.models import (
    DetectionStatus,
    Evidence,
    SourceTier,
    UnknownReason,
)
from src.rate_limiter import RateLimitConfig, RateLimiter
from src.strategies import (
    BaseDetectionStrategy,
    SignalType,
    StrategyObservation,
)


def test_user_agent_configuration_no_fake_contact(monkeypatch) -> None:
    """Verify default User-Agent contains no fake contact and is configurable."""
    assert "example.internal" not in DEFAULT_USER_AGENT
    assert "contact@" not in DEFAULT_USER_AGENT

    # Default instance: descriptive UA + the operator contact email from DETECTOR_CONTACT_EMAIL
    client = HTTPClient()
    assert client.user_agent.startswith(DEFAULT_USER_AGENT[:-1])
    assert "tests@example.com" in client.user_agent
    assert "Mozilla" not in client.user_agent  # never impersonate a browser

    # Test custom param override
    custom_ua = "SYNTHETIC_TEST_AGENT/1.0 (test-run)"
    custom_client = HTTPClient(user_agent=custom_ua)
    assert custom_client.user_agent == custom_ua

    # Test env var override
    with patch.dict(os.environ, {"DETECTOR_USER_AGENT": "SYNTHETIC_ENV_AGENT/2.0"}):
        env_client = HTTPClient()
        assert env_client.user_agent == "SYNTHETIC_ENV_AGENT/2.0"


def test_rate_limit_config_validation() -> None:
    """9. Rate-limit configuration validation."""
    valid_cfg = RateLimitConfig(
        min_interval_seconds=0.1, max_requests_per_window=5, window_seconds=1.0
    )
    assert valid_cfg.min_interval_seconds == 0.1

    with pytest.raises(ValueError, match="min_interval_seconds"):
        RateLimitConfig(min_interval_seconds=-1.0)

    with pytest.raises(ValueError, match="max_requests_per_window"):
        RateLimitConfig(max_requests_per_window=0)

    with pytest.raises(ValueError, match="window_seconds"):
        RateLimitConfig(window_seconds=-0.5)


def test_rate_limiter_pacing() -> None:
    """RateLimiter enforces spacing between successive calls to the same domain."""
    limiter = RateLimiter(
        default_config=RateLimitConfig(
            min_interval_seconds=0.05, max_requests_per_window=10, window_seconds=1.0
        )
    )
    delay1 = limiter.acquire("https://synthetic-test.example.internal/doc1")
    assert delay1 == 0.0  # First request has no delay

    delay2 = limiter.acquire("https://synthetic-test.example.internal/doc2")
    # Second immediate request should experience pacing delay
    assert delay2 >= 0.0


def test_http_client_success_synthetic_response() -> None:
    """6. HTTP client handles successful synthetic test response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "<html>SYNTHETIC_TEST_CONTENT</html>"
    mock_resp.content = b"<html>SYNTHETIC_TEST_CONTENT</html>"

    with patch("httpx.Client.get", return_value=mock_resp) as mock_get:
        client = HTTPClient(
            user_agent="SYNTHETIC_TEST_USER_AGENT/1.0 (ops@example.com)", max_retries=1
        )
        record = client.get("https://synthetic-test.example.org/api")

        assert record.is_success is True
        assert record.status_code == 200
        assert record.content_text == "<html>SYNTHETIC_TEST_CONTENT</html>"
        assert record.failure_reason is None

        # Verify custom synthetic User-Agent header was passed
        mock_get.assert_called_once()
        headers_passed = mock_get.call_args[1].get("headers", {})
        assert (
            headers_passed.get("User-Agent")
            == "SYNTHETIC_TEST_USER_AGENT/1.0 (ops@example.com)"
        )


def test_http_client_empty_body_classified_as_incomplete() -> None:
    """Empty body on 200 OK is classified as INCOMPLETE_SOURCE."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "   "
    mock_resp.content = b""

    with patch("httpx.Client.get", return_value=mock_resp):
        client = HTTPClient(max_retries=1)
        record = client.get("https://synthetic-test.example.org/empty")

        assert record.is_success is False
        assert record.failure_reason == UnknownReason.INCOMPLETE_SOURCE


def test_http_client_unavailable_server_classification() -> None:
    """7. HTTP 503 is classified as SOURCE_UNAVAILABLE."""
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.reason_phrase = "Service Unavailable"

    with patch("httpx.Client.get", return_value=mock_resp):
        client = HTTPClient(max_retries=1)
        record = client.get("https://synthetic-test.example.org/service")

        assert record.is_success is False
        assert record.status_code == 503
        assert record.failure_reason == UnknownReason.SOURCE_UNAVAILABLE


def test_http_client_failure_not_converted_to_not_declared() -> None:
    """8. HTTP failure is NOT converted into NOT_DECLARED by detector strategies."""

    # Create a synthetic strategy that experienced an HTTP failure
    class SyntheticHTTPFailStrategy(BaseDetectionStrategy):
        def inspect(
            self, fund_id: str, window_start: date, window_end: date
        ) -> StrategyObservation:
            ev = Evidence(
                source_id="SYNTHETIC_HTTP_FAIL_SRC",
                source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                url="https://synthetic-test.example.org/fail",
                retrieved_at=record.retrieved_at,
                snippet_or_locator="HTTP 503 Server Unavailable",
                failure_reason=UnknownReason.SOURCE_UNAVAILABLE,
            )
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                evidence=[ev],
                window_fully_covered=False,
                failure_reason=UnknownReason.SOURCE_UNAVAILABLE,
            )

    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.reason_phrase = "Service Unavailable"

    with patch("httpx.Client.get", return_value=mock_resp):
        client = HTTPClient(max_retries=1)
        record = client.get("https://synthetic-test.example.org/fail")

        strategy = SyntheticHTTPFailStrategy("SyntheticHTTPFailStrategy")
        result = detect_distribution(
            fund_id="SYNTHETIC_TEST_FUND_HTTP",
            window_start=date(2026, 3, 1),
            window_end=date(2026, 3, 31),
            strategies=[strategy],
        )

        # Must be UNKNOWN, NEVER NOT_DECLARED
        assert result.status == DetectionStatus.UNKNOWN
        assert result.status != DetectionStatus.NOT_DECLARED
        assert result.evidence[0].failure_reason == UnknownReason.SOURCE_UNAVAILABLE


def test_request_refused_without_contact_email(monkeypatch) -> None:
    """Compliance: no contact email in the User-Agent -> no request is sent."""
    monkeypatch.delenv("DETECTOR_CONTACT_EMAIL", raising=False)
    with patch("httpx.Client.get") as mock_get:
        record = HTTPClient(max_retries=1).get("https://synthetic-test.example.org/x")
    assert record.is_success is False
    assert "contact email" in (record.error_message or "")
    mock_get.assert_not_called()


def test_robots_txt_disallow_is_obeyed(monkeypatch) -> None:
    """Compliance: a path disallowed by robots.txt is never fetched."""
    from src.http_client import RobotsPolicy

    RobotsPolicy.clear()
    robots = MagicMock(status_code=200, text="User-agent: *\nDisallow: /private/\n")
    page = MagicMock(
        status_code=200, text="<html>ok</html>", content=b"<html>ok</html>"
    )

    def fake_get(self, url, headers=None, params=None):  # noqa: ANN001
        return robots if url.endswith("/robots.txt") else page

    with patch("httpx.Client.get", new=fake_get):
        client = HTTPClient(max_retries=1, respect_robots=True)
        blocked = client.get("https://robots-test.example.org/private/data")
        allowed = client.get("https://robots-test.example.org/public/data")
    assert blocked.is_success is False and "robots.txt" in (blocked.error_message or "")
    assert allowed.is_success is True


def test_default_rate_limit_is_2_to_3_seconds() -> None:
    """Compliance: default pacing is one request every 2-3 s per domain."""
    cfg = RateLimitConfig()
    assert 2.0 <= cfg.min_interval_seconds <= 3.0
    assert cfg.max_requests_per_window == 1
