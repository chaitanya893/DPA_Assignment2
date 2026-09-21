"""Source registry loader and schema validator.

Parses config/source_registry.yaml and provides structured, validated SourceDefinition objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

from src.models import SourceTier


class SourceRoleCategory(str, Enum):
    """Functional role category of a distribution detection source."""

    DIRECT_DECLARATION = "DIRECT_DECLARATION"  # Can directly establish an explicit declaration when containing it
    SUPPORTING_RETROSPECTIVE = (
        "SUPPORTING_RETROSPECTIVE"  # Provides supporting/retrospective evidence
    )
    METADATA_ONLY = "METADATA_ONLY"  # Metadata and identifier mapping only
    CORROBORATION_ONLY = "CORROBORATION_ONLY"  # Corroboration cross-check only


@dataclass(frozen=True)
class SourceDefinition:
    """Validated metadata definition for a distribution detection source."""

    source_id: str
    source_type: str
    source_tier: SourceTier
    role_category: SourceRoleCategory
    applicable_country: str  # US, CA, or BOTH
    applicable_fund_types: tuple[str, ...]
    evidence_capabilities: tuple[str, ...]
    available_date_fields: tuple[str, ...]
    limitations: tuple[str, ...]
    access_method: str
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.source_id or not isinstance(self.source_id, str):
            raise ValueError("SourceDefinition source_id must be a non-empty string.")
        if not isinstance(self.source_tier, SourceTier):
            raise TypeError(
                f"Invalid source_tier: {self.source_tier}. Must be a valid SourceTier."
            )
        if not isinstance(self.role_category, SourceRoleCategory):
            raise TypeError(
                f"Invalid role_category: {self.role_category}. Must be a valid SourceRoleCategory."
            )
        if self.applicable_country not in {"US", "CA", "BOTH"}:
            raise ValueError(
                f"Invalid applicable_country: {self.applicable_country}. Must be 'US', 'CA', or 'BOTH'."
            )
        if not self.limitations:
            raise ValueError(
                "Every source must have explicitly documented limitations."
            )


class SourceRegistry:
    """Registry holding all validated source definitions."""

    def __init__(self, sources: list[SourceDefinition]) -> None:
        self._sources_by_id: dict[str, SourceDefinition] = {
            s.source_id: s for s in sources
        }

    @classmethod
    def from_yaml(cls, path: str | Path) -> SourceRegistry:
        """Load and validate source registry from a YAML file."""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Source registry file not found: {file_path}")

        with file_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict) or "sources" not in data:
            raise ValueError(
                "Invalid source registry format: root must be a dict containing 'sources' list."
            )

        source_definitions: list[SourceDefinition] = []
        for item in data.get("sources", []):
            tier_val = item.get("source_tier")
            try:
                tier_enum = SourceTier(int(tier_val))
            except (ValueError, TypeError) as err:
                raise ValueError(
                    f"Invalid source_tier '{tier_val}' in source '{item.get('source_id')}': {err}"
                ) from err

            role_val = item.get("role_category")
            try:
                role_enum = SourceRoleCategory(str(role_val))
            except (ValueError, KeyError) as err:
                raise ValueError(
                    f"Invalid role_category '{role_val}' in source '{item.get('source_id')}': {err}"
                ) from err

            src_def = SourceDefinition(
                source_id=item.get("source_id", ""),
                source_type=item.get("source_type", ""),
                source_tier=tier_enum,
                role_category=role_enum,
                applicable_country=item.get("applicable_country", ""),
                applicable_fund_types=tuple(item.get("applicable_fund_types", [])),
                evidence_capabilities=tuple(item.get("evidence_capabilities", [])),
                available_date_fields=tuple(item.get("available_date_fields", [])),
                limitations=tuple(item.get("limitations", [])),
                access_method=item.get("access_method", ""),
                notes=item.get("notes", ""),
            )
            source_definitions.append(src_def)

        return cls(sources=source_definitions)

    def get_source(self, source_id: str) -> SourceDefinition | None:
        """Get source definition by ID."""
        return self._sources_by_id.get(source_id)

    def list_sources(self) -> list[SourceDefinition]:
        """Return all registered source definitions."""
        return list(self._sources_by_id.values())

    def get_sources_by_tier(self, tier: SourceTier) -> list[SourceDefinition]:
        """Filter sources by tier hierarchy."""
        return [s for s in self._sources_by_id.values() if s.source_tier == tier]

    def get_sources_by_role(self, role: SourceRoleCategory) -> list[SourceDefinition]:
        """Filter sources by role category."""
        return [s for s in self._sources_by_id.values() if s.role_category == role]

    def get_sources_by_country(self, country: str) -> list[SourceDefinition]:
        """Filter sources applicable to US or CA."""
        return [
            s
            for s in self._sources_by_id.values()
            if s.applicable_country in {country, "BOTH"}
        ]
