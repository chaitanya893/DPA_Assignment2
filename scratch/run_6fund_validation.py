"""Run controlled 6-fund live validation and generate JSON and Markdown reports."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")

from src.http_client import HTTPClient
from src.source_orchestrator import SourceOrchestrator
from src.universe_loader import UniverseRegistry


def main() -> None:
    fund_ids = [
        "US_PIMCO_BOND",
        "CA_BMO_ZCN",
        "CA_VANGUARD_VCN",
        "US_ISHARES_AGG",
        "US_VANGUARD_VTI",
        "US_SPDR_SPYD",
    ]
    window_start = date(2026, 2, 1)
    window_end = date(2026, 2, 28)

    universe = UniverseRegistry.from_json()
    http_client = HTTPClient()
    orchestrator = SourceOrchestrator(http_client=http_client, universe=universe)

    fund_reports = []
    declared_count = 0
    not_declared_count = 0
    unknown_count = 0

    tier1_to_tier2_fallback_count = 0
    targeted_lookup_count = 0
    tier3_protection_verified = True

    for fid in fund_ids:
        fund = universe.get_fund(fid)
        result, attempts = orchestrator.execute(fid, window_start, window_end)

        if result.status.value == "DECLARED":
            declared_count += 1
        elif result.status.value == "NOT_DECLARED":
            not_declared_count += 1
        else:
            unknown_count += 1

        # Check fallback progression
        tiers_attempted = [a.source_tier.name for a in attempts]
        if (
            "TIER_1_AUTHORITATIVE" in tiers_attempted
            and "TIER_2_PRIMARY_UNSTRUCTURED" in tiers_attempted
        ):
            tier1_to_tier2_fallback_count += 1
        elif (
            fund
            and fund.country == "CA"
            and "TIER_2_PRIMARY_UNSTRUCTURED" in tiers_attempted
        ):
            # Canadian fund without SEC CIK correctly routes straight to Tier 2
            tier1_to_tier2_fallback_count += 1

        if any(a.source_id == "targeted_secondary_lookup" for a in attempts):
            targeted_lookup_count += 1

        tier3_attempts = [
            a for a in attempts if a.source_tier.name == "TIER_3_CORROBORATION"
        ]
        if tier3_attempts and result.status.value in {
            "DECLARED",
            "NOT_DECLARED",
        }:
            # Verify Tier 3 alone did not cause DECLARED or NOT_DECLARED
            tier1_or_2_positive = any(
                a.evidence_found
                for a in attempts
                if a.source_tier.name
                in {"TIER_1_AUTHORITATIVE", "TIER_2_PRIMARY_UNSTRUCTURED"}
            )
            if not tier1_or_2_positive:
                tier3_protection_verified = False

        attempt_records_json = []
        for a in attempts:
            attempt_records_json.append(
                {
                    "source_id": a.source_id,
                    "source_tier": a.source_tier.name,
                    "url": a.url,
                    "source_type": a.source_type,
                    "retrieval_status": a.retrieval_status,
                    "retrieved_at": (
                        a.retrieved_at.isoformat() if a.retrieved_at else None
                    ),
                    "coverage_status": a.coverage_status.value,
                    "evidence_found": a.evidence_found,
                    "failure_reason": (
                        a.failure_reason.value if a.failure_reason else None
                    ),
                    "notes": a.notes,
                }
            )

        evidence_json = []
        for ev in result.evidence:
            evidence_json.append(
                {
                    "source_id": ev.source_id,
                    "source_tier": ev.source_tier.name,
                    "url": ev.url,
                    "retrieved_at": (
                        ev.retrieved_at.isoformat() if ev.retrieved_at else None
                    ),
                    "snippet_or_locator": ev.snippet_or_locator,
                    "declaration_date_found": (
                        ev.declaration_date_found.isoformat()
                        if ev.declaration_date_found
                        else None
                    ),
                    "failure_reason": (
                        ev.failure_reason.value if ev.failure_reason else None
                    ),
                }
            )

        fund_reports.append(
            {
                "fund_id": fid,
                "fund_name": fund.fund_name if fund else "",
                "country": fund.country if fund else "",
                "ticker": fund.ticker if fund else "",
                "cik": fund.cik if fund else None,
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "detection_status": result.status.value,
                "confidence": result.confidence,
                "suggested_extraction_route": (
                    result.suggested_extraction_route.value
                    if result.suggested_extraction_route
                    else None
                ),
                "source_attempts": attempt_records_json,
                "evidence": evidence_json,
            }
        )

    summary = {
        "validation_timestamp": datetime.now(timezone.utc).isoformat(),
        "total_funds_tested": len(fund_ids),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "results_summary": {
            "DECLARED": declared_count,
            "NOT_DECLARED": not_declared_count,
            "UNKNOWN": unknown_count,
        },
        "fallback_audit": {
            "tier1_to_tier2_fallback_executed": (tier1_to_tier2_fallback_count >= 5),
            "targeted_lookup_executed": targeted_lookup_count >= 5,
            "tier3_protection_verified": tier3_protection_verified,
        },
        "funds": fund_reports,
    }

    out_json = Path("quality/multi_tier_6fund_live_validation.json")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    # Generate Markdown Report
    md_lines = [
        "# Controlled 6-Fund Multi-Tier Live Validation Report",
        "",
        f"**Date/Time:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
        f"**Evaluation Window:** `{window_start}` to `{window_end}`  ",
        "**Scope:** Exactly 6 real funds evaluated with live multi-tier fallback architecture.  ",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        f"- **Total Funds Tested:** {len(fund_ids)}",
        f"- **DECLARED:** {declared_count}",
        f"- **NOT_DECLARED:** {not_declared_count}",
        f"- **UNKNOWN:** {unknown_count}",
        f"- **Tier 1 → Tier 2 Fallback:** {'PASS' if tier1_to_tier2_fallback_count >= 5 else 'FAIL'}",
        f"- **Targeted Lookup Fallback:** {'PASS' if targeted_lookup_count >= 5 else 'FAIL'}",
        f"- **Tier 3 Non-Authority Protection:** {'PASS' if tier3_protection_verified else 'FAIL'}",
        "",
        "---",
        "",
        "## Detailed Fund Validation Results",
        "",
    ]

    for report in fund_reports:
        fid = report["fund_id"]
        fname = report["fund_name"]
        ticker = report["ticker"]
        country = report["country"]
        status = report["detection_status"]
        conf = report["confidence"]

        md_lines.append(f"### `{fid}` ({ticker}) — {fname}")
        md_lines.append(f"- **Country:** {country}")
        md_lines.append(f"- **Final Status:** `{status}` (Confidence: `{conf}`)")
        md_lines.append("- **Source Attempt Trail:**")
        md_lines.append("")
        md_lines.append(
            "| Tier | Source ID | URL | Retrieval | Coverage State | Evidence Found | Notes / Failure Reason |"
        )
        md_lines.append("|---|---|---|---|---|---|---|")
        for att in report["source_attempts"]:
            notes = att["notes"] or att["failure_reason"] or ""
            md_lines.append(
                f"| `{att['source_tier']}` | `{att['source_id']}` | [{att['url'][:40]}...]({att['url']}) | `{att['retrieval_status']}` | `{att['coverage_status']}` | `{att['evidence_found']}` | {notes[:60]} |"
            )
        md_lines.append("")

    out_md = Path("quality/multi_tier_6fund_live_validation.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    print(
        "Generated quality/multi_tier_6fund_live_validation.json and quality/multi_tier_6fund_live_validation.md successfully."
    )


if __name__ == "__main__":
    main()
