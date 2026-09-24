"""Layer A Atomic Detector for Fund Distribution Detection.

Evaluates: "Was a distribution DECLARED within [window_start, window_end]?"
Adheres strictly to deterministic window semantics, auditable evidence trails,
and zero fabricated data.
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
    VerifiedScheduleStrategy,
)
from src.universe_loader import UniverseRegistry

logger = logging.getLogger(__name__)


def get_default_strategies(
    http_client: HTTPClient | None = None,
    universe: UniverseRegistry | None = None,
) -> list[BaseDetectionStrategy]:
    """Provide standard real-source strategy pipeline."""
    return [
        CalendarExpectationStrategy(universe=universe),
        VerifiedScheduleStrategy(universe=universe),
        SECEdgarSubmissionsStrategy(http_client=http_client, universe=universe),
        CanadianRegulatoryStrategy(http_client=http_client, universe=universe),
        OfficialSponsorWebStrategy(http_client=http_client, universe=universe),
    ]


def synthesize_detection_result(
    fund_id: str,
    window_start: date,
    window_end: date,
    observations: list[StrategyObservation],
) -> DetectionResult:
    """Deterministically synthesize strategy observations into a single DetectionResult.

    Strict rules:
    - Explicit declaration evidence with declaration date in [window_start, window_end] -> DECLARED
    - Complete authoritative/primary coverage with sufficient negative evidence -> NOT_DECLARED
    - Conflicting observations -> UNKNOWN (CONFLICTING_EVIDENCE)
    - Source failure -> UNKNOWN (SOURCE_UNAVAILABLE / RETRIEVAL_FAILED)
    - Incomplete coverage / Insufficient data -> UNKNOWN (INCOMPLETE_SOURCE / INSUFFICIENT_EVIDENCE)
    - Calendar signals alone NEVER produce DECLARED or NOT_DECLARED.
    """
    all_evidence: list[Evidence] = []
    for obs in observations:
        all_evidence.extend(obs.evidence)

    # Collect distinct declaration evidence matching window
    valid_declarations: list[Evidence] = []
    has_conflicting_negative = False
    has_full_window_coverage = False
    failure_reasons: list[UnknownReason] = []
    suggested_route: ExtractionRoute | None = None

    for obs in observations:
        if obs.failure_reason is not None:
            failure_reasons.append(obs.failure_reason)

        if obs.signal_type == SignalType.EVIDENCE_OBSERVATION:
            if obs.suggested_route is not None and suggested_route is None:
                suggested_route = obs.suggested_route

            for ev in obs.evidence:
                # Tier 3 can NEVER be the sole basis for DECLARED
                has_date_in_window = False
                for d in (
                    ev.declaration_date_found,
                    ev.ex_date_found,
                    ev.record_date_found,
                    ev.payable_date_found,
                ):
                    if d is not None and window_start <= d <= window_end:
                        has_date_in_window = True
                        break

                if (
                    ev.source_tier
                    in (
                        SourceTier.TIER_1_AUTHORITATIVE,
                        SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                    )
                    and has_date_in_window
                ):
                    valid_declarations.append(ev)

        # Tier 3 can NEVER establish full window coverage for NOT_DECLARED
        if obs.window_fully_covered:
            has_tier1_or_2 = any(
                ev.source_tier
                in (
                    SourceTier.TIER_1_AUTHORITATIVE,
                    SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                )
                for ev in obs.evidence
            )
            if has_tier1_or_2:
                has_full_window_coverage = True
                if not obs.has_declaration_in_window:
                    has_conflicting_negative = True

    # 1. Check for conflicting evidence (both valid declaration and confirmed negative on full window)
    if valid_declarations and has_conflicting_negative:
        conflict_evidence = Evidence(
            source_id="DETECTOR_SYNTHESIS_CONFLICT",
            source_tier=SourceTier.TIER_1_AUTHORITATIVE,
            url="internal://synthesis/conflict",
            retrieved_at=datetime.now(timezone.utc),
            snippet_or_locator="Conflicting observations: positive declaration vs negative coverage.",
            failure_reason=UnknownReason.CONFLICTING_EVIDENCE,
        )
        return DetectionResult(
            fund_id=fund_id,
            status=DetectionStatus.UNKNOWN,
            confidence=None,
            evidence=all_evidence + [conflict_evidence],
            suggested_extraction_route=None,
            window_start=window_start,
            window_end=window_end,
        )

    # 2. Positive Declaration match within window
    if valid_declarations:
        conf = compute_confidence(DetectionStatus.DECLARED, valid_declarations)
        return DetectionResult(
            fund_id=fund_id,
            status=DetectionStatus.DECLARED,
            confidence=conf,
            evidence=all_evidence or valid_declarations,
            suggested_extraction_route=suggested_route or ExtractionRoute.MANUAL,
            window_start=window_start,
            window_end=window_end,
        )

    # 3. Complete negative evidence (demonstrably covered window with no declaration)
    primary_outage = any(
        r in {UnknownReason.SOURCE_UNAVAILABLE, UnknownReason.RETRIEVAL_FAILED}
        for r in failure_reasons
    )
    if has_full_window_coverage and not valid_declarations and not primary_outage:
        conf = compute_confidence(
            DetectionStatus.NOT_DECLARED,
            all_evidence,
            window_covered=True,
        )
        return DetectionResult(
            fund_id=fund_id,
            status=DetectionStatus.NOT_DECLARED,
            confidence=conf,
            evidence=all_evidence,
            suggested_extraction_route=None,
            window_start=window_start,
            window_end=window_end,
        )

    # 4. UNKNOWN outcome with specific failure reason
    primary_reason = UnknownReason.INSUFFICIENT_EVIDENCE
    if UnknownReason.SOURCE_UNAVAILABLE in failure_reasons:
        primary_reason = UnknownReason.SOURCE_UNAVAILABLE
    elif UnknownReason.RETRIEVAL_FAILED in failure_reasons:
        primary_reason = UnknownReason.RETRIEVAL_FAILED
    elif UnknownReason.INCOMPLETE_SOURCE in failure_reasons:
        primary_reason = UnknownReason.INCOMPLETE_SOURCE
    elif UnknownReason.CONFLICTING_EVIDENCE in failure_reasons:
        primary_reason = UnknownReason.CONFLICTING_EVIDENCE

    # Ensure at least one auditable evidence item is present
    if not all_evidence:
        fallback_evidence = Evidence(
            source_id="DETECTOR_INSPECTION_RECORD",
            source_tier=SourceTier.TIER_3_CORROBORATION,
            url="internal://inspection/log",
            retrieved_at=datetime.now(timezone.utc),
            snippet_or_locator=f"Inspection completed with reason: {primary_reason.value}",
            failure_reason=primary_reason,
        )
        all_evidence = [fallback_evidence]

    return DetectionResult(
        fund_id=fund_id,
        status=DetectionStatus.UNKNOWN,
        confidence=None,
        evidence=all_evidence,
        suggested_extraction_route=None,
        window_start=window_start,
        window_end=window_end,
    )


def detect_distribution(
    fund_id: str,
    window_start: date,
    window_end: date,
    strategies: list[BaseDetectionStrategy] | None = None,
    http_client: HTTPClient | None = None,
    universe: UniverseRegistry | None = None,
) -> DetectionResult:
    """Evaluate whether a fund declared a distribution in the requested window [window_start, window_end].

    Args:
        fund_id: Internal fund identifier or ticker.
        window_start: Start date of the declaration window (inclusive).
        window_end: End date of the declaration window (inclusive).
        strategies: Optional list of inspection strategies. If None, uses default real-source strategies.
        http_client: Optional HTTPClient instance for network queries.
        universe: Optional UniverseRegistry instance.

    Returns:
        DetectionResult carrying status, confidence, evidence list, and recommended route.
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
        # Explicit empty strategies list -> UNKNOWN with insufficient evidence
        insufficient_evidence = Evidence(
            source_id="DETECTOR_UNCONFIGURED_PASS",
            source_tier=SourceTier.TIER_3_CORROBORATION,
            url="internal://detector/unconfigured",
            retrieved_at=datetime.now(timezone.utc),
            snippet_or_locator="No inspection strategies executed for fund.",
            failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
        )
        return DetectionResult(
            fund_id=fund_id,
            status=DetectionStatus.UNKNOWN,
            confidence=None,
            evidence=[insufficient_evidence],
            suggested_extraction_route=None,
            window_start=window_start,
            window_end=window_end,
        )

    observations: list[StrategyObservation] = []
    for strategy in strategies:
        try:
            obs = strategy.inspect(fund_id, window_start, window_end)
            observations.append(obs)
        except (
            OSError,
            ValueError,
            RuntimeError,
            TimeoutError,
            KeyError,
            TypeError,
        ) as err:
            logger.warning(
                "Strategy %s failed for fund %s: %s",
                strategy.name,
                fund_id,
                err,
            )
            error_evidence = Evidence(
                source_id=f"STRATEGY_EXECUTION_ERROR_{strategy.name}",
                source_tier=SourceTier.TIER_3_CORROBORATION,
                url=f"internal://strategy/{strategy.name}/error",
                retrieved_at=datetime.now(timezone.utc),
                snippet_or_locator=f"Execution error: {err!s}",
                failure_reason=UnknownReason.RETRIEVAL_FAILED,
            )
            observations.append(
                StrategyObservation(
                    strategy_name=strategy.name,
                    signal_type=SignalType.NO_DATA_OBSERVATION,
                    evidence=[error_evidence],
                    failure_reason=UnknownReason.RETRIEVAL_FAILED,
                    notes=f"Strategy execution error: {err!s}",
                )
            )

    return synthesize_detection_result(
        fund_id=fund_id,
        window_start=window_start,
        window_end=window_end,
        observations=observations,
    )
