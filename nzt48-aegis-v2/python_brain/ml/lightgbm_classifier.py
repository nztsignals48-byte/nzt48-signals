"""Book 23: LightGBM 48-feature entry classifier via ONNX runtime.

Applied as a universal filter to ALL signals in _generate_signals().
P(win) > 0.65 → +5 confidence. P(win) < 0.40 → -8 confidence.

Model trained offline, deployed as /app/models/lgbm_entry.onnx.
Falls open if model not found (returns 0.5, neutral).

Consumed by: bridge.py _generate_signals() — boosts/penalizes all signals.
"""

import os
import sys
import numpy as np

_MODEL_PATH = "/app/models/lgbm_entry.onnx"

# 48 features in order (must match training)
_FEATURE_NAMES = [
    # Price momentum (5)
    "ret_1bar", "ret_5bar", "ret_10bar", "ret_20bar", "ret_50bar",
    # Volume (5)
    "rvol", "vol_slope", "vol_div", "vpin", "volume_acceleration",
    # Volatility (5)
    "atr_pct", "realized_vol", "hurst", "garch_vol", "vol_of_vol",
    # Microstructure (8)
    "spread_pct", "tmr", "tick_ratio", "quote_imbalance", "amihud",
    "structural_score", "micro_score", "spread_ratio",
    # Regime (5)
    "adx", "regime_score", "vix", "drawdown_pct", "consecutive_losses",
    # Technical (10)
    "rsi_2", "rsi_14", "ibs", "bb_zscore", "sma20_dist", "sma50_dist",
    "macd_signal", "obv_divergence", "price_vs_vwap", "vwap_slope",
    # Calendar (5)
    "hour_utc", "day_of_week", "day_of_month", "month", "tom_signal",
    # Cross-asset (5)
    "spy_return_1h", "vix_change", "dxy_change", "credit_spread", "sector_momentum",
]


