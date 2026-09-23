"""Export all 9 tables of the database to CSV and Excel format."""

from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd
from sqlalchemy import select

from src.database.connection import get_engine, session_scope
from src.database.models import (
    CrawlLog,
    DistributionComponent,
    DistributionEvent,
    DQFlag,
    EventEvidence,
    FundMaster,
    RawDocument,
    ShareClass,
    SourceRegistry,
)


def export_database_tables(out_dir: str = "data/exports", db_url: str | None = None) -> None:
    """Export all database tables to CSV and Excel."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    engine = get_engine(db_url)

    tables = [
        ("fund_master", FundMaster),
        ("share_class", ShareClass),
        ("source_registry", SourceRegistry),
        ("crawl_log", CrawlLog),
        ("raw_document", RawDocument),
        ("distribution_event", DistributionEvent),
        ("distribution_component", DistributionComponent),
        ("event_evidence", EventEvidence),
        ("dq_flag", DQFlag),
    ]

    excel_file = out_path / "fund_distributions_complete_export.xlsx"
    with pd.ExcelWriter(excel_file, engine="openpyxl") as writer:
        with session_scope(engine) as session:
            for tbl_name, model_cls in tables:
                stmt = select(model_cls)
                records = session.scalars(stmt).all()
                data = [
                    {col.name: getattr(r, col.name) for col in model_cls.__table__.columns}
                    for r in records
                ]
                df = pd.DataFrame(data)
                
                # Write individual CSV
                csv_file = out_path / f"{tbl_name}.csv"
                df.to_csv(csv_file, index=False)
                
                # Write sheet in Excel workbook
                df.to_excel(writer, sheet_name=tbl_name[:31], index=False)
                print(f"Exported {len(df):>4} rows -> {csv_file.name}")

    print(f"\nAll 9 tables exported to Excel workbook: {excel_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export database tables to CSV and Excel.")
    parser.add_argument("--out-dir", type=str, default="data/exports", help="Output directory")
    parser.add_argument("--db-url", type=str, default=None, help="Optional database URL")
    args = parser.parse_args()
    export_database_tables(args.out_dir, args.db_url)


if __name__ == "__main__":
    main()
