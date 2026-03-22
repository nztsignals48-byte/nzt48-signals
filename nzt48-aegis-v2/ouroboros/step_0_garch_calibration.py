"""STANDALONE UTILITY — NOT part of nightly Ouroboros pipeline.

GARCH parameters are now computed at engine startup via:
  - Engine loads 60-day historical data from yfinance (via config_writer)
  - Rust GarchRegistry fits GARCH(1,1) at boot from garch_params.json
  - Updates incrementally per tick (no nightly recalc needed)
  - See: rust_core/src/garch_inference.rs (349 LOC)

This file is kept for manual testing/reference only.
It is NOT imported by cli.py, nightly_v6.py, or any pipeline module.

To use manually:
    python3 -m ouroboros.step_0_garch_calibration

Original purpose: Fit GARCH(1,1) to each ticker using the `arch` library.
Produces garch_params.json consumed by Rust's GarchInference (O(1) per tick).
Uses yfinance for historical returns (60-day lookback).
RM-1: Prevents Tokio reactor freeze from per-tick MLE optimization.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Dict, Optional

import numpy as np


def fit_garch_single(returns: np.ndarray) -> Optional[dict]:
    """Fit GARCH(1,1) to a return series. Returns params dict or None on failure."""
    try:
        from arch import arch_model
    except ImportError:
        print("ERROR: `arch` package not installed. pip install arch", file=sys.stderr)
        return None

    if len(returns) < 30:
        return None

    # Scale returns to percentage for numerical stability (arch convention)
    returns_pct = returns * 100.0

    try:
        model = arch_model(returns_pct, vol="Garch", p=1, q=1, mean="Zero", rescale=False)
        result = model.fit(disp="off", show_warning=False)

        omega = result.params.get("omega", 0.0)
        alpha = result.params.get("alpha[1]", 0.0)
        beta = result.params.get("beta[1]", 0.0)

        # Convert back from percentage scale: divide omega by 10000
        omega_decimal = omega / 10000.0

        # Stationarity check
        if alpha + beta >= 1.0 or omega_decimal <= 0.0:
            return None

        # Get last conditional variance (in decimal scale)
        cond_vol = result.conditional_volatility
        if len(cond_vol) == 0:
            return None
        sigma2_prev = (cond_vol.iloc[-1] / 100.0) ** 2  # Convert back to decimal

        return {
            "omega": float(omega_decimal),
            "alpha": float(alpha),
            "beta": float(beta),
            "sigma2_prev": float(sigma2_prev),
        }
    except Exception:
        return None


def calibrate_universe(
    ticker_ids: Dict[str, int],
    lookback_days: int = 60,
    output_path: str = "data/garch_params.json",
) -> dict:
    """Fit GARCH(1,1) for all tickers. Save to JSON keyed by ticker_id (int).

    Args:
        ticker_ids: mapping of yfinance symbol → TickerId integer
        lookback_days: number of trading days for fitting
        output_path: where to write garch_params.json
    """
    import yfinance as yf

    results = {}
    symbols = list(ticker_ids.keys())
    total = len(symbols)

    print(f"GARCH calibration: {total} tickers, {lookback_days}-day lookback")

    for i, symbol in enumerate(symbols):
        ticker_id = ticker_ids[symbol]

        try:
            data = yf.download(
                symbol,
                period=f"{lookback_days + 10}d",  # extra days for market closures
                progress=False,
                auto_adjust=True,
            )

            if data.empty or len(data) < 30:
                print(f"  [{i+1}/{total}] {symbol}: insufficient data ({len(data)} rows)")
                continue

            # Handle MultiIndex columns from yfinance
            close_col = data["Close"]
            if hasattr(close_col, "columns"):
                close_col = close_col.iloc[:, 0]

            closes = close_col.dropna().values.astype(float)
            returns = np.diff(np.log(closes))  # Log returns

            params = fit_garch_single(returns)
            if params is not None:
                results[str(ticker_id)] = params
                print(
                    f"  [{i+1}/{total}] {symbol} (id={ticker_id}): "
                    f"ω={params['omega']:.2e} α={params['alpha']:.4f} "
                    f"β={params['beta']:.4f} σ²={params['sigma2_prev']:.6f}"
                )
            else:
                print(f"  [{i+1}/{total}] {symbol}: fit failed (non-stationary or insufficient)")

        except Exception as e:
            print(f"  [{i+1}/{total}] {symbol}: error — {str(e)[:80]}")

        # Brief pause to avoid yfinance rate limits
        if (i + 1) % 5 == 0:
            time.sleep(1)

    # Write output
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nGARCH calibration complete: {len(results)}/{total} tickers fitted")
    print(f"Output: {output_path}")

    return results


def main():
    """CLI entry point for standalone calibration."""
    # Load full universe from contracts.toml (dynamic — no hardcoded tickers)
    try:
        from python_brain.ouroboros.contract_loader import load_yfinance_symbols
        all_symbols = load_yfinance_symbols()
    except ImportError:
        # Fallback: try loading directly
        import tomllib
        contracts_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "contracts.toml"
        )
        with open(contracts_path, "rb") as f:
            data = tomllib.load(f)
        all_symbols = [c["symbol"] for c in data.get("contracts", []) if c.get("symbol")]

    # Build ticker_id map: {symbol: index}
    universe = {sym: i for i, sym in enumerate(all_symbols)}
    print(f"GARCH calibration: {len(universe)} tickers from contracts.toml")

    output_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data",
        "garch_params.json",
    )

    calibrate_universe(universe, lookback_days=60, output_path=output_path)


if __name__ == "__main__":
    main()
