"""
Book 151/152: Agent Swarm Consensus — Deterministic Micro-Agent Ensemble

Lightweight ensemble of 10 rule-based "micro-agents" that independently vote on
whether a signal is sound. Each agent checks ONE aspect. The SwarmConsensus
aggregator computes a weighted consensus score and returns confidence adjustments.

NOT an LLM swarm (too expensive per tick). Pure deterministic rules. O(1) per tick,
no external deps, handles missing data gracefully.

Usage in bridge.py:
    from python_brain.swarm.signal_consensus import get_swarm
    result = get_swarm().evaluate(signal_dict=best, indicators_dict=ind, msg_dict=msg)
    if result.should_block:
        return None
    best["confidence"] += result.confidence_delta
    best["swarm_score"] = result.score
    best["swarm_votes"] = f"{result.n_agree}/{result.n_total}"
"""

import sys
import logging

logger = logging.getLogger(__name__)


# ─── Data Classes ────────────────────────────────────────────────────────────

class Vote:
    """Single micro-agent vote."""
    __slots__ = ("vote", "weight", "reason")

    def __init__(self, vote, weight, reason):
        self.vote = bool(vote)
        self.weight = max(0.0, min(1.0, float(weight)))
        self.reason = str(reason)

    def to_dict(self):
        return {"vote": self.vote, "weight": self.weight, "reason": self.reason}


class ConsensusResult:
    """Aggregated swarm consensus result."""
    __slots__ = (
        "score", "n_agree", "n_total", "adjustments",
        "should_dampen", "should_block", "should_boost",
        "confidence_delta", "votes",
    )

    def __init__(self, score, n_agree, n_total, adjustments, votes):
        self.score = score              # 0-100
        self.n_agree = n_agree
        self.n_total = n_total
        self.adjustments = adjustments  # dict of agent_name -> vote_dict
        self.votes = votes              # list of (agent_name, Vote)

        # Derived thresholds
        self.should_block = score < 25
        self.should_dampen = score < 40
        self.should_boost = score > 75

        # Compute confidence delta
        if self.should_block:
            # Block handled by caller (return None), but set delta for logging
            self.confidence_delta = -100
        elif self.should_dampen:
            self.confidence_delta = -20
        elif self.should_boost:
            self.confidence_delta = +5
        else:
            self.confidence_delta = 0

    def to_dict(self):
        return {
            "score": self.score,
            "n_agree": self.n_agree,
            "n_total": self.n_total,
            "should_dampen": self.should_dampen,
            "should_block": self.should_block,
            "should_boost": self.should_boost,
            "confidence_delta": self.confidence_delta,
            "adjustments": self.adjustments,
        }


# ─── Micro-Agent Base ────────────────────────────────────────────────────────

class MicroAgent:
    """Base class for all micro-agents. Subclasses implement evaluate()."""
    name = "BaseAgent"
    weight = 1.0  # Default weight, overridden per agent

    def evaluate(self, signal, ind, msg):
        """
        Evaluate the signal. Must return a Vote.
        Args:
            signal: signal dict (direction, confidence, kelly_fraction, strategy, etc.)
            ind: indicators dict (rvol, hurst, hurst_regime, bars_5m, spread_pct, etc.)
            msg: message dict (vix, time_fraction, last, equity, etc.)
        Returns:
            Vote(vote=True/False, weight=0.0-1.0, reason="...")
        """
        return Vote(True, 0.5, "neutral — base agent")

    def _neutral(self, reason="data unavailable"):
        """Return a neutral vote when data is missing. Neutral = True with low weight."""
        return Vote(True, 0.3, reason)

    def _is_long(self, signal):
        d = signal.get("direction", "").lower()
        return d == "long"

    def _is_short(self, signal):
        d = signal.get("direction", "").lower()
        return d in ("short", "flat")


# ─── Agent 1: Trend Alignment ────────────────────────────────────────────────

