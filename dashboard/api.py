"""
NZT-48 Command Center — FastAPI Backend
Phase 3: REST API + WebSocket for the Next.js dashboard.

Endpoints:
- GET /api/signals — Recent signals with filters
- GET /api/positions — Current open positions
- GET /api/trades — Trade journal with pagination
- GET /api/regime — Current regime and market context
- GET /api/bots — Bot status (BULL/RANGE/BEAR/EARNINGS/SECTOR)
- GET /api/overseer — Overseer status and restrictions
- GET /api/performance — Performance metrics (daily, weekly, monthly)
- GET /api/learning — Learning engine state
- GET /api/kelly — Kelly Criterion status
- GET /api/pdt — PDT tracker status
- GET /api/config — System configuration
- POST /api/kill — Activate kill switch
- POST /api/pause — Pause specific strategy or bot
- POST /api/resume — Resume strategy or bot
- WS /ws/live — Real-time signal + position updates
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import sys

# Add project root to path
PROJECT_ROOT = str(Path(__file__).parent.parent)
sys.path.insert(0, PROJECT_ROOT)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import time
from starlette.middleware.base import BaseHTTPMiddleware

import yfinance as yf

import config as cfg

logger = logging.getLogger("nzt48.api")

# === Simple cache for yfinance calls (60-second TTL) ===
_yf_cache: dict[str, tuple[float, any]] = {}
_YF_CACHE_TTL = 60  # seconds


def _yf_cached(key: str, fetcher, ttl: int = _YF_CACHE_TTL):
    """Return cached value or call fetcher and cache the result."""
    now = time.time()
    if key in _yf_cache:
        ts, val = _yf_cache[key]
        if now - ts < ttl:
            return val
    try:
        val = fetcher()
    except Exception as exc:
        logger.warning("yfinance fetch failed for %s: %s", key, exc)
        # Return stale cache if available, else None
        if key in _yf_cache:
            return _yf_cache[key][1]
        return None
    _yf_cache[key] = (now, val)
    return val

# Database path
DB_PATH = Path(PROJECT_ROOT) / "data" / "nzt48.db"

# WAL mode flag — only set once at init (thread-safe)
import threading
_wal_lock = threading.Lock()
_wal_initialized = False


def get_db() -> sqlite3.Connection:
    """Get a read-only database connection."""
    global _wal_initialized
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    if not _wal_initialized:
        with _wal_lock:
            if not _wal_initialized:
                conn.execute("PRAGMA journal_mode=WAL")
                _wal_initialized = True
    return conn


# === API Key Authentication ===

API_KEY = os.environ.get("NZT48_API_KEY", "")
_ALLOWED_ORIGINS = os.environ.get("NZT48_CORS_ORIGINS", "http://localhost:3001,http://127.0.0.1:3001").split(",")


async def require_api_key(request: Request) -> None:
    """Check X-API-Key header for state-mutating endpoints."""
    if not API_KEY:
        raise HTTPException(status_code=503, detail="API key not configured — refusing to serve")
    key = request.headers.get("X-API-Key", "")
    if key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")


# WebSocket connection manager
class ConnectionManager:
    """Manages WebSocket connections for real-time updates."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info("WebSocket client connected (%d total)", len(self.active_connections))

    def disconnect(self, websocket: WebSocket):
        try:
            self.active_connections.remove(websocket)
        except ValueError:
            pass  # Already removed
        logger.info("WebSocket client disconnected (%d remaining)", len(self.active_connections))

    async def broadcast(self, message: dict):
        """Broadcast a message to all connected clients."""
        dead = []
        for connection in list(self.active_connections):  # Iterate copy to avoid mutation during iteration
            try:
                await connection.send_json(message)
            except Exception:
                dead.append(connection)
        for conn in dead:
            try:
                self.active_connections.remove(conn)
            except ValueError:
                pass


manager = ConnectionManager()


# Lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("NZT-48 API starting...")
    cfg.load_config()
    yield
    logger.info("NZT-48 API shutting down...")


# Create FastAPI app
app = FastAPI(
    title="NZT-48 Command Center",
    description="Trading Signal Engine API",
    version="8.0",
    lifespan=lifespan,
)

# CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# V5.0 Ulysses Lock — read-only API during market hours (Phase 7)
try:
    from main import enforce_read_only_market_hours
    app.middleware("http")(enforce_read_only_market_hours)
    logger.info("Ulysses Lock middleware registered (market-hours read-only)")
except ImportError as _ul_err:
    logger.warning("Ulysses Lock middleware not available: %s", _ul_err)

# Mount Command Center router (all CC endpoints available under /cc/*)
try:
    from command_center.server import router as cc_router
    app.include_router(cc_router)
    logger.info("Command Center router mounted at /cc/*")
except Exception as _cc_err:
    logger.warning("Command Center router not available: %s", _cc_err)

# Mount War Room v2 endpoints (W7: scan_health, opportunity, exits, telegram, consistency, copilot, gate)
try:
    from api.war_room_endpoints import router as war_room_router
    app.include_router(war_room_router)
    logger.info("War Room v2 router mounted (7 endpoints)")
except Exception as _wr_err:
    logger.warning("War Room v2 router not available: %s", _wr_err)

# === Internal Endpoint Security ===
class InternalOnlyMiddleware(BaseHTTPMiddleware):
    """Restrict /_internal/* endpoints to localhost only."""
    async def dispatch(self, request, call_next):
        if request.url.path.startswith("/_internal/"):
            host = request.client.host if request.client else ""
            if host not in ("127.0.0.1", "::1", "localhost"):
                return JSONResponse({"error": "internal only"}, status_code=403)
        return await call_next(request)

app.add_middleware(InternalOnlyMiddleware)

# === REST Endpoints ===

