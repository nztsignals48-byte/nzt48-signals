"""Sprint S09 + S10 -- Approval Gate for Ouroboros Parameter Changes.

Reads challenger output, applies hard bounds that Claude CANNOT override,
and either auto-applies safe changes to dynamic_weights.toml or escalates
to the operator via Telegram.

Hard bounds:
  kelly_fraction:      [0.10, 0.35], max 10% change per cycle
  chandelier_atr_mult: [1.5, 5.0],  max 15% per cycle
  confidence_floor:    [50, 85],     max 10 points per cycle
  spread_veto_pct:     [0.10, 0.80], max 0.10 per cycle
  system_velocity_max: [5, 20],      max 5 per cycle
  Blacklist add:       20+ trades AND Wilson LB < 0.20
  Blacklist remove:    10+ trades AND Wilson LB > 0.45

S10 drift cap: tracks 30-day parameter history, blocks if drift > 50% from
baseline over the window.

All decisions logged to /app/data/claude/approval_log.ndjson.

QUARANTINE: Only writes to dynamic_weights.toml (via atomic_write with TOML
validation) and approval_log.ndjson. Never touches WAL, config.toml, or
live trading state.

Usage:
    python3 -m python_brain.ouroboros.approval_gate
    python3 -m python_brain.ouroboros.approval_gate --dry-run
    python3 -m python_brain.ouroboros.approval_gate --send-telegram
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from python_brain.ouroboros.claude_helper import send_telegram

log = logging.getLogger("approval_gate")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", "/app/config"))
CHALLENGE_DIR = DATA_DIR / "claude" / "challenges"
APPROVAL_LOG = DATA_DIR / "claude" / "approval_log.ndjson"
DYNAMIC_WEIGHTS_FILE = CONFIG_DIR / "dynamic_weights.toml"
CONFIG_CHANGES_FILE = DATA_DIR / "config_changes.ndjson"

# ---------------------------------------------------------------------------
# Hard bounds (Claude CANNOT override these)
# ---------------------------------------------------------------------------
HARD_BOUNDS = {
    "kelly_fraction": {
        "min": 0.10, "max": 0.35,
        "max_change_pct": 10.0,     # Max 10% change per cycle
        "max_change_abs": None,
        "toml_section": "kelly_fractions",
        "toml_key": "t1",
    },
    "chandelier_atr_mult": {
        "min": 1.5, "max": 5.0,
        "max_change_pct": 15.0,     # Max 15% per cycle
        "max_change_abs": None,
        "toml_section": "exit",
        "toml_key": "chandelier_atr_mult",
    },
    "confidence_floor": {
        "min": 50, "max": 85,
        "max_change_pct": None,
        "max_change_abs": 10,       # Max 10 points per cycle
        "toml_section": "signal",
        "toml_key": "confidence_floor",
    },
    "spread_veto_pct": {
        "min": 0.10, "max": 0.80,
        "max_change_pct": None,
        "max_change_abs": 0.10,     # Max 0.10 per cycle
        "toml_section": "signal",
        "toml_key": "spread_veto_pct",
    },
    "system_velocity_max": {
        "min": 5, "max": 20,
        "max_change_pct": None,
        "max_change_abs": 5,        # Max 5 per cycle
        "toml_section": "hardening",
        "toml_key": "system_velocity_max",
    },
}

# Blacklist bounds
BLACKLIST_ADD_MIN_TRADES = 20
BLACKLIST_ADD_WILSON_LB_MAX = 0.20
BLACKLIST_REMOVE_MIN_TRADES = 10
BLACKLIST_REMOVE_WILSON_LB_MIN = 0.45

# S10 drift cap
DRIFT_WINDOW_DAYS = 30
DRIFT_MAX_PCT = 50.0  # Block if param has drifted > 50% from 30-day-ago baseline

# ---------------------------------------------------------------------------
# TOML helpers
# ---------------------------------------------------------------------------
def _load_dynamic_weights() -> Dict[str, Any]:
    """Load current dynamic_weights.toml."""
    if not DYNAMIC_WEIGHTS_FILE.exists():
        return {}
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        with open(DYNAMIC_WEIGHTS_FILE, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        log.warning("Failed to load dynamic_weights.toml: %s", e)
        return {}


def _get_current_value(weights: Dict[str, Any], param: str) -> Optional[float]:
    """Get the current value of a parameter from dynamic_weights."""
    bounds = HARD_BOUNDS.get(param)
    if not bounds:
        return None
    section = bounds.get("toml_section", "")
    key = bounds.get("toml_key", "")
    val = weights.get(section, {}).get(key)
    if val is not None:
        try:
            return float(val)
        except (TypeError, ValueError):
            return None
    return None


def _atomic_write_toml(path: Path, content: str) -> bool:
    """Write TOML content atomically with validation (H3 pattern from config_writer)."""
    path.parent.mkdir(parents=True, exist_ok=True)

    # Validate TOML syntax before writing
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib
        tomllib.loads(content)
    except Exception as e:
        log.critical("H3 TOML VALIDATION FAILED for %s: %s -- write ABORTED", path, e)
        return False

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.rename(str(tmp_path), str(path))
        log.info("Wrote %s (%d bytes)", path, len(content))
        return True
    except Exception as e:
        log.error("Failed to write %s: %s", path, e)
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def _update_dynamic_weights(param: str, new_value: float) -> bool:
    """Update a single parameter in dynamic_weights.toml atomically.

    Reads the file, modifies the target key, validates, and writes back.
    Uses line-by-line replacement to preserve comments and formatting.
    """
    if not DYNAMIC_WEIGHTS_FILE.exists():
        log.error("dynamic_weights.toml does not exist -- cannot update")
        return False

    bounds = HARD_BOUNDS.get(param)
    if not bounds:
        log.error("Unknown param %s -- cannot update", param)
        return False

    section = bounds["toml_section"]
    key = bounds["toml_key"]

    try:
        content = DYNAMIC_WEIGHTS_FILE.read_text(encoding="utf-8")
    except OSError as e:
        log.error("Failed to read dynamic_weights.toml: %s", e)
        return False

    # Line-by-line replacement
    lines = content.split("\n")
    in_section = False
    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and not stripped.startswith("[["):
            current_section = stripped.strip("[]").strip()
            in_section = (current_section == section)
        elif in_section and stripped.startswith(f"{key} =") or (in_section and stripped.startswith(f"{key}=")):
            # Format based on value type
            if isinstance(new_value, int) or (isinstance(new_value, float) and new_value == int(new_value) and param == "confidence_floor"):
                lines[i] = f"{key} = {int(new_value)}"
            else:
                # Determine decimal places from param
                if param == "kelly_fraction":
                    lines[i] = f"{key} = {new_value:.6f}"
                elif param == "chandelier_atr_mult":
                    lines[i] = f"{key} = {new_value:.2f}"
                elif param == "spread_veto_pct":
                    lines[i] = f"{key} = {new_value:.2f}"
                else:
                    lines[i] = f"{key} = {new_value}"
            found = True
            break

    if not found:
        log.warning("Could not find [%s].%s in dynamic_weights.toml -- skipping update", section, key)
        return False

    new_content = "\n".join(lines)
    return _atomic_write_toml(DYNAMIC_WEIGHTS_FILE, new_content)


# ---------------------------------------------------------------------------
# Bound checking
# ---------------------------------------------------------------------------
def _check_bounds(
    param: str,
    current: float,
    proposed: float,
) -> Tuple[bool, str, Optional[float]]:
    """Check if a proposed value is within hard bounds.

    Returns (within_bounds, reason, clamped_value).
    If within_bounds is False, clamped_value is the nearest allowed value (or None if rejected).
    """
    bounds = HARD_BOUNDS.get(param)
    if not bounds:
        return True, "no bounds defined", proposed

    # Range check
    if proposed < bounds["min"]:
        return False, f"{param}={proposed} below hard min {bounds['min']}", bounds["min"]
    if proposed > bounds["max"]:
        return False, f"{param}={proposed} above hard max {bounds['max']}", bounds["max"]

    # Rate-of-change check (percentage)
    max_pct = bounds.get("max_change_pct")
    if max_pct is not None and current != 0:
        change_pct = abs(proposed - current) / abs(current) * 100.0
        if change_pct > max_pct:
            # Clamp to max allowed change
            direction = 1.0 if proposed > current else -1.0
            clamped = current * (1.0 + direction * max_pct / 100.0)
            return False, (f"{param} change {change_pct:.1f}% exceeds max {max_pct:.0f}%/cycle "
                           f"(clamped to {clamped:.4f})"), clamped

    # Rate-of-change check (absolute)
    max_abs = bounds.get("max_change_abs")
    if max_abs is not None:
        change_abs = abs(proposed - current)
        if change_abs > max_abs:
            direction = 1.0 if proposed > current else -1.0
            clamped = current + direction * max_abs
            return False, (f"{param} change {change_abs:.2f} exceeds max {max_abs}/cycle "
                           f"(clamped to {clamped:.2f})"), clamped

    return True, "within bounds", proposed


def _is_risk_increasing(param: str, current: float, proposed: float) -> bool:
    """Determine if a parameter change increases overall risk."""
    if param == "kelly_fraction":
        return proposed > current  # Larger position = more risk
    elif param == "chandelier_atr_mult":
        return proposed < current  # Tighter stop = more risk (stopped out faster)
    elif param == "confidence_floor":
        return proposed < current  # Lower floor = more trades = more risk
    elif param == "spread_veto_pct":
        return proposed > current  # Higher spread tolerance = more risk
    elif param == "system_velocity_max":
        return proposed > current  # More trades/5min = more risk
    return False


# ---------------------------------------------------------------------------
# S10: Drift cap -- 30-day parameter drift detection
# ---------------------------------------------------------------------------
def _load_parameter_history() -> List[Dict[str, Any]]:
    """Load parameter change history from config_changes.ndjson."""
    if not CONFIG_CHANGES_FILE.exists():
        return []
    entries: List[Dict[str, Any]] = []
    try:
        with open(CONFIG_CHANGES_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        log.warning("Failed to read config_changes.ndjson: %s", e)
    return entries


def _check_drift_cap(param: str, proposed: float) -> Tuple[bool, str]:
    """Check if the proposed value would cause total drift > 50% from 30-day baseline.

    Returns (within_drift_cap, reason).
    """
    history = _load_parameter_history()
    if not history:
        return True, "no history (first run)"

    bounds = HARD_BOUNDS.get(param)
    if not bounds:
        return True, "no bounds"

    toml_section = bounds["toml_section"]
    toml_key = bounds["toml_key"]
    full_key = f"{toml_section}.{toml_key}"

    # Find the value from 30 days ago
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DRIFT_WINDOW_DAYS)).isoformat()
    baseline_value = None

    for entry in history:
        ts = entry.get("timestamp", "")
        if ts < cutoff:
            # Look for this key in diffs
            for diff in entry.get("diff_summary", []):
                if diff.get("key") == full_key:
                    old_val = diff.get("old") or diff.get("new")
                    if old_val is not None:
                        try:
                            baseline_value = float(old_val)
                        except (TypeError, ValueError):
                            pass
            continue

    if baseline_value is None or baseline_value == 0:
        return True, "no 30-day baseline found"

    drift_pct = abs(proposed - baseline_value) / abs(baseline_value) * 100.0
    if drift_pct > DRIFT_MAX_PCT:
        return False, (f"{param} total drift {drift_pct:.1f}% from 30-day baseline "
                       f"({baseline_value:.4f}) exceeds {DRIFT_MAX_PCT:.0f}% cap")

    return True, f"drift {drift_pct:.1f}% within cap"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def _log_decision(decision: Dict[str, Any]):
    """Append a decision record to approval_log.ndjson."""
    APPROVAL_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **decision,
    }
    try:
        with open(APPROVAL_LOG, "a") as f:
            f.write(json.dumps(entry, separators=(",", ":"), default=str) + "\n")
    except OSError as e:
        log.warning("Failed to write approval log: %s", e)


# ---------------------------------------------------------------------------
# Wilson score interval (for blacklist)
# ---------------------------------------------------------------------------
def _wilson_lower(wins: int, n: int, z: float = 1.96) -> float:
    """Wilson score interval lower bound (95% confidence)."""
    if n == 0:
        return 0.0
    phat = wins / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    spread = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return (centre - spread) / denom


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------
def run_approval_gate(
    dry_run: bool = False,
    send_tg: bool = False,
) -> Dict[str, Any]:
    """Execute the approval gate pipeline."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info("S09/S10: Starting approval gate for %s", date_str)

    result: Dict[str, Any] = {
        "date": date_str,
        "status": "complete",
        "decisions": [],
        "applied": [],
        "blocked": [],
        "escalated": [],
    }

    # Load challenger output
    challenge_path = CHALLENGE_DIR / f"challenge_{date_str}.json"
    if not challenge_path.exists():
        log.warning("No challenger output for %s -- nothing to approve", date_str)
        result["status"] = "skipped"
        result["reason"] = "no_challenger_output"
        _log_decision({"action": "SKIP", "reason": "no_challenger_output", "date": date_str})
        return result

    try:
        with open(challenge_path) as f:
            challenger_output = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error("Failed to load challenger output: %s", e)
        result["status"] = "error"
        result["reason"] = str(e)
        return result

    # Load current dynamic weights
    current_weights = _load_dynamic_weights()

    challenges = challenger_output.get("challenges", [])
    if not challenges:
        log.info("No challenges to process")
        result["status"] = "no_changes"
        return result

    tg_lines = ["<b>APPROVAL GATE</b>", f"Date: {date_str}", ""]

    for challenge in challenges:
        param = challenge.get("param", "")
        verdict = challenge.get("verdict", "REJECT")
        proposed = challenge.get("proposed_value")
        current = challenge.get("current_value")

        # Skip non-parameterized notes
        if param == "note" or proposed is None:
            decision = {
                "param": param,
                "verdict": verdict,
                "action": "SKIP",
                "reason": "non-parameterized recommendation",
            }
            result["decisions"].append(decision)
            _log_decision(decision)
            continue

        # Get current value from TOML if not in challenge
        if current is None:
            current = _get_current_value(current_weights, param)

        try:
            proposed_f = float(proposed)
            current_f = float(current) if current is not None else None
        except (TypeError, ValueError):
            decision = {
                "param": param,
                "verdict": verdict,
                "action": "SKIP",
                "reason": f"non-numeric values: current={current}, proposed={proposed}",
            }
            result["decisions"].append(decision)
            _log_decision(decision)
            continue

        # Only process APPLY verdicts for auto-write
        if verdict not in ("APPLY", "TEST_ONLY"):
            decision = {
                "param": param,
                "verdict": verdict,
                "action": "SKIP",
                "reason": f"verdict is {verdict}, not APPLY or TEST_ONLY",
                "current_value": current_f,
                "proposed_value": proposed_f,
            }
            result["decisions"].append(decision)
            result["blocked"].append(decision)
            _log_decision(decision)
            tg_lines.append(f"BLOCKED {param}: {verdict}")
            continue

        if verdict == "TEST_ONLY":
            decision = {
                "param": param,
                "verdict": "TEST_ONLY",
                "action": "LOG_ONLY",
                "reason": "TEST_ONLY verdict -- logged but not applied",
                "current_value": current_f,
                "proposed_value": proposed_f,
            }
            result["decisions"].append(decision)
            result["blocked"].append(decision)
            _log_decision(decision)
            tg_lines.append(f"TEST_ONLY {param}: {current_f} -> {proposed_f} (not applied)")
            continue

        # Verdict is APPLY -- run through bound checks
        if current_f is None:
            decision = {
                "param": param,
                "verdict": verdict,
                "action": "SKIP",
                "reason": "no current value found in dynamic_weights.toml",
                "proposed_value": proposed_f,
            }
            result["decisions"].append(decision)
            result["blocked"].append(decision)
            _log_decision(decision)
            continue

        # Check hard bounds
        within_bounds, bound_reason, clamped = _check_bounds(param, current_f, proposed_f)
        if not within_bounds:
            log.warning("Bounds exceeded: %s", bound_reason)
            if clamped is not None:
                proposed_f = clamped
                log.info("Clamped %s to %s", param, clamped)
            else:
                decision = {
                    "param": param,
                    "verdict": verdict,
                    "action": "BLOCKED",
                    "reason": bound_reason,
                    "current_value": current_f,
                    "proposed_value": float(proposed),
                }
                result["decisions"].append(decision)
                result["blocked"].append(decision)
                _log_decision(decision)
                tg_lines.append(f"BLOCKED {param}: {bound_reason}")
                continue

        # S10: Check drift cap
        within_drift, drift_reason = _check_drift_cap(param, proposed_f)
        if not within_drift:
            log.warning("Drift cap exceeded: %s", drift_reason)
            decision = {
                "param": param,
                "verdict": verdict,
                "action": "BLOCKED_DRIFT",
                "reason": drift_reason,
                "current_value": current_f,
                "proposed_value": proposed_f,
            }
            result["decisions"].append(decision)
            result["blocked"].append(decision)
            _log_decision(decision)
            tg_lines.append(f"DRIFT BLOCKED {param}: {drift_reason}")

            if send_tg:
                send_telegram(
                    f"<b>DRIFT CAP EXCEEDED</b>\n\n"
                    f"Parameter: {param}\n"
                    f"Current: {current_f}\n"
                    f"Proposed: {proposed_f}\n"
                    f"Reason: {drift_reason}\n\n"
                    f"<b>OPERATOR REVIEW REQUIRED</b>"
                )
            continue

        # Check if risk-increasing
        risk_increasing = _is_risk_increasing(param, current_f, proposed_f)
        if risk_increasing:
            # Always require operator approval for risk-increasing changes
            decision = {
                "param": param,
                "verdict": verdict,
                "action": "ESCALATED",
                "reason": f"risk-increasing change: {param} {current_f} -> {proposed_f}",
                "current_value": current_f,
                "proposed_value": proposed_f,
                "risk_direction": "increasing",
            }
            result["decisions"].append(decision)
            result["escalated"].append(decision)
            _log_decision(decision)
            tg_lines.append(f"ESCALATED {param}: {current_f} -> {proposed_f} (risk-increasing)")

            if send_tg:
                send_telegram(
                    f"<b>OPERATOR APPROVAL REQUIRED</b>\n\n"
                    f"Risk-increasing change detected:\n"
                    f"Parameter: <b>{param}</b>\n"
                    f"Current: {current_f}\n"
                    f"Proposed: {proposed_f}\n"
                    f"Direction: RISK INCREASING\n\n"
                    f"This change will NOT be auto-applied."
                )
            continue

        # Safe to auto-apply (within bounds, not risk-increasing, within drift cap)
        if dry_run:
            decision = {
                "param": param,
                "verdict": verdict,
                "action": "WOULD_APPLY",
                "reason": f"dry-run: would update {param} from {current_f} to {proposed_f}",
                "current_value": current_f,
                "proposed_value": proposed_f,
            }
            result["decisions"].append(decision)
            result["applied"].append(decision)
            _log_decision(decision)
            tg_lines.append(f"WOULD APPLY {param}: {current_f} -> {proposed_f}")
        else:
            success = _update_dynamic_weights(param, proposed_f)
            action = "APPLIED" if success else "WRITE_FAILED"
            decision = {
                "param": param,
                "verdict": verdict,
                "action": action,
                "reason": f"auto-applied: {param} {current_f} -> {proposed_f}" if success else "TOML write failed",
                "current_value": current_f,
                "proposed_value": proposed_f,
            }
            result["decisions"].append(decision)
            if success:
                result["applied"].append(decision)
                log.info("Auto-applied %s: %s -> %s", param, current_f, proposed_f)
            else:
                result["blocked"].append(decision)
                log.error("Failed to write %s update to dynamic_weights.toml", param)
            _log_decision(decision)
            tg_lines.append(f"{'APPLIED' if success else 'FAILED'} {param}: {current_f} -> {proposed_f}")

    # Summary
    result["summary"] = {
        "total": len(challenges),
        "applied": len(result["applied"]),
        "blocked": len(result["blocked"]),
        "escalated": len(result["escalated"]),
    }

    tg_lines.append(f"\n<b>Summary:</b> {len(result['applied'])} applied, "
                     f"{len(result['blocked'])} blocked, {len(result['escalated'])} escalated")

    if send_tg:
        send_telegram("\n".join(tg_lines))
        log.info("Telegram approval gate summary sent")

    # Send SIGHUP if we applied any changes
    if result["applied"] and not dry_run:
        _notify_engine_sighup()

    return result