class TrendAlignmentAgent(MicroAgent):
    """Checks if signal direction aligns with 20-bar trend (price vs SMA20)."""
    name = "TrendAlignment"
    weight = 1.0

    def evaluate(self, signal, ind, msg):
        bars = ind.get("bars_5m")
        if not bars or len(bars) < 20:
            return self._neutral("insufficient bars for SMA20")

        closes = [b.get("close", 0) for b in bars[-20:] if b.get("close", 0) > 0]
        if len(closes) < 20:
            return self._neutral("insufficient valid closes for SMA20")

        sma20 = sum(closes) / len(closes)
        current = closes[-1]

        if sma20 <= 0:
            return self._neutral("SMA20 zero")

        is_long = self._is_long(signal)
        price_above_sma = current > sma20

        if is_long and price_above_sma:
            return Vote(True, self.weight, f"price {current:.2f} > SMA20 {sma20:.2f} — trend aligned for long")
        elif not is_long and not price_above_sma:
            return Vote(True, self.weight, f"price {current:.2f} < SMA20 {sma20:.2f} — trend aligned for short")
        elif is_long and not price_above_sma:
            return Vote(False, self.weight, f"price {current:.2f} < SMA20 {sma20:.2f} — counter-trend long")
        else:
            return Vote(False, self.weight, f"price {current:.2f} > SMA20 {sma20:.2f} — counter-trend short")


# ─── Agent 2: Volume Confirmation ────────────────────────────────────────────

class VolumeConfirmationAgent(MicroAgent):
    """Checks if RVOL > 1.2 — volume supports the move."""
    name = "VolumeConfirmation"
    weight = 0.9

    def evaluate(self, signal, ind, msg):
        rvol = ind.get("rvol")
        if rvol is None:
            # Also check signal dict (common_fields merged)
            rvol = signal.get("rvol")
        if rvol is None:
            return self._neutral("RVOL unavailable")

        if rvol > 1.2:
            return Vote(True, self.weight, f"RVOL {rvol:.2f} > 1.2 — volume confirms")
        elif rvol > 0.8:
            return Vote(False, 0.5, f"RVOL {rvol:.2f} — tepid volume, marginal")
        else:
            return Vote(False, self.weight, f"RVOL {rvol:.2f} < 0.8 — volume absent")


# ─── Agent 3: Spread Health ──────────────────────────────────────────────────

class SpreadHealthAgent(MicroAgent):
    """Checks if spread_pct < 0.5% — reasonable execution cost."""
    name = "SpreadHealth"
    weight = 0.8

    def evaluate(self, signal, ind, msg):
        spread = ind.get("spread_pct")
        if spread is None:
            spread = signal.get("spread_pct")
        if spread is None:
            return self._neutral("spread_pct unavailable")

        if spread < 0.15:
            return Vote(True, self.weight, f"spread {spread:.3f}% — excellent liquidity")
        elif spread < 0.50:
            return Vote(True, self.weight, f"spread {spread:.3f}% < 0.5% — acceptable")
        elif spread < 1.0:
            return Vote(False, 0.6, f"spread {spread:.3f}% — elevated, marginal")
        else:
            return Vote(False, self.weight, f"spread {spread:.3f}% >= 1.0% — too wide")


# ─── Agent 4: Momentum (RSI) ─────────────────────────────────────────────────

class MomentumAgent(MicroAgent):
    """Checks if RSI is not at extreme levels (not >85 for longs, not <15 for shorts)."""
    name = "Momentum"
    weight = 0.7

    def evaluate(self, signal, ind, msg):
        rsi = signal.get("rsi")
        if rsi is None:
            rsi = ind.get("rsi")
        if rsi is None or rsi == 0:
            return self._neutral("RSI unavailable")

        is_long = self._is_long(signal)

        if is_long:
            if rsi > 85:
                return Vote(False, self.weight, f"RSI {rsi:.1f} > 85 — overbought, long risky")
            elif rsi > 70:
                return Vote(True, 0.4, f"RSI {rsi:.1f} — elevated but not extreme for long")
            else:
                return Vote(True, self.weight, f"RSI {rsi:.1f} — room to run for long")
        else:
            if rsi < 15:
                return Vote(False, self.weight, f"RSI {rsi:.1f} < 15 — oversold, short risky")
            elif rsi < 30:
                return Vote(True, 0.4, f"RSI {rsi:.1f} — depressed but not extreme for short")
            else:
                return Vote(True, self.weight, f"RSI {rsi:.1f} — room to fall for short")


# ─── Agent 5: Volatility ─────────────────────────────────────────────────────

