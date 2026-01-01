#!/usr/bin/env python3
# main.py - Bot Telegram con ricerca FAQ fuzzy e ricerca "lista" da justpaste.it
# Ottimizzato per deploy (non esegue app.run() automaticamente; usa wsgi.py con Gunicorn)

import os
import json
import logging
import signal
import asyncio
import secrets
import re
from threading import Thread

from flask import Flask, request

import httpx
from bs4 import BeautifulSoup
from difflib import SequenceMatcher

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# -----------------------
# Config & Logging
# -----------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
try:
    ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
except:
    ADMIN_CHAT_ID = 0
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()
PORT = int(os.environ.get("PORT", 10000))

AUTHORIZED_USERS_FILE = "authorized_users.json"
ACCESS_CODE_FILE = "access_code.json"
FAQ_FILE = "faq.json"

PASTE_FAQ_URL = "https://justpaste.it/faq_4all"
LISTA_URL = "https://justpaste.it/lista_4all"
LISTA_FILE = "lista.txt"

# threshold for fuzzy match (0..1)
FUZZY_THRESHOLD = 0.60

PAYMENT_KEYWORDS = [
    "contanti", "carta", "bancomat", "bonifico", "paypal", "satispay",
    "postepay", "pos", "wallet", "ricarica", "usdt", "crypto", "cripto",
    "bitcoin", "bit", "btc", "eth", "usdc",
]

# Flask app (webhook)
app = Flask(__name__)

# Bot application (initialized later)
bot_application: Application | None = None
bot_initialized = False
BOT_LOOP: asyncio.AbstractEventLoop | None = None

# -----------------------
# Utility: file I/O JSON
# -----------------------
def load_json_file(filename, default=None):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}

