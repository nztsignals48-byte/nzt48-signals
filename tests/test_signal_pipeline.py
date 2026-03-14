"""
tests/test_signal_pipeline.py
===============================
Tests for signal pipeline integrity.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestRegimeDefault:
    def test_pipeline_runner_regime_optional(self):
        """Verify regime parameter defaults to None (not hardcoded NEUTRAL)."""
        import inspect
        from signal_engine.pipeline_runner import run_pipeline
        sig = inspect.signature(run_pipeline)
        regime_param = sig.parameters.get("regime")
        assert regime_param is not None
        assert regime_param.default is None

    def test_tiered_pipeline_regime_optional(self):
        import inspect
        from signal_engine.pipeline_runner import run_tiered_pipeline
        sig = inspect.signature(run_tiered_pipeline)
        regime_param = sig.parameters.get("regime")
        assert regime_param is not None
        assert regime_param.default is None


class TestUniverseVersion:
    def test_universe_version_is_2026(self):
        from uk_isa.isa_universe import SESSION_CONFIG
        assert SESSION_CONFIG["universe_version"] == "2026-Q1"

    def test_extended_universe_has_peer_tickers(self):
        from uk_isa.isa_universe import EXTENDED_UNIVERSE
        peers = ["AMD3.L", "ARM3.L", "NVDS.L", "TSLS.L", "3LDE.L", "3LEU.L"]
        for peer in peers:
            assert peer in EXTENDED_UNIVERSE, f"{peer} missing from EXTENDED_UNIVERSE"

    def test_inverse_etps_in_leverage_map(self):
        from uk_isa.isa_universe import LEVERAGE_MAP
        assert LEVERAGE_MAP["NVDS.L"] < 0
        assert LEVERAGE_MAP["TSLS.L"] < 0
        assert LEVERAGE_MAP["QQQS.L"] < 0
        assert LEVERAGE_MAP["3USS.L"] < 0


class TestSignalDedup:
    def test_dedup_key_format(self):
        """Verify signal dedup uses composite key."""
        source = Path(__file__).parent.parent / "learning" / "signal_logger.py"
        content = source.read_text()
        assert "_seen_ids" in content, "Signal logger should use _seen_ids set"
