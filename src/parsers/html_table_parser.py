"""HTML table parser for Layer B extraction (route HTML_TABLE).

Rules enforced here (PDF Assignment 2, Phase 0 and Phase 2):
- Dates are read only from the column whose header names that date type. An ex-date is
  never invented from a record, payable or declaration date.
- Amounts are read only from amount columns, never from a date column.
- Tax components are recorded only when the source table has a column for them.
  No component is invented; if the table only shows a total, ``components`` stays empty.
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

_DATE_HEADER_WORDS = ("date", "day", "year", "month", "as of")
_GROSS_HEADER_WORDS = (
    "amount",
    "rate",
    "$/share",
    "$/unit",
    "per share",
    "per unit",
    "total",
    "distribution",
    "dividend",
    "income",
)
_HEADER_HINTS = (
    "payable",
    "record",
    "ex-div",
    "ex div",
    "ex-date",
    "ex date",
    "amount",
    "rate",
)


def _clean_text(s: str) -> str:
    return clean_html_text(s)


def _parse_amount(text: str) -> float | None:
    return parse_amount(text)


def _parse_date(text: str) -> date | None:
    return parse_date(text)


def classify_component(
    header: str, country: str
) -> USComponentType | CAComponentType | None:
    """Map a column header to a component type, or None if it is not a component column."""
    h = header.lower()
    if country.upper() == "CA":
        if "non-eligible" in h or "non eligible" in h:
            return CAComponentType.NON_ELIGIBLE_DIVIDEND
        if "eligible" in h:
            return CAComponentType.ELIGIBLE_DIVIDEND
        if "capital gain" in h:
            return CAComponentType.CAPITAL_GAINS
        if "return of capital" in h or re.search(r"\broc\b", h):
            return CAComponentType.RETURN_OF_CAPITAL
        if "foreign tax" in h:
            return CAComponentType.FOREIGN_TAX_PAID
        if "foreign" in h:
            return CAComponentType.FOREIGN_INCOME
        if "interest" in h or "other income" in h:
            return CAComponentType.INTEREST_AND_OTHER
        return None
    if "qualified" in h:
        return USComponentType.QUALIFIED_DIVIDEND
    if "ordinary" in h or "net investment income" in h:
        return USComponentType.ORDINARY_INCOME
    if "short-term" in h or "short term" in h:
        return USComponentType.SHORT_TERM_CAPITAL_GAIN
    if "long-term" in h or "long term" in h:
        return USComponentType.LONG_TERM_CAPITAL_GAIN
    if "capital gain" in h:
        return USComponentType.CAPITAL_GAIN_UNCLASSIFIED
    if "return of capital" in h or re.search(r"\broc\b", h):
        return USComponentType.RETURN_OF_CAPITAL
    if "foreign tax" in h:
        return USComponentType.FOREIGN_TAX_PAID
    if "199a" in h:
        return USComponentType.SECTION_199A
    if "exempt" in h:
        return USComponentType.TAX_EXEMPT_INCOME
    return None


def _date_kind(header: str) -> str | None:
    h = header.lower()
    if any(k in h for k in ("decl", "announced")):
        return "declaration"
    if any(
        k in h
        for k in ("ex-div", "ex div", "ex-date", "ex date", "exdate", "ex-dividend")
    ):
        return "ex"
    if "record" in h:
        return "record"
    if any(k in h for k in ("payable", "pay date", "payment", "paid")):
        return "payable"
    return None


def parse_html_distribution_tables(
    html_content: str,
    fund_id: str,
    country: str,
    source_url: str,
    ticker: str | None = None,
    fundserv_code: str | None = None,
    target_ex_date: date | None = None,
    window_start: date | None = None,
    window_end: date | None = None,
    source_tier: SourceTier = SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
    extraction_route: ExtractionRoute = ExtractionRoute.HTML_TABLE,
) -> list[ExtractedDistribution]:
    """Parse HTML distribution tables into ExtractedDistribution rows.

    Only rows that carry an explicit ex-date and an explicit amount become events.
    """
    rows = html_table_rows(html_content)
    return parse_table_rows(
        rows,
        fund_id=fund_id,
        country=country,
        source_url=source_url,
        ticker=ticker,
        fundserv_code=fundserv_code,
        target_ex_date=target_ex_date,
        window_start=window_start,
        window_end=window_end,
        source_tier=source_tier,
        extraction_route=extraction_route,
    )


def html_table_rows(html_content: str) -> list[tuple[list[str], list[str]]]:
    """(headers, cells) for every data row of every <table> in the page."""
    rows: list[tuple[list[str], list[str]]] = []
    for t_m in re.finditer(
        r"<table[^>]*>([\s\S]*?)</table>", html_content, re.IGNORECASE
    ):
        headers: list[str] = []
        for r_m in re.finditer(
            r"<tr[^>]*>([\s\S]*?)</tr>", t_m.group(1), re.IGNORECASE
        ):
            r_html = r_m.group(1)
            all_cell_matches = list(
                re.finditer(r"<(th|td)[^>]*>([\s\S]*?)</\1>", r_html, re.IGNORECASE)
            )
            if not all_cell_matches:
                continue

            has_th = any(m.group(1).lower() == "th" for m in all_cell_matches)
            has_td = any(m.group(1).lower() == "td" for m in all_cell_matches)

            # Only a <tr> with <th> cells and NO <td> is a header row
            if has_th and not has_td:
                headers = [_clean_text(m.group(2)).lower() for m in all_cell_matches]
                continue

            # A <tr> with <td> (or both <th> and <td>) is a data row
            cells = [_clean_text(m.group(2)) for m in all_cell_matches]
            if not headers:
                joined = " ".join(cells).lower()
                if any(k in joined for k in _HEADER_HINTS) and not any(
                    parse_date(c) for c in cells
                ):
                    headers = [c.lower() for c in cells]
                continue
            rows.append((headers, cells))
    return rows


def table_date_rows(
    html_content: str,
    ticker: str | None = None,
    fundserv_code: str | None = None,
) -> list[tuple[dict[str, date], str]]:
    """(labelled dates, row text) per table row (ex / record / payable / declaration), amount not required.

    Used by Layer A to decide whether a schedule table covers a window. Only dates in columns
    whose header names the date type are returned.
    """
    target = {x.upper() for x in (ticker, fundserv_code) if x}
    out: list[tuple[dict[str, date], str]] = []
    for headers, cells in html_table_rows(html_content):
        if len(headers) != len(cells):
            continue
        sym_idx = next(
            (
                i
                for i, h in enumerate(headers)
                if any(k in h for k in ("ticker", "symbol", "fund code"))
            ),
            None,
        )
        if (
            sym_idx is not None
            and target
            and cells[sym_idx].upper().strip() not in target | {""}
        ):
            continue
        found: dict[str, date] = {}
        for h, cell in zip(headers, cells, strict=True):
            kind = _date_kind(h)
            if kind and kind not in found:
                d = parse_date(cell)
                if d:
                    found[kind] = d
        if found:
            out.append((found, " | ".join(c for c in cells if c)))
    return out


def parse_table_rows(
    rows: list[tuple[list[str], list[str]]],
    fund_id: str,
    country: str,
    source_url: str,
    ticker: str | None = None,
    fundserv_code: str | None = None,
    target_ex_date: date | None = None,
    window_start: date | None = None,
    window_end: date | None = None,
    source_tier: SourceTier = SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
    extraction_route: ExtractionRoute = ExtractionRoute.HTML_TABLE,
) -> list[ExtractedDistribution]:
    """Turn (headers, cells) rows from any tabular source (HTML, Excel, CSV) into events."""
    results: list[ExtractedDistribution] = []
    currency = "CAD" if country.upper() == "CA" else "USD"
    target_syms = {s.upper() for s in (ticker, fundserv_code) if s}

    for headers, cells in rows:
        if len(headers) != len(cells):
            continue

        # Multi-fund tables: skip rows that belong to another fund.
        sym_idx = next(
            (
                i
                for i, h in enumerate(headers)
                if any(k in h for k in ("ticker", "symbol", "fund code"))
            ),
            None,
        )
        if sym_idx is not None and target_syms:
            row_sym = cells[sym_idx].upper().strip()
            if row_sym and row_sym not in target_syms:
                continue

        dates: dict[str, date] = {}
        total_amt: float | None = None
        fallback_gross_amt: float | None = None
        dist_type = "Income"
        components: list[ExtractedComponent] = []
        is_estimated = False

        for h, cell in zip(headers, cells, strict=True):
            if "estimat" in h or "estimat" in cell.lower():
                is_estimated = True
            if "type" in h or h in ("distribution", "description"):
                if cell and parse_amount(cell) is None:
                    dist_type = cell
                    continue
            kind = _date_kind(h)
            if kind:
                d = parse_date(cell)
                if d and kind not in dates:
                    dates[kind] = d
                continue
            if any(k in h for k in _DATE_HEADER_WORDS):
                continue
            amt = parse_amount(cell)
            if amt is None:
                continue
            comp_type = classify_component(h, country)
            if comp_type is not None:
                components.append(
                    ExtractedComponent(
                        component_name=h, component_type=comp_type, amount=amt
                    )
                )
            else:
                if "total" in h and any(k in h for k in _GROSS_HEADER_WORDS):
                    if total_amt is None:
                        total_amt = amt
                elif fallback_gross_amt is None and any(
                    k in h for k in _GROSS_HEADER_WORDS
                ):
                    fallback_gross_amt = amt

        gross_amt = total_amt if total_amt is not None else fallback_gross_amt
        ex_d = dates.get("ex")
        notes = ""
        if gross_amt is None and components:
            gross_amt = round(sum(c.amount for c in components), 6)
            notes = "Gross derived from sum of published components (no total column)."
        if ex_d is None or gross_amt is None:
            continue
        if target_ex_date and ex_d != target_ex_date:
            continue
        if window_start and ex_d < window_start:
            continue
        if window_end and ex_d > window_end:
            continue

        results.append(
            ExtractedDistribution(
                fund_id=fund_id,
                country=country,
                currency=currency,
                ticker=ticker,
                fundserv_code=fundserv_code,
                declaration_date=dates.get("declaration"),
                ex_date=ex_d,
                record_date=dates.get("record"),
                payable_date=dates.get("payable"),
                gross_amount=gross_amt,
                distribution_type=dist_type,
                is_estimated=is_estimated,
                components=components,
                source_url=source_url,
                source_tier=source_tier,
                extraction_route=extraction_route,
                retrieved_at=datetime.now(timezone.utc),
                raw_doc_snippet=" | ".join(cells)[:200],
                validation_passed=True,
                validation_notes=notes,
            )
        )
    return results
