"""
learning/meta_learner.py
==========================
Meta-Learner: sets dynamic strategy weights based on Edge Ledger evidence.

Rules:
- Weights change max +/-10% per week
- No strategy > max_allocation cap
- Minimum sample size before increasing weight
- Defensive mode (from drift) forces weight redistribution to safer strategies

Reads:  data/edge_ledger.json, data/meta_weights.json, data/drift_reports.jsonl
Writes: data/meta_weights.json
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from learning.schemas import MetaLearnerWeights

logger = logging.getLogger("nzt48.learning.meta_learner")

_DATA          = Path(__file__).parent.parent / "data"
_EDGE_LEDGER   = _DATA / "edge_ledger.json"
_META_WEIGHTS  = _DATA / "meta_weights.json"
_DRIFT_REPORTS = _DATA / "drift_reports.jsonl"

# Config
MAX_WEIGHT_CHANGE_PER_CYCLE = 0.10   # +/-10% per update
MAX_SINGLE_STRATEGY_WEIGHT  = 0.40   # no strategy > 40%
MIN_SAMPLE_FOR_BOOST        = 15     # need 15+ outcomes to increase weight
DEFAULT_WEIGHTS = {
    "TREND_MOMENTUM_CTA":    0.20,
    "BREAKOUT":              0.15,
    "MEAN_REVERSION":        0.10,
    "SECTOR_ROTATION":       0.10,
    "EARNINGS_PLAY":         0.10,
    "VOL_CRUSH":             0.10,
    "PAIRS_TRADE":           0.10,
    "MACRO_REGIME":          0.10,
    "REGIME_TREND":          0.05,
}
# "Safer" strategies for defensive mode
SAFE_STRATEGIES = {"MEAN_REVERSION", "SECTOR_ROTATION", "MACRO_REGIME"}


class MetaLearner:
    """
    Evidence-based capital allocator across strategies.
    Conservative: weight changes are bounded and logged.
    """

    def load_current(self) -> MetaLearnerWeights:
        if _META_WEIGHTS.exists():
            try:
                d = json.loads(_META_WEIGHTS.read_text())
                return MetaLearnerWeights.from_dict(d)
            except Exception:
                pass
        return MetaLearnerWeights(
            weights        = dict(DEFAULT_WEIGHTS),
            allowed_tracks = {s: ["SCALP", "INTRADAY_SWING", "OVERNIGHT_SWING"] for s in DEFAULT_WEIGHTS},
            regime_tag     = "",
            generated_at   = datetime.now(timezone.utc).isoformat(),
        )

    def _load_ledger(self) -> dict:
        if not _EDGE_LEDGER.exists():
            return {}
        try:
            return json.loads(_EDGE_LEDGER.read_text())
        except Exception:
            return {}

    def _is_defensive(self) -> bool:
        if not _DRIFT_REPORTS.exists():
            return False
        lines = []
        try:
            with open(_DRIFT_REPORTS) as f:
                lines = [l.strip() for l in f if l.strip()]
        except Exception:
            return False
        for line in lines[-5:]:
            try:
                d = json.loads(line)
                if d.get("defensive_mode_triggered") and d.get("severity") in ("HIGH", "CRITICAL"):
                    ts_str = d.get("generated_at", "")
                    if ts_str:
                        from datetime import timedelta
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if (datetime.now(timezone.utc) - ts).total_seconds() < 86400:
                            return True
            except Exception:
                pass
        return False

    def _strategy_score_from_ledger(self, strategy_tag: str, ledger: dict) -> Optional[float]:
        """Find best expectancy for a strategy tag across all regimes."""
        best = None
        for key, record in ledger.items():
            if key.startswith(strategy_tag + "|"):
                n = record.get("trades_count", 0)
                if n < 5:
                    continue
                exp = record.get("expectancy_net", 0.0)
                if best is None or exp > best:
                    best = exp
        return best

    def update(self, regime_tag: str = "") -> MetaLearnerWeights:
        """
        Compute new weights from evidence.
        Returns new MetaLearnerWeights (also saves to file).
        """
        current   = self.load_current()
        ledger    = self._load_ledger()
        defensive = self._is_defensive()
        now_str   = datetime.now(timezone.utc).isoformat()
        notes     = []

        if defensive:
            notes.append("DEFENSIVE MODE: boosting safe strategies")

        new_weights = {}
        for strategy, cur_w in current.weights.items():
            if defensive and strategy not in SAFE_STRATEGIES:
                # Reduce non-safe strategies by up to max change
                target = max(0.02, cur_w - MAX_WEIGHT_CHANGE_PER_CYCLE)
                new_weights[strategy] = target
                continue

            score = self._strategy_score_from_ledger(strategy, ledger)

            if score is None or ledger == {}:
                # No data: keep current
                new_weights[strategy] = cur_w
            elif score > 0.1:
                # Positive expectancy: allow increase up to cap
                target = min(cur_w + MAX_WEIGHT_CHANGE_PER_CYCLE, MAX_SINGLE_STRATEGY_WEIGHT)
                n_records = sum(r.get("trades_count", 0) for k, r in ledger.items() if k.startswith(strategy + "|"))
                if n_records < MIN_SAMPLE_FOR_BOOST:
                    target = cur_w  # not enough data to boost
                    notes.append(f"{strategy}: positive edge but only {n_records} samples -- holding weight")
                else:
                    notes.append(f"{strategy}: +expectancy={score:.2f}R -> up weight")
                new_weights[strategy] = target
            elif score < -0.1:
                # Negative expectancy: reduce
                target = max(0.01, cur_w - MAX_WEIGHT_CHANGE_PER_CYCLE)
                notes.append(f"{strategy}: -expectancy={score:.2f}R -> down weight")
                new_weights[strategy] = target
            else:
                new_weights[strategy] = cur_w

        # Normalize to sum to 1.0
        total = sum(new_weights.values())
        if total > 0:
            new_weights = {k: round(v / total, 4) for k, v in new_weights.items()}

        # Defensive: explicitly boost safe strategies
        if defensive:
            for s in SAFE_STRATEGIES:
                if s in new_weights:
                    new_weights[s] = min(MAX_SINGLE_STRATEGY_WEIGHT,
                                         new_weights[s] + MAX_WEIGHT_CHANGE_PER_CYCLE)
            # Re-normalize
            total = sum(new_weights.values())
            if total > 0:
                new_weights = {k: round(v / total, 4) for k, v in new_weights.items()}

        result = MetaLearnerWeights(
            weights          = new_weights,
            allowed_tracks   = current.allowed_tracks,
            sizing_overrides = current.sizing_overrides,
            regime_tag       = regime_tag,
            generated_at     = now_str,
            evidence_summary = f"Ledger buckets: {len(ledger)}, defensive={defensive}",
            guardrail_notes  = notes,
        )

        _META_WEIGHTS.write_text(json.dumps(result.to_dict(), indent=2))
        logger.info(f"Meta weights updated. Defensive={defensive}. Notes: {notes[:3]}")
        return result


# Singleton
_meta: Optional[MetaLearner] = None

def get_meta_learner() -> MetaLearner:
    global _meta
    if _meta is None:
        _meta = MetaLearner()
    return _meta
