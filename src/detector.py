"""Layer A atomic detector (PDF Assignment 2, Phase 1).

``detect_distribution(fund_id, window_start, window_end) -> DetectionResult`` answers one
question: did the fund declare a distribution in the window? Yes, no or unknown.

Synthesis rules
---------------
- Only strategies that apply to the fund are considered (SEC EDGAR is ignored for a Canadian
  fund, TMX/SEDAR for a US fund). A non-applicable source can never block an answer.
- DECLARED: at least one Tier 1 or Tier 2 evidence item whose declaration date, ex-date or
  publication date is inside the window. Record or payable dates alone do not count, and
  Tier 3 evidence alone never produces DECLARED.
- NOT_DECLARED: at least one applicable Tier 1/2 source demonstrably covered the whole window
  and found nothing, no source reported a declaration, and no applicable Tier 1/2 source was
  unreachable. A detector that says NOT_DECLARED when it could not reach a source loses data
  forever, so an outage always yields UNKNOWN instead.
- Positive evidence from one source and full negative coverage from another -> UNKNOWN
  (CONFLICTING_EVIDENCE).
- Everything else -> UNKNOWN with the most specific reason.
- Strategies marked ``only_if_inconclusive`` (targeted lookup, PDF strategy 4) run only when
  the cheaper strategies did not reach an answer.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from src.confidence import compute_confidence
from src.http_client import HTTPClient
from src.models import (
    DetectionResult,
    DetectionStatus,
    Evidence,
    ExtractionRoute,
    SourceTier,
    UnknownReason,
)
from src.strategies import (
    BaseDetectionStrategy,
    CalendarExpectationStrategy,
    CanadianRegulatoryStrategy,
    OfficialSponsorWebStrategy,
    SECEdgarSubmissionsStrategy,
    SignalType,
    StrategyObservation,
    TargetedLookupStrategy,
    _dates_in_window,
)
from src.universe_loader import UniverseRegistry

logger = logging.getLogger(__name__)

_T12 = (SourceTier.TIER_1_AUTHORITATIVE, SourceTier.TIER_2_PRIMARY_UNSTRUCTURED)
_OUTAGES = (UnknownReason.SOURCE_UNAVAILABLE, UnknownReason.RETRIEVAL_FAILED)


def get_default_strategies(
    http_client: HTTPClient | None = None,
    universe: UniverseRegistry | None = None,
) -> list[BaseDetectionStrategy]:
    """Real-source strategy pipeline, cheapest first (PDF 'Detection strategies')."""
    return [
        CalendarExpectationStrategy(universe=universe),
        SECEdgarSubmissionsStrategy(http_client=http_client, universe=universe),
        CanadianRegulatoryStrategy(http_client=http_client, universe=universe),
        OfficialSponsorWebStrategy(http_client=http_client, universe=universe),
        TargetedLookupStrategy(http_client=http_client, universe=universe),
    ]


def _is_outage(obs: StrategyObservation) -> bool:
    if obs.failure_reason not in _OUTAGES:
        return False
    return any(ev.source_tier in _T12 for ev in obs.evidence) or not obs.evidence


def synthesize_detection_result(
    fund_id: str,
    window_start: date,
    window_end: date,
    observations: list[StrategyObservation],
) -> DetectionResult:
    """Deterministically combine strategy observations into one DetectionResult."""
    applicable = [o for o in observations if o.applicable]
    all_evidence: list[Evidence] = [ev for o in applicable for ev in o.evidence]

    valid_declarations: list[Evidence] = []
    negative_evidence: list[Evidence] = []
    suggested_route: ExtractionRoute | None = None
    failure_reasons: list[UnknownReason] = []

    for obs in applicable:
        if obs.failure_reason is not None:
            failure_reasons.append(obs.failure_reason)
        if obs.signal_type == SignalType.EVIDENCE_OBSERVATION:
            matched = [
                ev
                for ev in obs.evidence
                if ev.source_tier in _T12
                and _dates_in_window(ev, window_start, window_end)
            ]
            if matched:
                valid_declarations.extend(matched)
                if suggested_route is None and obs.suggested_route is not None:
                    suggested_route = obs.suggested_route
        elif (
            obs.window_fully_covered
            and not obs.has_declaration_in_window
            and obs.failure_reason is None
        ):
            clean = [
                ev
                for ev in obs.evidence
                if ev.source_tier in _T12 and ev.failure_reason is None
            ]
            negative_evidence.extend(clean)

    outage = any(_is_outage(o) for o in applicable)

    if valid_declarations and negative_evidence:
        conflict = Evidence(
            source_id="DETECTOR_SYNTHESIS_CONFLICT",
            source_tier=SourceTier.TIER_1_AUTHORITATIVE,
            url="internal://synthesis/conflict",
            retrieved_at=datetime.now(timezone.utc),
            snippet_or_locator="One source shows a declaration in the window; another fully covers the window and shows none.",
            failure_reason=UnknownReason.CONFLICTING_EVIDENCE,
        )
        return DetectionResult(
            fund_id=fund_id,
            status=DetectionStatus.UNKNOWN,
            confidence=0.0,
            evidence=all_evidence + [conflict],
            suggested_extraction_route=None,
            window_start=window_start,
            window_end=window_end,
            unknown_reason=UnknownReason.CONFLICTING_EVIDENCE,
        )

    if valid_declarations:
        return DetectionResult(
            fund_id=fund_id,
            status=DetectionStatus.DECLARED,
            confidence=compute_confidence(
                DetectionStatus.DECLARED, valid_declarations, other_source_failed=outage
            ),
            evidence=all_evidence,
            suggested_extraction_route=suggested_route or ExtractionRoute.MANUAL,
            window_start=window_start,
            window_end=window_end,
        )

    if negative_evidence and not outage:
        return DetectionResult(
            fund_id=fund_id,
            status=DetectionStatus.NOT_DECLARED,
            confidence=compute_confidence(
                DetectionStatus.NOT_DECLARED, negative_evidence, window_covered=True
            ),
            evidence=all_evidence,
            suggested_extraction_route=None,
            window_start=window_start,
            window_end=window_end,
        )

    reason = UnknownReason.INSUFFICIENT_EVIDENCE
    for candidate in (
        UnknownReason.SOURCE_UNAVAILABLE,
        UnknownReason.RETRIEVAL_FAILED,
        UnknownReason.INCOMPLETE_SOURCE,
        UnknownReason.CONFLICTING_EVIDENCE,
    ):
        if candidate in failure_reasons:
            reason = candidate
            break

    if not all_evidence:
        all_evidence = [
            Evidence(
                source_id="DETECTOR_INSPECTION_RECORD",
                source_tier=SourceTier.TIER_3_CORROBORATION,
                url="internal://inspection/log",
                retrieved_at=datetime.now(timezone.utc),
                snippet_or_locator=f"No applicable source produced evidence: {reason.value}",
                failure_reason=reason,
            )
        ]
    return DetectionResult(
        fund_id=fund_id,
        status=DetectionStatus.UNKNOWN,
        confidence=0.0,
        evidence=all_evidence,
        suggested_extraction_route=None,
        window_start=window_start,
        window_end=window_end,
        unknown_reason=reason,
    )


def _run(
    strategy: BaseDetectionStrategy, fund_id: str, window_start: date, window_end: date
) -> StrategyObservation:
    try:
        return strategy.inspect(fund_id, window_start, window_end)
    except (
        Exception
    ) as err:  # noqa: BLE001 - one broken source must not crash the detector
        logger.warning(
            "Strategy %s failed for fund %s: %s", strategy.name, fund_id, err
        )
        error_evidence = Evidence(
            source_id=f"STRATEGY_EXECUTION_ERROR_{strategy.name}",
            source_tier=SourceTier.TIER_3_CORROBORATION,
            url=f"internal://strategy/{strategy.name}/error",
            retrieved_at=datetime.now(timezone.utc),
            snippet_or_locator=f"Execution error: {type(err).__name__}: {err!s}",
            failure_reason=UnknownReason.RETRIEVAL_FAILED,
        )
        return StrategyObservation(
            strategy_name=strategy.name,
            signal_type=SignalType.NO_DATA_OBSERVATION,
            evidence=[error_evidence],
            failure_reason=UnknownReason.RETRIEVAL_FAILED,
            notes=f"Strategy execution error: {err!s}",
        )


def detect_distribution(
    fund_id: str,
    window_start: date,
    window_end: date,
    strategies: list[BaseDetectionStrategy] | None = None,
    http_client: HTTPClient | None = None,
    universe: UniverseRegistry | None = None,
) -> DetectionResult:
    """Did ``fund_id`` declare a distribution in [window_start, window_end]?

    Returns a DetectionResult with status DECLARED | NOT_DECLARED | UNKNOWN, confidence in
    [0.0, 1.0], evidence items (source_id, source_tier, url, retrieved_at, snippet_or_locator)
    and the suggested extraction route (API | HTML_TABLE | PDF | FILING | MANUAL).
    """
    if not fund_id or not isinstance(fund_id, str):
        raise ValueError("fund_id must be a non-empty string.")
    if not isinstance(window_start, date) or not isinstance(window_end, date):
        raise TypeError("window_start and window_end must be date objects.")
    if window_start > window_end:
        raise ValueError(
            f"Invalid date window: window_start ({window_start}) cannot be after window_end ({window_end})."
        )

    if strategies is None:
        strategies = get_default_strategies(http_client=http_client, universe=universe)

    if not strategies:
        return DetectionResult(
            fund_id=fund_id,
            status=DetectionStatus.UNKNOWN,
            confidence=0.0,
            evidence=[
                Evidence(
                    source_id="DETECTOR_UNCONFIGURED_PASS",
                    source_tier=SourceTier.TIER_3_CORROBORATION,
                    url="internal://detector/unconfigured",
                    retrieved_at=datetime.now(timezone.utc),
                    snippet_or_locator="No inspection strategies executed for fund.",
                    failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
                )
            ],
            suggested_extraction_route=None,
            window_start=window_start,
            window_end=window_end,
            unknown_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
        )

    primary = [s for s in strategies if not getattr(s, "only_if_inconclusive", False)]
    fallback = [s for s in strategies if getattr(s, "only_if_inconclusive", False)]

    observations = [_run(s, fund_id, window_start, window_end) for s in primary]
    result = synthesize_detection_result(
        fund_id, window_start, window_end, observations
    )
    if result.status == DetectionStatus.UNKNOWN and fallback:
        observations += [_run(s, fund_id, window_start, window_end) for s in fallback]
        result = synthesize_detection_result(
            fund_id, window_start, window_end, observations
        )
    return result
