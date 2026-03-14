# TELEGRAM DESK TAPE -- COMPLETE SPECIFICATION

**Document ID**: NZT48-ANNEX-002
**Version**: 2.0
**Date**: 2026-02-27
**Status**: BINDING -- All Telegram message delivery MUST conform to this specification
**Scope**: Message types, templates, labelling, deduplication, rate limiting, restart hygiene, system mode message filtering, formatting standards

---

## 1. OBJECTIVE

Define the complete specification for all Telegram message delivery in the NZT-48 system. Every message type has a defined template, required fields, deduplication window, and rate limit. This specification addresses issues identified in FORENSICS_MAP sections 4.1-4.3 (duplicate sends, formatting inconsistency, restart state loss).

---

## 2. GLOBAL RULES

### 2.1 Parse Mode
ALL Telegram messages MUST use `parse_mode="HTML"`. No exceptions. No `parse_mode=None`.

**Current violation**: `telegram_bot.py:801-806` sends firewall block and trade close messages with `parse_mode=None`. This MUST be fixed.

### 2.2 Number Formatting
| Data Type | Format | Example |
|-----------|--------|---------|
| Price (USD) | 2 decimal places | `$142.50` |
| Price (GBP/LSE) | 2 decimal places | `142.50p` or `£1.43` |
| Percentage | 1 decimal place | `+2.1%`, `-0.5%` |
| R-multiple | 1 decimal place with sign | `+1.8R`, `-0.3R` |
| Confidence score | Integer, no decimals | `78/100` |
| Dollar P&L | 2 decimal places with sign | `+$260.00`, `-$45.20` |
| Risk percentage | 2 decimal places | `0.75%` |
| Count | Integer | `3 trades`, `12 signals` |

### 2.3 Time Formatting
Every timestamp in a Telegram message MUST include BOTH UTC and UK local time:
```
Format: HH:MM UTC (HH:MM UK)
Example: 14:30 UTC (14:30 GMT) or 14:30 UTC (15:30 BST)
```

### 2.4 Character Limit
Telegram message limit is 4096 characters. All messages MUST be validated against this limit before sending. If a message exceeds 4096 characters:
1. Truncate the body (not the header or footer).
2. Append `\n... (truncated -- see War Room for full data)` at the end.
3. Log the truncation event.

### 2.5 HTML Escaping
All dynamic text content MUST be escaped via `_escape_html()` before insertion into templates. This prevents HTML injection from ticker names, strategy names, or user input.

---

## 3. MESSAGE TYPES

### 3.1 Message Type Registry

| Type ID | Label | Description | Dedupe Window | Max Frequency |
|---------|-------|-------------|---------------|---------------|
| `SIGNAL` | `[SIGNAL]` | Qualifying trade signal (buy/sell) | 5 minutes | 5/min, 30/hr |
| `PREMARKET_BRIEF` | `[BRIEF]` | Pre-market intelligence brief | 30 minutes | 1/hour |
| `FIREWALL_BLOCK` | `[FIREWALL]` | Emotional firewall block notification | 5 minutes | 5/min |
| `DROUGHT_ALERT` | `[DROUGHT]` | Drought state escalation | 30 minutes | 1/hour |
| `REGIME_CHANGE` | `[REGIME]` | Market regime transition | 5 minutes | 3/min |
| `NIGHTLY_DIGEST` | `[DIGEST]` | Comprehensive nightly review | 60 minutes | 1/day |
| `SYSTEM_STATUS` | `[SYSTEM]` | System health, restarts, warnings | 60 minutes | 2/hour |
| `KILL_SWITCH` | `[KILL]` | Kill switch activation | NO DEDUPE | UNLIMITED |
| `ERROR` | `[ERROR]` | System errors requiring attention | 15 minutes | 5/hour |
| `TRADE_CLOSED` | `[CLOSED]` | Position closed notification | 5 minutes | 10/min |
| `CONTRADICTION` | `[SYSTEM]` | Regime-drought contradiction | 30 minutes | 2/hour |

---

## 4. MESSAGE TEMPLATES

### 4.1 SIGNAL (Buy)

