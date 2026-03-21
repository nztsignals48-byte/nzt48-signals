# Ouroboros Operations Runbook

AEGIS V2 Python Analytics Brain -- Manual Operations Guide

EC2 Instance: `3.230.44.22` (us-east-1c, c7i-flex.large)
Containers: `aegis-v2` (engine + cron) | `aegis-ib-gateway` (IBKR port 4003) | `aegis-redis` (port 6379 internal)

---

## 1. Quick Reference: SSH + Docker Commands

### Connect to EC2

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22
```

### Docker exec into engine container

```bash
# Interactive shell
docker exec -it aegis-v2 bash

# Run a single command
docker exec aegis-v2 python3 -m python_brain.ouroboros.<module>
```

### View logs

```bash
# Engine (Rust) logs — last 100 lines, follow
docker logs aegis-v2 --tail 100 -f

# Specific Ouroboros log inside container
docker exec aegis-v2 tail -100 /var/log/ouroboros.log
docker exec aegis-v2 tail -100 /var/log/config_writer.log
docker exec aegis-v2 tail -100 /var/log/ticker_selector.log
docker exec aegis-v2 tail -100 /var/log/sheets_sync.log
docker exec aegis-v2 tail -100 /var/log/external_monitor.log

# All available log files
docker exec aegis-v2 ls -lht /var/log/*.log
```

### Restart services

```bash
# Restart engine (keeps IB Gateway + Redis running)
cd /home/ubuntu/nzt48-aegis-v2 && docker compose restart aegis-v2

# Restart everything
cd /home/ubuntu/nzt48-aegis-v2 && docker compose down && docker compose up -d

# Rebuild and restart (after code changes)
cd /home/ubuntu/nzt48-aegis-v2 && docker compose build aegis-v2 && docker compose up -d

# Restart IB Gateway only
cd /home/ubuntu/nzt48-aegis-v2 && docker compose restart ib-gateway
```

### Check container status

```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
docker stats --no-stream
```

---

## 2. Daily Schedule (UTC)

All times UTC. Runs Mon-Fri unless noted. The crontab lives at `/app/crontab` inside the container, executed by Supercronic.

### Nightly Learning Window (04:40 - 05:10 UTC)

| Time  | Module                 | Description                                          | Log File                       |
|-------|------------------------|------------------------------------------------------|--------------------------------|
| 04:40 | maintenance --cleanup  | Parquet orphan cleanup (prevents disk fill)          | /var/log/maintenance.log       |
| 04:45 | log_rotate             | Rotate all /var/log/*.log, keep 7 days               | /var/log/log_rotate.log        |
| 04:50 | nightly_v6             | Main learning loop: trade analysis, parameter tuning | /var/log/ouroboros.log          |
| 04:51 | config_writer          | Write dynamic_weights.toml from nightly output       | /var/log/config_writer.log     |
| 04:52 | win_loss_delta --push-sheets | Indicator delta analysis, push to Sheets       | /var/log/win_loss_delta.log    |
| 04:52 | config_fixes --write-ftt | FTT registry (Mondays only)                        | /var/log/config_fixes.log      |
| 04:53 | claude_review --send-telegram | AI nightly review via Anthropic API           | /var/log/claude_review.log     |
| 04:54 | fill_quality           | Paper vs live slippage model                         | /var/log/fill_quality.log      |
| 04:55 | post_trade_diagnostics | Consolidated diagnostic suite (N10w-N10gg)           | /var/log/post_trade_diagnostics.log |
| 05:10 | ouroboros_monitor --all --send-telegram | TOML health + staleness check       | /var/log/ouroboros_monitor.log  |

### Pre-Market (06:00 - 07:55 UTC)

| Time  | Module                 | Description                                          | Log File                       |
|-------|------------------------|------------------------------------------------------|--------------------------------|
| 06:00 | universe_refresh       | Validate rotating subset of 500 tickers              | /var/log/universe_refresh.log  |
| 06:30 | ticker_selector        | Full daily re-score after universe refresh            | /var/log/ticker_selector.log   |
| 07:00 | backfill_simulator     | 7-day historical backtest simulation                  | /var/log/ouroboros_backfill.log |
| 07:45 | claude_briefing --send-telegram | Morning briefing before European session    | /var/log/claude_briefing.log   |
| 07:55 | session_pdf --session european | European session PDF + Telegram              | /var/log/session_pdf.log       |

### Market Hours (08:00 - 20:45 UTC)

| Time     | Module                 | Description                                       | Log File                       |
|----------|------------------------|---------------------------------------------------|--------------------------------|
| */15     | ticker_selector        | 15-min watchlist rotation (08:00-20:45)           | /var/log/ticker_selector_15m.log |
| */15     | bridge_health          | Bridge health check (08:00-20:00)                 | /var/log/bridge_health.log     |
| 14:25    | session_pdf --session american | US session PDF + Telegram                  | /var/log/session_pdf.log       |
| 16:30    | session_pdf --session us_only  | US-only session PDF + Telegram             | /var/log/session_pdf.log       |

### After-Hours / Evening (21:00 - 23:59 UTC)

| Time  | Module                       | Description                                  | Log File                  |
|-------|------------------------------|----------------------------------------------|---------------------------|
| 21:15 | daily_sim_report --send-telegram | Daily simulated trade PDF + Telegram     | /var/log/sim_report.log   |
| 21:20 | cost_drag_report --send-telegram | Friction/cost analysis                   | /var/log/cost_drag.log    |

