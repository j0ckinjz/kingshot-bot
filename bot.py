"""
KingShot Gift Code Bot — Telegram Edition (Ubuntu)
========================================================================
Per-player code tracking:
  seen_codes.json = {
    "CODE123": {
      "pid1": {
        "status": "redeemed",
        "message": "Redeemed, please claim the rewards in your mail!",
        "terminal": true,
        "success": true,
        "updated_at": "2026-04-17T01:23:45"
      }
    }
  }

Legacy layout is still accepted on load:
  { "CODE123": ["pid1", "pid2"] }

Legacy entries are migrated in memory to:
  {
    "status": "legacy",
    "message": "Migrated from legacy seen_codes.json entry",
    "terminal": true,
    "success": true,
    "updated_at": ""
  }

In this version, a record can represent either:
  - a terminal done state (redeemed / already_claimed / same_type_once /
    expired / not_found / legacy), or
  - a retryable last-known state (not_logged_in / unknown)
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import Any, Callable, Dict, List, Tuple

from dotenv import load_dotenv

load_dotenv()

from apscheduler.schedulers.background import BackgroundScheduler
from curl_cffi import requests as curl_requests
import telebot

from redeemer import redeem_code_for_players

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "PASTE_YOUR_TOKEN_HERE")
ADMIN_IDS = [
    int(x) for x in os.environ.get("ADMIN_IDS", "0").split(",")
    if x.strip().isdigit() and int(x.strip()) != 0
]
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "30"))
API_URL = "https://kingshot.net/api/gift-codes"
PLAYERS_FILE = "players.json"
SEEN_FILE = "seen_codes.json"
RECONNECT_DELAY = 15
BOT_START_TIME = datetime.now()

_players_lock = threading.Lock()
_seen_lock = threading.Lock()
_check_running = threading.Event()
_redeem_job_lock = threading.Lock()

SeenRecord = Dict[str, Any]
SeenCodeMap = Dict[str, SeenRecord]
SeenData = Dict[str, SeenCodeMap]

os.makedirs("logs", exist_ok=True)
log_handler = TimedRotatingFileHandler(
    "logs/run.log",
    when="midnight",
    interval=1,
    backupCount=14,
    encoding="utf-8",
)
log_handler.suffix = "%Y%m%d"
stream_handler = logging.StreamHandler()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[log_handler, stream_handler],
)
log = logging.getLogger(__name__)


def _atomic_write_json(path: str, data):
    """Write JSON via a temp file and atomic replace to avoid partial/corrupt writes."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


scheduler = BackgroundScheduler()


class BotExceptionHandler(telebot.ExceptionHandler):
    def handle(self, exception):
        network_errors = (
            "Read timed out", "timed out", "getaddrinfo failed",
            "Failed to resolve", "ConnectionError", "Max retries exceeded",
            "RemoteDisconnected", "Connection reset", "Connection aborted",
            "NameResolutionError", "ConnectTimeoutError", "ReadTimeoutError",
        )
        err_str = str(exception)
        for kw in network_errors:
            if kw.lower() in err_str.lower():
                log.warning(
                    "⚠️  Network hiccup (auto-reconnect): %s",
                    type(exception).__name__,
                )
                return True
        log.error("❌ Unhandled bot exception: %s", exception, exc_info=True)
        return True


bot = telebot.TeleBot(BOT_TOKEN, threaded=True, exception_handler=BotExceptionHandler())

_ORIGINAL_SEND_MESSAGE = bot.send_message
_ORIGINAL_REPLY_TO = bot.reply_to
_MARKDOWN_STRIP_TABLE = str.maketrans("", "", "*_`[]()")


def strip_markdown(text: str) -> str:
    return str(text).translate(_MARKDOWN_STRIP_TABLE)


def _send_message_with_fallback(chat_id: int, text: str, *args, **kwargs):
    try:
        return _ORIGINAL_SEND_MESSAGE(chat_id, text, *args, **kwargs)
    except Exception as exc:
        parse_mode = kwargs.get("parse_mode")
        if not parse_mode:
            raise
        fallback_kwargs = dict(kwargs)
        fallback_kwargs.pop("parse_mode", None)
        fallback_kwargs.pop("entities", None)
        fallback_text = strip_markdown(text)
        log.warning(
            "send_message parse failure for %s with %s; retrying plain text: %s",
            chat_id,
            parse_mode,
            exc,
        )
        return _ORIGINAL_SEND_MESSAGE(chat_id, fallback_text, *args, **fallback_kwargs)


