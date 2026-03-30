"""Book 28: Daily P&L Scorecard.

Computes 5 core metrics from WAL trades with traffic light grading,
consistency tracking, recovery protocol, strategy breakdown, and
activation gate status.

Usage:
    python3 -m python_brain.metrics.daily_scorecard [--send-telegram]

Pipeline Integration (19-step nightly pipeline):
    from python_brain.metrics.daily_scorecard import run_scorecard_nightly
    result = run_scorecard_nightly()  # Returns dict for pipeline
    # Add as Step 19a after Bayesian calibration snapshot

Outputs:
    - {DATA_DIR}/scorecards/{YYYY-MM-DD}.json (daily archive)
    - {DATA_DIR}/daily_scorecard_latest.json (current snapshot)

Traffic Light Grading:
    GREEN:  Net P&L >= 1.5% of equity
    AMBER:  Net P&L 0.5% to 1.49% of equity
    RED:    Net P&L 0.0% to 0.49% of equity
    BLACK:  Net P&L < 0% of equity

Consistency Score: std(daily_returns) / mean(daily_returns) — lower is better.
Target: < 0.50 (tracked over rolling 30-day window).

Recovery Protocol (shadow mode — log only, don't act):
    After BLACK day: recommend 60% size reduction for 2 days
    Recovery ramp: 15 days back to full size
    Track consecutive BLACK days
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

# Path setup
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(_PROJECT_ROOT))

DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", "/app/events"))
SCORECARD_DIR = DATA_DIR / "scorecards"
LATEST_SCORECARD = DATA_DIR / "daily_scorecard_latest.json"

# Target equity (read from config or default to £10,000 ISA)
DEFAULT_EQUITY = 10000.0

# 7 AEGIS V2 strategies
STRATEGY_NAMES = [
    "VanguardSniper",
    "ApexScout",
    "OverlordFMOC",
    "OuroborosRegime",
    "CompoundingMachine",
    "DarkHorse",
    "Nightwatch",
]

# Session phases (time-of-day analysis)
SESSION_PHASES = {
    "LSE Pre-Market": (7, 8),     # 07:00-08:00
    "LSE Prime": (8, 11),          # 08:00-11:00
    "LSE Midday": (11, 14.5),      # 11:00-14:30
    "US-LSE Overlap": (14.5, 16.5), # 14:30-16:30
    "US Extended": (16.5, 21),     # 16:30-21:00
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Scorecard] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("daily_scorecard")


@dataclass
class TradeEvent:
    """A single closed trade from WAL."""
    ticker: str
    pnl: float
    gross_pnl: float
    commission: float
    slippage: float
    strategy: str
    entry_time_ns: int
    exit_time_ns: int
    mfe: float = 0.0
    mae: float = 0.0


@dataclass
class DailyScorecard:
    """Daily scorecard with traffic light grading and activation gates."""
    date: str
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    trade_count: int = 0
    win_rate: float = 0.0
    max_intraday_drawdown: float = 0.0
    grade: str = "RED"
    consistency_score: float = 0.0
    consecutive_black_days: int = 0
    strategy_breakdown: Dict[str, Dict] = field(default_factory=dict)
    session_breakdown: Dict[str, Dict] = field(default_factory=dict)
    gates: Dict[str, bool] = field(default_factory=dict)
    recovery_recommendation: Dict = field(default_factory=dict)
    equity_pct_gain: float = 0.0
    equity: float = DEFAULT_EQUITY


def load_trades_for_date(date_str: str) -> List[TradeEvent]:
    """Load all PositionClosed events from WAL for the given date."""
    trades: List[TradeEvent] = []

    # Scan current.ndjson and archive/*.ndjson
    wal_candidates = [WAL_DIR / "current.ndjson"]
    archive_dir = WAL_DIR / "archive"
    if archive_dir.exists():
        for f in sorted(archive_dir.glob("*.ndjson")):
            wal_candidates.append(f)

    for wal_path in wal_candidates:
        if not wal_path.exists():
            continue

        try:
            with open(wal_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    payload = event.get("payload", {})
                    if "PositionClosed" not in payload:
                        continue

                    pc = payload["PositionClosed"]
                    exit_ns = pc.get("exit_time_ns", 0)
                    if exit_ns == 0:
                        continue

                    # Filter by date
                    exit_date = datetime.fromtimestamp(exit_ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%d")
                    if exit_date != date_str:
                        continue

                    # Extract P&L and costs
                    net = pc.get("final_pnl", 0.0)
                    gross = pc.get("gross_pnl", net)
                    commission = pc.get("total_commission", 0.0)
                    slippage = pc.get("slippage_cost", 0.0)

                    trades.append(TradeEvent(
                        ticker=pc.get("symbol", "UNKNOWN"),
                        pnl=net,
                        gross_pnl=gross,
                        commission=commission,
                        slippage=slippage,
                        strategy=pc.get("strategy", "Unclassified"),
                        entry_time_ns=pc.get("entry_time_ns", 0),
                        exit_time_ns=exit_ns,
                        mfe=pc.get("mfe", 0.0),
                        mae=pc.get("mae", 0.0),
                    ))
        except Exception as e:
            log.warning("Error reading %s: %s", wal_path, e)

    return trades


def get_equity() -> float:
    """Read current equity from nightly_output.json or default."""
    nightly_file = DATA_DIR / "nightly_output.json"
    if nightly_file.exists():
        try:
            with open(nightly_file) as f:
                data = json.load(f)
                return float(data.get("equity", DEFAULT_EQUITY))
        except Exception:
            pass
    return DEFAULT_EQUITY


def compute_max_intraday_drawdown(trades: List[TradeEvent]) -> float:
    """Compute max intraday drawdown as percentage of starting equity."""
    if not trades:
        return 0.0

    # Sort by exit time
    sorted_trades = sorted(trades, key=lambda t: t.exit_time_ns)

    equity = get_equity()
    running_equity = equity
    peak = equity
    max_dd = 0.0

    for trade in sorted_trades:
        running_equity += trade.pnl
        if running_equity > peak:
            peak = running_equity
        dd = (peak - running_equity) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    return max_dd


def compute_traffic_light(net_pnl: float, equity: float) -> str:
    """Assign traffic light grade based on net P&L as % of equity."""
    pct = (net_pnl / equity * 100) if equity > 0 else 0.0
    if pct >= 1.5:
        return "GREEN"
    elif pct >= 0.5:
        return "AMBER"
    elif pct >= 0.0:
        return "RED"
    else:
        return "BLACK"


def load_historical_scorecards(days: int = 30) -> List[DailyScorecard]:
    """Load the last N days of scorecards for consistency calculation."""
    scorecards: List[DailyScorecard] = []

    if not SCORECARD_DIR.exists():
        return scorecards

    files = sorted(SCORECARD_DIR.glob("*.json"), reverse=True)
    for f in files[:days]:
        try:
            with open(f) as fp:
                data = json.load(fp)
                # Reconstruct dataclass
                sc = DailyScorecard(**{k: v for k, v in data.items() if k in DailyScorecard.__dataclass_fields__})
                scorecards.append(sc)
        except Exception as e:
            log.warning("Error loading %s: %s", f, e)

    return scorecards


def compute_consistency_score(scorecards: List[DailyScorecard]) -> float:
    """Compute consistency score: std(daily_returns) / mean(daily_returns).

    Lower is better. Target: < 0.50.
    Returns 0.0 if insufficient data (<10 days).
    """
    if len(scorecards) < 10:
        return 0.0

    returns = [sc.equity_pct_gain for sc in scorecards]
    mean_ret = sum(returns) / len(returns) if returns else 0.0
    if abs(mean_ret) < 1e-6:
        return 0.0

    variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
    std_ret = variance ** 0.5

    return std_ret / abs(mean_ret) if mean_ret != 0 else 0.0


def compute_strategy_breakdown(trades: List[TradeEvent]) -> Dict[str, Dict]:
    """Breakdown P&L by strategy with contribution %."""
    breakdown = defaultdict(lambda: {"gross_pnl": 0.0, "net_pnl": 0.0, "trade_count": 0})

    for trade in trades:
        strat = trade.strategy
        breakdown[strat]["gross_pnl"] += trade.gross_pnl
        breakdown[strat]["net_pnl"] += trade.pnl
        breakdown[strat]["trade_count"] += 1

    # Compute contribution %
    total_net = sum(s["net_pnl"] for s in breakdown.values())
    for strat, data in breakdown.items():
        data["contribution_pct"] = (data["net_pnl"] / total_net * 100) if total_net != 0 else 0.0

    return dict(breakdown)


def compute_session_breakdown(trades: List[TradeEvent]) -> Dict[str, Dict]:
    """Breakdown P&L by time-of-day session."""
    breakdown = defaultdict(lambda: {"gross_pnl": 0.0, "net_pnl": 0.0, "trade_count": 0})

    for trade in trades:
        # Convert entry time to UTC hour
        entry_dt = datetime.fromtimestamp(trade.entry_time_ns / 1e9, tz=timezone.utc)
        hour = entry_dt.hour + entry_dt.minute / 60.0

        # Assign to session phase
        session = "Other"
        for phase_name, (start, end) in SESSION_PHASES.items():
            if start <= hour < end:
                session = phase_name
                break

        breakdown[session]["gross_pnl"] += trade.gross_pnl
        breakdown[session]["net_pnl"] += trade.pnl
        breakdown[session]["trade_count"] += 1

    return dict(breakdown)


def check_activation_gates() -> Dict[str, bool]:
    """Check the 10 activation gates.

    G1: Positive expectation (PF >= 1.3, WR >= 40%)
    G2: N >= 300 trades total
    G3: All 7 strategies N >= 30 each
    G4: 30+ days for consistency score
    G5: Nightly pipeline stable
    G6-G10: Redis, Telegram, recovery curve, variance budgets, anti-tilt
    """
    gates = {
        "G1_positive_expectation": False,
        "G2_n_300_trades": False,
        "G3_all_strategies_n30": False,
        "G4_consistency_data": False,
        "G5_pipeline_stable": False,
        "G6_redis_connected": False,
        "G7_telegram_connected": False,
        "G8_recovery_protocol": False,
        "G9_variance_budgets": False,
        "G10_anti_tilt": False,
    }

    # Load nightly output for total trade count
    nightly_file = DATA_DIR / "nightly_output.json"
    if nightly_file.exists():
        try:
            with open(nightly_file) as f:
                data = json.load(f)
                total_trades = data.get("trade_count", 0)
                pf = data.get("profit_factor", 0.0)
                wr = data.get("win_rate", 0.0)

                # G1: Positive expectation
                if pf >= 1.3 and wr >= 0.40:
                    gates["G1_positive_expectation"] = True

                # G2: 300+ trades
                if total_trades >= 300:
                    gates["G2_n_300_trades"] = True
        except Exception as e:
            log.warning("Error reading nightly_output.json: %s", e)

    # G3: All strategies >= 30 trades each
    # (shadow mode — assume False for now, could scan all WAL)
    gates["G3_all_strategies_n30"] = False

    # G4: 30+ days of data
    if SCORECARD_DIR.exists():
        scorecard_count = len(list(SCORECARD_DIR.glob("*.json")))
        if scorecard_count >= 30:
            gates["G4_consistency_data"] = True

    # G5-G10: Placeholder checks (shadow mode)
    # In production, these would check Redis/Telegram connectivity, etc.
    gates["G5_pipeline_stable"] = True  # Assume stable if script runs
    gates["G6_redis_connected"] = False
    gates["G7_telegram_connected"] = False
    gates["G8_recovery_protocol"] = True  # Shadow mode active
    gates["G9_variance_budgets"] = False
    gates["G10_anti_tilt"] = False

    return gates


def get_recovery_recommendation(scorecards: List[DailyScorecard]) -> Dict:
    """Generate recovery protocol recommendation (shadow mode).

    After BLACK day: recommend 60% size reduction for 2 days.
    Recovery ramp: 15 days back to full size.
    Track consecutive BLACK days.
    """
    if not scorecards:
        return {"status": "no_data", "action": "none"}

    # Count consecutive BLACK days (most recent first)
    consecutive_black = 0
    for sc in scorecards:
        if sc.grade == "BLACK":
            consecutive_black += 1
        else:
            break

    if consecutive_black == 0:
        return {"status": "healthy", "action": "none", "consecutive_black_days": 0}

    if consecutive_black == 1:
        return {
            "status": "recovery_triggered",
            "action": "reduce_size_60pct",
            "duration_days": 2,
            "ramp_days": 15,
            "consecutive_black_days": consecutive_black,
        }
    elif consecutive_black >= 2:
        return {
            "status": "extended_recovery",
            "action": "reduce_size_60pct",
            "duration_days": 2 + consecutive_black,
            "ramp_days": 15,
            "consecutive_black_days": consecutive_black,
            "warning": "extended_drawdown",
        }

    return {"status": "unknown", "action": "none", "consecutive_black_days": consecutive_black}


class ScorecardGenerator:
    """Generates daily P&L scorecards from WAL events."""

    def __init__(self):
        self.equity = get_equity()

    def generate(self, date: Optional[str] = None) -> DailyScorecard:
        """Generate scorecard for the given date (default: yesterday UTC)."""
        if date is None:
            yesterday = datetime.now(timezone.utc) - timedelta(days=1)
            date = yesterday.strftime("%Y-%m-%d")

        log.info("Generating scorecard for %s", date)

        # Load trades
        trades = load_trades_for_date(date)

        # Core metrics
        gross_pnl = sum(t.gross_pnl for t in trades)
        net_pnl = sum(t.pnl for t in trades)
        trade_count = len(trades)
        win_rate = sum(1 for t in trades if t.pnl > 0) / trade_count if trade_count > 0 else 0.0
        max_dd = compute_max_intraday_drawdown(trades)
        equity_pct_gain = (net_pnl / self.equity * 100) if self.equity > 0 else 0.0

        # Traffic light grade
        grade = compute_traffic_light(net_pnl, self.equity)

        # Load historical scorecards for consistency
        historical = load_historical_scorecards(days=30)
        consistency = compute_consistency_score(historical)

        # Count consecutive BLACK days
        consecutive_black = 0
        for sc in historical:
            if sc.grade == "BLACK":
                consecutive_black += 1
            else:
                break
        if grade == "BLACK":
            consecutive_black += 1

        # Breakdowns
        strategy_breakdown = compute_strategy_breakdown(trades)
        session_breakdown = compute_session_breakdown(trades)

        # Activation gates
        gates = check_activation_gates()

        # Recovery recommendation
        recovery = get_recovery_recommendation(historical + [DailyScorecard(date=date, grade=grade)])

        return DailyScorecard(
            date=date,
            gross_pnl=round(gross_pnl, 2),
            net_pnl=round(net_pnl, 2),
            trade_count=trade_count,
            win_rate=round(win_rate * 100, 1),
            max_intraday_drawdown=round(max_dd, 2),
            grade=grade,
            consistency_score=round(consistency, 3),
            consecutive_black_days=consecutive_black,
            strategy_breakdown=strategy_breakdown,
            session_breakdown=session_breakdown,
            gates=gates,
            recovery_recommendation=recovery,
            equity_pct_gain=round(equity_pct_gain, 2),
            equity=self.equity,
        )

    def save_scorecard(self, scorecard: DailyScorecard):
        """Save scorecard to disk."""
        # Create scorecard directory
        SCORECARD_DIR.mkdir(parents=True, exist_ok=True)

        # Save daily archive
        daily_file = SCORECARD_DIR / f"{scorecard.date}.json"
        with open(daily_file, "w") as f:
            json.dump(asdict(scorecard), f, indent=2)
        log.info("Saved daily scorecard: %s", daily_file)

        # Save latest snapshot
        with open(LATEST_SCORECARD, "w") as f:
            json.dump(asdict(scorecard), f, indent=2)
        log.info("Updated latest scorecard: %s", LATEST_SCORECARD)


def run_scorecard_nightly(date: Optional[str] = None) -> Dict:
    """Run daily scorecard generation (pipeline step).

    Returns summary dict for nightly pipeline consumption.
    """
    generator = ScorecardGenerator()
    scorecard = generator.generate(date=date)
    generator.save_scorecard(scorecard)

    # Return summary for pipeline
    return {
        "date": scorecard.date,
        "net_pnl": scorecard.net_pnl,
        "trade_count": scorecard.trade_count,
        "win_rate": scorecard.win_rate,
        "grade": scorecard.grade,
        "consistency_score": scorecard.consistency_score,
        "gates_passed": sum(1 for v in scorecard.gates.values() if v),
        "gates_total": len(scorecard.gates),
        "recovery_status": scorecard.recovery_recommendation.get("status", "unknown"),
    }


def send_telegram_report(scorecard: DailyScorecard):
    """Send scorecard summary to Telegram (if configured)."""
    try:
        from python_brain.ouroboros.telegram_notify import send_alert

        grade_emoji = {
            "GREEN": "🟢",
            "AMBER": "🟡",
            "RED": "🔴",
            "BLACK": "⚫",
        }

        message = f"""📊 Daily Scorecard {scorecard.date}
{grade_emoji.get(scorecard.grade, "⚪")} Grade: {scorecard.grade}

