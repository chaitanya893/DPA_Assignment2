"""Tests for State Street SPDR historical distributions Excel parser."""

from __future__ import annotations

import io
from datetime import date, datetime, timezone

import openpyxl
import pytest

from src.http_client import HTTPClient, HTTPResponseRecord
from src.models import (
    ExtractionRoute,
    USComponentType,
)
from src.parsers.spdr_distributions_parser import parse_spdr_distributions
from src.strategies import OfficialSponsorWebStrategy
from src.universe_loader import UniverseFund, UniverseRegistry


def _create_synthetic_spdr_xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "dividend"
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def sample_spdr_xlsx_bytes() -> bytes:
    headers = [
        "FUND NAME",
        "TICKER",
        "CUSIP",
        "EX-DATE",
        "RECORD DATE",
        "PAYABLE DATE",
        "DIVIDEND ($)",
        "SHORT TERM CAPITAL GAIN ($)",
        "LONG TERM CAPITAL GAIN ($)",
        "FREQUENCY",
    ]
    rows = [
        headers,
        [
            "SPDR Portfolio S&P 1500 Composite Stock Market ETF",
            "SPTM",
            "78464A706",
            "2025-03-21",
            "2025-03-24",
            "2025-03-27",
            "0.250000",
            "0.000000",
            "0.000000",
            "Quarterly",
        ],
        [
            "SPDR Portfolio S&P 1500 Composite Stock Market ETF",
            "SPTM",
            "78464A706",
            "2025-06-20",
            "2025-06-23",
            "2025-06-26",
            "0.260000",
            "0.050000",
            "0.100000",
            "Quarterly",
        ],
        [
            "SPDR Bloomberg High Yield Bond ETF",
            "JNK",
            "78464A409",
            "2025-06-02",
            "2025-06-03",
            "2025-06-06",
            "0.500000",
            "0.000000",
            "0.000000",
            "Monthly",
        ],
    ]
    return _create_synthetic_spdr_xlsx(rows)


def test_parse_spdr_distributions_basic(sample_spdr_xlsx_bytes: bytes):
    events = parse_spdr_distributions(
        sample_spdr_xlsx_bytes,
        fund_id="US_SPDR_SPTM",
        ticker="SPTM",
        source_url="https://www.ssga.com/sample.xlsx",
    )
    assert len(events) == 2
    ev1 = events[0]
    assert ev1.fund_id == "US_SPDR_SPTM"
    assert ev1.ticker == "SPTM"
    assert ev1.ex_date == date(2025, 3, 21)
    assert ev1.record_date == date(2025, 3, 24)
    assert ev1.payable_date == date(2025, 3, 27)
    assert ev1.gross_amount == 0.25
    assert ev1.distribution_type == "Income"
    assert ev1.extraction_route == ExtractionRoute.PDF
    assert len(ev1.components) == 1
    assert ev1.components[0].component_type == USComponentType.ORDINARY_INCOME
    assert ev1.components[0].amount == 0.25

    ev2 = events[1]
    assert ev2.ex_date == date(2025, 6, 20)
    assert ev2.record_date == date(2025, 6, 23)
    assert ev2.payable_date == date(2025, 6, 26)
    assert ev2.gross_amount == 0.41  # 0.26 + 0.05 + 0.10
    assert len(ev2.components) == 3
    comp_types = {c.component_type: c.amount for c in ev2.components}
    assert comp_types[USComponentType.ORDINARY_INCOME] == 0.26
    assert comp_types[USComponentType.SHORT_TERM_CAPITAL_GAIN] == 0.05
    assert comp_types[USComponentType.LONG_TERM_CAPITAL_GAIN] == 0.10


def test_parse_spdr_distributions_window_filtering(sample_spdr_xlsx_bytes: bytes):
    events = parse_spdr_distributions(
        sample_spdr_xlsx_bytes,
        fund_id="US_SPDR_SPTM",
        ticker="SPTM",
        source_url="https://www.ssga.com/sample.xlsx",
        window_start=date(2025, 6, 1),
        window_end=date(2025, 6, 30),
    )
    assert len(events) == 1
    assert events[0].ex_date == date(2025, 6, 20)


def test_parse_spdr_distributions_ticker_filtering(sample_spdr_xlsx_bytes: bytes):
    events_jnk = parse_spdr_distributions(
        sample_spdr_xlsx_bytes,
        fund_id="US_SPDR_JNK",
        ticker="JNK",
        source_url="https://www.ssga.com/sample.xlsx",
    )
    assert len(events_jnk) == 1
    assert events_jnk[0].ticker == "JNK"
    assert events_jnk[0].gross_amount == 0.50

    events_unknown = parse_spdr_distributions(
        sample_spdr_xlsx_bytes,
        fund_id="US_SPDR_VOO",
        ticker="VOO",
        source_url="https://www.ssga.com/sample.xlsx",
    )
    assert events_unknown == []


def test_parse_spdr_distributions_error_handling():
    assert parse_spdr_distributions(b"", fund_id="F", ticker="T", source_url="U") == []
    assert parse_spdr_distributions(None, fund_id="F", ticker="T", source_url="U") == []
    assert (
        parse_spdr_distributions(
            b"not an xlsx", fund_id="F", ticker="T", source_url="U"
        )
        == []
    )

    # Workbook without proper headers
    empty_xlsx = _create_synthetic_spdr_xlsx([["col1", "col2"], ["a", "b"]])
    assert (
        parse_spdr_distributions(empty_xlsx, fund_id="F", ticker="T", source_url="U")
        == []
    )


def test_official_sponsor_web_strategy_spdr_xlsx(
    monkeypatch, sample_spdr_xlsx_bytes: bytes
):
    class DummyClient(HTTPClient):
        def get(self, url, **kwargs):
            return HTTPResponseRecord(
                url=url,
                status_code=200,
                retrieved_at=datetime(2025, 6, 30, tzinfo=timezone.utc),
                content_text="",
                content_bytes=sample_spdr_xlsx_bytes,
                failure_reason=None,
                is_success=True,
                elapsed_seconds=0.1,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    fund = UniverseFund(
        fund_id="US_SPDR_SPTM",
        fund_name="SPDR Portfolio S&P 1500 Composite Stock Market ETF",
        fund_type="ETF",
        ticker="SPTM",
        country="US",
        fund_family="SPDR",
        is_etf=True,
        distribution_api_url="https://www.ssga.com/sample.xlsx",
        official_source_url="https://www.ssga.com/sample.xlsx",
    )
    reg = UniverseRegistry(funds=[fund])
    strategy = OfficialSponsorWebStrategy(http_client=DummyClient(), universe=reg)

    # Positive window (June 2025)
    obs = strategy.inspect("US_SPDR_SPTM", date(2025, 6, 1), date(2025, 6, 30))
    assert obs.has_declaration_in_window is True
    assert obs.suggested_route == ExtractionRoute.PDF
    assert len(obs.evidence) >= 1
    assert obs.evidence[0].ex_date_found == date(2025, 6, 20)

    # Negative window covered by sheet spanning dates (April 2025)
    obs_neg = strategy.inspect("US_SPDR_SPTM", date(2025, 4, 1), date(2025, 4, 30))
    assert obs_neg.has_declaration_in_window is False
    assert obs_neg.window_fully_covered is True
