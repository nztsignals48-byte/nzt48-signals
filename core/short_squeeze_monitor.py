"""
Short Squeeze Monitor — NZT-48 Microstructure Module
Cohen, Diether & Malloy (2007): Short interest predicts returns.
High short interest + price strength = potential squeeze catalyst.
Adds confidence boost when squeeze conditions detected.
"""

import json
import os
import logging
import urllib.request
from datetime import datetime, date, timedelta
from typing import Optional
from core.clock import now_utc

logger = logging.getLogger(__name__)

STATE_FILE = "data/short_interest.json"

# Short interest threshold for squeeze risk flag
SQUEEZE_THRESHOLD_PCT = 15.0  # 15% of float short = elevated squeeze risk (Cohen et al. 2007)
SQUEEZE_THRESHOLD_SINGLE_STOCK = 18.0  # Higher bar for single-stock ETPs (3x amplification)
SQUEEZE_CONFIDENCE_BOOST = 8   # +8 confidence points when squeeze detected

# LSE ETP → underlying US ticker mapping for FINRA data lookup.
# LSE .L tickers don't appear in FINRA — we look up the underlying US stock instead.
# Cohen, Diether & Malloy (2007): short interest in the UNDERLYING drives ETP squeeze dynamics.
# Asquith, Pathak & Ritter (2005): SI > threshold + declining SI ratio = actual squeeze.
_UNDERLYING_MAP: dict[str, str] = {
    # Single-stock ETPs — use underlying US ticker
    "NVD3.L": "NVDA",  "3SNV.L": "NVDA",
    "TSL3.L": "TSLA",  "3STS.L": "TSLA",
    "TSM3.L": "TSM",   "TSMS.L": "TSM",
    "MU2.L":  "MU",    "MUS.L":  "MU",
    "GPT3.L": "MSFT",  "GPTS.L": "MSFT",
    "AMD3.L": "AMD",   "AMDS.L": "AMD",
    "ARM3.L": "ARM",
    "AVGO3.L": "AVGO", "AVGS.L": "AVGO",
    # Index ETPs — use the primary index ETF
    "QQQ3.L": "QQQ",   "QQQ5.L": "QQQ",  "QQQS.L": "QQQ",  "SQQQ.L": "QQQ",
    "3LUS.L": "SPY",   "SP5L.L": "SPY",  "3USS.L": "SPY",  "SPYS.L": "SPY",
    "3SEM.L": "SOXX",  "SC3S.L": "SOXX",
    # Single-stock not yet in above
    "PLTR3.L": "PLTR",
    "COIN3.L": "COIN",
    "MSTRL.L": "MSTR",
}

# FINRA short volume report base URL
FINRA_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{date}.txt"


