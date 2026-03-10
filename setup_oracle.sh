#!/usr/bin/env bash
# setup_oracle.sh
# ─────────────────────────────────────────────────────────────────────────────
# One-shot setup script for Oracle Cloud Free Tier (Ubuntu 22.04)
# Run this ONCE after SSHing into your Oracle VM:
#
#   bash setup_oracle.sh
#
# What it does:
#   1. Updates the system
#   2. Installs Google Chrome (for Selenium)
#   3. Installs Python 3 + pip
#   4. Installs all Python dependencies
#   5. Clones / sets up the project
# ─────────────────────────────────────────────────────────────────────────────

set -e  # Stop on any error

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║  KingShot Bot — Oracle Cloud Setup Script   ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── Step 1: System update ──────────────────────────────────────────────────
echo "▶ Step 1/4: Updating system packages..."
sudo apt-get update -qq
sudo apt-get upgrade -y -qq
sudo apt-get install -y -qq wget curl gnupg2 unzip python3 python3-pip git screen

# ── Step 2: Install Google Chrome ─────────────────────────────────────────
echo "▶ Step 2/4: Installing Google Chrome..."
wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" \
    | sudo tee /etc/apt/sources.list.d/google-chrome.list
sudo apt-get update -qq
sudo apt-get install -y -qq google-chrome-stable

CHROME_VER=$(google-chrome --version)
echo "   ✅ Installed: $CHROME_VER"

# ── Step 3: Install Python dependencies ───────────────────────────────────
echo "▶ Step 3/4: Installing Python packages..."
pip3 install --upgrade pip -q
pip3 install selenium==4.20.0 requests==2.31.0 pyTelegramBotAPI==4.18.0 APScheduler==3.10.4 -q
echo "   ✅ Python packages installed."

# ── Step 4: Open firewall ports (Oracle Cloud requires this) ───────────────
echo "▶ Step 4/4: Configuring firewall..."
# Oracle's internal iptables blocks everything by default
# We don't need any ports open for polling mode, but just in case
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || true
sudo iptables -I OUTPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || true
echo "   ✅ Firewall configured."

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║  ✅ Setup complete!                          ║"
echo "║                                              ║"
echo "║  Next steps:                                 ║"
echo "║  1. Edit bot.py — paste your BOT_TOKEN       ║"
echo "║     and ADMIN_IDS at the top                 ║"
echo "║  2. Run: python3 bot.py  (to test)           ║"
echo "║  3. Then set up systemd for auto-start       ║"
echo "║     (see README.md for commands)             ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
