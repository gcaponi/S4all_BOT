import os
import json
import logging
import asyncio
import re
import secrets
import requests
from flask import Flask, request
from bs4 import BeautifulSoup
from difflib import SequenceMatcher
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler, 
    ChatMemberHandler, 
    filters, 
    ContextTypes
)

# ---- Configurazione Logging ----
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ---- Variabili di Ambiente ----
BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_CHAT_ID = int(os.environ.get('ADMIN_CHAT_ID', 0))
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '')
PORT = int(os.environ.get('PORT', 10000))

# ---- Costanti e File ----
AUTHORIZED_USERS_FILE = 'authorized_users.json'
ACCESS_CODE_FILE = 'access_code.json'
FAQ_FILE = 'faq.json'
LISTA_URL = "https://justpaste.it/lista_4all"
LISTA_FILE = "lista.txt"
PASTE_URL = "https://justpaste.it/faq_4all"
FUZZY_THRESHOLD = 0.6

PAYMENT_KEYWORDS = ["contanti", "carta", "bancomat", "bonifico", "paypal", "satispay", "postepay", "pos", "wallet", "ricarica", "usdt", "crypto", "cripto", "bitcoin", "bit", "btc", "eth", "usdc"]

app = Flask(__name__)
bot_application = None
bot_initialized = False
initialization_lock = False

# ---- Utils: web fetch, parsing, I/O ----
def fetch_markdown_from_html(url: str) -> str:
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    content = soup.select_one("#articleContent")
    if content is None:
        raise RuntimeError("Contenuto non trovato")
    return content.get_text("\n").strip()

def parse_faq(markdown: str) -> list:
    pattern = r"^##\s+(.*?)\n(.*?)(?=\n##\s+|\Z)"
    matches = re.findall(pattern, markdown, flags=re.S | re.M)
    if not matches:
        raise RuntimeError("Formato non valido")
    return [{"domanda": d.strip(), "risposta": r.strip()} for d, r in matches]

def write_faq_json(faq: list, filename: str):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"faq": faq}, f, ensure_ascii=False, indent=2)

def update_faq_from_web():
    try:
        markdown = fetch_markdown_from_html(PASTE_URL)
        faq = parse_faq(markdown)
        write_faq_json(faq, FAQ_FILE)
        logger.info(f"FAQ aggiornate: {len(faq)}")
        return True
    except Exception as e:
        logger.error(f"Errore FAQ: {e}")
        return False