class LGBMEntryClassifier:
    """ONNX-based LightGBM classifier for entry quality."""

    def __init__(self):
        self._session = None
        self._loaded = False
        self._load_error = False

    def _ensure_loaded(self):
        if self._loaded or self._load_error:
            return
        self._loaded = True
        if not os.path.exists(_MODEL_PATH):
            self._load_error = True
            return
        try:
            import onnxruntime as ort
            self._session = ort.InferenceSession(
                _MODEL_PATH,
                providers=["CPUExecutionProvider"],
            )
        except Exception as e:
            self._load_error = True
            sys.stderr.write(f"LGBM: failed to load ONNX model: {e}\n")
            sys.stderr.flush()

    def extract_features(self, ind, msg, common_fields=None):
        """Extract 48 features from indicator dict and message context.

        Returns numpy array of shape (1, 48) or None if insufficient data.
        """
        if common_fields is None:
            common_fields = {}

        bars_5m = ind.get("bars_5m", [])
        if not bars_5m or len(bars_5m) < 20:
            return None

        closes = [b["close"] for b in bars_5m]
        volumes = [b["volume"] for b in bars_5m]
        current = closes[-1] if closes else 0

        def _safe_ret(n):
            if len(closes) > n and closes[-n - 1] > 0:
                return (closes[-1] - closes[-n - 1]) / closes[-n - 1]
            return 0.0

        def _safe_sma(n):
            if len(closes) >= n:
                sma = sum(closes[-n:]) / n
                return (current - sma) / sma if sma > 0 else 0
            return 0.0

        # Build feature vector
        features = np.zeros(48, dtype=np.float32)

        # Price momentum
        features[0] = _safe_ret(1)
        features[1] = _safe_ret(5)
        features[2] = _safe_ret(10)
        features[3] = _safe_ret(20)
        features[4] = _safe_ret(min(50, len(closes) - 1))

        # Volume
        features[5] = ind.get("rvol", 1.0)
        features[6] = ind.get("vol_slope", 0.0)
        features[7] = ind.get("vol_div", 0.0)
        features[8] = ind.get("vpin", 0.5)
        features[9] = 0.0  # volume_acceleration — compute if available

        # Volatility
        features[10] = ind.get("atr_pct", 0.5) if "atr_pct" in ind else 0.5
        features[11] = msg.get("realized_vol", 0.30)
        features[12] = ind.get("hurst", 0.5)
        features[13] = 0.0  # garch_vol — from Rust if available
        features[14] = 0.0  # vol_of_vol

        # Microstructure
        features[15] = ind.get("spread_pct", 0.1)
        features[16] = common_fields.get("s1_tmr", 0.0)
        features[17] = common_fields.get("s1_tick_ratio", 0.5)
        features[18] = ind.get("quote_imbalance", 0.0)
        features[19] = msg.get("amihud", 0.0)
        features[20] = ind.get("structural_score", 50)
        features[21] = 0.0  # micro_score
        features[22] = 0.0  # spread_ratio

        # Regime
        features[23] = ind.get("adx", 15.0)
        features[24] = 0.0  # regime_score
        features[25] = msg.get("vix", 20.0)
        features[26] = msg.get("drawdown_pct", 0.0)
        features[27] = msg.get("consecutive_losses", 0)

        # Technical
        features[28] = 50.0  # rsi_2 placeholder
        features[29] = 0.0   # rsi_14
        features[30] = ind.get("ibs", 0.5)
        # BB z-score
        if len(closes) >= 20:
            sma20 = sum(closes[-20:]) / 20
            std20 = (sum((c - sma20) ** 2 for c in closes[-20:]) / 20) ** 0.5
            features[31] = (current - sma20) / std20 if std20 > 1e-9 else 0
        features[32] = _safe_sma(20)
        features[33] = _safe_sma(50) if len(closes) >= 50 else 0
        features[34] = 0.0  # macd_signal
        features[35] = ind.get("vol_div", 0.0)  # obv_divergence proxy
        features[36] = ind.get("vwap_dist_pct", 0.0) / 100.0
        features[37] = ind.get("vwap_slope", 0.0)

        # Calendar
        try:
            from datetime import datetime, timezone
            ts_ns = msg.get("timestamp_ns", 0)
            if ts_ns > 0:
                dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
                features[38] = dt.hour
                features[39] = dt.weekday()
                features[40] = dt.day
                features[41] = dt.month
            # TOM signal
            import calendar
            if ts_ns > 0:
                dom = dt.day
                dim = calendar.monthrange(dt.year, dt.month)[1]
                if dom >= dim or dom <= 4:
                    features[42] = 1.0
        except Exception:
            pass

        # Cross-asset
        features[43] = msg.get("spy_first_30min_return", 0.0)
        features[44] = 0.0  # vix_change
        features[45] = 0.0  # dxy_change
        features[46] = 0.0  # credit_spread
        features[47] = 0.0  # sector_momentum

        # Replace NaN/Inf
        features = np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0)

        return features.reshape(1, -1)

    def predict(self, features):
        """Predict P(win) from feature array. Returns float 0-1 or None."""
        self._ensure_loaded()
        if self._session is None or features is None:
            return None
        try:
            input_name = self._session.get_inputs()[0].name
            outputs = self._session.run(None, {input_name: features.astype(np.float32)})
            # LightGBM ONNX: outputs[1] is probability array [[p_loss, p_win]]
            if len(outputs) >= 2 and hasattr(outputs[1], '__len__'):
                probs = outputs[1]
                if hasattr(probs, 'shape') and len(probs.shape) == 2:
                    return float(probs[0][1])  # P(win)
                if isinstance(probs, list) and len(probs) > 0:
                    return float(probs[0].get(1, 0.5))
            # Fallback: single output = raw prediction
            return float(outputs[0][0]) if len(outputs) > 0 else None
        except Exception as e:
            if not getattr(self, '_predict_err_logged', False):
                sys.stderr.write(f"LGBM predict error: {e}\n")
                sys.stderr.flush()
                self._predict_err_logged = True
            return None
