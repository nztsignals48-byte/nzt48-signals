# NZT-48 ADVERSARIAL AUDIT — Sprints 0, 1, 2 Combined
## 4-Persona Review Board — Code-Verified Against Live Codebase

---

## SECTION 1: Per-Persona Findings

---

### CHIEF QUANT OFFICER (CQ) — Signal Validity & Statistical Foundations

---

**CQ-1 | CRITICAL | Sprint 1 T-05 + Sprint 0 S0-2 | FAST Tier Bypasses Confidence Floor Check**
The FAST tier path in `_execute_s15_priority_path` (main.py:3927-3932) logs "FAST PATH QUALIFIED" and skips all confidence modifiers (`if _signal_tier != "FAST"`). However, the FAST path does NOT enforce the `_MIN_CONFIDENCE = 65.0` floor. Gap signals are hardcoded to confidence 72.0 (daily_target.py:1272), but SLOW-originated FAST signals use whatever confidence `_determine_direction` assigns. If a FAST-qualified signal has confidence 45 (possible when weighted_score is low but 3/4 FAST indicators agree with a large price move), it bypasses the floor check and reaches execution.
**Impact**: Sub-floor-confidence signals can execute on the FAST path.
**Fix**: Add explicit confidence floor check in FAST path before sizing.
**Verdict**: ⚠️ CONCERN

---

**CQ-2 | HIGH | Sprint 1 T-05 | FAST Qualification Double-Counts VWAP, MACD, RSI**
The 4 FAST indicators (VWAP, MACD, RSI, ROC30) at daily_target.py:944-964 are a SUBSET of the 8 SLOW indicators (lines 986-1058). When `is_fast_qualified and fast_direction == direction`, the signal is classified FAST. But the full weighted consensus still runs and produces `weighted_score`. The FAST tier bonus (lines 893-922) adds +0 to +3.5 from "agreeing SLOW indicators" — but VWAP, MACD, and RSI already voted in FAST. This isn't multicollinearity in the statistical sense, but it IS double-counting: the same indicators that qualified the signal as FAST are also boosting its confidence.
**Impact**: FAST signals get inflated confidence from indicators that already voted them in.
**Fix**: Exclude FAST-tier indicators from the bonus calculation, or document that this is intentional signal reinforcement.
**Verdict**: ⚠️ CONCERN

---

**CQ-3 | HIGH | Sprint 1 T-01 | Gap Signal Assumes Momentum Continuation Without Regime Check**
`_scan_gaps()` (daily_target.py:1180-1289) fires gap signals with hardcoded tier="FAST" (line 1274) and confidence=72.0 (line 1272). The gap scan does NOT check regime. A 3% gap in a CRASH or SHOCK regime would still fire. The regime check happens only in `_score_ticker_with_reason` (line 681, `_SKIP_REGIMES`), which is NOT called during gap scanning (Phase 2 is a separate code path).
**Impact**: Gap signals fire in CRASH/SHOCK regimes where gaps are likely dead-cat bounces.
**Fix**: Add regime check in `_scan_gaps()` — at minimum, skip signals in `_SKIP_REGIMES`.
**Verdict**: ❌ BUG

---

**CQ-4 | MEDIUM | Sprint 1 T-08 | ROC(30) Lookback Mismatch with Bar Frequency**
ROC(30) is computed as `(close[-1] - close[-31]) / close[-31] * 100` in indicators.py:264-268. The comment says "30-bar Rate of Change (%) — 30 min on 1-min bars". But the actual bars depend on the data feed frequency. If bars are 5-minute (which some yfinance feeds return for intraday), this becomes ROC(150 min) = 2.5 hours — a completely different signal. The threshold of 1.5% (daily_target.py:961) was calibrated for 30-minute lookback.
**Impact**: If bar frequency changes, ROC(30) threshold becomes meaningless.
**Fix**: Validate bar frequency in compute_all() or compute ROC based on time delta, not bar count.
**Verdict**: ⚠️ CONCERN

---

**CQ-5 | MEDIUM | Sprint 1 T-06 | ADX Acceleration Bonus is Non-Discriminating**
ADX delta > 2.0 pts/bar adds +5.0 effective ADX (daily_target.py:672). ADX acceleration is common during volatile regime transitions (TRENDING → SHOCK) — not just trend births. An ADX jumping from 12 to 15 gets +5 bonus = effective 20, passing the SLOW threshold. But ADX rising rapidly from low values often signals regime instability, not clean trend onset.
**Impact**: False positive entries during regime transitions.
**Fix**: Gate the acceleration bonus on current ADX level (e.g., only apply if ADX already ≥ 12).
**Verdict**: ⚠️ CONCERN

---

**CQ-6 | MEDIUM | Sprint 0 S0-3 + Sprint 1 T-01 | Signal Count Cap Has Dual-Path Accounting**
`_daily_signal_count` is incremented in TWO places: `_scan_gaps()` (line 1281) and the normal scan path (needs verification). The daily cap check also appears in two places (lines 387 and 1162). If a gap signal fires at 09:10, it consumes 1 of 3 daily slots. If the gap signal loses, only 2 recovery signals remain. If the daily cap were per-path instead of shared, gap signals wouldn't cannibalize normal scan capacity.
**Impact**: Gap signals reduce available recovery slots, partially defeating S0-3's purpose.
**Fix**: Consider separate caps or exempting gap signals from the cap.
**Verdict**: ⚠️ CONCERN

---

**CQ-7 | MEDIUM | Sprint 1 T-07 | RVOL Trajectory Offset [-4, -3, -2] Skips Most Recent Bar**
RVOL trajectory in indicators.py:297 uses offsets `[-4, -3, -2]` instead of `[-3, -2, -1]`. This means the "last 3 bars' RVOL" calculation uses bars 4, 3, and 2 positions from the end — skipping the most recent bar. The current bar's RVOL is `snap.rvol` (current), so the trajectory is `current / mean(3 PRIOR bars)`, which is actually correct — it measures acceleration vs recent past. But the comment "last 3 bars" is misleading; it should say "3 prior bars".
**Impact**: None (logic is correct, comment is misleading).
**Verdict**: ✅ CORRECT (documentation nit)

---

**CQ-8 | LOW | Sprint 1 T-05 | ROC(30) None Guard Asymmetry in FAST Check**
In `_determine_direction` (daily_target.py:960-964), ROC(30) has explicit None guard: `roc_30 is not None and roc_30 > 1.5`. When `roc_30 is None`, FAST check has only 3 indicators voting (VWAP, MACD, RSI). The 3/4 threshold becomes 3/3 — unanimity required. This makes FAST qualification HARDER when ROC(30) is unavailable, which is conservative and correct.
**Impact**: Slightly lower FAST qualification rate when data is sparse. Acceptable.
**Verdict**: ✅ CORRECT

---

**CQ-9 | MEDIUM | Sprint 1 T-03 + T-01 | Anomaly Detection Threshold Inconsistency**
`_check_price_anomalies()` (main.py:~1390) triggers on >1% move from session open or >0.5% in 5 min. But gap scan (T-01) triggers on 2.5%/4.0% gaps. A 1.2% move triggers anomaly priority scanning but is below the gap threshold. The anomaly ticker gets prepended to the scan list, but when it reaches `_score_ticker_with_reason`, it faces normal gates. The anomaly detection is more sensitive than the gap gate, creating a "priority scan that mostly rejects" pattern — noisy but not harmful.
**Impact**: Wasted compute on anomaly-priority tickers that rarely pass gates.
**Verdict**: ✅ CORRECT (by design — anomaly detection is a dragnet, not a signal)

