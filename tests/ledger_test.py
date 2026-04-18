"""Field Consumption Ledger test. Validates that the ledger parses and structural rules hold."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "docs" / "FIELD_CONSUMPTION_LEDGER.md"


def test_ledger_exists():
    assert LEDGER.exists(), "FIELD_CONSUMPTION_LEDGER.md must exist"


def test_ledger_has_ibkr_fields():
    txt = LEDGER.read_text()
    for required in ["bid", "ask", "last", "volume", "bid_size", "ask_size"]:
        assert required in txt, f"missing IBKR field {required}"


def test_ledger_has_llm_outputs():
    txt = LEDGER.read_text()
    for required in ["llm_conviction", "llm_regime_label", "llm_news_sentiment"]:
        assert required in txt, f"missing LLM output {required}"
