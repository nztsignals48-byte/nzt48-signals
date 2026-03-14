"""
command_center/copilot/handlers.py
===================================
Intent handlers for the NZT-48 Operator Copilot.

Each handler inspects system state, gate reports, and artifacts to
produce a structured response dict. Handlers NEVER place orders.

Every handler returns:
    {
        "answer":       str,           # Human-readable answer
        "actions":      list[dict],    # Advisory actions (NEVER order placement)
        "evidence":     list[dict],    # Artifact paths + run_id/as_of
        "warnings":     list[str],     # Safety warnings
        "as_of":        str,           # ISO timestamp
        "system_state": str,           # OK / DEGRADED / HALTED
        "regime":       str,           # Current regime tag
        "confidence":   str,           # A / B / C
    }

SAFETY RULES (applied in every handler):
    - NEVER suggest placing orders — only "consider" or "monitor"
    - If data is missing, say "DATA MISSING" and reference the missing artifact
    - Always include as_of timestamp
    - Never fabricate data — if state is None, say "System state unavailable"
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from command_center.copilot.intents import Intent
from command_center.copilot.evidence import collect_evidence

# ---------------------------------------------------------------------------
# Conditional imports — degrade gracefully if modules unavailable
# ---------------------------------------------------------------------------

try:
    from command_center.state import get_state
except ImportError:
    get_state = None  # type: ignore[assignment]

try:
    from signal_engine.pipeline_runner import run_pipeline, run_tiered_pipeline
except ImportError:
    run_pipeline = None           # type: ignore[assignment]
    run_tiered_pipeline = None    # type: ignore[assignment]

try:
    from signal_engine.scoring import PlayScore
except ImportError:
    PlayScore = None  # type: ignore[assignment]

try:
    from signal_engine.gates import TickerGateReport, GateOutcome
except ImportError:
    TickerGateReport = None  # type: ignore[assignment]
    GateOutcome = None       # type: ignore[assignment]


logger = logging.getLogger("nzt48.copilot.handlers")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """UTC now as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _safe_state():
    """Get system state safely. Returns None if unavailable."""
    if get_state is None:
        return None
    try:
        return get_state()
    except Exception as exc:
        logger.warning("Failed to get system state: %s", exc)
        return None


def _determine_system_status(state) -> str:
    """Derive system status badge from state panels."""
    if state is None:
        return "DEGRADED"
    try:
        if getattr(state, "halt_new_signals", False):
            return "HALTED"
        badge = getattr(getattr(state, "data_health", None), "badge", "UNKNOWN")
        if badge == "RED":
            return "DEGRADED"
        if badge == "AMBER":
            return "DEGRADED"
        return "OK"
    except Exception:
        return "DEGRADED"


def _determine_regime(state) -> str:
    """Extract current regime tag from state."""
    if state is None:
        return "UNKNOWN"
    try:
        return getattr(getattr(state, "strategies", None), "regime_tag", "UNKNOWN") or "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def _determine_confidence(state) -> str:
    """Determine copilot confidence grade.

    A = DataHealth GREEN + regime confidence >= 0.6
    B = DataHealth AMBER or regime confidence < 0.6
    C = DataHealth RED or missing data
    """
    if state is None:
        return "C"
    try:
        badge = getattr(getattr(state, "data_health", None), "badge", "UNKNOWN")
        regime_conf = getattr(getattr(state, "strategies", None), "regime_confidence", 0.0)
        if badge == "GREEN" and regime_conf >= 0.6:
            return "A"
        elif badge == "RED" or badge == "UNKNOWN":
            return "C"
        else:
            return "B"
    except Exception:
        return "C"


def _base_response(state=None) -> dict:
    """Build a base response dict with common fields populated."""
    return {
        "answer": "",
        "actions": [],
        "evidence": [],
        "warnings": [],
        "as_of": _now_iso(),
        "system_state": _determine_system_status(state),
        "regime": _determine_regime(state),
        "confidence": _determine_confidence(state),
    }


