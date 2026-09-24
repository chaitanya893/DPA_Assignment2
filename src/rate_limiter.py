"""Configurable domain rate limiter for polite, controlled network access.

Maintains per-domain timestamps to enforce configurable minimum intervals
and requests-per-window limits without hardcoding unverified external claims.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Configuration for a specific domain's rate limits."""

    # PDF compliance rule: one request every 2 to 3 seconds per domain.
    min_interval_seconds: float = 2.5
    max_requests_per_window: int = 1
    window_seconds: float = 2.5

    def __post_init__(self) -> None:
        if self.min_interval_seconds < 0:
            raise ValueError("min_interval_seconds cannot be negative.")
        if self.max_requests_per_window <= 0:
            raise ValueError("max_requests_per_window must be greater than zero.")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be greater than zero.")


class RateLimiter:
    """Thread-safe rate limiter managing per-domain request pacing."""

    def __init__(
        self,
        default_config: RateLimitConfig | None = None,
        domain_configs: dict[str, RateLimitConfig] | None = None,
    ) -> None:
        self.default_config = default_config or RateLimitConfig()
        self.domain_configs = domain_configs or {}
        self._last_request_time: dict[str, float] = {}
        self._request_history: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def get_domain(self, url_or_domain: str) -> str:
        """Extract netloc domain from URL or return string as is."""
        if "://" in url_or_domain:
            parsed = urlparse(url_or_domain)
            return parsed.netloc.lower()
        return url_or_domain.lower()

    def get_config(self, domain: str) -> RateLimitConfig:
        """Get the specific RateLimitConfig for a domain or fallback to default."""
        return self.domain_configs.get(domain, self.default_config)

    def acquire(self, url_or_domain: str) -> float:
        """Wait if necessary to respect rate limits for the given domain.

        Returns:
            The number of seconds waited (0.0 if no delay was required).
        """
        domain = self.get_domain(url_or_domain)
        config = self.get_config(domain)

        with self._lock:
            now = time.monotonic()

            # 1. Clean history outside window
            history = self._request_history.setdefault(domain, [])
            cutoff = now - config.window_seconds
            self._request_history[domain] = [t for t in history if t > cutoff]
            history = self._request_history[domain]

            # 2. Check window capacity
            delay = 0.0
            if len(history) >= config.max_requests_per_window:
                oldest_in_window = history[0]
                delay = max(delay, (oldest_in_window + config.window_seconds) - now)

            # 3. Check min interval spacing
            last_time = self._last_request_time.get(domain, 0.0)
            elapsed_since_last = now - last_time
            if elapsed_since_last < config.min_interval_seconds:
                delay = max(delay, config.min_interval_seconds - elapsed_since_last)

            if delay > 0.0:
                logger.debug(
                    "Rate limiter delaying request to %s by %.3fs", domain, delay
                )
                time.sleep(delay)
                now = time.monotonic()

            self._last_request_time[domain] = now
            self._request_history[domain].append(now)
            return delay


_SHARED: RateLimiter | None = None
_SHARED_LOCK = threading.Lock()


def shared_rate_limiter() -> RateLimiter:
    """Process-wide limiter so every HTTPClient paces the same domain together.

    Default 2.5 s between requests to one domain. DETECTOR_MIN_INTERVAL_SECONDS overrides it
    (tests set 0; production should stay within the 2-3 s rule).
    """
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            import os

            interval = float(os.getenv("DETECTOR_MIN_INTERVAL_SECONDS", "2.5"))
            _SHARED = RateLimiter(
                default_config=RateLimitConfig(
                    min_interval_seconds=interval,
                    max_requests_per_window=1 if interval > 0 else 1_000_000,
                    window_seconds=interval if interval > 0 else 1.0,
                )
            )
        return _SHARED


def reset_shared_rate_limiter() -> None:
    global _SHARED
    with _SHARED_LOCK:
        _SHARED = None
