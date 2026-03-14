"""
NZT-48 ReconciliationAuditor — Three-Way Reconciliation (AEGIS K-03)
=====================================================================
Three-way reconciliation: broker vs Redis vs SQLite every 5 minutes.

A position mismatch between any two sources is a CRITICAL fault that
demands immediate action. The escalation sequence:

  1. DETECT  — compare broker positions, Redis state, SQLite records
  2. ALERT   — log CRITICAL + Telegram alert with full diff
  3. FLATTEN — submit MOC (Market-On-Close) orders for mismatched tickers
  4. KILL    — write kill switch flag, send SIGKILL to engine if needed

Why three sources:
  - Broker (IBKR) is the legal source of truth for actual holdings
  - Redis is the engine's hot state (chandelier stops, positions, P&L)
  - SQLite is the persistent journal (trades, fills, daily summaries)

  Any drift between them indicates a bug, missed fill, or phantom position
  that could lead to unhedged risk or duplicated trades.

This is a SKELETON — full Q2 implementation will add:
  - Real IBKR position query via IBKRGateway.get_positions()
  - Redis HGETALL for chandelier/position state
  - SQLite query for open positions from trade journal
  - Detailed diff with per-field comparison (qty, avg_price, stop)
  - MOC order submission for emergency flatten
  - Integration with system_watchdog.py kill switch
  - Scheduled execution via APScheduler (every 5 min during market hours)

References:
  - AEGIS H-06: Broker Failure Protocol
  - AEGIS H-07: Position Reconciliation Requirements
  - SEC Rule 15c3-1: Net Capital Requirements (broker-dealer reconciliation)
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("nzt48.reconciliation")


# ---------------------------------------------------------------------------
# Enums & data classes
# ---------------------------------------------------------------------------

class MismatchSeverity(str, enum.Enum):
    """Severity classification for reconciliation mismatches."""
    INFO = "INFO"           # minor float rounding (<0.01 shares or <0.1% price)
    WARNING = "WARNING"     # quantity off by 1-5% or price off by >0.5%
    CRITICAL = "CRITICAL"   # quantity mismatch >5%, phantom position, or missing position


class MismatchSource(str, enum.Enum):
    """Which source pair disagrees."""
    BROKER_VS_REDIS = "BROKER_VS_REDIS"
    BROKER_VS_SQLITE = "BROKER_VS_SQLITE"
    REDIS_VS_SQLITE = "REDIS_VS_SQLITE"


@dataclass
class PositionSnapshot:
    """Position state from a single source."""
    source: str = ""         # "broker", "redis", "sqlite"
    ticker: str = ""
    quantity: int = 0
    avg_price: float = 0.0
    stop_price: float = 0.0
    unrealised_pnl: float = 0.0
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Mismatch:
    """Single field-level mismatch between two sources."""
    ticker: str = ""
    field_name: str = ""       # "quantity", "avg_price", "stop_price", etc.
    source_pair: MismatchSource = MismatchSource.BROKER_VS_REDIS
    severity: MismatchSeverity = MismatchSeverity.WARNING
    value_a: str = ""          # value from source A
    value_b: str = ""          # value from source B
    message: str = ""          # human-readable description


@dataclass
class ReconciliationResult:
    """Complete result of a three-way reconciliation run."""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    clean: bool = True                       # True if all three sources agree
    mismatches: list[Mismatch] = field(default_factory=list)
    broker_positions: dict[str, PositionSnapshot] = field(default_factory=dict)
    redis_positions: dict[str, PositionSnapshot] = field(default_factory=dict)
    sqlite_positions: dict[str, PositionSnapshot] = field(default_factory=dict)
    phantom_tickers: list[str] = field(default_factory=list)  # in one source but not others
    missing_tickers: list[str] = field(default_factory=list)  # in broker but not engine
    duration_ms: float = 0.0                 # how long reconciliation took

    @property
    def has_critical(self) -> bool:
        """Whether any mismatch is CRITICAL severity."""
        return any(m.severity == MismatchSeverity.CRITICAL for m in self.mismatches)

    @property
    def mismatch_count(self) -> int:
        """Total number of mismatches detected."""
        return len(self.mismatches)

    def summary(self) -> str:
        """One-line summary for logging.

        Returns
        -------
        str
            e.g. "CLEAN: 4 positions reconciled in 12.3ms"
            or "MISMATCH: 2 critical, 1 warning across 4 positions (15.1ms)"
        """
        if self.clean:
            total = len(self.broker_positions)
            return f"CLEAN: {total} positions reconciled in {self.duration_ms:.1f}ms"

        crit = sum(1 for m in self.mismatches if m.severity == MismatchSeverity.CRITICAL)
        warn = sum(1 for m in self.mismatches if m.severity == MismatchSeverity.WARNING)
        total = len(
            set(self.broker_positions) | set(self.redis_positions) | set(self.sqlite_positions)
        )
        return (
            f"MISMATCH: {crit} critical, {warn} warning across "
            f"{total} positions ({self.duration_ms:.1f}ms)"
        )


# ---------------------------------------------------------------------------
# ReconciliationAuditor
# ---------------------------------------------------------------------------

class ReconciliationAuditor:
    """Three-way reconciliation: broker vs Redis vs SQLite every 5 min.

    Detects position drift between the three sources and escalates
    through alert -> flatten -> kill sequence on CRITICAL mismatches.

    Parameters
    ----------
    broker : object | None
        IBKRGateway or SyntheticBroker instance for broker position queries.
    redis_client : object | None
        Redis client for hot state queries.
    db_path : str
        Path to SQLite database for trade journal queries.
    kill_switch_path : str
        File path for the kill switch flag. Writing to this file triggers
        engine shutdown.
    auto_flatten : bool
        If True, submit MOC orders on CRITICAL mismatch (default False
        for skeleton — safety first).
    """

    def __init__(
        self,
        broker: object | None = None,
        redis_client: object | None = None,
        db_path: str = "data/nzt48.db",
        kill_switch_path: str = "data/KILL_SWITCH",
        auto_flatten: bool = False,
    ):
        self._broker = broker
        self._redis = redis_client
        self._db_path = db_path
        self._kill_switch_path = kill_switch_path
        self._auto_flatten = auto_flatten

        # History
        self._last_result: Optional[ReconciliationResult] = None
        self._run_count: int = 0
        self._total_mismatches: int = 0

        logger.info(
            "ReconciliationAuditor initialised: db=%s, auto_flatten=%s",
            db_path, auto_flatten,
        )

    # -------------------------------------------------------------------
    # Core reconciliation
    # -------------------------------------------------------------------

    async def reconcile(self) -> ReconciliationResult:
        """Run three-way reconciliation across broker, Redis, and SQLite.

        Queries all three sources for current positions, performs
        field-level comparison, and returns a ReconciliationResult
        with any mismatches classified by severity.

        Returns
        -------
        ReconciliationResult
            Full reconciliation result with mismatch details.
        """
        # TODO (Q2): implement full reconciliation
        #   1. Query broker positions via self._broker
        #   2. Query Redis positions via HGETALL on position keys
        #   3. Query SQLite for open positions from trades table
        #   4. Build PositionSnapshot for each source
        #   5. Detect phantom/missing tickers (set differences)
        #   6. Compare fields: quantity, avg_price, stop_price
        #   7. Classify mismatches by severity
        #   8. If has_critical and auto_flatten: call on_mismatch()
        #   9. Store result in self._last_result
        #  10. Return result
        raise NotImplementedError("K-03 skeleton — Q2 implementation pending")

    # -------------------------------------------------------------------
    # Escalation
    # -------------------------------------------------------------------

    async def on_mismatch(self, result: ReconciliationResult) -> None:
        """Escalation handler for reconciliation mismatches.

        Escalation sequence for CRITICAL mismatches:
          1. Log CRITICAL with full diff details
          2. Send Telegram alert to operator
          3. Submit MOC (Market-On-Close) orders for mismatched tickers
          4. Write kill switch file to halt engine
          5. Send SIGKILL to engine process as last resort

        For WARNING mismatches:
          1. Log WARNING with diff details
          2. Send Telegram notification (non-urgent)

        Parameters
        ----------
        result : ReconciliationResult
            The reconciliation result containing mismatch details.
        """
        # TODO (Q2): implement escalation sequence
        #   1. Categorise severity
        #   2. For CRITICAL:
        #      a. logger.critical() with full mismatch dump
        #      b. Telegram alert via bots/telegram_bot.py
        #      c. Submit MOC orders via self._broker for phantom tickers
        #      d. Write self._kill_switch_path
        #      e. os.kill(os.getpid(), signal.SIGKILL) as nuclear option
        #   3. For WARNING:
        #      a. logger.warning() with details
        #      b. Telegram notification
        raise NotImplementedError("K-03 skeleton — Q2 implementation pending")

    # -------------------------------------------------------------------
    # Source queries (private — to be implemented in Q2)
    # -------------------------------------------------------------------

    async def _fetch_broker_positions(self) -> dict[str, PositionSnapshot]:
        """Query broker for current positions.

        Returns
        -------
        dict[str, PositionSnapshot]
            Ticker -> position snapshot from broker.
        """
        # TODO (Q2): query IBKRGateway or SyntheticBroker
        raise NotImplementedError("K-03 skeleton — Q2 implementation pending")

    async def _fetch_redis_positions(self) -> dict[str, PositionSnapshot]:
        """Query Redis for hot-state positions.

        Returns
        -------
        dict[str, PositionSnapshot]
            Ticker -> position snapshot from Redis.
        """
        # TODO (Q2): HGETALL on position hash keys
        raise NotImplementedError("K-03 skeleton — Q2 implementation pending")

    async def _fetch_sqlite_positions(self) -> dict[str, PositionSnapshot]:
        """Query SQLite trade journal for open positions.

        Returns
        -------
        dict[str, PositionSnapshot]
            Ticker -> position snapshot from SQLite.
        """
        # TODO (Q2): SELECT from trades WHERE exit_time IS NULL
        raise NotImplementedError("K-03 skeleton — Q2 implementation pending")

    # -------------------------------------------------------------------
    # Emergency actions (private — to be implemented in Q2)
    # -------------------------------------------------------------------

    async def _submit_moc_flatten(self, tickers: list[str]) -> None:
        """Submit Market-On-Close orders to flatten mismatched positions.

        Parameters
        ----------
        tickers : list[str]
            Tickers to flatten via MOC orders.
        """
        # TODO (Q2): submit MOC orders via broker
        raise NotImplementedError("K-03 skeleton — Q2 implementation pending")

    def _write_kill_switch(self, reason: str) -> None:
        """Write kill switch file to halt the engine.

        Parameters
        ----------
        reason : str
            Human-readable reason for the kill switch activation.
        """
        # TODO (Q2): write reason + timestamp to kill_switch_path
        raise NotImplementedError("K-03 skeleton — Q2 implementation pending")

    # -------------------------------------------------------------------
    # Status queries
    # -------------------------------------------------------------------

    @property
    def last_result(self) -> Optional[ReconciliationResult]:
        """Most recent reconciliation result, or None if never run."""
        return self._last_result

    @property
    def run_count(self) -> int:
        """Total number of reconciliation runs completed."""
        return self._run_count

    def get_status(self) -> dict:
        """Get auditor status summary.

        Returns
        -------
        dict
            Keys: run_count, total_mismatches, last_clean, last_run_at,
            auto_flatten_enabled.
        """
        return {
            "run_count": self._run_count,
            "total_mismatches": self._total_mismatches,
            "last_clean": self._last_result.clean if self._last_result else None,
            "last_run_at": (
                self._last_result.timestamp.isoformat()
                if self._last_result else None
            ),
            "auto_flatten_enabled": self._auto_flatten,
        }
