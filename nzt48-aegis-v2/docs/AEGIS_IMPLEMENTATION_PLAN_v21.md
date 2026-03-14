# AEGIS V2 — IMPLEMENTATION PLAN v21
## How to Execute All Phases in One Autonomous Run
**Version**: 21.0 | **Date**: 2026-03-09 | **Status**: READY TO EXECUTE

---

## OVERVIEW

This document is the operational guide for executing AEGIS_MASTER_PLAN_v21.md from Phase 8 through Phase 23. It explains:
1. How to bypass Claude Code's permission prompts (Shift+Tab → accept-edits mode)
2. How to arm the Ralph Wiggum autonomous loop hook (prevents Claude stopping mid-run)
3. The exact terminal super command to paste into Claude Code
4. Checkpoint gates where you must respond "APPROVED" to continue
5. Emergency abort procedure

Phases 1-7 are **already complete** (~9,000 LOC Rust engine). This plan covers **Phases 8-23** (~337h, ~230 acceptance tests, ending with live capital approval).

---

## STEP 1 — BYPASS PERMISSIONS IN CLAUDE CODE

Claude Code has a permission mode that asks before every file edit, bash command, or tool use. For an autonomous multi-phase run, you want **accept-edits mode** which auto-approves safe operations.

**How to enable:**

In the Claude Code terminal, press **Shift+Tab** to cycle through permission modes. The modes cycle in order:

```
default  →  accept-edits  →  bypass-permissions (full auto)
```

**Which to choose:**

| Mode | What it auto-approves | What still asks |
|------|----------------------|-----------------|
| `accept-edits` | File reads, file writes, file edits | Bash commands, network ops |
| `bypass-permissions` | Everything including bash | Nothing — full auto |

**Recommendation: `accept-edits` (safe autonomous mode)**

> **⚠️ CRITICAL SAFETY UPDATE (v21 Gemini G3-P6 adversarial audit):** Do NOT use `bypass-permissions` for autonomous multi-phase runs. `bypass-permissions` grants the LLM root-level execution authority over the EC2 environment — a hallucinated `rm -rf` or networking change executes immediately with no human circuit breaker. `accept-edits` is the correct mode: it auto-approves file reads and writes (the 95% of operations that are safe) while requiring manual human approval for bash commands (the 5% that can cause irreversible harm).

Press **Shift+Tab once** from default mode to reach `accept-edits`.

You will see the permission indicator in the bottom status bar change. It reads:
- `◆` = default (asks about everything)
- `◇` = accept-edits (auto-approves file ops; bash requires approval) ← **USE THIS**
- `○` = bypass-permissions (auto-approves everything — DO NOT USE)

Press **Shift+Tab** once from the Claude Code terminal (not a browser tab — the actual claude terminal window).

With `accept-edits`:
- File reads, writes, edits → auto-approved ✓
- Cargo builds, docker commands, test runs → will prompt you. Just press Enter to approve. Takes 2 seconds.
- The Ralph Wiggum hook still prevents Claude from stopping between phases.
- You remain in control of all bash execution — no hallucinated destructive commands can execute without you seeing them first.

> **What changed**: Previous v21 implementation plan recommended `bypass-permissions`. The Gemini G3 adversarial audit correctly identified this as operational suicide — an LLM with root execution authority over an EC2 instance with no human circuit breaker is a total loss scenario. `accept-edits` provides 95% of the autonomy with 100% safety on bash commands. The Ralph Wiggum hook continues to provide phase continuity.

---

## STEP 2 — ARM THE RALPH WIGGUM HOOK

The Ralph Wiggum stop hook prevents Claude Code from stopping between phases. It intercepts every stop attempt and checks for the completion marker `<promise>DONE</promise>`. Claude must output this marker only when ALL phases (8-23) are genuinely complete and all gates are signed off.

**Verify the hook is installed:**

```bash
ls ~/.claude/hooks/ralph-wiggum.sh
```

If missing, create it:

```bash
cat > ~/.claude/hooks/ralph-wiggum.sh << 'HOOKEOF'
#!/bin/bash
# Ralph Wiggum - Autonomous Loop Hook for Claude Code
# Activation: touch /tmp/ralph-wiggum-active
# Deactivation: rm /tmp/ralph-wiggum-active (or auto on completion)

set -euo pipefail

COMPLETION_MARKER='<promise>DONE</promise>'
MAX_ITERATIONS=25
ITERATION_FILE="/tmp/ralph-wiggum-iterations"
ACTIVE_FILE="/tmp/ralph-wiggum-active"

if [ ! -f "$ACTIVE_FILE" ]; then exit 0; fi

INPUT=$(cat)
LAST_MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // ""')
TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // ""')

search_transcript() {
  local marker="$1" tpath="$2"
  [ -z "$tpath" ] || [ ! -f "$tpath" ] && return 1
  local texts
  texts=$(tail -30 "$tpath" 2>/dev/null \
    | jq -r 'select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text' 2>/dev/null || true)
  echo "$texts" | grep -qF "$marker"
}

if [ -n "$LAST_MSG" ] && echo "$LAST_MSG" | grep -qF "$COMPLETION_MARKER"; then
  rm -f "$ITERATION_FILE" "$ACTIVE_FILE"; exit 0
fi
if search_transcript "$COMPLETION_MARKER" "$TRANSCRIPT"; then
  rm -f "$ITERATION_FILE" "$ACTIVE_FILE"; exit 0
fi

CURRENT=$(cat "$ITERATION_FILE" 2>/dev/null || echo "0")
CURRENT=$((CURRENT + 1))
echo "$CURRENT" > "$ITERATION_FILE"

if [ "$CURRENT" -ge "$MAX_ITERATIONS" ]; then
  echo "Ralph Wiggum: Safety limit reached. Allowing stop." >&2
  rm -f "$ITERATION_FILE" "$ACTIVE_FILE"; exit 0
fi

cat <<EOF
{ "decision": "block",
  "reason": "Ralph Wiggum (iteration $CURRENT/$MAX_ITERATIONS): Continue working. Output exactly: $COMPLETION_MARKER" }
EOF
exit 0
HOOKEOF
chmod +x ~/.claude/hooks/ralph-wiggum.sh
```

**Verify Claude Code hook configuration** (`~/.claude/settings.json`):

```bash
cat ~/.claude/settings.json | grep -A5 "hooks"
```

The hook must be registered as a `stop` event hook. If not present, add it (check Claude Code docs for your version — hooks are configured in settings.json or via the `/hooks` command).

**Arm the hook (run immediately before pasting the super command):**

```bash
touch /tmp/ralph-wiggum-active
```

**Emergency abort (if Claude gets stuck or misbehaves):**

```bash
rm /tmp/ralph-wiggum-active
```

This immediately disarms the hook. Claude's next stop attempt succeeds.

---

## STEP 3 — THE SUPER COMMAND

Open a fresh Claude Code session in the AEGIS V2 working directory:

```bash
cd /Users/rr/nzt48-signals/nzt48-aegis-v2
```

Press **Shift+Tab** twice to reach `bypass-permissions` mode.

Run `touch /tmp/ralph-wiggum-active` to arm the hook.

Then paste the following as a single message to Claude Code:

---

