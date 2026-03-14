"""
Indicator Ranking Report — NZT-48 Friday Weekly Telegram Report
Computes lift ratios for each indicator/filter: how much does each one
improve win rate above baseline? Sent every Friday 21:00 UTC.
"""

import json
import os
import logging
from datetime import date, datetime
from typing import Optional
from core.clock import now_utc

logger = logging.getLogger(__name__)

OUTCOMES_FILE = "data/outcomes.jsonl"

ISA_TICKERS = [
    "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L", "TSL3.L",
    "TSM3.L", "MU2.L", "QQQS.L", "3USS.L", "QQQ5.L", "SP5L.L",
]

MIN_TRADES_FOR_INCLUSION = 5  # minimum trades before an indicator is reported
BASELINE_WIN_RATE = 0.383      # from 413 historical trades


class IndicatorRankingReport:
    """
    Reads outcomes.jsonl and computes per-indicator lift ratios.
    Lift = win_rate_when_indicator_present / baseline_win_rate.
    Lift > 1.0 = indicator improves outcomes.
    Lift < 1.0 = indicator is noise or harmful.
    """

    def __init__(self, outcomes_file: str = OUTCOMES_FILE):
        self.outcomes_file = outcomes_file
        self._trades = None

    def _load_trades(self) -> list:
        if self._trades is not None:
            return self._trades
        trades = []
        if not os.path.exists(self.outcomes_file):
            return trades
        try:
            with open(self.outcomes_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            trades.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            logger.warning(f"IndicatorRankingReport: failed to load trades: {e}")
        self._trades = trades
        return trades

    def compute_lift_ratios(self) -> dict:
        """
        Returns dict of {indicator_name: {lift, win_rate, trade_count, label}}.
        Indicators are inferred from trade metadata fields.
        """
        trades = self._load_trades()
        if not trades:
            return {}

        # Collect all possible indicator fields from trade data
        indicator_fields = set()
        for t in trades:
            for key in t.keys():
                if key.startswith("ind_") or key in {
                    "gap_type", "day_of_week", "is_pre_expiry",
                    "squeeze_risk", "iv_elevated", "window_dressing",
                    "earnings_fade_veto", "regime",
                }:
                    indicator_fields.add(key)

        results = {}
        total = len(trades)
        baseline = sum(1 for t in trades if t.get("outcome") in ("WIN", "TARGET")) / max(total, 1)

        for field in indicator_fields:
            # Group trades by field value
            present = [t for t in trades if t.get(field) not in (None, False, "", 0)]
            if len(present) < MIN_TRADES_FOR_INCLUSION:
                continue

            wins = sum(1 for t in present if t.get("outcome") in ("WIN", "TARGET"))
            wr = wins / len(present)
            lift = wr / baseline if baseline > 0 else 1.0

            results[field] = {
                "lift": round(lift, 3),
                "win_rate": round(wr, 3),
                "trade_count": len(present),
                "label": self.get_confidence_label(lift),
            }

        # Also rank by ticker
        for ticker in ISA_TICKERS:
            ticker_trades = [t for t in trades if t.get("ticker") == ticker]
            if len(ticker_trades) < MIN_TRADES_FOR_INCLUSION:
                continue
            wins = sum(1 for t in ticker_trades if t.get("outcome") in ("WIN", "TARGET"))
            wr = wins / len(ticker_trades)
            lift = wr / baseline if baseline > 0 else 1.0
            results[f"ticker:{ticker}"] = {
                "lift": round(lift, 3),
                "win_rate": round(wr, 3),
                "trade_count": len(ticker_trades),
                "label": self.get_confidence_label(lift),
            }

        return results

    def get_confidence_label(self, lift: float) -> str:
        if lift >= 1.40:
            return "🟢 STRONG"
        elif lift >= 1.15:
            return "🟡 POSITIVE"
        elif lift >= 0.85:
            return "⚪ NEUTRAL"
        elif lift >= 0.60:
            return "🟠 WEAK"
        else:
            return "🔴 REMOVE"

    def generate_report(self) -> str:
        trades = self._load_trades()
        total = len(trades)
        wins = sum(1 for t in trades if t.get("outcome") in ("WIN", "TARGET"))
        baseline = wins / max(total, 1)

        ratios = self.compute_lift_ratios()
        if not ratios:
            return (
                "📊 Weekly Indicator Ranking — NZT-48\n"
                f"  Total trades: {total}  |  Win rate: {baseline*100:.1f}%\n"
                "  No indicator data available yet (need metadata in outcomes.jsonl)"
            )

        # Sort by lift descending
        ranked = sorted(ratios.items(), key=lambda x: x[1]["lift"], reverse=True)

        lines = [
            "📊 *Weekly Indicator Ranking — NZT-48*",
            f"  Trades: {total}  |  Baseline WR: {baseline*100:.1f}%",
            f"  Date: {date.today().strftime('%d %b %Y')}",
            "",
            "*Indicator Lift Ratios (trades where indicator was present):*",
        ]

        for name, data in ranked[:20]:  # Top 20
            lines.append(
                f"  {data['label']}  `{name}` — "
                f"lift: {data['lift']:.2f}x, WR: {data['win_rate']*100:.0f}%, "
                f"n={data['trade_count']}"
            )

        # Bottom performers
        bottom = [x for x in ranked if x[1]["lift"] < 0.85]
        if bottom:
            lines.append("")
            lines.append("*⚠️ Underperforming indicators (review or disable):*")
            for name, data in bottom[:5]:
                lines.append(f"  {data['label']}  `{name}` — lift: {data['lift']:.2f}x")

        lines.append("")
        lines.append(f"_NZT-48 Auto-generated {now_utc().strftime('%Y-%m-%d %H:%M')} UTC_")

        return "\n".join(lines)

    def should_send_today(self, check_date: Optional[date] = None) -> bool:
        """True on Fridays."""
        d = check_date or date.today()
        return d.weekday() == 4