def save_json_file(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# -----------------------
# Authorization helpers
# -----------------------
def load_authorized_users():
    data = load_json_file(AUTHORIZED_USERS_FILE, default={})
    if isinstance(data, list):
        return {str(uid): {"id": int(uid), "name": "Sconosciuto", "username": None} for uid in data}
    return data

def save_authorized_users(users):
    save_json_file(AUTHORIZED_USERS_FILE, users)

def load_access_code():
    data = load_json_file(ACCESS_CODE_FILE, default={})
    code = data.get("code")
    if not code:
        code = secrets.token_urlsafe(12)
        save_json_file(ACCESS_CODE_FILE, {"code": code})
    return code

def save_access_code(code):
    save_json_file(ACCESS_CODE_FILE, {"code": code})

def is_user_authorized(user_id) -> bool:
    if user_id is None:
        return False
    return str(user_id) in load_authorized_users()

def authorize_user(user_id, first_name=None, last_name=None, username=None) -> bool:
    if user_id is None:
        return False
    users = load_authorized_users()
    key = str(user_id)
    if key not in users:
        full_name = " ".join(filter(None, [first_name, last_name])).strip() or "Sconosciuto"
        users[key] = {"id": int(user_id), "name": full_name, "username": username}
        save_authorized_users(users)
        return True
    return False

# -----------------------
# FAQ fetching and parsing
# -----------------------
async def fetch_markdown_from_html_async(url: str) -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        content = soup.select_one("#articleContent") or soup.select_one(".content") or soup.body
        if content is None:
            raise RuntimeError("Contenuto principale non trovato nella pagina")
        return content.get_text("\n").strip()

def parse_faq(markdown_text: str) -> list:
    pattern = r"^##\s+(.*?)\n(.*?)(?=\n##\s+|$)"
    matches = re.findall(pattern, markdown_text, flags=re.S | re.M)
    if not matches:
        lines = [l.strip() for l in markdown_text.splitlines() if l.strip()]
        return [{"domanda": l, "risposta": ""} for l in lines]
    return [{"domanda": d.strip(), "risposta": r.strip()} for d, r in matches]

async def update_faq_from_web_async() -> bool:
    try:
        md = await fetch_markdown_from_html_async(PASTE_FAQ_URL)
        faq = parse_faq(md)
        await asyncio.to_thread(lambda: save_json_file(FAQ_FILE, {"faq": faq}))
        logger.info("FAQ aggiornate: %d", len(faq))
        return True
    except Exception:
        logger.exception("Errore aggiornamento FAQ")
        return False

def load_faq() -> dict:
    return load_json_file(FAQ_FILE, default={})

# -----------------------
# Lista fetching & searching
# -----------------------
async def fetch_lista_async() -> bool:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(LISTA_URL)
            r.raise_for_status()
            text = r.text
            await asyncio.to_thread(lambda: open(LISTA_FILE, "w", encoding="utf-8").write(text))
            logger.info("Lista aggiornata correttamente")
            return True
    except Exception:
        logger.exception("Errore aggiornamento lista")
        return False

def load_lista() -> list:
    try:
        with open(LISTA_FILE, "r", encoding="utf-8") as f:
            return [l.strip() for l in f.read().splitlines() if l.strip()]
    except Exception:
        return []

def search_lista(query: str, lista_lines: list) -> list:
    q = query.lower().strip()
    if not q:
        return []
    results = [line for line in lista_lines if q in line.lower()]
    return results

# -----------------------
# Fuzzy FAQ search
# -----------------------
def normalize_text(text: str) -> str:
    if not text:
        return ""
    t = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t

def extract_keywords(text: str) -> list:
    words = normalize_text(text).split()
    stop_words = {
        "che", "sono", "come", "dove", "quando", "quale", "quali",
        "del", "della", "dei", "delle", "con", "per", "una", "uno",
        "il", "lo", "la", "le", "e", "o", "a", "di", "da", "in"
    }
    return [w for w in words if len(w) > 2 and w not in stop_words]

def calculate_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()

def fuzzy_search_faq(user_message: str, faq_list: list) -> dict:
    logger.info("Ricerca fuzzy per: %s", user_message)
    user_normalized = normalize_text(user_message)
    user_keywords = extract_keywords(user_message)
    best_match = None
    best_score = 0.0
    match_method = None

    for item in faq_list:
        domanda = item.get("domanda", "")
        domanda_norm = normalize_text(domanda)

        if domanda_norm and (domanda_norm in user_normalized or user_normalized in domanda_norm):
            logger.info("Match esatto trovato: %s", domanda)
            return {"match": True, "item": item, "score": 1.0, "method": "exact"}

        sim = calculate_similarity(user_normalized, domanda_norm)
        logger.debug("Similitudine con '%s': %.3f", domanda, sim)
        if sim > best_score:
            best_score = sim
            best_match = item
            match_method = "similarity"

        if user_keywords:
            domanda_keywords = extract_keywords(domanda)
            if domanda_keywords:
                matched = sum(1 for kw in user_keywords if any(calculate_similarity(kw, dk) > 0.8 for dk in domanda_keywords))
                if matched:
                    keyword_score = (matched / len(user_keywords)) * 1.1 + 0.05
                    logger.debug("Keyword score con '%s': %.3f (matched=%d)", domanda, keyword_score, matched)
                    if keyword_score > best_score:
                        best_score = keyword_score
                        best_match = item
                        match_method = "keywords"

    logger.info("Best match: %s (score=%.3f)", best_match.get("domanda") if best_match else None, best_score)
    if best_score >= FUZZY_THRESHOLD:
        return {"match": True, "item": best_match, "score": best_score, "method": match_method}
    return {"match": False, "item": None, "score": best_score, "method": None}

# -----------------------
# Handlers: commands & messages
# -----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user is None:
        return
    args = context.args or []
    if args and args[0] == load_access_code():
        was_new = authorize_user(user.id, user.first_name, user.last_name, user.username)
        if was_new:
            await update.effective_message.reply_text("✅ Autorizzato! Usa /help")
            if ADMIN_CHAT_ID:
                try:
                    await context.bot.send_message(ADMIN_CHAT_ID, f"✅ Nuovo autorizzato: {user.first_name} ({user.id})")
                except Exception:
                    logger.exception("Invio notifica admin fallito")
        else:
            await update.effective_message.reply_text("✅ Già autorizzato!")
        return

    if is_user_authorized(user.id):
        await update.effective_message.reply_text(f"👋 Ciao {user.first_name}! Usa /help")
    else:
        await update.effective_message.reply_text("❌ Non autorizzato")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user is None:
        return
    if not is_user_authorized(user.id):
        await update.effective_message.reply_text("❌ Non autorizzato")
        return

    intro = (
        "📚 Usa semplicemente messaggi: il bot cercherà nella FAQ e nella lista.\n\n"
        "📌 Comandi utili:\n"
        "• /lista <parola> - cerca nella lista\n"
        "• /aggiorna_lista - (admin) aggiorna la lista da JustPaste\n"
        "• /aggiorna_faq - (admin) aggiorna le FAQ\n\n"
    )
    await update.effective_message.reply_text(intro)

async def genera_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None:
        return
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.effective_message.reply_text("❌ Solo admin")
        return
    bot_me = await context.bot.get_me()
    code = load_access_code()
    link = f"https://t.me/{bot_me.username}?start={code}"
    await update.effective_message.reply_text(f"🔗 Link di invito:\n<code>{link}</code>", parse_mode="HTML")

async def cambia_codice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        await update.effective_message.reply_text("❌ Solo admin")
        return
    new_code = secrets.token_urlsafe(12)
    save_access_code(new_code)
    bot_me = await context.bot.get_me()
    link = f"https://t.me/{bot_me.username}?start={new_code}"
    await update.effective_message.reply_text(f"✅ Nuovo link:\n<code>{link}</code>", parse_mode="HTML")

async def lista_autorizzati_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        await update.effective_message.reply_text("❌ Solo admin")
        return
    users = load_authorized_users()
    if not users:
        await update.effective_message.reply_text("🗂️ Nessun autorizzato")
        return
    lines = []
    for i, (uid, data) in enumerate(users.items(), start=1):
        name = data.get("name", "N/A")
        username = data.get("username", "N/A")
        uid_val = data.get("id", uid)
        lines.append(f"{i}. {name} (@{username}) - {uid_val}")
    msg = "👥 Autorizzati:\n" + "\n".join(lines)
    await update.effective_message.reply_text(msg)

async def revoca_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        await update.effective_message.reply_text("❌ Solo admin")
        return
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("❌ Uso: /revoca <user_id>")
        return
    try:
        target = str(int(args[0]))
    except:
        await update.effective_message.reply_text("❌ ID non valido")
        return
    users = load_authorized_users()
    if target in users:
        del users[target]
        save_authorized_users(users)
        await update.effective_message.reply_text(f"✅ Rimosso {target}")
    else:
        await update.effective_message.reply_text("❌ Utente non trovato")

async def aggiorna_faq_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        await update.effective_message.reply_text("❌ Solo admin")
        return
    await update.effective_message.reply_text("⏳ Aggiorno FAQ...")
    ok = await update_faq_from_web_async()
    if ok:
        faq_list = load_faq().get("faq", [])
        await update.effective_message.reply_text(f"✅ FAQ aggiornate. Totale: {len(faq_list)}")
    else:
        await update.effective_message.reply_text("❌ Errore aggiornamento FAQ")

async def lista_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None:
        return
    if not context.args:
        await update.effective_message.reply_text("❌ Usa: /lista <parola da cercare>")
        return
    query = " ".join(context.args)
    lista_lines = load_lista()
    results = search_lista(query, lista_lines)
    if results:
        response = "\n".join(results[:10])
        await update.effective_message.reply_text(f"Risultati per '{query}':\n{response}")
    else:
        await update.effective_message.reply_text(f"Nessun risultato trovato per '{query}'.")

async def aggiorna_lista_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        await update.effective_message.reply_text("❌ Solo admin")
        return
    await update.effective_message.reply_text("⏳ Aggiorno lista...")
    ok = await fetch_lista_async()
    if ok:
        await update.effective_message.reply_text("✅ Lista aggiornata!")
    else:
        await update.effective_message.reply_text("❌ Errore aggiornamento lista")

async def admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        await update.effective_message.reply_text("❌ Solo admin")
        return
    msg = (
        "👑 Comandi Admin:\n\n"
        "• /genera_link\n"
        "• /cambia_codice\n"
        "• /lista_autorizzati\n"
        "• /revoca <id>\n"
        "• /aggiorna_faq\n"
        "• /aggiorna_lista\n"
    )
    await update.effective_message.reply_text(msg)

# -----------------------
# Message processing (private & group/channel)
# -----------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message
    text = message.text if message and message.text else ""
    if user is None:
        return
    logger.info("Private message from %s (%s): %s", user.id, user.username, text)

    if not is_user_authorized(user.id):
        await message.reply_text("❌ Non autorizzato")
        return

    faq_data = load_faq()
    faq_list = faq_data.get("faq", []) if faq_data else []
    if faq_list and text.strip():
        faq_result = fuzzy_search_faq(text, faq_list)
        if faq_result["match"]:
            item = faq_result["item"]
            emoji = "🎯" if faq_result["score"] > 0.9 else "✅" if faq_result["score"] > 0.75 else "💡"
            resp = f"{emoji} <b>{item['domanda']}</b>\n\n{item['risposta']}"
            if faq_result["score"] < 0.9:
                resp += f"\n\n<i>Confidenza: {faq_result['score']:.0%}</i>"
            await message.reply_text(resp, parse_mode="HTML")

    if text.strip():
        lista_lines = load_lista()
        lista_results = search_lista(text, lista_lines)
        if lista_results:
            response = "\n".join(lista_results[:10])
            await message.reply_text(f"Risultati lista:\n{response}")

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    text = message.text if message and message.text else ""
    chat = update.effective_chat
    user = update.effective_user
    logger.info("Message in chat %s by %s: %s", chat.id if chat else "?", user.id if user else "?", text)

    if not text or not text.strip():
        return

    lista_lines = load_lista()
    lista_results = search_lista(text, lista_lines)
    if lista_results:
        try:
            await message.reply_text(f"Risultati lista:\n" + "\n".join(lista_results[:8]))
        except Exception:
            logger.exception("Errore inviando risultati lista")

    faq_data = load_faq()
    faq_list = faq_data.get("faq", []) if faq_data else []
    if faq_list:
        faq_result = fuzzy_search_faq(text, faq_list)
        if faq_result["match"] and faq_result["score"] >= 0.8:
            item = faq_result["item"]
            try:
                await message.reply_text(f"🤖 {item['domanda']}:\n{item['risposta']}")
            except Exception:
                logger.exception("Errore invio FAQ in gruppo")

# -----------------------
# Callback query handler
# -----------------------
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    data = query.data or ""
    if data.startswith("payment_ok_"):
        try:
            await query.edit_message_text(f"✅ Confermato da {query.from_user.first_name}!")
        except Exception:
            logger.exception("Errore edit message payment_ok")
    elif data.startswith("payment_no_"):
        try:
            await query.edit_message_text("⚠️ Indicaci il metodo di pagamento corretto.")
        except Exception:
            logger.exception("Errore edit message payment_no")
    else:
        try:
            await query.edit_message_text("ℹ️ Azione eseguita.")
        except Exception:
            logger.exception("Errore edit message callback")

# -----------------------
# Startup / initialization
# -----------------------
def signal_handler(signum, frame):
    logger.info("Segnale ricevuto: %s. Arresto in corso...", signum)
    shutdown_bot()
    os._exit(0)

def shutdown_bot():
    global bot_application, BOT_LOOP
    if bot_application and BOT_LOOP:
        async def stop_app():
            try:
                await bot_application.stop()
                await bot_application.shutdown()
            except Exception:
                logger.exception("Errore durante shutdown bot")
        try:
            BOT_LOOP.run_until_complete(stop_app())
        except Exception:
            logger.exception("Errore fermando loop")
        try:
            BOT_LOOP.stop()
        except Exception:
            pass

def start_bot_thread():
    t = Thread(target=initialize_bot_sync, daemon=True)
    t.start()

def initialize_bot_sync():
    global bot_application, bot_initialized, BOT_LOOP
    if bot_initialized:
        return
    try:
        logger.info("Inizializzazione bot...")
        loop = asyncio.new_event_loop()
        BOT_LOOP = loop
        asyncio.set_event_loop(loop)

        async def setup_and_start():
            global bot_application
            application = Application.builder().token(BOT_TOKEN).build()

            application.add_handler(CommandHandler("start", start))
            application.add_handler(CommandHandler("help", help_command))
            application.add_handler(CommandHandler("genera_link", genera_link_command))
            application.add_handler(CommandHandler("cambia_codice", cambia_codice_command))
            application.add_handler(CommandHandler("lista", lista_command))
            application.add_handler(CommandHandler("aggiorna_lista", aggiorna_lista_command))
            application.add_handler(CommandHandler("lista_autorizzati", lista_autorizzati_command))
            application.add_handler(CommandHandler("revoca", revoca_command))
            application.add_handler(CommandHandler("admin_help", admin_help_command))
            application.add_handler(CommandHandler("aggiorna_faq", aggiorna_faq_command))
            application.add_handler(CallbackQueryHandler(handle_callback_query))

            application.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), handle_group_message)
            )
            application.add_handler(
                MessageHandler(filters.TEXT & filters.ChatType.CHANNEL, handle_group_message)
            )
            application.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_message)
            )

            await application.initialize()
            await application.start()
            bot_application = application
            me = await application.bot.get_me()
            logger.info("Bot avviato: @%s", me.username)

            if WEBHOOK_URL:
                try:
                    await application.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
                    logger.info("Webhook impostato: %s/webhook", WEBHOOK_URL)
                except Exception:
                    logger.exception("Impostazione webhook fallita")

            return application

        bot_application = loop.run_until_complete(setup_and_start())
        bot_initialized = True

      ##  signal.signal(signal.SIGINT, signal_handler)
      ##  signal.signal(signal.SIGTERM, signal_handler)

        logger.info("Inizializzazione completata")
        try:
            loop.run_until_complete(fetch_lista_async())
        except Exception:
            logger.exception("Errore fetching lista iniziale")
        try:
            loop.run_until_complete(update_faq_from_web_async())
        except Exception:
            logger.exception("Errore fetching faq iniziale")

    except Exception:
        logger.exception("Errore initialize_bot_sync")

# -----------------------
# Flask routes for webhook & health
# -----------------------
@app.route("/", methods=["GET"])
def index():
    return "Bot attivo", 200

@app.route("/health", methods=["GET"])
def health():
    return "OK", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    global bot_initialized, bot_application
    if not bot_initialized:
        initialize_bot_sync()
    if not bot_application:
        return "Bot not ready", 503
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, bot_application.bot)
        loop = BOT_LOOP or asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
        fut = asyncio.run_coroutine_threadsafe(bot_application.process_update(update), loop)
        fut.result(timeout=15)
        return "OK", 200
    except Exception:
        logger.exception("Errore webhook processing")
        return "ERROR", 500

# Note: per deploy con Gunicorn/Wsgi, non avviare app.run() qui.
# Se vuoi eseguire localmente direttamente, puoi decommentare il blocco seguente:
# if __name__ == "__main__":
#     start_bot_thread()
#     app.run(host="0.0.0.0", port=PORT, debug=False)
