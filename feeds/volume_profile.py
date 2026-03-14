"""
NZT-48 Trading System — Volume Profile Analysis Engine
Research Enhancement #13: Volume-at-Price Profile

Builds volume-at-price profiles from intraday bar data to identify
key structural levels:
  - Point of Control (POC): price with highest traded volume
  - Value Area (VA): range containing 70% of total volume
  - High Volume Nodes (HVN): price clusters of heavy activity (support/resistance)
  - Low Volume Nodes (LVN): thin zones where price moves quickly (breakaway areas)
  - Naked POC: prior-session POC levels that haven't been revisited
  - Single Prints: fast-move zones with minimal volume (magnets for revisit)

Usage:
    engine = VolumeProfileEngine()
    profile = engine.compute_profile(df_1min_bars)
    position = engine.get_price_position(current_price, profile)
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.volume_profile")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_NUM_BINS: int = 50
VALUE_AREA_PCT: float = 0.70          # 70% of volume defines the Value Area
HVN_THRESHOLD_PERCENTILE: float = 80  # Top 20% bins by volume are HVN
LVN_THRESHOLD_PERCENTILE: float = 20  # Bottom 20% bins by volume are LVN
SINGLE_PRINT_PERCENTILE: float = 5    # Bottom 5% volume = single print
POC_PROXIMITY_PCT: float = 0.001      # Within 0.1% of POC = "AT_POC"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class VolumeProfile:
    """Volume-at-price profile for a trading session or multi-day period.

    Attributes:
        poc: Point of Control — price level with highest traded volume.
        va_high: Upper bound of the Value Area (70% of volume).
        va_low: Lower bound of the Value Area.
        hvn_levels: High Volume Nodes — price levels with concentrated activity.
        lvn_levels: Low Volume Nodes — thin price levels where moves are fast.
        total_volume: Sum of all volume in the profile.
        bin_edges: Numpy histogram bin edges (len = num_bins + 1).
        bin_volumes: Volume per bin (len = num_bins).
    """
    poc: float = 0.0
    va_high: float = 0.0
    va_low: float = 0.0
    hvn_levels: list[float] = field(default_factory=list)
    lvn_levels: list[float] = field(default_factory=list)
    total_volume: float = 0.0
    bin_edges: list[float] = field(default_factory=list)
    bin_volumes: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class VolumeProfileEngine:
    """Computes and analyses volume-at-price profiles.

    Accepts 1-minute bar DataFrames with columns:
        Open, High, Low, Close, Volume
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger("nzt48.volume_profile")

    # ------------------------------------------------------------------
    # Core profile computation
    # ------------------------------------------------------------------

    def compute_profile(
        self,
        df: pd.DataFrame,
        num_bins: int = DEFAULT_NUM_BINS,
    ) -> VolumeProfile:
        """Build a volume-at-price profile from OHLCV bars.

        For each bar the volume is distributed across the bar's price range
        using the typical price (HLC/3) as a single representative level.
        This is the standard approximation when tick data is unavailable.

        Args:
            df: DataFrame with Open, High, Low, Close, Volume columns.
            num_bins: Number of price bins for the histogram.

        Returns:
            A fully populated VolumeProfile dataclass.
        """
        profile = VolumeProfile()

        if df is None or df.empty:
            self.logger.warning("compute_profile called with empty DataFrame.")
            return profile

        required_cols = {"High", "Low", "Close", "Volume"}
        if not required_cols.issubset(df.columns):
            self.logger.error(
                "DataFrame missing required columns. Need %s, got %s",
                required_cols,
                set(df.columns),
            )
            return profile

        try:
            # Typical price as representative level for each bar
            prices = ((df["High"] + df["Low"] + df["Close"]) / 3.0).values
            volumes = df["Volume"].values.astype(float)

            # Filter out zero-volume bars
            mask = volumes > 0
            prices = prices[mask]
            volumes = volumes[mask]

            if len(prices) == 0:
                self.logger.warning("No non-zero volume bars found.")
                return profile

            # Build histogram
            bin_volumes, bin_edges = np.histogram(
                prices,
                bins=num_bins,
                weights=volumes,
            )

            # Bin midpoints for labelling
            bin_midpoints = (bin_edges[:-1] + bin_edges[1:]) / 2.0

            # --- Point of Control (max volume bin) ---
            poc_idx = int(np.argmax(bin_volumes))
            profile.poc = float(bin_midpoints[poc_idx])

            # --- Value Area (70% of total volume, expanding outward from POC) ---
            total_vol = float(np.sum(bin_volumes))
            profile.total_volume = total_vol
            va_high, va_low = self._compute_value_area(
                bin_volumes, bin_midpoints, poc_idx, total_vol,
            )
            profile.va_high = va_high
            profile.va_low = va_low

            # --- HVN / LVN detection ---
            if total_vol > 0:
                nonzero_vols = bin_volumes[bin_volumes > 0]
                if len(nonzero_vols) > 0:
                    hvn_thresh = float(np.percentile(nonzero_vols, HVN_THRESHOLD_PERCENTILE))
                    lvn_thresh = float(np.percentile(nonzero_vols, LVN_THRESHOLD_PERCENTILE))

                    profile.hvn_levels = [
                        float(bin_midpoints[i])
                        for i in range(len(bin_volumes))
                        if bin_volumes[i] >= hvn_thresh
                    ]
                    profile.lvn_levels = [
                        float(bin_midpoints[i])
                        for i in range(len(bin_volumes))
                        if 0 < bin_volumes[i] <= lvn_thresh
                    ]

            # Store raw histogram data
            profile.bin_edges = [float(e) for e in bin_edges]
            profile.bin_volumes = [float(v) for v in bin_volumes]

            self.logger.debug(
                "Profile computed: POC=%.2f VA=[%.2f, %.2f] HVN=%d LVN=%d vol=%.0f",
                profile.poc,
                profile.va_low,
                profile.va_high,
                len(profile.hvn_levels),
                len(profile.lvn_levels),
                profile.total_volume,
            )

        except Exception:
            self.logger.exception("Error computing volume profile.")

        return profile

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    def compute_daily_profile(
        self,
        df: pd.DataFrame,
        num_bins: int = DEFAULT_NUM_BINS,
    ) -> VolumeProfile:
        """Compute a volume profile for a single trading session.

        This is a convenience wrapper around compute_profile for a
        DataFrame that already represents one day's bars.

        Args:
            df: Single-session OHLCV DataFrame.
            num_bins: Number of price bins.

        Returns:
            VolumeProfile for the session.
        """
        return self.compute_profile(df, num_bins=num_bins)

    def compute_multi_day_profile(
        self,
        dfs: list[pd.DataFrame],
        days: int = 5,
        num_bins: int = DEFAULT_NUM_BINS,
    ) -> VolumeProfile:
        """Compute a rolling multi-day composite volume profile.

        Concatenates the most recent *days* DataFrames and builds a
        single profile across the combined period.  This highlights
        structural levels that persist across sessions.

        Args:
            dfs: List of daily OHLCV DataFrames (most recent last).
            days: Number of recent days to include.
            num_bins: Number of price bins.

        Returns:
            Composite VolumeProfile.
        """
        if not dfs:
            self.logger.warning("compute_multi_day_profile called with empty list.")
            return VolumeProfile()

        recent = dfs[-days:] if len(dfs) >= days else dfs
        try:
            combined = pd.concat(recent, ignore_index=True)
        except Exception:
            self.logger.exception("Error concatenating DataFrames for multi-day profile.")
            return VolumeProfile()

        self.logger.debug(
            "Computing %d-day composite profile (%d bars).",
            len(recent),
            len(combined),
        )
        return self.compute_profile(combined, num_bins=num_bins)

    # ------------------------------------------------------------------
    # Position and level analysis
    # ------------------------------------------------------------------

    def get_price_position(
        self,
        current_price: float,
        profile: VolumeProfile,
    ) -> str:
        """Determine where the current price sits relative to the Value Area.

        Returns one of:
            "AT_POC"   — within 0.1% of the Point of Control
            "IN_VA"    — inside the Value Area
            "ABOVE_VA" — above the Value Area high
            "BELOW_VA" — below the Value Area low

        Args:
            current_price: The live or most recent price.
            profile: A computed VolumeProfile.

        Returns:
            Position string.
        """
        if profile.poc == 0.0:
            return "IN_VA"  # No profile data — default neutral

        # Check proximity to POC first
        if profile.poc > 0:
            poc_distance = abs(current_price - profile.poc) / profile.poc
            if poc_distance <= POC_PROXIMITY_PCT:
                return "AT_POC"

        if current_price > profile.va_high:
            return "ABOVE_VA"
        if current_price < profile.va_low:
            return "BELOW_VA"
        return "IN_VA"

    def detect_naked_poc(
        self,
        profiles: list[VolumeProfile],
        current_price: float,
    ) -> list[float]:
        """Find POC levels from prior sessions that haven't been revisited.

        A 'naked POC' is a Point of Control from a previous session that
        the market has not traded through since.  These act as magnets
        and often attract price.

        The check is simplified: a POC is considered naked if the current
        price has never been within 0.1% of it (we compare the current
        price only — in production, the intraday bar range should be used).

        Args:
            profiles: List of VolumeProfile objects from prior sessions
                      (most recent last).  The last profile is treated as
                      the current session and excluded from the check.
            current_price: The current market price.

        Returns:
            List of naked POC price levels sorted ascending.
        """
        naked: list[float] = []

        if len(profiles) < 2:
            return naked

        # Exclude the current session (last profile)
        prior_profiles = profiles[:-1]

        for prof in prior_profiles:
            if prof.poc == 0.0:
                continue
            distance_pct = abs(current_price - prof.poc) / prof.poc if prof.poc > 0 else 0.0
            if distance_pct > POC_PROXIMITY_PCT:
                naked.append(prof.poc)

        naked.sort()
        self.logger.debug("Detected %d naked POC levels.", len(naked))
        return naked

    def detect_single_prints(
        self,
        df: pd.DataFrame,
        profile: VolumeProfile,
    ) -> list[float]:
        """Find price levels with extremely low volume during fast moves.

        Single prints occur when the market moves so quickly through a
        price zone that very little volume trades there.  These zones
        often get revisited later.

        Uses the bottom 5th-percentile of non-zero bin volumes as the
        threshold.

        Args:
            df: The OHLCV DataFrame used to build the profile.
            profile: The corresponding VolumeProfile.

        Returns:
            List of single-print price levels.
        """
        if not profile.bin_volumes or not profile.bin_edges:
            return []

        try:
            volumes = np.array(profile.bin_volumes)
            edges = np.array(profile.bin_edges)
            midpoints = (edges[:-1] + edges[1:]) / 2.0

            nonzero = volumes[volumes > 0]
            if len(nonzero) == 0:
                return []

            threshold = float(np.percentile(nonzero, SINGLE_PRINT_PERCENTILE))
            single_prints = [
                float(midpoints[i])
                for i in range(len(volumes))
                if 0 < volumes[i] <= threshold
            ]

            self.logger.debug("Detected %d single-print levels.", len(single_prints))
            return single_prints

        except Exception:
            self.logger.exception("Error detecting single prints.")
            return []

    def is_breakaway_through_lvn(
        self,
        current_price: float,
        profile: VolumeProfile,
    ) -> bool:
        """Check whether the current price is moving through a Low Volume Node.

        When price breaks through an LVN, it signals directional conviction
        because there is no volume cluster to slow or reverse the move.

        Args:
            current_price: The live price.
            profile: A computed VolumeProfile.

        Returns:
            True if price is within or crossing an LVN.
        """
        if not profile.lvn_levels or not profile.bin_edges:
            return False

        try:
            # Determine the bin width for proximity matching
            edges = np.array(profile.bin_edges)
            if len(edges) < 2:
                return False
            bin_width = float(edges[1] - edges[0])
            half_bin = bin_width / 2.0

            for lvn_price in profile.lvn_levels:
                if abs(current_price - lvn_price) <= half_bin:
                    self.logger.debug(
                        "Price %.2f breaking through LVN at %.2f.",
                        current_price,
                        lvn_price,
                    )
                    return True

        except Exception:
            self.logger.exception("Error checking LVN breakaway.")

        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_value_area(
        bin_volumes: np.ndarray,
        bin_midpoints: np.ndarray,
        poc_idx: int,
        total_volume: float,
    ) -> tuple[float, float]:
        """Expand outward from the POC bin until 70% of volume is captured.

        Uses the standard TPO Value Area algorithm:
        1. Start at the POC bin.
        2. Look one bin above and one below.
        3. Add the side with more volume.
        4. Repeat until cumulative volume >= 70% of total.

        Returns:
            (va_high, va_low) price levels.
        """
        if total_volume <= 0 or len(bin_volumes) == 0:
            return (0.0, 0.0)

        target = total_volume * VALUE_AREA_PCT
        cumulative = float(bin_volumes[poc_idx])

        lo = poc_idx
        hi = poc_idx
        n = len(bin_volumes)

        while cumulative < target:
            can_go_up = hi + 1 < n
            can_go_down = lo - 1 >= 0

            if not can_go_up and not can_go_down:
                break

            vol_above = float(bin_volumes[hi + 1]) if can_go_up else -1.0
            vol_below = float(bin_volumes[lo - 1]) if can_go_down else -1.0

            if vol_above >= vol_below:
                hi += 1
                cumulative += float(bin_volumes[hi])
            else:
                lo -= 1
                cumulative += float(bin_volumes[lo])

        va_high = float(bin_midpoints[hi])
        va_low = float(bin_midpoints[lo])
        return (va_high, va_low)
