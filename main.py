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
# UTILS: WEB FETCH, PARSING, I/O
# ---------------------------------------------------------

def fetch_markdown_from_html(url: str) -> str:
    """Scarica il contenuto HTML da JustPaste e lo converte in testo"""
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        content = soup.select_one("#articleContent")
        if content is None:
            raise RuntimeError("Contenuto non trovato nel selettore #articleContent")
        return content.get_text("\n").strip()
    except Exception as e:
        logger.error(f"Errore fetch: {e}")
        return ""

def parse_faq(markdown: str) -> list:
    """Parsa le FAQ basandosi sui titoli ## del markdown"""
    pattern = r"^##\s+(.*?)\n(.*?)(?=\n##\s+|\Z)"
    matches = re.findall(pattern, markdown, flags=re.S | re.M)
    if not matches:
        return []
    return [{"domanda": d.strip(), "risposta": r.strip()} for d, r in matches]

def write_faq_json(faq: list, filename: str):
    """Salva le FAQ in un file JSON"""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"faq": faq}, f, ensure_ascii=False, indent=2)

def update_faq_from_web():
    """Aggiorna le FAQ scaricandole dal link JustPaste"""
    markdown = fetch_markdown_from_html(PASTE_URL)
    if markdown:
        faq = parse_faq(markdown)
        if faq:
            write_faq_json(faq, FAQ_FILE)
            logger.info(f"FAQ aggiornate: {len(faq)} elementi")
            return True
    return False

