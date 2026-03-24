"""Q-051 — Unified Cost Model for Python-side modules.

Single source of truth: reads [costs] from config/config.toml.
All Python code that needs cost parameters MUST use this module
instead of hardcoding values.

Usage:
    from python_brain.ouroboros.cost_model import costs, total_round_trip_cost

    # Access individual parameters
    costs.round_trip_fee_pct   # 0.003
    costs.ibkr_commission_gbp  # 1.70

    # Compute total cost for a trade
    total = total_round_trip_cost(position_gbp=2000, is_fx=True)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("ouroboros.cost_model")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))


@dataclass(frozen=True)
class CostModel:
    """Immutable cost parameters from config.toml [costs] section."""
    round_trip_fee_pct: float = 0.003    # 0.3% round-trip
    ibkr_commission_gbp: float = 1.70    # IBKR tiered minimum UK
    stamp_duty_pct: float = 0.0          # 0% for ETPs
    ftt_pct: float = 0.0                 # 0% for UK ISA ETPs
    fx_conversion_pct: float = 0.002     # 0.2% FX spread
    # Derived from [risk] section (kept here for convenience)
    spread_veto_pct: float = 0.003       # 0.3%
    min_gross_edge_pct: float = 0.0015   # 0.15%
    slippage_pct: float = 0.005          # 0.5%
    # Per-exchange round-trip cost overrides (from [costs.per_exchange])
    per_exchange_rt: tuple = ()           # Stored as tuple of (exchange, pct) pairs

    @classmethod
    def from_toml(cls, config_dir: Optional[Path] = None) -> "CostModel":
        """Load from config.toml [costs] section."""
        cfg_dir = config_dir or CONFIG_DIR
        toml_path = cfg_dir / "config.toml"
        if not toml_path.exists():
            log.warning("config.toml not found at %s — using defaults", toml_path)
            return cls()

        try:
            import tomli
        except ImportError:
            try:
                import tomllib as tomli  # Python 3.11+
            except ImportError:
                log.warning("No TOML parser available — using defaults")
                return cls()

        try:
            with open(toml_path, "rb") as f:
                config = tomli.load(f)
        except Exception as e:
            log.error("Failed to parse config.toml: %s — using defaults", e)
            return cls()

        costs = config.get("costs", {})
        risk = config.get("risk", {})

        # Per-exchange round-trip costs
        per_exch_raw = costs.get("per_exchange", {})
        per_exch = tuple(sorted(per_exch_raw.items()))

        return cls(
            round_trip_fee_pct=costs.get("round_trip_fee_pct", 0.003),
            ibkr_commission_gbp=costs.get("ibkr_commission_gbp", 1.70),
            stamp_duty_pct=costs.get("stamp_duty_pct", 0.0),
            ftt_pct=costs.get("ftt_pct", 0.0),
            fx_conversion_pct=costs.get("fx_conversion_pct", 0.002),
            spread_veto_pct=risk.get("spread_veto_pct", 0.003),
            min_gross_edge_pct=risk.get("min_gross_edge_pct", 0.0015),
            slippage_pct=risk.get("slippage_assumption_pct", 0.005),
            per_exchange_rt=per_exch,
        )


def total_round_trip_cost(
    position_gbp: float,
    spread_pct: float = 0.0,
    is_fx: bool = False,
    model: Optional[CostModel] = None,
) -> float:
    """Compute total round-trip cost in GBP for a given position size.

    Components:
      1. Commission (2× entry + exit)
      2. Spread cost (bid-ask)
      3. FX conversion (if USD-denominated ETP)
      4. Stamp duty (0 for ETPs)
      5. FTT (0 for UK ISA ETPs)
    """
    m = model or costs

    commission = m.ibkr_commission_gbp * 2  # Entry + exit
    spread_cost = position_gbp * spread_pct  # One-way spread (bid-ask half)
    fx_cost = position_gbp * m.fx_conversion_pct * 2 if is_fx else 0.0
    stamp = position_gbp * m.stamp_duty_pct
    ftt = position_gbp * m.ftt_pct

    return commission + spread_cost + fx_cost + stamp + ftt


def cost_drag_annual(
    trades_per_day: float,
    avg_position_gbp: float,
    avg_spread_pct: float = 0.002,
    is_fx: bool = True,
    model: Optional[CostModel] = None,
) -> float:
    """Estimate annual cost drag in GBP.

    Args:
        trades_per_day: average daily trade count
        avg_position_gbp: average position size
        avg_spread_pct: average bid-ask spread (one-way)
        is_fx: whether trades involve FX conversion
        model: cost model instance

    Returns:
        Estimated annual cost in GBP (252 trading days)
    """
    per_trade = total_round_trip_cost(avg_position_gbp, avg_spread_pct, is_fx, model)
    return per_trade * trades_per_day * 252


def exchange_round_trip_pct(exchange: str, model: Optional[CostModel] = None) -> float:
    """Get exchange-specific round-trip cost, falling back to default."""
    m = model or costs
    per_exch = dict(m.per_exchange_rt)
    return per_exch.get(exchange, m.round_trip_fee_pct)


# Exchanges that require FX conversion from GBP perspective
_FX_EXCHANGES = {"US", "TSE", "HKEX", "SGX", "XETRA", "EURONEXT"}


def estimate_trade_cost(
    position_gbp: float,
    exchange: str = "",
    spread_at_entry_pct: float = 0.0,
    spread_at_exit_pct: float = 0.0,
    model: Optional[CostModel] = None,
) -> float:
    """Estimate realistic cost for a single trade in GBP.

    Components:
      1. Commission: 2x ibkr_commission_gbp (entry + exit)
      2. Slippage: position_gbp * slippage_pct
      3. FX: position_gbp * fx_conversion_pct * 2 (if non-GBP exchange)
      4. Spread: uses WAL-recorded spread if available, else per-exchange estimate

    Returns total cost in GBP (always positive).
    """
    m = model or costs
    commission = m.ibkr_commission_gbp * 2
    slippage = position_gbp * m.slippage_pct / 100.0  # slippage_pct is in % (e.g. 0.5 = 0.5%)
    is_fx = exchange in _FX_EXCHANGES
    fx_cost = position_gbp * m.fx_conversion_pct * 2 if is_fx else 0.0

    # Spread cost: use WAL data if available, else fall back to per-exchange estimate
    if spread_at_entry_pct > 0 or spread_at_exit_pct > 0:
        spread_cost = position_gbp * (spread_at_entry_pct + spread_at_exit_pct) / 200.0
    else:
        # Use half the per-exchange round-trip as spread estimate
        rt_pct = exchange_round_trip_pct(exchange, m)
        spread_cost = position_gbp * rt_pct / 2.0

    return commission + slippage + fx_cost + spread_cost


# Module-level singleton — loaded once on import
costs = CostModel.from_toml()


if __name__ == "__main__":
    import json
    from dataclasses import asdict

    model = CostModel.from_toml()
    print("Q-051 UNIFIED COST MODEL")
    print("=" * 50)
    for k, v in asdict(model).items():
        if isinstance(v, float) and v < 1:
            print(f"  {k:<25s} = {v:.4f} ({v*100:.2f}%)")
        else:
            print(f"  {k:<25s} = {v}")

    print()
    print("COST SCENARIOS:")
    for size in [1000, 2000, 5000]:
        cost = total_round_trip_cost(size, spread_pct=0.002, is_fx=True, model=model)
        print(f"  {size} GBP position (FX): {cost:.2f} GBP round-trip ({cost/size*100:.2f}%)")

    print()
    for trades in [1, 2, 3]:
        drag = cost_drag_annual(trades, 2000, 0.002, True, model)
        equity = 10000
        print(f"  {trades} trades/day: {drag:.0f} GBP/year ({drag/equity*100:.1f}% of {equity} equity)")
