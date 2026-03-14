"""
NZT-48 B-Team Manager — Promotion/Relegation System
====================================================
Dynamic "farm team" of 20 tickers that compete for promotion to the Core group
based on profitability. Best performers get promoted, worst get relegated.

LEAGUE STRUCTURE:
  - A-Team (Core): 12 ISA tickers — target 2%+ with ratchet ladder
  - B-Team (Challengers): 20 tickers from extended/sector universe — target 2% flat
  - C-Team (Prospects): All other LSE leveraged products — scan-only, no trades

PROMOTION RULES (B→A):
  - 5+ profitable trades in 10 trading days
  - avg R > 1.0
  - win rate > 55%

RELEGATION RULES (A→B):
  - 3 consecutive losing trades OR
  - win rate < 40% over 10 trades

B-TEAM ENTRY:
  - Any C-Team ticker: 3+ days of ATR% > 1.5% + RVOL > 0.5 + clean data

B-TEAM EXIT:
  - 5 consecutive zero-volume days OR spread > 100 bps consistently

EVALUATION CYCLE: Every 3 trading days at 16:00 UK (post-LSE close)

Research basis:
  - Jegadeesh & Titman (1993): Momentum factor rotation
  - Quantpedia sector momentum: Rotational systems outperform static allocation
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nzt48.b_team")


@dataclass
class TickerStats:
    """Performance statistics for a ticker in the league system."""
    ticker: str = ""
    team: str = "C"  # A, B, or C
    trades_taken: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    avg_r: float = 0.0
    best_strategy: str = ""
    avg_hold_minutes: float = 0.0
    best_time_of_day: str = ""
    consecutive_losses: int = 0
    consecutive_zero_volume_days: int = 0
    avg_spread_bps: float = 0.0
    last_trade_date: str = ""
    promotion_date: str = ""
    relegation_date: str = ""
    last_evaluated: str = ""

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return (self.wins / total * 100) if total > 0 else 0.0


@dataclass
class LeagueEvent:
    """A promotion/relegation event for audit trail."""
    timestamp: str = ""
    ticker: str = ""
    from_team: str = ""
    to_team: str = ""
    reason: str = ""
    stats_at_move: dict = field(default_factory=dict)


class BTeamManager:
    """Manages the A-Team / B-Team / C-Team promotion/relegation system.

    Tracks per-ticker stats, evaluates every 3 trading days, and
    moves tickers between teams based on profitability.
    """

    # Default A-Team (Core ISA tickers)
    DEFAULT_A_TEAM = [
        "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L", "TSL3.L",
        "TSM3.L", "MU2.L", "QQQS.L", "3USS.L", "QQQ5.L", "SP5L.L",
    ]

    # Default B-Team (Extended + Sector)
    DEFAULT_B_TEAM = [
        "AMD3.L", "ARM3.L", "NVDS.L", "TSLS.L", "3LDE.L", "3LEU.L",
        "3GOL.L", "3SIL.L", "3OIL.L", "LLY3.L",
        "3LHC.L", "BAC3.L", "GS3.L", "3LEN.L", "XOM3.L",
        "COIN3.L", "MSTRL.L", "PLTR3.L", "AVGO3.L", "MFAS.L",
    ]

    # Promotion thresholds — B→A requires CONSISTENT long-term profitability
    # A B-Team ticker must PROVE itself over many trades before promotion
    PROMO_MIN_TRADES = 10           # Must have 10+ completed trades (not 5)
    PROMO_MIN_PROFITABLE = 7        # At least 7 of those must be profitable
    PROMO_TRADE_WINDOW = 20         # Over 20 trading days (long observation period)
    PROMO_MIN_AVG_R = 0.8           # Average R > 0.8 (slightly relaxed but sustained)
    PROMO_MIN_WIN_RATE = 55.0       # 55%+ win rate
    PROMO_MIN_TOTAL_PNL = 0.0       # Must be net profitable overall
    PROMO_CONSISTENCY_CHECK = True   # No 3+ consecutive losses in evaluation window

    # Relegation thresholds — A→B only when CONSISTENTLY unprofitable
    # Core tickers get more rope — only relegated when clearly failing
    RELEGATE_CONSECUTIVE_LOSSES = 5  # 5 straight losses (was 3 — more forgiving for core)
    RELEGATE_MIN_WIN_RATE = 35.0     # Below 35% over sample (was 40% — more patient)
    RELEGATE_TRADE_WINDOW = 15       # Need 15+ trades sample (statistically meaningful)
    RELEGATE_MIN_NEGATIVE_PNL = -50  # Must be £50+ in the red overall to be relegated

    # B-Team entry thresholds
    ENTRY_ATR_PCT_MIN = 1.5
    ENTRY_ATR_DAYS = 3
    ENTRY_RVOL_MIN = 0.5

    # B-Team exit thresholds
    EXIT_ZERO_VOLUME_DAYS = 5
    EXIT_SPREAD_BPS_MAX = 100

    # Evaluation frequency
    EVAL_EVERY_N_DAYS = 3

    def __init__(self, state_path: Optional[str] = None) -> None:
        self._state_path = state_path or "data/b_team_state.json"
        self._ticker_stats: dict[str, TickerStats] = {}
        self._league_events: list[LeagueEvent] = []
        self._trading_days_since_eval: int = 0
        self._initialized = False

    def initialize(self) -> None:
        """Load state or create default."""
        try:
            path = Path(self._state_path)
            if path.exists():
                data = json.loads(path.read_text())
                for ticker, stats_dict in data.get("ticker_stats", {}).items():
                    self._ticker_stats[ticker] = TickerStats(**stats_dict)
                for evt_dict in data.get("league_events", []):
                    self._league_events.append(LeagueEvent(**evt_dict))
                self._trading_days_since_eval = data.get("days_since_eval", 0)
                logger.info("B-Team state loaded: %d tickers tracked", len(self._ticker_stats))
            else:
                self._init_defaults()
            self._initialized = True
        except Exception as e:
            logger.error("Failed to load B-Team state: %s", e)
            self._init_defaults()
            self._initialized = True

    def _init_defaults(self) -> None:
        """Create default league structure."""
        for ticker in self.DEFAULT_A_TEAM:
            self._ticker_stats[ticker] = TickerStats(ticker=ticker, team="A")
        for ticker in self.DEFAULT_B_TEAM:
            self._ticker_stats[ticker] = TickerStats(ticker=ticker, team="B")
        logger.info(
            "B-Team defaults initialized: %d A-Team, %d B-Team",
            len(self.DEFAULT_A_TEAM), len(self.DEFAULT_B_TEAM),
        )

    def save_state(self) -> None:
        """Persist state to disk."""
        try:
            path = Path(self._state_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "ticker_stats": {t: asdict(s) for t, s in self._ticker_stats.items()},
                "league_events": [asdict(e) for e in self._league_events[-100:]],  # Keep last 100
                "days_since_eval": self._trading_days_since_eval,
            }
            path.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error("Failed to save B-Team state: %s", e)

    # -----------------------------------------------------------------------
    # Trade Recording
    # -----------------------------------------------------------------------

    def record_trade(self, ticker: str, pnl: float, r_multiple: float,
                     strategy: str, hold_minutes: float = 0) -> None:
        """Record a completed trade for league evaluation."""
        if ticker not in self._ticker_stats:
            self._ticker_stats[ticker] = TickerStats(ticker=ticker, team="C")

        stats = self._ticker_stats[ticker]
        stats.trades_taken += 1
        stats.total_pnl += pnl

        if pnl > 0:
            stats.wins += 1
            stats.consecutive_losses = 0
        elif pnl < 0:
            stats.losses += 1
            stats.consecutive_losses += 1

        # Rolling average R
        total = stats.wins + stats.losses
        if total > 0:
            stats.avg_r = ((stats.avg_r * (total - 1)) + r_multiple) / total

        # Best strategy tracking
        if not stats.best_strategy or r_multiple > stats.avg_r:
            stats.best_strategy = strategy

        # Holding duration
        if hold_minutes > 0:
            if stats.avg_hold_minutes == 0:
                stats.avg_hold_minutes = hold_minutes
            else:
                stats.avg_hold_minutes = (stats.avg_hold_minutes * 0.8) + (hold_minutes * 0.2)

        stats.last_trade_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        logger.debug(
            "B-Team trade recorded: %s team=%s P&L=%.2f R=%.2f WR=%.1f%% trades=%d",
            ticker, stats.team, pnl, r_multiple, stats.win_rate, stats.trades_taken,
        )

    # -----------------------------------------------------------------------
    # Evaluation Cycle
    # -----------------------------------------------------------------------

    def evaluate(self) -> list[LeagueEvent]:
        """Run promotion/relegation evaluation. Returns list of events."""
        self._trading_days_since_eval += 1
        if self._trading_days_since_eval < self.EVAL_EVERY_N_DAYS:
            return []

        self._trading_days_since_eval = 0
        events: list[LeagueEvent] = []
        now = datetime.now(timezone.utc).isoformat()

        # --- RELEGATION BATTLE: Check A→B relegations FIRST ---
        # Core tickers only get relegated when CONSISTENTLY unprofitable.
        # This is a battle — the ticker must be clearly failing over many trades.
        relegated_tickers = []
        for ticker, stats in list(self._ticker_stats.items()):
            if stats.team != "A":
                continue
            stats.last_evaluated = now

            # Need minimum sample size for relegation (statistically meaningful)
            if stats.trades_taken < self.RELEGATE_TRADE_WINDOW:
                continue

            # Consecutive losses trigger (5 straight = clear failure)
            if stats.consecutive_losses >= self.RELEGATE_CONSECUTIVE_LOSSES:
                event = self._relegate(
                    ticker, stats, now,
                    f"{stats.consecutive_losses} consecutive losses — clear underperformance",
                )
                events.append(event)
                relegated_tickers.append(ticker)
                continue

            # Win rate + P&L trigger (must be BOTH losing AND below threshold)
            if (
                stats.win_rate < self.RELEGATE_MIN_WIN_RATE
                and stats.total_pnl < self.RELEGATE_MIN_NEGATIVE_PNL
            ):
                event = self._relegate(
                    ticker, stats, now,
                    f"WR {stats.win_rate:.1f}% < {self.RELEGATE_MIN_WIN_RATE}% AND total P&L £{stats.total_pnl:.0f} — sustained underperformance",
                )
                events.append(event)
                relegated_tickers.append(ticker)

        # --- PROMOTION BATTLE: Check B→A promotions ---
        # B-Team tickers must PROVE themselves over a LONG period before promotion.
        # Promotion battle is HARDER when no core spots are open from relegation.
        for ticker, stats in list(self._ticker_stats.items()):
            if stats.team != "B":
                continue
            stats.last_evaluated = now

            # Must have enough trades for statistical significance
            if stats.trades_taken < self.PROMO_MIN_TRADES:
                continue

            # Must have enough WINNING trades (not just total trades)
            if stats.wins < self.PROMO_MIN_PROFITABLE:
                continue

            # Must meet ALL criteria — consistent long-term profitability
            if (
                stats.avg_r >= self.PROMO_MIN_AVG_R
                and stats.win_rate >= self.PROMO_MIN_WIN_RATE
                and stats.total_pnl >= self.PROMO_MIN_TOTAL_PNL
            ):
                # Consistency check: no recent streak of losses
                if self.PROMO_CONSISTENCY_CHECK and stats.consecutive_losses >= 3:
                    logger.info(
                        "B-Team %s meets promotion criteria but has %d recent consecutive losses — holding",
                        ticker, stats.consecutive_losses,
                    )
                    continue

                event = self._promote(ticker, stats, now)
                events.append(event)

        # --- Check B-Team exits (zero volume / illiquid) ---
        for ticker, stats in list(self._ticker_stats.items()):
            if stats.team != "B":
                continue
            if stats.consecutive_zero_volume_days >= self.EXIT_ZERO_VOLUME_DAYS:
                event = LeagueEvent(
                    timestamp=now, ticker=ticker, from_team="B", to_team="C",
                    reason=f"{stats.consecutive_zero_volume_days} zero-volume days",
                    stats_at_move=asdict(stats),
                )
                stats.team = "C"
                self._league_events.append(event)
                events.append(event)
                logger.info("B-TEAM EXIT: %s → C-Team (%s)", ticker, event.reason)

        if events:
            self.save_state()
            logger.info("League evaluation complete: %d moves", len(events))

        return events

    def _promote(self, ticker: str, stats: TickerStats, now: str) -> LeagueEvent:
        """Promote ticker from B→A."""
        event = LeagueEvent(
            timestamp=now, ticker=ticker, from_team="B", to_team="A",
            reason=f"Promoted: {stats.trades_taken} trades, WR={stats.win_rate:.1f}%, avg R={stats.avg_r:.2f}",
            stats_at_move=asdict(stats),
        )
        stats.team = "A"
        stats.promotion_date = now
        self._league_events.append(event)
        logger.info("🟢 PROMOTION: %s → A-Team! WR=%.1f%% R=%.2f", ticker, stats.win_rate, stats.avg_r)
        return event

    def _relegate(self, ticker: str, stats: TickerStats, now: str, reason: str) -> LeagueEvent:
        """Relegate ticker from A→B."""
        event = LeagueEvent(
            timestamp=now, ticker=ticker, from_team="A", to_team="B",
            reason=f"Relegated: {reason}",
            stats_at_move=asdict(stats),
        )
        stats.team = "B"
        stats.relegation_date = now
        stats.consecutive_losses = 0  # Reset after relegation
        self._league_events.append(event)
        logger.info("🔴 RELEGATION: %s → B-Team! %s", ticker, reason)
        return event

    # -----------------------------------------------------------------------
    # Market Data Updates
    # -----------------------------------------------------------------------

    def update_market_data(self, ticker: str, volume: float = 0, spread_bps: float = 0,
                           atr_pct: float = 0, rvol: float = 0) -> None:
        """Update market data for C-Team entry evaluation."""
        if ticker not in self._ticker_stats:
            self._ticker_stats[ticker] = TickerStats(ticker=ticker, team="C")

        stats = self._ticker_stats[ticker]

        if volume == 0:
            stats.consecutive_zero_volume_days += 1
        else:
            stats.consecutive_zero_volume_days = 0

        stats.avg_spread_bps = (stats.avg_spread_bps * 0.8) + (spread_bps * 0.2) if stats.avg_spread_bps else spread_bps

    # -----------------------------------------------------------------------
    # Query Methods
    # -----------------------------------------------------------------------

    def score_play(self, ticker: str, signal_confidence: int,
                   atr_pct: float = 0, rvol: float = 0,
                   regime: str = "", direction: str = "",
                   learning_adj: dict = None) -> dict:
        """Score a play with percentage brackets and detailed reasons.

        Returns a dict with:
          - score: 0-100 composite score
          - bracket: percentage bracket (e.g., "75-85%")
          - grade: letter grade (A+, A, B, C, D, F)
          - reasons: list of score components with explanations
          - self_learning_boost: adjustment from learning system

        Uses the self-learning system (ticker profiles, meta weights,
        historical performance) to refine scores.
        """
        stats = self._ticker_stats.get(ticker)
        reasons = []
        score = 0.0

        # 1. BASE SIGNAL CONFIDENCE (40% of total — from strategy scanner)
        base_score = signal_confidence * 0.4
        score += base_score
        reasons.append({
            "factor": "Signal Confidence",
            "value": signal_confidence,
            "weight": "40%",
            "contribution": round(base_score, 1),
            "reason": f"Strategy scanner rated this setup at {signal_confidence}/100",
        })

        # 2. SELF-LEARNING TICKER PROFILE (25% of total)
        learning_score = 0.0
        if stats and stats.trades_taken >= 3:
            # Historical win rate for this ticker
            wr_bonus = min(25, stats.win_rate / 4)  # Max 25 from 100% WR
            learning_score = wr_bonus
            reasons.append({
                "factor": "Self-Learning (Ticker History)",
                "value": round(stats.win_rate, 1),
                "weight": "25%",
                "contribution": round(learning_score, 1),
                "reason": f"Historical WR={stats.win_rate:.1f}% over {stats.trades_taken} trades, avg R={stats.avg_r:.2f}",
            })
        elif learning_adj:
            # Use learning adjustments if no direct history
            adj_score = learning_adj.get("confidence_adj", 0) or 0
            learning_score = min(25, max(0, adj_score * 0.25))
            reasons.append({
                "factor": "Self-Learning (Model Adjusted)",
                "value": adj_score,
                "weight": "25%",
                "contribution": round(learning_score, 1),
                "reason": f"Learning model confidence adjustment: {adj_score:+.0f}",
            })
        else:
            learning_score = 12.5  # Neutral default for unknown tickers
            reasons.append({
                "factor": "Self-Learning (No History)",
                "value": 0,
                "weight": "25%",
                "contribution": 12.5,
                "reason": "No historical data — neutral score applied",
            })
        score += learning_score

        # 3. VOLATILITY REACHABILITY (15% of total — can the stock move 2%?)
        vol_score = 0.0
        if atr_pct >= 3.0:
            vol_score = 15
        elif atr_pct >= 2.0:
            vol_score = 12
        elif atr_pct >= 1.5:
            vol_score = 9
        elif atr_pct >= 1.0:
            vol_score = 5
        else:
            vol_score = 2
        score += vol_score
        reasons.append({
            "factor": "Volatility Reachability",
            "value": round(atr_pct, 2),
            "weight": "15%",
            "contribution": round(vol_score, 1),
            "reason": f"ATR%={atr_pct:.2f}% — {'easily' if atr_pct >= 2 else 'possibly' if atr_pct >= 1.5 else 'unlikely to'} reach 2% target",
        })

        # 4. REGIME ALIGNMENT (10% of total)
        regime_score = 0.0
        if direction == "LONG" and "TRENDING_UP" in regime:
            regime_score = 10
        elif direction == "SHORT" and "TRENDING_DOWN" in regime:
            regime_score = 10
        elif "RANGE" in regime:
            regime_score = 5
        elif direction == "LONG" and "DOWN" in regime:
            regime_score = 0
        elif direction == "SHORT" and "UP" in regime:
            regime_score = 0
        else:
            regime_score = 5
        score += regime_score
        reasons.append({
            "factor": "Regime Alignment",
            "value": regime,
            "weight": "10%",
            "contribution": round(regime_score, 1),
            "reason": f"{direction} in {regime} regime — {'strongly aligned' if regime_score >= 8 else 'neutral' if regime_score >= 4 else 'misaligned'}",
        })

        # 5. RVOL (RELATIVE VOLUME) (10% of total)
        rvol_score = 0.0
        if rvol >= 3.0:
            rvol_score = 10
        elif rvol >= 2.0:
            rvol_score = 8
        elif rvol >= 1.5:
            rvol_score = 6
        elif rvol >= 1.0:
            rvol_score = 4
        elif rvol >= 0.5:
            rvol_score = 2
        score += rvol_score
        reasons.append({
            "factor": "Relative Volume",
            "value": round(rvol, 2),
            "weight": "10%",
            "contribution": round(rvol_score, 1),
            "reason": f"RVOL={rvol:.1f}x — {'high conviction' if rvol >= 2 else 'above average' if rvol >= 1 else 'below average'}",
        })

        # Clamp final score
        final_score = max(0, min(100, round(score)))

        # Determine bracket and grade
        bracket = self._score_to_bracket(final_score)
        grade = self._score_to_grade(final_score)

        return {
            "score": final_score,
            "bracket": bracket,
            "grade": grade,
            "reasons": reasons,
            "self_learning_boost": round(learning_score - 12.5, 1),  # vs neutral
            "ticker": ticker,
            "direction": direction,
        }

    @staticmethod
    def _score_to_bracket(score: int) -> str:
        """Convert score to percentage bracket."""
        if score >= 90: return "90-100% (Elite)"
        if score >= 80: return "80-90% (Strong)"
        if score >= 70: return "70-80% (Good)"
        if score >= 60: return "60-70% (Acceptable)"
        if score >= 50: return "50-60% (Marginal)"
        if score >= 40: return "40-50% (Weak)"
        return "0-40% (Poor)"

    @staticmethod
    def _score_to_grade(score: int) -> str:
        """Convert score to letter grade."""
        if score >= 95: return "A+"
        if score >= 90: return "A"
        if score >= 85: return "A-"
        if score >= 80: return "B+"
        if score >= 75: return "B"
        if score >= 70: return "B-"
        if score >= 65: return "C+"
        if score >= 60: return "C"
        if score >= 55: return "C-"
        if score >= 50: return "D+"
        if score >= 45: return "D"
        return "F"

    def get_a_team(self) -> list[str]:
        """Return current A-Team tickers."""
        return [t for t, s in self._ticker_stats.items() if s.team == "A"]

    def get_b_team(self) -> list[str]:
        """Return current B-Team tickers."""
        return [t for t, s in self._ticker_stats.items() if s.team == "B"]

    def get_c_team(self) -> list[str]:
        """Return current C-Team tickers."""
        return [t for t, s in self._ticker_stats.items() if s.team == "C"]

    def get_league_table(self) -> list[dict]:
        """Return full league table sorted by team then P&L."""
        team_order = {"A": 0, "B": 1, "C": 2}
        items = sorted(
            self._ticker_stats.values(),
            key=lambda s: (team_order.get(s.team, 3), -s.total_pnl),
        )
        return [asdict(s) for s in items]

    def get_recent_events(self, limit: int = 20) -> list[dict]:
        """Return recent league events."""
        return [asdict(e) for e in self._league_events[-limit:]]

    def get_ticker_team(self, ticker: str) -> str:
        """Return which team a ticker is on."""
        stats = self._ticker_stats.get(ticker)
        return stats.team if stats else "C"

    def get_stats(self, ticker: str) -> Optional[dict]:
        """Return stats for a specific ticker."""
        stats = self._ticker_stats.get(ticker)
        return asdict(stats) if stats else None
