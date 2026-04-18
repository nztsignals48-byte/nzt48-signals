"""Cancel stuck PendingSubmit orders on DUM983136.

Adds an on_cancel handler to the paper_executor pattern. Uses a fresh
client_id and just issues cancels for every open order not yet Filled.
"""
from __future__ import annotations

import asyncio
import sys


async def main() -> None:
    from ib_insync import IB
    ib = IB()
    await ib.connectAsync(
        "127.0.0.1", 4002, clientId=251, readonly=False, timeout=30,
    )
    print(f"connected, account={ib.managedAccounts()}")
    # Give ib_insync a moment to sync open orders.
    await asyncio.sleep(3)
    trades = ib.openTrades()
    print(f"open trades: {len(trades)}")
    for t in trades:
        c = t.contract
        st = t.orderStatus.status
        if st in ("Filled", "Cancelled", "Inactive"):
            continue
        print(f"  cancelling: {c.symbol} qty={t.order.totalQuantity} status={st}")
        ib.cancelOrder(t.order)
    await asyncio.sleep(5)
    # Refresh + report.
    trades = ib.openTrades()
    still = [(t.contract.symbol, t.orderStatus.status) for t in trades
             if t.orderStatus.status not in ("Filled", "Cancelled", "Inactive")]
    print(f"still-open after cancel: {still}")
    ib.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
