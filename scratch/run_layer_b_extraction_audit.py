"""Targeted Layer B Extraction Audit Runner on all 40 DECLARED Funds."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.detector import detect_distribution
from src.extractor import extract_distribution
from src.universe_loader import UniverseRegistry


def main() -> None:
    reg = UniverseRegistry.from_json()
    funds = reg.list_funds()

    print("=" * 115)
    print("           LAYER-B HIGH-PRECISION EXTRACTION AUDIT (ASSIGNMENT 2 SPECIFICATION)")
    print("=" * 115)
    print("Extracting exact $/share amounts, date types, and tax components for all DECLARED funds...")
    print("-" * 115)
    print(f"{'#':<3} | {'Fund ID':<20} | {'Country':<7} | {'Symbol':<8} | {'Ex-Date':<10} | {'Gross Amt':<12} | {'Route':<10} | {'Components'}")
    print("-" * 115)

    extracted_records = []
    total_extracted_events = 0

    for fund in funds:
        # Run Layer A detector over December 2024 active window
        w_start = date(2024, 12, 1)
        w_end = date(2024, 12, 31)
        if fund.expected_frequency == "QUARTERLY" and fund.country == "US":
            w_start = date(2024, 3, 1)
            w_end = date(2024, 3, 31)

        detection = detect_distribution(
            fund_id=fund.fund_id,
            window_start=w_start,
            window_end=w_end,
            universe=reg,
        )

        # Layer B is ONLY invoked when Layer A returns DECLARED
        if detection.status.value != "DECLARED":
            continue

        events = extract_distribution(detection, universe=reg)
        for ev in events:
            total_extracted_events += 1
            comp_strs = [f"{c.component_name} ({c.amount:.4f})" for c in ev.components]
            comp_summary = ", ".join(comp_strs) if comp_strs else "Standard"

            amt_str = f"${ev.gross_amount:.4f} {ev.currency}"
            sym = ev.ticker or ev.fundserv_code or "N/A"
            print(
                f"{total_extracted_events:<3} | {ev.fund_id:<20} | {ev.country:<7} | {sym:<8} | {str(ev.ex_date):<10} | {amt_str:<12} | {ev.extraction_route.value:<10} | {comp_summary}",
                flush=True,
            )

            extracted_records.append({
                "fund_id": ev.fund_id,
                "country": ev.country,
                "symbol": sym,
                "currency": ev.currency,
                "gross_amount": ev.gross_amount,
                "distribution_type": ev.distribution_type,
                "ex_date": str(ev.ex_date),
                "record_date": str(ev.record_date) if ev.record_date else None,
                "payable_date": str(ev.payable_date) if ev.payable_date else None,
                "declaration_date": str(ev.declaration_date) if ev.declaration_date else None,
                "route": ev.extraction_route.value,
                "source_url": ev.source_url,
                "components": [
                    {
                        "name": c.component_name,
                        "type": str(c.component_type.value) if hasattr(c.component_type, "value") else str(c.component_type),
                        "amount": c.amount,
                        "percentage": c.percentage,
                    }
                    for c in ev.components
                ]
            })

    print("=" * 115)
    print("LAYER-B EXTRACTION SUMMARY:")
    print(f"  * Total DECLARED Funds Processed : {len(set(r['fund_id'] for r in extracted_records))}")
    print(f"  * Total Distribution Events Extracted : {len(extracted_records)}")
    print("=" * 115)

    out_file = Path("quality/layer_b_extracted_events.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({"total_events": len(extracted_records), "events": extracted_records}, f, indent=2)
    print(f"Saved Layer B extracted events to {out_file}")


if __name__ == "__main__":
    main()
