"""Offline unit tests for Vanguard fund profile embedded HTML parser, Layer A detection, and Layer B extraction."""

from __future__ import annotations

from datetime import date, datetime, timezone

from src.detector import detect_distribution
from src.extractor import ExtractionEngine
from src.http_client import HTTPResponseRecord
from src.models import (
    DetectionResult,
    DetectionStatus,
    ExtractionRoute,
    SourceTier,
)
from src.parsers.vanguard_profile_parser import parse_vanguard_profile
from src.strategies import OfficialSponsorWebStrategy
from src.universe_loader import UniverseFund, UniverseRegistry

SYNTHETIC_VANGUARD_HTML = """
<!DOCTYPE html>
<html>
<head><title>Vanguard Synthetic Fund Profile</title></head>
<body>
  <div class="profile-header"><h1>Vanguard Synthetic Total Market ETF (VSYN)</h1></div>
  <div data-vgn-funds-profile="{&#34;distributions&#34;:{&#34;incomeCapitalGains&#34;:[{&#34;type&#34;:&#34;Dividend&#34;,&#34;share&#34;:&#34;$1.0437000000&#34;,&#34;payableDate&#34;:&#34;06/30/2026&#34;,&#34;recordDate&#34;:&#34;06/26/2026&#34;,&#34;reInvestDate&#34;:&#34;06/26/2026&#34;,&#34;reInvestPrice&#34;:&#34;$363.02&#34;},{&#34;type&#34;:&#34;ROC&#34;,&#34;share&#34;:&#34;$0.0500000000&#34;,&#34;payableDate&#34;:&#34;06/30/2026&#34;,&#34;recordDate&#34;:&#34;06/26/2026&#34;,&#34;reInvestDate&#34;:&#34;06/26/2026&#34;,&#34;reInvestPrice&#34;:&#34;$363.02&#34;},{&#34;type&#34;:&#34;Dividend&#34;,&#34;share&#34;:&#34;$0.9500000000&#34;,&#34;payableDate&#34;:&#34;03/31/2026&#34;,&#34;recordDate&#34;:&#34;03/25/2026&#34;,&#34;reInvestDate&#34;:&#34;03/25/2026&#34;,&#34;reInvestPrice&#34;:&#34;$350.00&#34;}]}}">
  </div>
</body>
</html>
"""


class SyntheticHTTPClient:
    """Mock HTTP client returning predetermined response records."""

    def __init__(self, record_factory) -> None:
        self.record_factory = record_factory
        self.call_count = 0
        self.urls: list[str] = []

    def get(
        self, url: str, headers: dict | None = None, params: dict | None = None
    ) -> HTTPResponseRecord:
        self.call_count += 1
        self.urls.append(url)
        return self.record_factory(url)


def test_parse_vanguard_profile_fields_and_roc_mapping() -> None:
    """Parser extracts correct dates, amounts, routes, and maps ROC properly."""
    events = parse_vanguard_profile(
        html_text=SYNTHETIC_VANGUARD_HTML,
        fund_id="US_VANGUARD_VSYN",
        source_url="https://investor.vanguard.com/investment-products/etfs/profile/vsyn",
        ticker="VSYN",
    )

    assert len(events) == 3

    # Row 1: June 2026 Dividend
    ev1 = events[0]
    assert ev1.fund_id == "US_VANGUARD_VSYN"
    assert ev1.ticker == "VSYN"
    assert ev1.country == "US"
    assert ev1.currency == "USD"
    assert ev1.ex_date == date(2026, 6, 26)
    assert ev1.record_date == date(2026, 6, 26)
    assert ev1.payable_date == date(2026, 6, 30)
    assert ev1.gross_amount == 1.0437
    assert ev1.distribution_type == "Income"
    assert ev1.distribution_category == "INCOME"
    assert ev1.extraction_route == ExtractionRoute.API
    assert ev1.source_tier == SourceTier.TIER_2_PRIMARY_UNSTRUCTURED

    # Row 2: June 2026 ROC
    ev2 = events[1]
    assert ev2.ex_date == date(2026, 6, 26)
    assert ev2.gross_amount == 0.05
    assert ev2.distribution_type == "Return of Capital"
    assert ev2.distribution_category == "RETURN_OF_CAPITAL"

    # Row 3: March 2026 Dividend
    ev3 = events[2]
    assert ev3.ex_date == date(2026, 3, 25)
    assert ev3.gross_amount == 0.95


