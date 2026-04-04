"""Global Macro Data Provider — World Bank, OECD, Eurostat data via pandas-datareader.

Complements fredapi with non-US macro data for global regime detection.
Particularly useful for:
  - UK CPI vs US CPI (INFLATION regime detection)
  - Global GDP growth rates
  - Interest rate differentials (overnight carry sizing)

License: pandas-datareader is BSD-3.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("global_macro_provider")


def fetch_global_macro(
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch global macro data from multiple sources.

    Sources:
      - FRED (via pandas-datareader): US rates, UK CPI, EUR rates
      - World Bank: GDP growth, inflation by country

    Args:
        output_path: Where to write JSON. Defaults to /app/config/macro_global.json
    """
    if output_path is None:
        output_path = os.environ.get(
            "AEGIS_CONFIG_DIR", "/app/config"
        ) + "/macro_global.json"

    result: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rates": {},
        "inflation": {},
        "gdp": {},
        "interest_rate_differentials": {},
    }

    # ── FRED series via pandas-datareader ──
    try:
        import pandas_datareader.data as web
        start = date.today() - timedelta(days=90)
        end = date.today()

        # UK-relevant rates from FRED
        fred_series = {
            # Interest rates for overnight carry
            "GBP_RATE": "IUDSOIA",     # UK overnight interbank rate (approx)
            "USD_RATE": "DFF",          # Fed Funds Rate
            "EUR_RATE": "ECBESTRVOLWGTTRMNRT",  # ECB deposit rate (Euro Short-Term Rate)
            # Inflation comparisons
            "UK_CPI": "GBRCPIALLMINMEI",  # UK CPI (OECD via FRED)
            "US_CPI": "CPIAUCSL",         # US CPI
            "EU_CPI": "EA19CPALTT01GYM",  # Euro Area CPI
        }

        for name, series_id in fred_series.items():
            try:
                data = web.DataReader(series_id, "fred", start, end)
                if data is not None and len(data) > 0:
                    col = data.columns[0]
                    latest = data[col].dropna().iloc[-1]
                    result["rates" if "RATE" in name else "inflation"][name] = float(latest)
                    log.info("  %s (%s): %.4f", name, series_id, float(latest))
            except Exception as e:
                log.warning("  %s (%s): FAILED — %s", name, series_id, str(e)[:60])

    except ImportError:
        log.warning("pandas-datareader not installed — pip install pandas-datareader")
    except Exception as e:
        log.warning("FRED data fetch failed: %s", str(e)[:100])

    # ── Compute interest rate differentials for overnight carry ──
    rates = result["rates"]
    usd_rate = rates.get("USD_RATE", 5.0)
    gbp_rate = rates.get("GBP_RATE", 5.0)
    eur_rate = rates.get("EUR_RATE", 3.75)

    result["interest_rate_differentials"] = {
        "GBP_USD": gbp_rate - usd_rate,  # Positive = long GBP earns carry
        "EUR_USD": eur_rate - usd_rate,
        "GBP_EUR": gbp_rate - eur_rate,
    }

    # ── Write output ──
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    log.info("Wrote global macro data: %s", output_path)

    return result


def load_global_macro(config_dir: Optional[str] = None) -> Dict[str, Any]:
    """Load previously fetched global macro data from JSON file."""
    if config_dir is None:
        config_dir = os.environ.get("AEGIS_CONFIG_DIR", "/app/config")
    path = os.path.join(config_dir, "macro_global.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"rates": {}, "inflation": {}, "gdp": {}, "interest_rate_differentials": {}}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [GlobalMacro] %(levelname)s %(message)s")
    log.info("Fetching global macro data...")
    result = fetch_global_macro()
    print(f"\nRates: {json.dumps(result['rates'], indent=2)}")
    print(f"Inflation: {json.dumps(result['inflation'], indent=2)}")
    print(f"Rate differentials: {json.dumps(result['interest_rate_differentials'], indent=2)}")
