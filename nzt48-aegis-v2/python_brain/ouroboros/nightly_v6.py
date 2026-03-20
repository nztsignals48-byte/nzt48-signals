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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
REPORTS_DIR = DATA_DIR / "ouroboros_reports"
RECS_FILE = DATA_DIR / "ouroboros_recommendations.json"

PRIMARY_TICKERS = [
    "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L", "TSL3.L",
    "TSM3.L", "MU2.L", "QQQS.L", "3USS.L", "QQQ5.L", "5SPY.L",
]

TICKER_ID_MAP = {sym: i for i, sym in enumerate(PRIMARY_TICKERS)}

# Guardrails
KELLY_MIN = 0.15
KELLY_MAX = 0.30
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
                        trades.append(TradeRecord(
                            ticker=pc.get("symbol", f"TID_{pc.get('ticker_id', '?')}"),
                            ticker_id=pc.get("ticker_id", -1),
                            pnl=pc.get("final_pnl", 0.0),
                            entry_price=pc.get("entry_price", 0.0),
                            exit_price=pc.get("exit_price", 0.0),
                            entry_time_ns=pc.get("entry_time_ns", 0),
                            exit_time_ns=pc.get("exit_time_ns", 0),
                            entry_type=pc.get("entry_type", "TypeA"),
                            # Read strategy/regime/confidence/rung from enriched WAL fields
                            strategy=pc.get("strategy", "VanguardSniper"),
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
    metrics.losses = sum(1 for t in trades if t.pnl <= 0)
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
        regime_stats = getattr(mem, 'regime_stats', {})
        regime_scales = {}
        for regime_name, rstats in regime_stats.items():
            rtrades = rstats.get('total_trades', 0)
            if rtrades >= 5:
                rwr = rstats.get('win_rate', 0.5)
                # Scale: 0.5 at 20% WR → 1.0 at 50% WR → 1.5 at 80% WR
                scale = 0.5 + (rwr - 0.2) * (1.0 / 0.6)
                regime_scales[regime_name] = max(0.4, min(1.5, scale))
                recs["adjustments"].append(
                    f"Regime '{regime_name}' scale={scale:.2f} (WR={rwr:.0%} over {rtrades} trades)"
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
    if metrics.total_trades >= 3:
        # Count cost-impaired trades from per_ticker metrics (proxy until taxonomy is wired)
        # When taxonomy data is available via trade_class, use that instead.
        loser_count = metrics.losses
        if loser_count > 0 and metrics.total_trades > 0:
            loss_rate = loser_count / metrics.total_trades
            avg_loss_size = abs(sum(
                d.get("total_pnl", 0) for d in metrics.per_ticker.values() if d.get("total_pnl", 0) < 0
            )) / max(loser_count, 1)
            # If average loss is tiny (< GBP 5), these are likely spread victims / noise exits
            if avg_loss_size < 5.0 and loss_rate > 0.40:
                cost_penalty = 0.97  # 3% Kelly reduction per day of high cost drag
                new_kelly = max(new_kelly * cost_penalty, KELLY_MIN)
                recs["adjustments"].append(
                    f"N1a COST PENALTY: Kelly *0.97 -> {new_kelly:.3f} "
                    f"(avg_loss=GBP {avg_loss_size:.2f} < 5, loss_rate={loss_rate:.0%})"
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
# Main Entry Point
# ---------------------------------------------------------------------------
def run_nightly() -> int:
    """Execute the complete Ouroboros v6.0 nightly loop."""
    start = time.monotonic()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info("Ouroboros v6.0 nightly run starting for %s", today)

    # Step 1: Load and analyze today's trades
    trades = load_todays_trades(today)
    metrics = analyze_trades(trades, today)
    log.info(
        "Trade analysis: %d trades, WR=%.1f%%, PF=%.2f, PnL=GBP %.2f",
        metrics.total_trades, metrics.win_rate * 100, metrics.profit_factor, metrics.total_pnl,
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
            mem.record_trade(
                symbol=t.ticker, pnl=t.pnl, rung=t.rung_achieved,
                kelly=recommendations.get("kelly_fraction", 0.2),
                regime=t.regime_at_entry,
                exchange=getattr(t, 'exchange', ''),  # AUDIT-FIX: pass exchange from WAL
                confidence=t.confidence, strategy=t.strategy,
            )
        mem.record_session(
            date=today, trades=metrics.total_trades, exits=metrics.total_trades,
            pnl=metrics.total_pnl, win_rate=metrics.win_rate,
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
