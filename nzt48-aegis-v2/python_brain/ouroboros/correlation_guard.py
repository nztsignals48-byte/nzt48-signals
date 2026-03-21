"""RT3-P2 — Correlation-Aware Position Limits for 3x/5x ETPs.

Prevents concentrated exposure to correlated leveraged ETPs.
If QQQ3.L, NVD3.L, and TSM3.L are all long, a single tech selloff
wipes out 3× the expected loss. This module:

1. Groups ETPs by underlying sector/index correlation
2. Enforces sector-level position limits
3. Computes portfolio heat considering correlations
4. Generates config_writer-compatible gate rules

Integration:
  - Called by bridge.py before signal approval
  - Config written by config_writer at 04:51 UTC (nightly)
  - Real-time check: O(1) lookup against precomputed groups

QUARANTINE: Read-only analysis. Config changes go through config_writer pipeline.

Usage:
    python3 -m python_brain.ouroboros.correlation_guard              # Print groups
    python3 -m python_brain.ouroboros.correlation_guard --check      # Check current positions
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

log = logging.getLogger("ouroboros.correlation_guard")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))

# ---------------------------------------------------------------------------
# Static correlation groups (known LSE leveraged ETP families)
# ---------------------------------------------------------------------------
# Each group contains ETPs that are >0.85 correlated.
# A crash in the underlying hits ALL members simultaneously.

CORRELATION_GROUPS: Dict[str, Dict[str, Any]] = {
    "nasdaq_tech_3x": {
        "members": ["QQQ3.L", "QQQS.L"],
        "underlying": "NASDAQ-100",
        "leverage": "3x",
        "max_positions": 1,           # Only 1 from this group at a time
        "max_heat_pct": 5.0,          # Max 5% equity in this group
        "description": "3x NASDAQ-100 long/short pair",
    },
    "nasdaq_5x": {
        "members": ["QQQ5.L"],
        "underlying": "NASDAQ-100",
        "leverage": "5x",
        "max_positions": 1,
        "max_heat_pct": 3.0,          # Tighter for 5x leverage
        "description": "5x NASDAQ-100",
    },
    "sp500_leveraged": {
        "members": ["3LUS.L", "3USS.L", "5SPY.L", "SP5L.L"],
        "underlying": "S&P 500",
        "leverage": "3x-5x",
        "max_positions": 1,
        "max_heat_pct": 5.0,
        "description": "S&P 500 leveraged family",
    },
    "semiconductor": {
        "members": ["3SEM.L", "NVD3.L", "TSM3.L", "MU2.L"],
        "underlying": "Semiconductors",
        "leverage": "3x",
        "max_positions": 2,           # Allow 2 semis (diversified within sector)
        "max_heat_pct": 8.0,
        "description": "Semiconductor leveraged ETPs",
    },
    "single_stock_tech": {
        "members": ["GPT3.L", "TSL3.L"],
        "underlying": "Single-stock tech (MSFT, TSLA)",
        "leverage": "3x",
        "max_positions": 1,
        "max_heat_pct": 4.0,
        "description": "Single-stock 3x leveraged",
    },
}

# Build reverse lookup: symbol -> group_name
_SYMBOL_TO_GROUP: Dict[str, str] = {}
for group_name, group_info in CORRELATION_GROUPS.items():
    for member in group_info["members"]:
        _SYMBOL_TO_GROUP[member] = group_name


# ---------------------------------------------------------------------------
# Position state (read from telemetry or WAL)
# ---------------------------------------------------------------------------
@dataclass
class PositionState:
    """Current open positions for correlation check."""
    symbol: str
    qty: int
    entry_price: float
    unrealized_pnl: float = 0.0

    @property
    def position_value(self) -> float:
        return abs(self.entry_price * self.qty)


@dataclass
class CorrelationCheckResult:
    """Result of checking whether a new position violates correlation limits."""
    allowed: bool
    symbol: str
    group_name: str
    reason: str = ""
    current_group_positions: int = 0
    max_group_positions: int = 0
    current_group_heat_pct: float = 0.0
    max_group_heat_pct: float = 0.0


@dataclass
class CorrelationReport:
    """Full correlation guard analysis."""
    timestamp: str
    total_positions: int
    groups_in_use: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    violations: List[Dict[str, Any]] = field(default_factory=list)
    headroom: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


# ---------------------------------------------------------------------------
# Core guard logic
# ---------------------------------------------------------------------------
def get_group_for_symbol(symbol: str) -> Optional[str]:
    """Return the correlation group name for a symbol, or None if ungrouped."""
    return _SYMBOL_TO_GROUP.get(symbol)


def check_position_allowed(
    symbol: str,
    open_positions: List[PositionState],
    equity: float = 10_000.0,
) -> CorrelationCheckResult:
    """Check if opening a new position in `symbol` would violate correlation limits.

    Args:
        symbol: Symbol to check (e.g., "QQQ3.L")
        open_positions: Current open positions
        equity: Current portfolio equity

    Returns:
        CorrelationCheckResult with allowed=True/False and reason.
    """
    group_name = _SYMBOL_TO_GROUP.get(symbol)
    if group_name is None:
        # Symbol not in any correlation group — always allowed (no corr constraint)
        return CorrelationCheckResult(
            allowed=True,
            symbol=symbol,
            group_name="ungrouped",
            reason="Not in any correlation group",
        )

    group = CORRELATION_GROUPS[group_name]
    group_members = set(group["members"])
    max_positions = group["max_positions"]
    max_heat_pct = group["max_heat_pct"]

    # Count current positions in this group
    group_positions = [p for p in open_positions if p.symbol in group_members]
    current_count = len(group_positions)
    current_heat = sum(p.position_value for p in group_positions)
    current_heat_pct = current_heat / max(equity, 1.0) * 100

    # Check position count limit
    if current_count >= max_positions:
        return CorrelationCheckResult(
            allowed=False,
            symbol=symbol,
            group_name=group_name,
            reason=f"Group '{group_name}' already has {current_count}/{max_positions} positions "
                   f"({', '.join(p.symbol for p in group_positions)})",
            current_group_positions=current_count,
            max_group_positions=max_positions,
            current_group_heat_pct=round(current_heat_pct, 2),
            max_group_heat_pct=max_heat_pct,
        )

    # Check heat limit
    if current_heat_pct >= max_heat_pct:
        return CorrelationCheckResult(
            allowed=False,
            symbol=symbol,
            group_name=group_name,
            reason=f"Group '{group_name}' heat at {current_heat_pct:.1f}% >= {max_heat_pct}% limit",
            current_group_positions=current_count,
            max_group_positions=max_positions,
            current_group_heat_pct=round(current_heat_pct, 2),
            max_group_heat_pct=max_heat_pct,
        )

    return CorrelationCheckResult(
        allowed=True,
        symbol=symbol,
        group_name=group_name,
        reason=f"Allowed: {current_count + 1}/{max_positions} positions, "
               f"heat {current_heat_pct:.1f}% < {max_heat_pct}%",
        current_group_positions=current_count,
        max_group_positions=max_positions,
        current_group_heat_pct=round(current_heat_pct, 2),
        max_group_heat_pct=max_heat_pct,
    )


def analyze_portfolio_correlation(
    open_positions: List[PositionState],
    equity: float = 10_000.0,
) -> CorrelationReport:
    """Full portfolio correlation analysis."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    groups_in_use: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "positions": [], "heat_pct": 0.0, "max_positions": 0, "max_heat_pct": 0.0,
    })
    violations: List[Dict[str, Any]] = []

    for pos in open_positions:
        group_name = _SYMBOL_TO_GROUP.get(pos.symbol, "ungrouped")
        group_data = CORRELATION_GROUPS.get(group_name, {})

        groups_in_use[group_name]["positions"].append(pos.symbol)
        groups_in_use[group_name]["heat_pct"] += pos.position_value / max(equity, 1.0) * 100
        groups_in_use[group_name]["max_positions"] = group_data.get("max_positions", 99)
        groups_in_use[group_name]["max_heat_pct"] = group_data.get("max_heat_pct", 100.0)

    # Check violations
    for group_name, info in groups_in_use.items():
        if group_name == "ungrouped":
            continue
        n = len(info["positions"])
        if n > info["max_positions"]:
            violations.append({
                "group": group_name,
                "type": "position_count",
                "current": n,
                "limit": info["max_positions"],
                "positions": info["positions"],
            })
        if info["heat_pct"] > info["max_heat_pct"]:
            violations.append({
                "group": group_name,
                "type": "heat_limit",
                "current_pct": round(info["heat_pct"], 2),
                "limit_pct": info["max_heat_pct"],
                "positions": info["positions"],
            })

    # Compute headroom for each group
    headroom: Dict[str, Dict[str, Any]] = {}
    for group_name, group_info in CORRELATION_GROUPS.items():
        current_count = len(groups_in_use.get(group_name, {}).get("positions", []))
        current_heat = groups_in_use.get(group_name, {}).get("heat_pct", 0.0)
        headroom[group_name] = {
            "positions_available": max(0, group_info["max_positions"] - current_count),
            "heat_available_pct": round(max(0, group_info["max_heat_pct"] - current_heat), 2),
            "current_positions": current_count,
            "current_heat_pct": round(current_heat, 2),
        }

    return CorrelationReport(
        timestamp=now,
        total_positions=len(open_positions),
        groups_in_use=dict(groups_in_use),
        violations=violations,
        headroom=headroom,
    )


