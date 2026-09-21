"""Bulk Authentic Source Acquisition and Document Retrieval Layer for Phase 1 Layer A.

Coordinates retrievable document acquisition across SEC EDGAR filings, official sponsor
distribution portals/PDFs, and Canadian sources. Implements safe raw caching, strict domain
and identity validation, multi-level acquisition status tracking, and distribution event
candidate extraction without fabricating financial data.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

from src.http_client import HTTPClient, HTTPResponseRecord
from src.models import (
    SourceCandidate,
    SourceTier,
)
from src.source_discovery import (
    DistributionSemanticsValidator,
    FundIdentityValidator,
    OfficialDomainValidator,
    PDFSourceValidator,
)
from src.strategies import _extract_date
from src.universe_loader import UniverseFund

logger = logging.getLogger(__name__)


class AcquisitionStatus(str, Enum):
    """Granular acquisition outcome for a source document."""

    RETRIEVED = "RETRIEVED"
    NOT_RETRIEVED = "NOT_RETRIEVED"
    BLOCKED = "BLOCKED"
    TIMEOUT = "TIMEOUT"
    INVALID_CONTENT = "INVALID_CONTENT"
    REDIRECTED = "REDIRECTED"
    INCOMPLETE = "INCOMPLETE"


@dataclass
class AcquisitionResult:
    """Standardized result of acquiring and inspecting a specific document artifact."""

    source_id: str
    fund_id: str
    source_tier: SourceTier
    url: str
    final_url: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    content_hash: str | None = None
    retrieved_at: datetime | None = None
    status: AcquisitionStatus = AcquisitionStatus.NOT_RETRIEVED
    failure_reason: str | None = None
    content_text: str | None = None
    content_bytes: bytes | None = None
    extracted_evidence_candidates: list[dict[str, Any]] = field(default_factory=list)
    is_usable: bool = False
    is_distribution_evidence: bool = False
    is_coverage_evidence: bool = False
    redirect_chain: list[str] = field(default_factory=list)


class RawSourceCache:
    """Safe local cache storing raw retrieved bytes and audit metadata."""

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        if cache_dir is None:
            cache_dir = (
                Path(__file__).resolve().parent.parent / "quality" / "raw_sources"
            )
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get(self, url: str, content_hash: str) -> bytes | None:
        """Retrieve cached bytes by SHA-256 hash."""
        target_path = self.cache_dir / f"{content_hash}.bin"
        if target_path.exists():
            try:
                return target_path.read_bytes()
            except (OSError, ValueError):
                return None
        return None

    def put(
        self,
        url: str,
        content_bytes: bytes,
        content_hash: str,
        metadata: dict[str, Any],
    ) -> None:
        """Persist raw bytes and metadata sidecar file."""
        if not content_bytes or not content_hash:
            return
        try:
            bin_path = self.cache_dir / f"{content_hash}.bin"
            meta_path = self.cache_dir / f"{content_hash}.meta.json"

            if not bin_path.exists():
                bin_path.write_bytes(content_bytes)

            meta_data = {
                "url": url,
                "content_hash": content_hash,
                "cached_at": datetime.now(timezone.utc).isoformat(),
                **metadata,
            }
            meta_path.write_text(json.dumps(meta_data, indent=2), encoding="utf-8")
        except (OSError, TypeError, ValueError) as err:
            logger.warning("Failed to write to raw source cache: %s", err)


class SECDocumentAcquisition:
    """Acquires and inspects authentic SEC EDGAR submissions and primary filing documents."""

    # Prioritized SEC filing form types that can contain distribution disclosures
    PRIORITIZED_FORMS: ClassVar[set[str]] = {
        "19A-1",
        "497",
        "497K",
        "497J",
        "485BPOS",
        "N-CEN",
        "NPORT-P",
        "NPORT-EX",
        "8-K",
    }

    def __init__(
        self,
        http_client: HTTPClient | None = None,
        cache: RawSourceCache | None = None,
    ) -> None:
        self.http_client = http_client or HTTPClient()
        self.cache = cache or RawSourceCache()

    def fetch_submissions_index(self, cik: str) -> dict[str, Any] | None:
        """Fetch the authoritative submissions metadata JSON for a CIK."""
        cik_formatted = cik.zfill(10)
        url = f"https://data.sec.gov/submissions/CIK{cik_formatted}.json"
        record = self.http_client.get(url)
        if not record.is_success or not record.content_text:
            return None
        try:
            return json.loads(record.content_text)
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    def identify_relevant_filings(
        self,
        submissions_json: dict[str, Any],
        fund: UniverseFund,
        window_start: date,
        window_end: date,
    ) -> list[dict[str, Any]]:
        """Identify potentially relevant filings from submissions index near the target window."""
        recent = submissions_json.get("filings", {}).get("recent", {})
        if not recent or "form" not in recent:
            return []

        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])

        candidates: list[dict[str, Any]] = []

        for i in range(len(forms)):
            form_type = str(forms[i]).upper()
            f_date_str = str(filing_dates[i])
            accession = str(accessions[i])
            primary_doc = str(primary_docs[i])

            # Filter by form type priority
            if not any(p in form_type for p in self.PRIORITIZED_FORMS):
                continue

            try:
                f_date = date.fromisoformat(f_date_str)
            except ValueError:
                continue

            # Check if filing is within or reasonably close (±60 days) to target window
            days_from_start = (f_date - window_start).days
            days_from_end = (f_date - window_end).days
            if -60 <= days_from_start <= 60 or -60 <= days_from_end <= 60:
                candidates.append(
                    {
                        "form": form_type,
                        "filing_date": f_date,
                        "accession_number": accession,
                        "primary_document": primary_doc,
                    }
                )

        return candidates

    def build_document_url(
        self, cik: str, accession_number: str, primary_document: str
    ) -> str:
        """Construct canonical SEC EDGAR archive document URL."""
        cik_clean = str(int(cik))
        acc_clean = accession_number.replace("-", "")
        return f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{acc_clean}/{primary_document}"

    def fetch_and_inspect_filing(
        self,
        filing_meta: dict[str, Any],
        fund: UniverseFund,
        window_start: date,
        window_end: date,
    ) -> AcquisitionResult:
        """Retrieve and inspect a specific SEC filing document."""
        if not fund.cik:
            return AcquisitionResult(
                source_id="sec_edgar_filing",
                fund_id=fund.fund_id,
                source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                url="",
                status=AcquisitionStatus.NOT_RETRIEVED,
                failure_reason="Fund has no CIK",
            )

        doc_url = self.build_document_url(
            fund.cik,
            filing_meta["accession_number"],
            filing_meta["primary_document"],
        )

        record = self.http_client.get(doc_url)
        retrieved_at = record.retrieved_at

        if not record.is_success or not record.content_text:
            status = AcquisitionStatus.NOT_RETRIEVED
            if record.status_code in {401, 403}:
                status = AcquisitionStatus.BLOCKED
            elif record.status_code is None or (
                record.error_message and "timeout" in record.error_message.lower()
            ):
                status = AcquisitionStatus.TIMEOUT

            return AcquisitionResult(
                source_id="sec_edgar_filing",
                fund_id=fund.fund_id,
                source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                url=doc_url,
                http_status=record.status_code,
                retrieved_at=retrieved_at,
                status=status,
                failure_reason=record.error_message or "Filing retrieval failed",
            )

        content = record.content_text
        content_bytes = record.content_bytes or content.encode("utf-8")
        content_hash = hashlib.sha256(content_bytes).hexdigest()

        # Cache raw content
        self.cache.put(
            doc_url,
            content_bytes,
            content_hash,
            {"fund_id": fund.fund_id, "form": filing_meta["form"]},
        )

        # 1. Fund identity match inside filing
        has_identity, id_reason = FundIdentityValidator.validate_identity(fund, content)
        if not has_identity:
            return AcquisitionResult(
                source_id="sec_edgar_filing",
                fund_id=fund.fund_id,
                source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                url=doc_url,
                http_status=record.status_code,
                content_hash=content_hash,
                retrieved_at=retrieved_at,
                status=AcquisitionStatus.INVALID_CONTENT,
                failure_reason=f"Identity mismatch: {id_reason}",
                content_text=content,
            )

        # 2. Distribution Semantics validation (rejecting pure 12b-1 fee disclosures)
        has_dist, sem_reason = DistributionSemanticsValidator.validate_semantics(
            content
        )
        if not has_dist:
            return AcquisitionResult(
                source_id="sec_edgar_filing",
                fund_id=fund.fund_id,
                source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                url=doc_url,
                http_status=record.status_code,
                content_hash=content_hash,
                retrieved_at=retrieved_at,
                status=AcquisitionStatus.RETRIEVED,
                is_usable=True,
                is_distribution_evidence=False,
                failure_reason=sem_reason,
                content_text=content,
            )

        # 3. Extract event candidates
        evidence_candidates = self._extract_filing_evidence(
            content, filing_meta, window_start, window_end
        )

        is_dist_evidence = len(evidence_candidates) > 0

        return AcquisitionResult(
            source_id="sec_edgar_filing",
            fund_id=fund.fund_id,
            source_tier=SourceTier.TIER_1_AUTHORITATIVE,
            url=doc_url,
            http_status=record.status_code,
            content_hash=content_hash,
            retrieved_at=retrieved_at,
            status=AcquisitionStatus.RETRIEVED,
            is_usable=True,
            is_distribution_evidence=is_dist_evidence,
            extracted_evidence_candidates=evidence_candidates,
            content_text=content,
        )

    def _extract_filing_evidence(
        self,
        content: str,
        filing_meta: dict[str, Any],
        window_start: date,
        window_end: date,
    ) -> list[dict[str, Any]]:
        """Extract candidate distribution events from filing text."""
        events: list[dict[str, Any]] = []
        sentences = re.split(r"(?<=[.!?])\s+", content)

        for sent in sentences:
            if not re.search(
                r"(?:dividend|distribution|capital\s*gain)", sent, re.IGNORECASE
            ):
                continue
            if re.search(
                r"(?:12b-1|service\s+fee|distribution\s+plan|sales\s+and\s+distribution)",
                sent,
                re.IGNORECASE,
            ):
                continue

            extracted_d = _extract_date(sent)
            if extracted_d and window_start <= extracted_d <= window_end:
                # Per-share amount search
                amt_match = re.search(r"\$\s*([0-9]+\.[0-9]+)", sent)
                amount = float(amt_match.group(1)) if amt_match else None

                events.append(
                    {
                        "event_date": extracted_d.isoformat(),
                        "form": filing_meta["form"],
                        "filing_date": filing_meta["filing_date"].isoformat(),
                        "amount": amount,
                        "snippet": sent.strip()[:300],
                    }
                )
                break  # Record first conclusive event in window

        return events


class SponsorDocumentAcquisition:
    """Acquires and inspects official sponsor portal web pages and distribution PDF documents."""

    def __init__(
        self,
        http_client: HTTPClient | None = None,
        cache: RawSourceCache | None = None,
    ) -> None:
        self.http_client = http_client or HTTPClient()
        self.cache = cache or RawSourceCache()

    def fetch_and_inspect_sponsor_source(
        self,
        candidate: SourceCandidate,
        fund: UniverseFund,
        window_start: date,
        window_end: date,
    ) -> AcquisitionResult:
        """Retrieve and validate official sponsor source."""
        # Domain check
        if not OfficialDomainValidator.is_official_domain(candidate.url):
            return AcquisitionResult(
                source_id=candidate.source_id,
                fund_id=fund.fund_id,
                source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                url=candidate.url,
                status=AcquisitionStatus.INVALID_CONTENT,
                failure_reason=f"Domain '{candidate.official_domain}' is not in verified official registry.",
            )

        record = self.http_client.get(candidate.url)
        retrieved_at = record.retrieved_at

        # HTTP error / block / timeout handling
        if not record.is_success or (
            not record.content_text and not record.content_bytes
        ):
            status = AcquisitionStatus.NOT_RETRIEVED
            if record.status_code in {401, 403}:
                status = AcquisitionStatus.BLOCKED
            elif record.status_code in {301, 302, 307, 308}:
                status = AcquisitionStatus.REDIRECTED
            elif record.status_code is None or (
                record.error_message and "timeout" in record.error_message.lower()
            ):
                status = AcquisitionStatus.TIMEOUT

            return AcquisitionResult(
                source_id=candidate.source_id,
                fund_id=fund.fund_id,
                source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                url=candidate.url,
                http_status=record.status_code,
                retrieved_at=retrieved_at,
                status=status,
                failure_reason=record.error_message
                or (
                    record.failure_reason.value
                    if record.failure_reason
                    else "Retrieval failure"
                ),
            )

        content_bytes = record.content_bytes or (
            record.content_text.encode("utf-8") if record.content_text else b""
        )
        content_hash = hashlib.sha256(content_bytes).hexdigest()

        # Cache raw payload
        self.cache.put(
            candidate.url,
            content_bytes,
            content_hash,
            {"fund_id": fund.fund_id, "provider": candidate.provider},
        )

        # PDF Branch
        if content_bytes.startswith(b"%PDF") or candidate.url.lower().endswith(".pdf"):
            return self._inspect_pdf(
                candidate,
                fund,
                content_bytes,
                content_hash,
                record,
                window_start,
                window_end,
            )

        # HTML Branch
        content = record.content_text or ""
        is_shell, shell_reason = OfficialDomainValidator.is_generic_shell_or_blocked(
            content, record.status_code
        )
        if is_shell:
            return AcquisitionResult(
                source_id=candidate.source_id,
                fund_id=fund.fund_id,
                source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                url=candidate.url,
                http_status=record.status_code,
                content_hash=content_hash,
                retrieved_at=retrieved_at,
                status=AcquisitionStatus.INCOMPLETE,
                failure_reason=shell_reason,
                content_text=content,
            )

        # Identity validation
        has_id, id_reason = FundIdentityValidator.validate_identity(fund, content)
        if not has_id:
            return AcquisitionResult(
                source_id=candidate.source_id,
                fund_id=fund.fund_id,
                source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                url=candidate.url,
                http_status=record.status_code,
                content_hash=content_hash,
                retrieved_at=retrieved_at,
                status=AcquisitionStatus.INVALID_CONTENT,
                failure_reason=id_reason,
                content_text=content,
            )

        # Distribution Semantics
        has_dist, sem_reason = DistributionSemanticsValidator.validate_semantics(
            content
        )
        if not has_dist:
            return AcquisitionResult(
                source_id=candidate.source_id,
                fund_id=fund.fund_id,
                source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                url=candidate.url,
                http_status=record.status_code,
                content_hash=content_hash,
                retrieved_at=retrieved_at,
                status=AcquisitionStatus.RETRIEVED,
                is_usable=True,
                is_distribution_evidence=False,
                failure_reason=sem_reason,
                content_text=content,
            )

        # Schedule coverage check
        has_coverage = bool(
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

        evidence_candidates = self._extract_sponsor_evidence(
            content, window_start, window_end
        )

        return AcquisitionResult(
            source_id=candidate.source_id,
            fund_id=fund.fund_id,
            source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
            url=candidate.url,
            http_status=record.status_code,
            content_hash=content_hash,
            retrieved_at=retrieved_at,
            status=AcquisitionStatus.RETRIEVED,
            is_usable=True,
            is_distribution_evidence=len(evidence_candidates) > 0,
            is_coverage_evidence=has_coverage,
            extracted_evidence_candidates=evidence_candidates,
            content_text=content,
        )

    def _inspect_pdf(
        self,
        candidate: SourceCandidate,
        fund: UniverseFund,
        pdf_bytes: bytes,
        content_hash: str,
        record: HTTPResponseRecord,
        window_start: date,
        window_end: date,
    ) -> AcquisitionResult:
        """Validate and inspect distribution PDF document."""
        is_valid_pdf, pdf_reason, _ = PDFSourceValidator.validate_pdf_bytes(
            pdf_bytes, fund
        )
        if not is_valid_pdf:
            return AcquisitionResult(
                source_id=candidate.source_id,
                fund_id=fund.fund_id,
                source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                url=candidate.url,
                http_status=record.status_code,
                content_hash=content_hash,
                retrieved_at=record.retrieved_at,
                status=AcquisitionStatus.INVALID_CONTENT,
                failure_reason=pdf_reason,
            )

        # Basic textual extraction from PDF stream
        pdf_text = pdf_bytes.decode("latin-1", errors="ignore")
        _has_dist, _sem_reason = DistributionSemanticsValidator.validate_semantics(
            pdf_text
        )

        evidence_candidates = self._extract_sponsor_evidence(
            pdf_text, window_start, window_end
        )

        return AcquisitionResult(
            source_id=candidate.source_id,
            fund_id=fund.fund_id,
            source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
            url=candidate.url,
            http_status=record.status_code,
            content_hash=content_hash,
            retrieved_at=record.retrieved_at,
            status=AcquisitionStatus.RETRIEVED,
            is_usable=True,
            is_distribution_evidence=len(evidence_candidates) > 0,
            extracted_evidence_candidates=evidence_candidates,
            content_text=pdf_text[:5000],
        )

    def _extract_sponsor_evidence(
        self,
        content: str,
        window_start: date,
        window_end: date,
    ) -> list[dict[str, Any]]:
        """Extract candidate distribution events from sponsor content."""
        events: list[dict[str, Any]] = []
        sentences = re.split(r"(?<=[.!?\n])\s+", content)

        for sent in sentences:
            if not re.search(
                r"(?:dividend|distribution|capital\s*gain)", sent, re.IGNORECASE
            ):
                continue
            if re.search(
                r"(?:12b-1|distribution\s+fee|sales\s+and\s+distribution)",
                sent,
                re.IGNORECASE,
            ):
                continue

            extracted_d = _extract_date(sent)
            if extracted_d and window_start <= extracted_d <= window_end:
                amt_match = re.search(r"\$\s*([0-9]+\.[0-9]+)", sent)
                amount = float(amt_match.group(1)) if amt_match else None

                events.append(
                    {
                        "event_date": extracted_d.isoformat(),
                        "amount": amount,
                        "snippet": sent.strip()[:300],
                    }
                )
                break

        return events


class CanadianDocumentAcquisition:
    """Acquires and inspects Canadian regulatory, exchange, and sponsor sources."""

    def __init__(
        self,
        sponsor_acquisition: SponsorDocumentAcquisition | None = None,
    ) -> None:
        self.sponsor_acquisition = sponsor_acquisition or SponsorDocumentAcquisition()

    def fetch_and_inspect_canadian_source(
        self,
        candidate: SourceCandidate,
        fund: UniverseFund,
        window_start: date,
        window_end: date,
    ) -> AcquisitionResult:
        """Inspect Canadian source candidate."""
        # Handle CDS / Depository feeds requiring client authorization
        if "cds.ca" in candidate.url.lower():
            return AcquisitionResult(
                source_id=candidate.source_id,
                fund_id=fund.fund_id,
                source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                url=candidate.url,
                status=AcquisitionStatus.BLOCKED,
                failure_reason="CDS Enhanced Bulletins require authenticated depository connection (no public unauthenticated feed).",
            )

        # Delegate Canadian sponsor URLs to sponsor acquisition engine
        return self.sponsor_acquisition.fetch_and_inspect_sponsor_source(
            candidate, fund, window_start, window_end
        )


class SourceAcquisitionEngine:
    """Unified engine coordinating bulk authentic source acquisition."""

    def __init__(
        self,
        http_client: HTTPClient | None = None,
        cache: RawSourceCache | None = None,
    ) -> None:
        self.http_client = http_client or HTTPClient()
        self.cache = cache or RawSourceCache()
        self.sec_acquisition = SECDocumentAcquisition(
            http_client=self.http_client, cache=self.cache
        )
        self.sponsor_acquisition = SponsorDocumentAcquisition(
            http_client=self.http_client, cache=self.cache
        )
        self.canadian_acquisition = CanadianDocumentAcquisition(
            sponsor_acquisition=self.sponsor_acquisition
        )

    def acquire_for_fund(
        self,
        fund: UniverseFund,
        candidates: list[SourceCandidate],
        window_start: date,
        window_end: date,
    ) -> list[AcquisitionResult]:
        """Acquire documents for all discovered candidates for a single fund."""
        results: list[AcquisitionResult] = []

        for candidate in candidates:
            # 1. Tier 1 SEC EDGAR
            if candidate.source_id == "sec_edgar_submissions_api" and fund.cik:
                sub_json = self.sec_acquisition.fetch_submissions_index(fund.cik)
                if not sub_json:
                    results.append(
                        AcquisitionResult(
                            source_id="sec_edgar_submissions_api",
                            fund_id=fund.fund_id,
                            source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                            url=candidate.url,
                            status=AcquisitionStatus.NOT_RETRIEVED,
                            failure_reason="Failed to fetch SEC submissions index",
                        )
                    )
                    continue

                relevant_filings = self.sec_acquisition.identify_relevant_filings(
                    sub_json, fund, window_start, window_end
                )
                if not relevant_filings:
                    results.append(
                        AcquisitionResult(
                            source_id="sec_edgar_submissions_api",
                            fund_id=fund.fund_id,
                            source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                            url=candidate.url,
                            http_status=200,
                            status=AcquisitionStatus.RETRIEVED,
                            is_usable=True,
                            failure_reason="No relevant filing forms in target window",
                        )
                    )
                else:
                    for f_meta in relevant_filings[
                        :3
                    ]:  # Inspect up to 3 top relevant filings
                        f_res = self.sec_acquisition.fetch_and_inspect_filing(
                            f_meta, fund, window_start, window_end
                        )
                        results.append(f_res)

            # 2. Tier 2 Sponsor / Canadian
            elif candidate.source_tier == SourceTier.TIER_2_PRIMARY_UNSTRUCTURED:
                if fund.country == "CA":
                    res = self.canadian_acquisition.fetch_and_inspect_canadian_source(
                        candidate, fund, window_start, window_end
                    )
                else:
                    res = self.sponsor_acquisition.fetch_and_inspect_sponsor_source(
                        candidate, fund, window_start, window_end
                    )
                results.append(res)

        return results