---

**CQ-10 | HIGH | Sprint 2 SK-02 + Sprint 1 T-10 | 12h Window Doesn't Span Overnight Gap**
SK-02 adds `WHERE exit_time >= datetime('now', '-12 hours')` to consecutive loss queries. LSE trades during 09:00-16:30 UK. A trade exiting at 15:00 on Day 1 is invisible by 03:00 on Day 2 — BEFORE the 09:00 trading session begins. At 09:00 Day 2, the query looks back 12h (to 21:00 Day 1) and sees nothing from the 15:00 exit.
However, `reset_daily()` clears `_consecutive_losses = 0` at daily reset, so this is moot — the losses are cleared anyway. The 12h window only matters for intra-session queries. Within a single LSE session (09:00-16:30 = 7.5 hours), 12h is more than sufficient.
**Impact**: None — daily reset zeroes the counter, 12h > session length.
**Verdict**: ✅ CORRECT

---

**CQ-11 | MEDIUM | Sprint 1 T-02 | Lunch Window End at 13:00 But Comment Says 11:30-13:00**
The lunch window expression `(now_uk.hour == 11 and now_uk.minute >= 30) or now_uk.hour == 12` covers 11:30-12:59. The FAST path lunch block at main.py:3907 logs "BLOCKED during lunch (11:30-13:00)". But `now_uk.hour == 12` only matches 12:00-12:59, NOT 13:00. So the actual window is 11:30-12:59, and the log message is off by 1 minute. Not a functional bug, but the log is misleading.
**Impact**: Cosmetic — log says 13:00 but actual block ends at 12:59.
**Verdict**: ✅ CORRECT (off-by-one in log string only)

---

### LEAD SYSTEMS ARCHITECT (SA) — Thread Safety, Async/Sync, Infrastructure

---

**SA-1 | CRITICAL | Sprint 1 T-10 | FAST Path Missing Flow Control — Earnings Fade Gate Runs for FAST Signals**
At main.py:3927-3932, the FAST path logs "FAST PATH QUALIFIED" but does NOT `continue` or jump past the confidence modifiers. The individual modifier blocks (PEAD, VWAP, sector, etc.) are gated by `if _signal_tier != "FAST"`, so they're correctly skipped. However, the Earnings Fade Gate (RC-07b, line 3936) has NO tier guard — it runs for ALL signals including FAST.
This is actually CORRECT behavior — earnings fade is a safety veto, not a confidence modifier. Explicitly intended. But the code structure is fragile: any future modifier added without the `_signal_tier != "FAST"` guard will apply to FAST signals unintentionally.
**Impact**: Correct today, but brittle for future changes.
**Fix**: Refactor to use explicit `if _signal_tier == "FAST": ... continue` after essential gates, or add a comment marking where FAST-safe gates end and SLOW-only modifiers begin.
**Verdict**: ⚠️ CONCERN

---

**SA-2 | HIGH | Sprint 1 T-04 | Sync Redis Client Created Per S15 Constructor — No Connection Pooling**
At main.py:1331, a NEW `redis.Redis()` client is created for S15 injection. The main engine also uses async Redis (via `aioredis`). S15 uses synchronous `scan()` calls. If strategies are reconstructed (e.g., on reload/redeploy), stale Redis connections accumulate. The `socket_connect_timeout=3` prevents blocking but doesn't reclaim connections.
**Impact**: Potential Redis connection leak on strategy reinit.
**Fix**: Use a shared sync Redis client or implement proper connection cleanup in S15 teardown.
**Verdict**: ⚠️ CONCERN

---

**SA-3 | HIGH | Sprint 1 T-04 | VIX Supremacy SCAN+DEL is Synchronous in Scan Thread**
`_scan_gaps()` and `scan()` are synchronous methods called from APScheduler threads. The VIX supremacy SCAN+DEL at daily_target.py:429-437 iterates Redis keys with `count=100`. For 12-50 GPD keys, this is 1 SCAN iteration (fast). But if key namespace grows (e.g., debug keys accumulate), the SCAN loop becomes slower. The APScheduler 60s cron interval acts as a natural timeout — if scan() takes >60s, `max_instances=1 + coalesce=True` skips the next run rather than stacking.
**Impact**: Low risk — 12 keys = 1 SCAN iteration < 1ms. APScheduler prevents stacking.
**Verdict**: ✅ CORRECT

---

**SA-4 | MEDIUM | Sprint 1 T-03 | Anomaly Detection Redis Keys Lack Namespace Isolation**
`_check_price_anomalies()` uses `nzt:price_5m:{ticker}` and `nzt:last_scan:{ticker}` Redis keys. These are in the same Redis instance as GPD cache (`nzt:gpd:{ticker}`). The VIX supremacy SCAN+DEL pattern (daily_target.py:431) uses `match="nzt:gpd:*"` — correctly scoped to GPD keys only. No cross-contamination risk. But all NZT keys share the flat `nzt:` namespace with no formal schema.
**Impact**: Low — current patterns are correctly scoped.
**Verdict**: ✅ CORRECT

---

**SA-5 | HIGH | Sprint 2 SK-01 | SheetsLogger Starting Equity Set at Init Only**
`SheetsLogger(starting_equity=self.equity)` at main.py:678 captures equity at engine boot time. The sheets logger uses `_starting_equity` for daily P&L % calculations. Unlike circuit_breakers (which gets `reset_daily(current_equity=self.equity)` every morning), the SheetsLogger does NOT receive equity updates. After Day 1, sheet P&L % is computed against boot-time equity.
**Impact**: Sheets logging P&L % drifts from actual daily performance over time.
**Fix**: Add equity refresh to SheetsLogger's daily cycle (same pattern as circuit_breakers.reset_daily).
**Verdict**: ❌ BUG

---

**SA-6 | MEDIUM | Sprint 2 SK-01 | self.equity Freshness at Daily Reset Time**
`self.circuit_breakers.reset_daily(current_equity=self.equity)` at main.py:6757. The `self.equity` value depends on when it was last updated. It's computed from IBKR account value or SQLite P&L summation. If the daily reset fires before equity is refreshed (e.g., 00:00 UTC before any account sync), `self.equity` could be yesterday's end-of-day value — which is actually correct for "start-of-day equity". But if `self.equity` hasn't been initialized yet (container just started), it falls back to the constructor default.
**Impact**: If container restarts between sessions, initial equity may be stale. Low risk in practice — nightly equity sync typically runs before daily reset.
**Verdict**: ⚠️ CONCERN

---

**SA-7 | MEDIUM | Sprint 1 T-01 | Session Opens Dict Not Persistent Across Container Restarts**
`self._session_opens = {}` in the S15 constructor (daily_target.py:310). Session opens are recorded during Phase 1 (09:00-09:05). If the container restarts at 09:10, session opens are lost. Gap scan requires open prices. The gap scan phase (09:05-09:15) would have empty `_session_opens` and emit no gap signals.
**Impact**: Container restart during 09:00-09:15 loses gap scanning for that session.
**Fix**: Persist session opens to Redis (same pattern as price_5m keys).
**Verdict**: ⚠️ CONCERN

---

