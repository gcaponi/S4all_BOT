import os
import json
import logging
import difflib
from typing import Dict, List

from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# CONFIG
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "supersecret")
BASE_URL = os.getenv("BASE_URL")  # es: https://s4all-bot.onrender.com
FAQ_FILE = "faq.json"

ADMIN_IDS = {
    123456789,  # <-- sostituisci con il tuo Telegram ID
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

# =========================
# LOAD FAQ
# =========================

def load_faq() -> Dict[str, str]:
    if not os.path.exists(FAQ_FILE):
        return {}
    with open(FAQ_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

FAQ_DATA: Dict[str, str] = load_faq()
logger.info("FAQ caricate: %s", len(FAQ_DATA))


# =========================
# FUZZY SEARCH
# =========================

def fuzzy_search(query: str, data: Dict[str, str]) -> List[str]:
    keys = list(data.keys())
    matches = difflib.get_close_matches(
        query,
        keys,
        n=3,
        cutoff=0.45,  # soglia reale (non troppo alta)
    )
    return matches


# =========================
# TELEGRAM HANDLERS
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Benvenuto!\nScrivi una domanda oppure usa /help"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not FAQ_DATA:
        await update.message.reply_text("⚠️ Nessuna FAQ disponibile.")
        return

    text = "📚 *FAQ disponibili:*\n\n"
    for q in FAQ_DATA.keys():
        text += f"• {q}\n"

    await update.message.reply_text(text, parse_mode="Markdown")


async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    await update.message.reply_text(
        "/lista_autorizzati\n"
        "/admin_help\n"
    )


async def lista_autorizzati(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    text = "👮 Admin autorizzati:\n\n"
    for admin in ADMIN_IDS:
        text += f"• {admin}\n"

    await update.message.reply_text(text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()

    if not FAQ_DATA:
        await update.message.reply_text("⚠️ FAQ vuote.")
        return

    # match diretto
    if query in FAQ_DATA:
        await update.message.reply_text(FAQ_DATA[query])
        return

    # fuzzy
    matches = fuzzy_search(query, FAQ_DATA)

    if matches:
        text = "🤔 Intendevi:\n\n"
        for m in matches:
            text += f"• {m}\n"
        await update.message.reply_text(text)
    else:
        await update.message.reply_text("❌ Nessuna FAQ trovata.")


# =========================
# TELEGRAM APPLICATION
# =========================

application = Application.builder().token(BOT_TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_cmd))
application.add_handler(CommandHandler("admin_help", admin_help))
application.add_handler(CommandHandler("lista_autorizzati", lista_autorizzati))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))


# =========================
# FLASK APP
# =========================

app = Flask(__name__)

@app.route("/", methods=["GET", "HEAD"])
def index():
    return "OK", 200


@app.route("/health", methods=["GET", "HEAD"])
def health():
    return jsonify(status="ok"), 200


@app.route("/webhook", methods=["POST"])
async def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return "Unauthorized", 403

    update = Update.de_json(request.json, application.bot)
    await application.process_update(update)
    return "OK", 200


# =========================
# WEBHOOK SETUP (ON BOOT)
# =========================

async def setup_webhook():
    webhook_url = f"{BASE_URL}/webhook"
    await application.bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET,
    )
    logger.info("Webhook impostato: %s", webhook_url)


import asyncio
asyncio.get_event_loop().run_until_complete(setup_webhook())
