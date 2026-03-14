"""
signal_engine/intel_card.py
============================
Intel Card model — context/watch items for tickers NOT in the core ISA universe.
Written to artifacts/YYYY-MM-DD/{session}/intel.json.

Intel cards provide market context from an extended universe of instruments.
They are explicitly labelled INTEL-ONLY / NOT-CORE to prevent confusion with
actionable TRADE signals.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd


ARTIFACTS_ROOT = Path(__file__).parent.parent / "artifacts"


@dataclass
class IntelCard:
    """One intel item — contextual insight for monitoring, NOT actionable trade."""
    ticker:         str
    label:          str  = "INTEL-ONLY"       # INTEL-ONLY | NOT-CORE | WATCH-INTEL
    category:       str  = "CONTEXT"          # CONTEXT | SECTOR | REGIME | MOMENTUM | VOLATILITY
    is_core:        bool = False              # Always False for intel items

    # Price context
    price:          float = 0.0
    move_pct:       float = 0.0               # close-to-close % change
    range_pct:      float = 0.0               # intraday range %

    # Indicators
    rsi:            float = 0.0
    atr_pct:        float = 0.0
    rvol:           Optional[float] = None
    adx:            float = 0.0
    trend:          str   = "NEUTRAL"         # BULLISH | BEARISH | NEUTRAL

    # Regime context
    vol_regime:     str   = ""                # EXPANSION | COMPRESSION | BLOW_OFF | EXHAUSTION
    momentum_rank:  float = 0.0               # 0-100 momentum score

    # Insight text
    insight:        str   = ""                # Human-readable 1-liner
    reasons:        list[str] = field(default_factory=list)

    # Metadata
    factor_group:   str   = ""
    generated_at:   str   = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "IntelCard":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


def build_intel_cards(
    tickers: list[str],
    period: str = "5d",
) -> list[IntelCard]:
    """Build intel cards for a list of non-core tickers.

    Fetches OHLCV via yfinance and computes basic indicators.
    Returns list of IntelCard objects sorted by momentum_rank descending.
    """
    from data_hub.hub import DataHub
    import logging

    logger = logging.getLogger("nzt48.intel_card")
    cards: list[IntelCard] = []

    for ticker in tickers:
        try:
            _hub = DataHub()
            bar_result = _hub.get_bars(ticker, period=period, interval="1h")
            if bar_result.df is None or bar_result.df.empty:
                cards.append(IntelCard(
                    ticker=ticker,
                    insight=f"{ticker}: no data available",
                    reasons=["DataHub returned empty"],
                ))
                continue

            raw = bar_result.df
            # DataHub returns lowercase columns; convert to Title case
            raw.columns = [c.title() for c in raw.columns]

            df = raw
            n = len(df)
            if n < 3:
                cards.append(IntelCard(
                    ticker=ticker,
                    insight=f"{ticker}: insufficient bars ({n})",
                    reasons=[f"only {n} bars available"],
                ))
                continue

            close = float(df["Close"].iloc[-1])
            close_prev = float(df["Close"].iloc[-2])
            high_s = df["High"].astype(float)
            low_s = df["Low"].astype(float)
            close_s = df["Close"].astype(float)

            # Move %
            move_pct = round((close - close_prev) / close_prev * 100, 3) if close_prev > 0 else 0.0

            # Range %
            day_high = float(high_s.iloc[-1])
            day_low = float(low_s.iloc[-1])
            range_pct = round((day_high - day_low) / close_prev * 100, 3) if close_prev > 0 else 0.0

            # ATR
            ind_window = min(n, 14)
            tr = pd.concat([
                high_s - low_s,
                (high_s - close_s.shift(1)).abs(),
                (low_s - close_s.shift(1)).abs(),
            ], axis=1).max(axis=1)
            atr = float(tr.rolling(ind_window).mean().iloc[-1]) if n >= ind_window else float(tr.mean())
            atr_pct = round(atr / close * 100, 3) if close > 0 else 0.0

            # RSI (Wilder's smoothing)
            delta = close_s.diff()
            gain = delta.clip(lower=0).ewm(alpha=1/ind_window, adjust=False).mean()
            loss = (-delta.clip(upper=0)).ewm(alpha=1/ind_window, adjust=False).mean()
            rs = gain / loss.replace(0, 1e-9)
            rsi = float(100 - 100 / (1 + rs.iloc[-1]))

            # RVOL
            vol_s = df["Volume"].astype(float)
            rvol: Optional[float] = None
            if vol_s.sum() > 0 and len(vol_s) >= 5:
                avg_vol = float(vol_s.iloc[:-1].mean())
                last_vol = float(vol_s.iloc[-1])
                rvol = round(last_vol / avg_vol, 2) if avg_vol > 0 else None

            # ADX (simplified)
            adx = 0.0
            try:
                plus_dm = high_s.diff().clip(lower=0)
                minus_dm = (-low_s.diff()).clip(lower=0)
                atr_s = tr.rolling(ind_window).mean()
                plus_di = 100 * plus_dm.rolling(ind_window).mean() / atr_s.replace(0, 1e-9)
                minus_di = 100 * minus_dm.rolling(ind_window).mean() / atr_s.replace(0, 1e-9)
                dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
                adx = float(dx.rolling(ind_window).mean().iloc[-1])
            except Exception:
                pass

            # Trend
            ema9 = float(close_s.ewm(span=9, adjust=False).mean().iloc[-1])
            ema20 = float(close_s.ewm(span=20, adjust=False).mean().iloc[-1])
            if close > ema9 > ema20:
                trend = "BULLISH"
            elif close < ema9 < ema20:
                trend = "BEARISH"
            else:
                trend = "NEUTRAL"

            # Vol regime
            if n >= 21:
                atr5 = float(tr.rolling(5).mean().iloc[-1])
                atr21 = float(tr.rolling(21).mean().iloc[-1])
                ratio = atr5 / atr21 if atr21 > 0 else 1.0
                if ratio > 1.5:
                    vol_regime = "BLOW_OFF"
                elif ratio > 1.1:
                    vol_regime = "EXPANSION"
                elif ratio < 0.7:
                    vol_regime = "COMPRESSION"
                else:
                    vol_regime = "NORMAL"
            else:
                vol_regime = "UNKNOWN"

            # Momentum rank (simple: RSI weight + trend weight + move weight)
            mom_rank = round(
                0.4 * min(rsi / 100, 1.0) * 100
                + 0.3 * (80 if trend == "BULLISH" else (20 if trend == "BEARISH" else 50))
                + 0.3 * min(max(move_pct + 5, 0) / 10, 1.0) * 100,
                1,
            )

            # Build insight
            parts = []
            if abs(move_pct) > 2:
                parts.append(f"{'UP' if move_pct > 0 else 'DOWN'} {abs(move_pct):.1f}%")
            if trend != "NEUTRAL":
                parts.append(trend)
            if vol_regime in ("EXPANSION", "BLOW_OFF"):
                parts.append(f"VOL:{vol_regime}")
            if rvol and rvol > 2.0:
                parts.append(f"HIGH RVOL ({rvol:.1f}x)")
            insight = f"{ticker}: " + ", ".join(parts) if parts else f"{ticker}: quiet"

            cards.append(IntelCard(
                ticker=ticker,
                label="INTEL-ONLY",
                category="CONTEXT",
                is_core=False,
                price=round(close, 4),
                move_pct=move_pct,
                range_pct=range_pct,
                rsi=round(rsi, 2),
                atr_pct=atr_pct,
                rvol=rvol,
                adx=round(adx, 2),
                trend=trend,
                vol_regime=vol_regime,
                momentum_rank=mom_rank,
                insight=insight,
                reasons=parts or ["quiet session"],
            ))

        except Exception as exc:
            logger.debug("intel_card build failed for %s: %s", ticker, exc)
            cards.append(IntelCard(
                ticker=ticker,
                insight=f"{ticker}: build failed ({exc})",
                reasons=[str(exc)[:80]],
            ))

    # Sort by momentum rank descending
    cards.sort(key=lambda c: c.momentum_rank, reverse=True)
    return cards


def write_intel_artifact(
    cards: list[IntelCard],
    session: str,
    run_date: Optional[date] = None,
) -> Path:
    """Write intel.json to artifacts/YYYY-MM-DD/{session}/intel.json (atomic)."""
    import tempfile

    today = run_date or date.today()
    session_key = session.lower().replace(" ", "_")
    out_dir = ARTIFACTS_ROOT / str(today) / session_key
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session": session,
        "count": len(cards),
        "intel_cards": [c.to_dict() for c in cards],
    }

    out_path = out_dir / "intel.json"
    tmp_fd, tmp_name = tempfile.mkstemp(dir=out_dir, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            f.write(json.dumps(payload, indent=2, default=str))
            f.flush()
            os.fsync(f.fileno())
        Path(tmp_name).replace(out_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except Exception:
            pass
        raise
    return out_path
