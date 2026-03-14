"""
learning/calibration.py
========================
Bounded calibration suggestions.
Rules:
- No auto-changes unless min sample threshold met.
- Max 1 parameter change per week per parameter.
- All suggestions bounded within hard safety limits.
- DataHealth gate NEVER bypassed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from learning.attribution import AttributionEngine, AttributionSlice

logger = logging.getLogger("nzt48.learning.calibration")

# Hard bounds — these cannot be exceeded by calibration suggestions
PARAM_BOUNDS = {
    "STRICT_MIN_RVOL":     (0.55, 0.90),
    "STRICT_MIN_RR":       (1.20, 1.80),
    "STRICT_MOMENTUM_MIN": (0.40, 0.65),
    "STRICT_MIN_ATR_PCT":  (0.60, 1.50),
}
MIN_SAMPLE_THRESHOLD = 20   # minimum outcomes before suggesting a change
MAX_CHANGE_PER_CYCLE = 0.05  # maximum parameter move per calibration cycle


@dataclass
class CalibrationSuggestion:
    param_name:     str
    current:        float
    suggested:      float
    bounded:        bool
    evidence:       str      # e.g. "20 outcomes: win_rate=0.65 at RVOL>0.70"
    expected_effect: str
    safe:           bool     # True if suggestion is within bounds and has sufficient evidence
    generated_at:   str = ""

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


class CalibrationEngine:
    """Produces bounded calibration suggestions from attribution data."""

    def __init__(self):
        self._attribution = AttributionEngine()

    def get_suggestions(self) -> list[CalibrationSuggestion]:
        """
        Compute bounded calibration suggestions.
        Returns empty list (not error) if insufficient data.
        """
        summary = self._attribution.get_summary()
        suggestions = []

        if summary["n_outcomes_recorded"] < MIN_SAMPLE_THRESHOLD:
            logger.info("[CALIBRATION] insufficient data (%d < %d) — no suggestions",
                        summary["n_outcomes_recorded"], MIN_SAMPLE_THRESHOLD)
            return suggestions

        # Analyse strategy attribution for RVOL-related failures
        strat_slices = summary.get("strategy_attribution", [])
        for sl in strat_slices:
            if sl.get("calibration_status") == "NEEDS_DATA":
                continue
            win_rate = sl.get("win_rate", 0.0)
            n_outcomes = sl.get("n_outcomes", 0)
            if n_outcomes < MIN_SAMPLE_THRESHOLD:
                continue

            # If win rate is high and we have enough data, can suggest loosening RVOL
            if win_rate > 0.60 and n_outcomes >= MIN_SAMPLE_THRESHOLD:
                current_rvol = 0.80
                suggested = min(current_rvol - 0.03, PARAM_BOUNDS["STRICT_MIN_RVOL"][1])
                suggested = max(suggested, PARAM_BOUNDS["STRICT_MIN_RVOL"][0])
                if abs(suggested - current_rvol) <= MAX_CHANGE_PER_CYCLE:
                    suggestions.append(CalibrationSuggestion(
                        param_name="STRICT_MIN_RVOL",
                        current=current_rvol,
                        suggested=round(suggested, 3),
                        bounded=True,
                        evidence=f"{n_outcomes} outcomes: win_rate={win_rate:.2f} for {sl.get('value')}",
                        expected_effect="May admit 1-2 more signals per session at margin",
                        safe=True,
                        generated_at=datetime.now(timezone.utc).isoformat(),
                    ))

        return suggestions[:3]   # cap at 3 suggestions per cycle

    def get_api_response(self) -> dict:
        suggestions = self.get_suggestions()
        attribution  = self._attribution.get_summary()
        return {
            "suggestions":     [s.to_dict() for s in suggestions],
            "attribution":     attribution,
            "min_sample_threshold": MIN_SAMPLE_THRESHOLD,
            "calibration_note": (
                "No auto-changes are made automatically. "
                "All suggestions require human review and are bounded within safety limits."
            ),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
