"""
signal_engine/engine.py
========================
NZT-48 Signal Engine — strict mode + automatic fallback guarantee.

This is the single source of truth for all signals that flow into:
  - Command Center (real-time)
  - PDFs (batch)
  - Signal Tape (audit log)
  - DB (persistence)

Two-layer architecture:
  Layer 1 (STRICT):   all gates must pass at institutional thresholds
  Layer 2 (FALLBACK): stepwise gate relaxation, signals labelled clearly
                      Data Health is NEVER relaxed.

If even fallback produces < MIN_SIGNALS_FALLBACK → SignalDroughtReport.

Stop / target logic (fixes the 2%-target + 1xATR + R:R>=1.5 contradiction):
  - Stop: setup-type-specific ATR fraction (NOT always 1x)
      Continuation: 0.40x ATR
      Breakout:     0.35x ATR
      Default:      0.50x ATR
  - Primary target: max(1.2x stop_distance, ATR * target_mult)
  - Runner target:  2.5x stop_distance
  - R:R gate applied AFTER cost model deduction
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from data_hub.hub import DataHub

from signal_engine.gates import (
    run_full_gate_funnel,
    MIN_SIGNALS_STRICT,
    MIN_SIGNALS_FALLBACK,
)
from signal_engine.scoring import PlayScore, compute_play_score, SignalDroughtReport
from signal_engine.state_machine import SignalRecord, SignalTape
from uk_isa.isa_universe import (
    CORE_UNIVERSE, EXTENDED_UNIVERSE,
    ISA_FACTOR_GROUPS, LEVERAGE_MAP,
    get_factor_group, get_net_return,
)
from uk_isa.data_health import DataHealthGate, validate_universe
from learning.edge_ledger import get_edge_ledger, EdgeLedger

# Artifacts root (for v4.0 risk_officer.json and drought.json writes)
ARTIFACTS_ROOT = Path(__file__).parent.parent / "artifacts"

# Operating modes
MODE_WIN_RATE  = "WIN_RATE"
MODE_R_MULTIPLE = "R_MULTIPLE"

# Inverse ETPs — these go UP when the market goes DOWN.
# Direction "LONG" on an inverse = bearish market play, so regime gate must treat it as SHORT.
# F-03: import from single source of truth (config.universe_constants)
# NOTE: was previously missing 8 inverse ETPs — now has the complete set.
from config.universe_constants import INVERSE_ETPS_SET as INVERSE_ETPS

# RegimeConfidence threshold for WIN_RATE_MODE "easy conditions only"
WIN_RATE_MIN_REGIME_CONFIDENCE = 0.60

logger = logging.getLogger("nzt48.signal_engine")


# ---------------------------------------------------------------------------
# Signal schema validation
# ---------------------------------------------------------------------------

def _validate_signal_fields(ps: "PlayScore", session: str) -> "PlayScore":
    """Validate and fix critical signal fields before logging.

    Ensures no signal is logged with empty critical fields.
    """
    # strategy_tag must never be empty
    if not getattr(ps, 'setup_type', '') and not getattr(ps, 'strategy_tag', ''):
        ps.setup_type = "default"
        logger.warning("[ENGINE] signal %s has empty strategy_tag, defaulted to 'default'", ps.ticker)

    # Ensure composite is bounded
    if hasattr(ps, 'composite'):
        ps.composite = max(0.0, min(100.0, ps.composite))

    # Ensure entry > 0
    if ps.entry <= 0:
        logger.warning("[ENGINE] signal %s has invalid entry=%.4f", ps.ticker, ps.entry)

    # Ensure stop is set
    if ps.stop <= 0:
        logger.warning("[ENGINE] signal %s has invalid stop=%.4f", ps.ticker, ps.stop)

    return ps


# ---------------------------------------------------------------------------
# Stop / target ATR fractions by setup type
# ---------------------------------------------------------------------------
_STOP_ATR_FRACTIONS = {
    "continuation": 0.40,
    "breakout":     0.35,
    "mean_revert":  0.60,
    "default":      0.50,
}
_RUNNER_MULT = 2.5    # runner target = entry ± (stop_dist * _RUNNER_MULT)
_SPREAD_BPS: dict[str, float] = {
    "QQQ3.L": 15.0, "3LUS.L": 15.0, "QQQ5.L": 20.0, "SP5L.L": 20.0,
    "QQQS.L": 15.0, "3USS.L": 15.0, "3SEM.L": 18.0, "GPT3.L": 20.0,
    "NVD3.L": 18.0, "TSL3.L": 18.0, "TSM3.L": 20.0, "MU2.L":  22.0,
}
_SLIPPAGE_BPS = 5.0   # per side; round-trip = 2 * (_SPREAD_BPS/2 + _SLIPPAGE_BPS)


@dataclass
class TickerFeatures:
    """Derived features for one ticker, ready for gate + scoring."""
    ticker:        str
    direction:     str
    price:         float
    atr:           float
    atr_pct:       float
    rsi:           float
    macd_hist:     float
    ema_aligned:   bool
    bb_width_rank: float
    rvol:          Optional[float]
    adx:           float
    close:         float
    n_bars:        int
    regime:        str
    factor_group:  str
    setup_type:    str = "default"
    health_result: object = None
    # SHORT_WINDOW fields (v3.0)
    short_window:        bool  = False
    indicator_window:    int   = 14
    reliability_penalty: float = 0.0
    data_as_of:          str   = ""  # ISO timestamp of when data was fetched

    # Computed levels
    entry:   float = 0.0
    stop:    float = 0.0
    target1: float = 0.0
    target2: float = 0.0
    rr_net:  float = 0.0   # after cost model

    def compute_levels(self) -> None:
        """Fill entry/stop/targets using setup-type ATR fractions."""
        atr_frac = _STOP_ATR_FRACTIONS.get(self.setup_type, _STOP_ATR_FRACTIONS["default"])
        stop_dist = self.atr * atr_frac
        t1_dist   = max(stop_dist * 2.0, self.atr * 1.0)
        t2_dist   = stop_dist * _RUNNER_MULT

        if self.direction == "LONG":
            self.entry   = round(self.price, 4)
            self.stop    = round(self.price - stop_dist, 4)
            self.target1 = round(self.price + t1_dist, 4)
            self.target2 = round(self.price + t2_dist, 4)
        else:
            self.entry   = round(self.price, 4)
            self.stop    = round(self.price + stop_dist, 4)
            self.target1 = round(self.price - t1_dist, 4)
            self.target2 = round(self.price - t2_dist, 4)

        reward = abs(self.target1 - self.entry)
        risk   = abs(self.entry - self.stop)

        # Net R:R after round-trip cost
        spread_bps = _SPREAD_BPS.get(self.ticker, 20.0)
        rt_cost    = ((spread_bps + _SLIPPAGE_BPS * 2) / 10_000) * self.price
        net_reward = max(0.0, reward - rt_cost)
        self.rr_net = round(net_reward / risk, 3) if risk > 0 else 0.0


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------

class SignalEngine:
    """Produces PlayScore lists for Command Center and PDFs.

    Usage::
        engine = SignalEngine()
        result = engine.run(session="PRE_LSE")
        plays  = result.plays
        tape   = result.tape
    """

    def __init__(
        self,
        universe:      Optional[list[str]] = None,
        use_extended:  bool = False,
        tape:          Optional[SignalTape] = None,
        mode:          str = MODE_WIN_RATE,
    ) -> None:
        self.universe  = universe or (EXTENDED_UNIVERSE if use_extended else CORE_UNIVERSE)
        self.tape      = tape or SignalTape()
        self._health   = DataHealthGate()
        self.mode      = mode

        # Load edge ledger for confidence adjustments
        self._edge_data: dict = {}
        try:
            el = get_edge_ledger()
            self._edge_data = el.load()
            if self._edge_data:
                logger.info("[ENGINE] edge ledger loaded: %d buckets", len(self._edge_data))
        except Exception as e:
            logger.debug("[ENGINE] edge ledger not available: %s", e)

        # DataHub for validated market data
        self._data_hub = DataHub()

    # ------------------------------------------------------------------
    def run(
        self,
        session:            str   = "INTRADAY",
        regime:             str   = "NEUTRAL",
        regime_confidence:  float = 0.65,
        n_plays_min:   int = 5,
        n_plays_max:   int = 20,
        period:        str = "5d",
        interval:      str = "1h",
        write_artifacts: bool = True,
    ) -> "EngineResult":
        """Full pipeline: fetch → health check → gate funnel (strict) → fallback → score → rank."""

        logger.info("[ENGINE] run session=%s regime=%s mode=%s rc=%.2f",
                    session, regime, self.mode, regime_confidence)

        # WIN_RATE: enforce easy-conditions filter
        if self.mode == MODE_WIN_RATE and regime_confidence < WIN_RATE_MIN_REGIME_CONFIDENCE:
            logger.info("[ENGINE] WIN_RATE: regime_confidence %.2f < %.2f — reducing n_plays_min",
                        regime_confidence, WIN_RATE_MIN_REGIME_CONFIDENCE)
            n_plays_min = max(1, n_plays_min - 2)

        # 1. Fetch and health-check (use longer period for richer OHLCV)
        health_period = period if period not in ("1d", "2d") else "5d"
        health_summary = validate_universe(self.universe, period=health_period)
        healthy = {
            t: r for t, r in health_summary.results.items()
            if r.status in ("PASS", "WARN")
        }
        excluded = {
            t: r.exceptions for t, r in health_summary.results.items()
            if r.status == "FAIL"
        }
        if not healthy:
            logger.warning("[ENGINE] ALL tickers failed data health")
            return EngineResult.drought(
                tickers_checked=len(self.universe),
                hard_fail_count=len(excluded),
                blockers=[f"{t}: {'; '.join(e)}" for t, e in excluded.items()],
            )

        # 2. Fetch OHLCV for healthy tickers
        features_map: dict[str, TickerFeatures] = {}
        for ticker, hr in healthy.items():
            feat = self._build_features(ticker, hr, regime, period=period)
            if feat is not None:
                features_map[ticker] = feat

        if not features_map:
            return EngineResult.drought(
                tickers_checked=len(self.universe),
                hard_fail_count=len(excluded),
                blockers=["all healthy tickers returned empty features"],
            )

        # 3. Strict mode
        strict_plays, strict_gate_reports, group_counts_strict = self._run_mode(
            features_map, regime, fallback_step=0
        )

        # 4. Fallback if strict underdelivers
        fallback_plays: list[PlayScore] = []
        all_gate_reports = dict(strict_gate_reports)
        group_counts = dict(group_counts_strict)

        if len(strict_plays) < MIN_SIGNALS_FALLBACK:
            for step in range(1, 5):
                extra, extra_gates, extra_groups = self._run_mode(
                    features_map, regime,
                    fallback_step=step,
                    exclude_tickers={p.ticker for p in strict_plays + fallback_plays},
                )
                fallback_plays.extend(extra)
                all_gate_reports.update(extra_gates)
                for g, c in extra_groups.items():
                    group_counts[g] = group_counts.get(g, 0) + c
                if len(strict_plays) + len(fallback_plays) >= MIN_SIGNALS_FALLBACK:
                    break

        all_plays = strict_plays + fallback_plays
        all_plays.sort(key=lambda p: p.composite, reverse=True)
        # Validate signal fields
        all_plays = [_validate_signal_fields(p, session) for p in all_plays]
        all_plays = all_plays[:n_plays_max]

        # 5. Emit to tape
        for ps in all_plays[:n_plays_min]:   # emit top N to tape
            rec = SignalRecord.from_play_score(ps, session=session)
            rec.transition(rec.state.__class__.QUALIFIED, "engine qualified")
            self.tape.emit(rec)

        # 6. Drought check
        drought: Optional[SignalDroughtReport] = None
        if not all_plays:
            blockers = sorted(
                set(r.blocker for r in all_gate_reports.values() if r.blocker),
                key=lambda x: x
            )[:5]
            drought = SignalDroughtReport(
                top_blockers=blockers or ["no plays generated — unknown blocker"],
                tickers_checked=len(self.universe),
                hard_fail_count=len(excluded),
                soft_fail_count=len(features_map) - len(strict_plays),
                recommended_actions=[
                    "Check market hours — is session active?",
                    "Review data health: are .L tickers returning data?",
                    "Lower STRICT_MIN_ATR_PCT if all tickers fail TRADABILITY",
                    "Increase universe size (use EXTENDED_UNIVERSE)",
                ],
            )
            logger.warning("[ENGINE] SIGNAL DROUGHT: %s", drought.to_text())

        result = EngineResult(
            session=session,
            regime=regime,
            regime_confidence=regime_confidence,
            mode=self.mode,
            plays=all_plays,
            strict_count=len(strict_plays),
            fallback_count=len(fallback_plays),
            excluded=excluded,
            gate_reports=all_gate_reports,
            health_summary=health_summary,
            tape=self.tape,
            drought=drought,
        )

        # Run Strategy Router (additive — enriches plays with strategy context)
        router_result = None
        try:
            from signal_engine.strategy_router import StrategyRouter
            from datetime import datetime as _dt
            _now_uk = _dt.now()
            router = StrategyRouter()
            router_result = router.run(
                regime=regime,
                session=session,
                hour_uk=_now_uk.hour,
                minute_uk=_now_uk.minute,
                write_artifact=write_artifacts,
            )
            # Apply strategy-weighted score boost to all plays
            if router_result and not router_result.kill_switch:
                for ps in all_plays:
                    ps.strategy_weighted_score = router_result.apply_score_boost(ps.composite)
            logger.info("[ENGINE] router: active=%d boost=%.3f sizing=%s",
                        router_result.active_count() if router_result else 0,
                        router_result.score_boost if router_result else 0.0,
                        router_result.sizing_mode if router_result else "N/A")
        except Exception as router_err:
            logger.debug("[ENGINE] strategy router failed (non-fatal): %s", router_err)

        # Write artifacts unless suppressed
        if write_artifacts:
            try:
                from signal_engine.signal_card import SignalCard, write_plays_artifact
                cards = []
                for p in all_plays:
                    sc = SignalCard.from_play_score(p, session=session, regime=regime,
                                                   regime_confidence=regime_confidence)
                    # Enrich with router context
                    if router_result:
                        active = router_result.active_strategies
                        # Pick best matching active strategy for this play's setup_type
                        best_strat = None
                        for s in active:
                            if s.active and s.data_available:
                                if best_strat is None or s.weight > best_strat.weight:
                                    best_strat = s
                        if best_strat:
                            sc.strategy_tag = best_strat.tag
                            sc.why_strategy_now = best_strat.why_active[:3]
                        sc.overlay_tags = list(router_result.overlay_tags)
                        sc.overlay_warnings = list(router_result.overlay_warnings)
                        sc.time_of_day_window = router_result.time_of_day_window
                        sc.sizing_hint = ("S" if router_result.sizing_mode == "REDUCED"
                                          else ("XS" if router_result.sizing_mode == "DEFENSIVE"
                                                else "M"))
                        sc.strategy_weighted_score = getattr(p, "strategy_weighted_score", sc.composite)
                    # Enrich with SHORT_WINDOW data from features_map
                    feat = features_map.get(p.ticker)
                    if feat:
                        sc.bars_available = feat.n_bars
                        sc.indicator_window_used = feat.indicator_window
                        sc.reliability_penalty = feat.reliability_penalty
                        sc.short_window = feat.short_window
                        sc.data_reliability = max(0.0, 1.0 - feat.reliability_penalty)
                    # ── v4.0: Allocation weights ─────────────────────────────────
                    if router_result and router_result.allocation_weights:
                        sc.allocation_weight = router_result.allocation_weights.get(
                            sc.strategy_tag, 0.0
                        )
                        sc.final_rank_score = round(
                            sc.strategy_weighted_score
                            * max(sc.allocation_weight, 0.01)
                            * sc.risk_adjustment_factor,
                            2,
                        )

                    # ── v4.0: Execution Plan (spread-aware) ──────────────────────
                    spread_bps = _SPREAD_BPS.get(sc.ticker, 20.0)
                    spread_gate = (
                        "PASS" if spread_bps <= 20
                        else ("WATCH" if spread_bps <= 28 else "VETO")
                    )
                    sc.execution_plan = {
                        "order_type":         ("MARKETABLE_LIMIT"
                                               if (sc.rvol and sc.rvol > 1.0) else "LIMIT"),
                        "max_slippage_bps":   _SLIPPAGE_BPS * 2,
                        "spread_proxy_bps":   spread_bps,
                        "spread_gate_result": spread_gate,
                        "cancel_conditions":  [
                            f"price moves >{sc.atr_pct:.1f}% against entry before fill",
                            "session closes before fill",
                        ],
                        "time_in_trade_window": sc.time_of_day_window,
                    }

                    cards.append(sc)

                # ── v4.0: RiskOfficer evaluation ────────────────────────────────
                try:
                    from risk_officer.officer import RiskOfficer
                    officer = RiskOfficer()
                    evaluated = officer.evaluate(
                        cards=cards,
                        router_result=router_result,
                        features_map=features_map,
                        context={"vix": self._get_live_vix(), "consecutive_losses": 0},
                    )
                    for sc_ev, decision in evaluated:
                        sc_ev.risk_officer_decision = decision.decision
                        sc_ev.risk_officer_reasons  = decision.reasons
                        sc_ev.risk_adjustment_factor = decision.risk_score
                        if decision.decision != "VETO":
                            sc_ev.sizing_hint = decision.final_sizing
                    # Write risk_officer.json artifact
                    ro_report = officer.build_report(session, evaluated)
                    try:
                        today_str   = str(result.session)
                        session_key = session.lower().replace(" ", "_")
                        from datetime import date as _date
                        ro_dir = ARTIFACTS_ROOT / str(_date.today()) / session_key
                        ro_dir.mkdir(parents=True, exist_ok=True)
                        ro_path = ro_dir / "risk_officer.json"
                        import tempfile as _tf
                        ro_fd, ro_tmp = _tf.mkstemp(dir=ro_dir, suffix=".tmp")
                        with os.fdopen(ro_fd, "w") as rf:
                            rf.write(json.dumps(ro_report.to_dict(), indent=2, default=str))
                            rf.flush()
                            os.fsync(rf.fileno())
                        Path(ro_tmp).replace(ro_path)
                    except Exception:
                        pass
                except Exception as ro_err:
                    logger.debug("[ENGINE] RiskOfficer failed (non-fatal): %s", ro_err)

                # ── v4.0: DroughtPackage ─────────────────────────────────────────
                drought_pkg = None
                try:
                    from signal_engine.signal_card import build_drought_package
                    # Use gate_reports already on result (all_gate_reports from run loop)
                    drought_pkg = build_drought_package(drought, result.gate_reports, features_map)
                    # Store on result
                    result.drought_package = drought_pkg
                    # Write drought.json artifact
                    try:
                        from datetime import date as _date2
                        dr_dir = ARTIFACTS_ROOT / str(_date2.today()) / session.lower().replace(" ", "_")
                        dr_dir.mkdir(parents=True, exist_ok=True)
                        dr_path = dr_dir / "drought.json"
                        import tempfile as _tf2
                        dr_fd, dr_tmp = _tf2.mkstemp(dir=dr_dir, suffix=".tmp")
                        with os.fdopen(dr_fd, "w") as df:
                            df.write(json.dumps(drought_pkg.to_dict(), indent=2, default=str))
                            df.flush()
                            os.fsync(df.fileno())
                        Path(dr_tmp).replace(dr_path)
                    except Exception:
                        pass
                except Exception as dp_err:
                    logger.debug("[ENGINE] DroughtPackage build failed (non-fatal): %s", dp_err)

                art_path = write_plays_artifact(
                    cards=cards,
                    session=session,
                    regime=regime,
                    strict_count=len(strict_plays),
                    fallback_count=len(fallback_plays),
                    funnel=result.gate_funnel,
                    drought=drought.to_text().split("\n") if drought else None,
                )
                logger.info("[ENGINE] artifact written: %s", art_path)
            except Exception as art_err:
                logger.warning("[ENGINE] artifact write failed: %s", art_err)

        return result

    # ------------------------------------------------------------------
    def _run_mode(
        self,
        features_map:    dict[str, TickerFeatures],
        regime:          str,
        fallback_step:   int,
        exclude_tickers: set = frozenset(),
    ) -> tuple[list[PlayScore], dict, dict]:
        """Run gate funnel + scoring at a given fallback step."""
        plays:        list[PlayScore] = []
        gate_reports: dict = {}
        group_counts: dict[str, int] = {}

        for ticker, feat in features_map.items():
            if ticker in exclude_tickers:
                continue
            feat.compute_levels()
            gate = run_full_gate_funnel(
                ticker=ticker,
                direction=feat.direction,
                atr_pct=feat.atr_pct,
                close=feat.close,
                n_bars=feat.n_bars,
                rvol=feat.rvol,
                rr=feat.rr_net,
                momentum_score=(
                    0.3 * min(feat.rsi / 100, 1.0) +
                    0.4 * (0.8 if (feat.direction == "LONG") == (feat.macd_hist > 0) else 0.2) +
                    0.3 * (0.9 if feat.ema_aligned else 0.3)
                ),
                regime=regime,
                factor_group=feat.factor_group,
                group_counts=group_counts,
                health_result=feat.health_result,
                fallback_step=fallback_step,
                is_inverse=(ticker in INVERSE_ETPS),
            )
            gate_reports[ticker] = gate

            if gate.all_passed:
                ps = compute_play_score(
                    ticker=ticker,
                    direction=feat.direction,
                    rsi=feat.rsi,
                    macd_hist=feat.macd_hist,
                    ema_aligned=feat.ema_aligned,
                    atr_pct=feat.atr_pct,
                    bb_width_rank=feat.bb_width_rank,
                    rvol=feat.rvol,
                    adx=feat.adx,
                    regime=regime,
                    rr_ratio=feat.rr_net,
                    factor_group=feat.factor_group,
                    group_counts=group_counts,
                    entry=feat.entry,
                    stop=feat.stop,
                    target1=feat.target1,
                    target2=feat.target2,
                    setup_type=feat.setup_type,
                    fallback_step=fallback_step,
                )
                # Edge ledger confidence adjustment
                if self._edge_data:
                    self._apply_edge_adjustment(ps, feat, regime)
                plays.append(ps)
                group_counts[feat.factor_group] = group_counts.get(feat.factor_group, 0) + 1

        plays.sort(key=lambda p: p.composite, reverse=True)
        return plays, gate_reports, group_counts

    # ------------------------------------------------------------------
    def _build_features(
        self,
        ticker:       str,
        health_result,
        regime:       str,
        period:       str = "5d",
    ) -> Optional[TickerFeatures]:
        """Build TickerFeatures from health result's corrected_df."""
        try:
            df = getattr(health_result, "corrected_df", None)
            if df is None or df.empty:
                # Try a fresh fetch
                bar_result = self._data_hub.get_bars(ticker, period=period, interval="1h")
                if bar_result.df is None or bar_result.df.empty:
                    return None
                raw = bar_result.df
                # DataHub returns lowercase columns; engine expects Title case
                raw.columns = [c.title() for c in raw.columns]
                df = raw

            n_total = len(df)
            if n_total < 7:
                return None   # hard minimum: < 7 bars → meaningless indicators

            # Adaptive window: use available bars if < 14 (SHORT_WINDOW mode)
            short_window_mode = n_total < 14
            ind_window = min(n_total, 14)

            close   = float(df["Close"].iloc[-1])
            high_s  = df["High"].astype(float)
            low_s   = df["Low"].astype(float)
            close_s = df["Close"].astype(float)

            # ATR (adaptive window — Wilder's)
            tr  = pd.concat([
                high_s - low_s,
                (high_s - close_s.shift(1)).abs(),
                (low_s  - close_s.shift(1)).abs(),
            ], axis=1).max(axis=1)
            atr = float(tr.rolling(ind_window).mean().iloc[-1]) if len(tr) >= ind_window \
                  else float(tr.mean())
            atr_pct = (atr / close * 100) if close > 0 else 0.0

            # RSI (adaptive window, Wilder's smoothing)
            delta = close_s.diff()
            gain  = delta.clip(lower=0).ewm(alpha=1/ind_window, adjust=False).mean()
            loss  = (-delta.clip(upper=0)).ewm(alpha=1/ind_window, adjust=False).mean()
            rs    = gain / loss.replace(0, 1e-9)
            rsi   = float(100 - 100 / (1 + rs.iloc[-1]))

            # MACD histogram
            ema12 = close_s.ewm(span=12, adjust=False).mean()
            ema26 = close_s.ewm(span=26, adjust=False).mean()
            macd_line = ema12 - ema26
            signal_line = macd_line.ewm(span=9, adjust=False).mean()
            macd_hist = float((macd_line - signal_line).iloc[-1])

            # EMA alignment (9/20/50)
            ema9  = float(close_s.ewm(span=9,  adjust=False).mean().iloc[-1])
            ema20 = float(close_s.ewm(span=20, adjust=False).mean().iloc[-1])
            ema50 = float(close_s.ewm(span=50, adjust=False).mean().iloc[-1])
            ema_aligned_long  = close > ema9 > ema20 > ema50
            ema_aligned_short = close < ema9 < ema20 < ema50

            # Direction
            long_score = sum([
                rsi > 52,
                macd_hist > 0,
                ema_aligned_long,
                close > ema20,
            ])
            raw_direction = "LONG" if long_score >= 2 else "SHORT"

            # ISA constraint: you can only BUY in a T212 ISA — no shorting.
            # ALL tickers forced to LONG. If indicators say SHORT, skip —
            # we cannot open short positions in ISA.
            # For inverse ETPs: LONG means ETP price rising (market falling),
            # which is a valid BUY. SHORT means ETP price falling — skip.
            if raw_direction == "SHORT":
                return None  # can't short anything in ISA

            direction = "LONG"

            ema_aligned  = ema_aligned_long if direction == "LONG" else ema_aligned_short

            # ADX
            adx = _compute_adx(high_s, low_s, close_s)

            # BB width rank
            bb_ma  = close_s.rolling(20).mean()
            bb_std = close_s.rolling(20).std()
            bb_upper = bb_ma + 2 * bb_std
            bb_lower = bb_ma - 2 * bb_std
            bb_width = (bb_upper - bb_lower) / bb_ma.replace(0, 1e-9)
            bb_rank = float(
                bb_width.rank(pct=True).iloc[-1]
                if len(bb_width.dropna()) > 5 else 0.5
            )

            # RVOL
            vol_s = df["Volume"].astype(float)
            rvol: Optional[float] = None
            if vol_s.sum() > 0 and len(vol_s) >= 5:
                avg_vol = float(vol_s.iloc[:-1].mean())
                last_vol = float(vol_s.iloc[-1])
                rvol = round(last_vol / avg_vol, 2) if avg_vol > 0 else None

            factor_group = get_factor_group(ticker) or "other"
            setup_type   = _infer_setup_type(rsi, macd_hist, ema_aligned, bb_rank, adx)

            # SHORT_WINDOW reliability penalty: 0.05 per missing bar (honest, not faked)
            rel_penalty = round(0.05 * max(0, 14 - n_total), 3) if short_window_mode else 0.0

            feat = TickerFeatures(
                ticker=ticker,
                direction=direction,
                price=close,
                atr=atr,
                atr_pct=round(atr_pct, 3),
                rsi=round(rsi, 2),
                macd_hist=round(macd_hist, 6),
                ema_aligned=ema_aligned,
                bb_width_rank=round(bb_rank, 3),
                rvol=rvol,
                adx=round(adx, 2),
                close=close,
                n_bars=n_total,
                regime=regime,
                factor_group=factor_group,
                setup_type=setup_type,
                health_result=health_result,
                short_window=short_window_mode,
                indicator_window=ind_window,
                reliability_penalty=rel_penalty,
            )
            feat.data_as_of = datetime.now(timezone.utc).isoformat()
            feat.compute_levels()
            return feat

        except Exception as exc:
            logger.debug("_build_features %s failed: %s", ticker, exc)
            return None

    # ------------------------------------------------------------------
    def _get_live_vix(self) -> float:
        """Fetch live VIX from MarketStructureMonitor (15-min cache).
        Falls back to 20.0 (neutral) on failure."""
        try:
            from feeds.market_structure import MarketStructureMonitor
            ms = MarketStructureMonitor()
            vix_data = ms.fetch_vix_data()
            return float(vix_data.get("vix", 20.0))
        except Exception:
            return 20.0  # neutral default

    # ------------------------------------------------------------------
    def _apply_edge_adjustment(self, ps: PlayScore, feat: TickerFeatures, regime: str) -> None:
        """Adjust confidence based on historical edge data for this bucket."""
        # Build bucket key matching edge_ledger format: strategy_tag|regime_tag|track|time_window|liquidity_bucket
        strategy_tag = ps.setup_type or "default"
        track = ps.track or "INTRADAY_SWING"
        # Try multiple key patterns (exact match first, then relaxed)
        keys_to_try = [
            f"{strategy_tag}|{regime}|{track}|INTRADAY|NORMAL",
            f"{strategy_tag}|{regime}|{track}|ANY|NORMAL",
            f"{strategy_tag}|ANY|{track}|ANY|NORMAL",
        ]
        for key in keys_to_try:
            bucket = self._edge_data.get(key)
            if bucket is None:
                continue
            # Need minimum sample size to act on
            n = getattr(bucket, 'trades_count', 0) if hasattr(bucket, 'trades_count') else bucket.get('trades_count', 0)
            if n < 10:
                continue
            win_rate = getattr(bucket, 'win_rate', 0) if hasattr(bucket, 'win_rate') else bucket.get('win_rate', 0)
            # Apply adjustment: penalize losing buckets, boost winning ones
            if win_rate < 0.35:
                ps.composite = max(0, ps.composite - 10)
                ps.reasons.append(f"[-10] edge ledger: win_rate={win_rate:.0%} in bucket ({n} trades)")
                logger.debug("[ENGINE] edge penalty: %s bucket %s wr=%.2f n=%d", ps.ticker, key, win_rate, n)
            elif win_rate > 0.65 and n >= 20:
                ps.composite = min(100, ps.composite + 5)
                ps.reasons.append(f"[+5] edge ledger: win_rate={win_rate:.0%} in bucket ({n} trades)")
            # Recalculate stars after composite change
            ps.stars = 5 if ps.composite >= 90 else 4 if ps.composite >= 80 else 3 if ps.composite >= 70 else 2 if ps.composite >= 60 else 1
            ps.stars_str = {5: "[*****]", 4: "[****_]", 3: "[***__]", 2: "[**___]", 1: "[*____]"}.get(ps.stars, "[*____]")
            break  # Use first matching bucket


