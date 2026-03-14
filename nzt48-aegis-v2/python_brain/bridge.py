"""Python Brain Bridge — long-lived subprocess for signal generation.

Protocol: JSON lines over stdin/stdout.
- Receives: {"type":"tick", "ticker_id":0, "last":10.5, "high":10.6, "low":10.4, "bid":10.49, "ask":10.51, "volume":1000, "timestamp_ns":..., ...context...}
- Responds: {"type":"signal", ...} or {"type":"no_signal", "ticker_id":...}
- Shutdown: {"type":"shutdown"}

Accumulates bar history per ticker. Evaluates Vanguard Sniper on each tick.
Runs 12-factor Kelly sizing when a signal is generated.
"""

import json
import sys
from collections import defaultdict, deque

# Add python_brain to path so brain.* imports work (strategies use `from brain.config import ...`)
sys.path.insert(0, "/app/python_brain")
sys.path.insert(0, "/app")

from brain.strategies.vanguard_sniper import evaluate as vanguard_evaluate
from brain.strategies.apex_scout import evaluate as apex_evaluate
from brain.sizing.kelly_12factor import kelly_12factor

MAX_BARS = 500

bar_history = defaultdict(lambda: deque(maxlen=MAX_BARS))


def process_tick(msg):
    """Process a tick message, return a response dict."""
    ticker_id = msg["ticker_id"]

    bar_history[ticker_id].append({
        "last": msg["last"],
        "bid": msg.get("bid", msg["last"]),
        "ask": msg.get("ask", msg["last"]),
        "high": msg.get("high", msg["last"]),
        "low": msg.get("low", msg["last"]),
        "volume": msg.get("volume", 0),
        "timestamp_ns": msg.get("timestamp_ns", 0),
    })

    ticks = list(bar_history[ticker_id])
    result = vanguard_evaluate(ticks)

    if result is None:
        return {"type": "no_signal", "ticker_id": ticker_id}

    # Run 12-factor Kelly sizing
    kelly = kelly_12factor(
        win_rate_raw=msg.get("win_rate", 0.5),
        total_trades=msg.get("total_trades", 0),
        avg_win=msg.get("avg_win", 0.02),
        avg_loss=msg.get("avg_loss", 0.02),
        leverage_factor=msg.get("leverage", 3),
        realized_vol_annual=msg.get("realized_vol", 0.30),
        correlation_to_portfolio=msg.get("correlation", 0.0),
        current_drawdown_pct=msg.get("drawdown_pct", 0.0),
        amihud_illiq=msg.get("amihud", 0.0),
        regime=msg.get("regime", "normal"),
        spread_pct=msg.get("spread_pct", 0.1),
        time_of_day_fraction=msg.get("time_fraction", 0.5),
        confidence=result["confidence"],
        portfolio_heat_pct=msg.get("heat_pct", 0.0),
        equity=msg.get("equity", 10000.0),
        price=msg["last"],
    )

    return {
        "type": "signal",
        "ticker_id": ticker_id,
        "direction": "Long",
        "confidence": result["confidence"],
        "kelly_fraction": kelly["kelly_fraction"],
        "shares": kelly["shares"],
        "strategy": "VanguardSniper",
    }


def process_apex_snapshot(msg):
    """Process an Apex snapshot message via ApexScout, return a response dict."""
    ticker_id = msg["ticker_id"]
    snapshots = msg.get("snapshots", [])

    if not snapshots:
        return {"type": "no_signal", "ticker_id": ticker_id}

    result = apex_evaluate(snapshots)

    if result is None:
        return {"type": "no_signal", "ticker_id": ticker_id}

    # Apex signals use preliminary Kelly from the scout (full 12-factor in future).
    return {
        "type": "signal",
        "ticker_id": ticker_id,
        "direction": "Long",
        "confidence": result["confidence"],
        "kelly_fraction": result["kelly_fraction"],
        "shares": 0,  # Apex sizing done by Rust side based on kelly_fraction
        "strategy": "ApexScout",
    }


def main():
    """Main loop: read JSON lines from stdin, write responses to stdout."""
    sys.stderr.write("Python Brain Bridge: started\n")
    sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"Bridge: JSON decode error: {e}\n")
            sys.stderr.flush()
            response = {"type": "error", "message": str(e)}
            print(json.dumps(response), flush=True)
            continue

        msg_type = msg.get("type", "")

        if msg_type == "tick":
            try:
                response = process_tick(msg)
            except Exception as e:
                # FIX 2026-03-11: Return "error" type (not "no_signal") so Rust
                # can distinguish "no trade setup" from "strategy is broken".
                # This prevents silent V1-style rot where a broken strategy
                # looks identical to a quiet market.
                import traceback
                tb = traceback.format_exc()
                sys.stderr.write(f"Bridge: tick processing error: {e}\n{tb}\n")
                sys.stderr.flush()
                response = {
                    "type": "error",
                    "ticker_id": msg.get("ticker_id", -1),
                    "error": f"{type(e).__name__}: {e}",
                }
            print(json.dumps(response), flush=True)

        elif msg_type == "apex_snapshot":
            # P6-I: Apex Scout evaluation for Apex-class tickers (60s OHLCV snapshots).
            try:
                response = process_apex_snapshot(msg)
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                sys.stderr.write(f"Bridge: apex processing error: {e}\n{tb}\n")
                sys.stderr.flush()
                response = {
                    "type": "error",
                    "ticker_id": msg.get("ticker_id", -1),
                    "error": f"{type(e).__name__}: {e}",
                }
            print(json.dumps(response), flush=True)

        elif msg_type == "shutdown":
            sys.stderr.write("Python Brain Bridge: shutting down\n")
            sys.stderr.flush()
            break

        else:
            response = {"type": "error", "message": f"unknown type: {msg_type}"}
            print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
