"""
learning/execution_quality_model.py
=====================================
Predicts execution quality (slippage / fill risk) for a signal.

Inputs: spread proxy, volatility, time of day, liquidity, momentum.
Output: ExecutionQualityRecord with recommendation (NORMAL|DOWNSIZE|WATCH|SKIP)

Also feeds as a feature into ExpectancyModel.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from learning.schemas import ExecutionQualityRecord

logger = logging.getLogger("nzt48.learning.execution_quality")

_DATA    = Path(__file__).parent.parent / "data"
_EQ_LOG  = _DATA / "execution_quality.jsonl"

# Spread BPS by ticker type (round-trip)
_SPREAD_BPS = {
    "QQQ5":  14.0,
    "SP5L":  10.0,
    "3LUS":  12.0,
    "3SEM":  15.0,
    "GPT3":  13.0,
    "NVD3":  13.0,
    "TSL3":  14.0,
    "TSM3":  12.0,
    "3USS":  12.0,
    "QQQS":  10.0,
    "MU2":   10.0,
    "QQQ3":  10.0,
    "default": 8.0,
}

# Fill risk multipliers
_TIME_RISK = {
    "PRE_MARKET":    1.5,
    "OPEN":          1.3,
    "MORNING":       1.0,
    "MIDDAY":        0.8,
    "AFTERNOON":     1.0,
    "CLOSE":         1.2,
    "POST_MARKET":   1.6,
    "OFF_HOURS":     2.0,
}


def _spread_bps_for(ticker: str) -> float:
    for key, bps in _SPREAD_BPS.items():
        if key in ticker:
            return bps
    return _SPREAD_BPS["default"]


class ExecutionQualityModel:
    """
    Rule-based execution quality predictor.
    Pure deterministic -- no ML training required.
    """

    def predict(self, signal_id: str, ticker: str, rvol: float = 1.0,
                atr_pct: float = 1.0, time_window: str = "MORNING",
                composite: float = 70.0, direction: str = "LONG") -> ExecutionQualityRecord:
        """
        Predict execution quality for a signal.
        """
        spread_bps = _spread_bps_for(ticker)

        # Base slippage: 30% of spread (market order mid vs fill)
        base_slippage = spread_bps * 0.3

        # Adjust for volatility (high ATR = wider spreads in practice)
        vol_multiplier = 1.0 + max(0.0, (atr_pct - 1.0) * 0.3)
        expected_slippage = round(base_slippage * vol_multiplier, 2)

        # Fill risk: composite of liquidity + time risk + RVOL
        time_key = "MORNING"
        for key in _TIME_RISK:
            if key.lower() in time_window.lower():
                time_key = key
                break

        time_factor = _TIME_RISK.get(time_key, 1.0)

        # RVOL: low RVOL = harder to fill at expected price
        rvol_penalty = max(0.0, (1.0 - min(rvol, 2.0) / 2.0) * 0.3)

        fill_risk = min(1.0, (spread_bps / 20.0) * time_factor * (1 + rvol_penalty))
        fill_risk = round(fill_risk, 3)

        # Recommendation
        if fill_risk > 0.7 or expected_slippage > 15:
            recommendation = "SKIP"
        elif fill_risk > 0.5 or expected_slippage > 10:
            recommendation = "WATCH"
        elif fill_risk > 0.3 or expected_slippage > 7:
            recommendation = "DOWNSIZE"
        else:
            recommendation = "NORMAL"

        now_str = datetime.now(timezone.utc).isoformat()
        record = ExecutionQualityRecord(
            signal_id             = signal_id,
            ticker                = ticker,
            expected_slippage_bps = expected_slippage,
            actual_slippage_bps   = 0.0,  # filled post-trade
            fill_risk_score       = fill_risk,
            spread_bps            = spread_bps,
            recommendation        = recommendation,
            generated_at          = now_str,
        )

        # Log it
        try:
            _DATA.mkdir(parents=True, exist_ok=True)
            with open(_EQ_LOG, "a") as f:
                f.write(json.dumps(record.to_dict()) + "\n")
        except Exception as e:
            logger.warning(f"Failed to log execution quality: {e}")

        return record


# Singleton
_eq_model: Optional[ExecutionQualityModel] = None

def get_execution_quality_model() -> ExecutionQualityModel:
    global _eq_model
    if _eq_model is None:
        _eq_model = ExecutionQualityModel()
    return _eq_model
