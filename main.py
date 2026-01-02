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
from datetime import datetime

# ---------------------------------------------------------
# CONFIGURAZIONE LOGGING (VERSIONE ESTESA)
# ---------------------------------------------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# VARIABILI DI AMBIENTE E COSTANTI
# ---------------------------------------------------------
BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_CHAT_ID = int(os.environ.get('ADMIN_CHAT_ID', 0))
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '')
PORT = int(os.environ.get('PORT', 10000))

# Nomi dei file per la persistenza dei dati (Database JSON)
AUTHORIZED_USERS_FILE = 'authorized_users.json'
ACCESS_CODE_FILE = 'access_code.json'
FAQ_FILE = 'faq.json'
LISTA_FILE = "lista.txt"
ORDINI_FILE = 'ordini_confermati.json'

# Link esterni per aggiornamento dinamico
LISTA_URL = "https://justpaste.it/lista_4all"
PASTE_URL = "https://justpaste.it/faq_4all"

# Parametri di ricerca
FUZZY_THRESHOLD = 0.6

# Keywords per intercettazione pagamenti
PAYMENT_KEYWORDS = [
    "contanti", "carta", "bancomat", "bonifico", "paypal", "satispay", 
    "postepay", "pos", "wallet", "ricarica", "usdt", "crypto", 
    "cripto", "bitcoin", "bit", "btc", "eth", "usdc"
]

# Keywords per richiesta listino completo
LISTA_KEYWORDS_FULL = [
    "lista", "listino", "prodotti", "catalogo", "hai la lista", "che prodotti hai", 
    "mostrami la lista", "fammi vedere la lista", "hai lista", "voglio la lista", 
    "mandami la lista", "inviami la lista", "lista prodotti", "elenco prodotti", 
    "quali prodotti ci sono", "cosa vendi", "cosa hai", "mostra prodotti", 
    "fammi vedere i prodotti", "lista aggiornata", "lista completa", "lista prezzi", 
    "lista disponibile", "lista articoli", "elenco articoli", "elenco disponibile", 
    "prodotti disponibili", "prodotti in vendita", "catalogo prodotti", 
    "catalogo aggiornato", "catalogo prezzi", "puoi mandarmi la lista", 
    "puoi mostrarmi la lista", "puoi inviarmi la lista", "voglio vedere la lista", 
    "voglio vedere i prodotti"
]

app = Flask(__name__)
bot_application = None
bot_initialized = False
initialization_lock = False

# ---------------------------------------------------------
# UTILS: I/O E GESTIONE DATABASE LOCALE
# ---------------------------------------------------------

def load_json_file(filename, default=None):
    """Carica in sicurezza file JSON e forza il formato dizionario se necessario"""
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:
                    return default if default is not None else {}
                data = json.loads(content)
                return data
        except Exception as e:
            logger.error(f"Errore lettura file JSON {filename}: {e}")
    return default if default is not None else {}

def load_authorized_users():
    """Carica gli utenti e corregge istantaneamente il formato se è una lista"""
    data = load_json_file(AUTHORIZED_USERS_FILE, default={})
    
    # Se per qualche motivo il file contiene una lista [], la trasformiamo in {}
    if isinstance(data, list):
        logger.warning("Conversione forzata: authorized_users era una lista, trasformata in dizionario.")
        return {} 
    return data

def authorize_user(user_id, first_name=None, last_name=None, username=None):
    """Registra un nuovo utente assicurandosi che il database sia un dizionario"""
    users = load_authorized_users() # Ora siamo sicuri che restituisca un dict
    user_id_str = str(user_id)
    
    if user_id_str not in users:
        full_name = f"{first_name or ''} {last_name or ''}".strip() or "Sconosciuto"
        users[user_id_str] = {
            "id": user_id, 
            "name": full_name, 
            "username": username
        }
        save_authorized_users(users)
        logger.info(f"Utente {user_id_str} salvato correttamente.")
        return True
    return False

