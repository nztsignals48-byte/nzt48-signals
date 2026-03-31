"""Book 135: Real-time fractional differentiation rolling buffer.

Per-instrument d calibrated nightly (smallest d where ADF p < 0.05).
Rolling dot product with FD weights for stationary-but-memory-preserving features.

Consumed by: bridge.py _compute_indicators() → adds frac_diff_value to indicator dict.
Calibration by: nightly_v6.py → writes /app/data/fracdiff_params.json.
"""

import json
import math
import os
from collections import defaultdict, deque

_PARAMS_PATH = "/app/data/fracdiff_params.json"
_params_cache = None
_params_mtime = 0

# Default d values by instrument type (used before nightly calibrates)
_DEFAULT_D = {
    "3x_etp": 0.30,
    "index": 0.40,
    "vix": 0.50,
    "inverse": 0.25,
    "default": 0.35,
}


def _load_params():
    """Load per-instrument d parameters from disk."""
    global _params_cache, _params_mtime
    if not os.path.exists(_PARAMS_PATH):
        return {}
    try:
        mtime = os.path.getmtime(_PARAMS_PATH)
        if _params_cache is not None and mtime == _params_mtime:
            return _params_cache
        with open(_PARAMS_PATH) as f:
            _params_cache = json.load(f)
        _params_mtime = mtime
        return _params_cache
    except Exception:
        return {}


def _get_d(symbol):
    """Get fractional diff parameter d for a symbol."""
    params = _load_params()
    if symbol in params:
        d = params[symbol].get("d", _DEFAULT_D["default"])
        # Clamp to valid range
        return max(0.1, min(0.7, d))
    # Default by type
    sym_upper = symbol.upper()
    if any(x in sym_upper for x in ("NVD3", "3TSL", "AMD3", "3USL", "QQQ3", "3GOL")):
        return _DEFAULT_D["3x_etp"]
    if any(x in sym_upper for x in ("VIX", "LVO", "UVXY")):
        return _DEFAULT_D["vix"]
    if any(x in sym_upper for x in ("QQQS", "3USS", "SUK2")):
        return _DEFAULT_D["inverse"]
    return _DEFAULT_D["default"]


def _compute_fd_weights(d, window):
    """Compute fractional differentiation weights using the binomial series.

    w_k = (-1)^k * C(d, k) where C(d,k) = d*(d-1)*...*(d-k+1) / k!
    Weights decay as k increases; truncated at `window`.
    """
    weights = [1.0]
    for k in range(1, window):
        w = weights[-1] * (-(d - k + 1)) / k
        if abs(w) < 1e-6:
            break
        weights.append(w)
    return weights


class RealTimeFracDiff:
    """Rolling fractional differentiation for a single instrument.

    Usage:
        fd = RealTimeFracDiff(d=0.35, window=200)
        for price in prices:
            value = fd.update(price)
    """

    def __init__(self, d=0.35, window=200):
        self.d = d
        self.window = window
        self.weights = _compute_fd_weights(d, window)
        self.buffer = deque(maxlen=window)

    def update(self, price):
        """Add a price and return the fractionally differenced value.

        Returns None if not enough data yet.
        """
        self.buffer.append(price)
        if len(self.buffer) < 2:
            return None

        # Dot product: sum(w_k * price_{t-k}) for k=0..min(len, len_weights)-1
        n = min(len(self.buffer), len(self.weights))
        value = 0.0
        buf = list(self.buffer)
        for k in range(n):
            value += self.weights[k] * buf[-(k + 1)]
        return value


# Per-ticker instances (persist across ticks)
_instances = {}


def get_fracdiff(symbol, window=200):
    """Get or create a FracDiff instance for a symbol."""
    if symbol not in _instances:
        d = _get_d(symbol)
        _instances[symbol] = RealTimeFracDiff(d=d, window=window)
    return _instances[symbol]


def calibrate_d(prices, max_d=0.7, min_d=0.1, step=0.05):
    """Binary search for smallest d where ADF test has p < 0.05.

    Used by nightly pipeline to calibrate per-instrument d.
    Returns optimal d value.
    """
    try:
        from statsmodels.tsa.stattools import adfuller
    except ImportError:
        return _DEFAULT_D["default"]  # No statsmodels → use default

    if len(prices) < 50:
        return _DEFAULT_D["default"]

    best_d = max_d
    lo, hi = min_d, max_d

    for _ in range(20):  # Max 20 iterations
        d = (lo + hi) / 2
        weights = _compute_fd_weights(d, len(prices))
        n = min(len(prices), len(weights))
        fd_series = []
        for t in range(n, len(prices)):
            val = sum(weights[k] * prices[t - k] for k in range(n))
            fd_series.append(val)

        if len(fd_series) < 30:
            lo = d
            continue

        try:
            adf_result = adfuller(fd_series, maxlag=10, autolag="AIC")
            p_value = adf_result[1]
            if p_value < 0.05:
                best_d = d
                hi = d  # Try smaller d
            else:
                lo = d  # Need more differencing
        except Exception:
            lo = d
            continue

    return round(best_d, 3)
