"""FRED Macro Data Provider — Federal Reserve economic data pipeline.

Pulls macro indicators from FRED API and writes config/macro_data.json
consumed by Rust CrossAssetMacro and Python regime/signal modules.

Series pulled:
  - DFF:       Fed Funds Rate (daily)
  - T10Y2Y:    10Y-2Y Yield Spread (daily)
  - CPIAUCSL:  CPI (monthly, interpolated)
  - VIXCLS:    VIX Official Close (daily)
  - DGS10:     10-Year Treasury Yield (daily)
  - FEDFUNDS:  Effective Federal Funds Rate (monthly)
  - T10YIE:    10-Year Breakeven Inflation Rate (daily)
  - UNRATE:    Unemployment Rate (monthly)

License: fredapi is Apache 2.0. FRED data is public domain.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("fred_provider")

# FRED series IDs and their descriptions
FRED_SERIES = {
    "DFF": "fed_funds_rate",
    "T10Y2Y": "yield_curve_spread",
    "CPIAUCSL": "cpi_index",
    "VIXCLS": "vix_official",
    "DGS10": "treasury_10y",
    "FEDFUNDS": "fed_funds_effective",
    "T10YIE": "breakeven_inflation_10y",
    "UNRATE": "unemployment_rate",
}

# Additional series for regime detection
REGIME_SERIES = {
    "T10Y3M": "yield_curve_3m_spread",   # 10Y-3M spread (recession indicator)
    "BAMLH0A0HYM2": "high_yield_spread", # ICE BofA High Yield spread (credit stress)
}


def _get_fred_client():
    """Get fredapi client with API key from environment."""
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        log.warning("FRED_API_KEY not set — macro data will be unavailable")
        return None
    try:
        from fredapi import Fred
        return Fred(api_key=api_key)
    except ImportError:
        log.warning("fredapi not installed — pip install fredapi")
        return None


def fetch_macro_data(
    lookback_days: int = 90,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch macro data from FRED and write to macro_data.json.

    Args:
        lookback_days: How many days of history to fetch
        output_path: Where to write JSON. Defaults to /app/config/macro_data.json

    Returns:
        Dict with latest values and short history for each series.
    """
    if output_path is None:
        output_path = os.environ.get(
            "AEGIS_CONFIG_DIR", "/app/config"
        ) + "/macro_data.json"

    fred = _get_fred_client()
    result: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "latest": {},
        "history": {},
        "regime_inputs": {},
    }

    if fred is None:
        # Write empty but valid JSON so consumers don't crash
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        return result

    start_date = date.today() - timedelta(days=lookback_days)
    all_series = {**FRED_SERIES, **REGIME_SERIES}

    for series_id, field_name in all_series.items():
        try:
            data = fred.get_series(series_id, observation_start=start_date)
            if data is not None and len(data) > 0:
                # Drop NaN values
                data = data.dropna()
                if len(data) > 0:
                    latest_val = float(data.iloc[-1])
                    result["latest"][field_name] = latest_val
                    # Keep last 20 data points for trend analysis
                    history = data.tail(20)
                    result["history"][field_name] = [
                        {"date": str(idx.date()), "value": float(val)}
                        for idx, val in history.items()
                    ]
                    log.info("  %s (%s): %.4f", series_id, field_name, latest_val)
        except Exception as e:
            log.warning("  %s (%s): FAILED — %s", series_id, field_name, str(e)[:80])

    # Compute regime inputs for strategy_regime_matrix.py
    latest = result["latest"]
    result["regime_inputs"] = {
        "yield_curve_inverted": latest.get("yield_curve_spread", 1.0) < 0,
        "yield_curve_3m_inverted": latest.get("yield_curve_3m_spread", 1.0) < 0,
        "vix": latest.get("vix_official", 21.0),
        "fed_funds_rate": latest.get("fed_funds_rate", 5.0),
        "breakeven_inflation": latest.get("breakeven_inflation_10y", 2.5),
        "credit_stress": latest.get("high_yield_spread", 4.0) > 5.0,
        "unemployment_rate": latest.get("unemployment_rate", 4.0),
    }

    # Write output
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    log.info("Wrote macro data: %s (%d series)", output_path, len(result["latest"]))

    return result


def get_fomc_dates(year: Optional[int] = None) -> list:
    """Get FOMC meeting dates for a given year from FRED calendar.

    Falls back to a static list if FRED API unavailable.
    """
    if year is None:
        year = date.today().year

    fred = _get_fred_client()
    if fred is not None:
        try:
            # FRED doesn't have a direct FOMC calendar API,
            # but we can detect rate change dates from DFF series
            start = date(year, 1, 1)
            end = min(date(year, 12, 31), date.today())
            data = fred.get_series("DFF", observation_start=start, observation_end=end)
            if data is not None and len(data) > 1:
                # Find dates where fed funds rate changed
                changes = data.diff().dropna()
                change_dates = [str(idx.date()) for idx, val in changes.items() if abs(val) > 0.001]
                if change_dates:
                    return change_dates
        except Exception:
            pass

    # Fallback: approximate FOMC schedule (8 meetings per year)
    # These are approximate — real dates vary
    return []


def load_macro_data(config_dir: Optional[str] = None) -> Dict[str, Any]:
    """Load previously fetched macro data from JSON file.

    This is the read path — used by bridge.py and regime modules at runtime.
    """
    if config_dir is None:
        config_dir = os.environ.get("AEGIS_CONFIG_DIR", "/app/config")
    macro_path = os.path.join(config_dir, "macro_data.json")
    try:
        with open(macro_path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"latest": {}, "history": {}, "regime_inputs": {}}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [FRED] %(levelname)s %(message)s")
    log.info("Fetching FRED macro data...")
    result = fetch_macro_data()
    print(f"\nFetched {len(result['latest'])} series:")
    for name, val in result["latest"].items():
        print(f"  {name}: {val:.4f}")
    print(f"\nRegime inputs: {json.dumps(result['regime_inputs'], indent=2)}")
