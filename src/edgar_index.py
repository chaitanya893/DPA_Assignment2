"""EDGAR daily form-index poller (PDF Phase 1, detection strategy 3: filing index polling).

Downloads ``https://www.sec.gov/Archives/edgar/daily-index/{YYYY}/QTR{q}/form.{YYYYMMDD}.idx``
and returns the distribution-relevant filings made by CIKs in the fund universe. It is a
cheap, structured trigger: a hit means "sweep this fund now", never "distribution declared".
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta

from src.http_client import HTTPClient

logger = logging.getLogger(__name__)

# Form types that can carry distribution information for registered funds.
DISTRIBUTION_FORMS = frozenset(
    {
        "497",
        "497K",
        "497J",
        "N-CSR",
        "N-CSRS",
        "N-CEN",
        "NPORT-P",
        "485BPOS",
        "8-K",
        "N-30D",
    }
)

_ROW_RE = re.compile(
    r"^(?P<form>\S+(?: \S+)*?)\s{2,}(?P<company>.+?)\s{2,}(?P<cik>\d{1,10})\s{2,}"
    r"(?P<filed>\d{8}|\d{4}-\d{2}-\d{2})\s{2,}(?P<file>\S+)\s*$"
)


@dataclass(frozen=True)
class IndexFiling:
    form: str
    company: str
    cik: str
    filed: date
    url: str


def daily_index_url(day: date) -> str:
    q = (day.month - 1) // 3 + 1
    return f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx"


def parse_form_index(text: str) -> list[IndexFiling]:
    """Parse a form.YYYYMMDD.idx file into filings."""
    filings: list[IndexFiling] = []
    started = False
    for line in text.splitlines():
        if not started:
            if line.startswith("-----"):
                started = True
            continue
        m = _ROW_RE.match(line.rstrip())
        if not m:
            continue
        raw = m.group("filed").replace("-", "")
        try:
            filed = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        except ValueError:
            continue
        filings.append(
            IndexFiling(
                form=m.group("form").strip(),
                company=m.group("company").strip(),
                cik=m.group("cik").lstrip("0") or "0",
                filed=filed,
                url=f"https://www.sec.gov/Archives/{m.group('file')}",
            )
        )
    return filings


class EdgarDailyIndexPoller:
    """Poll EDGAR daily indexes for distribution-relevant filings by universe CIKs."""

    def __init__(
        self,
        http_client: HTTPClient | None = None,
        forms: frozenset[str] = DISTRIBUTION_FORMS,
    ) -> None:
        self.http_client = http_client or HTTPClient()
        self.forms = forms

    def poll_day(self, day: date, ciks: set[str]) -> tuple[list[IndexFiling], bool]:
        """Return (matching filings, index_available). Weekends/holidays have no index (404)."""
        if day.weekday() >= 5:
            return [], False
        rec = self.http_client.get(daily_index_url(day))
        if not rec.is_success or not rec.content_text:
            if rec.status_code not in (403, 404):
                logger.warning(
                    "EDGAR daily index unavailable for %s: %s", day, rec.error_message
                )
            return [], False
        wanted = {c.lstrip("0") for c in ciks}
        hits = [
            f
            for f in parse_form_index(rec.content_text)
            if f.cik in wanted and f.form.upper() in self.forms
        ]
        return hits, True

    def poll_range(
        self, start: date, end: date, ciks: set[str]
    ) -> dict[str, list[IndexFiling]]:
        """Map CIK -> filings between start and end (inclusive)."""
        out: dict[str, list[IndexFiling]] = {}
        day = start
        while day <= end:
            hits, _ = self.poll_day(day, ciks)
            for f in hits:
                out.setdefault(f.cik, []).append(f)
            day += timedelta(days=1)
        return out
