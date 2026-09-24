"""Database package for Fund Distribution Extraction System."""

from src.database.connection import get_engine, get_session, init_db, session_scope
from src.database.models import (
    Base,
    CrawlLog,
    DetectionRun,
    DistributionComponent,
    DistributionEvent,
    DQFlag,
    EventEvidence,
    FundDetectionState,
    FundMaster,
    RawDocument,
    ReviewQueue,
    ShareClass,
    SourceRegistry,
)
from src.database.repository import DistributionRepository


def __getattr__(
    name: str,
):  # lazy: the populator imports the pipeline, which imports this package
    if name in ("DatabasePopulator", "run_population"):
        from src.database import populator

        return getattr(populator, name)
    raise AttributeError(name)


__all__ = [
    "Base",
    "FundMaster",
    "ShareClass",
    "SourceRegistry",
    "CrawlLog",
    "RawDocument",
    "DistributionEvent",
    "DistributionComponent",
    "EventEvidence",
    "DQFlag",
    "DetectionRun",
    "FundDetectionState",
    "ReviewQueue",
    "DistributionRepository",
    "DatabasePopulator",
    "get_engine",
    "get_session",
    "init_db",
    "session_scope",
    "run_population",
]
