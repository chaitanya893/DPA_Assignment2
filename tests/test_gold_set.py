"""Test Suite for Gold Set Benchmark (Phase 4).

Validates:
1. Exact 300-event benchmark size.
2. 50-fund universe coverage (30 US + 20 CA).
3. Primary source evidence URL presence for 100% of events.
4. Precision >= 99% and Recall >= 98% compliance against Gold Set.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from src.validators.gold_set_evaluator import GoldSetEvaluator


@pytest.fixture
def gold_set_data():
    path = Path(__file__).parent.parent / "config" / "gold_set_300.json"
    assert path.exists(), f"Gold set not found at {path}"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_gold_set_exactly_300_events(gold_set_data):
    events = gold_set_data.get("events", [])
    assert len(events) == 300, f"Expected exactly 300 gold set events, got {len(events)}"


def test_gold_set_covers_at_least_50_funds(gold_set_data):
    events = gold_set_data.get("events", [])
    funds = set(e["fund_id"] for e in events)
    assert len(funds) >= 50, f"Expected >= 50 distinct funds, got {len(funds)}"


def test_gold_set_mandatory_fields_and_evidence(gold_set_data):
    events = gold_set_data.get("events", [])
    for idx, e in enumerate(events, 1):
        assert "fund_id" in e and e["fund_id"], f"Event #{idx} missing fund_id"
        assert "ex_date" in e and e["ex_date"], f"Event #{idx} missing ex_date"
        assert "expected_gross_amount" in e and e["expected_gross_amount"] > 0, f"Event #{idx} invalid amount"
        assert "currency" in e and e["currency"] in ("USD", "CAD"), f"Event #{idx} invalid currency"
        assert "evidence_url" in e and e["evidence_url"].startswith("http"), f"Event #{idx} invalid evidence_url"


def test_gold_set_evaluator_precision_and_recall_targets():
    evaluator = GoldSetEvaluator()
    report = evaluator.evaluate()

    assert report.total_gold_events == 300
    assert report.precision_pct >= 99.0, f"Precision {report.precision_pct}% below 99.0% threshold"
    assert report.recall_pct >= 98.0, f"Recall {report.recall_pct}% below 98.0% threshold"
    assert report.precision_target_met is True
    assert report.recall_target_met is True
