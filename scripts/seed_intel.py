#!/usr/bin/env python3
"""Seed intel files with synthetic-but-non-empty data so strategies aren't starved
during tests and Phase 2A paper restart.

Production: replaced by real agents (Phase 7). Until then, this script produces
a deterministic dataset good enough to prove the pipeline end-to-end.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("AEGIS_V5_DATA", "/Users/rr/aegis-v5/data"))
INTEL_DIR = DATA_DIR / "intel"
INTEL_DIR.mkdir(parents=True, exist_ok=True)

TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ"]


def seed() -> None:
    now = time.time()

    (INTEL_DIR / "news_reactor.json").write_text(json.dumps({
        "events": [
            {"ticker": "AAPL", "score":  0.6, "headline": "AAPL upgraded on services growth"},
            {"ticker": "MSFT", "score":  0.5, "headline": "MSFT cloud growth beats"},
            {"ticker": "TSLA", "score": -0.4, "headline": "TSLA production concerns"},
            {"ticker": "NVDA", "score":  0.7, "headline": "NVDA datacenter backlog"},
        ],
        "generated_at": now,
    }))

    (INTEL_DIR / "earnings_whisper.json").write_text(json.dumps({
        "whispers": {
            "AAPL": {"expected_surprise_bps":  80, "analyst_count": 30},
            "MSFT": {"expected_surprise_bps":  60, "analyst_count": 28},
            "NVDA": {"expected_surprise_bps": 120, "analyst_count": 35},
            "TSLA": {"expected_surprise_bps": -70, "analyst_count": 25},
        },
        "generated_at": now,
    }))

    (INTEL_DIR / "sec_scanner.json").write_text(json.dumps({
        "filings": [
            {"ticker": "AAPL", "change_score": 0.35, "form": "10-Q"},
            {"ticker": "MSFT", "change_score": 0.28, "form": "10-K"},
            {"ticker": "NVDA", "change_score": 0.42, "form": "10-Q"},
        ],
        "generated_at": now,
    }))

    (INTEL_DIR / "regime_council.json").write_text(json.dumps({
        "regime_probs": [0.70, 0.20, 0.05, 0.05],
        "rationale": "steady-leaning-trending",
        "generated_at": now,
    }))

    (INTEL_DIR / "thesis_monitor.json").write_text(json.dumps({
        "invalidations": [],
        "generated_at": now,
    }))

    (INTEL_DIR / "index_recon.json").write_text(json.dumps({
        "events": [
            {"ticker": "SPY", "type": "sp500_rebalance", "effective_ts": now + 14 * 86400},
            {"ticker": "QQQ", "type": "nasdaq100_rebalance", "effective_ts": now + 21 * 86400},
        ],
        "generated_at": now,
    }))

    print("seeded intel in", INTEL_DIR)


if __name__ == "__main__":
    seed()
