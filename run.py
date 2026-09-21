"""Main runner script for inspecting the Fund Universe and Layer A Detector.

Usage:
    python run.py
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.detector import detect_distribution


def main() -> None:
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
    print("Running Layer A Atomic Detector Sample:")
    sample_fund = funds[0]["fund_id"]
    result = detect_distribution(sample_fund, date(2026, 2, 1), date(2026, 2, 28))
    print(f"  * Target Fund:        {result.fund_id}")
    print(f"  * Evaluation Window:  {result.window_start} to {result.window_end}")
    print(f"  * Detection Status:   {result.status.value}")
    print(f"  * Evidence Count:     {len(result.evidence)}")
    print(
        f"  * Primary Reason:     {result.evidence[0].failure_reason.value if result.evidence[0].failure_reason else 'None'}"
    )
    print("=" * 70)


if __name__ == "__main__":
    main()