def _reply_to_with_fallback(message, text: str, *args, **kwargs):
    try:
        return _ORIGINAL_REPLY_TO(message, text, *args, **kwargs)
    except Exception as exc:
        parse_mode = kwargs.get("parse_mode")
        if not parse_mode:
            raise
        fallback_kwargs = dict(kwargs)
        fallback_kwargs.pop("parse_mode", None)
        fallback_kwargs.pop("entities", None)
        fallback_text = strip_markdown(text)
        log.warning(
            "reply_to parse failure for chat %s with %s; retrying plain text: %s",
            getattr(getattr(message, "chat", None), "id", "?"),
            parse_mode,
            exc,
        )
        return _ORIGINAL_REPLY_TO(message, fallback_text, *args, **fallback_kwargs)


bot.send_message = _send_message_with_fallback
bot.reply_to = _reply_to_with_fallback


def _load_players_unlocked() -> List[Dict[str, str]]:
    if not os.path.exists(PLAYERS_FILE):
        return []
    try:
        with open(PLAYERS_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, IOError) as exc:
        log.error("Failed to load players: %s", exc)
        return []


def load_players() -> List[Dict[str, str]]:
    with _players_lock:
        return _load_players_unlocked()


def _save_players_unlocked(players: List[Dict[str, str]]):
    try:
        _atomic_write_json(PLAYERS_FILE, players)
    except IOError as exc:
        log.error("Failed to save players: %s", exc)


def save_players(players: List[Dict[str, str]]):
    with _players_lock:
        _save_players_unlocked(players)


def update_players_atomic(mutator: Callable[[List[Dict[str, str]]], object]):
    """Lock the full player read-modify-write cycle to avoid lost updates."""
    with _players_lock:
        players = _load_players_unlocked()
        result = mutator(players)
        _save_players_unlocked(players)
        return result


def _legacy_record() -> SeenRecord:
    return {
        "status": "legacy",
        "message": "Migrated from legacy seen_codes.json entry",
        "terminal": True,
        "success": True,
        "updated_at": "",
    }


def normalize_seen_record(record: Any, *, fallback_status: str = "unknown") -> SeenRecord:
    if not isinstance(record, dict):
        record = {}
    status = str(record.get("status", fallback_status))
    message = str(record.get("message", ""))
    terminal = bool(record.get("terminal", False))
    success = bool(record.get("success", False))
    updated_at = str(record.get("updated_at", ""))
    return {
        "status": status,
        "message": message,
        "terminal": terminal,
        "success": success,
        "updated_at": updated_at,
    }


def load_seen() -> SeenData:
    with _seen_lock:
        return _load_seen_unlocked()


def _load_seen_unlocked() -> SeenData:
    if not os.path.exists(SEEN_FILE):
        return {}
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, IOError) as exc:
        log.error("Failed to load seen codes: %s", exc)
        return {}

    if not isinstance(data, dict):
        return {}

    normalized: SeenData = {}
    for code, value in data.items():
        code_key = str(code).upper()
        normalized[code_key] = {}

        if isinstance(value, list):
            legacy = _legacy_record()
            for pid in value:
                normalized[code_key][str(pid)] = dict(legacy)
        elif isinstance(value, dict):
            for pid, record in value.items():
                if isinstance(record, dict):
                    normalized[code_key][str(pid)] = normalize_seen_record(record)
        else:
            continue

        if not normalized[code_key]:
            del normalized[code_key]

    return normalized


def save_seen(seen: SeenData):
    with _seen_lock:
        _save_seen_unlocked(seen)


def _save_seen_unlocked(seen: SeenData):
    serializable: SeenData = {}
    for code, records in seen.items():
        code_key = str(code).upper()
        if not isinstance(records, dict):
            continue
        serializable[code_key] = {}
        for pid, record in records.items():
            serializable[code_key][str(pid)] = normalize_seen_record(record)
        if not serializable[code_key]:
            del serializable[code_key]

    try:
        _atomic_write_json(SEEN_FILE, serializable)
    except IOError as exc:
        log.error("Failed to save seen codes: %s", exc)


