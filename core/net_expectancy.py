"""
Net Expectancy Engine -- NZT-48 Institutional Risk Metric
Thorp (1997): Kelly Criterion; Vince (1992): Mathematics of Money Management.
Formula: E = (WinRate x AvgWinR) - ((1 - WinRate) x AvgLossR) - CostDrag
Threshold: E > 0.10 to trade; E < 0.05 = skip; E < 0.0 = immediate halt.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = "data/net_expectancy.json"

# Grade thresholds (E per dollar risked)
GRADE_STRONG = 0.20      # E > 0.20: full size
GRADE_POSITIVE = 0.10    # E 0.10-0.20: standard size
GRADE_MARGINAL = 0.05    # E 0.05-0.10: half size
GRADE_NO_EDGE = 0.0      # E 0-0.05: skip (veto)
GRADE_NEGATIVE = -999    # E < 0: immediate halt + Telegram alert

# Spread and slippage estimates per leverage class
_SPREAD_BPS = {
    "QQQ3.L": 15, "3LUS.L": 20, "3SEM.L": 25, "GPT3.L": 30,
    "NVD3.L": 20, "TSL3.L": 25, "TSM3.L": 25, "MU2.L": 30,
    "QQQS.L": 15, "3USS.L": 15, "QQQ5.L": 12, "SP5L.L": 12,
}
_SLIPPAGE_3X = 0.001   # 0.1% slippage estimate for 3x ETPs
_SLIPPAGE_5X = 0.002   # 0.2% slippage estimate for 5x ETPs


class NetExpectancyEngine:
    """
    Computes net expectancy per strategy/ticker/regime.
    Refreshes every 50 trades.

    Thresholds:
      E > 0.20: Strong edge -- full size
      E 0.10-0.20: Positive edge -- standard size
      E 0.05-0.10: Marginal edge -- half size
      E < 0.05: No edge -- skip (veto)
      E < 0.0: Negative edge -- immediate halt + Telegram alert
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
        return {"expectancy": {}, "last_update": {}}

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(self.state_file) if os.path.dirname(self.state_file) else ".", exist_ok=True)
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.warning("NetExpectancyEngine: save failed: %s", e)

    def compute_expectancy(self, ticker: str, outcomes: list,
                           strategy: str = "S15", regime: str = "ALL") -> dict:
        """
        Computes net expectancy from a list of outcome dicts.
        Each outcome must have: status (WIN/LOSS/STOPPED_OUT), r_multiple (float).
        Returns full expectancy dict including grade and size_multiplier.
        """
        # Filter relevant outcomes
        relevant = [
            o for o in outcomes
            if o.get("ticker") == ticker
            and o.get("status") in ("WIN", "LOSS", "STOPPED_OUT")
            and (strategy == "ALL" or o.get("strategy") == strategy)
            and (regime == "ALL" or o.get("regime") == regime)
        ]

        if len(relevant) < 10:
            return {
                "ticker": ticker, "strategy": strategy, "regime": regime,
                "gross_e": None, "net_e": None, "grade": "INSUFFICIENT_DATA",
                "sample_n": len(relevant), "size_multiplier": 1.0,
                "veto": False,
            }

        wins = [o for o in relevant if o.get("status") == "WIN"]
        losses = [o for o in relevant if o.get("status") in ("LOSS", "STOPPED_OUT")]

        win_rate = len(wins) / len(relevant)
        avg_win_r = sum(o.get("r_multiple", 0) for o in wins) / max(1, len(wins))
        avg_loss_r = abs(sum(o.get("r_multiple", 0) for o in losses) / max(1, len(losses)))

        # Gross expectancy
        gross_e = (win_rate * avg_win_r) - ((1 - win_rate) * avg_loss_r)

        # Cost drag
        spread_bps = _SPREAD_BPS.get(ticker, 20)
        is_5x = ticker in ("QQQ5.L", "SP5L.L")
        slippage = _SLIPPAGE_5X if is_5x else _SLIPPAGE_3X
        cost_drag = (spread_bps / 10000) + slippage
        net_e = gross_e - cost_drag

        # Grade + size multiplier
        if net_e > GRADE_STRONG:
            grade, size_mult = "STRONG", 1.0
        elif net_e > GRADE_POSITIVE:
            grade, size_mult = "POSITIVE", 1.0
        elif net_e > GRADE_MARGINAL:
            grade, size_mult = "MARGINAL", 0.5
        elif net_e >= GRADE_NO_EDGE:
            grade, size_mult = "NO_EDGE", 0.0
        else:
            grade, size_mult = "NEGATIVE", 0.0

        result = {
            "ticker": ticker, "strategy": strategy, "regime": regime,
            "gross_e": round(gross_e, 4),
            "net_e": round(net_e, 4),
            "win_rate": round(win_rate, 3),
            "avg_win_r": round(avg_win_r, 3),
            "avg_loss_r": round(avg_loss_r, 3),
            "cost_drag": round(cost_drag, 4),
            "sample_n": len(relevant),
            "grade": grade,
            "size_multiplier": size_mult,
            "veto": net_e < GRADE_MARGINAL,
            "halt": net_e < 0,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }

        key = f"{ticker}:{strategy}:{regime}"
        self.state["expectancy"][key] = result
        self.state["last_update"][key] = result["computed_at"]
        self._save_state()
        return result

    def get_expectancy(self, ticker: str, strategy: str = "S15", regime: str = "ALL") -> Optional[dict]:
        """Returns cached expectancy or None if not computed."""
        key = f"{ticker}:{strategy}:{regime}"
        return self.state["expectancy"].get(key)

    def get_entry_veto(self, ticker: str, strategy: str = "S15") -> tuple:
        """Returns (veto: bool, reason: str)."""
        result = self.get_expectancy(ticker, strategy)
        if result is None:
            return False, "no_data"
        if result.get("halt"):
            net_e_val = result["net_e"]
            return True, f"NEGATIVE_EXPECTANCY(E={net_e_val:.4f}) -- HALT"
        if result.get("veto"):
            net_e_val = result["net_e"]
            grade_val = result["grade"]
            return True, f"no_edge(E={net_e_val:.4f},grade={grade_val})"
        return False, "ok"

    def get_size_multiplier(self, ticker: str, strategy: str = "S15") -> float:
        result = self.get_expectancy(ticker, strategy)
        if result is None:
            return 1.0
        return result.get("size_multiplier", 1.0)

    def refresh_all(self, outcomes: list, tickers: list = None, strategy: str = "S15") -> dict:
        """Refreshes expectancy for all given tickers."""
        results = {}
        if tickers is None:
            tickers = list(set(o.get("ticker") for o in outcomes if o.get("ticker")))
        for ticker in tickers:
            results[ticker] = self.compute_expectancy(ticker, outcomes, strategy)
        return results

    def get_telegram_summary(self, tickers: list) -> str:
        lines = ["Net Expectancy:"]
        for t in tickers:
            result = self.get_expectancy(t)
            if not result:
                lines.append(f"  {t}: no data")
            else:
                grade = result["grade"]
                net_e = result.get("net_e", 0)
                sample_n = result["sample_n"]
                lines.append(f"  {t}: E={net_e:+.3f} ({grade}) n={sample_n}")
        return chr(10).join(lines)
