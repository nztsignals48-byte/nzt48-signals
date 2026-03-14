"""
Performance-Based Promotion/Relegation System — NZT-48 V8.0
A-team/B-team tier classification for the ISA ticker universe.

Design:
  A_TEAM: 12 core active tickers — full scan, full size
  B_TEAM: Extended universe (up to 12 more) — scan but reduced size (0.5x)
  WATCH:  Sector radar — signal monitoring only, never traded

Promotion (B → A): ≥20 trades, win_rate ≥55%, avg_R ≥1.2, 90d data continuity ≥95%
Relegation (A → B): win_rate <42% (min 20 sample), avg_R <0.8, 3 consecutive
                    stopped-out weeks, volatility drag sigma >2% for 10 days
Hard delist gate: requires human Telegram confirmation; auto-cancels after 24h.
"""

import json
import logging
import os
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
RELEGATION_STATE_FILE = DATA_DIR / "governance_decisions.jsonl"
PENDING_FILE = DATA_DIR / "relegation_pending.json"

# Tier labels
TIER_A = "A_TEAM"
TIER_B = "B_TEAM"
TIER_WATCH = "WATCH"

# Promotion thresholds
PROMO_MIN_TRADES = 20
PROMO_WIN_RATE = 0.55
PROMO_AVG_R = 1.2
PROMO_CONTINUITY = 0.95  # 95% data availability over 90 days

# Relegation thresholds
RELG_MIN_SAMPLE = 20
RELG_WIN_RATE = 0.42
RELG_AVG_R = 0.8
RELG_CONSEC_STOPPED_WEEKS = 3
RELG_SIGMA_THRESHOLD = 0.02  # 2% daily sigma
RELG_SIGMA_DAYS = 10

# Confirmation timeout (24 hours — auto-cancel after this)
CONFIRMATION_TIMEOUT_HOURS = 24

# Default A-team — 10 LONG core ETPs (full size, scan priority)
DEFAULT_A_TEAM = {
    "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L",
    "NVD3.L", "TSL3.L", "TSM3.L", "MU2.L",
    "QQQ5.L", "SP5L.L",
}

# Default B-team — inverse ETPs + expansion candidates (0.5x size, promotable)
# These are scanned in S15/S16 at 0.5x size; promoted to A-team after 20 trades WR>=55%
# F-03: inverse ETPs imported from single source of truth (config.universe_constants)
from config.universe_constants import INVERSE_ETPS_SET as _INVERSE_ETPS_SET
DEFAULT_B_TEAM = _INVERSE_ETPS_SET | {
    # High-volume expansion candidates
    "SPXL.L", "SEMI.L", "SOXL.L",
}


