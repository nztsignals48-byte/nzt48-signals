"""Config Management & Feature Flags — Book 71.

Parameter governance: every tunable parameter has a defined range,
owner, and change protocol. Feature flags enable gradual rollout
of new functionality without full redeploy.

Rules:
  1. No parameter changes > 20% per cycle (Book 190)
  2. Every parameter has a [min, max] allowable range
  3. Changes must be logged with timestamp and reason
  4. Feature flags are boolean — ON or OFF, no partial states
  5. Critical flags require human approval to change

Usage:
    from python_brain.risk.feature_flags import (
        FeatureFlagManager, ParameterGovernor,
    )

    flags = FeatureFlagManager()
    if flags.is_enabled("overnight_risk"):
        run_overnight_risk_check()

    gov = ParameterGovernor()
    ok = gov.propose_change("kelly_max", current=0.05, proposed=0.06, reason="Ouroboros")
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("feature_flags")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))


# ═══════════════════════════════════════════════════════════════════════
# Feature Flags
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class FeatureFlag:
    """A single feature flag."""
    name: str
    enabled: bool = True
    description: str = ""
    requires_approval: bool = False  # Human must approve toggle
    added_date: str = ""


# Default feature flags for all new modules
DEFAULT_FLAGS: Dict[str, FeatureFlag] = {
    "overnight_risk": FeatureFlag("overnight_risk", True, "Overnight gap risk filtering in bridge.py"),
    "regime_matrix": FeatureFlag("regime_matrix", True, "Strategy-regime matrix filtering"),
    "drawdown_monitor": FeatureFlag("drawdown_monitor", True, "5-phase drawdown recovery sizing"),
    "correlation_tracker": FeatureFlag("correlation_tracker", True, "EWMA correlation position sizing"),
    "vol_targeting": FeatureFlag("vol_targeting", True, "Volatility-adjusted Kelly sizing"),
    "calendar_anomalies": FeatureFlag("calendar_anomalies", True, "Calendar effect confidence modifiers"),
    "conformal_prediction": FeatureFlag("conformal_prediction", False, "Conformal prediction sizing — needs calibration"),
    "bayesian_aggregation": FeatureFlag("bayesian_aggregation", False, "Bayesian signal combination — needs track record"),
    "hmm_regime": FeatureFlag("hmm_regime", True, "HMM Student-t regime detection in nightly"),
    "mfe_mae_tracking": FeatureFlag("mfe_mae_tracking", True, "MFE/MAE analysis in nightly"),
    "lifecycle_sprt": FeatureFlag("lifecycle_sprt", True, "Strategy lifecycle SPRT evaluation"),
    "monte_carlo": FeatureFlag("monte_carlo", True, "Monte Carlo simulation in nightly"),
    "duckdb_ingestion": FeatureFlag("duckdb_ingestion", False, "DuckDB WAL ingestion — requires duckdb package"),
    "health_monitor": FeatureFlag("health_monitor", True, "15-check health monitor"),
    "validation_gates": FeatureFlag("validation_gates", True, "DSR/PBO/CPCV validation in nightly"),
    "edge_forensics": FeatureFlag("edge_forensics", True, "Per-trade edge attribution"),
    "telegram_alerts": FeatureFlag("telegram_alerts", False, "Telegram notifications — needs bot token"),
    "shadow_trading": FeatureFlag("shadow_trading", False, "A/B testing framework — not yet wired"),
    "alpha_factory": FeatureFlag("alpha_factory", True, "Formulaic alpha signals in bridge.py"),
    "pairs_trading": FeatureFlag("pairs_trading", False, "Cointegration pairs — needs dual-ticker data"),
    "lead_lag": FeatureFlag("lead_lag", False, "Cross-market lead-lag — needs US data"),
    "nav_arbitrage": FeatureFlag("nav_arbitrage", False, "NAV premium/discount — needs iNAV data"),
    "rebalancing_flow": FeatureFlag("rebalancing_flow", True, "ETP rebalancing flow prediction"),
    "vol_compression": FeatureFlag("vol_compression", True, "Squeeze breakout detection"),
    "circuit_breakers": FeatureFlag("circuit_breakers", True, "Subsystem circuit breakers"),
    "ouroboros_learning": FeatureFlag("ouroboros_learning", False, "Ouroboros auto-parameter updates", requires_approval=True),
    "live_trading": FeatureFlag("live_trading", False, "Submit real orders to IBKR", requires_approval=True),
}


class FeatureFlagManager:
    """Manage feature flags with persistence."""

    def __init__(self, flags_path: Optional[Path] = None):
        self._flags = dict(DEFAULT_FLAGS)
        self._path = flags_path or (DATA_DIR / "feature_flags.json")
        self._load()

    def _load(self):
        """Load persisted flag overrides."""
        if self._path.exists():
            try:
                with open(self._path) as f:
                    overrides = json.load(f)
                for name, enabled in overrides.items():
                    if name in self._flags:
                        self._flags[name].enabled = enabled
            except (json.JSONDecodeError, IOError):
                pass

    def _save(self):
        """Persist flag states."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {name: f.enabled for name, f in self._flags.items()}
        with open(self._path, "w") as f:
            json.dump(data, f, indent=2)

    def is_enabled(self, name: str) -> bool:
        flag = self._flags.get(name)
        return flag.enabled if flag else False

    def enable(self, name: str) -> bool:
        flag = self._flags.get(name)
        if flag is None:
            return False
        if flag.requires_approval:
            log.warning("FLAG %s: requires human approval to enable", name)
            return False
        flag.enabled = True
        self._save()
        log.info("FLAG %s: ENABLED", name)
        return True

    def disable(self, name: str) -> bool:
        flag = self._flags.get(name)
        if flag is None:
            return False
        flag.enabled = False
        self._save()
        log.info("FLAG %s: DISABLED", name)
        return True

    def status(self) -> Dict[str, bool]:
        return {name: f.enabled for name, f in self._flags.items()}

    def enabled_count(self) -> int:
        return sum(1 for f in self._flags.values() if f.enabled)


