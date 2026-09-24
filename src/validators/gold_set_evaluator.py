"""Gold Set Benchmark Evaluator for Layer A Detection and Layer B Extraction.

Evaluates system precision and recall against the 300-event Gold Set
mandated on PDF Page 8 & Page 9:
- Recall >= 98%
- Precision >= 99%
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from src.detector import detect_distribution
from src.strategies import CalendarExpectationStrategy, VerifiedScheduleStrategy
from src.universe_loader import UniverseRegistry

logger = logging.getLogger(__name__)


@dataclass
class GoldSetEvaluationReport:
    """Benchmark evaluation outcome against Gold Set."""

    total_gold_events: int = 0
    evaluated_funds: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0
    precision_pct: float = 0.0
    recall_pct: float = 0.0
    f1_score_pct: float = 0.0
    precision_target_met: bool = False
    recall_target_met: bool = False
    failure_details: list[dict[str, Any]] = field(default_factory=list)


class GoldSetEvaluator:
    """Orchestrates Precision and Recall benchmarking against config/gold_set_300.json."""

    def __init__(self, gold_set_path: Path | None = None) -> None:
        self.gold_set_path = (
            gold_set_path
            or Path(__file__).parent.parent.parent / "config" / "gold_set_300.json"
        )
        self.universe = UniverseRegistry.from_json()

    def evaluate(self) -> GoldSetEvaluationReport:
        """Run full evaluation against Gold Set and calculate metrics."""
        if not self.gold_set_path.exists():
            raise FileNotFoundError(f"Gold set file not found: {self.gold_set_path}")

        with open(self.gold_set_path, "r", encoding="utf-8") as f:
            gold_data = json.load(f)

        gold_events = gold_data.get("events", [])
        report = GoldSetEvaluationReport(total_gold_events=len(gold_events))
        evaluated_fund_set: set[str] = set()

        tp = 0
        fp = 0
        fn = 0
        tn = 0

        strategies = [
            CalendarExpectationStrategy(universe=self.universe),
            VerifiedScheduleStrategy(universe=self.universe),
        ]

        for item in gold_events:
            fund_id = item["fund_id"]
            evaluated_fund_set.add(fund_id)
            expected_status = item.get("expected_status", "DECLARED")
            ex_date_str = item.get("ex_date")
            if not ex_date_str:
                continue

            ex_d = date.fromisoformat(ex_date_str)
            w_start = date(ex_d.year, ex_d.month, 1)
            if ex_d.month in {1, 3, 5, 7, 8, 10, 12}:
                w_end = date(ex_d.year, ex_d.month, 31)
            elif ex_d.month in {4, 6, 9, 11}:
                w_end = date(ex_d.year, ex_d.month, 30)
            else:
                w_end = date(
                    ex_d.year, ex_d.month, 29 if ex_d.year % 4 == 0 else 28
                )

            fund = self.universe.get_fund(fund_id)
            if not fund:
                fn += 1
                report.failure_details.append(
                    {"fund_id": fund_id, "error": "Fund not found in universe"}
                )
                continue

            # Execute genuine Layer A detection engine over the event window
            detection = detect_distribution(
                fund_id=fund_id,
                window_start=w_start,
                window_end=w_end,
                strategies=strategies,
                universe=self.universe,
            )
            actual_status = detection.status.value

            if expected_status == "DECLARED":
                if actual_status == "DECLARED":
                    tp += 1
                else:
                    fn += 1
                    report.failure_details.append(
                        {
                            "fund_id": fund_id,
                            "ex_date": ex_date_str,
                            "expected": "DECLARED",
                            "actual": actual_status,
                        }
                    )
            else:
                if actual_status == "DECLARED":
                    fp += 1
                else:
                    tn += 1

        report.evaluated_funds = len(evaluated_fund_set)
        report.true_positives = tp
        report.false_positives = fp
        report.false_negatives = fn
        report.true_negatives = tn

        # Precision = TP / (TP + FP)
        precision = (tp / (tp + fp)) * 100 if (tp + fp) > 0 else 100.0
        # Recall = TP / (TP + FN)
        recall = (tp / (tp + fn)) * 100 if (tp + fn) > 0 else 100.0
        f1 = (
            (2 * precision * recall) / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )

        report.precision_pct = round(precision, 2)
        report.recall_pct = round(recall, 2)
        report.f1_score_pct = round(f1, 2)

        report.precision_target_met = report.precision_pct >= 99.0
        report.recall_target_met = report.recall_pct >= 98.0

        return report


def main() -> None:
    evaluator = GoldSetEvaluator()
    report = evaluator.evaluate()

    print("=" * 100)
    print("                 GOLD SET ACCURACY BENCHMARK (PDF PAGE 8 & 9 SPECIFICATION)")
    print("=" * 100)
    print(f"Total Gold Events Evaluated   : {report.total_gold_events}")
    print(f"Distinct Funds in Gold Set    : {report.evaluated_funds}")
    print(f"True Positives (TP)           : {report.true_positives}")
    print(f"False Positives (FP)          : {report.false_positives}")
    print(f"False Negatives (FN)          : {report.false_negatives}")
    print("-" * 100)
    print(f"Precision                     : {report.precision_pct:.2f}% (Target: >= 99.0%) -> {'PASS' if report.precision_target_met else 'FAIL'}")
    print(f"Recall                        : {report.recall_pct:.2f}% (Target: >= 98.0%) -> {'PASS' if report.recall_target_met else 'FAIL'}")
    print(f"F1 Score                      : {report.f1_score_pct:.2f}%")
    print("=" * 100)


if __name__ == "__main__":
    main()
