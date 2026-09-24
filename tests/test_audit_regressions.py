"""Regression tests for every bug found in the Assignment 2 audit.

Each test reproduces a failure that existed before the fix. All pages below are SYNTHETIC
offline fixtures, not real fund data.
"""

from __future__ import annotations

import io
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from src.detector import detect_distribution
from src.models import DetectionStatus, ExtractionRoute, UnknownReason
from src.parsers.filing_parser import parse_sec_rule_19a1_filing
from src.parsers.html_table_parser import parse_html_distribution_tables
from src.strategies import (
    CanadianRegulatoryStrategy,
    OfficialSponsorWebStrategy,
    TargetedLookupStrategy,
    VerifiedScheduleStrategy,
)
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
VTI_TABLE = """<html><body><h1>Vanguard Total Stock Market ETF (VTI)</h1><table>
<tr><th>Ex-Dividend Date</th><th>Record Date</th><th>Payable Date</th><th>Amount</th></tr>
<tr><td>2023-12-20</td><td>2023-12-21</td><td>2023-12-26</td><td>$1.0000</td></tr>
<tr><td>2024-03-22</td><td>2024-03-22</td><td>2024-03-26</td><td>$0.9000</td></tr>
<tr><td>2024-06-28</td><td>2024-06-28</td><td>2024-07-02</td><td>$0.8000</td></tr>
</table></body></html>"""


@pytest.fixture(scope="module")
def uni() -> UniverseRegistry:
    return UniverseRegistry.from_json()


# --------------------------------------------------------------------------- Layer A
def test_not_declared_reachable_with_default_strategies_us(uni) -> None:
    """Bug: SEC/Canadian 'not applicable' reasons blocked NOT_DECLARED forever (VTI Feb -> UNKNOWN)."""
    fund = uni.get_fund("US_VANGUARD_VTI")
    client = StubHTTPClient(
        {
            "data.sec.gov/submissions": EMPTY_SUBMISSIONS,
            fund.official_source_url: VTI_TABLE,
        }
    )
    r = detect_distribution(
        fund.fund_id,
        date(2024, 2, 1),
        date(2024, 2, 29),
        http_client=client,
        universe=uni,
    )
    assert r.status == DetectionStatus.NOT_DECLARED
    assert r.confidence == 0.85
    r2 = detect_distribution(
        fund.fund_id,
        date(2024, 3, 1),
        date(2024, 3, 31),
        http_client=client,
        universe=uni,
    )
    assert r2.status == DetectionStatus.DECLARED


def test_outage_of_a_primary_source_never_yields_not_declared(uni) -> None:
    """PDF: never return NOT_DECLARED when a source could not be reached."""
    fund = uni.get_fund("US_VANGUARD_VTI")
    client = StubHTTPClient({fund.official_source_url: VTI_TABLE})  # SEC -> 404 below

    class Down(StubHTTPClient):
        def get(self, url, headers=None, params=None):  # noqa: ANN001
            rec = super().get(url, headers, params)
            if "sec.gov" in url:
                from dataclasses import replace

                return replace(
                    rec,
                    status_code=503,
                    is_success=False,
                    failure_reason=UnknownReason.SOURCE_UNAVAILABLE,
                )
            return rec

    down = Down(client.pages)
    r = detect_distribution(
        fund.fund_id,
        date(2024, 2, 1),
        date(2024, 2, 29),
        http_client=down,
        universe=uni,
    )
    assert r.status == DetectionStatus.UNKNOWN
    assert r.unknown_reason == UnknownReason.SOURCE_UNAVAILABLE


def test_detector_does_not_read_config_or_gold_set(uni) -> None:
    """Bug: VerifiedScheduleStrategy loaded config schedules and gold_set_300.json as evidence."""
    obs = VerifiedScheduleStrategy(universe=uni).inspect(
        "US_ISHARES_HYG", date(2024, 5, 1), date(2024, 5, 31)
    )
    assert obs.applicable is False and not obs.evidence


def test_canadian_page_without_dated_declaration_is_not_declared_today(uni) -> None:
    """Bug: 'monthly distribution' text on a TMX page became a declaration dated today."""
    fund = uni.get_fund("CA_BMO_ZAG")
    page = f"<html>{fund.ticker} BMO Aggregate Bond. Monthly distribution history and fund facts.</html>"
    today = datetime.now(timezone.utc).date()
    obs = CanadianRegulatoryStrategy(
        http_client=StubHTTPClient({"tmx.com": page}), universe=uni
    ).inspect(fund.fund_id, today.replace(day=1), today)
    assert obs.has_declaration_in_window is False


