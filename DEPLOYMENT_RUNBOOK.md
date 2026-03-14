# NZT-48 Institutional Deployment & Operations Runbook
**Version**: 2.0 | **Date**: 2026-02-28

> This runbook exists because a series of critical errors were discovered where the core
> strategy (S15) was not running in production, the wrong instruments were being scanned, and
> monitoring was broken. Every procedure here has a direct root cause in a real production failure.
> **Follow every step. Do not skip.**

---

## PART 1 — NEVER-FAIL CHECKLIST (run after EVERY change to production)

### The 5-Minute Post-Deploy Verification Protocol

After any `docker cp`, `docker restart`, file patch, or config change:

```bash
# 1. Health check — must show engine:ok
curl -s http://localhost:8000/api/health | python3 -m json.tool
# PASS: {"engine": "ok", ...}
# FAIL: {"engine": "stale"} → engine didn't start, check docker logs nzt48 --tail 50

# 2. Strategy verification — S15 must be in the list
docker exec nzt48 python3 -c "
import config as cfg, importlib
mod = importlib.import_module('strategies.daily_target')
cls = getattr(mod, 'DailyTargetStrategy')
s = cls()
print('S15 OK:', s.strategy_id, s.name)
"
# PASS: S15 OK: S15 2% Daily Target

# 3. ISA tickers in universe — QQQ3.L must appear
docker exec nzt48 python3 -c "
import config as cfg
tickers = cfg.get_tickers()
print('get_tickers():', tickers[:5])
print('QQQ3.L in defaults:', 'QQQ3.L' in tickers)
"
# NOTE: QQQ3.L may not be in defaults (bot_b_universe = US equities)
# But run_scan() prepends _ISA_ETPS — verify with log check below

# 4. Approved params loaded
docker exec nzt48 grep -a 'approved params\|S15 loaded\|S15 using' /app/data/engine_err.log | tail -3
# PASS: S15 loaded approved params: RVOL≥0.60 ADX≥15.0 ...

# 5. Container restart policy
docker inspect nzt48 --format '{{.HostConfig.RestartPolicy}}'
# PASS: {always 0}
```

---

## PART 2 — CANONICAL FILE LOCATIONS

### The Golden Rule: Container vs Local vs EC2

```
LOCAL (dev machine):      /Users/rr/nzt48-signals/         ← where you write code
EC2 (server):             /home/ubuntu/nzt48-signals/       ← rsync destination
EC2 (working patch):      /home/ubuntu/main_patched.py      ← the patched container main.py
CONTAINER:                /app/                              ← what actually runs
```

**CRITICAL**: Local `main.py` may import modules not in the container image.
- NEVER do `docker cp main.py nzt48:/app/main.py` using the local file.
- ALL changes to main.py must go through `/home/ubuntu/main_patched.py`.
- To update: extract → patch → cp → restart (see Part 4).

### File → Container Mapping

| Local file | Container path | Deploy method |
|---|---|---|
| `strategies/daily_target.py` | `/app/strategies/daily_target.py` | `docker cp` direct |
| `strategies/*.py` | `/app/strategies/*.py` | `docker cp` direct |
| `main_patched.py` (EC2) | `/app/main.py` | `docker cp main_patched.py nzt48:/app/main.py` |
| `scripts/*.py` | `/app/scripts/*.py` | `docker cp` then run inside container |
| `config/settings.yaml` | `/app/config/settings.yaml` | `docker cp` direct |
| `data/*.json` | `/app/data/*.json` | `docker cp` direct |

---

## PART 3 — STANDARD SSH/DOCKER COMMANDS

