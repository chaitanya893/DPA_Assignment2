"""Gold set tooling (PDF Assignment 2: "Build a gold set" + atomic detector acceptance).

The gold set is ``config/gold_set.csv``: 300 distribution facts across >= 50 funds and >= 24
months, each verified by a person from a primary source, with the evidence URL recorded.
It must include NOT_DECLARED rows (months with no distribution), otherwise precision cannot be
measured. See docs/GOLD_SET_GUIDE.md.

The evaluator runs the *real* detector (default live strategies) over each gold window. It
never loads the gold set into the detector.

    python -m src.validators.gold_set_evaluator --validate-only    # check the file itself
    python -m src.validators.gold_set_evaluator                    # measure detector + extraction

Metrics
-------
positive = DECLARED.  TP: gold DECLARED, detector DECLARED.  FN: gold DECLARED, detector
NOT_DECLARED or UNKNOWN (UNKNOWN counted separately too).  FP: gold NOT_DECLARED, detector
DECLARED.  TN: gold NOT_DECLARED, detector NOT_DECLARED.
Targets: recall >= 98 %, precision >= 99 %.
For DECLARED rows with ex_date and gross_amount, extraction accuracy = share of rows where
Layer B produced an event with the same ex-date and amount (+/- 0.000001).
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from src.detector import detect_distribution
from src.extractor import ExtractionEngine
from src.http_client import HTTPClient, RecordingHTTPClient
from src.models import DetectionStatus
from src.universe_loader import UniverseRegistry

DEFAULT_GOLD_PATH = (
    Path(__file__).resolve().parent.parent.parent / "config" / "gold_set.csv"
)
REQUIRED_COLUMNS = (
    "gold_id",
    "fund_id",
    "window_start",
    "window_end",
    "expected_status",
    "ex_date",
    "gross_amount",
    "currency",
    "evidence_url",
    "verified_by",
    "verified_at",
)
MIN_EVENTS, MIN_FUNDS, MIN_MONTHS = 300, 50, 24


@dataclass
class GoldRow:
    gold_id: str
    fund_id: str
    window_start: date
    window_end: date
    expected_status: str
    ex_date: date | None
    gross_amount: float | None
    currency: str
    evidence_url: str
    verified_by: str
    verified_at: str


@dataclass
class GoldSetEvaluationReport:
    total_gold_events: int = 0
    evaluated_funds: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0
    unknown_on_declared: int = 0
    unknown_on_not_declared: int = 0
    precision_pct: float | None = None
    recall_pct: float | None = None
    f1_score_pct: float | None = None
    precision_target_met: bool = False
    recall_target_met: bool = False
    extraction_checked: int = 0
    extraction_correct: int = 0
    extraction_accuracy_pct: float | None = None
    by_family: dict[str, dict[str, int]] = field(default_factory=dict)
    failure_details: list[dict[str, Any]] = field(default_factory=list)


def _d(value: str | None) -> date | None:
    value = (value or "").strip()
    return date.fromisoformat(value) if value else None


def load_gold_set(path: Path | None = None) -> list[GoldRow]:
    p = Path(path or DEFAULT_GOLD_PATH)
    if not p.exists():
        raise FileNotFoundError(
            f"Gold set not found at {p}. Build it by hand from primary sources (docs/GOLD_SET_GUIDE.md)."
        )
    rows: list[GoldRow] = []
    with p.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"Gold set is missing columns: {missing}")
        for r in reader:
            if not any((v or "").strip() for v in r.values()):
                continue
            rows.append(
                GoldRow(
                    gold_id=r["gold_id"].strip(),
                    fund_id=r["fund_id"].strip(),
                    window_start=_d(r["window_start"]) or date.min,
                    window_end=_d(r["window_end"]) or date.min,
                    expected_status=r["expected_status"].strip().upper(),
                    ex_date=_d(r["ex_date"]),
                    gross_amount=(
                        float(r["gross_amount"])
                        if (r["gross_amount"] or "").strip()
                        else None
                    ),
                    currency=(r["currency"] or "").strip().upper(),
                    evidence_url=(r["evidence_url"] or "").strip(),
                    verified_by=(r["verified_by"] or "").strip(),
                    verified_at=(r["verified_at"] or "").strip(),
                )
            )
    return rows


def validate_gold_set(
    rows: list[GoldRow], universe: UniverseRegistry | None = None
) -> list[str]:
    """Return a list of problems. Empty list = the gold set meets the PDF requirements."""
    problems: list[str] = []
    universe = universe or UniverseRegistry.from_json()
    declared = [r for r in rows if r.expected_status == "DECLARED"]
    negatives = [r for r in rows if r.expected_status == "NOT_DECLARED"]
    if len(declared) < MIN_EVENTS:
        problems.append(
            f"{len(declared)} verified DECLARED events; PDF requires {MIN_EVENTS}."
        )
    funds = {r.fund_id for r in rows}
    if len(funds) < MIN_FUNDS:
        problems.append(f"{len(funds)} funds; PDF requires at least {MIN_FUNDS}.")
    months = {
        (r.window_start.year, r.window_start.month)
        for r in rows
        if r.window_start != date.min
    }
    if months:
        lo, hi = min(months), max(months)
        span = (hi[0] - lo[0]) * 12 + hi[1] - lo[1] + 1
        if span < MIN_MONTHS:
            problems.append(
                f"Gold set spans {span} months; PDF requires at least {MIN_MONTHS}."
            )
    if not negatives:
        problems.append(
            "No NOT_DECLARED rows: precision (false positives) cannot be measured."
        )
    ids: set[str] = set()
    for r in rows:
        where = f"row {r.gold_id or '?'}"
        if r.gold_id in ids:
            problems.append(f"{where}: duplicate gold_id")
        ids.add(r.gold_id)
        if universe.get_fund(r.fund_id) is None:
            problems.append(f"{where}: fund_id {r.fund_id} not in universe")
        if r.expected_status not in ("DECLARED", "NOT_DECLARED"):
            problems.append(
                f"{where}: expected_status must be DECLARED or NOT_DECLARED"
            )
        if (
            r.window_start == date.min
            or r.window_end == date.min
            or r.window_start > r.window_end
        ):
            problems.append(f"{where}: invalid window")
        if not r.evidence_url.startswith("http"):
            problems.append(
                f"{where}: evidence_url missing (primary source link required)"
            )
        if not r.verified_by or not r.verified_at:
            problems.append(
                f"{where}: verified_by / verified_at missing (manual verification required)"
            )
        if r.expected_status == "DECLARED":
            if r.ex_date is None or r.gross_amount is None or r.gross_amount <= 0:
                problems.append(
                    f"{where}: DECLARED rows need ex_date and a positive gross_amount"
                )
            elif not (r.window_start <= r.ex_date <= r.window_end):
                problems.append(f"{where}: ex_date outside its window")
    # Heuristic for generated data: one fund with the same amount in every month.
    amounts: dict[str, set[float]] = defaultdict(set)
    counts: dict[str, int] = defaultdict(int)
    for r in declared:
        if r.gross_amount is not None:
            amounts[r.fund_id].add(r.gross_amount)
            counts[r.fund_id] += 1
    for fid, vals in amounts.items():
        if counts[fid] >= 6 and len(vals) == 1:
            problems.append(
                f"{fid}: identical amount in {counts[fid]} events - looks generated, re-verify."
            )
    return problems


class GoldSetEvaluator:
    """Measure the real detector (and extractor) against a manually verified gold set."""

    def __init__(
        self,
        gold_set_path: Path | None = None,
        http_client: HTTPClient | None = None,
        universe: UniverseRegistry | None = None,
        strategies_factory: Any = None,
    ) -> None:
        self.gold_set_path = Path(gold_set_path or DEFAULT_GOLD_PATH)
        self.universe = universe or UniverseRegistry.from_json()
        self.http = RecordingHTTPClient(inner=http_client)
        self.strategies_factory = strategies_factory

    def evaluate(self, require_valid: bool = True) -> GoldSetEvaluationReport:
        rows = load_gold_set(self.gold_set_path)
        if require_valid:
            problems = validate_gold_set(rows, self.universe)
            if problems:
                raise ValueError(
                    "Gold set does not meet the PDF requirements:\n- "
                    + "\n- ".join(problems[:30])
                )
        report = GoldSetEvaluationReport(total_gold_events=len(rows))
        report.evaluated_funds = len({r.fund_id for r in rows})
        extractor = ExtractionEngine(http_client=self.http, universe=self.universe)

        for r in rows:
            strategies = (
                self.strategies_factory(self.http, self.universe)
                if self.strategies_factory
                else None
            )
            det = detect_distribution(
                r.fund_id,
                r.window_start,
                r.window_end,
                strategies=strategies,
                http_client=self.http,
                universe=self.universe,
            )
            fund = self.universe.get_fund(r.fund_id)
            fam = report.by_family.setdefault(
                fund.fund_family if fund else "?",
                {"TP": 0, "FN": 0, "FP": 0, "TN": 0, "UNK": 0},
            )
            got = det.status
            if r.expected_status == "DECLARED":
                if got == DetectionStatus.DECLARED:
                    report.true_positives += 1
                    fam["TP"] += 1
                    if r.ex_date and r.gross_amount:
                        report.extraction_checked += 1
                        events = extractor.extract_with_routes(det).events
                        if any(
                            e.ex_date == r.ex_date
                            and abs(e.gross_amount - r.gross_amount) < 1e-6
                            for e in events
                        ):
                            report.extraction_correct += 1
                        else:
                            report.failure_details.append(
                                {
                                    "gold_id": r.gold_id,
                                    "problem": "extraction mismatch",
                                    "expected": [str(r.ex_date), r.gross_amount],
                                    "got": [
                                        [str(e.ex_date), e.gross_amount] for e in events
                                    ],
                                }
                            )
                else:
                    report.false_negatives += 1
                    fam["FN"] += 1
                    if got == DetectionStatus.UNKNOWN:
                        report.unknown_on_declared += 1
                        fam["UNK"] += 1
                    report.failure_details.append(
                        {
                            "gold_id": r.gold_id,
                            "expected": "DECLARED",
                            "actual": got.value,
                        }
                    )
            else:
                if got == DetectionStatus.DECLARED:
                    report.false_positives += 1
                    fam["FP"] += 1
                    report.failure_details.append(
                        {
                            "gold_id": r.gold_id,
                            "expected": "NOT_DECLARED",
                            "actual": "DECLARED",
                        }
                    )
                else:
                    report.true_negatives += 1
                    fam["TN"] += 1
                    if got == DetectionStatus.UNKNOWN:
                        report.unknown_on_not_declared += 1

        tp, fp, fn = (
            report.true_positives,
            report.false_positives,
            report.false_negatives,
        )
        if tp + fp:
            report.precision_pct = round(tp / (tp + fp) * 100, 2)
        if tp + fn:
            report.recall_pct = round(tp / (tp + fn) * 100, 2)
        if report.precision_pct and report.recall_pct:
            p, rc = report.precision_pct, report.recall_pct
            report.f1_score_pct = round(2 * p * rc / (p + rc), 2)
        report.precision_target_met = (report.precision_pct or 0) >= 99.0
        report.recall_target_met = (report.recall_pct or 0) >= 98.0
        if report.extraction_checked:
            report.extraction_accuracy_pct = round(
                report.extraction_correct / report.extraction_checked * 100, 2
            )
        return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the gold set and measure detector accuracy against it"
    )
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD_PATH)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--out", type=Path, default=Path("quality/gold_set_evaluation.json")
    )
    args = parser.parse_args()

    rows = load_gold_set(args.gold)
    problems = validate_gold_set(rows)
    print(f"Gold set rows: {len(rows)}  funds: {len({r.fund_id for r in rows})}")
    if problems:
        print(f"{len(problems)} problem(s):")
        for p in problems[:50]:
            print("  -", p)
    else:
        print("Gold set meets the PDF requirements.")
    if args.validate_only or problems:
        raise SystemExit(1 if problems else 0)

    report = GoldSetEvaluator(args.gold).evaluate(require_valid=False)
    print(
        f"Precision {report.precision_pct}% (target >= 99)  Recall {report.recall_pct}% (target >= 98)"
    )
    print(
        f"TP {report.true_positives} FP {report.false_positives} FN {report.false_negatives} TN {report.true_negatives}"
        f"  (UNKNOWN on declared: {report.unknown_on_declared})"
    )
    print(
        f"Extraction accuracy: {report.extraction_accuracy_pct}% of {report.extraction_checked}"
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report.__dict__, indent=2, default=str), encoding="utf-8"
    )
    print(f"Report written to {args.out}")


if __name__ == "__main__":
    main()
