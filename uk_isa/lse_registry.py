"""
NZT-48 V8.0 — LSE Leveraged Product Registry
==============================================
Automated catalog of ALL leveraged ETPs and ETFs listed on the London Stock Exchange.
Updates daily, detects new listings and delistings, classifies each product.

Classification per product:
  - Underlying asset
  - Sector
  - Geographic exposure
  - Leverage factor
  - Long or short bias
  - Liquidity profile
  - Average daily volume (rolling 20-day)
  - Tracking structure (ETP / ETF / ETC)

Storage: SQLite table `lse_registry` (persistent, daily snapshot)
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
import sys

import pandas as pd
import yfinance as yf

try:
    import requests as _requests
except ImportError:
    _requests = None  # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.safe_math import safe_divide

logger = logging.getLogger("nzt48.lse_registry")

# ---------------------------------------------------------------------------
# Seed catalog — known LSE leveraged products
# This list is extended dynamically via discovery. Format:
#   (ticker, name, underlying, sector, geography, leverage, bias, structure)
# ---------------------------------------------------------------------------
_SEED_CATALOG: list[tuple] = [
    # ── Broad Index Long ────────────────────────────────────────────────────
    ("QQQ3.L", "WisdomTree NASDAQ 100 3x Daily ETP", "QQQ/NDX", "Technology", "US", 3.0, "LONG", "ETP"),
    ("3LUS.L", "WisdomTree S&P 500 3x Daily ETP", "SPY/SPX", "Broad Market", "US", 3.0, "LONG", "ETP"),
    ("QQQ5.L", "WisdomTree NASDAQ 100 5x Daily ETP", "QQQ/NDX", "Technology", "US", 5.0, "LONG", "ETP"),
    ("SP5L.L", "WisdomTree S&P 500 5x Daily ETP", "SPY/SPX", "Broad Market", "US", 5.0, "LONG", "ETP"),
    ("3LDE.L", "WisdomTree DAX 3x Daily ETP", "DAX", "Broad Market", "EU", 3.0, "LONG", "ETP"),
    ("3LEU.L", "WisdomTree Euro STOXX 50 3x Daily ETP", "EuroStoxx50", "Broad Market", "EU", 3.0, "LONG", "ETP"),
    ("3LJP.L", "WisdomTree Nikkei 225 3x Daily ETP", "Nikkei225", "Broad Market", "JP", 3.0, "LONG", "ETP"),
    ("3LHK.L", "WisdomTree Hang Seng 3x Daily ETP", "HSI", "Broad Market", "HK", 3.0, "LONG", "ETP"),
    # ── Broad Index Short ────────────────────────────────────────────────────
    ("QQQS.L", "WisdomTree NASDAQ 100 3x Daily Short ETP", "QQQ/NDX", "Technology", "US", -3.0, "SHORT", "ETP"),
    ("3USS.L", "WisdomTree S&P 500 3x Daily Short ETP", "SPY/SPX", "Broad Market", "US", -3.0, "SHORT", "ETP"),
    ("QQQE.L", "WisdomTree NASDAQ 100 5x Daily Short ETP", "QQQ/NDX", "Technology", "US", -5.0, "SHORT", "ETP"),
    ("SP5S.L", "WisdomTree S&P 500 5x Daily Short ETP", "SPY/SPX", "Broad Market", "US", -5.0, "SHORT", "ETP"),
    ("3SDE.L", "WisdomTree DAX 3x Daily Short ETP", "DAX", "Broad Market", "EU", -3.0, "SHORT", "ETP"),
    ("3SEU.L", "WisdomTree Euro STOXX 50 3x Daily Short ETP", "EuroStoxx50", "Broad Market", "EU", -3.0, "SHORT", "ETP"),
    # ── Sector Long ──────────────────────────────────────────────────────────
    ("3SEM.L", "WisdomTree Semiconductors 3x Daily ETP", "SOX/SMH", "Semiconductors", "US", 3.0, "LONG", "ETP"),
    ("GPT3.L", "WisdomTree US AI 3x Daily ETP", "AI/Tech", "AI & Technology", "US", 3.0, "LONG", "ETP"),
    ("3LEN.L", "WisdomTree Energy 3x Daily ETP", "XLE/Energy", "Energy", "US", 3.0, "LONG", "ETP"),
    ("3LFI.L", "WisdomTree Financials 3x Daily ETP", "XLF/Finance", "Financials", "US", 3.0, "LONG", "ETP"),
    ("3LHC.L", "WisdomTree Healthcare 3x Daily ETP", "XLV/Health", "Healthcare", "US", 3.0, "LONG", "ETP"),
    # ── Sector Short ─────────────────────────────────────────────────────────
    ("3SSM.L", "WisdomTree Semiconductors 3x Daily Short ETP", "SOX/SMH", "Semiconductors", "US", -3.0, "SHORT", "ETP"),
    ("3SEN.L", "WisdomTree Energy 3x Daily Short ETP", "XLE/Energy", "Energy", "US", -3.0, "SHORT", "ETP"),
    # ── Single-Stock Long ETPs ────────────────────────────────────────────────
    ("NVD3.L", "GraniteShares NVIDIA 3x Long Daily ETP", "NVDA", "Semiconductors", "US", 3.0, "LONG", "ETP"),
    ("TSL3.L", "GraniteShares Tesla 3x Long Daily ETP", "TSLA", "EV/Tech", "US", 3.0, "LONG", "ETP"),
    ("TSM3.L", "GraniteShares TSMC 3x Long Daily ETP", "TSM", "Semiconductors", "TW", 3.0, "LONG", "ETP"),
    ("MU2.L", "GraniteShares Micron 2x Long Daily ETP", "MU", "Semiconductors", "US", 2.0, "LONG", "ETP"),
    ("MFAS.L", "GraniteShares Meta 3x Long Daily ETP", "META", "Technology", "US", 3.0, "LONG", "ETP"),
    ("AMZL.L", "GraniteShares Amazon 3x Long Daily ETP", "AMZN", "Technology", "US", 3.0, "LONG", "ETP"),
    ("MSFL.L", "GraniteShares Microsoft 3x Long Daily ETP", "MSFT", "Technology", "US", 3.0, "LONG", "ETP"),
    ("AAPLL.L", "GraniteShares Apple 3x Long Daily ETP", "AAPL", "Technology", "US", 3.0, "LONG", "ETP"),
    ("GOOGL3.L", "GraniteShares Alphabet 3x Long Daily ETP", "GOOGL", "Technology", "US", 3.0, "LONG", "ETP"),
    ("AMD3.L", "GraniteShares AMD 3x Long Daily ETP", "AMD", "Semiconductors", "US", 3.0, "LONG", "ETP"),
    ("AVGO3.L", "GraniteShares Broadcom 3x Long Daily ETP", "AVGO", "Semiconductors", "US", 3.0, "LONG", "ETP"),
    ("ARM3.L", "GraniteShares ARM 3x Long Daily ETP", "ARM", "Semiconductors", "GB", 3.0, "LONG", "ETP"),
    ("PLTR3.L", "GraniteShares Palantir 3x Long Daily ETP", "PLTR", "AI/Tech", "US", 3.0, "LONG", "ETP"),
    ("COIN3.L", "GraniteShares Coinbase 3x Long Daily ETP", "COIN", "Crypto/Finance", "US", 3.0, "LONG", "ETP"),
    ("MSTRL.L", "GraniteShares MicroStrategy 3x Long Daily ETP", "MSTR", "Crypto/Tech", "US", 3.0, "LONG", "ETP"),
    ("BAC3.L", "GraniteShares Bank of America 3x Long Daily ETP", "BAC", "Financials", "US", 3.0, "LONG", "ETP"),
    ("GS3.L", "GraniteShares Goldman Sachs 3x Long Daily ETP", "GS", "Financials", "US", 3.0, "LONG", "ETP"),
    ("XOM3.L", "GraniteShares ExxonMobil 3x Long Daily ETP", "XOM", "Energy", "US", 3.0, "LONG", "ETP"),
    ("LLY3.L", "GraniteShares Eli Lilly 3x Long Daily ETP", "LLY", "Healthcare", "US", 3.0, "LONG", "ETP"),
    # ── Single-Stock Short ETPs ───────────────────────────────────────────────
    ("NVDS.L", "GraniteShares NVIDIA -3x Short Daily ETP", "NVDA", "Semiconductors", "US", -3.0, "SHORT", "ETP"),
    ("TSLS.L", "GraniteShares Tesla -3x Short Daily ETP", "TSLA", "EV/Tech", "US", -3.0, "SHORT", "ETP"),
    ("MFASS.L", "GraniteShares Meta -3x Short Daily ETP", "META", "Technology", "US", -3.0, "SHORT", "ETP"),
    ("AMZS.L", "GraniteShares Amazon -3x Short Daily ETP", "AMZN", "Technology", "US", -3.0, "SHORT", "ETP"),
    ("MSFS.L", "GraniteShares Microsoft -3x Short Daily ETP", "MSFT", "Technology", "US", -3.0, "SHORT", "ETP"),
    # ── Commodity Leveraged ───────────────────────────────────────────────────
    ("3GOL.L", "WisdomTree Gold 3x Daily ETP", "Gold/GC", "Commodities", "Global", 3.0, "LONG", "ETC"),
    ("3SIL.L", "WisdomTree Silver 3x Daily ETP", "Silver/SI", "Commodities", "Global", 3.0, "LONG", "ETC"),
    ("3OIL.L", "WisdomTree WTI Crude Oil 3x Daily ETP", "CL/Oil", "Energy", "Global", 3.0, "LONG", "ETC"),
    ("GOIL.L", "WisdomTree Gold 3x Daily Short ETP", "Gold/GC", "Commodities", "Global", -3.0, "SHORT", "ETC"),
    ("SOIL.L", "WisdomTree WTI Crude Oil 3x Daily Short ETP", "CL/Oil", "Energy", "Global", -3.0, "SHORT", "ETC"),
]

# Deduplicate seed catalog by ticker
_TICKER_SET: set[str] = set()
_DEDUPED_CATALOG: list[tuple] = []
for _row in _SEED_CATALOG:
    if _row[0] not in _TICKER_SET:
        _TICKER_SET.add(_row[0])
        _DEDUPED_CATALOG.append(_row)
_SEED_CATALOG = _DEDUPED_CATALOG


@dataclass
class LSEProduct:
    """Represents a single LSE leveraged product with full classification."""

    ticker: str
    name: str
    underlying: str
    sector: str
    geography: str
    leverage_factor: float
    bias: str                       # LONG or SHORT
    structure: str                  # ETP / ETF / ETC
    avg_daily_volume: float = 0.0
    last_price: float = 0.0
    price_change_pct: float = 0.0
    is_active: bool = True
    last_updated: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    liquidity_tier: str = "UNKNOWN"  # HIGH / MEDIUM / LOW / ILLIQUID
    aser: float = 0.0               # I-02: ADR-to-Spread Efficiency Ratio

    @property
    def abs_leverage(self) -> float:
        return abs(self.leverage_factor)

    @property
    def leverage_label(self) -> str:
        sign = "+" if self.leverage_factor > 0 else "-"
        return f"{sign}{self.abs_leverage:.0f}x"


class LSERegistry:
    """
    Automated registry of all LSE-listed leveraged products.

    Responsibilities:
      1. Maintain a persistent catalog in SQLite (`lse_registry` table)
      2. Refresh daily: fetch live quotes, update volumes, detect delistings
      3. Classify liquidity tier from average daily volume
      4. Expose query methods for the rest of the V2 engine

    Usage:
        registry = LSERegistry(db_path="data/nzt48.db")
        registry.refresh()                    # call daily
        products = registry.get_all_active()  # all live products
        longs = registry.get_by_bias("LONG")  # long-only
        semis = registry.get_by_sector("Semiconductors")
    """

    _LIQUIDITY_THRESHOLDS = {
        "HIGH": 500_000,
        "MEDIUM": 100_000,
        "LOW": 10_000,
    }

    def __init__(self, db_path: str = "data/nzt48.db") -> None:
        self._db_path = db_path
        self._products: dict[str, LSEProduct] = {}
        self._last_refresh: Optional[datetime] = None
        self._init_db()
        self._load_from_db()
        # Seed with known catalog if DB is empty
        if not self._products:
            self._seed_from_catalog()

    # ── DB setup ─────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lse_registry (
                    ticker TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    underlying TEXT,
                    sector TEXT,
                    geography TEXT,
                    leverage_factor REAL,
                    bias TEXT,
                    structure TEXT,
                    avg_daily_volume REAL DEFAULT 0,
                    last_price REAL DEFAULT 0,
                    price_change_pct REAL DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    last_updated TEXT,
                    liquidity_tier TEXT DEFAULT 'UNKNOWN',
                    first_seen TEXT,
                    last_seen TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lse_registry_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_date TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    last_price REAL,
                    price_change_pct REAL,
                    avg_daily_volume REAL,
                    is_active INTEGER,
                    liquidity_tier TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reg_snap_date ON lse_registry_snapshots(snapshot_date)"
            )
            conn.commit()

    def _load_from_db(self) -> None:
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT * FROM lse_registry").fetchall()
                for row in rows:
                    p = LSEProduct(
                        ticker=row["ticker"],
                        name=row["name"],
                        underlying=row["underlying"] or "",
                        sector=row["sector"] or "",
                        geography=row["geography"] or "",
                        leverage_factor=row["leverage_factor"] or 0.0,
                        bias=row["bias"] or "LONG",
                        structure=row["structure"] or "ETP",
                        avg_daily_volume=row["avg_daily_volume"] or 0.0,
                        last_price=row["last_price"] or 0.0,
                        price_change_pct=row["price_change_pct"] or 0.0,
                        is_active=bool(row["is_active"]),
                        last_updated=row["last_updated"] or "",
                        liquidity_tier=row["liquidity_tier"] or "UNKNOWN",
                    )
                    self._products[p.ticker] = p
        except Exception as exc:
            logger.warning("Failed to load registry from DB: %s", exc)

    def _seed_from_catalog(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for row in _SEED_CATALOG:
            ticker, name, underlying, sector, geo, leverage, bias, structure = row
            p = LSEProduct(
                ticker=ticker, name=name, underlying=underlying, sector=sector,
                geography=geo, leverage_factor=leverage, bias=bias,
                structure=structure, last_updated=now,
            )
            self._products[ticker] = p
        self._persist_all()
        logger.info("Seeded LSE registry with %d products", len(self._products))

    def _persist_all(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            for p in self._products.values():
                conn.execute("""
                    INSERT INTO lse_registry (
                        ticker, name, underlying, sector, geography,
                        leverage_factor, bias, structure, avg_daily_volume,
                        last_price, price_change_pct, is_active, last_updated,
                        liquidity_tier, first_seen, last_seen
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(ticker) DO UPDATE SET
                        name=excluded.name,
                        underlying=excluded.underlying,
                        sector=excluded.sector,
                        geography=excluded.geography,
                        leverage_factor=excluded.leverage_factor,
                        bias=excluded.bias,
                        structure=excluded.structure,
                        avg_daily_volume=excluded.avg_daily_volume,
                        last_price=excluded.last_price,
                        price_change_pct=excluded.price_change_pct,
                        is_active=excluded.is_active,
                        last_updated=excluded.last_updated,
                        liquidity_tier=excluded.liquidity_tier,
                        last_seen=excluded.last_seen
                """, (
                    p.ticker, p.name, p.underlying, p.sector, p.geography,
                    p.leverage_factor, p.bias, p.structure, p.avg_daily_volume,
                    p.last_price, p.price_change_pct, int(p.is_active),
                    p.last_updated, p.liquidity_tier, now, now,
                ))
            conn.commit()

    # ── I-04: Discovery — Web Scraping for New Listings ─────────────────────
    # Automated discovery of new LSE leveraged/inverse ETPs.
    # Runs daily at 06:00 UK (called from refresh with run_discovery=True).
    # Discovery pipeline: scrape candidates -> yfinance verify -> Amihud + ASER -> add
    #
    # Providers scraped (priority order):
    #   1. GraniteShares product page (single-stock leveraged ETPs)
    #   2. WisdomTree product page (index/commodity leveraged ETPs)
    #   3. yfinance ticker probe (brute-force from known ticker patterns)
    #
    # New listings must pass:
    #   - Amihud (2002) illiquidity ratio < 1e-6 (liquid enough to trade)
    #   - ASER > 10 (ADR-to-Spread Efficiency Ratio — enough daily range vs cost)
    # ──────────────────────────────────────────────────────────────────────────

    # Underlying stocks to probe for new GraniteShares-style single-stock ETPs
    _PROBE_UNDERLYINGS: list[str] = [
        "NVDA", "TSLA", "AMD", "AMZN", "MSFT", "META", "AAPL", "GOOG",
        "AVGO", "ARM", "PLTR", "COIN", "MSTR", "BAC", "GS", "XOM",
        "LLY", "MU", "TSM", "NFLX", "CRM", "UBER", "SQ", "SNOW",
        "SHOP", "ABNB", "DKNG", "RIVN", "LCID", "SMCI", "DELL",
        "MRVL", "INTC", "QCOM", "ASML", "LRCX", "AMAT", "KLAC",
    ]

    # Index/region codes to probe for WisdomTree-style ETPs
    _PROBE_INDEX_CODES: list[str] = [
        "US", "EU", "DE", "JP", "HK", "UK", "EN", "FI", "HC",
        "SM", "CH", "IN", "KR", "TW", "AU",
    ]

    # Amihud (2002) illiquidity ratio threshold
    _AMIHUD_MAX_ILLIQUIDITY: float = 1e-6

    def discover_new_listings(self) -> list[LSEProduct]:
        """Discover new LSE leveraged ETPs not yet in the registry.

        Pipeline:
          1. Generate candidate tickers from known naming patterns
          2. Filter out tickers already in registry
          3. Batch verify via yfinance (does the ticker exist & trade?)
          4. Apply Amihud illiquidity + ASER liquidity filters
          5. Classify and add passing products

        Returns list of newly discovered LSEProduct objects.
        """
        logger.info("LSE Registry discovery starting — searching for new leveraged ETPs")
        candidates = self._generate_discovery_candidates()
        existing = set(self._products.keys())
        new_candidates = [t for t in candidates if t not in existing]

        if not new_candidates:
            logger.info("Discovery: no new candidates to probe (all %d known)", len(candidates))
            return []

        logger.info("Discovery: probing %d new candidates (excluding %d known)",
                     len(new_candidates), len(existing))

        # Batch verify via yfinance — check which tickers actually exist
        verified = self._yfinance_verify_candidates(new_candidates)
        if not verified:
            logger.info("Discovery: no new tickers verified via yfinance")
            return []

        logger.info("Discovery: %d candidates verified via yfinance", len(verified))

        # Apply Amihud + ASER liquidity filters
        passed = []
        for ticker, info in verified.items():
            amihud_ok = self._check_amihud(info)
            if not amihud_ok:
                logger.debug("Discovery: %s rejected — Amihud FAIL", ticker)
                continue

            product = self._classify_discovered_product(ticker, info)
            if not product:
                continue

            # ASER check uses the registry's existing compute_aser method (I-02)
            aser_val = self.compute_aser(ticker)
            product.aser = aser_val
            aser_ok = aser_val >= self._ASER_CORE_THRESHOLD

            if aser_ok:
                passed.append(product)
                logger.info(
                    "NEW LISTING DISCOVERED: %s (%s) — Amihud=OK, ASER=%.1f, tier=%s",
                    ticker, product.name, aser_val, product.liquidity_tier,
                )
            else:
                logger.debug(
                    "Discovery: %s rejected — ASER=%.1f < %.1f threshold",
                    ticker, aser_val, self._ASER_CORE_THRESHOLD,
                )

        # Persist newly discovered products
        for product in passed:
            self._products[product.ticker] = product

        if passed:
            self._persist_all()
            logger.info(
                "LSE Registry discovery complete: %d new products added (%s)",
                len(passed), [p.ticker for p in passed],
            )
        else:
            logger.info("Discovery: 0 new products passed liquidity filters")

        return passed

    def _generate_discovery_candidates(self) -> list[str]:
        """Generate candidate tickers from known LSE leveraged ETP naming patterns."""
        candidates: set[str] = set()

        # GraniteShares-style: NVDA -> NVD3.L, NVDS.L etc.
        for stock in self._PROBE_UNDERLYINGS:
            for suffix in ["3.L", "S.L", "L.L", "2.L", "5.L"]:
                candidates.add(f"{stock}{suffix}")
                if len(stock) >= 4:
                    candidates.add(f"{stock[:3]}{suffix}")
                    candidates.add(f"{stock[:4]}{suffix}")

        # WisdomTree-style: 3LUS.L, 3SUS.L, 5LUS.L etc.
        for code in self._PROBE_INDEX_CODES:
            for prefix in ["3L", "3S", "5L", "5S", "2L", "2S"]:
                candidates.add(f"{prefix}{code}.L")

        # Also try scraping provider pages for additional candidates
        candidates.update(self._scrape_provider_pages())

        return sorted(candidates)

    def _scrape_provider_pages(self) -> set[str]:
        """Scrape GraniteShares and WisdomTree product pages for LSE tickers.

        Returns set of candidate tickers found.
        Falls back gracefully if requests unavailable or pages change format.
        """
        found: set[str] = set()
        if _requests is None:
            logger.debug("Discovery: requests library not available, skipping provider scrape")
            return found

        _PROVIDER_URLS = [
            "https://graniteshares.com/institutional/uk/en-uk/etps/",
            "https://graniteshares.com/institutional/uk/en-uk/products/",
            "https://www.wisdomtree.eu/en-gb/products",
        ]

        for url in _PROVIDER_URLS:
            try:
                resp = _requests.get(url, timeout=15, headers={
                    "User-Agent": "NZT48-Registry/1.0 (research; non-commercial)"
                })
                if resp.status_code != 200:
                    continue
                # Extract potential LSE tickers from page content
                tickers_raw = re.findall(
                    r'\b([A-Z0-9]{2,6}(?:\.L)?)\b', resp.text,
                )
                for t in tickers_raw:
                    candidate = f"{t}.L" if not t.endswith(".L") else t
                    if self._looks_like_leveraged_etp(candidate):
                        found.add(candidate)
                logger.debug("Provider scrape: found %d candidates from %s", len(found), url)
            except Exception as exc:
                logger.debug("Provider scrape failed for %s: %s", url, exc)

        return found

    @staticmethod
    def _looks_like_leveraged_etp(ticker: str) -> bool:
        """Heuristic: does this ticker match LSE leveraged ETP naming patterns?"""
        base = ticker.replace(".L", "")
        if len(base) < 3 or len(base) > 6:
            return False
        # Pattern 1: starts with digit (2,3,5) + L/S — WisdomTree style (3LUS, 3SDE)
        if re.match(r'^[235][LS][A-Z]{2,4}$', base):
            return True
        # Pattern 2: ends with digit (2,3,5) — leverage suffix (NVD3, QQQ5)
        if re.match(r'^[A-Z]{2,5}[235]$', base):
            return True
        # Pattern 3: ends with S and 3+ alpha chars — short ETP (NVDS, TSLS)
        if re.match(r'^[A-Z]{3,5}S$', base):
            return True
        # Pattern 4: ends with L and 3+ alpha chars — long ETP (AMZL, MSFL)
        if re.match(r'^[A-Z]{3,5}L$', base):
            return True
        return False

    def _yfinance_verify_candidates(
        self, candidates: list[str], batch_size: int = 50
    ) -> dict[str, dict]:
        """Batch-verify candidate tickers via yfinance.

        Returns dict of {ticker: info_dict} for tickers that exist and have data.
        info_dict contains: df (DataFrame), avg_vol, last_price, name.
        """
        verified: dict[str, dict] = {}

        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i + batch_size]
            try:
                raw = yf.download(
                    batch, period="1mo", interval="1d",
                    group_by="ticker", auto_adjust=True, progress=False, threads=True,
                )
                if raw is None or raw.empty:
                    continue

                for ticker in batch:
                    try:
                        if len(batch) == 1:
                            df = raw
                        else:
                            if ticker not in raw.columns.get_level_values(0):
                                continue
                            df = raw[ticker]

                        if df is None or df.empty or "Close" not in df.columns:
                            continue
                        df = df.dropna(subset=["Close"])
                        if len(df) < 5:
                            continue

                        last_price = float(df["Close"].iloc[-1])
                        if last_price <= 0:
                            continue

                        vol_series = df["Volume"] if "Volume" in df.columns else pd.Series([0])
                        avg_vol = float(vol_series.tail(20).mean()) if not vol_series.isna().all() else 0.0

                        # Try to get the product name from yfinance info
                        name = ""
                        try:
                            yf_ticker = yf.Ticker(ticker)
                            info = yf_ticker.info or {}
                            name = info.get("longName", info.get("shortName", ""))
                        except Exception:
                            pass

                        verified[ticker] = {
                            "df": df,
                            "avg_vol": avg_vol,
                            "last_price": last_price,
                            "name": name or f"Discovered ETP ({ticker})",
                        }
                    except Exception:
                        continue

            except Exception as exc:
                logger.debug("yfinance batch verify failed for batch %d: %s", i, exc)

        return verified

    def _check_amihud(self, info: dict) -> bool:
        """Amihud (2002) illiquidity ratio: |return| / dollar_volume.

        Lower = more liquid. Threshold: < 1e-6.
        Products above threshold are too illiquid for our leveraged ETP universe.
        """
        df = info.get("df")
        if df is None or df.empty or len(df) < 5:
            return False

        try:
            returns = df["Close"].pct_change().dropna().abs()
            volumes = df["Volume"] if "Volume" in df.columns else pd.Series([0] * len(df))
            dollar_vol = df["Close"] * volumes

            valid = dollar_vol > 0
            if valid.sum() < 3:
                return False

            amihud_values = returns[valid] / dollar_vol[valid]
            avg_amihud = float(amihud_values.mean())

            return avg_amihud < self._AMIHUD_MAX_ILLIQUIDITY
        except Exception as exc:
            logger.debug("Amihud calculation failed: %s", exc)
            return False

    def _classify_discovered_product(self, ticker: str, info: dict) -> Optional[LSEProduct]:
        """Classify a newly discovered ticker into an LSEProduct.

        Infers: underlying, sector, geography, leverage, bias, structure
        from the ticker pattern and yfinance metadata.
        """
        name = info.get("name", f"Discovered ETP ({ticker})")
        avg_vol = info.get("avg_vol", 0.0)
        last_price = info.get("last_price", 0.0)
        base = ticker.replace(".L", "")

        # Determine leverage factor and bias from ticker pattern
        leverage = 3.0
        bias = "LONG"

        # WisdomTree-style: 3LUS, 3SDE, 5LUS
        wt_match = re.match(r'^([235])([LS])([A-Z]{2,4})$', base)
        if wt_match:
            leverage = float(wt_match.group(1))
            bias = "LONG" if wt_match.group(2) == "L" else "SHORT"
            if bias == "SHORT":
                leverage = -leverage
        elif re.match(r'^[A-Z]+[235]$', base):
            leverage = float(base[-1])
            bias = "LONG"
        elif re.match(r'^[A-Z]+S$', base) and len(base) >= 4:
            leverage = -3.0
            bias = "SHORT"
        elif re.match(r'^[A-Z]+L$', base) and len(base) >= 4:
            leverage = 3.0
            bias = "LONG"

        underlying = self._infer_underlying(ticker, name)
        sector = self._infer_sector(underlying, name)
        geography = self._infer_geography(underlying, name)
        structure = "ETP"
        if any(kw in name.lower() for kw in ("commodity", "gold", "silver", "oil", "crude")):
            structure = "ETC"

        now = datetime.now(timezone.utc).isoformat()
        return LSEProduct(
            ticker=ticker,
            name=name,
            underlying=underlying,
            sector=sector,
            geography=geography,
            leverage_factor=leverage,
            bias=bias,
            structure=structure,
            avg_daily_volume=avg_vol,
            last_price=last_price,
            is_active=True,
            last_updated=now,
            liquidity_tier=self._classify_liquidity(avg_vol),
        )

    @staticmethod
    def _infer_underlying(ticker: str, name: str) -> str:
        """Best-effort underlying inference from ticker/name."""
        name_lower = name.lower()
        _KEYWORD_MAP = {
            "nasdaq": "QQQ/NDX", "s&p": "SPY/SPX", "s&p 500": "SPY/SPX",
            "nvidia": "NVDA", "tesla": "TSLA", "tsmc": "TSM", "micron": "MU",
            "amd": "AMD", "broadcom": "AVGO", "arm": "ARM", "apple": "AAPL",
            "amazon": "AMZN", "microsoft": "MSFT", "meta": "META",
            "alphabet": "GOOGL", "google": "GOOGL", "palantir": "PLTR",
            "coinbase": "COIN", "microstrategy": "MSTR",
            "semiconductor": "SOX/SMH", "gold": "Gold/GC",
            "silver": "Silver/SI", "oil": "CL/Oil", "crude": "CL/Oil",
            "dax": "DAX", "euro stoxx": "EuroStoxx50", "nikkei": "Nikkei225",
            "hang seng": "HSI", "energy": "XLE/Energy",
            "financial": "XLF/Finance", "healthcare": "XLV/Health",
            "bank of america": "BAC", "goldman": "GS", "exxon": "XOM",
            "eli lilly": "LLY",
        }
        for keyword, ul in _KEYWORD_MAP.items():
            if keyword in name_lower:
                return ul
        base = ticker.replace(".L", "")
        return base.rstrip("0123456789SL") or "UNKNOWN"

    @staticmethod
    def _infer_sector(underlying: str, name: str) -> str:
        """Infer sector from underlying or name."""
        name_lower = name.lower()
        _SECTOR_KEYWORDS = {
            "Semiconductors": ["semiconductor", "nvda", "amd", "tsm", "mu", "avgo", "arm", "sox", "smh"],
            "Technology": ["tech", "nasdaq", "ai", "gpt", "apple", "microsoft", "meta", "alphabet"],
            "Broad Market": ["s&p", "dow", "russell", "dax", "stoxx", "nikkei", "hang seng"],
            "Energy": ["energy", "oil", "crude", "exxon", "xle"],
            "Financials": ["financial", "bank", "goldman", "xlf"],
            "Healthcare": ["health", "pharma", "lilly", "xlv"],
            "Commodities": ["gold", "silver", "commodity"],
            "EV/Tech": ["tesla", "rivian", "lucid"],
            "Crypto/Finance": ["coinbase", "microstrategy", "crypto", "bitcoin"],
        }
        for sector, keywords in _SECTOR_KEYWORDS.items():
            if any(kw in name_lower or kw in underlying.lower() for kw in keywords):
                return sector
        return "Uncategorized"

    @staticmethod
    def _infer_geography(underlying: str, name: str) -> str:
        """Infer geographic exposure from underlying or name."""
        name_lower = name.lower()
        if any(kw in name_lower for kw in ("dax", "euro stoxx", "europe")):
            return "EU"
        if any(kw in name_lower for kw in ("nikkei", "japan")):
            return "JP"
        if any(kw in name_lower for kw in ("hang seng", "hong kong")):
            return "HK"
        if any(kw in name_lower for kw in ("gold", "silver", "oil", "crude", "commodity")):
            return "Global"
        return "US"

    # ── Refresh ───────────────────────────────────────────────────────────────

    def refresh(self, run_discovery: bool = False) -> dict:
        """Fetch live data for all known products. Returns summary dict.

        Args:
            run_discovery: If True, run new listing discovery before refreshing.
                          Set True for the daily 06:00 UK refresh.
        """
        logger.info("LSE Registry refresh starting (%d products)", len(self._products))

        # I-04: Run discovery for new listings (daily 06:00 UK)
        new_listings = 0
        if run_discovery:
            try:
                discovered = self.discover_new_listings()
                new_listings = len(discovered)
            except Exception as exc:
                logger.error("LSE Registry discovery failed (non-fatal): %s", exc)

        tickers = list(self._products.keys())
        updated = 0
        delisted = 0

        # Batch download via yfinance
        try:
            raw = yf.download(
                tickers, period="1mo", interval="1d",
                group_by="ticker", auto_adjust=True, progress=False, threads=True,
            )
        except Exception as exc:
            logger.error("yfinance batch download failed: %s", exc)
            return {"updated": 0, "delisted": 0, "new": 0, "error": str(exc)}

        now = datetime.now(timezone.utc).isoformat()
        today = date.today().isoformat()

        for ticker, product in list(self._products.items()):
            try:
                if len(tickers) == 1:
                    df = raw
                else:
                    df = raw[ticker] if ticker in raw.columns.get_level_values(0) else pd.DataFrame()

                if df is None or df.empty or "Close" not in df.columns:
                    if product.is_active:
                        product.is_active = False
                        delisted += 1
                        logger.info("Possible delisting detected: %s", ticker)
                    continue

                df = df.dropna(subset=["Close"])
                if df.empty:
                    continue

                last_price = float(df["Close"].iloc[-1])
                prev_price = float(df["Close"].iloc[-2]) if len(df) >= 2 else last_price
                change_pct = safe_divide(last_price - prev_price, prev_price, fallback=0.0, context=f"{ticker} change_pct") * 100

                # Average daily volume (20-day)
                vol_col = "Volume" if "Volume" in df.columns else None
                if vol_col and not df[vol_col].isna().all():
                    avg_vol = float(df[vol_col].tail(20).mean())
                else:
                    avg_vol = product.avg_daily_volume

                product.last_price = last_price
                product.price_change_pct = change_pct
                product.avg_daily_volume = avg_vol
                product.is_active = True
                product.last_updated = now
                product.liquidity_tier = self._classify_liquidity(avg_vol)
                updated += 1

            except Exception as exc:
                logger.debug("Error refreshing %s: %s", ticker, exc)

        self._persist_all()
        self._snapshot_today(today)
        self._last_refresh = datetime.now(timezone.utc)

        result = {"updated": updated, "delisted": delisted, "new": new_listings, "total": len(self._products)}
        logger.info("LSE Registry refresh complete: %s", result)
        return result

    def _classify_liquidity(self, avg_daily_vol: float) -> str:
        if avg_daily_vol >= self._LIQUIDITY_THRESHOLDS["HIGH"]:
            return "HIGH"
        elif avg_daily_vol >= self._LIQUIDITY_THRESHOLDS["MEDIUM"]:
            return "MEDIUM"
        elif avg_daily_vol >= self._LIQUIDITY_THRESHOLDS["LOW"]:
            return "LOW"
        return "ILLIQUID"

    def _snapshot_today(self, today: str) -> None:
        """Write daily snapshot row for each active product."""
        with sqlite3.connect(self._db_path) as conn:
            for p in self._products.values():
                conn.execute("""
                    INSERT INTO lse_registry_snapshots
                        (snapshot_date, ticker, last_price, price_change_pct,
                         avg_daily_volume, is_active, liquidity_tier)
                    VALUES (?,?,?,?,?,?,?)
                """, (today, p.ticker, p.last_price, p.price_change_pct,
                      p.avg_daily_volume, int(p.is_active), p.liquidity_tier))
            conn.commit()

    # ── I-02: ASER (ADR-to-Spread Efficiency Ratio) ──────────────────────────
    #
    # ASER = ADR% / Spread%
    #   ADR%    = Average Daily Range / Close x 100
    #   Spread% = median_spread_bps / 100
    #
    # Require ASER > 10 for CORE tier inclusion.
    # ETPs below threshold relegated to intelligence-only.
    # ---------------------------------------------------------------------------

    _ASER_CORE_THRESHOLD: float = 10.0

    def compute_aser(self, ticker: str, period: str = "1mo") -> float:
        """Compute ASER (ADR-to-Spread Efficiency Ratio) for *ticker*.

        ASER = (ADR% / Spread%) where:
            ADR% = mean(high - low) / mean(close) x 100
            Spread% = median_spread_bps / 100

        Higher ASER = the daily range comfortably exceeds trading costs.
        An ASER of 10 means the average daily range is 10x the spread.

        Parameters
        ----------
        ticker : str
            Yahoo Finance ticker symbol (e.g. "QQQ3.L").
        period : str
            yfinance period for historical data (default "1mo").

        Returns
        -------
        float
            ASER value. Returns 0.0 if data is insufficient or spread is zero.
        """
        try:
            data = yf.download(
                ticker, period=period, interval="1d",
                auto_adjust=True, progress=False,
            )
        except Exception as exc:
            logger.warning("ASER: yfinance download failed for %s: %s", ticker, exc)
            return 0.0

        if data is None or data.empty:
            logger.warning("ASER: no data for %s", ticker)
            return 0.0

        # Flatten MultiIndex if present (yfinance quirk)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        required_cols = {"High", "Low", "Close"}
        if not required_cols.issubset(data.columns):
            logger.warning("ASER: missing columns for %s (have: %s)", ticker, list(data.columns))
            return 0.0

        df = data.dropna(subset=["High", "Low", "Close"])
        if len(df) < 5:
            logger.warning("ASER: insufficient data for %s (%d rows)", ticker, len(df))
            return 0.0

        high = df["High"].values.flatten()
        low = df["Low"].values.flatten()
        close = df["Close"].values.flatten()

        # ADR% = mean daily range as percentage of close
        daily_range = high - low
        mean_close = close.mean()
        if mean_close <= 0:
            return 0.0
        adr_pct = (daily_range.mean() / mean_close) * 100.0

        # Spread%: use static spread_bps from isa_universe.py SLIPPAGE_MODEL
        try:
            from uk_isa.isa_universe import SLIPPAGE_MODEL
            spread_bps_map = SLIPPAGE_MODEL.get("spread_bps", {})
            median_spread_bps = spread_bps_map.get(
                ticker, SLIPPAGE_MODEL.get("default_bps", 10)
            )
        except ImportError:
            median_spread_bps = 10
            logger.warning("ASER: could not import SLIPPAGE_MODEL, using default %d bps", median_spread_bps)

        spread_pct = median_spread_bps / 100.0

        if spread_pct <= 0:
            logger.warning("ASER: zero spread for %s, cannot compute", ticker)
            return 0.0

        aser = safe_divide(adr_pct, spread_pct, fallback=0.0, context=f"ASER {ticker}")

        logger.debug(
            "ASER: %s — ADR%%=%.3f, spread_bps=%d, spread%%=%.4f, ASER=%.2f",
            ticker, adr_pct, median_spread_bps, spread_pct, aser,
        )
        return aser

    def check_aser_core_eligible(self, ticker: str) -> bool:
        """Return True if *ticker* meets the ASER threshold for CORE tier.

        CORE tier requires ASER > 10. ETPs below threshold are relegated
        to intelligence-only (SECTOR_RADAR).

        Parameters
        ----------
        ticker : str
            Yahoo Finance ticker symbol.

        Returns
        -------
        bool
            True if ASER > _ASER_CORE_THRESHOLD (currently 10.0).
        """
        product = self._products.get(ticker)
        if product is None:
            logger.warning("ASER: ticker %s not in registry", ticker)
            return False

        aser = product.aser if product.aser > 0 else self.compute_aser(ticker)

        if aser > 0 and product.aser != aser:
            product.aser = aser

        is_eligible = aser > self._ASER_CORE_THRESHOLD

        if not is_eligible:
            logger.info(
                "ASER_GATE FAIL: %s — ASER=%.2f < threshold=%.1f, relegating to intel-only",
                ticker, aser, self._ASER_CORE_THRESHOLD,
            )
        else:
            logger.debug(
                "ASER_GATE PASS: %s — ASER=%.2f >= threshold=%.1f",
                ticker, aser, self._ASER_CORE_THRESHOLD,
            )
        return is_eligible

    def get_core_eligible(self) -> list[LSEProduct]:
        """Return active products that meet ASER threshold for CORE tier."""
        return [
            p for p in self._products.values()
            if p.is_active and p.aser > self._ASER_CORE_THRESHOLD
        ]

    # ── Query methods ─────────────────────────────────────────────────────────

    def get_all_active(self) -> list[LSEProduct]:
        return [p for p in self._products.values() if p.is_active]

    def get_by_bias(self, bias: str) -> list[LSEProduct]:
        return [p for p in self._products.values() if p.is_active and p.bias == bias.upper()]

    def get_by_sector(self, sector: str) -> list[LSEProduct]:
        return [p for p in self._products.values() if p.is_active and sector.lower() in p.sector.lower()]

    def get_by_leverage(self, factor: float) -> list[LSEProduct]:
        return [p for p in self._products.values() if p.is_active and p.leverage_factor == factor]

    def get_by_geography(self, geo: str) -> list[LSEProduct]:
        return [p for p in self._products.values() if p.is_active and geo.upper() in p.geography.upper()]

    def get_high_liquidity(self) -> list[LSEProduct]:
        return [p for p in self._products.values() if p.is_active and p.liquidity_tier == "HIGH"]

    def get_by_underlying(self, underlying: str) -> list[LSEProduct]:
        return [p for p in self._products.values() if p.is_active and underlying.upper() in p.underlying.upper()]

    def get_product(self, ticker: str) -> Optional[LSEProduct]:
        return self._products.get(ticker)

    def add_product(self, product: LSEProduct) -> None:
        """Manually add or update a product in the registry."""
        self._products[product.ticker] = product
        self._persist_all()

    def get_tickers(self, bias: Optional[str] = None, min_liquidity: Optional[str] = None) -> list[str]:
        """Return list of tickers matching optional filters."""
        products = self.get_all_active()
        if bias:
            products = [p for p in products if p.bias == bias.upper()]
        if min_liquidity:
            tiers = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "ILLIQUID": 0}
            min_tier = tiers.get(min_liquidity.upper(), 0)
            products = [p for p in products if tiers.get(p.liquidity_tier, 0) >= min_tier]
        return [p.ticker for p in products]

    def summary(self) -> dict:
        active = self.get_all_active()
        longs = [p for p in active if p.bias == "LONG"]
        shorts = [p for p in active if p.bias == "SHORT"]
        return {
            "total_active": len(active),
            "long_products": len(longs),
            "short_products": len(shorts),
            "high_liquidity": len([p for p in active if p.liquidity_tier == "HIGH"]),
            "sectors": list({p.sector for p in active}),
            "geographies": list({p.geography for p in active}),
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
        }


# Module-level singleton for shared access
_registry_instance: Optional[LSERegistry] = None


def get_registry(db_path: str = "data/nzt48.db") -> LSERegistry:
    """Return the module-level singleton LSERegistry."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = LSERegistry(db_path=db_path)
    return _registry_instance
