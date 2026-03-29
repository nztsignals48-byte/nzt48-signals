"""Ouroboros v6.0 — Nightly Learning Loop.

Runs at 23:50 ET (04:50 UTC) every weekday. Performs:
  1. Trade analysis (today's paper trades from WAL)
  2. Regime accuracy check
  3. Parameter optimization with guardrails
  4. Alpha decay detection (7d vs 30d rolling)
  5. Daily report generation
  6. Pre-market battle plan for tomorrow

Usage: python3 -m python_brain.ouroboros.nightly_v6

Quarantine rules:
  - NEVER writes to live WAL
  - NEVER influences live decisions in-session
  - Reads ONLY the finished day's journal
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path setup — works both locally and in Docker (/app)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PROJECT_ROOT / "python_brain"))
sys.path.insert(0, str(_PROJECT_ROOT))

from brain.indicators.hurst import classify_regime, estimate_hurst
from brain.indicators.volume_analytics import calculate_rvol
from python_brain.ouroboros.cost_model import costs as _cost_model, estimate_trade_cost
from python_brain.ouroboros.contract_loader import load_lse_symbols

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
REPORTS_DIR = DATA_DIR / "ouroboros_reports"
RECS_FILE = DATA_DIR / "ouroboros_recommendations.json"

PRIMARY_TICKERS = load_lse_symbols()

TICKER_ID_MAP = {sym: i for i, sym in enumerate(PRIMARY_TICKERS)}

# Guardrails
# P2-#35: CRITICAL FIX — KELLY_MAX was 0.20 but engine clamps at config.toml [kelly] clamp_max = 0.05.
# Ouroboros recommendations must stay within the range the engine actually uses.
KELLY_MIN = 0.005  # Minimum Kelly fraction (0.5% — below this, trade is dust)
KELLY_MAX = 0.05   # Maximum Kelly fraction — must match config.toml [kelly] clamp_max
CHANDELIER_ATR_MIN = 1.5
CHANDELIER_ATR_MAX = 4.0
MAX_DRIFT_PCT = 15.0  # No parameter can drift >15% from baseline in one night

ENTRY_TYPES = ["TypeA", "TypeB", "TypeC", "TypeD"]
SESSION_PHASES = ["open_auction", "morning", "midday", "afternoon", "close_auction"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Ouroboros v6] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ouroboros_v6")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class TradeRecord:
    """A single closed trade from the WAL."""
    ticker: str
    ticker_id: int
    pnl: float
    entry_price: float
    exit_price: float
    entry_time_ns: int
    exit_time_ns: int
    entry_type: str
    strategy: str
    regime_at_entry: str
    rung_achieved: int
    confidence: float
    exchange: str = ""  # AUDIT-FIX: track per-exchange performance
    # N1a: Cost-aware fields from enriched PositionClosed (N0e + N2b)
    gross_pnl: float = 0.0
    total_commission: float = 0.0
    spread_at_entry_pct: float = 0.0
    spread_at_exit_pct: float = 0.0
    mae: float = 0.0
    mfe: float = 0.0
    hold_time_mins: int = 0
    entry_session_phase: str = ""
    vwap_dist_at_entry_pct: float = 0.0
    atr_pct_at_entry: float = 0.01
    qty: int = 1
    trade_class: str = ""  # Assigned by trade taxonomy classifier
    # S5: Cost-adjusted P&L (sim_pnl minus estimated real costs)
    cost_adjusted_pnl: float = 0.0
    estimated_cost: float = 0.0


@dataclass
class DailyMetrics:
    """Aggregated metrics for one day."""
    date: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_rung: float = 0.0
    avg_entry_delay_ms: float = 0.0
    per_ticker: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    per_entry_type: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    per_session: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    per_exchange: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    best_trade: Optional[Dict[str, Any]] = None
    worst_trade: Optional[Dict[str, Any]] = None


@dataclass
class RegimeAccuracy:
    """Regime prediction accuracy for the day."""
    total_predictions: int = 0
    correct: int = 0
    accuracy_pct: float = 0.0
    transitions: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class AlphaDecaySignal:
    """Alpha decay warning for a strategy or ticker."""
    entity: str
    metric_7d: float
    metric_30d: float
    decay_pct: float
    severity: str  # "warning" or "critical"


# ---------------------------------------------------------------------------
# 1. Trade Analysis
# ---------------------------------------------------------------------------
def load_todays_trades(date_str: str) -> List[TradeRecord]:
    """Load today's trades from ALL WAL ndjson files including archives.

    The Rust engine rotates current.ndjson to archive/wal_<epoch>.ndjson on
    every restart. With multiple restarts per day, trades scatter across
    archive files. We must scan ALL archive files + current.ndjson to find
    every PositionClosed event from today.
    """
    trades: List[TradeRecord] = []

    # Build candidate list: current + date-stamped + ALL archive files
    wal_candidates = [
        WAL_DIR / "current.ndjson",
        WAL_DIR / f"{date_str}.ndjson",
        WAL_DIR / f"wal_{date_str}.ndjson",
    ]
    # Scan archive directory for all .ndjson files
    archive_dir = WAL_DIR / "archive"
    if archive_dir.exists():
        for f in sorted(archive_dir.glob("*.ndjson")):
            if f not in wal_candidates:
                wal_candidates.append(f)

    for wal_path in wal_candidates:
        if not wal_path.exists():
            continue
        log.info("Reading WAL: %s", wal_path)
        try:
            with open(wal_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = event.get("payload", {})
                    if "PositionClosed" in payload:
                        pc = payload["PositionClosed"]
                        # FIXED (Sprint 3I): Date filter — only include trades that exited TODAY.
                        # Without this, trades from previous days leak into daily analytics.
                        exit_ns = pc.get("exit_time_ns", 0)
                        if exit_ns > 0:
                            from datetime import datetime, timezone
                            exit_date = datetime.fromtimestamp(exit_ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%d")
                            if exit_date != date_str:
                                continue  # Skip trades that closed on a different day
                        trades.append(TradeRecord(
                            ticker=pc.get("symbol", f"TID_{pc.get('ticker_id', '?')}"),
                            ticker_id=pc.get("ticker_id", -1),
                            pnl=pc.get("final_pnl", 0.0),
                            entry_price=pc.get("entry_price", 0.0),
                            exit_price=pc.get("exit_price", 0.0),
                            entry_time_ns=pc.get("entry_time_ns", 0),
                            exit_time_ns=pc.get("exit_time_ns", 0),
                            entry_type=pc.get("entry_type", "Unclassified"),
                            # Read strategy/regime/confidence/rung from enriched WAL fields
                            strategy=pc.get("strategy", "Unclassified"),
                            regime_at_entry=pc.get("regime_at_entry", pc.get("regime", "unknown")),
                            rung_achieved=pc.get("highest_rung", 0),
                            confidence=pc.get("confidence", 0.0),
                            exchange=pc.get("exchange", ""),
                            # N1a: Cost-aware fields from N0e + N2b enrichment
                            gross_pnl=pc.get("gross_pnl", 0.0),
                            total_commission=pc.get("total_commission", 0.0),
                            spread_at_entry_pct=pc.get("spread_at_entry_pct", 0.0),
                            spread_at_exit_pct=pc.get("spread_at_exit_pct", 0.0),
                            mae=pc.get("mae", 0.0),
                            mfe=pc.get("mfe", 0.0),
                            hold_time_mins=pc.get("hold_time_mins", 0),
                            entry_session_phase=pc.get("entry_session_phase", ""),
                            vwap_dist_at_entry_pct=pc.get("vwap_dist_at_entry_pct", 0.0),
                            atr_pct_at_entry=pc.get("atr_pct_at_entry", 0.01),
                            qty=pc.get("qty", 1),
                        ))
        except Exception as e:
            log.warning("Error reading %s: %s", wal_path, e)

    log.info("Loaded %d trades for %s", len(trades), date_str)
    return trades


def analyze_trades(trades: List[TradeRecord], date_str: str) -> DailyMetrics:
    """Calculate comprehensive daily metrics from trade records."""
    metrics = DailyMetrics(date=date_str)
    if not trades:
        return metrics

    metrics.total_trades = len(trades)
    metrics.wins = sum(1 for t in trades if t.pnl > 0)
    # FIXED (Sprint 5, 3K): Breakeven trades (pnl=0) no longer counted as losses.
    # pnl=0 typically = spread-victim breakeven, not a signal failure.
    metrics.losses = sum(1 for t in trades if t.pnl < 0)
    metrics.breakeven = sum(1 for t in trades if t.pnl == 0.0)
    metrics.total_pnl = sum(t.pnl for t in trades)
    metrics.win_rate = metrics.wins / metrics.total_trades if metrics.total_trades > 0 else 0.0

    gross_wins = sum(t.pnl for t in trades if t.pnl > 0)
    gross_losses = abs(sum(t.pnl for t in trades if t.pnl < 0))
    metrics.profit_factor = gross_wins / max(gross_losses, 1e-9)
    metrics.avg_rung = sum(t.rung_achieved for t in trades) / metrics.total_trades

    # Entry delay (ns to ms)
    delays = []
    for t in trades:
        if t.entry_time_ns > 0:
            delays.append(t.entry_time_ns / 1e6)  # Placeholder: actual delay from signal->fill
    metrics.avg_entry_delay_ms = sum(delays) / len(delays) if delays else 0.0

    # Per-ticker metrics
    by_ticker: Dict[str, List[TradeRecord]] = defaultdict(list)
    for t in trades:
        by_ticker[t.ticker].append(t)

    for ticker, ticker_trades in by_ticker.items():
        n = len(ticker_trades)
        wins = sum(1 for t in ticker_trades if t.pnl > 0)
        total_pnl = sum(t.pnl for t in ticker_trades)
        ticker_gross_wins = sum(t.pnl for t in ticker_trades if t.pnl > 0)
        ticker_gross_losses = abs(sum(t.pnl for t in ticker_trades if t.pnl < 0))
        ticker_pf = ticker_gross_wins / max(ticker_gross_losses, 1e-9)
        avg_spread = sum(t.spread_at_entry_pct for t in ticker_trades) / n if n > 0 else 0.0
        avg_gross = sum(abs(t.gross_pnl) for t in ticker_trades) / n if n > 0 else 0.0
        metrics.per_ticker[ticker] = {
            "trades": n,
            "wins": wins,
            "win_rate": wins / n if n > 0 else 0.0,
            "total_pnl": total_pnl,
            "avg_rung": sum(t.rung_achieved for t in ticker_trades) / n,
            "profit_factor": ticker_pf,
            "avg_spread_cost": avg_spread,
            "avg_gross_pnl": avg_gross,
        }

    # Per-entry-type metrics
    by_type: Dict[str, List[TradeRecord]] = defaultdict(list)
    for t in trades:
        by_type[t.entry_type].append(t)

    for etype, type_trades in by_type.items():
        n = len(type_trades)
        wins = sum(1 for t in type_trades if t.pnl > 0)
        total_pnl = sum(t.pnl for t in type_trades)
        metrics.per_entry_type[etype] = {
            "trades": n,
            "wins": wins,
            "win_rate": wins / n if n > 0 else 0.0,
            "total_pnl": total_pnl,
        }

    # Per-session metrics (approximate from entry timestamps)
    for t in trades:
        phase = _classify_session_phase(t.entry_time_ns)
        if phase not in metrics.per_session:
            metrics.per_session[phase] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
        metrics.per_session[phase]["trades"] += 1
        if t.pnl > 0:
            metrics.per_session[phase]["wins"] += 1
        metrics.per_session[phase]["total_pnl"] += t.pnl

    for phase_data in metrics.per_session.values():
        n = phase_data["trades"]
        phase_data["win_rate"] = phase_data["wins"] / n if n > 0 else 0.0

    # Per-exchange metrics (from WAL exchange field)
    for t in trades:
        exchange = getattr(t, "exchange", "") or "unknown"
        if exchange not in metrics.per_exchange:
            metrics.per_exchange[exchange] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
        metrics.per_exchange[exchange]["trades"] += 1
        if t.pnl > 0:
            metrics.per_exchange[exchange]["wins"] += 1
        metrics.per_exchange[exchange]["total_pnl"] += t.pnl

    for exch_data in metrics.per_exchange.values():
        n = exch_data["trades"]
        exch_data["win_rate"] = exch_data["wins"] / n if n > 0 else 0.0

    # Best/worst trade
    if trades:
        best = max(trades, key=lambda t: t.pnl)
        worst = min(trades, key=lambda t: t.pnl)
        metrics.best_trade = {"ticker": best.ticker, "pnl": best.pnl, "rung": best.rung_achieved}
        metrics.worst_trade = {"ticker": worst.ticker, "pnl": worst.pnl, "rung": worst.rung_achieved}

    return metrics


def _classify_session_phase(entry_time_ns: int) -> str:
    """Classify entry timestamp into LSE session phase."""
    if entry_time_ns == 0:
        return "unknown"
    try:
        dt = datetime.fromtimestamp(entry_time_ns / 1e9, tz=timezone.utc)
        hour = dt.hour  # UTC — approximate for LSE
        if hour < 8:
            return "open_auction"
        elif hour < 10:
            return "morning"
        elif hour < 13:
            return "midday"
        elif hour < 16:
            return "afternoon"
        else:
            return "close_auction"
    except (OSError, ValueError):
        return "unknown"


# ---------------------------------------------------------------------------
# 2. Regime Accuracy Check
# ---------------------------------------------------------------------------
def check_regime_accuracy(trades: List[TradeRecord], prices_by_ticker: Dict[str, List[float]]) -> RegimeAccuracy:
    """Compare predicted regime vs actual regime from price action."""
    result = RegimeAccuracy()

    for t in trades:
        if t.regime_at_entry == "unknown":
            continue
        result.total_predictions += 1

        # Determine actual regime from price action
        prices = prices_by_ticker.get(t.ticker, [])
        if len(prices) >= 22:
            actual_hurst = estimate_hurst(prices, max_lag=20)
            actual_regime = classify_regime(actual_hurst)
        else:
            actual_regime = "random"

        # Map predicted regime to hurst-style labels
        predicted_mapped = _map_regime_label(t.regime_at_entry)
        if predicted_mapped == actual_regime:
            result.correct += 1

        result.transitions.append({
            "ticker": t.ticker,
            "predicted": t.regime_at_entry,
            "actual": actual_regime,
            "match": predicted_mapped == actual_regime,
        })

    if result.total_predictions > 0:
        result.accuracy_pct = (result.correct / result.total_predictions) * 100.0

    return result


def _map_regime_label(label: str) -> str:
    """Map WAL regime labels to hurst-style classification."""
    label_lower = label.lower()
    if "trend" in label_lower or "bull" in label_lower:
        return "trending"
    elif "revert" in label_lower or "mean" in label_lower or "bear" in label_lower:
        return "mean_reverting"
    return "random"


# ---------------------------------------------------------------------------
# 3. Parameter Optimization (with guardrails)
# ---------------------------------------------------------------------------
def optimize_parameters(metrics: DailyMetrics, *, mem=None) -> Dict[str, Any]:
    """Auto-tune parameters within guardrails based on today's performance + cumulative memory."""
    recs: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "date": metrics.date,
        "adjustments": [],
        "kelly_fraction": None,
        "chandelier_atr_mult": None,
    }

    # Load current parameters
    current = _load_current_params()
    current_kelly = current.get("kelly_fraction", 0.20)
    current_chandelier = current.get("chandelier_atr_mult", 3.0)

    new_kelly = current_kelly
    new_chandelier = current_chandelier

    # --- CUMULATIVE MEMORY FEEDBACK (ISS-003/ISS-017 fix) ---
    # Use all-time stats from persistent memory, not just today's metrics.
    # This closes the learning loop: memory accumulates → parameters adapt.
    effective_trades = metrics.total_trades
    effective_wr = metrics.win_rate
    effective_rung = metrics.avg_rung

    if mem is not None and hasattr(mem, 'total_trades') and mem.total_trades >= 10:
        # Blend today's metrics with all-time memory (70% cumulative, 30% today)
        # This prevents single-day noise from swinging parameters too much.
        all_time_wr = getattr(mem, 'all_time_win_rate', 0.5)
        if metrics.total_trades >= 3:
            effective_wr = 0.3 * metrics.win_rate + 0.7 * all_time_wr
        else:
            effective_wr = all_time_wr
        effective_trades = mem.total_trades + metrics.total_trades
        log.info("Memory blend: effective_wr=%.1f%% (today=%.1f%%, alltime=%.1f%%)",
                 effective_wr * 100, metrics.win_rate * 100, all_time_wr * 100)

        # Apply lessons from persistent memory
        lessons = getattr(mem, 'lessons', [])
        for lesson in lessons:
            recs["adjustments"].append(f"Memory lesson: {lesson.get('reason', 'unknown')}")

        # Per-regime learning: build regime_scales from cumulative stats
        # Q-068 FIX: Require minimum 50 trades per regime for statistical significance.
        # Below 50 trades, regime scale is locked at 1.0 (neutral).
        MIN_REGIME_TRADES = 50
        regime_stats = getattr(mem, 'regime_stats', {})
        regime_scales = {}
        for regime_name, rstats in regime_stats.items():
            rtrades = rstats.get('total_trades', 0)
            if rtrades >= MIN_REGIME_TRADES:
                rwr = rstats.get('win_rate', 0.5)
                # Scale: 0.5 at 20% WR → 1.0 at 50% WR → 1.5 at 80% WR
                scale = 0.5 + (rwr - 0.2) * (1.0 / 0.6)
                regime_scales[regime_name] = max(0.4, min(1.5, scale))
            elif rtrades >= 5:
                # Insufficient data: log but use neutral scale
                regime_scales[regime_name] = 1.0
                rwr = rstats.get('win_rate', 0.5)
                recs["adjustments"].append(
                    f"Regime '{regime_name}' scale=1.00 (WR={rwr:.0%} over {rtrades} trades, <{MIN_REGIME_TRADES} min)"
                )
        if regime_scales:
            recs["regime_scales"] = regime_scales

    # Kelly adjustment based on blended win rate
    if effective_trades >= 3:
        if effective_wr > 0.50:
            new_kelly = min(current_kelly * 1.02, KELLY_MAX)
            recs["adjustments"].append(
                f"Kelly +2% ({current_kelly:.3f} -> {new_kelly:.3f}): blended WR={effective_wr:.1%} > 50%"
            )
        elif effective_wr < 0.30:
            new_kelly = max(current_kelly * 0.95, KELLY_MIN)
            recs["adjustments"].append(
                f"Kelly -5% ({current_kelly:.3f} -> {new_kelly:.3f}): blended WR={effective_wr:.1%} < 30%"
            )

    # Chandelier adjustment based on avg rung
    if metrics.total_trades >= 3:
        if metrics.avg_rung < 2.0:
            new_chandelier = min(current_chandelier + 0.05, CHANDELIER_ATR_MAX)
            recs["adjustments"].append(
                f"Chandelier ATR +0.05 ({current_chandelier:.2f} -> {new_chandelier:.2f}): "
                f"avg_rung={metrics.avg_rung:.1f} < 2.0 (widen to let trades breathe)"
            )
        elif metrics.avg_rung > 3.5:
            new_chandelier = max(current_chandelier - 0.05, CHANDELIER_ATR_MIN)
            recs["adjustments"].append(
                f"Chandelier ATR -0.05 ({current_chandelier:.2f} -> {new_chandelier:.2f}): "
                f"avg_rung={metrics.avg_rung:.1f} > 3.5 (tighten to capture profit)"
            )

    # GUARDRAIL: Max drift 15% from baseline in one night
    baseline_kelly = current.get("baseline_kelly", 0.20)
    baseline_chandelier = current.get("baseline_chandelier", 3.0)
    new_kelly = _clamp_drift(new_kelly, baseline_kelly, MAX_DRIFT_PCT / 100.0)
    new_chandelier = _clamp_drift(new_chandelier, baseline_chandelier, MAX_DRIFT_PCT / 100.0)

    # N1a: Cost-aware learning — tighten Kelly when cost drag is excessive.
    # If spread victims + noise exits > 40% of trades, reduce Kelly to cut churning.
    # Q-051: cost victim threshold derived from CostModel (1.5x round-trip commission).
    _cost_victim_threshold = _cost_model.ibkr_commission_gbp * 2 * 1.5  # ~5.10 GBP
    if metrics.total_trades >= 3:
        # Count cost-impaired trades from per_ticker metrics (proxy until taxonomy is wired)
        # When taxonomy data is available via trade_class, use that instead.
        loser_count = metrics.losses
        if loser_count > 0 and metrics.total_trades > 0:
            loss_rate = loser_count / metrics.total_trades
            avg_loss_size = abs(sum(
                d.get("total_pnl", 0) for d in metrics.per_ticker.values() if d.get("total_pnl", 0) < 0
            )) / max(loser_count, 1)
            # If average loss is tiny (< cost_victim_threshold), likely spread victims / noise exits
            if avg_loss_size < _cost_victim_threshold and loss_rate > 0.40:
                cost_penalty = 0.97  # 3% Kelly reduction per day of high cost drag
                new_kelly = max(new_kelly * cost_penalty, KELLY_MIN)
                recs["adjustments"].append(
                    f"N1a COST PENALTY: Kelly *0.97 -> {new_kelly:.3f} "
                    f"(avg_loss=GBP {avg_loss_size:.2f} < {_cost_victim_threshold:.1f}, loss_rate={loss_rate:.0%})"
                )

    recs["kelly_fraction"] = new_kelly
    recs["chandelier_atr_mult"] = new_chandelier
    recs["guardrails"] = {
        "kelly_range": [KELLY_MIN, KELLY_MAX],
        "chandelier_range": [CHANDELIER_ATR_MIN, CHANDELIER_ATR_MAX],
        "max_drift_pct": MAX_DRIFT_PCT,
    }

    return recs