def save_json_file(filename, data):
    """Salva i dati in formato JSON indentato per facilitare la lettura"""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def save_conferma_ordine(user_info, order_text):
    """Salva i dettagli di un ordine confermato con data e ora nel database"""
    ordini = load_json_file(ORDINI_FILE, default=[])
    nuovo_ordine = {
        "data": datetime.now().strftime("%Y-%m-%d"),
        "ora": datetime.now().strftime("%H:%M:%S"),
        "utente": user_info,
        "testo": order_text
    }
    ordini.append(nuovo_ordine)
    save_json_file(ORDINI_FILE, ordini)
    logger.info(f"Nuovo ordine salvato per l'utente: {user_info}")

# ---------------------------------------------------------
# UTILS: WEB FETCH E PARSING (JustPaste)
# ---------------------------------------------------------

def fetch_markdown_from_html(url: str) -> str:
    """Scarica il contenuto HTML da JustPaste e lo converte in testo pulito"""
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        content = soup.select_one("#articleContent")
        if content is None:
            raise RuntimeError("Contenuto non trovato nel selettore #articleContent")
        return content.get_text("\n").strip()
    except Exception as e:
        logger.error(f"Errore durante il fetch da {url}: {e}")
        return ""

def parse_faq(markdown: str) -> list:
    """Parsa le FAQ basandosi sui titoli ## del markdown"""
    pattern = r"^##\s+(.*?)\n(.*?)(?=\n##\s+|\Z)"
    matches = re.findall(pattern, markdown, flags=re.S | re.M)
    if not matches:
        return []
    return [{"domanda": d.strip(), "risposta": r.strip()} for d, r in matches]

def update_faq_from_web():
    """Sincronizza le FAQ scaricandole dal link JustPaste configurato"""
    markdown = fetch_markdown_from_html(PASTE_URL)
    if markdown:
        faq = parse_faq(markdown)
        if faq:
            save_json_file(FAQ_FILE, {"faq": faq})
            logger.info(f"FAQ sincronizzate: {len(faq)} elementi salvati.")
            return True
    return False

