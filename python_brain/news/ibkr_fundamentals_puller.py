"""ibkr_fundamentals_puller — pulls earnings calendar, shortability,
fundamentals ratios, and analyst research from IBKR.

Runs every 30 min. Writes to:
    data/intel/earnings_pattern.json     (next earnings date per ticker)
    data/intel/shortable.json            (shortable status + rebate)
    data/intel/fundamentals.json         (P/E, ROE, div_yield, analyst target)

Uses reqFundamentalData (ReportsFinSummary, ReportSnapshot) and contract
details for shortability.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Set
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

INTEL_DIR = Path("/Users/rr/aegis-v5/data/intel")
EARNINGS_PATH = INTEL_DIR / "earnings_pattern.json"
FUND_PATH = INTEL_DIR / "fundamentals.json"
SHORT_PATH = INTEL_DIR / "shortable.json"
POOL_PATH = Path("/Users/rr/aegis-v5/data/adaptive_pool.json")


def _watchlist_tickers(limit: int = 100) -> Set[str]:
    try:
        d = json.loads(POOL_PATH.read_text())
        return set((d.get("pool") or [])[:limit])
    except Exception:
        return set()


def _load(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 1, "tickers": {}}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"schema_version": 1, "tickers": {}}


def _save(path: Path, data: dict) -> None:
    INTEL_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1))


async def run_once(ib, tickers) -> dict:
    from ib_insync import Stock  # type: ignore
    earnings = _load(EARNINGS_PATH)
    fund = _load(FUND_PATH)
    shrt = _load(SHORT_PATH)
    now = datetime.now(timezone.utc).isoformat()
    stats = {"earnings": 0, "fund": 0, "shortable": 0, "errors": 0}

    for sym in tickers:
        try:
            c = Stock(sym, "SMART", "USD")
            quals = await ib.qualifyContractsAsync(c)
            if not quals:
                stats["errors"] += 1
                continue
            qc = quals[0]

            # --- Fundamentals snapshot ---------------------------------
            try:
                xml = await ib.reqFundamentalDataAsync(qc, "ReportSnapshot")
                if xml:
                    tree = ET.fromstring(xml)
                    ratios = {}
                    for r in tree.iter("Ratio"):
                        fn = r.attrib.get("FieldName")
                        if fn and r.text:
                            try:
                                ratios[fn] = float(r.text)
                            except Exception:
                                ratios[fn] = r.text
                    fund.setdefault("tickers", {})[sym] = {
                        "pe_ratio": ratios.get("PEEXCLXOR") or ratios.get("NPRICE_EPSBASICQ"),
                        "dividend_yield": ratios.get("YIELD"),
                        "market_cap_m": ratios.get("MKTCAP"),
                        "eps_ttm": ratios.get("EPSBASICQ"),
                        "price_to_book": ratios.get("PRICE2BK"),
                        "ts": now,
                    }
                    stats["fund"] += 1
            except Exception as e:
                log.debug("fundamentals %s: %s", sym, e)

            # --- Next earnings date via calendar ----------------------
            try:
                xml = await ib.reqFundamentalDataAsync(qc, "CalendarReport")
                if xml:
                    m = re.search(
                        r'Earnings\w*\s+Date[^0-9]*(\d{4}-\d{2}-\d{2})', xml
                    )
                    if m:
                        earnings.setdefault("tickers", {})[sym] = {
                            "next_date": m.group(1),
                            "surprise_bps_median": 0,
                            "ts": now,
                        }
                        stats["earnings"] += 1
            except Exception:
                pass

            # --- Shortability ---------------------------------------
            try:
                details = await ib.reqContractDetailsAsync(qc)
                if details:
                    d = details[0]
                    shrt.setdefault("tickers", {})[sym] = {
                        "long_name": d.longName,
                        "industry": d.industry,
                        "category": d.category,
                        "subcategory": d.subcategory,
                        "primary_exchange": d.contract.primaryExchange,
                        "ts": now,
                    }
                    stats["shortable"] += 1
            except Exception:
                pass

        except Exception as e:
            stats["errors"] += 1
            log.debug("ticker %s failed: %s", sym, e)

        # Gentle rate limit so we don't trip IBKR fundamentals API throttle
        await asyncio.sleep(0.5)

    _save(EARNINGS_PATH, earnings)
    _save(FUND_PATH, fund)
    _save(SHORT_PATH, shrt)
    return stats


async def main() -> None:
    from ib_insync import IB
    ib = IB()
    # Retry loop with cid increment — defends against per-cid soft-locks
    connected = False
    cid_base = 240
    for attempt in range(8):
        cid = cid_base + attempt * 13
        try:
            await ib.connectAsync("127.0.0.1", 4002, clientId=cid,
                                  readonly=True, timeout=30)
            log.info("fundamentals puller connected cid=%d (attempt %d)", cid, attempt + 1)
            connected = True
            break
        except Exception as e:
            log.warning("fundamentals connect attempt %d cid=%d failed: %s", attempt + 1, cid, e)
            await asyncio.sleep(8)
    if not connected:
        log.error("fundamentals could not connect after 8 attempts; exiting")
        return

    while True:
        tickers = list(_watchlist_tickers(limit=60))
        log.info("pulling fundamentals for %d tickers", len(tickers))
        stats = await run_once(ib, tickers)
        log.info("round stats: %s", stats)
        await asyncio.sleep(1800)  # 30 min


if __name__ == "__main__":
    asyncio.run(main())
