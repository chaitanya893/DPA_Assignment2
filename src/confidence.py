"""Deterministic confidence rules for Layer A detection results.

The PDF interface defines ``confidence: 0.0 - 1.0`` for every result, so every status gets a
float. The score reflects the authority of the evidence behind the status:

DECLARED
  - Tier 1 authoritative evidence dated in the window              -> 1.00
  - Tier 2 with an explicit declaration date or labelled ex-date   -> 0.90
  - Tier 2 with only a publication date (press-release dateline)   -> 0.80
NOT_DECLARED (requires demonstrated full coverage of the window)
  - Tier 1 complete coverage                                        -> 1.00
  - Tier 2 complete coverage                                        -> 0.85
Any status: minus 0.05 when another applicable source failed to respond.
UNKNOWN -> 0.0 (no confidence either way). Tier 3 alone never scores above 0.0.
"""

from __future__ import annotations

from src.models import DetectionStatus, Evidence, SourceTier

_T12 = (SourceTier.TIER_1_AUTHORITATIVE, SourceTier.TIER_2_PRIMARY_UNSTRUCTURED)


def compute_confidence(
    status: DetectionStatus,
    evidence_list: list[Evidence],
    window_covered: bool = True,
    other_source_failed: bool = False,
) -> float:
    """Return a confidence in [0.0, 1.0] for an already-decided status."""
    if status == DetectionStatus.UNKNOWN or not evidence_list:
        return 0.0

    tiers = {e.source_tier for e in evidence_list}
    penalty = 0.05 if other_source_failed else 0.0

    if status == DetectionStatus.DECLARED:
        dated = [
            e
            for e in evidence_list
            if e.source_tier in _T12
            and (e.declaration_date_found or e.ex_date_found or e.published_date_found)
        ]
        if not dated:
            return 0.0
        if any(e.source_tier == SourceTier.TIER_1_AUTHORITATIVE for e in dated):
            base = 1.0
        elif any(e.declaration_date_found or e.ex_date_found for e in dated):
            base = 0.90
        else:
            base = 0.80
        return round(max(0.0, base - penalty), 2)

    if status == DetectionStatus.NOT_DECLARED:
        if not window_covered:
            return 0.0
        if SourceTier.TIER_1_AUTHORITATIVE in tiers:
            base = 1.0
        elif SourceTier.TIER_2_PRIMARY_UNSTRUCTURED in tiers:
            base = 0.85
        else:
            return 0.0
        return round(max(0.0, base - penalty), 2)

    return 0.0
