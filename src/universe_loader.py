"""Universe loader and registry lookup for verified funds in config/universe_100.json."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class UniverseFund:
    """Verified fund record representation."""

    fund_id: str
    country: str
    fund_name: str
    fund_type: str
    fund_family: str
    official_source_url: str
    ticker: str | None = None
    cik: str | None = None
    sec_series_id: str | None = None
    sec_class_id: str | None = None
    sedar_id: str | None = None
    fundserv_code: str | None = None
    verification_source: str = "OFFICIAL"
    verified_at: str = "2026-09-20"
    expected_frequency: str | None = None
    is_etf: bool = False
    is_monthly_payer: bool = False
    is_verified: bool = True
    verification_audit: dict[str, Any] = field(default_factory=dict)


class UniverseRegistry:
    """In-memory registry of verified funds loaded from universe_100.json."""

    def __init__(self, funds: list[UniverseFund]) -> None:
        self._funds_by_id: dict[str, UniverseFund] = {f.fund_id: f for f in funds}
        self._funds_by_ticker: dict[str, UniverseFund] = {
            f.ticker.upper(): f for f in funds if f.ticker
        }

    @classmethod
    def from_json(cls, path: str | Path | None = None) -> UniverseRegistry:
        """Load universe registry from JSON file."""
        if path is None:
            path = Path(__file__).parent.parent / "config" / "universe_100.json"
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Universe file not found: {file_path}")

        with file_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            raise TypeError(
                "Invalid universe format: root must be a list of fund records."
            )

        funds: list[UniverseFund] = []
        for item in data:
            funds.append(
                UniverseFund(
                    fund_id=item["fund_id"],
                    country=item["country"],
                    fund_name=item["fund_name"],
                    fund_type=item["fund_type"],
                    ticker=item.get("ticker"),
                    fund_family=item["fund_family"],
                    cik=item.get("cik"),
                    sec_series_id=item.get("sec_series_id"),
                    sec_class_id=item.get("sec_class_id"),
                    sedar_id=item.get("sedar_id"),
                    fundserv_code=item.get("fundserv_code"),
                    official_source_url=item["official_source_url"],
                    verification_source=item["verification_source"],
                    verified_at=item["verified_at"],
                    expected_frequency=item.get("expected_frequency"),
                    is_etf=bool(item.get("is_etf")),
                    is_monthly_payer=bool(item.get("is_monthly_payer")),
                    is_verified=bool(item.get("is_verified")),
                    verification_audit=item.get("verification_audit", {}),
                )
            )
        return cls(funds)

    def get_fund(self, fund_id_or_ticker: str) -> UniverseFund | None:
        """Find a verified fund by fund_id or exchange ticker."""
        key = fund_id_or_ticker.strip()
        if key in self._funds_by_id:
            return self._funds_by_id[key]
        return self._funds_by_ticker.get(key.upper())

    def list_funds(self) -> list[UniverseFund]:
        """Return all verified funds in the universe."""
        return list(self._funds_by_id.values())
