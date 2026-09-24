"""Shared test setup: no real network, no pacing delays, raw documents in a temp folder."""

from __future__ import annotations

import httpx
import pytest

from src.http_client import RobotsPolicy
from src.rate_limiter import reset_shared_rate_limiter


@pytest.fixture(autouse=True)
def _offline_test_environment(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("DETECTOR_CONTACT_EMAIL", "tests@example.com")
    monkeypatch.delenv("DETECTOR_USER_AGENT", raising=False)
    monkeypatch.setenv("DETECTOR_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("DETECTOR_RESPECT_ROBOTS", "0")
    monkeypatch.setenv("RAW_DOCUMENT_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("RAW_SOURCE_CACHE_DIR", str(tmp_path / "raw_sources"))
    reset_shared_rate_limiter()
    RobotsPolicy.clear()

    def _no_network(self, request):  # noqa: ANN001
        raise httpx.ConnectError(f"Network disabled in tests: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _no_network)
    yield
    reset_shared_rate_limiter()
    RobotsPolicy.clear()
