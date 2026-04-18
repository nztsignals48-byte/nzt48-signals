"""Adaptive intel seeder — keeps 2500-ticker pool with pre-filled intel rows.

Every strategy that checks `intel[ticker]` needs an entry per candidate
ticker. Without this, strategies return None on rotator-discovered tickers
(thinly-traded small caps, etc.) and the engine only ever trades mega-caps.

Pool sources:
  - contracts.toml (static seeds)
  - watchlist.v5.json (rotator's current live 100)
  - archive/scanner_hits_*.jsonl (all hits from the past 24h, scored)

Pool size: top 2500 by composite score (most-recent-hit-weighted).

Seeded intel files:
  data/intel/sentiment_long_short.json
  data/intel/filing_change_detect.json
  data/intel/earnings_pattern.json
  data/intel/overnight_return.json
  data/intel/index_recon.json
  data/intel/ibs_mean_reversion.json

Entries are null-valued — they unblock strategy .evaluate() returning
None on KeyError, but strategies still compute their price signals.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

ROOT = Path("/Users/rr/aegis-v5")
CONTRACTS_PATH = ROOT / "config" / "contracts.toml"
WATCHLIST_PATH = ROOT / "data" / "watchlist.v5.json"
ARCHIVE_DIR = ROOT / "data" / "archive"
INTEL_DIR = ROOT / "data" / "intel"
POOL_PATH = ROOT / "data" / "adaptive_pool.json"

POOL_SIZE = 2500
REBUILD_INTERVAL_S = 300  # 5 min


INTEL_TEMPLATES = {
    "sentiment_long_short.json": {
        "schema_version": 1,
        "tickers": {},  # ticker -> {score, direction, ts}
    },
    "filing_change_detect.json": {
        "schema_version": 1,
        "tickers": {},  # ticker -> {last_8k, diff_pct, ts}
    },
    "earnings_pattern.json": {
        "schema_version": 1,
        "tickers": {},  # ticker -> {next_date, surprise_bps_median, ts}
    },
    "overnight_return.json": {
        "schema_version": 1,
        "tickers": {},  # ticker -> {mean_bps, sd_bps, ts}
    },
    "index_recon.json": {
        "schema_version": 1,
        "tickers": {},  # ticker -> {index, direction, effective_date, ts}
    },
    "ibs_mean_reversion.json": {
        "schema_version": 1,
        "tickers": {},  # ticker -> {ibs_threshold, ts}
    },
}


def _parse_contracts() -> Set[str]:
    if not CONTRACTS_PATH.exists():
        return set()
    text = CONTRACTS_PATH.read_text()
    return set(re.findall(r'symbol\s*=\s*"([^"]+)"', text))


def _read_watchlist() -> List[str]:
    if not WATCHLIST_PATH.exists():
        return []
    try:
        return [x["ticker"] for x in json.loads(WATCHLIST_PATH.read_text())
                if "ticker" in x]
    except Exception:
        return []


def _read_scanner_hits() -> Counter:
    """Aggregate scanner.hits.* from the last 7 days archive files, weighted with decay."""
    from datetime import timedelta
    scores: Counter = Counter()
    today = datetime.now(timezone.utc).date()
    for days_back in range(7):
        day = (today - timedelta(days=days_back)).isoformat()
        decay = 0.9 ** days_back
        for p in ARCHIVE_DIR.glob(f"scanner_hits*_{day}.jsonl"):
            try:
                for line in p.read_text().splitlines():
                    try:
                        rec = json.loads(line)
                        payload = rec.get("payload") or rec
                        t = payload.get("ticker")
                        rank = int(payload.get("rank", 50))
                        if t:
                            scores[t] += decay * max(0.0, 1.0 - (rank - 1) / 50.0)
                    except Exception:
                        continue
            except Exception:
                continue
    return scores


def build_pool() -> List[str]:
    static = _parse_contracts()
    watchlist = _read_watchlist()
    scanner_scores = _read_scanner_hits()

    # Composite: static=1.0 base, watchlist=+0.5, scanner=weighted
    pool_scores: Dict[str, float] = defaultdict(float)
    for t in static:
        pool_scores[t] += 1.0
    for t in watchlist:
        pool_scores[t] += 0.5
    for t, s in scanner_scores.items():
        pool_scores[t] += s

    ranked = sorted(pool_scores.items(), key=lambda x: -x[1])
    top = [t for t, _ in ranked[:POOL_SIZE]]
    return top


def _seed_intel(pool: List[str]) -> None:
    INTEL_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    for fname, template in INTEL_TEMPLATES.items():
        path = INTEL_DIR / fname
        existing: dict = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except Exception:
                existing = {}
        existing.setdefault("schema_version", 1)
        existing.setdefault("tickers", {})
        for t in pool:
            # Only seed if missing (preserve real values that were actually computed)
            if t not in existing["tickers"]:
                if fname == "sentiment_long_short.json":
                    existing["tickers"][t] = {
                        "score": 0.0, "direction": "neutral",
                        "confidence": 0.5, "ts": now,
                    }
                elif fname == "filing_change_detect.json":
                    existing["tickers"][t] = {
                        "last_8k": None, "diff_pct": 0.0, "ts": now,
                    }
                elif fname == "earnings_pattern.json":
                    existing["tickers"][t] = {
                        "next_date": None, "surprise_bps_median": 0,
                        "ts": now,
                    }
                elif fname == "overnight_return.json":
                    existing["tickers"][t] = {
                        "mean_bps": 0.0, "sd_bps": 20.0, "ts": now,
                    }
                elif fname == "index_recon.json":
                    existing["tickers"][t] = {
                        "index": None, "direction": None,
                        "effective_date": None, "ts": now,
                    }
                elif fname == "ibs_mean_reversion.json":
                    existing["tickers"][t] = {
                        "ibs_threshold": 0.25, "ts": now,
                    }
        path.write_text(json.dumps(existing, indent=1))


async def main() -> None:
    while True:
        pool = build_pool()
        _seed_intel(pool)
        POOL_PATH.parent.mkdir(parents=True, exist_ok=True)
        POOL_PATH.write_text(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "size": len(pool),
            "pool": pool,
        }, indent=1))
        log.info("adaptive pool rebuilt: %d tickers  top10=%s",
                 len(pool), pool[:10])
        await asyncio.sleep(REBUILD_INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(main())