**SA-8 | SHOWSTOPPER | Sprint 0 S0-7 | Data Freshness Gate Kills ALL Normal Scan Signals**
**CONFIRMED ON PRODUCTION** via `docker exec` indentation analysis (2026-03-07).

At daily_target.py:479-483, the per-ticker stale check has a catastrophic control flow bug:
```
L479 [indent=12]: if hasattr(snap, 'timestamp') and snap.timestamp:
L480 [indent=16]:     _ticker_age = (datetime.now(timezone.utc) - snap.timestamp).total_seconds()
L481 [indent=16]:     if _ticker_age > _MAX_DATA_AGE_SECONDS:
L482 [indent=20]:         gate_rejections[ticker] = f"stale_data({_ticker_age:.0f}s)"
L483 [indent=16]:     continue  # <-- SAME INDENT AS L481, INSIDE OUTER if, OUTSIDE INNER if
```
The `continue` at L483 (indent=16) is:
- INSIDE the outer `if hasattr(snap, 'timestamp') and snap.timestamp:` (indent=12)
- OUTSIDE the inner `if _ticker_age > _MAX_DATA_AGE_SECONDS:` (indent=16)

`IndicatorSnapshot.timestamp` is a REQUIRED field (models.py:158) — every valid snapshot has one. Therefore ALL tickers with valid snapshots hit `continue` and are NEVER passed to `_score_ticker_with_reason()`.

**This means the entire normal scan path (Phase 3+) has been DEAD since Sprint 0 introduced S0-7.** The ONLY signals that can fire are:
1. Gap signals from `_scan_gaps()` (separate code path, Phase 2)
2. Anomaly-triggered signals (if they use a different entry point)

This likely explains a significant portion of the 0% win rate on 52 paper trades — ALL 52 trades were gap-only signals.

**Fix**: Indent `continue` to indent=20 (inside the inner `if _ticker_age > _MAX_DATA_AGE_SECONDS:` block).
**Impact**: SHOWSTOPPER — normal scan path completely non-functional.
**Verdict**: ❌ SHOWSTOPPER BUG — Sprint 3 P0 #1

---

**SA-9 | MEDIUM | Sprint 1 T-04 | Nightly GPD Batch Uses `pass` for Per-Ticker Failures**
At main.py:7930, `except Exception: pass` swallows ALL errors per ticker during GPD batch computation. This includes network timeouts, malformed yfinance data, and GPD computation errors. With 12+ tickers, silent failures mean some tickers have no GPD cache, and the missing-key path allows trades (fail-open).
**Impact**: Silent GPD gaps allow unvetted tickers through.
**Fix**: Log per-ticker failures at DEBUG level instead of bare `pass`.
**Verdict**: ⚠️ CONCERN

---

**SA-10 | MEDIUM | Sprint 0 S0-1 + Sprint 1 T-04 | VIX=35.0 Default Skipped by Session Open VIX Capture**
S0-1 sets VIX fail-closed default to 35.0. T-04's session open VIX capture (daily_target.py:~410) explicitly skips VIX=35.0: "skips VIX=35.0 default". This is correct — you don't want the fail-closed fallback to set session_open_vix, because then a VIX recovery to 25 would trigger VIX supremacy (delta = 25-35 = -10, which is <+10 so no trigger). But what if REAL VIX happens to be exactly 35.0? The skip logic would incorrectly treat real VIX=35.0 as a fallback value.
**Impact**: In the rare case real VIX = exactly 35.0, session_open_vix would not be captured. VIX supremacy delta calculation would use None as base, which likely skips the check entirely.
**Fix**: Use a sentinel value (e.g., -1.0) for fallback instead of a valid VIX level.
**Verdict**: ⚠️ CONCERN

---

**SA-11 | LOW | Sprint 1 T-03 | Anomaly Debounce Uses Redis TTL Which Can Desync**
The 30s debounce (`nzt:last_scan:{ticker}`) uses `SETEX ... 30`. Redis TTL precision depends on server clock. If Redis time drifts from engine time (Docker containers can have clock skew), debounce could be shorter or longer than intended. In practice, Docker containers share host clock, so this is minimal risk.
**Verdict**: ✅ CORRECT

---

### CHIEF RISK OFFICER (CRO) — Loss Limits, Circuit Breakers, Catastrophic Scenarios

---

**CRO-1 | CRITICAL | Sprint 0 S0-3 + Sprint 1 T-01 | 3 Signals/Day on 3x/5x = Potential 4.5% Daily Loss**
S0-3 raised the daily cap from 1 to 3 signals. Each S15 trade has a 1.5x ATR stop. With ATR ~1% on 3x ETPs, stop distance = ~1.5%. Three consecutive stopped-out trades = 3 x 1.5% = 4.5% loss. The circuit breaker daily loss limit is -3% (main.py:4141). After 2 losses (3% total), the circuit breaker should halt trading. But the circuit breaker checks `self._daily_pnl_pct < -0.03` — this is checked BEFORE each trade entry, not between trades. If 2 trades are entered in rapid succession (e.g., anomaly-triggered priority scan fires immediately after a loss), both could execute before the loss is realized and the P&L updated.
**Impact**: Race condition between P&L update and next signal entry. Quarter-Kelly sizing (0.75% max risk per trade) limits actual risk to ~2.25% max for 3 trades.
**Fix**: Force P&L refresh before each S15 entry, or add a trade-level cooldown after losses.
**Verdict**: ⚠️ CONCERN

---

**CRO-2 | CRITICAL | Sprint 0 S0-4 | Session Halt Removal Has No Replacement Cap**
S0-4 removed the +1.5% session halt. There is now NO upper bound on daily P&L. While this is intentional ("leaving 30% of daily target on the table"), it also removes the profit-taking discipline. In a volatile session, the engine could enter 3 positions that all go +3%, then reverse and stop out — turning a +9% day into a -4.5% day. The circuit breaker only triggers at -3%, so the reversal has to cross +3% → -3% (a 6% swing) to get caught.
**Impact**: Profitable sessions can reverse into losses without circuit breaker triggering.
**Fix**: Consider a trailing daily profit protector (e.g., halt if P&L drops 50% from intraday high).
**Verdict**: ⚠️ CONCERN

---

