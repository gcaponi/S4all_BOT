import os
import json
import logging
import asyncio
import secrets
import re
import requests

from flask import Flask, request
from bs4 import BeautifulSoup
from difflib import SequenceMatcher

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================= CONFIG =================

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
WEBHOOK_URL = os.environ["WEBHOOK_URL"]  # es: https://xxx.onrender.com/webhook

AUTHORIZED_USERS_FILE = "authorized_users.json"
ACCESS_CODE_FILE = "access_code.json"
FAQ_FILE = "faq.json"

FAQ_URL = "https://justpaste.it/faq_4all"
FUZZY_THRESHOLD = 0.6

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

log = logging.getLogger(__name__)

app = Flask(__name__)
application: Application | None = None
event_loop: asyncio.AbstractEventLoop | None = None

# ================= UTILS =================

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ================= AUTH =================

def load_authorized_users():
    return load_json(AUTHORIZED_USERS_FILE, {})


def is_authorized(user_id: int) -> bool:
    return str(user_id) in load_authorized_users()


def authorize_user(user):
    users = load_authorized_users()
    uid = str(user.id)

    if uid not in users:
        users[uid] = {
            "id": user.id,
            "name": f"{user.first_name or ''} {user.last_name or ''}".strip(),
            "username": user.username,
        }
        save_json(AUTHORIZED_USERS_FILE, users)
        return True
    return False


def load_access_code():
    data = load_json(ACCESS_CODE_FILE, {})
    if "code" not in data:
        data["code"] = secrets.token_urlsafe(12)
        save_json(ACCESS_CODE_FILE, data)
    return data["code"]


# ================= FAQ =================

def fetch_faq():
    r = requests.get(FAQ_URL, timeout=10)
    soup = BeautifulSoup(r.text, "html.parser")
    content = soup.select_one("#articleContent")
    text = content.get_text("\n")

    pattern = r"^##\s+(.*?)\n(.*?)(?=\n##|\Z)"
    matches = re.findall(pattern, text, re.S | re.M)

    faq = [{"domanda": d.strip(), "risposta": r.strip()} for d, r in matches]
    save_json(FAQ_FILE, {"faq": faq})
    log.info("FAQ aggiornate: %s", len(faq))


def load_faq():
    return load_json(FAQ_FILE, {"faq": []})["faq"]


# ================= FUZZY =================

def normalize(t: str) -> str:
    t = re.sub(r"[^\w\s]", "", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def fuzzy_search(query, faq):
    q = normalize(query)
    best, score = None, 0

    for item in faq:
        s = similarity(q, normalize(item["domanda"]))
        if s > score:
            best, score = item, s

    if score >= FUZZY_THRESHOLD:
        return best, score

    return None, score


# ================= HANDLERS =================

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if ctx.args:
        if ctx.args[0] == load_access_code():
            new = authorize_user(user)
            await update.message.reply_text(
                "✅ Accesso autorizzato!" if new else "✅ Sei già autorizzato"
            )
            return

    if not is_authorized(user.id):
        await update.message.reply_text("❌ Accesso non autorizzato")
        return

    await update.message.reply_text("👋 Scrivi la tua domanda")


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return

    faq = load_faq()
    msg = "📚 FAQ disponibili:\n\n"
    for i, f in enumerate(faq, 1):
        msg += f"{i}. {f['domanda']}\n"

    await update.message.reply_text(msg)


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return

    faq = load_faq()
    match, score = fuzzy_search(update.message.text, faq)

    if match:
        await update.message.reply_text(
            f"🎯 {match['domanda']}\n\n{match['risposta']}"
        )
    else:
        await update.message.reply_text("❓ Nessuna risposta trovata")


# ================= BOT INIT =================

async def init_bot():
    global application

    fetch_faq()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await application.initialize()
    await application.bot.set_webhook(WEBHOOK_URL)
    await application.start()

    log.info("🤖 Bot pronto")


# ================= FLASK =================

@app.route("/")
def root():
    return "OK", 200


@app.route("/health", methods=["GET", "HEAD"])
def health():
    return "OK", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.json, application.bot)
    asyncio.run_coroutine_threadsafe(
        application.process_update(update), event_loop
    )
    return "OK", 200


# ================= ENTRY =================

def start_bot():
    global event_loop
    event_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(event_loop)
    event_loop.run_until_complete(init_bot())
    event_loop.run_forever()


import threading
threading.Thread(target=start_bot, daemon=True).start()
