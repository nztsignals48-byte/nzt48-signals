"""
signal_engine/pipeline_runner.py
=================================
Unified pipeline runner — runs the Signal Engine, writes artifacts,
logs signals, builds intel cards, and produces PDF-ready outputs.

Used by:
  - Preview PDF generation (on-demand)
  - Scheduled PDF jobs (07:00, 13:30, 22:00 UK)
  - On-demand test runs

Guarantees:
  1. Signal Logger called after every engine run
  2. Intel cards generated for extended universe
  3. Drought package written if no signals
  4. Session status updated
  5. All artifacts atomic-written before PDF render begins
"""
from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nzt48.pipeline_runner")

ARTIFACTS_ROOT = Path(__file__).parent.parent / "artifacts"
REPORTS_ROOT = Path(__file__).parent.parent / "reports"


@dataclass
class PipelineResult:
    """Complete result from a pipeline run."""
    session:            str
    run_id:             str
    engine_result:      object = None     # EngineResult
    artifacts_written:  bool = False
    pdf_written:        bool = False
    pdf_path:           str = ""
    signals_logged:     int = 0
    intel_count:        int = 0
    error_msg:          str = ""
    generated_at_uk:    str = ""
    is_preview:         bool = False

    @property
    def strict_count(self) -> int:
        return getattr(self.engine_result, "strict_count", 0)

    @property
    def fallback_count(self) -> int:
        return getattr(self.engine_result, "fallback_count", 0)

    @property
    def drought_flag(self) -> bool:
        return bool(getattr(self.engine_result, "drought", None))


@dataclass
class TieredPipelineResult:
    """Result from a tiered universe pipeline run (CORE + PEER + FULL_SCAN)."""
    core_result:      PipelineResult
    peer_plays:       list  = field(default_factory=list)   # PlayScore objects with tier=PEER
    full_scan_cards:  list  = field(default_factory=list)   # IntelCard objects with tier=FULL_SCAN
    universe_sizes:   dict  = field(default_factory=dict)   # {core: N, peers: N, full_scan: N}
    compute_time_ms:  dict  = field(default_factory=dict)   # {core: N, peers: N, full_scan: N}


