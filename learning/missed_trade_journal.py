"""
NZT-48 Missed Trade Journal
Tracks every signal that passed qualification but was blocked by:
- Position limits (max concurrent)
- Portfolio heat cap
- Session protection (daily P&L limits)
- Drawdown recovery protocol
- Overseer restrictions

Then tracks what WOULD have happened to answer:
"Are our filters helping or hurting?"
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.missed_trades")


@dataclass
class MissedTrade:
    """A signal that was qualified but not executed."""
    id: str = ""
    timestamp: str = ""
    ticker: str = ""
    direction: str = ""
    strategy: str = ""
    confidence: float = 0.0
    entry: float = 0.0
    stop: float = 0.0
    target_1r: float = 0.0
    target_2r: float = 0.0
    rejection_reason: str = ""
    # What happened after rejection
    price_at_rejection: float = 0.0
    price_1h_later: float = 0.0
    price_4h_later: float = 0.0
    price_eod: float = 0.0
    would_have_hit_target: bool = False
    would_have_hit_stop: bool = False
    hypothetical_r: float = 0.0
    outcome: str = ""  # "WINNER", "LOSER", "FLAT"


class MissedTradeJournal:
    """Records and analyses missed trades.

    Logs every rejected-but-qualified signal, then periodically
    checks what the price did afterward to evaluate filter quality.
    """

    def __init__(self) -> None:
        self._pending_followups: list[MissedTrade] = []

    def record_miss(self, signal_data: dict) -> MissedTrade:
        """Record a missed trade from a rejected signal.

        Args:
            signal_data: Dict with signal fields (from qualification pipeline).
        """
        mt = MissedTrade(
            id=signal_data.get("id", ""),
            timestamp=datetime.now(timezone.utc).isoformat(),
            ticker=signal_data.get("ticker", ""),
            direction=signal_data.get("direction", ""),
            strategy=signal_data.get("strategy", ""),
            confidence=signal_data.get("confidence", 0),
            entry=signal_data.get("entry", 0),
            stop=signal_data.get("stop", 0),
            target_1r=signal_data.get("target_1r", 0),
            target_2r=signal_data.get("target_2r", 0),
            rejection_reason=signal_data.get("rejection_reason", ""),
            price_at_rejection=signal_data.get("entry", 0),
        )
        self._pending_followups.append(mt)
        logger.info(
            "MISSED TRADE RECORDED: %s %s %s conf=%d reason=%s",
            mt.direction, mt.ticker, mt.strategy,
            mt.confidence, mt.rejection_reason,
        )
        return mt

    def update_outcomes(self, price_data: dict[str, float]) -> list[MissedTrade]:
        """Update pending missed trades with current prices.

        Called periodically (e.g., every 30 minutes) to check outcomes.
        After 4 hours, the trade is finalized.

        Args:
            price_data: Dict of ticker -> current_price.

        Returns:
            List of finalized MissedTrade objects.
        """
        finalized = []
        still_pending = []
        now = datetime.now(timezone.utc)

        for mt in self._pending_followups:
            try:
                record_time = datetime.fromisoformat(mt.timestamp)
            except (ValueError, TypeError):
                still_pending.append(mt)
                continue

            elapsed = (now - record_time).total_seconds() / 3600  # hours
            current_price = price_data.get(mt.ticker, 0)

            if current_price <= 0:
                still_pending.append(mt)
                continue

            # Update price checkpoints
            if elapsed >= 1 and mt.price_1h_later == 0:
                mt.price_1h_later = current_price
            if elapsed >= 4 and mt.price_4h_later == 0:
                mt.price_4h_later = current_price

            # Finalize after 4 hours
            if elapsed >= 4:
                mt.price_eod = current_price
                self._evaluate_outcome(mt)
                finalized.append(mt)
            else:
                still_pending.append(mt)

        self._pending_followups = still_pending
        return finalized

    def _evaluate_outcome(self, mt: MissedTrade) -> None:
        """Evaluate what would have happened if the trade was taken."""
        if mt.entry <= 0 or mt.stop <= 0 or mt.target_1r <= 0:
            mt.outcome = "UNKNOWN"
            return

        risk = abs(mt.entry - mt.stop)
        if risk <= 0:
            mt.outcome = "UNKNOWN"
            return

        # Use the best price we have
        check_price = mt.price_eod or mt.price_4h_later or mt.price_1h_later
        if check_price <= 0:
            mt.outcome = "UNKNOWN"
            return

        if mt.direction == "LONG":
            # Check if target was hit (use 4h high as proxy)
            move = check_price - mt.entry
            mt.hypothetical_r = move / risk
            mt.would_have_hit_target = check_price >= mt.target_1r
            mt.would_have_hit_stop = check_price <= mt.stop
        else:
            move = mt.entry - check_price
            mt.hypothetical_r = move / risk
            mt.would_have_hit_target = check_price <= mt.target_1r
            mt.would_have_hit_stop = check_price >= mt.stop

        if mt.hypothetical_r >= 1.0:
            mt.outcome = "WINNER"
        elif mt.hypothetical_r <= -0.5:
            mt.outcome = "LOSER"
        else:
            mt.outcome = "FLAT"

        logger.info(
            "MISSED TRADE OUTCOME: %s %s → %s (%.1fR) | Reason was: %s",
            mt.direction, mt.ticker, mt.outcome,
            mt.hypothetical_r, mt.rejection_reason,
        )

    def persist(self, conn, missed_trade: MissedTrade) -> None:
        """Save a finalized missed trade to the database."""
        try:
            conn.execute(
                """INSERT OR REPLACE INTO missed_trades
                   (id, timestamp, ticker, direction, strategy, confidence,
                    entry, stop, target_1r, rejection_reason,
                    price_at_rejection, price_1h_later, price_4h_later,
                    price_eod, would_have_hit_target, would_have_hit_stop,
                    hypothetical_r, outcome)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (missed_trade.id, missed_trade.timestamp, missed_trade.ticker,
                 missed_trade.direction, missed_trade.strategy,
                 missed_trade.confidence, missed_trade.entry, missed_trade.stop,
                 missed_trade.target_1r, missed_trade.rejection_reason,
                 missed_trade.price_at_rejection, missed_trade.price_1h_later,
                 missed_trade.price_4h_later, missed_trade.price_eod,
                 int(missed_trade.would_have_hit_target),
                 int(missed_trade.would_have_hit_stop),
                 missed_trade.hypothetical_r, missed_trade.outcome)
            )
        except Exception as e:
            logger.error("Failed to persist missed trade: %s", e)

    def get_filter_analysis(self, conn) -> dict:
        """Analyse whether filters are helping or hurting.

        Returns summary stats comparing missed trade outcomes
        to actual trade outcomes.
        """
        try:
            # Missed trade stats
            missed_total = conn.execute(
                "SELECT COUNT(*) FROM missed_trades WHERE outcome != 'UNKNOWN'"
            ).fetchone()[0]
            missed_winners = conn.execute(
                "SELECT COUNT(*) FROM missed_trades WHERE outcome = 'WINNER'"
            ).fetchone()[0]
            missed_avg_r = conn.execute(
                "SELECT AVG(hypothetical_r) FROM missed_trades WHERE outcome != 'UNKNOWN'"
            ).fetchone()[0] or 0

            # Actual trade stats
            actual_total = conn.execute(
                "SELECT COUNT(*) FROM virtual_trades"
            ).fetchone()[0]
            actual_winners = conn.execute(
                "SELECT COUNT(*) FROM virtual_trades WHERE r_multiple > 0"
            ).fetchone()[0]
            actual_avg_r = conn.execute(
                "SELECT AVG(r_multiple) FROM virtual_trades"
            ).fetchone()[0] or 0

            missed_wr = (missed_winners / missed_total * 100) if missed_total > 0 else 0
            actual_wr = (actual_winners / actual_total * 100) if actual_total > 0 else 0

            # Per-rejection-reason breakdown
            reason_stats = conn.execute(
                """SELECT rejection_reason, COUNT(*) as cnt,
                          SUM(CASE WHEN outcome='WINNER' THEN 1 ELSE 0 END) as wins,
                          AVG(hypothetical_r) as avg_r
                   FROM missed_trades
                   WHERE outcome != 'UNKNOWN'
                   GROUP BY rejection_reason
                   ORDER BY cnt DESC"""
            ).fetchall()

            return {
                "missed_total": missed_total,
                "missed_win_rate": missed_wr,
                "missed_avg_r": missed_avg_r,
                "actual_total": actual_total,
                "actual_win_rate": actual_wr,
                "actual_avg_r": actual_avg_r,
                "filters_helping": actual_wr > missed_wr,
                "edge_from_filters": actual_avg_r - missed_avg_r,
                "per_reason": [
                    {
                        "reason": row[0],
                        "count": row[1],
                        "win_rate": (row[2] / row[1] * 100) if row[1] > 0 else 0,
                        "avg_r": row[3],
                    }
                    for row in reason_stats
                ],
            }
        except Exception as e:
            logger.error("Filter analysis failed: %s", e)
            return {"error": str(e)}

    def to_telegram(self, analysis: dict) -> str:
        """Format filter analysis for Telegram."""
        if "error" in analysis:
            return f"FILTER ANALYSIS ERROR: {analysis['error']}"

        lines = [
            "FILTER ANALYSIS",
            "=" * 30,
            f"Taken Trades: {analysis['actual_total']} | WR: {analysis['actual_win_rate']:.0f}% | Avg R: {analysis['actual_avg_r']:.2f}",
            f"Missed Trades: {analysis['missed_total']} | WR: {analysis['missed_win_rate']:.0f}% | Avg R: {analysis['missed_avg_r']:.2f}",
            "",
        ]

        if analysis.get("filters_helping"):
            lines.append(f"FILTERS ARE HELPING: +{analysis['edge_from_filters']:.2f}R edge")
        else:
            lines.append(f"WARNING: Filters may be HURTING: {analysis['edge_from_filters']:.2f}R difference")

        if analysis.get("per_reason"):
            lines.append("")
            lines.append("By Rejection Reason:")
            for r in analysis["per_reason"][:5]:
                lines.append(f"  {r['reason']}: {r['count']} missed, {r['win_rate']:.0f}% WR, {r['avg_r']:.2f}R")

        return "\n".join(lines)
