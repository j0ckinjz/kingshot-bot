# 🚀 KingShot Gift Code Bot — Telegram Edition

Automatically detects and redeems KingShot gift codes for multiple players using Telegram.

---

## ✨ Features

- 🎁 Auto-detects new gift codes
- 🤖 Redeems codes for multiple players
- 🔁 Retries failed redemptions automatically
- 🧠 Smart duplicate tracking (per player)
- 🌐 Bypasses Cloudflare using `curl_cffi`
- 📊 Telegram commands for full control
- 🔄 Runs 24/7 via systemd

---

## 📖 Full Installation Guide

👉 See: `DEPLOY-GUIDE.md`

---

## ⚡ Quick Start (Ubuntu)

```bash
git clone https://github.com/j0ckinjz/kingshot-bot.git
cd ~/kingshot-bot

bash setup.sh
cd ~/kingshot-bot
nano .env

python3 bot.py
```

Then test in Telegram:

```
/ping
```

---

## 🔁 Run as a Service

```bash
sudo systemctl enable kingshot
sudo systemctl start kingshot
```

---

## 📜 Logs

```bash
journalctl -u kingshot -f
```

---

## 🤖 Bot Commands

### Player Management
| Command | Description |
|---------|-------------|
| `/addplayer 876734319 Gopi` | Register a player (admin only) |
| `/addplayers` | Bulk add players — one `id name` per line (admin only) |
| `/removeplayer 876734319` | Remove a player (admin only) |
| `/listplayers` | Show all players with redemption progress |

### Code Management
| Command | Description |
|---------|-------------|
| `/listcodes` | Show all tracked gift codes and claim counts |
| `/addcode CODE123` | Manually force-redeem a code for all players (admin only) |
| `/clearcode CODE123` | Re-queue a code to be redeemed again for all players (admin only) |
| `/mystatus 876734319` | Show which codes a specific player has claimed |
| `/resetplayer 876734319` | Re-queue ALL codes for one player (admin only) |

### Bot Control
| Command | Description |
|---------|-------------|
| `/checkcode` | Force a gift code check right now (admin only) |
| `/nextcheck` | Show when the next scheduled check fires (admin only) |
| `/status` | Show bot status, uptime, and player count |
| `/ping` | Quick alive check |
| `/help` | Show command list |

---

## 📁 Project Structure

| File | Purpose |
|------|---------|
| `bot.py` | Main bot — Telegram polling + APScheduler |
| `redeemer.py` | Selenium redemption logic |
| `.env` | Your actual tokens (created with setup.sh) (never commit this to GitHub) |
| `kingshot.service` | systemd service for auto-start (created with setup.sh) |
| `setup.sh` | One-shot setup script for the VM |
| `DEPLOY-GUIDE.md` | Full step-by-step deployment guide |
| `players.json` | Auto-created — stores registered player IDs |
| `seen_codes.json` | Auto-created — tracks redeemed codes per player |
| `logs/` | Daily log files |
| `screenshots/` | Auto-saved on Selenium errors |

---

## ⚙️ How It Works

- API fetch → `curl_cffi` (Cloudflare bypass — no proxy or browser required)
- Redemption → Selenium + Chrome
- Scheduling → APScheduler
- Control → Telegram bot