def test_parse_vanguard_profile_missing_attribute_or_invalid_json() -> None:
    """Returns empty list when attribute is missing or JSON is invalid without raising."""
    # Missing attribute
    assert (
        parse_vanguard_profile(
            "<html><body>No attribute here</body></html>",
            "US_VANGUARD_VTI",
            "https://investor.vanguard.com",
        )
        == []
    )

    # Empty string or None
    assert (
        parse_vanguard_profile(
            None,
            "US_VANGUARD_VTI",
            "https://investor.vanguard.com",
        )
        == []
    )

    # Corrupt JSON inside attribute
    corrupt_html = '<div data-vgn-funds-profile="{invalid: json content}"></div>'
    assert (
        parse_vanguard_profile(
            corrupt_html,
            "US_VANGUARD_VTI",
            "https://investor.vanguard.com",
        )
        == []
    )


def test_detector_window_containing_ex_date_declared() -> None:
    """Window containing an ex-date returns DECLARED with suggested route API."""
    fund = UniverseFund(
        fund_id="US_VANGUARD_VSYN",
        country="US",
        fund_name="Vanguard Synthetic ETF",
        fund_type="ETF",
        fund_family="Vanguard",
        official_source_url="https://investor.vanguard.com/investment-products/etfs/profile/vsyn",
        ticker="VSYN",
    )
    registry = UniverseRegistry([fund])

    retrieved_at = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
    mock_client = SyntheticHTTPClient(
        lambda url: HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=retrieved_at,
            content_text=SYNTHETIC_VANGUARD_HTML,
            content_bytes=SYNTHETIC_VANGUARD_HTML.encode("utf-8"),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.05,
            content_type="text/html",
        )
    )

    strategy = OfficialSponsorWebStrategy(
        http_client=mock_client,
        universe=registry,
    )

    # June 2026 window containing 2026-06-26 distributions
    w_start = date(2026, 6, 1)
    w_end = date(2026, 6, 30)

    obs = strategy.inspect("US_VANGUARD_VSYN", w_start, w_end)
    assert obs.has_declaration_in_window is True
    assert obs.suggested_route == ExtractionRoute.API
    assert len(obs.evidence) == 2

    res = detect_distribution(
        fund_id="US_VANGUARD_VSYN",
        window_start=w_start,
        window_end=w_end,
        strategies=[strategy],
    )
    assert res.status == DetectionStatus.DECLARED
    assert res.suggested_extraction_route == ExtractionRoute.API


def test_detector_window_after_earliest_row_not_declared() -> None:
    """Window after earliest row with no ex-date, fetched 8+ days later -> NOT_DECLARED."""
    fund = UniverseFund(
        fund_id="US_VANGUARD_VSYN",
        country="US",
        fund_name="Vanguard Synthetic ETF",
        fund_type="ETF",
        fund_family="Vanguard",
        official_source_url="https://investor.vanguard.com/investment-products/etfs/profile/vsyn",
        ticker="VSYN",
    )
    registry = UniverseRegistry([fund])

    # May 2026 window: after earliest row (2026-03-25), no distribution in May 2026
    # Retrieved on 2026-06-08 (8 days after window_end 2026-05-31 >= 7 day buffer)
    retrieved_at = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
    mock_client = SyntheticHTTPClient(
        lambda url: HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=retrieved_at,
            content_text=SYNTHETIC_VANGUARD_HTML,
            content_bytes=SYNTHETIC_VANGUARD_HTML.encode("utf-8"),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.05,
            content_type="text/html",
        )
    )

    strategy = OfficialSponsorWebStrategy(
        http_client=mock_client,
        universe=registry,
    )

    w_start = date(2026, 5, 1)
    w_end = date(2026, 5, 31)

    obs = strategy.inspect("US_VANGUARD_VSYN", w_start, w_end)
    assert obs.has_declaration_in_window is False
    assert obs.window_fully_covered is True

    res = detect_distribution(
        fund_id="US_VANGUARD_VSYN",
        window_start=w_start,
        window_end=w_end,
        strategies=[strategy],
    )
    assert res.status == DetectionStatus.NOT_DECLARED


