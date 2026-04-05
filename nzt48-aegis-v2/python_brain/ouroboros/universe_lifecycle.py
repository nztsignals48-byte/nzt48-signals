"""Universe Lifecycle — Governed state machine for every instrument in the universe.

Every instrument gets a lifecycle state. Transitions are explicit, logged, and
reason-coded. Prevents contracts.toml from becoming a graveyard.

States:
    discovered → resolved → admissible → ranked → shortlisted → live_active
                                                                → sunset → inactive
                                                                → quarantined → retired

State definitions:
    discovered:   found by IBKR scanner, no contract metadata yet
    resolved:     reqContractDetails returned valid con_id
    admissible:   passed all deterministic hard gates
    ranked:       scored by Gemini (or deterministic fallback)
    shortlisted:  in the 250 (core_200 or tactical_50)
    live_active:  in the live 100 being streamed
    sunset:       reduce-only — evicted from live_100 but position still open.
                  blocks re-entry until Rust confirms position == 0.
                  counts toward live_100 slots until flat.
    inactive:     was active, now below threshold / exchange closed / position flat
    quarantined:  flagged for review (resolution failure, delisted, etc.)
    retired:      permanently removed from universe

Persistence: data/universe/lifecycle_state.json
Audit trail:  data/universe/lifecycle_log.ndjson

Usage:
    from python_brain.ouroboros.universe_lifecycle import LifecycleManager
    mgr = LifecycleManager()
    mgr.load()
    transitions = mgr.update_from_run(current_admissible, shortlist, live_100)
    mgr.apply_quarantine_rules()
    mgr.apply_retirement_rules()
    mgr.save()
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from python_brain.ouroboros.universe_reason_codes import (
    QuarantineReason,
    RetireReason,
)

log = logging.getLogger("universe_lifecycle")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
UNIVERSE_DIR = DATA_DIR / "universe"
STATE_FILE = UNIVERSE_DIR / "lifecycle_state.json"
LOG_FILE = UNIVERSE_DIR / "lifecycle_log.ndjson"

# ---------------------------------------------------------------------------
# Valid states and transitions
# ---------------------------------------------------------------------------
VALID_STATES = {
    "discovered", "resolved", "admissible", "ranked",
    "shortlisted", "live_active", "sunset", "inactive",
    "quarantined", "retired",
}

# Allowed state transitions: from_state → {to_state, ...}
ALLOWED_TRANSITIONS = {
    "discovered":   {"resolved", "quarantined", "retired"},
    "resolved":     {"admissible", "quarantined", "retired"},
    "admissible":   {"ranked", "quarantined", "retired"},
    "ranked":       {"shortlisted", "inactive", "quarantined"},
    "shortlisted":  {"live_active", "inactive", "quarantined"},
    "live_active":  {"sunset", "inactive", "shortlisted", "quarantined"},
    "sunset":       {"inactive", "quarantined", "retired"},  # reduce-only until position flat
    "inactive":     {"ranked", "shortlisted", "live_active", "quarantined", "retired"},
    "quarantined":  {"admissible", "retired"},
}

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
QUARANTINE_RESOLUTION_FAILURES = 3      # quarantine after N resolution failures
QUARANTINE_RANKING_IRRELEVANT_RUNS = 10 # quarantine after N runs not ranked
QUARANTINE_ZERO_LIQUIDITY_DAYS = 7      # quarantine after N days zero liquidity
RETIREMENT_QUARANTINE_DAYS = 14         # retire after N days quarantined


class InstrumentState:
    """Lifecycle state for a single instrument."""

    def __init__(
        self,
        symbol: str,
        con_id: int = 0,
        exchange: str = "",
        state: str = "discovered",
        resolution_failures: int = 0,
        ranking_miss_runs: int = 0,
        zero_liquidity_days: int = 0,
        quarantined_at: str = "",
        last_seen_run: str = "",
        last_transition: str = "",
        reason_code: str = "",
        sunset_since: str = "",
        sunset_reason: str = "",
        position_confirmed_flat_at: str = "",
        tags: Optional[Dict[str, Any]] = None,
    ):
        self.symbol = symbol
        self.con_id = con_id
        self.exchange = exchange
        self.state = state
        self.resolution_failures = resolution_failures
        self.ranking_miss_runs = ranking_miss_runs
        self.zero_liquidity_days = zero_liquidity_days
        self.quarantined_at = quarantined_at
        self.last_seen_run = last_seen_run
        self.last_transition = last_transition
        self.reason_code = reason_code
        self.sunset_since = sunset_since
        self.sunset_reason = sunset_reason
        self.position_confirmed_flat_at = position_confirmed_flat_at
        self.tags = tags or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "con_id": self.con_id,
            "exchange": self.exchange,
            "state": self.state,
            "resolution_failures": self.resolution_failures,
            "ranking_miss_runs": self.ranking_miss_runs,
            "zero_liquidity_days": self.zero_liquidity_days,
            "quarantined_at": self.quarantined_at,
            "last_seen_run": self.last_seen_run,
            "last_transition": self.last_transition,
            "reason_code": self.reason_code,
            "sunset_since": self.sunset_since,
            "sunset_reason": self.sunset_reason,
            "position_confirmed_flat_at": self.position_confirmed_flat_at,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "InstrumentState":
        return cls(
            symbol=d["symbol"],
            con_id=d.get("con_id", 0),
            exchange=d.get("exchange", ""),
            state=d.get("state", "discovered"),
            resolution_failures=d.get("resolution_failures", 0),
            ranking_miss_runs=d.get("ranking_miss_runs", 0),
            zero_liquidity_days=d.get("zero_liquidity_days", 0),
            quarantined_at=d.get("quarantined_at", ""),
            last_seen_run=d.get("last_seen_run", ""),
            last_transition=d.get("last_transition", ""),
            reason_code=d.get("reason_code", ""),
            sunset_since=d.get("sunset_since", ""),
            sunset_reason=d.get("sunset_reason", ""),
            position_confirmed_flat_at=d.get("position_confirmed_flat_at", ""),
            tags=d.get("tags", {}),
        )


class LifecycleManager:
    """Manages lifecycle state for all instruments."""

    def __init__(self):
        self._state: Dict[str, InstrumentState] = {}
        self._transitions: List[Dict[str, Any]] = []

    # ── Persistence ──────────────────────────────────────────────────────

    def load(self) -> int:
        """Load lifecycle state from disk. Returns instrument count."""
        UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
        if not STATE_FILE.exists():
            log.info("No lifecycle state file found — starting fresh")
            return 0
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
            for sym, d in data.get("instruments", {}).items():
                self._state[sym] = InstrumentState.from_dict(d)
            log.info("Loaded lifecycle state: %d instruments", len(self._state))
            return len(self._state)
        except Exception as e:
            log.error("Failed to load lifecycle state: %s", e)
            return 0

    def save(self) -> bool:
        """Atomic save lifecycle state to disk."""
        UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "updated": datetime.now(timezone.utc).isoformat(),
            "instrument_count": len(self._state),
            "instruments": {sym: s.to_dict() for sym, s in self._state.items()},
        }
        try:
            fd, tmp = tempfile.mkstemp(dir=UNIVERSE_DIR, suffix=".json")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, STATE_FILE)
            log.info("Saved lifecycle state: %d instruments", len(self._state))
            return True
        except Exception as e:
            log.error("Failed to save lifecycle state: %s", e)
            return False

    def _log_transition(
        self, symbol: str, con_id: int, from_state: str, to_state: str, reason: str
    ):
        """Append transition to NDJSON audit log."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "con_id": con_id,
            "from": from_state,
            "to": to_state,
            "reason": reason,
        }
        self._transitions.append(entry)
        try:
            UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
            with open(LOG_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass  # Audit log failure is non-critical

    # ── State Transitions ────────────────────────────────────────────────

    def transition(
        self, symbol: str, to_state: str, reason: str, con_id: int = 0, exchange: str = ""
    ) -> bool:
        """Transition an instrument to a new state. Returns True if successful."""
        if to_state not in VALID_STATES:
            log.warning("Invalid target state '%s' for %s", to_state, symbol)
            return False

        inst = self._state.get(symbol)
        if inst is None:
            # New instrument — create in discovered state first
            if to_state == "discovered":
                inst = InstrumentState(symbol=symbol, con_id=con_id, exchange=exchange)
                self._state[symbol] = inst
                inst.last_transition = datetime.now(timezone.utc).isoformat()
                inst.reason_code = reason
                self._log_transition(symbol, con_id, "new", "discovered", reason)
                return True
            else:
                # Auto-create as discovered, then validate the target transition
                inst = InstrumentState(symbol=symbol, con_id=con_id, exchange=exchange)
                # Only add to state if the subsequent transition is valid
                allowed_from_discovered = ALLOWED_TRANSITIONS.get("discovered", set())
                if to_state not in allowed_from_discovered:
                    log.warning(
                        "Cannot create %s directly in state '%s' (must be discovered first)",
                        symbol, to_state,
                    )
                    return False
                self._state[symbol] = inst
                self._log_transition(symbol, con_id, "new", "discovered", f"auto_created_for:{reason}")

        from_state = inst.state

        # Validate transition
        allowed = ALLOWED_TRANSITIONS.get(from_state, set())
        if to_state not in allowed and from_state != to_state:
            log.warning(
                "Invalid transition %s → %s for %s (allowed: %s)",
                from_state, to_state, symbol, allowed,
            )
            return False

        if from_state == to_state:
            # Same state — update metadata only
            inst.last_seen_run = datetime.now(timezone.utc).isoformat()
            if con_id:
                inst.con_id = con_id
            if exchange:
                inst.exchange = exchange
            return True

        # Execute transition
        old_state = inst.state
        inst.state = to_state
        inst.last_transition = datetime.now(timezone.utc).isoformat()
        inst.reason_code = reason
        if con_id:
            inst.con_id = con_id
        if exchange:
            inst.exchange = exchange

        # Special state handling
        if to_state == "sunset":
            inst.sunset_since = inst.last_transition
            inst.sunset_reason = reason
        elif to_state == "inactive" and old_state == "sunset":
            inst.position_confirmed_flat_at = inst.last_transition
            inst.sunset_since = ""
            inst.sunset_reason = ""
        elif to_state == "quarantined":
            inst.quarantined_at = inst.last_transition
        elif to_state in ("admissible", "ranked", "shortlisted", "live_active"):
            inst.quarantined_at = ""
            inst.resolution_failures = 0
            inst.sunset_since = ""
            inst.sunset_reason = ""

        self._log_transition(symbol, inst.con_id, old_state, to_state, reason)
        return True

    # ── Batch Updates ────────────────────────────────────────────────────

    def update_from_run(
        self,
        discovered: List[Dict[str, Any]],
        admissible: List[Dict[str, Any]],
        resolved: List[Dict[str, Any]],
        ranked: List[Dict[str, Any]],
        shortlisted: List[Dict[str, Any]],
        live_100: List[Dict[str, Any]],
        run_id: str = "",
    ) -> List[Dict[str, Any]]:
        """Update lifecycle state from a complete run. Returns transitions."""
        self._transitions = []
        now = datetime.now(timezone.utc).isoformat()

        # Extract symbol sets for quick lookup
        discovered_syms = {d["symbol"] for d in discovered}
        admissible_syms = {d["symbol"] for d in admissible}
        resolved_syms = {d["symbol"] for d in resolved}
        ranked_syms = {d["symbol"] for d in ranked}
        shortlisted_syms = {d["symbol"] for d in shortlisted}
        live_syms = {d["symbol"] for d in live_100}

        # Build con_id + exchange lookup
        sym_meta = {}
        for lst in (discovered, admissible, resolved, ranked, shortlisted, live_100):
            for d in lst:
                sym_meta[d["symbol"]] = (d.get("con_id", 0), d.get("exchange", ""))

        # Process all symbols seen this run
        all_syms = discovered_syms | admissible_syms | resolved_syms | ranked_syms | shortlisted_syms | live_syms
        for sym in all_syms:
            cid, exch = sym_meta.get(sym, (0, ""))

            if sym not in self._state:
                self.transition(sym, "discovered", "run_discovery", con_id=cid, exchange=exch)

            # Promote through states based on membership
            if sym in live_syms:
                target = "live_active"
            elif sym in shortlisted_syms:
                target = "shortlisted"
            elif sym in ranked_syms:
                target = "ranked"
            elif sym in admissible_syms:
                target = "admissible"
            elif sym in resolved_syms:
                target = "resolved"
            else:
                target = "discovered"

            inst = self._state[sym]

            # Skip if already at or beyond target (avoid downgrade within a run)
            state_order = {
                "discovered": 0, "resolved": 1, "admissible": 2, "ranked": 3,
                "shortlisted": 4, "live_active": 5, "sunset": 4,
                "inactive": 3, "quarantined": -1, "retired": -2,
            }
            current_rank = state_order.get(inst.state, -1)
            target_rank = state_order.get(target, -1)

            if target_rank > current_rank:
                # Need intermediate transitions
                path = self._compute_path(inst.state, target)
                for step in path:
                    self.transition(sym, step, f"run_promotion:{run_id}", con_id=cid, exchange=exch)
            elif target_rank < current_rank and inst.state == "live_active" and target == "shortlisted":
                self.transition(sym, "shortlisted", f"rotation_demotion:{run_id}", con_id=cid, exchange=exch)

            inst.last_seen_run = run_id or now

            # Track ranking misses for inactive instruments
            if sym in self._state and self._state[sym].state in ("inactive", "ranked"):
                if sym not in ranked_syms:
                    self._state[sym].ranking_miss_runs += 1
                else:
                    self._state[sym].ranking_miss_runs = 0

        # Mark instruments not seen this run — live_active goes to sunset, not inactive
        for sym, inst in self._state.items():
            if sym not in all_syms:
                if inst.state == "live_active":
                    self.transition(sym, "sunset", f"evicted_from_live:{run_id}")
                elif inst.state == "shortlisted":
                    self.transition(sym, "inactive", f"not_in_run:{run_id}")

        return self._transitions

    def _compute_path(self, from_state: str, to_state: str) -> List[str]:
        """Compute valid transition path between states."""
        order = ["discovered", "resolved", "admissible", "ranked", "shortlisted", "live_active"]
        try:
            fi = order.index(from_state)
            ti = order.index(to_state)
        except ValueError:
            return [to_state]
        if ti <= fi:
            return [to_state]
        return order[fi + 1 : ti + 1]

    # ── Quarantine Rules ─────────────────────────────────────────────────

    def apply_quarantine_rules(self) -> List[Dict[str, Any]]:
        """Apply deterministic quarantine rules. Returns NEW transitions (appends, does not reset)."""
        quarantine_transitions: List[Dict[str, Any]] = []
        pre_count = len(self._transitions)
        for sym, inst in list(self._state.items()):
            if inst.state in ("quarantined", "retired", "sunset"):
                continue  # sunset instruments are protected until position flat

            # Rule 1: repeated resolution failures
            if inst.resolution_failures >= QUARANTINE_RESOLUTION_FAILURES:
                self.transition(
                    sym, "quarantined",
                    QuarantineReason.RESOLUTION_FAILED_REPEATED,
                    con_id=inst.con_id, exchange=inst.exchange,
                )
                continue

            # Rule 2: extended ranking irrelevance
            if (
                inst.state in ("inactive", "ranked", "admissible")
                and inst.ranking_miss_runs >= QUARANTINE_RANKING_IRRELEVANT_RUNS
            ):
                self.transition(
                    sym, "quarantined",
                    QuarantineReason.RANKING_IRRELEVANT_EXTENDED,
                    con_id=inst.con_id, exchange=inst.exchange,
                )
                continue

            # Rule 3: extended zero liquidity
            if inst.zero_liquidity_days >= QUARANTINE_ZERO_LIQUIDITY_DAYS:
                self.transition(
                    sym, "quarantined",
                    QuarantineReason.ZERO_LIQUIDITY_EXTENDED,
                    con_id=inst.con_id, exchange=inst.exchange,
                )

        return self._transitions[pre_count:]

    # ── Retirement Rules ─────────────────────────────────────────────────

    def apply_retirement_rules(self) -> List[Dict[str, Any]]:
        """Apply deterministic retirement rules. Returns NEW transitions (appends, does not reset)."""
        pre_count = len(self._transitions)
        now = datetime.now(timezone.utc)

        for sym, inst in list(self._state.items()):
            if inst.state != "quarantined":
                continue

            # Rule: quarantine duration exceeded
            if inst.quarantined_at:
                try:
                    q_time = datetime.fromisoformat(inst.quarantined_at.replace("Z", "+00:00"))
                    if hasattr(q_time, "tzinfo") and q_time.tzinfo is None:
                        q_time = q_time.replace(tzinfo=timezone.utc)
                    days_quarantined = (now - q_time).days
                    if days_quarantined >= RETIREMENT_QUARANTINE_DAYS:
                        self.transition(
                            sym, "retired",
                            RetireReason.QUARANTINE_EXPIRED,
                            con_id=inst.con_id, exchange=inst.exchange,
                        )
                except (ValueError, TypeError):
                    pass

        return self._transitions[pre_count:]

    # ── Increment Failure Counters ───────────────────────────────────────

    def record_resolution_failure(self, symbol: str):
        """Increment resolution failure counter for a symbol."""
        inst = self._state.get(symbol)
        if inst:
            inst.resolution_failures += 1

    def record_zero_liquidity(self, symbol: str):
        """Increment zero liquidity counter for a symbol."""
        inst = self._state.get(symbol)
        if inst:
            inst.zero_liquidity_days += 1

    # ── Queries ──────────────────────────────────────────────────────────

    def get_state(self, symbol: str) -> Optional[str]:
        """Get current lifecycle state for a symbol."""
        inst = self._state.get(symbol)
        return inst.state if inst else None

    def get_all_in_state(self, state: str) -> List[str]:
        """Get all symbols in a given state."""
        return [sym for sym, inst in self._state.items() if inst.state == state]

    def get_transitions(self) -> List[Dict[str, Any]]:
        """Get transitions from the last operation."""
        return self._transitions

    def count_by_state(self) -> Dict[str, int]:
        """Count instruments by state."""
        counts: Dict[str, int] = {}
        for inst in self._state.values():
            counts[inst.state] = counts.get(inst.state, 0) + 1
        return counts

    @property
    def total(self) -> int:
        return len(self._state)
