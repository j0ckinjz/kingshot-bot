"""
KingShot Gift Code Bot — Telegram Edition (Oracle Cloud / Polling Mode)
========================================================================
Per-player code tracking:
  seen_codes.json = { "CODE123": ["pid1", "pid2"], "CODE456": ["pid1"] }

When a new player is added, any active codes they haven't redeemed yet
will automatically be redeemed for them on the next check.

Network error handling:
  - Read timeouts → silently ignored, polling resumes automatically
  - DNS failures  → waits 15s and reconnects automatically
  - Any crash     → outer while loop restarts polling immediately
"""

import os
import time
import json
from dotenv import load_dotenv
load_dotenv()
import logging
import requests
import threading
import signal
import sys
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
import telebot
from telebot import apihelper
from redeemer import redeem_code_for_players

# ─── Config ────────────────────────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("TELEGRAM_BOT_TOKEN", "PASTE_YOUR_TOKEN_HERE")
ADMIN_IDS      = [int(x) for x in os.environ.get("ADMIN_IDS", "PASTE_YOUR_ID_HERE").split(",") if x.strip().isdigit()]
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "2"))
API_URL        = "https://kingshot.net/api/gift-codes"
PLAYERS_FILE   = "players.json"
SEEN_FILE      = "seen_codes.json"

# How many seconds to wait before reconnecting after a network error
RECONNECT_DELAY = 15
# ───────────────────────────────────────────────────────────────────────────

# ─── Logging ───────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
log_file = f"logs/run_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)
# ───────────────────────────────────────────────────────────────────────────

# ─── Exception handler ─────────────────────────────────────────────────────

class BotExceptionHandler(telebot.ExceptionHandler):
    """
    Passed to infinity_polling so ALL exceptions are caught here
    instead of crashing the bot.

    Returning True = exception is handled, polling continues.
    Returning False = exception is re-raised (crashes the bot).
    """
    def handle(self, exception):
        # Network errors — log a warning and keep going
        network_errors = (
            "Read timed out",
            "timed out",
            "getaddrinfo failed",
            "Failed to resolve",
            "ConnectionError",
            "Max retries exceeded",
            "RemoteDisconnected",
            "Connection reset",
            "Connection aborted",
            "NameResolutionError",
            "ConnectTimeoutError",
            "ReadTimeoutError",
        )
        err_str = str(exception)
        for keyword in network_errors:
            if keyword.lower() in err_str.lower():
                log.warning(f"⚠️  Network hiccup (will auto-reconnect): {type(exception).__name__}")
                return True   # ← Handled. polling will resume on its own.

        # Unexpected error — log the full traceback but still keep polling
        log.error(f"❌ Unhandled bot exception: {exception}", exc_info=True)
        return True  # Still return True so the bot doesn't die


bot = telebot.TeleBot(BOT_TOKEN, threaded=True, exception_handler=BotExceptionHandler())


# ─── Storage helpers ───────────────────────────────────────────────────────

def load_players() -> list:
    if not os.path.exists(PLAYERS_FILE):
        return []
    with open(PLAYERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_players(players: list):
    with open(PLAYERS_FILE, "w", encoding="utf-8") as f:
        json.dump(players, f, indent=2, ensure_ascii=False)

def load_seen() -> dict:
    """Returns dict: { "CODE123": ["pid1", "pid2"], ... }"""
    if not os.path.exists(SEEN_FILE):
        return {}
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_seen(seen: dict):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, ensure_ascii=False)

def mark_redeemed(seen: dict, code: str, pid: str):
    if code not in seen:
        seen[code] = []
    if pid not in seen[code]:
        seen[code].append(pid)

def has_redeemed(seen: dict, code: str, pid: str) -> bool:
    return pid in seen.get(code, [])

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


# ─── Telegram helpers ──────────────────────────────────────────────────────

def notify_admins(text: str):
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, text, parse_mode="Markdown")
        except Exception as e:
            log.warning(f"Could not notify admin {admin_id}: {e}")


# ─── Bot Commands ──────────────────────────────────────────────────────────

