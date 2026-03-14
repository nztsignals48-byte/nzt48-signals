"""
Accruals Quality Veto — NZT-48 Academic Signal Module
Sloan (1996): high accruals (earnings > operating cash flow) = lower quality
earnings = mean-revert within 1 year. Reduces PEAD signal confidence boost
or vetoes it entirely when earnings quality is suspect.

Applies to: NVDA→NVD3.L, TSLA→TSL3.L, AMD→AMD3.L, TSM→TSM3.L, MU→MU2.L
Index ETPs (QQQ, SPY) exempt.
"""

import logging
import json
import os
from datetime import datetime, date, timezone
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = "data/accruals_state.json"

# Accruals ratio thresholds
ACCRUALS_WARN_THRESHOLD = 0.05   # > 5% of assets = earnings quality warning
ACCRUALS_VETO_THRESHOLD = 0.10   # > 10% of assets = veto PEAD signal entirely

# PEAD confidence reduction when accruals warning triggered
PEAD_REDUCTION_WARN = 0.50   # Reduce PEAD boost by 50% on warning
PEAD_REDUCTION_VETO = 1.00   # Cancel PEAD boost entirely on veto

# Map LSE ETP → US underlying with options/fundamentals
_LSE_TO_UNDERLYING = {
    "NVD3.L": "NVDA",
    "TSL3.L": "TSLA",
    "TSM3.L": "TSM",
    "MU2.L": "MU",
    "3SEM.L": "SMH",  # synthetic — will return None
}

# Tickers that support accruals analysis
_ACCRUALS_UNIVERSE = {"NVDA", "TSLA", "TSM", "MU", "AMD", "ARM", "MSFT", "META"}


