"""Comprehensive tests for the Source Authentication & Discovery Layer (Phase 1).

Tests all 10 required scenarios:
1. SEC EDGAR Official Source Authentication
2. Canadian TMX / SEDAR+ / Sponsor Source Authentication
3. Sponsor Domain Strict Whitelist Enforcement
4. Multi-Fund Trust Cross-Fund Contamination Prevention
5. Content Hashing & Change Detection
6. Dynamic SPA / JS-Rendered Shell Rejection
7. Official Distribution PDF Authentication
8. Tier 3 Public Market Domain Isolation
9. Date Window Coverage Boundaries
10. Exhaustive Negative Proof Validation
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.http_client import HTTPClient, HTTPResponseRecord
from src.models import (
    SourceCandidate,
    SourceTier,
    ValidationStatus,
)
from src.source_discovery import (
    FundIdentityValidator,
    OfficialDomainValidator,
    PDFSourceValidator,
    SourceDiscoveryEngine,
)
from src.universe_loader import UniverseFund


@pytest.fixture
def sample_us_fund() -> UniverseFund:
    return UniverseFund(
        fund_id="US_VANGUARD_VTI",
        fund_name="Vanguard Total Stock Market ETF",
        ticker="VTI",
        country="US",
        fund_type="ETF",
        fund_family="Vanguard",
        cik="0000036405",
        sec_series_id="S000001234",
        sec_class_id="C000005678",
        official_source_url="https://investor.vanguard.com/investment-products/etfs/profile/vti",
    )


@pytest.fixture
def sample_ca_fund() -> UniverseFund:
    return UniverseFund(
        fund_id="CA_BMO_ZCN",
        fund_name="BMO S&P/TSX Capped Composite Index ETF",
        ticker="ZCN",
        country="CA",
        fund_type="ETF",
        fund_family="BMO",
        sedar_id="00028741",
        official_source_url="https://www.bmoetfs.com/fund/zcn",
    )


# ---------------------------------------------------------------------------
# Test 1: SEC EDGAR Official Source Authentication
# ---------------------------------------------------------------------------
def test_sec_edgar_source_authentication(sample_us_fund: UniverseFund):
    engine = SourceDiscoveryEngine()
    candidates = engine.discover_candidates(
        sample_us_fund, date(2026, 2, 1), date(2026, 2, 28)
    )

    sec_candidates = [
        c for c in candidates if c.source_tier == SourceTier.TIER_1_AUTHORITATIVE
    ]
    assert len(sec_candidates) == 1
    sec = sec_candidates[0]

    assert sec.official_domain == "data.sec.gov"
    assert "CIK0000036405" in sec.url
    assert OfficialDomainValidator.is_official_domain(sec.url) is True
    assert sec.direct_declaration_capable is False
    assert sec.validation_status == ValidationStatus.SUPPORTING_FILING_INDEX


# ---------------------------------------------------------------------------
# Test 2: Canadian TMX / SEDAR+ / Sponsor Source Authentication
# ---------------------------------------------------------------------------
def test_canadian_source_authentication(sample_ca_fund: UniverseFund):
    assert (
        OfficialDomainValidator.is_official_domain(
            "https://www.sedarplus.ca/sedarplus-search"
        )
        is True
    )
    assert (
        OfficialDomainValidator.is_official_domain(
            "https://www.tmx.com/newsroom/dividends"
        )
        is True
    )
    assert (
        OfficialDomainValidator.is_official_domain("https://www.bmoetfs.com/fund/zcn")
        is True
    )
    assert (
        OfficialDomainValidator.is_official_domain(
            "https://www.vanguard.ca/en/investor/products/etfs"
        )
        is True
    )

    engine = SourceDiscoveryEngine()
    candidates = engine.discover_candidates(
        sample_ca_fund, date(2026, 2, 1), date(2026, 2, 28)
    )
    ca_sponsor = [
        c for c in candidates if c.source_tier == SourceTier.TIER_2_PRIMARY_UNSTRUCTURED
    ]
    assert len(ca_sponsor) == 1
    assert ca_sponsor[0].validation_status == ValidationStatus.PARTIALLY_VERIFIED


# ---------------------------------------------------------------------------
# Test 3: Sponsor Domain Strict Whitelist Enforcement
# ---------------------------------------------------------------------------
def test_sponsor_domain_strict_whitelist_enforcement(sample_us_fund: UniverseFund):
    unverified_url = "https://fake-vanguard-distributions.biz/vti"
    assert OfficialDomainValidator.is_official_domain(unverified_url) is False

    mock_client = MagicMock(spec=HTTPClient)
    engine = SourceDiscoveryEngine(http_client=mock_client)

    candidate = SourceCandidate(
        fund_id="US_VANGUARD_VTI",
        source_id="unverified_source",
        source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
        source_role="DIRECT_DECLARATION",
        provider="Fake Vanguard",
        url=unverified_url,
        source_type="UNVERIFIED",
        official_domain="fake-vanguard-distributions.biz",
    )

    validated = engine.validate_candidate(candidate, sample_us_fund)
    assert validated.validation_status == ValidationStatus.INVALID
    assert "not in verified official registry" in validated.validation_reason


# ---------------------------------------------------------------------------
# Test 4: Multi-Fund Trust Cross-Fund Contamination Prevention
# ---------------------------------------------------------------------------
def test_multi_fund_trust_cross_fund_prevention(sample_us_fund: UniverseFund):
    # Filing from the same Vanguard Index Funds trust, but discussing a different series/fund (e.g. VOO)
    foreign_fund_text = """
    Vanguard Index Funds SEC Filing
    Series S000009999 Class C000008888
    The Vanguard 500 Index Fund (VOO) declared a cash dividend of $1.50 per share
    payable on February 28, 2026.
    """
    has_id, reason = FundIdentityValidator.validate_identity(
        sample_us_fund, foreign_fund_text
    )
    assert has_id is False
    assert "does not explicitly identify target fund" in reason

    # Correct filing mentioning VTI specifically
    target_fund_text = """
    Vanguard Index Funds SEC Filing
    Vanguard Total Stock Market ETF (VTI)
    Declared a cash distribution of $0.85 per share payable on February 28, 2026.
    """
    has_id_vti, reason_vti = FundIdentityValidator.validate_identity(
        sample_us_fund, target_fund_text
    )
    assert has_id_vti is True
    assert "VTI" in reason_vti


# ---------------------------------------------------------------------------
# Test 5: Content Hashing & Change Detection
# ---------------------------------------------------------------------------
def test_content_hashing_and_change_detection(sample_us_fund: UniverseFund):
    content_v1 = "<html><body><h1>VTI Schedule</h1><p>No distributions in Feb 2026.</p></body></html>"
    content_v2 = "<html><body><h1>VTI Schedule</h1><p>Updated: Feb 2026 dividend declared: $0.80</p></body></html>"

    hash_v1 = hashlib.sha256(content_v1.encode("utf-8")).hexdigest()
    hash_v2 = hashlib.sha256(content_v2.encode("utf-8")).hexdigest()

    assert hash_v1 != hash_v2
    assert len(hash_v1) == 64
    assert len(hash_v2) == 64


# ---------------------------------------------------------------------------
# Test 6: Dynamic SPA / JS-Rendered Shell Rejection
# ---------------------------------------------------------------------------
def test_dynamic_spa_shell_rejection(sample_us_fund: UniverseFund):
    spa_shell_html = """
    <!DOCTYPE html>
    <html>
      <head><title>Vanguard Distribution Center</title></head>
      <body>
        <div id="root"></div>
        <p>Please enable JavaScript to view this application.</p>
      </body>
    </html>
    """
    is_shell, reason = OfficialDomainValidator.is_generic_shell_or_blocked(
        spa_shell_html, 200
    )
    assert is_shell is True
    assert "Dynamic SPA" in reason or "JavaScript" in reason

    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://investor.vanguard.com/investment-products/etfs/profile/vti",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=spa_shell_html,
        content_bytes=spa_shell_html.encode("utf-8"),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )
    engine = SourceDiscoveryEngine(http_client=mock_client)
    candidate = SourceCandidate(
        fund_id="US_VANGUARD_VTI",
        source_id="vanguard_spa_page",
        source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
        source_role="DIRECT_DECLARATION",
        provider="Vanguard",
        url="https://investor.vanguard.com/investment-products/etfs/profile/vti",
        source_type="SPONSOR_PORTAL_SCHEDULE",
        official_domain="investor.vanguard.com",
    )
    validated = engine.validate_candidate(candidate, sample_us_fund)
    assert validated.validation_status == ValidationStatus.INCOMPLETE
    assert validated.content_usable is False
    assert validated.coverage_complete is False


# ---------------------------------------------------------------------------
# Test 7: Official Distribution PDF Authentication
# ---------------------------------------------------------------------------
def test_official_distribution_pdf_authentication(sample_us_fund: UniverseFund):
    # Invalid non-PDF binary
    invalid_bytes = b"NOT_A_PDF_STREAM_12345"
    is_valid, reason, _ = PDFSourceValidator.validate_pdf_bytes(
        invalid_bytes, sample_us_fund
    )
    assert is_valid is False
    assert "lacks standard %PDF" in reason

    # Valid PDF header containing target fund ticker VTI
    valid_pdf_bytes = b"%PDF-1.7\n1 0 obj\n<< /Title (Vanguard VTI Distribution Notice) >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"
    is_valid, reason, content_hash = PDFSourceValidator.validate_pdf_bytes(
        valid_pdf_bytes, sample_us_fund
    )
    assert is_valid is True
    assert content_hash is not None
    assert len(content_hash) == 64


# ---------------------------------------------------------------------------
# Test 8: Tier 3 Public Market Domain Isolation
# ---------------------------------------------------------------------------
def test_tier_3_public_market_domain_isolation(sample_us_fund: UniverseFund):
    t3_url = "https://finance.yahoo.com/quote/VTI/history"
    assert OfficialDomainValidator.is_official_domain(t3_url) is False
    assert OfficialDomainValidator.is_tier_3_domain(t3_url) is True

    mock_client = MagicMock(spec=HTTPClient)
    engine = SourceDiscoveryEngine(http_client=mock_client)
    candidate = SourceCandidate(
        fund_id="US_VANGUARD_VTI",
        source_id="yahoo_market_data",
        source_tier=SourceTier.TIER_3_CORROBORATION,
        source_role="CORROBORATION_ONLY",
        provider="Yahoo Finance",
        url=t3_url,
        source_type="THIRD_PARTY_MARKET_DATA",
        official_domain="finance.yahoo.com",
    )
    validated = engine.validate_candidate(candidate, sample_us_fund)
    assert validated.validation_status == ValidationStatus.CORROBORATION_ONLY
    assert validated.source_tier == SourceTier.TIER_3_CORROBORATION
    assert "corroboration only" in validated.validation_reason


# ---------------------------------------------------------------------------
# Test 9: Date Window Coverage Boundaries
# ---------------------------------------------------------------------------
def test_date_window_coverage_boundaries(sample_us_fund: UniverseFund):
    engine = SourceDiscoveryEngine()
    w_start = date(2026, 2, 1)
    w_end = date(2026, 2, 28)
    candidates = engine.discover_candidates(sample_us_fund, w_start, w_end)

    for c in candidates:
        assert c.coverage_start == w_start
        assert c.coverage_end == w_end


# ---------------------------------------------------------------------------
# Test 10: Exhaustive Negative Proof Validation
# ---------------------------------------------------------------------------
def test_exhaustive_negative_proof_validation(sample_us_fund: UniverseFund):
    # Valid schedule table HTML containing distribution headers and full annual schedule
    exhaustive_schedule_html = """
    <html>
      <head><title>VTI Distribution Schedule</title></head>
      <body>
        <h1>Vanguard Total Stock Market ETF (VTI)</h1>
        <h2>2026 Distribution Schedule</h2>
        <table>
          <thead>
            <tr><th>Quarter</th><th>Declaration Date</th><th>Record Date</th><th>Payable Date</th></tr>
          </thead>
          <tbody>
            <tr><td>Q1</td><td>2026-03-20</td><td>2026-03-23</td><td>2026-03-26</td></tr>
            <tr><td>Q2</td><td>2026-06-19</td><td>2026-06-22</td><td>2026-06-25</td></tr>
            <tr><td>Q3</td><td>2026-09-18</td><td>2026-09-21</td><td>2026-09-24</td></tr>
            <tr><td>Q4</td><td>2026-12-18</td><td>2026-12-21</td><td>2026-12-24</td></tr>
          </tbody>
        </table>
      </body>
    </html>
    """
    mock_client = MagicMock(spec=HTTPClient)
    mock_client.get.return_value = HTTPResponseRecord(
        url="https://investor.vanguard.com/investment-products/etfs/profile/vti",
        status_code=200,
        retrieved_at=datetime.now(timezone.utc),
        content_text=exhaustive_schedule_html,
        content_bytes=exhaustive_schedule_html.encode("utf-8"),
        failure_reason=None,
        is_success=True,
        elapsed_seconds=0.1,
    )
    engine = SourceDiscoveryEngine(http_client=mock_client)
    candidate = SourceCandidate(
        fund_id="US_VANGUARD_VTI",
        source_id="vanguard_vti_schedule",
        source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
        source_role="DIRECT_DECLARATION",
        provider="Vanguard",
        url="https://investor.vanguard.com/investment-products/etfs/profile/vti",
        source_type="SPONSOR_PORTAL_SCHEDULE",
        official_domain="investor.vanguard.com",
    )
    validated = engine.validate_candidate(candidate, sample_us_fund)
    assert validated.validation_status == ValidationStatus.VERIFIED_COVERAGE_SOURCE
    assert validated.fund_identity_verified is True
    assert validated.coverage_verified is True


# ---------------------------------------------------------------------------
# Test 11: Distribution Semantics & False Positive Rejection
# ---------------------------------------------------------------------------
def test_distribution_semantics_and_false_positive_rejection():
    from src.source_discovery import DistributionSemanticsValidator

    # False positive 12b-1 fee mention without actual dividend/distribution amounts
    fee_text = """
    The Fund has adopted a Distribution and Service Plan pursuant to Rule 12b-1 under the 1940 Act.
    Under the Plan, the Fund pays distribution fees to the distributor at an annual rate of 0.25%
    for sales and distribution expenses.
    """
    is_dist, reason = DistributionSemanticsValidator.validate_semantics(fee_text)
    assert is_dist is False
    assert "fee or policy" in reason or "No recognized" in reason

    # True distribution announcement with payable dates and per-share amounts
    real_dist_text = """
    Vanguard Total Stock Market ETF announced a cash dividend distribution of $0.8523 per share.
    Record date: March 23, 2026. Payable date: March 26, 2026. Ex-dividend date: March 20, 2026.
    """
    is_real, reason_real = DistributionSemanticsValidator.validate_semantics(
        real_dist_text
    )
    assert is_real is True
    assert "Verified distribution semantics" in reason_real