@bot.message_handler(commands=["start", "help"])
def cmd_help(message):
    bot.reply_to(message, (
        "🎮 *KingShot Gift Code Bot*\n\n"
        "*Admin Commands:*\n"
        "`/addplayer <id> <n>` — Register a player\n"
        "`/addplayers` — Register multiple players (one per line)\n"
        "`/removeplayer <id>` — Remove a player\n"
        "`/listplayers` — Show all registered players\n"
        "`/checkcode` — Force check for new codes now\n"
        "`/mystatus <id>` — Show which codes a player has claimed\n"
        "`/resetplayer <id>` — Re-queue ALL codes for one player\n"
        "`/clearcode <code>` — Re-queue one code for ALL players\n"
        "`/status` — Show overall bot status\n\n"
        f"_Codes checked automatically every {CHECK_INTERVAL} min_ 🚀"
    ), parse_mode="Markdown")


@bot.message_handler(commands=["addplayer"])
def cmd_add_player(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        bot.reply_to(
            message,
            "❌ Usage: `/addplayer <player_id> <n>`\n"
            "Example: `/addplayer 876734319 Gopi`",
            parse_mode="Markdown"
        )
        return
    pid, name = parts[1].strip(), parts[2].strip()
    players = load_players()
    if any(p["id"] == pid for p in players):
        bot.reply_to(message, f"⚠️ Player `{pid}` is already registered.", parse_mode="Markdown")
        return
    players.append({"id": pid, "name": name})
    save_players(players)

    seen      = load_seen()
    unclaimed = [code for code in seen if not has_redeemed(seen, code, pid)]

    msg = f"✅ Added *{name}* (`{pid}`)\n"
    if unclaimed:
        msg += f"\n🎁 Found *{len(unclaimed)}* active code(s) they haven't claimed yet.\nRunning redemption now..."
        bot.reply_to(message, msg, parse_mode="Markdown")
        threading.Thread(
            target=redeem_for_new_player,
            args=(pid, name, unclaimed),
            daemon=True
        ).start()
    else:
        msg += "\n_No active codes to redeem right now — they'll get future ones automatically._"
        bot.reply_to(message, msg, parse_mode="Markdown")
    log.info(f"Player added: {name} ({pid})")


@bot.message_handler(commands=["removeplayer"])
def cmd_remove_player(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Usage: `/removeplayer <player_id>`", parse_mode="Markdown")
        return
    pid     = parts[1].strip()
    players = load_players()
    updated = [p for p in players if p["id"] != pid]
    if len(updated) == len(players):
        bot.reply_to(message, f"❌ Player `{pid}` not found.", parse_mode="Markdown")
        return
    save_players(updated)
    bot.reply_to(message, f"🗑 Removed player `{pid}`", parse_mode="Markdown")
    log.info(f"Player removed: {pid}")


@bot.message_handler(commands=["listplayers"])
def cmd_list_players(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return
    players = load_players()
    if not players:
        bot.reply_to(
            message,
            "No players yet.\nUse `/addplayer <id> <n>` to add one.",
            parse_mode="Markdown"
        )
        return
    seen  = load_seen()
    lines = []
    for p in players:
        claimed = sum(1 for code in seen if has_redeemed(seen, code, p["id"]))
        lines.append(f"`{p['id']}` — {p['name']}  _(claimed {claimed})_")
    bot.reply_to(
        message,
        f"👥 *Registered Players ({len(players)}):*\n" + "\n".join(lines),
        parse_mode="Markdown"
    )


@bot.message_handler(commands=["addplayers"])
def cmd_add_players_bulk(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return

    # Remove the /addplayers command part, get the rest
    lines = message.text.split("\n")[1:]  # everything after the first line
    lines = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]

    if not lines:
        bot.reply_to(message, (
            "❌ Usage: send each player on a new line:\n\n"
            "`/addplayers`\n"
            "`876734319 Gopi`\n"
            "`123456756 PlayerTwo`\n"
            "`999999999 PlayerThree`"
        ), parse_mode="Markdown")
        return

    players  = load_players()
    seen     = load_seen()
    added    = []
    skipped  = []

    for line in lines:
        parts = line.split(maxsplit=1)
        if len(parts) < 2:
            skipped.append(f"`{line}` — missing name")
            continue
        pid, name = parts[0].strip(), parts[1].strip()
        if any(p["id"] == pid for p in players):
            skipped.append(f"`{pid}` ({name}) — already exists")
            continue
        players.append({"id": pid, "name": name})
        added.append((pid, name))

    if added:
        save_players(players)

    # Build reply
    msg = ""
    if added:
        msg += f"✅ *Added {len(added)} player(s):*\n"
        msg += "\n".join(f"  • {name} (`{pid}`)" for pid, name in added)
    if skipped:
        msg += f"\n\n⚠️ *Skipped {len(skipped)}:*\n"
        msg += "\n".join(f"  • {s}" for s in skipped)

    bot.reply_to(message, msg, parse_mode="Markdown")
    log.info(f"Bulk add: {len(added)} added, {len(skipped)} skipped")

    # Redeem unclaimed codes for newly added players
    for pid, name in added:
        unclaimed = [code for code in seen if not has_redeemed(seen, code, pid)]
        if unclaimed:
            log.info(f"New player {name} ({pid}) has {len(unclaimed)} unclaimed codes — redeeming...")
            threading.Thread(
                target=redeem_for_new_player,
                args=(pid, name, unclaimed),
                daemon=True
            ).start()


@bot.message_handler(commands=["mystatus"])
def cmd_my_status(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Usage: `/mystatus <player_id>`", parse_mode="Markdown")
        return
    pid    = parts[1].strip()
    player = next((p for p in load_players() if p["id"] == pid), None)
    if not player:
        bot.reply_to(message, f"❌ Player `{pid}` not found.", parse_mode="Markdown")
        return
    seen      = load_seen()
    claimed   = [c for c in seen if has_redeemed(seen, c, pid)]
    unclaimed = [c for c in seen if not has_redeemed(seen, c, pid)]
    msg  = f"📊 *Status for {player['name']}* (`{pid}`)\n\n"
    msg += f"✅ Claimed   ({len(claimed)}): "   + ("`" + "`, `".join(claimed)   + "`" if claimed   else "_none_") + "\n"
    msg += f"⏳ Unclaimed ({len(unclaimed)}): " + ("`" + "`, `".join(unclaimed) + "`" if unclaimed else "_none_")
    bot.reply_to(message, msg, parse_mode="Markdown")


@bot.message_handler(commands=["resetplayer"])
def cmd_reset_player(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Usage: `/resetplayer <player_id>`", parse_mode="Markdown")
        return
    pid   = parts[1].strip()
    seen  = load_seen()
    count = 0
    for code in seen:
        if pid in seen[code]:
            seen[code].remove(pid)
            count += 1
    save_seen(seen)
    bot.reply_to(
        message,
        f"🔄 Reset *{count}* code(s) for `{pid}` — will re-redeem on next check.",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=["clearcode"])
def cmd_clearcode(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Usage: `/clearcode <code>`", parse_mode="Markdown")
        return
    code = parts[1].strip()
    seen = load_seen()
    if code not in seen:
        bot.reply_to(message, f"⚠️ Code `{code}` not in seen list.", parse_mode="Markdown")
        return
    del seen[code]
    save_seen(seen)
    bot.reply_to(
        message,
        f"✅ Cleared `{code}` — will re-run for ALL players on next check.",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=["checkcode"])
def cmd_checkcode(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return
    bot.reply_to(message, "🔍 Forcing a gift code check right now...")
    threading.Thread(target=check_and_redeem, daemon=True).start()


@bot.message_handler(commands=["status"])
def cmd_status(message):
    players = load_players()
    seen    = load_seen()
    bot.reply_to(message, (
        f"✅ *Bot Status*\n\n"
        f"👥 Players registered : `{len(players)}`\n"
        f"🎁 Codes tracked      : `{len(seen)}`\n"
        f"⏱  Check interval     : every `{CHECK_INTERVAL}` min\n"
        f"🖥  Mode               : Polling (Oracle Cloud)"
    ), parse_mode="Markdown")


# ─── Core redemption logic ─────────────────────────────────────────────────

def fetch_active_codes() -> list:
    try:
        r = requests.get(API_URL, timeout=15)
        r.raise_for_status()
        data  = r.json()
        codes = data.get("data", {}).get("giftCodes", [])
        return [c["code"] for c in codes if c.get("code")]
    except Exception as e:
        log.error(f"Failed to fetch gift codes: {e}")
        return []


def redeem_for_new_player(pid: str, name: str, codes: list):
    """Called immediately when a new player is added with unclaimed codes."""
    seen = load_seen()
    for code in codes:
        if has_redeemed(seen, code, pid):
            continue
        log.info(f"Redeeming old code [{code}] for new player {name} ({pid})")
        results = redeem_code_for_players(code, [(pid, name)], log)
        if results.get(pid):
            mark_redeemed(seen, code, pid)
            save_seen(seen)
            log.info(f"✅ {name} ({pid}) claimed {code}")
        else:
            log.warning(f"❌ {name} ({pid}) failed {code}")
    notify_admins(f"✅ Finished redeeming old codes for *{name}* (`{pid}`)")


def check_and_redeem():
    """
    Main scheduled job.
    For each active code, only redeem it for players who haven't claimed it yet.
    Failed players are NOT marked as done — they'll be retried next cycle.
    """
    log.info("─── Checking for new gift codes ───")
    active_codes = fetch_active_codes()
    if not active_codes:
        log.info("No active codes returned from API.")
        return

    log.info(f"Active codes from API: {active_codes}")
    players = load_players()
    if not players:
        log.warning("No players configured — skipping.")
        return

    seen          = load_seen()
    any_work_done = False

    for code in active_codes:
        pending = [
            (p["id"], p["name"])
            for p in players
            if not has_redeemed(seen, code, p["id"])
        ]
        if not pending:
            log.info(f"[{code}] All {len(players)} players already redeemed. Skipping.")
            continue

        log.info(f"[{code}] {len(pending)} player(s) pending redemption.")
        any_work_done = True
        notify_admins(f"🎁 Redeeming `{code}` for *{len(pending)}* player(s)...")

        results = redeem_code_for_players(code, pending, log)

        for pid, name in pending:
            if results.get(pid):
                mark_redeemed(seen, code, pid)
                log.info(f"  ✅ {name} ({pid}) → {code}")
            else:
                log.warning(f"  ❌ {name} ({pid}) failed → will retry next cycle")

        save_seen(seen)

    if any_work_done:
        notify_admins("✅ *Redemption round complete!*")
    else:
        log.info("Nothing to do — all players are up to date.")
    log.info("─── Check complete ───\n")


# ─── Entry point ───────────────────────────────────────────────────────────

def main():
    log.info("╔═══════════════════════════════════════════════╗")
    log.info("║  KingShot Auto Gift Code Bot (Oracle Cloud)   ║")
    log.info("╚═══════════════════════════════════════════════╝")
    log.info(f"Admin IDs      : {ADMIN_IDS}")
    log.info(f"Check interval : {CHECK_INTERVAL} min")

    def shutdown(signum, frame):
        log.info("Shutting down gracefully...")
        scheduler.shutdown(wait=False)
        sys.exit(0)
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Background scheduler for gift code polling
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_and_redeem, "interval", minutes=CHECK_INTERVAL, id="gift_code_check")
    scheduler.start()
    log.info(f"✅ Scheduler started — polling every {CHECK_INTERVAL} min")

    # Run one check immediately on startup
    threading.Thread(target=check_and_redeem, daemon=True).start()

    # ── Telegram polling with auto-reconnect ──────────────────────────────
    # Outer while loop: if infinity_polling ever exits (shouldn't happen
    # with exception_handler, but just in case), we restart it after a delay.
    notify_admins("🟢 *KingShot Bot started!*\nType /help for commands.")

    while True:
        try:
            log.info("✅ Starting Telegram bot (polling mode)...")
            bot.infinity_polling(
                timeout=60,            # Seconds to wait per long-poll request
                long_polling_timeout=30,  # Telegram server hold time
                skip_pending=True      # Ignore messages sent while bot was offline
            )
        except Exception as e:
            # This should almost never fire now, but if it does — reconnect
            log.error(f"❌ infinity_polling crashed unexpectedly: {e}")
            log.info(f"🔄 Reconnecting in {RECONNECT_DELAY}s...")
            time.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    main()