def _play_to_dict(play) -> dict:
    """Convert a PlayScore (or dict) to a standardised dict for response."""
    if isinstance(play, dict):
        return {
            "ticker": play.get("ticker", "?"),
            "direction": play.get("direction", "?"),
            "stars": play.get("stars", play.get("stars_str", "?")),
            "composite_score": play.get("score", play.get("composite", 0)),
            "entry": play.get("entry", 0),
            "stop": play.get("stop", 0),
            "target1": play.get("target1", 0),
            "target2": play.get("target2", 0),
            "rr_ratio": play.get("rr", play.get("rr_ratio", 0)),
            "setup_type": play.get("setup_type", ""),
            "track": play.get("track", "INTRADAY_SWING"),
            "strategy_tag": play.get("strategy_tag", ""),
            "factor_group": play.get("factor_group", ""),
            "reasons": play.get("reasons", []),
            "label": play.get("label", ""),
        }
    # PlayScore object
    return {
        "ticker": getattr(play, "ticker", "?"),
        "direction": getattr(play, "direction", "?"),
        "stars": getattr(play, "stars_str", getattr(play, "stars", "?")),
        "composite_score": getattr(play, "composite", 0),
        "entry": getattr(play, "entry", 0),
        "stop": getattr(play, "stop", 0),
        "target1": getattr(play, "target1", 0),
        "target2": getattr(play, "target2", 0),
        "rr_ratio": getattr(play, "rr_ratio", 0),
        "setup_type": getattr(play, "setup_type", ""),
        "track": getattr(play, "track", "INTRADAY_SWING"),
        "strategy_tag": getattr(play, "strategy_tag", ""),
        "factor_group": getattr(play, "factor_group", ""),
        "reasons": list(getattr(play, "reasons", [])),
        "label": getattr(play, "label", ""),
    }


def _format_play_text(p: dict) -> str:
    """Format a single play dict into human-readable text."""
    lines = [
        f"  {p['ticker']} {p['direction']} {p['stars']}  "
        f"Score: {p['composite_score']}  R:R {p['rr_ratio']:.1f}",
        f"    Entry: {p['entry']:.2f}  Stop: {p['stop']:.2f}  "
        f"T1: {p['target1']:.2f}  T2: {p['target2']:.2f}",
        f"    Setup: {p['setup_type']}  Track: {p['track']}",
    ]
    if p.get("reasons"):
        lines.append(f"    Reasons: {'; '.join(p['reasons'][:3])}")
    lines.append(
        f"    Execution: enter at {p['entry']:.2f}, "
        f"stop at {p['stop']:.2f}, target {p['target1']:.2f}"
    )
    lines.append(f"    Kill condition: stop hit or regime flip")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Intent Handlers
# ---------------------------------------------------------------------------

def handle_scan_now(params: dict) -> dict:
    """Handle SCAN_NOW: run an on-demand pipeline preview scan.

    Checks throttle, runs the pipeline in preview mode, and separates
    results into TRADE (strict, score >= 75) and WATCH (fallback, < 75).
    """
    state = _safe_state()
    resp = _base_response(state)
    resp["evidence"] = collect_evidence(Intent.SCAN_NOW, params, state)

    # --- Throttle check ---
    throttle = params.get("throttle")
    if throttle is not None:
        allowed, reason = throttle.can_scan()
        if not allowed:
            resp["answer"] = f"Rate limited. {reason}"
            resp["warnings"].append("Scan throttled — try again shortly")
            return resp

    # --- Check pipeline availability ---
    if run_pipeline is None:
        resp["answer"] = (
            "Pipeline runner is not available in this deployment. "
            "Cannot execute on-demand scan."
        )
        resp["system_state"] = "DEGRADED"
        resp["warnings"].append("DATA MISSING: signal_engine.pipeline_runner not importable")
        return resp

    # --- Run pipeline ---
    lane = params.get("lane", "CORE")
    max_results = params.get("max_results", 10)

    try:
        pipeline_result = run_pipeline(
            session="COPILOT_SCAN",
            period="5d",
            is_preview=True,
        )

        # Record scan in throttle
        if throttle is not None:
            throttle.record_scan()

        engine_result = pipeline_result.engine_result
        if engine_result is None:
            resp["answer"] = "Pipeline ran but returned no engine result."
            resp["warnings"].append("DATA MISSING: engine_result is None")
            return resp

        # Extract plays
        plays = getattr(engine_result, "plays", []) or []

        # Separate into TRADE and WATCH
        trade_plays = []
        watch_plays = []
        for play in plays:
            score = getattr(play, "composite", 0) if not isinstance(play, dict) else play.get("score", play.get("composite", 0))
            pd_dict = _play_to_dict(play)
            if score >= 75:
                pd_dict["classification"] = "TRADE"
                trade_plays.append(pd_dict)
            else:
                pd_dict["classification"] = "WATCH"
                watch_plays.append(pd_dict)

        # Apply lane filter
        if lane != "ALL":
            # Core plays = strict (label = STRICT), Opportunity = fallback
            if lane == "CORE":
                trade_plays = [p for p in trade_plays if p.get("label") == "STRICT"]
                watch_plays = [p for p in watch_plays if p.get("label") == "STRICT"]

        # Limit results
        trade_plays = trade_plays[:max_results]
        watch_plays = watch_plays[:max_results]

        # Build answer
        lines = [f"COPILOT SCAN COMPLETE (lane={lane})"]
        lines.append(f"Run ID: {pipeline_result.run_id}")
        lines.append(f"Regime: {resp['regime']}  |  Data Health: {resp['system_state']}")
        lines.append("")

        if trade_plays:
            lines.append(f"=== TRADE ({len(trade_plays)} qualifying) ===")
            for p in trade_plays:
                lines.append(_format_play_text(p))
                lines.append("")
        else:
            lines.append("=== NO TRADE-GRADE SIGNALS ===")

        if watch_plays:
            lines.append(f"=== WATCH ({len(watch_plays)} monitor) ===")
            for p in watch_plays[:5]:
                lines.append(_format_play_text(p))
                lines.append("")

        # Drought diagnostics if no plays at all
        if not trade_plays and not watch_plays:
            drought = getattr(engine_result, "drought", None)
            if drought:
                lines.append("--- DROUGHT DIAGNOSTICS ---")
                lines.append(f"Tickers checked: {getattr(drought, 'tickers_checked', '?')}")
                for blocker in getattr(drought, "top_blockers", [])[:5]:
                    lines.append(f"  Blocker: {blocker}")

            # Include closest misses from drought cockpit
            if state and hasattr(state, "drought_cockpit"):
                dc = state.drought_cockpit
                misses = getattr(dc, "closest_misses", [])
                if misses:
                    lines.append("")
                    lines.append("--- CLOSEST MISSES ---")
                    for m in misses[:5]:
                        if isinstance(m, dict):
                            lines.append(
                                f"  {m.get('ticker', '?')}: "
                                f"failed {m.get('failed_gate', '?')} "
                                f"(value={m.get('value', '?')} vs "
                                f"threshold={m.get('threshold', '?')}, "
                                f"delta={m.get('delta', '?')})"
                            )

        resp["answer"] = "\n".join(lines)

        # Actions — advisory only, NEVER order placement
        for p in trade_plays:
            resp["actions"].append({
                "type": "CONSIDER_ENTRY",
                "ticker": p["ticker"],
                "direction": p["direction"],
                "entry": p["entry"],
                "stop": p["stop"],
                "target": p["target1"],
                "note": f"Consider monitoring {p['ticker']} for entry at {p['entry']:.2f}",
            })

        # Confidence adjustment
        resp["confidence"] = _determine_confidence(state)

    except Exception as exc:
        logger.exception("Copilot scan failed: %s", exc)
        resp["answer"] = f"Scan failed with error: {exc}"
        resp["warnings"].append(f"Pipeline error: {exc}")
        resp["system_state"] = "DEGRADED"

    return resp


