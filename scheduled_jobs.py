"""
scheduled_jobs.py
=================
Scheduled PDF generation + Telegram delivery for NZT-48 Paper Launch.

Three scheduled windows (UK time):
  PRE_LSE:   07:00 — Momentum & Opportunity PDF
  PRE_NYSE:  13:30 — Momentum & Opportunity PDF (updated)
  EOD:       22:00 — Daily Review PDF + Risk PDF

Each job:
  1. Runs the tiered pipeline (CORE + PEER + FULL_SCAN)
  2. Writes all artifacts (system_state, reliability, quality, drought, etc.)
  3. Generates the appropriate PDF(s)
  4. Sends PDF(s) to Telegram with rich caption
  5. Writes telegram_delivery.json artifact

Can also be run on-demand for preview generation.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone, date
from core.clock import now_utc
from pathlib import Path
from typing import Optional

try:
    from delivery.pdf_overnight_risk import OvernightRiskPDF
except ImportError:
    OvernightRiskPDF = None

try:
    from delivery.pdf_mid_session import MidSessionRiskPDF
except ImportError:
    MidSessionRiskPDF = None

try:
    from delivery.pdf_master_spec import MasterSpecPDF
except ImportError:
    MasterSpecPDF = None

logger = logging.getLogger("nzt48.scheduled_jobs")

ARTIFACTS_ROOT = Path(__file__).parent / "artifacts"
REPORTS_ROOT = Path(__file__).parent / "reports"


def run_scheduled_session(
    session: str,
    regime: str = "NEUTRAL",
    is_preview: bool = False,
    send_telegram: bool = True,
) -> dict:
    """Run a complete scheduled session: pipeline + PDFs + Telegram.

    Args:
        session: PRE_LSE, PRE_NYSE, or EOD_INSTITUTIONAL
        regime: Current market regime
        is_preview: If True, label outputs as PREVIEW
        send_telegram: If True, attempt Telegram delivery

    Returns:
        Summary dict with all results
    """
    logger.info("=== SCHEDULED SESSION: %s (preview=%s) ===", session, is_preview)
    t0 = time.monotonic()
    summary: dict = {
        "session": session,
        "is_preview": is_preview,
        "regime": regime,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    # 1. Run tiered pipeline
    try:
        from signal_engine.pipeline_runner import run_tiered_pipeline
        tiered = run_tiered_pipeline(
            session=session,
            period="5d",
            regime=regime,
            is_preview=is_preview,
            generate_intel=True,
            n_plays_min=5,
            n_plays_max=20,
        )
        core = tiered.core_result
        summary["pipeline"] = {
            "core_plays": len(core.engine_result.plays) if core.engine_result else 0,
            "strict_count": core.strict_count,
            "fallback_count": core.fallback_count,
            "peer_plays": len(tiered.peer_plays),
            "full_scan_cards": len(tiered.full_scan_cards),
            "drought": core.drought_flag,
            "signals_logged": core.signals_logged,
            "intel_count": core.intel_count,
            "error": core.error_msg or None,
            "compute_time_ms": tiered.compute_time_ms,
        }
        logger.info("[SCHEDULED] pipeline: core=%d peers=%d full_scan=%d",
                     summary["pipeline"]["core_plays"],
                     summary["pipeline"]["peer_plays"],
                     summary["pipeline"]["full_scan_cards"])
    except Exception as exc:
        summary["pipeline"] = {"error": str(exc)[:200]}
        logger.error("[SCHEDULED] pipeline failed: %s", exc, exc_info=True)

    # 2. Run watchdog + quality artifacts
    try:
        from system_watchdog import (
            SystemWatchdog, compute_data_reliability, run_quality_gate,
            write_watchdog_artifacts,
        )
        watchdog = SystemWatchdog()
        watchdog.record_tick()
        watchdog.record_data_fetch()

        state_report = watchdog.check_state(
            tick_count=summary["pipeline"].get("core_plays", 0),
        )

        # Get engine result for reliability computation
        engine_result = None
        features_map = {}
        if "pipeline" in summary and summary["pipeline"].get("core_plays", 0) > 0:
            try:
                engine_result = core.engine_result
                # features_map not easily accessible here — use health_summary
                health_summary = getattr(engine_result, "health_summary", None)
                reliability = compute_data_reliability(health_summary, features_map)
            except Exception:
                reliability = compute_data_reliability(None, {})
        else:
            reliability = compute_data_reliability(None, {})

        # Quality gate on plays
        plays = []
        if core and core.engine_result:
            plays = core.engine_result.plays or []
        quality = run_quality_gate(plays, regime, features_map)

        # Write artifacts
        session_key = f"preview_{session.lower()}" if is_preview else session
        artifact_paths = write_watchdog_artifacts(
            session=session_key,
            state_report=state_report,
            reliability_report=reliability,
            quality_report=quality,
        )
        summary["watchdog"] = {
            "system_state": state_report.state,
            "data_reliability_score": reliability.score,
            "quality_passed": quality.passed,
            "artifacts_written": list(artifact_paths.keys()),
        }
    except Exception as exc:
        summary["watchdog"] = {"error": str(exc)[:200]}
        logger.error("[SCHEDULED] watchdog failed: %s", exc, exc_info=True)

    # 3. Generate PDFs
    pdf_paths: list[str] = []
    session_upper = session.upper().replace(" ", "_")

    try:
        from signal_engine.pipeline_runner import generate_preview_pdf, generate_scheduled_pdf
        gen_fn = generate_preview_pdf if is_preview else generate_scheduled_pdf

        # Determine which PDFs to generate based on session
        pdf_types = _get_pdf_types_for_session(session)

        for pdf_type in pdf_types:
            try:
                pdf_path = gen_fn(session=session, pdf_type=pdf_type)
                if pdf_path:
                    pdf_paths.append(pdf_path)
                    logger.info("[SCHEDULED] %s PDF generated: %s", pdf_type, pdf_path)
                else:
                    logger.warning("[SCHEDULED] %s PDF returned None", pdf_type)
            except Exception as pdf_exc:
                logger.error("[SCHEDULED] %s PDF failed: %s", pdf_type, pdf_exc)

        summary["pdfs"] = {
            "generated": len(pdf_paths),
            "paths": pdf_paths,
            "types": pdf_types,
        }
    except Exception as exc:
        summary["pdfs"] = {"error": str(exc)[:200]}
        logger.error("[SCHEDULED] PDF generation failed: %s", exc, exc_info=True)

    # 4. Telegram delivery
    telegram_results: list[dict] = []
    if send_telegram and pdf_paths:
        try:
            telegram_results = _send_pdfs_to_telegram(
                pdf_paths=pdf_paths,
                session=session,
                summary=summary,
                is_preview=is_preview,
            )
        except Exception as exc:
            logger.error("[SCHEDULED] Telegram delivery failed: %s", exc)
            telegram_results = [{"error": str(exc)[:200]}]

    summary["telegram"] = {
        "attempted": len(pdf_paths),
        "results": telegram_results,
    }

    # 5. Write telegram_delivery.json
    try:
        _write_delivery_artifact(session, is_preview, summary, telegram_results)
    except Exception as exc:
        logger.warning("[SCHEDULED] delivery artifact failed: %s", exc)

    elapsed_ms = round((time.monotonic() - t0) * 1000)
    summary["elapsed_ms"] = elapsed_ms
    summary["completed_at"] = datetime.now(timezone.utc).isoformat()

    logger.info("[SCHEDULED] session=%s complete in %dms pdfs=%d telegram=%d",
                session, elapsed_ms, len(pdf_paths), len(telegram_results))
    return summary


def run_overnight_risk_session():
    """06:30 UK — Overnight Risk & Macro Tape."""
    logger.info("=== OVERNIGHT RISK SESSION START ===")
    try:
        if OvernightRiskPDF is None:
            logger.warning("OvernightRiskPDF not available, skipping")
            return
        pdf_gen = OvernightRiskPDF()
        pdf_path = pdf_gen.generate(session="OVERNIGHT")
        if pdf_path:
            logger.info(f"Overnight Risk PDF generated: {pdf_path}")
            # Send to Telegram
            _send_pdf_to_telegram(pdf_path, "OVERNIGHT RISK & MACRO TAPE")
        else:
            logger.warning("Overnight Risk PDF generation returned None")
    except Exception as e:
        logger.error(f"Overnight risk session failed: {e}", exc_info=True)
    logger.info("=== OVERNIGHT RISK SESSION END ===")


def run_mid_session_risk():
    """16:40 UK — Mid-Session Risk Check."""
    logger.info("=== MID-SESSION RISK CHECK START ===")
    try:
        if MidSessionRiskPDF is None:
            logger.warning("MidSessionRiskPDF not available, skipping")
            return
        pdf_gen = MidSessionRiskPDF()
        pdf_path = pdf_gen.generate(session="MID_SESSION")
        if pdf_path:
            logger.info(f"Mid-Session Risk PDF generated: {pdf_path}")
            _send_pdf_to_telegram(pdf_path, "MID-SESSION RISK CHECK")
        else:
            logger.warning("Mid-Session Risk PDF generation returned None")
    except Exception as e:
        logger.error(f"Mid-session risk failed: {e}", exc_info=True)
    logger.info("=== MID-SESSION RISK CHECK END ===")


def run_master_spec():
    """00:00 UK — Master Spec of the Day."""
    logger.info("=== MASTER SPEC SESSION START ===")
    try:
        if MasterSpecPDF is None:
            logger.warning("MasterSpecPDF not available, skipping")
            return
        pdf_gen = MasterSpecPDF()
        pdf_path = pdf_gen.generate(session="MASTER_SPEC")
        if pdf_path:
            logger.info(f"Master Spec PDF generated: {pdf_path}")
            _send_pdf_to_telegram(pdf_path, "MASTER SPECIFICATION OF THE DAY")
        else:
            logger.warning("Master Spec PDF generation returned None")
    except Exception as e:
        logger.error(f"Master spec session failed: {e}", exc_info=True)
    logger.info("=== MASTER SPEC SESSION END ===")


def _send_pdf_to_telegram(pdf_path: str, caption_prefix: str):
    """Helper to send a PDF to Telegram."""
    try:
        import os
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            logger.info("Telegram credentials not set, skipping PDF send")
            return
        from delivery.telegram_bot import TelegramDelivery
        # Use sync send if available
        import requests
        url = f"https://api.telegram.org/bot{token}/sendDocument"
        with open(pdf_path, "rb") as f:
            files = {"document": f}
            data = {"chat_id": chat_id, "caption": f"\U0001f4cb {caption_prefix}\n{now_utc().strftime('%Y-%m-%d %H:%M')}"}
            resp = requests.post(url, files=files, data=data, timeout=30)
            if resp.status_code == 200:
                logger.info(f"PDF sent to Telegram: {pdf_path}")
            else:
                logger.warning(f"Telegram send failed: {resp.status_code}")
    except Exception as e:
        logger.warning(f"Telegram PDF send failed: {e}")


def _get_pdf_types_for_session(session: str) -> list[str]:
    """Map session to PDF types to generate."""
    session_upper = session.upper()
    if "PRE_LSE" in session_upper:
        return ["momentum"]
    elif "PRE_NYSE" in session_upper:
        return ["momentum"]
    elif "EOD" in session_upper:
        return ["momentum", "risk", "review"]
    else:
        # Default: generate all three
        return ["momentum", "risk", "review"]


def _build_telegram_caption(
    session: str,
    summary: dict,
    pdf_type: str,
    is_preview: bool,
) -> str:
    """Build rich Telegram caption for PDF delivery."""
    today_str = date.today().strftime("%Y-%m-%d")
    prefix = "PREVIEW " if is_preview else ""
    pipeline = summary.get("pipeline", {})
    watchdog = summary.get("watchdog", {})

    core_plays = pipeline.get("core_plays", 0)
    strict = pipeline.get("strict_count", 0)
    fallback = pipeline.get("fallback_count", 0)
    peers = pipeline.get("peer_plays", 0)
    full_scan = pipeline.get("full_scan_cards", 0)
    drought = pipeline.get("drought", False)

    sys_state = watchdog.get("system_state", "UNKNOWN")
    reliability = watchdog.get("data_reliability_score", 0.0)

    # Top 3 plays summary
    top_plays_text = "No plays"
    # We can't easily get play details here, but we can count
    trade_count = strict
    watch_count = fallback

    lines = [
        f"NZT-48 {prefix}{pdf_type.upper()} | {today_str} | {session}",
        f"System: {sys_state} | Reliability: {reliability:.0%}",
        f"TRADE: {trade_count} | WATCH: {watch_count} | INTEL: {full_scan}",
    ]

    if drought:
        lines.append("SIGNAL DROUGHT — see report for details")

    return "\n".join(lines)


def _send_pdfs_to_telegram(
    pdf_paths: list[str],
    session: str,
    summary: dict,
    is_preview: bool,
) -> list[dict]:
    """Send PDFs to Telegram. Returns delivery results."""
    results: list[dict] = []

    try:
        from delivery.telegram_bot import TelegramDelivery
        tg = TelegramDelivery()
    except Exception as exc:
        logger.warning("[TELEGRAM] TelegramDelivery init failed: %s", exc)
        return [{"status": "SKIPPED", "reason": f"init failed: {exc}"}]

    # Check if telegram is configured
    if not tg.token or not tg.chat_id:
        logger.info("[TELEGRAM] not configured (no token/chat_id) — SKIPPED")
        return [{"status": "SKIPPED", "reason": "no TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"}]

    import asyncio

    for pdf_path in pdf_paths:
        pdf_type = "unknown"
        pdf_name = Path(pdf_path).name.upper()
        if "MOMENTUM" in pdf_name:
            pdf_type = "momentum"
        elif "RISK" in pdf_name:
            pdf_type = "risk"
        elif "REVIEW" in pdf_name:
            pdf_type = "review"

        caption = _build_telegram_caption(session, summary, pdf_type, is_preview)

        try:
            # Initialize bot for sending
            async def _do_send():
                await tg.initialize()
                sent = await tg.send_document(pdf_path, caption=caption)
                return sent

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # We're inside an async context — schedule it
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        sent = pool.submit(
                            lambda: asyncio.run(_do_send())
                        ).result(timeout=30)
                else:
                    sent = loop.run_until_complete(_do_send())
            except RuntimeError:
                sent = asyncio.run(_do_send())

            results.append({
                "pdf": pdf_path,
                "pdf_type": pdf_type,
                "status": "SENT" if sent else "FAILED",
                "caption_preview": caption[:100],
            })
        except Exception as exc:
            logger.error("[TELEGRAM] send failed for %s: %s", pdf_path, exc)
            results.append({
                "pdf": pdf_path,
                "pdf_type": pdf_type,
                "status": "ERROR",
                "error": str(exc)[:100],
            })

    return results


def _write_delivery_artifact(
    session: str,
    is_preview: bool,
    summary: dict,
    telegram_results: list[dict],
) -> None:
    """Write telegram_delivery.json artifact."""
    today_str = str(date.today())
    session_key = f"preview_{session.lower()}" if is_preview else session.lower()
    out_dir = ARTIFACTS_ROOT / today_str / session_key.replace(" ", "_")
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "session": session,
        "is_preview": is_preview,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pdfs_generated": summary.get("pdfs", {}).get("generated", 0),
        "pdf_paths": summary.get("pdfs", {}).get("paths", []),
        "telegram_results": telegram_results,
        "system_state": summary.get("watchdog", {}).get("system_state", "UNKNOWN"),
        "data_reliability": summary.get("watchdog", {}).get("data_reliability_score", 0.0),
        "pipeline_summary": {
            "core_plays": summary.get("pipeline", {}).get("core_plays", 0),
            "peer_plays": summary.get("pipeline", {}).get("peer_plays", 0),
            "full_scan_cards": summary.get("pipeline", {}).get("full_scan_cards", 0),
            "drought": summary.get("pipeline", {}).get("drought", False),
        },
    }

    out_path = out_dir / "telegram_delivery.json"
    try:
        fd, tmp_name = tempfile.mkstemp(dir=out_dir, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(payload, indent=2, default=str))
            f.flush()
            os.fsync(f.fileno())
        Path(tmp_name).replace(out_path)
        logger.info("[DELIVERY] artifact written: %s", out_path)
    except Exception as exc:
        logger.warning("[DELIVERY] artifact write failed: %s", exc)
        try:
            os.unlink(tmp_name)
        except Exception:
            pass


def run_all_preview_sessions(regime: str = "NEUTRAL") -> dict:
    """Run preview sessions for all 3 windows. Used for paper launch proof."""
    results = {}
    for session in ["PRE_LSE", "PRE_NYSE", "EOD_INSTITUTIONAL"]:
        try:
            result = run_scheduled_session(
                session=session,
                regime=regime,
                is_preview=True,
                send_telegram=False,  # Don't spam on preview
            )
            results[session] = result
        except Exception as exc:
            results[session] = {"error": str(exc)[:200]}
            logger.error("[PREVIEW] %s failed: %s", session, exc, exc_info=True)
    return results
