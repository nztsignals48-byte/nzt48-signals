"""
NZT-48 Market-Driven Session Scheduler (Phase 2a)

CRITICAL REQUIREMENT:
All timing is DERIVED from LIVE market open/close times, NOT hardcoded UTC times.
Handles DST automatically by querying broker for actual trading hours.

Replaces fixed hardcoded times (08:00, 14:30, 16:30, etc) with dynamic,
market-aware scheduling that adapts to daylight saving transitions.

---

MARKET TIME SOURCES:
- LSE (London): 08:00-16:30 GMT (winter) / 08:00-16:30 BST (summer)
  IB reference ticker: QQQ3.L or LSE-listed ETPs
- US (NYSE): 09:30-16:00 EST (winter) / 09:30-16:00 EDT (summer)
  IB reference ticker: SPY or ES
- ASIA (Hong Kong): 09:30-16:00 HKT (no DST)
  IB reference ticker: 0700.HK (Tencent) or HKG-listed stocks

All times returned in UTC (internally consistent).
Automatically handles DST via IB Gateway's regularTradingStart/End fields.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, time as dtime, timezone
from typing import Dict, Tuple, Optional, Any
from zoneinfo import ZoneInfo
import threading

logger = logging.getLogger("nzt48.market_session_scheduler")

# Timezone objects
UK_TZ = ZoneInfo("Europe/London")
ET_TZ = ZoneInfo("America/New_York")
HK_TZ = ZoneInfo("Asia/Hong_Kong")


class MarketSessionScheduler:
    """
    Adaptive market-driven scheduling, timezone-aware, DST-automatic.

    KEY PRINCIPLE:
    ✅ All market times derive from LIVE market data (broker query)
    ✅ NO hardcoded UTC times anywhere
    ✅ Timezone-aware (all internal times in UTC)
    ✅ DST-aware (ZoneInfo handles transitions automatically)
    ✅ 24-hour cache to avoid excessive broker queries
    ✅ Graceful fallback if broker unavailable
    """

    def __init__(self, ib_client: Optional[Any] = None):
        """
        Initialize market session scheduler.

        Args:
            ib_client: Optional ib_insync IB instance. If None, uses defaults.
        """
        self.ib = ib_client
        self.market_times_cache: Dict[str, Dict[str, datetime]] = {}
        self.cache_expiry: Optional[datetime] = None
        self._cache_lock = threading.RLock()
        self._fallback_mode = False
        self._last_query_error: Optional[str] = None

    def get_current_session(self) -> str:
        """
        Return current session based on LIVE market times.

        Returns:
            "LSE" | "US" | "ASIA" | "CLOSED" | "PRE_MARKET"
        """
        now = datetime.now(timezone.utc)
        weekday = now.weekday()

        # Market closed on weekends
        if weekday >= 5:  # Sat/Sun
            return "CLOSED"

        try:
            lse_open, lse_close = self._fetch_market_hours("LSE")
            us_open, us_close = self._fetch_market_hours("US")
            asia_open, asia_close = self._fetch_market_hours("ASIA")

            if lse_open <= now < lse_close:
                return "LSE"
            elif us_open <= now < us_close:
                return "US"
            elif asia_open <= now < asia_close:
                return "ASIA"
            else:
                # Check if approaching next market open
                return "CLOSED"
        except Exception as e:
            logger.warning("Failed to determine current session: %s. Using fallback.", e)
            return self._get_session_fallback(now)

    def get_phase_timings(self) -> Dict[str, Tuple[datetime, datetime]]:
        """
        Return exact phase boundaries based on LIVE market times.

        Returns:
            Dict mapping phase name → (start_time_utc, end_time_utc)

        Phases:
        - Phase1_LSE_EU: LSE open → 6.5 hours later (typically 08:00-14:30)
        - Phase2_LSE_US: LSE 6.5hr → LSE close (typically 14:30-16:30)
        - Phase3_US_only: US open → US close (typically 13:30-21:00 UTC)
        - Phase4_US_Asia_warmup: US close -1hr → US close (overnight overlap)
        - Phase5_Asia: Next day ASIA open → ASIA close
        """
        try:
            lse_open, lse_close = self._fetch_market_hours("LSE")
            us_open, us_close = self._fetch_market_hours("US")
            asia_open, asia_close = self._fetch_market_hours("ASIA")

            return {
                "Phase1_LSE_EU": (
                    lse_open,
                    lse_open + timedelta(hours=6.5)
                ),
                "Phase2_LSE_US": (
                    lse_open + timedelta(hours=6.5),
                    lse_close
                ),
                "Phase3_US_only": (
                    us_open,
                    us_close
                ),
                "Phase4_US_Asia_warmup": (
                    us_close - timedelta(hours=1),
                    us_close
                ),
                "Phase5_Asia": (
                    asia_open,
                    asia_close
                )
            }
        except Exception as e:
            logger.error("Failed to get phase timings: %s. Using fallback defaults.", e)
            return self._get_phase_timings_fallback()

    def schedule_universe_refresh(self, phase: str) -> Optional[datetime]:
        """
        Schedule universe refresh 15min before phase starts.

        Args:
            phase: Phase name (e.g., "Phase1_LSE_EU")

        Returns:
            datetime when refresh should occur (UTC), or None if phase not found
        """
        timings = self.get_phase_timings()
        if phase not in timings:
            logger.warning("Phase '%s' not found in timings", phase)
            return None

        phase_start = timings[phase][0]
        refresh_time = phase_start - timedelta(minutes=15)

        return refresh_time

    def get_time_until_market_close(self, market: Optional[str] = None) -> Optional[int]:
        """
        Get minutes until current market closes (or next market opens if closed).

        Used for Tier 3 exit enforcement (exit 5-min before close).

        Args:
            market: Optional market override ("LSE" | "US" | "ASIA").
                   If None, uses current_session().

        Returns:
            Minutes until close (positive), or None if error
        """
        if not market:
            market = self.get_current_session()

        if market == "CLOSED":
            # Return minutes until next market opens
            return self._minutes_until_next_open()

        try:
            _, market_close = self._fetch_market_hours(market)
            now = datetime.now(timezone.utc)
            minutes_until_close = (market_close - now).total_seconds() / 60
            return int(minutes_until_close)
        except Exception as e:
            logger.warning("Failed to get time until close for %s: %s", market, e)
            return None

    def is_approaching_market_close(
        self, market: Optional[str] = None, minutes_threshold: int = 15
    ) -> bool:
        """
        Check if approaching market close within threshold minutes.

        Args:
            market: Optional market override. Defaults to current session.
            minutes_threshold: Alert threshold (default 15 minutes).

        Returns:
            True if within threshold of market close
        """
        minutes_left = self.get_time_until_market_close(market)
        if minutes_left is None:
            return False
        return 0 < minutes_left < minutes_threshold

    # ─── Private Methods ───────────────────────────────────────────────────

    def _fetch_market_hours(self, market: str) -> Tuple[datetime, datetime]:
        """
        Fetch market open/close times from cache or broker.

        Returns:
            (open_time_utc, close_time_utc)

        Raises:
            RuntimeError if unable to fetch and no cached value available
        """
        with self._cache_lock:
            # Check cache validity
            if self._is_cache_valid():
                if market in self.market_times_cache:
                    cached = self.market_times_cache[market]
                    return cached["open"], cached["close"]

            # Fetch from broker or use fallback
            open_time, close_time = self._query_broker_market_hours(market)

            # Update cache
            self.market_times_cache[market] = {
                "open": open_time,
                "close": close_time
            }
            self.cache_expiry = datetime.now(timezone.utc) + timedelta(days=1)

            return open_time, close_time

    def _is_cache_valid(self) -> bool:
        """Check if cache is still valid (not expired)."""
        if self.cache_expiry is None:
            return False
        return datetime.now(timezone.utc) < self.cache_expiry

    def _query_broker_market_hours(self, market: str) -> Tuple[datetime, datetime]:
        """
        Query broker (IB Gateway) for market hours.
        Falls back to defaults if broker unavailable or ib_client not set.

        Returns:
            (open_time_utc, close_time_utc)
        """
        if self.ib is None:
            logger.debug("No IB client available, using fallback market hours for %s", market)
            return self._get_market_hours_fallback(market)

        try:
            return self._query_ib_for_market_hours(market)
        except Exception as e:
            self._last_query_error = str(e)
            self._fallback_mode = True
            logger.warning(
                "Failed to query IB for %s market hours: %s. Using fallback.",
                market, e
            )
            return self._get_market_hours_fallback(market)

    def _query_ib_for_market_hours(self, market: str) -> Tuple[datetime, datetime]:
        """
        Query IB Gateway for market hours via contractDetails.

        Process:
        1. Get reference contract for market (e.g., QQQ3.L for LSE)
        2. Query contractDetails
        3. Parse regularTradingStart/regularTradingEnd
        4. Convert to UTC
        5. Return (open_utc, close_utc)
        """
        from ib_insync import Contract

        # Map market to reference contract
        contracts_by_market = {
            "LSE": Contract(symbol="QQQ3", exchange="LSEETF", currency="GBP"),
            "US": Contract(symbol="SPY", exchange="SMART", currency="USD"),
            "ASIA": Contract(symbol="0700", exchange="HKONG", currency="HKD"),
        }

        if market not in contracts_by_market:
            raise ValueError(f"Unknown market: {market}")

        contract = contracts_by_market[market]

        # Query contract details
        details = self.ib.contractDetails(contract)
        if not details:
            raise RuntimeError(f"No contract details found for {market}")

        detail = details[0]

        # Parse trading hours from regularTradingStart/End
        # Format: "20260315:0800-20260315:1630" (YYYYMMDD:HHMM-YYYYMMDD:HHMM)
        trading_hours = detail.tradingHours
        if not trading_hours:
            raise RuntimeError(f"No trading hours found for {market}")

        # Parse the trading hours string
        # Example: "20260315:0800-20260315:1630"
        parts = trading_hours.split("-")
        if len(parts) < 2:
            raise RuntimeError(f"Unexpected tradingHours format: {trading_hours}")

        open_str, close_str = parts[0], parts[1]

        # Extract date and time
        open_date, open_time = open_str.split(":")
        close_date, close_time = close_str.split(":")

        # Parse dates and times
        tz = self._get_market_timezone(market)
        open_dt = self._parse_trading_hours_datetime(open_date, open_time, tz)
        close_dt = self._parse_trading_hours_datetime(close_date, close_time, tz)

        logger.debug(
            "Fetched %s market hours from IB: %s UTC - %s UTC",
            market,
            open_dt.isoformat(),
            close_dt.isoformat()
        )

        return open_dt, close_dt

    def _get_market_timezone(self, market: str) -> ZoneInfo:
        """Get timezone for a market."""
        tz_map = {
            "LSE": UK_TZ,
            "US": ET_TZ,
            "ASIA": HK_TZ,
        }
        return tz_map.get(market, UK_TZ)

    def _parse_trading_hours_datetime(
        self, date_str: str, time_str: str, tz: ZoneInfo
    ) -> datetime:
        """
        Parse trading hours datetime string and convert to UTC.

        Args:
            date_str: "YYYYMMDD" format
            time_str: "HHMM" format
            tz: Timezone for the market

        Returns:
            UTC datetime
        """
        # Parse date: "20260315" → 2026-03-15
        year = int(date_str[0:4])
        month = int(date_str[4:6])
        day = int(date_str[6:8])

        # Parse time: "0800" → 08:00
        hour = int(time_str[0:2])
        minute = int(time_str[2:4])

        # Create aware datetime in market timezone
        local_dt = datetime(year, month, day, hour, minute, 0, tzinfo=tz)

        # Convert to UTC
        utc_dt = local_dt.astimezone(timezone.utc)

        return utc_dt

    def _get_market_hours_fallback(self, market: str) -> Tuple[datetime, datetime]:
        """
        Fallback market hours if broker unavailable.

        These are TYPICAL hours (not DST-adjusted).
        During DST transitions, this will be offset by 1 hour until cache expires.
        """
        now_utc = datetime.now(timezone.utc)

        # Determine which day to use (today or tomorrow)
        # For simplicity, use today's UTC date
        market_tz = self._get_market_timezone(market)
        now_local = now_utc.astimezone(market_tz)

        # Get today's date in market timezone
        year, month, day = now_local.year, now_local.month, now_local.day

        # Typical market hours (these can be off by 1h during DST transitions)
        fallback_hours = {
            "LSE": (
                datetime(year, month, day, 8, 0, tzinfo=UK_TZ),  # 08:00 UK
                datetime(year, month, day, 16, 30, tzinfo=UK_TZ)  # 16:30 UK
            ),
            "US": (
                datetime(year, month, day, 9, 30, tzinfo=ET_TZ),  # 09:30 ET
                datetime(year, month, day, 16, 0, tzinfo=ET_TZ)   # 16:00 ET
            ),
            "ASIA": (
                datetime(year, month, day, 9, 30, tzinfo=HK_TZ),  # 09:30 HK
                datetime(year, month, day, 16, 0, tzinfo=HK_TZ)   # 16:00 HK
            ),
        }

        if market not in fallback_hours:
            raise ValueError(f"Unknown market: {market}")

        # Convert to UTC
        local_open, local_close = fallback_hours[market]
        utc_open = local_open.astimezone(timezone.utc)
        utc_close = local_close.astimezone(timezone.utc)

        logger.warning(
            "Using fallback market hours for %s (broker unavailable): %s - %s UTC",
            market, utc_open.isoformat(), utc_close.isoformat()
        )

        return utc_open, utc_close

    def _get_phase_timings_fallback(self) -> Dict[str, Tuple[datetime, datetime]]:
        """Fallback phase timings when broker unavailable."""
        try:
            lse_open, lse_close = self._get_market_hours_fallback("LSE")
            us_open, us_close = self._get_market_hours_fallback("US")
            asia_open, asia_close = self._get_market_hours_fallback("ASIA")

            return {
                "Phase1_LSE_EU": (
                    lse_open,
                    lse_open + timedelta(hours=6.5)
                ),
                "Phase2_LSE_US": (
                    lse_open + timedelta(hours=6.5),
                    lse_close
                ),
                "Phase3_US_only": (
                    us_open,
                    us_close
                ),
                "Phase4_US_Asia_warmup": (
                    us_close - timedelta(hours=1),
                    us_close
                ),
                "Phase5_Asia": (
                    asia_open,
                    asia_close
                )
            }
        except Exception as e:
            logger.error("Failed to construct fallback phase timings: %s", e)
            return {}

    def _get_session_fallback(self, now: datetime) -> str:
        """
        Fallback to hardcoded defaults when broker unavailable.
        Uses typical market hours (may be off by 1h during DST).
        """
        try:
            lse_open, lse_close = self._get_market_hours_fallback("LSE")
            us_open, us_close = self._get_market_hours_fallback("US")
            asia_open, asia_close = self._get_market_hours_fallback("ASIA")

            if lse_open <= now < lse_close:
                return "LSE"
            elif us_open <= now < us_close:
                return "US"
            elif asia_open <= now < asia_close:
                return "ASIA"
            else:
                return "CLOSED"
        except Exception:
            return "CLOSED"

    def _minutes_until_next_open(self) -> Optional[int]:
        """Calculate minutes until next market opens."""
        now = datetime.now(timezone.utc)

        try:
            # Check which market opens next
            timings = self.get_phase_timings()
            next_opens = []

            for phase, (open_time, _) in timings.items():
                if open_time > now:
                    next_opens.append(open_time)

            if not next_opens:
                # No market opening today; assume LSE opens tomorrow
                tomorrow = now + timedelta(days=1)
                lse_open, _ = self._get_market_hours_fallback("LSE")
                next_open = lse_open.replace(
                    year=tomorrow.year,
                    month=tomorrow.month,
                    day=tomorrow.day
                )
            else:
                next_open = min(next_opens)

            minutes = (next_open - now).total_seconds() / 60
            return int(minutes)
        except Exception as e:
            logger.warning("Failed to calculate minutes until next open: %s", e)
            return None

    # ─── Utility Methods ───────────────────────────────────────────────────

    def get_diagnostic_info(self) -> Dict[str, Any]:
        """
        Return diagnostic information for troubleshooting.
        """
        with self._cache_lock:
            return {
                "fallback_mode": self._fallback_mode,
                "last_query_error": self._last_query_error,
                "cache_valid": self._is_cache_valid(),
                "cache_expiry": self.cache_expiry.isoformat() if self.cache_expiry else None,
                "cached_markets": list(self.market_times_cache.keys()),
                "current_session": self.get_current_session(),
            }


# Singleton instance (optional global access)
_scheduler_instance: Optional[MarketSessionScheduler] = None


def get_market_scheduler(ib_client: Optional[Any] = None) -> MarketSessionScheduler:
    """
    Get or create global market scheduler instance.

    Args:
        ib_client: Optional IB instance to use for queries.

    Returns:
        MarketSessionScheduler singleton
    """
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = MarketSessionScheduler(ib_client)
    return _scheduler_instance