def update_seen_atomic(mutator: Callable[[SeenData], object]):
    """Lock the full read-modify-write cycle to avoid lost updates."""
    with _seen_lock:
        seen = _load_seen_unlocked()
        result = mutator(seen)
        _save_seen_unlocked(seen)
        return result


def get_code_record(seen: SeenData, code: str, pid: str) -> SeenRecord:
    return seen.get(code.upper(), {}).get(str(pid), {})


def has_processed(seen: SeenData, code: str, pid: str) -> bool:
    record = get_code_record(seen, code, pid)
    return bool(record and record.get("terminal"))


def has_success(seen: SeenData, code: str, pid: str) -> bool:
    record = get_code_record(seen, code, pid)
    return bool(record and record.get("success"))


def result_priority(record: SeenRecord) -> Tuple[int, int, int]:
    normalized = normalize_seen_record(record)
    success = 1 if normalized.get("success") else 0
    terminal = 1 if normalized.get("terminal") else 0
    status = str(normalized.get("status", "unknown"))

    status_rank = {
        "redeemed": 50,
        "already_claimed": 49,
        "same_type_once": 48,
        "legacy": 47,
        "invalid_player": 40,
        "expired": 39,
        "claim_limit_reached": 38,
        "not_found": 37,
        "requirements_not_met": 36,
        "server_busy": 20,
        "not_logged_in": 19,
        "unknown": 10,
    }.get(status, 0)
    return success, terminal, status_rank


def should_replace_result(existing: SeenRecord, incoming: SeenRecord) -> bool:
    if not existing:
        return True
    return result_priority(incoming) >= result_priority(existing)


def record_result(seen: SeenData, code: str, pid: str, result: SeenRecord):
    key = code.upper()
    pid_key = str(pid)
    if key not in seen:
        seen[key] = {}

    normalized = normalize_seen_record(result)
    existing = normalize_seen_record(seen[key].get(pid_key, {}))
    if existing and not should_replace_result(existing, normalized):
        log.info(
            "Preserving existing result for %s/%s: existing=%s incoming=%s",
            key,
            pid_key,
            existing.get("status"),
            normalized.get("status"),
        )
        return

    normalized["updated_at"] = datetime.now().isoformat(timespec="seconds")
    seen[key][pid_key] = normalized


def summarize_results(results: Dict[str, SeenRecord]) -> Tuple[int, int, int]:
    satisfied = sum(1 for result in results.values() if result.get("success"))
    terminal = sum(
        1 for result in results.values()
        if result.get("terminal") and not result.get("success")
    )
    retryable = len(results) - satisfied - terminal
    return satisfied, terminal, retryable


def persist_results(code: str, results: Dict[str, SeenRecord]):
    def mutator(seen: SeenData):
        for pid, result in results.items():
            record_result(seen, code, str(pid), result)

    update_seen_atomic(mutator)


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def split_text_chunks(text: str, max_len: int = 4000) -> List[str]:
    if len(text) <= max_len:
        return [text]

    chunks: List[str] = []
    current = ""

    for line in text.splitlines(keepends=True):
        if len(line) > max_len:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(line), max_len):
                chunks.append(line[start:start + max_len])
            continue

        if len(current) + len(line) > max_len:
            if current:
                chunks.append(current)
            current = line
        else:
            current += line

    if current:
        chunks.append(current)

    return chunks or [text[:max_len]]


def safe_send(chat_id: int, text: str, parse_mode: str = "Markdown"):
    for chunk in split_text_chunks(text):
        try:
            bot.send_message(chat_id, chunk, parse_mode=parse_mode)
        except Exception as exc:
            log.warning("safe_send failed for %s: %s", chat_id, exc)
        time.sleep(0.3)


def notify_admins(text: str):
    for admin_id in ADMIN_IDS:
        safe_send(admin_id, text)


def get_uptime() -> str:
    delta = datetime.now() - BOT_START_TIME
    hours, rem = divmod(int(delta.total_seconds()), 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours}h {minutes}m {seconds}s"


def get_next_check_str() -> str:
    try:
        job = scheduler.get_job("gift_code_check")
        if job and job.next_run_time:
            return job.next_run_time.strftime("%H:%M:%S")
    except Exception:
        pass
    return "Unknown"