def generate_config_gates() -> Dict[str, Any]:
    """Generate correlation gate config for config_writer.

    Returns dict suitable for writing to [correlation_gates] section
    in dynamic_weights.toml.
    """
    gates: Dict[str, Any] = {}
    for group_name, group_info in CORRELATION_GROUPS.items():
        for member in group_info["members"]:
            gates[member] = {
                "group": group_name,
                "max_group_positions": group_info["max_positions"],
                "max_group_heat_pct": group_info["max_heat_pct"],
            }
    return gates


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [CorrGuard] %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="RT3-P2 — Correlation-Aware Position Limits")
    parser.add_argument("--check", action="store_true", help="Check current positions from telemetry")
    args = parser.parse_args()

    # Print group definitions
    print("CORRELATION GROUPS:")
    print(f"{'Group':<25} {'Members':<40} {'Max Pos':<10} {'Max Heat':<10}")
    print("-" * 85)
    for name, info in CORRELATION_GROUPS.items():
        members = ", ".join(info["members"])
        print(f"{name:<25} {members:<40} {info['max_positions']:<10} {info['max_heat_pct']}%")

    print(f"\nTotal groups: {len(CORRELATION_GROUPS)}")
    print(f"Total tracked symbols: {len(_SYMBOL_TO_GROUP)}")

    # Check positions if requested
    if args.check:
        telemetry_file = DATA_DIR / "telemetry_snapshot.json"
        if telemetry_file.exists():
            try:
                with open(telemetry_file) as f:
                    telem = json.load(f)
                positions = []
                for pos in telem.get("open_positions", []):
                    positions.append(PositionState(
                        symbol=pos.get("symbol", ""),
                        qty=pos.get("qty", 0),
                        entry_price=pos.get("entry_price", 0),
                        unrealized_pnl=pos.get("unrealized_pnl", 0),
                    ))
                equity = telem.get("equity", 10000.0)
                report = analyze_portfolio_correlation(positions, equity)
                print(f"\nPortfolio Analysis ({len(positions)} positions):")
                if report.violations:
                    print("  VIOLATIONS:")
                    for v in report.violations:
                        print(f"    {v['group']}: {v['type']} — {v}")
                else:
                    print("  No violations.")
                print("\nHeadroom:")
                for group, hr in report.headroom.items():
                    print(f"  {group}: {hr['positions_available']} pos avail, "
                          f"{hr['heat_available_pct']}% heat avail")
            except Exception as e:
                print(f"  Error reading telemetry: {e}")
        else:
            print("\n  No telemetry snapshot found.")

    # Generate config gates
    gates = generate_config_gates()
    print(f"\nConfig gates generated for {len(gates)} symbols")


if __name__ == "__main__":
    main()
