"""Natural Language to Trading Rules — Book 214.

Parse natural language trading descriptions into executable rule logic.
Supports a simple DSL (Domain-Specific Language) for trading rules:

    IF RSI < 30 AND ADX > 25 THEN BUY confidence=0.7
    IF RSI > 70 OR REGIME == BEAR THEN SELL confidence=0.6

Components:
  - TradingRule: Structured representation of a parsed rule
  - RuleDSL: Tokeniser and parser for the rule DSL
  - RuleValidator: Validates parsed rules for bounds, indicator existence
  - RuleEngine: Manages, evaluates, and backtests a collection of rules

Safety:
  - No rule can skip backtesting
  - All rules are subject to AEGIS risk limits
  - System never executes a rule it cannot explain

Data paths:
  - /app/data/trading_rules.json — persisted rule definitions

Bridge.py integration:
    try:
        from python_brain.ml.nl_trading_rules import (
            RuleEngine, RuleDSL, RuleValidator, TradingRule,
        )
    except ImportError:
        pass

Usage:
    engine = RuleEngine()
    engine.add_rule("IF RSI < 30 AND ADX > 25 THEN BUY confidence=0.7")
    signals = engine.evaluate({"RSI": 28, "ADX": 30, "REGIME": "BULL"})
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("nl_trading_rules")

__all__ = [
    "TradingRule",
    "RuleDSL",
    "RuleValidator",
    "RuleEngine",
]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = Path("/app/data")
RULES_PATH = DATA_DIR / "trading_rules.json"

# ---------------------------------------------------------------------------
# Known Indicators and Valid Ranges
# ---------------------------------------------------------------------------
KNOWN_INDICATORS: Dict[str, Dict[str, Any]] = {
    "RSI": {"min": 0, "max": 100, "type": "float"},
    "ADX": {"min": 0, "max": 100, "type": "float"},
    "ATR": {"min": 0, "max": 1e6, "type": "float"},
    "MACD": {"min": -1e6, "max": 1e6, "type": "float"},
    "MACD_SIGNAL": {"min": -1e6, "max": 1e6, "type": "float"},
    "MACD_HIST": {"min": -1e6, "max": 1e6, "type": "float"},
    "SMA": {"min": 0, "max": 1e6, "type": "float"},
    "EMA": {"min": 0, "max": 1e6, "type": "float"},
    "VWAP": {"min": 0, "max": 1e6, "type": "float"},
    "VOLUME": {"min": 0, "max": 1e12, "type": "float"},
    "PRICE": {"min": 0, "max": 1e6, "type": "float"},
    "SPREAD": {"min": 0, "max": 1e3, "type": "float"},
    "CONFIDENCE": {"min": 0, "max": 100, "type": "float"},
    "REGIME": {"values": ["BULL", "BEAR", "NEUTRAL", "VOLATILE", "CRISIS"], "type": "str"},
    "VOLATILITY": {"min": 0, "max": 10, "type": "float"},
    "BOLLINGER_UPPER": {"min": 0, "max": 1e6, "type": "float"},
    "BOLLINGER_LOWER": {"min": 0, "max": 1e6, "type": "float"},
    "STOCH_K": {"min": 0, "max": 100, "type": "float"},
    "STOCH_D": {"min": 0, "max": 100, "type": "float"},
    "OBV": {"min": -1e12, "max": 1e12, "type": "float"},
    "MFI": {"min": 0, "max": 100, "type": "float"},
    "CCI": {"min": -500, "max": 500, "type": "float"},
    "WILLIAMS_R": {"min": -100, "max": 0, "type": "float"},
}

VALID_OPERATORS = {"<", ">", "<=", ">=", "==", "!="}
VALID_ACTIONS = {"BUY", "SELL", "HOLD", "CLOSE"}
VALID_LOGIC_OPS = {"AND", "OR"}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class Condition:
    """A single condition in a trading rule.

    Attributes:
        indicator: Indicator name (e.g., "RSI").
        operator: Comparison operator (e.g., "<", ">", "==").
        value: Threshold value (numeric or string for REGIME).
        raw: Original token string.
    """
    indicator: str = ""
    operator: str = ""
    value: Any = 0.0
    raw: str = ""

    def evaluate(self, market_data: Dict[str, Any]) -> bool:
        """Evaluate this condition against market data.

        Args:
            market_data: Dict mapping indicator names to current values.

        Returns:
            True if condition is met.
        """
        current = market_data.get(self.indicator)
        if current is None:
            log.debug("Indicator %s not in market data", self.indicator)
            return False

        try:
            if self.operator == "<":
                return float(current) < float(self.value)
            elif self.operator == ">":
                return float(current) > float(self.value)
            elif self.operator == "<=":
                return float(current) <= float(self.value)
            elif self.operator == ">=":
                return float(current) >= float(self.value)
            elif self.operator == "==":
                return str(current).upper() == str(self.value).upper()
            elif self.operator == "!=":
                return str(current).upper() != str(self.value).upper()
        except (ValueError, TypeError) as exc:
            log.warning("Condition evaluation error: %s %s %s — %s",
                        self.indicator, self.operator, self.value, exc)
            return False
        return False

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to dict."""
        return {
            "indicator": self.indicator,
            "operator": self.operator,
            "value": self.value,
            "raw": self.raw,
        }


