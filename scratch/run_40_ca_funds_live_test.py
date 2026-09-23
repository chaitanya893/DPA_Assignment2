"""Targeted live detection runner for the 40 Canadian Funds."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.detector import detect_distribution
from src.universe_loader import UniverseRegistry


def main() -> None:
    reg = UniverseRegistry.from_json()
    ca_funds = [f for f in reg.list_funds() if f.country == "CA"]

    ca_sched_path = Path("config/ca_distribution_schedules.json")
    sched_map = {}
    if ca_sched_path.exists():
        with ca_sched_path.open("r", encoding="utf-8") as f:
            for item in json.load(f):
                sched_map[item["fund_id"]] = item

    print("=" * 105)
    print("                LIVE LAYER-A DETECTION RUNNER — 40 CANADIAN FUNDS AUDIT")
    print("=" * 105)
    print(f"Total Canadian Funds to test: {len(ca_funds)}")
    print("-" * 105)
    print(f"{'#':<3} | {'Fund ID':<20} | {'Ticker/Code':<11} | {'Window':<23} | {'Status':<12} | {'Evidence Summary'}")
    print("-" * 105)

    results = []
    status_counts = {"DECLARED": 0, "NOT_DECLARED": 0, "UNKNOWN": 0}

    for i, fund in enumerate(ca_funds, 1):
        # Determine appropriate authenticated window
        w_start = date(2024, 12, 1)
        w_end = date(2024, 12, 31)

        sched_entry = sched_map.get(fund.fund_id)
        if sched_entry and sched_entry.get("events"):
            ev0 = sched_entry["events"][0]
            ev_date_str = ev0.get("ex_date") or ev0.get("payable_date") or ev0.get("record_date")
            if ev_date_str:
                d = date.fromisoformat(ev_date_str)
                w_start = date(d.year, d.month, 1)
                if d.month in {1, 3, 5, 7, 8, 10, 12}:
                    w_end = date(d.year, d.month, 31)
                elif d.month in {4, 6, 9, 11}:
                    w_end = date(d.year, d.month, 30)
                else:
                    w_end = date(d.year, d.month, 29 if d.year % 4 == 0 else 28)

        res = detect_distribution(
            fund_id=fund.fund_id,
            window_start=w_start,
            window_end=w_end,
            universe=reg,
        )

        status_str = res.status.value
        status_counts[status_str] = status_counts.get(status_str, 0) + 1

        ev_summary = "No declaration found"
        if res.status.value == "DECLARED" and res.evidence:
            ev = res.evidence[0]
            ev_summary = f"Declared: {ev.declaration_date_found or ev.ex_date_found} via {ev.source_id}"
        elif res.status.value == "UNKNOWN":
            reasons = [e.failure_reason.value for e in res.evidence if e.failure_reason]
            ev_summary = f"Unknown ({', '.join(set(reasons)) or 'insufficient data'})"
        elif res.status.value == "NOT_DECLARED":
            ev_summary = "Exhaustive schedule verified with 0 declarations"

        w_str = f"{w_start} to {w_end}"
        symbol = fund.ticker or fund.fundserv_code or "N/A"
        print(
            f"{i:<3} | {fund.fund_id:<20} | {symbol:<11} | {w_str:<23} | {status_str:<12} | {ev_summary}",
            flush=True,
        )

        results.append({
            "fund_id": fund.fund_id,
            "ticker": fund.ticker,
            "fundserv_code": fund.fundserv_code,
            "status": status_str,
            "window": w_str,
            "evidence_count": len(res.evidence),
            "evidence": [
                {
                    "source_id": e.source_id,
                    "source_tier": e.source_tier.value,
                    "url": e.url,
                    "decl_date": str(e.declaration_date_found) if e.declaration_date_found else None,
                    "snippet": e.snippet_or_locator,
                }
                for e in res.evidence
            ]
        })

    print("=" * 105)
    print("LIVE 40 CANADIAN FUNDS SUMMARY:")
    print(f"  * Total CA Funds Tested : {len(ca_funds)}")
    print(f"  * DECLARED              : {status_counts['DECLARED']}")
    print(f"  * NOT_DECLARED          : {status_counts['NOT_DECLARED']}")
    print(f"  * UNKNOWN               : {status_counts['UNKNOWN']}")
    print("=" * 105)

    # Save to quality artifact
    out_path = Path("quality/ca_40_live_test_results.json")
    with out_path.open("w", encoding="utf-8") as f:
        json.dump({"summary": status_counts, "results": results}, f, indent=2)
    print(f"Saved live results to {out_path}")


if __name__ == "__main__":
    main()
