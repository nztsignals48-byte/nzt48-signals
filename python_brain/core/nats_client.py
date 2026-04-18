"""Message bus client.

File-backed (JSONL per subject under $AEGIS_V5_DATA/bus/) to let tests, single-host
dev and Phase 1 acceptance run without external infra. Production swaps in real
NATS JetStream with the same API.

Every message carries `schema_version`. Consumers fail CLOSED on unknown version
(`UnknownSchemaVersion` raised by `BusMessage.from_line`).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

DATA_DIR = Path(os.environ.get("AEGIS_V5_DATA", "/Users/rr/aegis-v5/data"))
BUS_DIR = DATA_DIR / "bus"
SUPPORTED_SCHEMA_VERSIONS = {1}


class UnknownSchemaVersion(ValueError):
    pass


def _subject_path(subject: str) -> Path:
    safe = subject.replace("/", "_")
    return BUS_DIR / f"{safe}.jsonl"


@dataclass
class BusMessage:
    subject: str
    schema_version: int
    payload: Dict[str, Any]
    ts_ns: int

    def to_line(self) -> str:
        return json.dumps({
            "subject": self.subject,
            "schema_version": self.schema_version,
            "payload": self.payload,
            "ts_ns": self.ts_ns,
        }) + "\n"

    @classmethod
    def from_line(cls, line: str) -> "BusMessage":
        d = json.loads(line)
        if d["schema_version"] not in SUPPORTED_SCHEMA_VERSIONS:
            raise UnknownSchemaVersion(f"{d['schema_version']} not in {SUPPORTED_SCHEMA_VERSIONS}")
        return cls(subject=d["subject"], schema_version=d["schema_version"],
                   payload=d["payload"], ts_ns=d["ts_ns"])


@dataclass
class NatsClient:
    url: str = "nats://localhost:4222"
    backend: str = "file"
    _subscribers: Dict[str, List[Callable[[BusMessage], None]]] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "NatsClient":
        return cls(url=os.environ.get("AEGIS_V5_NATS_URL", "nats://localhost:4222"))

    async def connect(self) -> None:
        BUS_DIR.mkdir(parents=True, exist_ok=True)

    async def publish(self, subject: str, payload: Dict[str, Any], schema_version: int = 1) -> None:
        msg = BusMessage(subject=subject, schema_version=schema_version,
                         payload=payload, ts_ns=time.time_ns())
        path = _subject_path(subject)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(msg.to_line())
        for handler in self._subscribers.get(subject, []):
            try:
                handler(msg)
            except Exception as exc:
                print(f"[nats] handler error on {subject}: {exc}")

    async def subscribe(self, subject: str, handler: Callable[[BusMessage], None]) -> None:
        self._subscribers.setdefault(subject, []).append(handler)

    def subjects_on_disk(self) -> List[str]:
        if not BUS_DIR.exists():
            return []
        return [p.stem.replace("_", ".") for p in BUS_DIR.glob("*.jsonl")]

    async def last(self, subject: str) -> Optional[BusMessage]:
        path = _subject_path(subject)
        if not path.exists():
            return None
        lines = path.read_text().splitlines()
        if not lines:
            return None
        try:
            return BusMessage.from_line(lines[-1])
        except UnknownSchemaVersion:
            return None
