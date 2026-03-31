"""ml/model_registry.py — Book 65: Model Registry with 7-Stage Lifecycle.

Tracks all ML models through DEVELOPMENT → TRAINING → VALIDATION →
STAGING → PRODUCTION → MONITORING → RETIRED lifecycle stages.

JSON-file backed (no DuckDB dependency). Two persistence files:
  - /app/data/model_registry.json  — Current state of all models
  - /app/data/model_events.ndjson  — Append-only lifecycle event log

Key features:
  - UUID-based model identification
  - Validated stage transitions (no skipping stages)
  - Champion/challenger model tracking per strategy
  - Prediction drift scoring for model degradation detection
  - Full lifecycle audit trail

Bridge.py integration:
    from python_brain.ml.model_registry import ModelRegistry, ModelMetadata, ModelStage, ModelType

    registry = ModelRegistry()
    # Register a new model
    meta = ModelMetadata(
        name="lgbm_vanguard_v3",
        model_type=ModelType.LIGHTGBM,
        strategy="VanguardSniper",
    )
    model_id = registry.register(meta)

    # Promote through stages
    registry.promote(model_id, ModelStage.TRAINING, "Started training run")
    registry.promote(model_id, ModelStage.VALIDATION, "Training complete, AUC=0.62")
    registry.promote(model_id, ModelStage.STAGING, "Validation passed")
    registry.promote(model_id, ModelStage.PRODUCTION, "Shadow period clean")

    # Query champion
    champion = registry.get_champion("VanguardSniper")
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

log = logging.getLogger(__name__)

__all__ = [
    "ModelStage",
    "ModelType",
    "ModelMetadata",
    "ModelRegistry",
]

# ── Persistence Paths ──────────────────────────────────────────────────

DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
REGISTRY_PATH = DATA_DIR / "model_registry.json"
EVENTS_PATH = DATA_DIR / "model_events.ndjson"


# ── Enums ──────────────────────────────────────────────────────────────

class ModelStage(Enum):
    """7-stage model lifecycle."""
    DEVELOPMENT = "development"
    TRAINING = "training"
    VALIDATION = "validation"
    STAGING = "staging"
    PRODUCTION = "production"
    MONITORING = "monitoring"
    RETIRED = "retired"


class ModelType(Enum):
    """Supported model architectures."""
    XGBOOST = "xgboost"
    LIGHTGBM = "lightgbm"
    TCN = "tcn"
    TFT = "tft"
    LINEAR = "linear"
    ENSEMBLE = "ensemble"
    GARCH = "garch"
    KALMAN = "kalman"


# ── Valid Stage Transitions ────────────────────────────────────────────

VALID_TRANSITIONS: Dict[ModelStage, List[ModelStage]] = {
    ModelStage.DEVELOPMENT: [ModelStage.TRAINING],
    ModelStage.TRAINING: [ModelStage.VALIDATION],
    ModelStage.VALIDATION: [ModelStage.STAGING, ModelStage.RETIRED],
    ModelStage.STAGING: [ModelStage.PRODUCTION, ModelStage.RETIRED],
    ModelStage.PRODUCTION: [ModelStage.MONITORING, ModelStage.RETIRED],
    ModelStage.MONITORING: [ModelStage.PRODUCTION, ModelStage.RETIRED],
    ModelStage.RETIRED: [],  # Terminal state
}


# ── Dataclass ──────────────────────────────────────────────────────────

@dataclass
class ModelMetadata:
    """Full metadata for a registered model."""
    # Identity
    model_id: str = ""
    name: str = ""
    version: str = "1.0.0"
    model_type: ModelType = ModelType.LIGHTGBM
    stage: ModelStage = ModelStage.DEVELOPMENT
    strategy: str = ""

    # Training metadata
    training_samples: int = 0
    training_features: int = 0
    training_start: str = ""
    training_end: str = ""
    training_duration_s: float = 0.0
    hyperparameters: Dict[str, Any] = field(default_factory=dict)

    # Validation metrics
    val_auc: float = 0.0
    val_brier: float = 1.0
    val_sharpe: float = 0.0
    val_accuracy: float = 0.0
    val_f1: float = 0.0
    val_metrics: Dict[str, float] = field(default_factory=dict)

    # Deployment
    onnx_path: str = ""
    onnx_size_bytes: int = 0
    inference_latency_ms: float = 0.0

    # Lifecycle dates
    created_at: str = ""
    promoted_at: str = ""
    retired_at: str = ""

    # Drift and health
    drift_score: float = 0.0
    last_drift_check: str = ""
    predictions_served: int = 0

    # Tags for filtering
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-safe dict."""
        d = asdict(self)
        d["model_type"] = self.model_type.value
        d["stage"] = self.stage.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ModelMetadata":
        """Deserialize from JSON dict."""
        d = dict(d)  # Shallow copy to avoid mutating original
        if "model_type" in d and isinstance(d["model_type"], str):
            d["model_type"] = ModelType(d["model_type"])
        if "stage" in d and isinstance(d["stage"], str):
            d["stage"] = ModelStage(d["stage"])
        # Filter to known fields only
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)