### Asian Pre-Market (23:00 - 07:45 UTC)

| Time     | Module            | Description                                      | Log File                       |
|----------|-------------------|--------------------------------------------------|--------------------------------|
| */15     | ticker_selector   | 15-min rotation (23:00 Sun-Thu through 07:45)    | /var/log/ticker_selector_15m.log |
| 00:55    | session_pdf --session asian | Asian session PDF + Telegram              | /var/log/session_pdf.log       |

### Continuous / Periodic

| Frequency    | Module                     | Description                               | Log File                       |
|--------------|----------------------------|-------------------------------------------|--------------------------------|
| Every 5 min  | sheets_sync                | Drain Redis queue to Google Sheets         | /var/log/sheets_sync.log       |
| Every 5 min  | external_monitor --quiet   | Deep health check (engine, disk, IBKR)     | /var/log/external_monitor.log  |
| Every 4 hrs  | telegram_notify --heartbeat| Heartbeat ping to Telegram                 | /var/log/telegram.log          |
| Every 6 hrs  | fx_refresh                 | Live FX rates from yfinance                | /var/log/fx_refresh.log        |
| Every 6 hrs  | contract_expander          | Auto-grow contracts.toml from scored tickers| /var/log/contract_expander.log |
| 08:00 daily  | external_monitor --daily   | Daily health summary to Telegram           | /var/log/external_monitor.log  |
| Sunday 22:00 | ibkr_scanner               | Weekly full IBKR universe scan             | /var/log/ibkr_scanner.log      |

### Background Daemons (started by entrypoint.sh, not cron)

| Process              | Description                                         |
|----------------------|-----------------------------------------------------|
| supercronic          | Cron scheduler for all the above jobs               |
| wal_watcher          | Tails WAL ndjson, sends Telegram alerts + Sheets    |
| kill_switch --listen | Telegram bot polling for /kill /pause /resume        |
| bridge_watchdog      | Bridge SPOF heartbeat monitor + auto-restart         |

### Execution Order Dependencies

```
maintenance --cleanup  (04:40)  ── standalone, cleans before nightly
log_rotate             (04:45)  ── standalone, rotates before nightly
nightly_v6             (04:50)  ── MUST run first: produces recommendations JSON
  config_writer        (04:51)  ── reads nightly_v6 output
  win_loss_delta       (04:52)  ── reads nightly_v6 output (WAL analysis)
  config_fixes         (04:52)  ── standalone (Monday only)
  claude_review        (04:53)  ── reads nightly_v6 + config_writer + delta output
  fill_quality         (04:54)  ── reads WAL (independent of nightly)
  post_trade_diagnostics(04:55) ── reads WAL (independent of nightly)
  ouroboros_monitor    (05:10)  ── checks TOML freshness (after config_writer)

universe_refresh       (06:00)  ── MUST run before ticker_selector
  ticker_selector      (06:30)  ── reads universe_refresh output

claude_briefing        (07:45)  ── reads claude_review output (N6a feeds N6b)
```

### What Happens If a Step Fails

| Failed Step          | Impact                                                         | Recovery                            |
|----------------------|----------------------------------------------------------------|-------------------------------------|
| nightly_v6           | No new recommendations; config_writer uses stale data          | Re-run manually; config_writer safe |
| config_writer        | Engine uses previous dynamic_weights.toml (stale but valid)    | Re-run manually, then SIGHUP engine |
| ticker_selector      | Watchlist goes stale; engine continues with existing contracts  | Re-run manually                     |
| universe_refresh     | ticker_selector uses stale universe; no new ticker discovery    | Re-run manually next day            |
| sheets_sync          | Dashboard stops updating; Redis queue grows (bounded by memory) | Re-run; queue drains automatically  |
| claude_review        | No AI review; briefing runs with reduced context               | Re-run with --send-telegram         |
| external_monitor     | No health alerts; system keeps running                          | Re-run with --daily                 |

---

## 3. Manual Step Execution

All commands run inside the aegis-v2 container. Prefix with:
```bash
docker exec aegis-v2 <command>
```

Or get an interactive shell first:
```bash
docker exec -it aegis-v2 bash
cd /app
```

---

### nightly_v6 -- Main Learning Loop

**What it does:** Analyzes today's paper trades from WAL, checks regime accuracy, optimizes parameters with guardrails (max 15% drift), generates daily report and pre-market battle plan.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.nightly_v6
```

**Output:**
- `/app/data/ouroboros_recommendations.json` (parameter recommendations)
- `/app/data/ouroboros_reports/report_YYYY-MM-DD.json` (daily report)

**Dependencies:** WAL events must exist in `/app/events/`

**Common failures:**
- No WAL events for today (no trades occurred) -- produces empty report, safe
- WAL file locked by engine -- retries internally
- OOM kill if WAL is very large (>500MB) -- see Troubleshooting section

---

### config_writer -- Dynamic Weights to Engine

**What it does:** Reads nightly_v6 recommendations and WAL data, writes TOML config files that the Rust engine loads at boot or via SIGHUP.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.config_writer
```

**Output:**
- `/app/config/dynamic_weights.toml` (Bayesian stats, exit params, regime, Kelly)
- `/app/config/spread_cache.toml` (5-day median intraday spreads)
- `/app/config/universe_classification.toml` (tiered ticker IDs)

