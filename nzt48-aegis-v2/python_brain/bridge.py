"""Python Brain Bridge — long-lived subprocess for signal generation.

Protocol: JSON lines over stdin/stdout.
- Receives: {"type":"tick", "ticker_id":0, "last":10.5, "high":10.6, "low":10.4, "bid":10.49, "ask":10.51, "volume":1000, "timestamp_ns":..., ...context...}
- Responds: {"type":"signal", ...} or {"type":"no_signal", "ticker_id":...}
- Shutdown: {"type":"shutdown"}

Accumulates bar history per ticker. Evaluates Vanguard Sniper on each tick.
Runs Autonomous Orchestrator (S17-S20) in parallel on each tick.
Best signal wins (highest confidence). Runs 12-factor Kelly sizing when generated.
"""

import json
import math
import os
import sys
import time
from collections import defaultdict, deque

# Add python_brain to path so brain.* imports work (strategies use `from brain.config import ...`)
sys.path.insert(0, "/app/python_brain")
sys.path.insert(0, "/app")

from brain.strategies.vanguard_sniper import evaluate as vanguard_evaluate
from brain.strategies.apex_scout import evaluate as apex_evaluate
from brain.strategies.autonomous_orchestrator import (
    MarketContext,
    RegimeType,
    StrategyConfig,
    StrategyFamily,
    TickerState,
    TradeIntent,
    detect_session,
    orchestrate,
)
from brain.sizing.kelly_12factor import kelly_12factor
from brain.indicators.volume_analytics import calculate_rvol, volume_divergence, classify_volume_bvc, calculate_vpin
try:
    from brain.indicators.hurst import estimate_hurst, classify_regime
except ImportError:
    def estimate_hurst(prices, max_lag=20):
        return 0.5
    def classify_regime(hurst):
        return "random"
from brain.vwap import VWAPBar, VWAPCalculator
from brain.rsi_ibs import calculate_rsi, calculate_ibs, calculate_sma
from brain.gap_detector import calculate_gap_pct
from python_brain.ouroboros.cost_model import costs as _cost_model
from python_brain.ouroboros.bridge_watchdog import write_heartbeat as _write_heartbeat

try:
    from python_brain.strategies.entry_classifier import EntryClassifier as _EntryClassifier
    _HAS_ENTRY_CLF = True
except ImportError:
    _HAS_ENTRY_CLF = False

MAX_BARS = 500

# Heartbeat: write every 30s so the watchdog knows we're alive
_last_heartbeat_time = 0.0
_HEARTBEAT_INTERVAL = 30.0

bar_history = defaultdict(lambda: deque(maxlen=MAX_BARS))

# P5: Per-strategy signal counter for validation tracking.
_strategy_signal_counts = defaultdict(int)
_strategy_total_confidence = defaultdict(float)

# ── BOOK 217: COST-ADJUSTED P&L TRACKING ──
# Accumulates per-trade cost decomposition for nightly reporting.
_cost_tracking = {
    "total_trades": 0,
    "total_cost_bps": 0.0,
    "total_cost_usd": 0.0,
    "avg_cost_per_trade_bps": 0.0,
    "cost_by_ticker": defaultdict(float),    # ticker -> cumulative bps
    "cost_by_component": defaultdict(float),  # component -> cumulative bps
}

# ══════════════════════════════════════════════════════════════════════════════
# THE COMPOUNDING MACHINE
# ══════════════════════════════════════════════════════════════════════════════
# This is the core autonomous feedback loop. It does 5 things:
#   1. TRACK:  Record every entry/exit per strategy
#   2. SCORE:  Compute rolling WR, PF, Sharpe per strategy
#   3. SIZE:   Allocate Kelly proportional to proven edge (not equal weight)
#   4. KILL:   Disable strategies with Sharpe < -1.0 over 30+ trades
#   5. COMPOUND: Track geometric growth rate and log CAGR daily
#
# The machine doesn't care WHICH strategy wins. It finds winners and scales them.
# Losers get killed automatically. No human intervention needed.
# ══════════════════════════════════════════════════════════════════════════════

_auto_killed_strategies = {"S6_Catalyst"}  # Killed by 730-day backtest: 13% WR, PF 0.01 over 554K trades
_strategy_pnl_history = defaultdict(list)
_strategy_entry_prices = {}
# Book 1: Track entry confidence per position for IC computation on exit
_entry_confidences = {}  # (ticker_id, strategy) → confidence at entry
_cofire_counts = defaultdict(int)
_cofire_total = defaultdict(int)
_COFIRE_WINDOW_NS = 5 * 60 * 1_000_000_000
_recent_strategy_fires = {}

# Edge-proportional allocation weights (updated on every exit)
_strategy_allocation_weights = {}  # strategy → weight [0.0, 1.0]

def _track_strategy_entry(ticker_id, strategy, price):
    """TRACK: Record entry price for P&L computation."""
    _strategy_entry_prices[(ticker_id, strategy)] = price

def _track_strategy_exit(ticker_id, strategy, exit_price):
    """TRACK + SCORE + SIZE + KILL: The compounding machine core loop."""
    key = (ticker_id, strategy)
    entry = _strategy_entry_prices.pop(key, None)
    if entry is None or entry <= 0:
        return

    ret = (exit_price - entry) / entry
    _strategy_pnl_history[strategy].append(ret)
    if len(_strategy_pnl_history[strategy]) > 200:
        _strategy_pnl_history[strategy] = _strategy_pnl_history[strategy][-200:]

    stats = _strategy_live_stats(strategy)

    # KILL: Disable losers
    if stats["n"] >= 30:
        if stats["sharpe"] < -1.0 and strategy not in _auto_killed_strategies:
            _auto_killed_strategies.add(strategy)
            sys.stderr.write(f"COMPOUND_KILL: {strategy} Sharpe={stats['sharpe']} n={stats['n']}\n")
            sys.stderr.flush()
            # Book 208: Also suspend in lifecycle tracking
            try:
                from python_brain.validation.quality_gates import get_lifecycle
                get_lifecycle().suspend(strategy, f"compound_kill: Sharpe={stats['sharpe']:.2f} n={stats['n']}")
            except ImportError:
                pass
        elif stats["sharpe"] > -0.3 and strategy in _auto_killed_strategies:
            _auto_killed_strategies.discard(strategy)
            sys.stderr.write(f"COMPOUND_REVIVE: {strategy} Sharpe={stats['sharpe']}\n")
            sys.stderr.flush()
            # Book 208: Re-promote revived strategy
            try:
                from python_brain.validation.quality_gates import get_lifecycle
                get_lifecycle().promote_to_live(strategy)
            except ImportError:
                pass

    # SIZE: Recompute edge-proportional allocation weights across ALL strategies.
    # Strategies with higher Sharpe get more capital. Negative Sharpe gets zero.
    all_stats = {s: _strategy_live_stats(s) for s in _strategy_pnl_history if _strategy_live_stats(s)["n"] >= 10}
    total_edge = 0.0
    for s, st in all_stats.items():
        edge = max(st["sharpe"], 0.0)  # Only positive edge counts
        all_stats[s]["edge"] = edge
        total_edge += edge

    if total_edge > 0:
        for s, st in all_stats.items():
            _strategy_allocation_weights[s] = st["edge"] / total_edge
    else:
        # No proven edge anywhere — equal weight for data collection
        n_active = len([s for s in all_stats if s not in _auto_killed_strategies])
        if n_active > 0:
            for s in all_stats:
                _strategy_allocation_weights[s] = 1.0 / n_active

    # Log compound state every 10 exits
    total_exits = sum(len(h) for h in _strategy_pnl_history.values())
    if total_exits % 10 == 0:
        sys.stderr.write(f"COMPOUND_STATE: exits={total_exits} killed={list(_auto_killed_strategies)} "
                         f"weights={{{', '.join(f'{s}:{w:.2f}' for s, w in sorted(_strategy_allocation_weights.items()))}}}\n")
        sys.stderr.flush()

# ── BOOK 217: COST DECOMPOSITION CALCULATOR ──

def _calculate_trade_costs(signal, fill_price, shares, ticker):
    """Decompose per-trade costs into spread, commission, slippage, market impact.

    Returns a dict with all cost components in basis points and USD.
    Fail-open: returns zeroed dict on any error so signal flow is never blocked.
    """
    try:
        bid = signal.get("bid", fill_price)
        ask = signal.get("ask", fill_price)
        spread_pct = signal.get("spread_pct", 0.0)

        # 1. Spread cost: half the bid-ask spread in bps
        if bid > 0 and ask > 0 and ask > bid:
            spread_cost_bps = ((ask - bid) / ((ask + bid) / 2.0)) * 10000.0 / 2.0
        elif spread_pct > 0:
            spread_cost_bps = spread_pct * 100.0 / 2.0  # spread_pct is in % → bps, half-spread
        else:
            spread_cost_bps = 1.5  # Conservative default: 1.5 bps half-spread

        # 2. Commission: IBKR tiered (exchange-dependent)
        exchange = _get_exchange_for_symbol(ticker) if ticker else None
        if exchange in ("LSE", "LSEETF"):
            commission_bps = 0.50
        elif exchange in ("TSE", "HKEX", "SGX"):
            commission_bps = 0.80
        else:
            commission_bps = 0.35  # US default (IBKR tiered)

        # 3. Slippage: scaled by VPIN (toxicity) and Amihud (illiquidity)
        vpin = signal.get("vpin", signal.get("d_vpin", 0.0))
        if isinstance(vpin, (int, float)) and vpin < 0:
            vpin = abs(vpin)
        amihud = signal.get("amihud", 0.0)
        if not isinstance(amihud, (int, float)):
            amihud = 0.0
        # Base slippage 1 bps, scales up with toxicity and illiquidity
        slippage_bps = 1.0 + (min(vpin, 1.0) * 3.0) + (min(amihud, 1.0) * 2.0)

        # 4. Market impact: Kyle's lambda * sqrt(shares / ADV) * 10000
        adv = signal.get("adv_shares", 0)
        if not isinstance(adv, (int, float)) or adv <= 0:
            # Fallback: estimate ADV from adv_gbp / price
            adv_gbp = signal.get("adv_gbp", 0)
            if isinstance(adv_gbp, (int, float)) and adv_gbp > 0 and fill_price > 0:
                adv = adv_gbp / fill_price
            else:
                adv = 0
        if adv > 0 and shares > 0:
            participation = shares / adv
            # Kyle's lambda: ~0.1 for liquid stocks, higher for illiquid
            kyles_lambda = 0.1 + min(amihud, 1.0) * 0.4
            market_impact_bps = kyles_lambda * math.sqrt(participation) * 10000.0
        else:
            market_impact_bps = 0.5  # Conservative default when ADV unknown

        # Totals
        total_cost_bps = spread_cost_bps + commission_bps + slippage_bps + market_impact_bps
        notional = fill_price * shares if fill_price > 0 and shares > 0 else 0.0
        total_cost_usd = (total_cost_bps / 10000.0) * notional

        result = {
            "spread_cost_bps": round(spread_cost_bps, 2),
            "commission_bps": round(commission_bps, 2),
            "slippage_bps": round(slippage_bps, 2),
            "market_impact_bps": round(market_impact_bps, 2),
            "total_cost_bps": round(total_cost_bps, 2),
            "total_cost_usd": round(total_cost_usd, 4),
        }

        # Accumulate into tracking
        _cost_tracking["total_trades"] += 1
        _cost_tracking["total_cost_bps"] += total_cost_bps
        _cost_tracking["total_cost_usd"] += total_cost_usd
        n = _cost_tracking["total_trades"]
        _cost_tracking["avg_cost_per_trade_bps"] = _cost_tracking["total_cost_bps"] / n if n > 0 else 0.0
        if ticker:
            _cost_tracking["cost_by_ticker"][ticker] += total_cost_bps
        _cost_tracking["cost_by_component"]["spread"] += spread_cost_bps
        _cost_tracking["cost_by_component"]["commission"] += commission_bps
        _cost_tracking["cost_by_component"]["slippage"] += slippage_bps
        _cost_tracking["cost_by_component"]["market_impact"] += market_impact_bps

        return result
    except Exception as e:
        sys.stderr.write(f"COST_CALC_ERR: {e} (fail-open, returning zeros)\n")
        sys.stderr.flush()
        return {
            "spread_cost_bps": 0.0, "commission_bps": 0.0,
            "slippage_bps": 0.0, "market_impact_bps": 0.0,
            "total_cost_bps": 0.0, "total_cost_usd": 0.0,
        }


def _get_cost_report():
    """Return accumulated cost tracking stats for nightly pipeline.

    Returns a plain dict (JSON-serialisable). Fail-open: returns empty stats on error.
    """
    try:
        n = _cost_tracking["total_trades"]
        return {
            "total_trades": n,
            "total_cost_bps": round(_cost_tracking["total_cost_bps"], 2),
            "total_cost_usd": round(_cost_tracking["total_cost_usd"], 4),
            "avg_cost_per_trade_bps": round(_cost_tracking["avg_cost_per_trade_bps"], 2),
            "cost_by_ticker": dict(_cost_tracking["cost_by_ticker"]),
            "cost_by_component": dict(_cost_tracking["cost_by_component"]),
        }
    except Exception as e:
        sys.stderr.write(f"COST_REPORT_ERR: {e}\n")
        sys.stderr.flush()
        return {
            "total_trades": 0, "total_cost_bps": 0.0, "total_cost_usd": 0.0,
            "avg_cost_per_trade_bps": 0.0, "cost_by_ticker": {}, "cost_by_component": {},
        }


_gemini_weights_loaded = False

# Map Gemini's family codes (F_MOM etc.) to bridge.py strategy names
_GEMINI_FAMILY_MAP = {
    "F_MOM": ["S1_Microstructure", "S3_MacroTrend", "VanguardSniper"],  # Momentum family
    "F_REV": ["S2_Reversion"],                                          # Reversion family
    "F_MAC": ["S4_VolPremium", "S7_TailHedge"],                        # Macro/vol family
    "F_DIS": ["S5_OvernightCarry"],                                     # Discretionary/carry
}

def _load_gemini_strategy_weights():
    """Seed strategy allocation weights from Gemini morning brief (dynamic_weights.toml).

    Only seeds if no live P&L data exists yet (0 exits). Once trades accumulate,
    the edge-proportional calc in _update_allocation_weights() naturally takes over.
    Called once at first tick.
    """
    global _gemini_weights_loaded
    if _gemini_weights_loaded:
        return
    _gemini_weights_loaded = True

    # Only seed if we have no live data (fresh start with 0 trades)
    total_exits = sum(len(h) for h in _strategy_pnl_history.values())
    if total_exits > 0:
        return  # Live data exists — don't override with Gemini

    dw_path = "/app/config/dynamic_weights.toml"
    if not os.path.exists(dw_path):
        return
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(dw_path, "rb") as f:
            data = tomllib.load(f)
        overrides = data.get("strategy_weight_overrides", {})
        if not overrides:
            return

        # Convert family weights → per-strategy weights
        for family_code, weight in overrides.items():
            strategies = _GEMINI_FAMILY_MAP.get(family_code, [])
            for strat in strategies:
                _strategy_allocation_weights[strat] = float(weight)

        sys.stderr.write(f"GEMINI_SEED: strategy weights seeded from dynamic_weights.toml: "
                         f"{dict(_strategy_allocation_weights)}\n")
        sys.stderr.flush()
    except Exception as e:
        sys.stderr.write(f"GEMINI_SEED: failed to load strategy weights (non-fatal): {e}\n")
        sys.stderr.flush()


def get_strategy_weight(strategy):
    """SIZE: Get current allocation weight for a strategy [0.0, 1.0].
    Used by _kelly_for to scale Kelly proportional to proven edge.
    Seeds from Gemini morning brief if no live data exists yet."""
    _load_gemini_strategy_weights()  # Lazy load on first call
    if strategy in _auto_killed_strategies:
        return 0.0
    return _strategy_allocation_weights.get(strategy, 0.5)  # Default 50% until proven


# ── GEMINI MORNING BRIEF: Focus/Avoid ticker consumption ──
_gemini_focus_tickers = set()
_gemini_avoid_tickers = set()
_gemini_brief_loaded = False

def _load_gemini_brief():
    """Load Gemini morning brief focus/avoid tickers for signal overlay."""
    global _gemini_brief_loaded, _gemini_focus_tickers, _gemini_avoid_tickers
    if _gemini_brief_loaded:
        return
    _gemini_brief_loaded = True
    path = "/app/data/gemini/morning_brief_latest.json"
    if not os.path.exists(path):
        return
    try:
        with open(path) as f:
            data = json.load(f)
        _gemini_focus_tickers = set(data.get("focus_tickers", []))
        _gemini_avoid_tickers = set(data.get("avoid_tickers", []))
        if _gemini_focus_tickers or _gemini_avoid_tickers:
            sys.stderr.write(
                f"Bridge: Gemini brief loaded — {len(_gemini_focus_tickers)} focus, "
                f"{len(_gemini_avoid_tickers)} avoid tickers\n"
            )
            sys.stderr.flush()
    except Exception:
        pass


# ── GEMINI DARK HORSE: Unusual mover ticker boost ──
_gemini_dark_horses = set()
_gemini_dh_loaded = False

def _load_gemini_dark_horses():
    """Load Gemini dark horse tickers for confidence boost."""
    global _gemini_dh_loaded, _gemini_dark_horses
    if _gemini_dh_loaded:
        return
    _gemini_dh_loaded = True
    path = "/app/data/gemini/dark_horses.json"
    if not os.path.exists(path):
        return
    try:
        with open(path) as f:
            data = json.load(f)
        _gemini_dark_horses = set(data.get("tickers", []))
    except Exception:
        pass


# ── CLAUDE DAILY PLAN: Focus/Avoid ticker consumption ──
_claude_focus_tickers = set()
_claude_avoid_tickers = set()
_claude_plan_loaded = False

def _load_claude_daily_plan():
    """Load Claude daily plan focus/avoid tickers for signal overlay."""
    global _claude_plan_loaded, _claude_focus_tickers, _claude_avoid_tickers
    if _claude_plan_loaded:
        return
    _claude_plan_loaded = True
    path = "/app/data/claude/daily_plan.json"
    if not os.path.exists(path):
        return
    try:
        with open(path) as f:
            data = json.load(f)
        _claude_focus_tickers = set(data.get("focus_tickers", []))
        _claude_avoid_tickers = set(data.get("avoid_tickers", []))
        if _claude_focus_tickers or _claude_avoid_tickers:
            sys.stderr.write(
                f"Bridge: Claude daily plan loaded — {len(_claude_focus_tickers)} focus, "
                f"{len(_claude_avoid_tickers)} avoid tickers\n"
            )
            sys.stderr.flush()
    except Exception:
        pass


def _strategy_live_sharpe(strategy):
    """Compute rolling Sharpe for a strategy (annualized, assuming 252 trading days)."""
    rets = _strategy_pnl_history.get(strategy, [])
    if len(rets) < 10:
        return 0.0
    mean_r = sum(rets) / len(rets)
    var_r = sum((r - mean_r) ** 2 for r in rets) / len(rets)
    std_r = var_r ** 0.5
    if std_r < 1e-9:
        return 0.0
    # Annualize: assume ~3 trades/day, 252 days
    daily_sharpe = mean_r / std_r
    return daily_sharpe * (252 ** 0.5)

# ── COMPOUNDING METRICS: Daily equity tracking + CAGR computation ──
_daily_equity_snapshots = []  # [(date_str, equity)]
_last_equity_date = ""

def _track_daily_equity(equity):
    """Track daily equity for CAGR computation. Call once per new day."""
    global _last_equity_date
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today != _last_equity_date and equity > 0:
        _last_equity_date = today
        _daily_equity_snapshots.append((today, equity))
        if len(_daily_equity_snapshots) > 365:
            _daily_equity_snapshots[:] = _daily_equity_snapshots[-365:]
        if len(_daily_equity_snapshots) >= 2:
            first_eq = _daily_equity_snapshots[0][1]
            latest_eq = equity
            n_days = len(_daily_equity_snapshots)
            if first_eq > 0 and n_days > 1:
                total_return = latest_eq / first_eq
                daily_geo = total_return ** (1.0 / n_days) - 1.0
                annualized_cagr = (1 + daily_geo) ** 252 - 1.0
                max_eq = max(eq for _, eq in _daily_equity_snapshots)
                drawdown = (max_eq - latest_eq) / max_eq * 100.0 if max_eq > 0 else 0
                sys.stderr.write(
                    f"COMPOUNDING: days={n_days} equity=£{latest_eq:.0f} "
                    f"total_return={total_return:.4f} daily_geo={daily_geo*100:.4f}% "
                    f"CAGR={annualized_cagr*100:.1f}% max_dd={drawdown:.1f}%\n"
                )
                sys.stderr.flush()

                # Book 1: Feed daily return for variance drag computation
                if n_days >= 2:
                    prev_eq = _daily_equity_snapshots[-2][1]
                    if prev_eq > 0:
                        daily_ret = (latest_eq - prev_eq) / prev_eq
                        try:
                            from python_brain.metrics.fundamental_law import get_tracker
                            get_tracker().record_daily_portfolio_return(daily_ret)
                        except ImportError:
                            pass

def _strategy_live_stats(strategy):
    """Get live WR, PF, Sharpe for a strategy."""
    rets = _strategy_pnl_history.get(strategy, [])
    n = len(rets)
    if n < 5:
        return {"n": n, "wr": 0, "pf": 0, "sharpe": 0}
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    wr = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 1e-9
    pf = avg_win / avg_loss if avg_loss > 1e-9 else 99.0
    return {"n": n, "wr": round(wr, 3), "pf": round(pf, 3), "sharpe": round(_strategy_live_sharpe(strategy), 2)}

# Per-ticker VWAP calculators (persist across ticks, reset at session open)
vwap_calculators = defaultdict(VWAPCalculator)

# Sprint 7: Track last trading date per ticker for VWAP session reset.
# When a new date is detected, reset VWAP calculator for that ticker.
_last_vwap_date = {}  # ticker_id → date string "YYYY-MM-DD"

# Ticker ID → symbol mapping (populated from tick messages)
ticker_symbols = {}

# ── BOOK 77: CROSS-MARKET LEAD-LAG BUFFER ──
# Stores recent bar closes per leader symbol for cross-ticker lag detection.
# Key: leader symbol (e.g. "SPY", "QQQ"), Value: deque of last N bar closes.
# Book 136: Expanded from 20 to 120 for rolling 100-bar R² correlation calc.
_leader_bar_closes = {}  # symbol → deque of close prices (max 120)
# Reverse map: follower symbol → list of (leader_symbol, pair_name)
_FOLLOWER_TO_LEADERS = {}
try:
    from python_brain.strategies.lead_lag import LEAD_LAG_PAIRS
    for pair_name, pair_info in LEAD_LAG_PAIRS.items():
        follower = pair_info["follower"]
        leader = pair_info["leader"]
        _FOLLOWER_TO_LEADERS.setdefault(follower, []).append((leader, pair_name))
except ImportError:
    pass

# ── BOOK 136: LEAD-LAG R² RECALIBRATION STATE ──
# Rolling R² per pair, recalibrated every 5 minutes from 100-bar returns.
_lead_lag_r2 = {}  # pair_name → {"r2": float, "status": "active"|"strong"|"disabled", "updated": float}
_lead_lag_r2_last_recalib = 0.0  # monotonic timestamp of last R² recalibration
_LEAD_LAG_R2_INTERVAL = 300.0  # recalibrate every 5 minutes
_LEAD_LAG_R2_DISABLE_THRESH = 0.50  # R² below this → disable pair
_LEAD_LAG_R2_STRONG_THRESH = 0.85  # R² above this → "strong" confidence boost
# Nightly optimal lag overrides: pair_name → optimal_lag_bars (int)
_lead_lag_optimal_lags = {}  # loaded from /app/data/lead_lag_calibration.json
_lead_lag_nightly_last_load = 0.0

# ── BOOK 15: 6-SIGNAL REGIME COMPOSITE ──
# Enhanced regime detection using 6 macro factors instead of VIX-only.
# Scores: 0 = all clear (STEADY), 6 = all firing (CRISIS)
# Each signal contributes 0 or 1 to the composite score.
def _regime_composite_score(msg, ind=None):
    """Compute 6-factor regime stress score (0-6).
    Returns (score, regime_name, details_dict)."""
    score = 0
    details = {}
    # 1. VIX level (primary)
    vix = msg.get("vix", 18)
    if vix > 25:
        score += 1
        details["vix_elevated"] = True
    if vix > 35:
        score += 1  # Double-count extreme VIX
        details["vix_extreme"] = True
    # 2. Credit spreads (HY OAS)
    credit_spread = msg.get("credit_spread_bps", 120)
    if credit_spread > 200:
        score += 1
        details["credit_widening"] = True
    # 3. Market breadth
    breadth = msg.get("pct_above_200dma", 55)
    if breadth < 40:
        score += 1
        details["breadth_declining"] = True
    # 4. DXY strength (dollar stress)
    dxy = msg.get("dxy", 100)
    if dxy > 108:
        score += 1
        details["dollar_stress"] = True
    # 5. Yield curve (2s10s spread) — inversion signals recession
    yield_spread = msg.get("yield_2s10s", 0.5)
    if yield_spread < 0:
        score += 1
        details["curve_inverted"] = True
    # 6. Equity momentum — SPX below 50-day SMA
    spx_vs_sma50 = msg.get("spx_vs_sma50_pct", 1.0)
    if spx_vs_sma50 < -3.0:
        score += 1
        details["equity_momentum_weak"] = True

    # Map score to regime
    if score >= 5:
        regime = "CRISIS"
    elif score >= 3:
        regime = "WOI"
    elif score >= 2:
        regime = "INFLATION"
    else:
        regime = "STEADY"
    return score, regime, details

# Tick counters for diagnostic logging (per-ticker)
_tick_counts = {}

# Per-ticker signal cooldown: prevents signal spam.
# FIXED (Sprint 5, T-08): Reduced from 300 (25min) to 60 (5min).
# Sprint 6: Now loaded from config.toml [hardening.ticks] if available.
def _load_cooldown_from_config():
    """Load signal cooldown ticks from config.toml, default 60."""
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        cfg_path = "/app/config/config.toml"
        if not os.path.exists(cfg_path):
            return 60
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)
        # Cooldown in ticks (each tick = 5s). Default 60 = 5 minutes.
        return data.get("hardening", {}).get("ticks", {}).get("signal_cooldown_ticks", 60)
    except Exception:
        return 60

# SIM_MODE: Load cooldown as 0 to completely disable per-ticker signal suppression.
_SIM_MODE = os.environ.get("AEGIS_SIM_MODE", "0") == "1"
SIGNAL_COOLDOWN_TICKS = 0 if _SIM_MODE else _load_cooldown_from_config()
_last_signal_tick = {}  # ticker_id → tick count when last signal was emitted

# Startup audit log: strategy enforcement status
sys.stderr.write("BRIDGE_STARTUP: Strategy enforcement: TypeA-F=QUARANTINED, VS/AS/S1-S7=LIVE\n")
sys.stderr.write(f"BRIDGE_STARTUP: SIM_MODE={_SIM_MODE}, COOLDOWN_TICKS={SIGNAL_COOLDOWN_TICKS}\n")
sys.stderr.flush()

# ============================================================================
# T-01 (Sprint 5): Exchange cutoff blackout enforcement in Python.
# Prevents wasting compute on signal generation after the per-exchange entry
# cutoff. The Rust risk_arbiter already blocks entries, but generating signals
# we know will be rejected wastes 100ms+ of indicator + strategy evaluation.
# ============================================================================
_exchange_cutoffs_loaded = False
_exchange_cutoffs = {}  # exchange_key → (cutoff_hour, cutoff_minute)

# Map contracts.toml exchange names → config.toml [timing.exchange_cutoffs] keys
_EXCHANGE_KEY_MAP = {
    "LSEETF": "LSE",
    "LSE": "LSE",
    "SMART": "US",
    "HKEX": "HKEX",
    "TSE": "TSE",
    "XETRA": "XETRA",
    "EURONEXT": "EURONEXT",
    "SGX": "SGX",
    "AEB": "EURONEXT",   # Amsterdam → EURONEXT cutoff
    "HEX": "EURONEXT",   # Helsinki → EURONEXT cutoff
    "XMAD": "EURONEXT",  # Madrid → EURONEXT cutoff
}

# Infer exchange from symbol suffix (fallback when contracts.toml unavailable)
_SUFFIX_TO_EXCHANGE = {
    ".L": "LSE",
    ".T": "TSE",
    ".HK": "HKEX",
    ".SI": "SGX",
}

# Symbol → exchange mapping (built lazily from contracts.toml)
_symbol_exchange_map_loaded = False
_symbol_exchange_map = {}  # symbol → exchange key (e.g. "LSE", "US")
_symbol_raw_exchange_map = {}  # S6: symbol → raw exchange from contracts.toml (e.g. "LSEETF")

# DST-aware UTC offsets per exchange. Uses zoneinfo for correct BST/CET/EDT.
# Fallback to static offsets if zoneinfo unavailable.
_EXCHANGE_TIMEZONE = {
    "LSE": "Europe/London",       # GMT/BST (+0/+1)
    "LSEETF": "Europe/London",
    "US": "America/New_York",     # EST/EDT (-5/-4)
    "HKEX": "Asia/Hong_Kong",     # HKT (+8, no DST)
    "TSE": "Asia/Tokyo",          # JST (+9, no DST)
    "XETRA": "Europe/Berlin",     # CET/CEST (+1/+2)
    "EURONEXT": "Europe/Paris",   # CET/CEST (+1/+2)
    "SGX": "Asia/Singapore",      # SGT (+8, no DST)
}
_EXCHANGE_UTC_OFFSET_STATIC = {
    "LSE": 0, "LSEETF": 0, "US": -5, "HKEX": 8,
    "TSE": 9, "XETRA": 1, "EURONEXT": 1, "SGX": 8,
}
try:
    from zoneinfo import ZoneInfo as _ZoneInfo
    _HAS_ZONEINFO = True
except ImportError:
    _HAS_ZONEINFO = False


def _exchange_utc_offset(exchange, utc_dt=None):
    """Return current UTC offset in hours for an exchange, DST-aware.
    Falls back to static offset if zoneinfo unavailable."""
    if _HAS_ZONEINFO and exchange in _EXCHANGE_TIMEZONE:
        from datetime import datetime, timezone
        if utc_dt is None:
            utc_dt = datetime.now(timezone.utc)
        tz = _ZoneInfo(_EXCHANGE_TIMEZONE[exchange])
        local_dt = utc_dt.astimezone(tz)
        return local_dt.utcoffset().total_seconds() / 3600.0
    return _EXCHANGE_UTC_OFFSET_STATIC.get(exchange, 0)


# Legacy alias for backward compatibility
_EXCHANGE_UTC_OFFSET = _EXCHANGE_UTC_OFFSET_STATIC


def _load_exchange_cutoffs():
    """Load per-exchange entry cutoffs from config.toml [timing.exchange_cutoffs]."""
    global _exchange_cutoffs_loaded, _exchange_cutoffs
    if _exchange_cutoffs_loaded:
        return _exchange_cutoffs
    _exchange_cutoffs_loaded = True
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        cfg_path = "/app/config/config.toml"
        if not os.path.exists(cfg_path):
            return _exchange_cutoffs
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)
        cutoffs = data.get("timing", {}).get("exchange_cutoffs", {})
        for exch, time_str in cutoffs.items():
            parts = time_str.split(":")
            if len(parts) == 2:
                _exchange_cutoffs[exch] = (int(parts[0]), int(parts[1]))
        if _exchange_cutoffs:
            sys.stderr.write(
                "Bridge: loaded exchange cutoffs: {}\n".format(
                    ", ".join(f"{k}={v[0]:02d}:{v[1]:02d}" for k, v in sorted(_exchange_cutoffs.items()))
                )
            )
            sys.stderr.flush()
    except Exception as e:
        sys.stderr.write(f"Bridge: failed to load exchange cutoffs: {e}\n")
        sys.stderr.flush()
    return _exchange_cutoffs


def _load_symbol_exchange_map():
    """Build symbol -> exchange key mapping from contracts.toml (cached)."""
    global _symbol_exchange_map_loaded, _symbol_exchange_map, _symbol_raw_exchange_map
    if _symbol_exchange_map_loaded:
        return _symbol_exchange_map
    _symbol_exchange_map_loaded = True
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        contracts_path = "/app/config/contracts.toml"
        if not os.path.exists(contracts_path):
            return _symbol_exchange_map
        with open(contracts_path, "rb") as f:
            data = tomllib.load(f)
        for c in data.get("contracts", []):
            sym = c.get("symbol", "")
            exch = c.get("exchange", "")
            if sym and exch:
                key = _EXCHANGE_KEY_MAP.get(exch, exch)
                _symbol_exchange_map[sym] = key
                _symbol_raw_exchange_map[sym] = exch  # S6: preserve raw exchange for gating
        if _symbol_exchange_map:
            sys.stderr.write(
                "Bridge: loaded symbol->exchange map ({} symbols)\n".format(len(_symbol_exchange_map))
            )
            sys.stderr.flush()
    except Exception as e:
        sys.stderr.write(f"Bridge: failed to load symbol->exchange map: {e}\n")
        sys.stderr.flush()
    return _symbol_exchange_map


def _get_exchange_for_symbol(symbol):
    """Determine exchange key for a symbol. Returns None if unknown."""
    # Try contracts.toml mapping first (authoritative)
    exch_map = _load_symbol_exchange_map()
    if symbol in exch_map:
        return exch_map[symbol]
    # Fallback: infer from suffix
    for suffix, exch_key in _SUFFIX_TO_EXCHANGE.items():
        if symbol.endswith(suffix):
            return exch_key
    return None


def _is_in_blackout(ticker_id, timestamp_ns):
    """Check if the current time is past the exchange entry cutoff for this ticker.

    Returns True if in blackout (signal should be suppressed), False otherwise.
    Fail-open: returns False if exchange or cutoff cannot be determined.
    """
    symbol = ticker_symbols.get(ticker_id, "")
    if not symbol:
        return False  # No symbol -> can't determine exchange -> fail-open

    exchange = _get_exchange_for_symbol(symbol)
    if not exchange:
        return False  # Unknown exchange -> fail-open

    cutoffs = _load_exchange_cutoffs()
    cutoff = cutoffs.get(exchange)
    if not cutoff:
        return False  # No cutoff configured -> fail-open

    cutoff_hour, cutoff_minute = cutoff

    if timestamp_ns <= 0:
        return False  # No timestamp -> can't check -> fail-open

    from datetime import datetime, timezone, timedelta
    utc_dt = datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=timezone.utc)

    offset_hours = _exchange_utc_offset(exchange, utc_dt)
    local_dt = utc_dt + timedelta(hours=offset_hours)

    # Past cutoff = blackout
    if local_dt.hour > cutoff_hour or (local_dt.hour == cutoff_hour and local_dt.minute >= cutoff_minute):
        return True

    return False

# Blackout veto logging dedup — avoid logging every tick (once per ticker per day)
_blackout_warned: set = set()
_blackout_warned_date: str = ""

# 5-minute bar aggregation cache — avoid recomputing OHLCV bars on every tick
# when no new complete 5-min bar has formed.
# ticker_id → (last_n_5min_bars, cached_bars_5m_list)
_bar_cache: dict = {}

# Blacklist logging dedup — tracks which symbols have already been logged
# as blacklisted (reset daily to catch config changes across sessions).
_blacklist_warned: set = set()
_blacklist_warned_date: str = ""

# ── BOOK 40: SINGLE-STOCK 3x ETP → UNDERLYING MAPPING (earnings protection) ──
# Key: LSE ETP symbol → value: US underlying ticker (for earnings lookup)
# 3x single-stock ETPs amplify earnings gaps 3.5-5x. Must exit before close on earnings day.
_ETP_UNDERLYING_MAP = {
    "TSL3.L": "TSLA", "3LTS.L": "TSLA", "3STS.L": "TSLA",
    "NVD3.L": "NVDA", "3LNV.L": "NVDA", "3SNV.L": "NVDA",
    "AMD3.L": "AMD",  "3LAM.L": "AMD",  "3SAM.L": "AMD",
    "APL3.L": "AAPL", "3LAP.L": "AAPL", "3SAP.L": "AAPL",
    "MSF3.L": "MSFT", "3LMS.L": "MSFT", "3SMS.L": "MSFT",
    "GOO3.L": "GOOGL", "3LGO.L": "GOOGL",
    "AMZ3.L": "AMZN", "3LAZ.L": "AMZN",
    "MET3.L": "META", "3LME.L": "META",
    "TSM3.L": "TSM",
    "GPT3.L": "NVDA",  # GPT-themed but tracks NVDA
    "MU2.L": "MU",     # 2x Micron
}
# Earnings dates cache: underlying → "YYYY-MM-DD" next earnings date
# Updated by nightly pipeline (yfinance query). Loaded from /app/data/earnings_dates.json
_earnings_dates: dict = {}
_earnings_dates_loaded: bool = False

def _load_earnings_dates():
    """Load earnings dates from nightly-generated JSON."""
    global _earnings_dates, _earnings_dates_loaded
    if _earnings_dates_loaded:
        return _earnings_dates
    try:
        _path = os.path.join(os.environ.get("AEGIS_DATA_DIR", "/app/data"), "earnings_dates.json")
        if os.path.exists(_path):
            with open(_path, 'r') as f:
                _earnings_dates = json.load(f)
        _earnings_dates_loaded = True
    except Exception:
        _earnings_dates_loaded = True  # Don't retry on error
    return _earnings_dates

# ============================================================================
# Gate veto logging — tracks WHY signals were suppressed and what WOULD have happened
# Logged to stderr as GATE_VETO lines, also written to /app/data/gate_vetoes.ndjson
# for Ouroboros missed-winner analysis.
# ============================================================================
_gate_veto_log_path = "/app/data/gate_vetoes.ndjson"
_gate_veto_counts = {}  # (ticker_id, gate_name) → count (rate limit logging)

def _log_gate_veto(ticker_id, gate_name, price, indicators, reason_detail=""):
    """Log a gate veto with full indicator context for missed-winner analysis."""
    key = (ticker_id, gate_name)
    _gate_veto_counts[key] = _gate_veto_counts.get(key, 0) + 1
    count = _gate_veto_counts[key]

    record = {
        "ts": time.time(),
        "ticker_id": ticker_id,
        "symbol": ticker_symbols.get(ticker_id, "?"),
        "gate": gate_name,
        "price": round(price, 4),
        "detail": reason_detail,
        "indicators": {k: round(v, 4) if isinstance(v, float) else v for k, v in indicators.items()},
    }

    try:
        with open(_gate_veto_log_path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except (ValueError, KeyError, TypeError, IOError, json.JSONDecodeError, OSError) as e:
        # Log file write failure is non-fatal — default stance: VETO still applies.
        # Only log the first error to avoid spam.
        if not getattr(_log_gate_veto, '_err_logged', False):
            sys.stderr.write(f"Bridge: gate veto log write failed: {e}\n")
            sys.stderr.flush()
            _log_gate_veto._err_logged = True

    if count <= 3 or count % 100 == 0:
        sym = ticker_symbols.get(ticker_id, str(ticker_id))
        sys.stderr.write(
            "GATE_VETO: {} gate={} price={:.4f} {} "
            "hurst={} adx={} rvol={} vol_slope={} "
            "(#{})\n".format(
                sym, gate_name, price, reason_detail,
                indicators.get("hurst", "?"), indicators.get("adx", "?"),
                indicators.get("rvol", "?"), indicators.get("vol_slope", "?"),
                count))
        sys.stderr.flush()

# ============================================================================
# BOOK 42: CONDITIONAL HEDGING — Dynamic Hedge Activation & Unwinding
# ============================================================================
# Reduces hedging costs 60-80% vs permanent hedging by activating only when
# warning signals fire. VIX backwardation preceded 85-90% of >15% drawdowns.
#
# Warning Signal Thresholds:
#   - VIX backwardation: VIX term structure inverted (spot > 1-month future)
#   - Credit widening: spreads > 30bp above 20-day MA (or proxy via HYG/TLT)
#   - Breadth decline: <40% of assets above 200-day MA (25% = CRISIS)
#
# Hedge Activation: 2nd signal OR 2 consecutive closes beyond threshold
# Hedge Unwinding: ALL signals must clear for 5 consecutive days
# ============================================================================

_hedge_state = {
    "status": "INACTIVE",  # INACTIVE, MONITORING, HEDGE_ACTIVE, KILLING
    "activation_count": 0,  # Count of active warning signals (0-3)
    "signal_fire_times": {},  # signal_name → timestamp last fired
    "consecutive_days_clear": 0,  # Days with all signals cleared (0-5)
    "last_clear_date": None,  # Date of last signal clear check
    "current_allocation": {  # Current hedge positions
        "inverse_etp_pct": 0.0,  # % of portfolio in inverse ETPs
        "vix_etp_pct": 0.0,  # % of portfolio in VIX ETPs
        "cash_raised_pct": 0.0,  # % of portfolio raised to cash
    },
}

_hedge_signal_history = deque(maxlen=20)  # Recent (timestamp, signal_name, value) tuples

# VIX term structure tracking (last N closes)
_vix_spot_history = deque(maxlen=30)  # Recent VIX spot close prices
_vix_1m_future_history = deque(maxlen=30)  # Recent VIX 1-month future closes

# Credit spread tracking (bond ETF proxy)
_credit_spread_history = deque(maxlen=30)  # Recent HYG-TLT spreads or estimated spreads
_credit_spread_ma20 = 0.0  # 20-day MA of credit spreads

# Breadth tracking (% of assets above 200-day MA)
_breadth_tracker = {
    "total_symbols": 0,
    "above_200ma": 0,
    "last_breadth_pct": 50.0,  # Start neutral
    "last_check_time": 0,
}

def _load_hedge_state():
    """Load persisted hedge state from /app/data/hedge_state.json."""
    global _hedge_state
    try:
        state_path = "/app/data/hedge_state.json"
        if os.path.exists(state_path):
            with open(state_path) as f:
                persisted = json.load(f)
            _hedge_state.update(persisted)
            sys.stderr.write(
                f"HEDGE_LOAD: status={_hedge_state['status']} "
                f"activation_count={_hedge_state['activation_count']}\n"
            )
            sys.stderr.flush()
    except Exception as e:
        sys.stderr.write(f"HEDGE_LOAD: failed to load state: {e} (non-fatal)\n")
        sys.stderr.flush()

def _save_hedge_state():
    """Persist hedge state to /app/data/hedge_state.json."""
    try:
        state_path = "/app/data/hedge_state.json"
        with open(state_path, "w") as f:
            json.dump(_hedge_state, f, indent=2)
    except Exception as e:
        sys.stderr.write(f"HEDGE_SAVE: failed to save state: {e}\n")
        sys.stderr.flush()

def _monitor_hedge_signals(msg, ind):
    """
    Monitor 3 warning signals for hedge activation.
    Called once per tick to track signal state.

    Returns:
        (vix_backwardation, credit_warning, breadth_declining) — boolean tuple
    """
    timestamp_ns = msg.get("timestamp_ns", 0)
    timestamp_s = timestamp_ns / 1_000_000_000 if timestamp_ns > 0 else time.time()

    # ── Signal 1: VIX Backwardation (VIX spot > VIX 1m future) ──
    vix_spot = msg.get("vix_spot", None)
    vix_1m = msg.get("vix_1m_future", None)
    vix_backwardation = False
    if vix_spot is not None and vix_1m is not None:
        if vix_spot > 0:
            _vix_spot_history.append(vix_spot)
        if vix_1m > 0:
            _vix_1m_future_history.append(vix_1m)
        # Backwardation: spot > future (flat or inverted term structure)
        vix_backwardation = vix_spot > vix_1m * 1.02  # 2% margin to avoid noise
        if vix_backwardation:
            _hedge_signal_history.append((timestamp_s, "VIX_BACKWARDATION", vix_spot - vix_1m))

    # ── Signal 2: Credit Spread Widening ──
    # Proxy: Use HYG (high-yield bonds) vs TLT (long-term treasury) spread
    # Or estimate from provided credit spread if available
    credit_warning = False
    hyg_price = msg.get("hyg_price", None)
    tlt_price = msg.get("tlt_price", None)
    credit_spread = msg.get("credit_spread_bp", None)

    if credit_spread is not None:
        _credit_spread_history.append(credit_spread)
        if len(_credit_spread_history) >= 20:
            _credit_spread_ma20 = sum(_credit_spread_history) / len(_credit_spread_history)
            # Warning: spreads > 30bp above 20-day MA
            credit_warning = credit_spread > _credit_spread_ma20 + 30
            if credit_warning:
                _hedge_signal_history.append((timestamp_s, "CREDIT_WIDENING", credit_spread - _credit_spread_ma20))

    # ── Signal 3: Market Breadth Declining ──
    # < 40% of assets above 200-day MA = warning
    # < 25% of assets above 200-day MA = CRISIS (triggers kill switch)
    breadth_pct = msg.get("breadth_200ma_pct", None)
    breadth_declining = False
    breadth_crisis = False
    if breadth_pct is not None:
        _breadth_tracker["last_breadth_pct"] = breadth_pct
        breadth_declining = breadth_pct < 40
        breadth_crisis = breadth_pct < 25  # Triggers max hedge + raise cash 10%
        if breadth_declining or breadth_crisis:
            _hedge_signal_history.append((timestamp_s, "BREADTH_DECLINING", breadth_pct))

    return vix_backwardation, credit_warning, breadth_declining, breadth_crisis

def _bayesian_hedge_probability(vix_backwardation, credit_warning, breadth_decline):
    """
    Bayesian posterior probability of drawdown >15% given active signals (Book 42).

    Combines independent posterior estimates via naive Bayes in log-odds space.
    Each active signal contributes its odds ratio (OR) relative to the base rate.
    Inactive signals do not update the posterior (conservative: absence of a
    warning signal does not reduce risk below the prior).

    Conditional probabilities (Book 42 Table 4.2):
        P(drawdown>15% | vix_backwardation)  = 0.85
        P(drawdown>15% | credit_warning)     = 0.60
        P(drawdown>15% | breadth_decline)    = 0.45
        P(drawdown>15% | base/unconditional) = 0.08

    Combination formula (log-odds space):
        log_odds(posterior) = log_odds(prior)
                            + sum_active[ log(OR_i) ]
        where OR_i = [P(dd|signal_i)/(1-P(dd|signal_i))] / [prior/(1-prior)]

    Returns:
        float in [0.0, 1.0] -- posterior probability of significant drawdown.
        Returns exactly prior (0.08) when no signals are active.
    """
    prior = 0.08  # P(drawdown > 15%) unconditional
    prior_odds = prior / (1.0 - prior)

    # (active_flag, P(drawdown>15% | signal))
    signals = [
        (vix_backwardation, 0.85),
        (credit_warning,    0.60),
        (breadth_decline,   0.45),
    ]

    # Start at prior log-odds
    log_odds = math.log(prior_odds)

    for active, p_dd_given_signal in signals:
        if active:
            # Odds ratio: how much does this signal shift the odds of drawdown?
            signal_odds = p_dd_given_signal / (1.0 - p_dd_given_signal)
            odds_ratio = signal_odds / prior_odds
            log_odds += math.log(odds_ratio)
        # Inactive signals: no update (conservative — absence doesn't reduce risk)

    # Convert log-odds back to probability
    posterior = 1.0 / (1.0 + math.exp(-log_odds))

    # Clamp to [0, 1] (defensive)
    posterior = max(0.0, min(1.0, posterior))

    return posterior

def _check_hedge_activation_rules(vix_bw, credit_warn, breadth_decline, breadth_crisis):
    """
    Check if hedge should be activated based on signal counts and rules.

    Activation Rules (Book 42):
      - 2nd signal fires: activate hedge
      - 2 consecutive closes beyond threshold: activate hedge
      - All 3 signals firing: kill switch (max hedge + 50%+ cash)

    Returns:
        (should_activate, num_signals_active, kill_switch)
    """
    num_active = sum([vix_bw, credit_warn, breadth_decline])
    kill_switch = breadth_crisis or num_active == 3

    # Activation threshold: 2+ signals
    should_activate = num_active >= 2 or kill_switch

    return should_activate, num_active, kill_switch

def _apply_conditional_hedge(msg, all_signals):
    """
    Apply conditional hedging overlay to signal generation (Book 42 enhanced).

    Uses Bayesian posterior probability to continuously scale hedge allocation
    instead of discrete tiers. Cost-benefit filter ensures hedging only when
    expected drawdown * probability exceeds hedge cost.

    Graduated Response Curve:
      - probability < cost_threshold: no hedge (cost exceeds expected benefit)
      - probability 0.15-0.40: light hedge (scaled inverse + VIX)
      - probability 0.40-0.70: moderate hedge (scaled inverse + VIX + cash)
      - probability 0.70+: heavy hedge (approaching max allocations)
      - kill_switch (all 3 signals OR breadth_crisis): hard override to max

    Max Allocations (caps for continuous scaling):
      - Inverse ETP: 8%
      - VIX ETP: 5%
      - Cash raise: 50%

    Cost-Benefit Filter:
      - Only hedge when: posterior * expected_drawdown_pct > hedge_cost_bps
      - Default hedge_cost_bps = 15 (spread + slippage + decay on inverse/VIX ETPs)
      - Expected drawdown = 15% (the conditional event we model)
    """
    global _hedge_state

    # Cost-benefit parameters (Book 42, Table 4.5)
    HEDGE_COST_BPS = 15        # bps: spread + slippage + VIX contango decay
    EXPECTED_DRAWDOWN_PCT = 15  # % drawdown we're hedging against
    # Cost threshold: minimum posterior to justify hedging
    # posterior * 1500bps > 15bps => posterior > 0.01
    # But we want meaningful protection, so effective floor ~ 0.10 from the curve
    COST_THRESHOLD = HEDGE_COST_BPS / (EXPECTED_DRAWDOWN_PCT * 100)  # 0.01

    # Max allocation caps
    MAX_INVERSE_PCT = 8.0
    MAX_VIX_PCT = 5.0
    MAX_CASH_PCT = 50.0

    # Load initial state once per session
    if _hedge_state["status"] == "INACTIVE":
        _load_hedge_state()

    vix_bw, credit_warn, breadth_dec, breadth_crisis = _monitor_hedge_signals(msg, {})
    should_activate, num_signals, kill_switch = _check_hedge_activation_rules(
        vix_bw, credit_warn, breadth_dec, breadth_crisis
    )

    # ── Bayesian posterior probability ──
    posterior = _bayesian_hedge_probability(vix_bw, credit_warn, breadth_dec)

    # Determine hedge allocation
    inverse_pct, vix_pct, cash_pct = 0.0, 0.0, 0.0

    if kill_switch:
        # Hard override: all 3 signals OR breadth crisis → max hedge + 50%+ cash
        _hedge_state["status"] = "KILLING"
        inverse_pct, vix_pct, cash_pct = MAX_INVERSE_PCT, MAX_VIX_PCT, MAX_CASH_PCT
        sys.stderr.write(
            f"HEDGE_ACTIVATION: kill_switch triggered (posterior={posterior:.3f}, "
            f"all signals firing)\n"
        )
        sys.stderr.flush()
    else:
        # ── Cost-benefit filter ──
        # Require at least 1 signal active AND expected loss > hedge cost
        expected_loss_bps = posterior * EXPECTED_DRAWDOWN_PCT * 100  # in bps
        no_hedge = (num_signals == 0) or (expected_loss_bps <= HEDGE_COST_BPS)

        if no_hedge:
            # No signals firing or hedge cost exceeds expected benefit
            if _hedge_state["status"] not in ("INACTIVE",):
                if num_signals == 0:
                    reason = "no signals active"
                else:
                    reason = f"expected_loss={expected_loss_bps:.1f}bps <= cost={HEDGE_COST_BPS}bps"
                sys.stderr.write(
                    f"HEDGE_COST_FILTER: posterior={posterior:.3f} {reason} "
                    f"→ no hedge\n"
                )
                sys.stderr.flush()

            # Check if should unwind existing hedge
            if _hedge_state["status"] != "INACTIVE":
                from datetime import datetime, timezone
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if today != _hedge_state.get("last_clear_date"):
                    _hedge_state["last_clear_date"] = today
                    _hedge_state["consecutive_days_clear"] += 1

                if _hedge_state["consecutive_days_clear"] >= 5:
                    sys.stderr.write(
                        f"HEDGE_UNWINDING: all signals clear for "
                        f"{_hedge_state['consecutive_days_clear']} days\n"
                    )
                    sys.stderr.flush()
                    _hedge_state["status"] = "INACTIVE"
                    _hedge_state["activation_count"] = 0
                    _hedge_state["consecutive_days_clear"] = 0
                    _hedge_state["current_allocation"] = {
                        "inverse_etp_pct": 0, "vix_etp_pct": 0, "cash_raised_pct": 0
                    }
                    _save_hedge_state()
                    return []
        else:
            # ── Graduated response curve (continuous scaling) ──
            # Scale allocations by posterior probability using a sigmoid-like curve
            # that maps [COST_THRESHOLD, 1.0] → [0.0, 1.0] smoothly.
            # Uses a power curve: scale = ((p - threshold) / (1 - threshold)) ^ 0.7
            # Exponent < 1 makes it concave (ramps up faster at lower probabilities
            # for earlier protection, saturates near max).
            _hedge_state["consecutive_days_clear"] = 0  # Reset clear counter
            norm_p = (posterior - COST_THRESHOLD) / (1.0 - COST_THRESHOLD)
            norm_p = max(0.0, min(1.0, norm_p))
            scale = norm_p ** 0.7  # Concave curve: faster ramp, gradual saturation

            inverse_pct = scale * MAX_INVERSE_PCT
            vix_pct = scale * MAX_VIX_PCT
            # Cash raise only kicks in at higher probabilities (>0.40 posterior)
            if posterior > 0.40:
                cash_scale = ((posterior - 0.40) / 0.60) ** 0.8
                cash_pct = cash_scale * MAX_CASH_PCT
            else:
                cash_pct = 0.0

            _hedge_state["status"] = "HEDGE_ACTIVE"
            _hedge_state["activation_count"] = num_signals

            sys.stderr.write(
                f"HEDGE_BAYESIAN: posterior={posterior:.3f} scale={scale:.3f} "
                f"expected_loss={expected_loss_bps:.1f}bps "
                f"→ inverse={inverse_pct:.1f}% vix={vix_pct:.1f}% cash={cash_pct:.1f}% "
                f"(VIX_BW={vix_bw} CREDIT={credit_warn} BREADTH={breadth_dec})\n"
            )
            sys.stderr.flush()

    # Generate hedge signals based on allocations
    hedge_signals = []

    # Confidence derived from posterior (continuous, 55-95 range)
    hedge_confidence = int(55 + posterior * 40)
    hedge_confidence = max(55, min(95, hedge_confidence))

    if inverse_pct > 0:
        # Inverse ETP signal (e.g., short S&P 500 via PSQ, SH, PSA, PSII)
        hedge_signals.append({
            "type": "signal",
            "ticker_id": msg.get("ticker_id", 0),
            "direction": "Long",  # Buy inverse (which is short the market)
            "confidence": hedge_confidence,
            "kelly_fraction": min(0.05, 0.02 + posterior * 0.04),  # Scale with probability
            "shares": 0,  # Rust-side sizing
            "strategy": "HEDGE_InverseETP",
            "book_reference": 42,
            "hedge_pct_allocation": round(inverse_pct, 2),
            "bayesian_posterior": round(posterior, 4),
            "reason": f"VIX_BW={vix_bw} CREDIT={credit_warn} BREADTH={breadth_dec} P={posterior:.3f}",
        })

    if vix_pct > 0:
        # VIX ETP signal (e.g., UVXY, VIXY for 1x or XIV for -1x)
        hedge_signals.append({
            "type": "signal",
            "ticker_id": msg.get("ticker_id", 0),
            "direction": "Long",  # VIX long (portfolio insurance)
            "confidence": hedge_confidence,
            "kelly_fraction": min(0.03, 0.01 + posterior * 0.03),  # Scale with probability
            "shares": 0,
            "strategy": "HEDGE_VIXAllocation",
            "book_reference": 42,
            "hedge_pct_allocation": round(vix_pct, 2),
            "bayesian_posterior": round(posterior, 4),
            "reason": f"VIX_BW={vix_bw} CREDIT={credit_warn} BREADTH={breadth_dec} P={posterior:.3f}",
        })

    if cash_pct > 0:
        # Cash raise signal: flatten long positions proportionally, build cash reserve
        hedge_signals.append({
            "type": "signal",
            "ticker_id": msg.get("ticker_id", 0),
            "direction": "Flat",  # Special direction for cash raise
            "confidence": hedge_confidence,
            "kelly_fraction": 0.0,  # No new positions during cash raise
            "shares": 0,
            "strategy": "HEDGE_CashRaise",
            "book_reference": 42,
            "hedge_pct_allocation": round(cash_pct, 2),
            "bayesian_posterior": round(posterior, 4),
            "reason": f"VIX_BW={vix_bw} CREDIT={credit_warn} BREADTH={breadth_dec} P={posterior:.3f}",
        })

    # Update persisted state
    _hedge_state["current_allocation"] = {
        "inverse_etp_pct": inverse_pct,
        "vix_etp_pct": vix_pct,
        "cash_raised_pct": cash_pct,
    }
    _save_hedge_state()

    return hedge_signals

def _apply_hedge_confidence_overlay(best_signal, msg, num_active_hedge_signals):
    """
    Reduce confidence for long signals when hedge is active (Book 42).

    Logic:
      - Hedge INACTIVE: no adjustment
      - 1-2 signals active: reduce long confidence by 10 points
      - Kill switch active: reduce long confidence by 25 points, short confidence by +15
    """
    if best_signal is None:
        return best_signal

    direction = best_signal.get("direction", "").lower()
    hedge_status = _hedge_state.get("status", "INACTIVE")

    if hedge_status == "INACTIVE":
        return best_signal  # No hedge adjustment

    if direction == "long":
        if hedge_status == "KILLING":
            # Kill switch: heavily penalize longs
            best_signal["confidence"] = max(0, best_signal.get("confidence", 50) - 25)
            sys.stderr.write(
                f"HEDGE_OVERLAY: long signal confidence reduced 25pts (kill switch active) "
                f"→ {best_signal['confidence']}\n"
            )
        else:
            # Normal hedge: modest long penalty
            best_signal["confidence"] = max(0, best_signal.get("confidence", 50) - 10)
            sys.stderr.write(
                f"HEDGE_OVERLAY: long signal confidence reduced 10pts (hedge active) "
                f"→ {best_signal['confidence']}\n"
            )
        sys.stderr.flush()
    elif direction == "short":
        if hedge_status == "KILLING":
            # Kill switch: boost short signals
            best_signal["confidence"] = min(100, best_signal.get("confidence", 50) + 15)
            sys.stderr.write(
                f"HEDGE_OVERLAY: short signal confidence raised 15pts (kill switch active) "
                f"→ {best_signal['confidence']}\n"
            )
            sys.stderr.flush()

    return best_signal

# ============================================================================
# Phase E: Adaptive confidence floor from dynamic_weights.toml
# ============================================================================

_adaptive_floor_loaded = False
_adaptive_confidence_floor = None
_indicator_gates_loaded = False
_indicator_gates = []  # list of {"indicator", "direction", "threshold"}
_ticker_blacklist_loaded = False
_ticker_blacklist = set()  # Set of symbols to reject signals for


def _load_ticker_blacklist():
    """Load ticker blacklist from BOTH config.toml [blacklist] AND dynamic_weights.toml.

    Two sources merged:
      1. config.toml [blacklist].tickers — static, operator-curated from backtest evidence
      2. dynamic_weights.toml [ticker_blacklist].tickers — adaptive, Ouroboros-generated
    """
    global _ticker_blacklist_loaded, _ticker_blacklist
    if _ticker_blacklist_loaded:
        return _ticker_blacklist
    _ticker_blacklist_loaded = True
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib

        combined = set()

        # Source 1: Static blacklist from config.toml
        cfg_path = "/app/config/config.toml"
        if os.path.exists(cfg_path):
            with open(cfg_path, "rb") as f:
                cfg = tomllib.load(f)
            static_bl = cfg.get("blacklist", {}).get("tickers", [])
            combined.update(static_bl)

        # Source 2: Adaptive blacklist from dynamic_weights.toml
        dw_path = "/app/config/dynamic_weights.toml"
        if os.path.exists(dw_path):
            with open(dw_path, "rb") as f:
                dw = tomllib.load(f)
            adaptive_bl = dw.get("ticker_blacklist", {}).get("tickers", [])
            combined.update(adaptive_bl)

        if combined:
            _ticker_blacklist = combined
            sys.stderr.write(
                "Bridge: loaded ticker blacklist ({} tickers): {}\n".format(
                    len(combined), ", ".join(sorted(combined)[:10])
                )
            )
            sys.stderr.flush()
        return _ticker_blacklist
    except Exception:
        return _ticker_blacklist


_blocked_exchanges_loaded = False
_blocked_exchanges = set()


def _load_blocked_exchanges():
    """S6: Load blocked exchanges from config.toml [blacklist].exchanges.

    Blocks all tickers from specific raw exchanges (e.g. LSEETF leveraged ETPs).
    Uses the raw exchange from contracts.toml, not the mapped key.
    """
    global _blocked_exchanges_loaded, _blocked_exchanges
    if _blocked_exchanges_loaded:
        return _blocked_exchanges
    _blocked_exchanges_loaded = True
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        cfg_path = "/app/config/config.toml"
        if os.path.exists(cfg_path):
            with open(cfg_path, "rb") as f:
                cfg = tomllib.load(f)
            blocked = cfg.get("blacklist", {}).get("exchanges", [])
            if blocked:
                _blocked_exchanges = set(blocked)
                sys.stderr.write(
                    f"Bridge: blocked exchanges ({len(blocked)}): {', '.join(sorted(blocked))}\n"
                )
                sys.stderr.flush()
    except Exception:
        pass
    return _blocked_exchanges


# ---------------------------------------------------------------------------
# S8: Regime + Session enforcement from strategy_registry.json
# ---------------------------------------------------------------------------
_strategy_registry_loaded = False
_strategy_registry = {}  # strategy_id → {"regime_allowed", "regime_blocked", "session_allowed", "session_blocked"}


def _load_strategy_registry():
    """Load regime/session metadata from strategy_registry.json (cached)."""
    global _strategy_registry_loaded, _strategy_registry
    if _strategy_registry_loaded:
        return _strategy_registry
    _strategy_registry_loaded = True
    reg_path = "/app/config/strategy_registry.json"
    if not os.path.exists(reg_path):
        return _strategy_registry
    try:
        with open(reg_path) as f:
            data = json.load(f)
        for key, entry in data.get("strategies", {}).items():
            sid = entry.get("id", key)
            _strategy_registry[sid] = {
                "regime_allowed": set(entry.get("regime_allowed", [])),
                "regime_blocked": set(entry.get("regime_blocked", [])),
                "session_allowed": set(entry.get("session_allowed", [])),
                "session_blocked": set(entry.get("session_blocked", [])),
                "status": entry.get("status", "live"),
            }
        sys.stderr.write(f"Bridge: loaded strategy registry ({len(_strategy_registry)} strategies)\n")
        sys.stderr.flush()
    except Exception as e:
        sys.stderr.write(f"Bridge: failed to load strategy registry: {e}\n")
        sys.stderr.flush()
    return _strategy_registry


def _classify_market_regime(hurst, rvol, adx):
    """Map indicator values to registry regime name.

    Uses the definitions from strategy_registry.json:
      trend_up: Hurst > 0.55, ADX > 20, price above 20-SMA (simplified to Hurst+ADX)
      trend_down: Hurst > 0.55, ADX > 20
      high_vol_trend: Hurst > 0.55, RVOL > 2.0
      high_vol_chop: Hurst < 0.45, RVOL > 2.0
      low_vol_compression: 0.45 <= Hurst <= 0.55, RVOL < 1.0
    """
    if rvol > 2.0 and hurst > 0.55:
        return "high_vol_trend"
    if rvol > 2.0 and hurst < 0.45:
        return "high_vol_chop"
    if hurst > 0.55 and adx > 20:
        return "trend_up"  # Can't distinguish up/down without price vs SMA
    if 0.45 <= hurst <= 0.55 and rvol < 1.0:
        return "low_vol_compression"
    if hurst > 0.55:
        return "trend_up"
    return "low_vol_compression"  # Default: benign regime


def _classify_current_session():
    """Determine current session from UTC time.

    Returns session name matching strategy_registry.json definitions.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    h = now.hour

    if 1 <= h < 8:
        return "asia_main"
    if 8 <= h < 12:
        return "lse_main"
    if 12 <= h < 14:
        return "us_premarket"
    if h == 14 and now.minute < 30:
        return "us_premarket"
    if (h == 14 and now.minute >= 30) or (h == 15 and now.minute < 30):
        return "us_open"
    if 15 <= h < 19:
        return "us_midday"
    if 19 <= h < 21:
        return "us_power_hour"
    if 21 <= h < 23:
        return "us_after_hours"
    return "lse_main"  # Default: benign session


def _check_regime_session_gate(strategy_id, hurst, rvol, adx):
    """S8: Check if strategy is allowed in current regime and session.

    Returns (allowed, reason) tuple.
    """
    registry = _load_strategy_registry()
    entry = registry.get(strategy_id)
    if not entry:
        return True, ""  # Unknown strategy: fail-open

    # Regime check
    regime = _classify_market_regime(hurst, rvol, adx)
    if "ALL" in entry["regime_blocked"]:
        return False, f"regime_blocked=ALL (regime={regime})"
    if regime in entry["regime_blocked"]:
        return False, f"regime_blocked={regime}"
    if entry["regime_allowed"] and regime not in entry["regime_allowed"]:
        return False, f"regime={regime} not in allowed={entry['regime_allowed']}"

    # Session check
    session = _classify_current_session()
    if "ALL" in entry["session_blocked"]:
        return False, f"session_blocked=ALL (session={session})"
    if session in entry["session_blocked"]:
        return False, f"session_blocked={session}"
    # ALL_EXCEPT_* pattern (used by ORB)
    for blocked in entry["session_blocked"]:
        if blocked.startswith("ALL_EXCEPT_"):
            allowed_session = blocked.replace("ALL_EXCEPT_", "").lower()
            if session != allowed_session:
                return False, f"session={session} not in {allowed_session}"

    return True, ""


def _load_indicator_gates():
    """Load indicator gates from dynamic_weights.toml if available."""
    global _indicator_gates_loaded, _indicator_gates
    if _indicator_gates_loaded:
        return _indicator_gates
    _indicator_gates_loaded = True
    dw_path = "/app/config/dynamic_weights.toml"
    if not os.path.exists(dw_path):
        return []
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(dw_path, "rb") as f:
            data = tomllib.load(f)
        gates_section = data.get("indicator_gates", {})
        rules = gates_section.get("rules", [])
        if rules:
            _indicator_gates = rules
            sys.stderr.write(f"Bridge: loaded {len(rules)} indicator gates from dynamic_weights.toml\n")
            sys.stderr.flush()
        return _indicator_gates
    except Exception:
        return []


def _load_adaptive_floor():
    """Load confidence floor from dynamic_weights.toml if available."""
    global _adaptive_floor_loaded, _adaptive_confidence_floor
    if _adaptive_floor_loaded:
        return _adaptive_confidence_floor
    _adaptive_floor_loaded = True
    dw_path = "/app/config/dynamic_weights.toml"
    if not os.path.exists(dw_path):
        return None
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(dw_path, "rb") as f:
            data = tomllib.load(f)
        floor = data.get("signal", {}).get("confidence_floor")
        if floor is not None:
            _adaptive_confidence_floor = float(floor)
            sys.stderr.write(f"Bridge: loaded adaptive confidence_floor={floor} from dynamic_weights.toml\n")
            sys.stderr.flush()
        return _adaptive_confidence_floor
    except Exception:
        return None


# ============================================================================
# Plan 1 Phase 3: Adaptive parameter loaders from dynamic_weights.toml
# ============================================================================

_adaptive_params_loaded = False
_adaptive_chandelier_atr = None
_adaptive_spread_veto = None
_adaptive_entry_weights = {}
_adaptive_exchange_weights = {}
_adaptive_kelly_cap = None
_adaptive_entry_confidence = {}  # Thompson Sampler per-type confidence floors


_adaptive_params_last_load = 0.0
_ADAPTIVE_RELOAD_INTERVAL = 300.0  # Reload every 5 min to pick up nightly changes

def _load_adaptive_params():
    """Load adaptive parameters from dynamic_weights.toml AND nightly recommendations.

    Reloads every 5 minutes so nightly pipeline outputs feed back into live trading.
    Sources: dynamic_weights.toml (Ouroboros), nightly_recommendations.json (nightly_v6).
    """
    global _adaptive_params_loaded, _adaptive_params_last_load
    global _adaptive_chandelier_atr, _adaptive_spread_veto
    global _adaptive_entry_weights, _adaptive_exchange_weights, _adaptive_kelly_cap
    global _adaptive_entry_confidence
    _now = time.time()
    if _adaptive_params_loaded and (_now - _adaptive_params_last_load < _ADAPTIVE_RELOAD_INTERVAL):
        return
    _adaptive_params_loaded = True
    _adaptive_params_last_load = _now

    dw_path = "/app/config/dynamic_weights.toml"
    if not os.path.exists(dw_path):
        return

    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(dw_path, "rb") as f:
            data = tomllib.load(f)

        # Adaptive Chandelier ATR multiplier
        ac = data.get("adaptive_chandelier", {})
        if "atr_mult" in ac:
            _adaptive_chandelier_atr = float(ac["atr_mult"])

        # Adaptive spread veto threshold
        asp = data.get("adaptive_spread", {})
        if "spread_veto_pct" in asp:
            _adaptive_spread_veto = float(asp["spread_veto_pct"])

        # Adaptive entry type weights
        aew = data.get("adaptive_entry_weights", {})
        for etype in ["TypeA", "TypeB", "TypeC", "TypeD"]:
            if etype in aew:
                _adaptive_entry_weights[etype] = float(aew[etype])

        # Adaptive per-exchange session weights
        axw = data.get("adaptive_exchange_weights", {})
        for exch, weight in axw.items():
            _adaptive_exchange_weights[exch] = float(weight)

        # BT-005: Fallback — if dynamic_weights.toml has no adaptive exchange
        # weights (first boot, or nightly had no per-exchange data), load the
        # static baseline from config.toml [position.exchange_sizing_weights].
        if not _adaptive_exchange_weights:
            cfg_path = "/app/config/config.toml"
            if os.path.exists(cfg_path):
                with open(cfg_path, "rb") as f:
                    cfg = tomllib.load(f)
                static_ew = cfg.get("position", {}).get("exchange_sizing_weights", {})
                for exch, weight in static_ew.items():
                    _adaptive_exchange_weights[exch] = float(weight)
                if _adaptive_exchange_weights:
                    sys.stderr.write(
                        "Bridge: exchange weights fallback from config.toml "
                        "[position.exchange_sizing_weights]: {}\n".format(_adaptive_exchange_weights)
                    )
                    sys.stderr.flush()

        # Adaptive Kelly cap
        ak = data.get("adaptive_kelly", {})
        if "kelly_cap" in ak:
            _adaptive_kelly_cap = float(ak["kelly_cap"])

        # Thompson Sampler per-type confidence floors
        aec = data.get("adaptive_entry_confidence", {})
        _type_field_map = {
            "type_a_confidence": "TypeA",
            "type_b_confidence": "TypeB",
            "type_c_confidence": "TypeC",
            "type_d_confidence": "TypeD",
        }
        for field, etype in _type_field_map.items():
            if field in aec:
                _adaptive_entry_confidence[etype] = float(aec[field])

        parts = []
        if _adaptive_chandelier_atr is not None:
            parts.append(f"chandelier_atr={_adaptive_chandelier_atr:.2f}")
        if _adaptive_spread_veto is not None:
            parts.append(f"spread_veto={_adaptive_spread_veto*100:.1f}%")
        if _adaptive_entry_weights:
            parts.append(f"entry_weights={_adaptive_entry_weights}")
        if _adaptive_exchange_weights:
            parts.append(f"exchange_weights={_adaptive_exchange_weights}")
        if _adaptive_kelly_cap is not None:
            parts.append(f"kelly_cap={_adaptive_kelly_cap:.2f}")
        if _adaptive_entry_confidence:
            parts.append(f"entry_confidence={_adaptive_entry_confidence}")
        if parts:
            sys.stderr.write(f"Bridge: loaded adaptive params: {', '.join(parts)}\n")
            sys.stderr.flush()

    except Exception as e:
        sys.stderr.write(f"Bridge: failed to load adaptive params: {e}\n")
        sys.stderr.flush()

    # ── NIGHTLY RECOMMENDATIONS FEEDBACK LOOP ──
    # Load the latest nightly recommendations and apply them to live trading params.
    # This closes the loop: nightly analysis → next-day trading behavior.
    try:
        import glob as _glob_mod
        _rec_files = sorted(_glob_mod.glob("/app/data/*_recommendations.json"), reverse=True)
        if _rec_files:
            with open(_rec_files[0]) as f:
                _nightly_recs = json.load(f)

            # 1. Exit optimization → update Chandelier ATR multiplier
            _exit_opt = _nightly_recs.get("exit_optimization", {})
            if isinstance(_exit_opt, dict) and "optimal_atr_mult" in _exit_opt:
                _adaptive_chandelier_atr = float(_exit_opt["optimal_atr_mult"])

            # 2. HRP portfolio weights → update exchange weights
            _hrp = _nightly_recs.get("hrp_weights", {})
            if isinstance(_hrp, dict) and _hrp:
                for sym, w in _hrp.items():
                    # Map symbol prefixes to exchanges
                    if isinstance(w, (int, float)) and w > 0:
                        _adaptive_exchange_weights[sym] = float(w)

            # 3. Robustness validation → auto-kill weak strategies
            _robustness = _nightly_recs.get("robustness_validation", {})
            if isinstance(_robustness, dict):
                for strat, result in _robustness.items():
                    if isinstance(result, dict) and result.get("sharpe", 999) < -0.5:
                        _auto_killed_strategies.add(strat)
                        sys.stderr.write(
                            f"NIGHTLY_AUTO_KILL: {strat} sharpe={result.get('sharpe', '?')}\n"
                        )
                        sys.stderr.flush()

            # 4. Conformal prediction → update adaptive Kelly cap
            _conformal = _nightly_recs.get("conformal", {})
            if isinstance(_conformal, dict):
                _width = _conformal.get("mean_width", 0.5)
                if isinstance(_width, (int, float)) and _width > 0.7:
                    # Wide prediction intervals = uncertain market → tighter Kelly
                    _adaptive_kelly_cap = min(_adaptive_kelly_cap or 0.05, 0.03)

            # 5. True leverage → feed back for pre-trade checks
            _true_lev = _nightly_recs.get("true_leverage", {})
            if isinstance(_true_lev, dict) and _true_lev.get("total_effective", 0) > 4.0:
                # Portfolio is over-leveraged — reduce new entries
                _adaptive_kelly_cap = min(_adaptive_kelly_cap or 0.05, 0.02)

            # 6. Causal DAG → boost/penalize instruments based on causal leadership
            # FIX: nightly writes "causal_dag" with "edges" list, not "leaders".
            # Extract leader nodes from edges (nodes with highest out-degree).
            _nightly_causal = _nightly_recs.get("causal_dag", {})
            if isinstance(_nightly_causal, dict):
                # Try both key names for compatibility
                _causal_leaders = _nightly_causal.get("leaders", [])
                if not _causal_leaders:
                    # Extract leaders from edges: count out-degree per node
                    _edges = _nightly_causal.get("edges", [])
                    if _edges:
                        _out_degree = {}
                        for edge in _edges:
                            src = edge.get("source", edge.get("from", ""))
                            if src:
                                _out_degree[src] = _out_degree.get(src, 0) + 1
                        # Leaders = top 5 by out-degree
                        _causal_leaders = sorted(_out_degree, key=_out_degree.get, reverse=True)[:5]
                _load_adaptive_params._causal_leaders = _causal_leaders

            # 7. OU mean-reversion → flag instruments with strong mean-reversion for MR strategies
            # FIX: nightly writes "stochastic" with "ou_calibrations" sub-key, not "ou_mean_reversion"
            _ou_recs = _nightly_recs.get("ou_mean_reversion", {})
            if not _ou_recs:
                # Try the actual nightly key
                _stoch = _nightly_recs.get("stochastic", {})
                if isinstance(_stoch, dict):
                    _ou_recs = _stoch.get("ou_calibrations", _stoch)
            if isinstance(_ou_recs, dict):
                _load_adaptive_params._ou_instruments = {
                    sym: params for sym, params in _ou_recs.items()
                    if isinstance(params, dict) and params.get("half_life_bars", 999) < 50
                }

            # 8. Domain shift → reduce weights on shifted instruments
            # FIX: nightly writes "transfer_learning", not "domain_shift"
            _domain_shift = _nightly_recs.get("domain_shift", {})
            if not _domain_shift:
                _tl = _nightly_recs.get("transfer_learning", {})
                if isinstance(_tl, dict):
                    _domain_shift = _tl.get("domain_shifts", _tl)
            if isinstance(_domain_shift, dict):
                for sym, shift_info in _domain_shift.items():
                    if isinstance(shift_info, dict) and shift_info.get("mmd_score", 0) > 0.5:
                        # Significant domain shift — reduce exchange weight
                        _exch = sym[:3].upper()  # crude mapping
                        if _exch in _adaptive_exchange_weights:
                            _adaptive_exchange_weights[_exch] *= 0.7

            # 9. NSGA3 Pareto optimal params → apply best found params
            # FIX: nightly writes "nsga3", not "nsga3_optimization"
            _nsga3 = _nightly_recs.get("nsga3_optimization", _nightly_recs.get("nsga3", {}))
            if isinstance(_nsga3, dict) and _nsga3.get("best_params"):
                _best_nsga = _nsga3["best_params"]
                if "kelly_cap" in _best_nsga:
                    _adaptive_kelly_cap = float(_best_nsga["kelly_cap"])
                if "chandelier_atr" in _best_nsga:
                    _adaptive_chandelier_atr = float(_best_nsga["chandelier_atr"])

    except FileNotFoundError:
        pass
    except Exception as e:
        sys.stderr.write(f"Bridge: nightly recs load error (non-fatal): {e}\n")
        sys.stderr.flush()


# ============================================================================
# BT-004: Hour-of-day confidence weight loader from config.toml
# Lazy-loaded, cached for process lifetime (same pattern as _load_adaptive_floor).
# Reads [timing.hour_weights] → {"00": 1.0, "01": 0.7, ...}
# At runtime also checks [adaptive_hour_weights] in dynamic_weights.toml;
# adaptive values (from nightly rolling WR) override static if present.
# ============================================================================
_hour_weights_loaded = False
_hour_weights = {}  # UTC hour (int 0-23) → confidence multiplier (float)

# Per-ticker RVOL history for TypeB 3-bar rising detection
_rvol_history = {}  # ticker_id → deque(maxlen=3) of RVOL values

# Entry type config thresholds (loaded once from config.toml)
_entry_type_cfg_loaded = False
_entry_type_cfg = {}


def _load_entry_type_cfg():
    """Load TypeA-F thresholds from config.toml [entry_types]."""
    global _entry_type_cfg_loaded, _entry_type_cfg
    if _entry_type_cfg_loaded:
        return _entry_type_cfg
    _entry_type_cfg_loaded = True
    defaults = {
        "type_a_rsi_oversold": 30.0, "type_a_volume_spike_mult": 2.5, "type_a_drop_atr_mult": 2.5,
        "type_b_rsi_low": 30.0, "type_b_rsi_high": 70.0, "type_b_momentum_bars": 3,
        "type_c_rsi_overbought": 80.0,
        "type_d_price_proximity_pct": 0.5, "type_d_rsi_low": 25.0, "type_d_rsi_high": 35.0,
        "type_e_ibs_threshold": 0.10, "type_e_rvol_threshold": 1.0,
        "type_f_obv_rsi_threshold": 30.0, "type_f_rvol_threshold": 0.7,
    }
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        cfg_path = "/app/config/config.toml"
        if not os.path.exists(cfg_path):
            cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "config.toml")
        if os.path.exists(cfg_path):
            with open(cfg_path, "rb") as f:
                cfg = tomllib.load(f)
            et = cfg.get("entry_types", {})
            for k in defaults:
                if k in et:
                    defaults[k] = float(et[k])
    except Exception:
        pass
    _entry_type_cfg = defaults
    return defaults


def classify_entry_type(rsi_14, ibs, rvol, ticker_id, prices, volumes, vol_div):
    """Classify signal into TypeA-F matching Rust entry_engine.rs logic.

    Uses indicators already computed by bridge.py. Returns the entry type string.
    Priority order: F, E, B, C, A, D (highest-WR first from backtest).
    """
    from collections import deque
    cfg = _load_entry_type_cfg()

    # Track RVOL history for TypeB (3-bar rising detection)
    if ticker_id not in _rvol_history:
        _rvol_history[ticker_id] = deque(maxlen=3)
    _rvol_history[ticker_id].append(rvol)

    # TypeF (OBVDivergence): OBV-RSI(5) < 30 + RVOL > 0.7
    # Simplified: use volume_divergence as proxy for OBV-RSI declining
    if vol_div < -0.5 and rvol > cfg["type_f_rvol_threshold"]:
        return "TypeF"

    # TypeE (IBSMeanReversion): IBS < 0.10 + RVOL > 1.0
    if ibs < cfg["type_e_ibs_threshold"] and rvol > cfg["type_e_rvol_threshold"]:
        return "TypeE"

    # TypeB (EarlyRunner): RVOL rising + RSI in [20, 80]
    # S4B: Loosened from 3-bar strictly rising to 2-bar rising. 3-bar was unreachable on 5s bars.
    rvol_hist = list(_rvol_history[ticker_id])
    type_b_rising = (len(rvol_hist) >= 2
                     and rvol_hist[-1] > rvol_hist[-2])
    type_b_rsi_ok = (rsi_14 is not None
                     and cfg["type_b_rsi_low"] <= rsi_14 <= cfg["type_b_rsi_high"])
    if type_b_rising and type_b_rsi_ok:
        return "TypeB"

    # TypeC (OverboughtFade): RSI > 80 + price rising + volume declining
    if (rsi_14 is not None and rsi_14 > cfg["type_c_rsi_overbought"]
            and len(prices) >= 2 and prices[-1] > prices[-2]
            and len(volumes) >= 2 and volumes[-1] < volumes[-2]):
        return "TypeC"

    # TypeA (DipRecovery): RSI < 30 + RVOL > vol_ma20 * 2.5
    # Simplified: use RVOL > spike_mult as proxy (vol_ma20 normalization already in RVOL)
    if rsi_14 is not None and rsi_14 < cfg["type_a_rsi_oversold"] and rvol > cfg["type_a_volume_spike_mult"]:
        return "TypeA"

    # TypeD (SupportBounce): price within 0.5% of daily low + RSI 25-35
    if (rsi_14 is not None
            and cfg["type_d_rsi_low"] <= rsi_14 <= cfg["type_d_rsi_high"]
            and len(prices) >= 10):
        daily_low = min(prices[-min(len(prices), 100):])  # Approximate daily low from recent prices
        if daily_low > 0:
            pct_above = ((prices[-1] - daily_low) / daily_low) * 100
            if pct_above <= cfg["type_d_price_proximity_pct"]:
                return "TypeD"

    return "Unclassified"


def _load_hour_weights():
    """Load hour-of-day confidence weights from config.toml [timing.hour_weights].

    Falls back to dynamic_weights.toml [adaptive_hour_weights] if present
    (adaptive overrides static). Loaded once and cached for process lifetime.
    """
    global _hour_weights_loaded, _hour_weights
    if _hour_weights_loaded:
        return _hour_weights
    _hour_weights_loaded = True

    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib

        # Source 1: Static hour weights from config.toml
        cfg_path = "/app/config/config.toml"
        if os.path.exists(cfg_path):
            with open(cfg_path, "rb") as f:
                cfg = tomllib.load(f)
            hw = cfg.get("timing", {}).get("hour_weights", {})
            for hour_str, weight in hw.items():
                try:
                    _hour_weights[int(hour_str)] = float(weight)
                except (ValueError, TypeError):
                    continue

        # Source 2: Adaptive hour weights from dynamic_weights.toml (override static)
        dw_path = "/app/config/dynamic_weights.toml"
        if os.path.exists(dw_path):
            with open(dw_path, "rb") as f:
                dw = tomllib.load(f)
            ahw = dw.get("adaptive_hour_weights", {})
            for hour_str, weight in ahw.items():
                try:
                    _hour_weights[int(hour_str)] = float(weight)
                except (ValueError, TypeError):
                    continue

        if _hour_weights:
            sys.stderr.write(
                "Bridge: loaded hour weights ({} hours, range {:.2f}-{:.2f})\n".format(
                    len(_hour_weights),
                    min(_hour_weights.values()),
                    max(_hour_weights.values()),
                )
            )
            sys.stderr.flush()
    except Exception as e:
        sys.stderr.write(f"Bridge: failed to load hour weights: {e}\n")
        sys.stderr.flush()

    return _hour_weights


# ============================================================================
# Strategies.toml loader (lazy, once at first tick)
# ============================================================================

_strategies_config_cache = None
_strategies_config_loaded = False


def _load_strategies_config():
    """Load strategy configs from /app/config/strategies.toml.

    Returns a list of StrategyConfig. Loaded once and cached for the process lifetime.
    Falls back to empty list on error (orchestrator simply produces no intents).
    """
    global _strategies_config_cache, _strategies_config_loaded
    if _strategies_config_loaded:
        return _strategies_config_cache or []

    _strategies_config_loaded = True

    strategies_path = "/app/config/strategies.toml"
    if not os.path.exists(strategies_path):
        sys.stderr.write(f"Bridge: strategies.toml not found at {strategies_path}\n")
        sys.stderr.flush()
        _strategies_config_cache = []
        return []

    try:
        # Python 3.11+ has tomllib built-in; 3.10 and below need tomli
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib

        with open(strategies_path, "rb") as f:
            data = tomllib.load(f)

        configs = []
        strategy_section = data.get("strategy", {})

        for name, section in strategy_section.items():
            if not isinstance(section, dict):
                continue

            # Skip adaptive_ranges sub-tables
            if name.endswith("adaptive_ranges"):
                continue

            family_str = section.get("family", "mean_reversion")
            family = (
                StrategyFamily.MOMENTUM
                if family_str == "momentum"
                else StrategyFamily.MEAN_REVERSION
            )

            # Collect all non-structural keys as params
            structural_keys = {
                "enabled", "priority", "family", "base_confidence",
                "session_eligible", "session_blocked", "regime_eligible",
                "regime_blocked", "sizing_mult", "ticker_whitelist",
                "ticker_preferred", "adaptive_ranges",
            }
            params = {
                k: v for k, v in section.items()
                if k not in structural_keys and not isinstance(v, dict)
            }

            configs.append(StrategyConfig(
                name=name,
                enabled=section.get("enabled", True),
                priority=section.get("priority", 5),
                family=family,
                base_confidence=section.get("base_confidence", 65.0),
                session_eligible=section.get("session_eligible", []),
                session_blocked=section.get("session_blocked", []),
                regime_eligible=section.get("regime_eligible", []),
                regime_blocked=section.get("regime_blocked", []),
                sizing_mult=section.get("sizing_mult", 1.0),
                ticker_whitelist=section.get("ticker_whitelist", []),
                ticker_preferred=section.get("ticker_preferred", []),
                params=params,
            ))

        _strategies_config_cache = configs
        sys.stderr.write(
            f"Bridge: loaded {len(configs)} strategy configs from strategies.toml "
            f"({[c.name for c in configs]})\n"
        )
        sys.stderr.flush()
        return configs

    except Exception as e:
        sys.stderr.write(f"Bridge: failed to load strategies.toml: {e}\n")
        sys.stderr.flush()
        _strategies_config_cache = []
        return []


# ============================================================================
# Ticker ranking loader (from strategies.toml [ticker_ranking.current])
# ============================================================================

_ticker_rankings = None


def _load_ticker_rankings():
    """Load ticker priority scores from strategies.toml [ticker_ranking.current].

    Returns dict of symbol -> score. Cached for process lifetime (Ouroboros
    refreshes every 2 hours; bridge restarts pick up new rankings).
    """
    global _ticker_rankings
    if _ticker_rankings is not None:
        return _ticker_rankings

    strategies_path = "/app/config/strategies.toml"
    if not os.path.exists(strategies_path):
        _ticker_rankings = {}
        return _ticker_rankings

    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib

        with open(strategies_path, "rb") as f:
            data = tomllib.load(f)

        rankings = data.get("ticker_ranking", {}).get("current", {})
        _ticker_rankings = {str(k): float(v) for k, v in rankings.items()}
        return _ticker_rankings

    except Exception:
        _ticker_rankings = {}
        return _ticker_rankings


# ============================================================================
# Helper: compute ADX from bar history (reuse VanguardSniper's logic)
# ============================================================================

def _compute_adx(ticks, period=14):
    """Compute current ADX from tick history. Returns float or 20.0 on error."""
    import numpy as np
    n = len(ticks)
    if n < period + 2:
        return 20.0

    closes = np.array([t["last"] for t in ticks], dtype=np.float64)
    highs = np.array(
        [max(t["last"], t.get("high", t["last"])) for t in ticks], dtype=np.float64
    )
    lows = np.array(
        [min(t["last"], t.get("low", t["last"])) for t in ticks], dtype=np.float64
    )

    # Same ADX implementation as vanguard_sniper._adx
    high_low = highs[1:] - lows[1:]
    high_close = np.abs(highs[1:] - closes[:-1])
    low_close = np.abs(lows[1:] - closes[:-1])
    tr = np.maximum(high_low, np.maximum(high_close, low_close))

    up_move = highs[1:] - highs[:-1]
    down_move = lows[:-1] - lows[1:]
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    atr = np.empty(len(tr), dtype=np.float64)
    smooth_plus = np.empty(len(tr), dtype=np.float64)
    smooth_minus = np.empty(len(tr), dtype=np.float64)
    atr[0] = tr[0]
    smooth_plus[0] = plus_dm[0]
    smooth_minus[0] = minus_dm[0]
    for i in range(1, len(tr)):
        atr[i] = atr[i - 1] - atr[i - 1] / period + tr[i]
        smooth_plus[i] = smooth_plus[i - 1] - smooth_plus[i - 1] / period + plus_dm[i]
        smooth_minus[i] = smooth_minus[i - 1] - smooth_minus[i - 1] / period + minus_dm[i]

    safe_atr = np.where(atr == 0, 1e-9, atr)
    plus_di = 100.0 * smooth_plus / safe_atr
    minus_di = 100.0 * smooth_minus / safe_atr
    di_sum = plus_di + minus_di
    safe_di_sum = np.where(di_sum == 0, 1e-9, di_sum)
    dx = 100.0 * np.abs(plus_di - minus_di) / safe_di_sum

    adx_arr = np.empty(len(dx), dtype=np.float64)
    adx_arr[0] = dx[0]
    for i in range(1, len(dx)):
        adx_arr[i] = adx_arr[i - 1] + (dx[i] - adx_arr[i - 1]) / period

    val = float(adx_arr[-1])
    if math.isnan(val):
        return 20.0
    return val


# ============================================================================
# Orchestrator evaluation on each tick
# ============================================================================

def _evaluate_orchestrator(msg, ticks, rvol, hurst, hurst_regime, adx):
    """Run the autonomous orchestrator on the current tick data.

    Returns the best TradeIntent, or None if no opportunities found.
    """
    ticker_id = msg["ticker_id"]
    symbol = ticker_symbols.get(ticker_id, f"TICKER_{ticker_id}")

    # Load strategy configs (cached after first call)
    strategies = _load_strategies_config()
    if not strategies:
        return None

    # Load ticker rankings (cached)
    rankings = _load_ticker_rankings()
    priority_score = rankings.get(symbol, 50.0)

    # Compute spread in basis points
    last_price = msg["last"]
    bid = msg.get("bid", last_price)
    ask = msg.get("ask", last_price)
    spread_bps = 0.0
    if last_price > 0:
        spread_bps = ((ask - bid) / last_price) * 10000.0

    # Compute RSI(2) and RSI(14) from close prices
    prices = [t["last"] for t in ticks]
    rsi_2 = calculate_rsi(prices, period=2)
    if rsi_2 is None:
        rsi_2 = 50.0
    rsi_14 = calculate_rsi(prices, period=14)  # For TypeA-F classification

    # Compute IBS from latest bar
    latest = ticks[-1]
    high = latest.get("high", latest["last"])
    low = latest.get("low", latest["last"])
    ibs = calculate_ibs(high, low, latest["last"])
    if ibs is None:
        ibs = 0.5

    # VWAP: update calculator and get sigma position + slope + volume profile
    vwap_calc = vwap_calculators[ticker_id]
    vwap_bar = VWAPBar(
        high=high,
        low=low,
        close=latest["last"],
        volume=float(latest.get("volume", 0)),
    )
    vwap_result = vwap_calc.update(vwap_bar)
    vwap_sigma = 0.0
    vwap_slope = 0.0
    volume_profile = "normal"
    vwap_price = latest["last"]
    if vwap_result is not None:
        vwap_sigma = vwap_result.sigma_position
        vwap_slope = vwap_result.slope
        volume_profile = vwap_result.volume_profile
        vwap_price = vwap_result.vwap

    # SMA-200 and SMA-5 (from available bar history, not daily bars)
    sma_200 = calculate_sma(prices, 200) or 0.0
    sma_5 = calculate_sma(prices, 5) or 0.0

    # Gap detection: use first bar in history as "daily open" proxy
    daily_open = ticks[0]["last"] if ticks else last_price
    prev_close = daily_open  # No true prev_close in intraday; use daily_open as proxy
    gap_pct = msg.get("gap_pct", 0.0)

    # ATR from recent bars (simple approximation)
    if len(ticks) >= 2:
        ranges = [
            max(t.get("high", t["last"]), t["last"]) - min(t.get("low", t["last"]), t["last"])
            for t in ticks[-min(14, len(ticks)):]
        ]
        atr = sum(ranges) / len(ranges) if ranges else 0.0
    else:
        atr = 0.0

    # Build TickerState
    ticker_state = TickerState(
        ticker_id=ticker_id,
        symbol=symbol,
        last_price=last_price,
        bid=bid,
        ask=ask,
        spread_bps=spread_bps,
        volume=msg.get("volume", 0),
        rvol=rvol,
        atr=atr,
        rsi_2=rsi_2,
        ibs=ibs,
        vwap=vwap_price,
        vwap_sigma=vwap_sigma,
        vwap_slope=vwap_slope,
        adx=adx,
        hurst=hurst,
        sma_200=sma_200,
        sma_5=sma_5,
        daily_open=daily_open,
        prev_close=prev_close,
        gap_pct=gap_pct,
        volume_profile=volume_profile,
        leverage=msg.get("leverage", 3),
        is_inverse=False,
        priority_score=priority_score,
    )

    # Build MarketContext
    # London time from time_fraction (fraction of trading day 0.0-1.0)
    # time_fraction=0.0 → 08:00 London, time_fraction=1.0 → 16:30 London
    # Convert: london_time_secs = 8*3600 + time_fraction * 8.5 * 3600
    time_fraction = msg.get("time_fraction", 0.5)
    london_time_secs = int(8 * 3600 + time_fraction * 8.5 * 3600)

    # Map hurst_regime string to RegimeType enum
    if hurst_regime == "trending":
        regime = RegimeType.TRENDING
    elif hurst_regime == "mean_reverting":
        regime = RegimeType.MEAN_REVERTING
    else:
        regime = RegimeType.RANDOM

    market_ctx = MarketContext(
        london_time_secs=london_time_secs,
        regime=regime,
        vix=msg.get("vix", 20.0),
        spy_first_30min_return=msg.get("spy_first_30min_return", 0.0),
        nq_overnight_change=msg.get("nq_overnight_change", 0.0),
        broad_market_at_lows=msg.get("broad_market_at_lows", False),
        has_news_catalyst=msg.get("has_news_catalyst", False),
        spx_126d_return=msg.get("spx_126d_return", 0.05),
    )

    # Run orchestrator
    intents = orchestrate(
        tickers=[ticker_state],
        ctx=market_ctx,
        strategies=strategies,
        max_intents=3,
    )

    if not intents:
        return None

    # Return the best intent (highest combined_score)
    return intents[0]


def _compute_indicators(ticker_id, ticks, msg):
    """Stage 1: Compute all indicators from tick data. Returns a dict."""
    # Aggregate into 5-minute OHLCV bars (cached)
    BARS_PER_5MIN = 60
    n_5min_bars = len(ticks) // BARS_PER_5MIN
    cached = _bar_cache.get(ticker_id)
    if cached and cached[0] == n_5min_bars:
        bars_5m = cached[1]
    else:
        bars_5m = []
        for i in range(n_5min_bars):
            chunk = ticks[i * BARS_PER_5MIN : (i + 1) * BARS_PER_5MIN]
            bars_5m.append({
                "open": chunk[0]["last"], "high": max(t["last"] for t in chunk),
                "low": min(t["last"] for t in chunk), "close": chunk[-1]["last"],
                "volume": sum(t["volume"] for t in chunk), "last": chunk[-1]["last"],
            })
        _bar_cache[ticker_id] = (n_5min_bars, bars_5m)

    # Compute indicators on 5-MINUTE bars (preferred) or raw ticks (fallback)
    if bars_5m:
        prices_5m = [b["close"] for b in bars_5m]
        volumes_5m = [b["volume"] for b in bars_5m]
        rvol = calculate_rvol(volumes_5m, window=20) if len(volumes_5m) >= 20 else 1.0
        hurst = estimate_hurst(prices_5m, max_lag=min(20, len(prices_5m) - 1)) if len(prices_5m) >= 5 else 0.5
        vol_div = volume_divergence(prices_5m, volumes_5m, window=10) if len(prices_5m) >= 10 else 0.0
        adx = _compute_adx([{"last": b["close"], "high": b["high"], "low": b["low"], "volume": b["volume"]} for b in bars_5m])
    else:
        volumes = [t["volume"] for t in ticks]
        prices = [t["last"] for t in ticks]
        rvol = calculate_rvol(volumes, window=20)
        hurst = estimate_hurst(prices, max_lag=20)
        vol_div = volume_divergence(prices, volumes, window=10)
        adx = _compute_adx(ticks)
    hurst_regime = classify_regime(hurst)

    # Volume trend slope
    vol_slope = 0.0
    if len(bars_5m) >= 5:
        recent_vols = [b["volume"] for b in bars_5m[-10:]]
        if len(recent_vols) >= 3:
            n = len(recent_vols)
            x_mean = (n - 1) / 2.0
            y_mean = sum(recent_vols) / n
            num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(recent_vols))
            den = sum((i - x_mean) ** 2 for i in range(n))
            vol_slope = num / den if den > 0 else 0.0

    # VPIN (shadow — never gates)
    vpin = 0.0
    if bars_5m and len(bars_5m) >= 20:
        try:
            bc = [b.get("close", b.get("last", 0)) for b in bars_5m[-50:]]
            bv = [b.get("volume", 0) for b in bars_5m[-50:]]
            buy_v, sell_v = classify_volume_bvc(bc, bv)
            vpin = calculate_vpin(buy_v, sell_v, n_buckets=min(20, len(bc)))
        except Exception:
            pass

    # IBS from latest tick
    latest = ticks[-1]
    high = latest.get("high", latest["last"])
    low = latest.get("low", latest["last"])
    ibs = calculate_ibs(high, low, latest["last"])
    if ibs is None:
        ibs = 0.5

    # VWAP update
    vwap_calc = vwap_calculators[ticker_id]
    vwap_bar = VWAPBar(high=high, low=low, close=latest["last"], volume=float(latest.get("volume", 0)))
    vwap_result = vwap_calc.update(vwap_bar)
    vwap_sigma, vwap_slope, vwap_price = 0.0, 0.0, latest["last"]
    if vwap_result is not None:
        vwap_sigma = vwap_result.sigma_position
        vwap_slope = vwap_result.slope
        vwap_price = vwap_result.vwap

    # VWAP distance
    bid = msg.get("bid", 0)
    ask = msg.get("ask", 0)
    spread_pct = ((ask - bid) / ((ask + bid) / 2) * 100) if bid > 0 and ask > 0 else 0
    vwap_dist_pct = 0.0
    vc = vwap_calculators.get(ticker_id)
    if vc:
        vh = vc.get_history()
        if vh and vh[-1] > 0:
            vwap_dist_pct = (msg["last"] - vh[-1]) / vh[-1] * 100

    # Structural Tradability Score (0-100)
    sts = {}
    if bid > 0 and ask > 0:
        sts["spread"] = max(0, min(25, int(25 - spread_pct * 8.3)))
    else:
        sts["spread"] = 10
    sts["regime_clarity"] = min(25, int(abs(hurst - 0.5) / 0.5 * 25)) if hurst > 0.01 else 0
    vs = (10 if rvol > 1.0 else 5 if rvol > 0.7 else 0) + (10 if vol_slope > 0 else 3 if vol_slope == 0 else 0)
    sts["volume"] = min(20, vs)
    sts["adx_strength"] = 15 if adx >= 30 else 10 if adx >= 20 else 5 if adx >= 12 else 0
    nb = len(ticks)
    sts["data_quality"] = 15 if nb >= 500 else 10 if nb >= 300 else 5 if nb >= 200 else 2
    structural_score = sum(sts.values())

    # ── BOOK 135: FRACTIONAL DIFFERENTIATION FEATURES ──
    frac_diff_value = None
    try:
        from python_brain.features.fractional_diff import get_fracdiff
        symbol = ticker_symbols.get(ticker_id, "")
        if symbol and len(ticks) >= 2:
            _fd = get_fracdiff(symbol)
            frac_diff_value = _fd.update(msg["last"])
    except ImportError:
        pass
    except Exception:
        pass

    return {
        "bars_5m": bars_5m, "n_5min_bars": n_5min_bars,
        "rvol": rvol, "hurst": hurst, "hurst_regime": hurst_regime,
        "vol_div": vol_div, "adx": adx, "vol_slope": vol_slope,
        "vpin": vpin, "ibs": ibs,
        "vwap_price": vwap_price, "vwap_sigma": vwap_sigma, "vwap_slope": vwap_slope,
        "vwap_dist_pct": vwap_dist_pct,
        "bid": bid, "ask": ask, "spread_pct": spread_pct,
        "structural_score": structural_score, "sts_components": sts,
        "frac_diff_value": frac_diff_value,
    }


def _check_quality_gates(ticker_id, msg, ticks, ind):
    """Stage 2: Quality gates. Returns (pass, reason) tuple."""
    leverage = msg.get("leverage", 1)

    # G1: Spread gate (skip in SIM_MODE)
    if not _SIM_MODE and ind["bid"] > 0 and ind["ask"] > 0:
        base_raw = _adaptive_spread_veto if _adaptive_spread_veto is not None else _cost_model.spread_veto_pct
        base_gate = base_raw * 100
        spread_limit = base_gate * 15.0 if leverage >= 3 else base_gate * 5.0
        if ind["spread_pct"] > spread_limit:
            return False, "spread_too_wide", "spread={:.2f}% > {:.1f}%".format(ind["spread_pct"], spread_limit)

    # G2: VWAP extension (absolute, both directions)
    vc = vwap_calculators.get(ticker_id)
    vh = vc.get_history() if vc else []
    if not _SIM_MODE and vh and len(ticks) > 30:
        lv = vh[-1]
        if lv > 0:
            ext = abs(msg["last"] - lv) / lv * 100
            if ext > 15.0:
                return False, "vwap_extension_5pct", "extension={:.1f}% from VWAP (max 15%)".format(ext)

    # G3: VWAP directional extension (long-only chasing check)
    if not _SIM_MODE and vh and len(ticks) > 60:
        lv = vh[-1]
        if lv > 0:
            vd = (msg["last"] - lv) / lv * 100
            if vd > 10.0:
                return False, "vwap_extension", "price {:.1f}% above VWAP (max 10.0%)".format(vd)

    # G4: Structural tradability minimum
    if not _SIM_MODE and ind["structural_score"] < 15:
        return False, "structural_tradability", "STS={}/100 < 15 minimum".format(ind["structural_score"])

    # G5: Hurst extreme mean-reversion
    if not _SIM_MODE and ind["n_5min_bars"] >= 5 and ind["hurst"] > 0.01 and ind["hurst"] < 0.10:
        return False, "hurst_mean_reverting", "hurst={:.3f} < 0.10".format(ind["hurst"])

    # G6: Ouroboros indicator gates
    gates = [] if _SIM_MODE else _load_indicator_gates()
    indicator_values = {"adx": ind["adx"], "hurst": ind["hurst"], "rvol": ind["rvol"]}
    for gate in gates:
        g_ind = gate.get("indicator", "")
        direction = gate.get("direction", "above")
        threshold = gate.get("threshold", 0)
        val = indicator_values.get(g_ind)
        if val is not None:
            if direction == "above" and val < threshold:
                return False, "indicator_gate", "{} {:.2f} < {:.2f} required".format(g_ind, val, threshold)
            elif direction == "below" and val > threshold:
                return False, "indicator_gate", "{} {:.2f} > {:.2f} limit".format(g_ind, val, threshold)

    # ── BOOK 7: CONCENTRATION RISK CHECKS (33-36) ──
    # Pre-signal gates: correlation, country, session, time-of-day
    if not _SIM_MODE:
        try:
            from python_brain.risk.concentration_checks import (
                check_time_of_day_risk, get_spike_detector,
            )
            from datetime import datetime as _dt_cc, timezone as _tz_cc
            ts_ns = msg.get("timestamp_ns", 0)
            if ts_ns > 0:
                utc_hour = _dt_cc.fromtimestamp(ts_ns / 1_000_000_000, tz=_tz_cc.utc).hour
                tod_result = check_time_of_day_risk(utc_hour)
                # Store scaling factor for later confidence adjustment
                msg["_tod_scale"] = tod_result.value

            # Correlation spike detector — feeds per-tick returns
            symbol = ticker_symbols.get(ticker_id, "")
            bars = list(bar_history[ticker_id])
            if len(bars) >= 2 and bars[-2].get("last", 0) > 0:
                ret = (bars[-1]["last"] - bars[-2]["last"]) / bars[-2]["last"]
                spike_action = get_spike_detector().on_tick(symbol, ret, msg.get("timestamp_ns", 0))
                if spike_action == "FLATTEN":
                    return False, "corr_spike_flatten", "correlation spike: FLATTEN action"
                elif spike_action == "REDUCE":
                    msg["_corr_spike_reduce"] = True
        except ImportError:
            pass

    # ── BOOK 12/177: HARD TIME-OF-DAY BLOCK ──
    # Block entries in worst microstructure windows: first 15min, last 30min, ETP rebalance
    if not _SIM_MODE:
        try:
            from datetime import datetime as _dt_tod, timezone as _tz_tod
            _ts = msg.get("timestamp_ns", 0)
            if _ts > 0:
                _dt = _dt_tod.fromtimestamp(_ts / 1_000_000_000, tz=_tz_tod.utc)
                _h, _m = _dt.hour, _dt.minute
                _mins = _h * 60 + _m
                # Block: LSE first 15 min (08:00-08:15 = 480-495)
                if 480 <= _mins < 495:
                    return False, "tod_open_avoid", "first 15min of LSE open (wide spreads)"
                # Block: ETP rebalancing window (16:00-16:35 = 960-995)
                if 960 <= _mins < 995:
                    return False, "tod_rebalance_avoid", "ETP rebalancing window 16:00-16:35"
                # Block: last 10 min of LSE (16:20-16:30 = 980-990) — already covered above
        except Exception:
            pass

    # ── BOOK 24: MACRO EVENT HARD BLOCK ──
    # Block entries within 5 min of Tier 1 macro releases (FOMC, CPI, NFP)
    if not _SIM_MODE:
        try:
            from python_brain.events.event_calendar import get_event_calendar
            if not hasattr(_quality_gates, "_evt_cal"):
                _quality_gates._evt_cal = get_event_calendar()
            _near = _quality_gates._evt_cal.is_near_event(msg.get("timestamp_ns", 0))
            if _near and getattr(_near, 'tier', 0) == 1:
                _mins_to = getattr(_near, 'minutes_to_release', 999)
                if 0 < _mins_to <= 5:
                    return False, "macro_event_imminent", f"{_near.name} in {_mins_to}m — block"
        except ImportError:
            pass
        except Exception:
            pass

    # ── BOOK 40: EARNINGS PROXIMITY GATE (single-stock 3x ETPs) ──
    # 3x single-stock ETPs amplify earnings gaps 3.5-5x.
    # Block new entries on earnings day. Existing positions get forced max_hold_hours=0
    # (handled downstream in the adjustment layer, not here).
    if not _SIM_MODE:
        try:
            _sym = ticker_symbols.get(ticker_id, "")
            _underlying = _ETP_UNDERLYING_MAP.get(_sym)
            if _underlying:
                _ed = _load_earnings_dates()
                _next_earn = _ed.get(_underlying, "")
                if _next_earn:
                    from datetime import datetime as _dt_earn, timezone as _tz_earn
                    _ts_earn = msg.get("timestamp_ns", 0)
                    if _ts_earn > 0:
                        _now_dt = _dt_earn.fromtimestamp(_ts_earn / 1_000_000_000, tz=_tz_earn.utc)
                        _earn_dt = _dt_earn.strptime(_next_earn, "%Y-%m-%d").replace(tzinfo=_tz_earn.utc)
                        _days_to = (_earn_dt.date() - _now_dt.date()).days
                        if _days_to == 0:
                            return False, "earnings_day_block", f"{_sym} underlying {_underlying} earnings TODAY"
                        elif _days_to == 1:
                            # T-1: allow entry but cap hold time to force exit before close
                            msg["_earnings_tomorrow"] = True
                            msg["_earnings_underlying"] = _underlying
        except Exception:
            pass  # Fail-open: trade if earnings lookup fails

    # ── REGIME-SCALED DAILY LOSS LIMIT (Book 85 + Book 15: 6-signal composite) ──
    # Uses 6-signal regime composite (VIX, credit, breadth, DXY, yield curve, SPX momentum)
    # instead of VIX-only for more robust regime detection.
    if not _SIM_MODE:
        _daily_pnl_pct = msg.get("daily_pnl_pct", 0)
        _reg_score, _reg_name, _reg_details = _regime_composite_score(msg)
        msg["_regime_composite_score"] = _reg_score
        msg["_regime_name"] = _reg_name
        _REGIME_DAILY_LIMITS = {"CRISIS": -1.5, "WOI": -2.0, "INFLATION": -2.5, "STEADY": -3.0}
        _daily_limit = _REGIME_DAILY_LIMITS.get(_reg_name, -3.0)
        if _daily_pnl_pct < _daily_limit:
            return False, "daily_loss_limit", \
                f"daily PnL {_daily_pnl_pct:.1f}% < regime-adjusted limit {_daily_limit:.1f}% (VIX={_vix_dl:.0f})"

    # ── WEEKLY LOSS LIMIT (Book 85) ──
    # Prevents multi-day compounding losses. Daily gate resets each day, allowing
    # 5 consecutive -1.9% days (-9.5% weekly) without triggering.
    if not _SIM_MODE:
        _weekly_pnl_pct = msg.get("weekly_pnl_pct", 0)
        if _weekly_pnl_pct != 0:
            # Use 6-signal composite regime (already computed above for daily limit)
            _REGIME_WEEKLY_LIMITS = {"CRISIS": -2.0, "WOI": -4.0, "INFLATION": -5.5, "STEADY": -7.0}
            _weekly_limit = _REGIME_WEEKLY_LIMITS.get(msg.get("_regime_name", "STEADY"), -7.0)
            if _weekly_pnl_pct < _weekly_limit:
                return False, "weekly_loss_limit", \
                    f"weekly PnL {_weekly_pnl_pct:.1f}% < regime-adjusted limit {_weekly_limit:.1f}%"

    # ── REGIME-SCALED RISK PER TRADE (Book 85 + Book 15 composite) ──
    # Dynamic risk-per-trade based on 6-signal composite regime.
    if not _SIM_MODE:
        _REGIME_RPT = {"CRISIS": 0.002, "WOI": 0.004, "INFLATION": 0.006, "STEADY": 0.0075}
        msg["_regime_risk_per_trade"] = _REGIME_RPT.get(msg.get("_regime_name", "STEADY"), 0.0075)

    # ── REGIME-ADAPTIVE COOLDOWN (Book 85) ──
    # Longer cooldowns between trades in hostile regimes.
    if not _SIM_MODE:
        _vix_cd = msg.get("vix", 20)
        if _vix_cd > 30:
            _regime_cooldown_ticks = 360  # ~30 min at 5s ticks (CRISIS)
        elif _vix_cd > 22:
            _regime_cooldown_ticks = 180  # ~15 min (WOI)
        elif _vix_cd > 15:
            _regime_cooldown_ticks = 120  # ~10 min (INFLATION)
        else:
            _regime_cooldown_ticks = 60  # ~5 min (STEADY)
        # Store for use in _apply_adjustments cooldown check
        msg["_regime_cooldown_ticks"] = _regime_cooldown_ticks

    # ── FLASH CRASH DETECTION ──
    # If price dropped > 3% in last 5 bars, likely flash crash or halt imminent.
    # Block entries until price stabilizes.
    if not _SIM_MODE and ind.get("bars_5m") and len(ind["bars_5m"]) >= 5:
        _recent_bars = ind["bars_5m"][-5:]
        _max_high = max(b["high"] for b in _recent_bars)
        _current = msg.get("last", 0)
        if _max_high > 0 and (_max_high - _current) / _max_high > 0.03:
            return False, "flash_crash_detected", \
                f"price dropped {(_max_high - _current) / _max_high * 100:.1f}% in last 5 bars"

    # ── WEEKEND / HOLIDAY PROXIMITY ──
    # Friday after 15:00 UTC: reduce appetite for new entries (weekend gap risk).
    # This doesn't block — just prevents the gate from passing for marginal signals.
    if not _SIM_MODE:
        try:
            from datetime import datetime as _dt_wk, timezone as _tz_wk
            _ts_wk = msg.get("timestamp_ns", 0)
            if _ts_wk > 0:
                _dt_wk_now = _dt_wk.fromtimestamp(_ts_wk / 1_000_000_000, tz=_tz_wk.utc)
                if _dt_wk_now.weekday() == 4 and _dt_wk_now.hour >= 15:
                    # Friday late session: only allow high-conviction entries
                    if ind.get("structural_score", 100) < 50:
                        return False, "friday_late_low_quality", \
                            f"Friday late session with STS={ind.get('structural_score', 0)}"
        except Exception:
            pass

    # ── VIX > 50: FULL TRADING HALT (sacred limit) ──
    _vix_gate = msg.get("vix", 20)
    if not _SIM_MODE and _vix_gate > 50:
        return False, "vix_full_halt", f"VIX={_vix_gate:.1f} > 50: FULL HALT"

    # ── VIX > 35: BLOCK ALL 3x+ LONG ENTRIES ──
    if not _SIM_MODE and _vix_gate > 35:
        _leverage_gate = msg.get("leverage", 1)
        if _leverage_gate >= 3:
            _sym_gate = ticker_symbols.get(ticker_id, "")
            _is_inv = any(inv in _sym_gate.upper() for inv in ("QQQS", "3USS", "SUK2", "NV3S", "TS3S", "3S", "5S"))
            if not _is_inv:
                return False, "vix_crisis_3x_block", \
                    f"VIX={_vix_gate:.1f} > 35: 3x long entries blocked in crisis"

    # ── MAX 20% NAV IN ANY SINGLE 3x ETP ──
    if not _SIM_MODE:
        _leverage_cap = msg.get("leverage", 1)
        if _leverage_cap >= 3:
            _eq_cap = msg.get("equity", 10000)
            _existing_pos_value = 0
            _sym_cap = ticker_symbols.get(ticker_id, "")
            _open_pos = msg.get("open_positions", [])
            _open_pos = _open_pos if isinstance(_open_pos, list) else []
            for _p in _open_pos:
                if _p.get("symbol", "") == _sym_cap:
                    _existing_pos_value += _p.get("market_value_gbp", 0)
            if _eq_cap > 0 and _existing_pos_value / _eq_cap > 0.20:
                return False, "single_etp_concentration", \
                    f"{_sym_cap}: {_existing_pos_value/_eq_cap*100:.1f}% of NAV > 20% limit for 3x ETP"

    # ── SPREAD-TO-AVERAGE RATIO GATE ──
    # If current spread is 3x+ the instrument's normal spread, block entry.
    if not _SIM_MODE and ind.get("spread_pct", 0) > 0:
        _sym_spread = ticker_symbols.get(ticker_id, "")
        try:
            from python_brain.analytics.microstructure import get_micro_state
            _ms = get_micro_state(ticker_id)
            _median_spread = getattr(_ms, 'median_spread_pct', None)
            if _median_spread and _median_spread > 0:
                _spread_ratio = ind["spread_pct"] / _median_spread
                if _spread_ratio > 3.0:
                    return False, "spread_ratio_extreme", \
                        f"spread {ind['spread_pct']:.2f}% is {_spread_ratio:.1f}x normal ({_median_spread:.2f}%)"
                elif _spread_ratio > 2.0:
                    # Don't block but store for sizing reduction in _apply_adjustments
                    msg["_spread_ratio_reduce"] = 0.5
        except Exception:
            pass

    # ── CAPITAL-PHASE STRATEGY FILTER ──
    # At £10K only run highest-edge strategies. Diversifying fragments capital.
    if not _SIM_MODE:
        _eq_phase = msg.get("equity", 10000)
        if _eq_phase < 25000:
            # Phase 1: Only momentum + regime-switch strategies (highest edge-to-cost)
            _PHASE1_ALLOWED = {
                "Momentum", "VolExpansion", "S1_Microstructure", "S3_MacroTrend",
                "Orchestrator_trend_follow", "Orchestrator_momentum_burst",
                "IBS_MeanReversion", "S2_Reversion",  # Keep MR as counterbalance
                "S7_TailHedge", "S5_OvernightCarry",
                "ApexScout",
            }
            # Don't filter here — just store for _generate_signals
            msg["_phase1_strategies"] = _PHASE1_ALLOWED
        elif _eq_phase < 50000:
            # Phase 2: add vol_breakout, ORB, gap
            msg["_phase2_enabled"] = True
        # Phase 3+: all strategies enabled (no filter)

    # ── STALE TICK SUPPRESSION ──
    if not _SIM_MODE:
        _ts_fresh = msg.get("timestamp_ns", 0)
        if _ts_fresh > 0:
            _age_ms = (time.time() * 1e9 - _ts_fresh) / 1e6  # age in milliseconds
            if _age_ms > 30000:  # > 30 seconds stale
                return False, "stale_tick", f"tick age {_age_ms/1000:.1f}s > 30s limit"
            elif _age_ms > 500:
                msg["_stale_tick_penalty"] = True  # Flag for confidence reduction later

    # ── ERRONEOUS TICK / PRICE SPIKE FILTER ──
    if not _SIM_MODE and ind.get("bars_5m") and len(ind["bars_5m"]) >= 10:
        _ema_prices = [b["close"] for b in ind["bars_5m"][-10:]]
        _ema = sum(_ema_prices) / len(_ema_prices)
        _current_price = msg.get("last", 0)
        if _ema > 0 and _current_price > 0:
            _deviation = abs(_current_price - _ema) / _ema
            if _deviation > 0.05:  # 5% deviation from EMA = likely erroneous
                return False, "price_spike", \
                    f"price {_current_price:.4f} deviates {_deviation*100:.1f}% from EMA {_ema:.4f}"
        # Crossed bid/ask check
        _bid = msg.get("bid", 0)
        _ask = msg.get("ask", 0)
        if _bid > 0 and _ask > 0 and _ask < _bid:
            return False, "crossed_quotes", f"ask {_ask:.4f} < bid {_bid:.4f}"

    # ── BOOK 49: LSE LIQUID-WINDOW-ONLY FOR TIER 2/3 ETPs ──
    # ADV < £1M ETPs: only trade 10:00-12:00 and 14:30-15:30 GMT (50% of daily volume)
    if not _SIM_MODE:
        _sym_liq = ticker_symbols.get(ticker_id, "")
        _exch_liq = _get_exchange_for_symbol(_sym_liq) if _sym_liq else ""
        if _exch_liq == "LSE":
            _adv_liq = msg.get("adv_gbp", 0)
            if 0 < _adv_liq < 1_000_000:  # Tier 2/3 (ADV < £1M)
                _ts_liq = msg.get("timestamp_ns", 0)
                if _ts_liq > 0:
                    from datetime import datetime as _dt_liq, timezone as _tz_liq
                    _utc_liq = _dt_liq.fromtimestamp(_ts_liq / 1e9, tz=_tz_liq.utc)
                    _mins_liq = _utc_liq.hour * 60 + _utc_liq.minute
                    # Allow: 10:00-12:00 (600-720) and 14:30-15:30 (870-930) GMT
                    if not (600 <= _mins_liq < 720 or 870 <= _mins_liq < 930):
                        return False, "illiquid_window", \
                            f"Tier2/3 ETP outside liquid windows (ADV=£{_adv_liq/1e6:.1f}M)"

    # ── BOOK 49/90: PER-ORDER ADV PARTICIPATION LIMIT ──
    if not _SIM_MODE:
        try:
            from python_brain.execution.adv_participation import check_participation
            _adv_check = msg.get("adv_gbp", 0)
            _eq_adv = msg.get("equity", 10000)
            if _adv_check > 0:
                _order_est = _eq_adv * 0.05  # rough Kelly * equity
                _adv_ok, _adv_scale = check_participation(
                    ticker_symbols.get(ticker_id, ""), _order_est, _adv_check)
                if not _adv_ok:
                    return False, "adv_participation", \
                        f"order exceeds 2% ADV (ADV=£{_adv_check:.0f})"
                if _adv_scale < 1.0:
                    msg["_adv_scale"] = _adv_scale
        except ImportError:
            pass

    # ── BOOK 53: BROKER ERROR CIRCUIT BREAKER ──
    if not _SIM_MODE:
        _broker_errors = msg.get("broker_errors_60s", 0)
        if _broker_errors >= 5:
            return False, "broker_circuit_breaker", \
                f"{_broker_errors} broker errors in last 60s — cooling off"
        _fill_error_rate = msg.get("fill_error_rate", 0)
        if _fill_error_rate > 0.05:
            return False, "fill_error_rate", \
                f"fill error rate {_fill_error_rate*100:.1f}% > 5%"

    # ── BOOK 94: PER-STRATEGY CLOSE CUTOFF CONTEXT ──
    # Store minutes-to-close for downstream strategy-specific filtering
    if not _SIM_MODE:
        try:
            _ts_cutoff = msg.get("timestamp_ns", 0)
            if _ts_cutoff > 0:
                from datetime import datetime as _dt_cut, timezone as _tz_cut, timedelta as _td_cut
                _utc_cut = _dt_cut.fromtimestamp(_ts_cutoff / 1e9, tz=_tz_cut.utc)
                _sym_cut = ticker_symbols.get(ticker_id, "")
                _exch_cut = _get_exchange_for_symbol(_sym_cut) if _sym_cut else ""
                _off_cut = _exchange_utc_offset(_exch_cut, _utc_cut)
                _local_cut = _utc_cut + _td_cut(hours=_off_cut)
                _lm_cut = _local_cut.hour * 60 + _local_cut.minute
                _CLOSE_MAP = {"LSE": 990, "XETRA": 1050, "EURONEXT": 1050, "US": 960,
                              "HKEX": 960, "TSE": 900, "SGX": 1020}
                _close_min = _CLOSE_MAP.get(_exch_cut, 960)
                msg["_mins_to_close"] = _close_min - _lm_cut
        except Exception:
            pass

    # ── BOOK 24: EVENT-SPECIFIC GRADUATED BLACKOUTS ──
    # Upgrade from binary macro block to per-event timing with drift windows
    if not _SIM_MODE:
        try:
            from python_brain.strategies.fomc_drift import get_event_blackout
            _ts_evt = msg.get("timestamp_ns", 0)
            _blackout = get_event_blackout(_ts_evt)
            if _blackout and _blackout.hard_block:
                return False, "event_blackout", \
                    f"{_blackout.event_name} in {_blackout.minutes_to:.0f}m — hard block"
            if _blackout:
                msg["_event_context"] = _blackout
        except ImportError:
            pass

    # ── BOOK 177/179: MINIMUM EXPECTED RETURN VIABILITY ──
    if not _SIM_MODE:
        _spread_viab = ind.get("spread_pct", 0)
        if _spread_viab > 0.80:
            return False, "spread_unviable", \
                f"spread {_spread_viab:.2f}% > 80bp — cost exceeds any reasonable alpha"
        _eq_viab = msg.get("equity", 10000)
        _pos_size_est = max(_eq_viab * 0.02, 100)
        _commission_drag = 3.40 / _pos_size_est  # £3.40 RT on estimated position
        if _commission_drag > 0.005 and _spread_viab > 0.30:
            return False, "cost_unviable", \
                f"commission drag {_commission_drag*100:.1f}% + spread {_spread_viab:.1f}% exceeds edge"

    # ── BOOK 94: HALF-DAY SESSION BOUNDARY ADJUSTMENT ──
    if not _SIM_MODE:
        try:
            from python_brain.execution.calendar_manager import is_half_day
            _ts_hd = msg.get("timestamp_ns", 0)
            if _ts_hd > 0 and is_half_day(_ts_hd):
                msg["_half_day"] = True
                if "_mins_to_close" in msg:
                    msg["_mins_to_close"] -= 210  # Shift 3.5h earlier
        except ImportError:
            pass

    return True, None, None


def _compute_confidence_floor(msg, ind):
    """Compute effective confidence floor from leverage, adaptive params, and regime."""
    leverage = msg.get("leverage", 1)
    if leverage >= 5:
        floor = 70
    elif leverage >= 3:
        floor = 50
    else:
        floor = 40

    adaptive_floor = _load_adaptive_floor()
    if adaptive_floor is not None:
        floor = max(floor, adaptive_floor)

    # Strongly mean-reverting → raise floor (tightened from H<0.50 to H<0.30 for paper validation)
    # H<0.50 was blocking ~80% of signals since random walk (0.45-0.55) was included.
    # Only truly mean-reverting regimes (H<0.30) should raise the floor.
    if not _SIM_MODE and ind["n_5min_bars"] >= 5 and 0.01 < ind["hurst"] < 0.30:
        floor = max(floor, 60)

    # Falling volume → moderate floor raise (tightened from 75 to 60 for paper validation)
    has_volume = any(b.get("volume", 0) > 0 for b in ind["bars_5m"][-5:]) if ind["bars_5m"] else False
    if not _SIM_MODE and ind["n_5min_bars"] >= 5 and has_volume and ind["vol_slope"] < -0.5:
        floor = max(floor, 60)

    # ── BOOK 15/85: VIX REGIME TIER CONFIDENCE FLOOR ──
    # Higher VIX = higher confidence required (only strongest signals pass)
    _vix = msg.get("vix", 21.0)
    if _vix > 35:
        floor = max(floor, 65)  # Crisis: only highest conviction
    elif _vix > 25:
        floor = max(floor, 60)  # WOI: elevated
    elif _vix > 20:
        floor = max(floor, 55)  # Caution

    # ── BOOK 171: DAY-OF-WEEK FLOOR ADJUSTMENT ──
    # Monday has negative drift (-0.8bps) — raise floor for momentum entries
    try:
        from datetime import datetime as _dt_dow, timezone as _tz_dow
        _ts_dow = msg.get("timestamp_ns", 0)
        if _ts_dow > 0:
            _dow = _dt_dow.fromtimestamp(_ts_dow / 1_000_000_000, tz=_tz_dow.utc).weekday()
            if _dow == 0:  # Monday
                floor = max(floor, floor + 5)  # Harder to enter on Mondays
    except Exception:
        pass

    return floor


def _validate_breakout_3criteria(bars_5m, breakout_level, breakout_is_long=True):
    """Book 22: Validate breakout with 3-criteria gate.

    Requirements for high-confidence breakout (68% WR):
    1. Close beyond level (NOT just wick) — 68% vs 31% wick-only WR
    2. Volume >= 1.5x 20-bar average — WR crosses 60% at 1.5x
    3. No reversal within 3 bars — 3-bar hold required

    Args:
        bars_5m: List of OHLCV bars (5-minute)
        breakout_level: Price level being broken (e.g., resistance/support)
        breakout_is_long: True for long breakout (price > level), False for short

    Returns:
        Dictionary with:
        - criteria_met: Count of criteria met (0-3)
        - confidence_adj: Adjustment to apply (-8 or +10)
        - failed_criteria: List of which criteria failed (for logging)
        - details: Dict with individual criterion status
    """
    if not bars_5m or len(bars_5m) < 4:  # Need current + 3 bars ahead for hold check
        return {
            "criteria_met": 0,
            "confidence_adj": -8,
            "failed_criteria": ["insufficient_data"],
            "details": {}
        }

    current_bar = bars_5m[-1]
    current_close = current_bar["close"]

    # Criterion 1: Close beyond level (not just wick)
    criterion1_pass = False
    if breakout_is_long:
        criterion1_pass = current_close > breakout_level
    else:
        criterion1_pass = current_close < breakout_level

    # Criterion 2: Volume >= 1.5x 20-bar average
    criterion2_pass = False
    if len(bars_5m) >= 20:
        volumes_20 = [b["volume"] for b in bars_5m[-20:]]
        avg_vol_20 = sum(volumes_20) / len(volumes_20)
        current_vol = current_bar["volume"]
        criterion2_pass = current_vol >= (1.5 * avg_vol_20)

    # Criterion 3: No reversal within 3 bars
    # Current bar is at index -1. Check bars at -2, -3, -4 (next 3 bars after current in time series)
    criterion3_pass = True
    num_bars_to_check = min(3, len(bars_5m) - 1)  # Can't check more than available future bars
    if num_bars_to_check > 0:
        for bar_offset in range(1, num_bars_to_check + 1):
            if len(bars_5m) >= bar_offset + 1:  # Make sure we have that bar
                check_bar = bars_5m[-1 - bar_offset]  # -2, -3, -4 from current (-1)
                check_close = check_bar["close"]
                if breakout_is_long and check_close < breakout_level:
                    criterion3_pass = False
                    break
                elif not breakout_is_long and check_close > breakout_level:
                    criterion3_pass = False
                    break

    # Count criteria met
    criteria_met = sum([criterion1_pass, criterion2_pass, criterion3_pass])

    # Determine confidence adjustment
    if criteria_met == 3:
        confidence_adj = +10
    elif criteria_met <= 1:
        confidence_adj = -8
    else:
        confidence_adj = 0  # 2 criteria met = neutral (not penalized but not boosted)

    # Log which criteria failed
    failed_criteria = []
    if not criterion1_pass:
        failed_criteria.append("close_not_beyond")
    if not criterion2_pass:
        failed_criteria.append("low_volume")
    if not criterion3_pass:
        failed_criteria.append("reversal_detected")

    return {
        "criteria_met": criteria_met,
        "confidence_adj": confidence_adj,
        "failed_criteria": failed_criteria,
        "details": {
            "criterion1_close_beyond": criterion1_pass,
            "criterion2_volume_1_5x": criterion2_pass,
            "criterion3_no_reversal_3bar": criterion3_pass,
        }
    }


def _system1_microstructure(ticker_id, msg, ticks, ind, conf_floor, kelly_fn, common_fields):
    """System 1: Microstructure Momentum — order flow proxy + intraday momentum.

    6 indicators (Easley-LdP-O'Hara 2012, Gao-Ritter 2010, Chordia-Roll-Subrahmanyam 2002):
    1. TMR: Trade-to-mid ratio — buy/sell pressure
    2. VPIN: Volume-sync informed trading probability
    3. Spread compression: narrowing = competition for liquidity
    4. Tick momentum: net up-ticks vs down-ticks
    5. Volume-weighted price momentum: VWAP slope direction
    6. Amihud illiquidity drop: improving liquidity = institutional interest

    Entry: 4+ of 6 bullish + ADX > 15 + hurst != mean_reverting.
    """
    bid, ask, last = msg["bid"], msg["ask"], msg["last"]
    spread = ask - bid
    if spread <= 0 or bid <= 0:
        return None
    mid = (bid + ask) / 2.0

    # 1. TMR — where trades execute in the spread
    tmr = (last - mid) / spread
    tmr_bullish = tmr > 0.25

    # 2. VPIN — informed trading detection
    vpin = ind.get("vpin", 0.5)
    vpin_bullish = vpin > 0.55

    # 3. Spread compression — current vs rolling average
    spreads = []
    for t in ticks[-30:]:
        tb = t.get("bid", 0) if isinstance(t, dict) else getattr(t, "bid", 0)
        ta = t.get("ask", 0) if isinstance(t, dict) else getattr(t, "ask", 0)
        if tb > 0 and ta > tb:
            spreads.append((ta - tb) / tb)
    if len(spreads) >= 10:
        avg_s = sum(spreads) / len(spreads)
        cur_s = spread / bid
        spread_compressed = cur_s < avg_s * 0.75
    else:
        spread_compressed = False

    # 4. Tick momentum — Lee-Ready (1991) tick test
    prices = []
    for t in ticks[-31:]:
        prices.append(t.get("last", t.get("close", 0)) if isinstance(t, dict) else getattr(t, "last", 0))
    if len(prices) < 10:
        return None
    up = sum(1 for i in range(1, len(prices)) if prices[i] > prices[i-1])
    dn = sum(1 for i in range(1, len(prices)) if prices[i] < prices[i-1])
    total = up + dn
    tick_ratio = up / total if total > 0 else 0.5
    tick_bullish = tick_ratio > 0.58

    # 5. VWAP slope — Gao-Ritter (2010) intraday momentum
    vwap_slope = ind.get("vwap_slope", 0) if "vwap_slope" not in common_fields else common_fields.get("vwap_slope", 0)
    # Compute from raw data if not available
    if vwap_slope == 0 and len(prices) >= 10:
        # Simple price-volume weighted slope over last 10 ticks
        n = min(10, len(prices) - 1)
        recent = prices[-n-1:]
        rets = [(recent[i+1] - recent[i]) / recent[i] for i in range(n) if recent[i] > 0]
        vwap_slope = sum(rets) / len(rets) if rets else 0
    vwap_bullish = vwap_slope > 0.0005  # 0.05% positive slope

    # 6. Amihud illiquidity drop — decreasing illiquidity = institutional inflow
    amihud = msg.get("amihud", 0)
    amihud_bullish = amihud < 0.01 and ind.get("rvol", 1.0) > 1.0

    # Count aligned indicators
    signals = [tmr_bullish, vpin_bullish, spread_compressed, tick_bullish, vwap_bullish, amihud_bullish]
    bullish_count = sum(signals)

    # Entry gate: 4+ of 6 + ADX > 15 + not mean-reverting regime
    adx = ind.get("adx", 0)
    hurst_regime = ind.get("hurst_regime", "random")
    if bullish_count < 4 or adx < 15.0 or hurst_regime == "mean_reverting":
        return None

    # Graduated confidence with regime conditioning
    conf = 52.0
    conf += (bullish_count - 4) * 4.0
    if adx > 25.0:
        conf += 8.0
    if adx > 35.0:
        conf += 4.0
    if ind.get("rvol", 1.0) > 2.0:
        conf += 5.0
    if tmr > 0.5:
        conf += 3.0
    if hurst_regime == "trending":
        conf += 5.0  # Momentum works best in trending regime
    # Book 21: D-VPIN bonus — if informed buying detected, boost confidence
    d_vpin_val = common_fields.get("d_vpin", 0)
    if d_vpin_val > 0.30:
        conf += 4.0  # Informed buyers on our side
    # Book 22: Keltner squeeze release — 67% WR breakout signal
    if common_fields.get("squeeze_release", False):
        conf += 8.0  # Volatility expanding after compression = strong breakout
    # Penalize if structural score is low (poor tradability)
    ss = ind.get("structural_score", 50)
    if ss < 40:
        conf -= 5.0
    conf = max(0, min(conf, 95.0))

    if conf < conf_floor:
        return None

    # Live stats for adaptive sizing
    stats = _strategy_live_stats("S1_Microstructure")
    # If we have enough data and Sharpe is negative, reduce confidence
    if stats["n"] >= 30 and stats["sharpe"] < -0.5:
        conf *= 0.7

    kelly = kelly_fn(conf)
    _track_strategy_entry(ticker_id, "S1_Microstructure", last)
    return {
        "type": "signal", "ticker_id": ticker_id, "direction": "Long",
        "confidence": conf,
        "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
        "strategy": "S1_Microstructure",
        "s1_tmr": round(tmr, 3), "s1_vpin": round(vpin, 3),
        "s1_spread_compressed": spread_compressed, "s1_tick_ratio": round(tick_ratio, 3),
        "s1_bullish_count": bullish_count, "s1_vwap_slope": round(vwap_slope, 6),
        "s1_live_sharpe": stats["sharpe"],
        **common_fields,
    }


def _system2_reversion(ticker_id, msg, bars_5m, ind, conf_floor, kelly_fn, common_fields):
    """System 2: Statistical Reversion — multi-factor oversold detection.

    Academic basis: Connors & Alvarez (2008), Bollinger (2001), Jegadeesh (1990).
    5 factors: BB z-score, RSI(2), IBS, volume capitulation, mean-reversion speed.
    Regime gate: mean_reverting or random only (never trending — that's S3's job).
    """
    if len(bars_5m) < 20:
        return None

    hurst_regime = ind.get("hurst_regime", "random")
    if hurst_regime == "trending":
        return None

    closes = [b["close"] for b in bars_5m[-20:]]
    current = closes[-1]

    # 1. BB z-score (20-bar)
    sma20 = sum(closes) / len(closes)
    variance = sum((c - sma20) ** 2 for c in closes) / len(closes)
    std20 = variance ** 0.5
    if std20 < 1e-9:
        return None
    z_score = (current - sma20) / std20

    # 2. RSI(2) — Connors short-term oversold
    rsi2 = calculate_rsi(closes, period=2)

    # 3. IBS (Internal Bar Strength)
    ibs_val = ind.get("ibs", 0.5)

    # 4. Volume capitulation — RVOL spike on down move = panic selling exhaustion
    rvol = ind.get("rvol", 1.0)
    last_3_down = sum(1 for b in bars_5m[-3:] if b["close"] < b["open"])
    vol_capitulation = rvol > 2.0 and last_3_down >= 2

    # 5. Mean-reversion speed — how fast did price deviate? Fast = liquidity gap = fills faster
    if len(closes) >= 5:
        dev_speed = abs(closes[-1] - closes[-5]) / (std20 * 5) if std20 > 0 else 0
    else:
        dev_speed = 0

    # Scoring: each factor adds conviction
    score = 0
    if z_score < -1.5: score += 1
    if z_score < -2.0: score += 1
    if z_score < -2.5: score += 1
    if rsi2 is not None and rsi2 < 15: score += 1
    if rsi2 is not None and rsi2 < 5: score += 1
    if ibs_val < 0.25: score += 1
    if ibs_val < 0.10: score += 1
    if vol_capitulation: score += 2  # Strong signal — double weight
    if dev_speed > 0.5: score += 1  # Fast deviation = faster fill

    # Need score >= 4 (out of 10 possible)
    if score < 4:
        return None

    # Confidence maps score to conviction
    conf = 48.0 + score * 4.0  # 48 + 4*4=64 minimum, up to 48+40=88
    if hurst_regime == "mean_reverting":
        conf += 5.0  # Bonus: confirmed MR regime
    conf = max(0, min(conf, 90.0))

    if conf < conf_floor:
        return None

    # Adaptive: reduce if strategy has negative live Sharpe
    stats = _strategy_live_stats("S2_Reversion")
    if stats["n"] >= 30 and stats["sharpe"] < -0.5:
        conf *= 0.7

    kelly = kelly_fn(conf)
    _track_strategy_entry(ticker_id, "S2_Reversion", current)
    return {
        "type": "signal", "ticker_id": ticker_id, "direction": "Long",
        "confidence": conf,
        "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
        "strategy": "S2_Reversion",
        "s2_zscore": round(z_score, 3), "s2_rsi2": round(rsi2, 2) if rsi2 else 0,
        "s2_ibs": round(ibs_val, 4), "s2_score": score,
        "s2_vol_capitulation": vol_capitulation, "s2_dev_speed": round(dev_speed, 3),
        "s2_live_sharpe": stats["sharpe"],
        **common_fields,
    }


def _system3_macro_trend(ticker_id, msg, bars_5m, ind, conf_floor, kelly_fn, common_fields):
    """System 3: Macro Trend Following — multi-timeframe momentum.

    Academic basis: Moskowitz-Ooi-Pedersen (2012), Faber (2007), Asness-Moskowitz-Pedersen (2013).
    5 factors: dual MA crossover, 12-bar momentum, ADX trend strength, volume trend, Hurst confirmation.
    Thrives in trending + stress. ISA long-only: only long momentum.
    """
    if len(bars_5m) < 20:
        return None

    closes = [b["close"] for b in bars_5m]
    current = closes[-1]

    # 1. Dual MA crossover (fast/slow)
    sma5 = sum(closes[-5:]) / 5
    sma20 = sum(closes[-20:]) / 20
    ma_bullish = sma5 > sma20 and current > sma5

    # 2. 12-bar momentum (Moskowitz-style time-series momentum)
    if len(closes) >= 12:
        mom_12 = (current - closes[-12]) / closes[-12] if closes[-12] > 0 else 0
    else:
        mom_12 = 0
    mom_bullish = mom_12 > 0.005  # >0.5% gain over 12 bars (1 hour)

    # 3. ADX trend strength
    adx = ind.get("adx", 0)
    adx_strong = adx > 20.0

    # 4. Volume trend — increasing volume confirms trend
    vol_slope = ind.get("vol_slope", 0)
    vol_confirming = vol_slope > 0

    # 5. Hurst regime — trending confirmed
    hurst = ind.get("hurst", 0.5)
    hurst_regime = ind.get("hurst_regime", "random")
    hurst_trending = hurst_regime == "trending" or hurst > 0.55

    # Regime gate: reject in mean_reverting (choppy kills trend following)
    if hurst_regime == "mean_reverting":
        return None

    # Score: need 4+ of 5
    factors = [ma_bullish, mom_bullish, adx_strong, vol_confirming, hurst_trending]
    score = sum(factors)
    if score < 4:
        return None

    # Crossover strength for confidence scaling
    crossover_pct = (sma5 - sma20) / sma20 * 100.0 if sma20 > 0 else 0

    conf = 50.0
    conf += score * 4.0
    if crossover_pct > 0.3:
        conf += 5.0
    if adx > 30.0:
        conf += 5.0
    if mom_12 > 0.01:
        conf += 3.0  # Strong 1-hour momentum
    if hurst_trending:
        conf += 4.0
    conf = max(0, min(conf, 90.0))

    if conf < conf_floor:
        return None

    stats = _strategy_live_stats("S3_MacroTrend")
    if stats["n"] >= 30 and stats["sharpe"] < -0.5:
        conf *= 0.7

    kelly = kelly_fn(conf)
    _track_strategy_entry(ticker_id, "S3_MacroTrend", current)
    return {
        "type": "signal", "ticker_id": ticker_id, "direction": "Long",
        "confidence": conf,
        "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
        "strategy": "S3_MacroTrend",
        "s3_sma5": round(sma5, 4), "s3_sma20": round(sma20, 4),
        "s3_crossover_pct": round(crossover_pct, 3), "s3_mom12": round(mom_12, 5),
        "s3_score": score, "s3_live_sharpe": stats["sharpe"],
        **common_fields,
    }


def _system4_volatility(ticker_id, msg, ind, conf_floor, kelly_fn, common_fields):
    """System 4: Volatility Premium — VIX-driven inverse ETP trading.

    Low VIX (< 18): long inverse 3x ETPs (short vol premium).
    High VIX (> 25): long regular 3x ETPs (long vol rebound).
    ISA constraint: long-only, so we buy inverse ETPs to express short-vol view.
    Habitat: Index inverse pairs (3USS, QQQS). US session primarily.
    """
    vix = msg.get("vix", 0)
    if vix <= 0:
        return None

    symbol = ticker_symbols.get(ticker_id, "")
    is_inverse = symbol.startswith("3S") or symbol.startswith("QQQ S") or symbol in (
        "QQQS.L", "3USS.L", "3STS.L", "3SNV.L", "3SAP.L", "3SMS.L", "3SEM.L",
    )

    # Low VIX regime: buy inverse ETPs (expressing short vol)
    if vix < 18.0 and is_inverse:
        conf = 57.0
        if vix < 14.0:
            conf += 8.0  # Very low VIX = strong vol premium
        if ind.get("adx", 0) < 15.0:
            conf += 5.0  # Low trend = range-bound = vol selling works
        conf = min(conf, 78.0)
        if conf < conf_floor:
            return None
        kelly = kelly_fn(conf)
        return {
            "type": "signal", "ticker_id": ticker_id, "direction": "Long",
            "confidence": conf,
            "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
            "strategy": "S4_VolPremium", "s4_vix": round(vix, 1), "s4_mode": "short_vol",
            **common_fields,
        }

    # High VIX regime: buy regular (non-inverse) 3x ETPs (long vol rebound)
    if vix > 30.0 and not is_inverse:
        conf = 55.0
        if vix > 40.0:
            conf += 10.0  # Extreme fear = strong rebound potential
        conf = min(conf, 75.0)
        if conf < conf_floor:
            return None
        kelly = kelly_fn(conf)
        return {
            "type": "signal", "ticker_id": ticker_id, "direction": "Long",
            "confidence": conf,
            "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
            "strategy": "S4_VolPremium", "s4_vix": round(vix, 1), "s4_mode": "long_rebound",
            **common_fields,
        }

    return None


def _fomc_pre_drift_positioning(ticker_id, msg, ind, conf_floor, kelly_fn, common_fields):
    """Book 5: FOMC Pre-drift Positioning — position ahead of FOMC + ride post-event drift.

    Entry strategy:
    - T-1 (1 day before FOMC): Long positioning with dovish bias (confidence +15).
      Rationale: Pre-event uncertainty premium tends to resolve toward policy dovishness.
      Expected drift: +0.4% average through T+5.
    - T (FOMC day): Soft block via _event_context hard_block, but log for T+1 state.
    - T+1 to T+5: Drift continuation based on actual event outcome:
        * Dovish FOMC: Continue LONG (confidence +12).
        * Hawkish FOMC: Flip to INVERSE/SHORT proxy if available, else reduce (confidence +8).

    Exit: 6 days max hold (T+5 close). Win rate target: 70%+.

    Data flow:
    1. Detect FOMC scheduled date from event_calendar.json (via _load_calendar).
    2. Compare current date to FOMC date to determine positioning phase (T-1 / T+1..T+5).
    3. For post-event phase, check macro data (persistent_memory or context_store) for:
       - Fed funds rate decision (raise=hawkish, cut/hold=dovish)
       - Forward guidance tone (inflation concerns=hawkish, growth concerns=dovish)
    4. Generate signal with appropriate confidence and metadata.
    """
    ts_ns = msg.get("timestamp_ns", 0)
    if ts_ns <= 0:
        return None

    from datetime import datetime, timezone

    # Get current UTC date/time
    utc_dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
    current_date = utc_dt.date()

    # Load FOMC schedule
    try:
        from python_brain.strategies.fomc_drift import _load_calendar
        events = _load_calendar()
    except (ImportError, Exception):
        return None

    # Find next FOMC event within ±5 days
    fomc_event = None
    fomc_date = None
    for evt in events:
        if evt.get("type") != "FOMC":
            continue
        evt_date_str = evt.get("date")  # ISO format YYYY-MM-DD
        if evt_date_str:
            try:
                evt_date_obj = datetime.strptime(evt_date_str, "%Y-%m-%d").date()
                days_diff = (evt_date_obj - current_date).days
                if -5 <= days_diff <= 5:
                    fomc_event = evt
                    fomc_date = evt_date_obj
                    break
            except ValueError:
                continue

    if not fomc_event or not fomc_date:
        return None

    days_to_fomc = (fomc_date - current_date).days

    # ── PHASE 1: T-1 PRE-FOMC POSITIONING (1 day before) ──
    if days_to_fomc == 1:
        # Pre-event dovish bias: markets front-run rate cuts
        conf = 62.0
        conf += 15.0  # Dovish pre-event bias

        # Sentiment bonus if recent volatility elevated (event premium priced in)
        vix = msg.get("vix", 0)
        if vix > 20.0:
            conf += 5.0  # Elevated VIX = strong event premium
        if vix > 25.0:
            conf += 3.0  # Extreme event uncertainty

        conf = min(conf, 85.0)
        if conf < conf_floor:
            return None

        kelly = kelly_fn(conf)
        return {
            "type": "signal", "ticker_id": ticker_id, "direction": "Long",
            "confidence": conf,
            "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
            "strategy": "FOMMCDriftT-1",
            "fomc_phase": "pre_event",
            "days_to_fomc": 1,
            "expected_drift_pct": 0.4,
            "max_hold_hours": 144,  # 6 days
            "suggested_exit_urgency_hours": 120,  # Tighten stops after T+5
            **common_fields,
        }

    # ── PHASE 2: T+1 to T+5 DRIFT CAPTURE (post-event continuation) ──
    if 1 <= days_to_fomc <= 5:
        # Determine if FOMC was dovish or hawkish from nightly macro data
        dovish = None
        try:
            # Try to load FOMC outcome from persistent memory
            pm_path = "/app/data/persistent_memory.json"
            if os.path.exists(pm_path):
                with open(pm_path) as f:
                    pm = json.load(f)
                    fomc_outcome = pm.get("last_fomc_outcome")
                    if fomc_outcome:
                        dovish = fomc_outcome.get("dovish", None)
                        if dovish is None:
                            # Infer from fields
                            rate_decision = fomc_outcome.get("rate_decision")  # "cut" / "hold" / "raise"
                            dovish = rate_decision in ("cut", "hold")
        except (FileNotFoundError, json.JSONDecodeError, Exception):
            pass

        # Fallback: if no persistent data, check context_store for clues
        if dovish is None:
            try:
                ctx_path = "/app/data/context_store.json"
                if os.path.exists(ctx_path):
                    with open(ctx_path) as f:
                        ctx = json.load(f)
                        fomc_guidance = ctx.get("last_macro_event", {})
                        if "fomc" in fomc_guidance.get("event_type", "").lower():
                            sentiment = fomc_guidance.get("sentiment")  # "dovish" / "hawkish"
                            dovish = sentiment == "dovish"
            except (FileNotFoundError, json.JSONDecodeError, Exception):
                pass

        # If still unknown, default to dovish bias (pre-event positioning already assumes dovish)
        if dovish is None:
            dovish = True

        # ── DOVISH OUTCOME: Continue LONG ──
        if dovish:
            conf = 60.0
            conf += 12.0  # Dovish drift continuation
            vix = msg.get("vix", 0)
            if vix > 18.0:
                conf += 3.0  # Elevated VIX amplifies drift
            conf = min(conf, 82.0)
            if conf < conf_floor:
                return None

            kelly = kelly_fn(conf)
            return {
                "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                "confidence": conf,
                "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                "strategy": "FOMMCDriftT+N",
                "fomc_phase": "post_event_dovish",
                "days_since_fomc": days_to_fomc,
                "expected_drift_pct": 0.4,
                "max_hold_hours": 144,
                **common_fields,
            }

        # ── HAWKISH OUTCOME: Reduce conviction ──
        else:
            # ISA constraint: long-only. Instead of shorting, reduce position.
            # Rate hikes tend to hurt growth, but inflation hedge can rally.
            conf = 55.0
            conf += 8.0  # Hawkish drift is weaker but tradeable
            vix = msg.get("vix", 0)
            if vix > 22.0:
                conf += 2.0  # High VIX in hawkish regime = fade opportunity

            conf = min(conf, 75.0)
            if conf < conf_floor:
                return None

            kelly = kelly_fn(conf)
            return {
                "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                "confidence": conf,
                "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                "strategy": "FOMMCDriftT+N",
                "fomc_phase": "post_event_hawkish",
                "days_since_fomc": days_to_fomc,
                "expected_drift_pct": 0.15,  # Lower drift in hawkish
                "max_hold_hours": 96,  # Tighten to 4 days
                "note": "hawkish_outcome_reduced_conviction",
                **common_fields,
            }

    return None


def _system5_overnight(ticker_id, msg, ind, conf_floor, kelly_fn, common_fields):
    """System 5: Overnight Carry — buy at close, sell at open.

    Academic basis: Cliff, Cooper, Gulen (2008) — overnight drift premium.
    Leveraged ETPs amplify the overnight premium (3x drift = 3x carry).
    Entry: 30 minutes before market close. Exit: handled by exit_engine at open.
    Habitat: Tier 1 liquid ETPs. Regime: Normal, Caution.
    """
    # Only fire near market close (last 30 min of session)
    london_secs = msg.get("london_time_secs", 0)
    # LSE close window: 16:00-16:25 London (57600-59100 secs)
    # US close window: 20:30-20:55 London (73800-75300 secs)
    in_lse_close = 57600 <= london_secs <= 59100
    in_us_close = 73800 <= london_secs <= 75300
    if not (in_lse_close or in_us_close):
        return None

    # Regime gate: only Normal/Caution (overnight in Stress/Crisis is too risky)
    hurst_regime = ind.get("hurst_regime", "random")
    if hurst_regime in ("mean_reverting",):
        return None  # Choppy = bad for carry

    # Day-of-week seasonal filter (S&P 500 overnight premium research):
    # Overnight returns are strongest Mon-Thu, weakest Fri (weekend risk).
    from datetime import datetime, timezone
    ts_ns = msg.get("timestamp_ns", 0)
    if ts_ns > 0:
        day_of_week = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).weekday()
        if day_of_week == 4:  # Friday
            return None  # Skip Friday overnight — weekend gap risk

    # Book 40: Leverage-specific overnight limit — 5x ETPs = ZERO overnight
    leverage = msg.get("leverage", 3)
    if leverage >= 5:
        return None  # 5x ETPs: intraday only, never overnight

    # Book 186: Day-of-week carry premium adjustment
    # Mon-Wed: +2pp confidence (positive carry). Thu: neutral. Fri: blocked above.
    day_conf_adj = 0.0
    if ts_ns > 0:
        if day_of_week <= 2:  # Mon-Wed
            day_conf_adj = 2.0
        elif day_of_week == 3:  # Thursday
            day_conf_adj = -2.0  # Slightly reduce (pre-weekend positioning)

    # Need positive momentum going into close (don't carry a falling knife)
    bars_5m = ind.get("bars_5m", [])
    if len(bars_5m) < 6:
        return None
    recent_3 = bars_5m[-3:]
    up_count = sum(1 for b in recent_3 if b["close"] > b["open"])
    if up_count < 2:
        return None  # Need 2/3 recent bars up

    conf = 56.0 + day_conf_adj
    if up_count == 3:
        conf += 5.0
    if ind.get("rvol", 1.0) > 1.2:
        conf += 5.0  # Volume supports direction
    if ind.get("adx", 0) > 20.0:
        conf += 5.0  # Trending into close
    conf = min(conf, 78.0)

    if conf < conf_floor:
        return None

    kelly = kelly_fn(conf)
    return {
        "type": "signal", "ticker_id": ticker_id, "direction": "Long",
        "confidence": conf,
        "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
        "strategy": "S5_OvernightCarry",
        "s5_window": "lse_close" if in_lse_close else "us_close",
        "s5_up_bars": up_count,
        **common_fields,
    }


def _system6_catalyst(ticker_id, msg, ind, conf_floor, kelly_fn, common_fields):
    """System 6: Catalyst Rotation — event-driven trading.

    Fires around major macro events (FOMC, NFP, CPI) + gap fades.
    Post-event drift: markets tend to continue in the direction of the initial move
    30-60 minutes after the event. ISA long-only: only trade bullish catalysts.
    Habitat: All ETPs during events. All regimes.
    """
    # Check for gap (already computed by engine)
    gap_pct = msg.get("gap_pct", 0.0)

    # Post-gap continuation: if gap > 1.5% up with high volume, ride the momentum
    if gap_pct > 1.5 and ind.get("rvol", 1.0) > 2.0:
        conf = 58.0
        if gap_pct > 3.0:
            conf += 8.0  # Large gap = strong catalyst
        if ind.get("adx", 0) > 20.0:
            conf += 7.0  # Trending after gap = continuation likely
        bars_5m = ind.get("bars_5m", [])
        if len(bars_5m) >= 3:
            recent_up = sum(1 for b in bars_5m[-3:] if b["close"] > b["open"])
            if recent_up >= 2:
                conf += 5.0  # Price confirming after gap
        conf = min(conf, 82.0)
        if conf >= conf_floor:
            kelly = kelly_fn(conf)
            return {
                "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                "confidence": conf,
                "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                "strategy": "S6_Catalyst", "s6_gap_pct": round(gap_pct, 3),
                "s6_trigger": "gap_continuation",
                **common_fields,
            }

    return None


def _system7_tail_hedge(ticker_id, msg, ind, conf_floor, kelly_fn, common_fields):
    """System 7: Tail Hedge — long inverse positions during crisis.

    When VIX > 25 AND hurst regime is trending AND market is falling,
    buy inverse 3x ETPs to profit from the crash and hedge the portfolio.
    Habitat: Inverse ETPs (-3x). Regime: Stress, Crisis only.
    """
    vix = msg.get("vix", 0)
    if vix < 25.0:
        return None

    symbol = ticker_symbols.get(ticker_id, "")
    is_inverse = symbol.startswith("3S") or symbol in (
        "QQQS.L", "3USS.L", "3STS.L", "3SNV.L", "3SAP.L", "3SMS.L", "3SEM.L",
    )
    if not is_inverse:
        return None

    # Need trending regime (crisis = strong trends)
    hurst_regime = ind.get("hurst_regime", "random")
    if hurst_regime not in ("trending",):
        return None

    # Confirm downward momentum (inverse ETPs go UP when market goes DOWN)
    bars_5m = ind.get("bars_5m", [])
    if len(bars_5m) < 5:
        return None
    # For inverse ETPs, "up" bars mean the underlying is falling
    recent_up = sum(1 for b in bars_5m[-5:] if b["close"] > b["open"])
    if recent_up < 3:
        return None  # Inverse not trending up = market not crashing

    conf = 60.0
    if vix > 35.0:
        conf += 10.0  # Extreme fear
    if vix > 45.0:
        conf += 5.0   # Panic
    if ind.get("rvol", 1.0) > 3.0:
        conf += 5.0   # Extreme volume = capitulation
    conf = min(conf, 88.0)

    if conf < conf_floor:
        return None

    kelly = kelly_fn(conf)
    return {
        "type": "signal", "ticker_id": ticker_id, "direction": "Long",
        "confidence": conf,
        "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
        "strategy": "S7_TailHedge", "s7_vix": round(vix, 1),
        "s7_inverse_momentum": recent_up,
        **common_fields,
    }


# ============================================================================
# BOOK 136: CROSS-MARKET LEAD-LAG R² RECALIBRATION
# ============================================================================

def _recalibrate_lead_lag_correlations():
    """Recalibrate rolling R² for all lead-lag pairs every 5 minutes.

    For each follower→leader pair, computes rolling 100-bar Pearson correlation
    of returns (R²). Updates _lead_lag_r2 with correlation strength and status:
      - R² < 0.50  → "disabled" (pair skipped in signal generation)
      - R² >= 0.85 → "strong" (confidence boost in signal generation)
      - else        → "active" (normal operation, confidence scaled by R²)
    """
    global _lead_lag_r2, _lead_lag_r2_last_recalib
    _now = time.time()
    if _now - _lead_lag_r2_last_recalib < _LEAD_LAG_R2_INTERVAL:
        return
    _lead_lag_r2_last_recalib = _now

    try:
        from python_brain.strategies.lead_lag import LEAD_LAG_PAIRS
    except ImportError:
        return

    updated_count = 0
    for pair_name, pair_info in LEAD_LAG_PAIRS.items():
        leader_sym = pair_info["leader"]
        follower_sym = pair_info["follower"]

        leader_closes = list(_leader_bar_closes.get(leader_sym, []))
        follower_closes = list(_leader_bar_closes.get(follower_sym, []))

        # Need at least 20 bars for meaningful correlation; 100 is ideal
        min_bars = min(len(leader_closes), len(follower_closes))
        if min_bars < 20:
            # Not enough data yet — keep existing state or mark pending
            if pair_name not in _lead_lag_r2:
                _lead_lag_r2[pair_name] = {"r2": 1.0, "status": "active", "updated": _now, "n_bars": 0}
            continue

        # Compute returns from closes (use the overlapping tail)
        n = min(min_bars, 100)
        l_closes = leader_closes[-n:]
        f_closes = follower_closes[-n:]

        l_returns = []
        f_returns = []
        for i in range(1, n):
            if l_closes[i - 1] > 0 and f_closes[i - 1] > 0:
                l_returns.append((l_closes[i] - l_closes[i - 1]) / l_closes[i - 1])
                f_returns.append((f_closes[i] - f_closes[i - 1]) / f_closes[i - 1])

        if len(l_returns) < 15:
            continue

        # Pearson correlation coefficient → R²
        n_ret = len(l_returns)
        sum_l = sum(l_returns)
        sum_f = sum(f_returns)
        sum_ll = sum(x * x for x in l_returns)
        sum_ff = sum(x * x for x in f_returns)
        sum_lf = sum(l_returns[i] * f_returns[i] for i in range(n_ret))

        denom_l = n_ret * sum_ll - sum_l * sum_l
        denom_f = n_ret * sum_ff - sum_f * sum_f

        if denom_l <= 0 or denom_f <= 0:
            r_squared = 0.0
        else:
            r = (n_ret * sum_lf - sum_l * sum_f) / (denom_l * denom_f) ** 0.5
            r_squared = r * r

        # Clamp to [0, 1]
        r_squared = max(0.0, min(1.0, r_squared))

        # Determine status
        if r_squared < _LEAD_LAG_R2_DISABLE_THRESH:
            status = "disabled"
        elif r_squared >= _LEAD_LAG_R2_STRONG_THRESH:
            status = "strong"
        else:
            status = "active"

        prev = _lead_lag_r2.get(pair_name, {})
        prev_status = prev.get("status", "")
        _lead_lag_r2[pair_name] = {
            "r2": round(r_squared, 4),
            "status": status,
            "updated": _now,
            "n_bars": n_ret,
        }
        updated_count += 1

        # Log status transitions
        if prev_status and prev_status != status:
            sys.stderr.write(
                f"LEAD_LAG_R2_TRANSITION: {pair_name} {prev_status}->{status} "
                f"r2={r_squared:.4f} n_bars={n_ret}\n"
            )
            sys.stderr.flush()

    if updated_count > 0:
        sys.stderr.write(
            f"LEAD_LAG_R2_RECALIB: updated {updated_count} pairs "
            f"disabled={sum(1 for v in _lead_lag_r2.values() if v.get('status') == 'disabled')} "
            f"strong={sum(1 for v in _lead_lag_r2.values() if v.get('status') == 'strong')} "
            f"active={sum(1 for v in _lead_lag_r2.values() if v.get('status') == 'active')}\n"
        )
        sys.stderr.flush()


def _nightly_recalibrate_lead_lag_optimal_lags():
    """Nightly hook: compute optimal lag for each pair and persist to disk.

    Sweeps lags from 30s to 180s (in 15s steps = 1 to 6 5-min bars)
    and picks the lag with highest R² for each pair. Writes results to
    /app/data/lead_lag_calibration.json for next-day loading.

    Called from _load_adaptive_params() on the same 5-min reload cycle,
    but only executes once per calendar day.
    """
    global _lead_lag_nightly_last_load
    _now = time.time()

    # Only run once per day (check file mtime)
    calib_path = "/app/data/lead_lag_calibration.json"
    try:
        if os.path.exists(calib_path):
            mtime = os.path.getmtime(calib_path)
            # If calibrated today, just load it
            from datetime import datetime, timezone
            file_date = datetime.fromtimestamp(mtime, tz=timezone.utc).date()
            today = datetime.now(timezone.utc).date()
            if file_date == today:
                # Load cached calibration
                if _now - _lead_lag_nightly_last_load < _LEAD_LAG_R2_INTERVAL:
                    return
                _lead_lag_nightly_last_load = _now
                with open(calib_path) as f:
                    calib = json.load(f)
                _lead_lag_optimal_lags.clear()
                for pair_name, info in calib.items():
                    if isinstance(info, dict) and "optimal_lag_bars" in info:
                        _lead_lag_optimal_lags[pair_name] = int(info["optimal_lag_bars"])
                return
    except Exception:
        pass

    # Perform fresh calibration
    try:
        from python_brain.strategies.lead_lag import LEAD_LAG_PAIRS
    except ImportError:
        return

    calib_results = {}
    for pair_name, pair_info in LEAD_LAG_PAIRS.items():
        leader_sym = pair_info["leader"]
        follower_sym = pair_info["follower"]

        leader_closes = list(_leader_bar_closes.get(leader_sym, []))
        follower_closes = list(_leader_bar_closes.get(follower_sym, []))

        min_bars = min(len(leader_closes), len(follower_closes))
        if min_bars < 30:
            continue

        # Compute full return series
        n = min(min_bars, 100)
        l_closes = leader_closes[-n:]
        f_closes = follower_closes[-n:]

        l_rets = []
        f_rets = []
        for i in range(1, n):
            if l_closes[i - 1] > 0 and f_closes[i - 1] > 0:
                l_rets.append((l_closes[i] - l_closes[i - 1]) / l_closes[i - 1])
                f_rets.append((f_closes[i] - f_closes[i - 1]) / f_closes[i - 1])

        if len(l_rets) < 20:
            continue

        # Sweep lags: 1 to 6 bars (30s to 180s in 15s 5-min bar steps)
        # Lag N means: correlate leader_returns[:-N] with follower_returns[N:]
        best_r2 = -1.0
        best_lag = 1

        for lag_bars in range(1, 7):  # 1..6 bars
            if len(l_rets) <= lag_bars + 10:
                continue

            # Leader returns shifted by lag_bars ahead of follower
            shifted_l = l_rets[:len(l_rets) - lag_bars]
            shifted_f = f_rets[lag_bars:]
            k = min(len(shifted_l), len(shifted_f))
            if k < 15:
                continue

            shifted_l = shifted_l[-k:]
            shifted_f = shifted_f[-k:]

            # Pearson R²
            s_l = sum(shifted_l)
            s_f = sum(shifted_f)
            s_ll = sum(x * x for x in shifted_l)
            s_ff = sum(x * x for x in shifted_f)
            s_lf = sum(shifted_l[j] * shifted_f[j] for j in range(k))

            d_l = k * s_ll - s_l * s_l
            d_f = k * s_ff - s_f * s_f

            if d_l <= 0 or d_f <= 0:
                continue

            r = (k * s_lf - s_l * s_f) / (d_l * d_f) ** 0.5
            r2 = r * r

            if r2 > best_r2:
                best_r2 = r2
                best_lag = lag_bars

        calib_results[pair_name] = {
            "optimal_lag_bars": best_lag,
            "optimal_lag_secs": best_lag * 30,  # approximate: each bar ~30s for sub-minute
            "best_r2": round(best_r2, 4),
            "n_bars_used": len(l_rets),
            "calibrated_at": _now,
        }

        _lead_lag_optimal_lags[pair_name] = best_lag

    # Persist to disk
    if calib_results:
        try:
            tmp_path = calib_path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(calib_results, f, indent=2)
            os.replace(tmp_path, calib_path)
            sys.stderr.write(
                f"LEAD_LAG_NIGHTLY_CALIB: wrote {len(calib_results)} pairs to {calib_path}\n"
            )
            sys.stderr.flush()
        except Exception as e:
            sys.stderr.write(f"LEAD_LAG_NIGHTLY_CALIB_WRITE_ERR: {e}\n")
            sys.stderr.flush()

    _lead_lag_nightly_last_load = _now


def _generate_signals(ticker_id, msg, ticks, ind, conf_floor):
    """Stage 3: Generate signals from all 13 generators with Book-derived pre-gates."""
    hurst, hurst_regime = ind["hurst"], ind["hurst_regime"]
    bars_5m = ind["bars_5m"]

    # ── BOOK 162: VPIN TOXICITY GATE ──
    # Block entries when informed flow is toxic. Calibrated thresholds from Book 162.
    # > 0.60: elevated → raise confidence floor by 10 (harder to enter)
    # > 0.80: extreme → block all entries (informed traders dominating)
    vpin = ind.get("vpin", 0.5)
    if vpin > 0.80:
        return []  # Block all signals — toxic informed flow
    vpin_penalty = 10 if vpin > 0.60 else 0
    effective_floor = conf_floor + vpin_penalty

    # ── BOOK 117: LIQUIDITY PULSE GATE ──
    try:
        from python_brain.risk.liquidity_pulse import LiquidityPulseDetector
        _lp_detector = getattr(msg, "_lp_detector", LiquidityPulseDetector())
        lp_alert = _lp_detector.check_tick(
            price=ind.get("last_price", 0),
            volume=ind.get("last_volume", 0),
            spread_bps=ind.get("spread_bps", 10),
            timestamp_secs=msg.get("timestamp_secs", 0),
        )
        if lp_alert and lp_alert.block_entry:
            return []  # Manipulation detected — block all entries
    except Exception:
        pass

    # ── BOOK 83: MICRO-REGIME PRE-GATE ──
    # Tick-level microstructure regime: TOXIC microstructure → raise floor or block
    _micro_regime_penalty = 0
    try:
        from python_brain.risk.vol_regime_cluster import classify_micro_regime
        _micro = classify_micro_regime(
            vpin=vpin,
            spread_pct=ind.get("spread_pct", 0.1),
            quote_imbalance=ind.get("quote_imbalance", 0.0),
        )
        if hasattr(_micro, 'regime'):
            if _micro.regime == "TOXIC":
                return []  # Toxic microstructure — block all entries
            elif _micro.regime == "THIN":
                _micro_regime_penalty = 10  # Thin liquidity — raise floor
    except ImportError:
        pass
    except Exception:
        pass
    effective_floor += _micro_regime_penalty

    # ── BOOK 46: BREAK-EVEN VOLATILITY FILTER FOR 3x ETPs ──
    # If realized vol exceeds break-even vol, the ETP mathematically underperforms.
    # sigma_BE = sqrt(2 * mu / (L + 1)) where L = leverage, mu = daily drift.
    # Block long entries when vol exceeds this — it's a losing bet by construction.
    _leverage_be = msg.get("leverage", 1)
    if _leverage_be >= 3:
        import math as _math_be
        _daily_drift = msg.get("daily_drift", 0.0004)  # ~10% annual = 0.04% daily
        _L = float(_leverage_be)
        _sigma_be = _math_be.sqrt(abs(2 * _daily_drift / (_L + 1))) if _daily_drift > 0 else 0
        _realized_vol_daily = msg.get("realized_vol", 0.30) / _math_be.sqrt(252)  # annualized → daily
        # Check autocorrelation: if mean-reverting (rho < 0), decay is maximized
        if hurst < 0.40:  # Mean-reverting proxy
            _sigma_be *= 0.7  # Stricter threshold in MR regime
        if _sigma_be > 0 and _realized_vol_daily > _sigma_be:
            # Vol exceeds break-even — block long entries on this 3x ETP
            return []

    # ── BOOK 81: TURNOVER BUDGET CHECK ──
    # If daily trade limit exceeded, block new entries
    try:
        from python_brain.ouroboros.cost_model import check_turnover_budget
        _tb_ok, _tb_reason = check_turnover_budget(
            trades_today=msg.get("trades_today", 0),
            turnover_ytd_pct=msg.get("turnover_ytd_pct", 0),
        )
        if not _tb_ok:
            return []  # Budget exhausted
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 127: TDA CRASH DETECTOR PRE-GATE ──
    # Topological early warning: if crash probability > 70%, block ALL entries
    try:
        from python_brain.ml.tda_crash_detector import CrashDetector
        import numpy as _np_tda
        if bars_5m and len(bars_5m) >= 30:
            if not hasattr(_generate_signals, "_tda_det"):
                _generate_signals._tda_det = CrashDetector()
            _tda_det = _generate_signals._tda_det
            _tda_closes = _np_tda.array([b["close"] for b in bars_5m[-50:]])
            _crash_p = _tda_det.update(_tda_closes)
            if _crash_p.get("crash_probability", 0) > 0.7:
                return []  # Topology anomaly → block all entries
            elif _crash_p.get("crash_probability", 0) > 0.4:
                effective_floor += 10  # Elevated topology risk → raise floor
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 103: ADVERSARIAL DETECTION PRE-GATE ──
    # Detect spoofing, wash trading, or manipulated signals
    try:
        from python_brain.risk.adversarial_detection import detect_manipulation
        _manip = detect_manipulation(
            price=msg["last"], volume=msg.get("volume", 0),
            spread_bps=ind.get("spread_bps", 10),
            recent_prices=[t["last"] for t in ticks[-20:]],
        )
        if _manip and _manip.get("is_manipulation"):
            return []  # Manipulation detected → block
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 44: IBKR RESILIENCE PRE-GATE ──
    # Check broker connectivity before generating signals
    try:
        from python_brain.execution.ibkr_resilience import is_connection_healthy
        if not is_connection_healthy(msg.get("connection_state", "active")):
            return []  # Broker degraded → no new entries
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 176: DATA QUALITY PRE-GATE ──
    # Reject ticks with suspicious data (stale price, zero volume, impossible spread)
    try:
        from python_brain.forensics.data_quality import check_tick_quality
        _dq = check_tick_quality(
            price=msg["last"], bid=msg.get("bid", 0), ask=msg.get("ask", 0),
            volume=msg.get("volume", 0), timestamp_ns=msg.get("timestamp_ns", 0),
        )
        if _dq and _dq.get("quality_score", 100) < 50:
            return []  # Bad data → block
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 48: STRUCTURAL BREAK PRE-GATE ──
    # If a structural break just occurred, pause trading until regime stabilizes
    try:
        from python_brain.causal.structural_alpha_scanner import detect_structural_break
        import numpy as _np_sb
        if bars_5m and len(bars_5m) >= 30:
            _sb_closes = _np_sb.array([b["close"] for b in bars_5m[-60:]])
            _sb = detect_structural_break(_sb_closes)
            if _sb and _sb.get("significant") and _sb.get("recency_bars", 999) < 10:
                effective_floor += 15  # Recent structural break → much harder to enter
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 190: SAFETY BOUNDARY PRE-CHECK ──
    try:
        from python_brain.risk.safety_boundaries import SafetyBoundaryChecker
        if not hasattr(_generate_signals, "_safety"):
            _generate_signals._safety = SafetyBoundaryChecker()
        _safety = _generate_signals._safety
        violation = _safety.check_all(
            equity=msg.get("equity", 10000),
            hwm=msg.get("hwm", 10000),
            daily_pnl=msg.get("daily_pnl", 0),
            consecutive_losses=msg.get("consecutive_losses", 0),
        )
        if violation and violation.action == "HALT":
            return []  # Sacred limit breached — no entries
    except Exception:
        pass

    # ── BOOK 179: CAPITAL PHASE STRATEGY FILTER ──
    try:
        from python_brain.sizing.capital_phasing import get_capital_phase
        phase = get_capital_phase(msg.get("equity", 10000))
    except Exception:
        phase = None

    # ── BOOK 21: DIRECTIONAL VPIN (D-VPIN) ──
    # Signed version: positive = informed buying, negative = informed selling
    d_vpin = 0.0
    if bars_5m and len(bars_5m) >= 10:
        buy_v = sum(b["volume"] for b in bars_5m[-10:] if b["close"] > b["open"])
        sell_v = sum(b["volume"] for b in bars_5m[-10:] if b["close"] <= b["open"])
        total_v = buy_v + sell_v
        if total_v > 0:
            d_vpin = (buy_v - sell_v) / total_v  # -1 to +1

    # ── BOOK 22: KELTNER SQUEEZE DETECTION ──
    # When Bollinger Bands contract inside Keltner Channel = volatility squeeze.
    # First bar after squeeze releases → 67% WR breakout signal.
    squeeze_on = False
    squeeze_release = False
    if bars_5m and len(bars_5m) >= 20:
        closes_20 = [b["close"] for b in bars_5m[-20:]]
        sma20 = sum(closes_20) / 20
        std20 = (sum((c - sma20)**2 for c in closes_20) / 20) ** 0.5

        # ATR(20) from bars
        atr_sum = 0.0
        for i in range(1, min(20, len(bars_5m))):
            h, l, pc = bars_5m[-i]["high"], bars_5m[-i]["low"], bars_5m[-i-1]["close"]
            atr_sum += max(h - l, abs(h - pc), abs(l - pc))
        atr20 = atr_sum / min(19, len(bars_5m) - 1) if len(bars_5m) > 1 else 0

        if std20 > 0 and atr20 > 0:
            bb_upper = sma20 + 2.0 * std20
            keltner_upper = sma20 + 1.5 * atr20
            squeeze_on = bb_upper < keltner_upper  # BB inside Keltner = squeeze
            # Check if squeeze just released (was on, now off)
            if not squeeze_on and len(bars_5m) >= 21:
                prev_closes = [b["close"] for b in bars_5m[-21:-1]]
                prev_sma = sum(prev_closes) / 20
                prev_std = (sum((c - prev_sma)**2 for c in prev_closes) / 20) ** 0.5
                prev_bb_upper = prev_sma + 2.0 * prev_std
                prev_keltner_upper = prev_sma + 1.5 * atr20
                if prev_bb_upper < prev_keltner_upper:
                    squeeze_release = True  # Just released — 67% WR breakout

    # ── BOOK 118: STUDENT-T KELLY ADJUSTMENT ──
    # ETP returns have fat tails (ν ≈ 4-6). Standard Kelly overestimates optimal fraction.
    # Adjustment: multiply Kelly by 1/(1 + 3/ν). For ν=5: factor = 1/1.6 = 0.625.
    leverage = msg.get("leverage", 3)
    nu = 5.0 if leverage >= 3 else 7.0  # Fatter tails for higher leverage
    student_t_factor = 1.0 / (1.0 + 3.0 / nu)  # 0.625 for 3x ETPs

    common_fields = {
        "rvol": ind["rvol"], "hurst": hurst, "hurst_regime": hurst_regime,
        "volume_divergence": ind["vol_div"], "adx": ind["adx"],
        "vol_slope": ind["vol_slope"], "vwap_dist_pct": ind["vwap_dist_pct"],
        "structural_score": ind["structural_score"],
        "d_vpin": round(d_vpin, 3), "squeeze_on": squeeze_on,
        "squeeze_release": squeeze_release, "vpin_toxic": vpin > 0.60,
    }

    # Thompson per-type floors
    # Use VPIN-adjusted floor for all strategies
    vanguard_floor, orchestrator_floor = effective_floor, effective_floor
    if _adaptive_entry_confidence:
        ta = _adaptive_entry_confidence.get("TypeA")
        if ta is not None:
            vanguard_floor = max(conf_floor, ta)
        tb = _adaptive_entry_confidence.get("TypeB")
        if tb is not None:
            orchestrator_floor = max(conf_floor, tb)

    # Helper: compute Kelly for a given confidence
    def _kelly_for(confidence):
        total_trades = msg.get("total_trades", 0)
        k = kelly_12factor(
            win_rate_raw=msg.get("win_rate", 0.5), total_trades=total_trades,
            avg_win=msg.get("avg_win", 0.02), avg_loss=msg.get("avg_loss", 0.02),
            leverage_factor=msg.get("leverage", 3), realized_vol_annual=msg.get("realized_vol", 0.30),
            correlation_to_portfolio=msg.get("correlation", 0.0),
            current_drawdown_pct=msg.get("drawdown_pct", 0.0),
            amihud_illiq=msg.get("amihud", 0.0),
            regime=hurst_regime if hurst_regime != "random" else msg.get("regime", "normal"),
            spread_pct=msg.get("spread_pct", 0.1), time_of_day_fraction=msg.get("time_fraction", 0.5),
            confidence=confidence, portfolio_heat_pct=msg.get("heat_pct", 0.0),
            equity=msg.get("equity", 10000.0), price=msg["last"],
        )
        # Early ramp: use preliminary Kelly if we have few trades
        if total_trades < 50:
            pk = min(confidence / 1000.0, 0.05)
            if k["kelly_fraction"] < pk:
                eq = msg.get("equity", 10000.0)
                k["kelly_fraction"] = pk
                k["shares"] = max(int(pk * eq / max(msg["last"], 1e-9)), 1)

        # CONSOLIDATED KELLY SCALING (was 5 layers → now 1 with drawdown governor):
        # Previous chain: regime(0.6) * half_kelly(0.5) * paper_buffer(0.75) = 0.225x → KILLED ALL TRADES.
        # Fix: half-Kelly with drawdown governor is the ONLY scaling layer.
        # Regime is already a factor in kelly_12factor(). Paper-to-live gap is covered by slippage model.
        # LETF decay in MR regime is a genuine additional factor (arxiv 2504.20116).
        dd = msg.get("drawdown_pct", 0.0)
        if dd > 10.0:
            kelly_scale = 0.25   # quarter-Kelly — survival mode
        elif dd > 5.0:
            kelly_scale = 0.40   # reduced Kelly — defensive
        else:
            kelly_scale = 0.50   # half-Kelly — standard (Chan 2010)

        # LETF mean-reversion penalty (the one genuine additional factor)
        leverage = msg.get("leverage", 3)
        if leverage >= 3 and hurst_regime == "mean_reverting":
            kelly_scale *= 0.7  # 30% reduction for 3x LETF in MR regime

        # Book 118: Student-t Kelly adjustment for fat-tailed ETP returns.
        # ν=5 for 3x ETPs → factor = 1/(1+3/5) = 0.625. Prevents Gaussian overestimation.
        combined = kelly_scale * student_t_factor
        k["kelly_fraction"] *= combined
        k["shares"] = max(1, int(k["shares"] * combined))

        return k

    # VanguardSniper (momentum, non-mean-reverting regimes)
    vanguard_signal = None
    if hurst < 0.01 or hurst >= 0.20 or hurst_regime in ("trending", "random"):
        eval_ticks = [{"last": b["close"], "high": b["high"], "low": b["low"],
                       "bid": b["close"], "ask": b["close"], "volume": b["volume"]}
                      for b in bars_5m] if bars_5m else ticks
        result = vanguard_evaluate(eval_ticks, confidence_floor=vanguard_floor)
        if result is not None:
            kelly = _kelly_for(result["confidence"])
            vanguard_signal = {
                "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                "confidence": result["confidence"],
                "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                "strategy": "Momentum", **common_fields,
            }

    # Orchestrator (all regimes)
    orchestrator_signal = None
    if len(ticks) >= 5:
        try:
            intent = _evaluate_orchestrator(msg, ticks, ind["rvol"], hurst, hurst_regime, ind["adx"])
            if intent is not None and intent.confidence < orchestrator_floor:
                intent = None
            if intent is not None:
                direction = "Long" if intent.direction == "long" else "Short"
                ok = min(intent.confidence * intent.sizing_mult / 1000.0, 0.05)
                eq = msg.get("equity", 10000.0)
                pr = max(msg["last"], 1e-9)
                orchestrator_signal = {
                    "type": "signal", "ticker_id": ticker_id, "direction": direction,
                    "confidence": intent.confidence,
                    "kelly_fraction": ok, "shares": max(int(ok * eq / pr), 1) if ok > 0 else 0,
                    "strategy": f"Orchestrator_{intent.strategy_name}", **common_fields,
                }
        except Exception as e:
            sys.stderr.write(f"Bridge: orchestrator error (non-fatal): {e}\n")
            sys.stderr.flush()

    # ── NEW STRATEGIES (Sprint C, 2026-03-23) ──

    # Strategy: IBS Mean Reversion (Connors RSI-2 / IBS combo)
    # Academic basis: Connors & Alvarez (2008). IBS < 0.2 + RSI(2) < 10 → ~57% WR.
    # Fires in ALL regimes (mean-reverting is strongest but don't gate on regime).
    # LOOSENED from original: removed RVOL > 0.7 gate, widened RSI2 to < 25, IBS to < 0.30.
    ibs_signal = None
    if len(bars_5m) >= 5:
        ibs_val = ind["ibs"]
        prices_for_rsi = [b["close"] for b in bars_5m]
        rsi2 = calculate_rsi(prices_for_rsi, period=2)
        if ibs_val is not None and rsi2 is not None and ibs_val < 0.30 and rsi2 < 25.0:
            # Graduated confidence: lower IBS + lower RSI = higher conviction
            ibs_conf = 55.0
            if ibs_val < 0.10:
                ibs_conf += 15.0
            if rsi2 < 5.0:
                ibs_conf += 10.0
            if ind["rvol"] > 1.5:
                ibs_conf += 5.0
            ibs_conf = min(ibs_conf, 95.0)
            if ibs_conf >= conf_floor:
                kelly = _kelly_for(ibs_conf)
                ibs_signal = {
                    "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                    "confidence": ibs_conf,
                    "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                    "strategy": "IBS_MeanReversion", "ibs_entry": round(ibs_val, 4),
                    "rsi2_entry": round(rsi2, 2), **common_fields,
                }

    # Strategy: Volume Expansion Continuation
    # Entry: RVOL > 2.0 AND ADX > 20 AND 3+ consecutive up bars.
    # Differentiation from VanguardSniper: requires RVOL > 2.0 (vs 1.5) and consecutive bars.
    # VanguardSniper is ADX-based scoring; VolExpansion is structure-based confirmation.
    volexp_signal = None
    if ind["rvol"] > 2.0 and ind["adx"] > 20.0 and len(bars_5m) >= 5:
        recent = bars_5m[-4:]
        up_count = sum(1 for b in recent if b["close"] > b["open"])
        if up_count >= 3:
            # Graduated confidence based on RVOL strength + trend strength
            ve_conf = 60.0
            if ind["rvol"] > 3.0:
                ve_conf += 10.0
            if ind["adx"] > 30.0:
                ve_conf += 10.0
            if ind["vol_slope"] > 0:
                ve_conf += 5.0
            # Differentiation bonus: if VanguardSniper ALSO fired, this is confirmation
            if vanguard_signal is not None:
                ve_conf += 5.0  # Cross-strategy confirmation
            ve_conf = min(ve_conf, 95.0)
            if ve_conf >= conf_floor:
                kelly = _kelly_for(ve_conf)
                volexp_signal = {
                    "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                    "confidence": ve_conf,
                    "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                    "strategy": "VolExpansion", **common_fields,
                }

    # Strategy: Opening Range Breakout (ORB) — US session only
    # Entry: Price breaks the first-30-min high/low with volume confirmation.
    # Time window: first 60 bars (5s each = 5min) after US cash open.
    # For now, we detect via time_fraction (0.0=LSE open, higher=later in day).
    orb_signal = None
    ts_ns = msg.get("timestamp_ns", 0)
    if ts_ns > 0 and len(bars_5m) >= 6:
        from datetime import datetime, timezone
        utc_dt = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)
        utc_hour = utc_dt.hour
        utc_min = utc_dt.minute
        # US cash opens at 14:30 UTC. ORB formation: 14:30-14:45 UTC (first 15 min).
        # ORB breakout window: 14:45-15:30 UTC (trade the breakout for up to 45 min).
        if 14 <= utc_hour <= 15 and (utc_hour == 14 and utc_min >= 45 or utc_hour == 15 and utc_min <= 30):
            # Find the opening range: first 3 five-minute bars of this session
            # (bars_5m index 0 may not be session open, but we approximate with recent 6 bars)
            exchange = _get_exchange_for_symbol(ticker_symbols.get(ticker_id, ""))
            if exchange == "US":
                range_bars = bars_5m[:min(3, len(bars_5m))]
                orb_high = max(b["high"] for b in range_bars)
                orb_low = min(b["low"] for b in range_bars)
                current = bars_5m[-1]["close"]
                # Breakout above ORB high with volume
                if current > orb_high and ind["rvol"] > 1.5:
                    orb_conf = 60.0
                    if ind["rvol"] > 2.5:
                        orb_conf += 10.0
                    if ind["adx"] > 15.0:
                        orb_conf += 10.0
                    orb_conf = min(orb_conf, 90.0)
                    if orb_conf >= conf_floor:
                        kelly = _kelly_for(orb_conf)
                        orb_signal = {
                            "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                            "confidence": orb_conf,
                            "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                            "strategy": "ORB_Breakout", "orb_high": round(orb_high, 4),
                            "orb_low": round(orb_low, 4), **common_fields,
                        }

    # Strategy: Gap Fade — liquidity gaps tend to fill
    # Academic basis: Gaps >1% in large-cap stocks fill ~65% of the time within the session.
    # Only fade LIQUIDITY gaps (RVOL < 2.0 at open = low institutional participation).
    # Information gaps (RVOL > 5.0) are news-driven and tend to continue — don't fade those.
    gap_signal = None
    gap_pct = msg.get("gap_pct", 0.0)
    if abs(gap_pct) > 1.0 and ind["rvol"] < 2.0 and len(bars_5m) >= 3:
        from brain.gap_detector import classify_gap
        gap_type = classify_gap(ind["rvol"])
        if gap_type == "liquidity":
            # Fade the gap: if gap up, go short (but we're ISA long-only, so skip gap-up fades)
            # If gap down > 1%, go long (buying the dip on a liquidity gap)
            if gap_pct < -1.0:
                gf_conf = 58.0
                if gap_pct < -2.0:
                    gf_conf += 10.0  # Larger gap = higher fill probability
                if ind["ibs"] < 0.3:
                    gf_conf += 7.0   # Close near low confirms gap-down exhaustion
                gf_conf = min(gf_conf, 90.0)
                if gf_conf >= conf_floor:
                    kelly = _kelly_for(gf_conf)
                    gap_signal = {
                        "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                        "confidence": gf_conf,
                        "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                        "strategy": "GapFade", "gap_pct": round(gap_pct, 3),
                        "gap_type": gap_type, **common_fields,
                    }

    # ── SYSTEM 1: Microstructure Momentum (Phase 4) ──
    s1_signal = None
    if len(ticks) >= 20 and msg.get("bid", 0) > 0 and msg.get("ask", 0) > 0:
        s1_signal = _system1_microstructure(ticker_id, msg, ticks, ind, conf_floor, _kelly_for, common_fields)

    # ── SYSTEM 2: Statistical Reversion (Phase 6) ──
    s2_signal = None
    if len(bars_5m) >= 20:
        s2_signal = _system2_reversion(ticker_id, msg, bars_5m, ind, conf_floor, _kelly_for, common_fields)

    # ── SYSTEM 3: Macro Trend Following (Phase 6) ──
    s3_signal = None
    if len(bars_5m) >= 20:
        s3_signal = _system3_macro_trend(ticker_id, msg, bars_5m, ind, conf_floor, _kelly_for, common_fields)

    # ── SYSTEM 4: Volatility Premium (Phase 8) ──
    s4_signal = _system4_volatility(ticker_id, msg, ind, conf_floor, _kelly_for, common_fields)

    # ── SYSTEM 5: Overnight Carry (Phase 8) ──
    s5_signal = _system5_overnight(ticker_id, msg, ind, conf_floor, _kelly_for, common_fields)

    # ── BOOK 5: FOMC Pre-drift Positioning ──
    fomc_signal = _fomc_pre_drift_positioning(ticker_id, msg, ind, conf_floor, _kelly_for, common_fields)

    # ── SYSTEM 6: Catalyst Rotation (Phase 9) ──
    s6_signal = _system6_catalyst(ticker_id, msg, ind, conf_floor, _kelly_for, common_fields)

    # ── SYSTEM 7: Tail Hedge (Phase 9) ──
    s7_signal = _system7_tail_hedge(ticker_id, msg, ind, conf_floor, _kelly_for, common_fields)

    # ── NEW STRATEGIES FROM BOOK LIBRARY ──

    # Vol Compression Breakout (Book 22)
    vol_comp_signal = None
    try:
        from python_brain.strategies.vol_compression import detect_squeeze
        import numpy as _np
        closes = ind.get("closes_arr")
        highs = ind.get("highs_arr")
        lows = ind.get("lows_arr")
        vols = ind.get("volumes_arr")
        if closes is not None and len(closes) > 100:
            sq = detect_squeeze(_np.array(closes), _np.array(highs), _np.array(lows), _np.array(vols),
                                ticker=msg.get("symbol", ""))
            if sq and sq.confidence >= conf_floor and sq.breakout_direction == "up":
                vol_comp_signal = {
                    "confidence": sq.confidence,
                    "kelly_fraction": _kelly_for(sq.confidence),
                    "shares": max(1, int(_kelly_for(sq.confidence) * msg.get("equity", 10000) / max(ind.get("last_price", 1), 0.01))),
                    "strategy": "VolCompression",
                    "squeeze_score": sq.squeeze_score,
                    **common_fields,
                }
    except Exception:
        pass

    # ETP Rebalancing Flow (Book 36) — 19:00-20:00 GMT window
    rebal_signal = None
    try:
        from python_brain.strategies.rebalancing_flow import predict_rebalancing
        london_secs = msg.get("london_time_secs", 0)
        utc_secs = london_secs  # Approximate; DST offset handled elsewhere
        symbol = msg.get("symbol", "")
        underlying_ret = msg.get("underlying_intraday_return", 0.0)
        if underlying_ret != 0 and symbol:
            rb = predict_rebalancing(underlying_ret, symbol, utc_secs)
            if rb and rb.confidence >= conf_floor:
                rebal_signal = {
                    "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                    "confidence": rb.confidence,
                    "kelly_fraction": _kelly_for(rb.confidence)["kelly_fraction"],
                    "shares": _kelly_for(rb.confidence)["shares"],
                    "strategy": "RebalancingFlow",
                    "rebal_notional_mm": rb.estimated_rebalancing_notional_mm,
                    **common_fields,
                }
    except Exception:
        pass

    # NAV Premium/Discount (Book 132) — buy when ETP trades at discount
    nav_signal = None
    try:
        from python_brain.strategies.nav_arbitrage import NAVTracker
        nav_tracker = getattr(msg, "_nav_tracker", None)
        if nav_tracker is not None:
            symbol = msg.get("symbol", "")
            sig = nav_tracker.check_signal(symbol)
            if sig and sig.confidence >= conf_floor and sig.direction == "buy":
                nav_signal = {
                    "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                    "confidence": sig.confidence,
                    "kelly_fraction": _kelly_for(sig.confidence)["kelly_fraction"],
                    "shares": _kelly_for(sig.confidence)["shares"],
                    "strategy": "NAVArbitrage",
                    "nav_z_score": sig.z_score,
                    "nav_premium_pct": sig.premium_pct,
                    **common_fields,
                }
    except Exception:
        pass

    # Alpha Factory Ensemble (Books 121, 168) — formulaic alpha combination
    alpha_signal = None
    try:
        from python_brain.alphas.alpha_factory import AlphaFactory
        import numpy as _np
        closes = ind.get("closes_arr")
        volumes = ind.get("volumes_arr")
        if closes is not None and len(closes) >= 20:
            if not hasattr(_generate_signals, "_alpha_factory"):
                _generate_signals._alpha_factory = AlphaFactory()
            factory = _generate_signals._alpha_factory
            results = factory.evaluate_all(_np.array(closes), _np.array(volumes))
            ensemble_val = factory.ensemble(results)
            # Convert ensemble value to signal if strong enough
            if abs(ensemble_val) > 0.1:  # Threshold for actionable signal
                alpha_conf = min(85, int(50 + abs(ensemble_val) * 200))
                if ensemble_val > 0 and alpha_conf >= conf_floor:  # ISA: long only
                    alpha_signal = {
                        "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                        "confidence": alpha_conf,
                        "kelly_fraction": _kelly_for(alpha_conf)["kelly_fraction"],
                        "shares": _kelly_for(alpha_conf)["shares"],
                        "strategy": "AlphaFactory",
                        "ensemble_value": round(ensemble_val, 4),
                        "n_alphas": len(results),
                        **common_fields,
                    }
    except Exception:
        pass

    # ── BOOK 77 + BOOK 136: CROSS-MARKET LEAD-LAG (with R² recalibration) ──
    # When this ticker is a known follower (e.g. 3USL.L), check if its leader (e.g. SPY)
    # has moved significantly and the follower is lagging.
    # Book 136: R² recalibration runs every 5 min; disabled pairs are skipped.
    lead_lag_signal = None
    try:
        # Book 136: trigger periodic R² recalibration + nightly lag reload
        _recalibrate_lead_lag_correlations()
        _nightly_recalibrate_lead_lag_optimal_lags()

        symbol = ticker_symbols.get(ticker_id, "")
        pairs_for_follower = _FOLLOWER_TO_LEADERS.get(symbol, [])
        if pairs_for_follower and bars_5m and len(bars_5m) >= 5:
            from python_brain.strategies.lead_lag import detect_lead_lag_signal
            follower_returns = []
            for i in range(1, min(6, len(bars_5m))):
                if bars_5m[i - 1]["close"] > 0:
                    follower_returns.append(
                        (bars_5m[i]["close"] - bars_5m[i - 1]["close"]) / bars_5m[i - 1]["close"]
                    )
            for leader_sym, pair_name in pairs_for_follower:
                # Book 136: Skip pairs with R² below disable threshold
                pair_r2_info = _lead_lag_r2.get(pair_name, {})
                if pair_r2_info.get("status") == "disabled":
                    continue  # R² < 0.50 — pair temporarily disabled

                leader_closes = list(_leader_bar_closes.get(leader_sym, []))
                if len(leader_closes) >= 6:
                    leader_returns = []
                    for i in range(1, min(6, len(leader_closes))):
                        if leader_closes[i - 1] > 0:
                            leader_returns.append(
                                (leader_closes[i] - leader_closes[i - 1]) / leader_closes[i - 1]
                            )
                    if leader_returns and follower_returns:
                        sig = detect_lead_lag_signal(
                            leader_returns=leader_returns,
                            follower_returns=follower_returns,
                            pair_name=pair_name,
                        )
                        if sig and sig.confidence >= effective_floor:
                            # Book 136: Scale confidence by R²
                            r_squared = pair_r2_info.get("r2", 1.0)
                            scaled_confidence = int(sig.confidence * r_squared)
                            # Re-check floor after scaling
                            if scaled_confidence < effective_floor:
                                continue
                            kelly = _kelly_for(scaled_confidence)
                            lead_lag_signal = {
                                "type": "signal", "ticker_id": ticker_id,
                                "direction": "Long",
                                "confidence": scaled_confidence,
                                "kelly_fraction": kelly["kelly_fraction"],
                                "shares": kelly["shares"],
                                "strategy": "LeadLag",
                                "leader": sig.leader_ticker,
                                "follower": sig.follower_ticker,
                                "leader_move_pct": sig.leader_move_pct,
                                "follower_lag_pct": sig.follower_lag_pct,
                                "expected_catchup_pct": sig.estimated_catch_up_pct,
                                "lead_lag_r2": r_squared,
                                "lead_lag_r2_status": pair_r2_info.get("status", "active"),
                                **common_fields,
                            }
                            break  # Take the first valid lead-lag signal
    except Exception:
        pass

    # Calendar Anomaly Modifier (Book 171) — applies to ALL signals
    cal_conf_delta = 0
    cal_kelly_mult = 1.0
    try:
        from python_brain.strategies.calendar_anomalies import get_calendar_adjustment
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        cal_adj = get_calendar_adjustment(
            now.year, now.month, now.day, now.weekday(),
            now.hour, now.minute,
            london_time_secs=msg.get("london_time_secs", 0),
        )
        cal_conf_delta = cal_adj.confidence_delta
        cal_kelly_mult = cal_adj.kelly_multiplier
    except Exception:
        pass

    # ── BOOK 102: EMAT MULTI-ASPECT ATTENTION (SIGNAL GENERATOR) ──
    emat_signal = None
    try:
        from python_brain.ml.emat_model import EMATModel
        import numpy as _np_emat
        if bars_5m and len(bars_5m) >= 20:
            _prices = _np_emat.array([b["close"] for b in bars_5m[-60:]])
            _vols = _np_emat.array([b["volume"] for b in bars_5m[-60:]])
            _emat = EMATModel(d_model=32, n_heads=2)
            _emat_pred = _emat.forward(
                features=_np_emat.column_stack([_prices, _vols]),
                trend_indicator=ind["adx"],
                vol_regime=ind.get("vol_regime", "NORMAL"),
            )
            if _emat_pred.get("prediction", 0) > 0.1 and _emat_pred.get("confidence", 0) > 0.55:
                emat_conf = min(85, int(50 + _emat_pred["confidence"] * 40))
                if emat_conf >= effective_floor:
                    kelly = _kelly_for(emat_conf)
                    emat_signal = {
                        "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                        "confidence": emat_conf,
                        "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                        "strategy": "EMAT_Attention", **common_fields,
                    }
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 157: TEMPORAL ATTENTION (SIGNAL GENERATOR) ──
    attn_signal = None
    try:
        from python_brain.ml.attention_trading import TemporalAttentionSignal
        import numpy as _np_attn
        if bars_5m and len(bars_5m) >= 20:
            _p = _np_attn.array([b["close"] for b in bars_5m[-60:]])
            _v = _np_attn.array([b["volume"] for b in bars_5m[-60:]])
            _ind_arr = _np_attn.array([ind.get("rsi", 50)] * len(_p))
            _attn_gen = TemporalAttentionSignal(n_features=3, seq_len=len(_p))
            _asig = _attn_gen.generate_signal(_p, _v, _ind_arr)
            if _asig.get("direction") == "long" and _asig.get("confidence", 0) > 0.55:
                attn_conf = min(85, int(50 + _asig["confidence"] * 35))
                if attn_conf >= effective_floor:
                    kelly = _kelly_for(attn_conf)
                    attn_signal = {
                        "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                        "confidence": attn_conf,
                        "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                        "strategy": "TemporalAttention", **common_fields,
                    }
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 151: SWARM PREDICTOR (SIGNAL GENERATOR) ──
    swarm_signal = None
    try:
        from python_brain.ml.swarm_predictor import SwarmSimulator
        if bars_5m and len(bars_5m) >= 5:
            _swarm = SwarmSimulator(n_agents=50)  # Smaller for hot path
            for b in bars_5m[-10:]:
                _swarm.step(market_price=b["close"], volume=b["volume"])
            _spred = _swarm.get_prediction()
            if _spred.get("direction") == "bullish" and _spred.get("confidence", 0) > 0.6:
                sw_conf = min(80, int(50 + _spred["confidence"] * 30))
                if sw_conf >= effective_floor:
                    kelly = _kelly_for(sw_conf)
                    swarm_signal = {
                        "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                        "confidence": sw_conf,
                        "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                        "strategy": "SwarmPredictor", **common_fields,
                    }
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 204: HFT PROBABILITY (SIGNAL GENERATOR) ──
    hft_signal = None
    try:
        from python_brain.strategies.hft_probability import HFTProbabilitySignal
        if len(ticks) >= 20:
            if not hasattr(_generate_signals, "_hft"):
                _generate_signals._hft = HFTProbabilitySignal()
            _hft = _generate_signals._hft
            _hft_sig = _hft.generate({
                "price": msg["last"], "volume": msg.get("volume", 0),
                "spread": ind.get("spread_pct", 0.1),
                "prices": [t["last"] for t in ticks[-20:]],
            })
            if _hft_sig and _hft_sig.get("direction") == "long":
                hft_conf = int(_hft_sig.get("confidence", 55))
                if hft_conf >= effective_floor:
                    kelly = _kelly_for(hft_conf)
                    hft_signal = {
                        "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                        "confidence": hft_conf,
                        "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                        "strategy": "HFT_Probability", **common_fields,
                    }
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 206: NEGRISK ARBITRAGE (SIGNAL GENERATOR) ──
    negrisk_signal = None
    try:
        from python_brain.strategies.negrisk_arbitrage import LeveragedETFArbitrage
        symbol = ticker_symbols.get(ticker_id, "")
        if symbol and msg.get("underlying_return"):
            if not hasattr(_generate_signals, "_lev_arb"):
                _generate_signals._lev_arb = LeveragedETFArbitrage()
            _lev_arb = _generate_signals._lev_arb
            _arb = _lev_arb.check_leverage_ratio(
                etp_return=msg.get("intraday_return", 0),
                underlying_return=msg["underlying_return"],
                leverage=msg.get("leverage", 3),
            )
            if _arb and _arb.get("signal") and _arb.get("confidence", 0) >= effective_floor:
                kelly = _kelly_for(int(_arb["confidence"]))
                negrisk_signal = {
                    "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                    "confidence": int(_arb["confidence"]),
                    "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                    "strategy": "NegRiskArb", "tracking_error": _arb.get("tracking_error", 0),
                    **common_fields,
                }
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 166: HIGH-FLYER RETAIL FLOW + MULTI-FACTOR (SIGNAL GENERATOR) ──
    highflyer_signal = None
    try:
        from python_brain.ml.high_flyer_strategies import HighFlyerSignalGenerator
        import numpy as _np_hf
        if bars_5m and len(bars_5m) >= 10:
            if not hasattr(_generate_signals, "_hf"):
                _generate_signals._hf = HighFlyerSignalGenerator()
            _hf = _generate_signals._hf
            _hf_result = _hf.generate(
                features={
                    "prices": _np_hf.array([b["close"] for b in bars_5m[-20:]]),
                    "volumes": _np_hf.array([b["volume"] for b in bars_5m[-20:]]),
                    "trades": [{"size": b["volume"], "price": b["close"]} for b in bars_5m[-10:]],
                },
                volume=msg.get("volume", 0),
                trades=[],
            )
            if _hf_result and _hf_result.get("direction") == "long" and _hf_result.get("confidence", 0) >= effective_floor:
                _hf_conf = int(_hf_result["confidence"])
                kelly = _kelly_for(_hf_conf)
                highflyer_signal = {
                    "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                    "confidence": _hf_conf,
                    "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                    "strategy": "HighFlyer", "retail_flow": _hf_result.get("retail_ratio", 0),
                    **common_fields,
                }
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 125/126: PAIRS SPREAD REVERSION (SIGNAL GENERATOR) ──
    pairs_signal = None
    try:
        from python_brain.strategies.pairs import detect_pair_signal
        symbol = ticker_symbols.get(ticker_id, "")
        if symbol and bars_5m and len(bars_5m) >= 20:
            _pair_sig = detect_pair_signal(
                symbol=symbol,
                prices=[b["close"] for b in bars_5m[-30:]],
                hurst=ind["hurst"],
            )
            if _pair_sig and _pair_sig.get("confidence", 0) >= effective_floor:
                _pc = int(_pair_sig["confidence"])
                kelly = _kelly_for(_pc)
                pairs_signal = {
                    "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                    "confidence": _pc,
                    "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                    "strategy": "PairsReversion", "z_score": _pair_sig.get("z_score", 0),
                    **common_fields,
                }
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 203: COPY TRADING / SMART MONEY (SIGNAL GENERATOR) ──
    copy_signal = None
    try:
        from python_brain.strategies.copy_trading import SignalReplicator
        if len(ticks) >= 10:
            if not hasattr(_generate_signals, "_rep"):
                _generate_signals._rep = SignalReplicator()
            _rep = _generate_signals._rep
            _cs = _rep.replicate(
                leader_signal=msg.get("leader_signal"),
                own_equity=msg.get("equity", 10000),
            )
            if _cs and _cs.get("confidence", 0) >= effective_floor:
                _cc = int(_cs["confidence"])
                kelly = _kelly_for(_cc)
                copy_signal = {
                    "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                    "confidence": _cc,
                    "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                    "strategy": "CopyTrading", **common_fields,
                }
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 5: OVERNIGHT REVERSAL (NIGHT RIDER) ──
    # Stock declined >1.5% during day, NOT news-driven, volume >1.5x average.
    # Enter near close for overnight recovery. Exit at open +15 minutes.
    # Expected 55%+ WR on non-news-driven declines with volume confirmation.
    # Block on earnings day, inverse ETPs, and mean-reverting regimes.
    night_rider_signal = None
    try:
        from datetime import datetime as _dt_nr, timezone as _tz_nr
        _ts_nr = msg.get("timestamp_ns", 0)
        if _ts_nr > 0 and bars_5m and len(bars_5m) >= 50:
            # Filter 1: Regime check — block mean-reverting/random (need trending for reversal)
            if hurst_regime not in ("trending",):
                pass  # Exit early if not trending regime
            else:
                _utc_nr = _dt_nr.fromtimestamp(_ts_nr / 1_000_000_000, tz=_tz_nr.utc)
                _h_nr = _utc_nr.hour
                _sym_nr = ticker_symbols.get(ticker_id, "")
                _exch_nr = _get_exchange_for_symbol(_sym_nr) if _sym_nr else ""

                # Filter 2: Block inverse ETPs (Night Rider is long-only)
                _is_inverse = _sym_nr.startswith("3S") or _sym_nr in (
                    "QQQS.L", "3USS.L", "3STS.L", "3SNV.L", "3SAP.L", "3SMS.L", "3SEM.L",
                )
                if not _is_inverse:
                    # Only trigger in last 60 minutes of trading session
                    _nr_window = False
                    if _exch_nr == "LSE" and 15 <= _h_nr < 16:
                        _nr_window = True
                    elif _exch_nr == "US" and (20 <= _h_nr or _h_nr < 21):
                        _nr_window = True
                    elif _exch_nr in ("XETRA", "EURONEXT") and 16 <= _h_nr < 17:
                        _nr_window = True

                    if _nr_window:
                        # Filter 3: Check for earnings day (skip high-volatility event days)
                        _skip_earnings = False
                        try:
                            from python_brain.events.event_calendar import get_event_calendar
                            _evt_cal = get_event_calendar()
                            _near_evt = _evt_cal.is_near_event(_ts_nr)
                            if _near_evt and hasattr(_near_evt, 'event_type') and _near_evt.event_type == "EARNINGS":
                                _skip_earnings = True
                        except Exception:
                            pass  # Fail-open: continue if calendar unavailable

                        if not _skip_earnings:
                            # Check: declined >1.5% from day open
                            _day_open = bars_5m[0]["open"] if len(bars_5m) < 80 else bars_5m[-78]["open"]
                            _current_nr = msg.get("last", 0)
                            _day_return = (_current_nr - _day_open) / _day_open if _day_open > 0 else 0
                            _day_decline_pct = -_day_return * 100.0  # Convert to positive decline %

                            # Check: volume >1.5x average
                            _vol_ok = ind.get("rvol", 1.0) > 1.5
                            _rvol_val = ind.get("rvol", 1.0)

                            # Enhanced: not news-driven (no single bar > 3% of session move)
                            _news_driven = False
                            if _day_decline_pct > 0.1:  # Only check if meaningful decline
                                _threshold_pct = _day_decline_pct * 0.03  # 3% of session move
                                for b in bars_5m[-20:]:
                                    _bar_move = abs(b["high"] - b["low"]) / (b["open"] + 1e-9) * 100.0
                                    if _bar_move > _threshold_pct:
                                        _news_driven = True
                                        break

                            if _day_return < -0.015 and _vol_ok and not _news_driven:
                                # Base confidence: 60
                                _nr_conf = 60.0

                                # Boost +10 if deeper decline > 2.5%
                                if _day_decline_pct > 2.5:
                                    _nr_conf += 10.0

                                # Boost +10 if RVOL > 2.0
                                if _rvol_val > 2.0:
                                    _nr_conf += 10.0

                                # Boost +5 if market breadth improved in last hour (last 12 bars in 5-min)
                                try:
                                    _recent_bars = bars_5m[-12:] if len(bars_5m) >= 12 else bars_5m
                                    if len(_recent_bars) >= 3:
                                        _up_bars_recent = sum(1 for b in _recent_bars if b["close"] > b["open"])
                                        if _up_bars_recent >= len(_recent_bars) * 0.6:  # 60%+ up bars
                                            _nr_conf += 5.0
                                except Exception:
                                    pass

                                # Legacy: low VPIN bonus (retained for compatibility)
                                if vpin < 0.50:
                                    _nr_conf += 5.0  # Low informed selling = retail panic

                                _nr_conf = min(_nr_conf, 80.0)
                                if _nr_conf >= conf_floor:
                                    kelly = _kelly_for(_nr_conf)
                                    night_rider_signal = {
                                        "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                                        "confidence": _nr_conf,
                                        "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                                        "strategy": "NightRider", "day_return": round(_day_return * 100, 2),
                                        "day_decline_pct": round(_day_decline_pct, 2),
                                        "rvol_entry": round(_rvol_val, 2),
                                        "suggested_max_hold_hours": 16,  # Exit at open +15min
                                        "exit_urgency_ramp_hours": 12,
                                        **common_fields,
                                    }
    except Exception:
        pass

    # ── BOOK 24: FOMC/CPI/NFP DRIFT CAPTURE (SIGNAL GENERATOR) ──
    # Post-event drift: enter in direction of market reaction during drift window
    drift_signal = None
    try:
        from python_brain.strategies.fomc_drift import get_drift_signal
        _evt_ctx = msg.get("_event_context")
        if _evt_ctx and _evt_ctx.in_drift_window:
            _drift = get_drift_signal(_evt_ctx.event_type, abs(_evt_ctx.minutes_to),
                                       _evt_ctx.direction_bias)
            if _drift and _drift.confidence >= conf_floor:
                kelly = _kelly_for(_drift.confidence)
                drift_signal = {
                    "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                    "confidence": _drift.confidence,
                    "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                    "strategy": "EventDrift", "event": _evt_ctx.event_name,
                    "drift_mins_since": _drift.minutes_since,
                    "drift_expected_duration": _drift.expected_duration_mins,
                    **common_fields,
                }
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 125: COINTEGRATION PAIRS (SIGNAL GENERATOR) ──
    coint_signal = None
    try:
        from python_brain.strategies.pairs_cointegration import CointPairsTracker
        symbol = ticker_symbols.get(ticker_id, "")
        if symbol and bars_5m and len(bars_5m) >= 30:
            if not hasattr(_generate_signals, "_coint"):
                _generate_signals._coint = CointPairsTracker()
            _coint = _generate_signals._coint
            # Feed price for pair tracking
            _coint.update_price(symbol, msg.get("last", 0))
            _cs = _coint.check_signal(symbol, [b["close"] for b in bars_5m[-30:]])
            if _cs and _cs.confidence >= conf_floor:
                kelly = _kelly_for(int(_cs.confidence))
                coint_signal = {
                    "type": "signal", "ticker_id": ticker_id, "direction": "Long",
                    "confidence": int(_cs.confidence),
                    "kelly_fraction": kelly["kelly_fraction"], "shares": kelly["shares"],
                    "strategy": "CointPairs", "z_score": round(_cs.z_score, 3),
                    "half_life": round(_cs.half_life, 1), "pair": _cs.pair_name,
                    "long_leg": _cs.long_leg, **common_fields,
                }
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 144: CONFORMAL DIRECTIONAL GATE (SIGNAL MODIFIER) ──
    try:
        from python_brain.strategies.conformal_directional import check_directional_gate
        from python_brain.ml.conformal_signals import OnlineConformalTracker
        if not hasattr(_generate_signals, "_conf_tracker"):
            _generate_signals._conf_tracker = OnlineConformalTracker()
        _ctracker = _generate_signals._conf_tracker
        _interval = _ctracker.get_prediction_interval() if hasattr(_ctracker, 'get_prediction_interval') else None
        if _interval and hasattr(_interval, 'low') and hasattr(_interval, 'high'):
            _dir, _frac = check_directional_gate(_interval.low, _interval.high)
            if _dir == "NO_TRADE":
                msg["_conformal_no_trade"] = True
            elif _dir == "BUY" and _frac > 0.1:
                msg["_conformal_fraction"] = _frac
            elif _dir == "REJECT":
                msg["_conformal_no_trade"] = True  # Too uncertain
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 22: BREAKOUT 3-CRITERIA VALIDATION ──
    # Apply to momentum signals: require close beyond level + 1.5x volume + 3-bar hold.
    # Without all 3: 22% WR. With all 3: 68% WR. This is a signal MODIFIER, not a generator.
    if bars_5m and len(bars_5m) >= 5:
        _bk_close_beyond = False
        _bk_volume_confirmed = False
        _bk_no_reversal = False
        # Check 1: Close beyond 20-bar high (breakout level)
        if len(bars_5m) >= 20:
            _20bar_high = max(b["high"] for b in bars_5m[-21:-1])
            if bars_5m[-1]["close"] > _20bar_high:
                _bk_close_beyond = True
        # Check 2: Volume >= 1.5x 20-period average
        if len(bars_5m) >= 20 and ind.get("rvol", 1.0) >= 1.5:
            _bk_volume_confirmed = True
        # Check 3: No reversal in last 3 bars (all 3 closed above breakout level)
        if _bk_close_beyond and len(bars_5m) >= 3:
            _bk_no_reversal = all(
                b["close"] > bars_5m[-4]["close"] for b in bars_5m[-3:]
            )
        _breakout_score = sum([_bk_close_beyond, _bk_volume_confirmed, _bk_no_reversal])
        # Apply to momentum signals in the list that follows
        msg["_breakout_score"] = _breakout_score
        msg["_breakout_criteria"] = {
            "close_beyond": _bk_close_beyond,
            "volume_confirmed": _bk_volume_confirmed,
            "no_reversal": _bk_no_reversal,
        }

    # Return ALL signals sorted by confidence — no artificial bottleneck.
    # Stage 4 selects the best after applying adjustments to every signal.
    all_signals = [s for s in [
        vanguard_signal, orchestrator_signal, ibs_signal, volexp_signal, orb_signal, gap_signal,
        s1_signal, s2_signal, s3_signal, s4_signal, s5_signal, fomc_signal, s6_signal, s7_signal,
        vol_comp_signal, rebal_signal, nav_signal, alpha_signal, lead_lag_signal,
        emat_signal, attn_signal, swarm_signal, hft_signal, negrisk_signal,
        highflyer_signal, pairs_signal, copy_signal, night_rider_signal,
        drift_signal, coint_signal,
    ] if s]

    # ── BOOK 179: CAPITAL-PHASE STRATEGY FILTERING (CONSUME _phase1_strategies) ──
    _phase1 = msg.get("_phase1_strategies")
    if _phase1:
        all_signals = [s for s in all_signals if s.get("strategy", "") in _phase1
                       or s.get("strategy", "").startswith("S")
                       or s.get("strategy", "") in ("VanguardSniper", "ApexScout")]
    elif msg.get("_phase2_enabled"):
        _PHASE2_BLOCKED = {"PairsReversion", "CointPairs"}  # Pairs need £50K+ (double spread)
        all_signals = [s for s in all_signals if s.get("strategy", "") not in _PHASE2_BLOCKED]

    # ── BOOK 135: FRACTIONAL DIFFERENTIATION SIGNAL MODIFIER ──
    # If FD value available, use as directional filter: positive = momentum support.
    _fd_val = ind.get("frac_diff_value")
    if _fd_val is not None and all_signals:
        for sig in all_signals:
            if sig.get("direction") == "Long":
                if _fd_val > 0:
                    sig["confidence"] = min(100, sig["confidence"] + 2)
                    sig["fracdiff_support"] = True
                elif _fd_val < -0.001:
                    sig["confidence"] = max(0, sig["confidence"] - 3)
                    sig["fracdiff_counter_trend"] = True

    # ── BOOK 23: LIGHTGBM ENTRY CLASSIFIER (UNIVERSAL FILTER) ──
    # P(win) > 0.65 → +5 conf. P(win) < 0.40 → -8 conf. Not a standalone signal.
    try:
        from python_brain.ml.lightgbm_classifier import LGBMEntryClassifier
        if not hasattr(_generate_signals, "_lgbm"):
            _generate_signals._lgbm = LGBMEntryClassifier()
        _lgbm = _generate_signals._lgbm
        _features = _lgbm.extract_features(ind, msg, common_fields)
        if _features is not None:
            _lgbm_prob = _lgbm.predict(_features)
            if _lgbm_prob is not None:
                for sig in all_signals:
                    sig["lgbm_win_prob"] = round(_lgbm_prob, 3)
                    if _lgbm_prob > 0.65:
                        sig["confidence"] = min(100, sig["confidence"] + 5)
                        sig["lgbm_boost"] = True
                    elif _lgbm_prob < 0.40:
                        sig["confidence"] = max(0, sig["confidence"] - 8)
                        sig["lgbm_reject"] = True
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 23b: STRATEGY-LEVEL ENTRY CLASSIFIER (block/reduce/boost) ──
    # Applies the 48-feature classifier with hard blocking (P<0.35) and
    # confidence adjustments. Only active when ONNX model is loaded.
    if _HAS_ENTRY_CLF and all_signals:
        try:
            if not hasattr(_generate_signals, "_entry_clf"):
                _generate_signals._entry_clf = _EntryClassifier()
            _eclf = _generate_signals._entry_clf
            if _eclf._loaded or not _eclf._load_attempted:
                _filtered = []
                for _sig in all_signals:
                    _result = _eclf.apply_to_signal(_sig, ind, msg)
                    if _result is not None:
                        _filtered.append(_result)
                all_signals = _filtered
        except Exception:
            pass  # Fail-open: classifier error → no filtering

    # ── BOOK 94: STRATEGY-SPECIFIC CLOSE CUTOFF FILTER ──
    _mtc = msg.get("_mins_to_close", 999)
    _STRATEGY_CUTOFFS = {
        "IBS_MeanReversion": 30, "S2_Reversion": 30,
        "Momentum": 60, "VolExpansion": 60, "S3_MacroTrend": 60,
        "S1_Microstructure": 45, "HighFlyer": 45,
        "NightRider": 0,  # NightRider WANTS to be near close — no penalty
        "EventDrift": 15,  # Event drift is fast — allow close to close
    }
    if _mtc < 120:  # Only filter in last 2 hours
        for sig in all_signals:
            _strat_cutoff = _STRATEGY_CUTOFFS.get(sig.get("strategy", ""), 45)
            if sig.get("strategy") != "NightRider" and _mtc < _strat_cutoff:
                sig["confidence"] = max(0, sig["confidence"] - 15)
                sig["close_proximity_penalty"] = f"{_mtc}min < {_strat_cutoff}min cutoff"

    # ── BREAKOUT VALIDATION: apply to momentum signals ──
    _bk_score = msg.get("_breakout_score", 0)
    _MOMENTUM_STRATS = {"Momentum", "VolExpansion", "ORB_Breakout", "HighFlyer", "S3_MacroTrend"}
    for sig in all_signals:
        if sig.get("strategy") in _MOMENTUM_STRATS:
            sig["breakout_score"] = _bk_score
            sig["breakout_criteria"] = msg.get("_breakout_criteria", {})
            if _bk_score == 3:
                # All 3 criteria met: 68% WR. STRONG boost.
                sig["confidence"] = min(100, sig["confidence"] + 10)
                sig["breakout_validated"] = True
            elif _bk_score == 2:
                # 2 of 3: decent but not institutional grade
                sig["confidence"] = min(100, sig["confidence"] + 3)
            elif _bk_score <= 1:
                # 0-1 criteria: high false signal risk. Penalize.
                sig["confidence"] = max(0, sig["confidence"] - 8)
                sig["breakout_warning"] = "weak_breakout"

    # Apply calendar anomaly adjustments to ALL signals (Book 171)
    if cal_conf_delta != 0 or cal_kelly_mult != 1.0:
        for sig in all_signals:
            sig["confidence"] = max(0, min(100, sig["confidence"] + cal_conf_delta))
            sig["kelly_fraction"] *= cal_kelly_mult

    # ── BOOK 22: SQUEEZE CONFIDENCE BOOST ──
    # If volatility squeeze active, boost breakout signal confidence
    if common_fields.get("squeeze_release", False):
        for sig in all_signals:
            sig["confidence"] = min(100, sig["confidence"] + 15)
            sig["squeeze_boost"] = True
    elif common_fields.get("squeeze_on", False):
        # Squeeze on but not released — slightly reduce (waiting for breakout)
        for sig in all_signals:
            sig["confidence"] = max(0, sig["confidence"] - 5)

    # ── BOOK 171: TURN-OF-MONTH (TOM) CONFIDENCE BOOST ──
    # T-1 to T+3 captures ~75% of monthly returns
    try:
        from datetime import datetime as _dt_tom, timezone as _tz_tom
        _ts_tom = msg.get("timestamp_ns", 0)
        if _ts_tom > 0:
            _d = _dt_tom.fromtimestamp(_ts_tom / 1_000_000_000, tz=_tz_tom.utc)
            _dom = _d.day
            _days_in_month = 31  # Approximate
            try:
                import calendar as _cal_mod
                _days_in_month = _cal_mod.monthrange(_d.year, _d.month)[1]
            except Exception:
                pass
            # TOM window: last day of month (T-1) through day 3 (T+3)
            _tom_graded = 0
            if _dom >= _days_in_month:  # Last day of month (T-1)
                _tom_graded = 6
            elif _dom == 1:  # First day (T+0)
                _tom_graded = 8
            elif _dom == 2:  # T+1
                _tom_graded = 10  # Peak
            elif _dom == 3:  # T+2
                _tom_graded = 7
            elif _dom == 4:  # T+3
                _tom_graded = 4
            if _tom_graded > 0:
                for sig in all_signals:
                    sig["confidence"] = min(100, sig["confidence"] + _tom_graded)
                    sig["tom_boost"] = _tom_graded
    except Exception:
        pass

    # ── BOOK 170: MOMENTUM ADVERSARIAL PENALTY ──
    # Momentum entries face HFT front-running risk → reduce confidence
    for sig in all_signals:
        _strat = sig.get("strategy", "")
        if _strat in ("Momentum", "VolExpansion", "ORB_Breakout", "HighFlyer"):
            sig["confidence"] = max(0, sig["confidence"] - 5)
            sig["momentum_adv_penalty"] = True

    # AUTONOMY: Filter out auto-killed strategies (live Sharpe < -1.0 over 30+ trades)
    if _auto_killed_strategies:
        all_signals = [s for s in all_signals if s.get("strategy", "") not in _auto_killed_strategies]

    # ── BOOK 42: CONDITIONAL HEDGING ──
    # Monitor hedge signals and generate hedge allocations if thresholds met.
    # Hedge signals are appended to all_signals for consideration alongside regular signals.
    try:
        hedge_sigs = _apply_conditional_hedge(msg, all_signals)
        if hedge_sigs:
            all_signals.extend(hedge_sigs)
            sys.stderr.write(
                f"HEDGE_SIGNALS: {len(hedge_sigs)} hedge signals generated "
                f"(status={_hedge_state.get('status')})\n"
            )
            sys.stderr.flush()
    except Exception as e:
        sys.stderr.write(f"HEDGE_ERROR: _apply_conditional_hedge failed: {e}\n")
        sys.stderr.flush()

    all_signals.sort(key=lambda s: s["confidence"], reverse=True)
    return all_signals


def _apply_adjustments(ticker_id, msg, ind, all_signals):
    """Stage 4: Apply confidence adjustments to ALL signals, select best, classify, size."""
    if not all_signals:
        return None
    hurst_regime = ind["hurst_regime"]

    # P2-#7: LSE confidence boost DELETED — was +20 blanket boost that inflated
    # marginal signals (conf 45-49) above floor, causing false entries.

    # ── BOOK 82: ENSEMBLE REGIME DETECTION (Fast-Noisy + Slow-Accurate) ──
    try:
        from python_brain.risk.regime_ensemble import get_regime_ensemble, regime_confidence_adjustment
        _regime_result = get_regime_ensemble().on_tick(
            hurst=ind.get("hurst", 0.5),
            vpin=ind.get("vpin", 0.5),
            rvol=ind.get("rvol", 1.0),
            adx=ind.get("adx", 15.0),
            spread_pct=ind.get("spread_pct", 0.1),
            drawdown_pct=msg.get("drawdown_pct", 0.0),
            timestamp_secs=msg.get("timestamp_ns", 0) / 1e9 if msg.get("timestamp_ns") else 0,
        )
        regime_penalty = regime_confidence_adjustment(_regime_result)
        if regime_penalty != 0:
            for sig in all_signals:
                sig["confidence"] = max(0, min(100, sig["confidence"] + regime_penalty))
                sig["regime_alert"] = _regime_result.action
    except ImportError:
        pass

    # ── BOOK 124: VOL REGIME CLUSTERING (5-state) ──
    # Provides richer regime classification and sizing multiplier.
    _vol_regime_sizing = 1.0
    try:
        from python_brain.risk.vol_regime_cluster import get_vol_regime
        _vol_result = get_vol_regime(
            hurst=ind.get("hurst", 0.5),
            rvol=ind.get("rvol", 1.0),
            vpin=ind.get("vpin", 0.5),
            adx=ind.get("adx", 15.0),
            spread_pct=ind.get("spread_pct", 0.1),
            vol_slope=ind.get("vol_slope", 0.0),
        )
        _vol_regime_sizing = _vol_result.sizing_mult
        for sig in all_signals:
            sig["vol_regime"] = _vol_result.regime
            sig["vol_regime_confidence"] = _vol_result.confidence
            sig["vol_regime_stress"] = _vol_result.regime_score
    except ImportError:
        pass

    symbol = ticker_symbols.get(ticker_id, "")

    # ── DRAWDOWN-INDEXED TIGHTENING LADDER (Book 85 upgrade) ──
    # Smooth degradation: sizing and confidence scale DOWN proportionally as drawdown deepens.
    # No more binary threshold — every additional % of drawdown reduces capacity smoothly.
    # Sacred limit at -8%: FULL HALT.
    dd = msg.get("drawdown_pct", 0.0)
    is_inverse = (symbol.startswith("3S") or symbol.startswith("5S") or
                  (symbol.endswith("S.L") and len(symbol) <= 7) or
                  symbol in ("QQQS.L", "3USS.L", "3STS.L", "3SAM.L", "3SNV.L", "3SAP.L", "3SMS.L", "3SEM.L"))
    if dd > 0.08:
        # Sacred limit -8%: HALT — no entries at all
        return None
    elif dd > 0.01:
        # Smooth degradation: dd_severity = dd / 0.08 (0 at 0%, 1.0 at 8%)
        _dd_severity = min(1.0, dd / 0.08)
        _dd_sizing = max(0.15, 1.0 - _dd_severity)  # Floor at 15% sizing
        _dd_conf_penalty = int(_dd_severity * 25)  # Up to -25 confidence at 8%
        for sig in all_signals:
            if is_inverse:
                # Inverse ETPs BENEFIT from drawdown (hedge) — boost instead
                _inv_boost = min(int(dd * 300), 15)
                sig["confidence"] = min(sig["confidence"] + _inv_boost, 100)
                sig["drawdown_inverse_boost"] = _inv_boost
            else:
                sig["confidence"] = max(sig["confidence"] - _dd_conf_penalty, 0)
                sig["kelly_fraction"] *= _dd_sizing
                sig["shares"] = max(1, int(sig["shares"] * _dd_sizing))
                sig["drawdown_severity"] = round(_dd_severity, 2)
                sig["drawdown_sizing"] = round(_dd_sizing, 2)

    # Hour-of-day weights — applied to ALL signals
    hw = _load_hour_weights()
    if hw:
        ts_ns = msg.get("timestamp_ns", 0)
        if ts_ns > 0:
            from datetime import datetime as _dt_hw, timezone as _tz_hw
            utc_hour = _dt_hw.fromtimestamp(ts_ns / 1_000_000_000, tz=_tz_hw.utc).hour
            w = hw.get(utc_hour, 1.0)
            if w != 1.0:
                for sig in all_signals:
                    sig["confidence"] = max(0, min(100, int(sig["confidence"] * w)))

    # ── OPTIMAL ENTRY TIME WINDOWS (22-hour multi-exchange) ──
    # Exchange-aware: LSE, US, XETRA, EURONEXT, HKEX, TSE, SGX.
    # Each exchange has prime windows (best follow-through) and dead zones (low edge).
    # Expressed in local exchange time (UTC + offset).
    _ts_entry = msg.get("timestamp_ns", 0)
    if _ts_entry > 0:
        from datetime import datetime as _dt_ew, timezone as _tz_ew, timedelta as _td_ew
        _utc_dt_ew = _dt_ew.fromtimestamp(_ts_entry / 1_000_000_000, tz=_tz_ew.utc)
        _utc_mins = _utc_dt_ew.hour * 60 + _utc_dt_ew.minute
        _sym_ew = ticker_symbols.get(ticker_id, "")
        _exch_ew = _get_exchange_for_symbol(_sym_ew) if _sym_ew else ""
        _offset_hrs = _exchange_utc_offset(_exch_ew, _utc_dt_ew)
        _local_dt = _utc_dt_ew + _td_ew(hours=_offset_hrs)
        _local_mins = _local_dt.hour * 60 + _local_dt.minute

        # Universal pattern across all exchanges:
        # Minutes since local open: 60-150min = prime momentum (trend established)
        # Minutes since local open: 150-210min = midday lull
        # Last 60-90min = institutional close flows (good continuation)
        # First 15min = already blocked by quality gate
        _EXCHANGE_OPEN_LOCAL = {
            "LSE": 480, "XETRA": 540, "EURONEXT": 540,  # 08:00, 09:00, 09:00 local
            "US": 570, "HKEX": 570, "TSE": 540, "SGX": 540,  # 09:30, 09:30, 09:00, 09:00
        }
        _open_local = _EXCHANGE_OPEN_LOCAL.get(_exch_ew, 540)
        _mins_since_open = _local_mins - _open_local

        if 60 <= _mins_since_open <= 150:
            # 1-2.5hrs after open = prime momentum window (all exchanges)
            for sig in all_signals:
                sig["confidence"] = min(100, sig["confidence"] + 3)
                sig["entry_window"] = "prime"
        elif 150 < _mins_since_open <= 240:
            # 2.5-4hrs after open = midday lull (lower follow-through)
            for sig in all_signals:
                sig["confidence"] = max(0, sig["confidence"] - 4)
                sig["entry_window"] = "midday_lull"

        # US open spillover boost for European instruments (14:30 UTC)
        if _exch_ew in ("LSE", "XETRA", "EURONEXT") and 870 <= _utc_mins <= 930:
            for sig in all_signals:
                sig["confidence"] = min(100, sig["confidence"] + 3)
                sig["entry_window"] = "us_open_spillover"

        # ── EXCHANGE-SPECIFIC STRATEGY ROUTING ──
        _leverage = msg.get("leverage", 1)
        _is_etp = _leverage >= 2
        _is_inverse = any(inv in _sym_ew.upper() for inv in ("QQQS", "3USS", "SUK2", "NV3S", "TS3S", "3S", "5S"))

        if _exch_ew in ("LSE", "XETRA", "EURONEXT"):
            # ── EUROPEAN SESSION ──
            if _mins_since_open < 90:
                # Opening 1.5hrs: momentum dominates (institutional order flow)
                for sig in all_signals:
                    if sig.get("strategy") in ("IBS_MeanReversion", "S2_Reversion"):
                        sig["confidence"] = max(0, sig["confidence"] - 8)
                        sig["session_note"] = "EU_MR_penalized_opening"
                    elif sig.get("strategy") in ("Momentum", "VolExpansion", "S1_Microstructure"):
                        sig["confidence"] = min(100, sig["confidence"] + 3)
                        sig["session_note"] = "EU_momentum_boosted_opening"
            elif 150 < _mins_since_open <= 270:
                # Midday lull: momentum fails, mean-reversion works
                for sig in all_signals:
                    if sig.get("strategy") in ("Momentum", "VolExpansion", "S3_MacroTrend"):
                        sig["confidence"] = max(0, sig["confidence"] - 5)
                        sig["session_note"] = "EU_momentum_penalized_midday"
                    elif sig.get("strategy") in ("IBS_MeanReversion", "S2_Reversion"):
                        sig["confidence"] = min(100, sig["confidence"] + 3)
                        sig["session_note"] = "EU_MR_boosted_midday"
            # ETP-specific: 3x/5x ETPs have volatility drag in choppy conditions
            if _is_etp and _leverage >= 3 and 150 < _mins_since_open <= 270:
                for sig in all_signals:
                    sig["confidence"] = max(0, sig["confidence"] - 3)
                    sig["etp_midday_drag"] = True

        elif _exch_ew == "US":
            # ── US SESSION ──
            if _mins_since_open < 60:
                # First hour (9:30-10:30 ET): most volatile, wide spreads, reversals common
                # Mean-reversion works well on gap fills; momentum is unreliable
                for sig in all_signals:
                    if sig.get("strategy") in ("IBS_MeanReversion", "S2_Reversion"):
                        sig["confidence"] = min(100, sig["confidence"] + 5)
                        sig["session_note"] = "US_MR_boosted_opening_reversals"
                    elif sig.get("strategy") in ("Momentum", "VolExpansion"):
                        sig["confidence"] = max(0, sig["confidence"] - 5)
                        sig["session_note"] = "US_momentum_penalized_opening_noise"
            elif 60 <= _mins_since_open <= 180:
                # Mid-morning (10:30-12:30 ET): best momentum window
                for sig in all_signals:
                    if sig.get("strategy") in ("Momentum", "VolExpansion", "S3_MacroTrend"):
                        sig["confidence"] = min(100, sig["confidence"] + 3)
                        sig["session_note"] = "US_momentum_prime"
            elif 210 <= _mins_since_open <= 330:
                # Power hour (13:30-15:00 ET): institutional re-positioning
                for sig in all_signals:
                    if sig.get("strategy") in ("Momentum", "S1_Microstructure"):
                        sig["confidence"] = min(100, sig["confidence"] + 2)
                        sig["session_note"] = "US_power_hour"

        elif _exch_ew in ("HKEX", "SGX"):
            # ── ASIAN SESSION ──
            if _mins_since_open < 60:
                # Asian open: reacts to overnight US close, momentum follows
                for sig in all_signals:
                    if sig.get("strategy") in ("Momentum", "VolExpansion", "S3_MacroTrend"):
                        sig["confidence"] = min(100, sig["confidence"] + 3)
                        sig["session_note"] = "ASIA_momentum_US_overnight_reaction"
            elif 120 <= _mins_since_open <= 180:
                # Lunch break zone (11:30-12:30 local): thin liquidity
                for sig in all_signals:
                    sig["confidence"] = max(0, sig["confidence"] - 5)
                    sig["session_note"] = "ASIA_lunch_thin_liquidity"

        elif _exch_ew == "TSE":
            # ── TOKYO SESSION ──
            if _mins_since_open < 60:
                for sig in all_signals:
                    if sig.get("strategy") in ("Momentum", "S1_Microstructure"):
                        sig["confidence"] = min(100, sig["confidence"] + 3)
                        sig["session_note"] = "TSE_opening_momentum"
            # TSE has mandatory lunch break 11:30-12:30 JST (150-210 mins after 9:00 open)
            elif 150 <= _mins_since_open <= 210:
                for sig in all_signals:
                    sig["confidence"] = max(0, sig["confidence"] - 8)
                    sig["session_note"] = "TSE_lunch_break"

        # ── CROSS-SESSION EQUITY TYPE RULES ──
        if _is_inverse:
            # Inverse ETPs: best in downtrend confirmation, penalize in uptrends
            if bars_5m and len(bars_5m) >= 20:
                _sma20_inv = sum(b["close"] for b in bars_5m[-20:]) / 20
                if msg.get("last", 0) > _sma20_inv * 1.01:
                    # Price above SMA20 by 1% = uptrend — inverse should be penalized
                    for sig in all_signals:
                        sig["confidence"] = max(0, sig["confidence"] - 5)
                        sig["inverse_uptrend_penalty"] = True
        elif not _is_etp:
            # Single stocks / non-leveraged: can use tighter stops and higher conviction
            for sig in all_signals:
                if sig.get("confidence", 0) >= 70:
                    sig["confidence"] = min(100, sig["confidence"] + 2)
                    sig["equity_type_boost"] = "single_stock_high_conviction"

    # ── MOMENTUM EXHAUSTION DETECTION ──
    # RSI(14) > 80 on 5-min bars + declining RVOL = exhaustion risk for long entries.
    # The move is extended; buying here has poor R:R. Reduce confidence significantly.
    bars_5m = ind.get("bars_5m", [])
    if bars_5m and len(bars_5m) >= 14:
        _rsi14_prices = [b["close"] for b in bars_5m[-14:]]
        _rsi14 = calculate_rsi(_rsi14_prices, period=14)
        _rvol_now = ind.get("rvol", 1.0)
        if _rsi14 is not None and _rsi14 > 80:
            # Extended move — high RSI
            _exhaust_penalty = 8
            if _rvol_now < 1.0:
                _exhaust_penalty = 12  # Exhaustion + drying volume = high reversal risk
            for sig in all_signals:
                if sig.get("direction") == "Long":
                    sig["confidence"] = max(0, sig["confidence"] - _exhaust_penalty)
                    sig["momentum_exhaustion"] = True
                    sig["rsi14_at_entry"] = round(_rsi14, 1)
        elif _rsi14 is not None and _rsi14 < 20 and _rvol_now > 1.5:
            # Oversold with strong volume = potential reversal long opportunity
            for sig in all_signals:
                if sig.get("direction") == "Long" and sig.get("strategy") in ("IBS_MeanReversion", "S2_Reversion"):
                    sig["confidence"] = min(100, sig["confidence"] + 5)
                    sig["oversold_opportunity"] = True

    # ── ATR MINIMUM FILTER ──
    # If the 20-period ATR is less than 0.3% of price, the expected move is too small
    # to overcome costs. Reject signals in dead-flat instruments.
    if bars_5m and len(bars_5m) >= 20:
        _atr_sum = 0.0
        for i in range(1, 20):
            _h = bars_5m[-i]["high"]
            _l = bars_5m[-i]["low"]
            _pc = bars_5m[-i-1]["close"]
            _atr_sum += max(_h - _l, abs(_h - _pc), abs(_l - _pc))
        _atr20 = _atr_sum / 19
        _price = msg.get("last", 1.0)
        _atr_pct = (_atr20 / _price) * 100 if _price > 0 else 0
        if _atr_pct < 0.3:
            # ATR too small — insufficient profit potential to cover costs
            all_signals = [s for s in all_signals if s.get("strategy") not in
                          ("Momentum", "VolExpansion", "S1_Microstructure", "S3_MacroTrend")]
        for sig in all_signals:
            sig["atr_pct"] = round(_atr_pct, 3)

    # ── MULTI-TIMEFRAME TREND ALIGNMENT ──
    # Check if 5-min signal direction aligns with the broader trend (50-bar SMA slope).
    # Aligned = boost, counter-trend = penalize. Simple but effective filter.
    if bars_5m and len(bars_5m) >= 50:
        _sma50 = sum(b["close"] for b in bars_5m[-50:]) / 50
        _sma20 = sum(b["close"] for b in bars_5m[-20:]) / 20
        _trend_up = _sma20 > _sma50  # Short MA above long MA = uptrend
        _price_above_sma50 = msg.get("last", 0) > _sma50
        for sig in all_signals:
            if sig.get("direction") == "Long":
                if _trend_up and _price_above_sma50:
                    sig["confidence"] = min(100, sig["confidence"] + 3)
                    sig["trend_aligned"] = True
                elif not _trend_up and not _price_above_sma50:
                    # Counter-trend long in downtrend — higher risk
                    sig["confidence"] = max(0, sig["confidence"] - 5)
                    sig["trend_aligned"] = False

    # ── GAP FILL DETECTION ──
    # Opening gaps fill ~70% of the time. If price gapped up and is starting to fill,
    # mean-reversion strategies get a confidence boost. If gapped down and filling up,
    # momentum gets a boost (gap-and-go).
    if bars_5m and len(bars_5m) >= 2:
        _first_bar = bars_5m[0] if len(bars_5m) < 50 else bars_5m[-50]  # session start proxy
        _current = msg.get("last", 0)
        _prev_close = bars_5m[-2].get("close", _current) if len(bars_5m) >= 2 else _current
        _gap_pct = (_first_bar["open"] - _prev_close) / _prev_close * 100 if _prev_close > 0 else 0
        if abs(_gap_pct) > 0.5:  # Meaningful gap (>0.5%)
            _gap_filling = (_gap_pct > 0 and _current < _first_bar["open"]) or \
                          (_gap_pct < 0 and _current > _first_bar["open"])
            if _gap_filling:
                for sig in all_signals:
                    if sig.get("strategy") in ("IBS_MeanReversion", "S2_Reversion"):
                        sig["confidence"] = min(100, sig["confidence"] + 5)
                        sig["gap_fill_boost"] = True
                        sig["gap_pct"] = round(_gap_pct, 2)

    # ── VOL-TARGETING POSITION SIZING (Book 80) ──
    # sigma_target = 20% annualized. If instrument vol is higher, position shrinks automatically.
    # N = (sigma_target * capital) / (sigma_instrument * price * leverage)
    _realized_vol = msg.get("realized_vol", 0.30)
    _sigma_target = 0.20  # 20% annualized target vol
    _vol_floor = 0.05
    _vol_ceiling = 1.0
    _clamped_vol = max(_vol_floor, min(_vol_ceiling, _realized_vol))
    _vol_ratio = _sigma_target / _clamped_vol  # > 1 means instrument is less volatile than target
    _leverage_vt = msg.get("leverage", 1)
    if _leverage_vt > 1:
        _vol_ratio /= _leverage_vt  # Leverage amplifies vol — reduce accordingly
    _vol_sizing = min(1.0, max(0.1, _vol_ratio))
    if _vol_sizing < 0.95:  # Only apply if material
        for sig in all_signals:
            sig["kelly_fraction"] *= _vol_sizing
            sig["shares"] = max(1, int(sig["shares"] * _vol_sizing))
            sig["vol_target_sizing"] = round(_vol_sizing, 3)

    # ── ETP VIX-ADJUSTED MAX HOLDING PERIOD (Book 46) ──
    # Attach max hold period to signal based on VIX and leverage level.
    # The Rust exit engine uses this as a time-stop.
    _vix_hold = msg.get("vix", 20)
    _lev_hold = msg.get("leverage", 1)
    if _lev_hold >= 5:
        # 5x ETPs: ALWAYS intraday
        for sig in all_signals:
            sig["max_hold_hours"] = 8  # Force intraday exit
            sig["etp_hold_rule"] = "5x_intraday_only"
    elif _lev_hold >= 3:
        _sym_hold = ticker_symbols.get(ticker_id, "")
        _is_single_stock_3x = any(s in _sym_hold.upper() for s in ("NVD3", "3TSL", "AMD3", "TSLA", "NVDA"))
        if _is_single_stock_3x:
            # 3x single-stock ETPs: tighter hold limits
            if _vix_hold > 35:
                for sig in all_signals:
                    sig["max_hold_hours"] = 0  # NO ENTRY (caught by quality gate)
            elif _vix_hold > 25:
                for sig in all_signals:
                    sig["max_hold_hours"] = 8  # Intraday only
                    sig["etp_hold_rule"] = "3x_single_vix25_intraday"
            elif _vix_hold > 20:
                for sig in all_signals:
                    sig["max_hold_hours"] = 24  # 1 day max
                    sig["etp_hold_rule"] = "3x_single_vix20_1day"
            elif _vix_hold > 15:
                for sig in all_signals:
                    sig["max_hold_hours"] = 72  # 3 days
                    sig["etp_hold_rule"] = "3x_single_vix15_3day"
            else:
                for sig in all_signals:
                    sig["max_hold_hours"] = 120  # 5 days
                    sig["etp_hold_rule"] = "3x_single_vix_low"
        else:
            # 3x index ETPs: wider limits
            if _vix_hold > 35:
                for sig in all_signals:
                    sig["max_hold_hours"] = 8  # Intraday only
                    sig["etp_hold_rule"] = "3x_index_vix35_intraday"
            elif _vix_hold > 25:
                for sig in all_signals:
                    sig["max_hold_hours"] = 48  # 2 days
                    sig["etp_hold_rule"] = "3x_index_vix25_2day"
            elif _vix_hold > 20:
                for sig in all_signals:
                    sig["max_hold_hours"] = 120  # 5 days
                    sig["etp_hold_rule"] = "3x_index_vix20_5day"
            elif _vix_hold > 15:
                for sig in all_signals:
                    sig["max_hold_hours"] = 240  # 10 days
                    sig["etp_hold_rule"] = "3x_index_vix15_10day"
            else:
                for sig in all_signals:
                    sig["max_hold_hours"] = 480  # 20 days
                    sig["etp_hold_rule"] = "3x_index_vix_low"

    # ── BOOK 40: EARNINGS-TOMORROW HOLD CAP ──
    # If underlying has earnings tomorrow, cap hold to force exit before close today.
    # This prevents holding 3x single-stock ETPs through amplified earnings gaps.
    if msg.get("_earnings_tomorrow"):
        _earn_ul = msg.get("_earnings_underlying", "?")
        for sig in all_signals:
            _prev_hold = sig.get("max_hold_hours", 999)
            sig["max_hold_hours"] = min(_prev_hold, 4)  # Exit within 4 hours (well before close)
            sig["etp_hold_rule"] = f"earnings_T-1_{_earn_ul}"
            sig["suggested_max_hold_hours"] = min(sig.get("suggested_max_hold_hours", 999), 4)

    # ── BOOK 130: IV SURFACE SIZING MODIFIER ──
    # Adjust position size based on implied volatility regime.
    # Backwardation + high IV → reduce size. Contango + low IV → can size up.
    try:
        from python_brain.analytics.iv_surface import compute_iv_regime, iv_sizing_modifier
        _vix = msg.get("vix", 20)
        _vix_1m = msg.get("vix_1m", _vix)
        _vix_3m = msg.get("vix_3m", _vix)
        _iv_regime = compute_iv_regime(_vix, _vix_1m, _vix_3m)
        _iv_mult = iv_sizing_modifier(_iv_regime)
        if abs(_iv_mult - 1.0) > 0.01:
            for sig in all_signals:
                _orig_kelly = sig.get("kelly_fraction", 0.20)
                sig["kelly_fraction"] = round(_orig_kelly * _iv_mult, 4)
                _orig_shares = sig.get("shares", 0)
                if _orig_shares > 0:
                    sig["shares"] = max(1, int(_orig_shares * _iv_mult))
                sig["iv_sizing_mult"] = _iv_mult
                sig["iv_regime"] = _iv_regime.get("label", "unknown")
    except ImportError:
        pass
    except Exception:
        pass

    # ── TURN-OF-MONTH (TOM) SIZING OVERLAY (Book 171) ──
    # Unlike the confidence boost in _generate_signals, this applies to Kelly sizing.
    # TOM captures 75% of monthly returns in 25% of trading days.
    try:
        from datetime import datetime as _dt_toms, timezone as _tz_toms
        _ts_toms = msg.get("timestamp_ns", 0)
        if _ts_toms > 0 and _vix_hold <= 25:  # Disable TOM overlay when VIX > 25
            _d_toms = _dt_toms.fromtimestamp(_ts_toms / 1_000_000_000, tz=_tz_toms.utc)
            _dom_s = _d_toms.day
            import calendar as _cal_s
            _dim_s = _cal_s.monthrange(_d_toms.year, _d_toms.month)[1]
            _tom_mult = 1.0
            if _dom_s >= _dim_s:
                _tom_mult = 1.12  # T-1
            elif _dom_s == 1:
                _tom_mult = 1.16  # T+0
            elif _dom_s == 2:
                _tom_mult = 1.20  # T+1 (peak)
            elif _dom_s == 3:
                _tom_mult = 1.14  # T+2
            elif _dom_s == 4:
                _tom_mult = 1.08  # T+3
            if _tom_mult > 1.0:
                for sig in all_signals:
                    sig["kelly_fraction"] *= _tom_mult
                    sig["shares"] = max(1, int(sig["shares"] * _tom_mult))
                    sig["tom_sizing_mult"] = round(_tom_mult, 2)
            # Weaken in August (historically weak month)
            if _d_toms.month == 8:
                for sig in all_signals:
                    sig["kelly_fraction"] *= 0.90
                    sig["shares"] = max(1, int(sig["shares"] * 0.90))
                    sig["august_seasonal_reduction"] = True
            # Strengthen in January (January effect)
            elif _d_toms.month == 1:
                for sig in all_signals:
                    sig["kelly_fraction"] *= 1.05
                    sig["shares"] = max(1, int(sig["shares"] * 1.05))
                    sig["january_effect_boost"] = True
    except Exception:
        pass

    # ── OPENING / LAST-HOUR GRADUATED SIZING (Book 94) ──
    # First 25 min: max 50% of normal. Last hour: max 70%. Mid-session: 100%.
    _ts_oh = msg.get("timestamp_ns", 0)
    if _ts_oh > 0:
        from datetime import datetime as _dt_oh, timezone as _tz_oh, timedelta as _td_oh
        _utc_oh = _dt_oh.fromtimestamp(_ts_oh / 1_000_000_000, tz=_tz_oh.utc)
        _sym_oh = ticker_symbols.get(ticker_id, "")
        _exch_oh = _get_exchange_for_symbol(_sym_oh) if _sym_oh else ""
        _off_oh = _exchange_utc_offset(_exch_oh, _utc_oh)
        _local_oh = _utc_oh + _td_oh(hours=_off_oh)
        _lm_oh = _local_oh.hour * 60 + _local_oh.minute
        _OPEN_OH = {"LSE": 480, "XETRA": 540, "EURONEXT": 540, "US": 570,
                    "HKEX": 570, "TSE": 540, "SGX": 540}
        _CLOSE_OH = {"LSE": 990, "XETRA": 1050, "EURONEXT": 1050, "US": 960,
                     "HKEX": 960, "TSE": 900, "SGX": 1020}
        _open_oh = _OPEN_OH.get(_exch_oh, 540)
        _close_oh = _CLOSE_OH.get(_exch_oh, 960)
        _ms_open = _lm_oh - _open_oh
        _ms_close = _close_oh - _lm_oh
        if 0 < _ms_open <= 25:
            # First 25 min: max 50% sizing, max 2 new orders
            _oh_scale = 0.50
            for sig in all_signals:
                sig["kelly_fraction"] *= _oh_scale
                sig["shares"] = max(1, int(sig["shares"] * _oh_scale))
                sig["opening_sizing_reduction"] = True
        elif 0 < _ms_close <= 60:
            # Last hour: max 70% sizing
            _oh_scale = 0.70
            for sig in all_signals:
                sig["kelly_fraction"] *= _oh_scale
                sig["shares"] = max(1, int(sig["shares"] * _oh_scale))
                sig["last_hour_sizing_reduction"] = True

    # ── SPREAD-RATIO SIZING REDUCTION (from quality gate flag) ──
    if msg.get("_spread_ratio_reduce"):
        _sr_scale = msg["_spread_ratio_reduce"]
        for sig in all_signals:
            sig["kelly_fraction"] *= _sr_scale
            sig["shares"] = max(1, int(sig["shares"] * _sr_scale))
            sig["spread_ratio_sized_down"] = True

    # Simulated commission + slippage deduction (paper mode reality check)
    # Deducts estimated round-trip cost from Kelly sizing to prevent false positive edges.
    # IBKR tiered: £1.70 entry + £1.70 exit = £3.40 per round trip.
    # Slippage: 0.5% of position value (config.toml [risk] slippage_assumption_pct).
    for sig in all_signals:
        eq = msg.get("equity", 10000.0)
        notional = sig["kelly_fraction"] * eq
        sim_commission = 3.40  # £1.70 × 2 (IBKR tiered minimum)
        sim_slippage = notional * 0.005  # 0.5% slippage assumption
        total_cost = sim_commission + sim_slippage
        # Attach cost estimate to signal for Ouroboros forensics
        sig["sim_commission_gbp"] = round(sim_commission, 2)
        sig["sim_slippage_gbp"] = round(sim_slippage, 2)
        sig["sim_total_cost_gbp"] = round(total_cost, 2)
        # Reduce shares by cost fraction (so Kelly reflects post-cost reality)
        if notional > 0:
            cost_frac = total_cost / notional
            sig["kelly_fraction"] = max(0.001, sig["kelly_fraction"] * (1 - cost_frac))
            sig["shares"] = max(1, int(sig["shares"] * (1 - cost_frac)))

    # COMPOUNDING: Cost-aware edge filter — reject if cost > 50% of expected edge.
    # Expected edge ≈ kelly_fraction * equity * (WR - 0.5) * 2 (simplified edge proxy).
    # If the cost of the trade exceeds half the expected profit, it's not worth taking.
    wr = msg.get("win_rate", 0.5)
    edge_proxy = max(wr - 0.45, 0) * 2  # edge above 45% WR breakeven (after costs)
    all_signals = [
        sig for sig in all_signals
        if sig["sim_total_cost_gbp"] <= 0 or edge_proxy <= 0 or
        sig["sim_total_cost_gbp"] < (sig["kelly_fraction"] * msg.get("equity", 10000) * edge_proxy * 0.5)
    ]
    if not all_signals:
        return None

    # ── BOOK 124: Apply vol regime sizing multiplier to Kelly ──
    if _vol_regime_sizing < 1.0:
        for sig in all_signals:
            sig["kelly_fraction"] *= _vol_regime_sizing
            sig["shares"] = max(1, int(sig["shares"] * _vol_regime_sizing))

    # ── BOOK 85: VIX REGIME POSITION SIZING ──
    # VIX < 15: 1.0x (full). VIX 15-25: 0.8x. VIX 25-35: 0.5x. VIX > 35: 0.25x (inverse only)
    _vix_adj = msg.get("vix", 21.0)
    if _vix_adj > 35:
        _vix_scale = 0.25
        # In crisis, only allow inverse ETPs
        _sym = msg.get("symbol", "")
        _is_inverse = any(inv in _sym.upper() for inv in ("QQQS", "3USS", "SUK2", "NV3S", "TS3S"))
        if not _is_inverse:
            all_signals = []  # Block all long signals in crisis
    elif _vix_adj > 25:
        _vix_scale = 0.50
    elif _vix_adj > 20:
        _vix_scale = 0.80
    else:
        _vix_scale = 1.0
    if _vix_scale < 1.0 and all_signals:
        for sig in all_signals:
            sig["kelly_fraction"] *= _vix_scale
            sig["shares"] = max(1, int(sig["shares"] * _vix_scale))
            sig["vix_scale"] = round(_vix_scale, 2)

    # ── BOOK 85: REGIME RISK PER TRADE (CONSUME _regime_risk_per_trade) ──
    # Scale Kelly to regime-appropriate risk budget. STEADY=0.75%, CRISIS=0.20%.
    _rrpt = msg.get("_regime_risk_per_trade")
    if _rrpt and _rrpt < 0.0075:
        _rrpt_scale = _rrpt / 0.0075  # Normalize: STEADY=1.0, CRISIS=0.27
        for sig in all_signals:
            sig["kelly_fraction"] *= _rrpt_scale
            sig["shares"] = max(1, int(sig["shares"] * _rrpt_scale))
            sig["regime_risk_per_trade"] = _rrpt

    # ── BOOK 45: STALE TICK PENALTY (CONSUME _stale_tick_penalty) ──
    if msg.get("_stale_tick_penalty"):
        for sig in all_signals:
            sig["confidence"] = max(0, sig["confidence"] - 5)
            sig["kelly_fraction"] *= 0.85
            sig["shares"] = max(1, int(sig["shares"] * 0.85))
            sig["stale_tick_penalized"] = True

    # ── BOOK 47: STRATEGY QUARANTINE (SPRT edge-death test) ──
    try:
        from python_brain.risk.strategy_quarantine import get_quarantine
        _sq = get_quarantine()
        _sq_filtered = []
        for sig in all_signals:
            _strat_sq = sig.get("strategy", "")
            _sq_status = _sq.get_status(_strat_sq)
            if _sq_status == "killed":
                continue  # Drop signal entirely
            elif _sq_status == "quarantine":
                _sq_scale = _sq.get_allocation_scale(_strat_sq)
                sig["kelly_fraction"] *= _sq_scale
                sig["shares"] = max(1, int(sig["shares"] * _sq_scale))
                sig["quarantine_status"] = "quarantine"
                sig["quarantine_scale"] = round(_sq_scale, 2)
            _sq_filtered.append(sig)
        all_signals = _sq_filtered
        if not all_signals:
            return None
    except ImportError:
        pass

    # ── BOOK 49: LIQUIDITY SCORE SIZING MODIFIER ──
    try:
        from python_brain.risk.liquidity_scoring import get_liquidity_scale
        symbol = ticker_symbols.get(ticker_id, "")
        if symbol:
            _liq_scale = get_liquidity_scale(symbol)
            if _liq_scale < 1.0:
                for sig in all_signals:
                    sig["kelly_fraction"] *= _liq_scale
                    sig["shares"] = max(1, int(sig["shares"] * _liq_scale))
                    sig["liquidity_scale"] = _liq_scale
    except ImportError:
        pass

    # ── BOOK 49/90: ADV PARTICIPATION SIZING (from quality gate flag) ──
    if msg.get("_adv_scale") and msg["_adv_scale"] < 1.0:
        _adv_s = msg["_adv_scale"]
        for sig in all_signals:
            sig["kelly_fraction"] *= _adv_s
            sig["shares"] = max(1, int(sig["shares"] * _adv_s))
            sig["adv_participation_scaled"] = round(_adv_s, 2)

    # ── BOOK 144: CONFORMAL DIRECTIONAL HARD GATE ──
    # PROMOTED: If prediction interval straddles zero, there is NO statistically significant
    # direction. Hard-block all signals (was -10 conf, now full block).
    if msg.get("_conformal_no_trade"):
        return None  # Hard block: no directional clarity
    elif msg.get("_conformal_fraction"):
        _cf = msg["_conformal_fraction"]
        for sig in all_signals:
            sig["kelly_fraction"] *= _cf
            sig["shares"] = max(1, int(sig["shares"] * _cf))
            sig["conformal_directional_fraction"] = round(_cf, 2)

    # ── GEMINI MORNING BRIEF: FOCUS/AVOID TICKER OVERLAY ──
    try:
        _load_gemini_brief()
        symbol = ticker_symbols.get(ticker_id, "")
        if symbol and symbol in _gemini_focus_tickers:
            for sig in all_signals:
                sig["confidence"] = min(100, sig["confidence"] + 3)
                sig["gemini_focus"] = True
        elif symbol and symbol in _gemini_avoid_tickers:
            for sig in all_signals:
                sig["confidence"] = max(0, sig["confidence"] - 8)
                sig["kelly_fraction"] *= 0.5
                sig["shares"] = max(1, int(sig["shares"] * 0.5))
                sig["gemini_avoid"] = True
    except Exception:
        pass

    # ── CLAUDE DAILY PLAN: FOCUS TICKER OVERLAY ──
    try:
        _load_claude_daily_plan()
        symbol = ticker_symbols.get(ticker_id, "")
        if symbol and symbol in _claude_focus_tickers:
            for sig in all_signals:
                sig["confidence"] = min(100, sig["confidence"] + 3)
                sig["claude_focus"] = True
        elif symbol and symbol in _claude_avoid_tickers:
            for sig in all_signals:
                sig["confidence"] = max(0, sig["confidence"] - 5)
                sig["claude_avoid"] = True
    except Exception:
        pass

    # ── CAUSAL DAG LEADERSHIP BOOST (CONSUME _causal_leaders) ──
    # If this instrument is a causal leader (high out-degree in nightly causal DAG),
    # its signals have structural alpha support → boost confidence.
    try:
        _causal_leaders = getattr(_load_adaptive_params, '_causal_leaders', [])
        if _causal_leaders:
            symbol = ticker_symbols.get(ticker_id, "")
            if symbol in _causal_leaders:
                for sig in all_signals:
                    sig["confidence"] = min(100, sig["confidence"] + 3)
                    sig["causal_leader"] = True
    except Exception:
        pass

    # ── OU MEAN-REVERSION INSTRUMENTS BOOST (CONSUME _ou_instruments) ──
    # If this instrument has strong mean-reversion (short half-life from OU calibration),
    # boost mean-reversion strategies, penalize momentum strategies.
    try:
        _ou_instruments = getattr(_load_adaptive_params, '_ou_instruments', {})
        if _ou_instruments:
            symbol = ticker_symbols.get(ticker_id, "")
            _ou_params = _ou_instruments.get(symbol)
            if _ou_params:
                _half_life = _ou_params.get("half_life_bars", 999)
                _MR_STRATS = {"IBS_MeanReversion", "S2_Reversion", "PairsReversion", "CointPairs"}
                _MOM_STRATS = {"Momentum", "VolExpansion", "S1_Microstructure", "S3_MacroTrend"}
                for sig in all_signals:
                    strat = sig.get("strategy", "")
                    if strat in _MR_STRATS and _half_life < 30:
                        sig["confidence"] = min(100, sig["confidence"] + 5)
                        sig["ou_mr_boost"] = round(_half_life, 1)
                    elif strat in _MOM_STRATS and _half_life < 20:
                        sig["confidence"] = max(0, sig["confidence"] - 5)
                        sig["ou_mr_momentum_penalty"] = round(_half_life, 1)
    except Exception:
        pass

    # ── VOLUME CONFIRMATION AT ENTRY ──
    # Momentum signals without volume backing are noise. Require RVOL > 0.7 for momentum
    # strategies, and boost signals with RVOL > 2.0 (institutional interest).
    _rvol_entry = ind.get("rvol", 1.0)
    _vol_slope_entry = ind.get("vol_slope", 0.0)
    _MOMENTUM_STRATEGIES = {"Momentum", "VolExpansion", "S1_Microstructure", "S3_MacroTrend",
                            "HighFlyer", "ORB", "GapMomentum"}
    _MR_STRATEGIES = {"IBS_MeanReversion", "S2_Reversion", "PairsReversion"}
    for sig in all_signals:
        strat = sig.get("strategy", "")
        if strat in _MOMENTUM_STRATEGIES:
            if _rvol_entry < 0.5:
                # Very low volume — momentum signal without conviction. Hard penalize.
                sig["confidence"] = max(0, sig["confidence"] - 10)
                sig["volume_warning"] = "very_low_rvol"
            elif _rvol_entry < 0.8:
                sig["confidence"] = max(0, sig["confidence"] - 4)
                sig["volume_warning"] = "low_rvol"
            elif _rvol_entry > 2.5:
                sig["confidence"] = min(100, sig["confidence"] + 3)
                sig["volume_note"] = "strong_institutional_rvol"
        elif strat in _MR_STRATEGIES:
            # Mean-reversion actually works BETTER in low volume (less institutional flow)
            # but high volume + oversold = capitulation setup (very high edge)
            if _rvol_entry > 3.0 and sig.get("oversold_opportunity"):
                sig["confidence"] = min(100, sig["confidence"] + 5)
                sig["volume_note"] = "capitulation_volume_MR_boost"
        # Volume acceleration check: rising volume = conviction building
        if _vol_slope_entry > 0.5:
            sig["confidence"] = min(100, sig["confidence"] + 2)
            sig["volume_accelerating"] = True
        elif _vol_slope_entry < -0.5 and strat in _MOMENTUM_STRATEGIES:
            sig["confidence"] = max(0, sig["confidence"] - 3)
            sig["volume_decelerating"] = True

    # ── ORDER FLOW IMBALANCE ──
    # If quote_imbalance strongly favors one side, it confirms directional signals.
    _qi = ind.get("quote_imbalance", 0.0)  # -1 (all sell) to +1 (all buy)
    if abs(_qi) > 0.3:
        for sig in all_signals:
            if sig.get("direction") == "Long" and _qi > 0.3:
                sig["confidence"] = min(100, sig["confidence"] + 3)
                sig["order_flow_aligned"] = True
            elif sig.get("direction") == "Long" and _qi < -0.3:
                sig["confidence"] = max(0, sig["confidence"] - 5)
                sig["order_flow_opposed"] = True

    # NEWS/SENTIMENT ENRICHMENT — same-time-as-hedge-funds data
    # Enrich each signal with news sentiment, dark pool flow, options flow, Congress trades.
    # Confidence modified: +8 max for aligned sentiment, -15 max for opposing.
    try:
        from python_brain.feeds.data_manager import get_data_manager
        _dm = get_data_manager()
        ticker_symbol = msg.get("symbol", "")
        for sig in all_signals:
            _dm.enrich_signal(sig, ticker_symbol)
    except Exception:
        pass  # Non-fatal: if feeds are down, signals proceed without enrichment

    # FEATURE FLAGS GATE (Book 71) — disable modules via feature flags
    try:
        from python_brain.risk.feature_flags import FeatureFlagManager
        if not hasattr(_apply_adjustments, "_flags"):
            _apply_adjustments._flags = FeatureFlagManager()
        _flags = _apply_adjustments._flags
    except Exception:
        _flags = None

    # ── BOOK 85: REGIME-SCALED RISK LIMITS ──
    # Daily/weekly loss limits, per-trade risk budget, and cooldown enforcement.
    try:
        from python_brain.risk.regime_risk_limits import get_regime_limits
        rl = get_regime_limits(vix=msg.get("vix", 21), hurst=ind.get("hurst", 0.5))

        # 1. Apply regime-scaled confidence floor
        all_signals = [s for s in all_signals if s["confidence"] >= rl.confidence_floor]
        if not all_signals:
            return None

        # 2. Store regime risk-per-trade for Rust engine (CHECK 37)
        msg["_regime_risk_per_trade"] = rl.risk_per_trade_pct / 100.0  # Convert to decimal
        msg["_regime_daily_loss_limit"] = rl.daily_loss_limit_pct / 100.0
        msg["_regime_weekly_loss_limit"] = rl.weekly_loss_limit_pct / 100.0
        msg["_regime_name"] = rl.regime

        # 3. Enforce regime-scaled cooldown between trades
        # Cooldown is checked per-ticker: store regime cooldown in msg for signal gating
        if not hasattr(_apply_adjustments, "_last_signal_time"):
            _apply_adjustments._last_signal_time = {}
        last_sig_ns = _apply_adjustments._last_signal_time.get(ticker_id, 0)
        now_ns = msg.get("timestamp_ns", int(time.time() * 1e9))
        elapsed_ns = now_ns - last_sig_ns
        cooldown_ns = rl.cooldown_secs * 1_000_000_000

        if last_sig_ns > 0 and elapsed_ns < cooldown_ns:
            # Still in cooldown — suppress signals for this ticker
            seconds_remaining = (cooldown_ns - elapsed_ns) / 1e9
            log.info(f"Regime cooldown active for {symbol}: {seconds_remaining:.1f}s remaining "
                     f"({rl.regime}={rl.cooldown_secs}s)")
            return None  # Hard block: cooldown not elapsed

        # 4. Attach regime info to signals for Rust processing + diagnostics
        for sig in all_signals:
            sig["regime"] = rl.regime
            sig["regime_risk_per_trade_pct"] = rl.risk_per_trade_pct
            sig["regime_cooldown_secs"] = rl.cooldown_secs

    except Exception as e:
        log.warning(f"Book 85 regime limits failed: {e}")
        pass

    # Update last signal time if signals still pending (before any further filtering)
    if all_signals and hasattr(_apply_adjustments, "_last_signal_time"):
        try:
            now_ns = msg.get("timestamp_ns", int(time.time() * 1e9))
            _apply_adjustments._last_signal_time[ticker_id] = now_ns
        except Exception:
            pass

    # SIGNAL ROUTER — session-aware filtering + conflict resolution (Book 216)
    try:
        from python_brain.regime.signal_router import SignalRouter
        if not hasattr(_apply_adjustments, "_router"):
            _apply_adjustments._router = SignalRouter()
        router = _apply_adjustments._router
        all_signals = router.filter_by_session(all_signals, msg.get("london_time_secs", 0))
        if not all_signals:
            return None
    except Exception:
        pass

    # CAPACITY MONITOR — reject oversized orders (Books 49, 181)
    try:
        from python_brain.execution.capacity_monitor import CapacityMonitor
        if not hasattr(_apply_adjustments, "_cap_mon"):
            _apply_adjustments._cap_mon = CapacityMonitor()
        cap_mon = _apply_adjustments._cap_mon
        for sig in all_signals:
            notional = sig.get("kelly_fraction", 0) * msg.get("equity", 10000)
            ticker = sig.get("ticker", msg.get("symbol", ""))
            cap = cap_mon.check(ticker, notional)
            if not cap.within_capacity:
                sig["kelly_fraction"] *= cap.max_order_gbp / max(notional, 1)
                sig["shares"] = max(1, int(sig["shares"] * cap.max_order_gbp / max(notional, 1)))
    except Exception:
        pass

    # STRATEGY-REGIME MATRIX (Book 15, 113, 124)
    # Disable or scale strategies based on current market regime.
    try:
        from python_brain.regime.strategy_regime_matrix import (
            RegimeState, apply_regime_adjustments,
        )
        vix_val = msg.get("vix", 21.0)
        hurst_val = msg.get("hurst", 0.50)
        hmm_st = msg.get("hmm_state", 1)
        regime_state = RegimeState.from_indicators(
            vix=vix_val, hurst=hurst_val, hmm_state=hmm_st,
        )
        regime_filtered = []
        for sig in all_signals:
            strat = sig.get("strategy", "")
            adj_conf, adj_kelly = apply_regime_adjustments(
                strat, sig["confidence"], sig["kelly_fraction"], regime_state,
            )
            if adj_conf > 0:
                sig["confidence"] = adj_conf
                sig["kelly_fraction"] = adj_kelly
                regime_filtered.append(sig)
        all_signals = regime_filtered
        if not all_signals:
            return None
    except Exception:
        pass  # Non-fatal: if regime matrix fails, proceed without it

    # OVERNIGHT GAP RISK FILTER (Book 40, 148, 186)
    # Adjust confidence and Kelly for late-session entries approaching overnight.
    # Blocks entries that would create overnight exposure exceeding tier/regime limits.
    try:
        from python_brain.overnight.risk import check_overnight_risk
        vix_val = msg.get("vix", 21.0)
        london_secs = msg.get("london_time_secs", 0)
        is_fri = msg.get("day_of_week", 0) == 4  # 0=Mon, 4=Fri
        filtered_signals = []
        for sig in all_signals:
            ticker = sig.get("ticker", "")
            lev = sig.get("leverage", 3)
            adj_conf, adj_kelly = check_overnight_risk(
                ticker=ticker,
                confidence=sig["confidence"],
                kelly_fraction=sig["kelly_fraction"],
                vix=vix_val,
                london_time_secs=london_secs,
                leverage=lev,
                is_friday=is_fri,
            )
            if adj_conf > 0:
                sig["confidence"] = adj_conf
                sig["kelly_fraction"] = adj_kelly
                filtered_signals.append(sig)
        all_signals = filtered_signals
        if not all_signals:
            return None
    except Exception:
        pass  # Non-fatal: if overnight risk module fails, proceed without it

    # DRAWDOWN RECOVERY SIZING (Book 42)
    # Scale Kelly based on current drawdown phase. Deeper drawdown = smaller size.
    try:
        from python_brain.risk.drawdown_recovery import DrawdownMonitor
        dd_monitor = DrawdownMonitor(initial_equity=msg.get("initial_equity", 10000.0))
        dd_monitor.update(msg.get("equity", 10000.0))
        dd_scale = dd_monitor.kelly_scale()
        dd_min_conf = dd_monitor.min_confidence()
        if dd_scale < 1.0:
            for sig in all_signals:
                sig["kelly_fraction"] *= dd_scale
                sig["shares"] = max(1, int(sig["shares"] * dd_scale))
        # Filter by drawdown-adjusted confidence floor
        all_signals = [s for s in all_signals if s["confidence"] >= dd_min_conf]
        if not all_signals:
            return None
    except Exception:
        pass

    # CORRELATION POSITION SIZING (Book 41)
    # Reduce size when portfolio correlation is elevated.
    try:
        from python_brain.risk.correlation import CorrelationTracker
        corr_tracker = getattr(msg, "_corr_tracker", None)
        if corr_tracker is not None:
            corr_mult = corr_tracker.position_size_multiplier()
            if corr_mult < 1.0:
                for sig in all_signals:
                    sig["kelly_fraction"] *= corr_mult
                    sig["shares"] = max(1, int(sig["shares"] * corr_mult))
            if corr_tracker.should_block_long_entry():
                all_signals = [s for s in all_signals
                               if s.get("ticker", "").endswith("S.L")]  # Only inverse
                if not all_signals:
                    return None
    except Exception:
        pass

    # VOL-TARGETING (Book 80)
    # Scale Kelly inversely to realized volatility for constant dollar risk.
    try:
        from python_brain.sizing.vol_targeting import vol_adjusted_kelly, student_t_correction
        for sig in all_signals:
            rv = msg.get("realized_vol", 0.02)
            lev = sig.get("leverage", 3)
            # Vol-target: shrink in high vol, expand in low vol
            sig["kelly_fraction"] = vol_adjusted_kelly(
                sig["kelly_fraction"], rv, target_vol=0.02,
            )
            # Student-t correction for fat-tailed ETP returns
            sig["kelly_fraction"] = student_t_correction(
                sig["kelly_fraction"], nu=5.0, leverage=lev,
            )
            sig["shares"] = max(1, int(sig["kelly_fraction"] * msg.get("equity", 10000) / max(sig.get("price", 1), 0.01)))
    except Exception:
        pass

    # ── BOOK 10: ROLLING KELLY + DRAWDOWN STAGING ──
    # Dynamic Kelly based on recent performance + 4-stage drawdown response.
    try:
        from python_brain.sizing.rolling_kelly import get_drawdown_stager
        dd_stager = get_drawdown_stager()
        dd_stager.update(msg.get("equity", 10000.0))
        dd_kelly_scale = dd_stager.kelly_scale()
        dd_conf_add = dd_stager.confidence_floor_add()
        if dd_stager.should_block_new_entries():
            return None  # FLATTEN stage: exit-only mode
        if dd_kelly_scale < 1.0:
            for sig in all_signals:
                sig["kelly_fraction"] *= dd_kelly_scale
                sig["shares"] = max(1, int(sig["shares"] * dd_kelly_scale))
        if dd_conf_add > 0:
            all_signals = [s for s in all_signals if s["confidence"] >= (50 + dd_conf_add)]
            if not all_signals:
                return None
    except ImportError:
        pass

    # ── BOOK 12: REALISTIC SLIPPAGE MODEL ──
    # Replace flat 0.5% slippage with dynamic model (RVOL, ToD, order-size).
    try:
        from python_brain.execution.slippage_model import total_round_trip_cost
        from datetime import datetime as _dt_slip, timezone as _tz_slip
        ts_ns_slip = msg.get("timestamp_ns", 0)
        utc_hour_slip = 12
        if ts_ns_slip > 0:
            utc_hour_slip = _dt_slip.fromtimestamp(ts_ns_slip / 1e9, tz=_tz_slip.utc).hour
        for sig in all_signals:
            eq = msg.get("equity", 10000.0)
            notional = sig["kelly_fraction"] * eq
            sym = sig.get("ticker", msg.get("symbol", ""))
            exch = msg.get("exchange", "LSE")
            ccy = msg.get("currency", "GBP")
            cost_est = total_round_trip_cost(
                notional_gbp=notional, symbol=sym, exchange=exch,
                currency=ccy, rvol=ind.get("rvol", 1.0),
                utc_hour=utc_hour_slip,
            )
            sig["sim_total_cost_gbp"] = round(cost_est.total_gbp, 2)
            sig["sim_breakeven_pct"] = round(cost_est.breakeven_move_pct, 3)
    except ImportError:
        pass

    # ── BOOK 7: TIME-OF-DAY CONFIDENCE SCALING ──
    tod_scale = msg.get("_tod_scale", 1.0)
    if tod_scale < 1.0:
        for sig in all_signals:
            sig["confidence"] = max(0, min(100, int(sig["confidence"] * tod_scale)))

    # ── BOOK 7: CORRELATION SPIKE REDUCE ──
    if msg.get("_corr_spike_reduce"):
        for sig in all_signals:
            sig["kelly_fraction"] *= 0.5
            sig["shares"] = max(1, int(sig["shares"] * 0.5))
            sig["corr_spike_reduce"] = True

    # ── BOOK 144: CONFORMAL PREDICTION — Calibrate confidence to empirical reality ──
    # Raw confidence ≠ actual win probability. Adjust using historical outcomes.
    # Only applies when we have 20+ calibration samples.
    try:
        from python_brain.analytics.conformal_calibrator import get_calibrators
        _cals = get_calibrators()
        if _cals._global._total_recorded >= 20:
            for sig in all_signals:
                strat = sig.get("strategy", "")
                raw = sig["confidence"]
                cal_result = _cals.calibrate(strat, raw)
                if cal_result.n_samples >= 5:
                    sig["raw_confidence"] = raw
                    sig["confidence"] = cal_result.calibrated_confidence
                    sig["calibration_bucket"] = cal_result.bucket
    except ImportError:
        pass

    # ── BOOK 105/118: KELLY FROM CALIBRATED PROBABILITY ──
    # Raw model confidence != true win probability. Use calibrated conf for Kelly
    # to prevent catastrophic oversizing from miscalibrated ML models.
    for sig in all_signals:
        _raw_conf = sig.get("raw_confidence", sig["confidence"])
        _cal_conf = sig.get("confidence", _raw_conf)
        if _raw_conf > 0 and _cal_conf < _raw_conf:
            _kelly_cal_adj = max(0.10, _cal_conf / max(_raw_conf, 1))
            sig["kelly_fraction"] *= _kelly_cal_adj
            sig["shares"] = max(1, int(sig["shares"] * _kelly_cal_adj))
            sig["kelly_calibration_adj"] = round(_kelly_cal_adj, 3)

    # ── BOOK 101: EXECUTION COST VS SIGNAL ALPHA GATE ──
    # Reject if estimated execution cost exceeds estimated signal alpha
    _cost_alpha_filtered = []
    for sig in all_signals:
        _alpha_est = sig.get("kelly_fraction", 0) * max(sig.get("confidence", 50) - 45, 0) / 100
        _cost_gbp = sig.get("sim_total_cost_gbp", 0)
        _eq = msg.get("equity", 10000)
        _alpha_gbp = _alpha_est * _eq
        if _cost_gbp > 0 and _alpha_gbp > 0 and _cost_gbp > _alpha_gbp:
            sig["cost_exceeds_alpha"] = True
            continue  # Drop this signal
        _cost_alpha_filtered.append(sig)
    all_signals = _cost_alpha_filtered
    if not all_signals:
        return None

    # ── BOOK 78: IV SIGNALS — Adjust confidence based on implied volatility regime ──
    try:
        from python_brain.ml.iv_signals import IVSignalGenerator
        if not hasattr(_apply_adjustments, "_iv_gen"):
            _apply_adjustments._iv_gen = IVSignalGenerator()
        _iv_gen = _apply_adjustments._iv_gen
        _iv_data = msg.get("iv_data")
        if _iv_data:
            _iv_signals = _iv_gen.generate_signals_from_raw(
                symbol=msg.get("symbol", ""),
                vix_spot=_iv_data.get("vix_spot", 0),
                vix_front=_iv_data.get("vix_front", 0),
                vix_second=_iv_data.get("vix_second", 0),
                realized_vol_20d=ind.get("yz_vol", 0),
            )
            if _iv_signals.get("overall_signal"):
                for sig in all_signals:
                    sig["confidence"] = _iv_gen.confidence_adjustment(
                        sig["confidence"], _iv_signals
                    )
                    sig["iv_regime"] = _iv_signals.get("term_structure", {}).get("classification", "unknown")
    except ImportError:
        pass

    # ── BOOK 76: ONLINE LEARNING — Feed trade outcome to drift detector ──
    try:
        from python_brain.ml.online_learning import OnlineLearningEngine, DriftType
        # Online learning runs post-trade (on_trade_complete), not per-tick.
        # Here we just check if drift was recently detected → reduce confidence.
        _ol_state_path = Path("/app/data/online_learning")
        if _ol_state_path.exists():
            import json as _ol_json
            for _ol_f in _ol_state_path.glob("*.json"):
                try:
                    _ol_state = _ol_json.loads(_ol_f.read_text())
                    if _ol_state.get("last_drift", "none") != "none":
                        for sig in all_signals:
                            if sig.get("strategy", "").lower() in _ol_f.stem.lower():
                                sig["confidence"] = max(0, sig["confidence"] - 5)
                                sig["drift_detected"] = _ol_state["last_drift"]
                except Exception:
                    pass
    except ImportError:
        pass

    # ── WARM PATH: PER-BAR ML MODELS ──
    # These run on completed bars (not every tick). Cache results and apply as adjustments.
    # Only compute when a new 5-min bar has completed (detected by bar count change).
    _bar_count = len(ind.get("bars_5m", []))
    _prev_bar_count = getattr(_apply_adjustments, "_prev_bar_count", {}).get(ticker_id, 0)
    _new_bar = _bar_count > _prev_bar_count
    if not hasattr(_apply_adjustments, "_prev_bar_count"):
        _apply_adjustments._prev_bar_count = {}
    _apply_adjustments._prev_bar_count[ticker_id] = _bar_count

    if _new_bar and ind.get("bars_5m") and len(ind["bars_5m"]) >= 20:
        import numpy as _np_warm
        _closes_warm = _np_warm.array([b["close"] for b in ind["bars_5m"][-60:]])
        _vols_warm = _np_warm.array([b["volume"] for b in ind["bars_5m"][-60:]])

        # Book 75: TFT quantile prediction → confidence width adjustment
        try:
            from python_brain.ml.temporal_fusion_transformer import TFTPreprocessor
            if not hasattr(_apply_adjustments, "_tft_pre"):
                _apply_adjustments._tft_pre = TFTPreprocessor()
            _tft_pre = _apply_adjustments._tft_pre
            # TFT gives prediction intervals — narrower = more confident
            if not hasattr(_apply_adjustments, "_tft_cache"):
                _apply_adjustments._tft_cache = {}
            _apply_adjustments._tft_cache[ticker_id] = {"available": True}
        except ImportError:
            pass

        # Book 161: Mamba S4 sequence prediction → directional bias
        try:
            from python_brain.ml.mamba_model import MambaModel, S4Config
            if not hasattr(_apply_adjustments, "_mamba_cache"):
                _apply_adjustments._mamba_cache = {}
            _mamba = MambaModel(S4Config(d_model=32, d_state=8, seq_len=min(60, len(_closes_warm))))
            _mamba_pred = _mamba.predict(_np_warm.column_stack([_closes_warm[-30:], _vols_warm[-30:]]))
            if _mamba_pred and _mamba_pred.get("direction") == "bullish":
                for sig in all_signals:
                    sig["confidence"] = min(100, sig["confidence"] + 3)
                    sig["mamba_boost"] = True
            elif _mamba_pred and _mamba_pred.get("direction") == "bearish":
                for sig in all_signals:
                    sig["confidence"] = max(0, sig["confidence"] - 3)
                    sig["mamba_dampen"] = True
        except ImportError:
            pass
        except Exception:
            pass

        # Book 114: GP uncertainty → Kelly adjustment
        try:
            from python_brain.ml.kernel_methods import GaussianProcess, RBFKernel
            if len(_closes_warm) >= 20:
                _X_gp = _np_warm.arange(len(_closes_warm)).reshape(-1, 1)
                _gp = GaussianProcess(kernel=RBFKernel(sigma=1.0))
                _gp.fit(_X_gp[:-1], _np_warm.diff(_closes_warm))
                _mu, _var = _gp.predict(_X_gp[-1:])
                _uncertainty = float(_np_warm.sqrt(_var[0])) if _var[0] > 0 else 1.0
                # Higher uncertainty → reduce Kelly
                if _uncertainty > 0.02:
                    _unc_scale = max(0.5, 1.0 - _uncertainty * 10)
                    for sig in all_signals:
                        sig["kelly_fraction"] *= _unc_scale
                        sig["gp_uncertainty"] = round(_uncertainty, 4)
        except ImportError:
            pass
        except Exception:
            pass

        # Book 115: Wavelet denoised features → trend confirmation + COUNTER-TREND PENALTY
        try:
            from python_brain.features.wavelet_processor import WaveletFeaturePipeline
            if not hasattr(_apply_adjustments, "_wfp"):
                _apply_adjustments._wfp = WaveletFeaturePipeline()
            _wfp = _apply_adjustments._wfp
            _wf = _wfp.process(_closes_warm, _vols_warm)
            _wavelet_trend = _wf.get("denoised_trend", "neutral")
            if _wavelet_trend == "up":
                for sig in all_signals:
                    if sig.get("direction") == "Long":
                        sig["confidence"] = min(100, sig["confidence"] + 3)
                        sig["wavelet_trend"] = "confirmed"
                    else:
                        sig["confidence"] = max(0, sig["confidence"] - 5)
                        sig["wavelet_trend"] = "counter_long_uptrend"
            elif _wavelet_trend == "down":
                for sig in all_signals:
                    if sig.get("direction") == "Long":
                        # Long entry against denoised downtrend — penalize heavily
                        sig["confidence"] = max(0, sig["confidence"] - 8)
                        sig["wavelet_trend"] = "counter_short_downtrend"
                        sig["kelly_fraction"] *= 0.7
                        sig["shares"] = max(1, int(sig["shares"] * 0.7))
        except ImportError:
            pass
        except Exception:
            pass

        # Book 129: Reservoir computing regime change → BLOCK at extreme, SIZE DOWN at moderate
        try:
            from python_brain.ml.reservoir_computing import ReservoirFeatureExtractor
            if not hasattr(_apply_adjustments, "_rfe"):
                _apply_adjustments._rfe = ReservoirFeatureExtractor()
            _rfe = _apply_adjustments._rfe
            _rc_score = _rfe.regime_change_score(_closes_warm)
            if _rc_score > 0.85:
                # Extreme regime shift — block all entries until regime stabilizes
                return None
            elif _rc_score > 0.7:
                for sig in all_signals:
                    sig["confidence"] = max(0, sig["confidence"] - 8)
                    sig["kelly_fraction"] *= 0.6
                    sig["shares"] = max(1, int(sig["shares"] * 0.6))
                    sig["reservoir_regime_shift"] = round(_rc_score, 3)
            elif _rc_score > 0.5:
                for sig in all_signals:
                    sig["confidence"] = max(0, sig["confidence"] - 3)
                    sig["reservoir_regime_shift"] = round(_rc_score, 3)
        except ImportError:
            pass
        except Exception:
            pass

        # Book 96: GNN cross-instrument signal → USE graph density for confidence adjustment
        try:
            from python_brain.ml.gnn_market_structure import GNNSignalGenerator
            if not hasattr(_apply_adjustments, "_gnn"):
                _apply_adjustments._gnn = GNNSignalGenerator()
            _gnn = _apply_adjustments._gnn
            _gnn_result = _gnn.generate_signals(
                ticker_id=ticker_id,
                prices=_closes_warm,
                volumes=_vols_warm,
            )
            if _gnn_result:
                _graph_density = _gnn_result.get("graph_density", 0.5)
                _sector_momentum = _gnn_result.get("sector_momentum", 0)
                # High graph density + aligned sector momentum = strong structural support
                if _graph_density > 0.7 and _sector_momentum > 0:
                    for sig in all_signals:
                        if sig.get("direction") == "Long":
                            sig["confidence"] = min(100, sig["confidence"] + 3)
                            sig["gnn_structural_support"] = True
                elif _graph_density > 0.7 and _sector_momentum < -0.3:
                    # Sector divergence — this ticker is fighting its sector
                    for sig in all_signals:
                        if sig.get("direction") == "Long":
                            sig["confidence"] = max(0, sig["confidence"] - 5)
                            sig["gnn_sector_divergence"] = True
        except ImportError:
            pass

    # ── BOOK 123: MARKET IMPACT ESTIMATION ──
    # Attach pre-trade impact estimate to best signal for Rust execution layer
    try:
        from python_brain.execution.market_impact import PreTradeImpactEstimator
        for sig in all_signals:
            if not hasattr(_apply_adjustments, "_pie"):
                _apply_adjustments._pie = PreTradeImpactEstimator()
            _pie = _apply_adjustments._pie
            _notional = sig.get("kelly_fraction", 0) * msg.get("equity", 10000)
            _impact = _pie.estimate(
                order_size_gbp=_notional,
                symbol=msg.get("symbol", ""),
                adv=msg.get("adv_gbp", 100000),
                sigma=msg.get("realized_vol", 0.02),
            )
            sig["impact_bps"] = _impact.get("total_bps", 0)
            if _pie.should_split(_notional, msg.get("adv_gbp", 100000)):
                sig["execution_algo"] = "TWAP"
            # If impact > 50 bps, reduce Kelly to account for cost
            if _impact.get("total_bps", 0) > 50:
                sig["kelly_fraction"] *= 0.7
                sig["shares"] = max(1, int(sig["shares"] * 0.7))
                sig["high_impact_warning"] = True
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 187: TRUE LEVERAGE CHECK ──
    # Compute effective leverage across all 5 layers — block if too high
    try:
        from python_brain.execution.true_leverage import TrueLeverageCalculator
        _positions = msg.get("open_positions", [])
        _positions = _positions if isinstance(_positions, list) else []
        if _positions:
            _tlc = TrueLeverageCalculator(_positions)
            _true_lev = _tlc.total_effective_leverage()
            if not _tlc.is_safe(max_leverage=5.0):
                # Reduce all signals by leverage overshoot
                _lev_scale = max(0.3, 5.0 / max(_true_lev, 0.01))
                for sig in all_signals:
                    sig["kelly_fraction"] *= _lev_scale
                    sig["shares"] = max(1, int(sig["shares"] * _lev_scale))
                    sig["true_leverage_warning"] = round(_true_lev, 2)
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 170: GAME THEORY CROWDING DETECTION ──
    try:
        from python_brain.ml.game_theory_execution import GameTheoreticSignal
        if not hasattr(_apply_adjustments, "_gts"):
            _apply_adjustments._gts = GameTheoreticSignal()
        _gts = _apply_adjustments._gts
        _crowding = _gts.assess_crowding(
            volume_profile={"volume": msg.get("volume", 0)},
            order_imbalance=ind.get("quote_imbalance", 0),
        )
        if _crowding and _crowding.get("crowding_score", 0) > 0.6:
            _crowd_penalty = int(_crowding["crowding_score"] * 20)
            for sig in all_signals:
                sig["confidence"] = max(0, sig["confidence"] - _crowd_penalty)
                sig["crowding_score"] = round(_crowding["crowding_score"], 2)
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 144: CONFORMAL INTERVAL WIDTH → CONFIDENCE ──
    try:
        from python_brain.ml.conformal_signals import OnlineConformalTracker
        if not hasattr(_apply_adjustments, "_conf_tracker"):
            _apply_adjustments._conf_tracker = OnlineConformalTracker()
        _cw = _apply_adjustments._conf_tracker.get_current_width()
        if _cw < 0.3:  # Narrow interval = high confidence environment
            for sig in all_signals:
                sig["confidence"] = min(100, sig["confidence"] + 3)
                sig["conformal_width"] = round(_cw, 3)
        elif _cw > 0.7:  # Wide interval = uncertain environment
            for sig in all_signals:
                sig["confidence"] = max(0, sig["confidence"] - 5)
                sig["conformal_width"] = round(_cw, 3)
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 172: MODEL DISAGREEMENT PENALTY ──
    if len(all_signals) >= 3:
        try:
            from python_brain.risk.model_disagreement import compute_disagreement
            _confs = [s["confidence"] for s in all_signals]
            _disagree = compute_disagreement(_confs)
            if _disagree > 0.4:  # High disagreement among generators
                _dp = min(12, int(_disagree * 25))
                for sig in all_signals:
                    sig["confidence"] = max(0, sig["confidence"] - _dp)
                    sig["model_disagreement"] = round(_disagree, 2)
        except ImportError:
            pass
        except Exception:
            pass

    # ── BOOK 46: ETP DECAY MONITOR ──
    try:
        from python_brain.forensics.etp_decay_monitor import estimate_daily_decay
        symbol = ticker_symbols.get(ticker_id, "")
        _lev = msg.get("leverage", 1)
        if _lev >= 2:
            _decay = estimate_daily_decay(
                leverage=_lev,
                daily_vol=msg.get("realized_vol", 0.02),
            )
            if _decay > 0.001:  # >10bps daily decay
                for sig in all_signals:
                    sig["kelly_fraction"] *= max(0.5, 1.0 - _decay * 50)
                    sig["etp_decay_daily"] = round(_decay, 5)
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 213: CONSTRAINED PPO RISK-SHAPED TIMING ──
    try:
        from python_brain.ml.constrained_ppo import ConstrainedPPOAgent, PPOConfig
        if not hasattr(_apply_adjustments, "_ppo"):
            _apply_adjustments._ppo = ConstrainedPPOAgent(
                state_dim=8, action_dim=3, config=PPOConfig(),
            )
        import numpy as _np_ppo
        _ppo_state = _np_ppo.array([
            ind.get("rsi", 50) / 100, ind.get("adx", 15) / 50,
            ind.get("rvol", 1.0), ind.get("hurst", 0.5),
            ind.get("vpin", 0.5), ind.get("spread_pct", 0.1),
            msg.get("drawdown_pct", 0) / 8.0,  # Normalized to sacred limit
            msg.get("equity", 10000) / 10000,
        ])
        _ppo_action, _ppo_logp = _apply_adjustments._ppo.select_action(_ppo_state)
        if _ppo_action == 0:  # SKIP
            for sig in all_signals:
                sig["confidence"] = max(0, sig["confidence"] - 10)
                sig["ppo_action"] = "skip"
        elif _ppo_action == 2:  # SCALE DOWN
            for sig in all_signals:
                sig["kelly_fraction"] *= 0.6
                sig["ppo_action"] = "scale_down"
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 131: META-ALLOCATOR STRATEGY WEIGHTING ──
    try:
        from python_brain.sizing.meta_allocator import MetaAllocator
        if not hasattr(_apply_adjustments, "_meta_alloc"):
            _apply_adjustments._meta_alloc = MetaAllocator(total_equity=msg.get("equity", 10000))
        for sig in all_signals:
            strat = sig.get("strategy", "")
            if strat:
                _alloc_weight = _apply_adjustments._meta_alloc.get_strategy_weight(strat)
                if _alloc_weight is not None and _alloc_weight < 1.0:
                    sig["kelly_fraction"] *= _alloc_weight
                    sig["meta_alloc_weight"] = round(_alloc_weight, 3)
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 36: INEFFICIENCY SCORER ──
    try:
        from python_brain.analytics.inefficiency_scorer import score_inefficiency
        import numpy as _np_ineff
        if bars_5m and len(bars_5m) >= 20:
            _ineff = score_inefficiency(
                prices=_np_ineff.array([b["close"] for b in bars_5m[-30:]]),
                volumes=_np_ineff.array([b["volume"] for b in bars_5m[-30:]]),
            )
            if _ineff and _ineff.get("total_score", 0) > 70:
                for sig in all_signals:
                    sig["confidence"] = min(100, sig["confidence"] + 5)
                    sig["inefficiency_score"] = _ineff["total_score"]
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 116: JUMP PROBABILITY ESTIMATION ──
    try:
        from python_brain.models.stochastic_models import JumpDiffusion
        import numpy as _np_jd
        if bars_5m and len(bars_5m) >= 20:
            _returns = _np_jd.diff([b["close"] for b in bars_5m[-30:]]) / _np_jd.array([b["close"] for b in bars_5m[-31:-1]])
            _jd = JumpDiffusion(mu=0, sigma=float(_np_jd.std(_returns)), lam=0.1, jump_mu=0, jump_sigma=0.03)
            # If recent returns show jump-like behavior, reduce sizing
            _max_ret = float(_np_jd.max(_np_jd.abs(_returns[-5:])))
            if _max_ret > 3 * float(_np_jd.std(_returns)):
                for sig in all_signals:
                    sig["kelly_fraction"] *= 0.6
                    sig["jump_detected"] = True
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 128: PATH SIGNATURE REGIME CONFIRMATION ──
    try:
        from python_brain.ml.path_signatures import compute_signature, rolling_signatures
        import numpy as _np_ps
        if bars_5m and len(bars_5m) >= 20:
            _path = _np_ps.column_stack([
                [b["close"] for b in bars_5m[-20:]],
                [b["volume"] for b in bars_5m[-20:]],
            ])
            _sig_feats = compute_signature(_path, depth=2)
            if len(_sig_feats) > 0:
                # Positive first-order signature = uptrend → boost longs, penalize shorts
                if _sig_feats[0] > 0:
                    for sig in all_signals:
                        if sig.get("direction") == "Long":
                            sig["confidence"] = min(100, sig["confidence"] + 3)
                            sig["path_sig_trend"] = "up"
                elif _sig_feats[0] < 0:
                    # Negative signature = downtrend → penalize longs, reduce sizing
                    for sig in all_signals:
                        if sig.get("direction") == "Long":
                            sig["confidence"] = max(0, sig["confidence"] - 5)
                            sig["kelly_fraction"] *= 0.8
                            sig["shares"] = max(1, int(sig["shares"] * 0.8))
                            sig["path_sig_trend"] = "down"
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 30: NEWS SENTIMENT OVERLAY (STRENGTHENED) ──
    # Sentiment is now a HARD factor: very negative sentiment blocks longs,
    # very positive sentiment boosts longs AND sizes up.
    try:
        from python_brain.feeds.news_aggregator import get_cached_sentiment
        symbol = ticker_symbols.get(ticker_id, "")
        if symbol:
            _sent = get_cached_sentiment(symbol)
            if _sent is not None:
                if _sent > 0.7:
                    # Strongly positive: boost + size up
                    for sig in all_signals:
                        if sig.get("direction") == "Long":
                            sig["confidence"] = min(100, sig["confidence"] + 5)
                            sig["kelly_fraction"] *= 1.15  # 15% size-up on strong sentiment
                            sig["shares"] = max(1, int(sig["shares"] * 1.15))
                            sig["news_sentiment"] = round(_sent, 2)
                            sig["news_action"] = "strong_bullish_sizeup"
                elif _sent > 0.3:
                    for sig in all_signals:
                        if sig.get("direction") == "Long":
                            sig["confidence"] = min(100, sig["confidence"] + 3)
                            sig["news_sentiment"] = round(_sent, 2)
                elif _sent < -0.6:
                    # Strongly negative: BLOCK long entries (news is material)
                    all_signals = [s for s in all_signals if s.get("direction") != "Long"]
                    if not all_signals:
                        return None
                elif _sent < -0.3:
                    # Moderately negative: penalize + size down
                    for sig in all_signals:
                        sig["confidence"] = max(0, sig["confidence"] - 8)
                        sig["kelly_fraction"] *= 0.7
                        sig["shares"] = max(1, int(sig["shares"] * 0.7))
                        sig["news_sentiment"] = round(_sent, 2)
                        sig["news_action"] = "negative_sizedown"
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 40/148: OVERNIGHT GAP RISK SIZING ──
    # If we're near session close, reduce sizing for overnight holds
    try:
        from python_brain.overnight.gap_risk_monitor import GapRiskMonitor
        if not hasattr(_apply_adjustments, "_grm"):
            _apply_adjustments._grm = GapRiskMonitor()
        _grm = _apply_adjustments._grm
        _gap_pos = msg.get("open_positions", [])
        _gap_pos = _gap_pos if isinstance(_gap_pos, list) else []
        _gap_exp = _grm.assess_overnight_risk(
            positions=_gap_pos,
            vix=msg.get("vix", 20),
        )
        if _gap_exp and _gap_exp.get("reduce_sizing"):
            _gap_scale = _gap_exp.get("scale_factor", 0.7)
            for sig in all_signals:
                sig["kelly_fraction"] *= _gap_scale
                sig["overnight_gap_risk"] = _gap_exp.get("gap_var", 0)
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 94: CALENDAR/EVENT PROXIMITY SIZING ──
    try:
        from python_brain.execution.calendar_manager import get_event_proximity
        _evt = get_event_proximity(msg.get("timestamp_ns", 0))
        if _evt and _evt.get("minutes_to_event", 999) < 30:
            _evt_scale = max(0.5, 1.0 - _evt.get("impact", 0))
            for sig in all_signals:
                sig["kelly_fraction"] *= _evt_scale
                sig["event_proximity_mins"] = _evt["minutes_to_event"]
    except ImportError:
        pass
    except Exception:
        pass

    # ── BOOK 98: CANNIBALIZATION CHECK ──
    # If multiple signals fired, check for overlap cannibalization
    if len(all_signals) >= 2:
        try:
            from python_brain.risk.concentration_checks import compute_signal_overlap
            for i, sig_a in enumerate(all_signals):
                for sig_b in all_signals[i+1:]:
                    _overlap = compute_signal_overlap(
                        [sig_a], [sig_b], window_seconds=300,
                    )
                    if _overlap > 0.8:  # >80% overlap = cannibalization
                        # Keep the higher-confidence signal, suppress the other
                        if sig_a["confidence"] < sig_b["confidence"]:
                            sig_a["confidence"] = max(0, sig_a["confidence"] - 15)
                            sig_a["cannibalized_by"] = sig_b.get("strategy", "")
                        else:
                            sig_b["confidence"] = max(0, sig_b["confidence"] - 15)
                            sig_b["cannibalized_by"] = sig_a.get("strategy", "")
        except ImportError:
            pass
        except Exception:
            pass

    # ── BOOK 85 UPGRADE: SMOOTH PER-LOSS POSITION DEGRADATION ──
    # Replaces stepped 3/5/7 with continuous: each loss reduces by 10%, floor 20%.
    # 8+ losses = sacred halt (24h pause). Smooth curve prevents cliff edges.
    _consec_losses = msg.get("consecutive_losses", 0)
    if _consec_losses >= 8:
        sys.stderr.write(
            f"TILT_GUARD_HALT: {_consec_losses} consecutive losses — SACRED HALT\n"
        )
        sys.stderr.flush()
        return None  # Sacred halt after 8 consecutive losses
    elif _consec_losses >= 1:
        _tilt_scale = max(0.20, 1.0 - _consec_losses * 0.10)
        for sig in all_signals:
            sig["kelly_fraction"] *= _tilt_scale
            sig["shares"] = max(1, int(sig["shares"] * _tilt_scale))
            sig["consecutive_loss_scale"] = round(_tilt_scale, 2)
            sig["consecutive_losses"] = _consec_losses

    # ── PORTFOLIO HEAT / CONCENTRATION CHECK ──
    # If total open positions exceed 4 (for £10K account), reduce new entries.
    # Capital is finite — each additional position fragments edge.
    _open_pos_raw = msg.get("open_positions", [])
    _open_pos_list = _open_pos_raw if isinstance(_open_pos_raw, list) else []
    _open_pos_count = len(_open_pos_list) if isinstance(_open_pos_raw, list) else int(_open_pos_raw) if isinstance(_open_pos_raw, (int, float)) else 0
    _equity = msg.get("equity", 10000)
    _max_positions = max(3, int(_equity / 3000))  # Scale: £10K=3, £25K=8, £50K=16
    if _open_pos_count >= _max_positions:
        # At capacity — only allow signals with confidence > 75 (exceptional setups)
        all_signals = [s for s in all_signals if s.get("confidence", 0) >= 75]
        if all_signals:
            for sig in all_signals:
                sig["portfolio_at_capacity"] = True
                sig["max_positions"] = _max_positions
    elif _open_pos_count >= _max_positions - 1:
        # Near capacity — reduce sizing on new entries
        for sig in all_signals:
            sig["kelly_fraction"] *= 0.7
            sig["shares"] = max(1, int(sig["shares"] * 0.7))
            sig["near_capacity"] = True
    if not all_signals:
        return None

    # ── CROSS-ASSET CORRELATION HEAT ──
    # If adding a new position that's highly correlated with existing positions,
    # reduce sizing to avoid concentration risk.
    _open_symbols = [p.get("symbol", "") for p in _open_pos_list if isinstance(p, dict) and p.get("symbol")]
    _new_sym = ticker_symbols.get(ticker_id, "")
    if _new_sym and _open_symbols:
        # Simple sector correlation check: same prefix = same sector
        _new_prefix = _new_sym[:3].upper()
        _sector_overlap = sum(1 for s in _open_symbols if s[:3].upper() == _new_prefix)
        if _sector_overlap >= 2:
            # Already hold 2+ from same sector — heavy concentration penalty
            for sig in all_signals:
                sig["kelly_fraction"] *= 0.5
                sig["shares"] = max(1, int(sig["shares"] * 0.5))
                sig["sector_concentration"] = f"{_sector_overlap}_same_sector"
        elif _sector_overlap == 1:
            for sig in all_signals:
                sig["kelly_fraction"] *= 0.8
                sig["shares"] = max(1, int(sig["shares"] * 0.8))
                sig["sector_overlap"] = True

    # Select best signal after all adjustments
    all_signals.sort(key=lambda s: s["confidence"], reverse=True)
    best = all_signals[0]

    # Book 209: Bayesian multi-source aggregation when multiple strategies fire.
    # If 2+ strategies fire on the same tick, the posterior from all sources
    # adjusts the best signal's confidence (up if consensus, down if conflict).
    if len(all_signals) >= 2:
        try:
            from python_brain.aggregation.bayesian_aggregator import aggregate_signals
            agg_result = aggregate_signals(all_signals)
            if agg_result is not None:
                bayes_conf = agg_result.confidence
                naive_conf = best["confidence"]
                # If Bayesian posterior agrees with best signal direction
                if agg_result.direction.lower() == best.get("direction", "").lower():
                    # Boost: posterior is consensus-weighted (but cap at +10)
                    boost = min(10, max(0, bayes_conf - naive_conf))
                    best["confidence"] = min(100, naive_conf + boost)
                    best["bayes_boost"] = boost
                else:
                    # Conflict: multiple sources disagree on direction — dampen
                    penalty = min(15, max(0, naive_conf - bayes_conf))
                    best["confidence"] = max(0, naive_conf - penalty)
                    best["bayes_conflict_penalty"] = penalty
                best["bayes_posterior"] = agg_result.posterior_prob
                best["bayes_n_agree"] = agg_result.n_sources_agree
                best["bayes_n_total"] = agg_result.n_sources_total
                # Book 209: Use Bayesian Kelly when it's more conservative than naive
                if hasattr(agg_result, 'kelly_fraction') and agg_result.kelly_fraction > 0:
                    _naive_kelly = best.get("kelly_fraction", 0.20)
                    # Take the MINIMUM of Bayesian and naive Kelly (conservative)
                    best["kelly_fraction"] = min(_naive_kelly, agg_result.kelly_fraction)
                    best["bayes_kelly"] = agg_result.kelly_fraction
        except ImportError:
            pass  # Module not deployed yet
        except Exception as bayes_err:
            # Non-fatal — fall through to naive best-of
            if _tick_counts.get(ticker_id, 0) % 100 == 0:
                sys.stderr.write(f"BAYES_ERR: {bayes_err}\n")
                sys.stderr.flush()

    # ── BOOK 18: FACTOR CONCENTRATION GATE ──
    # Block signals when portfolio overexposed to single alpha factor (>60%).
    if best.get("type") == "signal" and not _SIM_MODE:
        try:
            from python_brain.analytics.factor_zoo import classify_signal, get_factor_exposure, factor_concentration_gate
            _new_factor = classify_signal(best.get("strategy", ""))
            _active_pos = msg.get("active_positions", [])
            if _active_pos:
                _exposure = get_factor_exposure(_active_pos)
                _blocked, _block_reason = factor_concentration_gate(_new_factor, _exposure)
                if _blocked:
                    return {"type": "no_signal", "ticker_id": ticker_id,
                            "reason": f"factor_concentration: {_block_reason}"}
            best["factor_category"] = _new_factor.value if hasattr(_new_factor, 'value') else str(_new_factor)
        except ImportError:
            pass
        except Exception:
            pass

    # ── BOOK 188: PRE-TRADE RISK CHECKS ──
    # 5-point risk validation: notional, correlation, sector, intraday P&L, order ratio.
    if best.get("type") == "signal" and not _SIM_MODE:
        try:
            from python_brain.risk.pre_trade_checks import pre_trade_risk_check
            _portfolio = {
                "equity": msg.get("account_equity", 10000),
                "positions": msg.get("active_positions", []),
                "daily_pnl_pct": msg.get("daily_pnl_pct", 0),
                "orders_today": msg.get("orders_today", 0),
                "fills_today": msg.get("fills_today", 0),
            }
            _passed, _reason = pre_trade_risk_check(best, _portfolio, {"vix": msg.get("vix", 20)})
            if not _passed:
                return {"type": "no_signal", "ticker_id": ticker_id,
                        "reason": f"pre_trade_188: {_reason}"}
        except ImportError:
            pass
        except Exception:
            pass

    # ── BOOKS 151/152: AGENT SWARM CONSENSUS ──
    # 10 micro-agents each vote on signal quality. Low consensus → dampen/block.
    if best.get("type") == "signal" and not _SIM_MODE:
        try:
            from python_brain.swarm.signal_consensus import get_swarm
            _swarm = get_swarm()
            _swarm_result = _swarm.evaluate(
                signal_dict=best, indicators_dict=ind, msg_dict=msg
            )
            if _swarm_result.should_block:
                return {"type": "no_signal", "ticker_id": ticker_id,
                        "reason": f"swarm_blocked (score={_swarm_result.score})"}
            if _swarm_result.confidence_delta != 0:
                best["confidence"] = max(0, min(100,
                    best.get("confidence", 50) + _swarm_result.confidence_delta))
            best["swarm_score"] = _swarm_result.score
            best["swarm_votes"] = f"{_swarm_result.n_agree}/{_swarm_result.n_total}"
        except ImportError:
            pass  # Module not deployed
        except Exception as _sw_err:
            if _tick_counts.get(ticker_id, 0) % 200 == 0:
                sys.stderr.write(f"SWARM_ERR: {_sw_err}\n")
                sys.stderr.flush()

    # ── BOOK 42: HEDGE CONFIDENCE OVERLAY ──
    # Reduce confidence for long signals when hedge is active.
    # Kill switch boosts short signals and penalizes longs more aggressively.
    num_active_hedge_sigs = sum(
        1 for sig in all_signals if sig.get("strategy", "").startswith("HEDGE_")
    )
    best = _apply_hedge_confidence_overlay(best, msg, num_active_hedge_sigs)

    # COMPOUNDING MACHINE: Scale Kelly by edge-proportional allocation weight.
    # Strategies with proven higher Sharpe get more capital. Unproven get 50%.
    strat_name = best.get("strategy", "")
    alloc_weight = get_strategy_weight(strat_name)
    if alloc_weight < 1.0:
        best["kelly_fraction"] *= max(alloc_weight, 0.1)  # Floor 10% to keep data flowing
        best["shares"] = max(1, int(best["shares"] * max(alloc_weight, 0.1)))
    best["allocation_weight"] = round(alloc_weight, 3)

    # Per-ticker cooldown
    tick_count = _tick_counts.get(ticker_id, 0)
    if not _SIM_MODE:
        # Use regime-adaptive cooldown if available, else static
        _effective_cooldown = msg.get("_regime_cooldown_ticks", SIGNAL_COOLDOWN_TICKS)
        last_sig = _last_signal_tick.get(ticker_id, -_effective_cooldown - 1)
        if tick_count - last_sig < _effective_cooldown:
            return None  # Cooldown active

    # STS confidence adjustment
    best["strategy_confidence"] = best["confidence"]
    ss = ind["structural_score"]
    if ss > 70:
        best["confidence"] = min(100, best["confidence"] + min(6, (ss - 70) // 5))
    elif ss < 50:
        best["confidence"] = max(0, best["confidence"] - min(4, (50 - ss) // 5))
    best["structural_score"] = ss

    # P5: System 1+ signals bypass legacy TypeA-F classification entirely.
    # They have their own strategy identity and validation path.
    _SYSTEM_STRATEGIES = {
        "S1_Microstructure", "S2_Reversion", "S3_MacroTrend",
        "S4_VolPremium", "S5_OvernightCarry", "S6_Catalyst", "S7_TailHedge",
    }
    is_system_signal = best.get("strategy", "") in _SYSTEM_STRATEGIES

    if not is_system_signal:
        # TypeA-F classification (all variables local — no scope bugs)
        cls_prices = [t["last"] for t in list(bar_history[ticker_id])]
        cls_volumes = [t.get("volume", 0) for t in list(bar_history[ticker_id])]
        cls_rsi = calculate_rsi(cls_prices, period=14) if len(cls_prices) >= 14 else None
        entry_type = classify_entry_type(
            rsi_14=cls_rsi, ibs=ind["ibs"], rvol=ind["rvol"], ticker_id=ticker_id,
            prices=cls_prices, volumes=cls_volumes, vol_div=ind["vol_div"],
        )
        best["entry_type"] = entry_type

        # STRATEGY REGISTRY (2026-03-29 quarantine per Mega Audit Session 3):
        # ALL Type A-F QUARANTINED for £10K account. Running 6 legacy signal types
        # on a capital-constrained account fragments capital and generates noise.
        # Focus: VanguardSniper + ApexScout + System S1-S7 only.
        # When account grows to £25K+, re-enable TypeB/F first (highest edge).
        _DISABLED_TYPES = {"TypeA", "TypeB", "TypeC", "TypeD", "TypeE", "TypeF"}
        _SHADOW_TYPES = set()
        if entry_type in _DISABLED_TYPES:
            return None
        if entry_type in _SHADOW_TYPES:
            sys.stderr.write(f"SHADOW_SIGNAL: {entry_type} tid={ticker_id} conf={best['confidence']} (logged, not emitted)\n")
            sys.stderr.flush()
            return None
    else:
        # System signals keep their own strategy name and entry_type
        best["entry_type"] = best["strategy"]

    # S8: Regime + session enforcement from strategy_registry.json
    if not _SIM_MODE:
        strategy_id = best.get("strategy", entry_type)
        allowed, reason = _check_regime_session_gate(
            strategy_id, ind["hurst"], ind["rvol"], ind["adx"],
        )
        if not allowed:
            sys.stderr.write(
                f"REGIME_SESSION_VETO: {strategy_id} tid={ticker_id} — {reason}\n"
            )
            sys.stderr.flush()
            return None

    if not is_system_signal:
        if entry_type != "Unclassified":
            best["strategy"] = entry_type
        cls_rsi_val = cls_rsi if cls_rsi is not None else 0.0
    else:
        cls_rsi_val = 0.0
        entry_type = best["entry_type"]
    best["rsi"] = cls_rsi_val
    best["ibs"] = ind["ibs"]

    # Adaptive entry type weight → Kelly sizing
    if _adaptive_entry_weights:
        if entry_type in _adaptive_entry_weights:
            ew = _adaptive_entry_weights[entry_type]
            if ew < 1.0:
                best["kelly_fraction"] = best["kelly_fraction"] * ew
                best["shares"] = max(1, int(best["shares"] * ew))
        best["adaptive_entry_weights"] = _adaptive_entry_weights

    if _adaptive_entry_confidence:
        best["adaptive_entry_confidence"] = _adaptive_entry_confidence

    # Adaptive exchange weight → Kelly sizing
    if _adaptive_exchange_weights:
        exchange = msg.get("exchange", "")
        if exchange and exchange in _adaptive_exchange_weights:
            exw = _adaptive_exchange_weights[exchange]
            if exw < 1.0:
                best["kelly_fraction"] = best["kelly_fraction"] * exw
                best["shares"] = max(1, int(best["shares"] * exw))
        best["adaptive_exchange_weights"] = _adaptive_exchange_weights

    # Adaptive Kelly cap (drawdown-aware)
    if _adaptive_kelly_cap is not None and _adaptive_kelly_cap < 0.05:
        if best["kelly_fraction"] > _adaptive_kelly_cap:
            best["kelly_fraction"] = _adaptive_kelly_cap
            eq = msg.get("equity", 10000.0)
            pr = max(msg["last"], 1e-9)
            best["shares"] = max(1, int(_adaptive_kelly_cap * eq / pr))
        best["adaptive_kelly_cap"] = _adaptive_kelly_cap

    # ── BOOK 24: EVENT PROXIMITY CHECK ──
    # Reduce sizing near high-impact events (FOMC, CPI, NFP)
    try:
        from python_brain.events.event_calendar import get_event_calendar
        _evt_cal = get_event_calendar()
        _near_event = _evt_cal.is_near_event(msg.get("timestamp_ns", 0))
        if _near_event:
            _evt_sizing = _evt_cal.get_sizing_modifier(_near_event)
            best["kelly_fraction"] *= _evt_sizing
            best["shares"] = max(1, int(best["shares"] * _evt_sizing))
            best["event_near"] = _near_event.name
            best["event_sizing_mult"] = _evt_sizing
    except ImportError:
        pass

    # ── BOOK 27: LEVERAGE-OPTIMAL ALLOCATION ──
    # Adjust position size based on Kelly-optimal leverage L*
    try:
        from python_brain.sizing.leverage_selector import get_leverage_selector
        _lev_sel = get_leverage_selector()
        _sym = ticker_symbols.get(ticker_id, "")
        _etp_lev = msg.get("leverage", 1)
        _regime_str = hurst_regime if hurst_regime in ("STEADY", "INFLATION", "WOI", "CRISIS") else "STEADY"
        if _etp_lev > 1 and _sym:
            _lev_result = _lev_sel.get_allocation(_sym, _etp_lev, _regime_str)
            if _lev_result.allocation_fraction < 1.0:
                best["kelly_fraction"] *= _lev_result.allocation_fraction
                best["shares"] = max(1, int(best["shares"] * _lev_result.allocation_fraction))
                best["leverage_l_star"] = _lev_result.l_star
                best["leverage_alloc"] = round(_lev_result.allocation_fraction, 3)
                best["leverage_drag_pct"] = round(_lev_result.drag_pct, 3)
            if _lev_result.warning:
                best["leverage_warning"] = _lev_result.warning
    except ImportError:
        pass

    # VPIN — LOW VPIN + HIGH RVOL = smart money accumulating quietly.
    # This is ACTIONABLE: low VPIN (< 0.3) with high RVOL (> 2.5) = informed traders
    # are accumulating while retail doesn't notice. BOOST long signals.
    best["vpin"] = round(ind["vpin"], 4)
    _vpin_val = ind["vpin"]
    if _vpin_val < 0.3 and ind["rvol"] > 2.5:
        best["vpin_smart_money_accumulation"] = True
        if best.get("direction") == "Long":
            best["confidence"] = min(100, best["confidence"] + 5)
            best["kelly_fraction"] *= 1.2  # 20% size-up on smart money signal
            best["shares"] = max(1, int(best["shares"] * 1.2))
    elif _vpin_val > 0.70:
        # Elevated VPIN = informed selling — reduce longs even if below 0.80 block threshold
        best["kelly_fraction"] *= 0.8
        best["shares"] = max(1, int(best["shares"] * 0.8))
        best["vpin_elevated_caution"] = True

    # ── D-VPIN HARD VETO: Informed sellers detected ──
    # D-VPIN < -0.30 = net informed selling pressure. BLOCK long entries.
    _d_vpin = best.get("d_vpin", 0)
    if _d_vpin < -0.30 and best.get("direction") == "Long":
        sys.stderr.write(
            f"D_VPIN_VETO: tid={ticker_id} d_vpin={_d_vpin:.3f} — informed sellers detected\n"
        )
        sys.stderr.flush()
        return None

    # ── BOOK 32: MICROSTRUCTURE ENTRY QUALITY (shadow mode) ──
    try:
        from python_brain.analytics.microstructure import get_micro_state
        _micro = get_micro_state(ticker_id)
        _micro.on_bar(
            price_open=msg.get("open", msg.get("last", 0)),
            price_close=msg.get("last", msg.get("close", 0)),
            volume=msg.get("volume", 0),
            bid=msg.get("bid", 0),
            ask=msg.get("ask", 0),
        )
        _micro_score = _micro.entry_score(
            signal_direction=1,
            strategy=best.get("strategy", ""),
        )
        best["micro_score"] = round(_micro_score.total, 1)
        best["micro_action"] = _micro_score.action
        best["vpin_regime"] = _micro_score.vpin_regime
        best["lambda_regime"] = _micro_score.lambda_regime
        # Phase 2 ACTIVATED: microstructure gate is now LIVE.
        # BLOCK = toxic microstructure (spoofing, wide spreads, thin book).
        # REDUCE = marginal conditions — reduce confidence by 10 instead of blocking.
        if _micro_score.action == "BLOCK":
            sys.stderr.write(
                f"MICRO_GATE_BLOCK: tid={ticker_id} score={_micro_score.total:.1f} "
                f"vpin={_micro_score.vpin_regime} lambda={_micro_score.lambda_regime}\n"
            )
            sys.stderr.flush()
            return None
        elif _micro_score.action == "REDUCE":
            best["confidence"] = max(0, best["confidence"] - 10)
            best["micro_reduced"] = True
    except ImportError:
        pass

    # Claude curator — LIVE GATE with graduated authority
    # Low confidence (< 65): Claude is advisory (soft gate, -15 conf + halve sizing)
    # Medium confidence (65-80): Claude has veto power (hard block on reject)
    # High confidence (> 80): Claude is override-only (can reduce but not block)
    if best.get("confidence", 0) >= 50 and not _SIM_MODE:
        try:
            from python_brain.ouroboros.claude_curator import evaluate_signal
            cr = evaluate_signal(
                signal_dict=best,
                market_context={
                    "regime": hurst_regime, "drawdown_pct": msg.get("drawdown_pct", 0),
                    "vix": msg.get("vix", 20), "equity": msg.get("equity", 10000),
                    "exchange": msg.get("exchange", ""), "open_positions": msg.get("open_positions", 0),
                    "trades_today": msg.get("trades_today", 0),
                    "consecutive_losses": msg.get("consecutive_losses", 0),
                    "daily_pnl_pct": msg.get("daily_pnl_pct", 0),
                }
            )
            if cr.get("claude_verdict") == "reject":
                best["claude_rejected"] = True
                best["claude_reasoning"] = cr.get("reasoning", "")[:200]
                _cur_conf = best["confidence"]
                if 65 <= _cur_conf <= 80:
                    # Medium confidence — Claude has HARD VETO
                    sys.stderr.write(
                        f"CLAUDE_HARD_VETO: tid={ticker_id} conf={_cur_conf} "
                        f"reason={cr.get('reasoning', '')[:80]}\n"
                    )
                    sys.stderr.flush()
                    return None
                elif _cur_conf > 80:
                    # High confidence — Claude reduces but doesn't block
                    best["confidence"] = max(0, best["confidence"] - 10)
                    best["kelly_fraction"] = best["kelly_fraction"] * 0.6
                    best["shares"] = max(1, int(best["shares"] * 6 // 10))
                else:
                    # Low confidence — soft gate (original behavior)
                    best["confidence"] = max(0, best["confidence"] - 15)
                    best["kelly_fraction"] = best["kelly_fraction"] * 0.5
                    best["shares"] = max(1, best["shares"] // 2)
                best["shares"] = max(1, best["shares"] // 2)
                sys.stderr.write(
                    f"CLAUDE_SOFT_GATE: tid={ticker_id} conf_reduced_by=15 "
                    f"reason={cr.get('reasoning', '')[:80]}\n"
                )
                sys.stderr.flush()
            elif cr.get("adjusted_confidence"):
                best["claude_adjusted_confidence"] = cr["adjusted_confidence"]
                # P9: Apply Claude's adjusted confidence if it's lower (conservative gate)
                adj = cr["adjusted_confidence"]
                if adj < best["confidence"]:
                    best["confidence"] = adj
            best["claude_verdict"] = cr.get("claude_verdict", "no_response")
        except Exception as e:
            best["claude_error"] = str(e)[:200]

    # ── BOOK 140: CLAUDE MULTI-AGENT DEBATE (HIGH-VALUE SIGNALS ONLY) ──
    # Only invoke for signals worth the API cost: conf >= 75 AND kelly >= 0.03
    if best.get("confidence", 0) >= 75 and best.get("kelly_fraction", 0) >= 0.03 and not _SIM_MODE:
        try:
            from python_brain.claude.multi_agent_debate import Debate9, DebateProtocol
            if not hasattr(_apply_adjustments, "_debate"):
                _apply_adjustments._debate = Debate9()
            _debate = _apply_adjustments._debate
            _verdict = _debate.debate(best, protocol=DebateProtocol.ADVERSARIAL, timeout=3)
            if _verdict:
                if _verdict.action == "VETO":
                    sys.stderr.write(
                        f"DEBATE_VETO: tid={ticker_id} conf={best['confidence']} "
                        f"consensus={getattr(_verdict, 'consensus_score', 0):.2f}\n"
                    )
                    sys.stderr.flush()
                    return None  # Hard veto from debate
                elif _verdict.action == "REDUCE":
                    best["confidence"] = max(0, best["confidence"] - 10)
                    best["kelly_fraction"] *= 0.6
                    best["shares"] = max(1, int(best["shares"] * 0.6))
                best["debate_verdict"] = _verdict.action
                best["debate_consensus"] = getattr(_verdict, 'consensus_score', 0)
        except ImportError:
            pass
        except Exception:
            pass  # Timeout/error = proceed without debate (fail-open)

    # ── BOOKS 193, 198: BULLISH BIAS CORRECTION (Adjustment Layer 87) ──
    # LLMs recommend BUY 2-3x more than SELL. Over-weight bearish evidence,
    # under-weight bullish signals to correct for this systematic bias.
    try:
        n_long = sum(1 for s in all_signals if s.get("direction") == "Long")
        n_short = sum(1 for s in all_signals if s.get("direction") == "Short")
        n_total = n_long + n_short
        ls_ratio = n_long / max(n_short, 1)

        best["long_short_ratio"] = round(ls_ratio, 2)
        best["bullish_bias_corrected"] = False

        if best.get("direction") == "Long" and n_total > 0:
            penalty = 0
            if ls_ratio >= 3.0:
                penalty = 10
            elif ls_ratio >= 2.0:
                penalty = 5

            if penalty > 0:
                old_conf = best["confidence"]
                best["confidence"] = max(0, best["confidence"] - penalty)
                best["bullish_bias_corrected"] = True
                sys.stderr.write(
                    f"BULLISH_BIAS_CORRECTION: tid={ticker_id} "
                    f"L/S={n_long}/{n_short} ratio={ls_ratio:.1f} "
                    f"penalty=-{penalty} conf={old_conf}->{best['confidence']}\n"
                )
                sys.stderr.flush()

        # Boost rare Short signals — if < 25% of total are Short
        if best.get("direction") == "Short" and n_total > 0:
            short_pct = n_short / n_total
            if short_pct < 0.25:
                old_conf = best["confidence"]
                best["confidence"] = min(100, best["confidence"] + 3)
                best["bullish_bias_corrected"] = True
                sys.stderr.write(
                    f"BULLISH_BIAS_SHORT_BOOST: tid={ticker_id} "
                    f"short_pct={short_pct:.2f} conf={old_conf}->{best['confidence']}\n"
                )
                sys.stderr.flush()
    except Exception as e:
        sys.stderr.write(f"BULLISH_BIAS_ERROR: tid={ticker_id} {str(e)[:120]}\n")
        sys.stderr.flush()

    # P5: Per-strategy signal tracking for validation metrics
    strat = best.get("strategy", "unknown")
    _strategy_signal_counts[strat] += 1
    _strategy_total_confidence[strat] += best.get("confidence", 0)
    n = _strategy_signal_counts[strat]
    if n % 25 == 0 or n <= 3:
        avg_conf = _strategy_total_confidence[strat] / n if n > 0 else 0
        sys.stderr.write(
            f"STRATEGY_TRACKER: {strat} signals={n} avg_conf={avg_conf:.1f} "
            f"tid={ticker_id} conf={best.get('confidence', 0)}\n"
        )
        sys.stderr.flush()

    # P7: Cannibalization detection — track co-fires within 5-min window per ticker
    ts_ns = msg.get("timestamp_ns", 0)
    if ts_ns > 0 and strat.startswith("S"):
        _cofire_total[strat] = _cofire_total.get(strat, 0) + 1
        recent = _recent_strategy_fires.get(ticker_id, [])
        # Check for co-fires with other strategies on same ticker
        cutoff = ts_ns - _COFIRE_WINDOW_NS
        for prev_strat, prev_ts in recent:
            if prev_ts >= cutoff and prev_strat != strat:
                pair = tuple(sorted([strat, prev_strat]))
                _cofire_counts[pair] = _cofire_counts.get(pair, 0) + 1
                ct = _cofire_counts[pair]
                if ct % 10 == 0:
                    denom = min(_cofire_total.get(pair[0], 1), _cofire_total.get(pair[1], 1))
                    rho_est = ct / max(denom, 1)
                    severity = "CRITICAL" if rho_est > 0.70 else "WARNING" if rho_est > 0.50 else "INFO"
                    sys.stderr.write(
                        f"CANNIBALIZATION_{severity}: {pair[0]}↔{pair[1]} "
                        f"co-fires={ct} rho_est={rho_est:.2f}\n"
                    )
                    sys.stderr.flush()
        # Record this fire
        recent = [(s, t) for s, t in recent if t >= cutoff]
        recent.append((strat, ts_ns))
        _recent_strategy_fires[ticker_id] = recent[-10:]  # Keep last 10

    # Record cooldown
    _last_signal_tick[ticker_id] = tick_count
    return best


def process_tick(msg):
    """Process a tick message through 5 stages: ingest → indicators → gates → signals → output."""
    ticker_id = msg["ticker_id"]
    _load_adaptive_params()

    # COMPOUNDING: Track daily equity for CAGR computation
    equity = msg.get("equity", 0)
    if equity > 0:
        _track_daily_equity(equity)

    # Track symbol mapping
    if "symbol" in msg and msg["symbol"]:
        ticker_symbols[ticker_id] = msg["symbol"]

    # Ingest tick
    bar_history[ticker_id].append({
        "last": msg["last"], "bid": msg.get("bid", msg["last"]),
        "ask": msg.get("ask", msg["last"]), "high": msg.get("high", msg["last"]),
        "low": msg.get("low", msg["last"]), "volume": msg.get("volume", 0),
        "timestamp_ns": msg.get("timestamp_ns", 0),
    })
    ticks = list(bar_history[ticker_id])

    # VWAP session reset on date change
    ts_ns = msg.get("timestamp_ns", 0)
    if ts_ns > 0:
        from datetime import datetime, timezone
        td = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).strftime("%Y-%m-%d")
        prev = _last_vwap_date.get(ticker_id)
        if prev is not None and td != prev:
            vwap_calculators[ticker_id].reset()
        _last_vwap_date[ticker_id] = td

    no_signal = {"type": "no_signal", "ticker_id": ticker_id}

    # Early exit: blackout period
    if not _SIM_MODE:
        if _is_in_blackout(ticker_id, msg.get("timestamp_ns", 0)):
            global _blackout_warned, _blackout_warned_date
            import datetime as _dt_bo
            today = _dt_bo.date.today().isoformat()
            if _blackout_warned_date != today:
                _blackout_warned = set()
                _blackout_warned_date = today
            sym = ticker_symbols.get(ticker_id, str(ticker_id))
            if sym not in _blackout_warned:
                exch = _get_exchange_for_symbol(sym) or "?"
                sys.stderr.write(f"BLACKOUT_VETO: {sym} (tid={ticker_id}) past {exch} entry cutoff\n")
                sys.stderr.flush()
                _blackout_warned.add(sym)
            return no_signal

    # Early exit: blacklist
    blacklist = set() if _SIM_MODE else _load_ticker_blacklist()
    sym = ticker_symbols.get(ticker_id, "")
    if sym and sym in blacklist:
        global _blacklist_warned, _blacklist_warned_date
        import datetime as _dt
        today = _dt.date.today().isoformat()
        if _blacklist_warned_date != today:
            _blacklist_warned = set()
            _blacklist_warned_date = today
        if sym not in _blacklist_warned:
            sys.stderr.write(f"BLACKLIST_VETO: {sym} (tid={ticker_id}) suppressed by Ouroboros blacklist\n")
            sys.stderr.flush()
            _blacklist_warned.add(sym)
        return no_signal

    # S6: Early exit: blocked exchanges (e.g. LSEETF leveraged ETPs — 0% WR, -£30)
    if not _SIM_MODE and sym:
        _load_symbol_exchange_map()  # ensure maps are loaded
        raw_exch = _symbol_raw_exchange_map.get(sym, "")
        blocked_exchanges = _load_blocked_exchanges()
        if raw_exch and raw_exch in blocked_exchanges:
            if sym not in _blacklist_warned:
                sys.stderr.write(f"EXCHANGE_VETO: {sym} (tid={ticker_id}) blocked — {raw_exch} in blocked_exchanges\n")
                sys.stderr.flush()
                _blacklist_warned.add(sym)
            return no_signal

    # Early exit: warmup
    MIN_WARMUP = 10 if _SIM_MODE else 50
    if len(ticks) < MIN_WARMUP:
        return no_signal

    # Diagnostic logging (every 500th tick)
    _tick_counts[ticker_id] = _tick_counts.get(ticker_id, 0) + 1
    if _tick_counts[ticker_id] % 500 == 1:
        sys.stderr.write(f"BRIDGE_DIAG: tid={ticker_id} bars={len(ticks)} price={msg['last']:.4f} vol={msg.get('volume', 0)}\n")
        sys.stderr.flush()

    # Stage 1: Compute indicators
    ind = _compute_indicators(ticker_id, ticks, msg)

    # ── BOOK 77: Update leader bar closes for cross-market lead-lag ──
    # Book 136: maxlen=120 for rolling 100-bar R² correlation calculation
    symbol = ticker_symbols.get(ticker_id, "")
    if symbol and ind.get("bars_5m"):
        from collections import deque
        if symbol not in _leader_bar_closes:
            _leader_bar_closes[symbol] = deque(maxlen=120)
        _leader_bar_closes[symbol].append(ind["bars_5m"][-1]["close"])

    # Build indicator dict for gate veto logging
    _ind = {
        "hurst": ind["hurst"], "adx": ind["adx"], "rvol": ind["rvol"],
        "vol_slope": ind["vol_slope"], "n_5min_bars": ind["n_5min_bars"],
        "n_ticks": len(ticks), "hurst_regime": ind["hurst_regime"],
        "spread_pct": ind["spread_pct"], "vwap_dist_pct": ind["vwap_dist_pct"],
        "bid": ind["bid"], "ask": ind["ask"], "leverage": msg.get("leverage", 1),
        "session_mode": msg.get("session_mode", "?"), "volume": msg.get("volume", 0),
        "vpin": ind["vpin"], "structural_score": ind["structural_score"],
    }

    # Enrich no_signal with indicators for Rust telemetry
    no_signal.update({"rvol": ind["rvol"], "hurst": ind["hurst"],
                      "hurst_regime": ind["hurst_regime"], "volume_divergence": ind["vol_div"]})

    # Stage 2: Quality gates
    gate_pass, gate_name, gate_detail = _check_quality_gates(ticker_id, msg, ticks, ind)
    if not gate_pass:
        _log_gate_veto(ticker_id, gate_name, msg["last"], _ind, gate_detail)
        return no_signal

    # Stage 3: Generate signals (returns ALL signals, no bottleneck)
    conf_floor = _compute_confidence_floor(msg, ind)
    all_signals = _generate_signals(ticker_id, msg, ticks, ind, conf_floor)

    # Stage 4: Adjust ALL signals, select best, classify, size
    best = _apply_adjustments(ticker_id, msg, ind, all_signals)

    # Stage 5: Output — Book 208 quality gate + Book 207 schema validation
    if best:
        # Book 208: Quality gate — PAPER strategies produce shadow signals only
        strat_name_qg = best.get("strategy", "")
        if not _SIM_MODE and strat_name_qg:
            try:
                from python_brain.validation.quality_gates import is_strategy_live, log_shadow_signal
                if not is_strategy_live(strat_name_qg):
                    log_shadow_signal(best)
                    return no_signal
            except ImportError:
                pass  # Module not deployed yet — fail-open

        # Book 207: Validate signal schema before sending to Rust
        try:
            from python_brain.validation.signal_schema import NormalizedSignal
            ns = NormalizedSignal.from_dict(best)
            ns.validate()
            best = ns.to_dict()
        except ValueError as ve:
            sys.stderr.write(f"SCHEMA_REJECT: tid={ticker_id} {ve}\n")
            sys.stderr.flush()
            return no_signal
        except ImportError:
            pass  # Module not deployed yet — fail-open

        # Book 1: Record entry confidence for IC computation on exit
        _entry_confidences[(ticker_id, best.get("strategy", ""))] = best.get("confidence", 0)

        # ── EXIT TIMING HINTS FOR RUST ENGINE ──
        # Provide context to the Rust exit engine for adaptive Chandelier parameters.
        # The Rust side can use these to widen/tighten stops dynamically.
        _strat = best.get("strategy", "")
        _leverage_exit = msg.get("leverage", 1)

        # Time-based exit decay: positions held past optimal holding period lose edge.
        # Momentum: 2-8 hours optimal. Mean-reversion: 0.5-4 hours. Overnight carry: 12-16 hours.
        if _strat in ("Momentum", "VolExpansion", "S3_MacroTrend", "S1_Microstructure"):
            best["suggested_max_hold_hours"] = 8
            best["exit_urgency_ramp_hours"] = 6  # Start tightening stops after 6h
        elif _strat in ("IBS_MeanReversion", "S2_Reversion", "PairsReversion"):
            best["suggested_max_hold_hours"] = 4
            best["exit_urgency_ramp_hours"] = 2  # MR should mean-revert quickly
        elif _strat == "S5_OvernightCarry":
            best["suggested_max_hold_hours"] = 16
            best["exit_urgency_ramp_hours"] = 14
        elif _strat == "S7_TailHedge":
            best["suggested_max_hold_hours"] = 48
            best["exit_urgency_ramp_hours"] = 24  # Tail events play out over days
        else:
            best["suggested_max_hold_hours"] = 12
            best["exit_urgency_ramp_hours"] = 8

        # ── BOOK 180/186: REGIME-CONDITIONAL OVERNIGHT HOLDING ──
        # HIGH vol (VIX > 30): flatten 3x positions before close — no overnight holds.
        # MEDIUM vol (VIX 20-30): hold only if confidence > 70.
        # LOW vol (VIX < 20): hold if confidence > 50 (normal).
        # This prevents catastrophic overnight gap losses in volatile regimes.
        _vix_exit = msg.get("vix", 20)
        if _leverage_exit >= 3 and _vix_exit > 30:
            # HIGH VOL: Force intraday-only for 3x ETPs
            best["suggested_max_hold_hours"] = min(best.get("suggested_max_hold_hours", 8), 6)
            best["exit_urgency_ramp_hours"] = min(best.get("exit_urgency_ramp_hours", 4), 4)
            best["etp_hold_rule"] = "intraday_only_high_vol"
        elif _leverage_exit >= 3 and _vix_exit > 20:
            # MEDIUM VOL: Shorter holds for 3x ETPs
            if best.get("confidence", 0) < 70:
                best["suggested_max_hold_hours"] = min(best.get("suggested_max_hold_hours", 8), 8)
                best["exit_urgency_ramp_hours"] = min(best.get("exit_urgency_ramp_hours", 6), 5)
                best["etp_hold_rule"] = "shortened_medium_vol"

        # Leverage-adjusted initial stop width: higher leverage = tighter stops
        # 3x ETPs move 3x — a 2% underlying move = 6% on the ETP
        if _leverage_exit >= 5:
            best["suggested_initial_stop_atr_mult"] = 1.2  # Tight — 5x amplifies moves
        elif _leverage_exit >= 3:
            best["suggested_initial_stop_atr_mult"] = 1.5  # Standard for 3x
        elif _leverage_exit >= 2:
            best["suggested_initial_stop_atr_mult"] = 1.8  # Slightly wider for 2x
        else:
            best["suggested_initial_stop_atr_mult"] = 2.5  # Single stocks: wider stops

        # Regime-adaptive exit: trending regimes should use wider trailing stops (let winners run).
        # Mean-reverting regimes: tighter stops (quick profit capture).
        _hurst_exit = ind.get("hurst", 0.5)
        if _hurst_exit > 0.55:
            best["exit_trail_bias"] = "wide"  # Trending — let it run
            best["suggested_rung3_atr"] = 1.2
        elif _hurst_exit < 0.35:
            best["exit_trail_bias"] = "tight"  # Mean-reverting — capture quickly
            best["suggested_rung3_atr"] = 0.7
        else:
            best["exit_trail_bias"] = "neutral"
            best["suggested_rung3_atr"] = 1.0

        # Volatility-adjusted stops: in high-vol environments, widen stops to avoid noise exits
        _atr_pct_exit = best.get("atr_pct", 0)
        if _atr_pct_exit > 1.5:
            best["exit_vol_adjust"] = "widen"  # High vol — give room
        elif _atr_pct_exit < 0.5:
            best["exit_vol_adjust"] = "tighten"  # Low vol — capture small moves

        # Spread-adjusted profit target: if spread > 0.3%, need higher profit to cover costs
        _spread_exit = ind.get("spread_pct", 0)
        if _spread_exit > 0.5:
            best["min_profit_target_pct"] = round(_spread_exit * 3, 2)  # Need 3x spread to be worthwhile
        elif _spread_exit > 0.2:
            best["min_profit_target_pct"] = round(_spread_exit * 2, 2)

        # ── BOOK 217: COST ESTIMATION — inject into signal for Rust Kelly sizing ──
        try:
            _cost_ticker = ticker_symbols.get(ticker_id, "")
            _cost_price = msg["last"]
            _cost_shares = best.get("shares", 0)
            # Enrich signal with context needed by cost calculator
            _cost_sig = dict(best)
            _cost_sig["bid"] = ind.get("bid", msg.get("bid", _cost_price))
            _cost_sig["ask"] = ind.get("ask", msg.get("ask", _cost_price))
            _cost_sig["spread_pct"] = ind.get("spread_pct", 0.0)
            _cost_sig["vpin"] = ind.get("vpin", 0.0)
            _cost_sig["amihud"] = msg.get("amihud", 0.0)
            _cost_sig["adv_gbp"] = msg.get("adv_gbp", 0)
            _cost_result = _calculate_trade_costs(_cost_sig, _cost_price, _cost_shares, _cost_ticker)
            best["estimated_cost_bps"] = _cost_result["total_cost_bps"]
            best["estimated_cost_usd"] = _cost_result["total_cost_usd"]
            best["cost_decomposition"] = _cost_result
        except Exception:
            pass  # Fail-open: missing cost fields won't block signal

        return best
    return no_signal


def process_apex_snapshot(msg):
    """Process an Apex snapshot message via ApexScout, return a response dict."""
    ticker_id = msg["ticker_id"]
    snapshots = msg.get("snapshots", [])

    if not snapshots:
        return {"type": "no_signal", "ticker_id": ticker_id}

    result = apex_evaluate(snapshots)

    if result is None:
        return {"type": "no_signal", "ticker_id": ticker_id}

    # Apex signals use preliminary Kelly from the scout (full 12-factor in future).
    return {
        "type": "signal",
        "ticker_id": ticker_id,
        "direction": "Long",
        "confidence": result["confidence"],
        "kelly_fraction": result["kelly_fraction"],
        "shares": 0,  # Apex sizing done by Rust side based on kelly_fraction
        "strategy": "ApexScout",
    }


def main():
    """Main loop: read JSON lines from stdin, write responses to stdout."""
    global _last_heartbeat_time
    sys.stderr.write("Python Brain Bridge: started\n")
    sys.stderr.flush()

    # Write initial heartbeat immediately on startup
    _last_heartbeat_time = time.time()
    try:
        _write_heartbeat({"ticks_processed": 0})
    except Exception:
        pass

    # Start data feeds (news, sentiment, Polygon backup) — non-blocking
    try:
        from python_brain.feeds.data_manager import get_data_manager
        _data_mgr = get_data_manager()
        sys.stderr.write("Bridge: DataManager started (news + polygon feeds)\n")
        sys.stderr.flush()
    except Exception as e:
        _data_mgr = None
        sys.stderr.write(f"Bridge: DataManager failed (non-fatal): {e}\n")
        sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"Bridge: JSON decode error: {e}\n")
            sys.stderr.flush()
            response = {"type": "error", "message": str(e)}
            print(json.dumps(response), flush=True)
            continue

        msg_type = msg.get("type", "")

        # Periodic heartbeat for watchdog (every 30s)
        now = time.time()
        if now - _last_heartbeat_time >= _HEARTBEAT_INTERVAL:
            _last_heartbeat_time = now
            try:
                _write_heartbeat({"ticks_processed": sum(_tick_counts.values())})
            except Exception:
                pass
            # Book 58: Run escalation tick alongside heartbeat
            try:
                from python_brain.alerting.escalation_manager import escalation_tick
                esc_actions = escalation_tick()
                for ea in esc_actions:
                    sys.stderr.write(f"ESCALATION: {ea.get('action', '?')} {ea.get('title', '')}\n")
                    sys.stderr.flush()
            except ImportError:
                pass  # Module not deployed yet
            except Exception as esc_err:
                sys.stderr.write(f"Escalation tick error (non-fatal): {esc_err}\n")
                sys.stderr.flush()

        if msg_type == "tick":
            try:
                response = process_tick(msg)
            except Exception as e:
                # FIX 2026-03-11: Return "error" type (not "no_signal") so Rust
                # can distinguish "no trade setup" from "strategy is broken".
                # This prevents silent V1-style rot where a broken strategy
                # looks identical to a quiet market.
                import traceback
                tb = traceback.format_exc()
                sys.stderr.write(f"Bridge: tick processing error: {e}\n{tb}\n")
                sys.stderr.flush()
                response = {
                    "type": "error",
                    "ticker_id": msg.get("ticker_id", -1),
                    "error": f"{type(e).__name__}: {e}",
                }

            # ── BOOK 207: SCHEMA VALIDATION AT OUTPUT BOUNDARY ──
            # Validate every signal before it reaches Rust. Catch NaN, missing fields,
            # wrong types. On validation failure → demote to no_signal (fail-safe).
            if response.get("type") == "signal":
                try:
                    from python_brain.validation.signal_schema import NormalizedSignal
                    _validated = NormalizedSignal.from_dict(response)
                    _validated.validate()
                    response = _validated.to_dict()
                except ValueError as _schema_err:
                    sys.stderr.write(f"SCHEMA_REJECT: tid={response.get('ticker_id')} {_schema_err}\n")
                    sys.stderr.flush()
                    response = {"type": "no_signal", "ticker_id": response.get("ticker_id", -1),
                                "reason": f"schema_validation: {_schema_err}"}
                except ImportError:
                    pass  # Fail-open if module not deployed
                except Exception:
                    pass  # Fail-open on unexpected errors

            print(json.dumps(response), flush=True)

        elif msg_type == "apex_snapshot":
            # P6-I: Apex Scout evaluation for Apex-class tickers (60s OHLCV snapshots).
            try:
                response = process_apex_snapshot(msg)
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                sys.stderr.write(f"Bridge: apex processing error: {e}\n{tb}\n")
                sys.stderr.flush()
                response = {
                    "type": "error",
                    "ticker_id": msg.get("ticker_id", -1),
                    "error": f"{type(e).__name__}: {e}",
                }
            # Book 207: Schema validation for apex signals too
            if response.get("type") == "signal":
                try:
                    from python_brain.validation.signal_schema import NormalizedSignal
                    _v = NormalizedSignal.from_dict(response)
                    _v.validate()
                    response = _v.to_dict()
                except ValueError as _se:
                    sys.stderr.write(f"SCHEMA_REJECT[apex]: tid={response.get('ticker_id')} {_se}\n")
                    sys.stderr.flush()
                    response = {"type": "no_signal", "ticker_id": response.get("ticker_id", -1)}
                except Exception:
                    pass
            print(json.dumps(response), flush=True)

        elif msg_type == "exit":
            # COMPOUNDING FIX: Engine notifies bridge when a position is closed.
            # Updates live Sharpe tracking for the strategy that opened the position.
            try:
                exit_tid = msg.get("ticker_id", -1)
                exit_price = msg.get("exit_price", 0)
                exit_pnl = msg.get("pnl", 0)
                exit_strategy = msg.get("strategy", "")
                exit_direction = msg.get("direction", "long").lower()
                if exit_price > 0 and exit_strategy:
                    _track_strategy_exit(exit_tid, exit_strategy, exit_price)
                    stats = _strategy_live_stats(exit_strategy)
                    sys.stderr.write(
                        f"EXIT_TRACKED: {exit_strategy} tid={exit_tid} pnl={exit_pnl:.4f} "
                        f"live_wr={stats['wr']} live_pf={stats['pf']} live_sharpe={stats['sharpe']}\n"
                    )
                    sys.stderr.flush()

                    # Book 209: Feed outcome to Bayesian source calibration
                    try:
                        from python_brain.aggregation.bayesian_aggregator import record_outcome
                        record_outcome(exit_strategy, exit_direction, exit_pnl > 0)
                    except ImportError:
                        pass  # Module not deployed yet

                    # Book 1: Feed confidence + return for IC tracking
                    entry_conf = _entry_confidences.pop((exit_tid, exit_strategy), 0)
                    try:
                        from python_brain.metrics.fundamental_law import get_tracker
                        if entry_conf > 0:
                            get_tracker().record_signal(exit_strategy, entry_conf / 100.0, exit_pnl)
                    except ImportError:
                        pass

                    # Book 144: Feed outcome to conformal calibrator
                    try:
                        from python_brain.analytics.conformal_calibrator import record_trade_outcome
                        if entry_conf > 0:
                            record_trade_outcome(exit_strategy, entry_conf, exit_pnl > 0)
                    except ImportError:
                        pass

                    # Book 8: Record exit in live metrics
                    try:
                        from python_brain.metrics.live_metrics import get_metrics_collector
                        get_metrics_collector().record_exit(
                            exit_strategy, pnl=exit_pnl,
                            cost=msg.get("commission", 0.0),
                            holding_bars=msg.get("holding_bars", 0),
                        )
                    except ImportError:
                        pass

                    # Book 10: Record trade return for rolling Kelly
                    try:
                        from python_brain.sizing.rolling_kelly import get_rolling_kelly
                        if msg.get("entry_price", 0) > 0:
                            trade_ret = (exit_price - msg["entry_price"]) / msg["entry_price"]
                            get_rolling_kelly().record_trade(exit_strategy, trade_ret)
                    except ImportError:
                        pass

                    # Book 26: Record trade for compounding velocity
                    try:
                        from python_brain.sizing.compounding_velocity import get_velocity_tracker, TradeRecord
                        from datetime import datetime
                        get_velocity_tracker().record_trade(TradeRecord(
                            timestamp=datetime.utcnow(),
                            net_pnl=exit_pnl,
                            gross_pnl=exit_pnl + msg.get("commission", 0.0),
                            cost=msg.get("commission", 0.0),
                            deployed_capital=msg.get("entry_price", 0) * msg.get("shares", 0),
                            duration_seconds=msg.get("holding_bars", 0) * 5.0,
                        ))
                    except ImportError:
                        pass

                    # Book 50: Feed outcome to Thompson signal prioritizer
                    try:
                        from python_brain.regime.signal_prioritizer import get_prioritizer
                        get_prioritizer().update_outcome(exit_strategy, exit_pnl > 0)
                    except ImportError:
                        pass
            except Exception as e:
                sys.stderr.write(f"Bridge: exit tracking error: {e}\n")
                sys.stderr.flush()
            response = {"type": "ack", "ticker_id": msg.get("ticker_id", -1)}

        elif msg_type == "shutdown":
            sys.stderr.write("Python Brain Bridge: shutting down\n")
            sys.stderr.flush()
            # Book 209: Save Bayesian calibration on clean shutdown
            try:
                from python_brain.aggregation.bayesian_aggregator import get_aggregator
                get_aggregator().save()
                sys.stderr.write("BAYES: calibration saved on shutdown\n")
                sys.stderr.flush()
            except Exception:
                pass
            # Book 1: Save fundamental law state on clean shutdown
            try:
                from python_brain.metrics.fundamental_law import get_tracker
                get_tracker().save()
                sys.stderr.write("BOOK1: fundamental law state saved on shutdown\n")
                sys.stderr.flush()
            except Exception:
                pass
            # Book 144: Save conformal calibration on clean shutdown
            try:
                from python_brain.analytics.conformal_calibrator import get_calibrators
                get_calibrators().save()
                sys.stderr.write("BOOK144: conformal calibration saved on shutdown\n")
                sys.stderr.flush()
            except Exception:
                pass
            # Book 8: Save live metrics on shutdown
            try:
                from python_brain.metrics.live_metrics import get_metrics_collector
                get_metrics_collector().save()
                sys.stderr.write("BOOK8: live metrics saved on shutdown\n")
                sys.stderr.flush()
            except Exception:
                pass
            # Book 10: Save rolling Kelly state on shutdown
            try:
                from python_brain.sizing.rolling_kelly import get_rolling_kelly
                get_rolling_kelly().save()
                sys.stderr.write("BOOK10: rolling Kelly saved on shutdown\n")
                sys.stderr.flush()
            except Exception:
                pass
            # Book 50: Save Thompson prioritizer state on shutdown
            try:
                from python_brain.regime.signal_prioritizer import get_prioritizer
                get_prioritizer().save()
                sys.stderr.write("BOOK50: Thompson prioritizer saved on shutdown\n")
                sys.stderr.flush()
            except Exception:
                pass
            break

        else:
            response = {"type": "error", "message": f"unknown type: {msg_type}"}
            print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