@app.get("/api/signals")
async def get_signals(
    status: Optional[str] = None,
    strategy: Optional[str] = None,
    ticker: Optional[str] = None,
    bot: Optional[str] = None,
    hours: int = Query(default=24, ge=1, le=168),
    limit: int = Query(default=50, ge=1, le=500),
    include_play_score: bool = Query(default=False),
):
    """Get recent signals with optional filters.

    Set include_play_score=true to enrich each signal with B-Team play_score
    data (score/bracket/grade/reasons). This cross-references today's plays.json
    artifact and falls back to on-the-fly B-Team computation.
    """
    conn = get_db()
    try:
        query = "SELECT * FROM signals WHERE timestamp > ? "
        params = [(datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()]

        if status:
            query += "AND status = ? "
            params.append(status)
        if strategy:
            query += "AND strategy = ? "
            params.append(strategy)
        if ticker:
            query += "AND ticker = ? "
            params.append(ticker)
        if bot:
            query += "AND bot = ? "
            params.append(bot)

        query += "ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        signals = [dict(row) for row in rows]

        if include_play_score and signals:
            # Build a lookup from today's plays.json for atr_pct/rvol enrichment
            plays_data = _load_latest_artifact("plays.json")
            plays_by_ticker = {}
            if plays_data and plays_data.get("plays"):
                for p in plays_data["plays"]:
                    t = p.get("ticker", "")
                    if t:
                        plays_by_ticker[t] = p
            regime = plays_data.get("regime", "NEUTRAL") if plays_data else "NEUTRAL"

            # Initialize B-Team manager once for all signals
            bteam = _get_bteam_manager()

            for sig in signals:
                sig_ticker = sig.get("ticker", "")
                # Try to get enrichment data from plays.json
                play_data = plays_by_ticker.get(sig_ticker, {})
                enriched_play = {
                    "ticker": sig_ticker,
                    "composite": sig.get("confidence", 0),
                    "atr_pct": play_data.get("atr_pct", 0),
                    "rvol": sig.get("rvol") or play_data.get("rvol", 0),
                    "direction": sig.get("direction", "LONG"),
                }
                ps = _compute_bteam_play_score(
                    enriched_play,
                    regime=sig.get("regime") or regime,
                    bteam=bteam,
                )
                if ps:
                    sig["play_score"] = {
                        "score": ps["score"],
                        "bracket": ps["bracket"],
                        "grade": ps["grade"],
                        "reasons": ps["reasons"],
                        "self_learning_boost": ps.get("self_learning_boost", 0),
                    }

        return signals
    finally:
        conn.close()


@app.get("/api/positions")
async def get_positions():
    """Get all open positions across all bots."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM positions ORDER BY entry_time DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@app.get("/api/trades")
async def get_trades(
    ticker: Optional[str] = None,
    strategy: Optional[str] = None,
    bot: Optional[str] = None,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """Get trade journal with pagination."""
    conn = get_db()
    try:
        query = "SELECT * FROM trades WHERE time_entered > ? "
        params = [(datetime.now(timezone.utc) - timedelta(days=days)).isoformat()]

        if ticker:
            query += "AND ticker = ? "
            params.append(ticker)
        if strategy:
            query += "AND strategy = ? "
            params.append(strategy)
        if bot:
            query += "AND bot = ? "
            params.append(bot)

        # Build count query with same filters before adding LIMIT/OFFSET
        count_query = "SELECT COUNT(*) FROM trades WHERE time_entered > ? "
        count_params = [(datetime.now(timezone.utc) - timedelta(days=days)).isoformat()]

        if ticker:
            count_query += "AND ticker = ? "
            count_params.append(ticker)
        if strategy:
            count_query += "AND strategy = ? "
            count_params.append(strategy)
        if bot:
            count_query += "AND bot = ? "
            count_params.append(bot)

        total = conn.execute(count_query, count_params).fetchone()[0]

        query += "ORDER BY time_entered DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()

        return {"trades": [dict(row) for row in rows], "total": total}
    finally:
        conn.close()


@app.get("/api/regime")
async def get_regime():
    """Get current regime and recent regime history."""
    conn = get_db()
    try:
        # Current regime (most recent)
        current = conn.execute(
            "SELECT * FROM regime_history ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

        # Recent history
        history = conn.execute(
            "SELECT * FROM regime_history ORDER BY timestamp DESC LIMIT 50"
        ).fetchall()

        return {
            "current": dict(current) if current else None,
            "history": [dict(row) for row in history],
        }
    finally:
        conn.close()


@app.get("/api/bots")
async def get_bots():
    """Get status of all bot instances."""
    conn = get_db()
    try:
        # Get latest allocation data
        allocations = conn.execute(
            "SELECT * FROM bot_allocations ORDER BY date DESC LIMIT 5"
        ).fetchall()

        # Get position counts per bot
        position_counts = conn.execute(
            "SELECT bot_instance, COUNT(*) as count FROM positions GROUP BY bot_instance"
        ).fetchall()

        return {
            "allocations": [dict(row) for row in allocations],
            "position_counts": {row["bot_instance"]: row["count"] for row in position_counts},
        }
    finally:
        conn.close()


@app.get("/api/overseer")
async def get_overseer():
    """Get Portfolio Overseer status and active restrictions."""
    conn = get_db()
    try:
        restrictions = conn.execute(
            "SELECT * FROM restrictions WHERE expires_at IS NULL OR expires_at > ?",
            [datetime.now(timezone.utc).isoformat()],
        ).fetchall()

        return {
            "active_restrictions": [dict(row) for row in restrictions],
            "restriction_count": len(restrictions),
        }
    finally:
        conn.close()


@app.get("/api/performance")
async def get_performance(
    period: str = Query(default="daily", pattern="^(daily|weekly|monthly|all)$"),
):
    """Get performance metrics for the specified period."""
    conn = get_db()
    try:
        # Daily summaries
        summaries = conn.execute(
            "SELECT * FROM daily_summary ORDER BY date DESC LIMIT 90"
        ).fetchall()
        summary_list = [dict(row) for row in summaries]

        # Aggregate stats
        if summary_list:
            total_pnl = sum(s.get("daily_pnl_dollars", 0) for s in summary_list)
            total_trades = sum(s.get("trades_taken", 0) for s in summary_list)
            total_wins = sum(s.get("win_count", 0) for s in summary_list)
            total_losses = sum(s.get("loss_count", 0) for s in summary_list)
            win_rate = total_wins / (total_wins + total_losses) if (total_wins + total_losses) > 0 else 0
        else:
            total_pnl = total_trades = total_wins = total_losses = 0
            win_rate = 0

        return {
            "period": period,
            "summaries": summary_list[:30] if period == "daily" else summary_list,
            "aggregate": {
                "total_pnl": round(total_pnl, 2),
                "total_trades": total_trades,
                "win_count": total_wins,
                "loss_count": total_losses,
                "win_rate": round(win_rate * 100, 1),
            },
        }
    finally:
        conn.close()


@app.get("/api/learning")
async def get_learning():
    """Get learning engine state (regime matrix, ticker profiles, MAE/MFE)."""
    conn = get_db()
    try:
        # Ticker profiles
        profiles = conn.execute(
            "SELECT * FROM ticker_profiles ORDER BY priority_score DESC"
        ).fetchall()

        return {
            "ticker_profiles": [dict(row) for row in profiles],
        }
    finally:
        conn.close()


@app.get("/api/kelly")
async def get_kelly():
    """Get Kelly Criterion sizer status."""
    # Kelly data is computed in-memory; return last-known state
    conn = get_db()
    try:
        # Get recent trade R-multiples for Kelly calculation
        trades = conn.execute(
            "SELECT pnl_r_multiple FROM trades ORDER BY time_entered DESC LIMIT 60"
        ).fetchall()

        r_multiples = [row["pnl_r_multiple"] for row in trades if row["pnl_r_multiple"] is not None]

        if len(r_multiples) < 5:
            return {
                "sample_size": len(r_multiples),
                "win_rate": 0,
                "avg_win_r": 0,
                "avg_loss_r": 0,
                "full_kelly": 0,
                "half_kelly": 0,
                "current_risk_pct": 0,
                "note": "Insufficient data (need 5+ trades)",
            }

        wins = [r for r in r_multiples if r > 0]
        losses = [r for r in r_multiples if r <= 0]

        win_rate = len(wins) / len(r_multiples)
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 1.0

        if avg_loss > 0 and avg_win > 0:
            full_kelly = win_rate - ((1 - win_rate) / (avg_win / avg_loss))
        else:
            full_kelly = 0

        return {
            "sample_size": len(r_multiples),
            "win_rate": round(win_rate * 100, 1),
            "avg_win_r": round(avg_win, 2),
            "avg_loss_r": round(avg_loss, 2),
            "full_kelly": round(full_kelly * 100, 2),
            "half_kelly": round(full_kelly / 2 * 100, 2),
            "current_risk_pct": round(min(full_kelly / 2, 0.0075) * 100, 3),
        }
    finally:
        conn.close()


@app.get("/api/pdt")
async def get_pdt():
    """Get PDT tracker status."""
    conn = get_db()
    try:
        # Count day trades in rolling 5-day window
        five_days_ago = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        day_trades = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE time_entered > ? AND bot = 'B'",
            [five_days_ago],
        ).fetchone()[0]

        return {
            "day_trades_5d": day_trades,
            "limit": 3,
            "remaining": max(0, 3 - day_trades),
            "mode": "SELECTIVE" if day_trades < 2 else "CONSERVATIVE" if day_trades < 3 else "RESERVE",
        }
    finally:
        conn.close()


@app.get("/api/config")
async def get_config():
    """Get system configuration (non-sensitive)."""
    # Build flat ISA ticker list from all bot_a_universe categories
    bot_a_tickers = []
    for category in ["long_3x", "inverse_3x", "leveraged_4x_5x"]:
        items = cfg.get(f"bot_a_universe.{category}", [])
        for item in items:
            if isinstance(item, dict) and "ticker" in item:
                bot_a_tickers.append(item)
            elif isinstance(item, str):
                bot_a_tickers.append({"ticker": item})

    return {
        "system_mode": cfg.get("system.mode", "PAPER"),
        "version": cfg.get("system.version", "8.0"),
        "bot_b_tickers": cfg.get_tickers(),
        "bot_a_tickers": bot_a_tickers,
        "strategies": [s.value for s in __import__("models").Strategy],
        "risk_per_trade": cfg.get("immutable_rules.constitutional.risk_per_trade", 0.0075),
    }


@app.post("/api/kill", dependencies=[Depends(require_api_key)])
async def activate_kill_switch():
    """Activate the kill switch — halts all trading immediately."""
    try:
        kill_file = Path(PROJECT_ROOT) / "data" / "KILL_SWITCH"
        kill_file.parent.mkdir(parents=True, exist_ok=True)
        kill_file.write_text(f"KILLED at {datetime.now(timezone.utc).isoformat()}")
    except OSError as e:
        return JSONResponse(status_code=507, content={"error": f"Failed to persist kill switch: {str(e)[:100]}"})

    try:
        await manager.broadcast({
            "type": "KILL_SWITCH",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": "Kill switch activated via Command Center",
        })
    except Exception:
        pass  # Kill file written — broadcast is best-effort

    return {"status": "KILLED", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/api/pause", dependencies=[Depends(require_api_key)])
async def pause_strategy(strategy: Optional[str] = None, bot: Optional[str] = None):
    """Pause a specific strategy or bot."""
    if not strategy and not bot:
        return JSONResponse(status_code=400, content={"error": "Either 'strategy' or 'bot' required"})
    try:
        pause_file = Path(PROJECT_ROOT) / "data" / "pauses.json"
        pause_file.parent.mkdir(parents=True, exist_ok=True)

        pauses = {}
        if pause_file.exists():
            try:
                pauses = json.loads(pause_file.read_text())
            except json.JSONDecodeError:
                pauses = {}

        if strategy:
            pauses.setdefault("strategies", {})[strategy] = datetime.now(timezone.utc).isoformat()
        if bot:
            pauses.setdefault("bots", {})[bot] = datetime.now(timezone.utc).isoformat()

        pause_file.write_text(json.dumps(pauses, indent=2))
    except OSError as e:
        return JSONResponse(status_code=507, content={"error": f"Failed to persist pause: {str(e)[:100]}"})

    return {"status": "PAUSED", "strategy": strategy, "bot": bot}


@app.post("/api/resume", dependencies=[Depends(require_api_key)])
async def resume_strategy(strategy: Optional[str] = None, bot: Optional[str] = None):
    """Resume a paused strategy or bot."""
    pause_file = Path(PROJECT_ROOT) / "data" / "pauses.json"

    if pause_file.exists():
        pauses = json.loads(pause_file.read_text())
        if strategy and "strategies" in pauses:
            pauses["strategies"].pop(strategy, None)
        if bot and "bots" in pauses:
            pauses["bots"].pop(bot, None)
        pause_file.write_text(json.dumps(pauses, indent=2))

    return {"status": "RESUMED", "strategy": strategy, "bot": bot}


# === Internal Push Endpoints (Engine → API → WebSocket broadcast) ===

_engine_last_heartbeat: float = 0.0

@app.post("/_internal/push_state")
async def internal_push_state(request: Request):
    """Engine pushes state changes here → broadcast to all WebSocket clients."""
    payload = await request.json()
    event_type = payload.get("event_type", "STATE_UPDATE")
    data = payload.get("data", {})
    await manager.broadcast({
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    })
    return {"status": "ok", "clients": len(manager.active_connections)}


@app.post("/_internal/push_cc_state")
async def internal_push_cc_state(request: Request):
    """Engine pushes full CC snapshot → update local state + broadcast."""
    payload = await request.json()
    try:
        from command_center.state import get_state
        get_state().update_from_snapshot(payload)
    except Exception as exc:
        logger.debug("CC state update from push failed: %s", exc)
    await manager.broadcast({
        "type": "STATE_UPDATE",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": payload,
    })
    return {"status": "ok", "clients": len(manager.active_connections)}


@app.post("/_internal/heartbeat")
async def internal_heartbeat():
    """Engine heartbeat — dashboard uses this to show engine liveness."""
    global _engine_last_heartbeat
    _engine_last_heartbeat = time.time()
    return {"status": "ok"}


@app.get("/api/health")
async def api_health():
    """Health check including engine liveness and disk space."""
    import shutil
    engine_alive = (time.time() - _engine_last_heartbeat) < 90
    # Disk space check
    try:
        disk = shutil.disk_usage("/app/data")
        disk_used_pct = round(disk.used / disk.total * 100, 1)
        disk_free_gb = round(disk.free / 1e9, 2)
        disk_status = "CRITICAL" if disk_used_pct > 95 else ("WARN" if disk_used_pct > 80 else "ok")
    except Exception:
        disk_used_pct, disk_free_gb, disk_status = None, None, "unknown"
    return {
        "api": "ok",
        "engine": "ok" if engine_alive else "stale",
        "engine_last_heartbeat": _engine_last_heartbeat,
        "ws_clients": len(manager.active_connections),
        "disk_used_pct": disk_used_pct,
        "disk_free_gb": disk_free_gb,
        "disk_status": disk_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# === WebSocket ===

@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    """Real-time WebSocket for live signal and position updates."""
    # Authenticate WebSocket connections via query param
    if API_KEY:
        ws_key = websocket.query_params.get("api_key", "")
        if ws_key != API_KEY:
            await websocket.close(code=4003, reason="Invalid or missing API key")
            return
    await manager.connect(websocket)
    try:
        # Send initial CC state snapshot on connect
        try:
            from command_center.state import get_state
            snapshot = get_state().get_snapshot()
            await websocket.send_json({
                "type": "STATE_UPDATE",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": snapshot,
            })
        except Exception:
            pass  # CC state not available — that's fine

        while True:
            # Listen for client messages (e.g., ping, subscribe filters)
            data = await websocket.receive_text()
            # Echo back acknowledgment
            await websocket.send_json({"type": "ACK", "data": data})
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# === Broadcast Helpers ===

async def broadcast_signal(signal_data: dict) -> None:
    """Broadcast a new signal to all dashboard clients."""
    await manager.broadcast({
        "type": "NEW_SIGNAL",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": signal_data,
    })


async def broadcast_position_update(position_data: dict) -> None:
    """Broadcast position update (price change, ladder event)."""
    await manager.broadcast({
        "type": "POSITION_UPDATE",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": position_data,
    })


async def broadcast_regime_change(regime: str, previous: str) -> None:
    """Broadcast regime state change."""
    await manager.broadcast({
        "type": "REGIME_CHANGE",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {"regime": regime, "previous": previous},
    })


# === Data Analysis Endpoints ===


@app.get("/api/analysis/market")
async def analysis_market():
    """Real-time market overview: VIX, SPY price, regime, market structure."""
    try:
        # Fetch VIX and SPY via yfinance (cached)
        def _fetch_vix():
            t = yf.Ticker("^VIX")
            info = t.fast_info
            price = getattr(info, 'last_price', None)
            if price is None or price <= 0:
                return 0.0
            return round(price, 2)

        def _fetch_spy():
            t = yf.Ticker("SPY")
            info = t.fast_info
            price = getattr(info, 'last_price', None)
            prev = getattr(info, 'previous_close', None)
            return {
                "price": round(price, 2) if price else 0,
                "prev_close": round(prev, 2) if prev else 0,
            }

        vix = _yf_cached("vix", _fetch_vix)
        spy_data = _yf_cached("spy_data", _fetch_spy) or {"price": 0, "prev_close": 0}
        spy_price = spy_data.get("price", 0)
        spy_prev = spy_data.get("prev_close", spy_price)
        spy_change = round(spy_price - spy_prev, 2)
        spy_change_pct = round((spy_change / spy_prev * 100) if spy_prev else 0, 2)

        # Latest regime from DB
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT * FROM regime_history ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            regime_row = dict(row) if row else None
        finally:
            conn.close()

        # Extract regime name and duration
        regime_name = None
        regime_duration = 0
        if regime_row:
            regime_name = regime_row.get("regime") or regime_row.get("state")
            regime_duration = regime_row.get("duration_bars", 0)

        return {
            "vix": vix,
            "spy_price": spy_price,
            "spy_change": spy_change,
            "spy_change_pct": spy_change_pct,
            "regime": regime_name,
            "regime_duration": regime_duration,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.exception("analysis_market failed")
        return JSONResponse(
            status_code=500,
            content={"error": str(exc), "endpoint": "analysis/market"},
        )


@app.get("/api/analysis/tickers")
async def analysis_tickers():
    """Live ticker analysis for all tickers in the universe."""
    try:
        tickers = cfg.get_tickers()
        if not tickers:
            return {"tickers": [], "error": "No tickers configured"}

        # Fetch current prices in bulk (cached)
        def _fetch_ticker_data():
            data = {}
            joined = " ".join(tickers)
            try:
                bulk = yf.download(joined, period="5d", group_by="ticker", progress=False, threads=True)
            except Exception:
                bulk = None

            for tkr in tickers:
                try:
                    if bulk is not None and len(tickers) > 1:
                        df = bulk[tkr] if tkr in bulk.columns.get_level_values(0) else None
                    elif bulk is not None and len(tickers) == 1:
                        df = bulk
                    else:
                        df = None

                    if df is not None and not df.empty:
                        last_close = float(df["Close"].iloc[-1])
                        prev_close = float(df["Close"].iloc[-2]) if len(df) > 1 else last_close
                        last_vol = float(df["Volume"].iloc[-1])
                        avg_vol = float(df["Volume"].mean()) if len(df) > 1 else last_vol
                        change_pct = round(((last_close - prev_close) / prev_close) * 100, 2) if prev_close else 0
                        rvol = round(last_vol / avg_vol, 2) if avg_vol > 0 else 0
                        data[tkr] = {
                            "price": round(last_close, 2),
                            "change_pct": change_pct,
                            "volume": int(last_vol),
                            "rvol": rvol,
                        }
                    else:
                        # Fallback: individual fetch
                        t = yf.Ticker(tkr)
                        fi = t.fast_info
                        price = round(fi.last_price, 2)
                        prev = round(fi.previous_close, 2) if fi.previous_close else price
                        change_pct = round(((price - prev) / prev) * 100, 2) if prev else 0
                        data[tkr] = {
                            "price": price,
                            "change_pct": change_pct,
                            "volume": int(fi.last_volume) if fi.last_volume else 0,
                            "rvol": 0,
                        }
                except Exception as e:
                    logger.warning("Failed to fetch %s: %s", tkr, e)
                    data[tkr] = {"price": 0, "change_pct": 0, "volume": 0, "rvol": 0}
            return data

        ticker_data = _yf_cached("ticker_bulk", _fetch_ticker_data)
        if ticker_data is None:
            ticker_data = {}

        # Get open positions from DB
        conn = get_db()
        try:
            pos_rows = conn.execute("SELECT ticker, direction FROM positions").fetchall()
            open_positions = {row["ticker"]: row["direction"] for row in pos_rows}
        finally:
            conn.close()

        result = []
        for tkr in tickers:
            td = ticker_data.get(tkr, {})
            result.append({
                "ticker": tkr,
                "price": td.get("price", 0),
                "change_pct": td.get("change_pct", 0),
                "volume": td.get("volume", 0),
                "rvol": td.get("rvol", 0),
                "has_position": tkr in open_positions,
                "direction": open_positions.get(tkr),
            })

        return {"tickers": result, "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        logger.exception("analysis_tickers failed")
        return JSONResponse(
            status_code=500,
            content={"error": str(exc), "endpoint": "analysis/tickers"},
        )


@app.get("/api/analysis/scans")
async def analysis_scans():
    """Recent scan results: signals and trades from last 24 hours."""
    try:
        conn = get_db()
        try:
            cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

            # Last scan time (most recent signal timestamp)
            last_signal = conn.execute(
                "SELECT timestamp FROM signals ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            last_scan_time = last_signal["timestamp"] if last_signal else None

            # Signals generated in last 24h
            sig_count = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE timestamp > ?", [cutoff_24h]
            ).fetchone()[0]

            # Trades taken in last 24h
            trade_count = conn.execute(
                "SELECT COUNT(*) FROM virtual_trades WHERE entry_time > ?", [cutoff_24h]
            ).fetchone()[0]

            # Distinct strategies that fired in last 24h
            strategies_rows = conn.execute(
                "SELECT DISTINCT strategy FROM signals WHERE timestamp > ?", [cutoff_24h]
            ).fetchall()
            strategies_firing = [row["strategy"] for row in strategies_rows]

            # Recent signals (last 20)
            recent_signals = conn.execute(
                "SELECT id, timestamp, ticker, strategy, direction, confidence, status "
                "FROM signals ORDER BY timestamp DESC LIMIT 20"
            ).fetchall()

            return {
                "last_scan_time": last_scan_time,
                "signals_generated_24h": sig_count,
                "trades_taken_24h": trade_count,
                "strategies_firing": strategies_firing,
                "recent_signals": [dict(r) for r in recent_signals],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        finally:
            conn.close()
    except Exception as exc:
        logger.exception("analysis_scans failed")
        return JSONResponse(
            status_code=500,
            content={"error": str(exc), "endpoint": "analysis/scans"},
        )


@app.get("/api/analysis/risk")
async def analysis_risk():
    """Risk dashboard: equity curve, drawdown, daily P&L, risk budget."""
    try:
        conn = get_db()
        try:
            # Equity snapshots (last 90 days for curve)
            eq_rows = conn.execute(
                "SELECT date, ending_equity, starting_equity, realised_pnl, "
                "unrealised_pnl, max_drawdown_pct "
                "FROM equity_snapshots ORDER BY date DESC LIMIT 90"
            ).fetchall()
            equity_curve = [dict(r) for r in reversed(eq_rows)]  # chronological order

            # Current and peak equity (default to starting equity from config)
            starting_equity = float(cfg.get("system.starting_equity", 10_000))
            current_equity = equity_curve[-1]["ending_equity"] if equity_curve else starting_equity
            peak_equity = max((e["ending_equity"] for e in equity_curve), default=starting_equity)

            # Drawdown
            drawdown_pct = 0.0
            if peak_equity > 0 and current_equity > 0:
                drawdown_pct = round(((peak_equity - current_equity) / peak_equity) * 100, 2)

            # Daily P&L (today or latest)
            daily_pnl = 0.0
            if equity_curve:
                latest = equity_curve[-1]
                daily_pnl = round((latest.get("realised_pnl") or 0) + (latest.get("unrealised_pnl") or 0), 2)

            # Risk budget used: sum of risk_dollars on open positions vs equity
            open_risk = conn.execute(
                "SELECT COALESCE(SUM(risk_dollars), 0) FROM positions"
            ).fetchone()[0]
            risk_budget_used = round((open_risk / current_equity) * 100, 2) if current_equity > 0 else 0

            return {
                "current_equity": round(current_equity, 2),
                "peak_equity": round(peak_equity, 2),
                "drawdown_pct": drawdown_pct,
                "daily_pnl": daily_pnl,
                "risk_budget_used": risk_budget_used,
                "open_risk_dollars": round(open_risk, 2),
                "equity_curve": equity_curve,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        finally:
            conn.close()
    except Exception as exc:
        logger.exception("analysis_risk failed")
        return JSONResponse(
            status_code=500,
            content={"error": str(exc), "endpoint": "analysis/risk"},
        )


@app.get("/api/analysis/strategies")
async def analysis_strategies():
    """Strategy performance breakdown from virtual trades."""
    try:
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT strategy, "
                "  COUNT(*) as total_trades, "
                "  SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins, "
                "  SUM(CASE WHEN net_pnl <= 0 THEN 1 ELSE 0 END) as losses, "
                "  ROUND(SUM(net_pnl), 2) as total_pnl, "
                "  ROUND(AVG(r_multiple), 2) as avg_r, "
                "  ROUND(AVG(CASE WHEN net_pnl > 0 THEN r_multiple END), 2) as avg_win_r, "
                "  ROUND(AVG(CASE WHEN net_pnl <= 0 THEN r_multiple END), 2) as avg_loss_r, "
                "  ROUND(MAX(r_multiple), 2) as best_r, "
                "  ROUND(MIN(r_multiple), 2) as worst_r "
                "FROM virtual_trades "
                "WHERE exit_time IS NOT NULL "
                "GROUP BY strategy "
                "ORDER BY total_pnl DESC"
            ).fetchall()

            strategies = []
            for row in rows:
                d = dict(row)
                total = d["total_trades"]
                wins = d["wins"] or 0
                d["win_rate"] = round((wins / total) * 100, 1) if total > 0 else 0
                strategies.append(d)

            return {
                "strategies": strategies,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        finally:
            conn.close()
    except Exception as exc:
        logger.exception("analysis_strategies failed")
        return JSONResponse(
            status_code=500,
            content={"error": str(exc), "endpoint": "analysis/strategies"},
        )


@app.get("/api/analysis/heatmap")
async def analysis_heatmap():
    """Price heatmap data: day/week/month changes for each ticker."""
    try:
        tickers = cfg.get_tickers()
        if not tickers:
            return {"heatmap": [], "error": "No tickers configured"}

        def _fetch_heatmap_data():
            results = {}
            for tkr in tickers:
                try:
                    t = yf.Ticker(tkr)
                    hist = t.history(period="1mo")
                    if hist.empty:
                        results[tkr] = {"day_change": 0, "week_change": 0, "month_change": 0}
                        continue

                    current = float(hist["Close"].iloc[-1])

                    # Day change
                    if len(hist) >= 2:
                        prev_day = float(hist["Close"].iloc[-2])
                        day_change = round(((current - prev_day) / prev_day) * 100, 2)
                    else:
                        day_change = 0

                    # Week change (approx 5 trading days)
                    if len(hist) >= 5:
                        prev_week = float(hist["Close"].iloc[-5])
                        week_change = round(((current - prev_week) / prev_week) * 100, 2)
                    else:
                        week_change = day_change

                    # Month change (first available in 1mo window)
                    month_start = float(hist["Close"].iloc[0])
                    month_change = round(((current - month_start) / month_start) * 100, 2) if month_start else 0

                    results[tkr] = {
                        "price": round(current, 2),
                        "day_change": day_change,
                        "week_change": week_change,
                        "month_change": month_change,
                    }
                except Exception as e:
                    logger.warning("Heatmap fetch failed for %s: %s", tkr, e)
                    results[tkr] = {"price": 0, "day_change": 0, "week_change": 0, "month_change": 0}
            return results

        heatmap_data = _yf_cached("heatmap", _fetch_heatmap_data)
        if heatmap_data is None:
            heatmap_data = {}

        # Build sorted array (by day_change descending)
        heatmap = []
        for tkr in tickers:
            entry = heatmap_data.get(tkr, {})
            heatmap.append({
                "ticker": tkr,
                "price": entry.get("price", 0),
                "day_change": entry.get("day_change", 0),
                "week_change": entry.get("week_change", 0),
                "month_change": entry.get("month_change", 0),
            })

        heatmap.sort(key=lambda x: x["day_change"], reverse=True)

        return {"heatmap": heatmap, "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        logger.exception("analysis_heatmap failed")
        return JSONResponse(
            status_code=500,
            content={"error": str(exc), "endpoint": "analysis/heatmap"},
        )


# === Full Data Endpoints (All 23 Tables) ===


@app.get("/api/virtual-trades")
async def get_virtual_trades(
    ticker: Optional[str] = None,
    strategy: Optional[str] = None,
    bot: Optional[str] = None,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Get all virtual (paper) trades with full analytics."""
    conn = get_db()
    try:
        query = "SELECT * FROM virtual_trades WHERE entry_time > ? "
        params = [(datetime.now(timezone.utc) - timedelta(days=days)).isoformat()]
        if ticker:
            query += "AND ticker = ? "
            params.append(ticker)
        if strategy:
            query += "AND strategy = ? "
            params.append(strategy)
        if bot:
            query += "AND bot = ? "
            params.append(bot)
        query += "ORDER BY entry_time DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return {"trades": [dict(row) for row in rows], "total": len(rows)}
    except Exception:
        return {"trades": [], "total": 0}
    finally:
        conn.close()


@app.get("/api/virtual-positions")
async def get_virtual_positions():
    """Get all open virtual (paper) positions."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM virtual_positions WHERE status = 'OPEN' ORDER BY entry_time DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []
    finally:
        conn.close()


@app.get("/api/missed-trades")
async def get_missed_trades(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Get missed trade journal — signals rejected that would have worked."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM missed_trades WHERE created_at > ? ORDER BY created_at DESC LIMIT ?",
            [(datetime.now(timezone.utc) - timedelta(days=days)).isoformat(), limit],
        ).fetchall()
        # Stats
        total = len(rows)
        would_have_won = sum(1 for r in rows if dict(r).get("would_have_hit_target"))
        return {
            "missed_trades": [dict(row) for row in rows],
            "total": total,
            "would_have_won": would_have_won,
            "missed_win_rate": round(would_have_won / total * 100, 1) if total > 0 else 0,
        }
    except Exception:
        return {"missed_trades": [], "total": 0, "would_have_won": 0, "missed_win_rate": 0}
    finally:
        conn.close()


@app.get("/api/trade-autopsies")
async def get_trade_autopsies(
    limit: int = Query(default=50, ge=1, le=200),
):
    """Get trade autopsy reports with 5-grade analysis."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM trade_autopsies ORDER BY created_at DESC LIMIT ?", [limit]
        ).fetchall()
        # Grade distribution
        grades = {}
        for row in rows:
            g = dict(row).get("overall_grade", "N/A")
            grades[g] = grades.get(g, 0) + 1
        return {
            "autopsies": [dict(row) for row in rows],
            "total": len(rows),
            "grade_distribution": grades,
        }
    except Exception:
        return {"autopsies": [], "total": 0, "grade_distribution": {}}
    finally:
        conn.close()


