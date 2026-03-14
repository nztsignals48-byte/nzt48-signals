"""
G-13: Drought-Regime Contradiction Detection.
Cross-checks drought state vs regime to detect system bugs.

TRENDING + drought → WARNING "data feed or gate issue"
EXPANSION + drought → "gates too tight"
RANGE + drought → expected (no contradiction)
SHOCK + no drought → "counter bug" (should always be drought in SHOCK)

Replaces killed G-12 (Drought State Machine) — we do NOT lower quality
thresholds to force trades. Time elapsed does not increase expected value.
"""

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class DroughtContradiction:
    """Represents a detected contradiction between drought and regime state."""
    __slots__ = ('regime', 'drought_state', 'severity', 'message', 'timestamp')

    def __init__(self, regime: str, drought_state: str, severity: str, message: str):
        self.regime = regime
        self.drought_state = drought_state
        self.severity = severity  # 'INFO', 'WARNING', 'ERROR'
        self.message = message
        self.timestamp = datetime.utcnow()


class DroughtRegimeMonitor:
    """Monitors for contradictions between drought state and market regime.

    If the market is trending strongly but no signals are firing, something
    is wrong — either data feeds are broken or gates are miscalibrated.
    This is a diagnostic tool, NOT a mechanism to lower standards.
    """

    # Regimes that should produce signals in non-drought conditions
    _SIGNAL_EXPECTED_REGIMES = {
        "TRENDING_UP_STRONG", "TRENDING_UP_MOD",
        "TRENDING_DOWN_STRONG", "TRENDING_DOWN_MOD",
    }
    # Regimes where drought is expected
    _DROUGHT_EXPECTED_REGIMES = {
        "RANGE_BOUND", "RISK_OFF", "SHOCK",
    }

    def __init__(self):
        self._last_contradiction: Optional[DroughtContradiction] = None
        self._contradiction_count = 0

    def check(self, regime: str, drought_state: str,
              signals_fired_today: int = 0) -> Optional[DroughtContradiction]:
        """Check for contradictions between regime and drought state.

        Args:
            regime: Current market regime (e.g. 'TRENDING_UP_STRONG')
            drought_state: Current drought state (e.g. 'DROUGHT', 'NORMAL')
            signals_fired_today: Number of signals fired in current session

        Returns:
            DroughtContradiction if detected, None otherwise
        """
        is_drought = drought_state in ("DROUGHT", "CRITICAL")
        contradiction = None

        if regime in self._SIGNAL_EXPECTED_REGIMES and is_drought:
            # Trending market + no signals = something broken
            contradiction = DroughtContradiction(
                regime=regime,
                drought_state=drought_state,
                severity="WARNING",
                message=(
                    f"CONTRADICTION: {regime} regime but {drought_state} state. "
                    f"Signals today: {signals_fired_today}. "
                    f"Possible data feed issue or gates too tight."
                ),
            )

        elif regime == "SHOCK" and not is_drought and signals_fired_today > 0:
            # SHOCK + signals firing = counter bug (should be blocked)
            contradiction = DroughtContradiction(
                regime=regime,
                drought_state=drought_state,
                severity="ERROR",
                message=(
                    f"CONTRADICTION: SHOCK regime but signals are firing "
                    f"({signals_fired_today} today). RISK_OFF gate may be broken."
                ),
            )

        elif regime == "RANGE_BOUND" and is_drought:
            # Range-bound + drought is expected — log for info only
            logger.debug(
                "Expected: RANGE_BOUND + %s (no signals in range market)",
                drought_state,
            )
            return None

        if contradiction:
            self._last_contradiction = contradiction
            self._contradiction_count += 1
            log_fn = logger.error if contradiction.severity == "ERROR" else logger.warning
            log_fn(
                "DROUGHT MONITOR [%s]: %s",
                contradiction.severity,
                contradiction.message,
            )

        return contradiction

    @property
    def last_contradiction(self) -> Optional[DroughtContradiction]:
        return self._last_contradiction

    @property
    def total_contradictions(self) -> int:
        return self._contradiction_count

    def reset(self) -> None:
        self._last_contradiction = None
        self._contradiction_count = 0
