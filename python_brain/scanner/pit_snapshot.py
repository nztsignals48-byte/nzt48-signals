"""Point-in-time universe snapshot — daily at 23:00 UTC.

Writes data/universe_snapshots/YYYY-MM-DD.json — frozen universe + metadata.
Future backtests load only snapshots <= backtest date (no survivorship).

Runs once per day via supervisor cron-like scheduling (own loop).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path


log = logging.getLogger("pit-snap")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path("/Users/rr/aegis-v5")
SNAPSHOT_DIR = ROOT / "data/universe_snapshots"
CONTRACTS = ROOT / "config/contracts.toml"


def parse_contracts() -> list[dict]:
    if not CONTRACTS.exists():
        return []
    text = CONTRACTS.read_text()
    blocks = re.split(r"\[\[contracts\]\]", text)[1:]
    out = []
    for b in blocks:
        d = {}
        for line in b.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'(\w+)\s*=\s*"?([^"#]+?)"?\s*(#.*)?$', line)
            if not m:
                continue
            key, val = m.group(1), m.group(2).strip()
            if key == "con_id":
                try:
                    val = int(val)
                except ValueError:
                    continue
            elif val in ("true", "false"):
                val = (val == "true")
            d[key] = val
        if "symbol" in d and "exchange" in d:
            out.append(d)
    return out


def write_snapshot() -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    path = SNAPSHOT_DIR / f"{date_str}.json"

    contracts = parse_contracts()
    by_exchange = {}
    for c in contracts:
        by_exchange.setdefault(c["exchange"], []).append(c["symbol"])

    snap = {
        "snapshot_date": date_str,
        "timestamp_utc": now.isoformat(),
        "total_contracts": len(contracts),
        "exchanges": {e: sorted(set(s)) for e, s in by_exchange.items()},
        "contracts": contracts,
    }
    path.write_text(json.dumps(snap, indent=2))
    log.info("wrote snapshot %s (%d contracts)", path, len(contracts))
    return path


def load_snapshot(date_str: str) -> dict | None:
    path = SNAPSHOT_DIR / f"{date_str}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def universe_at_date(date_str: str) -> list[str]:
    """Return list of (symbol, exchange) pairs valid at given date."""
    snap = load_snapshot(date_str)
    if snap is None:
        # Find closest earlier snapshot
        files = sorted(SNAPSHOT_DIR.glob("*.json"))
        before = [f for f in files if f.stem < date_str]
        if not before:
            return []
        snap = json.loads(before[-1].read_text())
    return [(c["symbol"], c["exchange"]) for c in snap.get("contracts", [])]


async def run():
    # Write one snapshot immediately, then every 86400 seconds (daily)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    write_snapshot()
    while True:
        # Sleep until next 23:00 UTC
        now = datetime.now(timezone.utc)
        target = now.replace(hour=23, minute=0, second=0, microsecond=0)
        if target <= now:
            from datetime import timedelta
            target = target + timedelta(days=1)
        seconds_until = (target - now).total_seconds()
        log.info("next snapshot in %.0f seconds", seconds_until)
        await asyncio.sleep(max(60, seconds_until))
        try:
            write_snapshot()
        except Exception as e:
            log.error("snapshot failed: %s", e)


if __name__ == "__main__":
    if os.environ.get("SNAPSHOT_NOW") == "1":
        write_snapshot()
    else:
        asyncio.run(run())
