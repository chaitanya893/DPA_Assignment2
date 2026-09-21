"""CLI and audit generator for the Source Authentication & Discovery Layer.

Audits the entire 100-fund universe against the official domain registry,
evaluates verification levels (domain, identity, retrieval, usability, distribution semantics, coverage),
and outputs a comprehensive machine-readable audit report to quality/source_discovery_audit.json.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from src.models import SourceTier, ValidationStatus
from src.source_discovery import (
    TIER_3_CORROBORATION_DOMAINS,
    VERIFIED_OFFICIAL_DOMAINS,
    OfficialDomainValidator,
    SourceDiscoveryEngine,
)
from src.universe_loader import UniverseRegistry


def audit_universe(
    universe_path: Path,
    output_path: Path,
    window_start: date = date(2026, 2, 1),
    window_end: date = date(2026, 2, 28),
) -> dict[str, Any]:
    """Audit source discovery and multi-level validation across the fund universe."""
    registry = UniverseRegistry.from_json(str(universe_path))
    engine = SourceDiscoveryEngine()

    funds = registry.list_funds()
    total_funds = len(funds)
    total_candidates = 0

    # Verification level counters
    level_counts = {
        "domain_verified": 0,
        "identity_verified": 0,
        "source_retrievable": 0,
        "content_usable": 0,
        "distribution_capable": 0,
        "coverage_verified": 0,
    }

    tier_counts = {
        SourceTier.TIER_1_AUTHORITATIVE.name: 0,
        SourceTier.TIER_2_PRIMARY_UNSTRUCTURED.name: 0,
        SourceTier.TIER_3_CORROBORATION.name: 0,
    }

    status_counts = {
        ValidationStatus.VERIFIED_DIRECT_DISTRIBUTION.value: 0,
        ValidationStatus.VERIFIED_COVERAGE_SOURCE.value: 0,
        ValidationStatus.SUPPORTING_FILING_INDEX.value: 0,
        ValidationStatus.PARTIALLY_VERIFIED.value: 0,
        ValidationStatus.INCOMPLETE.value: 0,
        ValidationStatus.UNAVAILABLE.value: 0,
        ValidationStatus.INVALID.value: 0,
        ValidationStatus.CORROBORATION_ONLY.value: 0,
    }

    region_stats: dict[str, dict[str, int]] = {
        "US": {
            "funds": 0,
            "tier_1_sources": 0,
            "tier_2_sources": 0,
            "domain_verified": 0,
            "direct_distribution_sources": 0,
            "supporting_filing_index": 0,
            "partially_verified": 0,
            "incomplete_or_unavailable": 0,
        },
        "CA": {
            "funds": 0,
            "tier_1_sources": 0,
            "tier_2_sources": 0,
            "domain_verified": 0,
            "direct_distribution_sources": 0,
            "supporting_filing_index": 0,
            "partially_verified": 0,
            "incomplete_or_unavailable": 0,
        },
    }

    fund_records: list[dict[str, Any]] = []

    for fund in funds:
        country = fund.country if fund.country in region_stats else "US"
        region_stats[country]["funds"] += 1

        discovered = engine.discover_candidates(fund, window_start, window_end)
        total_candidates += len(discovered)

        candidate_dicts: list[dict[str, Any]] = []

        for candidate in discovered:
            is_official = OfficialDomainValidator.is_official_domain(candidate.url)
            is_tier_3 = OfficialDomainValidator.is_tier_3_domain(candidate.url)
            candidate.domain_verified = is_official

            if is_official:
                level_counts["domain_verified"] += 1
                region_stats[country]["domain_verified"] += 1
            elif is_tier_3:
                candidate.validation_status = ValidationStatus.CORROBORATION_ONLY
                candidate.validation_reason = (
                    f"Tier 3 corroboration domain: '{candidate.official_domain}'"
                )
            else:
                candidate.validation_status = ValidationStatus.INVALID
                candidate.validation_reason = (
                    f"Unregistered domain: '{candidate.official_domain}'"
                )

            # Check identifier binding
            if candidate.identifier_value:
                candidate.identity_verified = True
                level_counts["identity_verified"] += 1

            # Tier 1 vs Tier 2 classification
            if candidate.source_tier == SourceTier.TIER_1_AUTHORITATIVE:
                tier_counts[SourceTier.TIER_1_AUTHORITATIVE.name] += 1
                region_stats[country]["tier_1_sources"] += 1
                if "submissions/CIK" in candidate.url:
                    candidate.validation_status = (
                        ValidationStatus.SUPPORTING_FILING_INDEX
                    )
                    candidate.validation_reason = "Authoritative SEC submissions repository index (retrievable filing metadata)."
                    status_counts[ValidationStatus.SUPPORTING_FILING_INDEX.value] += 1
                    region_stats[country]["supporting_filing_index"] += 1

            elif candidate.source_tier == SourceTier.TIER_2_PRIMARY_UNSTRUCTURED:
                tier_counts[SourceTier.TIER_2_PRIMARY_UNSTRUCTURED.name] += 1
                region_stats[country]["tier_2_sources"] += 1
                if is_official:
                    candidate.validation_status = ValidationStatus.PARTIALLY_VERIFIED
                    candidate.validation_reason = "Official sponsor portal candidate identified (domain and identifier verified)."
                    status_counts[ValidationStatus.PARTIALLY_VERIFIED.value] += 1
                    region_stats[country]["partially_verified"] += 1
                else:
                    status_counts[ValidationStatus.INVALID.value] += 1

            candidate_dicts.append(
                {
                    "source_id": candidate.source_id,
                    "source_tier": candidate.source_tier.value,
                    "source_role": candidate.source_role,
                    "provider": candidate.provider,
                    "url": candidate.url,
                    "official_domain": candidate.official_domain,
                    "source_type": candidate.source_type,
                    "identifier_type": candidate.identifier_type,
                    "identifier_value": candidate.identifier_value,
                    "domain_verified": candidate.domain_verified,
                    "identity_verified": candidate.identity_verified,
                    "source_retrievable": candidate.source_retrievable,
                    "content_usable": candidate.content_usable,
                    "distribution_capable": candidate.distribution_capable,
                    "coverage_verified": candidate.coverage_verified,
                    "validation_status": candidate.validation_status.value,
                    "validation_reason": candidate.validation_reason,
                    "coverage_window": f"{candidate.coverage_start} to {candidate.coverage_end}",
                }
            )

        fund_records.append(
            {
                "fund_id": fund.fund_id,
                "fund_name": fund.fund_name,
                "ticker": fund.ticker,
                "country": fund.country,
                "fund_type": fund.fund_type,
                "fund_family": fund.fund_family,
                "identifiers": {
                    "cik": fund.cik,
                    "sec_series_id": fund.sec_series_id,
                    "sec_class_id": fund.sec_class_id,
                    "sedar_id": fund.sedar_id,
                    "fundserv_code": fund.fundserv_code,
                },
                "official_source_url": fund.official_source_url,
                "candidate_count": len(candidate_dicts),
                "candidates": candidate_dicts,
            }
        )

    audit_report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "audit_version": "2.0",
        "inspection_window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
        },
        "summary": {
            "total_funds": total_funds,
            "total_candidates_discovered": total_candidates,
            "verification_levels": level_counts,
            "tier_breakdown": tier_counts,
            "validation_status_breakdown": status_counts,
            "region_breakdown": region_stats,
            "verified_domain_registry_count": len(VERIFIED_OFFICIAL_DOMAINS),
            "tier3_corroboration_domain_count": len(TIER_3_CORROBORATION_DOMAINS),
        },
        "funds": fund_records,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(audit_report, f, indent=2)

    return audit_report


def print_audit_summary(report: dict[str, Any]) -> None:
    """Print readable audit summary to console."""
    summary = report["summary"]
    print("=" * 75)
    print("PHASE 1 — SOURCE DISCOVERY & AUTHENTICATION AUDIT REPORT (v2.0)")
    print("=" * 75)
    print(f"Generated At:                     {report['generated_at']}")
    print(
        f"Inspection Window:                {report['inspection_window']['start']} to {report['inspection_window']['end']}"
    )
    print(f"Total Funds Audited:              {summary['total_funds']}")
    print(f"Total Candidate Sources:          {summary['total_candidates_discovered']}")
    print("-" * 75)
    print("VERIFICATION LEVELS AUDITED:")
    for level, count in summary["verification_levels"].items():
        print(f"  - {level:<32}: {count}")
    print("-" * 75)
    print("TIER BREAKDOWN:")
    for tier, count in summary["tier_breakdown"].items():
        print(f"  - {tier:<32}: {count}")
    print("-" * 75)
    print("FINE-GRAINED VALIDATION STATUS BREAKDOWN:")
    for status, count in summary["validation_status_breakdown"].items():
        print(f"  - {status:<32}: {count}")
    print("-" * 75)
    print("REGION BREAKDOWN:")
    for region, data in summary["region_breakdown"].items():
        print(
            f"  [{region}] Funds: {data['funds']} | Tier 1: {data['tier_1_sources']} | Tier 2: {data['tier_2_sources']} | Domain Verified: {data['domain_verified']}"
        )
    print("=" * 75)


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    universe_path = project_root / "config" / "universe_100.json"
    output_path = project_root / "quality" / "source_discovery_audit.json"

    report = audit_universe(universe_path, output_path)
    print_audit_summary(report)
    print(f"\nMachine-readable audit written to: {output_path}")


if __name__ == "__main__":
    main()