@bot.message_handler(commands=["start", "help"])
def cmd_help(message):
    bot.reply_to(message, (
        "🎮 *KingShot Gift Code Bot*\n\n"
        "━━━ *Player Management* ━━━\n"
        "`/addplayer <id> <name>` — Register one player\n"
        "`/addplayers` — Bulk add (one `id name` per line)\n"
        "`/removeplayer <id>` — Remove a player\n"
        "`/listplayers` — Show all registered players\n\n"
        "━━━ *Code Management* ━━━\n"
        "`/listcodes` — Show all tracked codes\n"
        "`/addcode <code>` — Manually process a code\n"
        "`/clearcode <code>` — Re-queue code for all players\n"
        "`/mystatus <id>` — Player processing history\n"
        "`/resetplayer <id>` — Re-queue all codes for a player\n\n"
        "━━━ *Bot Control* ━━━\n"
        "`/checkcode` — Force a gift code check right now\n"
        "`/nextcheck` — When is the next scheduled check\n"
        "`/status` — Bot status + uptime\n"
        "`/ping` — Quick alive check\n\n"
        f"_Auto-checks every {CHECK_INTERVAL} min_ 🚀"
    ), parse_mode="Markdown")


@bot.message_handler(commands=["ping"])
def cmd_ping(message):
    bot.reply_to(message, "🏓 Pong! Bot is alive and kicking.")


