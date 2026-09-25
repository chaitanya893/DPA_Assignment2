#!/usr/bin/env python3
"""Exploration script: Probe sponsor official source reachability across the 100 funds.

Loads config/universe_100.json, fetches each fund's official_source_url using the project's
HTTPClient (User-Agent with contact info, robots.txt, 2.5s pacing), checks for dated table markers,
and exports results to quality/source_reachability.csv and quality/source_reachability.md.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Ensure environment variable defaults for contact email if not set
if "DETECTOR_CONTACT_EMAIL" not in os.environ:
    os.environ["DETECTOR_CONTACT_EMAIL"] = "research@dpa.com"
if "DETECTOR_CONTACT_NAME" not in os.environ:
    os.environ["DETECTOR_CONTACT_NAME"] = "DPA Research"

from src.http_client import HTTPClient


def main() -> None:
    root_dir = Path(__file__).resolve().parent.parent
    universe_path = root_dir / "config" / "universe_100.json"
    quality_dir = root_dir / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)

    csv_path = quality_dir / "source_reachability.csv"
    md_path = quality_dir / "source_reachability.md"

    with open(universe_path, "r", encoding="utf-8") as f:
        universe_data = json.load(f)

    funds = universe_data if isinstance(universe_data, list) else universe_data.get("funds", [])
    print(f"Loaded {len(funds)} funds from {universe_path.name}. Probing reachability...")

    client = HTTPClient()
    results = []

    for idx, fund in enumerate(funds, start=1):
        fund_id = fund["fund_id"]
        fund_family = fund.get("fund_family", "Unknown")
        country = fund.get("country", "")
        url = fund["official_source_url"]
        domain = urlparse(url).netloc

        print(f"[{idx:3d}/{len(funds)}] Probing {fund_id} ({domain})...", end=" ", flush=True)
        rec = client.get(url)
        status = rec.status_code
        reason = rec.failure_reason.value if rec.failure_reason else ""
        text = rec.content_text or ""
        body_len = len(rec.content_bytes or b"")

        print(f"HTTP {status or 'ERR'} ({body_len} bytes)")

        # Markers
        has_vgn = "data-vgn-funds-profile" in text
        has_ex_date = "Ex-Date" in text
        has_ex_div = "Ex-Dividend" in text
        has_record = "Record Date" in text
        has_payable = "Payable" in text
        has_dist = "Distribution" in text
        has_table = "<table" in text.lower()

        has_dated_marker = (
            has_vgn
            or (has_table and (has_ex_date or has_ex_div or has_record or has_payable or has_dist))
            or (has_ex_date and has_dist)
        )

        results.append({
            "fund_id": fund_id,
            "fund_family": fund_family,
            "country": country,
            "domain": domain,
            "http_status": status if status is not None else "",
            "body_length": body_len,
            "failure_reason": reason,
            "data_vgn_funds_profile": has_vgn,
            "ex_date": has_ex_date,
            "ex_dividend": has_ex_div,
            "record_date": has_record,
            "payable": has_payable,
            "distribution": has_dist,
            "table_tag": has_table,
            "has_dated_table_marker": has_dated_marker,
        })

    # 1. Write CSV
    fieldnames = [
        "fund_id",
        "fund_family",
        "country",
        "domain",
        "http_status",
        "body_length",
        "failure_reason",
        "data_vgn_funds_profile",
        "ex_date",
        "ex_dividend",
        "record_date",
        "payable",
        "distribution",
        "table_tag",
        "has_dated_table_marker",
    ]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"\nSaved CSV to: {csv_path}")

    # 2. Compute Summary Grouped by fund_family
    summary_by_family: dict[str, dict[str, int]] = defaultdict(
        lambda: {"funds": 0, "reachable_200": 0, "blocked_403": 0, "other_error": 0, "dated_table": 0}
    )

    for r in results:
        fam = r["fund_family"]
        s = summary_by_family[fam]
        s["funds"] += 1
        st = r["http_status"]
        if st == 200:
            s["reachable_200"] += 1
        elif st == 403:
            s["blocked_403"] += 1
        else:
            s["other_error"] += 1

        if r["has_dated_table_marker"]:
            s["dated_table"] += 1

    # Totals
    total_funds = len(results)
    total_200 = sum(s["reachable_200"] for s in summary_by_family.values())
    total_403 = sum(s["blocked_403"] for s in summary_by_family.values())
    total_other = sum(s["other_error"] for s in summary_by_family.values())
    total_dated = sum(s["dated_table"] for s in summary_by_family.values())

    # Build Markdown Summary
    md_lines = [
        "# Sponsor Official Source Reachability Summary",
        "",
        f"Audited {total_funds} verified funds from universe using compliant `HTTPClient`.",
        "",
        "| Fund Family | Funds | Reachable (200) | Blocked (403) | Other Error | Pages with Dated Table Marker |",
        "| :--- | :---: | :---: | :---: | :---: | :---: |",
    ]

    for fam in sorted(summary_by_family.keys()):
        s = summary_by_family[fam]
        md_lines.append(
            f"| {fam} | {s['funds']} | {s['reachable_200']} | {s['blocked_403']} | {s['other_error']} | {s['dated_table']} |"
        )

    md_lines.append(
        f"| **TOTAL** | **{total_funds}** | **{total_200}** | **{total_403}** | **{total_other}** | **{total_dated}** |"
    )
    md_lines.append("")

    md_content = "\n".join(md_lines)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"Saved Markdown summary to: {md_path}\n")

    # 3. Print Summary to stdout
    print("=" * 90)
    print("                SPONSOR OFFICIAL SOURCE REACHABILITY SUMMARY")
    print("=" * 90)
    print(md_content)
    print("=" * 90)


if __name__ == "__main__":
    main()
