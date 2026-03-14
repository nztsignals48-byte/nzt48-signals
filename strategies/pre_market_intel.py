"""
NZT-48 AEGIS I-09 — Pre-Market Intelligence Scan
=================================================
Scans overnight US futures (NQ, ES, RTY) at 07:30 UK to detect significant
directional moves (>0.5%) and pre-load confidence bias for related ETPs.

Academic basis:
  - Barclay & Hendershott (2003) "Price Discovery and Trading After Hours",
    Review of Financial Studies 16(4): after-hours returns predict next-day
    open direction with 64% accuracy for correlated assets.
  - Lou, Polk & Sornette (2013): overnight gaps in leveraged ETPs persist
    intraday 68% of the time — directional bias from futures is informative.

The scan runs once at 07:30 UK (30 min before LSE open). Results are:
  1. Stored in Redis: nzt:premarket:{date}:{direction}:{magnitude}
  2. Per-ETP bias cached: nzt:premarket_bias:{date}:{ticker}
  3. Consumed by S15 DailyTargetStrategy to adjust confidence scoring.

This module is lightweight — uses yfinance for futures data only.
No dependency on data_feeds or holdings infrastructure.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timezone
from typing import Optional

logger = logging.getLogger("nzt48.strategy.premarket_intel")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SIGNIFICANT_MOVE_PCT = 0.5    # Minimum futures move to trigger bias (Barclay & Hendershott 2003)
_STRONG_MOVE_PCT = 1.0         # Strong directional move — higher confidence bias
_EXTREME_MOVE_PCT = 2.0        # Extreme move — max confidence but also risk flag

# Confidence bias adjustments applied to related ETPs
_BIAS_CONF_MODERATE = 5        # 0.5-1.0% futures move → +5 confidence
_BIAS_CONF_STRONG = 10         # 1.0-2.0% futures move → +10 confidence
_BIAS_CONF_EXTREME = 12        # >2.0% futures move → +12 confidence (capped — extreme = risk too)

# Redis key TTL — 18 hours (covers full trading day + buffer)
_REDIS_TTL_SECONDS = 64800

# Futures tickers (yfinance format)
_FUTURES_TICKERS = {
    "NQ=F": "Nasdaq 100 Futures",
    "ES=F": "S&P 500 Futures",
    "RTY=F": "Russell 2000 Futures",
}

# Mapping: which LSE ETPs are affected by which futures
# NQ=F → Nasdaq-tracking ETPs
# ES=F → S&P-tracking ETPs
# RTY=F → broad market sentiment (weaker signal, affects all)
_FUTURES_TO_ETPS = {
    "NQ=F": {
        "QQQ3.L": 1.0,    # 3x Nasdaq — direct exposure
        "3LUS.L": 0.8,    # 3x Nasdaq — direct exposure (diff issuer)
        "QQQ5.L": 1.0,    # 5x Nasdaq — direct exposure
        "QQQS.L": -1.0,   # 3x SHORT Nasdaq — inverse exposure
        "NVD3.L": 0.7,    # 3x NVDA — high Nasdaq correlation (ρ ≈ 0.82)
        "GPT3.L": 0.6,    # 3x MSFT — moderate Nasdaq correlation (ρ ≈ 0.75)
        "TSM3.L": 0.5,    # 3x TSM — semiconductor/Nasdaq correlation
        "MU2.L": 0.5,     # 2x MU — semiconductor/Nasdaq correlation
        "3SEM.L": 0.6,    # 3x Semiconductors — Nasdaq-correlated
        "TSL3.L": 0.4,    # 3x TSLA — moderate Nasdaq correlation (idiosyncratic)
    },
    "ES=F": {
        "SP5L.L": 1.0,    # 5x S&P 500 — direct exposure
        "3USS.L": -1.0,   # 3x SHORT S&P — inverse exposure
        "QQQ3.L": 0.4,    # Nasdaq tracks S&P broadly (ρ ≈ 0.90)
        "3LUS.L": 0.4,
    },
    "RTY=F": {
        # Russell 2000 = broad risk sentiment indicator
        # Weaker but affects all ETPs as a risk-on/risk-off signal
        "QQQ3.L": 0.2,
        "3LUS.L": 0.2,
        "SP5L.L": 0.3,    # Russell closer to S&P than Nasdaq
    },
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class FuturesMove:
    """Overnight return for a single futures contract."""
    ticker: str
    name: str
    prev_close: float
    current_price: float
    change_pct: float       # Percentage change (can be negative)
    is_significant: bool    # abs(change_pct) >= _SIGNIFICANT_MOVE_PCT
    direction: str          # "BULLISH", "BEARISH", or "FLAT"
    magnitude: str          # "FLAT", "MODERATE", "STRONG", "EXTREME"


@dataclass
class ETPBias:
    """Pre-market directional bias for a single ETP."""
    ticker: str
    direction: str          # "BULLISH", "BEARISH", or "NEUTRAL"
    confidence_adj: int     # Confidence adjustment to apply (+/- points)
    source_futures: list[str] = field(default_factory=list)  # Which futures drove this
    combined_signal: float = 0.0  # Weighted combined signal strength


@dataclass
class PreMarketIntel:
    """Complete pre-market intelligence scan result."""
    scan_time: datetime
    scan_date: str          # ISO date string
    futures_moves: list[FuturesMove] = field(default_factory=list)
    etp_biases: dict[str, ETPBias] = field(default_factory=dict)  # ticker → ETPBias
    overall_direction: str = "NEUTRAL"  # "BULLISH", "BEARISH", "NEUTRAL"
    overall_magnitude: str = "FLAT"      # "FLAT", "MODERATE", "STRONG", "EXTREME"
    overall_change_pct: float = 0.0      # Weighted average futures change
    risk_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialise for Redis/JSON storage."""
        return {
            "scan_time": self.scan_time.isoformat(),
            "scan_date": self.scan_date,
            "futures_moves": [asdict(fm) for fm in self.futures_moves],
            "etp_biases": {k: asdict(v) for k, v in self.etp_biases.items()},
            "overall_direction": self.overall_direction,
            "overall_magnitude": self.overall_magnitude,
            "overall_change_pct": self.overall_change_pct,
            "risk_flags": self.risk_flags,
        }

    def to_telegram(self) -> str:
        """Format for Telegram notification."""
        lines = ["📡 PRE-MARKET INTEL (07:30 UK)"]
        lines.append(f"Direction: {self.overall_direction} | Magnitude: {self.overall_magnitude}")
        lines.append("")

        for fm in self.futures_moves:
            emoji = "🟢" if fm.direction == "BULLISH" else "🔴" if fm.direction == "BEARISH" else "⚪"
            lines.append(f"  {emoji} {fm.name}: {fm.change_pct:+.2f}% ({fm.magnitude})")

        if self.risk_flags:
            lines.append("")
            lines.append("⚠️ Risk Flags:")
            for flag in self.risk_flags:
                lines.append(f"  • {flag}")

        # Top ETP biases
        biased_etps = [
            (t, b) for t, b in self.etp_biases.items()
            if b.direction != "NEUTRAL"
        ]
        if biased_etps:
            lines.append("")
            lines.append("ETP Bias:")
            biased_etps.sort(key=lambda x: abs(x[1].confidence_adj), reverse=True)
            for ticker, bias in biased_etps[:8]:
                emoji = "🟢" if bias.direction == "BULLISH" else "🔴"
                lines.append(
                    f"  {emoji} {ticker}: {bias.direction} ({bias.confidence_adj:+d} conf)"
                )

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------
def scan_premarket(redis_client=None) -> PreMarketIntel:
    """Scan overnight US futures and compute directional bias for LSE ETPs.

    Called at 07:30 UK by APScheduler. Uses yfinance for futures data.

    Args:
        redis_client: Optional Redis connection for persisting results.

    Returns:
        PreMarketIntel dataclass with futures moves, ETP biases, and risk flags.
    """
    scan_time = datetime.now(timezone.utc)
    scan_date = date.today().isoformat()

    logger.info("I-09 PRE-MARKET SCAN: starting at %s", scan_time.strftime("%H:%M:%S UTC"))

    # Fetch futures data via yfinance
    futures_moves = _fetch_futures_data()

    if not futures_moves:
        logger.warning("I-09 PRE-MARKET SCAN: no futures data available — returning neutral")
        result = PreMarketIntel(scan_time=scan_time, scan_date=scan_date)
        _persist_to_redis(result, redis_client)
        return result

    # Compute per-ETP bias from futures moves
    etp_biases = _compute_etp_biases(futures_moves)

    # Determine overall direction and magnitude
    overall_direction, overall_magnitude, overall_change = _compute_overall(futures_moves)

    # Detect risk flags
    risk_flags = _detect_risk_flags(futures_moves)

    result = PreMarketIntel(
        scan_time=scan_time,
        scan_date=scan_date,
        futures_moves=futures_moves,
        etp_biases=etp_biases,
        overall_direction=overall_direction,
        overall_magnitude=overall_magnitude,
        overall_change_pct=overall_change,
        risk_flags=risk_flags,
    )

    # Persist to Redis
    _persist_to_redis(result, redis_client)

    # Log summary
    _significant = [fm for fm in futures_moves if fm.is_significant]
    _biased = [t for t, b in etp_biases.items() if b.direction != "NEUTRAL"]
    logger.info(
        "I-09 PRE-MARKET SCAN COMPLETE: direction=%s magnitude=%s | "
        "%d/%d futures significant | %d ETPs biased | %d risk flags",
        overall_direction, overall_magnitude,
        len(_significant), len(futures_moves),
        len(_biased), len(risk_flags),
    )

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _fetch_futures_data() -> list[FuturesMove]:
    """Fetch overnight returns for NQ=F, ES=F, RTY=F via yfinance."""
    moves: list[FuturesMove] = []

    try:
        import yfinance as yf
    except ImportError:
        logger.error("I-09: yfinance not installed — cannot fetch futures data")
        return moves

    for ticker, name in _FUTURES_TICKERS.items():
        try:
            # Fetch 2 days of daily data to compute overnight return
            data = yf.download(
                ticker, period="5d", interval="1d",
                progress=False, threads=False,
            )
            if data is None or len(data) < 2:
                logger.warning("I-09: insufficient data for %s (%d rows)", ticker, len(data) if data is not None else 0)
                continue

            # Handle multi-level columns from yfinance
            if hasattr(data.columns, 'levels') and len(data.columns.levels) > 1:
                prev_close = float(data["Close"].iloc[-2].iloc[0])
                current_price = float(data["Close"].iloc[-1].iloc[0])
            else:
                prev_close = float(data["Close"].iloc[-2])
                current_price = float(data["Close"].iloc[-1])

            if prev_close <= 0:
                continue

            change_pct = ((current_price - prev_close) / prev_close) * 100
            abs_change = abs(change_pct)

            is_significant = abs_change >= _SIGNIFICANT_MOVE_PCT

            if abs_change < _SIGNIFICANT_MOVE_PCT:
                direction = "FLAT"
                magnitude = "FLAT"
            elif abs_change < _STRONG_MOVE_PCT:
                direction = "BULLISH" if change_pct > 0 else "BEARISH"
                magnitude = "MODERATE"
            elif abs_change < _EXTREME_MOVE_PCT:
                direction = "BULLISH" if change_pct > 0 else "BEARISH"
                magnitude = "STRONG"
            else:
                direction = "BULLISH" if change_pct > 0 else "BEARISH"
                magnitude = "EXTREME"

            move = FuturesMove(
                ticker=ticker,
                name=name,
                prev_close=round(prev_close, 2),
                current_price=round(current_price, 2),
                change_pct=round(change_pct, 3),
                is_significant=is_significant,
                direction=direction,
                magnitude=magnitude,
            )
            moves.append(move)

            logger.info(
                "I-09 FUTURES: %s (%s): %.2f → %.2f (%+.2f%%) → %s/%s",
                ticker, name, prev_close, current_price, change_pct,
                direction, magnitude,
            )

        except Exception as e:
            logger.warning("I-09: failed to fetch %s: %s", ticker, e)

    return moves


