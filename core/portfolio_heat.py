"""
RC-02 -- Portfolio Heat Monitor
================================
Aggregates P&L across ALL strategies throughout the trading day.
Implements escalating circuit breakers:
  -3%%  -> WARNING alert to Telegram
  -5%%  -> Reduce ALL new position sizes by 50%%
  -10%% -> FULL HALT -- no new positions until next session

Academic basis:
  Thorp (1997) -- drawdown control is more important than maximising E(X)
  Kelly (1956) -- ruin probability approaches 1 without drawdown limits
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds (% of starting equity)
# ---------------------------------------------------------------------------
_WARNING_THRESHOLD: float = -3.0
_REDUCE_THRESHOLD: float = -5.0
_HALT_THRESHOLD: float = -10.0

# Alert levels for deduplication
_ALERT_LEVELS = ("WARNING", "REDUCE", "HALTED")

# Persist format version
_DATA_VERSION: int = 1


class PortfolioHeatMonitor:
    """RC-02: Portfolio heat aggregator with escalating circuit breakers.

    Tracks intraday realised P&L across all strategies and enforces
    three escalating drawdown limits:

      GREEN   : daily P&L > -3%%   -- normal operation
      WARNING : daily P&L <= -3%%  -- Telegram alert sent
      REDUCE  : daily P&L <= -5%%  -- new position sizes halved
      HALTED  : daily P&L <= -10%% -- no new entries until next session

    Data is persisted to data/daily_pnl.json after every trade so the
    monitor survives process restarts.
    """

    def __init__(
        self,
        data_path: str = "data/daily_pnl.json",
        equity: float = 10_000.0,
    ) -> None:
        """Initialise the monitor.

        Args:
            data_path:  Path to the JSON persistence file.
            equity:     Starting equity for the session.
        """
        self._data_path = data_path
        self._equity = equity
        _dir = os.path.dirname(data_path) or "data"
        os.makedirs(_dir, exist_ok=True)
        self._state: dict[str, Any] = self._load_or_reset()
        logger.info(
            "PortfolioHeatMonitor init | equity=%.2f | pnl_pct=%.2f%%",
            self._equity, self._state.get("total_pnl_pct", 0.0),
        )

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _fresh_state(self) -> dict[str, Any]:
        """Return a blank state dict for a new session."""
        return {
            "version": _DATA_VERSION,
            "date": date.today().isoformat(),
            "total_pnl_pct": 0.0,
            "total_pnl_abs": 0.0,
            "trades": [],
            "last_alert_level": None,
        }

    def _load_or_reset(self) -> dict[str, Any]:
        """Load persisted state if it matches today; else return a fresh state."""
        today = date.today().isoformat()
        if not os.path.exists(self._data_path):
            logger.debug("No persisted P&L file at %s -- starting fresh", self._data_path)
            return self._fresh_state()
        try:
            with open(self._data_path, "r", encoding="utf-8") as fh:
                state = json.load(fh)
            if state.get("date") == today:
                logger.info(
                    "Resumed session P&L | pnl_pct=%.2f%% | trades=%d",
                    state.get("total_pnl_pct", 0.0), len(state.get("trades", [])),
                )
                return state
            logger.info("Stale P&L file (date=%s) -- resetting for %s", state.get("date"), today)
            return self._fresh_state()
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load P&L state: %s -- starting fresh", exc)
            return self._fresh_state()

    def _persist(self) -> None:
        """Write current state to disk immediately."""
        try:
            _d = os.path.dirname(self._data_path) or "data"
            os.makedirs(_d, exist_ok=True)
            with open(self._data_path, "w", encoding="utf-8") as fh:
                json.dump(self._state, fh, indent=2)
        except OSError as exc:
            logger.error("Failed to persist P&L state: %s", exc)

    # ------------------------------------------------------------------
    # Core recording and aggregation
    # ------------------------------------------------------------------

    def record_trade(
        self,
        ticker: str,
        strategy: str,
        pnl_pct: float,
        pnl_abs: float,
    ) -> None:
        """Record a completed trade and update running totals.

        Args:
            ticker:    Ticker symbol.
            strategy:  Strategy identifier (e.g. "S15", "S3").
            pnl_pct:   Trade P&L as percentage of trade equity.
            pnl_abs:   Trade P&L in absolute GBP.
        """
        trade = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ticker": ticker,
            "strategy": strategy,
            "pnl_pct": round(pnl_pct, 4),
            "pnl_abs": round(pnl_abs, 2),
        }
        self._state["trades"].append(trade)
        self._state["total_pnl_abs"] = round(
            self._state.get("total_pnl_abs", 0.0) + pnl_abs, 2
        )
        self._state["total_pnl_pct"] = round(
            (self._state["total_pnl_abs"] / self._equity) * 100.0, 4
        )
        logger.info(
            "Trade recorded: %s/%s pnl=%.2f%% | session_total=%.2f%%",
            ticker, strategy, pnl_pct, self._state["total_pnl_pct"],
        )
        self._persist()

    def get_total_daily_pnl_pct(self) -> float:
        """Return today total realised P&L as percentage of starting equity."""
        return self._state.get("total_pnl_pct", 0.0)

    def get_current_heat(
        self,
        open_positions: list[dict[str, Any]] | None = None,
    ) -> float:
        """Return total unrealised exposure as percentage of equity.

        Args:
            open_positions:  List of open position dicts.  Each dict must
                             contain a "size_pct" key (position size as %% of
                             equity).  If None or empty, returns 0.0.

        Returns:
            Sum of all open position sizes as percentage of equity.
        """
        if not open_positions:
            return 0.0
        total = sum(
            float(pos.get("size_pct", 0.0)) for pos in open_positions
        )
        return round(total, 4)

    def get_status(self) -> str:
        """Return the current risk status label.

        Thresholds based on daily realised P&L vs starting equity::

          GREEN   : pnl_pct > -3%%
          WARNING : -5%% < pnl_pct <= -3%%
          REDUCE  : -10%% < pnl_pct <= -5%%
          HALTED  : pnl_pct <= -10%%

        Returns:
            One of "GREEN", "WARNING", "REDUCE", "HALTED".
        """
        pnl = self.get_total_daily_pnl_pct()
        if pnl <= _HALT_THRESHOLD:
            return "HALTED"
        if pnl <= _REDUCE_THRESHOLD:
            return "REDUCE"
        if pnl <= _WARNING_THRESHOLD:
            return "WARNING"
        return "GREEN"

    def is_halted(self) -> bool:
        """Return True if the session is fully halted (P&L <= -10%%)."""
        return self.get_total_daily_pnl_pct() <= _HALT_THRESHOLD

    def should_reduce_size(self) -> bool:
        """Return True if new position sizes should be halved (P&L <= -5%%)."""
        return self.get_total_daily_pnl_pct() <= _REDUCE_THRESHOLD

    def get_size_multiplier(self) -> float:
        """Return position size multiplier based on current heat level.

        Returns:
            1.0 at GREEN/WARNING, 0.5 at REDUCE, 0.0 at HALTED.
        """
        if self.is_halted():
            return 0.0
        if self.should_reduce_size():
            return 0.5
        return 1.0

    def reset_for_new_session(self) -> None:
        """Reset for a new trading session at 00:00 UTC.

        Archives yesterday P&L to a history file, then reinitialises
        today state to zero.  Called by the scheduler at midnight UTC.
        """
        yesterday = self._state.copy()
        logger.info(
            "Session reset | archiving date=%s pnl_pct=%.2f%% trades=%d",
            yesterday.get("date"), yesterday.get("total_pnl_pct", 0.0),
            len(yesterday.get("trades", [])),
        )
        self._archive_yesterday(yesterday)
        self._state = self._fresh_state()
        self._persist()

    def _archive_yesterday(self, state: dict[str, Any]) -> None:
        """Append yesterday summary to data/pnl_history.jsonl."""
        archive_path = os.path.join(
            os.path.dirname(self._data_path) or "data", "pnl_history.jsonl"
        )
        summary = {
            "date": state.get("date"),
            "total_pnl_pct": state.get("total_pnl_pct", 0.0),
            "total_pnl_abs": state.get("total_pnl_abs", 0.0),
            "trade_count": len(state.get("trades", [])),
        }
        try:
            with open(archive_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(summary) + "\n")
            logger.debug("Archived session: %s", summary)
        except OSError as exc:
            logger.error("Failed to archive session P&L: %s", exc)

    def get_telegram_alert(self) -> str | None:
        """Return a formatted Telegram alert string if a threshold was newly breached.

        Uses deduplication: each threshold level is only alerted once per
        session.  Returns None if no new alert is needed.

        Alert hierarchy (ascending severity):
          WARNING -> REDUCE -> HALTED

        Returns:
            Alert string for Telegram, or None if no action needed.
        """
        status = self.get_status()
        last_alert = self._state.get("last_alert_level")
        pnl = self.get_total_daily_pnl_pct()

        # Determine if this is a new (escalated) alert
        alert_order = {None: 0, "WARNING": 1, "REDUCE": 2, "HALTED": 3}
        current_level = None if status == "GREEN" else status
        current_rank = alert_order.get(current_level, 0)
        last_rank = alert_order.get(last_alert, 0)

        if current_rank <= last_rank or current_level is None:
            # No new threshold crossed
            return None

        # New threshold crossed -- build alert and persist the new level
        self._state["last_alert_level"] = current_level
        self._persist()

        if current_level == "WARNING":
            return (
                "RC-02 WARNING | Daily P&L: %.2f%% | "
                "Threshold: -3%% breached. Monitor closely."
            ) % pnl

        if current_level == "REDUCE":
            return (
                "RC-02 REDUCE | Daily P&L: %.2f%% | "
                "Threshold: -5%% breached. All new positions at 50%% size."
            ) % pnl

        if current_level == "HALTED":
            return (
                "RC-02 HALTED | Daily P&L: %.2f%% | "
                "Threshold: -10%% breached. NO NEW POSITIONS until next session."
            ) % pnl

        return None

    # ------------------------------------------------------------------
    # Convenience / introspection
    # ------------------------------------------------------------------

    def get_state_summary(self) -> dict[str, Any]:
        """Return a lightweight summary dict for dashboard / logging."""
        return {
            "date": self._state.get("date"),
            "status": self.get_status(),
            "total_pnl_pct": self.get_total_daily_pnl_pct(),
            "total_pnl_abs": self._state.get("total_pnl_abs", 0.0),
            "trade_count": len(self._state.get("trades", [])),
            "size_multiplier": self.get_size_multiplier(),
            "last_alert_level": self._state.get("last_alert_level"),
        }
