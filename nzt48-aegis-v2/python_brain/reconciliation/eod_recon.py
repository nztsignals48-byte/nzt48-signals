"""End-of-Day Reconciliation — Book 59.

4-layer reconciliation at 22:00 UTC after all markets close:
  Layer 1: Positions (WAL vs broker)
  Layer 2: Orders (pending/filled/cancelled)
  Layer 3: Cash (available/committed/pending settlement)
  Layer 4: P&L (WAL vs broker statement)

3-severity classification:
  MINOR:  Auto-resolve (e.g., rounding differences < £0.10)
  MEDIUM: Flag for Claude analysis
  MAJOR:  Human escalation required (Telegram alert)

The intraday reconciler (reconciler.rs) runs every 5 minutes.
This module handles the comprehensive nightly reconciliation.

Usage:
    from python_brain.reconciliation.eod_recon import (
        EODReconciler, ReconciliationResult,
    )

    recon = EODReconciler()
    result = recon.run(wal_positions, broker_positions, broker_orders)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("eod_recon")


class Severity(Enum):
    MINOR = "MINOR"    # Auto-resolve
    MEDIUM = "MEDIUM"  # Claude analysis
    MAJOR = "MAJOR"    # Human escalation


@dataclass
class Discrepancy:
    """A single reconciliation discrepancy."""
    layer: str          # "positions", "orders", "cash", "pnl"
    severity: Severity
    description: str
    wal_value: Any = None
    broker_value: Any = None
    ticker: str = ""
    delta: float = 0.0
    auto_resolved: bool = False
    resolution: str = ""


@dataclass
class ReconciliationResult:
    """Complete EOD reconciliation result."""
    timestamp: str = ""
    discrepancies: List[Discrepancy] = field(default_factory=list)
    position_match: bool = False
    order_match: bool = False
    cash_match: bool = False
    pnl_match: bool = False

    @property
    def is_clean(self) -> bool:
        return self.position_match and self.order_match and self.cash_match and self.pnl_match

    @property
    def has_major(self) -> bool:
        return any(d.severity == Severity.MAJOR for d in self.discrepancies)

    @property
    def minor_count(self) -> int:
        return sum(1 for d in self.discrepancies if d.severity == Severity.MINOR)

    @property
    def medium_count(self) -> int:
        return sum(1 for d in self.discrepancies if d.severity == Severity.MEDIUM)

    @property
    def major_count(self) -> int:
        return sum(1 for d in self.discrepancies if d.severity == Severity.MAJOR)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "is_clean": self.is_clean,
            "position_match": self.position_match,
            "order_match": self.order_match,
            "cash_match": self.cash_match,
            "pnl_match": self.pnl_match,
            "discrepancies": {
                "minor": self.minor_count,
                "medium": self.medium_count,
                "major": self.major_count,
                "details": [
                    {"layer": d.layer, "severity": d.severity.value,
                     "description": d.description, "ticker": d.ticker,
                     "delta": d.delta, "resolved": d.auto_resolved}
                    for d in self.discrepancies
                ],
            },
        }


class EODReconciler:
    """End-of-Day reconciliation engine."""

    def __init__(self, tolerance_gbp: float = 0.10, tolerance_shares: int = 0):
        self.tolerance_gbp = tolerance_gbp
        self.tolerance_shares = tolerance_shares

    def run(
        self,
        wal_positions: Dict[str, Dict],
        broker_positions: Dict[str, Dict],
        wal_orders: Optional[List[Dict]] = None,
        broker_orders: Optional[List[Dict]] = None,
        wal_cash: float = 0.0,
        broker_cash: float = 0.0,
        wal_pnl: float = 0.0,
        broker_pnl: float = 0.0,
    ) -> ReconciliationResult:
        """Run all 4 reconciliation layers."""
        result = ReconciliationResult(
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # Layer 1: Positions
        pos_discs = self._reconcile_positions(wal_positions, broker_positions)
        result.discrepancies.extend(pos_discs)
        result.position_match = not any(d.severity != Severity.MINOR for d in pos_discs)

        # Layer 2: Orders
        if wal_orders is not None and broker_orders is not None:
            ord_discs = self._reconcile_orders(wal_orders, broker_orders)
            result.discrepancies.extend(ord_discs)
            result.order_match = not any(d.severity != Severity.MINOR for d in ord_discs)
        else:
            result.order_match = True  # Skip if no order data

        # Layer 3: Cash
        cash_discs = self._reconcile_cash(wal_cash, broker_cash)
        result.discrepancies.extend(cash_discs)
        result.cash_match = not any(d.severity != Severity.MINOR for d in cash_discs)

        # Layer 4: P&L
        pnl_discs = self._reconcile_pnl(wal_pnl, broker_pnl)
        result.discrepancies.extend(pnl_discs)
        result.pnl_match = not any(d.severity != Severity.MINOR for d in pnl_discs)

        # Log summary
        if result.is_clean:
            log.info("EOD RECON: CLEAN — all 4 layers match")
        else:
            log.warning(
                "EOD RECON: %d discrepancies (MINOR=%d, MEDIUM=%d, MAJOR=%d)",
                len(result.discrepancies), result.minor_count,
                result.medium_count, result.major_count,
            )

        return result

    def _reconcile_positions(
        self,
        wal: Dict[str, Dict],
        broker: Dict[str, Dict],
    ) -> List[Discrepancy]:
        """Layer 1: Compare WAL positions vs broker positions."""
        discs: List[Discrepancy] = []

        all_tickers = set(wal.keys()) | set(broker.keys())
        for ticker in all_tickers:
            wal_pos = wal.get(ticker, {})
            brk_pos = broker.get(ticker, {})

            wal_qty = wal_pos.get("quantity", 0)
            brk_qty = brk_pos.get("quantity", 0)

            if wal_qty != brk_qty:
                delta = abs(wal_qty - brk_qty)
                severity = Severity.MAJOR if delta > self.tolerance_shares else Severity.MINOR
                discs.append(Discrepancy(
                    layer="positions", severity=severity,
                    description=f"Quantity mismatch: WAL={wal_qty}, broker={brk_qty}",
                    ticker=ticker, wal_value=wal_qty, broker_value=brk_qty,
                    delta=delta,
                ))

            # Cost basis comparison
            wal_cost = wal_pos.get("avg_cost", 0)
            brk_cost = brk_pos.get("avg_cost", 0)
            if abs(wal_cost - brk_cost) > self.tolerance_gbp and wal_qty > 0:
                discs.append(Discrepancy(
                    layer="positions", severity=Severity.MEDIUM,
                    description=f"Cost basis mismatch: WAL={wal_cost:.4f}, broker={brk_cost:.4f}",
                    ticker=ticker, delta=abs(wal_cost - brk_cost),
                ))

        # Orphan detection
        for ticker in broker.keys() - wal.keys():
            if broker[ticker].get("quantity", 0) > 0:
                discs.append(Discrepancy(
                    layer="positions", severity=Severity.MAJOR,
                    description=f"ORPHAN: broker has position, WAL does not",
                    ticker=ticker, broker_value=broker[ticker].get("quantity"),
                ))

        for ticker in wal.keys() - broker.keys():
            if wal[ticker].get("quantity", 0) > 0:
                discs.append(Discrepancy(
                    layer="positions", severity=Severity.MAJOR,
                    description=f"PHANTOM: WAL has position, broker does not",
                    ticker=ticker, wal_value=wal[ticker].get("quantity"),
                ))

        return discs

    def _reconcile_orders(
        self,
        wal_orders: List[Dict],
        broker_orders: List[Dict],
    ) -> List[Discrepancy]:
        """Layer 2: Compare order status between WAL and broker."""
        discs: List[Discrepancy] = []

        wal_ids = {o.get("order_id"): o for o in wal_orders if o.get("order_id")}
        brk_ids = {o.get("order_id"): o for o in broker_orders if o.get("order_id")}

        # Missing fills
        for oid in wal_ids.keys() - brk_ids.keys():
            discs.append(Discrepancy(
                layer="orders", severity=Severity.MEDIUM,
                description=f"Order {oid} in WAL but not in broker",
                wal_value=wal_ids[oid],
            ))

        # Extra fills
        for oid in brk_ids.keys() - wal_ids.keys():
            discs.append(Discrepancy(
                layer="orders", severity=Severity.MAJOR,
                description=f"Order {oid} in broker but not in WAL",
                broker_value=brk_ids[oid],
            ))

        return discs

    def _reconcile_cash(self, wal_cash: float, broker_cash: float) -> List[Discrepancy]:
        """Layer 3: Compare cash balances."""
        delta = abs(wal_cash - broker_cash)
        if delta <= self.tolerance_gbp:
            return []

        severity = Severity.MINOR if delta < 1.0 else (Severity.MEDIUM if delta < 10.0 else Severity.MAJOR)
        return [Discrepancy(
            layer="cash", severity=severity,
            description=f"Cash mismatch: WAL={wal_cash:.2f}, broker={broker_cash:.2f}",
            wal_value=wal_cash, broker_value=broker_cash, delta=delta,
        )]

    def _reconcile_pnl(self, wal_pnl: float, broker_pnl: float) -> List[Discrepancy]:
        """Layer 4: Compare P&L calculations."""
        delta = abs(wal_pnl - broker_pnl)
        if delta <= self.tolerance_gbp:
            return []

        severity = Severity.MINOR if delta < 1.0 else (Severity.MEDIUM if delta < 5.0 else Severity.MAJOR)
        return [Discrepancy(
            layer="pnl", severity=severity,
            description=f"P&L mismatch: WAL={wal_pnl:.2f}, broker={broker_pnl:.2f}",
            wal_value=wal_pnl, broker_value=broker_pnl, delta=delta,
        )]


def save_recon_report(result: ReconciliationResult, output_dir: Path) -> Path:
    """Save reconciliation report to JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = output_dir / f"eod_recon_{today}.json"
    with open(path, "w") as f:
        json.dump(result.to_dict(), f, indent=2)
    return path
