"""Offline test doubles. Every page served here is SYNTHETIC test content, not real fund data."""

from __future__ import annotations

from datetime import datetime, timezone

from src.http_client import HTTPClient, HTTPResponseRecord
from src.models import UnknownReason


class StubHTTPClient(HTTPClient):
    """Serves fixed pages by URL (exact match or substring key); everything else is a 404."""

    def __init__(
        self,
        pages: dict[str, str | bytes] | None = None,
        content_types: dict[str, str] | None = None,
    ):
        super().__init__(user_agent="TestAgent/1.0 (tests@example.com)")
        self.pages = pages or {}
        self.content_types = content_types or {}
        self.calls: list[str] = []

    def _lookup(self, url: str) -> tuple[str | bytes | None, str | None]:
        if url in self.pages:
            return self.pages[url], self.content_types.get(url)
        for key, body in self.pages.items():
            if key in url:
                return body, self.content_types.get(key)
        return None, None

    def get(self, url, headers=None, params=None):  # noqa: ANN001
        self.calls.append(url)
        body, ctype = self._lookup(url)
        now = datetime.now(timezone.utc)
        if body is None:
            return HTTPResponseRecord(
                url,
                404,
                now,
                None,
                None,
                UnknownReason.INSUFFICIENT_EVIDENCE,
                False,
                0.0,
                "HTTP 404",
            )
        raw = body if isinstance(body, bytes) else body.encode()
        text = body if isinstance(body, str) else ""
        return HTTPResponseRecord(
            url, 200, now, text, raw, None, True, 0.01, None, ctype
        )
