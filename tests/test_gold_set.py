"""Gold set tooling tests (PDF: 300 manually verified events, >= 50 funds, >= 24 months).

The real gold set must be built by a person from primary sources. These tests check the
validator and evaluator with SYNTHETIC rows only.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from src.strategies import OfficialSponsorWebStrategy
from src.universe_loader import UniverseRegistry
from src.validators.gold_set_evaluator import (
    REQUIRED_COLUMNS,
    GoldSetEvaluator,
    load_gold_set,
    validate_gold_set,
)
from tests.fakes import StubHTTPClient


@pytest.fixture(scope="module")
def universe() -> UniverseRegistry:
    return UniverseRegistry.from_json()


def _write(path: Path, rows: list[dict]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(REQUIRED_COLUMNS) + ["notes"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in list(REQUIRED_COLUMNS) + ["notes"]})
    return path


def _row(
    i: int, fund: str, start: date, end: date, status: str, ex: str = "", amt: str = ""
) -> dict:
    return {
        "gold_id": f"g{i}",
        "fund_id": fund,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "expected_status": status,
        "ex_date": ex,
        "gross_amount": amt,
        "currency": "USD",
        "evidence_url": "https://primary.source.test/notice",
        "verified_by": "reviewer",
        "verified_at": "2026-09-24",
    }


def test_committed_template_has_required_columns() -> None:
    header = (
        Path("config/gold_set.csv")
        .read_text(encoding="utf-8")
        .splitlines()[0]
        .split(",")
    )
    assert all(c in header for c in REQUIRED_COLUMNS)


def test_validator_rejects_small_generated_or_positive_only_sets(
    tmp_path: Path, universe
) -> None:
    rows = [
        _row(
            i,
            "US_VANGUARD_BND",
            date(2024, m, 1),
            date(2024, m, 28),
            "DECLARED",
            f"2024-{m:02d}-03",
            "0.2312",
        )
        for i, m in enumerate(range(1, 10))
    ]
    problems = validate_gold_set(
        load_gold_set(_write(tmp_path / "g.csv", rows)), universe
    )
    text = " | ".join(problems)
    assert "PDF requires 300" in text
    assert "NOT_DECLARED" in text
    assert "looks generated" in text  # identical amount every month
    assert "spans" in text  # fewer than 24 months


def test_validator_requires_evidence_and_verifier(tmp_path: Path, universe) -> None:
    r = _row(
        1,
        "US_VANGUARD_VTI",
        date(2024, 3, 1),
        date(2024, 3, 31),
        "DECLARED",
        "2024-03-22",
        "0.9",
    )
    r["evidence_url"] = ""
    r["verified_by"] = ""
    problems = validate_gold_set(
        load_gold_set(_write(tmp_path / "g.csv", [r])), universe
    )
    assert any("evidence_url missing" in p for p in problems)
    assert any("verified_by" in p for p in problems)


def test_evaluator_measures_real_detector_including_false_positives(
    tmp_path: Path, universe
) -> None:
    """Evaluator runs the detector (never the gold file) and counts TP / FP / FN / TN."""
    fund = universe.get_fund("US_VANGUARD_VTI")
    page = """<html><body>VTI 2024 Distribution Schedule<table>
    <tr><th>Ex-Date</th><th>Record Date</th><th>Payable Date</th><th>Amount</th></tr>
    <tr><td>2024-03-22</td><td>2024-03-22</td><td>2024-03-26</td><td>$0.9168</td></tr>
    <tr><td>2024-06-28</td><td>2024-06-28</td><td>2024-07-02</td><td>$0.8590</td></tr>
    </table></body></html>"""  # SYNTHETIC
    client = StubHTTPClient({fund.official_source_url: page})
    rows = [
        _row(
            1,
            fund.fund_id,
            date(2024, 3, 1),
            date(2024, 3, 31),
            "DECLARED",
            "2024-03-22",
            "0.9168",
        ),
        _row(
            2,
            fund.fund_id,
            date(2024, 6, 1),
            date(2024, 6, 30),
            "DECLARED",
            "2024-06-28",
            "0.8590",
        ),
        _row(3, fund.fund_id, date(2024, 4, 1), date(2024, 4, 30), "NOT_DECLARED"),
        _row(
            4,
            fund.fund_id,
            date(2024, 9, 1),
            date(2024, 9, 30),
            "DECLARED",
            "2024-09-27",
            "0.8872",
        ),
    ]
    ev = GoldSetEvaluator(
        _write(tmp_path / "g.csv", rows),
        http_client=client,
        universe=universe,
        strategies_factory=lambda http, uni: [
            OfficialSponsorWebStrategy(http_client=http, universe=uni)
        ],
    )
    report = ev.evaluate(require_valid=False)
    assert (
        report.true_positives,
        report.false_negatives,
        report.true_negatives,
        report.false_positives,
    ) == (2, 1, 1, 0)
    assert report.recall_pct == pytest.approx(66.67)
    assert report.extraction_checked == 2 and report.extraction_correct == 2


def test_evaluator_refuses_invalid_gold_set(tmp_path: Path, universe) -> None:
    path = _write(
        tmp_path / "g.csv",
        [
            _row(
                1,
                "US_VANGUARD_VTI",
                date(2024, 3, 1),
                date(2024, 3, 31),
                "NOT_DECLARED",
            )
        ],
    )
    with pytest.raises(ValueError, match="does not meet the PDF requirements"):
        GoldSetEvaluator(path, universe=universe).evaluate()
