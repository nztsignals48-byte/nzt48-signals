"""
NZT-48 Trading System -- Canonical Schemas
============================================
Single-source-of-truth dataclass definitions for every structured object
that flows through the system. All delivery surfaces (PDF, Telegram,
War Room, Dashboard) MUST consume these schemas -- no ad-hoc dicts.

Every schema includes:
  - __post_init__ validation (required fields, range checks)
  - to_dict() for JSON serialisation
  - from_dict() classmethod for deserialisation
  - Sensible defaults where applicable

Usage:
    from core.schemas import SignalRecord, PlayCard, TruthManifest
    signal = SignalRecord.from_dict(json.load(open("signal.json")))
    assert signal.to_dict() == json.load(open("signal.json"))
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("nzt48.core.schemas")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    """ISO 8601 UTC timestamp string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp a numeric value to [lo, hi]."""
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# TruthManifest
# ---------------------------------------------------------------------------

@dataclass
class TruthManifest:
    """Cryptographic truth anchor for every artifact bundle.

    Every generated output (PDF, Telegram message, dashboard payload)
    carries a TruthManifest so that downstream consumers can verify
    that they are reading from the same engine run and the same plays.

    Fields:
        run_id:         Unique identifier for this engine run (UUID or timestamp-based).
        plays_hash:     SHA-256 hex digest of the canonical plays JSON (sorted keys).
        config_hash:    SHA-256 hex digest of the active settings.yaml at run time.
        engine_version: Semantic version string of the engine (e.g. "2.4.1").
        artifact_dir:   Absolute path to the artifact directory for this session.
        generated_at:   ISO 8601 UTC timestamp when the manifest was created.
    """
    run_id: str = ""
    plays_hash: str = ""
    config_hash: str = ""
    engine_version: str = ""
    artifact_dir: str = ""
    generated_at: str = field(default_factory=_utcnow_iso)

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("TruthManifest.run_id is required")
        if not self.plays_hash:
            raise ValueError("TruthManifest.plays_hash is required")
        if not self.engine_version:
            raise ValueError("TruthManifest.engine_version is required")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> TruthManifest:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# SignalRecord
# ---------------------------------------------------------------------------

@dataclass
class SignalRecord:
    """Complete signal record written to the signal log and artifact bundle.

    This is the full-fidelity record -- every field that was known at signal
    generation time. PlayCard is a subset of this for display purposes.
    """
    signal_id: str = ""
    timestamp_utc: str = ""
    timestamp_uk: str = ""
    session: str = ""
    ticker: str = ""
    direction: str = ""             # LONG | SHORT
    tier: str = ""                  # T1_CONVICTION | T2_SWING | T3_SPECULATIVE
    decision: str = ""              # FIRE | WATCH | BLOCK
    strategy_tag: str = ""          # e.g. "S15_DAILY_TARGET"
    score: float = 0.0              # Composite score 0-100
    regime_tag: str = ""            # e.g. "TRENDING_UP_STRONG"
    regime_confidence: float = 0.0  # 0.0-1.0
    entry: float = 0.0
    stop: float = 0.0
    target1: float = 0.0
    target2: float = 0.0
    rr_net: float = 0.0            # Reward-risk ratio after costs
    spread_bps: float = 0.0
    slippage_bps: float = 0.0
    data_health_status: str = ""    # PASS | WARN | FAIL
    reliability_score: float = 0.0  # 0.0-1.0
    rvol: float = 0.0
    atr_pct: float = 0.0
    rsi: float = 50.0
    macd_hist: float = 0.0
    ema_alignment: int = 0          # 0-8
    adx: float = 0.0
    setup_type: str = ""            # e.g. "VWAP_RECLAIM", "ORB_BREAKOUT"
    track: str = ""                 # MOMENTUM | MEAN_REVERSION | BREAKOUT
    why: str = ""                   # Human-readable signal rationale
    execution_plan: str = ""        # Detailed execution instructions
    risk_officer_decision: str = "" # APPROVED | BLOCKED | REDUCED
    truth_manifest: Optional[dict] = None

    def __post_init__(self) -> None:
        if not self.signal_id:
            raise ValueError("SignalRecord.signal_id is required")
        if not self.ticker:
            raise ValueError("SignalRecord.ticker is required")
        if self.direction and self.direction not in ("LONG", "SHORT"):
            raise ValueError(f"SignalRecord.direction must be LONG or SHORT, got '{self.direction}'")
        if self.score < 0 or self.score > 100:
            raise ValueError(f"SignalRecord.score must be 0-100, got {self.score}")
        if self.regime_confidence < 0.0 or self.regime_confidence > 1.0:
            self.regime_confidence = _clamp(self.regime_confidence, 0.0, 1.0)
        if self.reliability_score < 0.0 or self.reliability_score > 1.0:
            self.reliability_score = _clamp(self.reliability_score, 0.0, 1.0)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> SignalRecord:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# PlayCard
# ---------------------------------------------------------------------------

@dataclass
class PlayCard:
    """Display-ready play card consumed by War Room, Telegram, and PDF generators.

    This is the subset of SignalRecord that is safe and useful for display.
    All monetary values are in the native currency of the ticker.
    """
    ticker: str = ""
    direction: str = ""
    stars: int = 0                  # 1-5 quality rating
    composite_score: float = 0.0    # 0-100
    strategy_tag: str = ""
    label: str = ""                 # Human-friendly label, e.g. "DAILY TARGET"
    entry: float = 0.0
    stop: float = 0.0
    target1: float = 0.0
    target2: float = 0.0
    rr: float = 0.0                 # Reward:risk ratio
    atr_pct: float = 0.0
    rvol: float = 0.0
    setup_type: str = ""
    track: str = ""
    tier: str = ""
    decision: str = ""
    reasons: list[str] = field(default_factory=list)
    sizing_hint: str = ""           # e.g. "FULL", "HALF", "QUARTER"
    execution_plan: str = ""
    exit_score: float = 0.0         # 0-100 kill urgency
    why: str = ""
    truth_manifest: Optional[dict] = None

    def __post_init__(self) -> None:
        if not self.ticker:
            raise ValueError("PlayCard.ticker is required")
        if self.stars < 0 or self.stars > 5:
            self.stars = int(_clamp(self.stars, 0, 5))
        if self.composite_score < 0 or self.composite_score > 100:
            self.composite_score = _clamp(self.composite_score, 0.0, 100.0)
        if self.exit_score < 0 or self.exit_score > 100:
            self.exit_score = _clamp(self.exit_score, 0.0, 100.0)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> PlayCard:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# DroughtReport
# ---------------------------------------------------------------------------

@dataclass
class DroughtReport:
    """Generated when the engine finds zero qualifying plays in a session.

    Provides diagnostic information to understand WHY nothing qualified
    and what knob adjustments might help.
    """
    is_drought: bool = True
    closest_misses: list[dict] = field(default_factory=list)
    recommended_knobs: list[str] = field(default_factory=list)
    blockers_summary: str = ""
    timestamp: str = field(default_factory=_utcnow_iso)

    def __post_init__(self) -> None:
        if not isinstance(self.is_drought, bool):
            raise ValueError("DroughtReport.is_drought must be a boolean")
        if not isinstance(self.closest_misses, list):
            raise ValueError("DroughtReport.closest_misses must be a list")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> DroughtReport:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# DataHealthReport
# ---------------------------------------------------------------------------

@dataclass
class DataHealthReport:
    """Aggregated data health status across the ticker universe.

    Produced by DataHealthProvider, consumed by all delivery surfaces
    to show a data quality badge.
    """
    status: str = "UNKNOWN"         # PASS | WARN | FAIL | UNKNOWN
    tickers_checked: int = 0
    tickers_passed: int = 0
    tickers_failed: int = 0
    per_ticker: dict = field(default_factory=dict)  # ticker -> {"status": str, "reasons": list}
    provider: str = "yfinance"
    data_as_of: str = ""
    staleness_seconds: float = 0.0

    def __post_init__(self) -> None:
        valid_statuses = ("PASS", "WARN", "FAIL", "UNKNOWN")
        if self.status not in valid_statuses:
            raise ValueError(f"DataHealthReport.status must be one of {valid_statuses}, got '{self.status}'")
        if self.tickers_checked < 0:
            raise ValueError("DataHealthReport.tickers_checked must be >= 0")
        if self.tickers_passed < 0:
            raise ValueError("DataHealthReport.tickers_passed must be >= 0")
        if self.tickers_failed < 0:
            raise ValueError("DataHealthReport.tickers_failed must be >= 0")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> DataHealthReport:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# RegimeSnapshot
# ---------------------------------------------------------------------------

@dataclass
class RegimeSnapshot:
    """Point-in-time snapshot of the market regime.

    Produced by RegimeProvider, consumed by all downstream components
    that need to know the current regime state.
    """
    tag: str = "RANGE_BOUND"
    confidence: float = 0.0         # 0.0-1.0
    evidence: dict = field(default_factory=dict)
    vix: float = 0.0
    spx_trend: str = ""             # UP | DOWN | FLAT
    timestamp: str = field(default_factory=_utcnow_iso)

    def __post_init__(self) -> None:
        valid_tags = (
            "TRENDING_UP_STRONG", "TRENDING_UP_MOD",
            "TRENDING_DOWN_STRONG", "TRENDING_DOWN_MOD",
            "RANGE_BOUND", "HIGH_VOLATILITY", "RISK_OFF", "SHOCK",
            "UNKNOWN",
        )
        if self.tag not in valid_tags:
            raise ValueError(f"RegimeSnapshot.tag must be one of {valid_tags}, got '{self.tag}'")
        if self.confidence < 0.0 or self.confidence > 1.0:
            self.confidence = _clamp(self.confidence, 0.0, 1.0)
        if self.vix < 0:
            raise ValueError(f"RegimeSnapshot.vix must be >= 0, got {self.vix}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> RegimeSnapshot:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# SystemStateReport
# ---------------------------------------------------------------------------

@dataclass
class SystemStateReport:
    """Overall system health snapshot for dashboards and alerting.

    Combines scan health, data health, position state, and engine metrics
    into a single report.
    """
    state: str = "OK"               # OK | DEGRADED | HALTED
    mode: str = "PAPER"             # PAPER | LIVE
    reasons: list[str] = field(default_factory=list)
    tick_count: int = 0
    last_tick_age_seconds: float = 0.0
    data_freshness_seconds: float = 0.0
    open_positions: int = 0
    daily_pnl_pct: float = 0.0
    consecutive_losses: int = 0
    memory_mb: float = 0.0
    config_hash: str = ""
    engine_version: str = ""

    def __post_init__(self) -> None:
        valid_states = ("OK", "DEGRADED", "HALTED")
        if self.state not in valid_states:
            raise ValueError(f"SystemStateReport.state must be one of {valid_states}, got '{self.state}'")
        if self.tick_count < 0:
            raise ValueError("SystemStateReport.tick_count must be >= 0")
        if self.consecutive_losses < 0:
            raise ValueError("SystemStateReport.consecutive_losses must be >= 0")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> SystemStateReport:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# TelegramEvent
# ---------------------------------------------------------------------------

@dataclass
class TelegramEvent:
    """Audit trail entry for every Telegram send attempt.

    Whether the message was actually sent, suppressed by rate limiting,
    deduped, or blocked by a gate -- we log it here.
    """
    timestamp: str = field(default_factory=_utcnow_iso)
    action: str = "SENT"            # SENT | SUPPRESSED | RATE_LIMITED | DEDUPED | GATE_FAILED
    label: str = ""
    ticker: str = ""
    content_hash: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        valid_actions = ("SENT", "SUPPRESSED", "RATE_LIMITED", "DEDUPED", "GATE_FAILED")
        if self.action not in valid_actions:
            raise ValueError(f"TelegramEvent.action must be one of {valid_actions}, got '{self.action}'")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> TelegramEvent:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# ScanHealth
# ---------------------------------------------------------------------------

@dataclass
class ScanHealth:
    """Heartbeat health snapshot of the scan engine.

    Tracks tick counts, signal emission, errors, and overall state.
    The ScanHealthTracker (core/scan_health.py) produces these.
    """
    tick_count: int = 0
    engine_runs: int = 0
    signals_emitted: int = 0
    signals_logged: int = 0
    last_success_ts: str = ""
    last_error_ts: str = ""
    last_error_msg: str = ""
    state: str = "OK"               # OK | DEGRADED | HALTED
    uptime_seconds: float = 0.0

    def __post_init__(self) -> None:
        valid_states = ("OK", "DEGRADED", "HALTED")
        if self.state not in valid_states:
            raise ValueError(f"ScanHealth.state must be one of {valid_states}, got '{self.state}'")
        if self.tick_count < 0:
            raise ValueError("ScanHealth.tick_count must be >= 0")
        if self.engine_runs < 0:
            raise ValueError("ScanHealth.engine_runs must be >= 0")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ScanHealth:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# OpportunityCandidate
# ---------------------------------------------------------------------------

@dataclass
class OpportunityCandidate:
    """A scored candidate from the opportunity scanner.

    Used by S15 (2% Daily Target) to rank the universe and pick
    the single best play of the day.
    """
    ticker: str = ""
    direction: str = "LONG"
    atr_pct: float = 0.0
    spread_bps: float = 0.0
    round_trip_cost_pct: float = 0.0
    net_target_pct: float = 0.0
    feasibility_score: float = 0.0   # 0.0-1.0
    expected_net_r: float = 0.0
    p_target: float = 0.0           # Probability of hitting target (0.0-1.0)
    uncertainty: float = 0.0        # 0.0-1.0
    decision: str = ""              # FIRE | WATCH | SKIP
    execution_plan: str = ""
    why: str = ""

    def __post_init__(self) -> None:
        if not self.ticker:
            raise ValueError("OpportunityCandidate.ticker is required")
        if self.direction not in ("LONG", "SHORT"):
            raise ValueError(f"OpportunityCandidate.direction must be LONG or SHORT, got '{self.direction}'")
        if self.feasibility_score < 0.0 or self.feasibility_score > 1.0:
            self.feasibility_score = _clamp(self.feasibility_score, 0.0, 1.0)
        if self.p_target < 0.0 or self.p_target > 1.0:
            self.p_target = _clamp(self.p_target, 0.0, 1.0)
        if self.uncertainty < 0.0 or self.uncertainty > 1.0:
            self.uncertainty = _clamp(self.uncertainty, 0.0, 1.0)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> OpportunityCandidate:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# ExitScore
# ---------------------------------------------------------------------------

@dataclass
class ExitScore:
    """Real-time exit urgency assessment for an open position.

    Higher exit_score = more urgently should be closed.
    kill_conditions are the specific triggers that are active.
    """
    position_id: str = ""
    ticker: str = ""
    current_r: float = 0.0
    exit_score: float = 0.0          # 0-100 (100 = SELL NOW)
    kill_conditions: list[str] = field(default_factory=list)
    sell_intent: str = ""            # HOLD | REDUCE | CLOSE
    reasoning: str = ""

    def __post_init__(self) -> None:
        if not self.ticker:
            raise ValueError("ExitScore.ticker is required")
        if self.exit_score < 0 or self.exit_score > 100:
            self.exit_score = _clamp(self.exit_score, 0.0, 100.0)
        valid_intents = ("HOLD", "REDUCE", "CLOSE", "")
        if self.sell_intent not in valid_intents:
            raise ValueError(f"ExitScore.sell_intent must be one of {valid_intents}, got '{self.sell_intent}'")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ExitScore:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# ArtifactBundle
# ---------------------------------------------------------------------------

@dataclass
class ArtifactBundle:
    """Complete artifact bundle for a single session.

    This is the top-level container that the ArtifactLoader assembles
    from disk. Every delivery surface consumes one of these.
    """
    date: str = ""
    session: str = ""
    plays: list[dict] = field(default_factory=list)
    peer_plays: list[dict] = field(default_factory=list)
    full_scan: list[dict] = field(default_factory=list)
    regime: Optional[dict] = None
    data_health: Optional[dict] = None
    system_state: Optional[dict] = None
    drought: Optional[dict] = None
    opportunity: list[dict] = field(default_factory=list)
    truth_manifest: Optional[dict] = None

    def __post_init__(self) -> None:
        if not self.date:
            raise ValueError("ArtifactBundle.date is required")
        if not self.session:
            raise ValueError("ArtifactBundle.session is required")
        if not isinstance(self.plays, list):
            raise ValueError("ArtifactBundle.plays must be a list")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ArtifactBundle:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def get_plays_as_cards(self) -> list[PlayCard]:
        """Deserialise plays dicts into PlayCard objects."""
        cards = []
        for p in self.plays:
            try:
                cards.append(PlayCard.from_dict(p))
            except (ValueError, TypeError) as e:
                logger.warning("Skipping malformed play: %s", e)
        return cards

    def get_regime_snapshot(self) -> Optional[RegimeSnapshot]:
        """Deserialise regime dict into RegimeSnapshot."""
        if self.regime is None:
            return None
        try:
            return RegimeSnapshot.from_dict(self.regime)
        except (ValueError, TypeError) as e:
            logger.warning("Failed to parse regime snapshot: %s", e)
            return None

    def get_data_health_report(self) -> Optional[DataHealthReport]:
        """Deserialise data_health dict into DataHealthReport."""
        if self.data_health is None:
            return None
        try:
            return DataHealthReport.from_dict(self.data_health)
        except (ValueError, TypeError) as e:
            logger.warning("Failed to parse data health report: %s", e)
            return None


# ---------------------------------------------------------------------------
# ProvenanceEnvelope (W3: Provenance & Freshness Tracking)
# ---------------------------------------------------------------------------

@dataclass
class ProvenanceEnvelope:
    """Metadata envelope that wraps any data field with provenance info.

    Every data point flowing through the system can carry one of these
    to declare where the data came from, when it was fetched, and how
    long it should be considered fresh.

    Fields:
        field_name:  Logical name of the data field (e.g. "vix", "price.QQQ3.L").
        provider:    Data source identifier (e.g. "yfinance", "polygon", "fallback").
        as_of:       ISO 8601 UTC timestamp when the data was fetched/computed.
        as_of_epoch: Unix epoch seconds of as_of (for fast arithmetic).
        ttl_seconds: Time-to-live in seconds; data is stale after as_of + ttl.
        value:       The actual data value (any type -- float, dict, DataFrame ref, etc.).
        stale:       Whether the data has exceeded its TTL (set by FreshnessChecker).
    """
    field_name: str = ""
    provider: str = "unknown"
    as_of: str = field(default_factory=_utcnow_iso)
    as_of_epoch: float = 0.0
    ttl_seconds: int = 300          # 5 min default
    value: Any = None
    stale: bool = False

    def __post_init__(self) -> None:
        if not self.field_name:
            raise ValueError("ProvenanceEnvelope.field_name is required")
        if self.ttl_seconds < 0:
            raise ValueError(f"ProvenanceEnvelope.ttl_seconds must be >= 0, got {self.ttl_seconds}")
        # Auto-compute epoch if not provided
        if self.as_of_epoch <= 0.0 and self.as_of:
            try:
                dt = datetime.fromisoformat(self.as_of.replace("Z", "+00:00"))
                self.as_of_epoch = dt.timestamp()
            except (ValueError, TypeError):
                self.as_of_epoch = datetime.now(timezone.utc).timestamp()

    def is_fresh(self, now_epoch: float = 0.0) -> bool:
        """Check if this envelope's data is still within TTL.

        Args:
            now_epoch: Current time as unix epoch. If 0, uses time.time().

        Returns:
            True if data is fresh, False if stale.
        """
        if now_epoch <= 0.0:
            import time as _time
            now_epoch = _time.time()
        return (now_epoch - self.as_of_epoch) < self.ttl_seconds

    def age_seconds(self, now_epoch: float = 0.0) -> float:
        """Return the age of this data in seconds."""
        if now_epoch <= 0.0:
            import time as _time
            now_epoch = _time.time()
        return max(0.0, now_epoch - self.as_of_epoch)

    def to_dict(self) -> dict:
        return {
            "field_name": self.field_name,
            "provider": self.provider,
            "as_of": self.as_of,
            "as_of_epoch": self.as_of_epoch,
            "ttl_seconds": self.ttl_seconds,
            "stale": self.stale,
            # Deliberately omit 'value' -- it may be a DataFrame or other
            # non-serialisable type. Callers serialise values separately.
        }

    @classmethod
    def from_dict(cls, d: dict) -> ProvenanceEnvelope:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Artifact file validation
# ---------------------------------------------------------------------------

def validate_artifact_file(path: str) -> dict:
    """Load a JSON artifact file and validate it against the appropriate schema.

    Attempts to determine the schema from the file name or content structure
    and validates accordingly.

    Args:
        path: Filesystem path to a JSON artifact file.

    Returns:
        dict with keys:
            valid (bool): Whether the artifact passed validation.
            schema (str): Name of the matched schema, or "unknown".
            errors (list[str]): Validation error messages (empty if valid).
            data (dict|list|None): The parsed JSON data, or None on parse failure.

    Raises:
        Nothing -- all errors are captured in the return dict.
    """
    result: dict[str, Any] = {
        "valid": False,
        "schema": "unknown",
        "errors": [],
        "data": None,
    }

    p = Path(path)
    if not p.exists():
        result["errors"].append(f"File not found: {path}")
        return result

    if not p.suffix.lower() == ".json":
        result["errors"].append(f"Expected .json file, got '{p.suffix}'")
        return result

    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        result["data"] = data
    except json.JSONDecodeError as e:
        result["errors"].append(f"JSON parse error: {e}")
        return result
    except Exception as e:
        result["errors"].append(f"File read error: {e}")
        return result

    # Determine schema from filename conventions or content keys
    fname = p.stem.lower()
    schema_map = {
        "plays": ("PlayCard", PlayCard),
        "signals": ("SignalRecord", SignalRecord),
        "regime": ("RegimeSnapshot", RegimeSnapshot),
        "data_health": ("DataHealthReport", DataHealthReport),
        "system_state": ("SystemStateReport", SystemStateReport),
        "drought": ("DroughtReport", DroughtReport),
        "opportunity": ("OpportunityCandidate", OpportunityCandidate),
        "scan_health": ("ScanHealth", ScanHealth),
        "manifest": ("TruthManifest", TruthManifest),
        "telegram_events": ("TelegramEvent", TelegramEvent),
        "exit_scores": ("ExitScore", ExitScore),
    }

    matched_schema_name = "unknown"
    matched_cls = None
    for key, (name, cls) in schema_map.items():
        if key in fname:
            matched_schema_name = name
            matched_cls = cls
            break

    result["schema"] = matched_schema_name

    if matched_cls is None:
        # Try to validate as ArtifactBundle if it has date+session keys
        if isinstance(data, dict) and "date" in data and "session" in data:
            matched_schema_name = "ArtifactBundle"
            matched_cls = ArtifactBundle
            result["schema"] = matched_schema_name
        else:
            result["errors"].append(
                f"Cannot determine schema for '{p.name}'. "
                "Name does not match any known schema pattern."
            )
            return result

    # Validate
    errors = []
    if isinstance(data, list):
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                errors.append(f"Item [{i}] is not a dict")
                continue
            try:
                matched_cls.from_dict(item)
            except (ValueError, TypeError) as e:
                errors.append(f"Item [{i}]: {e}")
    elif isinstance(data, dict):
        try:
            matched_cls.from_dict(data)
        except (ValueError, TypeError) as e:
            errors.append(str(e))
    else:
        errors.append(f"Expected dict or list, got {type(data).__name__}")

    result["errors"] = errors
    result["valid"] = len(errors) == 0
    return result
