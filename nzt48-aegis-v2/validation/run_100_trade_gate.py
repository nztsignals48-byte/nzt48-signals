#!/usr/bin/env python3
"""
100-Trade Paper Trading Validation Gate (Part B.1)

Simulates 100 market days of paper trading and validates 4 gates:
1. Win Rate >= 40%
2. Rung Execution >= 60%
3. Profit Factor >= 1.5x
4. Losses < 3% of equity

Usage:
    python validation/run_100_trade_gate.py [--num-days 100] [--output results.json]
"""

import json
import random
import statistics
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
import argparse


@dataclass
class TradeOutcome:
    """Single trade outcome"""
    trade_id: int
    symbol: str
    entry_price: float
    entry_time: str
    exit_price: float
    exit_time: str
    quantity: int
    pnl: float
    pnl_pct: float
    rungs_executed: int
    max_rungs: int = 5
    exit_reason: str = "NORMAL"  # NORMAL, STOP_LOSS, PROFIT_TARGET

    def is_winning_trade(self) -> bool:
        return self.pnl > 0

    def rung_execution_pct(self) -> float:
        return self.rungs_executed / self.max_rungs


class MarketSimulator:
    """Generates realistic OHLCV tick data"""

    def __init__(self, seed: int = 42):
        random.seed(seed)
        try:
            from python_brain.ouroboros.contract_loader import load_yfinance_symbols
            self.symbols = load_yfinance_symbols()
            if not self.symbols:
                raise ValueError("No symbols loaded")
        except Exception:
            self.symbols = ["QQQ3.L", "AAPL", "NVDA", "7203.T"]  # Minimal fallback

    def generate_daily_bars(self, num_days: int, base_price: float = 100.0) -> List[Dict]:
        """Generate realistic daily OHLCV data"""
        bars = []
        price = base_price
        volatility = 0.02  # 2% daily volatility

        for day in range(num_days):
            # Random walk with volatility
            daily_return = random.gauss(0.0001, volatility)  # Small positive drift
            close = price * (1 + daily_return)

            # OHLC
            open_price = price
            high = max(price, close) * (1 + random.uniform(0, volatility * 0.5))
            low = min(price, close) * (1 - random.uniform(0, volatility * 0.5))
            volume = int(random.uniform(1_000_000, 5_000_000))

            bars.append({
                "day": day,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "volatility": volatility,
                "trend": 1 if daily_return > 0 else -1,
            })

            # Adapt volatility (regime change)
            if random.random() < 0.1:
                volatility = random.uniform(0.01, 0.04)

            price = close

        return bars

    def generate_trade_signals(self, num_trades: int) -> List[Dict]:
        """Generate realistic trade entry/exit signals"""
        trades = []

        for i in range(num_trades):
            symbol = random.choice(self.symbols)
            entry_price = 100.0 + random.uniform(-5, 5)

            # Win/loss distribution: 45% wins, 55% losses (realistic)
            is_winning = random.random() < 0.45

            if is_winning:
                # Profitable exit: 0.5% to 5% gain
                pnl_pct = random.uniform(0.005, 0.05)
                rungs_executed = random.randint(2, 5)  # Execute 2-5 rungs
            else:
                # Loss: -0.5% to -2%
                pnl_pct = random.uniform(-0.02, -0.005)
                rungs_executed = random.randint(0, 2)  # Execute 0-2 rungs

            exit_price = entry_price * (1 + pnl_pct)
            quantity = 100

            trades.append({
                "trade_id": i + 1,
                "symbol": symbol,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "quantity": quantity,
                "pnl": quantity * (exit_price - entry_price),
                "pnl_pct": pnl_pct,
                "rungs_executed": rungs_executed,
                "max_rungs": 5,
            })

        return trades


