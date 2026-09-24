"""Compliant HTTP client: descriptive User-Agent with contact email, robots.txt, pacing,
exponential backoff with jitter, hard timeout, and deterministic failure classification.

Compliance rules implemented (PDF "Compliance rules" + "Standards"):
- One descriptive User-Agent for every site, containing the operator's contact email.
  Configure it with DETECTOR_CONTACT_EMAIL (or a full DETECTOR_USER_AGENT). Requests are
  refused while no contact email is configured; the client never pretends to be a browser.
- robots.txt is fetched once per host and obeyed (RFC 9309: 4xx = no rules, 5xx or
  unreachable = treat as disallowed). A blocked URL is logged and returned as a failure,
  never worked around.
- One request every 2.5 s per domain by default, shared by every client in the process
  (DETECTOR_MIN_INTERVAL_SECONDS overrides it).
- Every call is retried with exponential backoff + jitter and has a hard timeout.
"""

from __future__ import annotations

import logging
import os
import random
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from src.models import UnknownReason
from src.rate_limiter import RateLimiter, shared_rate_limiter

logger = logging.getLogger(__name__)

PROJECT_URL = "https://github.com/chaitanya893/DPA_Assignment2"
DEFAULT_USER_AGENT = f"FundDistributionDetector/1.0 (+{PROJECT_URL})"
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def build_user_agent() -> str:
    """User-Agent from DETECTOR_USER_AGENT, else DEFAULT_USER_AGENT + DETECTOR_CONTACT_EMAIL."""
    explicit = os.getenv("DETECTOR_USER_AGENT")
    if explicit:
        return explicit
    email = os.getenv("DETECTOR_CONTACT_EMAIL", "").strip()
    if email:
        return f"FundDistributionDetector/1.0 (+{PROJECT_URL}; {email})"
    return DEFAULT_USER_AGENT