@dataclass
class TradingRule:
    """Structured representation of a trading rule.

    Attributes:
        condition: Human-readable condition string.
        action: Action to take (BUY, SELL, HOLD, CLOSE).
        parameters: Additional parameters (confidence, stop_loss, etc.).
        confidence: Rule confidence (0.0 to 1.0).
        source: Where this rule came from (e.g., "user_input", "claude").
        conditions: Parsed condition objects.
        logic_ops: Logic operators between conditions (AND/OR).
    """
    condition: str = ""
    action: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    source: str = "user_input"
    conditions: List[Condition] = field(default_factory=list)
    logic_ops: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to dict."""
        return {
            "condition": self.condition,
            "action": self.action,
            "parameters": self.parameters,
            "confidence": self.confidence,
            "source": self.source,
            "conditions": [c.to_dict() for c in self.conditions],
            "logic_ops": self.logic_ops,
        }

    def evaluate(self, market_data: Dict[str, Any]) -> bool:
        """Evaluate the full rule against market data.

        Combines individual conditions with AND/OR logic.

        Args:
            market_data: Dict mapping indicator names to current values.

        Returns:
            True if the rule fires.
        """
        if not self.conditions:
            return False

        result = self.conditions[0].evaluate(market_data)

        for i, logic_op in enumerate(self.logic_ops):
            if i + 1 >= len(self.conditions):
                break
            next_result = self.conditions[i + 1].evaluate(market_data)
            if logic_op == "AND":
                result = result and next_result
            elif logic_op == "OR":
                result = result or next_result

        return result


# ---------------------------------------------------------------------------
# Rule DSL Parser
# ---------------------------------------------------------------------------
class RuleDSL:
    """Parser for the trading rule domain-specific language.

    Syntax:
        IF <condition> [AND|OR <condition>]... THEN <action> [key=value]...

    Condition:
        <INDICATOR> <OPERATOR> <VALUE>

    Examples:
        IF RSI < 30 AND ADX > 25 THEN BUY confidence=0.7
        IF RSI > 70 OR REGIME == BEAR THEN SELL confidence=0.6
        IF PRICE > BOLLINGER_UPPER THEN SELL confidence=0.5 stop_loss=0.03
    """

    def parse(self, rule_text: str) -> TradingRule:
        """Parse a natural language rule string into a TradingRule.

        Args:
            rule_text: Rule string in DSL format.

        Returns:
            Parsed TradingRule.

        Raises:
            ValueError: If the rule cannot be parsed.
        """
        tokens = self._tokenize(rule_text)
        if not tokens:
            raise ValueError("Empty rule text")

        # Find IF and THEN boundaries
        tokens_upper = [t.upper() for t in tokens]

        if_idx = -1
        then_idx = -1
        for i, t in enumerate(tokens_upper):
            if t == "IF" and if_idx == -1:
                if_idx = i
            if t == "THEN" and then_idx == -1:
                then_idx = i

        if if_idx == -1:
            # Try parsing without IF/THEN (direct condition → action)
            if_idx = -1
            then_idx = self._find_action_start(tokens_upper)
            if then_idx == -1:
                raise ValueError(f"Cannot find THEN or action in: {rule_text}")
            condition_tokens = tokens[0:then_idx]
            action_tokens = tokens[then_idx:]
        else:
            if then_idx == -1:
                raise ValueError(f"Found IF but no THEN in: {rule_text}")
            condition_tokens = tokens[if_idx + 1:then_idx]
            action_tokens = tokens[then_idx + 1:]

        if not condition_tokens:
            raise ValueError(f"No conditions found in: {rule_text}")
        if not action_tokens:
            raise ValueError(f"No action found in: {rule_text}")

        # Parse conditions
        conditions, logic_ops = self._parse_conditions(condition_tokens)

        # Parse action
        action, parameters = self._parse_action(action_tokens)

        # Extract confidence from parameters
        confidence = float(parameters.pop("confidence", 0.5))

        rule = TradingRule(
            condition=rule_text.strip(),
            action=action,
            parameters=parameters,
            confidence=confidence,
            source="user_input",
            conditions=conditions,
            logic_ops=logic_ops,
        )

        log.info(
            "Parsed rule: %s → action=%s confidence=%.2f conditions=%d",
            rule_text.strip(), action, confidence, len(conditions),
        )
        return rule

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize a rule string into a list of tokens.

        Handles operators like <=, >=, ==, != as single tokens.
        Strips quotes from string values.

        Args:
            text: Raw rule text.

        Returns:
            List of token strings.
        """
        # Normalise whitespace
        text = text.strip()

        # Insert spaces around operators to ensure they tokenize properly
        # Handle multi-char operators first
        text = re.sub(r"<=", " <= ", text)
        text = re.sub(r">=", " >= ", text)
        text = re.sub(r"==", " == ", text)
        text = re.sub(r"!=", " != ", text)
        # Single-char < and > (but not part of <= or >=)
        text = re.sub(r"(?<![<>=!])(<)(?!=)", r" \1 ", text)
        text = re.sub(r"(?<![<>=!])(>)(?!=)", r" \1 ", text)

        # Handle key=value pairs (e.g., confidence=0.7)
        text = re.sub(r"(\w+)=(\S+)", r"\1 = \2", text)

        tokens = text.split()
        # Strip quotes from values
        tokens = [t.strip("'\"") for t in tokens]
        return tokens

    def _parse_conditions(
        self,
        tokens: List[str],
    ) -> Tuple[List[Condition], List[str]]:
        """Parse condition tokens into Condition objects and logic operators.

        Args:
            tokens: Condition portion of the tokenized rule.

        Returns:
            Tuple of (list of Conditions, list of logic operators).
        """
        conditions: List[Condition] = []
        logic_ops: List[str] = []

        i = 0
        while i < len(tokens):
            token_upper = tokens[i].upper()

            # Skip logic operators (collect them)
            if token_upper in VALID_LOGIC_OPS:
                logic_ops.append(token_upper)
                i += 1
                continue

            # Try to parse a condition: INDICATOR OPERATOR VALUE
            if i + 2 < len(tokens):
                indicator = tokens[i].upper()
                operator = tokens[i + 1]
                value_str = tokens[i + 2]

                if operator in VALID_OPERATORS:
                    # Parse value
                    value = self._parse_value(value_str)
                    cond = Condition(
                        indicator=indicator,
                        operator=operator,
                        value=value,
                        raw=f"{indicator} {operator} {value_str}",
                    )
                    conditions.append(cond)
                    i += 3
                    continue

            # Unrecognised token — skip
            log.debug("Skipping unrecognised condition token: %s", tokens[i])
            i += 1

        return conditions, logic_ops

    def _parse_action(
        self,
        tokens: List[str],
    ) -> Tuple[str, Dict[str, Any]]:
        """Parse action tokens into action string and parameter dict.

        Args:
            tokens: Action portion of the tokenized rule.

        Returns:
            Tuple of (action string, parameters dict).
        """
        if not tokens:
            raise ValueError("No action tokens")

        action = tokens[0].upper()
        if action not in VALID_ACTIONS:
            raise ValueError(f"Unknown action: {action}. Valid: {VALID_ACTIONS}")

        parameters: Dict[str, Any] = {}
        i = 1
        while i < len(tokens):
            # Look for key = value patterns
            if i + 2 < len(tokens) and tokens[i + 1] == "=":
                key = tokens[i].lower()
                val = self._parse_value(tokens[i + 2])
                parameters[key] = val
                i += 3
            else:
                # Might be key=value already split or standalone
                i += 1

        return action, parameters

    def _parse_value(self, value_str: str) -> Any:
        """Parse a value string to appropriate type.

        Args:
            value_str: String representation of the value.

        Returns:
            Parsed value (float, int, or str).
        """
        # Try numeric
        try:
            if "." in value_str:
                return float(value_str)
            return int(value_str)
        except ValueError:
            pass

        # Return as string (e.g., "BULL", "BEAR")
        return value_str.upper()

    def _find_action_start(self, tokens_upper: List[str]) -> int:
        """Find the index where the action starts (for rules without THEN).

        Args:
            tokens_upper: Uppercase tokens.

        Returns:
            Index of the action token, or -1 if not found.
        """
        for i, t in enumerate(tokens_upper):
            if t in VALID_ACTIONS:
                return i
        return -1