class VolatilityAgent(MicroAgent):
    """Checks if realized vol is within 1-3 ATR range (not too quiet, not too wild)."""
    name = "Volatility"
    weight = 0.8

    def evaluate(self, signal, ind, msg):
        rvol = ind.get("rvol")
        if rvol is None:
            rvol = signal.get("rvol")
        if rvol is None:
            return self._neutral("RVOL unavailable for vol check")

        # RVOL is a ratio: 1.0 = average volume. We use it as a proxy for
        # activity level. "1-3 ATR" maps to RVOL in [0.5, 3.0] for our purposes:
        # < 0.5 → too quiet (no participation), > 3.0 → too wild (whipsaw risk)
        if 0.5 <= rvol <= 3.0:
            return Vote(True, self.weight, f"RVOL {rvol:.2f} in [0.5, 3.0] — healthy activity")
        elif rvol < 0.5:
            return Vote(False, self.weight, f"RVOL {rvol:.2f} < 0.5 — too quiet, no participation")
        else:
            return Vote(False, self.weight, f"RVOL {rvol:.2f} > 3.0 — too wild, whipsaw risk")


# ─── Agent 6: Hurst Regime ───────────────────────────────────────────────────

class HurstAgent(MicroAgent):
    """Checks if hurst_regime matches signal type (trending for momentum, mean_rev for reversion)."""
    name = "HurstRegime"
    weight = 0.9

    # Strategies that need trending regime
    _TREND_STRATEGIES = frozenset({
        "S1_Microstructure", "S3_MacroTrend", "S5_BreakoutSwing",
        "S6_TrendAccumulation", "LeadLag", "S10_GapSurfer",
    })
    # Strategies that need mean-reverting regime
    _REVERSION_STRATEGIES = frozenset({
        "S2_Reversion", "S4_OvernightFade",
    })

    def evaluate(self, signal, ind, msg):
        regime = ind.get("hurst_regime")
        if regime is None:
            regime = signal.get("hurst_regime")
        if regime is None:
            return self._neutral("hurst_regime unavailable")

        strategy = signal.get("strategy", "")

        if strategy in self._TREND_STRATEGIES:
            if regime == "trending":
                return Vote(True, self.weight, f"regime={regime} — aligned for trend strategy {strategy}")
            elif regime == "random":
                return Vote(True, 0.4, f"regime=random — neutral for trend strategy {strategy}")
            else:
                return Vote(False, self.weight, f"regime={regime} — mean_reverting opposes trend strategy {strategy}")

        elif strategy in self._REVERSION_STRATEGIES:
            if regime == "mean_reverting":
                return Vote(True, self.weight, f"regime={regime} — aligned for reversion strategy {strategy}")
            elif regime == "random":
                return Vote(True, 0.4, f"regime=random — neutral for reversion strategy {strategy}")
            else:
                return Vote(False, self.weight, f"regime={regime} — trending opposes reversion strategy {strategy}")

        else:
            # Unknown strategy: regime check is neutral
            return Vote(True, 0.5, f"regime={regime} — unknown strategy {strategy}, neutral")


# ─── Agent 7: Time of Day ────────────────────────────────────────────────────

class TimeOfDayAgent(MicroAgent):
    """Checks if within optimal trading hours (not first/last 15 min of session).

    time_fraction: 0.0 = market open (08:00 London), 1.0 = market close (16:30).
    First 15 min ≈ time_fraction < 0.029 (15/510 minutes).
    Last 15 min ≈ time_fraction > 0.971.
    """
    name = "TimeOfDay"
    weight = 0.6

    # 15 minutes out of 510-minute session (8:00-16:30 = 510 min)
    _OPEN_THRESHOLD = 15.0 / 510.0   # ~0.0294
    _CLOSE_THRESHOLD = 1.0 - (15.0 / 510.0)  # ~0.9706

    def evaluate(self, signal, ind, msg):
        tf = msg.get("time_fraction")
        if tf is None:
            return self._neutral("time_fraction unavailable")

        if tf < self._OPEN_THRESHOLD:
            return Vote(False, self.weight, f"time_fraction={tf:.3f} — first 15 min, opening chaos")
        elif tf > self._CLOSE_THRESHOLD:
            return Vote(False, self.weight, f"time_fraction={tf:.3f} — last 15 min, closing volatility")
        elif 0.1 <= tf <= 0.85:
            return Vote(True, self.weight, f"time_fraction={tf:.3f} — core session, optimal")
        else:
            return Vote(True, 0.4, f"time_fraction={tf:.3f} — near open/close but within tolerance")


