"""FinBERT Sentiment Provider — financial-domain NLP sentiment scoring.

Runs FinBERT locally for sub-10ms sentiment classification of:
  - SEC 8-K filing text
  - News headlines from NewsAPI/Finnhub
  - Gemini scanner summaries

Output: {positive, negative, neutral} scores per text input.
Feeds into event_calendar.py for catalyst weighting and bridge.py confidence modifier.

License: FinBERT model weights are MIT (ProsusAI/finBERT).
         transformers library is Apache 2.0.

NOTE: FinBERT model is ~440MB. First run downloads from HuggingFace.
      For Docker: pre-download during image build or mount a volume.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger("sentiment_provider")

# Lazy-loaded model
_model = None
_tokenizer = None
_device = "cpu"

_MODEL_NAME = "ProsusAI/finbert"


def _load_model():
    """Load FinBERT model and tokenizer (lazy, cached)."""
    global _model, _tokenizer

    if _model is not None:
        return True

    try:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        import torch

        log.info("Loading FinBERT model from %s...", _MODEL_NAME)
        _tokenizer = AutoTokenizer.from_pretrained(_MODEL_NAME)
        _model = AutoModelForSequenceClassification.from_pretrained(_MODEL_NAME)
        _model.eval()  # Set to inference mode
        log.info("FinBERT loaded successfully (%s)", _device)
        return True
    except ImportError:
        log.warning("transformers not installed — pip install transformers torch")
        return False
    except Exception as e:
        log.error("Failed to load FinBERT: %s", str(e)[:200])
        return False


def score_text(text: str) -> Optional[Dict[str, float]]:
    """Score a single text for financial sentiment.

    Args:
        text: Financial text (news headline, filing excerpt, etc.)

    Returns:
        Dict with {positive, negative, neutral} probabilities, or None on failure.
    """
    if not _load_model():
        return None

    try:
        import torch

        inputs = _tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
        with torch.no_grad():
            outputs = _model(**inputs)

        probs = torch.nn.functional.softmax(outputs.logits, dim=-1)[0]

        # FinBERT labels: positive, negative, neutral
        labels = ["positive", "negative", "neutral"]
        scores = {label: float(probs[i]) for i, label in enumerate(labels)}

        return scores
    except Exception as e:
        log.warning("Sentiment scoring failed: %s", str(e)[:100])
        return None


def score_batch(texts: List[str], batch_size: int = 16) -> List[Optional[Dict[str, float]]]:
    """Score multiple texts efficiently in batches."""
    if not _load_model():
        return [None] * len(texts)

    results = []
    try:
        import torch

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            inputs = _tokenizer(
                batch, return_tensors="pt", truncation=True,
                max_length=512, padding=True,
            )
            with torch.no_grad():
                outputs = _model(**inputs)

            probs = torch.nn.functional.softmax(outputs.logits, dim=-1)

            labels = ["positive", "negative", "neutral"]
            for j in range(len(batch)):
                scores = {label: float(probs[j][k]) for k, label in enumerate(labels)}
                results.append(scores)

    except Exception as e:
        log.error("Batch sentiment scoring failed: %s", str(e)[:200])
        results.extend([None] * (len(texts) - len(results)))

    return results


def classify_sentiment(scores: Dict[str, float]) -> str:
    """Classify sentiment scores into a single label."""
    if not scores:
        return "neutral"
    return max(scores, key=scores.get)


def compute_composite_score(scores: Dict[str, float]) -> float:
    """Compute a single composite sentiment score [-1.0, +1.0].

    +1.0 = extremely positive, -1.0 = extremely negative, 0.0 = neutral.
    """
    if not scores:
        return 0.0
    return scores.get("positive", 0) - scores.get("negative", 0)


def score_sec_filings(filings_dir: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Score all SEC filings in the filings directory.

    Returns dict of {ticker: {avg_sentiment, filings_scored, composite_score}}
    """
    if filings_dir is None:
        filings_dir = os.environ.get("AEGIS_DATA_DIR", "/app/data") + "/sec_filings"

    summary_path = os.path.join(filings_dir, "filings_summary.json")
    try:
        with open(summary_path) as f:
            summary = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    results = {}
    for ticker, filings in summary.get("filings", {}).items():
        texts = [f.get("preview", "") for f in filings if f.get("preview")]
        if not texts:
            continue

        scores = score_batch(texts)
        valid_scores = [s for s in scores if s is not None]
        if not valid_scores:
            continue

        avg_composite = sum(compute_composite_score(s) for s in valid_scores) / len(valid_scores)
        avg_positive = sum(s.get("positive", 0) for s in valid_scores) / len(valid_scores)
        avg_negative = sum(s.get("negative", 0) for s in valid_scores) / len(valid_scores)

        results[ticker] = {
            "composite_score": avg_composite,
            "avg_positive": avg_positive,
            "avg_negative": avg_negative,
            "filings_scored": len(valid_scores),
            "classification": "positive" if avg_composite > 0.1 else "negative" if avg_composite < -0.1 else "neutral",
        }
        log.info("  %s: composite=%.3f (%s, %d filings)",
                 ticker, avg_composite, results[ticker]["classification"], len(valid_scores))

    # Write sentiment summary
    output_path = os.path.join(filings_dir, "sentiment_summary.json")
    with open(output_path, "w") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tickers": results,
        }, f, indent=2)

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [Sentiment] %(levelname)s %(message)s")

    # Quick test
    test_texts = [
        "Apple reported record quarterly revenue, beating analyst expectations by 15%",
        "The company faces severe regulatory headwinds and declining margins",
        "Trading volume was average with no significant price movement",
    ]

    for text in test_texts:
        scores = score_text(text)
        if scores:
            label = classify_sentiment(scores)
            composite = compute_composite_score(scores)
            print(f"  [{label:>8}] ({composite:+.3f}) {text[:60]}...")
        else:
            print(f"  [FAILED] {text[:60]}...")
