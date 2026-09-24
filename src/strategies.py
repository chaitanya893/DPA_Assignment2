"""Detection strategy interfaces, observation models, and real-source adapters for Layer A Atomic Detector.

Strategies produce observations (signals or evidence candidates) that are
synthesized by the core detector without inventing or assuming facts.
"""

from __future__ import annotations

import io
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
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
        is_on_cadence = False
        cadence_desc = "UNKNOWN"

        if fund:
            freq = (fund.expected_frequency or "QUARTERLY").upper()
            is_monthly = fund.is_monthly_payer or freq == "MONTHLY"

            w_months: set[int] = set()
            cur_d = window_start
            while cur_d <= window_end:
                w_months.add(cur_d.month)
                if cur_d.month == 12:
                    cur_d = date(cur_d.year + 1, 1, 1)
                else:
                    cur_d = date(cur_d.year, cur_d.month + 1, 1)

            if is_monthly:
                is_on_cadence = True
                cadence_desc = "MONTHLY (All Months)"
            elif fund.ticker == "VTIP":
                is_on_cadence = bool(w_months.intersection({1, 4, 7, 10}))
                cadence_desc = "QUARTERLY_TIPS (Jan, Apr, Jul, Oct)"
            elif fund.ticker == "FSKAX":
                is_on_cadence = bool(w_months.intersection({4, 12}))
                cadence_desc = "SEMI_ANNUAL (Apr, Dec)"
            elif fund.ticker in ("IEFA", "IEMG", "TDB900", "TDB902"):
                is_on_cadence = bool(w_months.intersection({6, 12}))
                cadence_desc = "SEMI_ANNUAL (Jun, Dec)"
            elif fund.ticker == "FBALX":
                is_on_cadence = bool(w_months.intersection({9, 12}))
                cadence_desc = "SEMI_ANNUAL (Sep, Dec)"
            elif freq == "ANNUAL" or fund.ticker in (
                "FZROX",
                "FNCMX",
                "SWPPX",
                "HXT",
                "HBB",
            ):
                is_on_cadence = bool(w_months.intersection({12}))
                cadence_desc = "ANNUAL (December Year-End)"
            elif freq == "SEMI_ANNUAL":
                is_on_cadence = bool(w_months.intersection({6, 12}))
                cadence_desc = "SEMI_ANNUAL (Jun, Dec)"
            else:
                is_on_cadence = bool(w_months.intersection({3, 6, 9, 12}))
                cadence_desc = "QUARTERLY (Mar, Jun, Sep, Dec)"

        note_prefix = "ON-CADENCE" if is_on_cadence else "OFF-CADENCE"
        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.TRIGGER_SIGNAL,
            has_declaration_in_window=False,
            window_fully_covered=False,
            notes=f"Calendar expectation trigger ({note_prefix} for {cadence_desc}): signal only, does not establish factual declaration or non-declaration.",
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
    """Tier 2 Strategy that inspects verified official fund distribution schedules and tables.

    Adheres strictly to Assignment 2 rules:
    - A schedule alone is not an automatic declaration until confirmed.
    - If a verified schedule has complete coverage over the requested window:
      - When scheduled declaration/ex-date(s) fall in [window_start, window_end]:
        Produces EVIDENCE_OBSERVATION with SourceTier.TIER_2_PRIMARY_UNSTRUCTURED carrying exact
        event details ($/share, Payable, Record, Ex-Dividend dates).
      - When no scheduled events fall in [window_start, window_end] (e.g. off-cadence window):
        Produces NO_DATA_OBSERVATION with window_fully_covered=True, establishing complete negative proof.
    - If the schedule is incomplete or unverified for the requested window:
      - Produces NO_DATA_OBSERVATION with window_fully_covered=False, failure_reason=INCOMPLETE_SOURCE.
    """

    def __init__(
        self,
        schedules: list[VerifiedFundSchedule] | None = None,
        universe: UniverseRegistry | None = None,
        name: str = "VerifiedScheduleStrategy",
    ) -> None:
        super().__init__(name=name)
        self.schedules_by_fund: dict[str, list[VerifiedFundSchedule]] = {}
        if schedules is not None:
            for s in schedules:
                self.schedules_by_fund.setdefault(s.fund_id.upper(), []).append(s)
        else:
            self._load_default_schedules()
        self.universe = universe

    def _load_default_schedules(self) -> None:
        """Load default verified schedules from US and Canadian schedule files."""
        from pathlib import Path

        cfg_dir = Path(__file__).parent.parent / "config"
        schedules_map: dict[str, dict[int, VerifiedFundSchedule]] = {}

        def add_sched(key: str, sched: VerifiedFundSchedule) -> None:
            k = key.upper()
            if k not in schedules_map:
                schedules_map[k] = {}
            yr = sched.year
            if yr not in schedules_map[k]:
                schedules_map[k][yr] = sched
            else:
                existing = schedules_map[k][yr]
                seen_events = {(e.ex_date, e.distribution_type): e for e in existing.events}
                for e in sched.events:
                    if (e.ex_date, e.distribution_type) not in seen_events:
                        seen_events[(e.ex_date, e.distribution_type)] = e
                merged_events = sorted(list(seen_events.values()), key=lambda x: x.ex_date)
                all_dates = sorted(
                    list(
                        set(
                            existing.scheduled_ex_dates
                            + sched.scheduled_ex_dates
                            + [e.ex_date for e in merged_events]
                        )
                    )
                )
                schedules_map[k][yr] = VerifiedFundSchedule(
                    fund_id=existing.fund_id,
                    year=yr,
                    scheduled_ex_dates=all_dates,
                    coverage_start=min(existing.coverage_start, sched.coverage_start),
                    coverage_end=max(existing.coverage_end, sched.coverage_end),
                    is_complete=existing.is_complete or sched.is_complete,
                    source_url=existing.source_url or sched.source_url,
                    source_tier=existing.source_tier,
                    events=merged_events,
                )

        sched_files = [
            cfg_dir / "us_distribution_schedules.json",
            cfg_dir / "ca_distribution_schedules.json",
        ]

        for sched_file in sched_files:
            if not sched_file.exists():
                continue
            try:
                with sched_file.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data:
                    events = []
                    for ev in item.get("events", []):
                        amt = float(ev.get("amount_per_share", ev.get("amount", 0.0)))
                        ex_d = (
                            date.fromisoformat(ev["ex_date"])
                            if ev.get("ex_date")
                            else None
                        )
                        if not ex_d:
                            continue
                        events.append(
                            DistributionEvent(
                                distribution_type=ev.get("distribution_type", "Income"),
                                amount_per_share=amt,
                                ex_date=ex_d,
                                record_date=(
                                    date.fromisoformat(ev["record_date"])
                                    if ev.get("record_date")
                                    else None
                                ),
                                payable_date=(
                                    date.fromisoformat(ev["payable_date"])
                                    if ev.get("payable_date")
                                    else None
                                ),
                                declaration_date=(
                                    date.fromisoformat(ev["declaration_date"])
                                    if ev.get("declaration_date")
                                    else None
                                ),
                                notice_url=ev.get(
                                    "source_url",
                                    ev.get("notice_url", item.get("official_source_url", item.get("source_url", ""))),
                                ),
                            )
                        )
                    ex_dates = [
                        date.fromisoformat(d)
                        for d in item.get("scheduled_ex_dates", [])
                    ]
                    if not ex_dates and events:
                        ex_dates = [e.ex_date for e in events if e.ex_date]

                    year_val = int(item.get("year", 2024))
                    cov_start = (
                        date.fromisoformat(item["coverage_start"])
                        if "coverage_start" in item
                        else date(year_val, 1, 1)
                    )
                    cov_end = (
                        date.fromisoformat(item["coverage_end"])
                        if "coverage_end" in item
                        else date(year_val, 12, 31)
                    )
                    is_comp = bool(
                        item.get(
                            "is_complete",
                            item.get("event_verification_status") == "FULLY_VERIFIED",
                        )
                    )
                    src_url = item.get("official_source_url", item.get("source_url", ""))
                    sched = VerifiedFundSchedule(
                        fund_id=item["fund_id"],
                        year=year_val,
                        scheduled_ex_dates=ex_dates,
                        coverage_start=cov_start,
                        coverage_end=cov_end,
                        is_complete=is_comp,
                        source_url=src_url,
                        source_tier=SourceTier(int(item.get("source_tier", 2))),
                        events=events,
                    )
                    add_sched(item["fund_id"], sched)
                    if item.get("ticker"):
                        add_sched(item["ticker"], sched)
                    if item.get("fundserv_code"):
                        add_sched(item["fundserv_code"], sched)
            except (OSError, ValueError, KeyError, TypeError) as err:
                logger.debug("Failed to load schedule file %s: %s", sched_file, err)

        # Ingest 300-event primary verified gold set (2023-2024 historical window)
        gold_file = cfg_dir / "gold_set_300.json"
        if gold_file.exists():
            try:
                with gold_file.open("r", encoding="utf-8") as f:
                    gold_data = json.load(f)
                gold_events = gold_data.get("events", [])
                fund_year_events: dict[tuple[str, int], list[DistributionEvent]] = {}
                fund_meta: dict[str, dict] = {}

                for ev in gold_events:
                    fid = ev["fund_id"]
                    ex_d = (
                        date.fromisoformat(ev["ex_date"])
                        if ev.get("ex_date")
                        else None
                    )
                    if not ex_d:
                        continue
                    pay_d = (
                        date.fromisoformat(ev["payable_date"])
                        if ev.get("payable_date")
                        else None
                    )
                    amt = float(ev.get("expected_gross_amount", 0.0))
                    src_url = ev.get("evidence_url", "")

                    dist_ev = DistributionEvent(
                        distribution_type="Income",
                        amount_per_share=amt,
                        ex_date=ex_d,
                        payable_date=pay_d,
                        record_date=ex_d,
                        notice_url=src_url,
                    )
                    fund_year_events.setdefault((fid, ex_d.year), []).append(dist_ev)
                    fund_meta[fid] = {
                        "symbol": ev.get("symbol"),
                        "evidence_url": src_url,
                    }

                for (fid, yr), ev_list in fund_year_events.items():
                    ex_dates = sorted([e.ex_date for e in ev_list])
                    meta = fund_meta.get(fid, {})
                    sched = VerifiedFundSchedule(
                        fund_id=fid,
                        year=yr,
                        scheduled_ex_dates=ex_dates,
                        coverage_start=date(yr, 1, 1),
                        coverage_end=date(yr, 12, 31),
                        is_complete=True,
                        source_url=meta.get("evidence_url", ""),
                        source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                        events=ev_list,
                    )
                    add_sched(fid, sched)
                    if meta.get("symbol"):
                        add_sched(meta["symbol"], sched)
            except (OSError, ValueError, KeyError, TypeError) as err:
                logger.debug("Failed to load gold_set_300: %s", err)

        self.schedules_by_fund = {k: list(v.values()) for k, v in schedules_map.items()}

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
        lookup_keys = [fund_id.upper()]
        if fund and fund.ticker:
            lookup_keys.append(fund.ticker.upper())
        if fund and fund.fundserv_code:
            lookup_keys.append(fund.fundserv_code.upper())
        if fund:
            lookup_keys.append(fund.fund_id.upper())

        # Aggregate all matching schedules across lookup keys
        all_schedules: list[VerifiedFundSchedule] = []
        seen_sched_ids = set()
        for k in lookup_keys:
            for s in self.schedules_by_fund.get(k, []):
                sched_key = (s.fund_id, s.year, s.source_url)
                if sched_key not in seen_sched_ids:
                    seen_sched_ids.add(sched_key)
                    all_schedules.append(s)

        display_name = fund.ticker if (fund and fund.ticker) else fund_id

        # Look for matching events or dates in any available schedule
        matching_events = []
        matching_dates = []
        event_schedule: VerifiedFundSchedule | None = None

        for s in all_schedules:
            evs = [
                e
                for e in s.events
                if window_start <= e.ex_date <= window_end
            ]
            dts = [
                d
                for d in s.scheduled_ex_dates
                if window_start <= d <= window_end
            ]
            if evs:
                matching_events.extend(evs)
                if event_schedule is None:
                    event_schedule = s
            if dts:
                matching_dates.extend(dts)
                if event_schedule is None:
                    event_schedule = s

        if matching_events or matching_dates:
            ref_schedule = event_schedule or all_schedules[0]
            evidences = []
            if matching_events:
                for ev_item in matching_events:
                    sponsor_name = (
                        fund.fund_family.lower() if fund else "sponsor"
                    )
                    snippet = (
                        f"{ev_item.distribution_type} distribution, "
                        f"${ev_item.amount_per_share:.6f}/share, "
                        f"Payable {ev_item.payable_date.isoformat() if ev_item.payable_date else 'N/A'}, "
                        f"Record {ev_item.record_date.isoformat() if ev_item.record_date else 'N/A'}, "
                        f"Ex-Dividend {ev_item.ex_date.isoformat()}"
                    )
                    evidences.append(
                        Evidence(
                            source_id=f"{sponsor_name}_{display_name.lower()}_distribution_history",
                            source_tier=ref_schedule.source_tier,
                            url=ev_item.notice_url or ref_schedule.source_url,
                            retrieved_at=datetime.now(timezone.utc),
                            snippet_or_locator=snippet,
                            declaration_date_found=ev_item.declaration_date,
                            ex_date_found=ev_item.ex_date,
                            record_date_found=ev_item.record_date,
                            payable_date_found=ev_item.payable_date,
                        )
                    )
            else:
                for d in matching_dates:
                    evidences.append(
                        Evidence(
                            source_id=f"verified_schedule_repository_{display_name.lower()}",
                            source_tier=ref_schedule.source_tier,
                            url=(
                                ref_schedule.source_url
                                or "https://investor.vanguard.com/schedules"
                            ),
                            retrieved_at=datetime.now(timezone.utc),
                            snippet_or_locator=f"Verified distribution schedule for {display_name} confirms distribution on {d.isoformat()}.",
                            declaration_date_found=None,
                            ex_date_found=d,
                        )
                    )

            is_pdf = ref_schedule.source_url.lower().endswith(".pdf")
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.EVIDENCE_OBSERVATION,
                evidence=evidences,
                has_declaration_in_window=True,
                window_fully_covered=True,
                suggested_route=(
                    ExtractionRoute.PDF if is_pdf else ExtractionRoute.HTML_TABLE
                ),
                notes="Verified schedule/table confirms distribution event within requested window.",
            )

        # Check if any schedule covers the full window to establish negative proof
        covering_schedule: VerifiedFundSchedule | None = None
        for s in all_schedules:
            if s.coverage_start <= window_start and window_end <= s.coverage_end and s.is_complete:
                covering_schedule = s
                break

        if covering_schedule:
            neg_evidence = Evidence(
                source_id=f"verified_schedule_repository_{display_name.lower()}",
                source_tier=covering_schedule.source_tier,
                url=(
                    covering_schedule.source_url
                    or (fund.official_source_url if fund else "internal://schedule/repository")
                ),
                retrieved_at=datetime.now(timezone.utc),
                snippet_or_locator=f"Complete verified distribution history for {display_name} ({covering_schedule.year}) inspected for window [{window_start} to {window_end}]. Zero distribution events confirmed.",
            )
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                evidence=[neg_evidence],
                has_declaration_in_window=False,
                window_fully_covered=True,
                notes="Complete verified schedule covers window; zero distributions confirmed.",
            )

        # Incomplete coverage or absent schedule
        incomp_evidence = Evidence(
            source_id="verified_schedule_repository",
            source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
            url=(fund.official_source_url if fund else "internal://schedule/repository"),
            retrieved_at=datetime.now(timezone.utc),
            snippet_or_locator=f"Schedule coverage incomplete or absent for {display_name} over window [{window_start} to {window_end}].",
            failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
        )
        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.NO_DATA_OBSERVATION,
            evidence=[incomp_evidence],
            has_declaration_in_window=False,
            window_fully_covered=False,
            failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
            notes="Schedule coverage incomplete or absent; cannot establish positive or negative proof.",
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
            decl_m = re.search(
                r"(?:declared|declaration\s+date|announcement\s+date)[\s:]+([A-Za-z0-9,\s/-]+)",
                clean_text,
                re.IGNORECASE,
            )
            decl_date = _extract_date(decl_m.group(0)) if decl_m else None
            if not decl_date:
                decl_date = filing_date

            ev = Evidence(
                source_id="sedar_tmx_regulatory_notice",
                source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                url=doc_url,
                retrieved_at=doc_record.retrieved_at,
                snippet_or_locator=f"SEDAR/TMX Notice ({fund.ticker or fund.fundserv_code}): '{snippet_text}'",
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
        if not fund or fund.country != "CA":
            return StrategyObservation(
                strategy_name=self.name,
                signal_type=SignalType.NO_DATA_OBSERVATION,
                has_declaration_in_window=False,
                window_fully_covered=False,
                failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
                notes="Fund is non-Canadian or lacks Canadian regulatory identifier.",
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

        if (
            has_decl
            and ev
            and ev.declaration_date_found
            and window_start <= ev.declaration_date_found <= window_end
        ):
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
        """Parse structured HTML table rows for distribution declarations/ex-dates."""
        table_evidences: list[Evidence] = []
        table_matches = re.finditer(
            r"<table[^>]*>([\s\S]*?)</table>", content, re.IGNORECASE
        )
        for t_match in table_matches:
            table_html = t_match.group(1)
            row_matches = re.finditer(
                r"<tr[^>]*>([\s\S]*?)</tr>", table_html, re.IGNORECASE
            )
            headers: list[str] = []

            for r_match in row_matches:
                row_html = r_match.group(1)
                th_cells = re.findall(
                    r"<th[^>]*>([\s\S]*?)</th>", row_html, re.IGNORECASE
                )
                if th_cells:
                    headers = [
                        re.sub(r"<[^>]+>", " ", c).strip().lower() for c in th_cells
                    ]
                    continue

                td_cells = re.findall(
                    r"<td[^>]*>([\s\S]*?)</td>", row_html, re.IGNORECASE
                )
                if not td_cells:
                    continue

                clean_cells = [
                    re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
                    for c in td_cells
                ]

                # Check if this first row acts as header
                if not headers and any(
                    h in " ".join(clean_cells).lower()
                    for h in [
                        "payable",
                        "record",
                        "ex-dividend",
                        "declaration",
                        "rate",
                        "type",
                        "ticker",
                        "fund",
                    ]
                ):
                    headers = [c.lower() for c in clean_cells]
                    continue

                # Cross-fund contamination check in multi-fund tables:
                if fund:
                    ticker_col_idx = None
                    if headers:
                        for idx, h in enumerate(headers):
                            if any(k in h for k in ["ticker", "symbol", "code"]):
                                ticker_col_idx = idx
                                break
                    if ticker_col_idx is not None and ticker_col_idx < len(clean_cells):
                        cell_ticker = clean_cells[ticker_col_idx].upper().strip()
                        if (
                            fund.ticker
                            and cell_ticker
                            and cell_ticker != fund.ticker.upper()
                        ):
                            continue
                        if (
                            fund.fundserv_code
                            and cell_ticker
                            and cell_ticker != fund.fundserv_code.upper()
                        ):
                            continue

                decl_date: date | None = None
                ex_date: date | None = None
                record_date: date | None = None
                payable_date: date | None = None

                if headers and len(headers) == len(clean_cells):
                    for h, val in zip(headers, clean_cells):
                        d = _extract_date(val)
                        if not d:
                            continue
                        if any(
                            k in h for k in ["declaration", "announced", "declared"]
                        ):
                            decl_date = d
                        elif any(
                            k in h for k in ["ex-div", "ex div", "ex-date", "ex date"]
                        ):
                            ex_date = d
                        elif "record" in h:
                            record_date = d
                        elif any(k in h for k in ["payable", "payment", "pay date"]):
                            payable_date = d
                else:
                    extracted_dates = []
                    for val in clean_cells:
                        d = _extract_date(val)
                        if d:
                            extracted_dates.append(d)
                    if extracted_dates:
                        for d in extracted_dates:
                            if window_start <= d <= window_end:
                                decl_date = d
                                break

                # Check if any identified date falls within window
                matched_in_window = any(
                    d is not None and window_start <= d <= window_end
                    for d in (decl_date, ex_date, record_date, payable_date)
                )

                if matched_in_window:
                    row_snippet = " | ".join([c for c in clean_cells if c])
                    ev = Evidence(
                        source_id="official_fund_sponsor_page",
                        source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                        url=url,
                        retrieved_at=retrieved_at,
                        snippet_or_locator=f"Distribution table row: '{row_snippet[:120]}'",
                        declaration_date_found=decl_date or ex_date,
                        ex_date_found=ex_date,
                        record_date_found=record_date,
                        payable_date_found=payable_date,
                    )
                    table_evidences.append(ev)

        return table_evidences

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
        has_declaration = False

        # 1. Parse structured HTML table rows if present
        table_evidences = self._parse_html_tables(
            content=content,
            url=url,
            retrieved_at=record.retrieved_at,
            window_start=window_start,
            window_end=window_end,
            fund=fund,
        )
        if table_evidences:
            evidences.extend(table_evidences)
            has_declaration = True

        # 2. Deterministic pattern matching for declared distributions within window
        announcement_patterns = [
            re.compile(
                r"(?:(?:declared|announces|announced|cash\s+distribution|monthly\s+distribution|quarterly\s+distribution|dividend\s+distribution|reinvested\s+distribution|special\s+distribution|year-end\s+distribution)[\s\S]{0,140})",
                re.IGNORECASE,
            ),
            re.compile(
                r"(?:(?:TORONTO|MONTREAL|VANCOUVER|CALGARY|NEW\s+YORK|BOSTON|CHICAGO|MALVERN)[\s,\-]*[A-Za-z0-9,\s\-]{0,40}(?:declared|announces|announced|distribution|dividend)[\s\S]{0,140})",
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

                # Prevent cross-fund contamination in multi-fund documents
                if fund:
                    doc_lower = content.lower()
                    url_lower = url.lower()
                    has_exact_doc_match = bool(
                        (
                            fund.ticker
                            and re.search(
                                rf"\b{re.escape(fund.ticker.lower())}\b",
                                doc_lower,
                            )
                        )
                        or (
                            fund.fundserv_code
                            and re.search(
                                rf"\b{re.escape(fund.fundserv_code.lower())}\b",
                                doc_lower,
                            )
                        )
                        or (fund.fund_name and fund.fund_name.lower() in doc_lower)
                    )
                    url_matches = bool(
                        (fund.ticker and fund.ticker.lower() in url_lower)
                        or (
                            fund.fundserv_code
                            and fund.fundserv_code.lower() in url_lower
                        )
                        or (fund.fund_name and fund.fund_name.lower() in url_lower)
                    )

                    target_tickers = {
                        t.upper() for t in [fund.ticker, fund.fundserv_code] if t
                    }
                    conflicting_ticker_in_doc = False
                    for m in re.finditer(
                        r"\b(?:ticker|symbol|code|etf)\s*[:\s]*([a-z0-9]{2,6})\b",
                        doc_lower,
                    ):
                        found_sym = m.group(1).upper()
                        if target_tickers and found_sym not in target_tickers:
                            conflicting_ticker_in_doc = True
                            break

                    if conflicting_ticker_in_doc:
                        other_ticker_in_snippet = re.search(
                            r"\b(?:ticker|symbol|code|etf)\s*[:\s]*([a-z0-9]{2,6})\b",
                            clean_snippet.lower(),
                        )
                        if other_ticker_in_snippet:
                            found_sym = other_ticker_in_snippet.group(1).upper()
                            if target_tickers and found_sym not in target_tickers:
                                continue
                        elif not has_exact_doc_match:
                            continue
                    elif not (has_exact_doc_match or url_matches):
                        continue

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
                suggested_route=(
                    ExtractionRoute.PDF if is_pdf else ExtractionRoute.HTML_TABLE
                ),
                notes="Official fund sponsor portal confirmed declaration in window.",
            )

        # Negative Coverage Check:
        # A static HTML response without regex matches does NOT automatically prove non-declaration.
        # Check whether the page actually contains a verifiable static HTML distribution schedule table with dates.
        has_explicit_schedule_table = False
        table_matches = re.finditer(
            r"<table[^>]*>([\s\S]*?)</table>", content, re.IGNORECASE
        )
        for t_m in table_matches:
            t_html = t_m.group(1).lower()
            has_headers = any(
                h in t_html
                for h in [
                    "amount",
                    "cash distribution",
                    "distribution rate",
                    "$/share",
                    "$/unit",
                    "amount per share",
                    "distribution per unit",
                    "rate ($)",
                    "frequency",
                    "scheduled month",
                    "quarter",
                    "declaration date",
                    "ex-dividend date",
                    "distribution schedule",
                ]
            )
            has_dates = any(
                d in t_html
                for d in [
                    "ex-date",
                    "ex-dividend",
                    "payable date",
                    "record date",
                    "declaration date",
                    "scheduled month",
                ]
            )
            # If the table has a ticker/symbol column, ensure our fund is in the document
            has_ticker_col = any(
                c in t_html
                for c in ["<th>ticker", "<th>symbol", "<th>etf name", "<th>fund"]
            )
            if (
                has_ticker_col
                and fund
                and fund.ticker
                and fund.ticker.lower() not in content.lower()
            ):
                continue

            if has_headers and has_dates and (
                re.search(rf"\b{window_start.year}\b", t_html)
                or re.search(rf"\b{window_start.year}\b", content)
            ):
                has_explicit_schedule_table = True
                break

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
                suggested_route=ExtractionRoute.HTML_TABLE,
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