def handle_explain_signal(params: dict) -> dict:
    """Handle EXPLAIN_SIGNAL: deep-dive on a specific ticker's signal."""
    state = _safe_state()
    resp = _base_response(state)
    resp["evidence"] = collect_evidence(Intent.EXPLAIN_SIGNAL, params, state)

    ticker = params.get("ticker")
    if not ticker:
        resp["answer"] = "No ticker specified. Usage: 'explain QQQ3.L' or 'break down NVD3.L'"
        return resp

    if state is None:
        resp["answer"] = "System state unavailable. Cannot look up signal details."
        resp["warnings"].append("DATA MISSING: get_state() returned None")
        return resp

    # Search in top_plays
    top_plays = getattr(state, "top_plays", []) or []
    found_play = None
    for play in top_plays:
        play_ticker = play.get("ticker") if isinstance(play, dict) else getattr(play, "ticker", None)
        if play_ticker and play_ticker.upper() == ticker.upper():
            found_play = play
            break

    if found_play is None:
        # Not in current plays — list what IS available
        available = []
        for play in top_plays[:10]:
            t = play.get("ticker") if isinstance(play, dict) else getattr(play, "ticker", None)
            if t:
                available.append(t)
        resp["answer"] = (
            f"{ticker} is not in the current plays.\n"
            f"Available tickers: {', '.join(available) if available else 'none (drought active?)'}\n"
            f"Try 'why not {ticker}' to see gate failures."
        )
        return resp

    # Build explanation
    p = _play_to_dict(found_play)
    lines = [f"=== SIGNAL BREAKDOWN: {ticker} ==="]
    lines.append(f"Direction: {p['direction']}  |  Stars: {p['stars']}  |  Score: {p['composite_score']}")
    lines.append(f"Label: {p['label']}  |  Track: {p['track']}  |  Setup: {p['setup_type']}")
    lines.append("")

    # Score components (if available from PlayScore object)
    if not isinstance(found_play, dict) and hasattr(found_play, "momentum"):
        lines.append("--- SCORE BREAKDOWN (6 components) ---")
        lines.append(f"  Momentum:     {getattr(found_play, 'momentum', 0):.2f} (weight 0.30)")
        lines.append(f"  Volatility:   {getattr(found_play, 'volatility', 0):.2f} (weight 0.20)")
        lines.append(f"  Regime Fit:   {getattr(found_play, 'regime_fit', 0):.2f} (weight 0.15)")
        lines.append(f"  Liquidity:    {getattr(found_play, 'liquidity', 0):.2f} (weight 0.15)")
        lines.append(f"  R:R Score:    {getattr(found_play, 'rr_score', 0):.2f} (weight 0.10)")
        lines.append(f"  Quality:      {getattr(found_play, 'quality', 0):.2f} (weight 0.10)")
        lines.append(f"  Composite:    {getattr(found_play, 'composite', 0):.1f}/100")
        lines.append("")

    # Trade levels
    lines.append("--- TRADE LEVELS ---")
    lines.append(f"  Entry:   {p['entry']:.4f}")
    lines.append(f"  Stop:    {p['stop']:.4f}")
    lines.append(f"  Target1: {p['target1']:.4f}")
    lines.append(f"  Target2: {p['target2']:.4f}")
    lines.append(f"  R:R:     {p['rr_ratio']:.2f}")
    lines.append("")

    # Execution plan
    lines.append("--- EXECUTION PLAN ---")
    lines.append(f"  Enter at {p['entry']:.4f}")
    lines.append(f"  Stop at {p['stop']:.4f}")
    lines.append(f"  Primary target: {p['target1']:.4f}")
    lines.append(f"  Runner target:  {p['target2']:.4f}")
    lines.append("")

    # Kill conditions
    lines.append("--- KILL CONDITIONS ---")
    lines.append(f"  1. Stop hit at {p['stop']:.4f}")
    lines.append(f"  2. Regime flip (current: {resp['regime']})")
    lines.append(f"  3. Data health degradation to RED")
    lines.append("")

    # Reasons
    if p.get("reasons"):
        lines.append("--- REASONS ---")
        for reason in p["reasons"]:
            lines.append(f"  - {reason}")
        lines.append("")

    # Risk metadata
    lines.append("--- RISK METADATA ---")
    lines.append(f"  Factor Group:  {p.get('factor_group', '?')}")
    atr_pct = found_play.get("atr_pct") if isinstance(found_play, dict) else getattr(found_play, "atr_pct", None)
    rvol = found_play.get("rvol") if isinstance(found_play, dict) else getattr(found_play, "rvol", None)
    if atr_pct is not None:
        lines.append(f"  ATR%:          {atr_pct:.2f}%")
    if rvol is not None:
        lines.append(f"  RVOL:          {rvol:.2f}")

    resp["answer"] = "\n".join(lines)

    # Advisory action
    resp["actions"].append({
        "type": "MONITOR",
        "ticker": ticker,
        "note": f"Consider monitoring {ticker} at entry {p['entry']:.4f}",
    })

    return resp


