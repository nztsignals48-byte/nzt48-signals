"""Kyle's lambda estimator from L2 snapshots.

λ = price impact per unit signed volume. Standard linear approximation:
   r_t = λ · OFI_t + ε_t

Where OFI (order flow imbalance) is computed from L2 depth changes.
With 100ms snapshots we approximate OFI via depth deltas + sign of mid change.
Runs nightly as part of Ouroboros — writes per-symbol λ to learned.toml.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

import numpy as np


log = logging.getLogger("kyle-lam")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path("/Users/rr/aegis-v5")
ARCHIVE = ROOT / "data/archive"
OUTPUT = ROOT / "data/kyle_lambdas.json"


def ofi_from_snapshots(
    snapshots: list[dict],  # list of {bid, ask, bid_size, ask_size, mid, ts}
) -> list[tuple[float, float]]:
    """Return list of (OFI, mid_return) pairs from consecutive snapshots."""
    pairs = []
    for i in range(1, len(snapshots)):
        prev = snapshots[i - 1]
        curr = snapshots[i]
        try:
            d_bid_size = (curr.get("bid_size", 0) or 0) - (prev.get("bid_size", 0) or 0)
            d_ask_size = (curr.get("ask_size", 0) or 0) - (prev.get("ask_size", 0) or 0)
            ofi = d_bid_size - d_ask_size
            mid_prev = (prev.get("bid", 0) + prev.get("ask", 0)) / 2
            mid_curr = (curr.get("bid", 0) + curr.get("ask", 0)) / 2
            if mid_prev <= 0:
                continue
            ret = (mid_curr - mid_prev) / mid_prev
            pairs.append((float(ofi), float(ret)))
        except Exception:
            continue
    return pairs


def estimate_lambda(pairs: list[tuple[float, float]]) -> dict:
    """OLS regression of ret on OFI; return λ, R², residual std."""
    if len(pairs) < 30:
        return {"lambda": None, "r_squared": None, "n": len(pairs)}
    ofi = np.array([p[0] for p in pairs], dtype=float)
    ret = np.array([p[1] for p in pairs], dtype=float)

    # Center
    ofi_c = ofi - ofi.mean()
    ret_c = ret - ret.mean()

    denom = (ofi_c ** 2).sum()
    if denom == 0:
        return {"lambda": None, "r_squared": None, "n": len(pairs)}

    lam = float((ofi_c * ret_c).sum() / denom)
    pred = lam * ofi_c
    ss_res = float(((ret_c - pred) ** 2).sum())
    ss_tot = float((ret_c ** 2).sum())
    r2 = 1 - ss_res / max(ss_tot, 1e-12)
    return {
        "lambda": lam,
        "r_squared": float(r2),
        "n": len(pairs),
        "residual_std": float(np.sqrt(ss_res / max(len(pairs) - 1, 1))),
    }


def run() -> dict:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    result = {"per_symbol": {}, "n_symbols": 0}
    l2_files = sorted(ARCHIVE.glob("l2_book_*.jsonl"))
    if not l2_files:
        log.info("no L2 archives yet")
        OUTPUT.write_text(json.dumps(result, indent=2))
        return result

    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for path in l2_files[-7:]:
        try:
            with open(path) as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    sym = d.get("ticker")
                    if sym:
                        by_symbol[sym].append(d)
        except Exception as e:
            log.warning("skipped %s: %s", path, e)

    for sym, snaps in by_symbol.items():
        pairs = ofi_from_snapshots(snaps)
        result["per_symbol"][sym] = estimate_lambda(pairs)

    result["n_symbols"] = len(result["per_symbol"])
    OUTPUT.write_text(json.dumps(result, indent=2))
    log.info("estimated λ for %d symbols", result["n_symbols"])
    return result


def get_lambda_for(symbol: str) -> float | None:
    if not OUTPUT.exists():
        return None
    try:
        data = json.loads(OUTPUT.read_text())
    except Exception:
        return None
    info = data.get("per_symbol", {}).get(symbol)
    return info.get("lambda") if info else None


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        # Synthesize snapshots: ret = 0.0001 * OFI + noise
        rng = np.random.default_rng(42)
        snaps = []
        mid = 100.0
        bid_size, ask_size = 500, 500
        for _ in range(100):
            d_bid = int(rng.normal(0, 20))
            d_ask = int(rng.normal(0, 20))
            bid_size = max(1, bid_size + d_bid)
            ask_size = max(1, ask_size + d_ask)
            ofi = d_bid - d_ask
            mid += 0.0001 * ofi + rng.normal(0, 0.002)
            snaps.append({
                "bid": mid - 0.01, "ask": mid + 0.01,
                "bid_size": bid_size, "ask_size": ask_size,
                "ticker": "TEST",
            })
        pairs = ofi_from_snapshots(snaps)
        result = estimate_lambda(pairs)
        print(f"λ estimate: {result}")
        print("OK")
    else:
        run()
