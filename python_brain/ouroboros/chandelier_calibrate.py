"""Chandelier ATR-mult grid search on MAE/MFE samples."""
from __future__ import annotations

from typing import List, Tuple


def grid_search(mae_mfe: List[Tuple[float, float]]) -> float:
    if not mae_mfe:
        return 2.5
    # Best ATR-mult across grid [1.5, 2.0, 2.5, 3.0]: one that captures 80% MFE
    # while cutting losses beyond MAE.
    best = 2.5
    best_score = -1e9
    for mult in [1.5, 2.0, 2.5, 3.0]:
        score = sum((mfe if mfe > mae * mult else -mae * mult) for mae, mfe in mae_mfe)
        if score > best_score:
            best_score = score
            best = mult
    return best