def update_lista_from_web():
    try:
        r = requests.get(LISTA_URL, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        content = soup.select_one("#articleContent")
        if content is None:
            raise RuntimeError("Contenuto lista non trovato")
        text = content.get_text("\n").strip()
        with open(LISTA_FILE, "w", encoding="utf-8") as f:
            f.write(text)
        logger.info("Lista aggiornata correttamente")
        return True
    except Exception as e:
        logger.error(f"Errore aggiornamento lista: {e}")
        return False

def load_lista():
    try:
        with open(LISTA_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

def load_json_file(filename, default=None):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return default if default is not None else {}

def save_json_file(filename, data):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_authorized_users():
    data = load_json_file(AUTHORIZED_USERS_FILE, default={})
    if isinstance(data, list):
        return {str(uid): {"id": uid, "name": "Sconosciuto", "username": None} for uid in data}
    return data

def save_authorized_users(users):
    save_json_file(AUTHORIZED_USERS_FILE, users)

def load_access_code():
    data = load_json_file(ACCESS_CODE_FILE, default={})
    if not data.get('code'):
        code = secrets.token_urlsafe(12)
        data = {'code': code}
        save_json_file(ACCESS_CODE_FILE, data)
    return data['code']

def save_access_code(code):
    save_json_file(ACCESS_CODE_FILE, {'code': code})

def load_faq():
    return load_json_file(FAQ_FILE)

def is_user_authorized(user_id):
    return str(user_id) in load_authorized_users()

def authorize_user(user_id, first_name=None, last_name=None, username=None):
    authorized_users = load_authorized_users()
    user_id_str = str(user_id)
    if user_id_str not in authorized_users:
        full_name = f"{first_name or ''} {last_name or ''}".strip() or "Sconosciuto"
        authorized_users[user_id_str] = {"id": user_id, "name": full_name, "username": username}
        save_authorized_users(authorized_users)
        return True
    return False

def get_bot_username():
    return getattr(get_bot_username, 'username', 'tuobot')

# ---- Logica di Ricerca (Fuzzy) ----
def calculate_similarity(text1: str, text2: str) -> float:
    return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

def normalize_text(text: str) -> str:
    text = re.sub(r'[^\w\s]', '', text)
    return re.sub(r'\s+', ' ', text).strip().lower()

def extract_keywords(text: str) -> list:
    words = normalize_text(text).split()
    stop_words = {'che', 'sono', 'come', 'dove', 'quando', 'quale', 'quali', 'del', 'della', 'dei', 'delle', 'con', 'per', 'una', 'uno'}
    return [w for w in words if len(w) > 3 and w not in stop_words]

def fuzzy_search_faq(user_message: str, faq_list: list) -> dict:
    user_normalized = normalize_text(user_message)
    user_keywords = extract_keywords(user_message)
    best_match = None
    best_score = 0
    match_method = None

    for item in faq_list:
        domanda_normalized = normalize_text(item["domanda"])
        if domanda_normalized in user_normalized or user_normalized in domanda_normalized:
            return {'match': True, 'item': item, 'score': 1.0, 'method': 'exact'}

        similarity = calculate_similarity(user_normalized, domanda_normalized)
        if similarity > best_score:
            best_score = similarity
            best_match = item
            match_method = 'similarity'

        if user_keywords:
            domanda_keywords = extract_keywords(item["domanda"])
            matched = sum(1 for kw in user_keywords if any(calculate_similarity(kw, dk) > 0.8 for dk in domanda_keywords))
            keyword_score = (matched / len(user_keywords)) * (1.2 if matched > 1 else 1)
            if keyword_score > best_score:
                best_score = keyword_score
                best_match = item
                match_method = 'keywords'

    if best_score >= FUZZY_THRESHOLD:
        return {'match': True, 'item': best_match, 'score': best_score, 'method': match_method}
    return {'match': False, 'item': None, 'score': best_score, 'method': None}

def fuzzy_search_lista(user_message: str, lista_text: str) -> dict:
    if not lista_text:
        return {'match': False, 'snippet': None, 'score': 0}
    
    user_normalized = normalize_text(user_message)
    user_keywords = extract_keywords(user_message)
    
    if not user_keywords:
        return {'match': False, 'snippet': None, 'score': 0}
    
    lines = lista_text.split('\n')
    best_lines = []
    best_score = 0
    
    for line in lines:
        if not line.strip():
            continue
        
        line_normalized = normalize_text(line)
        matches = sum(1 for kw in user_keywords if kw in line_normalized)
        if matches > 0:
            score = matches / len(user_keywords)
            if score > best_score:
                best_score = score
                best_lines = [line.strip()]
            elif score == best_score:
                best_lines.append(line.strip())
    
    if best_score >= 0.3:
        snippet = '\n'.join(best_lines[:5])
        return {'match': True, 'snippet': snippet, 'score': best_score}
    
    return {'match': False, 'snippet': None, 'score': best_score}
    
def has_payment_method(text: str) -> bool:
    if not text:
        return False
    return any(kw in text.lower() for kw in PAYMENT_KEYWORDS)

def looks_like_order(text: str) -> bool:
    if not text:
        return False
    return bool(re.search(r'\d', text)) and len(text.strip()) >= 5

def is_requesting_lista(text: str) -> bool:
    if not text:
        return False
    t = normalize_text(text)
    keywords = ["lista", "prodotti", "mostrami la lista", "catalogo", "cosa vendi"]
    return any(kw in t for kw in keywords)
    
# ---- Handlers ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user is None:
        return
    user_id = user.id

    if context.args:
        if context.args[0] == load_access_code():
            was_new = authorize_user(user_id, user.first_name, user.last_name, user.username)
            if was_new:
                await update.message.reply_text("✅ Autorizzato! Usa /help")
                if ADMIN_CHAT_ID:
                    try:
                        await context.bot.send_message(ADMIN_CHAT_ID, f"✅ Nuovo utente: {user.first_name}")
                    except Exception:
                        logger.exception("Invio notifica admin fallito")
            else:
                await update.message.reply_text("✅ Già autorizzato!")
            return
        else:
            await update.message.reply_text("❌ Codice non valido")
            return

    if is_user_authorized(user_id):
        await update.message.reply_text(f"👋 Ciao {user.first_name}! Usa /help")
    else:
        await update.message.reply_text("❌ Non autorizzato")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or not is_user_authorized(update.effective_user.id):
        await update.message.reply_text("❌ Non autorizzato")
        return

    faq_data = load_faq()
    faq_list = faq_data.get("faq", []) if faq_data else []
    if not faq_list:
        await update.message.reply_text("❌ Nessuna FAQ caricata.")
        return

    full_text = "🗒️ Benvenuto! Leggi le FAQ qui sotto:\n\n"
    for item in faq_list:
        full_text += f"## {item['domanda']}\n{item['risposta']}\n\n"

    max_length = 4000
    if len(full_text) <= max_length:
        await update.message.reply_text(full_text)
    else:
        for i in range(0, len(full_text), max_length):
            await update.message.reply_text(full_text[i:i+max_length])

async def lista_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lista_text = load_lista()
    if not lista_text:
        await update.message.reply_text("❌ Lista non disponibile")
        return
    max_len = 4000
    for i in range(0, len(lista_text), max_len):
        await update.message.reply_text(lista_text[i:i+max_len])

async def aggiorna_lista_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Solo admin")
        return
    await update.message.reply_text("⏳ Aggiorno lista...")
    if update_lista_from_web():
        await update.message.reply_text("✅ Lista aggiornata!")
    else:
        await update.message.reply_text("❌ Errore aggiornamento")

async def genera_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        return
    link = f"https://t.me/{get_bot_username.username}?start={load_access_code()}"
    await update.message.reply_text(f"🔗 Link: <code>{link}</code>", parse_mode='HTML')

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return
    text = message.text.strip()

    if is_requesting_lista(text):
        await lista_command(update, context)
        return

    if looks_like_order(text) and not has_payment_method(text):
        keyboard = [[
            InlineKeyboardButton("✅ Sì", callback_data=f"payment_ok_{message.message_id}"),
            InlineKeyboardButton("❌ No", callback_data=f"payment_no_{message.message_id}")
        ]]
        await message.reply_text("🤔 <b>Ordine senza pagamento?</b>\nHai specificato il metodo?", 
                                 reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return

    faq = load_faq()
    if faq and faq.get("faq"):
        result = fuzzy_search_faq(text, faq.get("faq", []))
        if result['match'] and result['score'] > 0.75:
            await message.reply_text(f"✅ <b>{result['item']['domanda']}</b>\n\n{result['item']['risposta']}", parse_mode='HTML')
            return

    await message.reply_text("❓ Non ho capito. Usa /help", parse_mode='HTML')

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("payment_ok_"):
        await query.edit_message_text(f"✅ Confermato da {query.from_user.first_name}!")
    elif query.data.startswith("payment_no_"):
        await query.edit_message_text("💡 Per favore specifica il metodo di pagamento.")

# ---- Setup Bot ----
async def setup_bot():
    global bot_application, initialization_lock
    if initialization_lock: return None
    initialization_lock = True
    
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        bot_info = await application.bot.get_me()
        get_bot_username.username = bot_info.username
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("lista", lista_command))
        application.add_handler(CommandHandler("genera_link", genera_link_command))
        application.add_handler(CommandHandler("aggiorna_lista", aggiorna_lista_command))
        application.add_handler(CallbackQueryHandler(handle_callback_query))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_private_message))

        if WEBHOOK_URL:
            await application.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
        
        await application.initialize()
        await application.start()
        return application
    except Exception as e:
        logger.error(f"Setup error: {e}")
        initialization_lock = False
        raise

@app.route('/')
def index():
    return "Bot is running", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    global bot_application, bot_initialized
    
    # Inizializza il bot alla prima chiamata del webhook se non è ancora pronto
    if not bot_initialized:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            bot_application = loop.run_until_complete(setup_bot())
            bot_initialized = True
        except Exception as e:
            logger.error(f"Errore inizializzazione Webhook: {e}")
            return "Init error", 503
    
    if not bot_application:
        return "Bot not ready", 503
    
    try:
        # Trasforma il JSON ricevuto da Telegram in un oggetto Update
        update = Update.de_json(request.get_json(force=True), bot_application.bot)
        asyncio.run_coroutine_threadsafe(bot_application.process_update(update), asyncio.get_event_loop())
        
        return "OK", 200
    except Exception as e:
        logger.error(f"Errore processamento update: {e}")
        return "Error", 500

if __name__ == '__main__':
    app.run(port=PORT)