def update_lista_from_web():
    """Aggiorna il file lista.txt scaricandolo dal web"""
    try:
        r = requests.get(LISTA_URL, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        content = soup.select_one("#articleContent")
        if content:
            text = content.get_text("\n").strip()
            with open(LISTA_FILE, "w", encoding="utf-8") as f:
                f.write(text)
            logger.info("Listino prodotti aggiornato correttamente")
            return True
    except Exception as e:
        logger.error(f"Errore aggiornamento lista: {e}")
    return False

def load_lista():
    """Carica il testo del listino dal file locale"""
    if os.path.exists(LISTA_FILE):
        with open(LISTA_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return ""

def load_json_file(filename, default=None):
    """Carica un file JSON in modo sicuro"""
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Errore lettura {filename}: {e}")
    return default if default is not None else {}

def save_json_file(filename, data):
    """Salva dati in un file JSON"""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_authorized_users():
    """Carica la mappa degli utenti autorizzati"""
    data = load_json_file(AUTHORIZED_USERS_FILE, default={})
    if isinstance(data, list):
        # Gestione compatibilità vecchio formato lista
        return {str(uid): {"id": uid, "name": "Utente", "username": None} for uid in data}
    return data

def save_authorized_users(users):
    """Salva la mappa degli utenti autorizzati"""
    save_json_file(AUTHORIZED_USERS_FILE, users)

def load_access_code():
    """Carica o genera il codice segreto per il link /start"""
    data = load_json_file(ACCESS_CODE_FILE, default={})
    if not data.get('code'):
        code = secrets.token_urlsafe(12)
        save_json_file(ACCESS_CODE_FILE, {'code': code})
        return code
    return data['code']

def save_access_code(code):
    """Aggiorna il codice di accesso nel file"""
    save_json_file(ACCESS_CODE_FILE, {'code': code})

def load_faq():
    """Carica le FAQ dal file JSON locale"""
    return load_json_file(FAQ_FILE, default={"faq": []})

def is_user_authorized(user_id):
    """Controlla se un ID utente è presente negli autorizzati"""
    return str(user_id) in load_authorized_users()

def authorize_user(user_id, first_name=None, last_name=None, username=None):
    """Aggiunge un nuovo utente alla lista autorizzati"""
    users = load_authorized_users()
    user_id_str = str(user_id)
    if user_id_str not in users:
        full_name = f"{first_name or ''} {last_name or ''}".strip() or "Sconosciuto"
        users[user_id_str] = {
            "id": user_id, 
            "name": full_name, 
            "username": username
        }
        save_authorized_users(users)
        return True
    return False

def get_bot_username():
    """Restituisce lo username del bot salvato in memoria"""
    return getattr(get_bot_username, 'username', 'tuobot')

# ---------------------------------------------------------
# LOGICHE DI RICERCA (LISTA + FAQ INTELLIGENTE)
# ---------------------------------------------------------

def calculate_similarity(text1: str, text2: str) -> float:
    """Calcola la somiglianza tra due stringhe (0.0 a 1.0)"""
    return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

def normalize_text(text: str) -> str:
    """Pulisce il testo da simboli e spazi extra"""
    text = re.sub(r'[^\w\s]', '', text)
    return re.sub(r'\s+', ' ', text).strip().lower()

def is_requesting_lista_full(text: str) -> bool:
    """Determina se l'utente sta chiedendo esplicitamente il listino completo"""
    if not text:
        return False
    
    normalized_msg = normalize_text(text)
    
    # 1. Controllo corrispondenza parole chiave
    if any(kw in normalized_msg for kw in LISTA_KEYWORDS_FULL):
        return True
    
    # 2. Controllo Fuzzy sulle radici (per errori tipo "listno")
    words = normalized_msg.split()
    smart_roots = ["listino", "catalogo", "prodotti", "prezzi", "articoli", "lista"]
    for word in words:
        for root in smart_roots:
            if calculate_similarity(word, root) > 0.85:
                return True
                
    return False

def fuzzy_search_faq(user_message: str, faq_list: list) -> dict:
    """Ricerca avanzata nelle FAQ usando parole chiave e somiglianza"""
    user_normalized = normalize_text(user_message)
    
    # LIVELLO 1: Mappatura Sinonimi Critici (Tracking, Spedizioni, ecc.)
    keywords_map = {
        "spedizione": ["spedito", "spedisci", "spedite", "corriere", "pacco", "invio", "mandato"],
        "tracking": ["track", "codice", "tracciabilità", "tracciamento", "tracking"],
        "tempi": ["quando arriva", "quanto tempo", "giorni", "ricevo", "consegna"],
        "pagamento": ["pagare", "metodi", "bonifico", "ricarica", "paypal", "crypto", "pagamenti"]
    }

    for item in faq_list:
        domanda_norm = normalize_text(item["domanda"])
        risposta_norm = normalize_text(item["risposta"])
        
        for root, synonyms in keywords_map.items():
            if any(syn in user_normalized for syn in synonyms):
                # Se l'utente usa un sinonimo di "tracking", cerchiamo se la FAQ contiene la radice "tracking"
                if root in domanda_norm or root in risposta_norm:
                    logger.info(f"FAQ Match trovata tramite keyword: {root}")
                    return {'match': True, 'item': item, 'score': 1.0}

    # LIVELLO 2: Somiglianza classica sulla domanda
    best_match = None
    best_score = 0
    for item in faq_list:
        domanda_norm = normalize_text(item["domanda"])
        
        # Caso: il messaggio è contenuto esattamente nella domanda
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
    """Cerca righe specifiche nel listino prodotti"""
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
    """Controlla se l'utente ha menzionato un metodo di pagamento"""
    if not text:
        return False
    return any(kw in text.lower() for kw in PAYMENT_KEYWORDS)

def looks_like_order(text: str) -> bool:
    """Verifica se il messaggio somiglia a un ordine (presenza numeri e lunghezza)"""
    if not text:
        return False
    return bool(re.search(r'\d', text)) and len(text.strip()) >= 5

# ---------------------------------------------------------
# HANDLERS: COMANDI UTENTE E ADMIN
# ---------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user is None:
        return
        
    # Controllo se c'è un codice di autorizzazione nel comando /start
    if context.args and context.args[0] == load_access_code():
        authorize_user(user.id, user.first_name, user.last_name, user.username)
        await update.message.reply_text("✅ Accesso autorizzato con successo! Ora puoi usare il bot.")
        if ADMIN_CHAT_ID:
            await context.bot.send_message(ADMIN_CHAT_ID, f"🆕 Nuovo utente autorizzato: {user.first_name} (@{user.username})")
        return

    if is_user_authorized(user.id):
        await update.message.reply_text(f"👋 Bentornato {user.first_name}! Come posso aiutarti oggi?")
    else:
        await update.message.reply_text("❌ Non sei autorizzato. Contatta l'amministratore per il link di accesso.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user_authorized(update.effective_user.id):
        return
        
    faq_data = load_faq()
    faq_list = faq_data.get("faq", [])
    
    if not faq_list:
        await update.message.reply_text("⚠️ Al momento non ci sono FAQ caricate.")
        return
        
    full_text = "🗒️ <b>INFO E REGOLAMENTO COMPLETO</b>\n\n"
    for item in faq_list:
        full_text += f"🔹 <b>{item['domanda']}</b>\n{item['risposta']}\n\n"
        
    # Gestione messaggi lunghi di Telegram
    if len(full_text) > 4000:
        for i in range(0, len(full_text), 4000):
            await update.message.reply_text(full_text[i:i+4000], parse_mode='HTML')
    else:
        await update.message.reply_text(full_text, parse_mode='HTML')

async def lista_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_user_authorized(update.effective_user.id):
        return
        
    update_lista_from_web()
    lista_text = load_lista()
    
    if not lista_text:
        await update.message.reply_text("❌ Listino non disponibile al momento.")
        return
        
    for i in range(0, len(lista_text), 4000):
        await update.message.reply_text(lista_text[i:i+4000])

async def admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    msg = (
        "👑 <b>PANNELLO AMMINISTRATORE</b>\n\n"
        "• /genera_link - Crea link autorizzazione\n"
        "• /cambia_codice - Rigenera codice segreto\n"
        "• /lista_autorizzati - Elenco utenti\n"
        "• /revoca ID - Rimuovi autorizzazione\n"
        "• /aggiorna_faq - Forza sincronizzazione FAQ\n"
        "• /aggiorna_lista - Forza sincronizzazione listino"
    )
    await update.message.reply_text(msg, parse_mode='HTML')

async def aggiorna_faq_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    if update_faq_from_web():
        await update.message.reply_text("✅ FAQ sincronizzate correttamente da JustPaste.")
    else:
        await update.message.reply_text("❌ Errore durante la sincronizzazione FAQ.")

async def aggiorna_lista_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    if update_lista_from_web():
        await update.message.reply_text("✅ Listino prodotti aggiornato correttamente.")
    else:
        await update.message.reply_text("❌ Errore durante l'aggiornamento del listino.")

async def genera_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    link = f"https://t.me/{get_bot_username.username}?start={load_access_code()}"
    await update.message.reply_text(f"🔗 <b>Link di autorizzazione:</b>\n<code>{link}</code>", parse_mode='HTML')

async def lista_autorizzati_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    users = load_authorized_users()
    if not users:
        await update.message.reply_text("Nessun utente autorizzato.")
        return
        
    msg = "👥 <b>UTENTI AUTORIZZATI:</b>\n\n"
    for uid, info in users.items():
        msg += f"- {info['name']} (@{info['username']}) [<code>{uid}</code>]\n"
    await update.message.reply_text(msg, parse_mode='HTML')

async def revoca_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID or not context.args:
        return
    users = load_authorized_users()
    target_id = context.args[0]
    if target_id in users:
        del users[target_id]
        save_authorized_users(users)
        await update.message.reply_text(f"✅ Autorizzazione revocata per l'utente {target_id}.")
    else:
        await update.message.reply_text("❌ Utente non trovato.")

# ---------------------------------------------------------
# GESTIONE MESSAGGI PRIVATI E GRUPPI
# ---------------------------------------------------------

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return
        
    text = message.text.strip()

    # 1. PRIORITÀ: Richiesta lista completa
    if is_requesting_lista_full(text):
        lista = load_lista()
        if lista:
            for i in range(0, len(lista), 4000):
                await message.reply_text(lista[i:i+4000])
            return

    # 2. Controllo ordine senza metodo di pagamento
    if looks_like_order(text) and not has_payment_method(text):
        keyboard = [
            [
                InlineKeyboardButton("✅ Sì, l'ho scritto", callback_data=f"pay_ok_{message.message_id}"),
                InlineKeyboardButton("❌ No, scusa", callback_data=f"pay_no_{message.message_id}")
            ]
        ]
        await message.reply_text(
            "🤔 <b>Sembra un ordine!</b>\nHai specificato il metodo di pagamento scelto?", 
            reply_markup=InlineKeyboardMarkup(keyboard), 
            parse_mode="HTML"
        )
        return

    # 3. Ricerca nelle FAQ (Domanda + Risposta)
    faq_data = load_faq()
    res = fuzzy_search_faq(text, faq_data.get("faq", []))
    if res['match']:
        await message.reply_text(
            f"✅ <b>{res['item']['domanda']}</b>\n\n{res['item']['risposta']}", 
            parse_mode="HTML"
        )
        return

    # 4. Ricerca nel listino (Singoli prodotti)
    l_res = fuzzy_search_lista(text, load_lista())
    if l_res['match']:
        await message.reply_text(
            f"📦 <b>Ho trovato questo nel listino:</b>\n\n{l_res['snippet']}", 
            parse_mode="HTML"
        )
        return

    # 5. Fallback
    await message.reply_text("❓ Non sono sicuro di aver capito. Prova a consultare /help o scrivi 'lista'.")

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message or update.channel_post
    if not message or not message.text:
        return
        
    text = message.text.strip()
    
    # In gruppo rispondiamo solo se troviamo un match forte
    if is_requesting_lista_full(text):
        lista = load_lista()
        if lista:
            for i in range(0, len(lista), 4000):
                await context.bot.send_message(
                    chat_id=message.chat.id, 
                    text=lista[i:i+4000], 
                    reply_to_message_id=message.message_id
                )
            return

    if looks_like_order(text) and not has_payment_method(text):
        keyboard = [
            [
                InlineKeyboardButton("✅ Sì", callback_data=f"pay_ok_{message.message_id}"),
                InlineKeyboardButton("❌ No", callback_data=f"pay_no_{message.message_id}")
            ]
        ]
        await context.bot.send_message(
            chat_id=message.chat.id, 
            text="🤔 <b>Ordine rilevato:</b> hai indicato il metodo di pagamento?", 
            reply_markup=InlineKeyboardMarkup(keyboard), 
            parse_mode="HTML",
            reply_to_message_id=message.message_id
        )
        return

    res = fuzzy_search_faq(text, load_faq().get("faq", []))
    if res['match']:
        await context.bot.send_message(
            chat_id=message.chat.id, 
            text=f"✅ <b>{res['item']['domanda']}</b>\n\n{res['item']['risposta']}", 
            parse_mode="HTML", 
            reply_to_message_id=message.message_id
        )

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("pay_ok_"):
        await query.edit_message_text("✅ Perfetto! Elaborerò il tuo ordine il prima possibile.")
    elif query.data.startswith("pay_no_"):
        await query.edit_message_text("💡 Nessun problema. Per favore specifica come desideri pagare (es. PayPal, Crypto, etc.)")

# ---------------------------------------------------------
# INIZIALIZZAZIONE E CICLO WEBHOOK
# ---------------------------------------------------------

async def setup_bot():
    global bot_application, initialization_lock
    if initialization_lock:
        return None
    initialization_lock = True
    
    # Sincronizzazione iniziale dati
    update_faq_from_web()
    update_lista_from_web()
    
    application = Application.builder().token(BOT_TOKEN).updater(None).build()
    
    # Salviamo lo username del bot per i link
    bot_info = await application.bot.get_me()
    get_bot_username.username = bot_info.username
    logger.info(f"Avvio bot: @{bot_info.username}")
    
    # Registrazione handler comandi
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("lista", lista_command))
    application.add_handler(CommandHandler("admin_help", admin_help_command))
    application.add_handler(CommandHandler("aggiorna_faq", aggiorna_faq_command))
    application.add_handler(CommandHandler("aggiorna_lista", aggiorna_lista_command))
    application.add_handler(CommandHandler("genera_link", genera_link_command))
    application.add_handler(CommandHandler("lista_autorizzati", lista_autorizzati_command))
    application.add_handler(CommandHandler("revoca", revoca_command))
    
    # Registrazione handler messaggi e callback
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_private_message))
    
    # Filtro per gruppi e canali
    group_filter = (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP | filters.ChatType.CHANNEL)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & group_filter, handle_group_message))

    # Configurazione Webhook
    if WEBHOOK_URL:
        await application.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
        logger.info(f"Webhook impostato su: {WEBHOOK_URL}/webhook")
        
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
def index():
    return "Bot Online", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT)