```
You are Claude Code, acting as Principal Quant Systems Architect for AEGIS V2.

MANDATORY READING BEFORE STARTING:
Read /Users/rr/nzt48-signals/nzt48-aegis-v2/docs/AEGIS_MASTER_PLAN_v21.md in full.
Read /Users/rr/nzt48-signals/nzt48-aegis-v2/docs/00_CANONICAL_RULES.md in full.
Read /Users/rr/nzt48-signals/nzt48-aegis-v2/docs/01_DATA_CONTRACTS.md in full.
Read /Users/rr/nzt48-signals/nzt48-aegis-v2/docs/02_STATE_MACHINE.md in full.
Read /Users/rr/nzt48-signals/nzt48-aegis-v2/docs/03_ACCEPTANCE_TESTS.md in full.

WORKING DIRECTORY: /Users/rr/nzt48-signals/nzt48-aegis-v2/

READ-ONLY JAIL: /Users/rr/nzt48-signals/ (legacy repo — read only for reference)
ALL new code MUST go into: /Users/rr/nzt48-signals/nzt48-aegis-v2/

BANNED NAMES: S3, S8, S15, S16. Use canonical names: Vanguard Sniper, Apex Scout, Executioner, Ouroboros, Universe.

RALPH WIGGUM LOOP: This session uses the Ralph Wiggum stop hook. You CANNOT stop until you output:
  <promise>DONE</promise>
on its own line. Only output this when ALL phases (8-23) are complete and all checkpoint gates have been approved by the human. Do not fake it.

CHECKPOINT GATE PROTOCOL:
- At the end of each phase, output your gate document (actual cargo test output, actual docker build output — never fabricated).
- Then STOP and wait for the human to reply "APPROVED".
- The Ralph Wiggum hook will block your stop. The human will see your gate document and respond.
- Do NOT proceed to the next phase until you receive "APPROVED".

ARCHITECTURE CONSTRAINTS (non-negotiable):
1. tokio::sync::RwLock for active_line_count (NOT Mutex). tokio::sync::Semaphore(100) for line constraint. (v21-FIX-1)
2. NO reqOpenOrders calls anywhere in subscription_manager.rs. AtomicUsize only. (v21-FIX-2)
3. docker-compose.yml must have: stop_grace_period: 60s, POLARS_MAX_THREADS=2, shm_size: '2gb'. (v21-FIX-5)
4. crossbeam overflow: DUAL PATH — OFI suspends + QuoteImbalanceInvalidated WAL; Chandelier aggregates H/L/V. (v21-FIX-6)
5. Gaussian-Gaussian Thompson Sampler with DYNAMIC σ_noise from calibration/asset_volatility.json. (v21-FIX-7)
6. All Polygon corp action dates normalised to Europe/London timezone in Ouroboros step 2. (v21-FIX-8)
7. Cornish-Fisher: gate ALL THREE: N≥20 AND |S|<2 AND K > S²-1 (Maillard 2012). Gaussian fallback if any fails. (v21-FIX-3)
8. SmartRouter: read calibration/eod_spread_cache.json; real-time snapshot at 800ms timeout for Tier 1 only. (v21-FIX-4)
9. CarryMonitor: HashSet<conid> whitelist; discard non-authorized reqPnL updates silently. (v21-FIX-10)
10. Nightly active_state.wal rewrite in Ouroboros step 10; fast-path startup if < 25h old. (v21-FIX-9)

BEGIN WITH PHASE 8. Implement all 17 SC items in order:

SC-01: SIGTERM handler in main.rs — ctrlc crate, flatten positions → wait 30s fills → write SystemShutdown WAL → exit
SC-01a: docker-compose.yml — add stop_grace_period: 60s to aegis-v2 service
SC-02: SubscriptionManager skeleton — tokio::sync::RwLock + tokio::sync::Semaphore(100); deterministic cancel→ACK→subscribe (v21-FIX-1)
SC-03: LineBudget struct {carry, active, scan} with assert!(carry + active + scan <= 100)
SC-04: Two-tier data: IBKR token bucket 60req/10min + Polygon.io for nightly universe; separate Python bucket
SC-05: MINIMUM_ENTRY_GBP = 1500.0; suspended during Kelly ramp (validated_trades < 250)
SC-06: Dust guard: filled_gbp < 500 → Peg-to-Mid TIF=3min → market-sell fallback; cancel unfilled separately
SC-07: Remove S3 reactivation comment from mean_reversion.py
SC-08: APScheduler timezone audit — all pre-LSE jobs use timezone="Europe/London"
SC-09: crossbeam dual-path overflow: (a) OFI → QuoteImbalanceInvalidated WAL + suspend QI EWMA; (b) Chandelier → aggregate H/L/V into current bar (v21-FIX-6)
SC-10: CostBasisEntry VWAP tracker; nightly clear + reqPositions resync
SC-11: active_line_count AtomicUsize; NO reqOpenOrders (v21-FIX-2)
SC-12: symbology_mapper.py — 5 rules including reverse split adjustment
SC-13: Kelly ramp max(0.1, min(1.0, trades/250)); POLARS_MAX_THREADS=2; SplitAdjustment WAL variant
SC-14: reqMarketDataType(3) as FIRST call in ibkr_broker.rs::connect()
SC-15: StrategyId::HotScanner + StrategyId::RotationScanner added to types/enums.rs
SC-16: shm_size: '2gb' in docker-compose.yml (v21-FIX-5)
SC-17: WalPayload::QuoteImbalanceInvalidated {ticker_id, dropped_count, resumed_at_ts} (v21-FIX-6)

PHASE 8 GATE (output when all 17 items complete and tested):
===== PHASE 8 GATE =====
SC items complete: [list each SC with ✓]
cargo test output: [paste actual output]
docker build output: [paste actual output — last 10 lines]
docker-compose.yml verified: stop_grace_period=60s ✓ / POLARS_MAX_THREADS=2 ✓ / shm_size='2gb' ✓
df -h /dev/shm inside container: [paste actual output]
reqMarketDataType(3) first in logs: ✓ / grep shows line: [paste]
No reqOpenOrders in subscription_manager.rs: ✓
30-min SIGTERM drill: [describe outcome]
========================
[Wait for human: APPROVED]

After Phase 8 gate APPROVED, proceed to Phase 11 (5-Mode Clock + SubscriptionManager).
After Phase 11 gate APPROVED, proceed to Phase 12 (Smart Router).
After Phase 12 gate APPROVED, proceed to Phase 13 (HotScanner + RotationScanner).
After Phase 13 gate APPROVED, proceed to Phase 14 (Chandelier + Executioner V2).
After Phase 14 gate APPROVED, proceed to Phase 15 (RiskGate 31 Vetoes + CVaR).
After Phase 15 gate APPROVED, proceed to Phase 16 (Ouroboros).
After Phase 16 gate APPROVED, proceed to Phase 17 (Telemetry).
After Phase 17 gate APPROVED, proceed to Phase 18 (European Equities).
After Phase 18 gate APPROVED, proceed to Phase 19 (Asia-Pac MODE A).
After Phase 19 gate APPROVED, proceed to Phase 20 (Carry State Machine).
After Phase 20 gate APPROVED, proceed to Phase 21 (Cross-Timezone Intelligence).
After Phase 21 gate APPROVED, proceed to Phase 22 (Institutional Hardening).
After Phase 22 gate APPROVED, proceed to Phase 23 (Crucible: 7-Suite Verification).

When Phase 23 Crucible passes all 7 suites and 100 validated paper trades are complete:
Output exactly:
<promise>DONE</promise>
```

