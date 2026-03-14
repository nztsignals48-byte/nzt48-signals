"""
Closed-Loop Transaction Cost Analysis Engine.
Reconciles predicted vs actual execution costs.
Auto-corrects the EV gate's slippage assumptions.
"""
from __future__ import annotations

import logging
from collections import deque

logger = logging.getLogger("nzt48.tca")


class TransactionCostAnalyzer:
    """Closed-loop TCA feedback.
    If actual > predicted consistently, correction factor > 1.0,
    making EV gate stricter (self-correcting).
    """

    def __init__(self, window: int = 20):
        self._predicted: deque[float] = deque(maxlen=window)
        self._actual: deque[float] = deque(maxlen=window)

    def record(self, predicted_cost_bps: float, actual_cost_bps: float) -> None:
        self._predicted.append(predicted_cost_bps)
        self._actual.append(actual_cost_bps)
        logger.debug("TCA record: predicted=%.1f actual=%.1f", predicted_cost_bps, actual_cost_bps)

    def get_correction_factor(self) -> float:
        """Returns multiplier to apply to predicted costs in EV gate.
        If actual > predicted consistently, this > 1.0, making EV gate stricter.
        """
        if len(self._actual) < 5:
            return 1.0
        avg_predicted = sum(self._predicted) / len(self._predicted)
        avg_actual = sum(self._actual) / len(self._actual)
        if avg_predicted <= 0:
            return 1.0
        factor = max(1.0, avg_actual / avg_predicted)
        if factor > 1.05:
            logger.info("TCA correction: %.2fx (avg predicted=%.1f, avg actual=%.1f)",
                       factor, avg_predicted, avg_actual)
        return factor

    def get_stats(self) -> dict:
        """Return TCA stats for dashboard/monitoring."""
        n = len(self._actual)
        if n == 0:
            return {"sample_size": 0, "correction_factor": 1.0}
        return {
            "sample_size": n,
            "avg_predicted_bps": round(sum(self._predicted) / n, 1),
            "avg_actual_bps": round(sum(self._actual) / n, 1),
            "correction_factor": round(self.get_correction_factor(), 3),
        }

    def reset(self) -> None:
        self._predicted.clear()
        self._actual.clear()