class AccrualsQualityVeto:
    """
    Computes accruals ratio from yfinance quarterly financials.

    Sloan (1996) formula:
      Accruals Ratio = (Net Income - Operating Cash Flow) / Total Assets
      Ratio > 0.05: earnings quality warning — reduce PEAD confidence 50%
      Ratio > 0.10: earnings quality veto — cancel PEAD signal entirely

    High accruals = earnings driven by accounting choices, not cash generation.
    These earnings tend to reverse within 4-8 quarters.
    """

    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"accruals": {}, "last_update": {}}

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(self.state_file) if os.path.dirname(self.state_file) else ".", exist_ok=True)
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.warning("AccrualsQualityVeto: save failed: %s", e)

    # ─────────────────────────────────────────────────────────
    # Accruals computation
    # ─────────────────────────────────────────────────────────

    def compute_accruals(self, ticker: str) -> Optional[dict]:
        """
        Fetches quarterly financials from yfinance and computes accruals ratio.
        Returns None for LSE index ETPs or on data failure.
        Cache is valid for 24 hours.
        """
        underlying = _LSE_TO_UNDERLYING.get(ticker, ticker.replace(".L", ""))
        if underlying not in _ACCRUALS_UNIVERSE:
            return None

        # Check cache freshness (24h)
        last_update = self.state["last_update"].get(underlying)
        if last_update:
            age_hours = (
                datetime.now(timezone.utc) -
                datetime.fromisoformat(last_update)
            ).total_seconds() / 3600
            if age_hours < 24 and underlying in self.state["accruals"]:
                return self.state["accruals"][underlying]

        try:
            import yfinance as yf
            stock = yf.Ticker(underlying)

            # Quarterly income statement and cash flow
            income = stock.quarterly_financials
            cashflow = stock.quarterly_cashflow
            balance = stock.quarterly_balance_sheet

            if income is None or income.empty:
                return None
            if cashflow is None or cashflow.empty:
                return None
            if balance is None or balance.empty:
                return None

            # Use most recent quarter
            # Net Income
            net_income = None
            for label in ["Net Income", "Net Income Common Stockholders", "NetIncome"]:
                if label in income.index:
                    val = income.loc[label].iloc[0]
                    if val is not None and not (isinstance(val, float) and (val != val)):
                        net_income = float(val)
                        break

            # Operating Cash Flow
            op_cf = None
            for label in ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities", "OperatingCashFlow"]:
                if label in cashflow.index:
                    val = cashflow.loc[label].iloc[0]
                    if val is not None and not (isinstance(val, float) and (val != val)):
                        op_cf = float(val)
                        break

            # Total Assets
            total_assets = None
            for label in ["Total Assets", "TotalAssets"]:
                if label in balance.index:
                    val = balance.loc[label].iloc[0]
                    if val is not None and not (isinstance(val, float) and (val != val)):
                        total_assets = float(val)
                        break

            if net_income is None or op_cf is None or total_assets is None or total_assets == 0:
                logger.debug("AccrualsQualityVeto: incomplete data for %s", underlying)
                return None

            accruals_ratio = (net_income - op_cf) / abs(total_assets)

            # Classify
            if accruals_ratio > ACCRUALS_VETO_THRESHOLD:
                quality = "VETO"
                pead_reduction = PEAD_REDUCTION_VETO
            elif accruals_ratio > ACCRUALS_WARN_THRESHOLD:
                quality = "WARNING"
                pead_reduction = PEAD_REDUCTION_WARN
            else:
                quality = "OK"
                pead_reduction = 0.0

            result = {
                "ticker": underlying,
                "accruals_ratio": round(accruals_ratio, 4),
                "net_income": net_income,
                "operating_cf": op_cf,
                "total_assets": total_assets,
                "quality": quality,
                "pead_reduction": pead_reduction,
                "computed_at": datetime.now(timezone.utc).isoformat(),
            }

            self.state["accruals"][underlying] = result
            self.state["last_update"][underlying] = result["computed_at"]
            self._save_state()

            logger.info(
                "Accruals %s: ratio=%.4f quality=%s (NI=$%.0fM CF=$%.0fM)",
                underlying, accruals_ratio, quality,
                (net_income or 0) / 1e6, (op_cf or 0) / 1e6,
            )
            return result

        except Exception as e:
            logger.debug("AccrualsQualityVeto.compute_accruals(%s): %s", ticker, e)
            return None

    # ─────────────────────────────────────────────────────────
    # PEAD veto interface
    # ─────────────────────────────────────────────────────────

    def get_pead_multiplier(self, ticker: str) -> float:
        """
        Returns multiplier for PEAD confidence boost.
        1.0 = full boost (good earnings quality)
        0.5 = half boost (accruals warning)
        0.0 = no boost (accruals veto — earnings quality suspect)
        """
        result = self.compute_accruals(ticker)
        if result is None:
            return 1.0  # No data — don't penalise; let PEAD fire normally

        return 1.0 - result["pead_reduction"]

    def should_veto_pead(self, ticker: str) -> bool:
        """Returns True if accruals are too high to trust a PEAD signal."""
        result = self.compute_accruals(ticker)
        if result is None:
            return False
        return result["quality"] == "VETO"

    def get_quality(self, ticker: str) -> str:
        """Returns 'OK', 'WARNING', 'VETO', or 'NO_DATA'."""
        result = self.compute_accruals(ticker)
        if result is None:
            return "NO_DATA"
        return result["quality"]

    def get_accruals_ratio(self, ticker: str) -> Optional[float]:
        """Returns raw accruals ratio or None."""
        result = self.compute_accruals(ticker)
        return result["accruals_ratio"] if result else None

    # ─────────────────────────────────────────────────────────
    # Telegram
    # ─────────────────────────────────────────────────────────

    def get_telegram_note(self, ticker: str) -> str:
        result = self.compute_accruals(ticker)
        if result is None:
            return f"📊 Accruals {ticker}: N/A (index ETP or no data)"

        ratio = result["accruals_ratio"]
        quality = result["quality"]
        pead_mult = self.get_pead_multiplier(ticker)

        emoji = "✅" if quality == "OK" else "⚠️" if quality == "WARNING" else "🚫"
        pead_str = f"PEAD ×{pead_mult:.1f}" if pead_mult < 1.0 else ""
        return (
            f"{emoji} Accruals {ticker}: ratio={ratio:.4f} ({quality})"
            + (f" — {pead_str}" if pead_str else "")
        )
