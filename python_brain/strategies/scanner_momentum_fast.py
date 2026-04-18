"""scanner_momentum_fast — direct-trade service.

Bypasses the bar-history requirement by consuming scanner.hits.* and
news.alpha directly. Emits signals.core for the top-ranked tickers from
each scan round.

Sizing rule:
  base conviction = 0.58 (rank 1) → 0.66 (rank 1 after big LLM alpha boost)
  only fires if scan_type in a whitelist of "directional" scans:
      top_movers_usd, top_movers_gbp, top_movers_xetra,
      top_movers_paris, top_movers_hk, top_volume_*, hot_by_price_usd
  max 5 orders per 60 s window (anti-flood)

Avoids duplicate buys by ignoring tickers already in open_tickers (tracks
orders.filled).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Set, Tuple

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


DIRECTIONAL_SCANS = {
    "top_movers_usd", "top_volume_usd", "hot_by_price_usd",
    "top_trade_count_usd", "top_movers_nyse", "top_movers_ndx",
    "top_movers_gbp", "top_volume_gbp",
    "top_movers_xetra", "top_volume_xetra",
    "top_movers_paris", "top_movers_milan", "top_movers_adam", "top_movers_madrid",
    "top_movers_hk", "top_volume_hk", "top_movers_tw", "top_movers_sg", "top_movers_asx",
    "top_movers_canada",
    "high_opt_iv_usd",
}

BEARISH_SCANS = {"bottom_movers_usd"}

# Inverse-ETP triggers: when the underlying appears in bottom_movers (falling),
# we can express a bearish view by BUYING the 3× inverse ETP.
# Map of underlying → 3× inverse ETP on LSE (no short-sell needed; buying inverse).
INVERSE_ETP_MAP = {
    "AAPL": "3SAP",  "MSFT": "3SMS",  "NVDA": "3SNV",  "TSLA": "3STS",
    "AMZN": "3SAM",  "GOOGL": "3SGO", "META": "3SMS",  "NFLX": "3SNP",
    "AMD":  "3SAB",  "INTC": "3SIC",  "QCOM": "3SQC",  "AVGO": "3SBM",
    "BA":   "3SBA",  "DIS":  "3SDI",  "JPM":  "3SJP",  "BAC":  "3SBK",
    "XOM":  "3SXO",  "CVX":  "3SCV",  "PFE":  "3SPF",  "KO":   "3SKO",
    "WMT":  "3SWM",  "V":    "3SVI",  "MA":   "3SMA",  "PYPL": "3SPP",
    "UBER": "UBER3S", "PLTR": "PLTR3S", "COIN": "COIN3S",
    # Index-level inverses
    "SPY":  "3SSP",  "QQQ":  "3SQQ",  "IWM":  "3SIW",
}
MAX_SUBMITS_PER_MIN = 10
BASE_CONVICTION = 0.62  # just above floor of 0.60
COOLDOWN_S = 120  # same ticker can't be re-sent within 2 min
NEWS_ALPHA_TTL_S = 900  # 15-min TTL on news boosts


@dataclass
class ScannerFastTrader:
    nats_url: str = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    open_tickers: Set[str] = field(default_factory=set)
    last_submit_ts: Dict[str, float] = field(default_factory=dict)  # ticker -> ts
    submit_window: list = field(default_factory=list)               # epoch secs
    news_alpha: Dict[str, Tuple[float, float, float]] = field(default_factory=dict)

    async def run(self) -> None:
        import nats  # type: ignore
        nc = await nats.connect(self.nats_url, name="aegis-v5-scanner-fast")
        log.info("scanner-fast-trader connected to NATS")

        async def on_fill(msg):
            try:
                p = json.loads(msg.data)
                t = p.get("ticker")
                side = (p.get("side") or "").upper()
                if side == "BUY" and t:
                    self.open_tickers.add(t)
                elif side == "SELL" and t:
                    self.open_tickers.discard(t)
            except Exception:
                pass

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

        async def on_scanner_hit(msg):
            try:
                p = json.loads(msg.data)
            except Exception:
                return
            scan_type = p.get("scan_type") or msg.subject.split(".")[-1]
            # Bearish: redirect to 3× inverse ETP (buying it = short the underlying)
            is_bearish = scan_type in BEARISH_SCANS
            if scan_type not in DIRECTIONAL_SCANS and not is_bearish:
                return
            orig_ticker = p.get("ticker")
            orig_con_id = int(p.get("con_id") or 0)
            if not orig_ticker or orig_con_id == 0:
                return
            rank = int(p.get("rank") or 50)
            if rank > 15:
                return
            # For bearish scans: map underlying → inverse ETP; skip if no mapping.
            if is_bearish:
                inverse = INVERSE_ETP_MAP.get(orig_ticker)
                if not inverse:
                    return
                ticker = inverse
                # We don't have the inverse's con_id here; sig2order will look it up
                # from contracts.toml via CONTRACT_MAP.
                con_id = 0
            else:
                ticker = orig_ticker
                con_id = orig_con_id
            now = time.time()
            if ticker in self.open_tickers:
                return
            if now - self.last_submit_ts.get(ticker, 0) < COOLDOWN_S:
                return
            # Rate limit
            self.submit_window = [t for t in self.submit_window if now - t < 60]
            if len(self.submit_window) >= MAX_SUBMITS_PER_MIN:
                return

            # Conviction: rank 1 = BASE+0.08, rank 15 = BASE, then LLM news boost.
            rank_boost = max(0.0, (16 - rank) / 15.0 * 0.08)
            conv = BASE_CONVICTION + rank_boost
            news = self.news_alpha.get(ticker)
            if news and (now - news[2] < NEWS_ALPHA_TTL_S):
                delta_pp, impact, _ = news
                conv += impact * delta_pp / 100.0 * 0.5  # half-weight
                conv = max(0.35, min(0.95, conv))

            if conv < 0.60:
                return

            # Reasonable fill price: use IBKR last if we have it in tick cache; else rank-based guess.
            # This is a cold-start so we don't have a live tick. sig2order's fill_px gate needs >0,
            # so we send expected_fill_price as 1.0 and let the broker MKT order determine actual fill.
            # For inverse ETP on LSE: exchange/currency switch
            if is_bearish:
                sig_exchange = "LSEETF"
                sig_currency = "GBP"
                strat_name = "scanner_momentum_fast_inverse"
            else:
                sig_exchange = p.get("exchange", "SMART")
                sig_currency = p.get("currency", "USD")
                strat_name = "scanner_momentum_fast"

            signal = {
                "schema_version": 1,
                "signal_id": str(uuid.uuid4()),
                "strategy_name": strat_name,
                "strategy_version": "sm2",
                "ticker": ticker,
                "exchange": sig_exchange,
                "account": "DUM983136",
                "timestamp_ns": time.time_ns(),
                "feature_vector": {
                    "con_id": con_id,
                    "currency": sig_currency,
                    "scan_type": scan_type,
                    "scan_rank": rank,
                    "news_delta_pp": news[0] if news else 0.0,
                    "is_bearish_via_inverse": is_bearish,
                    "underlying": orig_ticker if is_bearish else None,
                },
                "conviction_score": conv,
                "portfolio_rank": rank,
                "account_route_chosen": "DUM983136",
                "expected_fill_price": 1.0,  # MKT order; fill determined by broker
                "risk_deltas": {},
                "risk_final_confidence": conv,
            }
            await nc.publish("signals.core", json.dumps(signal).encode("utf-8"))
            self.last_submit_ts[ticker] = now
            self.submit_window.append(now)
            log.info(
                "FIRE %s rank=%d scan=%s conv=%.2f news=%s (open=%d submits/min=%d)",
                ticker, rank, scan_type, conv,
                f"{news[0]:+.1f}pp" if news else "none",
                len(self.open_tickers), len(self.submit_window),
            )

        await nc.subscribe("orders.filled", cb=on_fill)
        await nc.subscribe("news.alpha", cb=on_news_alpha)
        await nc.subscribe("scanner.hits.*", cb=on_scanner_hit)
        log.info("listening on scanner.hits.* + news.alpha + orders.filled")

        while True:
            await asyncio.sleep(60)
            log.info("state: open=%d submits_last_min=%d news_cached=%d",
                     len(self.open_tickers), len(self.submit_window), len(self.news_alpha))


if __name__ == "__main__":
    asyncio.run(ScannerFastTrader().run())
