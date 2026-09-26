"""Parser for RBC ETF embedded distribution data (route API / structured JSON embedded in HTML).

Extracts distribution events from the first `const fundData = {...}` script block.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any

from src.models import ExtractedDistribution, ExtractionRoute, SourceTier
from src.text_utils import parse_amount, parse_date

logger = logging.getLogger(__name__)


def _extract_first_fund_data_json(html: str) -> dict[str, Any] | None:
    marker = "const fundData = "
    idx = html.find(marker)
    if idx == -1:
        return None

    start_brace = html.find("{", idx)
    if start_brace == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    quote_char = None
    end_idx = -1

    for i in range(start_brace, len(html)):
        c = html[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == quote_char:
                in_string = False
        else:
            if c in ('"', "'", "`"):
                in_string = True
                quote_char = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end_idx = i + 1
                    break

    if end_idx == -1:
        return None

    raw_json = html[start_brace:end_idx]
    try:
        return json.loads(raw_json)
    except Exception as e:
        logger.warning("Failed to parse RBC fundData JSON: %s", e)
        return None


def parse_rbc_fund_data(
    html: str,
    fund_id: str,
    ticker: str,
    source_url: str,
    window_start: date | None = None,
    window_end: date | None = None,
    source_tier: SourceTier = SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
) -> list[ExtractedDistribution]:
    """Parse distribution events from RBC fundData embedded in HTML."""
    if not html or "const fundData" not in html:
        return []

    data = _extract_first_fund_data_json(html)
    if not data or not isinstance(data, dict):
        return []

    data_ticker = (data.get("ticker") or "").strip().upper()
    if ticker and data_ticker != ticker.strip().upper():
        return []

    distributions_map = data.get("distributions")
    if not isinstance(distributions_map, dict):
        return []

    now = datetime.now(timezone.utc)
    events: list[ExtractedDistribution] = []

    for _year_key, year_obj in distributions_map.items():
        if not isinstance(year_obj, dict):
            continue
        details = year_obj.get("details")
        if not isinstance(details, list):
            continue

        for item in details:
            if not isinstance(item, dict):
                continue

            ex_date_str = item.get("exDivDate")
            if not ex_date_str:
                continue
            ex_date = parse_date(str(ex_date_str))
            if ex_date is None:
                continue

            if window_start and ex_date < window_start:
                continue
            if window_end and ex_date > window_end:
                continue

            total_distr = item.get("totalDistr")
            gross_amount = (
                parse_amount(str(total_distr)) if total_distr is not None else None
            )
            if gross_amount is None or gross_amount <= 0:
                continue

            rec_date_str = item.get("recordDate")
            pay_date_str = item.get("payDate")
            record_date = parse_date(str(rec_date_str)) if rec_date_str else None
            payable_date = parse_date(str(pay_date_str)) if pay_date_str else None

            reinv_distr = item.get("reInvDistr")
            reinv_amt = (
                parse_amount(str(reinv_distr)) if reinv_distr is not None else None
            )

            notes = "Extracted from embedded RBC fundData."
            if reinv_amt is not None and reinv_amt > 0:
                cash_amt = item.get("cashDistr")
                notes += f" Note: ${reinv_amt:.6f} was reinvested (non-cash); cash was ${cash_amt}."

            evt = ExtractedDistribution(
                fund_id=fund_id,
                country="CA",
                currency="CAD",
                ticker=ticker or data_ticker or None,
                fundserv_code=None,
                ex_date=ex_date,
                record_date=record_date,
                payable_date=payable_date,
                gross_amount=gross_amount,
                distribution_type="Income",
                components=[],
                source_url=source_url,
                extraction_route=ExtractionRoute.API,
                source_tier=source_tier,
                retrieved_at=now,
                raw_doc_snippet=f"RBC fundData: exDivDate={ex_date_str}, totalDistr={total_distr}, reInvDistr={reinv_distr}",
                validation_passed=True,
                validation_notes=notes,
            )
            events.append(evt)

    events.sort(key=lambda e: e.ex_date)
    return events
