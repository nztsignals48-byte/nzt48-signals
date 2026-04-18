"""signal_to_order_bridge — V5 signal→order router.

Subscribes NATS `signals.core` (emitted by engine.loop._open_position),
enforces final risk gates (dedupe, max_concurrent, portfolio heat),
and emits `orders.submit` which paper_executor.py translates to real
IBKR paper placeOrder calls.

Design:
  * One-to-one: each signal → one order. No netting.
  * Dedupe: same ticker within `dedupe_window_s` drops duplicate signals.
  * max_concurrent enforced here (engine's in-memory count isn't authoritative).
  * Size calculation: Kelly * equity * conviction, capped by min_position_gbp.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Set, Tuple


def _load_contract_map() -> Dict[str, Dict]:
    """Parse contracts.toml into a symbol -> metadata map for con_id lookup."""
    path = Path("/Users/rr/aegis-v5/config/contracts.toml")
    if not path.exists():
        return {}
    text = path.read_text()
    blocks = re.split(r"\[\[contracts\]\]", text)
    out: Dict[str, Dict] = {}
    for b in blocks[1:]:
        data: Dict[str, object] = {}
        for line in b.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'(\w+)\s*=\s*"?([^"]+)"?\s*(#.*)?$', line)
            if not m:
                continue
            key, val = m.group(1), m.group(2).strip()
            if key in ("con_id",):
                try: val = int(val)
                except: continue
            elif val in ("true", "false"):
                val = (val == "true")
            data[key] = val
        sym = data.get("symbol")
        if sym:
            out[str(sym)] = data
    return out


CONTRACT_MAP = _load_contract_map()

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@dataclass
class SignalToOrderBridge:
    nats_url: str = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    account: str = os.environ.get("IBKR_ACCOUNT", "DUM983136")
    equity_gbp: float = 100_000.0
    kelly_fraction: float = 0.20         # Option C: 0.10 → 0.20
    min_position_gbp: float = 1_500.0    # v5: £5k → £1.5k for broader trade spread
    max_position_gbp: float = 30_000.0   # Option C
    max_concurrent: int = 100            # v5: 20 → 100 (matches IBKR live slots)
    dedupe_window_s: float = 30.0
    min_conviction: float = 0.60

    open_tickers: Set[str] = field(default_factory=set)
    last_sig_ts: Dict[str, float] = field(default_factory=dict)
    news_alpha: Dict[str, tuple] = field(default_factory=dict)  # ticker -> (delta_pp, impact, ts)
    news_decay_s: float = 600.0
    regime_size_boost: float = 1.0
    regime_state: str = "calm"
    # Per-ticker VPIN (Volume-Synchronised Probability of Informed Trading).
    # vpin > vpin_veto_threshold blocks BUY signals (HFT-sweep defence).
    vpin_by_ticker: Dict[str, float] = field(default_factory=dict)
    vpin_veto_threshold: float = 0.7
    # Whitelisted (ticker, strategy) tuples cleared by reentry_manager.
    # On match we bypass dedupe + open_tickers gates ONCE.
    reentry_whitelist: Set[Tuple[str, str]] = field(default_factory=set)

    async def run(self) -> None:
        import nats  # type: ignore

        nc = await nats.connect(self.nats_url, name="aegis-v5-signal-to-order")
        log.info("signal-to-order bridge connected to NATS %s", self.nats_url)
        log.info("account=%s max_concurrent=%d min_conv=%.2f",
                 self.account, self.max_concurrent, self.min_conviction)

        # Track fills to know which tickers are "live" on the broker.
        async def on_fill(msg):
            try:
                p = json.loads(msg.data)
                t = p.get("ticker")
                side = (p.get("side") or "").upper()
                if side == "BUY" and t:
                    self.open_tickers.add(t)
                elif side == "SELL" and t:
                    self.open_tickers.discard(t)
            except Exception as e:
                log.warning("fill parse failed: %s", e)

        async def on_reject(msg):
            try:
                p = json.loads(msg.data)
                log.warning("broker rejected %s: %s", p.get("ticker"), p.get("reason"))
            except Exception:
                pass

        await nc.subscribe("orders.filled", cb=on_fill)
        await nc.subscribe("orders.reject", cb=on_reject)

        async def on_news_alpha(msg):
            try:
                a = json.loads(msg.data)
                t = a.get("ticker")
                if t:
                    self.news_alpha[t] = (
                        float(a.get("conviction_delta_pp", 0)),
                        float(a.get("impact_magnitude", 0)),
                        time.time(),
                    )
            except Exception:
                pass

        await nc.subscribe("news.alpha", cb=on_news_alpha)

        async def on_equity(msg):
            try:
                e = json.loads(msg.data)
                if "equity_gbp" in e:
                    self.equity_gbp = float(e["equity_gbp"])  # compounding
            except Exception:
                pass

        await nc.subscribe("portfolio.equity", cb=on_equity)

        async def on_regime(msg):
            try:
                r = json.loads(msg.data)
                self.regime_size_boost = float(r.get("size_boost") or 1.0)
                self.regime_state = r.get("state") or "calm"
            except Exception:
                pass

        await nc.subscribe("risk.regime", cb=on_regime)

        async def on_vpin(msg):
            try:
                d = json.loads(msg.data)
                t = d.get("ticker")
                if t:
                    self.vpin_by_ticker[t] = float(d.get("vpin") or 0)
            except Exception:
                pass

        await nc.subscribe("regime.vpin.*", cb=on_vpin)

        async def on_reentry(msg):
            try:
                r = json.loads(msg.data)
                t = r.get("ticker")
                s = r.get("strategy")
                if t and s:
                    self.reentry_whitelist.add((t, s))
                    log.info("REENTRY whitelisted %s/%s", t, s)
            except Exception:
                pass
        await nc.subscribe("reentry.allowed", cb=on_reentry)

        submitted = 0
        rejected_local = 0
        async def on_signal(msg):
            nonlocal submitted, rejected_local
            try:
                s = json.loads(msg.data)
            except Exception as e:
                log.warning("bad signal: %s", e)
                return
            ticker = s.get("ticker")
            conv = float(s.get("conviction_score", 0))
            fv = s.get("feature_vector") or {}
            # Prefer signal-provided con_id; fall back to contracts.toml lookup.
            con_id = int(fv.get("con_id") or 0)
            meta = CONTRACT_MAP.get(ticker or "", {})
            if con_id == 0 and meta.get("con_id"):
                con_id = int(meta["con_id"])
            exch = s.get("exchange") or meta.get("exchange") or "SMART"
            currency = fv.get("currency") or meta.get("currency") or "USD"
            fill_px = float(s.get("expected_fill_price") or 0)
            strategy = s.get("strategy_name", "?")

            log.info("RECV signal: %s conv=%.2f px=%.2f con_id=%s strategy=%s",
                     ticker, conv, fill_px, con_id, strategy)
            # Reentry whitelist: if present, skip dedupe + open-ticker gates ONCE.
            reentry_key = (ticker, strategy)
            is_reentry = reentry_key in self.reentry_whitelist
            if is_reentry:
                self.reentry_whitelist.discard(reentry_key)
                log.info("  REENTRY bypass for %s/%s", ticker, strategy)
            # Local risk gates:
            now = time.time()
            last_t = self.last_sig_ts.get(ticker or "", 0)
            if not is_reentry and ticker and (now - last_t) < self.dedupe_window_s:
                rejected_local += 1
                log.info("  REJECT %s: dedupe (within %.0fs)", ticker, self.dedupe_window_s)
                return  # dedupe
            # LLM news alpha: apply decayed conviction delta if we have fresh news for this ticker.
            news_delta_pp = 0.0
            if ticker in self.news_alpha:
                delta_pp, impact, ts = self.news_alpha[ticker]
                age = now - ts
                if age < self.news_decay_s:
                    decay = max(0.0, 1.0 - age / self.news_decay_s)
                    news_delta_pp = delta_pp * decay * impact
                    conv = max(0.0, min(1.0, conv + news_delta_pp / 100.0))
                    s["conviction_score"] = conv  # propagate adjusted conv

            if conv < self.min_conviction:
                rejected_local += 1
                log.info("  REJECT %s: conv %.2f < floor %.2f", ticker, conv, self.min_conviction)
                return

            # VPIN veto: block BUYs when order flow is toxic (HFT sweep).
            side = (s.get("side") or "BUY").upper()
            vpin = self.vpin_by_ticker.get(ticker or "", 0.0)
            if side == "BUY" and vpin > self.vpin_veto_threshold:
                rejected_local += 1
                log.info("  REJECT %s: VPIN %.2f > %.2f (toxic flow)",
                         ticker, vpin, self.vpin_veto_threshold)
                return
            if ticker in self.open_tickers and not is_reentry:
                rejected_local += 1
                log.info("  REJECT %s: already open on broker", ticker)
                return  # already live on broker
            if len(self.open_tickers) >= self.max_concurrent:
                rejected_local += 1
                log.info("  REJECT %s: max_concurrent=%d reached", ticker, self.max_concurrent)
                return

            if not ticker or not con_id:
                rejected_local += 1
                log.info("  REJECT %s: missing ticker/con_id (px=%s con_id=%s)",
                         ticker, fill_px, con_id)
                return
            if fill_px <= 0:
                fill_px = 1.0  # MKT-order sentinel; IBKR determines fill

            self.last_sig_ts[ticker] = now

            # Size: Kelly × conviction × regime_boost
            raw_size = self.equity_gbp * self.kelly_fraction * conv * self.regime_size_boost
            size_gbp = max(self.min_position_gbp, min(self.max_position_gbp, raw_size))
            sizing_px = fill_px if fill_px > 5.0 else 100.0
            qty = max(1, min(1500, int(size_gbp / sizing_px)))  # cap at 1500 shares

            order = {
                "signal_id": s.get("signal_id"),
                "ticker": ticker,
                "exchange": exch,
                "currency": currency,
                "con_id": con_id,
                "side": "BUY",
                "qty": qty,
                "order_type": "MKT",
                "strategy": strategy,
                "account": self.account,
                "conviction": conv,
            }
            await nc.publish("orders.submit", json.dumps(order).encode("utf-8"))
            # positions.open — lets rotator know this ticker is held (do not evict)
            try:
                await nc.publish("positions.open", json.dumps({
                    "ts": time.time(),
                    "ticker": ticker,
                    "con_id": con_id,
                    "qty": qty,
                    "strategy": strategy,
                    "signal_id": order.get("signal_id"),
                }).encode("utf-8"))
            except Exception:
                pass
            submitted += 1
            if submitted % 10 == 0 or submitted <= 5:
                log.info(
                    "submit %d: %s %s qty=%d conv=%.2f strategy=%s (open=%d/%d rejected_local=%d)",
                    submitted, order["side"], ticker, qty, conv, strategy,
                    len(self.open_tickers), self.max_concurrent, rejected_local,
                )

        await nc.subscribe("signals.core", cb=on_signal)
        log.info("listening on signals.core")

        while True:
            await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(SignalToOrderBridge().run())
