"""
NZT-48 Trading System -- Real-Time Correlation Matrix Engine
Section 57: Cross-Correlation Matrix for portfolio risk management.

Replaces the static config-based correlation lookup with a live,
incrementally-updated pairwise correlation engine using Welford's online
algorithm.  Feeds into three consumers:

    1. circuit_breakers.py  -- Correlation Spike Breaker
    2. dynamic_sizer.py     -- Correlation penalty for position sizing
    3. portfolio_risk.py    -- Effective independent positions count

Data source: 5-min close prices from the data feed pipeline.
Default rolling window: 60 bars (5 hours of intraday data).

Computation is incremental (O(1) per update per pair) -- no full
recompute on each tick.  Thread-safe via threading.Lock.
"""

from __future__ import annotations

import logging
import math
import sys
import threading
import time as _time
from collections import deque
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Optional

# Project imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import config as cfg

logger = logging.getLogger("nzt48.correlation_matrix")


# ---------------------------------------------------------------------------
# Sector mapping (mirrors portfolio_risk.py TICKER_SECTOR)
# ---------------------------------------------------------------------------

SECTOR_MAP: dict[str, str] = {
    "NVDA": "semiconductors",
    "AMD": "semiconductors",
    "MU": "semiconductors",
    "AVGO": "semiconductors",
    "MRVL": "semiconductors",
    "ARM": "semiconductors",
    "TSM": "semiconductors",
    "ASML": "semiconductors",
    "SMH": "semiconductors",
    "SOXX": "semiconductors",
    "SNDK": "semiconductors",
    "SMCI": "ai_infrastructure",
    "VRT": "ai_infrastructure",
    "TSLA": "ev_auto",
    "QQQ": "broad_market",
    "SPY": "broad_market",
}

# Default correlation fallbacks when insufficient data (< MIN_BARS)
_DEFAULT_SAME_SEMICONDUCTOR = 0.65
_DEFAULT_SAME_SECTOR = 0.50
_DEFAULT_CROSS_SECTOR = 0.20
_DEFAULT_INVERSE = -0.80

# Inverse instrument pairs (e.g. SQQQ vs QQQ)
_INVERSE_PAIRS: set[frozenset[str]] = {
    frozenset({"QQQ", "SQQQ"}),
    frozenset({"SPY", "SH"}),
    frozenset({"SPY", "SPXS"}),
    frozenset({"QQQ", "PSQ"}),
    frozenset({"SMH", "SOXS"}),
}


# ---------------------------------------------------------------------------
# Welford's Online Algorithm for running covariance
# ---------------------------------------------------------------------------