def handle_why_not_ticker(params: dict) -> dict:
    """Handle WHY_NOT_TICKER: explain why a ticker was rejected."""
    state = _safe_state()
    resp = _base_response(state)
    resp["evidence"] = collect_evidence(Intent.WHY_NOT_TICKER, params, state)

    ticker = params.get("ticker")
    if not ticker:
        resp["answer"] = "No ticker specified. Usage: 'why not QQQ3.L' or 'why was NVD3.L rejected'"
        return resp

    if state is None:
        resp["answer"] = "System state unavailable. Cannot look up gate reports."
        resp["warnings"].append("DATA MISSING: get_state() returned None")
        return resp

    # Check in gate_reports from last engine result
    engine_result = getattr(state, "last_engine_result", None)
    gate_reports = getattr(engine_result, "gate_reports", {}) if engine_result else {}

    gate_report = gate_reports.get(ticker) or gate_reports.get(ticker.upper())

    if gate_report is not None:
        lines = [f"=== GATE REPORT: {ticker} ==="]
        lines.append(f"Hard failed: {'YES' if getattr(gate_report, 'hard_failed', False) else 'NO'}")
        lines.append(f"Mode: {getattr(gate_report, 'mode', '?')}")
        if getattr(gate_report, "blocker", ""):
            lines.append(f"Primary blocker: {gate_report.blocker}")
        lines.append("")

        lines.append("--- GATE OUTCOMES ---")
        gates = getattr(gate_report, "gates", [])
        for gate in gates:
            gate_name = getattr(gate, "gate_name", "?")
            result = getattr(gate, "result", "?")
            value = getattr(gate, "value", 0)
            threshold = getattr(gate, "threshold", 0)
            reason = getattr(gate, "reason", "")
            fallback_step = getattr(gate, "fallback_step", 0)

            status_icon = "PASS" if str(result) in ("PASS", "GateResult.PASS") else (
                "RELAXED" if str(result) in ("RELAXED", "GateResult.RELAXED") else "FAIL"
            )
            line = f"  {gate_name}: {status_icon}"
            if value or threshold:
                line += f"  (value={value:.3f} vs threshold={threshold:.3f})"
            if reason:
                line += f"  -- {reason}"
            if fallback_step > 0:
                line += f"  [fallback step {fallback_step}]"
            lines.append(line)

        lines.append("")

        # Suggest what would fix it
        failed_gates = [g for g in gates if str(getattr(g, "result", "")) in ("FAIL", "GateResult.FAIL")]
        if failed_gates:
            lines.append("--- WHAT WOULD FIX IT ---")
            for fg in failed_gates:
                gn = getattr(fg, "gate_name", "?")
                val = getattr(fg, "value", 0)
                thresh = getattr(fg, "threshold", 0)
                if thresh > 0 and val > 0:
                    delta = thresh - val
                    lines.append(f"  {gn}: needs +{delta:.3f} (current {val:.3f}, required {thresh:.3f})")
                else:
                    lines.append(f"  {gn}: {getattr(fg, 'reason', 'check data quality')}")

        resp["answer"] = "\n".join(lines)

    else:
        # No gate report found — ticker may not be in universe
        lines = [f"No gate report found for {ticker}."]
        lines.append("")
        lines.append("Possible reasons:")
        lines.append("  1. Ticker is not in the current scanning universe")
        lines.append("  2. Ticker was excluded at data-health level (no OHLC data)")
        lines.append("  3. Ticker may be delisted or have stale yfinance data")
        lines.append("")

        # Show what IS in the universe / gate reports
        if gate_reports:
            available = list(gate_reports.keys())[:15]
            lines.append(f"Tickers with gate reports: {', '.join(available)}")
        else:
            lines.append("No gate reports available (engine may not have run yet)")

        resp["answer"] = "\n".join(lines)
        resp["warnings"].append(f"No gate report for {ticker} — may not be in universe")

    return resp