def run_pipeline(
    session: str,
    period: str = "5d",
    regime: Optional[str] = None,
    is_preview: bool = False,
    generate_intel: bool = True,
    n_plays_min: int = 3,
    n_plays_max: int = 20,
) -> PipelineResult:
    """Run the full signal pipeline: engine + artifacts + signal log + intel.

    Returns PipelineResult with all details. Never raises — captures errors.
    """
    run_id = str(uuid.uuid4())[:8].upper()

    # Live regime detection when caller doesn't specify
    if regime is None:
        try:
            from uk_isa.volatility_regime import get_regime_classifier
            rc = get_regime_classifier()
            regime = rc.current_regime or "NEUTRAL"
            logger.info("[PIPELINE] live regime detected: %s", regime)
        except Exception as re_err:
            regime = "NEUTRAL"
            logger.warning("[PIPELINE] regime detection failed, using NEUTRAL: %s", re_err)

    session_key = f"preview_{session.lower()}" if is_preview else session

    logger.info("=== PIPELINE RUN session=%s run_id=%s preview=%s ===",
                session_key, run_id, is_preview)

    result = PipelineResult(
        session=session_key,
        run_id=run_id,
        is_preview=is_preview,
    )

    try:
        # Set UK time
        try:
            from core.clock import now_uk
            result.generated_at_uk = now_uk().isoformat()
        except Exception:
            result.generated_at_uk = datetime.now(timezone.utc).isoformat()

        # 1. Run Signal Engine
        from signal_engine.engine import SignalEngine
        engine = SignalEngine(use_extended=True)
        engine_result = engine.run(
            session=session_key,
            regime=regime,
            n_plays_min=n_plays_min,
            n_plays_max=n_plays_max,
            period=period,
            write_artifacts=True,
        )
        result.engine_result = engine_result
        result.artifacts_written = True

        logger.info("[PIPELINE] engine: plays=%d strict=%d fallback=%d drought=%s",
                    len(engine_result.plays), engine_result.strict_count,
                    engine_result.fallback_count, engine_result.drought is not None)

        # 2. Log signals
        try:
            from learning.signal_logger import get_signal_logger
            sig_logger = get_signal_logger()
            sig_ids = sig_logger.log_plays(
                engine_result.plays,
                session=session_key,
                regime_tag=regime,
                regime_confidence=engine_result.regime_confidence,
            )
            result.signals_logged = len(sig_ids)
            logger.info("[PIPELINE] logged %d signals", len(sig_ids))
        except Exception as log_err:
            logger.warning("[PIPELINE] signal logging failed: %s", log_err)

        # 3. Build Intel Cards
        if generate_intel:
            try:
                from uk_isa.isa_universe import INTEL_UNIVERSE
                from signal_engine.intel_card import build_intel_cards, write_intel_artifact
                intel_cards = build_intel_cards(INTEL_UNIVERSE, period=period)
                write_intel_artifact(intel_cards, session=session_key)
                result.intel_count = len(intel_cards)
                logger.info("[PIPELINE] intel cards: %d", len(intel_cards))
            except Exception as intel_err:
                logger.warning("[PIPELINE] intel card generation failed: %s", intel_err)

        # 4. Drought loud-fail: send telegram alert if drought
        if engine_result.drought:
            try:
                drought_text = engine_result.drought.to_text()
                blocker_summary = getattr(engine_result, "blocker_summary", [])
                _send_drought_alert(session_key, drought_text, blocker_summary)
            except Exception as drought_err:
                logger.warning("[PIPELINE] drought alert failed: %s", drought_err)

    except Exception as exc:
        result.error_msg = str(exc)[:200]
        logger.error("[PIPELINE] run failed: %s", exc, exc_info=True)

    # 5. Update session status
    try:
        from signal_engine.signal_card import update_session_status
        update_session_status(
            session_key, run_id,
            result.artifacts_written, result.pdf_written,
            result.error_msg,
            pdf_path=result.pdf_path,
            signals_strict_count=result.strict_count,
            signals_fallback_count=result.fallback_count,
            drought_flag=result.drought_flag,
            top_blockers=getattr(result.engine_result, "blocker_summary", [])[:3]
                         if result.engine_result else [],
            generated_at_uk=result.generated_at_uk,
        )
    except Exception as e:
        logger.warning("[PIPELINE] session status update failed: %s", e)

    return result


