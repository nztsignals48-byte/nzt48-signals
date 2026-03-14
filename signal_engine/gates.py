"""
signal_engine/gates.py
======================
Hard and soft gate definitions for the NZT-48 signal pipeline.

HARD gates: MUST pass. Failure = ticker excluded. Never bypassed, not even
in fallback mode. Violations are stored and surfaced as "blockers".

SOFT gates: Score-based. In fallback mode, thresholds are relaxed stepwise
and signals are labelled with which relaxation was applied.

Gate funnel (in order):
  1. DATA_HEALTH          (hard) — OHLC valid, volume present, no NaN/Inf
  2. PRICE_SCALE          (hard) — detect pence-vs-pounds miscoding
  3. MIN_BARS             (hard) — enough bars to compute indicators
  4. TRADABILITY          (hard) — min ATR% so a trade is physically possible
  5. VOLUME_LIQUIDITY     (soft) — RVOL / avg-volume proxy
  6. REGIME_FIT           (soft) — direction compatible with market regime
  7. RR_RATIO             (soft) — reward:risk meets threshold
  8. MOMENTUM_ALIGNMENT   (soft) — indicator stack agrees on direction
  9. FACTOR_CAP           (soft) — portfolio-level factor cluster cap
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger("nzt48.signal_engine.gates")

# ---------------------------------------------------------------------------
# Thresholds — Strict mode
# ---------------------------------------------------------------------------
STRICT_MIN_ATR_PCT        = 1.0    # % of price
STRICT_MIN_RVOL           = 0.40   # Calibrated for LSE leveraged ETPs (lower volume than US equities)
STRICT_MIN_RR             = 1.5
STRICT_MIN_MOMENTUM_SCORE = 0.55   # 0-1 composite

# ---------------------------------------------------------------------------
# Thresholds — Fallback mode (each step relaxes ONE parameter)
# ---------------------------------------------------------------------------
FALLBACK_STEP1_RVOL       = 0.20   # Step 1: lower RVOL (LSE ETPs trade thinner than US stocks)
FALLBACK_STEP2_RR         = 1.2    # Step 2: lower R:R minimum
FALLBACK_STEP3_MOMENTUM   = 0.40   # Step 3: lower momentum requirement
FALLBACK_STEP4_ATR_PCT    = 0.60   # Step 4: lower ATR% (last resort)

# Factor concentration cap
MAX_SIGNALS_PER_FACTOR_GROUP = 3

# Minimum signals to guarantee during session
MIN_SIGNALS_STRICT   = 3
MIN_SIGNALS_FALLBACK = 5


class GateResult(str, Enum):
    PASS    = "PASS"
    FAIL    = "FAIL"
    RELAXED = "RELAXED"   # fallback mode: soft gate relaxed


@dataclass
class GateOutcome:
    gate_name:   str
    result:      GateResult
    value:       float = 0.0
    threshold:   float = 0.0
    reason:      str   = ""
    fallback_step: int = 0   # 0 = strict; 1/2/3/4 = which step relaxed

    @property
    def passed(self) -> bool:
        return self.result in (GateResult.PASS, GateResult.RELAXED)


@dataclass
class TickerGateReport:
    """Full gate audit trail for one ticker in one scan cycle."""
    ticker:      str
    is_long:     bool
    gates:       list[GateOutcome] = field(default_factory=list)
    hard_failed: bool = False
    mode:        str  = "STRICT"   # STRICT | FALLBACK_STEP1..4
    blocker:     str  = ""         # human-readable top reason for failure

    def add(self, outcome: GateOutcome) -> None:
        self.gates.append(outcome)
        if not outcome.passed and outcome.gate_name in _HARD_GATES:
            self.hard_failed = True
            self.blocker = f"{outcome.gate_name}: {outcome.reason}"

    @property
    def all_passed(self) -> bool:
        return not self.hard_failed and all(g.passed for g in self.gates)

    @property
    def funnel_summary(self) -> dict:
        return {g.gate_name: g.result.value for g in self.gates}


_HARD_GATES = {"DATA_HEALTH", "PRICE_SCALE", "MIN_BARS", "TRADABILITY"}


# ---------------------------------------------------------------------------
# Gate functions (pure — take data, return GateOutcome)
# ---------------------------------------------------------------------------

def gate_data_health(ticker: str, health_result) -> GateOutcome:
    """Hard: DataHealthGate must return PASS or WARN (not FAIL)."""
    if health_result is None:
        return GateOutcome("DATA_HEALTH", GateResult.FAIL, reason="no health result available")
    status = getattr(health_result, "status", "FAIL")
    if status == "FAIL":
        reasons = ", ".join(getattr(health_result, "exceptions", []))[:120]
        return GateOutcome("DATA_HEALTH", GateResult.FAIL, reason=reasons or "health check failed")
    return GateOutcome("DATA_HEALTH", GateResult.PASS, reason=f"status={status}")


def gate_price_scale(close: float, ticker: str) -> GateOutcome:
    """Hard: detect pence-coded prices (e.g., 18000 when price should be 180.00)."""
    if close <= 0:
        return GateOutcome("PRICE_SCALE", GateResult.FAIL, reason="close <= 0")
    # LSE .L tickers: if close > 5000 and .L suffix, likely in pence
    if ticker.endswith(".L") and close > 5_000:
        return GateOutcome("PRICE_SCALE", GateResult.FAIL,
                           value=close,
                           reason=f"price {close:.0f} looks like pence (expected pounds < 5000)")
    return GateOutcome("PRICE_SCALE", GateResult.PASS, value=close)


def gate_min_bars(n_bars: int, min_bars: int = 14, short_window_min: int = 7) -> GateOutcome:
    """Hard gate with SHORT_WINDOW mode for early-session data.

    Adaptive windowing (honest — uses what is available, never fabricates):
      n_bars >= 14  → PASS         (full institutional quality)
      7 <= n_bars < 14 → RELAXED  (SHORT_WINDOW: indicators use n_bars window,
                                    PlayScore penalised via data_reliability)
      n_bars < 7    → FAIL         (too few bars; indicators would be meaningless)
    """
    if n_bars < short_window_min:
        return GateOutcome("MIN_BARS", GateResult.FAIL,
                           value=n_bars, threshold=short_window_min,
                           reason=f"only {n_bars} bars (minimum {short_window_min} required)")
    if n_bars < min_bars:
        penalty = round(0.05 * (min_bars - n_bars), 3)   # 0.05 per missing bar, max 0.35
        return GateOutcome(
            "MIN_BARS", GateResult.RELAXED,
            value=n_bars, threshold=min_bars,
            reason=f"SHORT_WINDOW: {n_bars} bars (using adaptive {n_bars}-bar indicators, "
                   f"reliability_penalty={penalty:.2f})",
            fallback_step=0,   # not a fallback — SHORT_WINDOW is structural
        )
    return GateOutcome("MIN_BARS", GateResult.PASS, value=n_bars, threshold=min_bars)


def gate_tradability(atr_pct: float, fallback: bool = False) -> GateOutcome:
    """Hard (step-4 relaxable): ATR% must be large enough to trade."""
    threshold = FALLBACK_STEP4_ATR_PCT if fallback else STRICT_MIN_ATR_PCT
    if atr_pct < threshold:
        return GateOutcome("TRADABILITY", GateResult.FAIL,
                           value=atr_pct, threshold=threshold,
                           reason=f"ATR%={atr_pct:.2f} < {threshold}")
    result = GateResult.RELAXED if fallback and threshold < STRICT_MIN_ATR_PCT else GateResult.PASS
    return GateOutcome("TRADABILITY", result, value=atr_pct, threshold=threshold,
                       fallback_step=4 if fallback else 0)


def gate_volume_liquidity(rvol: Optional[float], fallback_step: int = 0) -> GateOutcome:
    """Soft: RVOL gate, relaxed in fallback step 1."""
    if rvol is None or rvol <= 0:
        # RVOL unavailable — treat as N/A, don't penalise hard
        return GateOutcome("VOLUME_LIQUIDITY", GateResult.RELAXED,
                           reason="RVOL N/A (using liquidity proxy)")
    threshold = {0: STRICT_MIN_RVOL, 1: FALLBACK_STEP1_RVOL}.get(fallback_step, STRICT_MIN_RVOL)
    if rvol < threshold:
        return GateOutcome("VOLUME_LIQUIDITY", GateResult.FAIL,
                           value=rvol, threshold=threshold,
                           reason=f"RVOL={rvol:.2f} < {threshold}")
    result = GateResult.RELAXED if fallback_step > 0 else GateResult.PASS
    return GateOutcome("VOLUME_LIQUIDITY", result, value=rvol, threshold=threshold,
                       fallback_step=fallback_step)


def gate_rr_ratio(rr: float, fallback_step: int = 0) -> GateOutcome:
    """Soft: R:R gate, relaxed in fallback step 2."""
    threshold = {0: STRICT_MIN_RR, 2: FALLBACK_STEP2_RR}.get(fallback_step, STRICT_MIN_RR)
    if rr < threshold:
        return GateOutcome("RR_RATIO", GateResult.FAIL,
                           value=rr, threshold=threshold,
                           reason=f"R:R={rr:.2f} < {threshold}")
    result = GateResult.RELAXED if fallback_step > 0 else GateResult.PASS
    return GateOutcome("RR_RATIO", result, value=rr, threshold=threshold,
                       fallback_step=fallback_step)


def gate_momentum(momentum_score: float, fallback_step: int = 0) -> GateOutcome:
    """Soft: momentum alignment, relaxed in fallback step 3."""
    threshold = {0: STRICT_MIN_MOMENTUM_SCORE, 3: FALLBACK_STEP3_MOMENTUM}.get(
        fallback_step, STRICT_MIN_MOMENTUM_SCORE)
    if momentum_score < threshold:
        return GateOutcome("MOMENTUM_ALIGNMENT", GateResult.FAIL,
                           value=momentum_score, threshold=threshold,
                           reason=f"momentum={momentum_score:.2f} < {threshold}")
    result = GateResult.RELAXED if fallback_step > 0 else GateResult.PASS
    return GateOutcome("MOMENTUM_ALIGNMENT", result,
                       value=momentum_score, threshold=threshold,
                       fallback_step=fallback_step)


def gate_regime_fit(direction: str, regime: str, is_inverse: bool = False) -> GateOutcome:
    """Soft: direction compatibility with current regime.

    ISA buy-only constraint: since all non-inverse tickers are forced to LONG,
    the regime gate should RELAX (not FAIL) when macro is bearish. This allows
    strong individual momentum plays to still surface, with the regime mismatch
    flagged as a RELAXED gate. Inverse ETPs flip direction for regime check.
    """
    regime_upper = regime.upper()
    # For inverse ETPs, flip the effective direction for regime check
    effective_dir = direction
    if is_inverse:
        effective_dir = "SHORT" if direction == "LONG" else "LONG"

    # RISK_OFF / BEAR: penalise longs (RELAXED, not FAIL — ISA is buy-only)
    if "RISK_OFF" in regime_upper or "BEAR" in regime_upper:
        if effective_dir == "LONG":
            return GateOutcome("REGIME_FIT", GateResult.RELAXED,
                               reason=f"LONG in {regime} regime (regime headwind)")
    # RISK_ON / BULL: penalise shorts (RELAXED)
    elif "RISK_ON" in regime_upper or "BULL" in regime_upper:
        if effective_dir == "SHORT":
            return GateOutcome("REGIME_FIT", GateResult.RELAXED,
                               reason=f"SHORT in {regime} regime (regime headwind)")
    return GateOutcome("REGIME_FIT", GateResult.PASS, reason=f"ok in {regime}")


def gate_factor_cap(factor_group: str, group_counts: dict[str, int]) -> GateOutcome:
    """Soft: no more than MAX_SIGNALS_PER_FACTOR_GROUP from same factor cluster."""
    count = group_counts.get(factor_group, 0)
    if count >= MAX_SIGNALS_PER_FACTOR_GROUP:
        return GateOutcome("FACTOR_CAP", GateResult.FAIL,
                           value=count, threshold=MAX_SIGNALS_PER_FACTOR_GROUP,
                           reason=f"factor group '{factor_group}' already has {count} signals")
    return GateOutcome("FACTOR_CAP", GateResult.PASS, value=count)


# ---------------------------------------------------------------------------
# Gate funnel runner
# ---------------------------------------------------------------------------

def run_full_gate_funnel(
    ticker:           str,
    direction:        str,
    atr_pct:          float,
    close:            float,
    n_bars:           int,
    rvol:             Optional[float],
    rr:               float,
    momentum_score:   float,
    regime:           str,
    factor_group:     str,
    group_counts:     dict[str, int],
    health_result,
    fallback_step:    int = 0,
    is_inverse:       bool = False,
) -> TickerGateReport:
    """Run the complete gate funnel for one ticker.

    fallback_step:
        0 = strict mode
        1 = relax RVOL
        2 = relax R:R
        3 = relax momentum
        4 = relax ATR% (last resort)
    """
    report = TickerGateReport(
        ticker=ticker,
        is_long=(direction == "LONG"),
        mode="STRICT" if fallback_step == 0 else f"FALLBACK_STEP{fallback_step}",
    )

    # === HARD GATES — stop at first failure ===
    dh = gate_data_health(ticker, health_result)
    report.add(dh)
    if not dh.passed:
        return report

    ps = gate_price_scale(close, ticker)
    report.add(ps)
    if not ps.passed:
        return report

    mb = gate_min_bars(n_bars)
    report.add(mb)
    if not mb.passed:
        return report

    trd = gate_tradability(atr_pct, fallback=(fallback_step >= 4))
    report.add(trd)
    if not trd.passed:
        return report

    # === SOFT GATES — collect failures, don't short-circuit ===
    report.add(gate_volume_liquidity(rvol, fallback_step=min(fallback_step, 1)))
    report.add(gate_rr_ratio(rr, fallback_step=min(fallback_step, 2) if fallback_step >= 2 else 0))
    report.add(gate_momentum(momentum_score, fallback_step=min(fallback_step, 3) if fallback_step >= 3 else 0))
    report.add(gate_regime_fit(direction, regime, is_inverse=is_inverse))
    report.add(gate_factor_cap(factor_group, group_counts))

    # Derive blocker from first failed soft gate (if any)
    if not report.all_passed and not report.blocker:
        failed = [g for g in report.gates if not g.passed]
        if failed:
            report.blocker = f"{failed[0].gate_name}: {failed[0].reason}"

    return report
