"""
data_hub/normalization/price_units.py
=======================================
Pence/pounds detection and normalization for LSE tickers.
"""
from __future__ import annotations

# LSE ETPs typically trade in pence (GBX) — prices > 200 are usually pence
_PENCE_THRESHOLD = 200.0
_MAX_PLAUSIBLE_PENCE = 50_000.0   # >500 GBP in pence


def detect_pence(close: float, ticker: str = "") -> bool:
    """
    Heuristic: if close > threshold and ticker ends in .L, likely pence-coded.
    Returns True if price appears to be in pence (GBX) not pounds (GBP).
    """
    if not ticker.endswith(".L"):
        return False
    return _PENCE_THRESHOLD < close < _MAX_PLAUSIBLE_PENCE


def normalize_to_pounds(close: float, ticker: str = "") -> tuple[float, bool]:
    """
    Returns (normalized_close, was_converted).
    If pence detected: divides by 100.
    """
    if detect_pence(close, ticker):
        return round(close / 100.0, 6), True
    return close, False


def scale_bars(df, ticker: str):
    """
    Apply pence normalization to an OHLCV DataFrame.
    Returns (normalized_df, was_scaled).
    """
    if df is None or df.empty:
        return df, False
    last_close = float(df["close"].iloc[-1]) if "close" in df.columns else 0.0
    if detect_pence(last_close, ticker):
        scaled = df.copy()
        for col in ["open", "high", "low", "close"]:
            if col in scaled.columns:
                scaled[col] = scaled[col] / 100.0
        return scaled, True
    return df, False