---

## STEP 4 — CHECKPOINT GATES

At the end of each phase, Claude will output a gate document and wait. You respond with one word:

```
APPROVED
```

This advances to the next phase. Do NOT type APPROVED until you have read the gate document and verified:
- cargo test output shows all tests passing (no failures, no panics)
- docker build succeeds (last line: `Successfully built ...`)
- Paper session logs look clean (no unexpected ERRORs)
- The specific gate criteria for that phase are met

If something looks wrong, type `HOLD — [describe the issue]` and Claude will investigate before resubmitting the gate.

---

## STEP 5 — PHASE GATE REFERENCE

Each phase has specific gate criteria. Here is what to look for at each gate:

| Phase | Key Gate Checks |
|-------|----------------|
| 8 | docker-compose.yml has all 3 v21 fields; `/dev/shm` ≥2GB verified; reqMarketDataType(3) is FIRST in logs; NO reqOpenOrders in subscription_manager.rs |
| 11 | chrono-tz DST verified at March 26 2026 boundary; NZX pre-subscribe at 22:55 UTC; grep confirms no reqOpenOrders |
| 12 | eod_spread_cache.json populated; snapshot timeout 800ms for Tier 1; XETRA auction window correct |
| 13 | dynamic σ_noise loaded from asset_volatility.json; 3x ETP gets higher σ_noise than direct equity |
| 14 | TWAP cancel on Chandelier hit verified; leverage-adjusted floor verified at 3x |
| 15 | Maillard K>S²-1 domain check verified; CVaR scales with kelly_scale |
| 16 | Polygon dates normalised to Europe/London (test with US EDT date); EOD spread cache and asset_volatility.json present |
| 17 | Async Redis heartbeat (no blocking); HALT auth rejects unknown chat_id |
| 18 | UK ISIN stamp duty verified on MTF-routed GB-ISIN ticker; Polygon retry on 502 |
| 19 | JPY 0-decimal precision; reconnect 15s delay; ASX DST dynamic |
| 20 | HashSet whitelist verified; PnL staleness detection fires at 6min no-update |
| 21 | DCC-GARCH artifact timestamp check; active_state.wal hook verified in step 10 |
| 22 | active_state.wal fast-path startup < 100ms; ArcSwap config reload with open-position safety; 48h paper run clean |
| 23 | All 7 Crucible suites pass; 100 validated paper trades; WR ≥ 40% |

