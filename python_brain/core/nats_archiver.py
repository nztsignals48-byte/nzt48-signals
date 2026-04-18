"""nats_archiver — persist every significant NATS subject to disk.

One JSONL file per subject per day. Used by Ouroboros and manual audit.

Subjects:
    news.raw, news.alpha, orders.submit, orders.filled, orders.reject,
    scanner.hits.*, universe.rotation, portfolio.equity, portfolio.fill,
    signals.core, fills.closed, ibkr.status
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

ARCHIVE_DIR = Path("/Users/rr/aegis-v5/data/archive")

SUBJECTS = [
    "news.raw", "news.alpha",
    "orders.submit", "orders.filled", "orders.reject",
    "scanner.hits.*",
    "universe.rotation",
    "portfolio.equity", "portfolio.fill",
    "signals.core", "fills.closed",
    "ibkr.status",
]


async def main() -> None:
    import nats  # type: ignore
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    nc = await nats.connect(os.environ.get("NATS_URL", "nats://127.0.0.1:4222"),
                            name="aegis-v5-archiver")
    log.info("archiver connected; persisting to %s", ARCHIVE_DIR)

    counts: dict = {}
    files: dict = {}

    def path_for(subject: str) -> Path:
        day = datetime.now(timezone.utc).date().isoformat()
        safe = subject.replace("/", "_").replace(".", "_")
        return ARCHIVE_DIR / f"{safe}_{day}.jsonl"

    async def handler_for(subject_template: str):
        async def _h(msg):
            p = path_for(msg.subject)
            if p not in files:
                files[p] = p.open("a")
            wrap = {
                "subject": msg.subject,
                "ts_utc": datetime.now(timezone.utc).isoformat(),
            }
            try:
                wrap["payload"] = json.loads(msg.data)
            except Exception:
                wrap["payload_raw"] = msg.data.decode("utf-8", "replace")
            files[p].write(json.dumps(wrap) + "\n")
            files[p].flush()
            counts[msg.subject] = counts.get(msg.subject, 0) + 1
        return _h

    for s in SUBJECTS:
        await nc.subscribe(s, cb=await handler_for(s))

    log.info("listening on %d subjects", len(SUBJECTS))
    while True:
        await asyncio.sleep(60)
        top = sorted(counts.items(), key=lambda x: -x[1])[:6]
        log.info("archive: " + " ".join(f"{k}={v}" for k, v in top))


if __name__ == "__main__":
    asyncio.run(main())
