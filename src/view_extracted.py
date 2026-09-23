"""Interactive viewer for the 33 Layer-B Extracted Distribution Events and Evidence."""

from __future__ import annotations

import json
from pathlib import Path


def display_extracted_33() -> None:
    json_path = Path(__file__).parent.parent / "quality" / "layer_b_extracted_events.json"
    if not json_path.exists():
        print(f"Error: {json_path} not found.")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    events = data.get("events", [])
    print("=" * 125)
    print(f"               LAYER-B EXTRACTED DISTRIBUTION EVIDENCE (TOTAL {len(events)} FUNDS EXTRACTED)")
    print("=" * 125)
    print(
        f"{'#':<3} | {'Fund ID':<20} | {'Ticker':<7} | {'Ex-Date':<10} | {'Pay Date':<10} | {'Gross Amount':<14} | {'Route':<11} | {'Source URL'}"
    )
    print("-" * 125)

    for idx, ev in enumerate(events, 1):
        sym = ev.get("symbol") or "N/A"
        ex_d = ev.get("ex_date") or "N/A"
        pay_d = ev.get("payable_date") or "N/A"
        amt = f"${ev.get('gross_amount', 0.0):.4f} {ev.get('currency', 'USD')}"
        route = ev.get("route") or "HTML_TABLE"
        src_url = ev.get("source_url") or "N/A"

        print(f"{idx:<3} | {ev.get('fund_id', ''):<20} | {sym:<7} | {ex_d:<10} | {pay_d:<10} | {amt:<14} | {route:<11} | {src_url}")

    print("=" * 125)
    print(f"\nTotal Extracted Funds with High-Precision Evidence: {len(events)}")
    print(f"Detailed JSON File Location: quality/layer_b_extracted_events.json")

    # Also save to clean CSV for Senior
    export_dir = Path(__file__).parent.parent / "data" / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    csv_file = export_dir / "layer_b_33_extracted_evidence.csv"
    with open(csv_file, "w", encoding="utf-8") as f:
        f.write("idx,fund_id,symbol,country,ex_date,record_date,payable_date,gross_amount,currency,route,source_url\n")
        for idx, ev in enumerate(events, 1):
            f.write(
                f"{idx},{ev.get('fund_id','')},{ev.get('symbol','')},{ev.get('country','')},{ev.get('ex_date','')},{ev.get('record_date','')},{ev.get('payable_date','')},{ev.get('gross_amount',0.0)},{ev.get('currency','')},{ev.get('route','')},{ev.get('source_url','')}\n"
            )
    print(f"Generated Clean CSV for Senior: {csv_file}\n")


if __name__ == "__main__":
    display_extracted_33()
