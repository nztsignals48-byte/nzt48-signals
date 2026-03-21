"""N10w-N10hh — Post-Trade Diagnostics Suite (P2).

Comprehensive diagnostic modules that require 100+ trades for statistical validity.
All items from AEGIS_V2_MASTER_PLAN Phase 10 diagnostics:

  N10w:  Implementation shortfall in basis points
  N10x:  MAE/MFE normalized by intraday ATR
  N10y:  Sortino/Calmar as Ouroboros optimization targets
  N10z:  Session quality metrics by 30-minute bucket
  N10aa: Drawdown velocity check (X% in Y min → HALT)
  N10bb: IC decay tracking per indicator
  N10dd: Config checksum echo in session header
  N10ee: Signal tradeability classification
  N10gg: Tighten erroneous tick filter to MAD-based 3%

QUARANTINE: Read-only analysis. Never writes to WAL, config, or live parameters.

Usage:
    python3 -m python_brain.ouroboros.post_trade_diagnostics              # Full report
    python3 -m python_brain.ouroboros.post_trade_diagnostics --module X   # Specific module
    python3 -m python_brain.ouroboros.post_trade_diagnostics --days 60    # Lookback
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("ouroboros.post_trade_diagnostics")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))


# ---------------------------------------------------------------------------
# Shared WAL loader
# ---------------------------------------------------------------------------
def _load_position_closed(wal_dir: Path, days: int) -> List[Dict]:
    """Load PositionClosed events from WAL."""
    cutoff_ns = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1e9)
    trades = []
    wal_files = [wal_dir / "current.ndjson"]
    archive = wal_dir / "archive"
    if archive.exists():
        wal_files.extend(sorted(archive.glob("*.ndjson")))
    for f in sorted(wal_dir.glob("*.ndjson")):
        if f.name != "current.ndjson" and f not in wal_files:
            wal_files.append(f)

    for wp in wal_files:
        if not wp.exists():
            continue
        try:
            with open(wp) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("event_time_ns", 0) < cutoff_ns:
                        continue
                    payload = ev.get("payload", {})
                    if "PositionClosed" in payload:
                        pc = payload["PositionClosed"]
                        pc["_event_time_ns"] = ev.get("event_time_ns", 0)
                        trades.append(pc)
        except IOError:
            pass
    return trades


# ===========================================================================
# N10w: Implementation Shortfall in Basis Points
# ===========================================================================
@dataclass
class ImplementationShortfall:
    """Measures the gap between signal price and actual fill price in bps."""
    total_trades: int = 0
    avg_shortfall_bps: float = 0.0
    median_shortfall_bps: float = 0.0
    worst_shortfall_bps: float = 0.0
    by_ticker: Dict[str, float] = field(default_factory=dict)
    by_session: Dict[str, float] = field(default_factory=dict)


def compute_implementation_shortfall(trades: List[Dict]) -> ImplementationShortfall:
    """N10w: Compute implementation shortfall — gap between signal and fill.

    Shortfall = (fill_price - signal_price) / signal_price * 10000 (in bps)
    For buys: positive = paid more than signal price (bad)
    For sells: positive = received less than signal price (bad)
    """
    shortfalls: List[float] = []
    by_ticker: Dict[str, List[float]] = defaultdict(list)
    by_session: Dict[str, List[float]] = defaultdict(list)

    for t in trades:
        entry_price = t.get("entry_price", 0)
        # Implementation shortfall approximation using spread at entry
        spread_pct = t.get("spread_at_entry_pct", 0)
        if entry_price <= 0:
            continue
        # Half-spread is the shortfall per side (mid-to-fill distance)
        shortfall_bps = spread_pct * 100 / 2  # pct → bps, half-spread
        shortfalls.append(shortfall_bps)

        symbol = t.get("symbol", "?")
        session = t.get("entry_session_phase", "unknown")
        by_ticker[symbol].append(shortfall_bps)
        by_session[session].append(shortfall_bps)

    if not shortfalls:
        return ImplementationShortfall()

    arr = np.array(shortfalls)
    return ImplementationShortfall(
        total_trades=len(shortfalls),
        avg_shortfall_bps=round(float(np.mean(arr)), 2),
        median_shortfall_bps=round(float(np.median(arr)), 2),
        worst_shortfall_bps=round(float(np.max(arr)), 2),
        by_ticker={k: round(float(np.mean(v)), 2) for k, v in sorted(by_ticker.items())},
        by_session={k: round(float(np.mean(v)), 2) for k, v in sorted(by_session.items())},
    )


# ===========================================================================
# N10x: MAE/MFE Normalized by Intraday ATR
# ===========================================================================
@dataclass
class NormalizedMAEMFE:
    """MAE/MFE expressed as multiples of ATR for comparability."""
    total_trades: int = 0
    avg_mae_atr_multiple: float = 0.0
    avg_mfe_atr_multiple: float = 0.0
    median_mae_atr: float = 0.0
    median_mfe_atr: float = 0.0
    # Ratio: how much of MFE do we capture on average?
    avg_capture_ratio: float = 0.0
    by_trade_class: Dict[str, Dict[str, float]] = field(default_factory=dict)


def compute_normalized_mae_mfe(trades: List[Dict]) -> NormalizedMAEMFE:
    """N10x: Normalize MAE/MFE by intraday ATR for cross-ticker comparison."""
    mae_atrs: List[float] = []
    mfe_atrs: List[float] = []
    capture_ratios: List[float] = []
    by_class: Dict[str, List[Tuple[float, float]]] = defaultdict(list)

    for t in trades:
        mae = abs(t.get("mae", 0))
        mfe = abs(t.get("mfe", 0.0001))
        atr_pct = t.get("atr_pct_at_entry", 0)
        entry_price = t.get("entry_price", 1)
        qty = t.get("qty", 1)

        if atr_pct <= 0 or entry_price <= 0:
            continue

        atr_value = atr_pct / 100 * entry_price * qty
        if atr_value <= 0:
            continue

        mae_atr = mae / atr_value
        mfe_atr = mfe / atr_value
        pnl = t.get("final_pnl", 0)
        capture = pnl / mfe if mfe > 0 and pnl > 0 else 0

        mae_atrs.append(mae_atr)
        mfe_atrs.append(mfe_atr)
        capture_ratios.append(capture)

        tc = t.get("trade_class", "unknown")
        by_class[tc].append((mae_atr, mfe_atr))

    if not mae_atrs:
        return NormalizedMAEMFE()

    return NormalizedMAEMFE(
        total_trades=len(mae_atrs),
        avg_mae_atr_multiple=round(float(np.mean(mae_atrs)), 3),
        avg_mfe_atr_multiple=round(float(np.mean(mfe_atrs)), 3),
        median_mae_atr=round(float(np.median(mae_atrs)), 3),
        median_mfe_atr=round(float(np.median(mfe_atrs)), 3),
        avg_capture_ratio=round(float(np.mean(capture_ratios)), 3),
        by_trade_class={
            tc: {
                "avg_mae_atr": round(float(np.mean([x[0] for x in pairs])), 3),
                "avg_mfe_atr": round(float(np.mean([x[1] for x in pairs])), 3),
                "count": len(pairs),
            }
            for tc, pairs in sorted(by_class.items())
        },
    )


# ===========================================================================
# N10y: Sortino & Calmar Ratios as Ouroboros Optimization Targets
# ===========================================================================
@dataclass
class RiskAdjustedMetrics:
    """Sortino, Calmar, and related risk-adjusted performance metrics."""
    total_trades: int = 0
    sharpe_ratio: float = 0.0        # Annualized
    sortino_ratio: float = 0.0       # Penalizes downside only
    calmar_ratio: float = 0.0        # Return / max drawdown
    omega_ratio: float = 0.0         # P(gain) / P(loss) weighted
    max_drawdown_pct: float = 0.0
    avg_daily_return_pct: float = 0.0
    downside_deviation: float = 0.0
    upside_capture: float = 0.0


def compute_risk_adjusted_metrics(trades: List[Dict], equity: float = 10_000) -> RiskAdjustedMetrics:
    """N10y: Compute Sortino, Calmar, Omega ratios."""
    if not trades:
        return RiskAdjustedMetrics()

    # Build daily returns series
    daily_pnl: Dict[str, float] = defaultdict(float)
    for t in trades:
        ns = t.get("_event_time_ns", 0)
        if ns:
            try:
                dt = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
                day_key = dt.strftime("%Y-%m-%d")
            except (OSError, ValueError):
                day_key = "unknown"
        else:
            day_key = "unknown"
        daily_pnl[day_key] += t.get("final_pnl", 0)

    if not daily_pnl or "unknown" in daily_pnl and len(daily_pnl) == 1:
        return RiskAdjustedMetrics(total_trades=len(trades))

    returns = np.array(sorted(daily_pnl.values()))
    returns_pct = returns / equity * 100

    avg_return = float(np.mean(returns_pct))
    std_return = float(np.std(returns_pct, ddof=1)) if len(returns_pct) > 1 else 1.0

    # Sharpe (annualized, 252 trading days)
    sharpe = (avg_return / std_return) * np.sqrt(252) if std_return > 0 else 0

    # Sortino: only use downside deviation
    downside = returns_pct[returns_pct < 0]
    downside_dev = float(np.std(downside, ddof=1)) if len(downside) > 1 else 1.0
    sortino = (avg_return / downside_dev) * np.sqrt(252) if downside_dev > 0 else 0

    # Calmar: annualized return / max drawdown
    cumulative = np.cumsum(returns)
    high_water = np.maximum.accumulate(equity + cumulative)
    drawdowns = (equity + cumulative - high_water) / high_water * 100
    max_dd = abs(float(np.min(drawdowns))) if len(drawdowns) > 0 else 1.0
    annual_return = avg_return * 252
    calmar = annual_return / max_dd if max_dd > 0 else 0

    # Omega: sum of gains / sum of losses (above/below threshold=0)
    gains = returns_pct[returns_pct > 0]
    losses = returns_pct[returns_pct < 0]
    omega = float(np.sum(gains) / abs(np.sum(losses))) if len(losses) > 0 and np.sum(losses) != 0 else 0

    return RiskAdjustedMetrics(
        total_trades=len(trades),
        sharpe_ratio=round(float(sharpe), 3),
        sortino_ratio=round(float(sortino), 3),
        calmar_ratio=round(float(calmar), 3),
        omega_ratio=round(float(omega), 3),
        max_drawdown_pct=round(max_dd, 3),
        avg_daily_return_pct=round(avg_return, 4),
        downside_deviation=round(downside_dev, 4),
        upside_capture=round(float(np.mean(gains)) if len(gains) > 0 else 0, 4),
    )


# ===========================================================================
# N10z: Session Quality Metrics by 30-Minute Bucket
# ===========================================================================
@dataclass
class SessionBucketMetrics:
    """Performance metrics per 30-min time bucket."""
    buckets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    best_bucket: str = ""
    worst_bucket: str = ""


def compute_session_buckets(trades: List[Dict]) -> SessionBucketMetrics:
    """N10z: Break session into 30-min buckets and compute WR/PnL per bucket."""
    by_bucket: Dict[str, List[Dict]] = defaultdict(list)

    for t in trades:
        ns = t.get("entry_time_ns", t.get("_event_time_ns", 0))
        if not ns:
            continue
        try:
            dt = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
            # Round to 30-min bucket
            bucket_min = (dt.minute // 30) * 30
            bucket_key = f"{dt.hour:02d}:{bucket_min:02d}"
            by_bucket[bucket_key].append(t)
        except (OSError, ValueError):
            continue

    buckets: Dict[str, Dict[str, Any]] = {}
    for bucket, bucket_trades in sorted(by_bucket.items()):
        n = len(bucket_trades)
        wins = sum(1 for t in bucket_trades if t.get("final_pnl", 0) > 0)
        total_pnl = sum(t.get("final_pnl", 0) for t in bucket_trades)
        wr = wins / n if n > 0 else 0
        buckets[bucket] = {
            "trades": n,
            "wins": wins,
            "win_rate": round(wr, 4),
            "total_pnl": round(total_pnl, 4),
            "avg_pnl": round(total_pnl / n, 4) if n > 0 else 0,
        }

    best = max(buckets.items(), key=lambda x: x[1]["win_rate"]) if buckets else ("", {})
    worst = min(buckets.items(), key=lambda x: x[1]["win_rate"]) if buckets else ("", {})

    return SessionBucketMetrics(
        buckets=buckets,
        best_bucket=best[0],
        worst_bucket=worst[0],
    )


# ===========================================================================
# N10aa: Drawdown Velocity Check
# ===========================================================================
@dataclass
class DrawdownVelocity:
    """Track how fast drawdowns develop — fast drawdowns are more dangerous."""
    max_velocity_pct_per_min: float = 0.0
    avg_velocity_pct_per_min: float = 0.0
    fast_drawdown_count: int = 0       # DD >2% in <30 min
    suggested_halt_threshold: str = ""


def compute_drawdown_velocity(trades: List[Dict], equity: float = 10_000) -> DrawdownVelocity:
    """N10aa: Measure drawdown velocity for HALT trigger calibration."""
    if not trades:
        return DrawdownVelocity()

    # Sort trades by time
    sorted_trades = sorted(trades, key=lambda t: t.get("_event_time_ns", 0))

    # Compute rolling equity and drawdown between consecutive losses
    velocities: List[float] = []
    fast_dd_count = 0

    for i in range(1, len(sorted_trades)):
        curr = sorted_trades[i]
        prev = sorted_trades[i - 1]
        curr_pnl = curr.get("final_pnl", 0)
        prev_pnl = prev.get("final_pnl", 0)

        if curr_pnl >= 0:
            continue  # Only care about losses

        curr_ns = curr.get("_event_time_ns", 0)
        prev_ns = prev.get("_event_time_ns", 0)

        if curr_ns <= prev_ns or prev_ns == 0:
            continue

        time_diff_min = (curr_ns - prev_ns) / 60_000_000_000
        if time_diff_min <= 0:
            continue

        # Consecutive loss accumulation
        dd_pct = abs(curr_pnl) / equity * 100
        velocity = dd_pct / time_diff_min  # %/min

        velocities.append(velocity)

        if dd_pct > 2.0 and time_diff_min < 30:
            fast_dd_count += 1

    if not velocities:
        return DrawdownVelocity()

    max_vel = float(np.max(velocities))
    avg_vel = float(np.mean(velocities))

    # Suggest halt threshold: 2× average velocity
    suggested = f"{min(avg_vel * 2, 0.5):.3f}% per minute"

    return DrawdownVelocity(
        max_velocity_pct_per_min=round(max_vel, 4),
        avg_velocity_pct_per_min=round(avg_vel, 4),
        fast_drawdown_count=fast_dd_count,
        suggested_halt_threshold=suggested,
    )


# ===========================================================================
# N10bb: IC (Information Coefficient) Decay Tracking
# ===========================================================================
@dataclass
class ICDecay:
    """Track how indicator predictive power decays over time."""
    indicators: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def compute_ic_decay(trades: List[Dict]) -> ICDecay:
    """N10bb: Measure IC (rank correlation of indicator vs PnL) in weekly windows."""
    if len(trades) < 20:
        return ICDecay()

    indicators = ["entry_rvol", "entry_hurst", "entry_adx", "confidence"]

    # Sort by time and split into weekly windows
    sorted_trades = sorted(trades, key=lambda t: t.get("_event_time_ns", 0))

    # Split into windows of ~20 trades
    window_size = max(20, len(sorted_trades) // 5)
    windows = []
    for i in range(0, len(sorted_trades), window_size):
        windows.append(sorted_trades[i:i + window_size])

    result: Dict[str, Dict[str, Any]] = {}

    for indicator in indicators:
        window_ics: List[float] = []
        for window in windows:
            ind_vals = []
            pnl_vals = []
            for t in window:
                val = t.get(indicator)
                pnl = t.get("final_pnl")
                if val is not None and pnl is not None:
                    try:
                        ind_vals.append(float(val))
                        pnl_vals.append(float(pnl))
                    except (ValueError, TypeError):
                        continue

            if len(ind_vals) < 10:
                continue

            # Rank correlation (Spearman's rho approximation)
            ind_arr = np.array(ind_vals)
            pnl_arr = np.array(pnl_vals)
            try:
                ind_ranks = np.argsort(np.argsort(ind_arr)).astype(float)
                pnl_ranks = np.argsort(np.argsort(pnl_arr)).astype(float)
                n = len(ind_ranks)
                d = ind_ranks - pnl_ranks
                rho = 1 - (6 * np.sum(d**2)) / (n * (n**2 - 1))
                window_ics.append(float(rho))
            except Exception:
                continue

        if window_ics:
            result[indicator] = {
                "windows": len(window_ics),
                "ic_values": [round(ic, 4) for ic in window_ics],
                "current_ic": round(window_ics[-1], 4) if window_ics else 0,
                "ic_trend": "decaying" if len(window_ics) >= 3 and window_ics[-1] < window_ics[0] else "stable",
                "avg_ic": round(float(np.mean(window_ics)), 4),
            }

    return ICDecay(indicators=result)


# ===========================================================================
# N10dd: Config Checksum Echo
# ===========================================================================
def compute_config_checksum(config_dir: Path = CONFIG_DIR) -> Dict[str, str]:
    """N10dd: Generate checksums for all config files for session header echo."""
    checksums: Dict[str, str] = {}
    config_files = [
        "config.toml",
        "contracts.toml",
        "dynamic_weights.toml",
        "universe_classification.toml",
    ]
    for fname in config_files:
        fpath = config_dir / fname
        if fpath.exists():
            try:
                content = fpath.read_bytes()
                checksums[fname] = hashlib.md5(content).hexdigest()[:8]
            except IOError:
                checksums[fname] = "error"
        else:
            checksums[fname] = "missing"
    return checksums


# ===========================================================================
# N10ee: Signal Tradeability Classification
# ===========================================================================
@dataclass
class TradeabilityScore:
    """Structural tradeability score for each signal."""
    symbol: str
    score: float = 0.0     # 0-100
    components: Dict[str, float] = field(default_factory=dict)


def compute_tradeability_scores(trades: List[Dict]) -> Dict[str, Dict[str, Any]]:
    """N10ee: Classify signals by structural tradeability.

    Score 0-100 based on:
      - Spread quality (30%): lower spread = higher score
      - Volume quality (20%): higher RVOL = higher score
      - Trend quality (20%): Hurst > 0.5 = higher score
      - Confidence (15%): higher confidence = higher score
      - Session quality (15%): morning > afternoon
    """
    by_ticker: Dict[str, List[Dict]] = defaultdict(list)
    for t in trades:
        by_ticker[t.get("symbol", "?")].append(t)

    result: Dict[str, Dict[str, Any]] = {}
    for symbol, ticker_trades in sorted(by_ticker.items()):
        spreads = [t.get("spread_at_entry_pct", 0.5) for t in ticker_trades]
        rvols = [t.get("entry_rvol", 1.0) for t in ticker_trades if t.get("entry_rvol")]
        hursts = [t.get("entry_hurst", 0.5) for t in ticker_trades if t.get("entry_hurst")]
        confs = [t.get("confidence", 0.5) for t in ticker_trades]

        # Score components (0-100 each)
        avg_spread = float(np.mean(spreads)) if spreads else 0.5
        spread_score = max(0, min(100, (1 - avg_spread / 0.5) * 100))

        avg_rvol = float(np.mean(rvols)) if rvols else 1.0
        volume_score = min(100, avg_rvol / 3.0 * 100)

        avg_hurst = float(np.mean(hursts)) if hursts else 0.5
        trend_score = min(100, max(0, (avg_hurst - 0.3) / 0.4 * 100))

        avg_conf = float(np.mean(confs)) if confs else 0.5
        conf_score = min(100, avg_conf * 100)

        # Session score (morning trades tend to be better)
        morning_count = sum(1 for t in ticker_trades if t.get("entry_session_phase", "") in ("morning", "open_auction"))
        session_score = min(100, morning_count / max(len(ticker_trades), 1) * 200)

        total = (spread_score * 0.30 + volume_score * 0.20 +
                 trend_score * 0.20 + conf_score * 0.15 + session_score * 0.15)

        result[symbol] = {
            "score": round(total, 1),
            "trades": len(ticker_trades),
            "components": {
                "spread": round(spread_score, 1),
                "volume": round(volume_score, 1),
                "trend": round(trend_score, 1),
                "confidence": round(conf_score, 1),
                "session": round(session_score, 1),
            },
            "classification": "excellent" if total >= 70 else "good" if total >= 50 else "marginal" if total >= 30 else "poor",
        }

    return result


# ===========================================================================
# N10gg: MAD-Based Erroneous Tick Filter
# ===========================================================================
def compute_mad_tick_thresholds(trades: List[Dict]) -> Dict[str, Dict[str, float]]:
    """N10gg: Compute MAD-based (Median Absolute Deviation) tick filter thresholds.

    Replace fixed 5% erroneous tick filter with adaptive MAD-based 3σ filter.
    MAD is more robust to outliers than standard deviation.
    """
    by_ticker: Dict[str, List[float]] = defaultdict(list)

    for t in trades:
        symbol = t.get("symbol", "?")
        pnl_pct = t.get("final_pnl", 0) / max(t.get("entry_price", 1) * t.get("qty", 1), 1) * 100
        by_ticker[symbol].append(pnl_pct)

    result: Dict[str, Dict[str, float]] = {}
    for symbol, pnl_pcts in sorted(by_ticker.items()):
        if len(pnl_pcts) < 10:
            continue
        arr = np.array(pnl_pcts)
        median = float(np.median(arr))
        mad = float(np.median(np.abs(arr - median)))

        # 3σ equivalent for MAD: threshold = median ± 3 × 1.4826 × MAD
        k = 1.4826  # Scale factor for normal distribution equivalence
        lower = median - 3 * k * mad
        upper = median + 3 * k * mad

        outliers = int(np.sum((arr < lower) | (arr > upper)))

        result[symbol] = {
            "median_pnl_pct": round(median, 4),
            "mad": round(mad, 4),
            "lower_bound_pct": round(lower, 4),
            "upper_bound_pct": round(upper, 4),
            "outlier_count": outliers,
            "outlier_pct": round(outliers / len(pnl_pcts) * 100, 2),
        }

    return result


# ===========================================================================
# Full Diagnostic Report
# ===========================================================================
@dataclass
class DiagnosticReport:
    """Complete post-trade diagnostic report."""
    analysis_date: str
    lookback_days: int
    total_trades: int
    implementation_shortfall: Dict[str, Any] = field(default_factory=dict)
    normalized_mae_mfe: Dict[str, Any] = field(default_factory=dict)
    risk_adjusted_metrics: Dict[str, Any] = field(default_factory=dict)
    session_buckets: Dict[str, Any] = field(default_factory=dict)
    drawdown_velocity: Dict[str, Any] = field(default_factory=dict)
    ic_decay: Dict[str, Any] = field(default_factory=dict)
    config_checksums: Dict[str, str] = field(default_factory=dict)
    tradeability_scores: Dict[str, Any] = field(default_factory=dict)
    mad_tick_thresholds: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


def run_full_diagnostics(
    wal_dir: Path = WAL_DIR,
    days: int = 30,
    equity: float = 10_000.0,
) -> DiagnosticReport:
    """Run all diagnostic modules and produce consolidated report."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info("Post-trade diagnostics starting (%d-day lookback)", days)

    trades = _load_position_closed(wal_dir, days)
    log.info("Loaded %d trades", len(trades))

    report = DiagnosticReport(
        analysis_date=today,
        lookback_days=days,
        total_trades=len(trades),
    )

    if not trades:
        log.warning("No trades — diagnostics will be empty")
        report.config_checksums = compute_config_checksum()
        return report

    # Run each diagnostic module
    log.info("  N10w: Implementation shortfall...")
    report.implementation_shortfall = asdict(compute_implementation_shortfall(trades))

    log.info("  N10x: Normalized MAE/MFE...")
    report.normalized_mae_mfe = asdict(compute_normalized_mae_mfe(trades))

    log.info("  N10y: Risk-adjusted metrics (Sortino/Calmar)...")
    report.risk_adjusted_metrics = asdict(compute_risk_adjusted_metrics(trades, equity))

    log.info("  N10z: Session buckets...")
    report.session_buckets = asdict(compute_session_buckets(trades))

    log.info("  N10aa: Drawdown velocity...")
    report.drawdown_velocity = asdict(compute_drawdown_velocity(trades, equity))

    log.info("  N10bb: IC decay tracking...")
    report.ic_decay = asdict(compute_ic_decay(trades))

    log.info("  N10dd: Config checksums...")
    report.config_checksums = compute_config_checksum()

    log.info("  N10ee: Tradeability scores...")
    report.tradeability_scores = compute_tradeability_scores(trades)

    log.info("  N10gg: MAD tick thresholds...")
    report.mad_tick_thresholds = compute_mad_tick_thresholds(trades)

    log.info("Diagnostics complete: %d modules run", 9)
    return report


