"""Ouroboros Indicator Intelligence — learns which indicator conditions predict wins.

Reads WAL PositionClosed events (enriched with entry_rvol, entry_hurst, entry_adx)
and RoutedOrder fallback, separates winners vs losers, and computes per-indicator
statistics to discover which indicator ranges predict profitable trades.

Outputs:
  - JSON summary consumed by nightly_v6 for parameter adjustment
  - Data suitable for pushing to Google Sheets (Indicator_Intelligence tab)
  - Recommended filter thresholds derived from historical win/loss distributions

Quarantine rules (same as nightly_v6):
  - NEVER writes to live WAL
  - NEVER influences live decisions in-session
  - Reads ONLY the finished day's journal

Usage:
    from python_brain.ouroboros.indicator_intelligence import analyze_indicators
    result = analyze_indicators(wal_dir=Path("events"), days=30)
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("ouroboros.indicator_intelligence")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", _PROJECT_ROOT / "events"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))

# Indicators we track (field names in WAL PositionClosed / RoutedOrder events)
TRACKED_INDICATORS = [
    "entry_rvol",
    "entry_hurst",
    "entry_adx",
    "entry_atr_pct",
    "entry_spread_pct",
    "confidence",
]

# Minimum sample size for statistical significance
MIN_TRADES_FOR_STATS = 5
MIN_TRADES_FOR_RULES = 10

# Default threshold candidates for rule discovery
DEFAULT_THRESHOLDS = {
    "entry_rvol":       [0.5, 1.0, 1.5, 2.0, 3.0, 5.0],
    "entry_hurst":      [0.3, 0.4, 0.5, 0.6, 0.7],
    "entry_adx":        [10, 15, 20, 25, 30, 40],
    "entry_atr_pct":    [0.5, 1.0, 1.5, 2.0, 3.0, 5.0],
    "entry_spread_pct": [0.05, 0.10, 0.20, 0.50],
    "confidence":       [0.3, 0.5, 0.6, 0.7, 0.8],
}

# Session phase boundaries (UTC hours for LSE — mirrors nightly_v6.py)
SESSION_BOUNDARIES = {
    "open_auction": (0, 8),
    "morning":      (8, 10),
    "midday":       (10, 13),
    "afternoon":    (13, 16),
    "close_auction": (16, 24),
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class IndicatorStats:
    """Statistics for one indicator across a set of trades."""
    indicator: str
    count: int = 0
    mean: float = 0.0
    median: float = 0.0
    std: float = 0.0
    min_val: float = 0.0
    max_val: float = 0.0
    p25: float = 0.0
    p75: float = 0.0


@dataclass
class ThresholdRule:
    """A learned rule: above/below a threshold, what is the win rate?"""
    indicator: str
    direction: str         # "above" or "below"
    threshold: float
    trades_matching: int
    wins_matching: int
    win_rate: float
    avg_pnl: float
    # Context: what about trades NOT matching?
    trades_other: int
    win_rate_other: float
    lift: float            # win_rate - win_rate_other (positive = this threshold helps)


@dataclass
class IndicatorIntelligence:
    """Complete output of indicator intelligence analysis."""
    analysis_date: str
    lookback_days: int
    total_trades: int
    total_wins: int
    total_losses: int
    overall_win_rate: float

    # Per-indicator stats for winners vs losers
    winning_indicators: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    losing_indicators: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Regime performance
    regime_performance: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Session performance
    session_performance: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Discovered rules (sorted by lift, descending)
    rules: List[Dict[str, Any]] = field(default_factory=list)

    # Recommended filter thresholds
    recommended_filters: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Correlation matrix: indicator x indicator for winners
    indicator_correlations: Dict[str, Dict[str, float]] = field(default_factory=dict)

    # Per-ticker indicator performance
    ticker_indicator_summary: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(asdict(self), indent=2, default=str)

    def to_sheets_rows(self) -> Dict[str, List[List[Any]]]:
        """Convert to rows suitable for Google Sheets tabs.

        Returns dict of tab_name -> list of rows. Can be consumed by
        sheets_sync.py append_rows() or clear_and_write().
        """
        rows: Dict[str, List[List[Any]]] = {}

        # --- Indicator Stats tab ---
        indicator_rows = []
        for indicator in sorted(set(list(self.winning_indicators.keys()) +
                                    list(self.losing_indicators.keys()))):
            w = self.winning_indicators.get(indicator, {})
            l = self.losing_indicators.get(indicator, {})
            indicator_rows.append([
                self.analysis_date,
                indicator,
                w.get("count", 0),
                round(w.get("mean", 0), 4),
                round(w.get("median", 0), 4),
                round(w.get("std", 0), 4),
                l.get("count", 0),
                round(l.get("mean", 0), 4),
                round(l.get("median", 0), 4),
                round(l.get("std", 0), 4),
            ])
        rows["Indicator_Stats"] = indicator_rows

        # --- Regime Performance tab ---
        regime_rows = []
        for regime, data in sorted(self.regime_performance.items()):
            regime_rows.append([
                self.analysis_date,
                regime,
                data.get("trades", 0),
                data.get("wins", 0),
                round(data.get("win_rate", 0), 4),
                round(data.get("avg_pnl", 0), 4),
                round(data.get("total_pnl", 0), 4),
            ])
        rows["Regime_Performance"] = regime_rows

        # --- Session Performance tab ---
        session_rows = []
        for session, data in sorted(self.session_performance.items()):
            session_rows.append([
                self.analysis_date,
                session,
                data.get("trades", 0),
                data.get("wins", 0),
                round(data.get("win_rate", 0), 4),
                round(data.get("avg_pnl", 0), 4),
            ])
        rows["Session_Performance"] = session_rows

        # --- Learned Rules tab ---
        rule_rows = []
        for rule in self.rules[:30]:  # Top 30 rules
            rule_rows.append([
                self.analysis_date,
                rule.get("indicator", ""),
                rule.get("direction", ""),
                round(rule.get("threshold", 0), 4),
                rule.get("trades_matching", 0),
                round(rule.get("win_rate", 0), 4),
                round(rule.get("lift", 0), 4),
                round(rule.get("avg_pnl", 0), 4),
            ])
        rows["Learned_Rules"] = rule_rows

        return rows


# ---------------------------------------------------------------------------
# WAL reader — loads trades with indicator fields
# ---------------------------------------------------------------------------
@dataclass
class EnrichedTrade:
    """A trade with indicator values at entry time."""
    ticker: str
    ticker_id: int
    pnl: float
    entry_time_ns: int
    exit_time_ns: int
    regime: str
    session_phase: str
    confidence: float
    entry_type: str
    strategy: str
    # Indicator values (None = not available)
    entry_rvol: Optional[float] = None
    entry_hurst: Optional[float] = None
    entry_adx: Optional[float] = None
    entry_atr_pct: Optional[float] = None
    entry_spread_pct: Optional[float] = None

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0


def _classify_session_phase(entry_time_ns: int) -> str:
    """Classify entry timestamp into LSE session phase."""
    if entry_time_ns == 0:
        return "unknown"
    try:
        dt = datetime.fromtimestamp(entry_time_ns / 1e9, tz=timezone.utc)
        hour = dt.hour
        for phase, (start_h, end_h) in SESSION_BOUNDARIES.items():
            if start_h <= hour < end_h:
                return phase
        return "unknown"
    except (OSError, ValueError):
        return "unknown"


def _extract_indicator_from_event(
    payload_data: dict,
    indicator: str,
) -> Optional[float]:
    """Safely extract a numeric indicator value from a WAL event payload."""
    val = payload_data.get(indicator)
    if val is None:
        return None
    try:
        fval = float(val)
        if np.isnan(fval) or np.isinf(fval):
            return None
        return fval
    except (ValueError, TypeError):
        return None


def _load_all_wal_events(wal_dir: Path, cutoff_ns: int) -> List[dict]:
    """Load ALL WAL events from current + archive ndjson files since cutoff.

    Per project gotcha: WAL ARCHIVE FIX — ALL Python modules that read WAL
    MUST scan archive/*.ndjson (engine rotates on restart).
    """
    events: List[dict] = []
    wal_candidates = [wal_dir / "current.ndjson"]

    # Scan archive directory for all .ndjson files
    archive_dir = wal_dir / "archive"
    if archive_dir.exists():
        for f in sorted(archive_dir.glob("*.ndjson")):
            wal_candidates.append(f)

    # Also check date-stamped files in the main wal directory
    for f in sorted(wal_dir.glob("*.ndjson")):
        if f.name != "current.ndjson" and f not in wal_candidates:
            wal_candidates.append(f)

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
                        # Filter by time: only events since cutoff
                        event_time = event.get("event_time_ns", 0)
                        if event_time >= cutoff_ns or cutoff_ns == 0:
                            events.append(event)
                    except json.JSONDecodeError:
                        continue
        except IOError as e:
            log.warning("Error reading WAL %s: %s", wal_path, e)

    return events


def _build_routed_order_index(events: List[dict]) -> Dict[str, dict]:
    """Build an index of RoutedOrder events keyed by order_id.

    Used as fallback: when PositionClosed doesn't have indicator fields,
    we look up the corresponding RoutedOrder to get entry-time indicators.
    """
    index: Dict[str, dict] = {}
    for event in events:
        payload = event.get("payload", {})
        if "RoutedOrder" in payload:
            ro = payload["RoutedOrder"]
            order_id = ro.get("order_id", "")
            if order_id:
                index[order_id] = ro
            # Also index by symbol + approximate time for fuzzy matching
            symbol = ro.get("symbol", "")
            entry_ns = event.get("event_time_ns", 0)
            if symbol and entry_ns:
                index[f"{symbol}:{entry_ns}"] = ro
    return index


def _load_enriched_trades(
    wal_dir: Path,
    days: int,
) -> List[EnrichedTrade]:
    """Load trades with indicator enrichment from WAL events.

    Strategy:
    1. Read PositionClosed events — use entry_rvol/entry_hurst/entry_adx if present
    2. For trades missing indicator fields, fall back to matching RoutedOrder event
    3. Confidence is always available (from both event types)
    """
    # Calculate cutoff timestamp
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ns = int(cutoff_dt.timestamp() * 1e9)

    all_events = _load_all_wal_events(wal_dir, cutoff_ns)
    log.info("Loaded %d WAL events within %d-day lookback", len(all_events), days)

    # Build RoutedOrder index for fallback indicator lookup
    ro_index = _build_routed_order_index(all_events)
    log.info("Built RoutedOrder index: %d entries", len(ro_index))

    trades: List[EnrichedTrade] = []
    enriched_from_pc = 0
    enriched_from_ro = 0
    no_indicators = 0

    for event in all_events:
        payload = event.get("payload", {})
        if "PositionClosed" not in payload:
            continue

        pc = payload["PositionClosed"]
        symbol = pc.get("symbol", f"TID_{pc.get('ticker_id', '?')}")
        entry_time_ns = pc.get("entry_time_ns", 0)

        # --- Extract indicators from PositionClosed first ---
        indicators = {}
        has_pc_indicators = False
        for ind in TRACKED_INDICATORS:
            if ind == "confidence":
                continue  # Handled separately
            val = _extract_indicator_from_event(pc, ind)
            if val is not None:
                has_pc_indicators = True
            indicators[ind] = val

        # --- Fallback to RoutedOrder if PositionClosed lacks indicator fields ---
        if not has_pc_indicators:
            # Try matching by entry_order_id
            entry_order_id = pc.get("entry_order_id", "")
            ro = ro_index.get(entry_order_id)

            # Fuzzy match by symbol + entry_time_ns if no order_id match
            if ro is None and symbol and entry_time_ns:
                ro = ro_index.get(f"{symbol}:{entry_time_ns}")

            if ro is not None:
                for ind in TRACKED_INDICATORS:
                    if ind == "confidence":
                        continue
                    if indicators.get(ind) is None:
                        val = _extract_indicator_from_event(ro, ind)
                        if val is not None:
                            indicators[ind] = val
                            enriched_from_ro += 1
            else:
                no_indicators += 1
        else:
            enriched_from_pc += 1

        # Confidence is always on PositionClosed
        confidence = _extract_indicator_from_event(pc, "confidence") or 0.0

        trade = EnrichedTrade(
            ticker=symbol,
            ticker_id=pc.get("ticker_id", -1),
            pnl=pc.get("final_pnl", 0.0),
            entry_time_ns=entry_time_ns,
            exit_time_ns=pc.get("exit_time_ns", 0),
            regime=pc.get("regime_at_entry", pc.get("regime", "unknown")),
            session_phase=_classify_session_phase(entry_time_ns),
            confidence=confidence,
            entry_type=pc.get("entry_type", "TypeA"),
            strategy=pc.get("strategy", "Unclassified"),
            entry_rvol=indicators.get("entry_rvol"),
            entry_hurst=indicators.get("entry_hurst"),
            entry_adx=indicators.get("entry_adx"),
            entry_atr_pct=indicators.get("entry_atr_pct"),
            entry_spread_pct=indicators.get("entry_spread_pct"),
        )
        trades.append(trade)

    log.info(
        "Loaded %d enriched trades: %d from PositionClosed, %d fields from RoutedOrder fallback, %d with no indicators",
        len(trades), enriched_from_pc, enriched_from_ro, no_indicators,
    )
    return trades


# ---------------------------------------------------------------------------
# Statistics computation
# ---------------------------------------------------------------------------
def _compute_indicator_stats(
    trades: List[EnrichedTrade],
    indicator: str,
) -> Optional[IndicatorStats]:
    """Compute descriptive statistics for one indicator across a set of trades."""
    values = []
    for t in trades:
        val = getattr(t, indicator, None)
        if val is None and indicator == "confidence":
            val = t.confidence
        if val is not None:
            values.append(val)

    if len(values) < MIN_TRADES_FOR_STATS:
        return None

    arr = np.array(values, dtype=np.float64)
    return IndicatorStats(
        indicator=indicator,
        count=len(arr),
        mean=float(np.mean(arr)),
        median=float(np.median(arr)),
        std=float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0,
        min_val=float(np.min(arr)),
        max_val=float(np.max(arr)),
        p25=float(np.percentile(arr, 25)),
        p75=float(np.percentile(arr, 75)),
    )


def _discover_threshold_rules(
    trades: List[EnrichedTrade],
    indicator: str,
    overall_win_rate: float,
) -> List[ThresholdRule]:
    """Discover threshold rules for one indicator.

    For each candidate threshold, compute win rate above and below.
    Only return rules with meaningful lift (>= 5 percentage points).
    """
    thresholds = DEFAULT_THRESHOLDS.get(indicator, [])
    if not thresholds:
        # Auto-generate thresholds from data percentiles
        values = [getattr(t, indicator, None) for t in trades]
        values = [v for v in values if v is not None]
        if len(values) < MIN_TRADES_FOR_RULES:
            return []
        arr = np.array(values)
        thresholds = [
            float(np.percentile(arr, p))
            for p in [20, 40, 60, 80]
        ]

    rules: List[ThresholdRule] = []

    for threshold in thresholds:
        for direction in ("above", "below"):
            matching = []
            other = []
            for t in trades:
                val = getattr(t, indicator, None)
                if val is None and indicator == "confidence":
                    val = t.confidence
                if val is None:
                    continue
                if direction == "above" and val > threshold:
                    matching.append(t)
                elif direction == "below" and val < threshold:
                    matching.append(t)
                else:
                    other.append(t)

            if len(matching) < MIN_TRADES_FOR_RULES:
                continue
            if len(other) < MIN_TRADES_FOR_STATS:
                continue

            wins_matching = sum(1 for t in matching if t.is_winner)
            wr_matching = wins_matching / len(matching)
            avg_pnl_matching = sum(t.pnl for t in matching) / len(matching)

            wins_other = sum(1 for t in other if t.is_winner)
            wr_other = wins_other / len(other) if other else overall_win_rate

            lift = wr_matching - wr_other

            # Only keep rules with meaningful lift
            if abs(lift) < 0.05:
                continue

            rules.append(ThresholdRule(
                indicator=indicator,
                direction=direction,
                threshold=threshold,
                trades_matching=len(matching),
                wins_matching=wins_matching,
                win_rate=wr_matching,
                avg_pnl=avg_pnl_matching,
                trades_other=len(other),
                win_rate_other=wr_other,
                lift=lift,
            ))

    return rules


def _compute_regime_performance(
    trades: List[EnrichedTrade],
) -> Dict[str, Dict[str, Any]]:
    """Compute win rate and PnL per market regime."""
    by_regime: Dict[str, List[EnrichedTrade]] = defaultdict(list)
    for t in trades:
        regime = t.regime if t.regime and t.regime != "unknown" else "unknown"
        by_regime[regime].append(t)

    result: Dict[str, Dict[str, Any]] = {}
    for regime, regime_trades in sorted(by_regime.items()):
        n = len(regime_trades)
        wins = sum(1 for t in regime_trades if t.is_winner)
        total_pnl = sum(t.pnl for t in regime_trades)
        result[regime] = {
            "trades": n,
            "wins": wins,
            "win_rate": wins / n if n > 0 else 0.0,
            "avg_pnl": total_pnl / n if n > 0 else 0.0,
            "total_pnl": total_pnl,
        }

    return result


def _compute_session_performance(
    trades: List[EnrichedTrade],
) -> Dict[str, Dict[str, Any]]:
    """Compute win rate per session phase."""
    by_session: Dict[str, List[EnrichedTrade]] = defaultdict(list)
    for t in trades:
        by_session[t.session_phase].append(t)

    result: Dict[str, Dict[str, Any]] = {}
    for session, session_trades in sorted(by_session.items()):
        n = len(session_trades)
        wins = sum(1 for t in session_trades if t.is_winner)
        total_pnl = sum(t.pnl for t in session_trades)
        result[session] = {
            "trades": n,
            "wins": wins,
            "win_rate": wins / n if n > 0 else 0.0,
            "avg_pnl": total_pnl / n if n > 0 else 0.0,
        }

    return result


def _compute_indicator_correlations(
    trades: List[EnrichedTrade],
) -> Dict[str, Dict[str, float]]:
    """Compute correlation matrix between indicators for winning trades.

    Helps identify which indicators are redundant (highly correlated)
    vs providing independent signal.
    """
    winners = [t for t in trades if t.is_winner]
    if len(winners) < MIN_TRADES_FOR_STATS * 2:
        return {}

    # Collect indicator vectors (only trades with ALL indicators present)
    available_indicators = []
    for ind in TRACKED_INDICATORS:
        values = [getattr(t, ind, None) for t in winners]
        if ind == "confidence":
            values = [t.confidence for t in winners]
        non_none = [v for v in values if v is not None]
        if len(non_none) >= MIN_TRADES_FOR_STATS:
            available_indicators.append(ind)

    if len(available_indicators) < 2:
        return {}

    # Build matrix of trades that have ALL available indicators
    matrix_rows = []
    for t in winners:
        row = []
        skip = False
        for ind in available_indicators:
            val = getattr(t, ind, None)
            if ind == "confidence":
                val = t.confidence
            if val is None:
                skip = True
                break
            row.append(val)
        if not skip:
            matrix_rows.append(row)

    if len(matrix_rows) < MIN_TRADES_FOR_STATS:
        return {}

    arr = np.array(matrix_rows, dtype=np.float64)
    # Compute correlation matrix
    try:
        corr = np.corrcoef(arr.T)
    except (ValueError, FloatingPointError):
        return {}

    result: Dict[str, Dict[str, float]] = {}
    for i, ind_i in enumerate(available_indicators):
        result[ind_i] = {}
        for j, ind_j in enumerate(available_indicators):
            val = float(corr[i, j])
            if np.isnan(val):
                val = 0.0
            result[ind_i][ind_j] = round(val, 4)

    return result


def _compute_recommended_filters(
    rules: List[ThresholdRule],
    winning_stats: Dict[str, IndicatorStats],
    losing_stats: Dict[str, IndicatorStats],
) -> Dict[str, Dict[str, Any]]:
    """Compute recommended filter thresholds from discovered rules.

    Strategy:
    - For each indicator, find the rule with the highest positive lift
    - Cross-reference with winner/loser distribution separation
    - Only recommend if confidence is high (sufficient trades + lift)
    """
    recommended: Dict[str, Dict[str, Any]] = {}

    # Group rules by indicator
    rules_by_indicator: Dict[str, List[ThresholdRule]] = defaultdict(list)
    for rule in rules:
        if rule.lift > 0:  # Only positive-lift rules
            rules_by_indicator[rule.indicator].append(rule)

    for indicator, ind_rules in rules_by_indicator.items():
        if not ind_rules:
            continue

        # Sort by lift (descending), then by trade count (descending)
        ind_rules.sort(key=lambda r: (-r.lift, -r.trades_matching))
        best_rule = ind_rules[0]

        # Confidence score: lift * sqrt(trades) — rewards both magnitude and sample size
        confidence_score = best_rule.lift * (best_rule.trades_matching ** 0.5)

        # Only recommend if confidence is meaningful
        if confidence_score < 0.5:
            continue

        w_stats = winning_stats.get(indicator)
        l_stats = losing_stats.get(indicator)

        recommended[indicator] = {
            "direction": best_rule.direction,
            "threshold": best_rule.threshold,
            "win_rate_if_applied": best_rule.win_rate,
            "lift": best_rule.lift,
            "trades_supporting": best_rule.trades_matching,
            "confidence_score": round(confidence_score, 3),
            "winner_mean": round(w_stats.mean, 4) if w_stats else None,
            "loser_mean": round(l_stats.mean, 4) if l_stats else None,
            "rule_text": (
                f"{indicator} {best_rule.direction} {best_rule.threshold:.2f} "
                f"has {best_rule.win_rate:.0%} WR across {best_rule.trades_matching} trades "
                f"(+{best_rule.lift:.0%} lift)"
            ),
        }

    return recommended


def _compute_ticker_indicator_summary(
    trades: List[EnrichedTrade],
) -> Dict[str, Dict[str, Any]]:
    """Per-ticker summary: which indicators matter most for each ticker."""
    by_ticker: Dict[str, List[EnrichedTrade]] = defaultdict(list)
    for t in trades:
        by_ticker[t.ticker].append(t)

    result: Dict[str, Dict[str, Any]] = {}
    for ticker, ticker_trades in sorted(by_ticker.items()):
        n = len(ticker_trades)
        if n < MIN_TRADES_FOR_STATS:
            continue

        wins = sum(1 for t in ticker_trades if t.is_winner)
        wr = wins / n if n > 0 else 0.0
        total_pnl = sum(t.pnl for t in ticker_trades)

        # Per-indicator mean for this ticker's winners vs losers
        indicator_diffs = {}
        winners = [t for t in ticker_trades if t.is_winner]
        losers = [t for t in ticker_trades if not t.is_winner]

        for ind in TRACKED_INDICATORS:
            w_vals = []
            l_vals = []
            for t in winners:
                v = getattr(t, ind, None)
                if ind == "confidence":
                    v = t.confidence
                if v is not None:
                    w_vals.append(v)
            for t in losers:
                v = getattr(t, ind, None)
                if ind == "confidence":
                    v = t.confidence
                if v is not None:
                    l_vals.append(v)

            if w_vals and l_vals:
                w_mean = float(np.mean(w_vals))
                l_mean = float(np.mean(l_vals))
                indicator_diffs[ind] = {
                    "winner_mean": round(w_mean, 4),
                    "loser_mean": round(l_mean, 4),
                    "separation": round(w_mean - l_mean, 4),
                }

        result[ticker] = {
            "trades": n,
            "wins": wins,
            "win_rate": round(wr, 4),
            "total_pnl": round(total_pnl, 4),
            "indicator_separation": indicator_diffs,
        }

    return result


# ---------------------------------------------------------------------------
# Main analysis function
# ---------------------------------------------------------------------------
def analyze_indicators(
    wal_dir: Path = WAL_DIR,
    days: int = 30,
) -> IndicatorIntelligence:
    """Analyze which indicator conditions at entry predict winning vs losing trades.

    Reads WAL PositionClosed events (with entry_rvol, entry_hurst, entry_adx fields).
    For trades missing indicator fields, falls back to RoutedOrder event data.

    Args:
        wal_dir: Path to WAL events directory (contains current.ndjson + archive/)
        days: Lookback period in days

    Returns:
        IndicatorIntelligence dataclass with all analysis results
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info("Indicator intelligence analysis starting: %d-day lookback from %s", days, today)

    # --- Load enriched trades ---
    trades = _load_enriched_trades(wal_dir, days)

    if not trades:
        log.warning("No trades found in %d-day lookback — returning empty analysis", days)
        return IndicatorIntelligence(
            analysis_date=today,
            lookback_days=days,
            total_trades=0,
            total_wins=0,
            total_losses=0,
            overall_win_rate=0.0,
        )

    # --- Separate winners and losers ---
    winners = [t for t in trades if t.is_winner]
    losers = [t for t in trades if not t.is_winner]
    total = len(trades)
    overall_wr = len(winners) / total if total > 0 else 0.0

    log.info(
        "Trade split: %d total, %d winners (%.1f%%), %d losers (%.1f%%)",
        total, len(winners), overall_wr * 100, len(losers), (1 - overall_wr) * 100,
    )

    # --- Per-indicator statistics (winners vs losers) ---
    winning_indicators: Dict[str, Dict[str, Any]] = {}
    losing_indicators: Dict[str, Dict[str, Any]] = {}
    winning_stats_obj: Dict[str, IndicatorStats] = {}
    losing_stats_obj: Dict[str, IndicatorStats] = {}

    for indicator in TRACKED_INDICATORS:
        w_stats = _compute_indicator_stats(winners, indicator)
        l_stats = _compute_indicator_stats(losers, indicator)

        if w_stats:
            winning_indicators[indicator] = asdict(w_stats)
            winning_stats_obj[indicator] = w_stats
        if l_stats:
            losing_indicators[indicator] = asdict(l_stats)
            losing_stats_obj[indicator] = l_stats

        if w_stats and l_stats:
            separation = w_stats.mean - l_stats.mean
            log.info(
                "  %s: winners mean=%.4f, losers mean=%.4f, separation=%.4f",
                indicator, w_stats.mean, l_stats.mean, separation,
            )

    # --- Threshold rule discovery ---
    all_rules: List[ThresholdRule] = []
    for indicator in TRACKED_INDICATORS:
        rules = _discover_threshold_rules(trades, indicator, overall_wr)
        all_rules.extend(rules)

    # Sort all rules by absolute lift (descending)
    all_rules.sort(key=lambda r: -abs(r.lift))
    rules_dicts = [asdict(r) for r in all_rules]

    # Generate human-readable rule text
    for i, rule in enumerate(all_rules):
        if i < len(rules_dicts):
            rules_dicts[i]["rule_text"] = (
                f"{rule.indicator} {rule.direction} {rule.threshold:.2f}: "
                f"WR={rule.win_rate:.0%} ({rule.trades_matching} trades, "
                f"lift={rule.lift:+.0%})"
            )

    log.info("Discovered %d threshold rules (lift >= 5pp)", len(all_rules))
    for rule in all_rules[:5]:
        log.info(
            "  TOP RULE: %s %s %.2f → WR=%.0f%% (%d trades, lift=%+.0f%%)",
            rule.indicator, rule.direction, rule.threshold,
            rule.win_rate * 100, rule.trades_matching, rule.lift * 100,
        )

    # --- Regime performance ---
    regime_perf = _compute_regime_performance(trades)
    for regime, data in sorted(regime_perf.items(), key=lambda x: -x[1]["win_rate"]):
        log.info(
            "  Regime %s: %d trades, WR=%.0f%%, avg PnL=%.4f",
            regime, data["trades"], data["win_rate"] * 100, data["avg_pnl"],
        )

    # --- Session performance ---
    session_perf = _compute_session_performance(trades)
    for session, data in sorted(session_perf.items(), key=lambda x: -x[1]["win_rate"]):
        log.info(
            "  Session %s: %d trades, WR=%.0f%%",
            session, data["trades"], data["win_rate"] * 100,
        )

    # --- Indicator correlations ---
    correlations = _compute_indicator_correlations(trades)

    # --- Recommended filters ---
    recommended = _compute_recommended_filters(
        all_rules, winning_stats_obj, losing_stats_obj,
    )
    for ind, rec in recommended.items():
        log.info(
            "  RECOMMENDATION: %s %s %.2f (WR=%.0f%%, lift=%+.0f%%, confidence=%.2f)",
            ind, rec["direction"], rec["threshold"],
            rec["win_rate_if_applied"] * 100, rec["lift"] * 100, rec["confidence_score"],
        )

    # --- Per-ticker indicator summary ---
    ticker_summary = _compute_ticker_indicator_summary(trades)

    # --- Assemble result ---
    result = IndicatorIntelligence(
        analysis_date=today,
        lookback_days=days,
        total_trades=total,
        total_wins=len(winners),
        total_losses=len(losers),
        overall_win_rate=round(overall_wr, 4),
        winning_indicators=winning_indicators,
        losing_indicators=losing_indicators,
        regime_performance=regime_perf,
        session_performance=session_perf,
        rules=rules_dicts,
        recommended_filters=recommended,
        indicator_correlations=correlations,
        ticker_indicator_summary=ticker_summary,
    )

    log.info(
        "Indicator intelligence complete: %d trades, %d rules, %d recommendations",
        total, len(all_rules), len(recommended),
    )

    return result


def save_indicator_intelligence(result: IndicatorIntelligence, output_dir: Path = DATA_DIR) -> Path:
    """Save indicator intelligence results to JSON file.

    Returns the path to the saved file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "indicator_intelligence.json"
    tmp_path = output_path.with_suffix(".json.tmp")

    try:
        tmp_path.write_text(result.to_json(), encoding="utf-8")
        os.rename(str(tmp_path), str(output_path))
        log.info("Indicator intelligence saved: %s", output_path)
        return output_path
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    """CLI entry point: run indicator intelligence analysis."""
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [IndicatorIntel] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="AEGIS V2 -- Indicator Intelligence Analysis"
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Lookback period in days (default: 30)",
    )
    parser.add_argument(
        "--wal-dir", type=str, default=str(WAL_DIR),
        help="WAL events directory",
    )
    parser.add_argument(
        "--output-dir", type=str, default=str(DATA_DIR),
        help="Output directory for JSON results",
    )
    args = parser.parse_args()

    result = analyze_indicators(
        wal_dir=Path(args.wal_dir),
        days=args.days,
    )

    output_path = save_indicator_intelligence(result, Path(args.output_dir))
    print(f"\nAnalysis complete: {output_path}")
    print(f"  Trades analyzed: {result.total_trades}")
    print(f"  Overall WR: {result.overall_win_rate:.1%}")
    print(f"  Rules discovered: {len(result.rules)}")
    print(f"  Recommendations: {len(result.recommended_filters)}")

    # Print top rules
    if result.rules:
        print("\nTop 10 Rules:")
        for rule in result.rules[:10]:
            print(f"  {rule.get('rule_text', '')}")

    # Print recommendations
    if result.recommended_filters:
        print("\nRecommended Filters:")
        for ind, rec in result.recommended_filters.items():
            print(f"  {rec.get('rule_text', '')}")


if __name__ == "__main__":
    main()
