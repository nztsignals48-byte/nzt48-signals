"""Step Runner — Sequential execution wrapper with OOM prevention.

SC-19: Ensures Ouroboros steps run one-at-a-time with memory guards.
Uses a Redis-based lock to prevent concurrent execution.

Usage (in crontab):
    python3 -m python_brain.ouroboros.step_runner nightly_v6 -- --mode full
    python3 -m python_brain.ouroboros.step_runner config_writer
    python3 -m python_brain.ouroboros.step_runner ticker_selector -- --mode daily

The wrapper:
  1. Acquires a Redis lock (blocking up to 10 min, auto-expire 30 min)
  2. Checks system memory — skips if MemAvailable < 512 MB
  3. Runs the step as a subprocess (clean memory per invocation)
  4. Monitors subprocess RSS — sends SIGTERM if > 1.5 GB
  5. Releases lock, logs duration + peak memory to step_logs/
"""

from __future__ import annotations

import json
import logging
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
STEP_LOG_DIR = DATA_DIR / "step_logs"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [StepRunner] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("step_runner")

# ---------------------------------------------------------------------------
# Redis config
# ---------------------------------------------------------------------------

REDIS_URL = os.environ.get("REDIS_URL", "redis://aegis-redis:6379/0")
LOCK_KEY = "ouroboros:step_lock"
LOCK_TTL_SEC = 1800        # 30 minutes — auto-expire if holder dies
LOCK_WAIT_SEC = 600        # 10 minutes — max time to wait for lock
LOCK_POLL_SEC = 5          # poll interval while waiting

# ---------------------------------------------------------------------------
# Memory thresholds
# ---------------------------------------------------------------------------

MEM_SKIP_THRESHOLD_MB = 512     # skip step if MemAvailable < this
MEM_KILL_THRESHOLD_MB = 1536    # SIGTERM subprocess if its RSS > this (1.5 GB)
SUBPROCESS_TIMEOUT_SEC = 1800   # 30 minutes max per step

# ---------------------------------------------------------------------------
# Step registry — maps step names to Python module paths
# ---------------------------------------------------------------------------

STEP_REGISTRY: Dict[str, str] = {
    "nightly_v6":            "python_brain.ouroboros.nightly_v6",
    "config_writer":         "python_brain.ouroboros.config_writer",
    "ticker_selector":       "python_brain.ouroboros.ticker_selector",
    "backfill_simulator":    "python_brain.ouroboros.backfill_simulator",
    "sheets_sync":           "python_brain.ouroboros.sheets_sync",
    # P3: meta_label_optimizer deleted (no runtime consumers, no cron)
    "ibkr_scanner":          "python_brain.ouroboros.ibkr_scanner",
    "win_loss_delta":        "python_brain.ouroboros.win_loss_delta",
    "fill_quality":          "python_brain.ouroboros.fill_quality",
    "post_trade_diagnostics": "python_brain.ouroboros.post_trade_diagnostics",
    "universe_refresh":      "python_brain.ouroboros.universe_filters",
    "ouroboros_monitor":     "python_brain.ouroboros.ouroboros_monitor",
    "claude_review":         "python_brain.ouroboros.claude_review",
    "claude_briefing":       "python_brain.ouroboros.claude_briefing",
    "maintenance":           "python_brain.ouroboros.maintenance",
    "session_pdf":           "python_brain.ouroboros.session_pdf",
    "daily_sim_report":      "python_brain.ouroboros.daily_sim_report",
    "cost_drag_report":      "python_brain.ouroboros.cost_drag_report",
    "bridge_health":         "python_brain.ouroboros.bridge_health",
    "external_monitor":      "python_brain.ouroboros.external_monitor",
    "log_rotate":            "python_brain.ouroboros.log_rotate",
    "fx_refresh":            "python_brain.ouroboros.fx_refresh",
    "contract_expander":     "python_brain.ouroboros.contract_expander",
    "config_fixes":          "python_brain.ouroboros.config_fixes",
    "telegram_notify":       "python_brain.ouroboros.telegram_notify",
    "all_trades_report":     "python_brain.ouroboros.all_trades_report",
    "backtest_vanguard":     "python_brain.ouroboros.backtest_vanguard",
}


# ---------------------------------------------------------------------------
# Redis lock
# ---------------------------------------------------------------------------

def _connect_redis():
    """Create Redis connection. Returns None if Redis unavailable."""
    try:
        import redis as redis_pkg
        r = redis_pkg.from_url(REDIS_URL, socket_timeout=5, socket_connect_timeout=5)
        r.ping()
        return r
    except Exception as e:
        log.warning("Redis unavailable (%s) — running WITHOUT lock protection", e)
        return None


