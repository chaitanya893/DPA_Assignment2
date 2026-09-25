"""Deterministic Data Quality Validation Rules for Fund Distributions.

Fulfills Phase 4 Acceptance Criteria (PDF Pages 8-9):
1. ComponentSumRule: Components sum to the stated gross total within tolerance.
2. DateOrderRule: Declaration <= Ex-Date <= Record Date <= Payable Date.
3. NavDeclineRule: Ex-date NAV change is consistent with distribution per share.
4. MagnitudeRule: Flags distribution > 20% of NAV for review rather than reject.
5. CurrencyIntegrityRule: Currency matches the fund country and share class.
6. FrequencyContinuityRule: Monthly payers with missing months get flagged.
7. CrossSourceVarianceRule: Discrepancies between sources recorded and flagged.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any


class DQSeverity(str, Enum):
    """Severity levels for data quality validation flags."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass
class ValidationResult:
    """Outcome of a single validation rule execution."""

    is_valid: bool
    rule_name: str
    severity: DQSeverity = DQSeverity.INFO
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    # True when the rule could not run because its input data was not available
    # (e.g. no NAV series). A skipped check is reported separately and is NOT a pass.
    skipped: bool = False


class BaseValidationRule(ABC):
    """Abstract base class for data quality validation rules."""

    rule_name: str = "BASE_RULE"

    @abstractmethod
    def validate(
        self, event: Any, context: dict[str, Any] | None = None
    ) -> ValidationResult:
        """Execute validation rule logic against a distribution event."""
        raise NotImplementedError


# -----------------------------------------------------------------------------
# 1. Component Sum Rule
# -----------------------------------------------------------------------------
class ComponentSumRule(BaseValidationRule):
    """Validates that the sum of granular tax components equals gross distribution."""

    rule_name = "COMPONENT_SUM_CHECK"

    def __init__(self, tolerance: float = 0.0005) -> None:
        self.tolerance = tolerance

    def validate(
        self, event: Any, context: dict[str, Any] | None = None
    ) -> ValidationResult:
        gross_amt = float(getattr(event, "gross_amount", 0.0) or 0.0)
        components = getattr(event, "components", []) or []

        if not components:
            return ValidationResult(
                is_valid=True,
                rule_name=self.rule_name,
                message="Source did not publish a component breakdown; component sum not checkable.",
                skipped=True,
            )

        comp_sum = sum(float(getattr(c, "amount", 0.0) or 0.0) for c in components)
        diff = abs(gross_amt - comp_sum)

        if diff > self.tolerance:
            return ValidationResult(
                is_valid=False,
                rule_name=self.rule_name,
                severity=DQSeverity.CRITICAL,
                message=(
                    f"Component sum mismatch: sum of components (${comp_sum:.4f}) does not equal "
                    f"gross amount (${gross_amt:.4f}) within tolerance $\\pm${self.tolerance:.4f} (diff: {diff:.4f})."
                ),
                details={
                    "gross_amount": gross_amt,
                    "component_sum": comp_sum,
                    "diff": diff,
                },
            )

        return ValidationResult(
            is_valid=True,
            rule_name=self.rule_name,
            message="Component sum matches gross amount within tolerance.",
            details={"gross_amount": gross_amt, "component_sum": comp_sum},
        )


# -----------------------------------------------------------------------------
# 2. Date Ordering Sanity Rule
# -----------------------------------------------------------------------------
class DateOrderRule(BaseValidationRule):
    """Validates chronological date sequence: Declaration <= Ex <= Record <= Payable."""

    rule_name = "DATE_ORDERING_SANITY"

    def validate(
        self, event: Any, context: dict[str, Any] | None = None
    ) -> ValidationResult:
        decl_d: date | None = getattr(event, "declaration_date", None)
        ex_d: date | None = getattr(event, "ex_date", None)
        rec_d: date | None = getattr(event, "record_date", None)
        pay_d: date | None = getattr(event, "payable_date", None)

        if not ex_d:
            return ValidationResult(
                is_valid=False,
                rule_name=self.rule_name,
                severity=DQSeverity.CRITICAL,
                message="Missing mandatory Ex-Date.",
            )

        fund_type = (context or {}).get("fund_type", "ETF")
        errors: list[str] = []

        if fund_type == "MUTUAL_FUND":
            if decl_d and decl_d > ex_d:
                errors.append(f"Declaration date ({decl_d}) is after Ex-date ({ex_d})")

            if decl_d and rec_d and decl_d > rec_d:
                errors.append(
                    f"Declaration date ({decl_d}) is after Record date ({rec_d})"
                )

            if pay_d and pay_d < ex_d:
                errors.append(f"Payable date ({pay_d}) is before Ex-date ({ex_d})")

            if pay_d and rec_d and pay_d < rec_d:
                errors.append(f"Payable date ({pay_d}) is before Record date ({rec_d})")
        else:
            if decl_d and decl_d > ex_d:
                errors.append(f"Declaration date ({decl_d}) is after Ex-date ({ex_d})")

            if rec_d and rec_d < ex_d:
                errors.append(f"Record date ({rec_d}) is before Ex-date ({ex_d})")

            if pay_d and ex_d and pay_d < ex_d:
                errors.append(f"Payable date ({pay_d}) is before Ex-date ({ex_d})")

            if pay_d and rec_d and pay_d < rec_d:
                errors.append(f"Payable date ({pay_d}) is before Record date ({rec_d})")

        if errors:
            return ValidationResult(
                is_valid=False,
                rule_name=self.rule_name,
                severity=DQSeverity.CRITICAL,
                message="; ".join(errors),
                details={
                    "declaration": str(decl_d),
                    "ex": str(ex_d),
                    "record": str(rec_d),
                    "pay": str(pay_d),
                },
            )

        return ValidationResult(
            is_valid=True,
            rule_name=self.rule_name,
            message="Date ordering sequence is logically valid.",
        )


