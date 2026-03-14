"""
S9 — Pairs Trading

Market-neutral strategy exploiting mean reversion in defined pairs via
price ratio Z-scores.

Defined pairs:
  - NVDA / AMD       (GPU duopoly)
  - AVGO / MRVL      (Broadcom vs Marvell — networking semis)
  - TSM  / ASML      (Foundry vs equipment — semi supply chain)
  - QQQ3 / QQQS      (ISA pair — both BUY orders = beta-neutral)

Signal logic:
  - Compute price ratio for each pair: A / B
  - Calculate 20-day rolling Z-score of the ratio
  - Z > +2:  Short the outperformer (A), long the underperformer (B)
  - Z < -2:  Long the outperformer (A), short the underperformer (B)
  - Stop:    Z-score moves to +/-3 (further divergence)
  - Target:  Z-score returns to 0 (convergence)

Risk management:
  - 0.5% risk per leg (1% total per pair)
  - ISA pairs work because both legs are BUY orders:
    long QQQ3 + long QQQS = beta-neutral
"""

from __future__ import annotations

import logging
import math
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.base import StrategyBase
from models import (
    Signal,
    IndicatorSnapshot,
    MarketContext,
    SectorFlow,
    NarrativeContext,
    Direction,
    Bot,
    BotInstance,
    RegimeState,
    GEXRegime,
    ConfidenceBreakdown,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ZSCORE_ENTRY_THRESHOLD: float = 2.0   # |Z| > 2 to enter
ZSCORE_STOP_THRESHOLD: float = 3.0    # |Z| > 3 = stop out (further divergence)
ZSCORE_TARGET: float = 0.0            # Z returns to 0 = convergence

LOOKBACK_PERIOD: int = 20             # 20-day rolling window for Z-score
RISK_PER_LEG: float = 0.005           # 0.5% risk per leg
TOTAL_RISK_PER_PAIR: float = 0.01     # 1% total per pair

# Defined pairs: (ticker_a, ticker_b, description, is_isa_pair)
DEFINED_PAIRS: list[tuple[str, str, str, bool]] = [
    ("NVDA", "AMD", "GPU duopoly", False),
    ("AVGO", "MRVL", "Networking semis", False),
    ("TSM", "ASML", "Semi supply chain", False),
    ("QQQ3", "QQQS", "ISA beta-neutral", True),
]


@dataclass
class PairState:
    """Internal state for tracking a single pair's ratio history."""
    ticker_a: str
    ticker_b: str
    description: str
    is_isa_pair: bool
    ratio_history: deque = field(default_factory=lambda: deque(maxlen=LOOKBACK_PERIOD))
    current_zscore: float = 0.0
    is_active: bool = False  # True if a position is open on this pair

    @property
    def pair_id(self) -> str:
        return f"{self.ticker_a}/{self.ticker_b}"

    def has_sufficient_history(self) -> bool:
        """Need at least LOOKBACK_PERIOD data points for valid Z-score."""
        return len(self.ratio_history) >= LOOKBACK_PERIOD


class PairsTrade(StrategyBase):
    """S9 Pairs Trading strategy.

    Monitors defined pairs for ratio Z-score divergence beyond +/-2
    standard deviations, then enters a market-neutral position expecting
    mean reversion to the historical ratio.
    """

    def __init__(self) -> None:
        super().__init__(
            name="Pairs Trading",
            strategy_id="S9",
        )
        self._pairs: list[PairState] = [
            PairState(
                ticker_a=a,
                ticker_b=b,
                description=desc,
                is_isa_pair=isa,
            )
            for a, b, desc, isa in DEFINED_PAIRS
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(
        self,
        tickers: list[str],
        indicators: dict[str, IndicatorSnapshot],
        market_ctx: MarketContext,
        sector_flows: dict[str, SectorFlow],
        narratives: dict[str, NarrativeContext],
    ) -> list[Signal]:
        """Scan all defined pairs for Z-score divergence signals.

        For each pair:
        1. Calculate the current price ratio
        2. Update the rolling history
        3. Compute 20-day Z-score
        4. Generate signal if |Z| > 2
        """
        if not self.enabled:
            return []

        signals: list[Signal] = []

        for pair in self._pairs:
            # Skip pairs with active positions (one position per pair at a time)
            if pair.is_active:
                continue

            snap_a = indicators.get(pair.ticker_a)
            snap_b = indicators.get(pair.ticker_b)

            if snap_a is None or snap_b is None:
                self.logger.debug(
                    "Missing data for pair %s: A=%s B=%s",
                    pair.pair_id,
                    "OK" if snap_a else "MISSING",
                    "OK" if snap_b else "MISSING",
                )
                continue

            if snap_b.price <= 0:
                self.logger.warning(
                    "Zero price for %s in pair %s — cannot compute ratio.",
                    pair.ticker_b,
                    pair.pair_id,
                )
                continue

            # --- Step 1: Compute current ratio ---
            ratio = snap_a.price / snap_b.price
            pair.ratio_history.append(ratio)

            # --- Step 2: Check if we have enough history ---
            if not pair.has_sufficient_history():
                self.logger.debug(
                    "Pair %s: building history (%d/%d)",
                    pair.pair_id,
                    len(pair.ratio_history),
                    LOOKBACK_PERIOD,
                )
                continue

            # --- Step 3: Compute Z-score ---
            zscore = self._compute_zscore(pair.ratio_history, ratio)
            pair.current_zscore = zscore

            # --- Step 4: Check for entry signal ---
            pair_signals = self._evaluate_pair(
                pair, zscore, snap_a, snap_b, market_ctx
            )
            signals.extend(pair_signals)

        return signals

    def release_pair(self, ticker_a: str, ticker_b: str) -> None:
        """Mark a pair as no longer active (position closed)."""
        for pair in self._pairs:
            if pair.ticker_a == ticker_a and pair.ticker_b == ticker_b:
                pair.is_active = False
                self.logger.info("Pair released: %s", pair.pair_id)
                return

    # ------------------------------------------------------------------
    # Z-score computation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_zscore(history: deque, current_value: float) -> float:
        """Compute the Z-score of the current value relative to the rolling window.

        Z = (x - mean) / std
        Returns 0 if standard deviation is zero (no divergence).
        """
        if len(history) < 2:
            return 0.0

        values = list(history)
        n = len(values)
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / (n - 1)  # Sample variance
        std = math.sqrt(variance) if variance > 0 else 0.0

        if std < 1e-10:
            return 0.0

        return (current_value - mean) / std

    # ------------------------------------------------------------------
    # Pair evaluation
    # ------------------------------------------------------------------

    def _evaluate_pair(
        self,
        pair: PairState,
        zscore: float,
        snap_a: IndicatorSnapshot,
        snap_b: IndicatorSnapshot,
        market_ctx: MarketContext,
    ) -> list[Signal]:
        """Evaluate a pair for entry based on Z-score.

        Z > +2: A has outperformed B excessively -> Short A, Long B
        Z < -2: B has outperformed A excessively -> Long A, Short B

        For ISA pairs (QQQ3/QQQS), both legs are BUY orders because
        one is already an inverse ETP.
        """
        if abs(zscore) < ZSCORE_ENTRY_THRESHOLD:
            return []

        signals: list[Signal] = []

        if zscore > ZSCORE_ENTRY_THRESHOLD:
            # A is the outperformer — short A, long B
            self.logger.info(
                "Pair %s Z=%.2f > %.1f: Short %s, Long %s",
                pair.pair_id,
                zscore,
                ZSCORE_ENTRY_THRESHOLD,
                pair.ticker_a,
                pair.ticker_b,
            )

            if pair.is_isa_pair:
                # ISA pair: both legs are LONG (buy orders)
                sig_a = self._build_pair_leg(
                    pair=pair,
                    ticker=pair.ticker_b,  # Long the underperformer (QQQS = short exposure)
                    snap=snap_b,
                    direction=Direction.LONG,
                    market_ctx=market_ctx,
                    zscore=zscore,
                    leg="LONG_LEG",
                )
                sig_b = self._build_pair_leg(
                    pair=pair,
                    ticker=pair.ticker_a,  # Long the "short" ETP for hedge
                    snap=snap_a,
                    direction=Direction.LONG,
                    market_ctx=market_ctx,
                    zscore=zscore,
                    leg="HEDGE_LEG",
                )
            else:
                # Standard pair: short the outperformer, long the underperformer
                sig_a = self._build_pair_leg(
                    pair=pair,
                    ticker=pair.ticker_a,
                    snap=snap_a,
                    direction=Direction.SHORT,
                    market_ctx=market_ctx,
                    zscore=zscore,
                    leg="SHORT_LEG",
                )
                sig_b = self._build_pair_leg(
                    pair=pair,
                    ticker=pair.ticker_b,
                    snap=snap_b,
                    direction=Direction.LONG,
                    market_ctx=market_ctx,
                    zscore=zscore,
                    leg="LONG_LEG",
                )

            if sig_a is not None:
                signals.append(sig_a)
            if sig_b is not None:
                signals.append(sig_b)

        elif zscore < -ZSCORE_ENTRY_THRESHOLD:
            # B is the outperformer — long A, short B
            self.logger.info(
                "Pair %s Z=%.2f < -%.1f: Long %s, Short %s",
                pair.pair_id,
                zscore,
                ZSCORE_ENTRY_THRESHOLD,
                pair.ticker_a,
                pair.ticker_b,
            )

            if pair.is_isa_pair:
                # ISA pair: both legs are LONG
                sig_a = self._build_pair_leg(
                    pair=pair,
                    ticker=pair.ticker_a,  # Long the underperformer
                    snap=snap_a,
                    direction=Direction.LONG,
                    market_ctx=market_ctx,
                    zscore=zscore,
                    leg="LONG_LEG",
                )
                sig_b = self._build_pair_leg(
                    pair=pair,
                    ticker=pair.ticker_b,  # Long the "short" ETP
                    snap=snap_b,
                    direction=Direction.LONG,
                    market_ctx=market_ctx,
                    zscore=zscore,
                    leg="HEDGE_LEG",
                )
            else:
                sig_a = self._build_pair_leg(
                    pair=pair,
                    ticker=pair.ticker_a,
                    snap=snap_a,
                    direction=Direction.LONG,
                    market_ctx=market_ctx,
                    zscore=zscore,
                    leg="LONG_LEG",
                )
                sig_b = self._build_pair_leg(
                    pair=pair,
                    ticker=pair.ticker_b,
                    snap=snap_b,
                    direction=Direction.SHORT,
                    market_ctx=market_ctx,
                    zscore=zscore,
                    leg="SHORT_LEG",
                )

            if sig_a is not None:
                signals.append(sig_a)
            if sig_b is not None:
                signals.append(sig_b)

        # Mark pair as active if we generated both legs
        if len(signals) == 2:
            pair.is_active = True
        elif len(signals) == 1:
            # Only got one leg — do not enter a half-pair
            self.logger.warning(
                "Pair %s: only one leg built. Discarding incomplete pair.",
                pair.pair_id,
            )
            return []

        return signals

    # ------------------------------------------------------------------
    # Signal construction
    # ------------------------------------------------------------------

    def _build_pair_leg(
        self,
        pair: PairState,
        ticker: str,
        snap: IndicatorSnapshot,
        direction: Direction,
        market_ctx: MarketContext,
        zscore: float,
        leg: str,
    ) -> Optional[Signal]:
        """Build a Signal for one leg of a pairs trade.

        Stop: When Z-score would move to 3 (estimate from ATR).
        Target: When Z-score returns to 0 (convergence).
        """
        entry = snap.price
        atr = snap.atr14

        if entry <= 0 or atr <= 0:
            self.logger.warning("Invalid price or ATR for %s.", ticker)
            return None

        # Estimate the price move corresponding to Z=3 and Z=0
        # The ratio Z-score maps to individual stock moves approximately
        # as: delta_price ~ (delta_Z * std_ratio * price_B) for ticker A
        # We use ATR as a practical stop distance proxy
        zscore_to_stop = ZSCORE_STOP_THRESHOLD - abs(zscore)  # Distance to Z=3
        zscore_to_target = abs(zscore) - abs(ZSCORE_TARGET)   # Distance to Z=0

        # Map Z-score movement to price via ATR scaling
        # Each unit of Z ~ 0.5 ATR (empirical estimate)
        atr_per_z = 0.5 * atr

        stop_distance = max(atr_per_z * zscore_to_stop, 0.5 * atr)  # Floor at 0.5 ATR
        target_distance = atr_per_z * zscore_to_target

        if direction == Direction.LONG:
            stop = entry - stop_distance
            target_1r = entry + target_distance
            # Conservative second target: halfway between convergence and entry
            target_2r = entry + (target_distance * 0.5)
        else:
            stop = entry + stop_distance
            target_1r = entry - target_distance
            target_2r = entry - (target_distance * 0.5)

        signal = self._create_signal(
            ticker=ticker,
            direction=direction.value,
            entry=entry,
            stop=stop,
            indicators=snap,
            market_ctx=market_ctx,
        )

        signal.risk_pct = RISK_PER_LEG
        signal.target_1r = round(target_1r, 4)
        signal.target_2r = round(target_2r, 4)
        signal.timeframe_layer = "SWING"

        # Bot assignment
        if pair.is_isa_pair:
            signal.bot = Bot.A
            signal.isa_ticker = ticker
            signal.isa_leverage = "3x"
            signal.isa_underlying = "QQQ"
        else:
            signal.bot = Bot.B

        signal.bot_instance = BotInstance.RANGE  # Pairs are regime-agnostic

        # Confidence
        confidence = self._compute_pairs_confidence(zscore, snap, market_ctx, pair)
        signal.confidence = confidence.final_score
        signal.confidence_breakdown = confidence

        # Qualification log
        signal.qualification_log.append(
            f"PAIRS: {pair.pair_id} Z={zscore:+.2f} | {leg}"
        )
        signal.qualification_log.append(
            f"entry_threshold={ZSCORE_ENTRY_THRESHOLD} "
            f"stop_threshold={ZSCORE_STOP_THRESHOLD} "
            f"target=Z->0"
        )
        signal.qualification_log.append(f"pair_desc={pair.description}")

        return signal

    @staticmethod
    def _compute_pairs_confidence(
        zscore: float,
        snap: IndicatorSnapshot,
        market_ctx: MarketContext,
        pair: PairState,
    ) -> ConfidenceBreakdown:
        """Compute confidence for a pairs trade leg.

        Pairs trading confidence is driven primarily by:
        - Z-score magnitude (stronger divergence = higher confidence)
        - Historical ratio stability (lower vol of ratio = more reliable)
        - Market regime (pairs work in all regimes, but best in range-bound)
        """
        cb = ConfidenceBreakdown()

        # Layer 1: Price action — Z-score strength
        z_strength = abs(zscore)
        if z_strength >= 2.5:
            cb.layer1_price_action = 35.0
        elif z_strength >= 2.0:
            cb.layer1_price_action = 25.0
        else:
            cb.layer1_price_action = 15.0

        # Bonus for clear mean-reverting pattern (RSI extremes)
        if snap.rsi14 < 30 or snap.rsi14 > 70:
            cb.layer1_price_action = min(45.0, cb.layer1_price_action + 5.0)

        # Layer 2: Regime — pairs work everywhere, best in range-bound
        if market_ctx.regime == RegimeState.RANGE_BOUND:
            cb.layer2_regime = 18.0
        elif market_ctx.regime in (
            RegimeState.TRENDING_UP_MOD, RegimeState.TRENDING_DOWN_MOD
        ):
            cb.layer2_regime = 14.0
        elif market_ctx.regime in (
            RegimeState.TRENDING_UP_STRONG, RegimeState.TRENDING_DOWN_STRONG
        ):
            cb.layer2_regime = 10.0  # Strong trends can break pairs
        else:
            cb.layer2_regime = 8.0

        # Layer 3: Ratio history quality
        if pair.has_sufficient_history():
            # More history = higher confidence
            history_fullness = len(pair.ratio_history) / LOOKBACK_PERIOD
            cb.layer3_sector_flow = min(15.0, history_fullness * 15.0)
        else:
            cb.layer3_sector_flow = 5.0

        # Layer 4: Macro — pairs are market-neutral, so macro matters less
        cb.layer4_macro = 5.0

        # Layer 5: ISA pairs get a bonus (simpler execution)
        if pair.is_isa_pair:
            cb.layer5_narrative = 8.0
        else:
            cb.layer5_narrative = 5.0

        # Penalties
        # Strong trending regimes can cause pairs to diverge further
        if market_ctx.regime in (
            RegimeState.TRENDING_UP_STRONG, RegimeState.TRENDING_DOWN_STRONG
        ):
            cb.penalties = 5.0

        # SHOCK or high vol can break correlations
        if market_ctx.regime in (RegimeState.SHOCK, RegimeState.HIGH_VOLATILITY):
            cb.penalties += 8.0

        # Event days add noise
        if market_ctx.fomc_today or market_ctx.cpi_nfp_today:
            cb.penalties += 3.0

        cb.compute()
        return cb