**Dependencies:** nightly_v6 should run first (uses `ouroboros_recommendations.json`). Falls back to safe defaults if missing.

**Common failures:**
- Missing recommendations JSON -- writes sensible defaults (non-fatal)
- Disk full -- atomic write fails, previous TOML preserved
- After manual run, send SIGHUP to engine: `docker exec aegis-v2 kill -HUP 1`

---

### ticker_selector -- Universe Scoring

**What it does:** 4-tier scoring system for 36K+ tickers. Tier 1 (HOT, 200 tickers, real-time), Tier 2 (WARM, 800, daily), Tier 3 (APEX, 2000, weekly), Tier 4 (COLD, 30K+, static). Only Tier 1+2 fetch live yfinance data.

```bash
# Full daily re-score
docker exec aegis-v2 python3 -m python_brain.ouroboros.ticker_selector

# Check output
docker exec aegis-v2 cat /app/config/active_watchlist.json | python3 -m json.tool | head -50
```

**Output:**
- `/app/config/active_watchlist.json` (all tiers with metadata)
- `/app/data/ouroboros_reports/watchlist_YYYY-MM-DD.json` (daily report)
- `/app/data/universe_cache/price_cache.json` (weekly Tier 3 cache)

**Dependencies:** universe_refresh should run first for fresh universe data. Reads `contracts.toml` and `isa_universe_master.json`.

**Common failures:**
- yfinance rate limiting (HTTP 429) -- batched internally with backoff
- Missing `isa_universe_master.json` -- falls back to contracts.toml tickers only
- Stale price cache -- Tier 3 scores may be inaccurate; re-run universe_refresh

---

### backfill_simulator -- 7-Day Simulation

**What it does:** Pulls 7 days of 5-min historical data via yfinance for the 12 primary ISA ETPs. Simulates trades using the same indicators as the live engine and generates performance report.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.backfill_simulator
```

**Output:**
- `/app/data/ouroboros_reports/backfill_YYYY-MM-DD.json`

**Dependencies:** None (fetches its own data via yfinance).

**Common failures:**
- yfinance returns empty data for weekends/holidays -- produces empty report
- numpy memory issues on large datasets -- rare on 7-day window

---

### ibkr_scanner -- IBKR Contract Discovery

**What it does:** Connects to IB Gateway (client_id=102) and scans all ISA-eligible exchanges for available contracts. Weekly deep scan. Updates master universe.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.ibkr_scanner
```

**Output:**
- `/app/config/isa_universe_master.json` (updated with new discoveries)
- `/app/data/ouroboros_reports/ibkr_scan_YYYY-MM-DD.json`

**Dependencies:** IB Gateway must be connected and accepting API connections on port 4003.

**Common failures:**
- IB Gateway disconnected (2FA expired) -- graceful retry then abort
- IBKR rate limit (50 msg/s) -- built-in 25ms throttle, 10s batch sleep
- Scan exceeds 60-min timeout -- partial results saved, continues next week
- Client_id conflict -- ensure no other client_id=102 is connected

---

### sheets_sync -- Google Sheets Dashboard

**What it does:** Pops events from Redis `sheets:queue` list and batch-writes to Google Sheets ("AEGIS V2 Dashboard"). Tabs: Live_Trades, Daily_Summary, Open_Positions, Ouroboros_Changes, System_Health.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.sheets_sync
```

**Output:** Rows appended to Google Sheets spreadsheet.

**Dependencies:**
- Redis must be running with events in `sheets:queue`
- `/app/config/sheets_service_account.json` must exist
- Spreadsheet must be shared with the service account email

**Common failures:**
- Google API quota exceeded (60 writes/min) -- batches internally, retries
- Service account JSON missing -- logs error, no crash
- Redis connection refused -- retries with backoff
- SHA256 dedup prevents duplicate rows (last 10,000 hashes)

---

### meta_label_optimizer -- Threshold Tuning

**What it does:** Computes F1-optimal probability threshold per ticker using precision-recall curve analysis. Replaces flat 0.55 threshold. Needs 20+ trades per ticker.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.meta_label_optimizer
```

**Output:**
- `/app/config/meta_label_thresholds.toml`

**Dependencies:** WAL with sufficient PositionClosed + SignalRejected events (30-day lookback).

**Common failures:**
- Fewer than 20 trades for a ticker -- falls back to 0.55 default
- No WAL data -- writes all-default thresholds

---

### maintenance -- Cleanup Utilities

**What it does:** Parquet orphan cleanup (removes intermediate Polars files that fill disk), persistent memory read-back, BST/DST awareness check.

```bash
# Cleanup orphan parquet files
docker exec aegis-v2 python3 -m python_brain.ouroboros.maintenance --cleanup

# Read persistent memory summary
docker exec aegis-v2 python3 -m python_brain.ouroboros.maintenance --read-memory

# Check BST/DST schedule offsets
docker exec aegis-v2 python3 -m python_brain.ouroboros.maintenance --bst-check
```

**Output:** Frees disk space; no config changes.

**Dependencies:** None.

**Common failures:** Permission errors on Docker volumes (rare).

---

### ouroboros_monitor -- Health Check

**What it does:** Checks all TOML config files for validity and freshness. Alerts via Telegram if any file is stale (>36h), corrupt, or missing. Detects parameter drift anomalies.

