"""LLM Output Validator — Pydantic schema enforcement for Gemini/Claude outputs.

Uses Instructor to guarantee that LLM outputs are structurally valid before
they reach config_writer.py or any config file the Rust engine reads.

Prevents: hallucinated TOML syntax, out-of-bounds parameters, invalid types.

Consumers:
  - config_writer.py: validate Gemini parameter suggestions
  - claude/dispatcher.py: validate Claude daily decisions
  - gemini_scanner.py: validate universe scan results
  - Any future LLM call in the system

License: Instructor is MIT, Pydantic is MIT.
"""

from __future__ import annotations

import json
import logging
import os
from enum import Enum
from typing import Any, Dict, List, Optional

log = logging.getLogger("llm_validator")

try:
    from pydantic import BaseModel, Field, field_validator
    _HAS_PYDANTIC = True
except ImportError:
    _HAS_PYDANTIC = False
    log.warning("pydantic not installed — LLM output validation disabled")

try:
    import instructor
    _HAS_INSTRUCTOR = True
except ImportError:
    _HAS_INSTRUCTOR = False
    log.warning("instructor not installed — structured LLM output disabled")


# ── Pydantic Schemas for LLM Output Validation ──

if _HAS_PYDANTIC:

    class StrategyWeightRecommendation(BaseModel):
        """Schema for Gemini/Claude strategy weight recommendations."""
        heat_limit: float = Field(ge=5.0, le=10.0, description="Portfolio heat limit (%)")
        chandelier_atr_mult: float = Field(ge=1.5, le=3.0, description="Chandelier ATR multiplier")
        confidence_floor: float = Field(ge=0.55, le=0.80, description="Minimum entry confidence")
        kelly_cap: Optional[float] = Field(default=None, ge=0.005, le=0.05, description="Kelly fraction cap")
        spread_veto_pct: Optional[float] = Field(default=None, ge=0.05, le=0.50, description="Spread veto threshold (%)")
        reasoning: Optional[str] = Field(default=None, max_length=500)

    class EntryTypeConfidence(BaseModel):
        """Schema for per-entry-type confidence adjustments."""
        type_a_confidence: float = Field(ge=55.0, le=90.0)
        type_b_confidence: float = Field(ge=55.0, le=90.0)
        type_c_confidence: float = Field(ge=55.0, le=90.0)
        type_d_confidence: float = Field(ge=55.0, le=90.0)

    class RegimeClassification(BaseModel):
        """Schema for LLM regime classification."""
        regime: str = Field(pattern="^(CRISIS|STEADY|WOI|INFLATION)$")
        confidence: float = Field(ge=0.0, le=1.0)
        reasoning: Optional[str] = Field(default=None, max_length=300)

    class UniverseScanResult(BaseModel):
        """Schema for Gemini universe scan output."""
        add_tickers: List[str] = Field(default_factory=list, max_length=20)
        remove_tickers: List[str] = Field(default_factory=list, max_length=20)
        tier_changes: Dict[str, str] = Field(default_factory=dict)
        confidence: float = Field(ge=0.0, le=1.0)
        reasoning: Optional[str] = Field(default=None, max_length=500)

        @field_validator("add_tickers", "remove_tickers")
        @classmethod
        def validate_tickers(cls, v):
            return [t.upper().strip() for t in v if t.strip()]

    class DailyDecision(BaseModel):
        """Schema for Claude daily trading decision."""
        action: str = Field(pattern="^(PROCEED|REDUCE|HALT|PAUSE)$")
        risk_adjustment: float = Field(ge=0.0, le=2.0, description="Multiplier for risk params")
        strategies_to_disable: List[str] = Field(default_factory=list)
        reasoning: str = Field(max_length=500)

    class NightlyReview(BaseModel):
        """Schema for Claude forensic review output."""
        overall_assessment: str = Field(pattern="^(EXCELLENT|GOOD|ACCEPTABLE|POOR|CRITICAL)$")
        sharpe_estimate: float = Field(ge=-5.0, le=10.0)
        top_issues: List[str] = Field(default_factory=list, max_length=5)
        recommended_changes: Dict[str, Any] = Field(default_factory=dict)
        reasoning: str = Field(max_length=1000)


def validate_strategy_weights(raw_output: dict) -> Optional[dict]:
    """Validate LLM strategy weight recommendation against schema.

    Args:
        raw_output: Raw dict from LLM response

    Returns:
        Validated dict or None if validation fails.
    """
    if not _HAS_PYDANTIC:
        log.warning("Pydantic unavailable — returning raw output without validation")
        return raw_output

    try:
        validated = StrategyWeightRecommendation(**raw_output)
        return validated.model_dump(exclude_none=True)
    except Exception as e:
        log.error("Strategy weight validation FAILED: %s — raw: %s", str(e)[:200], str(raw_output)[:200])
        return None


def validate_regime_classification(raw_output: dict) -> Optional[dict]:
    """Validate LLM regime classification."""
    if not _HAS_PYDANTIC:
        return raw_output
    try:
        validated = RegimeClassification(**raw_output)
        return validated.model_dump(exclude_none=True)
    except Exception as e:
        log.error("Regime classification validation FAILED: %s", str(e)[:200])
        return None


def validate_universe_scan(raw_output: dict) -> Optional[dict]:
    """Validate Gemini universe scan output."""
    if not _HAS_PYDANTIC:
        return raw_output
    try:
        validated = UniverseScanResult(**raw_output)
        return validated.model_dump(exclude_none=True)
    except Exception as e:
        log.error("Universe scan validation FAILED: %s", str(e)[:200])
        return None


def validate_daily_decision(raw_output: dict) -> Optional[dict]:
    """Validate Claude daily decision."""
    if not _HAS_PYDANTIC:
        return raw_output
    try:
        validated = DailyDecision(**raw_output)
        return validated.model_dump(exclude_none=True)
    except Exception as e:
        log.error("Daily decision validation FAILED: %s", str(e)[:200])
        return None


def validate_nightly_review(raw_output: dict) -> Optional[dict]:
    """Validate Claude nightly review."""
    if not _HAS_PYDANTIC:
        return raw_output
    try:
        validated = NightlyReview(**raw_output)
        return validated.model_dump(exclude_none=True)
    except Exception as e:
        log.error("Nightly review validation FAILED: %s", str(e)[:200])
        return None


def patch_gemini_client(api_key: Optional[str] = None):
    """Patch the Gemini client with Instructor for structured output.

    Usage:
        client = patch_gemini_client()
        result = client.chat.completions.create(
            model="gemini-2.5-flash",
            response_model=StrategyWeightRecommendation,
            messages=[{"role": "user", "content": "..."}],
        )
    """
    if not _HAS_INSTRUCTOR:
        log.warning("Instructor not installed — cannot patch Gemini client")
        return None

    if api_key is None:
        api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        log.warning("GEMINI_API_KEY not set")
        return None

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        client = instructor.from_gemini(
            client=genai.GenerativeModel("gemini-2.5-flash"),
            mode=instructor.Mode.GEMINI_JSON,
        )
        log.info("Gemini client patched with Instructor (structured output enabled)")
        return client
    except Exception as e:
        log.error("Failed to patch Gemini client: %s", str(e)[:200])
        return None
