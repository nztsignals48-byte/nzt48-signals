"""Volkov honest report — weekly brutal assessment, no spin.

Generates a report that actively tries to poke holes in performance.
Flags suspicious wins (fat tails, single-ticker concentration, regime dependence).
Written to docs/volkov_reports/YYYY-WXX.md.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


ROOT = Path("/Users/rr/aegis-v5")
FILLS_PATH = ROOT / "data/bus/fills.closed.jsonl"
REPORT_DIR = ROOT / "docs/volkov_reports"


def load_fills(limit: int = 10000) -> list[dict]:
    if not FILLS_PATH.exists():
        return []
    out = []
    with open(FILLS_PATH) as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
            if len(out) >= limit:
                break
    return out


def brutal_analysis(fills: list[dict]) -> dict:
    """Return honest metrics + red flags."""
    if not fills:
        return {"n_fills": 0, "red_flags": ["No fills to analyze"]}

    pnls = [float(f.get("realized_pnl_bps") or f.get("realized_pnl_gbp") or 0) for f in fills]
    tickers = [f.get("ticker") for f in fills]
    strategies = [f.get("strategy_name") for f in fills]

    arr = np.array(pnls)
    red_flags = []

    # 1. Concentration: does one ticker dominate?
    ticker_pnl = defaultdict(float)
    for t, p in zip(tickers, pnls):
        if t:
            ticker_pnl[t] += p
    total = sum(ticker_pnl.values())
    if total > 0:
        top_ticker, top_pnl = max(ticker_pnl.items(), key=lambda x: x[1])
        top_pct = top_pnl / total * 100
        if top_pct > 40:
            red_flags.append(f"CONCENTRATION: {top_ticker} = {top_pct:.0f}% of all winnings")

    # 2. Fat tails
    if arr.std() > 0:
        skew = float(((arr - arr.mean()) ** 3).mean() / arr.std() ** 3)
        kurt = float(((arr - arr.mean()) ** 4).mean() / arr.std() ** 4)
        if kurt > 10:
            red_flags.append(f"FAT TAILS: kurtosis={kurt:.1f} — extreme outliers dominate")
        if skew < -1.5:
            red_flags.append(f"NEGATIVE SKEW: skew={skew:.2f} — wins small, losses large")

    # 3. Single-strategy dominance
    strat_pnl = defaultdict(float)
    strat_count = defaultdict(int)
    for s, p in zip(strategies, pnls):
        if s:
            strat_pnl[s] += p
            strat_count[s] += 1
    if strat_pnl:
        top_strat = max(strat_pnl.items(), key=lambda x: x[1])
        if top_strat[1] > total * 0.7 if total != 0 else False:
            red_flags.append(
                f"SINGLE-STRATEGY: {top_strat[0]} = {top_strat[1]/total*100:.0f}% of PnL"
            )

    # 4. Sample size
    if len(arr) < 100:
        red_flags.append(f"SAMPLE TOO SMALL: only {len(arr)} fills — edge estimates unreliable")

    # 5. Win rate vs expected
    wins = (arr > 0).sum()
    win_rate = wins / len(arr) if arr.size else 0
    if win_rate > 0.80:
        red_flags.append(f"SUSPICIOUS WIN RATE: {win_rate*100:.0f}% — probable tiny edge + costs lurking")
    if win_rate < 0.30:
        red_flags.append(f"LOW WIN RATE: {win_rate*100:.0f}% — need high avg win to compensate")

    # 6. Cost-adjusted reality
    # Assume crude 5 bps cost per trade
    cost_adjusted = arr - 5.0
    ca_mean = float(cost_adjusted.mean()) if arr.size else 0
    if ca_mean <= 0:
        red_flags.append(
            f"COST-ADJUSTED EDGE NEGATIVE: mean pnl {arr.mean():.2f}bps, "
            f"after 5bps cost = {ca_mean:.2f}bps"
        )

    return {
        "n_fills": len(fills),
        "total_pnl_bps": float(arr.sum()) if arr.size else 0,
        "mean_bps": float(arr.mean()) if arr.size else 0,
        "median_bps": float(np.median(arr)) if arr.size else 0,
        "std_bps": float(arr.std()) if arr.size else 0,
        "win_rate": win_rate,
        "skew": float(skew) if arr.std() > 0 else 0,
        "kurtosis": float(kurt) if arr.std() > 0 else 0,
        "cost_adjusted_mean_bps": ca_mean,
        "top_ticker": top_ticker if total > 0 else None,
        "top_ticker_contribution_pct": top_pct if total > 0 else 0,
        "red_flags": red_flags,
    }


def write_report() -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    week = now.strftime("%Y-W%W")
    path = REPORT_DIR / f"{week}.md"

    fills = load_fills()
    analysis = brutal_analysis(fills)

    lines = [
        f"# Volkov Honest Report — Week {week}",
        "",
        "## No Spin Assessment",
        "",
        f"- Fills analyzed: {analysis['n_fills']}",
        f"- Total PnL: {analysis['total_pnl_bps']:.2f} bps",
        f"- Mean per trade: {analysis['mean_bps']:.2f} bps (median: {analysis['median_bps']:.2f})",
        f"- Win rate: {analysis.get('win_rate', 0)*100:.1f}%",
        f"- Skew: {analysis.get('skew', 0):.2f}, Kurtosis: {analysis.get('kurtosis', 0):.2f}",
        f"- **Cost-adjusted mean (−5bps): {analysis['cost_adjusted_mean_bps']:.2f} bps**",
        "",
    ]
    if analysis["red_flags"]:
        lines.append("## RED FLAGS")
        for flag in analysis["red_flags"]:
            lines.append(f"- {flag}")
    else:
        lines.append("## No red flags (yet)")
    lines.append("")
    lines.append("## Honest Verdict")
    if analysis["cost_adjusted_mean_bps"] > 2:
        lines.append("System shows cost-adjusted edge. Continue paper.")
    elif analysis["cost_adjusted_mean_bps"] > 0:
        lines.append("Marginal edge. Needs more data before promotion.")
    else:
        lines.append("NEGATIVE cost-adjusted edge. Do not promote any strategy.")

    path.write_text("\n".join(lines))
    return path


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        p = write_report()
        print(f"Report: {p}")
        print(p.read_text()[:600])
        print("OK")
