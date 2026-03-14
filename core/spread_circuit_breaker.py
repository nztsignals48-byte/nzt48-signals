"""
K-08: Spread-Expansion Circuit Breaker.
Forbid Market Orders when spread >50bps. Hold cash >100bps. HALT >200bps.

Thresholds (basis points):
  NORMAL   :   0 -  50 bps  ->  all order types OK
  ELEVATED :  50 - 100 bps  ->  LIMIT orders only (no MARKET)
  BLOWOUT  : 100 - 200 bps  ->  HOLD CASH (no new orders at all)
  HALT     :      > 200 bps  ->  cancel all resting orders, halt trading
"""
import logging
from datetime import datetime
from enum import IntEnum
from typing import Optional

logger = logging.getLogger(__name__)


class SpreadState(IntEnum):
    NORMAL = 0       # <50 bps — all order types OK
    ELEVATED = 1     # 50-100 bps — LIMIT orders only (no MARKET)
    BLOWOUT = 2      # 100-200 bps — HOLD CASH (no new orders)
    HALT = 3         # >200 bps — HALT state, cancel all resting


class SpreadSnapshot:
    """Immutable record of a spread evaluation."""
    __slots__ = ('ticker', 'spread_bps', 'state', 'timestamp')

    def __init__(
        self,
        ticker: str,
        spread_bps: float,
        state: SpreadState,
        timestamp: datetime,
    ) -> None:
        self.ticker = ticker
        self.spread_bps = spread_bps
        self.state = state
        self.timestamp = timestamp

    def __repr__(self) -> str:
        return (
            f"SpreadSnapshot({self.ticker}, {self.spread_bps:.1f}bps, "
            f"{self.state.name})"
        )


class SpreadCircuitBreaker:
    """
    Per-ticker spread circuit breaker.

    Each call to ``evaluate()`` records the current spread and returns the
    corresponding SpreadState. Downstream callers use the query methods
    to gate order placement.
    """

    # Configurable thresholds (bps)
    THRESHOLD_ELEVATED: float = 50.0
    THRESHOLD_BLOWOUT: float = 100.0
    THRESHOLD_HALT: float = 200.0

    def __init__(self) -> None:
        self._state_by_ticker: dict[str, SpreadState] = {}
        self._last_snapshot: dict[str, SpreadSnapshot] = {}

    # ── evaluation ──────────────────────────────────────────────────────

    def evaluate(self, ticker: str, spread_bps: float) -> SpreadState:
        """
        Classify the current spread for *ticker* and return the resulting state.
        Logs on every state change.
        """
        if spread_bps > self.THRESHOLD_HALT:
            state = SpreadState.HALT
        elif spread_bps > self.THRESHOLD_BLOWOUT:
            state = SpreadState.BLOWOUT
        elif spread_bps > self.THRESHOLD_ELEVATED:
            state = SpreadState.ELEVATED
        else:
            state = SpreadState.NORMAL

        prev = self._state_by_ticker.get(ticker, SpreadState.NORMAL)
        if state != prev:
            logger.warning(
                "SPREAD CB %s: %s -> %s (%.1f bps)",
                ticker,
                prev.name,
                state.name,
                spread_bps,
            )

        self._state_by_ticker[ticker] = state
        self._last_snapshot[ticker] = SpreadSnapshot(
            ticker=ticker,
            spread_bps=spread_bps,
            state=state,
            timestamp=datetime.utcnow(),
        )
        return state

    # ── query helpers ───────────────────────────────────────────────────

    def get_state(self, ticker: str) -> SpreadState:
        """Return last known state for *ticker* (NORMAL if never evaluated)."""
        return self._state_by_ticker.get(ticker, SpreadState.NORMAL)

    def can_use_market_order(self, ticker: str) -> bool:
        """Market orders are only allowed in NORMAL (<50 bps)."""
        return self.get_state(ticker) == SpreadState.NORMAL

    def can_enter(self, ticker: str) -> bool:
        """New entries are allowed in NORMAL and ELEVATED (limit only)."""
        return self.get_state(ticker) <= SpreadState.ELEVATED

    def can_place_limit_order(self, ticker: str) -> bool:
        """Limit orders are allowed in NORMAL and ELEVATED."""
        return self.get_state(ticker) <= SpreadState.ELEVATED

    def must_hold_cash(self, ticker: str) -> bool:
        """True when no new orders should be submitted (BLOWOUT or worse)."""
        return self.get_state(ticker) >= SpreadState.BLOWOUT

    def must_halt(self, ticker: str) -> bool:
        """True when all resting orders should be cancelled (>200 bps)."""
        return self.get_state(ticker) >= SpreadState.HALT

    # ── introspection ───────────────────────────────────────────────────

    def get_snapshot(self, ticker: str) -> Optional[SpreadSnapshot]:
        """Return the last SpreadSnapshot for *ticker*, or None."""
        return self._last_snapshot.get(ticker)

    def all_states(self) -> dict[str, str]:
        """Return {ticker: state_name} for all evaluated tickers."""
        return {t: s.name for t, s in self._state_by_ticker.items()}

    def summary(self) -> dict:
        """JSON-serializable summary for telemetry."""
        return {
            "tickers_tracked": len(self._state_by_ticker),
            "states": self.all_states(),
            "halted": [
                t for t, s in self._state_by_ticker.items()
                if s >= SpreadState.HALT
            ],
            "blowout": [
                t for t, s in self._state_by_ticker.items()
                if s == SpreadState.BLOWOUT
            ],
        }