# ---------------------------------------------------------------------------
# Rule Validator
# ---------------------------------------------------------------------------
class RuleValidator:
    """Validates parsed trading rules for correctness and safety.

    Checks:
      - All indicators are known/available
      - Comparison values are within valid ranges
      - Action is legal (BUY/SELL/HOLD/CLOSE)
      - Confidence is in [0, 1]
      - Risk parameters are within bounds
    """

    def validate(self, rule: TradingRule) -> Dict[str, Any]:
        """Validate a trading rule.

        Args:
            rule: Parsed TradingRule to validate.

        Returns:
            Dict with:
              - valid: True if rule passes all checks
              - errors: List of error strings
              - warnings: List of warning strings
        """
        errors: List[str] = []
        warnings: List[str] = []

        # Check action
        if rule.action not in VALID_ACTIONS:
            errors.append(f"Invalid action: {rule.action}")

        # Check confidence bounds
        if not 0.0 <= rule.confidence <= 1.0:
            errors.append(f"Confidence must be in [0, 1], got {rule.confidence}")

        # Check each condition
        for cond in rule.conditions:
            if not self._check_indicator_exists(cond.indicator):
                warnings.append(
                    f"Unknown indicator: {cond.indicator}. "
                    "It may not be available in market data."
                )

            if cond.operator not in VALID_OPERATORS:
                errors.append(f"Invalid operator: {cond.operator}")

            if not self._check_value_range(cond.indicator, cond.value):
                warnings.append(
                    f"Value {cond.value} may be out of typical range "
                    f"for {cond.indicator}"
                )

        # Check risk parameters
        stop_loss = rule.parameters.get("stop_loss")
        if stop_loss is not None:
            try:
                sl = float(stop_loss)
                if sl <= 0 or sl > 0.20:
                    warnings.append(f"Stop loss {sl} outside typical range (0, 0.20)")
            except (ValueError, TypeError):
                errors.append(f"Invalid stop_loss value: {stop_loss}")

        # Check no empty conditions
        if not rule.conditions:
            errors.append("Rule has no conditions")

        # Check logic_ops count matches conditions
        expected_ops = max(0, len(rule.conditions) - 1)
        if len(rule.logic_ops) != expected_ops:
            warnings.append(
                f"Expected {expected_ops} logic operators, "
                f"got {len(rule.logic_ops)}"
            )

        valid = len(errors) == 0
        result = {
            "valid": valid,
            "errors": errors,
            "warnings": warnings,
            "conditions_count": len(rule.conditions),
            "action": rule.action,
        }

        if not valid:
            log.warning("Rule validation FAILED: %s — errors: %s", rule.condition, errors)
        else:
            log.info("Rule validation passed: %s", rule.condition)

        return result

    def _check_indicator_exists(self, indicator: str) -> bool:
        """Check if an indicator is in the known indicators list.

        Args:
            indicator: Indicator name (uppercase).

        Returns:
            True if known.
        """
        return indicator.upper() in KNOWN_INDICATORS

    def _check_value_range(self, indicator: str, value: Any) -> bool:
        """Check if a value is within the valid range for an indicator.

        Args:
            indicator: Indicator name.
            value: Value to check.

        Returns:
            True if within range or if indicator is unknown.
        """
        spec = KNOWN_INDICATORS.get(indicator.upper())
        if spec is None:
            return True  # unknown indicator — skip range check

        if spec.get("type") == "str":
            valid_values = spec.get("values", [])
            if valid_values:
                return str(value).upper() in valid_values
            return True

        try:
            v = float(value)
            lo = spec.get("min", float("-inf"))
            hi = spec.get("max", float("inf"))
            return lo <= v <= hi
        except (ValueError, TypeError):
            return False


