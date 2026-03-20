# 🚀 KingShot Bot — Ubuntu Installation Guide

> Complete guide to install and run the KingShot Telegram gift-code bot on Ubuntu 22.04 / 24.04

---

## 📦 Prerequisites

Before you begin, ensure you have:

- A **Telegram bot token** from @BotFather
- Your **Telegram user ID** (use @userinfobot)
- Ubuntu 22.04 or 24.04 system
- Terminal access (SSH or local)
- Basic familiarity with command line

---

## 📁 PART 1 — Get the Project

### Option A — Clone from GitHub

```bash
git clone https://github.com/j0ckinjz/kingshot-bot.git
cd ~/kingshot-bot
```

### Option B — Manual copy

Ensure these files exist:

- bot.py
- redeemer.py
- setup.sh
- requirements.txt

```bash
cd ~/kingshot-bot
ls
```

---

## ⚙️ PART 2 — Run Setup Script

> ⚠️ Run this script from inside your project directory.

```bash
bash setup.sh
cd ~/kingshot-bot
```

### What this does:

- Updates system packages
- Installs Google Chrome (required for Selenium)
- Installs Python dependencies
- Adds ~/.local/bin to PATH
- Creates directories: logs, screenshots
- Creates .env if missing
- Creates systemd service if missing

---

## 📝 PART 3 — Configure the Bot

```bash
nano .env
```

Fill in:

```
TELEGRAM_BOT_TOKEN=your_token_here
ADMIN_IDS=your_id_here
CHECK_INTERVAL=30
```

Save with `Ctrl+O`, `Enter`, then exit with `Ctrl+X`.

---

## 🧪 PART 4 — Test the Bot

```bash
python3 bot.py
```

Expected output:
```
2025-01-01 12:00:00 [INFO] KingShot Auto Gift Code Bot (Ubuntu)
2025-01-01 12:00:00 [INFO] Admin IDs      : [123456789]
2025-01-01 12:00:00 [INFO] Check interval : 30 min
2025-01-01 12:00:00 [INFO] Scheduler started — checks every 30 min
2025-01-01 12:00:00 [INFO] Starting Telegram polling...
```

On Telegram, open your bot and send `/ping` — you should get back `🏓 Pong! Bot is alive.`

Once confirmed working, press `Ctrl+C` to stop.

---

## 🔁 PART 5 — Enable Auto Start

```bash
sudo systemctl enable kingshot
sudo systemctl start kingshot
sudo systemctl status kingshot
```

---

## 📜 PART 6 — Useful Commands

### View logs

```bash
journalctl -u kingshot -f
```
(`Ctrl+C` to stop following)

### Restart

```bash
sudo systemctl restart kingshot
```

### Stop

```bash
sudo systemctl stop kingshot
```

---

## 🔄 PART 7 — Updating

```bash
cd ~/kingshot-bot
git pull
sudo systemctl restart kingshot
```

---

## 🤖 PART 8 — Bot Commands

| Command | Description | Admin Only |
|---------|-------------|------------|
| `/ping` | Quick alive check | No |
| `/help` | Show all commands | No |
| `/addplayer 876734319 Gopi` | Register a player | ✅ |
| `/addplayers` | Bulk add (one per line) | ✅ |
| `/removeplayer 876734319` | Remove a player | ✅ |
| `/listplayers` | Show all players + claim counts | ✅ |
| `/listcodes` | Show all tracked gift codes | ✅ |
| `/addcode CODE123` | Manually force-redeem a code | ✅ |
| `/clearcode CODE123` | Re-queue code for all players | ✅ |
| `/mystatus 876734319` | Show a player's claim history | ✅ |
| `/resetplayer 876734319` | Re-queue all codes for one player | ✅ |
| `/checkcode` | Force an immediate code check | ✅ |
| `/nextcheck` | Show next scheduled check time | ✅ |
| `/status` | Bot status, uptime, player count | ✅ |

---

## 🛠️ PART 9 — Troubleshooting

| Problem | Likely Cause | Fix |
|---------|--------------|-----|
| Bot doesn't respond | Wrong token or not running | Check `journalctl -u kingshot -n 50` |
| "Invalid token" in logs | Token typed wrong | Re-check `TELEGRAM_BOT_TOKEN` in .env file |
| Chrome not found | Chrome not installed | Run `which google-chrome` → should return `/usr/bin/google-chrome`. If not, re-run `setup.sh`. |
| `systemctl status` shows "failed" | Code crash at start | Check `journalctl -u kingshot -n 50` for the Python error |
| No codes being found | API URL changed or wrong response format | Check `journalctl -u kingshot` for API errors |
| Selenium timeout errors | Site layout changed | Check `screenshots/` folder for debug images |
| "No valid ADMIN_IDS" error | ADMIN_IDS not set | Make sure it's a plain number (e.g. `123456789`) |
| Bot starts twice | Old process still running | `sudo systemctl stop kingshot` then `start` |


---

## 👥 PART 10 — Multiple Admins

To add more than one admin, comma-separate the IDs in your `.env` or service file:

```ini
ADMIN_IDS=123456789,987654321,111222333
```

All listed users can run admin commands.

---

## 🔐 PART 11 — Security

```bash
echo ".env" >> .gitignore
chmod 600 .env
```

---

## ⚙️ PART 12 — How It Works

- Fetching: curl_cffi (bypasses Cloudflare)
- Redeeming: Selenium + Chrome
- Scheduler: APScheduler

---

## 🚀 Done!

Check logs:

```bash
journalctl -u kingshot -f
```
