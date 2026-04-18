"""Smoke test: every module imports without error."""
import importlib

MODULES = [
    "python_brain",
    "python_brain.server",
    "python_brain.core.nats_client",
    "python_brain.core.data_health",
    "python_brain.core.cost_governor",
    "python_brain.core.preference_logger",
    "python_brain.core.ab_harness",
    "python_brain.conviction_engine",
    "python_brain.portfolio_constructor",
    "python_brain.strategies.base",
    "python_brain.strategies.sentiment",
    "python_brain.strategies.filing_change",
    "python_brain.strategies.index_recon",
    "python_brain.strategies.earnings_pattern",
    "python_brain.strategies.overnight_return",
    "python_brain.strategies.ibs_mean_reversion",
    "python_brain.scanner.scanner",
    "python_brain.scanner.thompson",
    "python_brain.scanner.watchlist_publisher",
    "python_brain.intelligence.news_reactor",
    "python_brain.intelligence.earnings_whisper",
    "python_brain.intelligence.sec_scanner",
    "python_brain.intelligence.regime_council",
    "python_brain.intelligence.thesis_monitor",
    "python_brain.ouroboros.core",
    "python_brain.ouroboros.kelly_bayesian",
    "python_brain.ouroboros.chandelier_calibrate",
    "python_brain.ouroboros.alpha_decay",
    "python_brain.ouroboros.drift",
    "python_brain.ouroboros.demote_resurrect",
    "python_brain.ouroboros.learned_writer",
    "python_brain.ops.ig_financing",
    "python_brain.ops.isa_tax_year",
]


def test_imports():
    for m in MODULES:
        importlib.import_module(m)
