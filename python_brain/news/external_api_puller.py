"""external_api_puller — free external feeds into V5 intel.

Sources:
    SEC EDGAR RSS (no auth, free)  — real-time 8-K/10-Q/S-1 filings
    FRED                            — macro series (UNRATE, VIXCLS, DGS10, CPIAUCSL)

Writes:
    data/intel/filing_change_detect.json  — latest filing per ticker
    data/intel/macro.json                 — current macro indicators
    publishes news.raw with provider=SEC for each new filing

Runs every 5 min. Pure stdlib + urllib.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Set

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

INTEL_DIR = Path("/Users/rr/aegis-v5/data/intel")
FILING_PATH = INTEL_DIR / "filing_change_detect.json"
MACRO_PATH = INTEL_DIR / "macro.json"
POOL_PATH = Path("/Users/rr/aegis-v5/data/adaptive_pool.json")

SEC_RSS = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=8-K"
           "&dateb=&owner=include&count=40&output=atom")
SEC_UA = "AEGIS-V5/1.0 (aegisv5@example.com)"

FRED_KEY = os.environ.get("FRED_API_KEY", "")
FRED_SERIES = ["UNRATE", "VIXCLS", "DGS10", "CPIAUCSL", "FEDFUNDS", "T10Y2Y"]


def _watchlist() -> Set[str]:
    try:
        d = json.loads(POOL_PATH.read_text())
        return set(d.get("pool") or [])
    except Exception:
        return set()


def _load(p: Path) -> dict:
    if not p.exists():
        return {"schema_version": 1, "tickers": {}}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {"schema_version": 1, "tickers": {}}


def _save(p: Path, d: dict) -> None:
    INTEL_DIR.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=1))


def _http_get(url: str, ua: str | None = None) -> str:
    req = urllib.request.Request(url)
    if ua:
        req.add_header("User-Agent", ua)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8", errors="replace")


async def pull_sec_filings(nc, tickers: Set[str]) -> int:
    """Fetch SEC EDGAR atom feed, parse 8-K events, write to filing intel."""
    try:
        xml = await asyncio.to_thread(_http_get, SEC_RSS, SEC_UA)
    except Exception as e:
        log.warning("SEC fetch failed: %s", e)
        return 0
    data = _load(FILING_PATH)
    data.setdefault("tickers", {})
    count = 0
    # Very light parsing: pull <entry><title>...</title><updated>...</updated>
    entries = re.findall(
        r"<entry>.*?<title>(.*?)</title>.*?<updated>(.*?)</updated>.*?</entry>",
        xml, re.DOTALL,
    )
    for title, updated in entries:
        title = re.sub(r"<[^>]+>", "", title).strip()
        # Typical title: "8-K - COMPANY NAME INC (0001234567) (Filer)"
        m = re.search(r"(\d-[KQ](?:/A)?)\s*-\s*([A-Z0-9 &\.]+?)\s*\(", title)
        if not m:
            continue
        form = m.group(1)
        company = m.group(2).strip()
        # Try to match to a known ticker (very heuristic)
        tkr_hit = None
        up = company.upper()
        for t in tickers:
            # Matches exact symbol or first 4 chars of company
            if t in up.split() or up.startswith(t[:3]):
                tkr_hit = t
                break
        if not tkr_hit:
            continue
        data["tickers"][tkr_hit] = {
            "last_8k": form,
            "diff_pct": 0.0,
            "title": title[:140],
            "ts": updated,
        }
        count += 1
        # Also broadcast as news.raw so LLM analyzes it
        payload = {
            "provider": "SEC",
            "article_id": f"sec-{company}-{updated}",
            "headline": f"{form}: {company}",
            "summary": "",
            "ticker": tkr_hit,
            "ts": updated,
        }
        try:
            await nc.publish("news.raw", json.dumps(payload).encode("utf-8"))
        except Exception:
            pass
    _save(FILING_PATH, data)
    return count


async def pull_fred() -> int:
    if not FRED_KEY:
        return 0
    macro = {}
    if MACRO_PATH.exists():
        try:
            macro = json.loads(MACRO_PATH.read_text())
        except Exception:
            macro = {}
    count = 0
    for series in FRED_SERIES:
        try:
            url = (f"https://api.stlouisfed.org/fred/series/observations"
                   f"?series_id={series}&api_key={FRED_KEY}"
                   f"&file_type=json&sort_order=desc&limit=1")
            txt = await asyncio.to_thread(_http_get, url)
            j = json.loads(txt)
            obs = (j.get("observations") or [])
            if obs:
                v = obs[0].get("value")
                if v and v != ".":
                    try:
                        macro[series] = {"value": float(v), "date": obs[0].get("date"),
                                         "ts": datetime.now(timezone.utc).isoformat()}
                        count += 1
                    except Exception:
                        pass
        except Exception as e:
            log.debug("FRED %s failed: %s", series, e)
        await asyncio.sleep(0.1)
    INTEL_DIR.mkdir(parents=True, exist_ok=True)
    MACRO_PATH.write_text(json.dumps(macro, indent=1))
    return count


async def main() -> None:
    import nats  # type: ignore
    nc = await nats.connect(os.environ.get("NATS_URL", "nats://127.0.0.1:4222"),
                            name="aegis-v5-external-apis")
    log.info("external API puller connected")

    while True:
        tickers = _watchlist() or set()
        sec_n = await pull_sec_filings(nc, tickers)
        fred_n = await pull_fred()
        log.info("pulled %d SEC filings + %d FRED series", sec_n, fred_n)
        await asyncio.sleep(300)


if __name__ == "__main__":
    asyncio.run(main())
