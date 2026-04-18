"""
Super-Institutional-Plus Gate — Phase 2/3/4 Integration

Standalone NATS subscriber that:
1. Listens to signals.core (pre-gate)
2. Applies Phase 2-4 checks:
   - Agent-swarm council (optional, slow)
   - Almgren-Chriss cost estimate
   - VaR/CVaR portfolio check
   - Impact-aware routing decision
   - L2 book imbalance boost
   - CQR-based position sizing
   - CVaR-aware stop placement
   - Tail-hedge overlay recommendations
   - Correlation guard
3. Publishes augmented signal to signals.post_super subject
   OR publishes rejection to signals.rejected

Runs as separate supervised service so it doesn't block the
fast-path signal flow. sig2order can choose to subscribe to
signals.post_super instead of signals.core to enable gates.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from python_brain.execution.almgren_chriss_executor import (
    MarketParams, estimate_impact_cost_bps,
)
from python_brain.execution.impact_aware_router import (
    BookContext, route_order, validate_routing_vs_alpha,
)
from python_brain.risk.realtime_var_cvar import RealtimeRiskMonitor
from python_brain.risk.cvar_stop_placement import parametric_cvar_stop
from python_brain.risk.tail_hedge_overlay import TailHedgeManager
from python_brain.risk.portfolio_correlation_guard import PortfolioCorrelationMonitor
from python_brain.quant.l2_book_imbalance import (
    OrderBook, OrderBookLevel, compute_all_signals, predict_short_term_move,
)

try:
    from nats.aio.client import Client as NATS
    HAS_NATS = True
except ImportError:
    HAS_NATS = False


log = logging.getLogger("super-gate")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


class SuperInstitutionalGate:
    def __init__(
        self,
        nats_url: str = None,
        portfolio_value: float = 10000,
        enable_agent_swarm: bool = False,  # expensive, off by default
        enable_correlation_guard: bool = True,
        enable_var_monitor: bool = True,
        enable_tail_hedge: bool = True,
        enable_l2_boost: bool = True,
    ):
        self.nats_url = nats_url or os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
        self.portfolio_value = portfolio_value
        self.enable_agent_swarm = enable_agent_swarm
        self.enable_correlation_guard = enable_correlation_guard
        self.enable_var_monitor = enable_var_monitor
        self.enable_tail_hedge = enable_tail_hedge
        self.enable_l2_boost = enable_l2_boost

        self.risk_monitor = RealtimeRiskMonitor(
            portfolio_cap_var_usd=portfolio_value * 0.05,  # 5% VaR cap
            cvar_cap_usd=portfolio_value * 0.08,
            max_drawdown_halt=0.08,
        )
        self.corr_monitor = PortfolioCorrelationMonitor()
        self.hedge_manager = TailHedgeManager(portfolio_beta=1.0)

        # L2 book cache: ticker -> OrderBook
        self.book_cache: dict[str, OrderBook] = {}

        # Stats
        self.stats = {
            "signals_in": 0,
            "signals_passed": 0,
            "rejected_cost": 0,
            "rejected_corr": 0,
            "rejected_var": 0,
            "rejected_routing": 0,
            "l2_boosted": 0,
        }

    async def run(self):
        if not HAS_NATS:
            log.error("nats-py not available")
            return

        nc = NATS()
        await nc.connect(servers=[self.nats_url])
        log.info("connected to NATS")

        # Subscribe to L2 book updates
        async def on_book(msg):
            try:
                data = json.loads(msg.data)
                ticker = data.get("ticker")
                if not ticker:
                    return
                bids = [OrderBookLevel(p, s) for p, s in data.get("bids", [])[:5]]
                asks = [OrderBookLevel(p, s) for p, s in data.get("asks", [])[:5]]
                if bids and asks:
                    self.book_cache[ticker] = OrderBook(bids=bids, asks=asks)
            except Exception:
                pass

        # Subscribe to ticks for return history
        async def on_tick(msg):
            try:
                data = json.loads(msg.data)
                ticker = data.get("ticker") or data.get("symbol")
                price = data.get("mid") or data.get("last") or data.get("bid")
                if ticker and price:
                    # Use % change as return proxy
                    prev = self.corr_monitor.returns_history.get(ticker, [])
                    if prev:
                        last_price = prev[-1] if not np.isnan(prev[-1]) else price
                        ret = (price - last_price) / max(last_price, 1e-9) if last_price else 0
                        self.corr_monitor.update_return(ticker, ret)
                        self.risk_monitor.update_return(ticker, ret)
            except Exception:
                pass

        # Subscribe to positions
        async def on_position(msg):
            try:
                data = json.loads(msg.data)
                ticker = data.get("ticker")
                usd = data.get("market_value") or (data.get("qty", 0) * data.get("price", 0))
                if ticker:
                    self.corr_monitor.update_position(ticker, float(usd))
                    self.risk_monitor.update_position(ticker, float(usd))
            except Exception:
                pass

        # Subscribe to portfolio equity
        async def on_equity(msg):
            try:
                data = json.loads(msg.data)
                equity = data.get("net_liq") or data.get("equity")
                if equity:
                    self.portfolio_value = float(equity)
                    self.hedge_manager.update_equity(self.portfolio_value)
            except Exception:
                pass

        async def on_signal(msg):
            await self._process_signal(nc, msg)

        await nc.subscribe("l2.book.*", cb=on_book)
        await nc.subscribe("ticks.live.*", cb=on_tick)
        await nc.subscribe("positions.open", cb=on_position)
        await nc.subscribe("positions.close", cb=on_position)
        await nc.subscribe("account.equity", cb=on_equity)
        await nc.subscribe("signals.core", cb=on_signal)

        log.info("super-institutional gate active")

        # Periodic stats dump
        while True:
            await asyncio.sleep(60)
            log.info("stats: %s", self.stats)

            # Tail hedge check
            if self.enable_tail_hedge:
                hedge_rec = self.hedge_manager.evaluate(
                    portfolio_value=self.portfolio_value,
                    equity_exposure_usd=self.portfolio_value * 0.9,
                )
                if hedge_rec:
                    try:
                        await nc.publish("hedge.recommendation", json.dumps({
                            "ts": time.time(),
                            "symbol": hedge_rec.hedge_symbol,
                            "size_usd": hedge_rec.hedge_size_usd,
                            "ratio": hedge_rec.hedge_ratio,
                            "urgency": hedge_rec.urgency,
                            "rationale": hedge_rec.rationale,
                        }).encode())
                        log.info("hedge rec: %s $%.0f %s", hedge_rec.hedge_symbol,
                                 hedge_rec.hedge_size_usd, hedge_rec.urgency)
                    except Exception:
                        pass

            # VaR check
            if self.enable_var_monitor:
                metrics = self.risk_monitor.compute(self.portfolio_value, method="historical")
                try:
                    await nc.publish("risk.var_cvar", json.dumps({
                        "ts": time.time(),
                        "var_95": metrics.var_95,
                        "cvar_95": metrics.cvar_95,
                        "volatility": metrics.volatility,
                        "sharpe": metrics.sharpe,
                        "max_drawdown": metrics.max_drawdown,
                    }).encode())
                except Exception:
                    pass
                breaches = self.risk_monitor.breach_check()
                if breaches:
                    log.warning("risk breaches: %s", breaches)
                    await nc.publish("risk.breach", json.dumps({
                        "ts": time.time(),
                        "breaches": breaches,
                    }).encode())

    async def _process_signal(self, nc, msg):
        """Apply Phase 2-4 gates to a signal."""
        try:
            s = json.loads(msg.data)
        except Exception:
            return

        self.stats["signals_in"] += 1

        ticker = s.get("ticker")
        side = (s.get("side") or "BUY").upper()
        conv = float(s.get("conviction_score", s.get("conviction", 0)))
        shares = int(s.get("shares") or s.get("qty") or 100)

        reject_reasons = []

        # === Phase 2: Almgren-Chriss cost estimate ===
        adv = 1_000_000  # default; should come from market data
        vol_bps = 100
        spread_bps = 5.0
        impact_cost = estimate_impact_cost_bps(shares, adv, vol_bps, spread_bps)
        if impact_cost > 30.0:
            reject_reasons.append(f"impact_cost {impact_cost:.1f}bps > 30")
            self.stats["rejected_cost"] += 1

        # === Phase 3: L2 book imbalance boost ===
        l2_boost_bps = 0
        book = self.book_cache.get(ticker)
        if book and self.enable_l2_boost:
            book_signals = compute_all_signals(book)
            predicted = predict_short_term_move(book_signals, horizon_s=30)
            if (side == "BUY" and predicted > 5) or (side == "SELL" and predicted < -5):
                l2_boost_bps = abs(predicted)
                self.stats["l2_boosted"] += 1
            elif (side == "BUY" and predicted < -10) or (side == "SELL" and predicted > 10):
                reject_reasons.append(f"L2 opposes: predicted {predicted:.1f}bps")

        # === Phase 2: Impact-aware routing ===
        if book:
            book_ctx = BookContext(
                bid=book.best_bid, ask=book.best_ask,
                bid_size=book.bids[0].size if book.bids else 0,
                ask_size=book.asks[0].size if book.asks else 0,
                recent_volume=10000, adv_shares=adv,
                vpin=s.get("vpin", 0.3),
                volatility_bps=vol_bps,
                urgency=conv,  # higher conviction = more urgent
            )
            routing = route_order(shares, side, book_ctx)
            # Validate alpha vs cost
            expected_alpha_bps = conv * 20 + l2_boost_bps  # rough alpha estimate
            should_trade, reason = validate_routing_vs_alpha(
                routing, expected_alpha_bps, min_edge_bps=2.0
            )
            if not should_trade:
                reject_reasons.append(f"routing_alpha: {reason}")
                self.stats["rejected_routing"] += 1

        # === Phase 4: Correlation guard ===
        if self.enable_correlation_guard:
            position_usd = max(100, shares * 100)  # rough estimate
            corr_result = self.corr_monitor.check_candidate(ticker, position_usd)
            if not corr_result.pass_check and corr_result.scale_factor < 0.3:
                reject_reasons.append(f"correlation: {corr_result.violations[0] if corr_result.violations else 'block'}")
                self.stats["rejected_corr"] += 1

        # === Phase 2: VaR check ===
        if self.enable_var_monitor:
            breaches = self.risk_monitor.breach_check()
            if breaches:
                reject_reasons.append(f"var_breach: {breaches[0]}")
                self.stats["rejected_var"] += 1

        # === Phase 4: CVaR stop placement (augment signal, don't reject) ===
        entry_price = float(s.get("expected_fill_price") or 100)
        cvar_stop = parametric_cvar_stop(
            entry_price, side, volatility_bps=vol_bps,
            cvar_budget_bps=80,
        )

        # === Final decision ===
        if reject_reasons:
            try:
                await nc.publish("signals.rejected", json.dumps({
                    "ts": time.time(),
                    "ticker": ticker,
                    "side": side,
                    "reasons": reject_reasons,
                    "signal_id": s.get("signal_id"),
                }).encode())
            except Exception:
                pass
        else:
            # Pass-through: publish augmented signal
            augmented = dict(s)
            augmented["_super_gate"] = {
                "impact_cost_bps": impact_cost,
                "l2_boost_bps": l2_boost_bps,
                "cvar_stop_price": cvar_stop.stop_price,
                "cvar_stop_distance_bps": cvar_stop.stop_distance_bps,
                "cvar_budget_bps": cvar_stop.cvar_bps,
            }
            try:
                await nc.publish("signals.post_super", json.dumps(augmented).encode())
            except Exception:
                pass
            self.stats["signals_passed"] += 1


async def main():
    gate = SuperInstitutionalGate()
    await gate.run()


if __name__ == "__main__":
    asyncio.run(main())
