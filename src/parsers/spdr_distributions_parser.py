"""State Street SPDR historical distributions Excel parser.

Parses official SPDR distribution records from the historical Excel spreadsheet:
https://www.ssga.com/library-content/products/fund-data/etfs/us/spdr-etf-historical-distributions.xlsx
"""

from __future__ import annotations

import io
from datetime import date, datetime
from typing import Any

import openpyxl

from src.models import (
    ExtractedComponent,
    ExtractedDistribution,
    ExtractionRoute,
    SourceTier,
    USComponentType,
)
from src.text_utils import parse_date


def _clean_num(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace("$", "").replace(",", "")
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _extract_date(val: Any) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    if not s:
        return None
    return parse_date(s)


def parse_spdr_distributions(
    xlsx_bytes: bytes | None,
    fund_id: str,
    ticker: str | None,
    source_url: str,
    window_start: date | None = None,
    window_end: date | None = None,
    source_tier: SourceTier = SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
) -> list[ExtractedDistribution]:
    """Parse distribution records for a specific SPDR ETF ticker from the master Excel workbook.

    Returns [] on any problem or if bytes are invalid/empty. Never raises.
    """
    if not xlsx_bytes or not ticker:
        return []

    try:
        wb = openpyxl.load_workbook(
            io.BytesIO(xlsx_bytes), read_only=True, data_only=True
        )
    except Exception:
        return []

    try:
        sheet = wb.active
        if sheet is None:
            return []

        rows_iter = sheet.iter_rows(values_only=True)
        header_row = None
        ticker_idx = -1
        ex_date_idx = -1
        rec_date_idx = -1
        pay_date_idx = -1
        div_idx = -1
        stcg_idx = -1
        ltcg_idx = -1

        for row in rows_iter:
            if not row:
                continue
            str_cells = [str(c or "").strip().upper() for c in row]
            if "TICKER" in str_cells and any(
                "EX-DATE" in c or "EX DATE" in c for c in str_cells
            ):
                header_row = str_cells
                for i, cell in enumerate(str_cells):
                    if cell == "TICKER":
                        ticker_idx = i
                    elif "EX-DATE" in cell or "EX DATE" in cell:
                        ex_date_idx = i
                    elif "RECORD" in cell:
                        rec_date_idx = i
                    elif "PAYABLE" in cell:
                        pay_date_idx = i
                    elif "DIVIDEND" in cell:
                        div_idx = i
                    elif "SHORT" in cell and "CAPITAL" in cell:
                        stcg_idx = i
                    elif "LONG" in cell and "CAPITAL" in cell:
                        ltcg_idx = i
                break

        if header_row is None or ticker_idx == -1 or ex_date_idx == -1:
            return []

        target_ticker = ticker.strip().upper()
        results: list[ExtractedDistribution] = []

        for row in rows_iter:
            if not row or len(row) <= ticker_idx:
                continue

            row_ticker = str(row[ticker_idx] or "").strip().upper()
            if row_ticker != target_ticker:
                continue

            ex_d = _extract_date(row[ex_date_idx]) if ex_date_idx < len(row) else None
            if not ex_d:
                continue

            if window_start and window_end:
                if not (window_start <= ex_d <= window_end):
                    continue

            rec_d = (
                _extract_date(row[rec_date_idx])
                if 0 <= rec_date_idx < len(row)
                else None
            )
            pay_d = (
                _extract_date(row[pay_date_idx])
                if 0 <= pay_date_idx < len(row)
                else None
            )

            div_amt = _clean_num(row[div_idx]) if 0 <= div_idx < len(row) else 0.0
            stcg_amt = _clean_num(row[stcg_idx]) if 0 <= stcg_idx < len(row) else 0.0
            ltcg_amt = _clean_num(row[ltcg_idx]) if 0 <= ltcg_idx < len(row) else 0.0

            gross = round(div_amt + stcg_amt + ltcg_amt, 6)
            if gross <= 0:
                continue

            components: list[ExtractedComponent] = []
            if div_amt > 0:
                components.append(
                    ExtractedComponent(
                        component_name="Ordinary Income",
                        component_type=USComponentType.ORDINARY_INCOME,
                        amount=round(div_amt, 6),
                    )
                )
            if stcg_amt > 0:
                components.append(
                    ExtractedComponent(
                        component_name="Short-Term Capital Gain",
                        component_type=USComponentType.SHORT_TERM_CAPITAL_GAIN,
                        amount=round(stcg_amt, 6),
                    )
                )
            if ltcg_amt > 0:
                components.append(
                    ExtractedComponent(
                        component_name="Long-Term Capital Gain",
                        component_type=USComponentType.LONG_TERM_CAPITAL_GAIN,
                        amount=round(ltcg_amt, 6),
                    )
                )

            if div_amt > 0 and stcg_amt == 0 and ltcg_amt == 0:
                dist_type = "Income"
            elif ltcg_amt > 0 and div_amt == 0 and stcg_amt == 0:
                dist_type = "Long-Term Capital Gain"
            elif stcg_amt > 0 and div_amt == 0 and ltcg_amt == 0:
                dist_type = "Short-Term Capital Gain"
            elif div_amt > 0:
                dist_type = "Income"
            else:
                dist_type = "Capital Gain"

            results.append(
                ExtractedDistribution(
                    fund_id=fund_id,
                    country="US",
                    currency="USD",
                    ex_date=ex_d,
                    gross_amount=gross,
                    distribution_type=dist_type,
                    ticker=ticker,
                    record_date=rec_d,
                    payable_date=pay_d,
                    components=components,
                    source_url=source_url,
                    source_tier=source_tier,
                    extraction_route=ExtractionRoute.PDF,
                    raw_doc_snippet=f"SPDR ETF historical distributions sheet: {target_ticker} on {ex_d}",
                )
            )

        return results
    except Exception:
        return []
    finally:
        try:
            wb.close()
        except Exception:
            pass