```html
[SIGNAL] BUY

<b>{direction_emoji} BUY {ticker}</b> | {strategy_id} {strategy_name} | Conf: {confidence}/100

Entry: <code>${entry}</code> | Stop: <code>${stop}</code> ({stop_pct}) | Risk: <code>${risk}</code> ({risk_pct})
T1: <code>${target_1r}</code> ({t1_pct}) | T2: <code>${target_2r}</code> ({t2_pct}) | Trail: <code>${trail}</code>
Market Regime: {market_regime} | GEX: {gex_regime} | RVOL: {rvol}
ISA Map: {isa_ticker} ({isa_leverage} {isa_underlying}) | Bot: {bot}
Overseer: {overseer_status} | Portfolio heat: {heat}%/{heat_limit}%

{patterns_line}
{timeframe_line}
{timestamp_line}
```

**Required fields**: ticker, direction, strategy_id, strategy_name, confidence, entry, stop, stop_pct, risk, risk_pct, target_1r, t1_pct, target_2r, t2_pct, trail, market_regime, gex_regime, rvol, bot, overseer_status, heat, heat_limit
**Optional fields**: isa_ticker, isa_leverage, isa_underlying, patterns_line, timeframe_line
**Dedupe hash**: MD5(SIGNAL + ticker + direction + entry + stop + target_1r)
**Dedupe window**: 5 minutes

### 4.2 SIGNAL (Sell)

Same template as 4.1 but with header `[SIGNAL] SELL` and sell emoji.

### 4.3 SIGNAL (S15 Daily Target)

```html
[SIGNAL] 2% DAILY TARGET

<b>{direction_emoji} {direction} {ticker}</b>

Entry: <code>${entry}</code>
Target (+2%): <code>${target}</code>
Stop: <code>${stop}</code>
R:R: <code>{rr_ratio}:1</code>
Confidence: <code>{confidence}/100</code>

<b>THE COMPOUNDING MACHINE</b>
2% daily x 252 days = 14,757% annualised
10K -> 1,485,757

<i>Strategy S15 -- find ONE stock, make 2%, repeat.</i>
{timestamp_line}
```

**Required fields**: direction, ticker, entry, target, stop, rr_ratio, confidence
**Dedupe hash**: MD5(SIGNAL_S15 + ticker + direction + entry)
**Dedupe window**: 5 minutes

### 4.4 PREMARKET_BRIEF

```html
[BRIEF] PRE-MARKET INTELLIGENCE

<b>Market Overview</b>
ES Futures: {es_price} ({es_change})
NQ Futures: {nq_price} ({nq_change})
VIX: {vix} | DXY: {dxy}

<b>Regime</b>
Market: {market_regime}
Vol Regime Majority: {vol_regime_majority}

<b>Top Opportunities</b>
{opportunity_list}

<b>Risk Flags</b>
{risk_flags}

<b>Calendar</b>
{calendar_events}

Generated: {timestamp_utc} ({timestamp_uk})
```

**Required fields**: es_price, es_change, nq_price, nq_change, vix, dxy, market_regime, vol_regime_majority, timestamp_utc, timestamp_uk
**Optional fields**: opportunity_list, risk_flags, calendar_events
**Dedupe hash**: MD5(BRIEF + date)
**Dedupe window**: 30 minutes

### 4.5 FIREWALL_BLOCK

```html
[FIREWALL] {pattern_name}

Blocked: {direction} {ticker} | {strategy_id} | Conf: {confidence}
Reason: {reason}
Pattern: {pattern_description}

{timestamp_line}
```

**Required fields**: pattern_name, direction, ticker, strategy_id, confidence, reason
**Optional fields**: pattern_description
**Dedupe hash**: MD5(FIREWALL + ticker + pattern_name)
**Dedupe window**: 5 minutes

### 4.6 DROUGHT_ALERT

```html
[DROUGHT] {severity}

No qualifying signals for {cycle_count} cycles (~{minutes} min).
Market Regime: {market_regime}
Vol Regime Majority: {vol_regime_majority}
Last Signal: {last_signal_time} ({last_signal_ticker} {last_signal_strategy})

{operator_guidance}

{timestamp_line}
```

**Required fields**: severity (WATCH/ACTIVE/CRITICAL), cycle_count, minutes, market_regime, vol_regime_majority
**Optional fields**: last_signal_time, last_signal_ticker, last_signal_strategy, operator_guidance
**Dedupe hash**: MD5(DROUGHT + severity)
**Dedupe window**: 30 minutes
**Note**: DROUGHT_WATCH does NOT send a Telegram message (War Room only). Only DROUGHT_ACTIVE and DROUGHT_CRITICAL send.

