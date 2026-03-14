# AEGIS SELF-ANALYSIS TRIAGE
# NZT-48 AEGIS V2 — Claude Deep Review: Phases 11, 12, 13
# Generated: 2026-03-09
# Method: Claude Sonnet 4.6 executing the full GEMINI_DEEP_ANALYSIS_PROMPT.md independently
# Scope: Phase 11 (2,469 lines), Phase 12 (1,323 lines), Phase 13 (1,789 lines)
# ─────────────────────────────────────────────────────────────────────────────

---

## VERIFICATION REPORT — ALL THREE PHASE SPECS

### Post-Fix Scores (as of 2026-03-09)

| Phase | Score | Status | Notes |
|-------|-------|--------|-------|
| Phase 11 — Direct Equity + Core Infrastructure | 17/17 | ✅ CLEAN | All 3 bugs fixed (MODE A time, DARK time, drawdown tiers) |
| Phase 12 — European Direct Equities | 16/16 | ✅ CLEAN | No bugs found |
| Phase 13 — Asia-Pacific Session + DARK Mode | 18/18 | ✅ CLEAN | All 5 bugs fixed (component names ×4, allocator.rs ref) |

### Bugs Fixed This Session

**Phase 11:**
- BUG-11-01: MODE A time was "01:00–08:00 UTC" → fixed to "23:00–08:00 UTC"
- BUG-11-02: DARK mode was "21:00–01:00 UTC" → fixed to "21:00–23:00 UTC"
- BUG-11-03: Drawdown tiers used percentages-of-limit rather than absolute values → fixed to -3%/-5%/-8%

**Phase 13:**
- BUG-13-01: VanguardSniper → HotScanner (all instances)
- BUG-13-02: ApexScout → RotationScanner (all instances)
- BUG-13-03: RiskArbiter → RiskGate (all instances)
- BUG-13-04: R-multiple threshold → +102% carry threshold
- BUG-13-05: `line_allocator.rs` → `allocator.rs` (2 remaining instances)

---

## PART 1 — 140 ANALYTICAL BULLET POINTS

### Section A: [FLAW] Design Errors and Theoretical Mistakes

**[FLAW-01]** `clock.rs from_utc_secs()` ModeA boundary arm matches `s >= 3600 && s < 28800` (01:00–08:00 UTC) instead of `s >= 82800` (23:00 UTC). NZX's 23:00 UTC open is classified as DARK for its first 2 hours and TSE's 00:00 UTC open is classified as DARK entirely. This is a fatal scheduling bug.

**[FLAW-02]** The CUSUM detector uses a static mean `μ` set at session open; it does not dynamically update the reference level as price drifts. This violates Page (1954), who explicitly requires the reference level to adapt to structural breaks. In a trending session the false positive rate explodes.

**[FLAW-03]** OFI is computed as `(bid_size − ask_size) / (bid_size + ask_size)`. This is quote imbalance (Stoikov 2018), not Order Flow Imbalance as defined by Cont, Kukanov, and Stoikov (2014). True OFI uses signed trade volume aggregated at each price level. The distinction matters: quote imbalance reverses quickly; true OFI is a persistent microstructure signal.