# ---------------------------------------------------------------------------
# EngineResult
# ---------------------------------------------------------------------------

@dataclass
class EngineResult:
    session:           str
    regime:            str
    plays:             list[PlayScore]
    regime_confidence: float = 0.65
    mode:              str   = MODE_WIN_RATE
    strict_count:      int   = 0
    fallback_count:    int   = 0
    excluded:          dict  = field(default_factory=dict)
    gate_reports:      dict  = field(default_factory=dict)
    health_summary:    object = None
    tape:              Optional[SignalTape] = None
    drought:           Optional[SignalDroughtReport] = None
    drought_package:   Optional[object] = None    # v4.0: DroughtPackage (set post-artifact)
    _gate_reports:     dict = field(default_factory=dict)   # v4.0: used by DroughtPackage builder

    @property
    def has_signals(self) -> bool:
        return len(self.plays) > 0

    @property
    def top_plays(self) -> list[PlayScore]:
        return self.plays

    @property
    def gate_funnel(self) -> dict:
        """Returns funnel counts for the Command Center panel."""
        total    = len(self.gate_reports)
        hard_ok  = sum(1 for r in self.gate_reports.values() if not r.hard_failed)
        soft_ok  = sum(1 for r in self.gate_reports.values() if r.all_passed)
        return {
            "tracked":          total,
            "data_valid":       hard_ok,
            "passed_all_gates": soft_ok,
            "signals_strict":   self.strict_count,
            "signals_fallback": self.fallback_count,
            "total_signals":    len(self.plays),
        }

    @property
    def blocker_summary(self) -> list[str]:
        from collections import Counter
        blockers = [r.blocker for r in self.gate_reports.values() if r.blocker]
        return [f"{b} ({c}x)" for b, c in Counter(blockers).most_common(5)]

    @classmethod
    def drought(cls, tickers_checked: int, hard_fail_count: int, blockers: list[str]) -> "EngineResult":
        dr = SignalDroughtReport(
            top_blockers=blockers[:5],
            tickers_checked=tickers_checked,
            hard_fail_count=hard_fail_count,
        )
        return cls(
            session="UNKNOWN", regime="UNKNOWN", plays=[],
            strict_count=0, fallback_count=0, drought=dr,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    try:
        plus_dm  = (high.diff()).clip(lower=0)
        minus_dm = (-low.diff()).clip(lower=0)
        tr       = pd.concat([high - low,
                               (high - close.shift(1)).abs(),
                               (low  - close.shift(1)).abs()], axis=1).max(axis=1)
        atr      = tr.rolling(period).mean()
        plus_di  = 100 * plus_dm.rolling(period).mean()  / atr.replace(0, 1e-9)
        minus_di = 100 * minus_dm.rolling(period).mean() / atr.replace(0, 1e-9)
        dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
        return float(dx.rolling(period).mean().iloc[-1])
    except Exception:
        return 0.0


def _infer_setup_type(rsi: float, macd_hist: float, ema_aligned: bool,
                      bb_rank: float, adx: float) -> str:
    """Classify setup so stop/target fractions are applied correctly."""
    if adx >= 25 and ema_aligned:
        return "continuation"
    if bb_rank >= 0.80:
        return "breakout"
    if rsi > 70 or rsi < 30:
        return "mean_revert"
    return "default"
