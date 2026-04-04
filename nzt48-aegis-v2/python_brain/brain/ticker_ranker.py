"""Ticker Priority Ranking Engine — 2-hourly scoring of all tracked tickers.

Produces a ranked list of up to 100 tickers scored 0-100 for the current session.
Results written to config/strategies.toml [ticker_ranking.current] and a human-
readable report in reports/ticker_rankings/.

Scoring factors (weighted):
  1. Spread quality  (25%) — lower spread = higher score; >25 bps = 0
  2. RVOL            (15%) — relative volume vs 20-bar MA; sweet-spot scoring
  3. Regime fit       (20%) — Hurst/ADX alignment with active strategy family
  4. Recent perf      (15%) — win rate + edge ratio from Ouroboros data
  5. Session fit      (15%) — exchange open? preferred list for session window?
  6. Liquidity        (10%) — average daily volume; higher = better

PURE MODULE. No side effects except TOML/report write (caller-initiated).
No threading. No I/O in scoring functions (H07).
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger("ticker_ranker")

# ---------------------------------------------------------------------------
# Constants (H109: no magic numbers)
# ---------------------------------------------------------------------------

# Weight allocation — must sum to 1.0
W_SPREAD = 0.25
W_RVOL = 0.15
W_REGIME = 0.20
W_PERF = 0.15
W_SESSION = 0.15
W_LIQUIDITY = 0.10

# Spread scoring
SPREAD_ZERO_BPS = 25.0       # Spread >= this → score 0
SPREAD_PERFECT_BPS = 2.0     # Spread <= this → score 100

# RVOL scoring
RVOL_MR_OPTIMAL_LOW = 1.0    # Optimal RVOL for mean-reversion (lower bound)
RVOL_MR_OPTIMAL_HIGH = 2.0   # Optimal RVOL for mean-reversion (upper bound)
RVOL_MOM_BOOST = 3.0         # RVOL above this → momentum boost
RVOL_EXTREME = 5.0           # RVOL above this → penalty (jump-diffusion risk)

# Regime fit thresholds (aligned with hurst.py classify_regime)
HURST_TRENDING = 0.55
HURST_MEAN_REVERTING = 0.45
ADX_TRENDING = 25.0

# Performance scoring
PERF_MIN_TRADES = 5           # Minimum trades for reliable WR
PERF_EDGE_RATIO_CAP = 3.0    # Cap edge ratio contribution

# Liquidity scoring
LIQUIDITY_HIGH_ADV = 1_000_000  # High ADV (shares) → score 100
LIQUIDITY_LOW_ADV = 10_000      # Low ADV → score 0

# Output
MAX_RANKED_TICKERS = 100
SCHEMA_VERSION = 1

# Leverage boost config — additive bonus for leveraged/inverse ETPs during LSE hours
LEVERAGE_BOOST_BASE = 30.0      # Base bonus for leveraged/inverse during LSE hours
LEVERAGE_BOOST_PER_MULT = 5.0   # Additional bonus per leverage multiple


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TickerMarketData:
    """Current market snapshot for a single ticker."""
    ticker: str
    last_price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    volume: float = 0.0            # Current bar volume
    avg_daily_volume: float = 0.0  # 20-day ADV
    hurst: float = 0.5
    adx: float = 15.0
    exchange: str = "LSE"          # "LSE", "NYSE", "HKEx", "ASX", etc.


@dataclass(frozen=True)
class TickerPerformance:
    """Ouroboros performance summary for a single ticker."""
    ticker: str
    win_rate: float = 0.5      # 0.0 - 1.0
    edge_ratio: float = 1.0    # avg_win / avg_loss
    trade_count: int = 0
    total_pnl: float = 0.0


@dataclass(frozen=True)
class TickerScore:
    """Final scored ticker with breakdown."""
    ticker: str
    total_score: float
    spread_score: float
    rvol_score: float
    regime_score: float
    perf_score: float
    session_score: float
    liquidity_score: float


@dataclass
class RankingResult:
    """Complete output of a ranking run."""
    rankings: List[TickerScore]
    timestamp: str
    session_window: str
    regime_state: str
    ticker_count: int


# ---------------------------------------------------------------------------
# Portfolio performance loader (defensive — never crashes the ranker)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
_DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
_PORTFOLIO_PERF_FILE = _DATA_DIR / "persistent_memory.json"


def load_portfolio_performance(
    portfolio_path: Optional[Path] = None,
) -> Dict[str, "TickerPerformance"]:
    """Load per-ticker performance data from persistent memory JSON.

    Returns a dict of ticker symbol -> TickerPerformance suitable for passing
    as the ouroboros_perf argument to rank_tickers().

    The ranker MUST produce output even without historical performance data —
    the other 5 scoring factors (spread, RVOL, regime, session, liquidity)
    still work. This function therefore NEVER raises; on any failure it logs
    a warning and returns an empty dict.

    Args:
        portfolio_path: Override path to persistent_memory.json.
            Defaults to $AEGIS_DATA_DIR/persistent_memory.json.

    Returns:
        Dict mapping ticker symbols to TickerPerformance objects.
        Empty dict on any load failure.
    """
    path = portfolio_path or _PORTFOLIO_PERF_FILE
    if not path.exists():
        log.info("Portfolio perf file not found: %s — proceeding with zero perf data", path)
        return {}

    try:
        with open(path) as f:
            portfolio_data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError, IOError) as e:
        log.error("Failed to load portfolio data: %s — continuing with zero perf data", e)
        return {}

    # Extract per-ticker stats from persistent memory format
    ticker_stats = portfolio_data.get("ticker_stats", {})
    if not ticker_stats:
        log.info("No ticker_stats in portfolio data — proceeding with zero perf data")
        return {}

    result: Dict[str, TickerPerformance] = {}
    for symbol, stats in ticker_stats.items():
        try:
            total_trades = int(stats.get("total_trades", 0))
            win_rate = float(stats.get("win_rate", 0.5))
            total_pnl = float(stats.get("total_pnl", 0.0))
            avg_win = float(stats.get("avg_win", 0.0))
            avg_loss = float(stats.get("avg_loss", 0.0))
            edge_ratio = (avg_win / abs(avg_loss)) if avg_loss != 0 else 1.0

            result[symbol] = TickerPerformance(
                ticker=symbol,
                win_rate=win_rate,
                edge_ratio=edge_ratio,
                trade_count=total_trades,
                total_pnl=total_pnl,
            )
        except (ValueError, TypeError, ZeroDivisionError) as e:
            log.warning("Skipping malformed ticker stats for %s: %s", symbol, e)
            continue

    log.info("Loaded portfolio performance for %d tickers from %s", len(result), path)
    return result


# ---------------------------------------------------------------------------
# Scoring functions (pure, no side effects)
# ---------------------------------------------------------------------------

def score_spread(bid: float, ask: float, last_price: float) -> float:
    """Score spread quality. Lower spread = higher score.

    Spread > 25 bps = 0. Spread <= 2 bps = 100.
    Linear interpolation between.

    Returns:
        float in [0.0, 100.0]
    """
    if last_price <= 0.0:
        return 0.0
    if bid <= 0.0 or ask <= 0.0:
        # No bid/ask data — use conservative mid-range score
        return 30.0

    spread_bps = ((ask - bid) / last_price) * 10_000.0

    if spread_bps >= SPREAD_ZERO_BPS:
        return 0.0
    if spread_bps <= SPREAD_PERFECT_BPS:
        return 100.0

    # Linear interpolation: SPREAD_PERFECT_BPS → 100, SPREAD_ZERO_BPS → 0
    return 100.0 * (SPREAD_ZERO_BPS - spread_bps) / (SPREAD_ZERO_BPS - SPREAD_PERFECT_BPS)


def score_rvol(rvol: float, regime_state: str) -> float:
    """Score relative volume. Optimal depends on strategy family.

    Mean-reverting regime: RVOL 1.0-2.0 is ideal (liquid but not panicking).
    Trending regime: RVOL > 3.0 gets a momentum boost.
    Extreme RVOL (>5.0) penalised everywhere (jump-diffusion risk).

    Returns:
        float in [0.0, 100.0]
    """
    if rvol <= 0.0:
        return 0.0

    if rvol > RVOL_EXTREME:
        # Extreme volume — jump-diffusion territory, hard penalty
        # Drops from 60 at 5.0 to 0 at 6.5 (steep enough to be below any moderate score)
        return max(0.0, 60.0 - (rvol - RVOL_EXTREME) * 40.0)

    if regime_state == "mean_reverting":
        # Optimal band: 1.0-2.0
        if RVOL_MR_OPTIMAL_LOW <= rvol <= RVOL_MR_OPTIMAL_HIGH:
            return 100.0
        elif rvol < RVOL_MR_OPTIMAL_LOW:
            return max(0.0, rvol / RVOL_MR_OPTIMAL_LOW * 80.0)
        else:
            # 2.0-5.0: declining score (too much vol for MR)
            return max(0.0, 100.0 - (rvol - RVOL_MR_OPTIMAL_HIGH) / (RVOL_EXTREME - RVOL_MR_OPTIMAL_HIGH) * 80.0)

    elif regime_state == "trending":
        # Momentum loves volume. RVOL > 3.0 is the sweet spot.
        if rvol >= RVOL_MOM_BOOST:
            return 100.0
        elif rvol >= 1.5:
            # 1.5-3.0: ramp up
            return 50.0 + 50.0 * (rvol - 1.5) / (RVOL_MOM_BOOST - 1.5)
        else:
            return max(0.0, rvol / 1.5 * 50.0)

    else:
        # Random regime — moderate volume preferred
        if 0.8 <= rvol <= 2.5:
            return 80.0
        elif rvol < 0.8:
            return max(0.0, rvol / 0.8 * 60.0)
        else:
            return max(0.0, 80.0 - (rvol - 2.5) / (RVOL_EXTREME - 2.5) * 60.0)


def score_regime_fit(
    hurst: float,
    adx: float,
    regime_state: str,
) -> float:
    """Score how well a ticker's microstructure matches the active regime.

    If regime is 'trending', a ticker with Hurst > 0.55 and ADX > 25 scores high.
    If regime is 'mean_reverting', a ticker with Hurst < 0.45 and ADX < 25 scores high.
    If regime is 'random', anything near the boundary scores moderately.

    Returns:
        float in [0.0, 100.0]
    """
    if regime_state == "trending":
        # Hurst contribution (0-50): full marks for H > 0.65, zero for H < 0.40
        hurst_score = _linear_scale(hurst, 0.40, 0.65, 0.0, 50.0)
        # ADX contribution (0-50): full marks for ADX > 35, zero for ADX < 15
        adx_score = _linear_scale(adx, 15.0, 35.0, 0.0, 50.0)
        return hurst_score + adx_score

    elif regime_state == "mean_reverting":
        # Hurst contribution: full marks for H < 0.35, zero for H > 0.55
        hurst_score = _linear_scale(hurst, 0.55, 0.35, 0.0, 50.0)
        # ADX contribution: full marks for ADX < 15, zero for ADX > 30
        adx_score = _linear_scale(adx, 30.0, 15.0, 0.0, 50.0)
        return hurst_score + adx_score

    else:
        # Random regime — mid-range microstructure is ideal
        hurst_mid = 1.0 - abs(hurst - 0.50) / 0.15
        hurst_score = max(0.0, min(50.0, hurst_mid * 50.0))
        adx_mid = 1.0 - abs(adx - 20.0) / 15.0
        adx_score = max(0.0, min(50.0, adx_mid * 50.0))
        return hurst_score + adx_score


def score_performance(
    win_rate: float,
    edge_ratio: float,
    trade_count: int,
) -> float:
    """Score recent Ouroboros performance. Higher WR and edge ratio = better.

    Laplace-smoothed confidence adjustment when trade count is low.

    Returns:
        float in [0.0, 100.0]
    """
    if trade_count == 0:
        # No data — neutral score (Laplace prior: 50%)
        return 50.0

    # Confidence factor: ramp from 0.3 at 1 trade to 1.0 at PERF_MIN_TRADES+
    confidence = min(1.0, 0.3 + 0.7 * trade_count / PERF_MIN_TRADES)

    # Win rate component (0-60): 50% WR = 30, 70% WR = 60
    wr_score = _linear_scale(win_rate, 0.30, 0.75, 0.0, 60.0)

    # Edge ratio component (0-40): 1.0 = 10, 2.0 = 30, 3.0+ = 40
    capped_er = min(edge_ratio, PERF_EDGE_RATIO_CAP)
    er_score = _linear_scale(capped_er, 0.5, PERF_EDGE_RATIO_CAP, 0.0, 40.0)

    raw = wr_score + er_score

    # Blend toward neutral (50) based on confidence
    return 50.0 + (raw - 50.0) * confidence


def score_session_fit(
    exchange: str,
    session_window: str,
    ticker: str,
    preferred_tickers: Optional[Sequence[str]] = None,
) -> float:
    """Score whether the ticker fits the current session.

    Exchange must be open for full score. Preferred tickers get a bonus.

    Args:
        exchange: Ticker's exchange ("LSE", "NYSE", etc.)
        session_window: Current session as "HH:MM-HH:MM" (London local time).
        ticker: Ticker symbol.
        preferred_tickers: Optional list of tickers preferred for this session.

    Returns:
        float in [0.0, 100.0]
    """
    base_score = 0.0

    # Check if exchange is open during this session window
    exchange_open = _is_exchange_open_in_window(exchange, session_window)
    if exchange_open:
        base_score = 70.0
    else:
        # Exchange closed — ticker is not tradeable, score near zero
        # But keep a small score for pre-market analysis
        base_score = 5.0

    # Preferred ticker bonus: +30 if in preferred list
    if preferred_tickers and ticker in preferred_tickers:
        base_score = min(100.0, base_score + 30.0)

    return base_score


def score_liquidity(avg_daily_volume: float) -> float:
    """Score liquidity based on average daily volume.

    Higher ADV = better execution, lower market impact.

    Returns:
        float in [0.0, 100.0]
    """
    if avg_daily_volume <= 0.0:
        return 0.0
    if avg_daily_volume >= LIQUIDITY_HIGH_ADV:
        return 100.0
    if avg_daily_volume <= LIQUIDITY_LOW_ADV:
        return 0.0

    # Logarithmic scaling (10k → 0, 1M → 100)
    log_low = math.log10(LIQUIDITY_LOW_ADV)
    log_high = math.log10(LIQUIDITY_HIGH_ADV)
    log_vol = math.log10(avg_daily_volume)

    return 100.0 * (log_vol - log_low) / (log_high - log_low)


def score_leverage_boost(ticker_data: TickerMarketData, lse_is_open: bool) -> float:
    """Post-score additive bonus for leveraged/inverse ETPs during LSE hours.

    When LSE is open, leveraged/inverse ETPs get a score boost proportional
    to their leverage multiple. This ensures they naturally float to the top
    of the ranking without hardcoding any specific tickers.

    Args:
        ticker_data: Market data for the ticker (must have exchange field).
        lse_is_open: Whether the LSE is currently open.

    Returns:
        float >= 0.0 (additive bonus to composite score).
    """
    if not lse_is_open:
        return 0.0
    # Only boost LSE-listed tickers
    if ticker_data.exchange not in ("LSE", "LSEETF"):
        return 0.0
    # Detect leveraged/inverse from ticker suffix pattern
    # LSE leveraged ETPs: names like QQQ3.L, 3LUS.L, NVD3.L, QQQS.L, 3USS.L etc.
    ticker = ticker_data.ticker
    is_leveraged = ticker.endswith(".L") and any(c.isdigit() for c in ticker.replace(".L", ""))
    if not is_leveraged:
        return 0.0
    # Infer leverage factor from digits in ticker (e.g. QQQ3 → 3, QQQ5 → 5, MU2 → 2)
    digits = [int(c) for c in ticker.replace(".L", "") if c.isdigit()]
    lev = max(digits) if digits else 3
    return LEVERAGE_BOOST_BASE + lev * LEVERAGE_BOOST_PER_MULT


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _linear_scale(
    value: float,
    in_low: float,
    in_high: float,
    out_low: float,
    out_high: float,
) -> float:
    """Linear interpolation with clamping.

    Maps value from [in_low, in_high] to [out_low, out_high].
    Values outside input range are clamped to output range.
    Handles inverted ranges (in_low > in_high) correctly.
    """
    if abs(in_high - in_low) < 1e-12:
        return (out_low + out_high) / 2.0

    # Normalise to [0, 1]
    t = (value - in_low) / (in_high - in_low)
    t = max(0.0, min(1.0, t))

    return out_low + t * (out_high - out_low)


def _is_exchange_open_in_window(exchange: str, session_window: str) -> bool:
    """Check if an exchange is plausibly open during a session window.

    Session window is "HH:MM-HH:MM" in London local time.
    Exchange hours (London local, approximate):
        LSE:  08:00-16:30
        NYSE: 14:30-21:00
        HKEx: 01:30-08:00
        ASX:  00:00-06:00
        TSE:  00:00-06:00 (Tokyo via London)
    """
    exchange_hours: Dict[str, Tuple[int, int]] = {
        "LSE":  (480, 990),     # 08:00-16:30
        "NYSE": (870, 1260),    # 14:30-21:00
        "NASDAQ": (870, 1260),  # 14:30-21:00
        "HKEx": (90, 480),      # 01:30-08:00
        "ASX":  (0, 360),       # 00:00-06:00
        "TSE":  (0, 360),       # 00:00-06:00
    }

    hours = exchange_hours.get(exchange)
    if hours is None:
        return False

    try:
        start_str, end_str = session_window.split("-")
        sh, sm = int(start_str.split(":")[0]), int(start_str.split(":")[1])
        eh, em = int(end_str.split(":")[0]), int(end_str.split(":")[1])
        session_start = sh * 60 + sm
        session_end = eh * 60 + em
    except (ValueError, IndexError):
        return False

    ex_start, ex_end = hours

    # Check for overlap between session window and exchange hours
    return session_start < ex_end and session_end > ex_start


def _get_session_preferred_tickers(session_window: str) -> List[str]:
    """Return preferred tickers for a given session window.

    Based on strategies.toml session_eligible + ticker_preferred mappings.
    """
    try:
        start_str = session_window.split("-")[0]
        sh, sm = int(start_str.split(":")[0]), int(start_str.split(":")[1])
        start_mins = sh * 60 + sm
    except (ValueError, IndexError):
        return []

    # WIRED (Session 27): Load Thompson Sampler top-K ranking from Rust engine.
    # Rust engine writes thompson_top_k.json with top 5 tickers ranked by
    # expected return (Log-Normal Thompson Sampling, Russo et al. 2018).
    # These become "preferred tickers" and get +30 session_fit bonus.
    import json as _json
    from pathlib import Path as _Path

    thompson_path = _Path(os.environ.get("AEGIS_DATA_DIR", "/app/data")) / "thompson_top_k.json"
    preferred = []
    try:
        if thompson_path.exists():
            _age = time.time() - thompson_path.stat().st_mtime
            if _age < 3600:  # Only use if < 1 hour old
                with open(thompson_path) as _f:
                    _data = _json.load(_f)
                # Format: list of {"ticker_id": N, "expected_return": X, "pulls": N}
                if isinstance(_data, list):
                    # Map ticker_ids to symbols via contracts.toml
                    _contracts_path = os.environ.get("AEGIS_CONFIG_DIR", "/app/config") + "/contracts.toml"
                    _id_to_sym = {}
                    try:
                        try:
                            import tomllib as _tomllib
                        except ImportError:
                            import tomli as _tomllib
                        with open(_contracts_path, "rb") as _cf:
                            _cfg = _tomllib.load(_cf)
                        for c in _cfg.get("contracts", []):
                            if c.get("ticker_id") and c.get("symbol"):
                                _id_to_sym[c["ticker_id"]] = c["symbol"]
                    except Exception:
                        pass
                    for entry in _data[:10]:
                        tid = entry.get("ticker_id")
                        sym = _id_to_sym.get(tid)
                        if sym:
                            preferred.append(sym)
                    if preferred:
                        log.info("Thompson preferred tickers: %s", preferred[:5])
    except Exception:
        pass
    return preferred


# ---------------------------------------------------------------------------
# Main ranking engine
# ---------------------------------------------------------------------------

def rank_tickers(
    market_data: List[TickerMarketData],
    session_window: str,
    regime_state: str,
    ouroboros_perf: Dict[str, TickerPerformance],
    spread_cache: Dict[str, float],
    preferred_tickers: Optional[List[str]] = None,
    lse_is_open: bool = False,
) -> RankingResult:
    """Rank all tracked tickers by composite score for the current session.

    Args:
        market_data: Current market snapshot for every tracked ticker.
        session_window: Active session as "HH:MM-HH:MM" (London local).
        regime_state: Current global regime ("trending", "mean_reverting", "random").
        ouroboros_perf: Per-ticker performance from Ouroboros nightly pipeline.
            Keys are ticker symbols, values are TickerPerformance.
        spread_cache: Cached spread in basis points per ticker symbol.
        preferred_tickers: Override preferred tickers for this session.
            If None, derived from session_window.
        lse_is_open: Whether the LSE is currently open. When True, leveraged/
            inverse ETPs on LSE get an additive score boost.

    Returns:
        RankingResult with sorted list of up to MAX_RANKED_TICKERS tickers.
    """
    if preferred_tickers is None:
        preferred_tickers = _get_session_preferred_tickers(session_window)

    scores: List[TickerScore] = []

    for md in market_data:
        # 1. Spread score
        # Use spread_cache if available, else compute from bid/ask
        if md.ticker in spread_cache:
            cached_bps = spread_cache[md.ticker]
            if cached_bps >= SPREAD_ZERO_BPS:
                s_spread = 0.0
            elif cached_bps <= SPREAD_PERFECT_BPS:
                s_spread = 100.0
            else:
                s_spread = 100.0 * (SPREAD_ZERO_BPS - cached_bps) / (SPREAD_ZERO_BPS - SPREAD_PERFECT_BPS)
        else:
            s_spread = score_spread(md.bid, md.ask, md.last_price)

        # 2. RVOL score
        rvol = md.volume / md.avg_daily_volume if md.avg_daily_volume > 0 else 0.0
        s_rvol = score_rvol(rvol, regime_state)

        # 3. Regime fit score
        s_regime = score_regime_fit(md.hurst, md.adx, regime_state)

        # 4. Performance score
        perf = ouroboros_perf.get(md.ticker)
        if perf is not None:
            s_perf = score_performance(perf.win_rate, perf.edge_ratio, perf.trade_count)
        else:
            s_perf = score_performance(0.5, 1.0, 0)

        # 5. Session fit score
        s_session = score_session_fit(
            md.exchange, session_window, md.ticker, preferred_tickers,
        )

        # 6. Liquidity score
        s_liquidity = score_liquidity(md.avg_daily_volume)

        # Composite weighted score
        total = (
            W_SPREAD * s_spread
            + W_RVOL * s_rvol
            + W_REGIME * s_regime
            + W_PERF * s_perf
            + W_SESSION * s_session
            + W_LIQUIDITY * s_liquidity
        )

        # Post-score leverage boost for LSE leveraged/inverse ETPs
        total += score_leverage_boost(md, lse_is_open)

        scores.append(TickerScore(
            ticker=md.ticker,
            total_score=round(total, 2),
            spread_score=round(s_spread, 2),
            rvol_score=round(s_rvol, 2),
            regime_score=round(s_regime, 2),
            perf_score=round(s_perf, 2),
            session_score=round(s_session, 2),
            liquidity_score=round(s_liquidity, 2),
        ))

    # Sort descending by total_score, then by ticker for stability
    scores.sort(key=lambda s: (-s.total_score, s.ticker))

    # Keep top N
    top = scores[:MAX_RANKED_TICKERS]

    return RankingResult(
        rankings=top,
        timestamp=datetime.now(timezone.utc).isoformat() + "Z",
        session_window=session_window,
        regime_state=regime_state,
        ticker_count=len(top),
    )


# ---------------------------------------------------------------------------
# TOML writer — updates config/strategies.toml [ticker_ranking.current]
# ---------------------------------------------------------------------------

def write_ranking_toml(
    strategies_toml_path: Path,
    result: RankingResult,
) -> Path:
    """Update the [ticker_ranking.current] section of strategies.toml.

    Preserves all other content in the file. Replaces only the
    [ticker_ranking.current] block with fresh scores.

    Args:
        strategies_toml_path: Path to config/strategies.toml.
        result: RankingResult from rank_tickers().

    Returns:
        Path to the written file.
    """
    if not strategies_toml_path.exists():
        raise FileNotFoundError(f"strategies.toml not found: {strategies_toml_path}")

    content = strategies_toml_path.read_text()

    # Find and replace [ticker_ranking.current] block
    marker_start = "[ticker_ranking.current]"
    idx = content.find(marker_start)

    if idx == -1:
        # Section not found — append it
        new_block = _build_ranking_block(result)
        content = content.rstrip() + "\n\n" + new_block + "\n"
    else:
        # Find the end of this section (next [section] or EOF)
        after_marker = idx + len(marker_start)
        next_section = content.find("\n[", after_marker)
        if next_section == -1:
            # This is the last section — replace to EOF
            content = content[:idx] + _build_ranking_block(result) + "\n"
        else:
            content = content[:idx] + _build_ranking_block(result) + "\n" + content[next_section + 1:]

    with open(strategies_toml_path, "w") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())

    return strategies_toml_path


def _build_ranking_block(result: RankingResult) -> str:
    """Build the TOML text for [ticker_ranking.current]."""
    lines = [
        f"# Ticker rankings — generated {result.timestamp}",
        f"# Session: {result.session_window} | Regime: {result.regime_state} | Count: {result.ticker_count}",
        "[ticker_ranking.current]",
    ]
    for ts in result.rankings:
        lines.append(f'"{ts.ticker}" = {int(round(ts.total_score))}')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Human-readable report writer
# ---------------------------------------------------------------------------

def write_ranking_report(
    report_dir: Path,
    result: RankingResult,
) -> Path:
    """Write a human-readable ranking report to reports/ticker_rankings/.

    Args:
        report_dir: Directory for reports (e.g., reports/ticker_rankings/).
        result: RankingResult from rank_tickers().

    Returns:
        Path to the written report file.
    """
    report_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    filename = f"ranking_{ts}.txt"
    path = report_dir / filename

    lines = [
        "=" * 78,
        f"  TICKER PRIORITY RANKING — {result.timestamp}",
        f"  Session: {result.session_window} | Regime: {result.regime_state}",
        f"  Tickers ranked: {result.ticker_count}",
        "=" * 78,
        "",
        f"{'Rank':>4}  {'Ticker':<12} {'TOTAL':>6} {'Spread':>7} {'RVOL':>6} "
        f"{'Regime':>7} {'Perf':>6} {'Session':>8} {'Liquid':>7}",
        "-" * 78,
    ]

    for rank, ts_item in enumerate(result.rankings, start=1):
        lines.append(
            f"{rank:>4}  {ts_item.ticker:<12} {ts_item.total_score:>6.1f} "
            f"{ts_item.spread_score:>7.1f} {ts_item.rvol_score:>6.1f} "
            f"{ts_item.regime_score:>7.1f} {ts_item.perf_score:>6.1f} "
            f"{ts_item.session_score:>8.1f} {ts_item.liquidity_score:>7.1f}"
        )

    lines.append("-" * 78)
    lines.append("")

    # Summary statistics
    if result.rankings:
        all_scores = [r.total_score for r in result.rankings]
        lines.append(f"  Top score:    {max(all_scores):.1f}")
        lines.append(f"  Median score: {sorted(all_scores)[len(all_scores)//2]:.1f}")
        lines.append(f"  Bottom score: {min(all_scores):.1f}")

    # Weight breakdown reminder
    lines.extend([
        "",
        "  Weights: Spread=25%, RVOL=15%, Regime=20%, Perf=15%, Session=15%, Liquid=10%",
        "",
        "=" * 78,
    ])

    report_text = "\n".join(lines) + "\n"

    with open(path, "w") as f:
        f.write(report_text)
        f.flush()
        os.fsync(f.fileno())

    return path


# ---------------------------------------------------------------------------
# Convenience: full ranking cycle (rank + write TOML + write report)
# ---------------------------------------------------------------------------

def run_ranking_cycle(
    market_data: List[TickerMarketData],
    session_window: str,
    regime_state: str,
    ouroboros_perf: Dict[str, TickerPerformance],
    spread_cache: Dict[str, float],
    config_dir: Path,
    report_dir: Path,
    preferred_tickers: Optional[List[str]] = None,
    lse_is_open: bool = False,
) -> RankingResult:
    """Run the full ranking cycle: score, write TOML, write report.

    This is the entry point for the 2-hourly cron job.

    Args:
        market_data: Current market data for all tracked tickers.
        session_window: Current session as "HH:MM-HH:MM".
        regime_state: Current regime state.
        ouroboros_perf: Ouroboros per-ticker performance data.
        spread_cache: Spread in bps per ticker from real-time cache.
        config_dir: Path to config/ directory.
        report_dir: Path to reports/ticker_rankings/ directory.
        preferred_tickers: Override preferred tickers for this session.
        lse_is_open: Whether the LSE is currently open.

    Returns:
        RankingResult with the ranked list.
    """
    result = rank_tickers(
        market_data=market_data,
        session_window=session_window,
        regime_state=regime_state,
        ouroboros_perf=ouroboros_perf,
        spread_cache=spread_cache,
        preferred_tickers=preferred_tickers,
        lse_is_open=lse_is_open,
    )

    strategies_path = config_dir / "strategies.toml"
    if strategies_path.exists():
        write_ranking_toml(strategies_path, result)

    write_ranking_report(report_dir, result)

    return result
