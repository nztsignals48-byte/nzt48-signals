"""
NZT-48 Trading System — Pre-Market Intelligence Engine
=======================================================
Runs at 09:00 GMT (1hr after LSE open) and 09:00 ET (30min before US open).

Produces a PreMarketBrief that tells us:
  1. What's INSIDE every ETP (constituent-level overnight/premarket moves)
  2. Which stocks are gapping and why (earnings, upgrades, macro)
  3. Futures context (S&P, Nasdaq, VIX)
  4. Sector rotation signals (leaders/laggards)
  5. Market bias assessment with confidence
  6. High-conviction setups and risk flags

The brief is sent to Telegram and stored in DB. It enriches subsequent
strategy scans via MarketContext.premarket_brief.
"""

from __future__ import annotations

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Optional

from models import ConstituentAlert, ETPBrief, PreMarketBrief

logger = logging.getLogger("nzt48.feeds.premarket_intel")

# Thresholds
_GAP_THRESHOLD_PCT = 1.5
_VOL_SPIKE_MULT = 2.0
_STRONG_MOVE_PCT = 3.0

# Sanity clamps — no single stock can move > ±100% overnight in reality
# (even earnings surprise gaps rarely exceed ±50%)
_MAX_CONSTITUENT_CHANGE_PCT = 100.0
_MAX_ETP_EXPECTED_PCT = 150.0  # leveraged ETP theoretical max

def _clamp(value: float, limit: float) -> float:
    """Clamp value to [-limit, +limit]."""
    return max(-limit, min(limit, value))


