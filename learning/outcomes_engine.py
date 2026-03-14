"""
learning/outcomes_engine.py
============================
Path-based outcome resolver for NZT-48 signals.

Rules:
- Uses 1-min intraday bars from signal entry_time to expiry
- Determines which occurred first: stop hit vs target hit
- AMBIGUOUS (both in same bar): uses worst-case (stop hit)
- TIME_STOP: neither hit by expiry, exits at last bar close
- Computes MFE/MAE, realized_R gross and net (after cost model)
- Runs on schedule: SCALP=every 15min, SWING=T+4h + EOD

Signal log: data/signal_log.jsonl
Outcomes:   data/outcomes.jsonl
Index:      data/outcomes_index.json
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from data_hub.hub import DataHub

from learning.schemas import (
    SignalLogRecord, OutcomeRecord, CounterfactualVariant, make_signal_id
)
from learning.trade_attribution import TradeAttributionEngine, compute_attribution_from_outcome

logger = logging.getLogger("nzt48.learning.outcomes_engine")

# Paths
_DATA = Path(__file__).parent.parent / "data"
_SIGNAL_LOG = _DATA / "signal_log.jsonl"
_OUTCOMES   = _DATA / "outcomes.jsonl"
_INDEX      = _DATA / "outcomes_index.json"

# Cost model: spread + slippage estimates per ticker type
_COST_BPS = {
    "default":  8.0,   # default 8bps round-trip
    "3L":       12.0,  # 3x leveraged ETPs: wider spread
    "3S":       12.0,
    "QQQ5":     14.0,
    "SP5L":     10.0,
}


def _cost_bps_for(ticker: str) -> float:
    for key, bps in _COST_BPS.items():
        if key in ticker:
            return bps
    return _COST_BPS["default"]


def _fetch_intraday_bars(ticker: str, start_dt: datetime, end_dt: datetime):
    """Fetch 1-min bars for ticker between start and end (UTC datetimes).
    Uses DataHub with retry + backoff, falling back to hourly bars if 1m fails.
    """
    hub = DataHub()

    # Retry with exponential backoff
    bar_result = None
    for attempt in range(3):
        bar_result = hub.get_bars(ticker, period="5d", interval="1m")
        if bar_result.df is not None and not bar_result.df.empty:
            break
        # Retry backoff sleep — sync context, blocking is intentional.
        time.sleep(2 ** attempt)  # 1s, 2s, 4s backoff

    # Fallback: retry with longer period + hourly interval
    if bar_result is None or bar_result.df is None or bar_result.df.empty:
        logger.info("[OUTCOMES] 1m bars empty for %s, trying 1mo/1h fallback", ticker)
        bar_result = hub.get_bars(ticker, period="1mo", interval="1h")

    if bar_result.df is None or bar_result.df.empty:
        logger.warning("[OUTCOMES] no data for %s after retries, skipping resolution", ticker)
        return None

    df = bar_result.df
    # DataHub returns lowercase columns — outcomes_engine expects Title case
    df.columns = [c.title() for c in df.columns]

    try:
        # Filter to the window
        df = df[df.index >= start_dt]
        df = df[df.index <= end_dt]
        return df if not df.empty else None
    except Exception as e:
        logger.warning(f"Bar filter failed for {ticker}: {e}")
        return None


def _resolve_path(record: SignalLogRecord, bars) -> OutcomeRecord:
    """
    Core path-based resolution logic.
    Returns an OutcomeRecord with outcome filled in.
    """
    entry    = record.entry
    stop     = record.stop
    target1  = record.target1
    direction = record.direction.upper()
    cost_bps  = _cost_bps_for(record.ticker)

    mfe = 0.0
    mae = 0.0
    outcome     = "TIME_STOP"
    exit_price  = 0.0
    exit_bar_idx = len(bars) - 1
    bars_used   = len(bars)

    for i, (idx, row) in enumerate(bars.iterrows()):
        high = float(row["High"])
        low  = float(row["Low"])
        close = float(row["Close"])

        # MFE/MAE tracking
        if direction == "LONG":
            mfe = max(mfe, (high - entry) / entry * 100)
            mae = min(mae, (low  - entry) / entry * 100)
        else:
            mfe = max(mfe, (entry - low)  / entry * 100)
            mae = min(mae, (entry - high) / entry * 100)

        # Check target and stop in this bar
        if direction == "LONG":
            stop_hit   = low  <= stop
            target_hit = high >= target1
        else:
            stop_hit   = high >= stop
            target_hit = low  <= target1

        if stop_hit and target_hit:
            # Ambiguous: conservative worst-case = stop hit
            outcome    = "HIT_STOP"
            exit_price = stop
            exit_bar_idx = i
            break
        elif target_hit:
            outcome    = "HIT_TARGET"
            exit_price = target1
            exit_bar_idx = i
            break
        elif stop_hit:
            outcome    = "HIT_STOP"
            exit_price = stop
            exit_bar_idx = i
            break

    # Time stop: use last bar close
    if outcome == "TIME_STOP":
        last_close = float(bars.iloc[-1]["Close"])
        exit_price = last_close

    # Duration
    try:
        entry_ts = bars.index[0]
        exit_ts  = bars.index[min(exit_bar_idx, len(bars)-1)]
        duration_min = int((exit_ts - entry_ts).total_seconds() / 60)
    except Exception:
        duration_min = 0

    # P&L in R — denominator = RISK distance (entry → stop), not reward distance
    rr_denom = abs(entry - stop) if abs(entry - stop) > 0.0001 else abs(entry * 0.01)
    if direction == "LONG":
        pnl_r_gross = (exit_price - entry) / rr_denom
    else:
        pnl_r_gross = (entry - exit_price) / rr_denom

    # Cost model
    cost_r = (cost_bps / 10000) * entry / rr_denom
    pnl_r_net = pnl_r_gross - cost_r

    # Counterfactuals
    cfs = _compute_counterfactuals(record, bars, direction, entry, target1, stop, rr_denom, cost_r)

    now_str = datetime.now(timezone.utc).isoformat()
    return OutcomeRecord(
        signal_id    = record.signal_id,
        ticker       = record.ticker,
        direction    = record.direction,
        strategy_tag = record.strategy_tag,
        regime_tag   = record.regime_tag,
        time_window  = record.time_window,
        track        = record.track,
        session      = record.session,
        entry        = entry,
        stop         = stop,
        target1      = target1,
        net_rr       = record.net_rr,
        generated_at = record.generated_at,
        outcome      = outcome,
        exit_price   = round(exit_price, 4),
        pnl_r_gross  = round(pnl_r_gross, 4),
        pnl_r_net    = round(pnl_r_net, 4),
        mfe_pct      = round(mfe, 4),
        mae_pct      = round(mae, 4),
        duration_minutes = duration_min,
        cost_bps     = round(cost_bps, 2),
        closed_at    = now_str,
        resolution_method = "PATH_BASED",
        bars_used    = bars_used,
        counterfactuals = cfs,
    )


def _compute_counterfactuals(record, bars, direction, entry, target1, stop, rr_denom, base_cost_r) -> list:
    """Compute shadow variants for policy learning."""
    cfs = []
    atr_est = abs(target1 - stop)  # rough proxy

    # Stop variants
    for mult, label in [(0.35, "stop_0.35xATR"), (0.50, "stop_0.50xATR"), (0.65, "stop_0.65xATR")]:
        alt_stop = entry - mult * atr_est if direction == "LONG" else entry + mult * atr_est
        alt_rec = SignalLogRecord(
            signal_id=record.signal_id, ticker=record.ticker, direction=record.direction,
            strategy_tag=record.strategy_tag, regime_tag=record.regime_tag,
            time_window=record.time_window, track=record.track, session=record.session,
            composite=record.composite, entry=entry, stop=alt_stop, target1=target1,
            target2=target1, net_rr=record.net_rr, generated_at=record.generated_at,
            date_str=record.date_str,
        )
        try:
            result = _resolve_path(alt_rec, bars)
            cfs.append({"label": label, "exit_price": result.exit_price,
                        "pnl_r_gross": result.pnl_r_gross, "pnl_r_net": result.pnl_r_net,
                        "outcome": result.outcome})
        except Exception:
            pass

    return cfs


class OutcomeEngine:
    """Resolves PENDING signals in signal_log.jsonl using path-based bar analysis."""

    def __init__(self):
        _DATA.mkdir(parents=True, exist_ok=True)

    def load_pending(self) -> list[SignalLogRecord]:
        """Load all PENDING signals from signal_log.jsonl."""
        records = []
        if not _SIGNAL_LOG.exists():
            return records
        try:
            with open(_SIGNAL_LOG) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    d = json.loads(line)
                    r = SignalLogRecord.from_dict(d)
                    if r.outcome == "PENDING":
                        records.append(r)
        except Exception as e:
            logger.error(f"Error loading signal log: {e}")
        return records

    def load_index(self) -> dict:
        if _INDEX.exists():
            try:
                return json.loads(_INDEX.read_text())
            except Exception:
                return {}
        return {}

    def save_outcome(self, outcome: OutcomeRecord, attribution: dict = None) -> None:
        """Append resolved outcome to outcomes.jsonl and update index.

        Args:
            outcome: The resolved OutcomeRecord.
            attribution: Optional TradeAttribution dict (AEGIS 0-10).
                        Merged under "attribution" key -- additive, never breaks schema.
        """
        out_dict = outcome.to_dict()
        if attribution:
            out_dict["attribution"] = attribution
        with open(_OUTCOMES, "a") as f:
            f.write(json.dumps(out_dict) + "\n")

        idx = self.load_index()
        idx[outcome.signal_id] = {
            "outcome": outcome.outcome,
            "pnl_r_net": outcome.pnl_r_net,
            "closed_at": outcome.closed_at,
        }
        _INDEX.write_text(json.dumps(idx, indent=2))
        logger.info(f"Resolved {outcome.signal_id}: {outcome.outcome} @ {outcome.pnl_r_net:.2f}R")

    def mark_resolved_in_log(self, signal_id: str) -> None:
        """Update signal_log.jsonl to mark a signal as RESOLVED."""
        if not _SIGNAL_LOG.exists():
            return
        lines = []
        try:
            with open(_SIGNAL_LOG) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    d = json.loads(line)
                    if d.get("signal_id") == signal_id:
                        d["outcome"] = "RESOLVED"
                    lines.append(json.dumps(d))
        except Exception as e:
            logger.error(f"Error updating signal log: {e}")
            return
        with open(_SIGNAL_LOG, "w") as f:
            f.write("\n".join(lines) + "\n")

    def resolve_all_pending(self, max_signals: int = 50) -> dict:
        """
        Main entry point. Resolves all PENDING signals that have passed their expiry.
        Returns stats dict.
        """
        pending = self.load_pending()
        index   = self.load_index()
        now     = datetime.now(timezone.utc)

        stats = {"checked": 0, "resolved": 0, "still_pending": 0, "errors": 0}

        # Deduplicate vs index
        pending = [p for p in pending if p.signal_id not in index]
        pending = pending[:max_signals]

        # Determine expiry per track
        def expiry(r: SignalLogRecord) -> datetime:
            try:
                gen = datetime.fromisoformat(r.generated_at.replace("Z", "+00:00"))
            except Exception:
                return now - timedelta(hours=1)
            if "SCALP" in r.track:
                return gen + timedelta(minutes=90)
            elif "OVERNIGHT" in r.track:
                return gen + timedelta(hours=24)
            else:
                return gen + timedelta(hours=6)

        # Group by ticker for batch fetching
        ticker_groups: dict[str, list] = {}
        for r in pending:
            exp = expiry(r)
            if now < exp:
                stats["still_pending"] += 1
                continue
            ticker_groups.setdefault(r.ticker, []).append(r)

        stats["checked"] = sum(len(v) for v in ticker_groups.values())

        for ticker, records in ticker_groups.items():
            # Fetch bars once per ticker
            earliest = min(
                datetime.fromisoformat(r.generated_at.replace("Z", "+00:00"))
                for r in records
            )
            latest_exp = max(expiry(r) for r in records)
            bars = _fetch_intraday_bars(ticker, earliest, latest_exp)

            for r in records:
                try:
                    attribution_dict = None
                    signal_bars = None

                    if bars is None or bars.empty:
                        # No bars: mark as TIME_STOP with current price via DataHub
                        try:
                            hub = DataHub()
                            quote = hub.get_quote(r.ticker)
                            cur_price = float(quote.get("last_price", r.entry)) if quote else r.entry
                        except Exception:
                            cur_price = r.entry
                        logger.warning("[OUTCOMES] resolution failed for signal %s: no bar data after retries", r.signal_id)
                        outcome = OutcomeRecord(
                            signal_id=r.signal_id, ticker=r.ticker, direction=r.direction,
                            strategy_tag=r.strategy_tag, regime_tag=r.regime_tag,
                            time_window=r.time_window, track=r.track, session=r.session,
                            entry=r.entry, stop=r.stop, target1=r.target1,
                            net_rr=r.net_rr, generated_at=r.generated_at,
                            outcome="TIME_STOP", exit_price=cur_price,
                            closed_at=now.isoformat(),
                            resolution_method="PRICE_STUB",
                        )
                    else:
                        # Filter bars to this signal's window
                        try:
                            gen = datetime.fromisoformat(r.generated_at.replace("Z", "+00:00"))
                        except Exception:
                            gen = now - timedelta(hours=6)
                        exp = expiry(r)
                        signal_bars = bars[bars.index >= gen]
                        signal_bars = signal_bars[signal_bars.index <= exp]
                        if signal_bars.empty:
                            signal_bars = bars

                        outcome = _resolve_path(r, signal_bars)

                    # AEGIS 0-10: Compute TradeAttribution when bars available
                    try:
                        # Post-exit bars for shadow markout analysis
                        post_exit_bars = None
                        if signal_bars is not None and not signal_bars.empty and outcome.closed_at:
                            try:
                                exit_dt = datetime.fromisoformat(outcome.closed_at.replace("Z", "+00:00"))
                                post_exit_bars = bars[bars.index > exit_dt] if bars is not None else None
                            except Exception:
                                pass

                        attribution_dict = compute_attribution_from_outcome(
                            outcome.to_dict(),
                            price_series=signal_bars,
                            post_exit_series=post_exit_bars,
                        )
                    except Exception as attr_err:
                        logger.debug("[OUTCOMES] attribution failed for %s: %s", r.signal_id, attr_err)

                    self.save_outcome(outcome, attribution=attribution_dict)
                    self.mark_resolved_in_log(r.signal_id)
                    stats["resolved"] += 1
                except Exception as e:
                    logger.error(f"Failed to resolve {r.signal_id}: {e}")
                    stats["errors"] += 1

        return stats


# Module-level instance
_engine: Optional[OutcomeEngine] = None

def get_outcome_engine() -> OutcomeEngine:
    global _engine
    if _engine is None:
        _engine = OutcomeEngine()
    return _engine
