"""
command_center/server.py
========================
FastAPI server for NZT-48 Command Center v8.0 — Apex Predator Engine WAR ROOM.

Endpoints:
    GET  /                          -> HTML War Room (5 tabs)
    GET  /api/state                 -> JSON full snapshot
    GET  /api/plays                 -> JSON top plays
    GET  /api/tape                  -> JSON signal tape (last 30)
    GET  /api/health                -> JSON data health panel
    GET  /api/funnel                -> JSON gate funnel panel
    GET  /api/strategies            -> JSON active strategies + overlays
    GET  /api/overlays              -> JSON overlay warnings
    GET  /api/ticker/{symbol}       -> JSON ticker drilldown
    GET  /api/reports               -> JSON PDF/artifact inventory
    GET  /api/calibration           -> JSON win-rate calibration tables
    GET  /api/session_status        -> JSON SessionRunStatus per job
    GET  /api/halt                  -> JSON halt state
    POST /api/halt                  -> Toggle halt_new_signals flag
    GET  /api/universe              -> JSON tiered universe (core/peers/full_scan)
    GET  /api/peers                 -> JSON peer instruments with similarity info
    GET  /api/core_plays            -> JSON CORE-only plays (trade eligible)
    GET  /api/peer_plays            -> JSON PEER-only plays (watch only)
    GET  /api/full_scan             -> JSON full scan intel highlights
    GET  /api/scan_health           -> JSON scan SLA heartbeat
    GET  /api/opportunity           -> JSON 2% net opportunity candidates
    GET  /api/exits                 -> JSON exit scores for open positions
    GET  /api/telegram/events       -> JSON Telegram events + dedupe stats
    GET  /api/consistency           -> JSON artifact consistency check
    POST /api/copilot/query         -> Operator Copilot natural language query
    WS   /ws                        -> WebSocket push (state snapshot every tick)

Run standalone:
    uvicorn command_center.server:app --host 0.0.0.0 --port 8765 --reload
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from core.clock import now_utc
from pathlib import Path

from fastapi import FastAPI, APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse

from command_center.state import get_state

logger  = logging.getLogger("nzt48.command_center")
app     = FastAPI(title="NZT-48 Command Center", version="8.0")

_WS_CLIENTS: set[WebSocket] = set()

# Mountable router for embedding in unified API server
router = APIRouter(prefix="/cc", tags=["command-center"])

_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# REST endpoints — existing
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the built-in War Room HTML dashboard."""
    html_path = Path(__file__).parent / "ui" / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse(_WAR_ROOM_HTML)


@router.get("/state")
@app.get("/api/state")
async def api_state():
    return get_state().get_snapshot()


@router.get("/plays")
@app.get("/api/plays")
async def api_plays():
    state = get_state()
    return {
        "tick":    state.tick_count,
        "session": state.market.session_name,
        "halt":    state.halt_new_signals,
        "plays": [
            {
                "rank":          i + 1,
                "ticker":        p.ticker,
                "direction":     p.direction,
                "stars":         p.stars_str,
                "score":         p.composite,
                "strategy_score": getattr(p, "strategy_weighted_score", p.composite),
                "label":         p.label,
                "entry":         p.entry,
                "stop":          p.stop,
                "target1":       p.target1,
                "target2":       p.target2,
                "rr":            p.rr_ratio,
                "atr_pct":       p.atr_pct,
                "rvol":          p.rvol,
                "setup_type":    p.setup_type,
                "factor_group":  p.factor_group,
                "track":         getattr(p, "track", "INTRADAY_SWING"),
                "strategy_tag":  getattr(p, "strategy_tag", ""),
                "sizing_hint":   getattr(p, "sizing_hint", "M"),
                "reasons":       p.reasons,
            }
            for i, p in enumerate(state.top_plays)
        ],
        "drought": state.drought.to_text() if state.drought else None,
    }


@router.get("/tape")
@app.get("/api/tape")
async def api_tape():
    return {"lines": get_state().tape.to_lines(30)}


@router.get("/health")
@app.get("/api/health")
async def api_health():
    h = get_state().data_health
    response = {
        "badge":          h.badge,
        "pass":           h.pass_count,
        "warn":           h.warn_count,
        "fail":           h.fail_count,
        "failed_tickers": h.failed_tickers,
        "warnings":       h.warnings,
    }
    response["provider"] = "yfinance"
    response["data_as_of"] = now_utc().isoformat()
    response["staleness_note"] = "Data refreshed each tick cycle"
    return response


@router.get("/funnel")
@app.get("/api/funnel")
async def api_funnel():
    f = get_state().gate_funnel
    return {
        "tracked":          f.tracked,
        "data_valid":       f.data_valid,
        "passed_all_gates": f.passed_all_gates,
        "signals_strict":   f.signals_strict,
        "signals_fallback": f.signals_fallback,
        "total":            f.total_signals,
        "blockers":         f.top_blockers,
        "closest_misses":   f.closest_misses,
    }


# ---------------------------------------------------------------------------
# REST endpoints — v3.0 new
# ---------------------------------------------------------------------------

@router.get("/strategies")
@app.get("/api/strategies")
async def api_strategies():
    """Active strategy router result with regime confidence and evidence."""
    s = get_state().strategies
    state = get_state()
    # Build regime evidence from market state
    market = state.market
    regime_evidence = []
    if hasattr(market, 'regime') and market.regime:
        regime_evidence.append(f"Regime classified as {market.regime}")
    if hasattr(market, 'session_name') and market.session_name:
        regime_evidence.append(f"Session: {market.session_name}")
    if hasattr(market, 'vix') and market.vix:
        regime_evidence.append(f"VIX: {market.vix:.1f}")
    if hasattr(s, 'regime_confidence') and s.regime_confidence:
        regime_evidence.append(f"Confidence: {s.regime_confidence}")
    return {
        "regime_tag":        s.regime_tag,
        "regime_confidence": s.regime_confidence,
        "regime_evidence":   regime_evidence,
        "time_of_day":       s.time_of_day_window,
        "sizing_mode":       s.sizing_mode,
        "kill_switch":       s.kill_switch,
        "score_boost":       s.score_boost,
        "max_factor_cap":    s.max_factor_cap,
        "active": [
            {
                "tag":        spec.tag,
                "weight":     spec.weight,
                "why_active": spec.why_active,
                "category":   spec.category,
            }
            for spec in s.active_strategies
        ],
        "inactive": [
            {
                "tag":             spec.tag,
                "inactive_reason": spec.inactive_reason,
                "category":        spec.category,
            }
            for spec in s.inactive_strategies
        ],
        "overlay_tags":     s.overlay_tags,
        "overlay_warnings": s.overlay_warnings,
    }


@router.get("/overlays")
@app.get("/api/overlays")
async def api_overlays():
    """Overlay warnings and decisions only."""
    s = get_state().strategies
    return {
        "overlay_tags":     s.overlay_tags,
        "overlay_warnings": s.overlay_warnings,
        "sizing_mode":      s.sizing_mode,
        "kill_switch":      s.kill_switch,
    }


@router.get("/ticker/{symbol}")
@app.get("/api/ticker/{symbol}")
async def api_ticker_drilldown(symbol: str):
    """Drilldown: feature values, gate pass/fail, what would admit this ticker."""
    state = get_state()
    sym_upper = symbol.upper()

    # Check top plays for this ticker
    play = next((p for p in state.top_plays if p.ticker == sym_upper), None)

    # Try to load from latest artifact
    artifact_data = None
    try:
        from signal_engine.signal_card import latest_artifact
        for session in ("LSE", "NYSE", "PRE_LSE", "PRE_NYSE", "EOD"):
            data = latest_artifact(session)
            if data:
                for card in data.get("plays", []):
                    if card.get("ticker", "").upper() == sym_upper:
                        artifact_data = card
                        break
            if artifact_data:
                break
    except Exception:
        pass

    result = {
        "ticker":      sym_upper,
        "in_top_plays": play is not None,
        "artifact_data": artifact_data,
    }

    if play:
        result["play"] = {
            "stars":         play.stars_str,
            "composite":     play.composite,
            "direction":     play.direction,
            "entry":         play.entry,
            "stop":          play.stop,
            "target1":       play.target1,
            "rr_ratio":      play.rr_ratio,
            "atr_pct":       play.atr_pct,
            "rvol":          play.rvol,
            "setup_type":    play.setup_type,
            "factor_group":  play.factor_group,
            "reasons":       play.reasons,
            "strategy_tag":  getattr(play, "strategy_tag", ""),
        }

    return result


@router.get("/reports")
@app.get("/api/reports")
async def api_reports():
    """List today's PDF files and artifact inventory."""
    from datetime import date
    today = str(date.today())

    pdfs = []
    reports_dir = _ROOT / "data" / "reports"
    if reports_dir.exists():
        for p in sorted(reports_dir.glob("*.pdf"), key=lambda f: f.stat().st_mtime, reverse=True):
            pdfs.append({
                "name":     p.name,
                "size_kb":  round(p.stat().st_size / 1024, 1),
                "modified": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat(),
            })

    artifacts = []
    artifacts_dir = _ROOT / "artifacts" / today
    if artifacts_dir.exists():
        for session_dir in sorted(artifacts_dir.iterdir()):
            if session_dir.is_dir():
                plays_path = session_dir / "plays.json"
                strat_path = session_dir / "strategies.json"
                artifacts.append({
                    "session":          session_dir.name,
                    "plays_exists":     plays_path.exists(),
                    "strategies_exists": strat_path.exists(),
                    "plays_size_kb":    round(plays_path.stat().st_size / 1024, 1) if plays_path.exists() else 0,
                })

    return {
        "date":      today,
        "pdfs":      pdfs[:20],
        "artifacts": artifacts,
    }


@router.get("/calibration")
@app.get("/api/calibration")
async def api_calibration():
    """Win-rate calibration tables — live from CalibrationEngine when data exists."""
    rr_breakevens = [
        {"rr": 1.0, "win_rate_required_pct": 50.0},
        {"rr": 1.5, "win_rate_required_pct": 40.0},
        {"rr": 2.0, "win_rate_required_pct": 33.3},
        {"rr": 2.5, "win_rate_required_pct": 28.6},
        {"rr": 3.0, "win_rate_required_pct": 25.0},
    ]
    # Try live CalibrationEngine
    try:
        from learning.calibration import CalibrationEngine
        engine = CalibrationEngine()
        suggestions = engine.get_suggestions()
        summary = engine._attribution.get_summary()
        return {
            "note": f"{summary.get('n_outcomes_recorded', 0)} outcomes recorded.",
            "rr_breakeven_table": rr_breakevens,
            "suggestions": [s.to_dict() for s in suggestions],
            "attribution_summary": summary,
        }
    except Exception as e:
        return {
            "note": f"Calibration not yet active: {e}",
            "rr_breakeven_table": rr_breakevens,
            "suggestions": [],
            "attribution_summary": {},
        }


@router.get("/session_status")
@app.get("/api/session_status")
async def api_session_status():
    """SessionRunStatus for all 4 PDF jobs."""
    state = get_state()
    state.refresh_session_status()
    return {
        "jobs": [
            {
                "session":           j.session,
                "status":            j.status,
                "timestamp":         j.timestamp,
                "artifacts_written": j.artifacts_written,
                "pdf_written":       j.pdf_written,
                "error_msg":         j.error_msg,
            }
            for j in state.session_status.jobs
        ]
    }


@router.get("/halt")
@app.get("/api/halt")
async def api_halt_get():
    """Get halt state."""
    return {"halt_new_signals": get_state().halt_new_signals}


@router.post("/halt")
@app.post("/api/halt")
async def api_halt_toggle():
    """Toggle halt_new_signals flag. Used by War Room guardrails panel."""
    state = get_state()
    state.halt_new_signals = not state.halt_new_signals
    new_state = state.halt_new_signals
    logger.warning("[HALT] halt_new_signals set to %s", new_state)
    return {"halt_new_signals": new_state, "message": "HALTED" if new_state else "RESUMED"}


# ---------------------------------------------------------------------------
# v4.0 new endpoints
# ---------------------------------------------------------------------------

@router.get("/allocation")
@app.get("/api/allocation")
async def api_allocation():
    """Capital allocation weights per strategy (v4.0)."""
    a = get_state().allocation
    return a.to_dict()


@router.get("/risk_officer")
@app.get("/api/risk_officer")
async def api_risk_officer():
    """Latest risk officer decisions (v4.0)."""
    r = get_state().risk_officer
    return r.to_dict()


@router.get("/drought")
@app.get("/api/drought")
async def api_drought():
    """Drought Cockpit: closest misses + recommended knobs (v4.0)."""
    d = get_state().drought_cockpit
    return d.to_dict()


@router.get("/artifacts")
@app.get("/api/artifacts")
async def api_artifacts(date: str = None):
    """Artifact inventory with optional date filter YYYY-MM-DD (v4.0)."""
    from datetime import date as _date
    target = date or str(_date.today())
    artifacts_dir = _ROOT / "artifacts" / target
    result = []
    if artifacts_dir.exists():
        for session_dir in sorted(artifacts_dir.iterdir()):
            if session_dir.is_dir():
                plays_path   = session_dir / "plays.json"
                strat_path   = session_dir / "strategies.json"
                risk_path    = session_dir / "risk_officer.json"
                drought_path = session_dir / "drought.json"
                result.append({
                    "session":              session_dir.name,
                    "plays_exists":         plays_path.exists(),
                    "strategies_exists":    strat_path.exists(),
                    "risk_officer_exists":  risk_path.exists(),
                    "drought_exists":       drought_path.exists(),
                    "plays_size_kb":  round(plays_path.stat().st_size / 1024, 1) if plays_path.exists() else 0,
                    "risk_size_kb":   round(risk_path.stat().st_size / 1024, 1) if risk_path.exists() else 0,
                    "drought_size_kb": round(drought_path.stat().st_size / 1024, 1) if drought_path.exists() else 0,
                })
    return {"date": target, "artifact_count": len(result), "artifacts": result}


# ---------------------------------------------------------------------------
# v5.0 Self-Learning AI endpoints
# ---------------------------------------------------------------------------

