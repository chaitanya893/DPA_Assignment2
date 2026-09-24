"""Data Quality Audit CLI Runner.

Executes all 7 validation rules (6 per event + monthly continuity per fund) on the database and exports structured DQ report.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.validators.dq_engine import DataQualityEngine


def main() -> None:
    print("=" * 110)
    print("           DATA QUALITY & VALIDATION ENGINE AUDIT (PHASE 4 SPECIFICATION)")
    print("=" * 110)
    print(
        "Running 7 deterministic validation checks across database distribution records..."
    )
    print("-" * 110)

    engine = DataQualityEngine()
    report = engine.audit_database(log_to_db=True)

    print(f"Total Events Evaluated        : {report.total_events_evaluated}")
    print(f"Checks executed               : {report.total_checks_performed}")
    print(
        f"Checks skipped (no input data): {report.skipped_checks}  <- not counted as passes"
    )
    print(f"Total Flags Raised            : {report.total_flags_raised}")
    print(f"  - Critical Flags            : {report.critical_flags}")
    print(f"  - Warning Flags             : {report.warning_flags}")
    print(f"  - Info Flags                : {report.info_flags}")
    print("-" * 110)
    rate = (
        "n/a (no checks executed)"
        if report.pass_rate_pct is None
        else f"{report.pass_rate_pct:.2f}%"
    )
    print(f"Pass rate over executed checks: {rate}")
    print("=" * 110)

    print("\nRULE-BY-RULE EXECUTION METRICS:")
    print("-" * 110)
    print(f"{'Rule Name':<35} | {'Passed':<8} | {'Failed':<8} | {'Skipped':<8}")
    print("-" * 110)
    for rule_name, counts in report.rule_metrics.items():
        print(
            f"{rule_name:<35} | {counts['passed']:<8} | {counts['failed']:<8} | {counts.get('skipped', 0):<8}"
        )
    print("=" * 110)

    # Export report to JSON
    export_dir = Path(__file__).parent.parent.parent / "data" / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    report_file = export_dir / "dq_audit_report.json"

    export_data = {
        "total_events_evaluated": report.total_events_evaluated,
        "total_checks_performed": report.total_checks_performed,
        "skipped_checks": report.skipped_checks,
        "total_flags_raised": report.total_flags_raised,
        "critical_flags": report.critical_flags,
        "warning_flags": report.warning_flags,
        "info_flags": report.info_flags,
        "pass_rate_pct": report.pass_rate_pct,
        "rule_metrics": report.rule_metrics,
        "flagged_events": report.flagged_events,
    }
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(export_data, f, indent=2)

    print(f"\nStructured Data Quality Audit Report exported to: {report_file}\n")


if __name__ == "__main__":
    main()