def _make_lock_value(step_name: str) -> str:
    """Create unique lock value: step_name:pid:timestamp:uuid."""
    return f"{step_name}:{os.getpid()}:{time.time():.0f}:{uuid.uuid4().hex[:8]}"


def acquire_lock(r, step_name: str) -> Optional[str]:
    """Acquire Redis lock. Blocks up to LOCK_WAIT_SEC.

    Returns lock_value on success, None on timeout.
    """
    if r is None:
        return "no-redis-fallback"

    lock_value = _make_lock_value(step_name)
    deadline = time.monotonic() + LOCK_WAIT_SEC

    while time.monotonic() < deadline:
        # NX = only set if not exists, EX = TTL in seconds
        acquired = r.set(LOCK_KEY, lock_value, nx=True, ex=LOCK_TTL_SEC)
        if acquired:
            log.info("Lock acquired: %s", lock_value)
            return lock_value

        # Lock held by someone else — log who
        holder = r.get(LOCK_KEY)
        if holder:
            holder_str = holder.decode("utf-8", errors="replace") if isinstance(holder, bytes) else str(holder)
            remaining = r.ttl(LOCK_KEY)
            log.info("Lock held by [%s] (TTL %ds) — waiting...", holder_str, remaining)
        else:
            # Key vanished between SET and GET — retry immediately
            continue

        time.sleep(LOCK_POLL_SEC)

    log.warning("Lock acquisition timed out after %ds — SKIPPING step %s",
                LOCK_WAIT_SEC, step_name)
    return None


def release_lock(r, lock_value: str) -> bool:
    """Release Redis lock, but only if we still own it (compare-and-delete).

    Uses a Lua script for atomicity.
    """
    if r is None or lock_value == "no-redis-fallback":
        return True

    # Atomic compare-and-delete via Lua
    lua_script = """
    if redis.call("GET", KEYS[1]) == ARGV[1] then
        return redis.call("DEL", KEYS[1])
    else
        return 0
    end
    """
    try:
        result = r.eval(lua_script, 1, LOCK_KEY, lock_value)
        if result:
            log.info("Lock released: %s", lock_value)
            return True
        else:
            log.warning("Lock already released or stolen (value mismatch)")
            return False
    except Exception as e:
        log.error("Failed to release lock: %s", e)
        # Best-effort: try plain DEL as fallback
        try:
            r.delete(LOCK_KEY)
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# Memory checks (platform-aware)
# ---------------------------------------------------------------------------

def get_system_memory_mb() -> Dict[str, float]:
    """Read system memory info.

    Returns dict with keys: total_mb, available_mb, used_pct, swap_used_mb.
    Tries /proc/meminfo (Linux) first, then psutil fallback.
    """
    result = {
        "total_mb": 0.0,
        "available_mb": 0.0,
        "used_pct": 0.0,
        "swap_used_mb": 0.0,
        "source": "unknown",
    }

    # Try /proc/meminfo first (Linux, fast, no deps)
    meminfo_path = Path("/proc/meminfo")
    if meminfo_path.exists():
        try:
            text = meminfo_path.read_text()
            fields = {}
            for line in text.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    val_kb = int(parts[1])
                    fields[key] = val_kb

            total_kb = fields.get("MemTotal", 0)
            available_kb = fields.get("MemAvailable", 0)
            swap_total_kb = fields.get("SwapTotal", 0)
            swap_free_kb = fields.get("SwapFree", 0)

            result["total_mb"] = total_kb / 1024
            result["available_mb"] = available_kb / 1024
            if total_kb > 0:
                result["used_pct"] = ((total_kb - available_kb) / total_kb) * 100
            result["swap_used_mb"] = (swap_total_kb - swap_free_kb) / 1024
            result["source"] = "procfs"
            return result
        except (OSError, ValueError, KeyError) as e:
            log.debug("/proc/meminfo parse failed: %s", e)

    # Fallback: psutil (works on macOS + Linux)
    try:
        import psutil
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        result["total_mb"] = vm.total / (1024 * 1024)
        result["available_mb"] = vm.available / (1024 * 1024)
        result["used_pct"] = vm.percent
        result["swap_used_mb"] = sw.used / (1024 * 1024)
        result["source"] = "psutil"
        return result
    except ImportError:
        log.debug("psutil not available")
    except Exception as e:
        log.debug("psutil failed: %s", e)

    # Last resort on macOS: vm_stat
    if platform.system() == "Darwin":
        try:
            out = subprocess.check_output(["vm_stat"], text=True, timeout=5)
            page_size = 16384  # default on Apple Silicon
            # Parse first line for page size
            for line in out.splitlines():
                if "page size of" in line:
                    page_size = int(line.split()[-2])
                    break

            fields = {}
            for line in out.splitlines()[1:]:
                if ":" in line:
                    key, val = line.split(":", 1)
                    val = val.strip().rstrip(".")
                    try:
                        fields[key.strip()] = int(val)
                    except ValueError:
                        continue

            free_pages = fields.get("Pages free", 0)
            inactive_pages = fields.get("Pages inactive", 0)
            speculative_pages = fields.get("Pages speculative", 0)
            available_pages = free_pages + inactive_pages + speculative_pages

            # Get total from sysctl
            sysctl_out = subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"], text=True, timeout=5
            ).strip()
            total_bytes = int(sysctl_out)

            result["total_mb"] = total_bytes / (1024 * 1024)
            result["available_mb"] = (available_pages * page_size) / (1024 * 1024)
            if total_bytes > 0:
                result["used_pct"] = (1 - (available_pages * page_size / total_bytes)) * 100
            result["source"] = "vm_stat"
            return result
        except Exception as e:
            log.debug("vm_stat fallback failed: %s", e)

    log.warning("Could not determine system memory — skipping memory guard")
    return result


