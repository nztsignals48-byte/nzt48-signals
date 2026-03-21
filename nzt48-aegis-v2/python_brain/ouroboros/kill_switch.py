"""N10a — Remote Kill Switch for AEGIS V2.

Three control mechanisms:
  1. File-based: touch /app/data/KILL or /app/data/PAUSE → engine reacts within 1s
  2. Telegram: /kill, /pause, /resume, /status commands via bot polling
  3. SSH: aegis-ctl.sh convenience wrapper

The Rust engine checks for KILL/PAUSE files every second in the main loop.
This module handles the Telegram command listener + CLI convenience commands.

SAFETY:
  - /kill requires confirmation (two-step: /kill then "yes" within 30s)
  - Chat ID validated — only authorized operator can send commands
  - All actions logged to /app/data/kill_switch_audit.ndjson

Usage:
    # Telegram listener (runs as daemon in entrypoint.sh)
    python3 -m python_brain.ouroboros.kill_switch --listen

    # CLI commands (SSH convenience)
    python3 -m python_brain.ouroboros.kill_switch --kill
    python3 -m python_brain.ouroboros.kill_switch --pause
    python3 -m python_brain.ouroboros.kill_switch --resume
    python3 -m python_brain.ouroboros.kill_switch --status
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
KILL_FILE = DATA_DIR / "KILL"
PAUSE_FILE = DATA_DIR / "PAUSE"
AUDIT_FILE = DATA_DIR / "kill_switch_audit.ndjson"
TELEMETRY_FILE = Path(os.environ.get("AEGIS_WAL_DIR", "/app/events")) / "telemetry_snapshot.json"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
API_BASE = "https://api.telegram.org/bot"
POLL_INTERVAL = 2  # seconds between Telegram getUpdates polls

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [KillSwitch] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("kill_switch")


# ---------------------------------------------------------------------------
# Load credentials from .env if not in environment
# ---------------------------------------------------------------------------
def _load_env():
    global BOT_TOKEN, CHAT_ID
    if BOT_TOKEN and CHAT_ID:
        return
    for d in [Path(__file__).resolve().parents[2], Path("/app"), Path.cwd()]:
        env_file = d / ".env"
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key, val = key.strip(), val.strip()
                    if key == "TELEGRAM_BOT_TOKEN" and not BOT_TOKEN:
                        BOT_TOKEN = val
                    elif key == "TELEGRAM_CHAT_ID" and not CHAT_ID:
                        CHAT_ID = val
            break

_load_env()


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
def _audit(action: str, source: str, detail: str = ""):
    """Append to audit log (immutable NDJSON)."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "source": source,
        "detail": detail,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(AUDIT_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass
    log.info("AUDIT: %s via %s — %s", action, source, detail)


# ---------------------------------------------------------------------------
# File-based kill/pause/resume
# ---------------------------------------------------------------------------
def do_kill(source: str = "cli") -> str:
    """Create KILL file → engine shuts down within 1 second."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    KILL_FILE.write_text(f"KILL requested at {ts} via {source}\n")
    _audit("KILL", source, "KILL file created")
    return f"KILL signal sent. Engine will shut down within 1 second."


def do_pause(source: str = "cli") -> str:
    """Create PAUSE file → engine freezes signals but keeps market data."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    PAUSE_FILE.write_text(f"PAUSED at {ts} via {source}\n")
    _audit("PAUSE", source, "PAUSE file created")
    return f"PAUSE signal sent. Signal generation frozen (market data continues)."


def do_resume(source: str = "cli") -> str:
    """Remove PAUSE file → engine resumes signal generation."""
    if PAUSE_FILE.exists():
        PAUSE_FILE.unlink()
        _audit("RESUME", source, "PAUSE file removed")
        return "RESUME signal sent. Signal generation resumed."
    return "Engine was not paused."


def get_status() -> dict:
    """Read engine telemetry + kill switch state."""
    status = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kill_file": KILL_FILE.exists(),
        "pause_file": PAUSE_FILE.exists(),
        "engine_pid": _find_engine_pid(),
        "engine_alive": False,
        "telemetry": {},
    }

    # Check if engine process is running
    pid = status["engine_pid"]
    if pid:
        try:
            os.kill(pid, 0)  # Signal 0 = check if alive
            status["engine_alive"] = True
        except (ProcessLookupError, PermissionError):
            status["engine_alive"] = False

    # Read telemetry snapshot
    if TELEMETRY_FILE.exists():
        try:
            with open(TELEMETRY_FILE) as f:
                status["telemetry"] = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    return status


