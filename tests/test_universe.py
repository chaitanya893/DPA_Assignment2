"""Unit tests for the 100-Fund Verified Universe (config/universe_100.json).

Validates schema integrity, non-fabrication rules, minimum constraints,
and explicit dual-source verification audit metadata.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def universe_data() -> list[dict]:
    universe_path = Path(__file__).parent.parent / "config" / "universe_100.json"
    assert universe_path.exists(), f"Universe file does not exist at {universe_path}"
    with universe_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, list), "universe_100.json must be a JSON list"
    return data


def test_universe_total_and_country_split(universe_data: list[dict]) -> None:
    """Verify total fund count and 60 US / 40 Canadian split."""
    assert len(universe_data) == 100, f"Expected 100 funds, got {len(universe_data)}"

    us_funds = [r for r in universe_data if r.get("country") == "US"]
    ca_funds = [r for r in universe_data if r.get("country") == "CA"]

    assert len(us_funds) == 60, f"Expected 60 US funds, got {len(us_funds)}"
    assert len(ca_funds) == 40, f"Expected 40 Canadian funds, got {len(ca_funds)}"


def test_no_duplicate_fund_ids(universe_data: list[dict]) -> None:
    """Verify uniqueness of fund_id across the entire universe."""
    fund_ids = [r["fund_id"] for r in universe_data]
    assert len(fund_ids) == len(set(fund_ids)), "Duplicate fund_id detected in universe"


def test_no_duplicate_tickers_per_country(universe_data: list[dict]) -> None:
    """Verify ticker uniqueness within country when ticker is present."""
    us_tickers = [
        r["ticker"]
        for r in universe_data
        if r.get("country") == "US" and r.get("ticker")
    ]
    ca_tickers = [
        r["ticker"]
        for r in universe_data
        if r.get("country") == "CA" and r.get("ticker")
    ]

    assert len(us_tickers) == len(set(us_tickers)), "Duplicate US ticker detected"
    assert len(ca_tickers) == len(set(ca_tickers)), "Duplicate Canadian ticker detected"


def test_mandatory_audit_and_verification_fields(
    universe_data: list[dict],
) -> None:
    """Verify every record has complete audit trail and dual-source verification metadata."""
    for record in universe_data:
        f_id = record.get("fund_id")
        assert f_id, "Missing fund_id"
        assert record.get("fund_name"), f"Missing fund_name for {f_id}"
        assert record.get("fund_family"), f"Missing fund_family for {f_id}"
        assert record.get("fund_type") in {
            "MUTUAL_FUND",
            "ETF",
        }, f"Invalid fund_type for {f_id}"
        assert record.get("official_source_url", "").startswith(
            "https://"
        ), f"Invalid official URL for {f_id}"
        assert record.get(
            "verification_source"
        ), f"Missing verification_source for {f_id}"
        assert record.get("verified_at"), f"Missing verified_at for {f_id}"
        assert record.get("is_verified") is True, f"is_verified must be True for {f_id}"

        # Strict dual-source audit verification
        audit = record.get("verification_audit")
        assert audit and isinstance(
            audit, dict
        ), f"Missing verification_audit block for {f_id}"
        assert "primary_source" in audit, f"Missing primary_source in audit for {f_id}"
        assert (
            "secondary_source" in audit
        ), f"Missing secondary_source in audit for {f_id}"
        assert len(audit["primary_source"].get("verified_fields", [])) > 0
        assert len(audit["secondary_source"].get("verified_fields", [])) > 0

        # No placeholder/fake URLs in universe
        url = record.get("official_source_url", "")
        assert "example.com" not in url, f"Placeholder URL found in {f_id}"
        assert "synthetic-test" not in url, f"Test URL found in {f_id}"


def test_etf_and_monthly_payer_minimums(universe_data: list[dict]) -> None:
    """Verify at least 10 ETFs and 10 monthly payers with strict boolean consistency."""
    etfs = [r for r in universe_data if r.get("is_etf") is True]
    monthly_payers = [r for r in universe_data if r.get("is_monthly_payer") is True]

    assert len(etfs) >= 10, f"Expected >= 10 ETFs, got {len(etfs)}"
    assert (
        len(monthly_payers) >= 10
    ), f"Expected >= 10 monthly payers, got {len(monthly_payers)}"

    # Consistency check
    for r in universe_data:
        if r.get("is_etf"):
            assert (
                r.get("fund_type") == "ETF"
            ), f"is_etf is True but fund_type is {r.get('fund_type')}"
        if r.get("is_monthly_payer"):
            assert (
                r.get("expected_frequency") == "MONTHLY"
            ), f"is_monthly_payer is True but expected_frequency is {r.get('expected_frequency')}"


def test_fund_family_diversity(universe_data: list[dict]) -> None:
    """Verify reasonable diversity across major US and Canadian fund families."""
    families = {r.get("fund_family") for r in universe_data}
    assert (
        len(families) >= 8
    ), f"Expected >= 8 distinct fund families, got {len(families)}"

    # Check key major issuers exist
    assert any("Vanguard" in f for f in families)
    assert any("iShares" in f for f in families)
    assert any("BMO" in f for f in families)
    assert any("RBC" in f for f in families)
    assert any("TD" in f for f in families)


def test_us_sec_identifiers_validity(universe_data: list[dict]) -> None:
    """Verify all US funds have valid SEC CIK, Series ID, and Class ID."""
    us_funds = [r for r in universe_data if r.get("country") == "US"]
    for r in us_funds:
        f_id = r["fund_id"]
        cik = r.get("cik")
        series_id = r.get("sec_series_id")
        class_id = r.get("sec_class_id")

        assert (
            cik and len(cik) == 10 and cik.isdigit()
        ), f"Invalid CIK for {f_id}: {cik}"
        assert series_id and series_id.startswith(
            "S"
        ), f"Invalid SEC series_id for {f_id}: {series_id}"
        assert class_id and class_id.startswith(
            "C"
        ), f"Invalid SEC class_id for {f_id}: {class_id}"


def test_canadian_funds_strict_nulls(universe_data: list[dict]) -> None:
    """Verify Canadian funds do not contain fabricated US SEC identifiers."""
    ca_funds = [r for r in universe_data if r.get("country") == "CA"]
    for r in ca_funds:
        f_id = r["fund_id"]
        assert r.get("cik") is None, f"Canadian fund {f_id} should have null CIK"
        assert (
            r.get("sec_series_id") is None
        ), f"Canadian fund {f_id} should have null sec_series_id"
        assert (
            r.get("sec_class_id") is None
        ), f"Canadian fund {f_id} should have null sec_class_id"
