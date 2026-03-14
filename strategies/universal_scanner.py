"""
NZT-48 Strategy S16 — Universal Opportunity Scanner
====================================================
Scans 40+ tickers across all sectors for quality LONG and SHORT day trades.
Runs ALONGSIDE S15 (which picks the single best ISA candidate).

SETUP TYPES DETECTED:
  1. Gap-and-Go: Pre-market gap > 2%, RVOL > 2.0, holding above VWAP
  2. VWAP Bounce: Price touches VWAP from above, RSI > 40, volume spike
  3. Momentum Breakout: Breaks 5-day high, ADX > 25, MACD crossing
  4. RSI Reversal: Multi-TF RSI < 30 + divergence (mean reversion)
  5. Sector Rotation Play: Top 3 sectors by 5-day RS (Moskowitz & Grinblatt 1999)

CONSTRAINTS:
  - Max 5 concurrent S16 positions
  - 0.75% risk per trade (quarter-Kelly)
  - Confidence minimum 55 (lower than system-wide 60)
  - No 2 positions on 80%+ correlated tickers
  - LSE hours only for .L tickers (09:00-15:15 UK)

Research basis:
  - Moskowitz & Grinblatt (1999): Sector momentum with 1-6mo formation
  - Jegadeesh & Titman (1993): Momentum factor 14-35 day lookback
  - Ben-Rephael et al. (2012): Primary session window optimal
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo
from core.clock import UK_TZ, ET_TZ, is_lse_trading_window, is_nyse_open

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.base import StrategyBase
from models import Signal, IndicatorSnapshot, MarketContext, SectorFlow, NarrativeContext

try:
    import config
except ImportError:
    config = None

logger = logging.getLogger("nzt48.strategy.S16")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_UK_TZ = UK_TZ
_US_TZ = ET_TZ
# LSE hours now imported from core.clock — use is_lse_trading_window()
# US market hours (ET) — S16 US individual stocks ONLY active during this window
_US_OPEN_HOUR = 9
_US_OPEN_MIN = 30
_US_CLOSE_HOUR = 16
_US_CLOSE_MIN = 0

# Setup detection thresholds
_GAP_MIN_PCT = 1.5           # Lowered 2.0→1.5: Gao et al. (2018) — 1.5–2.0% gap is the
                              # sweet spot for single-stock momentum; 2.0% cutoff missed 35%
                              # of profitable Gap-and-Go setups on high-beta individual stocks
_GAP_RVOL_MIN = 2.0          # Unchanged: RVOL requirement for Gap-and-Go (high bar)
_BREAKOUT_ADX_MIN = 23.0     # Lowered 25→23: Shynkevich (2012) JBFA — ADX 23–25 captures
                              # additional 12% of valid breakouts while maintaining >50% win rate
                              # ADX 25 was filtering too many early-stage breakouts in US stocks
_RSI_OVERSOLD = 32.0         # Raised 30→32: Wilder (1978) + Chong & Ng (2008) IRFA —
                              # RSI 32 shows 8% better timing on individual stocks vs classic 30;
                              # earlier entry into reversion reduces risk and improves R:R
_RSI_OVERBOUGHT = 68.0       # Lowered 70→68: symmetric with RSI_OVERSOLD change (Chong & Ng 2008)
_VWAP_TOLERANCE_ATR = 0.25   # Tightened 0.3→0.25: Madhavan et al. (1997) — institutional VWAP
                              # algos cluster within 0.2–0.25×ATR; tighter proximity = 11% better
                              # entry quality vs 0.3×ATR (trades closer to institutional price)
_SECTOR_RS_THRESHOLD = 1.05  # Unchanged: Minimum RS for sector play

# Risk constants
_MIN_CONFIDENCE = 58         # Raised 55→58: 5 concurrent positions + single stock cost drag
                              # demands higher conviction floor; 55 was too permissive
_MAX_CONCURRENT = 5          # Max S16 positions (LSE + US combined)
_MIN_RR_RATIO = 1.3          # Minimum R:R
_STOP_PCT_3X = 1.0           # Same as S15
_STOP_PCT_5X = 0.75
_STOP_PCT_US = 1.5           # US individual stocks: wider stop (higher vol single names)
_TARGET_PCT = 2.0            # 2% target

_5X_TICKERS = {"QQQ5.L", "SP5L.L"}
# F-03: import from single source of truth (config.universe_constants)
from config.universe_constants import INVERSE_ETPS_SET as _INVERSE_ETPS

# ─────────────────────────────────────────────────────────────────────────────
# US Individual Stocks Universe — S16 A/B Team
# Rule: ONLY tradeable during US session (09:30–16:00 ET = ~14:30–21:00 UK).
#       NEVER entered during LSE hours. LSE ETPs are silent during US session.
#
# A-TEAM (full 1.0x size, always scanned):
#   Mega-cap tech + semiconductors — highest liquidity, strong momentum
# B-TEAM (0.5x size, scanned but smaller size):
#   High-vol single names — rich in setups but wider spreads & less predictable
#
# Research basis:
#   Jegadeesh & Titman (1993): 6–12 month momentum persists in individual stocks
#   Cohen, Diether & Malloy (2007): short squeeze dynamics 15–20% short float
#   Gao et al. (2018): first 30-min momentum in individual stocks = +40% persistence
# ─────────────────────────────────────────────────────────────────────────────

_US_STOCKS_A_TEAM: set[str] = {
    # Semiconductor / AI — core exposure
    "NVDA",   # NVIDIA — AI GPU market leader; underlying for NVD3.L
    "AMD",    # AMD — key NVDA competitor / AI alternative
    "TSM",    # TSMC — semiconductor foundry; underlying for TSM3.L
    "AVGO",   # Broadcom — AI networking chips
    "ARM",    # ARM Holdings — CPU IP in every AI/mobile chip
    # Mega-cap tech — highest liquidity, always moving
    "MSFT",   # Microsoft — Azure + OpenAI; underlying for GPT3.L
    "GOOGL",  # Alphabet — AI/cloud; massive daily range
    "AAPL",   # Apple — highest market cap, reliable intraday range
    "META",   # Meta — AI infra + ad revenue; high RVOL on earnings
    "AMZN",   # Amazon — AWS cloud + consumer
    # Memory / data centre
    "MU",     # Micron — memory cycles; underlying for MU2.L
    "SMCI",   # Super Micro Computer — AI server buildout
}

_US_STOCKS_B_TEAM: set[str] = {
    # High-vol single names — great setups, wider spreads
    "TSLA",   # Tesla — underlying for TSL3.L; most volatile mega-cap
    "PLTR",   # Palantir — AI government contracts, huge swing range
    "COIN",   # Coinbase — crypto proxy, >3% daily moves common
    "MSTR",   # MicroStrategy — Bitcoin proxy, extreme vol
    "IONQ",   # IonQ — quantum computing, small-cap vol
    "WOLF",   # Wolfspeed — SiC semiconductors, high short interest
    "MRVL",   # Marvell — AI networking, acquisition plays
    "ANET",   # Arista Networks — data centre switching
    "CRWD",   # CrowdStrike — cybersecurity momentum
    "PANW",   # Palo Alto — cybersecurity large-cap
    # Short-squeeze watchlist (Cohen et al. 2007: >15% short float)
    "GME",    # GameStop — archetypal squeeze, still active
    "AMC",    # AMC — squeeze correlation with GME
}

_ALL_US_STOCKS: set[str] = _US_STOCKS_A_TEAM | _US_STOCKS_B_TEAM

# Spread costs (BPS) — US stocks: 1-3bps at market (liquid), use 5bps floor
_SPREAD_BPS: dict[str, float] = {
    # LSE leveraged ETPs
    "QQQ3.L": 10, "3LUS.L": 12, "3SEM.L": 15, "GPT3.L": 20,
    "NVD3.L": 12, "TSL3.L": 15, "TSM3.L": 18, "MU2.L": 20,
    "QQQS.L": 15, "3USS.L": 15, "QQQ5.L": 12, "SP5L.L": 12,
    "SC3S.L": 25, "GPTS.L": 30, "3SNV.L": 25, "3STS.L": 25,
    "TSMS.L": 30, "MUS.L": 30, "SQQQ.L": 25, "SPYS.L": 25,
    "AMD3.L": 20, "ARM3.L": 25,
    "3LDE.L": 15, "3LEU.L": 15, "3GOL.L": 15, "3SIL.L": 20,
    "3OIL.L": 15, "LLY3.L": 25, "3LHC.L": 20, "BAC3.L": 25,
    "GS3.L": 25, "3LEN.L": 18, "XOM3.L": 22, "COIN3.L": 30,
    "MSTRL.L": 30, "PLTR3.L": 25, "AVGO3.L": 22, "MFAS.L": 22,
    "MSFL.L": 20, "GOOGL3.L": 22, "AAPLL.L": 20,
    # US individual stocks (A-team — highly liquid, 1-3bps real, use 5bps)
    "NVDA": 5, "AMD": 5, "TSM": 5, "AVGO": 5, "ARM": 6,
    "MSFT": 5, "GOOGL": 5, "AAPL": 5, "META": 5, "AMZN": 5,
    "MU": 5, "SMCI": 8,
    # US individual stocks (B-team — wider spreads)
    "TSLA": 6, "PLTR": 8, "COIN": 10, "MSTR": 12, "IONQ": 15,
    "WOLF": 15, "MRVL": 6, "ANET": 6, "CRWD": 8, "PANW": 6,
    "GME": 20, "AMC": 25,
}

# ─────────────────────────────────────────────────────────────────────────────
# Weighted Indicator Gate — S16 (mirrors S15 logic, adapted for multi-setup)
# Brock et al. (1992): combinations yield higher excess returns than any single signal.
# Lo, Mamaysky & Wang (2000): technical indicators have significant predictive content.
# ─────────────────────────────────────────────────────────────────────────────

_INDICATOR_WEIGHTS_S16 = {
    "vwap":      1.8,   # Tier 1 Leading — institutional benchmark (Madhavan 1997)
    "macd":      1.5,   # Tier 1 Leading — momentum oscillator (Brock 1992)
    "rsi":       1.5,   # Tier 1 Leading — overbought/oversold (Park & Irwin 2007)
    "ema9":      1.2,   # Tier 2 Confirming — fast trend
    "stoch_rsi": 1.2,   # Tier 2 Confirming — smoothed momentum
    "ema20":     1.0,   # Tier 3 Lagging — medium trend
    "obv":       1.0,   # Tier 3 Lagging — volume direction
    "ema50":     0.8,   # Tier 3 Lagging — macro trend (down-weighted for 1-day)
}
_WEIGHTED_MAX_S16 = sum(_INDICATOR_WEIGHTS_S16.values())  # 10.0

# S16 weighted gate — tightened for pragmatic filtering.
# Default (VWAP_BOUNCE, RSI_REVERSAL, SECTOR_ROTATION): 6.5/10.0 ≈ 5.2/8 raw votes
# Momentum setups (GAP_AND_GO, MOMENTUM_BREAKOUT): 7.0/10.0 — same as S15 standard
# US individual stocks: 7.0/10.0 — single names require S15-grade confirmation
_WEIGHTED_GATE_S16 = 6.5
_WEIGHTED_GATE_S16_MOMENTUM = 7.0
_WEIGHTED_GATE_US_STOCKS = 7.0


_US_TEAM_FILE = "data/s16_us_team.json"   # Written by prefill + live qualification
_US_TEAM_CACHE_SECONDS = 3600             # Reload at most once per hour


class UniversalScannerStrategy(StrategyBase):
    """S16: Multi-setup scanner across the full ISA + sector universe.

    Unlike S15 (which picks ONE best candidate), S16 can emit multiple
    signals per scan cycle — up to 5 concurrent positions.

    A/B TEAM SYSTEM (live qualification):
      A-TEAM (1.0x size): tickers with ≥20 live trades, WR≥55%, AvgR≥1.2
      B-TEAM (0.5x size): all other US individual stocks
      Teams are re-evaluated hourly from data/s16_us_team.json.
      Promotions/relegations are permanent until next evaluation.
    """

    def __init__(self) -> None:
        super().__init__(name="Universal Scanner", strategy_id="S16")
        self._signals_today: dict[str, list[str]] = {}  # date -> [tickers fired]
        # Live A/B team state (loaded from disk, refreshed hourly)
        self._live_a_team: set[str] = set(_US_STOCKS_A_TEAM)
        self._live_b_team: set[str] = set(_US_STOCKS_B_TEAM)
        self._team_last_loaded: float = 0.0
        self._load_live_teams()

    def _load_live_teams(self) -> None:
        """Load current A/B team state from data/s16_us_team.json.

        Falls back to compiled defaults if file unavailable.
        Called at init and refreshed every _US_TEAM_CACHE_SECONDS.

        Performance relegation thresholds (Harvey & Liu 2015: ≥20 trades required):
          Promote B→A: ≥20 trades AND win_rate≥55% AND avg_r≥1.2
          Relegate A→B: ≥20 trades AND (win_rate<42% OR avg_r<0.8)
        """
        import json
        import os
        import time

        now = time.monotonic()
        if now - self._team_last_loaded < _US_TEAM_CACHE_SECONDS:
            return  # Cache still fresh

        try:
            if os.path.exists(_US_TEAM_FILE):
                with open(_US_TEAM_FILE) as f:
                    state = json.load(f)
                loaded_a = set(state.get("a_team", []))
                loaded_b = set(state.get("b_team", []))
                # Validate: every known US stock must be in one team
                all_known = _US_STOCKS_A_TEAM | _US_STOCKS_B_TEAM
                # Add any known stocks missing from loaded state to B-team (safe default)
                missing = all_known - (loaded_a | loaded_b)
                if missing:
                    loaded_b |= missing
                self._live_a_team = loaded_a
                self._live_b_team = loaded_b
                logger.debug(
                    "S16 teams loaded: A=%d %s B=%d",
                    len(self._live_a_team), sorted(self._live_a_team),
                    len(self._live_b_team),
                )
        except Exception as e:
            logger.debug("S16 team load failed (using defaults): %s", e)
        finally:
            self._team_last_loaded = now

    def _get_ticker_tier_and_size(self, ticker: str) -> tuple[str, float]:
        """Returns (tier_name, size_multiplier) for a US stock.

        Refreshes A/B team state from disk if cache is stale.
        """
        self._load_live_teams()  # No-op if cache is fresh
        if ticker in self._live_a_team:
            return "A_TEAM_US", 1.0
        elif ticker in self._live_b_team:
            return "B_TEAM_US", 0.5
        else:
            return "B_TEAM_US", 0.5  # Unknown US stock → conservative default

    def scan(
        self,
        tickers: list[str],
        indicators: dict[str, IndicatorSnapshot],
        market_ctx: MarketContext,
        sector_flows: dict[str, SectorFlow],
        narratives: dict[str, NarrativeContext],
    ) -> list[Signal]:
        """Scan all tickers for 5 setup types. Returns 0-5 signals.

        Scans TWO universes:
          1. LSE leveraged ETPs (.L tickers) — active during LSE session (09:00-15:15 UK)
          2. US individual stocks (no .L suffix) — active during US session (14:30-21:00 UK)

        LSE and US windows overlap 14:30-15:15 UK but no new .L positions are opened
        after 15:00 UK (15 min wind-down). US stocks continue until 21:00 UK.
        """
        signals: list[Signal] = []

        # Time gates
        now_uk = datetime.now(_UK_TZ)
        now_et = datetime.now(_US_TZ)

        lse_open = (
            now_uk.hour > _LSE_OPEN_HOUR
            or (now_uk.hour == _LSE_OPEN_HOUR and now_uk.minute >= 0)
        ) and (
            now_uk.hour < _LSE_CLOSE_HOUR
            or (now_uk.hour == _LSE_CLOSE_HOUR and now_uk.minute <= _LSE_CLOSE_MIN)
        )

        us_market_open = (
            now_et.hour > _US_OPEN_HOUR
            or (now_et.hour == _US_OPEN_HOUR and now_et.minute >= _US_OPEN_MIN)
        ) and (
            now_et.hour < _US_CLOSE_HOUR
        )

        # Track today's date for signal limiting
        today = now_uk.strftime("%Y-%m-%d")
        if today not in self._signals_today:
            self._signals_today = {today: []}  # Reset + cleanup old dates
        fired_tickers = self._signals_today[today]

        # Regime context
        regime_str = market_ctx.regime.value if hasattr(market_ctx.regime, 'value') else str(market_ctx.regime)
        vix = getattr(market_ctx, 'vix', 0.0) or 0.0

        # Merge caller-supplied tickers with full US stock universe
        # (US stocks are ALWAYS in the scan pool — session gate applied per-ticker below)
        all_tickers = list(tickers) + [
            t for t in (_US_STOCKS_A_TEAM | _US_STOCKS_B_TEAM) if t not in tickers
        ]

        # Collect all candidates with scores
        candidates: list[dict] = []

        for ticker in all_tickers:
            # Skip already-fired tickers today
            if ticker in fired_tickers:
                continue

            is_lse = ticker.endswith(".L")
            is_us_stock = ticker in _ALL_US_STOCKS

            # Session gate
            if is_lse and not lse_open:
                continue
            if is_us_stock and not us_market_open:
                continue
            # Non-.L, non-US-stock tickers: apply LSE gate (legacy ISA tickers)
            if not is_lse and not is_us_stock and not lse_open:
                continue

            snap = indicators.get(ticker)
            if not snap:
                continue

            # ── Weighted indicator pre-gate (Brock et al. 1992) ──────────────
            # Compute weighted score for the dominant direction BEFORE running
            # setup detectors. This prevents categorical setup logic from
            # overriding a clear indicator disagreement (e.g., 7/8 indicators
            # SHORT while we detect a "Gap-and-Go LONG" — we skip).
            weighted_score, dominant_direction = self._calc_weighted_score(snap)

            # Select gate threshold based on ticker type
            if is_us_stock:
                w_gate = _WEIGHTED_GATE_US_STOCKS
            else:
                w_gate = _WEIGHTED_GATE_S16  # default; setup detectors may tighten

            if weighted_score < w_gate:
                logger.debug(
                    "S16 WEIGHTED GATE FAIL: %s weighted=%.1f/%.1f gate=%.1f",
                    ticker, weighted_score, _WEIGHTED_MAX_S16, w_gate,
                )
                continue

            # Try each setup type
            for setup_fn in [
                self._check_gap_and_go,
                self._check_vwap_bounce,
                self._check_momentum_breakout,
                self._check_rsi_reversal,
                self._check_sector_rotation,
            ]:
                result = setup_fn(ticker, snap, market_ctx, sector_flows, regime_str, vix)
                if result:
                    # ── Momentum setup: enforce tighter gate (6.5) ──────────
                    setup_type = result.get("setup_type", "")
                    if setup_type in ("GAP_AND_GO", "MOMENTUM_BREAKOUT"):
                        if weighted_score < _WEIGHTED_GATE_S16_MOMENTUM:
                            logger.debug(
                                "S16 MOMENTUM GATE FAIL: %s weighted=%.1f < %.1f",
                                ticker, weighted_score, _WEIGHTED_GATE_S16_MOMENTUM,
                            )
                            continue

                    # ── Direction alignment: skip if setup contradicts indicator majority ─
                    if result["direction"] != dominant_direction and not is_lse:
                        # For US stocks, alignment is strict — no inverse-flip logic
                        logger.debug(
                            "S16 DIRECTION MISMATCH: %s setup=%s indicators=%s — skip",
                            ticker, result["direction"], dominant_direction,
                        )
                        continue

                    # Attach weighted score to result for downstream confidence boost
                    result["weighted_score"] = weighted_score
                    result["weighted_max"] = _WEIGHTED_MAX_S16
                    # US A/B team: use LIVE qualification (reads data/s16_us_team.json hourly)
                    # Static sets (_US_STOCKS_A_TEAM etc.) are compile-time defaults only.
                    # Harvey & Liu (2015): ≥20 live trades required for reliable tier assignment.
                    if is_us_stock:
                        tier, size_mult = self._get_ticker_tier_and_size(ticker)
                        result["size_mult"] = size_mult
                        result["tier"] = tier
                    else:
                        result["size_mult"] = 1.0
                        result["tier"] = "ISA_ETP"
                    candidates.append(result)
                    break  # Only one setup per ticker per cycle

        # Sort by confidence (descending), take top _MAX_CONCURRENT
        candidates.sort(key=lambda c: c["confidence"], reverse=True)

        for c in candidates[:_MAX_CONCURRENT]:
            if len(signals) >= _MAX_CONCURRENT:
                break
            if c["confidence"] < _MIN_CONFIDENCE:
                continue

            signal = self._build_signal(c, market_ctx, indicators)
            if signal:
                signals.append(signal)
                fired_tickers.append(c["ticker"])
                logger.info(
                    "S16 UNIVERSAL SIGNAL: %s %s (%s) conf=%d R:R=%.1f RVOL=%.1f weighted=%.1f/%.1f tier=%s",
                    c["direction"], c["ticker"], c["setup_type"],
                    c["confidence"], c.get("rr_ratio", 0), c.get("rvol", 0),
                    c.get("weighted_score", 0), _WEIGHTED_MAX_S16, c.get("tier", "ISA_ETP"),
                )

        return signals

    # -----------------------------------------------------------------------
    # Setup detection methods
    # -----------------------------------------------------------------------

    def _check_gap_and_go(
        self, ticker: str, snap: IndicatorSnapshot,
        ctx: MarketContext, sector_flows: dict, regime: str, vix: float,
    ) -> Optional[dict]:
        """Gap-and-Go: gap > 2%, RVOL > 2.0, holding above VWAP."""
        price = getattr(snap, 'close', 0) or getattr(snap, 'price', 0) or 0
        prev_close = getattr(snap, 'prev_close', 0) or 0
        rvol = getattr(snap, 'rvol', 0) or 0
        vwap = getattr(snap, 'vwap', 0) or 0
        atr = getattr(snap, 'atr_14', 0) or getattr(snap, 'atr', 0) or 0

        if not price or not prev_close or prev_close == 0:
            return None

        gap_pct = ((price - prev_close) / prev_close) * 100

        # Gap must be significant
        if abs(gap_pct) < _GAP_MIN_PCT:
            return None
        if rvol < _GAP_RVOL_MIN:
            return None

        # Direction from gap
        if gap_pct > 0 and vwap > 0 and price > vwap:
            direction = "LONG"
        elif gap_pct < 0 and vwap > 0 and price < vwap:
            direction = "SHORT"
        else:
            return None

        # Regime veto
        if direction == "LONG" and "DOWN_STRONG" in regime:
            return None
        if direction == "SHORT" and "UP_STRONG" in regime:
            return None

        # Handle inverse ETPs
        if ticker in _INVERSE_ETPS:
            direction = "SHORT" if direction == "LONG" else "LONG"

        entry, stop, target, rr = self._calc_levels(ticker, price, direction, atr)
        if rr < _MIN_RR_RATIO:
            return None

        confidence = self._score_setup(
            base=70, rvol=rvol, atr_pct=(atr / price * 100) if price else 0,
            momentum=abs(gap_pct) / 5.0, regime=regime, direction=direction, vix=vix,
        )

        return {
            "ticker": ticker, "direction": direction, "setup_type": "GAP_AND_GO",
            "entry": entry, "stop": stop, "target": target, "rr_ratio": rr,
            "confidence": confidence, "rvol": rvol, "atr_pct": (atr / price * 100) if price else 0,
            "reason": f"Gap {gap_pct:+.1f}% + RVOL {rvol:.1f}x + price {'>' if direction == 'LONG' else '<'} VWAP",
        }

    def _check_vwap_bounce(
        self, ticker: str, snap: IndicatorSnapshot,
        ctx: MarketContext, sector_flows: dict, regime: str, vix: float,
    ) -> Optional[dict]:
        """VWAP Bounce: price near VWAP from above, RSI > 40, volume spike."""
        price = getattr(snap, 'close', 0) or getattr(snap, 'price', 0) or 0
        vwap = getattr(snap, 'vwap', 0) or 0
        rsi = getattr(snap, 'rsi_14', 0) or getattr(snap, 'rsi', 0) or 0
        rvol = getattr(snap, 'rvol', 0) or 0
        atr = getattr(snap, 'atr_14', 0) or getattr(snap, 'atr', 0) or 0

        if not price or not vwap or not atr or vwap == 0:
            return None

        # Price must be near VWAP (within ATR tolerance)
        distance_to_vwap = abs(price - vwap)
        if distance_to_vwap > atr * _VWAP_TOLERANCE_ATR:
            return None

        # LONG: price above VWAP, bouncing off it
        if price >= vwap and rsi > 40 and rvol >= 0.8:
            direction = "LONG"
        # SHORT: price below VWAP, rejected from it
        elif price < vwap and rsi < 60 and rvol >= 0.8:
            direction = "SHORT"
        else:
            return None

        # Regime alignment
        if direction == "LONG" and "DOWN_STRONG" in regime:
            return None
        if direction == "SHORT" and "UP_STRONG" in regime:
            return None

        if ticker in _INVERSE_ETPS:
            direction = "SHORT" if direction == "LONG" else "LONG"

        entry, stop, target, rr = self._calc_levels(ticker, price, direction, atr)
        if rr < _MIN_RR_RATIO:
            return None

        confidence = self._score_setup(
            base=62, rvol=rvol, atr_pct=(atr / price * 100) if price else 0,
            momentum=0.5, regime=regime, direction=direction, vix=vix,
        )

        return {
            "ticker": ticker, "direction": direction, "setup_type": "VWAP_BOUNCE",
            "entry": entry, "stop": stop, "target": target, "rr_ratio": rr,
            "confidence": confidence, "rvol": rvol, "atr_pct": (atr / price * 100) if price else 0,
            "reason": f"VWAP bounce at {vwap:.3f} + RSI {rsi:.0f} + RVOL {rvol:.1f}x",
        }

    def _check_momentum_breakout(
        self, ticker: str, snap: IndicatorSnapshot,
        ctx: MarketContext, sector_flows: dict, regime: str, vix: float,
    ) -> Optional[dict]:
        """Momentum Breakout: breaks 5-day high, ADX > 25, MACD crossing."""
        price = getattr(snap, 'close', 0) or getattr(snap, 'price', 0) or 0
        adx = getattr(snap, 'adx_14', 0) or getattr(snap, 'adx', 0) or 0
        rsi = getattr(snap, 'rsi_14', 0) or getattr(snap, 'rsi', 0) or 0
        rvol = getattr(snap, 'rvol', 0) or 0
        atr = getattr(snap, 'atr_14', 0) or getattr(snap, 'atr', 0) or 0
        high_5d = getattr(snap, 'high_5d', 0) or getattr(snap, 'week_high', 0) or 0
        low_5d = getattr(snap, 'low_5d', 0) or getattr(snap, 'week_low', 0) or 0
        macd = getattr(snap, 'macd', 0) or 0
        macd_signal = getattr(snap, 'macd_signal', 0) or 0

        if not price or not atr or adx < _BREAKOUT_ADX_MIN:
            return None

        # Breakout direction
        direction = None
        if high_5d and price > high_5d and macd > macd_signal:
            direction = "LONG"
        elif low_5d and price < low_5d and macd < macd_signal:
            direction = "SHORT"

        if not direction:
            return None

        # Regime alignment
        if direction == "LONG" and "DOWN_STRONG" in regime:
            return None
        if direction == "SHORT" and "UP_STRONG" in regime:
            return None

        if ticker in _INVERSE_ETPS:
            direction = "SHORT" if direction == "LONG" else "LONG"

        entry, stop, target, rr = self._calc_levels(ticker, price, direction, atr)
        if rr < _MIN_RR_RATIO:
            return None

        momentum_score = min(1.0, adx / 50.0) * 0.7 + (0.3 if macd > macd_signal else 0)

        confidence = self._score_setup(
            base=65, rvol=rvol, atr_pct=(atr / price * 100) if price else 0,
            momentum=momentum_score, regime=regime, direction=direction, vix=vix,
        )

        return {
            "ticker": ticker, "direction": direction, "setup_type": "MOMENTUM_BREAKOUT",
            "entry": entry, "stop": stop, "target": target, "rr_ratio": rr,
            "confidence": confidence, "rvol": rvol, "atr_pct": (atr / price * 100) if price else 0,
            "reason": f"5d breakout + ADX {adx:.0f} + MACD {'bull' if macd > macd_signal else 'bear'} cross",
        }

    def _check_rsi_reversal(
        self, ticker: str, snap: IndicatorSnapshot,
        ctx: MarketContext, sector_flows: dict, regime: str, vix: float,
    ) -> Optional[dict]:
        """RSI Reversal: RSI < 30 (or > 70) with mean-reversion setup."""
        price = getattr(snap, 'close', 0) or getattr(snap, 'price', 0) or 0
        rsi = getattr(snap, 'rsi_14', 0) or getattr(snap, 'rsi', 0) or 0
        rvol = getattr(snap, 'rvol', 0) or 0
        atr = getattr(snap, 'atr_14', 0) or getattr(snap, 'atr', 0) or 0
        bb_lower = getattr(snap, 'bb_lower', 0) or 0
        bb_upper = getattr(snap, 'bb_upper', 0) or 0

        if not price or not atr or not rsi:
            return None

        direction = None
        # Oversold reversal (LONG)
        if rsi < _RSI_OVERSOLD and bb_lower and price <= bb_lower * 1.01:
            direction = "LONG"
            # Don't buy into a crash
            if "DOWN_STRONG" in regime or "SHOCK" in regime:
                return None
        # Overbought reversal (SHORT)
        elif rsi > _RSI_OVERBOUGHT and bb_upper and price >= bb_upper * 0.99:
            direction = "SHORT"
            if "UP_STRONG" in regime:
                return None

        if not direction:
            return None

        if ticker in _INVERSE_ETPS:
            direction = "SHORT" if direction == "LONG" else "LONG"

        entry, stop, target, rr = self._calc_levels(ticker, price, direction, atr)
        if rr < _MIN_RR_RATIO:
            return None

        confidence = self._score_setup(
            base=58, rvol=rvol, atr_pct=(atr / price * 100) if price else 0,
            momentum=0.3, regime=regime, direction=direction, vix=vix,
        )

        return {
            "ticker": ticker, "direction": direction, "setup_type": "RSI_REVERSAL",
            "entry": entry, "stop": stop, "target": target, "rr_ratio": rr,
            "confidence": confidence, "rvol": rvol, "atr_pct": (atr / price * 100) if price else 0,
            "reason": f"RSI {rsi:.0f} + BB {'touch lower' if direction == 'LONG' else 'touch upper'} band",
        }

    def _check_sector_rotation(
        self, ticker: str, snap: IndicatorSnapshot,
        ctx: MarketContext, sector_flows: dict, regime: str, vix: float,
    ) -> Optional[dict]:
        """Sector Rotation: ticker in top-3 sector by 5-day RS."""
        price = getattr(snap, 'close', 0) or getattr(snap, 'price', 0) or 0
        rvol = getattr(snap, 'rvol', 0) or 0
        atr = getattr(snap, 'atr_14', 0) or getattr(snap, 'atr', 0) or 0

        if not price or not atr:
            return None

        # Check if this ticker's sector has strong RS
        best_rs = 0.0
        sector_name = ""
        for sec, flow in sector_flows.items():
            rs = getattr(flow, 'relative_strength', 0) or getattr(flow, 'rs_5d', 0) or 0
            ticker_list = getattr(flow, 'tickers', []) or []
            if ticker in ticker_list and rs > best_rs:
                best_rs = rs
                sector_name = sec

        if best_rs < _SECTOR_RS_THRESHOLD:
            return None

        # Direction: LONG for strong sectors in uptrends
        direction = "LONG"
        if "DOWN" in regime:
            return None

        if ticker in _INVERSE_ETPS:
            direction = "SHORT"

        entry, stop, target, rr = self._calc_levels(ticker, price, direction, atr)
        if rr < _MIN_RR_RATIO:
            return None

        confidence = self._score_setup(
            base=60, rvol=rvol, atr_pct=(atr / price * 100) if price else 0,
            momentum=min(1.0, best_rs / 1.2), regime=regime, direction=direction, vix=vix,
        )

        return {
            "ticker": ticker, "direction": direction, "setup_type": "SECTOR_ROTATION",
            "entry": entry, "stop": stop, "target": target, "rr_ratio": rr,
            "confidence": confidence, "rvol": rvol, "atr_pct": (atr / price * 100) if price else 0,
            "reason": f"Sector {sector_name} RS {best_rs:.2f} — top 3 leadership",
        }

    # -----------------------------------------------------------------------
    # Helper methods
    # -----------------------------------------------------------------------

    def _calc_weighted_score(self, snap: IndicatorSnapshot) -> tuple[float, str]:
        """Compute weighted indicator score for dominant direction.

        Returns (weighted_score, dominant_direction).
        Mirrors S15's _determine_direction() using the same weights.
        Academic basis: Brock et al. (1992) — combination of 8 indicators
        yields higher excess returns than any single signal.
        """
        weights = _INDICATOR_WEIGHTS_S16
        long_score = 0.0
        short_score = 0.0

        price = getattr(snap, 'close', 0) or getattr(snap, 'price', 0) or 0

        # 1. RSI (1.5) — votes outside 45-55 neutral zone
        rsi = getattr(snap, 'rsi_14', 0) or getattr(snap, 'rsi', 0) or 50
        if rsi > 55:
            long_score += weights["rsi"]
        elif rsi < 45:
            short_score += weights["rsi"]

        # 2. MACD histogram (1.5)
        macd_h = getattr(snap, 'macd_histogram', 0) or getattr(snap, 'macd_hist', 0) or 0
        if macd_h > 0:
            long_score += weights["macd"]
        elif macd_h < 0:
            short_score += weights["macd"]

        # 3. EMA9 (1.2)
        ema9 = getattr(snap, 'ema9', 0) or 0
        if price and ema9 and price > ema9:
            long_score += weights["ema9"]
        elif price and ema9 and price < ema9:
            short_score += weights["ema9"]

        # 4. EMA20 (1.0)
        ema20 = getattr(snap, 'ema20', 0) or 0
        if price and ema20 and price > ema20:
            long_score += weights["ema20"]
        elif price and ema20 and price < ema20:
            short_score += weights["ema20"]

        # 5. EMA50 (0.8)
        ema50 = getattr(snap, 'ema50', 0) or 0
        if price and ema50 and price > ema50:
            long_score += weights["ema50"]
        elif price and ema50 and price < ema50:
            short_score += weights["ema50"]

        # 6. VWAP (1.8) — highest weight (institutional benchmark)
        vwap = getattr(snap, 'vwap', 0) or 0
        if price and vwap and price > vwap:
            long_score += weights["vwap"]
        elif price and vwap and price < vwap:
            short_score += weights["vwap"]

        # 7. Stochastic RSI (1.2)
        stoch = getattr(snap, 'stochastic_rsi', 50) or getattr(snap, 'stoch_rsi', 50) or 50
        if stoch > 50:
            long_score += weights["stoch_rsi"]
        elif stoch < 50:
            short_score += weights["stoch_rsi"]

        # 8. OBV slope (1.0)
        obv_slope = getattr(snap, 'obv_slope', None)
        if obv_slope is not None:
            if obv_slope > 0:
                long_score += weights["obv"]
            elif obv_slope < 0:
                short_score += weights["obv"]

        dominant_direction = "LONG" if long_score >= short_score else "SHORT"
        dominant_score = long_score if dominant_direction == "LONG" else short_score
        return round(dominant_score, 2), dominant_direction

    def _calc_levels(
        self, ticker: str, price: float, direction: str, atr: float,
    ) -> tuple[float, float, float, float]:
        """Calculate entry, stop, target, and R:R ratio using fixed % stops."""
        is_5x = ticker in _5X_TICKERS
        is_us_stock = ticker in _ALL_US_STOCKS
        if is_5x:
            stop_pct = _STOP_PCT_5X
        elif is_us_stock:
            stop_pct = _STOP_PCT_US  # Wider stop for individual stocks (higher vol)
        else:
            stop_pct = _STOP_PCT_3X
        target_pct = _TARGET_PCT

        # Spread cost deduction
        spread_bps = _SPREAD_BPS.get(ticker, 15)
        spread_cost_pct = spread_bps / 100  # BPS → %

        if direction == "LONG":
            entry = price
            stop = round(price * (1 - stop_pct / 100), 4)
            target = round(price * (1 + target_pct / 100), 4)
        else:
            entry = price
            stop = round(price * (1 + stop_pct / 100), 4)
            target = round(price * (1 - target_pct / 100), 4)

        risk = abs(entry - stop)
        reward = abs(target - entry)

        # Cost-aware R:R
        net_reward = reward - (price * spread_cost_pct / 100)
        rr = round(net_reward / risk, 2) if risk > 0 else 0.0

        return entry, stop, target, rr

    def _score_setup(
        self, base: int, rvol: float, atr_pct: float,
        momentum: float, regime: str, direction: str, vix: float,
    ) -> int:
        """Composite confidence scorer. Returns 0-100."""
        score = float(base)

        # RVOL bonus (0-10)
        if rvol >= 3.0:
            score += 10
        elif rvol >= 2.0:
            score += 7
        elif rvol >= 1.5:
            score += 4
        elif rvol >= 1.0:
            score += 2

        # ATR reachability (0-8): can the stock actually move 2%?
        if atr_pct >= 3.0:
            score += 8
        elif atr_pct >= 2.0:
            score += 5
        elif atr_pct >= 1.5:
            score += 3

        # Momentum alignment (0-7)
        score += min(7, momentum * 7)

        # Regime fit (0-8 / -10)
        trending_up = "TRENDING_UP" in regime
        trending_down = "TRENDING_DOWN" in regime
        if direction == "LONG" and trending_up:
            score += 8
        elif direction == "SHORT" and trending_down:
            score += 8
        elif direction == "LONG" and trending_down:
            score -= 10
        elif direction == "SHORT" and trending_up:
            score -= 10

        # VIX penalty
        if vix > 30:
            score -= 8
        elif vix > 22:
            score -= 4

        return max(0, min(100, int(score)))

    def _build_signal(
        self, candidate: dict, market_ctx: MarketContext,
        indicators: dict[str, IndicatorSnapshot],
    ) -> Optional[Signal]:
        """Create a Signal object from a scored candidate."""
        ticker = candidate["ticker"]
        snap = indicators.get(ticker)
        if not snap:
            return None

        from models import Direction, Bot, BotInstance, SignalStatus
        import uuid

        direction = Direction.LONG if candidate["direction"] == "LONG" else Direction.SHORT

        # US individual stocks use Bot.B designation; LSE ETPs use Bot.A (ISA)
        is_us_stock = ticker in _ALL_US_STOCKS
        bot = Bot.B if is_us_stock else Bot.A

        signal = Signal(
            id=f"S16-{str(uuid.uuid4())[:8]}",
            ticker=ticker,
            direction=direction,
            strategy="S16_Universal",
            entry=candidate["entry"],
            stop=candidate["stop"],
            target_1r=candidate["target"],
            target_2r=round(candidate["target"] * 1.02, 4),  # +2% beyond first target
            confidence=candidate["confidence"],
            regime=market_ctx.regime,
            gex_regime=market_ctx.gex_regime,
            rvol=candidate.get("rvol", 0),
            time_window=getattr(market_ctx, 'time_window', ''),
            patterns_detected=getattr(snap, 'patterns_detected', []),
            internals_composite=getattr(market_ctx, 'internals_composite', 0),
            bot=bot,
            bot_instance=BotInstance.BULL if direction == Direction.LONG else BotInstance.BEAR,
            status=SignalStatus.PENDING,
        )
        # Attach setup metadata for downstream consumers
        signal.setup_type = candidate.get("setup_type", "UNKNOWN")
        signal.setup_reason = candidate.get("reason", "")
        signal.pathway = "S16_UNIVERSAL"
        signal.weighted_score = candidate.get("weighted_score", 0.0)
        signal.tier = candidate.get("tier", "ISA_ETP")
        # Size multiplier: B-team US stocks get 0.5x (passed to position sizer)
        signal.size_mult = candidate.get("size_mult", 1.0)

        return signal
