"""
Cross-Asset Macro Signal — NZT-48 Academic Signal Module
Erb & Harvey (2006): tactical value of macro/cross-asset signals.
Fong & Law (2018): VIX term structure as risk-on/off indicator.

Three signals:
1. VIX Term Structure: spot/3M ratio > 1.1 (backwardation) = risk-off -5 conf
2. DXY Strength: 5-day return ±1.5% affects NASDAQ-correlated tickers ±5 conf
3. Credit Spread Proxy: LQD/IEF ratio widening > 1% in 3 days = risk-off -8 conf
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# VIX term structure thresholds
VIX_BACKWARDATION_THRESHOLD = 1.10   # VIX spot / VIX 3M > 1.10 = risk-off
VIX_CONTANGO_THRESHOLD = 0.85        # VIX spot / VIX 3M < 0.85 = risk-on
VIX_CONF_BOOST = 5                   # Steep contango → risk-on boost
VIX_CONF_PENALTY = -5                # Backwardation → risk-off penalty

# DXY thresholds
DXY_STRONG_THRESHOLD = 1.5           # % 5-day DXY return = tech headwind
DXY_WEAK_THRESHOLD = -1.5            # % 5-day DXY return = tech tailwind
DXY_CONF_ADJ = 5                     # ±5 confidence

# Credit spread (LQD/IEF ratio)
CREDIT_SPREAD_WARN_PCT = 1.0         # 1% ratio decline in 3 days = warning
CREDIT_SPREAD_CONF_PENALTY = -8      # Risk-off signal

# Tickers affected by DXY (NASDAQ / tech heavy)
_DXY_SENSITIVE_TICKERS = {
    "QQQ3.L", "3LUS.L", "QQQ5.L", "SP5L.L",
    "GPT3.L", "NVD3.L", "TSL3.L", "TSM3.L", "3SEM.L",
}

# Cache durations — F-07: VIX needs 5-min freshness during crashes;
# DXY, credit, Fear&Greed are slow-moving → 30 min is fine.
_VIX_CACHE_SECONDS = 300    # 5 minutes  (was 1800 — F-07 fix)
_SLOW_CACHE_SECONDS = 1800  # 30 minutes (DXY, credit, Fear&Greed)

# C-06: HMM refit schedule — weekly instead of every 30 min
# Problem: HMM refitting on 63 data points every 30 min causes overfitting
# to intraday noise (Hamilton 1989 regime model assumes daily observations).
# Fix: refit model weekly; re-predict with cached model every 30 min.
_HMM_REFIT_SECONDS = 604800       # 7 days — weekly refit (was 1800)
_HMM_PREDICT_CACHE_SECONDS = 1800 # 30 minutes — re-predict with cached model

# C-06: Regime confirmation buffer — 3-tick confirmation before transition
# Problem: HMM regime applied immediately with no confirmation, causing
# whipsaw in strategy decisions on noisy regime oscillations.
# Fix: require 3 consecutive same-regime readings before accepting transition.
_HMM_CONFIRMATION_TICKS = 3


class CrossAssetMacro:
    """
    Monitors VIX term structure, DXY strength, and credit spreads
    to produce macro regime adjustments for all signals.

    Erb & Harvey (2006): cross-asset macro signals have predictive power
    over individual equity returns at 5-day forward horizons.
    """

    def __init__(self):
        # Per-signal caches with individual timestamps (F-07)
        self._vix_cache: Optional[dict] = None
        self._vix_cache_ts: Optional[datetime] = None
        self._dxy_cache: Optional[dict] = None
        self._dxy_cache_ts: Optional[datetime] = None
        self._credit_cache: Optional[dict] = None
        self._credit_cache_ts: Optional[datetime] = None
        self._fg_cache: Optional[dict] = None
        self._fg_cache_ts: Optional[datetime] = None
        self._hmm_cache: Optional[dict] = None
        self._hmm_cache_ts: Optional[datetime] = None

        # C-06: HMM model cache (refit weekly, predict every 30 min)
        self._hmm_model = None              # Cached fitted GaussianHMM model
        self._hmm_model_ts: Optional[datetime] = None  # When model was last fitted
        self._hmm_choppy_state: Optional[int] = None    # Which state index is choppy

        # C-06: Regime confirmation buffer (3-tick before transition)
        self._hmm_confirmed_regime: Optional[str] = None   # Last confirmed regime
        self._hmm_proposed_regime: Optional[str] = None     # Currently proposed regime
        self._hmm_proposal_count: int = 0                   # Consecutive proposal count

    def _is_signal_fresh(self, cache_ts: Optional[datetime], ttl: int) -> bool:
        """Check if a per-signal cache entry is still fresh."""
        if not cache_ts:
            return False
        age = (datetime.now(timezone.utc) - cache_ts).total_seconds()
        return age < ttl

    def update(self) -> dict:
        """
        Fetches all macro signals, each with its own cache TTL.
        VIX refreshes every 5 min; others every 30 min.
        Returns full macro state dict.
        """
        now = datetime.now(timezone.utc)

        # VIX — 5-minute TTL (F-07: critical during crashes)
        if not self._is_signal_fresh(self._vix_cache_ts, _VIX_CACHE_SECONDS) or not self._vix_cache:
            self._vix_cache = self._get_vix_signal()
            self._vix_cache_ts = now

        # DXY — 30-minute TTL (slow-moving)
        if not self._is_signal_fresh(self._dxy_cache_ts, _SLOW_CACHE_SECONDS) or not self._dxy_cache:
            self._dxy_cache = self._get_dxy_signal()
            self._dxy_cache_ts = now

        # Credit — 30-minute TTL (slow-moving)
        if not self._is_signal_fresh(self._credit_cache_ts, _SLOW_CACHE_SECONDS) or not self._credit_cache:
            self._credit_cache = self._get_credit_signal()
            self._credit_cache_ts = now

        # Fear & Greed — 30-minute TTL (slow-moving)
        if not self._is_signal_fresh(self._fg_cache_ts, _SLOW_CACHE_SECONDS) or not self._fg_cache:
            self._fg_cache = self._get_fear_greed_signal()
            self._fg_cache_ts = now

        # C-06: HMM regime — weekly refit, 30-min prediction refresh
        # Model is refitted weekly to avoid overfitting on 63 data points.
        # Predictions use cached model and refresh every 30 min.
        if not self._is_signal_fresh(self._hmm_cache_ts, _HMM_PREDICT_CACHE_SECONDS) or not self._hmm_cache:
            self._hmm_cache = self._get_hmm_signal()
            self._hmm_cache_ts = now

        return {
            "vix_signal": self._vix_cache,
            "dxy_signal": self._dxy_cache,
            "credit_signal": self._credit_cache,
            "fear_greed_signal": self._fg_cache,
            "hmm_signal": self._hmm_cache,
            "ts": now.isoformat(),
        }

    # ─────────────────────────────────────────────────────────
    # VIX term structure
    # ─────────────────────────────────────────────────────────

    # C-23 fix: cached last-known-good VIX for fallback
    _last_good_vix_spot: float = 20.0
    _last_good_vix_3m: float = 22.0

    def _get_vix_signal(self) -> dict:
        """
        VIX spot / VIX 3M futures ratio.
        Backwardation (>1.1) = risk-off; Contango (<0.85) = risk-on.
        Falls back to cached last-known-good VIX (default 20.0) if fetch fails.
        """
        try:
            import yfinance as yf
            vix_spot = yf.Ticker("^VIX").fast_info.get("lastPrice", None)
            vix_3m = yf.Ticker("^VIX3M").fast_info.get("lastPrice", None)

            if not vix_spot or not vix_3m or vix_3m <= 0:
                # C-23: Use cached last-known-good instead of returning NO_DATA
                vix_spot = vix_spot or self._last_good_vix_spot
                vix_3m = vix_3m or self._last_good_vix_3m
                if not vix_3m or vix_3m <= 0:
                    return {"status": "FALLBACK", "ratio": None, "confidence_adjustment": 0,
                            "note": "VIX unavailable — using default 20.0"}
            else:
                # Cache successful values
                self._last_good_vix_spot = vix_spot
                self._last_good_vix_3m = vix_3m

            ratio = vix_spot / vix_3m

            if ratio > VIX_BACKWARDATION_THRESHOLD:
                return {
                    "status": "BACKWARDATION",
                    "ratio": round(ratio, 3),
                    "vix_spot": vix_spot,
                    "vix_3m": vix_3m,
                    "confidence_adjustment": VIX_CONF_PENALTY,
                    "note": f"VIX inverted ({vix_spot:.1f}/{vix_3m:.1f}={ratio:.2f}) — risk-off",
                }
            elif ratio < VIX_CONTANGO_THRESHOLD:
                return {
                    "status": "STEEP_CONTANGO",
                    "ratio": round(ratio, 3),
                    "vix_spot": vix_spot,
                    "vix_3m": vix_3m,
                    "confidence_adjustment": VIX_CONF_BOOST,
                    "note": f"VIX steep contango ({ratio:.2f}) — risk-on",
                }
            else:
                return {
                    "status": "NORMAL",
                    "ratio": round(ratio, 3),
                    "confidence_adjustment": 0,
                }
        except Exception as e:
            logger.debug("VIX term structure fetch failed: %s", e)
            return {"status": "ERROR", "ratio": None, "confidence_adjustment": 0}

    # ─────────────────────────────────────────────────────────
    # DXY strength
    # ─────────────────────────────────────────────────────────

    def _get_dxy_signal(self) -> dict:
        """
        Dollar index 5-day return.
        Strong USD → tech headwind; Weak USD → tech tailwind.
        """
        try:
            import yfinance as yf
            df = yf.Ticker("DX-Y.NYB").history(period="10d", interval="1d")
            if df is None or len(df) < 5:
                return {"status": "NO_DATA", "return_5d": None, "confidence_adjustment": 0}

            close_now = float(df["Close"].iloc[-1])
            close_5d = float(df["Close"].iloc[-6] if len(df) >= 6 else df["Close"].iloc[0])
            ret_5d = ((close_now - close_5d) / close_5d) * 100

            if ret_5d >= DXY_STRONG_THRESHOLD:
                return {
                    "status": "STRONG_USD",
                    "return_5d": round(ret_5d, 2),
                    "confidence_adjustment": -DXY_CONF_ADJ,
                    "note": f"DXY +{ret_5d:.1f}% (5d) — tech headwind",
                    "affected_tickers": list(_DXY_SENSITIVE_TICKERS),
                }
            elif ret_5d <= DXY_WEAK_THRESHOLD:
                return {
                    "status": "WEAK_USD",
                    "return_5d": round(ret_5d, 2),
                    "confidence_adjustment": DXY_CONF_ADJ,
                    "note": f"DXY {ret_5d:.1f}% (5d) — tech tailwind",
                    "affected_tickers": list(_DXY_SENSITIVE_TICKERS),
                }
            else:
                return {
                    "status": "NEUTRAL",
                    "return_5d": round(ret_5d, 2),
                    "confidence_adjustment": 0,
                }
        except Exception as e:
            logger.debug("DXY signal fetch failed: %s", e)
            return {"status": "ERROR", "return_5d": None, "confidence_adjustment": 0}

    # ─────────────────────────────────────────────────────────
    # Credit spread proxy
    # ─────────────────────────────────────────────────────────

    def _get_credit_signal(self) -> dict:
        """
        LQD/IEF ratio as credit spread proxy.
        3-day decline > 1% = credit stress (risk-off signal).
        """
        try:
            import yfinance as yf
            import pandas as pd

            lqd_df = yf.Ticker("LQD").history(period="10d", interval="1d")
            ief_df = yf.Ticker("IEF").history(period="10d", interval="1d")

            if lqd_df is None or ief_df is None or len(lqd_df) < 4 or len(ief_df) < 4:
                return {"status": "NO_DATA", "ratio_change_3d": None, "confidence_adjustment": 0}

            # Align by date
            lqd_close = lqd_df["Close"].iloc[-1]
            lqd_close_3d = lqd_df["Close"].iloc[-4] if len(lqd_df) >= 4 else lqd_df["Close"].iloc[0]
            ief_close = ief_df["Close"].iloc[-1]
            ief_close_3d = ief_df["Close"].iloc[-4] if len(ief_df) >= 4 else ief_df["Close"].iloc[0]

            ratio_now = lqd_close / ief_close if ief_close > 0 else 0
            ratio_3d = lqd_close_3d / ief_close_3d if ief_close_3d > 0 else 0

            if ratio_3d <= 0:
                return {"status": "NO_DATA", "confidence_adjustment": 0}

            ratio_change_pct = ((ratio_now - ratio_3d) / ratio_3d) * 100

            if ratio_change_pct <= -CREDIT_SPREAD_WARN_PCT:
                return {
                    "status": "CREDIT_STRESS",
                    "ratio_change_3d": round(ratio_change_pct, 2),
                    "confidence_adjustment": CREDIT_SPREAD_CONF_PENALTY,
                    "note": f"LQD/IEF ratio {ratio_change_pct:.1f}% (3d) — credit widening",
                }
            else:
                return {
                    "status": "NORMAL",
                    "ratio_change_3d": round(ratio_change_pct, 2),
                    "confidence_adjustment": 0,
                }
        except Exception as e:
            logger.debug("Credit spread signal fetch failed: %s", e)
            return {"status": "ERROR", "ratio_change_3d": None, "confidence_adjustment": 0}

    # ─────────────────────────────────────────────────────────
    # Alternative.me Fear & Greed — Mandate 4c
    # ─────────────────────────────────────────────────────────

    def _get_fear_greed_signal(self) -> dict:
        """CNN Fear & Greed (equities) as supplementary risk-off signal.
        <25 = Extreme Fear → veto all longs. No API key needed.
        Falls back to conservative default of 50 (neutral) if CNN API unavailable.
        NEVER blocks trading if API unavailable — returns UNKNOWN."""
        try:
            import urllib.request
            import json as _json
            req = urllib.request.Request(
                "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                headers={"User-Agent": "NZT48/1.0"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                data = _json.loads(r.read())
                value = int(data["fear_and_greed"]["score"])
            if value < 25:
                return {
                    "status": "EXTREME_FEAR",
                    "value": value,
                    "confidence_adjustment": -10,
                    "note": f"Fear & Greed = {value} — extreme fear, veto longs",
                }
            elif value > 75:
                return {
                    "status": "EXTREME_GREED",
                    "value": value,
                    "confidence_adjustment": 3,
                    "note": f"Fear & Greed = {value} — extreme greed",
                }
            else:
                return {"status": "NEUTRAL", "value": value, "confidence_adjustment": 0}
        except Exception as e:
            logger.debug("CNN Fear & Greed API failed (non-blocking), defaulting to 50: %s", e)
            return {"status": "UNKNOWN", "value": 50, "confidence_adjustment": 0,
                    "note": "CNN F&G unavailable — conservative default 50 (neutral)"}

    # ─────────────────────────────────────────────────────────
    # HMM Regime Classifier — Mandate 4b (Hamilton 1989)
    # ─────────────────────────────────────────────────────────

    def _get_hmm_signal(self) -> dict:
        """2-state Gaussian HMM regime classifier with C-06 upgrades.

        Hamilton (1989) — Econometrica.
        State 0: Trending/Low-Vol (S15 permitted).
        State 1: Choppy/High-Vol (S15 halted, consider fallback).

        C-06 upgrades:
          1. Model refit is weekly (not every 30 min) to prevent overfitting
             on 63 data points. Predictions use the cached model every 30 min.
          2. 3-tick confirmation buffer: regime transition only accepted after
             3 consecutive same-regime readings to prevent whipsaw.

        Returns P(choppy). If unavailable, returns 0.5 (never blocks).
        """
        try:
            import numpy as np
            from hmmlearn.hmm import GaussianHMM
            import yfinance as yf

            # Get 90 days of QQQ data (needed for both fit and predict)
            qqq = yf.Ticker("QQQ")
            hist = qqq.history(period="90d", interval="1d")
            if hist is None or len(hist) < 30:
                return {"status": "NO_DATA", "choppy_prob": 0.5, "is_choppy": False}

            returns = hist["Close"].pct_change().dropna().values.reshape(-1, 1)
            if len(returns) < 30:
                return {"status": "NO_DATA", "choppy_prob": 0.5, "is_choppy": False}

            # C-06: Refit model only weekly (not every 30 min)
            needs_refit = (
                self._hmm_model is None
                or not self._is_signal_fresh(self._hmm_model_ts, _HMM_REFIT_SECONDS)
            )

            if needs_refit:
                model = GaussianHMM(
                    n_components=2, covariance_type="full",
                    n_iter=100, random_state=42, verbose=False,
                )
                model.fit(returns)

                # Identify which state is choppy (higher variance)
                state_vars = [float(model.covars_[i][0][0]) for i in range(2)]
                choppy_state = int(np.argmax(state_vars))

                # Cache the fitted model
                self._hmm_model = model
                self._hmm_model_ts = datetime.now(timezone.utc)
                self._hmm_choppy_state = choppy_state

                logger.info(
                    "C-06 HMM REFIT: model retrained on %d observations | "
                    "state_vars=%s | choppy_state=%d",
                    len(returns), [round(v, 8) for v in state_vars], choppy_state,
                )
            else:
                model = self._hmm_model
                choppy_state = self._hmm_choppy_state

            # Predict current state probabilities using cached model
            state_probs = model.predict_proba(returns)
            current_choppy_prob = float(state_probs[-1][choppy_state])
            raw_is_choppy = current_choppy_prob > 0.60
            raw_status = "CHOPPY" if raw_is_choppy else "TRENDING"

            # C-06: 3-tick confirmation buffer before regime transition
            # Only accept a regime change after 3 consecutive same-regime readings.
            if raw_status == self._hmm_proposed_regime:
                self._hmm_proposal_count += 1
            else:
                # New proposed regime — reset counter
                self._hmm_proposed_regime = raw_status
                self._hmm_proposal_count = 1

            if self._hmm_proposal_count >= _HMM_CONFIRMATION_TICKS:
                # Confirmed — accept the transition
                if self._hmm_confirmed_regime != raw_status:
                    logger.info(
                        "C-06 HMM REGIME CONFIRMED: %s → %s after %d ticks | "
                        "P(choppy)=%.3f",
                        self._hmm_confirmed_regime or "INIT", raw_status,
                        _HMM_CONFIRMATION_TICKS, current_choppy_prob,
                    )
                self._hmm_confirmed_regime = raw_status
            else:
                # Not yet confirmed — keep previous regime
                logger.debug(
                    "C-06 HMM BUFFERING: proposed=%s tick=%d/%d | "
                    "confirmed=%s | P(choppy)=%.3f",
                    raw_status, self._hmm_proposal_count, _HMM_CONFIRMATION_TICKS,
                    self._hmm_confirmed_regime or "INIT", current_choppy_prob,
                )

            # Use confirmed regime (or raw if no confirmation yet — first run)
            effective_status = self._hmm_confirmed_regime or raw_status
            is_choppy = effective_status == "CHOPPY"

            state_vars = [float(model.covars_[i][0][0]) for i in range(2)]

            return {
                "status": effective_status,
                "choppy_prob": round(current_choppy_prob, 3),
                "is_choppy": is_choppy,
                "state_means": [float(model.means_[i][0]) for i in range(2)],
                "state_vars": state_vars,
                "confidence_adjustment": -8 if is_choppy else 0,
                "note": (
                    f"HMM P(choppy)={current_choppy_prob:.2f}"
                    + (" — risk-off" if is_choppy else "")
                    + (f" [buffering {self._hmm_proposal_count}/{_HMM_CONFIRMATION_TICKS}]"
                       if self._hmm_proposal_count < _HMM_CONFIRMATION_TICKS else "")
                ),
                "confirmed": self._hmm_proposal_count >= _HMM_CONFIRMATION_TICKS,
                "raw_status": raw_status,
            }
        except ImportError:
            logger.debug("hmmlearn not installed — HMM signal unavailable")
            return {"status": "UNAVAILABLE", "choppy_prob": 0.5, "is_choppy": False, "confidence_adjustment": 0}
        except Exception as e:
            logger.debug("HMM regime classifier failed (non-blocking): %s", e)
            return {"status": "ERROR", "choppy_prob": 0.5, "is_choppy": False, "confidence_adjustment": 0}

    # ─────────────────────────────────────────────────────────
    # Hot path interface
    # ─────────────────────────────────────────────────────────

    def get_confidence_adjustment(self, ticker: str) -> int:
        """
        Returns net macro confidence adjustment for a given ticker.
        DXY only applies to DXY-sensitive tickers; VIX/credit apply to all longs.
        """
        macro = self.update()
        total_adj = 0

        # VIX term structure — applies to all
        total_adj += macro["vix_signal"].get("confidence_adjustment", 0)

        # Credit spread — applies to all long trades
        total_adj += macro["credit_signal"].get("confidence_adjustment", 0)

        # DXY — only applies to NASDAQ/tech-heavy tickers
        if ticker in _DXY_SENSITIVE_TICKERS:
            total_adj += macro["dxy_signal"].get("confidence_adjustment", 0)

        # Add HMM regime adjustment
        total_adj += macro.get("hmm_signal", {}).get("confidence_adjustment", 0)
        # Add Fear & Greed adjustment
        total_adj += macro.get("fear_greed_signal", {}).get("confidence_adjustment", 0)

        return total_adj

    def get_dxy_signal(self) -> dict:
        """Returns raw DXY signal (for war room display)."""
        return self.update().get("dxy_signal", {})

    def is_risk_off(self) -> bool:
        """True if any major risk-off signal is active."""
        macro = self.update()
        return (
            macro["vix_signal"].get("status") == "BACKWARDATION"
            or macro["credit_signal"].get("status") == "CREDIT_STRESS"
            or macro.get("fear_greed_signal", {}).get("status") == "EXTREME_FEAR"
            or macro.get("hmm_signal", {}).get("is_choppy", False)
        )

    # NOTE: Callers must wire this into DynamicSizer or position sizing pipeline
    def get_size_multiplier(self) -> float:
        """
        Position size multiplier based on macro risk state.
        Risk-off: 0.75x (reduce all long sizes 25%).
        DXY strong: 0.75x for NASDAQ tickers (applied in get_confidence_adjustment).
        """
        if self.is_risk_off():
            return 0.75
        return 1.0

    # ─────────────────────────────────────────────────────────
    # Telegram
    # ─────────────────────────────────────────────────────────

    def get_telegram_note(self) -> str:
        macro = self.update()
        vix = macro["vix_signal"]
        dxy = macro["dxy_signal"]
        credit = macro["credit_signal"]

        lines = ["📊 Cross-Asset Macro:"]
        vix_status = vix.get("status", "N/A")
        vix_ratio = vix.get("ratio")
        lines.append(
            f"  VIX: {vix_status}"
            + (f" ({vix_ratio:.2f})" if vix_ratio else "")
            + (f" conf{vix.get('confidence_adjustment', 0):+d}" if vix.get("confidence_adjustment") else "")
        )

        dxy_ret = dxy.get("return_5d")
        lines.append(
            f"  DXY: {dxy.get('status', 'N/A')}"
            + (f" ({dxy_ret:+.1f}% 5d)" if dxy_ret is not None else "")
            + (f" conf{dxy.get('confidence_adjustment', 0):+d}" if dxy.get("confidence_adjustment") else "")
        )

        credit_chg = credit.get("ratio_change_3d")
        lines.append(
            f"  Credit: {credit.get('status', 'N/A')}"
            + (f" ({credit_chg:+.1f}% 3d)" if credit_chg is not None else "")
            + (f" conf{credit.get('confidence_adjustment', 0):+d}" if credit.get("confidence_adjustment") else "")
        )

        if self.is_risk_off():
            lines.append("  ⚠️  MACRO RISK-OFF — size ×0.75")

        return "\n".join(lines)