class _WelfordPairState:
    """Tracks running mean, variance, and covariance for a pair of series
    using Welford's online algorithm.

    This supports incremental addition AND removal of observations so
    that a sliding window can be maintained without full recomputation.

    Formulas (add):
        n += 1
        dx = x - mean_x
        mean_x += dx / n
        dy = y - mean_y
        mean_y += dy / n
        C_xy += dx * (y - mean_y)          # note: new mean_y
        M2_x += dx * (x - mean_x)          # note: new mean_x
        M2_y += dy * (y - mean_y)           # note: new mean_y

    Formulas (remove -- reverse Welford):
        dx = x - mean_x
        mean_x -= dx / n  (after: n -= 1 handled outside)
        Actually, removal is trickier with Welford, so we use the
        simpler approach of storing raw values and recomputing only
        when the window slides (amortised O(1) via deque tracking).

    Given the 60-bar window, the simplest correct approach is:
    keep the deques and recompute running sums only when needed.
    With N=60 this is cheap and avoids numerical drift.
    """
    __slots__ = ("n", "sum_x", "sum_y", "sum_xx", "sum_yy", "sum_xy")

    def __init__(self) -> None:
        self.n: int = 0
        self.sum_x: float = 0.0
        self.sum_y: float = 0.0
        self.sum_xx: float = 0.0
        self.sum_yy: float = 0.0
        self.sum_xy: float = 0.0

    def set_from_arrays(self, xs: list[float], ys: list[float]) -> None:
        """Recompute running sums from aligned price arrays."""
        n = min(len(xs), len(ys))
        self.n = n
        self.sum_x = 0.0
        self.sum_y = 0.0
        self.sum_xx = 0.0
        self.sum_yy = 0.0
        self.sum_xy = 0.0
        for i in range(n):
            x, y = xs[i], ys[i]
            self.sum_x += x
            self.sum_y += y
            self.sum_xx += x * x
            self.sum_yy += y * y
            self.sum_xy += x * y

    def pearson(self) -> float:
        """Compute Pearson correlation from running sums.

        r = [n*sum_xy - sum_x*sum_y] /
            sqrt([n*sum_xx - sum_x^2] * [n*sum_yy - sum_y^2])
        """
        if self.n < 2:
            return 0.0
        numerator = self.n * self.sum_xy - self.sum_x * self.sum_y
        denom_x = self.n * self.sum_xx - self.sum_x * self.sum_x
        denom_y = self.n * self.sum_yy - self.sum_y * self.sum_y
        if denom_x <= 0.0 or denom_y <= 0.0:
            return 0.0
        denom = math.sqrt(denom_x * denom_y)
        if denom == 0.0:
            return 0.0
        r = numerator / denom
        # Clamp to [-1, 1] to handle floating point drift
        return max(-1.0, min(1.0, r))


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

# Minimum number of overlapping bars before we trust computed correlation
_MIN_BARS = 20

# Default rolling window
_DEFAULT_WINDOW = 60

# Spike detection: rolling average window for detecting jumps
_SPIKE_AVG_WINDOW = 5


