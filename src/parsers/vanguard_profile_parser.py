"""Vanguard fund profile HTML parser for embedded distribution data.

Extracts distributions from the `data-vgn-funds-profile` JSON attribute in Vanguard's
official product profile pages.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date

from src.models import ExtractedDistribution, ExtractionRoute, SourceTier
from src.text_utils import parse_amount, parse_date


def parse_vanguard_profile(
    html_text: str | None,
    fund_id: str,
    source_url: str,
    window_start: date | None = None,
    window_end: date | None = None,
    source_tier: SourceTier = SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
    ticker: str | None = None,
) -> list[ExtractedDistribution]:
    """Parse distribution records from data-vgn-funds-profile attribute in Vanguard profile HTML.

    Returns [] if attribute is missing or JSON is invalid. Never raises.
    """
    if not html_text or "data-vgn-funds-profile" not in html_text:
        return []

    # Find attribute value: data-vgn-funds-profile="..." or '...'
    match = re.search(
        r'data-vgn-funds-profile\s*=\s*(["\'])(.*?)\1', html_text, re.DOTALL
    )
    if not match:
        return []

    raw_attr = match.group(2)
    try:
        unescaped_json = html.unescape(raw_attr)
        data = json.loads(unescaped_json)
    except (ValueError, TypeError):
        return []

    if not isinstance(data, dict):
        return []

    distributions_obj = data.get("distributions")
    if not isinstance(distributions_obj, dict):
        return []

    rows = distributions_obj.get("incomeCapitalGains")
    if not isinstance(rows, list):
        return []

    extracted: list[ExtractedDistribution] = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        # Vanguard uses reInvestDate as the Ex-dividend date
        ex_date_str = row.get("reInvestDate") or row.get("exDate")
        ex_d = parse_date(str(ex_date_str)) if ex_date_str else None
        if not ex_d:
            continue

        if window_start and window_end:
            if not (window_start <= ex_d <= window_end):
                continue

        rec_date_str = row.get("recordDate")
        rec_d = parse_date(str(rec_date_str)) if rec_date_str else None

        pay_date_str = row.get("payableDate") or row.get("payDate")
        pay_d = parse_date(str(pay_date_str)) if pay_date_str else None

        decl_date_str = row.get("declarationDate") or row.get("declDate")
        decl_d = parse_date(str(decl_date_str)) if decl_date_str else None

        share_raw = str(row.get("share") or row.get("amount") or "")
        gross_amount = parse_amount(share_raw)
        if gross_amount is None:
            cleaned_str = share_raw.replace("$", "").replace(",", "").strip()
            try:
                gross_amount = float(cleaned_str)
            except ValueError:
                gross_amount = 0.0

        raw_type = str(row.get("type") or "Dividend").strip()
        type_lower = raw_type.lower()
        if type_lower == "dividend":
            dist_type = "Income"
        elif type_lower == "roc" or "return of capital" in type_lower:
            dist_type = "Return of Capital"
        else:
            dist_type = raw_type

        extracted.append(
            ExtractedDistribution(
                fund_id=fund_id,
                country="US",
                currency="USD",
                ex_date=ex_d,
                gross_amount=gross_amount,
                distribution_type=dist_type,
                ticker=ticker,
                declaration_date=decl_d,
                record_date=rec_d,
                payable_date=pay_d,
                source_url=source_url,
                source_tier=source_tier,
                extraction_route=ExtractionRoute.API,
                raw_doc_snippet=f"Vanguard Profile JSON: {json.dumps(row)}",
            )
        )

    return extracted
