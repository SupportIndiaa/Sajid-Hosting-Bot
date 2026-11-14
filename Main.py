#!/usr/bin/env python3
"""
Sajid Hosting Bot V24 — Simple English + Free 1 Credit
Requires: python-telegram-bot>=22.8
"""

from __future__ import annotations
import asyncio, html, json, logging, os, re, secrets, shutil, signal, subprocess
import sys, time, zipfile, threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, InputFile,
                      KeyboardButton, ReplyKeyboardMarkup, Update)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (ApplicationBuilder, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

# ═══════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════
TOKEN = "9273345591:AAE9JzfnowWb-fqBWJLYmtGQ9psMZlK8H70"
OWNER_ID = 8524111964

BRAND_NAME = "Sajid Hosting Bot"
OWNER_USERNAME = "NextTime002"
MAIN_CHANNEL_LINK = "https://t.me/TechnoSajidYT"
SUPPORT_LINK = "https://t.me/NextTime002"

MAX_FILE_MB = 20
MAX_ZIP_EXPANDED_MB = 100
DEFAULT_CREDITS = 1          # ⭐ Free 1 credit for everyone
CREDIT_PER_UPLOAD = 1
DAILY_UPLOAD_LIMIT = 1
MAX_RUNNING_PER_USER = 2
LAUNCH_CHECK_SECONDS = 2.0
MAX_LOG_CHARS = 3000
INSTALL_TIMEOUT_SECONDS = 180
HEALTH_PORT = int(os.getenv("PORT", "8080"))
APPROVAL_TTL_HOURS = 24

ALLOWED_EXTENSIONS = {
    ".py", ".zip",
    ".txt", ".json", ".env", ".cfg", ".ini", ".yaml", ".yml", ".toml",
    ".md", ".requirements",
}

# ═══════════════════════════════════════════════════════════
# PATHS
# ═══════════════════════════════════════════════════════════
BASE_DIR = Path(os.getenv("RENDER_DATA_DIR", Path(__file__).resolve().parent))
DOWNLOADS_DIR = BASE_DIR / "downloads"
PENDING_DIR = BASE_DIR / "pending_uploads"
DATA_FILE = BASE_DIR / "bot_data.json"
PROJECTS_FILE = BASE_DIR / "projects.json"
GIFTCODES_FILE = BASE_DIR / "giftcodes.json"
DOWNLOADS_DIR.mkdir(exist_ok=True)
PENDING_DIR.mkdir(exist_ok=True)

START_TIME = time.time()
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("SajidHostingBot")

# ═══════════════════════════════════════════════════════════
# STATE
# ═══════════════════════════════════════════════════════════
premium_users: set[int] = set()
banned_users: set[int] = set()
user_credits: dict[str, int] = {}
user_stats: dict[str, dict] = {}
running_scripts: dict[int, list["ScriptProcess"]] = {}
pending_names: dict[int, "ScriptProcess"] = {}
user_locks: dict[int, asyncio.Lock] = {}
maintenance_mode = False
bot_enabled = True
emoji_enabled = True
pending_approvals: dict[str, dict] = {}
gift_codes: dict[str, dict] = {}

@dataclass
class ScriptProcess:
    display_name: str
    entry_file: str
    folder: str
    log_path: str
    proc: subprocess.Popen | None = None
    started_at: float = field(default_factory=time.time)
    restarts: int = 0
    desired_running: bool = False

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def pid(self) -> str:
        return str(self.proc.pid) if self.proc else "—"

    def serialize(self) -> dict:
        return {"display_name": self.display_name, "entry_file": self.entry_file,
                "folder": self.folder, "log_path": self.log_path,
                "started_at": self.started_at, "restarts": self.restarts,
                "was_running": self.running or self.desired_running}

# ═══════════════════════════════════════════════════════════
# EMOJI HELPER
# ═══════════════════════════════════════════════════════════
import re as _re_emoji
EMOJI_PATTERN = _re_emoji.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002702-\U000027B0"
    "\U00002600-\U000026FF"
    "\U0001F1E0-\U0001F1FF"
    "\U0000FE00-\U0000FE0F"
    "\U0001F004"
    "\U0001F0CF"
    "\U0001F170-\U0001F251"
    "]+", flags=_re_emoji.UNICODE)

def strip_emoji(s: str) -> str:
    return EMOJI_PATTERN.sub("", s).strip()

def fmt(text: str) -> str:
    return text if emoji_enabled else strip_emoji(text)

# ═══════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════
def esc(v: Any) -> str: return html.escape(str(v))
def lock_for(uid: int) -> asyncio.Lock: return user_locks.setdefault(uid, asyncio.Lock())
def scripts_for(uid: int) -> list[ScriptProcess]: return running_scripts.setdefault(uid, [])
def active_count(uid: int) -> int: return sum(i.running for i in scripts_for(uid))
def is_owner(uid: int) -> bool: return uid == OWNER_ID
def is_premium(uid: int) -> bool: return is_owner(uid) or uid in premium_users

def plan_name(uid: int) -> str:
    if is_owner(uid): return "👑 Owner"
    if is_premium(uid): return "💎 Premium"
    return "✅ Free User"

def get_credits(uid: int) -> int:
    key = str(uid)
    if key not in user_credits:
        user_credits[key] = DEFAULT_CREDITS
        save_data()
    return user_credits[key]

def set_credits(uid: int, v: int):
    user_credits[str(uid)] = max(0, v); save_data()

def add_credits(uid: int, amount: int) -> int:
    cur = get_credits(uid)
    new = max(0, cur + amount)
    user_credits[str(uid)] = new
    save_data()
    return new

def get_stat(uid: int) -> dict:
    k = str(uid)
    if k not in user_stats:
        user_stats[k] = {"uploads_today": 0, "last_upload_date": "", "uploads_total": 0}
    return user_stats[k]

def reset_daily(uid: int):
    s = get_stat(uid)
    if s.get("last_upload_date") != date.today().isoformat():
        s["last_upload_date"] = date.today().isoformat()
        s["uploads_today"] = 0
        save_data()

def daily_limit(uid: int) -> int: return 999999 if is_owner(uid) else DAILY_UPLOAD_LIMIT
def running_limit(uid: int) -> int: return 999999 if is_owner(uid) else MAX_RUNNING_PER_USER

