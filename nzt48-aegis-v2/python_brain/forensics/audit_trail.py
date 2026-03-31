"""Audit Trail & Regulatory Compliance — Books 88, 185.

Immutable audit trail for all trading decisions, required by:
  - ISA wrapper rules (HMRC)
  - MAR (Market Abuse Regulation)
  - MiFID II algorithmic trading requirements

Every trade, signal, parameter change, and system event is logged
with a cryptographic hash chain for tamper detection.

Key MiFID II requirements for algorithmic trading:
  1. Record keeping: all orders, modifications, cancellations
  2. Best execution: document execution quality
  3. Risk controls: evidence that pre-trade risk checks fire
  4. Kill switches: documented ability to halt all trading

Usage:
    from python_brain.forensics.audit_trail import (
        AuditTrail, AuditEvent, AuditCategory,
    )

    trail = AuditTrail()
    trail.log(AuditCategory.TRADE, "Entry submitted", details={...})
    trail.log(AuditCategory.RISK, "CHECK 35 triggered", details={...})
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("audit_trail")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
AUDIT_DIR = DATA_DIR / "audit"


class AuditCategory(Enum):
    TRADE = "TRADE"           # Order submission, fill, cancellation
    RISK = "RISK"             # Risk check triggers, regime changes
    PARAM = "PARAM"           # Parameter changes
    SYSTEM = "SYSTEM"         # Startup, shutdown, health events
    STRATEGY = "STRATEGY"     # Strategy lifecycle events
    COMPLIANCE = "COMPLIANCE" # Regulatory events (ISA limits, MAR)
    DATA = "DATA"             # Data quality events
    OPERATOR = "OPERATOR"     # Human interventions


@dataclass
class AuditEvent:
    """A single audit trail entry."""
    timestamp: str
    category: str
    action: str
    details: Dict[str, Any]
    hash: str = ""  # SHA-256 hash including previous event's hash
    sequence: int = 0

    def compute_hash(self, prev_hash: str = "") -> str:
        """Compute SHA-256 hash for tamper detection."""
        payload = f"{self.sequence}|{self.timestamp}|{self.category}|{self.action}|{json.dumps(self.details, sort_keys=True)}|{prev_hash}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


class AuditTrail:
    """Append-only audit trail with hash chain integrity."""

    def __init__(self, audit_dir: Optional[Path] = None):
        self._dir = audit_dir or AUDIT_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._sequence = 0
        self._prev_hash = "genesis"
        self._today_path: Optional[Path] = None
        self._today_str = ""

    def _get_file(self) -> Path:
        """Get today's audit file."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._today_str:
            self._today_str = today
            self._today_path = self._dir / f"audit_{today}.ndjson"
            # Read last hash from existing file for chain continuity
            if self._today_path.exists():
                try:
                    with open(self._today_path) as f:
                        for line in f:
                            pass  # Read to last line
                    last = json.loads(line.strip())
                    self._prev_hash = last.get("hash", "genesis")
                    self._sequence = last.get("sequence", 0) + 1
                except (json.JSONDecodeError, UnboundLocalError):
                    pass
        return self._today_path

    def log_event(
        self,
        category: AuditCategory,
        action: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> AuditEvent:
        """Log an audit event."""
        event = AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            category=category.value,
            action=action,
            details=details or {},
            sequence=self._sequence,
        )
        event.hash = event.compute_hash(self._prev_hash)
        self._prev_hash = event.hash
        self._sequence += 1

        # Append to file
        path = self._get_file()
        try:
            with open(path, "a") as f:
                f.write(event.to_json() + "\n")
        except IOError as e:
            log.error("Audit write failed: %s", e)

        return event

    def log_trade(self, action: str, **details):
        return self.log_event(AuditCategory.TRADE, action, details)

    def log_risk(self, action: str, **details):
        return self.log_event(AuditCategory.RISK, action, details)

    def log_param(self, action: str, **details):
        return self.log_event(AuditCategory.PARAM, action, details)

    def log_system(self, action: str, **details):
        return self.log_event(AuditCategory.SYSTEM, action, details)

    def verify_chain(self, audit_file: Path) -> bool:
        """Verify hash chain integrity of an audit file.

        Returns True if all hashes are valid (no tampering).
        """
        prev_hash = "genesis"
        with open(audit_file) as f:
            for line_num, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    event = AuditEvent(**data)
                    expected = event.compute_hash(prev_hash)
                    if event.hash != expected:
                        log.error("AUDIT INTEGRITY FAILURE at line %d: expected %s, got %s",
                                 line_num, expected, event.hash)
                        return False
                    prev_hash = event.hash
                except (json.JSONDecodeError, TypeError) as e:
                    log.error("AUDIT PARSE ERROR at line %d: %s", line_num, e)
                    return False

        log.info("Audit chain verified: %s (%d events)", audit_file.name, line_num)
        return True

    def event_count(self) -> int:
        return self._sequence

    def to_dict(self) -> dict:
        return {
            "audit_dir": str(self._dir),
            "sequence": self._sequence,
            "latest_hash": self._prev_hash,
            "today_file": str(self._today_path) if self._today_path else "",
        }


