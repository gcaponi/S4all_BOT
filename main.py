import os
import json
import logging
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ChatMemberHandler, filters, ContextTypes
import secrets
import re
import requests
from bs4 import BeautifulSoup
from difflib import SequenceMatcher
import asyncio

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_CHAT_ID = int(os.environ.get('ADMIN_CHAT_ID', 0))
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '')
PORT = int(os.environ.get('PORT', 10000))

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

# -----------------------
# Utils: web fetch, parsing, I/O
# -----------------------
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
    """Cerca prodotti nella lista testuale"""
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
    # Semplice euristica: presenza di numeri + lunghezza minima
    return bool(re.search(r'\d', text)) and len(text.strip()) >= 5

def is_requesting_lista(text: str) -> bool:
    if not text:
        return False
    t = normalize_text(text)
    keywords = [
        "lista", "prodotti", "hai la lista", "che prodotti hai", "mostrami la lista", "fammi vedere la lista", "hai lista", "voglio la lista", 
        "mandami la lista", "inviami la lista", "lista prodotti", "elenco prodotti", "quali prodotti ci sono", "cosa vendi", "cosa hai", "mostra prodotti", 
        "fammi vedere i prodotti", "lista aggiornata", "lista completa", "lista prezzi", "lista disponibile", "lista articoli", "elenco articoli", 
        "elenco disponibile", "prodotti disponibili", "prodotti in vendita", "catalogo prodotti", "catalogo", "catalogo aggiornato", 
        "catalogo prezzi", "puoi mandarmi la lista", "puoi mostrarmi la lista", "puoi inviarmi la lista", "voglio vedere la lista", "voglio vedere i prodotti"
    ]
    # normalizziamo le keyword per confrontare coerentemente
    kws = [normalize_text(k) for k in keywords]
    return any(kw in t for kw in kws)
    
# -----------------------
# Handlers: commands & messages
# -----------------------
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
                        await context.bot.send_message(ADMIN_CHAT_ID, f"✅ Nuovo: {user.first_name}")
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
        await update.message.reply_text("❌ Nessuna FAQ")
        return

    full_text = "🗒️Ciao! Per favore prima di fare qualsiasi domanda o ordinare leggi interamente il listino dopo la lista prodotti dove troverai risposta alla maggior parte delle tue domande: tempi di spedizione, metodi di pagamento come ordinare ecc. 🗒️\n\n"
    full_text += "📍NOTA BENE: la qualità è la priorità principale, i vari brand sono selezionati direttamente tra i migliori sul mercato, se cerchi prodotti scadenti ed economici non acquistare qui!\n\n"
    full_text += "🔴🔴Se vuoi puoi lasciarmi la tua Email per essere avvertito in caso di cambio contatto Telegram {tra qualche mese mi sposto su una nuova piattaforma per la sicurezza di tutti} e per essere avvertito all' arrivo dei prodotti terminati o prodotti nuovi e promozioni🔴🔴\n\n"

    for item in faq_list:
        full_text += f"## {item['domanda']}\n{item['risposta']}\n\n"

    full_text += "💡 Scrivi anche con errori!"

    max_length = 4000
    if len(full_text) <= max_length:
        await update.message.reply_text(full_text)
    else:
        parts = []
        current = ""
        for line in full_text.split("\n"):
            if len(current) + len(line) + 1 > max_length:
                parts.append(current)
                current = line + "\n"
            else:
                current += line + "\n"
        if current:
            parts.append(current)

        for part in parts:
            await update.message.reply_text(part)

async def lista_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        update_lista_from_web()
    except Exception:
        logger.exception("Errore aggiornamento lista (ignorato)")

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
    ok = update_lista_from_web()
    if ok:
        await update.message.reply_text("✅ Lista aggiornata!")
    else:
        await update.message.reply_text("❌ Errore aggiornamento lista")

async def genera_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Solo admin")
        return

    link = f"https://t.me/{get_bot_username.username}?start={load_access_code()}"
    await update.message.reply_text(f"🔗 <code>{link}</code>\n\n👥 Autorizzati: {len(load_authorized_users())}", parse_mode='HTML')

async def cambia_codice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Solo admin")
        return

    new_code = secrets.token_urlsafe(12)
    save_access_code(new_code)
    link = f"https://t.me/{get_bot_username.username}?start={new_code}"
    await update.message.reply_text(f"✅ Nuovo link:\n<code>{link}</code>", parse_mode='HTML')

async def lista_autorizzati_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Solo admin")
        return

    users = load_authorized_users()
    if not users:
        await update.message.reply_text("📋 Nessuno")
        return

    msg = f"👥 <b>Autorizzati ({len(users)}):</b>\n\n"
    for i, (uid, data) in enumerate(users.items(), 1):
        name = data.get('name', 'N/A')
        username = data.get('username', 'N/A')
        user_id_val = data.get('id', uid)
        msg += f"{i}. {name} (@{username}) - {user_id_val}\n"
    await update.message.reply_text(msg, parse_mode='HTML')

