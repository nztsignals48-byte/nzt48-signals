"""BOOK 206: MULTI-LEG ARBITRAGE — Spreads across related instruments"""

import sys
from typing import Dict, Optional

log_name = "multi_leg_arb"


def multi_leg_arb_signal(
    ticker_id: str, msg: Dict, ind: Dict, conf_floor: int, kelly_fn, common_fields: Dict
) -> Optional[Dict]:
    """
    Generate spread arbitrage signals.

    Multi-leg opportunities:
    - ETF vs underlying basis (e.g., UPRO vs SPY)
    - Sector ETF spreads (XLV vs XLE momentum divergence)
    - Currency triangles (GBP/EUR misalignment)
    """
    try:
        # ETF-underlying pairs to monitor
        etf_underlying_pairs = {
            "UPRO": "SPY",  # 3x SPY
            "SQQQ": "QQQ",  # 3x Nasdaq (inverse)
            "VTI": "VOO",   # Total US vs S&P 500
        }

        if ticker_id not in etf_underlying_pairs:
            return None

        underlying = etf_underlying_pairs[ticker_id]
        etf_price = msg.get("ltp", 0)

        if etf_price <= 0:
            return None

        # Spread calculation would use bid/ask + fair value
        # Simplified: check if recent vol indicates mispricing
        vol_rank = msg.get("vol_rank", 0.5)

        # Fire if vol is extreme (likely mis-pricing)
        if vol_rank < 0.2 or vol_rank > 0.8:
            confidence = int(50 + 30 * abs(vol_rank - 0.5))

            if confidence >= conf_floor:
                direction = "SELL" if vol_rank > 0.8 else "BUY"

                signal = {
                    **common_fields,
                    "strategy": "MULTILEG",
                    "ticker": ticker_id,
                    "direction": direction,
                    "confidence": confidence,
                    "kelly_fraction": kelly_fn("MULTILEG", {"edge_bps": 30}),
                    "max_hold_hours": 2,
                    "_spread_pair": f"{ticker_id}/{underlying}",
                    "_vol_rank": vol_rank,
                }

                sys.stderr.write(f"MULTILEG signal: {ticker_id} vol_rank={vol_rank:.2f} conf={confidence}\n")
                sys.stderr.flush()

                return signal

        return None

    except Exception as e:
        sys.stderr.write(f"MULTILEG error: {e}\n")
        return None
