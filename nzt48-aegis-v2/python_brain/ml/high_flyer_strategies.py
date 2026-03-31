"""Chinese Quant Deep Learning Strategies — Book 166 (Lingjun / High-Flyer).

Extracts replicable techniques from China's top quant funds (Lingjun 1215%
annualised, High-Flyer $13B AUM) and adapts them for retail-dominated LSE
leveraged ETPs.  Three core components:

  1. **RetailFlowDetector** — Classifies retail vs institutional flow from
     volume profiles and trade-size distributions.  Key insight: retail
     trades cluster in small sizes and herd at the same timestamps.
  2. **MultiFactorAlpha** — Computes 20 standard cross-sectional alpha
     factors (momentum, value, quality, volatility, liquidity) and
     combines them with IC-weighted blending and exponential decay.
  3. **EnsembleDiversityManager** — Measures disagreement across an
     ensemble of model predictions and selects the most diverse subset.

Bridge.py integration:
    try:
        from python_brain.ml.high_flyer_strategies import (
            HighFlyerSignalGenerator, RetailFlowDetector,
            MultiFactorAlpha, EnsembleDiversityManager,
        )
        _hf_gen = HighFlyerSignalGenerator()
    except ImportError:
        _hf_gen = None

Cross-references:
  - Book 138 (Chinese Quant Methods)
  - Book 29 (TCN Deep Learning)
  - Book 75 (Transformer Architecture)
  - Book 36 (Inefficient Market Hunting)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("high_flyer_strategies")

__all__ = [
    "RetailFlowDetector",
    "MultiFactorAlpha",
    "EnsembleDiversityManager",
    "HighFlyerSignalGenerator",
]

# ---------------------------------------------------------------------------
# Paths (production)
# ---------------------------------------------------------------------------
_DATA_DIR = "/app/data"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_N_FACTORS = 20
_DEFAULT_IC_HALFLIFE = 30
_DEFAULT_SMALL_TRADE_THRESHOLD = 1000.0
_DEFAULT_MIN_DIVERSITY = 0.3
_FACTOR_NAMES: List[str] = [
    "momentum_1m",
    "momentum_3m",
    "momentum_6m",
    "momentum_12m",
    "reversal_5d",
    "value_ep",
    "value_bp",
    "value_sp",
    "quality_roe",
    "quality_roa",
    "quality_accruals",
    "volatility_realised",
    "volatility_idiosyncratic",
    "liquidity_turnover",
    "liquidity_amihud",
    "size_market_cap",
    "beta_market",
    "skewness_returns",
    "volume_trend",
    "spread_mean",
]


# ---------------------------------------------------------------------------
# RetailFlowDetector
# ---------------------------------------------------------------------------
class RetailFlowDetector:
    """Detect retail vs institutional order flow.

    Chinese quant funds (Lingjun, High-Flyer) exploit A-share retail
    dominance by classifying flow patterns.  LSE leveraged ETPs share
    similar retail-heavy microstructure.

    Signals:
      - small_trade_ratio:  fraction of trades below a size threshold
      - time_clustering:    herding score from timestamp bunching
      - retail_score:       combined [0, 1] probability of retail flow
    """

    def __init__(
        self,
        small_trade_threshold: float = _DEFAULT_SMALL_TRADE_THRESHOLD,
        cluster_window_s: float = 5.0,
    ) -> None:
        self.small_trade_threshold = small_trade_threshold
        self.cluster_window_s = cluster_window_s
        log.info(
            "RetailFlowDetector init | threshold=%.0f cluster_window=%.1fs",
            small_trade_threshold,
            cluster_window_s,
        )

    # ---- public -----------------------------------------------------------

    def detect(
        self,
        volume_profile: np.ndarray,
        trade_sizes: np.ndarray,
        timestamps: Optional[np.ndarray] = None,
    ) -> Dict[str, Any]:
        """Classify order flow as retail-dominated or institutional.

        Args:
            volume_profile: Volume per time-bucket (e.g. minute bars).
            trade_sizes:    Individual trade sizes in shares/units.
            timestamps:     Unix epoch seconds of each trade (optional).

        Returns:
            dict with keys:
              small_trade_ratio (float), time_clustering (float),
              retail_score (float 0-1), classification (str).
        """
        if len(trade_sizes) == 0:
            log.warning("detect: empty trade_sizes — returning neutral")
            return {
                "small_trade_ratio": 0.5,
                "time_clustering": 0.0,
                "retail_score": 0.5,
                "classification": "UNKNOWN",
            }

        str_ratio = self._small_trade_ratio(trade_sizes, self.small_trade_threshold)

        tc = 0.0
        if timestamps is not None and len(timestamps) > 1:
            tc = self._time_clustering(timestamps)

        # Volume profile skewness — retail tends to concentrate in
        # open/close, creating a U-shaped profile.
        profile_score = self._volume_profile_score(volume_profile)

        # Weighted combination (empirical weights from A-share research)
        w_str, w_tc, w_vp = 0.50, 0.25, 0.25
        retail_score = float(
            np.clip(w_str * str_ratio + w_tc * tc + w_vp * profile_score, 0.0, 1.0)
        )

        classification = "RETAIL" if retail_score > 0.6 else (
            "INSTITUTIONAL" if retail_score < 0.35 else "MIXED"
        )

        log.debug(
            "detect | str=%.3f tc=%.3f vp=%.3f => score=%.3f (%s)",
            str_ratio, tc, profile_score, retail_score, classification,
        )
        return {
            "small_trade_ratio": float(str_ratio),
            "time_clustering": float(tc),
            "retail_score": retail_score,
            "classification": classification,
        }

    # ---- private ----------------------------------------------------------

    @staticmethod
    def _small_trade_ratio(trade_sizes: np.ndarray, threshold: float = 1000.0) -> float:
        """Fraction of trades with size below *threshold*.

        High ratio => retail-dominated flow.
        """
        if len(trade_sizes) == 0:
            return 0.5
        return float(np.mean(trade_sizes < threshold))

    def _time_clustering(self, timestamps: np.ndarray) -> float:
        """Detect herding behaviour from timestamp clustering.

        Uses inter-arrival time coefficient-of-variation.  Low CV =>
        regular (algo) arrivals.  High CV => bursty (retail herding).

        Returns:
            Normalised clustering score in [0, 1].
        """
        if len(timestamps) < 3:
            return 0.0
        ts_sorted = np.sort(timestamps)
        iat = np.diff(ts_sorted)
        iat = iat[iat > 0]  # remove duplicates
        if len(iat) < 2:
            return 0.0

        mean_iat = float(np.mean(iat))
        if mean_iat <= 0:
            return 0.0

        cv = float(np.std(iat)) / mean_iat  # coefficient of variation
        # Map CV to [0, 1]:  CV >> 1 => bursty / herding
        clustering = float(np.clip(1.0 - math.exp(-cv), 0.0, 1.0))
        return clustering

    @staticmethod
    def _volume_profile_score(volume_profile: np.ndarray) -> float:
        """Score the U-shapedness of the intraday volume profile.

        Retail-dominated markets show pronounced volume spikes at open
        and close (U-shape).  Institutional markets are flatter.

        Returns:
            Score in [0, 1] where 1 = strong U-shape.
        """
        if len(volume_profile) < 4:
            return 0.5
        vp = volume_profile.astype(float)
        total = float(np.sum(vp))
        if total <= 0:
            return 0.5

        n = len(vp)
        edge_frac = int(max(1, n // 5))  # first/last 20%
        edge_vol = float(np.sum(vp[:edge_frac]) + np.sum(vp[-edge_frac:]))
        edge_ratio = edge_vol / total

        # Perfect U-shape: 40% edges / 60% middle when edges = 2*20%
        # Score: how much the edges dominate vs uniform expectation
        uniform_edge_ratio = 2 * edge_frac / n
        if uniform_edge_ratio <= 0:
            return 0.5
        excess = edge_ratio / uniform_edge_ratio
        return float(np.clip((excess - 1.0) / 1.0, 0.0, 1.0))


# ---------------------------------------------------------------------------
# MultiFactorAlpha
# ---------------------------------------------------------------------------
class MultiFactorAlpha:
    """Multi-factor alpha model inspired by High-Flyer's factor pipeline.

    Computes 20 standard factors across a cross-section of instruments,
    then combines them using IC-weighted blending with exponential decay.

    The factor zoo is deliberately kept to well-known, published factors
    to avoid overfitting — the edge comes from combination, not novelty.
    """

    def __init__(self, n_factors: int = _DEFAULT_N_FACTORS) -> None:
        self.n_factors = n_factors
        self._ic_history: List[np.ndarray] = []
        log.info("MultiFactorAlpha init | n_factors=%d", n_factors)

    # ---- public -----------------------------------------------------------

    def compute_alphas(self, data: Dict[str, np.ndarray]) -> np.ndarray:
        """Compute alpha factors from market data dictionary.

        Args:
            data: dict with keys matching the required inputs for each
                  factor.  Expected keys:
                    'returns_1m', 'returns_3m', 'returns_6m', 'returns_12m',
                    'returns_5d', 'ep', 'bp', 'sp', 'roe', 'roa',
                    'accruals', 'realised_vol', 'idio_vol', 'turnover',
                    'amihud', 'market_cap', 'beta', 'skewness',
                    'volume_trend', 'spread_mean'
                  Each value is a 1-D array of length n_instruments.

        Returns:
            np.ndarray of shape (n_instruments, n_factors) with
            cross-sectionally z-scored factor values.
        """
        factor_keys = [
            "returns_1m", "returns_3m", "returns_6m", "returns_12m",
            "returns_5d", "ep", "bp", "sp", "roe", "roa",
            "accruals", "realised_vol", "idio_vol", "turnover",
            "amihud", "market_cap", "beta", "skewness",
            "volume_trend", "spread_mean",
        ]

        n_instruments = 0
        for k in factor_keys:
            if k in data and len(data[k]) > 0:
                n_instruments = len(data[k])
                break

        if n_instruments == 0:
            log.warning("compute_alphas: no valid factor data")
            return np.zeros((0, self.n_factors))

        alphas = np.zeros((n_instruments, self.n_factors))

        for i, key in enumerate(factor_keys):
            if i >= self.n_factors:
                break
            raw = data.get(key)
            if raw is None:
                log.debug("Factor '%s' missing — filling zeros", key)
                continue
            raw = np.asarray(raw, dtype=float)
            if len(raw) != n_instruments:
                log.warning(
                    "Factor '%s' length %d != n_instruments %d",
                    key, len(raw), n_instruments,
                )
                continue
            alphas[:, i] = self._cross_section_zscore(raw)

        log.debug(
            "compute_alphas | instruments=%d factors=%d",
            n_instruments, self.n_factors,
        )
        return alphas

    def ic_weighted_combine(
        self,
        alphas: np.ndarray,
        forward_returns: np.ndarray,
    ) -> np.ndarray:
        """Combine factors using Information Coefficient weighting.

        IC = rank-correlation between each factor and realised forward
        returns.  Factors with higher predictive power get more weight.

        Args:
            alphas:           (n_instruments, n_factors)
            forward_returns:  (n_instruments,)

        Returns:
            Combined alpha signal (n_instruments,).
        """
        n_instruments, n_factors = alphas.shape
        if n_instruments < 5:
            log.warning("ic_weighted_combine: too few instruments (%d)", n_instruments)
            return np.mean(alphas, axis=1)

        ics = np.zeros(n_factors)
        for f in range(n_factors):
            ics[f] = self._rank_ic(alphas[:, f], forward_returns)

        self._ic_history.append(ics.copy())

        # Apply exponential decay to historical ICs
        if len(self._ic_history) > 1:
            decayed = self.decay_adjust(
                np.array(self._ic_history), halflife=_DEFAULT_IC_HALFLIFE
            )
        else:
            decayed = ics

        # Normalise weights (absolute IC — direction is in the factor sign)
        abs_ic = np.abs(decayed)
        total = float(np.sum(abs_ic))
        if total < 1e-12:
            weights = np.ones(n_factors) / n_factors
        else:
            weights = abs_ic / total

        combined = alphas @ (weights * np.sign(decayed))

        log.debug(
            "ic_weighted_combine | top3 IC: %s",
            np.argsort(-np.abs(decayed))[:3].tolist(),
        )
        return combined

    @staticmethod
    def decay_adjust(ic_history: np.ndarray, halflife: int = 30) -> np.ndarray:
        """Exponentially decay old IC observations.

        Args:
            ic_history: (n_periods, n_factors)
            halflife:   half-life in periods for exponential weighting

        Returns:
            Weighted-average IC per factor (n_factors,).
        """
        n_periods = ic_history.shape[0]
        if n_periods == 0:
            return np.zeros(ic_history.shape[1] if ic_history.ndim > 1 else 0)

        lam = math.log(2.0) / max(halflife, 1)
        ages = np.arange(n_periods - 1, -1, -1, dtype=float)
        weights = np.exp(-lam * ages)
        weights /= float(np.sum(weights))

        # Weighted average across periods
        return weights @ ic_history

    # ---- private ----------------------------------------------------------

    @staticmethod
    def _cross_section_zscore(x: np.ndarray) -> np.ndarray:
        """Z-score a cross-section, winsorised at +/-3 sigma."""
        mean = float(np.nanmean(x))
        std = float(np.nanstd(x))
        if std < 1e-12:
            return np.zeros_like(x)
        z = (x - mean) / std
        return np.clip(z, -3.0, 3.0)

    @staticmethod
    def _rank_ic(factor: np.ndarray, returns: np.ndarray) -> float:
        """Spearman rank-IC between a factor and forward returns."""
        n = len(factor)
        if n < 5:
            return 0.0
        # Rank-based correlation (manual — avoids scipy dependency)
        rank_f = np.argsort(np.argsort(factor)).astype(float)
        rank_r = np.argsort(np.argsort(returns)).astype(float)
        mean_rf = float(np.mean(rank_f))
        mean_rr = float(np.mean(rank_r))
        cov = float(np.mean((rank_f - mean_rf) * (rank_r - mean_rr)))
        std_f = float(np.std(rank_f))
        std_r = float(np.std(rank_r))
        if std_f < 1e-12 or std_r < 1e-12:
            return 0.0
        return cov / (std_f * std_r)


# ---------------------------------------------------------------------------
# EnsembleDiversityManager
# ---------------------------------------------------------------------------
class EnsembleDiversityManager:
    """Measure and enforce diversity among model predictions.

    High-Flyer's edge comes partly from running hundreds of diverse
    models.  Diversity is measured as average pairwise disagreement.
    """

    def __init__(self, min_diversity: float = _DEFAULT_MIN_DIVERSITY) -> None:
        self.min_diversity = min_diversity
        log.info("EnsembleDiversityManager init | min_diversity=%.2f", min_diversity)

    def compute_diversity(self, predictions: List[np.ndarray]) -> float:
        """Compute ensemble diversity as mean pairwise disagreement.

        Uses 1 - mean(pairwise_correlation) as the diversity metric.

        Args:
            predictions: list of prediction arrays, each shape (n_samples,).

        Returns:
            Diversity score in [0, 1] where 1 = maximum disagreement.
        """
        n = len(predictions)
        if n < 2:
            return 0.0

        corr_sum = 0.0
        count = 0
        for i in range(n):
            for j in range(i + 1, n):
                pi = predictions[i].ravel()
                pj = predictions[j].ravel()
                if len(pi) != len(pj) or len(pi) == 0:
                    continue
                r = self._pearson(pi, pj)
                corr_sum += r
                count += 1

        if count == 0:
            return 0.0

        mean_corr = corr_sum / count
        diversity = float(np.clip(1.0 - mean_corr, 0.0, 1.0))
        log.debug("compute_diversity | n_models=%d diversity=%.3f", n, diversity)
        return diversity

    def select_diverse_ensemble(
        self,
        models: List[Any],
        predictions: Optional[List[np.ndarray]] = None,
        min_diversity: Optional[float] = None,
    ) -> List[int]:
        """Greedily select the most diverse subset of models.

        Uses forward-selection: start with all models, iteratively add
        the model that maximises ensemble diversity until the threshold
        is met.

        Args:
            models:        list of model objects (any type).
            predictions:   corresponding prediction arrays.
            min_diversity: override default minimum diversity.

        Returns:
            List of selected model indices.
        """
        threshold = min_diversity if min_diversity is not None else self.min_diversity

        if predictions is None or len(predictions) < 2:
            return list(range(len(models)))

        n = len(predictions)
        if n <= 2:
            return list(range(n))

        # Greedy forward selection
        selected: List[int] = [0]  # seed with first model
        remaining = set(range(1, n))

        while remaining:
            best_idx = -1
            best_div = -1.0

            for idx in remaining:
                candidate = selected + [idx]
                div = self.compute_diversity([predictions[i] for i in candidate])
                if div > best_div:
                    best_div = div
                    best_idx = idx

            if best_idx < 0:
                break

            selected.append(best_idx)
            remaining.discard(best_idx)

            # Stop when diversity threshold is met
            if best_div >= threshold:
                break

        log.info(
            "select_diverse_ensemble | %d/%d selected, diversity=%.3f",
            len(selected), n, best_div if best_idx >= 0 else 0.0,
        )
        return selected

    # ---- private ----------------------------------------------------------

    @staticmethod
    def _pearson(x: np.ndarray, y: np.ndarray) -> float:
        """Pearson correlation coefficient."""
        n = len(x)
        if n < 2:
            return 0.0
        mx, my = float(np.mean(x)), float(np.mean(y))
        sx, sy = float(np.std(x)), float(np.std(y))
        if sx < 1e-12 or sy < 1e-12:
            return 0.0
        return float(np.mean((x - mx) * (y - my))) / (sx * sy)


# ---------------------------------------------------------------------------
# HighFlyerSignalGenerator
# ---------------------------------------------------------------------------
class HighFlyerSignalGenerator:
    """Top-level signal generator combining all High-Flyer components.

    Orchestrates retail flow detection, multi-factor alpha, and ensemble
    diversity to produce a composite trading signal.

    Output dict schema:
      - retail_flow:   RetailFlowDetector output
      - factor_alpha:  combined factor alpha (float)
      - ensemble_div:  diversity score (float)
      - composite:     final signal strength [-1, 1]
      - confidence:    signal confidence [0, 100]
      - regime:        RETAIL_OPPORTUNITY | INSTITUTIONAL | NEUTRAL
    """

    def __init__(
        self,
        n_factors: int = _DEFAULT_N_FACTORS,
        small_trade_threshold: float = _DEFAULT_SMALL_TRADE_THRESHOLD,
        min_diversity: float = _DEFAULT_MIN_DIVERSITY,
    ) -> None:
        self.retail_detector = RetailFlowDetector(
            small_trade_threshold=small_trade_threshold,
        )
        self.factor_model = MultiFactorAlpha(n_factors=n_factors)
        self.diversity_mgr = EnsembleDiversityManager(min_diversity=min_diversity)
        log.info("HighFlyerSignalGenerator init")

    def generate(
        self,
        features: Dict[str, np.ndarray],
        volume_profile: np.ndarray,
        trade_sizes: np.ndarray,
        timestamps: Optional[np.ndarray] = None,
        forward_returns: Optional[np.ndarray] = None,
        model_predictions: Optional[List[np.ndarray]] = None,
    ) -> Dict[str, Any]:
        """Generate composite signal from all High-Flyer components.

        Args:
            features:          factor data dict for MultiFactorAlpha.
            volume_profile:    intraday volume profile.
            trade_sizes:       individual trade sizes.
            timestamps:        trade timestamps (optional).
            forward_returns:   for IC calculation (optional, uses equal weight if missing).
            model_predictions: ensemble predictions (optional).

        Returns:
            Composite signal dictionary.
        """
        # 1. Retail flow detection
        flow = self.retail_detector.detect(volume_profile, trade_sizes, timestamps)

        # 2. Multi-factor alpha
        alphas = self.factor_model.compute_alphas(features)
        if alphas.shape[0] > 0 and forward_returns is not None:
            combined_alpha = self.factor_model.ic_weighted_combine(
                alphas, forward_returns,
            )
            factor_signal = float(np.mean(combined_alpha))
        elif alphas.shape[0] > 0:
            # Equal-weight if no forward returns available
            factor_signal = float(np.mean(alphas))
        else:
            factor_signal = 0.0

        # 3. Ensemble diversity
        diversity = 0.0
        if model_predictions and len(model_predictions) >= 2:
            diversity = self.diversity_mgr.compute_diversity(model_predictions)

        # 4. Composite signal
        # Retail-dominated => more alpha opportunity (Chinese quant insight)
        retail_boost = max(0.0, flow["retail_score"] - 0.5) * 0.5
        composite = float(np.clip(
            factor_signal * (1.0 + retail_boost),
            -1.0,
            1.0,
        ))

        # Confidence: higher when diversity is good and retail flow is clear
        base_confidence = 50.0
        if diversity > self.diversity_mgr.min_diversity:
            base_confidence += 15.0
        if flow["retail_score"] > 0.6 or flow["retail_score"] < 0.35:
            base_confidence += 10.0
        if abs(factor_signal) > 0.5:
            base_confidence += 10.0
        confidence = min(85.0, base_confidence)

        # Regime classification
        if flow["retail_score"] > 0.6 and abs(composite) > 0.2:
            regime = "RETAIL_OPPORTUNITY"
        elif flow["retail_score"] < 0.35:
            regime = "INSTITUTIONAL"
        else:
            regime = "NEUTRAL"

        result = {
            "retail_flow": flow,
            "factor_alpha": factor_signal,
            "ensemble_diversity": diversity,
            "composite": composite,
            "confidence": confidence,
            "regime": regime,
        }

        log.info(
            "generate | composite=%.3f confidence=%.0f regime=%s",
            composite, confidence, regime,
        )
        return result