def _clamp_drift(new_val: float, baseline: float, max_drift_frac: float) -> float:
    """Ensure new value doesn't drift more than max_drift_frac from baseline."""
    if baseline <= 0:
        return new_val
    lower = baseline * (1.0 - max_drift_frac)
    upper = baseline * (1.0 + max_drift_frac)
    return max(lower, min(upper, new_val))


def _load_current_params() -> Dict[str, Any]:
    """Load current parameter recommendations or return defaults."""
    if RECS_FILE.exists():
        try:
            with open(RECS_FILE) as f:
                data = json.load(f)
            return {
                "kelly_fraction": data.get("kelly_fraction", 0.20),
                "chandelier_atr_mult": data.get("chandelier_atr_mult", 3.0),
                "baseline_kelly": data.get("baseline_kelly", 0.20),
                "baseline_chandelier": data.get("baseline_chandelier", 3.0),
            }
        except (json.JSONDecodeError, KeyError):
            pass
    return {
        "kelly_fraction": 0.20,
        "chandelier_atr_mult": 3.0,
        "baseline_kelly": 0.20,
        "baseline_chandelier": 3.0,
    }


BACKFILL_FEEDBACK_FILE = DATA_DIR / "backfill_feedback.json"
BACKFILL_FEEDBACK_MAX_AGE_HOURS = 36  # Stale after 36 hours


def _load_backfill_feedback() -> Dict[str, Any]:
    """Load backfill simulator feedback from data/backfill_feedback.json.

    Returns the feedback dict if the file exists and is fresh (written within
    the last 36 hours). Returns an empty dict if the file is missing, stale,
    or corrupted.

    QUARANTINE: This function only reads data. It never writes to WAL, config,
    or live trading state.

    Returns:
        Dict with keys: backfill_date, simulated_trades_count, simulated_win_rate,
        simulated_avg_return, strategy_confidence_delta, recommended_parameter_changes.
        Empty dict if unavailable or stale.
    """
    if not BACKFILL_FEEDBACK_FILE.exists():
        log.info("Backfill feedback file not found: %s", BACKFILL_FEEDBACK_FILE)
        return {}

    try:
        # Check staleness via file mtime
        mtime = BACKFILL_FEEDBACK_FILE.stat().st_mtime
        age_hours = (time.time() - mtime) / 3600.0
        if age_hours > BACKFILL_FEEDBACK_MAX_AGE_HOURS:
            log.info(
                "Backfill feedback is stale (%.1f hours old, max %d). Ignoring.",
                age_hours, BACKFILL_FEEDBACK_MAX_AGE_HOURS,
            )
            return {}

        with open(BACKFILL_FEEDBACK_FILE) as f:
            data = json.load(f)

        # Basic schema validation
        required_keys = {"backfill_date", "simulated_trades_count", "simulated_win_rate"}
        if not required_keys.issubset(data.keys()):
            log.warning(
                "Backfill feedback missing required keys: %s",
                required_keys - data.keys(),
            )
            return {}

        log.info(
            "Backfill feedback loaded: date=%s trades=%d wr=%.1f%% avg_ret=%.2f%% (%.1fh old)",
            data.get("backfill_date", "?"),
            data.get("simulated_trades_count", 0),
            data.get("simulated_win_rate", 0) * 100,
            data.get("simulated_avg_return", 0),
            age_hours,
        )
        return data

    except (json.JSONDecodeError, IOError, OSError) as e:
        log.warning("Failed to load backfill feedback: %s", e)
        return {}


# ---------------------------------------------------------------------------
# 4. Alpha Decay Detection
# ---------------------------------------------------------------------------
def detect_alpha_decay(reports_dir: Path, current_metrics: DailyMetrics) -> List[AlphaDecaySignal]:
    """Compare rolling 7-day vs 30-day performance to detect alpha decay."""
    signals: List[AlphaDecaySignal] = []

    # Load historical reports
    history = _load_historical_metrics(reports_dir, days=30)
    if len(history) < 7:
        log.info("Insufficient history (%d days) for alpha decay detection, need 7+", len(history))
        return signals

    recent_7 = history[-7:]
    all_30 = history

    # Overall win rate decay
    wr_7 = _avg_metric(recent_7, "win_rate")
    wr_30 = _avg_metric(all_30, "win_rate")
    if wr_30 > 0 and wr_7 < wr_30 * 0.75:
        decay_pct = ((wr_30 - wr_7) / max(wr_30, 1e-9)) * 100
        severity = "critical" if decay_pct > 30 else "warning"
        signals.append(AlphaDecaySignal(
            entity="overall",
            metric_7d=wr_7,
            metric_30d=wr_30,
            decay_pct=decay_pct,
            severity=severity,
        ))

    # Overall profit factor decay
    pf_7 = _avg_metric(recent_7, "profit_factor")
    pf_30 = _avg_metric(all_30, "profit_factor")
    if pf_30 > 0 and pf_7 < pf_30 * 0.70:
        decay_pct = ((pf_30 - pf_7) / max(pf_30, 1e-9)) * 100
        severity = "critical" if decay_pct > 40 else "warning"
        signals.append(AlphaDecaySignal(
            entity="overall_PF",
            metric_7d=pf_7,
            metric_30d=pf_30,
            decay_pct=decay_pct,
            severity=severity,
        ))

    for signal in signals:
        log.warning(
            "ALPHA DECAY %s: %s — 7d=%.3f vs 30d=%.3f (decay=%.1f%%)",
            signal.severity.upper(), signal.entity,
            signal.metric_7d, signal.metric_30d, signal.decay_pct,
        )

    return signals


def _load_historical_metrics(reports_dir: Path, days: int) -> List[Dict[str, Any]]:
    """Load metrics from historical daily reports (JSON sidecar files)."""
    history = []
    if not reports_dir.exists():
        return history

    json_files = sorted(reports_dir.glob("*_metrics.json"))
    for jf in json_files[-days:]:
        try:
            with open(jf) as f:
                data = json.load(f)
            history.append(data)
        except (json.JSONDecodeError, KeyError):
            continue
    return history