def run_tiered_pipeline(
    session: str,
    period: str = "5d",
    regime: Optional[str] = None,
    is_preview: bool = False,
    generate_intel: bool = True,
    n_plays_min: int = 3,
    n_plays_max: int = 20,
) -> TieredPipelineResult:
    """Run the tiered universe pipeline: CORE + PEER + FULL_SCAN.

    - CORE scan:      full engine run on core_list only -> writes plays.json (TRADE eligible)
    - PEER scan:      lighter engine run on peer_list  -> writes peers_intel.json (WATCH only)
    - FULL_SCAN:      intel cards only on full_scan_list -> writes full_scan.json (INTEL only)
    - Each play/card receives a tier= tag ("CORE", "PEER", or "FULL_SCAN")
    - Writes universe artifacts via UniverseManager.write_universe_artifacts()

    Returns TieredPipelineResult. Never raises — captures errors.
    """
    if regime is None:
        try:
            from uk_isa.volatility_regime import get_regime_classifier
            rc = get_regime_classifier()
            regime = rc.current_regime or "NEUTRAL"
            logger.info("[TIERED] live regime detected: %s", regime)
        except Exception as re_err:
            regime = "NEUTRAL"
            logger.warning("[TIERED] regime detection failed, using NEUTRAL: %s", re_err)

    compute_times: dict = {}

    # ---------------------------------------------------------------
    # 1. CORE scan — full pipeline, TRADE eligible
    # ---------------------------------------------------------------
    t0 = time.monotonic()
    core_result = run_pipeline(
        session=session,
        period=period,
        regime=regime,
        is_preview=is_preview,
        generate_intel=generate_intel,
        n_plays_min=n_plays_min,
        n_plays_max=n_plays_max,
    )
    compute_times["core"] = round((time.monotonic() - t0) * 1000)

    # Tag all core plays with tier=CORE
    if core_result.engine_result and hasattr(core_result.engine_result, "plays"):
        for play in core_result.engine_result.plays:
            play.tier = "CORE"

    # ---------------------------------------------------------------
    # 2. Load universe tiers (lazy import — UniverseManager may not exist yet)
    # ---------------------------------------------------------------
    peer_list: list[str] = []
    full_scan_list: list[str] = []
    core_list: list[str] = []
    try:
        from uk_isa.universe_manager import get_universe_manager
        um = get_universe_manager()
        core_list = um.core_list
        full_scan_list = um.full_scan_list

        # Auto-select peers if not already selected
        if not um.peer_list:
            try:
                from uk_isa.peer_finder import run_peer_selection
                peers = run_peer_selection(session=session)
                peer_tickers = [p.ticker for p in peers]
                # Update universe manager with selected peers
                um.select_peers({t: 0.80 for t in peer_tickers})
            except Exception as pe:
                logger.warning("[TIERED] PeerFinder failed, using fallback: %s", pe)
                # Fallback: pick from peer_candidates
                candidates = um._peer_candidates[:um.peer_size_target]
                um.select_peers({t: 0.60 for t in candidates})

        peer_list = um.peer_list
    except ImportError:
        logger.warning("[TIERED] UniverseManager not found — falling back to isa_universe lists")
        try:
            from uk_isa.isa_universe import CORE_UNIVERSE, EXTENDED_UNIVERSE, INTEL_UNIVERSE
            core_list = CORE_UNIVERSE
            peer_list = [t for t in EXTENDED_UNIVERSE if t not in CORE_UNIVERSE]
            full_scan_list = INTEL_UNIVERSE
        except ImportError:
            logger.error("[TIERED] Could not load any universe definition")
    except Exception as exc:
        logger.warning("[TIERED] UniverseManager init failed: %s", exc)

    universe_sizes = {
        "core": len(core_list),
        "peers": len(peer_list),
        "full_scan": len(full_scan_list),
    }

    # ---------------------------------------------------------------
    # 3. PEER scan — lighter engine run, WATCH only
    # ---------------------------------------------------------------
    peer_plays: list = []
    t1 = time.monotonic()
    if peer_list:
        try:
            from signal_engine.engine import SignalEngine
            peer_engine = SignalEngine(universe=peer_list)
            peer_engine_result = peer_engine.run(
                session=f"{session}_PEER",
                regime=regime,
                n_plays_min=1,
                n_plays_max=n_plays_max,
                period=period,
                write_artifacts=False,  # Don't overwrite core artifacts
            )
            for play in peer_engine_result.plays:
                play.tier = "PEER"
                play.label = "PEER"
            peer_plays = peer_engine_result.plays

            # Write peers_intel.json artifact
            session_key = f"preview_{session.lower()}" if is_preview else session
            _write_tiered_artifact(
                items=[_play_to_dict(p) for p in peer_plays],
                artifact_name="peers_intel.json",
                session=session_key,
                tier="PEER",
                count=len(peer_plays),
            )
            logger.info("[TIERED] PEER scan: %d plays from %d tickers",
                        len(peer_plays), len(peer_list))
        except Exception as exc:
            logger.warning("[TIERED] PEER scan failed: %s", exc)
    compute_times["peers"] = round((time.monotonic() - t1) * 1000)

    # ---------------------------------------------------------------
    # 4. FULL_SCAN — intel cards only, INTEL classification
    # ---------------------------------------------------------------
    full_scan_cards: list = []
    t2 = time.monotonic()
    if full_scan_list:
        try:
            from signal_engine.intel_card import build_intel_cards, IntelCard
            raw_cards = build_intel_cards(full_scan_list, period=period)
            for card in raw_cards:
                card.label = "FULL_SCAN"
                card.category = "FULL_SCAN"
            full_scan_cards = raw_cards

            # Tag each card dict with tier
            session_key = f"preview_{session.lower()}" if is_preview else session
            _write_tiered_artifact(
                items=[c.to_dict() | {"tier": "FULL_SCAN"} for c in full_scan_cards],
                artifact_name="full_scan.json",
                session=session_key,
                tier="FULL_SCAN",
                count=len(full_scan_cards),
            )
            logger.info("[TIERED] FULL_SCAN: %d intel cards from %d tickers",
                        len(full_scan_cards), len(full_scan_list))
        except Exception as exc:
            logger.warning("[TIERED] FULL_SCAN failed: %s", exc)
    compute_times["full_scan"] = round((time.monotonic() - t2) * 1000)

    # ---------------------------------------------------------------
    # 5. Write universe artifacts (summary of all tiers)
    # ---------------------------------------------------------------
    try:
        from uk_isa.universe_manager import get_universe_manager
        um = get_universe_manager()
        today_str = str(date.today())
        um.write_universe_artifacts(today_str)
    except (ImportError, Exception) as exc:
        logger.warning("[TIERED] universe artifacts write failed: %s", exc)
        # Write a simple summary artifact if UniverseManager not available
        session_key = f"preview_{session.lower()}" if is_preview else session
        _write_tiered_artifact(
            items={
                "core": core_list,
                "peers": peer_list,
                "full_scan": full_scan_list,
                "sizes": universe_sizes,
                "compute_time_ms": compute_times,
            },
            artifact_name="universe.json",
            session=session_key,
            tier="META",
            count=sum(universe_sizes.values()),
        )
    except Exception as exc:
        logger.warning("[TIERED] universe artifacts write failed: %s", exc)

    total_ms = sum(compute_times.values())
    logger.info("[TIERED] complete: core=%d peers=%d full_scan=%d total_ms=%d",
                len(core_result.engine_result.plays) if core_result.engine_result else 0,
                len(peer_plays), len(full_scan_cards), total_ms)

    return TieredPipelineResult(
        core_result=core_result,
        peer_plays=peer_plays,
        full_scan_cards=full_scan_cards,
        universe_sizes=universe_sizes,
        compute_time_ms=compute_times,
    )


