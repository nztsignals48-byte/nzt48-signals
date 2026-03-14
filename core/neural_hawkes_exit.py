"""
L-03: Neural Hawkes Exit Engine -- LSTM (Phase Q3-Q4 skeleton).

Models event intensity lambda(t) for 4 event types:
  1. Adverse price movement
  2. Spread blowout
  3. Volume spike (exhaustion)
  4. Cross-asset contagion

Exit thresholds:
  P_exit > 0.85  ->  IMMEDIATE_EXIT
  P_exit > 0.60  ->  TIGHTEN_STOP
  P_exit > 0.40  ->  TIGHTEN_TRAIL
  P_exit <= 0.40 ->  HOLD

Requires ITCH-level data feed for proper event-intensity modeling.
Full implementation scheduled for Phase Q3-Q4.
"""
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ── Event Types ─────────────────────────────────────────────────────────────

class HawkesEventType(Enum):
    """The four event types whose intensity drives exit probability."""
    ADVERSE_PRICE = "adverse_price"
    SPREAD_BLOWOUT = "spread_blowout"
    VOLUME_SPIKE = "volume_spike"
    CROSS_ASSET_CONTAGION = "cross_asset_contagion"


# ── Exit Actions ────────────────────────────────────────────────────────────

class ExitAction(Enum):
    """Discrete exit action recommended by the model."""
    HOLD = "hold"
    TIGHTEN_TRAIL = "tighten_trail"
    TIGHTEN_STOP = "tighten_stop"
    IMMEDIATE_EXIT = "immediate_exit"


# ── Signal ──────────────────────────────────────────────────────────────────

@dataclass
class HawkesExitSignal:
    """Output of a Neural Hawkes exit prediction."""
    ticker: str
    p_exit: float
    action: ExitAction
    event_intensities: dict  # {HawkesEventType.value: lambda_t}


# ── Thresholds ──────────────────────────────────────────────────────────────

EXIT_THRESHOLD_IMMEDIATE: float = 0.85
EXIT_THRESHOLD_STOP: float = 0.60
EXIT_THRESHOLD_TRAIL: float = 0.40


def _classify_exit(p_exit: float) -> ExitAction:
    """Map exit probability to discrete action."""
    if p_exit > EXIT_THRESHOLD_IMMEDIATE:
        return ExitAction.IMMEDIATE_EXIT
    elif p_exit > EXIT_THRESHOLD_STOP:
        return ExitAction.TIGHTEN_STOP
    elif p_exit > EXIT_THRESHOLD_TRAIL:
        return ExitAction.TIGHTEN_TRAIL
    return ExitAction.HOLD


# ── Engine ──────────────────────────────────────────────────────────────────

class NeuralHawkesExit:
    """
    LSTM-based exit engine (Phase Q3-Q4 skeleton).

    The full implementation will:
      1. Ingest tick-level events (price moves, spread changes, volume, etc.)
      2. Feed them into a trained LSTM that models Hawkes process intensities.
      3. Output P(exit) for each open position.
      4. Map P(exit) to one of the four ExitActions.

    TODO (Q3-Q4):
      - Implement LSTM model architecture (PyTorch).
      - Build training pipeline on historical ITCH data.
      - Serialize model weights and load at init.
      - Wire into the Chandelier Exit / Profit Ladder as a modifier.
    """

    def __init__(self) -> None:
        self._model = None  # Placeholder for trained LSTM model
        self._enabled: bool = False
        logger.info(
            "NeuralHawkesExit: skeleton initialized (Q3-Q4). "
            "Model not loaded — predictions will return p_exit=0.0"
        )

    @property
    def is_enabled(self) -> bool:
        """True when a trained model is loaded and ready for inference."""
        return self._enabled and self._model is not None

    def load_model(self, model_path: str) -> bool:
        """
        Load a trained LSTM model from disk.

        TODO: Implement with torch.load() or equivalent.
        """
        logger.warning(
            "NeuralHawkesExit.load_model(%s): not implemented (Q3-Q4 skeleton)",
            model_path,
        )
        return False

    def predict_exit(
        self,
        ticker: str,
        features: dict,
    ) -> HawkesExitSignal:
        """
        Predict exit probability for *ticker* given current features.

        In the skeleton phase, always returns p_exit=0.0 (HOLD).

        Parameters
        ----------
        ticker : str
            Instrument symbol.
        features : dict
            Feature dict expected to contain:
              - price_series: list[float]
              - spread_series: list[float]
              - volume_series: list[float]
              - cross_asset_returns: dict[str, float]
        """
        if not self.is_enabled:
            # Skeleton: no model loaded, return safe default
            return HawkesExitSignal(
                ticker=ticker,
                p_exit=0.0,
                action=ExitAction.HOLD,
                event_intensities={
                    et.value: 0.0 for et in HawkesEventType
                },
            )

        # TODO: Replace with actual LSTM inference
        # raw_intensities = self._model.forward(features)
        # p_exit = aggregate(raw_intensities)
        p_exit = 0.0
        action = _classify_exit(p_exit)

        return HawkesExitSignal(
            ticker=ticker,
            p_exit=p_exit,
            action=action,
            event_intensities={et.value: 0.0 for et in HawkesEventType},
        )

    def summary(self) -> dict:
        """JSON-serializable snapshot for telemetry."""
        return {
            "enabled": self._enabled,
            "model_loaded": self._model is not None,
            "thresholds": {
                "immediate": EXIT_THRESHOLD_IMMEDIATE,
                "stop": EXIT_THRESHOLD_STOP,
                "trail": EXIT_THRESHOLD_TRAIL,
            },
        }
