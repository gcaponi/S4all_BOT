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

# ---------------------------------------------------------
# CONFIGURAZIONE LOGGING
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

AUTHORIZED_USERS_FILE = 'authorized_users.json'
ACCESS_CODE_FILE = 'access_code.json'
FAQ_FILE = 'faq.json'
LISTA_URL = "https://justpaste.it/lista_4all"
LISTA_FILE = "lista.txt"
PASTE_URL = "https://justpaste.it/faq_4all"
FUZZY_THRESHOLD = 0.6

PAYMENT_KEYWORDS = [
    "contanti", "carta", "bancomat", "bonifico", "paypal", "satispay", 
    "postepay", "pos", "wallet", "ricarica", "usdt", "crypto", 
    "cripto", "bitcoin", "bit", "btc", "eth", "usdc"
]

app = Flask(__name__)
bot_application = None
bot_initialized = False
initialization_lock = False

# ---------------------------------------------------------
# UTILS: WEB FETCH, PARSING, I/O
# ---------------------------------------------------------

def fetch_markdown_from_html(url: str) -> str:
    """Scarica il contenuto da JustPaste.it"""
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    content = soup.select_one("#articleContent")
    if content is None:
        raise RuntimeError("Contenuto non trovato nel selettore #articleContent")
    return content.get_text("\n").strip()

def parse_faq(markdown: str) -> list:
    """Parsa le FAQ basandosi sui titoli ##"""
    pattern = r"^##\s+(.*?)\n(.*?)(?=\n##\s+|\Z)"
    matches = re.findall(pattern, markdown, flags=re.S | re.M)
    if not matches:
        raise RuntimeError("Formato FAQ non valido (mancano i titoli ##)")
    return [{"domanda": d.strip(), "risposta": r.strip()} for d, r in matches]

def write_faq_json(faq: list, filename: str):
    """Salva le FAQ in un file JSON locale"""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"faq": faq}, f, ensure_ascii=False, indent=2)

def update_faq_from_web():
    """Aggiorna le FAQ scaricandole dal web"""
    try:
        markdown = fetch_markdown_from_html(PASTE_URL)
        faq = parse_faq(markdown)
        write_faq_json(faq, FAQ_FILE)
        logger.info(f"FAQ aggiornate correttamente: {len(faq)} elementi")
        return True
    except Exception as e:
        logger.error(f"Errore durante l'aggiornamento FAQ: {e}")
        return False

def update_lista_from_web():
    """Aggiorna il file lista.txt scaricandolo dal web"""
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
        logger.info("Lista prodotti aggiornata correttamente")
        return True
    except Exception as e:
        logger.error(f"Errore durante l'aggiornamento lista: {e}")
        return False