### 4.7 REGIME_CHANGE

```html
[REGIME] {transition_label}

{old_regime} -> {new_regime}
Action: {required_action}
Positions Affected: {positions_affected}
Realised P&L: {realised_pnl}

{timestamp_line}
```

**Required fields**: transition_label, old_regime, new_regime, required_action, positions_affected, realised_pnl
**Dedupe hash**: MD5(REGIME + old_regime + new_regime)
**Dedupe window**: 5 minutes

### 4.8 NIGHTLY_DIGEST

```html
[DIGEST] NIGHTLY REVIEW

<b>Strategy Performance</b>
{strategy_performance_table}

<b>Total: {total_sign}${total_pnl} | {total_trades} trades | WR: {win_rate}%</b>

<b>Missed Trade Analysis</b>
Blocked: {total_blocked} | Would have won: {would_have_won}
Edge lost: {edge_lost_r}R | Worst filter: {worst_filter}

<b>Firewall Summary</b>
Total blocks: {firewall_total}
Patterns: {firewall_patterns}

<b>Filter:</b> {filter_analysis}

<b>Lesson:</b> {top_lesson}

{timestamp_line}
```

**Required fields**: strategy_performance_table, total_pnl, total_trades, win_rate
**Optional fields**: all others (graceful fallback to "N/A")
**Dedupe hash**: MD5(DIGEST + date)
**Dedupe window**: 60 minutes

### 4.9 SYSTEM_STATUS

```html
[SYSTEM] {status_type}

{message}

Engine: {engine_state}
Uptime: {uptime}
Scan Cycles: {scan_count}
Last Signal: {last_signal_time}
Drought: {drought_state}

{timestamp_line}
```

**Required fields**: status_type, message, engine_state
**Optional fields**: uptime, scan_count, last_signal_time, drought_state
**Dedupe hash**: MD5(SYSTEM + status_type + message_hash)
**Dedupe window**: 60 minutes

### 4.10 KILL_SWITCH

```html
[KILL] KILL SWITCH ACTIVATED

Reason: {reason}
All signals HALTED. Manual intervention required.
Activated by: {method} (Telegram/File/Signal)

Positions at time of kill:
{position_summary}

{timestamp_line}
```

**Required fields**: reason, method
**Optional fields**: position_summary
**Dedupe hash**: NONE (always send)
**Dedupe window**: NONE (never dedupe kill switch messages)

### 4.11 ERROR

```html
[ERROR] {error_category}

{error_message}
Module: {module_name}
Function: {function_name}

{timestamp_line}
```

**Required fields**: error_category, error_message, module_name
**Optional fields**: function_name, stack_trace (truncated to 500 chars)
**Dedupe hash**: MD5(ERROR + error_category + module_name)
**Dedupe window**: 15 minutes

### 4.12 TRADE_CLOSED

```html
[CLOSED] {result_emoji} {direction} {ticker} | {strategy_id} {strategy_name} | {r_sign}{r_multiple}R

Entry: <code>${entry_price}</code> -> Exit: <code>${exit_price}</code> | P&L: {pnl_sign}${pnl}
Exit: {exit_reason} | Duration: {duration} | Peak: +{peak_r}R
Conf: {confidence} | Market Regime: {market_regime}

{missed_gain_line}
{missed_loss_line}
{timestamp_line}
```

**Required fields**: direction, ticker, strategy_id, strategy_name, r_multiple, entry_price, exit_price, pnl, exit_reason, duration, peak_r, confidence, market_regime
**Optional fields**: missed_gain_line, missed_loss_line
**Dedupe hash**: MD5(CLOSED + ticker + exit_price + r_multiple)
**Dedupe window**: 5 minutes

---

## 5. DEDUPLICATION SPECIFICATION

### 5.1 Hash Computation
```python
def compute_dedupe_hash(message_type: str, **fields) -> str:
    """Compute MD5 hash for deduplication."""
    raw = f"{message_type}|{'|'.join(str(v) for v in fields.values())}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]
```

