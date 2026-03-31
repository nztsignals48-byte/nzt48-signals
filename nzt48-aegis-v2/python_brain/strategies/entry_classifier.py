"""Book 23: LightGBM Entry Classifier — strategy-level signal filter.

48-feature classifier that predicts P(profitable) for every signal.
Applied AFTER signal generation, BEFORE Rust execution.

Signal modification rules:
  P(win) < 0.35  -> BLOCK signal (return None)
  P(win) 0.35-0.50 -> reduce confidence by 15
  P(win) 0.50-0.65 -> no change (neutral zone)
  P(win) > 0.65  -> boost confidence by 10

Falls open: if no model loaded, returns 0.5 (neutral) — no filtering.
Model path: /app/data/models/entry_classifier.onnx (nightly retrained).

Consumed by: bridge.py _generate_signals() post-processing.
Trained by: nightly_v6.py step or standalone train() classmethod.
"""

import math
import os
import sys
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("entry_classifier")

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    import lightgbm as lgb
    _HAS_LGB = True
except ImportError:
    _HAS_LGB = False

try:
    import onnxruntime as ort
    _HAS_ORT = True
except ImportError:
    _HAS_ORT = False

# Model location — matches Docker /app mount and local dev path
_MODEL_DIR = os.environ.get(
    "AEGIS_MODEL_DIR",
    "/app/data/models" if os.path.isdir("/app/data") else os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "models",
    ),
)
_ONNX_PATH = os.path.join(_MODEL_DIR, "entry_classifier.onnx")

# 48 features in strict order — must match training column order.
FEATURE_NAMES: List[str] = [
    # ── Price features (8) ──
    "returns_1m", "returns_5m", "returns_15m", "returns_60m",
    "rsi_14", "rsi_5", "macd_signal_diff", "bb_pct",
    # ── Volume features (6) ──
    "rvol_20", "rvol_5", "vpin", "obv_slope",
    "volume_ma_ratio", "tick_volume_imbalance",
    # ── Volatility features (6) ──
    "realized_vol_5m", "realized_vol_30m", "atr_14",
    "vol_of_vol", "garch_forecast", "iv_rv_spread",
    # ── Microstructure features (6) ──
    "bid_ask_spread_pct", "trade_imbalance", "kyle_lambda",
    "amihud_illiquidity", "price_impact", "order_flow_toxicity",
    # ── Regime features (6) ──
    "vix_level", "vix_term_structure", "hurst_exponent",
    "regime_state", "dxy_change", "credit_spread_bps",
    # ── Time features (4) ──
    "hour_sin", "hour_cos", "day_of_week", "minutes_to_close",
    # ── Strategy features (6) ──
    "signal_confidence", "signal_conviction", "strategy_win_rate",
    "strategy_sharpe", "consecutive_losses", "time_since_last_trade",
    # ── Cross-market features (6) ──
    "spy_return_15m", "leader_return", "leader_volume_surge",
    "correlation_to_spy", "sector_momentum", "breadth_pct",
]

assert len(FEATURE_NAMES) == 48, f"Expected 48 features, got {len(FEATURE_NAMES)}"


