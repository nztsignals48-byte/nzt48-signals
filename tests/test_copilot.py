"""
Operator Copilot Acceptance Tests
=================================
Tests for the NZT-48 Operator Copilot chatbot module.

The Copilot is a READ-ONLY chatbot that routes natural-language queries
to deterministic handlers. It CANNOT place orders.

Modules under test:
    command_center/copilot/intents.py     — Intent enum + parse_intent()
    command_center/copilot/throttling.py  — ScanThrottle rate limiter
    command_center/copilot/router.py      — CopilotRouter.query()
    command_center/copilot/handlers.py    — Individual intent handlers
    command_center/copilot/evidence.py    — Evidence collector

Run with: pytest tests/test_copilot.py -v --tb=short
"""

import sys
import os
import json
import time
import types
import inspect
import tempfile
import threading
import importlib
import pytest
from pathlib import Path

# Ensure project root is on path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Import helper: the copilot __init__.py unconditionally imports router.py,
# which may not exist yet. We pre-register a stub module so that intents.py
# and throttling.py can still be imported when router.py is absent.
# ---------------------------------------------------------------------------

def _ensure_copilot_importable():
    """Make sure command_center.copilot.intents and .throttling are importable
    even if router.py / handlers.py / evidence.py do not yet exist."""
    # If the full package already imports cleanly, nothing to do
    try:
        importlib.import_module("command_center.copilot.intents")
        return
    except (ImportError, ModuleNotFoundError):
        pass

    # Provide stub modules for missing submodules so __init__.py can import
    copilot_pkg = "command_center.copilot"
    for sub in ("router", "handlers", "evidence"):
        fqn = f"{copilot_pkg}.{sub}"
        if fqn not in sys.modules:
            stub = types.ModuleType(fqn)
            stub.__file__ = f"<stub for {fqn}>"
            stub.__package__ = copilot_pkg
            # router.py must export CopilotRouter for __init__.py
            if sub == "router":
                stub.CopilotRouter = None  # placeholder
            sys.modules[fqn] = stub

    # Now forcibly re-import / import the package
    if copilot_pkg in sys.modules:
        importlib.reload(sys.modules[copilot_pkg])
    else:
        importlib.import_module(copilot_pkg)


_ensure_copilot_importable()


# ---------------------------------------------------------------------------
# Imports that MUST exist (intents + throttling are already implemented)
# ---------------------------------------------------------------------------

from command_center.copilot.intents import Intent, parse_intent
from command_center.copilot.throttling import ScanThrottle


# ---------------------------------------------------------------------------
# Conditional imports for modules that may not yet be implemented.
# We check for the REAL module (not our stub) by looking for the query method.
# ---------------------------------------------------------------------------

def _try_import_router():
    """Attempt to import CopilotRouter; return class or None."""
    try:
        from command_center.copilot.router import CopilotRouter
        # If we got our stub placeholder (None), treat as not implemented
        if CopilotRouter is None:
            return None
        return CopilotRouter
    except (ImportError, AttributeError):
        return None