```bash
# SSH to EC2
ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11

# rsync files to EC2 (always use -e flag with key)
rsync -az -e "ssh -i ~/.ssh/nzt48-key.pem" ./localfile.py ubuntu@54.242.32.11:/home/ubuntu/

# Container management
docker logs nzt48 --tail 50               # recent logs
docker logs nzt48 --tail 200 2>&1         # includes stderr
docker exec nzt48 tail -50 /app/data/engine_err.log   # engine log
docker stats nzt48 --no-stream             # CPU/mem snapshot
docker inspect nzt48 --format '{{.State.Status}}'     # running/exited/etc

# Restart (NOT docker-compose — use direct docker commands)
docker restart nzt48
docker stop nzt48 && docker start nzt48   # harder restart

# Copy files into running container
docker cp /path/to/file nzt48:/app/destination/

# Run command inside container
docker exec nzt48 python3 /app/scripts/backfill_extended.py

# Health check
curl -s http://localhost:8000/api/health
```

---

## PART 4 — HOW TO PATCH main.py CORRECTLY

**Background**: The container runs a `main.py` baked into the image weeks/months ago. Local
`main.py` has grown and imports modules not in the container. Direct deployment of local
`main.py` WILL crash the container.

### Safe Patching Procedure

```bash
# Step 1: Extract the current working main.py from the container
ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11 "
    docker exec nzt48 cat /app/main.py > /home/ubuntu/main_patched.py
    wc -l /home/ubuntu/main_patched.py
"

# Step 2: Write a patch script locally (string replacement approach)
cat > /tmp/my_patch.py << 'EOF'
import sys
with open('/home/ubuntu/main_patched.py', 'r') as f:
    content = f.read()

OLD = """exact string to find"""
NEW = """replacement string"""

if OLD not in content:
    print("ERROR: patch point not found"); sys.exit(1)
content = content.replace(OLD, NEW, 1)
print("Patch applied OK")

with open('/home/ubuntu/main_patched.py', 'w') as f:
    f.write(content)
print("Written")
EOF

# Step 3: Upload patch script to EC2
rsync -az -e "ssh -i ~/.ssh/nzt48-key.pem" /tmp/my_patch.py ubuntu@54.242.32.11:/home/ubuntu/

# Step 4: Run patch on EC2
ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11 "python3 /home/ubuntu/my_patch.py"

# Step 5: Deploy patched file to container
ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11 "
    docker cp /home/ubuntu/main_patched.py nzt48:/app/main.py
    docker restart nzt48
    sleep 12
    curl -s http://localhost:8000/api/health
"

# Step 6: Run the 5-minute verification protocol (Part 1)
```

---

## PART 5 — DEPLOYING A NEW STRATEGY

**Every new strategy must go through ALL of these steps.**

```bash
# Step 1: Copy strategy file to container
rsync -az -e "ssh -i ~/.ssh/nzt48-key.pem" strategies/my_strategy.py ubuntu@54.242.32.11:/home/ubuntu/
ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11 "docker cp /home/ubuntu/my_strategy.py nzt48:/app/strategies/"

# Step 2: Verify the file is in the container
ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11 "docker exec nzt48 ls /app/strategies/ | grep my_strategy"

# Step 3: Add to _init_strategies in main_patched.py
# Write and run a patch script (see Part 4) adding:
# ("strategies.my_strategy", "MyStrategyClass"),
# to the strategy_imports list in _init_strategies()

# Step 4: If strategy needs specific tickers not in bot_b_universe,
# add them to the ISA ETP section in run_scan() or add a separate block

# Step 5: Deploy main_patched.py and restart (see Part 4, steps 5-6)

# Step 6: VERIFICATION — check engine_err.log for "Strategy loaded: MyStrategyClass"
ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11 "
    docker exec nzt48 grep -a 'Strategy loaded\|produced' /app/data/engine_err.log | tail -20
"
# REQUIRED OUTPUT: INFO: Strategy loaded: MyStrategyClass
```

---

## PART 6 — MONITORING & ALERTING

### Real-time Health

```bash
# Quick health check
curl -s http://localhost:8000/api/health

# Expected healthy response:
# {"api":"ok","engine":"ok","engine_last_heartbeat":1772293651,...}

# If engine=stale:
# 1. Check docker logs nzt48 --tail 20 (is container running?)
# 2. Check docker exec nzt48 tail -20 /app/data/engine_err.log (startup error?)
# 3. Check watchdog log: tail -20 /home/ubuntu/watchdog.log
```