def uptime(start: float = START_TIME) -> str:
    s = int(time.time() - start)
    d, s = divmod(s, 86400); h, s = divmod(s, 3600); m, s = divmod(s, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h or d: parts.append(f"{h}h")
    if m or h or d: parts.append(f"{m}m")
    if not parts: parts.append(f"{s}s")
    return " ".join(parts)

def clean_filename(n: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", Path(n).name)[:120] or "file.bin"

def clean_project_name(n: str) -> str:
    return re.sub(r"[^A-Za-z0-9_. -]", "_", n).strip()[:80] or "project"

def user_folder(uid: int) -> Path:
    f = DOWNLOADS_DIR / str(uid); f.mkdir(parents=True, exist_ok=True); return f

def find_project(uid: int, target: str) -> tuple[int, ScriptProcess] | None:
    tf = target.casefold()
    for i, item in enumerate(scripts_for(uid)):
        if item.display_name.casefold() == tf or Path(item.entry_file).name.casefold() == tf:
            return i, item
    return None

def safe_remove(folder: str, base: Path = DOWNLOADS_DIR):
    try:
        p = Path(folder).resolve()
        p.relative_to(base.resolve())
        shutil.rmtree(p, ignore_errors=True)
    except (ValueError, OSError):
        pass

def safe_extract(zip_path: Path, dest: Path):
    total = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            total += info.file_size
            if total > MAX_ZIP_EXPANDED_MB * 1024 * 1024:
                raise ValueError(f"ZIP exceeds {MAX_ZIP_EXPANDED_MB} MB")
            target = (dest / info.filename).resolve()
            target.relative_to(dest.resolve())
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("Symlinks not allowed")
        z.extractall(dest)

def detect_entry(folder: Path) -> Path | None:
    for name in ("main.py", "bot.py", "app.py", "index.py", "server.py", "run.py"):
        m = sorted(folder.rglob(name))
        if m: return m[0]
    files = sorted(folder.rglob("*.py"))
    return files[0] if files else None

def run_command(entry: Path) -> list[str] | None:
    if entry.suffix.lower() != ".py": return None
    py = sys.executable
    for parent in (entry.parent, *entry.parents):
        candidate = parent / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if candidate.exists(): py = str(candidate); break
    return [py, "-u", str(entry)]

def spawn_script(item: ScriptProcess | None, entry: Path, folder: Path,
                 log_path: Path) -> ScriptProcess:
    cmd = run_command(entry)
    if not cmd: raise ValueError("Unsupported entry")
    log = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)
    log.write(f"\n===== Launch {datetime.now().isoformat()} =====\n")
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(cmd, cwd=str(folder), stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env,
                            start_new_session=(os.name != "nt"))
    if item is None:
        item = ScriptProcess(display_name=entry.name, entry_file=str(entry),
                             folder=str(folder), log_path=str(log_path), proc=proc)
    else:
        item.proc = proc; item.started_at = time.time()
    item.desired_running = True
    save_projects()
    return item

def kill_process(item: ScriptProcess) -> bool:
    if not item.running:
        item.desired_running = False
        return True
    proc = item.proc
    try:
        if os.name == "nt": proc.terminate()
        else: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try: proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "nt": proc.kill()
            else: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        item.desired_running = False
        save_projects()
        return True
    except Exception:
        logger.exception("Kill failed"); return False

def generate_code(prefix: str = "GIFT") -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    body = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{prefix}-{body}"

# ═══════════════════════════════════════════════════════════
# STORAGE
# ═══════════════════════════════════════════════════════════
def load_data():
    global premium_users, banned_users, user_stats, user_credits, maintenance_mode, gift_codes, bot_enabled, emoji_enabled
    if DATA_FILE.exists():
        try:
            d = json.loads(DATA_FILE.read_text("utf-8"))
            premium_users = {int(x) for x in d.get("premium_users", [])}
            banned_users = {int(x) for x in d.get("banned_users", [])}
            user_stats = d.get("user_stats", {})
            user_credits = {str(k): int(v) for k, v in d.get("user_credits", {}).items()}
            maintenance_mode = bool(d.get("maintenance_mode", False))
            bot_enabled = bool(d.get("bot_enabled", True))
            emoji_enabled = bool(d.get("emoji_enabled", True))
        except Exception: logger.exception("Load data failed")
    if PROJECTS_FILE.exists():
        try:
            d = json.loads(PROJECTS_FILE.read_text("utf-8"))
            for uid, rows in d.items():
                items = []
                for r in rows:
                    entry, folder = Path(r["entry_file"]), Path(r["folder"])
                    if entry.exists() and folder.exists():
                        items.append(ScriptProcess(
                            display_name=r.get("display_name", entry.name),
                            entry_file=str(entry), folder=str(folder),
                            log_path=r.get("log_path", str(folder / "runtime.log")),
                            started_at=r.get("started_at", time.time()),
                            restarts=r.get("restarts", 0),
                            desired_running=r.get("was_running", False)))
                running_scripts[int(uid)] = items
        except Exception: logger.exception("Load projects failed")
    if GIFTCODES_FILE.exists():
        try:
            gift_codes = json.loads(GIFTCODES_FILE.read_text("utf-8"))
        except Exception: logger.exception("Load gift codes failed")

def save_data():
    payload = {"premium_users": sorted(premium_users), "banned_users": sorted(banned_users),
               "user_stats": user_stats, "user_credits": user_credits,
               "maintenance_mode": maintenance_mode,
               "bot_enabled": bot_enabled, "emoji_enabled": emoji_enabled}
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), "utf-8"); tmp.replace(DATA_FILE)

def save_projects():
    payload = {str(uid): [i.serialize() for i in items] for uid, items in running_scripts.items()}
    tmp = PROJECTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), "utf-8"); tmp.replace(PROJECTS_FILE)

def save_gift_codes():
    tmp = GIFTCODES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(gift_codes, indent=2), "utf-8"); tmp.replace(GIFTCODES_FILE)

# ═══════════════════════════════════════════════════════════
# UI — SIMPLE ENGLISH + ALL BLUE
# ═══════════════════════════════════════════════════════════
def main_menu(uid: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(fmt("📤 Upload File"), style="primary"), KeyboardButton(fmt("📁 My Files"), style="primary")],
        [KeyboardButton(fmt("📋 Show Logs"), style="primary"), KeyboardButton(fmt("💰 My Coins"), style="primary")],
        [KeyboardButton(fmt("🎁 Gift Code"), style="primary"), KeyboardButton(fmt("👤 My Profile"), style="primary")],
        [KeyboardButton(fmt("🛟 Get Help"), style="primary")],
    ]
    if is_owner(uid):
        rows.append([KeyboardButton(fmt("⚙️ Admin Panel"), style="primary")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)

def home_text(uid: int, first_name: str = "User") -> str:
    reset_daily(uid); s = get_stat(uid)
    plan = plan_name(uid)
    running = f"{active_count(uid)}/{running_limit(uid)}"
    uploads = f"{s['uploads_today']}/{daily_limit(uid)}"
    credits = get_credits(uid)
    bot_uptime = uptime()
    return fmt(
        f"╔══════════════════════════╗\n"
        f"👑 <b>𝐒𝐀𝐉𝐈𝐃 𝐇𝐎𝐒𝐓𝐈𝐍𝐆 𝐁𝐎𝐓</b> 👑\n"
        f"╚══════════════════════════╝\n\n"
        f"👋 <b>Hello, {esc(first_name)}!</b>\n\n"
        f"✨ Welcome to <b>Sajid Hosting Bot</b>.\n\n"
        f"☁️ Your personal Telegram bot hosting platform!\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💎 <b>OUR FEATURES</b>\n\n"
        f"⚡ Fast & reliable hosting\n"
        f"🤖 Python bot deploy\n"
        f"📂 Easy file upload\n"
        f"📊 Hosting dashboard\n"
        f"🔄 Restart & manage bot\n"
        f"🛡️ Secure hosting\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 <b>YOUR STATUS</b>\n\n"
        f"🏅 <b>Plan:</b> <code>{plan}</code>\n"
        f"🆔 <b>Your ID:</b> <code>{uid}</code>\n"
        f"🖥 <b>Running:</b> <code>{running}</code>\n"
        f"📤 <b>Uploads today:</b> <code>{uploads}</code>\n"
        f"💰 <b>Coins:</b> <code>{credits}</code>\n"
        f"⏱ <b>Uptime:</b> <code>{bot_uptime}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🚀 <b>READY TO START?</b>\n\n"
        f"Choose an option from the menu below.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👑 <b>POWERED BY SAJID</b>\n"
        f"💙 Trusted • Simple • Fast"
    )

def projects_text(uid: int) -> str:
    items = scripts_for(uid)
    if not items: return fmt("<b>📁 My Files</b>\n\n<b>No files yet.</b>")
    lines = [fmt("<b>📁 My Files</b>"), ""]
    for i, item in enumerate(items, 1):
        st = fmt("🟢 Running") if item.running else fmt("🔴 Stopped")
        lines.append(f"<b>{i}. {esc(item.display_name)}</b>\n   {st} | <b>PID:</b> <code>{item.pid}</code>")
    return "\n\n".join(lines)

def project_buttons(uid: int) -> InlineKeyboardMarkup:
    rows = []
    for i, item in enumerate(scripts_for(uid)):
        icon = "🟢" if item.running else "🔴"
        label = f"{icon} {item.display_name[:28]}" if emoji_enabled else f"{item.display_name[:30]}"
        rows.append([InlineKeyboardButton(label, callback_data=f"p:view:{i}", style="primary")])
    rows.append([InlineKeyboardButton(fmt("🔄 Refresh List"), callback_data="p:list", style="primary")])
    return InlineKeyboardMarkup(rows)

def control_buttons(idx: int, running: bool) -> InlineKeyboardMarkup:
    primary = (InlineKeyboardButton(fmt("⏹ Stop"), callback_data=f"p:stop:{idx}", style="primary")
               if running
               else InlineKeyboardButton(fmt("▶️ Start"), callback_data=f"p:start:{idx}", style="primary"))
    return InlineKeyboardMarkup([
        [primary, InlineKeyboardButton(fmt("🔄 Restart"), callback_data=f"p:restart:{idx}", style="primary")],
        [InlineKeyboardButton(fmt("📋 Show Logs"), callback_data=f"p:logs:{idx}", style="primary"),
         InlineKeyboardButton(fmt("🗑 Delete"), callback_data=f"p:delete:{idx}", style="primary")],
        [InlineKeyboardButton(fmt("◀️ Go Back"), callback_data="p:list", style="primary")]])

def approval_buttons(req_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(fmt("✅ Approve"), callback_data=f"appr:ok:{req_id}", style="primary"),
         InlineKeyboardButton(fmt("❌ Reject"), callback_data=f"appr:no:{req_id}", style="primary")],
        [InlineKeyboardButton(fmt("📥 Download File"), callback_data=f"appr:dl:{req_id}", style="primary")]])

