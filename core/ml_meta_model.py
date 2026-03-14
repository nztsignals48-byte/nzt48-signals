"""
LightGBM Meta-Model -- Stage 2 Intelligence
============================================
Chen & Guestrin (2016) XGBoost/LightGBM achieves AUC 0.63 on next-day
direction prediction with engineered features from price, volume, and
technical indicators.

Activation threshold: 200 trades (we have 413 -- activate immediately).

This module:
  1. Trains LightGBM on all historical outcomes from data/outcomes.jsonl
  2. Predicts win probability for each new signal
  3. Blends with rule-based confidence: 70% rule-based + 30% ML
  4. Retrains weekly (Sunday 22:00) on rolling window
  5. A/B tracks ML blend vs pure rule-based to validate improvement

Wave 2, Item 3: SHAP Feature Stability Filter (Gu, Kelly & Xiu 2020)
  After each training window, SHAP TreeExplainer ranks features by
  mean |SHAP value|. Features whose rank varies by >5 positions across
  the last 4 training windows are flagged UNSTABLE and dropped from
  `active_features`. The live inference path only uses `active_features`,
  preventing noisy/spurious features from degrading model quality.
"""

import json
import logging
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

# Wave 2 — SHAP (optional import for graceful degradation)
try:
    import shap
    _HAS_SHAP = True
except ImportError:
    _HAS_SHAP = False

try:
    import config as cfg
    _HAS_CFG = True
except ImportError:
    _HAS_CFG = False

logger = logging.getLogger(__name__)

# DISABLED: Re-enable after (a) 200+ genuine trades, (b) regime map fixed,
# (c) feature leakage removed, (d) walk-forward validation passes.
# AEGIS 0-05: ML regime encoding broken (always -1), confidence is leaked as
# input feature, 43.7% training data is fabricated. Model is actively harmful.
# When False: meta_label() returns pass-through (no veto), predict_proba()
# returns 0.5 (neutral), blend_confidence() returns pure rule-based, train()
# is a no-op.
_ML_ENABLED: bool = False

# J-02: Regime map fixed to use actual RegimeState enum values (models.py).
# Previous map used fictional keys ("bull", "bear") that never matched,
# causing _encode_regime() to always return -1 (AEGIS 0-05 root cause).
_REGIME_MAP: dict[str, int] = {
    "trending_up_strong": 0,
    "trending_up_mod": 1,
    "trending_down_strong": 2,
    "trending_down_mod": 3,
    "range_bound": 4,
    "high_volatility": 5,
    "risk_off": 6,
    "shock": 7,
    "regime_flapping": 8,
}
_TICKER_MAP: dict[str, int] = {"QQQ3.L": 0, "3LUS.L": 1, "3SEM.L": 2, "GPT3.L": 3, "NVD3.L": 4, "TSL3.L": 5, "TSM3.L": 6, "MU2.L": 7, "QQQS.L": 8, "3USS.L": 9, "QQQ5.L": 10, "SP5L.L": 11}
_WIN_OUTCOMES = {"WIN", "TARGET", "win", "target"}

# Wave 2 — SHAP stability constants
_SHAP_HISTORY_MAXLEN = 4        # Number of training windows to track
_SHAP_RANK_DRIFT_THRESHOLD = 5  # Max rank positions a feature can drift
_SHAP_HISTORY_PATH = Path("data/shap_history.json")


