"""Phase 1 / final-memo report built from what the pipeline actually recorded.

Reads ``detection_run`` (every Layer A check), ``distribution_event``, ``review_queue`` and
``crawl_log`` and writes quality/detection_report.{json,md} with:

- hit rate (share of checks that were DECLARED), NOT_DECLARED and UNKNOWN rates;
- UNKNOWN reasons (where automation fails);
- route taken for every DECLARED check (how much of the universe is automatable);
- extraction rate without manual intervention (PDF target >= 90 %);
- coverage by fund family and by source type;
- average cost per check (HTTP requests, bytes, seconds, and compute cost at a stated rate);
- false-negative rate when a gold-set evaluation file is present.

    python -m src.reports.detection_report [--usd-per-compute-hour 0.10]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import select

from src.database.connection import get_engine, session_scope
from src.database.models import (
    CrawlLog,
    DetectionRun,
    DistributionEvent,
    FundMaster,
    ReviewQueue,
)

OUT_DIR = Path(__file__).resolve().parent.parent.parent / "quality"


def build_report(
    db_url: str | None = None, usd_per_compute_hour: float = 0.10
) -> dict[str, Any]:
    engine = get_engine(db_url)
    with session_scope(engine) as s:
        runs = list(s.scalars(select(DetectionRun)).all())
        families = {
            f.fund_id: f.fund_family for f in s.scalars(select(FundMaster)).all()
        }
        countries = {f.fund_id: f.country for f in s.scalars(select(FundMaster)).all()}
        events = list(
            s.scalars(
                select(DistributionEvent).where(
                    DistributionEvent.is_superseded.is_(False)
                )
            ).all()
        )
        reviews = list(s.scalars(select(ReviewQueue)).all())
        crawls = list(s.scalars(select(CrawlLog)).all())

        n = len(runs)
        status = Counter(r.status for r in runs)
        declared = [r for r in runs if r.status == "DECLARED"]
        auto = [r for r in declared if r.route_taken and r.route_taken != "MANUAL"]
        fam: dict[str, Counter] = defaultdict(Counter)
        for r in runs:
            fam[families.get(r.fund_id, "?")][r.status] += 1
        by_country: dict[str, Counter] = defaultdict(Counter)
        for r in runs:
            by_country[countries.get(r.fund_id, "?")][r.status] += 1
        seconds = sum(r.duration_seconds for r in runs)

        fam_reasons: dict[str, Counter] = defaultdict(Counter)
        for r in runs:
            if r.status == "UNKNOWN" and r.unknown_reason:
                fam_reasons[families.get(r.fund_id, "?")][r.unknown_reason] += 1

        report: dict[str, Any] = {
            "checks": n,
            "funds_checked": len({r.fund_id for r in runs}),
            "status_counts": dict(status),
            "hit_rate_pct": round(status["DECLARED"] / n * 100, 2) if n else None,
            "not_declared_rate_pct": (
                round(status["NOT_DECLARED"] / n * 100, 2) if n else None
            ),
            "unknown_rate_pct": round(status["UNKNOWN"] / n * 100, 2) if n else None,
            "unknown_reasons": dict(
                Counter(r.unknown_reason for r in runs if r.status == "UNKNOWN")
            ),
            "unknown_reasons_by_family": {
                k: dict(v) for k, v in sorted(fam_reasons.items())
            },
            "route_taken_on_declared": dict(
                Counter(r.route_taken or "NONE" for r in declared)
            ),
            "route_logged_pct": (
                round(
                    sum(1 for r in declared if r.route_taken) / len(declared) * 100, 2
                )
                if declared
                else None
            ),
            "extracted_without_manual_pct": (
                round(len(auto) / len(declared) * 100, 2) if declared else None
            ),
            "events_stored": len(events),
            "events_by_route": dict(Counter(e.extraction_route for e in events)),
            "events_by_source_tier": dict(Counter(e.source_tier for e in events)),
            "events_with_published_components_pct": (
                round(
                    sum(1 for e in events if e.components_reported) / len(events) * 100,
                    2,
                )
                if events
                else None
            ),
            "review_queue_open": sum(1 for r in reviews if r.status == "OPEN"),
            "review_reasons": dict(Counter(r.reason for r in reviews)),
            "coverage_by_family": {k: dict(v) for k, v in sorted(fam.items())},
            "coverage_by_country": {k: dict(v) for k, v in sorted(by_country.items())},
            "crawl_outcomes_by_source": {
                src: dict(c)
                for src, c in _group(
                    crawls, lambda c: c.source_id, lambda c: c.outcome
                ).items()
            },
            "avg_http_requests_per_check": (
                round(sum(r.http_requests for r in runs) / n, 2) if n else None
            ),
            "avg_kb_per_check": (
                round(sum(r.bytes_downloaded for r in runs) / n / 1024, 1)
                if n
                else None
            ),
            "avg_seconds_per_check": round(seconds / n, 2) if n else None,
            "usd_per_compute_hour_assumed": usd_per_compute_hour,
            "avg_compute_cost_usd_per_check": (
                round(seconds / n / 3600 * usd_per_compute_hour, 6) if n else None
            ),
        }

    gold_file = OUT_DIR / "gold_set_evaluation.json"
    if gold_file.exists():
        g = json.loads(gold_file.read_text(encoding="utf-8"))
        tp, fn = g.get("true_positives", 0), g.get("false_negatives", 0)
        report["gold_set"] = {
            "precision_pct": g.get("precision_pct"),
            "recall_pct": g.get("recall_pct"),
            "false_negative_rate_pct": (
                round(fn / (tp + fn) * 100, 2) if (tp + fn) else None
            ),
            "extraction_accuracy_pct": g.get("extraction_accuracy_pct"),
        }
    else:
        report["gold_set"] = (
            "not evaluated yet (python -m src.validators.gold_set_evaluator)"
        )
    return report


def _group(items, key, val) -> dict[str, Counter]:
    out: dict[str, Counter] = defaultdict(Counter)
    for i in items:
        out[key(i)][val(i)] += 1
    return out


def to_markdown(r: dict[str, Any]) -> str:
    lines = ["# Detection report (generated from the database)", ""]
    for k, v in r.items():
        if isinstance(v, dict):
            lines.append(f"## {k}")
            for kk, vv in v.items():
                lines.append(f"- **{kk}**: {vv}")
            lines.append("")
        else:
            lines.append(f"- **{k}**: {v}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hit rate, false negatives, cost per check, coverage"
    )
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--usd-per-compute-hour", type=float, default=0.10)
    args = parser.parse_args()
    r = build_report(args.db_url, args.usd_per_compute_hour)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "detection_report.json").write_text(
        json.dumps(r, indent=2, default=str), encoding="utf-8"
    )
    (OUT_DIR / "detection_report.md").write_text(to_markdown(r), encoding="utf-8")
    print(to_markdown(r))


if __name__ == "__main__":
    main()
