"""Cross-Venue Arbitrage — detect price discrepancies across trading venues.

When the same stock trades on multiple venues (LSE, BATS Europe, Chi-X,
Aquis, IEX, NYSE, NASDAQ), temporary price discrepancies exist because
order books fragment across venues. This module detects:

1. **Crossed markets**: best_bid on venue A > best_ask on venue B
   (true arbitrage — buy on B, sell on A simultaneously).
2. **Best execution routing**: for any trade direction, identify the
   venue offering the best fill price.
3. **Spread improvement**: when a venue's spread is significantly
   tighter than the primary, route there for spread savings.

Data source: IBKR L2 subscriptions (free: BATS Europe, Chi-X, IEX, Aquis;
paid: LSE, XETRA, HKEX, TSE, KRX).

ISA constraint: Can only go long (no simultaneous buy+sell arb). Focus on:
- Best execution venue for entry/exit routing
- Detecting when primary venue is stale vs. alternative venue

Usage:
    from python_brain.strategies.cross_venue_arb import get_tracker

    tracker = get_tracker()
    tracker.update_quote("AAPL", "NYSE", bid=150.10, ask=150.12, ...)
    tracker.update_quote("AAPL", "IEX", bid=150.11, ask=150.13, ...)
    arb = tracker.check_arb("AAPL")
    venue = tracker.get_best_execution_venue("AAPL", "buy")
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("cross_venue_arb")

# ── VENUE CONFIGURATION ──
# Map IBKR exchange codes to canonical venue names.
# Primary exchanges use their standard IBKR exchange field.
IBKR_VENUE_MAP: Dict[str, str] = {
    # UK / Europe
    "LSE": "LSE",
    "LSEETF": "LSE",
    "BATSEU": "BATS",
    "BATSE": "BATS",
    "BATE": "BATS",
    "CHIX": "CHIX",
    "CHIXE": "CHIX",
    "AQXE": "AQUIS",
    "AQUIS": "AQUIS",
    "XETRA": "XETRA",
    "FWB": "XETRA",
    # US
    "IEX": "IEX",
    "NYSE": "NYSE",
    "ARCA": "ARCA",
    "NASDAQ": "NASDAQ",
    "SMART": "SMART",
    # Asia
    "HKEX": "HKEX",
    "SEHK": "HKEX",
    "TSE": "TSE",
    "KRX": "KRX",
    "SGX": "SGX",
}

# Which venues can trade the same underlying? Grouped by region.
# Cross-venue arb only makes sense within same settlement/currency zone.
VENUE_GROUPS: Dict[str, List[str]] = {
    "UK_EU": ["LSE", "BATS", "CHIX", "AQUIS", "XETRA"],
    "US": ["NYSE", "NASDAQ", "ARCA", "IEX", "SMART"],
    "ASIA": ["HKEX", "TSE", "KRX", "SGX"],
}

# Reverse lookup: venue -> group
_VENUE_TO_GROUP: Dict[str, str] = {}
for _grp, _venues in VENUE_GROUPS.items():
    for _v in _venues:
        _VENUE_TO_GROUP[_v] = _grp

# Minimum quote staleness before we ignore a venue's quote (seconds).
# Stale quotes from venues with thin liquidity should not drive arb signals.
QUOTE_STALENESS_SEC = 5.0

# Minimum spread improvement (bps) to recommend venue switch.
# Below this threshold the routing benefit is too small vs execution risk.
MIN_SPREAD_IMPROVEMENT_BPS = 2.0

# Minimum crossed market edge (bps) to generate an arb signal.
# Must exceed round-trip transaction costs (IBKR ~1-2 bps per leg).
MIN_ARB_EDGE_BPS = 3.0

# Maximum quotes to keep per symbol/venue (ring buffer).
MAX_QUOTE_HISTORY = 20


@dataclass
class VenueQuote:
    """A single venue quote snapshot."""
    venue: str
    bid: float
    ask: float
    bid_size: int
    ask_size: int
    timestamp: float  # epoch seconds
    spread_bps: float = 0.0

    def __post_init__(self):
        mid = (self.bid + self.ask) / 2.0 if (self.bid > 0 and self.ask > 0) else 1.0
        self.spread_bps = ((self.ask - self.bid) / mid) * 10000.0 if mid > 0 else 0.0


@dataclass
class ArbOpportunity:
    """Detected cross-venue arbitrage opportunity."""
    symbol: str
    buy_venue: str          # venue with lowest ask
    sell_venue: str         # venue with highest bid
    buy_ask: float          # best ask price (buy here)
    sell_bid: float         # best bid price (sell here)
    edge_bps: float         # (sell_bid - buy_ask) / buy_ask * 10000
    edge_pct: float         # (sell_bid - buy_ask) / buy_ask * 100
    confidence: int         # signal confidence [0, 100]
    n_venues: int           # number of venues quoting
    best_spread_venue: str  # venue with tightest spread
    best_spread_bps: float  # tightest spread in bps
    timestamp: float = 0.0


@dataclass
class BestExecution:
    """Best execution venue recommendation."""
    symbol: str
    direction: str          # "buy" or "sell"
    preferred_venue: str    # recommended venue
    price: float            # best price on that venue
    improvement_bps: float  # spread improvement vs primary venue
    primary_venue: str      # default primary venue
    primary_price: float    # price on primary venue
    n_venues: int           # number of venues with live quotes


class CrossVenueArbitrage:
    """Detect price discrepancies across venues for the same underlying.

    Thread-safe: all state is per-symbol dicts, no global locks needed
    (Python GIL protects dict mutations from concurrent bridge calls).
    """

    def __init__(
        self,
        min_arb_edge_bps: float = MIN_ARB_EDGE_BPS,
        min_spread_improvement_bps: float = MIN_SPREAD_IMPROVEMENT_BPS,
        quote_staleness_sec: float = QUOTE_STALENESS_SEC,
    ):
        self._min_arb_edge_bps = min_arb_edge_bps
        self._min_spread_improvement_bps = min_spread_improvement_bps
        self._quote_staleness_sec = quote_staleness_sec

        # {symbol: {venue: VenueQuote}} — latest quote per venue per symbol
        self._venue_quotes: Dict[str, Dict[str, VenueQuote]] = {}

        # {symbol: str} — primary venue for each symbol (first seen or from contracts.toml)
        self._primary_venue: Dict[str, str] = {}

        # Diagnostics
        self._arb_count = 0
        self._update_count = 0

    def set_primary_venue(self, symbol: str, venue: str) -> None:
        """Set the primary (default) venue for a symbol from contracts.toml."""
        canonical = IBKR_VENUE_MAP.get(venue, venue)
        self._primary_venue[symbol] = canonical

    def update_quote(
        self,
        symbol: str,
        venue: str,
        bid: float,
        ask: float,
        bid_size: int = 0,
        ask_size: int = 0,
        timestamp: float = 0.0,
    ) -> None:
        """Update venue quote for a symbol. Called on every tick from any venue.

        Args:
            symbol: Ticker symbol (e.g. "AAPL", "VOD.L")
            venue: IBKR exchange code (e.g. "NYSE", "BATSEU", "LSE")
            bid: Best bid price
            ask: Best ask price
            bid_size: Bid depth (shares)
            ask_size: Ask depth (shares)
            timestamp: Epoch seconds (0 = use current time)
        """
        if bid <= 0 or ask <= 0 or ask < bid:
            return  # Invalid quote

        canonical_venue = IBKR_VENUE_MAP.get(venue, venue)
        ts = timestamp if timestamp > 0 else time.time()

        quote = VenueQuote(
            venue=canonical_venue,
            bid=bid,
            ask=ask,
            bid_size=bid_size,
            ask_size=ask_size,
            timestamp=ts,
        )

        if symbol not in self._venue_quotes:
            self._venue_quotes[symbol] = {}
        self._venue_quotes[symbol][canonical_venue] = quote

        # Set primary venue to first venue seen (overridden by set_primary_venue)
        if symbol not in self._primary_venue:
            self._primary_venue[symbol] = canonical_venue

        self._update_count += 1

    def _get_live_quotes(self, symbol: str) -> Dict[str, VenueQuote]:
        """Get non-stale quotes for a symbol, filtered by venue group compatibility."""
        quotes = self._venue_quotes.get(symbol)
        if not quotes:
            return {}

        now = time.time()
        primary = self._primary_venue.get(symbol)
        primary_group = _VENUE_TO_GROUP.get(primary, "") if primary else ""

        live: Dict[str, VenueQuote] = {}
        for venue, q in quotes.items():
            # Skip stale quotes
            if now - q.timestamp > self._quote_staleness_sec:
                continue
            # Only include venues in the same group (same settlement zone)
            venue_group = _VENUE_TO_GROUP.get(venue, "")
            if primary_group and venue_group and venue_group != primary_group:
                continue
            live[venue] = q

        return live

    def check_arb(self, symbol: str) -> Optional[ArbOpportunity]:
        """Check if there's an exploitable price discrepancy across venues.

        Returns ArbOpportunity if a crossed market or significant spread
        improvement is detected, None otherwise.
        """
        live = self._get_live_quotes(symbol)
        if len(live) < 2:
            return None  # Need at least 2 venues to compare

        # Find best bid (highest) and best ask (lowest) across all venues
        best_bid_venue = ""
        best_bid = 0.0
        best_bid_size = 0
        best_ask_venue = ""
        best_ask = float("inf")
        best_ask_size = 0
        tightest_spread_venue = ""
        tightest_spread_bps = float("inf")

        for venue, q in live.items():
            if q.bid > best_bid:
                best_bid = q.bid
                best_bid_venue = venue
                best_bid_size = q.bid_size
            if q.ask < best_ask:
                best_ask = q.ask
                best_ask_venue = venue
                best_ask_size = q.ask_size
            if q.spread_bps < tightest_spread_bps:
                tightest_spread_bps = q.spread_bps
                tightest_spread_venue = venue

        if best_ask <= 0 or best_bid <= 0:
            return None

        # Edge = how much the best bid exceeds the best ask
        # Positive edge = crossed market (true arb)
        # Negative edge = no arb but we can still find best execution
        mid = (best_bid + best_ask) / 2.0 if (best_bid + best_ask) > 0 else 1.0
        edge_bps = ((best_bid - best_ask) / mid) * 10000.0

        if edge_bps < self._min_arb_edge_bps:
            return None  # No actionable arb

        # Confidence scales with edge size and number of venues confirming
        # Base 55 + edge bonus + venue bonus
        edge_bonus = min(20, int((edge_bps - self._min_arb_edge_bps) * 3))
        venue_bonus = min(10, (len(live) - 2) * 3)
        # Size bonus: larger depth = more executable
        size_bonus = 0
        if best_bid_size > 0 and best_ask_size > 0:
            min_size = min(best_bid_size, best_ask_size)
            if min_size >= 1000:
                size_bonus = 5
            elif min_size >= 100:
                size_bonus = 2

        confidence = min(90, 55 + edge_bonus + venue_bonus + size_bonus)

        self._arb_count += 1
        now = time.time()

        arb = ArbOpportunity(
            symbol=symbol,
            buy_venue=best_ask_venue,
            sell_venue=best_bid_venue,
            buy_ask=best_ask,
            sell_bid=best_bid,
            edge_bps=round(edge_bps, 2),
            edge_pct=round((best_bid - best_ask) / best_ask * 100, 4),
            confidence=confidence,
            n_venues=len(live),
            best_spread_venue=tightest_spread_venue,
            best_spread_bps=round(tightest_spread_bps, 2),
            timestamp=now,
        )

        if self._arb_count <= 10 or self._arb_count % 50 == 0:
            log.info(
                "ARB_DETECTED: %s buy@%s(%.4f) sell@%s(%.4f) edge=%.1fbps conf=%d venues=%d",
                symbol, best_ask_venue, best_ask, best_bid_venue, best_bid,
                edge_bps, confidence, len(live),
            )

        return arb

    def get_best_execution_venue(
        self, symbol: str, direction: str
    ) -> Optional[BestExecution]:
        """For a given trade direction, which venue gives the best fill?

        Args:
            symbol: Ticker symbol
            direction: "buy" or "sell" (case-insensitive)

        Returns:
            BestExecution with preferred venue, or None if insufficient data.
        """
        live = self._get_live_quotes(symbol)
        if not live:
            return None

        direction = direction.lower()
        primary = self._primary_venue.get(symbol, "")
        primary_quote = live.get(primary)

        if direction == "buy":
            # For buys: venue with lowest ask
            best_venue = ""
            best_price = float("inf")
            for venue, q in live.items():
                if q.ask < best_price and q.ask > 0:
                    best_price = q.ask
                    best_venue = venue
        elif direction == "sell":
            # For sells: venue with highest bid
            best_venue = ""
            best_price = 0.0
            for venue, q in live.items():
                if q.bid > best_price:
                    best_price = q.bid
                    best_venue = venue
        else:
            return None

        if not best_venue or best_price <= 0:
            return None

        # Calculate improvement vs primary venue
        improvement_bps = 0.0
        primary_price = 0.0
        if primary_quote:
            if direction == "buy":
                primary_price = primary_quote.ask
                if primary_price > 0:
                    improvement_bps = ((primary_price - best_price) / primary_price) * 10000.0
            else:
                primary_price = primary_quote.bid
                if primary_price > 0:
                    improvement_bps = ((best_price - primary_price) / primary_price) * 10000.0

        return BestExecution(
            symbol=symbol,
            direction=direction,
            preferred_venue=best_venue,
            price=best_price,
            improvement_bps=round(improvement_bps, 2),
            primary_venue=primary,
            primary_price=primary_price,
            n_venues=len(live),
        )

    def get_nbbo(self, symbol: str) -> Optional[Dict[str, float]]:
        """Get National Best Bid and Offer across all venues.

        Returns dict with best_bid, best_ask, nbbo_spread_bps, n_venues
        or None if no live quotes.
        """
        live = self._get_live_quotes(symbol)
        if not live:
            return None

        best_bid = max((q.bid for q in live.values()), default=0.0)
        best_ask = min((q.ask for q in live.values() if q.ask > 0), default=0.0)

        if best_bid <= 0 or best_ask <= 0:
            return None

        mid = (best_bid + best_ask) / 2.0
        spread_bps = ((best_ask - best_bid) / mid) * 10000.0 if mid > 0 else 0.0

        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "nbbo_spread_bps": round(spread_bps, 2),
            "n_venues": len(live),
        }

    def get_diagnostics(self) -> Dict:
        """Return diagnostic info for monitoring."""
        return {
            "update_count": self._update_count,
            "arb_count": self._arb_count,
            "symbols_tracked": len(self._venue_quotes),
            "venues_per_symbol": {
                sym: list(venues.keys())
                for sym, venues in self._venue_quotes.items()
                if len(venues) > 1
            },
        }


# ── MODULE SINGLETON (same pattern as NAVTracker) ──
_tracker_instance: Optional[CrossVenueArbitrage] = None


def get_tracker() -> CrossVenueArbitrage:
    """Get or create the module-level CrossVenueArbitrage singleton."""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = CrossVenueArbitrage()
    return _tracker_instance
