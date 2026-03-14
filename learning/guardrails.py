"""
learning/guardrails.py
=======================
Guardrails for calibration: enforce min sample sizes, max changes, no DataHealth bypass.
"""
from __future__ import annotations
from datetime import datetime, timezone


GUARDRAILS = {
    "min_sample_for_suggestion":   20,
    "min_sample_for_auto_change":  None,   # None = never auto-change
    "max_parameter_change_per_week": 0.05,
    "data_health_bypass":          False,  # NEVER
    "allowed_auto_changes":        [],     # empty = manual review only
}


def check_guardrails(
    param_name: str,
    current: float,
    suggested: float,
    n_outcomes: int,
) -> tuple[bool, str]:
    """
    Check if a calibration suggestion is safe to present (not auto-apply).
    Returns (safe, reason).
    """
    if n_outcomes < GUARDRAILS["min_sample_for_suggestion"]:
        return False, f"Insufficient data: {n_outcomes} < {GUARDRAILS['min_sample_for_suggestion']}"
    if abs(suggested - current) > GUARDRAILS["max_parameter_change_per_week"]:
        return False, f"Change too large: {abs(suggested-current):.3f} > {GUARDRAILS['max_parameter_change_per_week']}"
    if param_name in ("DATA_HEALTH", "MIN_BARS_HARD"):
        return False, "DataHealth/MinBars hard gates cannot be calibrated"
    return True, "OK"


def get_guardrails_status() -> dict:
    return {
        "guardrails": GUARDRAILS,
        "policy":     "Manual review required for all parameter changes. Zero auto-tuning.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