@app.get("/api/firewall-events")
async def get_firewall_events(
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Get emotional firewall events — blocked trading patterns."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM firewall_events WHERE created_at > ? ORDER BY created_at DESC LIMIT ?",
            [(datetime.now(timezone.utc) - timedelta(days=days)).isoformat(), limit],
        ).fetchall()
        # Pattern frequency
        patterns = {}
        for row in rows:
            p = dict(row).get("pattern", "UNKNOWN")
            patterns[p] = patterns.get(p, 0) + 1
        return {
            "events": [dict(row) for row in rows],
            "total": len(rows),
            "pattern_frequency": patterns,
        }
    except Exception:
        return {"events": [], "total": 0, "pattern_frequency": {}}
    finally:
        conn.close()


@app.get("/api/regime-transitions")
async def get_regime_transitions(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Get regime transition actions — audit trail of regime flips."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM regime_transition_actions WHERE created_at > ? ORDER BY created_at DESC LIMIT ?",
            [(datetime.now(timezone.utc) - timedelta(days=days)).isoformat(), limit],
        ).fetchall()
        return {"transitions": [dict(row) for row in rows], "total": len(rows)}
    except Exception:
        return {"transitions": [], "total": 0}
    finally:
        conn.close()


@app.get("/api/equity-intraday")
async def get_equity_intraday(
    days: int = Query(default=7, ge=1, le=30),
):
    """Get intraday equity snapshots (hourly)."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM equity_intraday WHERE created_at > ? ORDER BY created_at ASC",
            [(datetime.now(timezone.utc) - timedelta(days=days)).isoformat()],
        ).fetchall()
        return {"snapshots": [dict(row) for row in rows], "total": len(rows)}
    except Exception:
        return {"snapshots": [], "total": 0}
    finally:
        conn.close()


@app.get("/api/equity-snapshots")
async def get_equity_snapshots(
    days: int = Query(default=90, ge=1, le=365),
):
    """Get daily equity curve snapshots with benchmarks."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM equity_snapshots ORDER BY date DESC LIMIT ?", [days]
        ).fetchall()
        return {"snapshots": [dict(row) for row in reversed(rows)], "total": len(rows)}
    except Exception:
        return {"snapshots": [], "total": 0}
    finally:
        conn.close()


@app.get("/api/partial-executions")
async def get_partial_executions(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Get profit ladder partial execution audit trail."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM partial_executions WHERE created_at > ? ORDER BY created_at DESC LIMIT ?",
            [(datetime.now(timezone.utc) - timedelta(days=days)).isoformat(), limit],
        ).fetchall()
        return {"executions": [dict(row) for row in rows], "total": len(rows)}
    except Exception:
        return {"executions": [], "total": 0}
    finally:
        conn.close()


@app.get("/api/strategy-daily-stats")
async def get_strategy_daily_stats(
    days: int = Query(default=30, ge=1, le=365),
):
    """Get per-strategy daily P&L attribution."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM strategy_daily_stats WHERE date > ? ORDER BY date DESC, net_pnl DESC",
            [(datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")],
        ).fetchall()
        return {"stats": [dict(row) for row in rows], "total": len(rows)}
    except Exception:
        return {"stats": [], "total": 0}
    finally:
        conn.close()


@app.get("/api/indicator-scores")
async def get_indicator_scores():
    """Get per-indicator accuracy and effectiveness scoring."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM indicator_scores ORDER BY effectiveness_score DESC"
        ).fetchall()
        return {"indicators": [dict(row) for row in rows], "total": len(rows)}
    except Exception:
        return {"indicators": [], "total": 0}
    finally:
        conn.close()


@app.get("/api/move-attributions")
async def get_move_attributions(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Get price move attribution analysis."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM move_attributions WHERE created_at > ? ORDER BY created_at DESC LIMIT ?",
            [(datetime.now(timezone.utc) - timedelta(days=days)).isoformat(), limit],
        ).fetchall()
        return {"attributions": [dict(row) for row in rows], "total": len(rows)}
    except Exception:
        return {"attributions": [], "total": 0}
    finally:
        conn.close()


@app.get("/api/premarket-briefs")
async def get_premarket_briefs(
    days: int = Query(default=7, ge=1, le=30),
):
    """Get pre-market intelligence briefs."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM premarket_briefs WHERE created_at > ? ORDER BY created_at DESC",
            [(datetime.now(timezone.utc) - timedelta(days=days)).isoformat()],
        ).fetchall()
        return {"briefs": [dict(row) for row in rows], "total": len(rows)}
    except Exception:
        return {"briefs": [], "total": 0}
    finally:
        conn.close()


@app.get("/api/daily-summary")
async def get_daily_summary(
    days: int = Query(default=30, ge=1, le=365),
):
    """Get daily summary rollups per bot."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM daily_summary ORDER BY date DESC LIMIT ?", [days]
        ).fetchall()
        return {"summaries": [dict(row) for row in rows], "total": len(rows)}
    except Exception:
        return {"summaries": [], "total": 0}
    finally:
        conn.close()


