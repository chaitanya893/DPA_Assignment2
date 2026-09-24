"""Database population (PDF Phase 3: "populated with 24 months of history for 100 funds").

History is produced by the real pipeline only: Layer A detection -> Layer B extraction ->
validation gate -> database, one calendar-month window at a time, with every stored number
linked to the raw source document it came from.

Nothing is imported from ``config/*_distribution_schedules.json``: those hand-copied rows have
no stored source document, and loading them would break the provenance rule.

Usage (needs internet access and DETECTOR_CONTACT_EMAIL set):
    python -m src.database.populator                 # reference data + 24-month backfill, all funds
    python -m src.database.populator --reference-only
    python -m src.database.populator --fund-id US_VANGUARD_BND
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timezone
from typing import Any

from src.http_client import HTTPClient
from src.logging_setup import configure_logging
from src.pipeline import DistributionPipeline
from src.sweep_scheduler import SweepConfig, SweepScheduler
from src.universe_loader import UniverseRegistry

logger = logging.getLogger(__name__)


class DatabasePopulator:
    """Creates the schema, reference data and the 24-month history via the pipeline."""

    def __init__(
        self,
        db_url: str | None = None,
        http_client: HTTPClient | None = None,
        universe: UniverseRegistry | None = None,
        strategies_factory: Any = None,
    ) -> None:
        self.pipeline = DistributionPipeline(
            db_url=db_url,
            universe=universe,
            http_client=http_client,
            strategies_factory=strategies_factory,
        )
        self.engine = self.pipeline.engine
        self.universe = self.pipeline.universe

    def populate_universe_and_sources(self) -> dict[str, int]:
        return self.pipeline.ensure_reference_data()

    def populate_24month_distribution_history(
        self,
        as_of: date | None = None,
        fund_ids: list[str] | None = None,
        backfill_months: int = 24,
    ) -> dict[str, Any]:
        """Backfill ``backfill_months`` of history through the full pipeline."""
        today = as_of or datetime.now(timezone.utc).date()
        cfg = SweepConfig.load()
        cfg.backfill_months = backfill_months
        results = SweepScheduler(self.pipeline, config=cfg).run(
            today, fund_ids=fund_ids, backfill_all=True
        )
        summary: dict[str, Any] = {
            "status": "COMPLETED",
            "checks_run": len(results),
            "declared": sum(1 for r in results if r.get("status") == "DECLARED"),
            "not_declared": sum(
                1 for r in results if r.get("status") == "NOT_DECLARED"
            ),
            "unknown": sum(1 for r in results if r.get("status") == "UNKNOWN"),
            "total_events_inserted": sum(r.get("events_inserted", 0) for r in results),
            "sent_to_review": sum(r.get("sent_to_review", 0) for r in results),
        }
        logger.info("24-month population summary: %s", summary)
        return summary


def run_population(
    db_url: str | None = None,
    reference_only: bool = False,
    fund_ids: list[str] | None = None,
) -> dict[str, Any]:
    populator = DatabasePopulator(db_url=db_url)
    meta = populator.populate_universe_and_sources()
    if reference_only:
        return meta
    return {
        **meta,
        **populator.populate_24month_distribution_history(fund_ids=fund_ids),
    }


if __name__ == "__main__":
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Create schema and populate 24 months of history"
    )
    parser.add_argument("--db-url", default=None)
    parser.add_argument(
        "--reference-only",
        action="store_true",
        help="Only funds, share classes and sources",
    )
    parser.add_argument("--fund-id", action="append", default=None)
    args = parser.parse_args()
    res = run_population(args.db_url, args.reference_only, args.fund_id)
    print("=" * 75)
    print("          PHASE 3 DATABASE POPULATION SUMMARY")
    print("=" * 75)
    for k, v in res.items():
        print(f"  * {k:<38}: {v}")
    print("=" * 75)