def admin_buttons() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(fmt("📊 Show Stats"), callback_data="a:overview", style="primary"),
         InlineKeyboardButton(fmt("👥 All Users"), callback_data="a:users", style="primary")],
        [InlineKeyboardButton(fmt("🖥 All Files"), callback_data="a:proc", style="primary"),
         InlineKeyboardButton(fmt("🚫 Blocked Users"), callback_data="a:banned", style="primary")],
        [InlineKeyboardButton(fmt("⏳ Waiting Files"), callback_data="a:pending", style="primary"),
         InlineKeyboardButton(fmt("🧹 Clean Old"), callback_data="a:cleanup", style="primary")],
        [InlineKeyboardButton(fmt("💰 Add Coins"), callback_data="a:addcredits", style="primary"),
         InlineKeyboardButton(fmt("🎁 Gift Codes"), callback_data="a:giftcodes", style="primary")],
    ]
    bot_label = fmt("🔴 Bot OFF") if bot_enabled else fmt("🟢 Bot ON")
    rows.append([InlineKeyboardButton(bot_label, callback_data="a:togglebot", style="primary")])
    emoji_label = fmt("😀 Emoji OFF") if emoji_enabled else fmt("🚫 Emoji ON")
    rows.append([InlineKeyboardButton(emoji_label, callback_data="a:toggleemoji", style="primary")])
    rows.extend([
        [InlineKeyboardButton(fmt("▶️ Start All"), callback_data="a:startall", style="primary"),
         InlineKeyboardButton(fmt("⏹ Stop All"), callback_data="a:stopall", style="primary")],
        [InlineKeyboardButton(fmt("🛠 Maintenance"), callback_data="a:maint", style="primary"),
         InlineKeyboardButton(fmt("✖ Close"), callback_data="a:close", style="primary")],
    ])
    return InlineKeyboardMarkup(rows)

def admin_overview() -> str:
    all_items = [i for items in running_scripts.values() for i in items]
    active = sum(i.running for i in all_items)
    bot_status = fmt("🟢 ON") if bot_enabled else fmt("🔴 OFF")
    emoji_status = fmt("🟢 ON") if emoji_enabled else fmt("🔴 OFF")
    return fmt(
        f"<b>👑 {esc(BRAND_NAME)} — Admin Panel</b>\n\n"
        f"<b>Bot:</b> {bot_status}\n"
        f"<b>Emoji:</b> {emoji_status}\n"
        f"<b>Total Users:</b> <code>{len(user_stats)}</code>\n"
        f"<b>Premium Users:</b> <code>{len(premium_users)}</code>\n"
        f"<b>Blocked Users:</b> <code>{len(banned_users)}</code>\n"
        f"<b>Waiting Files:</b> <code>{len(pending_approvals)}</code>\n"
        f"<b>Gift Codes:</b> <code>{len(gift_codes)}</code>\n"
        f"<b>All Files:</b> <code>{len(all_items)}</code>\n"
        f"<b>Running Files:</b> <code>{active}</code>\n"
        f"<b>Maintenance:</b> <code>{'ON' if maintenance_mode else 'OFF'}</code>\n"
        f"<b>Uptime:</b> <code>{uptime()}</code>")

# ═══════════════════════════════════════════════════════════
# GUARD
# ═══════════════════════════════════════════════════════════
async def guard(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    u, m = update.effective_user, update.effective_message
    if not u or not m: return False
    if u.id in banned_users:
        await m.reply_text(fmt("🚫 <b>You are blocked.</b>"), parse_mode=ParseMode.HTML); return False
    if not bot_enabled and not is_owner(u.id):
        await m.reply_text(fmt("🔴 <b>Bot is OFF</b>\n\n<i>Please try again later.</i>"),
                           parse_mode=ParseMode.HTML); return False
    if maintenance_mode and not is_owner(u.id):
        await m.reply_text(fmt("🛠 <b>Under maintenance</b>\n\n<i>Please try again later.</i>"),
                           parse_mode=ParseMode.HTML); return False
    return True

# ═══════════════════════════════════════════════════════════
# USER COMMANDS
# ═══════════════════════════════════════════════════════════
async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not u: return
    if u.id in banned_users: return await update.message.reply_text(fmt("🚫 You are blocked."))
    if not bot_enabled and not is_owner(u.id):
        return await update.message.reply_text(fmt("🔴 <b>Bot is OFF</b>"), parse_mode=ParseMode.HTML)
    if maintenance_mode and not is_owner(u.id):
        return await update.message.reply_text(fmt("🛠 <b>Under maintenance</b>"), parse_mode=ParseMode.HTML)
    # Give free 1 credit if new user
    get_credits(u.id)
    await update.message.reply_text(home_text(u.id, u.first_name or "User"),
                                    parse_mode=ParseMode.HTML,
                                    reply_markup=main_menu(u.id))

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx): return
    await update.message.reply_text(fmt(
        "<b>📖 All Commands</b>\n\n"
        "<b>/start</b> — Main menu\n"
        "<b>/projects</b> — My files\n"
        "<b>/logs [name]</b> — Show logs\n"
        "<b>/credits</b> — My coins\n"
        "<b>/redeem CODE</b> — Gift code\n"
        "<b>/myrequests</b> — My waiting files\n"
        "<b>/skip</b> — Skip name\n"
        "<b>/restart NAME</b> — Restart file\n"
        "<b>/stop NAME</b> — Stop file\n"
        "<b>/delete NAME</b> — Delete file\n\n"
        f"💰 <b>1 coin = 1 file upload</b>"),
        parse_mode=ParseMode.HTML)

async def projects_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx): return
    uid = update.effective_user.id
    await update.message.reply_text(projects_text(uid), parse_mode=ParseMode.HTML,
                                    reply_markup=project_buttons(uid))

async def logs_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx): return
    uid = update.effective_user.id; items = scripts_for(uid)
    if not items: return await update.message.reply_text(fmt("📭 No logs."))
    item = items[-1]
    if ctx.args:
        f = find_project(uid, " ".join(ctx.args))
        if not f: return await update.message.reply_text(fmt("❌ File not found."))
        _, item = f
    p = Path(item.log_path)
    content = p.read_text("utf-8", errors="replace")[-MAX_LOG_CHARS:] if p.exists() else "No output yet."
    await update.message.reply_text(f"<b>{esc(item.display_name)}</b>\n<pre>{esc(content)}</pre>",
                                    parse_mode=ParseMode.HTML)

async def skip_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx): return
    uid = update.effective_user.id
    if uid not in pending_names: return await update.message.reply_text(fmt("ℹ️ No name is waiting."))
    pending_names.pop(uid)
    await update.message.reply_text(fmt("✅ Original name kept."))

async def credits_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx): return
    uid = update.effective_user.id
    credits = get_credits(uid)
    files_left = credits // CREDIT_PER_UPLOAD
    await update.message.reply_text(fmt(
        f"<b>💰 My Coins</b>\n\n"
        f"<b>Your ID:</b> <code>{uid}</code>\n"
        f"<b>Coins:</b> <code>{credits}</code>\n"
        f"<b>Files you can upload:</b> <code>{files_left}</code>\n"
        f"<b>Cost per file:</b> <code>{CREDIT_PER_UPLOAD}</code> coin\n\n"
        f"<b>🎁 Redeem code:</b> <code>/redeem CODE</code>"),
        parse_mode=ParseMode.HTML)

