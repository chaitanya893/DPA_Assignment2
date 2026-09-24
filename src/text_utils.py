"""Shared, defensive text helpers for dates, amounts and distribution categories.

One implementation is used by every strategy and parser so that a date is read the
same way everywhere. Every helper returns ``None`` instead of raising or guessing:
an impossible date such as "February 30, 2024" is not a date, and a value that does
not look like money is not an amount.
"""

from __future__ import annotations

import re
from datetime import date

MONTHS: dict[str, int] = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

_MONTH_ALT = (
    r"January|February|March|April|May|June|July|August|September|October|"
    r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec"
)

_ISO_RE = re.compile(r"\b(20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b")
_TEXT_MDY_RE = re.compile(
    rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(20\d{{2}})\b",
    re.IGNORECASE,
)
_TEXT_DMY_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_ALT})\.?,?\s+(20\d{{2}})\b",
    re.IGNORECASE,
)
_US_SLASH_RE = re.compile(r"\b(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])/(20\d{2})\b")

_AMOUNT_RE = re.compile(
    r"(?<![\d/.-])(?:US\$|C\$|CA\$|\$)?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d*\.\d+)(?![\d/-])"
)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _iter_dates(text: str) -> list[tuple[int, date]]:
    """Return (position, date) for every valid date found in text."""
    found: list[tuple[int, date]] = []
    for m in _ISO_RE.finditer(text):
        d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            found.append((m.start(), d))
    for m in _TEXT_MDY_RE.finditer(text):
        month = MONTHS.get(m.group(1).lower().rstrip("."))
        if month:
            d = _safe_date(int(m.group(3)), month, int(m.group(2)))
            if d:
                found.append((m.start(), d))
    for m in _TEXT_DMY_RE.finditer(text):
        month = MONTHS.get(m.group(2).lower().rstrip("."))
        if month:
            d = _safe_date(int(m.group(3)), month, int(m.group(1)))
            if d:
                found.append((m.start(), d))
    for m in _US_SLASH_RE.finditer(text):
        d = _safe_date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        if d:
            found.append((m.start(), d))
    found.sort(key=lambda t: t[0])
    return found


def parse_date(text: str | None) -> date | None:
    """Return the first valid date in ``text``, or None. Never raises."""
    if not text:
        return None
    dates = _iter_dates(text)
    return dates[0][1] if dates else None


def find_all_dates(text: str | None) -> list[date]:
    """Return every valid date in ``text`` in reading order."""
    if not text:
        return []
    return [d for _, d in _iter_dates(text)]


def looks_like_date(text: str | None) -> bool:
    return parse_date(text) is not None


def parse_amount(text: str | None) -> float | None:
    """Parse a per-share money amount from a cell. Returns None for dates and non-numbers."""
    if not text:
        return None
    if looks_like_date(text):
        return None
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def clean_html_text(s: str) -> str:
    """Strip tags and collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def distribution_category(type_text: str | None) -> str:
    """Normalise a free-text distribution type into a stable category used in the natural key.

    Two different distributions paid on the same ex-date (for example December income and a
    long-term capital gain) must never collapse into one row, so the category is part of the key.
    """
    t = (type_text or "").lower()
    if "return of capital" in t or re.search(r"\broc\b", t):
        return "RETURN_OF_CAPITAL"
    if "capital gain" in t or "cap gain" in t or "capital gains" in t:
        if "long" in t:
            return "LONG_TERM_CAPITAL_GAIN"
        if "short" in t:
            return "SHORT_TERM_CAPITAL_GAIN"
        if "income" in t or "dividend" in t:
            return "COMBINED"
        return "CAPITAL_GAIN"
    if "special" in t or "extra" in t:
        return "SPECIAL"
    if (
        "interest" in t
        or "income" in t
        or "dividend" in t
        or "distribution" in t
        or not t
    ):
        return "INCOME"
    return "OTHER"
