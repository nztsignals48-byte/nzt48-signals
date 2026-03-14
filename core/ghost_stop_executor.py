"""
Ghost Stop Executor (KRONOS Upgrade #4)
======================================
Hide stop-loss internally with Brownian motion jitter to prevent HFT hunting.
Stop-loss exists only in CPU cache, not sent to exchange.
"""

import random
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class GhostStopState:
    asset: str
    entry_price: float
    base_stop: float
    jitter_range: float
    current_jitter: float
    breached: bool = False
    exit_price: Optional[float] = None


class GhostStopExecutor:
    """
    Ghost Stop: Stop-loss that lives in memory only
    
    Prevents HFT algorithms from hunting retail stops by:
    1. Never sending stop order to exchange
    2. Applying Brownian motion jitter to internal stop price
    3. Firing market order instantly when breached
    """
    
    def __init__(self):
        self.logger = logging.getLogger("nzt48.ghost_stop_executor")
        self.stops = {}  # asset -> GhostStopState
        self.jitter_scale = 0.0002  # ±0.02%
    
    def create_ghost_stop(
        self,
        asset: str,
        entry_price: float,
        stop_pct: float = 2.0
    ) -> GhostStopState:
        """
        Create a ghost stop for an asset
        
        Args:
            asset: Asset ticker (e.g., "QQQ3.L")
            entry_price: Entry price when trade opened
            stop_pct: Stop loss percentage (default 2%)
        
        Returns:
            GhostStopState object
        """
        base_stop = entry_price * (1 - stop_pct / 100)
        jitter_range = base_stop * self.jitter_scale
        current_jitter = random.uniform(-jitter_range, jitter_range)
        
        state = GhostStopState(
            asset=asset,
            entry_price=entry_price,
            base_stop=base_stop,
            jitter_range=jitter_range,
            current_jitter=current_jitter
        )
        
        self.stops[asset] = state
        self.logger.info(
            f"Ghost stop created: {asset} @ £{base_stop:.2f} "
            f"(±£{jitter_range:.4f} jitter range)"
        )
        
        return state
    
    def get_current_stop(self, asset: str) -> float:
        """
        Get current stop price with Brownian motion jitter applied
        
        Jitter changes every call, making stop un-huntable
        """
        if asset not in self.stops:
            return None
        
        state = self.stops[asset]
        # Apply new Brownian motion jitter
        state.current_jitter = random.uniform(-state.jitter_range, state.jitter_range)
        
        return state.base_stop + state.current_jitter
    
    def check_breach(self, asset: str, current_price: float) -> bool:
        """
        Check if current price has breached ghost stop
        
        Returns:
            True if breached (should exit immediately)
        """
        if asset not in self.stops:
            return False
        
        stop_price = self.get_current_stop(asset)
        
        if current_price <= stop_price:
            self.stops[asset].breached = True
            self.stops[asset].exit_price = current_price
            self.logger.warning(
                f"Ghost stop BREACHED: {asset} @ £{current_price:.2f} "
                f"(stop: £{stop_price:.2f})"
            )
            return True
        
        return False
    
    def remove_ghost_stop(self, asset: str):
        """Remove ghost stop for an asset"""
        if asset in self.stops:
            del self.stops[asset]
            self.logger.info(f"Ghost stop removed: {asset}")


if __name__ == "__main__":
    executor = GhostStopExecutor()
    
    # Create ghost stop
    state = executor.create_ghost_stop("QQQ3.L", entry_price=145.50, stop_pct=2.0)
    print(f"✅ Ghost stop created at £{state.base_stop:.2f}")
    
    # Check multiple prices with jittered stops
    test_prices = [145.00, 144.50, 144.00, 142.00]
    for price in test_prices:
        breached = executor.check_breach("QQQ3.L", price)
        stop = executor.get_current_stop("QQQ3.L")
        print(f"   Price £{price:.2f} | Stop £{stop:.4f} | Breached: {breached}")