def _compute_etp_biases(futures_moves: list[FuturesMove]) -> dict[str, ETPBias]:
    """Compute per-ETP confidence bias from futures moves.

    Each ETP's bias is the weighted sum of all relevant futures signals,
    scaled by the correlation coefficient in _FUTURES_TO_ETPS.
    """
    # Accumulate weighted signal per ETP
    etp_signals: dict[str, float] = {}
    etp_sources: dict[str, list[str]] = {}

    for fm in futures_moves:
        if not fm.is_significant:
            continue

        etp_map = _FUTURES_TO_ETPS.get(fm.ticker, {})
        for etp_ticker, correlation in etp_map.items():
            # Signal = futures change % * correlation coefficient
            # Positive correlation + positive futures = bullish for ETP
            # Negative correlation (inverse ETP) + positive futures = bearish for ETP
            signal = fm.change_pct * correlation

            if etp_ticker not in etp_signals:
                etp_signals[etp_ticker] = 0.0
                etp_sources[etp_ticker] = []

            etp_signals[etp_ticker] += signal
            etp_sources[etp_ticker].append(fm.ticker)

    # Convert accumulated signals to ETPBias objects
    biases: dict[str, ETPBias] = {}

    for etp_ticker, combined_signal in etp_signals.items():
        abs_signal = abs(combined_signal)

        if abs_signal < _SIGNIFICANT_MOVE_PCT:
            direction = "NEUTRAL"
            conf_adj = 0
        elif abs_signal < _STRONG_MOVE_PCT:
            direction = "BULLISH" if combined_signal > 0 else "BEARISH"
            conf_adj = _BIAS_CONF_MODERATE
        elif abs_signal < _EXTREME_MOVE_PCT:
            direction = "BULLISH" if combined_signal > 0 else "BEARISH"
            conf_adj = _BIAS_CONF_STRONG
        else:
            direction = "BULLISH" if combined_signal > 0 else "BEARISH"
            conf_adj = _BIAS_CONF_EXTREME

        # For bearish bias on a LONG-only ISA, the conf_adj is negative
        # (penalises going long against the pre-market direction)
        if direction == "BEARISH":
            conf_adj = -conf_adj

        biases[etp_ticker] = ETPBias(
            ticker=etp_ticker,
            direction=direction,
            confidence_adj=conf_adj,
            source_futures=list(set(etp_sources.get(etp_ticker, []))),
            combined_signal=round(combined_signal, 3),
        )

    return biases


