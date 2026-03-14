"""
NZT-48 Extreme Value Theory — GPD Tail Risk Model (V8.0)
==========================================================
Balkema-de Haan-Pickands theorem: fit Generalized Pareto Distribution
to loss exceedances above a high threshold.

Used as a hard veto in virtual_trader.py execute_signal():
if P(loss > 5σ gap) > 1%, reject the trade.

Requires ≥10 exceedances and ≥20 total losses to activate.
Below that, returns veto=False (insufficient data).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger("nzt48.evt")


@dataclass
class EVTResult:
    """Result of GPD tail risk analysis."""
    veto: bool = False
    tail_prob: float = 0.0
    shape_xi: float = 0.0
    scale_sigma: float = 0.0
    n_exceedances: int = 0
    n_total_losses: int = 0
    threshold: float = 0.0
    gap_size: float = 0.0
    reason: str = ""


def gpd_tail_risk(
    r_multiples: np.ndarray,
    threshold_sigma: float = 2.0,
    veto_prob: float = 0.01,
    veto_gap_sigma: float = 5.0,
    min_exceedances: int = 10,
    min_losses: int = 20,
) -> EVTResult:
    """Fit GPD to loss tail and check if extreme gap risk exceeds threshold.

    Balkema-de Haan-Pickands (1974): For sufficiently high thresholds,
    exceedances of the threshold follow a Generalized Pareto Distribution.

    POT (Peaks Over Threshold) method:
    1. Take only losses (negative R-multiples)
    2. Set threshold at mean(|losses|) + threshold_sigma * std(|losses|)
    3. Fit GPD to exceedances above threshold
    4. Estimate P(loss > veto_gap_sigma * std(|losses|))
    5. If P > veto_prob (1%), veto the trade

    Args:
        r_multiples: Array of R-multiples from trade history
        threshold_sigma: Threshold for POT in standard deviations above mean loss
        veto_prob: Probability threshold for veto (default 1%)
        veto_gap_sigma: Gap size in standard deviations to check (default 5σ)
        min_exceedances: Minimum exceedances needed for reliable GPD fit
        min_losses: Minimum total losses needed

    Returns:
        EVTResult with veto flag and diagnostics
    """
    result = EVTResult()

    # Extract losses (negative R-multiples → positive loss magnitudes)
    losses = np.abs(r_multiples[r_multiples < 0])
    result.n_total_losses = len(losses)

    if len(losses) < min_losses:
        result.reason = f"Insufficient losses ({len(losses)}/{min_losses})"
        return result

    # POT threshold
    mu_loss = float(np.mean(losses))
    sigma_loss = float(np.std(losses, ddof=1))
    if sigma_loss <= 0:
        result.reason = "Zero loss variance"
        return result

    threshold = mu_loss + threshold_sigma * sigma_loss
    result.threshold = threshold

    # Exceedances above threshold
    exceedances = losses[losses > threshold] - threshold
    result.n_exceedances = len(exceedances)

    if len(exceedances) < min_exceedances:
        result.reason = f"Insufficient exceedances ({len(exceedances)}/{min_exceedances})"
        return result

    # Fit GPD using scipy
    try:
        from scipy.stats import genpareto

        # MLE fit
        shape, loc, scale = genpareto.fit(exceedances, floc=0)
        result.shape_xi = float(shape)
        result.scale_sigma = float(scale)

        # Sanity check on shape parameter
        # xi > 0.5 suggests extremely heavy tails — likely unreliable fit
        if shape > 0.5:
            result.reason = f"GPD shape xi={shape:.3f} > 0.5 — unreliable fit"
            result.veto = True  # Conservative: veto on unreliable heavy tail
            result.tail_prob = 1.0
            return result

        # Calculate P(excess > gap_size) where gap_size is relative to threshold
        gap_above_threshold = veto_gap_sigma * sigma_loss - threshold_sigma * sigma_loss
        if gap_above_threshold <= 0:
            # Gap is below our threshold — use empirical frequency
            gap_size = veto_gap_sigma * sigma_loss
            result.gap_size = gap_size
            empirical_prob = float(np.sum(losses > gap_size)) / len(losses)
            result.tail_prob = empirical_prob
        else:
            gap_size = veto_gap_sigma * sigma_loss
            result.gap_size = gap_size

            # P(excess > gap_above_threshold | excess > 0) from GPD
            # Then scale by P(exceeding threshold) from empirical data
            p_exceed_threshold = len(exceedances) / len(losses)
            p_gap_given_exceed = float(genpareto.sf(gap_above_threshold, shape, loc=0, scale=scale))
            result.tail_prob = p_exceed_threshold * p_gap_given_exceed

        # Veto decision
        result.veto = result.tail_prob > veto_prob
        if result.veto:
            result.reason = (
                f"GPD tail P(loss>{veto_gap_sigma}σ)={result.tail_prob:.4f} "
                f"> {veto_prob:.2f} — VETO"
            )
            logger.warning("EVT_VETO: %s", result.reason)
        else:
            result.reason = (
                f"GPD tail P(loss>{veto_gap_sigma}σ)={result.tail_prob:.4f} "
                f"<= {veto_prob:.2f} — OK"
            )

        return result

    except ImportError:
        result.reason = "scipy.stats.genpareto not available"
        logger.warning("EVT: scipy not available — skipping GPD analysis")
        return result
    except Exception as e:
        result.reason = f"GPD fit failed: {e}"
        logger.error("EVT: GPD fit error: %s", e)
        return result


def compute_evt_summary(
    r_multiples: np.ndarray,
    thresholds: Optional[list[float]] = None,
) -> dict:
    """Multi-threshold EVT analysis for monitoring/dashboard.

    Runs GPD at multiple thresholds (1.5σ, 2.0σ, 2.5σ) and returns
    a summary dict for the status endpoint.
    """
    if thresholds is None:
        thresholds = [1.5, 2.0, 2.5]

    results = {}
    for t in thresholds:
        evt = gpd_tail_risk(r_multiples, threshold_sigma=t)
        results[f"threshold_{t:.1f}sigma"] = {
            "veto": evt.veto,
            "tail_prob": round(evt.tail_prob, 6),
            "shape_xi": round(evt.shape_xi, 4),
            "scale_sigma": round(evt.scale_sigma, 4),
            "n_exceedances": evt.n_exceedances,
            "reason": evt.reason,
        }

    # Overall veto: ANY threshold triggers veto
    any_veto = any(
        results[k]["veto"] for k in results
    )
    return {
        "veto": any_veto,
        "thresholds": results,
        "n_total_losses": int(np.sum(r_multiples < 0)),
        "n_total_trades": len(r_multiples),
    }
