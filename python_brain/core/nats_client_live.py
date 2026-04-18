"""Real-NATS client — drop-in replacement for the file-backed nats_client stub.

Used by runners that need the engine to actually publish to the NATS bus
(Rust bridge + Python services). Keeps the same public API:
    connect(), publish(subject, payload, schema_version=1), subscribe(subject, handler)

Activated by setting AEGIS_V5_NATS_BACKEND=live in env.

When AEGIS_V5_NATS_BACKEND is unset or 'file', the stub is used
(run_live_sim.py + unit tests).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class LiveNatsClient:
    url: str = os.environ.get("AEGIS_V5_NATS_URL",
                              os.environ.get("NATS_URL", "nats://127.0.0.1:4222"))
    backend: str = "live"
    _nc: Optional[Any] = None
    _subscribers: Dict[str, List[Callable]] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "LiveNatsClient":
        return cls()

    async def connect(self) -> None:
        import nats  # type: ignore
        self._nc = await nats.connect(self.url, name="aegis-v5-engine-live")

    async def publish(self, subject: str, payload: Dict[str, Any],
                      schema_version: int = 1) -> None:
        if self._nc is None:
            return
        wrapped = {
            "subject": subject,
            "schema_version": schema_version,
            "payload": payload,
            "ts_ns": time.time_ns(),
        }
        # For consumers that expect raw payload (e.g. signal-to-order bridge),
        # publish the unwrapped payload plus top-level signal fields.
        # We publish the payload as-is so downstream can read .ticker etc.
        await self._nc.publish(subject, json.dumps(payload).encode("utf-8"))

    async def subscribe(self, subject: str, handler: Callable) -> None:
        if self._nc is None:
            return
        async def _cb(msg):
            try:
                p = json.loads(msg.data)
            except Exception:
                return
            try:
                handler(p)
            except Exception as exc:
                print(f"[live-nats] handler error on {subject}: {exc}")
        await self._nc.subscribe(subject, cb=_cb)


def get_client():
    """Factory: returns real-NATS when AEGIS_V5_NATS_BACKEND=live, else file-backed stub."""
    if os.environ.get("AEGIS_V5_NATS_BACKEND", "").lower() == "live":
        return LiveNatsClient.from_env()
    # Fall back to scaffold stub.
    from python_brain.core.nats_client import NatsClient  # type: ignore
    return NatsClient.from_env()
