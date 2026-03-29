"""Book 208 — Quality Gates: Paper → Validated → Live Lifecycle.

Every new strategy starts in PAPER mode. It must accumulate enough trades
and pass statistical validation before being promoted to LIVE.

Lifecycle states:
  PAPER:      Shadow-mode only. Signals logged but NOT sent to Rust.
              Minimum 30 days, minimum 50 signals.
  VALIDATED:  Passed DSR/PBO/walk-forward gates (strategy_gates.py).
              Operator approval required to move to LIVE.
  LIVE:       Full production. Signals sent to Rust engine.
  SUSPENDED:  Temporarily halted (drawdown, consecutive losses).
              Can be re-promoted to LIVE after review.
  RETIRED:    Permanently disabled. No signals generated.

State file:
  /app/data/strategy_lifecycle.json — persists across restarts.
  Updated by this module and by the nightly pipeline.

Bridge.py integration:
  Before returning a signal, bridge.py calls:
    from python_brain.validation.quality_gates import is_strategy_live
    if not is_strategy_live(strategy_name):
        log_shadow_signal(signal)
        return no_signal

Usage:
    from python_brain.validation.quality_gates import (
        StrategyLifecycle, LifecycleState,
        is_strategy_live, record_paper_signal, check_promotion,
    )
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("quality_gates")

DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
LIFECYCLE_FILE = DATA_DIR / "strategy_lifecycle.json"
SHADOW_LOG = DATA_DIR / "shadow_signals.ndjson"

# Promotion thresholds
MIN_PAPER_DAYS = 30
MIN_PAPER_SIGNALS = 50
MIN_PAPER_WIN_RATE = 0.35
MIN_PAPER_SHARPE = 0.3


class LifecycleState(Enum):
    PAPER = "PAPER"
    VALIDATED = "VALIDATED"
    LIVE = "LIVE"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


@dataclass
class StrategyRecord:
    """Per-strategy lifecycle tracking."""
    name: str
    state: str = "LIVE"  # Default LIVE for existing strategies
    entered_paper_at: float = 0.0  # Unix timestamp
    paper_signals: int = 0
    paper_wins: int = 0
    paper_losses: int = 0
    validated_at: float = 0.0
    promoted_at: float = 0.0
    suspended_at: float = 0.0
    suspended_reason: str = ""
    retired_at: float = 0.0

    @property
    def paper_days(self) -> float:
        if self.entered_paper_at <= 0:
            return 0
        return (time.time() - self.entered_paper_at) / 86400

    @property
    def paper_win_rate(self) -> float:
        total = self.paper_wins + self.paper_losses
        return self.paper_wins / total if total > 0 else 0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> StrategyRecord:
        return StrategyRecord(**{k: v for k, v in d.items()
                                 if k in StrategyRecord.__dataclass_fields__})


class StrategyLifecycle:
    """Manage the lifecycle of all strategies."""

    def __init__(self):
        self._strategies: Dict[str, StrategyRecord] = {}
        self._loaded = False

    def _ensure_loaded(self):
        if not self._loaded:
            self._load()
            self._loaded = True

    def get(self, name: str) -> StrategyRecord:
        """Get or create a strategy record. Unknown strategies default to LIVE."""
        self._ensure_loaded()
        if name not in self._strategies:
            self._strategies[name] = StrategyRecord(name=name, state="LIVE")
        return self._strategies[name]

    def is_live(self, name: str) -> bool:
        """Check if strategy is allowed to produce real signals."""
        rec = self.get(name)
        return rec.state == "LIVE"

    def register_paper(self, name: str) -> StrategyRecord:
        """Register a new strategy in PAPER mode."""
        self._ensure_loaded()
        rec = StrategyRecord(
            name=name,
            state="PAPER",
            entered_paper_at=time.time(),
        )
        self._strategies[name] = rec
        self._save()
        log.info("LIFECYCLE: %s registered as PAPER", name)
        return rec

    def record_paper_signal(self, name: str, profitable: bool):
        """Record a paper-mode signal outcome."""
        rec = self.get(name)
        if rec.state != "PAPER":
            return
        rec.paper_signals += 1
        if profitable:
            rec.paper_wins += 1
        else:
            rec.paper_losses += 1
        # Auto-save every 10 signals
        if rec.paper_signals % 10 == 0:
            self._save()

    def check_promotion(self, name: str) -> Dict[str, Any]:
        """Check if a PAPER strategy is ready for promotion to VALIDATED.

        Returns dict with {eligible: bool, reason: str, metrics: dict}.
        Does NOT auto-promote — that requires operator approval or nightly pipeline.
        """
        rec = self.get(name)
        result: Dict[str, Any] = {"eligible": False, "reason": "", "metrics": {}}

        if rec.state != "PAPER":
            result["reason"] = f"not in PAPER state (current: {rec.state})"
            return result

        metrics = {
            "paper_days": round(rec.paper_days, 1),
            "paper_signals": rec.paper_signals,
            "paper_win_rate": round(rec.paper_win_rate, 3),
        }
        result["metrics"] = metrics

        if rec.paper_days < MIN_PAPER_DAYS:
            result["reason"] = f"insufficient_days: {rec.paper_days:.0f} < {MIN_PAPER_DAYS}"
            return result

        if rec.paper_signals < MIN_PAPER_SIGNALS:
            result["reason"] = f"insufficient_signals: {rec.paper_signals} < {MIN_PAPER_SIGNALS}"
            return result

        if rec.paper_win_rate < MIN_PAPER_WIN_RATE:
            result["reason"] = f"win_rate_too_low: {rec.paper_win_rate:.1%} < {MIN_PAPER_WIN_RATE:.0%}"
            return result

        result["eligible"] = True
        result["reason"] = "meets_all_paper_thresholds"
        return result

    def promote_to_validated(self, name: str) -> bool:
        """Move PAPER → VALIDATED (after check_promotion passes)."""
        rec = self.get(name)
        if rec.state != "PAPER":
            return False
        rec.state = "VALIDATED"
        rec.validated_at = time.time()
        self._save()
        log.info("LIFECYCLE: %s PAPER→VALIDATED (days=%.0f, signals=%d, wr=%.1f%%)",
                 name, rec.paper_days, rec.paper_signals, rec.paper_win_rate * 100)
        return True

    def promote_to_live(self, name: str) -> bool:
        """Move VALIDATED → LIVE (requires operator approval)."""
        rec = self.get(name)
        if rec.state not in ("VALIDATED", "SUSPENDED"):
            return False
        old = rec.state
        rec.state = "LIVE"
        rec.promoted_at = time.time()
        rec.suspended_reason = ""
        self._save()
        log.info("LIFECYCLE: %s %s→LIVE", name, old)
        return True

    def suspend(self, name: str, reason: str) -> bool:
        """Suspend a LIVE strategy (e.g., drawdown, consecutive losses)."""
        rec = self.get(name)
        if rec.state != "LIVE":
            return False
        rec.state = "SUSPENDED"
        rec.suspended_at = time.time()
        rec.suspended_reason = reason
        self._save()
        log.warning("LIFECYCLE: %s LIVE→SUSPENDED reason=%s", name, reason)
        # Notify operator via Telegram
        try:
            from python_brain.ouroboros.claude_helper import send_telegram
            send_telegram(f"LIFECYCLE: {name} SUSPENDED\nReason: {reason}")
        except Exception:
            pass
        return True

    def retire(self, name: str) -> bool:
        """Permanently retire a strategy."""
        rec = self.get(name)
        if rec.state == "RETIRED":
            return False
        old = rec.state
        rec.state = "RETIRED"
        rec.retired_at = time.time()
        self._save()
        log.info("LIFECYCLE: %s %s→RETIRED", name, old)
        return True

    def summary(self) -> Dict[str, List[str]]:
        """Get all strategies grouped by state."""
        self._ensure_loaded()
        out: Dict[str, List[str]] = {s.value: [] for s in LifecycleState}
        for name, rec in self._strategies.items():
            state = rec.state if rec.state in out else "LIVE"
            out[state].append(name)
        return out

    # -----------------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------------
    def _save(self):
        LIFECYCLE_FILE.parent.mkdir(parents=True, exist_ok=True)
        state = {name: rec.to_dict() for name, rec in self._strategies.items()}
        try:
            tmp = LIFECYCLE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
            os.rename(str(tmp), str(LIFECYCLE_FILE))
        except OSError as e:
            log.warning("Failed to save lifecycle state: %s", e)

    def _load(self):
        if not LIFECYCLE_FILE.exists():
            return
        try:
            data = json.loads(LIFECYCLE_FILE.read_text(encoding="utf-8"))
            for name, d in data.items():
                self._strategies[name] = StrategyRecord.from_dict(d)
            log.info("LIFECYCLE: loaded %d strategy records", len(self._strategies))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load lifecycle state: %s", e)


# ---------------------------------------------------------------------------
# Module-level singleton + convenience functions for bridge.py
# ---------------------------------------------------------------------------
_lifecycle: Optional[StrategyLifecycle] = None


def get_lifecycle() -> StrategyLifecycle:
    global _lifecycle
    if _lifecycle is None:
        _lifecycle = StrategyLifecycle()
    return _lifecycle


def is_strategy_live(name: str) -> bool:
    """Quick check: is this strategy allowed to produce real signals?"""
    return get_lifecycle().is_live(name)


def record_paper_signal(name: str, profitable: bool):
    """Record a paper signal outcome for promotion tracking."""
    get_lifecycle().record_paper_signal(name, profitable)


def check_promotion(name: str) -> Dict[str, Any]:
    """Check if strategy is eligible for PAPER → VALIDATED promotion."""
    return get_lifecycle().check_promotion(name)


def log_shadow_signal(signal_dict: Dict[str, Any]):
    """Log a shadow (paper-mode) signal to NDJSON for analysis."""
    SHADOW_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": time.time(),
        "strategy": signal_dict.get("strategy", ""),
        "ticker_id": signal_dict.get("ticker_id", 0),
        "direction": signal_dict.get("direction", ""),
        "confidence": signal_dict.get("confidence", 0),
        "kelly_fraction": signal_dict.get("kelly_fraction", 0),
        "price": signal_dict.get("price", 0),
    }
    try:
        with open(SHADOW_LOG, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except OSError:
        pass  # Non-critical


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [QualityGates] %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Book 208: Strategy Quality Gates")
    parser.add_argument("--summary", action="store_true", help="Show all strategy states")
    parser.add_argument("--check", type=str, help="Check promotion eligibility for a strategy")
    parser.add_argument("--register-paper", type=str, help="Register a new strategy in PAPER mode")
    parser.add_argument("--promote-validated", type=str, help="Promote PAPER → VALIDATED")
    parser.add_argument("--promote-live", type=str, help="Promote VALIDATED → LIVE")
    parser.add_argument("--suspend", nargs=2, metavar=("STRATEGY", "REASON"), help="Suspend a strategy")
    parser.add_argument("--retire", type=str, help="Retire a strategy")
    args = parser.parse_args()

    lc = get_lifecycle()

    if args.summary:
        s = lc.summary()
        for state, strategies in s.items():
            if strategies:
                print(f"  {state}: {', '.join(strategies)}")
        return

    if args.check:
        result = check_promotion(args.check)
        print(json.dumps(result, indent=2))
        return

    if args.register_paper:
        lc.register_paper(args.register_paper)
        print(f"Registered {args.register_paper} as PAPER")
        return

    if args.promote_validated:
        ok = lc.promote_to_validated(args.promote_validated)
        print(f"{'OK' if ok else 'FAILED'}: {args.promote_validated} → VALIDATED")
        return

    if args.promote_live:
        ok = lc.promote_to_live(args.promote_live)
        print(f"{'OK' if ok else 'FAILED'}: {args.promote_live} → LIVE")
        return

    if args.suspend:
        ok = lc.suspend(args.suspend[0], args.suspend[1])
        print(f"{'OK' if ok else 'FAILED'}: {args.suspend[0]} → SUSPENDED")
        return

    if args.retire:
        ok = lc.retire(args.retire)
        print(f"{'OK' if ok else 'FAILED'}: {args.retire} → RETIRED")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