def handle_health_summary(params: dict) -> dict:
    """Handle HEALTH_SUMMARY: report system and data health status."""
    state = _safe_state()
    resp = _base_response(state)
    resp["evidence"] = collect_evidence(Intent.HEALTH_SUMMARY, params, state)

    if state is None:
        resp["answer"] = "System state unavailable. Cannot produce health summary."
        resp["warnings"].append("DATA MISSING: get_state() returned None")
        return resp

    lines = ["=== SYSTEM HEALTH SUMMARY ==="]
    lines.append(f"System State: {resp['system_state']}")
    lines.append(f"Regime: {resp['regime']}")
    lines.append(f"Copilot Confidence: {resp['confidence']}")
    lines.append("")

    # Data Health
    dh = getattr(state, "data_health", None)
    if dh:
        lines.append("--- DATA HEALTH ---")
        lines.append(f"  Badge:   {getattr(dh, 'badge', '?')}")
        lines.append(f"  Pass:    {getattr(dh, 'pass_count', 0)}")
        lines.append(f"  Warn:    {getattr(dh, 'warn_count', 0)}")
        lines.append(f"  Fail:    {getattr(dh, 'fail_count', 0)}")
        failed_tickers = getattr(dh, "failed_tickers", [])
        if failed_tickers:
            lines.append(f"  Failed:  {', '.join(failed_tickers)}")
        dh_warnings = getattr(dh, "warnings", [])
        if dh_warnings:
            for w in dh_warnings[:5]:
                lines.append(f"  Warning: {w}")
        lines.append("")

    # Market Overview
    market = getattr(state, "market", None)
    if market:
        lines.append("--- MARKET ---")
        lines.append(f"  Regime:   {getattr(market, 'regime', '?')}")
        lines.append(f"  Session:  {getattr(market, 'session_name', '?')}")
        lines.append(f"  Active:   {getattr(market, 'session_active', False)}")
        lines.append("")

    # Halt status
    halt = getattr(state, "halt_new_signals", False)
    if halt:
        lines.append("*** HALT ACTIVE — new signals blocked ***")
        lines.append("")
        resp["warnings"].append("HALT is active — no new signals will be generated")

    # Drought status
    drought = getattr(state, "drought", None)
    drought_cockpit = getattr(state, "drought_cockpit", None)
    if drought_cockpit and getattr(drought_cockpit, "drought_flag", False):
        lines.append("--- DROUGHT STATUS ---")
        lines.append(f"  Drought active: YES")
        lines.append(f"  Tickers checked: {getattr(drought_cockpit, 'tickers_checked', '?')}")
        blockers = getattr(drought_cockpit, "blockers_summary", [])
        if blockers:
            for b in blockers[:5]:
                lines.append(f"  Blocker: {b}")
        lines.append("")
    elif drought:
        lines.append("--- DROUGHT STATUS ---")
        drought_text = drought.to_text() if hasattr(drought, "to_text") else str(drought)
        lines.append(f"  {drought_text[:200]}")
        lines.append("")

    # Session status
    session_status = getattr(state, "session_status", None)
    if session_status:
        jobs = getattr(session_status, "jobs", [])
        if jobs:
            lines.append("--- SESSION JOBS ---")
            for job in jobs:
                sess = getattr(job, "session", "?")
                status = getattr(job, "status", "?")
                err = getattr(job, "error_msg", "")
                line = f"  {sess}: {status}"
                if err:
                    line += f" -- {err}"
                lines.append(line)
            lines.append("")

    # Tick metadata
    lines.append("--- TICK METADATA ---")
    lines.append(f"  Tick count: {getattr(state, 'tick_count', 0)}")
    last_tick = getattr(state, "last_tick", None)
    lines.append(f"  Last tick:  {last_tick.isoformat() if last_tick else 'never'}")
    lines.append(f"  Run ID:     {getattr(state, 'run_id', '?')}")

    resp["answer"] = "\n".join(lines)
    return resp


