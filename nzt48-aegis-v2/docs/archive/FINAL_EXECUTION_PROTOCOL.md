# FINAL EXECUTION PROTOCOL
## The Master Command is Ready. Here's Exactly How to Turn the Key.

**Status**: LOCKED FOR EXECUTION (2026-03-10)
**Safety Level**: Seventeenth-Order Audit Complete
**Readiness**: 100% (All 4 Fatal Traps Patched)

---

## PHASE 0: PRE-FLIGHT SETUP (30 Minutes)

### 1. Check Your Anthropic Workspace Budget

**URL**: https://console.anthropic.com/account/billing/overview

**What to Verify**:
- [ ] Workspace has minimum $100 pre-funded
- [ ] Auto-recharge enabled (if company policy allows)
- [ ] No monthly spending limits that might block Phase 1-4

**Why This Matters**:
- Phase 1 alone will consume $500-1,000 in API costs
- Total dev cost: $3,000-7,000 (NOT $0 as blueprint says)
- If tier limit is hit on Day 3, entire pipeline crashes

**Action**:
```
If insufficient balance:
  1. Add credit via Anthropic dashboard
  2. Request company IT approval for $10k spend limit
  3. Do NOT proceed without $5k+ available
```

---

### 2. Set Your Anthropic API Key

```bash
# In your terminal (on local machine or EC2):
export ANTHROPIC_API_KEY="sk-ant-..."

# Verify it's set:
echo $ANTHROPIC_API_KEY
# Should show: sk-ant-...

# Make it persistent (add to ~/.bashrc or ~/.zshrc):
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.bashrc
source ~/.bashrc
```

**Why This Matters**:
- Claude Code sessions CANNOT authenticate without this
- Ralph Wiggum loops (20 iterations) will fail silently if key is missing

---

### 3. Verify IB Gateway is Running & Authenticated

```bash
# Check if port 4001 is listening (IB Gateway TCP port)
nc -vz 127.0.0.1 4001

# Expected output:
# Connection to 127.0.0.1 4001 port [tcp/4001] succeeded!
```

**If port is NOT responding**:

```bash
# Start IB Gateway container
cd /Users/rr/nzt48-signals/nzt48-aegis-v2
docker-compose up -d ib-gateway

# Wait 30 seconds for Java to boot
sleep 30

# Try again
nc -vz 127.0.0.1 4001
```

**If still not responding**:

```
1. Open VNC to localhost:5900 (or your EC2 IP:5900)
2. Log in with IB credentials:
   - User: [REDACTED]
   - Password: [REDACTED]
3. IBC will show 2FA challenge
4. Approve the challenge
5. Gateway should now listen on port 4001
```

**Verify Connection Programmatically**:
```python
import socket

def is_port_open(host, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex((host, port))
    sock.close()
    return result == 0

if is_port_open('127.0.0.1', 4001):
    print("✓ IB Gateway is reachable on port 4001")
else:
    print("✗ IB Gateway is NOT reachable (Phase 0 Task 3 will fail)")
```

---

### 4. Set Your Polygon API Key

```bash
export POLYGON_API_KEY="[REDACTED - see .env]"

# Verify:
echo $POLYGON_API_KEY
```

---

### 5. Create a tmux Session (MANDATORY FOR 504-HOUR RUN)

```bash
# Create a new tmux session named 'aegis_master_build'
tmux new -s aegis_master_build

# You should see a new terminal with "[0] -bash" at the bottom
# Verify you're in tmux:
echo $TMUX
# Should output: /tmp/tmux-xxx/aegis_master_build,0,0

# Inside tmux, you can now safely run long commands
# If SSH drops, you can reconnect later: tmux attach -t aegis_master_build
```

**Why tmux is Critical**:
- Phase 1-4 will run for 4+ weeks continuously
- If your SSH connection drops (laptop sleep, network hiccup, etc.)
  - WITHOUT tmux: Entire pipeline crashes, all progress lost
  - WITH tmux: Session stays alive, reconnect with `tmux attach`, resume exactly where you left off

---

## PHASE 1: THE MASTER COMMAND EXECUTION

### Inside Your tmux Session, Execute:

```bash
# Step 1: Make sure you're inside tmux
tmux attach -t aegis_master_build
# (or if already inside, skip this)

# Step 2: Set environment variables
export POLYGON_API_KEY="[REDACTED - see .env]"
export ANTHROPIC_API_KEY="sk-ant-..."

# Step 3: Run the master command
bash /Users/rr/nzt48-signals/nzt48-aegis-v2/THE_MASTER_COMMAND.sh
```

**What Happens Next**:

1. **Seventeenth-Order Audit Checks** (1 min)
   - Verifies tmux session (you're protected)
   - Checks IB Gateway port (4001/4002 responding)
   - Checks Anthropic API key is set
   - Warns about API budget

2. **Pre-Flight Validation** (2 min)
   - Verifies POLYGON_API_KEY
   - Checks AEGIS_ROOT directory
   - Tests Python dependencies
   - Tests Polygon API connectivity
   - All checks must PASS or exit with error

3. **System Briefing Display** (5 min, you read)
   - Shows complete 15-week architecture
   - Lists all Phase 0-5 specifications
   - Shows data architecture (IBKR-primary)
   - Shows cost summary
   - Shows security protocols

4. **Approval Gate** (you answer yes/no)
   ```
   Ready to proceed with Phase 0 Bootstrap? [y/n]: y
   ```
   - Type `y` and press Enter to continue
   - Type `n` and press Enter to abort

5. **Phase 0 Bootstrap Executes** (~87 minutes, fully automated)
   ```
   Task 1: Dividend calendar (37.5 min)
   Task 2: Splits calendar (37.5 min)
   Task 3: IBKR LSE contract discovery (2 min)
   Task 4: GARCH calibration (8 min)
   Task 5: Validation (2 min)
   ```
   - Real-time progress updates
   - All output logged to TTY session file
   - All state saved to checkpoint.json (every 10 API calls)

6. **Phase 0 Complete** → Approval Gate for Phase 1
   ```
   Phase 0 COMPLETE ✓
   Ready to proceed to: PHASE 1 Refactoring
   Options:
     [c] Continue to PHASE 1
     [s] Skip to next phase
     [q] Quit execution

   Enter choice [c/s/q]: c
   ```

7. **Phase 1 Begins** (Days 2-8, ~7.3 hours interactive)
   - RM-1 through RM-5 (refactoring sessions)
   - Each has approval gate + cargo test validation
   - Each pauses for your explicit [c/s/q] choice
   - Total: 504 hours of coding across all phases

---

## MANAGING YOUR SESSION

### Detach from tmux (Keep It Running)

If you want to close your laptop or log off SSH:

```bash
# Press: Ctrl+B, then D
# You'll see: [detached from aegis_master_build]
# The master command keeps running in the background
```

### Reattach Later (Resume Where You Left Off)

```bash
# Log back into EC2
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22

# Reattach to your tmux session
tmux attach -t aegis_master_build

# The master command is still running (or paused at approval gate)
# All output is preserved in the TTY session file
```

### Check Session Status (While Detached)

```bash
# List all tmux sessions
tmux list-sessions

# Output example:
# aegis_master_build: 1 windows (created Mon Mar 10 09:00:00 2026)

# See what's running inside
tmux capture-pane -t aegis_master_build -p
# (shows last few lines of output)
```

### Kill Session (If You Need to Restart)

```bash
# Only do this if something went catastrophically wrong
# Normal pauses: Use [q] to quit gracefully
# Restart: Run master command again, it will resume from checkpoint.json

# Force kill (last resort):
tmux kill-session -t aegis_master_build
```

---

## MONITORING & BUDGET CONTROL

### Daily Budget Check

During Phase 1-4, check your Anthropic spend DAILY:

```
Visit: https://console.anthropic.com/account/usage
Check: Daily spend
Expect: $20-100/day during Phase 1, $50-200/day during Phases 2-4
Alert: If spend exceeds $500/day, something is wrong (stop and investigate)
```

### What to Do If Spend is Too High

1. **Stop the execution**: Type `q` at approval gate to exit gracefully
2. **Check recent logs**: `tail -500 /Users/rr/nzt48-signals/nzt48-aegis-v2/logs/execution/AEGIS_MASTER_*.script`
3. **Investigate Ralph Wiggum loops**: If cargo is retrying >10 times per session, there's a deeper issue
4. **Don't restart immediately**: Wait 1 hour for token counts to settle
5. **Resume carefully**: Run master command again, approve Phase 1, resume where you left off

### Normal Cost Breakdown

```
Phase 0: $0 (only Polygon API, no Claude Code)
Phase 1: $500-1,000 (7.3 hours × intensive refactoring)
Phase 2: $1,000-2,000 (77.4 hours infrastructure build)
Phase 3: $1,000-2,000 (358 hours, but parallelizable)
Phase 4: $500-1,000 (63 hours validation)
─────────────────────────────────────
Total: $3,500-6,000 (NOT $0)
```

---

## CHECKLIST BEFORE YOU PRESS ENTER

- [ ] Anthropic workspace has $100+ pre-funded
- [ ] ANTHROPIC_API_KEY is set in environment
- [ ] IB Gateway is running (port 4001 responds to nc check)
- [ ] IB Gateway is authenticated (2FA complete)
- [ ] POLYGON_API_KEY is set
- [ ] tmux session created: `tmux new -s aegis_master_build`
- [ ] You are inside the tmux session
- [ ] You understand all 4 Seventeenth-Order Audit traps
- [ ] You have reviewed SEVENTEENTH-ORDER-AUDIT.md
- [ ] You understand the cost will be $3,500-6,000 (not $0)
- [ ] You are ready for a 4-week execution with daily monitoring

---

## THE COMMAND (COPY & PASTE)

Inside your tmux session:

```bash
export POLYGON_API_KEY="[REDACTED - see .env]"
export ANTHROPIC_API_KEY="sk-ant-..."
bash /Users/rr/nzt48-signals/nzt48-aegis-v2/THE_MASTER_COMMAND.sh
```

---

## IF SOMETHING GOES WRONG

### Phase 0 Fails at Task 3 (IBKR LSE Discovery)
→ Check IB Gateway is running: `nc -vz 127.0.0.1 4001`
→ Authenticate via VNC if needed
→ Run master command again, it will resume from checkpoint

### Phase 1 RM-1 Fails 20 Times (Ralph Wiggum Loop)
→ Master command STOPS and asks for help
→ Check the error message in the logs
→ Do NOT re-run immediately (let Anthropic tokens cool down)
→ Fix the underlying issue (usually a Rust syntax error)
→ Run master command again to retry

### SSH Connection Drops During Phase 2
→ You are inside tmux, so it's fine
→ Reconnect to EC2: `ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22`
→ Reattach to session: `tmux attach -t aegis_master_build`
→ Master command is still running (or paused at approval gate)
→ Type [c] to continue

### Anthropic API Tier Limit Hit
→ Workspace is out of credits
→ Add more credit via Anthropic dashboard
→ Wait 1 hour for account to refresh
→ Reattach to tmux session
→ Type [c] to resume (Ralph Wiggum loop will retry)

---

## THE FINAL VERDICT

You are about to execute a 504-hour autonomous orchestration pipeline that will:

1. ✅ Bootstrap all data caches (Phase 0)
2. ✅ Refactor core trading engine (Phase 1)
3. ✅ Build infrastructure (Phase 2)
4. ✅ Implement complete trading system (Phase 3)
5. ✅ Validate with 100 paper trades (Phase 4)
6. ✅ Pause, ready for live capital (Phase 5)

**All 4 Fatal Orchestration Traps are now patched:**
- ✅ Trap 1 (API Budget): Seventeenth-Order checks + budget warning
- ✅ Trap 2 (IB Gateway): Port check + authentication gate
- ✅ Trap 3 (SSH Drops): tmux session protection
- ✅ Trap 4 (Log Corruption): TTY-safe logging with `script`

**You are 100% ready.**

---

## TURN THE KEY

```bash
tmux new -s aegis_master_build
export POLYGON_API_KEY="[REDACTED - see .env]"
export ANTHROPIC_API_KEY="sk-ant-..."
bash /Users/rr/nzt48-signals/nzt48-aegis-v2/THE_MASTER_COMMAND.sh
```

The Institutional Syndicate has sealed the blueprints.

**GO.**

---

*FINAL_EXECUTION_PROTOCOL.md — Generated 2026-03-10*
*Status: READY FOR EXECUTION*
*All safety checks complete. All traps patched. All contingencies documented.*
