"""Detection strategy interfaces, observation models, and real-source adapters for Layer A Atomic Detector.

Strategies produce observations (signals or evidence candidates) that are
synthesized by the core detector without inventing or assuming facts.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum

from src.http_client import HTTPClient
from src.models import (
    Evidence,
    ExtractionRoute,
    SourceTier,
    UnknownReason,
)
from src.universe_loader import UniverseFund, UniverseRegistry

logger = logging.getLogger(__name__)


class SignalType(str, Enum):
    """Categorization of strategy outputs."""

    TRIGGER_SIGNAL = "TRIGGER_SIGNAL"  # Internal indicator (e.g. calendar due)
    EVIDENCE_OBSERVATION = "EVIDENCE_OBSERVATION"  # Factual retrieval with evidence
    NO_DATA_OBSERVATION = "NO_DATA_OBSERVATION"  # Source checked, no declaration


@dataclass
class StrategyObservation:
    """Standardized output produced by any detection strategy inspection."""

    strategy_name: str
    signal_type: SignalType
    evidence: list[Evidence] = field(default_factory=list)
    has_declaration_in_window: bool = False
    window_fully_covered: bool = False
    suggested_route: ExtractionRoute | None = None
    failure_reason: UnknownReason | None = None
    notes: str = ""


MONTH_NAMES_MAP = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _extract_date(text: str) -> date | None:
    """Extract a date from text in ISO (YYYY-MM-DD), US (MM/DD/YYYY), or Textual (Month DD, YYYY) format."""
    if not text:
        return None
    # 1. ISO format: 2026-02-18
    m_iso = re.search(r"\b(202[0-9])-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])\b", text)
    if m_iso:
        try:
            return date.fromisoformat(m_iso.group(0))
        except ValueError:
            pass
    # 2. Textual format: February 18, 2026 or Feb. 18, 2026
    m_text = re.search(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December|Jan\.?|Feb\.?|Mar\.?|Apr\.?|May\.?|Jun\.?|Jul\.?|Aug\.?|Sep\.?|Oct\.?|Nov\.?|Dec\.?)\s+([0-9]{1,2}),?\s+(202[0-9])\b",
        text,
        re.IGNORECASE,
    )
    if m_text:
        m_str = m_text.group(1).lower().rstrip(".")
        month = MONTH_NAMES_MAP.get(m_str)
        day = int(m_text.group(2))
        year = int(m_text.group(3))
        if month:
            try:
                return date(year, month, day)
            except ValueError:
                pass
    # 3. US slash format: 02/18/2026
    m_us = re.search(
        r"\b(0[1-9]|1[0-2])/([0-3][0-9])/(202[0-9])\b",
        text,
    )
    if m_us:
        try:
            return date(int(m_us.group(3)), int(m_us.group(1)), int(m_us.group(2)))
        except ValueError:
            pass
    return None


class BaseDetectionStrategy(ABC):
    """Abstract base class for Layer A detection strategies."""

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def inspect(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
    ) -> StrategyObservation:
        """Inspect source/channel for distribution declaration in the given window.

        Args:
            fund_id: The internal fund identifier.
            window_start: Start of declaration date window (inclusive).
            window_end: End of declaration date window (inclusive).

        Returns:
            StrategyObservation containing findings and evidence.
        """


class CalendarExpectationStrategy(BaseDetectionStrategy):
    """Calendar-based expectation strategy.

    Rule: This strategy acts solely as an internal scheduling TRIGGER/SIGNAL.
    It NEVER directly declares distributions or confirms non-declarations.
    """

    def __init__(self, name: str = "CalendarExpectationStrategy") -> None:
        super().__init__(name=name)

    def inspect(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
    ) -> StrategyObservation:
        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.TRIGGER_SIGNAL,
            has_declaration_in_window=False,
            window_fully_covered=False,
            notes="Calendar expectation is a signal only; does not establish factual declaration.",
        )


class ChangeDetectionStrategy(BaseDetectionStrategy):
    """Monitors hash/content changes on official fund distribution pages or documents."""

    def __init__(self, name: str = "ChangeDetectionStrategy") -> None:
        super().__init__(name=name)

    def inspect(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
    ) -> StrategyObservation:
        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.TRIGGER_SIGNAL,
            has_declaration_in_window=False,
            window_fully_covered=False,
            notes="Change detection indicates potential update; requires evidence extraction.",
        )


class SECEdgarSubmissionsStrategy(BaseDetectionStrategy):
    """Authoritative Tier 1 strategy polling SEC EDGAR submissions API for US funds."""

    def __init__(
        self,
        http_client: HTTPClient | None = None,
        universe: UniverseRegistry | None = None,
        name: str = "SECEdgarSubmissionsStrategy",
    ) -> None:
        super().__init__(name=name)
        self.http_client = http_client or HTTPClient()
        self.universe = universe

    def _get_fund(self, fund_id: str) -> UniverseFund | None:
        if self.universe is not None:
            return self.universe.get_fund(fund_id)
        try:
            reg = UniverseRegistry.from_json()
            return reg.get_fund(fund_id)
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def _inspect_filing_content(
        self,
        doc_url: str,
        form: str,
        filing_date: date,
        fund: UniverseFund,
        record_retrieved_at: datetime,
    ) -> tuple[bool, Evidence | None]:
        """Fetch and inspect actual SEC document text for genuine distribution declaration.

        Strictly enforces:
        1. Fund Identity: Target fund must be explicitly identified in the filing title/cover/header
           block or via exact SEC Series ID, preventing cross-fund contamination from trustee equity tables.
        2. Distribution Semantics: Requires operative declaration language or Rule 19a-1 source notice.
           Explicitly rejects 12b-1 fees, qualified dividend tax narrative, and descriptive boilerplate.
        """
        doc_record = self.http_client.get(doc_url)
        if not doc_record.is_success or not doc_record.content_text:
            return False, None

        raw_text = doc_record.content_text
        clean_text = re.sub(r"<[^>]+>", " ", raw_text)
        clean_text = re.sub(r"\s+", " ", clean_text).strip()

        # 1. Fund Identity Validation
        # Strip trustee / officer ownership & compensation disclosure blocks before checking identity
        clean_text_no_trustee = re.sub(
            r"(?:Trustee|Director|Officer)\s+(?:Equity\s+)?(?:Ownership|Holdings|Compensation).*?(?=(?:<h2>|<h3>|<h1>|<p>|Taxes\b|Investment\b|Portfolio\b|Description\b|Distribution\s+of\s+Shares|Financial\b|Item\b|\Z))",
            " ",
            clean_text,
            flags=re.IGNORECASE,
        )
        header_text = clean_text_no_trustee[:12000]
        ticker_in_header = bool(
            fund.ticker
            and re.search(rf"\b{re.escape(fund.ticker)}\b", header_text, re.IGNORECASE)
        )
        name_in_header = bool(
            fund.fund_name and fund.fund_name.lower() in header_text.lower()
        )
        series_id_match = bool(
            fund.sec_series_id and fund.sec_series_id in clean_text_no_trustee
        )

        if not (ticker_in_header or name_in_header or series_id_match):
            return False, None

        # 2. Distribution Semantics Validation
        # Strip statutory tax explanations, spillback boilerplate, 12b-1 fees, and descriptive policy text
        sanitized_text = re.sub(
            r"(?:dividend|distribution)\s+declared\s+by\s+(?:the|a)\s+fund\s+in\s+october,\s*november\s+or\s+december[^.]*\.",
            " ",
            clean_text,
            flags=re.IGNORECASE,
        )
        sanitized_text = re.sub(
            r"capital\s+gain\s+rates\s+to\s+the\s+extent\s+the\s+fund\s+receives\s+qualified\s+dividend\s+income[^.]*\.",
            " ",
            sanitized_text,
            flags=re.IGNORECASE,
        )
        sanitized_text = re.sub(
            r"(?:qualified\s+dividend\s+income|corporate\s+dividends\s+received\s+deduction|taxable\s+at\s+capital\s+gain\s+rates)[^.]*\.",
            " ",
            sanitized_text,
            flags=re.IGNORECASE,
        )
        sanitized_text = re.sub(
            r"12b-1\s+distribution\s+fee[^.]*\.",
            " ",
            sanitized_text,
            flags=re.IGNORECASE,
        )
        sanitized_text = re.sub(
            r"(?:the\s+fund\s+(?:intends|expects)\s+to\s+distribute|distributions,\s+if\s+any,\s+will\s+be\s+declared)[^.]*\.",
            " ",
            sanitized_text,
            flags=re.IGNORECASE,
        )

        # Operative Declaration Evaluation
        # Case A: Rule 19a-1 source notice
        if (
            form.upper() in {"19A-1", "19A1"}
            or "rule 19a-1" in sanitized_text.lower()
            or "section 19(a)" in sanitized_text.lower()
        ) and re.search(
            r"source\s+of\s+distribution|section\s+19\(a\)|rule\s+19a-1",
            sanitized_text,
            re.IGNORECASE,
        ):
            match = re.search(
                r"(?:source\s+of\s+distribution|notice\s+pursuant\s+to\s+rule\s+19a-1|notice\s+of\s+distribution|has\s+declared\s+a\s+(?:monthly\s+|quarterly\s+|cash\s+)*distribution|declared\s+a\s+(?:monthly\s+|quarterly\s+|cash\s+)*distribution).*?(?:\.\s|\n|$)",
                sanitized_text,
                re.IGNORECASE,
            )
            snippet_text = (
                match.group(0).strip()[:200]
                if match
                else f"Form {form} Rule 19a-1 distribution notice found."
            )
            decl_m = re.search(
                r"(?:declared|declaration\s+date)[\s:]+([A-Za-z0-9,\s/-]+)",
                sanitized_text,
                re.IGNORECASE,
            )
            decl_date = _extract_date(decl_m.group(0)) if decl_m else None
            if not decl_date:
                decl_date = filing_date

            ev = Evidence(
                source_id="sec_edgar_submissions_filing",
                source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                url=doc_url,
                retrieved_at=doc_record.retrieved_at,
                snippet_or_locator=f"Form {form} ({fund.ticker}): '{snippet_text}'",
                declaration_date_found=decl_date,
            )
            return True, ev

        # Case B: Explicit Board / Fund Declaration Announcement
        decl_match = (
            re.search(
                r"(?:Board\s+of\s+(?:Trustees|Directors)|The\s+Fund|Fund)\s+(?:has\s+)?(?:declared|declares|announced|announces)\s+(?:a\s+)?(?:quarterly|monthly|annual|semi-annual|special)?\s*(?:cash\s+)?(?:dividend|distribution|capital\s*gain)\s+(?:of\s+\$[0-9]|\$[0-9]|payable).*?(?:\.\s|\n|$)",
                sanitized_text,
                re.IGNORECASE,
            )
            or re.search(
                r"(?:dividend|distribution)\s+(?:declared|payable)\s+on\s+[A-Za-z0-9,\s/-]+\s+to\s+shareholders\s+of\s+record.*?(?:\.\s|\n|$)",
                sanitized_text,
                re.IGNORECASE,
            )
            or re.search(
                r"distribution\s+rate\s+of\s+\$[0-9].*?(?:\.\s|\n|$)",
                sanitized_text,
                re.IGNORECASE,
            )
            or re.search(
                r"notice\s+of\s+(?:dividend\s+)?distribution.*?(?:declared|payable|rate).*?(?:\.\s|\n|$)",
                sanitized_text,
                re.IGNORECASE,
            )
        )

        if decl_match:
            snippet_text = decl_match.group(0).strip()[:200]
            decl_m = re.search(
                r"(?:declared|declaration\s+date)[\s:]+([A-Za-z0-9,\s/-]+)",
                sanitized_text,
                re.IGNORECASE,
            )
            decl_date = _extract_date(decl_m.group(0)) if decl_m else None
            if not decl_date:
                decl_date = filing_date

            ev = Evidence(
                source_id="sec_edgar_submissions_filing",
                source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                url=doc_url,
                retrieved_at=doc_record.retrieved_at,
                snippet_or_locator=f"Form {form} ({fund.ticker}): '{snippet_text}'",
                declaration_date_found=decl_date,
            )
            return True, ev

        return False, None

    def inspect(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
    ) -> StrategyObservation:
        fund = self._get_fund(fund_id)
        if not fund or not fund.cik or fund.country != "US":
            # Canadian funds or funds without CIK are not applicable for SEC submissions
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                has_declaration_in_window=False,
                window_fully_covered=False,
                failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
                notes="Fund is non-US or lacks verified SEC CIK.",
            )

        cik_formatted = fund.cik.zfill(10)
        sec_url = f"https://data.sec.gov/submissions/CIK{cik_formatted}.json"

        record = self.http_client.get(sec_url)
        if not record.is_success or not record.content_text:
            failure_reason = record.failure_reason or UnknownReason.RETRIEVAL_FAILED
            err_evidence = Evidence(
                source_id="sec_edgar_submissions_api",
                source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                url=sec_url,
                retrieved_at=record.retrieved_at,
                snippet_or_locator=f"SEC submissions query failed: {record.error_message or failure_reason.value}",
                failure_reason=failure_reason,
            )
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                evidence=[err_evidence],
                has_declaration_in_window=False,
                window_fully_covered=False,
                failure_reason=failure_reason,
                notes=f"SEC submissions API error: {failure_reason.value}",
            )

        try:
            data = json.loads(record.content_text)
            recent = data.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            filing_dates = recent.get("filingDate", [])
            accessions = recent.get("accessionNumber", [])
            primary_docs = recent.get("primaryDocument", [])
            primary_doc_descs = recent.get("primaryDocDescription", [])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as err:
            err_evidence = Evidence(
                source_id="sec_edgar_submissions_api",
                source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                url=sec_url,
                retrieved_at=record.retrieved_at,
                snippet_or_locator=f"Failed to parse SEC submissions JSON: {err!s}",
                failure_reason=UnknownReason.INCOMPLETE_SOURCE,
            )
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                evidence=[err_evidence],
                has_declaration_in_window=False,
                window_fully_covered=False,
                failure_reason=UnknownReason.INCOMPLETE_SOURCE,
                notes=f"JSON parse error: {err!s}",
            )

        # Check filings within requested date window
        evidences: list[Evidence] = []
        has_positive_declaration = False

        for form, f_date_str, acc, doc, desc in zip(
            forms, filing_dates, accessions, primary_docs, primary_doc_descs
        ):
            try:
                f_date = date.fromisoformat(f_date_str)
            except (ValueError, TypeError):
                continue

            if window_start <= f_date <= window_end:
                # Check for distribution-relevant candidate forms (Form 497, 497K, 497J, Rule 19a-1, 8-K)
                desc_str = (desc or "").lower()
                is_distribution_candidate = form in {
                    "497",
                    "497K",
                    "497J",
                    "19A-1",
                    "8-K",
                } and (
                    any(
                        kw in desc_str
                        for kw in [
                            "dividend",
                            "distribution",
                            "capital gain",
                            "rate",
                            "notice",
                        ]
                    )
                    or form in {"19A-1", "497"}
                )

                if is_distribution_candidate and doc:
                    acc_clean = acc.replace("-", "")
                    doc_url = f"https://www.sec.gov/Archives/edgar/data/{int(fund.cik)}/{acc_clean}/{doc}"
                    has_decl, ev = self._inspect_filing_content(
                        doc_url=doc_url,
                        form=form,
                        filing_date=f_date,
                        fund=fund,
                        record_retrieved_at=record.retrieved_at,
                    )
                    if has_decl and ev:
                        evidences.append(ev)
                        has_positive_declaration = True

        if has_positive_declaration:
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.EVIDENCE_OBSERVATION,
                evidence=evidences,
                has_declaration_in_window=True,
                window_fully_covered=True,
                suggested_route=ExtractionRoute.FILING,
                notes="Authoritative SEC filing found within requested declaration window.",
            )

        # Note on SEC Negative Coverage:
        # Absence of Form 497 or other candidate filings in the submissions feed does NOT prove non-declaration.
        # Other filing categories (e.g. N-CSR, N-PORT) or off-EDGAR channels may exist.
        # Therefore, the submissions index query alone does not establish complete negative coverage.
        index_evidence = Evidence(
            source_id="sec_edgar_submissions_api",
            source_tier=SourceTier.TIER_1_AUTHORITATIVE,
            url=sec_url,
            retrieved_at=record.retrieved_at,
            snippet_or_locator=f"SEC submissions index for CIK {cik_formatted} inspected for window [{window_start} to {window_end}]. Zero positive declaration filings detected (insufficient for full negative proof).",
            failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
        )
        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.NO_DATA_OBSERVATION,
            evidence=[index_evidence],
            has_declaration_in_window=False,
            window_fully_covered=False,
            failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
            notes="SEC submissions index checked; zero positive distribution filings detected. Submissions index alone does not constitute complete negative proof.",
        )


class OfficialSponsorWebStrategy(BaseDetectionStrategy):
    """Tier 2 Primary strategy inspecting official fund sponsor distribution schedules."""

    def __init__(
        self,
        http_client: HTTPClient | None = None,
        universe: UniverseRegistry | None = None,
        name: str = "OfficialSponsorWebStrategy",
    ) -> None:
        super().__init__(name=name)
        self.http_client = http_client or HTTPClient()
        self.universe = universe

    def _get_fund(self, fund_id: str) -> UniverseFund | None:
        if self.universe is not None:
            return self.universe.get_fund(fund_id)
        try:
            reg = UniverseRegistry.from_json()
            return reg.get_fund(fund_id)
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def inspect(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
    ) -> StrategyObservation:
        fund = self._get_fund(fund_id)
        if not fund or not fund.official_source_url:
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                has_declaration_in_window=False,
                window_fully_covered=False,
                failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
                notes="Fund missing official source URL in universe registry.",
            )

        url = fund.official_source_url
        record = self.http_client.get(url)

        if not record.is_success or not record.content_text:
            failure_reason = record.failure_reason or UnknownReason.RETRIEVAL_FAILED
            err_evidence = Evidence(
                source_id="official_fund_sponsor_page",
                source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                url=url,
                retrieved_at=record.retrieved_at,
                snippet_or_locator=f"Failed to retrieve official portal: {record.error_message or failure_reason.value}",
                failure_reason=failure_reason,
            )
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                evidence=[err_evidence],
                has_declaration_in_window=False,
                window_fully_covered=False,
                failure_reason=failure_reason,
                notes=f"Sponsor portal retrieval failure: {failure_reason.value}",
            )

        content = record.content_text
        evidences: list[Evidence] = []
        has_declaration = False

        # Deterministic pattern matching for declared distributions within window
        # Scans for distribution announcement phrasing and extracts the declaration/announcement date
        announcement_patterns = [
            re.compile(
                r"(?:(?:declared|announces|announced|cash\s+distribution|monthly\s+distribution|quarterly\s+distribution|dividend\s+distribution)[\s\S]{0,120})",
                re.IGNORECASE,
            ),
            re.compile(
                r"(?:(?:TORONTO|NEW\s+YORK|BOSTON|CHICAGO|MALVERN)[\s,\-]*[A-Za-z0-9,\s\-]{0,40}(?:declared|announces|announced|distribution|dividend)[\s\S]{0,120})",
                re.IGNORECASE,
            ),
            re.compile(
                r"(?:(?:declared|announcement|payable|distribution\s*date)[:\s]+[A-Za-z0-9,\s\-\/]{6,30})",
                re.IGNORECASE,
            ),
        ]

        seen_snippets: set[str] = set()
        seen_dates: set[date] = set()
        for pat in announcement_patterns:
            for match in pat.finditer(content):
                snippet = match.group(0).strip()
                clean_snippet = re.sub(
                    r"\s+", " ", re.sub(r"<[^>]+>", " ", snippet)
                ).strip()
                if clean_snippet in seen_snippets or len(clean_snippet) < 10:
                    continue
                seen_snippets.add(clean_snippet)

                decl_date = _extract_date(clean_snippet)
                if (
                    decl_date
                    and window_start <= decl_date <= window_end
                    and decl_date not in seen_dates
                ):
                    seen_dates.add(decl_date)
                    ev = Evidence(
                        source_id="official_fund_sponsor_page",
                        source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                        url=url,
                        retrieved_at=record.retrieved_at,
                        snippet_or_locator=f"Official distribution match: '{clean_snippet[:120]}'",
                        declaration_date_found=decl_date,
                    )
                    evidences.append(ev)
                    has_declaration = True

        if has_declaration:
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.EVIDENCE_OBSERVATION,
                evidence=evidences,
                has_declaration_in_window=True,
                window_fully_covered=True,
                suggested_route=ExtractionRoute.HTML_TABLE,
                notes="Official fund sponsor portal confirmed declaration in window.",
            )

        # Negative Coverage Check:
        # A static HTML response without regex matches does NOT automatically prove non-declaration.
        # Check whether the page actually contains a verifiable static HTML distribution schedule table.
        has_explicit_schedule_table = bool(
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

        if has_explicit_schedule_table:
            # The page demonstrably contains a full static schedule table with zero entries in the requested window
            audit_evidence = Evidence(
                source_id="official_fund_sponsor_page",
                source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                url=url,
                retrieved_at=record.retrieved_at,
                snippet_or_locator=f"Official distribution schedule table inspected for window [{window_start} to {window_end}]. Zero declared events confirmed.",
            )
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                evidence=[audit_evidence],
                has_declaration_in_window=False,
                window_fully_covered=True,
                notes="Sponsor distribution schedule table verified; zero declarations in window.",
            )

        # Page lacks an explicit verifiable static distribution schedule (e.g. dynamic/React single-page app or general profile)
        insufficient_evidence = Evidence(
            source_id="official_fund_sponsor_page",
            source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
            url=url,
            retrieved_at=record.retrieved_at,
            snippet_or_locator=f"Official portal HTML inspected for window [{window_start} to {window_end}]. No declared events detected, but page lacks an explicit static distribution schedule covering the window.",
            failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
        )
        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.NO_DATA_OBSERVATION,
            evidence=[insufficient_evidence],
            has_declaration_in_window=False,
            window_fully_covered=False,
            failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
            notes="Sponsor HTML retrieved but lacks verifiable distribution schedule table for window.",
        )


class FilingIndexPollingStrategy(BaseDetectionStrategy):
    """Polls authoritative regulatory filing indexes."""

    def __init__(self, name: str = "FilingIndexPollingStrategy") -> None:
        super().__init__(name=name)

    def inspect(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
    ) -> StrategyObservation:
        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.NO_DATA_OBSERVATION,
            has_declaration_in_window=False,
            window_fully_covered=False,
            failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
            notes="Filing index poller unconfigured without active source adapter.",
        )


class TargetedLookupStrategy(BaseDetectionStrategy):
    """Performs targeted query against secondary verified primary/authoritative endpoints."""

    def __init__(
        self,
        candidate_urls: list[str] | None = None,
        http_client: HTTPClient | None = None,
        universe: UniverseRegistry | None = None,
        name: str = "TargetedLookupStrategy",
    ) -> None:
        super().__init__(name=name)
        self.candidate_urls = candidate_urls or []
        self.http_client = http_client or HTTPClient()
        self.universe = universe

    def _get_fund(self, fund_id: str) -> UniverseFund | None:
        if self.universe is not None:
            return self.universe.get_fund(fund_id)
        try:
            reg = UniverseRegistry.from_json()
            return reg.get_fund(fund_id)
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def inspect(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
    ) -> StrategyObservation:
        fund = self._get_fund(fund_id)
        if not fund:
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                has_declaration_in_window=False,
                window_fully_covered=False,
                failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
                notes="Fund not found in universe registry.",
            )

        urls_to_try = list(self.candidate_urls)
        if not urls_to_try and fund.verification_audit:
            audit_url = fund.verification_audit.get(
                "official_url"
            ) or fund.verification_audit.get("source_url")
            if audit_url and audit_url != fund.official_source_url:
                urls_to_try.append(audit_url)

        if not urls_to_try:
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                has_declaration_in_window=False,
                window_fully_covered=False,
                failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
                notes="No targeted secondary candidate URLs configured.",
            )

        evidences: list[Evidence] = []
        has_declaration = False
        all_failures: list[UnknownReason] = []

        for target_url in urls_to_try:
            rec = self.http_client.get(target_url)
            if not rec.is_success or not rec.content_text:
                fail_reason = rec.failure_reason or UnknownReason.RETRIEVAL_FAILED
                all_failures.append(fail_reason)
                evidences.append(
                    Evidence(
                        source_id="targeted_lookup_source",
                        source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                        url=target_url,
                        retrieved_at=rec.retrieved_at,
                        snippet_or_locator=f"Targeted lookup retrieval failed: {rec.error_message or fail_reason.value}",
                        failure_reason=fail_reason,
                    )
                )
                continue

            content = rec.content_text
            clean_text = re.sub(r"<[^>]+>", " ", content)
            clean_text = re.sub(r"\s+", " ", clean_text).strip()

            ticker_match = bool(
                fund.ticker
                and re.search(
                    rf"\b{re.escape(fund.ticker)}\b", clean_text, re.IGNORECASE
                )
            )
            name_match = bool(
                fund.fund_name and fund.fund_name.lower() in clean_text.lower()
            )
            if not (ticker_match or name_match):
                continue

            decl_patterns = [
                re.compile(
                    r"(?:(?:declared|announces|announced|cash\s+distribution|monthly\s+distribution|quarterly\s+distribution|dividend\s+distribution)[\s\S]{0,120})",
                    re.IGNORECASE,
                ),
                re.compile(
                    r"(?:(?:declared|announcement|payable|distribution\s*date)[:\s]+[A-Za-z0-9,\s\-\/]{6,30})",
                    re.IGNORECASE,
                ),
            ]
            for pat in decl_patterns:
                for match in pat.finditer(clean_text):
                    snippet = match.group(0).strip()
                    decl_d = _extract_date(snippet)
                    if decl_d and window_start <= decl_d <= window_end:
                        ev = Evidence(
                            source_id="targeted_lookup_source",
                            source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                            url=target_url,
                            retrieved_at=rec.retrieved_at,
                            snippet_or_locator=f"Targeted lookup distribution match: '{snippet[:120]}'",
                            declaration_date_found=decl_d,
                        )
                        evidences.append(ev)
                        has_declaration = True
                        break
                if has_declaration:
                    break

        if has_declaration:
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.EVIDENCE_OBSERVATION,
                evidence=evidences,
                has_declaration_in_window=True,
                window_fully_covered=True,
                suggested_route=ExtractionRoute.WEB_PORTAL,
                notes="Targeted secondary source confirmed declaration in window.",
            )

        primary_fail = (
            all_failures[0] if all_failures else UnknownReason.INSUFFICIENT_EVIDENCE
        )
        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.NO_DATA_OBSERVATION,
            evidence=evidences,
            has_declaration_in_window=False,
            window_fully_covered=False,
            failure_reason=primary_fail,
            notes="Targeted lookup completed; no positive declaration found.",
        )


class Tier3CorroborationStrategy(BaseDetectionStrategy):
    """Tier 3 Corroboration Strategy inspecting public market data pages for cross-checking.

    CRITICAL ASSIGNMENT RULE: Tier 3 observations carry SourceTier.TIER_3_CORROBORATION.
    Tier 3 evidence alone must NEVER produce DECLARED or NOT_DECLARED.
    """

    def __init__(
        self,
        candidate_urls: list[str] | None = None,
        http_client: HTTPClient | None = None,
        universe: UniverseRegistry | None = None,
        name: str = "Tier3CorroborationStrategy",
    ) -> None:
        super().__init__(name=name)
        self.candidate_urls = candidate_urls or []
        self.http_client = http_client or HTTPClient()
        self.universe = universe

    def _get_fund(self, fund_id: str) -> UniverseFund | None:
        if self.universe is not None:
            return self.universe.get_fund(fund_id)
        try:
            reg = UniverseRegistry.from_json()
            return reg.get_fund(fund_id)
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def inspect(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
    ) -> StrategyObservation:
        fund = self._get_fund(fund_id)
        ticker = fund.ticker if fund else fund_id
        urls_to_try = list(self.candidate_urls)
        if not urls_to_try and ticker:
            urls_to_try.append(f"https://finance.yahoo.com/quote/{ticker}")

        evidences: list[Evidence] = []
        has_corroboration = False

        for url in urls_to_try:
            rec = self.http_client.get(url)
            if not rec.is_success or not rec.content_text:
                continue

            content = rec.content_text
            clean_text = re.sub(r"<[^>]+>", " ", content)
            clean_text = re.sub(r"\s+", " ", clean_text).strip()

            m = re.search(
                r"(?:dividend|distribution|yield)[:\s]+(?:Rate\s+)?([0-9]+\.[0-9]+|None)",
                clean_text,
                re.IGNORECASE,
            )
            if m:
                ev = Evidence(
                    source_id="public_market_data_corroboration",
                    source_tier=SourceTier.TIER_3_CORROBORATION,
                    url=url,
                    retrieved_at=rec.retrieved_at,
                    snippet_or_locator=f"Tier 3 market data cross-check: '{m.group(0)}'",
                )
                evidences.append(ev)
                has_corroboration = True

        if not evidences:
            fallback_ev = Evidence(
                source_id="public_market_data_corroboration",
                source_tier=SourceTier.TIER_3_CORROBORATION,
                url=urls_to_try[0] if urls_to_try else "https://finance.yahoo.com",
                retrieved_at=datetime.now(timezone.utc),
                snippet_or_locator="Tier 3 public market data checked; no corroboration record.",
                failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
            )
            evidences.append(fallback_ev)

        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.NO_DATA_OBSERVATION,  # Always NO_DATA_OBSERVATION so Tier 3 alone cannot declare
            evidence=evidences,
            has_declaration_in_window=has_corroboration,
            window_fully_covered=False,  # Tier 3 can never establish full negative coverage
            failure_reason=(
                None if has_corroboration else UnknownReason.INSUFFICIENT_EVIDENCE
            ),
            notes="Tier 3 corroboration only. Cannot independently establish DECLARED or NOT_DECLARED.",
        )
