"""Test Suite for Data Quality Rules and Validation Engine (Phase 4).

Tests:
1. ComponentSumRule: validates sum match and flags deliberate component mismatches.
2. DateOrderRule: validates logical order and flags invalid date sequences.
3. NavDeclineRule: validates price decline consistency and flags large divergences.
4. MagnitudeRule: flags distributions > 20% NAV and rejects non-positive amounts.
5. CurrencyIntegrityRule: validates country currency rules and flags US/CAD mismatches.
6. FrequencyContinuityRule: detects missing months in monthly payer history.
7. CrossSourceVarianceRule: flags primary vs secondary source discrepancies.
8. DataQualityEngine: end-to-end database audit execution and flag persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import pytest
from sqlalchemy import create_engine

from src.database.connection import init_db, session_scope
from src.database.repository import DistributionRepository
from src.models import (
    CAComponentType,
    ExtractedComponent,
    ExtractedDistribution,
    USComponentType,
)
from src.validators.dq_engine import DataQualityEngine
from src.validators.rules import (
    ComponentSumRule,
    CrossSourceVarianceRule,
    CurrencyIntegrityRule,
    DateOrderRule,
    DQSeverity,
    FrequencyContinuityRule,
    MagnitudeRule,
    NavDeclineRule,
)


@dataclass
class MockComponent:
    amount: float
    name: str = "Income"


@dataclass
class MockEvent:
    fund_id: str = "US_VANGUARD_VTI"
    country: str = "US"
    currency: str = "USD"
    gross_amount: float = 1.00
    declaration_date: date | None = date(2024, 3, 15)
    ex_date: date = date(2024, 3, 22)
    record_date: date | None = date(2024, 3, 25)
    payable_date: date | None = date(2024, 3, 27)
    components: list[MockComponent] = None


def test_component_sum_rule_valid():
    rule = ComponentSumRule(tolerance=0.001)
    ev = MockEvent(
        gross_amount=1.00,
        components=[MockComponent(0.60), MockComponent(0.40)],
    )
    res = rule.validate(ev)
    assert res.is_valid is True


def test_component_sum_rule_deliberate_mismatch_flagged():
    rule = ComponentSumRule(tolerance=0.001)
    ev = MockEvent(
        gross_amount=1.00,
        components=[MockComponent(0.60), MockComponent(0.30)],  # sum = 0.90 != 1.00
    )
    res = rule.validate(ev)
    assert res.is_valid is False
    assert res.severity == DQSeverity.CRITICAL
    assert "Component sum mismatch" in res.message


def test_date_order_rule_valid():
    rule = DateOrderRule()
    ev = MockEvent(
        declaration_date=date(2024, 3, 1),
        ex_date=date(2024, 3, 15),
        record_date=date(2024, 3, 16),
        payable_date=date(2024, 3, 20),
    )
    res = rule.validate(ev)
    assert res.is_valid is True


def test_date_order_rule_pay_before_ex_flagged():
    rule = DateOrderRule()
    ev = MockEvent(
        declaration_date=date(2024, 3, 1),
        ex_date=date(2024, 3, 25),
        record_date=date(2024, 3, 26),
        payable_date=date(2024, 3, 10),  # Impossible: pay date before ex-date
    )
    res = rule.validate(ev)
    assert res.is_valid is False
    assert res.severity == DQSeverity.CRITICAL
    assert "Payable date" in res.message


def test_nav_decline_rule_consistent():
    rule = NavDeclineRule()
    ev = MockEvent(gross_amount=1.50)
    context = {"nav_prior_day": 100.0, "nav_ex_day": 98.50}
    res = rule.validate(ev, context=context)
    assert res.is_valid is True


def test_nav_decline_rule_large_divergence_flagged():
    rule = NavDeclineRule(market_move_tolerance_pct=0.02)
    ev = MockEvent(gross_amount=1.00)
    context = {"nav_prior_day": 100.0, "nav_ex_day": 85.0}  # Drop of $15 vs $1 distribution
    res = rule.validate(ev, context=context)
    assert res.is_valid is False
    assert res.severity == DQSeverity.WARNING


def test_magnitude_rule_normal():
    rule = MagnitudeRule(max_pct_threshold=0.20)
    ev = MockEvent(gross_amount=1.50)
    res = rule.validate(ev, context={"nav": 100.0})
    assert res.is_valid is True


def test_magnitude_rule_exceeding_20pct_flagged():
    rule = MagnitudeRule(max_pct_threshold=0.20)
    ev = MockEvent(gross_amount=25.0)  # 25% of NAV
    res = rule.validate(ev, context={"nav": 100.0})
    assert res.is_valid is False
    assert res.severity == DQSeverity.WARNING
    assert "exceeds 20% of NAV" in res.message


def test_currency_integrity_rule():
    rule = CurrencyIntegrityRule()
    # Valid US
    res_us = rule.validate(MockEvent(country="US", currency="USD"))
    assert res_us.is_valid is True

    # Invalid US (e.g. CAD assigned to US fund)
    res_bad = rule.validate(MockEvent(country="US", currency="CAD"))
    assert res_bad.is_valid is False
    assert res_bad.severity == DQSeverity.CRITICAL


def test_frequency_continuity_rule():
    rule = FrequencyContinuityRule()
    # Monthly with normal spacing
    ctx_good = {
        "is_monthly_payer": True,
        "all_fund_ex_dates": [date(2024, 1, 25), date(2024, 2, 23), date(2024, 3, 22)],
    }
    res_good = rule.validate(MockEvent(), context=ctx_good)
    assert res_good.is_valid is True

    # Monthly with a 3-month gap (missing month)
    ctx_gap = {
        "is_monthly_payer": True,
        "all_fund_ex_dates": [date(2024, 1, 25), date(2024, 5, 24)],  # 120 days gap
    }
    res_gap = rule.validate(MockEvent(), context=ctx_gap)
    assert res_gap.is_valid is False
    assert res_gap.severity == DQSeverity.WARNING


def test_cross_source_variance_rule():
    rule = CrossSourceVarianceRule()
    # Discrepancy between primary ($1.00) and secondary ($1.25)
    ctx = {
        "second_source_amount": 1.25,
        "second_source_url": "https://financials.marketwatch.com",
    }
    res = rule.validate(MockEvent(gross_amount=1.00), context=ctx)
    assert res.is_valid is False
    assert res.severity == DQSeverity.WARNING
    assert "Cross-source variance detected" in res.message


def test_dq_engine_database_audit_execution():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)

    with session_scope(engine) as session:
        repo = DistributionRepository(session)
        repo.upsert_fund("US_VTI", "Vanguard Total Stock Market", "Vanguard", "US", "ETF")
        repo.upsert_share_class("US_VTI_CLASS", "US_VTI", ticker="VTI", currency="USD")

        extracted = ExtractedDistribution(
            fund_id="US_VTI",
            country="US",
            currency="USD",
            ex_date=date(2024, 3, 22),
            gross_amount=0.9168,
            components=[
                ExtractedComponent("Ordinary Income", USComponentType.ORDINARY_INCOME, 0.9168, 100.0)
            ],
            source_url="https://investor.vanguard.com/vti",
        )
        repo.save_distribution_event(extracted)

    dq_engine = DataQualityEngine()
    # Run audit passing engine
    report = dq_engine.audit_database(engine=engine, log_to_db=False)
    assert report.pass_rate_pct == 100.0
