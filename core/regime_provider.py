"""
NZT-48 Trading System -- RegimeProvider
=========================================
Single source of truth for the current market regime. All downstream
consumers (strategies, PDF generators, Telegram, dashboard) MUST query
this provider rather than running their own regime classification.

Delegates to feeds/regime_classifier.py for the actual 8-state logic,
then wraps the result in a canonical RegimeSnapshot schema.

Rule: Never returns "UNKNOWN" unless the system state is DEGRADED or HALTED.

Usage:
    from core.regime_provider import RegimeProvider
    provider = RegimeProvider()
    provider.update(vix=18.5, spx_data=spx_df, qqq_data=qqq_df)
    regime = provider.get_regime()        # -> RegimeSnapshot
    tag = provider.get_regime_tag()       # -> "TRENDING_UP_MOD"
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from core.schemas import RegimeSnapshot

logger = logging.getLogger("nzt48.core.regime_provider")

# Attempt to import the existing regime classifier
try:
    from feeds.regime_classifier import RegimeClassifier
    _HAS_CLASSIFIER = True
except ImportError:
    _HAS_CLASSIFIER = False
    logger.warning(
        "feeds.regime_classifier not available -- RegimeProvider "
        "will operate in fallback mode (VIX-only classification)"
    )

# Attempt to import the RegimeState enum for value extraction
try:
    from models import RegimeState
    _HAS_REGIME_STATE = True
except ImportError:
    _HAS_REGIME_STATE = False


class RegimeProvider:
    """Single regime source for the entire system.

    Wraps the 8-state RegimeClassifier from feeds/ and produces
    canonical RegimeSnapshot objects consumed by all delivery surfaces.

    The result is cached for the current tick cycle and invalidated
    on the next update() call.

    Thread-safe: all state access is guarded by a threading.Lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._classifier: Optional[object] = None
        self._cached_snapshot: Optional[RegimeSnapshot] = None
        self._tick_id: int = 0          # Incremented on each update()
        self._system_degraded: bool = False

        if _HAS_CLASSIFIER:
            self._classifier = RegimeClassifier()
            logger.info("RegimeProvider initialised with RegimeClassifier")
        else:
            logger.warning("RegimeProvider initialised WITHOUT RegimeClassifier (fallback mode)")

    # ------------------------------------------------------------------
    # Update (called once per scan tick)
    # ------------------------------------------------------------------

    def update(
        self,
        vix: float = 0.0,
        spx_data: object = None,
        qqq_data: object = None,
        *,
        spy_price: float = 0.0,
        spy_vwap: float = 0.0,
        qqq_price: float = 0.0,
        qqq_vwap: float = 0.0,
        ema9: float = 0.0,
        ema20: float = 0.0,
        ema50: float = 0.0,
        slope: float = 0.0,
        spy_change_pct: float = 0.0,
        or_range: float = 0.0,
        normal_range: float = 0.0,
        system_degraded: bool = False,
    ) -> RegimeSnapshot:
        """Update the regime classification with fresh market data.

        If the full RegimeClassifier is available and sufficient inputs
        are provided, it delegates to the 8-state classifier. Otherwise,
        it falls back to a simple VIX-based classification.

        Args:
            vix:              Current VIX level.
            spx_data:         SPX/SPY DataFrame (used for slope computation if needed).
            qqq_data:         QQQ DataFrame (used for slope computation if needed).
            spy_price:        Current SPY price.
            spy_vwap:         SPY VWAP level.
            qqq_price:        Current QQQ price.
            qqq_vwap:         QQQ VWAP level.
            ema9:             QQQ EMA(9).
            ema20:            QQQ EMA(20).
            ema50:            QQQ EMA(50).
            slope:            Price slope per bar (normalised).
            spy_change_pct:   SPY intraday % change.
            or_range:         Opening range width.
            normal_range:     20-day average range.
            system_degraded:  If True, allows "UNKNOWN" to be returned.

        Returns:
            RegimeSnapshot with the current regime state.
        """
        with self._lock:
            self._tick_id += 1
            self._system_degraded = system_degraded

            # Attempt full classification via the existing classifier
            tag = self._classify_full(
                vix=vix,
                qqq_price=qqq_price,
                qqq_vwap=qqq_vwap,
                spy_price=spy_price,
                spy_vwap=spy_vwap,
                ema9=ema9,
                ema20=ema20,
                ema50=ema50,
                slope=slope,
                spy_change_pct=spy_change_pct,
                or_range=or_range,
                normal_range=normal_range,
            )

            # Fall back to VIX-only if classifier is unavailable or inputs insufficient
            if tag is None:
                tag = self._classify_vix_only(vix)

            # Enforce: never return UNKNOWN unless degraded
            if tag == "UNKNOWN" and not self._system_degraded:
                tag = "RANGE_BOUND"
                logger.debug("Regime UNKNOWN overridden to RANGE_BOUND (system not degraded)")

            # Build confidence estimate
            confidence = self._estimate_confidence(tag, vix)

            # Determine SPX trend direction from input or data
            spx_trend = self._determine_spx_trend(spy_change_pct, spx_data)

            # Build evidence dict
            evidence = {
                "vix": round(vix, 2),
                "spy_change_pct": round(spy_change_pct, 3),
                "qqq_price": round(qqq_price, 2),
                "qqq_vwap": round(qqq_vwap, 2),
                "slope": round(slope, 6),
                "tick_id": self._tick_id,
            }

            snapshot = RegimeSnapshot(
                tag=tag,
                confidence=confidence,
                evidence=evidence,
                vix=round(vix, 2),
                spx_trend=spx_trend,
                timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            )

            self._cached_snapshot = snapshot
            logger.debug(
                "Regime updated (tick #%d): %s (confidence=%.2f, VIX=%.1f)",
                self._tick_id, tag, confidence, vix,
            )
            return snapshot

    # ------------------------------------------------------------------
    # Query (read-only, cached)
    # ------------------------------------------------------------------

    def get_regime(self) -> RegimeSnapshot:
        """Return the most recent RegimeSnapshot.

        If update() has never been called, returns a default RANGE_BOUND
        snapshot (or UNKNOWN if system is degraded).
        """
        with self._lock:
            if self._cached_snapshot is not None:
                return self._cached_snapshot

            # Never called -- return default
            default_tag = "UNKNOWN" if self._system_degraded else "RANGE_BOUND"
            return RegimeSnapshot(
                tag=default_tag,
                confidence=0.0,
                evidence={"reason": "RegimeProvider.update() not yet called"},
                vix=0.0,
                spx_trend="FLAT",
            )

    def get_regime_tag(self) -> str:
        """Return just the regime tag string (convenience)."""
        return self.get_regime().tag

    def get_regime_info(self) -> dict:
        """Return regime info dict compatible with the existing RegimeClassifier API."""
        snapshot = self.get_regime()
        info = snapshot.to_dict()
        # Add compatibility keys that existing code expects
        info["regime"] = snapshot.tag
        if self._classifier is not None and _HAS_CLASSIFIER:
            try:
                info.update(self._classifier.get_regime_info())
            except Exception:
                pass
        return info

    # ------------------------------------------------------------------
    # Internal classification methods
    # ------------------------------------------------------------------

    def _classify_full(
        self,
        vix: float,
        qqq_price: float,
        qqq_vwap: float,
        spy_price: float,
        spy_vwap: float,
        ema9: float,
        ema20: float,
        ema50: float,
        slope: float,
        spy_change_pct: float,
        or_range: float,
        normal_range: float,
    ) -> Optional[str]:
        """Attempt full 8-state classification via the existing RegimeClassifier.

        Returns None if classifier is unavailable or inputs are insufficient.
        """
        if self._classifier is None or not _HAS_CLASSIFIER:
            return None

        # Need at least price and vwap for a meaningful classification
        if qqq_price <= 0 or spy_price <= 0:
            return None
        if qqq_vwap <= 0 or spy_vwap <= 0:
            return None

        try:
            result = self._classifier.classify(
                qqq_price=qqq_price,
                qqq_vwap=qqq_vwap,
                spy_price=spy_price,
                spy_vwap=spy_vwap,
                ema9=ema9,
                ema20=ema20,
                ema50=ema50,
                slope_per_bar=slope,
                vix=vix,
                spy_change_pct=spy_change_pct,
                or_range=or_range,
                normal_range=normal_range,
            )

            # Extract the string value from the RegimeState enum
            if _HAS_REGIME_STATE and isinstance(result, RegimeState):
                return result.value
            elif hasattr(result, "value"):
                return str(result.value)
            else:
                return str(result)

        except Exception as e:
            logger.warning("Full regime classification failed: %s", e)
            return None

    def _classify_vix_only(self, vix: float) -> str:
        """Fallback: classify regime from VIX alone.

        This is intentionally conservative -- it can only detect
        extreme states (SHOCK, RISK_OFF, HIGH_VOLATILITY) and defaults
        to RANGE_BOUND otherwise.
        """
        if vix > 45:
            return "SHOCK"
        elif vix > 35:
            return "RISK_OFF"
        elif vix > 25:
            return "HIGH_VOLATILITY"
        elif self._system_degraded:
            return "UNKNOWN"
        else:
            return "RANGE_BOUND"

    def _estimate_confidence(self, tag: str, vix: float) -> float:
        """Estimate classification confidence (0.0-1.0).

        Higher confidence for extreme states (VIX-driven) which are
        unambiguous. Lower for RANGE_BOUND which is the default catch-all.
        """
        if tag == "SHOCK":
            return min(1.0, vix / 50.0)
        elif tag == "RISK_OFF":
            return min(1.0, vix / 40.0)
        elif tag == "HIGH_VOLATILITY":
            return min(0.9, vix / 30.0)
        elif tag in ("TRENDING_UP_STRONG", "TRENDING_DOWN_STRONG"):
            return 0.8
        elif tag in ("TRENDING_UP_MOD", "TRENDING_DOWN_MOD"):
            return 0.65
        elif tag == "RANGE_BOUND":
            return 0.5
        elif tag == "UNKNOWN":
            return 0.0
        return 0.5

    def _determine_spx_trend(self, spy_change_pct: float, spx_data: object) -> str:
        """Determine the SPX trend direction from available data."""
        if spy_change_pct > 0.3:
            return "UP"
        elif spy_change_pct < -0.3:
            return "DOWN"

        # Attempt to read from DataFrame if provided
        if spx_data is not None:
            try:
                import pandas as pd
                if isinstance(spx_data, pd.DataFrame) and len(spx_data) >= 2:
                    close = spx_data["close"].values if "close" in spx_data.columns else spx_data["Close"].values
                    change = (close[-1] - close[-2]) / close[-2] * 100
                    if change > 0.3:
                        return "UP"
                    elif change < -0.3:
                        return "DOWN"
            except Exception:
                pass

        return "FLAT"