# ═══════════════════════════════════════════════════════════════════════
# Parameter Governor
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ParamRange:
    """Allowable range for a tunable parameter."""
    min_val: float
    max_val: float
    max_change_pct: float = 20.0  # Max % change per cycle


# Parameter governance registry
PARAM_RANGES: Dict[str, ParamRange] = {
    "kelly_max": ParamRange(0.005, 0.10, 20.0),
    "confidence_floor": ParamRange(30, 80, 15.0),
    "chandelier_atr_mult": ParamRange(1.0, 4.0, 20.0),
    "portfolio_heat_limit_pct": ParamRange(3.0, 15.0, 20.0),
    "max_simultaneous_positions": ParamRange(1, 5, 50.0),
    "daily_trade_limit": ParamRange(1, 20, 30.0),
    "slippage_assumption_pct": ParamRange(0.1, 2.0, 25.0),
    "cash_buffer_pct": ParamRange(10.0, 50.0, 20.0),
}


@dataclass
class ChangeProposal:
    """A proposed parameter change."""
    param: str
    current: float
    proposed: float
    change_pct: float
    approved: bool
    reason: str
    timestamp: float = 0.0


class ParameterGovernor:
    """Enforce parameter change rules."""

    def __init__(self):
        self._history: List[ChangeProposal] = []

    def propose_change(
        self,
        param: str,
        current: float,
        proposed: float,
        reason: str = "",
    ) -> ChangeProposal:
        """Evaluate a proposed parameter change against governance rules."""
        ranges = PARAM_RANGES.get(param)
        change_pct = abs(proposed - current) / max(abs(current), 1e-10) * 100

        proposal = ChangeProposal(
            param=param, current=current, proposed=proposed,
            change_pct=round(change_pct, 1), approved=False,
            reason=reason, timestamp=time.time(),
        )

        # Check range
        if ranges:
            if proposed < ranges.min_val or proposed > ranges.max_val:
                proposal.reason = f"REJECTED: {proposed} outside [{ranges.min_val}, {ranges.max_val}]"
                log.warning("PARAM_GOV: %s", proposal.reason)
                self._history.append(proposal)
                return proposal

            if change_pct > ranges.max_change_pct:
                proposal.reason = f"REJECTED: {change_pct:.0f}% change > {ranges.max_change_pct}% max"
                log.warning("PARAM_GOV: %s = %s", param, proposal.reason)
                self._history.append(proposal)
                return proposal

        proposal.approved = True
        proposal.reason = f"APPROVED: {current} → {proposed} ({change_pct:.0f}% change)"
        log.info("PARAM_GOV: %s %s", param, proposal.reason)
        self._history.append(proposal)
        return proposal

    def recent_changes(self, n: int = 10) -> List[ChangeProposal]:
        return self._history[-n:]
