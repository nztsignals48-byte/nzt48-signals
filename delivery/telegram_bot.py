"""
NZT-48 Trading System — Telegram Delivery Bot
Section 54: Signal delivery via python-telegram-bot.
All signals pushed to Telegram with full context.

Commands (Section 54):
  /taken    — Log signal as EXECUTED, start tracking
  /skipped  — Log as SKIPPED, track skip rate
  /positions — All open positions with live P&L
  /close [ticker] — Mark closed, log P&L, MAE, MFE
  /stats    — Strategy scorecard
  /today    — Context: GEX, DIX, calendar, positions
  /pause [strat] — Temporarily disable strategy
  /kill [strat]  — Permanently disable strategy
  /bots     — Show active bot instances, capital allocation, regime state
  /overseer — Portfolio Overseer status: aggregate exposure, restrictions

Kill Switch: 3 methods — /kill command, config file flag, process signal.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable, Any
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.telegram")


def _escape_html(text: str) -> str:
    """Escape special characters for Telegram HTML parse mode."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# Signal format emojis based on confidence level
CONFIDENCE_EMOJI = {
    "high": "\U0001F7E2",      # Green circle (conf 80+)
    "qualified": "\U0001F7E2",  # Green circle (conf 60-79)
    "borderline": "\U0001F7E0",  # Orange circle (conf 55-59)
    "rejected": "\U0001F534",   # Red circle (rejected)
}

DIRECTION_EMOJI = {
    "LONG": "\U0001F7E2",   # Green
    "SHORT": "\U0001F534",  # Red
}


class TelegramDedupe:
    """Prevent duplicate messages — persisted to SQLite for restart survival."""

    def __init__(self, window_seconds: int = 300, db_path: str = "data/telegram_state.db"):
        self._window = window_seconds
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        try:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            conn = sqlite3.connect(self._db_path)
            conn.execute("""CREATE TABLE IF NOT EXISTS dedupe_hashes (
                content_hash TEXT PRIMARY KEY,
                sent_at REAL NOT NULL
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS telegram_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            )""")
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("TelegramDedupe DB init failed: %s", e)

    def should_send(self, content_hash: str) -> bool:
        """Return False if same hash sent within window."""
        import time
        now = time.time()
        with self._lock:
            try:
                conn = sqlite3.connect(self._db_path)
                # Clean expired
                conn.execute("DELETE FROM dedupe_hashes WHERE sent_at < ?", (now - self._window,))
                # Check
                row = conn.execute("SELECT 1 FROM dedupe_hashes WHERE content_hash = ?", (content_hash,)).fetchone()
                if row:
                    conn.close()
                    return False
                conn.execute("INSERT OR REPLACE INTO dedupe_hashes VALUES (?, ?)", (content_hash, now))
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                logger.warning("TelegramDedupe check failed: %s", e)
                return True  # Fail-open for dedupe (better to send than miss)


class TelegramPersistentState:
    """Persist kill switch and pause state across restarts."""

    def __init__(self, db_path: str = "data/telegram_state.db"):
        self._db_path = db_path
        self._lock = threading.Lock()

    def save_state(self, key: str, value: str):
        import time
        with self._lock:
            try:
                conn = sqlite3.connect(self._db_path)
                conn.execute("INSERT OR REPLACE INTO telegram_state VALUES (?, ?, ?)",
                           (key, value, time.time()))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning("TelegramPersistentState save failed: %s", e)

    def load_state(self, key: str, default: str = "") -> str:
        with self._lock:
            try:
                conn = sqlite3.connect(self._db_path)
                row = conn.execute("SELECT value FROM telegram_state WHERE key = ?", (key,)).fetchone()
                conn.close()
                return row[0] if row else default
            except Exception as e:
                logger.warning("TelegramPersistentState load failed: %s", e)
                return default

    def save_paused_strategies(self, strategies: set):
        self.save_state("paused_strategies", json.dumps(list(strategies)))

    def load_paused_strategies(self) -> set:
        raw = self.load_state("paused_strategies", "[]")
        try:
            return set(json.loads(raw))
        except Exception:
            return set()

    def save_killed_strategies(self, strategies: set):
        self.save_state("killed_strategies", json.dumps(list(strategies)))

    def load_killed_strategies(self) -> set:
        raw = self.load_state("killed_strategies", "[]")
        try:
            return set(json.loads(raw))
        except Exception:
            return set()

    def save_quiet_until(self, timestamp: float):
        self.save_state("quiet_until", str(timestamp))

    def load_quiet_until(self) -> float:
        raw = self.load_state("quiet_until", "0")
        try:
            return float(raw)
        except Exception:
            return 0.0


class TelegramRateLimiter:
    """Rate limiting for Telegram messages."""

    MAX_PER_MINUTE = 5
    MAX_PER_HOUR = 30
    SPAM_KILL_THRESHOLD = 10  # per minute -> auto-pause 15min

    def __init__(self):
        self._timestamps: list = []
        self._paused_until: float = 0
        self._lock = threading.Lock()

    def can_send(self) -> tuple:  # (bool, reason)
        """Check if sending is allowed."""
        import time
        now = time.time()
        with self._lock:
            if now < self._paused_until:
                return False, f"SPAM_PAUSED until {self._paused_until}"
            # Clean old timestamps
            self._timestamps = [t for t in self._timestamps if now - t < 3600]
            per_minute = sum(1 for t in self._timestamps if now - t < 60)
            per_hour = len(self._timestamps)
            if per_minute >= self.SPAM_KILL_THRESHOLD:
                self._paused_until = now + 900  # 15 min pause
                return False, "SPAM_KILL activated"
            if per_minute >= self.MAX_PER_MINUTE:
                return False, "RATE_LIMITED (per minute)"
            if per_hour >= self.MAX_PER_HOUR:
                return False, "RATE_LIMITED (per hour)"
            return True, None

    def record_send(self):
        import time
        with self._lock:
            self._timestamps.append(time.time())


