"""Filing and Regulatory Notice Parser for Layer B Extraction."""

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


def _parse_amount(text: str) -> float | None:
    m = re.search(r"\$?\s*([0-9]+\.[0-9]+|[0-9]+)", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _parse_date(text: str) -> date | None:
    m_iso = re.search(r"\b(202[0-9])-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])\b", text)
    if m_iso:
        return date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))

    months = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
        "nov": 11, "november": 11, "dec": 12, "december": 12,
    }
    m_named = re.search(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+([0-9]{1,2}),?\s+(202[0-9])\b",
        text,
        re.IGNORECASE,
    )
    if m_named:
        m_str = m_named.group(1).lower()
        m_num = months.get(m_str, 1)
        return date(int(m_named.group(3)), m_num, int(m_named.group(2)))
    return None


def parse_sec_rule_19a1_filing(
    filing_text: str,
    fund_id: str,
    filing_url: str,
    ticker: str | None = None,
    filing_date: date | None = None,
) -> list[ExtractedDistribution]:
    """Parse Form 19a-1 Return of Capital and component disclosure filing."""
    results: list[ExtractedDistribution] = []
    clean_text = re.sub(r"<[^>]+>", " ", filing_text)

    # Search for total distribution amount per share
    total_m = re.search(r"(?:total\s+distribution|distribution\s+per\s+share)[\s:]+\$?([0-9]+\.[0-9]+)", clean_text, re.IGNORECASE)
    gross_amt = float(total_m.group(1)) if total_m else None

    # Search for Return of Capital component
    roc_m = re.search(r"(?:return\s+of\s+capital|\broc\b)[^\$0-9]{0,30}\$?([0-9]+\.[0-9]+)", clean_text, re.IGNORECASE)
    roc_amt = float(roc_m.group(1)) if roc_m else 0.0

    # Search for Net Investment Income component
    nii_m = re.search(r"(?:net\s+investment\s+income|ordinary\s+income)[^\$0-9]{0,30}\$?([0-9]+\.[0-9]+)", clean_text, re.IGNORECASE)
    nii_amt = float(nii_m.group(1)) if nii_m else 0.0

    # Search for Capital Gain component
    cg_m = re.search(r"(?:capital\s+gains?|realized\s+gains?)[^\$0-9]{0,30}\$?([0-9]+\.[0-9]+)", clean_text, re.IGNORECASE)
    cg_amt = float(cg_m.group(1)) if cg_m else 0.0

    if gross_amt is None:
        calc_total = roc_amt + nii_amt + cg_amt
        if calc_total > 0:
            gross_amt = calc_total

    if gross_amt is not None and gross_amt > 0:
        ex_d = _parse_date(clean_text) or filing_date or date.today()
        components: list[ExtractedComponent] = []
        if nii_amt > 0:
            components.append(ExtractedComponent(component_name="Net Investment Income", component_type=USComponentType.ORDINARY_INCOME, amount=nii_amt))
        if roc_amt > 0:
            components.append(ExtractedComponent(component_name="Return of Capital", component_type=USComponentType.RETURN_OF_CAPITAL, amount=roc_amt))
        if cg_amt > 0:
            components.append(ExtractedComponent(component_name="Capital Gain", component_type=USComponentType.LONG_TERM_CAPITAL_GAIN, amount=cg_amt))

        if not components:
            components.append(ExtractedComponent(component_name="Distribution", component_type=USComponentType.ORDINARY_INCOME, amount=gross_amt))

        results.append(
            ExtractedDistribution(
                fund_id=fund_id,
                country="US",
                currency="USD",
                ticker=ticker,
                declaration_date=filing_date,
                ex_date=ex_d,
                gross_amount=gross_amt,
                distribution_type="19a-1 Notice",
                components=components,
                source_url=filing_url,
                source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                extraction_route=ExtractionRoute.FILING,
                retrieved_at=datetime.now(timezone.utc),
                raw_doc_snippet=clean_text[:200],
                validation_passed=True,
            )
        )

    return results
