"""ibkr_news_reactor — V5 news feed.

Subscribes to every IBKR news provider the account has access to
(Reuters Basic, Benzinga if trial active, Dow Jones, Briefing, Wall
Street Horizon, etc.) via reqNewsBulletins + reqNewsProviders, plus
pulls reqHistoricalNews per ticker in the live universe.

Publishes to NATS:
    news.raw      {provider, article_id, headline, summary, ticker, ts}
    news.tickers  {ticker, headlines: [...]}

Consumed by llm_news_analyzer.py → publishes news.alpha with
conviction delta.

client_id=109 (dedicated to news so scanner doesn't compete).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal as sig
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Set

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


# Seed universe of high-liquidity US tickers for historical news. Will be
# replaced by watchlist.v5.json once the rotator is producing.
SEED_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMD", "GOOGL", "META", "AMZN", "TSLA",
    "NFLX", "AVGO", "SPY", "QQQ",
]


@dataclass
class IbkrNewsReactor:
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 4002
    ibkr_client_id: int = 823  # was 109→209, both thrashed; using fresh random
    nats_url: str = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
    historical_lookback_hours: int = 24
    refresh_historical_every_s: int = 600
    seen_articles: Set[str] = field(default_factory=set)

    async def run(self) -> None:
        import nats  # type: ignore
        from ib_insync import IB, Stock, NewsBulletin, NewsTick  # type: ignore

        nc = await nats.connect(self.nats_url, name="aegis-v5-news-reactor")
        log.info("news reactor connected to NATS %s", self.nats_url)

        ib = IB()
        # Retry loop with cid increment — defends against per-cid soft-locks
        connected = False
        for attempt in range(8):
            cid = self.ibkr_client_id + attempt * 13
            try:
                await ib.connectAsync(
                    self.ibkr_host, self.ibkr_port, clientId=cid,
                    readonly=True, timeout=30,
                )
                log.info("news reactor connected cid=%d (attempt %d)", cid, attempt + 1)
                self.ibkr_client_id = cid
                connected = True
                break
            except Exception as e:
                log.warning("news connect attempt %d cid=%d failed: %s",
                            attempt + 1, cid, e)
                await asyncio.sleep(8)
        if not connected:
            log.error("news reactor could not connect after 8 attempts; exiting")
            return
        log.info("news reactor connected to IBKR client_id=%d", self.ibkr_client_id)

        # Providers the account has entitlement for.
        providers = await ib.reqNewsProvidersAsync()
        log.info("news providers available: %s", [p.code for p in providers])
        codes_csv = "+".join(p.code for p in providers)

        if not providers:
            log.warning("no news providers entitled on this account; reactor idle")
            while True:
                await asyncio.sleep(60)

        # --- Real-time bulletins (market-wide IBKR alerts) ------------------
        def on_bulletin(nb: "NewsBulletin"):
            try:
                aid = f"bull-{nb.msgId}"
                if aid in self.seen_articles:
                    return
                self.seen_articles.add(aid)
                payload = {
                    "provider": "IBKR_BULLETIN",
                    "article_id": aid,
                    "headline": nb.message,
                    "summary": "",
                    "ticker": None,
                    "origin_exchange": nb.origExchange,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
                asyncio.create_task(
                    nc.publish("news.raw", json.dumps(payload).encode("utf-8"))
                )
                log.info("bulletin: %s", nb.message[:80])
            except Exception as e:
                log.warning("bulletin handle failed: %s", e)

        ib.newsBulletinEvent += on_bulletin
        ib.reqNewsBulletins(True)  # allMsgs positional

        # --- Per-ticker news tick subscriptions ------------------------------
        def on_news_tick(t: "NewsTick"):
            try:
                aid = f"{t.providerCode}-{t.articleId}"
                if aid in self.seen_articles:
                    return
                self.seen_articles.add(aid)
                payload = {
                    "provider": t.providerCode,
                    "article_id": t.articleId,
                    "headline": t.headline,
                    "summary": "",
                    "ticker": getattr(t, "contract", None) and t.contract.symbol,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
                asyncio.create_task(
                    nc.publish("news.raw", json.dumps(payload).encode("utf-8"))
                )
                log.info("news tick [%s] %s: %s",
                         t.providerCode, payload["ticker"], (t.headline or "")[:80])
            except Exception as e:
                log.warning("news tick handle failed: %s", e)

        # Subscribe news ticks (generic tick 292) for seed tickers.
        contracts = []
        for sym in SEED_TICKERS:
            c = Stock(sym, "SMART", "USD")
            try:
                qual = await ib.qualifyContractsAsync(c)
                if qual:
                    contracts.append(qual[0])
            except Exception:
                pass
        for c in contracts:
            t = ib.reqMktData(c, genericTickList="mdoff,292", snapshot=False)
            t.updateEvent += lambda tkr: None  # retain subscription
        ib.newsEvent = on_news_tick  # ib_insync wires news updates via this
        log.info("subscribed news ticks for %d seed tickers", len(contracts))

        # --- Historical news poll loop ---------------------------------------
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for s in (sig.SIGINT, sig.SIGTERM):
            loop.add_signal_handler(s, stop.set)

        async def poll_historical():
            while not stop.is_set():
                total_new = 0
                end = datetime.now(timezone.utc)
                start = end - timedelta(hours=self.historical_lookback_hours)
                for c in contracts:
                    try:
                        articles = await ib.reqHistoricalNewsAsync(
                            conId=c.conId,
                            providerCodes=codes_csv,
                            startDateTime=start.strftime("%Y-%m-%d %H:%M:%S"),
                            endDateTime=end.strftime("%Y-%m-%d %H:%M:%S"),
                            totalResults=20,
                        )
                        for a in articles or []:
                            aid = f"{a.providerCode}-{a.articleId}"
                            if aid in self.seen_articles:
                                continue
                            self.seen_articles.add(aid)
                            # Pull full article body so the LLM has real context
                            # rather than just the headline.
                            body = ""
                            try:
                                art = await asyncio.wait_for(
                                    ib.reqNewsArticleAsync(a.providerCode, a.articleId),
                                    timeout=5,
                                )
                                if art and getattr(art, "articleText", None):
                                    body = (art.articleText or "")[:4000]  # cap for LLM token cost
                            except Exception as e:
                                log.debug("reqNewsArticle %s: %s", aid, e)

                            payload = {
                                "provider": a.providerCode,
                                "article_id": a.articleId,
                                "headline": a.headline,
                                "summary": body,
                                "ticker": c.symbol,
                                "ts": a.time.isoformat() if hasattr(a, "time") and a.time else
                                    datetime.now(timezone.utc).isoformat(),
                            }
                            await nc.publish(
                                "news.raw", json.dumps(payload).encode("utf-8")
                            )
                            total_new += 1
                    except Exception as e:
                        log.debug("hist news %s failed: %s", c.symbol, e)
                log.info("historical news round: %d new articles (seen=%d)",
                         total_new, len(self.seen_articles))
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.refresh_historical_every_s)
                except asyncio.TimeoutError:
                    pass

        await poll_historical()
        await nc.drain()
        ib.disconnect()
        log.info("news reactor stopped")


if __name__ == "__main__":
    asyncio.run(IbkrNewsReactor().run())