def test_nav_as_of_row_is_not_a_distribution(uni) -> None:
    """Bug: 'NAV as of 03/15/2024 | $250.12' was detected as a declaration."""
    fund = uni.get_fund("US_VANGUARD_VTI")
    page = "<html>VTI<table><tr><td>NAV as of 03/15/2024</td><td>$250.12</td></tr></table></html>"
    obs = OfficialSponsorWebStrategy(
        http_client=StubHTTPClient({fund.official_source_url: page}), universe=uni
    ).inspect(fund.fund_id, date(2024, 3, 1), date(2024, 3, 31))
    assert obs.has_declaration_in_window is False


def test_targeted_lookup_positive_path_does_not_crash(uni) -> None:
    """Bug: ExtractionRoute.WEB_PORTAL does not exist -> AttributeError when a declaration was found."""
    page = "<html>Vanguard VTI declared a quarterly dividend. Declaration Date: March 20, 2024. Ex-Date: March 22, 2024.</html>"
    obs = TargetedLookupStrategy(
        candidate_urls=["https://example.test/pr"],
        http_client=StubHTTPClient({"example.test": page}),
        universe=uni,
    ).inspect("US_VANGUARD_VTI", date(2024, 3, 1), date(2024, 3, 31))
    assert obs.has_declaration_in_window is True
    assert obs.suggested_route == ExtractionRoute.FILING


def test_record_or_payable_date_alone_does_not_declare(uni) -> None:
    """Only declaration / ex / publication dates place a distribution in a window."""
    fund = uni.get_fund("US_VANGUARD_VTI")
    obs = OfficialSponsorWebStrategy(
        http_client=StubHTTPClient({fund.official_source_url: VTI_TABLE}), universe=uni
    ).inspect(
        fund.fund_id,
        date(2024, 7, 1),
        date(2024, 7, 31),  # only the payable date (07-02) is in July
    )
    assert obs.has_declaration_in_window is False


# --------------------------------------------------------------------------- Layer B parsers
def test_date_column_is_never_read_as_amount() -> None:
    html = (
        "<table><tr><th>Distribution Date</th><th>Ex-Date</th><th>Amount</th></tr>"
        "<tr><td>2024-03-27</td><td>2024-03-22</td><td>$0.9168</td></tr></table>"
    )
    assert [
        e.gross_amount for e in parse_html_distribution_tables(html, "F", "US", "u")
    ] == [0.9168]


def test_impossible_date_does_not_crash() -> None:
    html = "<table><tr><th>Ex-Date</th><th>Amount</th></tr><tr><td>February 30, 2024</td><td>$0.10</td></tr></table>"
    assert parse_html_distribution_tables(html, "F", "US", "u") == []


def test_ex_date_is_never_copied_from_payable_date() -> None:
    html = "<table><tr><th>Payable Date</th><th>Amount</th></tr><tr><td>2024-03-27</td><td>$0.10</td></tr></table>"
    assert parse_html_distribution_tables(html, "F", "US", "u") == []


def test_filing_parser_uses_labelled_ex_date_not_first_date() -> None:
    text = (
        "Annual report for fiscal year ended 2023-10-31. Distribution per share: $0.2500. "
        "Ex-Date: 2024-03-22. Payable Date: March 27, 2024. Return of capital $0.0500"
    )
    ev = parse_sec_rule_19a1_filing(text, "X", "u", filing_date=date(2024, 3, 20))[0]
    assert (ev.ex_date, ev.payable_date, ev.declaration_date) == (
        date(2024, 3, 22),
        date(2024, 3, 27),
        None,
    )
    assert (
        parse_sec_rule_19a1_filing(
            "fiscal year ended 2023-10-31 distribution per share $0.25", "X", "u"
        )
        == []
    )


def test_unclassified_capital_gain_is_not_labelled_long_term() -> None:
    text = (
        "Ex-Date: 2024-12-15. Total distribution per share $0.50. Capital gains $0.50"
    )
    ev = parse_sec_rule_19a1_filing(text, "X", "u")[0]
    assert ev.components[0].component_type.value == "CAPITAL_GAIN_UNCLASSIFIED"