class PerformanceRelegation:
    """
    Tracks per-ticker performance and manages A/B-team tier classification.

    Key safety rule: A-team ticker demotion requires:
    1. ≥30 completed trades (never demote with small sample)
    2. Consistent underperformance over 20-trade rolling window
    3. Telegram alert with full stats
    4. Human confirmation OR 24h timeout (auto-cancels — do NOT auto-demote)
    5. All trade history preserved — never deleted
    """

    def __init__(self, state_file: str = None):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.state_file = Path(state_file) if state_file else DATA_DIR / "performance_relegation.json"
        self.state = self._load_state()

    # ─────────────────────────────────────────────────────────
    # State persistence
    # ─────────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                pass
        # Bootstrap: A-team = 10 core longs, B-team = 10 inverse + expansion
        tiers: dict[str, str] = {}
        for t in DEFAULT_A_TEAM:
            tiers[t] = TIER_A
        for t in DEFAULT_B_TEAM:
            if t not in tiers:
                tiers[t] = TIER_B
        return {
            "tiers": tiers,
            "last_check": None,
            "pending_demotions": {},  # ticker → {stats, alert_sent_at}
        }

    def _save_state(self) -> None:
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.warning("PerformanceRelegation: save failed: %s", e)

    def _log_governance_decision(self, ticker: str, action: str, stats: dict) -> None:
        """Append demotion/promotion decision to governance log (immutable audit trail)."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "ticker": ticker,
            "action": action,
            "stats": stats,
        }
        try:
            with open(RELEGATION_STATE_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning("Governance log write failed: %s", e)

    # ─────────────────────────────────────────────────────────
    # Tier queries
    # ─────────────────────────────────────────────────────────

    def get_tier(self, ticker: str) -> str:
        """Returns current tier for ticker. Defaults to B_TEAM for unknown tickers."""
        return self.state["tiers"].get(ticker, TIER_B)

    def get_size_multiplier(self, ticker: str) -> float:
        """A-team: 1.0x, B-team: 0.5x, Watch: 0.0x."""
        tier = self.get_tier(ticker)
        if tier == TIER_A:
            return 1.0
        if tier == TIER_B:
            return 0.5
        return 0.0  # WATCH — never trade

    def is_tradeable(self, ticker: str) -> bool:
        """Returns True if ticker can be traded (A or B team)."""
        return self.get_tier(ticker) in (TIER_A, TIER_B)

    def get_all_tiers(self) -> dict:
        """Returns full tier map {ticker: tier}."""
        return dict(self.state["tiers"])

    def set_tier(self, ticker: str, tier: str) -> None:
        """Manually set a ticker's tier. Use for onboarding new tickers."""
        self.state["tiers"][ticker] = tier
        self._save_state()

    # ─────────────────────────────────────────────────────────
    # Performance scoring
    # ─────────────────────────────────────────────────────────

    def compute_ticker_score(self, ticker: str, outcomes: list) -> dict:
        """
        Computes performance metrics from outcomes.jsonl records for this ticker.

        Returns:
          {tier, win_rate, avg_r, avg_win_r, avg_loss_r, sample_size,
           last_updated, promotion_eligible, relegation_risk, consecutive_stopped_weeks}
        """
        ticker_outcomes = [
            o for o in outcomes
            if o.get("ticker") == ticker and o.get("status") in ("WIN", "LOSS", "STOPPED_OUT")
        ]

        if not ticker_outcomes:
            return {
                "tier": self.get_tier(ticker),
                "win_rate": None, "avg_r": None,
                "sample_size": 0, "promotion_eligible": False,
                "relegation_risk": False,
            }

        # Rolling window: use last 40 trades for risk assessment, last 60 for promotion
        recent_40 = ticker_outcomes[-40:]
        recent_60 = ticker_outcomes[-60:]

        wins_40 = sum(1 for o in recent_40 if o.get("status") == "WIN")
        win_rate_40 = wins_40 / len(recent_40) if recent_40 else 0.0

        r_vals_40 = [o.get("r_multiple", 0.0) for o in recent_40 if o.get("r_multiple") is not None]
        avg_r_40 = sum(r_vals_40) / len(r_vals_40) if r_vals_40 else 0.0
        avg_win_r = sum(r for r in r_vals_40 if r > 0) / max(1, sum(1 for r in r_vals_40 if r > 0))
        avg_loss_r = sum(r for r in r_vals_40 if r <= 0) / max(1, sum(1 for r in r_vals_40 if r <= 0))

        # Promotion metrics (60-trade window)
        wins_60 = sum(1 for o in recent_60 if o.get("status") == "WIN")
        win_rate_60 = wins_60 / len(recent_60) if recent_60 else 0.0
        r_vals_60 = [o.get("r_multiple", 0.0) for o in recent_60 if o.get("r_multiple") is not None]
        avg_r_60 = sum(r_vals_60) / len(r_vals_60) if r_vals_60 else 0.0

        # Consecutive stopped-out weeks
        consec_stopped_weeks = self._count_consecutive_stopped_weeks(ticker_outcomes)

        # Promotion eligible (B → A)
        promotion_eligible = (
            len(ticker_outcomes) >= PROMO_MIN_TRADES
            and win_rate_60 >= PROMO_WIN_RATE
            and avg_r_60 >= PROMO_AVG_R
        )

        # Relegation risk (A → B)
        relegation_risk = (
            len(recent_40) >= RELG_MIN_SAMPLE
            and (win_rate_40 < RELG_WIN_RATE or avg_r_40 < RELG_AVG_R
                 or consec_stopped_weeks >= RELG_CONSEC_STOPPED_WEEKS)
        )

        return {
            "tier": self.get_tier(ticker),
            "win_rate": round(win_rate_40, 3),
            "avg_r": round(avg_r_40, 3),
            "avg_win_r": round(avg_win_r, 3),
            "avg_loss_r": round(avg_loss_r, 3),
            "sample_size": len(ticker_outcomes),
            "sample_40": len(recent_40),
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "promotion_eligible": promotion_eligible,
            "relegation_risk": relegation_risk,
            "consecutive_stopped_weeks": consec_stopped_weeks,
        }

    def _count_consecutive_stopped_weeks(self, outcomes: list) -> int:
        """Count consecutive calendar weeks (most recent first) where all exits were STOPPED_OUT."""
        if not outcomes:
            return 0

        # Group by ISO week
        week_outcomes: dict[str, list] = {}
        for o in outcomes:
            try:
                ts = o.get("close_time") or o.get("timestamp") or ""
                if ts:
                    d = datetime.fromisoformat(ts[:10]).date()
                    week_key = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
                    week_outcomes.setdefault(week_key, []).append(o)
            except Exception:
                continue

        sorted_weeks = sorted(week_outcomes.keys(), reverse=True)
        consecutive = 0
        for week in sorted_weeks:
            week_trades = week_outcomes[week]
            if all(o.get("status") == "STOPPED_OUT" for o in week_trades):
                consecutive += 1
            else:
                break
        return consecutive

    # ─────────────────────────────────────────────────────────
    # Full check cycle
    # ─────────────────────────────────────────────────────────

    def check_all_tickers(self, outcomes: list = None) -> dict:
        """
        Runs full promotion/relegation check on all tickers.
        Called nightly at 23:30 UTC.

        Returns: {promoted: list, relegated: list, pending_confirmation: list}
        """
        if outcomes is None:
            outcomes = self._load_outcomes()

        result = {"promoted": [], "relegated": [], "pending_confirmation": []}

        current_tiers = self.state["tiers"]
        pending = self.state.get("pending_demotions", {})

        # Check expired pending demotions (24h timeout → auto-cancel)
        expired = []
        for ticker, pending_info in list(pending.items()):
            sent_at = datetime.fromisoformat(pending_info["alert_sent_at"])
            age_hours = (datetime.now(timezone.utc) - sent_at).total_seconds() / 3600
            if age_hours >= CONFIRMATION_TIMEOUT_HOURS:
                logger.info(
                    "RELEGATION AUTO-CANCEL: %s — 24h elapsed with no confirmation, keeping in A-team",
                    ticker,
                )
                self._log_governance_decision(ticker, "DEMOTION_CANCELLED_TIMEOUT", pending_info.get("stats", {}))
                expired.append(ticker)
        for ticker in expired:
            del pending[ticker]

        for ticker in list(current_tiers.keys()):
            score = self.compute_ticker_score(ticker, outcomes)
            tier = current_tiers[ticker]

            # B → A promotion
            if tier == TIER_B and score["promotion_eligible"]:
                current_tiers[ticker] = TIER_A
                result["promoted"].append({"ticker": ticker, "stats": score})
                self._log_governance_decision(ticker, "PROMOTED_B_TO_A", score)
                logger.info(
                    "PROMOTED B→A: %s | wr=%.1f%% avg_R=%.2f n=%d",
                    ticker, (score["win_rate"] or 0) * 100,
                    score["avg_r"] or 0, score["sample_size"],
                )

            # A → B relegation (requires confirmation)
            elif tier == TIER_A and score["relegation_risk"] and ticker not in pending:
                # Hard gate: must have ≥30 trades before ANY demotion
                if score["sample_size"] < 30:
                    logger.info(
                        "RELEGATION BLOCKED: %s has only %d trades (need ≥30)",
                        ticker, score["sample_size"],
                    )
                    continue

                # Queue for human confirmation
                pending[ticker] = {
                    "stats": score,
                    "alert_sent_at": datetime.now(timezone.utc).isoformat(),
                    "auto_cancel_at": (
                        datetime.now(timezone.utc) + timedelta(hours=CONFIRMATION_TIMEOUT_HOURS)
                    ).isoformat(),
                }
                result["relegated"].append({"ticker": ticker, "stats": score, "status": "PENDING_CONFIRMATION"})
                self._log_governance_decision(ticker, "DEMOTION_VOTE_INITIATED", score)
                logger.warning(
                    "A-TEAM DEMOTION VOTE: %s | wr=%.1f%% avg_R=%.2f n=%d | "
                    "Waiting for Telegram confirmation (auto-cancels in 24h)",
                    ticker, (score["win_rate"] or 0) * 100,
                    score["avg_r"] or 0, score["sample_size"],
                )

            # Report pending confirmations
            if ticker in pending:
                result["pending_confirmation"].append({
                    "ticker": ticker,
                    "stats": pending[ticker].get("stats", {}),
                    "alert_sent_at": pending[ticker]["alert_sent_at"],
                })

        self.state["pending_demotions"] = pending
        self.state["last_check"] = datetime.now(timezone.utc).isoformat()
        self._save_state()
        return result

    def confirm_demotion(self, ticker: str) -> bool:
        """
        Called when operator confirms demotion via Telegram reply.
        Returns True if demotion applied, False if no pending vote.
        """
        pending = self.state.get("pending_demotions", {})
        if ticker not in pending:
            logger.warning("confirm_demotion: no pending vote for %s", ticker)
            return False

        stats = pending.pop(ticker)
        self.state["tiers"][ticker] = TIER_B
        self._save_state()
        self._log_governance_decision(ticker, "DEMOTED_A_TO_B_CONFIRMED", stats)
        logger.warning("A-TEAM DEMOTION CONFIRMED: %s → B-TEAM", ticker)
        return True

    def _load_outcomes(self) -> list:
        """Load outcomes.jsonl for analysis."""
        outcomes_path = DATA_DIR / "outcomes.jsonl"
        if not outcomes_path.exists():
            return []
        results = []
        try:
            with open(outcomes_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            results.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            logger.warning("Could not load outcomes.jsonl: %s", e)
        return results

    # ─────────────────────────────────────────────────────────
    # Telegram formatting
    # ─────────────────────────────────────────────────────────

    def get_demotion_alert(self, ticker: str, score: dict) -> str:
        """Format Telegram alert for demotion vote."""
        wr = (score.get("win_rate") or 0) * 100
        avg_r = score.get("avg_r") or 0
        n = score.get("sample_size", 0)
        consec = score.get("consecutive_stopped_weeks", 0)
        return (
            f"⚠️ A-TEAM DEMOTION VOTE: {ticker}\n"
            f"  win_rate={wr:.1f}% (threshold {RELG_WIN_RATE*100:.0f}%)\n"
            f"  avg_R={avg_r:.2f} (threshold {RELG_AVG_R:.1f})\n"
            f"  trades={n} | stopped_weeks={consec}\n"
            f"\n"
            f"Reply 'CONFIRM DEMOTE {ticker}' to demote to B-team.\n"
            f"No action within 24h = CANCELLED (ticker stays in A-team)."
        )

    def get_telegram_summary(self) -> str:
        """Short nightly summary of tier status."""
        tiers = self.state["tiers"]
        a_count = sum(1 for t in tiers.values() if t == TIER_A)
        b_count = sum(1 for t in tiers.values() if t == TIER_B)
        w_count = sum(1 for t in tiers.values() if t == TIER_WATCH)
        pending = len(self.state.get("pending_demotions", {}))

        parts = [f"📊 Tier Status: A={a_count} | B={b_count} | Watch={w_count}"]
        if pending:
            pending_tickers = list(self.state["pending_demotions"].keys())
            parts.append(f"  ⚠️ Pending demotion votes: {', '.join(pending_tickers)}")
        return "\n".join(parts)
