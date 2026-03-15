#!/bin/bash
# Monitor NZT-48 Phase Q1-Q4 Continuous Execution
# Real-time status dashboard for all 5 agents

set -e

LOG_FILE="/tmp/nzt48_orchestration.log"
EXEC_LOG="/Users/rr/nzt48-signals/PHASE_Q1_Q4_EXECUTION_LOG.md"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Agent IDs
Q1_AGENT="a5a1e4c"
Q2_AGENT="aeb7953"
Q3_AGENT="a3dd15e"
Q4_AGENT="a0376bc"
ORCH_AGENT="a19a09e"

# Agent output paths
Q1_OUTPUT="/private/tmp/claude-501/-Users-rr/tasks/${Q1_AGENT}.output"
Q2_OUTPUT="/private/tmp/claude-501/-Users-rr/tasks/${Q2_AGENT}.output"
Q3_OUTPUT="/private/tmp/claude-501/-Users-rr/tasks/${Q3_AGENT}.output"
Q4_OUTPUT="/private/tmp/claude-501/-Users-rr/tasks/${Q4_AGENT}.output"
ORCH_OUTPUT="/private/tmp/claude-501/-Users-rr/tasks/${ORCH_AGENT}.output"

check_agent_status() {
    local agent_id=$1
    local agent_name=$2
    local output_file=$3

    if [ ! -f "$output_file" ]; then
        echo -e "${RED}✗${NC} $agent_name: No output file (not started yet)"
        return 1
    fi

    # Get last 5 lines to see current status
    local tail_lines=$(tail -5 "$output_file" 2>/dev/null || echo "")

    if echo "$tail_lines" | grep -q "COMPLETE\|SUCCESS\|Deployed"; then
        echo -e "${GREEN}✓${NC} $agent_name: COMPLETE"
        return 0
    elif echo "$tail_lines" | grep -q "ERROR\|FAILED\|FAIL"; then
        echo -e "${RED}✗${NC} $agent_name: FAILED"
        echo "  Last line: $(tail -1 "$output_file" | cut -c1-80)"
        return 2
    elif echo "$tail_lines" | grep -q "Running\|Processing\|Implementing"; then
        echo -e "${YELLOW}◐${NC} $agent_name: RUNNING"
        echo "  $(tail -1 "$output_file" | cut -c1-80)"
        return 1
    else
        echo -e "${CYAN}◑${NC} $agent_name: IN PROGRESS"
        return 1
    fi
}

show_header() {
    clear
    echo -e "${PURPLE}╔════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${PURPLE}║${NC}  NZT-48 Phase Q1-Q4 CONTINUOUS EXECUTION MONITOR               ${PURPLE}║${NC}"
    echo -e "${PURPLE}║${NC}  Real-Time Status Dashboard                                   ${PURPLE}║${NC}"
    echo -e "${PURPLE}║${NC}  $(date '+%Y-%m-%d %H:%M:%S UTC')                                       ${PURPLE}║${NC}"
    echo -e "${PURPLE}╚════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

show_agent_status() {
    echo -e "${BLUE}┌─ AGENT STATUS BOARD ────────────────────────────────────────┐${NC}"
    echo ""

    check_agent_status "$Q1_AGENT" "Q1 (Quick Wins)" "$Q1_OUTPUT"
    check_agent_status "$Q2_AGENT" "Q2 (Performance)" "$Q2_OUTPUT"
    check_agent_status "$Q3_AGENT" "Q3 (Infrastructure)" "$Q3_OUTPUT"
    check_agent_status "$Q4_AGENT" "Q4 (Advanced ML)" "$Q4_OUTPUT"
    check_agent_status "$ORCH_AGENT" "Orchestrator" "$ORCH_OUTPUT"

    echo ""
    echo -e "${BLUE}└─────────────────────────────────────────────────────────────${NC}"
}

show_deployment_status() {
    echo ""
    echo -e "${BLUE}┌─ DEPLOYMENT STATUS ─────────────────────────────────────────┐${NC}"
    echo ""

    # Check if EC2 is reachable
    if ssh -o ConnectTimeout=5 -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 "curl -s http://localhost:8000/health > /dev/null 2>&1"; then
        echo -e "${GREEN}✓${NC} EC2 Instance: HEALTHY (3.230.44.22)"

        # Get Docker status
        local docker_status=$(ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 "docker ps --format='table {{.Names}}\t{{.Status}}'" 2>/dev/null)
        echo "  Docker containers:"
        echo "$docker_status" | head -5 | sed 's/^/    /'
    else
        echo -e "${YELLOW}◐${NC} EC2 Instance: UNREACHABLE (not deployed yet or offline)"
    fi

    echo ""
    echo -e "${BLUE}└─────────────────────────────────────────────────────────────${NC}"
}

show_recent_commits() {
    echo ""
    echo -e "${BLUE}┌─ RECENT GIT COMMITS ────────────────────────────────────────┐${NC}"
    echo ""

    if cd /Users/rr/nzt48-signals 2>/dev/null; then
        git log --oneline -5 2>/dev/null | sed 's/^/  /' || echo "  (no commits yet)"
    else
        echo "  (repo not accessible)"
    fi

    echo ""
    echo -e "${BLUE}└─────────────────────────────────────────────────────────────${NC}"
}

show_quick_commands() {
    echo ""
    echo -e "${BLUE}┌─ QUICK COMMANDS ────────────────────────────────────────────┐${NC}"
    echo ""
    echo "  Monitor Q1 agent:        tail -f $Q1_OUTPUT"
    echo "  Monitor Q2 agent:        tail -f $Q2_OUTPUT"
    echo "  Monitor Q3 agent:        tail -f $Q3_OUTPUT"
    echo "  Monitor Q4 agent:        tail -f $Q4_OUTPUT"
    echo "  Check EC2 logs:          ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 'docker logs nzt48 --tail 50'"
    echo "  View full execution log: cat $EXEC_LOG"
    echo ""
    echo -e "${BLUE}└─────────────────────────────────────────────────────────────${NC}"
}

main_loop() {
    while true; do
        show_header
        show_agent_status
        show_deployment_status
        show_recent_commits
        show_quick_commands

        echo ""
        echo -e "${YELLOW}Refreshing in 30 seconds... (Press Ctrl+C to exit)${NC}"
        sleep 30
    done
}

# Run main loop
main_loop