def handle_show_top_trades(params: dict) -> dict:
    """Handle SHOW_TOP_TRADES: list current top-ranked plays."""
    state = _safe_state()
    resp = _base_response(state)
    resp["evidence"] = collect_evidence(Intent.SHOW_TOP_TRADES, params, state)

    if state is None:
        resp["answer"] = "System state unavailable. Cannot retrieve plays."
        resp["warnings"].append("DATA MISSING: get_state() returned None")
        return resp

    top_plays = getattr(state, "top_plays", []) or []
    lane = params.get("lane", "ALL")
    track_filter = params.get("track")
    conf_filter = params.get("confidence")
    max_results = params.get("max_results", 10)

    # Convert to dicts
    plays = [_play_to_dict(p) for p in top_plays]

    # Apply lane filter
    if lane == "CORE":
        plays = [p for p in plays if p.get("label") == "STRICT"]
    elif lane == "OPPORTUNITY":
        plays = [p for p in plays if p.get("label") != "STRICT"]

    # Apply track filter
    if track_filter:
        track_upper = track_filter.upper()
        plays = [p for p in plays if track_upper in (p.get("track", "").upper())]

    # Apply confidence filter
    if conf_filter == "A":
        plays = [p for p in plays if p.get("composite_score", 0) >= 80]
    elif conf_filter == "B":
        plays = [p for p in plays if 60 <= p.get("composite_score", 0) < 80]
    elif conf_filter == "C":
        plays = [p for p in plays if p.get("composite_score", 0) < 60]

    # Limit
    plays = plays[:max_results]

    # Build answer
    lines = [f"=== TOP TRADES (lane={lane}, n={len(plays)}) ==="]
    lines.append(f"Regime: {resp['regime']}  |  System: {resp['system_state']}")
    lines.append("")

    if plays:
        for p in plays:
            lines.append(_format_play_text(p))
            lines.append("")

        # Advisory actions
        for p in plays:
            if p.get("composite_score", 0) >= 75:
                resp["actions"].append({
                    "type": "CONSIDER_ENTRY",
                    "ticker": p["ticker"],
                    "direction": p["direction"],
                    "entry": p["entry"],
                    "stop": p["stop"],
                    "target": p["target1"],
                    "note": f"Consider monitoring {p['ticker']} for entry near {p['entry']:.4f}",
                })
    else:
        lines.append("No plays match the current filters.")
        lines.append("Try broader filters or run a fresh scan.")

        # Check drought
        drought_cockpit = getattr(state, "drought_cockpit", None)
        if drought_cockpit and getattr(drought_cockpit, "drought_flag", False):
            lines.append("")
            lines.append("Drought is active. Use 'closest misses' for near-qualifying tickers.")

    resp["answer"] = "\n".join(lines)
    return resp