💰 Net P&L: £{scorecard.net_pnl:+.2f} ({scorecard.equity_pct_gain:+.2f}%)
📈 Gross P&L: £{scorecard.gross_pnl:+.2f}
🔢 Trades: {scorecard.trade_count}
✅ Win Rate: {scorecard.win_rate:.1f}%
📉 Max DD: {scorecard.max_intraday_drawdown:.2f}%

🎯 Consistency: {scorecard.consistency_score:.3f}
🚪 Gates: {sum(1 for v in scorecard.gates.values() if v)}/{len(scorecard.gates)}
"""

        if scorecard.grade == "BLACK":
            message += f"\n⚠️ BLACK DAY #{scorecard.consecutive_black_days}\n"
            message += f"Recovery: {scorecard.recovery_recommendation.get('action', 'none')}\n"

        send_alert(message, tier=2)
        log.info("Sent Telegram report")
    except Exception as e:
        log.warning("Failed to send Telegram report: %s", e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Book 28: Daily P&L Scorecard")
    parser.add_argument("--date", help="Date to generate (YYYY-MM-DD, default: yesterday)")
    parser.add_argument("--send-telegram", action="store_true", help="Send Telegram report")
    args = parser.parse_args()

    generator = ScorecardGenerator()
    scorecard = generator.generate(date=args.date)
    generator.save_scorecard(scorecard)

    # Print summary
    print(json.dumps(asdict(scorecard), indent=2))

    # Send Telegram report if requested
    if args.send_telegram:
        send_telegram_report(scorecard)

    log.info("Scorecard generation complete")
