"""Detection strategy interfaces and observation models for Layer A Atomic Detector.

Strategies produce observations (signals or evidence candidates) that are
synthesized by the core detector without inventing or assuming facts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from src.models import (
    Evidence,
    ExtractionRoute,
    UnknownReason,
)


class SignalType(str, Enum):
    """Categorization of strategy outputs."""

    TRIGGER_SIGNAL = "TRIGGER_SIGNAL"  # Internal indicator (e.g. calendar due)
    EVIDENCE_OBSERVATION = "EVIDENCE_OBSERVATION"  # Factual retrieval with evidence
    NO_DATA_OBSERVATION = "NO_DATA_OBSERVATION"  # Source checked, no declaration


@dataclass
class StrategyObservation:
    """Standardized output produced by any detection strategy inspection."""

    strategy_name: str
    signal_type: SignalType
    evidence: list[Evidence] = field(default_factory=list)
    has_declaration_in_window: bool = False
    window_fully_covered: bool = False
    suggested_route: ExtractionRoute | None = None
    failure_reason: UnknownReason | None = None
    notes: str = ""


class BaseDetectionStrategy(ABC):
    """Abstract base class for Layer A detection strategies."""

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def inspect(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
    ) -> StrategyObservation:
        """Inspect source/channel for distribution declaration in the given window.

        Args:
            fund_id: The internal fund identifier.
            window_start: Start of declaration date window (inclusive).
            window_end: End of declaration date window (inclusive).

        Returns:
            StrategyObservation containing findings and evidence.
        """


class CalendarExpectationStrategy(BaseDetectionStrategy):
    """Calendar-based expectation strategy.

    Rule: This strategy acts solely as an internal scheduling TRIGGER/SIGNAL.
    It NEVER directly declares distributions or confirms non-declarations.
    """

    def __init__(self, name: str = "CalendarExpectationStrategy") -> None:
        super().__init__(name=name)

    def inspect(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
    ) -> StrategyObservation:
        # Base implementation: emits trigger signal only.
        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.TRIGGER_SIGNAL,
            has_declaration_in_window=False,
            window_fully_covered=False,
            notes="Calendar expectation is a signal only; does not establish factual declaration.",
        )


class ChangeDetectionStrategy(BaseDetectionStrategy):
    """Monitors hash/content changes on official fund distribution pages or documents."""

    def __init__(self, name: str = "ChangeDetectionStrategy") -> None:
        super().__init__(name=name)

    def inspect(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
    ) -> StrategyObservation:
        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.TRIGGER_SIGNAL,
            has_declaration_in_window=False,
            window_fully_covered=False,
            notes="Change detection indicates potential update; requires evidence extraction.",
        )


class FilingIndexPollingStrategy(BaseDetectionStrategy):
    """Polls authoritative regulatory filing indexes (e.g. SEC EDGAR daily index)."""

    def __init__(self, name: str = "FilingIndexPollingStrategy") -> None:
        super().__init__(name=name)

    def inspect(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
    ) -> StrategyObservation:
        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.NO_DATA_OBSERVATION,
            has_declaration_in_window=False,
            window_fully_covered=False,
            failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
            notes="Filing index poller unconfigured without active source adapter.",
        )


class TargetedLookupStrategy(BaseDetectionStrategy):
    """Performs targeted query against verified primary/authoritative endpoints."""

    def __init__(self, name: str = "TargetedLookupStrategy") -> None:
        super().__init__(name=name)

    def inspect(
        self,
        fund_id: str,
        window_start: date,
        window_end: date,
    ) -> StrategyObservation:
        return StrategyObservation(
            strategy_name=self.name,
            signal_type=SignalType.NO_DATA_OBSERVATION,
            has_declaration_in_window=False,
            window_fully_covered=False,
            failure_reason=UnknownReason.INSUFFICIENT_EVIDENCE,
            notes="Targeted lookup unconfigured without active source adapter.",
        )