def get_process_rss_mb(pid: int) -> float:
    """Get RSS of a process in MB. Returns 0.0 on failure."""
    # Try /proc/{pid}/status (Linux)
    status_path = Path(f"/proc/{pid}/status")
    if status_path.exists():
        try:
            for line in status_path.read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024  # kB → MB
        except (OSError, ValueError, IndexError):
            pass

    # Try /proc/{pid}/statm (Linux, faster)
    statm_path = Path(f"/proc/{pid}/statm")
    if statm_path.exists():
        try:
            pages = int(statm_path.read_text().split()[1])
            page_size = os.sysconf("SC_PAGE_SIZE")
            return (pages * page_size) / (1024 * 1024)
        except (OSError, ValueError, IndexError):
            pass

    # Fallback: psutil
    try:
        import psutil
        proc = psutil.Process(pid)
        return proc.memory_info().rss / (1024 * 1024)
    except Exception:
        pass

    # Fallback: resource (self only)
    if pid == os.getpid():
        try:
            import resource
            # On macOS, ru_maxrss is bytes; on Linux, it's kB
            usage = resource.getrusage(resource.RUSAGE_CHILDREN)
            rss = usage.ru_maxrss
            if platform.system() == "Darwin":
                return rss / (1024 * 1024)
            else:
                return rss / 1024
        except Exception:
            pass

    return 0.0


# ---------------------------------------------------------------------------
# Step log management
# ---------------------------------------------------------------------------

def _ensure_log_dir() -> Path:
    """Create step_logs directory if it doesn't exist."""
    STEP_LOG_DIR.mkdir(parents=True, exist_ok=True)
    return STEP_LOG_DIR


def write_step_log(
    step_name: str,
    exit_code: int,
    duration_sec: float,
    peak_rss_mb: float,
    skipped: bool = False,
    skip_reason: str = "",
    stdout_tail: str = "",
    stderr_tail: str = "",
) -> Path:
    """Write step execution log as JSON. Uses atomic write (tmp + rename).

    Returns path to the log file.
    """
    log_dir = _ensure_log_dir()
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"{step_name}_{date_str}.json"
    tmp_path = log_dir / f".{step_name}_{date_str}.json.tmp"

    record = {
        "step": step_name,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "exit_code": exit_code,
        "duration_sec": round(duration_sec, 2),
        "peak_rss_mb": round(peak_rss_mb, 1),
        "skipped": skipped,
        "skip_reason": skip_reason,
        "stdout_tail": stdout_tail[-2000:] if stdout_tail else "",
        "stderr_tail": stderr_tail[-2000:] if stderr_tail else "",
        "pid": os.getpid(),
        "hostname": platform.node(),
    }

    try:
        tmp_path.write_text(json.dumps(record, indent=2) + "\n")
        tmp_path.rename(log_path)
    except OSError as e:
        log.error("Failed to write step log %s: %s", log_path, e)
        # Best effort: write directly
        try:
            log_path.write_text(json.dumps(record, indent=2) + "\n")
        except OSError:
            pass

    return log_path


# ---------------------------------------------------------------------------
# Subprocess memory monitor
# ---------------------------------------------------------------------------

