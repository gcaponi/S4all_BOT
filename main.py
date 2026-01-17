import os
import json
import logging
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ChatMemberHandler, filters, ContextTypes, TypeHandler
import secrets
import re
import requests
from bs4 import BeautifulSoup
from difflib import SequenceMatcher
import asyncio
from datetime import datetime
from intent_classifier import IntentClassifier, IntentType

# Import database module (PostgreSQL)
import database as db

# ============================================================================
# CONFIGURAZIONE LOGGING
# ============================================================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================================================
# VARIABILI DI AMBIENTE E COSTANTI
# ============================================================================
BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_CHAT_ID = int(os.environ.get('ADMIN_CHAT_ID', 0))
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '')
PORT = int(os.environ.get('PORT', 10000))
intent_classifier = None

# File dati
AUTHORIZED_USERS_FILE = 'authorized_users.json'
ACCESS_CODE_FILE = 'access_code.json'
FAQ_FILE = 'faq.json'
LISTA_FILE = "lista.txt"
ORDINI_FILE = "ordini_confermati.json"
USER_TAGS_FILE = 'user_tags.json'  # ← NUOVO

# Link JustPaste.it
LISTA_URL = "https://justpaste.it/lista_4all"
PASTE_URL = "https://justpaste.it/faq_4all"

# Tag clienti consentiti
ALLOWED_TAGS = ['aff', 'jgor5', 'ig5', 'sp20']

# Soglie
FUZZY_THRESHOLD = 0.6
FAQ_CONFIDENCE_THRESHOLD = 0.65
LISTA_CONFIDENCE_THRESHOLD = 0.30

# Keywords pagamento
PAYMENT_KEYWORDS = [
    "bonifico", "usdt", "crypto", "cripto", "bitcoin", "bit", "btc", "eth", "usdc", "xmr"
]

# Inizializzazione Flask
app = Flask(__name__)
bot_application = None
bot_initialized = False
initialization_lock = False

# ============================================================================
# FILTRO CUSTOM PER BUSINESS MESSAGES
# ============================================================================

class BusinessMessageFilter(filters.UpdateFilter):
    """Filtro custom per identificare SOLO messaggi Telegram Business (no callback)"""
    def filter(self, update):
        # Escludi callback_query (bottoni)
        if hasattr(update, 'callback_query') and update.callback_query is not None:
            return False
        
        # Escludi altri tipi di update
        if hasattr(update, 'edited_message') and update.edited_message is not None:
            return False
        
        if hasattr(update, 'channel_post') and update.channel_post is not None:
            return False
        
        # Accetta SOLO business_message
        update_dict = update.to_dict()
        return 'business_message' in update_dict

business_filter = BusinessMessageFilter()

# ============================================================================
# FUNZIONI DATABASE (usa PostgreSQL via database.py)
# ============================================================================

# User tags - usa database.py
get_user_tag = db.get_user_tag
set_user_tag = db.set_user_tag
remove_user_tag = db.remove_user_tag
load_user_tags = db.load_user_tags

# Authorized users - usa database.py
is_user_authorized = db.is_user_authorized
authorize_user = db.authorize_user
load_authorized_users = db.load_authorized_users

# Ordini - usa database.py
add_ordine_confermato = db.add_ordine_confermato
get_ordini_oggi = db.get_ordini_oggi

# Access code - usa database.py
load_access_code = db.load_access_code
save_access_code = db.save_access_code

# ============================================================================
# UTILS: WEB FETCH, PARSING, I/O
# ============================================================================

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
    """Parsa le FAQ basandosi sui titoli ## del markdown del JustPaste"""
    pattern = r"^##\s+(.*?)\n(.*?)(?=\n##\s+|\Z)"
    matches = re.findall(pattern, markdown, flags=re.S | re.M)
    if not matches:
        return []
    return [{"domanda": d.strip(), "risposta": r.strip()} for d, r in matches]

def write_faq_json(faq: list, filename: str):
    """Salva le FAQ strutturate in un file JSON locale"""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"faq": faq}, f, ensure_ascii=False, indent=2)

def update_faq_from_web():
    """Sincronizza le FAQ scaricandole dal link JustPaste configurato"""
    markdown = fetch_markdown_from_html(PASTE_URL)
    if markdown:
        faq = parse_faq(markdown)
        if faq:
            write_faq_json(faq, FAQ_FILE)
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

def load_json_file(filename, default=None):
    """Carica in sicurezza file JSON evitando crash se il file è corrotto o assente"""
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Errore lettura file JSON {filename}: {e}")
    return default if default is not None else {}

def save_json_file(filename, data):
    """Salva i dati in formato JSON indentato per facilitare la lettura umana"""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ============================================================================
# GESTIONE FAQ (rimane JSON - viene scaricato da web)
# ============================================================================

def load_faq():
    """Carica le FAQ dal database locale JSON"""
    return load_json_file(FAQ_FILE, default={"faq": []})

def get_bot_username():
    """Utility per ottenere lo username del bot per comporre link dinamici"""
    return getattr(get_bot_username, 'username', 'tuobot')

# ============================================================================
# LOGICHE DI RICERCA INTELLIGENTE
# ============================================================================

def calculate_similarity(text1: str, text2: str) -> float:
    """Calcola l'indice di somiglianza tra due stringhe (utilizzato per i refusi)"""
    return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

