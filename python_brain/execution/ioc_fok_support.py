"""IOC / FOK order type support helpers.

Extends paper_executor: signals can specify order_type=IOC (immediate-or-cancel)
or FOK (fill-or-kill). Converts to IBKR's tif + order_type combination.

Consumed by paper_executor.py when building Order object.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OrderSpec:
    ibkr_order_type: str    # MKT / LMT
    tif: str                # DAY / IOC / FOK / GTC
    outside_rth: bool


def translate(order_type: str) -> OrderSpec:
    """Translate signal's order_type string to IBKR parameters."""
    ot = order_type.upper()
    if ot == "IOC":
        return OrderSpec(ibkr_order_type="MKT", tif="IOC", outside_rth=False)
    if ot == "LMT_IOC":
        return OrderSpec(ibkr_order_type="LMT", tif="IOC", outside_rth=False)
    if ot == "FOK":
        return OrderSpec(ibkr_order_type="MKT", tif="FOK", outside_rth=False)
    if ot == "LMT_FOK":
        return OrderSpec(ibkr_order_type="LMT", tif="FOK", outside_rth=False)
    if ot == "MARKETABLE_LIMIT":
        return OrderSpec(ibkr_order_type="LMT", tif="DAY", outside_rth=False)
    if ot == "LMT":
        return OrderSpec(ibkr_order_type="LMT", tif="DAY", outside_rth=True)
    if ot == "GTC_LMT":
        return OrderSpec(ibkr_order_type="LMT", tif="GTC", outside_rth=True)
    return OrderSpec(ibkr_order_type="MKT", tif="DAY", outside_rth=True)


def is_aggressive(order_type: str) -> bool:
    """True if order is expected to execute quickly (MKT/IOC/FOK/marketable)."""
    return order_type.upper() in ("MKT", "IOC", "FOK", "MARKETABLE_LIMIT", "LMT_IOC", "LMT_FOK")


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        for ot in ["MKT", "IOC", "LMT_FOK", "LMT", "MARKETABLE_LIMIT", "GTC_LMT"]:
            s = translate(ot)
            print(f"{ot:18s} -> ibkr={s.ibkr_order_type:3s} tif={s.tif:3s} outside_rth={s.outside_rth}")
        print("OK")
