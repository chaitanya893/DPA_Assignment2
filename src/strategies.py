"""Detection strategy interfaces, observation models, and real-source adapters for Layer A Atomic Detector.

Strategies produce observations (signals or evidence candidates) that are
synthesized by the core detector without inventing or assuming facts.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

from src.http_client import HTTPClient
from src.models import (
    Evidence,
    ExtractionRoute,
    SourceTier,
    UnknownReason,
)
from src.text_utils import parse_date
from src.universe_loader import UniverseFund, UniverseRegistry

logger = logging.getLogger(__name__)


def _extract_text_from_pdf(pdf_bytes: bytes | None) -> str:
    """Safely extract plain text from PDF binary bytes using pypdf."""
    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF-") or PdfReader is None:
        return ""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                pages.append(t)
        return "\n".join(pages)
    except (OSError, ValueError, TypeError, RuntimeError) as e:
        logger.debug("PDF text extraction failed: %s", e)
        return ""


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
    # False when the strategy does not apply to this fund at all (e.g. SEC EDGAR for a
    # Canadian fund). Non-applicable observations are ignored by the synthesis, so they
    # can never block a NOT_DECLARED outcome.
    applicable: bool = True


def _extract_date(text: str) -> date | None:
    """First valid date in text (ISO, 'Month DD, YYYY', 'DD Month YYYY', MM/DD/YYYY)."""
    return parse_date(text)


_LABELS = {
    "declaration": r"(?:declaration\s+date|declared\s+on|date\s+declared|declared\s*:)",
    "ex": r"(?:ex[-\s]?(?:dividend\s+|distribution\s+)?date)",
    "record": r"(?:record\s+date|(?:holders|unitholders|shareholders)\s+of\s+record(?:\s+(?:on|at\s+the\s+close\s+of\s+business\s+on|as\s+of))?)",
    "payable": r"(?:payable\s+(?:date|on)|payment\s+date|pay\s+date|paid\s+on|payable)",
}
_DATELINE_RE = re.compile(
    r"(?:TORONTO|MONTREAL|VANCOUVER|CALGARY|NEW\s+YORK|BOSTON|CHICAGO|MALVERN|VALLEY\s+FORGE|SAN\s+FRANCISCO|NEWPORT\s+BEACH)"
    r"[\s,A-Za-z.()-]{0,40}?,\s*((?:[A-Z][a-z]{2,8}\.?\s+\d{1,2},?\s+20\d{2})|20\d{2}-\d{2}-\d{2})",
)


def labelled_dates(text: str) -> dict[str, date]:
    """Dates that sit directly after their own label (e.g. 'Record Date: March 20, 2024').

    A date is only ever assigned to the date type named by its label, so ex, record,
    payable and declaration dates are never confused with one another.
    """
    out: dict[str, date] = {}
    for kind, label in _LABELS.items():
        m = re.search(label + r"\s*[:\-]?\s*(.{0,40})", text, re.IGNORECASE)
        if m:
            d = parse_date(m.group(1))
            if d:
                out[kind] = d
    if "declaration" not in out:
        # "announced/declared ... on June 18, 2026" (no payable/paid/record wording in between)
        m = re.search(
            r"\b(?:announce[sd]?|declare[sd]?)\b(?:(?!payable|paid|record|ex-)(?:[^.]|\.(?=\d))){0,80}?\bon\s+(.{0,30})",
            text,
            re.IGNORECASE,
        )
        if m:
            d = parse_date(m.group(1))
            if d:
                out["declaration"] = d
    dl = _DATELINE_RE.search(text)
    if dl:
        d = parse_date(dl.group(1))
        if d:
            out["published"] = d
    return out


def _dates_in_window(ev: Evidence, window_start: date, window_end: date) -> bool:
    """Declaration, ex-date or publication date inside the window (record/payable alone do not count)."""
    return any(
        d is not None and window_start <= d <= window_end
        for d in (ev.declaration_date_found, ev.ex_date_found, ev.published_date_found)
    )


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
    """Detection strategy 1 (PDF Phase 1): calendar-based expectation.

    Emits a TRIGGER_SIGNAL only (on-cadence / off-cadence). It never declares a distribution
    and never confirms a non-declaration.
    """

    # Funds whose published pay months differ from the frequency default.
    SPECIAL_MONTHS: dict[str, tuple[set[int], str]] = {
        "VTIP": ({1, 4, 7, 10}, "QUARTERLY_TIPS (Jan, Apr, Jul, Oct)"),
        "FSKAX": ({4, 12}, "SEMI_ANNUAL (Apr, Dec)"),
        "IEFA": ({6, 12}, "SEMI_ANNUAL (Jun, Dec)"),
        "IEMG": ({6, 12}, "SEMI_ANNUAL (Jun, Dec)"),
        "TDB900": ({6, 12}, "SEMI_ANNUAL (Jun, Dec)"),
        "TDB902": ({6, 12}, "SEMI_ANNUAL (Jun, Dec)"),
        "FBALX": ({9, 12}, "SEMI_ANNUAL (Sep, Dec)"),
        "FZROX": ({12}, "ANNUAL (December)"),
        "FNCMX": ({12}, "ANNUAL (December)"),
        "SWPPX": ({12}, "ANNUAL (December)"),
        "HXT": ({12}, "ANNUAL (December)"),
        "HBB": ({12}, "ANNUAL (December)"),
    }

    def __init__(
        self,
        universe: UniverseRegistry | None = None,
        name: str = "CalendarExpectationStrategy",
    ) -> None:
        super().__init__(name=name)
        self.universe = universe

    def _get_fund(self, fund_id: str) -> UniverseFund | None:
        if self.universe is not None:
            return self.universe.get_fund(fund_id)
        try:
            return UniverseRegistry.from_json().get_fund(fund_id)
        except (OSError, ValueError, TypeError, KeyError):
            return None

    @classmethod
    def expected_in_window(
        cls, fund: UniverseFund, window_start: date, window_end: date
    ) -> tuple[bool, str]:
        """(on_cadence, description) for the fund's expected pay months in the window."""
        months: set[int] = set()
        cur = window_start.replace(day=1)
        while cur <= window_end:
            months.add(cur.month)
            cur = (
                date(cur.year + 1, 1, 1)
                if cur.month == 12
                else date(cur.year, cur.month + 1, 1)
            )
        freq = (fund.expected_frequency or "QUARTERLY").upper()
        if fund.is_monthly_payer or freq in ("MONTHLY", "DAILY"):
            return True, "MONTHLY (All Months)"
        if fund.ticker and fund.ticker in cls.SPECIAL_MONTHS:
            pay, desc = cls.SPECIAL_MONTHS[fund.ticker]
            return bool(months & pay), desc
        if freq == "ANNUAL":
            return 12 in months, "ANNUAL (December)"
        if freq == "SEMI_ANNUAL":
            return bool(months & {6, 12}), "SEMI_ANNUAL (Jun, Dec)"
        return bool(months & {3, 6, 9, 12}), "QUARTERLY (Mar, Jun, Sep, Dec)"

    def inspect(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
    ) -> StrategyObservation:
        fund = self._get_fund(fund_id)
        if fund is None:
            return StrategyObservation(
                self.name, SignalType.TRIGGER_SIGNAL, notes="Fund not in universe."
            )
        on, desc = self.expected_in_window(fund, window_start, window_end)
        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.TRIGGER_SIGNAL,
            notes=(
                f"Calendar expectation trigger ({'ON' if on else 'OFF'}-CADENCE for {desc}): "
                "signal only, does not establish factual declaration or non-declaration."
            ),
        )