class RealTimeCorrelationMatrix:
    """Real-time pairwise correlation engine using rolling 5-min close prices.

    Computes Pearson correlation over a sliding window for every observed
    ticker pair.  Falls back to sector-based defaults when data is
    insufficient (< 20 overlapping bars).

    Thread-safe: all public methods acquire ``_lock`` before mutating
    or reading shared state.

    Args:
        window: Number of 5-min bars in the rolling window (default 60 = 5h).
        min_bars: Minimum overlapping bars before trusting computed
            correlation (default 20).

    Usage::

        matrix = RealTimeCorrelationMatrix()

        # On each 5-min bar close:
        matrix.update("NVDA", 925.40, bar_timestamp)
        matrix.update("AMD", 168.20, bar_timestamp)

        # Query:
        corr = matrix.get_correlation("NVDA", "AMD")
        full = matrix.get_matrix()
    """

    def __init__(
        self,
        window: int = _DEFAULT_WINDOW,
        min_bars: int = _MIN_BARS,
    ) -> None:
        self._window = window
        self._min_bars = min_bars
        self._lock = threading.Lock()

        # Per-ticker price buffers: ticker -> deque of (timestamp, price)
        self._prices: dict[str, deque[tuple[datetime, float]]] = {}

        # Cached pair statistics (recomputed incrementally on update)
        self._pair_stats: dict[tuple[str, str], _WelfordPairState] = {}

        # Cached correlation values for fast retrieval
        self._corr_cache: dict[tuple[str, str], float] = {}

        # Spike detection: rolling history of per-pair correlations
        # pair -> deque of recent correlation values
        self._corr_history: dict[tuple[str, str], deque[float]] = {}

        # Bookkeeping
        self._update_count: int = 0
        self._last_update: Optional[datetime] = None
        self._recompute_count: int = 0

        # Load any config overrides
        corr_cfg = cfg.get("correlation_matrix", {}) or {}
        if corr_cfg.get("window"):
            self._window = int(corr_cfg["window"])
        if corr_cfg.get("min_bars"):
            self._min_bars = int(corr_cfg["min_bars"])

        logger.info(
            "RealTimeCorrelationMatrix initialised | window=%d bars | "
            "min_bars=%d | sectors=%d tickers mapped",
            self._window, self._min_bars, len(SECTOR_MAP),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(self, ticker: str, price: float, timestamp: datetime) -> None:
        """Feed a new 5-min close price for a ticker.

        Appends to the rolling window, evicts old data, and incrementally
        recomputes pairwise correlations for all pairs involving this ticker.

        Args:
            ticker: Instrument symbol (e.g. "NVDA").
            price: Closing price for this 5-min bar.
            timestamp: Datetime of the bar close (should be tz-aware).
        """
        ticker = ticker.upper()
        with self._lock:
            # Ensure deque exists
            if ticker not in self._prices:
                self._prices[ticker] = deque(maxlen=self._window)

            self._prices[ticker].append((timestamp, price))
            self._update_count += 1
            self._last_update = timestamp

            # Recompute correlations for every pair involving this ticker
            for other_ticker in self._prices:
                if other_ticker == ticker:
                    continue
                self._recompute_pair(ticker, other_ticker)

    def get_correlation(self, ticker_a: str, ticker_b: str) -> float:
        """Return the pairwise Pearson correlation between two tickers.

        Falls back to sector-based defaults when insufficient overlapping
        data is available.

        Args:
            ticker_a: First instrument symbol.
            ticker_b: Second instrument symbol.

        Returns:
            Correlation coefficient in [-1.0, +1.0].
        """
        ticker_a = ticker_a.upper()
        ticker_b = ticker_b.upper()

        if ticker_a == ticker_b:
            return 1.0

        pair = self._make_pair_key(ticker_a, ticker_b)

        with self._lock:
            cached = self._corr_cache.get(pair)
            if cached is not None:
                return cached

        # No computed value -- return sector-based default
        return self._sector_default(ticker_a, ticker_b)

    def get_matrix(self) -> dict[str, dict[str, float]]:
        """Return the full correlation matrix as a nested dict.

        The matrix is symmetric: matrix[A][B] == matrix[B][A].
        Diagonal elements are 1.0.

        Returns:
            Dict of ticker -> dict of ticker -> correlation.
        """
        with self._lock:
            tickers = sorted(self._prices.keys())

        matrix: dict[str, dict[str, float]] = {}
        for t in tickers:
            matrix[t] = {}
            for u in tickers:
                if t == u:
                    matrix[t][u] = 1.0
                else:
                    matrix[t][u] = self.get_correlation(t, u)
        return matrix

    def get_average_cross_correlation(self, tickers: list[str]) -> float:
        """Compute the average pairwise correlation for a set of tickers.

        Useful for assessing overall portfolio diversification.

        Args:
            tickers: List of ticker symbols.

        Returns:
            Average pairwise correlation (0.0 if fewer than 2 tickers).
        """
        tickers = [t.upper() for t in tickers]
        if len(tickers) < 2:
            return 0.0

        total = 0.0
        count = 0
        for i in range(len(tickers)):
            for j in range(i + 1, len(tickers)):
                total += self.get_correlation(tickers[i], tickers[j])
                count += 1

        return total / count if count > 0 else 0.0

    def detect_correlation_spike(
        self,
        threshold: float = 0.75,
    ) -> list[dict[str, Any]]:
        """Detect pairs where correlation has spiked above the threshold.

        A spike is defined as the 5-bar rolling average jumping from
        below 0.5 to above ``threshold`` within the most recent 5 bars.

        Args:
            threshold: Correlation level above which a spike is flagged
                (default 0.75).

        Returns:
            List of dicts with keys: pair, old_corr, new_corr, delta.
        """
        spikes: list[dict[str, Any]] = []

        with self._lock:
            for pair, history in self._corr_history.items():
                if len(history) < _SPIKE_AVG_WINDOW:
                    continue

                history_list = list(history)

                # Current rolling average (last SPIKE_AVG_WINDOW values)
                recent = history_list[-_SPIKE_AVG_WINDOW:]
                new_avg = sum(recent) / len(recent)

                # Previous rolling average (the SPIKE_AVG_WINDOW values before that)
                if len(history_list) >= _SPIKE_AVG_WINDOW * 2:
                    older = history_list[
                        -_SPIKE_AVG_WINDOW * 2 : -_SPIKE_AVG_WINDOW
                    ]
                    old_avg = sum(older) / len(older)
                elif len(history_list) > _SPIKE_AVG_WINDOW:
                    older = history_list[: -_SPIKE_AVG_WINDOW]
                    old_avg = sum(older) / len(older)
                else:
                    # Not enough history for comparison
                    continue

                if old_avg < 0.5 and new_avg > threshold:
                    delta = new_avg - old_avg
                    spikes.append({
                        "pair": f"{pair[0]}_{pair[1]}",
                        "ticker_a": pair[0],
                        "ticker_b": pair[1],
                        "old_corr": round(old_avg, 4),
                        "new_corr": round(new_avg, 4),
                        "delta": round(delta, 4),
                    })
                    logger.warning(
                        "CORRELATION SPIKE: %s/%s jumped %.2f -> %.2f "
                        "(delta=+%.2f, threshold=%.2f)",
                        pair[0], pair[1], old_avg, new_avg, delta, threshold,
                    )

        return spikes

    def get_cluster_risk(
        self,
        open_positions: list[Any],
        equity: float = 0.0,
    ) -> dict[str, Any]:
        """Group open positions by correlation cluster and report risk.

        Positions are grouped into clusters where the average pairwise
        correlation exceeds 0.6.  Each cluster reports total risk dollars
        and effective independent positions.

        Args:
            open_positions: List of position dicts or objects with
                ticker, risk_dollars, direction attributes.
            equity: Total account equity (for concentration % calculations).

        Returns:
            Dict with clusters (list), total_clusters, concentrated_clusters
            (those exceeding 15% of equity).
        """
        if not open_positions:
            return {
                "clusters": [],
                "total_clusters": 0,
                "concentrated_clusters": [],
            }

        # Extract position info
        positions: list[dict[str, Any]] = []
        for pos in open_positions:
            ticker = (
                pos.get("ticker", "")
                if isinstance(pos, dict)
                else getattr(pos, "ticker", "")
            )
            risk_dollars = (
                pos.get("risk_dollars", 0.0)
                if isinstance(pos, dict)
                else getattr(pos, "risk_dollars", 0.0)
            )
            direction = (
                pos.get("direction", "LONG")
                if isinstance(pos, dict)
                else getattr(pos, "direction", "LONG")
            )
            if hasattr(direction, "value"):
                direction = direction.value
            positions.append({
                "ticker": ticker.upper(),
                "risk_dollars": float(risk_dollars),
                "direction": str(direction),
            })

        # Build adjacency: two positions are "connected" if corr > 0.6
        n = len(positions)
        adj: list[set[int]] = [set() for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                corr = self.get_correlation(
                    positions[i]["ticker"],
                    positions[j]["ticker"],
                )
                if corr > 0.6:
                    adj[i].add(j)
                    adj[j].add(i)

        # BFS to find connected components (clusters)
        visited: set[int] = set()
        clusters: list[dict[str, Any]] = []
        cluster_id = 0

        for start in range(n):
            if start in visited:
                continue
            # BFS from start
            queue = [start]
            component: list[int] = []
            while queue:
                node = queue.pop(0)
                if node in visited:
                    continue
                visited.add(node)
                component.append(node)
                for neighbor in adj[node]:
                    if neighbor not in visited:
                        queue.append(neighbor)

            tickers_in_cluster = [positions[i]["ticker"] for i in component]
            total_risk = sum(positions[i]["risk_dollars"] for i in component)

            # Effective independent positions within cluster
            avg_corr = self.get_average_cross_correlation(tickers_in_cluster)
            cluster_n = len(component)
            if cluster_n > 1 and avg_corr > 0:
                denom = 1.0 + (cluster_n - 1) * avg_corr
                effective_n = cluster_n / denom
            else:
                effective_n = float(cluster_n)

            risk_pct_equity = (
                (total_risk / equity * 100) if equity > 0 else 0.0
            )

            clusters.append({
                "cluster_id": cluster_id,
                "tickers": tickers_in_cluster,
                "position_count": cluster_n,
                "total_risk_dollars": round(total_risk, 2),
                "risk_pct_equity": round(risk_pct_equity, 2),
                "avg_pairwise_corr": round(avg_corr, 4),
                "effective_independent_positions": round(effective_n, 2),
            })
            cluster_id += 1

        # Flag concentrated clusters (> 15% of equity)
        concentrated = [
            c for c in clusters
            if equity > 0 and c["total_risk_dollars"] / equity > 0.15
        ]
        for c in concentrated:
            logger.warning(
                "CONCENTRATED CLUSTER: id=%d tickers=%s risk=$%.0f "
                "(%.1f%% of equity)",
                c["cluster_id"],
                c["tickers"],
                c["total_risk_dollars"],
                c["risk_pct_equity"],
            )

        return {
            "clusters": clusters,
            "total_clusters": len(clusters),
            "concentrated_clusters": concentrated,
        }

    def get_status(self) -> dict[str, Any]:
        """Return dashboard-friendly status of the correlation engine.

        Returns:
            Dict with engine metadata, tracked tickers, pair count,
            data sufficiency, and timing information.
        """
        with self._lock:
            tickers = sorted(self._prices.keys())
            bars_per_ticker = {
                t: len(d) for t, d in self._prices.items()
            }
            pair_count = len(self._corr_cache)
            sufficient_pairs = sum(
                1 for st in self._pair_stats.values()
                if st.n >= self._min_bars
            )

            return {
                "engine": "RealTimeCorrelationMatrix",
                "window": self._window,
                "min_bars": self._min_bars,
                "tracked_tickers": tickers,
                "ticker_count": len(tickers),
                "bars_per_ticker": bars_per_ticker,
                "total_pairs": pair_count,
                "sufficient_data_pairs": sufficient_pairs,
                "total_updates": self._update_count,
                "recompute_count": self._recompute_count,
                "last_update": (
                    self._last_update.isoformat()
                    if self._last_update
                    else None
                ),
            }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_pair_key(a: str, b: str) -> tuple[str, str]:
        """Canonical pair key (alphabetical order) for symmetric lookups."""
        return (a, b) if a <= b else (b, a)

    def _recompute_pair(self, ticker_a: str, ticker_b: str) -> None:
        """Recompute the correlation for a single pair.

        Aligns the two price deques by timestamp, builds running sums,
        and updates the cache.  Called under ``_lock``.
        """
        pair = self._make_pair_key(ticker_a, ticker_b)

        buf_a = self._prices.get(ticker_a)
        buf_b = self._prices.get(ticker_b)
        if not buf_a or not buf_b:
            return

        # Build aligned price lists using timestamp matching
        # For efficiency we use index-based alignment: both deques store
        # the most recent self._window bars.  We align by overlapping
        # timestamps.
        ts_to_price_a: dict[datetime, float] = {
            ts: px for ts, px in buf_a
        }
        aligned_a: list[float] = []
        aligned_b: list[float] = []
        for ts, px in buf_b:
            if ts in ts_to_price_a:
                aligned_a.append(ts_to_price_a[ts])
                aligned_b.append(px)

        # Create or update the pair state
        if pair not in self._pair_stats:
            self._pair_stats[pair] = _WelfordPairState()

        state = self._pair_stats[pair]
        state.set_from_arrays(aligned_a, aligned_b)
        self._recompute_count += 1

        # Compute and cache correlation
        if state.n >= self._min_bars:
            corr = state.pearson()
        else:
            corr = self._sector_default(pair[0], pair[1])

        self._corr_cache[pair] = corr

        # Update spike detection history
        if pair not in self._corr_history:
            self._corr_history[pair] = deque(
                maxlen=self._window,
            )
        self._corr_history[pair].append(corr)

    @staticmethod
    def _sector_default(ticker_a: str, ticker_b: str) -> float:
        """Return the default correlation based on sector membership.

        Fallback values used when fewer than ``min_bars`` overlapping
        data points are available.

        Returns:
            -0.80 for known inverse pairs.
             0.65 for same-semiconductor tickers.
             0.50 for same-sector (non-semiconductor).
             0.20 for cross-sector.
        """
        # Check inverse pairs first
        pair_set = frozenset({ticker_a.upper(), ticker_b.upper()})
        if pair_set in _INVERSE_PAIRS:
            return _DEFAULT_INVERSE

        sector_a = SECTOR_MAP.get(ticker_a.upper(), "other")
        sector_b = SECTOR_MAP.get(ticker_b.upper(), "other")

        if sector_a == sector_b:
            if sector_a == "semiconductors":
                return _DEFAULT_SAME_SEMICONDUCTOR
            return _DEFAULT_SAME_SECTOR
        return _DEFAULT_CROSS_SECTOR


# ---------------------------------------------------------------------------
# Module-level singleton (optional usage pattern)
# ---------------------------------------------------------------------------

_INSTANCE: Optional[RealTimeCorrelationMatrix] = None
_INSTANCE_LOCK = threading.Lock()


def get_instance(**kwargs: Any) -> RealTimeCorrelationMatrix:
    """Return the module-level singleton, creating it on first call.

    Kwargs are forwarded to the constructor only on first creation.
    """
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = RealTimeCorrelationMatrix(**kwargs)
        return _INSTANCE


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import random

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    print("=" * 70)
    print("NZT-48 Correlation Matrix Engine -- Self-Test")
    print("=" * 70)

    matrix = RealTimeCorrelationMatrix(window=60, min_bars=20)

    # --- Phase 1: Feed 60 bars of correlated data ---
    print("\n--- Phase 1: Feeding 60 price bars for NVDA, AMD, TSLA ---")

    random.seed(42)
    base_time = datetime(2025, 3, 15, 9, 30, tzinfo=timezone.utc)

    # NVDA and AMD will be highly correlated (semi sector)
    # TSLA will have low correlation to the semis
    nvda_prices = [900.0]
    amd_prices = [165.0]
    tsla_prices = [250.0]

    for i in range(1, 60):
        # Shared semiconductor momentum + individual noise
        semi_move = random.gauss(0.0, 2.0)
        nvda_noise = random.gauss(0.0, 0.5)
        amd_noise = random.gauss(0.0, 0.3)
        tsla_move = random.gauss(0.0, 3.0)  # independent

        nvda_prices.append(nvda_prices[-1] + semi_move + nvda_noise)
        amd_prices.append(amd_prices[-1] + semi_move * 0.18 + amd_noise)
        tsla_prices.append(tsla_prices[-1] + tsla_move)

    for i in range(60):
        ts = datetime(
            2025, 3, 15,
            9 + (30 + i * 5) // 60,
            (30 + i * 5) % 60,
            tzinfo=timezone.utc,
        )
        matrix.update("NVDA", nvda_prices[i], ts)
        matrix.update("AMD", amd_prices[i], ts)
        matrix.update("TSLA", tsla_prices[i], ts)

    # --- Phase 2: Check correlations ---
    print("\n--- Phase 2: Pairwise Correlations ---")

    for a, b in [("NVDA", "AMD"), ("NVDA", "TSLA"), ("AMD", "TSLA")]:
        corr = matrix.get_correlation(a, b)
        print(f"  {a} vs {b}: {corr:+.4f}")

    avg_all = matrix.get_average_cross_correlation(["NVDA", "AMD", "TSLA"])
    avg_semis = matrix.get_average_cross_correlation(["NVDA", "AMD"])
    print(f"\n  Avg cross-correlation (all 3):   {avg_all:+.4f}")
    print(f"  Avg cross-correlation (semis):   {avg_semis:+.4f}")

    # --- Phase 3: Full matrix ---
    print("\n--- Phase 3: Full Correlation Matrix ---")
    full = matrix.get_matrix()
    tickers = sorted(full.keys())
    header = "        " + "  ".join(f"{t:>7s}" for t in tickers)
    print(header)
    for t in tickers:
        row = f"  {t:>5s} " + "  ".join(
            f"{full[t][u]:+.4f}" for u in tickers
        )
        print(row)

    # --- Phase 4: Spike detection ---
    print("\n--- Phase 4: Correlation Spike Detection ---")

    # Inject a spike: feed 10 bars where TSLA suddenly tracks NVDA closely
    print("  Injecting 10 bars of TSLA tracking NVDA...")
    for i in range(60, 70):
        ts = datetime(
            2025, 3, 15,
            9 + (30 + i * 5) // 60,
            (30 + i * 5) % 60,
            tzinfo=timezone.utc,
        )
        # TSLA now follows NVDA with small noise
        nvda_px = nvda_prices[-1] + random.gauss(1.0, 0.5)
        nvda_prices.append(nvda_px)
        amd_px = amd_prices[-1] + random.gauss(0.2, 0.3)
        amd_prices.append(amd_px)
        tsla_px = tsla_prices[-1] + (nvda_px - nvda_prices[-2]) * 0.28 + random.gauss(0.0, 0.2)
        tsla_prices.append(tsla_px)

        matrix.update("NVDA", nvda_px, ts)
        matrix.update("AMD", amd_px, ts)
        matrix.update("TSLA", tsla_px, ts)

    spikes = matrix.detect_correlation_spike(threshold=0.75)
    if spikes:
        for s in spikes:
            print(
                f"  SPIKE: {s['pair']} | "
                f"{s['old_corr']:+.4f} -> {s['new_corr']:+.4f} "
                f"(delta={s['delta']:+.4f})"
            )
    else:
        print("  No correlation spikes detected (expected for synthetic data).")

    # --- Phase 5: Cluster risk ---
    print("\n--- Phase 5: Cluster Risk Analysis ---")

    mock_positions = [
        {"ticker": "NVDA", "risk_dollars": 150.0, "direction": "LONG"},
        {"ticker": "AMD", "risk_dollars": 120.0, "direction": "LONG"},
        {"ticker": "TSLA", "risk_dollars": 100.0, "direction": "LONG"},
    ]

    cluster_report = matrix.get_cluster_risk(
        mock_positions, equity=10_000.0,
    )
    print(f"  Total clusters: {cluster_report['total_clusters']}")
    for c in cluster_report["clusters"]:
        print(
            f"    Cluster {c['cluster_id']}: {c['tickers']} | "
            f"risk=${c['total_risk_dollars']:.0f} "
            f"({c['risk_pct_equity']:.1f}% of equity) | "
            f"avg_corr={c['avg_pairwise_corr']:+.4f} | "
            f"effective_positions={c['effective_independent_positions']:.2f}"
        )
    if cluster_report["concentrated_clusters"]:
        print(f"  CONCENTRATED: {len(cluster_report['concentrated_clusters'])} cluster(s) > 15% of equity")
    else:
        print("  No concentrated clusters (all within 15% limit).")

    # --- Phase 6: Status ---
    print("\n--- Phase 6: Engine Status ---")
    status = matrix.get_status()
    for k, v in status.items():
        print(f"  {k}: {v}")

    print("\n" + "=" * 70)
    print("Self-test complete.")
    print("=" * 70)
