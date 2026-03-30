"""
Book 23: ML Entry Timing Ensemble
Weighted ensemble of LightGBM, XGBoost, GRU, and rule-based models
Scores signals 0-1 for entry timing quality
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv('DATA_DIR', '/app/data'))
MODEL_DIR = DATA_DIR / 'models' / 'entry_timing'


@dataclass
class EntryScore:
    """
    Entry timing score from ML ensemble.

    Attributes:
        score: Overall entry score (0-1, higher = better timing)
        model_scores: Individual model scores for debugging
        features_used: Number of features that were valid
        shadow_mode: True if in shadow mode (log only, don't gate)
    """
    score: float
    model_scores: Dict[str, float] = field(default_factory=dict)
    features_used: int = 0
    shadow_mode: bool = False


class EntryTimingEnsemble:
    """
    ML Ensemble for entry timing decisions.

    Architecture:
    - 40% LightGBM (tree-based, robust to noise)
    - 30% XGBoost (gradient boosting, good for interactions)
    - 20% GRU (temporal patterns, sequence learning)
    - 10% Rule-based (fallback, interpretable baseline)

    Modes:
    - Shadow mode: First 500 signals (log only, score=1.0 always)
    - Active mode: Gate signals with threshold=0.60

    Graceful degradation:
    - No models found: use rule-based only (score=1.0 for all)
    - Partial models: weighted average of available models
    """

    THRESHOLD = 0.60  # Minimum score to proceed with entry
    SHADOW_COUNT = 500  # Number of signals to shadow before gating

    def __init__(self):
        self.lgbm_model = None
        self.xgb_model = None
        self.gru_model = None
        self.shadow_mode = True
        self.signals_scored = 0

        self._load_models()

    def _load_models(self):
        """Load ONNX models if available (graceful skip if not found)"""
        try:
            lgbm_path = MODEL_DIR / 'lgbm_entry.onnx'
            xgb_path = MODEL_DIR / 'xgb_entry.onnx'
            gru_path = MODEL_DIR / 'gru_entry.onnx'

            # Check if any models exist
            if not MODEL_DIR.exists():
                logger.info(f"Model directory not found: {MODEL_DIR}, using rule-based only")
                return

            # Try loading each model (would need onnxruntime here, but graceful for now)
            if lgbm_path.exists():
                logger.info(f"Found LightGBM model: {lgbm_path}")
                # self.lgbm_model = ort.InferenceSession(str(lgbm_path))

            if xgb_path.exists():
                logger.info(f"Found XGBoost model: {xgb_path}")
                # self.xgb_model = ort.InferenceSession(str(xgb_path))

            if gru_path.exists():
                logger.info(f"Found GRU model: {gru_path}")
                # self.gru_model = ort.InferenceSession(str(gru_path))

            if not any([lgbm_path.exists(), xgb_path.exists(), gru_path.exists()]):
                logger.info("No ML models found, using rule-based scorer only")

        except Exception as e:
            logger.warning(f"Error loading ML models: {e}, falling back to rule-based")

    def score(self, features: Dict[str, float]) -> EntryScore:
        """
        Score entry timing quality from features.

        Args:
            features: Dict of 48 features from FeatureExtractor

        Returns:
            EntryScore with overall score and model breakdowns
        """
        self.signals_scored += 1

        # Shadow mode: log but return 1.0 (pass all signals)
        if self.signals_scored <= self.SHADOW_COUNT:
            rule_score = self._rule_based_score(features)
            logger.info(f"Shadow mode ({self.signals_scored}/{self.SHADOW_COUNT}): "
                       f"rule_score={rule_score:.3f}")
            return EntryScore(
                score=1.0,
                model_scores={'rule': rule_score},
                features_used=len(features),
                shadow_mode=True
            )

        # Active mode: ensemble scoring
        model_scores = {}

        # Rule-based baseline (always available)
        model_scores['rule'] = self._rule_based_score(features)

        # ML models (if loaded)
        if self.lgbm_model:
            model_scores['lgbm'] = self._score_lgbm(features)

        if self.xgb_model:
            model_scores['xgb'] = self._score_xgb(features)

        if self.gru_model:
            model_scores['gru'] = self._score_gru(features)

        # Weighted ensemble
        ensemble_score = self._ensemble_score(model_scores)

        return EntryScore(
            score=ensemble_score,
            model_scores=model_scores,
            features_used=len(features),
            shadow_mode=False
        )

    def _rule_based_score(self, features: Dict[str, float]) -> float:
        """
        Rule-based baseline scorer.

        Scoring criteria (0.2 points each):
        1. RSI in [30, 70] (not overbought/oversold)
        2. RVOL > 1.0 (above average volume)
        3. Spread < 10bps (good liquidity)
        4. Hurst in [0.3, 0.7] (tradeable regime)
        5. Confidence > 60 (high signal quality)
        """
        score = 0.0

        # 1. RSI check
        rsi = features.get('rsi_14', 50.0)
        if 30.0 <= rsi <= 70.0:
            score += 0.2

        # 2. Volume check
        rvol = features.get('rvol', 1.0)
        if rvol > 1.0:
            score += 0.2

        # 3. Spread check
        spread = features.get('spread_pct', 0.0)
        if spread < 0.10:  # 10 bps
            score += 0.2

        # 4. Hurst check (avoid strong trends/random walks)
        hurst = features.get('hurst', 0.5)
        if 0.3 <= hurst <= 0.7:
            score += 0.2

        # 5. Confidence check
        confidence = features.get('confidence', 0.0)
        if confidence > 60.0:
            score += 0.2

        return score

    def _score_lgbm(self, features: Dict[str, float]) -> float:
        """Score with LightGBM model (stub for now)"""
        # TODO: Run ONNX inference when model is trained
        # For now, return rule-based as placeholder
        return self._rule_based_score(features)

    def _score_xgb(self, features: Dict[str, float]) -> float:
        """Score with XGBoost model (stub for now)"""
        # TODO: Run ONNX inference when model is trained
        return self._rule_based_score(features)

    def _score_gru(self, features: Dict[str, float]) -> float:
        """Score with GRU model (stub for now)"""
        # TODO: Run ONNX inference when model is trained
        return self._rule_based_score(features)

    def _ensemble_score(self, model_scores: Dict[str, float]) -> float:
        """
        Weighted ensemble of model scores.

        Weights:
        - LightGBM: 40%
        - XGBoost: 30%
        - GRU: 20%
        - Rule-based: 10%

        If models missing, redistribute weights proportionally.
        """
        weights = {
            'lgbm': 0.40,
            'xgb': 0.30,
            'gru': 0.20,
            'rule': 0.10
        }

        # Calculate available weight
        available_weight = sum(weights[k] for k in model_scores.keys())

        if available_weight == 0:
            return 0.0

        # Normalize weights for available models
        weighted_sum = 0.0
        for model, score in model_scores.items():
            weight = weights.get(model, 0.0) / available_weight
            weighted_sum += weight * score

        return min(1.0, max(0.0, weighted_sum))

    def should_enter(self, entry_score: EntryScore) -> bool:
        """
        Determine if signal should proceed based on score.

        Args:
            entry_score: Score from score() method

        Returns:
            True if should enter trade (shadow mode or score >= threshold)
        """
        if entry_score.shadow_mode:
            return True

        return entry_score.score >= self.THRESHOLD


# Global singleton
_entry_ensemble: Optional[EntryTimingEnsemble] = None


def get_entry_ensemble() -> EntryTimingEnsemble:
    """Get or create singleton entry timing ensemble"""
    global _entry_ensemble

    if _entry_ensemble is None:
        _entry_ensemble = EntryTimingEnsemble()
        logger.info("Entry timing ensemble initialized")

    return _entry_ensemble


def run_ml_nightly():
    """
    Nightly ML retraining check (Book 23 pipeline step).

    This is a placeholder for the full ML training pipeline.
    Actual training happens in separate notebooks/scripts.

    Steps:
    1. Count labeled signals in trades database
    2. If >= 1000 new labels, trigger retraining job
    3. Export models to MODEL_DIR
    4. Log training metrics

    For now: just log status and return.
    """
    logger.info("=== ML Entry Timing Nightly Check ===")

    try:
        # Check for labeled data
        labeled_signals_path = DATA_DIR / 'labeled_signals.jsonl'

        if not labeled_signals_path.exists():
            logger.info("No labeled signals file found, skipping ML training")
            return

        # Count lines (each line = one labeled signal)
        with open(labeled_signals_path, 'r') as f:
            n_signals = sum(1 for _ in f)

        logger.info(f"Found {n_signals} labeled signals")

        if n_signals < 1000:
            logger.info(f"Insufficient data for training (need 1000, have {n_signals})")
            return

        logger.info("Sufficient data for ML training, but training not implemented yet")
        logger.info("TODO: Implement training pipeline (LightGBM, XGBoost, GRU)")
        logger.info("TODO: Export ONNX models to MODEL_DIR")
        logger.info("TODO: Track training metrics (AUC, precision@threshold, etc)")

    except Exception as e:
        logger.error(f"Error in ML nightly check: {e}")


if __name__ == '__main__':
    # Test scoring
    logging.basicConfig(level=logging.INFO)

    # Create ensemble
    ensemble = get_entry_ensemble()

    # Test features (mock)
    test_features = {
        'rsi_14': 55.0,
        'rvol': 1.5,
        'spread_pct': 0.05,
        'hurst': 0.45,
        'confidence': 75.0,
        'atr_pct': 0.8,
        'vol_20t': 0.012,
    }

    # Score
    score = ensemble.score(test_features)

    print(f"\nEntry Score: {score.score:.3f}")
    print(f"Model Scores: {score.model_scores}")
    print(f"Features Used: {score.features_used}")
    print(f"Shadow Mode: {score.shadow_mode}")
    print(f"Should Enter: {ensemble.should_enter(score)}")

    # Test nightly check
    print("\n" + "="*60)
    run_ml_nightly()
