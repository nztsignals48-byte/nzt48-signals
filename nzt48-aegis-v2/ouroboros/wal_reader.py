"""Read a finished day's WAL journal for Ouroboros analysis.

Quarantine: read-only access to completed day's ndjson file.
NEVER reads or writes the live WAL.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class ClosedTrade:
    """A completed trade extracted from WAL PositionClosed events."""
    ticker_id: int
    final_pnl: float
    entry_time_ns: int
    exit_time_ns: int
    entry_price: float = 0.0
    exit_price: float = 0.0
    qty: int = 0
    commission: float = 0.0
    exit_reason: str = ""
    strategy: str = ""
    regime_label: str = ""
    highest_rung: int = 0


@dataclass(frozen=True)
class FillRecord:
    """A fill event from WAL."""
    order_id: str
    ticker_id: int
    filled_qty: int
    price: float
    commission: float
    exec_id: str


@dataclass(frozen=True)
class OrderRecord:
    """A routed order from WAL."""
    order_id: str
    ticker_id: int
    confidence: float
    strategy: str
    kelly_fraction: float


@dataclass(frozen=True)
class RiskChange:
    """A risk state change from WAL."""
    from_state: str
    to_state: str
    trigger: str
    event_time_ns: int


@dataclass
class DayJournal:
    """All events extracted from a single day's WAL."""
    closed_trades: List[ClosedTrade] = field(default_factory=list)
    fills: List[FillRecord] = field(default_factory=list)
    orders: List[OrderRecord] = field(default_factory=list)
    risk_changes: List[RiskChange] = field(default_factory=list)
    equity_end: float = 0.0
    high_water: float = 0.0
    total_events: int = 0


def read_day_journal(wal_path: Path) -> Optional[DayJournal]:
    """Parse a day's WAL ndjson file into structured records.

    Returns None if file doesn't exist or is empty.
    Skips malformed lines (CRC mismatch tolerance per H27).
    """
    if not wal_path.exists():
        return None

    journal = DayJournal()
    order_map: dict = {}  # order_id → OrderRecord for enrichment
    fill_map: dict = {}   # order_id → list of FillRecord

    with open(wal_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # Skip malformed lines (H27)

            journal.total_events += 1
            payload = event.get("payload", {})
            _extract_event(payload, event, journal, order_map, fill_map)

    _enrich_closed_trades(journal, order_map, fill_map)
    return journal


def _extract_event(
    payload: dict,
    event: dict,
    journal: DayJournal,
    order_map: dict,
    fill_map: dict,
) -> None:
    """Extract a single WAL event into the journal."""
    if "PositionClosed" in payload:
        pc = payload["PositionClosed"]
        journal.closed_trades.append(ClosedTrade(
            ticker_id=pc["ticker_id"],
            final_pnl=pc["final_pnl"],
            entry_time_ns=pc["entry_time_ns"],
            exit_time_ns=pc["exit_time_ns"],
        ))
    elif "FillEvent" in payload:
        fe = payload["FillEvent"]
        rec = FillRecord(
            order_id=fe["order_id"],
            ticker_id=fe["ticker_id"],
            filled_qty=fe["filled_qty"],
            price=fe["price"],
            commission=fe["commission"],
            exec_id=fe["exec_id"],
        )
        journal.fills.append(rec)
        fill_map.setdefault(fe["order_id"], []).append(rec)
    elif "RoutedOrder" in payload:
        ro = payload["RoutedOrder"]
        rec = OrderRecord(
            order_id=ro["order_id"],
            ticker_id=ro["ticker_id"],
            confidence=ro["confidence"],
            strategy=ro["strategy"],
            kelly_fraction=ro["kelly_fraction"],
        )
        journal.orders.append(rec)
        order_map[ro["order_id"]] = rec
    elif "RiskStateChange" in payload:
        rsc = payload["RiskStateChange"]
        journal.risk_changes.append(RiskChange(
            from_state=rsc["from"],
            to_state=rsc["to"],
            trigger=rsc["trigger"],
            event_time_ns=event.get("event_time_ns", 0),
        ))
    elif "StateSnapshot" in payload:
        ss = payload["StateSnapshot"]
        journal.equity_end = ss.get("equity", 0.0)
        journal.high_water = ss.get("high_water", 0.0)


def _enrich_closed_trades(
    journal: DayJournal,
    order_map: dict,
    fill_map: dict,
) -> None:
    """Enrich closed trades with order/fill data for analytics."""
    # Build ticker_id → order mapping for enrichment
    ticker_orders: dict = {}
    for oid, orec in order_map.items():
        ticker_orders.setdefault(orec.ticker_id, []).append((oid, orec))

    enriched = []
    for trade in journal.closed_trades:
        orders = ticker_orders.get(trade.ticker_id, [])
        strategy = orders[0][1].strategy if orders else ""
        confidence = orders[0][1].confidence if orders else 0.0
        # Aggregate fills for this ticker
        total_commission = 0.0
        entry_price = 0.0
        total_qty = 0
        for oid, _ in orders:
            for fill in fill_map.get(oid, []):
                total_commission += fill.commission
                entry_price = fill.price  # Last fill price (simplified)
                total_qty += fill.filled_qty

        enriched.append(ClosedTrade(
            ticker_id=trade.ticker_id,
            final_pnl=trade.final_pnl,
            entry_time_ns=trade.entry_time_ns,
            exit_time_ns=trade.exit_time_ns,
            entry_price=entry_price,
            qty=total_qty,
            commission=total_commission,
            strategy=strategy,
        ))
    journal.closed_trades = enriched