async def revoca_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Solo admin")
        return

    if not context.args:
        await update.message.reply_text("❌ Uso: /revoca (ID)")
        return

    try:
        target_id = str(int(context.args[0]))
        users = load_authorized_users()
        if target_id in users:
            del users[target_id]
            save_authorized_users(users)
            await update.message.reply_text(f"✅ Rimosso {target_id}")
        else:
            await update.message.reply_text(f"❌ Non trovato {target_id}")
    except:
        await update.message.reply_text("❌ ID non valido")

async def admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Solo admin")
        return

    msg = (
        "👑 <b>Comandi Admin:</b>\n\n"
        "🔐 Accessi:\n"
        "• /genera_link\n"
        "• /cambia_codice\n"
        "• /lista_autorizzati\n"
        "• /revoca (ID)\n"
        "• /aggiorna_faq\n"
        "• /aggiorna_lista\n\n"
        "👤 Utente:\n"
        "• /start\n"
        "• /help\n"
        "• /lista\n\n"
    )
    await update.message.reply_text(msg, parse_mode='HTML')

async def aggiorna_faq_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Solo admin")
        return

    await update.message.reply_text("⏳ Aggiorno FAQ...")
    if update_faq_from_web():
        faq_list = load_faq().get("faq", [])
        await update.message.reply_text(f"✅ <b>FAQ aggiornate!</b>\n\n📊 Totale: {len(faq_list)}", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ Errore aggiornamento")

# -----------------------
# Prioritized message handling
# -----------------------
async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not getattr(message, "text", None):
        return

    text = message.text.strip()

    # Se l'utente chiede la lista esplicitamente, rispondi subito
    if is_requesting_lista(text):
        lista_text = load_lista()
        if not lista_text:
            await message.reply_text("❌ Lista non disponibile")
            return
        max_len = 4000
        for i in range(0, len(lista_text), max_len):
            await message.reply_text(lista_text[i:i+max_len])
        return
    
    # 1) Controllo ordine (PRIMA di tutto)
    if looks_like_order(text) and not has_payment_method(text):
        keyboard = [[
            InlineKeyboardButton("✅ Sì", callback_data=f"payment_ok_{message.message_id}"),
            InlineKeyboardButton("❌ No", callback_data=f"payment_no_{message.message_id}")
        ]]
        try:
            await context.bot.send_message(
                chat_id=message.chat.id,
                text="🤔 <b>Ordine senza pagamento?</b>\n\nHai specificato come pagherai?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Errore invio pulsanti (privato): {e}")
        return

    # 2) Cerca nelle FAQ
    faq = load_faq()
    if faq and faq.get("faq"):
        result = fuzzy_search_faq(text, faq.get("faq", []))
        if result['match'] and result['score'] > 0.75:
            item = result['item']
            emoji = "🎯" if result['score'] > 0.9 else "✅"
            resp = f"{emoji} <b>{item['domanda']}</b>\n\n{item['risposta']}"
            if result['score'] < 0.9:
                resp += f"\n\n<i>Confidenza: {result['score']:.0%}</i>"
            try:
                await message.reply_text(resp, parse_mode='HTML')
            except Exception as e:
                logger.error(f"Errore invio FAQ (privato): {e}")
            return

    # 3) Cerca nella Lista
    lista_text = load_lista()
    if lista_text:
        result = fuzzy_search_lista(text, lista_text)
        if result['match'] and result['score'] > 0.3:
            emoji = "🎯" if result['score'] > 0.7 else "📦"
            resp = f"{emoji} <b>Prodotti trovati:</b>\n\n{result['snippet']}"
            try:
                await message.reply_text(resp, parse_mode='HTML')
            except Exception as e:
                logger.error(f"Errore invio lista (privato): {e}")
            return

    # 4) Nessuna risposta
    try:
        await message.reply_text("❓ Nessuna risposta. Usa /help", parse_mode='HTML')
    except Exception as e:
        logger.error(f"Errore invio fallback (privato): {e}")

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Gestisce messaggi in group/supergroup e channel_post
    message = update.message or update.channel_post
    if not message or not getattr(message, "text", None):
        return

    text = message.text.strip()
    chat_id = getattr(message, "chat", None).id if getattr(message, "chat", None) else None
    user = getattr(message, "from_user", None)
    user_id = user.id if user else None

    # FUNZIONE 0: Benvenuto al primo messaggio dell'utente (se esiste from_user)
    if user_id and chat_id:
        greeted_key = f"greeted_{chat_id}_{user_id}"
        if not context.bot_data.get(greeted_key):
            context.bot_data[greeted_key] = True
            welcome_text = (
                f"👋 Benvenuto {user.first_name}!\n\n"
                "🗒️ Per favore prima di fare qualsiasi domanda o ordinare leggi interamente il listino "
                "dopo la lista prodotti dove troverai risposta alla maggior parte delle tue domande: "
                "tempi di spedizione, metodi di pagamento, come ordinare ecc. 🗒️\n\n"
                "📋 <b>Comandi disponibili:</b>\n"
                "• /help - Visualizza tutte le FAQ\n"
                "• /lista - Visualizza la lista prodotti"
            )
            try:
                kwargs = {
                    "chat_id": chat_id,
                    "text": welcome_text,
                    "parse_mode": "HTML"
                }
                thread_id = getattr(message, "message_thread_id", None)
                if thread_id:
                    kwargs["message_thread_id"] = thread_id
                    kwargs["reply_to_message_id"] = message.message_id
                await context.bot.send_message(**kwargs)
                logger.info(f"Benvenuto inviato a {user.first_name} ({user_id}) nel gruppo {chat_id}")
            except Exception as e:
                logger.error(f"Errore invio benvenuto: {e}")
    
    # Se l'utente chiede la lista esplicitamente, rispondi subito
    if is_requesting_lista(text):
    lista_text = load_lista()
    if not lista_text:
        await context.bot.send_message(chat_id=chat_id, text="❌ Lista non disponibile")
        return
    max_len = 4000
    thread_id = getattr(message, "message_thread_id", None)
    for i in range(0, len(lista_text), max_len):
        kwargs = {
            "chat_id": chat_id,
            "text": lista_text[i:i+max_len],
            "parse_mode": "HTML"
        }
        if thread_id:
            kwargs["message_thread_id"] = thread_id
            kwargs["reply_to_message_id"] = message.message_id
        await context.bot.send_message(**kwargs)
    return
    
    # CONTROLLO ORDINE PRIMA DI FAQ/LISTA
    if looks_like_order(text) and not has_payment_method(text):
        keyboard = [[
            InlineKeyboardButton("✅ Sì", callback_data=f"payment_ok_{message.message_id}"),
            InlineKeyboardButton("❌ No", callback_data=f"payment_no_{message.message_id}")
        ]]
        try:
            kwargs = {
                "chat_id": chat_id,
                "text": "🤔 <b>Ordine senza pagamento?</b>\n\nHai specificato come pagherai?",
                "reply_markup": InlineKeyboardMarkup(keyboard),
                "parse_mode": "HTML"
            }
            thread_id = getattr(message, "message_thread_id", None)
            if thread_id:
                kwargs["message_thread_id"] = thread_id
                kwargs["reply_to_message_id"] = message.message_id
            await context.bot.send_message(**kwargs)
        except Exception as e:
            logger.error(f"Errore pulsanti (gruppo/canale): {e}")
        return

    # 1) Cerca nelle FAQ (solo se non era un ordine)
    faq = load_faq()
    if faq and faq.get("faq"):
        result = fuzzy_search_faq(text, faq.get("faq", []))
        if result['match'] and result['score'] > 0.75:
            item = result['item']
            emoji = "🎯" if result['score'] > 0.9 else "✅"
            resp = f"{emoji} <b>{item['domanda']}</b>\n\n{item['risposta']}"
            try:
                kwargs = {
                    "chat_id": chat_id,
                    "text": resp,
                    "parse_mode": "HTML"
                }
                thread_id = getattr(message, "message_thread_id", None)
                if thread_id:
                    kwargs["message_thread_id"] = thread_id
                    kwargs["reply_to_message_id"] = message.message_id
                await context.bot.send_message(**kwargs)
            except Exception as e:
                logger.error(f"Errore invio FAQ gruppo: {e}")
            return

    # 2) Cerca nella LISTA
    lista_text = load_lista()
    if lista_text:
        result = fuzzy_search_lista(text, lista_text)
        if result['match'] and result['score'] > 0.3:
            emoji = "🎯" if result['score'] > 0.7 else "📦"
            resp = f"{emoji} <b>Prodotti trovati:</b>\n\n{result['snippet']}"
            try:
                kwargs = {
                    "chat_id": chat_id,
                    "text": resp,
                    "parse_mode": "HTML"
                }
                thread_id = getattr(message, "message_thread_id", None)
                if thread_id:
                    kwargs["message_thread_id"] = thread_id
                    kwargs["reply_to_message_id"] = message.message_id
                await context.bot.send_message(**kwargs)
            except Exception as e:
                logger.error(f"Errore invio lista gruppo: {e}")
            return

    # Se non è ordine e non trova FAQ/Lista, non rispondere (evita spam)
    return

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    try:
        await query.answer()
    except Exception:
        pass

    if query.data.startswith("payment_ok_"):
        try:
            await query.edit_message_text(f"✅ Confermato da {query.from_user.first_name}!", parse_mode="HTML")
        except Exception as e:
            logger.error(f"Errore edit message (payment_ok): {e}")
    elif query.data.startswith("payment_no_"):
        try:
            await query.edit_message_text(f"💡 Specifica: {', '.join(PAYMENT_KEYWORDS[:8])}...", parse_mode="HTML")
        except Exception as e:
            logger.error(f"Errore edit message (payment_no): {e}")

# Benvenuto per nuovi membri (status)
async def handle_user_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.new_chat_members:
        return
    
    for member in update.message.new_chat_members:
        welcome_text = (
            f"👋 Benvenuto {member.first_name}!\n\n"
            "🗒️ Per favore prima di fare qualsiasi domanda o ordinare leggi interamente il listino "
            "dopo la lista prodotti dove troverai risposta alla maggior parte delle tue domande: "
            "tempi di spedizione, metodi di pagamento, come ordinare ecc. 🗒️\n\n"
            "📋 <b>Comandi disponibili:</b>\n"
            "• /help - Visualizza tutte le FAQ\n"
            "• /lista - Visualizza la lista prodotti"
        )
        try:
            kwargs = {
                "chat_id": update.message.chat.id,
                "text": welcome_text,
                "parse_mode": "HTML"
            }
            thread_id = getattr(update.message, "message_thread_id", None)
            if thread_id:
                kwargs["message_thread_id"] = thread_id
            await context.bot.send_message(**kwargs)
            logger.info(f"Benvenuto inviato a {member.first_name} (nuovo membro)")
        except Exception as e:
            logger.error(f"Errore invio benvenuto: {e}")

async def handle_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Placeholder se vuoi gestire eventi di chat member in futuro
    pass

# -----------------------
# Setup / inizializzazione bot
# -----------------------
async def setup_bot():
    global bot_application, initialization_lock
    
    if initialization_lock:
        logger.info("⏳ Inizializzazione già in corso...")
        return None
    
    initialization_lock = True
    
    try:
        logger.info("📡 Inizializzazione bot...")
        
        # Prefetch FAQ e Lista (silently)
        try:
            update_faq_from_web()
            update_lista_from_web()
        except Exception as e:
            logger.warning(f"Prefetch warning: {e}")
        
        application = Application.builder().token(BOT_TOKEN).updater(None).build()
        bot = await application.bot.get_me()
        get_bot_username.username = bot.username
        logger.info(f"Bot: @{bot.username}")

        # Command handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("genera_link", genera_link_command))
        application.add_handler(CommandHandler("cambia_codice", cambia_codice_command))
        application.add_handler(CommandHandler("lista_autorizzati", lista_autorizzati_command))
        application.add_handler(CommandHandler("revoca", revoca_command))
        application.add_handler(CommandHandler("admin_help", admin_help_command))
        application.add_handler(CommandHandler("aggiorna_faq", aggiorna_faq_command))
        application.add_handler(CommandHandler("lista", lista_command))
        application.add_handler(CommandHandler("aggiorna_lista", aggiorna_lista_command))
        
        # Status handlers - benvenuto nuovi membri
        application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_user_status))
        application.add_handler(ChatMemberHandler(handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
        
        # Callbacks & messages
        application.add_handler(CallbackQueryHandler(handle_callback_query))
        # GROUPS / SUPERGROUPS / CHANNELS
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP | filters.ChatType.CHANNEL), handle_group_message))
        # PRIVATE
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_private_message))

        # Set webhook only if URL provided
        if WEBHOOK_URL:
            await application.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
            logger.info(f"✅ Webhook: {WEBHOOK_URL}/webhook")

        await application.initialize()
        await application.start()
        logger.info("🤖 Bot pronto!")
        
        return application
    except Exception as e:
        logger.error(f"❌ Setup error: {e}")
        initialization_lock = False
        raise

# -----------------------
# Flask endpoints (webhook)
# -----------------------
@app.route('/')
def index():
    return "🤖 Bot attivo! ✅", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    global bot_application, bot_initialized
    
    # Initialize bot on first webhook call
    if not bot_initialized:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            bot_application = loop.run_until_complete(setup_bot())
            bot_initialized = True
        except Exception as e:
            logger.error(f"Webhook init error: {e}")
            return "Init error", 503
    
    if not bot_application:
        return "Bot not ready", 503
    
    try:
        update = Update.de_json(request.get_json(force=True), bot_application.bot)
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.run_until_complete(bot_application.process_update(update))
        return "OK", 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "ERROR", 500

@app.route('/health')
def health():
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False)
