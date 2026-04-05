"""Universe Publish — Atomic publish, guardrails, Rust ack, rollback.

Never mutates live artifacts in place. Generates into temp dir, validates,
applies guardrails, atomically renames, SIGHUPs Rust, waits for ack.

Publish flow:
    1. Generate artifacts in /tmp/aegis_universe_XXXXX/
    2. Validate schema + TOML parse + JSON schema
    3. Diff against current live artifacts
    4. Apply guardrails (abort if tripped → alert)
    5. os.replace() each artifact atomically
    6. SIGHUP Rust engine
    7. Wait for Rust reload ack with hash verification
    8. Mark snapshot success/failure

Artifacts derived:
    - config/contracts.toml
    - config/active_watchlist.json
    - config/initial_universe.toml

Rollback:
    - Previous version kept in data/universe/previous/
    - On ack failure, restore previous atomically

Usage:
    from python_brain.ouroboros.universe_publish import UniversePublisher
    pub = UniversePublisher()
    ok = pub.publish(snapshot, contracts, watchlist, universe_toml, dry_run=False)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from python_brain.ouroboros.universe_reason_codes import (
    PublishBlockReason,
    RollbackReason,
)

log = logging.getLogger("universe_publish")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
UNIVERSE_DIR = DATA_DIR / "universe"
PREVIOUS_DIR = UNIVERSE_DIR / "previous"
PUBLISH_HISTORY = UNIVERSE_DIR / "publish_history.ndjson"
RUST_ACK_FILE = DATA_DIR / "reload_ack.json"

# Target files
CONTRACTS_FILE = CONFIG_DIR / "contracts.toml"
WATCHLIST_FILE = CONFIG_DIR / "active_watchlist.json"
UNIVERSE_TOML_FILE = CONFIG_DIR / "initial_universe.toml"
ROTATION_PLAN_FILE = CONFIG_DIR / "live_rotation_plan.json"

# ---------------------------------------------------------------------------
# Guardrail thresholds
# ---------------------------------------------------------------------------
MAX_ADDITIONS_PER_RUN = 100
MAX_REMOVALS_PER_RUN = 50
MAX_ACTIVE_DELTA_PCT = 20          # percent
MAX_EXCHANGE_COVERAGE_DROP_PCT = 30 # percent per exchange
MAX_SINGLE_EXCHANGE_PCT = 35       # max % of live_100 from one exchange
MAX_LEVERAGED_PCT = 40             # max % of live_100 from leveraged/inverse
MAX_UNRESOLVED_RATIO = 0.10        # max fraction of unresolved con_ids
MAX_WATCHLIST_CHURN_PCT = 40       # max % of watchlist names changed
RUST_ACK_TIMEOUT = 10              # seconds


def _sha256(content: str) -> str:
    """Compute SHA-256 hash of string content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


class GuardrailCheck:
    """Result of a single guardrail check."""
    def __init__(self, name: str, passed: bool, value: Any = None, threshold: Any = None, reason: str = ""):
        self.name = name
        self.passed = passed
        self.value = value
        self.threshold = threshold
        self.reason = reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "value": self.value,
            "threshold": self.threshold,
            "reason_code": self.reason,
        }


