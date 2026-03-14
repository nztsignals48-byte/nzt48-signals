"""
signal_engine/scoring.py
========================
Composite PlayScore (0-100) and star rating for NZT-48 plays.

Formula:
    PlayScore = 0.30*Momentum
              + 0.20*VolatilityOpportunity
              + 0.15*RegimeFit
              + 0.15*Liquidity
              + 0.10*RiskReward
              + 0.10*Quality

Star rating:
    90-100 = ★★★★★
    80-89  = ★★★★☆
    70-79  = ★★★☆☆
    60-69  = ★★☆☆☆
    <60    = ★☆☆☆☆  (included only for fallback / watch-signals)

Modifiers (each ±1 star, clamped 1-5):
    -1  factor cluster overloaded
    -1  decay risk critical in choppy regime
    -1  liquidity / spread risk high
    +1  multi-source agreement strong AND regime alignment strong
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------
W_MOMENTUM    = 0.30
W_VOLATILITY  = 0.20
W_REGIME      = 0.15
W_LIQUIDITY   = 0.15
W_RR          = 0.10
W_QUALITY     = 0.10


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class PlayScore:
    ticker:       str
    direction:    str

    # Component scores (each 0-1)
    momentum:     float = 0.0
    volatility:   float = 0.0
    regime_fit:   float = 0.0
    liquidity:    float = 0.0
    rr_score:     float = 0.0
    quality:      float = 0.0

    # Output
    composite:    float = 0.0    # 0-100
    stars:        int   = 1      # 1-5
    stars_str:    str   = "[*____]"
    label:        str   = ""     # STRICT / WATCH-SIGNAL / LOWER-CONVICTION
    reasons:      list[str] = field(default_factory=list)

    # Trade levels
    entry:        float = 0.0
    stop:         float = 0.0
    target1:      float = 0.0
    target2:      float = 0.0
    rr_ratio:     float = 0.0
    setup_type:   str   = ""

    # Risk metadata
    factor_group:      str   = ""
    atr_pct:           float = 0.0
    rvol:              float = 0.0
    rvol_reliable:     bool  = True
    data_reliability:  float = 1.0
    decay_risk:        str   = "LOW"
    spread_risk:       str   = "LOW"
    fallback_step:     int   = 0

    # Mode / track
    track:         str  = "INTRADAY_SWING"
    mode_label:    str  = "WIN_RATE"

    # Strategy Router (v3.0 — set by engine after router run)
    strategy_weighted_score: float = 0.0

    def __post_init__(self):
        self._compute()

    def _compute(self) -> None:
        raw = (
            self.momentum   * W_MOMENTUM  +
            self.volatility * W_VOLATILITY +
            self.regime_fit * W_REGIME     +
            self.liquidity  * W_LIQUIDITY  +
            self.rr_score   * W_RR         +
            self.quality    * W_QUALITY
        )
        # Penalise low data reliability
        reliability_penalty = max(0.0, 1.0 - self.data_reliability) * 0.15
        self.composite = round(max(0.0, raw - reliability_penalty) * 100, 1)
        self.stars     = _score_to_stars(self.composite)
        self.stars_str = _stars_str(self.stars)
        if self.label == "":
            self.label = "WATCH-SIGNAL" if self.fallback_step > 0 else "STRICT"


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def compute_play_score(
    ticker:           str,
    direction:        str,
    rsi:              float,
    macd_hist:        float,
    ema_aligned:      bool,
    atr_pct:          float,
    bb_width_rank:    float,   # 0-1 percentile of current BB width
    rvol:             Optional[float],
    adx:              float,
    regime:           str,
    rr_ratio:         float,
    factor_group:     str,
    group_counts:     dict[str, int],
    entry:            float,
    stop:             float,
    target1:          float,
    target2:          float,
    setup_type:       str     = "momentum",
    fallback_step:    int     = 0,
    multi_source_ok:  bool    = False,
) -> PlayScore:
    """Compute the composite PlayScore and return a PlayScore instance."""

    reasons: list[str] = []

    # --- 1. Momentum (RSI + MACD + EMA alignment) ---
    mom_parts: list[float] = []

    # RSI component (0-1)
    if direction == "LONG":
        rsi_s = _sigmoid_score(rsi, center=55, steepness=0.12)
    else:
        rsi_s = _sigmoid_score(100 - rsi, center=55, steepness=0.12)
    mom_parts.append(rsi_s)

    # MACD histogram direction agreement
    if direction == "LONG":
        macd_s = 0.8 if macd_hist > 0 else 0.2
    else:
        macd_s = 0.8 if macd_hist < 0 else 0.2
    mom_parts.append(macd_s)

    # EMA alignment
    mom_parts.append(0.9 if ema_aligned else 0.3)

    momentum_score = float(sum(mom_parts) / len(mom_parts))
    if momentum_score > 0.65:
        reasons.append(f"Momentum aligned (RSI={rsi:.0f}, MACD {'positive' if macd_hist > 0 else 'negative'})")

    # --- 2. Volatility Opportunity (ATR% + BB expansion) ---
    # ATR% — how much of a 2% target is "routine"?
    atr_s = min(atr_pct / 3.0, 1.0)    # 3% ATR = max score
    # BB width rank — expansion = opportunity
    bb_s = max(0.0, min(bb_width_rank, 1.0))
    volatility_score = 0.6 * atr_s + 0.4 * bb_s
    if atr_pct >= 2.0:
        reasons.append(f"ATR={atr_pct:.1f}% — routinely moves 2%+")

    # --- 3. Regime fit ---
    # ISA buy-only: direction is always LONG for non-inverse tickers.
    # In bearish regimes, a moderate penalty (0.5) differentiates vs bull (0.9)
    # without destroying the score — since we CAN'T short, the penalty
    # should be a headwind flag, not a veto.
    regime_upper = regime.upper()
    if "RISK_ON" in regime_upper or "BULL" in regime_upper:
        regime_s = 0.9 if direction == "LONG" else 0.5
    elif "RISK_OFF" in regime_upper or "BEAR" in regime_upper:
        regime_s = 0.9 if direction == "SHORT" else 0.5
    elif "NEUTRAL" in regime_upper or "CHOPPY" in regime_upper or "RANGE" in regime_upper:
        regime_s = 0.60
    else:
        regime_s = 0.60   # unknown / transitioning
    if regime_s >= 0.75:
        reasons.append(f"Regime ({regime}) favours {direction}")

    # --- 4. Liquidity ---
    if rvol is None or rvol <= 0:
        liquidity_s = 0.35   # RVOL N/A — below gate threshold (was 0.55, too generous)
    else:
        liquidity_s = min(rvol / 3.0, 1.0)   # 3x RVOL = max
        if rvol >= 1.5:
            reasons.append(f"RVOL={rvol:.1f}x — above-average volume")

    # --- 5. R:R ---
    rr_s = min((rr_ratio - 1.0) / 2.0, 1.0) if rr_ratio >= 1.0 else 0.0
    if rr_ratio >= 1.5:
        reasons.append(f"R:R={rr_ratio:.1f} — favourable risk/reward")

    # --- 6. Quality / consistency (ADX trend strength) ---
    quality_s = min(adx / 50.0, 1.0) if adx > 0 else 0.40
    if adx >= 25:
        reasons.append(f"ADX={adx:.0f} — trending market, not choppy")

    ps = PlayScore(
        ticker=ticker,
        direction=direction,
        momentum=momentum_score,
        volatility=volatility_score,
        regime_fit=regime_s,
        liquidity=liquidity_s,
        rr_score=rr_s,
        quality=quality_s,
        entry=entry,
        stop=stop,
        target1=target1,
        target2=target2,
        rr_ratio=rr_ratio,
        setup_type=setup_type,
        factor_group=factor_group,
        atr_pct=atr_pct,
        rvol=rvol or 0.0,
        fallback_step=fallback_step,
        reasons=reasons,
    )

    # --- Star modifiers ---
    star_adj = 0
    factor_count = group_counts.get(factor_group, 0)
    if factor_count >= 3:
        star_adj -= 1
        ps.reasons.append(f"[-1 star] factor cluster '{factor_group}' overloaded ({factor_count} signals)")

    if ps.decay_risk == "HIGH" and "CHOP" in regime_upper:
        star_adj -= 1
        ps.reasons.append("[-1 star] decay risk HIGH in choppy regime")

    if ps.spread_risk == "HIGH":
        star_adj -= 1
        ps.reasons.append("[-1 star] spread/liquidity risk HIGH")

    if multi_source_ok and regime_s >= 0.75:
        star_adj += 1
        ps.reasons.append("[+1 star] multi-source data agreement + regime alignment")

    ps.stars = max(1, min(5, ps.stars + star_adj))
    ps.stars_str = _stars_str(ps.stars)

    # Fallback labelling
    if fallback_step > 0:
        step_labels = {
            1: "RVOL-relaxed",
            2: "RR-relaxed",
            3: "MOMENTUM-relaxed",
            4: "ATR-relaxed",
        }
        ps.label = f"WATCH-SIGNAL ({step_labels.get(fallback_step, f'step{fallback_step}')})"
    else:
        ps.label = "STRICT"

    # Assign track: scalp if ATR high + short setup, swing otherwise
    # Scalp: BB breakout or very high ATR (>3%) and short setup type
    if setup_type == "breakout" and atr_pct >= 2.5:
        ps.track = "SCALP"
    elif setup_type == "continuation" or atr_pct < 3.0:
        ps.track = "INTRADAY_SWING"
    else:
        ps.track = "SCALP"

    return ps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sigmoid_score(x: float, center: float, steepness: float) -> float:
    """Smooth 0-1 score centred at `center`."""
    try:
        return 1.0 / (1.0 + math.exp(-steepness * (x - center)))
    except OverflowError:
        return 1.0 if x > center else 0.0


def _score_to_stars(score: float) -> int:
    if score >= 90:
        return 5
    elif score >= 80:
        return 4
    elif score >= 70:
        return 3
    elif score >= 60:
        return 2
    return 1


def _stars_str(n: int) -> str:
    """ASCII-safe star string for fpdf2 compatibility."""
    _MAP = {5: "[*****]", 4: "[****_]", 3: "[***__]", 2: "[**___]", 1: "[*____]"}
    return _MAP.get(n, "[*____]")


# ---------------------------------------------------------------------------
# Signal drought report
# ---------------------------------------------------------------------------

@dataclass
class SignalDroughtReport:
    """Returned when even fallback mode produces < MIN_SIGNALS_FALLBACK."""
    top_blockers:          list[str] = field(default_factory=list)
    tickers_checked:       int       = 0
    hard_fail_count:       int       = 0
    soft_fail_count:       int       = 0
    recommended_actions:   list[str] = field(default_factory=list)

    def to_text(self) -> str:
        lines = ["=== SIGNAL DROUGHT ==="]
        lines.append(f"Checked {self.tickers_checked} tickers — 0 signals after fallback mode.")
        lines.append("TOP BLOCKERS:")
        for i, b in enumerate(self.top_blockers[:5], 1):
            lines.append(f"  {i}. {b}")
        if self.recommended_actions:
            lines.append("RECOMMENDED ACTIONS:")
            for a in self.recommended_actions:
                lines.append(f"  - {a}")
        return "\n".join(lines)
