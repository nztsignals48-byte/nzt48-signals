"""Hedge executor — subscribes hedge.recommendation, places paper hedge orders.

Closes Sarah Mitchell's "detection without execution" gap. When tail_hedge_overlay
publishes a recommendation (SDS/SH/QID), this service submits a real paper order
via the existing orders.submit NATS pipeline.

Orders are tagged strategy="TailHedgeOverlay" so the capital bandit + compounding
tracker see them as a separate strategy.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid

try:
    from nats.aio.client import Client as NATS
except ImportError:
    NATS = None


log = logging.getLogger("hedge-exec")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# Instrument metadata — known IBKR con_ids for major inverse ETFs.
# These are SMART-routed US ETFs; fallback to live qualification if unknown.
HEDGE_INSTRUMENTS = {
    "SH":   {"con_id": 29717852, "currency": "USD", "exchange": "SMART", "desc": "ProShares Short S&P 500 (1x)"},
    "PSQ":  {"con_id": 29717806, "currency": "USD", "exchange": "SMART", "desc": "ProShares Short QQQ (1x)"},
    "SDS":  {"con_id": 30404638, "currency": "USD", "exchange": "SMART", "desc": "ProShares UltraShort S&P 500 (2x)"},
    "QID":  {"con_id": 30404624, "currency": "USD", "exchange": "SMART", "desc": "ProShares UltraShort QQQ (2x)"},
}


class HedgeState:
    """Tracks current hedge position so we only trade deltas."""

    def __init__(self):
        self.current_usd: dict[str, float] = {}  # symbol -> usd value held
        self.last_trade_ts: float = 0.0
        self.daily_notional_used: float = 0.0
        self.daily_window_start: float = 0.0

    def register(self, symbol: str, usd: float):
        self.current_usd[symbol] = usd

    def delta_needed(self, symbol: str, target_usd: float) -> float:
        current = self.current_usd.get(symbol, 0.0)
        return target_usd - current

    def reset_daily(self):
        now = time.time()
        if now - self.daily_window_start > 86400:
            self.daily_notional_used = 0.0
            self.daily_window_start = now


class HedgeExecutor:
    DAILY_NOTIONAL_CAP_USD = 3000.0  # ties to frozen 30% cap on $10k equity
    MIN_TRADE_NOTIONAL_USD = 100.0
    COOLDOWN_S = 60.0

    def __init__(self):
        self.state = HedgeState()
        self.nc = None
        self.last_recommendation_ts = 0.0

    async def connect(self, url: str):
        self.nc = NATS()
        await self.nc.connect(servers=[url])
        log.info("connected to NATS %s", url)

    async def on_recommendation(self, msg):
        try:
            data = json.loads(msg.data)
        except Exception as e:
            log.warning("bad payload: %s", e)
            return

        symbol = data.get("symbol")
        target_usd = float(data.get("size_usd", 0))
        urgency = data.get("urgency", "low")
        rationale = data.get("rationale", "")

        if not symbol or symbol not in HEDGE_INSTRUMENTS:
            log.info("ignore: %s not in supported hedges %s", symbol, list(HEDGE_INSTRUMENTS))
            return

        now = time.time()
        if now - self.last_recommendation_ts < self.COOLDOWN_S:
            log.info("cooldown active, skip")
            return

        self.state.reset_daily()
        delta = self.state.delta_needed(symbol, target_usd)

        if abs(delta) < self.MIN_TRADE_NOTIONAL_USD:
            log.info("delta too small: %s $%.2f", symbol, delta)
            return

        if self.state.daily_notional_used + abs(delta) > self.DAILY_NOTIONAL_CAP_USD:
            log.warning("daily notional cap reached ($%.0f), skip", self.DAILY_NOTIONAL_CAP_USD)
            return

        side = "BUY" if delta > 0 else "SELL"
        # Fake reference price (executor will use MKT): $20-30 for major inverse ETFs
        reference_px = 25.0
        qty = max(1, int(abs(delta) / reference_px))

        meta = HEDGE_INSTRUMENTS[symbol]
        order = {
            "signal_id": f"hedge_{uuid.uuid4().hex[:8]}",
            "ticker": symbol,
            "exchange": meta["exchange"],
            "currency": meta["currency"],
            "con_id": meta["con_id"],
            "side": side,
            "qty": qty,
            "order_type": "MKT",
            "strategy": "TailHedgeOverlay",
            "account": os.environ.get("IBKR_ACCOUNT", "DUM983136"),
            "conviction": 1.0,
            "rationale": f"[hedge {urgency}] {rationale}",
            "hedge_target_usd": target_usd,
        }
        await self.nc.publish("orders.submit", json.dumps(order).encode())
        self.state.daily_notional_used += abs(delta)
        self.last_recommendation_ts = now

        # Track intent — actual fill will confirm later via orders.filled
        self.state.register(symbol, target_usd)
        log.info("placed hedge: %s %s qty=%d target=$%.0f (%s)", side, symbol, qty, target_usd, urgency)

    async def run(self):
        nats_url = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
        await self.connect(nats_url)
        await self.nc.subscribe("hedge.recommendation", cb=self.on_recommendation)
        log.info("listening on hedge.recommendation")
        while True:
            await asyncio.sleep(60)


async def main():
    he = HedgeExecutor()
    await he.run()


if __name__ == "__main__":
    if NATS is None:
        log.error("nats-py required")
    else:
        asyncio.run(main())
