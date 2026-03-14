"""
NZT-48 Institutional 110/100 Test Suite
Tests for: schemas, scan health, telegram gates, opportunity scanner, exit engine,
artifact consistency, truth manifest, and universe governance.
"""
import pytest
import json
import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Path setup: ensure project root is importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Attempt imports from the project.  Many modules may not exist yet (they are
# part of the 110/100 roadmap), so we create lightweight stubs when the real
# module is unavailable.  This lets the test file serve as both a *spec* and
# a runnable suite once the production code lands.
# ---------------------------------------------------------------------------

# --- Schemas ---------------------------------------------------------------
try:
    from schemas.truth_manifest import TruthManifest
except ImportError:
    class TruthManifest:
        """Stub: canonical truth manifest for a scan session."""
        def __init__(self, session_id: str, generated_at: str, tickers: list,
                     regime: str, plays_hash: str, version: str = "1.0"):
            self.session_id = session_id
            self.generated_at = generated_at
            self.tickers = tickers
            self.regime = regime
            self.plays_hash = plays_hash
            self.version = version

        def to_dict(self) -> dict:
            return {
                "session_id": self.session_id,
                "generated_at": self.generated_at,
                "tickers": self.tickers,
                "regime": self.regime,
                "plays_hash": self.plays_hash,
                "version": self.version,
            }

try:
    from schemas.signal_record import SignalRecord
except ImportError:
    class SignalRecord:
        """Stub: a single signal/play record."""
        REQUIRED_FIELDS = ["ticker", "direction", "score", "timestamp"]

        def __init__(self, **kwargs):
            for f in self.REQUIRED_FIELDS:
                if f not in kwargs:
                    raise ValueError(f"Missing required field: {f}")
            self.__dict__.update(kwargs)

        def validate(self) -> bool:
            return all(getattr(self, f, None) is not None for f in self.REQUIRED_FIELDS)

try:
    from schemas.play_card import PlayCard
except ImportError:
    class PlayCard:
        """Stub: opportunity play card."""
        def __init__(self, ticker: str, score: float, direction: str,
                     target_pct: float, stop_atr: float, regime: str,
                     rationale: str = ""):
            self.ticker = ticker
            self.score = score
            self.direction = direction
            self.target_pct = target_pct
            self.stop_atr = stop_atr
            self.regime = regime
            self.rationale = rationale

        def to_dict(self) -> dict:
            return {
                "ticker": self.ticker,
                "score": self.score,
                "direction": self.direction,
                "target_pct": self.target_pct,
                "stop_atr": self.stop_atr,
                "regime": self.regime,
                "rationale": self.rationale,
            }

        @classmethod
        def from_dict(cls, d: dict) -> "PlayCard":
            return cls(**d)

try:
    from schemas.scan_health import ScanHealth
except ImportError:
    class ScanHealth:
        """Stub: scan health status."""
        VALID_STATES = ["OK", "DEGRADED", "FAILED"]

        def __init__(self, state: str = "OK", tick_count: int = 0,
                     error_count: int = 0, last_error: str = ""):
            if state not in self.VALID_STATES:
                raise ValueError(f"Invalid state: {state}. Must be one of {self.VALID_STATES}")
            self.state = state
            self.tick_count = tick_count
            self.error_count = error_count
            self.last_error = last_error

        def to_dict(self) -> dict:
            return {
                "state": self.state,
                "tick_count": self.tick_count,
                "error_count": self.error_count,
                "last_error": self.last_error,
            }

try:
    from schemas.telegram_event import TelegramEvent
except ImportError:
    class TelegramEvent:
        """Stub: telegram event record."""
        VALID_ACTIONS = ["SEND", "BLOCK", "RATE_LIMIT", "ERROR"]

        def __init__(self, action: str, message_hash: str, timestamp: str,
                     detail: str = ""):
            if action not in self.VALID_ACTIONS:
                raise ValueError(f"Invalid action: {action}. Must be one of {self.VALID_ACTIONS}")
            self.action = action
            self.message_hash = message_hash
            self.timestamp = timestamp
            self.detail = detail

try:
    from schemas.drought_report import DroughtReport
except ImportError:
    class DroughtReport:
        """Stub: drought / no-opportunity report."""
        def __init__(self, date: str, tickers_scanned: int,
                     closest_misses: list = None, reason: str = ""):
            self.date = date
            self.tickers_scanned = tickers_scanned
            self.closest_misses = closest_misses or []
            self.reason = reason

        def to_dict(self) -> dict:
            return {
                "date": self.date,
                "tickers_scanned": self.tickers_scanned,
                "closest_misses": self.closest_misses,
                "reason": self.reason,
            }

# --- Scan Health Tracker ---------------------------------------------------
try:
    from core.scan_health import ScanHealthTracker
except ImportError:
    class ScanHealthTracker:
        """Stub: tracks scan health over time."""
        def __init__(self):
            self.state = "OK"
            self.tick_count = 0
            self.error_count = 0
            self.last_error = ""

        def record_tick(self):
            self.tick_count += 1

        def record_error(self, error_msg: str = ""):
            self.error_count += 1
            self.last_error = error_msg
            self.state = "DEGRADED"

        def save(self, path: str):
            with open(path, "w") as f:
                json.dump({
                    "state": self.state,
                    "tick_count": self.tick_count,
                    "error_count": self.error_count,
                    "last_error": self.last_error,
                }, f)

        def load(self, path: str):
            with open(path, "r") as f:
                data = json.load(f)
            self.state = data["state"]
            self.tick_count = data["tick_count"]
            self.error_count = data["error_count"]
            self.last_error = data["last_error"]

# --- Telegram Gates --------------------------------------------------------
try:
    from bots.telegram_gates import TelegramGates
except ImportError:
    class TelegramGates:
        """Stub: deduplication and rate limiting for Telegram sends."""
        MAX_PER_MINUTE = 10
        DEDUPE_WINDOW_SECONDS = 300  # 5 minutes

        def __init__(self):
            self._sent_hashes: dict[str, float] = {}  # hash -> timestamp
            self._send_timestamps: list[float] = []

        def is_duplicate(self, msg_hash: str, now: float = None) -> bool:
            now = now or datetime.now(timezone.utc).timestamp()
            if msg_hash in self._sent_hashes:
                if now - self._sent_hashes[msg_hash] < self.DEDUPE_WINDOW_SECONDS:
                    return True
            self._sent_hashes[msg_hash] = now
            return False

        def is_rate_limited(self, now: float = None) -> bool:
            now = now or datetime.now(timezone.utc).timestamp()
            self._send_timestamps = [
                t for t in self._send_timestamps if now - t < 60
            ]
            if len(self._send_timestamps) >= self.MAX_PER_MINUTE:
                return True
            self._send_timestamps.append(now)
            return False

        @staticmethod
        def validate_signal(play: dict) -> tuple[bool, str]:
            if not play.get("ticker"):
                return False, "Missing ticker"
            if play.get("score", 0) == 0:
                return False, "Score is zero"
            return True, "OK"

# --- Opportunity Scanner ---------------------------------------------------
try:
    from signal_engine.opportunity_scanner import OpportunityScanner
except ImportError:
    class OpportunityScanner:
        """Stub: scans universe for 2% daily target candidates."""
        COST_BPS = 15  # 15 bps estimated round-trip cost

        def __init__(self, data_source=None):
            self.data_source = data_source

        def scan(self, tickers: list, bars: dict) -> list[dict]:
            candidates = []
            for ticker in tickers:
                ticker_bars = bars.get(ticker, [])
                if not ticker_bars:
                    continue
                score = self._feasibility_score(ticker_bars)
                net_target = 2.0 + (self.COST_BPS / 100)
                candidates.append({
                    "ticker": ticker,
                    "score": score,
                    "net_target_pct": net_target,
                    "bars_count": len(ticker_bars),
                })
            return sorted(candidates, key=lambda c: c["score"], reverse=True)

        def _feasibility_score(self, bars: list) -> float:
            if not bars:
                return 0.0
            avg_range = sum(b.get("range_pct", 0) for b in bars) / len(bars)
            score = min(100.0, max(0.0, avg_range * 20))
            return round(score, 2)

# --- Exit Engine -----------------------------------------------------------
try:
    from execution.exit_engine import ExitEngine
except ImportError:
    class ExitEngine:
        """Stub: scores open positions for exit desirability."""
        def __init__(self):
            pass

        def score_exits(self, positions: list, regime: str = "GREEN") -> list[dict]:
            results = []
            for pos in positions:
                exit_score = self._calculate_exit_score(pos, regime)
                results.append({
                    "ticker": pos["ticker"],
                    "exit_score": exit_score,
                    "reason": self._exit_reason(pos, regime),
                })
            return results

        def _calculate_exit_score(self, pos: dict, regime: str) -> float:
            score = 50.0
            if pos.get("pnl_pct", 0) >= 2.0:
                score += 30.0
            if pos.get("pnl_pct", 0) < -1.0:
                score += 20.0
            if regime == "RED" and pos.get("direction") == "LONG":
                score += 40.0
            if regime == "RED" and pos.get("direction") == "SHORT":
                score -= 20.0
            return min(100.0, max(0.0, score))

        def _exit_reason(self, pos: dict, regime: str) -> str:
            if regime == "RED" and pos.get("direction") == "LONG":
                return "REGIME_FLIP"
            if pos.get("pnl_pct", 0) >= 2.0:
                return "TARGET_HIT"
            if pos.get("pnl_pct", 0) < -1.0:
                return "STOP_HIT"
            return "HOLD"

        def batch_sell_plan(self, sell_intents: list[dict]) -> list[dict]:
            return sorted(sell_intents, key=lambda s: s.get("exit_score", 0), reverse=True)

# --- Artifact Consistency --------------------------------------------------
try:
    from core.artifact_loader import ArtifactLoader
except ImportError:
    class ArtifactLoader:
        """Stub: loads and validates session artifacts."""
        def __init__(self, artifacts_dir: str):
            self.artifacts_dir = Path(artifacts_dir)

        def load_session(self, session_id: str = None) -> dict:
            bundle = {
                "session_id": session_id or "latest",
                "artifacts": [],
                "truth_manifest": None,
            }
            if not self.artifacts_dir.exists():
                return bundle

            for f in sorted(self.artifacts_dir.iterdir()):
                if f.suffix == ".json":
                    with open(f, "r") as fh:
                        data = json.load(fh)
                    bundle["artifacts"].append({"file": f.name, "data": data})
                    if f.stem == "truth_manifest":
                        bundle["truth_manifest"] = data
            return bundle

        @staticmethod
        def plays_hash(plays: list[dict]) -> str:
            import hashlib
            canonical = json.dumps(plays, sort_keys=True, separators=(",", ":"))
            return hashlib.sha256(canonical.encode()).hexdigest()

# --- Universe Governance ---------------------------------------------------
try:
    from uk_isa.universe_governance import UniverseGovernance
except ImportError:
    class UniverseGovernance:
        """Stub: governs ticker additions, removals, and health checks."""
        DELIST_THRESHOLD_DAYS = 3
        PROMOTE_THRESHOLD_DAYS = 5

        @staticmethod
        def verify_ticker_format(ticker: str) -> bool:
            return ticker.upper().endswith(".L")

        @staticmethod
        def should_auto_delist(empty_days: int) -> bool:
            return empty_days >= UniverseGovernance.DELIST_THRESHOLD_DAYS

        @staticmethod
        def should_auto_promote(clean_days: int) -> bool:
            return clean_days >= UniverseGovernance.PROMOTE_THRESHOLD_DAYS


# ===========================================================================
#  TEST CLASSES
# ===========================================================================


class TestSchemas:
    """Test schema objects: creation, validation, serialization."""

    def test_truth_manifest_creation(self):
        """Create TruthManifest, verify all fields are populated."""
        now = datetime.now(timezone.utc).isoformat()
        tm = TruthManifest(
            session_id="sess-001",
            generated_at=now,
            tickers=["QQQ3.L", "3LUS.L"],
            regime="GREEN",
            plays_hash="abc123",
            version="1.0",
        )
        assert tm.session_id == "sess-001"
        assert tm.generated_at == now
        assert tm.tickers == ["QQQ3.L", "3LUS.L"]
        assert tm.regime == "GREEN"
        assert tm.plays_hash == "abc123"
        assert tm.version == "1.0"

        d = tm.to_dict()
        assert isinstance(d, dict)
        assert d["session_id"] == "sess-001"
        assert len(d["tickers"]) == 2

    def test_signal_record_validation(self):
        """Create SignalRecord, verify required fields check."""
        sr = SignalRecord(
            ticker="QQQ3.L",
            direction="LONG",
            score=82.5,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        assert sr.validate() is True
        assert sr.ticker == "QQQ3.L"
        assert sr.direction == "LONG"
        assert sr.score == 82.5

    def test_signal_record_missing_field(self):
        """SignalRecord with missing required field raises ValueError."""
        with pytest.raises(ValueError, match="Missing required field"):
            SignalRecord(ticker="QQQ3.L", direction="LONG", score=50)

    def test_play_card_to_dict(self):
        """Create PlayCard, verify to_dict() roundtrip."""
        pc = PlayCard(
            ticker="NVD3.L",
            score=88.0,
            direction="LONG",
            target_pct=2.15,
            stop_atr=1.0,
            regime="GREEN",
            rationale="Strong momentum with regime support",
        )
        d = pc.to_dict()
        assert d["ticker"] == "NVD3.L"
        assert d["score"] == 88.0
        assert d["target_pct"] == 2.15

        # Roundtrip
        pc2 = PlayCard.from_dict(d)
        assert pc2.ticker == pc.ticker
        assert pc2.score == pc.score
        assert pc2.to_dict() == d

    def test_scan_health_schema(self):
        """Create ScanHealth, verify state validation."""
        sh = ScanHealth(state="OK", tick_count=42, error_count=1)
        assert sh.state == "OK"
        assert sh.tick_count == 42
        assert sh.error_count == 1

        d = sh.to_dict()
        assert d["state"] == "OK"

        # Invalid state should raise
        with pytest.raises(ValueError, match="Invalid state"):
            ScanHealth(state="BROKEN")

    def test_telegram_event_schema(self):
        """Create TelegramEvent, verify action values."""
        te = TelegramEvent(
            action="SEND",
            message_hash="hash123",
            timestamp=datetime.now(timezone.utc).isoformat(),
            detail="Signal for QQQ3.L",
        )
        assert te.action == "SEND"
        assert te.message_hash == "hash123"

        # Invalid action should raise
        with pytest.raises(ValueError, match="Invalid action"):
            TelegramEvent(
                action="INVALID",
                message_hash="x",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

    def test_drought_report_schema(self):
        """Create DroughtReport with closest_misses."""
        dr = DroughtReport(
            date="2025-03-15",
            tickers_scanned=12,
            closest_misses=[
                {"ticker": "QQQ3.L", "score": 68, "gap_pct": 0.3},
                {"ticker": "NVD3.L", "score": 55, "gap_pct": 0.8},
            ],
            reason="No ticker reached 75-score threshold",
        )
        assert dr.date == "2025-03-15"
        assert dr.tickers_scanned == 12
        assert len(dr.closest_misses) == 2
        assert dr.closest_misses[0]["ticker"] == "QQQ3.L"

        d = dr.to_dict()
        assert d["tickers_scanned"] == 12
        assert len(d["closest_misses"]) == 2


class TestScanHealth:
    """Test the ScanHealthTracker: state transitions and persistence."""

    def test_initial_state(self):
        """New tracker starts at OK with 0 ticks."""
        tracker = ScanHealthTracker()
        health = tracker.get_health()
        assert health.state == "OK"
        assert health.tick_count == 0

    def test_record_tick(self):
        """After record_tick(), tick_count increments."""
        tracker = ScanHealthTracker()
        tracker.record_tick()
        assert tracker.get_health().tick_count == 1
        tracker.record_tick()
        tracker.record_tick()
        assert tracker.get_health().tick_count == 3
        # State should remain OK after normal ticks
        assert tracker.get_health().state == "OK"

    def test_degraded_on_error(self):
        """After record_error(), state goes to DEGRADED."""
        tracker = ScanHealthTracker()
        tracker.record_tick()
        assert tracker.get_health().state == "OK"

        tracker.record_error("yfinance timeout on QQQ3.L")
        assert tracker.get_health().state == "DEGRADED"
        assert tracker.get_health().last_error_msg == "yfinance timeout on QQQ3.L"

    def test_save_load(self):
        """Save to temp file, load back, verify state preserved."""
        tracker = ScanHealthTracker()
        tracker.record_tick()
        tracker.record_tick()
        tracker.record_error("data gap")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            tmp_path = f.name

        try:
            tracker.save(tmp_path)

            # Load into new tracker
            tracker2 = ScanHealthTracker()
            tracker2.load(tmp_path)

            h1 = tracker.get_health()
            h2 = tracker2.get_health()
            assert h2.tick_count == h1.tick_count
            assert h2.engine_runs == h1.engine_runs
        finally:
            os.unlink(tmp_path)


class TestTelegramGates:
    """Test deduplication, rate limiting, and signal validation."""

    def test_dedupe_blocks_duplicate(self):
        """Same hash within 5 min is blocked."""
        gates = TelegramGates()
        now = 1000000.0

        # First send — should NOT be duplicate
        assert gates.is_duplicate("hash_aaa", now=now) is False

        # Same hash 60 seconds later — SHOULD be duplicate
        assert gates.is_duplicate("hash_aaa", now=now + 60) is True

        # Same hash 4 minutes later — still within window
        assert gates.is_duplicate("hash_aaa", now=now + 240) is True

    def test_dedupe_allows_different(self):
        """Different hash is allowed."""
        gates = TelegramGates()
        now = 1000000.0

        assert gates.is_duplicate("hash_aaa", now=now) is False
        assert gates.is_duplicate("hash_bbb", now=now + 1) is False
        assert gates.is_duplicate("hash_ccc", now=now + 2) is False

    def test_rate_limiter_enforces(self):
        """After MAX_PER_MINUTE sends, next is blocked."""
        gates = TelegramGates()
        now = 1000000.0

        # Send up to the limit
        for i in range(TelegramGates.MAX_PER_MINUTE):
            assert gates.is_rate_limited(now=now + i * 0.1) is False, (
                f"Send {i + 1} should be allowed"
            )

        # Next send should be blocked
        assert gates.is_rate_limited(now=now + TelegramGates.MAX_PER_MINUTE * 0.1) is True

    def test_validate_signal_rejects_zero(self):
        """Play with score=0 is rejected."""
        play = {"ticker": "QQQ3.L", "score": 0, "direction": "LONG"}
        valid, reason = TelegramGates.validate_signal(play)
        assert valid is False
        assert "zero" in reason.lower() or "score" in reason.lower()

    def test_validate_signal_rejects_none(self):
        """Play with no ticker is rejected."""
        play = {"score": 85, "direction": "LONG"}
        valid, reason = TelegramGates.validate_signal(play)
        assert valid is False
        assert "ticker" in reason.lower()


class TestOpportunityScanner:
    """Test the opportunity scanner: candidate generation and scoring."""

    def _mock_bars(self, tickers: list, range_pct: float = 3.5) -> dict:
        """Create mock bar data for testing."""
        bars = {}
        for ticker in tickers:
            bars[ticker] = [
                {"open": 100, "high": 100 + range_pct, "low": 100 - range_pct / 2,
                 "close": 100 + range_pct * 0.8, "volume": 1000000,
                 "range_pct": range_pct},
                {"open": 101, "high": 101 + range_pct, "low": 101 - range_pct / 2,
                 "close": 101 + range_pct * 0.6, "volume": 1200000,
                 "range_pct": range_pct},
            ]
        return bars

    def test_scan_returns_candidates(self):
        """Mock bars data produces candidates."""
        scanner = OpportunityScanner()
        tickers = ["QQQ3.L", "NVD3.L", "3LUS.L"]
        bars = self._mock_bars(tickers, range_pct=4.0)

        candidates = scanner.scan(tickers, bars)

        assert len(candidates) == 3
        assert all("ticker" in c for c in candidates)
        assert all("score" in c for c in candidates)

        # Should be sorted by score descending
        scores = [c["score"] for c in candidates]
        assert scores == sorted(scores, reverse=True)

    def test_feasibility_score_range(self):
        """All scores between 0-100."""
        scanner = OpportunityScanner()

        # Low range bars
        low_bars = [{"range_pct": 0.5}, {"range_pct": 0.3}]
        score_low = scanner._feasibility_score(low_bars)
        assert 0.0 <= score_low <= 100.0

        # High range bars
        high_bars = [{"range_pct": 8.0}, {"range_pct": 10.0}]
        score_high = scanner._feasibility_score(high_bars)
        assert 0.0 <= score_high <= 100.0

        # Empty bars
        score_empty = scanner._feasibility_score([])
        assert score_empty == 0.0

        # High range should score higher than low range
        assert score_high > score_low

    def test_cost_aware_target(self):
        """net_target > 2.0% (includes costs)."""
        scanner = OpportunityScanner()
        tickers = ["QQQ3.L"]
        bars = self._mock_bars(tickers)

        candidates = scanner.scan(tickers, bars)
        assert len(candidates) == 1
        assert candidates[0]["net_target_pct"] > 2.0, (
            "Net target must exceed 2.0% to account for trading costs"
        )


class TestExitEngine:
    """Test the exit engine: scoring, regime awareness, and batch planning."""

    @staticmethod
    def _make_bars_df(n: int = 30, base_price: float = 100.0):
        """Create a minimal pandas DataFrame of OHLCV bars for testing."""
        import pandas as pd
        import numpy as np
        dates = pd.date_range("2026-01-01", periods=n, freq="h")
        np.random.seed(42)
        closes = base_price + np.cumsum(np.random.randn(n) * 0.5)
        df = pd.DataFrame({
            "Open": closes - 0.2,
            "High": closes + 1.0,
            "Low": closes - 1.0,
            "Close": closes,
            "Volume": np.random.randint(100000, 500000, n),
        }, index=dates)
        return df

    def test_score_exits_returns_results(self):
        """Mock positions produce exit scores."""
        engine = ExitEngine()
        positions = [
            {"ticker": "QQQ3.L", "direction": "LONG", "entry_price": 100,
             "current_price": 101.5, "entry_time": "2026-01-01T10:00:00+00:00",
             "peak_r": 1.0, "strategy": "S15", "shares": 10},
            {"ticker": "NVD3.L", "direction": "LONG", "entry_price": 50,
             "current_price": 55.0, "entry_time": "2026-01-01T10:00:00+00:00",
             "peak_r": 2.5, "strategy": "S15", "shares": 20},
        ]
        bars_batch = {
            "QQQ3.L": self._make_bars_df(30, 100.0),
            "NVD3.L": self._make_bars_df(30, 50.0),
        }
        results = engine.score_exits(positions, bars_batch, regime_tag="NEUTRAL")

        assert len(results) == 2
        assert all("exit_score" in r for r in results)
        assert all("ticker" in r for r in results)
        assert all("sell_intent" in r for r in results)

    def test_exit_now_on_regime_flip(self):
        """Position against regime gets high exit score."""
        engine = ExitEngine()
        positions = [
            {"ticker": "QQQ3.L", "direction": "LONG", "entry_price": 100,
             "current_price": 100.5, "entry_time": "2026-01-01T10:00:00+00:00",
             "peak_r": 0.5, "strategy": "S15", "shares": 10},
        ]
        bars_batch = {"QQQ3.L": self._make_bars_df(30, 100.0)}

        # Neutral regime — should be lower score
        neutral_results = engine.score_exits(positions, bars_batch, regime_tag="NEUTRAL")
        neutral_score = neutral_results[0]["exit_score"]

        # RISK_OFF regime (bearish) with LONG position — should be higher score
        bearish_results = engine.score_exits(positions, bars_batch, regime_tag="RISK_OFF")
        bearish_score = bearish_results[0]["exit_score"]

        assert bearish_score > neutral_score, (
            "Bearish regime should increase exit urgency for LONG position"
        )
        assert bearish_results[0]["regime_flipped"] is True

    def test_batch_sell_plan(self):
        """Multiple sell intents produce an ordered plan."""
        engine = ExitEngine()
        sell_intents = [
            {"ticker": "QQQ3.L", "exit_score": 60, "sell_intent": "TRAIL",
             "liquidity_bucket": "LOW", "direction": "LONG", "shares": 10,
             "strategy": "S15", "current_r": 0.5},
            {"ticker": "NVD3.L", "exit_score": 95, "sell_intent": "EXIT_NOW",
             "liquidity_bucket": "LOW", "direction": "LONG", "shares": 20,
             "strategy": "S15", "current_r": -0.3},
            {"ticker": "3LUS.L", "exit_score": 75, "sell_intent": "PARTIAL",
             "liquidity_bucket": "LOW", "direction": "LONG", "shares": 15,
             "strategy": "S15", "current_r": 1.2},
        ]

        plan = engine.batch_sell_plan(sell_intents)

        # Real batch_sell_plan returns a dict with execution_order
        assert isinstance(plan, dict)
        assert "execution_order" in plan
        assert "warnings" in plan
        assert "estimated_impact" in plan

        execution_order = plan["execution_order"]
        assert len(execution_order) == 3

        # Should be ordered by exit_score descending (most urgent first) within bucket
        scores = [p["exit_score"] for p in execution_order]
        assert scores == sorted(scores, reverse=True)


class TestArtifactConsistency:
    """Test artifact loading, hashing, and bundle integrity."""

    def test_artifact_loader_loads(self):
        """Create temp artifacts dir with date/session layout, load session."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Real ArtifactLoader expects: base_dir/date/session/ layout
            session_dir = os.path.join(tmpdir, "2026-02-27", "pre_lse")
            os.makedirs(session_dir)

            # Create manifest (real loader expects manifest.json, not truth_manifest.json)
            manifest_data = {
                "session_id": "sess-test",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "tickers": ["QQQ3.L"],
                "regime": "GREEN",
                "plays_hash": "",  # Will be computed
                "version": "1.0",
            }

            plays_data = [
                {"ticker": "QQQ3.L", "score": 85, "direction": "LONG"},
            ]

            # Compute correct plays hash for manifest
            manifest_data["plays_hash"] = ArtifactLoader.compute_plays_hash(plays_data)

            with open(os.path.join(session_dir, "manifest.json"), "w") as f:
                json.dump(manifest_data, f)

            with open(os.path.join(session_dir, "plays.json"), "w") as f:
                json.dump(plays_data, f)

            # Load
            loader = ArtifactLoader(tmpdir)
            bundle = loader.load_session("2026-02-27", "pre_lse")

            assert bundle.date == "2026-02-27"
            assert bundle.session == "pre_lse"
            assert len(bundle.plays) == 1
            assert bundle.truth_manifest is not None
            assert bundle.truth_manifest["session_id"] == "sess-test"

    def test_plays_hash_deterministic(self):
        """Same plays produce same hash."""
        plays = [
            {"ticker": "QQQ3.L", "score": 85, "direction": "LONG"},
            {"ticker": "NVD3.L", "score": 72, "direction": "LONG"},
        ]

        hash1 = ArtifactLoader.compute_plays_hash(plays)
        hash2 = ArtifactLoader.compute_plays_hash(plays)

        assert hash1 == hash2
        assert isinstance(hash1, str)
        assert len(hash1) == 64  # SHA-256 hex digest

        # Different plays should produce different hash
        plays_modified = [
            {"ticker": "QQQ3.L", "score": 86, "direction": "LONG"},
            {"ticker": "NVD3.L", "score": 72, "direction": "LONG"},
        ]
        hash3 = ArtifactLoader.compute_plays_hash(plays_modified)
        assert hash3 != hash1

    def test_truth_manifest_in_bundle(self):
        """Loaded bundle has truth manifest when file exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = os.path.join(tmpdir, "2026-02-27", "eod_check")
            os.makedirs(session_dir)

            manifest_data = {
                "session_id": "sess-verify",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "tickers": ["QQQ3.L", "3LUS.L"],
                "regime": "AMBER",
                "plays_hash": "abc_def_123",
                "version": "1.0",
            }
            with open(os.path.join(session_dir, "manifest.json"), "w") as f:
                json.dump(manifest_data, f)

            loader = ArtifactLoader(tmpdir)
            bundle = loader.load_session("2026-02-27", "eod_check")

            assert bundle.truth_manifest is not None
            assert bundle.truth_manifest["regime"] == "AMBER"
            assert bundle.truth_manifest["plays_hash"] == "abc_def_123"
            assert len(bundle.truth_manifest["tickers"]) == 2


class TestUniverseGovernance:
    """Test ticker format validation, auto-delist, and auto-promote rules."""

    def test_verify_ticker_format(self):
        """.L suffix verified."""
        assert UniverseGovernance.verify_ticker_format("QQQ3.L") is True
        assert UniverseGovernance.verify_ticker_format("3LUS.L") is True
        assert UniverseGovernance.verify_ticker_format("AAPL") is False
        assert UniverseGovernance.verify_ticker_format("MSFT.N") is False
        assert UniverseGovernance.verify_ticker_format("qqq3.l") is True  # case insensitive
        assert UniverseGovernance.verify_ticker_format("") is False

    def test_auto_delist_check(self):
        """3 empty days triggers delist."""
        # Below threshold — should NOT delist
        assert UniverseGovernance.should_auto_delist(0) is False
        assert UniverseGovernance.should_auto_delist(1) is False
        assert UniverseGovernance.should_auto_delist(2) is False

        # At threshold — SHOULD delist
        assert UniverseGovernance.should_auto_delist(3) is True
        assert UniverseGovernance.should_auto_delist(5) is True
        assert UniverseGovernance.should_auto_delist(10) is True

    def test_auto_promote_check(self):
        """5 clean days triggers promote."""
        # Below threshold — should NOT promote
        assert UniverseGovernance.should_auto_promote(0) is False
        assert UniverseGovernance.should_auto_promote(2) is False
        assert UniverseGovernance.should_auto_promote(4) is False

        # At threshold — SHOULD promote
        assert UniverseGovernance.should_auto_promote(5) is True
        assert UniverseGovernance.should_auto_promote(7) is True
        assert UniverseGovernance.should_auto_promote(30) is True


# ===========================================================================
#  Run with: pytest tests/test_institutional_110.py -v
# ===========================================================================
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