**CRO-3 | HIGH | Sprint 2 SK-01 | Circuit Breaker Equity Update Has No Validation Beyond > 0**
`reset_daily(current_equity)` at circuit_breakers.py:314 checks `current_equity > 0` only. If `self.equity` is corrupted (e.g., IBKR returns 0.01 due to API error), the circuit breaker would use 0.01 as the denominator — making every tiny loss appear as a massive drawdown percentage. The engine would immediately halt.
This is actually FAIL-SAFE: a corrupted low equity = aggressive circuit breaking. The danger is the opposite — if equity is corrupted HIGH (e.g., 1,000,000 due to API bug), the circuit breaker would use 1M as denominator, making real losses appear tiny. -3% of 1M = -30K, but actual equity is 10K.
**Impact**: Corrupted high equity = circuit breaker effectively disabled.
**Fix**: Add bounds checking (e.g., equity must be within 50%-200% of previous day's equity).
**Verdict**: ⚠️ CONCERN

---

**CRO-4 | HIGH | Sprint 1 T-04 | VIX Emergency Veto Only Blocks LONGs**
VIX supremacy at daily_target.py:422-425 says "EMERGENCY VETO ACTIVE, blocking all new LONG entries". But `_emergency_tail_risk_veto` in the GPD pre-screen (line 528-531) removes ALL candidates regardless of direction — it doesn't check if the signal is LONG or SHORT. The log message is misleading but the code is correctly conservative: it blocks ALL new entries during VIX spikes, not just LONGs.
**Wait** — re-reading line 529-530: `candidates.remove(cand)` removes the candidate unconditionally. So it blocks LONG AND SHORT during VIX emergency. This is overly conservative for inverse ETPs (which profit from crashes), but fail-closed is acceptable.
**Impact**: Inverse ETPs (QQQS.L, 3USS.L) blocked during VIX spikes — missed SHORT opportunities.
**Verdict**: ⚠️ CONCERN (fail-closed is safe but suboptimal)

---

**CRO-5 | HIGH | Sprint 2 SK-02 | 12h Window Can Miss Cross-Session Losing Streaks**
If the engine trades near LSE close (15:00-16:30) and the next session opens at 09:00, the gap is ~16.5 hours. But `reset_daily()` clears `_consecutive_losses = 0` at daily reset. So cross-session streaks are cleared anyway. The 12h window is only relevant for INTRA-session streaks.
However: what if `reset_daily()` fails? The `except` at main.py:6759 catches the error and logs it, but consecutive losses would carry over from yesterday with the 12h window. At 09:00 on Day 2, `datetime('now', '-12 hours')` = 21:00 Day 1. Any losses from Day 1's afternoon session (15:00-16:30) would be invisible. So if reset_daily fails AND yesterday had a losing streak, the zombie halt bug is partially re-introduced.
**Impact**: Edge case — only if reset_daily() fails. Low probability, medium impact.
**Verdict**: ⚠️ CONCERN

---

**CRO-6 | MEDIUM | Sprint 1 T-01 | Gap Signals Have Fixed Confidence=72 Regardless of Gap Size**
All gap signals get confidence=72.0 (daily_target.py:1272). A 2.6% gap (barely above threshold) and a 8% gap (massive move) get identical confidence. Larger gaps have higher completion probability but also higher mean-reversion risk. The fixed confidence prevents the sizing algorithm from distinguishing gap quality.
**Impact**: Suboptimal position sizing — all gaps treated equally.
**Fix**: Scale confidence with gap magnitude (e.g., 70 + gap_pct * 2, capped at 85).
**Verdict**: ⚠️ CONCERN

---

**CRO-7 | MEDIUM | Sprint 0 S0-2 + Sprint 1 T-05 | Confidence Floor 65 + FAST Bypass = Implicit Floor ~50**
Sprint 0 lowered confidence floor from 75 to 65. FAST tier skips confidence modifiers (which can add or subtract). But FAST signals can have low raw confidence from `_determine_direction` — the weighted_score might only reach 55-60 range. Since FAST bypasses the confidence floor check in the normal path, signals with confidence below 65 can execute.
Quarter-Kelly sizing uses confidence as an input. Confidence=55 → smaller position. This is risk-aware by construction, but it still means sub-floor signals are traded.
**Impact**: FAST signals occasionally execute at confidence levels that Sprint 0 explicitly rejected.
**Verdict**: ⚠️ CONCERN

---

**CRO-8 | MEDIUM | Sprint 2 SK-02 | go_nogo.py Uses 30-Day Window — System Can Be Permanently Blocked by 30-Day Losing Streak**
Go/No-Go criterion 6 (go_nogo.py:155-176) checks for "never 5 consecutive losses" over a 30-day rolling window. If the engine has 5 consecutive losses spread across a month, the Go/No-Go gate remains blocked for up to 30 days after the last loss, even if recent trades are profitable. The original bug (no time filter) was "permanent block"; the fix created a "30-day block".
**Impact**: Acceptable — 5 consecutive losses in 30 days is a genuine red flag. The 30-day window is appropriate for system fitness evaluation.
**Verdict**: ✅ CORRECT

---

**CRO-9 | LOW | Sprint 1 T-02 | Lunch RVOL Floor Reduction (0.60 → 0.50) Increases Slippage Risk**
Reducing the RVOL floor during lunch from 0.60 to 0.50 allows trades in thinner markets. LSE lunch hours (11:30-13:00) have widest spreads and lowest volume. Even with the spread gate (35 bps), RVOL=0.50 means volume is half the 20-day average — fills may be partial or adverse.
**Impact**: Higher slippage on lunch-window trades. Mitigated by spread gate.
**Verdict**: ✅ CORRECT (acceptable tradeoff)

---

**CRO-10 | HIGH | Sprint 1 T-04 | GPD Nightly Batch Failure = Stale Cache for 24+ Hours**
If the nightly GPD batch (main.py:7886-7945) fails completely (e.g., yfinance outage), all `nzt:gpd:*` keys expire after 24h TTL. The next day, all GPD lookups find no key. The missing-key path at daily_target.py:544 says "no veto" — so all tickers pass GPD screening. If `_emergency_tail_risk_veto` is also False (cleared at daily reset), there's no tail risk protection at all.
**Impact**: Full GPD protection loss after nightly batch failure + 24h TTL expiry.
**Fix**: Log a WARNING when >50% of GPD keys are missing. Consider extending TTL to 48h.
**Verdict**: ⚠️ CONCERN

---

**CRO-11 | MEDIUM | Cross-Sprint | Sprint 0 S0-1 + Sprint 2 SK-01 | VIX=35 Default + Stale Equity = Double Conservatism**
If VIX fetch fails (S0-1: default 35.0) AND equity is stale-low (SK-01 edge case), the circuit breaker is doubly conservative: VIX=35 blocks 5x tickers AND circuit breaker triggers more easily. This is fail-safe behavior — both defaults err on the side of caution. No corrective action needed.
**Verdict**: ✅ CORRECT

---

### ACADEMIC REVIEWER (AR) — Citation Accuracy, Methodology, Overfitting

---

**AR-1 | HIGH | Sprint 1 T-01 | Gao et al. (2018) Cited for Opening Gap, But Paper Studies Index ETFs, Not Leveraged ETPs**
T-01 cites "Gao et al. (2018): first-30-min return predicts EOD direction 62% for leveraged ETPs." Gao et al. (2018) — "Intraday Momentum: The First Half-Hour Return Predicts the Last Half-Hour Return" (Journal of Financial Economics) — studies broad market indices and ETFs, not specifically leveraged ETPs. The 62% predictive accuracy for leveraged ETPs is an extrapolation, not a direct finding from the paper. Leveraged ETPs have path-dependency that may invalidate the extrapolation.
**Impact**: Overstated academic backing for gap signal thresholds.
**Fix**: Qualify the citation: "Gao et al. (2018) for index ETFs; applied to leveraged ETPs as hypothesis."
**Verdict**: ⚠️ CONCERN

---

**AR-2 | HIGH | Sprint 1 T-05 | 3/4 FAST Indicator Threshold Lacks Statistical Justification**
The FAST tier requires 3/4 leading indicators to agree (daily_target.py:940-974). The comment at line 941 notes "3/4 alone has ~31% FPR; price move threshold is the binding constraint." But the 31% FPR is stated without derivation or citation. Under independence, P(3/4 agree by chance) = 4 * (0.5)^4 = 25% for binary indicators. With the correlation structure of VWAP/MACD/RSI/ROC (which are correlated), the FPR could be higher. The price move threshold (2.5%/4.0%) is the real gate, making the 3/4 check partially redundant.
**Impact**: FAST tier's statistical edge comes from the price move threshold, not the indicator agreement. The 3/4 check adds minimal value.
**Verdict**: ⚠️ CONCERN

---

**AR-3 | MEDIUM | Sprint 1 T-06 | ADX=15 Threshold for FAST Tier is Below Wilder's "Trending" Definition**
Wilder (1978) defined ADX < 20 as "no trend" and ADX > 25 as "strong trend." The FAST tier uses ADX ≥ 15 (daily_target.py:80), below Wilder's trending onset. The comment says "catch trend birth (Wilder 1978 onset zone)" — but Wilder's onset zone is 20-25, not 15. ADX=15 is in Wilder's "non-trending" zone.
**Impact**: FAST tier trades in conditions Wilder classified as trendless. The ADX acceleration bonus (+5) can rescue to effective 20, but the base threshold is academically unsupported.
**Fix**: Either raise FAST floor to 18-20, or cite a different source for the 15 threshold.
**Verdict**: ⚠️ CONCERN

---

**AR-4 | MEDIUM | Sprint 0 S0-2 | Confidence Floor Lowered Without Backtesting Evidence**
`_MIN_CONFIDENCE` lowered from 75 to 65 with comment "aligned with risk_sizer floor" and citation "Harvey & Liu 2015". Harvey & Liu (2015) — "...and the Cross-Section of Expected Returns" — discusses multiple testing in factor research. It does NOT validate or recommend any specific confidence threshold. The alignment rationale ("risk_sizer accepts at 65") is an implementation argument, not a statistical one.
**Impact**: No empirical evidence that 65 is the correct threshold. Risk_sizer floor is an arbitrary number too.
**Fix**: Run backtests comparing WR at conf≥65 vs conf≥75 on historical signals.
**Verdict**: ⚠️ CONCERN

---

**AR-5 | MEDIUM | Sprint 1 T-07 | RVOL Thresholds Are Round Numbers Without Empirical Calibration**
_MIN_RVOL_FAST=0.60, _MIN_RVOL_SLOW=0.65, _MIN_RVOL_LUNCH=0.50, _MIN_RVOL_RANGE_BOUND=1.2. These are round numbers — the hallmark of hand-tuned parameters. The comment cites "Chan (2013)" for RVOL thresholds, but Chan's "Algorithmic Trading" uses RVOL as a generic quality filter without specifying exact thresholds for leveraged ETPs.
**Impact**: Parameter choices may be arbitrary. Round numbers suggest they weren't derived from data.
**Fix**: Profile RVOL distribution of winning vs losing trades to empirically set thresholds.
**Verdict**: ⚠️ CONCERN

---

**AR-6 | LOW | Sprint 1 T-08 | ROC(30) Threshold 1.5% Is Arbitrary**
ROC(30) > 1.5 or < -1.5 for FAST qualification (daily_target.py:961-963). The comment says "raised from initial ROC(5) which was pure noise." The 1.5% threshold is not derived from any distribution analysis of 30-bar returns on LSE leveraged ETPs. On 3x ETPs, 1.5% over 30 minutes represents ~0.5% underlying move — which could be noise on volatile days.
**Impact**: Threshold may be too low on high-vol days (false positives) and too high on low-vol days (false negatives).
**Verdict**: ⚠️ CONCERN

---

**AR-7 | MEDIUM | Sprint 2 SK-02 | SQLite datetime('now') Uses UTC — Correct for Docker But Fragile**
All SK-02 queries use `datetime('now', '-12 hours')`. SQLite's `datetime('now')` returns UTC. The engine runs in Docker with UTC system clock. All timestamps stored in the database should be UTC. But if any code path stores UK local time as `exit_time`, the 12h window would be miscalculated during BST (UTC+1).
**Impact**: If timestamps are inconsistent (some UTC, some UK), queries return wrong results. Need to verify all INSERT paths use UTC.
**Verdict**: ⚠️ CONCERN

---

**AR-8 | LOW | Sprint 1 T-04 | GPD Uses 270 Days of Daily Returns — Insufficient for Extreme Tail Estimation**
The nightly batch (main.py:7911) downloads `period="270d"` of daily returns. Generalized Pareto Distribution requires sufficient extreme observations. With 270 trading days, there might be 10-15 extreme events (5th percentile = 13.5 observations). McNeil & Frey (2000) recommend minimum 250 observations for POT method, with 5% threshold = 12.5 tail observations. This is borderline — adequate for coarse screening but not for precise VaR estimation.
**Impact**: GPD estimates may be unstable, especially for recently listed ETPs with shorter histories.
**Verdict**: ✅ CORRECT (borderline but adequate for binary veto decision)

---

**AR-9 | MEDIUM | Sprint 0-2 Combined | 25+ Tunable Parameters = Overfitting Risk**
Across Sprints 0-2, the system now has: _MIN_CONFIDENCE, _MIN_ATR_PCT, _MIN_RVOL_FAST/SLOW/LUNCH/RANGE_BOUND, _MIN_ADX_FAST/SLOW/RANGE_BOUND, _ADX_ACCEL_THRESHOLD, _RVOL_RISING_THRESHOLD, _GAP_THRESHOLD_3X/5X, _GAP_MAX_SPREAD_BPS, _MAX_SIGNALS_PER_DAY, ROC(30) threshold 1.5, VIX supremacy delta threshold +10, confidence=72.0 for gaps, lunch window times, 12h consecutive loss window, 30-day go/no-go window, and more.
Each parameter was set individually without joint optimization. The probability that ALL parameters are simultaneously optimal approaches zero. This is classic "researcher degrees of freedom" (Simmons, Nelson & Simonsohn 2011).
**Impact**: System may be overfit to the developer's mental model rather than to data.
**Fix**: Define a minimal parameter set. Log sensitivity analysis: ±20% on each parameter, measure signal count change.
**Verdict**: ⚠️ CONCERN

---

**AR-10 | LOW | Sprint 1 T-05 | Ben-David et al. (2018) Citation Correctly Applied**
T-05 cites Ben-David, Franzoni & Moussawi (2018) for leveraged ETP oscillator miscalibration and applies reduced EMA50/EMA20 weights for `_BROAD_LEVERAGED_ETPS`. The paper's finding that leveraged ETF intraday flows are dominated by mechanical rebalancing is directly relevant. Downweighting lagging EMAs for leveraged products is a sound application.
**Verdict**: ✅ CORRECT

---

**AR-11 | MEDIUM | Sprint 1 T-06 | ADX Delta (Point Change Per Bar) Is Not Normalized**
ADX delta at indicators.py:283 is `adx_cur - adx_prev` — raw point difference per bar. But ADX is already bounded [0, 100], so a 2-point change from 10→12 is qualitatively different from 50→52. At low ADX (non-trending), 2 points = significant regime shift. At high ADX (strong trend), 2 points = noise within a trend. The acceleration threshold should be relative, not absolute.
**Impact**: Acceleration bonus applies too freely at low ADX levels (where it matters least) and too restrictively at high ADX levels.
**Verdict**: ⚠️ CONCERN

---

---

## SECTION 2: Cross-Sprint Interaction Matrix

| # | Sprint A | Sprint B | Interaction | Type |
|---|----------|----------|-------------|------|
| 1 | S0-2 (conf floor 65) | T-05 (FAST path) | FAST signals bypass conf floor. S0-2 lowered the floor that FAST doesn't even check. Combined effect: sub-65 signals can execute. | ⚠️ Emergent gap |
| 2 | S0-3 (3 signals/day) | T-01 (gap signals) | Gap signals consume daily cap slots. A 09:10 gap signal + 2 normal signals = cap reached. S0-3 was meant to enable recovery trades, but T-01 gap eats a slot. | ⚠️ Partial defeat |
| 3 | S0-1 (VIX=35 default) | T-04 (VIX supremacy) | VIX supremacy skips session_open_vix capture when VIX=35.0 (assumed fallback). S0-1 made 35.0 the fail-closed default. If VIX genuinely is 35.0, session_open_vix stays None and supremacy check is inactive. | ⚠️ Edge case gap |
| 4 | S0-4 (no +1.5% halt) | SK-01 (equity denominator) | Without session halt, equity can grow significantly intra-day. SK-01 updates equity only at daily reset. Intra-day equity growth isn't reflected in circuit breaker calculations — the 3% loss limit is calculated against morning equity, not peak equity. This is actually CORRECT (drawdown from start-of-day, not from peak). | ✅ Correct interaction |
| 5 | T-01 (gap scan 09:05-09:15) | SK-02 (12h loss window) | At 09:05, the 12h window reaches back to 21:05 yesterday. If yesterday's losses occurred at 15:00-16:30, they're within the 12h window and will show as consecutive losses, potentially blocking gap signals. But reset_daily() clears the counter at session start. If reset_daily fires before 09:05 (which it does — 00:00 UTC), gap signals see clean slate. | ✅ Correct interaction |
| 6 | T-07 (RVOL tiers) | S0-7 (data freshness) | Stale data (>120s) is skipped by S0-7. If RVOL data is stale, the ticker is rejected before T-07's RVOL gate runs. S0-7 acts as an upstream guard — correct ordering. | ✅ Correct interaction |
| 7 | T-10 (FAST path skips modifiers) | SK-01 (equity in circuit breaker) | FAST path still checks circuit breaker (GATE 2, main.py:4141). With SK-01, circuit breaker uses correct daily equity. FAST path gets accurate drawdown protection. | ✅ Correct interaction |

---

## SECTION 3: Failure Cascade Analysis

### Scenario 1: Redis Goes Down During Market Hours

| Sprint Change | Impact |
|---|---|
| T-03 (anomaly detection) | `_check_price_anomalies()` tries Redis GET/SETEX. try/except swallows errors. Anomaly detection silently disabled — falls back to normal scan order. |
| T-04 (GPD cache) | `self._redis_client.get(f"nzt:gpd:{ticker}")` fails. Outer try/except at daily_target.py:547 catches it. GPD pre-screen silently disabled — all candidates pass. **FAIL-OPEN.** |
| T-04 (VIX supremacy) | SCAN+DEL at daily_target.py:428-440 fails. try/except logs error but `_emergency_tail_risk_veto` is still set to True (set before Redis call). Veto active but cache not invalidated — stale GPD data persists until Redis recovers. |
| T-01 (session opens) | Session opens stored in Python dict, not Redis. **Unaffected.** |
| SK-02 (consecutive losses) | Queries SQLite, not Redis. **Unaffected.** |
| **Net effect** | GPD tail risk protection = OFF (fail-open). Anomaly priority = OFF. All other gates intact. System trades without tail risk screening. MEDIUM RISK. |

### Scenario 2: VIX Data Unavailable for Entire Day

| Sprint Change | Impact |
|---|---|
| S0-1 (VIX fail-closed) | VIX defaults to 35.0. All 5x tickers blocked (VIX ≥ 22). All 3x tickers get half-size (VIX ≥ 22). |
| T-04 (VIX supremacy) | Session open VIX = None (skips 35.0 as fallback). VIX supremacy inactive — no emergency veto regardless of events. Delta cannot be computed without base. |
| T-10 (FAST path VIX gate) | VIX=35 from market_ctx. Half-size applied to all FAST signals. 5x blocked. |
| **Net effect** | Highly conservative: 5x blocked, 3x at half-size. VIX supremacy inactive but S0-1 provides baseline protection. LOW RISK (overly conservative, not dangerous). |

### Scenario 3: yfinance Returns Empty Data for All Tickers

| Sprint Change | Impact |
|---|---|
| T-08 (new indicators) | ROC(30), ADX delta, RVOL trajectory all fail with empty DataFrames. try/except defaults to None. Indicators remain None. |
| T-05 (FAST tier) | ROC(30) = None → FAST only has 3 indicators (VWAP, MACD, RSI). 3/3 unanimity needed. FAST qualification harder but possible. |
| T-04 (GPD nightly batch) | yfinance.Ticker.history() returns empty → `len(_hist) < 50` → skip. All GPD keys expire after 24h. Next day: no GPD protection (fail-open). |
| S0-7 (data freshness) | If indicator snapshots have timestamps, data freshness gate checks them. But empty yfinance = no new data = stale timestamps → data freshness gate BLOCKS all tickers. **System self-halts.** |
| **Net effect** | S0-7 data freshness gate is the primary defense. If ALL data is stale, the system refuses to trade. SAFE. |

### Scenario 4: IBKR Gateway Disconnects Mid-Trade

| Sprint Change | Impact |
|---|---|
| SK-01 (equity denominator) | `self.equity` may freeze at last-known value. Circuit breaker uses frozen equity — acceptable for remainder of session. |
| T-01/T-05/T-10 | Signal generation unaffected (uses yfinance data, not IBKR). But order execution fails. Virtual trader may still process signals in paper mode. |
| All sprints | Primary impact is order execution, not signal generation. Sprint changes are upstream of execution. IBKR disconnect is handled by existing reconnection logic in IBC. |
| **Net effect** | Paper mode is largely unaffected (virtual_trader doesn't need IBKR). Live mode: orders fail, positions can't be opened. Existing reconnection handles recovery. LOW RISK for paper mode. |

### Scenario 5: Container Restart at 09:10 UK (During GAP SCAN Phase)

| Sprint Change | Impact |
|---|---|
| T-01 (session opens) | `_session_opens = {}` in constructor. Session opens from 09:00-09:05 are LOST. Gap scan (09:05-09:15) has no open prices → `_scan_gaps()` returns empty → no gap signals. |
| T-04 (VIX supremacy) | `_session_open_vix = None` in constructor. VIX supremacy inactive for rest of day (cannot compute delta without base). |
| T-04 (GPD cache) | Redis cache persists across container restarts (Redis is a separate container). GPD screening continues normally. |
| SK-01 (equity) | `self.equity` re-initialized from constructor default or SQLite. `reset_daily(current_equity)` may or may not have fired. If daily reset already happened (before 09:10), circuit breaker has correct equity. If not, uses init default. |
| T-03 (anomaly detection) | Redis-based debounce keys persist. Anomaly detection resumes immediately. |
| **Net effect** | Gap scanning lost for the day. VIX supremacy inactive. All other protections intact. MEDIUM RISK — loss of gap signal opportunity, not a safety issue. |

---

## SECTION 4: Statistical Validity

### Parameter Overfitting
**Rating: MODERATE RISK.** 25+ tunable parameters set by hand. No joint optimization or cross-validation. Each parameter was individually rationalized with citations but not empirically validated on this specific system. The 100-Trade Validation Gate (WR≥40%) in the AEGIS plan is the correct remedy — it's a forward-looking out-of-sample test.

### Look-Ahead Bias
**Rating: LOW RISK.** No computations use future data. ROC(30) uses `close[-1]` and `close[-31]` — both past. ADX delta uses `iloc[-1]` and `iloc[-2]` — both past. GPD uses 270 days of historical returns — all past. Session opens are recorded during OBSERVE phase and used in GAP SCAN phase (correct temporal ordering).

### Survivorship Bias
**Rating: LOW RISK.** The ticker universe is fixed in `uk_isa/isa_universe.py` — all currently listed LSE ETPs. There's no backtesting against historical universe that excluded delisted products. For forward-looking paper trading, survivorship bias is not applicable (you trade what currently exists).

### Multiple Comparison Problem
**Rating: HIGH RISK.** S15 has 8 indicator gates + regime gate + RVOL gate + ADX gate + ATR gate + VIX gate + spread gate + confidence floor + GPD tail risk + data freshness = ~14 gates. Each gate has ~5-30% rejection rate. Combined false negative rate (rejecting a genuinely good trade) could be 60-80%. The 0% win rate on 52 trades may have been partly caused by over-filtering, not just timing. Sprint 1's tier system (FAST = 8 gates, SLOW = all gates) is the right structural fix.

### Multicollinearity
**Rating: MODERATE RISK.** VWAP, MACD, and RSI are correlated in trending markets (all agree). The FAST tier uses 3/4 agreement among these + ROC(30). When they agree, it's strong confirmation. When they disagree, FAST doesn't qualify. The risk is in SLOW tier: weighted consensus of 8 indicators where VWAP+MACD+RSI dominate the weights (1.8+1.5+1.5 = 4.8 out of 10.0). Stochastic RSI is highly correlated with RSI. Effective degrees of freedom are lower than 8.

---

## SECTION 5: Summary Scorecard

| Metric | Sprint 0 | Sprint 1 | Sprint 2 |
|--------|----------|----------|----------|
| **Correctness** | 8/10 | 7/10 | 9/10 |
| **Safety (fail-closed)** | 9/10 | 7/10 | 8/10 |
| **Maintainability** | 8/10 | 5/10 | 9/10 |
| **Expected WR Impact** | +5-10% | +15-25% | +3-5% |

**Sprint 0**: Clean, minimal changes. VIX fail-closed (S0-1) and data freshness (S0-7) are the standouts. Session halt removal (S0-4) is the riskiest change — correct intent but no replacement cap.

**Sprint 1**: Highest impact but also highest complexity. T-05 FAST/SLOW tier is architecturally sound but creates a complex flow control problem in `_execute_s15_priority_path`. T-01 gap scan is the biggest win (captures opening moves). Maintainability suffers from 1,300 lines of strategy code with 25+ constants.

**Sprint 2**: Surgical fixes to real bugs. SK-01 (equity denominator) is clean. SK-02 (zombie halt) uses correct time windows. Lowest risk sprint. SheetsLogger equity not refreshed daily is the only gap.

---

## SECTION 6: Top 5 REVERT Candidates

### 1. **S0-4: +1.5% Session Halt Removal** — Risk: 6/10
**What happens if wrong**: Without a profit-taking discipline, the engine can ride a +3% session into reversal, turning a winning day into a -3% loser. The 3% circuit breaker catches the loss but doesn't preserve the profit.
**Rollback path**: Clean — add back `if self._daily_pnl_pct >= 0.015: return []`.
**Recommendation**: Don't revert, but ADD a trailing daily profit protector (e.g., halt if P&L drops 50% from intraday peak).

### 2. **SA-8: Data Freshness Gate `continue` Bug** (S0-7) — Risk: 10/10 **CONFIRMED SHOWSTOPPER**
**What happens**: The `continue` at daily_target.py:483 is at indent=16, inside the outer `if hasattr(snap, 'timestamp')` (indent=12) but OUTSIDE the inner `if _ticker_age > _MAX_DATA_AGE_SECONDS` (indent=16). Since `IndicatorSnapshot.timestamp` is a REQUIRED field, ALL tickers hit `continue` and skip `_score_ticker_with_reason()`. The entire normal scan path is DEAD. Only gap signals (`_scan_gaps()`) can fire.
**Verified on production**: `docker exec nzt48 python3 -c ...` confirms indent=16 on the `continue` line.
**Fix**: Indent `continue` from column 16 to column 20 (inside the inner `if` block). One-character fix.
**Recommendation**: FIX IMMEDIATELY. This is the #1 priority for Sprint 3.

### 3. **T-05: FAST Tier Architecture** — Risk: 5/10
**What happens if wrong**: FAST tier has lower signal quality gates (ADX 15, RVOL 0.60). If FAST signals have lower win rate than SLOW signals, they dilute overall performance. The 3/4 indicator check has ~31% FPR — the price move threshold is the real gate.
**Rollback path**: Change `tier = "FAST" if is_fast_qualified ... else "SLOW"` to `tier = "SLOW"` (force all signals through SLOW path). Non-destructive, additive code can stay.
**Recommendation**: Don't revert until 100-Trade Validation Gate. Track FAST vs SLOW WR separately.

### 4. **T-01: Opening Gap Protocol** — Risk: 4/10
**What happens if wrong**: Gap signals fire into dead-cat bounces during CRASH/SHOCK regimes (CQ-3 finding). Gap signals have fixed confidence 72 regardless of gap magnitude.
**Rollback path**: Set `_GAP_THRESHOLD_3X = 1.0` (impossible to reach) to effectively disable gap scanning without code changes.
**Recommendation**: Add regime check in _scan_gaps() before proceeding.

### 5. **S0-3: Single-Fire → 3 Signals/Day** — Risk: 3/10
**What happens if wrong**: 3 consecutive stopped-out trades in one day = max ~2.25% loss (quarter-Kelly). Circuit breaker catches at 3%. The additional capacity enables recovery trades after a morning loss.
**Rollback path**: Change `_MAX_SIGNALS_PER_DAY = 3` to `1`. One-line change.
**Recommendation**: Keep at 3 but verify that circuit breaker P&L refresh happens between trades.

---

## SECTION 7: Gemini 4-Persona Review — Triage & Verdict

Gemini's Institutional Syndicate review (40 findings across 4 personas) was triaged against the live codebase. Summary:

### Gemini Findings CONFIRMED Valid
| ID | Finding | My Verdict |
|---|---|---|
| CQ-4 | Gap asymmetry 3x vs 5x | ⚠️ CONCERN (0.83% vs 0.80% underlying — marginal) |
| CQ-5 | Anomaly 0.5% threshold too sensitive | ⚠️ CONCERN (but anomaly doesn't generate signals directly) |
| CQ-7 | Kelly overshoot from removing halt | ⚠️ CONCERN (same as my CRO-2) |
| SA-2 | SQLite timezone mismatch | ⚠️ CONCERN (needs INSERT path verification) |
| CRO-7 | Correlated losses (QQQ3.L + 3LUS.L) | ⚠️ CONCERN (valid, future sprint) |
| AR-1 | Brock 1992 misapplication to 1-min charts | ⚠️ CONCERN (extrapolation, not direct evidence) |
| AR-4 | Conf 65 = p-hacking without backtesting | ⚠️ CONCERN (Harvey & Liu 2015 doesn't endorse 65) |

### Gemini Findings REJECTED (Wrong)
| ID | Gemini Claim | Why Wrong |
|---|---|---|
| CQ-1 | FAST multicollinearity = BUG | NOT a bug. MACD/RSI/ROC measure different price properties. The price move threshold (2.5%/4.0%) is the binding gate, not 3/4 agreement. |
| CQ-2 | ROC(30) = REVERT to ROC(5) | ROC(5) was abandoned because 5-bar = pure noise on leveraged ETPs. ROC(30) has early-session concern but is superior after 09:30. |
| SA-1 | Sync Redis = BUG | APScheduler runs scan() in ThreadPoolExecutor, NOT the event loop. Sync Redis blocks the thread (correct), not the async loop. |
| SA-4 | Stale gate deadlock = BUG | This is CORRECT fail-closed behavior. Trading on 15-min-delayed data on 3x/5x ETPs is worse than not trading. |
| SA-6 | Anomaly list mutation = CONCERN | Standard Python — rebinding a local variable doesn't affect the caller's reference. |
| CRO-1 | FAST skips Short Squeeze = BUG | Short Squeeze and PEAD are confidence BOOSTERS (+8 points), NOT safety vetoes. Earnings Fade VETO correctly runs for ALL tiers. |
| CRO-2 | VIX false positive from data drop = BUG | Valid scenario but recovers at daily reset. Not "lethal" — one day of conservative trading. |
| AR-2 | ADX at 09:05 inflated by gap = BUG | ADX pre-filter is 15 (FAST floor) which is already low. Concern about reliability, not incorrectness. |
| AR-3 | Gap fading by MMs = BUG | Spread gate (35 bps) partially addresses this. Directional confirmation is a future enhancement, not a bug fix. |

### Gemini's Top 5 REVERT Candidates — All REJECTED
| # | Gemini Recommendation | My Verdict |
|---|---|---|
| 1 | Sync Redis = REVERT | **REJECT**. APScheduler threads, not event loop. |
| 2 | ROC(30) = REVERT to ROC(5) | **REJECT**. ROC(5) was worse. |
| 3 | Multicollinearity FAST gate = REVISE | **REJECT**. Different properties of same variable ≠ multicollinearity. |
| 4 | SQLite timezone mismatch = FIX | **NEEDS VERIFICATION** of INSERT path only. |
| 5 | FAST bypassing toxicity = REVISE | **REJECT**. Short Squeeze/PEAD are boosts, not vetoes. |

### Gemini Cross-Sprint Interactions — Triage
| # | Gemini Assessment | My Verdict |
|---|---|---|
| 1 | VIX fail-closed + supremacy = "Lethal" | ⚠️ CONCERN — recovers at daily reset. Not lethal. |
| 2 | BST timezone skew in SQL = "Negative" | ⚠️ CONCERN — valid if timestamps stored in local time. |
| 3 | Freshness gate blocks gap scan = "Lethal" | ✅ CORRECT behavior — fail-closed on stale data is safer than blind trading. |
| 5 | Lunch penalty = hard blackout = "Negative" | ⚠️ CONCERN — valid math but lunch block was intentional. |

---

## SECTION 8: Deep-Dive Agent Findings

### Agent 1: Database Schema & Timestamp Analysis
- `trade_outcomes` table uses `time_entered` and `time_exited` TEXT columns
- INSERT paths in `virtual_trader.py` use `datetime.now().isoformat()` — **LOCAL TIME, NOT UTC**
- SQLite `datetime('now')` returns UTC
- **CONFIRMED**: SA-2/AR-7 timezone mismatch is REAL. In BST (summer), `time_exited` stored as BST but queried against UTC `datetime('now', '-12 hours')` creates a 1-hour skew
- **Severity**: Medium — affects consecutive loss counting and zombie halt 12h window

### Agent 2: Gap Scan Code Path Analysis
- `_scan_gaps()` at daily_target.py:1180-1289 is a completely independent code path from `_score_ticker_with_reason()`
- Gap scan does NOT call `_score_ticker_with_reason()` — it directly constructs Signal objects
- Gap scan does NOT check `_SKIP_REGIMES` (confirming CQ-3 bug)
- Gap scan does NOT check RVOL (but checks spread gate)
- Gap scan increments `_daily_signal_count` and respects `_MAX_SIGNALS_PER_DAY`
- Gap scan is the ONLY functioning signal generator (due to SA-8 bug killing normal scan)

### Agent 3: SheetsLogger Equity Analysis
- `SheetsLogger.__init__()` receives `starting_equity` once at engine boot
- No `reset_daily()` or equity update method exists on SheetsLogger
- Circuit breakers get `reset_daily(current_equity=self.equity)` daily — SheetsLogger doesn't
- **CONFIRMED**: SA-5 bug — sheets P&L % drifts from actual over time

### Agent 4: Lunch Window & FAST Block Analysis
- Lunch window: `(now_uk.hour == 11 and now_uk.minute >= 30) or now_uk.hour == 12` = 11:30-12:59 UK
- FAST path lunch block at main.py:3907 returns `[]` during lunch (no FAST signals)
- SLOW path gets -10 confidence penalty during lunch
- Combined with 65 floor: SLOW needs raw 75+ during lunch = effectively blocked
- Gemini was right: lunch penalty + floor = de facto hard blackout for both tiers

### Agent 5: Daily Reset Sequence Analysis
- `reset_daily()` fires from `_check_daily_reset()` when `_last_reset_date != today`
- Reset order: `_daily_pnl_pct = 0`, `_daily_signal_count = 0`, `_consecutive_losses = 0`, `_emergency_tail_risk_veto = False`
- Circuit breakers get `reset_daily(current_equity=self.equity)` — SK-01 fix confirmed working
- VIX supremacy veto cleared at daily reset — correct
- Session opens dict NOT cleared at daily reset — they persist until overwritten at next session open

---

## SUMMARY OF VERDICTS

| Verdict | Count |
|---------|-------|
| ✅ CORRECT | 14 |
| ⚠️ CONCERN | 25 |
| ❌ BUG | 4 |
| ❌ SHOWSTOPPER | 1 |
| 🔧 REVERT | 0 |

**Bugs Found (Priority Order)**:

### P0 — SHOWSTOPPER (Fix Before ANY Trading)
1. **SA-8**: Data freshness `continue` indentation bug — **KILLS ALL NORMAL SCAN SIGNALS**. Only gap signals work. Fix: indent `continue` from col 16 to col 20. One-character fix that unlocks the entire normal scan path.

### P0 — CRITICAL (Fix in Sprint 3)
2. **CQ-3**: Gap scan missing regime check — fires in CRASH/SHOCK where gaps are dead-cat bounces. Fix: add `_SKIP_REGIMES` check at top of `_scan_gaps()`.
3. **SA-2/AR-7**: SQLite timezone mismatch — `virtual_trader.py` stores `datetime.now()` (local/BST) but queries use `datetime('now')` (UTC). 1-hour skew in summer. Fix: use `datetime.now(timezone.utc).isoformat()` in all INSERTs.
4. **SA-5**: SheetsLogger equity never refreshed daily — P&L % drifts. Fix: add equity update in daily reset cycle.

**No REVERTs recommended.** All sprint changes improve the system. SA-8 is the single most impactful finding — fixing it unlocks the entire SLOW scan path that has been dead since Sprint 0.