# -----------------------------------------------------------------------------
# 3. NAV Decline Consistency Rule
# -----------------------------------------------------------------------------
class NavDeclineRule(BaseValidationRule):
    """Checks whether Ex-Date NAV change is broadly consistent with distribution per share."""

    rule_name = "NAV_DECLINE_CONSISTENCY"

    def __init__(self, market_move_tolerance_pct: float = 0.05) -> None:
        self.market_tolerance = market_move_tolerance_pct

    def validate(
        self, event: Any, context: dict[str, Any] | None = None
    ) -> ValidationResult:
        ctx = context or {}
        nav_prior = ctx.get("nav_prior_day")
        nav_ex = ctx.get("nav_ex_day")
        gross_amt = float(getattr(event, "gross_amount", 0.0) or 0.0)

        if nav_prior is None or nav_ex is None:
            return ValidationResult(
                is_valid=True,
                rule_name=self.rule_name,
                severity=DQSeverity.INFO,
                message="NAV prior/ex-day prices not supplied in context; skipped.",
                skipped=True,
            )

        actual_decline = float(nav_prior) - float(nav_ex)
        expected_decline = gross_amt

        # In a real market, market drift affects ex-date price.
        # Check if actual decline is in the same direction or within reasonable bound:
        diff = abs(actual_decline - expected_decline)
        max_acceptable_diff = float(nav_prior) * self.market_tolerance + gross_amt * 0.5

        if diff > max_acceptable_diff:
            return ValidationResult(
                is_valid=False,
                rule_name=self.rule_name,
                severity=DQSeverity.WARNING,
                message=(
                    f"Ex-date NAV decline (${actual_decline:.4f}) deviates significantly from "
                    f"distribution per share (${gross_amt:.4f}) by ${diff:.4f}."
                ),
                details={
                    "nav_prior": nav_prior,
                    "nav_ex": nav_ex,
                    "gross_amt": gross_amt,
                    "diff": diff,
                },
            )

        return ValidationResult(
            is_valid=True,
            rule_name=self.rule_name,
            message="Ex-date NAV decline is consistent with distribution per share.",
        )


# -----------------------------------------------------------------------------
# 4. Magnitude Check (> 20% of NAV)
# -----------------------------------------------------------------------------
class MagnitudeRule(BaseValidationRule):
    """Flags distribution amounts exceeding 20% of NAV for human review without hard reject."""

    rule_name = "MAGNITUDE_20PCT_NAV_CHECK"

    def __init__(self, max_pct_threshold: float = 0.20) -> None:
        self.max_pct = max_pct_threshold

    def validate(
        self, event: Any, context: dict[str, Any] | None = None
    ) -> ValidationResult:
        ctx = context or {}
        nav = ctx.get("nav") or ctx.get("nav_prior_day")
        gross_amt = float(getattr(event, "gross_amount", 0.0) or 0.0)

        if gross_amt <= 0.0:
            return ValidationResult(
                is_valid=False,
                rule_name=self.rule_name,
                severity=DQSeverity.CRITICAL,
                message=f"Distribution amount (${gross_amt}) is non-positive.",
            )

        if not nav or float(nav) <= 0.0:
            return ValidationResult(
                is_valid=True,
                rule_name=self.rule_name,
                severity=DQSeverity.INFO,
                message="No NAV supplied; 20% of NAV magnitude check skipped.",
                skipped=True,
            )
        if nav and float(nav) > 0.0:
            dist_pct = gross_amt / float(nav)
            if dist_pct > self.max_pct:
                return ValidationResult(
                    is_valid=False,
                    rule_name=self.rule_name,
                    severity=DQSeverity.WARNING,
                    message=(
                        f"Distribution amount (${gross_amt:.4f}) exceeds {self.max_pct * 100:.0f}% of NAV "
                        f"(${float(nav):.2f}) at {dist_pct * 100:.1f}%. Flagged for human review."
                    ),
                    details={
                        "gross_amount": gross_amt,
                        "nav": nav,
                        "distribution_pct": dist_pct,
                    },
                )

        return ValidationResult(
            is_valid=True,
            rule_name=self.rule_name,
            message="Distribution magnitude is within normal operating bounds.",
        )


