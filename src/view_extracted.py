"""Show the distribution events stored by the pipeline, with the source document of each.

Reads the database (not a pre-computed JSON file), so what you see is exactly what Layer B
extracted and the validation gate accepted.

    python -m src.view_extracted [--limit 50]
"""

from __future__ import annotations

import argparse

from sqlalchemy import select

from src.database.connection import get_engine, session_scope
from src.database.models import DistributionEvent, EventEvidence, RawDocument


def display_extracted(limit: int = 50, db_url: str | None = None) -> None:
    engine = get_engine(db_url)
    with session_scope(engine) as s:
        events = list(
            s.scalars(
                select(DistributionEvent)
                .where(DistributionEvent.is_superseded.is_(False))
                .order_by(DistributionEvent.ex_date.desc())
                .limit(limit)
            ).all()
        )
        print("=" * 140)
        print(
            f"STORED DISTRIBUTION EVENTS (latest {len(events)}, current versions only)"
        )
        print("=" * 140)
        print(
            f"{'Fund ID':<22} | {'Ex-Date':<10} | {'Pay Date':<10} | {'Gross':<16} | {'Category':<22} | {'Route':<10} | Source document"
        )
        print("-" * 140)
        for ev in events:
            link = s.scalars(
                select(EventEvidence).where(EventEvidence.event_id == ev.event_id)
            ).first()
            doc = s.get(RawDocument, link.doc_id) if link else None
            print(
                f"{ev.fund_id:<22} | {ev.ex_date!s:<10} | {ev.payable_date or 'N/A'!s:<10} | "
                f"{float(ev.gross_amount):.6f} {ev.currency:<3} | {ev.distribution_category:<22} | "
                f"{ev.extraction_route:<10} | {doc.source_url if doc else 'MISSING'} ({doc.sha256[:10] if doc else '-'})"
            )
        if not events:
            print("No events stored yet. Run: python -m src.database.populator")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--db-url", default=None)
    args = parser.parse_args()
    display_extracted(args.limit, args.db_url)


if __name__ == "__main__":
    main()