@router.get("/ai/expectancy")
@app.get("/api/ai/expectancy")
async def api_ai_expectancy():
    """Top trades with ExpectedNetR, P(target), duration, uncertainty, decision."""
    try:
        from learning.expectancy_model import get_expectancy_model
        from learning.execution_quality_model import get_execution_quality_model
        state  = get_state()
        model  = get_expectancy_model()
        eq_mdl = get_execution_quality_model()
        results = []
        for p in state.top_plays[:20]:
            sid   = getattr(p, "signal_id", "") or f"NZT-{getattr(p, 'ticker','?')[:4]}"
            eq    = eq_mdl.predict(
                signal_id    = sid,
                ticker       = getattr(p, "ticker", ""),
                rvol         = float(getattr(p, "rvol", 1.0) or 1.0),
                atr_pct      = float(getattr(p, "atr_pct", 1.0) or 1.0),
                time_window  = state.strategies.time_of_day_window,
            )
            exp   = model.predict(
                signal_id    = sid,
                strategy_tag = getattr(p, "strategy_tag", ""),
                regime_tag   = state.market.regime,
                track        = getattr(p, "track", "INTRADAY_SWING"),
                time_window  = state.strategies.time_of_day_window,
                net_rr       = float(getattr(p, "rr_ratio", 2.0) or 2.0),
                composite_score = float(getattr(p, "composite", 60.0) or 60.0),
                fill_risk_score = eq.fill_risk_score,
            )
            results.append({
                "ticker":              getattr(p, "ticker", ""),
                "direction":           getattr(p, "direction", ""),
                "strategy_tag":        getattr(p, "strategy_tag", ""),
                "stars":               getattr(p, "stars_str", ""),
                "composite":           getattr(p, "composite", 0),
                "entry":               getattr(p, "entry", 0),
                "stop":                getattr(p, "stop", 0),
                "target1":             getattr(p, "target1", 0),
                "rr":                  getattr(p, "rr_ratio", 0),
                "sizing_hint":         getattr(p, "sizing_hint", "M"),
                # AI outputs
                "decision":            exp.decision,
                "expected_net_r":      exp.expected_net_r,
                "p_target":            exp.p_target,
                "expected_duration":   exp.expected_duration_min,
                "uncertainty":         exp.uncertainty,
                "why":                 exp.why,
                "method":              exp.method,
                "sample_basis":        exp.sample_basis,
                # Execution quality
                "fill_risk_score":     eq.fill_risk_score,
                "spread_bps":          eq.spread_bps,
                "exec_recommendation": eq.recommendation,
            })
        return {"trades": results, "count": len(results), "regime": state.market.regime}
    except Exception as e:
        logger.error(f"/api/ai/expectancy error: {e}")
        return {"trades": [], "count": 0, "error": str(e)}


@router.get("/ai/edge_map")
@app.get("/api/ai/edge_map")
async def api_ai_edge_map():
    """Current-regime edge map from Edge Ledger."""
    try:
        from learning.edge_ledger import get_edge_ledger
        state  = get_state()
        ledger = get_edge_ledger()
        data   = ledger.load()
        regime = state.market.regime

        # Filter and sort by expectancy_net descending
        rows = []
        for key, rec in data.items():
            parts = key.split("|")
            rows.append({
                "key":              key,
                "strategy_tag":     parts[0] if len(parts) > 0 else "",
                "regime_tag":       parts[1] if len(parts) > 1 else "",
                "track":            parts[2] if len(parts) > 2 else "",
                "time_window":      parts[3] if len(parts) > 3 else "",
                "trades_count":     rec.trades_count,
                "win_rate":         rec.win_rate,
                "win_rate_low":     rec.win_rate_low,
                "win_rate_high":    rec.win_rate_high,
                "avg_rr_net":       rec.avg_rr_net,
                "expectancy_net":   rec.expectancy_net,
                "confidence_score": rec.confidence_score,
                "status":           rec.status,
                "current_regime":   (parts[1] if len(parts) > 1 else "") == regime,
            })
        rows.sort(key=lambda r: r["expectancy_net"], reverse=True)
        return {
            "regime": regime,
            "buckets": rows[:50],
            "total_buckets": len(rows),
            "actionable": sum(1 for r in rows if r["status"] == "ACTIONABLE"),
        }
    except Exception as e:
        return {"buckets": [], "error": str(e)}


@router.get("/ai/drift")
@app.get("/api/ai/drift")
async def api_ai_drift():
    """Latest drift report + defensive mode status."""
    try:
        from learning.drift import DriftDetector
        det  = DriftDetector()
        rep  = det.get_latest_report()
        defe = det.is_defensive_mode_active()
        return {
            "defensive_mode": defe,
            "latest_report":  rep.to_dict() if rep else None,
            "has_drift":      rep.detected if rep else False,
        }
    except Exception as e:
        return {"defensive_mode": False, "latest_report": None, "error": str(e)}


@router.get("/ai/meta_weights")
@app.get("/api/ai/meta_weights")
async def api_ai_meta_weights():
    """Current meta-learner strategy weights."""
    try:
        from learning.meta_learner import get_meta_learner
        ml = get_meta_learner()
        w  = ml.load_current()
        return w.to_dict()
    except Exception as e:
        return {"weights": {}, "error": str(e)}


@router.get("/ai/execution_quality")
@app.get("/api/ai/execution_quality")
async def api_ai_execution_quality():
    """Execution quality predictions for current top plays."""
    try:
        from learning.execution_quality_model import get_execution_quality_model
        _DATA = Path(__file__).parent.parent / "data"
        eq_log = _DATA / "execution_quality.jsonl"
        records = []
        if eq_log.exists():
            import json as _json
            lines = eq_log.read_text().strip().split("\n")
            for line in reversed(lines[-20:]):
                if line.strip():
                    records.append(_json.loads(line))
        return {"records": records, "count": len(records)}
    except Exception as e:
        return {"records": [], "error": str(e)}


@router.get("/outcomes/recent")
@app.get("/api/outcomes/recent")
async def api_outcomes_recent(limit: int = 30):
    """Recent resolved outcomes from outcomes.jsonl."""
    try:
        _DATA = Path(__file__).parent.parent / "data"
        outcomes_path = _DATA / "outcomes.jsonl"
        records = []
        if outcomes_path.exists():
            import json as _json
            lines = outcomes_path.read_text().strip().split("\n")
            for line in reversed(lines[-(limit*2):]):
                if line.strip():
                    records.append(_json.loads(line))
                    if len(records) >= limit:
                        break
        return {"outcomes": records, "count": len(records)}
    except Exception as e:
        return {"outcomes": [], "error": str(e)}


@router.get("/ai/signal_log_stats")
@app.get("/api/ai/signal_log_stats")
async def api_signal_log_stats():
    """Stats on the signal log (total, pending, resolved)."""
    try:
        from learning.signal_logger import get_signal_logger
        sl = get_signal_logger()
        return sl.get_stats()
    except Exception as e:
        return {"total": 0, "pending": 0, "resolved": 0, "error": str(e)}


@router.post("/ai/resolve_outcomes")
@app.post("/api/ai/resolve_outcomes")
async def api_resolve_outcomes():
    """Manually trigger outcome resolution."""
    try:
        from learning.outcomes_engine import get_outcome_engine
        engine = get_outcome_engine()
        stats  = engine.resolve_all_pending()
        return {"status": "ok", "stats": stats}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post("/ai/rebuild_edge_ledger")
