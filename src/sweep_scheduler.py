"""Backfill and gap logic (PDF Assignment 2, Phase 1 - "the part the brief specifically asks for").

Per fund the database keeps ``expected_frequency``, ``last_confirmed_event_date``,
``last_checked_at`` and ``consecutive_unknowns`` (table ``fund_detection_state``).

A lookback sweep over a rolling window of N months is triggered when:
  1. the time since the last confirmed event exceeds 1.5x the expected interval;
  2. the last runs returned UNKNOWN more than twice in a row;
  3. a new fund enters the universe (backfill 24 months);
  4. it is month-end or the year-end period, unconditionally (December is when the capital
     gain volume lands).
Otherwise the fund gets a cheap routine check of the days since it was last checked.

Sweeps are cut into calendar-month windows because Layer A answers yes/no per window: one
window per month finds every monthly distribution separately.

N (``lookback_months``) and every threshold are configurable in ``config/sweep_config.yaml``.
Default N = 3: it covers one full quarterly cycle for the quarterly payers that make up most
of the universe and gives sources that publish late or restate (year-end reallocations,
estimated -> final capital gains) three chances to be picked up, while costing only three
checks per fund. See docs/FINAL_MEMO.md.

Usage:
    python -m src.sweep_scheduler --dry-run                 # show what would run today
    python -m src.sweep_scheduler                           # run due sweeps for all funds
    python -m src.sweep_scheduler --backfill-all            # 24-month backfill for every fund
    python -m src.sweep_scheduler --fund-id US_VANGUARD_BND --as-of 2026-09-24
"""

from __future__ import annotations

import argparse
import calendar
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from src.database.models import FundDetectionState
from src.database.repository import DistributionRepository
from src.logging_setup import configure_logging
from src.universe_loader import UniverseFund, UniverseRegistry

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sweep_config.yaml"

INTERVAL_DAYS = {
    "DAILY": 30,
    "MONTHLY": 30,
    "QUARTERLY": 91,
    "SEMI_ANNUAL": 182,
    "ANNUAL": 365,
}


@dataclass
class SweepConfig:
    lookback_months: int = 3
    backfill_months: int = 24
    gap_multiplier: float = 1.5
    unknown_threshold: int = 2
    month_end_days: int = 3
    year_end_start: tuple[int, int] = (12, 1)
    year_end_end: tuple[int, int] = (1, 15)
    routine_max_days: int = 31

    @classmethod
    def load(cls, path: Path | None = None) -> SweepConfig:
        p = path or CONFIG_PATH
        if not p.exists():
            return cls()
        data: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        cfg = cls()
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, tuple(v) if isinstance(getattr(cfg, k), tuple) else v)
        if cfg.lookback_months < 1 or cfg.backfill_months < 1:
            raise ValueError("lookback_months and backfill_months must be >= 1")
        return cfg