def _env_flag(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class HTTPResponseRecord:
    """Standardized response: outcome, payload and audit metadata."""

    url: str
    status_code: int | None
    retrieved_at: datetime
    content_text: str | None
    content_bytes: bytes | None
    failure_reason: UnknownReason | None
    is_success: bool
    elapsed_seconds: float
    error_message: str | None = None
    content_type: str | None = None


class RobotsPolicy:
    """Per-host robots.txt cache shared by all clients in the process."""

    _lock = threading.Lock()
    _cache: dict[str, RobotFileParser | None] = {}

    @classmethod
    def clear(cls) -> None:
        with cls._lock:
            cls._cache.clear()

    @classmethod
    def allowed(
        cls, url: str, user_agent: str, timeout: float, limiter: RateLimiter
    ) -> bool:
        parsed = urlparse(url)
        host = f"{parsed.scheme}://{parsed.netloc}"
        with cls._lock:
            cached = cls._cache.get(host, "missing")
        if cached == "missing":
            rp: RobotFileParser | None = RobotFileParser()
            robots_url = f"{host}/robots.txt"
            try:
                limiter.acquire(robots_url)
                with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                    resp = client.get(robots_url, headers={"User-Agent": user_agent})
                if 200 <= resp.status_code < 300:
                    rp.parse(resp.text.splitlines())
                elif 400 <= resp.status_code < 500:
                    rp.parse([])  # no robots.txt -> no restrictions
                else:
                    rp = None  # server error -> treat whole host as disallowed
            except httpx.HTTPError as err:
                logger.warning(
                    "robots.txt unreachable for %s: %s (treating host as disallowed)",
                    host,
                    err,
                )
                rp = None
            with cls._lock:
                cls._cache[host] = rp
            cached = rp
        if cached is None:
            return False
        return cached.can_fetch(user_agent, url)


class HTTPClient:
    """HTTP client with robots.txt, shared per-domain pacing, retries and failure classification."""

    def __init__(
        self,
        user_agent: str | None = None,
        timeout_seconds: float = 15.0,
        max_retries: int = 3,
        backoff_factor: float = 1.5,
        rate_limiter: RateLimiter | None = None,
        respect_robots: bool | None = None,
    ) -> None:
        self.user_agent = user_agent or build_user_agent()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.rate_limiter = rate_limiter or shared_rate_limiter()
        self.respect_robots = (
            _env_flag("DETECTOR_RESPECT_ROBOTS", True)
            if respect_robots is None
            else respect_robots
        )
        self.require_contact = _env_flag("DETECTOR_REQUIRE_CONTACT_EMAIL", True)

    def _classify_http_status(
        self, status_code: int
    ) -> tuple[bool, UnknownReason | None]:
        if 200 <= status_code < 300:
            return True, None
        if status_code in {502, 503, 504}:
            return False, UnknownReason.SOURCE_UNAVAILABLE
        if status_code in {404, 410}:
            return False, UnknownReason.INSUFFICIENT_EVIDENCE
        return False, UnknownReason.RETRIEVAL_FAILED

    def _refused(self, url: str, message: str) -> HTTPResponseRecord:
        logger.warning("Request refused for %s: %s", url, message)
        return HTTPResponseRecord(
            url=url,
            status_code=None,
            retrieved_at=datetime.now(timezone.utc),
            content_text=None,
            content_bytes=None,
            failure_reason=UnknownReason.RETRIEVAL_FAILED,
            is_success=False,
            elapsed_seconds=0.0,
            error_message=message,
        )

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> HTTPResponseRecord:
        """GET with robots.txt check, pacing, retries and structured error recording."""
        if self.require_contact and not _EMAIL_RE.search(self.user_agent):
            return self._refused(
                url,
                "No contact email in User-Agent. Set DETECTOR_CONTACT_EMAIL (compliance rule: "
                "descriptive User-Agent that includes a contact email).",
            )
        if self.respect_robots and not RobotsPolicy.allowed(
            url, self.user_agent, self.timeout_seconds, self.rate_limiter
        ):
            return self._refused(
                url, "Blocked by robots.txt (logged, not worked around)."
            )

        req_headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/json,application/pdf,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
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
                with httpx.Client(
                    timeout=self.timeout_seconds, follow_redirects=True
                ) as client:
                    resp = client.get(url, headers=req_headers, params=params)
                last_status_code = resp.status_code
                elapsed = time.monotonic() - start_time
                is_success, failure_reason = self._classify_http_status(
                    resp.status_code
                )
                content_type = None
                try:
                    content_type = resp.headers.get("content-type")
                    content_type = (
                        content_type if isinstance(content_type, str) else None
                    )
                except AttributeError:
                    content_type = None
                if is_success:
                    body_bytes = (
                        resp.content
                        if isinstance(resp.content, (bytes, bytearray))
                        else b""
                    )
                    text_body = resp.text if isinstance(resp.text, str) else ""
                    if not text_body.strip() and not body_bytes:
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
                            content_type=content_type,
                        )
                    if (
                        not text_body.strip()
                        and body_bytes
                        and not body_bytes.startswith(b"%PDF-")
                    ):
                        return HTTPResponseRecord(
                            url=url,
                            status_code=resp.status_code,
                            retrieved_at=retrieved_at,
                            content_text="",
                            content_bytes=bytes(body_bytes),
                            failure_reason=UnknownReason.INCOMPLETE_SOURCE,
                            is_success=False,
                            elapsed_seconds=elapsed,
                            error_message="HTTP 200 OK returned empty response body.",
                            content_type=content_type,
                        )
                    return HTTPResponseRecord(
                        url=url,
                        status_code=resp.status_code,
                        retrieved_at=retrieved_at,
                        content_text=text_body,
                        content_bytes=bytes(body_bytes),
                        failure_reason=None,
                        is_success=True,
                        elapsed_seconds=elapsed,
                        content_type=content_type,
                    )

                last_failure_reason = failure_reason
                last_error_msg = (
                    f"HTTP {resp.status_code}: {getattr(resp, 'reason_phrase', '')}"
                )
                if (
                    resp.status_code in {429, 500, 502, 503, 504}
                    and attempt < self.max_retries
                ):
                    sleep_time = self.backoff_factor**attempt + random.uniform(0, 0.5)
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
                    content_type=content_type,
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
                    "HTTP error on %s (attempt %d/%d)", url, attempt, self.max_retries
                )

            if attempt < self.max_retries:
                time.sleep(self.backoff_factor**attempt + random.uniform(0, 0.5))

        return HTTPResponseRecord(
            url=url,
            status_code=last_status_code,
            retrieved_at=retrieved_at,
            content_text=None,
            content_bytes=None,
            failure_reason=last_failure_reason or UnknownReason.RETRIEVAL_FAILED,
            is_success=False,
            elapsed_seconds=time.monotonic() - start_time,
            error_message=last_error_msg,
        )


class RecordingHTTPClient(HTTPClient):
    """HTTPClient that remembers every response of a run.

    - Each URL is fetched at most once per run (later calls reuse the stored response), which
      keeps the crawl polite and guarantees that Layer B extracts from exactly the bytes Layer A
      saw.
    - ``records`` is persisted by the pipeline into raw_document and crawl_log, so every stored
      number traces to the stored source document.
    """

    def __init__(self, inner: HTTPClient | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.inner = inner
        self.records: list[HTTPResponseRecord] = []
        self._by_url: dict[str, HTTPResponseRecord] = {}

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> HTTPResponseRecord:
        key = url if not params else f"{url}?{sorted(params.items())}"
        if key in self._by_url:
            return self._by_url[key]
        rec = (
            self.inner.get(url, headers=headers, params=params)
            if self.inner
            else super().get(url, headers, params)
        )
        self._by_url[key] = rec
        self.records.append(rec)
        return rec

    def response_for(self, url: str) -> HTTPResponseRecord | None:
        return self._by_url.get(url)

    def drain(self) -> list[HTTPResponseRecord]:
        """Return and forget records not yet persisted (keeps the URL cache)."""
        out, self.records = self.records, []
        return out