def load_lista():
    """Carica la lista dal file locale"""
    try:
        with open(LISTA_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

def load_json_file(filename, default=None):
    """Carica un file JSON generico"""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return default if default is not None else {}

def save_json_file(filename, data):
    """Salva un file JSON generico"""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_authorized_users():
    """Carica gli utenti autorizzati gestendo i formati vecchi"""
    data = load_json_file(AUTHORIZED_USERS_FILE, default={})
    if isinstance(data, list):
        return {str(uid): {"id": uid, "name": "Sconosciuto", "username": None} for uid in data}
    return data

def save_authorized_users(users):
    """Salva la lista utenti autorizzati"""
    save_json_file(AUTHORIZED_USERS_FILE, users)

def load_access_code():
    """Carica o genera il codice di accesso iniziale"""
    data = load_json_file(ACCESS_CODE_FILE, default={})
    if not data.get('code'):
        code = secrets.token_urlsafe(12)
        data = {'code': code}
        save_json_file(ACCESS_CODE_FILE, data)
    return data['code']

def save_access_code(code):
    """Salva un nuovo codice di accesso"""
    save_json_file(ACCESS_CODE_FILE, {'code': code})

def load_faq():
    """Carica le FAQ dal file JSON locale"""
    return load_json_file(FAQ_FILE)

def is_user_authorized(user_id):
    """Controlla se un utente è nel file autorizzati"""
    return str(user_id) in load_authorized_users()

def authorize_user(user_id, first_name=None, last_name=None, username=None):
    """Aggiunge un nuovo utente agli autorizzati"""
    authorized_users = load_authorized_users()
    user_id_str = str(user_id)
    if user_id_str not in authorized_users:
        full_name = f"{first_name or ''} {last_name or ''}".strip() or "Sconosciuto"
        authorized_users[user_id_str] = {
            "id": user_id, 
            "name": full_name, 
            "username": username
        }
        save_authorized_users(authorized_users)
        return True
    return False

def get_bot_username():
    """Recupera l'username del bot memorizzato"""
    return getattr(get_bot_username, 'username', 'tuobot')

# ---------------------------------------------------------
# LOGICHE DI RICERCA FUZZY E NORMALIZZAZIONE
# ---------------------------------------------------------

def calculate_similarity(text1: str, text2: str) -> float:
    """Calcola la similitudine tra due stringhe"""
    return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

def normalize_text(text: str) -> str:
    """Rimuove punteggiatura e normalizza spazi"""
    text = re.sub(r'[^\w\s]', '', text)
    return re.sub(r'\s+', ' ', text).strip().lower()

def extract_keywords(text: str) -> list:
    """Estrae parole chiave significative (lunghezza > 3)"""
    words = normalize_text(text).split()
    stop_words = {
        'che', 'sono', 'come', 'dove', 'quando', 'quale', 'quali', 
        'del', 'della', 'dei', 'delle', 'con', 'per', 'una', 'uno'
    }
    return [w for w in words if len(w) > 3 and w not in stop_words]

def is_requesting_lista(text: str) -> bool:
    """
    Controlla se l'utente sta chiedendo esplicitamente la lista prodotti.
    Utilizza la lista di parole chiave fornita dall'utente.
    """
    if not text:
        return False
    
    t = normalize_text(text)
    
    keywords = [
        "lista", "prodotti", "hai la lista", "che prodotti hai", "mostrami la lista", 
        "fammi vedere la lista", "hai lista", "voglio la lista", "mandami la lista", 
        "inviami la lista", "lista prodotti", "elenco prodotti", "quali prodotti ci sono", 
        "cosa vendi", "cosa hai", "mostra prodotti", "fammi vedere i prodotti", 
        "lista aggiornata", "lista completa", "lista prezzi", "lista disponibile", 
        "lista articoli", "elenco articoli", "elenco disponibile", "prodotti disponibili", 
        "prodotti in vendita", "catalogo prodotti", "catalogo", "catalogo aggiornato", 
        "catalogo prezzi", "puoi mandarmi la lista", "puoi mostrarmi la lista", 
        "puoi inviarmi la lista", "voglio vedere la lista", "voglio vedere i prodotti"
    ]
    
    # Controlla se una qualsiasi delle frasi chiave è presente nel testo normalizzato
    for kw in keywords:
        if normalize_text(kw) in t:
            return True
            
    return False

def fuzzy_search_faq(user_message: str, faq_list: list) -> dict:
    """Cerca la risposta migliore nelle FAQ"""
    user_normalized = normalize_text(user_message)
    user_keywords = extract_keywords(user_message)
    best_match = None
    best_score = 0
    match_method = None

    for item in faq_list:
        domanda_normalized = normalize_text(item["domanda"])
        
        # 1. Match Esatto (Contenimento)
        if domanda_normalized in user_normalized or user_normalized in domanda_normalized:
            return {'match': True, 'item': item, 'score': 1.0, 'method': 'exact'}

        # 2. Similitudine Testuale
        similarity = calculate_similarity(user_normalized, domanda_normalized)
        if similarity > best_score:
            best_score = similarity
            best_match = item
            match_method = 'similarity'

        # 3. Match per Parole Chiave
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
    """Cerca prodotti specifici all'interno della lista testuale"""
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
    """Controlla se il testo contiene riferimenti ai pagamenti"""
    if not text:
        return False
    return any(kw in text.lower() for kw in PAYMENT_KEYWORDS)

def looks_like_order(text: str) -> bool:
    """Euristica per capire se il messaggio è un ordine (numeri + lunghezza)"""
    if not text:
        return False
    return bool(re.search(r'\d', text)) and len(text.strip()) >= 5

# ---------------------------------------------------------
# HANDLERS: COMANDI
# ---------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestisce il comando /start (autorizzazione e benvenuto)"""
    user = update.effective_user
    if user is None:
        return
    user_id = user.id

    # Controllo se è presente un codice di avvio (?start=CODICE)
    if context.args:
        if context.args[0] == load_access_code():
            was_new = authorize_user(user_id, user.first_name, user.last_name, user.username)
            if was_new:
                await update.message.reply_text("✅ Autorizzazione completata! Ora puoi usare /help.")
                if ADMIN_CHAT_ID:
                    try:
                        await context.bot.send_message(
                            ADMIN_CHAT_ID, 
                            f"✅ Nuovo utente autorizzato: {user.first_name} (@{user.username})"
                        )
                    except Exception:
                        logger.exception("Impossibile inviare notifica all'admin")
            else:
                await update.message.reply_text("✅ Sei già autorizzato a usare questo bot!")
            return
        else:
            await update.message.reply_text("❌ Codice di accesso non valido.")
            return

    # Messaggio per chi è già autorizzato
    if is_user_authorized(user_id):
        await update.message.reply_text(f"👋 Ciao {user.first_name}! Sono pronto. Usa /help per info.")
    else:
        await update.message.reply_text("❌ Non sei autorizzato. Contatta l'amministratore.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Invia le FAQ complete all'utente"""
    if update.effective_user is None or not is_user_authorized(update.effective_user.id):
        await update.message.reply_text("❌ Non autorizzato.")
        return

    faq_data = load_faq()
    faq_list = faq_data.get("faq", []) if faq_data else []
    
    if not faq_list:
        await update.message.reply_text("❌ Nessuna FAQ caricata nel sistema.")
        return

    # Costruzione del messaggio di help
    header = (
        "🗒️ <b>INFO E REGOLAMENTO</b> 🗒️\n\n"
        "Per favore prima di fare domande leggi tutto il listino. "
        "Qui troverai info su spedizioni, pagamenti e ordini.\n\n"
        "📍 <i>La qualità è la nostra priorità.</i>\n\n"
    )
    
    full_text = header
    for item in faq_list:
        full_text += f"🔹 <b>{item['domanda']}</b>\n{item['risposta']}\n\n"

    full_text += "💡 <i>Puoi scrivermi le tue domande anche con errori di battitura!</i>"

    # Split del messaggio se troppo lungo per Telegram
    max_length = 4000
    if len(full_text) <= max_length:
        await update.message.reply_text(full_text, parse_mode='HTML')
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
            await update.message.reply_text(part, parse_mode='HTML')

async def lista_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Invia la lista prodotti completa"""
    try:
        update_lista_from_web()
    except Exception:
        logger.exception("Errore durante l'aggiornamento automatico della lista")

    lista_text = load_lista()
    if not lista_text:
        await update.message.reply_text("❌ Lista non disponibile al momento.")
        return

    max_len = 4000
    for i in range(0, len(lista_text), max_len):
        await update.message.reply_text(lista_text[i:i+max_len])

# ---------------------------------------------------------
# HANDLERS: COMANDI AMMINISTRATORE
# ---------------------------------------------------------

async def genera_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Genera il link di invito attuale"""
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Comando riservato all'amministratore.")
        return

    link = f"https://t.me/{get_bot_username.username}?start={load_access_code()}"
    await update.message.reply_text(
        f"🔗 <b>Link di autorizzazione attuale:</b>\n<code>{link}</code>\n\n"
        f"👥 Utenti autorizzati: {len(load_authorized_users())}", 
        parse_mode='HTML'
    )

async def cambia_codice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Cambia il codice segreto di invito"""
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Comando riservato all'amministratore.")
        return

    new_code = secrets.token_urlsafe(12)
    save_access_code(new_code)
    link = f"https://t.me/{get_bot_username.username}?start={new_code}"
    await update.message.reply_text(
        f"✅ <b>Codice cambiato!</b>\nNuovo link:\n<code>{link}</code>", 
        parse_mode='HTML'
    )

async def lista_autorizzati_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Mostra l'elenco degli ID autorizzati"""
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Comando riservato all'amministratore.")
        return

    users = load_authorized_users()
    if not users:
        await update.message.reply_text("📋 Non ci sono utenti autorizzati.")
        return

    msg = f"👥 <b>Utenti Autorizzati ({len(users)}):</b>\n\n"
    for i, (uid, data) in enumerate(users.items(), 1):
        name = data.get('name', 'N/A')
        username = data.get('username', 'N/A')
        msg += f"{i}. {name} (@{username}) - <code>{uid}</code>\n"
    
    await update.message.reply_text(msg, parse_mode='HTML')

async def revoca_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Revoca l'accesso a un utente tramite ID"""
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Comando riservato all'amministratore.")
        return

    if not context.args:
        await update.message.reply_text("❌ Specifica l'ID: /revoca 123456789")
        return

    try:
        target_id = str(int(context.args[0]))
        users = load_authorized_users()
        if target_id in users:
            del users[target_id]
            save_authorized_users(users)
            await update.message.reply_text(f"✅ Accesso revocato per l'ID {target_id}.")
        else:
            await update.message.reply_text(f"❌ ID {target_id} non trovato negli autorizzati.")
    except Exception:
        await update.message.reply_text("❌ ID non valido.")

async def aggiorna_faq_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Forza l'aggiornamento delle FAQ dal web"""
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Comando riservato all'amministratore.")
        return

    await update.message.reply_text("⏳ Aggiornamento FAQ in corso...")
    if update_faq_from_web():
        faq_list = load_faq().get("faq", [])
        await update.message.reply_text(f"✅ FAQ aggiornate! Totale: {len(faq_list)}")
    else:
        await update.message.reply_text("❌ Errore durante l'aggiornamento.")

async def aggiorna_lista_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Forza l'aggiornamento della lista dal web"""
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Solo admin.")
        return

    await update.message.reply_text("⏳ Aggiornamento lista in corso...")
    if update_lista_from_web():
        await update.message.reply_text("✅ Lista prodotti aggiornata con successo!")
    else:
        await update.message.reply_text("❌ Errore aggiornamento lista.")

async def admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Mostra i comandi riservati"""
    if update.effective_user is None or update.effective_user.id != ADMIN_CHAT_ID:
        return

    msg = (
        "👑 <b>PANNELLO AMMINISTRATORE</b>\n\n"
        "🔐 <b>Gestione Accessi:</b>\n"
        "• /genera_link - Mostra link attivo\n"
        "• /cambia_codice - Genera nuovo link\n"
        "• /lista_autorizzati - Elenco utenti\n"
        "• /revoca [ID] - Rimuovi utente\n\n"
        "🔄 <b>Aggiornamento Dati:</b>\n"
        "• /aggiorna_faq - Ricarica FAQ dal web\n"
        "• /aggiorna_lista - Ricarica Lista dal web"
    )
    await update.message.reply_text(msg, parse_mode='HTML')

# ---------------------------------------------------------
# MESSAGGI PRIVATI
# ---------------------------------------------------------

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestisce l'interazione testuale nei messaggi privati"""
    message = update.message
    if not message or not getattr(message, "text", None):
        return

    text = message.text.strip()
    
    # --- 1. CONTROLLO PRIORITARIO: RICHIESTA LISTA ---
    if is_requesting_lista(text):
        lista_text = load_lista()
        if not lista_text:
            await message.reply_text("❌ La lista non è al momento disponibile.")
            return
            
        max_len = 4000
        for i in range(0, len(lista_text), max_len):
            await message.reply_text(lista_text[i:i+max_len], parse_mode="HTML")
        return

    # --- 2. CONTROLLO ORDINE SENZA PAGAMENTO ---
    if looks_like_order(text) and not has_payment_method(text):
        keyboard = [
            [
                InlineKeyboardButton("✅ Sì, l'ho scritto", callback_data=f"pay_ok_{message.message_id}"),
                InlineKeyboardButton("❌ No, scusa", callback_data=f"pay_no_{message.message_id}")
            ]
        ]
        await message.reply_text(
            "🤔 <b>Sembra un ordine, ma mancano i dettagli di pagamento.</b>\n\n"
            "Hai specificato come intendi pagare? (Es. Crypto, Ricarica, ecc.)",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    # --- 3. RICERCA NELLE FAQ ---
    faq = load_faq()
    if faq and faq.get("faq"):
        result = fuzzy_search_faq(text, faq.get("faq", []))
        if result['match'] and result['score'] > 0.75:
            item = result['item']
            emoji = "🎯" if result['score'] > 0.9 else "✅"
            resp = f"{emoji} <b>{item['domanda']}</b>\n\n{item['risposta']}"
            await message.reply_text(resp, parse_mode='HTML')
            return

    # --- 4. RICERCA PRODOTTI NELLA LISTA ---
    lista_text = load_lista()
    if lista_text:
        result = fuzzy_search_lista(text, lista_text)
        if result['match'] and result['score'] > 0.3:
            emoji = "🎯" if result['score'] > 0.7 else "📦"
            resp = f"{emoji} <b>Ho trovato questi prodotti:</b>\n\n{result['snippet']}"
            await message.reply_text(resp, parse_mode='HTML')
            return

    # --- 5. FALLBACK ---
    await message.reply_text("❓ Non ho capito. Prova a scriverlo diversamente o usa /help.")

# ---------------------------------------------------------
# MESSAGGI NEI GRUPPI / CANALI
# ---------------------------------------------------------

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestisce l'interazione nei gruppi e canali"""
    message = update.message or update.channel_post
    if not message or not getattr(message, "text", None):
        return

    text = message.text.strip()
    chat_id = message.chat.id
    user = getattr(message, "from_user", None)
    
    # Benvenuto al primo messaggio dell'utente nel gruppo
    if user and user.id and chat_id:
        greeted_key = f"greeted_{chat_id}_{user.id}"
        if not context.bot_data.get(greeted_key):
            context.bot_data[greeted_key] = True
            welcome = f"👋 Benvenuto {user.first_name}! Per info e prezzi scrivi /lista o /help."
            await context.bot.send_message(chat_id=chat_id, text=welcome)

    # --- 1. PRIORITÀ: LISTA ---
    if is_requesting_lista(text):
        lista_text = load_lista()
        if lista_text:
            max_len = 4000
            for i in range(0, len(lista_text), max_len):
                await context.bot.send_message(chat_id=chat_id, text=lista_text[i:i+max_len])
            return

    # --- 2. CONTROLLO ORDINE ---
    if looks_like_order(text) and not has_payment_method(text):
        await context.bot.send_message(
            chat_id=chat_id, 
            text="⚠️ Ricordati di specificare il metodo di pagamento per velocizzare l'ordine!"
        )
        return

    # --- 3. FAQ ---
    faq = load_faq()
    if faq and faq.get("faq"):
        result = fuzzy_search_faq(text, faq.get("faq", []))
        if result['match'] and result['score'] > 0.8: # Confidenza più alta per i gruppi
            await context.bot.send_message(
                chat_id=chat_id, 
                text=f"✅ <b>{result['item']['domanda']}</b>\n\n{result['item']['risposta']}",
                parse_mode="HTML"
            )

# ---------------------------------------------------------
# CALLBACK E STATUS UPDATE
# ---------------------------------------------------------

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestisce i click sui bottoni inline"""
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("pay_ok_"):
        await query.edit_message_text(f"✅ Ottimo! Provvederemo a gestire la tua richiesta.")
    elif query.data.startswith("pay_no_"):
        await query.edit_message_text("💡 Per favore, specifica il metodo di pagamento nel prossimo messaggio.")

async def handle_user_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Invia il benvenuto ai nuovi membri del gruppo"""
    if not update.message or not update.message.new_chat_members:
        return
    
    for member in update.message.new_chat_members:
        welcome = f"👋 Benvenuto {member.first_name}!\n\nLeggi le FAQ con /help e guarda i prodotti con /lista."
        await context.bot.send_message(chat_id=update.message.chat.id, text=welcome)

# ---------------------------------------------------------
# SETUP E FLASK WEBHOOK
# ---------------------------------------------------------

async def setup_bot():
    """Inizializza l'applicazione del bot"""
    global bot_application, initialization_lock
    
    if initialization_lock:
        return None
        
    initialization_lock = True
    
    try:
        # Aggiornamento dati iniziale
        update_faq_from_web()
        update_lista_from_web()
        
        application = Application.builder().token(BOT_TOKEN).updater(None).build()
        
        # Recupero info bot
        bot_info = await application.bot.get_me()
        get_bot_username.username = bot_info.username
        
        # Registrazione Handler
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("lista", lista_command))
        
        # Handler Admin
        application.add_handler(CommandHandler("genera_link", genera_link_command))
        application.add_handler(CommandHandler("cambia_codice", cambia_codice_command))
        application.add_handler(CommandHandler("lista_autorizzati", lista_autorizzati_command))
        application.add_handler(CommandHandler("revoca", revoca_command))
        application.add_handler(CommandHandler("aggiorna_faq", aggiorna_faq_command))
        application.add_handler(CommandHandler("aggiorna_lista", aggiorna_lista_command))
        application.add_handler(CommandHandler("admin_help", admin_help_command))
        
        # Handler Messaggi e Status
        application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_user_status))
        application.add_handler(CallbackQueryHandler(handle_callback_query))
        
        # Filtri messaggi
        group_filter = (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP | filters.ChatType.CHANNEL)
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & group_filter, handle_group_message))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_private_message))

        # Impostazione Webhook
        if WEBHOOK_URL:
            await application.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
            logger.info(f"Webhook impostato su {WEBHOOK_URL}/webhook")

        await application.initialize()
        await application.start()
        logger.info("Bot Telegram avviato con successo!")
        return application
        
    except Exception as e:
        logger.error(f"Errore fatale durante il setup: {e}")
        initialization_lock = False
        raise

@app.route('/')
def index():
    return "🤖 Bot Online. Status: OK ✅", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    global bot_application, bot_initialized
    
    if not bot_initialized:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            bot_application = loop.run_until_complete(setup_bot())
            bot_initialized = True
        except Exception as e:
            return f"Errore inizializzazione: {e}", 503
    
    # Elaborazione update
    try:
        update = Update.de_json(request.get_json(force=True), bot_application.bot)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(bot_application.process_update(update))
        return "OK", 200
    except Exception as e:
        logger.error(f"Errore elaborazione webhook: {e}")
        return "Error", 500

if __name__ == '__main__':
    # Avvio Flask
    app.run(host='0.0.0.0', port=PORT)
