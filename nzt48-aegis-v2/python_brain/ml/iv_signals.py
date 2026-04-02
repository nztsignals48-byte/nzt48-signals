"""Volatility Surface Analysis and IV-Based Trading Signals — Book 78.

Analyzes implied volatility surfaces to generate trading signals:
  - VIX term structure (contango/backwardation, roll yield)
  - IV skew analysis (25-delta put/call skew, butterfly, risk reversal)
  - Volatility risk premium (IV vs realised vol spread)

Signals are combined and regime-adjusted via IVSignalGenerator.

State: /app/data/iv_history.json (rolling history for percentile calculations).

Bridge.py integration:
    from python_brain.ml.iv_signals import (
        IVSignalGenerator, VolatilitySurface, OptionQuote,
    )
    generator = IVSignalGenerator()
    signals = generator.generate_signals(surface, regime="trending")
    adjusted = generator.confidence_adjustment(base_confidence, signals)

Usage:
    from python_brain.ml.iv_signals import (
        IVSignalGenerator, VIXTermStructureAnalyzer,
        IVSkewAnalyzer, VolRiskPremiumCalculator,
        VolatilitySurface, OptionQuote,
    )
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("iv_signals")

__all__ = [
    "OptionQuote",
    "VolatilitySurface",
    "VIXTermStructureAnalyzer",
    "IVSkewAnalyzer",
    "VolRiskPremiumCalculator",
    "IVSignalGenerator",
]

# ── Constants ──────────────────────────────────────────────────────────

HISTORY_PATH = Path("/app/data/iv_history.json")
MAX_HISTORY_SIZE = 500      # Rolling window for percentile calcs
VRP_HIGH_PERCENTILE = 80    # Sell vol when VRP above this
VRP_LOW_PERCENTILE = 20     # Buy vol when VRP below this


# ── Dataclasses ────────────────────────────────────────────────────────

@dataclass
class OptionQuote:
    """A single option quote with greeks."""
    symbol: str
    expiry: str                 # ISO date string
    strike: float
    option_type: str            # "call" or "put"
    bid: float
    ask: float
    implied_vol: float
    delta: float
    gamma: float
    theta: float
    vega: float
    underlying_price: float
    volume: int = 0
    open_interest: int = 0

    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    def spread_pct(self) -> float:
        mid = self.mid()
        if mid < 1e-10:
            return 0.0
        return (self.ask - self.bid) / mid


@dataclass
class VolatilitySurface:
    """Volatility surface snapshot."""
    symbol: str
    timestamp: str              # ISO datetime
    underlying_price: float
    term_structure: Dict[str, float]   # tenor_label -> IV (e.g. "7d" -> 0.25)
    skew: Dict[str, float]             # "put_25d", "atm", "call_25d" -> IV
    risk_premium: float = 0.0          # IV - realised vol


# ── VIX Term Structure Analyzer ────────────────────────────────────────

class VIXTermStructureAnalyzer:
    """Analyzes VIX term structure for contango/backwardation signals.

    Contango (front < back) = normal, mean-reverting vol environment.
    Backwardation (front > back) = fear, elevated near-term risk.
    """

    def analyze(self, vix_spot: float, vix_front: float,
                vix_second: float, vix_back: float) -> Dict[str, Any]:
        """Analyze the VIX term structure.

        Args:
            vix_spot: Current VIX level.
            vix_front: Front month VIX future.
            vix_second: Second month VIX future.
            vix_back: Back month VIX future (3-6 months).

        Returns:
            Dict with structure classification, slope, roll_yield, and signal.
        """
        if vix_spot <= 0 or vix_front <= 0:
            return {
                "structure": "UNKNOWN",
                "slope": 0.0,
                "roll_yield": 0.0,
                "signal": "NEUTRAL",
                "vix_level": vix_spot,
            }

        # Slope: annualised % spread between front and back
        slope = (vix_back - vix_front) / vix_front if vix_front > 1e-6 else 0.0
        structure = self._classify_term_structure(slope)
        roll_yield = self._compute_roll_yield(vix_front, vix_second)

        # Signal logic
        if structure in ("STEEP_BACKWARDATION",) and vix_spot > 25:
            signal = "HIGH_FEAR"
        elif structure == "STEEP_CONTANGO" and vix_spot < 15:
            signal = "COMPLACENCY"
        elif structure in ("MILD_BACKWARDATION", "STEEP_BACKWARDATION"):
            signal = "RISK_OFF"
        elif structure in ("MILD_CONTANGO", "STEEP_CONTANGO"):
            signal = "RISK_ON"
        else:
            signal = "NEUTRAL"

        return {
            "structure": structure,
            "slope": round(slope, 4),
            "roll_yield": round(roll_yield, 4),
            "signal": signal,
            "vix_level": round(vix_spot, 2),
        }

    def _classify_term_structure(self, slope: float) -> str:
        """Classify term structure by slope magnitude.

        Args:
            slope: Normalised slope (back - front) / front.

        Returns:
            One of: STEEP_CONTANGO, MILD_CONTANGO, FLAT,
                    MILD_BACKWARDATION, STEEP_BACKWARDATION.
        """
        if slope > 0.10:
            return "STEEP_CONTANGO"
        elif slope > 0.03:
            return "MILD_CONTANGO"
        elif slope > -0.03:
            return "FLAT"
        elif slope > -0.10:
            return "MILD_BACKWARDATION"
        else:
            return "STEEP_BACKWARDATION"

    def _compute_roll_yield(self, front: float, second: float) -> float:
        """Compute annualised roll yield from front to second month.

        Args:
            front: Front month VIX future price.
            second: Second month VIX future price.

        Returns:
            Annualised roll yield (positive = contango = short vol profits).
        """
        if front <= 0 or second <= 0:
            return 0.0
        monthly_roll = (second - front) / front
        return monthly_roll * 12.0  # Annualise


# ── IV Skew Analyzer ──────────────────────────────────────────────────

class IVSkewAnalyzer:
    """Analyzes implied volatility skew from 25-delta options.

    Skew = (put_25d_iv - call_25d_iv) / atm_iv
    Butterfly = (put_25d_iv + call_25d_iv) / 2 - atm_iv
    Risk Reversal = call_25d_iv - put_25d_iv
    """

    def __init__(self):
        self._history: List[float] = []

    def analyze(self, put_25d_iv: float, atm_iv: float,
                call_25d_iv: float) -> Dict[str, Any]:
        """Analyze IV skew from 25-delta puts, ATM, and 25-delta calls.

        Args:
            put_25d_iv: 25-delta put implied volatility.
            atm_iv: At-the-money implied volatility.
            call_25d_iv: 25-delta call implied volatility.

        Returns:
            Dict with skew_ratio, butterfly, risk_reversal,
            percentile, shift_direction, and signal.
        """
        if atm_iv <= 0:
            return {
                "skew_ratio": 0.0,
                "butterfly": 0.0,
                "risk_reversal": 0.0,
                "skew_percentile": 50.0,
                "shift": "STABLE",
                "signal": "NEUTRAL",
            }

        skew_ratio = (put_25d_iv - call_25d_iv) / atm_iv
        butterfly = (put_25d_iv + call_25d_iv) / 2.0 - atm_iv
        risk_reversal = call_25d_iv - put_25d_iv

        # Track skew history
        self._history.append(skew_ratio)
        if len(self._history) > MAX_HISTORY_SIZE:
            self._history = self._history[-MAX_HISTORY_SIZE:]

        percentile = self._skew_percentile(skew_ratio, self._history)
        shift = self._detect_skew_shift(self._history)

        # Signal generation
        if percentile > 90 and shift == "STEEPENING":
            signal = "EXTREME_FEAR"
        elif percentile < 10 and shift == "FLATTENING":
            signal = "EXTREME_COMPLACENCY"
        elif percentile > 75:
            signal = "ELEVATED_SKEW"
        elif percentile < 25:
            signal = "LOW_SKEW"
        else:
            signal = "NEUTRAL"

        return {
            "skew_ratio": round(skew_ratio, 4),
            "butterfly": round(butterfly, 4),
            "risk_reversal": round(risk_reversal, 4),
            "skew_percentile": round(percentile, 1),
            "shift": shift,
            "signal": signal,
        }

    def _skew_percentile(self, current_skew: float,
                         history: List[float]) -> float:
        """Compute percentile rank of current skew vs history.

        Args:
            current_skew: Current skew ratio.
            history: Historical skew values.

        Returns:
            Percentile rank [0, 100].
        """
        if len(history) < 5:
            return 50.0

        arr = np.array(history)
        return float(np.sum(arr <= current_skew) / len(arr) * 100.0)

    def _detect_skew_shift(self, history: List[float],
                           window: int = 20) -> str:
        """Detect whether skew is steepening, flattening, or stable.

        Uses linear regression slope over recent window.

        Args:
            history: Skew ratio history.
            window: Lookback window for trend detection.

        Returns:
            One of: STEEPENING, FLATTENING, STABLE.
        """
        if len(history) < window:
            return "STABLE"

        recent = np.array(history[-window:])
        x = np.arange(window, dtype=np.float64)

        # Linear regression slope
        x_mean = np.mean(x)
        y_mean = np.mean(recent)
        numerator = np.sum((x - x_mean) * (recent - y_mean))
        denominator = np.sum((x - x_mean) ** 2)

        if abs(denominator) < 1e-10:
            return "STABLE"

        slope = numerator / denominator

        # Normalise slope by std of recent values
        std = np.std(recent)
        if std < 1e-10:
            return "STABLE"

        normalised_slope = slope / std

        if normalised_slope > 0.1:
            return "STEEPENING"
        elif normalised_slope < -0.1:
            return "FLATTENING"
        return "STABLE"


# ── Volatility Risk Premium Calculator ─────────────────────────────────

class VolRiskPremiumCalculator:
    """Computes the Volatility Risk Premium (VRP).

    VRP = Implied Vol - Realised Vol.
    Positive VRP = options are expensive relative to realised moves.
    Negative VRP = options are cheap (unusual, often before big moves).
    """

    def __init__(self):
        self._vrp_history: List[float] = []

    def compute(self, implied_vol: float, realized_vol_5d: float,
                realized_vol_20d: float) -> Dict[str, Any]:
        """Compute VRP and generate a trading signal.

        Args:
            implied_vol: Current implied volatility.
            realized_vol_5d: 5-day realised volatility.
            realized_vol_20d: 20-day realised volatility.

        Returns:
            Dict with vrp, vrp_20d, vrp_percentile, and signal.
        """
        vrp_5d = implied_vol - realized_vol_5d
        vrp_20d = implied_vol - realized_vol_20d

        # Use 20d VRP as primary (more stable)
        self._vrp_history.append(vrp_20d)
        if len(self._vrp_history) > MAX_HISTORY_SIZE:
            self._vrp_history = self._vrp_history[-MAX_HISTORY_SIZE:]

        # Percentile
        if len(self._vrp_history) >= 10:
            arr = np.array(self._vrp_history)
            percentile = float(np.sum(arr <= vrp_20d) / len(arr) * 100.0)
        else:
            percentile = 50.0

        signal = self._vrp_signal(vrp_20d, percentile)

        return {
            "vrp_5d": round(vrp_5d, 4),
            "vrp_20d": round(vrp_20d, 4),
            "vrp_percentile": round(percentile, 1),
            "signal": signal,
        }

    def _vrp_signal(self, vrp: float, vrp_percentile: float) -> str:
        """Generate VRP-based signal.

        Args:
            vrp: Current volatility risk premium.
            vrp_percentile: Percentile rank of current VRP.

        Returns:
            One of: SELL_VOL, NEUTRAL, BUY_VOL.
        """
        if vrp_percentile > VRP_HIGH_PERCENTILE and vrp > 0:
            return "SELL_VOL"
        elif vrp_percentile < VRP_LOW_PERCENTILE or vrp < 0:
            return "BUY_VOL"
        return "NEUTRAL"


# ── IV Signal Generator ───────────────────────────────────────────────

class IVSignalGenerator:
    """Main class combining all IV-based signals.

    Orchestrates term structure, skew, and VRP analysis, then
    adjusts signal confidence based on the IV regime.
    """

    def __init__(self):
        self._term_analyzer = VIXTermStructureAnalyzer()
        self._skew_analyzer = IVSkewAnalyzer()
        self._vrp_calc = VolRiskPremiumCalculator()
        self._load_history()

    def generate_signals(self, surface: VolatilitySurface,
                         regime: str = "unknown") -> Dict[str, Any]:
        """Generate combined IV signals from a volatility surface.

        Args:
            surface: VolatilitySurface snapshot.
            regime: Current market regime (e.g. "trending", "mean_reverting").

        Returns:
            Dict with term_structure, skew, vrp sub-signals and overall assessment.
        """
        term_signal = self._term_structure_signal(surface)
        skew_signal = self._skew_signal(surface)
        vrp_signal = self._vrp_signal(surface)

        # Overall assessment
        signals = [term_signal.get("signal", "NEUTRAL"),
                   skew_signal.get("signal", "NEUTRAL"),
                   vrp_signal.get("signal", "NEUTRAL")]

        fear_count = sum(1 for s in signals if s in (
            "HIGH_FEAR", "EXTREME_FEAR", "RISK_OFF", "ELEVATED_SKEW", "BUY_VOL",
        ))
        greed_count = sum(1 for s in signals if s in (
            "COMPLACENCY", "EXTREME_COMPLACENCY", "RISK_ON", "LOW_SKEW", "SELL_VOL",
        ))

        if fear_count >= 2:
            overall = "RISK_OFF"
        elif greed_count >= 2:
            overall = "RISK_ON"
        else:
            overall = "NEUTRAL"

        result = {
            "symbol": surface.symbol,
            "timestamp": surface.timestamp,
            "regime": regime,
            "term_structure": term_signal,
            "skew": skew_signal,
            "vrp": vrp_signal,
            "overall": overall,
            "fear_score": fear_count,
            "greed_score": greed_count,
        }

        self._save_history()
        return result

    def confidence_adjustment(self, base_confidence: float,
                              iv_signals: Dict[str, Any]) -> float:
        """Adjust signal confidence based on IV regime.

        High-fear IV environments reduce long confidence, increase short
        confidence. High-greed does the reverse.

        Args:
            base_confidence: Original confidence [0, 100].
            iv_signals: Output from generate_signals().

        Returns:
            Adjusted confidence [0, 100].
        """
        overall = iv_signals.get("overall", "NEUTRAL")
        fear_score = iv_signals.get("fear_score", 0)
        greed_score = iv_signals.get("greed_score", 0)

        adjustment = 0.0

        if overall == "RISK_OFF":
            # High fear: reduce confidence in long signals
            adjustment = -5.0 * fear_score
        elif overall == "RISK_ON":
            # High greed: slight confidence boost but cap it
            adjustment = 2.0 * greed_score

        # VRP-specific adjustment
        vrp_signal = iv_signals.get("vrp", {}).get("signal", "NEUTRAL")
        if vrp_signal == "BUY_VOL":
            adjustment -= 3.0  # Options cheap = something may be brewing
        elif vrp_signal == "SELL_VOL":
            adjustment += 1.0  # Options expensive = likely range-bound

        # Skew extreme adjustment
        skew_pct = iv_signals.get("skew", {}).get("skew_percentile", 50.0)
        if skew_pct > 90:
            adjustment -= 5.0
        elif skew_pct < 10:
            adjustment += 2.0

        adjusted = base_confidence + adjustment
        return float(np.clip(adjusted, 0.0, 100.0))

    def iv_skew_directional_signal(self, surface: VolatilitySurface,
                                   regime: str = "unknown") -> Dict[str, Any]:
        """Generate directional signal from IV skew widening (Book 130).

        High skew = elevated tail risk = tactical short vol setup.
        Signal: when skew_percentile > 80 and steepening, tactical short.

        Args:
            surface: VolatilitySurface snapshot.
            regime: Current market regime.

        Returns:
            Dict with signal confidence based on IV rank and skew steepness.
        """
        skew_analysis = self._skew_signal(surface)
        skew_pct = skew_analysis.get("skew_percentile", 50.0)
        shift = skew_analysis.get("shift", "STABLE")

        # IV rank: normalise percentile to [0, 100] for confidence
        iv_rank = skew_pct

        # Skew steepness confidence boost
        steepness_boost = 0.0
        if shift == "STEEPENING":
            steepness_boost = 10.0
        elif shift == "FLATTENING":
            steepness_boost = -5.0

        # Base confidence: IV rank percentile
        if iv_rank > 80 and shift == "STEEPENING":
            confidence = min(90, 60 + int((iv_rank - 80) * 3) + int(steepness_boost))
            direction = "short_vol"  # Tactical short when skew widens
        elif iv_rank < 20 and shift == "FLATTENING":
            confidence = min(85, 50 + int((20 - iv_rank) * 2))
            direction = "long_vol"
        else:
            confidence = 50 + int((iv_rank - 50) * 0.6)
            direction = "neutral"

        return {
            "signal": "IV_SKEW_DIRECTIONAL",
            "direction": direction,
            "confidence": max(0, int(confidence)),
            "iv_rank": round(iv_rank, 1),
            "skew_steepness": shift,
            "regime": regime,
            "skew_ratio": skew_analysis.get("skew_ratio", 0.0),
        }

    # ── Private Methods ────────────────────────────────────────────────

    def _term_structure_signal(self, surface: VolatilitySurface) -> Dict[str, Any]:
        """Extract term structure signal from surface."""
        ts = surface.term_structure
        if not ts:
            return {"signal": "NEUTRAL", "structure": "UNKNOWN"}

        # Map term structure keys to VIX-like inputs
        # Expected keys: "7d", "30d", "60d", "90d" (or similar)
        sorted_tenors = sorted(ts.items(), key=lambda kv: _tenor_to_days(kv[0]))

        if len(sorted_tenors) < 2:
            return {"signal": "NEUTRAL", "structure": "INSUFFICIENT_DATA"}

        # Use first as spot proxy, then map to front/second/back
        vals = [v for _, v in sorted_tenors]
        spot = vals[0]
        front = vals[0] if len(vals) >= 1 else spot
        second = vals[1] if len(vals) >= 2 else front
        back = vals[-1]

        return self._term_analyzer.analyze(spot, front, second, back)

    def _skew_signal(self, surface: VolatilitySurface) -> Dict[str, Any]:
        """Extract skew signal from surface."""
        skew = surface.skew
        if not skew:
            return {"signal": "NEUTRAL", "skew_ratio": 0.0}

        put_25d = skew.get("put_25d", 0.0)
        atm = skew.get("atm", 0.0)
        call_25d = skew.get("call_25d", 0.0)

        if atm <= 0:
            return {"signal": "NEUTRAL", "skew_ratio": 0.0}

        return self._skew_analyzer.analyze(put_25d, atm, call_25d)

    def _vrp_signal(self, surface: VolatilitySurface) -> Dict[str, Any]:
        """Extract VRP signal from surface.

        Uses the ATM IV as implied vol, and the risk_premium field
        to back out realised vol (if available).
        """
        atm_iv = surface.skew.get("atm", 0.0) if surface.skew else 0.0
        vrp = surface.risk_premium

        if atm_iv <= 0:
            return {"signal": "NEUTRAL", "vrp_20d": 0.0}

        # Estimate realised vols from VRP if not separately available
        realized_20d = atm_iv - vrp
        realized_5d = realized_20d * 1.1  # 5d tends to be noisier

        return self._vrp_calc.compute(atm_iv, realized_5d, realized_20d)

    def _save_history(self) -> None:
        """Persist skew and VRP history for percentile calculations."""
        try:
            HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "skew_history": self._skew_analyzer._history[-MAX_HISTORY_SIZE:],
                "vrp_history": self._vrp_calc._vrp_history[-MAX_HISTORY_SIZE:],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            with open(str(HISTORY_PATH), "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.warning("Failed to save IV history: %s", e)

    def _load_history(self) -> None:
        """Load persisted history for percentile calculations."""
        if not HISTORY_PATH.exists():
            return

        try:
            with open(str(HISTORY_PATH), "r") as f:
                data = json.load(f)

            self._skew_analyzer._history = data.get("skew_history", [])
            self._vrp_calc._vrp_history = data.get("vrp_history", [])
            log.info("Loaded IV history: %d skew, %d vrp observations",
                     len(self._skew_analyzer._history),
                     len(self._vrp_calc._vrp_history))
        except Exception as e:
            log.warning("Failed to load IV history: %s", e)


# ── Helpers ────────────────────────────────────────────────────────────

def _tenor_to_days(tenor: str) -> int:
    """Convert tenor string like '7d', '30d', '3m' to days."""
    tenor = tenor.strip().lower()
    try:
        if tenor.endswith("d"):
            return int(tenor[:-1])
        elif tenor.endswith("w"):
            return int(tenor[:-1]) * 7
        elif tenor.endswith("m"):
            return int(tenor[:-1]) * 30
        elif tenor.endswith("y"):
            return int(tenor[:-1]) * 365
        else:
            return int(tenor)
    except (ValueError, IndexError):
        return 9999