def normalize_text(text: str) -> str:
    """Rimuove simboli, punteggiatura e spazi eccessivi per facilitare il confronto"""
    text = re.sub(r'[^\w\s]', '', text)
    return re.sub(r'\s+', ' ', text).strip().lower()

def fuzzy_search_faq(user_message: str, faq_list: list) -> dict:
    """Cerca la risposta più pertinente nelle FAQ con score"""
    user_normalized = normalize_text(user_message)
    
    keywords_map = {
        "spedizione": ["spedito", "spedisci", "spedite", "corriere", "pacco", "invio", "mandato", "spedizioni", "arriva", "consegna"],
        "tracking": ["track", "codice", "tracciabilità", "tracciamento", "tracking", "traccia", "seguire", "dove"],
        "tempi": ["quando arriva", "quanto tempo", "giorni", "ricevo", "consegna", "tempistiche", "quanto ci vuole"],
        "pagamento": ["pagare", "metodi", "bonifico", "ricarica", "paypal", "crypto", "pagamenti", "come pago", "pagamento"],
        "ordinare": ["ordine", "ordinare", "fare ordine", "come ordino", "voglio ordinare", "fare un ordine", "posso ordinare", "come faccio", "procedura"]
    }

    for item in faq_list:
        domanda_norm = normalize_text(item["domanda"])
        risposta_norm = normalize_text(item["risposta"])
        
        for root, synonyms in keywords_map.items():
            if any(syn in user_normalized for syn in synonyms):
                if root in domanda_norm or root in risposta_norm:
                    logger.info(f"✅ FAQ Match (keyword): {root} → score: 1.0")
                    return {'match': True, 'item': item, 'score': 1.0, 'method': 'keyword'}

    best_match = None
    best_score = 0
    
    for item in faq_list:
        domanda_norm = normalize_text(item["domanda"])
        
        if user_normalized in domanda_norm or domanda_norm in user_normalized:
            logger.info(f"✅ FAQ Match (exact): score: 1.0")
            return {'match': True, 'item': item, 'score': 1.0, 'method': 'exact'}
        
        score = calculate_similarity(user_normalized, domanda_norm)
        if score > best_score:
            best_score = score
            best_match = item
    
    if best_score >= FAQ_CONFIDENCE_THRESHOLD:
        logger.info(f"✅ FAQ Match (fuzzy): score: {best_score:.2f}")
        return {'match': True, 'item': best_match, 'score': best_score, 'method': 'similarity'}
    
    logger.info(f"❌ FAQ: No match (best score: {best_score:.2f})")
    return {'match': False, 'item': None, 'score': best_score, 'method': None}

def fuzzy_search_lista(user_message: str, lista_text: str) -> dict:
    """
    Cerca prodotti nel listino con pattern ULTRA-SPECIFICI.
    Risponde SOLO a richieste esplicite di prodotti.
    """
    if not lista_text:
        return {'match': False, 'snippet': None, 'score': 0}
    
    text_lower = user_message.lower()
    user_normalized = normalize_text(user_message)
    
    # STEP 1: VERIFICA INTENT ESPLICITO
    explicit_request_patterns = [
        r'\bhai\s+(la|il|dello|della|l\'|un[ao]?)\s*\w{4,}',
        r'\bvendete\s+\w{4,}',
        r'\bavete\s+(la|il|dello|della|l\'|un[ao]?)\s*\w{4,}',
        r'\bquanto\s+costa\s+(la|il|dello|della|l\'|un[ao]?)\s*\w{4,}',
        r'\bprezzo\s+(di|del|della|dello)\s+\w{4,}',
        r'\bcosto\s+(di|del|della|dello)\s+\w{4,}',
        r'\bdisponibile\s+\w{4,}',
        r'\bdisponibilità\s+(di|del|della)\s+\w{4,}',
        r'\bin\s+stock\s+\w{4,}',
        r'\bce\s+(la|il|l\'|hai|avete)\s*\w{4,}',
        r'\bvorrei\s+(il|la|dello|della|un[ao]?)\s*\w{4,}',
        r'\bcerco\s+\w{4,}',
        r'\bmi\s+serve\s+(il|la|un[ao]?)\s*\w{4,}',
    ]
    
    has_explicit_intent = False
    for pattern in explicit_request_patterns:
        if re.search(pattern, text_lower):
            has_explicit_intent = True
            logger.info(f"✅ Pattern richiesta esplicita: {pattern[:30]}")
            break
    
    words = user_normalized.split()
    if len(words) == 1 and len(user_normalized) >= 4:  # Fix: >= 4 invece di > 5
        has_explicit_intent = True
        logger.info(f"✅ Query singola: '{user_normalized}'")
    
    if not has_explicit_intent:
        logger.info(f"❌ Nessun intent esplicito di ricerca prodotto")
        return {'match': False, 'snippet': None, 'score': 0}
    
    # STEP 2: ESTRAI NOME PRODOTTO
    stopwords = {
        'hai', 'avete', 'vendete', 'quanto', 'costa', 'prezzo', 'costo',
        'disponibile', 'disponibilità', 'stock', 'vorrei', 'cerco', 'serve',
        'per', 'sono', 'nel', 'con', 'che', 'questa', 'quello', 'tutte',
        'della', 'dello', 'delle', 'degli', 'alla', 'allo', 'alle', 'agli'
    }
    
    product_keywords = [
        w for w in words 
        if len(w) >= 4 and w not in stopwords
    ]
    
    if not product_keywords:
        logger.info(f"❌ Nessuna keyword prodotto trovata")
        return {'match': False, 'snippet': None, 'score': 0}
    
    logger.info(f"🔍 Cerco prodotti con keywords: {product_keywords}")
    
    # STEP 3: CERCA NEL LISTINO
    lines = lista_text.split('\n')
    matched_lines = []
    
    for line in lines:
        if not line.strip():
            continue
        
        if line.strip().startswith('_'):
            continue
        if line.strip().startswith('⬛') and line.strip().endswith('⬛'):
            continue
        if line.strip().startswith('🔘') and line.strip().endswith('🔘'):
            continue
        
        line_normalized = normalize_text(line)
        
        for keyword in product_keywords:
            if keyword in line_normalized:
                if ('💊' in line or '💉' in line or '€' in line):
                    matched_lines.append(line.strip())
                    logger.info(f"  ✅ Match: '{keyword}' in '{line[:50]}'")
                    break
    
    # STEP 4: RISULTATO
    if matched_lines:
        snippet = '\n'.join(matched_lines[:15])
        
        if len(snippet) > 3900:
            snippet = snippet[:3900] + "\n\n💡 (Scrivi il nome specifico per una ricerca più precisa)"
        
        score = len(matched_lines) / len(product_keywords) if product_keywords else 0
        
        logger.info(f"✅ Trovate {len(matched_lines)} righe prodotto")
        return {'match': True, 'snippet': snippet, 'score': min(score, 1.0)}
    
    logger.info(f"❌ Nessun prodotto trovato nel listino")
    return {'match': False, 'snippet': None, 'score': 0}

