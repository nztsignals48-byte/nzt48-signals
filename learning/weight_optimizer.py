"""
NZT-48 Learning Module 8: Confidence Weight Evolution
After every 100 trades: regression on which layers most predict positive R.
Suggests weight swaps. Operator approves via Telegram.
"""
from __future__ import annotations
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.learning.weights")


class WeightOptimizer:
    """Evolves confidence scoring weights based on actual trade outcomes."""

    # Default weights from spec (max values per layer)
    DEFAULT_WEIGHTS = {
        "L1_price_action": 45,
        "L2_regime": 20,
        "L3_sector_flow": 15,
        "L4_macro": 10,
        "L5_narrative": 10,
    }

    RECALC_INTERVAL = 100  # Every 100 trades

    def __init__(self):
        self.current_weights = dict(self.DEFAULT_WEIGHTS)
        self.proposed_weights: Optional[dict] = None
        self._trade_data: list[dict] = []
        self._trades_since_recalc = 0
        self._pending_approval = False
        self._suggestions: list[dict] = []

    def record_trade(self, confidence_layers: dict, r_multiple: float) -> Optional[dict]:
        """Record confidence layer values and trade outcome.

        confidence_layers: {L1: 38, L2: 18, L3: 12, L4: 8, L5: 7}
        """
        self._trade_data.append({
            "layers": confidence_layers,
            "r": r_multiple,
        })
        self._trades_since_recalc += 1

        if self._trades_since_recalc >= self.RECALC_INTERVAL:
            return self._analyze_and_suggest()
        return None

    def _analyze_and_suggest(self) -> Optional[dict]:
        """Analyze which layers best predict positive R, suggest weight changes."""
        self._trades_since_recalc = 0

        if len(self._trade_data) < self.RECALC_INTERVAL:
            return None

        recent = self._trade_data[-self.RECALC_INTERVAL:]

        # For each layer, calculate correlation with R-multiple
        layer_names = ["L1", "L2", "L3", "L4", "L5"]
        layer_correlations = {}

        for layer in layer_names:
            values = [d["layers"].get(layer, 0) for d in recent]
            r_values = [d["r"] for d in recent]

            if not values or not r_values:
                continue

            # Simple correlation
            n = len(values)
            mean_v = sum(values) / n
            mean_r = sum(r_values) / n

            cov = sum((v - mean_v) * (r - mean_r) for v, r in zip(values, r_values))
            var_v = sum((v - mean_v) ** 2 for v in values)
            var_r = sum((r - mean_r) ** 2 for r in r_values)

            denom = (var_v * var_r) ** 0.5
            corr = cov / denom if denom > 0 else 0
            layer_correlations[layer] = corr

        if not layer_correlations:
            return None

        # Rank layers by predictive power
        ranked = sorted(layer_correlations.items(), key=lambda x: x[1], reverse=True)

        # Check if any layer significantly outpredicts another
        # Propose swapping weights between highest and lowest correlating layers
        best_layer = ranked[0][0]
        worst_layer = ranked[-1][0]
        best_corr = ranked[0][1]
        worst_corr = ranked[-1][1]

        weight_keys = {
            "L1": "L1_price_action", "L2": "L2_regime", "L3": "L3_sector_flow",
            "L4": "L4_macro", "L5": "L5_narrative",
        }

        if best_corr - worst_corr > 0.15:  # Significant difference
            # Propose weight adjustment
            proposed = dict(self.current_weights)
            best_key = weight_keys[best_layer]
            worst_key = weight_keys[worst_layer]

            # Shift 5 points from worst to best
            shift = min(5, proposed[worst_key])
            proposed[best_key] = min(50, proposed[best_key] + shift)
            proposed[worst_key] = max(5, proposed[worst_key] - shift)

            # Backtest: what would R-distribution look like with new weights?
            old_avg_r = sum(d["r"] for d in recent) / len(recent)

            suggestion = {
                "type": "WEIGHT_SUGGESTION",
                "current_weights": dict(self.current_weights),
                "proposed_weights": proposed,
                "best_layer": best_layer,
                "worst_layer": worst_layer,
                "layer_correlations": {k: round(v, 3) for k, v in ranked},
                "sample_size": len(recent),
                "current_avg_r": round(old_avg_r, 3),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": (
                    f"WEIGHT SUGGESTION: {best_layer} outpredicting {worst_layer}. "
                    f"Shift {shift} points {worst_layer}→{best_layer}. "
                    f"/approve_weights or /reject_weights"
                ),
            }

            self.proposed_weights = proposed
            self._pending_approval = True
            self._suggestions.append(suggestion)

            logger.info("WEIGHT SUGGESTION: %s → %s (%s)",
                        worst_layer, best_layer, suggestion["message"])
            return suggestion

        return None

    def approve_weights(self) -> dict:
        """Operator approves weight change."""
        if self.proposed_weights and self._pending_approval:
            old = dict(self.current_weights)
            self.current_weights = dict(self.proposed_weights)
            self.proposed_weights = None
            self._pending_approval = False
            logger.info("WEIGHTS APPROVED: %s → %s", old, self.current_weights)
            return {"status": "APPROVED", "old": old, "new": dict(self.current_weights)}
        return {"status": "NO_PENDING_SUGGESTION"}

    def reject_weights(self) -> dict:
        """Operator rejects weight change."""
        self.proposed_weights = None
        self._pending_approval = False
        return {"status": "REJECTED"}

    def has_pending_suggestion(self) -> bool:
        return self._pending_approval

    def get_current_weights(self) -> dict:
        return dict(self.current_weights)

    def get_suggestions_history(self) -> list[dict]:
        return self._suggestions

    def save_state(self, conn: sqlite3.Connection) -> None:
        """Persist weight optimizer state to SQLite as a JSON blob."""
        conn.execute(
            """CREATE TABLE IF NOT EXISTS learning_state (
                module TEXT PRIMARY KEY,
                state_json TEXT,
                updated_at TEXT
            )"""
        )
        state = {
            "current_weights": self.current_weights,
            "proposed_weights": self.proposed_weights,
            "trade_data": self._trade_data[-500:],  # Keep last 500
            "trades_since_recalc": self._trades_since_recalc,
            "pending_approval": self._pending_approval,
            "suggestions": self._suggestions[-20:],
        }
        conn.execute(
            "INSERT OR REPLACE INTO learning_state (module, state_json, updated_at) VALUES (?, ?, ?)",
            ("weight_optimizer", json.dumps(state), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        logger.info("Weight optimizer state saved to DB")

    def load_state(self, conn: sqlite3.Connection) -> None:
        """Load weight optimizer state from SQLite."""
        try:
            row = conn.execute(
                "SELECT state_json FROM learning_state WHERE module = ?",
                ("weight_optimizer",),
            ).fetchone()
        except Exception:
            return
        if not row:
            return
        state = json.loads(row["state_json"] if isinstance(row, sqlite3.Row) else row[0])
        self.current_weights = state.get("current_weights", dict(self.DEFAULT_WEIGHTS))
        self.proposed_weights = state.get("proposed_weights")
        self._trade_data = state.get("trade_data", [])
        self._trades_since_recalc = state.get("trades_since_recalc", 0)
        self._pending_approval = state.get("pending_approval", False)
        self._suggestions = state.get("suggestions", [])
        logger.info("Weight optimizer state loaded")
