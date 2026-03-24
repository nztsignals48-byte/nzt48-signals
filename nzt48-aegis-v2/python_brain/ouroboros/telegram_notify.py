"""AEGIS V2 — Telegram Notification Module.

Lightweight, zero-dependency (beyond stdlib + requests) Telegram notifier.
Sends trade alerts, system alerts, session reports, and PDF documents.

Usage:
    # As module
    from python_brain.ouroboros.telegram_notify import send_alert, send_document

    # As CLI (for cron / testing)
    python3 -m python_brain.ouroboros.telegram_notify --test
    python3 -m python_brain.ouroboros.telegram_notify --session-start asian
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
API_BASE = "https://api.telegram.org/bot"
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]  # exponential backoff seconds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [Telegram] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("telegram_notify")

# ---------------------------------------------------------------------------
# Load credentials from .env if not in environment
# ---------------------------------------------------------------------------

def _load_env():
    """Load BOT_TOKEN and CHAT_ID from .env files if not already set."""
    global BOT_TOKEN, CHAT_ID
    if BOT_TOKEN and CHAT_ID:
        return

    # Search up from this file to find .env
    search_dirs = [
        Path(__file__).resolve().parent.parent.parent,  # project root
        Path("/app"),  # Docker
        Path.cwd(),
    ]
    for d in search_dirs:
        env_file = d / ".env"
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip()
                    if key == "TELEGRAM_BOT_TOKEN" and not BOT_TOKEN:
                        BOT_TOKEN = val
                    elif key == "TELEGRAM_CHAT_ID" and not CHAT_ID:
                        CHAT_ID = val
            break

_load_env()

# ---------------------------------------------------------------------------
# Core send functions
# ---------------------------------------------------------------------------

def _api_call(method: str, data: dict, files: Optional[dict] = None) -> dict:
    """Make a Telegram Bot API call with retry logic."""
    if not BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set — skipping send")
        return {"ok": False, "error": "no token"}
    if not CHAT_ID:
        log.error("TELEGRAM_CHAT_ID not set — skipping send")
        return {"ok": False, "error": "no chat_id"}

    url = f"{API_BASE}{BOT_TOKEN}/{method}"

    for attempt in range(MAX_RETRIES):
        try:
            if files:
                # Multipart form data for file uploads
                boundary = f"----AEGISBoundary{int(time.time()*1000)}"
                body = b""
                for key, val in data.items():
                    body += f"--{boundary}\r\n".encode()
                    body += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
                    body += f"{val}\r\n".encode()
                for key, (filename, content, content_type) in files.items():
                    body += f"--{boundary}\r\n".encode()
                    body += f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode()
                    body += f"Content-Type: {content_type}\r\n\r\n".encode()
                    body += content
                    body += b"\r\n"
                body += f"--{boundary}--\r\n".encode()

                req = Request(url, data=body, method="POST")
                req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
            else:
                body = json.dumps(data).encode("utf-8")
                req = Request(url, data=body, method="POST")
                req.add_header("Content-Type", "application/json")

            with urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
                if result.get("ok"):
                    return result
                log.warning("API returned ok=false: %s", result)
                return result

        except (URLError, HTTPError, TimeoutError) as e:
            delay = RETRY_DELAYS[attempt] if attempt < len(RETRY_DELAYS) else 4
            log.warning("Telegram API attempt %d failed: %s (retry in %ds)",
                       attempt + 1, e, delay)
            if attempt < MAX_RETRIES - 1:
                time.sleep(delay)

    log.error("Telegram API failed after %d attempts", MAX_RETRIES)
    return {"ok": False, "error": "max retries exceeded"}


def send_message(text: str, parse_mode: str = "HTML",
                 disable_notification: bool = False) -> dict:
    """Send a text message via Telegram."""
    # Telegram limit is 4096 chars
    if len(text) > 4000:
        text = text[:4000] + "\n\n<i>... truncated</i>"

    return _api_call("sendMessage", {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_notification": disable_notification,
        "disable_web_page_preview": True,
    })


def send_document(file_path: str, caption: str = "") -> dict:
    """Send a document (PDF, etc) via Telegram."""
    path = Path(file_path)
    if not path.exists():
        log.error("File not found: %s", file_path)
        return {"ok": False, "error": f"file not found: {file_path}"}

    with open(path, "rb") as f:
        content = f.read()

    data = {"chat_id": CHAT_ID}
    if caption:
        data["caption"] = caption[:1024]  # Telegram caption limit
        data["parse_mode"] = "HTML"

    return _api_call("sendDocument", data, files={
        "document": (path.name, content, "application/pdf"),
    })


# ---------------------------------------------------------------------------
# High-level alert functions
# ---------------------------------------------------------------------------

def send_alert(message: str, severity: str = "INFO") -> dict:
    """Send a system alert with severity prefix."""
    icons = {
        "CRITICAL": "\u26a0\ufe0f",  # warning
        "HIGH": "\U0001f534",  # red circle
        "MEDIUM": "\U0001f7e1",  # yellow circle
        "LOW": "\U0001f7e2",  # green circle
        "INFO": "\u2139\ufe0f",  # info
    }
    icon = icons.get(severity.upper(), "\u2139\ufe0f")
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    text = f"{icon} <b>AEGIS [{severity}]</b> {ts}\n\n{message}"
    is_silent = severity.upper() in ("LOW", "INFO")
    return send_message(text, disable_notification=is_silent)


def send_trade_entry(ticker: str, direction: str, price: float,
                     confidence: float, kelly: float, shares: int,
                     strategy: str = "") -> dict:
    """Send a trade entry notification."""
    icon = "\U0001f7e2" if direction == "Long" else "\U0001f534"
    text = (
        f"{icon} <b>TRADE ENTRY</b>\n"
        f"\n"
        f"<b>{ticker}</b> — {direction}\n"
        f"Price: {price:.4f}\n"
        f"Confidence: {confidence:.0f}/100\n"
        f"Kelly: {kelly:.1%} | Shares: {shares}\n"
    )
    if strategy:
        text += f"Strategy: {strategy}\n"
    text += f"\n<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</i>"
    return send_message(text)


def send_trade_exit(ticker: str, direction: str, entry_price: float,
                    exit_price: float, pnl_pct: float, pnl_gbp: float,
                    reason: str = "", rung_achieved: int = 0) -> dict:
    """Send a trade exit notification."""
    icon = "\U0001f7e2" if pnl_gbp >= 0 else "\U0001f534"
    text = (
        f"{icon} <b>TRADE EXIT</b>\n"
        f"\n"
        f"<b>{ticker}</b> — {direction}\n"
    )
    # Only show entry/exit prices if available (WAL PositionClosed may not have them)
    if entry_price > 0 or exit_price > 0:
        text += f"Entry: {entry_price:.4f} | Exit: {exit_price:.4f}\n"
    # Show P&L — prefer percentage if available, always show GBP
    if pnl_pct != 0:
        text += f"P&L: {pnl_pct:+.2f}% ({pnl_gbp:+.2f} GBP)\n"
    else:
        text += f"P&L: \u00a3{pnl_gbp:+.2f}\n"
    text += f"Rung: {rung_achieved}/5\n"
    if reason:
        text += f"Reason: {reason}\n"
    text += f"\n<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</i>"
    return send_message(text)


def send_regime_change(old_regime: str, new_regime: str, reason: str = "") -> dict:
    """Send regime transition notification."""
    icons = {"Normal": "\U0001f7e2", "Reduce": "\U0001f7e1",
             "Flatten": "\U0001f7e0", "Halt": "\U0001f534"}
    icon = icons.get(new_regime, "\u26a0\ufe0f")
    text = (
        f"{icon} <b>REGIME CHANGE</b>\n"
        f"\n"
        f"{old_regime} \u2192 <b>{new_regime}</b>\n"
    )
    if reason:
        text += f"Reason: {reason}\n"
    text += f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}</i>"
    return send_message(text)


def send_session_start(session: str, ticker_count: int,
                       exchanges: list, pdf_path: Optional[str] = None) -> dict:
    """Send session start notification with optional PDF briefing."""
    icons = {"asian": "\U0001f30f", "european": "\U0001f30d", "american": "\U0001f30e"}
    icon = icons.get(session, "\U0001f4ca")
    text = (
        f"{icon} <b>SESSION START: {session.upper()}</b>\n"
        f"\n"
        f"Tickers: {ticker_count}\n"
        f"Exchanges: {', '.join(exchanges)}\n"
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
    )
    result = send_message(text)

    if pdf_path and Path(pdf_path).exists():
        send_document(pdf_path, caption=f"{icon} {session.upper()} Session Briefing")

    return result


def send_session_summary(session: str, trades: int, wins: int,
                         pnl_gbp: float, max_rung: int) -> dict:
    """Send end-of-session summary."""
    icon = "\U0001f7e2" if pnl_gbp >= 0 else "\U0001f534"
    wr = (wins / trades * 100) if trades > 0 else 0
    text = (
        f"{icon} <b>SESSION END: {session.upper()}</b>\n"
        f"\n"
        f"Trades: {trades} | Wins: {wins} ({wr:.0f}%)\n"
        f"P&L: {pnl_gbp:+.2f} GBP\n"
        f"Max Rung: {max_rung}/5\n"
        f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>"
    )
    return send_message(text)


def send_heartbeat() -> dict:
    """Send a system heartbeat (silent)."""
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    return send_message(
        f"\U0001f49a <b>AEGIS Heartbeat</b> {ts} — All systems operational",
        disable_notification=True,
    )


def send_hourly_pnl() -> dict:
    """Send hourly trade count + P&L summary from WAL and system_memory."""
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    # Read system_memory.json for cumulative stats
    mem_path = Path("/app/data/system_memory.json")
    cum_pnl = 0.0
    cum_trades = 0
    cum_wr = 0.0
    cum_pf = 0.0
    if mem_path.exists():
        try:
            mem = json.loads(mem_path.read_text())
            cum_pnl = mem.get("cumulative_pnl", 0.0)
            cum_trades = mem.get("total_trades", 0)
            cum_wr = mem.get("win_rate", 0.0) * 100 if mem.get("win_rate", 0) <= 1.0 else mem.get("win_rate", 0.0)
            cum_pf = mem.get("all_time_profit_factor", 0.0)
        except Exception:
            pass
    # Count today's trades from WAL
    today_trades = 0
    today_pnl = 0.0
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    wal_path = Path("/app/events/current.ndjson")
    if wal_path.exists():
        try:
            for line in wal_path.read_text().splitlines():
                if "PositionClosed" in line:
                    evt = json.loads(line)
                    payload = evt.get("payload", {})
                    if payload.get("type") == "PositionClosed":
                        today_trades += 1
                        today_pnl += payload.get("final_pnl", 0.0)
        except Exception:
            pass
    # Count open positions from WAL (opened but not closed)
    open_count = 0
    opened_tickers = set()
    closed_tickers = set()
    if wal_path.exists():
        try:
            for line in wal_path.read_text().splitlines():
                evt = json.loads(line)
                payload = evt.get("payload", {})
                tid = payload.get("ticker_id", -1)
                if payload.get("type") == "SimulatedFill":
                    opened_tickers.add(tid)
                elif payload.get("type") == "PositionClosed":
                    closed_tickers.add(tid)
            open_count = len(opened_tickers - closed_tickers)
        except Exception:
            pass
    # Format P&L with color emoji
    pnl_emoji = "\U0001f7e2" if today_pnl >= 0 else "\U0001f534"
    cum_emoji = "\U0001f7e2" if cum_pnl >= 0 else "\U0001f534"
    msg = (
        f"\U0001f4ca <b>AEGIS Hourly Update</b> {ts}\n"
        f"\n"
        f"<b>Today:</b>\n"
        f"  Trades: {today_trades} | Open: {open_count}\n"
        f"  {pnl_emoji} P&L: £{today_pnl:+.2f}\n"
        f"\n"
        f"<b>Cumulative:</b>\n"
        f"  Trades: {cum_trades} | WR: {cum_wr:.1f}%\n"
        f"  {cum_emoji} P&L: £{cum_pnl:+.2f} | PF: {cum_pf:.2f}\n"
    )
    return send_message(msg)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="AEGIS V2 Telegram Notifier")
    parser.add_argument("--test", action="store_true", help="Send a test message")
    parser.add_argument("--session-start", type=str, choices=["asian", "european", "american"],
                        help="Send session start notification")
    parser.add_argument("--heartbeat", action="store_true", help="Send heartbeat")
    parser.add_argument("--hourly-pnl", action="store_true", help="Send hourly trade+P&L summary")
    parser.add_argument("--send-pdf", type=str, help="Send a PDF file")
    parser.add_argument("--alert", type=str, help="Send an alert message")
    parser.add_argument("--severity", type=str, default="INFO",
                        choices=["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"])
    args = parser.parse_args()

    if args.test:
        result = send_alert(
            "Test message from AEGIS V2.\n"
            f"Bot token: ...{BOT_TOKEN[-6:]}\n"
            f"Chat ID: {CHAT_ID}\n"
            f"Timestamp: {datetime.now(timezone.utc).isoformat()}",
            severity="INFO",
        )
        print(json.dumps(result, indent=2))

    elif args.session_start:
        from python_brain.ouroboros.telegram_notify import send_session_start
        session_exchanges = {
            "asian": ["XTKS", "XASX", "XKRX", "XNZE", "XHKG", "XSES"],
            "european": ["LSE", "XETRA", "EURONEXT", "NYSE", "NASDAQ"],
            "american": ["NYSE", "NASDAQ", "AMEX"],
            "us_only": ["NYSE", "NASDAQ", "AMEX"],
        }
        result = send_session_start(
            args.session_start,
            ticker_count=200,
            exchanges=session_exchanges.get(args.session_start, []),
        )
        print(json.dumps(result, indent=2))

    elif args.heartbeat:
        result = send_heartbeat()
        print(json.dumps(result, indent=2))

    elif args.hourly_pnl:
        result = send_hourly_pnl()
        print(json.dumps(result, indent=2))

    elif args.send_pdf:
        result = send_document(args.send_pdf, caption="AEGIS V2 Report")
        print(json.dumps(result, indent=2))

    elif args.alert:
        result = send_alert(args.alert, severity=args.severity)
        print(json.dumps(result, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