def has_payment_method(text: str) -> bool:
    """Verifica se il messaggio contiene un metodo di pagamento noto"""
    if not text:
        return False
    return any(kw in text.lower() for kw in PAYMENT_KEYWORDS)

# ============================================================================
# INTENT CLASSIFICATION
# ============================================================================

PAROLE_CHIAVE_LISTA = set()

def estrai_parole_chiave_lista():
    """Estrae keywords dalla lista per il classifier"""
    global PAROLE_CHIAVE_LISTA, intent_classifier
    
    testo = load_lista()
    if not testo:
        logger.warning("⚠️ Lista prodotti vuota")
        PAROLE_CHIAVE_LISTA = set()
    else:
        testo_norm = re.sub(r'[^\w\s]', ' ', testo.lower())
        parole = set(testo_norm.split())
        PAROLE_CHIAVE_LISTA = {p for p in parole if len(p) > 2}
    
    try:
        intent_classifier = IntentClassifier(
            lista_keywords=PAROLE_CHIAVE_LISTA,
            load_lista_func=load_lista
        )
        logger.info(f"✅ {len(PAROLE_CHIAVE_LISTA)} keywords estratte")
    except Exception as e:
        logger.error(f"❌ Errore creazione classifier: {e}")
        intent_classifier = IntentClassifier(
            lista_keywords=set(),
            load_lista_func=load_lista
        )
        logger.warning("⚠️ Classifier inizializzato vuoto")
    
    return PAROLE_CHIAVE_LISTA

def calcola_intenzione(text: str) -> str:
    """Classifica l'intento usando il classifier"""
    global intent_classifier
    
    if intent_classifier is None:
        logger.warning("⚠️ Classifier non inizializzato, inizializzo...")
        estrai_parole_chiave_lista()
    
    if intent_classifier is None:
        logger.error("❌ CRITICAL: Classifier ancora None!")
        return "fallback"
    
    try:
        result = intent_classifier.classify(text)
        
        logger.info(f"🎯 Intento: {result.intent.value} (conf: {result.confidence:.2f})")
        logger.info(f"💡 Ragione: {result.reason}")
        logger.info(f"🔑 Match: {result.matched_keywords}")
        
        intent_map = {
            IntentType.RICHIESTA_LISTA: "lista",
            IntentType.INVIO_ORDINE: "ordine",
            IntentType.DOMANDA_FAQ: "faq",
            IntentType.RICERCA_PRODOTTO: "ricerca_prodotti",
            IntentType.SALUTO: "fallback",
            IntentType.FALLBACK: "fallback",
        }
        
        return intent_map.get(result.intent, "fallback")
        
    except Exception as e:
        logger.error(f"❌ Errore classify: {e}")
        return "fallback"