# ── Registry ───────────────────────────────────────────────────────────

class ModelRegistry:
    """JSON-file backed model registry with lifecycle management.

    Thread-safe for single-process use (nightly pipeline).
    Loads on init, writes on every mutation.
    """

    def __init__(self, registry_path: Optional[str] = None,
                 events_path: Optional[str] = None):
        self._registry_path = Path(registry_path) if registry_path else REGISTRY_PATH
        self._events_path = Path(events_path) if events_path else EVENTS_PATH
        self._models: Dict[str, ModelMetadata] = {}
        self._load()

    # ── Public API ─────────────────────────────────────────────────────

    def register(self, metadata: ModelMetadata) -> str:
        """Register a new model in DEVELOPMENT stage.

        Args:
            metadata: ModelMetadata with at least name and model_type set.

        Returns:
            Assigned model_id (UUID).
        """
        model_id = str(uuid.uuid4())
        metadata.model_id = model_id
        metadata.stage = ModelStage.DEVELOPMENT
        metadata.created_at = datetime.now(timezone.utc).isoformat()

        if not metadata.name:
            metadata.name = f"{metadata.model_type.value}_{model_id[:8]}"

        self._models[model_id] = metadata
        self._save()
        self._append_event({
            "event": "registered",
            "model_id": model_id,
            "name": metadata.name,
            "model_type": metadata.model_type.value,
            "strategy": metadata.strategy,
            "stage": ModelStage.DEVELOPMENT.value,
            "timestamp": metadata.created_at,
        })

        log.info("Registered model %s (%s) for strategy=%s",
                 metadata.name, model_id[:8], metadata.strategy or "global")
        return model_id

    def promote(self, model_id: str, to_stage: ModelStage, reason: str = "") -> bool:
        """Promote a model to the next lifecycle stage.

        Validates the transition is legal per VALID_TRANSITIONS.

        Args:
            model_id: UUID of the model.
            to_stage: Target stage.
            reason: Human-readable reason for promotion.

        Returns:
            True if promotion succeeded, False if invalid.
        """
        meta = self._models.get(model_id)
        if meta is None:
            log.error("promote(): model_id=%s not found", model_id[:8] if model_id else "None")
            return False

        current = meta.stage
        valid_targets = VALID_TRANSITIONS.get(current, [])

        if to_stage not in valid_targets:
            log.warning(
                "Invalid transition %s → %s for model %s. Valid: %s",
                current.value, to_stage.value, meta.name,
                [s.value for s in valid_targets],
            )
            return False

        old_stage = current
        meta.stage = to_stage
        now = datetime.now(timezone.utc).isoformat()
        meta.promoted_at = now

        if to_stage == ModelStage.RETIRED:
            meta.retired_at = now

        # If promoting to PRODUCTION, retire the current champion for this strategy
        if to_stage == ModelStage.PRODUCTION and meta.strategy:
            self._retire_previous_champion(model_id, meta.strategy, reason)

        self._save()
        self._append_event({
            "event": "promoted",
            "model_id": model_id,
            "name": meta.name,
            "from_stage": old_stage.value,
            "to_stage": to_stage.value,
            "reason": reason,
            "timestamp": now,
        })

        log.info("Promoted %s: %s → %s (reason: %s)",
                 meta.name, old_stage.value, to_stage.value, reason or "none")
        return True

    def get_champion(self, strategy: str) -> Optional[ModelMetadata]:
        """Get the PRODUCTION model for a given strategy.

        If multiple models are in PRODUCTION for the same strategy
        (shouldn't happen, but defensive), returns the most recently promoted.

        Args:
            strategy: Strategy name (e.g. "VanguardSniper").

        Returns:
            ModelMetadata or None if no production model exists.
        """
        champions = [
            m for m in self._models.values()
            if m.strategy == strategy and m.stage == ModelStage.PRODUCTION
        ]
        if not champions:
            return None
        # Most recently promoted wins
        champions.sort(key=lambda m: m.promoted_at or "", reverse=True)
        return champions[0]

    def get_challengers(self, strategy: str) -> List[ModelMetadata]:
        """Get STAGING models for a given strategy (challenger candidates).

        Args:
            strategy: Strategy name.

        Returns:
            List of ModelMetadata in STAGING for this strategy, sorted by val_auc desc.
        """
        challengers = [
            m for m in self._models.values()
            if m.strategy == strategy and m.stage == ModelStage.STAGING
        ]
        challengers.sort(key=lambda m: m.val_auc, reverse=True)
        return challengers

    def retire(self, model_id: str, reason: str = "") -> bool:
        """Move a model to RETIRED stage (terminal).

        Can retire from any non-RETIRED stage.

        Args:
            model_id: UUID of the model.
            reason: Why it's being retired.

        Returns:
            True if retired, False if not found or already retired.
        """
        meta = self._models.get(model_id)
        if meta is None:
            log.error("retire(): model_id=%s not found", model_id[:8] if model_id else "None")
            return False

        if meta.stage == ModelStage.RETIRED:
            log.info("Model %s already retired", meta.name)
            return False

        old_stage = meta.stage
        # Retirement is allowed from any stage (not just via VALID_TRANSITIONS)
        # This is a safety mechanism — we must always be able to retire a model
        meta.stage = ModelStage.RETIRED
        now = datetime.now(timezone.utc).isoformat()
        meta.retired_at = now
        meta.promoted_at = now

        self._save()
        self._append_event({
            "event": "retired",
            "model_id": model_id,
            "name": meta.name,
            "from_stage": old_stage.value,
            "reason": reason,
            "timestamp": now,
        })

        log.info("Retired %s from %s (reason: %s)",
                 meta.name, old_stage.value, reason or "none")
        return True

    def update_drift_score(self, model_id: str, score: float) -> bool:
        """Update the prediction drift score for a model.

        Drift score is a measure of how much the model's predictions have
        shifted from its validation distribution. Higher = more drift.
        Typically measured via PSI (Population Stability Index) or
        KS statistic on predicted probabilities.

        Args:
            model_id: UUID of the model.
            score: Drift score (0.0 = no drift, >0.25 = severe drift).

        Returns:
            True if updated, False if model not found.
        """
        meta = self._models.get(model_id)
        if meta is None:
            log.error("update_drift_score(): model_id=%s not found",
                      model_id[:8] if model_id else "None")
            return False

        old_score = meta.drift_score
        meta.drift_score = float(score)
        meta.last_drift_check = datetime.now(timezone.utc).isoformat()

        self._save()
        self._append_event({
            "event": "drift_update",
            "model_id": model_id,
            "name": meta.name,
            "old_drift": round(old_score, 4),
            "new_drift": round(score, 4),
            "timestamp": meta.last_drift_check,
        })

        # Auto-escalate: move to MONITORING if drift is high and model is in PRODUCTION
        if score > 0.25 and meta.stage == ModelStage.PRODUCTION:
            log.warning("High drift (%.3f) for production model %s — escalating to MONITORING",
                        score, meta.name)
            self.promote(model_id, ModelStage.MONITORING,
                         f"Auto-escalated: drift={score:.3f} > 0.25 threshold")

        return True

    def get_history(self, model_id: str) -> List[Dict[str, Any]]:
        """Get full lifecycle event history for a model.

        Reads from the append-only events log.

        Args:
            model_id: UUID of the model.

        Returns:
            List of event dicts, chronologically ordered.
        """
        events = []
        if not self._events_path.exists():
            return events

        try:
            with open(self._events_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if event.get("model_id") == model_id:
                            events.append(event)
                    except json.JSONDecodeError:
                        continue
        except OSError as e:
            log.error("Failed to read events file: %s", e)

        return events

    def summary(self) -> Dict[str, Any]:
        """Get summary statistics of the registry.

        Returns:
            Dict with counts by stage, strategy, and model type.
        """
        by_stage: Dict[str, int] = {}
        by_strategy: Dict[str, int] = {}
        by_type: Dict[str, int] = {}
        production_models: List[Dict[str, str]] = []

        for meta in self._models.values():
            stage_name = meta.stage.value
            by_stage[stage_name] = by_stage.get(stage_name, 0) + 1

            if meta.strategy:
                by_strategy[meta.strategy] = by_strategy.get(meta.strategy, 0) + 1

            type_name = meta.model_type.value
            by_type[type_name] = by_type.get(type_name, 0) + 1

            if meta.stage == ModelStage.PRODUCTION:
                production_models.append({
                    "model_id": meta.model_id[:8],
                    "name": meta.name,
                    "strategy": meta.strategy,
                    "val_auc": round(meta.val_auc, 4),
                    "drift_score": round(meta.drift_score, 4),
                })

        return {
            "total_models": len(self._models),
            "by_stage": by_stage,
            "by_strategy": by_strategy,
            "by_type": by_type,
            "production_models": production_models,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_model(self, model_id: str) -> Optional[ModelMetadata]:
        """Get metadata for a specific model.

        Args:
            model_id: UUID of the model.

        Returns:
            ModelMetadata or None.
        """
        return self._models.get(model_id)

    def get_models_by_stage(self, stage: ModelStage) -> List[ModelMetadata]:
        """Get all models in a given stage.

        Args:
            stage: ModelStage to filter by.

        Returns:
            List of ModelMetadata in that stage.
        """
        return [m for m in self._models.values() if m.stage == stage]

    def get_models_by_strategy(self, strategy: str) -> List[ModelMetadata]:
        """Get all models for a given strategy (any stage).

        Args:
            strategy: Strategy name.

        Returns:
            List of ModelMetadata for that strategy.
        """
        return [m for m in self._models.values() if m.strategy == strategy]

    # ── Private Helpers ────────────────────────────────────────────────

    def _retire_previous_champion(self, new_champion_id: str,
                                   strategy: str, reason: str) -> None:
        """When a new model is promoted to PRODUCTION, retire the old champion."""
        for model_id, meta in self._models.items():
            if (model_id != new_champion_id
                    and meta.strategy == strategy
                    and meta.stage == ModelStage.PRODUCTION):
                self.retire(
                    model_id,
                    f"Replaced by {new_champion_id[:8]}: {reason}",
                )

    def _load(self) -> None:
        """Load registry from JSON file."""
        if not self._registry_path.exists():
            log.info("No registry file at %s — starting fresh", self._registry_path)
            self._models = {}
            return

        try:
            with open(self._registry_path, "r") as f:
                data = json.load(f)

            models_list = data.get("models", [])
            self._models = {}
            for d in models_list:
                try:
                    meta = ModelMetadata.from_dict(d)
                    if meta.model_id:
                        self._models[meta.model_id] = meta
                except Exception as e:
                    log.warning("Skipping malformed model entry: %s", e)

            log.info("Loaded %d models from registry", len(self._models))
        except json.JSONDecodeError as e:
            log.error("Corrupt registry file %s: %s — starting fresh",
                      self._registry_path, e)
            self._models = {}
        except OSError as e:
            log.error("Failed to read registry: %s", e)
            self._models = {}

    def _save(self) -> None:
        """Persist registry to JSON file (atomic write via tmp + rename)."""
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)

        models_list = [m.to_dict() for m in self._models.values()]
        data = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "model_count": len(models_list),
            "models": models_list,
        }

        tmp_path = self._registry_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(str(tmp_path), str(self._registry_path))
        except OSError as e:
            log.error("Failed to save registry: %s", e)
            # Clean up tmp file on failure
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _append_event(self, event: Dict[str, Any]) -> None:
        """Append event to NDJSON events log (append-only, crash-safe)."""
        self._events_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(self._events_path, "a") as f:
                f.write(json.dumps(event, default=str) + "\n")
        except OSError as e:
            log.error("Failed to append event: %s", e)
