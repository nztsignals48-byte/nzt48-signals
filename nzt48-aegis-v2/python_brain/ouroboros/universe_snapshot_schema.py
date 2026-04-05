"""Universe Snapshot Schema — Pydantic models for the canonical universe snapshot.

Every dynamic universe run produces exactly ONE snapshot artifact containing the
full audit record: discovery counts, admissibility outcomes, contract resolution,
Gemini scores, Claude vetoes, rotation plan, publish decision, and Rust ack.

Saved to: data/universe/dynamic_universe_snapshot.json

Usage:
    from python_brain.ouroboros.universe_snapshot_schema import (
        UniverseSnapshot, GeminiScore, ClaudeVeto, LifecycleTransition,
        RotationMetrics, GuardrailResult,
    )
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class GeminiScore(BaseModel):
    """Structured output from Gemini ranking phase."""
    symbol: str
    con_id: int = 0
    score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    reason_codes: List[str] = Field(default_factory=list)
    sector_tags: List[str] = Field(default_factory=list)
    theme_tags: List[str] = Field(default_factory=list)
    liquidity_assessment: str = ""  # "high", "medium", "low"
    catalyst_tags: List[str] = Field(default_factory=list)
    reject_flag: bool = False
    timestamp: str = ""


class ClaudeVeto(BaseModel):
    """Structured output from Claude vetting phase."""
    symbol: str
    recommendation: Literal["allow", "downrank", "veto", "review_required"] = "allow"
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    reasons: List[str] = Field(default_factory=list)
    session_relevance: float = Field(ge=0.0, le=1.0, default=0.5)
    regime_relevance: float = Field(ge=0.0, le=1.0, default=0.5)
    timestamp: str = ""


class LifecycleTransition(BaseModel):
    """Record of a single lifecycle state transition."""
    symbol: str
    con_id: int = 0
    from_state: str
    to_state: str
    reason_code: str
    timestamp: str = ""


class RotationMetrics(BaseModel):
    """Metrics from the rotation planning phase."""
    core_200_count: int = 0
    tactical_50_count: int = 0
    live_100_count: int = 0
    live_100_additions: int = 0
    live_100_removals: int = 0
    shortlist_churn: int = 0
    strategy_protected_count: int = 0
    min_residency_holds: int = 0
    cooldown_blocks: int = 0
    exchange_distribution: Dict[str, int] = Field(default_factory=dict)
    leveraged_count: int = 0
    inverse_count: int = 0


class GuardrailResult(BaseModel):
    """Result from a single guardrail check."""
    name: str
    passed: bool
    value: Any = None
    threshold: Any = None
    reason_code: str = ""


class UniverseSnapshot(BaseModel):
    """Canonical audit record for every dynamic universe run."""
    # ── Run Identity ──
    run_id: str
    run_mode: Literal["bootstrap", "session", "prep_next", "full", "dry_run"]
    universe_version: int = 0
    started_at: str
    completed_at: str = ""

    # ── Discovery ──
    exchange_coverage: Dict[str, int] = Field(default_factory=dict)  # exchange → count
    raw_discoveries: int = 0
    exchanges_scanned: List[str] = Field(default_factory=list)
    exchanges_failed: List[str] = Field(default_factory=list)

    # ── Admissibility ──
    admissible_count: int = 0
    rejected_count: int = 0
    quarantine_count: int = 0
    reject_reasons: Dict[str, int] = Field(default_factory=dict)  # reason_code → count

    # ── Resolution ──
    resolved_count: int = 0
    resolution_failures: int = 0
    new_con_ids: int = 0

    # ── Ranking ──
    gemini_scores: Optional[List[GeminiScore]] = None
    gemini_degraded: bool = False
    claude_vetoes: Optional[List[ClaudeVeto]] = None
    claude_degraded: bool = False

    # ── Rotation ──
    rotation_metrics: Optional[RotationMetrics] = None

    # ── Shortlist / Live ──
    shortlist_250_count: int = 0
    live_100_count: int = 0
    final_watchlist_count: int = 0

    # ── Publish ──
    publish_decision: Literal["published", "blocked", "degraded", "dry_run", "rolled_back"] = "blocked"
    publish_reason_codes: List[str] = Field(default_factory=list)
    guardrail_results: List[GuardrailResult] = Field(default_factory=list)
    artifact_hashes: Dict[str, str] = Field(default_factory=dict)  # filename → sha256

    # ── Rust Ack ──
    rust_reload_ack: bool = False
    rust_ack_payload: Dict[str, Any] = Field(default_factory=dict)
    rust_ack_hash_match: bool = False

    # ── Lifecycle ──
    lifecycle_changes: List[LifecycleTransition] = Field(default_factory=list)

    # ── Degraded ──
    degraded_reasons: List[str] = Field(default_factory=list)
    emergency_baseline_active: bool = False

    # ── Metrics ──
    metrics: Dict[str, Any] = Field(default_factory=dict)
    duration_seconds: float = 0.0