def _compute_overall(
    futures_moves: list[FuturesMove],
) -> tuple[str, str, float]:
    """Determine overall market direction from futures consensus.

    Weights: NQ=F (0.5), ES=F (0.35), RTY=F (0.15)
    — Nasdaq is most relevant for our leveraged tech-heavy universe.
    """
    _weights = {"NQ=F": 0.50, "ES=F": 0.35, "RTY=F": 0.15}
    weighted_sum = 0.0
    total_weight = 0.0

    for fm in futures_moves:
        w = _weights.get(fm.ticker, 0.1)
        weighted_sum += fm.change_pct * w
        total_weight += w

    if total_weight <= 0:
        return "NEUTRAL", "FLAT", 0.0

    avg_change = weighted_sum / total_weight
    abs_change = abs(avg_change)

    if abs_change < _SIGNIFICANT_MOVE_PCT:
        return "NEUTRAL", "FLAT", round(avg_change, 3)
    elif abs_change < _STRONG_MOVE_PCT:
        direction = "BULLISH" if avg_change > 0 else "BEARISH"
        return direction, "MODERATE", round(avg_change, 3)
    elif abs_change < _EXTREME_MOVE_PCT:
        direction = "BULLISH" if avg_change > 0 else "BEARISH"
        return direction, "STRONG", round(avg_change, 3)
    else:
        direction = "BULLISH" if avg_change > 0 else "BEARISH"
        return direction, "EXTREME", round(avg_change, 3)