# ─── Agent 8: Regime (VIX) ───────────────────────────────────────────────────

class RegimeAgent(MicroAgent):
    """Checks if VIX < 35 for longs (crisis = danger). Shorts pass in high-VIX."""
    name = "Regime"
    weight = 1.0

    def evaluate(self, signal, ind, msg):
        vix = msg.get("vix")
        if vix is None:
            return self._neutral("VIX unavailable")

        is_long = self._is_long(signal)

        if is_long:
            if vix < 20:
                return Vote(True, self.weight, f"VIX {vix:.1f} < 20 — calm regime, safe for longs")
            elif vix < 35:
                return Vote(True, 0.6, f"VIX {vix:.1f} — elevated but not crisis, proceed with caution")
            else:
                return Vote(False, self.weight, f"VIX {vix:.1f} >= 35 — crisis regime, longs dangerous")
        else:
            # Shorts / hedges benefit from high VIX
            if vix >= 25:
                return Vote(True, self.weight, f"VIX {vix:.1f} >= 25 — fear regime supports short/hedge")
            elif vix >= 15:
                return Vote(True, 0.5, f"VIX {vix:.1f} — normal regime, neutral for short")
            else:
                return Vote(False, 0.6, f"VIX {vix:.1f} < 15 — complacency, shorting into calm")


# ─── Agent 9: Cost Efficiency ────────────────────────────────────────────────

class CostAgent(MicroAgent):
    """Checks if kelly_fraction * spread_cost < 0.1% (cost-effective entry).

    If the expected cost of entry (half-spread * position fraction) exceeds 0.1%
    of equity, the entry is not cost-effective.
    """
    name = "CostEfficiency"
    weight = 0.7

    def evaluate(self, signal, ind, msg):
        kelly = signal.get("kelly_fraction")
        if kelly is None:
            return self._neutral("kelly_fraction unavailable")

        spread_pct = ind.get("spread_pct")
        if spread_pct is None:
            spread_pct = signal.get("spread_pct")
        if spread_pct is None:
            return self._neutral("spread_pct unavailable for cost check")

        # Cost = kelly_fraction * half_spread (you pay half the spread on entry)
        # spread_pct is in percentage points (e.g., 0.3 means 0.3%)
        half_spread_pct = spread_pct / 2.0
        entry_cost_pct = kelly * half_spread_pct

        if entry_cost_pct < 0.05:
            return Vote(True, self.weight, f"cost={entry_cost_pct:.4f}% — very efficient")
        elif entry_cost_pct < 0.10:
            return Vote(True, self.weight, f"cost={entry_cost_pct:.4f}% < 0.1% — acceptable")
        elif entry_cost_pct < 0.20:
            return Vote(False, 0.5, f"cost={entry_cost_pct:.4f}% — marginal cost efficiency")
        else:
            return Vote(False, self.weight, f"cost={entry_cost_pct:.4f}% >= 0.2% — too expensive")


# ─── Agent 10: Lead-Lag Confirmation ─────────────────────────────────────────

class LeadLagAgent(MicroAgent):
    """Checks if a leader instrument confirms the signal direction.

    For LeadLag strategy: checks lead_lag_r2 strength.
    For other strategies: checks if leader_move_pct aligns with direction.
    Falls back to neutral if no lead-lag data available (most tickers).
    """
    name = "LeadLagConfirm"
    weight = 0.6

    def evaluate(self, signal, ind, msg):
        # LeadLag strategy has explicit leader data
        if signal.get("strategy") == "LeadLag":
            r2 = signal.get("lead_lag_r2", 0)
            leader_move = signal.get("leader_move_pct", 0)
            if r2 > 0.7 and abs(leader_move) > 0.1:
                return Vote(True, self.weight, f"LeadLag R²={r2:.2f}, leader_move={leader_move:.2f}% — strong confirmation")
            elif r2 > 0.5:
                return Vote(True, 0.4, f"LeadLag R²={r2:.2f} — moderate confirmation")
            else:
                return Vote(False, self.weight, f"LeadLag R²={r2:.2f} — weak leader correlation")

        # For non-LeadLag strategies, check if we have any leader data from msg
        leader_move = signal.get("leader_move_pct")
        if leader_move is None:
            # No leader data for this ticker — neutral (most common case)
            return self._neutral("no lead-lag data for this ticker")

        is_long = self._is_long(signal)
        if is_long and leader_move > 0.05:
            return Vote(True, self.weight, f"leader up {leader_move:.2f}% — confirms long")
        elif not is_long and leader_move < -0.05:
            return Vote(True, self.weight, f"leader down {leader_move:.2f}% — confirms short")
        elif abs(leader_move) < 0.05:
            return Vote(True, 0.3, f"leader flat {leader_move:.2f}% — no signal")
        else:
            return Vote(False, self.weight, f"leader {leader_move:.2f}% — opposes direction")