def _find_engine_pid() -> Optional[int]:
    """Find the aegis engine PID."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", "aegis"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().split()[0])
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        pass
    return None


def format_status(status: dict) -> str:
    """Format status dict into human-readable Telegram message."""
    t = status.get("telemetry", {})
    engine = "ALIVE" if status.get("engine_alive") else "DEAD"
    paused = "YES" if status.get("pause_file") else "NO"

    lines = [
        f"<b>AEGIS STATUS</b>",
        f"",
        f"Engine: <b>{engine}</b> (PID {status.get('engine_pid', '?')})",
        f"Paused: <b>{paused}</b>",
        f"",
    ]

    if t:
        regime = t.get("regime", "?")
        lines.extend([
            f"Regime: <b>{regime}</b>",
            f"Positions: {t.get('positions', '?')}",
            f"Equity: {t.get('equity', '?')}",
            f"Ticks: {t.get('ticks_received', '?')} | Signals: {t.get('signals_generated', '?')}",
            f"Approved: {t.get('signals_approved', '?')} | Vetoed: {t.get('signals_vetoed', '?')}",
            f"Session: {t.get('session_mode', '?')}",
        ])
    else:
        lines.append("No telemetry data available.")

    lines.append(f"\n<i>{status['timestamp']}</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------
def _tg_request(method: str, params: dict) -> dict:
    """Make a Telegram Bot API request."""
    if not BOT_TOKEN:
        return {"ok": False, "error": "no token"}
    url = f"{API_BASE}{BOT_TOKEN}/{method}"
    body = json.dumps(params).encode("utf-8")
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except (URLError, HTTPError, TimeoutError) as e:
        log.warning("Telegram API error: %s", e)
        return {"ok": False, "error": str(e)}


def _tg_send(text: str, parse_mode: str = "HTML") -> dict:
    """Send a message via Telegram."""
    if len(text) > 4000:
        text = text[:4000] + "\n\n<i>...truncated</i>"
    return _tg_request("sendMessage", {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    })


# ---------------------------------------------------------------------------
# Telegram Command Listener
# ---------------------------------------------------------------------------
class TelegramCommandListener:
    """Polls Telegram for operator commands. Validates chat_id for security."""

    COMMANDS = {"/kill", "/pause", "/resume", "/status", "/help"}

    def __init__(self):
        self.offset = 0
        self.pending_kill_from = None  # chat_id of pending kill confirmation
        self.pending_kill_ts = 0.0    # timestamp of /kill request
        self.running = True

    def start(self):
        """Main polling loop."""
        log.info("Telegram command listener starting (chat_id=%s)", CHAT_ID)
        _audit("LISTENER_START", "telegram", f"chat_id={CHAT_ID}")

        # Handle SIGTERM/SIGINT for clean shutdown
        def _stop(sig, frame):
            self.running = False
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        while self.running:
            try:
                self._poll()
            except Exception as e:
                log.error("Poll error: %s", e)
            time.sleep(POLL_INTERVAL)

        log.info("Telegram command listener stopped")

    def _poll(self):
        """Fetch and process new Telegram updates."""
        result = _tg_request("getUpdates", {
            "offset": self.offset,
            "timeout": 10,  # long polling
            "allowed_updates": ["message"],
        })
        if not result.get("ok"):
            return

        for update in result.get("result", []):
            self.offset = update["update_id"] + 1
            msg = update.get("message", {})
            text = msg.get("text", "").strip()
            chat_id = str(msg.get("chat", {}).get("id", ""))

            if not text or not chat_id:
                continue

            # Security: only accept commands from authorized chat
            if chat_id != CHAT_ID:
                log.warning("Unauthorized command from chat_id=%s: %s", chat_id, text)
                _audit("UNAUTHORIZED", "telegram", f"chat_id={chat_id}, text={text[:100]}")
                continue

            self._handle_command(text.lower())

    def _handle_command(self, text: str):
        """Process a validated command."""
        if text == "/status":
            status = get_status()
            _tg_send(format_status(status))

        elif text == "/pause":
            result = do_pause(source="telegram")
            _tg_send(f"\u23f8 {result}")

        elif text == "/resume":
            result = do_resume(source="telegram")
            _tg_send(f"\u25b6 {result}")

        elif text == "/kill":
            self.pending_kill_from = CHAT_ID
            self.pending_kill_ts = time.time()
            _tg_send(
                "\u26a0\ufe0f <b>KILL SWITCH CONFIRMATION</b>\n\n"
                "This will gracefully shut down the AEGIS engine.\n"
                "All open positions will be flattened.\n\n"
                "Type <b>yes</b> within 30 seconds to confirm.\n"
                "Type anything else to cancel."
            )

        elif text == "yes" and self.pending_kill_from:
            if time.time() - self.pending_kill_ts <= 30:
                result = do_kill(source="telegram")
                _tg_send(f"\U0001f6d1 {result}")
                self.pending_kill_from = None
            else:
                _tg_send("Kill confirmation expired (>30s). Send /kill again to retry.")
                self.pending_kill_from = None

        elif text == "/help":
            _tg_send(
                "<b>AEGIS Kill Switch Commands</b>\n\n"
                "/status — Engine status + telemetry\n"
                "/pause — Freeze signal generation (market data continues)\n"
                "/resume — Resume signal generation\n"
                "/kill — Graceful shutdown (requires confirmation)\n"
                "/help — This message"
            )

        elif self.pending_kill_from:
            # Any non-"yes" response cancels the kill
            _tg_send("Kill cancelled.")
            self.pending_kill_from = None
            _audit("KILL_CANCELLED", "telegram", f"response={text[:50]}")

        # Ignore unrecognized commands silently


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="AEGIS V2 Kill Switch (N10a)")
    parser.add_argument("--kill", action="store_true", help="Send KILL signal")
    parser.add_argument("--pause", action="store_true", help="Send PAUSE signal")
    parser.add_argument("--resume", action="store_true", help="Send RESUME signal")
    parser.add_argument("--status", action="store_true", help="Show engine status")
    parser.add_argument("--listen", action="store_true",
                        help="Start Telegram command listener (daemon)")
    args = parser.parse_args()

    if args.kill:
        print(do_kill(source="cli"))
    elif args.pause:
        print(do_pause(source="cli"))
    elif args.resume:
        print(do_resume(source="cli"))
    elif args.status:
        status = get_status()
        import pprint
        pprint.pprint(status)
    elif args.listen:
        if not BOT_TOKEN or not CHAT_ID:
            print("ERROR: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
            sys.exit(1)
        listener = TelegramCommandListener()
        listener.start()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