def update_lista_from_web():
    """Scarica il listino prodotti e lo salva nel file locale lista.txt"""
    try:
        r = requests.get(LISTA_URL, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        content = soup.select_one("#articleContent")
        if content:
            text = content.get_text("\n").strip()
            with open(LISTA_FILE, "w", encoding="utf-8") as f:
                f.write(text)
            logger.info("Listino prodotti aggiornato con successo.")
            return True
    except Exception as e:
        logger.error(f"Errore aggiornamento listino: {e}")
    return False

def load_lista():
    """Carica il contenuto testuale del listino dal file locale"""
    if os.path.exists(LISTA_FILE):
        with open(LISTA_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return ""

# ---------------------------------------------------------
# AUTORIZZAZIONI E GESTIONE ACCESSO
# ---------------------------------------------------------

def load_authorized_users():
    """Carica il database degli utenti autorizzati"""
    return load_json_file(AUTHORIZED_USERS_FILE, default={})

def save_authorized_users(users):
    """Salva il database degli utenti autorizzati"""
    save_json_file(AUTHORIZED_USERS_FILE, users)

def load_authorized_users():
    """Carica il database degli utenti e corregge eventuali formati lista errati"""
    data = load_json_file(AUTHORIZED_USERS_FILE, default={})
    
    # CORREZIONE ERRORE: Se i dati sono una lista, trasformali in dizionario
    if isinstance(data, list):
        logger.warning("Rilevato formato lista in authorized_users.json, conversione in dizionario...")
        new_data = {}
        for item in data:
            if isinstance(item, dict) and "id" in item:
                new_data[str(item["id"])] = item
            elif isinstance(item, (int, str)):
                # Caso di emergenza per soli ID
                new_data[str(item)] = {"id": int(item), "name": "Utente", "username": None}
        save_authorized_users(new_data)
        return new_data
        
    return data

def is_user_authorized(user_id):
    """Verifica se l'ID Telegram è presente tra gli autorizzati"""
    return str(user_id) in load_authorized_users()

def authorize_user(user_id, first_name=None, last_name=None, username=None):
    """Registra un nuovo utente nel database"""
    users = load_authorized_users()
    user_id_str = str(user_id)
    if user_id_str not in users:
        full_name = f"{first_name or ''} {last_name or ''}".strip() or "Sconosciuto"
        users[user_id_str] = {"id": user_id, "name": full_name, "username": username}
        save_authorized_users(users)
        return True
    return False

def get_bot_username():
    """Utility per ottenere lo username del bot"""
    return getattr(get_bot_username, 'username', 'tuobot')

# ---------------------------------------------------------
# LOGICHE DI RICERCA INTELLIGENTE (CORE)
# ---------------------------------------------------------

def calculate_similarity(text1: str, text2: str) -> float:
    """Calcola l'indice di somiglianza tra due stringhe"""
    return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

def normalize_text(text: str) -> str:
    """Pulisce il testo per facilitare il confronto"""
    text = re.sub(r'[^\w\s]', '', text)
    return re.sub(r'\s+', ' ', text).strip().lower()

def is_requesting_lista_full(text: str) -> bool:
    """Analizza se l'utente desidera ricevere l'intero listino"""
    if not text:
        return False
    normalized_msg = normalize_text(text)
    if any(kw in normalized_msg for kw in LISTA_KEYWORDS_FULL):
        return True
    words = normalized_msg.split()
    smart_roots = ["listino", "catalogo", "prodotti", "prezzi", "articoli", "lista"]
    for word in words:
        for root in smart_roots:
            if calculate_similarity(word, root) > 0.85:
                return True
    return False

def fuzzy_search_faq(user_message: str, faq_list: list) -> dict:
    """Cerca la risposta più pertinente nelle FAQ"""
    user_normalized = normalize_text(user_message)
    keywords_map = {
        "spedizione": ["spedito", "spedisci", "spedite", "corriere", "pacco", "invio", "mandato", "spedizioni"],
        "tracking": ["track", "codice", "tracciabilità", "tracciamento", "tracking", "traccia"],
        "tempi": ["quando arriva", "quanto tempo", "giorni", "ricevo", "consegna", "tempistiche"],
        "pagamento": ["pagare", "metodi", "bonifico", "ricarica", "paypal", "crypto", "pagamenti"]
    }
    for item in faq_list:
        domanda_norm = normalize_text(item["domanda"])
        risposta_norm = normalize_text(item["risposta"])
        for root, synonyms in keywords_map.items():
            if any(syn in user_normalized for syn in synonyms):
                if root in domanda_norm or root in risposta_norm:
                    return {'match': True, 'item': item, 'score': 1.0}

    best_match = None
    best_score = 0
    for item in faq_list:
        domanda_norm = normalize_text(item["domanda"])
        if user_normalized in domanda_norm or domanda_norm in user_normalized:
            return {'match': True, 'item': item, 'score': 1.0}
        score = calculate_similarity(user_normalized, domanda_norm)
        if score > best_score:
            best_score = score
            best_match = item
    
    if best_score >= FUZZY_THRESHOLD:
        return {'match': True, 'item': best_match, 'score': best_score}
    return {'match': False}

def fuzzy_search_lista(user_message: str, lista_text: str) -> dict:
    """Cerca righe specifiche nel listino per singoli prodotti"""
    if not lista_text:
        return {'match': False}
    user_normalized = normalize_text(user_message)
    words = [w for w in user_normalized.split() if len(w) > 3]
    if not words:
        return {'match': False}
    lines = lista_text.split('\n')
    best_lines = []
    for line in lines:
        if any(w in normalize_text(line) for w in words):
            best_lines.append(line.strip())
    if best_lines:
        return {'match': True, 'snippet': '\n'.join(best_lines[:5])}
    return {'match': False}

def has_payment_method(text: str) -> bool:
    """Verifica presenza metodo pagamento"""
    if not text: return False
    return any(kw in text.lower() for kw in PAYMENT_KEYWORDS)

def looks_like_order(text: str) -> bool:
    """Verifica struttura potenziale ordine"""
    if not text: return False
    return bool(re.search(r'\d', text)) and len(text.strip()) >= 5

# ---------------------------------------------------------
# HANDLERS: COMANDI UTENTE E ADMIN
# ---------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user is None: return
    if context.args and context.args[0] == load_access_code():
        authorize_user(user.id, user.first_name, user.last_name, user.username)
        await update.message.reply_text("✅ Accesso autorizzato!")
        if ADMIN_CHAT_ID:
            await context.bot.send_message(ADMIN_CHAT_ID, f"🆕 Utente autorizzato: {user.first_name}")
        return
    if is_user_authorized(user.id):
        await update.message.reply_text(f"👋 Ciao {user.first_name}!")
    else:
        await update.message.reply_text("❌ Accesso negato.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra regolamento completo"""
    if not is_user_authorized(update.effective_user.id): return
    faq_data = load_json_file(FAQ_FILE, default={"faq": []})
    faq_list = faq_data.get("faq", [])
    if not faq_list:
        await update.message.reply_text("⚠️ Regolamento non disponibile.")
        return
    full_text = "🗒️ <b>REGOLAMENTO COMPLETO</b>\n\n"
    for item in faq_list:
        full_text += f"🔹 <b>{item['domanda']}</b>\n{item['risposta']}\n\n"
    for i in range(0, len(full_text), 4000):
        await update.message.reply_text(full_text[i:i+4000], parse_mode='HTML')

async def lista_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Invia listino completo"""
    if not is_user_authorized(update.effective_user.id): return
    update_lista_from_web()
    text = load_lista()
    if not text:
        await update.message.reply_text("❌ Listino non disponibile.")
        return
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i+4000])

async def ordine_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra all'admin gli ordini confermati oggi (solo in chat privata)"""
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type

    if user_id != ADMIN_CHAT_ID:
        return
    if chat_type != "private":
        await update.message.reply_text("⚠️ Questo comando funziona solo in chat privata.")
        return

    ordini = load_json_file(ORDINI_FILE, default=[])
    oggi = datetime.now().strftime("%Y-%m-%d")
    ordini_oggi = [o for o in ordini if o['data'] == oggi]

    if not ordini_oggi:
        await update.message.reply_text(f"📅 <b>Ordini del {oggi}:</b>\nNessun ordine confermato oggi.", parse_mode="HTML")
        return

    msg = f"📅 <b>ORDINI CONFERMATI OGGI ({oggi}):</b>\n\n"
    for i, o in enumerate(ordini_oggi, 1):
        msg += f"{i}. 👤 {o['utente']}\n⏰ Ore: {o['ora']}\n📝 <code>{o['testo']}</code>\n\n"
        if len(msg) > 3500:
            await update.message.reply_text(msg, parse_mode="HTML")
            msg = ""
    if msg:
        await update.message.reply_text(msg, parse_mode="HTML")

async def admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Aiuto per amministratori"""
    if update.effective_user.id != ADMIN_CHAT_ID: return
    msg = (
        "👑 <b>PANNELLO ADMIN</b>\n\n"
        "• /genera_link - Crea link accesso\n"
        "• /lista_autorizzati - Elenco utenti\n"
        "• /ordine - Ordini di oggi (solo in privata)\n"
        "• /aggiorna_faq - Sync FAQ\n"
        "• /aggiorna_lista - Sync Listino"
    )
    await update.message.reply_text(msg, parse_mode='HTML')

# ---------------------------------------------------------
# GESTIONE MESSAGGI: PRIVATI E GRUPPI
# ---------------------------------------------------------

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Logica intelligente chat privata"""
    message = update.message
    if not message or not message.text: return
    text = message.text.strip()

    if is_requesting_lista_full(text):
        lista = load_lista()
        if lista:
            for i in range(0, len(lista), 4000): await message.reply_text(lista[i:i+4000])
            return

    if looks_like_order(text) and not has_payment_method(text):
        keyboard = [[InlineKeyboardButton("✅ Sì", callback_data=f"pay_ok_{message.message_id}"), InlineKeyboardButton("❌ No", callback_data=f"pay_no_{message.message_id}")]]
        await message.reply_text("🤔 <b>Ordine rilevato!</b> Hai specificato il pagamento?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return

    faq_data = load_json_file(FAQ_FILE, default={"faq": []})
    res = fuzzy_search_faq(text, faq_data.get("faq", []))
    if res['match']:
        await message.reply_text(f"✅ <b>{res['item']['domanda']}</b>\n\n{res['item']['risposta']}", parse_mode="HTML")
        return

    l_res = fuzzy_search_lista(text, load_lista())
    if l_res['match']:
        await message.reply_text(f"📦 <b>Trovato nel listino:</b>\n\n{l_res['snippet']}", parse_mode="HTML")

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Logica intelligente chat di gruppo"""
    message = update.message or update.channel_post
    if not message or not message.text: return
    if message.from_user and message.from_user.is_bot: return
    text = message.text.strip()
    
    if is_requesting_lista_full(text):
        lista = load_lista()
        if lista:
            for i in range(0, len(lista), 4000):
                await context.bot.send_message(chat_id=message.chat.id, text=lista[i:i+4000], reply_to_message_id=message.message_id)
            return

    if looks_like_order(text) and not has_payment_method(text):
        keyboard = [[InlineKeyboardButton("✅ Sì", callback_data=f"pay_ok_{message.message_id}"), InlineKeyboardButton("❌ No", callback_data=f"pay_no_{message.message_id}")]]
        await context.bot.send_message(chat_id=message.chat.id, text="🤔 <b>Pagamento indicato?</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML", reply_to_message_id=message.message_id)
        return

    faq_data = load_json_file(FAQ_FILE, default={"faq": []})
    res = fuzzy_search_faq(text, faq_data.get("faq", []))
    if res['match']:
        await context.bot.send_message(chat_id=message.chat.id, text=f"✅ <b>{res['item']['domanda']}</b>\n\n{res['item']['risposta']}", parse_mode="HTML", reply_to_message_id=message.message_id)

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestione bottoni e salvataggio ordini"""
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("pay_ok_"):
        original_msg = query.message.reply_to_message
        order_text = original_msg.text if original_msg else "Testo non disponibile"
        user = query.from_user
        user_info = f"{user.first_name} (@{user.username if user.username else 'N/A'}) [<code>{user.id}</code>]"
        
        save_conferma_ordine(user_info, order_text)
        await query.edit_message_text("✅ Ottimo! Ordine registrato. L'admin potrà visualizzarlo con /ordine.")
        
    elif query.data.startswith("pay_no_"):
        await query.edit_message_text("💡 Per favore, indica il metodo (es. PayPal, Bonifico, Crypto).")

# ---------------------------------------------------------
# SETUP SERVER E WEBHOOK
# ---------------------------------------------------------

async def setup_bot():
    global bot_application, initialization_lock
    if initialization_lock: return None
    initialization_lock = True
    
    update_faq_from_web()
    update_lista_from_web()
    
    application = Application.builder().token(BOT_TOKEN).updater(None).build()
    bot_info = await application.bot.get_me()
    get_bot_username.username = bot_info.username
    
    # Registrazione Handler
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("lista", lista_command))
    application.add_handler(CommandHandler("ordine", ordine_command))
    application.add_handler(CommandHandler("admin_help", admin_help_command))
    application.add_handler(CommandHandler("aggiorna_faq", lambda u, c: update_faq_from_web()))
    application.add_handler(CommandHandler("aggiorna_lista", lambda u, c: update_lista_from_web()))
    application.add_handler(CommandHandler("genera_link", lambda u, c: u.message.reply_text(f"https://t.me/{get_bot_username.username}?start={load_access_code()}")))
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_private_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP | filters.ChatType.CHANNEL), handle_group_message))

    if WEBHOOK_URL:
        await application.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
        
    await application.initialize()
    await application.start()
    return application

@app.route('/webhook', methods=['POST'])
def webhook():
    global bot_application, bot_initialized
    if not bot_initialized:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        bot_application = loop.run_until_complete(setup_bot())
        bot_initialized = True
    update = Update.de_json(request.get_json(force=True), bot_application.bot)
    asyncio.get_event_loop().run_until_complete(bot_application.process_update(update))
    return "OK", 200

@app.route('/')
def index(): return "Bot Active", 200

@app.route('/health')
def health_check():
    return "OK", 200
    
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT)

#End main.py
