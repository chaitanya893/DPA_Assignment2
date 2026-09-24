"""PDF and Excel document parsers for Layer B extraction (routes PDF).

PDF text layer: pdfplumber (tables first, then text rows) with pypdf as a fallback.
Scanned PDFs without a text layer return no events; the caller routes them to manual
review (OCR with ocrmypdf/Tesseract is documented as a known limitation, not faked).
Excel: openpyxl through pandas.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date

from src.models import ExtractedDistribution, ExtractionRoute, SourceTier
from src.parsers.html_table_parser import parse_table_rows
from src.text_utils import find_all_dates, parse_amount

logger = logging.getLogger(__name__)

_KIND_PATTERNS = [
    ("declaration", r"declar|announc"),
    ("ex", r"ex[-\s]?(?:div|dividend|date)"),
    ("record", r"record"),
    ("payable", r"pay(?:able|ment)?\s*date|payable"),
]


def _pdf_tables_and_text(pdf_bytes: bytes) -> tuple[list[list[list[str]]], str]:
    tables: list[list[list[str]]] = []
    text = ""
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                for tbl in page.extract_tables() or []:
                    tables.append(
                        [[(c or "").strip() for c in row] for row in tbl if row]
                    )
                text += (page.extract_text() or "") + "\n"
        return tables, text
    except ImportError:
        logger.info("pdfplumber not installed; falling back to pypdf text layer")
    except Exception as err:  # noqa: BLE001 - malformed PDFs raise many types
        logger.warning("pdfplumber failed to read PDF: %s", err)
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as err:  # noqa: BLE001
        logger.warning("pypdf failed to read PDF: %s", err)
    return tables, text


def _header_kinds(line: str) -> list[str]:
    low = line.lower()
    hits: list[tuple[int, str]] = []
    for kind, pat in _KIND_PATTERNS:
        m = re.search(pat, low)
        if m:
            hits.append((m.start(), kind))
    hits.sort()
    return [k for _, k in hits]


def _rows_from_text(text: str, ticker: str | None) -> list[tuple[list[str], list[str]]]:
    """Rebuild table rows from a text-layer schedule: a header line naming date types,
    followed by lines with the same number of dates plus an amount."""
    rows: list[tuple[list[str], list[str]]] = []
    kinds: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        hk = _header_kinds(line)
        if len(hk) >= 2 and not find_all_dates(line):
            kinds = hk
            continue
        if not kinds:
            continue
        dates = find_all_dates(line)
        if len(dates) != len(kinds):
            continue
        stripped = line
        for m in re.finditer(r"\S+", line):
            if find_all_dates(m.group(0)):
                stripped = stripped.replace(m.group(0), " ")
        amt = None
        for tok in re.findall(r"\$?\d*\.\d+|\$\d+", stripped):
            amt = parse_amount(tok)
            if amt is not None:
                break
        if amt is None:
            continue
        headers = [f"{k} date" if k != "ex" else "ex-date" for k in kinds] + ["amount"]
        cells = [d.isoformat() for d in dates] + [str(amt)]
        rows.append((headers, cells))
    return rows


def parse_pdf_distribution_document(
    pdf_bytes: bytes,
    fund_id: str,
    country: str,
    source_url: str,
    ticker: str | None = None,
    fundserv_code: str | None = None,
    window_start: date | None = None,
    window_end: date | None = None,
    source_tier: SourceTier = SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
) -> list[ExtractedDistribution]:
    """Extract distribution rows from a PDF schedule. Returns [] when nothing reliable is found."""
    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF-"):
        return []
    tables, text = _pdf_tables_and_text(pdf_bytes)
    rows: list[tuple[list[str], list[str]]] = []
    for tbl in tables:
        if len(tbl) < 2:
            continue
        headers = [h.lower() for h in tbl[0]]
        rows.extend((headers, r) for r in tbl[1:])
    common = dict(
        fund_id=fund_id,
        country=country,
        source_url=source_url,
        ticker=ticker,
        fundserv_code=fundserv_code,
        window_start=window_start,
        window_end=window_end,
        source_tier=source_tier,
        extraction_route=ExtractionRoute.PDF,
    )
    events = parse_table_rows(rows, **common) if rows else []
    if not events and text:
        events = parse_table_rows(_rows_from_text(text, ticker), **common)
    return events


def parse_excel_distribution_document(
    xlsx_bytes: bytes,
    fund_id: str,
    country: str,
    source_url: str,
    ticker: str | None = None,
    fundserv_code: str | None = None,
    window_start: date | None = None,
    window_end: date | None = None,
    source_tier: SourceTier = SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
) -> list[ExtractedDistribution]:
    """Extract distribution rows from every sheet of an Excel schedule."""
    try:
        import pandas as pd

        sheets = pd.read_excel(
            io.BytesIO(xlsx_bytes), sheet_name=None, header=None, dtype=str
        )
    except Exception as err:  # noqa: BLE001
        logger.warning("Excel schedule could not be read: %s", err)
        return []
    rows: list[tuple[list[str], list[str]]] = []
    for df in sheets.values():
        values = [
            [
                (
                    ""
                    if v is None or str(v) in ("nan", "<NA>", "NaT", "None")
                    else str(v).strip()
                )
                for v in r
            ]
            for r in df.values.tolist()
        ]
        headers: list[str] = []
        for r in values:
            joined = " ".join(r).lower()
            if not headers:
                if (
                    "ex" in joined or "record" in joined or "pay" in joined
                ) and not any(find_all_dates(c) for c in r):
                    headers = [c.lower() for c in r]
                continue
            rows.append((headers, r))
    return parse_table_rows(
        rows,
        fund_id=fund_id,
        country=country,
        source_url=source_url,
        ticker=ticker,
        fundserv_code=fundserv_code,
        window_start=window_start,
        window_end=window_end,
        source_tier=source_tier,
        extraction_route=ExtractionRoute.PDF,
    )