# ============================================================================
# HANDLERS: COMANDI
# ============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user is None:
        return
        
    if context.args and context.args[0] == load_access_code():
        authorize_user(user.id, user.first_name, user.last_name, user.username)
        await update.message.reply_text("✅ Accesso autorizzato! Ora puoi interagire con il bot e visualizzare i prodotti.")
        if ADMIN_CHAT_ID:
            await context.bot.send_message(ADMIN_CHAT_ID, f"🆕 Utente autorizzato: {user.first_name} (@{user.username})")
        return

    if is_user_authorized(user.id):
        await update.message.reply_text(f"👋 Ciao {user.first_name}! Sono il tuo assistente. Scrivi 'lista' per vedere i prodotti o chiedimi informazioni su spedizioni e pagamenti. Usa i comandi /help, /lista")
    else:
        await update.message.reply_text("❌ Accesso negato. Devi utilizzare il link di invito ufficiale per abilitare il bot.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra l'intero regolamento e le FAQ caricate"""
    if not is_user_authorized(update.effective_user.id):
        return
        
    faq_data = load_faq()
    faq_list = faq_data.get("faq", [])
    
    if not faq_list:
        await update.message.reply_text("⚠️ Il regolamento non è ancora stato configurato.")
        return
        
    full_text = "🗒️ <b>REGOLAMENTO E INFORMAZIONI</b>\n\n"
    for item in faq_list:
        full_text += f"🔹 <b>{item['domanda']}</b>\n{item['risposta']}\n\n"
        
    if len(full_text) > 4000:
        for i in range(0, len(full_text), 4000):
            await update.message.reply_text(full_text[i:i+4000], parse_mode='HTML')
    else:
        await update.message.reply_text(full_text, parse_mode='HTML')

async def lista_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando manuale per visualizzare il listino prodotti"""
    if not is_user_authorized(update.effective_user.id):
        return
        
    update_lista_from_web()
    lista_text = load_lista()
    
    if not lista_text:
        await update.message.reply_text("❌ Listino non disponibile. Riprova più tardi.")
        return
        
    for i in range(0, len(lista_text), 4000):
        await update.message.reply_text(lista_text[i:i+4000])

# ============================================================================
# HANDLERS: AMMINISTRAZIONE
# ============================================================================

async def admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    msg = (
        "👑 <b>PANNELLO DI CONTROLLO ADMIN</b>\n\n"
        "<b>📝 Comandi Admin:</b>\n"
        "• /genera_link - Crea il link per autorizzare nuovi utenti\n"
        "• /cambia_codice - Rigenera il token di sicurezza\n"
        "• /lista_autorizzati - Vedi chi può usare il bot\n"
        "• /revoca ID - Rimuovi un utente dal database\n"
        "• /aggiorna_faq - Scarica le FAQ da JustPaste\n"
        "• /aggiorna_lista - Scarica il listino da JustPaste\n"
        "• /ordini - Visualizza ordini confermati oggi\n"
        "• /listtags - Vedi clienti registrati con tag\n"
        "• /removetag ID - Rimuovi tag cliente\n\n"
        "<b>👤 Comandi Utente:</b>\n"
        "• /start - Avvia il bot\n"
        "• /help - Visualizza FAQ e regolamento\n"
        "• /lista - Mostra il listino prodotti"
    )
    await update.message.reply_text(msg, parse_mode='HTML')

async def aggiorna_faq_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID: return
    if update_faq_from_web():
        await update.message.reply_text("✅ FAQ sincronizzate con successo.")
    else:
        await update.message.reply_text("❌ Errore durante l'aggiornamento FAQ.")

async def aggiorna_lista_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID: return
    if update_lista_from_web():
        await update.message.reply_text("✅ Listino prodotti aggiornato.")
    else:
        await update.message.reply_text("❌ Errore aggiornamento listino.")

async def genera_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID: return
    link = f"https://t.me/{get_bot_username.username}?start={load_access_code()}"
    await update.message.reply_text(
        f"🔗 <b>Link Autorizzazione:</b>\n<a href='{link}'>{link}</a>",
        parse_mode='HTML'
    )

async def cambia_codice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID: return
    new_code = secrets.token_urlsafe(12)
    save_access_code(new_code)
    link = f"https://t.me/{get_bot_username.username}?start={new_code}"
    await update.message.reply_text(f"✅ Nuovo codice generato:\n<code>{link}</code>", parse_mode='HTML')

async def lista_autorizzati_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID: return
    users = load_authorized_users()
    if not users:
        await update.message.reply_text("Nessun utente registrato.")
        return
    msg = "👥 <b>UTENTI ABILITATI:</b>\n\n"
    for uid, info in users.items():
        msg += f"- {info['name']} (@{info.get('username', 'N/A')}) [<code>{uid}</code>]\n"
    await update.message.reply_text(msg, parse_mode='HTML')

async def revoca_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID or not context.args: return
    users = load_authorized_users()
    target = context.args[0]
    if target in users:
        del users[target]
        save_authorized_users(users)
        await update.message.reply_text(f"✅ Utente {target} rimosso.")
    else:
        await update.message.reply_text("❌ ID non trovato.")

async def ordini_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra all'admin gli ordini confermati oggi"""
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    
    if update.effective_chat.type != "private":
        await update.message.reply_text("⚠️ Questo comando funziona solo in chat privata.")
        return

    ordini_oggi = get_ordini_oggi()
    
    if not ordini_oggi:
        await update.message.reply_text("📋 Nessun ordine confermato oggi.")
        return
    
    msg = f"📦 <b>ORDINI CONFERMATI OGGI ({len(ordini_oggi)})</b>\n\n"
    
    for i, ordine in enumerate(ordini_oggi, 1):
        user_name = ordine.get('user_name', 'N/A')
        username = ordine.get('username', 'N/A')
        user_id = ordine.get('user_id', 'N/A')
        ora = ordine.get('ora', 'N/A')
        message = ordine.get('message', 'N/A')
        chat_id = ordine.get('chat_id', 'N/A')
        msg += f"<b>{i}. {user_name}</b> (@{username})    🆔 ID: <code>{user_id}</code>\n"
        msg += f"   🕐 Ora: {ora}    💬 Chat: <code>{chat_id}</code>\n"
        msg += f"   📝 Messaggio:\n   <code>{message[:100]}...</code>\n\n"
    
    if len(msg) > 4000:
        for i in range(0, len(msg), 4000):
            await update.message.reply_text(msg[i:i+4000], parse_mode='HTML')
    else:
        await update.message.reply_text(msg, parse_mode='HTML')

async def list_tags_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra tutti i clienti registrati con tag - /listtags"""
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    
    tags = load_user_tags()
    
    if not tags:
        await update.message.reply_text("Nessun cliente registrato con tag")
        return
    
    msg = "📋 <b>CLIENTI REGISTRATI CON TAG</b>\n\n"
    for user_id, tag in tags.items():
        msg += f"• ID <code>{user_id}</code> → <b>{tag}</b>\n"
    
    if len(msg) > 4000:
        for i in range(0, len(msg), 4000):
            await update.message.reply_text(msg[i:i+4000], parse_mode='HTML')
    else:
        await update.message.reply_text(msg, parse_mode='HTML')

async def remove_tag_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rimuovi tag cliente - /removetag USER_ID"""
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    
    if not context.args:
        await update.message.reply_text("Uso: /removetag USER_ID")
        return
    
    user_id = context.args[0]
    if remove_user_tag(user_id):
        await update.message.reply_text(f"✅ Tag rimosso per user {user_id}")
    else:
        await update.message.reply_text(f"❌ User {user_id} non trovato")

# ============================================================================
# FLASK ROUTES
# ============================================================================

@app.route('/', methods=['GET'])
def home():
    """Homepage con status del bot"""
    global bot_application
    status = "✅ ATTIVO" if bot_application else "⏳ INIZIALIZZAZIONE"
    
    return f'''
    🤖 Bot Telegram Business - {status}
    
    Endpoint disponibili:
    - GET  /        → Status page
    - GET  /health  → Health check  
    - POST /webhook → Telegram webhook
    ''', 200

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint per Render"""
    global bot_application
    
    if bot_application:
        return 'OK - Bot active', 200
    else:
        return 'OK - Bot initializing', 200

@app.route('/webhook', methods=['POST'])
def webhook():
    """Endpoint webhook per ricevere update da Telegram"""
    global bot_application
    
    try:
        logger.info("=" * 60)
        logger.info("🔔 WEBHOOK RICEVUTO")
        logger.info("=" * 60)
        
        if not bot_application:
            logger.warning("⚠️ Bot non inizializzato al momento del webhook")
            return 'Bot not ready', 503
        
        json_data = request.get_json(force=True)
        
        if not json_data:
            logger.warning("⚠️ Webhook ricevuto senza dati")
            return 'No data', 400
        
        # Log tipo update
        if 'business_message' in json_data:
            msg = json_data['business_message']
            logger.info(f"💼 Business message")
            logger.info(f"   User: {msg.get('from', {}).get('id')} - Chat: {msg.get('chat', {}).get('id')}")
            logger.info(f"   Text: {msg.get('text', 'N/A')}")
        elif 'message' in json_data:
            msg = json_data['message']
            logger.info(f"💬 Private message")
            logger.info(f"   User: {msg.get('from', {}).get('id')}")
            logger.info(f"   Text: {msg.get('text', 'N/A')}")
        
        update = Update.de_json(json_data, bot_application.bot)
        
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        logger.info("⚙️ Processing update...")
        loop.run_until_complete(bot_application.process_update(update))
        logger.info("✅ Update processato")
        
        return 'ok', 200
        
    except Exception as e:
        logger.error(f"❌ Errore webhook: {e}", exc_info=True)
        return 'Error', 500

# ============================================================================
# HANDLER BUSINESS MESSAGES (CON SISTEMA /reg)
# ============================================================================

async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Gestisce messaggi Business con:
    - Rilevamento automatico admin
    - Sistema /reg per registrazione clienti
    - Whitelist basata su tag
    """
    from telegram import Message  # ← AGGIUNGI QUESTO IMPORT
    
    logger.info(f"🎯 TypeHandler chiamato")
    
    # Accesso diretto al dizionario raw
    update_dict = update.to_dict()
    
    if 'business_message' not in update_dict:
        return  # Non è Business message
    
    # Ricrea il Message object dal dizionario
    from telegram import Message
    message = Message.de_json(update_dict['business_message'], context.bot)
    
    logger.info("🔥 BUSINESS MESSAGE RILEVATO 🔥")
    
    # Estrai dati dal message
    business_connection_id = message.business_connection_id
    text = message.text.strip() if message.text else ""
    
    if not text:
        logger.info("⏭️ Messaggio vuoto, skip")
        return
    
    text_lower = text.lower()

    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # ========================================
    # IGNORA BOT
    # ========================================
    
    if message.from_user and message.from_user.is_bot:
        logger.info(f"🤖 Bot ignorato")
        return
    
    # ========================================
    # RILEVA ADMIN AUTOMATICAMENTE
    # ========================================
    
    # Se from_user.id != chat.id → Admin sta scrivendo al cliente
    if user_id != chat_id:
        logger.info(f"⏭️ Admin (user={user_id}) scrive a cliente (chat={chat_id})")
        
        # ECCEZIONE: Comando /reg
        if text_lower.startswith('/reg'):
            logger.info(f"✅ Comando /reg dall'admin - ESEGUO")
            
            parts = text.split()
            
            if len(parts) != 2:
                await context.bot.send_message(
                    business_connection_id=business_connection_id,
                    chat_id=chat_id,
                    text="❌ Formato: /reg TAG\nEsempio: /reg sp20"
                )
                return
            
            tag = parts[1].lower()
            
            if tag not in ALLOWED_TAGS:
                await context.bot.send_message(
                    business_connection_id=business_connection_id,
                    chat_id=chat_id,
                    text=f"❌ Tag non valido.\n\nTag disponibili:\n• {chr(10).join(ALLOWED_TAGS)}"
                )
                return
            
            # Registra il cliente (chat_id = ID del cliente)
            set_user_tag(chat_id, tag)
            
            await context.bot.send_message(
                business_connection_id=business_connection_id,
                chat_id=chat_id,
                text=f"✅ Cliente registrato con tag: <b>{tag}</b>",
                parse_mode='HTML'
            )
            
            logger.info(f"👨‍💼 Admin ha registrato cliente {chat_id} con tag {tag}")
            return
        
        # Ignora tutti gli altri messaggi dell'admin (inclusi automatici!)
        logger.info(f"⏭️ Messaggio admin ignorato")
        return
    
    # ========================================
    # MESSAGGIO DAL CLIENTE
    # ========================================
    
    logger.info(f"📱 Messaggio da cliente {user_id}: '{text}'")
    
    # ========================================
    # CHECK WHITELIST TAG
    # ========================================
    
    user_tag = get_user_tag(user_id)
    
    if not user_tag:
        logger.info(f"⛔ Cliente {user_id} non registrato - ignoro")
        return
    
    logger.info(f"✅ Cliente con tag: {user_tag}")
    
    # ========================================
    # HELPER INVIO RISPOSTE
    # ========================================
    
    async def send_business_reply(text_reply, parse_mode='HTML', reply_markup=None):
        try:
            await context.bot.send_message(
                business_connection_id=business_connection_id,
                chat_id=chat_id,
                text=text_reply,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
            logger.info(f"✅ Reply inviata")
        except Exception as e:
            logger.error(f"❌ Errore invio: {e}")
    
    # ========================================
    # CALCOLA INTENTO E RISPONDI
    # ========================================
    
    intent = calcola_intenzione(text)
    logger.info(f"🔄 Intent ricevuto: '{intent}'")
    
    # 1. LISTA
    if intent == "lista":
        logger.info(f"➡️ Entrato in blocco LISTA")
        await send_business_reply(
            "Ciao clicca qui per visualizzare il listino sempre aggiornato https://t.me/+uepM4qLBCrM0YTRk",
            parse_mode=None
        )
        return
    
    # 2. ORDINE
    if intent == "ordine":
        logger.info(f"➡️ Entrato in blocco ORDINE")
    
        # Salva l'ordine temporaneamente
        order_data = {
            'text': text,
            'user_id': user_id,
            'chat_id': chat_id,
            'message_id': message.message_id
        }
    
    # Usa callback_data per passare info
    callback_data = f"pay_ok_{user_id}_{message.message_id}"
    
    keyboard = [[
        InlineKeyboardButton("✅ Sì", callback_data=callback_data),
        InlineKeyboardButton("❌ No", callback_data=f"pay_no_{message.message_id}")
    ]]
    
    # Salva in context per recuperarlo dopo
    if not hasattr(context, 'bot_data'):
        context.bot_data = {}
    if 'pending_orders' not in context.bot_data:
        context.bot_data['pending_orders'] = {}
    
    context.bot_data['pending_orders'][callback_data] = order_data
    logger.info(f"💾 Ordine temporaneo salvato: {callback_data}")
    
    await send_business_reply(
        "🤔 <b>Sembra un ordine!</b>\nC'è il metodo di pagamento?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return
    
    # 3. FAQ
    if intent == "faq":
        logger.info(f"➡️ Entrato in blocco FAQ")
        faq_data = load_faq()
        res = fuzzy_search_faq(text, faq_data.get("faq", []))
        if res.get("match"):
            await send_business_reply(
                f"✅ <b>{res['item']['domanda']}</b>\n\n{res['item']['risposta']}"
            )
        return
    
    # 4. RICERCA PRODOTTI
    if intent == "ricerca_prodotti":
        logger.info(f"➡️ Entrato in blocco RICERCA")
        l_res = fuzzy_search_lista(text, load_lista())
        if l_res.get("match"):
            await send_business_reply(
                f"📦 <b>Nel listino ho trovato:</b>\n\n{l_res['snippet']}"
            )
            return
    
    # 5. FALLBACK
    trigger_words = [
        'ordine', 'lista', 'listino', 'prodotto', 'quanto costa',
        'spedizione', 'tracking', 'voglio', 'vorrei'
    ]
    
    if any(word in text_lower for word in trigger_words):
        await send_business_reply(
            "❓ Non ho capito. Usa /help per le FAQ o scrivi 'lista' per il catalogo."
        )

# ============================================================================
# HANDLER MESSAGGI PRIVATI
# ============================================================================

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    text = message.text.strip()
    intent = calcola_intenzione(text)
    
    # 1. LISTA
    if intent == "lista":
    await message.reply_text(
        "Ciao clicca qui per visualizzare il listino sempre aggiornato https://t.me/+uepM4qLBCrM0YTRk"
    )
    return

    # 2. ORDINE
    if intent == "ordine":
        keyboard = [[
            InlineKeyboardButton("✅ Sì", callback_data=f"pay_ok_{message.message_id}"),
            InlineKeyboardButton("❌ No", callback_data=f"pay_no_{message.message_id}")
        ]]
        await message.reply_text(
            "🤔 <b>Sembra un ordine!</b>\nC'è il metodo di pagamento?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    # 3. FAQ
    if intent == "faq":
        faq_data = load_faq()
        res = fuzzy_search_faq(text, faq_data.get("faq", []))
        if res.get("match"):
            await message.reply_text(
                f"✅ <b>{res['item']['domanda']}</b>\n\n{res['item']['risposta']}",
                parse_mode="HTML"
            )
            return

    # 4. RICERCA PRODOTTI
    if intent == "ricerca_prodotti":
        l_res = fuzzy_search_lista(text, load_lista())
        if l_res.get("match"):
            await message.reply_text(
                f"📦 <b>Nel listino ho trovato:</b>\n\n{l_res['snippet']}",
                parse_mode="HTML"
            )
            return

    # 5. FALLBACK
    await message.reply_text("❓ Non ho capito. Scrivi 'lista' o usa /help.")

# ============================================================================
# HANDLER MESSAGGI GRUPPI
# ============================================================================

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message or update.channel_post
    if not message or not message.text:
        return
    if message.from_user and message.from_user.is_bot:
        return

    text = message.text.strip()
    intent = calcola_intenzione(text)
    chat_id = message.chat.id

    # 1. LISTA
    if intent == "lista":
    await context.bot.send_message(
        chat_id=message.chat.id,
        text="Ciao clicca qui per visualizzare il listino sempre aggiornato https://t.me/+uepM4qLBCrM0YTRk",
        reply_to_message_id=message.message_id
    )
    return

    # 2. ORDINE
    if intent == "ordine":
        keyboard = [[
            InlineKeyboardButton("✅ Sì", callback_data=f"pay_ok_{message.message_id}"),
            InlineKeyboardButton("❌ No", callback_data=f"pay_no_{message.message_id}")
        ]]
        await context.bot.send_message(
            chat_id=message.chat.id,
            text="🤔 <b>Sembra un ordine!</b>\nC'è il metodo di pagamento?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
            reply_to_message_id=message.message_id
        )
        return

    # 3. FAQ
    if intent == "faq":
        faq_data = load_faq()
        res = fuzzy_search_faq(text, faq_data.get("faq", []))
        if res.get("match"):
            await context.bot.send_message(
                chat_id=message.chat.id,
                text=f"✅ <b>{res['item']['domanda']}</b>\n\n{res['item']['risposta']}",
                parse_mode="HTML",
                reply_to_message_id=message.message_id
            )
        return

    # 4. RICERCA PRODOTTI
    if intent == "ricerca_prodotti":
        l_res = fuzzy_search_lista(text, load_lista())
        if l_res.get("match"):
            await context.bot.send_message(
                chat_id=message.chat.id,
                text=f"📦 <b>Nel listino ho trovato:</b>\n\n{l_res['snippet']}",
                parse_mode="HTML",
                reply_to_message_id=message.message_id
            )
            return
    
    # 5. FALLBACK
    trigger_words = [
        'ordine', 'lista', 'listino', 'prodotto', 'quanto costa',
        'spedizione', 'tracking', 'voglio', 'vorrei'
    ]
    
    if any(word in text.lower() for word in trigger_words):
        await context.bot.send_message(
            chat_id=message.chat.id,
            text="❓ Non ho capito. Usa /lista o /help.",
            reply_to_message_id=message.message_id
        )

# ============================================================================
# HANDLER CALLBACK QUERY (BOTTONI)
# ============================================================================

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestisce i bottoni Inline e salva gli ordini confermati"""
    query = update.callback_query
    await query.answer()
    
    logger.info(f"🔘 Callback ricevuto: {query.data}")
    
    if query.data.startswith("pay_ok_"):
        logger.info("✅ Bottone 'Sì' premuto")
        
        # Recupera ordine salvato
        if not hasattr(context, 'bot_data'):
            context.bot_data = {}
        
        pending_orders = context.bot_data.get('pending_orders', {})
        order_data = pending_orders.get(query.data)
        
        if not order_data:
            logger.warning("⚠️ Ordine non trovato in memoria")
            await query.edit_message_text("✅ Ordine confermato!")
            return
        
        user = query.from_user
        
        add_ordine_confermato(
            user_id=order_data['user_id'],
            user_name=user.first_name or "Sconosciuto",
            username=user.username or "nessuno",
            message_text=order_data['text'],
            chat_id=order_data['chat_id'],
            message_id=order_data['message_id']
        )
        
        logger.info(f"💾 Ordine salvato per user {order_data['user_id']}")
        
        # Rimuovi dalla memoria
        del pending_orders[query.data]
        
        await query.edit_message_text(f"✅ Ordine confermato da {user.first_name}! Procederò appena possibile.")
        
        if ADMIN_CHAT_ID:
            try:
                notifica = (
                    f"📩 <b>NUOVO ORDINE CONFERMATO</b>\n\n"
                    f"👤 Utente: {user.first_name} (@{user.username})\n"
                    f"🆔 ID: <code>{order_data['user_id']}</code>\n"
                    f"💬 Chat: <code>{order_data['chat_id']}</code>\n"
                    f"📝 Messaggio:\n<code>{order_data['text'][:200]}</code>"
                )
                await context.bot.send_message(ADMIN_CHAT_ID, notifica, parse_mode='HTML')
                logger.info("📧 Notifica admin inviata")
            except Exception as e:
                logger.error(f"❌ Errore notifica admin: {e}")
            
    elif query.data.startswith("pay_no_"):
        logger.info("❌ Bottone 'No' premuto")
        await query.edit_message_text("💡 Per favore, indica il metodo (Bonifico, Crypto).")

# ============================================================================
# HANDLER STATUS UPDATES
# ============================================================================

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
        except Exception as e:
            logger.error(f"Errore benvenuto: {e}")

async def handle_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass

# ============================================================================
# SETUP BOT
# ============================================================================

async def setup_bot():
    global bot_application, initialization_lock, PAROLE_CHIAVE_LISTA, intent_classifier
    
    if initialization_lock:
        return None
    
    initialization_lock = True
    
    try:
        logger.info("🔡 Inizializzazione bot...")
        
        # ========================================
        # INIZIALIZZA DATABASE POSTGRESQL
        # ========================================
        logger.info("🗄️ Inizializzazione database...")
        if db.init_db():
            logger.info("✅ Database PostgreSQL pronto")
        else:
            logger.error("❌ Errore inizializzazione database!")
            raise RuntimeError("Database init failed")
        
        # Inizializza classifier
        try:
            # Prova aggiornamento da web
            faq_data = load_faq()
            if not faq_data.get("faq"):
                logger.warning("⚠️ FAQ vuote, scarico da web")
                update_faq_from_web()
            
            logger.info("📥 Download lista...")
            update_lista_from_web()
            
            # Crea classifier
            PAROLE_CHIAVE_LISTA = estrai_parole_chiave_lista()
            
        except Exception as e:
            logger.error(f"❌ Errore init: {e}")
            intent_classifier = IntentClassifier(
                lista_keywords=set(),
                load_lista_func=load_lista
            )
        
        application = Application.builder().token(BOT_TOKEN).updater(None).build()
        bot = await application.bot.get_me()
        get_bot_username.username = bot.username
        logger.info(f"Bot: @{bot.username}")
        
        # ========================================
        # REGISTRAZIONE HANDLER
        # ========================================

        # BUSINESS MESSAGES
        from telegram.ext import BaseHandler
        class BusinessMessageHandler(BaseHandler):
            """Handler custom per Business Messages"""
            def __init__(self, callback):
                super().__init__(callback)
                self.callback = callback
    
            def check_update(self, update):
                """Verifica se è un business message"""
                if not update:
                    return False        
                # Escludi callback_query
                if hasattr(update, 'callback_query') and update.callback_query:
                    return False        
                # Verifica business_message
                update_dict = update.to_dict()
                return 'business_message' in update_dict
        # Registrazione
        application.add_handler(BusinessMessageHandler(handle_business_message), group=0)
        logger.info("✅ Handler Business Messages registrato (priority group=0)")

        # 1. COMANDI
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
        application.add_handler(CommandHandler("ordini", ordini_command))
        application.add_handler(CommandHandler("listtags", list_tags_command))
        application.add_handler(CommandHandler("removetag", remove_tag_command))
        
        # 2. STATUS UPDATES
        application.add_handler(MessageHandler(
            filters.StatusUpdate.NEW_CHAT_MEMBERS, 
            handle_user_status
        ))
        application.add_handler(ChatMemberHandler(
            handle_chat_member_update, 
            ChatMemberHandler.CHAT_MEMBER
        ))
        
        # 3. CALLBACK QUERY
        application.add_handler(CallbackQueryHandler(handle_callback_query))
        
        # 5. MESSAGGI GRUPPI
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND & (
                filters.ChatType.GROUP | 
                filters.ChatType.SUPERGROUP | 
                filters.ChatType.CHANNEL
            ),
            handle_group_message
        )) 

        # 6. MESSAGGI PRIVATI
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            handle_private_message
        ))

        # ========================================
        # WEBHOOK CONFIGURATION
        # ========================================
        if WEBHOOK_URL:
            await application.bot.set_webhook(
                url=f"{WEBHOOK_URL}/webhook",
                allowed_updates=[
                    "message",
                    "edited_message", 
                    "channel_post",
                    "edited_channel_post",
                    "callback_query",
                    "chat_member",
                    "my_chat_member",
                    "business_connection",
                    "business_message",
                    "edited_business_message"
                ]
            )
            logger.info(f"✅ Webhook: {WEBHOOK_URL}/webhook")

        await application.initialize()
        await application.start()
        logger.info("🤖 Bot pronto!")
        
        return application
        
    except Exception as e:
        logger.error(f"❌ Setup error: {e}")
        initialization_lock = False
        raise

# End main.py