def _load_wal_returns_for_hmm(lookback_days: int = 90) -> np.ndarray:
    """Extract daily log returns from WAL files for HMM regime detection.

    Reads PositionClosed events from the last `lookback_days` WAL files,
    computes daily aggregate log returns, and returns as numpy array.
    Falls back to simple daily P&L-based returns if insufficient data.
    """
    import numpy as _np
    from datetime import datetime as _dt, timedelta as _td

    returns = []
    today = _dt.now(timezone.utc).date()

    for d in range(lookback_days):
        date = today - _td(days=d)
        date_str = date.strftime("%Y-%m-%d")
        wal_path = WAL_DIR / f"{date_str}.ndjson"
        if not wal_path.exists():
            continue

        day_pnl = 0.0
        day_equity = 10000.0  # Fallback if no equity snapshot
        try:
            with open(wal_path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        evt = json.loads(line)
                        et = evt.get("event_type", "")
                        if et == "PositionClosed":
                            day_pnl += evt.get("realized_pnl", 0.0)
                        elif et == "StateSnapshot":
                            day_equity = max(evt.get("equity", 10000.0), 1000.0)
                    except (json.JSONDecodeError, KeyError):
                        continue
        except IOError:
            continue

        if day_equity > 0:
            daily_return = day_pnl / day_equity
            returns.append(daily_return)

    if not returns:
        return _np.array([], dtype=_np.float64)

    # Reverse to chronological order (we iterated newest→oldest)
    returns.reverse()
    return _np.array(returns, dtype=_np.float64)


def _avg_metric(records: List[Dict[str, Any]], key: str) -> float:
    """Average a metric across historical records."""
    vals = [r.get(key, 0.0) for r in records if key in r]
    return sum(vals) / len(vals) if vals else 0.0


# ---------------------------------------------------------------------------
# 4b. Ticker Scoreboard — Promotion / Demotion / Kill
# ---------------------------------------------------------------------------
def generate_ticker_scoreboard(
    metrics: DailyMetrics,
    mem: Any,
) -> Dict[str, Any]:
    """Compute composite health score for each ticker and classify as PROMOTE/HOLD/DEMOTE/KILL.

    Blends persistent memory (all-time, 70%) with today's stats (30%) to produce
    a 0-100 composite score per ticker. Tickers without cumulative history get
    a 0.7 penalty multiplier on their score.

    Components (weights sum to 1.0):
      - Win rate        (0.3): WR% mapped to 0-100
      - Profit factor   (0.2): PF capped at 3.0 → 100
      - Avg rung        (0.2): avg_rung / 5 * 100
      - Sample size     (0.1): min(n_trades / 10, 1) * 100
      - Spread health   (0.2): 100 - (spread_cost / gross_pnl * 100), floor 0

    Returns dict with: scoreboard, promotes, demotes, kills, holds.
    """
    WEIGHTS = {"wr": 0.3, "pf": 0.2, "rung": 0.2, "sample": 0.1, "spread": 0.2}
    NO_HISTORY_PENALTY = 0.7

    # Gather the full ticker universe: everything in PRIMARY_TICKERS + anything traded today
    universe = set(PRIMARY_TICKERS)
    universe.update(metrics.per_ticker.keys())
    if mem is not None:
        ts_dict = getattr(mem, "ticker_stats", {})
        universe.update(ts_dict.keys())

    scoreboard = []
    promotes, demotes, kills, holds = [], [], [], []

    for symbol in sorted(universe):
        today = metrics.per_ticker.get(symbol, {})
        cumul = {}
        has_history = False
        if mem is not None:
            ts_dict = getattr(mem, "ticker_stats", {})
            cumul = ts_dict.get(symbol, {})
            if cumul.get("total_trades", 0) > 0:
                has_history = True

        # --- Blend today (30%) + cumulative (70%) for each raw stat ---
        def _blend(today_val: float, cumul_val: float, has_cumul: bool) -> float:
            if has_cumul:
                return 0.3 * today_val + 0.7 * cumul_val
            return today_val

        today_wr = today.get("win_rate", 0.0)
        cumul_wr = cumul.get("win_rate", 0.0)
        eff_wr = _blend(today_wr, cumul_wr, has_history)

        today_pf = today.get("profit_factor", 0.0)
        # Derive cumulative PF from cumul stats (wins PnL / losses PnL)
        cumul_total_pnl = cumul.get("total_pnl", 0.0)
        cumul_wins_n = cumul.get("wins", 0)
        cumul_losses_n = cumul.get("losses", 0)
        cumul_avg_pnl = cumul.get("avg_pnl", 0.0)
        # Approximate cumulative PF: total_wins_pnl / total_losses_pnl
        # We don't have separate win/loss PnL in TickerStats, so approximate via WR + avg_pnl
        # PF ~ (wins * avg_win) / (losses * avg_loss). With only avg_pnl, use WR-based proxy:
        # If WR > 0 and total_pnl > 0, PF > 1; approximate as total_pnl_positive / total_pnl_negative
        cumul_pf = 0.0
        if cumul_total_pnl > 0 and cumul_losses_n > 0:
            # Rough: positive total means wins outweigh losses; scale PF from WR
            cumul_pf = min(3.0, 1.0 + (cumul_wr - 0.5) * 4.0) if cumul_wr > 0.5 else cumul_wr * 2.0
            cumul_pf = max(0.0, cumul_pf)
        elif cumul_wins_n > 0 and cumul_losses_n == 0:
            cumul_pf = 3.0  # All winners
        eff_pf = _blend(today_pf, cumul_pf, has_history)

        today_rung = today.get("avg_rung", 0.0)
        cumul_rung = cumul.get("avg_rung", 0.0)
        eff_rung = _blend(today_rung, cumul_rung, has_history)

        today_n = today.get("trades", 0)
        cumul_n = cumul.get("total_trades", 0)
        eff_n = today_n + cumul_n  # Total sample size (not blended — additive)

        today_spread = today.get("avg_spread_cost", 0.0)
        today_gross = today.get("avg_gross_pnl", 0.0)

        # --- Component scores (each 0-100) ---
        c_wr = max(0.0, min(100.0, eff_wr * 100.0))
        c_pf = max(0.0, min(100.0, (eff_pf / 3.0) * 100.0))
        c_rung = max(0.0, min(100.0, (eff_rung / 5.0) * 100.0))
        c_sample = max(0.0, min(100.0, min(eff_n / 10.0, 1.0) * 100.0))

        # Spread health: 100 - (avg_spread / avg_gross * 100), floor 0
        # Use today's values only (cumulative doesn't store spread data)
        if today_gross > 0 and today_spread > 0:
            spread_ratio_pct = (today_spread / today_gross) * 100.0
            c_spread = max(0.0, min(100.0, 100.0 - spread_ratio_pct))
        elif today_n == 0 and has_history:
            # No trades today, give neutral spread score
            c_spread = 50.0
        else:
            # No spread data — give neutral score if has trades, else 0
            c_spread = 50.0 if (today_n > 0 or has_history) else 0.0

        # --- Composite score ---
        composite = (
            WEIGHTS["wr"] * c_wr
            + WEIGHTS["pf"] * c_pf
            + WEIGHTS["rung"] * c_rung
            + WEIGHTS["sample"] * c_sample
            + WEIGHTS["spread"] * c_spread
        )

        # Penalty for no cumulative history
        if not has_history:
            composite *= NO_HISTORY_PENALTY

        composite = max(0.0, min(100.0, composite))

        # --- Classification ---
        if composite >= 70:
            classification = "PROMOTE"
            promotes.append(symbol)
        elif composite >= 40:
            classification = "HOLD"
            holds.append(symbol)
        elif composite >= 20:
            classification = "DEMOTE"
            demotes.append(symbol)
        else:
            classification = "KILL"
            kills.append(symbol)

        scoreboard.append({
            "symbol": symbol,
            "score": round(composite, 1),
            "classification": classification,
            "components": {
                "wr": round(c_wr, 1),
                "pf": round(c_pf, 1),
                "rung": round(c_rung, 1),
                "sample": round(c_sample, 1),
                "spread": round(c_spread, 1),
            },
        })

    # Sort by score descending
    scoreboard.sort(key=lambda x: -x["score"])

    # Log formatted table
    log.info("=" * 72)
    log.info("TICKER SCOREBOARD — Promotion / Demotion / Kill")
    log.info("-" * 72)
    log.info("%-12s %5s  %6s  %5s %5s %5s %5s %5s", "TICKER", "SCORE", "ACTION", "WR", "PF", "RUNG", "SMPL", "SPRD")
    log.info("-" * 72)
    for entry in scoreboard:
        c = entry["components"]
        log.info(
            "%-12s %5.1f  %-6s  %5.1f %5.1f %5.1f %5.1f %5.1f",
            entry["symbol"], entry["score"], entry["classification"],
            c["wr"], c["pf"], c["rung"], c["sample"], c["spread"],
        )
    log.info("-" * 72)
    log.info("PROMOTE: %s", ", ".join(promotes) if promotes else "(none)")
    log.info("HOLD:    %s", ", ".join(holds) if holds else "(none)")
    log.info("DEMOTE:  %s", ", ".join(demotes) if demotes else "(none)")
    log.info("KILL:    %s", ", ".join(kills) if kills else "(none)")
    log.info("=" * 72)

    return {
        "scoreboard": scoreboard,
        "promotes": promotes,
        "demotes": demotes,
        "kills": kills,
        "holds": holds,
    }


# ---------------------------------------------------------------------------
# 5. Daily Report
# ---------------------------------------------------------------------------
def generate_daily_report(
    date_str: str,
    metrics: DailyMetrics,
    regime_acc: RegimeAccuracy,
    recommendations: Dict[str, Any],
    decay_signals: List[AlphaDecaySignal],
) -> Path:
    """Write human-readable daily summary report."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{date_str}.txt"

    lines = [
        f"{'=' * 60}",
        f"  OUROBOROS v6.0 DAILY REPORT — {date_str}",
        f"{'=' * 60}",
        "",
        "1. TRADE SUMMARY",
        f"   Total trades:    {metrics.total_trades}",
        f"   Wins:            {metrics.wins}",
        f"   Losses:          {metrics.losses}",
        f"   Win rate:        {metrics.win_rate:.1%}",
        f"   Profit factor:   {metrics.profit_factor:.2f}",
        f"   Total PnL:       GBP {metrics.total_pnl:+.2f}",
        f"   Avg rung:        {metrics.avg_rung:.1f}",
        "",
    ]

    if metrics.best_trade:
        lines.append(f"   Best trade:  {metrics.best_trade['ticker']} "
                      f"GBP {metrics.best_trade['pnl']:+.2f} (rung {metrics.best_trade['rung']})")
    if metrics.worst_trade:
        lines.append(f"   Worst trade: {metrics.worst_trade['ticker']} "
                      f"GBP {metrics.worst_trade['pnl']:+.2f} (rung {metrics.worst_trade['rung']})")

    lines += ["", "2. PER-TICKER PERFORMANCE"]
    for ticker, data in sorted(metrics.per_ticker.items(), key=lambda x: -x[1]["total_pnl"]):
        lines.append(
            f"   {ticker:12s}  trades={data['trades']:2d}  "
            f"WR={data['win_rate']:.0%}  PnL={data['total_pnl']:+7.2f}  "
            f"avg_rung={data['avg_rung']:.1f}"
        )

    # Scoreboard section (between per-ticker and per-entry-type)
    sb = recommendations.get("ticker_scoreboard", {})
    sb_entries = sb.get("scoreboard", [])
    if sb_entries:
        lines += ["", "3. TICKER SCOREBOARD (Promote / Demote / Kill)"]
        lines.append(f"   {'TICKER':12s} {'SCORE':>5s}  {'ACTION':6s}  {'WR':>5s} {'PF':>5s} {'RUNG':>5s} {'SMPL':>5s} {'SPRD':>5s}")
        lines.append(f"   {'-' * 56}")
        for entry in sb_entries:
            c = entry["components"]
            lines.append(
                f"   {entry['symbol']:12s} {entry['score']:5.1f}  {entry['classification']:6s}  "
                f"{c['wr']:5.1f} {c['pf']:5.1f} {c['rung']:5.1f} {c['sample']:5.1f} {c['spread']:5.1f}"
            )
        promotes = sb.get("promotes", [])
        demotes = sb.get("demotes", [])
        kills = sb.get("kills", [])
        if promotes:
            lines.append(f"   PROMOTE: {', '.join(promotes)}")
        if demotes:
            lines.append(f"   DEMOTE:  {', '.join(demotes)}")
        if kills:
            lines.append(f"   KILL:    {', '.join(kills)}")
    else:
        lines += ["", "3. TICKER SCOREBOARD: No data (no trades or memory)"]

    lines += ["", "4. PER-ENTRY-TYPE PERFORMANCE"]
    for etype, data in sorted(metrics.per_entry_type.items()):
        lines.append(
            f"   {etype:10s}  trades={data['trades']:2d}  "
            f"WR={data['win_rate']:.0%}  PnL={data['total_pnl']:+7.2f}"
        )

    lines += ["", "5. PER-SESSION PERFORMANCE"]
    for phase, data in metrics.per_session.items():
        lines.append(
            f"   {phase:16s}  trades={data['trades']:2d}  "
            f"WR={data['win_rate']:.0%}  PnL={data['total_pnl']:+7.2f}"
        )

    lines += [
        "",
        "6. REGIME ACCURACY",
        f"   Predictions: {regime_acc.total_predictions}",
        f"   Correct:     {regime_acc.correct}",
        f"   Accuracy:    {regime_acc.accuracy_pct:.1f}%",
    ]

    lines += ["", "7. PARAMETER RECOMMENDATIONS"]
    for adj in recommendations.get("adjustments", []):
        lines.append(f"   - {adj}")
    if not recommendations.get("adjustments"):
        lines.append("   No adjustments recommended (insufficient trades or within tolerance)")
    lines.append(f"   Kelly fraction:     {recommendations.get('kelly_fraction', 'N/A')}")
    lines.append(f"   Chandelier ATR:     {recommendations.get('chandelier_atr_mult', 'N/A')}")

    if decay_signals:
        lines += ["", "8. ALPHA DECAY WARNINGS"]
        for sig in decay_signals:
            lines.append(
                f"   [{sig.severity.upper()}] {sig.entity}: "
                f"7d={sig.metric_7d:.3f} vs 30d={sig.metric_30d:.3f} "
                f"(decay {sig.decay_pct:.1f}%)"
            )
    else:
        lines += ["", "8. ALPHA DECAY: No decay detected"]

    lines += ["", f"{'=' * 60}", ""]

    report_path.write_text("\n".join(lines))
    log.info("Daily report written: %s", report_path)

    # Also save machine-readable metrics sidecar
    metrics_path = REPORTS_DIR / f"{date_str}_metrics.json"
    metrics_data = {
        "date": date_str,
        "total_trades": metrics.total_trades,
        "win_rate": metrics.win_rate,
        "profit_factor": metrics.profit_factor,
        "total_pnl": metrics.total_pnl,
        "avg_rung": metrics.avg_rung,
    }
    metrics_path.write_text(json.dumps(metrics_data, indent=2))
    log.info("Metrics sidecar written: %s", metrics_path)

    return report_path


# ---------------------------------------------------------------------------
# 6. Pre-Market Battle Plan
# ---------------------------------------------------------------------------
def generate_battle_plan(
    date_str: str,
    metrics: DailyMetrics,
    recommendations: Dict[str, Any],
) -> Path:
    """Generate pre-market battle plan for tomorrow."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    plan_path = REPORTS_DIR / f"battle_plan_{date_str}.txt"

    # Rank tickers by recent performance
    ranked = sorted(
        metrics.per_ticker.items(),
        key=lambda x: (x[1]["win_rate"], x[1]["total_pnl"]),
        reverse=True,
    )

    # Determine directional bias
    long_tickers = [t for t in PRIMARY_TICKERS if not t.startswith("QQQ") or "S" not in t]
    inverse_tickers = ["QQQS.L", "3USS.L"]

    long_pnl = sum(
        d["total_pnl"] for t, d in metrics.per_ticker.items()
        if t not in inverse_tickers
    )
    inverse_pnl = sum(
        d["total_pnl"] for t, d in metrics.per_ticker.items()
        if t in inverse_tickers
    )

    bias = "LONG" if long_pnl >= inverse_pnl else "INVERSE"

    lines = [
        f"{'=' * 60}",
        f"  BATTLE PLAN — {date_str} (Generated by Ouroboros v6.0)",
        f"{'=' * 60}",
        "",
        f"  Directional bias: {bias}",
        f"  Kelly fraction:   {recommendations.get('kelly_fraction', 0.20):.3f}",
        f"  Chandelier ATR:   {recommendations.get('chandelier_atr_mult', 3.0):.2f}",
        "",
        "TICKER RANKINGS (by WR + PnL):",
    ]

    for rank, (ticker, data) in enumerate(ranked, 1):
        star = " *" if data["win_rate"] >= 0.5 and data["total_pnl"] > 0 else ""
        lines.append(
            f"  {rank:2d}. {ticker:12s}  WR={data['win_rate']:.0%}  "
            f"PnL={data['total_pnl']:+7.2f}  rung={data['avg_rung']:.1f}{star}"
        )

    # Best session phases
    if metrics.per_session:
        best_phase = max(metrics.per_session.items(), key=lambda x: x[1]["total_pnl"])
        lines += [
            "",
            f"  Best session phase: {best_phase[0]} (PnL={best_phase[1]['total_pnl']:+.2f})",
        ]

    # Best entry types
    if metrics.per_entry_type:
        best_type = max(metrics.per_entry_type.items(), key=lambda x: x[1]["win_rate"])
        lines += [
            f"  Best entry type:    {best_type[0]} (WR={best_type[1]['win_rate']:.0%})",
        ]

    # Per-exchange breakdown
    if metrics.per_exchange:
        lines += ["", "EXCHANGE PERFORMANCE:"]
        for exch, data in sorted(metrics.per_exchange.items(), key=lambda x: -x[1]["total_pnl"]):
            lines.append(
                f"  {exch:12s}  trades={data['trades']:2d}  "
                f"WR={data['win_rate']:.0%}  PnL={data['total_pnl']:+7.2f}"
            )

    # Scoreboard summary
    sb = recommendations.get("ticker_scoreboard", {})
    sb_entries = sb.get("scoreboard", [])
    if sb_entries:
        lines += ["", "TICKER SCOREBOARD SUMMARY:"]
        promotes = sb.get("promotes", [])
        holds = sb.get("holds", [])
        demotes_list = sb.get("demotes", [])
        kills_list = sb.get("kills", [])
        if promotes:
            lines.append(f"  PROMOTE (score>=70): {', '.join(promotes)}")
        if holds:
            lines.append(f"  HOLD    (40-69):     {', '.join(holds)}")
        if demotes_list:
            lines.append(f"  DEMOTE  (20-39):     {', '.join(demotes_list)}")
        if kills_list:
            lines.append(f"  KILL    (<20):       {', '.join(kills_list)}")
        # Show top 3 and bottom 3
        lines.append("")
        for entry in sb_entries[:3]:
            lines.append(f"  TOP  {entry['symbol']:12s}  score={entry['score']:5.1f}  {entry['classification']}")
        for entry in sb_entries[-3:]:
            if entry not in sb_entries[:3]:
                lines.append(f"  BOT  {entry['symbol']:12s}  score={entry['score']:5.1f}  {entry['classification']}")

    # Recommendations
    if recommendations.get("adjustments"):
        lines += ["", "PARAMETER ADJUSTMENTS:"]
        for adj in recommendations["adjustments"]:
            lines.append(f"  - {adj}")

    # Gate veto summary in battle plan
    gate_stats = recommendations.get("gate_veto_stats", {})
    if gate_stats:
        lines += ["", "GATE VETO ANALYSIS (missed-winner review):"]
        for gate, stats in sorted(gate_stats.items(), key=lambda x: -x[1]["total"]):
            syms = ", ".join(stats.get("symbols", [])[:5])
            lines.append("  {:<25s}  {:>5d} vetoes  ({})".format(gate, stats["total"], syms))
        lines += [
            "",
            "  ACTION: Review gate_vetoes_archive/ to check if vetoed signals were right.",
            "  If many vetoes WOULD have been winners, loosen that gate.",
            "  If most vetoes were correct, gate is working — keep it.",
        ]

    lines += [
        "",
        "NOTES:",
        "  - Tickers marked with * are recommended (WR >= 50% and profitable)",
        "  - Battle plan is advisory only; signals still require strategy confirmation",
        "",
        f"{'=' * 60}",
        "",
    ]

    plan_path.write_text("\n".join(lines))
    log.info("Battle plan written: %s", plan_path)
    return plan_path


# ---------------------------------------------------------------------------
# 5.7 Missed-Winner Analysis (N2c)
# ---------------------------------------------------------------------------
@dataclass
class SignalRejection:
    """A signal that was rejected by a gate, loaded from WAL."""
    event_id: str
    event_time_ns: int
    ticker_id: int
    symbol: str
    strategy: str
    confidence: float
    gate_name: str
    gate_reason: str
    price_at_reject: float
    hurst: float = 0.0
    adx: float = 0.0
    rvol: float = 0.0
    vol_slope: float = 0.0
    spread_pct: float = 0.0


@dataclass
class MissedWinner:
    """A rejected signal that turned out to be a winner."""
    rejected_event_id: str
    ticker_id: int
    symbol: str
    gate_name: str
    price_at_reject: float
    best_price_after: float
    hypothetical_pnl_pct: float
    time_to_best_mins: int
    matching_trade_pnl: float = 0.0
    entry_price_diff_pct: float = 0.0


def _build_wal_candidates(date_str: str) -> List[Path]:
    """Build the list of WAL files to scan (same pattern as load_todays_trades)."""
    candidates = [
        WAL_DIR / "current.ndjson",
        WAL_DIR / f"{date_str}.ndjson",
        WAL_DIR / f"wal_{date_str}.ndjson",
    ]
    archive_dir = WAL_DIR / "archive"
    if archive_dir.exists():
        for f in sorted(archive_dir.glob("*.ndjson")):
            if f not in candidates:
                candidates.append(f)
    return candidates


def _load_todays_signal_rejections(date_str: str, wal_candidates: List[Path]) -> List[SignalRejection]:
    """Load today's SignalRejected events from WAL files."""
    rejections: List[SignalRejection] = []

    for wal_path in wal_candidates:
        if not wal_path.exists():
            continue
        try:
            with open(wal_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Filter by date: check event_time_ns falls on today
                    event_time_ns = event.get("event_time_ns", 0)
                    if event_time_ns > 0:
                        try:
                            event_date = datetime.fromtimestamp(
                                event_time_ns / 1e9, tz=timezone.utc
                            ).strftime("%Y-%m-%d")
                            if event_date != date_str:
                                continue
                        except (OSError, ValueError):
                            continue
                    else:
                        continue

                    payload = event.get("payload", {})
                    if "SignalRejected" not in payload:
                        continue

                    sr = payload["SignalRejected"]
                    rejections.append(SignalRejection(
                        event_id=event.get("event_id", ""),
                        event_time_ns=event_time_ns,
                        ticker_id=sr.get("ticker_id", 0),
                        symbol=sr.get("symbol", f"TID_{sr.get('ticker_id', '?')}"),
                        strategy=sr.get("strategy", ""),
                        confidence=sr.get("confidence", 0.0),
                        gate_name=sr.get("gate_name", "unknown"),
                        gate_reason=sr.get("gate_reason", ""),
                        price_at_reject=sr.get("price_at_reject", 0.0),
                        hurst=sr.get("hurst", 0.0),
                        adx=sr.get("adx", 0.0),
                        rvol=sr.get("rvol", 0.0),
                        vol_slope=sr.get("vol_slope", 0.0),
                        spread_pct=sr.get("spread_pct", 0.0),
                    ))
        except Exception as e:
            log.warning("Error reading %s for SignalRejected: %s", wal_path, e)

    return rejections


def _load_todays_position_closed(date_str: str, wal_candidates: List[Path]) -> List[Dict[str, Any]]:
    """Load today's PositionClosed events from WAL files as raw dicts with envelope timestamps."""
    closed: List[Dict[str, Any]] = []

    for wal_path in wal_candidates:
        if not wal_path.exists():
            continue
        try:
            with open(wal_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    event_time_ns = event.get("event_time_ns", 0)
                    if event_time_ns > 0:
                        try:
                            event_date = datetime.fromtimestamp(
                                event_time_ns / 1e9, tz=timezone.utc
                            ).strftime("%Y-%m-%d")
                            if event_date != date_str:
                                continue
                        except (OSError, ValueError):
                            continue
                    else:
                        continue

                    payload = event.get("payload", {})
                    if "PositionClosed" not in payload:
                        continue

                    pc = payload["PositionClosed"]
                    pc["_event_time_ns"] = event_time_ns
                    closed.append(pc)
        except Exception as e:
            log.warning("Error reading %s for PositionClosed: %s", wal_path, e)

    return closed


def analyze_missed_winners(date_str: str, wal_candidates: List[Path]) -> Dict[str, Any]:
    """Cross-reference rejected signals with subsequent price movement.

    For each SignalRejected event, check if any PositionClosed on the same
    ticker profited >1% within 2 hours of the rejection. If the entry price
    of the profitable trade is within 0.5% of the rejection price, flag it
    as a missed winner.

    Returns a summary dict for recommendations["missed_winner_stats"].
    """
    TWO_HOURS_NS = 2 * 60 * 60 * 1_000_000_000  # 2 hours in nanoseconds
    MIN_PROFIT_PCT = 1.0  # Minimum profit % to qualify as missed winner
    MAX_ENTRY_DIFF_PCT = 0.5  # Maximum price difference between reject and entry

    rejections = _load_todays_signal_rejections(date_str, wal_candidates)
    log.info("Loaded %d SignalRejected events for %s", len(rejections), date_str)

    if not rejections:
        return {
            "total_rejected": 0,
            "total_missed_winners": 0,
            "missed_winner_rate": 0.0,
            "worst_gates": [],
            "total_hypothetical_pnl": 0.0,
        }

    closed_events = _load_todays_position_closed(date_str, wal_candidates)
    log.info("Loaded %d PositionClosed events for cross-reference", len(closed_events))

    # Index PositionClosed by ticker_id for fast lookup
    closed_by_ticker: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for pc in closed_events:
        tid = pc.get("ticker_id", -1)
        closed_by_ticker[tid].append(pc)

    missed_winners: List[MissedWinner] = []
    gate_missed_counts: Dict[str, int] = defaultdict(int)

    for rej in rejections:
        if rej.price_at_reject <= 0:
            continue  # Can't analyze without a rejection price

        # Find PositionClosed events on the same ticker AFTER the rejection
        ticker_closed = closed_by_ticker.get(rej.ticker_id, [])
        for pc in ticker_closed:
            pc_entry_time = pc.get("entry_time_ns", 0)

            # Must have been entered AFTER the rejection
            if pc_entry_time <= rej.event_time_ns:
                continue

            # Must be within 2 hours of the rejection
            if pc_entry_time - rej.event_time_ns > TWO_HOURS_NS:
                continue

            # Must have profited >1%
            entry_price = pc.get("entry_price", 0.0)
            final_pnl = pc.get("final_pnl", 0.0)
            if entry_price <= 0:
                continue

            pnl_pct = (final_pnl / entry_price) * 100.0
            if pnl_pct <= MIN_PROFIT_PCT:
                continue

            # Check if the rejection price was close to the eventual entry
            entry_diff_pct = abs(rej.price_at_reject - entry_price) / rej.price_at_reject * 100.0
            if entry_diff_pct > MAX_ENTRY_DIFF_PCT:
                continue  # Price moved too far; the gate may have been correct to wait

            # Calculate hypothetical P&L: if we'd entered at reject price,
            # what would the PnL have been? (use the actual exit price as best proxy)
            exit_price = pc.get("exit_price", 0.0)
            if exit_price > 0 and rej.price_at_reject > 0:
                hypo_pnl_pct = ((exit_price - rej.price_at_reject) / rej.price_at_reject) * 100.0
            else:
                hypo_pnl_pct = pnl_pct  # Fallback to actual PnL %

            time_to_entry_mins = int((pc_entry_time - rej.event_time_ns) / 1e9 / 60)

            mw = MissedWinner(
                rejected_event_id=rej.event_id,
                ticker_id=rej.ticker_id,
                symbol=rej.symbol,
                gate_name=rej.gate_name,
                price_at_reject=rej.price_at_reject,
                best_price_after=exit_price,
                hypothetical_pnl_pct=hypo_pnl_pct,
                time_to_best_mins=time_to_entry_mins,
                matching_trade_pnl=final_pnl,
                entry_price_diff_pct=entry_diff_pct,
            )
            missed_winners.append(mw)
            gate_missed_counts[rej.gate_name] += 1

            log.info(
                "MISSED WINNER: %s rejected by %s at %.4f, "
                "trade entered %.0fmin later at %.4f, PnL=%.2f%% (hypo=%.2f%%)",
                rej.symbol, rej.gate_name, rej.price_at_reject,
                time_to_entry_mins, entry_price, pnl_pct, hypo_pnl_pct,
            )
            break  # One match per rejection is sufficient

    # Write missed winners to file
    mw_path = DATA_DIR / f"missed_winners_{date_str}.ndjson"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(mw_path, "w") as f:
            for mw in missed_winners:
                f.write(json.dumps(asdict(mw)) + "\n")
        log.info("Wrote %d missed winners to %s", len(missed_winners), mw_path)
    except Exception as e:
        log.warning("Failed to write missed winners file: %s", e)

    # Build summary
    total_rejected = len(rejections)
    total_missed = len(missed_winners)
    missed_rate = (total_missed / total_rejected * 100.0) if total_rejected > 0 else 0.0
    total_hypo_pnl = sum(mw.hypothetical_pnl_pct for mw in missed_winners)

    # Sort gates by missed-winner count descending
    worst_gates = sorted(gate_missed_counts.items(), key=lambda x: -x[1])
    worst_gates_list = [{"gate": g, "missed_count": c} for g, c in worst_gates]

    summary = {
        "total_rejected": total_rejected,
        "total_missed_winners": total_missed,
        "missed_winner_rate": round(missed_rate, 1),
        "worst_gates": worst_gates_list,
        "total_hypothetical_pnl": round(total_hypo_pnl, 2),
    }

    log.info(
        "Missed-winner analysis: %d rejected, %d missed winners (%.1f%%), "
        "hypothetical PnL=%.2f%%, worst gates: %s",
        total_rejected, total_missed, missed_rate, total_hypo_pnl,
        ", ".join(f"{g}({c})" for g, c in worst_gates[:5]) or "none",
    )

    return summary


# ---------------------------------------------------------------------------
# 5.11. 3-Tier Intelligence Effectiveness Analysis
# ---------------------------------------------------------------------------
def _analyze_intelligence_tiers(
    date_str: str,
    trades: List[TradeRecord],
    wal_candidates: List[Path],
) -> Dict[str, Any]:
    """Analyze effectiveness of all 3 intelligence tiers for Ouroboros meta-learning.

    Tier 1 (IBKR Scanner): What % of scanner-recommended tickers led to profitable trades?
    Tier 2 (Gemini): What % improvement does Gemini curation add over random selection?
    Tier 3 (Claude): Do Claude rejections correlate with trade losses?

    Returns a dict of effectiveness metrics for system_memory.json.
    """
    result: Dict[str, Any] = {
        "date": date_str,
        "ibkr_scanner_hit_rate": 0.0,
        "gemini_alpha_vs_random": 0.0,
        "claude_rejection_accuracy": 0.0,
        "sample_sizes": {},
    }

    traded_symbols = {t.ticker for t in trades}
    winning_symbols = {t.ticker for t in trades if t.pnl > 0}

    # --- Tier 1: IBKR Scanner effectiveness ---
    # Read scanner_results.json — which scanner-recommended tickers actually traded?
    try:
        scanner_path = DATA_DIR / "scanner_results.json"
        if scanner_path.exists():
            with open(scanner_path) as f:
                scanner_data = json.load(f)
            scanner_tickers = set(scanner_data.get("tickers", []))
            if scanner_tickers:
                scanner_traded = scanner_tickers & traded_symbols
                scanner_winners = scanner_tickers & winning_symbols
                hit_rate = len(scanner_winners) / max(len(scanner_traded), 1)
                result["ibkr_scanner_hit_rate"] = round(hit_rate, 4)
                result["sample_sizes"]["ibkr_scanner"] = {
                    "recommended": len(scanner_tickers),
                    "traded": len(scanner_traded),
                    "won": len(scanner_winners),
                }
                log.info("Tier 1 IBKR Scanner: %d recommended, %d traded, %d won (hit=%.0f%%)",
                         len(scanner_tickers), len(scanner_traded), len(scanner_winners),
                         hit_rate * 100)
    except Exception as e:
        log.warning("Tier 1 IBKR Scanner analysis failed: %s", e)

    # --- Tier 2: Gemini curation effectiveness ---
    # Read gemini_scanner.ndjson — which Gemini-recommended tickers performed best?
    try:
        gemini_core_path = DATA_DIR / "gemini" / "core_universe_latest.json"
        if gemini_core_path.exists():
            with open(gemini_core_path) as f:
                gemini_data = json.load(f)
            gemini_tickers = set(gemini_data.get("data", {}).get("tickers", []))
            if gemini_tickers and trades:
                gemini_traded = gemini_tickers & traded_symbols
                gemini_winners = gemini_tickers & winning_symbols

                # Gemini alpha = (gemini WR) - (overall WR)
                gemini_wr = len(gemini_winners) / max(len(gemini_traded), 1) if gemini_traded else 0
                overall_wr = len(winning_symbols) / max(len(traded_symbols), 1) if traded_symbols else 0
                gemini_alpha = gemini_wr - overall_wr

                result["gemini_alpha_vs_random"] = round(gemini_alpha, 4)
                result["sample_sizes"]["gemini"] = {
                    "recommended": len(gemini_tickers),
                    "traded": len(gemini_traded),
                    "won": len(gemini_winners),
                    "gemini_wr": round(gemini_wr, 4),
                    "overall_wr": round(overall_wr, 4),
                }
                log.info("Tier 2 Gemini: %d recommended, %d traded, %d won (WR=%.0f%% vs overall=%.0f%%, alpha=%+.1f%%)",
                         len(gemini_tickers), len(gemini_traded), len(gemini_winners),
                         gemini_wr * 100, overall_wr * 100, gemini_alpha * 100)
    except Exception as e:
        log.warning("Tier 2 Gemini analysis failed: %s", e)

    # --- Tier 3: Claude challenge effectiveness ---
    # Read claude_curator.ndjson — do Claude rejections correlate with losses?
    try:
        claude_log_path = DATA_DIR / "claude_curator.ndjson"
        if claude_log_path.exists():
            claude_verdicts = []
            with open(claude_log_path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("action") == "evaluate_signal":
                            claude_verdicts.append(entry)
                    except json.JSONDecodeError:
                        continue

            # Also scan WAL PositionClosed events for claude_verdict field
            rejection_outcomes = {"correct_reject": 0, "false_reject": 0,
                                  "correct_approve": 0, "false_approve": 0}
            for wal_path in wal_candidates:
                if not wal_path.exists():
                    continue
                try:
                    with open(wal_path) as f:
                        for line in f:
                            if not line.strip():
                                continue
                            try:
                                event = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            payload = event.get("payload", {})
                            if "PositionClosed" not in payload:
                                continue
                            pc = payload["PositionClosed"]
                            verdict = pc.get("claude_verdict", "")
                            pnl = pc.get("final_pnl", 0.0)
                            if verdict == "reject":
                                if pnl < 0:
                                    rejection_outcomes["correct_reject"] += 1
                                else:
                                    rejection_outcomes["false_reject"] += 1
                            elif verdict in ("approve", "reduce"):
                                if pnl > 0:
                                    rejection_outcomes["correct_approve"] += 1
                                else:
                                    rejection_outcomes["false_approve"] += 1
                except Exception:
                    continue

            total_rejections = rejection_outcomes["correct_reject"] + rejection_outcomes["false_reject"]
            if total_rejections > 0:
                accuracy = rejection_outcomes["correct_reject"] / total_rejections
                result["claude_rejection_accuracy"] = round(accuracy, 4)
            total_approvals = rejection_outcomes["correct_approve"] + rejection_outcomes["false_approve"]

            result["sample_sizes"]["claude"] = {
                "total_verdicts": len(claude_verdicts),
                "correct_rejections": rejection_outcomes["correct_reject"],
                "false_rejections": rejection_outcomes["false_reject"],
                "correct_approvals": rejection_outcomes["correct_approve"],
                "false_approvals": rejection_outcomes["false_approve"],
            }

            # Compute PnL saved by rejections (shadow mode — trades still happened)
            # In shadow mode, claude_rejected trades still execute. We measure if
            # Claude's rejections WOULD have saved money.
            saved_pnl = 0.0
            for wal_path in wal_candidates:
                if not wal_path.exists():
                    continue
                try:
                    with open(wal_path) as f:
                        for line in f:
                            if not line.strip():
                                continue
                            try:
                                event = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            payload = event.get("payload", {})
                            if "PositionClosed" not in payload:
                                continue
                            pc = payload["PositionClosed"]
                            if pc.get("claude_rejected") and pc.get("final_pnl", 0) < 0:
                                saved_pnl += abs(pc["final_pnl"])
                except Exception:
                    continue
            result["claude_rejection_saved_pnl"] = round(saved_pnl, 2)

            log.info("Tier 3 Claude: %d verdicts, rejections=%d (accuracy=%.0f%%), would-save=GBP %.2f",
                     len(claude_verdicts), total_rejections,
                     result["claude_rejection_accuracy"] * 100,
                     saved_pnl)
    except Exception as e:
        log.warning("Tier 3 Claude analysis failed: %s", e)

    return result


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------
def run_nightly() -> int:
    """Execute the complete Ouroboros v6.0 nightly loop."""
    start = time.monotonic()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info("Ouroboros v6.0 nightly run starting for %s", today)

    # Step 1: Load and analyze today's trades
    trades = load_todays_trades(today)

    # S5: Enrich each trade with cost-adjusted P&L
    for t in trades:
        position_gbp = abs(t.entry_price * t.qty) if t.entry_price > 0 else 20.0
        t.estimated_cost = estimate_trade_cost(
            position_gbp=position_gbp,
            exchange=t.exchange,
            spread_at_entry_pct=t.spread_at_entry_pct,
            spread_at_exit_pct=t.spread_at_exit_pct,
        )
        t.cost_adjusted_pnl = t.pnl - t.estimated_cost

    metrics = analyze_trades(trades, today)
    log.info(
        "Trade analysis: %d trades, WR=%.1f%%, PF=%.2f, PnL=GBP %.2f",
        metrics.total_trades, metrics.win_rate * 100, metrics.profit_factor, metrics.total_pnl,
    )
    # S5: Log cost-adjusted reality
    if trades:
        cost_adj_pnl = sum(t.cost_adjusted_pnl for t in trades)
        total_costs = sum(t.estimated_cost for t in trades)
        cost_adj_wins = sum(1 for t in trades if t.cost_adjusted_pnl > 0)
        cost_adj_wr = cost_adj_wins / len(trades) * 100
        log.info(
            "COST-ADJUSTED: PnL=GBP %.2f (sim=%.2f, costs=%.2f), WR=%.1f%% (sim=%.1f%%)",
            cost_adj_pnl, metrics.total_pnl, total_costs, cost_adj_wr, metrics.win_rate * 100,
        )

    # Step 1.5: N1a — Cost-aware trade classification (trade taxonomy)
    # Classify each trade and compute cost-aware metrics for learning.
    cost_report = {}
    try:
        from python_brain.ouroboros.trade_taxonomy import classify_trade, build_class_report
        for t in trades:
            trade_dict = {
                "final_pnl": t.pnl,
                "gross_pnl": t.gross_pnl,
                "total_commission": t.total_commission,
                "spread_at_entry_pct": t.spread_at_entry_pct,
                "spread_at_exit_pct": t.spread_at_exit_pct,
                "highest_rung": t.rung_achieved,
                "mae": t.mae,
                "mfe": t.mfe,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "qty": t.qty,
                "hold_time_mins": t.hold_time_mins,
                "atr_pct_at_entry": t.atr_pct_at_entry,
                "entry_session_phase": t.entry_session_phase,
                "vwap_dist_at_entry_pct": t.vwap_dist_at_entry_pct,
            }
            t.trade_class = classify_trade(trade_dict)

        # Build per-class report
        trade_dicts = [{
            "final_pnl": t.pnl, "gross_pnl": t.gross_pnl,
            "total_commission": t.total_commission,
            "spread_at_entry_pct": t.spread_at_entry_pct,
            "spread_at_exit_pct": t.spread_at_exit_pct,
            "highest_rung": t.rung_achieved,
            "mae": t.mae, "mfe": t.mfe,
            "entry_price": t.entry_price, "qty": t.qty,
            "hold_time_mins": t.hold_time_mins,
            "atr_pct_at_entry": t.atr_pct_at_entry,
            "entry_session_phase": t.entry_session_phase,
        } for t in trades]
        cost_report = build_class_report(trade_dicts)

        # Cost drag metrics
        total_gross = sum(t.gross_pnl for t in trades)
        total_commission = sum(t.total_commission for t in trades)
        total_spread_cost = sum(
            (t.spread_at_entry_pct + t.spread_at_exit_pct) / 100.0 * t.entry_price * t.qty
            for t in trades
        )
        spread_victims = sum(1 for t in trades if t.trade_class == "spread_victim")
        noise_exits = sum(1 for t in trades if t.trade_class == "noise_exit")

        if metrics.total_trades > 0:
            cost_drag_pct = (total_commission + total_spread_cost) / max(abs(total_gross), 1.0) * 100
            avg_spread_pct = sum(t.spread_at_entry_pct for t in trades) / len(trades)
        else:
            cost_drag_pct = 0.0
            avg_spread_pct = 0.0

        log.info(
            "Cost analysis: gross=GBP %.2f, commission=GBP %.2f, spread_cost=GBP %.2f, "
            "drag=%.1f%%, spread_victims=%d, noise_exits=%d, avg_spread=%.2f%%",
            total_gross, total_commission, total_spread_cost,
            cost_drag_pct, spread_victims, noise_exits, avg_spread_pct,
        )

        # Log per-class breakdown
        for tc_name, tc_stats in sorted(cost_report.items()):
            log.info(
                "  Class %-20s: n=%d  WR=%.0f%%  PnL=GBP %+.2f  avg_rung=%.1f",
                tc_name, tc_stats.count, tc_stats.win_rate * 100,
                tc_stats.total_pnl, tc_stats.avg_rung,
            )
    except Exception as e:
        log.warning("Trade taxonomy classification failed (non-fatal): %s", e)

    # Step 2: Regime accuracy (uses empty dict if no live price data available)
    regime_acc = check_regime_accuracy(trades, {})
    log.info("Regime accuracy: %d predictions, %.1f%% correct", regime_acc.total_predictions, regime_acc.accuracy_pct)

    # Step 2.1: HMM Student-t Regime Detection (Book 113, Book 43 wiring fix)
    # Runs the HMM model on recent WAL returns data and writes regime state files
    # that signal_filters.py reads for regime-conditional strategy activation.
    try:
        from python_brain.ouroboros.hmm_student_t import StudentTHMM, generate_regime_report
        hmm_model = StudentTHMM(n_regimes=3, nu=4.0)
        # Load returns from WAL (last 90 days of daily log returns)
        wal_returns = _load_wal_returns_for_hmm(lookback_days=90)
        if len(wal_returns) >= 20:
            hmm_model.fit(wal_returns)
            regime_report = generate_regime_report(hmm_model, wal_returns)
            current_regime = hmm_model.current_regime(wal_returns)
            regime_probs = hmm_model.regime_probabilities(wal_returns)
            regime_names = {0: "LowVol", 1: "Normal", 2: "HighVol"}
            log.info(
                "HMM regime: %s (P=%.2f), probs=[%.2f, %.2f, %.2f]",
                regime_names.get(current_regime, f"R{current_regime}"),
                regime_probs[current_regime] if current_regime < len(regime_probs) else 0.0,
                regime_probs[0] if len(regime_probs) > 0 else 0.0,
                regime_probs[1] if len(regime_probs) > 1 else 0.0,
                regime_probs[2] if len(regime_probs) > 2 else 0.0,
            )
            recommendations["hmm_regime"] = {
                "current": int(current_regime),
                "name": regime_names.get(current_regime, f"R{current_regime}"),
                "probabilities": [float(p) for p in regime_probs],
                "report_path": regime_report,
            }
        else:
            log.info("HMM regime: insufficient data (%d returns, need 20+)", len(wal_returns))
    except Exception as e:
        log.warning("HMM regime detection failed (non-fatal): %s", e)

    # Step 2.5: Load persistent memory for cumulative learning
    mem = None
    try:
        from python_brain.ouroboros.persistent_memory import load_memory, save_memory
        mem = load_memory()
        log.info(
            "Persistent memory loaded: %d total trades, %.1f%% all-time WR, %d lessons",
            mem.total_trades, mem.all_time_win_rate * 100, len(getattr(mem, 'lessons', [])),
        )
    except Exception as e:
        log.warning("Persistent memory load failed (non-fatal): %s", e)

    # Step 3: Parameter optimization (now uses cumulative memory + today's metrics)
    recommendations = optimize_parameters(metrics, mem=mem)

    # Step 3.5: Incorporate backfill simulator feedback (ISS-018)
    # The backfill simulator runs daily at 07:00 UTC and writes feedback to
    # data/backfill_feedback.json. We load it here and attach it to
    # recommendations so downstream consumers (config_writer, battle_plan)
    # can use simulated performance data to validate or adjust parameters.
    try:
        backfill_fb = _load_backfill_feedback()
        if backfill_fb:
            recommendations["backfill_feedback"] = backfill_fb

            # Log strategy confidence deltas from backfill simulation
            deltas = backfill_fb.get("strategy_confidence_delta", {})
            if deltas:
                delta_strs = [f"{k}={v:+.1f}" for k, v in deltas.items() if v != 0]
                if delta_strs:
                    log.info("Backfill confidence deltas: %s", ", ".join(delta_strs))
                    recommendations["adjustments"].append(
                        f"Backfill sim ({backfill_fb.get('simulated_trades_count', 0)} trades, "
                        f"WR={backfill_fb.get('simulated_win_rate', 0):.0%}): "
                        + ", ".join(delta_strs)
                    )

            # Log recommended parameter changes from backfill
            param_recs = backfill_fb.get("recommended_parameter_changes", [])
            if param_recs:
                log.info("Backfill parameter recommendations: %d suggestions", len(param_recs))
                for rec in param_recs[:5]:  # Log top 5
                    log.info("  %s %s %s: %s",
                             rec.get("ticker", "?"), rec.get("parameter", "?"),
                             rec.get("direction", "?"), rec.get("reason", ""))

            # TODO (ISS-018 Phase 2): Use strategy_confidence_delta to adjust
            # per-entry-type confidence floors in indicator_gates. For now we
            # only attach the data to recommendations for visibility in the
            # battle plan and config_writer. Actual parameter modification
            # requires validation gate: backfill WR must exceed live WR by
            # at least 5pp before any confidence adjustment is applied.
        else:
            log.info("No backfill feedback available for this nightly run")
    except Exception as e:
        log.warning("Backfill feedback integration failed (non-fatal): %s", e)

    RECS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RECS_FILE, "w") as f:
        json.dump(recommendations, f, indent=2)
    log.info("Recommendations written: %s", RECS_FILE)

    # Step 4: Update persistent memory (cumulative stats across all sessions)
    try:
        if mem is None:
            from python_brain.ouroboros.persistent_memory import load_memory, save_memory
            mem = load_memory()
        for t in trades:
            # S5: Record cost-adjusted P&L into persistent memory so Ouroboros
            # learns from realistic economics, not zero-cost simulation fantasy.
            mem.record_trade(
                symbol=t.ticker, pnl=t.cost_adjusted_pnl, rung=t.rung_achieved,
                kelly=recommendations.get("kelly_fraction", 0.2),
                regime=t.regime_at_entry,
                exchange=getattr(t, 'exchange', ''),
                confidence=t.confidence, strategy=t.strategy,
            )
        # S5: Session P&L uses cost-adjusted values
        cost_adj_session_pnl = sum(t.cost_adjusted_pnl for t in trades) if trades else metrics.total_pnl
        cost_adj_wr = (sum(1 for t in trades if t.cost_adjusted_pnl > 0) / len(trades)) if trades else metrics.win_rate
        mem.record_session(
            date=today, trades=metrics.total_trades, exits=metrics.total_trades,
            pnl=cost_adj_session_pnl, win_rate=cost_adj_wr,
            avg_rung=metrics.avg_rung,
            kelly=recommendations.get("kelly_fraction", 0.2),
            chandelier=recommendations.get("chandelier_atr_mult", 3.0),
        )
        save_memory(mem)
        log.info("Persistent memory updated: %s", mem.summary_text().split("\n")[2])
    except Exception as e:
        log.warning("Persistent memory update failed (non-fatal): %s", e)

    # Step 4.5: Ticker Scoreboard — Promotion / Demotion / Kill
    try:
        scoreboard_result = generate_ticker_scoreboard(metrics, mem)
        recommendations["ticker_scoreboard"] = scoreboard_result
        log.info(
            "Ticker scoreboard: %d promote, %d hold, %d demote, %d kill",
            len(scoreboard_result["promotes"]),
            len(scoreboard_result.get("holds", [])),
            len(scoreboard_result["demotes"]),
            len(scoreboard_result["kills"]),
        )
    except Exception as e:
        log.warning("Ticker scoreboard generation failed (non-fatal): %s", e)

    # Step 5: Alpha decay detection
    decay_signals = detect_alpha_decay(REPORTS_DIR, metrics)

    # Step 5.05: MFE/MAE Analysis & R-Multiple Tracking (Book 39)
    try:
        from python_brain.forensics.mfe_mae import analyze_all_trades, save_mfe_mae_report
        mfe_report = analyze_all_trades(WAL_DIR, lookback_days=30)
        if mfe_report["total_trades"] > 0:
            save_mfe_mae_report(mfe_report)
            overall = mfe_report.get("overall", {})
            log.info(
                "MFE/MAE: %d trades, avg_mfe=%.2f%%, avg_mae=%.2f%%, "
                "avg_exit_eff=%.2f, avg_edge_ratio=%.2f, exit_below_half_mfe=%.0f%%",
                mfe_report["total_trades"],
                overall.get("avg_mfe_pct", 0),
                overall.get("avg_mae_pct", 0),
                overall.get("avg_exit_efficiency", 0),
                overall.get("avg_edge_ratio", 0),
                overall.get("pct_exit_below_half_mfe", 0),
            )
            recommendations["mfe_mae"] = mfe_report
            # Per-strategy exit diagnostics
            for strat, stats in mfe_report.get("strategies", {}).items():
                if stats.get("count", 0) >= 5:
                    log.info(
                        "  %s: n=%d exit_eff=%.2f edge_ratio=%.2f exit_below_half=%.0f%%",
                        strat, stats["count"], stats.get("avg_exit_efficiency", 0),
                        stats.get("avg_edge_ratio", 0), stats.get("pct_exit_below_half_mfe", 0),
                    )
        else:
            log.info("MFE/MAE: no closed trades in 30-day lookback")
    except Exception as e:
        log.warning("MFE/MAE analysis failed (non-fatal): %s", e)

    # Step 5.5: Phase H — Indicator Intelligence (30-day lookback analysis)
    try:
        from python_brain.ouroboros.indicator_intelligence import (
            analyze_indicators, save_indicator_intelligence,
        )
        intel = analyze_indicators(wal_dir=WAL_DIR, days=30)
        if intel.total_trades > 0:
            save_indicator_intelligence(intel, DATA_DIR)
            log.info(
                "Indicator intelligence: %d trades, %d rules, %d recommendations, WR=%.1f%%",
                intel.total_trades, len(intel.rules), len(intel.recommended_filters),
                intel.overall_win_rate * 100,
            )
            # Feed recommended filters into recommendations for config_writer
            if intel.recommended_filters:
                recommendations["indicator_filters"] = intel.recommended_filters
            # Push indicator intelligence to Google Sheets
            try:
                _push_indicator_intelligence_to_sheets(intel)
            except Exception as sheets_err:
                log.warning("Indicator intelligence sheets push failed (non-fatal): %s", sheets_err)
        else:
            log.info("Indicator intelligence: 0 trades in 30-day lookback, skipping")
    except Exception as e:
        log.warning("Indicator intelligence failed (non-fatal): %s", e)

    # Step 5.6: Gate Veto Analysis — evaluate missed winners from gate_vetoes.ndjson
    # This tells Ouroboros whether the gates are too tight (blocking winners) or correct.
    try:
        gate_vetoes_path = DATA_DIR / "gate_vetoes.ndjson"
        if gate_vetoes_path.exists():
            import json as _json
            gate_stats = {}  # gate_name → {"total": N, "vetoes": [...]}
            with open(gate_vetoes_path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        veto = _json.loads(line)
                        gate = veto.get("gate", "unknown")
                        if gate not in gate_stats:
                            gate_stats[gate] = {"total": 0, "symbols": set()}
                        gate_stats[gate]["total"] += 1
                        gate_stats[gate]["symbols"].add(veto.get("symbol", "?"))
                    except Exception:
                        pass

            if gate_stats:
                log.info("Gate veto analysis:")
                for gate, stats in sorted(gate_stats.items(), key=lambda x: -x[1]["total"]):
                    syms = ", ".join(sorted(stats["symbols"])[:5])
                    log.info("  %s: %d vetoes (%s)", gate, stats["total"], syms)

                # Add to recommendations for battle plan
                recommendations["gate_veto_stats"] = {
                    g: {"total": s["total"], "symbols": list(s["symbols"])[:10]}
                    for g, s in gate_stats.items()
                }

            # Rotate vetoes file (keep today's, archive yesterday's)
            archive_path = DATA_DIR / "gate_vetoes_archive"
            archive_path.mkdir(exist_ok=True)
            import shutil
            archive_dest = archive_path / "gate_vetoes_{}.ndjson".format(today)
            shutil.copy2(gate_vetoes_path, archive_dest)
            gate_vetoes_path.write_text("")  # Clear for tomorrow
            log.info("Gate vetoes archived to %s", archive_dest)
        else:
            log.info("No gate vetoes file found (no vetoes today)")
    except Exception as e:
        log.warning("Gate veto analysis failed (non-fatal): %s", e)

    # Step 5.7: Missed-Winner Analysis (N2c) — cross-reference rejected signals
    # with subsequent profitable trades to identify overly-tight gates.
    try:
        wal_candidates = _build_wal_candidates(today)
        mw_stats = analyze_missed_winners(today, wal_candidates)
        recommendations["missed_winner_stats"] = mw_stats
        if mw_stats["total_rejected"] > 0:
            log.info(
                "Missed-winner summary: %d/%d rejected signals were missed winners (%.1f%%)",
                mw_stats["total_missed_winners"], mw_stats["total_rejected"],
                mw_stats["missed_winner_rate"],
            )
        else:
            log.info("Missed-winner analysis: no SignalRejected events today")
    except Exception as e:
        log.warning("Missed-winner analysis failed (non-fatal): %s", e)

    # Step 5.8: Analytics Pack — friction-adjusted expectancy, comparison tables, data quality
    try:
        from python_brain.ouroboros.analytics_pack import run_analytics_pack
        raw_trades = [asdict(t) for t in trades]
        analytics = run_analytics_pack(raw_trades, list(PRIMARY_TICKERS))
        recommendations["analytics_pack"] = analytics
        log.info(
            "Analytics pack: overall net expectancy=%.4f, friction ratio=%.1f%%, data quality=%.0f%%",
            analytics.get("friction_expectancy", {}).get("overall_net_expectancy", 0),
            analytics.get("friction_expectancy", {}).get("friction_ratio", 0) * 100,
            analytics.get("data_quality_scorecard", {}).get("avg_completeness", 0),
        )
    except Exception as e:
        log.warning("Analytics pack failed (non-fatal): %s", e)

    # Step 5.9: Macro event context — classify today's trades against economic calendar
    try:
        from python_brain.ouroboros.macro_event_layer import EventCalendar
        cal = EventCalendar()
        cal.load_cache()
        macro_contexts = []
        for t in trades:
            ctx = cal.classify_trade_macro_context(t.entry_time_ns, t.exit_time_ns)
            macro_contexts.append({"ticker": t.ticker, "pnl": t.pnl, **ctx})
        macro_victims = [m for m in macro_contexts if m["macro_classification"] == "macro_victim"]
        recommendations["macro_context"] = {
            "total_trades": len(macro_contexts),
            "clean": sum(1 for m in macro_contexts if m["macro_classification"] == "clean"),
            "macro_proximate": sum(1 for m in macro_contexts if m["macro_classification"] == "macro_proximate"),
            "macro_victim": len(macro_victims),
            "macro_victim_pnl": sum(m["pnl"] for m in macro_victims),
        }
        log.info("Macro context: %d clean, %d proximate, %d victims",
                 recommendations["macro_context"]["clean"],
                 recommendations["macro_context"]["macro_proximate"],
                 recommendations["macro_context"]["macro_victim"])
    except Exception as e:
        log.warning("Macro event analysis failed (non-fatal): %s", e)

    # Step 5.10: Research store + anomaly baselines + incident review
    try:
        from python_brain.ouroboros.research_store import (
            ResearchContextStore, AnomalyBaselineLibrary, generate_incident_pack
        )
        # Update research context
        rcs = ResearchContextStore()
        rcs.load()
        scoreboard_data = recommendations.get("ticker_scoreboard", {})
        mw_data = recommendations.get("missed_winner_stats", {})
        rcs.add_daily_context(today, asdict(metrics) if hasattr(metrics, '__dataclass_fields__') else {
            "total_trades": metrics.total_trades, "win_rate": metrics.win_rate,
            "profit_factor": metrics.profit_factor, "total_pnl": metrics.total_pnl,
            "avg_rung": metrics.avg_rung,
        }, recommendations, scoreboard_data, mw_data)
        rcs.save()

        # Update anomaly baselines + check
        abl = AnomalyBaselineLibrary()
        abl.load()
        today_metrics_dict = {
            "win_rate": metrics.win_rate, "total_pnl": metrics.total_pnl,
            "profit_factor": metrics.profit_factor, "total_trades": metrics.total_trades,
            "avg_spread": sum(t.spread_at_entry_pct for t in trades) / max(len(trades), 1),
            "avg_rung": metrics.avg_rung,
            "total_commission": sum(t.total_commission for t in trades),
        }
        abl.update_baselines(today, today_metrics_dict)
        anomalies = abl.check_anomalies(today_metrics_dict)
        abl.save()
        recommendations["anomalies"] = [a.to_dict() for a in anomalies]
        if anomalies:
            log.warning("ANOMALIES DETECTED: %s", ", ".join(f"{a.metric}(z={a.z_score:.1f})" for a in anomalies))

        # Generate incident pack if bad day
        if metrics.total_pnl < 0 or any(a.severity == "critical" for a in anomalies):
            raw_trades = [asdict(t) for t in trades]
            incident = generate_incident_pack(today, raw_trades,
                                              today_metrics_dict, anomalies, mw_data)
            log.warning("INCIDENT PACK generated: %s severity — %s",
                        incident.get("severity", "unknown"), incident.get("summary", ""))
    except Exception as e:
        log.warning("Research store/anomaly analysis failed (non-fatal): %s", e)

    # Step 5.11: 3-Tier Intelligence Effectiveness Analysis
    # Measure how well each intelligence tier (IBKR Scanner, Gemini, Claude) contributes.
    # Results feed into system_memory.json so Ouroboros can adapt tier weighting.
    try:
        tier_effectiveness = _analyze_intelligence_tiers(today, trades, wal_candidates if 'wal_candidates' in dir() else _build_wal_candidates(today))
        recommendations["tier_effectiveness"] = tier_effectiveness

        # Write to system_memory.json for cross-session persistence
        sys_mem_path = DATA_DIR / "system_memory.json"
        sys_mem = {}
        if sys_mem_path.exists():
            try:
                with open(sys_mem_path) as f:
                    sys_mem = json.load(f)
            except (json.JSONDecodeError, IOError):
                sys_mem = {}
        sys_mem["tier_effectiveness"] = tier_effectiveness
        sys_mem["tier_effectiveness_updated"] = datetime.now(timezone.utc).isoformat()
        try:
            tmp_path = sys_mem_path.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(sys_mem, indent=2), encoding="utf-8")
            os.rename(str(tmp_path), str(sys_mem_path))
            log.info("Tier effectiveness written to system_memory.json")
        except Exception as e:
            log.warning("Failed to write system_memory.json: %s", e)

        log.info("3-Tier Intelligence: scanner_hit=%.0f%%, gemini_alpha=%.1f%%, claude_accuracy=%.0f%%",
                 tier_effectiveness.get("ibkr_scanner_hit_rate", 0) * 100,
                 tier_effectiveness.get("gemini_alpha_vs_random", 0) * 100,
                 tier_effectiveness.get("claude_rejection_accuracy", 0) * 100)
    except Exception as e:
        log.warning("3-Tier intelligence analysis failed (non-fatal): %s", e)

    # Step 5.12: Strategy Lifecycle Evaluation (Books 47, 141, 189)
    # Evaluate each strategy's lifecycle state — SPRT edge test, quarantine, kill.
    try:
        from python_brain.lifecycle.strategy_state import StrategyLifecycle, detect_cannibalization
        lifecycle_results = {}
        for t in trades:
            strat = t.strategy or "unknown"
            if strat not in lifecycle_results:
                lifecycle_results[strat] = StrategyLifecycle(strat)
            lifecycle_results[strat].record_trade(pnl=t.cost_adjusted_pnl, won=t.cost_adjusted_pnl > 0)

        for strat, lc in lifecycle_results.items():
            new_state = lc.evaluate()
            if new_state:
                log.warning("LIFECYCLE: %s transitioned to %s", strat, new_state.value)

        recommendations["lifecycle"] = {
            name: lc.to_dict() for name, lc in lifecycle_results.items()
        }
        log.info("Lifecycle: %d strategies evaluated", len(lifecycle_results))
    except Exception as e:
        log.warning("Strategy lifecycle evaluation failed (non-fatal): %s", e)

    # Step 5.13: Validation Gates Check (Books 6, 31, 192)
    # Run DSR and PBO on each strategy's cumulative returns.
    try:
        from python_brain.validation.strategy_gates import deflated_sharpe_ratio, validate_strategy
        import numpy as _np
        if mem is not None:
            validation_results = {}
            for strat, stats in getattr(mem, 'per_strategy_stats', {}).items():
                returns = stats.get("returns", [])
                if len(returns) >= 30:
                    arr = _np.array(returns, dtype=_np.float64)
                    result = validate_strategy(arr, n_trials=len(getattr(mem, 'per_strategy_stats', {})))
                    validation_results[strat] = result.to_dict()
                    if not result.passed:
                        log.info("VALIDATION: %s FAILED gate: %s", strat, result.reason)

            if validation_results:
                recommendations["validation_gates"] = validation_results
                passed = sum(1 for v in validation_results.values() if v["passed"])
                log.info("Validation: %d/%d strategies pass all gates", passed, len(validation_results))
    except Exception as e:
        log.warning("Validation gates check failed (non-fatal): %s", e)

    # Step 5.14: Edge Forensics & Signal Attribution (Book 219)
    # Six-dimensional per-trade attribution for understanding where edge comes from.
    try:
        from python_brain.forensics.mfe_mae import compute_trade_metrics
        if trades:
            edge_scores = []
            for t in trades:
                trade_dict = {
                    "ticker": t.ticker, "strategy": t.strategy,
                    "entry_price": t.entry_price, "exit_price": t.exit_price,
                    "initial_stop": getattr(t, 'initial_stop', 0),
                    "highest_price": getattr(t, 'mfe', t.entry_price),
                    "lowest_price": getattr(t, 'mae', t.entry_price),
                    "hold_time_mins": t.hold_time_mins,
                    "highest_rung": t.rung_achieved,
                    "pnl": t.cost_adjusted_pnl,
                }
                m = compute_trade_metrics(trade_dict)
                edge_scores.append({
                    "ticker": m.ticker, "strategy": m.strategy,
                    "exit_efficiency": round(m.exit_efficiency, 3),
                    "edge_ratio": round(m.edge_ratio, 3),
                    "r_multiple": round(m.exit_r_multiple, 3),
                    "mfe_pct": round(m.mfe_pct, 3),
                    "mae_pct": round(m.mae_pct, 3),
                })

            recommendations["edge_forensics"] = edge_scores
            avg_eff = sum(s["exit_efficiency"] for s in edge_scores) / max(len(edge_scores), 1)
            avg_edge = sum(s["edge_ratio"] for s in edge_scores) / max(len(edge_scores), 1)
            log.info("Edge forensics: %d trades, avg_exit_eff=%.2f, avg_edge_ratio=%.2f",
                     len(edge_scores), avg_eff, avg_edge)
    except Exception as e:
        log.warning("Edge forensics failed (non-fatal): %s", e)

    # Step 5.15: Monte Carlo Simulation (Book 17)
    # Run 10K-path bootstrap MC on cumulative trade returns for ruin probability.
    try:
        from python_brain.validation.monte_carlo import run_monte_carlo
        import numpy as _np
        if mem is not None:
            all_returns = []
            for strat_stats in getattr(mem, 'per_strategy_stats', {}).values():
                all_returns.extend(strat_stats.get("returns", []))
            if len(all_returns) >= 20:
                mc_result = run_monte_carlo(
                    _np.array(all_returns, dtype=_np.float64),
                    n_paths=5000,  # 5K for nightly speed; 10K for deep analysis
                    horizon_days=252,
                )
                recommendations["monte_carlo"] = mc_result.to_dict()
                log.info(
                    "Monte Carlo: ruin=%.1f%%, median_DD=%.1f%%, Sharpe=[%.2f,%.2f], P(+)=%.0f%%",
                    mc_result.ruin_probability * 100,
                    mc_result.median_max_drawdown_pct,
                    mc_result.sharpe_ci_low, mc_result.sharpe_ci_high,
                    mc_result.prob_positive * 100,
                )
            else:
                log.info("Monte Carlo: insufficient returns (%d < 20)", len(all_returns))
    except Exception as e:
        log.warning("Monte Carlo simulation failed (non-fatal): %s", e)

    # Step 5.16: Health Check (Book 53)
    try:
        from python_brain.watchdog import HealthMonitor
        monitor = HealthMonitor(
            ibkr_host=os.environ.get("IBKR_HOST", "localhost"),
            ibkr_port=int(os.environ.get("IBKR_PORT", "4003")),
        )
        health = monitor.check_all()
        monitor.save_status(health)
        recommendations["health"] = health.to_dict()
        if health.healthy:
            log.info("Health check: ALL PASS (%d checks)", len(health.checks))
        else:
            failed = [c.name for c in health.failed_checks]
            log.warning("Health check: %d FAILED — %s", len(failed), ", ".join(failed))
    except Exception as e:
        log.warning("Health check failed (non-fatal): %s", e)

    # Step 5.17: DuckDB Warehouse Ingestion (Book 63)
    try:
        from python_brain.warehouse.duckdb_store import DataWarehouse
        dw = DataWarehouse()
        today_wal = WAL_DIR / f"{today}.ndjson"
        if today_wal.exists():
            counts = dw.ingest_wal(str(today_wal))
            log.info("DuckDB ingestion: %d trades, %d signals from %s",
                     counts["trades"], counts["signals"], today)
        dw.close()
    except ImportError:
        log.info("DuckDB not installed — skipping warehouse ingestion")
    except Exception as e:
        log.warning("DuckDB ingestion failed (non-fatal): %s", e)

    # Step 5.18: Data Quality Report (Book 45)
    try:
        from python_brain.forensics.data_quality import DataQualityAnalyzer
        dq = DataQualityAnalyzer()
        dq_report = dq.analyze_day(today)
        dq.save_report(dq_report)
        recommendations["data_quality"] = dq_report.to_dict()
        log.info("Data quality: score=%.0f%%, tickers=%d, coverage=%.0f%%",
                 dq_report.overall_score, dq_report.tickers_with_data, dq_report.avg_coverage_pct)
    except Exception as e:
        log.warning("Data quality analysis failed (non-fatal): %s", e)

    # Step 5.19: ETP Decay Monitor (Book 46)
    try:
        from python_brain.forensics.etp_decay_monitor import ETPDecayMonitor
        decay_mon = ETPDecayMonitor()
        # Check all open positions for holding period violations
        open_positions = recommendations.get("open_positions", {})
        vix_val = msg.get("vix", 21.0) if "msg" in dir() else 21.0
        decay_alerts = decay_mon.check_all_positions(open_positions, vix_val)
        if decay_alerts:
            recommendations["etp_decay_alerts"] = [
                {"ticker": a.ticker, "days_held": a.days_held, "max_days": a.max_days,
                 "decay_pct": a.estimated_decay_pct, "action": a.action}
                for a in decay_alerts
            ]
            log.warning("ETP decay: %d positions exceeding holding limits", len(decay_alerts))
    except Exception as e:
        log.warning("ETP decay monitor failed (non-fatal): %s", e)

    # Step 5.20: Performance Attribution (Books 81, 89)
    try:
        from python_brain.forensics.performance_attribution import AttributionAnalyzer
        attr = AttributionAnalyzer()
        trade_dicts = [{
            "strategy": t.strategy, "pnl": t.pnl, "gross_pnl": t.gross_pnl,
            "estimated_cost": t.estimated_cost, "hold_time_mins": t.hold_time_mins,
            "commission": t.total_commission, "notional": abs(t.entry_price * t.qty),
        } for t in trades]
        attr_report = attr.analyze(trade_dicts, trading_days=1, equity=msg.get("equity", 10000) if "msg" in dir() else 10000)
        recommendations["performance_attribution"] = attr_report.to_dict()
        log.info("Attribution: net_pnl=%.2f, Sharpe=%.2f, cost_drag=%.1f%%, turnover=%.1f/day",
                 attr_report.total_net_pnl, attr_report.portfolio_sharpe,
                 attr_report.cost_breakdown.cost_as_pct_of_gross, attr_report.turnover.trades_per_day)
    except Exception as e:
        log.warning("Performance attribution failed (non-fatal): %s", e)

    # Step 5.21: Audit Trail (Books 88, 185)
    try:
        from python_brain.forensics.audit_trail import AuditTrail, AuditCategory
        audit = AuditTrail()
        audit.log_event(AuditCategory.SYSTEM, "nightly_run_complete", {
            "date": today, "total_trades": metrics.total_trades,
            "net_pnl": sum(t.cost_adjusted_pnl for t in trades) if trades else 0,
            "win_rate": metrics.win_rate,
        })
        for t in trades:
            audit.log_trade("trade_closed", ticker=t.ticker, strategy=t.strategy,
                           pnl=t.cost_adjusted_pnl, rung=t.rung_achieved)
        log.info("Audit trail: %d events logged", audit.event_count())
    except Exception as e:
        log.warning("Audit trail failed (non-fatal): %s", e)

    # Step 5.22: System Journal — Institutional Memory (Book 13)
    try:
        from python_brain.forensics.system_journal import SystemJournal
        journal = SystemJournal()
        # Auto-generate lessons from today's data
        if metrics.total_trades > 0 and metrics.win_rate < 0.3:
            journal.add_lesson(
                f"Low win rate day ({metrics.win_rate:.0%}) with {metrics.total_trades} trades",
                source="nightly_auto", tags=["low_wr", today],
            )
        if any(d.get("action") == "FORCE_CLOSE" for d in recommendations.get("etp_decay_alerts", [])):
            journal.add_incident("ETP holding period violation triggered force-close", source="etp_decay")
        recommendations["journal_entries"] = journal.count
    except Exception as e:
        log.warning("System journal failed (non-fatal): %s", e)

    # Step 5.23: Compounding Journal — Milestone Tracking (Book 218)
    try:
        from python_brain.lifecycle.compounding_journal import CompoundingJournal
        comp_journal = CompoundingJournal(initial_equity=10000)
        equity = msg.get("equity", 10000) if "msg" in dir() else 10000
        comp_journal.record_week(
            equity=equity, trades=metrics.total_trades,
            net_pnl=sum(t.cost_adjusted_pnl for t in trades) if trades else 0,
            win_rate=metrics.win_rate, strategies_active=len(set(t.strategy for t in trades)),
        )
        milestone = comp_journal.check_milestone()
        if milestone:
            recommendations["milestone_achieved"] = {"phase": milestone.phase, "target": milestone.target_equity}
        recommendations["compounding"] = comp_journal.to_dict()
    except Exception as e:
        log.warning("Compounding journal failed (non-fatal): %s", e)

    # Step 5.24: Ouroboros Learning Loop Gates (Book 158)
    try:
        from python_brain.lifecycle.ouroboros_gates import OuroborosGatekeeper
        gatekeeper = OuroborosGatekeeper()
        gatekeeper.update_metrics(
            n_trades=mem.total_trades if mem else 0,
            sharpe_60=recommendations.get("sharpe_60", 0),
            recommendation_accuracy=recommendations.get("rec_accuracy", 0),
            n_recommendations=recommendations.get("n_recs", 0),
        )
        loop_state = gatekeeper.evaluate()
        recommendations["ouroboros_gate"] = gatekeeper.to_dict()
        log.info("Ouroboros gate: %s (can_modify=%s)", loop_state.value, gatekeeper.can_modify_params())
    except Exception as e:
        log.warning("Ouroboros gates failed (non-fatal): %s", e)

    # Step 5.25: Paper-to-Live Migration Check (Book 60)
    try:
        from python_brain.lifecycle.paper_to_live import MigrationChecker
        migration = MigrationChecker()
        for strat in set(t.strategy for t in trades):
            if not strat:
                continue
            strat_metrics = {
                "paper_trades": {strat: sum(1 for t in trades if t.strategy == strat)},
                "paper_sharpe": {strat: recommendations.get("sharpe_60", 0)},
                "data_coverage_pct": recommendations.get("data_quality", {}).get("avg_coverage_pct", 0),
            }
            result = migration.run_all(strat, {}, strat_metrics)
            recommendations.setdefault("migration_readiness", {})[strat] = result.to_dict()
    except Exception as e:
        log.warning("Migration check failed (non-fatal): %s", e)

    # Step 5.26: Promotion Pipeline Status (Book 52)
    try:
        from python_brain.validation.promotion_pipeline import PromotionPipeline, PipelineStage
        for strat in set(t.strategy for t in trades):
            if not strat:
                continue
            pipeline = PromotionPipeline(strat)
            # Evaluate current stage based on available metrics
            recommendations.setdefault("promotion_pipeline", {})[strat] = pipeline.to_dict()
    except Exception as e:
        log.warning("Promotion pipeline failed (non-fatal): %s", e)

    # Step 5.27: Simulation Fidelity Score (Book 69)
    try:
        from python_brain.validation.simulation_fidelity import FidelityScorer
        fidelity = FidelityScorer()
        import tomli
        config_path = CONFIG_DIR / "config.toml"
        with open(config_path, "rb") as f:
            config = tomli.load(f)
        fid_report = fidelity.score(
            {"data_coverage_pct": recommendations.get("data_quality", {}).get("avg_coverage_pct", 80)},
            config,
        )
        recommendations["simulation_fidelity"] = fid_report.to_dict()
        log.info("Simulation fidelity: %.0f%% (%s)",
                 fid_report.composite, "RELIABLE" if fid_report.is_reliable else "UNRELIABLE")
    except Exception as e:
        log.warning("Simulation fidelity failed (non-fatal): %s", e)

    # Step 5.28: Portfolio Construction — HRP Weights (Book 20, 180)
    try:
        from python_brain.risk.portfolio_construction import compute_hrp_weights
        import numpy as _np
        if mem and hasattr(mem, 'per_strategy_stats'):
            strat_returns = {}
            for strat, stats in mem.per_strategy_stats.items():
                rets = stats.get("returns", [])
                if len(rets) >= 20:
                    strat_returns[strat] = rets[-60:]  # Last 60 trades
            if len(strat_returns) >= 2:
                tickers = list(strat_returns.keys())
                max_len = max(len(r) for r in strat_returns.values())
                matrix = _np.zeros((max_len, len(tickers)))
                for i, t in enumerate(tickers):
                    r = strat_returns[t]
                    matrix[-len(r):, i] = r
                weights = compute_hrp_weights(matrix, tickers)
                recommendations["hrp_weights"] = weights
                log.info("HRP weights: %s", {k: f"{v:.1%}" for k, v in weights.items()})
    except Exception as e:
        log.warning("HRP portfolio construction failed (non-fatal): %s", e)

    # Step 5.29: Conformal Prediction Calibration (Books 105, 144)
    try:
        from python_brain.calibration.conformal import AdaptiveConformalPredictor
        import numpy as _np
        acp = AdaptiveConformalPredictor(alpha=0.10)
        # Calibrate from historical prediction residuals
        if mem and hasattr(mem, 'per_strategy_stats'):
            all_residuals = []
            for stats in mem.per_strategy_stats.values():
                rets = stats.get("returns", [])
                all_residuals.extend(rets)
            if len(all_residuals) >= 20:
                for r in all_residuals[-100:]:
                    acp.update(abs(r), abs(r) < acp._quantile if acp._quantile > 0 else True)
                recommendations["conformal"] = acp.to_dict()
                log.info("Conformal: coverage=%.0f%%, quantile=%.4f",
                         acp.empirical_coverage * 100, acp._quantile)
    except Exception as e:
        log.warning("Conformal calibration failed (non-fatal): %s", e)

    # Step 5.30: Bayesian Aggregator — Update Source Accuracy (Book 209)
    try:
        from python_brain.aggregation.bayesian_aggregator import BayesianAggregator
        agg = BayesianAggregator()
        for t in trades:
            strat = t.strategy or "unknown"
            agg.add_source(strat, accuracy=0.5, n_trades=0)
            agg.update_source(strat, "long", t.cost_adjusted_pnl > 0)
        recommendations["bayesian_sources"] = agg.get_all_sources()
    except Exception as e:
        log.warning("Bayesian aggregator update failed (non-fatal): %s", e)

    # Step 5.31: Claude Cold-Path Nightly Review (Books 72, 142)
    try:
        from python_brain.claude.decision_authority import DecisionAuthority, DecisionType
        claude_auth = DecisionAuthority(trade_count=mem.total_trades if mem else 0)
        if claude_auth.can_make_decision(DecisionType.NIGHTLY_REVIEW):
            review_request = claude_auth.get_prompt(DecisionType.NIGHTLY_REVIEW, {
                "metrics": {"trades": metrics.total_trades, "wr": metrics.win_rate,
                            "pf": metrics.profit_factor, "pnl": metrics.total_pnl},
                "cost_adjusted_pnl": sum(t.cost_adjusted_pnl for t in trades) if trades else 0,
                "regime": recommendations.get("hmm_regime", {}).get("name", "unknown"),
                "trade_count": mem.total_trades if mem else 0,
            })
            recommendations["claude_review_prompt"] = review_request.to_dict()
            log.info("Claude review: prompt generated (model=%s, authority=%s)",
                     review_request.model, claude_auth.current_level.name)
            # Execute the review via Claude CLI or Gemini SDK (not just prompt generation)
            review_response = claude_auth.execute(review_request)
            recommendations["claude_review_response"] = review_response.to_dict()
            log.info("Claude review: executed (model=%s, confidence=%.2f, cost=$%.4f)",
                     review_response.model_used, review_response.confidence, review_response.cost_usd)
        recommendations["claude_authority"] = claude_auth.to_dict()
    except Exception as e:
        log.warning("Claude authority failed (non-fatal): %s", e)

    # Step 5.32: WAL Deterministic Replay Check (Book 92)
    try:
        from python_brain.risk.deterministic_replay import WALReplayer
        today_wal = WAL_DIR / f"{today}.ndjson"
        if today_wal.exists():
            replayer = WALReplayer()
            replay_result = replayer.replay(str(today_wal))
            recommendations["wal_integrity"] = replay_result.to_dict()
            if not replay_result.deterministic:
                log.warning("WAL INTEGRITY: non-deterministic at event %d", replay_result.first_divergence_at)
            else:
                log.info("WAL integrity: OK (%d events)", replay_result.total_events)
    except Exception as e:
        log.warning("WAL replay check failed (non-fatal): %s", e)

    # Step 5.33: Feature Flags Status (Book 71)
    try:
        from python_brain.risk.feature_flags import FeatureFlagManager
        flags = FeatureFlagManager()
        recommendations["feature_flags"] = flags.status()
        log.info("Feature flags: %d/%d enabled", flags.enabled_count(), len(flags.status()))
    except Exception as e:
        log.warning("Feature flags status failed (non-fatal): %s", e)

    # Step 5.34: Subscription Optimizer (Book 220)
    try:
        from python_brain.execution.subscription_optimizer import SubscriptionOptimizer
        sub_opt = SubscriptionOptimizer()
        recommendations["subscription_status"] = sub_opt.to_dict()
        log.info("Subscriptions: %d/%d slots used", sub_opt.slots_used, sub_opt.max_slots)
    except Exception as e:
        log.warning("Subscription optimizer failed (non-fatal): %s", e)

    # Step 5.35: Calendar Planning for Tomorrow (Book 94)
    try:
        from python_brain.execution.calendar_manager import TradingCalendar
        from datetime import date as _date, timedelta as _td
        cal = TradingCalendar()
        tomorrow = _date.fromisoformat(today) + _td(days=1)
        is_trading = cal.is_trading_day(tomorrow)
        is_half = cal.is_half_day(tomorrow)
        recommendations["tomorrow"] = {
            "date": str(tomorrow),
            "is_trading_day": is_trading,
            "is_half_day": is_half,
            "offset_hours": cal.us_uk_time_offset_hours(tomorrow),
        }
        if not is_trading:
            log.info("Tomorrow %s: MARKET CLOSED", tomorrow)
        elif is_half:
            log.info("Tomorrow %s: HALF DAY", tomorrow)
    except Exception as e:
        log.warning("Calendar planning failed (non-fatal): %s", e)

    # Step 5.36: Telegram Daily Summary (Books 8, 38, 58)
    try:
        from python_brain.alerting.telegram import TelegramAlerter
        alerter = TelegramAlerter()
        alerter.send_daily_summary({
            "total_trades": metrics.total_trades,
            "net_pnl": sum(t.cost_adjusted_pnl for t in trades) if trades else 0,
            "win_rate": metrics.win_rate * 100,
            "drawdown_pct": recommendations.get("drawdown_pct", 0),
        })
    except Exception as e:
        log.warning("Telegram summary failed (non-fatal): %s", e)

    # Step 6: Daily report
    report_path = generate_daily_report(today, metrics, regime_acc, recommendations, decay_signals)

    # Step 7: Battle plan
    plan_path = generate_battle_plan(today, metrics, recommendations)

    elapsed = time.monotonic() - start
    log.info("Ouroboros v6.0 nightly run complete in %.1fs", elapsed)
    log.info("Report: %s", report_path)
    log.info("Battle plan: %s", plan_path)

    return 0


def _push_indicator_intelligence_to_sheets(intel):
    """Push indicator intelligence results to Google Sheets (Phase H/I).

    Writes to 4 tabs: Indicator_Stats, Regime_Performance, Session_Performance, Learned_Rules.
    Uses gspread via the same service account as sheets_sync.
    """
    import gspread
    from google.oauth2.service_account import Credentials

    sa_paths = [
        Path("/app/config/sheets_service_account.json"),
        Path("config/sheets_service_account.json"),
    ]
    sa_path = None
    for p in sa_paths:
        if p.exists():
            sa_path = p
            break
    if sa_path is None:
        log.warning("No service account JSON found, skipping Sheets push")
        return

    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(str(sa_path), scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open("AEGIS V2 Dashboard")

    sheets_data = intel.to_sheets_rows()
    for tab_name, rows in sheets_data.items():
        try:
            ws = sh.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound:
            # Create tab with headers from the first row keys
            ws = sh.add_worksheet(title=tab_name, rows=100, cols=20)
            if rows:
                ws.append_row(list(rows[0].keys()))
        # Clear existing data (keep header) and write fresh
        if ws.row_count > 1:
            ws.delete_rows(2, ws.row_count)
        for row in rows:
            ws.append_row(list(row.values()))
        log.info("Sheets: wrote %d rows to %s", len(rows), tab_name)


def main():
    """CLI entry point."""
    try:
        sys.exit(run_nightly())
    except Exception as e:
        log.error("Ouroboros v6.0 crashed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