class TelegramDebugLogger:
    """Log all Telegram events to telegram_debug.jsonl."""

    def __init__(self, path: str = "data/telegram_debug.jsonl"):
        self._path = path

    def log(self, action: str, label: str = "", ticker: str = "", content_hash: str = "", reason: str = ""):
        import json, time
        from datetime import datetime, timezone
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "label": label,
            "ticker": ticker,
            "content_hash": content_hash,
            "reason": reason
        }
        try:
            with open(self._path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass


# Telegram message labels
LABEL_TRADE = "\U0001F7E2 TRADE"
LABEL_WATCH = "\U0001F440 WATCH"
LABEL_INTEL = "\U0001F4CA INTEL"
LABEL_HEALTH = "\U0001F3E5 HEALTH"
LABEL_OPPORTUNITY = "\U0001F4C8 OPPORTUNITY"
LABEL_EXIT = "\U0001F6AA EXIT"


def validate_telegram_signal(play: dict) -> bool:
    """Hard gate: no messages with score<10, score=None, or empty fields."""
    score = play.get("composite_score") or play.get("score") or play.get("confidence")
    if score is None or (isinstance(score, (int, float)) and score < 10):
        return False
    if not play.get("ticker") or not play.get("direction"):
        return False
    return True


# Strategy name mapping (used in signal formatting and trade close messages)
STRATEGY_NAMES = {
    "S1": "Regime Trend", "S2": "Momentum Breakout",
    "S3": "Mean Reversion", "S4": "Catalyst/Narrative",
    "S5": "PEAD Earnings", "S6": "Macro Regime",
    "S7": "Sector Rotation", "S8": "Vol Crush",
    "S9": "Pairs Trade", "S10": "AI Thematic",
    "S11": "Hot Scanner", "S12": "Rebalance Flow",
    "S13": "Trend Compound", "S14": "Gamma Squeeze",
}


def format_signal_message(signal) -> str:
    """Format a Signal object into the Telegram message format from Section 54.

    Example output:
    🟢 LONG NVDA | S2 Momentum Breakout | Conf: 78/100
    Entry: $142.50 | Stop: $140.80 (-1.2%) | Risk: $170 (0.75%)
    T1: $144.20 (+1.2%) | T2: $145.90 (+2.4%) | Trail: $143.80
    Regime: TRENDING_UP | GEX: Negative | RVOL: 2.1
    ISA Map: NVD3.L (3x NVIDIA) | Bot: B (IBKR)
    Overseer: CLEAR | Portfolio heat: 1.8%/3.0%
    """
    # Direction emoji
    emoji = DIRECTION_EMOJI.get(signal.direction.value, "\u26AA")
    if signal.confidence >= 80:
        emoji = "\U0001F7E2"  # Bright green for high conf

    # Stop percentage
    stop_pct = ""
    if signal.entry > 0 and signal.stop > 0:
        pct = ((signal.stop - signal.entry) / signal.entry) * 100
        stop_pct = f" ({pct:+.1f}%)"

    # Target percentages
    t1_pct = ""
    if signal.entry > 0 and signal.target_1r > 0:
        pct = ((signal.target_1r - signal.entry) / signal.entry) * 100
        t1_pct = f" ({pct:+.1f}%)"

    t2_pct = ""
    if signal.entry > 0 and signal.target_2r > 0:
        pct = ((signal.target_2r - signal.entry) / signal.entry) * 100
        t2_pct = f" ({pct:+.1f}%)"

    strat_name = STRATEGY_NAMES.get(signal.strategy, signal.strategy)

    # Build message
    # BUY/SELL label based on direction
    action_label = "BUY" if signal.direction.value == "LONG" else "SELL"

    lines = [
        f"{emoji} {action_label} {signal.ticker} | "
        f"{signal.strategy} {strat_name} | Conf: {signal.confidence:.0f}/100",

        f"Entry: ${signal.entry:.2f} | Stop: ${signal.stop:.2f}{stop_pct} | "
        f"Risk: ${signal.risk_dollars:.0f} ({signal.risk_pct*100:.2f}%)",

        f"T1: ${signal.target_1r:.2f}{t1_pct} | T2: ${signal.target_2r:.2f}{t2_pct} | "
        f"Trail: ${signal.trail:.2f}",

        f"Regime: {signal.regime.value} | GEX: {signal.gex_regime.value} | "
        f"RVOL: {signal.rvol:.1f}",
    ]

    # ISA mapping line
    if signal.isa_ticker and signal.isa_ticker != "SB_ONLY":
        lines.append(
            f"ISA Map: {signal.isa_ticker} ({signal.isa_leverage} {signal.isa_underlying}) | "
            f"Bot: {signal.bot.value}"
        )
    else:
        lines.append(f"Bot: {signal.bot.value} (no ISA equivalent)")

    # Overseer line
    lines.append(
        f"Overseer: {signal.overseer_status} | "
        f"Portfolio heat: {signal.portfolio_heat:.1f}%/3.0%"
    )

    # Patterns
    if signal.patterns_detected:
        lines.append(f"Patterns: {', '.join(signal.patterns_detected)}")

    # Timeframe layer
    if signal.timeframe_layer:
        lines.append(f"Layer: {signal.timeframe_layer}")

    return "\n".join(lines)


def format_position_message(positions: list) -> str:
    """Format open positions for /positions command (HTML parse mode)."""
    if not positions:
        return "📊 No open positions."

    lines = ["📊 <b>OPEN POSITIONS</b>\n"]
    total_pnl = 0.0

    for pos in positions:
        emoji = "\U0001F7E2" if pos.unrealised_pnl >= 0 else "\U0001F534"
        r_str = f"{pos.unrealised_r:+.1f}R" if hasattr(pos, 'unrealised_r') else ""
        pnl = pos.unrealised_pnl if hasattr(pos, 'unrealised_pnl') else 0
        total_pnl += pnl
        ticker = _escape_html(str(pos.ticker))
        bot_inst = _escape_html(str(pos.bot_instance))

        lines.append(
            f"{emoji} {pos.direction} {ticker} | "
            f"Entry: ${pos.entry:.2f} | Now: ${pos.current_price:.2f} | "
            f"P&amp;L: ${pnl:+.2f} ({r_str}) | "
            f"Rung: {pos.ladder_rung} | Stop: ${pos.current_stop:.2f} | "
            f"Bot: {bot_inst}"
        )

    lines.append(f"\n<b>Total Unrealised: ${total_pnl:+.2f}</b>")
    return "\n".join(lines)


def format_stats_message(stats: dict) -> str:
    """Format strategy scorecard for /stats command (HTML parse mode)."""
    lines = ["📈 <b>STRATEGY SCORECARD</b>\n"]

    for strategy, data in stats.items():
        wr = data.get("win_rate", 0) * 100
        pf = data.get("profit_factor", 0)
        avg_r = data.get("avg_r", 0)
        trades = data.get("trades", 0)
        emoji = "\U0001F7E2" if wr > 55 else "\U0001F7E0" if wr > 45 else "\U0001F534"
        strat_name = _escape_html(str(strategy))

        lines.append(
            f"{emoji} {strat_name}: WR={wr:.0f}% | PF={pf:.1f} | "
            f"AvgR={avg_r:+.1f} | Trades={trades}"
        )

    return "\n".join(lines)


def format_today_message(context: dict) -> str:
    """Format /today context: GEX, DIX, calendar, positions (HTML parse mode)."""
    lines = ["🌍 <b>TODAY'S CONTEXT</b>\n"]

    lines.append(f"Regime: {context.get('regime', 'N/A')}")
    lines.append(f"VIX: {context.get('vix', 0):.1f} | "
                 f"GEX: {context.get('gex_regime', 'N/A')} | "
                 f"DIX: {context.get('dix', 0):.3f}")
    lines.append(f"Internals: {context.get('internals_composite', 0)}/4")
    lines.append(f"Calendar: {context.get('calendar_risk', 'CLEAR')}")

    if context.get('earnings_tonight'):
        lines.append(f"Earnings tonight: {', '.join(context['earnings_tonight'])}")

    lines.append(f"\nPositions open: {context.get('position_count', 0)}")
    lines.append(f"Daily P&L: {context.get('daily_pnl_pct', 0):+.2f}%")
    lines.append(f"Weekly P&L: {context.get('weekly_pnl_pct', 0):+.2f}%")

    return "\n".join(lines)


def format_bots_message(bots_info: dict) -> str:
    """Format /bots command: active bot instances, capital, regime (HTML parse mode)."""
    lines = ["🤖 <b>BOT STATUS</b>\n"]

    for bot_name, info in bots_info.items():
        status = "🟢 ACTIVE" if info.get("active") else "😴 SLEEPING"
        name_escaped = _escape_html(str(bot_name))
        lines.append(
            f"{status} <b>{name_escaped}</b>\n"
            f"  Capital: {info.get('capital_pct', 0):.0f}% | "
            f"Positions: {info.get('positions', 0)}/{info.get('max_positions', 0)} | "
            f"P&amp;L: {info.get('session_pnl', 0):+.2f}%\n"
            f"  Strategy: {_escape_html(str(info.get('current_strategy', 'N/A')))} | "
            f"Regime: {_escape_html(str(info.get('active_regime', 'N/A')))}"
        )

    return "\n".join(lines)


def format_trade_closed_message(trade) -> str:
    """Format a VirtualTrade close notification — institutional fund-manager grade.

    Shows complete trade lifecycle: entry → exit with profit ladder rungs,
    R-multiple, duration, and running portfolio statistics.
    """
    profitable = trade.net_pnl >= 0
    emoji = "📗" if profitable else "📕"
    result_emoji = "✅" if profitable else "❌"

    strat_label = STRATEGY_NAMES.get(trade.strategy, trade.strategy)

    # Human-readable exit reason
    exit_reason_map = {
        "TARGET": "TARGET HIT",
        "TARGET_1R": "TARGET 1R",
        "TARGET_2R": "TARGET 2R",
        "STOP": "STOPPED OUT",
        "TRAIL_STOP": "TRAILING STOP",
        "TIME_STOP": "TIME STOP",
        "REGIME_FLATTEN": "REGIME FLATTEN",
        "REGIME_FLIP_FLATTEN_SHORT": "REGIME FLIP",
        "REGIME_FLIP_FLATTEN_SHORT_AND_INVERSE": "REGIME FLIP (INV)",
        "REGIME_FLIP_FLATTEN_LONG": "REGIME FLIP",
        "REGIME_RISK_OFF_FLATTEN": "RISK OFF FLATTEN",
        "LSE_FORCE_CLOSE_1550": "LSE EOD (15:50 UK)",
        "EOD_FORCE_CLOSE": "EOD CLOSE",
        "OVERSEER": "OVERSEER HALT",
        "MANUAL": "MANUAL CLOSE",
        "KILL_SWITCH": "KILL SWITCH",
        "TIME_DECAY_PRESSURE": "TIME DECAY",
        "CIRCUIT_BREAKER_RED": "CIRCUIT BREAKER",
        "FIREWALL_HOLDING_LOSER": "FIREWALL",
        "ETP_OVERNIGHT_PROTECTION": "ETP OVERNIGHT",
        "ETP_TARGET_5%": "ETP TARGET",
    }
    exit_label = exit_reason_map.get(
        str(trade.exit_reason).upper() if trade.exit_reason else "",
        str(trade.exit_reason) if trade.exit_reason else "UNKNOWN",
    )

    # Duration formatting
    dur = getattr(trade, "duration_minutes", 0) or 0
    if dur >= 60:
        hours = int(dur // 60)
        mins = int(dur % 60)
        dur_str = f"{hours}h{mins}m" if mins else f"{hours}h"
    else:
        dur_str = f"{int(dur)}m"

    r_sign = "+" if trade.r_multiple >= 0 else ""
    pnl_sign = "+" if trade.net_pnl >= 0 else ""

    peak_r = getattr(trade, "peak_r", 0) or 0
    trough_r = getattr(trade, "trough_r", 0) or 0
    confidence = getattr(trade, "confidence", 0) or 0
    regime = getattr(trade, "regime_at_entry", "N/A") or "N/A"

    # P&L percentage
    pnl_pct = ((trade.exit_price - trade.entry_price) / trade.entry_price * 100) if trade.entry_price > 0 else 0
    if trade.direction == "SHORT":
        pnl_pct = -pnl_pct

    # Exit efficiency: how much of peak was captured
    exit_efficiency = (trade.r_multiple / peak_r * 100) if peak_r > 0 else 0

    # Build profit ladder summary from partials
    partials = getattr(trade, "partials", []) or []
    ladder_lines = []
    for p in partials:
        rung = p.get("rung", 0)
        p_pnl = p.get("pnl", 0)
        ladder_lines.append(f"  • Rung {rung}: £{p_pnl:+.2f}")

    lines = [
        f"━━━━━━━━━━━━━━━━━━━",
        f"{emoji} NZT-48 | TRADE CLOSED",
        f"━━━━━━━━━━━━━━━━━━━",
        f"{trade.direction} {trade.ticker} → {exit_label} {result_emoji}",
        f"━━━━━━━━━━━━━━━━━━━",
        f"",
        f"Entry:  £{trade.entry_price:.3f} → Exit: £{trade.exit_price:.3f}",
        f"P&L:    {pnl_sign}£{abs(trade.net_pnl):.2f} ({pnl_pct:+.1f}%)",
        f"R:      {r_sign}{trade.r_multiple:.1f}R",
        f"Duration: {dur_str}",
    ]

    if ladder_lines:
        lines.append(f"")
        lines.append(f"PROFIT LADDER")
        lines.extend(ladder_lines)

    if peak_r > 0:
        lines.append(f"  • Peak: +{peak_r:.1f}R (captured {exit_efficiency:.0f}%)")

    lines.extend([
        f"",
        f"Conf: {confidence:.0f} | Regime: {regime}",
        f"Strategy: {trade.strategy} {strat_label}",
        f"━━━━━━━━━━━━━━━━━━━",
    ])

    return "\n".join(lines)


def format_firewall_block_message(signal, pattern: str, reason: str) -> str:
    """Format a firewall block notification.

    Example output:
    🛡 FIREWALL BLOCK: REVENGE
    Blocked: LONG NVDA | S2 | Conf: 72
    Reason: Signal < 5 min after stop-out
    """
    direction = getattr(signal, "direction", "")
    if hasattr(direction, "value"):
        direction = direction.value
    ticker = getattr(signal, "ticker", "???")
    strategy = getattr(signal, "strategy", "??")
    confidence = getattr(signal, "confidence", 0)

    lines = [
        f"\U0001F6E1 FIREWALL BLOCK: {pattern.upper()}",
        f"Blocked: {direction} {ticker} | {strategy} | Conf: {confidence:.0f}",
        f"Reason: {reason}",
    ]

    return "\n".join(lines)


def format_regime_transition_action_message(
    old_regime: str,
    new_regime: str,
    positions_affected: int,
    realized_pnl: float,
) -> str:
    """Format a regime transition flatten notification.

    Example output:
    ⚡ REGIME FLATTEN: TRENDING_UP → TRENDING_DOWN
    Closed 3 longs | Realised: +$450
    """
    pnl_sign = "+" if realized_pnl >= 0 else "-"
    side = "positions"
    if old_regime and "UP" in old_regime.upper():
        side = "longs"
    elif old_regime and "DOWN" in old_regime.upper():
        side = "shorts"

    lines = [
        f"\u26A1 REGIME FLATTEN: {old_regime} \u2192 {new_regime}",
        f"Closed {positions_affected} {side} | Realised: {pnl_sign}${abs(realized_pnl):.0f}",
    ]

    return "\n".join(lines)


def format_nightly_digest_message(digest: dict) -> str:
    """Format a comprehensive nightly digest report.

    Expected digest keys:
    - strategy_pnl: dict of {strategy: {pnl, trades, wins, avg_r}}
    - missed_trade_summary: {total_blocked, would_have_won, edge_lost_r, worst_filter}
    - firewall_summary: {total_blocks, patterns: {pattern: count}}
    - filter_analysis: str (one-line summary)
    - top_autopsy_lesson: str
    """
    lines = ["\U0001F319 <b>NIGHTLY DIGEST</b>\n"]

    # --- Strategy P&L breakdown ---
    strategy_pnl = digest.get("strategy_pnl", {})
    if strategy_pnl:
        lines.append("<b>Strategy Performance</b>")
        total_pnl = 0.0
        total_trades = 0
        total_wins = 0
        for strat, data in strategy_pnl.items():
            pnl = data.get("pnl", 0)
            trades = data.get("trades", 0)
            wins = data.get("wins", 0)
            avg_r = data.get("avg_r", 0)
            total_pnl += pnl
            total_trades += trades
            total_wins += wins
            emoji = "\U0001F7E2" if pnl >= 0 else "\U0001F534"
            wr = (wins / trades * 100) if trades > 0 else 0
            pnl_sign = "+" if pnl >= 0 else ""
            lines.append(
                f"  {emoji} {_escape_html(strat)}: {pnl_sign}${pnl:.0f} | "
                f"{trades} trades | WR: {wr:.0f}% | Avg: {avg_r:+.1f}R"
            )
        total_emoji = "\U0001F7E2" if total_pnl >= 0 else "\U0001F534"
        total_wr = (total_wins / total_trades * 100) if total_trades > 0 else 0
        total_sign = "+" if total_pnl >= 0 else ""
        lines.append(
            f"  {total_emoji} <b>TOTAL: {total_sign}${total_pnl:.0f} | "
            f"{total_trades} trades | WR: {total_wr:.0f}%</b>"
        )
    lines.append("")

    # --- Missed trades / firewall impact ---
    missed = digest.get("missed_trade_summary", {})
    if missed:
        lines.append("<b>Missed Trade Analysis</b>")
        total_blocked = missed.get("total_blocked", 0)
        would_have_won = missed.get("would_have_won", 0)
        edge_lost = missed.get("edge_lost_r", 0)
        worst = missed.get("worst_filter", "N/A")
        lines.append(f"  Blocked: {total_blocked} | Would have won: {would_have_won}")
        lines.append(f"  Edge lost: {edge_lost:+.1f}R | Worst filter: {_escape_html(worst)}")
    lines.append("")

    # --- Firewall summary ---
    fw = digest.get("firewall_summary", {})
    if fw:
        lines.append("<b>Firewall Summary</b>")
        lines.append(f"  Total blocks: {fw.get('total_blocks', 0)}")
        patterns = fw.get("patterns", {})
        if patterns:
            pattern_parts = [
                f"{_escape_html(p)}: {c}" for p, c in patterns.items()
            ]
            lines.append(f"  Patterns: {' | '.join(pattern_parts)}")
    lines.append("")

    # --- Filter analysis ---
    filter_analysis = digest.get("filter_analysis", "")
    if filter_analysis:
        lines.append(f"<b>Filter:</b> {_escape_html(filter_analysis)}")

    # --- Top autopsy lesson ---
    lesson = digest.get("top_autopsy_lesson", "")
    if lesson:
        lines.append(f"\n\U0001F4A1 <b>Lesson:</b> {_escape_html(lesson)}")

    return "\n".join(lines)


def format_overseer_message(overseer_data: dict) -> str:
    """Format /overseer command: aggregate exposure, restrictions (HTML parse mode)."""
    lines = ["🏛️ <b>PORTFOLIO OVERSEER</b>\n"]

    lines.append(f"Aggregate Exposure: {overseer_data.get('net_exposure', 0):.0f}% "
                 f"(limit: 150%)")
    lines.append(f"Portfolio Heat: {overseer_data.get('heat_score', 0):.1f}% "
                 f"(limit: 3.0%)")
    lines.append(f"Direction: {overseer_data.get('direction_concentration', 0):.0f}% "
                 f"same-way (limit: 85%)")
    lines.append(f"Sector Max: {overseer_data.get('max_sector_pct', 0):.0f}% "
                 f"(limit: 50%)")
    lines.append(f"Daily Loss: {overseer_data.get('daily_loss_pct', 0):+.2f}% "
                 f"(limit: -1.5%)")

    restrictions = overseer_data.get("restrictions", [])
    if restrictions:
        lines.append("\n⚠️ <b>ACTIVE RESTRICTIONS:</b>")
        for r in restrictions:
            r_type = _escape_html(str(r.get('type', 'N/A')))
            r_value = _escape_html(str(r.get('value', 'N/A')))
            r_reason = _escape_html(str(r.get('reason', 'N/A')))
            lines.append(f"  🚫 {r_type}: {r_value} — {r_reason}")
    else:
        lines.append("\n✅ No active restrictions")

    return "\n".join(lines)


class TelegramDelivery:
    """Telegram bot for signal delivery and command handling.

    Uses python-telegram-bot library for async Telegram API interaction.
    Supports all 10 commands defined in Section 54.
    """

    # Timeout constants (seconds) — python-telegram-bot v20+ kwargs
    _CONNECT_TIMEOUT = 10
    _READ_TIMEOUT = 15
    _WRITE_TIMEOUT = 15

    # Exponential backoff delays for retries (seconds)
    _RETRY_DELAYS = [2, 5]  # retry 1: 2s, retry 2: 5s, then give up

    def __init__(self, token: str = "", chat_id: str = "") -> None:
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        self._bot = None
        self._app = None

        # Command handlers registry
        self._command_handlers: dict[str, Callable] = {}

        # Kill switch state
        self._killed_strategies: set[str] = set()
        self._paused_strategies: set[str] = set()

        # Enabled flag — False when token is missing
        self._enabled = bool(self.token and self.chat_id)
        # Guard flag: log "not configured" warning only once per session
        self._not_configured_warned = False
        if not self._enabled:
            logger.warning("TELEGRAM: bot token or chat_id not configured — delivery DISABLED")
            self._not_configured_warned = True
        else:
            logger.info("TELEGRAM: configured with chat_id=%s", self.chat_id[:4] + "***")

        # Dedupe, rate limiter, and debug logger
        self._dedupe = TelegramDedupe()
        self._rate_limiter = TelegramRateLimiter()
        self._debug_logger = TelegramDebugLogger()

    async def verify_connection(self) -> bool:
        """Verify Telegram bot connection by calling getMe API.

        Returns True if connected successfully, False otherwise.
        Includes explicit timeout to avoid hanging on network issues.
        """
        if not self._enabled:
            if not self._not_configured_warned:
                logger.warning("TELEGRAM: verify_connection skipped — bot not enabled")
                self._not_configured_warned = True
            return False
        try:
            from telegram import Bot
            bot = Bot(token=self.token)
            me = await bot.get_me(
                read_timeout=self._READ_TIMEOUT,
                connect_timeout=self._CONNECT_TIMEOUT,
            )
            logger.info("TELEGRAM: connection verified — bot @%s (id=%s)", me.username, me.id)
            return True
        except Exception as e:
            logger.error("TELEGRAM: connection verification FAILED: %s", e)
            self._enabled = False
            return False

    async def initialize(self) -> None:
        """Initialize the Telegram bot and register command handlers."""
        try:
            from telegram import Bot
            from telegram.ext import Application, CommandHandler

            self._app = Application.builder().token(self.token).build()
            self._bot = self._app.bot

            # Register commands
            self._app.add_handler(CommandHandler("taken", self._handle_taken))
            self._app.add_handler(CommandHandler("skipped", self._handle_skipped))
            self._app.add_handler(CommandHandler("positions", self._handle_positions))
            self._app.add_handler(CommandHandler("close", self._handle_close))
            self._app.add_handler(CommandHandler("stats", self._handle_stats))
            self._app.add_handler(CommandHandler("today", self._handle_today))
            self._app.add_handler(CommandHandler("pause", self._handle_pause))
            self._app.add_handler(CommandHandler("kill", self._handle_kill))
            self._app.add_handler(CommandHandler("bots", self._handle_bots))
            self._app.add_handler(CommandHandler("overseer", self._handle_overseer))

            logger.info("Telegram bot initialized")

        except ImportError:
            logger.warning("python-telegram-bot not installed. Telegram delivery disabled.")
        except Exception as e:
            logger.error("Failed to initialize Telegram bot: %s", e)

    async def send_signal(self, signal) -> bool:
        """Send a formatted signal to the Telegram chat.
        Routes to send_buy_signal or send_sell_signal based on direction.
        Special handling for S15 (2% Daily Target).
        """
        strategy = getattr(signal, 'strategy', '')
        if strategy == "S15":
            return await self.send_daily_target_signal(signal)

        direction = getattr(signal.direction, 'value', str(signal.direction))
        if direction == "LONG":
            return await self.send_buy_signal(signal)
        else:
            return await self.send_sell_signal(signal)

    async def send_daily_target_signal(self, signal) -> bool:
        """Send institutional fund-manager grade S15 entry signal.

        Format designed for professional fund operations:
        - Full trade parameters with R:R
        - Market context (regime, VIX, sector, RVOL)
        - Score decomposition showing WHY this trade was selected
        - Compounding progress tracker
        """
        direction = getattr(signal.direction, 'value', str(signal.direction))
        ticker = getattr(signal, 'ticker', 'N/A')
        entry = getattr(signal, 'entry', 0)
        stop = getattr(signal, 'stop', 0)
        confidence = getattr(signal, 'confidence', 0)
        risk_dollars = getattr(signal, 'risk_dollars', 0)
        shares = getattr(signal, 'shares', 0)
        rvol = getattr(signal, 'rvol', 0) or 0

        # Calculate target and R:R
        if direction == "LONG":
            target = round(entry * 1.02, 2)
            stop_pct = ((stop - entry) / entry * 100) if entry > 0 else 0
        else:
            target = round(entry * 0.98, 2)
            stop_pct = ((entry - stop) / entry * 100) if entry > 0 else 0

        rr = abs(target - entry) / abs(entry - stop) if abs(entry - stop) > 0 else 0
        position_value = shares * entry

        # Get regime and VIX from signal metadata
        regime = getattr(signal, 'regime', None)
        regime_str = regime.value if hasattr(regime, 'value') else str(regime or 'N/A')
        vix = getattr(signal, 'vix', 0) or 0

        # Confidence breakdown if available
        cb = getattr(signal, 'confidence_breakdown', None)

        message = (
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>NZT-48 | TRADE ENTRY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<b>{_escape_html(direction)} {_escape_html(ticker)}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"Entry:   <code>£{entry:.3f}</code>\n"
            f"Stop:    <code>£{stop:.3f} ({stop_pct:+.1f}%)</code>\n"
            f"Target:  <code>£{target:.3f} (+2.0%)</code>\n"
            f"R:R:     <code>{rr:.1f}:1</code>\n\n"
            f"Size:    <code>{shares} shares (£{position_value:.2f})</code>\n"
            f"Risk:    <code>£{risk_dollars:.2f} (0.75% of equity)</code>\n"
            f"Strategy: S15 Daily Target\n"
            f"Confidence: <code>{confidence:.0f}/100</code>\n\n"
            f"<b>MARKET CONTEXT</b>\n"
            f"• Regime: {_escape_html(regime_str)}\n"
            f"• VIX: {vix:.1f}\n"
            f"• RVOL: {rvol:.1f}x\n\n"
            f"<b>PATHWAY: S15_PRIORITY</b>\n"
            f"• Bypassed 18-gate gauntlet\n"
            f"• 5 essential gates only\n"
        )

        # Play score section — only if present
        play_score = getattr(signal, 'play_score', None)
        if play_score and isinstance(play_score, dict):
            ps_score = play_score.get('score', 0)
            ps_grade = play_score.get('grade', '?')
            ps_bracket = play_score.get('bracket', 'N/A')
            ps_reasons = play_score.get('reasons', [])
            ps_learning_boost = play_score.get('self_learning_boost', 0.0)

            message += (
                f"\n<b>PLAY SCORE: {ps_score}/100 ({_escape_html(ps_grade)})</b>\n"
                f"Bracket: {_escape_html(ps_bracket)}\n"
            )
            for r in ps_reasons:
                factor = _escape_html(str(r.get('factor', '?')))
                contribution = r.get('contribution', 0)
                weight_pct = r.get('weight', 0) * 100
                message += f"• {factor}: {contribution:+.1f} ({weight_pct:.0f}%)\n"

            message += f"Learning Boost: {ps_learning_boost:+.1f} vs neutral\n"

        message += f"━━━━━━━━━━━━━━━━━━━"
        return await self._send_message(message)

    async def send_sector_rotation_alert(self, old_leader: str, new_leader: str,
                                          old_rs: float, new_rs: float,
                                          old_ticker: str, new_ticker: str,
                                          trigger: str = "5-day RS crossover") -> bool:
        """Send INSTANT sector rotation alert — fires immediately, no delay.

        This is the institutional-grade alert that fund managers need:
        sector leadership shifts with actionable instrument recommendations.
        """
        message = (
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ <b>SECTOR ROTATION ALERT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<b>LEADERSHIP SHIFT DETECTED</b>\n\n"
            f"{_escape_html(old_leader)} → {_escape_html(new_leader)}\n"
            f"• {_escape_html(old_leader)} RS: {old_rs:.2f} (declining)\n"
            f"• {_escape_html(new_leader)} RS: {new_rs:.2f} (surging)\n\n"
            f"<b>ACTION: Rotate ISA allocation</b>\n"
            f"• Reduce: {_escape_html(old_ticker)} exposure\n"
            f"• Increase: {_escape_html(new_ticker)} exposure\n\n"
            f"Triggered by: {_escape_html(trigger)}\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )
        return await self._send_message(message)

    async def send_vix_threshold_alert(self, vix: float, threshold: float, direction: str) -> bool:
        """Send VIX threshold crossing alert — fires when VIX crosses 22 or 30."""
        emoji = "🔴" if direction == "UP" else "🟢"
        action = "REDUCE SIZE / NO 5x" if vix >= 22 else "FULL SIZE RESTORED"
        message = (
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"{emoji} <b>VIX THRESHOLD ALERT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"VIX crossed {threshold:.0f} ({direction})\n"
            f"Current: <code>{vix:.1f}</code>\n\n"
            f"<b>ACTION: {_escape_html(action)}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )
        return await self._send_message(message)

    async def send_league_update(self, action: str, ticker: str, stats: dict) -> bool:
        """Send B-Team promotion/relegation alert."""
        emoji = "⬆️" if "PROMOT" in action.upper() else "⬇️" if "RELEGAT" in action.upper() else "🆕"
        win_rate = stats.get("win_rate", 0) * 100
        avg_r = stats.get("avg_r", 0)
        trades = stats.get("trades", 0)
        message = (
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"{emoji} <b>LEAGUE UPDATE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<b>{_escape_html(action)}: {_escape_html(ticker)}</b>\n\n"
            f"• Trades: {trades}\n"
            f"• Win Rate: {win_rate:.0f}%\n"
            f"• Avg R: {avg_r:+.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )
        return await self._send_message(message)

    async def send_compounding_milestone(self, equity: float, milestone: float, day_number: int) -> bool:
        """Send compounding milestone alert — every £1,000 threshold."""
        target_equity = 10000 * (1.02 ** day_number)
        gap = equity - target_equity
        gap_emoji = "✅" if gap >= 0 else "⚠️"
        message = (
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🏆 <b>COMPOUNDING MILESTONE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"Equity: <code>£{equity:,.2f}</code>\n"
            f"Milestone: <code>£{milestone:,.0f}</code> reached!\n\n"
            f"Day #{day_number} of 252\n"
            f"Target: <code>£{target_equity:,.2f}</code>\n"
            f"Gap: <code>£{gap:+,.2f}</code> {gap_emoji}\n"
            f"━━━━━━━━━━━━━━━━━━━"
        )
        return await self._send_message(message)

    async def send_buy_signal(self, signal) -> bool:
        """Send a BUY signal with clear header."""
        header = "\U0001F7E2\U0001F7E2 BUY SIGNAL \U0001F7E2\U0001F7E2"
        message = format_signal_message(signal)
        return await self._send_message(f"{header}\n\n{message}")

    async def send_sell_signal(self, signal) -> bool:
        """Send a SELL signal with clear header."""
        header = "\U0001F534\U0001F534 SELL SIGNAL \U0001F534\U0001F534"
        message = format_signal_message(signal)
        return await self._send_message(f"{header}\n\n{message}")

    async def send_alert(self, message: str) -> bool:
        """Send a plain text alert (regime change, overseer warning, etc)."""
        return await self._send_message(message)

    async def _gated_send(self, text: str, label: str = "", ticker: str = "", play: dict = None) -> bool:
        """Send message through validation, dedupe, and rate limiting gates."""
        content_hash = hashlib.md5(text.encode()).hexdigest()[:12]

        # Gate 1: Validate signal (if play provided)
        if play and not validate_telegram_signal(play):
            self._debug_logger.log("GATE_FAILED", label, ticker, content_hash, "Invalid signal data")
            return False

        # Gate 2: Dedupe
        if not self._dedupe.should_send(content_hash):
            self._debug_logger.log("DEDUPED", label, ticker, content_hash)
            return False

        # Gate 3: Rate limit
        can_send, reason = self._rate_limiter.can_send()
        if not can_send:
            self._debug_logger.log("RATE_LIMITED", label, ticker, content_hash, reason)
            return False

        # Send
        try:
            await self.send_alert(text)
            self._rate_limiter.record_send()
            self._debug_logger.log("SENT", label, ticker, content_hash)
            return True
        except Exception as e:
            self._debug_logger.log("ERROR", label, ticker, content_hash, str(e))
            return False

    async def send_regime_change(self, old_regime: str, new_regime: str,
                                  action: str) -> bool:
        """Send a regime transition alert."""
        message = (
            f"⚡ <b>REGIME CHANGE</b>\n\n"
            f"{_escape_html(old_regime)} → {_escape_html(new_regime)}\n"
            f"Action: {_escape_html(action)}"
        )
        return await self._send_message(message)

    async def send_kill_switch_alert(self, reason: str) -> bool:
        """Send emergency kill switch notification."""
        message = (
            f"🚨🚨🚨 <b>KILL SWITCH ACTIVATED</b> 🚨🚨🚨\n\n"
            f"Reason: {_escape_html(reason)}\n"
            f"All signals HALTED. Manual intervention required."
        )
        return await self._send_message(message)

    async def send_daily_summary(self, summary: dict) -> bool:
        """Send end-of-day summary."""
        lines = [
            "📊 <b>END OF DAY SUMMARY</b>\n",
            f"Date: {_escape_html(str(summary.get('date', 'N/A')))}",
            f"Regime: {_escape_html(str(summary.get('regime', 'N/A')))}",
            f"Trades: {summary.get('trades_taken', 0)} taken, "
            f"{summary.get('signals_skipped', 0)} skipped",
            f"P&amp;L: ${summary.get('pnl_dollars', 0):+.2f} "
            f"({summary.get('pnl_pct', 0):+.2f}%)",
            f"W/L: {summary.get('wins', 0)}/{summary.get('losses', 0)}",
            f"Best: {summary.get('best_r', 0):+.1f}R | "
            f"Worst: {summary.get('worst_r', 0):+.1f}R",
            f"Emotional Grade: {_escape_html(str(summary.get('emotional_grade', 'N/A')))}",
            f"\n💡 {_escape_html(str(summary.get('lesson', 'No lesson recorded.')))}",
        ]
        return await self._send_message("\n".join(lines))

    async def send_trade_closed(self, trade) -> bool:
        """Send a trade close notification."""
        message = format_trade_closed_message(trade)
        return await self._send_message(message, parse_mode=None)

    async def send_firewall_block(self, signal, pattern: str, reason: str) -> bool:
        """Send a firewall block notification."""
        message = format_firewall_block_message(signal, pattern, reason)
        return await self._send_message(message, parse_mode=None)

    async def send_document(self, file_path: str, caption: str = "") -> bool:
        """Send a document (PDF, etc.) to the configured chat ID.

        Includes timeouts and exponential backoff retries.
        Falls back to plain text caption if HTML caption fails.
        """
        from pathlib import Path
        fp = Path(file_path)
        if not fp.exists():
            logger.warning("File not found for Telegram send: %s", file_path)
            return False
        if not self._bot or not self.chat_id:
            if not self._not_configured_warned:
                logger.warning("Telegram not configured. Document: %s", file_path)
                self._not_configured_warned = True
            logger.debug("TELEGRAM (paper): would send document %s", fp.name)
            return False

        timeout_kwargs = dict(
            connect_timeout=self._CONNECT_TIMEOUT,
            read_timeout=self._READ_TIMEOUT,
            write_timeout=self._WRITE_TIMEOUT,
        )
        cap = caption[:1024] if caption else fp.name

        # Try with HTML parse_mode first, then fallback to plain text
        for parse_mode in ("HTML", None):
            for attempt, delay in enumerate(self._RETRY_DELAYS, start=1):
                try:
                    with open(fp, 'rb') as f:
                        send_kwargs = dict(
                            chat_id=self.chat_id,
                            document=f,
                            filename=fp.name,
                            caption=cap,
                            **timeout_kwargs,
                        )
                        if parse_mode:
                            send_kwargs["parse_mode"] = parse_mode
                        await self._bot.send_document(**send_kwargs)
                    if parse_mode is None:
                        logger.info("Telegram document sent (plain text fallback): %s", fp.name)
                    else:
                        logger.info("Telegram document sent: %s", fp.name)
                    return True
                except Exception as e:
                    error_str = str(e).lower()
                    # HTML parse error — break to plain text fallback immediately
                    if parse_mode and ("parse" in error_str or "bad request" in error_str):
                        logger.warning(
                            "TELEGRAM: document HTML caption parse error, falling back to plain: %s", e
                        )
                        break
                    logger.warning(
                        "TELEGRAM: document send failed (attempt %d/%d, %s): %s — retrying in %ds",
                        attempt, len(self._RETRY_DELAYS) + 1, fp.name, e, delay,
                    )
                    await asyncio.sleep(delay)
            else:
                # All retries exhausted for this parse_mode
                if parse_mode is None:
                    # Plain text fallback also exhausted
                    logger.error("TELEGRAM: document send FAILED after all retries: %s", fp.name)
                    return False
                # HTML retries exhausted, fall through to plain text
                logger.warning("TELEGRAM: document HTML retries exhausted, trying plain text: %s", fp.name)
                continue
            # break from inner loop (parse error) → try next parse_mode
            continue

        logger.error("TELEGRAM: document send FAILED after all retries: %s", fp.name)
        return False

    async def send_regime_action(
        self,
        old_regime: str,
        new_regime: str,
        positions_affected: int,
        realized_pnl: float,
    ) -> bool:
        """Send a regime transition flatten notification."""
        message = format_regime_transition_action_message(
            old_regime, new_regime, positions_affected, realized_pnl
        )
        return await self._send_message(message, parse_mode=None)

    async def send_nightly_digest(self, digest: dict) -> bool:
        """Send the comprehensive nightly digest report."""
        message = format_nightly_digest_message(digest)
        return await self._send_message(message)

    async def _send_message(self, text: str, parse_mode: Optional[str] = "HTML") -> bool:
        """Send a message to the configured chat ID.

        Reliability features:
        - Explicit connect/read/write timeouts (10s/15s/15s)
        - Exponential backoff retries: 2s, 5s, then give up
        - HTML parse_mode fallback: if send fails with HTML, retry as plain text
        """
        if not self._enabled:
            logger.debug("TELEGRAM: message not sent — bot disabled")
            return False
        if not self._bot or not self.chat_id:
            if not self._not_configured_warned:
                logger.warning("Telegram not configured — message delivery disabled")
                self._not_configured_warned = True
            logger.debug("TELEGRAM (paper): %s", text[:100])
            return False

        timeout_kwargs = dict(
            connect_timeout=self._CONNECT_TIMEOUT,
            read_timeout=self._READ_TIMEOUT,
            write_timeout=self._WRITE_TIMEOUT,
        )

        # --- Attempt 1: send with requested parse_mode ---
        last_error = None
        for attempt, delay in enumerate(self._RETRY_DELAYS, start=1):
            try:
                await self._bot.send_message(
                    chat_id=self.chat_id,
                    text=text,
                    parse_mode=parse_mode,
                    **timeout_kwargs,
                )
                return True
            except Exception as e:
                last_error = e
                error_str = str(e).lower()

                # If it's an HTML parse error, break out immediately to fallback
                if parse_mode and ("parse" in error_str or "can't parse" in error_str
                                   or "bad request" in error_str):
                    logger.warning(
                        "TELEGRAM: HTML parse error (attempt %d), falling back to plain text: %s",
                        attempt, e,
                    )
                    break

                logger.warning(
                    "TELEGRAM: send failed (attempt %d/%d, parse_mode=%s, len=%d): %s — "
                    "retrying in %ds",
                    attempt, len(self._RETRY_DELAYS) + 1, parse_mode, len(text), e, delay,
                )
                await asyncio.sleep(delay)
        else:
            # All retries exhausted with original parse_mode
            logger.error(
                "TELEGRAM: all %d retries failed (parse_mode=%s, len=%d): %s — msg: %s",
                len(self._RETRY_DELAYS), parse_mode, len(text), last_error, text[:80],
            )
            # Fall through to plain-text fallback if parse_mode was set
            if not parse_mode:
                return False

        # --- Attempt 2: fallback to plain text (no parse_mode) ---
        if parse_mode:
            logger.info("TELEGRAM: retrying without parse_mode (plain text fallback)")
            for attempt, delay in enumerate(self._RETRY_DELAYS, start=1):
                try:
                    await self._bot.send_message(
                        chat_id=self.chat_id,
                        text=text,
                        **timeout_kwargs,
                    )
                    logger.info("TELEGRAM: plain text fallback succeeded on attempt %d", attempt)
                    return True
                except Exception as e2:
                    logger.warning(
                        "TELEGRAM: plain text fallback failed (attempt %d/%d): %s — "
                        "retrying in %ds",
                        attempt, len(self._RETRY_DELAYS) + 1, e2, delay,
                    )
                    await asyncio.sleep(delay)

            logger.error(
                "TELEGRAM: all retries exhausted (plain text fallback, len=%d) — message DROPPED: %s",
                len(text), text[:80],
            )

        return False

    # === Command Handlers ===

    async def _handle_taken(self, update, context) -> None:
        """Handler for /taken — log signal as EXECUTED and update DB."""
        args = context.args if context.args else []
        signal_id = args[0] if args else "last"
        try:
            from delivery.database import get_connection
            conn = get_connection()
            try:
                if signal_id == "last":
                    row = conn.execute(
                        "SELECT id FROM signals WHERE status = 'PENDING' ORDER BY timestamp DESC LIMIT 1"
                    ).fetchone()
                    if row:
                        signal_id = row["id"]
                    else:
                        await update.message.reply_text("No pending signals found.")
                        return
                conn.execute(
                    "UPDATE signals SET status = 'TAKEN' WHERE id = ?", (signal_id,)
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error("Failed to update signal status: %s", e)
        logger.info("Signal %s marked as TAKEN", signal_id)
        await update.message.reply_text(f"✅ Signal {signal_id} marked as TAKEN. Tracking started.")

    async def _handle_skipped(self, update, context) -> None:
        """Handler for /skipped — log signal as SKIPPED and update DB."""
        args = context.args if context.args else []
        signal_id = args[0] if args else "last"
        reason = " ".join(args[1:]) if len(args) > 1 else "No reason given"
        try:
            from delivery.database import get_connection
            conn = get_connection()
            try:
                if signal_id == "last":
                    row = conn.execute(
                        "SELECT id FROM signals WHERE status = 'PENDING' ORDER BY timestamp DESC LIMIT 1"
                    ).fetchone()
                    if row:
                        signal_id = row["id"]
                    else:
                        await update.message.reply_text("No pending signals found.")
                        return
                conn.execute(
                    "UPDATE signals SET status = 'SKIPPED', rejection_reason = ? WHERE id = ?",
                    (reason, signal_id),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error("Failed to update signal status: %s", e)
        logger.info("Signal %s marked as SKIPPED: %s", signal_id, reason)
        await update.message.reply_text(f"⏭ Signal {signal_id} SKIPPED. Reason: {_escape_html(reason)}")

    async def _handle_positions(self, update, context) -> None:
        """Handler for /positions — show all open positions with live P&L."""
        try:
            from delivery.database import get_connection, get_open_positions
            conn = get_connection()
            try:
                positions = get_open_positions(conn)
                if not positions:
                    await update.message.reply_text("No open positions.")
                    return

                lines = ["OPEN POSITIONS\n"]
                total_pnl = 0.0
                for pos in positions:
                    direction = pos["direction"]
                    ticker = pos["ticker"]
                    shares = pos["shares"] or 0
                    entry = pos["entry"] or 0.0
                    current_price = pos["current_price"] or 0.0
                    pnl = pos["unrealised_pnl"] or 0.0
                    r_mult = pos["unrealised_r"] or 0.0
                    rung = pos["ladder_rung"] or 0
                    stop = pos["current_stop"] or 0.0
                    bot = pos["bot_instance"] or "?"
                    total_pnl += pnl

                    emoji = "+" if pnl >= 0 else ""
                    lines.append(
                        f"{direction} {ticker} | {shares} shares @ ${entry:.2f}\n"
                        f"  Now: ${current_price:.2f} | P&L: {emoji}${pnl:.2f} ({r_mult:+.1f}R)\n"
                        f"  Rung: {rung}/7 | Stop: ${stop:.2f} | Bot: {bot}"
                    )

                lines.append(f"\nTotal Unrealised: ${total_pnl:+.2f}")
                msg = "\n".join(lines)
                # Truncate to Telegram limit
                if len(msg) > 4000:
                    msg = msg[:4000] + "\n... (truncated)"
                await update.message.reply_text(msg)
            finally:
                conn.close()
        except Exception as e:
            logger.error("/positions error: %s", e)
            await update.message.reply_text(f"Error fetching positions: {e}")

    async def _handle_close(self, update, context) -> None:
        """Handler for /close [ticker] — close a virtual position via the virtual trader."""
        args = context.args if context.args else []
        if not args:
            await update.message.reply_text("Usage: /close TICKER [exit_price]")
            return
        ticker = args[0].upper()
        exit_price = 0.0
        if len(args) > 1:
            try:
                exit_price = float(args[1])
            except ValueError:
                await update.message.reply_text(
                    f"Invalid exit price: '{args[1]}'. Usage: /close TICKER [exit_price]"
                )
                return

        try:
            from delivery.database import get_connection
            conn = get_connection()
            try:
                # Find the open virtual position for this ticker
                vpos = conn.execute(
                    "SELECT id, entry_price, direction, shares, current_stop, "
                    "unrealised_pnl, unrealised_r, bot_instance, strategy "
                    "FROM virtual_positions "
                    "WHERE ticker = ? AND status = 'OPEN' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (ticker,)
                ).fetchone()

                if not vpos:
                    # Also check the positions table
                    pos = conn.execute(
                        "SELECT id, entry, direction, shares, current_price, "
                        "unrealised_pnl, bot_instance "
                        "FROM positions WHERE ticker = ? LIMIT 1",
                        (ticker,)
                    ).fetchone()
                    if not pos:
                        await update.message.reply_text(f"No open position found for {ticker}.")
                        return
                    # Close from positions table — just delete it and log
                    if exit_price <= 0:
                        exit_price = pos["current_price"] or pos["entry"] or 0.0
                    conn.execute("DELETE FROM positions WHERE id = ?", (pos["id"],))
                    conn.commit()
                    pnl = pos["unrealised_pnl"] or 0.0
                    await update.message.reply_text(
                        f"Closed {pos['direction']} {ticker}\n"
                        f"Entry: ${pos['entry']:.2f} -> Exit: ${exit_price:.2f}\n"
                        f"P&L: ${pnl:+.2f}\n"
                        f"(Position removed from tracking)"
                    )
                    return

                # Use the virtual trader to close properly
                position_id = vpos["id"]
                if exit_price <= 0:
                    # Use current_stop as fallback if no exit price given
                    # Try to get a recent market price first
                    md_row = conn.execute(
                        "SELECT close FROM market_data WHERE ticker = ? ORDER BY timestamp DESC LIMIT 1",
                        (ticker,)
                    ).fetchone()
                    if md_row and md_row["close"]:
                        exit_price = md_row["close"]
                    else:
                        exit_price = vpos["entry_price"]
                        await update.message.reply_text(
                            f"No current price available for {ticker}. "
                            f"Using entry price ${exit_price:.2f}. "
                            f"Specify exit price: /close {ticker} <price>"
                        )
                        return

                # Close via direct DB update (mark position closed, insert trade)
                now_str = datetime.now(timezone.utc).isoformat()
                entry_price = vpos["entry_price"]
                direction = vpos["direction"]
                shares = vpos["shares"] or 0
                risk_per_share = abs(entry_price - vpos["current_stop"]) if vpos["current_stop"] else 1.0
                risk_dollars = risk_per_share * shares if risk_per_share > 0 else 1.0

                if direction == "LONG":
                    gross_pnl = (exit_price - entry_price) * shares
                else:
                    gross_pnl = (entry_price - exit_price) * shares

                r_multiple = gross_pnl / risk_dollars if risk_dollars > 0 else 0.0

                # Mark virtual position closed
                conn.execute(
                    "UPDATE virtual_positions SET status = 'CLOSED' WHERE id = ?",
                    (position_id,)
                )

                # Insert virtual trade record
                trade_id = f"VT-M-{str(position_id)[-8:]}"
                conn.execute(
                    "INSERT OR IGNORE INTO virtual_trades "
                    "(id, position_id, ticker, direction, strategy, bot_instance, "
                    "entry_price, exit_price, entry_time, exit_time, shares, "
                    "risk_dollars, gross_pnl, net_pnl, r_multiple, exit_reason, confidence) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (trade_id, position_id, ticker, direction,
                     vpos["strategy"], vpos["bot_instance"],
                     entry_price, exit_price,
                     "", now_str, shares,
                     risk_dollars, gross_pnl, gross_pnl - (shares * 0.005), r_multiple,
                     "MANUAL", vpos.get("confidence", 0))
                )

                # Also remove from positions table if present
                conn.execute("DELETE FROM positions WHERE ticker = ?", (ticker,))
                conn.commit()

                emoji = "+" if gross_pnl >= 0 else ""
                await update.message.reply_text(
                    f"Closed {direction} {ticker}\n"
                    f"Entry: ${entry_price:.2f} -> Exit: ${exit_price:.2f}\n"
                    f"Shares: {shares} | P&L: {emoji}${gross_pnl:.2f} ({r_multiple:+.1f}R)\n"
                    f"Reason: MANUAL CLOSE"
                )
                logger.info(
                    "Manual close: %s %s @ $%.2f -> $%.2f, P&L=$%.2f (%.1fR)",
                    direction, ticker, entry_price, exit_price, gross_pnl, r_multiple,
                )
            finally:
                conn.close()
        except Exception as e:
            logger.error("/close error: %s", e)
            await update.message.reply_text(f"Error closing position: {e}")

    async def _handle_stats(self, update, context) -> None:
        """Handler for /stats — strategy scorecard with today's performance."""
        try:
            from delivery.database import get_connection, get_strategy_daily_stats
            conn = get_connection()
            try:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

                # Per-strategy stats from strategy_daily_stats table
                strat_rows = get_strategy_daily_stats(conn, today)

                # Overall counts from signals table
                sig_row = conn.execute(
                    "SELECT COUNT(*) as total, "
                    "SUM(CASE WHEN status IN ('TAKEN','QUALIFIED') THEN 1 ELSE 0 END) as qualified "
                    "FROM signals WHERE date(timestamp) = date('now')"
                ).fetchone()
                signals_total = sig_row["total"] if sig_row else 0
                signals_qualified = sig_row["qualified"] if sig_row else 0

                # Today's virtual trades summary
                vt_row = conn.execute(
                    "SELECT COUNT(*) as trades, "
                    "COALESCE(SUM(net_pnl), 0) as total_pnl, "
                    "SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins "
                    "FROM virtual_trades WHERE date(exit_time) = date('now')"
                ).fetchone()
                trade_count = vt_row["trades"] if vt_row else 0
                total_pnl = vt_row["total_pnl"] if vt_row else 0.0
                wins = vt_row["wins"] if vt_row else 0
                win_rate = (wins / trade_count * 100) if trade_count > 0 else 0.0

                lines = [
                    "STRATEGY SCORECARD\n",
                    f"Signals today: {signals_total} received, {signals_qualified} qualified",
                    f"Trades closed: {trade_count} | Win rate: {win_rate:.0f}%",
                    f"Total P&L: ${total_pnl:+.2f}\n",
                ]

                if strat_rows:
                    lines.append("Per-Strategy:")
                    for row in strat_rows:
                        strat = row["strategy"]
                        trades = row["trades"] or 0
                        w = row["wins"] or 0
                        wr = row["win_rate"] or 0.0
                        pf = row["profit_factor"] or 0.0
                        avg_r = row["avg_r"] or 0.0
                        net = row["net_pnl"] or 0.0
                        lines.append(
                            f"  {strat}: {trades} trades | WR={wr*100:.0f}% | "
                            f"PF={pf:.1f} | AvgR={avg_r:+.1f} | ${net:+.2f}"
                        )
                else:
                    lines.append("No per-strategy stats recorded today.")

                msg = "\n".join(lines)
                if len(msg) > 4000:
                    msg = msg[:4000] + "\n... (truncated)"
                await update.message.reply_text(msg)
            finally:
                conn.close()
        except Exception as e:
            logger.error("/stats error: %s", e)
            await update.message.reply_text(f"Error fetching stats: {e}")

    async def _handle_today(self, update, context) -> None:
        """Handler for /today — market context, regime, trades, daily P&L."""
        try:
            from delivery.database import get_connection, get_open_positions
            conn = get_connection()
            try:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

                # Latest regime from regime_history
                regime_row = conn.execute(
                    "SELECT state, vix, gex, internals_composite "
                    "FROM regime_history ORDER BY timestamp DESC LIMIT 1"
                ).fetchone()
                regime = regime_row["state"] if regime_row else "N/A"
                vix = regime_row["vix"] if regime_row and regime_row["vix"] else 0.0
                internals = regime_row["internals_composite"] if regime_row and regime_row["internals_composite"] else 0

                # Open positions count
                positions = get_open_positions(conn)
                pos_count = len(positions)

                # Today's trades
                vt_row = conn.execute(
                    "SELECT COUNT(*) as trades, "
                    "COALESCE(SUM(net_pnl), 0) as total_pnl "
                    "FROM virtual_trades WHERE date(exit_time) = date('now')"
                ).fetchone()
                trade_count = vt_row["trades"] if vt_row else 0
                daily_pnl = vt_row["total_pnl"] if vt_row else 0.0

                # Unrealised P&L from open positions
                unrealised = sum((p["unrealised_pnl"] or 0.0) for p in positions)

                # Time window — check what session we're in
                now_utc = datetime.now(timezone.utc)
                from core.clock import ET_TZ
                et_now = now_utc.astimezone(ET_TZ)
                et_hour = et_now.hour
                if et_hour < 9:
                    time_window = "PRE-MARKET"
                elif et_hour < 10:
                    time_window = "MORNING MOMENTUM (9:30-10:00)"
                elif et_hour < 12:
                    time_window = "MORNING SESSION"
                elif et_hour < 14:
                    time_window = "MIDDAY CHOP"
                elif et_hour < 16:
                    time_window = "POWER HOUR"
                else:
                    time_window = "AFTER HOURS"

                lines = [
                    "TODAY'S CONTEXT\n",
                    f"Regime: {regime}",
                    f"VIX: {vix:.1f}",
                    f"Internals: {internals}/4",
                    f"Window: {time_window}\n",
                    f"Open positions: {pos_count}",
                    f"Trades closed: {trade_count}",
                    f"Realised P&L: ${daily_pnl:+.2f}",
                    f"Unrealised P&L: ${unrealised:+.2f}",
                    f"Net daily: ${daily_pnl + unrealised:+.2f}",
                ]

                await update.message.reply_text("\n".join(lines))
            finally:
                conn.close()
        except Exception as e:
            logger.error("/today error: %s", e)
            await update.message.reply_text(f"Error fetching today's context: {e}")

    async def _handle_pause(self, update, context) -> None:
        """Handler for /pause [strat] — temporarily disable."""
        args = context.args if context.args else []
        if not args:
            if self._paused_strategies:
                await update.message.reply_text(
                    f"Paused: {', '.join(self._paused_strategies)}"
                )
            else:
                await update.message.reply_text("No strategies paused. Usage: /pause S1")
            return
        strat = args[0].upper()
        self._paused_strategies.add(strat)
        logger.warning("Strategy %s PAUSED", strat)
        await update.message.reply_text(f"⏸ Strategy {strat} PAUSED")

    async def _handle_kill(self, update, context) -> None:
        """Handler for /kill [strat] — permanently disable.
        Also serves as kill switch method 1 of 3.
        """
        args = context.args if context.args else []
        if not args:
            await update.message.reply_text(
                "Usage: /kill S1 (disable strategy) or /kill ALL (kill switch)"
            )
            return

        target = args[0].upper()
        if target == "ALL":
            # KILL SWITCH — method 1 of 3
            logger.critical("KILL SWITCH ACTIVATED via Telegram /kill ALL")
            await self.send_kill_switch_alert("Manual kill via Telegram /kill ALL")
            # Write kill switch file (method 2)
            _write_kill_switch_file("Telegram /kill ALL command")
        else:
            self._killed_strategies.add(target)
            logger.warning("Strategy %s permanently KILLED", target)
            await update.message.reply_text(f"☠️ Strategy {target} permanently KILLED")

    async def _handle_bots(self, update, context) -> None:
        """Handler for /bots — show status of BullBot, RangeBot, BearBot."""
        try:
            from delivery.database import get_connection
            conn = get_connection()
            try:
                bot_names = {
                    "BULL": "BullBot",
                    "RANGE": "RangeBot",
                    "BEAR": "BearBot",
                }
                lines = ["BOT STATUS\n"]

                for instance, display_name in bot_names.items():
                    # Is this bot paused or killed?
                    is_active = instance not in self._paused_strategies and instance not in self._killed_strategies

                    # Today's trades for this bot
                    vt_row = conn.execute(
                        "SELECT COUNT(*) as trades, "
                        "COALESCE(SUM(net_pnl), 0) as total_pnl, "
                        "SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins "
                        "FROM virtual_trades "
                        "WHERE bot_instance = ? AND date(exit_time) = date('now')",
                        (instance,)
                    ).fetchone()
                    trade_count = vt_row["trades"] if vt_row else 0
                    daily_pnl = vt_row["total_pnl"] if vt_row else 0.0

                    # Open positions for this bot
                    pos_count = conn.execute(
                        "SELECT COUNT(*) FROM positions WHERE bot_instance = ?",
                        (instance,)
                    ).fetchone()[0]

                    # Also check virtual_positions (OPEN status)
                    vpos_count = conn.execute(
                        "SELECT COUNT(*) FROM virtual_positions "
                        "WHERE bot_instance = ? AND status = 'OPEN'",
                        (instance,)
                    ).fetchone()[0]
                    open_count = max(pos_count, vpos_count)

                    status_str = "ACTIVE" if is_active else "PAUSED"
                    lines.append(
                        f"{display_name} [{status_str}]\n"
                        f"  Open: {open_count} positions\n"
                        f"  Today: {trade_count} trades | P&L: ${daily_pnl:+.2f}"
                    )

                await update.message.reply_text("\n".join(lines))
            finally:
                conn.close()
        except Exception as e:
            logger.error("/bots error: %s", e)
            await update.message.reply_text(f"Error fetching bot status: {e}")

    async def _handle_overseer(self, update, context) -> None:
        """Handler for /overseer — portfolio heat, positions per bot, restrictions."""
        try:
            from delivery.database import get_connection, get_open_positions, get_active_restrictions
            conn = get_connection()
            try:
                # All open positions for heat calculation
                positions = get_open_positions(conn)
                total_risk = sum((p["risk_dollars"] or 0.0) for p in positions)

                # Get equity from equity_snapshots or fall back
                eq_row = conn.execute(
                    "SELECT ending_equity FROM equity_snapshots ORDER BY date DESC LIMIT 1"
                ).fetchone()
                equity = eq_row["ending_equity"] if eq_row and eq_row["ending_equity"] else 10000.0

                heat_pct = (total_risk / equity * 100) if equity > 0 else 0.0

                # Positions per bot
                bot_counts = {}
                for pos in positions:
                    bot = pos["bot_instance"] or "UNKNOWN"
                    bot_counts[bot] = bot_counts.get(bot, 0) + 1

                # Direction concentration
                long_count = sum(1 for p in positions if p["direction"] == "LONG")
                short_count = sum(1 for p in positions if p["direction"] == "SHORT")
                total_pos = len(positions)
                direction_pct = (max(long_count, short_count) / total_pos * 100) if total_pos > 0 else 0

                # Active restrictions
                restrictions = get_active_restrictions(conn)

                # Daily loss check
                vt_row = conn.execute(
                    "SELECT COALESCE(SUM(net_pnl), 0) as daily_pnl "
                    "FROM virtual_trades WHERE date(exit_time) = date('now')"
                ).fetchone()
                daily_pnl = vt_row["daily_pnl"] if vt_row else 0.0
                daily_loss_pct = (daily_pnl / equity * 100) if equity > 0 else 0.0

                lines = [
                    "PORTFOLIO OVERSEER\n",
                    f"Total heat: {heat_pct:.1f}% (limit: 3.0%)",
                    f"Total risk: ${total_risk:.0f} / ${equity:.0f} equity",
                    f"Positions: {total_pos} open",
                ]

                if bot_counts:
                    parts = [f"{bot}: {cnt}" for bot, cnt in sorted(bot_counts.items())]
                    lines.append(f"  Per bot: {', '.join(parts)}")

                lines.append(f"Direction: {long_count}L / {short_count}S ({direction_pct:.0f}% same-way, limit 85%)")
                lines.append(f"Daily P&L: {daily_loss_pct:+.2f}% (limit: -1.5%)")

                if restrictions:
                    lines.append("\nACTIVE RESTRICTIONS:")
                    for r in restrictions:
                        r_type = r["restriction_type"] or "N/A"
                        r_value = r["value"] or "N/A"
                        r_reason = r["reason"] or "N/A"
                        r_bot = r["bot_instance"] or "ALL"
                        lines.append(f"  [{r_bot}] {r_type}: {r_value} -- {r_reason}")
                else:
                    lines.append("\nNo active restrictions.")

                msg = "\n".join(lines)
                if len(msg) > 4000:
                    msg = msg[:4000] + "\n... (truncated)"
                await update.message.reply_text(msg)
            finally:
                conn.close()
        except Exception as e:
            logger.error("/overseer error: %s", e)
            await update.message.reply_text(f"Error fetching Overseer status: {e}")

    def is_strategy_active(self, strategy_id: str) -> bool:
        """Check if a strategy is active (not paused or killed)."""
        return (strategy_id not in self._paused_strategies
                and strategy_id not in self._killed_strategies)

    async def start_polling(self) -> None:
        """Start the Telegram bot polling loop."""
        if self._app:
            logger.info("Starting Telegram bot polling...")
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling()

    async def stop(self) -> None:
        """Stop the Telegram bot."""
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()


# F-04/CRO-08: Kill switch NEVER auto-clears. Requires human confirmation via Telegram /confirm.
_KILL_SWITCH_REQUIRES_HUMAN_CONFIRMATION = True


class KillSwitch:
    """Kill Switch — 3 methods to halt all trading immediately.

    Method 1: Telegram /kill ALL command
    Method 2: Config file flag (data/KILL_SWITCH)
    Method 3: Process signal (SIGTERM/SIGINT)

    When activated, ALL signals are blocked. No exceptions.
    Deactivation requires explicit human confirmation (F-04/CRO-08).
    """

    KILL_FILE = str(Path(__file__).parent.parent / "data" / "KILL_SWITCH")

    def __init__(self) -> None:
        self._process_killed = False

    def is_killed(self) -> bool:
        """Check all 3 kill switch methods."""
        # Method 2: File-based kill switch
        if os.path.exists(self.KILL_FILE):
            return True

        # Method 3: Process signal
        if self._process_killed:
            return True

        return False

    def activate(self, reason: str = "Manual activation") -> None:
        """Activate the kill switch."""
        self._process_killed = True
        _write_kill_switch_file(reason)
        logger.critical("KILL SWITCH ACTIVATED: %s", reason)

    def deactivate(self) -> None:
        """Deactivate the kill switch (manual resume)."""
        self._process_killed = False
        if os.path.exists(self.KILL_FILE):
            os.remove(self.KILL_FILE)
        logger.warning("Kill switch deactivated. Trading may resume.")

    def set_process_killed(self) -> None:
        """Called by signal handler for SIGTERM/SIGINT."""
        self._process_killed = True


def _write_kill_switch_file(reason: str) -> None:
    """Write the kill switch file (method 2)."""
    kill_path = Path(KillSwitch.KILL_FILE)
    kill_path.parent.mkdir(parents=True, exist_ok=True)
    with open(kill_path, "w") as f:
        f.write(json.dumps({
            "activated_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        }))