### 5.2 Dedupe Windows Per Type
| Type | Window (seconds) | Rationale |
|------|-----------------|-----------|
| SIGNAL | 300 (5 min) | Same signal should not repeat within 5 min |
| PREMARKET_BRIEF | 1800 (30 min) | Brief generated once per session |
| FIREWALL_BLOCK | 300 (5 min) | Same block pattern for same ticker |
| DROUGHT_ALERT | 1800 (30 min) | Drought escalation is slow-moving |
| REGIME_CHANGE | 300 (5 min) | Rapid regime changes are possible |
| NIGHTLY_DIGEST | 3600 (60 min) | Once per day |
| SYSTEM_STATUS | 3600 (60 min) | Prevent status spam |
| KILL_SWITCH | 0 (no dedupe) | Always deliver kill switch |
| ERROR | 900 (15 min) | Prevent error spam |
| TRADE_CLOSED | 300 (5 min) | Same close should not repeat |
| CONTRADICTION | 1800 (30 min) | Same contradiction rule |

### 5.3 Dedupe Storage
- Primary: In-memory dictionary `{hash: timestamp}` (current implementation).
- Persistence: On every dedupe check, write the hash store to `data/telegram_dedupe.json` (new requirement).
- On restart: Load `data/telegram_dedupe.json` and restore hashes that are still within their windows.
- Cleanup: Expired hashes are purged every 60 seconds.

---

## 6. RATE LIMITING

### 6.1 Rate Limit Configuration

| Parameter | Default Value | Configurable | Config Key |
|-----------|--------------|--------------|------------|
| Max per minute | 5 | YES | `telegram.rate_limit.max_per_minute` |
| Max per hour | 30 | YES | `telegram.rate_limit.max_per_hour` |
| Spam kill threshold (per minute) | 10 | YES | `telegram.rate_limit.spam_kill_threshold` |
| Spam pause duration (seconds) | 900 (15 min) | YES | `telegram.rate_limit.spam_pause_seconds` |

**Current state**: These values are hard-coded in `telegram_bot.py:90-92`. They MUST be moved to `settings.yaml`.

### 6.2 Rate Limit Bypass
The following message types bypass rate limiting (but NOT deduplication):
- `KILL_SWITCH` -- always delivered immediately.
- `ERROR` with category `CRITICAL` -- always delivered immediately.

### 6.3 Rate Limit Persistence
- Rate limiter timestamps MUST be persisted to `data/telegram_rate_state.json`.
- On restart, load persisted state.
- If the system was down for > 15 minutes, clear the rate limiter (fresh start).

---

## 7. RESTART HYGIENE

### 7.1 Quiet Period
On system restart, a **5-minute quiet period** applies:

| Action During Quiet Period | Allowed? |
|---------------------------|----------|
| Send `[SYSTEM] NZT-48 restarted` notice | YES (first message) |
| Send `[KILL]` kill switch alert | YES (safety-critical) |
| Send `[ERROR]` critical errors | YES |
| Send `[SIGNAL]` trade signals | NO -- queued and sent after quiet period |
| Send `[REGIME]` regime changes | NO -- queued |
| Send `[BRIEF]` premarket brief | NO -- queued |
| Send `[DROUGHT]` alerts | NO -- suppressed entirely (drought counter resets) |
| Send `[FIREWALL]` blocks | NO -- logged only |

### 7.2 Restart Sequence
1. **T+0s**: Engine starts. Load persisted state from:
   - `data/telegram_dedupe.json` (dedupe hashes)
   - `data/telegram_rate_state.json` (rate limiter)
   - `data/KILL_SWITCH` (kill switch file)
   - `data/pauses.json` (paused strategies/bots)
   - `artifacts/system_state.json` (drought state, regime)
2. **T+1s**: Send `[SYSTEM] NZT-48 restarted` notice:
   ```html
   [SYSTEM] ENGINE RESTART

   NZT-48 signal engine restarted.
   Version: {version}
   Mode: {mode}
   Previous shutdown: {last_shutdown_reason}
   Kill switch: {kill_status}
   Paused strategies: {paused_list}
   Drought state: {drought_state}
   Market regime: {market_regime}

   Quiet period: 5 minutes (signals queued until {quiet_end_time})
   ```
3. **T+2s**: Restore kill switch state. If kill file exists, remain in KILLED state.
4. **T+3s**: Restore paused strategies from `data/pauses.json`.
5. **T+5m**: Quiet period ends. Process queued messages in order. Resume normal operation.

### 7.3 State Persistence Schedule
The following states MUST be persisted to disk every 30 seconds:
- Dedupe hash store
- Rate limiter timestamps
- Kill switch state (already persisted via file)
- Paused/killed strategies (already persisted via file)
- Drought cycle counter (via system_state.json)

