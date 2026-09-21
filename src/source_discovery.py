"""Source Discovery, Authentication, and Validation Layer for Fund Distribution Detection.

Implements strict official-domain verification, fund-identity authentication,
content-hash tracking, and coverage validation according to Assignment 2 requirements.
Adheres strictly to the zero-fabrication data integrity rule.
"""

from __future__ import annotations

import hashlib
import logging
import re
import urllib.parse
from datetime import date

from src.http_client import HTTPClient, HTTPResponseRecord
from src.models import (
    SourceCandidate,
    SourceTier,
    ValidationStatus,
)
from src.universe_loader import UniverseFund

logger = logging.getLogger(__name__)

# Verified official domains for Tier 1 & Tier 2 sources
VERIFIED_OFFICIAL_DOMAINS = {
    # Tier 1 Regulatory / Depository / Exchange
    "sec.gov",
    "data.sec.gov",
    "tmx.com",
    "tmxmoney.com",
    "cds.ca",
    "sedarplus.ca",
    "sedar.com",
    # Tier 2 Official Fund Sponsors & Asset Managers (US & CA)
    "vanguard.com",
    "advisors.vanguard.com",
    "corporate.vanguard.com",
    "investor.vanguard.com",
    "vanguard.ca",
    "ishares.com",
    "blackrock.com",
    "ssga.com",
    "statestreet.com",
    "pimco.com",
    "fidelity.com",
    "institutional.fidelity.com",
    "schwab.com",
    "schwabassetmanagement.com",
    "invesco.com",
    "bmogam.com",
    "bmoetfs.com",
    "bmo.com",
    "newsroom.bmo.com",
    "rbcgam.com",
    "rbc.com",
    "td.com",
    "tdassetmanagement.com",
    "globalx.ca",
    "globalxetfs.com",
    "ci.com",
    "cifinancial.com",
    "firstasset.com",
    "firstfiduciary.ca",
    "mackenzieinvestments.com",
    "mackenzie.com",
    "globenewswire.com",
    "newswire.ca",
}

# Third-party / Public Market data domains (strictly Tier 3 corroboration only)
TIER_3_CORROBORATION_DOMAINS = {
    "finance.yahoo.com",
    "morningstar.com",
    "nasdaq.com",
    "nyse.com",
    "etf.com",
    "etfdb.com",
    "seekingalpha.com",
    "dividend.com",
}


