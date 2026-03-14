"""
signal_engine/signal_card.py
=============================
Canonical SignalCard model — single schema shared by Command Center,
all 3 daily PDFs, and the Mega PDF.

plays.json schema (written to artifacts/YYYY-MM-DD/{session}/plays.json):
  {
    "generated_at": "ISO8601",
    "session":      "PRE_LSE",
    "regime":       "NEUTRAL",
    "mode":         "WIN_RATE",
    "strict_count": 3,
    "fallback_count": 2,
    "drought":      null | {...},
    "funnel":       {...},
    "plays": [ <SignalCard.to_dict()>, ... ]
  }
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# SignalCard — one play / signal, fully described
# ---------------------------------------------------------------------------

@dataclass
class SignalCard:
    # Identity
    ticker:        str
    direction:     str                     # LONG | SHORT

    # Track
    track:         str  = "INTRADAY_SWING" # SCALP | INTRADAY_SWING
    mode:          str  = "WIN_RATE"       # WIN_RATE | R_MULTIPLE

    # Rating
    stars:         int  = 1
    stars_str:     str  = "[*____]"
    composite:     float = 0.0             # 0-100 PlayScore
    label:         str  = "STRICT"         # STRICT | WATCH-SIGNAL (xxx) | ...

    # Component scores (0-1)
    momentum_score:   float = 0.0
    volatility_score: float = 0.0
    regime_score:     float = 0.0
    liquidity_score:  float = 0.0
    rr_score:         float = 0.0
    quality_score:    float = 0.0

    # Trade levels
    entry:         float = 0.0
    stop:          float = 0.0
    target1:       float = 0.0
    target2:       float = 0.0
    rr_ratio:      float = 0.0
    stop_distance_pct: float = 0.0
    target1_distance_pct: float = 0.0

    # Trade plan (WIN_RATE_MODE)
    setup_type:       str   = "default"    # continuation|breakout|mean_revert|default
    entry_zone_lo:    float = 0.0
    entry_zone_hi:    float = 0.0
    be_level:         float = 0.0          # break-even move to entry at +R
    partial_at:       float = 0.0          # price to take partial
    time_stop_min:    int   = 0            # exit if not +0.3R by halfway
    time_stop_full:   int   = 0            # full time stop in minutes

    # Risk metadata
    factor_group:     str   = ""
    atr_pct:          float = 0.0
    rvol:             Optional[float] = None
    rvol_reliable:    bool  = True
    data_reliability: float = 1.0          # 0-1 DataReliabilityScore
    decay_risk:       str   = "LOW"
    spread_risk:      str   = "LOW"
    fallback_step:    int   = 0
    why_fallback:     str   = ""           # if fallback_step > 0

    # Regime context
    regime:           str   = "NEUTRAL"
    regime_confidence: float = 0.0

    # Reasons (human-readable bullets)
    reasons:          list[str] = field(default_factory=list)
    exclusion_reason: str  = ""            # populated for EXCLUDED cards

    # Session context
    session:          str  = ""
    generated_at:     str  = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # Category for PDF sectioning
    category:         str  = "TRADE"       # TRADE | WATCH | EXCLUDED

    # Strategy Router fields (v3.0)
    strategy_tag:        str   = ""           # e.g. TREND_MOMENTUM_CTA
    why_strategy_now:    list[str] = field(default_factory=list)
    time_of_day_window:  str   = ""           # e.g. MORNING_MOMENTUM
    overlay_tags:        list[str] = field(default_factory=list)
    overlay_warnings:    list[str] = field(default_factory=list)
    base_score:          float = 0.0          # composite before strategy boost
    strategy_weighted_score: float = 0.0      # boosted score (used for final rank)
    sizing_hint:         str   = "M"          # S / M / L
    sizing_reason:       str   = ""

    # SHORT_WINDOW fields (v3.0 — honest adaptive windowing)
    bars_available:      int   = 0
    indicator_window_used: int = 14
    reliability_penalty: float = 0.0
    short_window:        bool  = False        # True if 7 <= bars < 14

    # ── v4.0 fields: Risk Officer ────────────────────────────────────────
    risk_officer_decision:  str       = ""    # APPROVE / DOWNSIZE / VETO / ""
    risk_officer_reasons:   list[str] = field(default_factory=list)
    risk_adjustment_factor: float     = 1.0  # 0-1 risk severity (0=safe, 1=full veto risk)

    # ── v4.0 fields: Execution Plan ──────────────────────────────────────
    execution_plan: dict = field(default_factory=dict)
    # Schema: {order_type, max_slippage_bps, spread_proxy_bps,
    #          spread_gate_result, cancel_conditions, time_in_trade_window}

    # ── v4.0 fields: Capital Allocation ──────────────────────────────────
    allocation_weight: float = 0.0    # from RouterResult.allocation_weights[strategy_tag]
    final_rank_score:  float = 0.0    # strategy_weighted_score * alloc_weight * risk_adj

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_play_score(cls, ps, session: str = "", regime: str = "NEUTRAL",
                        regime_confidence: float = 0.0) -> "SignalCard":
        """Build a SignalCard from a PlayScore object."""
        # track inference
        track = getattr(ps, "track", "INTRADAY_SWING")
        mode  = getattr(ps, "mode_label", "WIN_RATE")

        # Entry zone ±0.10% of entry
        ez_lo = round(ps.entry * 0.999, 4)
        ez_hi = round(ps.entry * 1.001, 4)

        # Break-even and partial levels
        stop_dist  = abs(ps.entry - ps.stop)
        be_move    = stop_dist * 0.6     # BE at +0.6R
        partial    = stop_dist * 1.0     # partial at +1R (T1 for scalp)
        time_full  = 180 if track == "INTRADAY_SWING" else 12
        time_half  = time_full // 2

        if ps.direction == "LONG":
            be_level = round(ps.entry + be_move, 4)
            partial_at = round(ps.entry + partial, 4)
        else:
            be_level = round(ps.entry - be_move, 4)
            partial_at = round(ps.entry - partial, 4)

        stop_pct   = round(stop_dist / ps.entry * 100, 3) if ps.entry else 0.0
        t1_dist    = abs(ps.target1 - ps.entry)
        t1_pct     = round(t1_dist / ps.entry * 100, 3) if ps.entry else 0.0

        why_fallback = ""
        if ps.fallback_step > 0:
            _why = {1: "RVOL below strict threshold (relaxed to 0.55)",
                    2: "R:R below strict minimum (relaxed to 1.2)",
                    3: "Momentum below strict threshold (relaxed to 0.40)",
                    4: "ATR% below strict minimum (relaxed to 0.60%)"}
            why_fallback = _why.get(ps.fallback_step, f"fallback step {ps.fallback_step}")

        cat = "WATCH" if ps.fallback_step > 0 else "TRADE"

        return cls(
            ticker=ps.ticker,
            direction=ps.direction,
            track=track,
            mode=mode,
            stars=ps.stars,
            stars_str=getattr(ps, "stars_str", "[*____]"),
            composite=ps.composite,
            label=ps.label,
            momentum_score=getattr(ps, "momentum", 0.0),
            volatility_score=getattr(ps, "volatility", 0.0),
            regime_score=getattr(ps, "regime_fit", 0.0),
            liquidity_score=getattr(ps, "liquidity", 0.0),
            rr_score=getattr(ps, "rr_score", 0.0),
            quality_score=getattr(ps, "quality", 0.0),
            entry=ps.entry,
            stop=ps.stop,
            target1=ps.target1,
            target2=ps.target2,
            rr_ratio=ps.rr_ratio,
            stop_distance_pct=stop_pct,
            target1_distance_pct=t1_pct,
            setup_type=ps.setup_type,
            entry_zone_lo=ez_lo,
            entry_zone_hi=ez_hi,
            be_level=be_level,
            partial_at=partial_at,
            time_stop_full=time_full,
            time_stop_min=time_half,
            factor_group=ps.factor_group,
            atr_pct=ps.atr_pct,
            rvol=ps.rvol if ps.rvol else None,
            rvol_reliable=bool(ps.rvol and ps.rvol > 0),
            data_reliability=1.0,
            decay_risk=ps.decay_risk,
            spread_risk=ps.spread_risk,
            fallback_step=ps.fallback_step,
            why_fallback=why_fallback,
            regime=regime,
            regime_confidence=regime_confidence,
            reasons=list(ps.reasons),
            session=session,
            category=cat,
        )


# ---------------------------------------------------------------------------
# Artifact writer
# ---------------------------------------------------------------------------

ARTIFACTS_ROOT = Path(__file__).parent.parent / "artifacts"


def write_plays_artifact(
    cards:         list[SignalCard],
    session:       str,
    regime:        str,
    strict_count:  int,
    fallback_count: int,
    funnel:        dict,
    drought:       Optional[dict] = None,
    run_date:      Optional[date] = None,
) -> Path:
    """Write plays.json to artifacts/YYYY-MM-DD/{session}/plays.json (atomic)."""
    today = run_date or date.today()
    session_key = session.lower().replace(" ", "_")
    out_dir = ARTIFACTS_ROOT / str(today) / session_key
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "session":       session,
        "regime":        regime,
        "mode":          "WIN_RATE",
        "strict_count":  strict_count,
        "fallback_count": fallback_count,
        "total_plays":   len(cards),
        "drought":       drought,
        "funnel":        funnel,
        "plays":         [c.to_dict() for c in cards],
    }

    out_path = out_dir / "plays.json"
    # Atomic write: tmp → fsync → rename (prevents partial-file reads)
    import tempfile
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


# ---------------------------------------------------------------------------
# Session run status tracker
# ---------------------------------------------------------------------------

_STATUS_PATH = Path(__file__).parent.parent / "data" / "session_status.json"


def update_session_status(
    session_name:           str,
    run_id:                 str,
    artifacts_written:      bool,
    pdf_written:            bool,
    error_msg:              str       = "",
    # v4.0 additions (all optional with safe defaults for backward compat)
    artifact_paths:         list      = None,
    pdf_path:               str       = "",
    signals_strict_count:   int       = 0,
    signals_fallback_count: int       = 0,
    drought_flag:           bool      = False,
    top_blockers:           list      = None,
    generated_at_uk:        str       = "",
) -> None:
    """Update data/session_status.json with latest run result for a session (v4.0)."""
    try:
        _STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if _STATUS_PATH.exists():
            try:
                existing = json.loads(_STATUS_PATH.read_text())
            except Exception:
                existing = {}

        existing[session_name] = {
            # Core (v3.0 compat)
            "run_id":                run_id,
            "timestamp":             datetime.now(timezone.utc).isoformat(),
            "artifacts_written":     artifacts_written,
            "pdf_written":           pdf_written,
            "error_msg":             error_msg,
            "status":                "PASS" if artifacts_written and pdf_written else "FAIL",
            # v4.0 extended fields
            "artifact_paths":        artifact_paths or [],
            "pdf_path":              pdf_path,
            "signals_strict_count":  signals_strict_count,
            "signals_fallback_count": signals_fallback_count,
            "drought_flag":          drought_flag,
            "top_blockers":          top_blockers or [],
            "generated_at_uk":       generated_at_uk or datetime.now(timezone.utc).isoformat(),
        }

        # Atomic write
        import tempfile
        tmp_fd, tmp_name = tempfile.mkstemp(dir=_STATUS_PATH.parent, suffix=".tmp")
        with os.fdopen(tmp_fd, "w") as f:
            f.write(json.dumps(existing, indent=2, default=str))
            f.flush()
            os.fsync(f.fileno())
        Path(tmp_name).replace(_STATUS_PATH)
    except Exception as exc:
        import logging as _log
        _log.getLogger("nzt48.signal_card").warning("session_status update failed: %s", exc)


def read_session_status() -> dict:
    """Read the current session status JSON. Returns {} if not found."""
    try:
        if _STATUS_PATH.exists():
            return json.loads(_STATUS_PATH.read_text())
    except Exception:
        pass
    return {}


def read_plays_artifact(
    session: str,
    run_date: Optional[date] = None,
) -> Optional[dict]:
    """Read plays.json from artifacts/YYYY-MM-DD/{session}/plays.json."""
    today = run_date or date.today()
    session_key = session.lower().replace(" ", "_")
    path = ARTIFACTS_ROOT / str(today) / session_key / "plays.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


def latest_artifact(session: str) -> Optional[dict]:
    """Find the most recent artifact for a session (today or yesterday)."""
    from datetime import timedelta
    for delta in (0, 1, 2):
        d = date.today() - timedelta(days=delta)
        data = read_plays_artifact(session, run_date=d)
        if data:
            return data
    return None


# ---------------------------------------------------------------------------
# v4.0 Drought Package — Actionable Drought Analysis
# ---------------------------------------------------------------------------

@dataclass
class ClosestMiss:
    """One ticker that nearly passed — closest to admission."""
    ticker:               str
    strategy_tag:         str   = ""
    track:                str   = "INTRADAY_SWING"
    failed_gate:          str   = ""      # gate name that blocked it
    observed:             float = 0.0    # actual value observed
    required:             float = 0.0    # strict threshold it needed to meet
    delta:                float = 0.0    # |required - observed| (smaller = closer to pass)
    fallback_step_admits: int   = -1     # which step admits it (1-4); -1 = never
    safest_knob:          str   = ""     # plain-English suggestion

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RecommendedKnob:
    """One bounded, safe parameter adjustment suggestion."""
    param_name:      str
    current:         float
    suggested:       float
    bounded:         bool    # True if safe to change without DataHealth bypass
    tradeoff:        str     # "Admits 2 more signals but lowers quality bar"
    expected_effect: str     # "Admits TSL3.L and 3SEM.L at fallback step 1"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DroughtPackage:
    """Complete drought analysis — written to artifacts and surfaced in War Room."""
    drought_flag:       bool
    closest_misses:     list[ClosestMiss]    = field(default_factory=list)
    recommended_knobs:  list[RecommendedKnob] = field(default_factory=list)
    blockers_summary:   list[str]             = field(default_factory=list)
    tickers_checked:    int                   = 0
    generated_at:       str                   = ""

    def to_dict(self) -> dict:
        d = {
            "drought_flag":      self.drought_flag,
            "closest_misses":    [m.to_dict() for m in self.closest_misses],
            "recommended_knobs": [k.to_dict() for k in self.recommended_knobs],
            "blockers_summary":  self.blockers_summary,
            "tickers_checked":   self.tickers_checked,
            "generated_at":      self.generated_at,
        }
        return d


def build_drought_package(
    drought,         # SignalDroughtReport | None (from engine)
    gate_reports: dict,    # ticker -> TickerGateReport-like object or dict
    features_map: dict,    # ticker -> TickerFeatures-like object or dict
) -> DroughtPackage:
    """
    Compute ClosestMisses and RecommendedKnobs from gate_reports.
    Called from engine.py after the run loop.
    Safe: never bypasses DataHealth gate.
    """
    from datetime import datetime as _dt, timezone as _tz
    import logging as _log
    logger = _log.getLogger("nzt48.drought")

    if not drought and not gate_reports:
        return DroughtPackage(
            drought_flag=False,
            generated_at=_dt.now(_tz.utc).isoformat(),
        )

    # Collect ticker-level gate failures for ClosestMiss candidates
    misses: list[ClosestMiss] = []
    blockers_count: dict[str, int] = {}

    # Gate thresholds (strict)
    _STRICT = {
        "TRADABILITY":       ("atr_pct",  1.0),
        "VOLUME_LIQUIDITY":  ("rvol",     0.80),
        "RR_RATIO":          ("rr_ratio", 1.50),
        "MOMENTUM":          ("momentum", 0.55),
    }
    # Which fallback step admits each gate failure
    _FALLBACK_STEP = {
        "VOLUME_LIQUIDITY":  (1, 0.55),
        "RR_RATIO":          (2, 1.20),
        "MOMENTUM":          (3, 0.40),
        "TRADABILITY":       (4, 0.60),
    }

    for ticker, report in gate_reports.items():
        # Support both dict and object-style gate reports
        def _get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        blocker = _get(report, "blocker", "")
        if not blocker or blocker in ("DATA_HEALTH", "PRICE_SCALE", "MIN_BARS"):
            continue  # Skip hard gate failures — not tunable

        if blocker in blockers_count:
            blockers_count[blocker] += 1
        else:
            blockers_count[blocker] = 1

        # Get observed value
        feats = features_map.get(ticker, {})
        _fget = lambda k, d=0.0: feats.get(k, d) if isinstance(feats, dict) else getattr(feats, k, d)

        if blocker in _STRICT:
            feat_key, threshold = _STRICT[blocker]
            observed = _fget(feat_key)
            delta    = round(abs(threshold - observed), 4)

            fallback_step, fallback_thresh = _FALLBACK_STEP.get(blocker, (-1, 0.0))
            admits = fallback_step if (observed >= fallback_thresh and fallback_thresh > 0) else -1

            # Safest knob suggestion
            if blocker == "TRADABILITY":
                safest = f"Wait for higher volatility session (ATR%={observed:.2f}% vs required 1.0%)"
            elif blocker == "VOLUME_LIQUIDITY":
                safest = f"Lower STRICT_MIN_RVOL from 0.8 to {max(0.55, observed+0.02):.2f} (bounded: 0.55)"
            elif blocker == "RR_RATIO":
                safest = f"Lower STRICT_MIN_RR from 1.5 to {max(1.2, observed+0.05):.2f} (bounded: 1.2)"
            elif blocker == "MOMENTUM":
                safest = f"Lower STRICT_MOMENTUM_MIN from 0.55 to {max(0.40, observed+0.02):.2f} (bounded: 0.40)"
            else:
                safest = f"Investigate {blocker}: observed={observed:.3f} required={threshold:.3f}"

            misses.append(ClosestMiss(
                ticker=ticker,
                strategy_tag="",
                track="INTRADAY_SWING",
                failed_gate=blocker,
                observed=round(observed, 4),
                required=round(threshold, 4),
                delta=delta,
                fallback_step_admits=admits,
                safest_knob=safest,
            ))

    # Sort by delta ascending (closest misses first)
    misses.sort(key=lambda m: m.delta)
    top_misses = misses[:10]

    # Build RecommendedKnobs from most common blockers
    knobs: list[RecommendedKnob] = []
    for gate_name, count in sorted(blockers_count.items(), key=lambda x: -x[1])[:3]:
        if gate_name == "VOLUME_LIQUIDITY":
            knobs.append(RecommendedKnob(
                param_name="STRICT_MIN_RVOL",
                current=0.80,
                suggested=0.65,
                bounded=True,
                tradeoff=f"Admits ~{count} more tickers but lowers liquidity bar",
                expected_effect="Loosens RVOL gate to 0.65 (still above fallback floor of 0.55)",
            ))
        elif gate_name == "RR_RATIO":
            knobs.append(RecommendedKnob(
                param_name="STRICT_MIN_RR",
                current=1.50,
                suggested=1.35,
                bounded=True,
                tradeoff=f"Admits ~{count} more tickers but compresses R:R margin",
                expected_effect="Keeps R:R > fallback floor of 1.2 (bounded, safe)",
            ))
        elif gate_name == "MOMENTUM":
            knobs.append(RecommendedKnob(
                param_name="STRICT_MOMENTUM_MIN",
                current=0.55,
                suggested=0.48,
                bounded=True,
                tradeoff=f"Admits ~{count} more tickers in low-momentum conditions",
                expected_effect="Still above fallback floor of 0.40 (bounded, safe)",
            ))
        elif gate_name == "TRADABILITY":
            knobs.append(RecommendedKnob(
                param_name="STRICT_MIN_ATR_PCT",
                current=1.00,
                suggested=0.80,
                bounded=True,
                tradeoff=f"Admits ~{count} more tickers but R:R narrows on low-ATR ETPs",
                expected_effect="Bounded at 0.60 fallback floor; still above cost threshold",
            ))

    blockers_summary = [f"{g}:{n}" for g, n in sorted(blockers_count.items(), key=lambda x: -x[1])]

    return DroughtPackage(
        drought_flag=bool(drought),
        closest_misses=top_misses,
        recommended_knobs=knobs,
        blockers_summary=blockers_summary,
        tickers_checked=len(gate_reports),
        generated_at=_dt.now(_tz.utc).isoformat(),
    )