# ─── Swarm Consensus Aggregator ──────────────────────────────────────────────

class SwarmConsensus:
    """Evaluates a signal through all 10 micro-agents and produces consensus."""

    def __init__(self):
        self.agents = [
            TrendAlignmentAgent(),
            VolumeConfirmationAgent(),
            SpreadHealthAgent(),
            MomentumAgent(),
            VolatilityAgent(),
            HurstAgent(),
            TimeOfDayAgent(),
            RegimeAgent(),
            CostAgent(),
            LeadLagAgent(),
        ]

    def evaluate(self, signal_dict, indicators_dict, msg_dict):
        """
        Run all micro-agents and aggregate votes into a consensus score.

        Args:
            signal_dict: The candidate signal (direction, confidence, kelly_fraction, etc.)
            indicators_dict: The indicator dict from bridge.py (ind)
            msg_dict: The message dict from Rust (msg)

        Returns:
            ConsensusResult with score 0-100, adjustments, and confidence_delta.
        """
        votes = []
        adjustments = {}

        for agent in self.agents:
            try:
                vote = agent.evaluate(signal_dict, indicators_dict, msg_dict)
            except Exception as e:
                # Agent failure -> neutral vote. Never let one agent crash the swarm.
                vote = Vote(True, 0.3, f"AGENT_ERROR: {type(e).__name__}: {str(e)[:80]}")
                try:
                    sys.stderr.write(
                        f"SWARM_AGENT_ERROR: {agent.name}: {e}\n"
                    )
                    sys.stderr.flush()
                except Exception:
                    pass

            votes.append((agent.name, vote))
            adjustments[agent.name] = vote.to_dict()

        # Weighted consensus score
        total_weight = 0.0
        agree_weight = 0.0
        n_agree = 0

        for agent_name, vote in votes:
            total_weight += vote.weight
            if vote.vote:
                agree_weight += vote.weight
                n_agree += 1

        # Score: weighted agreement ratio, scaled to 0-100
        if total_weight > 0:
            score = int(round((agree_weight / total_weight) * 100))
        else:
            score = 50  # Fallback: all agents returned 0-weight → neutral

        score = max(0, min(100, score))

        # Conviction guard: if most agents returned low-weight neutrals (missing data),
        # cap the score at 50 to avoid false boosting. The maximum possible total_weight
        # with all agents fully evaluated is ~8.0. Below 4.0 means >half the agents
        # had insufficient data → we lack conviction for boost or dampen.
        if total_weight < 4.0:
            score = min(score, 50)

        result = ConsensusResult(
            score=score,
            n_agree=n_agree,
            n_total=len(votes),
            adjustments=adjustments,
            votes=votes,
        )

        # Log for visibility (stderr, same as bridge.py convention)
        try:
            ticker = signal_dict.get("ticker_id", "?")
            strategy = signal_dict.get("strategy", "?")
            sys.stderr.write(
                f"SWARM: tid={ticker} strat={strategy} score={score} "
                f"votes={n_agree}/{len(votes)} delta={result.confidence_delta}\n"
            )
            sys.stderr.flush()
        except Exception:
            pass

        return result


# ─── Singleton ───────────────────────────────────────────────────────────────

_swarm_instance = None


def get_swarm():
    """Return the singleton SwarmConsensus instance."""
    global _swarm_instance
    if _swarm_instance is None:
        _swarm_instance = SwarmConsensus()
    return _swarm_instance