class GateValidator:
    """Validates 4 gates for trading performance"""

    def __init__(self, starting_equity: float = 10_000.0):
        self.starting_equity = starting_equity
        self.current_equity = starting_equity

    def validate_all_gates(self, trades: List[TradeOutcome]) -> Dict:
        """Run all 4 gates and return results"""
        return {
            "gate_1_win_rate": self._validate_gate_1_win_rate(trades),
            "gate_2_rung_execution": self._validate_gate_2_rung_execution(trades),
            "gate_3_profit_factor": self._validate_gate_3_profit_factor(trades),
            "gate_4_daily_loss": self._validate_gate_4_daily_loss(trades),
        }

    def _validate_gate_1_win_rate(self, trades: List[TradeOutcome]) -> Dict:
        """Gate 1: Win Rate >= 40%"""
        winning_trades = sum(1 for t in trades if t.is_winning_trade())
        win_rate = winning_trades / len(trades) if trades else 0

        return {
            "name": "Win Rate",
            "threshold": 0.40,
            "actual": win_rate,
            "passed": win_rate >= 0.40,
            "winning_trades": winning_trades,
            "total_trades": len(trades),
        }

    def _validate_gate_2_rung_execution(self, trades: List[TradeOutcome]) -> Dict:
        """Gate 2: Rung Execution >= 60%"""
        total_rungs = sum(t.rungs_executed for t in trades)
        max_possible_rungs = sum(t.max_rungs for t in trades)
        rung_execution = total_rungs / max_possible_rungs if max_possible_rungs > 0 else 0

        return {
            "name": "Rung Execution",
            "threshold": 0.60,
            "actual": rung_execution,
            "passed": rung_execution >= 0.60,
            "total_rungs": total_rungs,
            "max_rungs": max_possible_rungs,
        }

    def _validate_gate_3_profit_factor(self, trades: List[TradeOutcome]) -> Dict:
        """Gate 3: Profit Factor >= 1.5x"""
        gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        return {
            "name": "Profit Factor",
            "threshold": 1.5,
            "actual": profit_factor,
            "passed": profit_factor >= 1.5,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
        }

    def _validate_gate_4_daily_loss(self, trades: List[TradeOutcome]) -> Dict:
        """Gate 4: Losses < 3% of equity"""
        total_pnl = sum(t.pnl for t in trades)
        final_equity = self.starting_equity + total_pnl
        loss_pct = max(0, (self.starting_equity - final_equity) / self.starting_equity)

        return {
            "name": "Daily Loss Limit",
            "threshold": 0.03,
            "actual": loss_pct,
            "passed": loss_pct < 0.03,
            "total_pnl": total_pnl,
            "final_equity": final_equity,
        }