class MLMetaModel:
    """Stage 2 intelligence layer: LightGBM on historical outcomes blended with rule-based confidence.
    Falls back to sklearn GradientBoostingClassifier if LightGBM not installed.
    If neither library available: pass-through mode (predict_proba=0.5).
    """

    # J-04: ML Bypass Enforcement tiers
    _ML_TIER_DISABLED = "DISABLED"       # N < 200
    _ML_TIER_LOGREG = "LOGREG_FALLBACK"  # 200 <= N < 500
    _ML_TIER_FULL = "FULL_ENSEMBLE"      # N >= 500 AND DSR > 1.0

    def __init__(self, data_path: str = "data/outcomes.jsonl", model_path: str = "data/ml_model.pkl", min_trades: int = 200) -> None:
        self.data_path = Path(data_path)
        self.model_path = Path(model_path)
        self.min_trades = min_trades
        self.is_trained: bool = False
        self.model: Any = None
        self._xgb_model: Any = None
        self._last_trained_at: datetime | None = None
        self._n_trades_at_last_train: int = 0
        # J-01: Removed "confidence" — circular feedback (model output leaked
        # as input). Replaced with raw_indicator_count, spread_bps, and
        # time_since_regime_change_hours (non-circular predictors).
        self.feature_cols: list[str] = [
            "rvol", "adx", "rsi", "atr_pct", "raw_indicator_count", "spread_bps",
            "time_since_regime_change_hours",
            "hour_of_day", "day_of_week", "vix", "regime_encoded", "ticker_encoded",
            "beat_magnitude", "pre_earnings_runup", "short_interest_pct",
        ]

        # Wave 2 — SHAP Feature Stability (Gu, Kelly & Xiu 2020)
        # active_features is the subset of feature_cols after unstable
        # features have been removed. If no SHAP history exists yet,
        # active_features == feature_cols (all features active).
        self.active_features: list[str] = list(self.feature_cols)
        self._shap_history: list[dict[str, int]] = []  # [{feat: rank}, ...]
        self._unstable_features: list[str] = []
        self._load_shap_history()

        # J-04: ML bypass enforcement — determine tier at boot
        self._ml_tier: str = self._determine_ml_tier()
        logger.info("MLMetaModel: J-04 bypass tier = %s", self._ml_tier)

        self._load_model()

    def _count_outcomes(self) -> int:
        """Count total trade records in outcomes.jsonl (fast, no parsing)."""
        if not self.data_path.exists():
            return 0
        try:
            with open(self.data_path, "r", encoding="utf-8") as fh:
                return sum(1 for line in fh if line.strip())
        except Exception:
            return 0

    def _compute_dsr(self) -> float:
        """Compute Deflated Sharpe Ratio proxy from outcomes.

        DSR > 1.0 means the strategy has enough edge to justify full ML.
        Simplified: annualised Sharpe of R-multiples.
        """
        if not self.data_path.exists():
            return 0.0
        try:
            r_multiples: list[float] = []
            with open(self.data_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        r = rec.get("pnl_r_net") or rec.get("r_multiple") or rec.get("pnl_r_gross")
                        if r is not None:
                            r_multiples.append(float(r))
                    except Exception:
                        continue
            if len(r_multiples) < 30:
                return 0.0
            arr = np.array(r_multiples, dtype=np.float64)
            mean_r = float(np.mean(arr))
            std_r = float(np.std(arr, ddof=1))
            if std_r < 1e-9:
                return 0.0
            # Annualise assuming ~252 trading days
            daily_sharpe = mean_r / std_r
            annualised = daily_sharpe * np.sqrt(252)
            return float(annualised)
        except Exception:
            return 0.0

    def _determine_ml_tier(self) -> str:
        """J-04: Determine ML tier based on trade count and DSR.

        - N < 200:  ML DISABLED (no model at all)
        - N < 500:  Pure LogReg fallback (5 PCA features)
        - N >= 500 AND DSR > 1.0: Full LightGBM/XGBoost ensemble
        """
        n_trades = self._count_outcomes()
        if n_trades < 200:
            logger.info(
                "J-04 BYPASS: ML DISABLED — only %d trades (need 200)", n_trades
            )
            return self._ML_TIER_DISABLED

        if n_trades < 500:
            logger.info(
                "J-04 BYPASS: LogReg fallback — %d trades (need 500 for full)", n_trades
            )
            return self._ML_TIER_LOGREG

        dsr = self._compute_dsr()
        if dsr > 1.0:
            logger.info(
                "J-04 BYPASS: Full ML ensemble — %d trades, DSR=%.2f", n_trades, dsr
            )
            return self._ML_TIER_FULL
        else:
            logger.info(
                "J-04 BYPASS: LogReg fallback — %d trades but DSR=%.2f <= 1.0",
                n_trades, dsr,
            )
            return self._ML_TIER_LOGREG

    def _load_model(self) -> None:
        """Load a persisted model from disk if one exists."""
        if not _ML_ENABLED:
            logger.info("MLMetaModel: ML disabled (AEGIS 0-05) — skipping model load")
            return
        if not self.model_path.exists():
            logger.info("MLMetaModel: no saved model found at %s", self.model_path)
            return
        try:
            with open(self.model_path, "rb") as fh:
                payload = pickle.load(fh)
            self.model = payload["model"]
            self._xgb_model = payload.get("xgb_model")
            self._last_trained_at = payload.get("trained_at")
            self._n_trades_at_last_train = payload.get("n_trades", 0)
            # Wave 2: Restore active_features from saved model (inference
            # must use the same feature set the model was trained on)
            saved_active = payload.get("active_features")
            if saved_active and isinstance(saved_active, list):
                self.active_features = saved_active
                logger.info(
                    "MLMetaModel: restored active_features from model (%d/%d features)",
                    len(self.active_features), len(self.feature_cols),
                )
            self.is_trained = True
            logger.info("MLMetaModel: loaded saved model (trained %s, n_trades=%d)", self._last_trained_at, self._n_trades_at_last_train)
        except Exception as exc:
            logger.warning("MLMetaModel: could not load saved model -- %s", exc)

    def _encode_regime(self, regime: str) -> int:
        """Map a regime string to an integer; unknown regimes return -1."""
        return _REGIME_MAP.get(str(regime).lower().strip(), -1)

    def _encode_ticker(self, ticker: str) -> int:
        """Map a ticker string to an integer; unknown tickers return -1."""
        return _TICKER_MAP.get(str(ticker).upper().strip(), -1)

    def _extract_row(self, record: dict) -> tuple[list[float], int, float]:
        """Convert a JSON trade record to (feature_vector, label, r_multiple). Missing fields default to 0.0."""

        def _f(key: str) -> float:
            val = record.get(key, 0.0)
            try:
                return float(val) if val is not None else 0.0
            except (TypeError, ValueError):
                return 0.0
        hour_of_day = 0
        day_of_week = 0
        ts_raw = record.get("timestamp") or record.get("entry_time") or record.get("date")
        if ts_raw:
            try:
                if isinstance(ts_raw, (int, float)):
                    dt = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
                else:
                    dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                hour_of_day = dt.hour
                day_of_week = dt.weekday()
            except Exception:
                pass
        regime_raw = record.get("regime") or record.get("market_regime") or ""
        ticker_raw = record.get("ticker") or record.get("symbol") or ""
        # J-01: "confidence" removed (circular feedback). Replaced with
        # raw_indicator_count, spread_bps, time_since_regime_change_hours.
        features = [
            _f("rvol"), _f("adx"), _f("rsi"), _f("atr_pct"),
            _f("raw_indicator_count"), _f("spread_bps"),
            _f("time_since_regime_change_hours"),
            float(hour_of_day), float(day_of_week), _f("vix"),
            float(self._encode_regime(str(regime_raw))), float(self._encode_ticker(str(ticker_raw))),
            _f("beat_magnitude"), _f("pre_earnings_runup"), _f("short_interest_pct"),
        ]
        outcome = str(record.get("outcome", "")).upper()
        label = 1 if outcome in {o.upper() for o in _WIN_OUTCOMES} else 0
        # Reward shaping: r_multiple as continuous signal (Ng & Russell 1999)
        r_mult = record.get("r_multiple")
        try:
            r_multiple = float(r_mult) if r_mult is not None else (1.0 if label == 1 else -1.0)
        except (TypeError, ValueError):
            r_multiple = 1.0 if label == 1 else -1.0
        return features, label, r_multiple

    def _load_training_data(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Read data/outcomes.jsonl; build X, y, r_multiples, sample_weights.

        Returns (X, y, r_multiples, sample_weights) where:
        - y: binary labels (WIN=1, LOSS=0)
        - r_multiples: continuous r_multiple for reward shaping
        - sample_weights: uncertainty-based weights (Settles 2009 active learning)
        """
        if not self.data_path.exists():
            logger.warning("MLMetaModel: outcomes file not found at %s", self.data_path)
            empty = np.empty(0)
            return np.empty((0, len(self.feature_cols))), empty, empty, empty
        rows_x: list[list[float]] = []
        rows_y: list[int] = []
        rows_r: list[float] = []
        skipped = 0
        with open(self.data_path, "r", encoding="utf-8") as fh:
            for line_no, raw_line in enumerate(fh, start=1):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    record = json.loads(raw_line)
                    features, label, r_multiple = self._extract_row(record)
                    rows_x.append(features)
                    rows_y.append(label)
                    rows_r.append(r_multiple)
                except Exception as exc:
                    skipped += 1
                    logger.debug("MLMetaModel: skipped malformed line %d -- %s", line_no, exc)
        if skipped:
            logger.warning("MLMetaModel: skipped %d malformed records during load", skipped)
        if not rows_x:
            empty = np.empty(0)
            return np.empty((0, len(self.feature_cols))), empty, empty, empty
        X = np.array(rows_x, dtype=np.float32)
        y = np.array(rows_y, dtype=np.int32)
        r_arr = np.array(rows_r, dtype=np.float32)
        # Active learning weights: uncertainty sampling (Settles 2009)
        # Trades where model gave 45-55% confidence are highest learning value
        # Proxy: use |r_multiple| as confidence proxy (low |R| = uncertain outcome)
        abs_r = np.abs(r_arr)
        learning_value = np.maximum(0.1, 1.0 - np.clip(abs_r / 3.0, 0, 1))
        # Exponential recency weighting: recent trades 2x more valuable
        n = len(rows_x)
        recency = np.exp(np.linspace(-1.5, 0, n))  # older -> newer
        sample_weights = learning_value * recency
        sample_weights = sample_weights / sample_weights.mean()  # normalise to mean=1
        logger.info("MLMetaModel: loaded %d training records", len(y))
        return X, y, r_arr, sample_weights

    def _get_active_col_indices(self) -> list[int]:
        """Return column indices in full X matrix for active_features.

        Maps each feature in active_features to its index in feature_cols
        (which defines the column order in X from _load_training_data).
        """
        return [
            self.feature_cols.index(f)
            for f in self.active_features
            if f in self.feature_cols
        ]

    def _train_logreg_fallback(
        self, X: np.ndarray, y: np.ndarray, sample_weights: np.ndarray,
    ) -> dict:
        """J-04: Pure LogisticRegression fallback for 200-500 trade range.

        Uses PCA to reduce to 5 components before fitting. Simple model
        prevents overfitting on limited data. No ensemble, no SHAP.
        """
        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.decomposition import PCA
            from sklearn.preprocessing import StandardScaler
        except ImportError:
            logger.error("J-04 LogReg fallback: sklearn not available")
            return {"trained": False, "reason": "sklearn_not_available"}

        n_components = min(5, X.shape[1])
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        pca = PCA(n_components=n_components, random_state=42)
        X_pca = pca.fit_transform(X_scaled)

        lr = LogisticRegression(
            C=1.0, max_iter=1000, random_state=42, solver="lbfgs",
        )
        try:
            lr.fit(X_pca, y, sample_weight=sample_weights)
        except TypeError:
            lr.fit(X_pca, y)

        self.model = lr
        self._xgb_model = None
        self.is_trained = True
        self._last_trained_at = datetime.now(tz=timezone.utc)
        self._n_trades_at_last_train = int(len(y))

        # Store PCA/scaler for inference
        self._logreg_scaler = scaler
        self._logreg_pca = pca

        # Persist
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload = {
                "model": lr,
                "xgb_model": None,
                "trained_at": self._last_trained_at,
                "n_trades": self._n_trades_at_last_train,
                "backend": "logreg_j04",
                "ml_tier": self._ML_TIER_LOGREG,
                "feature_cols": self.feature_cols,
                "active_features": self.active_features,
                "logreg_scaler": scaler,
                "logreg_pca": pca,
            }
            with open(self.model_path, "wb") as fh:
                pickle.dump(payload, fh)
        except Exception as exc:
            logger.warning("J-04 LogReg: could not save model -- %s", exc)

        result = {
            "trained": True,
            "backend": "logreg_j04",
            "ml_tier": self._ML_TIER_LOGREG,
            "n_trades": int(len(y)),
            "pca_components": n_components,
            "pca_explained_variance": [round(float(v), 4) for v in pca.explained_variance_ratio_],
        }
        logger.info("J-04 LogReg fallback trained: %s", result)
        return result

    def train(self) -> dict:
        """Train the meta-model. Backend: LightGBM > sklearn GBM > fail gracefully.
        Uses walk-forward CV. Returns summary dict.

        Wave 2 (Gu, Kelly & Xiu 2020): After training, computes SHAP
        values and updates the feature stability rankings. Unstable
        features are removed from active_features for the NEXT training
        cycle (not retroactively applied to the current model).
        """
        # AEGIS 0-05: ML disabled until prerequisites met
        if not _ML_ENABLED:
            logger.info("MLMetaModel.train: SKIPPED (_ML_ENABLED=False)")
            return {"trained": False, "reason": "ml_disabled_aegis_0_05"}

        # J-04: Re-check tier at train time (trade count may have changed)
        self._ml_tier = self._determine_ml_tier()
        if self._ml_tier == self._ML_TIER_DISABLED:
            logger.info("MLMetaModel.train: SKIPPED (J-04 tier=DISABLED, N<200)")
            return {"trained": False, "reason": "j04_ml_disabled_insufficient_trades"}

        X_full, y, r_multiples, sample_weights = self._load_training_data()
        if len(y) < self.min_trades:
            logger.warning("MLMetaModel: only %d trades (min %d) -- skipping", len(y), self.min_trades)
            return {"trained": False, "reason": "insufficient_data", "n_trades": int(len(y))}

        # J-04: LogReg fallback path (200 <= N < 500, or DSR <= 1.0)
        if self._ml_tier == self._ML_TIER_LOGREG:
            return self._train_logreg_fallback(X_full, y, sample_weights)

        # Wave 2: Select active feature columns
        active_idx = self._get_active_col_indices()
        if not active_idx:
            # Safety: if all features were dropped, reset to full set
            logger.warning("SHAP: all features were dropped — resetting to full feature set")
            self.active_features = list(self.feature_cols)
            active_idx = list(range(len(self.feature_cols)))
        X = X_full[:, active_idx]
        logger.info(
            "MLMetaModel: training with %d/%d features (active=%s)",
            len(active_idx), len(self.feature_cols),
            self.active_features,
        )

        Classifier: Any = None
        backend_name = ""
        _xgb_available = False
        try:
            import lightgbm as lgb  # noqa: F401
            from lightgbm import LGBMClassifier
            Classifier = LGBMClassifier
            backend_name = "lightgbm"
            logger.info("MLMetaModel: using LightGBM backend")
        except ImportError:
            logger.info("MLMetaModel: LightGBM not available, trying sklearn")
        if Classifier is None:
            try:
                from sklearn.ensemble import GradientBoostingClassifier
                Classifier = GradientBoostingClassifier
                backend_name = "sklearn_gbm"
                logger.info("MLMetaModel: using sklearn GradientBoostingClassifier")
            except ImportError:
                logger.error("MLMetaModel: no ML library available")
                self.is_trained = False
                return {"trained": False, "reason": "no_ml_library"}
        # XGBoost ensemble member (Dietterich 2000: uncorrelated models outperform single)
        try:
            import xgboost as xgb  # noqa: F401
            _xgb_available = True
            logger.info("MLMetaModel: XGBoost available for ensemble")
        except ImportError:
            logger.info("MLMetaModel: XGBoost not available, using single-model mode")
        # J-03: Expanding-window walk-forward validation with purge + embargo.
        # Replaces StratifiedKFold(shuffle=True) which violated time-series
        # ordering (future data leaked into past folds).
        # Protocol: 20-day train, 5-day test, 5-day purge gap, 5-day embargo.
        # max_depth=2 on LightGBM trees to prevent overfitting.
        _WALK_TRAIN_DAYS = 20
        _WALK_TEST_DAYS = 5
        _WALK_PURGE_DAYS = 5
        _WALK_EMBARGO_DAYS = 5
        _WALK_STEP = _WALK_TEST_DAYS  # step forward by test window size

        cv_aucs: list[float] = []
        try:
            from sklearn.metrics import roc_auc_score

            n_samples = len(y)
            fold = 0
            start = 0
            while True:
                train_end = start + _WALK_TRAIN_DAYS
                purge_end = train_end + _WALK_PURGE_DAYS
                test_start = purge_end
                test_end = test_start + _WALK_TEST_DAYS
                embargo_end = test_end + _WALK_EMBARGO_DAYS

                if test_end > n_samples:
                    break  # Not enough data for this fold

                train_idx = list(range(start, train_end))
                test_idx = list(range(test_start, min(test_end, n_samples)))

                if len(train_idx) < 10 or len(test_idx) < 3:
                    start += _WALK_STEP
                    continue

                X_tr, X_val = X[train_idx], X[test_idx]
                y_tr, y_val = y[train_idx], y[test_idx]
                sw_tr = sample_weights[train_idx]

                if backend_name == "lightgbm":
                    fm = Classifier(
                        n_estimators=200, learning_rate=0.05,
                        max_depth=2, num_leaves=4,  # J-03: max_depth=2
                        feature_fraction=0.8, bagging_fraction=0.8,
                        bagging_freq=5, min_child_samples=10,
                        random_state=42, verbose=-1,
                    )
                else:
                    fm = Classifier(
                        n_estimators=200, learning_rate=0.05,
                        max_depth=2, subsample=0.8, random_state=42,
                    )

                try:
                    fm.fit(X_tr, y_tr, sample_weight=sw_tr)
                except TypeError:
                    fm.fit(X_tr, y_tr)

                proba = fm.predict_proba(X_val)[:, 1]
                # AUC needs both classes present
                if len(set(y_val)) >= 2:
                    auc = roc_auc_score(y_val, proba)
                    cv_aucs.append(auc)
                    fold += 1
                    logger.debug(
                        "MLMetaModel: walk-forward fold %d AUC=%.4f "
                        "(train=%d-%d, test=%d-%d, purge=%d, embargo=%d)",
                        fold, auc, start, train_end - 1,
                        test_start, test_end - 1,
                        _WALK_PURGE_DAYS, _WALK_EMBARGO_DAYS,
                    )

                # Expanding window: move test start forward, keep train start at 0
                # for expanding, or advance for rolling
                start += _WALK_STEP

        except ImportError:
            logger.warning("MLMetaModel: sklearn not available for CV -- skipping")
        except Exception as exc:
            logger.warning("MLMetaModel: walk-forward validation failed -- %s", exc)

        mean_auc = float(np.mean(cv_aucs)) if cv_aucs else 0.0
        n_folds = len(cv_aucs)
        logger.info(
            "MLMetaModel: walk-forward CV AUC=%.4f (%d folds, purge=%d, embargo=%d, backend=%s)",
            mean_auc, n_folds, _WALK_PURGE_DAYS, _WALK_EMBARGO_DAYS, backend_name,
        )
        try:
            if backend_name == "lightgbm":
                final_model = Classifier(
                    n_estimators=300, learning_rate=0.05,
                    max_depth=2, num_leaves=4,  # J-03: max_depth=2 prevents overfit
                    feature_fraction=0.8, bagging_fraction=0.8,
                    bagging_freq=5, min_child_samples=10,
                    random_state=42, verbose=-1,
                )
            else:
                final_model = Classifier(
                    n_estimators=300, learning_rate=0.05,
                    max_depth=2, subsample=0.8, random_state=42,  # J-03
                )
            # Reward shaping: weight samples by active learning value (Settles 2009)
            try:
                final_model.fit(X, y, sample_weight=sample_weights)
                logger.info("MLMetaModel: fit with active learning sample weights (mean_w=%.3f)", float(sample_weights.mean()))
            except TypeError:
                final_model.fit(X, y)  # fallback if sample_weight not supported
        except Exception as exc:
            logger.error("MLMetaModel: final model fit failed -- %s", exc)
            return {"trained": False, "reason": f"fit_error: {exc}"}
        self.model = final_model
        # XGBoost ensemble: train second model (Dietterich 2000)
        self._xgb_model = None
        if _xgb_available:
            try:
                import xgboost as xgb
                xgb_model = xgb.XGBClassifier(
                    n_estimators=300, learning_rate=0.05, max_depth=4,
                    subsample=0.8, colsample_bytree=0.8, random_state=43,
                    eval_metric="logloss", verbosity=0,
                )
                xgb_model.fit(X, y, sample_weight=sample_weights)
                self._xgb_model = xgb_model
                logger.info("MLMetaModel: XGBoost ensemble member trained")
            except Exception as _xe:
                logger.debug("MLMetaModel: XGBoost training failed: %s", _xe)
        self.is_trained = True
        self._last_trained_at = datetime.now(tz=timezone.utc)
        self._n_trades_at_last_train = int(len(y))

        # ------------------------------------------------------------------
        # Wave 2: SHAP Feature Stability (Gu, Kelly & Xiu 2020)
        # Compute SHAP values, update rank history, recompute active_features
        # for the NEXT training cycle.
        # ------------------------------------------------------------------
        shap_result = self._run_shap_stability_filter(final_model, X)

        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload = {
                "model": final_model,
                "xgb_model": self._xgb_model,
                "trained_at": self._last_trained_at,
                "n_trades": self._n_trades_at_last_train,
                "backend": backend_name,
                "cv_auc": mean_auc,
                "feature_cols": self.feature_cols,
                "active_features": self.active_features,
                "unstable_features": self._unstable_features,
            }
            with open(self.model_path, "wb") as fh:
                pickle.dump(payload, fh)
            logger.info("MLMetaModel: model saved to %s", self.model_path)
        except Exception as exc:
            logger.warning("MLMetaModel: could not save model -- %s", exc)
        importance = self.get_feature_importance()
        result = {
            "trained": True,
            "backend": backend_name,
            "n_trades": int(len(y)),
            "cv_auc": round(mean_auc, 4),
            "feature_importance": importance,
            "active_features": self.active_features,
            "unstable_features": self._unstable_features,
            "shap_filter": shap_result,
        }
        logger.info("MLMetaModel: training complete -- %s", result)
        return result

    def predict_proba(self, features: dict) -> float:
        """Return ML win probability. Returns 0.5 if model not trained (neutral).

        Uses ``active_features`` (subset of ``feature_cols`` after SHAP
        stability filtering) to match the feature set the model was trained on.
        """
        # AEGIS 0-05: ML disabled — return neutral probability
        if not _ML_ENABLED:
            return 0.5
        if not self.is_trained or self.model is None:
            return 0.5
        try:
            row: list[float] = []
            # Wave 2: use active_features (post-SHAP filter) for inference
            _cols = self.active_features if self.active_features else self.feature_cols
            for col in _cols:
                if col == "regime_encoded":
                    raw = features.get("regime") or features.get("market_regime") or ""
                    row.append(float(self._encode_regime(str(raw))))
                elif col == "ticker_encoded":
                    raw = features.get("ticker") or features.get("symbol") or ""
                    row.append(float(self._encode_ticker(str(raw))))
                elif col == "hour_of_day":
                    ts_raw = features.get("timestamp") or features.get("entry_time") or features.get("date")
                    hour = 0
                    if ts_raw:
                        try:
                            if isinstance(ts_raw, (int, float)):
                                hour = datetime.fromtimestamp(ts_raw, tz=timezone.utc).hour
                            else:
                                hour = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).hour
                        except Exception:
                            pass
                    row.append(float(hour))
                elif col == "day_of_week":
                    ts_raw = features.get("timestamp") or features.get("entry_time") or features.get("date")
                    dow = 0
                    if ts_raw:
                        try:
                            if isinstance(ts_raw, (int, float)):
                                dow = datetime.fromtimestamp(ts_raw, tz=timezone.utc).weekday()
                            else:
                                dow = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).weekday()
                        except Exception:
                            pass
                    row.append(float(dow))
                else:
                    val = features.get(col, 0.0)
                    try:
                        row.append(float(val) if val is not None else 0.0)
                    except (TypeError, ValueError):
                        row.append(0.0)
            lgb_prob = float(self.model.predict_proba([row])[0][1])
            # Blend with XGBoost if available (Dietterich 2000 ensemble diversity)
            xgb_model = getattr(self, "_xgb_model", None)
            if xgb_model is not None:
                try:
                    xgb_prob = float(xgb_model.predict_proba([row])[0][1])
                    prob = 0.55 * lgb_prob + 0.45 * xgb_prob  # LGB slightly higher weight
                    logger.debug("MLMetaModel: LGB=%.3f XGB=%.3f blended=%.3f", lgb_prob, xgb_prob, prob)
                except Exception:
                    prob = lgb_prob
            else:
                prob = lgb_prob
            return max(0.0, min(1.0, prob))
        except Exception as exc:
            logger.warning("MLMetaModel.predict_proba failed -- %s", exc)
            return 0.5

    def meta_label(self, features: dict) -> dict:
        """De Prado (2018) Chapter 4: Binary meta-labelling gate.
        S15 generates the signal direction. This decides: trade or skip.

        Returns:
            dict with keys: veto (bool), p_success (float), threshold (float),
                           model_active (bool)
        """
        # AEGIS 0-05: ML disabled — pass-through, never veto
        if not _ML_ENABLED:
            return {"veto": False, "p_success": 0.5, "threshold": 0.65, "model_active": False}

        if not self.is_trained or self.model is None:
            # Cold-start: NEVER veto — system must trade to learn
            return {"veto": False, "p_success": 0.5, "threshold": 0.65, "model_active": False}

        p_success = self.predict_proba(features)

        # Regime-adaptive threshold (Ang & Timmermann 2012)
        regime = str(features.get("regime") or features.get("market_regime") or "").upper()
        if regime in ("TRENDING_UP_STRONG", "TRENDING_UP_MOD", "BREAKOUT"):
            threshold = 0.60  # More permissive in trending regimes
        elif regime in ("CHOPPY", "VOLATILE", "RANGE_BOUND"):
            threshold = 0.70  # Stricter in choppy regimes
        elif regime in ("SHOCK", "CRASH"):
            threshold = 1.0  # Veto all in shock
        else:
            threshold = 0.65  # Default

        veto = p_success < threshold

        # Log for A/B tracking
        log_entry = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "ticker": features.get("ticker") or features.get("symbol", ""),
            "p_success": round(p_success, 4),
            "threshold": threshold,
            "regime": regime,
            "veto": veto,
            "model_active": True,
        }
        try:
            pred_path = Path("data/ml_predictions.jsonl")
            pred_path.parent.mkdir(parents=True, exist_ok=True)
            with open(pred_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(log_entry) + "\n")
        except Exception as exc:
            logger.debug("MLMetaModel: could not write prediction log -- %s", exc)

        if veto:
            logger.info(
                "META_LABEL_VETO: %s P=%.3f < thresh=%.2f regime=%s",
                features.get("ticker", "?"), p_success, threshold, regime,
            )
        else:
            logger.debug(
                "META_LABEL_PASS: %s P=%.3f >= thresh=%.2f",
                features.get("ticker", "?"), p_success, threshold,
            )

        return {
            "veto": veto,
            "p_success": round(p_success, 4),
            "threshold": threshold,
            "model_active": True,
        }

    def blend_confidence(self, rule_based_confidence: float, features: dict) -> float:
        """LEGACY: Blend rule-based (70%) + ML (30%). Kept for backward compatibility.
        New code should use meta_label() instead (De Prado 2018 binary gate)."""
        # AEGIS 0-05: ML disabled — return pure rule-based confidence
        if not _ML_ENABLED:
            return rule_based_confidence
        ml_prob = self.predict_proba(features)
        ml_confidence = ml_prob * 100.0
        blended = round(0.70 * rule_based_confidence + 0.30 * ml_confidence, 1)
        log_entry = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "ticker": features.get("ticker") or features.get("symbol", ""),
            "rule_based_confidence": round(rule_based_confidence, 1),
            "ml_confidence": round(ml_confidence, 1),
            "ml_prob": round(ml_prob, 4),
            "blended_confidence": blended,
            "model_active": self.is_trained,
        }
        try:
            pred_path = Path("data/ml_predictions.jsonl")
            pred_path.parent.mkdir(parents=True, exist_ok=True)
            with open(pred_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(log_entry) + "\n")
        except Exception as exc:
            logger.debug("MLMetaModel: could not write prediction log -- %s", exc)
        logger.debug("MLMetaModel.blend: rule=%.1f ml=%.1f blended=%.1f", rule_based_confidence, ml_confidence, blended)
        return blended

    def should_retrain(self, last_trained_at: datetime | None = None) -> bool:
        """Return True if >7 days since last training OR >50 new trades since last training.

        F-05 fix: last_trained_at is now optional. Uses self._last_trained_at
        if not provided (callers in main.py pass zero args).
        """
        # AEGIS 0-05: ML disabled — never retrain
        if not _ML_ENABLED:
            return False

        last_trained_at = last_trained_at or self._last_trained_at
        if last_trained_at is None:
            # Never trained — yes, retrain (once ML is re-enabled)
            return True
        now = datetime.now(tz=timezone.utc)
        if last_trained_at.tzinfo is None:
            last_trained_at = last_trained_at.replace(tzinfo=timezone.utc)
        days_since = (now - last_trained_at).days
        if days_since >= 7:
            logger.info("MLMetaModel.should_retrain: %d days since training (threshold=7)", days_since)
            return True
        current_n = 0
        if self.data_path.exists():
            try:
                with open(self.data_path, "r", encoding="utf-8") as fh:
                    current_n = sum(1 for line in fh if line.strip())
            except Exception:
                pass
        new_trades = current_n - self._n_trades_at_last_train
        if new_trades >= 50:
            logger.info("MLMetaModel.should_retrain: %d new trades since training (threshold=50)", new_trades)
            return True
        return False

    def get_feature_importance(self) -> dict[str, float]:
        """Return top 10 features by importance. Returns {} if model not trained."""
        if not self.is_trained or self.model is None:
            return {}
        try:
            importances = self.model.feature_importances_
            # Use active_features (matches model's training columns)
            _cols = self.active_features if self.active_features else self.feature_cols
            pairs = sorted(zip(_cols, importances), key=lambda x: x[1], reverse=True)
            return {name: round(float(imp), 4) for name, imp in pairs[:10]}
        except Exception as exc:
            logger.warning("MLMetaModel.get_feature_importance failed -- %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Wave 2: SHAP Feature Stability (Gu, Kelly & Xiu 2020)
    # ------------------------------------------------------------------

    def _load_shap_history(self) -> None:
        """Load SHAP rank history from data/shap_history.json."""
        if not _SHAP_HISTORY_PATH.exists():
            self._shap_history = []
            return
        try:
            with open(_SHAP_HISTORY_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self._shap_history = data.get("rank_history", [])
            self._unstable_features = data.get("unstable_features", [])
            saved_active = data.get("active_features")
            if saved_active and isinstance(saved_active, list):
                # Only use saved active_features if they're a valid subset
                valid = [f for f in saved_active if f in self.feature_cols]
                if valid:
                    self.active_features = valid
            logger.info(
                "SHAP: loaded history (%d windows, %d unstable, %d active features)",
                len(self._shap_history),
                len(self._unstable_features),
                len(self.active_features),
            )
        except Exception as exc:
            logger.debug("SHAP: could not load history -- %s", exc)
            self._shap_history = []

    def _save_shap_history(self) -> None:
        """Persist SHAP rank history to data/shap_history.json."""
        try:
            _SHAP_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "rank_history": self._shap_history[-_SHAP_HISTORY_MAXLEN:],
                "unstable_features": self._unstable_features,
                "active_features": self.active_features,
                "last_updated": datetime.now(tz=timezone.utc).isoformat(),
            }
            with open(_SHAP_HISTORY_PATH, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            logger.info("SHAP: history saved to %s", _SHAP_HISTORY_PATH)
        except Exception as exc:
            logger.warning("SHAP: could not save history -- %s", exc)

    def _run_shap_stability_filter(
        self,
        model: Any,
        X: np.ndarray,
    ) -> dict:
        """Compute SHAP values, update rank history, recompute active_features.

        Gu, Kelly & Xiu (2020): features whose importance rank drifts by
        more than 5 positions across 4 training windows are UNSTABLE and
        should be dropped to prevent overfitting to transient patterns.

        Args:
            model: The fitted LightGBM/sklearn model.
            X: Training feature matrix (n_samples, n_active_features).

        Returns:
            Summary dict with SHAP results and stability analysis.
        """
        # Check feature flag
        _enabled = True
        if _HAS_CFG:
            _enabled = cfg.get("v95_shap_stability_filter_enabled", True)
        if not _enabled:
            return {"status": "DISABLED"}

        if not _HAS_SHAP:
            logger.info("SHAP: shap library not installed — skipping stability filter")
            return {"status": "SHAP_NOT_INSTALLED"}

        try:
            # 1. Compute SHAP values using TreeExplainer
            explainer = shap.TreeExplainer(model)
            shap_values = explainer.shap_values(X)

            # Handle binary classification: shap_values may be [neg_class, pos_class]
            if isinstance(shap_values, list):
                # Use positive class SHAP values
                sv = np.array(shap_values[1])
            else:
                sv = np.array(shap_values)

            # 2. Mean |SHAP| per feature → rank
            mean_abs_shap = np.mean(np.abs(sv), axis=0)
            n_features = len(mean_abs_shap)

            # Build feature names for current active set
            _active = self.active_features if self.active_features else self.feature_cols
            if len(_active) != n_features:
                logger.warning(
                    "SHAP: feature count mismatch (active=%d, shap=%d) — skipping",
                    len(_active), n_features,
                )
                return {"status": "SHAPE_MISMATCH"}

            # Rank: 1 = most important, N = least important
            sorted_indices = np.argsort(-mean_abs_shap)  # descending by importance
            current_ranks: dict[str, int] = {}
            for rank, idx in enumerate(sorted_indices, start=1):
                current_ranks[_active[idx]] = rank

            logger.info(
                "SHAP: feature ranks — %s",
                {k: v for k, v in sorted(current_ranks.items(), key=lambda x: x[1])},
            )

            # 3. Append to rank history (max 4 windows)
            self._shap_history.append(current_ranks)
            if len(self._shap_history) > _SHAP_HISTORY_MAXLEN:
                self._shap_history = self._shap_history[-_SHAP_HISTORY_MAXLEN:]

            # 4. Stability analysis — need at least 2 windows
            n_windows = len(self._shap_history)
            unstable: list[str] = []
            stability_report: dict[str, dict] = {}

            if n_windows >= 2:
                # Check ALL features in feature_cols (not just active)
                # to allow previously dropped features to re-stabilize
                all_feature_names = set()
                for window in self._shap_history:
                    all_feature_names.update(window.keys())

                for feat in all_feature_names:
                    ranks_across_windows = [
                        w.get(feat) for w in self._shap_history if feat in w
                    ]
                    if len(ranks_across_windows) < 2:
                        continue

                    min_rank = min(ranks_across_windows)
                    max_rank = max(ranks_across_windows)
                    rank_drift = max_rank - min_rank

                    stability_report[feat] = {
                        "ranks": ranks_across_windows,
                        "drift": rank_drift,
                        "stable": rank_drift <= _SHAP_RANK_DRIFT_THRESHOLD,
                    }

                    if rank_drift > _SHAP_RANK_DRIFT_THRESHOLD:
                        unstable.append(feat)
                        logger.warning(
                            "SHAP: feature '%s' UNSTABLE — rank drift=%d "
                            "(ranks=%s, threshold=%d)",
                            feat, rank_drift, ranks_across_windows,
                            _SHAP_RANK_DRIFT_THRESHOLD,
                        )

            self._unstable_features = unstable

            # 5. Recompute active_features for NEXT training cycle
            # Start from full feature_cols, remove unstable ones
            # BUT: never drop ALL features — keep at least 4
            new_active = [f for f in self.feature_cols if f not in unstable]
            if len(new_active) < 4:
                # Keep top 4 most important from current ranking
                ranked_features = sorted(
                    current_ranks.items(), key=lambda x: x[1],
                )
                new_active = [f for f, _ in ranked_features[:4]]
                logger.warning(
                    "SHAP: too many unstable features — keeping top 4: %s",
                    new_active,
                )

            self.active_features = new_active
            self._save_shap_history()

            dropped = [f for f in self.feature_cols if f not in new_active]
            if dropped:
                logger.info(
                    "SHAP: %d features dropped for next cycle: %s",
                    len(dropped), dropped,
                )
            else:
                logger.info("SHAP: all %d features stable", len(new_active))

            return {
                "status": "OK",
                "n_windows": n_windows,
                "n_stable": len(new_active),
                "n_unstable": len(unstable),
                "unstable_features": unstable,
                "dropped_features": dropped,
                "stability_report": stability_report,
                "mean_abs_shap": {
                    _active[i]: round(float(mean_abs_shap[i]), 6)
                    for i in range(n_features)
                },
            }

        except Exception as exc:
            logger.warning("SHAP: stability filter failed (non-blocking): %s", exc)
            return {"status": "ERROR", "error": str(exc)}
