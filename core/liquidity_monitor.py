"""
Liquidity Black Hole Monitor — NZT-48
Brunnermeier & Pedersen (2009): "Market Liquidity and Funding Liquidity"
Review of Financial Studies 22(6):2201-2238.

KEY FINDING: Market liquidity and funding liquidity mutually reinforce in crises.
When traders face margin calls they liquidate → prices fall → more margin calls.
For 3x leveraged ETPs, the daily rebalancing mechanism AMPLIFIES this spiral:
a 3x ETP that falls 5% on the underlying MUST sell underlying at close to rebalance,
further depressing the underlying. This creates dangerous feedback loops.

THREE WARNING INDICATORS (trigger 2/3 = cut 50%, trigger 3/3 = full exit):
1. Bid-ask spread > 3x 20-day average
2. VIX term structure inverted (spot > 3-month futures)
3. SOFR-OIS spread > 15bps (funding stress signal)

LEVERAGED ETP SPECIFIC: if underlying daily sigma > 2%, volatility drag on 3x ETP
makes expected daily return NEGATIVE regardless of direction. Hard exit rule.
"""

import json
import logging
import os
import urllib.request
from datetime import date, datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = "data/liquidity_monitor.json"

# Thresholds from Brunnermeier & Pedersen (2009) + empirical calibration
SPREAD_MULTIPLIER_WARNING = 3.0   # spread > 3x 20-day avg = warning
SOFR_OIS_WARNING_BPS = 15         # funding stress begins
SOFR_OIS_CRISIS_BPS = 25          # crisis level (March 2020: spiked to 80bps)

# Volatility drag kill switch: 3x ETP expected return goes negative at this threshold
UNDERLYING_SIGMA_MAX_PCT = 2.0    # daily sigma > 2% → leveraged ETP untradeble

# VIX term structure: contango = normal, inversion = stress
VIX_TERM_STRUCT_INVERSION_THRESHOLD = 0.0  # VIX spot > VIX 3M = inverted

# Status levels
STATUS_NORMAL = "NORMAL"
STATUS_WARNING = "WARNING"   # 1/3 indicators
STATUS_ELEVATED = "ELEVATED" # 2/3 indicators → cut 50%
STATUS_CRISIS = "CRISIS"     # 3/3 indicators → full exit