def handle_show_closest_misses(params: dict) -> dict:
    """Handle SHOW_CLOSEST_MISSES: tickers that almost qualified."""
    state = _safe_state()
    resp = _base_response(state)
    resp["evidence"] = collect_evidence(Intent.SHOW_CLOSEST_MISSES, params, state)

    if state is None:
        resp["answer"] = "System state unavailable. Cannot retrieve closest misses."
        resp["warnings"].append("DATA MISSING: get_state() returned None")
        return resp

    # Primary source: drought cockpit
    drought_cockpit = getattr(state, "drought_cockpit", None)
    misses = getattr(drought_cockpit, "closest_misses", []) if drought_cockpit else []

    # Fallback: gate funnel closest_misses
    if not misses:
        gate_funnel = getattr(state, "gate_funnel", None)
        misses = getattr(gate_funnel, "closest_misses", []) if gate_funnel else []

    max_results = params.get("max_results", 10)

    lines = [f"=== CLOSEST MISSES ({len(misses)} found) ==="]
    lines.append(f"Regime: {resp['regime']}  |  System: {resp['system_state']}")
    lines.append("")

    if misses:
        for m in misses[:max_results]:
            if isinstance(m, dict):
                ticker = m.get("ticker", "?")
                failed_gate = m.get("failed_gate", m.get("gate", "?"))
                value = m.get("value", "?")
                threshold = m.get("threshold", "?")
                delta = m.get("delta", m.get("gap", "?"))

                lines.append(f"  {ticker}:")
                lines.append(f"    Failed gate: {failed_gate}")
                lines.append(f"    Value: {value}  |  Threshold: {threshold}  |  Delta: {delta}")
                lines.append("")
            else:
                # Object with attributes
                ticker = getattr(m, "ticker", "?")
                failed_gate = getattr(m, "failed_gate", "?")
                value = getattr(m, "value", "?")
                threshold = getattr(m, "threshold", "?")
                delta = getattr(m, "delta", "?")

                lines.append(f"  {ticker}:")
                lines.append(f"    Failed gate: {failed_gate}")
                lines.append(f"    Value: {value}  |  Threshold: {threshold}  |  Delta: {delta}")
                lines.append("")

        # Advisory
        for m in misses[:3]:
            t = m.get("ticker", "?") if isinstance(m, dict) else getattr(m, "ticker", "?")
            resp["actions"].append({
                "type": "MONITOR_NEAR_MISS",
                "ticker": t,
                "note": f"Monitor {t} — close to qualifying",
            })
    else:
        lines.append("No closest misses available.")
        lines.append("This may mean: all tickers either qualified or failed hard gates by wide margins.")

    resp["answer"] = "\n".join(lines)
    return resp


def handle_what_changed(params: dict) -> dict:
    """Handle WHAT_CHANGED: report changes since last tick / scan cycle."""
    state = _safe_state()
    resp = _base_response(state)
    resp["evidence"] = collect_evidence(Intent.WHAT_CHANGED, params, state)

    if state is None:
        resp["answer"] = "System state unavailable. Cannot determine changes."
        resp["warnings"].append("DATA MISSING: get_state() returned None")
        return resp

    lines = ["=== WHAT CHANGED ==="]

    # Tick info
    tick_count = getattr(state, "tick_count", 0)
    last_tick = getattr(state, "last_tick", None)
    lines.append(f"Current tick: {tick_count}")
    lines.append(f"Last tick time: {last_tick.isoformat() if last_tick else 'never'}")
    lines.append(f"Run ID: {getattr(state, 'run_id', '?')}")
    lines.append("")

    # Current plays
    top_plays = getattr(state, "top_plays", []) or []
    lines.append(f"Active signals: {len(top_plays)}")
    if top_plays:
        for play in top_plays[:5]:
            t = play.get("ticker") if isinstance(play, dict) else getattr(play, "ticker", "?")
            s = play.get("score", play.get("composite", 0)) if isinstance(play, dict) else getattr(play, "composite", 0)
            lines.append(f"  - {t} (score: {s})")
    lines.append("")

    # Regime
    strategies = getattr(state, "strategies", None)
    if strategies:
        lines.append("--- REGIME ---")
        lines.append(f"  Tag: {getattr(strategies, 'regime_tag', '?')}")
        lines.append(f"  Confidence: {getattr(strategies, 'regime_confidence', 0):.2f}")
        lines.append(f"  Session window: {getattr(strategies, 'time_of_day_window', '?')}")
        lines.append(f"  Sizing mode: {getattr(strategies, 'sizing_mode', '?')}")
        kill_switch = getattr(strategies, "kill_switch", False)
        if kill_switch:
            lines.append("  *** KILL SWITCH ACTIVE ***")
        lines.append("")

    # Latest tape entries
    tape = getattr(state, "tape", None)
    if tape and hasattr(tape, "to_lines"):
        tape_lines = tape.to_lines(10)
        if tape_lines:
            lines.append("--- LATEST TAPE ENTRIES ---")
            for tl in tape_lines:
                lines.append(f"  {tl}")
            lines.append("")

    # Overlay warnings
    if strategies:
        overlay_warnings = getattr(strategies, "overlay_warnings", [])
        if overlay_warnings:
            lines.append("--- OVERLAY WARNINGS ---")
            for ow in overlay_warnings:
                lines.append(f"  {ow}")
            lines.append("")

    resp["answer"] = "\n".join(lines)
    return resp