@dataclass
class DistributionEvent:
    """An authenticated distribution declaration event."""

    distribution_type: str
    amount_per_share: float
    ex_date: date
    record_date: date | None = None
    payable_date: date | None = None
    declaration_date: date | None = None
    notice_url: str = ""


@dataclass
class VerifiedFundSchedule:
    """Represents a verified annual/periodic distribution schedule for a fund."""

    fund_id: str
    year: int
    scheduled_ex_dates: list[date]
    coverage_start: date
    coverage_end: date
    is_complete: bool = True
    source_url: str = ""
    source_tier: SourceTier = SourceTier.TIER_2_PRIMARY_UNSTRUCTURED
    events: list[DistributionEvent] = field(default_factory=list)


class VerifiedScheduleStrategy(BaseDetectionStrategy):
    """Test / injection strategy for schedules captured from a stored primary document.

    It only uses schedules passed in explicitly. It deliberately does NOT load
    ``config/*_distribution_schedules.json`` or any gold set: a detector that reads its own
    answer key cannot be measured against it, and hand-copied rows have no stored source
    document (PDF Phase 3 provenance rule). With no schedules it reports not-applicable.
    """

    def __init__(
        self,
        schedules: list[VerifiedFundSchedule] | None = None,
        universe: UniverseRegistry | None = None,
        name: str = "VerifiedScheduleStrategy",
    ) -> None:
        super().__init__(name=name)
        self.schedules_by_fund: dict[str, list[VerifiedFundSchedule]] = {}
        for sched in schedules or []:
            self.schedules_by_fund.setdefault(sched.fund_id.upper(), []).append(sched)
        self.universe = universe

    def _get_fund(self, fund_id: str) -> UniverseFund | None:
        if self.universe is not None:
            return self.universe.get_fund(fund_id)
        try:
            return UniverseRegistry.from_json().get_fund(fund_id)
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def inspect(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
    ) -> StrategyObservation:
        fund = self._get_fund(fund_id)
        keys = {fund_id.upper()}
        if fund:
            keys |= {
                k.upper() for k in (fund.fund_id, fund.ticker, fund.fundserv_code) if k
            }
        schedules = [s for k in keys for s in self.schedules_by_fund.get(k, [])]
        if not schedules:
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                applicable=False,
                notes="No injected verified schedule for this fund.",
            )

        display_name = fund.ticker if (fund and fund.ticker) else fund_id
        evidences: list[Evidence] = []
        ref: VerifiedFundSchedule | None = None
        for sched in schedules:
            for ev_item in sched.events:
                if window_start <= ev_item.ex_date <= window_end:
                    ref = ref or sched
                    evidences.append(
                        Evidence(
                            source_id=f"verified_schedule_{display_name.lower()}",
                            source_tier=sched.source_tier,
                            url=ev_item.notice_url
                            or sched.source_url
                            or "internal://schedule",
                            retrieved_at=datetime.now(timezone.utc),
                            snippet_or_locator=(
                                f"{ev_item.distribution_type} distribution, ${ev_item.amount_per_share:.6f}/share, "
                                f"Ex-Dividend {ev_item.ex_date.isoformat()}"
                            ),
                            declaration_date_found=ev_item.declaration_date,
                            ex_date_found=ev_item.ex_date,
                            record_date_found=ev_item.record_date,
                            payable_date_found=ev_item.payable_date,
                        )
                    )
            if not sched.events:
                for d in sched.scheduled_ex_dates:
                    if window_start <= d <= window_end:
                        ref = ref or sched
                        evidences.append(
                            Evidence(
                                source_id=f"verified_schedule_{display_name.lower()}",
                                source_tier=sched.source_tier,
                                url=sched.source_url or "internal://schedule",
                                retrieved_at=datetime.now(timezone.utc),
                                snippet_or_locator=f"Verified schedule lists ex-date {d.isoformat()} for {display_name}.",
                                ex_date_found=d,
                            )
                        )

        if evidences and ref is not None:
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.EVIDENCE_OBSERVATION,
                evidence=evidences,
                has_declaration_in_window=True,
                window_fully_covered=True,
                suggested_route=(
                    ExtractionRoute.PDF
                    if ref.source_url.lower().endswith(".pdf")
                    else ExtractionRoute.HTML_TABLE
                ),
                notes="Injected verified schedule confirms a distribution in the window.",
            )

        covering = next(
            (
                sc
                for sc in schedules
                if sc.is_complete
                and sc.coverage_start <= window_start
                and window_end <= sc.coverage_end
            ),
            None,
        )
        if covering:
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                evidence=[
                    Evidence(
                        source_id=f"verified_schedule_{display_name.lower()}",
                        source_tier=covering.source_tier,
                        url=covering.source_url or "internal://schedule",
                        retrieved_at=datetime.now(timezone.utc),
                        snippet_or_locator=(
                            f"Complete schedule for {display_name} ({covering.year}) has no event in "
                            f"[{window_start} to {window_end}]."
                        ),
                    )
                ],
                window_fully_covered=True,
                notes="Complete schedule covers the window; no distribution.",
            )
        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.NO_DATA_OBSERVATION,
            evidence=[
                Evidence(
                    source_id="verified_schedule",
                    source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                    url=(fund.official_source_url if fund else "internal://schedule"),
                    retrieved_at=datetime.now(timezone.utc),
                    snippet_or_locator=f"Schedule does not fully cover [{window_start} to {window_end}] for {display_name}.",
                    failure_reason=UnknownReason.INCOMPLETE_SOURCE,
                )
            ],
            failure_reason=UnknownReason.INCOMPLETE_SOURCE,
            notes="Schedule coverage incomplete for the window.",
        )