### Watchdog (runs every 5 minutes)

```bash
# View watchdog log
tail -50 /home/ubuntu/watchdog.log

# Watchdog checks (in order):
# 1. Container status (restarts if not running)
# 2. API responding at /api/health
# 3. Engine heartbeat age < 5 minutes
# 4. /app/data directory accessible
# 5. Memory < 85%
# Alerts via Telegram on failure, restarts container automatically
```

### Telegram Alerts

The engine sends Telegram alerts for:
- Startup/shutdown
- Trade signals
- Daily reset
- Weekend data collection summary
- Watchdog failures (container restart, stale engine, high memory)

---

## PART 7 — WEEKEND OPERATIONS

The system uses the weekend market-closed window for intelligence gathering.

**Automatic (every Sat+Sun 10:00 UK)**:
1. Extended 2-year backfill → seeds learning engine with historical S15 outcomes
2. 960-combo parameter sweep → finds optimal gate settings via Deflated Sharpe
3. 60-day walk-forward stress test → validates gate logic on recent data
4. Telegram summary of what changed

**On Monday morning**:
- `DailyTargetStrategy.__init__()` loads `data/approved_params.json`
- Gate constants automatically updated to weekend's optimal values
- Engine logs: `S15 loaded approved params: RVOL≥X.XX ADX≥YY...`

**Manual trigger** (run immediately):
```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11 "
docker exec nzt48 python3 /app/scripts/backfill_extended.py --years 2
docker exec nzt48 python3 /app/scripts/param_sweep.py --years 2 --top 10 --apply
docker exec nzt48 python3 /app/scripts/walkforward_stress.py --days 60
"
```

---

## PART 8 — IMAGE REBUILD CADENCE

The container runs a snapshot of the image. Over time, local code diverges.

**Rebuild schedule**: Monthly, or when any of these occur:
- New Python dependencies added to requirements.txt
- New top-level modules created (new directories alongside `strategies/`, `learning/`, etc.)
- Security patches needed

**Rebuild procedure**:
```bash
# On EC2
cd /home/ubuntu/nzt48-signals
git pull origin main  # sync all local code first

# Rebuild
docker build -t nzt48-nzt48:latest .
docker stop nzt48
docker run -d --name nzt48_new --restart=always [same args as original] nzt48-nzt48:latest
# Test for 10 minutes
# If OK: docker rm nzt48, docker rename nzt48_new nzt48
```

**After rebuild**: The patched `main_patched.py` becomes stale — re-extract from new image
and re-apply all patches (Part 4, Step 1 + all patch scripts).

---

## PART 9 — ERROR RESPONSE PLAYBOOK

### Scenario: Container exits / won't start

```bash
docker logs nzt48 --tail 50
# Look for: ModuleNotFoundError, ImportError, SyntaxError
# If ModuleNotFoundError: local code was deployed instead of patched version
# Fix: re-extract image's main.py, re-patch, re-deploy
```

### Scenario: Engine stale, API ok

```bash
docker exec nzt48 tail -30 /app/data/engine_err.log
# Look for: Exception in main loop, asyncio errors
# The background heartbeat loop should keep engine:ok — stale means the engine crashed
# Fix: docker restart nzt48 (supervisord will revive engine process)
```

### Scenario: S15 produces 0 signals consistently during market hours

```bash
# Check 1: Are ISA ETPs in the scan?
docker exec nzt48 grep -a 'STALE_BARS\|QQQ3\|ISA' /app/data/engine_err.log | tail -20

# Check 2: Are gate rejections logged?
docker exec nzt48 grep -a 'rvol_too_low\|adx_too_low\|not_lse_hours' /app/data/engine_err.log | tail -20

# Check 3: LSE hours gate (09:00–15:15 UK only)
# 0 signals is correct outside those hours
```

### Scenario: Watchdog keeps restarting container

```bash
tail -50 /home/ubuntu/watchdog.log
# If "Engine stale after 60s" repeatedly: engine crash loop
# Check engine_err.log for the crash reason before watchdog triggers
# A crash loop means an unhandled exception in start() or the scan loop
```