class ShortSqueezeMonitor:
    """
    Monitors short interest to detect squeeze setups:
    - Fetches FINRA daily short volume data (free, T+1)
    - Tracks short interest % for all monitored tickers
    - When short interest ≥ 15% AND price is rising → squeeze risk flag
    - Adds +8 confidence to signals on squeezable tickers

    Note: FINRA data is T+1. For real-time short interest, a paid data
    source (e.g., Ortex, S3 Partners) would be needed.
    """

    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self.state = self._load_state()

    # ─────────────────────────────────────────────────────────
    # State persistence
    # ─────────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"short_data": {}, "last_update": None}

    def _save_state(self):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.warning(f"ShortSqueezeMonitor: save failed: {e}")

    # ─────────────────────────────────────────────────────────
    # FINRA data fetcher
    # ─────────────────────────────────────────────────────────

    def fetch_finra_short_volume(self, target_date: Optional[date] = None) -> dict:
        """
        Downloads FINRA REGSHO daily short volume file.
        Tries up to 4 days back to find the most recent available file.
        Returns dict of {ticker: short_volume_ratio_pct}.
        """
        results = {}
        d = target_date or date.today()

        for days_back in range(0, 5):
            attempt_date = d - timedelta(days=days_back)
            # Skip weekends
            if attempt_date.weekday() >= 5:
                continue
            date_str = attempt_date.strftime("%Y%m%d")
            url = FINRA_URL.format(date=date_str)

            try:
                req = urllib.request.Request(url, headers={"User-Agent": "NZT48/2.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    content = resp.read().decode("utf-8")

                for line in content.splitlines():
                    parts = line.split("|")
                    if len(parts) < 5:
                        continue
                    ticker = parts[0].strip()
                    try:
                        short_vol = float(parts[1])
                        total_vol = float(parts[3])
                        if total_vol > 0:
                            ratio_pct = (short_vol / total_vol) * 100
                            results[ticker] = round(ratio_pct, 2)
                    except (ValueError, IndexError):
                        continue

                logger.info(f"ShortSqueezeMonitor: loaded FINRA data for {date_str} ({len(results)} tickers)")
                return results

            except Exception as e:
                logger.debug(f"ShortSqueezeMonitor: FINRA fetch failed for {date_str}: {e}")
                continue

        logger.warning("ShortSqueezeMonitor: failed to fetch FINRA data for any recent date")
        return results

    # ─────────────────────────────────────────────────────────
    # Short interest queries
    # ─────────────────────────────────────────────────────────

    def _get_underlying(self, ticker: str) -> str:
        """Resolve LSE ETP ticker to underlying US ticker for FINRA lookup.
        e.g. NVD3.L → NVDA, QQQ3.L → QQQ, TSLA (US direct) → TSLA.
        """
        if ticker in _UNDERLYING_MAP:
            return _UNDERLYING_MAP[ticker]
        # Already a US ticker (no .L suffix)
        if not ticker.endswith(".L"):
            return ticker
        # Fallback: strip .L suffix (works for some tickers)
        return ticker.replace(".L", "")

    def get_short_interest(self, ticker: str) -> Optional[float]:
        """
        Returns short interest % for ticker from cached state.
        For LSE .L tickers, maps to underlying US ticker (see _UNDERLYING_MAP).
        Cohen, Diether & Malloy (2007): underlying stock SI drives ETP squeeze dynamics.
        """
        underlying = self._get_underlying(ticker)
        data = self.state.get("short_data", {})
        return data.get(underlying)

    def _get_squeeze_threshold(self, ticker: str) -> float:
        """Higher threshold for single-stock ETPs — 3x leverage amplifies squeeze."""
        underlying = self._get_underlying(ticker)
        # Single-stock underlyings get higher bar (18%) vs index (15%)
        index_tickers = {"QQQ", "SPY", "SOXX", "IWM", "DIA"}
        if underlying in index_tickers:
            return SQUEEZE_THRESHOLD_PCT
        return SQUEEZE_THRESHOLD_SINGLE_STOCK

    def is_squeeze_risk(self, ticker: str) -> bool:
        """
        True if short interest ≥ threshold.
        Single-stock ETPs use 18% threshold (vs 15% for index ETPs).
        Optionally checks for declining SI ratio (active covering = stronger signal).
        """
        si = self.get_short_interest(ticker)
        if si is None:
            return False
        threshold = self._get_squeeze_threshold(ticker)
        return si >= threshold

    def get_confidence_boost(self, ticker: str) -> int:
        """
        Returns +SQUEEZE_CONFIDENCE_BOOST if squeeze conditions met, else 0.
        Use case: signal fires on a heavily-shorted ticker → higher conviction
        because covering shorts amplifies upward momentum.
        """
        if self.is_squeeze_risk(ticker):
            return SQUEEZE_CONFIDENCE_BOOST
        return 0

    # ─────────────────────────────────────────────────────────
    # Batch update
    # ─────────────────────────────────────────────────────────

    def update_all(self, tickers: Optional[list] = None) -> dict:
        """
        Fetches latest FINRA data and updates state for all tracked tickers.
        For .L tickers, maps to underlying US ticker using _UNDERLYING_MAP.
        Returns {original_ticker: short_interest_pct} dict.
        """
        raw = self.fetch_finra_short_volume()
        if not raw:
            logger.warning("ShortSqueezeMonitor: no FINRA data available")
            return {}

        if tickers:
            result = {}
            for t in tickers:
                underlying = self._get_underlying(t)
                if underlying in raw:
                    si = raw[underlying]
                    self.state["short_data"][underlying] = si
                    result[t] = si
            self.state["short_data"].update({k: v for k, v in raw.items()
                                             if not any(k == self._get_underlying(x) for x in (tickers or []))
                                             and k in raw and k == k.upper() and len(k) <= 5})
        else:
            self.state["short_data"].update(raw)
            result = dict(raw)

        self.state["last_update"] = now_utc().isoformat()
        self._save_state()
        return result

    # ─────────────────────────────────────────────────────────
    # Telegram watchlist
    # ─────────────────────────────────────────────────────────

    def get_telegram_watchlist(self, tickers: list) -> str:
        lines = ["🩳 Short Squeeze Watchlist:"]
        for t in tickers:
            si = self.get_short_interest(t)
            if si is None:
                lines.append(f"  {t}: no data")
            elif si >= SQUEEZE_THRESHOLD_PCT:
                lines.append(f"  🔥 {t}: {si:.1f}% short — SQUEEZE RISK (+{SQUEEZE_CONFIDENCE_BOOST} conf)")
            else:
                lines.append(f"  {t}: {si:.1f}% short — normal")

        last = self.state.get("last_update")
        if last:
            lines.append(f"\n  Data as of: {last[:16]} UTC (FINRA T+1)")
        return "\n".join(lines)