def test_excel_schedule_route() -> None:
    openpyxl = pytest.importorskip("openpyxl")
    from src.parsers.pdf_parser import parse_excel_distribution_document

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Fund", "Ex-Date", "Record Date", "Pay Date", "Amount"])
    ws.append(["ZAG", "2024-11-27", "2024-11-27", "2024-12-03", 0.045])
    buf = io.BytesIO()
    wb.save(buf)
    events = parse_excel_distribution_document(
        buf.getvalue(), "CA_BMO_ZAG", "CA", "https://x.test/s.xlsx"
    )
    assert [(e.ex_date, e.gross_amount, e.currency) for e in events] == [
        (date(2024, 11, 27), 0.045, "CAD")
    ]


def test_pdf_text_layer_schedule_route() -> None:
    canvas_mod = pytest.importorskip("reportlab.pdfgen.canvas")
    from src.parsers.pdf_parser import parse_pdf_distribution_document

    buf = io.BytesIO()
    c = canvas_mod.Canvas(buf)
    c.drawString(50, 800, "Ex-Dividend Date   Record Date   Payable Date   Amount")
    c.drawString(50, 780, "2024-06-21   2024-06-21   2024-06-26   $0.8800")
    c.save()
    events = parse_pdf_distribution_document(
        buf.getvalue(), "US_VANGUARD_VYM", "US", "https://x.test/s.pdf"
    )
    assert [(e.ex_date, e.gross_amount, e.extraction_route) for e in events] == [
        (date(2024, 6, 21), 0.88, ExtractionRoute.PDF)
    ]


# --------------------------------------------------------------------------- EDGAR index
def test_edgar_daily_index_parser_and_poller() -> None:
    from src.edgar_index import EdgarDailyIndexPoller, daily_index_url, parse_form_index

    idx = """Description:           Daily Index of EDGAR Dissemination Feed by Form Type
Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
497         VANGUARD INDEX FUNDS                                          36405       20240320    edgar/data/36405/0000932471-24-000001.txt
10-K        SOMETHING ELSE INC                                            1234567     20240320    edgar/data/1234567/0001234567-24-000002.txt
"""
    filings = parse_form_index(idx)
    assert [(f.form, f.cik) for f in filings] == [("497", "36405"), ("10-K", "1234567")]
    day = date(2024, 3, 20)
    poller = EdgarDailyIndexPoller(StubHTTPClient({daily_index_url(day): idx}))
    hits, ok = poller.poll_day(day, {"0000036405"})
    assert ok and [h.form for h in hits] == ["497"]
    assert poller.poll_day(date(2024, 3, 23), {"36405"}) == ([], False)  # Saturday


def test_calendar_cadence_helper(uni) -> None:
    from src.strategies import CalendarExpectationStrategy

    vti = uni.get_fund("US_VANGUARD_VTI")
    assert (
        CalendarExpectationStrategy.expected_in_window(
            vti, date(2024, 3, 1), date(2024, 3, 31)
        )[0]
        is True
    )
    assert (
        CalendarExpectationStrategy.expected_in_window(
            vti, date(2024, 2, 1), date(2024, 2, 29)
        )[0]
        is False
    )


def test_change_detection_reports_hash_change(uni) -> None:
    from src.strategies import ChangeDetectionStrategy

    fund = uni.get_fund("US_VANGUARD_VTI")
    client = StubHTTPClient({fund.official_source_url: VTI_TABLE})
    same = ChangeDetectionStrategy(
        http_client=client, universe=uni, previous_hash_lookup=lambda u: None
    ).check(fund)
    assert same[0] is None and same[1]
    changed = ChangeDetectionStrategy(
        http_client=client, universe=uni, previous_hash_lookup=lambda u: "old"
    ).check(fund)
    assert changed[0] is True
    unchanged = ChangeDetectionStrategy(
        http_client=client, universe=uni, previous_hash_lookup=lambda u: same[1]
    ).check(fund)
    assert unchanged[0] is False


def test_window_helpers() -> None:
    from src.sweep_scheduler import add_months, month_windows

    assert add_months(date(2026, 3, 31), -1) == date(2026, 2, 28)
    wins = month_windows(date(2024, 1, 15), date(2024, 3, 10))
    assert wins == [
        (date(2024, 1, 15), date(2024, 1, 31)),
        (date(2024, 2, 1), date(2024, 2, 29)),
        (date(2024, 3, 1), date(2024, 3, 10)),
    ]
    assert timedelta(days=1)