@bot.message_handler(commands=["addplayer"])
def cmd_add_player(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        bot.reply_to(
            message,
            "❌ Usage: `/addplayer <player_id> <name>`\nExample: `/addplayer 876734319 Gopi`",
            parse_mode="Markdown",
        )
        return

    pid, name = parts[1].strip(), parts[2].strip()
    if not pid.isdigit():
        bot.reply_to(message, "❌ Player ID must be numeric.", parse_mode="Markdown")
        return

    def mutator(players: List[Dict[str, str]]):
        if any(player["id"] == pid for player in players):
            return False
        players.append({"id": pid, "name": name, "added": datetime.now().isoformat()})
        return True

    added_ok = update_players_atomic(mutator)
    if not added_ok:
        bot.reply_to(message, f"⚠️ Player `{pid}` is already registered.", parse_mode="Markdown")
        return

    seen = load_seen()
    pending_codes = [code for code in seen if not has_processed(seen, code, pid)]

    msg = f"✅ Added *{name}* (`{pid}`)\n"
    if pending_codes:
        msg += f"\n🎁 Found *{len(pending_codes)}* outstanding code(s) — processing now..."
        bot.reply_to(message, msg, parse_mode="Markdown")
        threading.Thread(
            target=redeem_for_new_player,
            args=(pid, name, pending_codes),
            daemon=True,
        ).start()
    else:
        msg += "\n_No outstanding codes right now — future ones will be processed automatically._"
        bot.reply_to(message, msg, parse_mode="Markdown")

    log.info("Player added: %s (%s)", name, pid)


@bot.message_handler(commands=["addplayers"])
def cmd_add_players_bulk(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return

    lines = message.text.split("\n")[1:]
    lines = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]

    if not lines:
        bot.reply_to(
            message,
            "❌ Usage — one player per line after the command:\n\n`/addplayers`\n`876734319 Gopi`\n`123456789 Arjun`",
            parse_mode="Markdown",
        )
        return

    seen = load_seen()

    def mutator(players: List[Dict[str, str]]):
        added_local: List[Tuple[str, str]] = []
        skipped_local: List[str] = []

        for line in lines:
            parts = line.split(maxsplit=1)
            if len(parts) < 2:
                skipped_local.append(f"`{line}` — missing name")
                continue

            pid, name = parts[0].strip(), parts[1].strip()
            if not pid.isdigit():
                skipped_local.append(f"`{line}` — ID must be numeric")
                continue

            if any(player["id"] == pid for player in players):
                skipped_local.append(f"`{pid}` ({name}) — already exists")
                continue

            players.append({"id": pid, "name": name, "added": datetime.now().isoformat()})
            added_local.append((pid, name))

        return added_local, skipped_local

    added, skipped = update_players_atomic(mutator)

    msg = ""
    if added:
        msg += f"✅ *Added {len(added)} player(s):*\n"
        msg += "\n".join(f"  • {name} (`{pid}`)" for pid, name in added)
    if skipped:
        msg += f"\n\n⚠️ *Skipped {len(skipped)}:*\n"
        msg += "\n".join(f"  • {item}" for item in skipped)

    bot.reply_to(message, msg or "Nothing to do.", parse_mode="Markdown")
    log.info("Bulk add: %s added, %s skipped", len(added), len(skipped))

    for pid, name in added:
        pending_codes = [code for code in seen if not has_processed(seen, code, pid)]
        if pending_codes:
            threading.Thread(
                target=redeem_for_new_player,
                args=(pid, name, pending_codes),
                daemon=True,
            ).start()


@bot.message_handler(commands=["removeplayer"])
def cmd_remove_player(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Usage: `/removeplayer <player_id>`", parse_mode="Markdown")
        return

    pid = parts[1].strip()

    def player_mutator(players: List[Dict[str, str]]):
        target_local = next((player for player in players if player["id"] == pid), None)
        if not target_local:
            return None
        players[:] = [player for player in players if player["id"] != pid]
        return target_local

    target = update_players_atomic(player_mutator)
    if not target:
        bot.reply_to(message, f"❌ Player `{pid}` not found.", parse_mode="Markdown")
        return

    def seen_mutator(seen: SeenData):
        removed = 0
        for code in list(seen.keys()):
            if pid in seen[code]:
                del seen[code][pid]
                removed += 1
            if not seen.get(code):
                seen.pop(code, None)
        return removed

    removed_records = update_seen_atomic(seen_mutator)
    bot.reply_to(
        message,
        f"🗑 Removed *{target['name']}* (`{pid}`)\n"
        f"🧹 Cleared *{removed_records}* status record(s) from tracked codes.",
        parse_mode="Markdown",
    )
    log.info("Player removed: %s (%s) and cleared %s seen record(s)", target["name"], pid, removed_records)


@bot.message_handler(commands=["listplayers"])
def cmd_list_players(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return

    players = load_players()
    if not players:
        bot.reply_to(
            message,
            "No players yet.\nUse `/addplayer <id> <name>` to add one.",
            parse_mode="Markdown",
        )
        return

    seen = load_seen()
    total_codes = len(seen)
    lines: List[str] = []

    for index, player in enumerate(players, 1):
        processed = sum(1 for code in seen if has_processed(seen, code, player["id"]))
        success = sum(1 for code in seen if has_success(seen, code, player["id"]))
        retrying = sum(
            1 for code in seen
            if get_code_record(seen, code, player["id"])
            and not has_processed(seen, code, player["id"])
        )
        bar = "▓" * processed + "░" * max(0, total_codes - processed) if total_codes else ""
        lines.append(
            f"{index}. *{player['name']}* — `{player['id']}`\n"
            f"   ✅ processed {processed}/{total_codes} {bar}\n"
            f"   🎉 success {success}   🔄 retrying {retrying}"
        )

    safe_send(message.chat.id, f"👥 *Registered Players ({len(players)}):*\n\n" + "\n\n".join(lines))


@bot.message_handler(commands=["listcodes"])
def cmd_list_codes(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return

    seen = load_seen()
    players = load_players()
    total_players = len(players)
    if not seen:
        bot.reply_to(message, "📭 No tracked gift codes yet.", parse_mode="Markdown")
        return

    lines: List[str] = []
    for code, records in seen.items():
        success = sum(1 for record in records.values() if record.get("success"))
        terminal_fail = sum(
            1 for record in records.values()
            if record.get("terminal") and not record.get("success")
        )
        retrying = sum(1 for record in records.values() if not record.get("terminal"))
        processed = success + terminal_fail
        bar = "▓" * processed + "░" * max(0, total_players - processed) if total_players > 0 else ""
        lines.append(
            f"`{code}` — processed {processed}/{total_players} {bar}\n"
            f"   🎉 {success} success   ⛔ {terminal_fail} terminal   🔄 {retrying} retrying"
        )

    safe_send(message.chat.id, f"🎁 *Tracked Codes ({len(seen)}):*\n\n" + "\n\n".join(lines))


@bot.message_handler(commands=["addcode"])
def cmd_add_code(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Usage: `/addcode <CODE>`", parse_mode="Markdown")
        return

    code = parts[1].strip()
    players = load_players()
    if not players:
        bot.reply_to(message, "⚠️ No players registered yet.", parse_mode="Markdown")
        return

    bot.reply_to(message, f"🚀 Queuing manual processing for `{code}`...", parse_mode="Markdown")
    threading.Thread(target=_manual_redeem, args=(code,), daemon=True).start()


def _manual_redeem(code: str):
    def job():
        seen = load_seen()
        players = load_players()
        pending = [
            (player["id"], player["name"])
            for player in players
            if not has_processed(seen, code, player["id"])
        ]
        if not pending:
            notify_admins(f"ℹ️ All players have already been fully processed for `{code}`.")
            return

        notify_admins(f"🎁 Manually processing `{code}` for *{len(pending)}* player(s)...")
        results = redeem_code_for_players(code, pending, log)
        persist_results(code, results)

        satisfied, terminal, retryable = summarize_results(results)
        notify_admins(
            f"📊 `{code}` manual processing: "
            f"✅ {satisfied} satisfied  ⛔ {terminal} terminal  ❌ {retryable} retryable"
        )

    return run_serialized_redemption(f"manual:{code.upper()}", job)


@bot.message_handler(commands=["mystatus"])
def cmd_my_status(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Usage: `/mystatus <player_id>`", parse_mode="Markdown")
        return

    pid = parts[1].strip()
    player = next((item for item in load_players() if item["id"] == pid), None)
    if not player:
        bot.reply_to(message, f"❌ Player `{pid}` not found.", parse_mode="Markdown")
        return

    seen = load_seen()
    processed: List[str] = []
    pending: List[str] = []
    failed_retryable: List[str] = []
    details: List[str] = []

    for code in seen:
        record = get_code_record(seen, code, pid)
        if not record:
            pending.append(code)
            continue
        status = str(record.get("status", "unknown"))
        if record.get("terminal"):
            processed.append(code)
        else:
            failed_retryable.append(code)
        details.append(f"`{code}` → `{status}`")

    msg = f"📊 *{player['name']}* (`{pid}`)\n\n"
    msg += f"✅ Processed ({len(processed)}): "
    msg += ("`" + "`, `".join(processed) + "`" if processed else "_none_") + "\n"
    msg += f"🔄 Retryable ({len(failed_retryable)}): "
    msg += ("`" + "`, `".join(failed_retryable) + "`" if failed_retryable else "_none_") + "\n"
    msg += f"⏳ No record yet ({len(pending)}): "
    msg += ("`" + "`, `".join(pending) + "`" if pending else "_none_")

    if details:
        msg += "\n\n*Last known statuses:*\n" + "\n".join(details[:50])

    safe_send(message.chat.id, msg)


@bot.message_handler(commands=["resetplayer"])
def cmd_reset_player(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Usage: `/resetplayer <player_id>`", parse_mode="Markdown")
        return

    pid = parts[1].strip()
    player = next((item for item in load_players() if item["id"] == pid), None)
    if not player:
        bot.reply_to(message, f"❌ Player `{pid}` not found.", parse_mode="Markdown")
        return

    def mutator(seen: SeenData):
        count = 0
        for code in list(seen.keys()):
            if pid in seen[code]:
                del seen[code][pid]
                count += 1
            if not seen.get(code):
                seen.pop(code, None)
        return count

    count = update_seen_atomic(mutator)
    bot.reply_to(
        message,
        f"🔄 Reset *{count}* code record(s) for *{player['name']}* — they will be reprocessed on the next check.",
        parse_mode="Markdown",
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

    code = parts[1].strip().upper()

    def mutator(seen: SeenData):
        count = len(seen.get(code, {}))
        if code in seen:
            del seen[code]
        return count

    count = update_seen_atomic(mutator)
    if count == 0:
        bot.reply_to(message, f"⚠️ Code `{code}` is not in the tracked list.", parse_mode="Markdown")
        return

    bot.reply_to(
        message,
        f"✅ Cleared `{code}` — removed {count} status record(s).\nWill re-run for all players on next check.",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["checkcode"])
def cmd_checkcode(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return

    if _check_running.is_set():
        bot.reply_to(message, "⏳ A check is already in progress. Please wait...", parse_mode="Markdown")
        return

    bot.reply_to(message, "🔍 Running a gift code check right now...")
    threading.Thread(target=check_and_redeem, daemon=True).start()


@bot.message_handler(commands=["nextcheck"])
def cmd_next_check(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Access denied.")
        return

    if _check_running.is_set():
        bot.reply_to(message, "🔄 A check is currently running...", parse_mode="Markdown")
        return

    bot.reply_to(
        message,
        f"⏰ Next scheduled check at: `{get_next_check_str()}`",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["status"])
def cmd_status(message):
    players = load_players()
    seen = load_seen()
    total_records = sum(len(records) for records in seen.values())
    total_processed = sum(
        1
        for records in seen.values()
        for record in records.values()
        if record.get("terminal")
    )
    total_retryable = total_records - total_processed
    check_status = "🔄 *Running now*" if _check_running.is_set() else f"⏰ Next: `{get_next_check_str()}`"
    bot.reply_to(message, (
        "✅ *KingShot Bot — Status*\n\n"
        f"👥 Players registered : `{len(players)}`\n"
        f"🎁 Codes tracked      : `{len(seen)}`\n"
        f"🗂 Total records      : `{total_records}`\n"
        f"✅ Processed records  : `{total_processed}`\n"
        f"🔄 Retryable records  : `{total_retryable}`\n"
        f"⏱  Check interval     : every `{CHECK_INTERVAL}` min\n"
        f"🕐 Uptime             : `{get_uptime()}`\n"
        f"📡 Scheduler          : {check_status}\n"
        f"🖥  Mode               : Polling"
    ), parse_mode="Markdown")


def run_serialized_redemption(job_name: str, func: Callable[[], None]):
    log.info("Waiting for redemption lock: %s", job_name)
    with _redeem_job_lock:
        log.info("Acquired redemption lock: %s", job_name)
        return func()


def fetch_active_codes() -> List[str]:
    for attempt in range(1, 4):
        try:
            response = curl_requests.get(API_URL, impersonate="chrome120", timeout=15)
            response.raise_for_status()
            data = response.json()
            raw = (
                data.get("data", {}).get("giftCodes")
                or data.get("giftCodes")
                or data.get("codes")
                or data.get("data")
                or []
            )
            if not isinstance(raw, list):
                log.warning("Unexpected API response shape: %s", type(raw))
                return []

            result: List[str] = []
            seen_codes = set()
            for item in raw:
                code_val = ""
                if isinstance(item, str) and item.strip():
                    code_val = item.strip()
                elif isinstance(item, dict):
                    code_val = str(
                        item.get("code")
                        or item.get("gift_code")
                        or item.get("giftCode")
                        or item.get("name")
                        or ""
                    ).strip()

                normalized = code_val.upper()
                if code_val and normalized not in seen_codes:
                    seen_codes.add(normalized)
                    result.append(code_val)

            return result
        except Exception as exc:
            log.error("API fetch error (attempt %s/3): %s", attempt, exc)

        if attempt < 3:
            time.sleep(5)

    return []


def redeem_for_new_player(pid: str, name: str, codes: List[str]):
    def job():
        ok_count = 0
        terminal_count = 0
        retryable_count = 0

        for code in codes:
            if has_processed(load_seen(), code, pid):
                continue

            log.info("Redeeming old code [%s] for new player %s (%s)", code, name, pid)
            results = redeem_code_for_players(code, [(pid, name)], log)
            persist_results(code, results)
            result = results.get(pid, {})

            if result.get("success"):
                ok_count += 1
            elif result.get("terminal"):
                terminal_count += 1
            else:
                retryable_count += 1

        notify_admins(
            f"✅ Finished catch-up for *{name}* (`{pid}`)\n"
            f"  ✅ {ok_count} satisfied  ⛔ {terminal_count} terminal  ❌ {retryable_count} retryable"
        )

    return run_serialized_redemption(f"catchup:{pid}", job)


def check_and_redeem():
    if _check_running.is_set():
        log.warning("check_and_redeem already running — skipping this cycle.")
        return

    _check_running.set()
    try:
        def job():
            log.info("─── Checking for new gift codes ───")
            active_codes = fetch_active_codes()
            if not active_codes:
                log.info("No active codes returned from API.")
                return

            log.info("API returned %s code(s): %s", len(active_codes), active_codes)
            players = load_players()
            if not players:
                log.warning("No players configured — skipping redemption.")
                return

            seen = load_seen()
            any_work_done = False

            for code in active_codes:
                is_new = code.upper() not in seen
                pending = [
                    (player["id"], player["name"])
                    for player in players
                    if not has_processed(seen, code, player["id"])
                ]
                if not pending:
                    log.info("[%s] All %s players already processed. Skipping.", code, len(players))
                    continue

                any_work_done = True
                if is_new:
                    notify_admins(
                        f"🆕 *New gift code detected!*\nCode: `{code}`\nProcessing for *{len(pending)}* player(s)..."
                    )
                else:
                    log.info("[%s] Retrying %s pending player(s).", code, len(pending))
                    notify_admins(f"🔄 Retrying `{code}` for *{len(pending)}* pending player(s)...")

                results = redeem_code_for_players(code, pending, log)
                persist_results(code, results)

                for pid, name in pending:
                    result = results.get(pid, {})
                    status = result.get("status", "unknown")
                    message = result.get("message", "")
                    terminal = bool(result.get("terminal"))
                    success = bool(result.get("success"))

                    if success:
                        log.info("  ✅ %s (%s) → %s [%s]", name, pid, code, status)
                    elif terminal:
                        log.warning(
                            "  ⛔ %s (%s) → %s [%s] (terminal, no retry) | %s",
                            name, pid, code, status, message,
                        )
                    else:
                        log.warning(
                            "  ❌ %s (%s) → %s [%s] (will retry next cycle) | %s",
                            name, pid, code, status, message,
                        )

                satisfied, terminal_count, retryable = summarize_results(results)
                notify_admins(
                    f"📊 `{code}`: ✅ {satisfied} satisfied  ⛔ {terminal_count} terminal  ❌ {retryable} retryable"
                )

                seen = load_seen()

            if not any_work_done:
                log.info("Nothing to do — all players are up to date.")
            else:
                notify_admins("✅ *Redemption round complete!*")

            log.info("─── Check complete ───\n")

        run_serialized_redemption("scheduled_check", job)
    except Exception as exc:
        log.error("Unexpected error in check_and_redeem: %s", exc, exc_info=True)
        notify_admins(f"❌ Error during code check: `{exc}`")
    finally:
        _check_running.clear()


def main():
    log.info("╔═════════════════════════════════════════╗")
    log.info("║  KingShot Auto Gift Code Bot (Ubuntu)   ║")
    log.info("╚═════════════════════════════════════════╝")
    log.info("Admin IDs      : %s", ADMIN_IDS)
    log.info("Check interval : %s min", CHECK_INTERVAL)
    log.info("API endpoint   : %s", API_URL)

    if not ADMIN_IDS:
        log.critical("❌ No valid ADMIN_IDS configured! Set ADMIN_IDS in .env or service file.")
        sys.exit(1)

    if BOT_TOKEN == "PASTE_YOUR_TOKEN_HERE":
        log.critical("❌ BOT_TOKEN not configured! Set TELEGRAM_BOT_TOKEN in .env or service file.")
        sys.exit(1)

    def shutdown(signum, frame):
        log.info("Shutting down gracefully...")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    scheduler.add_job(
        check_and_redeem,
        "interval",
        minutes=CHECK_INTERVAL,
        id="gift_code_check",
    )
    scheduler.start()
    log.info("✅ Scheduler started — checks every %s min", CHECK_INTERVAL)

    threading.Thread(target=check_and_redeem, daemon=True).start()

    notify_admins(
        f"🟢 *KingShot Bot started!*\n"
        f"👥 Players: `{len(load_players())}`\n"
        f"⏱ Auto-check every `{CHECK_INTERVAL}` min\n"
        f"Type /help for all commands."
    )

    while True:
        try:
            log.info("✅ Starting Telegram polling...")
            bot.infinity_polling(timeout=60, long_polling_timeout=30, skip_pending=True)
        except Exception as exc:
            log.error("❌ infinity_polling crashed: %s", exc)
            log.info("🔄 Reconnecting in %ss...", RECONNECT_DELAY)
            time.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    main()
