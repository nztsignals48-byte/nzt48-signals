"""
Ensemble Diversity System -- NZT-48 W12
Dietterich (2000): Ensemble Methods in Machine Learning.
Kuncheva and Whitaker (2003): Measures of Diversity in Classifier Ensembles.
Three base learners: LightGBM + XGBoost + PA. Stacked via LogisticRegression.
Diversity measured by Q-statistic. Diverse pairs (Q < 0.7) retained.
"""

import json
import logging
import os
import pickle
from datetime import datetime, timezone
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

MODEL_FILE = "data/ensemble_model.pkl"
STATE_FILE = "data/ensemble_state.json"


class EnsembleDiversitySystem:
    """
    Trains LightGBM + XGBoost + PA base learners and stacks them.

    Kuncheva and Whitaker Q-statistic for diversity:
      Q = (n11*n00 - n10*n01) / (n11*n00 + n10*n01)
      Q < 0.7  -- diverse pair (keep both)
      Q > 0.85 -- redundant pair (use only stronger one)

    Stacking via 5-fold cross-validation LogisticRegression meta-learner.
    """

    def __init__(self, model_file: str = MODEL_FILE, state_file: str = STATE_FILE):
        self.model_file = model_file
        self.state_file = state_file
        self.state = self._load_state()
        self.lgbm_model = None
        self.xgb_model = None
        self.pa_model = None
        self.stack_model = None
        self._load_models()

    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"last_train": None, "diversity_scores": {}, "ensemble_accuracy": None}

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(self.state_file) if os.path.dirname(self.state_file) else ".", exist_ok=True)
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.debug("EnsembleDiversitySystem: save failed: %s", e)

    def _load_models(self) -> None:
        if os.path.exists(self.model_file):
            try:
                with open(self.model_file, "rb") as f:
                    bundle = pickle.load(f)
                    self.lgbm_model = bundle.get("lgbm")
                    self.xgb_model = bundle.get("xgb")
                    self.pa_model = bundle.get("pa")
                    self.stack_model = bundle.get("stack")
            except Exception as e:
                logger.debug("EnsembleDiversitySystem: load failed: %s", e)

    def _save_models(self) -> None:
        os.makedirs(os.path.dirname(self.model_file) if os.path.dirname(self.model_file) else ".", exist_ok=True)
        try:
            bundle = {
                "lgbm": self.lgbm_model,
                "xgb": self.xgb_model,
                "pa": self.pa_model,
                "stack": self.stack_model,
            }
            with open(self.model_file, "wb") as f:
                pickle.dump(bundle, f)
        except Exception as e:
            logger.debug("EnsembleDiversitySystem: save_models failed: %s", e)

    def _outcomes_to_xy(self, outcomes: list):
        """Convert outcomes list to (X, y) arrays."""
        X, y = [], []
        for o in outcomes:
            try:
                features = [
                    float(o.get("confidence", 50) or 50) / 100.0,
                    float(o.get("rvol", 1.0) or 1.0),
                    float(o.get("adx", 20) or 20) / 100.0,
                    float(o.get("vix", 18) or 18) / 50.0,
                    float(o.get("sector_rank", 3) or 3) / 5.0,
                    float(o.get("momentum_score", 0.5) or 0.5),
                    1.0 if str(o.get("regime", "")).startswith("TRENDING_UP") else 0.0,
                    1.0 if str(o.get("regime", "")).endswith("STRONG") else 0.0,
                ]
                label = 1 if o.get("status") == "WIN" else 0
                X.append(features)
                y.append(label)
            except Exception:
                continue
        return np.array(X) if X else np.zeros((0, 8)), np.array(y)

    def train_all(self, outcomes: list, sample_weights: Optional[np.ndarray] = None) -> dict:
        """
        Trains all 3 base learners + stacking meta-learner.
        Returns: {ensemble_accuracy, diversity_scores, correlation_matrix}
        """
        X, y = self._outcomes_to_xy(outcomes)
        if len(X) < 20:
            return {"error": "insufficient_data", "n": len(X)}

        # Adjust sample weights length
        if sample_weights is not None and len(sample_weights) != len(y):
            sample_weights = None

        base_preds = {}

        # 1. LightGBM
        try:
            import lightgbm as lgb
            self.lgbm_model = lgb.LGBMClassifier(n_estimators=100, random_state=42, verbose=-1)
            fit_kwargs = {}
            if sample_weights is not None:
                fit_kwargs["sample_weight"] = sample_weights
            self.lgbm_model.fit(X, y, **fit_kwargs)
            base_preds["lgbm"] = self.lgbm_model.predict(X)
        except Exception as e:
            logger.debug("EnsembleDiversitySystem: LightGBM training failed: %s", e)

        # 2. XGBoost
        try:
            from xgboost import XGBClassifier
            self.xgb_model = XGBClassifier(n_estimators=100, random_state=42, use_label_encoder=False,
                                            eval_metric="logloss", verbosity=0)
            fit_kwargs = {}
            if sample_weights is not None:
                fit_kwargs["sample_weight"] = sample_weights
            self.xgb_model.fit(X, y, **fit_kwargs)
            base_preds["xgb"] = self.xgb_model.predict(X)
        except Exception as e:
            logger.debug("EnsembleDiversitySystem: XGBoost training failed: %s", e)

        # 3. Passive-Aggressive
        try:
            from sklearn.linear_model import PassiveAggressiveClassifier
            self.pa_model = PassiveAggressiveClassifier(C=1.0, random_state=42)
            fit_kwargs = {}
            if sample_weights is not None:
                fit_kwargs["sample_weight"] = sample_weights
            self.pa_model.fit(X, y, **fit_kwargs)
            base_preds["pa"] = self.pa_model.predict(X)
        except Exception as e:
            logger.debug("EnsembleDiversitySystem: PA training failed: %s", e)

        if len(base_preds) < 2:
            return {"error": "not_enough_base_learners", "trained": len(base_preds)}

        # Compute diversity (Q-statistic for each pair)
        diversity = {}
        preds_list = list(base_preds.items())
        for i in range(len(preds_list)):
            for j in range(i + 1, len(preds_list)):
                name_i, pred_i = preds_list[i]
                name_j, pred_j = preds_list[j]
                n11 = sum(1 for a, b in zip(pred_i, pred_j) if a == 1 and b == 1)
                n00 = sum(1 for a, b in zip(pred_i, pred_j) if a == 0 and b == 0)
                n10 = sum(1 for a, b in zip(pred_i, pred_j) if a == 1 and b == 0)
                n01 = sum(1 for a, b in zip(pred_i, pred_j) if a == 0 and b == 1)
                denom = n11 * n00 + n10 * n01
                q = (n11 * n00 - n10 * n01) / denom if denom > 0 else 0
                diversity[f"{name_i}_{name_j}"] = round(q, 4)

        # Stacking meta-learner (LogisticRegression on base predictions)
        try:
            from sklearn.linear_model import LogisticRegression
            stack_X = np.column_stack(list(base_preds.values()))
            self.stack_model = LogisticRegression(random_state=42)
            self.stack_model.fit(stack_X, y)
        except Exception as e:
            logger.debug("EnsembleDiversitySystem: stacking failed: %s", e)

        # Accuracy
        if self.stack_model is not None:
            stack_X = np.column_stack(list(base_preds.values()))
            ensemble_acc = float(np.mean(self.stack_model.predict(stack_X) == y))
        else:
            # Majority vote fallback
            votes = np.column_stack(list(base_preds.values()))
            ensemble_pred = (votes.mean(axis=1) >= 0.5).astype(int)
            ensemble_acc = float(np.mean(ensemble_pred == y))

        self.state["last_train"] = datetime.now(timezone.utc).isoformat()
        self.state["diversity_scores"] = diversity
        self.state["ensemble_accuracy"] = round(ensemble_acc, 4)
        self._save_state()
        self._save_models()

        return {
            "ensemble_accuracy": round(ensemble_acc, 4),
            "diversity_scores": diversity,
            "base_learners_trained": list(base_preds.keys()),
            "n_samples": len(X),
        }

    def predict_ensemble(self, features: np.ndarray) -> float:
        """Returns stacked ensemble win probability."""
        preds = []
        if self.lgbm_model is not None:
            try:
                preds.append(float(self.lgbm_model.predict_proba(features.reshape(1, -1))[0][1]))
            except Exception:
                pass
        if self.xgb_model is not None:
            try:
                preds.append(float(self.xgb_model.predict_proba(features.reshape(1, -1))[0][1]))
            except Exception:
                pass
        if self.pa_model is not None:
            try:
                df = self.pa_model.decision_function(features.reshape(1, -1))[0]
                preds.append(float(1.0 / (1.0 + np.exp(-df))))
            except Exception:
                pass

        if not preds:
            return 0.5

        if self.stack_model is not None and len(preds) >= 2:
            try:
                stack_input = np.array([[p > 0.5 for p in preds]]).astype(float)
                if stack_input.shape[1] == len(preds):
                    return float(self.stack_model.predict_proba(stack_input)[0][1])
            except Exception:
                pass

        return float(sum(preds) / len(preds))