async def stop_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx): return
    if not ctx.args: return await projects_cmd(update, ctx)
    uid = update.effective_user.id; f = find_project(uid, " ".join(ctx.args))
    if not f: return await update.message.reply_text(fmt("❌ File not found."))
    _, item = f; kill_process(item)
    await update.message.reply_text(fmt(f"🛑 <b>Stopped:</b> {esc(item.display_name)}"),
                                    parse_mode=ParseMode.HTML)

async def restart_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx): return
    if not ctx.args: return await update.message.reply_text(fmt("Usage: /restart NAME"))
    uid = update.effective_user.id; f = find_project(uid, " ".join(ctx.args))
    if not f: return await update.message.reply_text(fmt("❌ File not found."))
    _, item = f
    async with lock_for(uid):
        if item.running: kill_process(item)
        item.restarts = 0
        try:
            spawn_script(item, Path(item.entry_file), Path(item.folder), Path(item.log_path))
            await asyncio.sleep(0.5)
            txt = f"✅ <b>Restarted:</b> {esc(item.display_name)}. PID: <code>{item.pid}</code>" if item.running else "❌ File stopped after restart."
            await update.message.reply_text(fmt(txt), parse_mode=ParseMode.HTML)
        except Exception as e:
            await update.message.reply_text(fmt(f"❌ <b>Error:</b> <code>{esc(e)}</code>"),
                                             parse_mode=ParseMode.HTML)

async def delete_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx): return
    if not ctx.args: return await update.message.reply_text(fmt("Usage: /delete NAME"))
    uid = update.effective_user.id; f = find_project(uid, " ".join(ctx.args))
    if not f: return await update.message.reply_text(fmt("❌ File not found."))
    idx, item = f
    if item.running: return await update.message.reply_text(fmt("⚠️ Stop the file first."))
    safe_remove(item.folder); scripts_for(uid).pop(idx); save_projects()
    await update.message.reply_text(fmt(f"🗑 <b>Deleted:</b> {esc(item.display_name)}"),
                                    parse_mode=ParseMode.HTML)

async def rename_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx): return
    if len(ctx.args) < 2: return await update.message.reply_text(fmt("Usage: /rename OLD NEW"))
    uid = update.effective_user.id; f = find_project(uid, ctx.args[0])
    if not f: return await update.message.reply_text(fmt("❌ File not found."))
    _, item = f; item.display_name = clean_project_name(" ".join(ctx.args[1:])); save_projects()
    await update.message.reply_text(fmt(f"✅ <b>Renamed to:</b> {esc(item.display_name)}"),
                                    parse_mode=ParseMode.HTML)

async def myrequests_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx): return
    uid = update.effective_user.id
    rows = [(rid, r) for rid, r in pending_approvals.items() if r["user_id"] == uid]
    if not rows: return await update.message.reply_text(fmt("📭 No files waiting."))
    lines = [fmt("<b>⏳ Files Waiting</b>"), ""]
    for rid, r in rows:
        age_min = int((time.time() - r["created_at"]) / 60)
        lines.append(f"• <code>{esc(r['filename'])}</code> — {age_min} min")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def redeem_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx): return
    uid = update.effective_user.id
    if not ctx.args:
        return await update.message.reply_text(fmt(
            "<b>🎁 Redeem Gift Code</b>\n\n<b>Usage:</b> <code>/redeem YOUR-CODE</code>"),
            parse_mode=ParseMode.HTML)
    code = ctx.args[0].strip().upper()
    data = gift_codes.get(code)
    if not data: return await update.message.reply_text(fmt("❌ Invalid gift code."))
    if data.get("disabled"): return await update.message.reply_text(fmt("❌ This code is disabled."))
    if uid in data.get("used_by", []): return await update.message.reply_text(fmt("❌ You already used this code."))
    max_uses = int(data.get("max_uses", 1))
    if max_uses > 0 and len(data.get("used_by", [])) >= max_uses:
        return await update.message.reply_text(fmt("❌ This code is fully used."))
    expiry_str = data.get("expiry")
    if expiry_str:
        try:
            if datetime.fromisoformat(expiry_str) <= datetime.now():
                return await update.message.reply_text(fmt("❌ This code is expired."))
        except ValueError: pass
    amount = int(data.get("amount", 0))
    if amount <= 0: return await update.message.reply_text(fmt("❌ Invalid code."))
    new_total = add_credits(uid, amount)
    data.setdefault("used_by", []).append(uid); save_gift_codes()
    files_possible = new_total // CREDIT_PER_UPLOAD
    await update.message.reply_text(fmt(
        f"<b>🎉 Code Redeemed!</b>\n\n"
        f"<b>Code:</b> <code>{esc(code)}</code>\n"
        f"<b>Coins added:</b> <b>+{amount}</b> 💰\n"
        f"<b>New balance:</b> <code>{new_total}</code>\n"
        f"<b>Files you can upload:</b> <code>{files_possible}</code>"),
        parse_mode=ParseMode.HTML)
    try:
        await ctx.bot.send_message(OWNER_ID,
            f"🎁 <b>Gift Code Used</b>\n\nUser: <code>{uid}</code>\nCode: <code>{esc(code)}</code>\nCoins: +{amount}",
            parse_mode=ParseMode.HTML)
    except TelegramError: pass

# ═══════════════════════════════════════════════════════════
# OWNER COMMANDS
# ═══════════════════════════════════════════════════════════
async def admin_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text(fmt("❌ Owner only."))
    await update.message.reply_text(admin_overview(), parse_mode=ParseMode.HTML,
                                    reply_markup=admin_buttons())

async def pending_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    if not pending_approvals: return await update.message.reply_text(fmt("📭 No files waiting."))
    for rid, r in list(pending_approvals.items()):
        await update.message.reply_text(approval_text(r), parse_mode=ParseMode.HTML,
                                        reply_markup=approval_buttons(rid))

async def addcredits_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    if len(ctx.args) < 2:
        return await update.message.reply_text(fmt(
            "<b>💰 Add Coins</b>\n\n<b>Usage:</b> <code>/addcredits USER_ID AMOUNT</code>\n\n"
            "Example: <code>/addcredits 123456789 10</code>"),
            parse_mode=ParseMode.HTML)
    try:
        target = int(ctx.args[0]); amount = int(ctx.args[1])
    except ValueError: return await update.message.reply_text(fmt("❌ Invalid numbers."))
    if target <= 0 or amount == 0: return await update.message.reply_text(fmt("❌ Invalid."))
    before = get_credits(target); after = add_credits(target, amount)
    word = "Added" if amount > 0 else "Removed"
    sign = "+" if amount > 0 else ""
    await update.message.reply_text(fmt(
        f"<b>✅ Coins Updated</b>\n\n<b>User:</b> <code>{target}</code>\n"
        f"<b>{word}:</b> <b>{sign}{amount}</b> 💰\n"
        f"<b>Before:</b> <code>{before}</code> → <b>After:</b> <code>{after}</code>"),
        parse_mode=ParseMode.HTML)
    try:
        await ctx.bot.send_message(target,
            f"💰 {word}: {sign}{amount} coins\nNew balance: {after}", parse_mode=ParseMode.HTML)
    except TelegramError: pass

async def setcredits_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    if len(ctx.args) < 2: return await update.message.reply_text(fmt("Usage: /setcredits USER_ID AMOUNT"))
    try:
        target = int(ctx.args[0]); amount = int(ctx.args[1])
    except ValueError: return await update.message.reply_text(fmt("❌ Invalid."))
    set_credits(target, amount)
    await update.message.reply_text(fmt(f"✅ Set <code>{target}</code> → <code>{amount}</code>💰"),
                                    parse_mode=ParseMode.HTML)

async def usercredits_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    if not ctx.args: return await update.message.reply_text(fmt("Usage: /usercredits USER_ID"))
    try: target = int(ctx.args[0])
    except ValueError: return await update.message.reply_text(fmt("❌ Invalid ID."))
    await update.message.reply_text(fmt(f"💰 <code>{target}</code> has <b>{get_credits(target)}</b> coins"),
                                    parse_mode=ParseMode.HTML)