# -----------------------------------------------------------------------------
# 5. Currency Integrity Rule
# -----------------------------------------------------------------------------
class CurrencyIntegrityRule(BaseValidationRule):
    """Validates the distribution currency matches the share class currency (PDF Phase 4).

    Falls back to the country default (USD for US, CAD for CA) only when the share class
    currency is not supplied.
    """

    rule_name = "CURRENCY_INTEGRITY"

    def validate(
        self, event: Any, context: dict[str, Any] | None = None
    ) -> ValidationResult:
        ctx = context or {}
        currency = str(getattr(event, "currency", "") or "").upper()
        expected = str(ctx.get("share_class_currency") or "").upper()
        if not expected:
            country = str(
                getattr(event, "country", "") or ctx.get("country", "")
            ).upper()
            expected = {"US": "USD", "CA": "CAD"}.get(country, "")

        if not currency:
            return ValidationResult(
                is_valid=False,
                rule_name=self.rule_name,
                severity=DQSeverity.CRITICAL,
                message="Distribution has no currency.",
            )
        if expected and currency != expected:
            return ValidationResult(
                is_valid=False,
                rule_name=self.rule_name,
                severity=DQSeverity.CRITICAL,
                message=f"Currency '{currency}' does not match share class currency '{expected}'.",
                details={"currency": currency, "expected": expected},
            )
        if not expected:
            return ValidationResult(
                is_valid=True,
                rule_name=self.rule_name,
                message="Share class currency unknown; currency check skipped.",
                skipped=True,
            )
        return ValidationResult(
            is_valid=True,
            rule_name=self.rule_name,
            message="Distribution currency matches share class currency.",
        )


# -----------------------------------------------------------------------------
# 6. Frequency Continuity Rule
# -----------------------------------------------------------------------------
class FrequencyContinuityRule(BaseValidationRule):
    """Flags calendar months with no distribution for a monthly payer (PDF Phase 4).

    Checks every month between ``coverage_start`` and ``coverage_end`` when the caller
    supplies them (so a missing first or last month is also caught); otherwise checks the
    months between the fund's first and last known ex-date.
    """

    rule_name = "FREQUENCY_CONTINUITY"

    def validate(
        self, event: Any, context: dict[str, Any] | None = None
    ) -> ValidationResult:
        ctx = context or {}
        if not ctx.get("is_monthly_payer", False):
            return ValidationResult(
                is_valid=True,
                rule_name=self.rule_name,
                message="Not a monthly payer; monthly continuity not applicable.",
                skipped=True,
            )
        all_event_dates: list[date] = sorted(set(ctx.get("all_fund_ex_dates", [])))
        if not all_event_dates:
            return ValidationResult(
                is_valid=True,
                rule_name=self.rule_name,
                message="No events to check.",
                skipped=True,
            )
        start: date = ctx.get("coverage_start") or all_event_dates[0]
        end: date = ctx.get("coverage_end") or all_event_dates[-1]
        have = {(d.year, d.month) for d in all_event_dates}
        missing: list[str] = []
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            if (y, m) not in have:
                missing.append(f"{y}-{m:02d}")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)

        if missing:
            return ValidationResult(
                is_valid=False,
                rule_name=self.rule_name,
                severity=DQSeverity.WARNING,
                message=f"Monthly payer has no distribution recorded for: {', '.join(missing)}",
                details={"missing_months": missing},
            )
        return ValidationResult(
            is_valid=True,
            rule_name=self.rule_name,
            message="Monthly payer has a distribution in every month of the period.",
        )


# -----------------------------------------------------------------------------
# 7. Cross-Source Variance Rule
# -----------------------------------------------------------------------------
class CrossSourceVarianceRule(BaseValidationRule):
    """Flags discrepancies between two sources without silently picking one."""

    rule_name = "CROSS_SOURCE_VARIANCE"

    def validate(
        self, event: Any, context: dict[str, Any] | None = None
    ) -> ValidationResult:
        ctx = context or {}
        second_source_amt = ctx.get("second_source_amount")
        second_source_url = ctx.get("second_source_url")
        gross_amt = float(getattr(event, "gross_amount", 0.0) or 0.0)

        if second_source_amt is None:
            return ValidationResult(
                is_valid=True,
                rule_name=self.rule_name,
                message="Only one source available; cross-source comparison skipped.",
                skipped=True,
            )
        if second_source_amt is not None:
            sec_amt = float(second_source_amt)
            diff = abs(gross_amt - sec_amt)
            if diff > 0.001:
                return ValidationResult(
                    is_valid=False,
                    rule_name=self.rule_name,
                    severity=DQSeverity.WARNING,
                    message=(
                        f"Cross-source variance detected: Primary source reported ${gross_amt:.4f} but "
                        f"Secondary source ({second_source_url or 'Secondary Feed'}) reported ${sec_amt:.4f} "
                        f"(diff: ${diff:.4f}). Both recorded and flagged for review."
                    ),
                    details={
                        "primary_amount": gross_amt,
                        "secondary_amount": sec_amt,
                        "diff": diff,
                    },
                )

        return ValidationResult(
            is_valid=True,
            rule_name=self.rule_name,
            message="Cross-source reconciliation verified.",
        )