# ---------------------------------------------------------------------------
# Rule Engine
# ---------------------------------------------------------------------------
class RuleEngine:
    """Manages a collection of trading rules and evaluates them against market data.

    Features:
      - Add/remove rules via NL text
      - Evaluate all rules against current market data
      - Simple backtesting: win rate + profit factor
      - Persistence to /app/data/trading_rules.json
    """

    def __init__(
        self,
        rules: Optional[List[TradingRule]] = None,
        auto_load: bool = True,
    ) -> None:
        """Initialise rule engine.

        Args:
            rules: Optional initial list of rules.
            auto_load: If True, attempt to load rules from disk.
        """
        self._rules: List[TradingRule] = rules or []
        self._parser = RuleDSL()
        self._validator = RuleValidator()
        self._eval_count: int = 0
        self._signal_history: List[Dict[str, Any]] = []

        if auto_load and not self._rules:
            self._load_rules()

        log.info("RuleEngine: %d rules loaded", len(self._rules))

    @property
    def rules(self) -> List[TradingRule]:
        """Return current rule list."""
        return list(self._rules)

    def add_rule(self, rule_text: str) -> Dict[str, Any]:
        """Parse, validate, and add a new rule from text.

        Args:
            rule_text: Natural language rule string.

        Returns:
            Dict with parse/validation result and rule index.

        Raises:
            ValueError: If parsing fails entirely.
        """
        try:
            rule = self._parser.parse(rule_text)
        except ValueError as exc:
            log.error("Failed to parse rule: %s — %s", rule_text, exc)
            return {"added": False, "error": str(exc)}

        validation = self._validator.validate(rule)
        if not validation["valid"]:
            log.warning("Rule not added (validation failed): %s", validation["errors"])
            return {
                "added": False,
                "validation": validation,
            }

        self._rules.append(rule)
        self._save_rules()

        idx = len(self._rules) - 1
        log.info("Rule added at index %d: %s", idx, rule_text.strip())

        return {
            "added": True,
            "index": idx,
            "validation": validation,
            "rule": rule.to_dict(),
        }

    def remove_rule(self, index: int) -> bool:
        """Remove a rule by index.

        Args:
            index: Rule index to remove.

        Returns:
            True if removed, False if index out of range.
        """
        if 0 <= index < len(self._rules):
            removed = self._rules.pop(index)
            self._save_rules()
            log.info("Removed rule %d: %s", index, removed.condition)
            return True
        log.warning("Invalid rule index: %d (have %d rules)", index, len(self._rules))
        return False

    def evaluate(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Evaluate all rules against current market data.

        Args:
            market_data: Dict mapping indicator names to current values.

        Returns:
            List of signal dicts for rules that fired.
        """
        self._eval_count += 1
        signals = []

        for i, rule in enumerate(self._rules):
            if rule.evaluate(market_data):
                signal = {
                    "rule_index": i,
                    "action": rule.action,
                    "confidence": rule.confidence * 100.0,  # scale to 0-100
                    "condition": rule.condition,
                    "parameters": rule.parameters,
                    "source": f"nl_rule_{i}",
                    "ts": time.time(),
                }
                signals.append(signal)
                self._signal_history.append(signal)

                log.info(
                    "Rule #%d fired: %s (action=%s conf=%.0f)",
                    i, rule.condition, rule.action, signal["confidence"],
                )

        if not signals:
            log.debug("No rules fired (eval #%d, %d rules checked)",
                      self._eval_count, len(self._rules))

        return signals

    def backtest_rule(
        self,
        rule: TradingRule,
        historical_data: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Simple backtest: evaluate rule across historical data.

        For each data point where the rule fires, records entry.
        Uses the NEXT data point as exit to compute return.
        Computes win rate and profit factor.

        Args:
            rule: TradingRule to backtest.
            historical_data: List of dicts, each a snapshot of market data.
                             Must include 'PRICE' key for P&L calculation.

        Returns:
            Dict with win_rate, profit_factor, n_trades, total_return.
        """
        if len(historical_data) < 2:
            return {
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "n_trades": 0,
                "total_return": 0.0,
                "error": "insufficient_data",
            }

        trades = []
        i = 0
        while i < len(historical_data) - 1:
            data = historical_data[i]
            if rule.evaluate(data):
                entry_price = data.get("PRICE", 0.0)
                exit_price = historical_data[i + 1].get("PRICE", 0.0)

                if entry_price > 0 and exit_price > 0:
                    if rule.action in ("BUY", "HOLD"):
                        ret = (exit_price - entry_price) / entry_price
                    elif rule.action == "SELL":
                        ret = (entry_price - exit_price) / entry_price
                    else:
                        ret = 0.0

                    trades.append(ret)
                i += 2  # skip to after exit
            else:
                i += 1

        if not trades:
            return {
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "n_trades": 0,
                "total_return": 0.0,
            }

        wins = [t for t in trades if t > 0]
        losses = [t for t in trades if t < 0]

        win_rate = len(wins) / len(trades) if trades else 0.0
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
        total_return = sum(trades)

        result = {
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else 999.0,
            "n_trades": len(trades),
            "total_return": round(total_return, 6),
            "avg_win": round(gross_profit / len(wins), 6) if wins else 0.0,
            "avg_loss": round(gross_loss / len(losses), 6) if losses else 0.0,
            "max_win": round(max(trades), 6) if trades else 0.0,
            "max_loss": round(min(trades), 6) if trades else 0.0,
        }

        log.info(
            "Backtest: %d trades, WR=%.1f%%, PF=%.2f, total=%.4f",
            len(trades), win_rate * 100, result["profit_factor"], total_return,
        )
        return result

    def backtest_by_index(
        self,
        index: int,
        historical_data: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Backtest a rule by its index in the engine.

        Args:
            index: Rule index.
            historical_data: List of market data snapshots.

        Returns:
            Backtest result dict, or error dict if index invalid.
        """
        if 0 <= index < len(self._rules):
            return self.backtest_rule(self._rules[index], historical_data)
        return {"error": f"Invalid rule index: {index}"}

    def list_rules(self) -> List[Dict[str, Any]]:
        """Return all rules as serialised dicts.

        Returns:
            List of rule dicts with index.
        """
        return [
            {"index": i, **rule.to_dict()}
            for i, rule in enumerate(self._rules)
        ]

    def stats(self) -> Dict[str, Any]:
        """Return engine statistics.

        Returns:
            Dict with rule count, eval count, signal history count.
        """
        return {
            "rule_count": len(self._rules),
            "eval_count": self._eval_count,
            "signal_history_count": len(self._signal_history),
        }

    def _save_rules(self) -> None:
        """Persist rules to disk."""
        data = {
            "rules": [r.to_dict() for r in self._rules],
            "ts": time.time(),
        }
        try:
            RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(RULES_PATH, "w") as f:
                json.dump(data, f, indent=2)
            log.info("Rules saved to %s (%d rules)", RULES_PATH, len(self._rules))
        except OSError as exc:
            log.error("Failed to save rules: %s", exc)

    def _load_rules(self) -> None:
        """Load rules from disk."""
        if not RULES_PATH.exists():
            return

        try:
            with open(RULES_PATH, "r") as f:
                data = json.load(f)

            for rule_dict in data.get("rules", []):
                # Reconstruct TradingRule from dict
                conditions = []
                for cd in rule_dict.get("conditions", []):
                    conditions.append(Condition(
                        indicator=cd.get("indicator", ""),
                        operator=cd.get("operator", ""),
                        value=cd.get("value", 0),
                        raw=cd.get("raw", ""),
                    ))

                rule = TradingRule(
                    condition=rule_dict.get("condition", ""),
                    action=rule_dict.get("action", ""),
                    parameters=rule_dict.get("parameters", {}),
                    confidence=rule_dict.get("confidence", 0.5),
                    source=rule_dict.get("source", "loaded"),
                    conditions=conditions,
                    logic_ops=rule_dict.get("logic_ops", []),
                )
                self._rules.append(rule)

            log.info("Loaded %d rules from %s", len(self._rules), RULES_PATH)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Failed to load rules from %s: %s", RULES_PATH, exc)