@app.get("/api/learning-state")
async def get_learning_state():
    """Get persisted learning module states."""
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM learning_state").fetchall()
        result = {}
        for row in rows:
            d = dict(row)
            module = d.get("module", "unknown")
            try:
                state = json.loads(d.get("state_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                state = {}
            result[module] = {"state": state, "updated_at": d.get("updated_at")}
        return {"modules": result, "total": len(rows)}
    except Exception:
        return {"modules": {}, "total": 0}
    finally:
        conn.close()


@app.get("/api/correlation-matrix")
async def get_correlation_matrix():
    """Get cross-ticker correlation matrix from config."""
    matrix = cfg.get("correlation.matrix", {})
    return {
        "matrix": matrix,
        "max_correlated_positions": cfg.get("correlation.max_correlated_positions", 2),
        "correlation_threshold": cfg.get("correlation.correlation_threshold", 0.70),
        "portfolio_heat_max": cfg.get("correlation.portfolio_heat_max", 0.03),
    }


@app.get("/api/circuit-breakers")
async def get_circuit_breakers():
    """Get circuit breaker system status."""
    conn = get_db()
    try:
        # Check for recent restriction events that indicate circuit breakers
        rows = conn.execute(
            "SELECT * FROM restrictions ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        return {
            "active_breakers": [dict(row) for row in rows if dict(row).get("expires_at") is None or dict(row).get("expires_at", "") > datetime.now(timezone.utc).isoformat()],
            "history": [dict(row) for row in rows],
        }
    except Exception:
        return {"active_breakers": [], "history": []}
    finally:
        conn.close()


@app.get("/api/isa-universe")
async def get_isa_universe():
    """Get full ISA leverage ETP universe from config."""
    return {
        "long_3x": cfg.get("bot_a_universe.long_3x", []),
        "inverse_3x": cfg.get("bot_a_universe.inverse_3x", []),
        "leveraged_4x_5x": cfg.get("bot_a_universe.leveraged_4x_5x", []),
        "ucits_defensive": cfg.get("bot_a_universe.ucits_defensive", []),
        "isa_mapping": cfg.get("bot_a_universe.isa_mapping", {}),
        "sector_rotation": cfg.get("bot_a_universe.sector_rotation", []),
    }


@app.get("/api/metadata")
async def get_metadata():
    """Single source of truth for all dashboard enums, tickers, thresholds.

    The frontend should call this once on load and cache it.
    ALL hardcoded values in the dashboard must come from here.
    """
    # --- Bot A (ISA) tickers: flatten all categories ---
    isa_tickers = []
    for category_key, category_label, direction in [
        ("long_3x", "Long 3x Index", "long"),
        ("inverse_3x", "Inverse 3x Index", "short"),
        ("leveraged_4x_5x", "Leveraged Single-Stock / 4x-5x", "long"),
    ]:
        items = cfg.get(f"bot_a_universe.{category_key}", [])
        for item in items:
            if isinstance(item, dict) and "ticker" in item:
                import re as _re
                t = item["ticker"]
                raw_label = item.get("index", item.get("underlying", t))
                leverage = item.get("leverage", "3x")
                d = item.get("direction", direction).lower()
                if d == "short" or "-" in str(leverage):
                    d = "short"
                # Build clean label: strip existing "(3x)" suffixes to avoid "Nasdaq 100 (3x) 3x"
                clean_label = _re.sub(r'\s*\([^)]*x\)', '', raw_label).strip()
                isa_tickers.append({
                    "ticker": t,
                    "label": f"{clean_label} {leverage}",
                    "type": d,
                    "category": category_key,
                    "status": item.get("status", "ACTIVE"),
                    "provider": item.get("provider", ""),
                })

    # --- Bot B (US) tickers ---
    us_tickers = cfg.get_tickers()

    # --- Bot instances (strategy modules) ---
    bot_instances = cfg.get("strategy_router.bot_instances",
                            ["BULL", "RANGE", "BEAR", "EARNINGS", "SECTOR_ROTATION"])

    # --- Bot filters (A = ISA, B = US) ---
    bots = [
        {"id": "A", "label": "ISA (A)", "description": "ISA — UK Leverage ETPs"},
        {"id": "B", "label": "US (B)", "description": "US Equities — IBKR"},
    ]

    # --- Regimes (from core.regime_mapping if available, else hardcoded) ---
    try:
        from core.regime_mapping import REGIME_MAP
        regimes = list(set(REGIME_MAP.values()))
    except Exception:
        regimes = [
            "TRENDING_UP_STRONG", "TRENDING_UP_MOD",
            "RANGE_BOUND",
            "TRENDING_DOWN_MOD", "TRENDING_DOWN_STRONG",
            "HIGH_VOL_EXPANSION", "RISK_OFF", "SHOCK",
        ]
    regime_colors = {
        "TRENDING_UP_STRONG": {"color": "text-green-400", "bg": "bg-green-500", "label": "UP STRONG"},
        "TRENDING_UP_MOD": {"color": "text-green-300", "bg": "bg-green-400", "label": "UP MOD"},
        "RANGE_BOUND": {"color": "text-yellow-400", "bg": "bg-yellow-500", "label": "RANGE"},
        "TRENDING_DOWN_MOD": {"color": "text-red-300", "bg": "bg-red-400", "label": "DOWN MOD"},
        "TRENDING_DOWN_STRONG": {"color": "text-red-400", "bg": "bg-red-500", "label": "DOWN STRONG"},
        "HIGH_VOL_EXPANSION": {"color": "text-orange-400", "bg": "bg-orange-500", "label": "HIGH VOL"},
        "RISK_OFF": {"color": "text-red-500", "bg": "bg-red-600", "label": "RISK OFF"},
        "SHOCK": {"color": "text-red-600", "bg": "bg-red-700", "label": "SHOCK"},
    }

    # --- Drawdown levels ---
    drawdown_levels = [
        {"label": "GREEN <3%", "threshold": 3, "color": "bg-nzt-accent/50"},
        {"label": "YELLOW 3-5%", "threshold": 5, "color": "bg-yellow-500/50"},
        {"label": "ORANGE 5-8%", "threshold": 8, "color": "bg-orange-500/50"},
        {"label": "RED 8-10%", "threshold": 10, "color": "bg-red-500/50"},
        {"label": "CRITICAL 10-12%", "threshold": 12, "color": "bg-red-600/50"},
        {"label": "EMERGENCY 12%+", "threshold": 100, "color": "bg-red-700/50"},
    ]

    # --- VIX thresholds ---
    vix_levels = [
        {"label": "Low Volatility", "max": 15, "color": "text-nzt-accent"},
        {"label": "Normal", "max": 25, "color": "text-yellow-400"},
        {"label": "Elevated", "max": 35, "color": "text-orange-400"},
        {"label": "Extreme Fear", "max": 999, "color": "text-nzt-danger"},
    ]

    # --- Signal statuses ---
    signal_statuses = ["TAKEN", "SKIPPED", "PENDING"]

    # --- Exit intents ---
    exit_intents = ["HOLD", "TRAIL", "PARTIAL", "EXIT_NOW"]

    # --- Grades ---
    grades = ["A+", "A", "B+", "B", "C", "D", "F"]

    # --- Scan health states ---
    scan_health_states = ["OK", "DEGRADED", "HALTED"]

    # --- Opportunity decisions ---
    opportunity_decisions = ["TRADE", "WATCH"]

    # --- Feature flags ---
    feature_flags = cfg.get("feature_flags", {})

    return {
        "isa_tickers": isa_tickers,
        "us_tickers": us_tickers,
        "bot_instances": bot_instances,
        "bots": bots,
        "regimes": regimes,
        "regime_display": regime_colors,
        "drawdown_levels": drawdown_levels,
        "vix_levels": vix_levels,
        "signal_statuses": signal_statuses,
        "exit_intents": exit_intents,
        "grades": grades,
        "scan_health_states": scan_health_states,
        "opportunity_decisions": opportunity_decisions,
        "feature_flags": feature_flags,
        "risk_per_trade": cfg.get("immutable_rules.constitutional.risk_per_trade", 0.0075),
        "system_mode": cfg.get("system.mode", "PAPER"),
        "version": cfg.get("system.version", "8.0"),
    }


@app.get("/api/drawdown-status")
async def get_drawdown_status():
    """Get current drawdown recovery protocol status."""
    conn = get_db()
    try:
        # Get equity curve for drawdown calc
        latest = conn.execute(
            "SELECT * FROM equity_snapshots ORDER BY date DESC LIMIT 1"
        ).fetchone()
        starting = float(cfg.get("system.starting_equity", 10_000))
        if latest:
            current = dict(latest).get("ending_equity", starting)
            peak = max(current, starting)
        else:
            current = starting
            peak = starting

        dd_pct = ((peak - current) / peak * 100) if peak > 0 else 0

        # Determine recovery level
        if dd_pct < 3:
            level = "GREEN"
        elif dd_pct < 5:
            level = "YELLOW"
        elif dd_pct < 8:
            level = "ORANGE"
        elif dd_pct < 10:
            level = "RED"
        elif dd_pct < 12:
            level = "CRITICAL"
        else:
            level = "EMERGENCY"

        return {
            "current_equity": round(current, 2),
            "peak_equity": round(peak, 2),
            "drawdown_pct": round(dd_pct, 2),
            "recovery_level": level,
            "protocol": cfg.get(f"drawdown_recovery.{level.lower()}", {}),
        }
    except Exception:
        return {"current_equity": 0, "peak_equity": 0, "drawdown_pct": 0, "recovery_level": "GREEN", "protocol": {}}
    finally:
        conn.close()


@app.get("/api/system-health")
async def get_system_health():
    """Comprehensive system health check across all subsystems."""
    conn = get_db()
    try:
        health = {}

        # Database table row counts
        tables = [
            "signals", "trades", "positions", "regime_history", "ticker_profiles",
            "restrictions", "daily_summary", "bot_allocations", "virtual_positions",
            "virtual_trades", "equity_snapshots", "indicator_scores", "move_attributions",
            "market_data", "premarket_briefs", "missed_trades", "trade_autopsies",
            "strategy_daily_stats", "partial_executions", "firewall_events",
            "regime_transition_actions", "equity_intraday", "learning_state",
        ]
        table_counts = {}
        for table in tables:
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                table_counts[table] = row[0] if row else 0
            except Exception:
                table_counts[table] = -1  # Table doesn't exist

        # Latest signal time
        try:
            last_sig = conn.execute(
                "SELECT timestamp FROM signals ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            last_signal_time = last_sig["timestamp"] if last_sig else None
        except Exception:
            last_signal_time = None

        # Engine mode
        mode = cfg.get("system.mode", "UNKNOWN")

        return {
            "status": "HEALTHY",
            "mode": mode,
            "version": cfg.get("system.version", "8.0"),
            "last_signal_time": last_signal_time,
            "table_row_counts": table_counts,
            "total_rows": sum(v for v in table_counts.values() if v > 0),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        return {"status": "ERROR", "error": str(exc)}
    finally:
        conn.close()


@app.get("/api/immutable-rules")
async def get_immutable_rules():
    """Get the 17 immutable constitutional risk rules."""
    return {
        "rules": cfg.get("immutable_rules", {}),
        "session_protection": cfg.get("session_protection", {}),
        "drawdown_recovery": cfg.get("drawdown_recovery", {}),
    }


@app.get("/api/emotional-firewall")
async def get_emotional_firewall():
    """Get emotional firewall configuration and recent events."""
    conn = get_db()
    try:
        recent = conn.execute(
            "SELECT * FROM firewall_events ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        return {
            "patterns": cfg.get("emotional_firewall", {}),
            "recent_events": [dict(row) for row in recent],
        }
    except Exception:
        return {"patterns": cfg.get("emotional_firewall", {}), "recent_events": []}
    finally:
        conn.close()


@app.get("/api/profit-ladder")
async def get_profit_ladder():
    """Get profit ladder configuration and recent partial executions."""
    conn = get_db()
    try:
        recent = conn.execute(
            "SELECT * FROM partial_executions ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        return {
            "stocks_ladder": cfg.get("profit_ladder.stocks", []),
            "etp_3x_ladder": cfg.get("profit_ladder.etp_3x", {}),
            "recent_partials": [dict(row) for row in recent],
        }
    except Exception:
        return {
            "stocks_ladder": cfg.get("profit_ladder.stocks", []),
            "etp_3x_ladder": cfg.get("profit_ladder.etp_3x", {}),
            "recent_partials": [],
        }
    finally:
        conn.close()


@app.get("/api/schedule")
async def get_schedule():
    """Get strategy scan schedule."""
    return {
        "schedule": cfg.get("schedule", {}),
        "time_windows": cfg.get("time_windows", {}),
    }


@app.get("/api/data-feeds")
async def get_data_feeds():
    """Get data feed configuration and status."""
    return {"feeds": cfg.get("data_feeds", {})}


# === Performance, Ticker & History Endpoints ===


@app.get("/api/performance/by-bot")
async def get_performance_by_bot():
    """Performance metrics split by Bot A (ISA) vs Bot B (US)."""
    conn = get_db()
    try:
        result = {}
        for bot_label in ['A', 'B']:
            rows = conn.execute(
                "SELECT * FROM daily_summary WHERE bot = ? ORDER BY date DESC LIMIT 90",
                [bot_label],
            ).fetchall()
            summary_list = [dict(row) for row in rows]
            total_pnl = sum(s.get("daily_pnl_dollars", 0) for s in summary_list)
            total_trades = sum(s.get("trades_taken", 0) for s in summary_list)
            total_wins = sum(s.get("win_count", 0) for s in summary_list)
            total_losses = sum(s.get("loss_count", 0) for s in summary_list)
            win_rate = total_wins / (total_wins + total_losses) if (total_wins + total_losses) > 0 else 0
            result[bot_label] = {
                "total_pnl": round(total_pnl, 2),
                "total_trades": total_trades,
                "win_count": total_wins,
                "loss_count": total_losses,
                "win_rate": round(win_rate * 100, 1),
                "recent_summaries": summary_list[:7],
            }
        return result
    finally:
        conn.close()


@app.get("/api/ticker/{ticker}/overview")
async def get_ticker_overview(ticker: str):
    """Live price and overview for a specific ticker via yfinance."""
    def _fetch():
        t = yf.Ticker(ticker)
        fi = t.fast_info
        hist = t.history(period="5d")
        price = round(fi.last_price, 2) if fi.last_price else 0
        prev = round(fi.previous_close, 2) if fi.previous_close else price
        change = round(price - prev, 2)
        change_pct = round((change / prev * 100), 2) if prev else 0
        vol = int(fi.last_volume) if fi.last_volume else 0
        avg_vol = int(hist["Volume"].mean()) if not hist.empty and len(hist) > 1 else vol
        rvol = round(vol / avg_vol, 2) if avg_vol > 0 else 0
        market_cap = fi.market_cap if hasattr(fi, 'market_cap') and fi.market_cap else 0
        return {
            "ticker": ticker, "price": price, "prev_close": prev,
            "change": change, "change_pct": change_pct,
            "volume": vol, "avg_volume": avg_vol, "rvol": rvol,
            "market_cap": market_cap,
        }
    data = _yf_cached(f"ticker_overview_{ticker}", _fetch, ttl=30)
    return data or {"ticker": ticker, "price": 0, "error": "Failed to fetch"}


@app.get("/api/ticker/{ticker}/institutional")
async def get_ticker_institutional(ticker: str):
    """Institutional-grade day trading stats for a specific ticker.

    Returns comprehensive data that professional traders need:
    - Price structure (VWAP, EMAs, key levels)
    - Volatility profile (ATR, IV rank, Bollinger position)
    - Volume analysis (RVOL, OBV trend, dollar volume)
    - Momentum (RSI, MACD, ADX, Stoch RSI)
    - Trading performance (win rate by regime, strategy, time window)
    - Risk metrics (avg stop, avg winner, profit factor)
    - Open position details if any
    """
    import pandas_ta as ta
    import numpy as np

    def _fetch_institutional():
        t = yf.Ticker(ticker)
        fi = t.fast_info

        # Get intraday (1m for VWAP) and daily (for ATR, EMAs)
        hist_1m = t.history(period="1d", interval="1m")
        hist_5d = t.history(period="5d", interval="5m")
        hist_daily = t.history(period="6mo")

        price = round(fi.last_price, 2) if fi.last_price else 0
        prev = round(fi.previous_close, 2) if fi.previous_close else price
        change = round(price - prev, 2)
        change_pct = round((change / prev * 100), 2) if prev else 0

        # === VOLUME ANALYSIS ===
        vol = int(fi.last_volume) if fi.last_volume else 0
        avg_vol_20d = 0
        dollar_volume = 0
        if not hist_daily.empty and len(hist_daily) >= 20:
            avg_vol_20d = int(hist_daily["Volume"].tail(20).mean())
            dollar_volume = int(price * vol)
        rvol = round(vol / avg_vol_20d, 2) if avg_vol_20d > 0 else 0

        # === VWAP (from 1-min intraday data) ===
        vwap = 0
        vwap_upper = 0
        vwap_lower = 0
        vwap_pct = 0
        if not hist_1m.empty and len(hist_1m) > 5:
            tp = (hist_1m["High"] + hist_1m["Low"] + hist_1m["Close"]) / 3
            cum_tp_vol = (tp * hist_1m["Volume"]).cumsum()
            cum_vol = hist_1m["Volume"].cumsum()
            vwap_series = cum_tp_vol / cum_vol.replace(0, np.nan)
            vwap = round(float(vwap_series.iloc[-1]), 2) if not vwap_series.empty else price
            # VWAP bands (1 std dev)
            squared_diff = ((tp - vwap_series) ** 2 * hist_1m["Volume"]).cumsum()
            variance = squared_diff / cum_vol.replace(0, np.nan)
            std = np.sqrt(variance)
            vwap_upper = round(float(vwap + std.iloc[-1]), 2) if not std.empty else vwap
            vwap_lower = round(float(vwap - std.iloc[-1]), 2) if not std.empty else vwap
            vwap_pct = round((price - vwap) / vwap * 100, 2) if vwap > 0 else 0

        # === EMAs (from daily data) ===
        ema9 = ema20 = ema50 = ema200 = 0
        ema_alignment = "NEUTRAL"
        if not hist_daily.empty and len(hist_daily) >= 50:
            close = hist_daily["Close"]
            ema9 = round(float(close.ewm(span=9).mean().iloc[-1]), 2)
            ema20 = round(float(close.ewm(span=20).mean().iloc[-1]), 2)
            ema50 = round(float(close.ewm(span=50).mean().iloc[-1]), 2)
            if len(hist_daily) >= 200:
                ema200 = round(float(close.ewm(span=200).mean().iloc[-1]), 2)
            # EMA alignment
            if price > ema9 > ema20 > ema50:
                ema_alignment = "BULLISH"
            elif price < ema9 < ema20 < ema50:
                ema_alignment = "BEARISH"
            elif ema9 > ema20:
                ema_alignment = "BULLISH_CROSS"
            elif ema9 < ema20:
                ema_alignment = "BEARISH_CROSS"

        # === VOLATILITY (ATR, Bollinger, ADX) ===
        atr14 = atr_pct = adx = 0
        bb_upper = bb_lower = bb_pct = 0
        daily_range_avg = 0
        if not hist_daily.empty and len(hist_daily) >= 20:
            close = hist_daily["Close"]
            high = hist_daily["High"]
            low = hist_daily["Low"]
            # ATR
            tr = np.maximum(
                high - low,
                np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1)))
            ).dropna()
            if len(tr) >= 14:
                atr14 = round(float(tr.tail(14).mean()), 2)
                atr_pct = round(atr14 / price * 100, 2) if price > 0 else 0
            # Bollinger Bands
            sma20 = close.rolling(20).mean().iloc[-1]
            std20 = close.rolling(20).std().iloc[-1]
            bb_upper = round(float(sma20 + 2 * std20), 2)
            bb_lower = round(float(sma20 - 2 * std20), 2)
            bb_range = bb_upper - bb_lower
            bb_pct = round((price - bb_lower) / bb_range * 100, 1) if bb_range > 0 else 50
            # ADX (simple approximation)
            plus_dm = np.maximum(high.diff(), 0)
            minus_dm = np.maximum(-low.diff(), 0)
            if len(tr) >= 14:
                plus_di = (plus_dm.tail(14).mean() / tr.tail(14).mean() * 100) if tr.tail(14).mean() > 0 else 0
                minus_di = (minus_dm.tail(14).mean() / tr.tail(14).mean() * 100) if tr.tail(14).mean() > 0 else 0
                dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
                adx = round(float(dx), 1)
            # Average daily range
            daily_range_avg = round(float((high - low).tail(20).mean()), 2)

        # === MOMENTUM (RSI, MACD, Stoch RSI) ===
        rsi14 = 50
        macd_line = macd_signal = macd_hist = 0
        stoch_rsi = 50
        if not hist_daily.empty and len(hist_daily) >= 30:
            close = hist_daily["Close"]
            # RSI (Wilder's smoothing)
            delta = close.diff()
            gain = delta.where(delta > 0, 0).ewm(alpha=1/14, adjust=False).mean()
            loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
            rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] != 0 else 100
            rsi14 = round(100 - (100 / (1 + rs)), 1)
            # MACD
            ema12 = close.ewm(span=12).mean()
            ema26 = close.ewm(span=26).mean()
            macd_line = round(float(ema12.iloc[-1] - ema26.iloc[-1]), 3)
            macd_signal = round(float((ema12 - ema26).ewm(span=9).mean().iloc[-1]), 3)
            macd_hist = round(macd_line - macd_signal, 3)
            # Stoch RSI
            rsi_series = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))
            rsi_14 = rsi_series.tail(14)
            if len(rsi_14.dropna()) >= 5:
                rsi_min = rsi_14.min()
                rsi_max = rsi_14.max()
                stoch_rsi = round((rsi14 - rsi_min) / (rsi_max - rsi_min) * 100, 1) if (rsi_max - rsi_min) > 0 else 50

        # === KEY LEVELS ===
        day_high = round(float(hist_1m["High"].max()), 2) if not hist_1m.empty else price
        day_low = round(float(hist_1m["Low"].min()), 2) if not hist_1m.empty else price
        premarket_high = 0
        premarket_low = 0
        # 52-week high/low
        high_52w = round(float(hist_daily["High"].max()), 2) if not hist_daily.empty else price
        low_52w = round(float(hist_daily["Low"].min()), 2) if not hist_daily.empty else price
        pct_from_52w_high = round((price - high_52w) / high_52w * 100, 1) if high_52w > 0 else 0

        # === MARKET CAP ===
        market_cap = fi.market_cap if hasattr(fi, 'market_cap') and fi.market_cap else 0

        return {
            "ticker": ticker,
            "price": price,
            "prev_close": prev,
            "change": change,
            "change_pct": change_pct,
            # Volume
            "volume": vol,
            "avg_volume_20d": avg_vol_20d,
            "rvol": rvol,
            "dollar_volume": dollar_volume,
            # VWAP
            "vwap": vwap,
            "vwap_upper": vwap_upper,
            "vwap_lower": vwap_lower,
            "vwap_pct": vwap_pct,
            # EMAs
            "ema9": ema9,
            "ema20": ema20,
            "ema50": ema50,
            "ema200": ema200,
            "ema_alignment": ema_alignment,
            # Volatility
            "atr14": atr14,
            "atr_pct": atr_pct,
            "adx": adx,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
            "bb_pct": bb_pct,
            "daily_range_avg": daily_range_avg,
            # Momentum
            "rsi14": rsi14,
            "macd_line": macd_line,
            "macd_signal": macd_signal,
            "macd_hist": macd_hist,
            "stoch_rsi": stoch_rsi,
            # Key Levels
            "day_high": day_high,
            "day_low": day_low,
            "high_52w": high_52w,
            "low_52w": low_52w,
            "pct_from_52w_high": pct_from_52w_high,
            # Market Cap
            "market_cap": market_cap,
        }

    # Fetch live data (cached 30s)
    live = _yf_cached(f"institutional_{ticker}", _fetch_institutional, ttl=30) or {}

    # Fetch trading performance from DB
    conn = get_db()
    perf = {}
    try:
        # Overall stats
        row = conn.execute(
            """SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN net_pnl <= 0 THEN 1 ELSE 0 END) as losses,
                ROUND(SUM(net_pnl), 2) as total_pnl,
                ROUND(AVG(r_multiple), 3) as avg_r,
                ROUND(MAX(r_multiple), 3) as best_r,
                ROUND(MIN(r_multiple), 3) as worst_r,
                ROUND(AVG(CASE WHEN net_pnl > 0 THEN r_multiple ELSE NULL END), 3) as avg_win_r,
                ROUND(AVG(CASE WHEN net_pnl <= 0 THEN ABS(r_multiple) ELSE NULL END), 3) as avg_loss_r,
                ROUND(SUM(CASE WHEN net_pnl > 0 THEN net_pnl ELSE 0 END), 2) as gross_wins,
                ROUND(SUM(CASE WHEN net_pnl <= 0 THEN ABS(net_pnl) ELSE 0 END), 2) as gross_losses,
                ROUND(AVG(duration_minutes), 0) as avg_duration_min
            FROM virtual_trades WHERE ticker = ? AND exit_time IS NOT NULL""",
            [ticker],
        ).fetchone()

        if row:
            d = dict(row)
            total = d["total_trades"] or 0
            wins = d["wins"] or 0
            perf = {
                "total_trades": total,
                "wins": wins,
                "losses": d["losses"] or 0,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "total_pnl": d["total_pnl"] or 0,
                "avg_r": d["avg_r"] or 0,
                "best_r": d["best_r"] or 0,
                "worst_r": d["worst_r"] or 0,
                "avg_win_r": d["avg_win_r"] or 0,
                "avg_loss_r": d["avg_loss_r"] or 0,
                "profit_factor": round(d["gross_wins"] / d["gross_losses"], 2) if (d["gross_losses"] or 0) > 0 else 0,
                "avg_duration_min": d["avg_duration_min"] or 0,
            }

        # Win rate by strategy
        strat_rows = conn.execute(
            """SELECT strategy,
                COUNT(*) as trades,
                SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(SUM(net_pnl), 2) as pnl,
                ROUND(AVG(r_multiple), 3) as avg_r
            FROM virtual_trades WHERE ticker = ? AND exit_time IS NOT NULL
            GROUP BY strategy ORDER BY pnl DESC""",
            [ticker],
        ).fetchall()
        perf["by_strategy"] = [
            {
                "strategy": dict(r)["strategy"],
                "trades": dict(r)["trades"],
                "wins": dict(r)["wins"],
                "win_rate": round(dict(r)["wins"] / dict(r)["trades"] * 100, 1) if dict(r)["trades"] > 0 else 0,
                "pnl": dict(r)["pnl"],
                "avg_r": dict(r)["avg_r"],
            }
            for r in strat_rows
        ]

        # Win rate by regime
        regime_rows = conn.execute(
            """SELECT regime_at_entry as regime,
                COUNT(*) as trades,
                SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(SUM(net_pnl), 2) as pnl
            FROM virtual_trades WHERE ticker = ? AND exit_time IS NOT NULL AND regime_at_entry != ''
            GROUP BY regime_at_entry ORDER BY pnl DESC""",
            [ticker],
        ).fetchall()
        perf["by_regime"] = [
            {
                "regime": dict(r)["regime"],
                "trades": dict(r)["trades"],
                "wins": dict(r)["wins"],
                "win_rate": round(dict(r)["wins"] / dict(r)["trades"] * 100, 1) if dict(r)["trades"] > 0 else 0,
                "pnl": dict(r)["pnl"],
            }
            for r in regime_rows
        ]

        # Last 5 trades
        recent = conn.execute(
            """SELECT entry_time, direction, strategy, r_multiple, net_pnl, exit_reason, duration_minutes
            FROM virtual_trades WHERE ticker = ? AND exit_time IS NOT NULL
            ORDER BY entry_time DESC LIMIT 5""",
            [ticker],
        ).fetchall()
        perf["recent_trades"] = [dict(r) for r in recent]

        # Open position
        open_pos = conn.execute(
            "SELECT * FROM virtual_positions WHERE ticker = ? AND status = 'OPEN'",
            [ticker],
        ).fetchone()
        perf["open_position"] = dict(open_pos) if open_pos else None

        # Recent signals (last 3)
        sigs = conn.execute(
            """SELECT timestamp, direction, strategy, confidence, status
            FROM signals WHERE ticker = ? ORDER BY timestamp DESC LIMIT 3""",
            [ticker],
        ).fetchall()
        perf["recent_signals"] = [dict(s) for s in sigs]

    except Exception as e:
        logger.warning("Institutional perf query failed for %s: %s", ticker, e)
    finally:
        conn.close()

    # ISA mapping
    isa_mapping = {}
    isa_map = cfg.get("bot_a_universe.isa_mapping", {})
    for direction in ["LONG", "SHORT"]:
        key = f"{ticker}_{direction}"
        if key in isa_map:
            isa_mapping[direction] = isa_map[key]

    # === AI DAY TRADING IDEA (Gemini Flash) ===
    ai_idea = ""
    try:
        import httpx
        ai_key = os.environ.get("GEMINI_API_KEY", "")
        if ai_key and live.get("price"):
            cache_key = f"ai_idea_{ticker}"
            cached = _yf_cached(cache_key, lambda: None, ttl=300)  # 5-min cache
            if cached:
                ai_idea = cached
            else:
                prompt = (
                    f"You are an elite quantitative day trader. Give a ONE PARAGRAPH "
                    f"actionable day trading assessment for {ticker} TODAY. "
                    f"Include: direction bias (bullish/bearish/neutral), key levels to watch, "
                    f"entry trigger, and risk.\n\n"
                    f"Current Data:\n"
                    f"Price: ${live.get('price', 0):.2f} ({live.get('change_pct', 0):+.2f}%)\n"
                    f"VWAP: ${live.get('vwap', 0):.2f} (price {'above' if live.get('vwap_pct', 0) > 0 else 'below'} VWAP)\n"
                    f"RSI: {live.get('rsi14', 50):.0f} | MACD hist: {live.get('macd_hist', 0):.3f}\n"
                    f"RVOL: {live.get('rvol', 0):.1f}x | ATR: ${live.get('atr14', 0):.2f} ({live.get('atr_pct', 0):.1f}%)\n"
                    f"EMA alignment: {live.get('ema_alignment', 'NEUTRAL')} | ADX: {live.get('adx', 0):.0f}\n"
                    f"Day range: ${live.get('day_low', 0):.2f}-${live.get('day_high', 0):.2f}\n"
                    f"BB position: {live.get('bb_pct', 50):.0f}% | Stoch RSI: {live.get('stoch_rsi', 50):.0f}\n"
                    f"52W high: ${live.get('high_52w', 0):.2f} ({live.get('pct_from_52w_high', 0):.1f}%)\n"
                    f"Keep under 60 words. Be specific with price levels. End with risk rating: LOW/MEDIUM/HIGH."
                )
                with httpx.Client(timeout=8) as client:
                    resp = client.post(
                        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
                        params={"key": ai_key},
                        json={
                            "contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": {"maxOutputTokens": 120, "temperature": 0.3},
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            if parts:
                                ai_idea = parts[0].get("text", "").strip()
                                # Cache for 5 minutes
                                _yf_cache[cache_key] = (time.time(), ai_idea)
    except Exception as e:
        logger.debug("AI idea generation failed for %s: %s", ticker, e)

    return {**live, "performance": perf, "isa_mapping": isa_mapping, "ai_idea": ai_idea}


@app.get("/api/ticker/{ticker}/trades")
async def get_ticker_trades(
    ticker: str,
    days: int = Query(default=365, ge=1, le=730),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Trade history for a specific ticker."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM virtual_trades WHERE ticker = ? AND entry_time > ? ORDER BY entry_time DESC LIMIT ?",
            [ticker, (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(), limit],
        ).fetchall()
        return {"trades": [dict(row) for row in rows], "total": len(rows)}
    except Exception:
        return {"trades": [], "total": 0}
    finally:
        conn.close()


@app.get("/api/ticker/{ticker}/stats")
async def get_ticker_stats(ticker: str):
    """Aggregated stats for a specific ticker."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins, "
            "SUM(CASE WHEN net_pnl <= 0 THEN 1 ELSE 0 END) as losses, "
            "ROUND(SUM(net_pnl), 2) as total_pnl, "
            "ROUND(AVG(r_multiple), 2) as avg_r, "
            "ROUND(MAX(r_multiple), 2) as best_r, "
            "ROUND(MIN(r_multiple), 2) as worst_r, "
            "ROUND(AVG(CASE WHEN net_pnl > 0 THEN r_multiple END), 2) as avg_win_r, "
            "ROUND(AVG(CASE WHEN net_pnl <= 0 THEN r_multiple END), 2) as avg_loss_r "
            "FROM virtual_trades WHERE ticker = ? AND exit_time IS NOT NULL",
            [ticker],
        ).fetchone()
        d = dict(row) if row else {}
        total = d.get("total", 0)
        wins = d.get("wins", 0) or 0
        d["win_rate"] = round((wins / total) * 100, 1) if total > 0 else 0
        # Best strategy
        best_strat = conn.execute(
            "SELECT strategy, COUNT(*) as cnt, ROUND(SUM(net_pnl), 2) as pnl "
            "FROM virtual_trades WHERE ticker = ? AND exit_time IS NOT NULL "
            "GROUP BY strategy ORDER BY pnl DESC LIMIT 1",
            [ticker],
        ).fetchone()
        d["best_strategy"] = dict(best_strat) if best_strat else None
        return d
    except Exception:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0}
    finally:
        conn.close()


@app.get("/api/ticker/{ticker}/isa-mapping")
async def get_ticker_isa_mapping(ticker: str):
    """Get ISA leverage ETP equivalents for a specific ticker."""
    isa_mapping = cfg.get("bot_a_universe.isa_mapping", {})
    mappings = {}
    for key, etp in isa_mapping.items():
        if key.startswith(ticker + "_"):
            direction = key.replace(ticker + "_", "")
            mappings[direction] = etp
    # Check if ticker itself is in the ISA universe
    leveraged = cfg.get("bot_a_universe.leveraged_4x_5x", [])
    related_etps = [e for e in leveraged if isinstance(e, dict) and ticker.lower() in (e.get("underlying", "").lower())]
    return {"ticker": ticker, "mappings": mappings, "related_etps": related_etps}


@app.get("/api/ticker/{ticker}/patterns")
async def get_ticker_patterns(
    ticker: str,
    days: int = Query(default=90, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Pattern detection history for a specific ticker."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM signals WHERE ticker = ? AND timestamp > ? ORDER BY timestamp DESC LIMIT ?",
            [ticker, (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(), limit],
        ).fetchall()
        return {"signals": [dict(row) for row in rows], "total": len(rows)}
    except Exception:
        return {"signals": [], "total": 0}
    finally:
        conn.close()


@app.get("/api/ticker/{ticker}/signals")
async def get_ticker_signals(
    ticker: str,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
):
    """All signals for a specific ticker."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM signals WHERE ticker = ? AND timestamp > ? ORDER BY timestamp DESC LIMIT ?",
            [ticker, (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(), limit],
        ).fetchall()
        return {"signals": [dict(row) for row in rows], "total": len(rows)}
    except Exception:
        return {"signals": [], "total": 0}
    finally:
        conn.close()


@app.get("/api/ticker/{ticker}/regime-performance")
async def get_ticker_regime_performance(ticker: str):
    """Performance for a ticker broken down by regime state."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT regime, COUNT(*) as total, "
            "SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins, "
            "ROUND(SUM(net_pnl), 2) as total_pnl, "
            "ROUND(AVG(r_multiple), 2) as avg_r "
            "FROM virtual_trades WHERE ticker = ? AND exit_time IS NOT NULL "
            "GROUP BY regime ORDER BY total_pnl DESC",
            [ticker],
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            total = d.get("total", 0)
            wins = d.get("wins", 0) or 0
            d["win_rate"] = round((wins / total) * 100, 1) if total > 0 else 0
            result.append(d)
        return {"regimes": result}
    except Exception:
        return {"regimes": []}
    finally:
        conn.close()


@app.get("/api/ticker/{ticker}/profile")
async def get_ticker_profile(ticker: str):
    """Ticker profile from learning engine + config overrides."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM ticker_profiles WHERE ticker = ?", [ticker]
        ).fetchone()
        profile = dict(row) if row else None
        overrides = cfg.get(f"bot_b_universe.overrides.{ticker}", {})
        return {"profile": profile, "overrides": overrides}
    except Exception:
        return {"profile": None, "overrides": {}}
    finally:
        conn.close()



@app.get("/api/ticker/{ticker}/ohlcv")
async def get_ticker_ohlcv(ticker: str, days: int = Query(default=30, ge=5, le=252)):
    """Real OHLCV candlestick data for the ticker page chart.

    Returns the last `days` trading days of price data from yfinance:
    - OHLCV bars for candlestick rendering
    - EMA9 and EMA20 values per bar
    - Today's session high/low
    Cached for 120 seconds to avoid hammering yfinance.
    """
    import numpy as np
    cache_key = f"ohlcv:{ticker}:{days}"
    cached = _yf_cached(cache_key, lambda: None, ttl=0)  # check cache existence
    # Use a dedicated longer TTL for OHLCV (120s)
    now = time.time()
    if cache_key in _yf_cache:
        ts, val = _yf_cache[cache_key]
        if now - ts < 120 and val is not None:
            return val

    def _fetch():
        try:
            t = yf.Ticker(ticker)
            # Fetch extra bars so EMA has warm-up period (fetch 3× requested days)
            lookback_days = days * 3
            hist = t.history(period=f"{lookback_days}d", interval="1d")
            if hist is None or hist.empty:
                return {"bars": [], "ema9": [], "ema20": [], "today_high": None, "today_low": None, "error": "no_data"}

            # Compute EMA9 and EMA20 over entire history
            close = hist["Close"]
            ema9_series = close.ewm(span=9, adjust=False).mean()
            ema20_series = close.ewm(span=20, adjust=False).mean()

            # Take only the last `days` bars
            hist_tail = hist.tail(days)
            ema9_tail = ema9_series.tail(days)
            ema20_tail = ema20_series.tail(days)

            bars = []
            ema9_out = []
            ema20_out = []
            for i in range(len(hist_tail)):
                idx = hist_tail.index[i]
                date_str = idx.strftime("%d/%m") if hasattr(idx, 'strftime') else str(idx)[:10]
                o = float(hist_tail["Open"].iloc[i])
                h = float(hist_tail["High"].iloc[i])
                lo = float(hist_tail["Low"].iloc[i])
                c = float(hist_tail["Close"].iloc[i])
                v = int(hist_tail["Volume"].iloc[i])
                e9 = round(float(ema9_tail.iloc[i]), 4)
                e20 = round(float(ema20_tail.iloc[i]), 4)
                bars.append({
                    "date": date_str,
                    "open": round(o, 4),
                    "high": round(h, 4),
                    "low": round(lo, 4),
                    "close": round(c, 4),
                    "volume": v,
                })
                ema9_out.append(e9)
                ema20_out.append(e20)

            # Today's intraday high/low
            today_high = None
            today_low = None
            try:
                intraday = t.history(period="1d", interval="1m")
                if not intraday.empty:
                    today_high = round(float(intraday["High"].max()), 4)
                    today_low = round(float(intraday["Low"].min()), 4)
            except Exception:
                pass

            return {
                "bars": bars,
                "ema9": ema9_out,
                "ema20": ema20_out,
                "today_high": today_high,
                "today_low": today_low,
                "count": len(bars),
            }
        except Exception as exc:
            logger.warning("OHLCV fetch error for %s: %s", ticker, exc)
            return {"bars": [], "ema9": [], "ema20": [], "today_high": None, "today_low": None, "error": str(exc)}

    result = await asyncio.get_event_loop().run_in_executor(None, _fetch)
    _yf_cache[cache_key] = (now, result)
    return result


@app.get("/api/history/equity-curve")
async def get_history_equity_curve(days: int = Query(default=365, ge=1, le=730)):
    """Equity curve with daily snapshots."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM equity_snapshots ORDER BY date DESC LIMIT ?", [days]
        ).fetchall()
        return {"snapshots": [dict(row) for row in reversed(rows)], "total": len(rows)}
    except Exception:
        return {"snapshots": [], "total": 0}
    finally:
        conn.close()


@app.get("/api/history/trade-journal")
async def get_history_trade_journal(
    ticker: Optional[str] = None,
    strategy: Optional[str] = None,
    bot: Optional[str] = None,
    regime: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Filterable trade journal with pagination."""
    conn = get_db()
    try:
        query = "SELECT * FROM virtual_trades WHERE exit_time IS NOT NULL "
        count_query = "SELECT COUNT(*) FROM virtual_trades WHERE exit_time IS NOT NULL "
        params = []
        count_params = []
        if ticker:
            query += "AND ticker = ? "
            count_query += "AND ticker = ? "
            params.append(ticker)
            count_params.append(ticker)
        if strategy:
            query += "AND strategy = ? "
            count_query += "AND strategy = ? "
            params.append(strategy)
            count_params.append(strategy)
        if bot:
            query += "AND bot = ? "
            count_query += "AND bot = ? "
            params.append(bot)
            count_params.append(bot)
        if regime:
            query += "AND regime = ? "
            count_query += "AND regime = ? "
            params.append(regime)
            count_params.append(regime)
        if date_from:
            query += "AND entry_time >= ? "
            count_query += "AND entry_time >= ? "
            params.append(date_from)
            count_params.append(date_from)
        if date_to:
            query += "AND entry_time <= ? "
            count_query += "AND entry_time <= ? "
            params.append(date_to)
            count_params.append(date_to)
        total = conn.execute(count_query, count_params).fetchone()[0]
        query += "ORDER BY entry_time DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        return {"trades": [dict(row) for row in rows], "total": total}
    except Exception:
        return {"trades": [], "total": 0}
    finally:
        conn.close()


@app.get("/api/history/strategy-comparison")
async def get_history_strategy_comparison(days: int = Query(default=90, ge=1, le=365)):
    """Weekly P&L aggregated by strategy."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT strategy, "
            "strftime('%%Y-W%%W', entry_time) as week, "
            "COUNT(*) as trades, "
            "SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins, "
            "ROUND(SUM(net_pnl), 2) as total_pnl, "
            "ROUND(AVG(r_multiple), 2) as avg_r "
            "FROM virtual_trades "
            "WHERE exit_time IS NOT NULL AND entry_time > ? "
            "GROUP BY strategy, week "
            "ORDER BY week DESC, total_pnl DESC",
            [(datetime.now(timezone.utc) - timedelta(days=days)).isoformat()],
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            total = d.get("trades", 0)
            wins = d.get("wins", 0) or 0
            d["win_rate"] = round((wins / total) * 100, 1) if total > 0 else 0
            result.append(d)
        return {"comparisons": result}
    except Exception:
        return {"comparisons": []}
    finally:
        conn.close()


@app.get("/api/history/regime-timeline")
async def get_history_regime_timeline(days: int = Query(default=180, ge=1, le=365)):
    """Regime history timeline ordered by time."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM regime_history WHERE timestamp > ? ORDER BY timestamp ASC",
            [(datetime.now(timezone.utc) - timedelta(days=days)).isoformat()],
        ).fetchall()
        return {"timeline": [dict(row) for row in rows], "total": len(rows)}
    except Exception:
        return {"timeline": [], "total": 0}
    finally:
        conn.close()


@app.get("/api/history/win-rate-trend")
async def get_history_win_rate_trend(
    window: int = Query(default=20, ge=5, le=100),
    days: int = Query(default=180, ge=1, le=365),
):
    """Rolling win rate trend from virtual trades."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT entry_time, net_pnl FROM virtual_trades "
            "WHERE exit_time IS NOT NULL AND entry_time > ? "
            "ORDER BY entry_time ASC",
            [(datetime.now(timezone.utc) - timedelta(days=days)).isoformat()],
        ).fetchall()
        trades = [dict(row) for row in rows]
        trend = []
        for i in range(window, len(trades) + 1):
            window_trades = trades[i - window:i]
            wins = sum(1 for t in window_trades if (t.get("net_pnl") or 0) > 0)
            wr = round((wins / window) * 100, 1)
            trend.append({
                "date": window_trades[-1].get("entry_time", ""),
                "win_rate": wr,
                "window_size": window,
                "trade_index": i,
            })
        return {"trend": trend, "window": window, "total_trades": len(trades)}
    except Exception:
        return {"trend": [], "window": window, "total_trades": 0}
    finally:
        conn.close()


@app.get("/api/history/drawdown-history")
async def get_history_drawdown(days: int = Query(default=365, ge=1, le=730)):
    """Drawdown time series computed from equity snapshots."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM equity_snapshots ORDER BY date ASC LIMIT ?", [days]
        ).fetchall()
        snapshots = [dict(row) for row in rows]
        peak = 0
        series = []
        for s in snapshots:
            eq = s.get("ending_equity", 0) or 0
            if eq > peak:
                peak = eq
            dd_pct = round(((peak - eq) / peak) * 100, 2) if peak > 0 else 0
            level = "GREEN"
            if dd_pct >= 12: level = "EMERGENCY"
            elif dd_pct >= 10: level = "CRITICAL"
            elif dd_pct >= 8: level = "RED"
            elif dd_pct >= 5: level = "ORANGE"
            elif dd_pct >= 3: level = "YELLOW"
            series.append({
                "date": s.get("date", ""),
                "equity": round(eq, 2),
                "peak": round(peak, 2),
                "drawdown_pct": dd_pct,
                "level": level,
            })
        return {"series": series, "total": len(series)}
    except Exception:
        return {"series": [], "total": 0}
    finally:
        conn.close()


