# AEGIS Master Plan v13.15 — Round 17: Architect's Feedback Amendment

**Author**: Claude Opus 4.6 (Executing Architect Persona)
**Date**: 2026-03-06
**Trigger**: Lead Systems Architect & Chief Quant verbal ruling on R15 Top 5 Critical Findings
**Status**: PLAN AMENDMENT — Awaiting ChatGPT supplementary feedback before code execution

---

## ARCHITECT'S RULING: SUMMARY

The Architect has reviewed R15's Top 5 Critical Findings and issued a binding ruling:

1. **AEGIS Master Plan v13.13 is LOCKED, SEALED, and ARCHIVED.** No further theoretical review rounds.
2. **The 8-Hour Priority Fix Order from R15 Part X is ratified** as the execution plan.
3. **Code execution is authorised** — the 63-day paper trading gauntlet begins after fixes land.
4. **Priority ordering is confirmed**: parameter fixes (seconds) before syntax fixes (minutes) before architectural fixes (hours).

---

## AMENDMENT 1: PRIORITY FIX ORDER (Ratified by Architect)

The Architect explicitly confirmed the following execution order. This supersedes all prior Phase A scheduling.

### IMMEDIATE EXECUTION BLOCK (Hours 1-2: "Weld the Metal")

| Priority | Bug ID | Fix | File:Line | Time | Impact |
|----------|--------|-----|-----------|------|--------|
| **#1** | GPT-111 | SessionProtection `0.015` → `0.025` | `risk_sizer.py:370` | 30s | 353x terminal wealth |
| **#2** | GPT-104 | List mutation → list comprehension | `main.py:1908-1929` | 15min | ML veto no longer skips signals |
| **#3** | GPT-102 | `should_retrain()` use `self._last_trained_at` | `main.py:5605` + `ml_meta_model.py:537` | 10min | ML model actually retrains |
| **#4** | GPT-55 | `asyncio.QueueFull` → `queue.Full` | `main.py:3081,4208,4437` + `tick_loop.py:1492` | 15min | Queue full = graceful warning, not crash |

**Total: ~45 minutes of coding.**

### HARDENING BLOCK (Hours 3-4)

| Priority | Bug ID | Fix | File:Line | Time |
|----------|--------|-----|-----------|------|
| **#5** | GPT-46 | VIX hysteresis proportional deadband | `regime_classifier.py:128-141` | 45min |
| **#6** | GPT-56 | Wire `decrement_transition_buffer()` | `regime_classifier.py:293` → call site in `main.py` | 30min |
| **#7** | GPT-58/103 | Fix `_REGIME_MAP` + `meta_label()` regime strings | `ml_meta_model.py:48,464-472` | 45min |

### SIZER BLOCK (Hours 5-6)

| Priority | Bug ID | Fix | File:Line | Time |
|----------|--------|-----|-----------|------|
| **#8** | GPT-105 | ISA correlation families (UK ticker mapping) | `dynamic_sizer.py:1302-1313` | 60min |
| **#9** | GPT-61 | SHOCK_RECOVERY count by date not signal | `dynamic_sizer.py:528-532` | 30min |
| **#10** | GPT-54 | `__setattr__` guard on ImmutableRiskRules | `risk_sizer.py:30-59` | 30min |

### TESTING BLOCK (Hours 7-8)

| Priority | Task | Time |
|----------|------|------|
| **#11** | Unit tests for all 10 fixes | 60min |
| **#12** | Integration test: full signal pipeline with correlated ISA tickers | 30min |
| **#13** | Regression verification: existing strategies unaffected | 30min |

---

## AMENDMENT 2: PHASE A RE-SCOPING

### What STAYS in Phase A (ratified by Architect):
- All 10 fixes from the 8-Hour Priority Fix Order above
- Go-Live Gate verification after fixes land

