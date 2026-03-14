"""
NZT-48 AEGIS Plan Item 0-09: ScopedQuery Builder — Blood Oath #3.

PURPOSE:
    Prevent unscoped DB queries from matching stale historical data, which
    causes permanent deadlock via phantom loss streaks (SK-02 root cause).

    Every query that feeds a risk decision (loss-streak counting, daily PnL,
    firewall state, etc.) MUST be scoped to the current trading session.
    A "session" starts at 06:00 UK local time (pre-market prep window).
    If current UK time is before 06:00, the session_start rolls back to
    the PREVIOUS trading day's 06:00.

INVARIANT (Blood Oath #3):
    No query that influences position sizing, halt decisions, or firewall
    state may ever read data older than session_start. Violation = deadlock.

USAGE:
    from core.scoped_query import ScopedQuery

    with transaction() as conn:
        sq = ScopedQuery(conn)

        # Simple scoped query — injects WHERE session scope automatically
        rows = sq.execute(
            "SELECT net_pnl FROM virtual_trades WHERE bot_instance = ?",
            ("S15-ISA",),
            time_column="exit_time",
        ).fetchall()

        # Or get session_start for manual query construction
        start = ScopedQuery.get_session_start()

MIGRATION TARGETS (B-06 will wire these up):
    The following queries in main.py and delivery/database.py currently lack
    session scoping and MUST be migrated to use ScopedQuery:

    main.py:
    -------
    1. Line ~1098: "SELECT r_multiple FROM virtual_trades ORDER BY exit_time ASC"
       - Dynamic sizer warm-up. Loads ALL historical R-multiples with no date bound.
       - Risk: inflates/deflates sizer with ancient regime data.
       - Fix: scope to session or rolling N-day window via ScopedQuery.

    2. Lines ~1176-1187: consecutive loss counting in _update_state_from_db()
       - "SELECT net_pnl FROM virtual_trades WHERE exit_time >= datetime('now', '-12 hours')"
       - Already has a 12-hour guard (SK-02 hotfix) but uses datetime('now') which is UTC.
       - Risk: during BST, 12-hour window is actually 13 hours UK time.
       - Fix: replace with ScopedQuery.get_session_start() for exact session scope.

    3. Lines ~1200-1204: last stopout time query
       - "SELECT exit_time FROM virtual_trades WHERE exit_reason = 'STOP_HIT' ORDER BY ..."
       - No date bound at all — scans entire history.
       - Risk: ancient stopout from months ago triggers false cooldown.
       - Fix: scope to current session via ScopedQuery.

    4. Lines ~2271-2276: autopsy setup grade query
       - "SELECT AVG(setup_grade) ... FROM trade_autopsies WHERE created_at > datetime('now', '-30 days')"
       - Has 30-day window but uses datetime('now') (UTC).
       - Risk: moderate — 30-day window is wide enough to absorb BST drift.
       - Fix: low priority, but should use ScopedQuery for consistency.

    5. Lines ~8022-8029: nightly digest strategy P&L
       - "SELECT ... FROM virtual_trades WHERE date(exit_time) = date('now')"
       - Uses date('now') which is UTC — during BST, UK trades after midnight UTC
         are attributed to the wrong date.
       - Fix: scope to session via ScopedQuery.

    6. Lines ~8042-8049: nightly digest missed trade analysis
       - "SELECT ... FROM missed_trades WHERE date(timestamp) = date('now')"
       - Same UTC date problem as #5.
       - Fix: scope to session via ScopedQuery.

    7. Lines ~8085-8089: nightly digest autopsy lesson
       - "SELECT primary_lesson FROM trade_autopsies WHERE date(created_at) = date('now')"
       - Same UTC date problem.
       - Fix: scope to session via ScopedQuery.

    delivery/database.py:
    --------------------
    8. get_consecutive_losses() (line ~1008):
       - Uses "datetime('now', '-12 hours')" — same BST drift as main.py #2.
       - Fix: accept session_start param or use ScopedQuery internally.

    9. get_daily_pnl() (line ~956):
       - Uses "date(time_entered) = date('now')" — UTC date, wrong during BST.
       - Fix: replace date('now') with session-aware bounds.

    10. get_daily_trade_count() (line ~920):
        - Same "date('now')" UTC problem.
        - Fix: session-aware bounds.

    11. get_weekly_trade_count() (line ~944):
        - Uses "datetime('now', '-7 days')" — acceptable but should be consistent.

    12. get_weekly_pnl() (line ~986):
        - Same rolling 7-day window — acceptable but consistency preferred.

    13. get_firewall_events_today() (line ~1170):
        - "date(timestamp) = date('now')" — UTC date problem.

    14. get_regime_transitions_today() (line ~1177):
        - "date(timestamp) = date('now')" — UTC date problem.

    15. get_strategy_daily_stats() (line ~1158):
        - "date = date('now')" — UTC date problem.

ARCHITECTURE:
    ScopedQuery wraps a sqlite3.Connection and intercepts execute() calls.
    It does NOT modify the original query string. Instead, it appends a
    session-scope predicate via a helper that the caller opts into by
    specifying the time_column parameter.

    This is opt-in, not magic rewriting — callers explicitly declare which
    column holds the timestamp. This prevents silent breakage from query
    rewriting and makes the scope visible in code review.

REFERENCES:
    - AEGIS Master Plan v16.0, Section 4.3: Blood Oath #3
    - SK-02: "Zombie halt from phantom loss streaks"
    - core/clock.py: UK_TZ, now_uk() — single source of time
"""