---

## STEP 6 — POST-CRUCIBLE: LIVE CAPITAL

When Phase 23 outputs `<promise>DONE</promise>` and the hook auto-releases:

1. The system has 100 validated paper trades and all 7 Crucible suites passing.
2. Change `IS_LIVE = false` to `IS_LIVE = true` in `main.rs:26`.
3. Run `bash deploy.sh rebuild` to push to EC2.
4. Fund the IBKR ISA account (paper → live account switch in IBKR portal).
5. Monitor first live session via Telegram alerts.

**Do NOT flip IS_LIVE until `<promise>DONE</promise>` is output.** No exceptions.

---

## EMERGENCY PROCEDURES

**Claude is stuck in a loop:**
```bash
rm /tmp/ralph-wiggum-active
```
Claude's next stop attempt succeeds. Investigate what went wrong before restarting.

**Claude produced wrong output at a gate:**
Type `HOLD — [issue description]`. Do NOT type APPROVED.

**Need to restart from a specific phase:**
Start a new Claude Code session. Arm the hook again. Paste a modified super command starting from the specific phase (e.g., "Begin with Phase 13 — Phases 8-12 are already complete and gated. Reference AEGIS_MASTER_PLAN_v21.md.").

**Docker/EC2 issues:**
```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22
docker compose logs aegis-v2 --tail 50
```

---

## FILE REFERENCE

| File | Purpose |
|------|---------|
| `docs/AEGIS_MASTER_PLAN_v21.md` | **Canonical plan** — all phase deliverables and acceptance tests |
| `docs/AEGIS_SELF_ANALYSIS_TRIAGE_v20.md` | 200-bullet Gemini triage with dispositions |
| `docs/AEGIS_IMPLEMENTATION_PLAN_v21.md` | This file — operational execution guide |
| `docs/00_CANONICAL_RULES.md` | Immutable architectural rules |
| `docs/01_DATA_CONTRACTS.md` | WAL event schemas and data contracts |
| `docs/02_STATE_MACHINE.md` | State machine transitions |
| `docs/03_ACCEPTANCE_TESTS.md` | Full acceptance test specifications |

---

*AEGIS_IMPLEMENTATION_PLAN_v21.md — Generated 2026-03-09*
*Pairs with: AEGIS_MASTER_PLAN_v21.md*
*Ralph Wiggum hook version: 1.0 (same hook as phases 1-7, unchanged)*