class ChangeDetectionStrategy(BaseDetectionStrategy):
    """Detection strategy 2 (PDF Phase 1): hash the fund's distribution page.

    Emits a TRIGGER_SIGNAL only. ``changed`` is True when the page hash differs from the
    last recorded hash, None on first sight. The sweep scheduler uses it to decide whether
    a fund needs a full check; the detector never treats it as evidence.
    """

    def __init__(
        self,
        http_client: HTTPClient | None = None,
        universe: UniverseRegistry | None = None,
        previous_hash_lookup: Callable[[str], str | None] | None = None,
        name: str = "ChangeDetectionStrategy",
    ) -> None:
        super().__init__(name=name)
        self.http_client = http_client or HTTPClient()
        self.universe = universe
        self.previous_hash_lookup = previous_hash_lookup or (lambda _url: None)

    def check(self, fund: UniverseFund) -> tuple[bool | None, str | None]:
        """Return (changed, current_sha256). changed=None means first observation or fetch failure."""
        if not fund.official_source_url:
            return None, None
        rec = self.http_client.get(fund.official_source_url)
        if not rec.is_success or rec.content_bytes is None:
            return None, None
        current = hashlib.sha256(rec.content_bytes).hexdigest()
        previous = self.previous_hash_lookup(fund.official_source_url)
        if previous is None:
            return None, current
        return previous != current, current

    def inspect(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
    ) -> StrategyObservation:
        fund = (
            self.universe.get_fund(fund_id)
            if self.universe
            else UniverseRegistry.from_json().get_fund(fund_id)
        )
        if fund is None:
            return StrategyObservation(
                self.name, SignalType.TRIGGER_SIGNAL, applicable=False
            )
        changed, sha = self.check(fund)
        state = (
            "FIRST_SEEN" if changed is None else ("CHANGED" if changed else "UNCHANGED")
        )
        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.TRIGGER_SIGNAL,
            notes=f"Page hash {state} ({sha[:12] if sha else 'n/a'}); trigger only, not evidence.",
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
            found = labelled_dates(sanitized_text)

            ev = Evidence(
                source_id="sec_edgar_submissions_filing",
                source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                url=doc_url,
                retrieved_at=doc_record.retrieved_at,
                snippet_or_locator=f"Form {form} ({fund.ticker}) filed {filing_date}: '{snippet_text}'",
                declaration_date_found=found.get("declaration"),
                ex_date_found=found.get("ex"),
                record_date_found=found.get("record"),
                payable_date_found=found.get("payable"),
                published_date_found=filing_date,
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
            found = labelled_dates(sanitized_text)

            ev = Evidence(
                source_id="sec_edgar_submissions_filing",
                source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                url=doc_url,
                retrieved_at=doc_record.retrieved_at,
                snippet_or_locator=f"Form {form} ({fund.ticker}) filed {filing_date}: '{snippet_text}'",
                declaration_date_found=found.get("declaration"),
                ex_date_found=found.get("ex"),
                record_date_found=found.get("record"),
                payable_date_found=found.get("payable"),
                published_date_found=filing_date,
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
        if not fund or fund.country != "US":
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                applicable=False,
                notes="SEC EDGAR does not apply to non-US funds.",
            )
        if not fund.cik:
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
                notes="US fund lacks a verified SEC CIK; EDGAR cannot be checked.",
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

        # "recent" holds only the latest ~1,000 filings. Large fund trusts file far more,
        # so a 24-month backfill must also read the older pages listed in filings.files.
        forms, filing_dates, accessions = (
            list(forms),
            list(filing_dates),
            list(accessions),
        )
        primary_docs, primary_doc_descs = list(primary_docs), list(primary_doc_descs)
        oldest_recent = min((d for d in filing_dates if d), default=None)
        if oldest_recent and window_start.isoformat() < oldest_recent:
            for page in data.get("filings", {}).get("files", []) or []:
                p_from, p_to = page.get("filingFrom", ""), page.get("filingTo", "")
                if p_to and p_to < window_start.isoformat():
                    continue
                if p_from and p_from > window_end.isoformat():
                    continue
                page_rec = self.http_client.get(
                    f"https://data.sec.gov/submissions/{page.get('name')}"
                )
                if not page_rec.is_success or not page_rec.content_text:
                    logger.warning(
                        "Older EDGAR submissions page unavailable: %s", page.get("name")
                    )
                    continue
                try:
                    older = json.loads(page_rec.content_text)
                except json.JSONDecodeError:
                    continue
                forms += older.get("form", [])
                filing_dates += older.get("filingDate", [])
                accessions += older.get("accessionNumber", [])
                primary_docs += older.get("primaryDocument", [])
                primary_doc_descs += older.get("primaryDocDescription", [])

        # Check filings within requested date window
        evidences: list[Evidence] = []
        has_positive_declaration = False

        for form, f_date_str, acc, doc, desc in zip(
            forms,
            filing_dates,
            accessions,
            primary_docs,
            primary_doc_descs,
            strict=False,
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


class CanadianRegulatoryStrategy(BaseDetectionStrategy):
    """Tier 1 Authoritative strategy inspecting Canadian regulatory sources (SEDAR+ / TMX notices)."""

    def __init__(
        self,
        http_client: HTTPClient | None = None,
        universe: UniverseRegistry | None = None,
        name: str = "CanadianRegulatoryStrategy",
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
        doc_type: str,
        filing_date: date,
        fund: UniverseFund,
        record_retrieved_at: datetime,
    ) -> tuple[bool, Evidence | None]:
        """Fetch and inspect Canadian regulatory filing / TMX bulletin for genuine distribution declaration."""
        doc_record = self.http_client.get(doc_url)
        if not doc_record.is_success or not doc_record.content_text:
            return False, None

        raw_text = doc_record.content_text
        clean_text = re.sub(r"<[^>]+>", " ", raw_text)
        clean_text = re.sub(r"\s+", " ", clean_text).strip()

        # 1. Fund Identity Validation
        ticker_match = bool(
            fund.ticker
            and re.search(rf"\b{re.escape(fund.ticker)}\b", clean_text, re.IGNORECASE)
        )
        fundserv_match = bool(
            fund.fundserv_code
            and re.search(
                rf"\b{re.escape(fund.fundserv_code)}\b", clean_text, re.IGNORECASE
            )
        )
        name_match = bool(
            fund.fund_name and fund.fund_name.lower() in clean_text.lower()
        )

        if not (ticker_match or fundserv_match or name_match):
            return False, None

        # 2. Reject T3/T5 tax slip narrative and general tax guides
        is_tax_only_guide = bool(
            re.search(
                r"\b(?:T3|T5)\s+(?:tax\s+slips?|reporting\s+guide|tax\s+package)\b",
                clean_text,
                re.IGNORECASE,
            )
            and not re.search(
                r"\b(?:cash\s+distribution|dividend\s+declared|monthly\s+distribution|quarterly\s+distribution|reinvested\s+distribution|payable\s+on|per\s+unit)\b",
                clean_text,
                re.IGNORECASE,
            )
        )
        if is_tax_only_guide:
            return False, None

        # 3. Operative Declaration Evaluation
        decl_match = re.search(
            r"(?:(?:declared|announces|announced|cash\s+distribution|monthly\s+distribution|quarterly\s+distribution|dividend\s+distribution|reinvested\s+distribution|special\s+distribution|year-end\s+distribution)[\s\S]{0,140})",
            clean_text,
            re.IGNORECASE,
        ) or re.search(
            r"(?:(?:distribution|dividend)\s+rate\s+of\s+\$[0-9].*?(?:\.\s|\n|$))",
            clean_text,
            re.IGNORECASE,
        )

        if decl_match:
            snippet_text = decl_match.group(0).strip()[:200]
            # Only dates that sit next to their own label count. There is no fallback to the
            # retrieval date: a page that merely says "monthly distribution" is not a
            # declaration made today.
            found = labelled_dates(clean_text)
            if not any(found.get(k) for k in ("declaration", "ex", "published")):
                return False, None

            ev = Evidence(
                source_id="sedar_tmx_regulatory_notice",
                source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                url=doc_url,
                retrieved_at=doc_record.retrieved_at,
                snippet_or_locator=f"SEDAR/TMX Notice ({fund.ticker or fund.fundserv_code}): '{snippet_text}'",
                declaration_date_found=found.get("declaration"),
                ex_date_found=found.get("ex"),
                record_date_found=found.get("record"),
                payable_date_found=found.get("payable"),
                published_date_found=found.get("published"),
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
        if not fund or fund.country != "CA":
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                applicable=False,
                notes="Canadian regulatory sources do not apply to non-Canadian funds.",
            )

        reg_url = (
            f"https://www.tmx.com/dividends/{fund.ticker}" if fund.ticker else None
        )
        if not reg_url:
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                has_declaration_in_window=False,
                window_fully_covered=False,
                failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
                notes="Canadian regulatory feed unconfigured without active endpoint.",
            )

        record = self.http_client.get(reg_url)
        if not record.is_success or not record.content_text:
            failure_reason = record.failure_reason or UnknownReason.RETRIEVAL_FAILED
            err_evidence = Evidence(
                source_id="sedar_tmx_regulatory_notice",
                source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                url=reg_url,
                retrieved_at=record.retrieved_at,
                snippet_or_locator=f"TMX/SEDAR regulatory query failed: {record.error_message or failure_reason.value}",
                failure_reason=failure_reason,
            )
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                evidence=[err_evidence],
                has_declaration_in_window=False,
                window_fully_covered=False,
                failure_reason=failure_reason,
                notes=f"Canadian regulatory API error: {failure_reason.value}",
            )

        has_decl, ev = self._inspect_filing_content(
            doc_url=reg_url,
            doc_type="TMX_DIVIDEND_NOTICE",
            filing_date=record.retrieved_at.date(),
            fund=fund,
            record_retrieved_at=record.retrieved_at,
        )

        if has_decl and ev and _dates_in_window(ev, window_start, window_end):
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.EVIDENCE_OBSERVATION,
                evidence=[ev],
                has_declaration_in_window=True,
                window_fully_covered=True,
                suggested_route=ExtractionRoute.FILING,
                notes="Authoritative TMX/SEDAR notice found within requested declaration window.",
            )

        index_evidence = Evidence(
            source_id="sedar_tmx_regulatory_notice",
            source_tier=SourceTier.TIER_1_AUTHORITATIVE,
            url=reg_url,
            retrieved_at=record.retrieved_at,
            snippet_or_locator=f"Canadian regulatory feed inspected for {fund.ticker} in window [{window_start} to {window_end}]. Zero declared events detected.",
            failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
        )
        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.NO_DATA_OBSERVATION,
            evidence=[index_evidence],
            has_declaration_in_window=False,
            window_fully_covered=False,
            failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
            notes="TMX/SEDAR feed checked; zero positive distribution notices detected.",
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

    def _parse_html_tables(
        self,
        content: str,
        url: str,
        retrieved_at: datetime,
        window_start: date,
        window_end: date,
        fund: UniverseFund | None = None,
    ) -> list[Evidence]:
        """Evidence rows from distribution tables whose ex-date or declaration date is in the window.

        Uses the same table parser as Layer B, so a row only counts when its columns are
        labelled (ex-date / record / payable / declaration + amount). A 'NAV as of' row or any
        unlabelled date is not a distribution.
        """
        from src.parsers.html_table_parser import table_date_rows

        evidences: list[Evidence] = []
        for row, row_text in table_date_rows(
            content, fund.ticker if fund else None, fund.fundserv_code if fund else None
        ):
            ev = Evidence(
                source_id="official_fund_sponsor_page",
                source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                url=url,
                retrieved_at=retrieved_at,
                snippet_or_locator=f"Distribution table row: '{row_text[:120]}'",
                declaration_date_found=row.get("declaration"),
                ex_date_found=row.get("ex"),
                record_date_found=row.get("record"),
                payable_date_found=row.get("payable"),
            )
            if _dates_in_window(ev, window_start, window_end):
                evidences.append(ev)
        return evidences

    @staticmethod
    def _table_rows(content: str, url: str, fund: UniverseFund | None) -> list:
        from src.parsers.html_table_parser import parse_html_distribution_tables

        return parse_html_distribution_tables(
            content,
            fund.fund_id if fund else "UNKNOWN",
            fund.country if fund else "US",
            url,
            ticker=fund.ticker if fund else None,
            fundserv_code=fund.fundserv_code if fund else None,
        )

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

        if not record.is_success:
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
                notes=f"Sponsor portal retrieval failure: {record.error_message or failure_reason.value}",
            )

        is_pdf = bool(
            record.content_bytes
            and record.content_bytes.startswith(b"%PDF-")
            or url.lower().endswith(".pdf")
        )
        content = ""
        if is_pdf and record.content_bytes:
            content = _extract_text_from_pdf(record.content_bytes)
        if not content and record.content_text:
            content = record.content_text

        if not content:
            failure_reason = record.failure_reason or UnknownReason.INCOMPLETE_SOURCE
            err_evidence = Evidence(
                source_id="official_fund_sponsor_page",
                source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                url=url,
                retrieved_at=record.retrieved_at,
                snippet_or_locator=f"Failed to extract content from official portal: {record.error_message or failure_reason.value}",
                failure_reason=failure_reason,
            )
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                evidence=[err_evidence],
                has_declaration_in_window=False,
                window_fully_covered=False,
                failure_reason=failure_reason,
                notes=f"Sponsor portal content extraction failure: {failure_reason.value}",
            )

        # Rejection of T3/T5 tax reporting summaries
        is_tax_only_guide = bool(
            re.search(
                r"\b(?:T3|T5)\s+(?:tax\s+slips?|reporting\s+guide|tax\s+package)\b",
                content,
                re.IGNORECASE,
            )
            and not re.search(
                r"\b(?:cash\s+distribution|dividend\s+declared|monthly\s+distribution|quarterly\s+distribution|reinvested\s+distribution|payable\s+on|per\s+unit)\b",
                content,
                re.IGNORECASE,
            )
        )
        if is_tax_only_guide:
            tax_ev = Evidence(
                source_id="official_fund_sponsor_page",
                source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                url=url,
                retrieved_at=record.retrieved_at,
                snippet_or_locator="Identified T3/T5 tax reporting guide without operative distribution declaration.",
                failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
            )
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                evidence=[tax_ev],
                has_declaration_in_window=False,
                window_fully_covered=False,
                failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
                notes="Page is a tax reporting narrative without operative cash distribution declaration.",
            )

        evidences: list[Evidence] = []

        # 1. Structured distribution table rows (labelled columns only).
        table_evidences = self._parse_html_tables(
            content=content,
            url=url,
            retrieved_at=record.retrieved_at,
            window_start=window_start,
            window_end=window_end,
            fund=fund,
        )
        evidences.extend(table_evidences)

        # 2. Announcement / press-release text. Dates count only next to their own label
        #    (ex-date, record, payable, declaration) or as the release dateline.
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", content))
        doc_lower = text.lower()
        url_lower = url.lower()
        identity_ok = bool(
            (
                fund.ticker
                and re.search(rf"\b{re.escape(fund.ticker.lower())}\b", doc_lower)
            )
            or (
                fund.fundserv_code
                and re.search(
                    rf"\b{re.escape(fund.fundserv_code.lower())}\b", doc_lower
                )
            )
            or (fund.fund_name and fund.fund_name.lower() in doc_lower)
            # the fund's own official page (URL names the fund) - a family-wide release on it
            or (
                fund.ticker
                and re.search(
                    rf"[/\-_]{re.escape(fund.ticker.lower())}(?:[/\-_.]|$)", url_lower
                )
            )
        )
        if identity_ok:
            seen: set[tuple] = set()
            for m in re.finditer(
                r"[^.]{0,200}\b(?:declare[sd]?|announce[sd]?)\b[^.]{0,300}\b(?:distributions?|dividends?)\b[^.]{0,400}",
                text,
                re.IGNORECASE,
            ):
                start = max(0, m.start() - 200)
                snippet = text[start : m.end() + 200]
                found = labelled_dates(snippet)
                key = (
                    found.get("declaration"),
                    found.get("ex"),
                    found.get("published"),
                )
                if key in seen or not any(key):
                    continue
                seen.add(key)
                ev = Evidence(
                    source_id="official_fund_sponsor_page",
                    source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                    url=url,
                    retrieved_at=record.retrieved_at,
                    snippet_or_locator=f"Official announcement: '{m.group(0).strip()[:160]}'",
                    declaration_date_found=found.get("declaration"),
                    ex_date_found=found.get("ex"),
                    record_date_found=found.get("record"),
                    payable_date_found=found.get("payable"),
                    published_date_found=found.get("published"),
                )
                if _dates_in_window(ev, window_start, window_end):
                    evidences.append(ev)

        if evidences:
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.EVIDENCE_OBSERVATION,
                evidence=evidences,
                has_declaration_in_window=True,
                window_fully_covered=True,
                suggested_route=(
                    ExtractionRoute.PDF
                    if is_pdf
                    else (
                        ExtractionRoute.HTML_TABLE
                        if table_evidences
                        else ExtractionRoute.FILING
                    )
                ),
                notes="Official fund sponsor source confirmed a distribution in the window.",
            )

        # 3. Negative proof only when the page's own distribution table demonstrably spans the
        #    window: its earliest ex-date is on/before window_start and it is current past
        #    window_end (latest ex-date after the window, or fetched >= 7 days after it).
        from src.parsers.html_table_parser import table_date_rows

        dated_rows = table_date_rows(content, fund.ticker, fund.fundserv_code)
        ex_dates = sorted(
            d
            for row, _txt in dated_rows
            for k, d in row.items()
            if k in ("ex", "declaration")
        )
        retrieved_day = record.retrieved_at.date()
        spans_window = (
            bool(ex_dates)
            and ex_dates[0] <= window_start
            and (
                ex_dates[-1] >= window_end
                or retrieved_day >= window_end + timedelta(days=7)
            )
        )
        # A published full-year schedule ("2026 Distribution Schedule") whose rows all fall in
        # that year also covers every month of that year.
        full_year_schedule = (
            bool(ex_dates)
            and window_start.year == window_end.year
            and all(d.year == window_start.year for d in ex_dates)
            and re.search(
                rf"{window_start.year}\s+(?:distribution|dividend)\s+schedule|(?:distribution|dividend)\s+schedule\s+(?:for\s+)?{window_start.year}",
                text,
                re.IGNORECASE,
            )
            is not None
        )
        if spans_window or full_year_schedule:
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                evidence=[
                    Evidence(
                        source_id="official_fund_sponsor_page",
                        source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                        url=url,
                        retrieved_at=record.retrieved_at,
                        snippet_or_locator=(
                            f"Official distribution table lists {len(ex_dates)} dated rows from {ex_dates[0]} to "
                            f"{ex_dates[-1]}; none in [{window_start} to {window_end}]."
                        ),
                    )
                ],
                window_fully_covered=True,
                notes="Sponsor distribution table spans the window with no row inside it.",
            )

        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.NO_DATA_OBSERVATION,
            evidence=[
                Evidence(
                    source_id="official_fund_sponsor_page",
                    source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                    url=url,
                    retrieved_at=record.retrieved_at,
                    snippet_or_locator=(
                        f"Official page inspected for [{window_start} to {window_end}]: no labelled distribution "
                        "in the window and no table spanning the window (e.g. JavaScript-rendered page)."
                    ),
                    failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
                )
            ],
            failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
            notes="Sponsor page does not demonstrably cover the window.",
        )


