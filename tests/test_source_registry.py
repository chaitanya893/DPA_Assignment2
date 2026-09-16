"""Unit tests for config/source_registry.yaml and src/registry_loader.py."""

from pathlib import Path

import pytest

from src.models import SourceTier
from src.registry_loader import SourceDefinition, SourceRegistry


@pytest.fixture
def registry_file_path() -> Path:
    return Path(__file__).parent.parent / "config" / "source_registry.yaml"


def test_source_registry_loads_successfully(registry_file_path: Path) -> None:
    """1. Source registry loads from YAML without schema errors."""
    registry = SourceRegistry.from_yaml(registry_file_path)
    sources = registry.list_sources()
    assert len(sources) >= 8


def test_required_registry_fields_present(registry_file_path: Path) -> None:
    """2. All required fields are present and non-empty for every registered source."""
    registry = SourceRegistry.from_yaml(registry_file_path)
    for source in registry.list_sources():
        assert source.source_id, f"Missing source_id for {source}"
        assert source.source_type, f"Missing source_type for {source.source_id}"
        assert isinstance(
            source.source_tier, SourceTier
        ), f"Invalid tier for {source.source_id}"
        assert source.applicable_country in {"US", "CA", "BOTH"}
        assert len(source.applicable_fund_types) > 0
        assert len(source.limitations) > 0
        assert source.access_method


def test_tier_hierarchy_and_filtering(registry_file_path: Path) -> None:
    """3 & 4. Tier values (1, 2, 3) and country applicability filtering work properly."""
    registry = SourceRegistry.from_yaml(registry_file_path)

    tier1_sources = registry.get_sources_by_tier(SourceTier.TIER_1_AUTHORITATIVE)
    tier2_sources = registry.get_sources_by_tier(SourceTier.TIER_2_PRIMARY_UNSTRUCTURED)
    tier3_sources = registry.get_sources_by_tier(SourceTier.TIER_3_CORROBORATION)

    assert len(tier1_sources) >= 4  # SEC Rule 19a-1, 497, NPORT/NCEN, mappings, TMX/CDS
    assert len(tier2_sources) >= 3  # Official pages, press releases, cap gains PDFs
    assert len(tier3_sources) >= 1  # Market data feed

    # Verify Canadian sources filter
    ca_sources = registry.get_sources_by_country("CA")
    ca_ids = {s.source_id for s in ca_sources}
    assert "tmx_cds_bulletins" in ca_ids
    assert "official_fund_distribution_page" in ca_ids


def test_limitations_explicitly_documented(registry_file_path: Path) -> None:
    """5. Every single source must document limitations explicitly."""
    registry = SourceRegistry.from_yaml(registry_file_path)
    for source in registry.list_sources():
        for lim in source.limitations:
            assert (
                len(lim.strip()) > 10
            ), f"Limitation description too brief for {source.source_id}"


def test_source_definition_validation_errors() -> None:
    """Verify SourceDefinition raises on invalid parameters."""
    with pytest.raises(ValueError, match="source_id"):
        SourceDefinition(
            source_id="",
            source_type="TEST",
            source_tier=SourceTier.TIER_1_AUTHORITATIVE,
            applicable_country="US",
            applicable_fund_types=("MUTUAL_FUND",),
            evidence_capabilities=("test",),
            available_date_fields=("ex_date",),
            limitations=("limitation",),
            access_method="HTTPS",
        )

    with pytest.raises(ValueError, match="applicable_country"):
        SourceDefinition(
            source_id="TEST_SRC",
            source_type="TEST",
            source_tier=SourceTier.TIER_1_AUTHORITATIVE,
            applicable_country="INVALID_COUNTRY",
            applicable_fund_types=("MUTUAL_FUND",),
            evidence_capabilities=("test",),
            available_date_fields=("ex_date",),
            limitations=("limitation",),
            access_method="HTTPS",
        )