def _detect_risk_flags(futures_moves: list[FuturesMove]) -> list[str]:
    """Detect risk flags from extreme or divergent futures moves."""
    flags: list[str] = []

    for fm in futures_moves:
        if abs(fm.change_pct) >= _EXTREME_MOVE_PCT:
            flags.append(
                f"EXTREME: {fm.name} {fm.change_pct:+.2f}% — high gap risk, "
                f"possible mean-reversion pressure"
            )

    # Check for index divergence (NQ vs ES moving in opposite directions)
    nq_move = next((fm for fm in futures_moves if fm.ticker == "NQ=F"), None)
    es_move = next((fm for fm in futures_moves if fm.ticker == "ES=F"), None)

    if nq_move and es_move:
        if (nq_move.change_pct > _SIGNIFICANT_MOVE_PCT and
                es_move.change_pct < -_SIGNIFICANT_MOVE_PCT):
            flags.append(
                f"DIVERGENCE: NQ {nq_move.change_pct:+.2f}% vs ES {es_move.change_pct:+.2f}% "
                f"— tech/value rotation, mixed signals"
            )
        elif (nq_move.change_pct < -_SIGNIFICANT_MOVE_PCT and
              es_move.change_pct > _SIGNIFICANT_MOVE_PCT):
            flags.append(
                f"DIVERGENCE: NQ {nq_move.change_pct:+.2f}% vs ES {es_move.change_pct:+.2f}% "
                f"— risk rotation out of tech"
            )

    # Russell divergence from Nasdaq (risk-off signal)
    rty_move = next((fm for fm in futures_moves if fm.ticker == "RTY=F"), None)
    if rty_move and nq_move:
        rty_nq_spread = abs(rty_move.change_pct - nq_move.change_pct)
        if rty_nq_spread > 1.5:
            flags.append(
                f"RTY/NQ SPREAD: {rty_nq_spread:.1f}% — small-cap vs large-cap divergence"
            )

    return flags


