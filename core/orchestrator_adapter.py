"""
Orchestrator Adapter - Bridges Master Orchestrator with DailyTargetStrategy

The DailyTargetStrategy.scan() requires:
- tickers: list of assets
- indicators: technical indicators
- market_ctx: market context
- sector_flows: sector flow data
- narratives: narrative context

This adapter converts simplified market_data to the required format.
"""

from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
import logging
from strategies.daily_target import DailyTargetStrategy, MarketContext, SectorFlow, NarrativeContext, IndicatorSnapshot
from models import RegimeState, GEXRegime, TimeWindow

logger = logging.getLogger("nzt48.orchestrator_adapter")


@dataclass
class SimplifiedSignalRequest:
    """Simplified signal request compatible with orchestrator"""
    ticker: str
    market_data: Dict[str, Any]
    indicators: Optional[Dict[str, IndicatorSnapshot]] = None
    position: Optional[Dict[str, Any]] = None


class OrchestratorAdapter:
    """Converts simplified requests to DailyTargetStrategy format"""
    
    def __init__(self):
        self.strategy = DailyTargetStrategy()
    
    def build_market_context(self, market_data: Dict[str, Any]) -> MarketContext:
        """Convert market_data dict to MarketContext (with graceful fallbacks)"""
        
        try:
            # Try minimal initialization with required fields
            return MarketContext(
                timestamp=market_data.get('timestamp', datetime.now(timezone.utc)),
                regime=RegimeState.RANGE_BOUND,
                regime_confidence=0.5,
                regime_duration_bars=0,
                qqq_vs_vwap=0,
                spy_vs_vwap=0,
                ema_alignment='NEUTRAL',
                gex_regime=GEXRegime.POSITIVE,
                gex_value=0,
                dix_value=0,
                dix_signal='NEUTRAL',
                dix_gex_regime='NEUTRAL',
                dix_trend='FLAT',
                tick=0,
                trin=1,
                add=0,
                vold=0,
                internals_composite=0,
                internals_confidence_adj=50,
                vix=market_data.get('vix', 20),
                vix3m=market_data.get('vix', 20),
                vix_term_structure='NORMAL',
                dxy=market_data.get('dxy', 104),
                ten_year_yield=4.0,
                put_call_ratio=0.8,
                macro_score=50,
                time_window=TimeWindow.US_SESSION_OPEN,
                fomc_today=False,
                earnings_tonight=[],
                cpi_nfp_today=False,
                calendar_risk='LOW',
                premarket_brief=None,
            )
        except Exception as e:
            # Last resort: return minimal stub
            logger.warning(f"MarketContext build failed: {e}")
            return MarketContext(
                timestamp=market_data.get('timestamp', datetime.now(timezone.utc)),
                regime=RegimeState.RANGE_BOUND,
                regime_confidence=0.5,
                regime_duration_bars=0,
                qqq_vs_vwap=0,
                spy_vs_vwap=0,
                ema_alignment='NEUTRAL',
                gex_regime=GEXRegime.POSITIVE,
                gex_value=0,
                dix_value=0,
                dix_signal='NEUTRAL',
                dix_gex_regime='NEUTRAL',
                dix_trend='FLAT',
                tick=0,
                trin=1,
                add=0,
                vold=0,
                internals_composite=0,
                internals_confidence_adj=50,
                vix=20,
                vix3m=20,
                vix_term_structure='NORMAL',
                dxy=104,
                ten_year_yield=4.0,
                put_call_ratio=0.8,
                macro_score=50,
                time_window=TimeWindow.US_SESSION_OPEN,
                fomc_today=False,
                earnings_tonight=[],
                cpi_nfp_today=False,
                calendar_risk='LOW',
                premarket_brief=None,
            )
    
    def build_sector_flows(self) -> Dict[str, SectorFlow]:
        """Build minimal sector flows"""
        return {
            'TECH': SectorFlow(
                sector='TECH',
                net_flow=0,
                momentum=0,
                regime='NEUTRAL',
            ),
            'FINANCE': SectorFlow(
                sector='FINANCE',
                net_flow=0,
                momentum=0,
                regime='NEUTRAL',
            ),
        }
    
    def build_narratives(self) -> Dict[str, NarrativeContext]:
        """Build minimal narratives"""
        return {
            'macro': NarrativeContext(
                theme='macro',
                confidence=0.5,
                sentiment=0.5,
                momentum=0,
            )
        }
    
    def build_indicators(self, market_data: Dict[str, Any]) -> Dict[str, IndicatorSnapshot]:
        """Build minimal indicators"""
        return {
            'DEFAULT': IndicatorSnapshot(
                adx=25,
                rsi=50,
                bb_width=0.02,
                rvol=1.0,
                ofi=market_data.get('ofi', 0),
                momentum_12=market_data.get('momentum', 0),
            )
        }
    
    async def generate_signal(self, ticker: str, market_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Generate signal using DailyTargetStrategy with simplified input"""
        try:
            # Build required context objects
            market_ctx = self.build_market_context(market_data)
            sector_flows = self.build_sector_flows()
            narratives = self.build_narratives()
            indicators = self.build_indicators(market_data)
            
            # Call DailyTargetStrategy.scan()
            signals = self.strategy.scan(
                tickers=[ticker],
                indicators=indicators,
                market_ctx=market_ctx,
                sector_flows=sector_flows,
                narratives=narratives
            )
            
            # Return first signal for this ticker
            if signals:
                signal_dict = {
                    'ticker': ticker,
                    'confidence': getattr(signals[0], 'confidence', 0),
                    'position_size': getattr(signals[0], 'size_multiplier', 1.0),
                    'direction': getattr(signals[0], 'direction', 'NEUTRAL'),
                    'raw_signal': signals[0],
                }
                return signal_dict
            
            return None
        
        except Exception as e:
            import logging
            logging.warning(f"Adapter signal generation error: {e}")
            return None


# Global adapter instance
_adapter: Optional[OrchestratorAdapter] = None


def get_adapter() -> OrchestratorAdapter:
    """Get or create adapter instance"""
    global _adapter
    if _adapter is None:
        _adapter = OrchestratorAdapter()
    return _adapter


if __name__ == "__main__":
    import asyncio
    
    async def test():
        adapter = get_adapter()
        
        market_data = {
            'timestamp': datetime.now(timezone.utc),
            'volatility': 0.15,
            'momentum': 0.02,
            'ofi': 100000,
            'regime': 'NORMAL',
            'vix': 20,
            'dxy': 104,
        }
        
        signal = await adapter.generate_signal('QQQ3.L', market_data)
        if signal:
            print(f"✅ Signal: {signal}")
        else:
            print("❌ No signal")
    
    asyncio.run(test())