```bash
# Full check with Telegram alert
docker exec aegis-v2 python3 -m python_brain.ouroboros.ouroboros_monitor --all --send-telegram

# TOML health only
docker exec aegis-v2 python3 -m python_brain.ouroboros.ouroboros_monitor --check-toml

# Staleness only
docker exec aegis-v2 python3 -m python_brain.ouroboros.ouroboros_monitor --check-staleness
```

**Output:** Telegram alerts on failures; stdout summary.

**Dependencies:** TOML files must exist in `/app/config/`. Best run 20+ minutes after config_writer.

**Common failures:** False positive staleness on weekends (no nightly run Sat/Sun).

---

### win_loss_delta -- W/L Indicator Analysis

**What it does:** Compares indicator distributions (RVOL, Hurst, ADX, ATR%, spread, confidence) between winning and losing trades. Identifies which indicators have strongest predictive power.

```bash
# Analyze and print
docker exec aegis-v2 python3 -m python_brain.ouroboros.win_loss_delta

# Push to Google Sheets
docker exec aegis-v2 python3 -m python_brain.ouroboros.win_loss_delta --push-sheets

# Custom lookback
docker exec aegis-v2 python3 -m python_brain.ouroboros.win_loss_delta --days 60
```

**Output:**
- `/app/data/ouroboros_reports/win_loss_delta_YYYY-MM-DD.json`
- Google Sheets `Win_Loss_Delta` tab (with `--push-sheets`)

**Dependencies:** WAL with 30+ closed trades for statistical validity.

**Common failures:** Too few trades -- report generated but flagged as low-confidence.

---

### fill_quality -- Fill Analysis

**What it does:** Models expected slippage from paper to live trading. Estimates spread cost, market impact, latency slippage, and partial fills. Produces a "realism discount" for paper PnL.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.fill_quality
docker exec aegis-v2 python3 -m python_brain.ouroboros.fill_quality --days 30
```

**Output:**
- `/app/data/ouroboros_reports/fill_quality_YYYY-MM-DD.json`

**Dependencies:** WAL with PositionClosed events.

**Common failures:** Empty WAL -- produces zero-slippage default report.

---

### post_trade_diagnostics -- Post-Trade Review

**What it does:** Consolidated diagnostic suite covering implementation shortfall, MAE/MFE analysis, Sortino/Calmar ratios, session quality by 30-min bucket, drawdown velocity, IC decay, config checksum echo, signal tradeability, and tick filter validation.

```bash
# Full report
docker exec aegis-v2 python3 -m python_brain.ouroboros.post_trade_diagnostics

# Specific module
docker exec aegis-v2 python3 -m python_brain.ouroboros.post_trade_diagnostics --module N10x

# Custom lookback
docker exec aegis-v2 python3 -m python_brain.ouroboros.post_trade_diagnostics --days 60
```

**Output:**
- `/app/data/ouroboros_reports/post_trade_diagnostics_YYYY-MM-DD.json`

**Dependencies:** 100+ trades for statistical validity (runs with fewer but flags results).

**Common failures:** numpy division warnings with sparse data -- non-fatal.

---

### universe_filters -- ISA Eligibility & Execution

**What it does:** Spread-to-ATR hard filter for universe scanning, TWAP/VWAP-weighted execution slicing (Almgren-Chriss 2000), Thompson Sampling reward model, atomic JSON writes.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.universe_filters
```

**Output:** Filter results used by ticker_selector; no standalone file output.

**Dependencies:** None (library module, rarely run standalone).

---

### signal_filters -- Pre-Trade Signal Checks

**What it does:** Computes parameters for config_writer to emit: CUSUM dynamic mean (EWMA), VPIN bucket reset, Half-Kelly (until 250 trades), meta-labeler minimum sample gate.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.signal_filters
```

**Output:** Parameters consumed by config_writer (not standalone file output).

**Dependencies:** WAL data for trade count determination.

---

### config_fixes -- FTT, Symbology, DST

**What it does:** Writes `transaction_tax_registry.toml` (French/Italian FTT rates), shadow book alert threshold, Polygon-to-IBKR reverse symbology, ASX DST detection.

```bash
# Write FTT registry
docker exec aegis-v2 python3 -m python_brain.ouroboros.config_fixes --write-ftt
```

**Output:**
- `/app/config/transaction_tax_registry.toml`

**Dependencies:** None.

**Common failures:** None typical -- deterministic output.

---

### symbology_mapper -- Ticker Format Conversion

**What it does:** Converts between IBKR (NVD3), Polygon (LSE:NVD3), and yfinance (NVD3.L) ticker formats. Maintains symbology cache.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.symbology_mapper
docker exec aegis-v2 python3 -m python_brain.ouroboros.symbology_mapper --test
```

**Output:**
- `/app/config/symbology_cache.json`

**Dependencies:** None.

---

### universe_refresh -- Daily Universe Validation

**What it does:** Validates a rotating subset of 500 tickers per day from the 36K+ master universe via yfinance. Detects new leveraged ETPs. Full universe covered in ~72 days.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.universe_refresh
```

**Output:**
- `/app/config/isa_universe_master.json` (updated)
- `/app/config/initial_universe.toml` (if new leveraged ETPs found)

**Dependencies:** yfinance network access.

**Common failures:**
- yfinance rate limiting -- internal retry with backoff
- Delisted tickers return empty -- handled gracefully, marked as inactive

---

### contract_expander -- Auto-Grow Contracts

**What it does:** Finds high-scoring tickers from active_watchlist.json that lack contract definitions in contracts.toml, validates via yfinance, appends new entries, sends SIGHUP to engine for hot-reload.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.contract_expander
```

