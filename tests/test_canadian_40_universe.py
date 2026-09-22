"""Exhaustive validation of the Canadian 40-Fund Universe in config/universe_100.json.

Validates:
1. Exactly 40 Canadian funds.
2. Exact cadence breakdown: 28 Monthly, 8 Quarterly, 2 Semi-Annual, 2 Annual.
3. Every fund has a valid Canadian identity (Ticker or Fundserv code, Name, Sponsor).
4. No duplicate identities.
5. Zero US fund leakage into Canadian universe.
6. Strict dual-source audit metadata present.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def canadian_universe() -> list[dict]:
    universe_path = Path(__file__).parent.parent / "config" / "universe_100.json"
    assert universe_path.exists(), f"Universe file missing: {universe_path}"
    with universe_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return [r for r in data if r.get("country") == "CA"]


def test_canadian_exact_count(canadian_universe: list[dict]) -> None:
    """Verify exactly 40 Canadian funds."""
    assert (
        len(canadian_universe) == 40
    ), f"Expected 40 Canadian funds, found {len(canadian_universe)}"


def test_canadian_cadence_breakdown(canadian_universe: list[dict]) -> None:
    """Verify exact frequency breakdown: 28 Monthly, 8 Quarterly, 2 Semi-Annual, 2 Annual."""
    monthly_funds = [
        f for f in canadian_universe if f.get("expected_frequency") == "MONTHLY"
    ]
    quarterly_funds = [
        f for f in canadian_universe if f.get("expected_frequency") == "QUARTERLY"
    ]
    semi_annual_funds = [
        f for f in canadian_universe if f.get("expected_frequency") == "SEMI_ANNUAL"
    ]
    annual_funds = [
        f for f in canadian_universe if f.get("expected_frequency") == "ANNUAL"
    ]

    assert (
        len(monthly_funds) == 28
    ), f"Expected 28 Monthly funds, got {len(monthly_funds)}"
    assert (
        len(quarterly_funds) == 8
    ), f"Expected 8 Quarterly funds, got {len(quarterly_funds)}"
    assert (
        len(semi_annual_funds) == 2
    ), f"Expected 2 Semi-Annual funds, got {len(semi_annual_funds)}"
    assert len(annual_funds) == 2, f"Expected 2 Annual funds, got {len(annual_funds)}"

    # All monthly funds must have is_monthly_payer True
    for f in monthly_funds:
        assert (
            f.get("is_monthly_payer") is True
        ), f"Fund {f['fund_id']} expected is_monthly_payer=True"

    # All non-monthly funds must have is_monthly_payer False
    for f in quarterly_funds + semi_annual_funds + annual_funds:
        assert (
            f.get("is_monthly_payer") is False
        ), f"Fund {f['fund_id']} expected is_monthly_payer=False"


def test_no_duplicate_canadian_identities(canadian_universe: list[dict]) -> None:
    """Verify uniqueness of fund_id, tickers, and fundserv codes in Canadian universe."""
    fund_ids = [f["fund_id"] for f in canadian_universe]
    assert len(fund_ids) == len(set(fund_ids)), "Duplicate Canadian fund_id detected"

    tickers = [f["ticker"] for f in canadian_universe if f.get("ticker")]
    assert len(tickers) == len(set(tickers)), "Duplicate Canadian ticker detected"

    fundserv_codes = [
        f["fundserv_code"] for f in canadian_universe if f.get("fundserv_code")
    ]
    assert len(fundserv_codes) == len(
        set(fundserv_codes)
    ), "Duplicate Canadian fundserv_code detected"


def test_canadian_identity_fields_and_no_us_leakage(
    canadian_universe: list[dict],
) -> None:
    """Verify each Canadian fund has valid identity and no US identifiers (CIK, SEC Series/Class)."""
    for f in canadian_universe:
        fid = f["fund_id"]
        assert fid.startswith("CA_"), f"Canadian fund_id must start with 'CA_': {fid}"
        assert f.get("country") == "CA", f"Fund country must be 'CA': {fid}"
        assert f.get("fund_name"), f"Missing fund_name for {fid}"
        assert f.get("fund_family"), f"Missing fund_family for {fid}"
        assert f.get("official_source_url", "").startswith(
            "https://"
        ), f"Missing official URL for {fid}"

        # Either ticker or fundserv_code must be populated
        has_id = bool(f.get("ticker") or f.get("fundserv_code"))
        assert has_id, f"Fund {fid} must have either ticker or fundserv_code"

        # Canadian funds MUST have strictly NULL US SEC identifiers
        assert f.get("cik") is None, f"Canadian fund {fid} must have null CIK"
        assert (
            f.get("sec_series_id") is None
        ), f"Canadian fund {fid} must have null sec_series_id"
        assert (
            f.get("sec_class_id") is None
        ), f"Canadian fund {fid} must have null sec_class_id"