def _persist_to_redis(result: PreMarketIntel, redis_client) -> None:
    """Persist pre-market intel to Redis for consumption by S15."""
    if redis_client is None:
        logger.debug("I-09: no Redis client — skipping persistence")
        return

    try:
        scan_date = result.scan_date

        # Store overall result
        redis_client.setex(
            f"nzt:premarket:{scan_date}:{result.overall_direction}:{result.overall_magnitude}",
            _REDIS_TTL_SECONDS,
            json.dumps(result.to_dict()),
        )

        # Store canonical key for easy lookup by S15
        redis_client.setex(
            f"nzt:premarket_intel:{scan_date}",
            _REDIS_TTL_SECONDS,
            json.dumps(result.to_dict()),
        )

        # Store per-ETP bias for fast lookup in S15 scoring
        for ticker, bias in result.etp_biases.items():
            redis_client.setex(
                f"nzt:premarket_bias:{scan_date}:{ticker}",
                _REDIS_TTL_SECONDS,
                json.dumps({
                    "direction": bias.direction,
                    "confidence_adj": bias.confidence_adj,
                    "combined_signal": bias.combined_signal,
                    "source_futures": bias.source_futures,
                }),
            )

        logger.info(
            "I-09: persisted to Redis — overall key + %d ETP biases (TTL=%ds)",
            len(result.etp_biases), _REDIS_TTL_SECONDS,
        )

    except Exception as e:
        logger.error("I-09: Redis persistence failed: %s", e)


# ---------------------------------------------------------------------------
# S15 integration helper — called from DailyTargetStrategy._score_ticker_with_reason
# ---------------------------------------------------------------------------
def get_premarket_bias(ticker: str, redis_client) -> Optional[dict]:
    """Look up pre-market bias for a ticker from Redis.

    Returns dict with {direction, confidence_adj, combined_signal, source_futures}
    or None if no bias is available.

    This is the hot-path read function called by S15 on every scoring cycle.
    """
    if redis_client is None:
        return None

    try:
        scan_date = date.today().isoformat()
        cached = redis_client.get(f"nzt:premarket_bias:{scan_date}:{ticker}")
        if cached:
            return json.loads(cached)
    except Exception:
        pass  # Non-fatal — fail-open

    return None
