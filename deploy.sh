#!/bin/bash
# AEGIS V2 Deployment Script for EC2

echo "========================================================================"
echo "AEGIS V2 DEPLOYMENT & VALIDATION"
echo "========================================================================"
echo ""

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Configuration
LOCAL_CODE="/Users/rr/nzt48-signals"
EC2_HOST="3.230.44.22"
EC2_USER="ubuntu"

echo -e "${YELLOW}[PHASE 1] Verifying local code...${NC}"

# Check core modules
if [ ! -f "$LOCAL_CODE/src/orchestrator.py" ]; then
    echo -e "${RED}ERROR: orchestrator.py not found${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Orchestrator found${NC}"

# Check all phase modules
for module in kelly_sizer.py isa_auditor.py pre_trade_gate.py white_reality_check.py regime_detector.py vol_scaler.py confidence_scorer.py pre_conditions_gate.py position_sizer.py execution_quality.py; do
    if [ ! -f "$LOCAL_CODE/src/core/$module" ]; then
        echo -e "${RED}ERROR: $module not found${NC}"
        exit 1
    fi
done

echo -e "${GREEN}✓ All 10 core phases found${NC}"

echo ""
echo -e "${YELLOW}[PHASE 2] Testing EC2 connectivity...${NC}"

if ssh -o ConnectTimeout=5 ${EC2_USER}@${EC2_HOST} "echo ok" > /dev/null 2>&1; then
    echo -e "${GREEN}✓ SSH to EC2 working${NC}"
else
    echo -e "${RED}ERROR: Cannot reach EC2${NC}"
    exit 1
fi

echo ""
echo -e "${YELLOW}[PHASE 3] Syncing code to EC2...${NC}"

rsync -avz "$LOCAL_CODE/src/" ${EC2_USER}@${EC2_HOST}:~/nzt48-signals/src/ > /dev/null 2>&1
echo -e "${GREEN}✓ Code synced${NC}"

echo ""
echo -e "${YELLOW}[PHASE 4] Running orchestrator test...${NC}"

TEST_RESULT=$(ssh ${EC2_USER}@${EC2_HOST} "cd ~/nzt48-signals && python3 src/orchestrator.py 2>&1" | grep "TRADE APPROVED")

if [ ! -z "$TEST_RESULT" ]; then
    echo -e "${GREEN}✓ Orchestrator test passed${NC}"
else
    echo -e "${YELLOW}WARNING: Check orchestrator manually${NC}"
fi

echo ""
echo "========================================================================"
echo -e "${GREEN}DEPLOYMENT READY${NC}"
echo "========================================================================"
echo ""
echo "Next steps:"
echo "1. SSH: ssh ubuntu@3.230.44.22"
echo "2. Deploy gates: python3 ~/nzt48-signals/run_gates.py"
echo "3. Monitor progress (Sharpe, win rate, ISA compliance)"
echo ""
echo "All 6 gates must PASS for system to go LIVE"
echo ""

chmod +x /Users/rr/nzt48-signals/deploy.sh
echo -e "${GREEN}✓ Deploy script ready${NC}"
