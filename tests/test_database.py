"""Comprehensive Test Suite for Phase 3 Relational Database Layer.

Tests:
1. Schema initialization and table creation across all 9 relational entities.
2. Fund master and share class upsert logic.
3. Source registry and immutable crawl audit logging.
4. SHA-256 raw document deduplication and cryptographic provenance.
5. Idempotent distribution event insertion (re-running produces zero duplicates).
6. Non-destructive amendments (version bump, is_superseded=True, superseded_by linking).
7. Estimated vs Final coexistence on same ex_date.
8. Component tax breakdown persistence.
9. Data quality flag logging.
10. End-to-end populator execution and export capability.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.database.connection import get_engine, init_db, session_scope
from src.database.models import (
    Base,
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
from src.database.populator import DatabasePopulator
from src.database.repository import DistributionRepository
from src.models import (
    CAComponentType,
    ExtractedComponent,
    ExtractedDistribution,
    ExtractionRoute,
    SourceTier,
    USComponentType,
)


@pytest.fixture
def in_memory_db():
    """Create a fresh in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    init_db(engine)
    return engine


def test_schema_creates_all_nine_tables(in_memory_db):
    """Verify that all 9 required tables are created in the database schema."""
    table_names = set(Base.metadata.tables.keys())
    expected_tables = {
        "fund_master",
        "share_class",
        "source_registry",
        "crawl_log",
        "raw_document",
        "distribution_event",
        "distribution_component",
        "event_evidence",
        "dq_flag",
    }
    assert expected_tables.issubset(table_names)


def test_fund_and_share_class_upsert(in_memory_db):
    """Test fund master and share class upsert operations."""
    with session_scope(in_memory_db) as session:
        repo = DistributionRepository(session)
        fund = repo.upsert_fund(
            fund_id="TEST_FUND_1",
            fund_name="Test US ETF",
            fund_family="Vanguard",
            country="US",
            fund_type="ETF",
            cik="0001234567",
        )
        assert fund.fund_id == "TEST_FUND_1"

        sc = repo.upsert_share_class(
            class_id="TEST_CLASS_1",
            fund_id="TEST_FUND_1",
            ticker="TUS",
            currency="USD",
            expected_frequency="QUARTERLY",
        )
        assert sc.ticker == "TUS"

        # Re-upsert with updated metadata
        repo.upsert_fund(
            fund_id="TEST_FUND_1",
            fund_name="Test US ETF Updated",
            fund_family="Vanguard",
            country="US",
            fund_type="ETF",
        )
        updated_fund = session.get(FundMaster, "TEST_FUND_1")
        assert updated_fund.fund_name == "Test US ETF Updated"


def test_source_registry_and_crawl_log(in_memory_db):
    """Test source registry and crawl log creation."""
    with session_scope(in_memory_db) as session:
        repo = DistributionRepository(session)
        repo.upsert_source_registry(
            source_id="sec_edgar_test",
            source_name="SEC EDGAR Test Feed",
            source_tier=1,
            url_pattern="https://data.sec.gov/{cik}",
        )
        log = repo.log_crawl_attempt(
            source_id="sec_edgar_test",
            target_url="https://data.sec.gov/123",
            outcome="SUCCESS",
            http_status=200,
            duration_seconds=0.45,
        )
        assert log.outcome == "SUCCESS"
        assert log.http_status == 200


def test_raw_document_sha256_deduplication(in_memory_db):
    """Verify raw document storage deduplicates identical content by SHA-256."""
    content = b"Official dividend press release content."
    expected_hash = hashlib.sha256(content).hexdigest()

    with session_scope(in_memory_db) as session:
        repo = DistributionRepository(session)
        doc1 = repo.store_raw_document(
            source_id="official_fund_sponsor_page",
            source_url="https://sponsor.com/pr",
            content_bytes=content,
        )
        assert doc1.sha256 == expected_hash

        # Store identical content again
        doc2 = repo.store_raw_document(
            source_id="official_fund_sponsor_page",
            source_url="https://sponsor.com/pr2",
            content_bytes=content,
        )
        assert doc1.doc_id == doc2.doc_id


def test_idempotent_distribution_insertion(in_memory_db):
    """Test that inserting identical distribution events does not create duplicates."""
    with session_scope(in_memory_db) as session:
        repo = DistributionRepository(session)
        repo.upsert_fund("US_VTI", "Vanguard Total Stock Market", "Vanguard", "US", "ETF")
        repo.upsert_share_class("US_VTI_CLASS", "US_VTI", ticker="VTI")

        extracted = ExtractedDistribution(
            fund_id="US_VTI",
            country="US",
            currency="USD",
            ex_date=date(2024, 3, 22),
            gross_amount=0.885,
            distribution_type="Income",
            components=[
                ExtractedComponent("Ordinary Income", USComponentType.ORDINARY_INCOME, 0.885, 100.0)
            ],
            source_url="https://investor.vanguard.com/vti",
        )

        # First insert -> is_new = True
        evt1, is_new1 = repo.save_distribution_event(extracted)
        assert is_new1 is True
        assert evt1.version == 1
        assert evt1.is_superseded is False

        # Duplicate insert -> is_new = False
        evt2, is_new2 = repo.save_distribution_event(extracted)
        assert is_new2 is False
        assert evt2.event_id == evt1.event_id

    # Verify total count in database is exactly 1
    with Session(in_memory_db) as session:
        events = list(session.scalars(select(DistributionEvent)).all())
        assert len(events) == 1
        comps = list(session.scalars(select(DistributionComponent)).all())
        assert len(comps) == 1


