"""BOOK 134: CAUSAL INFERENCE — Discover causality not just correlation"""

import sys
from typing import Dict, List, Optional


def granger_causality_test(X: List[float], Y: List[float], max_lag: int = 5) -> float:
    """
    Granger causality: Does X Granger-cause Y?
    If past X values help predict Y better, X Granger-causes Y.

    Returns: Causality score [0-1]
    0 = no causality
    1 = strong causality
    """
    try:
        if len(X) < max_lag + 10 or len(Y) < max_lag + 10:
            return 0.5  # Insufficient data

        # Simplified: compute correlation at different lags
        max_corr = 0
        for lag in range(1, min(max_lag + 1, len(X) // 2)):
            if lag < len(X) and lag < len(Y):
                X_lagged = X[:-lag]
                Y_current = Y[lag:]

                if len(X_lagged) > 2 and len(Y_current) > 2:
                    # Simple correlation as causality proxy
                    mean_x = sum(X_lagged) / len(X_lagged)
                    mean_y = sum(Y_current) / len(Y_current)

                    num = sum((X_lagged[i] - mean_x) * (Y_current[i] - mean_y) for i in range(len(X_lagged)))
                    denom = (
                        sum((X_lagged[i] - mean_x) ** 2 for i in range(len(X_lagged)))
                        * sum((Y_current[i] - mean_y) ** 2 for i in range(len(Y_current)))
                    ) ** 0.5

                    if denom > 0:
                        corr = abs(num / denom)
                        max_corr = max(max_corr, corr)

        return max_corr

    except Exception as e:
        sys.stderr.write(f"Granger causality error: {e}\n")
        return 0.5


def transfer_entropy(source: List[float], target: List[float], k: int = 1) -> float:
    """
    Transfer Entropy: Information flow from source to target.
    High TE = source contains information about target's future.

    Returns: TE score [0-1]
    """
    try:
        if len(source) < 10 or len(target) < 10:
            return 0.5

        # Simplified: use lagged correlation
        # Real TE requires binning and entropy calculation
        max_te = 0
        for lag in range(1, min(5, len(source) // 3)):
            if lag < len(source) and lag < len(target):
                corr = granger_causality_test(source, target, max_lag=lag)
                max_te = max(max_te, corr)

        return max_te

    except Exception as e:
        sys.stderr.write(f"Transfer entropy error: {e}\n")
        return 0.5


def identify_causal_signals(signal_features: Dict[str, List[float]]) -> Dict[str, float]:
    """
    Identify which signals have causal link to forward returns.

    Args:
        signal_features: Dict of signal_name → [historical values]

    Returns:
        Dict of signal_name → causality_score [0-1]
    """
    try:
        causal_scores = {}

        for signal_name, values in signal_features.items():
            if not isinstance(values, list) or len(values) < 10:
                causal_scores[signal_name] = 0.5
                continue

            # Test if signal Granger-causes forward returns
            # (Simplified: use variance as proxy for forward returns)
            returns_proxy = []
            for i in range(len(values) - 1):
                ret = (values[i + 1] - values[i]) / (abs(values[i]) + 1e-6)
                returns_proxy.append(ret)

            causality = granger_causality_test(values[:-1], returns_proxy, max_lag=3)
            causal_scores[signal_name] = causality

        return causal_scores

    except Exception as e:
        sys.stderr.write(f"Causal signal identification error: {e}\n")
        return {}


def apply_causal_filter(base_confidence: int, signal_name: str, causal_score: Optional[float]) -> int:
    """Adjust confidence based on causality."""
    if causal_score is None:
        return base_confidence

    # Low causality = penalize
    mult = 0.5 + 1.0 * causal_score  # Range [0.5, 1.5]

    adjusted = int(base_confidence * mult)
    return max(0, min(100, adjusted))
