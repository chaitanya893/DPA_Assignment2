"""Regulatory filing / notice parser for Layer B extraction (route FILING).

Handles Section 19(a) / Rule 19a-1 source-of-distribution notices and distribution
announcements filed on EDGAR or published as TMX/CDS notices.

Dates are read only next to their own label ("Ex-Date:", "Record Date:", ...). The first
date that happens to appear in a filing (often a fiscal year-end on the cover page) is
never used as the ex-date, and there is no fallback to today's date or the filing date.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from src.models import (
    CAComponentType,
    ExtractedComponent,
    ExtractedDistribution,
    ExtractionRoute,
    SourceTier,
    USComponentType,
)
from src.text_utils import clean_html_text, parse_amount, parse_date

_LABELLED_DATE = {
    "ex": r"ex[-\s]?(?:dividend\s+|distribution\s+)?date",
    "record": r"record\s+date",
    "payable": r"(?:payable|payment|pay)\s+date|payable\s+on",
    "declaration": r"declaration\s+date|declared\s+on",
}

_AMOUNT = r"\$?\s*(\d+\.\d+)"


def _parse_amount(text: str) -> float | None:
    return parse_amount(text)


def _parse_date(text: str) -> date | None:
    return parse_date(text)


def _labelled_date(text: str, kind: str) -> date | None:
    m = re.search(
        rf"(?:{_LABELLED_DATE[kind]})\s*[:\-]?\s*(.{{0,40}})", text, re.IGNORECASE
    )
    return parse_date(m.group(1)) if m else None


def _labelled_amount(text: str, label_regex: str) -> float | None:
    m = re.search(rf"(?:{label_regex})[^$0-9]{{0,40}}{_AMOUNT}", text, re.IGNORECASE)
    return float(m.group(1)) if m else None


def parse_sec_rule_19a1_filing(
    filing_text: str,
    fund_id: str,
    filing_url: str,
    ticker: str | None = None,
    filing_date: date | None = None,
    country: str = "US",
    source_tier: SourceTier = SourceTier.TIER_1_AUTHORITATIVE,
) -> list[ExtractedDistribution]:
    """Parse a distribution notice. Returns [] if the notice has no labelled ex-date or amount.

    ``filing_date`` is accepted for interface compatibility and recorded in the snippet only;
    it is not a declaration date and is never stored as one.
    """
    text = clean_html_text(filing_text)
    ex_d = _labelled_date(text, "ex")
    if ex_d is None:
        return []

    gross = _labelled_amount(
        text,
        r"total\s+distribution(?:\s+per\s+(?:share|unit))?|distribution\s+per\s+(?:share|unit)|"
        r"distribution\s+(?:amount|rate)|per\s+(?:share|unit)\s+distribution",
    )

    components: list[ExtractedComponent] = []
    is_ca = country.upper() == "CA"

    def add(
        label_regex: str,
        us_type: USComponentType,
        ca_type: CAComponentType | None,
        name: str,
    ) -> None:
        amt = _labelled_amount(text, label_regex)
        if amt is None or amt <= 0:
            return
        comp_type = ca_type if is_ca and ca_type is not None else us_type
        components.append(
            ExtractedComponent(
                component_name=name, component_type=comp_type, amount=amt
            )
        )

    add(
        r"net\s+investment\s+income|ordinary\s+income",
        USComponentType.ORDINARY_INCOME,
        None,
        "Net Investment Income",
    )
    add(
        r"return\s+of\s+capital|\broc\b",
        USComponentType.RETURN_OF_CAPITAL,
        CAComponentType.RETURN_OF_CAPITAL,
        "Return of Capital",
    )
    stcg = r"short[-\s]term\s+(?:realized\s+)?capital\s+gains?"
    ltcg = r"long[-\s]term\s+(?:realized\s+)?capital\s+gains?"
    add(
        stcg,
        USComponentType.SHORT_TERM_CAPITAL_GAIN,
        CAComponentType.CAPITAL_GAINS,
        "Short-Term Capital Gain",
    )
    add(
        ltcg,
        USComponentType.LONG_TERM_CAPITAL_GAIN,
        CAComponentType.CAPITAL_GAINS,
        "Long-Term Capital Gain",
    )
    if not re.search(stcg, text, re.IGNORECASE) and not re.search(
        ltcg, text, re.IGNORECASE
    ):
        add(
            r"(?<!short-term )(?<!long-term )(?:net\s+)?(?:realized\s+)?capital\s+gains?",
            USComponentType.CAPITAL_GAIN_UNCLASSIFIED,
            CAComponentType.CAPITAL_GAINS,
            "Capital Gain",
        )

    notes = ""
    if gross is None and components:
        gross = round(sum(c.amount for c in components), 6)
        notes = "Gross derived from sum of published components (no total stated)."
    if gross is None or gross <= 0:
        return []

    cg_types = {
        USComponentType.SHORT_TERM_CAPITAL_GAIN,
        USComponentType.LONG_TERM_CAPITAL_GAIN,
        USComponentType.CAPITAL_GAIN_UNCLASSIFIED,
        CAComponentType.CAPITAL_GAINS,
    }
    has_cg = any(c.component_type in cg_types for c in components)
    has_other = any(c.component_type not in cg_types for c in components)
    if has_cg and has_other:
        dist_type = "Income and Capital Gain Distribution"
    elif has_cg:
        dist_type = "Capital Gain Distribution"
    else:
        dist_type = "Income Distribution"
    if re.search(r"19\(a\)|19a-1", text, re.IGNORECASE):
        dist_type += " (Section 19(a) notice)"

    return [
        ExtractedDistribution(
            fund_id=fund_id,
            country=country.upper(),
            currency="CAD" if is_ca else "USD",
            ticker=ticker,
            declaration_date=_labelled_date(text, "declaration"),
            ex_date=ex_d,
            record_date=_labelled_date(text, "record"),
            payable_date=_labelled_date(text, "payable"),
            gross_amount=gross,
            distribution_type=dist_type,
            components=components,
            source_url=filing_url,
            source_tier=source_tier,
            extraction_route=ExtractionRoute.FILING,
            retrieved_at=datetime.now(timezone.utc),
            raw_doc_snippet=(f"[filed {filing_date}] " if filing_date else "")
            + text[:200],
            validation_passed=True,
            validation_notes=notes,
        )
    ]