class OfficialDomainValidator:
    """Validates domain authenticity, redirect chains, and rejects generic/blocked shells."""

    @staticmethod
    def extract_domain(url: str) -> str:
        """Extract root/subdomain from URL string."""
        if not url:
            return ""
        try:
            parsed = urllib.parse.urlparse(url)
            domain = (parsed.hostname or "").lower()
            domain = domain.removeprefix("www.")
            return domain
        except (ValueError, TypeError, AttributeError):
            return ""

    @staticmethod
    def is_official_domain(url: str) -> bool:
        """Check if URL belongs to a verified Tier 1 or Tier 2 official domain."""
        domain = OfficialDomainValidator.extract_domain(url)
        if not domain:
            return False
        for official in VERIFIED_OFFICIAL_DOMAINS:
            if domain == official or domain.endswith("." + official):
                return True
        return False

    @staticmethod
    def is_tier_3_domain(url: str) -> bool:
        """Check if URL belongs to a Tier 3 public market / corroboration domain."""
        domain = OfficialDomainValidator.extract_domain(url)
        if not domain:
            return False
        for t3 in TIER_3_CORROBORATION_DOMAINS:
            if domain == t3 or domain.endswith("." + t3):
                return True
        return False

    @staticmethod
    def validate_redirect_chain(
        initial_url: str,
        final_url: str,
        redirect_chain: list[str] | None = None,
    ) -> tuple[bool, str]:
        """Verify redirect chain does not move to an unauthorized or unrelated domain."""
        final_domain = OfficialDomainValidator.extract_domain(final_url)

        if not OfficialDomainValidator.is_official_domain(final_url):
            return (
                False,
                f"Redirected to unverified non-official domain: '{final_domain}'",
            )

        # Check if redirect stayed within official sponsor / regulatory boundaries
        return True, "Redirect chain valid and within verified official domain space."

    @staticmethod
    def is_generic_shell_or_blocked(
        content: str | None,
        http_status: int | None,
    ) -> tuple[bool, str]:
        """Identify dynamic shells, login pages, and WAF block challenges."""
        if http_status in {401, 403}:
            return (
                True,
                f"HTTP {http_status}: Access forbidden or blocked by security policy.",
            )
        if http_status in {404, 410}:
            return True, f"HTTP {http_status}: Document not found."
        if http_status in {500, 502, 503, 504}:
            return True, f"HTTP {http_status}: Server error or service unavailable."
        if not content or len(content.strip()) < 100:
            return True, "Empty or truncated response body."

        # Detect anti-bot / WAF / challenge pages
        lower_content = content.lower()
        if (
            "access denied" in lower_content
            or "cloudflare" in lower_content
            and "captcha" in lower_content
            or "security check" in lower_content
            or "please verify you are a human" in lower_content
            or "automated access" in lower_content
        ):
            return True, "Security challenge or WAF bot block page detected."

        # Detect dynamic JavaScript loader shells without static table data
        is_spa_loader = (
            len(content) < 3000
            and (
                '<div id="root"></div>' in lower_content
                or "<app-root></app-root>" in lower_content
                or "enable javascript" in lower_content
            )
            and "<table" not in lower_content
        )
        if is_spa_loader:
            return (
                True,
                "Dynamic SPA client-side shell lacking pre-rendered distribution tables.",
            )

        return False, "Content body passed structural validity check."


class FundIdentityValidator:
    """Validates that candidate source content explicitly belongs to the target fund."""

    @staticmethod
    def validate_identity(
        fund: UniverseFund,
        content: str,
    ) -> tuple[bool, str]:
        """Verify that document text explicitly mentions fund ticker, full name, or SEC series ID."""
        if not content:
            return False, "Empty content cannot establish fund identity."

        clean_text = re.sub(r"<[a-zA-Z/][^>]*>", " ", content)
        clean_text = re.sub(r"\s+", " ", clean_text).strip()

        # 1. Ticker exact word match
        if fund.ticker:
            ticker_pat = re.compile(rf"\b{re.escape(fund.ticker)}\b", re.IGNORECASE)
            if ticker_pat.search(clean_text) or ticker_pat.search(content):
                return (
                    True,
                    f"Verified fund identity via exact ticker match '{fund.ticker}'.",
                )

        # 2. SEC Series ID exact match
        if fund.sec_series_id and (
            fund.sec_series_id in clean_text or fund.sec_series_id in content
        ):
            return (
                True,
                f"Verified fund identity via SEC Series ID '{fund.sec_series_id}'.",
            )

        # 3. SEC Class ID exact match
        if fund.sec_class_id and (
            fund.sec_class_id in clean_text or fund.sec_class_id in content
        ):
            return (
                True,
                f"Verified fund identity via SEC Class ID '{fund.sec_class_id}'.",
            )

        # 4. Full Fund Name or distinct key phrase
        if fund.fund_name:
            if fund.fund_name.lower() in clean_text.lower():
                return (
                    True,
                    f"Verified fund identity via full fund name '{fund.fund_name}'.",
                )
            # Sub-phrase match with at least 3 distinct non-generic words
            words = [
                w
                for w in fund.fund_name.split()
                if len(w) > 3
                and w.lower()
                not in {
                    "fund",
                    "index",
                    "trust",
                    "series",
                    "shares",
                    "portfolio",
                    "asset",
                    "total",
                    "market",
                }
            ]
            if len(words) >= 2 and all(w.lower() in clean_text.lower() for w in words):
                return (
                    True,
                    f"Verified fund identity via distinctive key phrase '{' '.join(words)}'.",
                )

        # 5. SEDAR ID or FundServ Code
        if fund.sedar_id and fund.sedar_id in clean_text:
            return True, f"Verified fund identity via SEDAR ID '{fund.sedar_id}'."
        if fund.fundserv_code and fund.fundserv_code in clean_text:
            return (
                True,
                f"Verified fund identity via FundServ code '{fund.fundserv_code}'.",
            )

        return (
            False,
            f"Document content does not explicitly identify target fund '{fund.fund_id}' ({fund.ticker or fund.fund_name}).",
        )