def save_diagnostics(report: DiagnosticReport, output_dir: Path = DATA_DIR) -> Path:
    """Save diagnostic report to JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"diagnostics_{report.analysis_date}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(report.to_json(), encoding="utf-8")
    os.rename(str(tmp), str(path))
    log.info("Diagnostics saved: %s", path)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [Diagnostics] %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="N10w-N10hh Post-Trade Diagnostics Suite")
    parser.add_argument("--days", type=int, default=30, help="Lookback days")
    parser.add_argument("--equity", type=float, default=10000.0, help="Current equity")
    parser.add_argument("--wal-dir", type=str, default=str(WAL_DIR))
    args = parser.parse_args()

    report = run_full_diagnostics(Path(args.wal_dir), args.days, args.equity)
    save_diagnostics(report)

    # Print summary
    print(f"\nPost-Trade Diagnostics ({report.analysis_date})")
    print(f"  Trades: {report.total_trades}")
    print(f"  Lookback: {report.lookback_days} days")

    is_data = report.implementation_shortfall
    if is_data.get("total_trades", 0) > 0:
        print(f"\n  N10w Shortfall: avg={is_data['avg_shortfall_bps']:.1f} bps, worst={is_data['worst_shortfall_bps']:.1f} bps")

    ram = report.risk_adjusted_metrics
    if ram.get("total_trades", 0) > 0:
        print(f"\n  N10y Risk-Adjusted:")
        print(f"    Sharpe:  {ram['sharpe_ratio']:.3f}")
        print(f"    Sortino: {ram['sortino_ratio']:.3f}")
        print(f"    Calmar:  {ram['calmar_ratio']:.3f}")
        print(f"    Omega:   {ram['omega_ratio']:.3f}")
        print(f"    Max DD:  {ram['max_drawdown_pct']:.2f}%")

    sb = report.session_buckets
    if sb.get("best_bucket"):
        print(f"\n  N10z Best bucket: {sb['best_bucket']}, Worst: {sb['worst_bucket']}")

    dd = report.drawdown_velocity
    if dd.get("max_velocity_pct_per_min", 0) > 0:
        print(f"\n  N10aa DD Velocity: max={dd['max_velocity_pct_per_min']:.4f}%/min, "
              f"fast DDs={dd['fast_drawdown_count']}")
        print(f"    Suggested HALT: {dd['suggested_halt_threshold']}")

    print(f"\n  N10dd Config checksums: {report.config_checksums}")


if __name__ == "__main__":
    main()