async def giftcode_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    if not ctx.args:
        return await update.message.reply_text(fmt(
            "<b>🎁 Create Gift Code</b>\n\n"
            "<b>Usage:</b> <code>/giftcode AMOUNT [USES] [DAYS] [CUSTOM]</code>\n\n"
            "Examples:\n"
            "<code>/giftcode 10</code> — 10 coins, 1 use\n"
            "<code>/giftcode 50 5</code> — 50 coins, 5 uses\n"
            "<code>/giftcode 100 10 7</code> — 100 coins, 10 uses, 7 days\n"
            "<code>/giftcode 25 1 0 MYGIFT</code> — custom code"),
            parse_mode=ParseMode.HTML)
    try: amount = int(ctx.args[0])
    except ValueError: return await update.message.reply_text(fmt("❌ Invalid amount."))
    if amount <= 0: return await update.message.reply_text(fmt("❌ Amount must be positive."))
    max_uses = 1
    if len(ctx.args) >= 2:
        try: max_uses = int(ctx.args[1])
        except ValueError: return await update.message.reply_text(fmt("❌ Invalid uses."))
    expiry_days = 0
    if len(ctx.args) >= 3:
        try: expiry_days = int(ctx.args[2])
        except ValueError: return await update.message.reply_text(fmt("❌ Invalid days."))
    custom = None
    if len(ctx.args) >= 4:
        custom = re.sub(r"[^A-Za-z0-9_-]", "", ctx.args[3]).upper()[:32]
        if custom in gift_codes: return await update.message.reply_text(fmt("❌ Code already exists."))
    code = custom or generate_code()
    while code in gift_codes: code = generate_code()
    expiry_str = ""
    if expiry_days > 0:
        expiry_str = (datetime.now() + timedelta(days=expiry_days)).isoformat(timespec="seconds")
    gift_codes[code] = {
        "amount": amount, "max_uses": max_uses, "used_by": [], "redeemed_at": [],
        "created_by": update.effective_user.id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "expiry": expiry_str, "disabled": False,
    }
    save_gift_codes()
    uses_text = "∞" if max_uses == 0 else str(max_uses)
    await update.message.reply_text(fmt(
        f"<b>🎁 Gift Code Created!</b>\n\n"
        f"<b>Code:</b> <code>{esc(code)}</code>\n"
        f"<b>Coins:</b> <b>{amount}</b> 💰\n"
        f"<b>Max Uses:</b> <code>{uses_text}</code>\n"
        f"<b>Expires in:</b> <code>{expiry_days}d</code>\n\n"
        f"<b>Share this:</b>\n<code>/redeem {esc(code)}</code>"),
        parse_mode=ParseMode.HTML)

async def giftcodes_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    if not gift_codes: return await update.message.reply_text(fmt("📭 No gift codes yet."))
    lines = [fmt(f"<b>🎁 All Gift Codes ({len(gift_codes)})</b>"), ""]
    for code, d in list(gift_codes.items())[:30]:
        used = len(d.get("used_by", []))
        mx = d.get("max_uses", 1)
        mx_d = "∞" if mx == 0 else str(mx)
        st = "🔴" if d.get("disabled") else "🟢"
        lines.append(f"{st} <code>{esc(code)}</code> — {d.get('amount',0)}💰 — {used}/{mx_d}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

async def giftcodeinfo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id) or not ctx.args: return
    code = ctx.args[0].strip().upper(); d = gift_codes.get(code)
    if not d: return await update.message.reply_text(fmt("❌ Code not found."))
    used_by = d.get("used_by", [])
    await update.message.reply_text(fmt(
        f"<b>🎁 Code Details</b>\n\n"
        f"<b>Code:</b> <code>{esc(code)}</code>\n"
        f"<b>Coins:</b> {d.get('amount')}💰\n"
        f"<b>Used:</b> {len(used_by)}/{d.get('max_uses', 1)}\n"
        f"<b>Status:</b> {'🔴 Disabled' if d.get('disabled') else '🟢 Active'}"),
        parse_mode=ParseMode.HTML)

async def delgiftcode_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id) or not ctx.args: return
    code = ctx.args[0].strip().upper()
    gift_codes.pop(code, None); save_gift_codes()
    await update.message.reply_text(fmt(f"✅ Deleted <code>{esc(code)}</code>."), parse_mode=ParseMode.HTML)

async def togglegiftcode_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id) or not ctx.args: return
    code = ctx.args[0].strip().upper(); d = gift_codes.get(code)
    if not d: return await update.message.reply_text(fmt("❌ Code not found."))
    d["disabled"] = not bool(d.get("disabled")); save_gift_codes()
    await update.message.reply_text(fmt(f"✅ {'Disabled' if d['disabled'] else 'Enabled'} <code>{esc(code)}</code>."),
                                    parse_mode=ParseMode.HTML)

async def premium_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id) or not ctx.args: return
    try: uid = int(ctx.args[0])
    except ValueError: return await update.message.reply_text(fmt("❌ Invalid ID."))
    premium_users.add(uid); save_data()
    await update.message.reply_text(fmt(f"💎 Premium activated for <code>{uid}</code>."), parse_mode=ParseMode.HTML)

async def ban_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id) or not ctx.args: return
    try: uid = int(ctx.args[0])
    except ValueError: return await update.message.reply_text(fmt("❌ Invalid ID."))
    banned_users.add(uid); premium_users.discard(uid)
    for item in scripts_for(uid):
        if item.running: kill_process(item)
    save_data()
    await update.message.reply_text(fmt(f"🚫 Blocked <code>{uid}</code>."), parse_mode=ParseMode.HTML)

async def unban_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id) or not ctx.args: return
    try: uid = int(ctx.args[0])
    except ValueError: return await update.message.reply_text(fmt("❌ Invalid ID."))
    banned_users.discard(uid); save_data()
    await update.message.reply_text(fmt(f"✅ Unblocked <code>{uid}</code>."), parse_mode=ParseMode.HTML)

async def maintenance_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global maintenance_mode
    if not is_owner(update.effective_user.id): return
    if not ctx.args or ctx.args[0].lower() not in {"on", "off"}:
        return await update.message.reply_text(fmt("Usage: /maintenance on|off"))
    maintenance_mode = ctx.args[0].lower() == "on"; save_data()
    await update.message.reply_text(fmt(f"🛠 <b>Maintenance:</b> {'ON' if maintenance_mode else 'OFF'}"),
                                    parse_mode=ParseMode.HTML)

async def boton_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global bot_enabled
    if not is_owner(update.effective_user.id): return
    bot_enabled = True; save_data()
    await update.message.reply_text(fmt("🟢 <b>Bot is now ON</b>"), parse_mode=ParseMode.HTML)

async def botoff_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global bot_enabled
    if not is_owner(update.effective_user.id): return
    bot_enabled = False; save_data()
    await update.message.reply_text(fmt("🔴 <b>Bot is now OFF</b>"), parse_mode=ParseMode.HTML)

async def emojion_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global emoji_enabled
    if not is_owner(update.effective_user.id): return
    emoji_enabled = True; save_data()
    await update.message.reply_text("😀 <b>Emoji is now ON</b>", parse_mode=ParseMode.HTML)

async def emojioff_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global emoji_enabled
    if not is_owner(update.effective_user.id): return
    emoji_enabled = False; save_data()
    await update.message.reply_text("<b>🚫 Emoji is now OFF</b>", parse_mode=ParseMode.HTML)

async def stopall_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    stopped = 0
    for items in running_scripts.values():
        for item in items:
            if item.running and kill_process(item): stopped += 1
    await update.message.reply_text(fmt(f"⏹ Stopped {stopped} files."), parse_mode=ParseMode.HTML)

async def startall_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return
    started = failed = 0
    for uid, items in running_scripts.items():
        async with lock_for(uid):
            for item in items:
                if item.running: continue
                try:
                    spawn_script(item, Path(item.entry_file), Path(item.folder), Path(item.log_path))
                    await asyncio.sleep(0.15); started += 1 if item.running else 0
                except Exception: failed += 1
    await update.message.reply_text(fmt(f"✅ Started: {started} | ❌ Failed: {failed}"), parse_mode=ParseMode.HTML)

