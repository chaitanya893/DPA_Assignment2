"""PDF Parser for Layer B Extraction."""

from __future__ import annotations

import io
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
from src.parsers.html_table_parser import _parse_amount, _parse_date


def parse_pdf_distribution_document(
    pdf_bytes: bytes,
    fund_id: str,
    country: str,
    source_url: str,
    ticker: str | None = None,
) -> list[ExtractedDistribution]:
    """Parse text and tables from PDF document bytes."""
    results: list[ExtractedDistribution] = []
    currency = "CAD" if country.upper() == "CA" else "USD"

    # Extract text lines using basic PDF text layer or pdfplumber if available
    text_content = ""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                text_content += (page.extract_text() or "") + "\n"
    except ImportError:
        # Fallback regex search on byte stream if pdfplumber not installed
        text_content = pdf_bytes.decode("latin-1", errors="ignore")

    lines = [line.strip() for line in text_content.splitlines() if line.strip()]
    for line in lines:
        if ticker and ticker.upper() in line.upper():
            amt = _parse_amount(line)
            d = _parse_date(line)
            if amt is not None and d is not None:
                primary_comp = CAComponentType.ELIGIBLE_DIVIDEND if country == "CA" else USComponentType.ORDINARY_INCOME
                comp = ExtractedComponent(component_name="Income", component_type=primary_comp, amount=amt, percentage=100.0)
                results.append(
                    ExtractedDistribution(
                        fund_id=fund_id,
                        country=country,
                        currency=currency,
                        ticker=ticker,
                        ex_date=d,
                        gross_amount=amt,
                        distribution_type="Income",
                        components=[comp],
                        source_url=source_url,
                        source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                        extraction_route=ExtractionRoute.PDF,
                        retrieved_at=datetime.now(timezone.utc),
                        raw_doc_snippet=line[:200],
                        validation_passed=True,
                    )
                )

    return results