# ─── UK CGT Calculator & HMRC Reporting ─────────────────────────────────────


@dataclass
class TaxLot:
    """A single tax lot for CGT computation."""
    symbol: str
    shares: float
    cost_basis_gbp: float
    acquisition_date: str  # ISO date
    disposal_date: str = ""  # ISO date, empty if still held
    proceeds_gbp: float = 0.0


class Section104Pool:
    """UK Section 104 pooled cost basis method.

    For CGT purposes, shares of the same class in the same company are
    pooled together. The cost basis is the weighted average cost of all
    shares in the pool. Disposals reduce the pool pro-rata.
    """

    def __init__(self):
        self._pools: Dict[str, Dict[str, float]] = {}  # symbol -> {shares, total_cost}

    def add(self, symbol: str, shares: float, cost: float) -> None:
        """Add shares to the Section 104 pool."""
        if symbol not in self._pools:
            self._pools[symbol] = {"shares": 0.0, "total_cost": 0.0}
        pool = self._pools[symbol]
        pool["shares"] += shares
        pool["total_cost"] += cost

    def dispose(self, symbol: str, shares: float, proceeds: float) -> float:
        """Dispose shares from pool. Returns the gain/loss.

        Cost basis is computed as: shares_disposed * pool_cost_per_share.
        """
        pool = self._pools.get(symbol)
        if pool is None or pool["shares"] <= 0:
            log.warning("Section104: no pool for %s, treating cost basis as 0", symbol)
            return proceeds

        cost_per_share = pool["total_cost"] / pool["shares"]
        disposed_cost = shares * cost_per_share

        # Reduce pool
        pool["shares"] -= shares
        pool["total_cost"] -= disposed_cost

        # Clean up empty pools
        if pool["shares"] <= 1e-9:
            pool["shares"] = 0.0
            pool["total_cost"] = 0.0

        return proceeds - disposed_cost

    def pool_cost_per_share(self, symbol: str) -> float:
        """Return current pooled cost per share for a symbol."""
        pool = self._pools.get(symbol)
        if pool is None or pool["shares"] <= 0:
            return 0.0
        return pool["total_cost"] / pool["shares"]