**Output:**
- Appends to `/app/config/contracts.toml`
- Sends SIGHUP to engine PID for hot-reload

**Dependencies:** ticker_selector must have run (needs `active_watchlist.json`).

**Common failures:**
- yfinance validation failure for new ticker -- skipped, logged
- contracts.toml write permission -- rare (volume-mounted)
- Engine PID not found for SIGHUP -- non-fatal, engine picks up on next restart

---

### session_pdf -- Session Briefing PDFs

**What it does:** Generates pre-session PDF report with active tickers, exchange breakdown, top opportunities, risk parameters. Sends via Telegram.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.session_pdf --session asian --send-telegram
docker exec aegis-v2 python3 -m python_brain.ouroboros.session_pdf --session european --send-telegram
docker exec aegis-v2 python3 -m python_brain.ouroboros.session_pdf --session american --send-telegram
docker exec aegis-v2 python3 -m python_brain.ouroboros.session_pdf --session us_only --send-telegram
```

**Output:**
- PDF file in `/app/data/ouroboros_reports/`
- Telegram document (with `--send-telegram`)

**Dependencies:** active_watchlist.json (from ticker_selector).

---

### daily_sim_report -- Daily Trade Report PDF

**What it does:** Reads WAL for today's entries/exits and telemetry snapshot. Generates landscape-A4 PDF with trade details, equity curve, session stats.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.daily_sim_report --send-telegram
```

**Output:**
- PDF in `/app/data/ouroboros_reports/`
- Telegram document

**Dependencies:** WAL events, telemetry_snapshot.json.

---

### cost_drag_report -- Friction Analysis

**What it does:** Computes spread cost, commission drag, and friction-adjusted PnL per trade/ticker/session/regime. Alerts if friction exceeds 1%.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.cost_drag_report --send-telegram
docker exec aegis-v2 python3 -m python_brain.ouroboros.cost_drag_report --days 7
```

**Output:**
- `/app/data/ouroboros_reports/cost_drag_YYYY-MM-DD.json`
- Telegram summary

**Dependencies:** WAL with closed trades.

---

### claude_review -- AI Nightly Review

**What it does:** Calls the Anthropic Claude API with trade data from the ResearchContextStore. Produces structured JSON review with per-trade narrative classification and recommended actions.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.claude_review --send-telegram
docker exec aegis-v2 python3 -m python_brain.ouroboros.claude_review --dry-run
docker exec aegis-v2 python3 -m python_brain.ouroboros.claude_review --date 2026-03-20
```

**Output:**
- `/app/data/ouroboros_reviews/review_YYYY-MM-DD.json`
- Telegram summary

**Dependencies:** ANTHROPIC_API_KEY env var, nightly_v6 + config_writer output.

**Common failures:**
- API key missing or invalid -- logs error, no crash
- API rate limit -- built-in retry
- Large context exceeding token limit -- truncated automatically

---

### claude_briefing -- Morning Briefing

**What it does:** Generates human-readable morning briefing using claude_review output + system telemetry. Sent before European session open.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.claude_briefing --send-telegram
docker exec aegis-v2 python3 -m python_brain.ouroboros.claude_briefing --dry-run
```

**Output:**
- `/app/data/morning_briefings/briefing_YYYY-MM-DD.md`
- Telegram message

**Dependencies:** claude_review output (`/app/data/ouroboros_reviews/review_YYYY-MM-DD.json`).

---

### fx_refresh -- FX Rate Update

**What it does:** Fetches live FX rates (EURGBP, USDGBP, CHFGBP, JPYGBP, HKDGBP, AUDGBP, etc.) from yfinance and writes fx_rates.toml. Falls back to hardcoded rates on failure.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.fx_refresh
```

**Output:**
- `/app/config/fx_rates.toml`

**Dependencies:** yfinance network access.

**Common failures:** yfinance down -- hardcoded fallback rates used (never leaves engine without rates).

---

### external_monitor -- Deep Health Check

**What it does:** Checks engine process, disk space, IBKR connection, trade flow, cron health, Redis connectivity, bridge subprocess. Graduated Telegram alerts (INFO/WARNING/CRITICAL).

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.external_monitor
docker exec aegis-v2 python3 -m python_brain.ouroboros.external_monitor --daily
docker exec aegis-v2 python3 -m python_brain.ouroboros.external_monitor --json
```

**Output:**
- `/app/data/monitor_status.json`
- Telegram alerts on failures

**Dependencies:** None (reads system state).

---

### bridge_health -- Bridge Monitor

**What it does:** Reads bridge health status from `/app/data/bridge_health.json` (written by Rust engine). Sends Telegram alert if bridge is unhealthy (stale >5 min, errors, signal drought).

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.bridge_health
```

**Output:** Telegram alert on unhealthy state. 30-minute cooldown between alerts.

**Dependencies:** Engine must be writing `bridge_health.json`.

---

### log_rotate -- Log Rotation

