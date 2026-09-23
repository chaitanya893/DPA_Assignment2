"""Targeted consolidated live detection runner for the entire Universe of 100 Funds (60 US + 40 CA)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.detector import detect_distribution
from src.universe_loader import UniverseRegistry


def main() -> None:
    reg = UniverseRegistry.from_json()
    funds = reg.list_funds()
    us_funds = [f for f in funds if f.country == "US"]
    ca_funds = [f for f in funds if f.country == "CA"]

    # Load documented historical schedules to set optimal authenticated window
    us_sched_path = Path("config/us_distribution_schedules.json")
    ca_sched_path = Path("config/ca_distribution_schedules.json")
    sched_map = {}
    for p in (us_sched_path, ca_sched_path):
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                for item in json.load(f):
                    sched_map[item["fund_id"]] = item

    print("=" * 115)
    print("                LIVE LAYER-A DETECTION RUNNER — 100 FUNDS UNIVERSE AUDIT (60 US + 40 CA)")
    print("=" * 115)
    print(f"Total Funds in Universe: {len(funds)} (US: {len(us_funds)}, CA: {len(ca_funds)})")
    print("-" * 115)
    print(f"{'#':<4} | {'Country':<7} | {'Fund ID':<20} | {'Symbol':<8} | {'Window':<23} | {'Status':<12} | {'Evidence Summary'}")
    print("-" * 115)

    results = []
    status_counts = {"DECLARED": 0, "NOT_DECLARED": 0, "UNKNOWN": 0}
    country_counts = {
        "US": {"DECLARED": 0, "NOT_DECLARED": 0, "UNKNOWN": 0},
        "CA": {"DECLARED": 0, "NOT_DECLARED": 0, "UNKNOWN": 0},
    }

    for i, fund in enumerate(funds, 1):
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
        elif fund.expected_frequency == "QUARTERLY" and fund.country == "US":
            w_start = date(2024, 3, 1)
            w_end = date(2024, 3, 31)

        res = detect_distribution(
            fund_id=fund.fund_id,
            window_start=w_start,
            window_end=w_end,
            universe=reg,
        )

        status_str = res.status.value
        status_counts[status_str] = status_counts.get(status_str, 0) + 1
        country_counts[fund.country][status_str] = country_counts[fund.country].get(status_str, 0) + 1

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
        sym = fund.ticker or fund.fundserv_code or "N/A"
        print(
            f"{i:<4} | {fund.country:<7} | {fund.fund_id:<20} | {sym:<8} | {w_str:<23} | {status_str:<12} | {ev_summary}",
            flush=True,
        )

        results.append({
            "fund_id": fund.fund_id,
            "country": fund.country,
            "symbol": sym,
            "status": status_str,
            "window": w_str,
            "confidence": res.confidence,
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

    print("=" * 115)
    print("LIVE 100 FUNDS UNIVERSE AUDIT SUMMARY:")
    print(f"  * Total Funds Tested    : {len(funds)}")
    print(f"  * Overall DECLARED      : {status_counts['DECLARED']}")
    print(f"  * Overall NOT_DECLARED  : {status_counts['NOT_DECLARED']}")
    print(f"  * Overall UNKNOWN       : {status_counts['UNKNOWN']}")
    print("-" * 115)
    print(f"  * US Funds (60)  -> DECLARED: {country_counts['US']['DECLARED']}, NOT_DECLARED: {country_counts['US']['NOT_DECLARED']}, UNKNOWN: {country_counts['US']['UNKNOWN']}")
    print(f"  * CA Funds (40)  -> DECLARED: {country_counts['CA']['DECLARED']}, NOT_DECLARED: {country_counts['CA']['NOT_DECLARED']}, UNKNOWN: {country_counts['CA']['UNKNOWN']}")
    print("=" * 115)

    # Save to quality artifact
    out_path = Path("quality/universe_100_live_test_results.json")
    with out_path.open("w", encoding="utf-8") as f:
        json.dump({"summary": status_counts, "by_country": country_counts, "results": results}, f, indent=2)
    print(f"Saved live 100-fund results to {out_path}")


if __name__ == "__main__":
    main()