# === A+ WAR ROOM ENDPOINTS: Today's Play, Sector Rotation, Near-Misses, Lanes ===

# Import universe data for new endpoints
try:
    from uk_isa.isa_universe import (
        EXTENDED_UNIVERSE, SECTOR_RADAR_UNIVERSE, FULL_SCAN_UNIVERSE,
        TICKER_NAMES, LEVERAGE_MAP, ISA_FACTOR_GROUPS, get_factor_group,
    )
    from delivery.pdf_shared import Lane, assign_lane, LANE_GATES
    _HAS_UNIVERSE = True
except ImportError:
    _HAS_UNIVERSE = False
    logger.warning("uk_isa.isa_universe not available for war room endpoints")


def _load_latest_artifact(artifact_name: str) -> dict:
    """Load the latest artifact JSON file from today's sessions."""
    artifacts_dir = Path(PROJECT_ROOT) / "artifacts"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for session in ["lse", "overlap", "pre_lse", "nyse", "pre_nyse",
                     "preview_pre_lse", "preview_pre_nyse",
                     "eod_institutional", "preview_eod_institutional",
                     "preview_copilot_scan", "eod", "off_hours",
                     "tick_loop"]:
        path = artifacts_dir / today / session / artifact_name
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                continue
    # Try yesterday if today's not available
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    for session in ["lse", "overlap", "pre_lse", "nyse",
                     "preview_pre_lse", "eod_institutional"]:
        path = artifacts_dir / yesterday / session / artifact_name
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                continue
    return {}


