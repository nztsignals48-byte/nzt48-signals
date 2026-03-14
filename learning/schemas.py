"""
learning/schemas.py
====================
Canonical data contracts for the NZT-48 self-learning AI.
Single source of truth — import these everywhere.
All dataclasses serialise via .to_dict() / .from_dict().
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


# ── Signal ID ────────────────────────────────────────────────────────────────

def make_signal_id(date_str: str, timestamp_str: str, ticker: str,
                   strategy_tag: str, track: str, entry_price: float) -> str:
    """Deterministic 10-char signal ID. Stable across restarts."""
    raw = f"{date_str}|{timestamp_str[:16]}|{ticker}|{strategy_tag}|{track}|{entry_price:.4f}"
    return "NZT-" + hashlib.sha256(raw.encode()).hexdigest()[:6].upper()


# ── Core Records ─────────────────────────────────────────────────────────────

@dataclass
class SignalLogRecord:
    """Written at signal-generation time. Never mutated after write."""
    signal_id:     str
    ticker:        str
    direction:     str        # LONG | SHORT
    strategy_tag:  str
    regime_tag:    str
    regime_confidence: float
    time_window:   str
    track:         str        # SCALP | INTRADAY_SWING | OVERNIGHT_SWING
    session:       str        # LSE | US | PRE | POST
    composite:     float
    entry:         float
    stop:          float
    target1:       float
    target2:       float
    net_rr:        float
    generated_at:  str        # ISO UTC
    date_str:      str        # YYYY-MM-DD
    # Feature vector
    rvol:          float = 0.0
    atr_pct:       float = 0.0
    bb_width:      float = 0.0
    rsi:           float = 0.0
    adx:           float = 0.0
    spread_bps:    float = 0.0
    liquidity_bucket: str = "NORMAL"  # HIGH|NORMAL|LOW|THIN
    risk_officer_decision: str = "APPROVE"
    sizing_hint:   str = "M"
    # Per-indicator decomposition (AEGIS 0-06: enables ablation study)
    indicator_scores: dict = field(default_factory=dict)
    # Outcome status
    outcome:       str = "PENDING"  # PENDING | RESOLVED | EXPIRED

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SignalLogRecord":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


@dataclass
class CounterfactualVariant:
    """Shadow variant for policy learning."""
    label:        str    # e.g. "stop_0.5xATR", "time_10m", "partial_0.8R"
    exit_price:   float
    pnl_r_gross:  float
    pnl_r_net:    float
    outcome:      str


@dataclass
class OutcomeRecord:
    """Written after resolution. Path-based, net of costs."""
    signal_id:     str
    ticker:        str
    direction:     str
    strategy_tag:  str
    regime_tag:    str
    time_window:   str
    track:         str
    session:       str
    entry:         float
    stop:          float
    target1:       float
    net_rr:        float
    generated_at:  str
    # Resolution
    outcome:       str    # HIT_TARGET | HIT_STOP | TIME_STOP | AMBIGUOUS | SCRATCH
    exit_price:    float  = 0.0
    pnl_r_gross:   float  = 0.0
    pnl_r_net:     float  = 0.0   # net of spread + slippage
    mfe_pct:       float  = 0.0
    mae_pct:       float  = 0.0
    duration_minutes: int = 0
    cost_bps:      float  = 0.0
    closed_at:     str    = ""
    resolution_method: str = "PATH_BASED"
    bars_used:     int    = 0
    # Counterfactuals
    counterfactuals: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "OutcomeRecord":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        if "counterfactuals" not in known:
            known["counterfactuals"] = []
        return cls(**known)


@dataclass
class EdgeBucketKey:
    strategy_tag:     str
    regime_tag:       str
    track:            str
    time_window:      str
    liquidity_bucket: str = "NORMAL"

    def to_str(self) -> str:
        return f"{self.strategy_tag}|{self.regime_tag}|{self.track}|{self.time_window}|{self.liquidity_bucket}"

    @classmethod
    def from_str(cls, s: str) -> "EdgeBucketKey":
        parts = s.split("|")
        return cls(*parts) if len(parts) == 5 else cls(*parts, "NORMAL")


@dataclass
class EdgeLedgerRecord:
    key:              str   # EdgeBucketKey.to_str()
    trades_count:     int   = 0
    win_rate:         float = 0.0
    win_rate_low:     float = 0.0   # Wilson CI lower
    win_rate_high:    float = 0.0   # Wilson CI upper
    avg_rr_gross:     float = 0.0
    avg_rr_net:       float = 0.0
    avg_duration_min: float = 0.0
    max_loss_streak:  int   = 0
    expectancy_net:   float = 0.0   # win_rate * avg_rr_net - (1-win_rate)
    confidence_score: float = 0.0   # 0-1, function of sample size + stability
    last_updated:     str   = ""
    status:           str   = "NEEDS_DATA"  # NEEDS_DATA|CALIBRATION_READY|ACTIONABLE

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MetaLearnerWeights:
    """Strategy weights output by meta-learner."""
    weights:         dict  = field(default_factory=dict)  # strategy_tag -> float
    allowed_tracks:  dict  = field(default_factory=dict)  # strategy_tag -> [tracks]
    sizing_overrides: dict = field(default_factory=dict)  # strategy_tag -> sizing_hint
    regime_tag:      str   = ""
    generated_at:    str   = ""
    evidence_summary: str  = ""
    guardrail_notes:  list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MetaLearnerWeights":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


@dataclass
class DriftReport:
    """Drift detection output."""
    detected:           bool    = False
    severity:           str     = "NONE"   # NONE|LOW|MEDIUM|HIGH|CRITICAL
    drift_type:         str     = ""       # FEATURE|RESIDUAL|HIT_RATE|REGIME
    description:        str     = ""
    affected_strategies: list   = field(default_factory=list)
    defensive_mode_triggered: bool = False
    generated_at:       str     = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExecutionQualityRecord:
    signal_id:           str
    ticker:              str
    expected_slippage_bps: float = 0.0
    actual_slippage_bps: float  = 0.0
    fill_risk_score:     float  = 0.0   # 0-1, higher = worse
    spread_bps:          float  = 0.0
    recommendation:      str    = "NORMAL"  # NORMAL|DOWNSIZE|WATCH|SKIP
    generated_at:        str    = ""

    def to_dict(self) -> dict:
        return asdict(self)
