"""
NZT-48 Trading System -- Core Module
=====================================
Single-source-of-truth infrastructure: canonical schemas, health tracking,
regime provision, data validation, artifact loading, replay, and universe
governance.

All delivery surfaces (PDF, Telegram, War Room, Dashboard) consume from
this module. No schema duplication allowed outside core/.
"""

from core.schemas import (
    TruthManifest,
    SignalRecord,
    PlayCard,
    DroughtReport,
    DataHealthReport,
    RegimeSnapshot,
    SystemStateReport,
    TelegramEvent,
    ScanHealth,
    OpportunityCandidate,
    ExitScore,
    ArtifactBundle,
    ProvenanceEnvelope,
    validate_artifact_file,
)
from core.scan_health import ScanHealthTracker
from core.regime_provider import RegimeProvider
from core.data_health_provider import DataHealthProvider
from core.artifact_loader import ArtifactLoader
from core.replay import ReplayEngine, ReplayResult, Divergence
from core.universe_governance import UniverseGovernance, UniverseValidationReport, validate_universe
from core.safe_math import safe_divide, clamp_confidence, clamp_return_pct
from core.provenance import (
    FreshnessChecker,
    ProvenanceRegistry,
    get_registry as get_provenance_registry,
    get_ttl_for_field,
    TTL_DEFAULTS as PROVENANCE_TTL_DEFAULTS,
)

__all__ = [
    # Schemas
    "TruthManifest",
    "SignalRecord",
    "PlayCard",
    "DroughtReport",
    "DataHealthReport",
    "RegimeSnapshot",
    "SystemStateReport",
    "TelegramEvent",
    "ScanHealth",
    "OpportunityCandidate",
    "ExitScore",
    "ArtifactBundle",
    "ProvenanceEnvelope",
    "validate_artifact_file",
    # Providers & Trackers
    "ScanHealthTracker",
    "RegimeProvider",
    "DataHealthProvider",
    "ArtifactLoader",
    "ReplayEngine",
    "ReplayResult",
    "Divergence",
    "UniverseGovernance",
    "UniverseValidationReport",
    "validate_universe",
    # Safe math (W2)
    "safe_divide",
    "clamp_confidence",
    "clamp_return_pct",
    # Provenance (W3)
    "ProvenanceEnvelope",
    "FreshnessChecker",
    "ProvenanceRegistry",
    "get_provenance_registry",
    "get_ttl_for_field",
    "PROVENANCE_TTL_DEFAULTS",
]