def test_nondestructive_amendment_versioning(in_memory_db):
    """Test that restated/amended distributions create version 2 and mark version 1 superseded."""
    with session_scope(in_memory_db) as session:
        repo = DistributionRepository(session)
        repo.upsert_fund("US_VTI", "Vanguard Total Stock Market", "Vanguard", "US", "ETF")
        repo.upsert_share_class("US_VTI_CLASS", "US_VTI", ticker="VTI")

        # Initial distribution announcement: $0.800
        extracted_v1 = ExtractedDistribution(
            fund_id="US_VTI",
            country="US",
            currency="USD",
            ex_date=date(2024, 3, 22),
            gross_amount=0.800,
            distribution_type="Income",
            components=[
                ExtractedComponent("Ordinary Income", USComponentType.ORDINARY_INCOME, 0.800, 100.0)
            ],
            source_url="https://investor.vanguard.com/vti",
        )
        evt1, is_new1 = repo.save_distribution_event(extracted_v1)
        assert is_new1 is True
        assert evt1.version == 1

        # Restated distribution amendment: $0.850
        extracted_v2 = ExtractedDistribution(
            fund_id="US_VTI",
            country="US",
            currency="USD",
            ex_date=date(2024, 3, 22),
            gross_amount=0.850,
            distribution_type="Income",
            components=[
                ExtractedComponent("Ordinary Income", USComponentType.ORDINARY_INCOME, 0.850, 100.0)
            ],
            source_url="https://investor.vanguard.com/vti/amendment",
        )
        evt2, is_new2 = repo.save_distribution_event(extracted_v2, is_amendment=True)
        assert is_new2 is True
        assert evt2.version == 2
        assert evt2.is_superseded is False

    # Check database state
    with Session(in_memory_db) as session:
        events = list(
            session.scalars(
                select(DistributionEvent).order_by(DistributionEvent.version.asc())
            ).all()
        )
        assert len(events) == 2
        v1_record, v2_record = events[0], events[1]
        assert v1_record.version == 1
        assert v1_record.is_superseded is True
        assert v1_record.superseded_by == v2_record.event_id
        assert v2_record.version == 2
        assert v2_record.is_superseded is False
        assert v2_record.superseded_by is None


def test_estimated_vs_final_coexistence(in_memory_db):
    """Test that Estimated and Final distributions on same ex_date coexist without collision."""
    with session_scope(in_memory_db) as session:
        repo = DistributionRepository(session)
        repo.upsert_fund("CA_BMO_ZCN", "BMO S&P/TSX Capped Composite", "BMO", "CA", "ETF")
        repo.upsert_share_class("CA_BMO_ZCN_CLASS", "CA_BMO_ZCN", ticker="ZCN")

        # Estimated distribution
        ext_est = ExtractedDistribution(
            fund_id="CA_BMO_ZCN",
            country="CA",
            currency="CAD",
            ex_date=date(2024, 12, 30),
            gross_amount=0.220,
            is_estimated=True,
            components=[
                ExtractedComponent("Eligible Dividend", CAComponentType.ELIGIBLE_DIVIDEND, 0.220, 100.0)
            ],
            source_url="https://bmo.com/est",
        )
        evt_est, _ = repo.save_distribution_event(ext_est)
        assert evt_est.estimated_or_final == "ESTIMATED"

        # Final distribution on same date with actual confirmed amount
        ext_fin = ExtractedDistribution(
            fund_id="CA_BMO_ZCN",
            country="CA",
            currency="CAD",
            ex_date=date(2024, 12, 30),
            gross_amount=0.230,
            is_estimated=False,
            components=[
                ExtractedComponent("Eligible Dividend", CAComponentType.ELIGIBLE_DIVIDEND, 0.230, 100.0)
            ],
            source_url="https://bmo.com/final",
        )
        evt_fin, _ = repo.save_distribution_event(ext_fin)
        assert evt_fin.estimated_or_final == "FINAL"

    # Both must exist simultaneously and neither should be marked superseded
    with Session(in_memory_db) as session:
        events = list(session.scalars(select(DistributionEvent)).all())
        assert len(events) == 2
        types = {e.estimated_or_final for e in events}
        assert types == {"ESTIMATED", "FINAL"}
        assert all(not e.is_superseded for e in events)


def test_dq_flag_logging(in_memory_db):
    """Test logging and retrieval of DQ flags."""
    with session_scope(in_memory_db) as session:
        repo = DistributionRepository(session)
        flag = repo.log_dq_flag(
            fund_id="CA_BMO_ZCN",
            rule_name="NEGATIVE_DISTRIBUTION_AMOUNT",
            severity="CRITICAL",
            message="Extracted distribution amount is less than or equal to zero.",
        )
        assert flag.rule_name == "NEGATIVE_DISTRIBUTION_AMOUNT"
        assert flag.severity == "CRITICAL"


def test_populator_in_memory_execution():
    """Test DatabasePopulator on in-memory database."""
    populator = DatabasePopulator(db_url="sqlite:///:memory:")
    res = populator.populate_24month_distribution_history()
    assert res["status"] == "COMPLETED"
    assert res["total_events_inserted"] > 0
    assert res["total_funds_with_history"] > 0