# ═══════════════════════════════════════════════════════════
# APPROVAL
# ═══════════════════════════════════════════════════════════
def approval_text(r: dict) -> str:
    age_min = int((time.time() - r["created_at"]) / 60)
    return fmt(
        f"<b>⏳ New File Waiting for Approval</b>\n\n"
        f"<b>👤 User:</b> <code>{r['user_id']}</code>\n"
        f"<b>📄 File:</b> <code>{esc(r['filename'])}</code>\n"
        f"<b>📦 Size:</b> <b>{r['size_mb']:.2f} MB</b>\n"
        f"<b>🐍 Entry:</b> <code>{esc(Path(r['entry_file']).name)}</code>\n"
        f"<b>💰 Cost:</b> <b>{CREDIT_PER_UPLOAD}</b> coin\n"
        f"<b>⏱ Waiting:</b> {age_min} min")

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx): return
    uid = update.effective_user.id; user = update.effective_user
    doc = update.message.document
    if not doc: return
    fname = clean_filename(doc.file_name or "file")
    suffix = Path(fname).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        exts = " ".join(f"<code>{e}</code>" for e in sorted(ALLOWED_EXTENSIONS))
        return await update.message.reply_text(fmt(
            f"❌ <b>File type not allowed</b>\n\n<b>Allowed:</b> {exts}"),
            parse_mode=ParseMode.HTML)
    if (doc.file_size or 0) > MAX_FILE_MB * 1024 * 1024:
        return await update.message.reply_text(fmt(f"❌ Max size: <b>{MAX_FILE_MB} MB</b>."), parse_mode=ParseMode.HTML)

    async with lock_for(uid):
        reset_daily(uid); s = get_stat(uid)
        if s["uploads_today"] >= daily_limit(uid):
            return await update.message.reply_text(fmt("⚠️ Daily upload limit reached. Try tomorrow."))
        if not is_premium(uid) and get_credits(uid) < CREDIT_PER_UPLOAD:
            return await update.message.reply_text(fmt(
                f"💰 <b>Not enough coins</b>\n\nYou have: <code>{get_credits(uid)}</code>\n"
                f"Required: <code>{CREDIT_PER_UPLOAD}</code> per file\n\n"
                f"Use <code>/redeem CODE</code> to get coins."),
                parse_mode=ParseMode.HTML)
        if active_count(uid) >= running_limit(uid):
            return await update.message.reply_text(fmt(f"⚠️ You can only run {running_limit(uid)} files at once."))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    req_id = f"{stamp}_{uid}"
    pending_folder = PENDING_DIR / req_id
    pending_folder.mkdir(parents=True)
    uploaded = pending_folder / fname
    msg = await update.message.reply_text(fmt("📥 <b>Uploading…</b>"), parse_mode=ParseMode.HTML)
    try:
        tg = await ctx.bot.get_file(doc.file_id)
        await tg.download_to_drive(str(uploaded))
        if suffix == ".zip":
            safe_extract(uploaded, pending_folder); uploaded.unlink(missing_ok=True)
            entry = detect_entry(pending_folder)
        else:
            entry = detect_entry(pending_folder)
            if not entry and suffix == ".py": entry = uploaded
        if not entry:
            if suffix in {".txt", ".json", ".env", ".cfg", ".ini", ".yaml", ".yml", ".toml", ".md"}:
                entry = uploaded
            else:
                raise ValueError("No Python file found.")
        size_mb = sum(f.stat().st_size for f in pending_folder.rglob("*") if f.is_file()) / (1024 * 1024)
        pending_approvals[req_id] = {
            "user_id": uid, "username": user.username or "",
            "filename": fname, "folder": str(pending_folder),
            "entry_file": str(entry), "size_mb": size_mb, "created_at": time.time(),
        }
        await msg.edit_text(fmt(
            f"<b>📤 File sent for approval</b>\n\n"
            f"<b>📄 File:</b> <code>{esc(fname)}</code>\n"
            f"<b>📦 Size:</b> <b>{size_mb:.2f} MB</b>\n\n"
            f"⏳ <b>Please wait for owner to approve.</b>\n"
            f"Check status: <code>/myrequests</code>"),
            parse_mode=ParseMode.HTML)
        try:
            await ctx.bot.send_message(OWNER_ID, approval_text(pending_approvals[req_id]),
                                        parse_mode=ParseMode.HTML, reply_markup=approval_buttons(req_id))
        except TelegramError: logger.exception("Notify failed")
    except Exception as e:
        logger.exception("Upload failed"); shutil.rmtree(pending_folder, ignore_errors=True)
        await msg.edit_text(fmt(f"❌ <b>Upload failed:</b> <code>{esc(str(e)[:300])}</code>"),
                            parse_mode=ParseMode.HTML)