---

## PART 10 — KEY CONSTANTS REFERENCE

| Constant | Value | Location | Meaning |
|---|---|---|---|
| `_DAILY_TARGET_PCT` | 2.0% | daily_target.py | The non-negotiable target |
| `_MIN_RVOL` | 0.60* | daily_target.py | Loaded from approved_params.json |
| `_MIN_ADX` | 15.0* | daily_target.py | Loaded from approved_params.json |
| `_MIN_CONFIDENCE` | 70.0* | daily_target.py | Loaded from approved_params.json |
| `_MIN_INDICATOR_CONSENSUS` | 6* | daily_target.py | Loaded from approved_params.json |
| `_STOP_PCT_3X` | 1.0% | daily_target.py | Fixed stop for 3x ETPs |
| `_STOP_PCT_5X` | 0.75% | daily_target.py | Fixed stop for 5x ETPs |
| `_LSE_OPEN_HOUR` | 09:00 | daily_target.py | Strategy only fires during LSE |
| `_LSE_CLOSE_HOUR` | 15:15 | daily_target.py | Strategy only fires during LSE |
| Heartbeat TTL | 120s | dashboard/api.py | Engine declared stale after 120s |
| Heartbeat interval | 60s | main.py `_background_heartbeat` | Background loop fires every 60s |
| Watchdog interval | 5 min | crontab | System watchdog run frequency |
| Weekend collection | Sat+Sun 10:00 UK | main.py scheduler | Weekend intelligence refresh |

*\* These are defaults; actual values loaded from `data/approved_params.json` at startup.*

---

## PART 11 — INSTITUTIONAL ERROR PREVENTION RULES

These rules are ABSOLUTE. They exist because each one was violated at least once.

### Rule 1: Never Trust "It's Deployed" — Always Verify
After every deployment, run Part 1 verification. Check logs. Check engine status.
A "successful" `docker cp` or `docker restart` means nothing without log confirmation.

### Rule 2: The Container is the Ground Truth
Local code is development. The container is production. They can diverge.
When in doubt, `docker exec nzt48 cat /app/strategies/daily_target.py` to see what's actually running.

### Rule 3: New Strategies Require 3 Actions (not 1)
1. File deployed to container (`docker cp`)
2. Registration added to `_init_strategies` in main.py
3. Ticker universe includes the strategy's instruments
Verify ALL THREE before calling a strategy "live".

### Rule 4: Never Use Local main.py in Production
Local `main.py` can import modules the container doesn't have.
Always patch through `main_patched.py` (extracted from the container's own image).

### Rule 5: Settings.yaml is Config, Not Strategy Logic
The `bot_b_universe` in settings.yaml is for US equities (S1-S14).
S15 (ISA ETPs) overrides the universe in `run_scan()`. They are separate.
Changing `bot_b_universe` does NOT change what S15 scans.

### Rule 6: Sweep Results Must Feed Live Strategy
After every `param_sweep.py` run, `daily_target.py` picks up the new params on next restart.
Never manually edit gate constants in `daily_target.py` — let the sweep do it.

### Rule 7: Heartbeat ≠ Scan
The engine being "alive" and the engine being "scanning" are two different things.
`engine: ok` in health check means the process is running and the heartbeat loop fires.
It does NOT mean a scan is in progress or that signals are being generated.

### Rule 8: 0 Signals Outside Market Hours is CORRECT
S15 only fires during LSE hours (09:00–15:15 UK, Mon–Fri).
Outside those hours, `0 signals` in logs is correct behaviour, not a bug.

### Rule 9: Watchdog Restarts Are Alerts, Not Solutions
If the watchdog restarts the container 3+ times in an hour, something is broken.
Read `engine_err.log` to find the crash cause. Don't just let the watchdog loop.

### Rule 10: Paper Mode First, Always
System runs in `PAPER` mode. No real money is at risk.
When transitioning to live trading, a separate full audit of the execution layer is required.

---

*Last updated: 2026-02-28 after full system audit following S15 deployment failures.*
