"""End-to-end tests: Layer A -> Layer B -> validation gate -> database, and the backfill/gap
scheduler. All HTTP is served from SYNTHETIC offline fixtures."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from src.database.connection import get_engine, session_scope
from src.database.models import (
    CrawlLog,
    DetectionRun,
    DistributionEvent,
    EventEvidence,
    FundDetectionState,
    RawDocument,
    ReviewQueue,
)
from src.pipeline import DistributionPipeline
from src.sweep_scheduler import SweepConfig, SweepScheduler, decide
from src.universe_loader import UniverseRegistry
from tests.fakes import StubHTTPClient

EMPTY_SUBMISSIONS = json.dumps(
    {
        "filings": {
            "recent": {
                "form": [],
                "filingDate": [],
                "accessionNumber": [],
                "primaryDocument": [],
                "primaryDocDescription": [],
            }
        }
    }
)


def _vti_page(extra_rows: str = "") -> str:
    return (
        "<html><body><h1>Vanguard Total Stock Market ETF (VTI) - SYNTHETIC</h1><table>"
        "<tr><th>Ex-Dividend Date</th><th>Record Date</th><th>Payable Date</th><th>Amount</th></tr>"
        "<tr><td>2023-12-20</td><td>2023-12-21</td><td>2023-12-26</td><td>$1.0000</td></tr>"
        "<tr><td>2024-03-22</td><td>2024-03-22</td><td>2024-03-26</td><td>$0.9000</td></tr>"
        "<tr><td>2024-06-28</td><td>2024-06-28</td><td>2024-07-02</td><td>$0.8000</td></tr>"
        + extra_rows
        + "</table></body></html>"
    )


@pytest.fixture(scope="module")
def uni() -> UniverseRegistry:
    return UniverseRegistry.from_json()


@pytest.fixture
def pipeline(tmp_path, uni):
    def make(page: str) -> DistributionPipeline:
        fund = uni.get_fund("US_VANGUARD_VTI")
        client = StubHTTPClient(
            {
                "data.sec.gov/submissions": EMPTY_SUBMISSIONS,
                fund.official_source_url: page,
            }
        )
        p = DistributionPipeline(
            engine=get_engine(f"sqlite:///{tmp_path / 'p.db'}"),
            universe=uni,
            http_client=client,
        )
        p.ensure_reference_data()
        return p

    return make


def _count(engine, model) -> int:
    with session_scope(engine) as s:
        return s.scalar(select(func.count()).select_from(model)) or 0


def test_declared_check_stores_event_with_real_provenance(pipeline, uni) -> None:
    p = pipeline(_vti_page())
    summary = p.check("US_VANGUARD_VTI", date(2024, 3, 1), date(2024, 3, 31))
    assert (summary.status, summary.route_taken, summary.events_inserted) == (
        "DECLARED",
        "HTML_TABLE",
        1,
    )

    with session_scope(p.engine) as s:
        ev = s.scalars(select(DistributionEvent)).one()
        assert (ev.ex_date, float(ev.gross_amount), ev.extraction_route) == (
            date(2024, 3, 22),
            0.9,
            "HTML_TABLE",
        )
        assert ev.components_reported is False  # nothing invented
        link = s.scalars(
            select(EventEvidence).where(EventEvidence.event_id == ev.event_id)
        ).one()
        doc = s.get(RawDocument, link.doc_id)
        assert doc.source_url == uni.get_fund("US_VANGUARD_VTI").official_source_url
        assert "<table>" in doc.raw_text  # the actual page, not a generated sentence
        run = s.scalars(select(DetectionRun)).one()
        assert (run.status, run.route_taken, run.http_requests) == (
            "DECLARED",
            "HTML_TABLE",
            2,
        )
        assert s.scalar(select(func.count()).select_from(CrawlLog)) == 2
        state = s.get(FundDetectionState, "US_VANGUARD_VTI")
        assert (
            state.last_confirmed_event_date == date(2024, 3, 22)
            and state.consecutive_unknowns == 0
        )


def test_rerun_same_day_is_idempotent(pipeline) -> None:
    p = pipeline(_vti_page())
    for _ in range(3):
        p.check("US_VANGUARD_VTI", date(2024, 3, 1), date(2024, 3, 31))
    assert _count(p.engine, DistributionEvent) == 1
    assert _count(p.engine, EventEvidence) == 1
    assert (
        _count(p.engine, RawDocument) == 2
    )  # SEC JSON + sponsor page, stored once each


def test_not_declared_month_stores_nothing_but_logs_the_check(pipeline) -> None:
    p = pipeline(_vti_page())
    s = p.check("US_VANGUARD_VTI", date(2024, 4, 1), date(2024, 4, 30))
    assert s.status == "NOT_DECLARED" and s.events_inserted == 0
    assert _count(p.engine, DistributionEvent) == 0
    assert _count(p.engine, DetectionRun) == 1


def test_failed_validation_goes_to_review_not_database(pipeline) -> None:
    """PDF: any figure that fails validation goes to review, not to the database."""
    bad = "<tr><td>2024-09-27</td><td>2024-09-27</td><td>2024-09-01</td><td>$0.8872</td></tr>"  # paid before ex-date
    p = pipeline(_vti_page(bad))
    s = p.check("US_VANGUARD_VTI", date(2024, 9, 1), date(2024, 9, 30))
    assert s.status == "DECLARED" and s.events_inserted == 0 and s.sent_to_review == 1
    with session_scope(p.engine) as sess:
        item = sess.scalars(select(ReviewQueue)).one()
        assert (
            item.reason == "VALIDATION_FAILED"
            and "DATE_ORDERING_SANITY" in item.details
        )
    assert _count(p.engine, DistributionEvent) == 0


def test_declared_without_extractable_figures_goes_to_manual_review(
    pipeline, uni
) -> None:
    page = "<html>VTI: Vanguard announced a distribution. Declaration Date: March 20, 2024.</html>"
    p = pipeline(page)
    s = p.check("US_VANGUARD_VTI", date(2024, 3, 1), date(2024, 3, 31))
    assert (
        s.status == "DECLARED" and s.route_taken == "MANUAL" and s.sent_to_review == 1
    )
    assert _count(p.engine, DistributionEvent) == 0


def test_unknown_increments_consecutive_unknowns(tmp_path, uni) -> None:
    client = StubHTTPClient({})  # every source 404 -> nothing covers the window
    p = DistributionPipeline(
        engine=get_engine(f"sqlite:///{tmp_path / 'u.db'}"),
        universe=uni,
        http_client=client,
    )
    p.ensure_reference_data()
    for _ in range(3):
        p.check("US_VANGUARD_VTI", date(2024, 2, 1), date(2024, 2, 29))
    with session_scope(p.engine) as s:
        assert s.get(FundDetectionState, "US_VANGUARD_VTI").consecutive_unknowns == 3


# --------------------------------------------------------------------------- gap logic rules
class _State:
    def __init__(self, **kw) -> None:
        self.last_confirmed_event_date = kw.get("last")
        self.last_checked_at = kw.get("checked")
        self.consecutive_unknowns = kw.get("unknowns", 0)
        self.backfill_completed_at = kw.get(
            "backfilled", datetime(2026, 1, 1, tzinfo=timezone.utc)
        )


def test_new_fund_gets_24_month_backfill(uni) -> None:
    d = decide(uni.get_fund("US_VANGUARD_BND"), None, date(2026, 9, 24), SweepConfig())
    assert (
        d.mode == "BACKFILL"
        and d.window_start == date(2024, 9, 24)
        and len(d.month_windows()) == 25
    )


def test_gap_beyond_1_5x_interval_triggers_lookback(uni) -> None:
    bnd = uni.get_fund("US_VANGUARD_BND")  # monthly: 1.5 x 30 = 45 days
    today = date(2026, 9, 15)
    quiet = decide(
        bnd,
        _State(
            last=today - timedelta(days=40),
            checked=datetime(2026, 9, 14, tzinfo=timezone.utc),
        ),
        today,
        SweepConfig(),
    )
    assert quiet.mode == "ROUTINE"
    late = decide(
        bnd,
        _State(
            last=today - timedelta(days=46),
            checked=datetime(2026, 9, 14, tzinfo=timezone.utc),
        ),
        today,
        SweepConfig(),
    )
    assert late.mode == "LOOKBACK" and late.window_start == date(2026, 6, 15)
    assert any("1.5 x 30" in r for r in late.reasons)


def test_more_than_two_unknowns_triggers_lookback(uni) -> None:
    vti = uni.get_fund("US_VANGUARD_VTI")
    today = date(2026, 9, 15)
    st = _State(
        last=date(2026, 8, 20),
        checked=datetime(2026, 9, 14, tzinfo=timezone.utc),
        unknowns=3,
    )
    d = decide(vti, st, today, SweepConfig())
    assert d.mode == "LOOKBACK" and any("UNKNOWN 3 times" in r for r in d.reasons)
    st.consecutive_unknowns = 2
    assert decide(vti, st, today, SweepConfig()).mode == "ROUTINE"


def test_month_end_and_year_end_always_sweep(uni) -> None:
    vti = uni.get_fund("US_VANGUARD_VTI")
    st = _State(
        last=date(2026, 9, 20), checked=datetime(2026, 9, 28, tzinfo=timezone.utc)
    )
    assert (
        decide(vti, st, date(2026, 9, 29), SweepConfig()).mode == "LOOKBACK"
    )  # month-end
    st2 = _State(
        last=date(2026, 12, 1), checked=datetime(2026, 12, 9, tzinfo=timezone.utc)
    )
    d = decide(vti, st2, date(2026, 12, 10), SweepConfig())
    assert d.mode == "LOOKBACK" and any("year-end" in r for r in d.reasons)


def test_lookback_months_is_configurable(tmp_path, uni) -> None:
    cfg_file = tmp_path / "sweep.yaml"
    cfg_file.write_text("lookback_months: 6\n", encoding="utf-8")
    cfg = SweepConfig.load(cfg_file)
    st = _State(last=None, checked=datetime(2026, 9, 14, tzinfo=timezone.utc))
    d = decide(uni.get_fund("US_VANGUARD_VTI"), st, date(2026, 9, 15), cfg)
    assert d.window_start == date(2026, 3, 15)


def test_backfill_then_routine_gate_end_to_end(tmp_path, uni) -> None:
    fund = uni.get_fund("US_VANGUARD_VTI")
    client = StubHTTPClient(
        {
            "data.sec.gov/submissions": EMPTY_SUBMISSIONS,
            fund.official_source_url: _vti_page(),
        }
    )
    p = DistributionPipeline(
        engine=get_engine(f"sqlite:///{tmp_path / 's.db'}"),
        universe=uni,
        http_client=client,
    )
    cfg = SweepConfig(backfill_months=9)
    sched = SweepScheduler(p, config=cfg)

    results = sched.run(date(2024, 8, 5), fund_ids=["US_VANGUARD_VTI"])
    statuses = {(r["window_start"].month): r["status"] for r in results}
    assert (
        statuses[3] == "DECLARED"
        and statuses[6] == "DECLARED"
        and statuses[2] == "NOT_DECLARED"
    )
    assert (
        _count(p.engine, DistributionEvent) == 3
    )  # Dec 2023, Mar 2024, Jun 2024 (window starts 2023-11-05)
    with session_scope(p.engine) as s:
        assert (
            s.get(FundDetectionState, "US_VANGUARD_VTI").backfill_completed_at
            is not None
        )

    # Next day, off-cadence, page unchanged, no EDGAR filing -> the cheap gate skips the full check.
    nxt = sched.run(date(2024, 8, 6), fund_ids=["US_VANGUARD_VTI"])
    assert nxt == [
        {"fund_id": "US_VANGUARD_VTI", "status": "SKIPPED", "reason": nxt[0]["reason"]}
    ]
    assert "off cadence" in nxt[0]["reason"]