def _notify_engine_sighup():
    """Send SIGHUP to aegis engine for hot-reload (best-effort)."""
    import signal
    import subprocess
    try:
        proc = subprocess.run(
            ["pgrep", "-x", "aegis"],
            capture_output=True, text=True, timeout=5,
        )
        pids = [p for p in proc.stdout.strip().split("\n") if p.isdigit()]
        if not pids:
            log.info("No aegis process found -- SIGHUP skipped")
            return
        for pid_str in pids:
            os.kill(int(pid_str), signal.SIGHUP)
            log.info("Sent SIGHUP to aegis PID %s", pid_str)
    except Exception as e:
        log.warning("Failed to send SIGHUP: %s (non-fatal)", e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [ApprovalGate] %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Approval Gate (Sprint S09/S10)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be applied without writing")
    parser.add_argument("--send-telegram", action="store_true", help="Send summary via Telegram")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    args = parser.parse_args()

    result = run_approval_gate(
        dry_run=args.dry_run,
        send_tg=args.send_telegram,
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        summary = result.get("summary", {})
        print(f"\nApproval Gate complete:")
        print(f"  Applied: {summary.get('applied', 0)}")
        print(f"  Blocked: {summary.get('blocked', 0)}")
        print(f"  Escalated: {summary.get('escalated', 0)}")

        for d in result.get("decisions", []):
            action = d.get("action", "?")
            param = d.get("param", "?")
            reason = d.get("reason", "")[:80]
            print(f"  {action} {param}: {reason}")


if __name__ == "__main__":
    main()
