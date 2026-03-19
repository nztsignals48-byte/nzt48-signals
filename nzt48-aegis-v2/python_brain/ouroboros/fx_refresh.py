"""AEGIS V2 — Live FX Rate Refresh.

Fetches current FX rates from Yahoo Finance (free, no API key) and writes
fx_rates.toml for the Rust engine to load on next tick.

Pairs fetched: EURGBP, USDGBP, CHFGBP, JPYGBP, HKDGBP, AUDGBP, SEKGBP, NOKGBP, DKKGBP.

Falls back to hardcoded rates if yfinance fails (never leaves the engine without rates).

Usage:
    python3 -m python_brain.ouroboros.fx_refresh
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [FX-Refresh] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fx_refresh")

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", _PROJECT_ROOT / "config"))

# Pairs: we need X per 1 GBP for the engine (engine stores "how many GBP per 1 unit")
# Yahoo Finance ticker format: XXXYYY=X means 1 XXX = ??? YYY
# We want: 1 USD = ? GBP, so we fetch USDGBP=X
# But Yahoo uses GBPUSD=X format (1 GBP = ? USD), so we invert.
# Actually, Yahoo has both: USDGBP=X works directly.
FX_PAIRS = {
    "EURGBP=X": "EUR",
    "USDGBP=X": "USD",
    "CHFGBP=X": "CHF",
    "JPYGBP=X": "JPY",
    "HKDGBP=X": "HKD",
    "AUDGBP=X": "AUD",
    "SGDGBP=X": "SGD",
    "NZDGBP=X": "NZD",
    "SEKGBP=X": "SEK",
    "NOKGBP=X": "NOK",
    "DKKGBP=X": "DKK",
    "PLNGBP=X": "PLN",
    "TWDGBP=X": "TWD",
    "CNYGBP=X": "CNY",
    "INRGBP=X": "INR",
    "KRWGBP=X": "KRW",
}

# Hardcoded fallback rates (approximate, updated March 2026)
FALLBACK_RATES = {
    "EUR": 0.86,
    "USD": 0.79,
    "CHF": 0.89,
    "JPY": 0.0053,
    "HKD": 0.101,
    "AUD": 0.51,
    "SGD": 0.59,
    "NZD": 0.47,
    "SEK": 0.074,
    "NOK": 0.072,
    "DKK": 0.115,
    "PLN": 0.20,
    "TWD": 0.024,
    "CNY": 0.109,
    "INR": 0.0094,
    "KRW": 0.00056,
}


def fetch_live_rates() -> dict[str, float]:
    """Fetch live FX rates from yfinance. Returns {currency_code: rate_to_gbp}."""
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed — using fallback rates")
        return {}

    rates = {}
    tickers_str = " ".join(FX_PAIRS.keys())
    try:
        data = yf.download(tickers_str, period="1d", interval="1d", progress=False)
        if data.empty:
            log.warning("yfinance returned empty data")
            return {}

        for ticker, currency in FX_PAIRS.items():
            try:
                if len(FX_PAIRS) == 1:
                    close = data["Close"].iloc[-1]
                else:
                    close = data["Close"][ticker].iloc[-1]
                if close > 0:
                    rates[currency] = round(float(close), 6)
                    log.info("  %s = %.6f GBP", currency, rates[currency])
            except (KeyError, IndexError):
                log.warning("  %s: no data, using fallback", currency)
    except Exception as e:
        log.error("yfinance download failed: %s", e)

    return rates


def write_rates_toml(rates: dict[str, float], path: Path) -> None:
    """Write fx_rates.toml in TOML format for the Rust engine."""
    lines = [
        f"# FX rates updated {time.strftime('%Y-%m-%d %H:%M UTC')}",
        f"# Source: {'yfinance (live)' if rates else 'hardcoded fallback'}",
        "[rates]",
    ]
    # Merge: live rates override fallback
    merged = {**FALLBACK_RATES, **rates}
    for currency, rate in sorted(merged.items()):
        lines.append(f"{currency}GBP = {rate}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    log.info("Wrote %d rates to %s", len(merged), path)


def main():
    log.info("Fetching live FX rates...")
    live_rates = fetch_live_rates()

    if not live_rates:
        log.warning("No live rates — writing fallback rates")
        live_rates = {}

    fetched = len(live_rates)
    total = len(FALLBACK_RATES)
    log.info("Fetched %d/%d live rates, %d from fallback", fetched, total, total - fetched)

    output_path = CONFIG_DIR / "fx_rates.toml"
    write_rates_toml(live_rates, output_path)
    log.info("FX refresh complete")


if __name__ == "__main__":
    main()
