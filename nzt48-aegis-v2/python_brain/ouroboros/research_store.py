"""Ouroboros Research Store — Structured knowledge base for Claude nightly review.

Three components:
  1. ResearchContextStore   — Rolling context window for Claude to read
  2. AnomalyBaselineLibrary — Statistical baselines for anomaly detection
  3. OperatorIncidentReviewPack — Post-bad-day incident analysis

CLI:
  python3 -m python_brain.ouroboros.research_store --generate-baselines
  python3 -m python_brain.ouroboros.research_store --show-context
  python3 -m python_brain.ouroboros.research_store --list-incidents

All data under /data/research/. stdlib only.
"""
from __future__ import annotations

import argparse, json, logging, os, statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("ouroboros.research_store")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
RESEARCH_DIR = DATA_DIR / "research"
INCIDENTS_DIR = RESEARCH_DIR / "incidents"
CONTEXT_FILE = RESEARCH_DIR / "context_store.json"
BASELINES_FILE = RESEARCH_DIR / "anomaly_baselines.json"


def _safe_mean(vals: List[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _atomic_json_write(path: Path, data: Any):
    """Write JSON atomically via tmp+rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.rename(str(tmp), str(path))
    except OSError as e:
        log.error("Failed to write %s: %s", path, e)
        if tmp.exists():
            tmp.unlink()


def _safe_json_load(path: Path, default: Any = None) -> Any:
    """Load JSON with graceful fallback."""
    if not path.exists():
        return default() if callable(default) else (default if default is not None else {})
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to load %s: %s", path, e)
        return default() if callable(default) else (default if default is not None else {})


# ---------------------------------------------------------------------------
@dataclass
class AnomalyAlert:
    """A single anomaly detected by comparing today vs baselines."""
    metric: str
    today_value: float
    baseline_mean: float
    baseline_std: float
    z_score: float
    severity: str  # "warning" if |z|>2, "critical" if |z|>3

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================================
# Part 1: ResearchContextStore
# ============================================================================
class ResearchContextStore:
    """Rolling knowledge base of daily analysis for Claude nightly review."""

    def __init__(self, data_dir: Optional[Path] = None):
        self._file = (data_dir or RESEARCH_DIR) / "context_store.json"
        self._data: Dict[str, Any] = _safe_json_load(
            self._file, default=lambda: {"days": {}, "open_concerns": []})
        self._data.setdefault("days", {})
        self._data.setdefault("open_concerns", [])

    def load(self):
        self._data = _safe_json_load(
            self._file, default=lambda: {"days": {}, "open_concerns": []})

    def save(self):
        _atomic_json_write(self._file, self._data)

    def add_daily_context(self, date_str: str, metrics_dict: Dict, recommendations_dict: Dict,
                          scoreboard_dict: Dict, missed_winner_dict: Dict):
        """Save one day's analysis results."""
        m, r, s, mw = metrics_dict, recommendations_dict, scoreboard_dict, missed_winner_dict
        entry = {
            "date": date_str,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "metrics": {k: m.get(k, 0) for k in
                        ("total_trades", "wins", "losses", "total_pnl", "win_rate",
                         "profit_factor", "avg_rung")},
            "recommendations": {
                "kelly_fraction": r.get("kelly_fraction"),
                "chandelier_atr_mult": r.get("chandelier_atr_mult"),
                "adjustments": r.get("adjustments", []),
            },
            "scoreboard": {k: s.get(k, []) for k in ("promotes", "demotes", "kills", "holds")},
            "missed_winners": {k: mw.get(k, 0) for k in
                               ("total_rejected", "total_missed_winners", "missed_winner_rate")},
        }
        entry["metrics"]["per_ticker"] = m.get("per_ticker", {})
        entry["missed_winners"]["worst_gates"] = mw.get("worst_gates", [])

        self._data["days"][date_str] = entry
        # Prune to 60 days
        dates = sorted(self._data["days"])
        for old in dates[:-60]:
            del self._data["days"][old]
        self._auto_flag_concerns(date_str, entry)
        self.save()
        log.info("Research context saved for %s", date_str)

    def _auto_flag_concerns(self, date_str: str, e: Dict):
        m = e.get("metrics", {})
        mw = e.get("missed_winners", {})
        concerns = self._data["open_concerns"]
        if m.get("win_rate", 1) < 0.30 and m.get("total_trades", 0) >= 3:
            concerns.append({"date": date_str, "category": "performance",
                             "issue": f"Win rate critically low: {m['win_rate']:.0%} on {m['total_trades']} trades"})
        if m.get("total_pnl", 0) < -50:
            concerns.append({"date": date_str, "category": "risk",
                             "issue": f"Large daily loss: GBP {m['total_pnl']:.2f}"})
        if mw.get("missed_winner_rate", 0) > 30:
            concerns.append({"date": date_str, "category": "gate_tuning",
                             "issue": f"High missed-winner rate: {mw['missed_winner_rate']:.1f}%"})
        kills = e.get("scoreboard", {}).get("kills", [])
        if kills:
            concerns.append({"date": date_str, "category": "ticker_health",
                             "issue": f"Tickers in KILL zone: {', '.join(kills)}"})
        cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
        self._data["open_concerns"] = [c for c in concerns if c.get("date", "9999") >= cutoff]

    def get_context_for_claude(self, lookback_days: int = 7) -> Dict[str, Any]:
        """Structured summary of last N days for Claude."""
        all_dates = sorted(self._data["days"])
        recent_dates = all_dates[-lookback_days:]
        entries = [self._data["days"][d] for d in recent_dates]
        if not entries:
            return {"lookback_days": lookback_days, "actual_days": 0,
                    "recent_performance": {}, "trending_tickers": {"improving": [], "declining": []},
                    "parameter_drift": {}, "gate_veto_trend": {},
                    "open_concerns": self._data.get("open_concerns", []), "scoreboard_changes": []}

        # recent_performance
        wrs = [e["metrics"]["win_rate"] for e in entries if e["metrics"].get("total_trades", 0) > 0]
        pfs = [e["metrics"]["profit_factor"] for e in entries if e["metrics"].get("total_trades", 0) > 0]
        rp = {
            "win_rate_7d": _safe_mean(wrs),
            "pf_7d": _safe_mean(pfs),
            "total_pnl_7d": sum(e["metrics"].get("total_pnl", 0) for e in entries),
            "trade_count_7d": sum(e["metrics"].get("total_trades", 0) for e in entries),
        }

        # parameter_drift
        pd = {}
        for key, field in [("kelly", "kelly_fraction"), ("chandelier", "chandelier_atr_mult")]:
            vals = [e["recommendations"][field] for e in entries if e["recommendations"].get(field) is not None]
            if len(vals) >= 2:
                trend = "increasing" if vals[-1] > vals[0] else ("decreasing" if vals[-1] < vals[0] else "stable")
                pd[f"{key}_trend"] = trend
                pd[f"{key}_start"], pd[f"{key}_end"] = vals[0], vals[-1]

        # gate_veto_trend
        tv = sum(e["missed_winners"].get("total_rejected", 0) for e in entries)
        tm = sum(e["missed_winners"].get("total_missed_winners", 0) for e in entries)
        gv = {"total_vetoes_7d": tv, "total_missed_winners_7d": tm,
              "missed_winner_rate": (tm / tv * 100) if tv > 0 else 0.0}

        return {
            "lookback_days": lookback_days, "actual_days": len(entries),
            "date_range": {"from": recent_dates[0], "to": recent_dates[-1]} if recent_dates else {},
            "recent_performance": rp,
            "trending_tickers": dict(zip(["improving", "declining"], self._ticker_trends(entries))),
            "parameter_drift": pd,
            "gate_veto_trend": gv,
            "open_concerns": self._data.get("open_concerns", []),
            "scoreboard_changes": self._scoreboard_changes(entries),
        }

    def _ticker_trends(self, entries: List[Dict]) -> tuple:
        if len(entries) < 2:
            return [], []
        mid = max(1, len(entries) // 2)

        def _agg_wr(elist):
            agg: Dict[str, List[float]] = {}
            for e in elist:
                for t, td in e["metrics"].get("per_ticker", {}).items():
                    agg.setdefault(t, []).append(td.get("win_rate", 0))
            return {t: _safe_mean(v) for t, v in agg.items()}

        w1, w2 = _agg_wr(entries[:mid]), _agg_wr(entries[mid:])
        imp, dec = [], []
        for t in sorted(set(w1) | set(w2)):
            d = w2.get(t, 0) - w1.get(t, 0)
            if d > 0.10:
                imp.append({"ticker": t, "wr_delta": round(d, 3)})
            elif d < -0.10:
                dec.append({"ticker": t, "wr_delta": round(d, 3)})
        imp.sort(key=lambda x: -x["wr_delta"])
        dec.sort(key=lambda x: x["wr_delta"])
        return imp[:10], dec[:10]

    def _scoreboard_changes(self, entries: List[Dict]) -> List[Dict]:
        if len(entries) < 2:
            return []

        def _cls_map(e):
            sb, m = e.get("scoreboard", {}), {}
            for cat, label in [("promotes", "PROMOTE"), ("holds", "HOLD"),
                                ("demotes", "DEMOTE"), ("kills", "KILL")]:
                for t in sb.get(cat, []):
                    m[t] = label
            return m

        f, l = _cls_map(entries[0]), _cls_map(entries[-1])
        return [{"ticker": t, "from": f.get(t, "NEW"), "to": l.get(t, "REMOVED")}
                for t in sorted(set(f) | set(l)) if f.get(t, "NEW") != l.get(t, "REMOVED")]


# ============================================================================
# Part 2: AnomalyBaselineLibrary
# ============================================================================
class AnomalyBaselineLibrary:
    """Statistical baselines for anomaly detection (rolling 30-day window)."""

    TRACKED = ["win_rate", "total_pnl", "total_trades", "avg_spread",
               "avg_rung", "total_commission", "profit_factor"]

    def __init__(self, data_file: Optional[Path] = None):
        self._file = data_file or BASELINES_FILE
        self._data = _safe_json_load(self._file, default=lambda: {"history": [], "baselines": {}})
        self._data.setdefault("history", [])
        self._data.setdefault("baselines", {})

    def load(self):
        self._data = _safe_json_load(self._file, default=lambda: {"history": [], "baselines": {}})

    def save(self):
        _atomic_json_write(self._file, self._data)

    def update_baselines(self, date_str: str, metrics: Dict[str, Any]):
        """Add today's metrics and recompute baselines."""
        rec = {"date": date_str}
        for k in self.TRACKED:
            rec[k] = metrics.get(k, 0.0)
        self._data["history"] = [h for h in self._data["history"] if h.get("date") != date_str]
        self._data["history"].append(rec)
        self._data["history"] = self._data["history"][-30:]
        self._recompute()
        self.save()
        log.info("Baselines updated for %s (%d days)", date_str, len(self._data["history"]))

    def _recompute(self):
        hist = self._data["history"]
        bl = {}
        for m in self.TRACKED:
            vals = [h.get(m, 0.0) for h in hist]
            if len(vals) >= 2:
                bl[m] = {"mean": statistics.mean(vals), "std": statistics.stdev(vals),
                         "min": min(vals), "max": max(vals), "n": len(vals)}
            elif vals:
                bl[m] = {"mean": vals[0], "std": 0.0, "min": vals[0], "max": vals[0], "n": 1}
        self._data["baselines"] = bl

    def get_baselines(self) -> Dict[str, Any]:
        return dict(self._data.get("baselines", {}))

    def check_anomalies(self, today_metrics: Dict[str, Any]) -> List[AnomalyAlert]:
        """Compare today against baselines. Returns list of AnomalyAlert."""
        bl = self._data.get("baselines", {})
        if not bl:
            return []
        alerts: List[AnomalyAlert] = []
        # (metric, direction, z_threshold): "below" = anomaly when today < mean-z*std
        rules = [("win_rate", "below", 2.0), ("total_pnl", "below", 2.0),
                 ("total_trades", "above", 2.0), ("avg_spread", "above", 2.0),
                 ("avg_rung", "below", 1.0), ("total_commission", "above", 2.0),
                 ("profit_factor", "below", 2.0)]
        for metric, direction, z_thresh in rules:
            b = bl.get(metric)
            if not b or b.get("n", 0) < 3 or b["std"] < 1e-9:
                continue
            today_val = today_metrics.get(metric, 0.0)
            z = ((b["mean"] - today_val) if direction == "below" else (today_val - b["mean"])) / b["std"]
            if z >= z_thresh:
                sev = "critical" if abs(z) > 3.0 else "warning"
                alerts.append(AnomalyAlert(metric=metric, today_value=round(today_val, 4),
                    baseline_mean=round(b["mean"], 4), baseline_std=round(b["std"], 4),
                    z_score=round(z, 2), severity=sev))
                log.warning("ANOMALY [%s] %s: today=%.4f, mean=%.4f +/-%.4f, z=%.2f",
                            sev.upper(), metric, today_val, b["mean"], b["std"], z)
        return alerts


# ============================================================================
# Part 3: OperatorIncidentReviewPack
# ============================================================================
def generate_incident_pack(date_str: str, trades: List[Dict], metrics: Dict,
                           anomalies: List[AnomalyAlert], missed_winners: Dict) -> Dict:
    """Generate structured incident review. Writes to incidents/ and returns dict."""
    total_pnl = metrics.get("total_pnl", 0.0)
    has_crit = any(a.severity == "critical" for a in anomalies)
    severity = "critical" if has_crit or total_pnl < -100 else "warning"

    losers = []
    for t in trades:
        pnl = t.get("pnl", t.get("final_pnl", 0.0))
        if pnl < 0:
            losers.append({
                "symbol": t.get("symbol", t.get("ticker", "?")),
                "pnl": round(pnl, 2),
                "entry_time": t.get("entry_time", t.get("entry_time_ns", "")),
                "hold_time_mins": t.get("hold_time_mins", 0),
                "trade_class": t.get("trade_class", "unknown"),
                "confidence": t.get("confidence", 0.0),
            })
    losers.sort(key=lambda x: x["pnl"])

    causes = _root_causes(trades, losers, metrics)
    actions = _recommend(causes, anomalies)
    n = metrics.get("total_trades", 0)
    wr = metrics.get("win_rate", 0.0)
    summary = f"{severity.upper()} day: {n} trades, WR={wr:.0%}, PnL=GBP {total_pnl:+.2f}, {len(losers)} losers, {len(anomalies)} anomalies"

    pack = {"date": date_str, "severity": severity, "summary": summary,
            "total_pnl": round(total_pnl, 2), "losing_trades": losers,
            "root_cause_candidates": causes, "recommended_actions": actions,
            "anomalies_detected": [a.to_dict() for a in anomalies],
            "missed_winners_today": missed_winners.get("total_missed_winners", 0),
            "generated_at": datetime.now(timezone.utc).isoformat()}

    INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = INCIDENTS_DIR / f"incident_{date_str}.json"
    try:
        with open(path, "w") as f:
            json.dump(pack, f, indent=2, default=str)
        log.info("Incident pack written: %s", path)
    except OSError as e:
        log.error("Failed to write incident pack: %s", e)
    return pack


def _root_causes(all_trades: List[Dict], losers: List[Dict], metrics: Dict) -> List[str]:
    """Identify probable root causes from trade patterns."""
    if not losers:
        return []
    causes, n_los = [], len(losers)

    def _count_class(cls):
        return sum(1 for t in losers if t.get("trade_class") == cls)

    sv = _count_class("spread_victim")
    if sv >= 2 or (n_los and sv / n_los > 0.40):
        causes.append(f"Spread costs exceeded edge: {sv}/{n_los} losers are spread_victim")

    sh = _count_class("stop_hunt")
    if sh >= 2:
        syms = list({t["symbol"] for t in losers if t.get("trade_class") == "stop_hunt"})
        causes.append(f"Stop hunts on leveraged ETPs: {sh} trades on {', '.join(syms[:5])}")

    low_c = [t for t in losers if t.get("confidence", 1) < 0.65]
    if len(low_c) >= 2:
        causes.append(f"Quality gate too permissive: {len(low_c)} losers had confidence < 65%")

    # Concentration risk
    by_sym: Dict[str, float] = {}
    for t in losers:
        by_sym[t["symbol"]] = by_sym.get(t["symbol"], 0) + t["pnl"]
    total_loss = sum(t["pnl"] for t in losers)
    if by_sym and total_loss < 0:
        worst = min(by_sym, key=by_sym.get)
        if abs(by_sym[worst]) > abs(total_loss) * 0.50:
            causes.append(f"Concentration risk: {worst} = GBP {by_sym[worst]:+.2f} ({abs(by_sym[worst])/abs(total_loss):.0%} of losses)")

    hv = [t for t in losers if t.get("vix_at_entry", 0) > 25]
    if len(hv) >= 2:
        causes.append(f"Entered during elevated volatility: {len(hv)} losers had VIX > 25")

    ne = _count_class("noise_exit")
    if ne >= 3:
        causes.append(f"Noise exits dominating: {ne} noise_exit trades")

    ar = _safe_mean([t.get("rung_achieved", t.get("highest_rung", 0)) for t in losers])
    if ar < 1.0 and n_los >= 3:
        causes.append(f"Exits too early: avg rung={ar:.1f} — Chandelier may be too tight")

    return causes or ["No dominant pattern identified — review individual trades"]


def _recommend(causes: List[str], anomalies: List[AnomalyAlert]) -> List[str]:
    """Generate remediation actions from root causes and anomalies."""
    actions = []
    cause_map = {
        "spread": "Tighten spread filter or add spread_ceiling_pct gate",
        "stop hunt": "Widen Chandelier ATR +0.1 for leveraged ETPs",
        "quality gate": "Raise confidence_floor to 70%",
        "concentration": "Enable per-ticker max_daily_loss limit",
        "volatility": "Add VIX threshold gate: skip entries when VIX > 25",
        "noise exit": "Increase minimum hold time or widen initial stop",
        "exits too early": "Widen Chandelier ATR +0.1 to let trades breathe",
    }
    for cause in causes:
        cl = cause.lower()
        for key, action in cause_map.items():
            if key in cl:
                actions.append(action)

    for a in anomalies:
        if a.metric == "total_trades" and a.severity == "critical":
            actions.append("CRITICAL: Overtrading — investigate signal generation rate")
        if a.metric == "total_commission":
            actions.append("Commission drag anomaly — check for churning")

    if not actions:
        actions.append("Manual review recommended")
    return list(dict.fromkeys(actions))  # dedupe preserving order


def list_incidents(last_n_days: int = 30) -> List[Dict]:
    """Return incident summaries from the last N days."""
    if not INCIDENTS_DIR.exists():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=last_n_days)).strftime("%Y-%m-%d")
    incidents = []
    for p in sorted(INCIDENTS_DIR.glob("incident_*.json")):
        date_part = p.stem.replace("incident_", "")
        if date_part < cutoff:
            continue
        data = _safe_json_load(p)
        if data:
            incidents.append({
                "date": data.get("date", date_part), "severity": data.get("severity", "?"),
                "summary": data.get("summary", ""), "total_pnl": data.get("total_pnl", 0),
                "losing_trades_count": len(data.get("losing_trades", [])),
                "anomalies_count": len(data.get("anomalies_detected", [])),
                "root_causes": data.get("root_cause_candidates", []), "file": str(p),
            })
    return incidents


# ============================================================================
# CLI
# ============================================================================
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [research_store] %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Ouroboros Research Store")
    p.add_argument("--generate-baselines", action="store_true", help="Rebuild from ouroboros reports")
    p.add_argument("--show-context", action="store_true", help="Print Claude context (last 7 days)")
    p.add_argument("--list-incidents", action="store_true", help="List incident packs (last 30 days)")
    p.add_argument("--lookback", type=int, default=7, help="Lookback days for --show-context")
    args = p.parse_args()

    if args.generate_baselines:
        reports_dir = DATA_DIR / "ouroboros_reports"
        if not reports_dir.exists():
            print(f"No reports directory at {reports_dir}"); return
        lib = AnomalyBaselineLibrary()
        count = 0
        for f in sorted(reports_dir.glob("*_metrics.json"))[-30:]:
            data = _safe_json_load(f)
            if data:
                lib.update_baselines(data.get("date", f.stem.replace("_metrics", "")), data)
                count += 1
        print(f"\nBaselines rebuilt from {count} reports -> {BASELINES_FILE}")
        for m, bl in sorted(lib.get_baselines().items()):
            print(f"  {m:<20s}  mean={bl['mean']:.4f}  std={bl['std']:.4f}  n={bl['n']}")

    elif args.show_context:
        ctx = ResearchContextStore().get_context_for_claude(args.lookback)
        print(f"\n{'='*60}\n  CLAUDE RESEARCH CONTEXT (last {args.lookback} days)\n{'='*60}")
        if ctx["actual_days"] == 0:
            print("  No data yet."); return
        dr = ctx.get("date_range", {})
        print(f"  Range: {dr.get('from','?')} to {dr.get('to','?')} ({ctx['actual_days']}d)")
        rp = ctx["recent_performance"]
        print(f"  WR={rp.get('win_rate_7d',0):.1%}  PF={rp.get('pf_7d',0):.2f}  "
              f"PnL=GBP {rp.get('total_pnl_7d',0):+.2f}  trades={rp.get('trade_count_7d',0)}")
        for label, tickers in [("IMPROVING", ctx["trending_tickers"]["improving"]),
                                ("DECLINING", ctx["trending_tickers"]["declining"])]:
            if tickers:
                print(f"  {label}: " + ", ".join(f"{t['ticker']}({t['wr_delta']:+.1%})" for t in tickers))
        pd = ctx["parameter_drift"]
        if pd:
            for k in ("kelly", "chandelier"):
                if f"{k}_trend" in pd:
                    print(f"  {k}: {pd[f'{k}_trend']} ({pd.get(f'{k}_start','?')}->{pd.get(f'{k}_end','?')})")
        gv = ctx["gate_veto_trend"]
        print(f"  Vetoes: {gv['total_vetoes_7d']}  missed_winners: {gv['total_missed_winners_7d']}  rate: {gv['missed_winner_rate']:.1f}%")
        for c in ctx.get("scoreboard_changes", []):
            print(f"  SCOREBOARD: {c['ticker']} {c['from']}->{c['to']}")
        for c in ctx.get("open_concerns", [])[-10:]:
            print(f"  CONCERN [{c.get('date','?')}] [{c.get('category','?')}] {c.get('issue','')}")

    elif args.list_incidents:
        incidents = list_incidents(30)
        print(f"\n{'='*60}\n  INCIDENTS (last 30 days): {len(incidents)}\n{'='*60}")
        for inc in incidents:
            tag = "CRIT" if inc["severity"] == "critical" else "WARN"
            print(f"  [{tag}] {inc['date']} PnL=GBP {inc['total_pnl']:+.2f} | {inc['summary']}")
            for rc in inc.get("root_causes", [])[:2]:
                print(f"    -> {rc}")
    else:
        p.print_help()


if __name__ == "__main__":
    main()
