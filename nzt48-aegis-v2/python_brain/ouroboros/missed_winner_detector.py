"""
Sprint 3D: Missed Winner Detector — Offline Nightly Job

Reads today's vetoed signals from gate_vetoes.ndjson and WAL data.
Compares veto price to subsequent price movement.
Classifies each veto as GOOD_VETO, BAD_VETO, or AMBIGUOUS.
Writes MissedWinnerCandidate events to missed_winners.ndjson.

Run nightly after market close (during dark window).
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

GATE_VETOES_PATH = Path("/app/data/gate_vetoes.ndjson")
WAL_PATH = Path("/app/data/wal")
OUTPUT_PATH = Path("/app/data/claude/missed_winners")
ATR_THRESHOLD = 1.5  # Price must move > 1.5x ATR to qualify as missed winner


def load_todays_vetoes() -> list[dict]:
    """Load today's gate veto events."""
    vetoes = []
    if not GATE_VETOES_PATH.exists():
        return vetoes
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with open(GATE_VETOES_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                ts = event.get("timestamp", "")
                if ts.startswith(today):
                    vetoes.append(event)
            except json.JSONDecodeError:
                continue
    return vetoes


def load_price_after_veto(ticker_id: int, veto_ns: int) -> Optional[float]:
    """Load the price N bars after the veto from WAL data."""
    # Look for PositionClosed or bar data after veto timestamp
    # For now, look at the last known price for this ticker
    wal_files = sorted(WAL_PATH.glob("*.ndjson"), reverse=True)
    for wal_file in wal_files[:3]:  # Check last 3 WAL files
        with open(wal_file) as f:
            for line in f:
                try:
                    event = json.loads(line)
                    if (event.get("ticker_id") == ticker_id
                            and event.get("timestamp_ns", 0) > veto_ns
                            and "last_price" in event):
                        return event["last_price"]
                except json.JSONDecodeError:
                    continue
    return None


def classify_veto(veto: dict, price_after: Optional[float]) -> dict:
    """Classify a veto as GOOD, BAD, or AMBIGUOUS."""
    veto_price = veto.get("price_at_veto", 0.0)
    direction = veto.get("signal_direction", "Long")
    atr = veto.get("atr_at_veto", 0.0)

    if price_after is None or veto_price <= 0 or atr <= 0:
        return {
            **veto,
            "classification": "AMBIGUOUS",
            "reason": "insufficient_data",
            "price_after": None,
            "potential_move_atr": None,
        }

    if direction == "Long":
        move = price_after - veto_price
    else:
        move = veto_price - price_after

    move_in_atr = move / atr if atr > 0 else 0.0

    if move_in_atr > ATR_THRESHOLD:
        classification = "BAD_VETO"  # Missed a winner
        reason = f"price_moved_{move_in_atr:.1f}x_atr_in_signal_direction"
    elif move_in_atr < -ATR_THRESHOLD:
        classification = "GOOD_VETO"  # Avoided a loser
        reason = f"price_moved_{abs(move_in_atr):.1f}x_atr_against_signal"
    else:
        classification = "AMBIGUOUS"
        reason = f"price_moved_{move_in_atr:.1f}x_atr_marginal"

    return {
        **veto,
        "classification": classification,
        "reason": reason,
        "price_after": price_after,
        "potential_move_atr": round(move_in_atr, 2),
    }


def run():
    """Main entry point for nightly missed winner detection."""
    vetoes = load_todays_vetoes()
    if not vetoes:
        log.info("No vetoes found for today — nothing to analyze.")
        return

    log.info(f"Analyzing {len(vetoes)} vetoed signals for missed winners...")

    results = []
    bad_vetoes = 0
    good_vetoes = 0

    for veto in vetoes:
        ticker_id = veto.get("ticker_id", 0)
        veto_ns = veto.get("timestamp_ns", 0)
        price_after = load_price_after_veto(ticker_id, veto_ns)
        classified = classify_veto(veto, price_after)
        results.append(classified)

        if classified["classification"] == "BAD_VETO":
            bad_vetoes += 1
        elif classified["classification"] == "GOOD_VETO":
            good_vetoes += 1

    # Write results
    OUTPUT_PATH.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_file = OUTPUT_PATH / f"{today}_missed_winners.ndjson"

    with open(output_file, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    log.info(
        f"Missed winner analysis complete: {len(results)} vetoes analyzed, "
        f"{bad_vetoes} bad vetoes (missed winners), {good_vetoes} good vetoes, "
        f"{len(results) - bad_vetoes - good_vetoes} ambiguous"
    )

    # Summary for operator briefing
    summary = {
        "date": today,
        "total_vetoes": len(results),
        "bad_vetoes": bad_vetoes,
        "good_vetoes": good_vetoes,
        "ambiguous": len(results) - bad_vetoes - good_vetoes,
        "bad_veto_pct": round(bad_vetoes / max(len(results), 1) * 100, 1),
        "top_missed_winners": [
            r for r in sorted(
                [r for r in results if r["classification"] == "BAD_VETO"],
                key=lambda x: abs(x.get("potential_move_atr", 0)),
                reverse=True,
            )[:5]
        ],
    }

    summary_file = OUTPUT_PATH / f"{today}_missed_winners_summary.json"
    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2)

    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