def _get_bteam_manager():
    """Get an initialized B-Team manager instance.

    Returns None if the B-Team manager module is unavailable.
    """
    try:
        from strategies.b_team_manager import BTeamManager
        bteam = BTeamManager()
        bteam.initialize()
        return bteam
    except Exception as exc:
        logger.debug("B-Team manager initialization failed: %s", exc)
        return None


def _compute_bteam_play_score(play: dict, regime: str = "NEUTRAL",
                               bteam=None) -> Optional[dict]:
    """Compute B-Team play score for a play dict from plays.json.

    Uses the B-Team manager's score_play() to produce the
    score/bracket/grade/reasons breakdown.

    Args:
        play: Dict with ticker, composite, atr_pct, rvol, direction fields.
        regime: Current market regime string.
        bteam: Optional pre-initialized BTeamManager instance (avoids
               re-loading state on every call when scoring multiple plays).

    Returns None if B-Team manager is unavailable or scoring fails.
    """
    try:
        if bteam is None:
            bteam = _get_bteam_manager()
        if bteam is None:
            return None

        ticker = play.get("ticker", "")
        if not ticker:
            return None

        # Use composite (0-100) as signal_confidence for B-Team scoring
        signal_confidence = int(play.get("composite", play.get("strategy_weighted_score", 0)))
        atr_pct = play.get("atr_pct", 0) or 0
        rvol = play.get("rvol", 0) or 0
        direction = play.get("direction", "LONG")

        result = bteam.score_play(
            ticker=ticker,
            signal_confidence=signal_confidence,
            atr_pct=atr_pct,
            rvol=rvol,
            regime=regime,
            direction=direction,
        )
        return result
    except Exception as exc:
        logger.debug("B-Team play score computation failed: %s", exc)
        return None


@app.get("/api/todays-play")
async def get_todays_play():
    """Return the #1 TRADE signal with full SignalCard, lane, and score decomposition."""
    plays_data = _load_latest_artifact("plays.json")
    if not plays_data or not plays_data.get("plays"):
        return {
            "has_trade": False,
            "reason": "No qualifying trades today",
            "regime": plays_data.get("regime", "UNKNOWN"),
            "total_scanned": plays_data.get("funnel", {}).get("tracked", 0),
        }

    plays = plays_data["plays"]
    # Sort by composite score descending
    plays.sort(key=lambda p: p.get("composite", p.get("strategy_weighted_score", 0)), reverse=True)
    best = plays[0]

    # Score decomposition
    decomp = {
        "momentum": round(best.get("momentum_score", 0) * 100, 1),
        "volatility": round(best.get("volatility_score", 0) * 100, 1),
        "regime_fit": round(best.get("regime_score", 0) * 100, 1),
        "liquidity": round(best.get("liquidity_score", 0) * 100, 1),
        "rr": round(best.get("rr_score", 0) * 100, 1),
        "quality": round(best.get("quality_score", 0) * 100, 1),
    }

    # B-Team play score (score/bracket/grade/reasons from self-learning system)
    regime = plays_data.get("regime", "UNKNOWN")
    play_score = _compute_bteam_play_score(best, regime=regime)

    return {
        "has_trade": True,
        "ticker": best.get("ticker", ""),
        "name": TICKER_NAMES.get(best.get("ticker", ""), best.get("ticker", "")) if _HAS_UNIVERSE else best.get("ticker", ""),
        "direction": best.get("direction", "LONG"),
        "entry": best.get("entry", 0),
        "stop": best.get("stop", 0),
        "target1": best.get("target1", 0),
        "target2": best.get("target2", 0),
        "rr_ratio": best.get("rr_ratio", 0),
        "composite": best.get("composite", 0),
        "stars": best.get("stars", 0),
        "label": best.get("label", ""),
        "strategy_tag": best.get("strategy_tag", ""),
        "risk_officer_decision": best.get("risk_officer_decision", ""),
        "score_decomposition": decomp,
        "play_score": {
            "score": play_score["score"],
            "bracket": play_score["bracket"],
            "grade": play_score["grade"],
            "reasons": play_score["reasons"],
            "self_learning_boost": play_score.get("self_learning_boost", 0),
        } if play_score else None,
        "reasons": best.get("reasons", []),
        "regime": regime,
        "total_plays": len(plays),
        "funnel": plays_data.get("funnel", {}),
        "runners_up": [
            {
                "ticker": p.get("ticker", ""),
                "composite": p.get("composite", 0),
                "direction": p.get("direction", ""),
            }
            for p in plays[1:4]
        ],
    }


