# KingShot Gift Code Bot — Oracle Cloud Edition

> Runs 24/7 for free on Oracle Cloud Free Tier.
> No Flask, no webhooks, no UptimeRobot — just a simple persistent bot.

---

## One-Time Setup (takes ~15 minutes)

### Step 1 — Create Oracle Cloud Free Tier Account
1. Go to **https://www.oracle.com/cloud/free/**
2. Sign up — credit card is for identity only, **you will NOT be charged**
3. Choose your home region (pick the closest one — you can't change it later)

### Step 2 — Create a Free VM Instance
1. Go to **Compute → Instances → Create Instance**
2. Change image to **Ubuntu 22.04**
3. Change shape to **Ampere (ARM)** → `VM.Standard.A1.Flex`
   - Set **OCPUs: 1** (can use up to 4 free)
   - Set **RAM: 6 GB** (can use up to 24 GB free)
4. Under **Add SSH Keys** — paste your public SSH key
   (Generate one with: `ssh-keygen -t ed25519` on your PC)
5. Click **Create** — VM is ready in ~2 minutes

### Step 3 — SSH into your VM
```bash
ssh ubuntu@YOUR_VM_PUBLIC_IP
```
(Find the public IP in Oracle Console → Compute → Instances)

### Step 4 — Upload and run setup script
From your PC, upload the project files:
```bash
# On your local PC
scp -r ./kingshot-bot ubuntu@YOUR_VM_IP:/home/ubuntu/kingshot-bot
```
Or clone from GitHub if you push it there:
```bash
# On the Oracle VM
git clone https://github.com/YOUR_USERNAME/kingshot-bot.git
cd kingshot-bot
```
Then run the setup script:
```bash
bash setup_oracle.sh
```

### Step 5 — Configure your tokens
Edit `bot.py` and paste your values at the top:
```python
BOT_TOKEN  = "123456789:ABCdef..."       # From @BotFather on Telegram
ADMIN_IDS  = [123456789]                  # Your Telegram user ID (from @userinfobot)
```
Or set them as environment variables in the systemd service (more secure — see below).

### Step 6 — Test it manually first
```bash
cd /home/ubuntu/kingshot-bot
python3 bot.py
```
Send `/help` to your bot on Telegram — you should get a response.
Press `Ctrl+C` to stop once you've confirmed it works.

### Step 7 — Set up systemd (auto-start on reboot, auto-restart on crash)

Copy the service file:
```bash
sudo cp kingshot.service /etc/systemd/system/kingshot.service
```

Edit it to add your tokens:
```bash
sudo nano /etc/systemd/system/kingshot.service
```
Replace `PASTE_YOUR_BOT_TOKEN_HERE` and `PASTE_YOUR_TELEGRAM_USER_ID_HERE` with real values.

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable kingshot      # Auto-start on reboot
sudo systemctl start kingshot       # Start now
sudo systemctl status kingshot      # Check it's running (should say "active (running)")
```

Your bot is now running 24/7! 🎉

---

## Daily Usage

### View live logs
```bash
journalctl -u kingshot -f
```

### Restart the bot
```bash
sudo systemctl restart kingshot
```

### Stop the bot
```bash
sudo systemctl stop kingshot
```

### Update the code
```bash
cd /home/ubuntu/kingshot-bot
git pull                             # If using GitHub
sudo systemctl restart kingshot
```

---

## Bot Commands (via Telegram)

| Command | Description |
|---------|-------------|
| `/addplayer 876734319 Gopi` | Add a player (admin only) |
| `/removeplayer 876734319` | Remove a player (admin only) |
| `/listplayers` | Show all registered players |
| `/checkcode` | Force a gift code check right now |
| `/clearcode CODE123` | Re-queue a code to be redeemed again |
| `/status` | Show bot status |
| `/help` | Show command list |

---

## File Structure

| File | Purpose |
|------|---------|
| `bot.py` | Main bot — Telegram polling + APScheduler |
| `redeemer.py` | Selenium redemption logic |
| `players.json` | Auto-created — stores registered player IDs |
| `seen_codes.json` | Auto-created — tracks redeemed codes |
| `logs/` | Daily log files |
| `screenshots/` | Auto-saved on Selenium errors |
| `setup_oracle.sh` | One-shot setup script for the VM |
| `kingshot.service` | systemd service for auto-start |

---

## Why polling is better than webhooks on Oracle

- Oracle VM is **always on** — no need for Telegram to push to you
- No Flask web server needed — simpler, fewer dependencies
- No public URL needed — bot connects outbound to Telegram's servers
- Works even if Oracle's public IP changes
- `bot.infinity_polling()` automatically reconnects on network drops

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Bot doesn't respond | Check `journalctl -u kingshot -f` for errors |
| "Invalid token" error | Double-check `BOT_TOKEN` in the service file |
| Chrome not found | Run `which google-chrome` — should return `/usr/bin/google-chrome` |
| systemd shows "failed" | Check logs with `journalctl -u kingshot -n 50` |
| VM unreachable after reboot | Check Oracle Console — instance may be stopped |