---

## 8. SYSTEM MODE MESSAGE FILTERING

When the system enters DEGRADED or HALTED mode, Telegram output MUST be restricted to SYSTEM HEALTH messages only. This prevents misleading signal/brief messages from being sent when the system cannot guarantee data quality.

### 8.1 Mode Definitions

| System Mode | Trigger | Telegram Behaviour |
|-------------|---------|-------------------|
| NORMAL | All data feeds healthy, engine running, no errors | All message types permitted per rate limits |
| DEGRADED | Data quality <80%, >3 provider failures, or SOFT_GATE_BYPASS flagged | **SYSTEM HEALTH only**: suppress SIGNAL, BRIEF, REGIME, DROUGHT, FIREWALL messages; only [SYSTEM] and [CRITICAL ERROR] messages sent |
| HALTED | Kill switch active, engine crashed, or manual halt | **SYSTEM HEALTH only**: suppress ALL non-SYSTEM messages; send [SYSTEM] halt notification; continue sending [CRITICAL ERROR] messages |

### 8.2 Message Suppression Rules

When mode is DEGRADED or HALTED:

**ALLOWED (always sent)**:
- `[SYSTEM]` — system status updates (mode change, recovery, health reports)
- `[CRITICAL ERROR]` — crashes, kill switch activation, data loss
- `[KILL]` — kill switch state changes

**SUPPRESSED (queued, not sent)**:
- `[SIGNAL]` — trading signals (cannot trust data quality in DEGRADED)
- `[BRIEF]` — market briefs (may contain stale/incorrect data)
- `[REGIME]` — regime change notifications (regime may be misclassified)
- `[DROUGHT]` — drought state changes (drought detection unreliable)
- `[FIREWALL]` — firewall events (may be false positives from bad data)
- `[EXIT]` — exit recommendations (cannot trust exit scoring)

**Queue behaviour**: Suppressed messages are logged to `telegram_debug.jsonl` with `action: "SUPPRESSED_DEGRADED"` or `action: "SUPPRESSED_HALTED"`. They are NOT retransmitted when mode returns to NORMAL (they are stale by definition).

### 8.3 Mode Transition Messages

| Transition | Telegram Message | Priority |
|-----------|-----------------|----------|
| NORMAL → DEGRADED | `[SYSTEM] ⚠ DEGRADED MODE — signals suppressed until data quality restored. Reason: {reason}` | IMMEDIATE |
| DEGRADED → HALTED | `[SYSTEM] 🔴 HALTED — all non-system messages suppressed. Reason: {reason}` | IMMEDIATE |
| DEGRADED → NORMAL | `[SYSTEM] ✅ NORMAL MODE RESTORED — signal delivery resumed` | IMMEDIATE |
| HALTED → NORMAL | `[SYSTEM] ✅ NORMAL MODE RESTORED — system recovered from halt` | IMMEDIATE |
| HALTED → DEGRADED | `[SYSTEM] ⚠ DEGRADED MODE — partial recovery, signals still suppressed` | IMMEDIATE |

### 8.4 DEGRADED Mode Entry Criteria

The system enters DEGRADED mode when ANY of:
1. Data coverage drops below 80% for ANY CORE ticker (source: provenance engine)
2. More than 3 data providers report DOWN/UNRELIABLE status
3. Any SOFT_GATE_BYPASS flag has been attached to a signal in the current session
4. `system_state.json` reports `data_reliability < 0.80`
5. Manual operator command: `/degraded` via Telegram

### 8.5 HALTED Mode Entry Criteria

The system enters HALTED mode when ANY of:
1. Kill switch activated (file or DB flag)
2. Engine process not responding (heartbeat > 120s)
3. Docker container health check fails
4. Manual operator command: `/halt` via Telegram
5. Spam kill triggered (10 messages in 1 minute)

### 8.6 Operator Actions

| Scenario | Operator Action |
|----------|----------------|
| Unexpected DEGRADED mode entry | Check data providers: `curl /api/health`. Check provenance: `curl /api/consistency`. Investigate stale feeds. |
| Cannot exit DEGRADED mode | Verify all data providers are UP. Check `system_state.json` data_reliability. Manual override: `/normal` command. |
| HALTED after spam kill | Wait 15 minutes for auto-resume. Or manually clear: remove kill file + `/resume` command. |
| Telegram completely silent | Check if mode is HALTED. Check Docker: `docker logs nzt48 --tail 20`. Check bot token: send /status. |

