"""
command_center/state.py
========================
Shared in-memory state for the NZT-48 Command Center v8.0.

All panels read from this singleton. The realtime tick loop writes to it.
Thread-safe via asyncio.Lock (all writes happen inside the async tick).

v3.0 additions:
- StrategiesPanel: active strategies, overlays, sizing_mode from RouterResult
- SessionStatusPanel: PASS/FAIL per PDF job from session_status.json
- halt_new_signals: flag checked by tick loop
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# Optional imports — these may not exist in all deployment configurations.
# state.py works fine with Any/object types; the important logic is in
# get_snapshot() which uses getattr() for safety.
try:
    from signal_engine.engine import EngineResult
except ImportError:
    EngineResult = object  # type: ignore[assignment,misc]

try:
    from signal_engine.scoring import PlayScore, SignalDroughtReport
except ImportError:
    PlayScore = object          # type: ignore[assignment,misc]
    SignalDroughtReport = object  # type: ignore[assignment,misc]

try:
    from signal_engine.state_machine import SignalTape
except ImportError:
    class SignalTape:           # type: ignore[no-redef]
        """Stub when state_machine not available."""
        def to_lines(self, n: int = 20) -> list:
            return []


@dataclass
class MarketOverview:
    regime:          str   = "UNKNOWN"
    regime_confidence: float = 0.0
    breadth_score:   float = 0.0    # proxy: % of universe above EMA20
    vix_proxy:       float = 0.0
    spx_move_pct:    float = 0.0
    ndx_move_pct:    float = 0.0
    session_active:  bool  = False
    session_name:    str   = "OFF-HOURS"
    updated_at:      datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class DataHealthPanel:
    badge:       str              = "UNKNOWN"   # GREEN / AMBER / RED
    pass_count:  int              = 0
    warn_count:  int              = 0
    fail_count:  int              = 0
    failed_tickers: list[str]     = field(default_factory=list)
    warnings:    list[str]        = field(default_factory=list)
    updated_at:  datetime         = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class GateFunnelPanel:
    tracked:          int = 0
    data_valid:       int = 0
    passed_all_gates: int = 0
    signals_strict:   int = 0
    signals_fallback: int = 0
    total_signals:    int = 0
    top_blockers:     list[str] = field(default_factory=list)
    closest_misses:   list[dict] = field(default_factory=list)   # v3.0
    updated_at:       datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PortfolioRiskPanel:
    factor_exposure:  dict[str, int]   = field(default_factory=dict)
    max_factor_group: str              = ""
    max_factor_count: int              = 0
    decay_risk:       str              = "LOW"
    liquidity_risk:   str              = "LOW"
    warnings:         list[str]        = field(default_factory=list)
    vol_target_state: str              = "NORMAL"    # v3.0: NORMAL/REDUCED/DEFENSIVE
    halt_active:      bool             = False        # v3.0
    updated_at:       datetime         = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# v3.0 NEW PANELS
# ---------------------------------------------------------------------------

@dataclass
class StrategySpec:
    """Serialisable summary of one strategy from RouterResult."""
    tag:             str
    weight:          float
    active:          bool
    why_active:      list[str]
    inactive_reason: str
    category:        str
    constraints:     dict


@dataclass
class StrategiesPanel:
    """Active strategies, overlays and sizing mode from latest RouterResult."""
    regime_tag:         str              = "UNKNOWN"
    regime_confidence:  float            = 0.0
    time_of_day_window: str              = "OFF_HOURS"
    active_strategies:  list[StrategySpec] = field(default_factory=list)
    inactive_strategies: list[StrategySpec] = field(default_factory=list)
    overlay_tags:       list[str]        = field(default_factory=list)
    overlay_warnings:   list[str]        = field(default_factory=list)
    sizing_mode:        str              = "NORMAL"
    max_factor_cap:     int              = 3
    kill_switch:        bool             = False
    score_boost:        float            = 0.0
    updated_at:         datetime         = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class SessionJobStatus:
    session:           str
    run_id:            str   = ""
    timestamp:         str   = ""
    artifacts_written: bool  = False
    pdf_written:       bool  = False
    error_msg:         str   = ""
    status:            str   = "PENDING"    # PASS / FAIL / PENDING


@dataclass
class SessionStatusPanel:
    """PASS/FAIL per PDF job — read from data/session_status.json."""
    jobs:       list[SessionJobStatus] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_json(cls, data: dict) -> "SessionStatusPanel":
        jobs = []
        for sess, info in data.items():
            jobs.append(SessionJobStatus(
                session=sess,
                run_id=info.get("run_id", ""),
                timestamp=info.get("timestamp", ""),
                artifacts_written=info.get("artifacts_written", False),
                pdf_written=info.get("pdf_written", False),
                error_msg=info.get("error_msg", ""),
                status=info.get("status", "PENDING"),
            ))
        return cls(jobs=jobs)


# ---------------------------------------------------------------------------
# v4.0 New Panels
# ---------------------------------------------------------------------------

@dataclass
class RiskOfficerPanel:
    """Latest risk officer decisions — one per engine run."""
    session:        str           = ""
    veto_count:     int           = 0
    downsize_count: int           = 0
    approve_count:  int           = 0
    decisions:      list[dict]    = field(default_factory=list)
    updated_at:     datetime      = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "session":        self.session,
            "veto_count":     self.veto_count,
            "downsize_count": self.downsize_count,
            "approve_count":  self.approve_count,
            "decisions":      self.decisions[:50],
            "updated_at":     self.updated_at.isoformat(),
        }


@dataclass
class AllocationPanel:
    """Capital allocation weights from RouterResult."""
    weights:     dict  = field(default_factory=dict)   # strategy_tag -> float
    sizing_mode: str   = "NORMAL"
    regime_tag:  str   = ""
    updated_at:  datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "weights":     self.weights,
            "sizing_mode": self.sizing_mode,
            "regime_tag":  self.regime_tag,
            "updated_at":  self.updated_at.isoformat(),
        }


@dataclass
class DroughtCockpitPanel:
    """Actionable drought diagnostics for EXPLAINABILITY tab."""
    drought_flag:      bool       = False
    closest_misses:    list[dict] = field(default_factory=list)
    recommended_knobs: list[dict] = field(default_factory=list)
    blockers_summary:  list[str]  = field(default_factory=list)
    tickers_checked:   int        = 0
    updated_at:        datetime   = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "drought_flag":      self.drought_flag,
            "closest_misses":    self.closest_misses,
            "recommended_knobs": self.recommended_knobs,
            "blockers_summary":  self.blockers_summary,
            "tickers_checked":   self.tickers_checked,
            "updated_at":        self.updated_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Main state container
# ---------------------------------------------------------------------------

@dataclass
class CommandCenterState:
    """Singleton state container. Updated by tick loop, read by UI."""

    # Panels
    market:          MarketOverview     = field(default_factory=MarketOverview)
    data_health:     DataHealthPanel    = field(default_factory=DataHealthPanel)
    gate_funnel:     GateFunnelPanel    = field(default_factory=GateFunnelPanel)
    portfolio:       PortfolioRiskPanel = field(default_factory=PortfolioRiskPanel)
    strategies:      StrategiesPanel    = field(default_factory=StrategiesPanel)     # v3.0
    session_status:  SessionStatusPanel = field(default_factory=SessionStatusPanel)  # v3.0
    # v4.0 new panels
    risk_officer:    "RiskOfficerPanel"    = field(default_factory=lambda: RiskOfficerPanel())
    allocation:      "AllocationPanel"     = field(default_factory=lambda: AllocationPanel())
    drought_cockpit: "DroughtCockpitPanel" = field(default_factory=lambda: DroughtCockpitPanel())

    # Signal data
    top_plays:   list              = field(default_factory=list)
    tape:        object            = field(default_factory=SignalTape)
    drought:     Optional[object]  = None

    # Metadata
    last_engine_result: Optional[EngineResult] = None
    tick_count:  int      = 0
    last_tick:   Optional[datetime] = None
    started_at:  datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    run_id:      str      = ""

    # Guardrails v3.0
    halt_new_signals: bool = False     # POST /api/halt toggles this

    # Thread safety
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def update_from_engine(self, result: EngineResult) -> None:
        async with self._lock:
            self.last_engine_result = result
            self.top_plays = result.plays
            self.drought   = result.drought
            self.tick_count += 1
            self.last_tick  = datetime.now(timezone.utc)

            # Gate funnel
            gf = result.gate_funnel
            self.gate_funnel = GateFunnelPanel(
                tracked=gf["tracked"],
                data_valid=gf["data_valid"],
                passed_all_gates=gf["passed_all_gates"],
                signals_strict=gf["signals_strict"],
                signals_fallback=gf["signals_fallback"],
                total_signals=gf["total_signals"],
                top_blockers=result.blocker_summary,
            )

            # Data health
            hs = result.health_summary
            if hs:
                badge_map = {"GREEN": "GREEN", "AMBER": "AMBER", "RED": "RED"}
                self.data_health = DataHealthPanel(
                    badge=badge_map.get(getattr(hs, "status", "AMBER"), "AMBER"),
                    pass_count=getattr(hs, "pass_count", 0),
                    warn_count=getattr(hs, "warn_count", 0),
                    fail_count=getattr(hs, "fail_count", 0),
                    failed_tickers=list(result.excluded.keys()),
                    warnings=getattr(hs, "warnings", [])[:5],
                )

            # Portfolio risk
            fg_counts: dict[str, int] = {}
            for ps in result.plays:
                fg_counts[ps.factor_group] = fg_counts.get(ps.factor_group, 0) + 1
            top_fg  = max(fg_counts, key=fg_counts.get, default="")
            top_cnt = fg_counts.get(top_fg, 0)
            warns   = []
            if top_cnt >= 3:
                warns.append(f"Factor cluster '{top_fg}' has {top_cnt} signals -- concentration risk")

            # Vol target state
            vt_state = "NORMAL"
            if self.strategies.kill_switch:
                vt_state = "DEFENSIVE"
            elif self.strategies.sizing_mode in ("REDUCED", "HALF"):
                vt_state = "REDUCED"

            self.portfolio = PortfolioRiskPanel(
                factor_exposure=fg_counts,
                max_factor_group=top_fg,
                max_factor_count=top_cnt,
                warnings=warns,
                vol_target_state=vt_state,
                halt_active=self.halt_new_signals,
            )

    def update_strategies(self, router_result) -> None:
        """Update StrategiesPanel from a RouterResult object (non-async)."""
        if router_result is None:
            return
        active = []
        inactive = []
        for s in getattr(router_result, "active_strategies", []):
            spec = StrategySpec(
                tag=getattr(s, "tag", ""),
                weight=getattr(s, "weight", 0.0),
                active=getattr(s, "active", False),
                why_active=list(getattr(s, "why_active", [])),
                inactive_reason=getattr(s, "inactive_reason", ""),
                category=getattr(s, "category", ""),
                constraints=dict(getattr(s, "constraints", {})),
            )
            if spec.active:
                active.append(spec)
            else:
                inactive.append(spec)

        self.strategies = StrategiesPanel(
            regime_tag=getattr(router_result, "regime_tag", "UNKNOWN"),
            regime_confidence=getattr(router_result, "regime_confidence", 0.0),
            time_of_day_window=getattr(router_result, "time_of_day_window", ""),
            active_strategies=active,
            inactive_strategies=inactive,
            overlay_tags=list(getattr(router_result, "overlay_tags", [])),
            overlay_warnings=list(getattr(router_result, "overlay_warnings", [])),
            sizing_mode=getattr(router_result, "sizing_mode", "NORMAL"),
            max_factor_cap=getattr(router_result, "max_factor_cap", 3),
            kill_switch=getattr(router_result, "kill_switch", False),
            score_boost=getattr(router_result, "score_boost", 0.0),
        )

    def refresh_session_status(self) -> None:
        """Reload data/session_status.json into SessionStatusPanel."""
        try:
            import json, os
            p = os.path.join("data", "session_status.json")
            if os.path.exists(p):
                with open(p) as f:
                    data = json.load(f)
                if data:
                    self.session_status = SessionStatusPanel.from_json(data)
        except Exception:
            pass

    # ── v4.0 update methods ──────────────────────────────────────────────────

    def update_risk_officer(self, officer_report) -> None:
        """Update RiskOfficerPanel from a RiskOfficerReport."""
        try:
            self.risk_officer = RiskOfficerPanel(
                session=officer_report.session,
                veto_count=officer_report.veto_count,
                downsize_count=officer_report.downsize_count,
                approve_count=officer_report.approve_count,
                decisions=officer_report.decisions[:50],
            )
        except Exception:
            pass

    def update_allocation(self, router_result) -> None:
        """Update AllocationPanel from RouterResult."""
        try:
            self.allocation = AllocationPanel(
                weights=getattr(router_result, "allocation_weights", {}),
                sizing_mode=getattr(router_result, "sizing_mode", "NORMAL"),
                regime_tag=getattr(router_result, "regime_tag", ""),
            )
        except Exception:
            pass

    def update_drought_cockpit(self, drought_package) -> None:
        """Update DroughtCockpitPanel from DroughtPackage."""
        try:
            if drought_package is None:
                return
            self.drought_cockpit = DroughtCockpitPanel(
                drought_flag=drought_package.drought_flag,
                closest_misses=[m.to_dict() if hasattr(m, "to_dict") else m
                                for m in drought_package.closest_misses],
                recommended_knobs=[k.to_dict() if hasattr(k, "to_dict") else k
                                   for k in drought_package.recommended_knobs],
                blockers_summary=drought_package.blockers_summary,
                tickers_checked=drought_package.tickers_checked,
            )
        except Exception:
            pass

    def update_from_snapshot(self, snapshot: dict) -> None:
        """Restore dashboard-visible state from a snapshot dict (as returned by get_snapshot()).

        Resilient: uses .get() with defaults everywhere and wraps in try/except
        so a partial or stale snapshot never crashes the process.
        """
        try:
            # ── Metadata ─────────────────────────────────────────────────
            self.tick_count = snapshot.get("tick", self.tick_count)
            lt = snapshot.get("last_tick")
            if lt is not None:
                try:
                    self.last_tick = datetime.fromisoformat(lt)
                except (ValueError, TypeError):
                    pass
            self.run_id = snapshot.get("run_id", self.run_id)
            self.halt_new_signals = snapshot.get("halt", self.halt_new_signals)

            # ── Market ───────────────────────────────────────────────────
            mkt = snapshot.get("market")
            if isinstance(mkt, dict):
                self.market.regime = mkt.get("regime", self.market.regime)
                self.market.session_name = mkt.get("session", self.market.session_name)
                self.market.session_active = mkt.get("session_active", self.market.session_active)
                self.market.breadth_score = mkt.get("breadth_score", self.market.breadth_score)

            # ── Data Health ──────────────────────────────────────────────
            dh = snapshot.get("data_health")
            if isinstance(dh, dict):
                self.data_health.badge = dh.get("badge", self.data_health.badge)
                self.data_health.pass_count = dh.get("pass", self.data_health.pass_count)
                self.data_health.warn_count = dh.get("warn", self.data_health.warn_count)
                self.data_health.fail_count = dh.get("fail", self.data_health.fail_count)
                self.data_health.failed_tickers = dh.get("failed_tickers", self.data_health.failed_tickers)

            # ── Gate Funnel ──────────────────────────────────────────────
            gf = snapshot.get("gate_funnel")
            if isinstance(gf, dict):
                self.gate_funnel.tracked = gf.get("tracked", self.gate_funnel.tracked)
                self.gate_funnel.data_valid = gf.get("data_valid", self.gate_funnel.data_valid)
                self.gate_funnel.signals_strict = gf.get("signals_strict", self.gate_funnel.signals_strict)
                self.gate_funnel.signals_fallback = gf.get("signals_fallback", self.gate_funnel.signals_fallback)
                self.gate_funnel.total_signals = gf.get("total", self.gate_funnel.total_signals)
                self.gate_funnel.top_blockers = gf.get("blockers", self.gate_funnel.top_blockers)
                self.gate_funnel.closest_misses = gf.get("closest_misses", self.gate_funnel.closest_misses)

            # ── Strategies ───────────────────────────────────────────────
            st = snapshot.get("strategies")
            if isinstance(st, dict):
                self.strategies.regime_tag = st.get("regime_tag", self.strategies.regime_tag)
                self.strategies.time_of_day_window = st.get("time_of_day", self.strategies.time_of_day_window)
                self.strategies.sizing_mode = st.get("sizing_mode", self.strategies.sizing_mode)
                self.strategies.kill_switch = st.get("kill_switch", self.strategies.kill_switch)
                self.strategies.score_boost = st.get("score_boost", self.strategies.score_boost)
                self.strategies.overlay_tags = st.get("overlay_tags", self.strategies.overlay_tags)
                self.strategies.overlay_warnings = st.get("overlay_warnings", self.strategies.overlay_warnings)

                # Rebuild StrategySpec lists from plain dicts
                def _to_spec(d: dict) -> StrategySpec:
                    return StrategySpec(
                        tag=d.get("tag", ""),
                        weight=d.get("weight", 0.0),
                        active=d.get("active", False),
                        why_active=d.get("why_active", []),
                        inactive_reason=d.get("inactive_reason", ""),
                        category=d.get("category", ""),
                        constraints=d.get("constraints", {}),
                    )

                raw_active = st.get("active")
                if isinstance(raw_active, list):
                    self.strategies.active_strategies = [_to_spec(s) for s in raw_active if isinstance(s, dict)]
                raw_inactive = st.get("inactive")
                if isinstance(raw_inactive, list):
                    self.strategies.inactive_strategies = [_to_spec(s) for s in raw_inactive if isinstance(s, dict)]

            # ── Portfolio ────────────────────────────────────────────────
            pf = snapshot.get("portfolio")
            if isinstance(pf, dict):
                self.portfolio.factor_exposure = pf.get("factor_exposure", self.portfolio.factor_exposure)
                self.portfolio.warnings = pf.get("warnings", self.portfolio.warnings)
                self.portfolio.vol_target_state = pf.get("vol_target_state", self.portfolio.vol_target_state)
                self.portfolio.halt_active = pf.get("halt_active", self.portfolio.halt_active)

            # ── Top Plays (store raw list of dicts) ──────────────────────
            raw_plays = snapshot.get("top_plays")
            if isinstance(raw_plays, list):
                self.top_plays = raw_plays

            # ── Drought ──────────────────────────────────────────────────
            drought_text = snapshot.get("drought")
            if drought_text is not None:
                # Store raw text; dashboard can render it directly
                self.drought = drought_text

            # ── Risk Officer (v4.0) ──────────────────────────────────────
            ro = snapshot.get("risk_officer")
            if isinstance(ro, dict):
                self.risk_officer = RiskOfficerPanel(
                    session=ro.get("session", ""),
                    veto_count=ro.get("veto_count", 0),
                    downsize_count=ro.get("downsize_count", 0),
                    approve_count=ro.get("approve_count", 0),
                    decisions=ro.get("decisions", []),
                )

            # ── Allocation (v4.0) ────────────────────────────────────────
            al = snapshot.get("allocation")
            if isinstance(al, dict):
                self.allocation = AllocationPanel(
                    weights=al.get("weights", {}),
                    sizing_mode=al.get("sizing_mode", "NORMAL"),
                    regime_tag=al.get("regime_tag", ""),
                )

            # ── Drought Cockpit (v4.0) ───────────────────────────────────
            dc = snapshot.get("drought_cockpit")
            if isinstance(dc, dict):
                self.drought_cockpit = DroughtCockpitPanel(
                    drought_flag=dc.get("drought_flag", False),
                    closest_misses=dc.get("closest_misses", []),
                    recommended_knobs=dc.get("recommended_knobs", []),
                    blockers_summary=dc.get("blockers_summary", []),
                    tickers_checked=dc.get("tickers_checked", 0),
                )

            # ── Session Status ───────────────────────────────────────────
            ss = snapshot.get("session_status")
            if isinstance(ss, dict):
                raw_jobs = ss.get("jobs")
                if isinstance(raw_jobs, list):
                    jobs = []
                    for j in raw_jobs:
                        if isinstance(j, dict):
                            jobs.append(SessionJobStatus(
                                session=j.get("session", ""),
                                status=j.get("status", "PENDING"),
                                timestamp=j.get("timestamp", ""),
                                artifacts_written=j.get("artifacts_written", False),
                                pdf_written=j.get("pdf_written", False),
                                error_msg=j.get("error_msg", ""),
                            ))
                    self.session_status = SessionStatusPanel(jobs=jobs)

        except Exception:
            # Never crash the process — a partial update is acceptable
            pass

    def get_snapshot(self) -> dict:
        """Serialisable snapshot for REST API / websocket push."""
        def ps_dict(ps: PlayScore) -> dict:
            return {
                "ticker":        ps.ticker,
                "direction":     ps.direction,
                "stars":         ps.stars_str,
                "score":         ps.composite,
                "strategy_score": getattr(ps, "strategy_weighted_score", ps.composite),
                "label":         ps.label,
                "entry":         ps.entry,
                "stop":          ps.stop,
                "target1":       ps.target1,
                "target2":       ps.target2,
                "rr":            ps.rr_ratio,
                "atr_pct":       ps.atr_pct,
                "rvol":          ps.rvol,
                "setup_type":    ps.setup_type,
                "factor_group":  ps.factor_group,
                "track":         getattr(ps, "track", "INTRADAY_SWING"),
                "strategy_tag":  getattr(ps, "strategy_tag", ""),
                "sizing_hint":   getattr(ps, "sizing_hint", "M"),
                "reasons":       ps.reasons,
            }

        def strat_dict(s: StrategySpec) -> dict:
            return {
                "tag":             s.tag,
                "weight":          round(s.weight, 3),
                "active":          s.active,
                "why_active":      s.why_active,
                "inactive_reason": s.inactive_reason,
                "category":        s.category,
            }

        def job_dict(j: SessionJobStatus) -> dict:
            return {
                "session":     j.session,
                "status":      j.status,
                "timestamp":   j.timestamp,
                "artifacts_written": j.artifacts_written,
                "pdf_written": j.pdf_written,
                "error_msg":   j.error_msg,
            }

        return {
            "tick":       self.tick_count,
            "last_tick":  self.last_tick.isoformat() if self.last_tick else None,
            "run_id":     self.run_id,
            "halt":       self.halt_new_signals,
            "market": {
                "regime":         self.market.regime,
                "session":        self.market.session_name,
                "session_active": self.market.session_active,
                "breadth_score":  self.market.breadth_score,
            },
            "data_health": {
                "badge":          self.data_health.badge,
                "pass":           self.data_health.pass_count,
                "warn":           self.data_health.warn_count,
                "fail":           self.data_health.fail_count,
                "failed_tickers": self.data_health.failed_tickers,
            },
            "gate_funnel": {
                "tracked":          self.gate_funnel.tracked,
                "data_valid":       self.gate_funnel.data_valid,
                "signals_strict":   self.gate_funnel.signals_strict,
                "signals_fallback": self.gate_funnel.signals_fallback,
                "total":            self.gate_funnel.total_signals,
                "blockers":         self.gate_funnel.top_blockers,
                "closest_misses":   self.gate_funnel.closest_misses,
            },
            "portfolio": {
                "factor_exposure": self.portfolio.factor_exposure,
                "warnings":        self.portfolio.warnings,
                "vol_target_state": self.portfolio.vol_target_state,
                "halt_active":     self.portfolio.halt_active,
            },
            "strategies": {
                "regime_tag":       self.strategies.regime_tag,
                "time_of_day":      self.strategies.time_of_day_window,
                "active":           [strat_dict(s) for s in self.strategies.active_strategies],
                "inactive":         [strat_dict(s) for s in self.strategies.inactive_strategies],
                "overlay_tags":     self.strategies.overlay_tags,
                "overlay_warnings": self.strategies.overlay_warnings,
                "sizing_mode":      self.strategies.sizing_mode,
                "kill_switch":      self.strategies.kill_switch,
                "score_boost":      round(self.strategies.score_boost, 3),
            },
            "session_status": {
                "jobs": [job_dict(j) for j in self.session_status.jobs],
            },
            # v4.0 new panels
            "risk_officer":    self.risk_officer.to_dict(),
            "allocation":      self.allocation.to_dict(),
            "drought_cockpit": self.drought_cockpit.to_dict(),
            "top_plays":  [ps_dict(p) for p in self.top_plays[:15]],
            "tape":       self.tape.to_lines(20) if hasattr(self.tape, "to_lines") else [],
            "drought":    self.drought.to_text() if (self.drought and hasattr(self.drought, "to_text")) else None,
        }


# Module-level singleton
_state: Optional[CommandCenterState] = None


def get_state() -> CommandCenterState:
    global _state
    if _state is None:
        import uuid
        _state = CommandCenterState(run_id=str(uuid.uuid4())[:8].upper())
    return _state
