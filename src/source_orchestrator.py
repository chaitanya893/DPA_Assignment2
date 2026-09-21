"""Multi-Tier Source Fallback Orchestrator for Fund Distribution Detection.

Coordinates hierarchical evaluation across:
  Tier 1: Authoritative (SEC EDGAR filings, TMX/CDS notices)
  Tier 2: Primary Unstructured (Official Sponsor distribution schedules, press releases)
  Step 3: Targeted Secondary Lookup (Newsroom releases, PDF schedules, secondary candidate URLs)
  Tier 4: Tier 3 Corroboration (Public market data cross-checks)

Strict Invariant:
  Tier 3 can NEVER independently produce DECLARED or NOT_DECLARED.
  Detailed SourceAttemptRecord tracking is maintained for all evaluated candidate sources.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from src.detector import synthesize_detection_result
from src.http_client import HTTPClient
from src.models import (
    DetectionResult,
    SourceAttemptRecord,
    SourceCoverageState,
    SourceTier,
    UnknownReason,
)
from src.strategies import (
    CalendarExpectationStrategy,
    OfficialSponsorWebStrategy,
    SECEdgarSubmissionsStrategy,
    StrategyObservation,
    TargetedLookupStrategy,
    Tier3CorroborationStrategy,
)
from src.universe_loader import UniverseFund, UniverseRegistry

logger = logging.getLogger(__name__)


@dataclass
class SourceOrchestrationResult:
    """Complete result of multi-tier source orchestration including attempt trail."""

    detection_result: DetectionResult
    source_attempts: list[SourceAttemptRecord] = field(default_factory=list)


class SourceOrchestrator:
    """Orchestrates multi-tier source discovery, acquisition, fallback, and synthesis."""

    def __init__(
        self,
        http_client: HTTPClient | None = None,
        universe: UniverseRegistry | None = None,
    ) -> None:
        self.http_client = http_client or HTTPClient()
        self.universe = universe or UniverseRegistry.from_json()

    def _get_fund(self, fund_id: str) -> UniverseFund | None:
        return self.universe.get_fund(fund_id)

    def execute(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
    ) -> tuple[DetectionResult, list[SourceAttemptRecord]]:
        """Execute multi-tier fallback for a fund and target date window.

        Hierarchy:
          1. Tier 1 Authoritative (SEC EDGAR / TMX / CDS)
          2. Tier 2 Primary Unstructured (Official sponsor web portals / schedules)
          3. Targeted Secondary Lookup (Official newsroom PRs / PDF schedules)
          4. Tier 3 Corroboration (Public market cross-check)
          5. Synthesis & Validation
        """
        if not fund_id or not isinstance(fund_id, str):
            raise ValueError("fund_id must be a non-empty string.")
        if not isinstance(window_start, date) or not isinstance(window_end, date):
            raise TypeError("window_start and window_end must be date objects.")
        if window_start > window_end:
            raise ValueError(
                f"window_start ({window_start}) cannot be after window_end ({window_end})."
            )

        fund = self._get_fund(fund_id)
        attempts: list[SourceAttemptRecord] = []
        observations: list[StrategyObservation] = []

        # Always evaluate baseline calendar expectations
        cal_strat = CalendarExpectationStrategy()
        try:
            cal_obs = cal_strat.inspect(fund_id, window_start, window_end)
            observations.append(cal_obs)
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as e:
            logger.debug("Calendar strategy note: %s", e)

        # ---------------------------------------------------------------------
        # STEP 1: Tier 1 Authoritative (SEC EDGAR for US, TMX/CDS for CA)
        # ---------------------------------------------------------------------
        tier_1_conclusive = False
        if fund and fund.country == "US" and fund.cik:
            cik_fmt = fund.cik.zfill(10)
            sec_url = f"https://data.sec.gov/submissions/CIK{cik_fmt}.json"
            sec_strat = SECEdgarSubmissionsStrategy(
                http_client=self.http_client,
                universe=self.universe,
            )
            obs_sec = sec_strat.inspect(fund_id, window_start, window_end)
            observations.append(obs_sec)

            coverage_state = SourceCoverageState.NOT_RETRIEVED
            if obs_sec.has_declaration_in_window:
                coverage_state = SourceCoverageState.COMPLETE_POSITIVE
                tier_1_conclusive = True
            elif obs_sec.window_fully_covered and not obs_sec.failure_reason:
                coverage_state = SourceCoverageState.COMPLETE_NEGATIVE
                tier_1_conclusive = True
            elif obs_sec.failure_reason in (
                UnknownReason.SOURCE_UNAVAILABLE,
                UnknownReason.RETRIEVAL_FAILED,
            ):
                coverage_state = SourceCoverageState.UNAVAILABLE
            else:
                coverage_state = SourceCoverageState.PARTIAL

            attempts.append(
                SourceAttemptRecord(
                    fund_id=fund_id,
                    source_id="sec_edgar_submissions",
                    source_tier=SourceTier.TIER_1_AUTHORITATIVE,
                    url=sec_url,
                    source_type="REGULATORY_FILING_INDEX",
                    retrieval_status="SUCCESS" if obs_sec.evidence else "FAILED",
                    retrieved_at=datetime.now(timezone.utc),
                    coverage_status=coverage_state,
                    evidence_found=obs_sec.has_declaration_in_window,
                    failure_reason=obs_sec.failure_reason,
                    notes=obs_sec.notes,
                )
            )

        # ---------------------------------------------------------------------
        # STEP 2: Tier 2 Primary Unstructured (Official Sponsor Portal)
        # ---------------------------------------------------------------------
        tier_2_conclusive = False
        sponsor_url = fund.official_source_url if fund else ""
        if not tier_1_conclusive:
            sponsor_strat = OfficialSponsorWebStrategy(
                http_client=self.http_client,
                universe=self.universe,
            )
            obs_sponsor = sponsor_strat.inspect(fund_id, window_start, window_end)
            observations.append(obs_sponsor)

            coverage_state = SourceCoverageState.NOT_RETRIEVED
            if obs_sponsor.has_declaration_in_window:
                coverage_state = SourceCoverageState.COMPLETE_POSITIVE
                tier_2_conclusive = True
            elif obs_sponsor.window_fully_covered and not obs_sponsor.failure_reason:
                coverage_state = SourceCoverageState.COMPLETE_NEGATIVE
                tier_2_conclusive = True
            elif obs_sponsor.failure_reason in (
                UnknownReason.SOURCE_UNAVAILABLE,
                UnknownReason.RETRIEVAL_FAILED,
            ):
                coverage_state = (
                    SourceCoverageState.BLOCKED
                    if "blocked" in obs_sponsor.notes.lower()
                    or "403" in obs_sponsor.notes
                    else SourceCoverageState.UNAVAILABLE
                )
            else:
                coverage_state = SourceCoverageState.PARTIAL

            attempts.append(
                SourceAttemptRecord(
                    fund_id=fund_id,
                    source_id="official_fund_sponsor_portal",
                    source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                    url=sponsor_url or "https://official.sponsor.unknown",
                    source_type="SPONSOR_PORTAL_SCHEDULE",
                    retrieval_status="SUCCESS" if obs_sponsor.evidence else "FAILED",
                    retrieved_at=datetime.now(timezone.utc),
                    coverage_status=coverage_state,
                    evidence_found=obs_sponsor.has_declaration_in_window,
                    failure_reason=obs_sponsor.failure_reason,
                    notes=obs_sponsor.notes,
                )
            )

        # ---------------------------------------------------------------------
        # STEP 3: Targeted Secondary Lookup (Newsroom PRs / PDF Schedules)
        # ---------------------------------------------------------------------
        if not (tier_1_conclusive or tier_2_conclusive):
            targeted_strat = TargetedLookupStrategy(
                http_client=self.http_client,
                universe=self.universe,
            )
            obs_targeted = targeted_strat.inspect(fund_id, window_start, window_end)
            observations.append(obs_targeted)

            coverage_state = SourceCoverageState.NOT_RETRIEVED
            if obs_targeted.has_declaration_in_window:
                coverage_state = SourceCoverageState.COMPLETE_POSITIVE
            elif obs_targeted.failure_reason in (
                UnknownReason.SOURCE_UNAVAILABLE,
                UnknownReason.RETRIEVAL_FAILED,
            ):
                coverage_state = SourceCoverageState.UNAVAILABLE
            else:
                coverage_state = SourceCoverageState.PARTIAL

            attempts.append(
                SourceAttemptRecord(
                    fund_id=fund_id,
                    source_id="targeted_secondary_lookup",
                    source_tier=SourceTier.TIER_2_PRIMARY_UNSTRUCTURED,
                    url="targeted://official_secondary_candidates",
                    source_type="SECONDARY_OFFICIAL_LOOKUP",
                    retrieval_status="SUCCESS" if obs_targeted.evidence else "FAILED",
                    retrieved_at=datetime.now(timezone.utc),
                    coverage_status=coverage_state,
                    evidence_found=obs_targeted.has_declaration_in_window,
                    failure_reason=obs_targeted.failure_reason,
                    notes=obs_targeted.notes,
                )
            )

        # ---------------------------------------------------------------------
        # STEP 4: Tier 3 Corroboration (Public Market Data Cross-Check)
        # ---------------------------------------------------------------------
        tier_3_strat = Tier3CorroborationStrategy(
            http_client=self.http_client,
            universe=self.universe,
        )
        obs_tier_3 = tier_3_strat.inspect(fund_id, window_start, window_end)
        observations.append(obs_tier_3)

        attempts.append(
            SourceAttemptRecord(
                fund_id=fund_id,
                source_id="public_market_data_corroboration",
                source_tier=SourceTier.TIER_3_CORROBORATION,
                url=f"https://finance.yahoo.com/quote/{fund.ticker if fund else fund_id}",
                source_type="THIRD_PARTY_CORROBORATION",
                retrieval_status="SUCCESS" if obs_tier_3.evidence else "FAILED",
                retrieved_at=datetime.now(timezone.utc),
                coverage_status=SourceCoverageState.PARTIAL,
                evidence_found=obs_tier_3.has_declaration_in_window,
                failure_reason=obs_tier_3.failure_reason,
                notes="Corroboration only. Never sole evidence for DECLARED or NOT_DECLARED.",
            )
        )

        # ---------------------------------------------------------------------
        # STEP 5: Synthesis
        # ---------------------------------------------------------------------
        detection_result = synthesize_detection_result(
            fund_id=fund_id,
            window_start=window_start,
            window_end=window_end,
            observations=observations,
        )

        return detection_result, attempts


def detect_distribution_with_fallback(
    fund_id: str,
    window_start: date,
    window_end: date,
    http_client: HTTPClient | None = None,
    universe: UniverseRegistry | None = None,
) -> tuple[DetectionResult, list[SourceAttemptRecord]]:
    """Convenience helper to run multi-tier fallback detection."""
    orchestrator = SourceOrchestrator(http_client=http_client, universe=universe)
    return orchestrator.execute(fund_id, window_start, window_end)
