"""LightGBM Meta-Model — signal quality scoring before execution.

Trains on historical signal features → trade outcome. Produces a probability
of profit for each candidate signal. Bridge.py uses this to re-rank signals
so the best-expected-value signal wins (not just highest raw confidence).

Features per signal:
  - confidence, kelly_fraction, rvol, hurst, adx, vpin, spread_pct
  - vol_regime, time_fraction, drawdown_pct, structural_score
  - sentiment_score, portfolio_weight (if available)

Target: binary (1 = profitable trade, 0 = loss/breakeven).

License: LightGBM is MIT.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("lightgbm_scorer")

try:
    import lightgbm as lgb
    import numpy as np
    _HAS_LGB = True
except ImportError:
    _HAS_LGB = False

_MODEL_PATH = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data")) / "models" / "lgb_signal_scorer.txt"

_model = None
_FEATURE_COLS = [
    "confidence", "kelly_fraction", "rvol", "hurst", "adx",
    "vpin", "spread_pct", "time_fraction", "drawdown_pct",
    "structural_score", "sentiment_score",
]


def _load_model():
    """Load trained model from disk (lazy, cached)."""
    global _model
    if _model is not None:
        return True
    if not _HAS_LGB:
        return False
    if not _MODEL_PATH.exists():
        log.info("LightGBM model not found at %s — scorer disabled", _MODEL_PATH)
        return False
    try:
        _model = lgb.Booster(model_file=str(_MODEL_PATH))
        log.info("LightGBM signal scorer loaded (%d trees)", _model.num_trees())
        return True
    except Exception as e:
        log.warning("Failed to load LightGBM model: %s", e)
        return False


def score_signal(signal: Dict[str, Any]) -> Optional[float]:
    """Score a single signal dict. Returns probability of profit [0, 1] or None."""
    if not _load_model():
        return None
    try:
        features = np.array([[
            signal.get(col, 0.0) for col in _FEATURE_COLS
        ]], dtype=np.float64)
        prob = _model.predict(features)[0]
        return float(prob)
    except Exception as e:
        log.warning("LightGBM scoring failed: %s", e)
        return None


def score_batch(signals: List[Dict[str, Any]]) -> List[Optional[float]]:
    """Score multiple signals efficiently."""
    if not _load_model() or not signals:
        return [None] * len(signals)
    try:
        features = np.array([
            [s.get(col, 0.0) for col in _FEATURE_COLS]
            for s in signals
        ], dtype=np.float64)
        probs = _model.predict(features)
        return [float(p) for p in probs]
    except Exception as e:
        log.warning("LightGBM batch scoring failed: %s", e)
        return [None] * len(signals)


def train_from_trades(
    trades_path: Optional[str] = None,
    output_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Train the LightGBM meta-model from historical trade data.

    Called by nightly pipeline. Reads trade history with signal features,
    trains binary classifier (profit vs loss), saves model.

    Returns training metrics dict or None on failure.
    """
    if not _HAS_LGB:
        log.warning("LightGBM not installed — training skipped")
        return None

    if trades_path is None:
        trades_path = os.environ.get("AEGIS_DATA_DIR", "/app/data") + "/signal_trade_history.json"
    if output_path is None:
        output_path = str(_MODEL_PATH)

    try:
        with open(trades_path) as f:
            trades = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        log.info("No signal_trade_history.json found — training skipped")
        return None

    if len(trades) < 50:
        log.info("Insufficient trades for LGB training: %d < 50", len(trades))
        return None

    # Build feature matrix and labels
    X_rows = []
    y_labels = []
    for t in trades:
        features = [t.get(col, 0.0) for col in _FEATURE_COLS]
        X_rows.append(features)
        y_labels.append(1 if t.get("pnl", 0) > 0 else 0)

    X = np.array(X_rows, dtype=np.float64)
    y = np.array(y_labels, dtype=np.int32)

    # Train with early stopping
    train_data = lgb.Dataset(X, label=y, feature_name=_FEATURE_COLS)

    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_data_in_leaf": 10,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "verbose": -1,
    }

    model = lgb.train(
        params, train_data,
        num_boost_round=200,
        valid_sets=[train_data],
        callbacks=[lgb.log_evaluation(period=0)],
    )

    # Save model
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    model.save_model(output_path)
    log.info("LightGBM model saved: %s (%d trees)", output_path, model.num_trees())

    # Feature importance
    importance = dict(zip(_FEATURE_COLS, model.feature_importance("gain").tolist()))
    log.info("Feature importance: %s", importance)

    global _model
    _model = model

    return {
        "n_trades": len(trades),
        "n_features": len(_FEATURE_COLS),
        "n_trees": model.num_trees(),
        "feature_importance": importance,
    }