def _play_to_dict(play) -> dict:
    """Convert a PlayScore to a JSON-safe dict with tier tag."""
    d = {}
    for attr in ("ticker", "direction", "stars_str", "composite", "label",
                 "entry", "stop", "target1", "target2", "rr_ratio",
                 "atr_pct", "rvol", "setup_type", "factor_group",
                 "strategy_tag", "sizing_hint", "reasons", "track"):
        d[attr] = getattr(play, attr, None)
    d["tier"] = getattr(play, "tier", "UNKNOWN")
    return d


def _write_tiered_artifact(
    items,
    artifact_name: str,
    session: str,
    tier: str,
    count: int,
) -> Optional[Path]:
    """Write a tiered artifact JSON file. Returns path or None on error."""
    import os
    import tempfile

    today = str(date.today())
    session_key = session.lower().replace(" ", "_")
    out_dir = ARTIFACTS_ROOT / today / session_key
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session": session,
        "tier": tier,
        "count": count,
        "items": items if isinstance(items, (list, dict)) else [],
    }

    out_path = out_dir / artifact_name
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(dir=out_dir, suffix=".tmp")
        with os.fdopen(tmp_fd, "w") as f:
            f.write(json.dumps(payload, indent=2, default=str))
            f.flush()
            os.fsync(f.fileno())
        Path(tmp_name).replace(out_path)
        return out_path
    except Exception as exc:
        logger.warning("[TIERED] artifact write failed (%s): %s", artifact_name, exc)
        try:
            os.unlink(tmp_name)
        except Exception:
            pass
    return None


def _send_drought_alert(session: str, drought_text: str, blockers: list) -> None:
    """Send a Signal Drought alert via telegram (best effort)."""
    logger.warning("[DROUGHT ALERT] session=%s\n%s", session, drought_text)
    # Telegram alert is best-effort — fire and forget
    try:
        from delivery.telegram_bot import TelegramDelivery
        tg = TelegramDelivery.__new__(TelegramDelivery)
        if hasattr(tg, "send_alert"):
            import asyncio
            msg = (
                f"SIGNAL DROUGHT: {session}\n\n"
                f"Top blockers:\n"
                + "\n".join(f"  - {b}" for b in blockers[:5])
                + f"\n\n{drought_text[:500]}"
            )
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(tg.send_alert(msg))
                else:
                    loop.run_until_complete(tg.send_alert(msg))
            except Exception:
                pass
    except Exception:
        pass


