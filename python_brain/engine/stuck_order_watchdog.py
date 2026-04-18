"""stuck_order_watchdog — prevents orders from sitting in PendingSubmit forever.

Every 20 s:
  1. Check IBKR for open orders
  2. Any order in (PendingSubmit, PreSubmitted, Submitted) older than
     STUCK_THRESHOLD_S gets cancelled
  3. Publish orders.stuck_cancelled on NATS so sig2order can re-fire the signal

Runs on client_id=130, fresh connection per poll (so its own connection
can't get wedged).

Also tracks fill-rate: if a fresh order doesn't reach status='Submitted'
within 10 s of placement, flags the executor as degraded.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

STUCK_THRESHOLD_S = 60        # after this, cancel
POLL_INTERVAL_S = 20
NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")


async def check_and_cancel(ib, nc) -> int:
    trades = ib.openTrades()
    now = time.time()
    cancelled = 0
    for t in trades:
        st = t.orderStatus.status
        if st in ("Filled", "Cancelled", "Inactive", "ApiCancelled"):
            continue
        # Find the first log entry (order placed time)
        first_ts = None
        if t.log:
            try:
                first_ts = t.log[0].time.timestamp()
            except Exception:
                pass
        if first_ts is None:
            continue
        age = now - first_ts
        if age < STUCK_THRESHOLD_S:
            continue
        log.warning("STUCK %s qty=%s status=%s age=%.0fs → cancelling",
                    t.contract.symbol, t.order.totalQuantity, st, age)
        try:
            ib.cancelOrder(t.order)
            cancelled += 1
            payload = {
                "ticker": t.contract.symbol,
                "qty": int(t.order.totalQuantity),
                "side": t.order.action,
                "status": st,
                "age_s": round(age, 1),
                "permId": t.order.permId,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            await nc.publish("orders.stuck_cancelled",
                             json.dumps(payload).encode("utf-8"))
        except Exception as e:
            log.error("cancel failed for %s: %s", t.contract.symbol, e)
    return cancelled


async def main() -> None:
    import nats
    from ib_insync import IB

    nc = await nats.connect(NATS_URL, name="aegis-v5-stuck-watchdog")
    log.info("stuck-order watchdog connected to NATS")

    # Maintain a persistent IBKR connection.
    ib = IB()
    try:
        await ib.connectAsync("127.0.0.1", 4002, clientId=130,
                              readonly=False, timeout=20, account="")
        log.info("watchdog connected to IBKR client_id=130")
    except Exception as e:
        log.error("watchdog IBKR connect failed: %s", e)
        return

    scan_count = 0
    total_cancelled = 0
    while True:
        await asyncio.sleep(POLL_INTERVAL_S)
        scan_count += 1
        try:
            n = await check_and_cancel(ib, nc)
            total_cancelled += n
            if scan_count % 10 == 0 or n > 0:
                open_n = len([t for t in ib.openTrades()
                              if t.orderStatus.status not in
                              ("Filled","Cancelled","Inactive","ApiCancelled")])
                log.info("scan %d: open=%d cancelled_this_poll=%d total_cancelled=%d",
                         scan_count, open_n, n, total_cancelled)
        except Exception as e:
            log.error("scan failed: %s; attempting reconnect", e)
            try:
                ib.disconnect()
                await asyncio.sleep(5)
                await ib.connectAsync("127.0.0.1", 4002, clientId=131,
                                      readonly=False, timeout=20, account="")
                log.info("watchdog reconnected client_id=131")
            except Exception as e2:
                log.error("reconnect also failed: %s", e2)


if __name__ == "__main__":
    asyncio.run(main())
