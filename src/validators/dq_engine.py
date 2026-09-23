"""Data Quality Engine for Fund Distributions.

Orchestrates validation rules against distribution events, logs failures to the
`dq_flag` database table, and calculates comprehensive quality metrics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.connection import get_engine, session_scope
from src.database.models import DistributionComponent, DistributionEvent, DQFlag, FundMaster, ShareClass
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
    """Consolidated Data Quality Audit Report."""

    total_events_evaluated: int = 0
    total_checks_performed: int = 0
    total_flags_raised: int = 0
    critical_flags: int = 0
    warning_flags: int = 0
    info_flags: int = 0
    pass_rate_pct: float = 100.0
    rule_metrics: dict[str, dict[str, int]] = field(default_factory=dict)
    flagged_events: list[dict[str, Any]] = field(default_factory=list)


class DataQualityEngine:
    """Executes deterministic validation checks on extracted events and database records."""

    def __init__(self, rules: list[BaseValidationRule] | None = None) -> None:
        self.rules: list[BaseValidationRule] = rules or [
            ComponentSumRule(),
            DateOrderRule(),
            NavDeclineRule(),
            MagnitudeRule(),
            CurrencyIntegrityRule(),
            FrequencyContinuityRule(),
            CrossSourceVarianceRule(),
        ]

    def validate_event(
        self,
        event: Any,
        context: dict[str, Any] | None = None,
    ) -> list[ValidationResult]:
        """Run all configured validation rules against a single distribution event."""
        results: list[ValidationResult] = []
        for rule in self.rules:
            try:
                res = rule.validate(event, context=context)
                results.append(res)
            except Exception as err:
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

    def audit_database(
        self,
        db_url: str | None = None,
        engine: Any = None,
        log_to_db: bool = True,
    ) -> DQReport:
        """Run full data quality audit over all distribution events stored in the database."""
        eng = engine if engine is not None else get_engine(db_url)
        report = DQReport()

        with session_scope(eng) as session:
            repo = DistributionRepository(session)

            # Fetch all distribution events
            events = list(session.scalars(select(DistributionEvent).order_by(DistributionEvent.fund_id)).all())
            report.total_events_evaluated = len(events)

            # Group events by fund_id for continuity checking
            fund_event_map: dict[str, list[DistributionEvent]] = {}
            for ev in events:
                fund_event_map.setdefault(ev.fund_id, []).append(ev)

            # Rule metric counters
            for rule in self.rules:
                report.rule_metrics[rule.rule_name] = {"passed": 0, "failed": 0}

            for ev in events:
                # Fetch components for this event
                comp_stmt = select(DistributionComponent).where(DistributionComponent.event_id == ev.event_id)
                components = list(session.scalars(comp_stmt).all())
                ev.components = components  # Attach for component sum check

                # Build context
                fund_id = ev.fund_id
                fund_record = session.get(FundMaster, fund_id)
                sc_record = session.get(ShareClass, ev.class_id)

                all_dates = [e.ex_date for e in fund_event_map.get(fund_id, [])]
                context = {
                    "country": fund_record.country if fund_record else "US",
                    "is_monthly_payer": sc_record.is_monthly_payer if sc_record else False,
                    "all_fund_ex_dates": all_dates,
                }

                results = self.validate_event(ev, context=context)

                for res in results:
                    report.total_checks_performed += 1
                    rule_metric = report.rule_metrics.setdefault(res.rule_name, {"passed": 0, "failed": 0})

                    if res.is_valid:
                        rule_metric["passed"] += 1
                    else:
                        rule_metric["failed"] += 1
                        report.total_flags_raised += 1

                        if res.severity == DQSeverity.CRITICAL:
                            report.critical_flags += 1
                        elif res.severity == DQSeverity.WARNING:
                            report.warning_flags += 1
                        else:
                            report.info_flags += 1

                        report.flagged_events.append(
                            {
                                "fund_id": ev.fund_id,
                                "event_id": ev.event_id,
                                "ex_date": str(ev.ex_date),
                                "rule_name": res.rule_name,
                                "severity": res.severity.value,
                                "message": res.message,
                            }
                        )

                        if log_to_db:
                            repo.log_dq_flag(
                                fund_id=ev.fund_id,
                                event_id=ev.event_id,
                                rule_name=res.rule_name,
                                severity=res.severity.value,
                                message=res.message,
                            )

            if report.total_checks_performed > 0:
                passed_checks = report.total_checks_performed - report.total_flags_raised
                report.pass_rate_pct = round((passed_checks / report.total_checks_performed) * 100, 2)
            else:
                report.pass_rate_pct = 100.0

        return report