class PreMarketIntelligenceEngine:
    """Generates pre-market intelligence briefs by decomposing ETPs into
    their constituents and scanning overnight/premarket data."""

    def __init__(self, data_feeds, news_feed, screener, holdings) -> None:
        self.data_feeds = data_feeds
        self.news_feed = news_feed
        self.screener = screener
        self.holdings = holdings
        self.latest_brief: Optional[PreMarketBrief] = None
        self._executor = ThreadPoolExecutor(max_workers=8)

    # ── Main entry point ─────────────────────────────────────────────────

    async def run_scan(self, scan_window: str) -> PreMarketBrief:
        """Run a complete pre-market intelligence scan."""
        logger.info("PRE-MARKET INTELLIGENCE: Starting %s scan...", scan_window)
        loop = asyncio.get_event_loop()

        all_constituents = self.holdings.get_all_constituents()
        logger.info("Scanning %d unique constituents", len(all_constituents))

        premarket_data = await loop.run_in_executor(
            self._executor, self._fetch_premarket_batch, list(all_constituents))

        news_data = await loop.run_in_executor(
            self._executor, self._fetch_news_batch, list(all_constituents))

        futures_ctx = await loop.run_in_executor(
            self._executor, self._get_futures_context)

        etp_briefs = self._build_etp_briefs(premarket_data, news_data)
        stock_alerts = self._build_stock_alerts(premarket_data, news_data)

        sector_leaders, sector_laggards = await loop.run_in_executor(
            self._executor, self._assess_sectors)

        bias, bias_confidence = self._determine_bias(futures_ctx, etp_briefs, stock_alerts)
        setups = self._find_setups(etp_briefs, stock_alerts)
        risk_flags = self._find_risk_flags(futures_ctx, news_data)

        brief = PreMarketBrief(
            timestamp=datetime.now(timezone.utc),
            scan_window=scan_window,
            market_bias=bias,
            bias_confidence=bias_confidence,
            sp500_futures_pct=futures_ctx.get("sp500_pct", 0.0),
            nasdaq_futures_pct=futures_ctx.get("nasdaq_pct", 0.0),
            vix_level=futures_ctx.get("vix", 0.0),
            asia_summary=futures_ctx.get("asia_summary", ""),
            europe_summary=futures_ctx.get("europe_summary", ""),
            etp_briefs=etp_briefs,
            stock_alerts=stock_alerts,
            sector_leaders=sector_leaders,
            sector_laggards=sector_laggards,
            high_conviction_setups=setups,
            risk_flags=risk_flags,
        )

        logger.info(
            "PRE-MARKET BRIEF: bias=%s (%.0f%%) | %d ETP briefs | %d stock alerts | %d setups",
            bias, bias_confidence * 100, len(etp_briefs), len(stock_alerts), len(setups))

        self.latest_brief = brief
        return brief

    # ── Data fetching ────────────────────────────────────────────────────

    def _fetch_premarket_batch(self, tickers: list[str]) -> dict[str, dict[str, float]]:
        results: dict[str, dict[str, float]] = {}
        max_workers = min(len(tickers), 12)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = {pool.submit(self.data_feeds.get_premarket_data, t): t for t in tickers}
            for future in as_completed(futs, timeout=60):
                try:
                    data = future.result(timeout=15)
                    results[futs[future]] = data
                except Exception:
                    logger.debug("Premarket fetch failed for %s", futs[future])
        logger.info("Premarket data: %d/%d tickers", len(results), len(tickers))
        return results

    def _fetch_news_batch(self, tickers: list[str]) -> dict[str, list[dict]]:
        # Use batched API call to reduce NewsAPI request count
        try:
            all_news = self.news_feed.get_ticker_news_batch(tickers[:30], hours=24)
            results = {t: articles for t, articles in all_news.items() if articles}
        except Exception:
            # Fallback to individual calls if batch method unavailable
            results = {}
            for t in tickers[:30]:
                try:
                    articles = self.news_feed.get_ticker_news(t, hours=24)
                    if articles:
                        results[t] = articles
                except Exception:
                    pass
        logger.info("News fetched for %d tickers", len(results))
        return results

    def _get_futures_context(self) -> dict[str, Any]:
        ctx: dict[str, Any] = {
            "sp500_pct": 0.0, "nasdaq_pct": 0.0, "vix": 0.0,
            "asia_summary": "", "europe_summary": "",
        }
        try:
            ctx["sp500_pct"] = _clamp(self.data_feeds.get_premarket_data("SPY").get("change_pct", 0.0), 25.0)
        except Exception:
            pass
        try:
            ctx["nasdaq_pct"] = _clamp(self.data_feeds.get_premarket_data("QQQ").get("change_pct", 0.0), 25.0)
        except Exception:
            pass
        try:
            ctx["vix"] = self.data_feeds.get_realtime_price("^VIX")
        except Exception:
            pass

        asia_moves = []
        for ticker, name in [("EWJ", "Japan"), ("FXI", "China"), ("EWY", "Korea")]:
            try:
                pct = _clamp(self.data_feeds.get_premarket_data(ticker).get("change_pct", 0.0), 25.0)
                if abs(pct) > 0.1:
                    asia_moves.append(f"{name} {pct:+.1f}%")
            except Exception:
                pass
        ctx["asia_summary"] = ", ".join(asia_moves) if asia_moves else "Flat"

        eu_moves = []
        for ticker, name in [("EWG", "Germany"), ("EWU", "UK"), ("FEZ", "Eurozone")]:
            try:
                pct = _clamp(self.data_feeds.get_premarket_data(ticker).get("change_pct", 0.0), 25.0)
                if abs(pct) > 0.1:
                    eu_moves.append(f"{name} {pct:+.1f}%")
            except Exception:
                pass
        ctx["europe_summary"] = ", ".join(eu_moves) if eu_moves else "Flat"
        return ctx

    # ── ETP brief construction ───────────────────────────────────────────

    def _build_etp_briefs(self, premarket: dict, news: dict) -> list[ETPBrief]:
        briefs: list[ETPBrief] = []
        for etp_ticker in self.holdings.get_long_etps():
            profile = self.holdings.get(etp_ticker)
            if not profile or not profile.top_holdings:
                continue

            top_movers: list[ConstituentAlert] = []
            weighted_change = 0.0
            total_weight = 0.0

            for holding in profile.top_holdings:
                pm = premarket.get(holding.ticker, {})
                change_pct = _clamp(pm.get("change_pct", 0.0), _MAX_CONSTITUENT_CHANGE_PCT)
                weighted_change += change_pct * holding.weight
                total_weight += holding.weight

                if abs(change_pct) >= 0.5 or holding.ticker in news:
                    headlines = [a.get("title", "") for a in news.get(holding.ticker, [])[:3]]
                    sentiment = self._agg_sentiment(news.get(holding.ticker, []))
                    catalyst_type = ""
                    try:
                        cat = self.news_feed.detect_catalyst(holding.ticker)
                        if cat.get("detected"):
                            catalyst_type = cat.get("type", "")
                    except Exception:
                        pass

                    top_movers.append(ConstituentAlert(
                        ticker=holding.ticker, name=holding.name,
                        overnight_change_pct=change_pct,  # already clamped above
                        premarket_price=pm.get("price", 0.0),
                        premarket_volume=pm.get("volume", 0.0),
                        volume_spike=pm.get("volume", 0) > 0,
                        news_headlines=headlines, sentiment=sentiment,
                        catalyst_type=catalyst_type,
                        affected_etps=self.holdings.get_etps_for_stock(holding.ticker),
                    ))

            top_movers.sort(key=lambda m: abs(m.overnight_change_pct), reverse=True)
            if total_weight > 0:
                weighted_change /= total_weight
            expected_move = _clamp(weighted_change * profile.leverage, _MAX_ETP_EXPECTED_PCT)
            gap_setup = "flat"
            if abs(expected_move) >= _GAP_THRESHOLD_PCT:
                gap_setup = "gap_and_go"
            elif abs(expected_move) >= 0.5:
                gap_setup = "gap_and_fade"

            news_items = []
            for m in top_movers[:3]:
                if m.news_headlines:
                    news_items.append(f"{m.ticker}: {m.news_headlines[0][:60]}")

            briefs.append(ETPBrief(
                etp_ticker=etp_ticker, index_name=profile.index,
                leverage=profile.leverage, etp_overnight_change_pct=expected_move,
                top_movers=top_movers[:5], weighted_constituent_change=weighted_change,
                expected_etp_move_pct=expected_move, gap_setup=gap_setup,
                news_summary=" | ".join(news_items),
            ))

        briefs.sort(key=lambda b: abs(b.expected_etp_move_pct), reverse=True)
        return briefs

    # ── Bot B stock alerts ───────────────────────────────────────────────

    def _build_stock_alerts(self, premarket: dict, news: dict) -> list[ConstituentAlert]:
        from feeds.data_feeds import BOT_B_TICKERS
        alerts: list[ConstituentAlert] = []

        for ticker in BOT_B_TICKERS:
            pm = premarket.get(ticker, {})
            change_pct = _clamp(pm.get("change_pct", 0.0), _MAX_CONSTITUENT_CHANGE_PCT)
            if abs(change_pct) < 0.5 and ticker not in news:
                continue

            headlines = [a.get("title", "") for a in news.get(ticker, [])[:3]]
            sentiment = self._agg_sentiment(news.get(ticker, []))
            catalyst_type = ""
            try:
                cat = self.news_feed.detect_catalyst(ticker)
                if cat.get("detected"):
                    catalyst_type = cat.get("type", "")
            except Exception:
                pass

            alerts.append(ConstituentAlert(
                ticker=ticker, overnight_change_pct=change_pct,
                premarket_price=pm.get("price", 0.0),
                premarket_volume=pm.get("volume", 0.0),
                volume_spike=pm.get("volume", 0) > 0,
                news_headlines=headlines, sentiment=sentiment,
                catalyst_type=catalyst_type,
                affected_etps=self.holdings.get_etps_for_stock(ticker),
            ))

        alerts.sort(key=lambda a: abs(a.overnight_change_pct), reverse=True)
        return alerts

    # ── Sector rotation ──────────────────────────────────────────────────

    def _assess_sectors(self) -> tuple[list[str], list[str]]:
        sector_etfs = {
            "XLE": "Energy", "XLU": "Utilities", "XLI": "Industrials",
            "XLF": "Financials", "XLK": "Technology", "XLV": "Healthcare",
            "XLP": "Staples", "XLY": "Discretionary", "XLC": "Communication",
            "GDX": "Gold Miners",
        }
        moves: list[tuple[str, float]] = []
        for etf, sector in sector_etfs.items():
            try:
                pct = self.data_feeds.get_premarket_data(etf).get("change_pct", 0.0)
                moves.append((sector, pct))
            except Exception:
                pass
        if not moves:
            return [], []
        moves.sort(key=lambda x: x[1], reverse=True)
        leaders = [f"{s} ({p:+.1f}%)" for s, p in moves[:3] if p > 0.1]
        laggards = [f"{s} ({p:+.1f}%)" for s, p in moves[-3:] if p < -0.1]
        return leaders, laggards

    # ── Market bias ──────────────────────────────────────────────────────

    def _determine_bias(self, futures: dict, etps: list, stocks: list) -> tuple[str, float]:
        signals: list[float] = []
        sp_pct = futures.get("sp500_pct", 0.0)
        nq_pct = futures.get("nasdaq_pct", 0.0)
        vix = futures.get("vix", 0.0)

        if sp_pct != 0:
            signals.append(sp_pct * 2)
        if nq_pct != 0:
            signals.append(nq_pct * 2)
        if vix > 30:
            signals.append(-2.0)
        elif vix > 25:
            signals.append(-1.0)
        elif vix < 15:
            signals.append(0.5)

        for eb in etps:
            if eb.leverage > 0:
                signals.append(eb.weighted_constituent_change)

        pos_stocks = sum(1 for s in stocks if s.overnight_change_pct > 0.5)
        neg_stocks = sum(1 for s in stocks if s.overnight_change_pct < -0.5)
        if pos_stocks + neg_stocks > 0:
            signals.append((pos_stocks - neg_stocks) / (pos_stocks + neg_stocks) * 2)

        if not signals:
            return "NEUTRAL", 0.0

        avg = sum(signals) / len(signals)
        if avg > 0.3:
            bias = "BULLISH"
        elif avg < -0.3:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        if len(signals) >= 3:
            same_sign = sum(1 for s in signals if (s > 0) == (avg > 0))
            confidence = same_sign / len(signals)
        else:
            confidence = 0.5
        magnitude = min(abs(avg) / 3.0, 1.0)
        confidence = confidence * 0.7 + magnitude * 0.3
        return bias, round(confidence, 2)

    # ── Setup identification ─────────────────────────────────────────────

    def _find_setups(self, etps: list, stocks: list) -> list[str]:
        setups: list[str] = []
        for eb in etps:
            if eb.gap_setup == "gap_and_go" and abs(eb.expected_etp_move_pct) >= 2.0:
                direction = "LONG" if eb.expected_etp_move_pct > 0 else "SHORT"
                setups.append(f"{eb.etp_ticker} {direction} gap_and_go ({eb.expected_etp_move_pct:+.1f}%)")

        for sa in stocks:
            if sa.catalyst_type in ("earnings_beat", "upgrade") and sa.overnight_change_pct > 2.0:
                setups.append(f"{sa.ticker} LONG {sa.catalyst_type} ({sa.overnight_change_pct:+.1f}%)")
            elif sa.catalyst_type in ("earnings_miss", "downgrade") and sa.overnight_change_pct < -2.0:
                setups.append(f"{sa.ticker} SHORT {sa.catalyst_type} ({sa.overnight_change_pct:+.1f}%)")

        for sa in stocks:
            if abs(sa.overnight_change_pct) >= _STRONG_MOVE_PCT and not sa.catalyst_type:
                direction = "SHORT" if sa.overnight_change_pct > 0 else "LONG"
                setups.append(f"{sa.ticker} {direction} mean_reversion ({sa.overnight_change_pct:+.1f}% no catalyst)")
        return setups[:10]

    # ── Risk flags ───────────────────────────────────────────────────────

    def _find_risk_flags(self, futures: dict, news: dict) -> list[str]:
        flags: list[str] = []
        vix = futures.get("vix", 0.0)
        sp_pct = futures.get("sp500_pct", 0.0)
        nq_pct = futures.get("nasdaq_pct", 0.0)

        if vix > 35:
            flags.append(f"VIX EXTREME: {vix:.1f} — RISK_OFF likely")
        elif vix > 25:
            flags.append(f"VIX ELEVATED: {vix:.1f} — reduce size")
        if abs(sp_pct) > 1.5:
            flags.append(f"S&P futures {sp_pct:+.1f}% — large gap")
        if abs(nq_pct) > 2.0:
            flags.append(f"Nasdaq futures {nq_pct:+.1f}% — extreme gap")
        if abs(sp_pct - nq_pct) > 1.0:
            flags.append(f"Index divergence: S&P {sp_pct:+.1f}% vs Nasdaq {nq_pct:+.1f}%")

        from feeds.news_feed import CRISIS_KEYWORDS
        crisis_patterns = [re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE) for kw in CRISIS_KEYWORDS]
        for ticker, articles in news.items():
            for article in articles[:3]:
                title = article.get("title", "")
                for pattern in crisis_patterns:
                    if pattern.search(title):
                        flags.append(f"CRISIS: {ticker} — {title[:60]}")
                        break
        return flags[:10]

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _agg_sentiment(articles: list[dict]) -> str:
        if not articles:
            return "neutral"
        sentiments = [a.get("sentiment", "neutral") for a in articles[:5]]
        pos = sentiments.count("positive")
        neg = sentiments.count("negative")
        return "positive" if pos > neg else ("negative" if neg > pos else "neutral")