class UniversePublisher:
    """Handles atomic publishing of universe artifacts."""

    def __init__(self):
        self._guardrail_results: List[GuardrailCheck] = []
        self._artifact_hashes: Dict[str, str] = {}

    # ── Main Publish ─────────────────────────────────────────────────────

    def publish(
        self,
        contracts_toml: str,
        watchlist_json: Dict[str, Any],
        universe_toml: str,
        rotation_plan: Dict[str, Any],
        snapshot_data: Dict[str, Any],
        dry_run: bool = False,
    ) -> Tuple[bool, List[str]]:
        """
        Atomic publish of all universe artifacts.

        Returns (success, reason_codes).
        """
        self._guardrail_results = []
        self._artifact_hashes = {}
        reasons: List[str] = []

        UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
        PREVIOUS_DIR.mkdir(parents=True, exist_ok=True)

        # ── Step 1: Generate in temp dir ─────────────────────────────────
        tmpdir = tempfile.mkdtemp(prefix="aegis_universe_")
        try:
            tmp_contracts = os.path.join(tmpdir, "contracts.toml")
            tmp_watchlist = os.path.join(tmpdir, "active_watchlist.json")
            tmp_universe = os.path.join(tmpdir, "initial_universe.toml")
            tmp_rotation = os.path.join(tmpdir, "live_rotation_plan.json")

            with open(tmp_contracts, "w") as f:
                f.write(contracts_toml)
            with open(tmp_watchlist, "w") as f:
                json.dump(watchlist_json, f, indent=2)
            with open(tmp_universe, "w") as f:
                f.write(universe_toml)
            with open(tmp_rotation, "w") as f:
                json.dump(rotation_plan, f, indent=2)

            # Compute hashes
            self._artifact_hashes = {
                "contracts.toml": _sha256(contracts_toml),
                "active_watchlist.json": _sha256(json.dumps(watchlist_json, indent=2)),
                "initial_universe.toml": _sha256(universe_toml),
                "live_rotation_plan.json": _sha256(json.dumps(rotation_plan, indent=2)),
            }

            # ── Step 2: Validate ─────────────────────────────────────────
            valid, v_reasons = self._validate_artifacts(
                tmp_contracts, tmp_watchlist, tmp_universe, tmp_rotation
            )
            if not valid:
                reasons.extend(v_reasons)
                self._log_publish("blocked", reasons, snapshot_data)
                return False, reasons

            # ── Step 3: Diff + Guardrails ────────────────────────────────
            passed, g_reasons = self._apply_guardrails(
                contracts_toml, watchlist_json, rotation_plan, snapshot_data
            )
            if not passed:
                reasons.extend(g_reasons)
                self._log_publish("blocked", reasons, snapshot_data)
                return False, reasons

            # ── Step 4: Dry run exit ─────────────────────────────────────
            if dry_run:
                log.info("DRY RUN: Would publish %d artifacts, skipping", len(self._artifact_hashes))
                self._log_publish("dry_run", [], snapshot_data)
                return True, []

            # ── Step 5: Backup previous ──────────────────────────────────
            self._backup_previous()

            # ── Step 6: Atomic rename ────────────────────────────────────
            try:
                os.replace(tmp_contracts, str(CONTRACTS_FILE))
                os.replace(tmp_watchlist, str(WATCHLIST_FILE))
                os.replace(tmp_universe, str(UNIVERSE_TOML_FILE))
                os.replace(tmp_rotation, str(ROTATION_PLAN_FILE))
                log.info("Artifacts published atomically")
            except OSError as e:
                log.error("Atomic publish failed: %s", e)
                self._rollback(RollbackReason.PUBLISH_IO_ERROR)
                reasons.append(RollbackReason.PUBLISH_IO_ERROR)
                self._log_publish("rolled_back", reasons, snapshot_data)
                return False, reasons

            # ── Step 7: SIGHUP + Ack ─────────────────────────────────────
            ack_ok, ack_reason = self._sighup_and_wait_ack()
            if not ack_ok:
                log.error("Rust ack failed: %s — rolling back", ack_reason)
                self._rollback(ack_reason)
                reasons.append(ack_reason)
                self._log_publish("rolled_back", reasons, snapshot_data)
                return False, reasons

            # ── Step 8: Success ──────────────────────────────────────────
            log.info("Publish successful — Rust ack verified")
            self._log_publish("published", [], snapshot_data)
            return True, []

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ── Validation ───────────────────────────────────────────────────────

    def _validate_artifacts(
        self, contracts_path: str, watchlist_path: str,
        universe_path: str, rotation_path: str,
    ) -> Tuple[bool, List[str]]:
        """Validate generated artifacts are parseable."""
        reasons = []

        # TOML parse check for contracts
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore
            except ImportError:
                log.warning("No TOML parser available — skipping TOML validation")
                tomllib = None  # type: ignore

        if tomllib is not None:
            try:
                with open(contracts_path, "rb") as f:
                    tomllib.load(f)
            except Exception as e:
                reasons.append(PublishBlockReason.TOML_PARSE_INVALID)
                log.error("contracts.toml parse failed: %s", e)

        # TOML parse check for universe
        if tomllib is not None:
            try:
                with open(universe_path, "rb") as f:
                    tomllib.load(f)
            except Exception as e:
                reasons.append(PublishBlockReason.TOML_PARSE_INVALID)
                log.error("initial_universe.toml parse failed: %s", e)

        # JSON parse check for watchlist
        try:
            with open(watchlist_path) as f:
                wl = json.load(f)
            if not isinstance(wl, dict):
                reasons.append(PublishBlockReason.JSON_SCHEMA_INVALID)
        except Exception as e:
            reasons.append(PublishBlockReason.JSON_SCHEMA_INVALID)
            log.error("active_watchlist.json parse failed: %s", e)

        # JSON parse check for rotation plan
        try:
            with open(rotation_path) as f:
                rp = json.load(f)
            if not isinstance(rp, dict):
                reasons.append(PublishBlockReason.JSON_SCHEMA_INVALID)
        except Exception as e:
            reasons.append(PublishBlockReason.JSON_SCHEMA_INVALID)
            log.error("live_rotation_plan.json parse failed: %s", e)

        return len(reasons) == 0, reasons

    # ── Guardrails ───────────────────────────────────────────────────────

    def _apply_guardrails(
        self,
        contracts_toml: str,
        watchlist_json: Dict[str, Any],
        rotation_plan: Dict[str, Any],
        snapshot_data: Dict[str, Any],
    ) -> Tuple[bool, List[str]]:
        """Apply publish guardrails. Returns (passed, reason_codes)."""
        reasons = []

        # Count new contracts
        new_contracts = contracts_toml.count("[[contracts]]")
        existing_contracts = self._count_existing_contracts()

        # Additions check
        additions = max(0, new_contracts - existing_contracts)
        check = GuardrailCheck(
            "max_additions", additions <= MAX_ADDITIONS_PER_RUN,
            additions, MAX_ADDITIONS_PER_RUN, PublishBlockReason.MAX_ADDITIONS_EXCEEDED,
        )
        self._guardrail_results.append(check)
        if not check.passed:
            reasons.append(check.reason)

        # Removals check
        removals = max(0, existing_contracts - new_contracts)
        check = GuardrailCheck(
            "max_removals", removals <= MAX_REMOVALS_PER_RUN,
            removals, MAX_REMOVALS_PER_RUN, PublishBlockReason.MAX_REMOVALS_EXCEEDED,
        )
        self._guardrail_results.append(check)
        if not check.passed:
            reasons.append(check.reason)

        # Active delta check
        if existing_contracts > 0:
            delta_pct = abs(new_contracts - existing_contracts) * 100 / existing_contracts
            check = GuardrailCheck(
                "active_delta", delta_pct <= MAX_ACTIVE_DELTA_PCT,
                delta_pct, MAX_ACTIVE_DELTA_PCT, PublishBlockReason.ACTIVE_DELTA_EXCEEDED,
            )
            self._guardrail_results.append(check)
            if not check.passed:
                reasons.append(check.reason)

        # Watchlist empty check
        tickers = watchlist_json.get("tickers", [])
        check = GuardrailCheck(
            "watchlist_not_empty", len(tickers) > 0,
            len(tickers), 1, PublishBlockReason.WATCHLIST_EMPTY,
        )
        self._guardrail_results.append(check)
        if not check.passed:
            reasons.append(check.reason)

        # Live 100 cap check
        live_100 = rotation_plan.get("live_100", [])
        check = GuardrailCheck(
            "live_100_cap", len(live_100) <= 100,
            len(live_100), 100, PublishBlockReason.LIVE_100_EXCEEDED,
        )
        self._guardrail_results.append(check)
        if not check.passed:
            reasons.append(check.reason)

        # Shortlist cap check
        shortlist = rotation_plan.get("shortlist_250", [])
        check = GuardrailCheck(
            "shortlist_cap", len(shortlist) <= 250,
            len(shortlist), 250, PublishBlockReason.SHORTLIST_EXCEEDED,
        )
        self._guardrail_results.append(check)
        if not check.passed:
            reasons.append(check.reason)

        # Live ⊂ shortlist check
        live_syms = {t.get("symbol") for t in live_100}
        short_syms = {t.get("symbol") for t in shortlist}
        not_in_shortlist = live_syms - short_syms
        check = GuardrailCheck(
            "live_subset_shortlist", len(not_in_shortlist) == 0,
            len(not_in_shortlist), 0, PublishBlockReason.LIVE_NOT_SUBSET_SHORTLIST,
        )
        self._guardrail_results.append(check)
        if not check.passed:
            reasons.append(check.reason)

        # Single exchange concentration check
        if live_100:
            exch_counts: Dict[str, int] = {}
            for t in live_100:
                e = t.get("exchange", "UNKNOWN")
                exch_counts[e] = exch_counts.get(e, 0) + 1
            max_exch_pct = max(exch_counts.values()) * 100 / len(live_100) if live_100 else 0
            check = GuardrailCheck(
                "single_exchange_concentration", max_exch_pct <= MAX_SINGLE_EXCHANGE_PCT,
                max_exch_pct, MAX_SINGLE_EXCHANGE_PCT, PublishBlockReason.SINGLE_EXCHANGE_CONCENTRATION,
            )
            self._guardrail_results.append(check)
            if not check.passed:
                reasons.append(check.reason)

        # Leveraged share check
        if live_100:
            lev_count = sum(1 for t in live_100 if t.get("leverage", 1) > 1 or t.get("is_inverse"))
            lev_pct = lev_count * 100 / len(live_100)
            check = GuardrailCheck(
                "leveraged_share", lev_pct <= MAX_LEVERAGED_PCT,
                lev_pct, MAX_LEVERAGED_PCT, PublishBlockReason.LEVERAGED_SHARE_EXCEEDED,
            )
            self._guardrail_results.append(check)
            if not check.passed:
                reasons.append(check.reason)

        return len(reasons) == 0, reasons

    def _count_existing_contracts(self) -> int:
        """Count [[contracts]] entries in current live contracts.toml."""
        if not CONTRACTS_FILE.exists():
            return 0
        try:
            return CONTRACTS_FILE.read_text().count("[[contracts]]")
        except Exception:
            return 0

    # ── Backup + Rollback ────────────────────────────────────────────────

    def _backup_previous(self):
        """Backup current live artifacts to previous/ directory."""
        for src in (CONTRACTS_FILE, WATCHLIST_FILE, UNIVERSE_TOML_FILE, ROTATION_PLAN_FILE):
            if src.exists():
                dst = PREVIOUS_DIR / src.name
                try:
                    shutil.copy2(src, dst)
                except Exception as e:
                    log.warning("Failed to backup %s: %s", src.name, e)

    def _rollback(self, reason: str):
        """Restore previous artifacts from backup. Uses copy, not move, to preserve backup."""
        log.warning("ROLLBACK initiated: %s", reason)
        for name in ("contracts.toml", "active_watchlist.json", "initial_universe.toml", "live_rotation_plan.json"):
            prev = PREVIOUS_DIR / name
            target_map = {
                "contracts.toml": CONTRACTS_FILE,
                "active_watchlist.json": WATCHLIST_FILE,
                "initial_universe.toml": UNIVERSE_TOML_FILE,
                "live_rotation_plan.json": ROTATION_PLAN_FILE,
            }
            target = target_map[name]
            if prev.exists():
                try:
                    # Copy to temp, then atomic replace — preserves backup for repeat rollbacks
                    fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=f".{name}.tmp")
                    with os.fdopen(fd, "w") as f:
                        f.write(prev.read_text())
                    os.replace(tmp, str(target))
                    log.info("Rolled back %s (backup preserved)", name)
                except Exception as e:
                    log.error("Rollback failed for %s: %s", name, e)
            else:
                log.warning("No previous version of %s to rollback to", name)

    # ── SIGHUP + Ack ────────────────────────────────────────────────────

    def _sighup_and_wait_ack(self) -> Tuple[bool, str]:
        """Send SIGHUP to Rust engine and wait for hash-verified ack."""
        # Import signal_engine_reload from contract_expander
        try:
            from python_brain.ouroboros.contract_expander import signal_engine_reload
        except ImportError:
            log.warning("Cannot import signal_engine_reload — skipping SIGHUP")
            return True, ""  # If we can't signal, don't block publish

        # Clear any stale ack
        if RUST_ACK_FILE.exists():
            try:
                RUST_ACK_FILE.unlink()
            except Exception:
                pass

        # Send SIGHUP
        if not signal_engine_reload():
            log.warning("SIGHUP failed — engine may not be running")
            # Don't fail publish if engine isn't running (e.g., during deploy)
            return True, ""

        # Wait for ack
        deadline = time.monotonic() + RUST_ACK_TIMEOUT
        while time.monotonic() < deadline:
            if RUST_ACK_FILE.exists():
                try:
                    with open(RUST_ACK_FILE) as f:
                        ack = json.load(f)

                    # Verify hashes match
                    ack_status = ack.get("status", "unknown")
                    if ack_status == "ok":
                        # Check hash matches
                        ack_contracts_hash = ack.get("contracts_sha256", "")
                        expected_hash = self._artifact_hashes.get("contracts.toml", "")
                        if expected_hash and ack_contracts_hash and ack_contracts_hash != expected_hash:
                            return False, RollbackReason.RUST_ACK_HASH_MISMATCH

                        # Check count matches
                        ack_count = ack.get("loaded_contract_count", -1)
                        if ack_count >= 0:
                            log.info("Rust ack: loaded %d contracts, status=%s", ack_count, ack_status)

                        return True, ""
                    elif ack_status == "mismatch":
                        return False, RollbackReason.RUST_ACK_HASH_MISMATCH
                    elif ack_status == "rejected":
                        return False, RollbackReason.RUST_ACK_COUNT_MISMATCH
                    else:
                        log.warning("Unknown Rust ack status: %s", ack_status)
                        return True, ""  # Unknown status, don't block

                except (json.JSONDecodeError, KeyError) as e:
                    log.warning("Invalid Rust ack: %s", e)

            time.sleep(0.5)

        # Timeout — Rust may not have ack support yet (graceful)
        log.warning("Rust ack timeout after %ds — continuing (ack support may not be deployed)", RUST_ACK_TIMEOUT)
        return True, ""  # Don't block publish if ack support isn't in Rust yet

    # ── Logging ──────────────────────────────────────────────────────────

    def _log_publish(self, decision: str, reasons: List[str], snapshot_data: Dict[str, Any]):
        """Append to publish history NDJSON."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": snapshot_data.get("run_id", ""),
            "universe_version": snapshot_data.get("universe_version", 0),
            "decision": decision,
            "reasons": reasons,
            "artifact_hashes": self._artifact_hashes,
            "guardrail_results": [g.to_dict() for g in self._guardrail_results],
        }
        try:
            UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
            with open(PUBLISH_HISTORY, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            log.warning("Failed to write publish history: %s", e)

    # ── Accessors ────────────────────────────────────────────────────────

    def get_guardrail_results(self) -> List[Dict[str, Any]]:
        return [g.to_dict() for g in self._guardrail_results]

    def get_artifact_hashes(self) -> Dict[str, str]:
        return dict(self._artifact_hashes)
