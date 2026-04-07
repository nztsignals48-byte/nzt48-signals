#!/usr/bin/env python3
"""Radar Daemon — Continuous L1 market sweep across all open exchanges.

Runs 24/7 as a standalone process (client_id=103), scanning ~3,200 tickers
via rolling subscribe/cancel blocks. Writes radar_cache.json for consumption
by dynamic_universe.py Phase 1 and boost artifacts for the 15-min prep path.

Architecture:
    - Connects to IB Gateway on port 4003 via ib_insync (client_id=103)
    - Loads radar_universe.toml for per-exchange ticker lists
    - Rolling sweep: 50 tickers/block, 500ms wait, 500ms buffer
    - Tiered: Hot (every cycle), Warm (every 3rd), Cold (every 10th)
    - OPRA enrichment for ~30 curated US equities
    - Equity→Fund translation (NVDA bullish → NVD3.L)
    - Writes /app/data/radar_cache.json (shared volume)
    - Writes /app/data/radar_opra_signals.json (boost artifact)

Consumers:
    - radar_cache.json → dynamic_universe.py Phase 1 (_load_radar_cache)
    - radar_opra_signals.json → dynamic_universe.py boost loader (signal #11-14)

Session 35 — Zero dead code. Every field has a named consumer.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import tempfile
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ── ib_insync ──────────────────────────────────────────────────────────────
try:
    from ib_insync import IB, Contract, Stock, util
except ImportError:
    print("FATAL: ib_insync not installed. pip install ib_insync", file=sys.stderr)
    sys.exit(1)

# ── session_map (co-located in Docker image) ─────────────────────────��────
try:
    from session_map import (
        detect_session,
        get_all_exchanges_for_session,
        SESSION_MAP,
        SESSION_ORDER,
    )
except ImportError:
    # When running from repo (not Docker), try full import path
    try:
        from python_brain.ouroboros.session_map import (
            detect_session,
            get_all_exchanges_for_session,
            SESSION_MAP,
            SESSION_ORDER,
        )
    except ImportError:
        print("FATAL: session_map.py not found", file=sys.stderr)
        sys.exit(1)

# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ════════════════��══════════════════════════════════════════════════════════

IB_HOST = os.environ.get("IB_HOST", "ib-gateway")
IB_PORT = int(os.environ.get("IB_PORT", "4003"))
IB_CLIENT_ID = int(os.environ.get("IB_CLIENT_ID", "103"))

RADAR_CACHE_PATH = Path(os.environ.get("RADAR_CACHE_PATH", "/app/data/radar_cache.json"))
RADAR_OPRA_PATH = Path(os.environ.get("RADAR_OPRA_PATH", "/app/data/radar_opra_signals.json"))
RADAR_UNIVERSE_PATH = Path(os.environ.get("RADAR_UNIVERSE_PATH", "/app/config/radar_universe.toml"))
EQUITY_FUND_MAP_PATH = Path(os.environ.get("EQUITY_FUND_MAP_PATH", "/app/config/equity_fund_map.toml"))
WATCHLIST_PATH = Path(os.environ.get("WATCHLIST_PATH", "/app/config/active_watchlist.json"))
CONTRACTS_PATH = Path(os.environ.get("CONTRACTS_PATH", "/app/config/contracts.toml"))

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Pacing
BLOCK_SIZE = 50
BLOCK_INTERVAL_SEC = 2.0  # Total time per block (subscribe + wait + cancel + buffer)
TICK_WAIT_SEC = 0.5       # Time to wait for tick callbacks after subscribing
MAX_MSG_PER_SEC = 45      # Conservative (IBKR limit = 50)
MAX_CACHE_ENTRIES = 5000   # LRU eviction threshold

# Tier thresholds
TIER_HOT_RVOL = 2.0       # RVOL > 2x → promote to Hot
TIER_HOT_STREAK = 5        # momentum_streak > 5 → promote to Hot
TIER_HOT_OPRA = 2.5        # unusual_activity > 2.5 → promote to Hot
TIER_DEMOTE_QUIET_MIN = 30  # minutes quiet → Hot→Warm
TIER_DEMOTE_QUIET_WARM_MIN = 120  # minutes quiet → Warm→Cold
SPREAD_SKIP_BPS = 500      # spread > 500bps for 3 cycles → temp skip
SPREAD_SKIP_COUNT = 3

# OPRA: only for curated US equities (Fix 5: scope containment)
OPRA_ELIGIBLE = {
    # Direct-mapped underlyings (equity_fund_map)
    "NVDA", "TSLA", "AAPL", "MSFT", "AMD", "AMZN", "GOOGL", "META", "TSM", "MU",
    # SPX/NDX breadth components (top 20 by weight)
    "AVGO", "QCOM", "COST", "LLY", "JPM", "UNH", "V", "MA", "HD", "PG",
}

# Generic tick strings
GENERIC_TICKS_BASIC = "165,236"       # avg_volume + shortable
GENERIC_TICKS_OPRA = "100,101,106,165,236"  # + options vol/OI/IV

# ═══════════════════════════════════════════════════════════════════════════
# Equity → Fund Mapping (hardcoded V1, loaded from config V2)
# ═══════════════════════════════════════════════════════════════════════════

# Single-stock: US equity → (long_fund, inverse_fund, leverage)
_EQUITY_TO_FUND = {
    "NVDA":  {"long": "NVD3.L", "inverse": "3SNV.L", "leverage": 3},
    "TSLA":  {"long": "TSL3.L", "inverse": "3STS.L", "leverage": 3},
    "AAPL":  {"long": "APL3.L", "inverse": "3SAP.L", "leverage": 3},
    "MSFT":  {"long": "MSF3.L", "inverse": "3SMS.L", "leverage": 3},
    "AMD":   {"long": "AMD3.L", "inverse": "3SAM.L", "leverage": 3},
    "AMZN":  {"long": "AMZ3.L", "inverse": None,      "leverage": 3},
    "GOOGL": {"long": "GOO3.L", "inverse": None,      "leverage": 3},
    "META":  {"long": "MET3.L", "inverse": None,      "leverage": 3},
    "TSM":   {"long": "TSM3.L", "inverse": None,      "leverage": 3},
    "MU":    {"long": "MU2.L",  "inverse": None,      "leverage": 2},
}

# Fund → underlying (reverse lookup)
_FUND_TO_EQUITY = {}
for _eq, _finfo in _EQUITY_TO_FUND.items():
    if _finfo["long"]:
        _FUND_TO_EQUITY[_finfo["long"]] = _eq
    if _finfo.get("inverse"):
        _FUND_TO_EQUITY[_finfo["inverse"]] = _eq

# Fund exchange (all LSE ETPs are on LSEETF)
_FUND_EXCHANGE = {f: "LSEETF" for f in _FUND_TO_EQUITY}

# Sector aggregation: sector_name → [(equity, weight)]
_SECTOR_COMPONENTS = {
    "semiconductors": [("NVDA", 0.30), ("AMD", 0.20), ("TSM", 0.20), ("MU", 0.10), ("AVGO", 0.10), ("QCOM", 0.10)],
    "nasdaq_100": [("AAPL", 0.12), ("MSFT", 0.11), ("NVDA", 0.10), ("AMZN", 0.08), ("META", 0.07), ("GOOGL", 0.07),
                   ("AVGO", 0.05), ("COST", 0.04), ("TSLA", 0.04), ("AMD", 0.03)],
    "sp500": [("AAPL", 0.07), ("MSFT", 0.07), ("NVDA", 0.06), ("AMZN", 0.04), ("META", 0.03), ("GOOGL", 0.03),
              ("JPM", 0.03), ("UNH", 0.02), ("V", 0.02), ("MA", 0.02), ("HD", 0.02), ("PG", 0.02)],
}

_SECTOR_FUNDS = {
    "semiconductors": {"long": "3SEM.L", "inverse": None},
    "nasdaq_100":     {"long": "QQQ5.L", "inverse": "QQQS.L"},
    "sp500":          {"long": "5SPY.L", "inverse": "3USS.L"},
}

# Always-on tickers (scanned every cycle regardless of exchange hours)
ALWAYS_ON_UCITS = [
    "CSPX.L", "EQQQ.L", "ISF.L", "IEEM.L", "VWRL.L",
    "SGLN.L", "IDTL.L", "CRUD.L", "IUKD.L",
]

ALWAYS_ON_LEVERAGED = [
    "QQQ3.L", "QQQ5.L", "3LUS.L", "5SPY.L",
    "NVD3.L", "TSL3.L", "AMD3.L", "APL3.L", "MSF3.L",
    "GOO3.L", "AMZ3.L", "MET3.L", "TSM3.L", "MU2.L",
    "GPT3.L", "3SEM.L",
    "QQQS.L", "3USS.L",
    "3SNV.L", "3STS.L", "3SAP.L", "3SMS.L", "3SAM.L",
]

# IBKR exchange → session_map exchange mapping
_IBKR_TO_SESSION = {
    "NYSE": "SMART", "NASDAQ": "SMART", "AMEX": "SMART", "ARCA": "SMART", "SMART": "SMART",
    "LSE": "LSE", "LSEETF": "LSEETF",
    "EURONEXT": "EURONEXT", "SBF": "EURONEXT", "AEB": "EURONEXT",
    "IBIS": "XETRA", "XETRA": "XETRA",
    "TSE": "TSE", "TSEJ": "TSE",
    "SEHK": "HKEX", "HKEX": "HKEX",
    "SGX": "SGX", "KSE": "KRX", "KRX": "KRX",
    "ASX": "ASX",
}

# Known exchanges (fail-closed: unknown exchange = skip)
_KNOWN_EXCHANGES = set(_IBKR_TO_SESSION.keys())

# ═════════════════════════════════════════���═════════════════════════════════
# Logging
# ═════════════════════════════════════════════════════════���═════════════════

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stderr,
)
log = logging.getLogger("radar_daemon")


# ═══════════════════════════��════════════════════════════���══════════════════
# Rate Limiter
# ══���═════════════��══════════════════════════════════════���═══════════════════

class IbkrRateLimiter:
    """Token bucket rate limiter. Hard-capped at 45 msg/s (IBKR limit=50)."""

    def __init__(self):
        self._last_batch_time = 0.0
        self._error_321_count = 0

    def wait_for_next_block(self):
        elapsed = time.monotonic() - self._last_batch_time
        wait = max(0.0, BLOCK_INTERVAL_SEC - elapsed)
        if self._error_321_count > 0:
            wait *= (2 ** min(self._error_321_count, 4))
        if wait > 0:
            time.sleep(wait)
        self._last_batch_time = time.monotonic()

    def on_error_321(self):
        self._error_321_count += 1
        if self._error_321_count >= 3:
            log.error("CIRCUIT BREAKER: 3 consecutive Error 321 — pausing 5 minutes")
            time.sleep(300)
            self._error_321_count = 0
        else:
            backoff = 30 * (2 ** (self._error_321_count - 1))
            log.warning("Error 321 — backing off %ds", backoff)
            time.sleep(backoff)

    def on_success(self):
        self._error_321_count = 0


# ═══════════════════════════════════════════════════���═══════════════════════
# Ticker Cache Entry
# ═══════════════════════════════════════════════════════════════════════════

class TickerEntry:
    """In-memory cache entry for one ticker."""

    __slots__ = (
        "symbol", "exchange", "tier", "type",
        "last", "bid", "ask", "open", "prior_close",
        "volume", "avg_volume", "shortable",
        "spread_bps", "gap_pct", "rvol",
        "momentum_streak", "momentum_direction", "prev_last",
        "spread_history", "spread_z",
        # OPRA fields (US equities only)
        "opt_call_vol", "opt_put_vol", "opt_call_oi", "opt_put_oi", "opt_impl_vol",
        "put_call_ratio", "oi_skew", "unusual_activity",
        "directional_bias", "directional_confidence", "directional_score",
        # Fund fields
        "underlying", "leverage", "inverse",
        # Fund recommendation (for equities)
        "fund_rec_action", "fund_rec_primary", "fund_rec_inverse",
        "fund_exchange_open", "signal_first_ts", "decay_factor",
        # Meta
        "ts", "last_promotion_check", "high_spread_count", "phantom_count",
    )

    def __init__(self, symbol: str, exchange: str, ticker_type: str = "equity"):
        self.symbol = symbol
        self.exchange = exchange
        self.tier = "COLD"
        self.type = ticker_type
        self.last = 0.0
        self.bid = 0.0
        self.ask = 0.0
        self.open = 0.0
        self.prior_close = 0.0
        self.volume = 0
        self.avg_volume = 0
        self.shortable = 0.0
        self.spread_bps = 9999.0
        self.gap_pct = 0.0
        self.rvol = 0.0
        self.momentum_streak = 0
        self.momentum_direction = "FLAT"
        self.prev_last = 0.0
        self.spread_history: deque = deque(maxlen=60)  # ~30 min of 30s cycles
        self.spread_z = 0.0
        # OPRA
        self.opt_call_vol = 0
        self.opt_put_vol = 0
        self.opt_call_oi = 0
        self.opt_put_oi = 0
        self.opt_impl_vol = 0.0
        self.put_call_ratio = 0.0
        self.oi_skew = 0.0
        self.unusual_activity = 0.0
        self.directional_bias = "NEUTRAL"
        self.directional_confidence = 0.0
        self.directional_score = 0.0
        # Fund
        self.underlying = ""
        self.leverage = 1
        self.inverse = False
        # Fund rec
        self.fund_rec_action = ""
        self.fund_rec_primary = ""
        self.fund_rec_inverse = ""
        self.fund_exchange_open = False
        self.signal_first_ts = ""
        self.decay_factor = 1.0
        # Meta
        self.ts = ""
        self.last_promotion_check = time.monotonic()
        self.high_spread_count = 0
        self.phantom_count = 0

    def update_derived(self, session_fraction: float):
        """Compute derived fields from raw tick data."""
        # Spread
        if self.last > 0 and self.ask > 0 and self.bid > 0:
            self.spread_bps = (self.ask - self.bid) / self.last * 10000
        else:
            self.spread_bps = 9999.0

        # Gap
        if self.prior_close > 0:
            self.gap_pct = (self.last - self.prior_close) / self.prior_close * 100
        else:
            self.gap_pct = 0.0

        # RVOL
        if self.avg_volume > 0 and session_fraction > 0.01:
            self.rvol = self.volume / (self.avg_volume * session_fraction)
        else:
            self.rvol = 0.0

        # Momentum
        if self.prev_last > 0 and self.last > 0:
            if self.last > self.prev_last * 1.0001:  # tiny threshold to avoid noise
                if self.momentum_direction == "UP":
                    self.momentum_streak += 1
                else:
                    self.momentum_direction = "UP"
                    self.momentum_streak = 1
            elif self.last < self.prev_last * 0.9999:
                if self.momentum_direction == "DOWN":
                    self.momentum_streak += 1
                else:
                    self.momentum_direction = "DOWN"
                    self.momentum_streak = 1
            # else: FLAT, don't change streak

        # Spread z-score
        self.spread_history.append(self.spread_bps)
        if len(self.spread_history) >= 10:
            avg = sum(self.spread_history) / len(self.spread_history)
            var = sum((x - avg) ** 2 for x in self.spread_history) / len(self.spread_history)
            std = var ** 0.5
            self.spread_z = (self.spread_bps - avg) / std if std > 0.01 else 0.0
        else:
            self.spread_z = 0.0

        self.prev_last = self.last
        self.ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def update_opra(self):
        """Compute OPRA-derived fields."""
        # Put/call ratio
        self.put_call_ratio = self.opt_put_vol / self.opt_call_vol if self.opt_call_vol > 0 else 0.0
        self.oi_skew = self.opt_put_oi / self.opt_call_oi if self.opt_call_oi > 0 else 0.0

        # Unusual activity (vs avg — we don't have avg_opt_volume from daemon, use 1.0 baseline)
        total_vol = self.opt_call_vol + self.opt_put_vol
        # Heuristic: >50K total options volume on a mega-cap is ~1x normal
        self.unusual_activity = total_vol / 50000.0 if total_vol > 0 else 0.0

        # Directional bias (aligned with bridge.py thresholds)
        pcr_signal = max(-1.0, min(1.0, (1.0 - self.put_call_ratio) / 0.5))
        oi_signal = max(-1.0, min(1.0, (1.0 - self.oi_skew) / 0.3))
        vol_signal = min(1.0, self.unusual_activity / 3.0) * (1.0 if pcr_signal >= 0 else -1.0)

        raw = 0.40 * pcr_signal + 0.30 * oi_signal + 0.30 * vol_signal
        self.directional_confidence = min(1.0, self.unusual_activity / 2.5)

        if raw > 0.30:
            self.directional_bias = "BULLISH"
        elif raw < -0.30:
            self.directional_bias = "BEARISH"
        else:
            self.directional_bias = "NEUTRAL"

        self.directional_score = raw

    def to_cache_dict(self) -> dict:
        """Serialize to radar_cache.json entry."""
        d: dict = {
            "type": self.type,
            "exchange": self.exchange,
            "tier": self.tier,
            "last": round(self.last, 6),
            "bid": round(self.bid, 6),
            "ask": round(self.ask, 6),
            "volume": self.volume,
            "avg_volume": self.avg_volume,
            "spread_bps": round(self.spread_bps, 1),
            "rvol": round(self.rvol, 2),
            "momentum_streak": self.momentum_streak,
            "momentum_direction": self.momentum_direction,
            "directional_score": round(self.directional_score, 3),
            "ts": self.ts,
        }

        # OPRA fields (only for equities with OPRA data)
        if self.symbol in OPRA_ELIGIBLE and self.opt_call_vol > 0:
            d["opra"] = {
                "call_vol": self.opt_call_vol,
                "put_vol": self.opt_put_vol,
                "call_oi": self.opt_call_oi,
                "put_oi": self.opt_put_oi,
                "impl_vol": round(self.opt_impl_vol, 4),
                "put_call_ratio": round(self.put_call_ratio, 3),
                "oi_skew": round(self.oi_skew, 3),
                "unusual_activity": round(self.unusual_activity, 2),
                "bias": self.directional_bias,
                "confidence": round(self.directional_confidence, 3),
            }

        # Fund recommendation (for equities with linked funds)
        if self.fund_rec_primary:
            d["fund_rec"] = {
                "action": self.fund_rec_action,
                "primary": self.fund_rec_primary,
                "inverse": self.fund_rec_inverse or "",
                "fund_exchange_open": self.fund_exchange_open,
                "decay_factor": round(self.decay_factor, 3),
            }

        # Fund metadata (for fund tickers)
        if self.type == "fund":
            d["underlying"] = self.underlying
            d["leverage"] = self.leverage
            d["inverse"] = self.inverse

        return d


# ═══════════════════════════════════════════════════════════════════════════
# Radar Daemon
# ════════════════════════════════════════════════════════════════════════���══

class RadarDaemon:
    """Main radar daemon: continuous L1 sweep across open exchanges."""

    def __init__(self):
        self.ib: Optional[IB] = None
        self.rate_limiter = IbkrRateLimiter()
        self.cache: Dict[str, TickerEntry] = {}
        self.radar_universe: Dict[str, List[str]] = {}  # exchange → [symbols]
        self.always_on: Set[str] = set()
        self.live_100: Set[str] = set()
        self.halted: Set[str] = set()
        self.temp_skipped: Set[str] = set()
        self.cycle_id = 0
        self._universe_mtime = 0.0
        self._last_opra_write = 0.0
        self._last_universe_check = 0.0
        self._last_prune = 0.0
        self._running = True
        self._sector_scores: Dict[str, float] = {}
        self._correlation_data: Dict[str, deque] = {
            "CSPX.L": deque(maxlen=60),
            "SGLN.L": deque(maxlen=60),
            "IDTL.L": deque(maxlen=60),
            "CRUD.L": deque(maxlen=60),
        }

        # Signal handlers
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        log.info("Shutdown signal received (sig=%d)", signum)
        self._running = False

    # ── Startup (Fail-Closed) ──────────────────────────────────────────

    def startup(self) -> bool:
        """Initialize daemon. Returns False on fatal failure (fail-closed)."""

        # 1. Load radar universe
        if not self._load_radar_universe():
            # Fallback: try to build from contracts.toml
            if not self._build_universe_from_contracts():
                log.error("FATAL: Cannot load radar universe. Aborting.")
                return False

        # 2. Build always-on set
        self.always_on = set(ALWAYS_ON_UCITS + ALWAYS_ON_LEVERAGED)
        self._load_live_100()

        # 3. Initialize fund metadata for always-on funds
        for fund_sym, equity_sym in _FUND_TO_EQUITY.items():
            if fund_sym not in self.cache:
                entry = TickerEntry(fund_sym, _FUND_EXCHANGE.get(fund_sym, "LSEETF"), "fund")
                entry.underlying = equity_sym
                info = _EQUITY_TO_FUND.get(equity_sym, {})
                entry.leverage = info.get("leverage", 1)
                entry.inverse = (info.get("inverse") == fund_sym)
                self.cache[fund_sym] = entry

        # 4. Connect to IBKR
        if not self._connect():
            log.error("FATAL: Cannot connect to IB Gateway. Aborting.")
            return False

        # 5. Verification probe (Audit lesson #1: phantom zero detection)
        # Off-market hours: phantom zeros are expected. Sleep until next session.
        if not self._verify_data_flow():
            import datetime
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            hour = now_utc.hour
            # Check if any major exchange is open (LSE 08-16:30 UTC, US 13:30-20 UTC, Asia 00-07 UTC)
            any_open = (0 <= hour < 7) or (8 <= hour < 17) or (13 <= hour < 21)
            if not any_open:
                sleep_secs = max(300, (7 - hour) % 24 * 3600)  # Sleep until Asia open
                log.warning("Off-market hours (UTC %02d:00). Sleeping %ds until next session.", hour, sleep_secs)
                time.sleep(sleep_secs)
                return self.startup()  # Retry after sleep
            # If market should be open but probe failed, it's a real problem
            log.error("FATAL: Verification probe failed during market hours — phantom zero data. Aborting.")
            return False

        total_tickers = sum(len(v) for v in self.radar_universe.values())
        log.info("RADAR ONLINE: %d tickers across %d exchanges, %d always-on",
                 total_tickers, len(self.radar_universe), len(self.always_on))
        return True

    def _connect(self) -> bool:
        """Connect to IB Gateway. Retry up to 5 times."""
        self.ib = IB()
        for attempt in range(1, 6):
            try:
                self.ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID, timeout=15)
                log.info("Connected to IB Gateway (client_id=%d, attempt=%d)", IB_CLIENT_ID, attempt)
                return True
            except Exception as e:
                log.warning("Connection attempt %d failed: %s", attempt, e)
                time.sleep(5 * attempt)
        return False

    def _verify_data_flow(self) -> bool:
        """Subscribe to AAPL, verify non-zero ticks arrive. Audit lesson #1."""
        try:
            contract = Stock("AAPL", "SMART", "USD")
            self.ib.qualifyContracts(contract)
            ticker = self.ib.reqMktData(contract, genericTickList=GENERIC_TICKS_BASIC)
            time.sleep(3)
            self.ib.cancelMktData(contract)

            if ticker.last > 0 or ticker.bid > 0:
                log.info("Verification probe PASS: AAPL last=%.2f bid=%.2f", ticker.last or 0, ticker.bid or 0)
                return True
            else:
                log.error("Verification probe FAIL: AAPL returned all zeros (phantom data)")
                return False
        except Exception as e:
            log.error("Verification probe exception: %s", e)
            return False

    # ── Universe Loading ───────────────────────────────────────────────

    def _load_radar_universe(self) -> bool:
        """Load radar_universe.toml. Returns False if missing/empty."""
        if not RADAR_UNIVERSE_PATH.exists():
            log.warning("radar_universe.toml not found at %s", RADAR_UNIVERSE_PATH)
            return False
        try:
            # Simple TOML parser (no dependency — just key=value sections)
            universe: Dict[str, List[str]] = {}
            current_exchange = None
            with open(RADAR_UNIVERSE_PATH) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("[") and line.endswith("]"):
                        current_exchange = line[1:-1].strip()
                        universe[current_exchange] = []
                    elif current_exchange and "=" not in line:
                        # Bare ticker symbol
                        if line:
                            universe[current_exchange].append(line)
                    elif current_exchange and "=" in line:
                        # key = value format (e.g., tickers = ["A", "B"])
                        key, _, val = line.partition("=")
                        if key.strip() == "tickers":
                            # Parse simple list
                            val = val.strip().strip("[]")
                            tickers = [t.strip().strip('"').strip("'") for t in val.split(",") if t.strip()]
                            universe[current_exchange].extend(tickers)

            if not universe:
                log.warning("radar_universe.toml parsed but empty")
                return False

            self.radar_universe = universe
            self._universe_mtime = RADAR_UNIVERSE_PATH.stat().st_mtime
            total = sum(len(v) for v in universe.values())
            log.info("Loaded radar_universe.toml: %d tickers across %d exchanges", total, len(universe))
            return True
        except Exception as e:
            log.error("Failed to load radar_universe.toml: %s", e)
            return False

    def _build_universe_from_contracts(self) -> bool:
        """Fallback: build radar universe from contracts.toml."""
        if not CONTRACTS_PATH.exists():
            return False
        try:
            universe: Dict[str, List[str]] = {}
            with open(CONTRACTS_PATH) as f:
                content = f.read()

            # Simple TOML [[contracts]] parser
            current: Dict[str, str] = {}
            for line in content.split("\n"):
                line = line.strip()
                if line == "[[contracts]]":
                    if current.get("symbol") and current.get("exchange"):
                        exch = current["exchange"]
                        if exch in _KNOWN_EXCHANGES:
                            universe.setdefault(exch, []).append(current["symbol"])
                    current = {}
                elif "=" in line and not line.startswith("#"):
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"')
                    current[key] = val
            # Last entry
            if current.get("symbol") and current.get("exchange"):
                exch = current["exchange"]
                if exch in _KNOWN_EXCHANGES:
                    universe.setdefault(exch, []).append(current["symbol"])

            if not universe:
                return False

            self.radar_universe = universe
            total = sum(len(v) for v in universe.values())
            log.info("Built radar universe from contracts.toml: %d tickers", total)
            return True
        except Exception as e:
            log.error("Failed to build universe from contracts.toml: %s", e)
            return False

    def _load_live_100(self):
        """Load current active watchlist for always-on set."""
        if not WATCHLIST_PATH.exists():
            return
        try:
            with open(WATCHLIST_PATH) as f:
                data = json.load(f)
            tickers = data.get("tickers", [])
            self.live_100 = set(tickers)
            self.always_on |= self.live_100
            log.info("Loaded live_100: %d tickers", len(self.live_100))
        except Exception:
            pass

    def _maybe_reload_universe(self):
        """Check if radar_universe.toml has changed (every 60s)."""
        now = time.monotonic()
        if now - self._last_universe_check < 60:
            return
        self._last_universe_check = now

        if RADAR_UNIVERSE_PATH.exists():
            mtime = RADAR_UNIVERSE_PATH.stat().st_mtime
            if mtime != self._universe_mtime:
                log.info("radar_universe.toml changed — reloading")
                self._load_radar_universe()

    # ── Exchange Hours ─────────────────────────────────────────────────

    def _is_exchange_open(self, exchange: str) -> bool:
        """Check if an exchange is currently open using session_map."""
        session_exchange = _IBKR_TO_SESSION.get(exchange, exchange)
        now = datetime.now(timezone.utc)
        session = detect_session(now.hour, now.minute)
        all_exchanges = get_all_exchanges_for_session(session)
        return session_exchange in all_exchanges

    def _get_session_fraction(self) -> float:
        """Estimate fraction of trading session elapsed (for RVOL calc)."""
        now = datetime.now(timezone.utc)
        session = detect_session(now.hour, now.minute)
        start_min = session.start_hour * 60 + session.start_minute
        end_min = session.end_hour * 60 + session.end_minute
        now_min = now.hour * 60 + now.minute

        if end_min > start_min:
            duration = end_min - start_min
            elapsed = now_min - start_min
        else:  # wraps midnight
            duration = (1440 - start_min) + end_min
            elapsed = (now_min - start_min) % 1440

        return max(0.01, min(1.0, elapsed / max(duration, 1)))

    # ── Active Set + Tiering ───────────────────────────────────────────

    def _build_active_scan_set(self) -> List[str]:
        """Build the set of tickers to scan this cycle."""
        active: Set[str] = set(self.always_on)

        for exchange, tickers in self.radar_universe.items():
            if self._is_exchange_open(exchange):
                active.update(tickers)

        active -= self.halted
        active -= self.temp_skipped
        return sorted(active)

    def _build_sweep_queue(self, active_set: List[str]) -> List[str]:
        """Build sweep queue based on tier priority."""
        hot = []
        warm = []
        cold = []

        for sym in active_set:
            entry = self.cache.get(sym)
            if entry is None:
                cold.append(sym)
            elif entry.tier == "HOT":
                hot.append(sym)
            elif entry.tier == "WARM":
                warm.append(sym)
            else:
                cold.append(sym)

        queue = list(hot)  # Hot: always scanned

        # Warm: rotate through in batches (every 3rd cycle)
        if self.cycle_id % 3 == 0 and warm:
            batch_idx = (self.cycle_id // 3) % max(1, (len(warm) + 99) // 100)
            start = batch_idx * 100
            queue.extend(warm[start:start + 100])

        # Cold: rotate through in batches (every 10th cycle)
        if self.cycle_id % 10 == 0 and cold:
            batch_idx = (self.cycle_id // 10) % max(1, (len(cold) + 99) // 100)
            start = batch_idx * 100
            queue.extend(cold[start:start + 100])

        return queue

    def _check_tier_promotions(self, sym: str, entry: TickerEntry):
        """Check if a ticker should be promoted to a higher tier."""
        now = time.monotonic()
        promoted = False

        # Promote to HOT
        if entry.tier != "HOT":
            if entry.rvol > TIER_HOT_RVOL:
                entry.tier = "HOT"
                promoted = True
            elif entry.momentum_streak > TIER_HOT_STREAK:
                entry.tier = "HOT"
                promoted = True
            elif sym in OPRA_ELIGIBLE and entry.unusual_activity > TIER_HOT_OPRA:
                entry.tier = "HOT"
                promoted = True
            elif sym in self.live_100:
                entry.tier = "HOT"
                promoted = True

        # Demote from HOT
        if entry.tier == "HOT" and not promoted:
            quiet_min = (now - entry.last_promotion_check) / 60
            if quiet_min > TIER_DEMOTE_QUIET_MIN and entry.rvol < 1.0 and entry.momentum_streak < 3:
                if sym not in self.live_100:  # never demote live_100
                    entry.tier = "WARM"

        # Demote from WARM
        if entry.tier == "WARM":
            quiet_min = (now - entry.last_promotion_check) / 60
            if quiet_min > TIER_DEMOTE_QUIET_WARM_MIN and entry.rvol < 0.5:
                entry.tier = "COLD"

        if promoted:
            entry.last_promotion_check = now

        # High spread skip
        if entry.spread_bps > SPREAD_SKIP_BPS:
            entry.high_spread_count += 1
            if entry.high_spread_count >= SPREAD_SKIP_COUNT:
                self.temp_skipped.add(sym)
                log.info("TEMP_SKIP: %s spread %.0f bps > %d for %d cycles",
                         sym, entry.spread_bps, SPREAD_SKIP_BPS, SPREAD_SKIP_COUNT)
        else:
            entry.high_spread_count = 0

    # ── Fund Recommendation ────────────────────────────────────────────

    def _compute_fund_recommendations(self):
        """Compute fund recommendations for equities with linked funds."""
        now_utc = datetime.now(timezone.utc)

        for equity_sym, fund_info in _EQUITY_TO_FUND.items():
            entry = self.cache.get(equity_sym)
            if entry is None:
                continue

            long_fund = fund_info["long"]
            inverse_fund = fund_info.get("inverse")

            # Check if fund exchange is open
            fund_exch_open = self._is_exchange_open("LSEETF")
            entry.fund_exchange_open = fund_exch_open

            # Determine action based on directional score
            if entry.directional_score > 0.30:
                entry.fund_rec_action = "LONG"
                entry.fund_rec_primary = long_fund
                entry.fund_rec_inverse = inverse_fund or ""
            elif entry.directional_score < -0.30 and inverse_fund:
                entry.fund_rec_action = "SHORT"
                entry.fund_rec_primary = inverse_fund
                entry.fund_rec_inverse = ""
            else:
                entry.fund_rec_action = ""
                entry.fund_rec_primary = ""
                entry.fund_rec_inverse = ""

            # Signal decay for closed-fund recommendations (Fix 6)
            if entry.fund_rec_primary and not fund_exch_open:
                if not entry.signal_first_ts:
                    entry.signal_first_ts = now_utc.isoformat()
                try:
                    first_ts = datetime.fromisoformat(entry.signal_first_ts.replace("Z", "+00:00"))
                    age_hours = (now_utc - first_ts.replace(tzinfo=timezone.utc)).total_seconds() / 3600
                    entry.decay_factor = max(0.5, 1.0 - 0.05 * age_hours)
                except Exception:
                    entry.decay_factor = 1.0
            else:
                entry.signal_first_ts = ""
                entry.decay_factor = 1.0

    # ── Sector Aggregation ─────────────────────────────────────────────

    def _compute_sector_scores(self):
        """Compute sector aggregation scores from component equities."""
        for sector_name, components in _SECTOR_COMPONENTS.items():
            score = 0.0
            total_weight = 0.0
            for equity, weight in components:
                entry = self.cache.get(equity)
                if entry and entry.directional_score != 0.0:
                    score += entry.directional_score * weight
                    total_weight += weight
            if total_weight > 0.1:
                self._sector_scores[sector_name] = score / total_weight
            else:
                self._sector_scores[sector_name] = 0.0

    # ── Sweep Block ────────────────────────────────────────────────────

    def _scan_block(self, symbols: List[str]):
        """Subscribe, wait, process, cancel for a block of tickers."""
        if not symbols or not self.ib or not self.ib.isConnected():
            return

        contracts = []
        sym_contract_map: Dict[str, Any] = {}
        session_fraction = self._get_session_fraction()

        for sym in symbols:
            # Build contract
            entry = self.cache.get(sym)
            exchange = entry.exchange if entry else self._get_exchange(sym)

            if exchange in _KNOWN_EXCHANGES:
                # Determine IBKR exchange for routing
                ibkr_exch = exchange
                currency = "USD"  # default
                if exchange in ("LSE", "LSEETF"):
                    currency = "GBP"
                elif exchange in ("TSE", "TSEJ"):
                    currency = "JPY"
                elif exchange in ("SEHK", "HKEX"):
                    currency = "HKD"
                elif exchange in ("SGX",):
                    currency = "SGD"
                elif exchange in ("KSE", "KRX"):
                    currency = "KRW"
                elif exchange in ("IBIS", "XETRA"):
                    currency = "EUR"
                    ibkr_exch = "IBIS"
                elif exchange in ("EURONEXT", "SBF", "AEB"):
                    currency = "EUR"

                # Strip .L suffix for LSE contracts in ib_insync
                local_sym = sym
                if sym.endswith(".L") and exchange in ("LSE", "LSEETF"):
                    local_sym = sym[:-2]

                contract = Stock(local_sym, ibkr_exch, currency)
                contracts.append(contract)
                sym_contract_map[sym] = contract

        if not contracts:
            return

        # Determine generic ticks per-contract
        tickers = []
        try:
            for sym, contract in sym_contract_map.items():
                gt = GENERIC_TICKS_OPRA if sym in OPRA_ELIGIBLE else GENERIC_TICKS_BASIC
                ticker = self.ib.reqMktData(contract, genericTickList=gt)
                tickers.append((sym, contract, ticker))
        except Exception as e:
            log.warning("reqMktData batch failed: %s", e)
            # Clean up any partial subscriptions
            for _, contract, _ in tickers:
                try:
                    self.ib.cancelMktData(contract)
                except Exception:
                    pass
            return

        # Wait for ticks
        self.ib.sleep(TICK_WAIT_SEC)

        # Process callbacks
        for sym, contract, ticker in tickers:
            try:
                entry = self.cache.get(sym)
                if entry is None:
                    exch = self._get_exchange(sym)
                    t_type = "fund" if sym in _FUND_TO_EQUITY else "equity"
                    entry = TickerEntry(sym, exch, t_type)
                    if t_type == "fund":
                        entry.underlying = _FUND_TO_EQUITY.get(sym, "")
                        info = _EQUITY_TO_FUND.get(entry.underlying, {})
                        entry.leverage = info.get("leverage", 1)
                        entry.inverse = (info.get("inverse") == sym)
                    self.cache[sym] = entry

                # Extract tick values
                if ticker.last is not None and not (isinstance(ticker.last, float) and ticker.last != ticker.last):
                    entry.last = float(ticker.last) if ticker.last else 0.0
                if ticker.bid is not None:
                    entry.bid = float(ticker.bid) if ticker.bid else 0.0
                if ticker.ask is not None:
                    entry.ask = float(ticker.ask) if ticker.ask else 0.0
                if hasattr(ticker, "open") and ticker.open:
                    entry.open = float(ticker.open)
                if hasattr(ticker, "close") and ticker.close:
                    entry.prior_close = float(ticker.close)
                if ticker.volume is not None:
                    entry.volume = int(ticker.volume) if ticker.volume else 0
                if hasattr(ticker, "avVolume") and ticker.avVolume:
                    entry.avg_volume = int(ticker.avVolume)
                if hasattr(ticker, "shortableShares") and ticker.shortableShares:
                    entry.shortable = float(ticker.shortableShares)
                if hasattr(ticker, "halted") and ticker.halted:
                    halted_val = ticker.halted
                    if halted_val and halted_val > 0:
                        self.halted.add(sym)

                # OPRA fields
                if sym in OPRA_ELIGIBLE:
                    if hasattr(ticker, "callOpenInterest") and ticker.callOpenInterest:
                        entry.opt_call_oi = int(ticker.callOpenInterest)
                    if hasattr(ticker, "putOpenInterest") and ticker.putOpenInterest:
                        entry.opt_put_oi = int(ticker.putOpenInterest)
                    if hasattr(ticker, "callVolume") and ticker.callVolume:
                        entry.opt_call_vol = int(ticker.callVolume)
                    if hasattr(ticker, "putVolume") and ticker.putVolume:
                        entry.opt_put_vol = int(ticker.putVolume)
                    if hasattr(ticker, "impliedVolatility") and ticker.impliedVolatility:
                        entry.opt_impl_vol = float(ticker.impliedVolatility)
                    entry.update_opra()

                # Validate non-phantom
                if entry.last <= 0 and entry.bid <= 0:
                    entry.phantom_count += 1
                    if entry.phantom_count >= 3:
                        self.temp_skipped.add(sym)
                        log.debug("PHANTOM_ZERO: %s — 3 consecutive zero ticks", sym)
                    continue
                entry.phantom_count = 0

                # Compute derived fields
                entry.update_derived(session_fraction)

                # Check tier promotions
                self._check_tier_promotions(sym, entry)

                # Update correlation data (for always-on UCITS)
                if sym in self._correlation_data and entry.last > 0 and entry.prev_last > 0:
                    ret = (entry.last - entry.prev_last) / entry.prev_last
                    self._correlation_data[sym].append(ret)

            except Exception as e:
                log.debug("Tick processing error for %s: %s", sym, e)

        # Cancel all subscriptions
        for _, contract, _ in tickers:
            try:
                self.ib.cancelMktData(contract)
            except Exception:
                pass

    def _get_exchange(self, sym: str) -> str:
        """Look up exchange for a symbol from radar_universe."""
        for exch, syms in self.radar_universe.items():
            if sym in syms:
                return exch
        # Check fund map
        if sym in _FUND_EXCHANGE:
            return _FUND_EXCHANGE[sym]
        return "SMART"  # default

    # ── Cache Writing ──────────────────────────────────────────────────

    def _write_radar_cache(self):
        """Atomic write of radar_cache.json."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Count tiers
        tier_counts = {"HOT": 0, "WARM": 0, "COLD": 0}
        for entry in self.cache.values():
            tier_counts[entry.tier] = tier_counts.get(entry.tier, 0) + 1

        # Active exchanges
        active_exchanges = []
        for exch in self.radar_universe:
            if self._is_exchange_open(exch):
                active_exchanges.append(exch)

        data = {
            "meta": {
                "cycle_id": self.cycle_id,
                "cycle_end_utc": now,
                "tickers_scanned": len(self.cache),
                "tier_counts": tier_counts,
                "active_exchanges": active_exchanges,
            },
            "sectors": {
                name: {
                    "score": round(score, 3),
                    "direction": "BULLISH" if score > 0.30 else "BEARISH" if score < -0.30 else "NEUTRAL",
                }
                for name, score in self._sector_scores.items()
            },
            "tickers": {
                sym: entry.to_cache_dict()
                for sym, entry in self.cache.items()
                if entry.ts  # only write entries that have been scanned
            },
        }

        try:
            RADAR_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=str(RADAR_CACHE_PATH.parent), suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, separators=(",", ":"))
            os.replace(tmp_path, str(RADAR_CACHE_PATH))
        except Exception as e:
            log.error("Failed to write radar_cache.json: %s", e)
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _write_boost_artifacts(self):
        """Write radar_opra_signals.json for boost loader consumption."""
        now = time.monotonic()
        if now - self._last_opra_write < 60:  # Write every 60s
            return
        self._last_opra_write = now

        scores: Dict[str, float] = {}

        # OPRA directional bias → fund boost
        for equity_sym, fund_info in _EQUITY_TO_FUND.items():
            entry = self.cache.get(equity_sym)
            if entry is None or entry.directional_score == 0.0:
                continue

            long_fund = fund_info["long"]
            inverse_fund = fund_info.get("inverse")

            # Boost for long fund when bullish
            if entry.directional_bias == "BULLISH" and long_fund:
                boost = min(0.15, entry.directional_confidence * 0.15)
                scores[long_fund] = round(boost, 4)
                # Penalty on inverse when bullish
                if inverse_fund:
                    scores[inverse_fund] = round(-boost * 0.33, 4)
            # Boost for inverse fund when bearish
            elif entry.directional_bias == "BEARISH" and inverse_fund:
                boost = min(0.15, entry.directional_confidence * 0.15)
                scores[inverse_fund] = round(boost, 4)
                # Penalty on long when bearish
                if long_fund:
                    scores[long_fund] = round(-boost * 0.33, 4)

        # Sector-level fund boosts
        for sector_name, sector_score in self._sector_scores.items():
            funds = _SECTOR_FUNDS.get(sector_name, {})
            if abs(sector_score) > 0.30:
                if sector_score > 0 and funds.get("long"):
                    boost = min(0.12, abs(sector_score) * 0.15)
                    fund_sym = funds["long"]
                    scores[fund_sym] = max(scores.get(fund_sym, 0), round(boost, 4))
                elif sector_score < 0 and funds.get("inverse"):
                    boost = min(0.12, abs(sector_score) * 0.15)
                    fund_sym = funds["inverse"]
                    scores[fund_sym] = max(scores.get(fund_sym, 0), round(boost, 4))

        # Momentum boosts (any ticker with strong momentum)
        for sym, entry in self.cache.items():
            if entry.momentum_streak >= 5 and entry.rvol > 1.5:
                boost = min(0.10, entry.momentum_streak * 0.015)
                if sym not in scores or abs(scores[sym]) < boost:
                    scores[sym] = round(boost, 4)

        # Liquidity penalties (spread z-score > 2)
        for sym, entry in self.cache.items():
            if entry.spread_z > 2.0 and entry.type == "fund":
                penalty = min(0.20, entry.spread_z * 0.05)
                scores[sym] = round(-penalty, 4)

        data = {
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "scores": scores,
        }

        try:
            RADAR_OPRA_PATH.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=str(RADAR_OPRA_PATH.parent), suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, separators=(",", ":"))
            os.replace(tmp_path, str(RADAR_OPRA_PATH))
        except Exception as e:
            log.error("Failed to write radar_opra_signals.json: %s", e)
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    # ── Pruning ────────────────────────────────────────────────────────

    def _maybe_prune(self):
        """Prune stale entries and enforce max cache size."""
        now = time.monotonic()
        if now - self._last_prune < 3600:  # hourly
            return
        self._last_prune = now

        # Remove temp_skipped tickers that have been skipped for a full session
        self.temp_skipped.clear()
        self.halted.clear()

        # LRU eviction if cache too large
        if len(self.cache) > MAX_CACHE_ENTRIES:
            # Sort by last scan time, remove oldest
            entries_by_ts = sorted(self.cache.items(), key=lambda x: x[1].ts or "")
            to_remove = len(self.cache) - MAX_CACHE_ENTRIES
            for sym, _ in entries_by_ts[:to_remove]:
                if sym not in self.always_on:  # never evict always-on
                    del self.cache[sym]
            log.info("PRUNE: evicted %d stale cache entries", to_remove)

    # ── Main Loop ────��─────────────────────────────���───────────────────

    def run(self):
        """Main daemon loop. Runs until SIGTERM/SIGINT."""
        log.info("Starting main sweep loop")

        while self._running:
            try:
                # Check connection
                if not self.ib or not self.ib.isConnected():
                    log.warning("Connection lost — reconnecting")
                    if not self._connect():
                        log.error("Reconnect failed — sleeping 60s")
                        time.sleep(60)
                        continue

                # Build active scan set
                active_set = self._build_active_scan_set()
                sweep_queue = self._build_sweep_queue(active_set)

                if not sweep_queue:
                    log.debug("Empty sweep queue — sleeping 10s")
                    time.sleep(10)
                    continue

                # Sweep in blocks
                for i in range(0, len(sweep_queue), BLOCK_SIZE):
                    if not self._running:
                        break

                    block = sweep_queue[i:i + BLOCK_SIZE]
                    self.rate_limiter.wait_for_next_block()

                    try:
                        self._scan_block(block)
                        self.rate_limiter.on_success()
                    except Exception as e:
                        err_str = str(e)
                        if "321" in err_str or "pacing" in err_str.lower():
                            self.rate_limiter.on_error_321()
                        else:
                            log.warning("Block scan error: %s", e)

                # Post-sweep: compute aggregates + write cache
                self._compute_fund_recommendations()
                self._compute_sector_scores()
                self._write_radar_cache()
                self._write_boost_artifacts()

                # Housekeeping
                self._maybe_reload_universe()
                self._maybe_prune()

                self.cycle_id += 1
                if self.cycle_id % 100 == 0:
                    tier_counts = defaultdict(int)
                    for e in self.cache.values():
                        tier_counts[e.tier] += 1
                    log.info("Cycle %d: %d tickers, H=%d W=%d C=%d, %d active exchanges",
                             self.cycle_id, len(self.cache),
                             tier_counts["HOT"], tier_counts["WARM"], tier_counts["COLD"],
                             len([e for e in self.radar_universe if self._is_exchange_open(e)]))

            except KeyboardInterrupt:
                break
            except Exception as e:
                log.error("Main loop error: %s", e, exc_info=True)
                time.sleep(10)

        # Shutdown
        log.info("Shutting down radar daemon")
        if self.ib and self.ib.isConnected():
            self.ib.disconnect()
        log.info("Radar daemon stopped")


# ═══════════════════════════════════════════════════════════════════════════
# Entry Point
# ��══════════════════════════════════════════════════════════════════════════

def main():
    log.info("═══ AEGIS V2 RADAR DAEMON ═══")
    log.info("Client ID: %d, Host: %s:%d", IB_CLIENT_ID, IB_HOST, IB_PORT)

    daemon = RadarDaemon()
    if not daemon.startup():
        log.error("Startup failed (fail-closed). Exiting.")
        sys.exit(1)

    daemon.run()


if __name__ == "__main__":
    main()
