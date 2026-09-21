"""Resilient HTTP client foundation with bounded backoff, rate limiting, and failure classification.

Designed for Layer A detection without hardcoded credentials or unverified financial assumptions.

Note on User-Agent:
External authoritative sources (e.g. SEC EDGAR) require a compliant User-Agent header
with operator contact details. In production, this must be explicitly configured via the
DETECTOR_USER_AGENT environment variable or the user_agent constructor parameter.
Do not hardcode or fabricate contact emails.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from src.models import UnknownReason
from src.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Base descriptive User-Agent without fake contact info.
# Real production execution requires setting DETECTOR_USER_AGENT with valid operator details.
DEFAULT_USER_AGENT = (
    "FundDistributionDetector/1.0 (+https://github.com/chaitanya893/DPA_Assignment2)"
)


@dataclass(frozen=True)
class HTTPResponseRecord:
    """Standardized response representation capturing outcome, payload, and audit metadata."""

    url: str
    status_code: int | None
    retrieved_at: datetime
    content_text: str | None
    content_bytes: bytes | None
    failure_reason: UnknownReason | None
    is_success: bool
    elapsed_seconds: float
    error_message: str | None = None


class HTTPClient:
    """Robust HTTP client with exponential backoff, rate limiting, and deterministic failure classification."""

    def __init__(
        self,
        user_agent: str | None = None,
        timeout_seconds: float = 15.0,
        max_retries: int = 3,
        backoff_factor: float = 1.5,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.user_agent = user_agent or os.getenv(
            "DETECTOR_USER_AGENT", DEFAULT_USER_AGENT
        )
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.rate_limiter = rate_limiter or RateLimiter()

    def _classify_http_status(
        self, status_code: int
    ) -> tuple[bool, UnknownReason | None]:
        """Map HTTP status code to success status and UnknownReason."""
        if 200 <= status_code < 300:
            return True, None
        if status_code in {502, 503, 504}:
            return False, UnknownReason.SOURCE_UNAVAILABLE
        if status_code in {404, 410}:
            return False, UnknownReason.INSUFFICIENT_EVIDENCE
        if status_code in {401, 403}:
            return False, UnknownReason.RETRIEVAL_FAILED
        if status_code >= 500:
            return False, UnknownReason.RETRIEVAL_FAILED
        return False, UnknownReason.RETRIEVAL_FAILED

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> HTTPResponseRecord:
        """Execute a GET request with pacing, retry logic, and structured error recording."""
        req_headers = {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
        }
        if headers:
            req_headers.update(headers)

        last_error_msg: str | None = None
        last_failure_reason: UnknownReason | None = None
        last_status_code: int | None = None

        start_time = time.monotonic()
        retrieved_at = datetime.now(timezone.utc)

        for attempt in range(1, self.max_retries + 1):
            self.rate_limiter.acquire(url)
            retrieved_at = datetime.now(timezone.utc)

            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    resp = client.get(url, headers=req_headers, params=params)
                    last_status_code = resp.status_code
                    elapsed = time.monotonic() - start_time

                    is_success, failure_reason = self._classify_http_status(
                        resp.status_code
                    )
                    if is_success:
                        text_body = resp.text
                        if not text_body.strip():
                            # Empty body on 200 OK is an incomplete source
                            return HTTPResponseRecord(
                                url=url,
                                status_code=resp.status_code,
                                retrieved_at=retrieved_at,
                                content_text="",
                                content_bytes=b"",
                                failure_reason=UnknownReason.INCOMPLETE_SOURCE,
                                is_success=False,
                                elapsed_seconds=elapsed,
                                error_message="HTTP 200 OK returned empty response body.",
                            )

                        return HTTPResponseRecord(
                            url=url,
                            status_code=resp.status_code,
                            retrieved_at=retrieved_at,
                            content_text=text_body,
                            content_bytes=resp.content,
                            failure_reason=None,
                            is_success=True,
                            elapsed_seconds=elapsed,
                        )

                    # Non-2xx status: retry if server error / rate limit, else return
                    last_failure_reason = failure_reason
                    last_error_msg = f"HTTP {resp.status_code}: {resp.reason_phrase}"
                    if (
                        resp.status_code in {429, 500, 502, 503, 504}
                        and attempt < self.max_retries
                    ):
                        sleep_time = self.backoff_factor**attempt
                        logger.warning(
                            "HTTP %s for %s (attempt %d/%d). Retrying in %.2fs",
                            resp.status_code,
                            url,
                            attempt,
                            self.max_retries,
                            sleep_time,
                        )
                        time.sleep(sleep_time)
                        continue

                    return HTTPResponseRecord(
                        url=url,
                        status_code=last_status_code,
                        retrieved_at=retrieved_at,
                        content_text=None,
                        content_bytes=None,
                        failure_reason=last_failure_reason,
                        is_success=False,
                        elapsed_seconds=elapsed,
                        error_message=last_error_msg,
                    )

            except httpx.TimeoutException as err:
                last_error_msg = f"HTTP Timeout: {err!s}"
                last_failure_reason = UnknownReason.RETRIEVAL_FAILED
                logger.warning(
                    "Timeout connecting to %s (attempt %d/%d)",
                    url,
                    attempt,
                    self.max_retries,
                )
            except httpx.ConnectError as err:
                last_error_msg = f"HTTP Connection Error: {err!s}"
                last_failure_reason = UnknownReason.SOURCE_UNAVAILABLE
                logger.warning(
                    "Connection error to %s (attempt %d/%d)",
                    url,
                    attempt,
                    self.max_retries,
                )
            except httpx.HTTPError as err:
                last_error_msg = f"HTTP Error: {err!s}"
                last_failure_reason = UnknownReason.RETRIEVAL_FAILED
                logger.warning(
                    "HTTP generic error on %s (attempt %d/%d)",
                    url,
                    attempt,
                    self.max_retries,
                )

            if attempt < self.max_retries:
                sleep_time = self.backoff_factor**attempt
                time.sleep(sleep_time)

        elapsed = time.monotonic() - start_time
        return HTTPResponseRecord(
            url=url,
            status_code=last_status_code,
            retrieved_at=retrieved_at,
            content_text=None,
            content_bytes=None,
            failure_reason=last_failure_reason or UnknownReason.RETRIEVAL_FAILED,
            is_success=False,
            elapsed_seconds=elapsed,
            error_message=last_error_msg,
        )
