"""Main runner script for inspecting the Fund Universe and Layer A Detector.

Usage:
    python run.py
    python run.py --mode 5months
    python run.py --mode 24months --as-of 2026-09-22
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

from src.detector import detect_distribution
from src.source_orchestrator import (
    SourceOrchestrator,
    get_active_5month_windows,
    get_dynamic_24month_window,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fund Distribution Detection Engine — Phase 1 Runner"
    )
    parser.add_argument(
        "--mode",
        choices=["1month", "5months", "24months"],
        default="1month",
        help="Detection sweep mode: '1month' (Feb default), '5months' (Active Feb+Quarterly cycles), '24months' (Dynamic 24-month rolling lookback)",
    )
    parser.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="Optional reference date (YYYY-MM-DD) for rolling window calculations (default: today).",
    )
    parser.add_argument(
        "--fund-id",
        type=str,
        default=None,
        help="Optional specific fund ID to inspect (default: first fund in universe).",
    )
    args = parser.parse_args()

    ref_date = (
        date.fromisoformat(args.as_of)
        if args.as_of
        else datetime.now(timezone.utc).date()
    )

    universe_path = Path("config/universe_100.json")
    if not universe_path.exists():
        print("Universe file not found at config/universe_100.json")
        return

    with universe_path.open("r", encoding="utf-8") as f:
        funds = json.load(f)

    print("=" * 70)
    print("  FUND DISTRIBUTION DETECTION ENGINE - PHASE 1 UNIVERSE")
    print("=" * 70)
    print(f"Total Verified Funds: {len(funds)}")
    us_count = sum(1 for f in funds if f.get("country") == "US")
    ca_count = sum(1 for f in funds if f.get("country") == "CA")
    etf_count = sum(1 for f in funds if f.get("is_etf"))
    monthly_count = sum(1 for f in funds if f.get("is_monthly_payer"))

    print(f"  * US Funds:           {us_count}")
    print(f"  * Canadian Funds:     {ca_count}")
    print(f"  * ETFs:               {etf_count}")
    print(f"  * Monthly Payers:     {monthly_count}")
    print("-" * 70)
    print("Sample Top 10 Verified Funds:")
    print("-" * 70)
    for i, fund in enumerate(funds[:10], start=1):
        ticker = fund.get("ticker") or fund.get("fundserv_code") or "N/A"
        print(
            f" {i:2d}. [{fund.get('country')}] {ticker:<8} | {fund.get('fund_name')[:35]:<35} | {fund.get('fund_family')}"
        )

    print("=" * 70)
    target_fund_id = args.fund_id or funds[0]["fund_id"]
    print(
        f"Running Layer A Detector (Mode: {args.mode.upper()}, Reference Date: {ref_date}):"
    )

    orchestrator = SourceOrchestrator()

    if args.mode == "24months":
        w_start, w_end = get_dynamic_24month_window(ref_date)
        sweep_res = orchestrator.execute_sweep(
            target_fund_id, mode="24months", as_of=ref_date
        )
        print(f"  * Target Fund:        {target_fund_id}")
        print(f"  * Dynamic 24M Window: {w_start} to {w_end}")
        print(f"  * Overall Status:     {sweep_res['overall_status']}")
        print(f"  * Source Attempts:    {len(sweep_res['source_attempts'])}")
        print(f"  * Suggested Route:    {sweep_res.get('suggested_extraction_route')}")

    elif args.mode == "5months":
        cycles = get_active_5month_windows(ref_date.year)
        sweep_res = orchestrator.execute_sweep(
            target_fund_id, mode="5months", as_of=ref_date
        )
        print(f"  * Target Fund:        {target_fund_id}")
        print(
            f"  * Active Cycles:      {len(cycles)} cycles (Feb + Q1-Q4 {ref_date.year})"
        )
        print(f"  * Overall Status:     {sweep_res['overall_status']}")
        print("  * Cycle Breakdown:")
        for c in sweep_res["cycle_results"]:
            print(
                f"    - {c['cycle_name']:<15} [{c['window_start']} to {c['window_end']}]: {c['status']}"
            )
        print(f"  * Total Attempts:     {len(sweep_res['source_attempts'])}")

    else:
        # Default 1month (Feb)
        result = detect_distribution(
            target_fund_id, date(2026, 2, 1), date(2026, 2, 28)
        )
        print(f"  * Target Fund:        {result.fund_id}")
        print(f"  * Evaluation Window:  {result.window_start} to {result.window_end}")
        print(f"  * Detection Status:   {result.status.value}")
        print(f"  * Evidence Count:     {len(result.evidence)}")
        print(
            f"  * Primary Reason:     {result.evidence[0].failure_reason.value if result.evidence and result.evidence[0].failure_reason else 'None'}"
        )
    print("=" * 70)


if __name__ == "__main__":
    main()
