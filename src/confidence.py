"""Deterministic confidence evaluation rules for fund distribution detection results.

These rules deterministically score the confidence of an already-determined DetectionStatus
based on the authority tier of supporting evidence, the presence of explicit declaration evidence,
and source window coverage completeness. This is a deterministic rule-based evaluation,
not a probabilistic model.
"""

from __future__ import annotations

from src.models import DetectionStatus, Evidence, SourceTier


def compute_confidence(
    status: DetectionStatus,
    evidence_list: list[Evidence],
    window_covered: bool = True,
) -> float | None:
    """Compute deterministic confidence score for an already-determined DetectionStatus.

    Rules:
    - UNKNOWN -> None
    - Empty evidence -> None
    - DECLARED:
        - Must have explicit declaration evidence (declaration_date_found is not None).
        - Tier 1 authoritative + explicit declaration date -> 1.0
        - Tier 2 primary unstructured + explicit declaration date -> 0.90
        - Tier 3 corroboration only -> None
        - Without explicit declaration date -> None
    - NOT_DECLARED:
        - Must have demonstrably complete window coverage (window_covered is True).
        - Tier 1 authoritative + complete coverage -> 1.0
        - Tier 2 primary unstructured + complete coverage -> 0.85
        - Incomplete window or Tier 3 only -> None

    Args:
        status: The determined DetectionStatus (DECLARED, NOT_DECLARED, UNKNOWN).
        evidence_list: List of supporting Evidence objects.
        window_covered: Whether the inspected sources demonstrably cover the requested window.

    Returns:
        A deterministic float (1.0, 0.90, or 0.85) if criteria are met, or None if evidence
        is insufficient to establish confidence.
    """
    if status == DetectionStatus.UNKNOWN or not evidence_list:
        return None

    has_tier1 = any(
        e.source_tier == SourceTier.TIER_1_AUTHORITATIVE for e in evidence_list
    )
    has_tier2 = any(
        e.source_tier == SourceTier.TIER_2_PRIMARY_UNSTRUCTURED for e in evidence_list
    )

    if status == DetectionStatus.DECLARED:
        has_explicit_declaration = any(
            e.declaration_date_found is not None for e in evidence_list
        )
        if not has_explicit_declaration:
            return None

        if has_tier1:
            return 1.0
        elif has_tier2:
            return 0.90
        else:
            return None

    if status == DetectionStatus.NOT_DECLARED:
        if not window_covered:
            return None

        if has_tier1:
            return 1.0
        elif has_tier2:
            return 0.85
        else:
            return None

    return None
