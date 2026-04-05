"""Markov-Switching Regime Detection — replaces static VIX thresholds.

Uses statsmodels MarkovAutoregression to detect regime changes from
return data itself, rather than hardcoded VIX boundaries (18/30).

Produces regime_state.json consumed by:
  - strategy_regime_matrix.py: RegimeState.from_hmm()
  - bridge.py: signal prioritization by regime
  - config_writer.py: regime-scaled parameter adjustment

License: statsmodels is BSD-3.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("markov_regime")


@dataclass
class RegimeDetection:
    """Result of Markov regime detection."""
    current_regime: str           # "LOW_VOL", "NORMAL", "HIGH_VOL"
    regime_probability: float     # Probability of current regime [0,1]
    transition_matrix: List[List[float]]  # k_regimes × k_regimes
    regime_history: List[Dict[str, Any]]  # Last N regime states
    smoothed_probs: Dict[str, float]      # Current smoothed probabilities per regime
    model_info: Dict[str, Any]            # BIC, log-likelihood, etc.

    def to_dict(self) -> dict:
        return asdict(self)


# Regime labels by volatility level
_REGIME_LABELS = {0: "LOW_VOL", 1: "NORMAL", 2: "HIGH_VOL"}


def detect_regime(
    returns: np.ndarray,
    k_regimes: int = 3,
    lookback: int = 252,
) -> Optional[RegimeDetection]:
    """Fit Markov-switching model to return series and detect current regime.

    Args:
        returns: Array of daily log returns
        k_regimes: Number of hidden states (default 3: low/normal/high vol)
        lookback: Number of observations to use for fitting

    Returns:
        RegimeDetection with current state and transition matrix, or None on failure.
    """
    try:
        from statsmodels.tsa.regime_switching.markov_autoregression import MarkovAutoregression
    except ImportError:
        log.warning("statsmodels not installed — pip install statsmodels")
        return None

    if len(returns) < 60:
        log.warning("Insufficient data for regime detection: %d < 60", len(returns))
        return None

    # Use most recent lookback period
    data = returns[-lookback:] if len(returns) > lookback else returns

    try:
        # Fit Markov-switching AR(1) model
        model = MarkovAutoregression(
            data,
            k_regimes=k_regimes,
            order=1,            # AR(1) for return persistence
            switching_ar=False,  # Same AR coefficient across regimes
            switching_variance=True,  # Different variance per regime (key for vol regimes)
        )
        result = model.fit(disp=False, maxiter=200)

        # Get smoothed probabilities (filtered posterior)
        smoothed = result.smoothed_marginal_probabilities

        # Current regime = highest probability state
        current_probs = smoothed.iloc[-1].values
        current_state = int(np.argmax(current_probs))

        # Sort regimes by variance (ascending) to label them consistently
        # Regime with lowest variance = LOW_VOL, highest = HIGH_VOL
        regime_variances = []
        for i in range(k_regimes):
            # Extract variance parameter for regime i
            var_param = f"sigma2[{i}]"
            if var_param in result.params.index:
                regime_variances.append((i, result.params[var_param]))
            else:
                regime_variances.append((i, 0.0))

        # Sort by variance to create consistent labeling
        sorted_regimes = sorted(regime_variances, key=lambda x: x[1])
        regime_mapping = {}
        labels = ["LOW_VOL", "NORMAL", "HIGH_VOL"] if k_regimes == 3 else [
            f"REGIME_{i}" for i in range(k_regimes)
        ]
        for rank, (regime_idx, _) in enumerate(sorted_regimes):
            regime_mapping[regime_idx] = labels[min(rank, len(labels) - 1)]

        current_label = regime_mapping.get(current_state, f"REGIME_{current_state}")

        # Transition matrix
        transition = result.regime_transition.tolist() if hasattr(result.regime_transition, 'tolist') else []

        # Build regime history (last 20 days)
        history = []
        for i in range(-min(20, len(smoothed)), 0):
            probs = smoothed.iloc[i].values
            state = int(np.argmax(probs))
            history.append({
                "regime": regime_mapping.get(state, f"REGIME_{state}"),
                "probability": float(probs[state]),
                "all_probs": {
                    regime_mapping.get(j, f"REGIME_{j}"): float(probs[j])
                    for j in range(k_regimes)
                },
            })

        # Smoothed probabilities for current state
        smoothed_probs = {
            regime_mapping.get(j, f"REGIME_{j}"): float(current_probs[j])
            for j in range(k_regimes)
        }

        return RegimeDetection(
            current_regime=current_label,
            regime_probability=float(current_probs[current_state]),
            transition_matrix=transition,
            regime_history=history,
            smoothed_probs=smoothed_probs,
            model_info={
                "bic": float(result.bic),
                "llf": float(result.llf),
                "k_regimes": k_regimes,
                "nobs": int(result.nobs),
                "regime_mapping": regime_mapping,
            },
        )

    except Exception as e:
        log.warning("Markov regime detection failed: %s", str(e)[:200])
        return None


def run_regime_detection(
    output_path: Optional[str] = None,
    lookback_days: int = 252,
) -> Optional[RegimeDetection]:
    """Full pipeline: fetch returns, detect regime, write output.

    Reads strategy P&L history or market returns for regime detection.
    """
    if output_path is None:
        output_path = os.environ.get(
            "AEGIS_CONFIG_DIR", "/app/config"
        ) + "/regime_state.json"

    # Try to load market returns from VIX or SPY data
    returns = _load_market_returns(lookback_days)
    if returns is None or len(returns) < 60:
        log.warning("Could not load sufficient market returns for regime detection")
        return None

    detection = detect_regime(returns, k_regimes=3, lookback=lookback_days)
    if detection is None:
        return None

    # Write output
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **detection.to_dict(),
    }
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    log.info("Regime detection: %s (p=%.2f) → %s",
             detection.current_regime, detection.regime_probability, output_path)

    return detection


def _load_market_returns(lookback_days: int = 252) -> Optional[np.ndarray]:
    """Load market returns for regime detection.

    Priority:
      1. SPY daily returns from IBKR data provider
      2. SPY daily returns from yfinance (fallback)
      3. WAL-derived portfolio returns
    """
    # Try IBKR first (primary)
    try:
        from python_brain.ouroboros.ibkr_data_provider import get_provider
        provider = get_provider()
        spy = provider.get_price_data("SPY", days=lookback_days + 10, bar_size="1 day")
        if spy is not None and len(spy) >= 60:
            closes = spy["close"].dropna().values.astype(float)
            returns = np.diff(np.log(closes))
            return returns
    except Exception as e:
        log.debug("IBKR SPY fetch failed: %s", str(e)[:80])

    # Fallback to yfinance
    try:
        import yfinance as yf
        spy = yf.download("SPY", period=f"{lookback_days + 10}d", progress=False, auto_adjust=True)
        if spy is not None and len(spy) >= 60:
            close = spy["Close"]
            if hasattr(close, "columns"):
                close = close.iloc[:, 0]
            closes = close.dropna().values.astype(float)
            returns = np.diff(np.log(closes))
            return returns
    except Exception as e:
        log.warning("yfinance SPY fetch failed: %s", str(e)[:80])

    return None


def cointegration_test(series_a: np.ndarray, series_b: np.ndarray) -> Dict[str, Any]:
    """Engle-Granger cointegration test for pairs trading signals.

    Uses statsmodels.tsa.stattools.coint().

    Returns:
        Dict with test statistic, p-value, and whether cointegrated at 5% level.
    """
    try:
        from statsmodels.tsa.stattools import coint
    except ImportError:
        return {"error": "statsmodels not installed"}

    if len(series_a) != len(series_b) or len(series_a) < 30:
        return {"error": "insufficient data"}

    try:
        t_stat, p_value, crit_values = coint(series_a, series_b)
        return {
            "t_statistic": float(t_stat),
            "p_value": float(p_value),
            "critical_values": {
                "1%": float(crit_values[0]),
                "5%": float(crit_values[1]),
                "10%": float(crit_values[2]),
            },
            "cointegrated_5pct": p_value < 0.05,
        }
    except Exception as e:
        return {"error": str(e)[:100]}


def adf_stationarity_test(series: np.ndarray) -> Dict[str, Any]:
    """Augmented Dickey-Fuller test for stationarity.

    Used to validate that return series are stationary before GARCH fitting.
    """
    try:
        from statsmodels.tsa.stattools import adfuller
    except ImportError:
        return {"error": "statsmodels not installed"}

    if len(series) < 20:
        return {"error": "insufficient data"}

    try:
        result = adfuller(series, autolag="AIC")
        return {
            "adf_statistic": float(result[0]),
            "p_value": float(result[1]),
            "lags_used": int(result[2]),
            "nobs": int(result[3]),
            "stationary_5pct": result[1] < 0.05,
        }
    except Exception as e:
        return {"error": str(e)[:100]}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [Regime] %(levelname)s %(message)s")
    detection = run_regime_detection()
    if detection:
        print(f"\nCurrent regime: {detection.current_regime} (p={detection.regime_probability:.3f})")
        print(f"Smoothed probabilities: {json.dumps(detection.smoothed_probs, indent=2)}")
        print(f"Model BIC: {detection.model_info['bic']:.2f}")
    else:
        print("Regime detection failed")