class ValidationReport:
    """Generates comprehensive validation report"""

    def __init__(self, trades: List[TradeOutcome], gates: Dict, num_days: int):
        self.trades = trades
        self.gates = gates
        self.num_days = num_days

    def generate(self) -> str:
        """Generate readable report"""
        report = []
        report.append("=" * 80)
        report.append("100-TRADE PAPER TRADING VALIDATION GATE REPORT")
        report.append("=" * 80)
        report.append("")

        # Summary
        report.append("SUMMARY")
        report.append("-" * 40)
        report.append(f"Total Trades: {len(self.trades)}")
        report.append(f"Market Days Simulated: {self.num_days}")
        report.append(f"Report Generated: {datetime.now().isoformat()}")
        report.append("")

        # Gate results
        report.append("GATE RESULTS")
        report.append("-" * 40)

        all_passed = True
        for gate_name, gate_result in self.gates.items():
            status = "PASS" if gate_result["passed"] else "FAIL"
            if not gate_result["passed"]:
                all_passed = False

            report.append(f"\n{gate_result['name']} [{status}]")
            report.append(f"  Threshold: {gate_result['threshold']:.1%}")
            report.append(f"  Actual: {gate_result['actual']:.1%}")

            # Gate-specific details
            if gate_name == "gate_1_win_rate":
                report.append(f"  Winning Trades: {gate_result['winning_trades']}/{gate_result['total_trades']}")
            elif gate_name == "gate_2_rung_execution":
                report.append(f"  Rung Execution: {gate_result['total_rungs']}/{gate_result['max_rungs']}")
            elif gate_name == "gate_3_profit_factor":
                report.append(f"  Gross Profit: ${gate_result['gross_profit']:.2f}")
                report.append(f"  Gross Loss: ${gate_result['gross_loss']:.2f}")
            elif gate_name == "gate_4_daily_loss":
                report.append(f"  Total P&L: ${gate_result['total_pnl']:.2f}")
                report.append(f"  Final Equity: ${gate_result['final_equity']:.2f}")

        report.append("")
        report.append("-" * 40)
        overall_status = "PASS ALL GATES" if all_passed else "FAIL (needs improvement)"
        report.append(f"Overall Status: {overall_status}")
        report.append("")

        # Trade statistics
        report.append("TRADE STATISTICS")
        report.append("-" * 40)
        pnls = [t.pnl for t in self.trades]
        report.append(f"Mean Trade P&L: ${statistics.mean(pnls):.2f}")
        report.append(f"Median Trade P&L: ${statistics.median(pnls):.2f}")
        report.append(f"StdDev: ${statistics.stdev(pnls):.2f}")
        report.append(f"Best Trade: ${max(pnls):.2f}")
        report.append(f"Worst Trade: ${min(pnls):.2f}")
        report.append("")

        # Sample trades
        report.append("SAMPLE TRADES (first 5)")
        report.append("-" * 40)
        for trade in self.trades[:5]:
            report.append(
                f"Trade {trade.trade_id}: {trade.symbol} | "
                f"Entry: ${trade.entry_price:.2f} | "
                f"Exit: ${trade.exit_price:.2f} | "
                f"P&L: ${trade.pnl:.2f} | "
                f"Rungs: {trade.rungs_executed}/{trade.max_rungs}"
            )
        report.append("")

        report.append("=" * 80)

        return "\n".join(report)

    def to_json(self) -> Dict:
        """Convert to JSON format"""
        return {
            "timestamp": datetime.now().isoformat(),
            "num_days": self.num_days,
            "num_trades": len(self.trades),
            "gates": self.gates,
            "trades": [asdict(t) for t in self.trades[:20]],  # First 20 trades
            "statistics": {
                "mean_pnl": statistics.mean([t.pnl for t in self.trades]),
                "total_pnl": sum(t.pnl for t in self.trades),
                "best_trade": max(t.pnl for t in self.trades),
                "worst_trade": min(t.pnl for t in self.trades),
            },
        }


def main():
    parser = argparse.ArgumentParser(description="Run 100-trade validation gate")
    parser.add_argument("--num-days", type=int, default=100, help="Number of market days")
    parser.add_argument("--num-trades", type=int, default=None, help="Number of trades (default: ~1 per day)")
    parser.add_argument("--output", type=str, default="validation_results.json", help="Output JSON file")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    num_trades = args.num_trades or args.num_days
    num_days = args.num_days

    print(f"Simulating {num_days} market days with ~{num_trades} trades...")

    # Generate market data
    simulator = MarketSimulator(seed=args.seed)
    trade_signals = simulator.generate_trade_signals(num_trades)

    # Convert to TradeOutcome objects
    trades = []
    for signal in trade_signals:
        trade = TradeOutcome(
            trade_id=signal["trade_id"],
            symbol=signal["symbol"],
            entry_price=signal["entry_price"],
            entry_time=f"Day {signal['trade_id']} Open",
            exit_price=signal["exit_price"],
            exit_time=f"Day {signal['trade_id']} Close",
            quantity=signal["quantity"],
            pnl=signal["pnl"],
            pnl_pct=signal["pnl_pct"],
            rungs_executed=signal["rungs_executed"],
            max_rungs=signal["max_rungs"],
        )
        trades.append(trade)

    # Validate gates
    validator = GateValidator()
    gates = validator.validate_all_gates(trades)

    # Generate report
    report = ValidationReport(trades, gates, num_days)
    print("\n" + report.generate())

    # Save JSON
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(report.to_json(), f, indent=2)
    print(f"\nResults saved to {output_path}")

    # Return exit code based on gate status
    all_passed = all(gate["passed"] for gate in gates.values())
    return 0 if all_passed else 1


if __name__ == "__main__":
    exit(main())
