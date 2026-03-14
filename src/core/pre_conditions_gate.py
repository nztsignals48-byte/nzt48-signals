"""Phase 8: Pre-Conditions Gate (Final qualification before order submission)"""
from dataclasses import dataclass

@dataclass
class PreCondResult:
    passed: bool
    reason: str

class PreConditionsGate:
    def __init__(self, cooldown_seconds=600):
        self.cooldown_seconds = cooldown_seconds
        self.last_loss_time = None
        self.consecutive_losses = 0

    def check(
        self,
        confidence: float,
        confidence_threshold: float,
        isa_audit_pass: bool,
        seconds_since_loss: float,
        recent_losses: int
    ) -> PreCondResult:
        """Final gate before order submission"""
        if not isa_audit_pass:
            return PreCondResult(False, "ISA audit failed")
        if confidence < confidence_threshold:
            return PreCondResult(False, f"Confidence {confidence:.1f} < {confidence_threshold:.1f}")
        if recent_losses >= 3 and seconds_since_loss < self.cooldown_seconds:
            return PreCondResult(False, f"Cooldown after 3 losses ({seconds_since_loss:.0f}s < {self.cooldown_seconds}s)")
        return PreCondResult(True, "All pre-conditions met")

if __name__ == "__main__":
    gate = PreConditionsGate()
    r = gate.check(7.5, 6.5, True, 700, 0)
    print(f"✓ Pre-conditions gate: {r.passed}, reason: {r.reason}")
    print("✅ Phase 8 (Pre-Conditions Gate) complete")
