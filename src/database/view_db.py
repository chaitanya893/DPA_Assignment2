"""Interactive CLI inspection tool for the Fund Distribution Database.

Displays summary metrics, row counts across all 9 tables, sample events,
provenance links, and data quality flags.
"""

from __future__ import annotations

import argparse
from sqlalchemy import func, select

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


def display_sources(session) -> None:
    """Print all registered source endpoints."""
    stmt = select(SourceRegistry).order_by(SourceRegistry.source_tier, SourceRegistry.source_id)
    sources = session.scalars(stmt).all()
    print("\n" + "=" * 110)
    print("                    REGISTERED SOURCE ENDPOINTS (source_registry: 8 Endpoints)")
    print("=" * 110)
    print(f"{'#':<3} | {'Source ID':<30} | {'Tier':<6} | {'Source Name':<40} | {'URL Pattern'}")
    print("-" * 110)
    for idx, s in enumerate(sources, 1):
        print(f"{idx:<3} | {s.source_id:<30} | Tier {s.source_tier:<1} | {s.source_name:<40} | {s.url_pattern}")
    print("=" * 110 + "\n")


def display_raw_documents(session, limit: int = 50) -> None:
    """Print stored raw document cryptographic artifacts."""
    stmt = select(RawDocument).order_by(RawDocument.created_at.desc()).limit(limit)
    docs = session.scalars(stmt).all()
    print("\n" + "=" * 110)
    print(f"               RAW DOCUMENTS ARTIFACTS (raw_document: Showing {len(docs)} Documents)")
    print("=" * 110)
    print(f"{'#':<3} | {'Doc ID':<22} | {'SHA-256 (First 20 chars)':<24} | {'Size':<8} | {'Source URL'}")
    print("-" * 110)
    for idx, d in enumerate(docs, 1):
        sha_short = d.sha256[:20] + "..."
        size_str = f"{d.byte_size} B"
        print(f"{idx:<3} | {d.doc_id:<22} | {sha_short:<24} | {size_str:<8} | {d.source_url}")
    print("=" * 110 + "\n")


def display_all_events(session) -> None:
    """Print all 72 24-month distribution events."""
    stmt = select(DistributionEvent).order_by(DistributionEvent.fund_id, DistributionEvent.ex_date.desc())
    events = session.scalars(stmt).all()
    print("\n" + "=" * 110)
    print(f"               ALL 24-MONTH DISTRIBUTION EVENTS (distribution_event: Total {len(events)} Events)")
    print("=" * 110)
    print(f"{'#':<3} | {'Fund ID':<20} | {'Ex-Date':<10} | {'Pay Date':<10} | {'Gross Amount':<14} | {'Est/Fin':<9} | {'Route':<11} | {'Ver'}")
    print("-" * 110)
    for idx, ev in enumerate(events, 1):
        amt_str = f"${float(ev.gross_amount):.4f} {ev.currency}"
        pay_str = str(ev.payable_date) if ev.payable_date else "N/A"
        print(
            f"{idx:<3} | {ev.fund_id:<20} | {str(ev.ex_date):<10} | {pay_str:<10} | {amt_str:<14} | {ev.estimated_or_final:<9} | {ev.extraction_route:<11} | v{ev.version}"
        )
    print("=" * 110 + "\n")