def test_detector_window_before_earliest_row_unknown() -> None:
    """Window before earliest row (2026-03-25) cannot prove negative coverage -> UNKNOWN."""
    fund = UniverseFund(
        fund_id="US_VANGUARD_VSYN",
        country="US",
        fund_name="Vanguard Synthetic ETF",
        fund_type="ETF",
        fund_family="Vanguard",
        official_source_url="https://investor.vanguard.com/investment-products/etfs/profile/vsyn",
        ticker="VSYN",
    )
    registry = UniverseRegistry([fund])

    # January 2026 window: earlier than earliest ex-date (2026-03-25)
    retrieved_at = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
    mock_client = SyntheticHTTPClient(
        lambda url: HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=retrieved_at,
            content_text=SYNTHETIC_VANGUARD_HTML,
            content_bytes=SYNTHETIC_VANGUARD_HTML.encode("utf-8"),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.05,
            content_type="text/html",
        )
    )

    strategy = OfficialSponsorWebStrategy(
        http_client=mock_client,
        universe=registry,
    )

    w_start = date(2026, 1, 1)
    w_end = date(2026, 1, 31)

    obs = strategy.inspect("US_VANGUARD_VSYN", w_start, w_end)
    assert obs.has_declaration_in_window is False
    assert obs.window_fully_covered is False

    res = detect_distribution(
        fund_id="US_VANGUARD_VSYN",
        window_start=w_start,
        window_end=w_end,
        strategies=[strategy],
    )
    assert res.status == DetectionStatus.UNKNOWN


def test_extractor_routes_vanguard_profile_as_api() -> None:
    """Layer B ExtractionEngine routes Vanguard profile HTML to API route."""
    fund = UniverseFund(
        fund_id="US_VANGUARD_VSYN",
        country="US",
        fund_name="Vanguard Synthetic ETF",
        fund_type="ETF",
        fund_family="Vanguard",
        official_source_url="https://investor.vanguard.com/investment-products/etfs/profile/vsyn",
        ticker="VSYN",
    )
    registry = UniverseRegistry([fund])

    retrieved_at = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
    mock_client = SyntheticHTTPClient(
        lambda url: HTTPResponseRecord(
            url=url,
            status_code=200,
            retrieved_at=retrieved_at,
            content_text=SYNTHETIC_VANGUARD_HTML,
            content_bytes=SYNTHETIC_VANGUARD_HTML.encode("utf-8"),
            failure_reason=None,
            is_success=True,
            elapsed_seconds=0.05,
            content_type="text/html",
        )
    )

    extractor = ExtractionEngine(
        http_client=mock_client,
        universe=registry,
    )

    # Simulate DECLARED result for June 2026
    detection = DetectionResult(
        fund_id="US_VANGUARD_VSYN",
        status=DetectionStatus.DECLARED,
        confidence=1.0,
        window_start=date(2026, 6, 1),
        window_end=date(2026, 6, 30),
        evidence=OfficialSponsorWebStrategy(
            http_client=mock_client,
            universe=registry,
        )
        .inspect("US_VANGUARD_VSYN", date(2026, 6, 1), date(2026, 6, 30))
        .evidence,
    )

    outcome = extractor.extract_with_routes(detection)
    assert outcome.route_taken == ExtractionRoute.API
    assert len(outcome.events) == 2
    assert outcome.events[0].gross_amount == 1.0437
    assert outcome.events[1].gross_amount == 0.05