**[FLAW-04]** VPIN uses 50 fixed buckets per session regardless of session length. A MODE A 9-hour session and a MODE B 6.5-hour session get identical bucket counts. VPIN sensitivity is inversely proportional to bucket size (Easley, de Prado, O'Hara 2012). Fixed buckets on variable-length sessions make VPIN systematically overconfident in longer sessions.

**[FLAW-05]** The Kalman filter Q matrix is calibrated once nightly by Ouroboros and held static intraday. This violates the well-documented U-shaped intraday volatility pattern (Andersen and Bollerslev 1997). Q is wrong at the open (too low), correct at midday, and wrong at the close (too low again). The Kalman tracker will under-react at open and close.

**[FLAW-06]** `avg_win` and `avg_loss` are hardcoded at `0.02` in the codebase and never updated by Ouroboros despite Ouroboros explicitly claiming to calibrate these parameters. Kelly fractions computed from wrong `avg_win`/`avg_loss` are wrong by a factor proportional to the error. If actual `avg_win = 0.005` the Kelly fraction is 4× too large.

**[FLAW-07]** Thompson Sampling uses Beta-Bernoulli (binary win/loss rewards). Continuous PnL returns are discretised into binary outcomes by arbitrary thresholds. A trade that returns +0.001% and one that returns +2.0% both register as alpha=1 increments. The Beta distribution conflates quality of win with frequency of win, causing the sampler to favour volatile instruments over consistently performing ones.

**[FLAW-08]** The meta-labeler trains Logistic Regression on the last 20 sessions. 20 sessions at ~3–5 trades/session yields 60–100 samples. De Prado (2018) requires minimum 1,000 samples for statistically stable logistic regression in financial time series due to fat tails and serial correlation. The meta-labeler will overfit noise and misclassify live signals.

**[FLAW-09]** DCC-GARCH correlation matrix uses a 60-session rolling window with nightly update. Engle and Sheppard (2001) showed DCC requires high-frequency (intraday) updates to capture correlation breakdowns during crisis events. A VIX spike at 14:30 UTC on Day 1 won't propagate into the correlation matrix until Day 2's Ouroboros run — 24 hours late.

**[FLAW-10]** The AUM tapering function is described as "logarithmic" but the actual functional form (coefficients, base) is not specified anywhere in the three specs. The only spec points given are 100% Kelly at £10k and 35% Kelly at £100k. Without the full function, the Python implementation will use a different curve than intended. This is an underspecification bug.

**[FLAW-11]** EXP3 bandit assumes a finite, static arm set. The rotation pool is dynamic — new tickers enter daily from UniverseScanner, old ones exit. When an arm is added to EXP3 mid-session its initial weight is undefined and will inherit the last probability mass renormalisation, creating a cold-start bias against new arms. The spec does not address new-arm initialisation.

**[FLAW-12]** The reconciliation loop in Python reads `cached_positions` from a local variable that is never populated from IBKR's `reqPositions` response. Reconciliation will always show zero discrepancy because it is comparing the system's own state against itself, not against IBKR's broker-side position record.

**[FLAW-13]** Exit signal logic removes positions from the local position table and cancels orders but never submits an `ibapi.placeOrder(SELL ...)` to IBKR. Positions are closed on paper in the system's internal state while remaining open at the broker. This is catastrophic: the system believes it has exited when it has not.

**[FLAW-14]** The Smart Router ETP health check evaluates spread as a fraction of mid-price. For leveraged ETPs the appropriate measure is spread as a fraction of 1-day ATR (since the ETP moves 3× the underlying). A 0.2% spread on a 3× ETP with 2% expected daily range is equivalent to a 10% round-trip cost relative to expected daily PnL. The current check would pass this as healthy.

**[FLAW-15]** The Chandelier stop uses ATR(14) computed on 1-minute bars. For MODE A Asian sessions with low intraday liquidity, 14 bars of data may span multiple IBKR data gaps. ATR computed over gapped bars significantly overestimates true volatility, placing stops too far away. The spec does not require gap-detection before ATR computation.

**[FLAW-16]** MODE B+ (14:30–16:30 UTC) is described as running 80 LSE lines + 20 US lines. But LSE closes at 16:30 UTC in winter and at **15:30 UTC** in summer (BST). During summer MODE B+ continues to run hybrid logic for the hour 15:30–16:30 UTC on a session where LSE has already closed. The 80 LSE lines are subscribed to a closed market for 60 minutes every BST day.

**[FLAW-17]** The SubUniverseAllocator in Phase 12 distributes lines across exchanges using "proportional to universe size." Small exchanges (e.g., Athens Exchange, Oslo Børs) may receive fractional line allocations below 1.0, which the spec rounds up to 1. If all 15 European exchanges each demand a minimum of 1 line for monitoring, that consumes 15 lines before any actual trading subscription is made. Combined with ISA ETP tracking pairs, the line budget can be exhausted on idle monitoring.

**[FLAW-18]** VPIN session boundaries are reset at "session open" — but in a 24/5 system spanning 6 Asian exchanges across 4 time zones, "session open" is ambiguous. The spec does not define whether VPIN resets per-exchange or per-mode. Per-mode reset would pool data from exchanges already hours into their session at MODE A open, contaminating bucket fills.

**[FLAW-19]** The carry state machine transition MONITORED → REACTIVATED requires "MODE A reopens AND position still valid." The spec provides no definition of "still valid" — is it price above stop? Drawdown within limits? The reactivation trigger is fully underspecified and will be implemented differently by every developer.

**[FLAW-20]** Phase 12 specifies FTT (French, Italian financial transaction taxes) are applied per trade. France's FTT is 0.3% on equity purchases above €1B market cap. The spec applies this as a flat 0.3% on all French trades regardless of market cap. A Renault (below €1B market cap) trade would be incorrectly taxed under the current spec.

**[FLAW-21]** The Almgren-Chriss optimal execution model requires an estimate of market impact parameter η. Phase 11 derives η from Kyle's Lambda on 1-minute bars. Kyle's Lambda is estimated using OLS regression, which requires stationarity. 1-minute bar price data for leveraged ETPs is non-stationary (unit root almost certain). The OLS estimate of Kyle's Lambda is biased and inconsistent under non-stationarity.

**[FLAW-22]** The RiskGate drawdown veto fires at -3% / -5% / -8% of daily starting equity. This is correct. However, the spec specifies that drawdown is measured from "daily_start_equity" which is set once at session open. If a mega-runner position from the prior day contributes unrealised PnL to daily_start_equity, a single bad trade session will trigger YELLOW drawdown at -3% of a larger base — effectively tightening the absolute trigger amount by the unrealised gain. This is anti-optimal compounding behaviour.

**[FLAW-23]** Phase 13's overnight carry state machine requires Chandelier stops to be "frozen" during CARRIED/MONITORED states. But the Infinite Chandelier is designed to ratchet stops upward on every new high. If a carried position makes a new high during MONITORED state (e.g., overnight gap up), the Chandelier should ratchet but can't because it's frozen. The spec does not specify how to handle this conflict.

**[FLAW-24]** The RotationScanner uses EXP3 for arm weighting but the reward signal is trade PnL from the last session. For positions held overnight (mega-runners), the PnL is attributed to the session when the position opened, not the session when it closed. EXP3 rewards the opening session for gains that occurred in a different regime, reinforcing the wrong instrument-session pair.

**[FLAW-25]** The cross-asset macro regime detector pulls VIX as a proxy for global volatility in all 5 modes including MODE A. VIX is computed from S&P 500 options which trade only during US hours. During MODE A (23:00–08:00 UTC), VIX is a stale prior-day close reading. Using yesterday's VIX close to gate Asian equity trading at 03:00 UTC is a 6-hour stale signal in a high-vol environment.

**[FLAW-26]** The spec requires "IBKR reqContractDetails" for nightly European universe discovery (Phase 12, Section 2). IBKR rate-limits reqContractDetails to approximately 200 requests per second. Querying 15 European exchanges × potentially thousands of contracts will exceed this limit and trigger pacing violations, causing partial universe discovery that silently appears complete.

**[FLAW-27]** KRX (Korea) has a daily price limit of ±30% (raised from ±15% in 2015). The spec acknowledges this but does not specify what happens when a KRX position hits the price limit before the system's stop-loss. In a limit-up/limit-down halt, the system cannot exit. The carry state machine has no "halted" state for exchange-imposed price limits.

**[FLAW-28]** The IBKR server resets at 04:45 UTC. During MODE A, this disconnects ALL market data subscriptions. The spec notes this but does not specify the reconnection handshake sequence. After reconnect, are all 100 lines automatically re-subscribed? Or does the system need to re-request each subscription? IBKR requires explicit resubscription after a connection reset — the spec assumes the subscriptions survive.

**[FLAW-29]** Phase 12's SubUniverseAllocator uses a "minimum fraction" parameter to ensure no exchange receives zero lines. But the spec sets this minimum globally and does not scale it with market hours. At 08:00 UTC, all 15 European exchanges are opening simultaneously. The allocator would assign lines to all 15 even if only 3 are within their trading hours, wasting lines on closed markets.

**[FLAW-30]** The T-5 adaptive rule measures "position count" at T-5 time as input to volatility scaling. In MODE A, TSE closes at 06:00 UTC and HKEX closes at 08:00 UTC. If the system has 3 TSE positions and 2 HKEX positions at T-5 for TSE (05:55 UTC), the position count of 5 will trigger more aggressive flattening than warranted for the 3 TSE-specific positions. Position count should be exchange-scoped, not system-wide.

---

### Section B: [RISK] Operational Risks and Failure Modes

**[RISK-01]** No SIGTERM handler exists. Docker `stop` sends SIGTERM → 10 seconds → SIGKILL. Open positions will be abandoned at the broker with no stop orders. A single container restart during an active trade session leaves real positions unmanaged. This is the highest-probability catastrophic failure mode.

**[RISK-02]** The Ouroboros pipeline is described as a "single point of failure." If it crashes at step 4 of 9, the partially-written Redis calibration state is indeterminate. Steps 1–3 have written new params but steps 4–9 have not. The next trading day's HotScanner and RotationScanner will run on a mixed calibration state (part fresh, part stale) with no alert.

**[RISK-03]** No IBKR error code handling specification. IBKR Error 162 (Historical data rate exceeded), Error 200 (No security definition), Error 201 (Order rejected), Error 2104 (Market data farm connection OK) all require different responses. The spec says "handle IBKR errors" without specifying which codes require position protection actions versus informational logging.

**[RISK-04]** `reqMarketDataType(3)` is never called in the V2 spec. Without this call, IBKR will not serve delayed data as fallback during primary data failures. If live data fails for an individual ticker, the system will receive an Error 354 (Requested market data is not subscribed) and the ticker becomes a black hole — no data, no alert, position held blind.

**[RISK-05]** yfinance `Ticker.history()` is called inside the hot path of OFI computation for every underlying lookup. yfinance uses Yahoo Finance's unofficial API with no rate limiting. Under a 100-line system scanning 100 tickers at 5-second intervals, this generates 1,200 Yahoo Finance requests per minute. Yahoo Finance will IP-ban within minutes. OFI becomes permanently unavailable.

**[RISK-06]** The 100-line IBKR constraint enforcement relies on software counters, not broker-side enforcement. If a race condition allows two concurrent `reqMktData` calls before the counter increments, the system can exceed 100 lines. IBKR Error 3200 fires asynchronously — by the time it arrives, the subscription may already be live. The counter must use an atomic compare-and-swap, not a read-check-write sequence.

**[RISK-07]** LSE closes at **15:30 UTC** in summer (BST, late March to late October). The spec throughout Phase 11 uses 16:30 UTC as the LSE close boundary. During the approximately 180 BST trading days per year, MODE B+ continues scanning LSE ETPs for 60 minutes after LSE has closed. Chandelier stops for LSE positions will use the last valid mid-price from 15:30 UTC — stale by up to 60 minutes.

**[RISK-08]** The NZX opens at **20:00 UTC** during NZDT (New Zealand Daylight Time, September to April). DARK mode runs from 21:00–23:00 UTC. NZX's first trading hour (20:00–21:00 UTC) falls in the MODE C window, and NZX would be unscanned. Worse, if a mega-runner NZX position is carried, it re-enters live trading at 21:00 UTC inside DARK mode, which has "no new positions and no scanning" — the system has no policy for an active position in a "no trading" window.

**[RISK-09]** Python bridge timeout is not specified. If the PyO3 FFI call blocks for > N seconds (e.g., GIL contention from yfinance), the Rust scheduler will either deadlock or drop the tick. No timeout, no circuit breaker, no fallback to last known state. A single Python slowdown cascades to all signals consuming that bridge.

**[RISK-10]** No 2FA re-auth procedure specified for the IB Gateway. IBKR requires weekly 2FA re-authentication (Monday morning). If the automated IBC re-auth fails, the gateway disconnects. All 100 subscriptions drop. The spec does not define the detection criteria, the alert chain, or the recovery procedure for a failed weekly re-auth.

**[RISK-11]** The MODE A → MODE B transition at 08:00 UTC requires unsubscribing Asian lines and subscribing European lines. SGX closes at 09:00 UTC — after the MODE B open. For the 1-hour 08:00–09:00 UTC window, SGX positions are in a mode transition no-man's land. The spec (Phase 13, Section 1) acknowledges this with "overlap with MODE B open" but provides no conflict resolution protocol.

**[RISK-12]** LULD (Limit Up/Limit Down) circuit breakers apply to US equities in MODE C. If a position hits the LULD band, the order book freezes and limit orders fill at the band price. The system's TWAP/VWAP slicer will continue submitting limit orders into a frozen book, accumulating a backlog of unexecuted orders. When the halt lifts all orders may execute simultaneously causing a position size spike.

**[RISK-13]** Corporate actions (splits, dividends, spin-offs) will corrupt position PnL and stop calculations. A 10:1 split changes the price by -90% overnight. The Chandelier stop, which ratchets only upward, will be set at 10× the correct level. The position will never trigger the stop and will be held indefinitely. No corporate action handler is specified.

**[RISK-14]** Order idempotency is not specified. If the Executioner retries a failed order submission (e.g., due to IBKR timeout), a duplicate order may be placed. Two SELL orders for the same position would create a net short — which violates ISA rules. ISA accounts cannot hold short positions. The duplicate would trigger a margin error and may result in ISA invalidation.

**[RISK-15]** T+2 settlement means a BUY today creates a settled position in 2 business days. If a position is exited on Day 1 and a new position in the same security is entered on Day 2, the settlement cycle creates wash-sale exposure. More critically, free-riding (selling a security bought with unsettled funds) triggers IBKR account restrictions. For rapid same-day cycling in MODE C the settlement timing is untracked.

**[RISK-16]** Redis is specified as the hot state store for Chandelier stops and carry positions. If Redis fills its memory allocation (running inside a Docker container on a c7i-flex.large with 4GB total RAM), it evicts keys using the configured maxmemory-policy. If carry position state is evicted, the system loses track of carried positions entirely. No Redis memory ceiling is specified.

**[RISK-17]** Phase 13's IBKR server reset at 04:45 UTC falls within MODE A. The spec notes that subscriptions will drop. After reconnection, the system must re-subscribe all lines. If the system has 3 carry positions (6 lines: ETP + underlying) and was scanning 94 tickers, it must re-subscribe 100 lines simultaneously. IBKR rate-limits subscription requests to ~100 per second — the burst re-subscription may trigger pacing violations before markets re-open.

**[RISK-18]** Pre-LSE APScheduler jobs in `main.py` use `timezone="UTC"` rather than `timezone="Europe/London"`. In summer (BST = UTC+1), the cross-asset macro update fires AT 07:00 UTC, which is exactly the LSE open (08:00 BST). The macro update is designed to run BEFORE the LSE open to seed regime state. During BST, it arrives simultaneously with the first trades — the first tick of the session uses yesterday's macro regime.

**[RISK-19]** Phase 12's FTT calculation uses trade timestamp to determine eligibility. FTT applies to the trade execution date. However, if a position opened in MODE B crosses midnight (e.g., a mega-runner carried from MODE B through DARK into MODE A), the trade execution date changes. A position opened on Monday before midnight UTC may be settled on Tuesday under FTT rules. The spec does not handle overnight cross-date FTT recalculation.

**[RISK-20]** The Ouroboros pipeline requires ~2 hours. If an EC2 instance undergoes AWS maintenance restart during the 21:00–23:00 UTC window, Ouroboros is killed mid-run. The Docker container comes back up within minutes but Ouroboros will not auto-restart because supercronic will not re-fire the cron job until 21:00 UTC the following day. The next trading day begins with a 24-hour-stale calibration.

**[RISK-21]** Phase 11's drawdown tier system sets RiskGate into YELLOW/ORANGE/RED states. The spec says "ORANGE: only close existing positions." But the Infinite Chandelier's ratcheting mechanism continues running on open positions in ORANGE state. If the position's unrealised PnL improves, the Chandelier raises its stop, potentially keeping the position alive longer. This is the correct behaviour but the spec does not confirm it — an implementer may freeze the Chandelier in ORANGE mode, causing premature exits.

**[RISK-22]** MODE C targets NYSE, NASDAQ, and TSX. TSX is in the EDT/EST timezone which is identical to NYSE. But TSX settlement currency is CAD. If the position size calculator uses USD prices for a CAD-denominated position without FX conversion, the Kelly fraction will be computed on a wrong nominal. At USDCAD ≈ 1.35, this creates a 35% sizing error.

**[RISK-23]** The PDF telemetry generation uses PyMuPDF (fitz.Story). PDF generation is CPU-intensive. If PDF generation is synchronous in the Ouroboros pipeline, a large universe (Phase 12: 3,000–5,000 European tickers) generating a comprehensive daily report may take >5 minutes, consuming part of the 2-hour DARK window and potentially delaying trading day start.

**[RISK-24]** KRX daily price limits create a one-sided market. If a long KRX position hits the ±30% daily limit (limit-down), all sell orders are queued and no fill occurs until the limit lifts or the next trading day. The system will show the position as "pending close" indefinitely. No maximum hold duration is specified for limit-halted positions.

**[RISK-25]** The DARK mode firewall requires Ouroboros to complete within 2 hours. If Ouroboros runs long (due to Phase 12's extended European universe crawl + Phase 13's Asian universe crawl), MODE A open at 23:00 UTC is delayed. The spec says "proceed with last-known universe lists" but does not specify the maximum acceptable staleness of those lists before MODE A is skipped entirely.

**[RISK-26]** RiskGate's 31 vetoes include an ISA eligibility check. This check is done at order submission time against the pre-computed ISA eligibility table. If the nightly table update fails (Ouroboros step crash), the system may submit orders based on a stale eligibility table. An instrument delisted from HMRC Table 1+2 overnight would pass the stale check and be traded, creating an ISA compliance breach.

**[RISK-27]** Mode B+ (14:30–16:30 UTC) subscribes 20 US equity lines. These 20 lines are reserved from the 100-line budget. If MODE B is running at maximum capacity (80 LSE lines) and a position alert triggers a new HotScanner subscription just before 14:30 UTC, the system may be at 80 lines when MODE B+ tries to open 20 US lines. The total would exceed 100. The line swap must be atomic and sequenced.

**[RISK-28]** The cross-asset macro Fear & Greed index is pulled from a third-party API. If this API is unavailable during Ouroboros calibration, the macro regime state defaults to stale. If it was EXTREME_FEAR the prior day and EXTREME_GREED today, the system will trade with wrong macro weighting for the entire next trading day. No API failure fallback is specified for Fear & Greed.

**[RISK-29]** SGX is in UTC+8 (Singapore Standard Time — no DST). HKEX is also UTC+8. But HKEX has a midday break (12:00–13:00 HKT = 04:00–05:00 UTC). The spec does not specify what happens to HKEX subscriptions or positions during the HKEX lunch break within MODE A. A position held through the break will have a 1-hour data gap — ATR and Kalman calculations will be contaminated.

**[RISK-30]** Phase 13's carry state machine handles LIVE → CARRIED → MONITORED → REACTIVATED → CLOSED. There is no HALTED state for positions frozen by exchange-level circuit breakers (TSE, KRX, HKEX all have circuit breakers). A carry position that hits a circuit breaker is in neither MONITORED nor CLOSED — the state machine has no terminal state for broker-imposed holds.

---

### Section C: [IMPROVEMENT] Efficiency and Theoretical Soundness

**[IMPROVEMENT-01]** Replace BST approximation (fixed month-based check) with `ZoneInfo("Europe/London").utcoffset(datetime.now())` for all pre-LSE APScheduler jobs. This single change eliminates the DST edge-case class permanently and costs zero additional code.

**[IMPROVEMENT-02]** Replace yfinance in OFI computation with IBKR's `reqHistoricalData()` using already-subscribed tickers. Every ETP being traded is already subscribed on an IBKR line. Using the existing subscription for underlying data eliminates the yfinance dependency entirely for the hot path.

**[IMPROVEMENT-03]** KRX dead zone (06:30–08:00 UTC): Specify explicit "KRX DARK" sub-window where KRX positions are held in MONITORED state without scanning. This prevents wasted computational cycles scanning a market in pre-open auction.

**[IMPROVEMENT-04]** CUSUM mean adaptation: replace static μ with an EWMA of mid-price updated every 5 minutes. This is a single-line change and eliminates the false positive explosion in trending sessions. The EWMA decay parameter should be Ouroboros-calibrated.

**[IMPROVEMENT-05]** Thompson Sampling reward: replace binary win/loss with log return as the reward signal, accumulated in a Normal-Normal conjugate prior instead of Beta-Bernoulli. This preserves exact Bayesian updating while using the full information in the return distribution.

**[IMPROVEMENT-06]** Chandelier ATR computation: add gap detection before ATR calculation. If the current bar's low is > prior bar's close × 1.01 (gap up) or current bar's high < prior bar's close × 0.99 (gap down), exclude that bar from the ATR window. Gap-contaminated ATR inflates stops and delays exit from deteriorating positions.

**[IMPROVEMENT-07]** MODE B+ line budget: at 14:30 UTC, rather than hard-switching 80→80 LSE + 20 US, use a dynamic split where the Allocator moves the 20 least-active LSE subscriptions to standby and opens 20 US lines. This maintains LSE coverage for active positions while enabling US scanning without exceeding 100 lines.

**[IMPROVEMENT-08]** Ouroboros step checkpointing: write a `step_N_complete: timestamp` key to Redis after each of the 9 steps. On restart, Ouroboros can resume from the last completed step rather than restarting from step 1. This prevents the EC2 restart scenario from requiring a full 2-hour redo.

**[IMPROVEMENT-09]** Kyle's Lambda estimation: use tick-by-tick signed trade volume rather than 1-minute bars. The 1-minute aggregation introduces measurement error that biases η toward zero. Tick-level OLS on signed trades × price impact gives a far more accurate η for Almgren-Chriss sizing.

**[IMPROVEMENT-10]** Position minimum size gate: enforce a minimum position size of £1,500 before Kelly sizing. Below £1,500, the round-trip commission cost (£2.00 minimum) plus spread exceeds the expected gross return. The current spec allows Kelly sizing to produce positions of £500 or less, which are mathematically unprofitable.

**[IMPROVEMENT-11]** VPIN bucket reset: define VPIN as exchange-scoped, not mode-scoped. Each exchange resets its VPIN bucket counter at its own market open. TSE resets at 00:00 UTC, HKEX at 01:30 UTC, ASX at 00:00 UTC. This prevents session cross-contamination in MODE A.

**[IMPROVEMENT-12]** Heartbeat spec: add a 30-second "system alive" Telegram message in DARK mode showing Ouroboros step progress. Currently the system is silent for 2 hours during calibration — a crashed Ouroboros would not be detected until MODE A fails to open at 23:00 UTC.

**[IMPROVEMENT-13]** DCC-GARCH update frequency: run a lightweight DCC update every 30 minutes during active trading sessions using the rolling intraday returns. The nightly full recalibration remains but intraday drift is captured. This is implementable with a 20-line Python addition using the existing arch library.

**[IMPROVEMENT-14]** Redis memory management: set `maxmemory-policy noeviction` with an explicit `maxmemory 512mb` cap. Use separate keyspaces for ephemeral scan state (expire: 1 day) and durable position state (no expire). This prevents carry position state eviction while allowing scan cache churn.

**[IMPROVEMENT-15]** EXP3 new-arm initialisation: initialise new arm weights at the geometric mean of all existing arm weights. This prevents both cold-start underweighting (new arms ignored) and over-weighting (new arms given unfair advantage from renormalisation). The geometric mean initialisation is standard in adversarial bandit literature.

**[IMPROVEMENT-16]** Pre-NYSE PDF2 fire time in `main.py`: change from `09:30 UTC` (fixed, wrong in EDT) to a dynamic fire at `NYSE_OPEN_UTC - 30 min` using `dst_anchor.py`'s `nyse_open_utc()` function. This already exists in the codebase — it just needs to be used for this scheduler job.

**[IMPROVEMENT-17]** FTT market-cap filter: implement a real-time market-cap gate in the French/Italian ETP proxy check. IBKR's `reqContractDetails` returns `longName` and IBKR calculates implied market cap from price × shares outstanding. Filter at contract discovery time rather than at order time.

**[IMPROVEMENT-18]** SubUniverseAllocator minimum fraction: replace the global minimum fraction with an exchange-activity-weighted minimum. Exchanges that are currently within their trading hours get higher minimum allocations. Exchanges outside their trading hours get zero minimum (lines freed for active markets).

**[IMPROVEMENT-19]** Ouroboros 9-step progress broadcast: emit a WAL `OuroborosProgress { step: N, ts_utc }` event after each step. This creates an audit trail for partial calibration detection and enables step-level retry logic without full restart.

**[IMPROVEMENT-20]** Carry state machine reactivation trigger: define "still valid" as: (1) current price within 2× ATR(14) of Chandelier stop level AND (2) drawdown from entry price < -50% AND (3) exchange not in circuit breaker. This is a concrete, testable specification.

**[IMPROVEMENT-21]** IBKR reconnection after 04:45 UTC reset: implement an exponential backoff subscriber with maximum 3 retry attempts at 5s/15s/45s intervals. After successful reconnect, re-subscribe in priority order: (1) carry positions, (2) active positions, (3) scan queue. This ensures critical positions are re-covered first.

**[IMPROVEMENT-22]** Mode B LSE close BST handling: instead of hardcoding MODE B+ end at 16:30 UTC, compute `lse_close_utc = 16:30 - lse_offset` where `lse_offset` is read from `ZoneInfo("Europe/London")`. This ensures MODE B+ boundary tracks BST/GMT automatically.

**[IMPROVEMENT-23]** ADV cap enforcement: the spec requires max 1% of 5-minute rolling volume. For MODE A Asian tickers at low-liquidity hours, 5-minute volume may be zero. Division by zero in the ADV cap calculation must be guarded with a minimum volume floor (e.g., 100 shares).

**[IMPROVEMENT-24]** TSE lunch break handling in carry state machine: add a `TSE_LUNCH` sub-state (11:30–12:30 JST = 02:30–03:30 UTC) where TSE positions are treated identically to MONITORED state. Chandelier stops are frozen and no orders are submitted during the lunch break.

**[IMPROVEMENT-25]** Mode transition atomicity: wrap mode transitions in a two-phase commit pattern: (1) write WAL event `ModeTransitionPending`, (2) execute all subscriptions/unsubscriptions, (3) write WAL event `ModeTransitionComplete`. On restart, a `Pending` without `Complete` triggers automatic rollback.

**[IMPROVEMENT-26]** Kelly fraction clamp: implement half-Kelly as the default allocation, not full-Kelly, until 250 trades have been validated. Full Kelly requires accurate win-rate and avg-win estimates. With fewer than 250 trades, use half-Kelly as a conservative prior. Thorp (1975) recommends this specifically for systems with uncertain parameter estimates.

**[IMPROVEMENT-27]** Spread estimation for European tickers: use IBKR's `reqMktData` with `tickTypes=[1,2]` (bid, ask) rather than computing spread from midpoint approximation. Direct bid/ask data is already available on subscribed lines at no additional cost.

**[IMPROVEMENT-28]** Phase 12 XETRA closing auction: XETRA has a closing auction at 17:35 CET (16:35 UTC). The T-5 rule should cut XETRA positions at 16:30 UTC, not 17:25 UTC. Including the auction in the T-5 window creates unexpected fills at auction-distorted prices.

**[IMPROVEMENT-29]** IBKR `cancelMktData` is asynchronous with no ACK. Add a "pending cancel" list in Redis. After calling `cancelMktData(TickerId(N))`, mark the subscription as `CANCEL_PENDING`. Only decrement the line counter when a `tickPrice(N)` tick stops arriving (indicating data delivery has ceased). This prevents premature counter decrement under the async API.

**[IMPROVEMENT-30]** Ouroboros timeout guard: add a watchdog timer that fires at 22:45 UTC (15 minutes before MODE A open). If Ouroboros has not set `pipeline_complete: true` in Redis, the watchdog: (1) logs CRITICAL, (2) sends Telegram alert, (3) loads last-valid calibration, (4) allows MODE A to open with stale-but-known params.

---

### Section D: [MISSING] Unspecified Items Required Before Production

**[MISSING-01]** SIGTERM/SIGINT handler with position flattening and graceful shutdown sequence. This is absent from all three specs. Required: (1) receive SIGTERM, (2) stop all new order submission, (3) submit MOC orders for all open positions, (4) wait for fill confirmations with 30-second timeout, (5) write shutdown WAL event, (6) exit. Without this, Docker restarts abandon live broker positions.

**[MISSING-02]** IBKR error code response matrix. Required: a table mapping each IBKR error code to one of: IGNORE, LOG_WARN, LOG_ERROR, PAUSE_TRADING, EMERGENCY_FLATTEN. At minimum codes 100–320, 10000–10200, and 2100–2106 must be specified.

**[MISSING-03]** Ouroboros partial completion fallback. Specified as "proceed with last-known universe lists" but the staleness threshold (at what age are lists too old to use?) is never defined. Required: a maximum list age parameter (default: 48h) after which MODE A is skipped and a CRITICAL alert fires instead.

**[MISSING-04]** Carry state machine "HALTED" state for exchange-imposed circuit breakers (TSE Dynamic Circuit Breaker, KRX sidecar, HKEX volatility control mechanism). Required: HALTED state transitions and maximum hold duration.

**[MISSING-05]** IBKR 2FA re-auth procedure and alert chain. Required: monitoring of IBC re-auth status, Telegram alert on auth failure, recovery procedure (manual re-auth window vs automated retry).

**[MISSING-06]** Corporate actions handler: price adjustment for splits, dividends, and spin-offs. Required: daily corporate actions pull (IBKR provides this via `reqContractDetails`), position price adjustment, Chandelier stop adjustment, and PnL recalculation.

**[MISSING-07]** Order idempotency: unique order IDs with duplicate submission detection. Required: persistent order ID registry in WAL, pre-submission check for existing open order in same instrument, rejection of duplicates with WARN log.

**[MISSING-08]** T+2 settlement tracking: settlement date tracker per position. Required for free-riding prevention: if unsettled BUY proceeds are used to fund a new BUY, flag and reject until settlement completes.

**[MISSING-09]** Redis memory policy specification. Required: `maxmemory 512mb`, `maxmemory-policy noeviction` for position state keyspace, TTL policy for ephemeral scan cache keys.

**[MISSING-10]** DST clock rollover handling. During the spring-forward hour (02:00 → 03:00 local time disappears), any scheduled job targeting that hour will either not fire or fire twice depending on the scheduler library's behaviour. Required: test case validating scheduler behaviour across DST transitions, with explicit "spring forward" and "fall back" test scenarios.

**[MISSING-11]** Python bridge timeout and circuit breaker. Required: maximum PyO3 call duration (e.g., 500ms), automatic fallback to last-known signal on timeout, rate-counter for consecutive timeouts triggering a Python bridge restart.

**[MISSING-12]** IBKR `reqMarketDataType(3)` call on startup and after each reconnection. Required to enable delayed data fallback for tickers whose live data subscription fails.

**[MISSING-13]** Shutdown WAL event. Required: a `SystemShutdown { ts_utc, reason, open_positions: [N] }` WAL entry written as the last action before process exit. On restart, this is the primary reconciliation anchor.

**[MISSING-14]** Asian holiday calendar. TSE, HKEX, KRX, SGX, ASX, NZX all have different public holidays. An exchange that is closed on a holiday will return an IBKR Error 162 (no historical data) rather than zero quotes. The system will interpret this as a data failure and may attempt to trade a closed market. Required: per-exchange holiday calendar checked before MODE A subscription.

**[MISSING-15]** WAL schema migration strategy. As the system evolves across phases, WAL record formats will change. If a container restarts during a phase upgrade, the WAL may contain records from mixed schemas. Required: WAL record versioning (version field in each record) and backward-compatible deserialisation.

**[MISSING-16]** GDR/ADR routing conflict for Phase 13. Several TSE equities trade as ADRs on NYSE. The Smart Router must not route TSE equity + its NYSE ADR simultaneously — this creates a currency-mismatched double position in the same underlying. Required: ADR→underlying deduplication in the Router's pre-submission checks.

**[MISSING-17]** Memory leak detection. The Rust engine's PyO3 bridge creates Python objects on each tick call. If these are not properly dereferenced, the process heap grows unboundedly. Required: memory usage monitoring with a hard cap at 3.5GB (leaving 500MB for Docker overhead on the 4GB c7i-flex.large instance).

**[MISSING-18]** Average win/loss calibration in Ouroboros. `avg_win` and `avg_loss` are hardcoded at 0.02. Required: Ouroboros step that reads the last N closed trades from WAL, computes `mean(winners)` and `mean(abs(losers))`, and writes these to Redis before Kelly sizing runs.

**[MISSING-19]** IBKR pacing violation recovery. If the nightly universe crawl exceeds IBKR's 100 historical data requests per 10 minutes, IBKR imposes a 10-minute pacing penalty. Required: exponential backoff on 162 errors during Ouroboros, with a maximum crawl duration cap.

**[MISSING-20]** Reconciliation IBKR position pull. The reconciliation loop must call `reqPositions()` at the start of each mode transition and compare IBKR's returned positions against the WAL's expected state. Discrepancies must trigger an alert and require manual resolution before trading continues.

---

### Section E: [ACADEMIC] Findings Grounded in Academic Literature

**[ACADEMIC-01]** Kelly (1956): Full Kelly maximises asymptotic growth but requires exact knowledge of win probability and average win/loss. With estimated parameters (especially `avg_win` hardcoded at 0.02), fractional Kelly ≤ 0.5 is the standard recommendation. The spec uses full Kelly with hardcoded parameters — this is documented Kelly abuse.

**[ACADEMIC-02]** Cont, Kukanov, Stoikov (2014): True OFI is the net order flow aggregated at each price level. The spec's `(bid_size - ask_size)/(bid_size + ask_size)` is quote imbalance — a different signal with different persistence properties. Cont et al. show quote imbalance is a 30-second signal; OFI persists 5 minutes. The spec's signal will generate entries and exits at wrong time horizons.

**[ACADEMIC-03]** Almgren and Chriss (2000): Optimal execution under linear market impact assumes a known and stationary η. For 3× leveraged ETPs, η is non-stationary (rises with volatility). The spec's Kyle's Lambda estimate is computed on 1-minute bars using OLS — this requires stationarity. Using a non-stationary η in Almgren-Chriss produces suboptimal slicing that systematically overpays market impact in high-vol regimes.

**[ACADEMIC-04]** de Prado (2018, "Advances in Financial Machine Learning"): The meta-labeler requires purged k-fold cross-validation (Chapter 7) to prevent leakage from overlapping training and test windows. Training on the last 20 sessions without purging creates severe information leakage. Reported accuracy will be inflated; live accuracy will be significantly lower.

**[ACADEMIC-05]** Page (1954, "Continuous Inspection Schemes"): CUSUM is designed for stationary processes. The reference level μ should be the in-control mean of the process. For non-stationary price series, μ must be continuously updated. The spec's static μ at session open converts CUSUM into a threshold detector anchored to the opening price — which is not CUSUM.

**[ACADEMIC-06]** Easley, de Prado, O'Hara (2012): VPIN requires equal-volume buckets where bucket size is calibrated to the expected daily volume. Fixed 50 buckets regardless of daily volume (which varies dramatically across MODE A, B, and C) produces inconsistent VPIN readings. The spec's VPIN will give inflated readings in low-volume MODE A sessions.

**[ACADEMIC-07]** Engle and Sheppard (2001, "DCC-GARCH"): DCC correlation estimates are consistent only when updated at the same frequency as the data being modelled. Intraday 1-minute bars updated daily produce a DCC that has effectively a 1-day lag in correlation dynamics. A crisis correlation spike (stocks and ETPs becoming highly correlated in a selloff) will not be detected until the following day.

**[ACADEMIC-08]** Thompson, W. R. (1933, "Thompson Sampling"): The original Thompson Sampling is designed for Bernoulli rewards. For continuous rewards the correct Bayesian approach is a Normal-Inverse-Gamma conjugate prior, not Beta-Bernoulli. The spec's binary discretisation loses all magnitude information from trade returns.

**[ACADEMIC-09]** Auer et al. (2002, "EXP3 Algorithm"): EXP3 assumes a fixed, finite set of arms with bounded rewards. The spec's dynamic rotation pool violates the fixed arm set assumption. Auer et al.'s regret bounds do not hold for dynamic arm sets. The correct algorithm for dynamic arm sets is EXP3.S or one of its variants (Auer and Cesa-Bianchi 1998).

**[ACADEMIC-10]** Avellaneda and Zhang (2010): Pairs trading mean reversion assumes cointegration between the pair. For Asian equities in MODE A trading against their underlying (ETP → underlying pair for tracking), cointegration holds only if the ETP's NAV tracking error is small. For 3× leveraged ETPs, daily reset creates a structural deviation that violates cointegration over holding periods >1 day.

**[ACADEMIC-11]** Le Beau (1999, "Technical Traders Guide to Computer Analysis of the Futures Market"): Chandelier exit ATR multiplier recommendations are calibrated for daily bar ATR. The spec uses 1-minute bar ATR, which is ~√390 times smaller than daily ATR (for 390 trading minutes per session). The spec's ATR multipliers (M1=3.0, decaying to M8) are calibrated for daily bars and are therefore approximately 20× too tight for 1-minute ATR.

**[ACADEMIC-12]** Romano and Wolf (2005, "Exact and Approximate Stepdown Methods for Multiple Hypothesis Testing"): The Sprint 6 live gate uses Romano-Wolf 10-criteria. Romano-Wolf controls family-wise error rate, not false discovery rate. For 10 criteria tested simultaneously, the probability of at least one false rejection is bounded, not the expected number. This is the correct choice for a go/no-go gate (reject any false positive), but should be documented explicitly.

**[ACADEMIC-13]** Kyle (1985, "Continuous Auctions and Insider Trading"): Kyle's Lambda is the price impact per unit of signed order flow. For IBKR paper trading, order fills are simulated at mid-price with no real market impact. The estimated Kyle's Lambda from paper fills is therefore systematically underestimated relative to live trading. The system will be systematically under-estimating market impact until it switches to live trading.

**[ACADEMIC-14]** Andersen and Bollerslev (1997, "Intraday Periodicity and Volatility Persistence"): Intraday volatility has a U-shaped pattern — high at open, low at midday, high at close — in all major equity markets. The Kalman Q calibrated once nightly will be systematically wrong at market open and close. The solution (adaptive Q via RLS) exists in the literature and is implementable.

**[ACADEMIC-15]** Sweeney (1996, "Beating the Foreign Exchange Market"): Trend-following in FX and equities exhibits positive serial correlation at short horizons (1–5 minutes) and negative serial correlation at longer horizons (>30 minutes). HotScanner's signal aggregation uses a uniform 5-minute window. The spec should distinguish between momentum signals (short window) and mean-reversion signals (long window) rather than applying a single window to all signal types.

**[ACADEMIC-16]** Thorp (1975, "Portfolio Choice and the Kelly Criterion"): Half-Kelly has 75% of the geometric growth of full Kelly while reducing drawdown variance by 75%. For a system with estimated (not exact) parameters, half-Kelly is unambiguously superior. The spec uses full Kelly with estimated parameters — the expected geometric growth is lower than half-Kelly because parameter estimation error causes Kelly overbetting.

**[ACADEMIC-17]** Glosten and Milgrom (1985, "Bid, Ask and Transaction Prices in a Specialist Market"): The adverse selection component of the bid-ask spread is proportional to the probability of trading against an informed trader. For leveraged ETPs, informed traders (tracking underlying movements) represent a large fraction of flow. The spec's spread cost model uses quoted spread without adjusting for adverse selection — actual effective spread will be higher.

**[ACADEMIC-18]** Hasbrouck (1991, "Measuring the Information Content of Stock Trades"): Trade sign imputation using the Lee-Ready algorithm (used in VPIN) has a ~15% error rate for NYSE stocks in normal conditions. For Asian exchanges at low liquidity, error rates are higher. The spec does not address VPIN accuracy degradation in low-liquidity MODE A sessions.

**[ACADEMIC-19]** Cont (2001, "Empirical Characteristics of Asset Returns"): Fat-tailed return distributions mean Gaussian VaR underestimates tail risk by 2–5× at the 99th percentile. The spec's drawdown tier thresholds (-3%, -5%, -8%) assume daily returns are approximately Gaussian. For 3× leveraged ETPs, a -8% daily drawdown can occur within a single tick sequence — the system has no intraday circuit breaker tighter than -3%.

**[ACADEMIC-20]** Fama (1970, "Efficient Capital Markets"): The meta-labeler's signal is trained on historical patterns over 20 sessions (~6–8 weeks). If the market regime shifts (e.g., a liquidity crisis), the classifier trained on normal conditions will systematically generate false positives. The spec has no regime-change detection for the meta-labeler's training distribution — it assumes stationarity.

---

### Section F: [INFRA] Infrastructure and Systems Engineering

**[INFRA-01]** `clock.rs from_utc_secs()` ModeA arm: `s >= 3600 && s < 28800`. Should be `s >= 82800 || s < 28800` (wrapping condition for 23:00–08:00 spanning midnight). The current arm would classify 23:00–01:00 UTC as DARK mode, leaving MODE A without 2 of its 9 hours.

**[INFRA-02]** The `TickerId(0)` hardcoding: IBKR assigns ticker IDs sequentially. Hardcoding TickerId(0) for the first subscription will conflict with any other TickerId(0) in the system. Required: a monotonically incrementing atomic ID generator starting at 1.

**[INFRA-03]** Docker container memory: c7i-flex.large has 4GB RAM. Rust engine + Python interpreter + Redis + IB Gateway + OS overhead. The spec doesn't provide a memory budget. Under Phase 12/13 universe expansion (3,000–5,000 European tickers, 6 Asian exchanges), the Python heap for UniverseScanner can exceed 2GB. No OOM kill protection is specified.

**[INFRA-04]** SQLite WAL mode `PRAGMA synchronous=NORMAL` allows a data loss window of up to the last second of transactions on OS crash. For position-critical data (open orders, carry positions), `synchronous=FULL` is required. The performance hit (3–5ms per write) is acceptable for a system that writes at most 10–20 WAL records per second.

**[INFRA-05]** The PyO3 FFI creates a Python `GIL` acquisition on every Rust→Python call. With 100 subscribed tickers emitting ticks at 100ms intervals (IBKR tick rate), the GIL is acquired 1,000 times per second. At ~50μs per GIL acquisition, this consumes 50ms/second of the Python thread (5% CPU overhead from GIL alone). Under high-volatility conditions with 250ms tick rates, this doubles.

**[INFRA-06]** No specification for IBKR `reqContractDetails` pacing. IBKR allows 200 req/s for reqContractDetails but imposes a 300 req/10min soft limit with escalating penalties. Phase 12's European universe crawl of 15 exchanges × 1,000 contracts each = 15,000 requests. At the safe rate of 25/min, this takes 10 hours — longer than the Ouroboros window.

**[INFRA-07]** Redis AOF (Append-Only File) is configured but no fsync policy is specified. `appendfsync everysec` loses up to 1 second of data on crash. `appendfsync always` is required for position-critical data but reduces throughput to ~10,000 writes/second. For Chandelier stop updates (which are the primary Redis write-path), `appendfsync everysec` is acceptable if WAL provides the durability backstop.

**[INFRA-08]** IBKR `cancelMktData` is asynchronous. The line counter must decrement only after confirming data delivery has stopped (no tick received for 2+ seconds after cancel), not immediately after calling cancel. The spec's atomic compare-and-swap counter decrements immediately on cancel, creating a window where the counter shows N lines active while N+1 data feeds are actually running.

**[INFRA-09]** No network partition handling. If the EC2 instance loses network connectivity to IBKR for >30 seconds, IBKR disconnects the API session. Position state is now unknown — positions may have moved significantly. On reconnection, the system must not assume positions are where the WAL last recorded them. Required: post-reconnect `reqPositions()` before any trading resumes.

**[INFRA-10]** Docker health check is not specified. Without a health check, Docker considers the container healthy as long as the process is running. A deadlocked Rust thread with a live process PID will not trigger container restart. Required: `/healthz` HTTP endpoint polled every 30 seconds, returning 503 if no tick has been processed in >60 seconds.

**[INFRA-11]** The spec mentions "Supercronic" running Ouroboros at 23:50 ET (≈04:50 UTC). But Phase 13 redefines DARK mode as 21:00–23:00 UTC and Ouroboros should fire at 21:00 UTC. There is a conflicting Ouroboros start time between Phase 11's supercronic crontab (04:50 UTC) and Phase 13's DARK mode definition (21:00 UTC). One of these is wrong.

**[INFRA-12]** No specification for the Rust engine's panic handler. An unrecovered Rust panic in a hot path (e.g., integer overflow in stop calculation) will crash the entire engine process. Required: `panic = "abort"` in Cargo.toml with a custom panic hook that writes `SystemPanic { backtrace, ts_utc }` to WAL before aborting.

**[INFRA-13]** IBKR paper mode uses simulated fills. The simulation fills market orders at the next bid/ask. For low-liquidity MODE A Asian tickers, this simulation is unrealistic — actual fills on illiquid instruments involve partial fills, queue position, and significant slippage. Paper mode results for MODE A will systematically over-estimate fill quality.

**[INFRA-14]** The `crossbeam-channel` unbounded queue between Rust and Python: if Python processing falls behind Rust tick production (e.g., due to GIL contention), the channel queue grows unboundedly until OOM. Required: bounded channel with a backpressure mechanism — drop oldest ticks when queue depth exceeds N.

**[INFRA-15]** No specification for log rotation. On a c7i-flex.large with 20GB EBS, continuous logging at 10MB/hour fills the disk in ~80 days. Required: logrotate configuration with maximum 7-day retention and gzip compression.

**[INFRA-16]** The spec uses `reqHistoricalData` for Kalman training data. IBKR limits historical data requests to 6 simultaneous requests per client ID. During Ouroboros with 100 potential calibration instruments, this creates a severe bottleneck. Required: request queue with 6-concurrent slot pool and FIFO ordering.

**[INFRA-17]** EC2 instance metadata service (IMDS) endpoint is not used. The EC2 instance can query its own termination notice 2 minutes before AWS spot interruption via `169.254.169.254/latest/meta-data/spot/termination-time`. A 2-minute graceful shutdown window is available if this endpoint is polled. Not specified anywhere in the spec — on spot interruption, positions are abandoned with no warning.

**[INFRA-18]** The IBKR `reqContractDetails` for European universe crawl returns data asynchronously via the `contractDetails()` callback. If this callback is not rate-limited at the Python level, it creates a callback storm that can overflow the `EWrapper` message queue and cause message drops. Required: semaphore-limited callback consumption.

**[INFRA-19]** Phase 13's carry state machine state is stored in Redis. Redis is an in-memory store with persistence via AOF. If Redis is restarted (e.g., Docker restart after EC2 reboot), AOF replay time for a large keyspace can take 30–90 seconds. During this replay, MODE A may have already opened and subscribed lines while the carry state is unavailable. Required: explicit Redis readiness check before MODE A open.

**[INFRA-20]** No specification for WAL compaction. The SQLite WAL file grows with every trade, calibration, and mode transition. After 6 months of 24/5 trading, the WAL could contain millions of records. Query performance degrades without periodic compaction. Required: weekly WAL archive job (compress old records to S3, retain last 30 days in SQLite).

---

## PART 2 — ADVERSARIAL RED TEAM REVIEW

### A. The Five Most Likely Failure Modes (Probability × Severity)

---

**FM-01: Container Restart Abandons Live Positions (Probability: HIGH, Severity: CATASTROPHIC)**

**Failure description:** Docker `stop` sends SIGTERM to the Rust process. There is no SIGTERM handler. After 10 seconds, Docker sends SIGKILL. The Rust process terminates instantly. All open positions — whether in MODE A, B, B+, or C — remain open at IBKR with no stop orders, no closing orders, and no monitoring.

**Trigger condition:** Any Docker restart, `docker compose down`, EC2 reboot, OOM kill, or manual process kill during an active trading session.

**Capital loss rate:** Full position value is at risk. For a £10,000 account with 3 open positions × £1,500 each = £4,500 exposed. Without stops, a -30% overnight move (possible for 3× leveraged ETPs) = -£1,350 in an unmonitored position.

**Recoverable?** Only if manual intervention happens within minutes. The positions stay open until manually closed or until the EOD forced-flat at exchange close — which the system cannot execute because it is dead.

**Spec prevention:** None. The spec mentions WAL persistence and Redis state but never specifies a SIGTERM handler or position-flattening shutdown sequence.

**Verdict:** This is the #1 failure mode. A simple `ctrlc` crate + position flatten on SIGTERM eliminates it entirely in ~50 lines of code.

---

**FM-02: OFI yfinance IP Ban Disables All Signal Generation (Probability: HIGH, Severity: HIGH)**

**Failure description:** The OFI computation calls `yf.Ticker(underlying).history()` on every OFI update. With 100 subscribed tickers refreshing every 5 seconds, this is 1,200 Yahoo Finance requests per minute. Yahoo Finance's rate limit is approximately 2,000 requests per hour from a single IP. The system will be IP-banned within 2 hours of MODE B opening.

**Trigger condition:** Standard MODE B operation with full 100-line subscription.

**Capital loss rate:** No OFI signal = meta-labeler input missing = HotScanner confidence degrades to random. In worst case, HotScanner continues generating entries with degraded confidence scores. Losses accumulate on low-quality trades.

**Recoverable?** Yes — switch OFI to use IBKR's already-subscribed data. But recovery requires code change and redeployment. No automatic fallback specified.

**Spec prevention:** None. The spec specifies using yfinance for underlying data but does not flag the rate limiting issue.

---

**FM-03: Ouroboros Silent Partial Completion Corrupts Calibration (Probability: MEDIUM, Severity: HIGH)**

**Failure description:** Ouroboros writes Redis keys sequentially across 9 steps. If it crashes at step 4 (e.g., IBKR historical data pacing violation during calibration), steps 1–3 have written new params and steps 5–9 still hold stale params. The `pipeline_complete` flag is never set. The next MODE A/B open proceeds with "last-known universe lists" (per spec) but those lists were partially overwritten by the failed run.

**Trigger condition:** IBKR pacing violation during Ouroboros, EC2 instance instability, Python OOM during universe crawl.

**Capital loss rate:** Corrupted Thompson Sampling priors send capital to wrong instruments. Corrupted Kelly parameters cause oversizing. Corrupted meta-labeler weights cause systematic misclassification. Losses accumulate across the full trading day.

**Recoverable?** Only via manual inspection of Redis state. The system has no atomic rollback mechanism.

**Spec prevention:** Phase 13 specifies "proceed with last-known universe lists if Ouroboros doesn't complete" — but this addresses non-completion, not partial completion with corrupted intermediate state.

---

**FM-04: clock.rs ModeA Boundary Bug Leaves Asian Session Untraded (Probability: CERTAIN, Severity: MEDIUM)**

**Failure description:** `clock.rs from_utc_secs()` ModeA arm matches `s >= 3600 && s < 28800` (01:00–08:00 UTC). The intent is `s >= 82800 || s < 28800` (23:00–08:00 UTC). As a result, the window 23:00–01:00 UTC is classified as DARK mode. NZX (which opens at 20:00–23:00 UTC in NZDT) and the first 2 hours of TSE (00:00 UTC) are permanently unreachable.

**Trigger condition:** Any attempt to run Phase 13 MODE A. This is a compile-time logical error — it will fail on first run.

**Capital loss rate:** Zero direct loss but zero MODE A returns. The system will operate for the full 9-hour nominal MODE A window but only trade for 7 hours. NZX is effectively dead.

**Recoverable?** Yes — one-line fix in clock.rs. But requires code change, recompile, and deploy.

**Spec prevention:** Phase 13 specifies "23:00–08:00 UTC" correctly in prose. The clock.rs code has the wrong boundary condition. This is a spec-to-implementation divergence.

---

**FM-05: £500 Minimum Position Size Makes All Trades Unprofitable (Probability: HIGH, Severity: MEDIUM)**

**Failure description:** Kelly sizing with low-confidence signals can produce position sizes of £400–£700. IBKR charges £1.00 minimum commission per trade. A £500 buy + £500 sell = £2.00 round-trip = 0.40% transaction cost before spread. The spec's target gross capture for a single HotScanner entry is ~0.3–0.5%. Transaction costs alone eliminate the entire expected profit. These trades are mathematically unprofitable at £500.

**Trigger condition:** Any trade below £1,500 position size.

**Capital loss rate:** Slow capital bleed. With 10 such trades per day at -0.15% average net per trade = -1.5% daily drag that works against the 0.3–0.5% daily net target.

**Recoverable?** Yes — add minimum position size gate of £1,500 before Kelly output. One-line guard.

**Spec prevention:** None. The spec states "1% of equity" as minimum position but at £10k starting equity that is £100 — far below the commission floor.

---

### B. The Three Most Dangerous Theoretical Flaws

---

**TF-01: OFI is Not OFI (Quote Imbalance Masquerading as Order Flow Imbalance)**

**Theoretical assumption violated:** The spec claims HotScanner uses OFI (Order Flow Imbalance) as specified in Cont, Kukanov, and Stoikov (2014). True OFI measures the net signed order flow at each price level across the full order book depth. The spec computes `(bid_size - ask_size) / (bid_size + ask_size)` — this is the Stoikov (2018) quote imbalance, a Level 1 quote metric.

**What happens in live markets:** Quote imbalance is a mean-reverting signal with a half-life of ~30 seconds. OFI is a momentum signal with persistence up to 5 minutes. Trading quote imbalance as if it were OFI means the system enters at the peak of a quote imbalance spike and exits as the imbalance mean-reverts — exactly backwards. Expected outcome: systematic entry timing that is 30 seconds too late for momentum and 30 seconds too early for mean reversion. This systematically reduces win rate below 50%.

---

**TF-02: Kelly Overbetting Due to Hardcoded Parameters**

**Theoretical assumption violated:** Kelly's formula requires exact knowledge of `p` (win probability) and `avg_win/avg_loss`. The spec hardcodes `avg_win = avg_loss = 0.02` as constants never updated by Ouroboros. If actual average win is 0.005% (common for short-horizon strategies), the hardcoded 0.02 causes Kelly to bet 4× too large.

**What happens in live markets:** Full Kelly with 4× overestimated `avg_win` produces position sizes that are 4× optimal. Kelly (1956) showed that betting 2× Kelly produces zero expected growth (same as not betting). Betting 4× Kelly produces negative expected growth — the system is guaranteed to lose capital in expectation even if the underlying signal is correct. This is not a market risk — it is a mathematical certainty of ruin under the stated parameters.

---

**TF-03: Meta-Labeler Overfitting on 60–100 Samples**

**Theoretical assumption violated:** De Prado (2018) requires that the meta-labeler be trained on purged k-fold cross-validated data with minimum 1,000 samples for statistically stable estimates in financial time series. The spec trains on "last 20 sessions" (60–100 samples for most instruments). This violates the minimum sample size requirement by a factor of 10–16×.

**What happens in live markets:** A Logistic Regression trained on 100 samples from 6 weeks of price history will achieve ~70–80% apparent in-sample accuracy due to overfitting. Live accuracy will regress to 50–55% (near random). The meta-labeler will appear to be filtering signals but will actually be adding noise. The system will believe it has a high-quality filter gate while generating random binary classifications. Expected outcome: the meta-labeler gate reduces trading frequency without improving win rate, systematically lowering PnL.

---

### C. The Regime Change Stress Test

**Scenario 1: VIX Spike 15 → 45 in a Single Session (August 2024 Style)**

*Signal generation:* HotScanner generates entries using CUSUM, which uses a static session-open μ. In a VIX spike, the first 15 minutes of the session may already be +5% on the underlying. CUSUM with static μ generates immediate LONG signals on every ETP as prices move far from open. These are momentum entries — which is correct — but sized using yesterday's nightly Kalman Q and DCC-GARCH correlation matrix. Both are calibrated to a low-vol regime.

*Routing:* Router evaluates ETP health using spread as % of mid-price. In a VIX spike, bid-ask spreads on 3× ETPs widen from 0.05% to 0.3–0.5% of mid. The Router's spread health check may pass these as healthy (if the threshold is > 0.5%) or block them entirely. The spec does not specify the spread threshold value, so this branch is undefined.

*Sizing:* Kelly sizing uses `avg_win = 0.02` (hardcoded). In a VIX spike, actual wins are larger (5–15% on 3× ETPs) but loses are also larger (−10% to −30%). The hardcoded Kelly parameter produces underpositioning in the first hour and cannot adapt to the changed vol regime.

*RiskGate:* If the first position loses −3%, YELLOW tier fires. YELLOW reduces new position count to 3. With VIX at 45, the next 3 positions are likely in the same direction (everyone is selling). YELLOW → ORANGE at −5% stops new positions. The RiskGate correctly reduces exposure but the existing 3 positions are now in a −15% to −25% drawdown as 3× ETPs decline 5–8× the underlying move.

*Exit:* Chandelier stops are set at `ATR(14) × M1(3.0)`. With intraday VIX spike, ATR(14) on 1-minute bars surges during the first 30 minutes of the session. The Chandelier widens significantly — stops move AWAY from the current price as ATR rises. A −30% move in QQQ3.L would be required to trigger the Chandelier in a high-vol calibration. The system holds through the spike with wide stops.

*Verdict:* The system has limited protection in a VIX spike. The RiskGate correctly reduces new entries but existing positions are held through the spike with widening Chandelier stops. The spec has no intraday vol-regime circuit breaker.

---

**Scenario 2: Prolonged Low-Volatility Grind (VIX 10–12 for 60+ Days)**

*Signal generation:* CUSUM with static μ generates zero signals in a low-vol grind because price never moves far enough from open to trigger the detection threshold. After 20 minutes of tight-range trading, CUSUM has no triggers. HotScanner falls back to OFI (quote imbalance) and Kalman signals — both are calibrated to the prior 60 sessions of low vol, so signal thresholds are very tight.

*RotationScanner:* Thompson Sampling converges on the instruments that have shown any positive return in the low-vol environment. These are typically the lowest-beta instruments. Kelly sizing is minimal because `avg_win` is effectively zero in a low-vol environment (but hardcoded at 0.02 — the mismatch produces oversized entries in instruments that barely move).

*Execution cost:* With spread costs eating 0.3–0.4% round-trip and expected gross capture near zero in a low-vol grind, every trade is a net drain. The system will continue generating entries (because signals trigger at low thresholds) and losing money on each one.

*Ouroboros calibration:* After 60 days, Kalman Q is calibrated to very low volatility. When vol eventually returns, the Q matrix significantly underestimates process noise. The Kalman filter will be overconfident in the low-vol state and will react too slowly to the vol regime change.

*Verdict:* Low-vol grinds are more dangerous than VIX spikes for this system because the system cannot distinguish "low signal" from "zero signal" — it will continue trading at a loss in a range-bound market.

---

**Scenario 3: Flash Crash in QQQ3.L (−35% in 8 Minutes)**

*Note:* A −35% move in QQQ3.L implies a −11.7% move in QQQ. This is achievable (August 5, 2024: QQQ fell −6% in the first hour; a −12% intraday low is rare but not impossible for a severe catalyst). The scenario is realistic.

*Signal generation:* CUSUM immediately generates a SELL/exit signal as price drops below 3σ from session open. But CUSUM is a detection-only signal — it signals that a change has occurred, not that it will continue. If CUSUM's threshold is calibrated to trigger on normal 0.3% moves, it would have already triggered 30 times before the −35% move. The trigger has been normalised out. Alternatively, if the threshold is high (2%), the CUSUM signal fires at −2% and the system has a Chandelier stop at −3% (M1 × ATR). By the time Chandelier fires at −3%, the ETP is at −20%.

*Execution:* In a flash crash, the bid-ask spread on QQQ3.L widens to 1–3% of mid. The Router's spread health check fires (if the threshold is < 1%) and blocks new entries — but cannot block exits. The Executioner's TWAP slicer submits a limit order at mid. In a fast flash crash, limit orders at mid are never filled as the market moves through them. The system may hold the position for the full duration of the −35% move waiting for a mid-fill.

*RiskGate:* ORANGE fires at −5% daily. By the time QQQ3.L is −35%, the system is in RED. RED closes all positions — but "close all positions" via limit order in a flash crash takes minutes due to the wide spread and rapidly declining bid. A market order would be filled but at a catastrophic price.

*Verdict:* Flash crashes are the system's worst-case scenario. The combination of limit order execution, Chandelier stops calibrated to pre-crash ATR, and no emergency market-order escalation means the system will hold through the entire −35% move before exiting near the bottom.

---

### D. The Execution Cost Reality Check

**Target: 0.3–0.5% daily net return. Bottom-up cost stack analysis:**

| Cost Item | Basis | Estimate per Trade |
|-----------|-------|-------------------|
| IBKR Commission | £1.00 minimum or 0.05% | £1.00–£1.50 per side |
| Round-trip commission | 2 sides | £2.00–£3.00 |
| Spread cost (LSE ETP) | 0.05–0.10% of position | £0.50–£1.50 per side |
| Spread round-trip | Entry + exit | £1.00–£3.00 |
| Slippage (market impact) | Kyle's Lambda × volume | £0.25–£2.00 |
| 3× ETP volatility decay | Daily reset drag | ~0.1% per day held |
| FX conversion (non-GBP) | 0.03–0.05% per conversion | £0.30–£1.50 (if applicable) |

**Total round-trip cost per £2,000 position:**
- Minimum: £3.25 (0.16% of position)
- Typical: £6.00–£8.00 (0.30–0.40% of position)
- High-vol day: £10.00–£15.00 (0.50–0.75%)

**Break-even gross return required per trade at £2,000 position:**
- Minimum scenario: 0.16% gross per trade
- Typical scenario: 0.35–0.45% gross per trade
- High-vol scenario: 0.55–0.80% gross per trade

**3× ETP volatility decay (separate from transaction costs):**
- A 3× ETP resets daily to 3× the single-day return. For a £10,000 starting portfolio, with VIX=20 (daily underlying vol ≈ 1.25%), the daily decay is approximately: `0.5 × (3²-3) × (0.0125)²` = 0.047% per day. This is a structural alpha drag, not a transaction cost. Over 252 trading days, this compounds to ~11.4% annual structural drag per leveraged position held overnight.

**Assessment of 0.3–0.5% daily net target:**
The target is achievable in principle but requires average gross capture of ≥0.70% per trade to net 0.35% after costs. A 3× ETP moving 1% daily (underlying moves 0.33%) generates 1% gross. With 3 trades per day at 0.33% average gross capture, and costs of 0.35% per trade, the system nets approximately 0% on average. The target requires the system to find and capture the top 30% of daily moves — which requires the signal quality to be significantly better than random. Given the OFI mislabeling, hardcoded Kelly parameters, and meta-labeler sample size issues, the current spec does not demonstrate this signal quality.

**Minimum viable position size:** £1,500 (to keep commission below 0.13% of position). The spec allows Kelly to produce positions below £1,000 for low-confidence signals — these are guaranteed losers.

---

### E. The 100-Line Constraint Under Pressure

**Line budget analysis across Phases 11 + 12 + 13 simultaneously active:**

**Base allocation by mode:**
- MODE A active: 100 lines for Asian equities
- MODE B: ~80 LSE ETPs + tracking underlyings + European equities = pressure-tested below
- MODE B+: 80 LSE + 20 US = 100 lines
- MODE C: 100 US/Canada direct equities

**Tracking pairs cost:**
- Each open ETP position requires 1 ETP line + 1 underlying line = 2 lines per position
- With 5 open positions: 10 lines consumed by tracking pairs alone
- Phase 12 adds European direct equities: no underlying tracking required (trading direct equity, not ETP proxy)
- Phase 13 Asian carry positions: if 3 carry positions persist into MODE B, they hold 6 lines (3 ETP + 3 underlying) as carry-reserved lines that cannot be freed until positions close

**Worst-case line budget (MODE A → MODE B transition with carry positions):**
```
Carry positions (3 × ETP + underlying):    6 lines  [locked]
Active MODE A positions being flattened:   4 lines  [transitioning]
MODE B LSE ETPs to subscribe:             70 lines  [needed]
European equities to scan:                15 lines  [needed]
Total demand:                             95 lines  ✓ (barely)

If carry positions = 5:
5 × 2 = 10 carry lines + 4 active + 70 LSE + 10 European = 94 lines ✓

If carry positions = 8:
8 × 2 = 16 carry lines + 4 active + 70 LSE + 8 European = 98 lines ✓

If carry positions = 10:
10 × 2 = 20 carry lines + 4 active + 65 LSE + 5 European = 94 lines — but this limits European scanning severely
```

**Verdict on 100-line constraint:** With 5 or fewer carry positions, all three phases are simultaneously viable. At 8+ carry positions, the system must reduce LSE ETP scanning to accommodate carry tracking. At 10+ carry positions, the system should enforce a maximum carry position count (spec does not specify this). **Recommendation: cap carry positions at 6 (12 lines), leaving 88 lines for active scanning across all modes.**

---

### F. The Ouroboros Single Point of Failure

**What happens if Ouroboros runs long:**
The spec (Phase 13, Section 1) states: "Assert Ouroboros completed. If not complete after 2h: log CRITICAL, proceed with last-known universe lists." This is correct. However, "last-known" can be up to 48+ hours stale if the prior two nights both failed. No maximum staleness threshold is specified. The system could trade on a 3-day-old universe with delisted stocks.

**What happens if Ouroboros produces corrupted calibration:**
No atomic rollback mechanism. Redis keys are overwritten sequentially. A crash at step 4/9 leaves 4 keys with new values and 5 with old values. The system will trade on mixed-vintage calibration parameters. No alert fires. Detection only occurs if someone manually inspects Redis or notices abnormal trade behaviour.

**What happens if EC2 crashes at 22:30 during calibration:**
Docker restart policy `unless-stopped` restarts containers on EC2 reboot. On restart at ~22:45 UTC (15 minutes before MODE A), Ouroboros will not auto-restart (supercronic crontab won't fire until the next scheduled time). MODE A opens at 23:00 UTC with whatever partial Redis state exists from the crashed run. Positions from prior sessions may appear in Redis as open when they were physically closed before the crash. This is a state corruption scenario with no recovery path specified.

**What happens if IBKR data quality is poor during calibration:**
The Kyle's Lambda calibration step uses `reqHistoricalData` for each instrument. If IBKR returns empty or partial data for an instrument (common during off-hours maintenance windows), Kyle's Lambda for that instrument defaults to... nothing. The spec does not specify a fallback Kyle's Lambda for instruments where historical data is unavailable. The Almgren-Chriss sizer will divide by zero.

**Verdict:** Ouroboros has insufficient fault tolerance. The two required fixes are: (1) step checkpointing with Redis-backed resume, and (2) a watchdog timer at 22:45 UTC that loads the last complete calibration if `pipeline_complete` is not set.

---

### G. The ISA Compliance Exposure

**Operations that may violate ISA regulations:**

1. **Intraday position cycling:** ISA rules permit buying and selling the same security within a day (stocks and shares ISA). However, the spec's T+2 settlement means the proceeds of an intraday sale are not settled for 2 business days. Using unsettled proceeds to fund new purchases (free-riding) is prohibited under IBKR's ISA terms. The spec has no settlement cycle tracker.

2. **MODE C direct US equities:** All US equities traded in MODE C must be on HMRC Recognised Stock Exchanges (HMRC Table 1: NYSE and NASDAQ are included). This is correctly specified. However, the SubUniverseAllocator for Phase 13 does not explicitly filter out OTC stocks, pink sheet stocks, or newly-listed stocks that may not yet appear on HMRC's table. A new listing in its first 30 days may not be on HMRC's table.

3. **Foreign exchange transactions:** UK ISA rules require all investments to be in securities, not currency. When the system holds EUR-denominated European equities (Phase 12), the FX conversion at purchase and sale creates a currency exposure. HMRC's position on whether this constitutes a non-qualifying investment has not been resolved. The spec assumes it is compliant but provides no reference to HMRC guidance.

4. **Leveraged ETPs (3×):** Leveraged ETPs are equities (ETP shares) and are generally ISA-eligible if listed on a recognised exchange. This is correctly handled. However, some 3× ETPs are structured as debt securities (notes/ETNs) rather than equity ETPs. ETNs may not be ISA-eligible. The Smart Router's ISA gate does not distinguish between equity ETPs and debt ETNs.

5. **Short exposure via inverse ETPs:** QQQS.L is a short (inverse) QQQ ETP. Holding this creates synthetic short exposure. HMRC's position on whether synthetic short exposure via inverse ETPs violates ISA short-selling restrictions is legally ambiguous. The spec states "no shorting" but treats inverse ETPs as ISA-compliant long positions.

**Routing logic edge case:** If the ISA eligibility table (updated nightly by Ouroboros) fails to update due to a crashed Ouroboros step, an instrument that has become ISA-ineligible (e.g., following an HMRC table update) will not be caught by the gate. The system will submit orders for ISA-ineligible instruments with no alerting.

**Liability exposure:** An ISA compliance breach results in HMRC voiding the entire ISA wrapper. All gains become subject to capital gains tax and income tax, retroactively. IBKR may also close the ISA account. For a £10,000 account, the financial impact is bounded, but the precedent is severe.

---

## PART 3 — TOP 10 HIGHEST-PRIORITY FIXES

| Priority | Problem | Fix | Severity | Hours |
|----------|---------|-----|----------|-------|
| **P0-01** | No SIGTERM handler — Docker restart abandons live broker positions with no stops | Add `ctrlc` crate handler: on SIGTERM, submit MOC closes for all open positions, wait 30s for fills, write shutdown WAL event, then exit | CRITICAL | 8h |
| **P0-02** | `clock.rs` ModeA arm starts at 01:00 UTC not 23:00 UTC — 2 hours of Asian session permanently lost | Change `s >= 3600 && s < 28800` to `s >= 82800 \|\| s < 28800` | CRITICAL | 1h |
| **P0-03** | `avg_win` and `avg_loss` hardcoded at 0.02 and never updated — Kelly fractions may be 4× wrong | Add Ouroboros calibration step that reads last N closed trades from WAL, computes empirical avg_win/avg_loss, writes to Redis; replace hardcoded constants with Redis reads | CRITICAL | 6h |
| **P0-04** | OFI uses yfinance on every tick — guaranteed IP ban within 2 hours of MODE B opening | Replace yfinance calls in OFI computation with IBKR's already-subscribed `reqMktData` data; remove all yfinance from hot path | CRITICAL | 4h |
| **P0-05** | Exit signal logic closes positions locally but never submits SELL order to IBKR — positions remain open at broker | Add `ibapi.placeOrder(OrderId, Contract, sell_order)` call to all exit signal handlers, with WAL logging of broker order ID | CRITICAL | 6h |
| **P0-06** | Minimum position size allows Kelly to produce £400–£700 positions — guaranteed losers after commission | Add pre-Kelly gate: `if kelly_size < 1500: skip_trade(reason="below_min_size")` | CRITICAL | 1h |
| **P1-01** | Pre-LSE APScheduler jobs use `timezone="UTC"` — in BST the macro update fires at the same time as LSE open, not before it | Change `timezone="UTC"` to `timezone="Europe/London"` for all pre-market scheduler jobs in `main.py` | HIGH | 2h |
| **P1-02** | Ouroboros has no step checkpointing — EC2 restart during calibration requires full 2-hour redo | Write `ouroboros_step_N_ts` Redis key after each of 9 steps; on startup check for incomplete run and resume from last complete step | HIGH | 6h |
| **P1-03** | Reconciliation reads local cache not IBKR positions — discrepancies between system state and broker state are never detected | Call `reqPositions()` at each mode transition; reconcile against WAL; alert on discrepancy > 0 | HIGH | 8h |
| **P1-04** | LSE summer close is 15:30 UTC but MODE B+ end is hardcoded at 16:30 UTC — 60-minute scanning of a closed market every BST day | Compute `lse_close_utc = lse_offset_corrected(16:30)` using `ZoneInfo("Europe/London")`; use this as MODE B+ boundary | HIGH | 3h |

---

## TIMEZONE DECISION MATRIX

### TZ-01: US/UK DST Gap Weeks (~21 days March, ~7 days November)
**Issue:** During the US/UK DST gap, NYSE opens at 14:30 UTC (not 15:30 UTC). MODE B+ boundary shifts.
**Decision:** ACCEPT — `dst_anchor.py` already computes NYSE open dynamically using `ZoneInfo("America/New_York")`. This handles the gap correctly for execution. MODE B+ boundary for US equities is derived from NYSE open, not hardcoded.
**Action:** NONE (verify `dst_anchor.py` is called at runtime not just during Ouroboros)

### TZ-02: ASX Seasonal UTC Shift (AEST vs AEDT)
**Issue:** ASX opens at 00:00 UTC in AEST (winter) and 23:00 UTC in AEDT (summer). This shifts ASX entirely outside MODE A in AEDT.
**Decision:** ACCEPT — No ASX tickers are in the current active universe (12 ISA funds are all LSE ETPs). Phase 13 specifies ASX as future expansion only. Document as MONITOR for Phase 13 activation.
**Action:** Add comment in Phase 13 spec: "ASX open shifts 23:00–00:00 UTC depending on AEDT/AEST. Implement `ZoneInfo('Australia/Sydney')` offset check before subscribing ASX lines."

### TZ-03: IBKR Server Reset at 04:45 UTC (Within MODE A)
**Issue:** IBKR server reset disconnects all subscriptions during MODE A.
**Decision:** ACCEPT — unavoidable. The fix (reconnection + re-subscription) is already in spec scope (IMPROVEMENT-21).
**Action:** Update comment in spec from "04:45 UK time" to "04:45 UTC" (currently ambiguous in some spec sections).

### TZ-04: TSE/HKEX Lunch Breaks (02:30–03:30 UTC / 04:00–05:00 UTC)
**Issue:** HKEX has a 1-hour midday break during MODE A. TSE has a 30-minute lunch break.
**Decision:** ACCEPT with spec clarification — No TSE/HKEX tickers in current universe. For Phase 13 activation, add explicit lunch break sub-states.
**Action:** Add IMPROVEMENT-24 (TSE lunch break MONITORED sub-state) to Phase 13 spec as a deferred Phase 13 implementation note.

### TZ-05: KRX Dead Zone (06:30–08:00 UTC)
**Issue:** KRX pre-open auction period is not tradeable.
**Decision:** ACCEPT — No KRX tickers in current universe. Document for Phase 13 activation.
**Action:** NONE (Phase 13 expansion item)

### TZ-06: NZX NZDT Conflict with DARK Mode (NZX opens 20:00 UTC in summer)
**Issue:** NZX opens at 20:00 UTC during NZDT (Sep–Apr), which falls in the MODE C / pre-DARK window. The 20:00–21:00 UTC NZX session is in MODE C (unscanned). At 21:00 UTC (DARK open), any NZX carry position enters DARK with "no trading" policy.
**Decision:** ACCEPT with note — No NZX tickers in current universe. NZX is acknowledged as a complex edge case for Phase 13 activation.
**Action:** Add note to Phase 13: "NZX NZDT open (20:00 UTC) conflicts with DARK mode boundary. Defer NZX activation until carry state machine handles 20:00–21:00 UTC pre-DARK window explicitly."

### TZ-07: SGX No DST (UTC+8 Fixed)
**Issue:** SGX has no DST and always closes at 09:00 UTC. This overlaps with MODE B open at 08:00 UTC.
**Decision:** ACCEPT — SGX close at 09:00 UTC creates a 1-hour overlap with MODE B. The spec acknowledges this. The carry state machine handles SGX mega-runners in MONITORED state during the overlap.
**Action:** NONE

### TZ-08: Pre-LSE APScheduler Jobs Using `timezone="UTC"` → NEEDS FIX
**Issue:** In `main.py`, scheduled pre-market jobs (cross-asset macro update, etc.) use `timezone="UTC"`. In BST (UTC+1), a job scheduled for "07:00 UTC" fires 1 hour before LSE opens in winter but AT LSE open in summer. The cross-asset macro regime seeding arrives simultaneously with the first trades, not before them.
**Decision:** FIX — Change all pre-market job scheduler calls from `timezone="UTC"` to `timezone="Europe/London"`.
**Spec impact:** Phase 11, Section 6 (Clock Extensions) should explicitly state: "All pre-LSE jobs must use `timezone='Europe/London'` not `timezone='UTC'` to maintain correct relative timing across BST/GMT transitions."
**Action:** Apply this change to Phase 11 spec language AND to `main.py`.

### TZ-09: PDF2 Pre-NYSE Fire Time
**Issue:** PDF2 (Risk & Structural) is scheduled to fire before NYSE open. If this job uses `timezone="UTC"` with a fixed hour, it fires at the wrong time during EDT vs EST.
**Decision:** VERIFY — Confirm whether PDF2 scheduler call uses `dst_anchor.py`'s `nyse_open_utc()` or a hardcoded time.
**Action:** Audit `main.py` PDF2 scheduler call. If hardcoded, change to `nyse_open_utc() - timedelta(minutes=30)`.

### TZ-10: LSE Summer Close (15:30 UTC in BST)
**Issue:** LSE closes at 16:30 UTC in GMT but 15:30 UTC in BST. Mode B+ continues to 16:30 UTC throughout the year. In BST, MODE B+ runs on a closed LSE for 60 minutes.
**Decision:** FIX — Compute LSE close dynamically. MODE B+ boundary should be `max(lse_close_utc, nyse_open_utc + 2h)`.
**Spec impact:** Phase 11, Section 2 (Mode Definitions) must update the MODE B+ description to show dynamic end time, not fixed 16:30 UTC.
**Action:** Apply to Phase 11 spec.

---

## PRIORITY ACTION MATRIX

### P0 — CRITICAL (Must Fix Before Any Live Capital)

| ID | Finding | Phase | Effort |
|----|---------|-------|--------|
| SC-01 | SIGTERM handler + position flatten on shutdown | 11 | 8h |
| SC-02 | `clock.rs` ModeA boundary fix (01:00 → 23:00) | 13 | 1h |
| SC-03 | `avg_win`/`avg_loss` Ouroboros calibration (not hardcoded) | 11 | 6h |
| SC-04 | Replace yfinance in OFI hot path with IBKR subscription data | 11 | 4h |
| SC-05 | Exit signal must submit actual SELL order to IBKR | 11 | 6h |
| SC-06 | Minimum position size gate £1,500 | 11 | 1h |

### P1 — HIGH (Fix Before 100-Trade Validation Gate)

| ID | Finding | Phase | Effort |
|----|---------|-------|--------|
| SC-07 | Pre-LSE APScheduler timezone fix (`Europe/London`) | 11 | 2h |
| SC-08 | Ouroboros step checkpointing with Redis resume | 11/13 | 6h |
| SC-09 | Reconciliation via `reqPositions()` not local cache | 11 | 8h |
| SC-10 | LSE summer close dynamic boundary in MODE B+ | 11 | 3h |
| SC-11 | Ouroboros 22:45 UTC watchdog timer | 13 | 3h |
| SC-12 | Minimum carry position cap (max 6) | 13 | 2h |
| SC-13 | IBKR error code response matrix | 11 | 4h |
| SC-14 | `reqMarketDataType(3)` call on startup/reconnect | 11 | 1h |
| SC-15 | OFI → true Cont/Kukanov formula or rename to QuoteImbalance | 11 | 4h |

### P2 — MEDIUM (Fix Before Phase 12 Activation)

| ID | Finding | Phase | Effort |
|----|---------|-------|--------|
| SC-16 | CUSUM dynamic mean (EWMA update) | 11 | 3h |
| SC-17 | VPIN exchange-scoped bucket reset | 11/13 | 4h |
| SC-18 | Thompson Sampling log-return reward (Normal-Normal) | 11 | 6h |
| SC-19 | Chandelier gap-detection before ATR | 11 | 4h |
| SC-20 | Half-Kelly until 250 validated trades | 11 | 2h |
| SC-21 | Meta-labeler minimum sample size (1,000) | 11 | ongoing |
| SC-22 | Docker health check + `/healthz` endpoint | 11 | 3h |
| SC-23 | Rust panic handler → WAL before abort | 11 | 2h |
| SC-24 | Redis memory policy + keyspace TTLs | 11 | 1h |
| SC-25 | IBKR `cancelMktData` async confirmation before counter decrement | 11 | 4h |

---

## SUMMARY STATISTICS

| Category | Count |
|----------|-------|
| [FLAW] | 30 |
| [RISK] | 30 |
| [IMPROVEMENT] | 30 |
| [MISSING] | 20 |
| [ACADEMIC] | 20 |
| [INFRA] | 20 |
| **Total bullets** | **140** |
| Adversarial FM sections | 7 (A-G) |
| Top 10 fixes | 10 |
| Timezone decisions | 10 (TZ-01 to TZ-10) |
| P0 critical actions | 6 |
| P1 high actions | 9 |
| P2 medium actions | 10 |

---

*Generated by Claude Sonnet 4.6, 2026-03-09*
*Source specs: PHASE_11_DIRECT_EQUITY_SPEC.md (2,469L), PHASE_12_EUROPEAN_EQUITY_SPEC.md (1,323L), PHASE_13_ASIA_PACIFIC_SPEC.md (1,789L)*
*Analysis method: Full adversarial deep review per GEMINI_DEEP_ANALYSIS_PROMPT.md protocol*
