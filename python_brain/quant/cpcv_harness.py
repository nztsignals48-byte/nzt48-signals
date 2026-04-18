"""
Combinatorial Purged Cross-Validation

López de Prado (2018) AFML Chapter 12.
Replaces walk-forward with a more robust validation scheme that accounts
for overlapping observations and financial data's non-IID nature.
"""
from __future__ import annotations

from itertools import combinations
from dataclasses import dataclass

import numpy as np


@dataclass
class CPCVFold:
    train_idx: np.ndarray
    test_idx: np.ndarray
    embargo_gap: int


class CombinatorialPurgedCV:
    """
    Combinatorial Purged Cross-Validation.

    Generates combinations of train/test splits with purging
    (remove training samples overlapping test) + embargo (gap between splits).
    """

    def __init__(
        self,
        n_splits: int = 6,
        n_test_splits: int = 2,
        embargo_pct: float = 0.01,
    ):
        self.n_splits = n_splits
        self.n_test_splits = n_test_splits
        self.embargo_pct = embargo_pct

    def split(self, data):
        """Yield (train_idx, test_idx) folds."""
        if hasattr(data, "__len__"):
            n = len(data)
        else:
            n = int(data)

        if n < self.n_splits * 2:
            raise ValueError(f"need >= {self.n_splits * 2} samples, got {n}")

        # Create n_splits equal chunks
        chunk_size = n // self.n_splits
        embargo_size = max(1, int(n * self.embargo_pct))
        chunks = [
            np.arange(i * chunk_size, (i + 1) * chunk_size if i < self.n_splits - 1 else n)
            for i in range(self.n_splits)
        ]

        # Generate all combinations of n_test_splits chunks as test
        for test_chunks in combinations(range(self.n_splits), self.n_test_splits):
            test_idx = np.concatenate([chunks[i] for i in test_chunks])

            train_chunks = [i for i in range(self.n_splits) if i not in test_chunks]
            train_idx = np.concatenate([chunks[i] for i in train_chunks])

            # Embargo: purge train samples within embargo_size of any test sample
            train_idx = self._apply_embargo(train_idx, test_idx, embargo_size)

            yield CPCVFold(
                train_idx=train_idx,
                test_idx=test_idx,
                embargo_gap=embargo_size,
            )

    def _apply_embargo(self, train_idx: np.ndarray, test_idx: np.ndarray, embargo: int) -> np.ndarray:
        """Remove train samples within embargo of test samples."""
        test_set = set(test_idx.tolist())
        # Create embargo zones around each test chunk
        embargo_zones = set()
        for t in test_idx:
            for offset in range(-embargo, embargo + 1):
                embargo_zones.add(int(t) + offset)

        return np.array([i for i in train_idx if i not in embargo_zones])


def run_cpcv_validation(
    data,
    estimator,
    scoring_fn=None,
    n_splits: int = 6,
    n_test_splits: int = 2,
) -> dict:
    """
    Run CPCV validation on a dataset.

    Args:
        data: array-like
        estimator: object with .fit() and .predict()
        scoring_fn: callable (y_true, y_pred) -> score (default: mean)

    Returns dict with per-fold scores + summary stats.
    """
    if scoring_fn is None:
        scoring_fn = lambda y_true, y_pred: float(np.mean(y_pred == y_true))

    cv = CombinatorialPurgedCV(n_splits=n_splits, n_test_splits=n_test_splits)
    scores = []

    try:
        X, y = data
    except (TypeError, ValueError):
        X = data
        y = None

    for fold in cv.split(X):
        if y is not None:
            estimator.fit(X[fold.train_idx], y[fold.train_idx])
            y_pred = estimator.predict(X[fold.test_idx])
            score = scoring_fn(y[fold.test_idx], y_pred)
            scores.append(score)

    if not scores:
        return {"scores": [], "mean": 0, "std": 0, "min": 0, "max": 0}

    return {
        "scores": scores,
        "mean": float(np.mean(scores)),
        "std": float(np.std(scores)),
        "min": float(np.min(scores)),
        "max": float(np.max(scores)),
        "n_folds": len(scores),
    }


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        cv = CombinatorialPurgedCV(n_splits=6, n_test_splits=2, embargo_pct=0.02)
        data = np.arange(100)
        folds = list(cv.split(data))
        print(f"Generated {len(folds)} folds")
        for i, f in enumerate(folds[:3]):
            print(f"  Fold {i}: train={len(f.train_idx)} test={len(f.test_idx)} embargo={f.embargo_gap}")
        print("OK")