def _monitor_subprocess(
    proc: subprocess.Popen,
    step_name: str,
    poll_interval: float = 5.0,
) -> Tuple[float, bool]:
    """Poll subprocess RSS until it exits. Returns (peak_rss_mb, was_killed).

    Sends SIGTERM if RSS exceeds MEM_KILL_THRESHOLD_MB.
    """
    peak_rss = 0.0
    was_killed = False

    while proc.poll() is None:
        try:
            rss = get_process_rss_mb(proc.pid)
            if rss > peak_rss:
                peak_rss = rss

            if rss > MEM_KILL_THRESHOLD_MB:
                log.error(
                    "SC-19: Step %s RSS %.0f MB exceeds kill threshold %d MB — sending SIGTERM",
                    step_name, rss, MEM_KILL_THRESHOLD_MB,
                )
                proc.send_signal(signal.SIGTERM)
                was_killed = True
                # Give it 10s to clean up, then SIGKILL
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    log.error("SC-19: Step %s did not exit after SIGTERM — sending SIGKILL", step_name)
                    proc.kill()
                break
        except Exception as e:
            log.debug("Memory monitor error for pid %d: %s", proc.pid, e)

        time.sleep(poll_interval)

    return peak_rss, was_killed


# ---------------------------------------------------------------------------
# StepRunner
# ---------------------------------------------------------------------------

