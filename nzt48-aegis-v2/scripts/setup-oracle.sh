#!/bin/bash
# AEGIS V2 — Oracle Cloud ARM Instance Setup
# Run this ON the Oracle instance after SSH'ing in.
#
# Prerequisites:
#   - Oracle Cloud Always Free ARM instance (VM.Standard.A1.Flex)
#   - Ubuntu 22.04+ (Canonical image)
#   - SSH access configured
#
# Usage:
#   ssh -i ~/.ssh/oracle-nzt48.key ubuntu@<ORACLE_IP>
#   curl -fsSL https://raw.githubusercontent.com/nztsignals48-byte/nzt48-signals/feat/tier-system-enhancements-full/nzt48-aegis-v2/scripts/setup-oracle.sh | bash
#   # OR: copy this script and run it

set -e

echo "════════════════════════════════════════"
echo "AEGIS V2 — Oracle Cloud ARM Setup"
echo "════════════════════════════════════════"

# Step 1: System packages
echo "[1/7] Installing system packages..."
sudo apt-get update
sudo apt-get install -y \
    docker.io docker-compose-plugin \
    git curl jq htop \
    qemu-user-static binfmt-support  # x86 emulation for IB Gateway

# Enable Docker
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker ubuntu

# Step 2: QEMU binfmt for x86 emulation (IB Gateway needs this)
echo "[2/7] Configuring x86 emulation (QEMU binfmt)..."
sudo docker run --rm --privileged multiarch/qemu-user-static --reset -p yes 2>/dev/null || true

# Step 3: Clone repository
echo "[3/7] Cloning repository..."
cd ~
if [ -d "nzt48-signals-repo" ]; then
    echo "  Repo already exists, pulling latest..."
    cd nzt48-signals-repo
    git pull origin feat/tier-system-enhancements-full
else
    git clone https://github.com/nztsignals48-byte/nzt48-signals.git nzt48-signals-repo
    cd nzt48-signals-repo
    git checkout feat/tier-system-enhancements-full
fi

# Step 4: Create .env files
echo "[4/7] Creating environment files..."
cd nzt48-aegis-v2

if [ ! -f .env ]; then
    cat > .env << 'ENVEOF'
REDIS_PASSWORD=nzt48redis
POLYGON_API_KEY=
BENZINGA_API_KEY=
UW_API_KEY=
EODHD_API_KEY=
GEMINI_API_KEY=
QUIVER_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
ENVEOF
    echo "  Created .env (edit API keys later)"
fi

if [ ! -f .env.production ]; then
    cat > .env.production << 'PRODEOF'
# IB Gateway credentials — EDIT THESE
TWS_USERID=your_ibkr_username
TWS_PASSWORD=your_ibkr_password
TRADING_MODE=live
TWS_PORT=4003
VNC_SERVER_PASSWORD=aegis2026
PRODEOF
    echo "  Created .env.production — EDIT IBKR CREDENTIALS before starting!"
    echo ""
    echo "  ⚠️  IMPORTANT: Edit .env.production with your IBKR username/password:"
    echo "     nano ~/nzt48-signals-repo/nzt48-aegis-v2/.env.production"
    echo ""
fi

# Step 5: Open firewall ports
echo "[5/7] Configuring firewall..."
# Oracle Cloud uses iptables, not ufw
sudo iptables -I INPUT -p tcp --dport 22 -j ACCEPT      # SSH
sudo iptables -I INPUT -p tcp --dport 3000 -j ACCEPT     # Grafana
# Save iptables rules
sudo sh -c "iptables-save > /etc/iptables/rules.v4" 2>/dev/null || true

# Step 6: Increase system limits for Docker
echo "[6/7] Tuning system limits..."
sudo sysctl -w vm.max_map_count=262144
echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf >/dev/null

# Step 7: Summary
echo ""
echo "════════════════════════════════════════"
echo "SETUP COMPLETE"
echo "════════════════════════════════════════"
echo ""
echo "Next steps:"
echo "  1. Edit IBKR credentials:"
echo "     nano ~/nzt48-signals-repo/nzt48-aegis-v2/.env.production"
echo ""
echo "  2. Edit API keys (optional):"
echo "     nano ~/nzt48-signals-repo/nzt48-aegis-v2/.env"
echo ""
echo "  3. Build and start (first build takes ~10 min for Rust):"
echo "     cd ~/nzt48-signals-repo/nzt48-aegis-v2"
echo "     docker compose up -d --build"
echo ""
echo "  4. Monitor startup:"
echo "     docker compose logs -f aegis-v2"
echo ""
echo "  5. Approve IB Gateway 2FA on your phone"
echo ""
echo "  6. Verify:"
echo "     docker ps"
echo "     docker exec aegis-v2 cat /app/events/telemetry_snapshot.json | jq ."
