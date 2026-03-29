"""Claude Decision Dispatcher — Book 72 Implementation.

Dispatches Claude decision types on their scheduled cadence.
Called by cron or nightly pipeline. Each decision type runs independently.

Usage:
    python3 -m python_brain.claude.dispatcher --type D-HYPOTHESIS
    python3 -m python_brain.claude.dispatcher --type D-CLUSTER
    python3 -m python_brain.claude.dispatcher --type D-DECAY
    python3 -m python_brain.claude.dispatcher --type D-CONFIG
    python3 -m python_brain.claude.dispatcher --type D-JOURNAL
    python3 -m python_brain.claude.dispatcher --weekly   # Run all weekly decisions
    python3 -m python_brain.claude.dispatcher --daily    # Run all daily decisions

Cron schedule (Book 72):
    Daily (04:56 UTC):  D-JOURNAL, D-CONFIG
    Weekly (Fri 22:00): D-HYPOTHESIS, D-CLUSTER, D-DECAY
    On-demand:          D-ERROR, D-DEPLOY, D-FORENSIC, D-KILL, D-UNIVERSE
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from python_brain.claude.decision_authority import (
    DecisionAuthority, DecisionType, DecisionResponse,
)

log = logging.getLogger("claude_dispatcher")

DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", "/app/config"))
OUTPUT_DIR = DATA_DIR / "claude" / "decisions"


def _load_context(decision_type: DecisionType) -> Dict[str, Any]:
    """Build context dict for a decision type from available data files."""
    ctx: Dict[str, Any] = {}

    # Common context
    nightly_path = DATA_DIR / "nightly_output.json"
    if nightly_path.exists():
        try:
            with open(nightly_path) as f:
                nightly = json.load(f)
            ctx["trade_count"] = nightly.get("trade_count", nightly.get("total_trades", 0))
            ctx["net_pnl"] = nightly.get("net_pnl", 0)
            ctx["metrics"] = nightly.get("metrics", {})
            ctx["regime"] = nightly.get("regime", "unknown")
            ctx["cost_adjusted_pnl"] = nightly.get("cost_adjusted_pnl", 0)
        except Exception:
            pass

    # Strategy-specific context
    if decision_type in (DecisionType.HYPOTHESIS, DecisionType.EDGE_DECAY, DecisionType.STRATEGY_KILL):
        ctx["active_strategies"] = ["S1_Microstructure", "S2_Reversion", "S3_MacroTrend",
                                     "S4_VolPremium", "S5_OvernightCarry", "S7_TailHedge",
                                     "VanguardSniper", "ApexScout"]
        # Load strategy stats if available
        stats_path = DATA_DIR / "strategy_stats.json"
        if stats_path.exists():
            try:
                with open(stats_path) as f:
                    stats = json.load(f)
                ctx["rolling_stats"] = stats.get("rolling_30d", {})
                ctx["alltime_stats"] = stats.get("alltime", {})
                ctx["top_tickers"] = stats.get("top_tickers", [])
                ctx["loss_patterns"] = stats.get("loss_patterns", [])
            except Exception:
                pass

    # Config context
    if decision_type == DecisionType.CONFIG_AUDIT:
        for name, path in [("config", CONFIG_DIR / "config.toml"), ("dynamic_weights", CONFIG_DIR / "dynamic_weights.toml")]:
            if path.exists():
                try:
                    try:
                        import tomllib
                    except ImportError:
                        import tomli as tomllib
                    with open(path, "rb") as f:
                        ctx[name] = tomllib.load(f)
                except Exception:
                    pass

    # Cluster context (consecutive losses)
    if decision_type == DecisionType.CLUSTER_ANALYSIS:
        ctx["cluster_size"] = ctx.get("metrics", {}).get("consecutive_losses", 0)
        ctx["strategies"] = ctx.get("active_strategies", [])

    return ctx


def _save_result(decision_type: DecisionType, response: DecisionResponse) -> Path:
    """Save decision result to dated JSON file."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    filename = f"{decision_type.value}_{date_str}.json"
    output_path = OUTPUT_DIR / filename

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decision_type": decision_type.value,
        **response.to_dict(),
    }

    with open(output_path, "w") as f:
        json.dump(result, f, indent=2, default=str)

    log.info("Decision saved: %s", output_path)
    return output_path


def dispatch(decision_type: DecisionType, send_telegram: bool = False) -> Optional[DecisionResponse]:
    """Dispatch a single decision type."""
    log.info("Dispatching %s", decision_type.value)
    start = time.time()

    # Get trade count for authority context
    nightly_path = DATA_DIR / "nightly_output.json"
    trade_count = 0
    if nightly_path.exists():
        try:
            with open(nightly_path) as f:
                data = json.load(f)
            trade_count = int(data.get("trade_count", data.get("total_trades", 0)))
        except Exception:
            pass

    authority = DecisionAuthority(trade_count=trade_count)
    ctx = _load_context(decision_type)
    request = authority.get_prompt(decision_type, ctx)
    response = authority.execute(request)

    elapsed = time.time() - start
    log.info("%s completed in %.1fs: confidence=%.2f, model=%s",
             decision_type.value, elapsed, response.confidence, response.model_used)

    # Save result
    _save_result(decision_type, response)

    # Telegram notification for important decisions
    if send_telegram and response.confidence > 0.0:
        try:
            from python_brain.ouroboros.claude_helper import send_telegram as tg
            summary = f"<b>{decision_type.value}</b>\n"
            summary += f"Confidence: {response.confidence:.0%}\n"
            summary += f"Model: {response.model_used}\n"
            rec = response.recommendation
            if len(rec) > 200:
                rec = rec[:200] + "..."
            summary += f"\n{rec}"
            tg(summary)
        except Exception as e:
            log.warning("Telegram send failed: %s", e)

    return response


# Decision type groupings for batch dispatch
DAILY_DECISIONS = [DecisionType.JOURNAL_UPDATE, DecisionType.CONFIG_AUDIT]
WEEKLY_DECISIONS = [DecisionType.HYPOTHESIS, DecisionType.CLUSTER_ANALYSIS, DecisionType.EDGE_DECAY]


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [ClaudeDispatcher] %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="Claude Decision Dispatcher (Book 72)")
    parser.add_argument("--type", type=str, help="Decision type to dispatch (e.g. D-HYPOTHESIS)")
    parser.add_argument("--daily", action="store_true", help="Run all daily decisions")
    parser.add_argument("--weekly", action="store_true", help="Run all weekly decisions")
    parser.add_argument("--telegram", action="store_true", help="Send results via Telegram")
    args = parser.parse_args()

    if args.type:
        # Single decision dispatch
        try:
            dt = DecisionType(args.type)
        except ValueError:
            print(f"Unknown decision type: {args.type}")
            print(f"Available: {[d.value for d in DecisionType]}")
            sys.exit(1)
        dispatch(dt, send_telegram=args.telegram)

    elif args.daily:
        log.info("Running daily decisions: %s", [d.value for d in DAILY_DECISIONS])
        for dt in DAILY_DECISIONS:
            try:
                dispatch(dt, send_telegram=args.telegram)
            except Exception as e:
                log.error("Failed to dispatch %s: %s", dt.value, e)

    elif args.weekly:
        log.info("Running weekly decisions: %s", [d.value for d in WEEKLY_DECISIONS])
        for dt in WEEKLY_DECISIONS:
            try:
                dispatch(dt, send_telegram=args.telegram)
            except Exception as e:
                log.error("Failed to dispatch %s: %s", dt.value, e)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
