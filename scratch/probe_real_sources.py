"""Probe real sources for representative US and Canadian funds."""

from __future__ import annotations

import os
from datetime import date

from src.http_client import HTTPClient
from src.source_discovery import (
    OfficialDomainValidator,
    SourceDiscoveryEngine,
)
from src.universe_loader import UniverseRegistry


def main():
    os.environ["DETECTOR_USER_AGENT"] = "ChaitanyaResearch chaitanya893@gmail.com"
    client = HTTPClient(timeout_seconds=10.0, max_retries=1)
    engine = SourceDiscoveryEngine(http_client=client)
    registry = UniverseRegistry.from_json("config/universe_100.json")

    # Sample representative funds across US and CA
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

    print(f"Probing {len(sample_fund_ids)} representative funds...")
    for fid in sample_fund_ids:
        fund = registry.get_fund(fid)
        if not fund:
            continue
        candidates = engine.discover_candidates(
            fund, date(2026, 2, 1), date(2026, 2, 28)
        )
        print(
            f"\n==================== FUND: {fund.fund_id} ({fund.ticker or fund.fund_name}) ===================="
        )
        for c in candidates:
            validated = engine.validate_candidate(c, fund)
            print(f"[{validated.source_tier.name}] {validated.url}")
            print(
                f"  - Domain: {validated.official_domain} (Verified: {OfficialDomainValidator.is_official_domain(validated.url)})"
            )
            print(
                f"  - HTTP Status: {validated.http_status}, Retrieval: {validated.retrieval_status}"
            )
            print(f"  - Identity Verified: {validated.fund_identity_verified}")
            print(
                f"  - Distribution Semantics: {validated.distribution_semantics_verified}"
            )
            print(f"  - Coverage Complete: {validated.coverage_complete}")
            print(f"  - Validation Status: {validated.validation_status.value}")
            print(f"  - Validation Reason: {validated.validation_reason}")


if __name__ == "__main__":
    main()
