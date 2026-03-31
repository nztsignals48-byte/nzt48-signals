"""Book 125: Cointegration pairs entry/exit for natural long/inverse ETP pairs.

17 natural pairs (e.g., 3USL/3USS, QQQ3/QQQS). Long-only via ISA.
Entry: Z-score > 2.0 AND reverting (crossing threshold from outside).
Exit: Z-score returns to 0 (within 0.5 band).
Time-stop: 3x half-life. Max divergence: Z > 4.0 = broken.
Weekly ADF re-test in nightly: p > 0.10 = disable pair.

Consumed by: bridge.py _generate_signals() as new signal generator "CointPairs".
"""

import json
import math
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

_PARAMS_PATH = "/app/data/coint_pairs_params.json"

# Natural long/inverse ETP pairs (ISA: we can go long either leg)
_PAIRS = {
    "3USL_3USS": {"long": "3USL.L", "inverse": "3USS.L", "name": "S&P 3x"},
    "QQQ3_QQQS": {"long": "QQQ3.L", "inverse": "QQQS.L", "name": "Nasdaq 3x"},
    "3LTS_3STS": {"long": "3LTS.L", "inverse": "3STS.L", "name": "FTSE 3x"},
    "3LDE_3DES": {"long": "3LDE.L", "inverse": "3DES.L", "name": "DAX 3x"},
    "NVD3_3SNV": {"long": "NVD3.L", "inverse": "3SNV.L", "name": "Nvidia 3x"},
    "3TSL_TS3S": {"long": "3TSL.L", "inverse": "TS3S.L", "name": "Tesla 3x"},
    "3GOL_3GOS": {"long": "3GOL.L", "inverse": "3GOS.L", "name": "Gold 3x"},
    "3OIL_3OIS": {"long": "3OIL.L", "inverse": "3OIS.L", "name": "Oil 3x"},
    "3LVO_3SVO": {"long": "3LVO.L", "inverse": "3SVO.L", "name": "VIX 3x"},
}

# Build reverse lookup: symbol → list of (pair_key, role)
_SYMBOL_TO_PAIRS = {}
for pk, pv in _PAIRS.items():
    _SYMBOL_TO_PAIRS.setdefault(pv["long"], []).append((pk, "long"))
    _SYMBOL_TO_PAIRS.setdefault(pv["inverse"], []).append((pk, "inverse"))


@dataclass
class CointSignal:
    """Cointegration pair signal."""
    confidence: float
    z_score: float
    half_life: float
    pair_name: str
    long_leg: str
    direction: str  # Which leg to go long


@dataclass
class PairState:
    """Tracking state for a cointegration pair."""
    enabled: bool = True
    half_life: float = 20.0  # bars
    mean: float = 0.0
    std: float = 1.0
    last_adf_p: float = 0.01
    long_prices: deque = None
    inverse_prices: deque = None

    def __post_init__(self):
        if self.long_prices is None:
            self.long_prices = deque(maxlen=200)
        if self.inverse_prices is None:
            self.inverse_prices = deque(maxlen=200)


class CointPairsTracker:
    """Track cointegration pairs and generate entry signals."""

    def __init__(self):
        self._states = {}  # pair_key -> PairState
        self._params_loaded = False

    def _ensure_loaded(self):
        if self._params_loaded:
            return
        self._params_loaded = True
        if not os.path.exists(_PARAMS_PATH):
            # Initialize with defaults
            for pk in _PAIRS:
                self._states[pk] = PairState()
            return
        try:
            with open(_PARAMS_PATH) as f:
                data = json.load(f)
            for pk in _PAIRS:
                params = data.get(pk, {})
                self._states[pk] = PairState(
                    enabled=params.get("enabled", True),
                    half_life=params.get("half_life", 20.0),
                    mean=params.get("spread_mean", 0.0),
                    std=params.get("spread_std", 1.0),
                    last_adf_p=params.get("adf_p", 0.01),
                )
        except Exception:
            for pk in _PAIRS:
                self._states[pk] = PairState()

    def update_price(self, symbol, price):
        """Feed a price update for any symbol that's part of a pair."""
        self._ensure_loaded()
        pairs = _SYMBOL_TO_PAIRS.get(symbol, [])
        for pk, role in pairs:
            state = self._states.get(pk)
            if state is None:
                continue
            if role == "long":
                state.long_prices.append(price)
            else:
                state.inverse_prices.append(price)

    def check_signal(self, symbol, prices):
        """Check if a cointegration pair signal exists for this symbol.

        Args:
            symbol: The ticker being evaluated
            prices: Recent close prices (list, >=30 bars)

        Returns:
            CointSignal if entry conditions met, None otherwise.
        """
        self._ensure_loaded()
        pairs = _SYMBOL_TO_PAIRS.get(symbol, [])
        if not pairs:
            return None

        for pk, role in pairs:
            state = self._states.get(pk)
            if state is None or not state.enabled:
                continue

            # Check ADF validity
            if state.last_adf_p > 0.10:
                continue  # Cointegration broken — skip

            # Need both legs to have price data
            if len(state.long_prices) < 30 or len(state.inverse_prices) < 30:
                # Use the prices argument as this leg's data
                if role == "long":
                    for p in prices[-30:]:
                        state.long_prices.append(p)
                else:
                    for p in prices[-30:]:
                        state.inverse_prices.append(p)
                continue

            # Compute spread (log ratio)
            n = min(len(state.long_prices), len(state.inverse_prices), 30)
            long_p = list(state.long_prices)[-n:]
            inv_p = list(state.inverse_prices)[-n:]

            spreads = []
            for lp, ip in zip(long_p, inv_p):
                if lp > 0 and ip > 0:
                    spreads.append(math.log(lp) - math.log(ip))

            if len(spreads) < 20:
                continue

            # Z-score
            mean_s = sum(spreads) / len(spreads)
            var_s = sum((s - mean_s) ** 2 for s in spreads) / len(spreads)
            std_s = var_s ** 0.5
            if std_s < 1e-9:
                continue

            current_spread = spreads[-1]
            z = (current_spread - mean_s) / std_s

            # Entry conditions
            # Z > 2.0: spread is extended — go long the lagging leg
            # Z < -2.0: spread is compressed — go long the leading leg
            if abs(z) < 2.0:
                continue

            # Check reverting (current Z moving toward mean vs previous)
            if len(spreads) >= 2:
                prev_z = (spreads[-2] - mean_s) / std_s
                reverting = abs(z) < abs(prev_z)
                if not reverting:
                    continue  # Still diverging — wait for reversion

            # Max divergence stop: Z > 4.0 = cointegration broken
            if abs(z) > 4.0:
                continue

            # Determine which leg to go long
            pair_info = _PAIRS[pk]
            if z > 2.0:
                # Spread too wide: long leg is expensive, inverse is cheap → long inverse
                long_leg = pair_info["inverse"]
            else:
                # Spread too narrow: long leg is cheap → long the long leg
                long_leg = pair_info["long"]

            # Confidence based on Z-score magnitude
            conf = 55.0 + min(15, (abs(z) - 2.0) * 10)

            return CointSignal(
                confidence=round(min(80, conf), 1),
                z_score=round(z, 3),
                half_life=state.half_life,
                pair_name=pair_info["name"],
                long_leg=long_leg,
                direction="Long",
            )

        return None
