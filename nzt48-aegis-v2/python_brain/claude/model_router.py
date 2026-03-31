"""Multi-LLM Cost Router — Book 205.

Routes LLM calls to the cheapest adequate model for each task type and
complexity level.  Tracks cumulative daily/monthly spend with budget caps.

The router replaces the single-model assumption in DecisionAuthority._select_model
with a 4-tier model matrix keyed by (task_type, complexity).

Model Tiers (sorted by cost):
  Tier 1 — gemini-2.0-flash       $0.10/1M tokens   Simple classification, yes/no
  Tier 2 — claude-3-5-haiku        $0.80/1M tokens   Trade analysis, anomaly detection
  Tier 3 — claude-sonnet-4         $3.00/1M tokens   Nightly review, architecture
  Tier 4 — claude-opus-4           $15.00/1M tokens   Critical risk assessment only

Route Matrix:
  simple   -> Tier 1  (Gemini Flash)
  moderate -> Tier 2  (Haiku)
  complex  -> Tier 3  (Sonnet)
  critical -> Tier 4  (Opus)

Task-specific overrides narrow the mapping further — e.g. regime_classification
is always simple (Tier 1), while nightly_review starts at Tier 3.

Usage:
    from python_brain.claude.model_router import get_router

    router = get_router()
    model = router.route("trade_analysis", "moderate")
    # => "claude-3-5-haiku"

    summary = router.get_cost_summary()
    # => {"daily_usd": 0.042, "monthly_usd": 1.23, ...}
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("model_router")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_TASK_TYPES = frozenset({
    "nightly_review",
    "trade_analysis",
    "anomaly_detection",
    "parameter_optimization",
    "regime_classification",
})

VALID_COMPLEXITIES = ("simple", "moderate", "complex", "critical")

# Tier definitions: (model_id, cost_per_1m_input_tokens_usd)
TIER_1 = ("gemini-2.0-flash", 0.10)
TIER_2 = ("claude-3-5-haiku", 0.80)
TIER_3 = ("claude-sonnet-4", 3.00)
TIER_4 = ("claude-opus-4", 15.00)

# Complexity -> default tier
_COMPLEXITY_TO_TIER: Dict[str, Tuple[str, float]] = {
    "simple": TIER_1,
    "moderate": TIER_2,
    "complex": TIER_3,
    "critical": TIER_4,
}

# Task-type minimum tiers — some tasks demand at least a certain tier
# regardless of stated complexity.  The router picks whichever is HIGHER:
# the complexity-based tier or the task minimum.
_TASK_MIN_TIER: Dict[str, int] = {
    "nightly_review": 3,           # At least Sonnet
    "trade_analysis": 2,           # At least Haiku
    "anomaly_detection": 2,        # At least Haiku
    "parameter_optimization": 2,   # At least Haiku
    "regime_classification": 1,    # Gemini Flash is fine
}

_TIER_BY_NUMBER: Dict[int, Tuple[str, float]] = {
    1: TIER_1,
    2: TIER_2,
    3: TIER_3,
    4: TIER_4,
}

_COMPLEXITY_TIER_NUMBER: Dict[str, int] = {
    "simple": 1,
    "moderate": 2,
    "complex": 3,
    "critical": 4,
}

# Default budget caps
_DEFAULT_DAILY_CAP_USD = 5.0
_DEFAULT_MONTHLY_CAP_USD = 100.0

# Persistence path (same data directory convention as the rest of AEGIS)
_DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
_COST_LOG_PATH = _DATA_DIR / "claude" / "model_router_costs.json"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RouteResult:
    """Result of a routing decision."""
    model: str
    tier: int
    cost_per_1m_tokens: float
    task_type: str
    complexity: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "tier": self.tier,
            "cost_per_1m_tokens": self.cost_per_1m_tokens,
            "task_type": self.task_type,
            "complexity": self.complexity,
        }


@dataclass
class CostEntry:
    """A single cost record."""
    timestamp: str
    model: str
    task_type: str
    complexity: str
    tokens_used: int
    cost_usd: float


# ---------------------------------------------------------------------------
# ModelRouter
# ---------------------------------------------------------------------------

class ModelRouter:
    """Routes LLM calls to the cheapest adequate model.

    Thread-safe.  Maintains cumulative daily and monthly cost counters
    with configurable budget caps.  Persists cost log to disk for
    post-mortem analysis.

    Args:
        daily_cap_usd:   Maximum daily LLM spend before blocking calls.
        monthly_cap_usd: Maximum monthly LLM spend before blocking calls.
    """

    def __init__(
        self,
        daily_cap_usd: float = _DEFAULT_DAILY_CAP_USD,
        monthly_cap_usd: float = _DEFAULT_MONTHLY_CAP_USD,
    ) -> None:
        self._daily_cap = daily_cap_usd
        self._monthly_cap = monthly_cap_usd

        # Cost tracking state
        self._lock = threading.Lock()
        self._daily_cost: float = 0.0
        self._monthly_cost: float = 0.0
        self._current_day: str = ""    # YYYY-MM-DD
        self._current_month: str = ""  # YYYY-MM
        self._cost_log: List[Dict[str, Any]] = []
        self._route_count: int = 0

        # Initialise day/month trackers
        self._roll_period()

        # Load persisted costs for current period
        self._load_persisted_costs()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(self, task_type: str, complexity: str) -> str:
        """Pick the cheapest adequate model for the given task and complexity.

        Args:
            task_type:  One of VALID_TASK_TYPES.
            complexity: One of VALID_COMPLEXITIES.

        Returns:
            Model identifier string (e.g. "claude-3-5-haiku").

        Raises:
            ValueError: If task_type or complexity is unrecognised.
        """
        if task_type not in VALID_TASK_TYPES:
            raise ValueError(
                f"Unknown task_type={task_type!r}. "
                f"Valid: {sorted(VALID_TASK_TYPES)}"
            )
        if complexity not in VALID_COMPLEXITIES:
            raise ValueError(
                f"Unknown complexity={complexity!r}. "
                f"Valid: {list(VALID_COMPLEXITIES)}"
            )

        complexity_tier = _COMPLEXITY_TIER_NUMBER[complexity]
        task_min_tier = _TASK_MIN_TIER.get(task_type, 1)
        effective_tier = max(complexity_tier, task_min_tier)

        model, cost = _TIER_BY_NUMBER[effective_tier]

        with self._lock:
            self._route_count += 1

        log.debug(
            "Route: task=%s complexity=%s -> tier=%d model=%s ($%.2f/1M)",
            task_type, complexity, effective_tier, model, cost,
        )
        return model

    def route_detailed(self, task_type: str, complexity: str) -> RouteResult:
        """Like route() but returns full RouteResult with metadata."""
        model = self.route(task_type, complexity)
        complexity_tier = _COMPLEXITY_TIER_NUMBER[complexity]
        task_min_tier = _TASK_MIN_TIER.get(task_type, 1)
        effective_tier = max(complexity_tier, task_min_tier)
        _, cost = _TIER_BY_NUMBER[effective_tier]
        return RouteResult(
            model=model,
            tier=effective_tier,
            cost_per_1m_tokens=cost,
            task_type=task_type,
            complexity=complexity,
        )

    def record_cost(
        self,
        model: str,
        task_type: str,
        complexity: str,
        tokens_used: int,
        cost_usd: float,
    ) -> None:
        """Record actual cost of an LLM call.

        Thread-safe.  Rolls over daily/monthly counters as needed.

        Args:
            model:       Model identifier used.
            task_type:   Task type string.
            complexity:  Complexity level string.
            tokens_used: Total tokens (input + output).
            cost_usd:    Actual cost in USD.
        """
        with self._lock:
            self._roll_period()
            self._daily_cost += cost_usd
            self._monthly_cost += cost_usd

            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "model": model,
                "task_type": task_type,
                "complexity": complexity,
                "tokens_used": tokens_used,
                "cost_usd": round(cost_usd, 6),
            }
            self._cost_log.append(entry)

        log.info(
            "Cost recorded: model=%s tokens=%d cost=$%.4f (daily=$%.2f/%$.2f, monthly=$%.2f/$%.2f)",
            model, tokens_used, cost_usd,
            self._daily_cost, self._daily_cap,
            self._monthly_cost, self._monthly_cap,
        )

        # Persist asynchronously (best-effort)
        self._persist_costs()

    def check_budget(self, estimated_cost_usd: float) -> bool:
        """Check if budget allows a call with the estimated cost.

        Args:
            estimated_cost_usd: Estimated cost of the upcoming call.

        Returns:
            True if within both daily and monthly caps.
        """
        with self._lock:
            self._roll_period()
            daily_ok = (self._daily_cost + estimated_cost_usd) <= self._daily_cap
            monthly_ok = (self._monthly_cost + estimated_cost_usd) <= self._monthly_cap
            return daily_ok and monthly_ok

    def estimate_cost(self, model: str, total_tokens: int) -> float:
        """Estimate cost for a given model and token count.

        Args:
            model:        Model identifier string.
            total_tokens: Estimated total tokens (input + output).

        Returns:
            Estimated cost in USD.
        """
        # Find cost rate for this model
        for _tier_num, (tier_model, cost_per_1m) in _TIER_BY_NUMBER.items():
            if tier_model == model:
                return (total_tokens / 1_000_000) * cost_per_1m
        # Unknown model — use Tier 3 pricing as safe default
        log.warning("Unknown model %r for cost estimation, using Tier 3 rate", model)
        return (total_tokens / 1_000_000) * TIER_3[1]

    def get_cost_summary(self) -> Dict[str, Any]:
        """Return current cost tracking summary.

        Returns:
            Dict with daily/monthly spend, caps, remaining budget,
            route count, and per-model breakdown.
        """
        with self._lock:
            self._roll_period()

            # Per-model breakdown from today's log
            model_breakdown: Dict[str, float] = {}
            today = self._current_day
            for entry in self._cost_log:
                if entry["timestamp"][:10] == today:
                    m = entry["model"]
                    model_breakdown[m] = model_breakdown.get(m, 0.0) + entry["cost_usd"]

            return {
                "daily_usd": round(self._daily_cost, 4),
                "daily_cap_usd": self._daily_cap,
                "daily_remaining_usd": round(max(0.0, self._daily_cap - self._daily_cost), 4),
                "monthly_usd": round(self._monthly_cost, 4),
                "monthly_cap_usd": self._monthly_cap,
                "monthly_remaining_usd": round(max(0.0, self._monthly_cap - self._monthly_cost), 4),
                "route_count": self._route_count,
                "current_day": self._current_day,
                "current_month": self._current_month,
                "model_breakdown": {k: round(v, 4) for k, v in model_breakdown.items()},
            }

    def reset_daily(self) -> None:
        """Manually reset daily cost counter. Called at start of trading day."""
        with self._lock:
            log.info("Daily cost reset (was $%.4f)", self._daily_cost)
            self._daily_cost = 0.0
            self._current_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def reset_monthly(self) -> None:
        """Manually reset monthly cost counter."""
        with self._lock:
            log.info("Monthly cost reset (was $%.4f)", self._monthly_cost)
            self._monthly_cost = 0.0
            self._current_month = datetime.now(timezone.utc).strftime("%Y-%m")

    def to_dict(self) -> Dict[str, Any]:
        """Serialise router state for nightly_output.json inclusion."""
        summary = self.get_cost_summary()
        summary["tiers"] = {
            str(n): {"model": m, "cost_per_1m": c}
            for n, (m, c) in _TIER_BY_NUMBER.items()
        }
        summary["task_min_tiers"] = dict(_TASK_MIN_TIER)
        return summary

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _roll_period(self) -> None:
        """Roll over daily/monthly counters if the period has changed.

        Must be called with self._lock held.
        """
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        month = now.strftime("%Y-%m")

        if today != self._current_day:
            if self._current_day:
                log.info(
                    "Day rolled %s -> %s (spent $%.4f)",
                    self._current_day, today, self._daily_cost,
                )
            self._daily_cost = 0.0
            self._current_day = today

        if month != self._current_month:
            if self._current_month:
                log.info(
                    "Month rolled %s -> %s (spent $%.4f)",
                    self._current_month, month, self._monthly_cost,
                )
            self._monthly_cost = 0.0
            self._current_month = month

    def _persist_costs(self) -> None:
        """Best-effort write of cost log to disk."""
        try:
            _COST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                data = {
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                    "daily_cost_usd": round(self._daily_cost, 4),
                    "monthly_cost_usd": round(self._monthly_cost, 4),
                    "current_day": self._current_day,
                    "current_month": self._current_month,
                    "entries": self._cost_log[-500:],  # Keep last 500 entries
                }
            with open(_COST_LOG_PATH, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            log.warning("Failed to persist cost log: %s", e)

    def _load_persisted_costs(self) -> None:
        """Load persisted costs for current day/month on startup."""
        if not _COST_LOG_PATH.exists():
            return
        try:
            with open(_COST_LOG_PATH) as f:
                data = json.load(f)

            persisted_day = data.get("current_day", "")
            persisted_month = data.get("current_month", "")

            with self._lock:
                # Restore daily cost if same day
                if persisted_day == self._current_day:
                    self._daily_cost = float(data.get("daily_cost_usd", 0.0))
                    log.info("Restored daily cost: $%.4f", self._daily_cost)

                # Restore monthly cost if same month
                if persisted_month == self._current_month:
                    self._monthly_cost = float(data.get("monthly_cost_usd", 0.0))
                    log.info("Restored monthly cost: $%.4f", self._monthly_cost)

                # Restore log entries
                entries = data.get("entries", [])
                if isinstance(entries, list):
                    self._cost_log = entries

        except Exception as e:
            log.warning("Failed to load persisted costs: %s", e)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_router: Optional[ModelRouter] = None
_router_lock = threading.Lock()


def get_router(
    daily_cap_usd: float = _DEFAULT_DAILY_CAP_USD,
    monthly_cap_usd: float = _DEFAULT_MONTHLY_CAP_USD,
) -> ModelRouter:
    """Get or create the module-level singleton ModelRouter.

    Thread-safe.  The singleton is shared across all callers in the process.

    Args:
        daily_cap_usd:   Maximum daily LLM spend (only used on first call).
        monthly_cap_usd: Maximum monthly LLM spend (only used on first call).

    Returns:
        ModelRouter singleton instance.
    """
    global _router
    with _router_lock:
        if _router is None:
            _router = ModelRouter(
                daily_cap_usd=daily_cap_usd,
                monthly_cap_usd=monthly_cap_usd,
            )
            log.info(
                "ModelRouter initialised (daily_cap=$%.2f, monthly_cap=$%.2f)",
                daily_cap_usd, monthly_cap_usd,
            )
        return _router