class EntryClassifier:
    """LightGBM-based entry quality classifier.

    Lifecycle:
      1. load_model() at bridge startup (or lazy on first call).
      2. apply_to_signal() on every candidate signal.
      3. train() nightly from trade_history DataFrame.
    """

    def __init__(self, model_path: Optional[str] = None):
        self._model_path = model_path or _ONNX_PATH
        self._session: Optional[Any] = None
        self._loaded = False
        self._load_attempted = False

    # ── Model loading ────────────────────────────────────────────────────────

    def load_model(self) -> bool:
        """Load ONNX model from disk. Returns True if successful."""
        if self._loaded:
            return True
        if self._load_attempted:
            return False  # Already tried and failed — don't retry every tick
        self._load_attempted = True

        if not _HAS_ORT:
            logger.warning("onnxruntime not installed — classifier disabled (fail-open)")
            return False
        if not _HAS_NUMPY:
            logger.warning("numpy not installed — classifier disabled (fail-open)")
            return False
        if not os.path.exists(self._model_path):
            logger.info("No ONNX model at %s — classifier in neutral mode", self._model_path)
            return False

        try:
            self._session = ort.InferenceSession(
                self._model_path,
                providers=["CPUExecutionProvider"],
            )
            self._loaded = True
            logger.info("Entry classifier loaded from %s", self._model_path)
            return True
        except Exception as exc:
            logger.error("Failed to load entry classifier ONNX: %s", exc)
            return False

    # ── Feature extraction ───────────────────────────────────────────────────

    def extract_features(
        self,
        tick_context: Dict[str, Any],
        signal: Dict[str, Any],
        market_data: Dict[str, Any],
    ) -> Optional[Dict[str, float]]:
        """Extract 48 features from live tick context, signal, and market data.

        Args:
            tick_context: The `ind` dict from bridge.py (indicators, bars, etc.)
            signal: The candidate signal dict (confidence, strategy, direction, etc.)
            market_data: The `msg` dict from bridge.py (tick message + enrichments)

        Returns:
            Dict of 48 named features, or None if insufficient data.
        """
        if not _HAS_NUMPY:
            return None

        bars_5m = tick_context.get("bars_5m", [])
        if not bars_5m or len(bars_5m) < 20:
            return None  # Need minimum history

        closes = [b["close"] for b in bars_5m]
        volumes = [b["volume"] for b in bars_5m]
        highs = [b["high"] for b in bars_5m]
        lows = [b["low"] for b in bars_5m]
        current = closes[-1] if closes else 0.0

        def _ret(n):
            """N-bar return."""
            if len(closes) > n and closes[-(n + 1)] > 0:
                return (closes[-1] - closes[-(n + 1)]) / closes[-(n + 1)]
            return 0.0

        def _sma(vals, n):
            if len(vals) >= n:
                return sum(vals[-n:]) / n
            return sum(vals) / len(vals) if vals else 0.0

        def _std(vals, n):
            s = vals[-n:] if len(vals) >= n else vals
            if len(s) < 2:
                return 0.0
            m = sum(s) / len(s)
            return (sum((x - m) ** 2 for x in s) / len(s)) ** 0.5

        features: Dict[str, float] = {}

        # ── Price features (8) ──
        features["returns_1m"] = _ret(1)
        features["returns_5m"] = _ret(5)
        features["returns_15m"] = _ret(15)
        features["returns_60m"] = _ret(min(60, len(closes) - 1))
        features["rsi_14"] = tick_context.get("rsi_14", 50.0)
        features["rsi_5"] = tick_context.get("rsi_5", 50.0)
        features["macd_signal_diff"] = tick_context.get("macd_signal", 0.0)
        # Bollinger %B
        sma20 = _sma(closes, 20)
        std20 = _std(closes, 20)
        if std20 > 1e-9:
            features["bb_pct"] = (current - (sma20 - 2 * std20)) / (4 * std20)
        else:
            features["bb_pct"] = 0.5

        # ── Volume features (6) ──
        features["rvol_20"] = tick_context.get("rvol", 1.0)
        vol_5 = _sma(volumes, 5)
        vol_20 = _sma(volumes, 20)
        features["rvol_5"] = (volumes[-1] / vol_5) if vol_5 > 0 else 1.0
        features["vpin"] = tick_context.get("vpin", 0.5)
        # OBV slope: sign of recent OBV change
        obv_val = 0.0
        for i in range(max(0, len(closes) - 10), len(closes)):
            if i > 0 and closes[i] > closes[i - 1]:
                obv_val += volumes[i]
            elif i > 0 and closes[i] < closes[i - 1]:
                obv_val -= volumes[i]
        features["obv_slope"] = 1.0 if obv_val > 0 else (-1.0 if obv_val < 0 else 0.0)
        features["volume_ma_ratio"] = (vol_5 / vol_20) if vol_20 > 0 else 1.0
        # Tick volume imbalance: up-volume vs down-volume in last 10 bars
        up_vol = sum(
            volumes[i] for i in range(max(0, len(closes) - 10), len(closes))
            if i > 0 and closes[i] >= closes[i - 1]
        )
        dn_vol = sum(
            volumes[i] for i in range(max(0, len(closes) - 10), len(closes))
            if i > 0 and closes[i] < closes[i - 1]
        )
        total_vol = up_vol + dn_vol
        features["tick_volume_imbalance"] = ((up_vol - dn_vol) / total_vol) if total_vol > 0 else 0.0

        # ── Volatility features (6) ──
        rets_5 = [
            (closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(max(1, len(closes) - 5), len(closes))
            if closes[i - 1] > 0
        ]
        features["realized_vol_5m"] = (
            (sum(r ** 2 for r in rets_5) / max(len(rets_5), 1)) ** 0.5
        ) if rets_5 else 0.0
        rets_30 = [
            (closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(max(1, len(closes) - 30), len(closes))
            if closes[i - 1] > 0
        ]
        features["realized_vol_30m"] = (
            (sum(r ** 2 for r in rets_30) / max(len(rets_30), 1)) ** 0.5
        ) if rets_30 else 0.0
        features["atr_14"] = tick_context.get("atr_pct", 0.5)
        features["vol_of_vol"] = _std(
            [abs(closes[i] - closes[i - 1]) / closes[i - 1]
             for i in range(max(1, len(closes) - 20), len(closes))
             if closes[i - 1] > 0],
            20,
        )
        features["garch_forecast"] = market_data.get("garch_vol", 0.0)
        features["iv_rv_spread"] = market_data.get("iv_rv_spread", 0.0)

        # ── Microstructure features (6) ──
        bid = market_data.get("bid", 0.0)
        ask = market_data.get("ask", 0.0)
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else current
        features["bid_ask_spread_pct"] = ((ask - bid) / mid * 100) if mid > 0 else 0.0
        features["trade_imbalance"] = tick_context.get("quote_imbalance", 0.0)
        features["kyle_lambda"] = market_data.get("kyle_lambda", 0.0)
        features["amihud_illiquidity"] = market_data.get("amihud", 0.0)
        features["price_impact"] = tick_context.get("price_impact", 0.0)
        features["order_flow_toxicity"] = tick_context.get("vpin", 0.5)  # VPIN proxy

        # ── Regime features (6) ──
        features["vix_level"] = market_data.get("vix", 20.0)
        features["vix_term_structure"] = market_data.get("vix_term_structure", 0.0)
        features["hurst_exponent"] = tick_context.get("hurst", 0.5)
        # Regime encoding: STEADY=1.0, TRANSITION=0.5, CRISIS=0.0
        regime_str = market_data.get("regime", "STEADY")
        if regime_str == "CRISIS":
            features["regime_state"] = 0.0
        elif regime_str == "TRANSITION":
            features["regime_state"] = 0.5
        else:
            features["regime_state"] = 1.0
        features["dxy_change"] = market_data.get("dxy_change", 0.0)
        features["credit_spread_bps"] = market_data.get("credit_spread", 0.0)

        # ── Time features (4) ──
        try:
            from datetime import datetime, timezone
            ts_ns = market_data.get("timestamp_ns", 0)
            if ts_ns > 0:
                dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
                hour_frac = dt.hour + dt.minute / 60.0
                features["hour_sin"] = math.sin(2 * math.pi * hour_frac / 24.0)
                features["hour_cos"] = math.cos(2 * math.pi * hour_frac / 24.0)
                features["day_of_week"] = float(dt.weekday())
                # Minutes to close (approx US close 16:00 ET = 20:00 UTC)
                close_min = 20 * 60  # 20:00 UTC
                now_min = dt.hour * 60 + dt.minute
                features["minutes_to_close"] = max(0.0, float(close_min - now_min))
            else:
                features["hour_sin"] = 0.0
                features["hour_cos"] = 1.0
                features["day_of_week"] = 0.0
                features["minutes_to_close"] = 0.0
        except Exception:
            features["hour_sin"] = 0.0
            features["hour_cos"] = 1.0
            features["day_of_week"] = 0.0
            features["minutes_to_close"] = 0.0

        # ── Strategy features (6) ──
        features["signal_confidence"] = signal.get("confidence", 50.0)
        features["signal_conviction"] = signal.get("conviction", signal.get("confidence", 50.0))
        features["strategy_win_rate"] = market_data.get("win_rate", 0.5)
        features["strategy_sharpe"] = market_data.get("strategy_sharpe", 0.0)
        features["consecutive_losses"] = float(market_data.get("consecutive_losses", 0))
        features["time_since_last_trade"] = float(market_data.get("time_since_last_trade_s", 0))

        # ── Cross-market features (6) ──
        features["spy_return_15m"] = market_data.get("spy_first_30min_return", 0.0)
        features["leader_return"] = market_data.get("leader_return", 0.0)
        features["leader_volume_surge"] = market_data.get("leader_volume_surge", 0.0)
        features["correlation_to_spy"] = market_data.get("correlation_to_spy", 0.5)
        features["sector_momentum"] = market_data.get("sector_momentum", 0.0)
        features["breadth_pct"] = market_data.get("breadth_pct", 0.5)

        # Validate: all 48 features present, replace NaN/Inf
        for name in FEATURE_NAMES:
            val = features.get(name, 0.0)
            if not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val):
                features[name] = 0.0

        return features

    # ── Prediction ───────────────────────────────────────────────────────────

    def predict(self, features: Dict[str, float]) -> float:
        """Predict P(profitable) from feature dict.

        Returns:
            float in [0.0, 1.0]. Returns 0.5 (neutral) if no model loaded.
        """
        if not self._loaded or self._session is None:
            self.load_model()
        if self._session is None or not _HAS_NUMPY:
            return 0.5  # Fail-open: neutral, no filtering

        try:
            # Build feature vector in strict column order
            arr = np.array(
                [[features.get(name, 0.0) for name in FEATURE_NAMES]],
                dtype=np.float32,
            )
            # Replace any NaN/Inf that slipped through
            arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)

            input_name = self._session.get_inputs()[0].name
            outputs = self._session.run(None, {input_name: arr})

            # LightGBM ONNX: outputs[1] = [[P(class0), P(class1)]]
            if len(outputs) >= 2 and hasattr(outputs[1], "__len__"):
                probs = outputs[1]
                if hasattr(probs, "shape") and len(probs.shape) == 2:
                    return float(np.clip(probs[0][1], 0.0, 1.0))
                if isinstance(probs, list) and len(probs) > 0:
                    # dict-style output from some ONNX exporters
                    p = probs[0]
                    if isinstance(p, dict):
                        return float(np.clip(p.get(1, 0.5), 0.0, 1.0))
            # Fallback: single-output model (raw logit)
            if len(outputs) > 0:
                raw = float(outputs[0][0])
                # Sigmoid if raw looks like a logit (outside 0-1)
                if raw < 0 or raw > 1:
                    raw = 1.0 / (1.0 + math.exp(-raw))
                return float(np.clip(raw, 0.0, 1.0))

            return 0.5
        except Exception as exc:
            if not getattr(self, "_predict_err_logged", False):
                logger.error("EntryClassifier predict error: %s", exc)
                self._predict_err_logged = True
            return 0.5  # Fail-open

    # ── Signal application ───────────────────────────────────────────────────

    def apply_to_signal(
        self,
        signal: Dict[str, Any],
        tick_context: Dict[str, Any],
        market_data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Apply classifier to a candidate signal.

        Args:
            signal: Signal dict (must have "confidence" key).
            tick_context: Indicator dict from bridge.py.
            market_data: Message dict from bridge.py.

        Returns:
            Modified signal dict, or None if signal is blocked.
        """
        features = self.extract_features(tick_context, signal, market_data)
        if features is None:
            return signal  # Insufficient data — pass through unchanged

        prob = self.predict(features)

        # Attach probability to signal for downstream forensics
        signal["entry_clf_prob"] = round(prob, 4)

        if prob < 0.35:
            # BLOCK: classifier says this is likely a losing trade
            signal["entry_clf_action"] = "BLOCKED"
            logger.debug(
                "Signal BLOCKED by entry classifier: P(win)=%.3f strategy=%s",
                prob, signal.get("strategy", "?"),
            )
            return None

        if prob < 0.50:
            # REDUCE: marginal signal, penalize confidence
            signal["confidence"] = max(0, signal["confidence"] - 15)
            signal["entry_clf_action"] = "REDUCED"

        elif prob <= 0.65:
            # NEUTRAL: no modification
            signal["entry_clf_action"] = "NEUTRAL"

        else:
            # BOOST: classifier says high-quality entry
            signal["confidence"] = min(100, signal["confidence"] + 10)
            signal["entry_clf_action"] = "BOOSTED"

        return signal

    # ── Nightly training ─────────────────────────────────────────────────────

    @classmethod
    def train(cls, trade_history_df: "pd.DataFrame") -> Dict[str, float]:
        """Train a new LightGBM model from trade history and export to ONNX.

        Args:
            trade_history_df: DataFrame with columns matching FEATURE_NAMES + "outcome"
                              outcome: 1 = profitable trade, 0 = losing trade.

        Returns:
            Dict with validation metrics: auc, precision, recall, n_train, n_test.

        Raises:
            ImportError: If lightgbm or sklearn not available.
        """
        if not _HAS_LGB:
            raise ImportError("lightgbm required for training: pip install lightgbm")
        if not _HAS_NUMPY:
            raise ImportError("numpy required for training: pip install numpy")

        try:
            import pandas as pd
            from sklearn.model_selection import train_test_split
            from sklearn.metrics import roc_auc_score, precision_score, recall_score
        except ImportError as exc:
            raise ImportError(f"sklearn/pandas required for training: {exc}") from exc

        logger.info("Training entry classifier on %d rows", len(trade_history_df))

        # Validate columns
        feature_cols = [c for c in FEATURE_NAMES if c in trade_history_df.columns]
        if len(feature_cols) < 20:
            raise ValueError(
                f"Only {len(feature_cols)}/48 feature columns found in DataFrame. "
                f"Missing: {set(FEATURE_NAMES) - set(trade_history_df.columns)}"
            )
        missing = set(FEATURE_NAMES) - set(trade_history_df.columns)
        if missing:
            logger.warning("Missing %d features, filling with 0: %s", len(missing), missing)
            for col in missing:
                trade_history_df[col] = 0.0

        if "outcome" not in trade_history_df.columns:
            raise ValueError("DataFrame must contain 'outcome' column (1=profitable, 0=not)")

        X = trade_history_df[FEATURE_NAMES].values.astype(np.float32)
        y = trade_history_df["outcome"].values.astype(np.int32)

        # Replace NaN/Inf
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

        # Chronological split — no shuffle (time-series data)
        split_idx = int(len(X) * 0.80)
        if split_idx < 100:
            raise ValueError(f"Need at least 125 samples for 80/20 split, got {len(X)}")

        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]

        logger.info(
            "Split: train=%d (pos=%.1f%%), test=%d (pos=%.1f%%)",
            len(y_train), 100 * y_train.mean(),
            len(y_test), 100 * y_test.mean(),
        )

        # Train LightGBM
        train_data = lgb.Dataset(X_train, label=y_train, feature_name=FEATURE_NAMES)
        valid_data = lgb.Dataset(X_test, label=y_test, reference=train_data)

        params = {
            "objective": "binary",
            "metric": "auc",
            "n_estimators": 500,
            "max_depth": 6,
            "learning_rate": 0.05,
            "min_child_samples": 50,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "class_weight": "balanced",
            "verbose": -1,
            "seed": 42,
        }

        callbacks = [lgb.early_stopping(50, verbose=False)]
        model = lgb.train(
            params,
            train_data,
            num_boost_round=500,
            valid_sets=[valid_data],
            callbacks=callbacks,
        )

        # Evaluate
        y_prob = model.predict(X_test)
        y_pred = (y_prob >= 0.5).astype(int)

        auc = roc_auc_score(y_test, y_prob)
        precision = precision_score(y_test, y_pred, zero_division=0.0)
        recall = recall_score(y_test, y_pred, zero_division=0.0)

        metrics = {
            "auc": round(auc, 4),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "n_train": int(len(y_train)),
            "n_test": int(len(y_test)),
            "n_estimators_used": model.num_trees(),
        }

        logger.info(
            "Validation: AUC=%.4f, Precision=%.4f, Recall=%.4f",
            auc, precision, recall,
        )

        # Export to ONNX
        onnx_path = _export_to_onnx(model, FEATURE_NAMES, _ONNX_PATH)
        if onnx_path:
            metrics["onnx_path"] = onnx_path
            logger.info("Model saved to %s", onnx_path)
        else:
            # Fallback: save native LightGBM model
            fallback_path = _ONNX_PATH.replace(".onnx", ".lgbm.txt")
            os.makedirs(os.path.dirname(fallback_path), exist_ok=True)
            model.save_model(fallback_path)
            metrics["fallback_path"] = fallback_path
            logger.warning("ONNX export failed — saved native model to %s", fallback_path)

        # Save feature importance for nightly review
        try:
            import json
            importance = dict(zip(
                FEATURE_NAMES,
                [int(x) for x in model.feature_importance(importance_type="gain")],
            ))
            importance_path = os.path.join(
                os.path.dirname(_ONNX_PATH), "entry_classifier_importance.json"
            )
            with open(importance_path, "w") as f:
                json.dump(importance, f, indent=2)
            metrics["importance_path"] = importance_path
        except Exception as exc:
            logger.warning("Could not save feature importance: %s", exc)

        return metrics


def _export_to_onnx(
    model: "lgb.Booster",
    feature_names: List[str],
    output_path: str,
) -> Optional[str]:
    """Export LightGBM Booster to ONNX format.

    Returns output_path on success, None on failure.
    """
    try:
        from onnxmltools import convert_lightgbm
        from onnxmltools.convert.common.data_types import FloatTensorType
    except ImportError:
        logger.warning("onnxmltools not installed — cannot export ONNX. pip install onnxmltools")
        return None

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        initial_type = [("features", FloatTensorType([None, len(feature_names)]))]
        onnx_model = convert_lightgbm(model, initial_types=initial_type)
        with open(output_path, "wb") as f:
            f.write(onnx_model.SerializeToString())
        return output_path
    except Exception as exc:
        logger.error("ONNX export failed: %s", exc)
        return None
