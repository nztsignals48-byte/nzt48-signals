"""
NZT-48 Trading System — Market Structure Intelligence
Sections 8-9 of the Master Spec.

Fetches and interprets:
  - GEX / DIX from SqueezeMetrics (Section 8)
  - VIX / VIX3M term structure from CBOE / yfinance
  - Market internals: TICK, TRIN, ADD, VOLD (Section 9)

All results are cached for 15 minutes.  If a live fetch fails the last
cached values are returned so downstream consumers never crash.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("nzt48.market_structure")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SQUEEZEMETRICS_URL = "https://squeezemetrics.com/monitor/dix"
_CACHE_TTL_SECONDS = 15 * 60  # 15 minutes

# VIX thresholds (Section 8)
_VIX_RISK_OFF = 35.0
_VIX_SHOCK = 45.0

# DIX thresholds
_DIX_BULLISH = 0.45
_DIX_BEARISH = 0.40

# Internals thresholds (Section 9)
_TICK_BULLISH = 800.0
_TICK_BEARISH = -800.0
_TRIN_BULLISH = 0.80
_TRIN_BEARISH = 1.20
_VOLD_BULLISH = 1_500_000_000   # +1.5 billion (Section 9 spec)
_VOLD_BEARISH = -1_500_000_000  # -1.5 billion (Section 9 spec)

# User-Agent for web requests
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ---------------------------------------------------------------------------
# Cache entry helper
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    """Simple TTL cache entry."""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0

    def is_valid(self) -> bool:
        return self.data and (time.monotonic() - self.timestamp) < _CACHE_TTL_SECONDS


# ---------------------------------------------------------------------------
# MarketStructure
# ---------------------------------------------------------------------------

class MarketStructure:
    """Fetches and interprets GEX/DIX, VIX term structure, and market internals.

    All public methods return dicts that are safe to merge directly into a
    ``MarketContext`` dataclass.  Every method is wrapped in error handling so
    that a transient network failure never crashes the calling pipeline.
    """

    def __init__(self, primary_mode: str = "") -> None:
        self._primary_mode = primary_mode.upper() if primary_mode else ""
        self._cache_gex_dix = _CacheEntry()
        self._cache_vix = _CacheEntry()
        self._cache_internals = _CacheEntry()
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)

    # ------------------------------------------------------------------
    # 1. GEX / DIX  (Section 8)
    # ------------------------------------------------------------------

    def fetch_gex_dix(self) -> dict[str, Any]:
        """Scrape SqueezeMetrics for the latest GEX and DIX readings.

        Returns:
            dict with keys:
                gex_value    (float)  — raw gamma exposure value
                gex_regime   (str)    — POSITIVE / NEGATIVE / FLIPPING
                dix_value    (float)  — dark pool indicator 0-1
                dix_signal   (str)    — accumulation / distribution / neutral
                fetched_at   (str)    — ISO timestamp
        """
        if self._cache_gex_dix.is_valid():
            return self._cache_gex_dix.data

        try:
            result = self._scrape_squeezemetrics()
            self._cache_gex_dix = _CacheEntry(data=result, timestamp=time.monotonic())
            return result
        except requests.Timeout:
            logger.warning("SqueezeMetrics timeout — using %s",
                           "stale cache" if self._cache_gex_dix.data else "default GEX/DIX")
        except requests.RequestException as e:
            logger.warning("SqueezeMetrics HTTP error: %s — using %s",
                           e, "stale cache" if self._cache_gex_dix.data else "default GEX/DIX")
        except ValueError as e:
            logger.warning("SqueezeMetrics parse error: %s — using %s",
                           e, "stale cache" if self._cache_gex_dix.data else "default GEX/DIX")
        except Exception as e:
            logger.warning("SqueezeMetrics fetch failed: %s — using %s",
                           e, "stale cache" if self._cache_gex_dix.data else "default GEX/DIX")

        if self._cache_gex_dix.data:
            return self._cache_gex_dix.data
        return self._default_gex_dix()

    def _scrape_squeezemetrics(self) -> dict[str, Any]:
        """Scrape the SqueezeMetrics DIX page for GEX and DIX values.

        The page renders a chart whose latest data-points are embedded in
        inline JavaScript / JSON.  We attempt several extraction strategies:
        1.  Parse ``<script>`` tags for the chart dataset arrays.
        2.  Look for a JSON payload (API response embedded in the page).
        3.  Fall back to page-text regex patterns (multiple patterns each).
        4.  CSS class / data-attribute extraction for modern SPA layouts.

        If all strategies fail, returns neutral defaults (GEX=0.0, DIX=0.45)
        instead of raising, so downstream consumers are never disrupted.
        """
        import json
        import re

        resp = self._session.get(_SQUEEZEMETRICS_URL, timeout=(5, 10))
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        gex_value: float | None = None
        dix_value: float | None = None

        # --- Strategy 1: Embedded JS data arrays ---
        for script in soup.find_all("script"):
            text = script.string or ""

            # Look for DIX data array — patterns like: dix = [... , 0.4532]
            dix_match = re.search(
                r'(?:dix|DIX)\s*[:=]\s*\[([^\]]+)\]', text
            )
            if dix_match:
                try:
                    vals = [float(v.strip()) for v in dix_match.group(1).split(",") if v.strip()]
                    if vals:
                        dix_value = vals[-1]
                except (ValueError, IndexError):
                    pass

            # Look for GEX data array — patterns like: gex = [... , 1234567]
            gex_match = re.search(
                r'(?:gex|GEX)\s*[:=]\s*\[([^\]]+)\]', text
            )
            if gex_match:
                try:
                    vals = [float(v.strip()) for v in gex_match.group(1).split(",") if v.strip()]
                    if vals:
                        gex_value = vals[-1]
                except (ValueError, IndexError):
                    pass

        # --- Strategy 2: JSON blob embedded in page ---
        if gex_value is None or dix_value is None:
            for script in soup.find_all("script"):
                text = script.string or ""
                # Try to find a JSON object with gex/dix keys
                json_match = re.search(r'\{[^{}]*"(?:gex|dix)"[^{}]*\}', text, re.IGNORECASE)
                if json_match:
                    try:
                        blob = json.loads(json_match.group(0))
                        if gex_value is None and "gex" in blob:
                            gex_value = float(blob["gex"])
                        if dix_value is None and "dix" in blob:
                            dix_value = float(blob["dix"])
                    except (json.JSONDecodeError, ValueError, TypeError):
                        pass

        # --- Strategy 3: Plain text on the page (stat cards / headings) ---
        if gex_value is None or dix_value is None:
            page_text = soup.get_text(" ", strip=True)
            if dix_value is None:
                # Try multiple regex patterns for different page formats
                for pattern in [
                    r'DIX\s*[:\s]+(\d+\.\d+)',
                    r'DIX[^0-9]*(0\.\d+)',
                    r'(?:dark\s+index|dark\s+pool)[^0-9]*(0\.\d+)',
                ]:
                    m = re.search(pattern, page_text, re.IGNORECASE)
                    if m:
                        dix_value = float(m.group(1))
                        break
            if gex_value is None:
                for pattern in [
                    r'GEX\s*[:\s]+([-+]?[\d,]+(?:\.\d+)?)',
                    r'GEX[^0-9]*([-+]?[\d,]+(?:\.\d+)?)',
                    r'(?:gamma\s+exp)[^0-9]*([-+]?[\d,]+(?:\.\d+)?)',
                ]:
                    m = re.search(pattern, page_text, re.IGNORECASE)
                    if m:
                        gex_value = float(m.group(1).replace(",", ""))
                        break

        # --- Strategy 4: CSS class / data-attribute extraction ---
        if gex_value is None or dix_value is None:
            # Look for elements with data attributes or class names containing dix/gex
            for el in soup.find_all(attrs={"data-value": True}):
                label = (el.get("data-label", "") or el.get_text()).lower()
                try:
                    val = float(el["data-value"])
                    if "dix" in label and dix_value is None:
                        dix_value = val
                    elif "gex" in label and gex_value is None:
                        gex_value = val
                except (ValueError, TypeError):
                    pass

            # Also check common chart container patterns
            for cls_name in ["dix-value", "gex-value", "stat-value", "metric-value"]:
                for el in soup.find_all(class_=re.compile(cls_name, re.IGNORECASE)):
                    text = el.get_text(strip=True)
                    try:
                        val = float(text.replace(",", ""))
                        if "dix" in cls_name and dix_value is None:
                            dix_value = val
                        elif "gex" in cls_name and gex_value is None:
                            gex_value = val
                    except (ValueError, TypeError):
                        pass

        # If we still have nothing, log warning and return defaults (not raise)
        if gex_value is None and dix_value is None:
            logger.warning(
                "Could not extract GEX or DIX values from SqueezeMetrics page "
                "— all %d strategies failed. Returning neutral defaults.",
                4,
            )
            return self._default_gex_dix()

        gex_value = gex_value if gex_value is not None else 0.0
        dix_value = dix_value if dix_value is not None else 0.45  # neutral default

        return self._interpret_gex_dix(gex_value, dix_value)

    def _interpret_gex_dix(self, gex: float, dix: float) -> dict[str, Any]:
        """Classify raw GEX/DIX into regime and signal strings."""
        # GEX regime
        if abs(gex) < 50_000_000:
            # Near zero — transitioning
            gex_regime = "FLIPPING"
        elif gex > 0:
            gex_regime = "POSITIVE"
        else:
            gex_regime = "NEGATIVE"

        # DIX signal
        if dix >= _DIX_BULLISH:
            dix_signal = "accumulation"
        elif dix <= _DIX_BEARISH:
            dix_signal = "distribution"
        else:
            dix_signal = "neutral"

        return {
            "gex_value": gex,
            "gex_regime": gex_regime,
            "dix_value": dix,
            "dix_signal": dix_signal,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _default_gex_dix() -> dict[str, Any]:
        """Neutral fallback when no data is available.

        Defaults: GEX=0.0 (neutral, flipping zone), DIX=0.45 (neutral/
        slightly-bullish — the long-run average hovers around 0.43-0.46).
        Using 0.0 for DIX would incorrectly signal extreme distribution.
        """
        return {
            "gex_value": 0.0,
            "gex_regime": "FLIPPING",
            "dix_value": 0.45,
            "dix_signal": "neutral",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": "default",
        }

    # ------------------------------------------------------------------
    # 1b. DIX/GEX Regime Classification  (Research-backed)
    # ------------------------------------------------------------------

    @staticmethod
    def classify_dix_gex_regime(dix: float, gex: float) -> str:
        """Classify the combined DIX/GEX regime into an actionable state.

        Research basis: Dark pool buying (high DIX) combined with negative
        gamma exposure creates explosive upside setups. Conversely, low DIX
        with positive GEX = suppressed/pinned markets.

        Regime states:
            SETUP_BULLISH       — High DIX (>0.45) + Negative GEX = explosive upside
            SETUP_BEARISH       — Low DIX (<0.40) + Positive GEX = suppressed downside
            MOMENTUM_AMPLIFIED  — Negative GEX + any DIX = trending/crashing amplified
            MEAN_REVERSION      — Positive GEX + neutral DIX = range-bound, mean rev
            NEUTRAL             — everything else

        Args:
            dix: Dark pool index value (0-1 range, typically 0.35-0.50).
            gex: Gamma exposure value (positive = dealer long gamma,
                 negative = dealer short gamma).

        Returns:
            One of the 5 regime strings.
        """
        is_high_dix = dix > _DIX_BULLISH        # > 0.45
        is_low_dix = dix < _DIX_BEARISH          # < 0.40
        is_negative_gex = gex < 0
        is_positive_gex = gex > 0

        # Priority 1: High DIX + Negative GEX = explosive bullish setup
        if is_high_dix and is_negative_gex:
            return "SETUP_BULLISH"

        # Priority 2: Low DIX + Positive GEX = suppressed bearish setup
        if is_low_dix and is_positive_gex:
            return "SETUP_BEARISH"

        # Priority 3: Negative GEX with any DIX = momentum amplified
        if is_negative_gex:
            return "MOMENTUM_AMPLIFIED"

        # Priority 4: Positive GEX + neutral DIX = mean reversion
        if is_positive_gex and not is_high_dix and not is_low_dix:
            return "MEAN_REVERSION"

        # Everything else
        return "NEUTRAL"

    @staticmethod
    def compute_dix_trend(dix_history: list[float]) -> str:
        """Compute DIX trend using 3-day vs 10-day rolling averages.

        When the 3-day moving average crosses above the 10-day by more
        than 0.02, dark-pool participants are net accumulating.  The
        reverse indicates distribution.

        Args:
            dix_history: List of historical DIX values, most recent last.
                         Needs at least 10 values for a meaningful signal.

        Returns:
            "ACCUMULATING" if 3d > 10d by > 0.02
            "DISTRIBUTING" if 3d < 10d by > 0.02
            "NEUTRAL" otherwise (or insufficient data)
        """
        if len(dix_history) < 10:
            logger.debug(
                "DIX history too short (%d values) for trend — returning NEUTRAL",
                len(dix_history),
            )
            return "NEUTRAL"

        # 3-day rolling average (most recent 3 values)
        avg_3d = sum(dix_history[-3:]) / 3.0

        # 10-day rolling average (most recent 10 values)
        avg_10d = sum(dix_history[-10:]) / 10.0

        diff = avg_3d - avg_10d

        if diff > 0.02:
            logger.info(
                "DIX trend ACCUMULATING: 3d=%.4f 10d=%.4f diff=+%.4f",
                avg_3d, avg_10d, diff,
            )
            return "ACCUMULATING"
        elif diff < -0.02:
            logger.info(
                "DIX trend DISTRIBUTING: 3d=%.4f 10d=%.4f diff=%.4f",
                avg_3d, avg_10d, diff,
            )
            return "DISTRIBUTING"
        else:
            logger.debug(
                "DIX trend NEUTRAL: 3d=%.4f 10d=%.4f diff=%.4f",
                avg_3d, avg_10d, diff,
            )
            return "NEUTRAL"

    # ------------------------------------------------------------------
    # 2. VIX / VIX3M  (Section 8)
    # ------------------------------------------------------------------

    def fetch_vix_data(self) -> dict[str, Any]:
        """Fetch VIX and VIX3M, compute term structure and risk level.

        Attempts CBOE direct data first, falls back to yfinance.

        Returns:
            dict with keys:
                vix              (float)
                vix3m            (float)
                term_structure   (str)  — contango / backwardation
                risk_level       (str)  — NORMAL / ELEVATED / RISK_OFF / SHOCK
                fetched_at       (str)
        """
        if self._cache_vix.is_valid():
            return self._cache_vix.data

        try:
            result = self._fetch_vix_yfinance()
            self._cache_vix = _CacheEntry(data=result, timestamp=time.monotonic())
            return result
        except Exception:
            logger.exception("Failed to fetch VIX data")
            if self._cache_vix.data:
                logger.warning("Returning stale VIX cache")
                return self._cache_vix.data
            return self._default_vix()

    def _fetch_vix_yfinance(self) -> dict[str, Any]:
        """Use yfinance to pull the most recent VIX and VIX3M close."""
        import yfinance as yf

        vix_ticker = yf.Ticker("^VIX")
        vix3m_ticker = yf.Ticker("^VIX3M")

        vix_hist = vix_ticker.history(period="5d")
        vix3m_hist = vix3m_ticker.history(period="5d")

        if vix_hist.empty:
            raise ValueError("yfinance returned empty VIX data")

        vix_val = float(vix_hist["Close"].iloc[-1])
        vix3m_val = float(vix3m_hist["Close"].iloc[-1]) if not vix3m_hist.empty else vix_val * 1.05

        return self._interpret_vix(vix_val, vix3m_val)

    def _interpret_vix(self, vix: float, vix3m: float) -> dict[str, Any]:
        """Classify VIX readings into term structure and risk level."""
        # Term structure
        if vix > vix3m:
            term_structure = "backwardation"
        else:
            term_structure = "contango"

        # Risk level
        if vix >= _VIX_SHOCK:
            risk_level = "SHOCK"
        elif vix >= _VIX_RISK_OFF:
            risk_level = "RISK_OFF"
        elif vix >= 25:
            risk_level = "ELEVATED"
        else:
            risk_level = "NORMAL"

        return {
            "vix": round(vix, 2),
            "vix3m": round(vix3m, 2),
            "term_structure": term_structure,
            "risk_level": risk_level,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _default_vix() -> dict[str, Any]:
        # A-05: Fail-CLOSED — unknown VIX = SHOCK regime (VIX=99).
        # Previous Sprint 0.5 used 35.0 (RISK_OFF) to avoid self-DOS, but AEGIS
        # spec mandates fail-closed: if we cannot determine market volatility,
        # assume the worst. With A-02 fixing RISK_OFF to 0.0 for LONG, the
        # self-DOS concern is less relevant — SHOCK correctly blocks all trading
        # until VIX data is restored. This is the safe default.
        logger.warning(
            "A-05: VIX fetch failed — defaulting to VIX=99 (SHOCK regime). "
            "All trading blocked until VIX data is restored."
        )
        return {
            "vix": 99.0,
            "vix3m": 99.0,
            "term_structure": "backwardation",
            "risk_level": "SHOCK",
            "source": "default_failsafe",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # 3. Market Internals  (Section 9)
    # ------------------------------------------------------------------

    def fetch_internals(self) -> dict[str, Any]:
        """Fetch NYSE market internals: TICK, TRIN, ADD, VOLD.

        Uses yfinance ticker proxies for real-time-ish intraday data.
        Skipped entirely in UK_ISA mode (^TICK, ^TRIN, ^ADD, ^VOLD are
        US-only NYSE indicators that always fail for LSE tickers).

        Returns:
            dict with keys:
                tick              (float)
                trin              (float)
                add               (float)
                vold              (float)
                composite_score   (int)   — 0-4; each bullish reading = 1 point
                composite_signal  (str)   — strong_bullish / bullish / neutral /
                                            bearish / strong_bearish
                confidence_adj    (int)   — +5 if score 3-4, -5 if score 0-1, else 0
                fetched_at        (str)
        """
        # UK_ISA mode: skip US-only NYSE internals entirely — use neutral defaults
        if self._primary_mode == "UK_ISA":
            logger.debug("UK_ISA mode — skipping US market internals (^TICK/^TRIN/^ADD/^VOLD)")
            return self._default_internals()

        if self._cache_internals.is_valid():
            return self._cache_internals.data

        try:
            result = self._fetch_internals_yfinance()
            self._cache_internals = _CacheEntry(data=result, timestamp=time.monotonic())
            return result
        except Exception:
            logger.exception("Failed to fetch market internals")
            if self._cache_internals.data:
                logger.warning("Returning stale internals cache")
                return self._cache_internals.data
            return self._default_internals()

    def _fetch_internals_yfinance(self) -> dict[str, Any]:
        """Pull TICK, TRIN, ADD, VOLD from yfinance ticker symbols.

        yfinance symbol mapping (Yahoo Finance indices):
            ^TICK  — NYSE TICK index
            ^TRIN  — NYSE Arms (TRIN) index
            ^ADD   — NYSE Advance-Decline issues
            ^VOLD  — NYSE Up/Down volume

        These are intraday snapshots; we grab the latest available bar.
        If any individual symbol fails we substitute a neutral value so
        the composite score can still be computed from the others.
        """
        import yfinance as yf

        symbols = {
            "tick": "^TICK",
            "trin": "^TRIN",
            "add": "^ADD",
            "vold": "^VOLD",
        }

        values: dict[str, float] = {}

        for key, sym in symbols.items():
            try:
                ticker = yf.Ticker(sym)
                hist = ticker.history(period="1d", interval="1m")
                if hist.empty:
                    # Fallback: try 5-day daily bars
                    hist = ticker.history(period="5d")
                if not hist.empty:
                    values[key] = float(hist["Close"].iloc[-1])
                else:
                    logger.warning("No data for %s (%s) — using neutral default", key, sym)
                    values[key] = self._neutral_value(key)
            except Exception:
                logger.warning("Error fetching %s (%s) — using neutral default", key, sym, exc_info=True)
                values[key] = self._neutral_value(key)

        return self._interpret_internals(
            tick=values["tick"],
            trin=values["trin"],
            add_val=values["add"],
            vold=values["vold"],
        )

    @staticmethod
    def _neutral_value(key: str) -> float:
        """Return a neutral (neither bullish nor bearish) default for an internal."""
        defaults = {
            "tick": 0.0,
            "trin": 1.0,
            "add": 0.0,
            "vold": 0.0,
        }
        return defaults.get(key, 0.0)

    def _interpret_internals(
        self,
        tick: float,
        trin: float,
        add_val: float,
        vold: float,
    ) -> dict[str, Any]:
        """Score each internal 0 or 1 (bullish) and build composite.

        Section 9 composite scoring:
            Each bullish reading contributes 1 point (max 4).
            3-4 = strong internal support  -> +5 confidence adjustment
            2   = neutral                  ->  0
            0-1 = weak internals           -> -5 confidence adjustment
        """
        score = 0

        # TICK: bullish if > +800
        tick_bullish = tick > _TICK_BULLISH
        if tick_bullish:
            score += 1

        # TRIN: bullish if < 0.80  (low TRIN = buying pressure)
        trin_bullish = trin < _TRIN_BULLISH
        if trin_bullish:
            score += 1

        # ADD: bullish if positive (more advancing than declining issues)
        add_bullish = add_val > 0
        if add_bullish:
            score += 1

        # VOLD: bullish if > +1.5B
        vold_bullish = vold > _VOLD_BULLISH
        if vold_bullish:
            score += 1

        # Composite signal label
        if score >= 4:
            composite_signal = "strong_bullish"
        elif score == 3:
            composite_signal = "bullish"
        elif score == 2:
            composite_signal = "neutral"
        elif score == 1:
            composite_signal = "bearish"
        else:
            composite_signal = "strong_bearish"

        # Confidence adjustment for downstream scoring (Section 9)
        if score >= 3:
            confidence_adj = 5
        elif score <= 1:
            confidence_adj = -5
        else:
            confidence_adj = 0

        return {
            "tick": round(tick, 2),
            "trin": round(trin, 4),
            "add": round(add_val, 2),
            "vold": round(vold, 2),
            "composite_score": score,
            "composite_signal": composite_signal,
            "confidence_adj": confidence_adj,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _default_internals() -> dict[str, Any]:
        return {
            "tick": 0.0,
            "trin": 1.0,
            "add": 0.0,
            "vold": 0.0,
            "composite_score": 2,
            "composite_signal": "neutral",
            "confidence_adj": 0,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # 4. Combined context
    # ------------------------------------------------------------------

    def get_full_context(self, dix_history: list[float] | None = None) -> dict[str, Any]:
        """Fetch all three data sources and merge into a single dict.

        This is the primary entry point used by the regime classifier and
        the 5-layer confidence engine.

        Args:
            dix_history: Optional list of historical DIX values (most recent
                         last) for computing the DIX accumulation/distribution
                         trend.  If None, dix_trend defaults to "NEUTRAL".

        Returns:
            dict with all keys from fetch_gex_dix(), fetch_vix_data(),
            and fetch_internals(), plus dix_gex_regime, dix_trend,
            and a top-level ``timestamp``.
        """
        gex_dix = self.fetch_gex_dix()
        vix = self.fetch_vix_data()
        internals = self.fetch_internals()

        combined: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Namespace sub-dicts to avoid key collisions, but also
        # promote the most-used keys to the top level for convenience.
        combined["gex_dix"] = gex_dix
        combined["vix_data"] = vix
        combined["internals"] = internals

        # Top-level convenience keys (match MarketContext fields)
        combined["gex_value"] = gex_dix.get("gex_value", 0.0)
        combined["gex_regime"] = gex_dix.get("gex_regime", "POSITIVE")
        combined["dix_value"] = gex_dix.get("dix_value", 0.0)
        combined["dix_signal"] = gex_dix.get("dix_signal", "neutral")
        combined["vix"] = vix.get("vix", 0.0)
        combined["vix3m"] = vix.get("vix3m", 0.0)
        combined["vix_term_structure"] = vix.get("term_structure", "contango")
        combined["risk_level"] = vix.get("risk_level", "NORMAL")
        combined["tick"] = internals.get("tick", 0.0)
        combined["trin"] = internals.get("trin", 1.0)
        combined["add"] = internals.get("add", 0.0)
        combined["vold"] = internals.get("vold", 0.0)
        combined["internals_composite"] = internals.get("composite_score", 2)
        combined["internals_confidence_adj"] = internals.get("confidence_adj", 0)

        # DIX/GEX regime classification (research-backed)
        dix_val = combined["dix_value"]
        gex_val = combined["gex_value"]
        combined["dix_gex_regime"] = self.classify_dix_gex_regime(dix_val, gex_val)

        # DIX trend (3d vs 10d rolling average)
        if dix_history is not None:
            combined["dix_trend"] = self.compute_dix_trend(dix_history)
        else:
            combined["dix_trend"] = "NEUTRAL"

        logger.info(
            "Market structure context: GEX=%s DIX=%.4f VIX=%.2f term=%s "
            "internals=%d/4 dix_gex_regime=%s dix_trend=%s",
            combined["gex_regime"],
            combined["dix_value"],
            combined["vix"],
            combined["vix_term_structure"],
            combined["internals_composite"],
            combined["dix_gex_regime"],
            combined["dix_trend"],
        )

        return combined

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def invalidate_cache(self) -> None:
        """Force all caches to expire. Useful for testing or manual refresh."""
        self._cache_gex_dix = _CacheEntry()
        self._cache_vix = _CacheEntry()
        self._cache_internals = _CacheEntry()
        logger.info("Market structure cache invalidated")

    def cache_status(self) -> dict[str, bool]:
        """Return whether each cache is currently valid."""
        return {
            "gex_dix": self._cache_gex_dix.is_valid(),
            "vix": self._cache_vix.is_valid(),
            "internals": self._cache_internals.is_valid(),
        }