async def do_approve(ctx, req_id) -> tuple[bool, str]:
    r = pending_approvals.get(req_id)
    if not r: return False, "Request expired."
    uid = r["user_id"]; pending_folder = Path(r["folder"])
    if not pending_folder.exists():
        pending_approvals.pop(req_id, None); return False, "Folder missing."
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    final_folder = user_folder(uid) / f"{stamp}_{clean_project_name(Path(r['filename']).stem)}"
    try: shutil.move(str(pending_folder), str(final_folder))
    except Exception as e: return False, f"Move failed: {e}"
    entry = detect_entry(final_folder) or Path(r["entry_file"])
    if not Path(entry).exists():
        safe_remove(str(final_folder)); pending_approvals.pop(req_id, None)
        return False, "Entry missing."
    dep_note = "No requirements"
    req = next(iter(final_folder.rglob("requirements.txt")), None)
    if req:
        try:
            venv = final_folder / ".venv"
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=60)
            py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            subprocess.run([str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", str(req)],
                           cwd=final_folder, timeout=INSTALL_TIMEOUT_SECONDS,
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            dep_note = "Installed"
        except Exception as e: dep_note = f"Failed: {str(e)[:100]}"
    log_path = final_folder / "runtime.log"
    try: item = spawn_script(None, Path(entry), final_folder, log_path)
    except Exception as e:
        safe_remove(str(final_folder)); pending_approvals.pop(req_id, None)
        return False, f"Spawn failed: {e}"
    scripts_for(uid).append(item); save_projects()
    await asyncio.sleep(LAUNCH_CHECK_SECONDS)
    if not item.running:
        err = log_path.read_text("utf-8", errors="replace")[-MAX_LOG_CHARS:] if log_path.exists() else "No output"
        scripts_for(uid).remove(item); save_projects(); safe_remove(str(final_folder))
        pending_approvals.pop(req_id, None)
        return False, f"File stopped:\n<pre>{esc(err[:1500])}</pre>"
    if not is_premium(uid): set_credits(uid, get_credits(uid) - CREDIT_PER_UPLOAD)
    s = get_stat(uid); s["uploads_today"] += 1; s["uploads_total"] += 1
    s["last_upload_date"] = date.today().isoformat(); save_data()
    pending_approvals.pop(req_id, None)
    try:
        await ctx.bot.send_message(uid, fmt(
            f"<b>✅ Your file is now running!</b>\n\n"
            f"<b>📄 File:</b> <code>{esc(r['filename'])}</code>\n"
            f"<b>🆔 PID:</b> <code>{item.pid}</code>\n"
            f"<b>Dependencies:</b> {esc(dep_note)}\n"
            f"<b>💰 Coins left:</b> <code>{get_credits(uid)}</code>\n\n"
            f"<b>Send a name for your file, or /skip</b>"),
            parse_mode=ParseMode.HTML, reply_markup=control_buttons(len(scripts_for(uid)) - 1, True))
        pending_names[uid] = item
    except TelegramError: logger.exception("Notify failed")
    return True, f"File approved. PID={item.pid}"

async def do_reject(ctx, req_id) -> tuple[bool, str]:
    r = pending_approvals.pop(req_id, None)
    if not r: return False, "Request not found."
    safe_remove(r["folder"], base=PENDING_DIR)
    try:
        await ctx.bot.send_message(r["user_id"], fmt(
            f"<b>❌ Your file was rejected</b>\n\n"
            f"<b>📄 File:</b> <code>{esc(r['filename'])}</code>\n\n"
            f"<i>No coins were taken.</i>"),
            parse_mode=ParseMode.HTML)
    except TelegramError: pass
    return True, "File rejected."

# ═══════════════════════════════════════════════════════════
# TEXT HANDLER
# ═══════════════════════════════════════════════════════════
async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip(); u = update.effective_user
    if not u: return
    if u.id in pending_names:
        item = pending_names.pop(u.id); name = clean_project_name(text)
        if not name:
            pending_names[u.id] = item
            return await update.message.reply_text(fmt("❌ Invalid name."))
        item.display_name = name; save_projects()
        return await update.message.reply_text(fmt(f"✅ <b>Name set:</b> {esc(name)}"),
                                               parse_mode=ParseMode.HTML, reply_markup=main_menu(u.id))
    if u.id in banned_users: return
    if not bot_enabled and not is_owner(u.id):
        return await update.message.reply_text(fmt("🔴 <b>Bot is OFF</b>"), parse_mode=ParseMode.HTML)
    if maintenance_mode and not is_owner(u.id):
        return await update.message.reply_text(fmt("🛠 <b>Under maintenance</b>"), parse_mode=ParseMode.HTML)

    t = strip_emoji(text).lower()
    if t == "upload file":
        await update.message.reply_text(fmt(
            f"📤 <b>Send your file</b>\n\n"
            f"<b>Allowed:</b> <code>.py</code> <code>.zip</code> <code>.txt</code> <code>.json</code>\n\n"
            f"<b>Cost:</b> <b>{CREDIT_PER_UPLOAD}</b> coin per file\n"
            f"⏳ <b>Owner will approve it</b>"),
            parse_mode=ParseMode.HTML)
    elif t in {"my files", "show logs"}:
        await projects_cmd(update, ctx)
    elif t == "my coins":
        await credits_cmd(update, ctx)
    elif t in {"gift code", "giftcode"}:
        await update.message.reply_text(fmt(
            "🎁 <b>Redeem Gift Code</b>\n\n"
            "Use: <code>/redeem YOUR-CODE</code>"),
            parse_mode=ParseMode.HTML)
    elif t == "my profile":
        await update.message.reply_text(home_text(u.id, u.first_name or "User"), parse_mode=ParseMode.HTML)
    elif t == "get help":
        await update.message.reply_text(fmt(
            f"<b>🛟 Get Help</b>\n\n"
            f"<b>Owner:</b> @{OWNER_USERNAME}\n"
            f"<b>Channel:</b> {MAIN_CHANNEL_LINK}\n"
            f"<b>Support:</b> {SUPPORT_LINK}"),
            parse_mode=ParseMode.HTML)
    elif t == "admin panel" and is_owner(u.id):
        await admin_cmd(update, ctx)
    else:
        await update.message.reply_text(fmt("💡 <b>Use the menu or send a .py/.zip file.</b>"),
                                        parse_mode=ParseMode.HTML)

# ═══════════════════════════════════════════════════════════
# CALLBACKS
# ═══════════════════════════════════════════════════════════
async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except TelegramError: pass
    uid = q.from_user.id; data = q.data or ""

    if data.startswith("appr:"):
        if not is_owner(uid): return await q.answer("Owner only", show_alert=True)
        parts = data.split(":", 2)
        if len(parts) != 3: return
        action, req_id = parts[1], parts[2]
        r = pending_approvals.get(req_id)
        if not r:
            try: await q.edit_message_text(fmt("⚠️ Request expired."))
            except TelegramError: pass
            return
        if action == "dl":
            folder = Path(r["folder"])
            if not folder.exists(): return await q.answer("Folder missing", show_alert=True)
            zip_out = PENDING_DIR / f"{req_id}.zip"
            with zipfile.ZipFile(zip_out, "w", zipfile.ZIP_DEFLATED) as z:
                for f in folder.rglob("*"):
                    if f.is_file() and ".venv" not in f.parts:
                        z.write(f, f.relative_to(folder))
            await ctx.bot.send_document(OWNER_ID, InputFile(str(zip_out)), caption=f"📥 {r['filename']}")
            zip_out.unlink(missing_ok=True)
            return await q.answer("File sent")
        if action == "ok":
            try: await q.edit_message_text(fmt("⏳ Approving…"))
            except TelegramError: pass
            ok, detail = await do_approve(ctx, req_id)
            try: await q.edit_message_text(fmt(f"{'✅' if ok else '❌'} {esc(detail[:500])}"), parse_mode=ParseMode.HTML)
            except TelegramError: pass
            return
        if action == "no":
            try: await q.edit_message_text(fmt("⏳ Rejecting…"))
            except TelegramError: pass
            ok, detail = await do_reject(ctx, req_id)
            try: await q.edit_message_text(fmt(f"{'✅' if ok else '❌'} {esc(detail[:500])}"), parse_mode=ParseMode.HTML)
            except TelegramError: pass
            return

    if data.startswith("a:"):
        if not is_owner(uid): return await q.answer("Owner only", show_alert=True)
        action = data.split(":", 1)[1]
        try:
            if action == "close": return await q.edit_message_text(fmt("✖ <b>Closed.</b>"), parse_mode=ParseMode.HTML)
            if action == "overview":
                return await q.edit_message_text(admin_overview(), parse_mode=ParseMode.HTML, reply_markup=admin_buttons())
            if action == "users":
                rows = [f"• <code>{x}</code> — <b>{get_credits(int(x))}💰</b>" for x in sorted(user_stats.keys())[:50]]
                return await q.edit_message_text(fmt("<b>👥 All Users</b>\n\n" + ("\n".join(rows) or "None")),
                                                 parse_mode=ParseMode.HTML, reply_markup=admin_buttons())
            if action == "banned":
                rows = [f"• <code>{x}</code>" for x in sorted(banned_users)[:50]]
                return await q.edit_message_text(fmt("<b>🚫 Blocked Users</b>\n\n" + ("\n".join(rows) or "None")),
                                                 parse_mode=ParseMode.HTML, reply_markup=admin_buttons())
            if action == "proc":
                rows = [f"• <b>{esc(i.display_name)}</b> {'🟢' if i.running else '🔴'}" for items in running_scripts.values() for i in items][:60]
                return await q.edit_message_text(fmt("<b>🖥 All Files</b>\n\n" + ("\n".join(rows) or "None")),
                                                 parse_mode=ParseMode.HTML, reply_markup=admin_buttons())
            if action == "pending":
                if not pending_approvals:
                    return await q.edit_message_text(fmt("📭 <b>No files waiting.</b>"), parse_mode=ParseMode.HTML, reply_markup=admin_buttons())
                for rid, r in list(pending_approvals.items()):
                    await ctx.bot.send_message(OWNER_ID, approval_text(r), parse_mode=ParseMode.HTML, reply_markup=approval_buttons(rid))
                return await q.edit_message_text(fmt(f"📬 Sent <b>{len(pending_approvals)}</b> files."), parse_mode=ParseMode.HTML, reply_markup=admin_buttons())
            if action == "cleanup":
                removed = 0
                for rid, r in list(pending_approvals.items()):
                    if time.time() - r["created_at"] > APPROVAL_TTL_HOURS * 3600:
                        safe_remove(r["folder"], base=PENDING_DIR); pending_approvals.pop(rid, None); removed += 1
                return await q.edit_message_text(fmt(f"🧹 Cleaned <b>{removed}</b> old files."), parse_mode=ParseMode.HTML, reply_markup=admin_buttons())
            if action == "addcredits":
                return await q.edit_message_text(fmt(
                    "<b>💰 Add Coins</b>\n\n<code>/addcredits USER_ID AMOUNT</code>"),
                    parse_mode=ParseMode.HTML, reply_markup=admin_buttons())
            if action == "giftcodes":
                if not gift_codes:
                    return await q.edit_message_text(fmt("🎁 <b>No codes yet.</b>\n\n<code>/giftcode AMOUNT</code>"),
                                                    parse_mode=ParseMode.HTML, reply_markup=admin_buttons())
                rows = []
                for code, d in list(gift_codes.items())[:30]:
                    used = len(d.get("used_by", [])); mx = d.get("max_uses", 1)
                    mx_d = "∞" if mx == 0 else str(mx)
                    rows.append(f"<code>{esc(code)}</code> — <b>{d.get('amount',0)}💰</b> — {used}/{mx_d}")
                return await q.edit_message_text(fmt(f"<b>🎁 Gift Codes ({len(gift_codes)})</b>\n\n" + "\n".join(rows)),
                                                parse_mode=ParseMode.HTML, reply_markup=admin_buttons())
            if action == "togglebot":
                global bot_enabled
                bot_enabled = not bot_enabled; save_data()
                return await q.edit_message_text(admin_overview(), parse_mode=ParseMode.HTML, reply_markup=admin_buttons())
            if action == "toggleemoji":
                global emoji_enabled
                emoji_enabled = not emoji_enabled; save_data()
                return await q.edit_message_text(admin_overview(), parse_mode=ParseMode.HTML, reply_markup=admin_buttons())
            if action == "startall":
                started = failed = 0
                for u_id, items in running_scripts.items():
                    async with lock_for(u_id):
                        for item in items:
                            if item.running: continue
                            try:
                                spawn_script(item, Path(item.entry_file), Path(item.folder), Path(item.log_path))
                                await asyncio.sleep(0.1); started += 1 if item.running else 0
                            except Exception: failed += 1
                return await q.edit_message_text(fmt(f"✅ <b>{started}</b> started | ❌ <b>{failed}</b> failed"), parse_mode=ParseMode.HTML, reply_markup=admin_buttons())
            if action == "stopall":
                stopped = sum(1 for items in running_scripts.values() for item in items if item.running and kill_process(item))
                return await q.edit_message_text(fmt(f"⏹ Stopped <b>{stopped}</b> files."), parse_mode=ParseMode.HTML, reply_markup=admin_buttons())
            if action == "maint":
                global maintenance_mode
                maintenance_mode = not maintenance_mode; save_data()
                return await q.edit_message_text(admin_overview(), parse_mode=ParseMode.HTML, reply_markup=admin_buttons())
        except TelegramError: pass
        return

    if uid in banned_users: return await q.answer("You are blocked", show_alert=True)
    if data == "p:list":
        try: return await q.edit_message_text(projects_text(uid), parse_mode=ParseMode.HTML, reply_markup=project_buttons(uid))
        except TelegramError: return

    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "p": return
    try:
        action, idx = parts[1], int(parts[2]); item = scripts_for(uid)[idx]
    except (ValueError, IndexError):
        return await q.answer("Not found", show_alert=True)

    async with lock_for(uid):
        try:
            if action == "view":
                st = "🟢 Running" if item.running else "🔴 Stopped"
                return await q.edit_message_text(fmt(
                    f"<b>{esc(item.display_name)}</b>\n\n<b>Status:</b> {st}\n"
                    f"<b>PID:</b> <code>{item.pid}</code>\n"
                    f"<b>Uptime:</b> <code>{uptime(item.started_at) if item.running else '—'}</code>"),
                    parse_mode=ParseMode.HTML, reply_markup=control_buttons(idx, item.running))
            if action == "stop":
                item.desired_running = False; kill_process(item)
                return await q.edit_message_text(fmt(f"🛑 <b>Stopped:</b> {esc(item.display_name)}"), parse_mode=ParseMode.HTML, reply_markup=control_buttons(idx, False))
            if action in {"start", "restart"}:
                if item.running: kill_process(item)
                item.restarts = 0
                try:
                    spawn_script(item, Path(item.entry_file), Path(item.folder), Path(item.log_path))
                    await asyncio.sleep(0.5)
                    msg = fmt(f"✅ <b>Started</b> PID: <code>{item.pid}</code>") if item.running else fmt("❌ File stopped after start.")
                except Exception as e: msg = fmt(f"❌ <code>{esc(str(e)[:150])}</code>")
                return await q.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=control_buttons(idx, item.running))
            if action == "logs":
                p = Path(item.log_path)
                content = p.read_text("utf-8", errors="replace")[-MAX_LOG_CHARS:] if p.exists() else "No output yet."
                return await q.edit_message_text(f"<b>{esc(item.display_name)}</b>\n<pre>{esc(content)}</pre>", parse_mode=ParseMode.HTML, reply_markup=control_buttons(idx, item.running))
            if action == "delete":
                if item.running: return await q.answer("Stop the file first", show_alert=True)
                safe_remove(item.folder); scripts_for(uid).pop(idx); save_projects()
                return await q.edit_message_text(projects_text(uid), parse_mode=ParseMode.HTML, reply_markup=project_buttons(uid))
        except TelegramError: pass

# ═══════════════════════════════════════════════════════════
# HEALTH & JOBS
# ═══════════════════════════════════════════════════════════
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] not in {"/", "/health"}:
            self.send_response(404); self.end_headers(); return
        body = json.dumps({"service": BRAND_NAME, "status": "online",
                           "bot_enabled": bot_enabled, "emoji_enabled": emoji_enabled,
                           "uptime": uptime(),
                           "projects": sum(len(v) for v in running_scripts.values()),
                           "pending": len(pending_approvals),
                           "giftcodes": len(gift_codes)}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass

def start_health():
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", HEALTH_PORT), HealthHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        logger.info("%s health :%s", BRAND_NAME, HEALTH_PORT)
    except OSError as e: logger.warning("Health failed: %s", e)

async def approval_expiry_job(ctx):
    now = time.time()
    for rid, r in list(pending_approvals.items()):
        if now - r["created_at"] > APPROVAL_TTL_HOURS * 3600:
            try: await ctx.bot.send_message(r["user_id"], fmt(f"⌛ Expired: <code>{esc(r['filename'])}</code>"), parse_mode=ParseMode.HTML)
            except TelegramError: pass
            safe_remove(r["folder"], base=PENDING_DIR); pending_approvals.pop(rid, None)

async def watchdog_job(ctx):
    for uid, items in list(running_scripts.items()):
        async with lock_for(uid):
            for item in items:
                if item.running or not item.desired_running: continue
                try: spawn_script(item, Path(item.entry_file), Path(item.folder), Path(item.log_path))
                except Exception: logger.exception("Watchdog failed")

async def error_handler(update, ctx):
    err = ctx.error
    logger.error("ERROR: %s | %s", type(err).__name__, err)
    logger.exception("Traceback:", exc_info=err)
    try:
        if isinstance(update, Update) and update.effective_user:
            uname = update.effective_user.username or update.effective_user.id
            await ctx.bot.send_message(OWNER_ID, f"⚠️ {esc(uname)}\n<code>{esc(type(err).__name__)}: {esc(str(err)[:300])}</code>", parse_mode=ParseMode.HTML)
    except: pass

# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════
def main():
    if not TOKEN: raise SystemExit("TOKEN empty")
    if OWNER_ID <= 0: raise SystemExit("OWNER_ID invalid")
    load_data(); start_health()
    app = (ApplicationBuilder().token(TOKEN)
           .connect_timeout(30).read_timeout(60).write_timeout(60)
           .concurrent_updates(32).build())
    cmds = [
        ("start", start_cmd), ("help", help_cmd),
        ("projects", projects_cmd), ("logs", logs_cmd),
        ("skip", skip_cmd), ("credits", credits_cmd), ("stop", stop_cmd),
        ("restart", restart_cmd), ("delete", delete_cmd), ("rename", rename_cmd),
        ("myrequests", myrequests_cmd), ("redeem", redeem_cmd),
        ("addcredits", addcredits_cmd), ("setcredits", setcredits_cmd),
        ("usercredits", usercredits_cmd),
        ("giftcode", giftcode_cmd), ("giftcodes", giftcodes_cmd),
        ("giftcodeinfo", giftcodeinfo_cmd), ("delgiftcode", delgiftcode_cmd),
        ("togglegiftcode", togglegiftcode_cmd),
        ("boton", boton_cmd), ("botoff", botoff_cmd),
        ("emojion", emojion_cmd), ("emojioff", emojioff_cmd),
        ("admin", admin_cmd), ("pending", pending_cmd),
        ("premium", premium_cmd), ("ban", ban_cmd), ("unban", unban_cmd),
        ("maintenance", maintenance_cmd),
        ("stopalladmin", stopall_cmd), ("startall", startall_cmd),
    ]
    for name, cb in cmds: app.add_handler(CommandHandler(name, cb))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(error_handler)
    if app.job_queue:
        app.job_queue.run_repeating(watchdog_job, interval=30, first=20)
        app.job_queue.run_repeating(approval_expiry_job, interval=600, first=120)
    logger.info("%s started | Owner: %s", BRAND_NAME, OWNER_ID)
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
