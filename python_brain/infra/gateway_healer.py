#!/usr/bin/env python3
"""gateway_healer — watches IB Gateway's health and heals it.

Three healing actions, escalating:

  1. If :4002 is LISTENING but no clients can handshake → raise a
     `gateway.api_wedged` NATS alert (all IBKR services will back off).

  2. If :4002 stops LISTENING for >60s → try `open -a 'IB Gateway 10.37'` to
     relaunch from macOS Launch Services.

  3. If Gateway is logged in but no data is flowing on `ticks.live.*` for
     >5 min during market hours → raise `gateway.data_dead` so supervisor
     restarts the Rust bridge.

Also emits `gateway.health` every 30s with:
    {
      "ts": ...,
      "port_listening": bool,
      "handshake_ok": bool,        # fresh probe with random cid
      "established_sessions": int,
      "ticks_per_min": float,
      "status": "healthy" | "listening_no_api" | "down" | "data_dead"
    }

Runs permanently. If Gateway hangs again, this service detects it within
60s and triggers remediation, instead of the user finding out hours later.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import signal
import socket
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger("gateway-healer")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

NATS_URL = os.environ.get("NATS_URL", "nats://127.0.0.1:4222")
IBKR_HOST = "127.0.0.1"
IBKR_PORT = 4002
IBGATEWAY_APP = Path("/Users/rr/Applications/IB Gateway 10.37/IB Gateway 10.37.app")
HEARTBEAT_S = 30
WEDGE_THRESHOLD_FAILS = 2     # consecutive failed probes before raising alert
DATA_DEAD_MINUTES = 5
RELAUNCH_COOLDOWN_S = 600     # don't spam `open -a`; 10 min between relaunches
PROBE_TIMEOUT = 12


def port_listening(host: str, port: int) -> bool:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


async def handshake_probe(timeout: int = PROBE_TIMEOUT) -> tuple[bool, str]:
    """Try a full ib_insync apiStart handshake. Returns (ok, reason)."""
    try:
        from ib_insync import IB
    except ImportError:
        return False, "ib_insync missing"
    ib = IB()
    cid = random.randint(3000, 9999)
    try:
        await ib.connectAsync(IBKR_HOST, IBKR_PORT, clientId=cid,
                              readonly=True, timeout=timeout)
        ib.disconnect()
        return True, f"ok cid={cid}"
    except asyncio.TimeoutError:
        return False, "apiStart timeout"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def count_sessions() -> int:
    try:
        out = subprocess.check_output(
            ["lsof", "-iTCP:4002", "-sTCP:ESTABLISHED"],
            stderr=subprocess.DEVNULL, text=True,
        )
        return max(0, len(out.splitlines()) - 1)
    except Exception:
        return 0


def relaunch_gateway() -> bool:
    if not IBGATEWAY_APP.exists():
        log.error("Gateway app not found at %s", IBGATEWAY_APP)
        return False
    try:
        subprocess.Popen(
            ["open", "-a", str(IBGATEWAY_APP)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        log.warning("Issued `open -a` to relaunch IB Gateway")
        return True
    except Exception as e:
        log.error("Gateway relaunch failed: %s", e)
        return False


async def run() -> None:
    import nats  # type: ignore
    nc = await nats.connect(NATS_URL, name="aegis-v5-gateway-healer")
    log.info("gateway healer connected to NATS")

    # Track live-tick flow by subscribing
    tick_events = deque(maxlen=1000)   # timestamps of recent ticks

    async def on_tick(_msg):
        tick_events.append(time.time())

    await nc.subscribe("ticks.live.*", cb=on_tick)

    fail_streak = 0
    last_relaunch_ts = 0.0
    last_wedge_alert_ts = 0.0
    last_data_dead_alert_ts = 0.0

    stop = asyncio.Event()
    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
    except NotImplementedError:
        pass

    while not stop.is_set():
        now = time.time()
        listening = port_listening(IBKR_HOST, IBKR_PORT)
        sessions = count_sessions()

        if not listening:
            fail_streak += 1
            log.warning("Gateway NOT listening (streak=%d)", fail_streak)
            status = "down"
            handshake = False
            reason = "port not listening"
            # Auto-relaunch after threshold + cooldown
            if fail_streak >= 2 and (now - last_relaunch_ts) > RELAUNCH_COOLDOWN_S:
                log.warning("Auto-relaunching IB Gateway")
                if relaunch_gateway():
                    last_relaunch_ts = now
                    await nc.publish("gateway.relaunched", json.dumps({
                        "ts": now, "reason": "port_down",
                    }).encode())
                    # Give Gateway 60s before probing
                    await asyncio.sleep(60)
                    continue
        else:
            # Listening; probe the handshake
            handshake, reason = await handshake_probe()
            if handshake:
                fail_streak = 0
                status = "healthy"
            else:
                fail_streak += 1
                status = "listening_no_api"
                if fail_streak >= WEDGE_THRESHOLD_FAILS and (now - last_wedge_alert_ts) > 120:
                    log.warning("Gateway wedge detected (fails=%d reason=%s) — raising alert",
                                fail_streak, reason)
                    await nc.publish("gateway.api_wedged", json.dumps({
                        "ts": now, "streak": fail_streak, "reason": reason,
                        "sessions": sessions,
                    }).encode())
                    last_wedge_alert_ts = now
                # Auto-relaunch on sustained wedge (10+ consecutive failed probes =
                # ~5 minutes) — even when port is listening, force a Gateway
                # restart because the upstream CDC session is dead.
                if fail_streak >= 10 and (now - last_relaunch_ts) > RELAUNCH_COOLDOWN_S:
                    log.warning("Sustained wedge streak=%d — auto-relaunching Gateway", fail_streak)
                    # Kill existing Gateway process first
                    try:
                        out = subprocess.check_output(
                            ["pgrep", "-f", "IB Gateway"], text=True).strip().split()
                        for pid_s in out:
                            try:
                                os.kill(int(pid_s), 9)
                                log.warning("killed stuck Gateway pid=%s", pid_s)
                            except Exception:
                                pass
                        await asyncio.sleep(5)
                    except Exception:
                        pass
                    if relaunch_gateway():
                        last_relaunch_ts = now
                        fail_streak = 0
                        await nc.publish("gateway.relaunched", json.dumps({
                            "ts": now, "reason": "sustained_wedge",
                        }).encode())
                        await asyncio.sleep(60)
                        continue

        # Data-dead check — only during market hours-ish (06:00-22:00 UTC)
        hour_utc = time.gmtime(now).tm_hour
        is_market_hours = 6 <= hour_utc <= 22
        ticks_last_5min = sum(1 for t in tick_events if t > now - 300)
        ticks_per_min = ticks_last_5min / 5.0
        data_dead = is_market_hours and sessions > 0 and ticks_per_min < 0.2 and listening
        if data_dead and (now - last_data_dead_alert_ts) > 300:
            log.warning("Data flow dead despite %d sessions — raising alert", sessions)
            await nc.publish("gateway.data_dead", json.dumps({
                "ts": now, "ticks_per_min": ticks_per_min, "sessions": sessions,
            }).encode())
            last_data_dead_alert_ts = now
            status = "data_dead"

        # Periodic health broadcast
        payload = {
            "ts": now,
            "port_listening": listening,
            "handshake_ok": handshake if listening else False,
            "established_sessions": sessions,
            "ticks_per_min": round(ticks_per_min, 2),
            "fail_streak": fail_streak,
            "status": status,
            "reason": reason if not (listening and handshake) else "ok",
        }
        try:
            await nc.publish("gateway.health", json.dumps(payload).encode())
        except Exception:
            pass
        log.info("gateway.health: %s (sessions=%d ticks/min=%.1f streak=%d)",
                 status, sessions, ticks_per_min, fail_streak)

        await asyncio.sleep(HEARTBEAT_S)

    log.info("gateway healer stopping")
    await nc.drain()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
