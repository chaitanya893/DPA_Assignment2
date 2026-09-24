"""Structured feed / API parser for Layer B (route API).

Finds lists of records in a JSON document and maps their keys to the same labelled
columns the table parser understands (``exDate`` -> "ex date", ``payDate`` -> "pay date",
``amount`` -> "amount"...). Nothing is inferred beyond what the keys say.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from src.models import ExtractedDistribution, ExtractionRoute, SourceTier
from src.parsers.html_table_parser import parse_table_rows


def _key_to_header(key: str) -> str:
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", key)
    return spaced.replace("_", " ").replace("-", " ").lower().strip()


def _record_lists(obj: Any) -> list[list[dict[str, Any]]]:
    found: list[list[dict[str, Any]]] = []
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        found.append(obj)
    if isinstance(obj, dict):
        for v in obj.values():
            found.extend(_record_lists(v))
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)):
                found.extend(_record_lists(v))
    return found


def parse_json_distributions(
    payload: str | bytes | Any,
    fund_id: str,
    country: str,
    source_url: str,
    ticker: str | None = None,
    fundserv_code: str | None = None,
    window_start: date | None = None,
    window_end: date | None = None,
    source_tier: SourceTier = SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
) -> list[ExtractedDistribution]:
    if isinstance(payload, (str, bytes)):
        try:
            obj = json.loads(payload)
        except (ValueError, TypeError):
            return []
    else:
        obj = payload
    rows: list[tuple[list[str], list[str]]] = []
    for records in _record_lists(obj):
        for rec in records:
            flat = {k: v for k, v in rec.items() if not isinstance(v, (dict, list))}
            headers = [_key_to_header(k) for k in flat]
            cells = ["" if v is None else str(v) for v in flat.values()]
            rows.append((headers, cells))
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
        extraction_route=ExtractionRoute.API,
    )
