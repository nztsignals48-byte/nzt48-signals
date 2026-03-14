"""
learning/expectancy_model.py
=============================
Net Expectancy Predictor.

Outputs per-signal:
- P_target_before_stop (0-1)
- ExpectedNetR (float)
- ExpectedDurationMinutes (int)
- Uncertainty (0-1)
- Decision: TRADE | WATCH | ABSTAIN

Model hierarchy:
1. Empirical lookup from Edge Ledger (primary; most interpretable)
2. Recency-weighted stats fallback
3. Prior / breakeven estimates (always available)

Abstain rule: if uncertainty > 0.65 OR ExpectedNetR < 0.0 => WATCH
Explicit TRADE requires: ExpectedNetR > 0.05 AND uncertainty < 0.60

All outputs are logged alongside the signal for future meta-model training.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("nzt48.learning.expectancy_model")

_DATA        = Path(__file__).parent.parent / "data"
_EDGE_LEDGER = _DATA / "edge_ledger.json"
_META_W      = _DATA / "meta_weights.json"

# Thresholds
TRADE_MIN_EXPECTED_NET_R  = 0.05   # must be positive expectancy
TRADE_MAX_UNCERTAINTY     = 0.60
WATCH_MAX_UNCERTAINTY     = 0.85   # above this: ABSTAIN


@dataclass
class ExpectancyOutput:
    signal_id:            str
    p_target:             float   # P(hit target before stop)
    expected_net_r:       float   # net of costs
    expected_duration_min: int
    uncertainty:          float   # 0=certain, 1=no idea
    decision:             str     # TRADE | WATCH | ABSTAIN
    why:                  str     # human-readable reason
    sample_basis:         int     # how many outcomes drove this estimate
    method:               str     # EDGE_LEDGER | RECENCY | PRIOR
    generated_at:         str

    def to_dict(self) -> dict:
        return asdict(self)


class ExpectancyModel:
    """
    Computes net expectancy for a signal using the Edge Ledger.
    Falls back to prior estimates if insufficient data.
    """

    def __init__(self):
        self._ledger_cache: dict = {}
        self._ledger_loaded_at: Optional[datetime] = None

    def _load_ledger(self) -> dict:
        """Load edge ledger, caching for 5 minutes."""
        now = datetime.now(timezone.utc)
        if (self._ledger_loaded_at is None or
                (now - self._ledger_loaded_at).total_seconds() > 300):
            if _EDGE_LEDGER.exists():
                try:
                    self._ledger_cache = json.loads(_EDGE_LEDGER.read_text())
                    self._ledger_loaded_at = now
                except Exception:
                    self._ledger_cache = {}
        return self._ledger_cache

    def _find_bucket(self, strategy_tag: str, regime_tag: str,
                     track: str, time_window: str) -> Optional[dict]:
        """Find best matching bucket in edge ledger (exact then relaxed match)."""
        ledger = self._load_ledger()
        if not ledger:
            return None

        # Exact match
        key = f"{strategy_tag}|{regime_tag}|{track}|{time_window}|NORMAL"
        if key in ledger:
            return ledger[key]

        # Relax time_window
        for k, v in ledger.items():
            parts = k.split("|")
            if len(parts) >= 3 and parts[0] == strategy_tag and parts[1] == regime_tag and parts[2] == track:
                return v

        # Relax regime
        for k, v in ledger.items():
            parts = k.split("|")
            if len(parts) >= 1 and parts[0] == strategy_tag:
                return v

        return None

    def _prior_estimate(self, net_rr: float) -> tuple[float, float, int, float, str]:
        """
        Breakeven prior: win rate needed = 1/(1+rr), prior expectancy = 0.
        Returns: p_target, expected_net_r, duration_min, uncertainty, method
        """
        if net_rr > 0:
            p_breakeven = 1.0 / (1.0 + net_rr)
        else:
            p_breakeven = 0.5
        return p_breakeven, 0.0, 120, 0.75, "PRIOR"

    def predict(self, signal_id: str, strategy_tag: str, regime_tag: str,
                track: str, time_window: str, net_rr: float,
                composite_score: float = 60.0,
                risk_officer_decision: str = "APPROVE",
                fill_risk_score: float = 0.0,
                **kwargs) -> ExpectancyOutput:
        """
        Main prediction entry point.
        """
        now_str = datetime.now(timezone.utc).isoformat()
        bucket  = self._find_bucket(strategy_tag, regime_tag, track, time_window)

        if bucket and bucket.get("trades_count", 0) >= 5:
            # Use edge ledger data
            n            = bucket["trades_count"]
            win_rate     = bucket.get("win_rate", 0.5)
            avg_rr_net   = bucket.get("avg_rr_net", 0.0)
            avg_dur      = int(bucket.get("avg_duration_min", 120))
            expectancy   = bucket.get("expectancy_net", 0.0)
            conf         = bucket.get("confidence_score", 0.3)

            # Uncertainty: inverse of confidence, penalised by fill risk
            uncertainty = round((1.0 - conf) * (1.0 + fill_risk_score * 0.3), 3)
            uncertainty = min(1.0, uncertainty)

            p_target     = win_rate
            exp_net_r    = avg_rr_net if avg_rr_net != 0.0 else expectancy
            method       = "EDGE_LEDGER"
            sample_basis = n

        elif bucket and bucket.get("trades_count", 0) >= 2:
            # Recency-weighted, low confidence
            n          = bucket["trades_count"]
            win_rate   = bucket.get("win_rate", 0.5)
            avg_dur    = int(bucket.get("avg_duration_min", 120))
            exp_net_r  = bucket.get("expectancy_net", 0.0)
            p_target   = win_rate
            uncertainty = 0.65
            method      = "RECENCY"
            sample_basis = n

        else:
            # Prior
            p_target, exp_net_r, avg_dur, uncertainty, method = self._prior_estimate(net_rr)
            sample_basis = 0

        # Composite score adjustment: above-average scores get mild boost
        if composite_score >= 75:
            p_target   = min(1.0, p_target * 1.05)
            exp_net_r  = exp_net_r + 0.02

        # Risk officer override
        if risk_officer_decision == "VETO":
            decision = "ABSTAIN"
            why = "Risk Officer VETO"
        elif risk_officer_decision == "DOWNSIZE":
            exp_net_r = exp_net_r * 0.7  # penalise for sizing uncertainty

        # Apply decision rules
        if exp_net_r >= TRADE_MIN_EXPECTED_NET_R and uncertainty <= TRADE_MAX_UNCERTAINTY:
            if risk_officer_decision != "VETO":
                decision = "TRADE"
                why = f"ExpectedNetR={exp_net_r:.2f}R, P(target)={p_target:.0%}, uncertainty={uncertainty:.0%}"
            else:
                decision = "ABSTAIN"
                why = "Risk Officer VETO"
        elif uncertainty > WATCH_MAX_UNCERTAINTY:
            decision = "ABSTAIN"
            why = f"Uncertainty too high ({uncertainty:.0%}) -- no reliable estimate available"
        elif exp_net_r < 0:
            decision = "WATCH"
            why = f"Negative expected net R ({exp_net_r:.2f}R) in current conditions"
        else:
            decision = "WATCH"
            why = f"Insufficient edge evidence (method={method}, n={sample_basis}, uncertainty={uncertainty:.0%})"

        return ExpectancyOutput(
            signal_id             = signal_id,
            p_target              = round(p_target, 4),
            expected_net_r        = round(exp_net_r, 4),
            expected_duration_min = avg_dur,
            uncertainty           = round(uncertainty, 3),
            decision              = decision,
            why                   = why,
            sample_basis          = sample_basis,
            method                = method,
            generated_at          = now_str,
        )

    def predict_batch(self, signals: list[dict]) -> list[ExpectancyOutput]:
        """Predict for a list of signal dicts."""
        return [
            self.predict(
                signal_id             = s.get("signal_id", ""),
                strategy_tag          = s.get("strategy_tag", ""),
                regime_tag            = s.get("regime_tag", ""),
                track                 = s.get("track", "INTRADAY_SWING"),
                time_window           = s.get("time_window", ""),
                net_rr                = s.get("net_rr", 2.0),
                composite_score       = s.get("composite", 60.0),
                risk_officer_decision = s.get("risk_officer_decision", "APPROVE"),
                fill_risk_score       = s.get("fill_risk_score", 0.0),
            )
            for s in signals
        ]


# Module singleton
_model: Optional[ExpectancyModel] = None

def get_expectancy_model() -> ExpectancyModel:
    global _model
    if _model is None:
        _model = ExpectancyModel()
    return _model