def display_database_summary(
    db_url: str | None = None,
    show_sources: bool = False,
    show_raw_docs: bool = False,
    show_all_events: bool = False,
) -> None:
    """Print complete summary of all 9 tables in the database."""
    engine = get_engine(db_url)

    print("=" * 110)
    print("                FUND DISTRIBUTION DATABASE (PHASE 3 AUDIT & SUMMARY)")
    print("=" * 110)
    print(f"Database URL: {engine.url}")
    print("-" * 110)

    with session_scope(engine) as session:
        # Table counts
        fund_count = session.scalar(select(func.count(FundMaster.fund_id))) or 0
        sc_count = session.scalar(select(func.count(ShareClass.class_id))) or 0
        src_count = session.scalar(select(func.count(SourceRegistry.source_id))) or 0
        doc_count = session.scalar(select(func.count(RawDocument.doc_id))) or 0
        crawl_count = session.scalar(select(func.count(CrawlLog.log_id))) or 0
        event_count = session.scalar(select(func.count(DistributionEvent.event_id))) or 0
        comp_count = session.scalar(select(func.count(DistributionComponent.component_id))) or 0
        ev_link_count = session.scalar(select(func.count(EventEvidence.evidence_id))) or 0
        dq_count = session.scalar(select(func.count(DQFlag.flag_id))) or 0

        # Country breakdown
        us_funds = session.scalar(select(func.count(FundMaster.fund_id)).where(FundMaster.country == "US")) or 0
        ca_funds = session.scalar(select(func.count(FundMaster.fund_id)).where(FundMaster.country == "CA")) or 0

        # Frequency breakdown
        monthly_classes = session.scalar(select(func.count(ShareClass.class_id)).where(ShareClass.is_monthly_payer == True)) or 0
        etf_classes = session.scalar(select(func.count(ShareClass.class_id)).where(ShareClass.is_etf == True)) or 0

        print(f"1. Master Funds (fund_master)            : {fund_count} (US: {us_funds}, CA: {ca_funds})")
        print(f"2. Share Classes (share_class)           : {sc_count} (ETFs: {etf_classes}, Monthly Payers: {monthly_classes})")
        print(f"3. Source Endpoints (source_registry)    : {src_count} endpoints")
        print(f"4. Raw Documents (raw_document)          : {doc_count} cryptographic immutable artifacts")
        print(f"5. Crawl Audit Logs (crawl_log)          : {crawl_count} logged attempts")
        print(f"6. Distribution Events (distribution_event): {event_count} 24-month events")
        print(f"7. Tax Components (distribution_component): {comp_count} granular child rows")
        print(f"8. Provenance Links (event_evidence)     : {ev_link_count} verified links")
        print(f"9. Data Quality Flags (dq_flag)          : {dq_count} flags recorded")
        print("-" * 110)

        # Display Top 15 Recent Distribution Events
        print("SAMPLE DISTRIBUTION EVENTS (Latest 15 Events):")
        print(f"{'#':<3} | {'Fund ID':<20} | {'Ex-Date':<10} | {'Pay Date':<10} | {'Gross Amount':<14} | {'Route':<10} | {'Version':<7} | {'Superseded'}")
        print("-" * 110)

        stmt = select(DistributionEvent).order_by(DistributionEvent.ex_date.desc(), DistributionEvent.fund_id).limit(15)
        sample_events = session.scalars(stmt).all()
        for idx, ev in enumerate(sample_events, 1):
            amt_str = f"${float(ev.gross_amount):.4f} {ev.currency}"
            pay_str = str(ev.payable_date) if ev.payable_date else "N/A"
            sup_str = "YES" if ev.is_superseded else "NO"
            print(
                f"{idx:<3} | {ev.fund_id:<20} | {str(ev.ex_date):<10} | {pay_str:<10} | {amt_str:<14} | {ev.extraction_route:<10} | v{ev.version:<6} | {sup_str}"
            )

        print("=" * 110)

        if show_sources:
            display_sources(session)
        if show_raw_docs:
            display_raw_documents(session)
        if show_all_events:
            display_all_events(session)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect the Fund Distribution Database.")
    parser.add_argument("--db-url", type=str, default=None, help="Optional database URL (defaults to SQLite)")
    parser.add_argument("--sources", action="store_true", help="Display all 8 registered source endpoints")
    parser.add_argument("--raw-docs", action="store_true", help="Display all 40 raw document artifacts with SHA-256")
    parser.add_argument("--all-events", action="store_true", help="Display all 72 24-month distribution events")
    parser.add_argument("--all", action="store_true", help="Display everything (sources, raw documents, and all events)")
    args = parser.parse_args()

    show_all = args.all
    display_database_summary(
        db_url=args.db_url,
        show_sources=args.sources or show_all,
        show_raw_docs=args.raw_docs or show_all,
        show_all_events=args.all_events or show_all,
    )


if __name__ == "__main__":
    main()
