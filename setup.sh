#!/usr/bin/env bash
# setup.sh
# ─────────────────────────────────────────────────────────────────────────────
# One-shot setup script for Ubuntu 22.04 / 24.04
# Run ONCE after SSH-ing into your Ubuntu VM:
#
#   bash setup.sh
#
# What it does:
#   1. Updates the system
#   2. Installs Google Chrome (for Selenium)
#   3. Installs Python 3 + pip + Python dependencies
#   4. Creates logs and screenshots directories
#   5. Creates .env and service files
# ─────────────────────────────────────────────────────────────────────────────

set -e  # Stop on any error

echo ""
echo "╔═══════════════════════════════════════╗"
echo "║  KingShot Bot — Ubuntu Setup Script   ║"
echo "╚═══════════════════════════════════════╝"
echo ""

# ── Step 1: System update ──────────────────────────────────────────────────
echo "▶ Step 1/6: Updating system packages..."
sudo apt-get update -qq
sudo apt-get upgrade -y -qq
sudo apt-get install -y -qq wget curl gnupg2 unzip python3 python3-pip git screen nano
echo "   ✅ System updated."

# ── Step 2: Install Google Chrome ─────────────────────────────────────────
echo "▶ Step 2/6: Installing Google Chrome..."
if command -v google-chrome &> /dev/null; then
    echo "   ℹ️  Chrome already installed: $(google-chrome --version)"
else
    wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
    echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" \
        | sudo tee /etc/apt/sources.list.d/google-chrome.list
    sudo apt-get update -qq
    sudo apt-get install -y -qq google-chrome-stable
    echo "   ✅ Installed: $(google-chrome --version)"
fi

# ── Step 3: Install Python dependencies ───────────────────────────────────
echo "▶ Step 3/6: Installing Python packages..."
pip3 install --upgrade pip -q
pip3 install \
    selenium==4.20.0 \
    requests==2.31.0 \
    pyTelegramBotAPI==4.18.0 \
    APScheduler==3.10.4 \
    python-dotenv==1.0.1 \
    curl_cffi==0.14.0 \
    -q
echo "   ✅ Python packages installed."

# ── Step 4: Ensure ~/.local/bin is in PATH ────────────────────────────
echo "▶ Step 4/6: Ensuring ~/.local/bin is in PATH..."

LOCAL_BIN="$HOME/.local/bin"
PROFILE_FILE="$HOME/.bashrc"

# Check if already in PATH
if [[ ":$PATH:" != *":$LOCAL_BIN:"* ]]; then
    echo "   ⚠️  ~/.local/bin not in PATH, adding it..."

    # Add to .bashrc if not already present
    if ! grep -q 'export PATH="$HOME/.local/bin:$PATH"' "$PROFILE_FILE"; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$PROFILE_FILE"
        echo "   ✅ Added to $PROFILE_FILE"
    else
        echo "   ℹ️  PATH already configured in $PROFILE_FILE"
    fi

    # Apply immediately for current session
    export PATH="$HOME/.local/bin:$PATH"
    echo "   ✅ PATH updated for current session"
else
    echo "   ✅ ~/.local/bin already in PATH"
fi

# ── Step 5: Create project directories ────────────────────────────────────
echo "▶ Step 5/6: Creating project directories..."
mkdir -p logs screenshots .selenium_cache
echo "   ✅ Directories created."

# ── Step 6: Create .env and systemd service if missing ─────────────────
echo "▶ Step 6/6: Checking .env and systemd service..."

CURRENT_USER="$(id -un)"
PROJECT_DIR="$(pwd)"
ENV_FILE="$PROJECT_DIR/.env"
SERVICE_PATH="/etc/systemd/system/kingshot.service"

echo "   ℹ️  Detected user: $CURRENT_USER"
echo "   ℹ️  Project dir: $PROJECT_DIR"

if [ ! -f "$PROJECT_DIR/bot.py" ]; then
    echo "   ❌ bot.py not found in $PROJECT_DIR"
    echo "   ❌ Run this script from inside your KingShot bot project directory."
    exit 1
fi

# Create .env if it does not already exist
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<EOF
# ── KingShot Bot Configuration ──

# Telegram bot token (from @BotFather)
TELEGRAM_BOT_TOKEN=

# Your Telegram user ID(s), comma-separated
ADMIN_IDS=

# How often to check for new codes (minutes)
CHECK_INTERVAL=30
EOF

    echo "   ✅ Created .env template"
    echo "   ⚠️  Edit this file and add your values:"
    echo "       nano $ENV_FILE"
else
    echo "   ✅ .env already exists, leaving it unchanged"
fi

# Create systemd service if it does not already exist
if [ ! -f "$SERVICE_PATH" ]; then
    sudo tee "$SERVICE_PATH" > /dev/null <<EOF
[Unit]
Description=KingShot Auto Gift Code Bot (Telegram)
After=network-online.target
Wants=network-online.target

[Service]
User=$CURRENT_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/python3 $PROJECT_DIR/bot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    echo "   ✅ Created systemd service: $SERVICE_PATH"
else
    echo "   ✅ Service file already exists, leaving it unchanged"
fi

echo "   ℹ️  Service was not enabled or started automatically."
echo "   ℹ️  When ready, run:"
echo "       sudo systemctl enable kingshot"
echo "       sudo systemctl start kingshot"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  ✅ Setup complete!                                  ║"
echo "║                                                      ║"
echo "║  Next steps:                                         ║"
echo "║  1. nano .env  ← paste your BOT_TOKEN + ADMIN_IDS    ║"
echo "║  2. Test bot: python3 bot.py                         ║"
echo "║  3. Enable service: sudo systemctl enable kingshot   ║"
echo "║  4. Start service: sudo systemctl start kingshot     ║"
echo "║  5. Service logs: journalctl -u kingshot -f          ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