def _try_import_handlers():
    """Attempt to import handlers module; return module or None."""
    try:
        from command_center.copilot import handlers
        # Verify it has at least one real handle_ function (not a stub)
        real_handlers = [
            name for name in dir(handlers)
            if name.startswith("handle_") and callable(getattr(handlers, name))
        ]
        if not real_handlers:
            return None
        return handlers
    except (ImportError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Required response keys that every CopilotRouter.query() must return
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = {
    "answer", "actions", "evidence", "warnings",
    "as_of", "system_state", "regime", "confidence",
}


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def throttle():
    """Fresh ScanThrottle with short cooldown for testing."""
    return ScanThrottle(cooldown_seconds=2)


@pytest.fixture
def router():
    """CopilotRouter instance, skipped if router module not available."""
    cls = _try_import_router()
    if cls is None:
        pytest.skip("CopilotRouter not yet implemented (command_center/copilot/router.py)")
    return cls()


# ===========================================================================
# TEST 1: Scan Now returns correct structure
# ===========================================================================

class TestScanNow:
    """SCAN_NOW intent must return a well-structured response, even outside
    market hours when no signals exist."""

    def test_copilot_scan_now_returns_trade_watch(self, router):
        result = router.query(
            "I want to trade right now -- scan CORE for qualifying signals",
            lane="CORE",
            max_results=10,
        )

        # All required keys must be present
        for key in _REQUIRED_KEYS:
            assert key in result, f"Missing required key: {key}"

        # Confidence must be a valid grade
        assert result["confidence"] in ("A", "B", "C"), (
            f"confidence must be A/B/C, got: {result['confidence']}"
        )

        # as_of must be a non-empty timestamp string
        assert isinstance(result["as_of"], str) and len(result["as_of"]) > 0, (
            "as_of must be a non-empty string"
        )

        # answer must be a non-empty string
        assert isinstance(result["answer"], str) and len(result["answer"]) > 0, (
            "answer must be a non-empty string"
        )

        # If plays were found, actions should separate trade/watch
        # (outside market hours this list may be empty -- that's OK)
        if result["actions"]:
            for action in result["actions"]:
                assert isinstance(action, dict), (
                    f"Each action must be a dict, got: {type(action)}"
                )


# ===========================================================================
# TEST 2: Lane separation is respected
# ===========================================================================

class TestLaneSeparation:
    """parse_intent must extract lane information from the query."""

    def test_scan_core_lane(self):
        intent, params = parse_intent("scan now CORE")
        assert intent == Intent.SCAN_NOW
        assert params["lane"] == "CORE", (
            f"Expected lane='CORE', got: {params.get('lane')}"
        )

    def test_scan_opportunity_lane(self):
        intent, params = parse_intent("run scan OPPORTUNITY")
        assert intent == Intent.SCAN_NOW
        assert params["lane"] == "OPPORTUNITY", (
            f"Expected lane='OPPORTUNITY', got: {params.get('lane')}"
        )

    def test_show_intel_lane(self):
        _intent, params = parse_intent("show INTEL")
        # The intent may vary (SHOW_TOP_TRADES or other), but lane must be INTEL
        assert params["lane"] == "INTEL", (
            f"Expected lane='INTEL', got: {params.get('lane')}"
        )


# ===========================================================================
# TEST 3: Evidence paths
# ===========================================================================

class TestEvidencePaths:
    """Every copilot response must include evidence with path + notes."""

    def test_copilot_returns_evidence_paths(self, router):
        result = router.query("system health summary")

        assert "evidence" in result, "Response must include 'evidence' key"
        assert isinstance(result["evidence"], list), "evidence must be a list"

        # Each evidence item (if any) should have path + notes
        for item in result["evidence"]:
            assert isinstance(item, dict), f"Evidence item must be dict, got: {type(item)}"
            assert "path" in item, (
                f"Evidence item missing 'path' key: {item}"
            )
            assert "notes" in item, (
                f"Evidence item missing 'notes' key: {item}"
            )


# ===========================================================================
# TEST 4: Rate limiting
# ===========================================================================

class TestRateLimiting:
    """ScanThrottle must enforce cooldown between scans."""

    def test_copilot_rate_limited(self, throttle):
        # First call should be allowed (no previous scan)
        allowed, reason = throttle.can_scan()
        assert allowed is True, f"First scan should be allowed, got: {reason}"

        # Record the scan
        throttle.record_scan()

        # Immediate second call should be blocked
        allowed, reason = throttle.can_scan()
        assert allowed is False, "Scan immediately after record_scan() should be blocked"
        assert isinstance(reason, str) and len(reason) > 0, (
            "Blocked scan must provide a reason string"
        )

        # Wait for cooldown to expire (2s cooldown + 0.5s margin)
        time.sleep(2.5)

        # Should be allowed again
        allowed, reason = throttle.can_scan()
        assert allowed is True, f"Scan after cooldown should be allowed, got: {reason}"


# ===========================================================================
# TEST 5: No fabrication on missing artifacts
# ===========================================================================

class TestNoFabrication:
    """Copilot must not fabricate data for non-existent tickers."""

    def test_copilot_no_fabrication_on_missing_artifacts(self, router):
        result = router.query("why not ZZZZ.L")

        answer_lower = result["answer"].lower()

        # Must acknowledge the ticker is not found / has no data
        assert any(phrase in answer_lower for phrase in [
            "not found", "no data", "unavailable", "no signal",
            "unknown", "not recognised", "not recognized",
            "no information", "cannot find",
            "no gate report", "not in the current", "not in universe",
        ]), (
            f"Answer for missing ticker should mention not found/no data, "
            f"got: {result['answer'][:200]}"
        )

        # Must NOT contain fabricated trade levels (numbers that look like
        # entry/stop/target prices for a non-existent ticker)
        answer_text = result["answer"]
        # Check that the response does not claim to have entry/stop/target
        for forbidden_word in ["entry:", "stop:", "target:", "Entry:", "Stop:", "Target:"]:
            if forbidden_word in answer_text:
                # Only flag if followed by a number (fabricated level)
                import re
                pattern = rf"{re.escape(forbidden_word)}\s*[\d.]+"
                assert not re.search(pattern, answer_text), (
                    f"Response contains fabricated trade level: '{forbidden_word}' "
                    f"for non-existent ticker FAKE_TICKER_999.L"
                )

        # Confidence should NOT be "A" for missing data
        assert result["confidence"] in ("B", "C"), (
            f"Confidence for missing ticker should be B or C, got: {result['confidence']}"
        )


# ===========================================================================
# TEST 6: Never places orders (static analysis)
# ===========================================================================

class TestNeverPlacesOrders:
    """Static analysis: copilot handlers must NEVER contain order-placement code.

    This test inspects source code of all handler functions to verify the
    READ-ONLY safety guarantee. It passes regardless of market state.
    """

    def test_copilot_never_places_orders(self):
        handlers = _try_import_handlers()
        if handlers is None:
            pytest.skip("handlers module not yet implemented")

        # Collect all handler functions from the module
        handler_names = [
            name for name in dir(handlers)
            if name.startswith("handle_") and callable(getattr(handlers, name))
        ]
        assert len(handler_names) > 0, (
            "handlers module must export at least one handle_* function"
        )

        # Forbidden patterns that indicate order placement
        forbidden_patterns = [
            "VirtualTrader.execute",
            "virtual_trader.execute",
            "broker.place_order",
            "execute_trade",
            "place_order",
            "submit_order",
            "send_order",
            ".execute(",
        ]

        for name in handler_names:
            fn = getattr(handlers, name)
            try:
                source = inspect.getsource(fn)
            except (OSError, TypeError):
                continue  # built-in or C extension, not a concern

            for pattern in forbidden_patterns:
                assert pattern not in source, (
                    f"SAFETY VIOLATION: handler '{name}' contains '{pattern}'. "
                    f"Copilot handlers must be READ-ONLY and cannot place orders."
                )

        # Also check the handlers module-level imports
        try:
            module_source = inspect.getsource(handlers)
        except (OSError, TypeError):
            module_source = ""

        for pattern in ["from execution.virtual_trader import", "import broker"]:
            assert pattern not in module_source, (
                f"SAFETY VIOLATION: handlers.py imports order-placement module: '{pattern}'"
            )


# ===========================================================================
# BONUS TEST 7: Intent parsing comprehensive
# ===========================================================================

class TestIntentParsing:
    """Verify that various natural-language queries map to correct intents."""

    @pytest.mark.parametrize("query, expected_intent", [
        ("scan now please", Intent.SCAN_NOW),
        ("run a quick scan", Intent.SCAN_NOW),
        ("find trades now", Intent.SCAN_NOW),
    ])
    def test_scan_now_intents(self, query, expected_intent):
        intent, _params = parse_intent(query)
        assert intent == expected_intent, (
            f"Query '{query}' should be {expected_intent}, got: {intent}"
        )

    def test_why_not_ticker(self):
        intent, params = parse_intent("why not QQQ3.L")
        assert intent == Intent.WHY_NOT_TICKER, (
            f"Expected WHY_NOT_TICKER, got: {intent}"
        )
        assert params["ticker"] == "QQQ3.L", (
            f"Expected ticker='QQQ3.L', got: {params.get('ticker')}"
        )

    def test_explain_signal(self):
        intent, params = parse_intent("explain QQQ3.L signal")
        assert intent == Intent.EXPLAIN_SIGNAL, (
            f"Expected EXPLAIN_SIGNAL, got: {intent}"
        )
        assert params["ticker"] == "QQQ3.L", (
            f"Expected ticker='QQQ3.L', got: {params.get('ticker')}"
        )

    def test_health_summary(self):
        intent, _params = parse_intent("health check")
        assert intent == Intent.HEALTH_SUMMARY, (
            f"Expected HEALTH_SUMMARY, got: {intent}"
        )

    def test_show_top_trades(self):
        intent, params = parse_intent("top trades CORE")
        assert intent == Intent.SHOW_TOP_TRADES, (
            f"Expected SHOW_TOP_TRADES, got: {intent}"
        )
        assert params["lane"] == "CORE", (
            f"Expected lane='CORE', got: {params.get('lane')}"
        )

    def test_closest_misses(self):
        intent, _params = parse_intent("closest misses")
        assert intent == Intent.SHOW_CLOSEST_MISSES, (
            f"Expected SHOW_CLOSEST_MISSES, got: {intent}"
        )

    def test_what_changed(self):
        intent, _params = parse_intent("what changed since last tick")
        assert intent == Intent.WHAT_CHANGED, (
            f"Expected WHAT_CHANGED, got: {intent}"
        )

    def test_regime_status(self):
        intent, _params = parse_intent("current regime")
        assert intent == Intent.REGIME_STATUS, (
            f"Expected REGIME_STATUS, got: {intent}"
        )

    def test_unknown_gibberish(self):
        intent, _params = parse_intent("asdfghjkl")
        assert intent == Intent.UNKNOWN, (
            f"Expected UNKNOWN for gibberish, got: {intent}"
        )


# ===========================================================================
# BONUS TEST 8: Response structure validation
# ===========================================================================

class TestResponseStructure:
    """Verify that CopilotRouter.query() returns correctly typed fields."""

    def test_copilot_response_structure(self, router):
        result = router.query("top trades")

        # All required keys present
        for key in _REQUIRED_KEYS:
            assert key in result, f"Missing required key: {key}"

        # Type checks
        assert isinstance(result["answer"], str), (
            f"answer must be str, got: {type(result['answer'])}"
        )
        assert isinstance(result["actions"], list), (
            f"actions must be list, got: {type(result['actions'])}"
        )
        assert isinstance(result["evidence"], list), (
            f"evidence must be list, got: {type(result['evidence'])}"
        )
        assert isinstance(result["warnings"], list), (
            f"warnings must be list, got: {type(result['warnings'])}"
        )
        assert isinstance(result["confidence"], str), (
            f"confidence must be str, got: {type(result['confidence'])}"
        )
        assert isinstance(result["as_of"], str), (
            f"as_of must be str, got: {type(result['as_of'])}"
        )
        assert isinstance(result["regime"], str), (
            f"regime must be str, got: {type(result['regime'])}"
        )
        assert isinstance(result["system_state"], str), (
            f"system_state must be str, got: {type(result['system_state'])}"
        )


# ===========================================================================
# BONUS TEST 9: Throttle thread safety
# ===========================================================================

class TestThrottleThreadSafety:
    """ScanThrottle must be safe under concurrent access."""

    def test_throttle_thread_safety(self):
        throttle = ScanThrottle(cooldown_seconds=0.1)
        errors: list[Exception] = []
        results: list[tuple[bool, str]] = []
        lock = threading.Lock()

        def worker():
            try:
                for _ in range(5):
                    allowed, reason = throttle.can_scan()
                    with lock:
                        results.append((allowed, reason))
                    if allowed:
                        throttle.record_scan()
                    time.sleep(0.01)
            except Exception as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # No exceptions should have been raised
        assert len(errors) == 0, (
            f"Thread safety violation: {len(errors)} exceptions raised: "
            f"{[str(e) for e in errors[:5]]}"
        )

        # All results should be valid (bool, str) tuples
        for allowed, reason in results:
            assert isinstance(allowed, bool), f"allowed must be bool, got: {type(allowed)}"
            assert isinstance(reason, str), f"reason must be str, got: {type(reason)}"

        # scan_count should be consistent (at least 1 scan should have succeeded)
        assert throttle.scan_count >= 1, "At least one scan should have been recorded"


# ===========================================================================
# BONUS TEST 10: Audit logging
# ===========================================================================

class TestAuditLogging:
    """CopilotRouter should write query logs to data/copilot_queries.jsonl."""

    def test_audit_logging(self):
        cls = _try_import_router()
        if cls is None:
            pytest.skip("CopilotRouter not yet implemented")

        # Use a temp directory to isolate log output
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = os.path.join(tmp_dir, "copilot_queries.jsonl")

            # Try to create router with a custom data directory
            # The router may accept a log_dir param, or use DATA_DIR env var
            original_env = os.environ.get("NZT48_DATA_DIR")
            os.environ["NZT48_DATA_DIR"] = tmp_dir
            try:
                router_instance = cls()
                router_instance.query("health summary")
            finally:
                if original_env is not None:
                    os.environ["NZT48_DATA_DIR"] = original_env
                else:
                    os.environ.pop("NZT48_DATA_DIR", None)

            # Check default location as well
            project_root = Path(__file__).parent.parent
            default_log = project_root / "data" / "copilot_queries.jsonl"

            log_found = os.path.exists(log_path) or default_log.exists()

            if not log_found:
                pytest.skip(
                    "Audit log file not found at either "
                    f"{log_path} or {default_log}. "
                    "Audit logging may not yet be implemented."
                )

            # Read whichever log file exists
            actual_log = log_path if os.path.exists(log_path) else str(default_log)
            with open(actual_log, "r") as f:
                lines = f.readlines()

            assert len(lines) > 0, "Audit log should contain at least one entry"

            # Last line should be valid JSON
            last_entry = json.loads(lines[-1].strip())
            assert isinstance(last_entry, dict), "Log entry must be a JSON object"

            # Expected keys in the audit log entry
            expected_log_keys = {"query", "intent", "timestamp"}
            found_keys = set(last_entry.keys())
            missing = expected_log_keys - found_keys
            assert len(missing) == 0, (
                f"Audit log entry missing keys: {missing}. Found: {found_keys}"
            )