@dataclass
class SweepDecision:
    fund_id: str
    mode: str  # BACKFILL | LOOKBACK | ROUTINE
    window_start: date
    window_end: date
    reasons: list[str] = field(default_factory=list)

    def month_windows(self) -> list[tuple[date, date]]:
        return month_windows(self.window_start, self.window_end)


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def month_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Split [start, end] into calendar-month windows (first and last clipped)."""
    out: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        last = date(cur.year, cur.month, calendar.monthrange(cur.year, cur.month)[1])
        out.append((cur, min(last, end)))
        cur = last + timedelta(days=1)
    return out


def is_month_end(day: date, days: int) -> bool:
    return calendar.monthrange(day.year, day.month)[1] - day.day < days


def is_year_end(day: date, cfg: SweepConfig) -> bool:
    start = date(day.year, *cfg.year_end_start)
    end_same_year = date(day.year, *cfg.year_end_end)
    if cfg.year_end_start > cfg.year_end_end:  # period wraps into January
        return day >= start or day <= end_same_year
    return start <= day <= end_same_year


def expected_interval_days(frequency: str | None) -> int:
    return INTERVAL_DAYS.get((frequency or "QUARTERLY").upper(), 91)


def decide(
    fund: UniverseFund,
    state: FundDetectionState | None,
    today: date,
    cfg: SweepConfig,
) -> SweepDecision:
    """Decide what to check for one fund today."""
    freq = (
        fund.expected_frequency or ("MONTHLY" if fund.is_monthly_payer else "QUARTERLY")
    ).upper()

    if state is None or state.backfill_completed_at is None:
        return SweepDecision(
            fund.fund_id,
            "BACKFILL",
            add_months(today, -cfg.backfill_months),
            today,
            [f"new fund in universe: backfill {cfg.backfill_months} months"],
        )

    reasons: list[str] = []
    interval = expected_interval_days(freq)
    reference = state.last_confirmed_event_date
    if reference is None:
        reasons.append("no confirmed event on record")
    else:
        elapsed = (today - reference).days
        if elapsed > cfg.gap_multiplier * interval:
            reasons.append(
                f"{elapsed} days since last confirmed event > {cfg.gap_multiplier} x {interval}-day {freq} interval"
            )
    if state.consecutive_unknowns > cfg.unknown_threshold:
        reasons.append(
            f"UNKNOWN {state.consecutive_unknowns} times in a row (> {cfg.unknown_threshold})"
        )
    if is_month_end(today, cfg.month_end_days):
        reasons.append("month-end sweep")
    if is_year_end(today, cfg):
        reasons.append("year-end sweep (capital gain season)")

    if reasons:
        return SweepDecision(
            fund.fund_id,
            "LOOKBACK",
            add_months(today, -cfg.lookback_months),
            today,
            reasons,
        )

    last = (
        state.last_checked_at.date()
        if state.last_checked_at
        else today - timedelta(days=7)
    )
    start = max(last, today - timedelta(days=cfg.routine_max_days))
    return SweepDecision(
        fund.fund_id, "ROUTINE", start, today, ["routine check since last run"]
    )


class SweepScheduler:
    """Applies ``decide`` to every fund and runs the resulting checks through the pipeline."""

    def __init__(
        self,
        pipeline: Any,
        universe: UniverseRegistry | None = None,
        config: SweepConfig | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.universe = universe or pipeline.universe
        self.config = config or SweepConfig.load()

    def plan(
        self, today: date, fund_ids: list[str] | None = None, backfill_all: bool = False
    ) -> list[SweepDecision]:
        from src.database.connection import session_scope

        funds = (
            [self.universe.get_fund(f) for f in fund_ids]
            if fund_ids
            else self.universe.list_funds()
        )
        decisions: list[SweepDecision] = []
        with session_scope(self.pipeline.engine) as session:
            repo = DistributionRepository(session)
            for fund in funds:
                if fund is None:
                    continue
                state = None if backfill_all else repo.get_detection_state(fund.fund_id)
                decisions.append(decide(fund, state, today, self.config))
        return decisions

    def run(
        self,
        today: date,
        fund_ids: list[str] | None = None,
        backfill_all: bool = False,
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        from src.database.connection import session_scope

        self.pipeline.ensure_reference_data()
        results: list[dict[str, Any]] = []
        for decision in self.plan(today, fund_ids, backfill_all):
            windows = decision.month_windows()
            logger.info(
                "Sweep %s %s %s..%s (%d windows): %s",
                decision.fund_id,
                decision.mode,
                decision.window_start,
                decision.window_end,
                len(windows),
                "; ".join(decision.reasons),
            )
            if decision.mode == "ROUTINE" and not dry_run:
                needed, why = self._routine_needed(decision)
                self.pipeline.flush_http(decision.fund_id)
                if not needed:
                    logger.info("Skip %s: %s", decision.fund_id, why)
                    results.append(
                        {
                            "fund_id": decision.fund_id,
                            "status": "SKIPPED",
                            "reason": why,
                        }
                    )
                    continue
                decision.reasons.append(why)
            if dry_run:
                results.append(
                    {
                        "fund_id": decision.fund_id,
                        "mode": decision.mode,
                        "windows": len(windows),
                        "start": decision.window_start.isoformat(),
                        "end": decision.window_end.isoformat(),
                        "reasons": decision.reasons,
                    }
                )
                continue
            for ws, we in windows:
                s = self.pipeline.check(
                    decision.fund_id,
                    ws,
                    we,
                    sweep_reason=f"{decision.mode}: {'; '.join(decision.reasons)}",
                )
                results.append(s.__dict__)
            if decision.mode == "BACKFILL":
                with session_scope(self.pipeline.engine) as session:
                    DistributionRepository(session).upsert_detection_state(
                        decision.fund_id,
                        backfill_completed_at=datetime.now(timezone.utc),
                    )
        return results

    def _previous_hash(self, url: str) -> str | None:
        from sqlalchemy import select

        from src.database.connection import session_scope
        from src.database.models import CrawlLog

        with session_scope(self.pipeline.engine) as session:
            row = session.scalars(
                select(CrawlLog)
                .where(CrawlLog.target_url == url, CrawlLog.content_sha256.is_not(None))
                .order_by(CrawlLog.retrieved_at.desc())
            ).first()
            return row.content_sha256 if row else None

    def _routine_needed(self, decision: SweepDecision) -> tuple[bool, str]:
        """Cheapest-first gate for routine days (PDF detection strategies 1-3).

        1. calendar: is a payment expected in the window?
        2. change detection: did the fund's distribution page hash change?
        3. filing index: did the fund's CIK file a distribution-relevant form on EDGAR?
        Only when all three are quiet is the full check skipped.
        """
        from src.edgar_index import EdgarDailyIndexPoller
        from src.strategies import CalendarExpectationStrategy, ChangeDetectionStrategy

        fund = self.universe.get_fund(decision.fund_id)
        if fund is None:
            return False, "fund not in universe"
        on_cadence, desc = CalendarExpectationStrategy.expected_in_window(
            fund, decision.window_start, decision.window_end
        )
        if on_cadence:
            return True, f"calendar: on cadence ({desc})"
        changed, _sha = ChangeDetectionStrategy(
            http_client=self.pipeline.http,
            universe=self.universe,
            previous_hash_lookup=self._previous_hash,
        ).check(fund)
        if changed:
            return True, "change detection: distribution page hash changed"
        if fund.country == "US" and fund.cik:
            hits = EdgarDailyIndexPoller(self.pipeline.http).poll_range(
                decision.window_start, decision.window_end, {fund.cik}
            )
            if hits:
                return True, "EDGAR daily index: new distribution-relevant filing"
        return False, f"off cadence ({desc}), page unchanged, no new filing"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill / gap-driven sweep scheduler (Layer A -> B -> DB)"
    )
    parser.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="Reference date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--fund-id",
        action="append",
        default=None,
        help="Limit to these fund IDs (repeatable)",
    )
    parser.add_argument(
        "--backfill-all",
        action="store_true",
        help="Force a full backfill for the selected funds",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the plan without any network access",
    )
    parser.add_argument("--db-url", type=str, default=None)
    args = parser.parse_args()

    configure_logging()
    from src.pipeline import DistributionPipeline

    today = (
        date.fromisoformat(args.as_of)
        if args.as_of
        else datetime.now(timezone.utc).date()
    )
    pipeline = DistributionPipeline(db_url=args.db_url)
    results = SweepScheduler(pipeline).run(
        today, args.fund_id, args.backfill_all, args.dry_run
    )
    if args.dry_run:
        for r in results:
            print(
                f"{r['fund_id']:<22} {r['mode']:<9} {r['start']}..{r['end']} ({r['windows']} windows) - {'; '.join(r['reasons'])}"
            )
    else:
        by_status: dict[str, int] = {}
        for r in results:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        print(f"Checks run: {len(results)}  by status: {by_status}")
        print("Next: python -m src.reports.detection_report")


if __name__ == "__main__":
    main()