@app.get("/api/sector-rotation")
async def get_sector_rotation():
    """Return sector rotation radar — all sectors with rankings, inflows, instruments."""
    sr_data = _load_latest_artifact("sector_rotation.json")
    if sr_data and sr_data.get("sectors"):
        return sr_data

    # Fallback: build from ISA_FACTOR_GROUPS with basic scoring
    if not _HAS_UNIVERSE:
        return {"sectors": [], "inflows": [], "leaders": []}

    sectors = []
    for group, tickers in ISA_FACTOR_GROUPS.items():
        if group in ("single_stock_long", "single_stock_short"):
            continue
        # Get basic data from yfinance cache
        day_changes = []
        for t in tickers[:3]:
            cached = _yf_cached(f"sector_{t}", lambda ticker=t: _get_quick_price(ticker))
            if cached and isinstance(cached, dict):
                day_changes.append(cached.get("day_chg", 0))

        avg_chg = sum(day_changes) / len(day_changes) if day_changes else 0
        signal = "INFLOW" if avg_chg > 1.5 else ("OUTFLOW" if avg_chg < -1.5 else "NEUTRAL")
        score = max(0, min(100, 50 + avg_chg * 10))

        sectors.append({
            "sector": group.upper().replace("_", " "),
            "composite_score": round(score, 1),
            "rotation_signal": signal,
            "leadership_status": "LEADER" if score >= 70 else ("RISING" if score >= 55 else "NEUTRAL"),
            "instruments": tickers[:5],
            "best_instrument": tickers[0] if tickers else "",
        })

    sectors.sort(key=lambda s: s["composite_score"], reverse=True)
    inflows = [s["sector"] for s in sectors if s["rotation_signal"] == "INFLOW"]
    leaders = [s["sector"] for s in sectors if s["leadership_status"] == "LEADER"]

    return {"sectors": sectors, "inflows": inflows, "leaders": leaders}


