"""Rust bridge watchdog — restarts aegis-engine if it dies.

Checks every 30s whether `aegis-engine` process is alive. If not,
spawns a fresh one. Prevents silent bridge death (like what happened
when Gateway cycled).
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

BRIDGE_PATH = "/Users/rr/aegis-v5/rust_core/target/debug/aegis-engine"
LOG_PATH = "/tmp/v5_bridge.log"


def bridge_alive() -> bool:
    r = subprocess.run(["pgrep", "-f", "target/debug/aegis-engine"],
                       capture_output=True)
    return r.returncode == 0


def start_bridge() -> None:
    env = {
        **os.environ,
        "AEGIS_CONFIG_DIR": "/Users/rr/aegis-v5/config",
        "AEGIS_MODE": "paper",
        "RUST_LOG": "info",
    }
    with open(LOG_PATH, "a") as out:
        subprocess.Popen([BRIDGE_PATH], env=env, stdout=out, stderr=out,
                         start_new_session=True)


async def main() -> None:
    log.info("bridge watchdog up")
    while True:
        if not bridge_alive():
            log.warning("bridge DEAD — restarting")
            start_bridge()
            await asyncio.sleep(10)
        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