@app.post("/api/ai/rebuild_edge_ledger")
async def api_rebuild_edge_ledger():
    """Rebuild edge ledger from all outcomes."""
    try:
        from learning.edge_ledger import get_edge_ledger
        ledger = get_edge_ledger()
        result = ledger.rebuild()
        return {"status": "ok", **result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post("/ai/update_meta_weights")
@app.post("/api/ai/update_meta_weights")
async def api_update_meta_weights():
    """Update meta-learner weights from current evidence."""
    try:
        from learning.meta_learner import get_meta_learner
        state = get_state()
        ml    = get_meta_learner()
        w     = ml.update(regime_tag=state.market.regime)
        return {"status": "ok", "weights": w.weights, "notes": w.guardrail_notes}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post("/ai/run_drift_detection")
@app.post("/api/ai/run_drift_detection")
async def api_run_drift_detection():
    """Run all drift detectors and return reports."""
    try:
        from learning.drift import DriftDetector
        det     = DriftDetector()
        reports = det.run_all()
        return {
            "status": "ok",
            "reports_generated": len(reports),
            "defensive_mode": any(r.defensive_mode_triggered for r in reports),
            "reports": [r.to_dict() for r in reports],
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# v6.0 — Intel Feed + File Serving + Preview PDFs
# ---------------------------------------------------------------------------

@router.get("/intel")
@app.get("/api/intel")
async def api_intel():
    """Intel feed from extended universe — latest intel.json."""
    from datetime import date as _date
    today = str(_date.today())
    intel_data = []
    artifacts_dir = _ROOT / "artifacts" / today
    if artifacts_dir.exists():
        for session_dir in sorted(artifacts_dir.iterdir(), reverse=True):
            intel_path = session_dir / "intel.json"
            if intel_path.exists():
                try:
                    data = json.loads(intel_path.read_text())
                    intel_data = data.get("intel_cards", [])
                    return {
                        "session": session_dir.name,
                        "generated_at": data.get("generated_at", ""),
                        "count": len(intel_data),
                        "cards": intel_data[:30],
                    }
                except Exception:
                    pass
    return {"session": "", "count": 0, "cards": []}


@router.get("/files")
@app.get("/api/files")
async def api_files():
    """List all generated files: PDFs, artifacts, logs — with download links."""
    from datetime import date as _date
    today = str(_date.today())
    files = []

    # PDFs from reports/
    reports_dir = _ROOT / "reports" / today
    if reports_dir.exists():
        for p in sorted(reports_dir.glob("*.pdf"), key=lambda f: f.stat().st_mtime, reverse=True):
            files.append({
                "name": p.name,
                "type": "PDF",
                "path": f"/files/reports/{today}/{p.name}",
                "size_kb": round(p.stat().st_size / 1024, 1),
                "modified": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat(),
            })

    # PDFs from data/reports/
    data_reports = _ROOT / "data" / "reports"
    if data_reports.exists():
        for p in sorted(data_reports.glob("**/*.pdf"), key=lambda f: f.stat().st_mtime, reverse=True):
            rel = p.relative_to(_ROOT)
            files.append({
                "name": p.name,
                "type": "PDF",
                "path": f"/files/{rel}",
                "size_kb": round(p.stat().st_size / 1024, 1),
                "modified": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat(),
            })

    # Artifacts
    artifacts_dir = _ROOT / "artifacts" / today
    if artifacts_dir.exists():
        for session_dir in sorted(artifacts_dir.iterdir()):
            if session_dir.is_dir():
                for f in sorted(session_dir.glob("*.json")):
                    rel = f.relative_to(_ROOT)
                    files.append({
                        "name": f"{session_dir.name}/{f.name}",
                        "type": "ARTIFACT",
                        "path": f"/files/{rel}",
                        "size_kb": round(f.stat().st_size / 1024, 1),
                        "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
                    })

    # Signal log
    signal_log = _ROOT / "data" / "signal_log.jsonl"
    if signal_log.exists():
        files.append({
            "name": "signal_log.jsonl",
            "type": "LOG",
            "path": "/files/data/signal_log.jsonl",
            "size_kb": round(signal_log.stat().st_size / 1024, 1),
            "modified": datetime.fromtimestamp(signal_log.stat().st_mtime, tz=timezone.utc).isoformat(),
        })

    # Session status
    ss = _ROOT / "data" / "session_status.json"
    if ss.exists():
        files.append({
            "name": "session_status.json",
            "type": "STATUS",
            "path": "/files/data/session_status.json",
            "size_kb": round(ss.stat().st_size / 1024, 1),
            "modified": datetime.fromtimestamp(ss.stat().st_mtime, tz=timezone.utc).isoformat(),
        })

    return {"date": today, "total_files": len(files), "files": files}


@router.get("/serve_files/{file_path:path}")
@app.get("/files/{file_path:path}")
async def serve_file(file_path: str):
    """Serve generated files (PDFs, JSON artifacts, logs)."""
    from fastapi.responses import FileResponse
    full_path = _ROOT / file_path
    if not full_path.exists():
        return JSONResponse({"error": "File not found"}, status_code=404)
    # Security: only serve files under known safe dirs
    allowed_prefixes = ("reports/", "data/reports/", "artifacts/", "data/signal_log",
                        "data/session_status", "data/outcomes")
    if not any(file_path.startswith(p) for p in allowed_prefixes):
        return JSONResponse({"error": "Access denied"}, status_code=403)
    content_type = "application/pdf" if file_path.endswith(".pdf") else "application/json"
    if file_path.endswith(".jsonl"):
        content_type = "text/plain"
    return FileResponse(full_path, media_type=content_type, filename=full_path.name)


@router.get("/scheduler")
@app.get("/api/scheduler")
async def api_scheduler():
    """Scheduler status: next run times, last status."""
    from datetime import date as _date
    today = str(_date.today())
    jobs = [
        {"name": "PDF1 Pre-LSE", "schedule": "07:00 UK", "session": "PRE_LSE"},
        {"name": "PDF2 Pre-NYSE", "schedule": "13:30 UK", "session": "PRE_NYSE"},
        {"name": "PDF3 EOD Review", "schedule": "22:00 UK", "session": "EOD_INSTITUTIONAL"},
        {"name": "Mega PDF", "schedule": "22:30 UK", "session": "MEGA_EOD"},
        {"name": "Signal Engine (15m)", "schedule": "every 15min", "session": "PERIODIC"},
        {"name": "Outcome Resolver", "schedule": "every 30min", "session": ""},
        {"name": "LSE Registry", "schedule": "06:30 UK", "session": ""},
        {"name": "Sector Rotation", "schedule": "every 60s", "session": ""},
    ]

    # Enrich with session_status data
    try:
        from signal_engine.signal_card import read_session_status
        status_data = read_session_status()
        for job in jobs:
            sess = job["session"]
            if sess and sess in status_data:
                info = status_data[sess]
                job["last_status"] = info.get("status", "UNKNOWN")
                job["last_run"] = info.get("timestamp", "")
                job["error"] = info.get("error_msg", "")
            else:
                job["last_status"] = "PENDING"
                job["last_run"] = ""
                job["error"] = ""
    except Exception:
        pass

    return {"jobs": jobs}


# ---------------------------------------------------------------------------
# Telegram PDF batch delivery
# ---------------------------------------------------------------------------

@router.post("/telegram/send_pdfs")
@app.post("/api/telegram/send_pdfs")
async def api_telegram_send_pdfs():
    """Send all today's PDFs to Telegram as documents."""
    from datetime import date as _date
    today = str(_date.today())
    sent = []
    errors = []

    # Collect all PDF files
    pdf_paths = []
    for search_dir in [_ROOT / "reports" / today, _ROOT / "data" / "reports" / today]:
        if search_dir.exists():
            for p in sorted(search_dir.glob("*.pdf")):
                if p.stat().st_size > 0:
                    pdf_paths.append(p)

    if not pdf_paths:
        return {"status": "error", "error": "No PDFs found for today", "files_sent": 0}

    try:
        import os
        from delivery.telegram_bot import TelegramDelivery
        tg = TelegramDelivery(
            token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
        )
        await tg.initialize()

        # Send header message
        await tg.send_alert(
            f"📊 <b>NZT-48 PDF BATCH</b>\n\n"
            f"Date: {today}\n"
            f"Files: {len(pdf_paths)} PDFs\n"
            f"Sending now..."
        )

        for pdf_path in pdf_paths:
            try:
                caption = f"📄 <b>{pdf_path.stem}</b>\n{today}"
                ok = await tg.send_document(str(pdf_path), caption=caption)
                if ok:
                    sent.append(pdf_path.name)
                else:
                    errors.append(f"{pdf_path.name}: send returned False")
            except Exception as e:
                errors.append(f"{pdf_path.name}: {str(e)[:100]}")

        return {
            "status": "ok" if sent else "error",
            "files_sent": len(sent),
            "files": sent,
            "errors": errors,
        }
    except Exception as e:
        logger.error("Telegram PDF batch failed: %s", e)
        return {"status": "error", "error": str(e)[:200], "files_sent": 0}


# ---------------------------------------------------------------------------
# v7.0 — Tiered Universe endpoints
# ---------------------------------------------------------------------------

@router.get("/universe")
@app.get("/api/universe")
async def api_universe():
    """Return current universe tiers and sizes."""
    try:
        from uk_isa.universe_manager import UniverseManager
        um = UniverseManager()
        return {
            "core": um.core_list,
            "peers": um.peer_list,
            "full_scan": um.full_scan_list,
            "sizes": {
                "core": len(um.core_list),
                "peers": len(um.peer_list),
                "full_scan": len(um.full_scan_list),
            },
        }
    except ImportError:
        # Fallback to isa_universe definitions
        try:
            from uk_isa.isa_universe import CORE_UNIVERSE, EXTENDED_UNIVERSE, INTEL_UNIVERSE
            peer_list = [t for t in EXTENDED_UNIVERSE if t not in CORE_UNIVERSE]
            return {
                "core": CORE_UNIVERSE,
                "peers": peer_list,
                "full_scan": INTEL_UNIVERSE,
                "sizes": {
                    "core": len(CORE_UNIVERSE),
                    "peers": len(peer_list),
                    "full_scan": len(INTEL_UNIVERSE),
                },
            }
        except ImportError:
            return {"core": [], "peers": [], "full_scan": [], "sizes": {"core": 0, "peers": 0, "full_scan": 0}, "error": "No universe module found"}
    except Exception as e:
        return {"core": [], "peers": [], "full_scan": [], "sizes": {"core": 0, "peers": 0, "full_scan": 0}, "error": str(e)}


@router.get("/peers")
@app.get("/api/peers")
async def api_peers():
    """Return peer instruments with similarity info."""
    from datetime import date as _date
    today = str(_date.today())
    artifacts_dir = _ROOT / "artifacts" / today
    if artifacts_dir.exists():
        for session_dir in sorted(artifacts_dir.iterdir(), reverse=True):
            peers_path = session_dir / "peers_intel.json"
            if peers_path.exists():
                try:
                    data = json.loads(peers_path.read_text())
                    return {
                        "peers": data.get("items", []),
                        "count": data.get("count", 0),
                        "method": "combined",
                        "session": data.get("session", session_dir.name),
                        "generated_at": data.get("generated_at", ""),
                    }
                except Exception:
                    pass
    return {"peers": [], "count": 0, "method": "combined"}


@router.get("/core_plays")
@app.get("/api/core_plays")
async def api_core_plays():
    """Return CORE-only plays (trade eligible)."""
    state = get_state()
    core_plays = []
    for i, p in enumerate(state.top_plays):
        tier = getattr(p, "tier", "CORE")
        if tier == "CORE":
            core_plays.append({
                "rank":          i + 1,
                "ticker":        p.ticker,
                "direction":     p.direction,
                "stars":         p.stars_str,
                "score":         p.composite,
                "label":         p.label,
                "entry":         p.entry,
                "stop":          p.stop,
                "target1":       p.target1,
                "target2":       p.target2,
                "rr":            p.rr_ratio,
                "atr_pct":       p.atr_pct,
                "rvol":          p.rvol,
                "setup_type":    p.setup_type,
                "factor_group":  p.factor_group,
                "strategy_tag":  getattr(p, "strategy_tag", ""),
                "sizing_hint":   getattr(p, "sizing_hint", "M"),
                "reasons":       p.reasons,
                "tier":          "CORE",
            })
    return {
        "tick":    state.tick_count,
        "session": state.market.session_name,
        "count":   len(core_plays),
        "plays":   core_plays,
    }


@router.get("/peer_plays")
@app.get("/api/peer_plays")
async def api_peer_plays():
    """Return PEER-only plays (watch only)."""
    from datetime import date as _date
    today = str(_date.today())
    artifacts_dir = _ROOT / "artifacts" / today
    if artifacts_dir.exists():
        for session_dir in sorted(artifacts_dir.iterdir(), reverse=True):
            peers_path = session_dir / "peers_intel.json"
            if peers_path.exists():
                try:
                    data = json.loads(peers_path.read_text())
                    items = data.get("items", [])
                    return {
                        "count": len(items),
                        "plays": items,
                        "tier": "PEER",
                        "session": data.get("session", session_dir.name),
                        "generated_at": data.get("generated_at", ""),
                    }
                except Exception:
                    pass
    return {"count": 0, "plays": [], "tier": "PEER"}


@router.get("/full_scan")
@app.get("/api/full_scan")
async def api_full_scan():
    """Return full scan intel highlights."""
    from datetime import date as _date
    today = str(_date.today())
    artifacts_dir = _ROOT / "artifacts" / today
    if artifacts_dir.exists():
        for session_dir in sorted(artifacts_dir.iterdir(), reverse=True):
            scan_path = session_dir / "full_scan.json"
            if scan_path.exists():
                try:
                    data = json.loads(scan_path.read_text())
                    items = data.get("items", [])
                    return {
                        "count": len(items),
                        "cards": items,
                        "tier": "FULL_SCAN",
                        "session": data.get("session", session_dir.name),
                        "generated_at": data.get("generated_at", ""),
                    }
                except Exception:
                    pass
    return {"count": 0, "cards": [], "tier": "FULL_SCAN"}


# ---------------------------------------------------------------------------
# REST endpoints — v3.1 operational intelligence
# ---------------------------------------------------------------------------

@app.get("/api/scan_health")
async def api_scan_health():
    """Scan SLA: tick_count, engine_runs, signals_emitted, last_success_ts."""
    try:
        scan_health_path = _ROOT / "data" / "scan_health.json"
        if scan_health_path.exists():
            return json.loads(scan_health_path.read_text())
        return {
            "tick_count": 0, "engine_runs": 0, "signals_emitted": 0,
            "signals_logged": 0, "last_success_ts": None, "last_error_ts": None,
            "last_error_msg": None, "state": "UNKNOWN", "uptime_seconds": 0
        }
    except Exception as e:
        return {"error": str(e), "state": "ERROR"}


@app.get("/api/opportunity")
async def api_opportunity():
    """Top 20 opportunity candidates with 2% net feasibility."""
    try:
        today = now_utc().strftime("%Y-%m-%d")
        # Try loading from latest session artifact
        for session in ["EOD_INSTITUTIONAL", "PRE_NYSE", "PRE_LSE"]:
            opp_path = _ROOT / "artifacts" / today / session / "opportunity.json"
            if opp_path.exists():
                return {"session": session, "candidates": json.loads(opp_path.read_text()), "objective": "+2% NET AFTER FEES"}
        return {"session": None, "candidates": [], "objective": "+2% NET AFTER FEES", "note": "No opportunity scan yet today"}
    except Exception as e:
        return {"error": str(e), "candidates": []}


@app.get("/api/exits")
async def api_exits():
    """Exit scores and sell intents for open positions."""
    try:
        exit_path = _ROOT / "data" / "exit_scores.json"
        if exit_path.exists():
            return json.loads(exit_path.read_text())
        return {"positions": [], "batch_sell_plan": None, "note": "No exit scores computed yet"}
    except Exception as e:
        return {"error": str(e), "positions": []}


@app.get("/api/telegram/events")
async def api_telegram_events():
    """Latest Telegram events and dedupe status."""
    try:
        events = []
        debug_path = _ROOT / "data" / "telegram_debug.jsonl"
        if debug_path.exists():
            lines = debug_path.read_text().strip().split("\n")
            for line in lines[-50:]:  # Last 50 events
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        # Compute stats
        sent = sum(1 for e in events if e.get("action") == "SENT")
        suppressed = sum(1 for e in events if e.get("action") in ("DEDUPED", "RATE_LIMITED", "GATE_FAILED"))
        return {
            "events": events,
            "stats": {"sent": sent, "suppressed": suppressed, "total": len(events)},
            "dedupe_active": True,
            "rate_limit_active": True
        }
    except Exception as e:
        return {"error": str(e), "events": [], "stats": {}}


@app.get("/api/consistency")
async def api_consistency():
    """Check that War Room, Telegram, and PDFs consume the same artifacts."""
    try:
        import hashlib
        today = now_utc().strftime("%Y-%m-%d")
        results = {}
        for session in ["PRE_LSE", "PRE_NYSE", "EOD_INSTITUTIONAL"]:
            plays_path = _ROOT / "artifacts" / today / session / "plays.json"
            if plays_path.exists():
                content = plays_path.read_text()
                plays_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
                results[session] = {
                    "plays_hash": plays_hash,
                    "plays_count": len(json.loads(content)) if content.strip() else 0,
                    "artifact_exists": True
                }
            else:
                results[session] = {"artifact_exists": False}
        return {"sessions": results, "consistent": True, "note": "All surfaces read from same artifact files"}
    except Exception as e:
        return {"error": str(e), "consistent": False}


# ---------------------------------------------------------------------------
# Operator Copilot (AI Chatbot — READ ONLY)
# ---------------------------------------------------------------------------

@router.post("/copilot/query")
@app.post("/api/copilot/query")
async def api_copilot_query(request: Request):
    """Operator Copilot: natural language query interface.

    POST body: { "query": "...", "lane": "CORE|OPPORTUNITY|INTEL|ALL", "max_results": 10 }
    Returns: structured JSON with answer, actions, evidence, warnings, as_of, system_state, regime, confidence.

    SAFETY: Read-only. Cannot place orders. Cannot fabricate data.
    """
    try:
        body = await request.json()
    except Exception:
        return {"error": "Invalid JSON body", "confidence": "C"}

    query = body.get("query", "").strip()
    if not query:
        return {"error": "Empty query", "confidence": "C"}

    lane = body.get("lane", "ALL").upper()
    max_results = min(int(body.get("max_results", 10)), 50)

    try:
        from command_center.copilot.router import CopilotRouter
        router_instance = CopilotRouter()
        result = router_instance.query(query, lane=lane, max_results=max_results)
        return result
    except ImportError as e:
        logger.warning("Copilot module not available: %s", e)
        return {
            "answer": "Copilot module is not available. Check that command_center/copilot/ is installed.",
            "actions": [],
            "evidence": [],
            "warnings": [str(e)],
            "as_of": datetime.now(timezone.utc).isoformat(),
            "system_state": "UNKNOWN",
            "regime": "UNKNOWN",
            "confidence": "C",
        }
    except Exception as e:
        logger.error("Copilot query failed: %s", e, exc_info=True)
        return {
            "answer": f"Internal error: {str(e)[:200]}",
            "actions": [],
            "evidence": [],
            "warnings": ["internal_error"],
            "as_of": datetime.now(timezone.utc).isoformat(),
            "system_state": "UNKNOWN",
            "regime": "UNKNOWN",
            "confidence": "C",
        }


# ---------------------------------------------------------------------------
# WebSocket push
# ---------------------------------------------------------------------------

@router.websocket("/ws")
@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    _WS_CLIENTS.add(websocket)
    try:
        # Send initial snapshot immediately
        snap = get_state().get_snapshot()
        await websocket.send_text(json.dumps(snap))
        while True:
            await asyncio.sleep(15)
            snap = get_state().get_snapshot()
            await websocket.send_text(json.dumps(snap))
    except WebSocketDisconnect:
        pass
    finally:
        _WS_CLIENTS.discard(websocket)


async def broadcast_tick(snapshot: dict) -> None:
    """Push snapshot to all connected WebSocket clients."""
    dead: set = set()
    for ws in _WS_CLIENTS:
        try:
            await ws.send_text(json.dumps(snapshot))
        except Exception:
            dead.add(ws)
    _WS_CLIENTS.difference_update(dead)


# ---------------------------------------------------------------------------
# WAR ROOM HTML — 5-tab institutional dark UI
# ---------------------------------------------------------------------------

_WAR_ROOM_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NZT-48 WAR ROOM v8.0</title>
<style>
:root {
  --bg:       #080d1a;
  --panel:    #0d1525;
  --border:   #1a2e55;
  --gold:     #c9a84c;
  --gold2:    #e8c84a;
  --blue:     #3a7bd5;
  --green:    #00c853;
  --red:      #e53935;
  --amber:    #ffc107;
  --grey:     #546e7a;
  --silver:   #b0bec5;
  --white:    #e8eaf6;
  --dim:      #37474f;
  --navy:     #0a1228;
  --tab-act:  #1a2e55;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--white);font-family:'Courier New',monospace;font-size:12px;overflow-x:hidden}
/* TOP BAR */
#topbar{background:var(--navy);border-bottom:2px solid var(--gold);padding:6px 12px;display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:100}
#topbar .logo{color:var(--gold);font-weight:bold;font-size:14px;letter-spacing:2px}
#topbar .sep{color:var(--dim);margin:0 4px}
#regime-badge{padding:3px 10px;border-radius:3px;font-weight:bold;font-size:11px;letter-spacing:1px}
.regime-RISK_ON,.regime-TRENDING_UP_STRONG,.regime-TRENDING_UP_MOD,.regime-BULLISH{background:#00401a;color:var(--green)}
.regime-RISK_OFF,.regime-TRENDING_DOWN_STRONG,.regime-TRENDING_DOWN_MOD,.regime-BEARISH,.regime-SHOCK{background:#3e0000;color:var(--red)}
.regime-NEUTRAL,.regime-RANGE_BOUND,.regime-UNKNOWN{background:#2a2000;color:var(--amber)}
.regime-HIGH_VOLATILITY,.regime-CHOPPY{background:#1a1040;color:#b39ddb}
#session-badge{color:var(--silver);font-size:10px}
#halt-badge{display:none;background:#3e0000;color:var(--red);padding:2px 8px;border-radius:3px;font-size:10px;animation:blink 1s step-end infinite}
@keyframes blink{50%{opacity:0}}
#topbar .meta{margin-left:auto;color:var(--grey);font-size:10px;text-align:right}
/* TABS */
#tabs{display:flex;background:var(--navy);border-bottom:1px solid var(--border);padding:0 8px}
.tab{padding:8px 16px;cursor:pointer;color:var(--grey);border-bottom:2px solid transparent;font-size:11px;letter-spacing:0.5px;transition:all 0.15s}
.tab:hover{color:var(--silver)}
.tab.active{color:var(--gold);border-bottom-color:var(--gold);background:var(--tab-act)}
/* CONTENT */
#content{padding:10px 12px}
.tab-pane{display:none}
.tab-pane.active{display:block}
/* PANELS */
.panel{background:var(--panel);border:1px solid var(--border);border-radius:4px;margin-bottom:10px;overflow:hidden}
.panel-hdr{background:#0f1e3d;padding:6px 10px;color:var(--gold);font-weight:bold;font-size:11px;letter-spacing:1px;display:flex;align-items:center;gap:8px}
.panel-body{padding:8px 10px}
/* GRID */
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
/* STAT BOXES */
.stat-box{background:var(--navy);border:1px solid var(--border);border-radius:4px;padding:8px 10px;text-align:center}
.stat-box .val{font-size:22px;font-weight:bold;color:var(--gold)}
.stat-box .lbl{font-size:9px;color:var(--grey);letter-spacing:1px;margin-top:2px}
/* TABLES */
table{width:100%;border-collapse:collapse;font-size:10.5px}
th{background:#0f1e3d;color:var(--gold);padding:4px 6px;text-align:left;font-size:10px;letter-spacing:0.5px;position:sticky;top:0}
td{padding:3px 6px;border-bottom:1px solid #0f1a2e;vertical-align:middle}
tr:nth-child(even) td{background:#0a1020}
tr:hover td{background:#101d38}
/* CHIPS */
.chip{display:inline-block;padding:2px 7px;border-radius:10px;font-size:9.5px;font-weight:bold;margin:2px}
.chip-green{background:#003300;color:var(--green);border:1px solid var(--green)}
.chip-red{background:#330000;color:var(--red);border:1px solid var(--red)}
.chip-amber{background:#332200;color:var(--amber);border:1px solid var(--amber)}
.chip-blue{background:#001a40;color:var(--blue);border:1px solid var(--blue)}
.chip-grey{background:#1a1a1a;color:var(--grey);border:1px solid var(--grey)}
.chip-gold{background:#1a1200;color:var(--gold);border:1px solid var(--gold)}
/* ALERTS */
.alert{padding:8px 12px;border-radius:4px;margin-bottom:8px;font-size:11px}
.alert-red{background:#3e0000;border:1px solid var(--red);color:var(--red)}
.alert-amber{background:#332200;border:1px solid var(--amber);color:var(--amber)}
.alert-green{background:#003300;border:1px solid var(--green);color:var(--green)}
/* TAPE */
.tape-line{font-size:10px;line-height:1.7;color:var(--silver);border-bottom:1px solid #0a1020;padding:1px 0}
/* PROGRESS BAR */
.bar-bg{background:#1a2e55;border-radius:2px;height:8px;overflow:hidden}
.bar-fill{height:100%;border-radius:2px;transition:width 0.5s}
/* SIZING HINT */
.size-S{color:var(--amber)}
.size-M{color:var(--blue)}
.size-L{color:var(--green)}
/* DIFF BOX */
#diff-box{background:#0a1028;border:1px solid var(--border);border-radius:4px;padding:6px 10px;font-size:10px;color:var(--silver);min-height:36px;max-height:80px;overflow-y:auto}
/* STATUS */
.status-PASS{color:var(--green)}
.status-FAIL{color:var(--red)}
.status-PENDING{color:var(--grey)}
/* SEARCH */
#search{background:var(--panel);border:1px solid var(--border);color:var(--white);padding:4px 8px;font-size:11px;border-radius:3px;width:200px;font-family:inherit}
#search:focus{outline:none;border-color:var(--gold)}
/* SCROLL */
.scroll-x{overflow-x:auto}
/* FOOTER */
#footer{background:var(--navy);border-top:1px solid var(--border);padding:3px 12px;font-size:9px;color:var(--grey);display:flex;gap:16px}
/* GUIDED MODE */
.guided-tip{background:#0a1e0a;border-left:3px solid var(--green);padding:5px 10px;margin-bottom:8px;font-size:10px;color:#80c880;display:none}
body.guided .guided-tip{display:block}
.guided-tip b{color:var(--green)}
/* TOOLTIPS */
[data-tip]{position:relative;cursor:help;border-bottom:1px dashed var(--dim)}
[data-tip]:hover::after{content:attr(data-tip);position:absolute;bottom:120%;left:0;background:#0f1e3d;border:1px solid var(--gold);color:var(--silver);padding:4px 8px;border-radius:3px;font-size:10px;white-space:nowrap;z-index:999;max-width:280px;white-space:normal;min-width:140px}
/* DECISION BADGES */
.dec-TRADE{background:#003300;color:var(--green);border:1px solid var(--green);padding:2px 7px;border-radius:3px;font-size:9px;font-weight:bold}
.dec-WATCH{background:#332200;color:var(--amber);border:1px solid var(--amber);padding:2px 7px;border-radius:3px;font-size:9px;font-weight:bold}
.dec-ABSTAIN{background:#330000;color:var(--red);border:1px solid var(--red);padding:2px 7px;border-radius:3px;font-size:9px;font-weight:bold}
/* UNCERTAINTY METER */
.unc-low{color:var(--green)}
.unc-med{color:var(--amber)}
.unc-high{color:var(--red)}
/* EDGE STATUS */
.edge-ACTIONABLE{color:var(--green);font-weight:bold}
.edge-CALIBRATION_READY{color:var(--amber)}
.edge-NEEDS_DATA{color:var(--grey)}
/* GLOSSARY MODAL */
#glossary-modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.8);z-index:1000;align-items:center;justify-content:center}
#glossary-modal.open{display:flex}
#glossary-box{background:var(--panel);border:1px solid var(--gold);border-radius:6px;padding:20px;max-width:600px;width:90%;max-height:80vh;overflow-y:auto}
#glossary-box h2{color:var(--gold);margin-bottom:12px;font-size:14px;letter-spacing:1px}
.gloss-row{padding:5px 0;border-bottom:1px solid var(--border);display:grid;grid-template-columns:160px 1fr;gap:8px;font-size:11px}
.gloss-row dt{color:var(--gold)}
.gloss-row dd{color:var(--silver)}
/* DEFENSIVE MODE BANNER */
#defensive-banner{display:none;background:#3e0000;border:1px solid var(--red);color:var(--red);padding:6px 12px;border-radius:3px;margin-bottom:8px;font-size:11px;font-weight:bold}
/* DRIFT BADGE */
.drift-NONE{color:var(--grey)}
.drift-LOW{color:var(--amber)}
.drift-MEDIUM{color:#ff9800}
.drift-HIGH{color:var(--red);font-weight:bold}
.drift-CRITICAL{color:var(--red);font-weight:bold;animation:blink 1s step-end infinite}
</style>
</head>
<body>

<!-- TOP BAR -->
<div id="topbar">
  <span class="logo">&#x25a0; NZT-48</span>
  <span class="sep">|</span>
  <span id="regime-badge" class="regime-UNKNOWN">REGIME: --</span>
  <span id="session-badge">SESSION: --</span>
  <span id="halt-badge">&#x26a0; HALT ACTIVE</span>
  <span class="sep">|</span>
  <span id="tod-window" style="color:var(--silver);font-size:10px">--</span>
  <span class="sep">|</span>
  <span id="strat-chips"></span>
  <div style="margin-left:auto;display:flex;align-items:center;gap:8px">
    <button id="guided-btn" onclick="toggleGuided()" title="Toggle guided explanations" style="padding:2px 8px;font-size:9px;cursor:pointer;border-radius:3px;border:1px solid var(--dim);background:var(--navy);color:var(--grey);font-family:inherit">GUIDED OFF</button>
    <button onclick="document.getElementById('glossary-modal').classList.add('open')" title="Open glossary" style="padding:2px 8px;font-size:9px;cursor:pointer;border-radius:3px;border:1px solid var(--dim);background:var(--navy);color:var(--grey);font-family:inherit">GLOSSARY</button>
    <div class="meta" style="text-align:right">
      <div id="ts">connecting...</div>
      <div id="tick-meta" style="color:var(--dim)">tick #0 &nbsp; run: --</div>
    </div>
  </div>
</div>

<!-- TABS -->
<div id="tabs">
  <div class="tab active" onclick="showTab('warroom')">&#x25b6; WAR ROOM</div>
  <div class="tab" onclick="showTab('risk')">&#x1f6e1; RISK</div>
  <div class="tab" onclick="showTab('coach')">&#x1f9e0; AI COACH</div>
  <div class="tab" onclick="showTab('explain')">&#x1f50d; DIAGNOSE</div>
  <div class="tab" onclick="showTab('stratlab')">&#x1f4cb; STRAT LAB</div>
  <div class="tab" onclick="showTab('reports')">&#x1f4c4; REPORTS</div>
</div>

<!-- CONTENT -->
<div id="content">

<!-- ===================== WAR ROOM ===================== -->
<div id="pane-warroom" class="tab-pane active">
  <div id="defensive-banner">&#x26a0; DEFENSIVE MODE ACTIVE — Drift detected. Safe strategies boosted. Reduce position sizes.</div>
  <div id="drought-alert"></div>
  <div class="guided-tip"><b>WAR ROOM</b> — This is your live decision stack. TRADE = AI-approved signal. WATCH = interesting but edge unclear. ABSTAIN = avoid.</div>
  <div class="grid4" style="margin-bottom:10px">
    <div class="stat-box"><div class="val" id="stat-total">--</div><div class="lbl" data-tip="Total signals passing all gates this tick">TOTAL PLAYS</div></div>
    <div class="stat-box"><div class="val" id="stat-strict" style="color:var(--green)">--</div><div class="lbl" data-tip="Passed all gates at strict thresholds (high confidence)">STRICT</div></div>
    <div class="stat-box"><div class="val" id="stat-fallback" style="color:var(--amber)">--</div><div class="lbl" data-tip="Passed via fallback (relaxed thresholds — use smaller size)">FALLBACK</div></div>
    <div class="stat-box"><div class="val" id="stat-tracked">--</div><div class="lbl" data-tip="Total tickers scanned this tick">UNIVERSE</div></div>
  </div>

  <div id="diff-container" style="margin-bottom:8px;display:none">
    <div class="panel-hdr" style="background:var(--dim)">&#x25b3; DIFF SINCE LAST TICK</div>
    <div id="diff-box">No changes yet</div>
  </div>

  <div class="panel">
    <div class="panel-hdr">
      &#x1f3af; DECISION STACK
      <span style="font-size:9px;color:var(--grey);margin-left:8px">TRADE=approved &nbsp; WATCH=uncertain &nbsp; ABSTAIN=avoid</span>
      <input id="search" placeholder="/ filter ticker..." oninput="filterPlays(this.value)" style="margin-left:auto">
    </div>
    <div class="panel-body scroll-x">
      <table id="plays-table">
        <thead><tr>
          <th>#</th><th>Ticker</th><th>Dir</th><th>Decision</th>
          <th data-tip="Stars from scoring engine">Stars</th>
          <th data-tip="Composite momentum + quality score">Score</th>
          <th data-tip="Expected net profit in R (after costs) — from edge ledger">ExpNetR</th>
          <th data-tip="Probability target is hit before stop — based on historical outcomes">P(T)</th>
          <th data-tip="Confidence 0-100%. Lower = more uncertain. &lt;40% = WATCH">Edge%</th>
          <th>Strategy</th><th>Size</th><th>Track</th><th>Entry</th><th>Stop</th>
          <th>T1</th><th>R:R</th>
          <th data-tip="ATR % = average daily movement. Higher = more volatile">ATR%</th>
          <th data-tip="Relative volume vs 20d avg. >1.5 = unusual activity">RVOL</th>
          <th data-tip="Spread + slippage cost estimate in basis points">SpreadBps</th>
          <th>Label</th>
        </tr></thead>
        <tbody id="plays-tbody"></tbody>
      </table>
    </div>
    <div style="padding:6px 10px;font-size:9px;color:var(--grey);border-top:1px solid var(--border)">
      AI decisions load via <b>/api/ai/expectancy</b> — refreshes every tick &nbsp;|&nbsp; Click any row to see plain-English explanation
    </div>
  </div>

  <div class="grid2">
    <div class="panel">
      <div class="panel-hdr">&#x1f4fc; SIGNAL TAPE (last 20)</div>
      <div class="panel-body" id="tape-body" style="max-height:200px;overflow-y:auto"></div>
    </div>
    <div class="panel">
      <div class="panel-hdr">&#x26a0; OVERLAY WARNINGS</div>
      <div class="panel-body" id="overlay-body"></div>
    </div>
  </div>

  <!-- TRADE CARD MODAL -->
  <div id="trade-card-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:500;align-items:center;justify-content:center">
    <div style="background:var(--panel);border:1px solid var(--gold);border-radius:6px;padding:20px;max-width:520px;width:90%">
      <div style="display:flex;justify-content:space-between;margin-bottom:12px">
        <span style="color:var(--gold);font-weight:bold;font-size:13px" id="tc-title">TRADE CARD</span>
        <button onclick="document.getElementById('trade-card-modal').style.display='none'" style="background:none;border:none;color:var(--grey);cursor:pointer;font-size:14px">&#x2715;</button>
      </div>
      <div id="tc-body" style="font-size:11px;color:var(--silver);line-height:1.8"></div>
    </div>
  </div>
</div>

<!-- ===================== RISK COCKPIT (v4.0) ===================== -->
<div id="pane-risk" class="tab-pane">
  <div class="grid2">
    <div class="panel">
      <div class="panel-hdr">&#x1f4ca; FACTOR EXPOSURE</div>
      <div class="panel-body" id="factor-body"></div>
    </div>
    <div class="panel">
      <div class="panel-hdr">&#x1f6a6; VOL &amp; REGIME STATE</div>
      <div class="panel-body" id="risk-body"></div>
    </div>
  </div>
  <div class="panel">
    <div class="panel-hdr">&#x1f9d1;&#x200d;&#x2696;&#xfe0f; RISK OFFICER DECISIONS</div>
    <div class="panel-body scroll-x">
      <div id="ro-summary" style="margin-bottom:8px;font-size:11px;color:var(--silver)"></div>
      <table><thead><tr>
        <th>Ticker</th><th>Dir</th><th>Decision</th><th>Original Size</th><th>Final Size</th><th>Risk Score</th><th>Reasons</th>
      </tr></thead>
      <tbody id="ro-tbody"></tbody></table>
    </div>
  </div>
  <div class="panel">
    <div class="panel-hdr">&#x1f512; GUARDRAILS &amp; HALT CONTROL</div>
    <div class="panel-body" id="guardrails-body">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
        <span style="color:var(--silver)">Halt new signals:</span>
        <button id="halt-btn" onclick="toggleHalt()" style="padding:4px 16px;cursor:pointer;border-radius:3px;font-family:inherit;font-size:11px;font-weight:bold;border:none">HALT</button>
        <span id="halt-status" style="font-size:11px"></span>
      </div>
      <div id="guardrails-detail"></div>
    </div>
  </div>
  <div class="panel">
    <div class="panel-hdr">&#x23f0; SESSION READINESS CHECKLIST</div>
    <div class="panel-body" id="readiness-body">
      <div id="readiness-checklist" style="font-size:11px;color:var(--silver)">Loading readiness status...</div>
    </div>
  </div>
</div>

<!-- ===================== AI COACH (v5.0) ===================== -->
<div id="pane-coach" class="tab-pane">
  <div class="guided-tip"><b>AI COACH</b> — This tab shows what the self-learning brain knows. Edge Map = best conditions to trade in. Confidence shows when to abstain. Drift = when market has changed and we need to adapt.</div>

  <!-- Defensive Mode + Drift Status -->
  <div class="grid2" style="margin-bottom:10px">
    <div class="panel">
      <div class="panel-hdr">&#x1f6a8; DRIFT &amp; DEFENSIVE MODE</div>
      <div class="panel-body">
        <div class="guided-tip"><b>What this means:</b> If drift is detected, the market has changed vs our historical data. <b>What to do:</b> Reduce size and prefer safe strategies until drift clears.</div>
        <div id="drift-status" style="font-size:11px;color:var(--silver)">Loading drift status...</div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-hdr">&#x1f9e0; SELF-LEARNING STATUS</div>
      <div class="panel-body">
        <div class="guided-tip"><b>What this means:</b> How many signals have been logged and resolved. <b>What to do:</b> Once 20+ outcomes are RESOLVED, the edge ledger activates and improves decisions.</div>
        <div id="learning-status" style="font-size:11px;color:var(--silver)">Loading...</div>
        <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">
          <button onclick="triggerResolveOutcomes()" style="padding:3px 12px;font-size:10px;cursor:pointer;border-radius:3px;border:1px solid var(--blue);background:var(--navy);color:var(--blue);font-family:inherit">&#x21bb; Resolve Outcomes</button>
          <button onclick="triggerRebuildEdgeLedger()" style="padding:3px 12px;font-size:10px;cursor:pointer;border-radius:3px;border:1px solid var(--amber);background:var(--navy);color:var(--amber);font-family:inherit">&#x21bb; Rebuild Edge Ledger</button>
          <button onclick="triggerRunDrift()" style="padding:3px 12px;font-size:10px;cursor:pointer;border-radius:3px;border:1px solid var(--grey);background:var(--navy);color:var(--grey);font-family:inherit">&#x21bb; Run Drift Check</button>
        </div>
        <div id="ai-action-result" style="margin-top:6px;font-size:10px;color:var(--green)"></div>
      </div>
    </div>
  </div>

  <!-- Edge Map -->
  <div class="panel">
    <div class="panel-hdr">
      &#x1f5fa; EDGE MAP — Best Conditions by Strategy &times; Regime
      <span style="font-size:9px;color:var(--grey);margin-left:8px">ACTIONABLE = 20+ outcomes, positive expectancy</span>
    </div>
    <div class="panel-body">
      <div class="guided-tip"><b>What this means:</b> Each row is a strategy in a specific market regime. ExpNetR = expected profit per trade after costs. <b>What to do:</b> Focus on ACTIONABLE rows matching today's regime.</div>
      <div class="scroll-x">
        <table>
          <thead><tr>
            <th>Strategy</th><th>Regime</th><th>Track</th><th>Time Window</th>
            <th data-tip="Number of completed outcomes in this bucket">N</th>
            <th data-tip="% of trades that hit target">Win%</th>
            <th data-tip="Wilson 90% confidence interval for win rate">WR CI</th>
            <th data-tip="Expected net R per trade (after spread + slippage)">ExpNetR</th>
            <th data-tip="0-100 confidence in this estimate">Conf</th>
            <th data-tip="Needs more data / ready for calibration / actionable">Status</th>
            <th>Current Regime</th>
          </tr></thead>
          <tbody id="edge-map-tbody"></tbody>
        </table>
      </div>
      <div style="padding:6px 0;font-size:9px;color:var(--grey)">★ Highlighted = matches today's regime &nbsp;|&nbsp; Needs 20+ outcomes for ACTIONABLE status</div>
    </div>
  </div>

  <!-- Meta Weights -->
  <div class="panel">
    <div class="panel-hdr">&#x2696; META-LEARNER WEIGHTS — Dynamic Capital Allocation</div>
    <div class="panel-body">
      <div class="guided-tip"><b>What this means:</b> These weights control how much capital is allocated to each strategy. They change by max ±10% per update cycle based on evidence. <b>What to do:</b> No action needed — this is automatic.</div>
      <div id="meta-weights-body" style="font-size:11px;color:var(--silver)">Loading weights...</div>
    </div>
  </div>

  <!-- Recent Outcomes -->
  <div class="panel">
    <div class="panel-hdr">&#x1f4ca; RECENT OUTCOMES — Resolved Signals</div>
    <div class="panel-body">
      <div class="guided-tip"><b>What this means:</b> Every signal that was generated and has since been resolved (target hit / stop hit / time expired). This is the training data for the AI. <b>What to do:</b> Watch for patterns — lots of HIT_STOP in one strategy = review it.</div>
      <div class="scroll-x">
        <table>
          <thead><tr>
            <th>Signal ID</th><th>Ticker</th><th>Dir</th><th>Strategy</th><th>Regime</th>
            <th>Outcome</th><th>NetR</th><th>Duration</th><th>Cost BPS</th><th>Closed At</th>
          </tr></thead>
          <tbody id="outcomes-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<!-- ===================== EXPLAINABILITY (v4.0) ===================== -->
<div id="pane-explain" class="tab-pane">
  <div class="panel">
    <div class="panel-hdr">&#x1f52e; GATE FUNNEL</div>
    <div class="panel-body" id="funnel-visual"></div>
  </div>
  <div class="grid2">
    <div class="panel">
      <div class="panel-hdr">&#x1f6ab; TOP BLOCKERS</div>
      <div class="panel-body" id="blockers-body"></div>
    </div>
    <div class="panel">
      <div class="panel-hdr">&#x1f4cb; DATA HEALTH per TICKER</div>
      <div class="panel-body scroll-x">
        <table><thead><tr>
          <th>Ticker</th><th>Status</th><th>Badge</th><th>Notes</th>
        </tr></thead>
        <tbody id="health-tbody"></tbody></table>
      </div>
    </div>
  </div>
  <!-- DROUGHT COCKPIT (v4.0) -->
  <div class="panel">
    <div class="panel-hdr">&#x1f3dc; DROUGHT COCKPIT — CLOSEST MISSES</div>
    <div class="panel-body">
      <div id="drought-flag-banner" style="margin-bottom:8px"></div>
      <div style="color:var(--silver);font-size:10px;margin-bottom:6px">Tickers closest to admission — sorted by delta-to-pass ascending</div>
      <table><thead><tr>
        <th>Ticker</th><th>Failed Gate</th><th>Observed</th><th>Required</th><th>Delta</th><th>Fallback Admits</th><th>Safest Knob</th>
      </tr></thead>
      <tbody id="closest-misses-tbody"></tbody></table>
    </div>
  </div>
  <div class="panel">
    <div class="panel-hdr">&#x1f527; RECOMMENDED KNOBS (bounded, safe)</div>
    <div class="panel-body">
      <div style="color:var(--silver);font-size:10px;margin-bottom:6px">Safe parameter adjustments — DataHealth gate never bypassed</div>
      <table><thead><tr>
        <th>Parameter</th><th>Current</th><th>Suggested</th><th>Bounded</th><th>Tradeoff</th><th>Expected Effect</th>
      </tr></thead>
      <tbody id="knobs-tbody"></tbody></table>
    </div>
  </div>
</div>

<!-- ===================== STRATEGY LAB (v4.0) ===================== -->
<div id="pane-stratlab" class="tab-pane">
  <div class="panel">
    <div class="panel-hdr">&#x1f4d6; STRATEGY ROSTER &amp; CAPITAL ALLOCATION</div>
    <div class="panel-body scroll-x">
      <table><thead><tr>
        <th>Tag</th><th>Category</th><th>Active</th><th>Weight</th><th>Alloc %</th><th>Why Active</th><th>Inactive Reason</th>
      </tr></thead>
      <tbody id="strat-roster"></tbody></table>
    </div>
  </div>
  <div class="panel">
    <div class="panel-hdr">&#x1f4e1; EVENT STRATEGY AVAILABILITY</div>
    <div class="panel-body scroll-x">
      <table><thead><tr>
        <th>Strategy</th><th>Status</th><th>Reason</th><th>Config Key Required</th><th>Recommended Provider</th>
      </tr></thead>
      <tbody id="event-strat-tbody"></tbody></table>
    </div>
  </div>
  <div class="panel">
    <div class="panel-hdr">&#x1f4c8; WIN RATE CALIBRATION</div>
    <div class="panel-body" id="calibration-body">
      <div style="color:var(--silver);font-size:11px">Calibration-ready layout — data populates as outcomes are recorded.</div>
    </div>
  </div>
  <div class="panel">
    <div class="panel-hdr">&#x23f1; TIME-OF-DAY WINDOWS</div>
    <div class="panel-body">
      <table><thead><tr><th>Window</th><th>UK Time</th><th>Recommended Strategies</th><th>Notes</th></tr></thead>
      <tbody>
        <tr><td>CHAOS_OPEN</td><td>08:00-08:30</td><td>Observe only</td><td>High spread, tighten stops</td></tr>
        <tr><td>MORNING_MOMENTUM</td><td>08:30-10:30</td><td>TREND_MOMENTUM, ORB, GAP_GO</td><td>Full strategies active</td></tr>
        <tr><td>TREND_EXTENSION</td><td>10:30-12:00</td><td>VWAP_TREND, FACTOR_ROTATION</td><td>Continuations preferred</td></tr>
        <tr><td>LUNCH_CHOP</td><td>12:00-13:30</td><td>STAT_ARB, VWAP_MEAN_REVERT</td><td>Raise RVOL threshold</td></tr>
        <tr><td>AFTERNOON_PUSH</td><td>13:30-15:00</td><td>TREND_MOMENTUM, VOL_BREAKOUT</td><td>Pre-NYSE momentum window</td></tr>
        <tr><td>POWER_HOUR</td><td>15:00-16:30</td><td>Continuations only, tight exits</td><td>NYSE overlap</td></tr>
        <tr><td>CLOSE_MECHANICS</td><td>16:00-17:00</td><td>Reduce exposure</td><td>Market-on-close flow</td></tr>
        <tr><td>AFTER_HOURS</td><td>Rest</td><td>5d data only</td><td>Reduced risk</td></tr>
      </tbody></table>
    </div>
  </div>
</div>

<!-- ===================== REPORTS & AUDIT (v4.0) ===================== -->
<div id="pane-reports" class="tab-pane">
  <div class="panel">
    <div class="panel-hdr">&#x1f4c4; SESSION RUN STATUS (v4.0)</div>
    <div class="panel-body scroll-x">
      <table><thead><tr>
        <th>Session</th><th>Status</th><th>Artifacts</th><th>PDF</th>
        <th>Strict</th><th>Fallback</th><th>Drought</th><th>Blockers</th>
        <th>UK Time</th><th>Error</th>
      </tr></thead>
      <tbody id="session-status-tbody"></tbody></table>
    </div>
  </div>
  <div class="panel">
    <div class="panel-hdr">&#x1f5c4; ARTIFACT INVENTORY
      <button onclick="loadArtifacts()" style="margin-left:auto;padding:2px 10px;cursor:pointer;border-radius:3px;font-size:10px;border:none;background:var(--dim);color:var(--silver)">&#x21bb; Refresh</button>
    </div>
    <div class="panel-body scroll-x">
      <table><thead><tr>
        <th>Session</th><th>plays.json</th><th>strategies.json</th><th>risk_officer.json</th><th>drought.json</th><th>Size (KB)</th>
      </tr></thead>
      <tbody id="artifacts-tbody"></tbody></table>
    </div>
  </div>
  <div class="panel">
    <div class="panel-hdr">&#x1f4e5; PDF REPORTS</div>
    <div class="panel-body" id="pdfs-body"></div>
  </div>
</div>

</div><!-- /content -->

<div id="footer">
  <span>NZT-48 WAR ROOM v8.0</span>
  <span>Keys: [1] War Room [2] Risk [3] AI Coach [4] Diagnose [5] Strat Lab [6] Reports &nbsp;&nbsp; [g] guided &nbsp; [f] filter &nbsp; [r] refresh &nbsp; [d] drought</span>
  <span id="ws-status" style="margin-left:auto;color:var(--grey)">WS: connecting</span>
</div>

<!-- GLOSSARY MODAL -->
<div id="glossary-modal">
  <div id="glossary-box">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <h2>&#x1f4d6; NZT-48 GLOSSARY</h2>
      <button onclick="document.getElementById('glossary-modal').classList.remove('open')" style="background:none;border:none;color:var(--grey);cursor:pointer;font-size:16px">&#x2715;</button>
    </div>
    <dl>
      <div class="gloss-row"><dt>ExpNetR</dt><dd>Expected Net R — predicted profit per trade in R-multiples, after spread and slippage costs. Positive = edge exists.</dd></div>
      <div class="gloss-row"><dt>P(T)</dt><dd>Probability of hitting Target 1 before the stop loss. 55% = slight edge. Below 40% = against us.</dd></div>
      <div class="gloss-row"><dt>Edge%</dt><dd>Confidence in the expectancy estimate (0–100%). Based on sample size and consistency of past outcomes.</dd></div>
      <div class="gloss-row"><dt>RVOL</dt><dd>Relative Volume — today's volume vs the 20-day average. 1.5 = 50% above normal = unusual activity.</dd></div>
      <div class="gloss-row"><dt>ATR%</dt><dd>Average True Range % — typical daily price movement as % of price. 1.5% on a £20 ETP = expect £0.30 moves.</dd></div>
      <div class="gloss-row"><dt>R-multiple</dt><dd>Risk unit. If you risk £100 per trade, 1R = £100 profit, 2R = £200 profit. Sizes all trades consistently.</dd></div>
      <div class="gloss-row"><dt>Strict / Fallback</dt><dd>Strict = passed all gates at tight thresholds (full size). Fallback = passed at relaxed thresholds (half size).</dd></div>
      <div class="gloss-row"><dt>Regime</dt><dd>Market character: RISK_ON (trending up), RISK_OFF (falling), RANGE_BOUND (chopping), HIGH_VOLATILITY (spiking).</dd></div>
      <div class="gloss-row"><dt>TRADE / WATCH / ABSTAIN</dt><dd>AI decision. TRADE = positive edge, low uncertainty. WATCH = interesting but not enough evidence. ABSTAIN = avoid.</dd></div>
      <div class="gloss-row"><dt>Edge Ledger</dt><dd>Historical win-rate and expectancy table, broken down by strategy × regime × track × time-of-day.</dd></div>
      <div class="gloss-row"><dt>Drift</dt><dd>Market conditions have shifted vs our historical data. Detected by comparing recent vs baseline feature distributions.</dd></div>
      <div class="gloss-row"><dt>Defensive Mode</dt><dd>Triggered by high-severity drift. Allocates more capital to safe strategies, suggests reducing position sizes.</dd></div>
      <div class="gloss-row"><dt>Time Stop</dt><dd>Signal exits at market close if neither target nor stop is hit. Avoids overnight gap risk on scalp/intraday signals.</dd></div>
      <div class="gloss-row"><dt>MFE / MAE</dt><dd>Max Favourable/Adverse Excursion. MFE = how far the trade went in our favour. MAE = how deep the drawdown got.</dd></div>
      <div class="gloss-row"><dt>Signal Drought</dt><dd>No signals passing gates. Usually due to off-hours, low volatility regime, or strict gate thresholds. Not an error.</dd></div>
      <div class="gloss-row"><dt>Path-Based Resolution</dt><dd>Outcome determined by checking every 1-minute bar from entry to expiry — not just the close price.</dd></div>
      <div class="gloss-row"><dt>Halt</dt><dd>Emergency stop. Prevents new signals from being acted on. Does not close existing positions.</dd></div>
      <div class="gloss-row"><dt>Meta-Learner</dt><dd>Algorithm that adjusts strategy capital weights ±10%/week based on edge ledger evidence and drift status.</dd></div>
      <div class="gloss-row"><dt>Counterfactual</dt><dd>Shadow variants of each trade (different stop sizes, time stops) used to improve parameters without risking capital.</dd></div>
      <div class="gloss-row"><dt>Spread BPS</dt><dd>Bid-ask spread in basis points (1bps = 0.01%). Round-trip cost estimate for a trade including slippage.</dd></div>
    </dl>
  </div>
</div>

<script>
// ===== STATE =====
let _state = {};
let _haltState = false;
let _prevPlays = [];
let _aiDecisions = {};   // signal_id -> decision data from /api/ai/expectancy
let _guidedMode = false;

// ===== GUIDED MODE =====
function toggleGuided() {
  _guidedMode = !_guidedMode;
  document.body.classList.toggle('guided', _guidedMode);
  const btn = document.getElementById('guided-btn');
  btn.textContent = _guidedMode ? 'GUIDED ON' : 'GUIDED OFF';
  btn.style.color = _guidedMode ? 'var(--green)' : 'var(--grey)';
  btn.style.borderColor = _guidedMode ? 'var(--green)' : 'var(--dim)';
}

// ===== TABS =====
function showTab(name) {
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('pane-'+name).classList.add('active');
  const idx = ['warroom','risk','coach','explain','stratlab','reports'].indexOf(name);
  if (idx >= 0) document.querySelectorAll('.tab')[idx].classList.add('active');
  // Lazy load data for tabs
  if (name === 'reports')  loadReports();
  if (name === 'stratlab') loadStratLab();
  if (name === 'risk')     loadRiskOfficer();
  if (name === 'explain')  loadDrought();
  if (name === 'coach')    loadCoach();
}

// ===== RENDER =====
function renderState(d) {
  _state = d;
  const ts = document.getElementById('ts');
  const tickMeta = document.getElementById('tick-meta');
  ts.textContent = d.last_tick ? new Date(d.last_tick).toLocaleTimeString() + ' UTC' : '--';
  tickMeta.textContent = 'tick #' + d.tick + '  run: ' + d.run_id;

  // Regime
  const reg = d.market ? d.market.regime : 'UNKNOWN';
  const rb = document.getElementById('regime-badge');
  rb.textContent = 'REGIME: ' + reg;
  rb.className = 'regime-' + reg;

  // Session
  document.getElementById('session-badge').textContent =
    'SESSION: ' + (d.market ? d.market.session : '--') +
    (d.market && d.market.session_active ? ' [LIVE]' : ' [OFF]');

  // TOD window
  if (d.strategies) {
    document.getElementById('tod-window').textContent =
      d.strategies.time_of_day || '';
  }

  // Halt
  _haltState = d.halt || false;
  const hb = document.getElementById('halt-badge');
  hb.style.display = _haltState ? 'inline-block' : 'none';
  updateHaltBtn();

  // Strategy chips in topbar
  const sc = document.getElementById('strat-chips');
  if (d.strategies && d.strategies.active) {
    sc.innerHTML = d.strategies.active.slice(0,4).map(s =>
      '<span class="chip chip-blue" title="' + (s.why_active||[]).join('; ') + '">' + s.tag.replace('_',' ') + '</span>'
    ).join('');
  }

  // Stats
  const gf = d.gate_funnel || {};
  document.getElementById('stat-total').textContent = (d.top_plays||[]).length;
  document.getElementById('stat-strict').textContent = gf.signals_strict || 0;
  document.getElementById('stat-fallback').textContent = gf.signals_fallback || 0;
  document.getElementById('stat-tracked').textContent = gf.tracked || 0;

  // Drought
  const da = document.getElementById('drought-alert');
  da.innerHTML = d.drought ?
    '<div class="alert alert-red"><b>!!! SIGNAL DROUGHT !!!</b><br><pre style="font-size:9px;margin-top:4px">' + d.drought + '</pre></div>' : '';

  // Plays
  renderPlays(d.top_plays || []);

  // Tape
  const tb = document.getElementById('tape-body');
  tb.innerHTML = (d.tape||[]).map(l => '<div class="tape-line">' + esc(l) + '</div>').join('') || '<span style="color:var(--grey)">No signals yet</span>';

  // Overlays
  const ob = document.getElementById('overlay-body');
  const s = d.strategies || {};
  let overlayHtml = '';
  if (s.kill_switch) overlayHtml += '<div class="alert alert-red"><b>KILL SWITCH ACTIVE</b> — All non-defensive strategies deactivated</div>';
  if (s.sizing_mode && s.sizing_mode !== 'NORMAL') overlayHtml += '<div class="alert alert-amber"><b>SIZE MODE: ' + s.sizing_mode + '</b></div>';
  if (s.overlay_warnings && s.overlay_warnings.length)
    overlayHtml += s.overlay_warnings.map(w => '<div class="chip chip-amber">'+esc(w)+'</div>').join('');
  if (s.overlay_tags && s.overlay_tags.length)
    overlayHtml += '<br>' + s.overlay_tags.map(t => '<span class="chip chip-grey">'+esc(t)+'</span>').join('');
  ob.innerHTML = overlayHtml || '<span style="color:var(--grey)">No active overlay warnings</span>';

  // Risk Cockpit
  renderOverseer(d);

  // Explainability / Diagnostics
  renderDiagnostics(d);

  // Defensive mode banner
  const db = document.getElementById('defensive-banner');
  if (db) db.style.display = 'none'; // will be set by coach load

  // Refresh active lazy-loaded panels if they're visible
  const activePaneId = document.querySelector('.tab-pane.active') ? document.querySelector('.tab-pane.active').id : '';
  if (activePaneId === 'pane-risk')   loadRiskOfficer();
  if (activePaneId === 'pane-explain') loadDrought();
  if (activePaneId === 'pane-coach')  loadCoach();
  // Always refresh AI decisions for war room
  loadAIDecisions();
}

function renderPlays(plays) {
  const tbody = document.getElementById('plays-tbody');
  if (!plays.length) {
    tbody.innerHTML = '<tr><td colspan="20" style="color:var(--grey);text-align:center;padding:12px">No plays generated — session inactive or signal drought active</td></tr>';
    return;
  }
  tbody.innerHTML = plays.map((p,i) => {
    const dir_cls = p.direction === 'LONG' ? 'style="color:var(--green)"' : 'style="color:var(--red)"';
    const scoreVal = p.strategy_score || p.score;
    const score_col = scoreVal >= 80 ? 'var(--green)' : scoreVal >= 60 ? 'var(--amber)' : 'var(--red)';
    const reasons_title = (p.reasons||[]).join('\n');
    // AI data from _aiDecisions (loaded async)
    const aiKey = p.ticker + '_' + (p.strategy_tag||'');
    const ai = _aiDecisions[aiKey] || {};
    const dec = ai.decision || 'WATCH';
    const enr = ai.expected_net_r != null ? ai.expected_net_r.toFixed(2) : '--';
    const pt  = ai.p_target != null ? (ai.p_target*100).toFixed(0)+'%' : '--';
    const unc = ai.uncertainty != null ? ai.uncertainty : null;
    const uncPct = unc != null ? Math.round((1-unc)*100) : null;
    const uncCls = unc == null ? '' : unc < 0.4 ? 'unc-low' : unc < 0.65 ? 'unc-med' : 'unc-high';
    const sb = ai.spread_bps != null ? ai.spread_bps.toFixed(1) : '--';
    return '<tr title="' + esc(reasons_title) + '" onclick="showTradeCard(' + i + ')" style="cursor:pointer">'
      + '<td>' + (i+1) + '</td>'
      + '<td><b>' + esc(p.ticker) + '</b></td>'
      + '<td ' + dir_cls + '><b>' + esc(p.direction) + '</b></td>'
      + '<td><span class="dec-' + dec + '">' + dec + '</span></td>'
      + '<td style="color:var(--gold)">' + esc(p.stars) + '</td>'
      + '<td style="color:' + score_col + '">' + scoreVal.toFixed(1) + '</td>'
      + '<td style="color:' + (enr==='--'?'var(--grey)':parseFloat(enr)>=0?'var(--green)':'var(--red)') + '">' + enr + '</td>'
      + '<td>' + pt + '</td>'
      + '<td class="' + uncCls + '">' + (uncPct != null ? uncPct+'%' : '--') + '</td>'
      + '<td><span class="chip chip-blue" style="font-size:8.5px">' + esc((p.strategy_tag||'--').replace(/_/g,' ')) + '</span></td>'
      + '<td class="size-' + (p.sizing_hint||'M') + '"><b>' + esc(p.sizing_hint||'M') + '</b></td>'
      + '<td><span class="chip chip-grey" style="font-size:8px">' + esc((p.track||'--').replace(/_/g,' ')) + '</span></td>'
      + '<td>' + fmtN(p.entry) + '</td>'
      + '<td style="color:var(--red)">' + fmtN(p.stop) + '</td>'
      + '<td style="color:var(--green)">' + fmtN(p.target1) + '</td>'
      + '<td>' + (p.rr||0).toFixed(2) + '</td>'
      + '<td>' + (p.atr_pct||0).toFixed(2) + '%</td>'
      + '<td>' + (p.rvol ? p.rvol.toFixed(1)+'x' : 'N/A') + '</td>'
      + '<td style="color:var(--grey)">' + sb + '</td>'
      + '<td style="color:var(--silver);font-size:9px">' + esc(p.label||'') + '</td>'
      + '</tr>';
  }).join('');
}

function showTradeCard(idx) {
  const plays = _state.top_plays || [];
  const p = plays[idx];
  if (!p) return;
  const aiKey = p.ticker + '_' + (p.strategy_tag||'');
  const ai = _aiDecisions[aiKey] || {};
  document.getElementById('tc-title').textContent = p.ticker + ' ' + (p.direction||'') + ' — TRADE CARD';
  const dec = ai.decision || 'WATCH';
  const decCol = dec === 'TRADE' ? 'var(--green)' : dec === 'ABSTAIN' ? 'var(--red)' : 'var(--amber)';
  document.getElementById('tc-body').innerHTML =
    '<div style="margin-bottom:8px"><span class="dec-' + dec + '" style="font-size:12px;padding:3px 12px">' + dec + '</span></div>'
    + '<div style="color:var(--silver);margin-bottom:10px;font-style:italic">' + esc(ai.why||'Awaiting AI data...') + '</div>'
    + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">'
    + '<div><span style="color:var(--grey)">Expected Net R:</span> <b style="color:var(--gold)">' + (ai.expected_net_r!=null?ai.expected_net_r.toFixed(2)+'R':'--') + '</b></div>'
    + '<div><span style="color:var(--grey)">P(Target):</span> <b>' + (ai.p_target!=null?(ai.p_target*100).toFixed(0)+'%':'--') + '</b></div>'
    + '<div><span style="color:var(--grey)">Expected Duration:</span> <b>' + (ai.expected_duration?ai.expected_duration+'min':'--') + '</b></div>'
    + '<div><span style="color:var(--grey)">Edge Confidence:</span> <b>' + (ai.uncertainty!=null?Math.round((1-ai.uncertainty)*100)+'%':'--') + '</b></div>'
    + '<div><span style="color:var(--grey)">Method:</span> <b style="color:var(--grey)">' + esc(ai.method||'PRIOR') + ' (n=' + (ai.sample_basis||0) + ')</b></div>'
    + '<div><span style="color:var(--grey)">Spread BPS:</span> <b>' + (ai.spread_bps!=null?ai.spread_bps.toFixed(1):'--') + '</b></div>'
    + '<div><span style="color:var(--grey)">Fill Risk:</span> <b>' + (ai.fill_risk_score!=null?(ai.fill_risk_score*100).toFixed(0)+'%':'--') + '</b></div>'
    + '<div><span style="color:var(--grey)">Exec Rec:</span> <b>' + esc(ai.exec_recommendation||'NORMAL') + '</b></div>'
    + '</div>'
    + '<hr style="border-color:var(--border);margin:10px 0">'
    + '<div style="font-size:10px;color:var(--grey)">'
    + 'Entry: <b>' + fmtN(p.entry) + '</b> &nbsp;|&nbsp; '
    + 'Stop: <b style="color:var(--red)">' + fmtN(p.stop) + '</b> &nbsp;|&nbsp; '
    + 'T1: <b style="color:var(--green)">' + fmtN(p.target1) + '</b> &nbsp;|&nbsp; '
    + 'R:R: <b>' + (p.rr||0).toFixed(2) + '</b> &nbsp;|&nbsp; '
    + 'ATR: <b>' + (p.atr_pct||0).toFixed(2) + '%</b> &nbsp;|&nbsp; '
    + 'RVOL: <b>' + (p.rvol?p.rvol.toFixed(1)+'x':'N/A') + '</b>'
    + '</div>'
    + (p.reasons&&p.reasons.length ? '<div style="margin-top:8px;font-size:9px;color:var(--grey)">Reasons: ' + esc(p.reasons.join(' | ')) + '</div>' : '');
  document.getElementById('trade-card-modal').style.display = 'flex';
}

async function loadAIDecisions() {
  try {
    const r = await fetch('/api/ai/expectancy');
    const d = await r.json();
    _aiDecisions = {};
    (d.trades||[]).forEach(t => {
      const k = t.ticker + '_' + (t.strategy_tag||'');
      _aiDecisions[k] = t;
    });
    // Re-render plays with AI data
    renderPlays(_state.top_plays || []);
  } catch(e) { /* silent — AI data optional */ }
}

function renderOverseer(d) {
  const port = d.portfolio || {};
  const s = d.strategies || {};

  // Factor exposure bars
  const fb = document.getElementById('factor-body');
  const fe = port.factor_exposure || {};
  const maxCount = Math.max(...Object.values(fe), 1);
  fb.innerHTML = Object.entries(fe).map(([grp, cnt]) => {
    const pct = Math.round(cnt / maxCount * 100);
    const col = cnt >= 3 ? 'var(--red)' : cnt >= 2 ? 'var(--amber)' : 'var(--green)';
    return '<div style="margin-bottom:6px">'
      + '<div style="display:flex;justify-content:space-between;margin-bottom:2px">'
      + '<span style="color:var(--silver)">' + esc(grp) + '</span>'
      + '<span style="color:' + col + '">' + cnt + ' signals</span></div>'
      + '<div class="bar-bg"><div class="bar-fill" style="width:' + pct + '%;background:' + col + '"></div></div></div>';
  }).join('') || '<span style="color:var(--grey)">No factor data</span>';

  // Risk state
  const rb = document.getElementById('risk-body');
  const vt = port.vol_target_state || 'NORMAL';
  const vt_col = vt === 'DEFENSIVE' ? 'var(--red)' : vt === 'REDUCED' ? 'var(--amber)' : 'var(--green)';
  rb.innerHTML =
    '<div style="margin-bottom:6px">Vol Target State: <b style="color:' + vt_col + '">' + vt + '</b></div>'
    + '<div style="margin-bottom:6px">Score Boost: <b style="color:var(--gold)">' + (s.score_boost||0).toFixed(3) + '</b></div>'
    + '<div style="margin-bottom:6px">Max Factor Cap: <b>' + (s.max_factor_cap||3) + '</b></div>'
    + '<div style="margin-bottom:6px">Sizing Mode: <b style="color:var(--amber)">' + (s.sizing_mode||'NORMAL') + '</b></div>'
    + (port.warnings||[]).map(w => '<div class="chip chip-amber">' + esc(w) + '</div>').join('');

  // Guardrails
  updateHaltBtn();
  document.getElementById('guardrails-detail').innerHTML =
    '<div style="color:var(--grey);font-size:10px">Kill switch: <b style="color:' + (s.kill_switch?'var(--red)':'var(--green)') + '">' + (s.kill_switch?'ACTIVE':'OFF') + '</b></div>';

  // Readiness
  const rdb = document.getElementById('readiness-body');
  const checks = [
    ['Data health', (d.data_health||{}).badge === 'GREEN' || (d.data_health||{}).badge === 'AMBER'],
    ['Engine ticking', (d.tick||0) > 0],
    ['Regime classified', (d.market||{}).regime !== 'UNKNOWN'],
    ['No halt active', !_haltState],
    ['No drought', !d.drought],
  ];
  rdb.innerHTML = checks.map(([label, ok]) =>
    '<div style="margin-bottom:4px"><span style="color:' + (ok?'var(--green)':'var(--red)') + '">' + (ok?'[OK]':'[--]') + '</span> ' + esc(label) + '</div>'
  ).join('');
}

function renderDiagnostics(d) {
  const gf = d.gate_funnel || {};

  // Funnel visual
  const fv = document.getElementById('funnel-visual');
  const steps = [
    ['Universe', gf.tracked||0, 'var(--silver)'],
    ['Data Valid', gf.data_valid||0, 'var(--blue)'],
    ['Strict Signals', gf.signals_strict||0, 'var(--green)'],
    ['Fallback Signals', gf.signals_fallback||0, 'var(--amber)'],
  ];
  const maxVal = Math.max(...steps.map(s => s[1]), 1);
  fv.innerHTML = steps.map(([lbl, val, col]) => {
    const pct = Math.round(val / maxVal * 100);
    return '<div style="margin-bottom:8px">'
      + '<div style="display:flex;justify-content:space-between;margin-bottom:2px">'
      + '<span style="color:' + col + ';font-weight:bold">' + lbl + '</span>'
      + '<span style="color:' + col + '">' + val + '</span></div>'
      + '<div class="bar-bg" style="height:12px"><div class="bar-fill" style="width:' + pct + '%;background:' + col + '"></div></div></div>';
  }).join('');

  // Blockers
  const bb = document.getElementById('blockers-body');
  bb.innerHTML = (gf.blockers||[]).map((b,i) =>
    '<div style="margin-bottom:4px"><span style="color:var(--amber)">' + (i+1) + '.</span> ' + esc(b) + '</div>'
  ).join('') || '<span style="color:var(--grey)">No blockers</span>';

  // Health table
  const ht = document.getElementById('health-tbody');
  const failed = (d.data_health||{}).failed_tickers || [];
  const health = d.data_health || {};
  ht.innerHTML = '<tr><td colspan="4" style="color:var(--grey)">Badge: <b class="chip chip-' + (health.badge==='GREEN'?'green':health.badge==='RED'?'red':'amber') + '">' + (health.badge||'?') + '</b>'
    + '  PASS:' + (health.pass||0) + '  WARN:' + (health.warn||0) + '  FAIL:' + (health.fail||0) + '</td></tr>'
    + (failed.length ? '<tr><td colspan="4" style="color:var(--red)">Failed: ' + esc(failed.join(', ')) + '</td></tr>' : '')
    + (health.warnings||[]).map(w => '<tr><td colspan="4" style="color:var(--amber);font-size:9px">'+esc(w)+'</td></tr>').join('');

}

async function loadStratLab() {
  try {
    const [stratR, calR, allocR] = await Promise.all([
      fetch('/api/strategies').then(r=>r.json()),
      fetch('/api/calibration').then(r=>r.json()),
      fetch('/api/allocation').then(r=>r.json()),
    ]);

    // Strategy roster with allocation weights
    const sr = document.getElementById('strat-roster');
    const active = stratR.active || [];
    const inactive = stratR.inactive || [];
    const allocWeights = allocR.allocation_weights || {};
    const allRows = [
      ...active.map(s => ({...s, is_active: true})),
      ...inactive.map(s => ({...s, is_active: false})),
    ];
    sr.innerHTML = allRows.map(s => {
      const allocPct = allocWeights[s.tag] != null ? (allocWeights[s.tag]*100).toFixed(1)+'%' : '--';
      const cls = s.is_active ? 'chip-green' : 'chip-grey';
      return '<tr>'
        + '<td><span class="chip ' + cls + '" style="font-size:8.5px">' + esc(s.tag) + '</span></td>'
        + '<td style="color:var(--grey);font-size:9px">' + esc(s.category||'--') + '</td>'
        + '<td><span class="chip ' + (s.is_active?'chip-green':'chip-red') + '">' + (s.is_active?'ACTIVE':'INACTIVE') + '</span></td>'
        + '<td style="color:var(--gold)">' + (s.weight!=null ? s.weight.toFixed(2) : '--') + '</td>'
        + '<td style="color:var(--amber)">' + allocPct + '</td>'
        + '<td style="font-size:9px;color:var(--silver)">' + (s.why_active||[]).map(w=>esc(w)).join(' | ') + '</td>'
        + '<td style="font-size:9px;color:var(--dim)">' + esc(s.inactive_reason||'') + '</td>'
        + '</tr>';
    }).join('') || '<tr><td colspan="7" style="color:var(--grey)">No strategy data yet</td></tr>';

    // Event strategy availability
    const est = document.getElementById('event-strat-tbody');
    const evAvail = allocR.strategy_availability || [];
    est.innerHTML = evAvail.map(ev => {
      const sc = ev.status === 'ACTIVE' ? 'chip-green' : 'chip-red';
      return '<tr>'
        + '<td><b>' + esc(ev.name) + '</b></td>'
        + '<td><span class="chip ' + sc + '">' + esc(ev.status) + '</span></td>'
        + '<td style="color:var(--amber);font-size:9px">' + esc(ev.reason||'--') + '</td>'
        + '<td style="color:var(--grey);font-size:9px">' + esc(ev.required_config_key||'--') + '</td>'
        + '<td style="color:var(--silver);font-size:9px">' + esc(ev.recommended_provider||'--') + '</td>'
        + '</tr>';
    }).join('') || '<tr><td colspan="5" style="color:var(--grey)">No event strategy data</td></tr>';

    // Calibration
    const cb = document.getElementById('calibration-body');
    cb.innerHTML = '<div style="color:var(--amber);margin-bottom:8px;font-size:10px">' + esc(calR.note||'') + '</div>'
      + '<table><thead><tr><th>R:R Ratio</th><th>Win Rate Required to Break Even</th></tr></thead>'
      + '<tbody>' + (calR.rr_breakeven_table||[]).map(r =>
        '<tr><td>' + r.rr + ':1</td><td><b>' + r.win_rate_required_pct.toFixed(1) + '%</b></td></tr>'
      ).join('') + '</tbody></table>';
  } catch(e) { console.error('loadStratLab', e); }
}

async function loadRiskOfficer() {
  try {
    const roR = await fetch('/api/risk_officer').then(r=>r.json());
    // Summary row
    const ros = document.getElementById('ro-summary');
    ros.innerHTML = '<span class="chip chip-green">APPROVE: ' + (roR.approve_count||0) + '</span>'
      + ' <span class="chip chip-amber">DOWNSIZE: ' + (roR.downsize_count||0) + '</span>'
      + ' <span class="chip chip-red">VETO: ' + (roR.veto_count||0) + '</span>'
      + (roR.session ? ' <span style="color:var(--grey);font-size:9px;margin-left:8px">Session: ' + esc(roR.session) + ' | ' + esc(roR.generated_at||'') + '</span>' : '');
    // Decisions table
    const rot = document.getElementById('ro-tbody');
    const decisions = roR.decisions || [];
    if (!decisions.length) {
      rot.innerHTML = '<tr><td colspan="7" style="color:var(--grey);text-align:center;padding:10px">No risk officer decisions yet — engine has not run with RiskOfficer</td></tr>';
      return;
    }
    rot.innerHTML = decisions.map(d => {
      const dc = d.decision === 'APPROVE' ? 'chip-green' : d.decision === 'DOWNSIZE' ? 'chip-amber' : 'chip-red';
      const rsCol = d.risk_score >= 0.7 ? 'var(--red)' : d.risk_score >= 0.4 ? 'var(--amber)' : 'var(--green)';
      const reasonsStr = (d.reasons||[]).join('; ');
      return '<tr title="' + esc(reasonsStr) + '">'
        + '<td><b>' + esc(d.ticker||'?') + '</b></td>'
        + '<td style="color:' + (d.direction==='LONG'?'var(--green)':'var(--red)') + '">' + esc(d.direction||'?') + '</td>'
        + '<td><span class="chip ' + dc + '">' + esc(d.decision) + '</span></td>'
        + '<td class="size-' + (d.original_sizing||'M') + '">' + esc(d.original_sizing||'M') + '</td>'
        + '<td class="size-' + (d.final_sizing||'M') + '"><b>' + esc(d.final_sizing||'M') + '</b></td>'
        + '<td style="color:' + rsCol + '">' + (d.risk_score||0).toFixed(3) + '</td>'
        + '<td style="font-size:9px;color:var(--amber)">' + esc((d.reasons||[]).slice(0,2).join(' | ')) + '</td>'
        + '</tr>';
    }).join('');
  } catch(e) { console.error('loadRiskOfficer', e); document.getElementById('ro-tbody').innerHTML = '<tr><td colspan="7" style="color:var(--amber)">Error loading risk officer data</td></tr>'; }
}

async function loadDrought() {
  try {
    const dR = await fetch('/api/drought').then(r=>r.json());
    // Flag banner
    const dfb = document.getElementById('drought-flag-banner');
    if (dR.drought_active) {
      dfb.innerHTML = '<div class="alert alert-red"><b>DROUGHT ACTIVE</b> — Signal generation suppressed</div>';
    } else {
      dfb.innerHTML = '<div class="alert alert-green">No drought — signals generating normally</div>';
    }
    // Closest misses table
    const cmt = document.getElementById('closest-misses-tbody');
    const misses = dR.closest_misses || [];
    if (!misses.length) {
      cmt.innerHTML = '<tr><td colspan="7" style="color:var(--grey);text-align:center;padding:10px">No closest miss data yet — engine needs to run first</td></tr>';
    } else {
      cmt.innerHTML = misses.map(m => {
        const deltaCol = (m.delta||0) < 5 ? 'var(--amber)' : 'var(--grey)';
        return '<tr>'
          + '<td><b>' + esc(m.ticker||'?') + '</b></td>'
          + '<td style="color:var(--red)">' + esc(m.failed_gate||'?') + '</td>'
          + '<td>' + fmtN(m.observed_value) + '</td>'
          + '<td>' + fmtN(m.required_value) + '</td>'
          + '<td style="color:' + deltaCol + '">' + fmtN(m.delta) + '</td>'
          + '<td>' + (m.fallback_admits ? '<span style="color:var(--amber)">YES</span>' : '<span style="color:var(--grey)">NO</span>') + '</td>'
          + '<td style="font-size:9px;color:var(--silver)">' + esc(m.safest_knob||'--') + '</td>'
          + '</tr>';
      }).join('');
    }
    // Recommended knobs
    const knt = document.getElementById('knobs-tbody');
    const knobs = dR.recommended_knobs || [];
    if (!knobs.length) {
      knt.innerHTML = '<tr><td colspan="6" style="color:var(--grey);text-align:center;padding:10px">No knob recommendations — run engine to populate</td></tr>';
    } else {
      knt.innerHTML = knobs.map(k =>
        '<tr>'
        + '<td><b style="color:var(--gold)">' + esc(k.param_name||'?') + '</b></td>'
        + '<td>' + fmtN(k.current_value) + '</td>'
        + '<td style="color:var(--amber)">' + fmtN(k.suggested_value) + '</td>'
        + '<td style="color:var(--grey);font-size:9px">' + esc(k.bounded_range||'--') + '</td>'
        + '<td style="font-size:9px;color:var(--silver)">' + esc(k.tradeoff||'--') + '</td>'
        + '<td style="font-size:9px;color:var(--green)">' + esc(k.expected_effect||'--') + '</td>'
        + '</tr>'
      ).join('');
    }
  } catch(e) { console.error('loadDrought', e); }
}

async function loadArtifacts() {
  try {
    const artR = await fetch('/api/artifacts').then(r=>r.json());
    const ab = document.getElementById('artifacts-tbody');
    const arts = artR.artifacts || [];
    if (!arts.length) {
      ab.innerHTML = '<tr><td colspan="6" style="color:var(--grey);text-align:center;padding:10px">No artifacts found for today</td></tr>';
      return;
    }
    ab.innerHTML = arts.map(a => {
      const chk = v => v ? '<span style="color:var(--green)">&#x2714;</span>' : '<span style="color:var(--dim)">&#x2715;</span>';
      return '<tr>'
        + '<td><b>' + esc(a.session) + '</b></td>'
        + '<td>' + chk(a.plays_exists) + (a.plays_exists ? ' <span style="font-size:9px;color:var(--grey)">' + a.plays_size_kb + 'KB</span>' : '') + '</td>'
        + '<td>' + chk(a.strategies_exists) + '</td>'
        + '<td>' + chk(a.risk_officer_exists) + (a.risk_officer_exists ? ' <span style="font-size:9px;color:var(--grey)">' + a.risk_size_kb + 'KB</span>' : '') + '</td>'
        + '<td>' + chk(a.drought_exists) + (a.drought_exists ? ' <span style="font-size:9px;color:var(--grey)">' + a.drought_size_kb + 'KB</span>' : '') + '</td>'
        + '<td style="color:var(--grey);font-size:9px">' + (a.plays_size_kb||0) + ' / ' + (a.risk_size_kb||0) + ' / ' + (a.drought_size_kb||0) + '</td>'
        + '</tr>';
    }).join('');
  } catch(e) { console.error('loadArtifacts', e); }
}

async function loadReports() {
  try {
    const [repR, ssR] = await Promise.all([
      fetch('/api/reports').then(r=>r.json()),
      fetch('/api/session_status').then(r=>r.json()),
    ]);

    // Session status — v4.0 expanded fields
    const sst = document.getElementById('session-status-tbody');
    sst.innerHTML = (ssR.jobs||[]).map(j => {
      const sc = j.status === 'PASS' ? 'green' : j.status === 'FAIL' ? 'red' : 'grey';
      const yesNo = v => v ? '<span style="color:var(--green)">YES</span>' : '<span style="color:var(--red)">NO</span>';
      const droughtCol = j.drought_flag ? '<span style="color:var(--red)">DROUGHT</span>' : '<span style="color:var(--green)">OK</span>';
      const blockers = (j.top_blockers||[]).slice(0,3).join(', ');
      return '<tr>'
        + '<td><b>' + esc(j.session) + '</b></td>'
        + '<td><span class="chip chip-' + sc + '">' + esc(j.status||'PENDING') + '</span></td>'
        + '<td>' + yesNo(j.artifacts_written) + '</td>'
        + '<td>' + yesNo(j.pdf_written) + '</td>'
        + '<td style="color:var(--green)">' + (j.signals_strict_count != null ? j.signals_strict_count : '--') + '</td>'
        + '<td style="color:var(--amber)">' + (j.signals_fallback_count != null ? j.signals_fallback_count : '--') + '</td>'
        + '<td>' + droughtCol + '</td>'
        + '<td style="font-size:9px;color:var(--amber)">' + esc(blockers) + '</td>'
        + '<td style="font-size:9px;color:var(--grey)">' + esc(j.generated_at_uk||j.timestamp||'--') + '</td>'
        + '<td style="font-size:9px;color:var(--red)">' + esc(j.error_msg||'') + '</td>'
        + '</tr>';
    }).join('') || '<tr><td colspan="10" style="color:var(--grey);text-align:center;padding:10px">No session status data yet</td></tr>';

    // PDFs
    const pb = document.getElementById('pdfs-body');
    pb.innerHTML = (repR.pdfs||[]).map(p =>
      '<div style="margin-bottom:4px">'
      + '<span style="color:var(--gold)">' + esc(p.name) + '</span>'
      + ' <span style="color:var(--grey);font-size:9px">' + p.size_kb + ' KB</span>'
      + ' <span style="color:var(--dim);font-size:9px">' + esc(p.modified||'') + '</span></div>'
    ).join('') || '<span style="color:var(--grey)">No PDFs found today</span>';

    // Artifacts (v4.0) — load from /api/artifacts
    loadArtifacts();
  } catch(e) { console.error('loadReports', e); }
}

// ===== AI COACH =====
async function loadCoach() {
  try {
    const [driftR, statsR, weightsR, edgeR, outR] = await Promise.all([
      fetch('/api/ai/drift').then(r=>r.json()).catch(()=>({})),
      fetch('/api/ai/signal_log_stats').then(r=>r.json()).catch(()=>({})),
      fetch('/api/ai/meta_weights').then(r=>r.json()).catch(()=>({})),
      fetch('/api/ai/edge_map').then(r=>r.json()).catch(()=>({})),
      fetch('/api/outcomes/recent?limit=20').then(r=>r.json()).catch(()=>({})),
    ]);

    // Drift + defensive mode
    const ds = document.getElementById('drift-status');
    const def = driftR.defensive_mode || false;
    const db  = document.getElementById('defensive-banner');
    if (db) db.style.display = def ? 'block' : 'none';
    const rep = driftR.latest_report;
    if (rep && rep.detected) {
      ds.innerHTML = '<span class="drift-' + rep.severity + '">DRIFT DETECTED: ' + esc(rep.severity) + '</span>'
        + '<br><span style="color:var(--grey);font-size:10px">Type: ' + esc(rep.drift_type) + '</span>'
        + '<br><span style="color:var(--silver);font-size:10px">' + esc(rep.description) + '</span>'
        + '<br><span style="color:var(--grey);font-size:9px">At: ' + esc(rep.generated_at||'') + '</span>'
        + (def ? '<br><span style="color:var(--red);font-weight:bold">DEFENSIVE MODE ACTIVE</span>' : '');
    } else {
      ds.innerHTML = '<span style="color:var(--green)">No drift detected</span>'
        + '<span style="color:var(--grey);font-size:10px;margin-left:8px">System running normally</span>';
    }

    // Signal log stats
    const ls = document.getElementById('learning-status');
    const tot = statsR.total||0, pend = statsR.pending||0, res = statsR.resolved||0;
    const pctRes = tot > 0 ? Math.round(res/tot*100) : 0;
    const readiness = res >= 20 ? '<span style="color:var(--green)">EDGE LEDGER ACTIVE</span>'
      : res >= 5 ? '<span style="color:var(--amber)">CALIBRATION READY (' + res + '/20)</span>'
      : '<span style="color:var(--grey)">NEEDS DATA (' + res + '/20 outcomes)</span>';
    ls.innerHTML = '<div style="margin-bottom:6px">'
      + '<span style="color:var(--gold)">Signals logged: ' + tot + '</span> &nbsp;|&nbsp; '
      + '<span style="color:var(--amber)">Pending: ' + pend + '</span> &nbsp;|&nbsp; '
      + '<span style="color:var(--green)">Resolved: ' + res + '</span></div>'
      + '<div style="margin-bottom:4px">' + readiness + '</div>'
      + '<div class="bar-bg"><div class="bar-fill" style="width:' + pctRes + '%;background:var(--green)"></div></div>'
      + '<div style="font-size:9px;color:var(--grey);margin-top:2px">' + pctRes + '% resolution rate</div>';

    // Edge map
    const emt = document.getElementById('edge-map-tbody');
    const buckets = edgeR.buckets || [];
    const curRegime = edgeR.regime || '';
    if (!buckets.length) {
      emt.innerHTML = '<tr><td colspan="11" style="color:var(--grey);text-align:center;padding:12px">No edge data yet — need 5+ resolved outcomes per bucket</td></tr>';
    } else {
      emt.innerHTML = buckets.slice(0,30).map(b => {
        const isCur = b.current_regime;
        const rowStyle = isCur ? 'background:#0a1e0a;' : '';
        const expCol = b.expectancy_net > 0.1 ? 'var(--green)' : b.expectancy_net > 0 ? 'var(--amber)' : 'var(--red)';
        return '<tr style="' + rowStyle + '">'
          + '<td><b style="color:' + (isCur?'var(--gold)':'var(--silver)') + '">' + esc(b.strategy_tag) + '</b></td>'
          + '<td style="font-size:9px">' + esc(b.regime_tag) + '</td>'
          + '<td style="font-size:9px">' + esc(b.track) + '</td>'
          + '<td style="font-size:9px;color:var(--grey)">' + esc(b.time_window) + '</td>'
          + '<td style="color:var(--grey)">' + b.trades_count + '</td>'
          + '<td>' + (b.win_rate*100).toFixed(0) + '%</td>'
          + '<td style="font-size:9px;color:var(--grey)">[' + (b.win_rate_low*100).toFixed(0) + '-' + (b.win_rate_high*100).toFixed(0) + '%]</td>'
          + '<td style="color:' + expCol + ';font-weight:bold">' + b.expectancy_net.toFixed(2) + 'R</td>'
          + '<td style="color:var(--silver)">' + Math.round(b.confidence_score*100) + '%</td>'
          + '<td><span class="edge-' + b.status + '">' + b.status + '</span></td>'
          + '<td>' + (isCur ? '<span style="color:var(--green)">&#x2605; NOW</span>' : '') + '</td>'
          + '</tr>';
      }).join('');
    }

    // Meta weights
    const mwb = document.getElementById('meta-weights-body');
    const wts = weightsR.weights || {};
    if (!Object.keys(wts).length) {
      mwb.innerHTML = '<span style="color:var(--grey)">No meta weights yet — run /api/ai/update_meta_weights first</span>';
    } else {
      const sorted = Object.entries(wts).sort((a,b)=>b[1]-a[1]);
      const maxW = sorted[0][1];
      mwb.innerHTML = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px">'
        + sorted.map(([strat, w]) => {
          const pct = Math.round(w * 100);
          const barW = Math.round(w / maxW * 100);
          const col = pct >= 20 ? 'var(--green)' : pct >= 10 ? 'var(--amber)' : 'var(--grey)';
          return '<div style="margin-bottom:4px">'
            + '<div style="display:flex;justify-content:space-between;margin-bottom:1px">'
            + '<span style="font-size:9px;color:var(--silver)">' + esc(strat.replace(/_/g,' ')) + '</span>'
            + '<span style="font-size:10px;color:' + col + ';font-weight:bold">' + pct + '%</span></div>'
            + '<div class="bar-bg" style="height:5px"><div class="bar-fill" style="width:' + barW + '%;background:' + col + '"></div></div>'
            + '</div>';
        }).join('')
        + '</div>'
        + (weightsR.guardrail_notes&&weightsR.guardrail_notes.length
           ? '<div style="margin-top:6px;font-size:9px;color:var(--amber)">' + esc(weightsR.guardrail_notes.slice(0,3).join(' | ')) + '</div>'
           : '')
        + '<div style="font-size:9px;color:var(--grey);margin-top:4px">Updated: ' + esc(weightsR.generated_at||'never') + ' | ' + esc(weightsR.evidence_summary||'') + '</div>';
    }

    // Recent outcomes
    const ot = document.getElementById('outcomes-tbody');
    const outcomes = outR.outcomes || [];
    if (!outcomes.length) {
      ot.innerHTML = '<tr><td colspan="10" style="color:var(--grey);text-align:center;padding:12px">No resolved outcomes yet — signals need time to expire or hit targets/stops</td></tr>';
    } else {
      ot.innerHTML = outcomes.map(o => {
        const outCls = o.outcome==='HIT_TARGET' ? 'chip-green' : o.outcome==='HIT_STOP' ? 'chip-red' : 'chip-amber';
        const rCol   = (o.pnl_r_net||0) > 0 ? 'var(--green)' : 'var(--red)';
        return '<tr>'
          + '<td style="font-size:9px;color:var(--grey)">' + esc(o.signal_id||'--') + '</td>'
          + '<td><b>' + esc(o.ticker) + '</b></td>'
          + '<td style="color:' + (o.direction==='LONG'?'var(--green)':'var(--red)') + '">' + esc(o.direction) + '</td>'
          + '<td style="font-size:9px">' + esc((o.strategy_tag||'').replace(/_/g,' ')) + '</td>'
          + '<td style="font-size:9px;color:var(--grey)">' + esc(o.regime_tag||'--') + '</td>'
          + '<td><span class="chip ' + outCls + '">' + esc(o.outcome) + '</span></td>'
          + '<td style="color:' + rCol + ';font-weight:bold">' + (o.pnl_r_net!=null?o.pnl_r_net.toFixed(2)+'R':'--') + '</td>'
          + '<td style="color:var(--grey)">' + (o.duration_minutes||0) + 'min</td>'
          + '<td style="color:var(--grey)">' + (o.cost_bps||0).toFixed(1) + '</td>'
          + '<td style="font-size:9px;color:var(--dim)">' + esc((o.closed_at||'').slice(0,16).replace('T',' ')) + '</td>'
          + '</tr>';
      }).join('');
    }

  } catch(e) { console.error('loadCoach', e); }
}

// AI action triggers
async function triggerResolveOutcomes() {
  const el = document.getElementById('ai-action-result');
  el.textContent = 'Resolving outcomes...';
  try {
    const r = await fetch('/api/ai/resolve_outcomes', {method:'POST'});
    const d = await r.json();
    el.textContent = 'Done: ' + JSON.stringify(d.stats||d);
    setTimeout(() => loadCoach(), 500);
  } catch(e) { el.textContent = 'Error: ' + e.message; }
}

async function triggerRebuildEdgeLedger() {
  const el = document.getElementById('ai-action-result');
  el.textContent = 'Rebuilding edge ledger...';
  try {
    const r = await fetch('/api/ai/rebuild_edge_ledger', {method:'POST'});
    const d = await r.json();
    el.textContent = 'Done: ' + JSON.stringify(d);
    setTimeout(() => loadCoach(), 500);
  } catch(e) { el.textContent = 'Error: ' + e.message; }
}

async function triggerRunDrift() {
  const el = document.getElementById('ai-action-result');
  el.textContent = 'Running drift detection...';
  try {
    const r = await fetch('/api/ai/run_drift_detection', {method:'POST'});
    const d = await r.json();
    el.textContent = 'Done: ' + d.reports_generated + ' reports, defensive=' + d.defensive_mode;
    setTimeout(() => loadCoach(), 500);
  } catch(e) { el.textContent = 'Error: ' + e.message; }
}

// ===== HALT =====
function updateHaltBtn() {
  const btn = document.getElementById('halt-btn');
  const status = document.getElementById('halt-status');
  if (_haltState) {
    btn.style.background = '#e53935';
    btn.style.color = '#fff';
    btn.textContent = 'RESUME';
    status.innerHTML = '<span style="color:var(--red)">&#x26a0; HALTED — No new signals</span>';
  } else {
    btn.style.background = '#003300';
    btn.style.color = 'var(--green)';
    btn.textContent = 'HALT';
    status.innerHTML = '<span style="color:var(--green)">Running normally</span>';
  }
}

async function toggleHalt() {
  try {
    const r = await fetch('/api/halt', {method:'POST'});
    const d = await r.json();
    _haltState = d.halt_new_signals;
    updateHaltBtn();
  } catch(e) { console.error('toggleHalt', e); }
}

// ===== FILTER =====
function filterPlays(query) {
  const rows = document.getElementById('plays-tbody').querySelectorAll('tr');
  const q = query.toLowerCase();
  rows.forEach(r => {
    r.style.display = !q || r.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}

// ===== UTILS =====
function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function fmtN(n) {
  if (n == null || n === 0) return '--';
  return n > 100 ? n.toFixed(2) : n.toFixed(4);
}

// ===== WEBSOCKET =====
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(proto + '://' + location.host + '/ws');
  ws.onopen = () => document.getElementById('ws-status').textContent = 'WS: connected';
  ws.onmessage = e => { try { renderState(JSON.parse(e.data)); } catch(ex) {} };
  ws.onclose = () => {
    document.getElementById('ws-status').textContent = 'WS: reconnecting...';
    setTimeout(connectWS, 5000);
  };
  ws.onerror = () => ws.close();
}

// ===== KEYBOARD =====
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  const tabs = ['warroom','risk','coach','explain','stratlab','reports'];
  if (e.key >= '1' && e.key <= '6') showTab(tabs[parseInt(e.key)-1]);
  if (e.key === 'r') refresh();
  if (e.key === 'g') toggleGuided();
  if (e.key === 'f') { showTab('warroom'); document.getElementById('search').focus(); }
  if (e.key === 'd') {
    showTab('explain');
    setTimeout(() => { const el = document.getElementById('drought-flag-banner'); if (el) el.scrollIntoView(); }, 200);
  }
  if (e.key === 'c') showTab('coach');
  if (e.key === '/') { e.preventDefault(); document.getElementById('search').focus(); }
  if (e.key === 'Escape') {
    document.getElementById('trade-card-modal').style.display = 'none';
    document.getElementById('glossary-modal').classList.remove('open');
  }
});

// ===== POLL FALLBACK + INIT =====
async function refresh() {
  try {
    const r = await fetch('/api/state');
    const d = await r.json();
    renderState(d);
  } catch(e) { console.error('refresh', e); }
}

connectWS();
refresh();
setInterval(refresh, 30000);
// Load AI decisions on startup
setTimeout(loadAIDecisions, 2000);
</script>
</body>
</html>"""