class StepRunner:
    """Sequential step executor with Redis lock and memory guards."""

    def __init__(self, step_name: str, step_args: List[str] = None):
        self.step_name = step_name
        self.step_args = step_args or []
        self.module = STEP_REGISTRY.get(step_name)
        self.redis = None
        self.lock_value = None

    def validate(self) -> bool:
        """Check that the step name is valid."""
        if not self.module:
            available = ", ".join(sorted(STEP_REGISTRY.keys()))
            log.error("Unknown step: %s (available: %s)", self.step_name, available)
            return False
        return True

    def run(self) -> int:
        """Execute the step with lock and memory guards. Returns exit code."""
        if not self.validate():
            return 1

        log.info("=" * 60)
        log.info("SC-19: Starting step [%s] module=%s args=%s",
                 self.step_name, self.module, self.step_args)
        log.info("=" * 60)

        t0 = time.monotonic()

        # ----- 1. Connect to Redis and acquire lock -----
        self.redis = _connect_redis()
        self.lock_value = acquire_lock(self.redis, self.step_name)

        if self.lock_value is None:
            # Timed out waiting for lock — skip
            duration = time.monotonic() - t0
            write_step_log(
                self.step_name,
                exit_code=-1,
                duration_sec=duration,
                peak_rss_mb=0,
                skipped=True,
                skip_reason=f"Lock timeout after {LOCK_WAIT_SEC}s",
            )
            log.warning("SC-19: SKIPPED [%s] — could not acquire lock", self.step_name)
            return 2

        try:
            # ----- 2. Check system memory -----
            mem = get_system_memory_mb()
            log.info("System memory: %.0f MB total, %.0f MB available (%.1f%% used) [%s]",
                     mem["total_mb"], mem["available_mb"], mem["used_pct"], mem["source"])

            if mem["available_mb"] > 0 and mem["available_mb"] < MEM_SKIP_THRESHOLD_MB:
                duration = time.monotonic() - t0
                write_step_log(
                    self.step_name,
                    exit_code=-2,
                    duration_sec=duration,
                    peak_rss_mb=0,
                    skipped=True,
                    skip_reason=f"MemAvailable {mem['available_mb']:.0f} MB < {MEM_SKIP_THRESHOLD_MB} MB",
                )
                log.warning(
                    "SC-19: SKIPPED [%s] — MemAvailable %.0f MB < %d MB threshold",
                    self.step_name, mem["available_mb"], MEM_SKIP_THRESHOLD_MB,
                )
                return 3

            # ----- 3. Run step as subprocess -----
            cmd = ["python3", "-m", self.module] + self.step_args
            log.info("Executing: %s", " ".join(cmd))

            # Capture output for logging, but also write to step log file
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(_PROJECT_ROOT),
                env={**os.environ, "AEGIS_STEP_RUNNER": "1"},
            )

            # ----- 4. Monitor memory while running -----
            peak_rss, was_killed = _monitor_subprocess(proc, self.step_name)

            # Collect output
            stdout_bytes, stderr_bytes = proc.communicate(timeout=SUBPROCESS_TIMEOUT_SEC)
            stdout_str = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr_str = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

            exit_code = proc.returncode
            duration = time.monotonic() - t0

            # Log result
            if was_killed:
                log.error(
                    "SC-19: Step [%s] KILLED (OOM) — RSS %.0f MB, duration %.1fs",
                    self.step_name, peak_rss, duration,
                )
                exit_code = -9  # Indicate OOM kill
            elif exit_code == 0:
                log.info(
                    "SC-19: Step [%s] COMPLETED — exit=%d, duration=%.1fs, peak_rss=%.0f MB",
                    self.step_name, exit_code, duration, peak_rss,
                )
            else:
                log.warning(
                    "SC-19: Step [%s] FAILED — exit=%d, duration=%.1fs, peak_rss=%.0f MB",
                    self.step_name, exit_code, duration, peak_rss,
                )
                # Log stderr tail for debugging
                if stderr_str:
                    for line in stderr_str.strip().splitlines()[-10:]:
                        log.warning("  stderr: %s", line)

            # Write step log
            log_path = write_step_log(
                self.step_name,
                exit_code=exit_code,
                duration_sec=duration,
                peak_rss_mb=peak_rss,
                stdout_tail=stdout_str,
                stderr_tail=stderr_str,
            )
            log.info("Step log written: %s", log_path)

            return exit_code

        except subprocess.TimeoutExpired:
            duration = time.monotonic() - t0
            log.error("SC-19: Step [%s] TIMED OUT after %ds", self.step_name, SUBPROCESS_TIMEOUT_SEC)
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
            write_step_log(
                self.step_name,
                exit_code=-3,
                duration_sec=duration,
                peak_rss_mb=0,
                skipped=False,
                skip_reason=f"Timeout after {SUBPROCESS_TIMEOUT_SEC}s",
            )
            return 4

        except Exception as e:
            duration = time.monotonic() - t0
            log.error("SC-19: Step [%s] EXCEPTION: %s", self.step_name, e, exc_info=True)
            write_step_log(
                self.step_name,
                exit_code=-99,
                duration_sec=duration,
                peak_rss_mb=0,
                skip_reason=f"Exception: {e}",
            )
            return 5

        finally:
            # ----- 5. Always release lock -----
            release_lock(self.redis, self.lock_value)

            total_duration = time.monotonic() - t0
            log.info("SC-19: Step [%s] total wall time: %.1fs", self.step_name, total_duration)
            log.info("=" * 60)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """CLI: python3 -m python_brain.ouroboros.step_runner {step_name} [-- args...]"""
    # WP-4: Register clean exit handler for log flushing + signal handling
    from python_brain.ouroboros.exit_handler import register_handler
    register_handler()

    import argparse

    parser = argparse.ArgumentParser(
        description="SC-19: Sequential Ouroboros step runner with OOM prevention",
        usage="python3 -m python_brain.ouroboros.step_runner STEP [-- ARGS...]",
    )
    parser.add_argument(
        "step_name",
        help=f"Step to run. Available: {', '.join(sorted(STEP_REGISTRY.keys()))}",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available steps and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check lock + memory but don't actually run the step",
    )
    parser.add_argument(
        "--skip-lock",
        action="store_true",
        help="Skip Redis lock (dangerous — for debugging only)",
    )
    parser.add_argument(
        "--skip-memory-check",
        action="store_true",
        help="Skip memory availability check",
    )

    # Split on '--' to separate step_runner args from step args
    argv = sys.argv[1:]
    step_args = []
    if "--" in argv:
        split_idx = argv.index("--")
        step_args = argv[split_idx + 1:]
        argv = argv[:split_idx]

    args = parser.parse_args(argv)

    if args.list:
        print("Available steps:")
        for name, module in sorted(STEP_REGISTRY.items()):
            print(f"  {name:30s} -> {module}")
        return 0

    runner = StepRunner(args.step_name, step_args)

    if not runner.validate():
        return 1

    if args.dry_run:
        log.info("DRY RUN: would run step [%s] with args %s", args.step_name, step_args)
        mem = get_system_memory_mb()
        log.info("System memory: %.0f MB available / %.0f MB total (%.1f%% used)",
                 mem["available_mb"], mem["total_mb"], mem["used_pct"])
        if mem["available_mb"] > 0 and mem["available_mb"] < MEM_SKIP_THRESHOLD_MB:
            log.warning("Would SKIP: MemAvailable %.0f MB < %d MB", mem["available_mb"], MEM_SKIP_THRESHOLD_MB)
        else:
            log.info("Memory OK — would proceed")
        return 0

    try:
        return runner.run()
    except Exception as e:
        # step_runner itself must never crash
        log.error("FATAL: StepRunner crashed: %s", e, exc_info=True)
        return 99


if __name__ == "__main__":
    sys.exit(main())
