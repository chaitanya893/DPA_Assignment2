"""Audit generator for Source Acquisition and Document Retrieval Layer.

Executes authenticated source acquisition and document inspection across representative
real US and Canadian funds, evaluates acquisition statuses, distribution candidates,
and emits machine-readable audit report to quality/source_acquisition_audit.json.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from src.http_client import HTTPClient
from src.models import SourceTier
from src.source_acquisition import (
    AcquisitionStatus,
    RawSourceCache,
    SourceAcquisitionEngine,
)
from src.source_discovery import SourceDiscoveryEngine
from src.universe_loader import UniverseRegistry


def audit_source_acquisition(
    universe_path: Path,
    output_path: Path,
    sample_fund_ids: list[str] | None = None,
    window_start: date = date(2026, 2, 1),
    window_end: date = date(2026, 2, 28),
) -> dict[str, Any]:
    """Execute acquisition audit on sample funds and emit audit JSON."""
    registry = UniverseRegistry.from_json(str(universe_path))
    client = HTTPClient(timeout_seconds=12.0, max_retries=1)
    cache = RawSourceCache()
    discovery_engine = SourceDiscoveryEngine(http_client=client)
    acquisition_engine = SourceAcquisitionEngine(http_client=client, cache=cache)

    if sample_fund_ids is None:
        sample_fund_ids = [
            "US_VANGUARD_VTI",
            "US_ISHARES_AGG",
            "US_PIMCO_BOND",
            "US_SPDR_SPAB",
            "US_FIDELITY_FXAIX",
            "CA_VANGUARD_VCN",
            "CA_BMO_ZCN",
            "CA_CI_FIE",
            "CA_RBC_RBF556",
            "CA_GLOBALX_HXT",
        ]

    total_candidates_attempted = 0
    status_counts = {s.value: 0 for s in AcquisitionStatus}
    dist_evidence_count = 0
    coverage_count = 0
    tier_counts = {
        SourceTier.TIER_1_AUTHORITATIVE.name: 0,
        SourceTier.TIER_2_PRIMARY_UNSTRUCTURED.name: 0,
    }

    fund_results: list[dict[str, Any]] = []

    for fid in sample_fund_ids:
        fund = registry.get_fund(fid)
        if not fund:
            continue

        candidates = discovery_engine.discover_candidates(
            fund, window_start, window_end
        )
        total_candidates_attempted += len(candidates)

        acq_results = acquisition_engine.acquire_for_fund(
            fund, candidates, window_start, window_end
        )

        cand_dicts: list[dict[str, Any]] = []
        for r in acq_results:
            status_counts[r.status.value] += 1
            if r.source_tier == SourceTier.TIER_1_AUTHORITATIVE:
                tier_counts[SourceTier.TIER_1_AUTHORITATIVE.name] += 1
            elif r.source_tier == SourceTier.TIER_2_PRIMARY_UNSTRUCTURED:
                tier_counts[SourceTier.TIER_2_PRIMARY_UNSTRUCTURED.name] += 1

            if r.is_distribution_evidence:
                dist_evidence_count += 1
            if r.is_coverage_evidence:
                coverage_count += 1

            cand_dicts.append(
                {
                    "source_id": r.source_id,
                    "source_tier": r.source_tier.value,
                    "url": r.url,
                    "http_status": r.http_status,
                    "content_hash": r.content_hash,
                    "status": r.status.value,
                    "is_usable": r.is_usable,
                    "is_distribution_evidence": r.is_distribution_evidence,
                    "is_coverage_evidence": r.is_coverage_evidence,
                    "failure_reason": r.failure_reason,
                    "extracted_evidence_candidates": r.extracted_evidence_candidates,
                }
            )

        fund_results.append(
            {
                "fund_id": fund.fund_id,
                "fund_name": fund.fund_name,
                "ticker": fund.ticker,
                "country": fund.country,
                "acquisitions": cand_dicts,
            }
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "audit_version": "1.0",
        "inspection_window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
        },
        "summary": {
            "funds_audited": len(sample_fund_ids),
            "candidates_attempted": total_candidates_attempted,
            "total_documents_inspected": len(
                [a for f in fund_results for a in f["acquisitions"]]
            ),
            "acquisition_status_breakdown": status_counts,
            "tier_breakdown": tier_counts,
            "direct_distribution_candidates_found": dist_evidence_count,
            "coverage_schedule_candidates_found": coverage_count,
        },
        "fund_details": fund_results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    return report


def print_audit_summary(report: dict[str, Any]) -> None:
    """Print readable acquisition audit summary to console."""
    summary = report["summary"]
    print("=" * 75)
    print("PHASE 1 — SOURCE ACQUISITION & DOCUMENT RETRIEVAL AUDIT")
    print("=" * 75)
    print(f"Generated At:                     {report['generated_at']}")
    print(
        f"Inspection Window:                {report['inspection_window']['start']} to {report['inspection_window']['end']}"
    )
    print(f"Funds Audited:                    {summary['funds_audited']}")
    print(f"Candidates Attempted:             {summary['candidates_attempted']}")
    print(f"Total Documents Inspected:        {summary['total_documents_inspected']}")
    print("-" * 75)
    print("ACQUISITION STATUS BREAKDOWN:")
    for status, count in summary["acquisition_status_breakdown"].items():
        print(f"  - {status:<32}: {count}")
    print("-" * 75)
    print("EVIDENCE RECOVERY:")
    print(
        f"  - Direct Distribution Evidence Candidates : {summary['direct_distribution_candidates_found']}"
    )
    print(
        f"  - Exhaustive Coverage Schedule Candidates : {summary['coverage_schedule_candidates_found']}"
    )
    print("=" * 75)


def main() -> None:
    os.environ["DETECTOR_USER_AGENT"] = "ChaitanyaResearch chaitanya893@gmail.com"
    project_root = Path(__file__).resolve().parent.parent
    universe_path = project_root / "config" / "universe_100.json"
    output_path = project_root / "quality" / "source_acquisition_audit.json"

    report = audit_source_acquisition(universe_path, output_path)
    print_audit_summary(report)
    print(f"\nMachine-readable audit written to: {output_path}")


if __name__ == "__main__":
    main()