class LiquidityMonitor:
    """
    Monitors three independent liquidity stress indicators.
    Provides hard position-sizing rules when liquidity deteriorates.
    """

    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self.state = self._load_state()
        self._spread_history: dict = {}  # ticker → list of recent spreads

    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "spread_baselines": {},
            "sofr_ois_bps": None,
            "vix_spot": None,
            "vix_3m_futures": None,
            "last_update": None,
            "trigger_count": 0,
            "status": STATUS_NORMAL,
        }

    def _save_state(self):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.warning("LiquidityMonitor: save failed: %s", e)

    # ─────────────────────────────────────────────────────────
    # Indicator 1: Bid-Ask Spread vs baseline
    # ─────────────────────────────────────────────────────────

    def record_spread(self, ticker: str, spread_pct: float):
        """Record a bid-ask spread observation (spread as % of mid-price)."""
        history = self.state["spread_baselines"].setdefault(ticker, [])
        history.append({"date": date.today().isoformat(), "spread": spread_pct})
        if len(history) > 20:
            history[:] = history[-20:]
        self._save_state()

    def is_spread_elevated(self, ticker: str, current_spread_pct: float) -> bool:
        """True if current spread > 3x 20-day average spread."""
        history = self.state["spread_baselines"].get(ticker, [])
        if len(history) < 5:
            return False  # Not enough baseline data
        avg_spread = sum(h["spread"] for h in history) / len(history)
        if avg_spread <= 0:
            return False
        return current_spread_pct > avg_spread * SPREAD_MULTIPLIER_WARNING

    # ─────────────────────────────────────────────────────────
    # Indicator 2: VIX term structure
    # ─────────────────────────────────────────────────────────

    def update_vix(self, vix_spot: float, vix_3m: Optional[float] = None):
        """Update VIX spot and 3-month futures level."""
        self.state["vix_spot"] = vix_spot
        if vix_3m is not None:
            self.state["vix_3m_futures"] = vix_3m
        self.state["last_update"] = datetime.now(timezone.utc).isoformat()
        self._save_state()

    def is_vix_inverted(self) -> bool:
        """True if VIX spot > VIX 3-month futures (contango inverted = stress)."""
        vix_spot = self.state.get("vix_spot")
        vix_3m = self.state.get("vix_3m_futures")
        if vix_spot is None or vix_3m is None:
            # Fallback: if VIX spot > 30 with no futures data, assume stress
            return (vix_spot or 0) > 30
        return vix_spot > vix_3m + VIX_TERM_STRUCT_INVERSION_THRESHOLD

    def fetch_vix_from_yfinance(self) -> Optional[float]:
        """Fetch current VIX from yfinance as fallback."""
        try:
            import yfinance as yf
            vix = yf.Ticker("^VIX")
            price = vix.fast_info.get("lastPrice")
            if price and price > 0:
                self.update_vix(float(price))
                return float(price)
        except Exception as e:
            logger.debug("LiquidityMonitor: VIX fetch failed: %s", e)
        return None

    # ─────────────────────────────────────────────────────────
    # Indicator 3: SOFR-OIS spread (funding stress)
    # ─────────────────────────────────────────────────────────

    def update_sofr_ois(self, spread_bps: float):
        """Update SOFR-OIS spread in basis points."""
        self.state["sofr_ois_bps"] = spread_bps
        self.state["last_update"] = datetime.now(timezone.utc).isoformat()
        self._save_state()

    def is_sofr_ois_stressed(self) -> bool:
        """True if SOFR-OIS spread > 15bps (funding stress beginning)."""
        bps = self.state.get("sofr_ois_bps")
        if bps is None:
            return False
        return bps > SOFR_OIS_WARNING_BPS

    def is_sofr_ois_crisis(self) -> bool:
        """True if SOFR-OIS spread > 25bps (crisis level)."""
        bps = self.state.get("sofr_ois_bps")
        if bps is None:
            return False
        return bps > SOFR_OIS_CRISIS_BPS

    # ─────────────────────────────────────────────────────────
    # Volatility drag kill switch (Thorp / MacLean)
    # ─────────────────────────────────────────────────────────

    def is_vol_drag_zone(self, underlying_daily_sigma_pct: float) -> bool:
        """
        True if underlying daily sigma > 2% — at this level, 3x ETPs have
        NEGATIVE expected return due to volatility decay (beta-slippage).
        MacLean, Thorp & Ziemba (2011): Kelly fraction → 0 at sigma > 2%/day.
        """
        return underlying_daily_sigma_pct > UNDERLYING_SIGMA_MAX_PCT

    # ─────────────────────────────────────────────────────────
    # Composite status and position sizing
    # ─────────────────────────────────────────────────────────

    def get_status(
        self,
        ticker: Optional[str] = None,
        current_spread_pct: Optional[float] = None,
    ) -> dict:
        """
        Evaluates all three indicators and returns composite status.
        Returns:
            {
                "status": STATUS_*,
                "trigger_count": int (0-3),
                "size_multiplier": float,
                "should_exit_leveraged": bool,
                "reasons": list[str],
            }
        """
        triggers = []

        # Indicator 1: spread
        if ticker and current_spread_pct and self.is_spread_elevated(ticker, current_spread_pct):
            triggers.append(f"Spread elevated ({current_spread_pct:.3f}% > 3x baseline)")

        # Indicator 2: VIX term structure
        if self.is_vix_inverted():
            vix_spot = self.state.get("vix_spot", 0)
            triggers.append(f"VIX inverted (spot={vix_spot:.1f} > 3M futures)")

        # Indicator 3: SOFR-OIS
        if self.is_sofr_ois_stressed():
            bps = self.state.get("sofr_ois_bps", 0)
            triggers.append(f"SOFR-OIS stress ({bps:.0f}bps > {SOFR_OIS_WARNING_BPS}bps)")

        n = len(triggers)
        self.state["trigger_count"] = n

        if n == 0:
            status = STATUS_NORMAL
            size_mult = 1.0
            exit_lev = False
        elif n == 1:
            status = STATUS_WARNING
            size_mult = 0.75
            exit_lev = False
        elif n == 2:
            status = STATUS_ELEVATED
            size_mult = 0.50
            exit_lev = False
        else:
            status = STATUS_CRISIS
            size_mult = 0.0  # halt
            exit_lev = True  # exit all leveraged ETPs immediately

        self.state["status"] = status
        self._save_state()

        return {
            "status": status,
            "trigger_count": n,
            "size_multiplier": size_mult,
            "should_exit_leveraged": exit_lev,
            "reasons": triggers,
        }

    def get_size_multiplier(self, ticker: Optional[str] = None, current_spread_pct: Optional[float] = None) -> float:
        """Returns position size multiplier based on current liquidity status."""
        return self.get_status(ticker, current_spread_pct)["size_multiplier"]

    def should_halt_leveraged_etps(self) -> bool:
        """True if all 3 triggers are active — full exit of leveraged products."""
        return self.state.get("trigger_count", 0) >= 3

    # ─────────────────────────────────────────────────────────
    # Telegram alert
    # ─────────────────────────────────────────────────────────

    def get_telegram_alert(self, ticker: Optional[str] = None, current_spread_pct: Optional[float] = None) -> str:
        s = self.get_status(ticker, current_spread_pct)
        if s["status"] == STATUS_NORMAL:
            return ""

        emoji = {"WARNING": "⚠️", "ELEVATED": "🔴", "CRISIS": "🚨"}[s["status"]]
        lines = [
            f"{emoji} LIQUIDITY MONITOR — {s['status']} ({s['trigger_count']}/3 triggers)",
        ]
        for r in s["reasons"]:
            lines.append(f"  • {r}")
        lines.append(f"  Action: size multiplier = {s['size_multiplier']:.0%}")
        if s["should_exit_leveraged"]:
            lines.append("  🚨 EXIT ALL LEVERAGED ETPs — Brunnermeier-Pedersen spiral risk")
        return "\n".join(lines)
