"""
K-11: SFV (Synthetic Fair Value) Arbitrage Engine.
K-16: GBP/USD Flash Scrub -- disable SFV if cable moves >0.25% in 60s.

Compute:
    SFV = NQ_futures * leverage * (GBP/USD) - swap_accrual

The SFV gives the theoretical fair value of an LSE leveraged ETP based on
the underlying US futures price, the product's leverage factor, the spot
GBP/USD exchange rate, and any accumulated swap/financing cost.

When the ETP's market ask diverges from SFV by more than a configurable
number of ticks, an arbitrage signal is emitted.

Flash Scrub (K-16):
    If GBP/USD moves >0.25% within a rolling 60-second window, all SFV
    signals are disabled until the FX rate stabilizes.
"""
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


# ── Data Structures ─────────────────────────────────────────────────────────

@dataclass
class SFVSignal:
    """Output of an SFV computation or arbitrage check."""
    ticker: str
    sfv: float
    market_price: float
    divergence_bps: float
    timestamp: datetime
    actionable: bool


@dataclass
class FXSnapshot:
    """Internal record of a GBP/USD observation."""
    rate: float
    timestamp: datetime


# ── Configuration ───────────────────────────────────────────────────────────

@dataclass
class SFVConfig:
    """Tuneable parameters for the SFV engine."""
    flash_scrub_threshold_pct: float = 0.25     # K-16: 0.25% move triggers scrub
    flash_scrub_window_secs: float = 60.0       # K-16: rolling window
    min_divergence_ticks: int = 2               # minimum ticks for actionable signal
    fx_history_maxlen: int = 120                # keep ~2 min of FX ticks


# ── Engine ──────────────────────────────────────────────────────────────────

class SFVEngine:
    """
    Synthetic Fair Value engine for LSE leveraged ETPs.

    Phase Q2 implementation target. Provides:
      - Fair value computation from futures + FX + leverage
      - Arbitrage divergence detection
      - GBP/USD flash-scrub safety gate (K-16)
    """

    def __init__(self, config: Optional[SFVConfig] = None) -> None:
        self._config = config or SFVConfig()
        self._fx_history: deque[FXSnapshot] = deque(
            maxlen=self._config.fx_history_maxlen
        )
        self._flash_scrub_active: bool = False
        self._flash_scrub_since: Optional[datetime] = None
        self._last_gbp_usd: Optional[float] = None

    # ── FX feed ─────────────────────────────────────────────────────────

    def update_fx(self, gbp_usd: float, timestamp: datetime) -> None:
        """
        Ingest a GBP/USD tick.

        Activates flash scrub if the rate has moved >0.25% relative to any
        observation within the trailing 60-second window.
        """
        self._fx_history.append(FXSnapshot(rate=gbp_usd, timestamp=timestamp))

        # Prune observations older than the window
        cutoff = timestamp - timedelta(seconds=self._config.flash_scrub_window_secs)

        # Check flash-scrub condition against the oldest observation in window
        oldest_in_window: Optional[FXSnapshot] = None
        for snap in self._fx_history:
            if snap.timestamp >= cutoff:
                oldest_in_window = snap
                break

        if oldest_in_window is not None:
            pct_move = abs(gbp_usd / oldest_in_window.rate - 1) * 100
            if pct_move > self._config.flash_scrub_threshold_pct:
                if not self._flash_scrub_active:
                    logger.warning(
                        "SFV FLASH SCRUB ACTIVATED: GBP/USD moved %.3f%% in %.0fs "
                        "(%.5f -> %.5f)",
                        pct_move,
                        self._config.flash_scrub_window_secs,
                        oldest_in_window.rate,
                        gbp_usd,
                    )
                self._flash_scrub_active = True
                self._flash_scrub_since = timestamp
            else:
                if self._flash_scrub_active:
                    logger.info(
                        "SFV FLASH SCRUB cleared: GBP/USD stable (%.3f%% < %.3f%%)",
                        pct_move,
                        self._config.flash_scrub_threshold_pct,
                    )
                self._flash_scrub_active = False
                self._flash_scrub_since = None

        self._last_gbp_usd = gbp_usd

    @property
    def flash_scrub_active(self) -> bool:
        return self._flash_scrub_active

    @property
    def last_gbp_usd(self) -> Optional[float]:
        return self._last_gbp_usd

    # ── SFV computation ─────────────────────────────────────────────────

    def compute_sfv(
        self,
        ticker: str,
        futures_price: float,
        leverage: float,
        gbp_usd: float,
        swap_accrual: float = 0.0,
    ) -> SFVSignal:
        """
        Compute synthetic fair value.

        SFV = futures_price * leverage * gbp_usd - swap_accrual

        The signal is marked non-actionable if flash scrub is active.
        """
        sfv = futures_price * leverage * gbp_usd - swap_accrual
        return SFVSignal(
            ticker=ticker,
            sfv=sfv,
            market_price=0.0,
            divergence_bps=0.0,
            timestamp=datetime.utcnow(),
            actionable=not self._flash_scrub_active,
        )

    def check_arbitrage(
        self,
        ticker: str,
        sfv: float,
        market_ask: float,
        min_divergence_ticks: Optional[int] = None,
    ) -> Optional[SFVSignal]:
        """
        Check whether the market ask diverges enough from SFV to be actionable.

        Returns None if flash scrub is active (no trading during FX instability).
        Returns an SFVSignal with ``actionable=True`` if divergence >= threshold.
        """
        if self._flash_scrub_active:
            return None

        if sfv == 0:
            logger.warning("SFV is zero for %s — cannot compute divergence", ticker)
            return None

        threshold = min_divergence_ticks or self._config.min_divergence_ticks
        divergence_bps = (market_ask - sfv) / sfv * 10_000
        actionable = abs(divergence_bps) >= threshold

        return SFVSignal(
            ticker=ticker,
            sfv=sfv,
            market_price=market_ask,
            divergence_bps=divergence_bps,
            timestamp=datetime.utcnow(),
            actionable=actionable,
        )

    # ── introspection ───────────────────────────────────────────────────

    def summary(self) -> dict:
        """JSON-serializable snapshot for telemetry."""
        return {
            "flash_scrub_active": self._flash_scrub_active,
            "flash_scrub_since": (
                self._flash_scrub_since.isoformat()
                if self._flash_scrub_since
                else None
            ),
            "last_gbp_usd": self._last_gbp_usd,
            "fx_history_len": len(self._fx_history),
        }
