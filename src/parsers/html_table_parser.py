"""HTML Table Parser for Layer B Extraction."""

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


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def _parse_amount(text: str) -> float | None:
    m = re.search(r"\$?\s*([0-9]+\.[0-9]+|[0-9]+)", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _parse_date(text: str) -> date | None:
    # Match YYYY-MM-DD
    m_iso = re.search(r"\b(202[0-9])-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])\b", text)
    if m_iso:
        return date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))

    # Match MM/DD/YYYY
    m_us = re.search(r"\b(0?[1-9]|1[0-2])/(0?[1-9]|[12][0-9]|3[01])/(202[0-9])\b", text)
    if m_us:
        return date(int(m_us.group(3)), int(m_us.group(1)), int(m_us.group(2)))

    # Match Month DD, YYYY
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


def parse_html_distribution_tables(
    html_content: str,
    fund_id: str,
    country: str,
    source_url: str,
    ticker: str | None = None,
    fundserv_code: str | None = None,
    target_ex_date: date | None = None,
) -> list[ExtractedDistribution]:
    """Parse HTML tables into structured ExtractedDistribution objects."""
    results: list[ExtractedDistribution] = []
    currency = "CAD" if country.upper() == "CA" else "USD"

    # Find all <table>...</table>
    table_matches = re.finditer(r"<table[^>]*>([\s\S]*?)</table>", html_content, re.IGNORECASE)
    for t_m in table_matches:
        t_html = t_m.group(1)
        row_matches = re.finditer(r"<tr[^>]*>([\s\S]*?)</tr>", t_html, re.IGNORECASE)
        headers: list[str] = []

        for r_m in row_matches:
            r_html = r_m.group(1)
            th_cells = re.findall(r"<th[^>]*>([\s\S]*?)</th>", r_html, re.IGNORECASE)
            if th_cells:
                headers = [_clean_text(c).lower() for c in th_cells]
                continue

            td_cells = re.findall(r"<td[^>]*>([\s\S]*?)</td>", r_html, re.IGNORECASE)
            if not td_cells:
                continue

            clean_cells = [_clean_text(c) for c in td_cells]
            if not headers:
                # Check if first row is header
                if any(k in " ".join(clean_cells).lower() for k in ["payable", "record", "ex-dividend", "amount", "rate"]):
                    headers = [c.lower() for c in clean_cells]
                    continue

            # Multi-fund cross check
            if ticker or fundserv_code:
                ticker_col_idx = None
                for idx, h in enumerate(headers):
                    if any(k in h for k in ["ticker", "symbol", "code"]):
                        ticker_col_idx = idx
                        break
                if ticker_col_idx is not None and ticker_col_idx < len(clean_cells):
                    row_ticker = clean_cells[ticker_col_idx].upper().strip()
                    target_syms = {s.upper() for s in [ticker, fundserv_code] if s}
                    if row_ticker and row_ticker not in target_syms:
                        continue

            decl_d: date | None = None
            ex_d: date | None = None
            rec_d: date | None = None
            pay_d: date | None = None
            gross_amt: float | None = None
            dist_type: str = "Income"
            components: list[ExtractedComponent] = []

            for h, cell in zip(headers, clean_cells):
                d = _parse_date(cell)
                if d:
                    if any(k in h for k in ["decl", "announced"]):
                        decl_d = d
                    elif any(k in h for k in ["ex-div", "ex div", "ex-date", "ex date"]):
                        ex_d = d
                    elif "record" in h:
                        rec_d = d
                    elif any(k in h for k in ["pay", "payable"]):
                        pay_d = d

                amt = _parse_amount(cell)
                if amt is not None:
                    if any(k in h for k in ["amount", "rate", "$/share", "$/unit", "total", "distribution"]):
                        if gross_amt is None:
                            gross_amt = amt
                    # Component mappings
                    if "capital gain" in h or "cap gain" in h:
                        c_type = CAComponentType.CAPITAL_GAINS if country == "CA" else USComponentType.LONG_TERM_CAPITAL_GAIN
                        components.append(ExtractedComponent(component_name=h, component_type=c_type, amount=amt))
                    elif "return of capital" in h or "roc" in h:
                        c_type = CAComponentType.RETURN_OF_CAPITAL if country == "CA" else USComponentType.RETURN_OF_CAPITAL
                        components.append(ExtractedComponent(component_name=h, component_type=c_type, amount=amt))
                    elif "eligible" in h:
                        components.append(ExtractedComponent(component_name=h, component_type=CAComponentType.ELIGIBLE_DIVIDEND, amount=amt))
                    elif "qualified" in h:
                        components.append(ExtractedComponent(component_name=h, component_type=USComponentType.QUALIFIED_DIVIDEND, amount=amt))

                if "type" in h:
                    dist_type = cell

            if not ex_d and (rec_d or pay_d or decl_d):
                ex_d = rec_d or pay_d or decl_d

            if ex_d and gross_amt is not None:
                if target_ex_date and ex_d != target_ex_date:
                    continue

                # If no individual components parsed, assign full gross to primary income
                if not components:
                    primary_comp_type = CAComponentType.ELIGIBLE_DIVIDEND if country == "CA" else USComponentType.ORDINARY_INCOME
                    components.append(
                        ExtractedComponent(
                            component_name=dist_type,
                            component_type=primary_comp_type,
                            amount=gross_amt,
                            percentage=100.0,
                        )
                    )

                results.append(
                    ExtractedDistribution(
                        fund_id=fund_id,
                        country=country,
                        currency=currency,
                        ticker=ticker,
                        fundserv_code=fundserv_code,
                        declaration_date=decl_d,
                        ex_date=ex_d,
                        record_date=rec_d,
                        payable_date=pay_d,
                        gross_amount=gross_amt,
                        distribution_type=dist_type,
                        components=components,
                        source_url=source_url,
                        source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                        extraction_route=ExtractionRoute.HTML_TABLE,
                        retrieved_at=datetime.now(timezone.utc),
                        raw_doc_snippet=" | ".join(clean_cells)[:200],
                        validation_passed=True,
                    )
                )

    return results
