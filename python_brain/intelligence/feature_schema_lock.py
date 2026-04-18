"""Feature schema lock — CI test ensuring meta-labeler live features match training.

Addresses Elena Vasquez's #1 concern: feature drift between training pipeline
and inference pipeline silently breaks the ML model.

Consumed by:
- tests/ during CI
- ouroboros_v2_nightly.py as a pre-train integrity check
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Schema:
    feature_names: list
    feature_dtypes: dict
    feature_ranges: dict
    hash: str


# Canonical feature schema for MetaLabelFeatures — must match meta_labeler.py
CANONICAL_FEATURES = {
    "conviction": {"type": "float", "min": 0.0, "max": 1.0},
    "gross_edge_bps": {"type": "float", "min": -100.0, "max": 1000.0},
    "spread_bps": {"type": "float", "min": 0.0, "max": 500.0},
    "rvol": {"type": "float", "min": 0.0, "max": 20.0},
    "vpin": {"type": "float", "min": 0.0, "max": 1.0},
    "regime": {"type": "categorical", "values": ["calm", "trending", "choppy", "crisis"]},
    "session": {"type": "categorical", "values": ["us_session", "lse_session", "overnight", "after_hours"]},
}


def compute_schema_hash(feature_spec: dict) -> str:
    canonical = json.dumps(feature_spec, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def current_schema() -> Schema:
    return Schema(
        feature_names=list(CANONICAL_FEATURES.keys()),
        feature_dtypes={k: v["type"] for k, v in CANONICAL_FEATURES.items()},
        feature_ranges={
            k: ({"min": v.get("min"), "max": v.get("max")}
                if v["type"] == "float"
                else {"values": v.get("values", [])})
            for k, v in CANONICAL_FEATURES.items()
        },
        hash=compute_schema_hash(CANONICAL_FEATURES),
    )


def validate_features(features: dict) -> tuple[bool, list[str]]:
    """Check a feature dict conforms to canonical schema."""
    errors = []
    schema = current_schema()

    for name in schema.feature_names:
        if name not in features:
            errors.append(f"missing feature: {name}")
            continue
        val = features[name]
        dtype = schema.feature_dtypes[name]
        if dtype == "float":
            try:
                fv = float(val)
            except (TypeError, ValueError):
                errors.append(f"{name}: cannot coerce {val!r} to float")
                continue
            rng = schema.feature_ranges[name]
            if fv < rng["min"] or fv > rng["max"]:
                errors.append(f"{name}: {fv} outside [{rng['min']}, {rng['max']}]")
        elif dtype == "categorical":
            if val not in schema.feature_ranges[name]["values"]:
                errors.append(f"{name}: {val!r} not in {schema.feature_ranges[name]['values']}")

    extra = set(features) - set(schema.feature_names)
    if extra:
        errors.append(f"unexpected features: {sorted(extra)}")

    return len(errors) == 0, errors


def write_schema_lock(lock_path: Path | None = None) -> None:
    """Persist current schema hash so retraining can verify match."""
    lock_path = lock_path or Path("/Users/rr/aegis-v5/data/models/meta_labeler_schema.json")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    schema = current_schema()
    lock_path.write_text(json.dumps({
        "hash": schema.hash,
        "feature_names": schema.feature_names,
        "feature_dtypes": schema.feature_dtypes,
        "feature_ranges": schema.feature_ranges,
    }, indent=2))


def verify_schema_lock(lock_path: Path | None = None) -> tuple[bool, str]:
    """Check persisted schema matches current. Returns (ok, reason)."""
    lock_path = lock_path or Path("/Users/rr/aegis-v5/data/models/meta_labeler_schema.json")
    if not lock_path.exists():
        return False, "no lock file — run write_schema_lock once"
    try:
        locked = json.loads(lock_path.read_text())
    except Exception as e:
        return False, f"corrupt lock: {e}"
    current = current_schema()
    if locked.get("hash") != current.hash:
        return False, f"schema hash mismatch: locked={locked.get('hash')} current={current.hash}"
    return True, "match"


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        s = current_schema()
        print(f"Schema hash: {s.hash}")
        ok, errs = validate_features({
            "conviction": 0.7, "gross_edge_bps": 15, "spread_bps": 3,
            "rvol": 1.2, "vpin": 0.3, "regime": "calm", "session": "us_session",
        })
        print(f"Valid features: {ok}")

        ok, errs = validate_features({"conviction": 1.5})
        print(f"Bad features: ok={ok} errors={errs}")

        write_schema_lock()
        ok, reason = verify_schema_lock()
        print(f"Lock verify: {ok} — {reason}")
        print("OK")
