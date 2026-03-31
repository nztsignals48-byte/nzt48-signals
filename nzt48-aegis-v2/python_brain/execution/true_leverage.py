"""5-Layer True Leverage Calculator — Book 187.

Most traders only consider product leverage (3x). True leverage is
the compounding of FIVE distinct leverage layers:

  Layer 1: ETP Leverage — product-level (e.g., 3x daily reset)
  Layer 2: Margin Leverage — broker margin (currently 1x for ISA)
  Layer 3: Concentration — HHI-based position concentration
  Layer 4: Correlation — diversification ratio (correlated = higher)
  Layer 5: Volatility Decay — leveraged ETP drag amplification

True Leverage = Product(all layers)

Example: A 3x ETP at 40% concentration in a correlated portfolio
with 25% vol = 3 * 1.0 * 1.4 * 1.3 * 1.1 = 6.0x true leverage.

This module provides the calculator used by the risk engine to
ensure total effective leverage stays within safe bounds.

State persisted to /app/data/true_leverage/.

Usage:
    from python_brain.execution.true_leverage import (
        TrueLeverageCalculator, LeverageLayer,
    )
    calc = TrueLeverageCalculator(positions)
    result = calc.compute()
    total = calc.total_effective_leverage()
    safe = calc.is_safe(max_leverage=5.0)
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("true_leverage")

__all__ = [
    "LeverageLayer",
    "TrueLeverageCalculator",
]

# ── Constants ──────────────────────────────────────────────────────────

STATE_DIR = Path("/app/data/true_leverage")
DEFAULT_MAX_LEVERAGE = 5.0    # Safety threshold
ISA_MARGIN_LEVERAGE = 1.0     # ISA accounts: no margin leverage
CORRELATION_DATA_PATH = Path("/app/data/correlation_matrix.npy")


# ── Leverage Layers ───────────────────────────────────────────────────

class LeverageLayer(Enum):
    """The five layers of true leverage."""
    ETP_LEVERAGE = "etp_leverage"
    MARGIN_LEVERAGE = "margin_leverage"
    CONCENTRATION = "concentration"
    CORRELATION = "correlation"
    VOL_DECAY = "vol_decay"


# ── True Leverage Calculator ──────────────────────────────────────────

class TrueLeverageCalculator:
    """Computes 5-layer true leverage for a portfolio of leveraged ETPs.

    Each layer represents a distinct source of leverage amplification.
    The total effective leverage is the product of all five layers,
    giving the true risk exposure of the portfolio.
    """

    def __init__(self, positions: List[Dict[str, Any]],
                 correlation_matrix: Optional[np.ndarray] = None):
        """Initialize calculator with current positions.

        Args:
            positions: List of position dicts, each with:
                       - symbol: Ticker symbol
                       - notional: Position value in GBP
                       - leverage: Product leverage (e.g., 3)
                       - realized_vol: Annualized realized volatility (optional)
                       - sector: Sector for correlation (optional)
            correlation_matrix: Asset correlation matrix. Loaded from disk if None.
        """
        self._positions = positions
        self._corr_matrix = correlation_matrix
        self._n_positions = len(positions)
        self._layer_results: Dict[LeverageLayer, float] = {}

        # Load correlation matrix if not provided
        if self._corr_matrix is None:
            self._corr_matrix = self._load_correlation_matrix()

        log.info("TrueLeverageCalculator: %d positions", self._n_positions)

    def compute(self) -> Dict[str, Any]:
        """Compute all five leverage layers and total effective leverage.

        Returns:
            Dict with per-layer values, total leverage, and safety status.
        """
        if not self._positions:
            return {
                "layers": {},
                "total_effective_leverage": 1.0,
                "is_safe": True,
                "n_positions": 0,
            }

        # Compute each layer
        etp = self._etp_leverage_portfolio()
        margin = self._margin_leverage()
        concentration = self._concentration_leverage(self._positions)
        correlation = self._correlation_leverage(
            self._positions, self._corr_matrix
        )

        # Average realized vol across positions
        vols = [p.get("realized_vol", 0.20) for p in self._positions]
        avg_vol = float(np.mean(vols)) if vols else 0.20
        vol_decay = self._vol_decay_leverage_portfolio(avg_vol)

        self._layer_results = {
            LeverageLayer.ETP_LEVERAGE: etp,
            LeverageLayer.MARGIN_LEVERAGE: margin,
            LeverageLayer.CONCENTRATION: concentration,
            LeverageLayer.CORRELATION: correlation,
            LeverageLayer.VOL_DECAY: vol_decay,
        }

        total = self.total_effective_leverage()
        safe = self.is_safe()

        result = {
            "layers": {
                layer.value: {
                    "value": round(val, 3),
                    "description": self._layer_description(layer),
                }
                for layer, val in self._layer_results.items()
            },
            "total_effective_leverage": round(total, 2),
            "is_safe": safe,
            "max_safe_leverage": DEFAULT_MAX_LEVERAGE,
            "n_positions": self._n_positions,
            "per_position": self._per_position_detail(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if not safe:
            log.warning("TRUE LEVERAGE UNSAFE: %.2fx (max=%.1fx)",
                        total, DEFAULT_MAX_LEVERAGE)
        else:
            log.info("True leverage: %.2fx (safe, max=%.1fx)",
                     total, DEFAULT_MAX_LEVERAGE)

        return result

    def _etp_leverage(self, position: Dict) -> float:
        """Get ETP product leverage for a single position.

        Args:
            position: Position dict with 'leverage' key.

        Returns:
            Product leverage multiplier.
        """
        return abs(float(position.get("leverage", 1)))

    def _etp_leverage_portfolio(self) -> float:
        """Compute weighted average ETP leverage across portfolio.

        Returns:
            Notional-weighted average product leverage.
        """
        if not self._positions:
            return 1.0

        total_notional = sum(abs(float(p.get("notional", 0)))
                             for p in self._positions)
        if total_notional < 1e-10:
            return 1.0

        weighted_lev = sum(
            abs(float(p.get("notional", 0))) * abs(float(p.get("leverage", 1)))
            for p in self._positions
        )
        return weighted_lev / total_notional

    def _margin_leverage(self) -> float:
        """Get margin leverage.

        For ISA accounts, margin leverage is always 1.0.
        For margin accounts, this would be notional / equity.

        Returns:
            Margin leverage multiplier.
        """
        return ISA_MARGIN_LEVERAGE

    def _concentration_leverage(self, positions: List[Dict]) -> float:
        """Compute concentration leverage using HHI.

        Herfindahl-Hirschman Index measures portfolio concentration.
        HHI = sum(w_i^2). A perfectly diversified n-asset portfolio
        has HHI = 1/n. Concentration leverage = HHI * n (ratio vs equal).

        Args:
            positions: List of position dicts with notional values.

        Returns:
            Concentration leverage multiplier (>= 1.0).
        """
        if len(positions) <= 1:
            return 1.0

        notionals = np.array([abs(float(p.get("notional", 0)))
                              for p in positions])
        total = np.sum(notionals)
        if total < 1e-10:
            return 1.0

        weights = notionals / total
        hhi = float(np.sum(weights ** 2))
        n = len(positions)

        # Equal-weight HHI = 1/n
        equal_hhi = 1.0 / n

        # Concentration leverage = how much more concentrated than equal weight
        # Ranges from 1.0 (equal weight) to n (single position)
        concentration_ratio = hhi / equal_hhi

        # Dampen the effect: sqrt to prevent extreme values
        leverage = math.sqrt(concentration_ratio)

        return max(leverage, 1.0)

    def _correlation_leverage(self, positions: List[Dict],
                              corr_matrix: Optional[np.ndarray]) -> float:
        """Compute correlation-based leverage (diversification ratio).

        Diversification ratio = sum(w_i * sigma_i) / sigma_portfolio
        High correlation means less diversification = higher effective leverage.

        Args:
            positions: List of position dicts.
            corr_matrix: Asset correlation matrix.

        Returns:
            Correlation leverage multiplier (>= 1.0).
        """
        n = len(positions)
        if n <= 1:
            return 1.0

        notionals = np.array([abs(float(p.get("notional", 0)))
                              for p in positions])
        total = np.sum(notionals)
        if total < 1e-10:
            return 1.0

        weights = notionals / total
        vols = np.array([float(p.get("realized_vol", 0.20))
                         for p in positions])

        # Weighted sum of individual volatilities
        weighted_vol_sum = float(np.sum(weights * vols))

        # Portfolio volatility using correlation
        if corr_matrix is not None and corr_matrix.shape[0] >= n:
            corr = corr_matrix[:n, :n]
        else:
            # Default: assume moderate correlation (0.5 pairwise)
            corr = np.full((n, n), 0.5)
            np.fill_diagonal(corr, 1.0)

        # Covariance from correlation + volatilities
        cov = np.outer(vols, vols) * corr
        port_var = float(weights @ cov @ weights)
        port_vol = math.sqrt(max(port_var, 1e-10))

        # Diversification ratio
        if port_vol > 1e-10:
            div_ratio = weighted_vol_sum / port_vol
        else:
            div_ratio = 1.0

        # Correlation leverage: inverse of diversification benefit
        # Perfect diversification (div_ratio = sqrt(n)) → leverage = 1
        # No diversification (div_ratio = 1) → leverage = sqrt(n)
        # Clamp between 1.0 and 2.0 for practical use
        corr_leverage = 1.0 / max(div_ratio / math.sqrt(n), 0.5)
        corr_leverage = float(np.clip(corr_leverage, 1.0, 2.0))

        return corr_leverage

    def _vol_decay_leverage(self, position: Dict,
                            realized_vol: float) -> float:
        """Compute volatility decay leverage for a single position.

        Vol decay drag = 0.5 * L * (L-1) * sigma^2
        This represents annual return loss from daily leverage rebalancing.

        Args:
            position: Position dict.
            realized_vol: Annualized realized volatility.

        Returns:
            Vol decay leverage multiplier (>= 1.0).
        """
        L = abs(float(position.get("leverage", 1)))
        if L <= 1:
            return 1.0

        # Drag as fraction of returns
        drag = 0.5 * L * (L - 1) * realized_vol ** 2

        # Convert drag to leverage multiplier
        # Higher drag = higher effective leverage needed to achieve target return
        vol_leverage = 1.0 + drag

        return max(vol_leverage, 1.0)

    def _vol_decay_leverage_portfolio(self, avg_vol: float) -> float:
        """Compute portfolio-level volatility decay leverage.

        Args:
            avg_vol: Average annualized volatility.

        Returns:
            Portfolio vol decay leverage.
        """
        if not self._positions:
            return 1.0

        total_notional = sum(abs(float(p.get("notional", 0)))
                             for p in self._positions)
        if total_notional < 1e-10:
            return 1.0

        weighted_decay = 0.0
        for p in self._positions:
            notional = abs(float(p.get("notional", 0)))
            weight = notional / total_notional
            vol = float(p.get("realized_vol", avg_vol))
            decay = self._vol_decay_leverage(p, vol)
            weighted_decay += weight * decay

        return max(weighted_decay, 1.0)

    def total_effective_leverage(self) -> float:
        """Compute total effective leverage as product of all layers.

        Returns:
            Total effective leverage multiplier.
        """
        if not self._layer_results:
            self.compute()

        total = 1.0
        for layer_val in self._layer_results.values():
            total *= layer_val

        return total

    def is_safe(self, max_leverage: float = DEFAULT_MAX_LEVERAGE) -> bool:
        """Check if total effective leverage is within safe bounds.

        Args:
            max_leverage: Maximum allowed effective leverage.

        Returns:
            True if total leverage <= max_leverage.
        """
        return self.total_effective_leverage() <= max_leverage

    def _per_position_detail(self) -> List[Dict[str, Any]]:
        """Compute per-position leverage detail."""
        details = []
        for p in self._positions:
            vol = float(p.get("realized_vol", 0.20))
            etp_lev = self._etp_leverage(p)
            decay_lev = self._vol_decay_leverage(p, vol)
            pos_total = etp_lev * ISA_MARGIN_LEVERAGE * decay_lev

            details.append({
                "symbol": p.get("symbol", "UNKNOWN"),
                "notional": round(abs(float(p.get("notional", 0))), 2),
                "product_leverage": etp_lev,
                "vol_decay_leverage": round(decay_lev, 3),
                "position_leverage": round(pos_total, 2),
                "realized_vol": round(vol, 4),
            })
        return details

    @staticmethod
    def _layer_description(layer: LeverageLayer) -> str:
        """Human-readable description of each leverage layer."""
        descriptions = {
            LeverageLayer.ETP_LEVERAGE: "Product leverage (e.g., 3x daily reset)",
            LeverageLayer.MARGIN_LEVERAGE: "Broker margin (1x for ISA)",
            LeverageLayer.CONCENTRATION: "Position concentration (HHI-based)",
            LeverageLayer.CORRELATION: "Correlation / diversification ratio",
            LeverageLayer.VOL_DECAY: "Volatility decay amplification",
        }
        return descriptions.get(layer, "Unknown layer")

    def _load_correlation_matrix(self) -> Optional[np.ndarray]:
        """Load correlation matrix from disk."""
        try:
            if CORRELATION_DATA_PATH.exists():
                return np.load(str(CORRELATION_DATA_PATH))
        except Exception as e:
            log.warning("Failed to load correlation matrix: %s", e)
        return None

    def save_report(self, path: str = "/app/data/true_leverage/report.json") -> None:
        """Save leverage report."""
        save_path = Path(path)
        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            report = self.compute()
            with open(str(save_path), "w") as f:
                json.dump(report, f, indent=2, default=str)
            log.info("Leverage report saved to %s", path)
        except Exception as e:
            log.error("Failed to save leverage report: %s", e)