### What is DEFERRED to Phase B (explicitly by Architect):
- Signal queue consumer architecture (GPT-12) — the queue is write-only dead code, but fixing the 4 exception handlers (Priority #4) makes it safe dead code rather than crash-on-fill dead code
- ChandelierExit consolidation (GPT-101/107) — the VT inline ladder works, it's just undocumented
- ISA Eligibility Gate (A-1) — critical for live trading but NOT for paper trading
- Shadow Markout Tracker (A-7) — informational, not stop-ship for paper

### What is KILLED (Architect's ruling):
- No more review rounds. R17 is the last plan document before code execution.
- No more theoretical enhancements until the 63-day paper gauntlet produces data.

---

## AMENDMENT 3: DEFINITION OF DONE

A fix is "done" when:
1. Code change is committed with descriptive message
2. Unit test passes for the specific fix
3. `python -m pytest tests/ -x` passes (no regressions)
4. The fix is verified on the running Docker container via `docker logs nzt48 --tail 50`

A fix is NOT done when:
- Only the plan document is updated
- A test is written but not run
- The change is made locally but not deployed

---

## AMENDMENT 4: CORRELATION BYPASS FIX SPECIFICATION (GPT-105 Expanded)

The Architect specifically called out the correlation bypass as a "Silent Killer." Here is the detailed fix specification:

### Current State (BROKEN):
```python
# dynamic_sizer.py:1302-1313
_FAMILIES = [
    {"QQQ", "TQQQ", "SQQQ", "QLD", "PSQ"},
    {"SPY", "SPXL", "SPXS", "SSO", "SH", "UPRO"},
    ...
]
# "QQQ3.L".upper() → "QQQ3.L" → NOT in {"QQQ", "TQQQ", ...} → correlation = 0
```

### Required State (FIXED):
Add a `_BASE_SYMBOL_MAP` that normalizes UK ISA tickers to their underlying:

```
QQQ3.L  → QQQ     (3x Nasdaq Long)
QQQ5.L  → QQQ     (5x Nasdaq Long)
QQQS.L  → QQQ     (3x Nasdaq Short — SAME underlying, INVERSE direction)
3LUS.L  → SPY     (3x S&P 500 Long)
3USS.L  → SPY     (3x S&P 500 Short)
SP5L.L  → SPY     (5x S&P 500 Long)
3SEM.L  → SOX     (3x Semiconductors)
NVD3.L  → NVDA    (3x NVIDIA)
TSM3.L  → TSM     (3x TSMC)
MU2.L   → MU      (2x Micron)
GPT3.L  → AI_TECH (3x AI/Tech basket)
TSL3.L  → TSLA    (3x Tesla)
```

### Files to Modify:
1. `qualification/dynamic_sizer.py` — Add `_BASE_SYMBOL_MAP`, update `_are_instruments_correlated()`
2. `feeds/correlation_matrix.py` — Add UK ISA tickers to `SECTOR_MAP`
3. `qualification/portfolio_risk.py` — Case-insensitive matching in `get_isa_factor_concentration()`

### Test Case:
```
Input: open_positions = [Position(ticker="QQQ3.L", direction=LONG)]
       new_signal = Signal(ticker="3LUS.L", direction=LONG)
Expected: correlation_scalar < 1.0 (penalty applied)
Previous (broken): correlation_scalar = 1.0 (no penalty — bypass)
```

---

## AMENDMENT 5: GIT COMMIT STRATEGY

The Architect said: "See you on the other side of the git commit."

Commit strategy for the 8-hour sprint:

```
Commit 1: "fix(risk): raise SessionProtection hardcap 0.015→0.025 (GPT-111)"
Commit 2: "fix(signals): replace list mutation with comprehension (GPT-104)"
Commit 3: "fix(ml): pass _last_trained_at to should_retrain (GPT-102)"
Commit 4: "fix(queue): asyncio.QueueFull→queue.Full at 4 call sites (GPT-55)"
Commit 5: "fix(regime): add VIX hysteresis + wire transition buffer (GPT-46/56)"
Commit 6: "fix(ml): align _REGIME_MAP + meta_label thresholds (GPT-58/103)"
Commit 7: "fix(sizer): add ISA correlation families + base symbol map (GPT-105)"
Commit 8: "fix(sizer): SHOCK_RECOVERY count by date + ImmutableRules guard (GPT-61/54)"
Commit 9: "test: unit tests for all 8 fix commits"
Commit 10: "deploy: rebuild + push to EC2 paper trading"
```

---

## AMENDMENT 6: AWAITING CHATGPT SUPPLEMENTARY FEEDBACK

The Architect indicated ChatGPT feedback is incoming. This amendment will be updated with:
- Any additional bugs ChatGPT identifies
- Any priority reordering based on ChatGPT's analysis
- Any fixes ChatGPT proposes that overlap with the current 10-fix list

**Placeholder for ChatGPT findings**: [TO BE POPULATED]

---

## SIGN-OFF

This amendment converts the AEGIS Master Plan from a theoretical blueprint to an execution order. The Architect's ruling is clear: the planning phase is over. The 8-Hour Priority Fix Order is the deliverable. The 63-day paper trading gauntlet is the validation.

**Total amendments across all rounds**: 116 (GPT-01 through GPT-116) + 6 R17 amendments
**Total review rounds**: 17 (R10-R17, with R17 being the terminal round)

**Architect's final words**: "Go weld the metal, run the tests, and let's get this system ready for the 63-day paper trading gauntlet."

**Author**: Claude Opus 4.6
**Date**: 2026-03-06
