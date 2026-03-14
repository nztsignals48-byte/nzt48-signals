#!/usr/bin/env python3
"""
scripts/start_local.py
========================
Unified local startup — starts API, engine, and frontend in one command.
Monitors all 3 processes, auto-restarts on crash, handles SIGINT gracefully.

Usage:
    python3 scripts/start_local.py           # start all 3
    python3 scripts/start_local.py --no-fe   # skip frontend (Next.js)
    python3 scripts/start_local.py --api-only  # only start API
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "dashboard" / "frontend"

# Colors for terminal output
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
RESET  = "\033[0m"


def log(tag: str, msg: str, color: str = CYAN) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{tag}]{RESET} {msg}", flush=True)


class ProcessManager:
    """Manages child processes with auto-restart and graceful shutdown."""

    def __init__(self):
        self._procs: dict[str, subprocess.Popen] = {}
        self._commands: dict[str, list[str]] = {}
        self._cwds: dict[str, Path] = {}
        self._envs: dict[str, dict] = {}
        self._shutting_down = False
        self._restart_counts: dict[str, int] = {}
        self._max_restarts = 5

    def add(self, name: str, cmd: list[str], cwd: Path | None = None,
            env_extra: dict | None = None) -> None:
        env = os.environ.copy()
        if env_extra:
            env.update(env_extra)
        self._commands[name] = cmd
        self._cwds[name] = cwd or PROJECT_ROOT
        self._envs[name] = env
        self._restart_counts[name] = 0

    def start(self, name: str) -> None:
        if name in self._procs and self._procs[name].poll() is None:
            return  # Already running
        cmd = self._commands[name]
        cwd = self._cwds[name]
        env = self._envs[name]
        log(name, f"Starting: {' '.join(cmd)}", GREEN)
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            self._procs[name] = proc
        except FileNotFoundError:
            log(name, f"Command not found: {cmd[0]}", RED)

    def start_all(self) -> None:
        for name in self._commands:
            self.start(name)

    def shutdown(self) -> None:
        self._shutting_down = True
        log("MAIN", "Shutting down all processes...", YELLOW)
        for name, proc in self._procs.items():
            if proc.poll() is None:
                log(name, "Sending SIGTERM", YELLOW)
                proc.terminate()
        # Wait up to 5 seconds for graceful shutdown
        deadline = time.time() + 5
        for name, proc in self._procs.items():
            remaining = max(0.1, deadline - time.time())
            try:
                proc.wait(timeout=remaining)
                log(name, "Stopped", YELLOW)
            except subprocess.TimeoutExpired:
                log(name, "Force killing (SIGKILL)", RED)
                proc.kill()

    def monitor(self) -> None:
        """Monitor processes, stream output, auto-restart on crash."""
        import select
        import io

        # Set up non-blocking reads
        fds = {}
        for name, proc in self._procs.items():
            if proc.stdout:
                os.set_blocking(proc.stdout.fileno(), False)
                fds[proc.stdout.fileno()] = name

        while not self._shutting_down:
            # Read output from all processes
            readable_fds = list(fds.keys())
            if not readable_fds:
                time.sleep(0.5)
                continue

            try:
                ready, _, _ = select.select(readable_fds, [], [], 1.0)
            except (ValueError, OSError):
                # FD closed — rebuild fd map
                fds = {}
                for name, proc in self._procs.items():
                    if proc.stdout and proc.poll() is None:
                        try:
                            os.set_blocking(proc.stdout.fileno(), False)
                            fds[proc.stdout.fileno()] = name
                        except (ValueError, OSError):
                            pass
                continue

            for fd in ready:
                name = fds.get(fd, "?")
                proc = self._procs.get(name)
                if proc and proc.stdout:
                    try:
                        data = proc.stdout.read(4096)
                        if data:
                            for line in data.decode("utf-8", errors="replace").splitlines():
                                log(name, line)
                    except (IOError, OSError):
                        pass

            # Check for crashed processes
            for name in list(self._procs.keys()):
                proc = self._procs[name]
                rc = proc.poll()
                if rc is not None and not self._shutting_down:
                    # Clean up dead fd
                    if proc.stdout:
                        try:
                            del fds[proc.stdout.fileno()]
                        except (KeyError, ValueError):
                            pass

                    self._restart_counts[name] += 1
                    if self._restart_counts[name] > self._max_restarts:
                        log(name, f"Exceeded max restarts ({self._max_restarts}). Giving up.", RED)
                        continue

                    log(name, f"Crashed (exit code {rc}). Restarting in 3s... "
                              f"(attempt {self._restart_counts[name]}/{self._max_restarts})", RED)
                    time.sleep(3)
                    self.start(name)
                    # Re-register fd
                    new_proc = self._procs[name]
                    if new_proc.stdout:
                        os.set_blocking(new_proc.stdout.fileno(), False)
                        fds[new_proc.stdout.fileno()] = name


def main():
    parser = argparse.ArgumentParser(description="NZT-48 Local Startup")
    parser.add_argument("--no-fe", action="store_true", help="Skip frontend (Next.js)")
    parser.add_argument("--api-only", action="store_true", help="Only start API server")
    args = parser.parse_args()

    pm = ProcessManager()

    # 1. Unified API (always)
    pm.add("API", [
        sys.executable, "-m", "uvicorn", "dashboard.api:app",
        "--host", "0.0.0.0", "--port", "8000", "--log-level", "info",
    ], cwd=PROJECT_ROOT, env_extra={
        "NZT48_API_URL": "http://localhost:8000",
    })

    if not args.api_only:
        # 2. Engine
        pm.add("ENGINE", [
            sys.executable, str(PROJECT_ROOT / "main.py"),
        ], cwd=PROJECT_ROOT, env_extra={
            "NZT48_API_URL": "http://localhost:8000",
        })

        # 3. Frontend (Next.js)
        if not args.no_fe and FRONTEND_DIR.exists():
            pm.add("FRONTEND", [
                "npm", "run", "dev",
            ], cwd=FRONTEND_DIR, env_extra={
                "NEXT_PUBLIC_API_URL": "http://localhost:8000",
                "PORT": "3001",
            })

    # Signal handlers
    def on_signal(signum, frame):
        pm.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    # Start
    log("MAIN", "=" * 60, GREEN)
    log("MAIN", "NZT-48 LOCAL STARTUP", GREEN)
    log("MAIN", f"API:      http://localhost:8000", GREEN)
    log("MAIN", f"CC:       http://localhost:8000/cc/", GREEN)
    if not args.api_only:
        log("MAIN", f"Engine:   main.py", GREEN)
        if not args.no_fe:
            log("MAIN", f"Frontend: http://localhost:3001", GREEN)
    log("MAIN", "=" * 60, GREEN)

    # Start API first, wait for it to be ready
    pm.start("API")
    log("MAIN", "Waiting 3s for API to start...", YELLOW)
    time.sleep(3)

    # Start remaining processes
    for name in pm._commands:
        if name != "API":
            pm.start(name)

    # Monitor (blocks until shutdown)
    pm.monitor()


if __name__ == "__main__":
    main()