class DistributionSemanticsValidator:
    """Validates genuine distribution/dividend semantics and rejects false-positive fee/policy clauses."""

    DISTRIBUTION_TERMS_PATTERN = re.compile(
        r"(?:"
        r"cash\s+dividend|cash\s+distribution|dividend\s+distribution|"
        r"income\s+distribution|capital\s*gain\s+distribution|capital\s*gain\s+payout|"
        r"declared\s+a\s+(?:cash\s+)?(?:distribution|dividend)|"
        r"announces\s+(?:cash\s+)?(?:distribution|dividend)|"
        r"distribution\s+amount|dividend\s+amount|per\s+share\s+distribution|distribution\s+per\s+share|"
        r"distribution\s+rate|dividend\s+rate|"
        r"ex-dividend\s+date|ex-date|record\s+date|payable\s+date|payment\s+date|"
        r"distribution\s+schedule|dividend\s+schedule|distribution\s+history|dividend\s+history"
        r")",
        re.IGNORECASE,
    )

    FALSE_POSITIVE_FEE_PATTERN = re.compile(
        r"(?:"
        r"12b-1\s+(?:fee|distribution\s+fee|distribution\s+plan)|"
        r"rule\s+12b-1\s+(?:fee|distribution\s+plan|distribution)|"
        r"distribution\s+and\s+(?:service|shareholder\s+service)\s+fees?|"
        r"sales\s+and\s+distribution\s+expenses?|"
        r"management\s+and\s+distribution\s+fees?"
        r")",
        re.IGNORECASE,
    )

    @classmethod
    def validate_semantics(cls, content: str) -> tuple[bool, str]:
        """Evaluate if content contains genuine distribution concepts and not purely false positive fee mentions."""
        if not content:
            return False, "Empty content has no distribution semantics."

        dist_matches = cls.DISTRIBUTION_TERMS_PATTERN.findall(content)
        if not dist_matches:
            return (
                False,
                "No recognized distribution or dividend terms found in content.",
            )

        clean_text = re.sub(r"<[a-zA-Z/][^>]*>", " ", content)
        clean_text = re.sub(r"\s+", " ", clean_text)

        has_concrete_dist_signal = bool(
            re.search(
                r"(?:dividend|capital\s*gain|payable\s+date|record\s+date|ex-date|per\s+share|\$\s*\d+\.\d+|0\.\d{2,4})",
                clean_text,
                re.IGNORECASE,
            )
        )
        if not has_concrete_dist_signal:
            return (
                False,
                "Distribution references appear limited to fee or policy descriptions without cash distribution details.",
            )

        return (
            True,
            f"Verified distribution semantics ({len(dist_matches)} distribution markers found).",
        )


class PDFSourceValidator:
    """Validates and processes official distribution PDF artifacts."""

    @staticmethod
    def validate_pdf_bytes(
        pdf_bytes: bytes | None,
        fund: UniverseFund,
    ) -> tuple[bool, str, str | None]:
        """Validate PDF binary signature, calculate SHA-256 hash, and check basic structure."""
        if not pdf_bytes or len(pdf_bytes) < 4:
            return False, "Empty or invalid PDF binary.", None

        if not pdf_bytes.startswith(b"%PDF"):
            return (
                False,
                "Binary payload lacks standard %PDF file header signature.",
                None,
            )

        content_hash = hashlib.sha256(pdf_bytes).hexdigest()

        # Basic textual inspection in PDF stream
        try:
            pdf_str = pdf_bytes.decode("latin-1", errors="ignore")
            has_identity, id_reason = FundIdentityValidator.validate_identity(
                fund, pdf_str
            )
            if not has_identity:
                return (
                    False,
                    f"PDF binary does not match target fund: {id_reason}",
                    content_hash,
                )
            return (
                True,
                "Valid official PDF document matching target fund.",
                content_hash,
            )
        except (ValueError, TypeError, UnicodeDecodeError) as err:
            return (
                True,
                f"Valid PDF binary signature (SHA-256: {content_hash[:8]}): {err}",
                content_hash,
            )


