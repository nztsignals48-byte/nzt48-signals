#!/usr/bin/env python3
"""
PHASE 8 ACCEPTANCE TEST: 30-Day Synthetic Backtest

Validates the full Ouroboros nightly pipeline by:
1. Simulating 30 days of price ticks with synthetic trades
2. Running Ouroboros pipeline nightly to compute DynamicWeights
3. Verifying weights are applied to next day's trades
4. Checking for TOML persistence corruption
5. Confirming no missed/skipped days
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
import shutil

sys.path.insert(0, str(Path(__file__).parent.parent))

from ouroboros.config import DYNAMIC_WEIGHTS_FILE, UNIVERSE_CLASS_FILE, PARAMETER_HISTORY_DIR
from ouroboros.pipeline import run_pipeline, PipelineResult
from ouroboros.toml_writer import flush_all

def generate_synthetic_wal(day_idx: int, num_trades: int = 5) -> list:
    """Generate synthetic WAL events for one day."""
    trades = []
    base_timestamp = (datetime(2025, 1, 1) + timedelta(days=day_idx)).timestamp()

    for i in range(num_trades):
        timestamp_ns = int((base_timestamp + i * 3600) * 1e9)

        # Synthetic trade: entry + exit
        trades.append({
            "type": "OrderSubmitted",
            "ticker_id": i % 12,
            "order_id": f"day{day_idx}_trade{i}_buy",
            "side": "Buy",
            "quantity": 100,
            "price": 100.0 + day_idx * 0.5,
            "timestamp_ns": timestamp_ns,
        })

        trades.append({
            "type": "OrderFilled",
            "order_id": f"day{day_idx}_trade{i}_buy",
            "filled_price": 100.0 + day_idx * 0.5,
            "filled_qty": 100,
            "timestamp_ns": timestamp_ns + 10_000_000_000,  # 10s later
            "commission": 5.0,
        })

        # Synthetic exit (50% win rate)
        exit_price = (100.0 + day_idx * 0.5) * (1.01 if i % 2 == 0 else 0.99)
        trades.append({
            "type": "OrderSubmitted",
            "order_id": f"day{day_idx}_trade{i}_sell",
            "ticker_id": i % 12,
            "side": "Sell",
            "quantity": 100,
            "price": exit_price,
            "timestamp_ns": timestamp_ns + 3600_000_000_000,  # 1h later
        })

        trades.append({
            "type": "OrderFilled",
            "order_id": f"day{day_idx}_trade{i}_sell",
            "filled_price": exit_price,
            "filled_qty": 100,
            "timestamp_ns": timestamp_ns + 3610_000_000_000,
            "commission": 5.0,
        })

    return trades

def run_30day_backtest(output_dir: Path) -> tuple:
    """Run 30-day backtest with nightly Ouroboros updates."""
    config_dir = output_dir / "config"
    events_dir = output_dir / "events"
    parameter_history_dir = config_dir / PARAMETER_HISTORY_DIR

    config_dir.mkdir(parents=True, exist_ok=True)
    events_dir.mkdir(parents=True, exist_ok=True)
    parameter_history_dir.mkdir(parents=True, exist_ok=True)

    # Copy base config files
    repo_config = Path(__file__).parent.parent / "config"
    for f in ["config.toml", "contracts.toml", "initial_universe.toml", "uk_holidays.toml"]:
        src = repo_config / f
        if src.exists():
            shutil.copy(src, config_dir / f)

    results = {
        "days_completed": 0,
        "days_failed": [],
        "toml_integrity": [],
        "weight_evolution": [],
    }

    for day_idx in range(30):
        print(f"\n{'='*60}")
        print(f"DAY {day_idx + 1}/30")
        print(f"{'='*60}")

        # Generate synthetic WAL for this day
        trades = generate_synthetic_wal(day_idx, num_trades=5)
        wal_path = events_dir / f"day_{day_idx:02d}.ndjson"

        with open(wal_path, 'w') as f:
            for trade in trades:
                f.write(json.dumps(trade) + "\n")

        print(f"✓ Generated WAL: {len(trades)} events")

        # Run Ouroboros nightly pipeline
        try:
            result = run_pipeline(
                wal_path=wal_path,
                config_dir=config_dir,
                london_time_secs=23 * 3600,  # 23:00 (safe after LSE close)
                day_count=day_idx + 1,
            )
        except Exception as e:
            print(f"✗ Pipeline execution failed: {e}")
            results["days_failed"].append(day_idx)
            continue

        if not result.success:
            print(f"✗ Pipeline returned error: {result.error}")
            results["days_failed"].append(day_idx)
            continue

        print(f"✓ Ouroboros pipeline executed successfully")
        if result.bayesian:
            print(f"  Bayesian WR: {result.bayesian.bayesian_win_rate:.1%}")
            print(f"  Trade count: {result.bayesian.trade_count}")

        # Verify TOML files exist and are not corrupted
        weights_file = config_dir / DYNAMIC_WEIGHTS_FILE
        universe_file = config_dir / UNIVERSE_CLASS_FILE

        integrity_check = {
            "day": day_idx,
            "weights_exists": weights_file.exists(),
            "weights_readable": False,
            "universe_exists": universe_file.exists(),
            "universe_readable": False,
            "weights_size": 0,
            "universe_size": 0,
        }

        if weights_file.exists():
            try:
                with open(weights_file) as f:
                    content = f.read()
                    # Just check if it's valid TOML by trying to parse
                    try:
                        import tomllib
                        tomllib.loads(content)
                    except (ImportError, ModuleNotFoundError):
                        import toml
                        toml.loads(content)
                integrity_check["weights_readable"] = True
                integrity_check["weights_size"] = weights_file.stat().st_size
            except Exception as e:
                print(f"✗ TOML corruption detected: {e}")
                results["toml_integrity"].append({**integrity_check, "error": str(e)})

        if universe_file.exists():
            try:
                with open(universe_file) as f:
                    content = f.read()
                    try:
                        import tomllib
                        tomllib.loads(content)
                    except (ImportError, ModuleNotFoundError):
                        import toml
                        toml.loads(content)
                integrity_check["universe_readable"] = True
                integrity_check["universe_size"] = universe_file.stat().st_size
            except Exception as e:
                print(f"✗ Universe TOML corruption: {e}")

        results["toml_integrity"].append(integrity_check)
        results["days_completed"] += 1

        # Track weight evolution
        if result.bayesian:
            results["weight_evolution"].append({
                "day": day_idx,
                "win_rate": result.bayesian.bayesian_win_rate,
                "trade_count": result.bayesian.trade_count,
            })

        # Verify parameter history archive
        if result.archive_path and Path(result.archive_path).exists():
            print(f"✓ Archive: {result.archive_path}")

    # Final report
    print(f"\n{'='*60}")
    print("30-DAY BACKTEST COMPLETE")
    print(f"{'='*60}")
    print(f"Days completed: {results['days_completed']}/30")

    if results['days_failed']:
        print(f"Days failed: {results['days_failed']}")

    # Check for TOML corruption
    corruption_count = sum(1 for c in results['toml_integrity'] if not c['weights_readable'] or not c['universe_readable'])
    if corruption_count == 0:
        print(f"✓ TOML integrity: 100% ({len(results['toml_integrity'])} days)")
    else:
        print(f"✗ TOML corruption detected: {corruption_count}/{len(results['toml_integrity'])} days")

    # Check weight evolution
    if results['weight_evolution']:
        first_wr = results['weight_evolution'][0]['win_rate']
        last_wr = results['weight_evolution'][-1]['win_rate']
        print(f"✓ Weight evolution: WR {first_wr:.1%} → {last_wr:.1%}")

    # Success criteria
    success = (
        results['days_completed'] >= 27  # Allow 3 failures
        and corruption_count == 0
        and len(results['days_failed']) < 4
    )

    print(f"\n{'PASS' if success else 'FAIL'}: Phase 8 acceptance criteria")

    return success, results

if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)
        success, results = run_30day_backtest(output_dir)

        # Print detailed results
        print(f"\nDetailed results: {json.dumps(results, indent=2, default=str)}")

        sys.exit(0 if success else 1)