---

## 9. TELEGRAM DEBUG LOG

### 9.1 Log Format
Every Telegram event (sent, deduped, rate-limited, error) MUST be logged to `data/telegram_debug.jsonl`:

```json
{
  "ts": "2026-02-27T14:30:00.000Z",
  "action": "SENT|DEDUPED|RATE_LIMITED|GATE_FAILED|ERROR|QUEUED|QUIET_PERIOD",
  "label": "[SIGNAL]",
  "message_type": "SIGNAL",
  "ticker": "NVD3.L",
  "content_hash": "a1b2c3d4e5f6",
  "reason": "",
  "message_preview": "first 100 chars...",
  "dedupe_window_active": true,
  "rate_limit_remaining": 4,
  "quiet_period_active": false
}
```

### 9.2 Log Rotation
- Rotate `telegram_debug.jsonl` daily at 00:00 UTC.
- Keep last 7 days of logs.
- Archive pattern: `data/telegram_debug_YYYYMMDD.jsonl`.

---

## 10. FAILURE MODES

| # | Failure Mode | Detection | Impact | Mitigation |
|---|-------------|-----------|--------|------------|
| F1 | Telegram API returns 429 (Too Many Requests) | HTTP response code | Messages not delivered | Exponential backoff: 1s, 2s, 4s, 8s, max 60s. Log event. |
| F2 | Telegram API returns 400 (Bad Request, HTML parse error) | HTTP response code | Message not delivered | Retry without parse_mode (current behaviour). Log the HTML that failed. |
| F3 | Telegram bot token invalid/expired | 401 response | ALL messages fail | Fire [ERROR] via fallback (plain log). Set `_enabled = False`. |
| F4 | Network timeout on send | Exception after 10s | Message not delivered | Retry once after 5s. If fails again, log and continue. |
| F5 | Dedupe JSON file corrupted | JSON parse error on load | Dedupe lost | Start with empty dedupe store. Log warning. |
| F6 | Rate limiter JSON file corrupted | JSON parse error on load | Rate state lost | Start with empty rate state. Log warning. |
| F7 | Message exceeds 4096 characters | Length check before send | API rejection | Truncate per Section 2.4. |
| F8 | Quiet period never ends (bug) | Timer check: if quiet > 10 minutes | Signals permanently queued | Hard cap: quiet period CANNOT exceed 10 minutes. Auto-expire. |
| F9 | Kill switch state not restored on restart | Missing KILL_SWITCH file | Trading resumes when it should not | Check for KILL_SWITCH file at T+0s. Default to KILLED if file check fails (fail-safe). |

---

## 11. OPERATOR ACTIONS

| Scenario | Operator Action |
|----------|----------------|
| Telegram messages not arriving | Check bot token validity via `/api/health`. Check `telegram_debug.jsonl` for ERROR entries. Verify chat_id is correct. |
| Spam pause activated (10/min) | Wait 15 minutes or manually clear via `data/telegram_rate_state.json`. Investigate what caused burst. |
| Duplicate messages received | Check dedupe window for the message type. Review `telegram_debug.jsonl` for DEDUPED entries. Ensure persistence is working. |
| Messages arriving during quiet period | Only SYSTEM, KILL, and CRITICAL ERROR messages bypass quiet period. This is expected. |
| Kill switch not restored after restart | Check `data/KILL_SWITCH` file exists. Manually create it if needed: `echo '{"reason":"manual"}' > data/KILL_SWITCH`. |

---

## 12. ACCEPTANCE TESTS

### 12.1 Message Format Tests
| # | Test | Expected Result | Pass Criteria |
|---|------|-----------------|---------------|
| T1 | Send each of the 11 message types | All render correctly in Telegram | No HTML parse errors in logs |
| T2 | Send a SIGNAL with price $0.001 and $99999.99 | Numbers formatted to 2 decimal places | `$0.00` and `$99999.99` displayed |
| T3 | Send a message with `<script>alert(1)</script>` in ticker name | HTML escaped correctly | Raw text displayed, no script execution |
| T4 | Send a message exactly 4096 characters | Message delivered without truncation | Full message visible in Telegram |
| T5 | Send a message of 4200 characters | Message truncated to 4096 with truncation notice | Truncation suffix present |