def generate_preview_pdf(
    session: str,
    pdf_type: str = "momentum",
) -> Optional[str]:
    """Generate a preview PDF for a session. Returns path or None.

    pdf_type: "momentum" (PDF1), "risk" (PDF2), "review" (PDF3)
    """
    today_str = str(date.today())
    out_dir = REPORTS_ROOT / today_str
    out_dir.mkdir(parents=True, exist_ok=True)

    session_upper = session.upper().replace(" ", "_")

    try:
        if pdf_type == "momentum":
            try:
                from delivery.pdf_v2_momentum import MomentumPDFReport
            except ImportError:
                from delivery.pdf_v2_momentum import PDFMomentumReport as MomentumPDFReport
            report = MomentumPDFReport()
            # PDFMomentumReport.generate() does not take session param
            pdf_path = report.generate()
            final_path = out_dir / f"NZT48_MOMENTUM_PREVIEW_{session_upper}.pdf"
            if pdf_path and Path(pdf_path).exists():
                shutil.move(str(pdf_path), str(final_path))
                return str(final_path)

        elif pdf_type == "risk":
            from delivery.pdf_v2_risk import RiskPDFReport
            report = RiskPDFReport()
            pdf_path = report.generate(session=f"preview_{session.lower()}")
            final_path = out_dir / f"NZT48_RISK_PREVIEW_{session_upper}.pdf"
            if pdf_path and Path(pdf_path).exists():
                shutil.move(str(pdf_path), str(final_path))
                return str(final_path)

        elif pdf_type == "review":
            try:
                from delivery.pdf_v2_daily_review import DailyReviewPDFReport
                report = DailyReviewPDFReport()
                pdf_path = report.generate(session=f"preview_{session.lower()}")
                final_path = out_dir / f"NZT48_REVIEW_PREVIEW_{session_upper}.pdf"
                if pdf_path and Path(pdf_path).exists():
                    shutil.move(str(pdf_path), str(final_path))
                    return str(final_path)
            except ImportError:
                logger.warning("[PREVIEW PDF] review module not available")

    except Exception as exc:
        logger.error("[PREVIEW PDF] %s generation failed: %s", pdf_type, exc)

    return None


def generate_scheduled_pdf(
    session: str,
    pdf_type: str = "momentum",
) -> Optional[str]:
    """Generate a scheduled PDF. Returns path or None.

    Unlike preview, this writes to the final (non-preview) path.
    """
    today_str = str(date.today())
    out_dir = REPORTS_ROOT / today_str
    out_dir.mkdir(parents=True, exist_ok=True)

    session_upper = session.upper().replace(" ", "_")

    try:
        if pdf_type == "momentum":
            try:
                from delivery.pdf_v2_momentum import MomentumPDFReport
            except ImportError:
                from delivery.pdf_v2_momentum import PDFMomentumReport as MomentumPDFReport
            report = MomentumPDFReport()
            pdf_path = report.generate()
            final_path = out_dir / f"NZT48_MOMENTUM_{session_upper}.pdf"
            if pdf_path and Path(pdf_path).exists():
                shutil.move(str(pdf_path), str(final_path))
                return str(final_path)

        elif pdf_type == "risk":
            from delivery.pdf_v2_risk import RiskPDFReport
            report = RiskPDFReport()
            pdf_path = report.generate(session=session)
            final_path = out_dir / f"NZT48_RISK_{session_upper}.pdf"
            if pdf_path and Path(pdf_path).exists():
                shutil.move(str(pdf_path), str(final_path))
                return str(final_path)

        elif pdf_type == "review":
            try:
                from delivery.pdf_v2_daily_review import DailyReviewPDFReport
                report = DailyReviewPDFReport()
                pdf_path = report.generate(session=session)
                final_path = out_dir / f"NZT48_REVIEW_{session_upper}.pdf"
                if pdf_path and Path(pdf_path).exists():
                    shutil.move(str(pdf_path), str(final_path))
                    return str(final_path)
            except ImportError:
                logger.warning("[SCHEDULED PDF] review module not available")

    except Exception as exc:
        logger.error("[SCHEDULED PDF] %s generation failed: %s", pdf_type, exc)

    return None
