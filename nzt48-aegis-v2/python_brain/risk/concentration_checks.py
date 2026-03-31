"""Book 7: Concentration Risk Checks (CHECKs 33-36) + Correlation Spike Detector.

Adds 4 Python-side quality gates that fire BEFORE signal generation:
  CHECK 33: Correlation Concentration — block if portfolio pairwise corr > 0.70
  CHECK 34: Country Concentration — block if >60% exposure in one country
  CHECK 35: Session Exposure — block if session heat exceeds limit
  CHECK 36: Time-of-Day Risk — block during high-risk hours

Plus: CorrelationSpikeDetector — detects sudden correlation regime shifts.

Wired into bridge.py _check_quality_gates().
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

# Exchange → Country mapping
EXCHANGE_COUNTRY = {
    "LSE": "UK", "LSEETF": "UK",
    "SMART": "US", "ARCA": "US", "NASDAQ": "US", "NYSE": "US",
    "IBIS": "DE", "XETRA": "DE",
    "SBF": "FR", "AEB": "NL", "BVME": "IT", "BM": "ES", "EBS": "CH",
    "TSE": "JP", "SEHK": "HK", "ASX": "AU", "SGX": "SG", "KSE": "KR",
}

# High-risk hours (UTC) — first/last 30 min of major sessions
# Book 7 Table 36.2: Time-of-day risk tiers
HIGH_RISK_HOURS_UTC = {
    7: 0.8,   # LSE open 07:00-08:00
    8: 0.9,   # LSE first hour
    14: 0.8,  # US open overlap 14:30
    15: 0.9,  # US first hour
    20: 0.85, # LSE close auction
    21: 0.85, # US close
}

# Session exposure limits (max fraction of equity per session)
SESSION_LIMITS = {
    "LSE_MORNING": 0.50,   # 08:00-12:00 UTC
    "LSE_AFTERNOON": 0.40, # 12:00-16:30 UTC
    "US_CORE": 0.50,       # 14:30-21:00 UTC
    "ASIA": 0.30,          # 00:00-08:00 UTC
    "OVERLAP": 0.60,       # 14:30-16:30 UTC (LSE+US)
}


@dataclass
class ConcentrationCheckResult:
    """Result from concentration checks."""
    passed: bool
    check_name: str
    reason: str = ""
    value: float = 0.0
    threshold: float = 0.0


def check_correlation_concentration(
    open_positions: List[Dict],
    return_histories: Dict[str, List[float]],
    max_pairwise_corr: float = 0.70,
    max_avg_corr: float = 0.50,
) -> ConcentrationCheckResult:
    """CHECK 33: Block new entry if portfolio pairwise correlation too high.

    Uses recent 60-bar return correlation between all open positions.
    """
    if len(open_positions) < 2:
        return ConcentrationCheckResult(passed=True, check_name="CHECK_33_CORR")

    symbols = [p.get("symbol", "") for p in open_positions]
    valid_pairs = []
    max_corr = 0.0
    corr_sum = 0.0
    n_pairs = 0

    for i in range(len(symbols)):
        for j in range(i + 1, len(symbols)):
            r1 = return_histories.get(symbols[i], [])
            r2 = return_histories.get(symbols[j], [])
            if len(r1) < 20 or len(r2) < 20:
                continue
            # Use last 60 returns
            n = min(60, len(r1), len(r2))
            r1 = r1[-n:]
            r2 = r2[-n:]
            corr = _pearson_corr(r1, r2)
            if abs(corr) > abs(max_corr):
                max_corr = corr
            corr_sum += abs(corr)
            n_pairs += 1

    if n_pairs == 0:
        return ConcentrationCheckResult(passed=True, check_name="CHECK_33_CORR")

    avg_corr = corr_sum / n_pairs

    if abs(max_corr) > max_pairwise_corr:
        return ConcentrationCheckResult(
            passed=False, check_name="CHECK_33_CORR",
            reason=f"Max pairwise corr {max_corr:.2f} > {max_pairwise_corr}",
            value=max_corr, threshold=max_pairwise_corr,
        )
    if avg_corr > max_avg_corr:
        return ConcentrationCheckResult(
            passed=False, check_name="CHECK_33_CORR",
            reason=f"Avg portfolio corr {avg_corr:.2f} > {max_avg_corr}",
            value=avg_corr, threshold=max_avg_corr,
        )

    return ConcentrationCheckResult(
        passed=True, check_name="CHECK_33_CORR", value=max_corr,
    )


def check_country_concentration(
    open_positions: List[Dict],
    new_exchange: str,
    max_country_pct: float = 0.60,
) -> ConcentrationCheckResult:
    """CHECK 34: Block if >60% exposure in one country after this trade."""
    if not open_positions:
        return ConcentrationCheckResult(passed=True, check_name="CHECK_34_COUNTRY")

    country_exposure: Dict[str, float] = defaultdict(float)
    total_exposure = 0.0

    for pos in open_positions:
        exch = pos.get("exchange", "")
        notional = abs(pos.get("notional", 0.0))
        country = EXCHANGE_COUNTRY.get(exch, "OTHER")
        country_exposure[country] += notional
        total_exposure += notional

    # Add the proposed new trade (assume similar size to avg)
    avg_size = total_exposure / max(len(open_positions), 1)
    new_country = EXCHANGE_COUNTRY.get(new_exchange, "OTHER")
    country_exposure[new_country] += avg_size
    total_exposure += avg_size

    if total_exposure <= 0:
        return ConcentrationCheckResult(passed=True, check_name="CHECK_34_COUNTRY")

    for country, exposure in country_exposure.items():
        pct = exposure / total_exposure
        if pct > max_country_pct:
            return ConcentrationCheckResult(
                passed=False, check_name="CHECK_34_COUNTRY",
                reason=f"{country} exposure {pct:.0%} > {max_country_pct:.0%}",
                value=pct, threshold=max_country_pct,
            )

    return ConcentrationCheckResult(passed=True, check_name="CHECK_34_COUNTRY")


def check_session_exposure(
    open_positions: List[Dict],
    utc_hour: int,
    equity: float,
    max_session_pct: float = 0.50,
) -> ConcentrationCheckResult:
    """CHECK 35: Block if session heat exceeds limit."""
    # Determine current session
    if 0 <= utc_hour < 8:
        session = "ASIA"
    elif 8 <= utc_hour < 12:
        session = "LSE_MORNING"
    elif 12 <= utc_hour < 14:
        session = "LSE_AFTERNOON"
    elif 14 <= utc_hour < 16:
        session = "OVERLAP"
    elif 16 <= utc_hour < 21:
        session = "US_CORE"
    else:
        session = "CLOSED"
        return ConcentrationCheckResult(passed=True, check_name="CHECK_35_SESSION")

    limit = SESSION_LIMITS.get(session, max_session_pct)

    # Sum open exposure from positions entered this session
    session_exposure = sum(
        abs(p.get("notional", 0.0))
        for p in open_positions
    )

    if equity <= 0:
        return ConcentrationCheckResult(passed=True, check_name="CHECK_35_SESSION")

    pct = session_exposure / equity
    if pct > limit:
        return ConcentrationCheckResult(
            passed=False, check_name="CHECK_35_SESSION",
            reason=f"{session} exposure {pct:.0%} > {limit:.0%}",
            value=pct, threshold=limit,
        )

    return ConcentrationCheckResult(passed=True, check_name="CHECK_35_SESSION")


def check_time_of_day_risk(utc_hour: int) -> ConcentrationCheckResult:
    """CHECK 36: Apply time-of-day risk multiplier. Returns confidence scaling factor.

    High-risk hours (session open/close) get reduced confidence.
    Returns passed=True always but with a scaling value in [0.8, 1.0].
    """
    scale = HIGH_RISK_HOURS_UTC.get(utc_hour, 1.0)
    return ConcentrationCheckResult(
        passed=True, check_name="CHECK_36_TOD",
        reason=f"Hour {utc_hour} UTC, scale={scale}",
        value=scale,
    )


# ─── Correlation Spike Detector ──────────────────────────────────────────────

class CorrelationSpikeDetector:
    """Detects sudden increases in cross-asset correlation (contagion).

    Maintains rolling correlation estimates and flags when the delta
    exceeds thresholds (warning=0.10, reduce=0.15, flatten=0.25).
    """

    def __init__(self):
        self._return_buffers: Dict[str, List[float]] = defaultdict(list)
        self._last_avg_corr: float = 0.0
        self._max_buffer = 120  # 10 hours of 5-min bars
        self._last_update_ns: int = 0

    def on_tick(self, symbol: str, ret: float, timestamp_ns: int = 0) -> Optional[str]:
        """Feed a return observation. Returns action if spike detected.

        Returns: None, "WARNING", "REDUCE", or "FLATTEN"
        """
        buf = self._return_buffers[symbol]
        buf.append(ret)
        if len(buf) > self._max_buffer:
            self._return_buffers[symbol] = buf[-self._max_buffer:]

        # Only recalculate every ~5 minutes worth of ticks
        if timestamp_ns - self._last_update_ns < 300_000_000_000:
            return None
        self._last_update_ns = timestamp_ns

        # Need at least 3 symbols with 30+ returns
        valid = {s: r for s, r in self._return_buffers.items() if len(r) >= 30}
        if len(valid) < 3:
            return None

        # Compute average pairwise correlation (last 30 bars)
        symbols = list(valid.keys())
        corr_sum = 0.0
        n_pairs = 0
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                r1 = valid[symbols[i]][-30:]
                r2 = valid[symbols[j]][-30:]
                n = min(len(r1), len(r2))
                corr = _pearson_corr(r1[-n:], r2[-n:])
                corr_sum += abs(corr)
                n_pairs += 1

        if n_pairs == 0:
            return None

        avg_corr = corr_sum / n_pairs
        delta = avg_corr - self._last_avg_corr
        self._last_avg_corr = avg_corr

        if delta >= 0.25:
            return "FLATTEN"
        elif delta >= 0.15:
            return "REDUCE"
        elif delta >= 0.10:
            return "WARNING"
        return None

    @property
    def current_avg_correlation(self) -> float:
        return self._last_avg_corr


# Singleton
_spike_detector: Optional[CorrelationSpikeDetector] = None


def get_spike_detector() -> CorrelationSpikeDetector:
    global _spike_detector
    if _spike_detector is None:
        _spike_detector = CorrelationSpikeDetector()
    return _spike_detector


# ─── Utility ─────────────────────────────────────────────────────────────────

def _pearson_corr(x: List[float], y: List[float]) -> float:
    """Pearson correlation coefficient (stdlib only)."""
    n = min(len(x), len(y))
    if n < 5:
        return 0.0
    x, y = x[:n], y[:n]
    mx = sum(x) / n
    my = sum(y) / n
    sx = math.sqrt(sum((xi - mx) ** 2 for xi in x) / (n - 1))
    sy = math.sqrt(sum((yi - my) ** 2 for yi in y) / (n - 1))
    if sx < 1e-10 or sy < 1e-10:
        return 0.0
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / (n - 1)
    return cov / (sx * sy)


# ─── Signal Overlap & Cannibalization Detection ─────────────────────────────


@dataclass
class OverlapMetrics:
    """Cross-strategy overlap measurements."""
    signal_overlap_rate: float = 0.0   # Fraction of signals firing together
    timing_overlap_rate: float = 0.0   # Fraction of time with overlapping positions
    factor_overlap_rate: float = 0.0   # Cosine similarity of feature importance
    capacity_overlap: float = 0.0      # Fraction of capacity consumed by overlap


def compute_signal_overlap(
    signals_a: List[Dict],
    signals_b: List[Dict],
    window_seconds: int = 300,
) -> float:
    """Compute fraction of signals that fire within window_seconds of each other.

    Each signal dict should have at least: {timestamp_ns: int} or {timestamp: float}.
    Returns: overlap_rate in [0, 1] = count_within_window / union_count.
    """
    if not signals_a or not signals_b:
        return 0.0

    def _get_ts(s: Dict) -> float:
        ts = s.get("timestamp_ns", 0)
        if ts:
            return ts / 1e9  # Convert ns to seconds
        return s.get("timestamp", 0.0)

    ts_a = sorted(_get_ts(s) for s in signals_a)
    ts_b = sorted(_get_ts(s) for s in signals_b)

    overlap_count = 0
    j = 0
    for ta in ts_a:
        # Advance j to first ts_b within window
        while j < len(ts_b) and ts_b[j] < ta - window_seconds:
            j += 1
        # Count matches within window
        k = j
        while k < len(ts_b) and ts_b[k] <= ta + window_seconds:
            overlap_count += 1
            break  # Count each pair once
            k += 1

    union_count = len(ts_a) + len(ts_b) - overlap_count
    if union_count <= 0:
        return 0.0
    return overlap_count / union_count


def compute_timing_overlap(
    positions_a: List[Dict],
    positions_b: List[Dict],
) -> float:
    """Fraction of time both strategies hold overlapping positions.

    Each position dict: {symbol: str, entry_time: float, exit_time: float}.
    Returns overlap_rate in [0, 1].
    """
    if not positions_a or not positions_b:
        return 0.0

    total_duration = 0.0
    overlap_duration = 0.0

    for pa in positions_a:
        sym_a = pa.get("symbol", "")
        entry_a = pa.get("entry_time", 0.0)
        exit_a = pa.get("exit_time", 0.0)
        if exit_a <= entry_a:
            continue

        total_duration += exit_a - entry_a

        for pb in positions_b:
            if pb.get("symbol", "") != sym_a:
                continue
            entry_b = pb.get("entry_time", 0.0)
            exit_b = pb.get("exit_time", 0.0)
            if exit_b <= entry_b:
                continue

            # Compute overlap interval
            overlap_start = max(entry_a, entry_b)
            overlap_end = min(exit_a, exit_b)
            if overlap_end > overlap_start:
                overlap_duration += overlap_end - overlap_start

    if total_duration <= 0:
        return 0.0
    return min(1.0, overlap_duration / total_duration)


def compute_factor_overlap(
    features_a: Dict[str, float],
    features_b: Dict[str, float],
) -> float:
    """Cosine similarity of feature importance vectors.

    features_a, features_b: {feature_name: importance_weight}.
    Returns cosine similarity in [0, 1] (absolute value).
    """
    all_keys = set(features_a.keys()) | set(features_b.keys())
    if not all_keys:
        return 0.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0

    for k in all_keys:
        va = features_a.get(k, 0.0)
        vb = features_b.get(k, 0.0)
        dot += va * vb
        norm_a += va * va
        norm_b += vb * vb

    denom = math.sqrt(norm_a) * math.sqrt(norm_b)
    if denom < 1e-12:
        return 0.0
    return abs(dot / denom)


@dataclass
class CannibalizationScore:
    """Composite score measuring strategy cannibalization risk."""
    signal: float = 0.0    # Signal overlap component
    timing: float = 0.0    # Timing overlap component
    factor: float = 0.0    # Factor overlap component
    capacity: float = 0.0  # Capacity overlap component
    composite: float = 0.0  # Weighted composite


def cannibalization_score(
    strategy_a: str,
    strategy_b: str,
    signals: Dict[str, List[Dict]],
    positions: Dict[str, List[Dict]],
    features: Dict[str, Dict[str, float]],
    capacity_overlap: float = 0.0,
) -> CannibalizationScore:
    """Compute cannibalization risk between two strategies.

    Weighted composite: 0.3*signal + 0.3*timing + 0.2*factor + 0.2*capacity.

    Args:
        strategy_a, strategy_b: strategy names
        signals: {strategy_name: [signal_dicts]}
        positions: {strategy_name: [position_dicts]}
        features: {strategy_name: {feature: importance}}
        capacity_overlap: pre-computed capacity overlap [0, 1]
    """
    sig_a = signals.get(strategy_a, [])
    sig_b = signals.get(strategy_b, [])
    pos_a = positions.get(strategy_a, [])
    pos_b = positions.get(strategy_b, [])
    feat_a = features.get(strategy_a, {})
    feat_b = features.get(strategy_b, {})

    sig_overlap = compute_signal_overlap(sig_a, sig_b)
    tim_overlap = compute_timing_overlap(pos_a, pos_b)
    fac_overlap = compute_factor_overlap(feat_a, feat_b)
    cap_overlap = max(0.0, min(1.0, capacity_overlap))

    composite = 0.3 * sig_overlap + 0.3 * tim_overlap + 0.2 * fac_overlap + 0.2 * cap_overlap

    return CannibalizationScore(
        signal=round(sig_overlap, 4),
        timing=round(tim_overlap, 4),
        factor=round(fac_overlap, 4),
        capacity=round(cap_overlap, 4),
        composite=round(composite, 4),
    )


if __name__ == "__main__":
    # Smoke test
    r = check_time_of_day_risk(14)
    print(f"CHECK 36 at 14:00 UTC: scale={r.value}")
    r = check_country_concentration(
        [{"exchange": "LSE", "notional": 3000}, {"exchange": "LSE", "notional": 4000}],
        "LSE",
    )
    print(f"CHECK 34 with 3 LSE positions: passed={r.passed}, reason={r.reason}")