def handle_regime_status(params: dict) -> dict:
    """Handle REGIME_STATUS: current market regime and strategy routing."""
    state = _safe_state()
    resp = _base_response(state)
    resp["evidence"] = collect_evidence(Intent.REGIME_STATUS, params, state)

    if state is None:
        resp["answer"] = "System state unavailable. Cannot determine regime."
        resp["warnings"].append("DATA MISSING: get_state() returned None")
        return resp

    lines = ["=== REGIME STATUS ==="]

    # Market overview
    market = getattr(state, "market", None)
    if market:
        lines.append(f"Market Regime: {getattr(market, 'regime', '?')}")
        lines.append(f"Regime Confidence: {getattr(market, 'regime_confidence', 0):.2f}")
        lines.append(f"Session: {getattr(market, 'session_name', '?')}")
        lines.append(f"Session Active: {getattr(market, 'session_active', False)}")
        lines.append(f"Breadth Score: {getattr(market, 'breadth_score', 0):.2f}")
        lines.append("")

    # Strategy routing
    strategies = getattr(state, "strategies", None)
    if strategies:
        lines.append("--- STRATEGY ROUTING ---")
        lines.append(f"  Regime Tag: {getattr(strategies, 'regime_tag', '?')}")
        lines.append(f"  Regime Confidence: {getattr(strategies, 'regime_confidence', 0):.2f}")
        lines.append(f"  Time Window: {getattr(strategies, 'time_of_day_window', '?')}")
        lines.append(f"  Sizing Mode: {getattr(strategies, 'sizing_mode', '?')}")
        lines.append(f"  Score Boost: {getattr(strategies, 'score_boost', 0):.3f}")
        lines.append(f"  Kill Switch: {getattr(strategies, 'kill_switch', False)}")
        lines.append(f"  Max Factor Cap: {getattr(strategies, 'max_factor_cap', 3)}")
        lines.append("")

        # Active strategies
        active = getattr(strategies, "active_strategies", [])
        if active:
            lines.append(f"  Active Strategies ({len(active)}):")
            for s in active:
                tag = getattr(s, "tag", "?")
                weight = getattr(s, "weight", 0)
                why = getattr(s, "why_active", [])
                lines.append(f"    {tag} (weight={weight:.3f})")
                if why:
                    for reason in why[:2]:
                        lines.append(f"      - {reason}")
            lines.append("")

        # Inactive strategies
        inactive = getattr(strategies, "inactive_strategies", [])
        if inactive:
            lines.append(f"  Inactive Strategies ({len(inactive)}):")
            for s in inactive[:5]:
                tag = getattr(s, "tag", "?")
                reason = getattr(s, "inactive_reason", "")
                lines.append(f"    {tag}: {reason}")
            lines.append("")

        # Overlays
        overlays = getattr(strategies, "overlay_tags", [])
        if overlays:
            lines.append(f"  Overlays: {', '.join(overlays)}")
        overlay_warnings = getattr(strategies, "overlay_warnings", [])
        if overlay_warnings:
            lines.append("  Overlay Warnings:")
            for ow in overlay_warnings:
                lines.append(f"    - {ow}")
            resp["warnings"].extend(overlay_warnings)

    # Allocation
    allocation = getattr(state, "allocation", None)
    if allocation:
        weights = getattr(allocation, "weights", {})
        if weights:
            lines.append("")
            lines.append("--- CAPITAL ALLOCATION ---")
            for strat_tag, weight in sorted(weights.items(), key=lambda x: -x[1]):
                lines.append(f"  {strat_tag}: {weight:.1%}")

    resp["answer"] = "\n".join(lines)
    return resp


def handle_unknown(params: dict) -> dict:
    """Handle UNKNOWN: unrecognised query with helpful suggestions."""
    state = _safe_state()
    resp = _base_response(state)

    resp["answer"] = (
        "I didn't understand that query.\n"
        "\n"
        "Available commands:\n"
        "  - 'scan now' / 'find trades'      -- run an on-demand scan\n"
        "  - 'explain QQQ3.L'                 -- deep-dive on a signal\n"
        "  - 'why not NVD3.L'                 -- see why a ticker was rejected\n"
        "  - 'health' / 'system status'       -- data & system health\n"
        "  - 'top trades' / 'show trades'     -- current top plays\n"
        "  - 'closest misses'                 -- near-qualifying tickers\n"
        "  - 'what changed'                   -- changes since last tick\n"
        "  - 'regime' / 'market state'        -- regime & strategy routing\n"
        "\n"
        "Modifiers:\n"
        "  - Add 'CORE' or 'OPPORTUNITY' to filter by lane\n"
        "  - Add 'scalp' or 'swing' to filter by track\n"
        "  - Add 'confidence A' for high-confidence only\n"
    )

    return resp