### 12.2 Deduplication Tests
| # | Test | Expected Result | Pass Criteria |
|---|------|-----------------|---------------|
| T6 | Send identical SIGNAL twice within 5 minutes | Second message deduped | Only 1 message in Telegram, DEDUPED in log |
| T7 | Send identical SIGNAL 6 minutes apart | Both messages delivered | 2 messages in Telegram |
| T8 | Send KILL_SWITCH twice within 1 second | Both messages delivered | 2 messages in Telegram (no dedupe on KILL) |
| T9 | Restart system, send same SIGNAL that was sent 3 minutes before restart | Deduped (restored from persisted state) | DEDUPED in log |
| T10 | Restart system after 6+ minutes, send same SIGNAL | Delivered (dedupe window expired) | Message in Telegram |

### 12.3 Rate Limit Tests
| # | Test | Expected Result | Pass Criteria |
|---|------|-----------------|---------------|
| T11 | Send 6 messages in 1 minute | 5th delivered, 6th rate-limited | RATE_LIMITED in log |
| T12 | Send 31 messages in 1 hour | 30th delivered, 31st rate-limited | RATE_LIMITED in log |
| T13 | Send 10 messages in 30 seconds | Spam kill activated, 15-min pause | SPAM_KILL in log, no messages for 15 min |
| T14 | Send KILL_SWITCH during spam pause | Delivered immediately | Message in Telegram |

### 12.4 Restart Hygiene Tests
| # | Test | Expected Result | Pass Criteria |
|---|------|-----------------|---------------|
| T15 | Restart engine | [SYSTEM] restart notice sent within 5 seconds | Message in Telegram |
| T16 | Generate SIGNAL during quiet period | Signal queued, delivered after 5 minutes | QUEUED in log, then SENT after quiet period |
| T17 | Restart with KILL_SWITCH file present | Kill state restored, no signals sent | Kill state active in system_state.json |
| T18 | Restart with pauses.json containing "S15" | S15 remains paused | S15 signals blocked |

---

## 13. PROOF ARTIFACTS

| # | Artifact | Location | Description |
|---|----------|----------|-------------|
| A1 | Message format screenshots | `artifacts/telegram_format_test/` | Screenshot of each message type rendered in Telegram |
| A2 | Dedupe test log | `artifacts/telegram_dedupe_test.jsonl` | JSONL from tests T6-T10 |
| A3 | Rate limit test log | `artifacts/telegram_rate_test.jsonl` | JSONL from tests T11-T14 |
| A4 | Restart hygiene test log | `artifacts/telegram_restart_test.jsonl` | JSONL from tests T15-T18 |
| A5 | Parse mode audit | `artifacts/parse_mode_audit.txt` | grep output confirming ALL send calls use `parse_mode="HTML"` |
| A6 | Persisted state files | `data/telegram_dedupe.json`, `data/telegram_rate_state.json` | Sample files showing correct schema |

---

## 14. CONFIGURATION PARAMETERS

All parameters MUST be configurable via `config/settings.yaml` under a `telegram` section:

```yaml
telegram:
  # Rate limiting
  rate_limit:
    max_per_minute: 5
    max_per_hour: 30
    spam_kill_threshold: 10
    spam_pause_seconds: 900

  # Dedupe windows (seconds)
  dedupe:
    signal: 300
    premarket_brief: 1800
    firewall_block: 300
    drought_alert: 1800
    regime_change: 300
    nightly_digest: 3600
    system_status: 3600
    kill_switch: 0
    error: 900
    trade_closed: 300
    contradiction: 1800

  # Restart hygiene
  restart:
    quiet_period_seconds: 300
    quiet_period_max_seconds: 600
    state_persist_interval_seconds: 30

  # Debug logging
  debug:
    log_path: "data/telegram_debug.jsonl"
    rotation: "daily"
    retention_days: 7

  # Formatting
  formatting:
    parse_mode: "HTML"
    max_message_length: 4096
    truncation_suffix: "\n... (truncated -- see War Room for full data)"
    price_decimals: 2
    percentage_decimals: 1
    r_multiple_decimals: 1
```

---

## REVISION HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-27 | NZT-48 Spec Engine | Initial binding specification |
| 2.0 | 2026-02-27 | NZT-48 Spec Engine | Added Section 8: System Mode Message Filtering (DEGRADED/HALTED mode suppression rules, mode transition messages, entry criteria, operator actions). Renumbered sections 8-13 to 9-14. |