from __future__ import annotations

import sqlite3
import logging
from datetime import datetime, time as dtime, timedelta
from typing import Any, Optional

from core.clock import UK_TZ, now_uk

logger = logging.getLogger("nzt48.scoped_query")

# Session starts at 06:00 UK local time (pre-market prep, before LSE 08:00 open).
# This gives 2 hours of margin for data feed warm-up, pre-market scanning, etc.
SESSION_START_HOUR: int = 6
SESSION_START_MINUTE: int = 0


class ScopedQuery:
    """Session-scoped database query builder.

    Prevents SK-02 (zombie halt) by ensuring all risk-relevant queries
    are bounded to the current trading session.

    The session boundary is 06:00 UK local time. If the current UK time
    is before 06:00, the session rolls back to the previous day's 06:00.
    Weekends are handled: if 06:00 falls on Saturday, it rolls back to
    Friday 06:00. If Sunday, also rolls back to Friday 06:00.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._session_start: datetime = self.get_session_start()
        # ISO format string for SQLite comparisons (UTC, timezone-stripped
        # for compatibility with SQLite's text-based datetime storage).
        self._session_start_iso: str = self._to_sqlite_iso(self._session_start)
        logger.debug("ScopedQuery initialized: session_start=%s", self._session_start_iso)

    @staticmethod
    def _to_sqlite_iso(dt: datetime) -> str:
        """Convert a timezone-aware datetime to an ISO string suitable for SQLite.

        SQLite stores datetimes as TEXT in ISO 8601 format, typically without
        timezone info (naive). The NZT-48 database stores all timestamps in
        UTC (via datetime('now') which is UTC in SQLite).

        We convert the UK-local session_start to UTC, then strip tzinfo
        to match the database's naive UTC storage format.
        """
        from datetime import timezone
        utc_dt = dt.astimezone(timezone.utc)
        return utc_dt.strftime("%Y-%m-%d %H:%M:%S")

    @classmethod
    def get_session_start(cls, ref_time: Optional[datetime] = None) -> datetime:
        """Compute the start of the current trading session (06:00 UK local).

        If ref_time is provided, uses it instead of now_uk(). This is useful
        for testing and replay.

        Session rules:
        1. If current UK time >= 06:00, session_start = today 06:00 UK.
        2. If current UK time <  06:00, session_start = yesterday 06:00 UK.
        3. If computed session_start falls on Saturday, roll back to Friday 06:00.
        4. If computed session_start falls on Sunday, roll back to Friday 06:00.

        Returns a timezone-aware datetime in UK timezone.
        """
        if ref_time is None:
            uk_now = now_uk()
        else:
            if ref_time.tzinfo is None:
                uk_now = ref_time.replace(tzinfo=UK_TZ)
            else:
                uk_now = ref_time.astimezone(UK_TZ)

        session_time = dtime(SESSION_START_HOUR, SESSION_START_MINUTE)

        if uk_now.time() >= session_time:
            # Session started today
            session_start = uk_now.replace(
                hour=SESSION_START_HOUR,
                minute=SESSION_START_MINUTE,
                second=0,
                microsecond=0,
            )
        else:
            # Before 06:00 — session started yesterday
            yesterday = uk_now - timedelta(days=1)
            session_start = yesterday.replace(
                hour=SESSION_START_HOUR,
                minute=SESSION_START_MINUTE,
                second=0,
                microsecond=0,
            )

        # Weekend rollback: if session_start is Saturday or Sunday, go to Friday
        weekday = session_start.weekday()
        if weekday == 5:  # Saturday -> Friday
            session_start -= timedelta(days=1)
        elif weekday == 6:  # Sunday -> Friday
            session_start -= timedelta(days=2)

        return session_start

    @classmethod
    def get_session_start_iso(cls, ref_time: Optional[datetime] = None) -> str:
        """Get session start as an ISO string ready for SQLite WHERE clauses.

        Returns UTC-normalized, timezone-stripped ISO string matching the
        database's storage format.
        """
        return cls._to_sqlite_iso(cls.get_session_start(ref_time))

    def execute(
        self,
        query: str,
        params: tuple[Any, ...] | list[Any] = (),
        *,
        time_column: Optional[str] = None,
    ) -> sqlite3.Cursor:
        """Execute a query with automatic session scoping.

        If time_column is provided, appends
            AND {time_column} >= '{session_start_iso}'
        to the query. The scope is injected as a literal (not a parameter)
        because session_start is a computed constant, not user input.

        If time_column is None, executes the query as-is (no scoping).
        This allows ScopedQuery to be used as a drop-in replacement for
        conn.execute() with opt-in scoping.

        Args:
            query: SQL query string (SELECT, UPDATE, DELETE).
            params: Query parameters (tuple or list).
            time_column: Column name to scope on (e.g., "exit_time",
                         "timestamp", "created_at"). Must be a valid
                         column name (alphanumeric + underscore only).

        Returns:
            sqlite3.Cursor from the executed query.

        Raises:
            ValueError: If time_column contains invalid characters.
        """
        if time_column is not None:
            # Validate column name to prevent SQL injection
            if not time_column.replace("_", "").isalnum():
                raise ValueError(
                    f"Invalid time_column name: {time_column!r}. "
                    "Only alphanumeric characters and underscores are allowed."
                )

            scope_clause = f" AND {time_column} >= '{self._session_start_iso}'"

            # Inject the scope clause before ORDER BY, GROUP BY, LIMIT, or at end
            query_upper = query.upper()
            insert_pos = len(query)
            for keyword in ("ORDER BY", "GROUP BY", "LIMIT", "HAVING"):
                idx = query_upper.rfind(keyword)
                if idx != -1 and idx < insert_pos:
                    insert_pos = idx

            scoped_query = query[:insert_pos].rstrip() + scope_clause + " " + query[insert_pos:]
            logger.debug(
                "ScopedQuery: injected scope on '%s' >= '%s'",
                time_column, self._session_start_iso,
            )
            return self._conn.execute(scoped_query, params)

        # No scoping requested — pass through
        return self._conn.execute(query, params)

    def execute_with_explicit_start(
        self,
        query: str,
        params: tuple[Any, ...] | list[Any] = (),
        *,
        time_column: str,
        session_start: Optional[datetime] = None,
    ) -> sqlite3.Cursor:
        """Execute with a custom session start override.

        Useful for replay mode or when you need to query a specific
        historical session.
        """
        if session_start is not None:
            start_iso = self._to_sqlite_iso(session_start)
        else:
            start_iso = self._session_start_iso

        if not time_column.replace("_", "").isalnum():
            raise ValueError(f"Invalid time_column name: {time_column!r}")

        scope_clause = f" AND {time_column} >= '{start_iso}'"

        query_upper = query.upper()
        insert_pos = len(query)
        for keyword in ("ORDER BY", "GROUP BY", "LIMIT", "HAVING"):
            idx = query_upper.rfind(keyword)
            if idx != -1 and idx < insert_pos:
                insert_pos = idx

        scoped_query = query[:insert_pos].rstrip() + scope_clause + " " + query[insert_pos:]
        return self._conn.execute(scoped_query, params)

    @property
    def session_start(self) -> datetime:
        """The current session start as a timezone-aware UK datetime."""
        return self._session_start

    @property
    def session_start_iso(self) -> str:
        """The current session start as a SQLite-compatible ISO string (UTC)."""
        return self._session_start_iso

    @property
    def connection(self) -> sqlite3.Connection:
        """Access the underlying connection for non-scoped operations."""
        return self._conn

    def __repr__(self) -> str:
        return f"ScopedQuery(session_start={self._session_start_iso!r})"
