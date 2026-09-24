"""Data Quality Engine for Fund Distributions.

Orchestrates validation rules against distribution events, logs failures to the
`dq_flag` database table, and calculates comprehensive quality metrics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select

from src.database.connection import get_engine, session_scope
from src.database.models import (
    DistributionComponent,
    DistributionEvent,
    EventEvidence,
    FundMaster,
    ShareClass,
)
from src.database.repository import DistributionRepository
from src.validators.rules import (
    BaseValidationRule,
    ComponentSumRule,
    CrossSourceVarianceRule,
    CurrencyIntegrityRule,
    DateOrderRule,
    DQSeverity,
    FrequencyContinuityRule,
    MagnitudeRule,
    NavDeclineRule,
    ValidationResult,
)

logger = logging.getLogger(__name__)


@dataclass
class DQReport:
    """Consolidated Data Quality Audit Report.

    ``pass_rate_pct`` is computed over checks that actually ran. Checks that could not run
    (missing NAV data, single source, no published components) are counted in
    ``skipped_checks`` and never reported as passes.
    """

    total_events_evaluated: int = 0
    total_checks_performed: int = 0
    skipped_checks: int = 0
    total_flags_raised: int = 0
    critical_flags: int = 0
    warning_flags: int = 0
    info_flags: int = 0
    pass_rate_pct: float | None = None
    rule_metrics: dict[str, dict[str, int]] = field(default_factory=dict)
    flagged_events: list[dict[str, Any]] = field(default_factory=list)


EVENT_RULES = (
    ComponentSumRule,
    DateOrderRule,
    NavDeclineRule,
    MagnitudeRule,
    CurrencyIntegrityRule,
    CrossSourceVarianceRule,
)


class DataQualityEngine:
    """Executes deterministic validation checks on extracted events and database records."""

    def __init__(self, rules: list[BaseValidationRule] | None = None) -> None:
        self.rules: list[BaseValidationRule] = rules or [cls() for cls in EVENT_RULES]
        self.fund_rules: list[BaseValidationRule] = [FrequencyContinuityRule()]

    def validate_event(
        self,
        event: Any,
        context: dict[str, Any] | None = None,
    ) -> list[ValidationResult]:
        """Run all per-event rules against one event (ORM row or ExtractedDistribution)."""
        results: list[ValidationResult] = []
        for rule in self.rules:
            try:
                results.append(rule.validate(event, context=context))
            except (
                Exception
            ) as err:  # noqa: BLE001 - a broken rule must not crash the run
                logger.error("Rule %s execution error: %s", rule.rule_name, err)
                results.append(
                    ValidationResult(
                        is_valid=False,
                        rule_name=rule.rule_name,
                        severity=DQSeverity.CRITICAL,
                        message=f"Rule internal execution error: {err}",
                    )
                )
        return results

    @staticmethod
    def blocking_failures(results: list[ValidationResult]) -> list[ValidationResult]:
        """CRITICAL failures: the figure goes to review, not to the database."""
        return [
            r for r in results if not r.is_valid and r.severity == DQSeverity.CRITICAL
        ]

    @staticmethod
    def warnings(results: list[ValidationResult]) -> list[ValidationResult]:
        """WARNING failures: stored, but flagged for review (e.g. > 20% of NAV)."""
        return [
            r for r in results if not r.is_valid and r.severity != DQSeverity.CRITICAL
        ]

    def _tally(self, report: DQReport, res: ValidationResult) -> bool:
        metric = report.rule_metrics.setdefault(
            res.rule_name, {"passed": 0, "failed": 0, "skipped": 0}
        )
        if res.skipped:
            metric["skipped"] += 1
            report.skipped_checks += 1
            return False
        report.total_checks_performed += 1
        if res.is_valid:
            metric["passed"] += 1
            return False
        metric["failed"] += 1
        report.total_flags_raised += 1
        if res.severity == DQSeverity.CRITICAL:
            report.critical_flags += 1
        elif res.severity == DQSeverity.WARNING:
            report.warning_flags += 1
        else:
            report.info_flags += 1
        return True

    def audit_database(
        self,
        db_url: str | None = None,
        engine: Any = None,
        log_to_db: bool = True,
        coverage_start: date | None = None,
        coverage_end: date | None = None,
    ) -> DQReport:
        """Audit every current (non-superseded) event in the database. Idempotent flags."""
        eng = engine if engine is not None else get_engine(db_url)
        report = DQReport()

        with session_scope(eng) as session:
            repo = DistributionRepository(session)
            events = list(
                session.scalars(
                    select(DistributionEvent)
                    .where(DistributionEvent.is_superseded.is_(False))
                    .order_by(DistributionEvent.fund_id, DistributionEvent.ex_date)
                ).all()
            )
            report.total_events_evaluated = len(events)
            for rule in self.rules + self.fund_rules:
                report.rule_metrics[rule.rule_name] = {
                    "passed": 0,
                    "failed": 0,
                    "skipped": 0,
                }

            fund_event_map: dict[str, list[DistributionEvent]] = {}
            for ev in events:
                fund_event_map.setdefault(ev.fund_id, []).append(ev)

            for ev in events:
                components = list(
                    session.scalars(
                        select(DistributionComponent).where(
                            DistributionComponent.event_id == ev.event_id
                        )
                    ).all()
                )
                sc_record = session.get(ShareClass, ev.class_id)
                fund_record = session.get(FundMaster, ev.fund_id)
                other_amounts = [
                    float(e.reported_amount)
                    for e in session.scalars(
                        select(EventEvidence).where(
                            EventEvidence.event_id == ev.event_id
                        )
                    ).all()
                    if e.reported_amount is not None
                    and abs(float(e.reported_amount) - float(ev.gross_amount)) > 1e-9
                ]
                context = {
                    "country": fund_record.country if fund_record else None,
                    "share_class_currency": sc_record.currency if sc_record else None,
                    "second_source_amount": other_amounts[0] if other_amounts else None,
                    "components": components,
                }
                view = _EventView(ev, components)
                for res in self.validate_event(view, context=context):
                    if self._tally(report, res):
                        self._record(
                            report,
                            repo,
                            ev.fund_id,
                            ev.event_id,
                            str(ev.ex_date),
                            res,
                            log_to_db,
                        )

            for fund_id, fund_events in fund_event_map.items():
                sc_record = session.get(ShareClass, fund_events[0].class_id)
                ctx = {
                    "is_monthly_payer": bool(sc_record and sc_record.is_monthly_payer),
                    "all_fund_ex_dates": [e.ex_date for e in fund_events],
                    "coverage_start": coverage_start,
                    "coverage_end": coverage_end,
                }
                for rule in self.fund_rules:
                    res = rule.validate(None, context=ctx)
                    if self._tally(report, res):
                        self._record(report, repo, fund_id, None, None, res, log_to_db)

            if report.total_checks_performed > 0:
                passed = report.total_checks_performed - report.total_flags_raised
                report.pass_rate_pct = round(
                    passed / report.total_checks_performed * 100, 2
                )
        return report

    @staticmethod
    def _record(
        report: DQReport,
        repo: DistributionRepository,
        fund_id: str,
        event_id: str | None,
        ex_date: str | None,
        res: ValidationResult,
        log_to_db: bool,
    ) -> None:
        report.flagged_events.append(
            {
                "fund_id": fund_id,
                "event_id": event_id,
                "ex_date": ex_date,
                "rule_name": res.rule_name,
                "severity": res.severity.value,
                "message": res.message,
            }
        )
        if log_to_db:
            repo.log_dq_flag(
                fund_id=fund_id,
                event_id=event_id,
                rule_name=res.rule_name,
                severity=res.severity.value,
                message=res.message,
            )


class _EventView:
    """Read-only view of a stored event with its components attached (no ORM mutation)."""

    def __init__(
        self, event: DistributionEvent, components: list[DistributionComponent]
    ) -> None:
        self._event = event
        self.components = components

    def __getattr__(self, name: str) -> Any:
        return getattr(self._event, name)
