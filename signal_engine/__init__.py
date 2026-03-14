"""NZT-48 Signal Engine — strict + fallback two-layer signal pipeline."""
from signal_engine.engine import SignalEngine, EngineResult
from signal_engine.scoring import PlayScore, compute_play_score
from signal_engine.gates import run_full_gate_funnel, GateOutcome
from signal_engine.state_machine import SignalRecord, SignalTape, SignalState

__all__ = [
    "SignalEngine", "EngineResult",
    "PlayScore", "compute_play_score",
    "run_full_gate_funnel", "GateOutcome",
    "SignalRecord", "SignalTape", "SignalState",
]