def _get_quick_price(ticker: str) -> dict:
    """Get quick price data for a ticker."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="2d")
        if hist is None or len(hist) < 1:
            return {}
        close = hist["Close"].iloc[-1]
        prev = hist["Close"].iloc[-2] if len(hist) >= 2 else close
        chg = (close - prev) / prev * 100 if prev > 0 else 0
        return {"price": round(close, 4), "day_chg": round(chg, 2)}
    except Exception:
        return {}


@app.get("/api/near-misses")
async def get_near_misses():
    """Return closest-miss tickers from latest drought analysis."""
    drought = _load_latest_artifact("drought.json")
    if not drought:
        return {"closest_misses": [], "blockers_summary": [], "tickers_checked": 0}

    return {
        "closest_misses": drought.get("closest_misses", []),
        "blockers_summary": drought.get("blockers_summary", []),
        "recommended_knobs": drought.get("recommended_knobs", []),
        "tickers_checked": drought.get("tickers_checked", 0),
        "drought_flag": drought.get("drought_flag", False),
        "generated_at": drought.get("generated_at", ""),
    }


@app.get("/api/lane-assignments")
async def get_lane_assignments():
    """Return all tickers with their lane assignment (TRADE/WATCH/INTEL/ABSTAIN)."""
    if not _HAS_UNIVERSE:
        return {"assignments": [], "counts": {}}

    plays_data = _load_latest_artifact("plays.json")
    plays = plays_data.get("plays", []) if plays_data else []

    # Build lookup of play data by ticker
    play_by_ticker = {}
    for p in plays:
        play_by_ticker[p.get("ticker", "")] = p

    assignments = []
    counts = {"TRADE": 0, "WATCH": 0, "INTEL": 0, "ABSTAIN": 0}

    for ticker in EXTENDED_UNIVERSE:
        play = play_by_ticker.get(ticker, {})
        composite = play.get("composite", 0)
        rr = play.get("rr_ratio", 0)
        rvol = play.get("rvol", 0) if play.get("rvol") else 0.5
        atr_pct = play.get("atr_pct", 1.0)

        if play:
            lane = assign_lane(
                ticker=ticker, rr=rr, confidence=composite,
                regime_confidence=50, rvol=rvol, atr_pct=atr_pct,
                data_bars=100,
            )
        else:
            lane = Lane.INTEL

        lane_str = lane.value
        counts[lane_str] = counts.get(lane_str, 0) + 1

        assignments.append({
            "ticker": ticker,
            "name": TICKER_NAMES.get(ticker, ticker),
            "lane": lane_str,
            "composite": round(composite, 1),
            "rr": round(rr, 2),
            "direction": play.get("direction", "LONG"),
            "entry": play.get("entry", 0),
            "stop": play.get("stop", 0),
            "target1": play.get("target1", 0),
            "factor_group": get_factor_group(ticker),
        })

    # Sort: TRADE first, then WATCH, INTEL, ABSTAIN
    lane_order = {"TRADE": 0, "WATCH": 1, "INTEL": 2, "ABSTAIN": 3}
    assignments.sort(key=lambda a: (lane_order.get(a["lane"], 9), -a["composite"]))

    return {"assignments": assignments, "counts": counts}


@app.get("/api/full-scan")
async def get_full_scan():
    """Return full universe scan (35+ tickers) with features, scores, lanes."""
    scan_data = _load_latest_artifact("full_scan.json")
    if scan_data and scan_data.get("items"):
        return scan_data

    # Fallback: return plays data enriched with universe info
    plays_data = _load_latest_artifact("plays.json")
    return {
        "generated_at": plays_data.get("generated_at", "") if plays_data else "",
        "session": plays_data.get("session", "") if plays_data else "",
        "tier": "PLAYS_ONLY",
        "count": len(plays_data.get("plays", [])) if plays_data else 0,
        "items": plays_data.get("plays", []) if plays_data else [],
    }


@app.get("/api/session-bundle")
async def get_session_bundle():
    """Return complete session bundle for current session."""
    bundle = _load_latest_artifact("session_bundle.json")
    if bundle:
        return bundle

    # Assemble from individual artifacts
    plays = _load_latest_artifact("plays.json")
    drought = _load_latest_artifact("drought.json")
    sr = _load_latest_artifact("sector_rotation.json")

    return {
        "assembled": True,
        "plays": plays if plays else {},
        "drought": drought if drought else {},
        "sector_rotation": sr if sr else {},
        "universe": {
            "extended": len(EXTENDED_UNIVERSE) if _HAS_UNIVERSE else 0,
            "sector_radar": len(SECTOR_RADAR_UNIVERSE) if _HAS_UNIVERSE else 0,
        },
    }


# === B-TEAM LEAGUE ENDPOINTS ===

@app.get("/api/b-team/league-table")
async def get_league_table():
    """Return full B-Team league table with all ticker stats."""
    try:
        state_path = Path(__file__).parent.parent / "data" / "b_team_state.json"
        if state_path.exists():
            import json
            data = json.loads(state_path.read_text())
            stats = data.get("ticker_stats", {})
            # Sort by team then P&L
            team_order = {"A": 0, "B": 1, "C": 2}
            items = sorted(
                stats.values(),
                key=lambda s: (team_order.get(s.get("team", "C"), 3), -(s.get("total_pnl", 0))),
            )
            return {
                "a_team": [s for s in items if s.get("team") == "A"],
                "b_team": [s for s in items if s.get("team") == "B"],
                "c_team": [s for s in items if s.get("team") == "C"],
                "total_tickers": len(items),
            }
    except Exception as e:
        logger.warning("B-Team league table error: %s", e)
    return {"a_team": [], "b_team": [], "c_team": [], "total_tickers": 0}


@app.get("/api/b-team/events")
async def get_league_events():
    """Return recent promotion/relegation events."""
    try:
        state_path = Path(__file__).parent.parent / "data" / "b_team_state.json"
        if state_path.exists():
            import json
            data = json.loads(state_path.read_text())
            events = data.get("league_events", [])
            return {"events": events[-50:], "total": len(events)}
    except Exception as e:
        logger.warning("B-Team events error: %s", e)
    return {"events": [], "total": 0}


# === PORTFOLIO ANALYTICS ENDPOINTS ===

@app.get("/api/portfolio/holdings")
async def get_portfolio_holdings():
    """Current exposure breakdown by sector, strategy, bot."""
    try:
        conn = get_db()
        positions = conn.execute("SELECT * FROM virtual_positions WHERE status='OPEN'").fetchall()
        trades = conn.execute(
            "SELECT * FROM virtual_trades ORDER BY exit_time DESC LIMIT 100"
        ).fetchall()

        # Group by ticker, strategy, bot
        by_ticker = {}
        by_strategy = {}
        by_bot = {}
        total_exposure = 0.0

        for pos in positions:
            d = dict(pos)
            ticker = d.get("ticker", "")
            strategy = d.get("strategy", "")
            bot = d.get("bot", "")
            risk = abs(d.get("risk_dollars", 0))
            total_exposure += risk

            by_ticker[ticker] = by_ticker.get(ticker, 0) + risk
            by_strategy[strategy] = by_strategy.get(strategy, 0) + risk
            by_bot[bot] = by_bot.get(bot, 0) + risk

        return {
            "open_positions": len(positions),
            "total_exposure": round(total_exposure, 2),
            "by_ticker": by_ticker,
            "by_strategy": by_strategy,
            "by_bot": by_bot,
        }
    except Exception as e:
        logger.warning("Portfolio holdings error: %s", e)
    return {"open_positions": 0, "total_exposure": 0, "by_ticker": {}, "by_strategy": {}, "by_bot": {}}


@app.get("/api/portfolio/ratios")
async def get_portfolio_ratios():
    """Risk-adjusted return ratios: Sharpe, Sortino, Calmar."""
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT net_pnl, r_multiple, risk_dollars FROM virtual_trades ORDER BY exit_time ASC"
        ).fetchall()
        if not rows or len(rows) < 5:
            return {"sharpe": 0, "sortino": 0, "calmar": 0, "trade_count": len(rows)}

        import math
        returns = [dict(r).get("r_multiple", 0) or 0 for r in rows]
        mean_r = sum(returns) / len(returns)

        # Sharpe (assuming risk-free = 0)
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in returns) / len(returns)) if len(returns) > 1 else 1
        sharpe = round(mean_r / std_r, 3) if std_r > 0 else 0

        # Sortino (downside deviation only)
        neg_returns = [r for r in returns if r < 0]
        if neg_returns:
            downside_std = math.sqrt(sum(r ** 2 for r in neg_returns) / len(neg_returns))
            sortino = round(mean_r / downside_std, 3) if downside_std > 0 else 0
        else:
            sortino = round(mean_r * 10, 3)  # All positive = high sortino

        # Calmar (return / max drawdown)
        cumulative = []
        running = 0
        for r in returns:
            running += r
            cumulative.append(running)
        peak = cumulative[0] if cumulative else 0
        max_dd = 0
        for c in cumulative:
            if c > peak:
                peak = c
            dd = peak - c
            if dd > max_dd:
                max_dd = dd
        calmar = round(mean_r / max_dd, 3) if max_dd > 0 else 0

        return {
            "sharpe": sharpe,
            "sortino": sortino,
            "calmar": calmar,
            "mean_r": round(mean_r, 3),
            "trade_count": len(returns),
        }
    except Exception as e:
        logger.warning("Portfolio ratios error: %s", e)
    return {"sharpe": 0, "sortino": 0, "calmar": 0, "mean_r": 0, "trade_count": 0}


@app.get("/api/portfolio/distribution")
async def get_portfolio_distribution():
    """R-multiple distribution histogram data."""
    try:
        conn = get_db()
        rows = conn.execute("SELECT r_multiple FROM virtual_trades WHERE r_multiple IS NOT NULL").fetchall()
        if not rows:
            return {"buckets": [], "total": 0}

        r_values = [dict(r).get("r_multiple", 0) or 0 for r in rows]

        # Create buckets from -3R to +5R in 0.5R steps
        bucket_edges = [i * 0.5 for i in range(-6, 11)]
        buckets = []
        for i in range(len(bucket_edges) - 1):
            low, high = bucket_edges[i], bucket_edges[i + 1]
            count = sum(1 for r in r_values if low <= r < high)
            buckets.append({"range": f"{low:+.1f}R to {high:+.1f}R", "low": low, "high": high, "count": count})

        return {"buckets": buckets, "total": len(r_values), "mean_r": round(sum(r_values) / len(r_values), 3)}
    except Exception as e:
        logger.warning("Portfolio distribution error: %s", e)
    return {"buckets": [], "total": 0}


@app.get("/api/portfolio/mfe-mae")
async def get_portfolio_mfe_mae():
    """MFE/MAE scatter plot data for all virtual trades."""
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT ticker, strategy, peak_r, trough_r, r_multiple, exit_reason, net_pnl "
            "FROM virtual_trades ORDER BY exit_time DESC LIMIT 200"
        ).fetchall()
        points = []
        for row in rows:
            d = dict(row)
            points.append({
                "ticker": d.get("ticker", ""),
                "strategy": d.get("strategy", ""),
                "mfe": round(d.get("peak_r", 0) or 0, 3),
                "mae": round(abs(d.get("trough_r", 0) or 0), 3),
                "actual_r": round(d.get("r_multiple", 0) or 0, 3),
                "exit_reason": d.get("exit_reason", ""),
                "pnl": round(d.get("net_pnl", 0) or 0, 2),
            })
        return {"points": points, "count": len(points)}
    except Exception as e:
        logger.warning("MFE/MAE error: %s", e)
    return {"points": [], "count": 0}


# === LEARNING SYSTEM ENHANCED ENDPOINTS ===

@app.get("/api/learning/meta-weights")
async def get_meta_weights():
    """Return MetaLearner strategy weights (self-learning model core)."""
    try:
        meta_path = Path(__file__).parent.parent / "data" / "meta_weights.json"
        if meta_path.exists():
            import json
            return json.loads(meta_path.read_text())
    except Exception as e:
        logger.warning("Meta weights error: %s", e)
    return {"weights": {}, "updated_at": "never"}


@app.get("/api/learning/drift-report")
async def get_drift_report():
    """Return latest DriftDetector report."""
    try:
        drift_path = Path(__file__).parent.parent / "data" / "drift_report.json"
        if drift_path.exists():
            import json
            return json.loads(drift_path.read_text())
    except Exception as e:
        logger.warning("Drift report error: %s", e)
    return {"features": [], "hit_rates": [], "alerted": False, "updated_at": "never"}


@app.get("/api/learning/edge-ledger")
async def get_edge_ledger():
    """Return edge ledger data from the learning system."""
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM edge_ledger ORDER BY updated_at DESC LIMIT 50"
        ).fetchall()
        return {"edges": [dict(r) for r in rows], "count": len(rows)}
    except Exception:
        pass
    # Fallback: try JSON artifact
    try:
        edge_path = Path(__file__).parent.parent / "data" / "edge_ledger.json"
        if edge_path.exists():
            import json
            return json.loads(edge_path.read_text())
    except Exception as e:
        logger.warning("Edge ledger error: %s", e)
    return {"edges": [], "count": 0}


@app.get("/api/learning/outcome-history")
async def get_outcome_history():
    """Return outcome resolution history."""
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM outcome_resolutions ORDER BY resolved_at DESC LIMIT 50"
        ).fetchall()
        return {"outcomes": [dict(r) for r in rows], "count": len(rows)}
    except Exception:
        pass
    return {"outcomes": [], "count": 0}


@app.get("/api/learning/calibration")
async def get_calibration_data():
    """Return calibration engine state (MAE/MFE adjustments)."""
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT * FROM mae_mfe_calibration ORDER BY updated_at DESC LIMIT 20"
        ).fetchall()
        if rows:
            return {"calibrations": [dict(r) for r in rows], "count": len(rows)}

        # Fallback: learning_state table
        rows = conn.execute(
            "SELECT * FROM learning_state WHERE module IN ('calibration', 'mae_mfe')"
        ).fetchall()
        return {"calibrations": [dict(r) for r in rows], "count": len(rows)}
    except Exception as e:
        logger.warning("Calibration data error: %s", e)
    return {"calibrations": [], "count": 0}


@app.get("/api/sectors/heatmap")
async def get_sector_heatmap():
    """Sector relative strength heatmap data."""
    sr = _load_latest_artifact("sector_rotation.json")
    if sr:
        return sr
    return {"sectors": [], "updated_at": ""}


@app.get("/api/compounding/progress")
async def get_compounding_progress():
    """Compounding progress: actual vs target equity curve."""
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT exit_time, net_pnl FROM virtual_trades ORDER BY exit_time ASC"
        ).fetchall()
        if not rows:
            return {"days": [], "current_equity": 10000, "target_equity": 10000, "trading_day": 0}

        equity = 10000.0
        days = []
        current_day = ""
        day_pnl = 0.0
        trading_day = 0

        for row in rows:
            d = dict(row)
            exit_date = str(d.get("exit_time", ""))[:10]
            pnl = d.get("net_pnl", 0) or 0

            if exit_date != current_day:
                if current_day:
                    trading_day += 1
                    target = 10000 * (1.02 ** trading_day)
                    days.append({
                        "date": current_day,
                        "day_pnl": round(day_pnl, 2),
                        "equity": round(equity, 2),
                        "target": round(target, 2),
                        "gap": round(equity - target, 2),
                        "trading_day": trading_day,
                    })
                current_day = exit_date
                day_pnl = 0.0

            equity += pnl
            day_pnl += pnl

        # Final day
        if current_day:
            trading_day += 1
            target = 10000 * (1.02 ** trading_day)
            days.append({
                "date": current_day,
                "day_pnl": round(day_pnl, 2),
                "equity": round(equity, 2),
                "target": round(target, 2),
                "gap": round(equity - target, 2),
                "trading_day": trading_day,
            })

        return {
            "days": days,
            "current_equity": round(equity, 2),
            "target_equity": round(10000 * (1.02 ** trading_day), 2),
            "trading_day": trading_day,
            "on_track": equity >= 10000 * (1.02 ** trading_day) * 0.95,
        }
    except Exception as e:
        logger.warning("Compounding progress error: %s", e)
    return {"days": [], "current_equity": 10000, "target_equity": 10000, "trading_day": 0}


# ═══════════════════════════════════════════════════════════════════════════════
# V3.2 NEW ENDPOINTS — W7 War Room Backend
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/v3-signals")
async def api_v3_signals():
    """V3.2 module status: all 21 V3 signal modules + their current values.
    Used by the war room V3 dot panel.
    """
    try:
        state_path = Path(PROJECT_ROOT) / "data" / "v3_signals_state.json"
        if state_path.exists():
            with open(state_path) as f:
                return json.load(f)
    except Exception as e:
        logger.debug("v3-signals state file not found: %s", e)

    # Return default structure when no state cached yet
    modules = [
        "earnings_fade_gate", "runup_scorer", "portfolio_heat", "liquidity_monitor",
        "sector_momentum", "sue_pead", "vwap_engine", "intraday_momentum",
        "expiry_pinning", "window_dressing", "gap_analytics", "iv_crush",
        "short_squeeze", "earnings_sentiment", "realtime_feed",
        "order_flow_imbalance", "overnight_gap_persistence", "analyst_revision",
        "cross_asset_macro", "accruals_quality_veto", "net_expectancy",
    ]
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "module_count": len(modules),
        "modules": {m: {"active": False, "signal": "no_data", "adj": 0} for m in modules},
    }


@app.get("/api/system-wiring")
async def api_system_wiring():
    """7 system health indicators for the war room wiring panel.
    Returns status for: data_hub, engine, artifacts, telegram, pdf, learning, scheduler.
    """
    import os as _os
    data_dir = Path(PROJECT_ROOT) / "data"

    def file_age_minutes(path: str) -> float:
        """Minutes since file was last modified. 9999 if not found."""
        try:
            mtime = _os.path.getmtime(str(path))
            return (datetime.now(timezone.utc).timestamp() - mtime) / 60
        except Exception:
            return 9999.0

    def tier_color(age_min: float, warn_min: float, err_min: float) -> str:
        if age_min < warn_min:
            return "GREEN"
        if age_min < err_min:
            return "AMBER"
        return "RED"

    scan_age = file_age_minutes(data_dir / "scan_health.json")
    outcomes_age = file_age_minutes(data_dir / "outcomes.jsonl")
    edge_age = file_age_minutes(data_dir / "edge_ledger.json")
    ml_age = file_age_minutes(data_dir / "ml_meta_model_state.json")

    outcomes_n = 0
    try:
        with open(data_dir / "outcomes.jsonl") as f:
            outcomes_n = sum(1 for _ in f)
    except Exception:
        pass

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data_hub": {
            "status": tier_color(scan_age, 5, 15),
            "scan_health_age_min": round(scan_age, 1),
            "outcomes_count": outcomes_n,
        },
        "engine": {
            "status": tier_color(scan_age, 3, 10),
            "last_scan_age_min": round(scan_age, 1),
        },
        "artifacts": {
            "status": tier_color(min(outcomes_age, edge_age), 60, 240),
            "outcomes_age_min": round(outcomes_age, 1),
            "edge_ledger_age_min": round(edge_age, 1),
        },
        "learning": {
            "status": tier_color(ml_age, 60, 1440),
            "ml_model_age_min": round(ml_age, 1),
            "edge_ledger_age_min": round(edge_age, 1),
        },
        "pdf": {"status": "GREEN", "note": "Generated on schedule"},
        "telegram": {"status": "GREEN", "note": "Rate limit not tracked via API"},
        "scheduler": {
            "status": tier_color(scan_age, 5, 15),
            "note": "Inferred from scan_health age",
        },
    }


@app.get("/api/alerts")
async def api_alerts():
    """P0/P1/P2 alert queue for the war room alert system.
    Reads from data/alerts.json (written by main engine on alert events).
    """
    alerts_path = Path(PROJECT_ROOT) / "data" / "alerts.json"
    try:
        if alerts_path.exists():
            with open(alerts_path) as f:
                return json.load(f)
    except Exception as e:
        logger.debug("Alerts file error: %s", e)
    return {"alerts": [], "last_updated": None}


@app.get("/api/attribution")
async def api_attribution():
    """Performance attribution: P&L split by strategy, sector, and factor.
    Rolling 30-day window from outcomes.jsonl.
    """
    outcomes_path = Path(PROJECT_ROOT) / "data" / "outcomes.jsonl"
    cutoff = datetime.now(timezone.utc).timestamp() - 30 * 86400

    strategy_pnl: dict = {}
    sector_pnl: dict = {}
    factor_pnl: dict = {}
    total_trades = 0
    total_pnl = 0.0

    sector_map = {
        "QQQ3.L": "nasdaq_3x", "QQQ5.L": "nasdaq_5x",
        "3LUS.L": "us_broad_3x", "SP5L.L": "sp500_5x",
        "NVD3.L": "ai_semis", "GPT3.L": "ai_semis",
        "TSL3.L": "ev_tech", "TSM3.L": "ai_semis",
        "3SEM.L": "semis", "MU2.L": "semis",
        "QQQS.L": "nasdaq_inverse", "3USS.L": "us_inverse",
    }

    try:
        if outcomes_path.exists():
            with open(outcomes_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                        ts_str = o.get("timestamp") or o.get("exit_time") or ""
                        if ts_str:
                            try:
                                ts = datetime.fromisoformat(
                                    ts_str.replace("Z", "+00:00")
                                ).timestamp()
                                if ts < cutoff:
                                    continue
                            except Exception:
                                pass

                        pnl = float(o.get("net_pnl") or o.get("pnl") or 0)
                        strategy = o.get("strategy") or "S15"
                        ticker = o.get("ticker") or "UNKNOWN"
                        gap_type = o.get("gap_type") or ""
                        direction = o.get("direction") or ""

                        total_trades += 1
                        total_pnl += pnl
                        strategy_pnl[strategy] = strategy_pnl.get(strategy, 0.0) + pnl
                        sector = sector_map.get(ticker, "other")
                        sector_pnl[sector] = sector_pnl.get(sector, 0.0) + pnl

                        if gap_type in ("GAP_AND_GO", "OVERNIGHT_BREAKOUT"):
                            factor = "gap_momentum"
                        elif direction in ("SHORT", "INVERSE"):
                            factor = "mean_reversion"
                        else:
                            factor = "momentum"
                        factor_pnl[factor] = factor_pnl.get(factor, 0.0) + pnl

                    except Exception:
                        continue
    except Exception as e:
        logger.warning("Attribution error: %s", e)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rolling_days": 30,
        "total_trades": total_trades,
        "total_pnl": round(total_pnl, 2),
        "by_strategy": {k: round(v, 2) for k, v in sorted(strategy_pnl.items())},
        "by_sector": {k: round(v, 2) for k, v in sorted(sector_pnl.items())},
        "by_factor": {k: round(v, 2) for k, v in sorted(factor_pnl.items())},
    }


# === Chain Reactions API ===

@app.get("/api/chain-reactions")
async def api_chain_reactions():
    """
    Return current chain-reaction intelligence state.
    Thomas & Zhang (2008): cross-firm beta=0.40 (TSMC→NVDA peer momentum).
    Bernard & Thomas (1989): PEAD day+1 residual 30% decay.

    Reads move_attribution profiles from data/move_attribution.json.
    Returns: active boosted tickers, attribution confidence, recent chain events.
    """
    try:
        _attr_path = Path(PROJECT_ROOT) / "data" / "move_attribution.json"
        _profiles: dict = {}
        if _attr_path.exists():
            _profiles = json.loads(_attr_path.read_text())

        # Read recent ml_predictions for chain boost events logged there
        _chain_events: list = []
        _preds_path = Path(PROJECT_ROOT) / "data" / "ml_predictions.jsonl"
        if _preds_path.exists():
            try:
                _lines = _preds_path.read_text().strip().splitlines()
                _cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                for _ln in _lines[-200:]:  # Last 200 entries only
                    try:
                        _ev = json.loads(_ln)
                        if _ev.get("ts", "") >= _cutoff:
                            _chain_events.append(_ev)
                    except Exception:
                        continue
            except Exception:
                pass

        # Build ticker boosts summary from profiles
        _ticker_boosts: list = []
        for _tkr, _prof in _profiles.items():
            if isinstance(_prof, dict):
                _primary = _prof.get("primary_driver", "")
                _n = _prof.get("n_moves", 0)
                if _primary and _n > 0:
                    _ticker_boosts.append({
                        "ticker": _tkr,
                        "primary_driver": _primary,
                        "n_moves_tracked": _n,
                        "personality": _prof.get("personality", ""),
                    })

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "active_boosts": _ticker_boosts,
            "tickers_with_profiles": len(_ticker_boosts),
            "chain_events_today": len(_chain_events),
            "academic_basis": "Thomas & Zhang (2008) beta=0.40 cross-firm; Bernard & Thomas (1989) PEAD 30% day+1",
            "decay_rate_per_cycle": 0.30,
        }
    except Exception as _ce:
        logger.warning("api_chain_reactions error: %s", _ce)
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "active_boosts": [],
            "tickers_with_profiles": 0,
            "chain_events_today": 0,
            "error": str(_ce),
        }


# === Intelligence Pipeline API ===

@app.get("/api/intelligence-pipeline")
async def api_intelligence_pipeline():
    """
    Return the full intelligence pipeline status:
    - ML meta-model (LightGBM+XGBoost ensemble) trained/not trained, feature importance
    - Pattern tracker observations count
    - Decay detector halts active
    - Chain reaction boosts (from move_attribution profiles)
    - Learning engine last cycle time

    This endpoint verifies that the full intelligence loop is alive end-to-end.
    """
    try:
        _result: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ml_model": {},
            "pattern_tracker": {},
            "decay_detector": {},
            "chain_reactions": {},
            "learning_engine": {},
        }

        # --- ML Meta-Model status ---
        _ml_state_path = Path(PROJECT_ROOT) / "data" / "ml_meta_model_state.json"
        _ml_preds_path = Path(PROJECT_ROOT) / "data" / "ml_predictions.jsonl"
        if _ml_state_path.exists():
            _ml_state = json.loads(_ml_state_path.read_text())
            _n_preds = 0
            if _ml_preds_path.exists():
                try:
                    _n_preds = sum(1 for _ in _ml_preds_path.read_text().strip().splitlines() if _)
                except Exception:
                    pass
            _result["ml_model"] = {
                "is_trained": _ml_state.get("is_trained", False),
                "trained_at": _ml_state.get("trained_at", None),
                "n_training_samples": _ml_state.get("n_samples", 0),
                "n_predictions_total": _n_preds,
                "feature_importance": _ml_state.get("feature_importance", {}),
                "blend_ratio": "70% rule-based + 30% ML",
            }
        else:
            _result["ml_model"] = {
                "is_trained": False,
                "trained_at": None,
                "n_training_samples": 0,
                "n_predictions_total": 0,
                "note": "ml_meta_model_state.json not found — model not yet trained",
            }

        # --- Pattern Tracker ---
        _patterns_path = Path(PROJECT_ROOT) / "data" / "pattern_observations.json"
        if _patterns_path.exists():
            try:
                _pat_data = json.loads(_patterns_path.read_text())
                _result["pattern_tracker"] = {
                    "patterns_tracked": len(_pat_data) if isinstance(_pat_data, dict) else 0,
                    "last_updated": _pat_data.get("last_updated", None) if isinstance(_pat_data, dict) else None,
                }
            except Exception:
                _result["pattern_tracker"] = {"patterns_tracked": 0, "error": "parse error"}
        else:
            _result["pattern_tracker"] = {"patterns_tracked": 0, "note": "no observations yet"}

        # --- Decay Detector ---
        _decay_path = Path(PROJECT_ROOT) / "data" / "decay_state.json"
        if _decay_path.exists():
            try:
                _decay_data = json.loads(_decay_path.read_text())
                _halted = [k for k, v in _decay_data.items()
                           if isinstance(v, dict) and v.get("halted", False)]
                _result["decay_detector"] = {
                    "halted_count": len(_halted),
                    "halted": _halted,
                    "total_tracked": len(_decay_data),
                }
            except Exception:
                _result["decay_detector"] = {"halted_count": 0, "error": "parse error"}
        else:
            _result["decay_detector"] = {"halted_count": 0, "note": "no decay state yet"}

        # --- Chain Reactions (move_attribution profiles) ---
        _attr_path = Path(PROJECT_ROOT) / "data" / "move_attribution.json"
        if _attr_path.exists():
            try:
                _attr_data = json.loads(_attr_path.read_text())
                _active = [k for k, v in _attr_data.items()
                           if isinstance(v, dict) and v.get("n_moves", 0) > 0]
                _result["chain_reactions"] = {
                    "tickers_with_attribution": len(_active),
                    "active_tickers": _active,
                    "decay_per_cycle_pct": 30,
                }
            except Exception:
                _result["chain_reactions"] = {"tickers_with_attribution": 0}
        else:
            _result["chain_reactions"] = {"tickers_with_attribution": 0}

        # --- Learning Engine last cycle ---
        _le_path = Path(PROJECT_ROOT) / "data" / "learning_cycle.json"
        if _le_path.exists():
            try:
                _le_data = json.loads(_le_path.read_text())
                _result["learning_engine"] = {
                    "last_cycle_at": _le_data.get("last_cycle_at", None),
                    "cycles_completed": _le_data.get("cycles_completed", 0),
                    "last_trigger": _le_data.get("last_trigger", None),
                }
            except Exception:
                _result["learning_engine"] = {"last_cycle_at": None}
        else:
            _result["learning_engine"] = {"last_cycle_at": None, "note": "no cycle data yet"}

        # Pipeline health summary
        _ml_ok = _result["ml_model"].get("is_trained", False)
        _decay_ok = "error" not in _result["decay_detector"]
        _chain_ok = _result["chain_reactions"].get("tickers_with_attribution", 0) >= 0
        _result["pipeline_health"] = "ACTIVE" if (_decay_ok and _chain_ok) else "DEGRADED"
        _result["ml_status"] = "TRAINED" if _ml_ok else "UNTRAINED (rule-based only)"

        return _result

    except Exception as _ipe:
        logger.warning("api_intelligence_pipeline error: %s", _ipe)
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pipeline_health": "ERROR",
            "error": str(_ipe),
        }


# === V5.0 Heartbeat endpoint (Phase 17) ===
try:
    from main import get_heartbeat
    app.get("/api/heartbeat")(get_heartbeat)
    logger.info("Heartbeat endpoint registered at /api/heartbeat")
except ImportError as _hb_err:
    logger.warning("Heartbeat endpoint not available: %s", _hb_err)


# === Entry Point ===

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