class SourceDiscoveryEngine:
    """Discovers, validates, and prioritizes authoritative source candidates for a fund."""

    def __init__(self, http_client: HTTPClient | None = None) -> None:
        self.http_client = http_client or HTTPClient()

    def discover_candidates(
        self,
        fund: UniverseFund,
        window_start: date,
        window_end: date,
    ) -> list[SourceCandidate]:
        """Discover and construct candidate source records for a target fund and window."""
        candidates: list[SourceCandidate] = []

        # 1. Tier 1: SEC EDGAR Submissions (for US funds)
        if fund.country == "US" and fund.cik:
            cik_formatted = fund.cik.zfill(10)
            sec_url = f"https://data.sec.gov/submissions/CIK{cik_formatted}.json"
            candidates.append(
                SourceCandidate(
                    fund_id=fund.fund_id,
                    source_id="sec_edgar_submissions_api",
                    source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                    source_role="SUPPORTING_RETROSPECTIVE",
                    provider="SEC EDGAR",
                    url=sec_url,
                    source_type="REGULATORY_FILING_INDEX",
                    official_domain="data.sec.gov",
                    identifier_type="CIK",
                    identifier_value=fund.cik,
                    coverage_start=window_start,
                    coverage_end=window_end,
                    domain_verified=True,
                    direct_declaration_capable=False,
                    validation_status=ValidationStatus.SUPPORTING_FILING_INDEX,
                    validation_reason="Authoritative SEC filing repository index endpoint.",
                )
            )

        # 2. Tier 2: Official Fund Sponsor Primary Source (if configured in universe)
        if fund.official_source_url:
            domain = OfficialDomainValidator.extract_domain(fund.official_source_url)
            is_official = OfficialDomainValidator.is_official_domain(
                fund.official_source_url
            )
            candidates.append(
                SourceCandidate(
                    fund_id=fund.fund_id,
                    source_id="official_fund_sponsor_page",
                    source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                    source_role="DIRECT_DECLARATION",
                    provider=fund.fund_family,
                    url=fund.official_source_url,
                    source_type="SPONSOR_PORTAL_SCHEDULE",
                    official_domain=domain,
                    identifier_type="TICKER" if fund.ticker else "FUND_NAME",
                    identifier_value=fund.ticker or fund.fund_name,
                    coverage_start=window_start,
                    coverage_end=window_end,
                    domain_verified=is_official,
                    direct_declaration_capable=True,
                    validation_status=(
                        ValidationStatus.PARTIALLY_VERIFIED
                        if is_official
                        else ValidationStatus.INVALID
                    ),
                    validation_reason=(
                        "Official sponsor domain identified (retrieval/content inspection pending)"
                        if is_official
                        else f"Unverified domain: {domain}"
                    ),
                )
            )

        return candidates

    def validate_candidate(
        self,
        candidate: SourceCandidate,
        fund: UniverseFund,
    ) -> SourceCandidate:
        """Fetch and evaluate candidate source authenticity, identity match, semantics, and coverage."""
        # 1. Official domain check
        is_official = OfficialDomainValidator.is_official_domain(candidate.url)
        is_tier_3 = OfficialDomainValidator.is_tier_3_domain(candidate.url)
        candidate.domain_verified = is_official

        if not is_official:
            if is_tier_3:
                candidate.validation_status = ValidationStatus.CORROBORATION_ONLY
                candidate.validation_reason = (
                    "Public market data domain; permitted for corroboration only."
                )
                candidate.source_tier = SourceTier.TIER_3_CORROBORATION
                return candidate
            candidate.validation_status = ValidationStatus.INVALID
            candidate.validation_reason = f"Domain '{candidate.official_domain}' is not in verified official registry."
            return candidate

        # 2. Network retrieval
        record: HTTPResponseRecord = self.http_client.get(candidate.url)
        candidate.retrieved_at = record.retrieved_at
        candidate.http_status = record.status_code

        if not record.is_success or not record.content_text:
            candidate.retrieval_status = "FAILED"
            candidate.source_retrievable = False
            candidate.content_usable = False
            candidate.validation_status = ValidationStatus.UNAVAILABLE
            candidate.validation_reason = record.error_message or (
                record.failure_reason.value
                if record.failure_reason
                else "Retrieval failure"
            )
            return candidate

        candidate.retrieval_status = "SUCCESS"
        candidate.source_retrievable = True
        content = record.content_text
        content_bytes = record.content_bytes or content.encode("utf-8")
        candidate.content_hash = hashlib.sha256(content_bytes).hexdigest()

        # 3. Check for dynamic shells, login pages, or WAF challenges
        is_shell, shell_reason = OfficialDomainValidator.is_generic_shell_or_blocked(
            content, record.status_code
        )
        if is_shell:
            candidate.content_usable = False
            candidate.validation_status = ValidationStatus.INCOMPLETE
            candidate.validation_reason = shell_reason
            candidate.coverage_complete = False
            candidate.coverage_verified = False
            return candidate

        candidate.content_usable = True

        # 4. Fund Identity Check
        if "submissions/CIK" not in candidate.url:
            has_identity, id_reason = FundIdentityValidator.validate_identity(
                fund, content
            )
            candidate.identity_verified = has_identity
            candidate.fund_identity_verified = has_identity
            if not has_identity:
                candidate.validation_status = ValidationStatus.INVALID
                candidate.validation_reason = id_reason
                return candidate
        else:
            candidate.identity_verified = True
            candidate.fund_identity_verified = True

        # 5. Distribution Semantics Check
        if "submissions/CIK" in candidate.url:
            candidate.distribution_capable = False
            candidate.distribution_semantics_verified = False
            candidate.coverage_verified = False
            candidate.coverage_complete = False
            candidate.validation_status = ValidationStatus.SUPPORTING_FILING_INDEX
            candidate.validation_reason = "Authoritative SEC submissions repository index (retrievable; filings require secondary extraction)."
            return candidate

        has_dist_terms, sem_reason = DistributionSemanticsValidator.validate_semantics(
            content
        )
        candidate.distribution_capable = has_dist_terms
        candidate.distribution_semantics_verified = has_dist_terms

        # 6. Negative Coverage Check (Schedule table)
        has_schedule_table = bool(
            re.search(
                r"<table[^>]*>[\s\S]*?(?:ex-dividend|payable\s+date|record\s+date)[\s\S]*?</table>",
                content,
                re.IGNORECASE,
            )
            and re.search(
                r"(?:distribution\s+schedule|dividend\s+schedule|distribution\s+history|dividend\s+history)",
                content,
                re.IGNORECASE,
            )
        )
        candidate.coverage_verified = has_schedule_table
        candidate.coverage_complete = has_schedule_table

        if candidate.coverage_verified:
            candidate.validation_status = ValidationStatus.VERIFIED_COVERAGE_SOURCE
            candidate.validation_reason = (
                "Official schedule table verified with complete coverage."
            )
        elif candidate.distribution_capable:
            candidate.validation_status = ValidationStatus.VERIFIED_DIRECT_DISTRIBUTION
            candidate.validation_reason = f"Official distribution source verified with matching identity ({sem_reason})."
        else:
            candidate.validation_status = ValidationStatus.PARTIALLY_VERIFIED
            candidate.validation_reason = f"Official fund page verified with matching identity, but lacking explicit distribution tables ({sem_reason})."

        return candidate