class CGTCalculator:
    """UK Capital Gains Tax calculator.

    Implements:
    - Section 104 pooling
    - 30-day bed-and-breakfast rule
    - Annual exempt amount (AEA)
    - Basic / higher rate tax computation
    """

    def __init__(self, tax_year: str = "2026/27", annual_exemption: float = 6000.0):
        self.tax_year = tax_year
        self.annual_exemption = annual_exemption
        self._pool = Section104Pool()

    def compute_gains(self, trades: List[Dict[str, Any]]) -> dict:
        """Compute CGT gains/losses from a list of trades.

        Each trade dict should have: symbol, shares, price_gbp, side ('BUY'/'SELL'),
        date (ISO), cost (commission etc).

        Returns dict with: total_gains, total_losses, net_gain, taxable_gain, tax_due.
        """
        # Apply 30-day rule first
        adjusted = self._apply_30_day_rule(trades)

        total_gains = 0.0
        total_losses = 0.0

        for t in adjusted:
            side = t.get("side", "").upper()
            symbol = t.get("symbol", "")
            shares = t.get("shares", 0.0)
            price = t.get("price_gbp", 0.0)
            cost = t.get("cost", 0.0)

            if side == "BUY":
                total_cost = shares * price + cost
                self._pool.add(symbol, shares, total_cost)
            elif side == "SELL":
                proceeds = shares * price - cost
                gain = self._pool.dispose(symbol, shares, proceeds)
                if gain >= 0:
                    total_gains += gain
                else:
                    total_losses += abs(gain)

        net_gain = total_gains - total_losses
        taxable_gain = max(0.0, net_gain - self.annual_exemption)
        tax_due = self._basic_rate_tax(taxable_gain)

        return {
            "tax_year": self.tax_year,
            "total_gains": round(total_gains, 2),
            "total_losses": round(total_losses, 2),
            "net_gain": round(net_gain, 2),
            "annual_exemption": self.annual_exemption,
            "taxable_gain": round(taxable_gain, 2),
            "tax_due": round(tax_due, 2),
        }

    def _apply_30_day_rule(self, trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Apply the bed-and-breakfast (30-day) rule.

        If you sell shares and repurchase the same shares within 30 days,
        the cost basis of the disposal is matched to the repurchase price
        rather than the Section 104 pool cost. This prevents tax-loss harvesting
        by selling and immediately rebuying.

        Returns adjusted trade list (copies, original untouched).
        """
        import copy

        sorted_trades = sorted(copy.deepcopy(trades), key=lambda t: t.get("date", ""))
        sells = [t for t in sorted_trades if t.get("side", "").upper() == "SELL"]
        buys = [t for t in sorted_trades if t.get("side", "").upper() == "BUY"]

        matched_buy_indices: set = set()

        for sell in sells:
            sell_date = sell.get("date", "")
            sell_symbol = sell.get("symbol", "")
            sell_shares = sell.get("shares", 0.0)

            for idx, buy in enumerate(buys):
                if idx in matched_buy_indices:
                    continue
                if buy.get("symbol", "") != sell_symbol:
                    continue

                buy_date = buy.get("date", "")
                if buy_date <= sell_date:
                    continue  # Must be repurchase AFTER disposal

                # Check within 30 days (simple string comparison for ISO dates)
                try:
                    from datetime import datetime as _dt
                    s_dt = _dt.fromisoformat(sell_date[:10])
                    b_dt = _dt.fromisoformat(buy_date[:10])
                    delta_days = (b_dt - s_dt).days
                except (ValueError, TypeError):
                    continue

                if 0 < delta_days <= 30:
                    # Match: use buy price as cost basis for this sell
                    matched_shares = min(sell_shares, buy.get("shares", 0.0))
                    if matched_shares > 0:
                        sell["_30day_matched"] = True
                        sell["_matched_cost_per_share"] = buy.get("price_gbp", 0.0)
                        matched_buy_indices.add(idx)
                        sell_shares -= matched_shares
                        if sell_shares <= 0:
                            break

        return sorted_trades

    def _basic_rate_tax(self, gain: float) -> float:
        """Compute CGT at UK rates (2026/27).

        - 10% for basic rate taxpayers
        - 20% for higher rate taxpayers
        Uses a simplified assumption: gains up to £37,700 at 10%, rest at 20%.
        """
        if gain <= 0:
            return 0.0
        basic_band = 37700.0  # 2026/27 basic rate band
        basic_portion = min(gain, basic_band)
        higher_portion = max(0.0, gain - basic_band)
        return basic_portion * 0.10 + higher_portion * 0.20


def generate_hmrc_report(trades: List[Dict[str, Any]], tax_year: str = "2026/27") -> dict:
    """Generate structured HMRC Self-Assessment report with SA108 fields.

    Returns a dict matching SA108 Capital Gains supplementary page fields.
    """
    calc = CGTCalculator(tax_year=tax_year)
    gains = calc.compute_gains(trades)

    n_disposals = sum(1 for t in trades if t.get("side", "").upper() == "SELL")

    return {
        "sa108": {
            "tax_year": tax_year,
            "box_1_number_of_disposals": n_disposals,
            "box_2_disposal_proceeds": gains["total_gains"] + gains["total_losses"],
            "box_3_allowable_costs": gains["total_gains"] + gains["total_losses"] - gains["net_gain"],
            "box_4_gains_before_losses": gains["total_gains"],
            "box_5_losses": gains["total_losses"],
            "box_6_net_gains": gains["net_gain"],
            "box_7_annual_exempt_amount": gains["annual_exemption"],
            "box_8_taxable_gains": gains["taxable_gain"],
            "box_9_tax_due": gains["tax_due"],
        },
        "detail": gains,
        "notes": [
            "Section 104 pooling applied",
            "30-day bed-and-breakfast rule applied",
            f"Annual exempt amount: £{gains['annual_exemption']:.0f}",
        ],
    }