**What it does:** Archives /var/log/*.log to /var/log/archive/, keeps 7 days, truncates files >50MB immediately.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.log_rotate
```

**Output:** Archived logs in `/var/log/archive/`.

---

### telegram_notify -- Telegram Alerts

**What it does:** Lightweight notification sender. Used as library by other modules. CLI for testing and heartbeats.

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.telegram_notify --test
docker exec aegis-v2 python3 -m python_brain.ouroboros.telegram_notify --heartbeat
```

**Dependencies:** TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars.

---

### kill_switch -- Remote Control

**What it does:** Telegram bot listener for /kill, /pause, /resume, /status commands. Also provides CLI for SSH-based control.

```bash
# CLI commands (from SSH)
docker exec aegis-v2 python3 -m python_brain.ouroboros.kill_switch --kill
docker exec aegis-v2 python3 -m python_brain.ouroboros.kill_switch --pause
docker exec aegis-v2 python3 -m python_brain.ouroboros.kill_switch --resume
docker exec aegis-v2 python3 -m python_brain.ouroboros.kill_switch --status
```

**Output:**
- Creates `/app/data/KILL` or `/app/data/PAUSE` files
- Audit log: `/app/data/kill_switch_audit.ndjson`

**Safety:** /kill requires two-step confirmation (within 30s). Chat ID validated.

---

### persistent_memory -- System Knowledge

**What it does:** Cumulative system state: trade stats, per-ticker performance, per-regime analysis, parameter history, lessons learned. Read at boot via entrypoint.sh.

```bash
# Print memory summary
docker exec aegis-v2 python3 -m python_brain.ouroboros.persistent_memory

# View raw file
docker exec aegis-v2 cat /app/data/system_memory.json | python3 -m json.tool | head -80
```

**Output:**
- `/app/data/system_memory.json`

---

### wal_watcher -- Event Streamer (daemon)

**What it does:** Tails WAL ndjson files and sends Telegram alerts for fills, exits, regime changes, and system alerts. Also pushes to Redis for sheets_sync.

```bash
# Normally runs as daemon via entrypoint.sh. Manual run:
docker exec aegis-v2 python3 -m python_brain.ouroboros.wal_watcher --wal-dir /app/events --config-dir /app/config
```

**Output:** Telegram messages, Redis `sheets:queue` entries.

**Note:** Runs continuously. Do not run manually unless the daemon died.

---

### bridge_watchdog -- SPOF Monitor (daemon)

**What it does:** Monitors Python bridge subprocess heartbeat, process liveness, output freshness. Auto-restarts with exponential backoff. Max 3 restarts/hour.

```bash
# Check status
docker exec aegis-v2 python3 -m python_brain.ouroboros.bridge_watchdog --status

# Normally runs as daemon. Manual run:
docker exec aegis-v2 python3 -m python_brain.ouroboros.bridge_watchdog --monitor
```

---

## 4. Troubleshooting

### Engine not connecting to IB Gateway

```bash
# Check IB Gateway is running
docker ps | grep ib-gateway

# Check IB Gateway health
docker exec aegis-ib-gateway bash -c 'echo > /dev/tcp/localhost/4003'

# Check IB Gateway logs for 2FA issues
docker logs aegis-ib-gateway --tail 50

# Test connectivity from engine container
docker exec aegis-v2 bash -c 'echo > /dev/tcp/ib-gateway/4003 && echo "OK" || echo "FAIL"'

# Common fix: IB Gateway needs weekly 2FA re-auth on Monday mornings
# Restart IB Gateway after approving 2FA on phone:
cd /home/ubuntu/nzt48-aegis-v2 && docker compose restart ib-gateway

# Wait 15s for secdef farm before engine connects
docker compose restart aegis-v2
```

### OOM Kills

```bash
# Check if container was OOM killed
docker inspect aegis-v2 --format='{{.State.OOMKilled}}'

# Check memory usage
docker stats --no-stream

# Memory limits: aegis-v2=1024M, ib-gateway=1024M, redis=512M
# Total EC2: 4GB. ~1.5GB for OS.

# Diagnose: which process is eating memory?
docker exec aegis-v2 ps aux --sort=-%mem | head -10

# Prevention:
# - POLARS_MAX_THREADS=1 (already set) prevents Polars thread explosion
# - Ensure log_rotate runs (04:45) to keep /var/log small
# - Run maintenance --cleanup to remove orphan parquet files
docker exec aegis-v2 python3 -m python_brain.ouroboros.maintenance --cleanup
```

### Redis Connection Failures

```bash
# Check Redis is healthy
docker exec aegis-redis redis-cli -a nzt48redis ping
# Expected: PONG

# Check Redis memory usage
docker exec aegis-redis redis-cli -a nzt48redis info memory | grep used_memory_human

# Check sheets queue depth
docker exec aegis-redis redis-cli -a nzt48redis llen sheets:queue

# Flush stale queue (if queue is massive and blocking)
docker exec aegis-redis redis-cli -a nzt48redis del sheets:queue

# Redis maxmemory policy is noeviction -- if full, writes fail
# Check: docker exec aegis-redis redis-cli -a nzt48redis info memory | grep maxmemory
```

### Google Sheets API Quota Exceeded

```bash
# Check sheets_sync log for quota errors
docker exec aegis-v2 tail -50 /var/log/sheets_sync.log | grep -i "quota\|429\|rate"

# Google Sheets API limit: 60 writes/min, 300 reads/min
# sheets_sync batches up to 50 rows per append_rows call

# Fix: wait 60 seconds and re-run
docker exec aegis-v2 python3 -m python_brain.ouroboros.sheets_sync

# If persistent: check service account is not rate-limited
# Verify: /app/config/sheets_service_account.json exists and is valid
docker exec aegis-v2 python3 -c "import json; json.load(open('/app/config/sheets_service_account.json'))"
```

### yfinance Rate Limiting

```bash
# Check ticker_selector or universe_refresh logs
docker exec aegis-v2 tail -50 /var/log/ticker_selector.log | grep -i "429\|rate\|throttle"

# yfinance uses Yahoo Finance (no API key needed) but rate-limits aggressively
# ticker_selector batches requests and uses price_cache.json for Tier 3

# Fix: wait 5-10 minutes and re-run
# Or clear the price cache to force fresh fetch:
docker exec aegis-v2 rm -f /app/data/universe_cache/price_cache.json
```

### WAL Corruption Recovery

```bash
# Check WAL file integrity
docker exec aegis-v2 wc -l /app/events/current.ndjson
docker exec aegis-v2 tail -5 /app/events/current.ndjson | python3 -m json.tool

# Check for truncated last line (common after OOM/crash)
docker exec aegis-v2 python3 -c "
import json
bad = 0
with open('/app/events/current.ndjson') as f:
    for i, line in enumerate(f, 1):
        try:
            json.loads(line)
        except:
            bad += 1
            print(f'Line {i}: CORRUPT')
print(f'Total corrupt lines: {bad}')
"

# Fix truncated last line
docker exec aegis-v2 python3 -c "
lines = open('/app/events/current.ndjson').readlines()
import json
good = [l for l in lines if l.strip()]
# Test last line
try:
    json.loads(good[-1])
except:
    print('Removing corrupt last line')
    good = good[:-1]
with open('/app/events/current.ndjson', 'w') as f:
    f.writelines(good)
"

# WAL archives: engine rotates current.ndjson on restart
# Archives live at /app/events/archive/*.ndjson
docker exec aegis-v2 ls -lht /app/events/archive/ | head -10
```

### Stale Config (engine not picking up new weights)

```bash
# Check dynamic_weights.toml age
docker exec aegis-v2 stat /app/config/dynamic_weights.toml | grep Modify

# Force config_writer re-run
docker exec aegis-v2 python3 -m python_brain.ouroboros.config_writer

# Send SIGHUP to engine to hot-reload config
docker exec aegis-v2 kill -HUP 1

# Verify engine loaded new config (check engine logs)
docker logs aegis-v2 --tail 20 | grep -i "config\|reload\|SIGHUP"

# Nuclear option: full restart
cd /home/ubuntu/nzt48-aegis-v2 && docker compose restart aegis-v2
```

---

## 5. Emergency Procedures

### Kill All Positions

```bash
# Via Telegram: send /kill to the bot (requires confirmation within 30s)

# Via SSH (immediate):
docker exec aegis-v2 python3 -m python_brain.ouroboros.kill_switch --kill

# What it does: creates /app/data/KILL file
# Engine checks every 1s, flattens all positions, then halts

# Verify:
docker exec aegis-v2 ls -la /app/data/KILL
docker exec aegis-v2 cat /app/data/kill_switch_audit.ndjson | tail -5
```

### Pause Trading

```bash
# Via Telegram: send /pause to the bot

# Via SSH:
docker exec aegis-v2 python3 -m python_brain.ouroboros.kill_switch --pause

# Creates /app/data/PAUSE -- engine stops opening new positions but manages existing ones

# Resume:
docker exec aegis-v2 python3 -m python_brain.ouroboros.kill_switch --resume
# Removes /app/data/PAUSE file
```

**Note:** IS_LIVE=false is already set in docker-compose.yml. The engine is in simulation mode and never submits real orders to IBKR. The kill/pause mechanism affects simulated position management.

### Force Restart Cycle

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22

cd /home/ubuntu/nzt48-aegis-v2

# Clean stop (waits up to 60s for graceful shutdown)
docker compose down

# Clear stale control files
docker exec aegis-v2 rm -f /app/data/KILL /app/data/PAUSE 2>/dev/null || true

# Clean restart
docker compose up -d

# Verify all 3 containers are running
docker ps --format "table {{.Names}}\t{{.Status}}"

# Watch engine startup
docker logs aegis-v2 --tail 30 -f
```

### Rollback to Previous Config

```bash
# Config files are in a host-mounted volume: ./config:/app/config
# Git tracks config changes

cd /home/ubuntu/nzt48-aegis-v2

# See recent config changes
git log --oneline -10 -- config/

# Restore specific file to previous version
git checkout HEAD~1 -- config/dynamic_weights.toml

# Or manually copy a known-good backup
# config_writer writes atomically (.tmp then rename) so partial writes are rare

# After rollback, reload engine
docker exec aegis-v2 kill -HUP 1
```

### WAL Backup and Restore

```bash
# Backup current WAL
docker exec aegis-v2 cp /app/events/current.ndjson /app/events/backup_$(date +%Y%m%d_%H%M%S).ndjson

# Copy WAL from container to host
docker cp aegis-v2:/app/events/current.ndjson /home/ubuntu/wal_backup/

# Copy all WAL files including archives
docker cp aegis-v2:/app/events/ /home/ubuntu/wal_backup_full/

# Restore WAL (after replacing container)
docker cp /home/ubuntu/wal_backup/current.ndjson aegis-v2:/app/events/current.ndjson
docker compose restart aegis-v2
```

---

## 6. Health Checks

### Verify Engine is Running

```bash
# Quick check
docker ps | grep aegis-v2

# Healthcheck status (uses pgrep aegis)
docker inspect aegis-v2 --format='{{.State.Health.Status}}'
# Expected: healthy

# Check engine is processing ticks
docker logs aegis-v2 --tail 5 | grep -i "tick\|signal\|position"

# Check telemetry freshness
docker exec aegis-v2 stat /app/events/telemetry_snapshot.json 2>/dev/null | grep Modify
```

### Verify IB Gateway is Connected

```bash
# Container health
docker inspect aegis-ib-gateway --format='{{.State.Health.Status}}'
# Expected: healthy

# Port check
docker exec aegis-ib-gateway bash -c 'echo > /dev/tcp/localhost/4003 && echo "connected"'

# Check for authentication errors
docker logs aegis-ib-gateway --tail 30 | grep -i "auth\|2fa\|login\|connect"

# IBKR client connections
docker logs aegis-v2 --tail 30 | grep -i "ibkr\|gateway\|connect\|disconnect"
```

### Verify Redis is Healthy

```bash
# Ping
docker exec aegis-redis redis-cli -a nzt48redis ping
# Expected: PONG

# Memory
docker exec aegis-redis redis-cli -a nzt48redis info memory | grep used_memory_human

# Queue depth
docker exec aegis-redis redis-cli -a nzt48redis llen sheets:queue

# Persistence (AOF)
docker exec aegis-redis redis-cli -a nzt48redis info persistence | grep aof_enabled
# Expected: aof_enabled:1

# Key count
docker exec aegis-redis redis-cli -a nzt48redis dbsize
```

### Check Disk Space

```bash
# Host disk
df -h /

# Docker disk usage
docker system df

# Biggest offenders
du -sh /home/ubuntu/nzt48-aegis-v2/

# Docker volume sizes
docker system df -v | grep aegis

# If disk >80%, clean up:
docker system prune -f
docker exec aegis-v2 python3 -m python_brain.ouroboros.maintenance --cleanup
```

### Verify Cron is Running

```bash
# Check supercronic process
docker exec aegis-v2 pgrep supercronic
# Should return a PID

# Check recent cron activity
docker exec aegis-v2 ls -lt /var/log/*.log | head -15
# Files should have recent modification times

# Check specific job ran today
docker exec aegis-v2 tail -5 /var/log/ouroboros.log
docker exec aegis-v2 tail -5 /var/log/config_writer.log

# If supercronic died, restart the container
cd /home/ubuntu/nzt48-aegis-v2 && docker compose restart aegis-v2
```

### Full System Health (One-Liner)

```bash
docker exec aegis-v2 python3 -m python_brain.ouroboros.external_monitor --json
```

This produces a JSON object with status for every subsystem. Use `--daily` to send the full report via Telegram.

---

## Appendix: File Locations Quick Reference

| File                                      | Location (inside container)                    | Written By         |
|-------------------------------------------|------------------------------------------------|--------------------|
| Dynamic weights                           | /app/config/dynamic_weights.toml               | config_writer      |
| Spread cache                              | /app/config/spread_cache.toml                  | config_writer      |
| Universe classification                   | /app/config/universe_classification.toml       | config_writer      |
| Active watchlist                          | /app/config/active_watchlist.json               | ticker_selector    |
| Master universe                           | /app/config/isa_universe_master.json            | universe_refresh   |
| Contracts                                 | /app/config/contracts.toml                      | contract_expander  |
| FX rates                                  | /app/config/fx_rates.toml                       | fx_refresh         |
| FTT registry                              | /app/config/transaction_tax_registry.toml       | config_fixes       |
| Meta-label thresholds                     | /app/config/meta_label_thresholds.toml          | meta_label_optimizer|
| Symbology cache                           | /app/config/symbology_cache.json                | symbology_mapper   |
| Sheets service account                    | /app/config/sheets_service_account.json         | manual (secret)    |
| Nightly recommendations                   | /app/data/ouroboros_recommendations.json         | nightly_v6         |
| System memory                             | /app/data/system_memory.json                    | persistent_memory  |
| Monitor status                            | /app/data/monitor_status.json                   | external_monitor   |
| Bridge health                             | /app/data/bridge_health.json                    | Rust engine        |
| Kill switch audit                         | /app/data/kill_switch_audit.ndjson              | kill_switch        |
| Gate vetoes                               | /app/data/gate_vetoes.ndjson                    | engine (bridge.py) |
| Daily reports                             | /app/data/ouroboros_reports/report_YYYY-MM-DD.json | nightly_v6      |
| Claude reviews                            | /app/data/ouroboros_reviews/review_YYYY-MM-DD.json | claude_review   |
| Morning briefings                         | /app/data/morning_briefings/briefing_YYYY-MM-DD.md | claude_briefing |
| WAL (current)                             | /app/events/current.ndjson                      | Rust engine        |
| WAL (archives)                            | /app/events/archive/*.ndjson                    | Rust engine        |
| Telemetry snapshot                        | /app/events/telemetry_snapshot.json             | Rust engine        |
| Price cache (Tier 3)                      | /app/data/universe_cache/price_cache.json       | ticker_selector    |
