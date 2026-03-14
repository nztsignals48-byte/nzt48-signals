"""Tests for CORE-FIRST + 50% Peer Expansion + Full Scan tiered universe."""
import json
import math
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestUniverseManager:
    """UniverseManager must enforce tier sizes and separation."""

    def test_universe_manager_loads(self):
        from uk_isa.universe_manager import get_universe_manager
        um = get_universe_manager(force_reload=True)
        assert um is not None

    def test_core_list_is_12(self):
        from uk_isa.universe_manager import get_universe_manager
        um = get_universe_manager()
        assert len(um.core_list) == 12, f"CORE must be 12, got {len(um.core_list)}"

    def test_peer_size_target(self):
        from uk_isa.universe_manager import get_universe_manager
        um = get_universe_manager()
        expected = math.ceil(0.50 * len(um.core_list))
        assert um.peer_size_target == expected, f"Peer target must be {expected}, got {um.peer_size_target}"

    def test_peer_size_target_is_6(self):
        from uk_isa.universe_manager import get_universe_manager
        um = get_universe_manager()
        assert um.peer_size_target == 6

    def test_compute_budget_sums_to_100(self):
        from uk_isa.universe_manager import get_universe_manager
        um = get_universe_manager()
        budget = um.get_compute_budget()
        total = budget['core_pct'] + budget['peer_pct'] + budget['full_scan_pct']
        assert total == 100, f"Budget must sum to 100, got {total}"

    def test_compute_budget_core_is_70(self):
        from uk_isa.universe_manager import get_universe_manager
        um = get_universe_manager()
        budget = um.get_compute_budget()
        assert budget['core_pct'] == 70

    def test_tier_classification(self):
        from uk_isa.universe_manager import get_universe_manager
        um = get_universe_manager()
        # CORE tickers
        assert um.get_tier("QQQ3.L") == "CORE"
        assert um.get_tier("NVD3.L") == "CORE"
        assert um.get_tier("SP5L.L") == "CORE"
        # FULL_SCAN tickers
        assert um.get_tier("SPY") == "FULL_SCAN"
        assert um.get_tier("^VIX") == "FULL_SCAN"
        # Unknown
        assert um.get_tier("RANDOM.L") == "UNKNOWN"

    def test_all_tradable_is_core_only(self):
        """CRITICAL: Only CORE instruments can generate TRADE signals."""
        from uk_isa.universe_manager import get_universe_manager
        um = get_universe_manager()
        tradable = um.all_tradable
        for t in tradable:
            assert um.get_tier(t) == "CORE", f"{t} is tradable but tier={um.get_tier(t)}"

    def test_no_peer_in_core(self):
        """CRITICAL: No peer ticker should appear in CORE list."""
        from uk_isa.universe_manager import get_universe_manager
        um = get_universe_manager()
        core_set = set(um.core_list)
        for p in um.peer_list:
            assert p not in core_set, f"PEER {p} found in CORE list!"

    def test_no_full_scan_in_core(self):
        from uk_isa.universe_manager import get_universe_manager
        um = get_universe_manager()
        core_set = set(um.core_list)
        for t in um.full_scan_list:
            assert t not in core_set, f"FULL_SCAN {t} found in CORE list!"

    def test_scan_cadence(self):
        from uk_isa.universe_manager import get_universe_manager
        um = get_universe_manager()
        cadence = um.get_scan_cadence()
        # CORE must be fastest
        assert cadence['core_interval_seconds'] <= cadence['peer_interval_seconds']
        assert cadence['peer_interval_seconds'] <= cadence['full_scan_interval_seconds']

    def test_write_universe_artifacts(self, tmp_path):
        from uk_isa.universe_manager import get_universe_manager
        um = get_universe_manager()
        # Monkey-patch _PROJECT_ROOT (used internally for artifact paths)
        import uk_isa.universe_manager as um_mod
        original = um_mod._PROJECT_ROOT
        um_mod._PROJECT_ROOT = tmp_path
        try:
            um.write_universe_artifacts("2026-01-01")
            core_f = tmp_path / "artifacts" / "2026-01-01" / "universe" / "core.json"
            assert core_f.exists(), "core.json not written"
            data = json.loads(core_f.read_text())
            assert len(data['tickers']) == 12
        finally:
            um_mod._PROJECT_ROOT = original


class TestPeerFinder:
    """PeerFinder must select correct number of peers."""

    def test_peer_finder_imports(self):
        from uk_isa.peer_finder import PeerFinder, PeerMatch
        assert PeerFinder is not None
        assert PeerMatch is not None

    def test_peer_match_dataclass(self):
        from uk_isa.peer_finder import PeerMatch
        pm = PeerMatch(
            ticker="AMD3.L",
            similarity_score=0.85,
            similarity_method="correlation",
            core_parent="3SEM.L",
            factor_group="semiconductors",
        )
        assert pm.tier == "PEER"
        assert pm.tradable == True
        d = pm.to_dict()
        assert d['ticker'] == "AMD3.L"
        assert d['tier'] == "PEER"

    def test_default_candidates_excludes_core(self):
        from uk_isa.peer_finder import default_candidates
        from uk_isa.isa_universe import CORE_UNIVERSE
        candidates = default_candidates()
        core_set = set(CORE_UNIVERSE)
        for c in candidates:
            assert c not in core_set, f"Candidate {c} is in CORE!"


class TestTieredPipeline:
    """TieredPipelineResult must enforce tier separation."""

    def test_tiered_pipeline_result_imports(self):
        from signal_engine.pipeline_runner import TieredPipelineResult
        assert TieredPipelineResult is not None

    def test_tiered_pipeline_result_defaults(self):
        from signal_engine.pipeline_runner import TieredPipelineResult, PipelineResult
        core = PipelineResult(session="TEST", run_id="test-001")
        result = TieredPipelineResult(core_result=core)
        assert result.peer_plays == []
        assert result.full_scan_cards == []
        assert result.universe_sizes == {}
        assert result.compute_time_ms == {}


class TestTierSeparation:
    """CRITICAL: Tiers must never leak into each other."""

    def test_core_known_tickers(self):
        """All CORE tickers must be known ISA instruments."""
        from uk_isa.universe_manager import get_universe_manager
        um = get_universe_manager()
        for t in um.core_list:
            assert t.endswith('.L'), f"CORE ticker {t} must be LSE (.L suffix)"

    def test_full_scan_not_tradable(self):
        """Full scan instruments must never be in tradable set."""
        from uk_isa.universe_manager import get_universe_manager
        um = get_universe_manager()
        tradable_set = set(um.all_tradable)
        for t in um.full_scan_list:
            assert t not in tradable_set, f"FULL_SCAN {t} in tradable set!"
