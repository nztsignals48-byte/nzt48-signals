"""
NZT-48 Trading System — Session Boundary Manager
Manages critical transition points during the trading day: market open,
lunch, close. Prevents the system from making bad entries/exits at these
boundary moments where alpha is lowest or execution risk is highest.

Session Phases (all times Eastern):
    PRE_MARKET:     04:00 - 09:25  (warm-up, no entries)
    OPEN_AUCTION:   09:25 - 09:35  (opening volatility, no new entries)
    MORNING_EDGE:   09:35 - 11:30  (highest alpha, full size)
    LUNCH_DEAD:     11:30 - 13:30  (lowest alpha, reduce size 50%, tighten filters)
    AFTERNOON:      13:30 - 15:25  (moderate alpha, standard size)
    CLOSE_WINDOW:   15:25 - 15:50  (closing mechanics, exit-only or reduce new entries 75%)
    MOC_PERIOD:     15:50 - 16:00  (market-on-close only, NO new entries)
    AFTER_HOURS:    16:00 - 20:00  (position audit, no new entries)

Fatigue Model:
    Trades 1-10:   normal sizing
    Trades 11-15:  reduce size by 15% per trade beyond 10
    Trades 16+:    suggest stopping for the day

Force-Close Rules:
    - Underwater ETP positions after 15:30 (leveraged decay/rebalance risk)
    - Any position open >6 hours with negative P&L (dead money)

Pre-Close Audit (15:25 ET):
    - P&L < -0.5R AND held >2h    → CLOSE (losing trade, don't carry overnight)
    - ETP AND time > 15:30        → CLOSE (rebalance risk window)
    - Illiquidity score < 40      → CLOSE (spreads widen at close)
    - P&L > +1R                   → TRAIL TIGHTER (protect gains for close)
    - Held <30 min AND flat       → HOLD (give it time)

Post-Open Warmup (09:35 ET):
    - Gap > +1% + volume normal   → gap_and_go
    - Gap > +1% + volume low      → gap_and_fade
    - Gap < -1%                   → gap_down (defensive 30 min)
    - Gap < 0.3%                  → flat_open (normal trading)
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.session_manager")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

from core.clock import ET_TZ as ET, UK_TZ as UK

# Session phase definitions: (name, start_time, end_time)
# Order matters — checked top-to-bottom; first match wins.
_PHASES = [
    ("PRE_MARKET",    time(4, 0),   time(9, 25)),
    ("OPEN_AUCTION",  time(9, 25),  time(9, 35)),
    ("MORNING_EDGE",  time(9, 35),  time(11, 30)),
    ("LUNCH_DEAD",    time(11, 30), time(13, 30)),
    ("AFTERNOON",     time(13, 30), time(15, 25)),
    ("CLOSE_WINDOW",  time(15, 25), time(15, 50)),
    ("MOC_PERIOD",    time(15, 50), time(16, 0)),
    ("AFTER_HOURS",   time(16, 0),  time(20, 0)),
]

# LSE session phase definitions (UK time)
# LSE official hours: 08:00-16:30 GMT, 2-min intraday auction break at 12:00-12:02
_LSE_PHASES = [
    ("LSE_PRE_OPEN",    time(7, 0),   time(8, 0)),
    ("LSE_OPEN_AUCTION", time(8, 0),  time(8, 5)),
    ("LSE_MORNING",     time(8, 5),   time(12, 0)),
    ("LSE_LUNCH",       time(12, 0),  time(13, 0)),
    ("LSE_AFTERNOON",   time(13, 0),  time(16, 25)),
    ("LSE_CLOSE",       time(16, 25), time(16, 30)),
    ("LSE_AFTER_HOURS", time(16, 30), time(20, 0)),
]

_LSE_PHASE_META: dict[str, dict] = {
    "LSE_PRE_OPEN": {
        "size_multiplier": 0.0,
        "allow_new_entries": False,
        "message": "LSE pre-open. Data collection only.",
    },
    "LSE_OPEN_AUCTION": {
        "size_multiplier": 0.0,
        "allow_new_entries": False,
        "message": "LSE opening auction. Wait for price discovery.",
    },
    "LSE_MORNING": {
        "size_multiplier": 1.0,
        "allow_new_entries": True,
        "message": "LSE morning session. Highest alpha for ISA ETPs. Full size.",
    },
    "LSE_LUNCH": {
        "size_multiplier": 0.5,
        "allow_new_entries": True,
        "message": "LSE lunch. Reduced liquidity. Size at 50%, confidence > 80.",
    },
    "LSE_AFTERNOON": {
        "size_multiplier": 1.0,
        "allow_new_entries": True,
        "message": "LSE afternoon. Standard size. US overlap from 14:30. Entries allowed until 16:25.",
    },
    "LSE_CLOSE": {
        "size_multiplier": 0.0,
        "allow_new_entries": False,
        "message": "LSE closing auction 16:25-16:30. Exit-only. No new entries.",
    },
    "LSE_AFTER_HOURS": {
        "size_multiplier": 0.0,
        "allow_new_entries": False,
        "message": "LSE after hours from 16:30. Market closed.",
    },
}

# Phase metadata: size_multiplier, allow_new_entries, description
_PHASE_META: dict[str, dict] = {
    "PRE_MARKET": {
        "size_multiplier": 0.0,
        "allow_new_entries": False,
        "message": "Pre-market warm-up. Data collection only — no entries.",
    },
    "OPEN_AUCTION": {
        "size_multiplier": 0.0,
        "allow_new_entries": False,
        "message": "Opening auction volatility. Wait for price discovery to complete.",
    },
    "MORNING_EDGE": {
        "size_multiplier": 1.0,
        "allow_new_entries": True,
        "message": "Highest alpha window. Full size, all strategies active.",
    },
    "LUNCH_DEAD": {
        "size_multiplier": 0.5,
        "allow_new_entries": True,  # Allowed but with tighter filters (conf > 80)
        "message": "Lunch dead zone. Size reduced 50%, confidence > 80 required.",
    },
    "AFTERNOON": {
        "size_multiplier": 1.0,
        "allow_new_entries": True,
        "message": "Afternoon session. Standard size, moderate alpha.",
    },
    "CLOSE_WINDOW": {
        "size_multiplier": 0.25,
        "allow_new_entries": False,  # Exit-only; size reduced 75% for exceptional entries
        "message": "Close window. Exit-only mode. Reduce new entries 75%.",
    },
    "MOC_PERIOD": {
        "size_multiplier": 0.0,
        "allow_new_entries": False,
        "message": "Market-on-close period. NO new entries. MOC orders only.",
    },
    "AFTER_HOURS": {
        "size_multiplier": 0.0,
        "allow_new_entries": False,
        "message": "After hours. Position audit mode — no new entries.",
    },
}

# Fatigue thresholds
_FATIGUE_NORMAL_LIMIT = 10       # Full size for first 10 trades
_FATIGUE_REDUCE_PER_TRADE = 0.15 # 15% reduction per trade beyond 10
_FATIGUE_STOP_THRESHOLD = 15     # Suggest stopping after 15 trades

# Force-close thresholds
_ETP_FORCE_CLOSE_TIME = time(15, 30)
_MAX_HOLD_HOURS_NEGATIVE = 6     # Force close after 6h with negative P&L

# Phase that is considered "outside session"
_OVERNIGHT_PHASE = "OVERNIGHT"


class SessionBoundaryManager:
    """Manages session state across the trading day.

    Enforces rules at key transition points (open, lunch, close) to
    prevent the system from making bad entries or exits when alpha is
    lowest or execution risk is highest.

    All times use Eastern Time (America/New_York) via zoneinfo.

    Usage::

        mgr = SessionBoundaryManager()
        phase = mgr.get_current_phase()
        allowed, reason = mgr.should_allow_entry(signal_confidence=72)
        audit = mgr.run_pre_close_audit(open_positions)
        warmup = mgr.run_post_open_warmup(price_data)
        summary = mgr.run_end_of_day_summary(trades_today, equity)
    """

    def __init__(self) -> None:
        self._trades_today: int = 0
        self._phase_transitions: list[dict] = []
        self._last_phase: str = ""
        self._regime_transitions: list[dict] = []
        self._daily_reset_done: bool = False
        logger.info("SessionBoundaryManager initialised")

    # ------------------------------------------------------------------
    # 1. Current Phase
    # ------------------------------------------------------------------

    def get_current_phase(self, now: datetime | None = None) -> dict:
        """Return the current session phase with all metadata.

        Args:
            now: Override current time (for testing). Must be timezone-aware
                 or will be treated as ET. Defaults to current ET time.

        Returns:
            Dict with keys: phase, size_multiplier, allow_new_entries,
            minutes_until_next_phase, message, fatigue_multiplier,
            effective_size_multiplier, trades_today, fatigue_level.
        """
        et_now = self._to_et(now)
        et_time = et_now.time()

        phase_name = _OVERNIGHT_PHASE
        next_phase_start: time | None = None

        for i, (name, start, end) in enumerate(_PHASES):
            if start <= et_time < end:
                phase_name = name
                # Next phase start is the end of this phase
                if i + 1 < len(_PHASES):
                    next_phase_start = _PHASES[i + 1][1]
                else:
                    next_phase_start = None
                break

        # Handle overnight (before 04:00 or after 20:00)
        if phase_name == _OVERNIGHT_PHASE:
            meta = {
                "size_multiplier": 0.0,
                "allow_new_entries": False,
                "message": "Overnight. Markets closed — no activity.",
            }
            # Next phase is PRE_MARKET at 04:00 (either today or tomorrow)
            if et_time >= time(20, 0):
                next_dt = (et_now + timedelta(days=1)).replace(
                    hour=4, minute=0, second=0, microsecond=0
                )
            else:
                next_dt = et_now.replace(hour=4, minute=0, second=0, microsecond=0)
            minutes_until_next = int((next_dt - et_now).total_seconds() / 60)
        else:
            meta = _PHASE_META[phase_name]
            if next_phase_start is not None:
                next_dt = et_now.replace(
                    hour=next_phase_start.hour,
                    minute=next_phase_start.minute,
                    second=0, microsecond=0,
                )
                minutes_until_next = max(0, int((next_dt - et_now).total_seconds() / 60))
            else:
                # Last phase (AFTER_HOURS) — next is overnight at 20:00
                next_dt = et_now.replace(hour=20, minute=0, second=0, microsecond=0)
                minutes_until_next = max(0, int((next_dt - et_now).total_seconds() / 60))

        # Track phase transitions
        if phase_name != self._last_phase:
            if self._last_phase:
                transition = {
                    "from": self._last_phase,
                    "to": phase_name,
                    "time": et_now.isoformat(),
                    "trades_at_transition": self._trades_today,
                    "fatigue_level": self._get_fatigue_level(),
                }
                self._phase_transitions.append(transition)
                logger.info(
                    "Phase transition: %s -> %s at %s (trades=%d, fatigue=%s)",
                    self._last_phase, phase_name,
                    et_now.strftime("%H:%M:%S"),
                    self._trades_today, self._get_fatigue_level(),
                )
            self._last_phase = phase_name

        # Fatigue adjustment
        fatigue_mult = self._get_fatigue_multiplier()

        return {
            "phase": phase_name,
            "size_multiplier": meta["size_multiplier"],
            "allow_new_entries": meta["allow_new_entries"],
            "minutes_until_next_phase": minutes_until_next,
            "message": meta["message"],
            "fatigue_multiplier": round(fatigue_mult, 2),
            "effective_size_multiplier": round(
                meta["size_multiplier"] * fatigue_mult, 2
            ),
            "trades_today": self._trades_today,
            "fatigue_level": self._get_fatigue_level(),
        }

    # ------------------------------------------------------------------
    # 1b. Current LSE Phase
    # ------------------------------------------------------------------

    def get_current_lse_phase(self, now: datetime | None = None) -> dict:
        """Return the current LSE session phase with all metadata.

        Uses UK time instead of US Eastern time.
        """
        if now is None:
            uk_now = datetime.now(UK)
        elif now.tzinfo is None:
            uk_now = now.replace(tzinfo=UK)
        else:
            uk_now = now.astimezone(UK)

        uk_time = uk_now.time()

        phase_name = _OVERNIGHT_PHASE
        next_phase_start = None

        for i, (name, start, end) in enumerate(_LSE_PHASES):
            if start <= uk_time < end:
                phase_name = name
                if i + 1 < len(_LSE_PHASES):
                    next_phase_start = _LSE_PHASES[i + 1][1]
                break

        if phase_name == _OVERNIGHT_PHASE:
            meta = {
                "size_multiplier": 0.0,
                "allow_new_entries": False,
                "message": "LSE overnight. Markets closed.",
            }
            minutes_until_next = 0
        else:
            meta = _LSE_PHASE_META[phase_name]
            if next_phase_start is not None:
                next_dt = uk_now.replace(
                    hour=next_phase_start.hour,
                    minute=next_phase_start.minute,
                    second=0, microsecond=0,
                )
                minutes_until_next = max(0, int((next_dt - uk_now).total_seconds() / 60))
            else:
                minutes_until_next = 0

        fatigue_mult = self._get_fatigue_multiplier()

        return {
            "phase": phase_name,
            "size_multiplier": meta["size_multiplier"],
            "allow_new_entries": meta["allow_new_entries"],
            "minutes_until_next_phase": minutes_until_next,
            "message": meta["message"],
            "fatigue_multiplier": round(fatigue_mult, 2),
            "effective_size_multiplier": round(
                meta["size_multiplier"] * fatigue_mult, 2
            ),
            "trades_today": self._trades_today,
            "fatigue_level": self._get_fatigue_level(),
            "market": "LSE",
        }

    # ------------------------------------------------------------------
    # 2. Entry Filter
    # ------------------------------------------------------------------

    def should_allow_entry(
        self,
        signal_confidence: int,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        """Phase-aware entry filter.

        Rules:
        - PRE_MARKET, OPEN_AUCTION, MOC_PERIOD, AFTER_HOURS, OVERNIGHT:
          No entries allowed.
        - MORNING_EDGE, AFTERNOON: All entries allowed.
        - LUNCH_DEAD: Requires confidence > 80.
        - CLOSE_WINDOW: Exit-only. No new entries (only exceptional with
          confidence > 90 get through at 25% size).
        - Fatigue: After 15 trades, suggest stopping.

        Args:
            signal_confidence: Signal confidence score (0-100).
            now: Override current time for testing.

        Returns:
            Tuple of (allowed: bool, reason: str).
        """
        phase_info = self.get_current_phase(now)
        phase = phase_info["phase"]

        # Fatigue hard block
        if self._trades_today >= _FATIGUE_STOP_THRESHOLD:
            return (
                False,
                f"Fatigue block: {self._trades_today} trades today "
                f"(limit {_FATIGUE_STOP_THRESHOLD}). Stop trading.",
            )

        # Phase-specific rules
        if phase in ("PRE_MARKET", "OPEN_AUCTION", "MOC_PERIOD",
                      "AFTER_HOURS", _OVERNIGHT_PHASE):
            return (
                False,
                f"Phase {phase}: no new entries allowed. "
                f"{phase_info['message']}",
            )

        if phase == "MORNING_EDGE":
            self._trades_today += 1
            return (
                True,
                f"MORNING_EDGE: full size, full alpha. "
                f"Confidence {signal_confidence}. "
                f"Trade #{self._trades_today} today.",
            )

        if phase == "LUNCH_DEAD":
            if signal_confidence > 80:
                self._trades_today += 1
                return (
                    True,
                    f"LUNCH_DEAD: entry allowed (confidence {signal_confidence} > 80). "
                    f"Size reduced to 50%. Trade #{self._trades_today} today.",
                )
            return (
                False,
                f"LUNCH_DEAD: confidence {signal_confidence} <= 80. "
                f"Need > 80 during lunch dead zone.",
            )

        if phase == "AFTERNOON":
            self._trades_today += 1
            return (
                True,
                f"AFTERNOON: standard entry. Confidence {signal_confidence}. "
                f"Trade #{self._trades_today} today.",
            )

        if phase == "CLOSE_WINDOW":
            if signal_confidence > 90:
                self._trades_today += 1
                return (
                    True,
                    f"CLOSE_WINDOW: exceptional entry allowed "
                    f"(confidence {signal_confidence} > 90). "
                    f"Size at 25%. Trade #{self._trades_today} today.",
                )
            return (
                False,
                f"CLOSE_WINDOW: exit-only mode. "
                f"Confidence {signal_confidence} <= 90. "
                f"Only confidence > 90 entries at 25% size.",
            )

        # Fallback: block unknown phases
        return (False, f"Unknown phase {phase}: entry blocked for safety.")

    # ------------------------------------------------------------------
    # 2b. Ticker-Aware Entry Filters (LSE / US auto-detect)
    # ------------------------------------------------------------------

    def get_phase_for_ticker(self, ticker: str, now: datetime | None = None) -> dict:
        """Auto-detect market from ticker suffix and return appropriate phase.

        .L suffix -> LSE phases (UK time)
        Otherwise -> US phases (ET time)
        """
        if ticker and ticker.endswith('.L'):
            return self.get_current_lse_phase(now)
        return self.get_current_phase(now)

    def should_allow_entry_for_ticker(
        self,
        ticker: str,
        signal_confidence: int,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        """Phase-aware entry filter that auto-detects market from ticker.

        For .L tickers: uses LSE phases
        For US tickers: uses existing US phases
        """
        if ticker and ticker.endswith('.L'):
            phase_info = self.get_current_lse_phase(now)
            phase = phase_info["phase"]

            if phase in ("LSE_PRE_OPEN", "LSE_OPEN_AUCTION", "LSE_CLOSE",
                        "LSE_AFTER_HOURS", _OVERNIGHT_PHASE):
                return (False, f"LSE Phase {phase}: no new entries. {phase_info['message']}")

            if phase == "LSE_MORNING":
                self._trades_today += 1
                return (True, f"LSE_MORNING: full size. Confidence {signal_confidence}. Trade #{self._trades_today}.")

            if phase == "LSE_LUNCH":
                if signal_confidence > 80:
                    self._trades_today += 1
                    return (True, f"LSE_LUNCH: entry allowed (confidence {signal_confidence} > 80). 50% size.")
                return (False, f"LSE_LUNCH: confidence {signal_confidence} <= 80. Need > 80 during lunch.")

            if phase == "LSE_AFTERNOON":
                self._trades_today += 1
                return (True, f"LSE_AFTERNOON: standard entry. Confidence {signal_confidence}. Trade #{self._trades_today}.")

            return (False, f"Unknown LSE phase {phase}: entry blocked.")

        # Fallback to existing US logic
        return self.should_allow_entry(signal_confidence, now)

    # ------------------------------------------------------------------
    # 3. Pre-Close Audit
    # ------------------------------------------------------------------

    def run_pre_close_audit(
        self,
        open_positions: list[dict],
        now: datetime | None = None,
    ) -> list[dict]:
        """Audit all open positions at 15:25 ET.

        Rules:
        - P&L < -0.5R AND held >2 hours     -> CLOSE
        - ETP position AND time > 15:30      -> CLOSE
        - Illiquidity score < 40             -> CLOSE
        - P&L > +1R                          -> TRAIL TIGHTER
        - Held <30 min AND P&L near flat     -> HOLD

        Args:
            open_positions: List of position dicts. Expected keys:
                ticker, bot (str "A" or "B"), pnl_r (float),
                entry_time (ISO string or datetime), unrealised_pnl (float),
                liquidity_score (float, 0-100, optional).
            now: Override current time for testing.

        Returns:
            List of audit action dicts with keys: ticker, action,
            reason, priority.
        """
        et_now = self._to_et(now)
        actions: list[dict] = []

        logger.info(
            "Running pre-close audit at %s with %d open positions",
            et_now.strftime("%H:%M:%S"), len(open_positions),
        )

        for pos in open_positions:
            ticker = pos.get("ticker", "UNKNOWN")
            bot = pos.get("bot", "B")
            pnl_r = pos.get("pnl_r", 0.0)
            entry_time = pos.get("entry_time")
            liquidity_score = pos.get("liquidity_score", 100.0)

            # Parse entry_time
            if isinstance(entry_time, str):
                try:
                    entry_dt = datetime.fromisoformat(entry_time)
                    if entry_dt.tzinfo is None:
                        entry_dt = entry_dt.replace(tzinfo=ET)
                except (ValueError, TypeError):
                    entry_dt = et_now - timedelta(hours=1)  # Default to 1h ago
            elif isinstance(entry_time, datetime):
                entry_dt = entry_time
                if entry_dt.tzinfo is None:
                    entry_dt = entry_dt.replace(tzinfo=ET)
            else:
                entry_dt = et_now - timedelta(hours=1)

            hold_minutes = (et_now - entry_dt).total_seconds() / 60

            # Rule 5: Held <30 min AND P&L near flat -> HOLD (check first, highest priority for new trades)
            if hold_minutes < 30 and abs(pnl_r) < 0.3:
                actions.append({
                    "ticker": ticker,
                    "action": "HOLD",
                    "reason": (
                        f"Position held only {int(hold_minutes)} min with "
                        f"P&L {pnl_r:+.2f}R (near flat). Give it time."
                    ),
                    "priority": "LOW",
                })
                continue

            # Rule 1: P&L < -0.5R AND held >2 hours -> CLOSE
            if pnl_r < -0.5 and hold_minutes > 120:
                actions.append({
                    "ticker": ticker,
                    "action": "CLOSE",
                    "reason": (
                        f"Underwater {pnl_r:+.2f}R for {int(hold_minutes)} min "
                        f"(>{120} min). Don't carry overnight."
                    ),
                    "priority": "HIGH",
                })
                continue

            # Rule 2: ETP position AND time > 15:30 -> CLOSE
            if bot == "A" and et_now.time() > _ETP_FORCE_CLOSE_TIME:
                actions.append({
                    "ticker": ticker,
                    "action": "CLOSE",
                    "reason": (
                        "ETP position (Bot A) past 15:30 ET. "
                        "Rebalance risk window — close before spread widens."
                    ),
                    "priority": "HIGH",
                })
                continue

            # Rule 3: Illiquidity score < 40 -> CLOSE
            if liquidity_score < 40:
                actions.append({
                    "ticker": ticker,
                    "action": "CLOSE",
                    "reason": (
                        f"Liquidity score {liquidity_score:.0f} < 40. "
                        f"Spreads widen at close — exit now."
                    ),
                    "priority": "HIGH",
                })
                continue

            # Rule 4: P&L > +1R -> TRAIL TIGHTER
            if pnl_r > 1.0:
                actions.append({
                    "ticker": ticker,
                    "action": "TRAIL_TIGHTER",
                    "reason": (
                        f"Profit {pnl_r:+.2f}R. Tighten trailing stop "
                        f"to protect gains into close."
                    ),
                    "priority": "MEDIUM",
                })
                continue

            # Default: no action needed
            actions.append({
                "ticker": ticker,
                "action": "HOLD",
                "reason": "No pre-close action required.",
                "priority": "LOW",
            })

        logger.info(
            "Pre-close audit complete: %d actions (%d CLOSE, %d TRAIL, %d HOLD)",
            len(actions),
            sum(1 for a in actions if a["action"] == "CLOSE"),
            sum(1 for a in actions if a["action"] == "TRAIL_TIGHTER"),
            sum(1 for a in actions if a["action"] == "HOLD"),
        )

        return actions

    # ------------------------------------------------------------------
    # 4. Post-Open Warmup
    # ------------------------------------------------------------------

    def run_post_open_warmup(self, price_data: dict) -> dict:
        """Analyze first 5 minutes of trading at 09:35 ET.

        Evaluates the opening gap direction and volume quality to set
        the recommended trading bias for the morning session.

        Args:
            price_data: Dict with keys:
                previous_close (float), current_price (float),
                current_volume (int/float), average_volume_5min (int/float),
                high_5min (float), low_5min (float).

        Returns:
            Dict with keys: gap_direction, gap_pct, initial_volume_quality,
            opening_range, opening_range_pct, recommended_bias, flags.
        """
        prev_close = price_data.get("previous_close", 0.0)
        current = price_data.get("current_price", 0.0)
        volume = price_data.get("current_volume", 0)
        avg_vol = price_data.get("average_volume_5min", 1)
        high_5m = price_data.get("high_5min", current)
        low_5m = price_data.get("low_5min", current)

        # Gap calculation
        gap_pct = ((current - prev_close) / prev_close * 100) if prev_close > 0 else 0.0

        # Volume quality (current vs average for first 5 min)
        vol_ratio = (volume / avg_vol * 100) if avg_vol > 0 else 0.0

        # Opening range
        opening_range = high_5m - low_5m
        opening_range_pct = (opening_range / prev_close * 100) if prev_close > 0 else 0.0

        # Classification
        flags: list[str] = []
        if gap_pct > 1.0:
            if vol_ratio >= 50.0:
                gap_direction = "GAP_UP"
                recommended_bias = "BULLISH"
                flags.append("gap_and_go potential — watch for continuation above opening range")
            else:
                gap_direction = "GAP_UP"
                recommended_bias = "CAUTIOUS"
                flags.append(
                    f"gap_and_fade likely — gap +{gap_pct:.1f}% but volume "
                    f"only {vol_ratio:.0f}% of average (need >50%)"
                )
        elif gap_pct < -1.0:
            gap_direction = "GAP_DOWN"
            recommended_bias = "DEFENSIVE"
            flags.append("gap_down — defensive mode first 30 min, watch for reversal or continuation")
        elif abs(gap_pct) < 0.3:
            gap_direction = "FLAT"
            recommended_bias = "NEUTRAL"
            flags.append("flat_open — normal trading, wait for direction to establish")
        else:
            # Small gap (0.3% - 1.0%)
            gap_direction = "GAP_UP" if gap_pct > 0 else "GAP_DOWN"
            recommended_bias = "NEUTRAL"
            flags.append(
                f"small gap {gap_pct:+.1f}% — not enough for directional bias, "
                f"trade normally"
            )

        # Volume quality label
        if vol_ratio >= 150:
            volume_quality = "HIGH"
            flags.append(f"Volume {vol_ratio:.0f}% of average — strong participation")
        elif vol_ratio >= 80:
            volume_quality = "NORMAL"
        elif vol_ratio >= 50:
            volume_quality = "BELOW_AVERAGE"
            flags.append(f"Volume {vol_ratio:.0f}% of average — below normal, be cautious on breakouts")
        else:
            volume_quality = "LOW"
            flags.append(f"Volume {vol_ratio:.0f}% of average — very low, avoid breakout trades")

        result = {
            "gap_direction": gap_direction,
            "gap_pct": round(gap_pct, 2),
            "initial_volume_quality": volume_quality,
            "volume_ratio_pct": round(vol_ratio, 1),
            "opening_range": round(opening_range, 4),
            "opening_range_pct": round(opening_range_pct, 2),
            "recommended_bias": recommended_bias,
            "flags": flags,
        }

        logger.info(
            "Post-open warmup: gap=%s %.2f%%, volume=%s (%.0f%%), "
            "OR=%.4f (%.2f%%), bias=%s",
            gap_direction, gap_pct, volume_quality, vol_ratio,
            opening_range, opening_range_pct, recommended_bias,
        )

        return result

    # ------------------------------------------------------------------
    # 5. End-of-Day Summary
    # ------------------------------------------------------------------

    def run_end_of_day_summary(
        self,
        trades_today: list[dict],
        equity: float,
    ) -> dict:
        """Compile end-of-day summary at 16:05 ET.

        Args:
            trades_today: List of trade dicts. Expected keys:
                net_pnl (float), r_multiple (float), strategy (str),
                ticker (str), direction (str).
            equity: Current account equity after today's trades.

        Returns:
            Dict with keys: total_pnl, win_rate, best_trade, worst_trade,
            total_trades, winners, losers, avg_r, strategies_used,
            regime_transitions, fatigue_level, equity, phase_transitions.
        """
        if not trades_today:
            return {
                "total_pnl": 0.0,
                "win_rate": 0.0,
                "best_trade": None,
                "worst_trade": None,
                "total_trades": 0,
                "winners": 0,
                "losers": 0,
                "avg_r": 0.0,
                "strategies_used": [],
                "regime_transitions": list(self._regime_transitions),
                "fatigue_level": self._get_fatigue_level(),
                "equity": equity,
                "phase_transitions": list(self._phase_transitions),
            }

        total_pnl = sum(t.get("net_pnl", 0.0) for t in trades_today)
        r_multiples = [t.get("r_multiple", 0.0) for t in trades_today]
        winners = [t for t in trades_today if t.get("net_pnl", 0.0) > 0]
        losers = [t for t in trades_today if t.get("net_pnl", 0.0) <= 0]
        win_rate = (len(winners) / len(trades_today) * 100) if trades_today else 0.0

        # Best and worst trades
        best = max(trades_today, key=lambda t: t.get("net_pnl", 0.0))
        worst = min(trades_today, key=lambda t: t.get("net_pnl", 0.0))

        best_trade = {
            "ticker": best.get("ticker", ""),
            "net_pnl": best.get("net_pnl", 0.0),
            "r_multiple": best.get("r_multiple", 0.0),
            "strategy": best.get("strategy", ""),
            "direction": best.get("direction", ""),
        }
        worst_trade = {
            "ticker": worst.get("ticker", ""),
            "net_pnl": worst.get("net_pnl", 0.0),
            "r_multiple": worst.get("r_multiple", 0.0),
            "strategy": worst.get("strategy", ""),
            "direction": worst.get("direction", ""),
        }

        # Strategies used
        strategies_used = sorted(set(
            t.get("strategy", "UNKNOWN") for t in trades_today
        ))

        avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0.0

        summary = {
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(win_rate, 1),
            "best_trade": best_trade,
            "worst_trade": worst_trade,
            "total_trades": len(trades_today),
            "winners": len(winners),
            "losers": len(losers),
            "avg_r": round(avg_r, 2),
            "strategies_used": strategies_used,
            "regime_transitions": list(self._regime_transitions),
            "fatigue_level": self._get_fatigue_level(),
            "equity": round(equity, 2),
            "phase_transitions": list(self._phase_transitions),
        }

        logger.info(
            "EOD Summary: PnL=$%.2f | W/L=%d/%d (%.1f%%) | "
            "Avg R=%.2f | Trades=%d | Fatigue=%s",
            total_pnl, len(winners), len(losers), win_rate,
            avg_r, len(trades_today), self._get_fatigue_level(),
        )

        return summary

    # ------------------------------------------------------------------
    # 6. Force-Close Logic
    # ------------------------------------------------------------------

    def should_force_close(
        self,
        position: dict,
        current_time: datetime | None = None,
    ) -> tuple[bool, str]:
        """Determine whether a position must be force-closed.

        Rules:
        1. Underwater ETP positions after 15:30 ET (leveraged decay risk
           during daily rebalance window).
        2. Any position open >6 hours with negative P&L (dead money).

        Args:
            position: Dict with keys: bot (str "A" or "B"),
                entry_time (ISO string or datetime), pnl_r (float),
                ticker (str), unrealised_pnl (float).
            current_time: Override current time for testing.

        Returns:
            Tuple of (should_close: bool, reason: str).
        """
        et_now = self._to_et(current_time)
        bot = position.get("bot", "B")
        pnl_r = position.get("pnl_r", 0.0)
        unrealised_pnl = position.get("unrealised_pnl", 0.0)
        ticker = position.get("ticker", "UNKNOWN")
        entry_time = position.get("entry_time")

        # Parse entry_time
        if isinstance(entry_time, str):
            try:
                entry_dt = datetime.fromisoformat(entry_time)
                if entry_dt.tzinfo is None:
                    entry_dt = entry_dt.replace(tzinfo=ET)
            except (ValueError, TypeError):
                entry_dt = et_now - timedelta(hours=1)
        elif isinstance(entry_time, datetime):
            entry_dt = entry_time
            if entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=ET)
        else:
            entry_dt = et_now - timedelta(hours=1)

        hold_hours = (et_now - entry_dt).total_seconds() / 3600

        # Rule 1: Underwater ETP after 15:30
        if bot == "A" and et_now.time() >= _ETP_FORCE_CLOSE_TIME:
            if pnl_r < 0 or unrealised_pnl < 0:
                reason = (
                    f"FORCE CLOSE {ticker}: underwater ETP (Bot A) at "
                    f"{pnl_r:+.2f}R after 15:30 ET. Leveraged decay and "
                    f"rebalance risk — must exit before spread widens."
                )
                logger.warning(reason)
                return (True, reason)

        # Rule 2: Any position held >6 hours with negative P&L
        if hold_hours > _MAX_HOLD_HOURS_NEGATIVE:
            if pnl_r < 0 or unrealised_pnl < 0:
                reason = (
                    f"FORCE CLOSE {ticker}: held {hold_hours:.1f} hours "
                    f"with {pnl_r:+.2f}R. Dead money — exceeds "
                    f"{_MAX_HOLD_HOURS_NEGATIVE}h threshold."
                )
                logger.warning(reason)
                return (True, reason)

        return (False, f"{ticker}: no force-close required.")

    # ------------------------------------------------------------------
    # 7. Daily Schedule (APScheduler integration)
    # ------------------------------------------------------------------

    def get_daily_schedule(self, now: datetime | None = None) -> list[dict]:
        """Return the full schedule of events for the current trading day.

        Designed for APScheduler integration. Each event has a time,
        action name, and description.

        Args:
            now: Override current time for testing.

        Returns:
            List of event dicts with keys: time_et, action, description.
        """
        et_now = self._to_et(now)
        today = et_now.date()

        def _make_dt(h: int, m: int) -> datetime:
            return datetime(today.year, today.month, today.day, h, m, tzinfo=ET)

        schedule = [
            {
                "time_et": _make_dt(4, 0).isoformat(),
                "action": "pre_market_start",
                "description": "Pre-market data collection begins.",
            },
            {
                "time_et": _make_dt(9, 0).isoformat(),
                "action": "pre_market_brief",
                "description": "Generate and deliver pre-market intelligence brief.",
            },
            {
                "time_et": _make_dt(9, 25).isoformat(),
                "action": "open_auction_start",
                "description": "Opening auction — volatility window. No new entries.",
            },
            {
                "time_et": _make_dt(9, 35).isoformat(),
                "action": "post_open_warmup",
                "description": (
                    "Run post-open warmup analysis. Evaluate gap, volume, "
                    "opening range. Set morning bias."
                ),
            },
            {
                "time_et": _make_dt(11, 30).isoformat(),
                "action": "lunch_dead_start",
                "description": (
                    "Lunch dead zone begins. Reduce size 50%, "
                    "require confidence > 80 for new entries."
                ),
            },
            {
                "time_et": _make_dt(13, 30).isoformat(),
                "action": "afternoon_start",
                "description": "Afternoon session begins. Resume standard sizing.",
            },
            {
                "time_et": _make_dt(15, 25).isoformat(),
                "action": "pre_close_audit",
                "description": (
                    "Run pre-close audit on all open positions. "
                    "Flag underwater, illiquid, and profitable positions."
                ),
            },
            {
                "time_et": _make_dt(15, 30).isoformat(),
                "action": "etp_force_close_check",
                "description": (
                    "ETP force-close window opens. Close all underwater "
                    "Bot A positions (rebalance risk)."
                ),
            },
            {
                "time_et": _make_dt(15, 50).isoformat(),
                "action": "moc_period_start",
                "description": "MOC period. NO new entries. Market-on-close orders only.",
            },
            {
                "time_et": _make_dt(16, 0).isoformat(),
                "action": "market_close",
                "description": "Market close. Begin after-hours position audit.",
            },
            {
                "time_et": _make_dt(16, 5).isoformat(),
                "action": "end_of_day_summary",
                "description": (
                    "Compile end-of-day summary: P&L, win rate, "
                    "best/worst trades, fatigue level."
                ),
            },
            {
                "time_et": _make_dt(16, 30).isoformat(),
                "action": "daily_reset",
                "description": "Reset all daily state for next trading day.",
            },
        ]

        return schedule

    # ------------------------------------------------------------------
    # 8. Daily Reset
    # ------------------------------------------------------------------

    def reset_daily(self) -> None:
        """Reset all state for a new trading day.

        Called at end of day (typically 16:30 ET) to prepare for
        the next session.
        """
        prev_trades = self._trades_today
        prev_transitions = len(self._phase_transitions)

        self._trades_today = 0
        self._phase_transitions = []
        self._last_phase = ""
        self._regime_transitions = []
        self._daily_reset_done = True

        logger.info(
            "Daily reset complete. Previous day: %d trades, "
            "%d phase transitions.",
            prev_trades, prev_transitions,
        )

    # ------------------------------------------------------------------
    # 9. Dashboard Status
    # ------------------------------------------------------------------

    def get_status(self, now: datetime | None = None) -> dict:
        """Return full dashboard status.

        Args:
            now: Override current time for testing.

        Returns:
            Dict with current phase info, fatigue metrics, trade counts,
            phase transitions, and schedule.
        """
        phase = self.get_current_phase(now)

        return {
            "module": "SessionBoundaryManager",
            "current_phase": phase,
            "trades_today": self._trades_today,
            "fatigue_level": self._get_fatigue_level(),
            "fatigue_multiplier": self._get_fatigue_multiplier(),
            "phase_transitions": list(self._phase_transitions),
            "regime_transitions": list(self._regime_transitions),
            "daily_reset_done": self._daily_reset_done,
        }

    # ------------------------------------------------------------------
    # 10. Regime Transition Tracking
    # ------------------------------------------------------------------

    def record_regime_transition(
        self,
        from_regime: str,
        to_regime: str,
        now: datetime | None = None,
    ) -> None:
        """Record a regime transition for EOD summary.

        Args:
            from_regime: Previous regime state string.
            to_regime: New regime state string.
            now: Override current time for testing.
        """
        et_now = self._to_et(now)
        transition = {
            "from": from_regime,
            "to": to_regime,
            "time": et_now.isoformat(),
        }
        self._regime_transitions.append(transition)
        logger.info(
            "Regime transition recorded: %s -> %s at %s",
            from_regime, to_regime, et_now.strftime("%H:%M:%S"),
        )

    # ==================================================================
    # Private helpers
    # ==================================================================

    @staticmethod
    def _to_et(dt: datetime | None = None) -> datetime:
        """Convert a datetime to Eastern Time.

        If dt is None, returns current ET time.
        If dt is naive, assumes it is already in ET.
        If dt is timezone-aware, converts to ET.
        """
        if dt is None:
            return datetime.now(ET)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=ET)
        return dt.astimezone(ET)

    def _get_fatigue_multiplier(self) -> float:
        """Calculate size multiplier based on trade count fatigue.

        Trades 1-10:  1.0 (no reduction)
        Trades 11-15: -15% per trade beyond 10
        Trades 16+:   0.0 (blocked)
        """
        if self._trades_today <= _FATIGUE_NORMAL_LIMIT:
            return 1.0
        if self._trades_today > _FATIGUE_STOP_THRESHOLD:
            return 0.0
        excess = self._trades_today - _FATIGUE_NORMAL_LIMIT
        reduction = excess * _FATIGUE_REDUCE_PER_TRADE
        return max(0.0, round(1.0 - reduction, 2))

    def _get_fatigue_level(self) -> str:
        """Return a human-readable fatigue level."""
        if self._trades_today <= 5:
            return "FRESH"
        if self._trades_today <= _FATIGUE_NORMAL_LIMIT:
            return "NORMAL"
        if self._trades_today <= 13:
            return "ELEVATED"
        if self._trades_today <= _FATIGUE_STOP_THRESHOLD:
            return "HIGH"
        return "EXHAUSTED"


# ---------------------------------------------------------------------------
# Module-level convenience singleton
# ---------------------------------------------------------------------------

_default_manager: SessionBoundaryManager | None = None


def get_session_manager() -> SessionBoundaryManager:
    """Get or create the default SessionBoundaryManager singleton."""
    global _default_manager
    if _default_manager is None:
        _default_manager = SessionBoundaryManager()
    return _default_manager


# ---------------------------------------------------------------------------
# Self-test: Simulate a full trading day
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json as _json

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    print("=" * 72)
    print("NZT-48 Session Boundary Manager — Full Day Simulation")
    print("=" * 72)

    mgr = SessionBoundaryManager()

    # Use a fixed date for reproducible simulation
    sim_date = datetime.now(ET).date()

    def sim_time(h: int, m: int) -> datetime:
        return datetime(sim_date.year, sim_date.month, sim_date.day, h, m, tzinfo=ET)

    # ---- 1. Walk through every phase ----
    print("\n--- Phase Walk-Through ---")
    test_times = [
        (3, 0, "Overnight"),
        (4, 30, "Pre-market"),
        (9, 27, "Open auction"),
        (9, 40, "Morning edge"),
        (12, 0, "Lunch dead"),
        (14, 0, "Afternoon"),
        (15, 30, "Close window"),
        (15, 55, "MOC period"),
        (17, 0, "After hours"),
        (21, 0, "Overnight again"),
    ]
    for h, m, label in test_times:
        phase = mgr.get_current_phase(sim_time(h, m))
        print(
            f"  {label:20s} ({h:02d}:{m:02d}) -> {phase['phase']:15s} | "
            f"size={phase['effective_size_multiplier']:.2f} | "
            f"entry={phase['allow_new_entries']} | "
            f"next in {phase['minutes_until_next_phase']}min"
        )

    # ---- 2. Entry filter tests ----
    print("\n--- Entry Filter Tests ---")
    entry_tests = [
        (9, 40, 70, "Morning, conf 70"),
        (9, 40, 50, "Morning, conf 50"),
        (12, 15, 75, "Lunch, conf 75"),
        (12, 15, 85, "Lunch, conf 85"),
        (14, 30, 60, "Afternoon, conf 60"),
        (15, 30, 70, "Close window, conf 70"),
        (15, 30, 92, "Close window, conf 92"),
        (15, 55, 95, "MOC period, conf 95"),
    ]
    for h, m, conf, label in entry_tests:
        allowed, reason = mgr.should_allow_entry(conf, sim_time(h, m))
        status = "ALLOWED" if allowed else "BLOCKED"
        print(f"  {label:30s} -> {status:7s} | {reason[:80]}")

    # ---- 3. Post-open warmup ----
    print("\n--- Post-Open Warmup ---")
    warmup_scenarios = [
        {
            "name": "Gap-and-go",
            "data": {
                "previous_close": 150.0,
                "current_price": 152.5,
                "current_volume": 8_000_000,
                "average_volume_5min": 6_000_000,
                "high_5min": 153.0,
                "low_5min": 151.8,
            },
        },
        {
            "name": "Gap-and-fade (low vol)",
            "data": {
                "previous_close": 150.0,
                "current_price": 152.5,
                "current_volume": 2_000_000,
                "average_volume_5min": 6_000_000,
                "high_5min": 153.0,
                "low_5min": 151.8,
            },
        },
        {
            "name": "Gap down",
            "data": {
                "previous_close": 150.0,
                "current_price": 147.0,
                "current_volume": 10_000_000,
                "average_volume_5min": 6_000_000,
                "high_5min": 148.0,
                "low_5min": 146.5,
            },
        },
        {
            "name": "Flat open",
            "data": {
                "previous_close": 150.0,
                "current_price": 150.3,
                "current_volume": 5_500_000,
                "average_volume_5min": 6_000_000,
                "high_5min": 150.8,
                "low_5min": 149.9,
            },
        },
    ]
    for scenario in warmup_scenarios:
        result = mgr.run_post_open_warmup(scenario["data"])
        print(
            f"  {scenario['name']:25s} -> gap={result['gap_pct']:+.2f}% "
            f"vol={result['initial_volume_quality']:5s} "
            f"bias={result['recommended_bias']}"
        )
        for flag in result["flags"]:
            print(f"    FLAG: {flag}")

    # ---- 4. Pre-close audit ----
    print("\n--- Pre-Close Audit (simulated 15:35 ET) ---")
    positions = [
        {
            "ticker": "NVDA",
            "bot": "B",
            "pnl_r": -0.8,
            "entry_time": sim_time(10, 0).isoformat(),
            "unrealised_pnl": -45.0,
            "liquidity_score": 95.0,
        },
        {
            "ticker": "3QQQ",
            "bot": "A",
            "pnl_r": 0.5,
            "entry_time": sim_time(11, 0).isoformat(),
            "unrealised_pnl": 30.0,
            "liquidity_score": 75.0,
        },
        {
            "ticker": "SMCI",
            "bot": "B",
            "pnl_r": 0.1,
            "entry_time": sim_time(15, 15).isoformat(),
            "unrealised_pnl": 5.0,
            "liquidity_score": 35.0,
        },
        {
            "ticker": "AAPL",
            "bot": "B",
            "pnl_r": 1.8,
            "entry_time": sim_time(9, 45).isoformat(),
            "unrealised_pnl": 120.0,
            "liquidity_score": 98.0,
        },
        {
            "ticker": "TSLA",
            "bot": "B",
            "pnl_r": 0.05,
            "entry_time": sim_time(15, 20).isoformat(),
            "unrealised_pnl": 2.0,
            "liquidity_score": 90.0,
        },
    ]
    audit = mgr.run_pre_close_audit(positions, sim_time(15, 35))
    for action in audit:
        print(
            f"  {action['ticker']:6s} -> {action['action']:15s} "
            f"[{action['priority']:6s}] {action['reason'][:70]}"
        )

    # ---- 5. Force-close checks ----
    print("\n--- Force-Close Checks ---")
    force_tests = [
        {
            "name": "Underwater ETP at 15:35",
            "pos": {"ticker": "3QQQ", "bot": "A", "pnl_r": -0.3,
                    "entry_time": sim_time(10, 0).isoformat(),
                    "unrealised_pnl": -20.0},
            "time": sim_time(15, 35),
        },
        {
            "name": "6h+ hold, negative P&L",
            "pos": {"ticker": "AMD", "bot": "B", "pnl_r": -0.2,
                    "entry_time": sim_time(9, 40).isoformat(),
                    "unrealised_pnl": -15.0},
            "time": sim_time(15, 50),
        },
        {
            "name": "Profitable ETP at 15:35",
            "pos": {"ticker": "3QQQ", "bot": "A", "pnl_r": 1.5,
                    "entry_time": sim_time(10, 0).isoformat(),
                    "unrealised_pnl": 80.0},
            "time": sim_time(15, 35),
        },
        {
            "name": "Short hold, slight loss",
            "pos": {"ticker": "MSFT", "bot": "B", "pnl_r": -0.1,
                    "entry_time": sim_time(14, 0).isoformat(),
                    "unrealised_pnl": -5.0},
            "time": sim_time(15, 30),
        },
    ]
    for test in force_tests:
        should_close, reason = mgr.should_force_close(test["pos"], test["time"])
        status = "FORCE CLOSE" if should_close else "OK"
        print(f"  {test['name']:30s} -> {status:12s} | {reason[:65]}")

    # ---- 6. End-of-day summary ----
    print("\n--- End-of-Day Summary ---")
    sim_trades = [
        {"ticker": "NVDA", "net_pnl": 85.0, "r_multiple": 1.5,
         "strategy": "S1", "direction": "LONG"},
        {"ticker": "AAPL", "net_pnl": -30.0, "r_multiple": -0.6,
         "strategy": "S2", "direction": "LONG"},
        {"ticker": "TSLA", "net_pnl": 120.0, "r_multiple": 2.1,
         "strategy": "S1", "direction": "LONG"},
        {"ticker": "AMD", "net_pnl": -15.0, "r_multiple": -0.3,
         "strategy": "S3", "direction": "SHORT"},
        {"ticker": "3QQQ", "net_pnl": 55.0, "r_multiple": 0.9,
         "strategy": "S6", "direction": "LONG"},
    ]
    summary = mgr.run_end_of_day_summary(sim_trades, equity=10_215.0)
    print(f"  Total P&L:       ${summary['total_pnl']:+.2f}")
    print(f"  Win Rate:        {summary['win_rate']:.1f}%")
    print(f"  Avg R:           {summary['avg_r']:+.2f}")
    print(f"  Total Trades:    {summary['total_trades']}")
    print(f"  Winners/Losers:  {summary['winners']}/{summary['losers']}")
    print(f"  Strategies:      {', '.join(summary['strategies_used'])}")
    print(f"  Fatigue:         {summary['fatigue_level']}")
    if summary["best_trade"]:
        bt = summary["best_trade"]
        print(f"  Best Trade:      {bt['ticker']} {bt['direction']} "
              f"${bt['net_pnl']:+.2f} ({bt['r_multiple']:+.1f}R)")
    if summary["worst_trade"]:
        wt = summary["worst_trade"]
        print(f"  Worst Trade:     {wt['ticker']} {wt['direction']} "
              f"${wt['net_pnl']:+.2f} ({wt['r_multiple']:+.1f}R)")

    # ---- 7. Daily schedule ----
    print("\n--- Daily Schedule (APScheduler) ---")
    schedule = mgr.get_daily_schedule()
    for event in schedule:
        # Parse the ISO time for display
        evt_dt = datetime.fromisoformat(event["time_et"])
        print(f"  {evt_dt.strftime('%H:%M')} ET  {event['action']:25s}  {event['description'][:60]}")

    # ---- 8. Dashboard status ----
    print("\n--- Dashboard Status ---")
    status = mgr.get_status()
    print(f"  Module:           {status['module']}")
    print(f"  Phase:            {status['current_phase']['phase']}")
    print(f"  Trades Today:     {status['trades_today']}")
    print(f"  Fatigue:          {status['fatigue_level']}")
    print(f"  Fatigue Mult:     {status['fatigue_multiplier']:.2f}")
    print(f"  Phase Transitions: {len(status['phase_transitions'])}")

    # ---- 9. Fatigue escalation simulation ----
    print("\n--- Fatigue Escalation Simulation ---")
    mgr2 = SessionBoundaryManager()
    for i in range(18):
        allowed, reason = mgr2.should_allow_entry(75, sim_time(10, 0))
        phase = mgr2.get_current_phase(sim_time(10, 0))
        status_str = "ALLOWED" if allowed else "BLOCKED"
        print(
            f"  Trade #{i+1:2d}: {status_str:7s} | "
            f"fatigue={phase['fatigue_level']:10s} | "
            f"size_mult={phase['effective_size_multiplier']:.2f}"
        )
        if not allowed:
            print(f"           BLOCKED: {reason[:70]}")

    # ---- 10. Daily reset ----
    print("\n--- Daily Reset ---")
    mgr.reset_daily()
    status_after = mgr.get_status()
    print(f"  Trades after reset: {status_after['trades_today']}")
    print(f"  Fatigue after reset: {status_after['fatigue_level']}")
    print(f"  Phase transitions after reset: {len(status_after['phase_transitions'])}")

    print("\n" + "=" * 72)
    print("Simulation complete.")
    print("=" * 72)