class FilingIndexPollingStrategy(BaseDetectionStrategy):
    """Detection strategy 3 (PDF Phase 1): EDGAR daily form-index polling.

    Emits a TRIGGER_SIGNAL when the fund's CIK filed a distribution-relevant form in the
    window. The filing itself is then read by SECEdgarSubmissionsStrategy.
    """

    def __init__(
        self,
        http_client: HTTPClient | None = None,
        universe: UniverseRegistry | None = None,
        name: str = "FilingIndexPollingStrategy",
    ) -> None:
        super().__init__(name=name)
        from src.edgar_index import EdgarDailyIndexPoller

        self.poller = EdgarDailyIndexPoller(http_client=http_client)
        self.universe = universe

    def inspect(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
    ) -> StrategyObservation:
        fund = (
            self.universe.get_fund(fund_id)
            if self.universe
            else UniverseRegistry.from_json().get_fund(fund_id)
        )
        if fund is None or fund.country != "US" or not fund.cik:
            return StrategyObservation(
                self.name, SignalType.TRIGGER_SIGNAL, applicable=False
            )
        hits = self.poller.poll_range(window_start, window_end, {fund.cik}).get(
            fund.cik.lstrip("0"), []
        )
        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.TRIGGER_SIGNAL,
            notes=(
                f"{len(hits)} distribution-relevant EDGAR filings in window: "
                + ", ".join(f"{h.form}@{h.filed}" for h in hits[:10])
            ),
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
        # Strategy 4 (PDF): only run when strategies 1-3 were inconclusive.
        self.only_if_inconclusive = True
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
                applicable=False,
                notes="No targeted secondary candidate URLs configured for this fund.",
            )

        evidences: list[Evidence] = []
        has_declaration = False
        all_failures: list[UnknownReason] = []

        for target_url in urls_to_try:
            rec = self.http_client.get(target_url)
            is_pdf = bool(
                rec.content_bytes
                and rec.content_bytes.startswith(b"%PDF-")
                or target_url.lower().endswith(".pdf")
            )
            content = ""
            if is_pdf and rec.content_bytes:
                content = _extract_text_from_pdf(rec.content_bytes)
            if not content and rec.content_text:
                content = rec.content_text

            if not content:
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

            for m in re.finditer(
                r"[^.]{0,200}\b(?:declare[sd]?|announce[sd]?)\b[^.]{0,300}\b(?:distributions?|dividends?)\b[^.]{0,400}",
                clean_text,
                re.IGNORECASE,
            ):
                snippet = clean_text[max(0, m.start() - 200) : m.end() + 200]
                found = labelled_dates(snippet)
                ev = Evidence(
                    source_id="targeted_lookup_source",
                    source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                    url=target_url,
                    retrieved_at=rec.retrieved_at,
                    snippet_or_locator=f"Targeted lookup distribution match: '{m.group(0).strip()[:120]}'",
                    declaration_date_found=found.get("declaration"),
                    ex_date_found=found.get("ex"),
                    record_date_found=found.get("record"),
                    payable_date_found=found.get("payable"),
                    published_date_found=found.get("published"),
                )
                if _dates_in_window(ev, window_start, window_end):
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
                suggested_route=ExtractionRoute.FILING,
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
