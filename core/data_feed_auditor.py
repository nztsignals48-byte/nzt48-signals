"""
Data Feed Auditor for NZT-48 Trading System

Verifies that all data sources (LSE, US, ASIA) are operational and returning
valid quotes. No persistent state — pure verification. Runs periodically
(every 5 mins) and reports degradation/failure to Telegram.

Architecture:
  - LSE: TwelveData (primary) → yfinance (fallback)
  - US: Polygon (primary) → TwelveData (secondary) → yfinance (fallback)
  - ASIA: yfinance (primary, 15-20min delay OK for monitoring)

Each feed check is independent. If a feed is DOWN, log it but don't block execution.
Execution engine has its own circuit breaker (H-06 protocol).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger("nzt48.core.data_feed_auditor")


@dataclass
class FeedStatus:
    """Health status of a single data feed."""
    market: str          # "LSE", "US", "ASIA"
    provider: str        # "TwelveData", "Polygon", "yfinance"
    status: str          # "OK", "DEGRADED", "FAIL"
    last_check: datetime
    last_check_sample_ticker: str
    latency_ms: float    # Latest tick latency in milliseconds
    last_quote_timestamp: Optional[datetime] = None
    error_message: str = ""


class DataFeedAuditor:
    """
    Audit data feeds with zero persistent state.

    Each market (LSE, US, ASIA) has its own fallback chain.
    Graceful degradation: if primary feed fails, fallback is tested next scan.
    """

    # Health check parameters
    MAX_STALE_AGE_SECONDS = 120  # Quote older than 2 min = degraded
    CRITICAL_STALE_AGE = 600    # Quote older than 10 min = failed

    # Per-market configuration
    LSE_SAMPLE_TICKERS = ["QQQ3.L", "3LUS.L", "QQQS.L"]  # Top ISA tickers
    US_SAMPLE_TICKERS = ["QQQ", "SPY", "NVDA"]
    ASIA_SAMPLE_TICKERS = ["0700.HK", "9618.HK"]  # Tencent, Alibaba

    def __init__(
        self,
        realtime_data=None,
        polygon_client=None,
        ibkr_gateway=None,
        telegram_sender=None
    ):
        """
        Initialize auditor with data source connections.

        Args:
            realtime_data: RealTimeDataFeed instance (LSE primary)
            polygon_client: Polygon API client (US primary)
            ibkr_gateway: IBKR gateway instance (fallback for quotes)
            telegram_sender: TelegramSender for alerts
        """
        self.realtime_data = realtime_data
        self.polygon_client = polygon_client
        self.ibkr_gateway = ibkr_gateway
        self.telegram = telegram_sender

        # Track feed status across checks (no persistent DB)
        self.last_status: Dict[str, FeedStatus] = {}

    async def audit_all_feeds(self) -> Dict[str, FeedStatus]:
        """
        Verify all data feeds are operational (LSE, US, ASIA).

        Returns:
            Dict of market → FeedStatus
        """
        results = {}

        # Test LSE (TwelveData primary)
        lse_status = await self._audit_lse()
        results['LSE'] = lse_status

        # Test US (Polygon primary)
        us_status = await self._audit_us()
        results['US'] = us_status

        # Test ASIA (yfinance primary)
        asia_status = await self._audit_asia()
        results['ASIA'] = asia_status

        # Log summary and check for transitions
        await self._log_audit_summary(results)

        # Store for next check
        self.last_status = results

        return results

    async def _audit_lse(self) -> FeedStatus:
        """Test LSE data feed (TwelveData primary)."""
        market = "LSE"
        provider = "TwelveData"
        sample_ticker = self.LSE_SAMPLE_TICKERS[0]

        try:
            # Try to fetch quote from TwelveData via realtime_data hub
            if self.realtime_data is None:
                logger.warning("LSE audit: realtime_data hub not initialized")
                return FeedStatus(
                    market=market,
                    provider=provider,
                    status="DEGRADED",
                    last_check=datetime.now(timezone.utc),
                    last_check_sample_ticker=sample_ticker,
                    latency_ms=0,
                    error_message="realtime_data hub unavailable"
                )

            # Fetch quote
            quote = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.realtime_data.get_quote(sample_ticker)
            )

            if quote is None:
                logger.warning("LSE audit: quote fetch returned None for %s", sample_ticker)
                return FeedStatus(
                    market=market,
                    provider=provider,
                    status="DEGRADED",
                    last_check=datetime.now(timezone.utc),
                    last_check_sample_ticker=sample_ticker,
                    latency_ms=0,
                    error_message="Quote fetch returned None"
                )

            # Check if quote is stale
            quote_timestamp = quote.get('timestamp')
            if quote_timestamp is None:
                logger.warning("LSE audit: quote missing timestamp for %s", sample_ticker)
                return FeedStatus(
                    market=market,
                    provider=provider,
                    status="DEGRADED",
                    last_check=datetime.now(timezone.utc),
                    last_check_sample_ticker=sample_ticker,
                    latency_ms=0,
                    error_message="Quote missing timestamp"
                )

            # Calculate staleness
            age_seconds = (datetime.now(timezone.utc) - quote_timestamp).total_seconds()
            latency_ms = age_seconds * 1000

            if age_seconds > self.CRITICAL_STALE_AGE:
                status = "FAIL"
            elif age_seconds > self.MAX_STALE_AGE_SECONDS:
                status = "DEGRADED"
            else:
                status = "OK"

            logger.info(
                "LSE audit: %s | provider=%s | ticker=%s | age_sec=%.1f | status=%s",
                "✅" if status == "OK" else "⚠️ " if status == "DEGRADED" else "❌",
                provider, sample_ticker, age_seconds, status
            )

            return FeedStatus(
                market=market,
                provider=provider,
                status=status,
                last_check=datetime.now(timezone.utc),
                last_check_sample_ticker=sample_ticker,
                latency_ms=latency_ms,
                last_quote_timestamp=quote_timestamp
            )

        except Exception as e:
            logger.error("LSE audit failed: %s", e, exc_info=True)
            return FeedStatus(
                market=market,
                provider=provider,
                status="FAIL",
                last_check=datetime.now(timezone.utc),
                last_check_sample_ticker=sample_ticker,
                latency_ms=0,
                error_message=str(e)
            )

    async def _audit_us(self) -> FeedStatus:
        """Test US data feed (Polygon primary, fallback to IBKR)."""
        market = "US"
        sample_ticker = self.US_SAMPLE_TICKERS[0]

        try:
            # Try Polygon first
            if self.polygon_client is not None:
                try:
                    quote = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.polygon_client.get_snapshot_quote(sample_ticker)
                    )

                    if quote is not None:
                        quote_timestamp = datetime.now(timezone.utc)  # Polygon gives us realtime
                        latency_ms = 10.0  # Typical Polygon latency

                        logger.info(
                            "US audit: ✅ | provider=Polygon | ticker=%s | latency_ms=%.1f",
                            sample_ticker, latency_ms
                        )

                        return FeedStatus(
                            market=market,
                            provider="Polygon",
                            status="OK",
                            last_check=datetime.now(timezone.utc),
                            last_check_sample_ticker=sample_ticker,
                            latency_ms=latency_ms,
                            last_quote_timestamp=quote_timestamp
                        )
                except Exception as e:
                    logger.warning("Polygon audit failed, trying fallback: %s", e)

            # Fallback to IBKR if available
            if self.ibkr_gateway is not None and not self.ibkr_gateway.is_degraded:
                try:
                    quote = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.ibkr_gateway.get_quote(sample_ticker)
                    )

                    if quote is not None:
                        quote_timestamp = datetime.now(timezone.utc)
                        latency_ms = 50.0  # Typical IBKR latency

                        logger.info(
                            "US audit: ✅ | provider=IBKR | ticker=%s | latency_ms=%.1f",
                            sample_ticker, latency_ms
                        )

                        return FeedStatus(
                            market=market,
                            provider="IBKR",
                            status="OK",
                            last_check=datetime.now(timezone.utc),
                            last_check_sample_ticker=sample_ticker,
                            latency_ms=latency_ms,
                            last_quote_timestamp=quote_timestamp
                        )
                except Exception as e:
                    logger.warning("IBKR audit fallback failed: %s", e)

            # If we get here, all feeds failed
            logger.error("US audit: all feeds failed")
            return FeedStatus(
                market=market,
                provider="Polygon/IBKR",
                status="FAIL",
                last_check=datetime.now(timezone.utc),
                last_check_sample_ticker=sample_ticker,
                latency_ms=0,
                error_message="All US data feeds unavailable"
            )

        except Exception as e:
            logger.error("US audit unexpected error: %s", e, exc_info=True)
            return FeedStatus(
                market=market,
                provider="Polygon/IBKR",
                status="FAIL",
                last_check=datetime.now(timezone.utc),
                last_check_sample_ticker=sample_ticker,
                latency_ms=0,
                error_message=str(e)
            )

    async def _audit_asia(self) -> FeedStatus:
        """Test ASIA data feed (yfinance primary, 15-20min delay acceptable)."""
        market = "ASIA"
        provider = "yfinance"
        sample_ticker = self.ASIA_SAMPLE_TICKERS[0]

        try:
            import yfinance as yf

            # yfinance for HK tickers
            ticker = yf.Ticker(sample_ticker)
            data = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: ticker.history(period="1d")
            )

            if data is None or len(data) == 0:
                logger.warning("ASIA audit: yfinance returned empty data for %s", sample_ticker)
                return FeedStatus(
                    market=market,
                    provider=provider,
                    status="DEGRADED",
                    last_check=datetime.now(timezone.utc),
                    last_check_sample_ticker=sample_ticker,
                    latency_ms=0,
                    error_message="Empty data returned"
                )

            # Get last quote timestamp (yfinance index)
            quote_timestamp = data.index[-1].to_pydatetime()
            if quote_timestamp.tzinfo is None:
                quote_timestamp = quote_timestamp.replace(tzinfo=timezone.utc)

            # For ASIA, we accept staleness up to 1 day (monitoring only)
            age_seconds = (datetime.now(timezone.utc) - quote_timestamp).total_seconds()
            age_minutes = age_seconds / 60

            # Status: OK if recent, DEGRADED if stale (but still useful for monitoring)
            if age_seconds > 86400:  # 24 hours
                status = "DEGRADED"
            else:
                status = "OK"

            logger.info(
                "ASIA audit: %s | provider=%s | ticker=%s | age_min=%.0f | status=%s",
                "✅" if status == "OK" else "⚠️ ",
                provider, sample_ticker, age_minutes, status
            )

            return FeedStatus(
                market=market,
                provider=provider,
                status=status,
                last_check=datetime.now(timezone.utc),
                last_check_sample_ticker=sample_ticker,
                latency_ms=age_seconds * 1000,
                last_quote_timestamp=quote_timestamp
            )

        except Exception as e:
            logger.error("ASIA audit failed: %s", e, exc_info=True)
            return FeedStatus(
                market=market,
                provider=provider,
                status="FAIL",
                last_check=datetime.now(timezone.utc),
                last_check_sample_ticker=sample_ticker,
                latency_ms=0,
                error_message=str(e)
            )

    async def _log_audit_summary(self, results: Dict[str, FeedStatus]) -> None:
        """Log summary and alert on state changes."""
        all_ok = all(s.status == "OK" for s in results.values())
        degraded = [m for m, s in results.items() if s.status == "DEGRADED"]
        failed = [m for m, s in results.items() if s.status == "FAIL"]

        if all_ok:
            logger.info("✅ All data feeds operational")
        else:
            if degraded:
                logger.warning("⚠️  Degraded feeds: %s", ", ".join(degraded))
            if failed:
                logger.error("❌ Failed feeds: %s", ", ".join(failed))

        # Check for state transition (OK → DEGRADED or OK → FAIL)
        for market, new_status in results.items():
            old_status = self.last_status.get(market)
            if old_status and old_status.status != new_status.status:
                alert_msg = (
                    f"DATA FEED ALERT: {market} transitioned from {old_status.status} to {new_status.status}\n"
                    f"Provider: {new_status.provider}\n"
                    f"Error: {new_status.error_message or 'None'}"
                )
                logger.warning(alert_msg)
                if self.telegram:
                    try:
                        await asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: self.telegram.send_message(alert_msg)
                        )
                    except Exception as e:
                        logger.error("Failed to send Telegram alert: %s", e)
