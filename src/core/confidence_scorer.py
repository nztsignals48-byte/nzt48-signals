"""Phase 7: Confidence Scorer (8-Indicator Consensus)"""
import numpy as np
from dataclasses import dataclass
from typing import Dict

@dataclass
class ConfidenceResult:
    score: float
    regime_threshold: float
    passed: bool
    scores_dict: Dict[str, float]

class ConfidenceScorer:
    """8-indicator consensus scoring"""
    WEIGHTS = {
        'vwap': 1.8, 'rsi': 1.2, 'ema': 0.8, 'roc': 1.0,
        'macd': 1.0, 'adx': 1.5, 'bb': 0.7, 'volume': 0.9
    }

    def score(
        self,
        vwap_score: float, rsi_score: float, ema_score: float, roc_score: float,
        macd_score: float, adx_score: float, bb_score: float, vol_score: float,
        regime: str
    ) -> ConfidenceResult:
        """Score 8 indicators, return weighted consensus"""
        scores = {
            'vwap': np.clip(vwap_score, 0, 10),
            'rsi': np.clip(rsi_score, 0, 10),
            'ema': np.clip(ema_score, 0, 10),
            'roc': np.clip(roc_score, 0, 10),
            'macd': np.clip(macd_score, 0, 10),
            'adx': np.clip(adx_score, 0, 10),
            'bb': np.clip(bb_score, 0, 10),
            'volume': np.clip(vol_score, 0, 10),
        }

        weighted = sum(scores[k] * self.WEIGHTS[k] for k in scores)
        total_weight = sum(self.WEIGHTS.values())
        confidence = weighted / total_weight

        # Regime-specific thresholds
        thresholds = {
            'TRENDING_UP': 6.5, 'TRENDING_DOWN': 6.0,
            'RANGE': 6.5, 'HIGH_VOL': 7.5, 'RISK_OFF': 8.0
        }
        threshold = thresholds.get(regime, 6.5)

        return ConfidenceResult(
            score=confidence,
            regime_threshold=threshold,
            passed=confidence >= threshold,
            scores_dict=scores
        )

if __name__ == "__main__":
    scorer = ConfidenceScorer()
    r = scorer.score(8, 7, 8, 7, 7, 8, 6, 7, "TRENDING_UP")
    print(f"✓ Confidence: {r.score:.1f}/10, threshold: {r.threshold:.1f}, passed: {r.passed}")
    print("✅ Phase 7 (Confidence Scorer) complete")
