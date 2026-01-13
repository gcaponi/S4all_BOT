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
from intent_classifier import IntentClassifier, IntentType

# ============================================================
# CONFIGURAZIONE LOGGING (DETTAGLIATO)
# ============================================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================================
# VARIABILI DI AMBIENTE E COSTANTI
# ============================================================
BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_CHAT_ID = int(os.environ.get('ADMIN_CHAT_ID', 0))
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '')
PORT = int(os.environ.get('PORT', 10000))
intent_classifier = None

# Nomi dei file per la persistenza dei dati
AUTHORIZED_USERS_FILE = 'authorized_users.json'
ACCESS_CODE_FILE = 'access_code.json'
FAQ_FILE = 'faq.json'
LISTA_FILE = "lista.txt"
ORDINI_FILE = "ordini_confermati.json"

# Link JustPaste.it per aggiornamento dinamico
LISTA_URL = "https://justpaste.it/lista_4all"
PASTE_URL = "https://justpaste.it/faq_4all"

# Soglia di precisione per la ricerca approssimativa (Fuzzy)
FUZZY_THRESHOLD = 0.6

# Parole chiave per intercettare i metodi di pagamento negli ordini
PAYMENT_KEYWORDS = [
    "bonifico", "usdt", "crypto", "cripto", "bitcoin", "bit", "btc", "eth", "usdc", "xmr"
]

# Inizializzazione Flask e variabili globali Bot
app = Flask(__name__)
bot_application = None
bot_initialized = False
initialization_lock = False
FAQ_CONFIDENCE_THRESHOLD = 0.65
LISTA_CONFIDENCE_THRESHOLD = 0.30

# ============================================================
# FILTRO CUSTOM PER BUSINESS MESSAGES
# ============================================================
class BusinessMessageFilter(filters.MessageFilter):
    """
    Filtro custom per identificare messaggi Telegram Business.
    Controlla se il messaggio ha business_connection_id.
    """
    def filter(self, message):
        return (
            hasattr(message, 'business_connection_id') and 
            message.business_connection_id is not None
        )

# Istanza del filtro
business_filter = BusinessMessageFilter()

# ============================================================
# UTILS: WEB FETCH, PARSING, I/O (SISTEMA DI AGGIORNAMENTO)
# ============================================================
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
    
# Variabile globale per parole chiave dinamiche estratte da lista.txt
PAROLE_CHIAVE_LISTA = set()

def estrai_parole_chiave_lista():
    global PAROLE_CHIAVE_LISTA, intent_classifier
    
    testo = load_lista()
    if not testo:
        logger.warning("⚠️ Lista prodotti vuota, creo classifier con set vuoto")
        parole_filtrate = set()
    else:
        testo_norm = re.sub(r'[^\w\s]', ' ', testo.lower())
        parole = set(testo_norm.split())
        parole_filtrate = {p for p in parole if len(p) > 2}
    
    # IMPORTANTE: Crea il classifier QUI con fallback sicuro
    try:
        intent_classifier = IntentClassifier(
            lista_keywords=parole_filtrate,
            load_lista_func=load_lista
        )
        logger.info(f"✅ Classifier creato con {len(parole_filtrate)} keywords")
    except Exception as e:
        logger.error(f"❌ Errore creazione classifier: {e}")
        intent_classifier = IntentClassifier(
            lista_keywords=set(),
            load_lista_func=load_lista
        )
        logger.warning("⚠️ Classifier inizializzato in modalità fallback (vuoto)")
    
    return parole_filtrate

def calcola_intenzione(text: str) -> str:
    """
    NUOVA VERSIONE: Usa il classificatore intelligente
    """
    global intent_classifier
    
    # SAFETY CHECK: Se il classifier non è inizializzato, inizializzalo
    if intent_classifier is None:
        logger.warning("⚠️ Intent classifier non inizializzato, inizializzo ora...")
        try:
            estrai_parole_chiave_lista()
            logger.info("✅ Classifier inizializzato in emergenza")
        except Exception as e:
            logger.error(f"❌ Errore inizializzazione classifier: {e}")
            return "fallback"
    
    # Doppio check
    if intent_classifier is None:
        logger.error("❌ CRITICAL: Classifier ancora None!")
        return "fallback"
    
    # Classifica il messaggio
    try:
        result = intent_classifier.classify(text)
    except Exception as e:
        logger.error(f"❌ Errore durante classify: {e}")
        return "fallback"
    
    # Log per debugging
    logger.info(f"🎯 Intento: {result.intent.value} (conf: {result.confidence:.2f})")
    logger.info(f"💡 Ragione: {result.reason}")
    logger.info(f"🔑 Match: {result.matched_keywords}")
    
    # Mappa IntentType ai tuoi valori attuali
    intent_map = {
        IntentType.RICHIESTA_LISTA: "lista",
        IntentType.INVIO_ORDINE: "ordine",
        IntentType.DOMANDA_FAQ: "faq",
        # IntentType.RICERCA_PRODOTTO: "ricerca_prodotti",  # DISABILITATO
        IntentType.SALUTO: "fallback",
        IntentType.FALLBACK: "fallback",
    }
    
    return intent_map.get(result.intent, "fallback")
    
def load_lista():
    """Carica il contenuto testuale del listino dal file locale"""
    if os.path.exists(LISTA_FILE):
        with open(LISTA_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return ""

def load_json_file(filename, default=None):
    """Carica in sicurezza file JSON evitando crash se il file Ã¨ corrotto o assente"""
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

# ============================================================
# GESTIONE AUTORIZZAZIONI E UTENTI
# ============================================================
def load_authorized_users():
    """Carica il database degli utenti che hanno usato il link segreto"""
    data = load_json_file(AUTHORIZED_USERS_FILE, default={})
    if isinstance(data, list):
        return {str(uid): {"id": uid, "name": "Utente", "username": None} for uid in data}
    return data

def save_authorized_users(users):
    """Salva il database aggiornato degli utenti autorizzati"""
    save_json_file(AUTHORIZED_USERS_FILE, users)

def load_access_code():
    """Recupera il codice segreto o ne crea uno nuovo al primo avvio"""
    data = load_json_file(ACCESS_CODE_FILE, default={})
    if not data.get('code'):
        code = secrets.token_urlsafe(12)
        save_json_file(ACCESS_CODE_FILE, {'code': code})
        return code
    return data['code']

def save_access_code(code):
    """Aggiorna manualmente il codice di accesso"""
    save_json_file(ACCESS_CODE_FILE, {'code': code})

def load_faq():
    """Carica le FAQ dal database locale JSON"""
    return load_json_file(FAQ_FILE, default={"faq": []})

def is_user_authorized(user_id):
    """Verifica se l'ID Telegram Ã¨ presente tra gli autorizzati"""
    return str(user_id) in load_authorized_users()

def authorize_user(user_id, first_name=None, last_name=None, username=None):
    """Registra un nuovo utente nel database degli autorizzati"""
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
    """Utility per ottenere lo username del bot per comporre link dinamici"""
    return getattr(get_bot_username, 'username', 'tuobot')

# ============================================================
# GESTIONE ORDINI CONFERMATI
# ============================================================
def load_ordini():
    """Carica il database degli ordini confermati"""
    return load_json_file(ORDINI_FILE, default=[])

def save_ordini(ordini):
    """Salva il database degli ordini confermati"""
    save_json_file(ORDINI_FILE, ordini)

def add_ordine_confermato(user_id, user_name, username, message_text, chat_id, message_id):
    """Registra un ordine confermato nel database"""
    ordini = load_ordini()
    
    ordine = {
        "user_id": user_id,
        "user_name": user_name,
        "username": username,
        "message": message_text,
        "chat_id": chat_id,
        "message_id": message_id,
        "timestamp": datetime.now().isoformat(),
        "data": datetime.now().strftime("%Y-%m-%d"),
        "ora": datetime.now().strftime("%H:%M:%S")
    }
    
    ordini.append(ordine)
    save_ordini(ordini)
    logger.info(f"Ordine confermato salvato: {user_name} ({user_id})")

def get_ordini_oggi():
    """Recupera tutti gli ordini confermati di oggi"""
    ordini = load_ordini()
    oggi = datetime.now().strftime("%Y-%m-%d")
    return [o for o in ordini if o.get("data") == oggi]

# ============================================================
# LOGICHE DI RICERCA INTELLIGENTE (CORE)
# ============================================================
def calculate_similarity(text1: str, text2: str) -> float:
    """Calcola l'indice di somiglianza tra due stringhe (utilizzato per i refusi)"""
    return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

def normalize_text(text: str) -> str:
    """Rimuove simboli, punteggiatura e spazi eccessivi per facilitare il confronto"""
    text = re.sub(r'[^\w\s]', '', text)
    return re.sub(r'\s+', ' ', text).strip().lower()

def fuzzy_search_faq(user_message: str, faq_list: list) -> dict:
    """Cerca la risposta piÃ¹ pertinente nelle FAQ con score"""
    user_normalized = normalize_text(user_message)
    
    # Sistema sinonimi esteso
    keywords_map = {
        "spedizione": ["spedito", "spedisci", "spedite", "corriere", "pacco", "invio", "mandato", "spedizioni", "arriva", "consegna"],
        "tracking": ["track", "codice", "tracciabilitÃ ", "tracciamento", "tracking", "traccia", "seguire", "dove"],
        "tempi": ["quando arriva", "quanto tempo", "giorni", "ricevo", "consegna", "tempistiche", "quanto ci vuole"],
        "pagamento": ["pagare", "metodi", "bonifico", "ricarica", "paypal", "crypto", "pagamenti", "come pago", "pagamento"],
        "ordinare": ["ordine", "ordinare", "fare ordine", "come ordino", "voglio ordinare", "fare un ordine", "posso ordinare", "come faccio", "procedura"]
    }

    # PRIORITÃ€ ALTA: Match con keywords (score massimo)
    for item in faq_list:
        domanda_norm = normalize_text(item["domanda"])
        risposta_norm = normalize_text(item["risposta"])
        
        for root, synonyms in keywords_map.items():
            if any(syn in user_normalized for syn in synonyms):
                if root in domanda_norm or root in risposta_norm:
                    logger.info(f"âœ… FAQ Match (keyword): {root} â†’ score: 1.0")
                    return {'match': True, 'item': item, 'score': 1.0, 'method': 'keyword'}

    # PRIORITÃ€ MEDIA: Match per similaritÃ 
    best_match = None
    best_score = 0
    
    for item in faq_list:
        domanda_norm = normalize_text(item["domanda"])
        
        # Match perfetto
        if user_normalized in domanda_norm or domanda_norm in user_normalized:
            logger.info(f"âœ… FAQ Match (exact): score: 1.0")
            return {'match': True, 'item': item, 'score': 1.0, 'method': 'exact'}
        
        # Calcolo similaritÃ 
        score = calculate_similarity(user_normalized, domanda_norm)
        if score > best_score:
            best_score = score
            best_match = item
    
    # Se supera la soglia
    if best_score >= FAQ_CONFIDENCE_THRESHOLD:
        logger.info(f"âœ… FAQ Match (fuzzy): score: {best_score:.2f}")
        return {'match': True, 'item': best_match, 'score': best_score, 'method': 'similarity'}
    
    logger.info(f"âŒ FAQ: No match (best score: {best_score:.2f})")
    return {'match': False, 'item': None, 'score': best_score, 'method': None}

def fuzzy_search_lista(user_message: str, lista_text: str) -> dict:
    """Cerca prodotti nella lista con filtro semantico + score"""
    if not lista_text:
        return {'match': False, 'snippet': None, 'score': 0}
    
    user_normalized = normalize_text(user_message)
    
    # Estrai parole significative (>3 caratteri)
    words = [w for w in user_normalized.split() if len(w) > 3]
    
    if not words:
        return {'match': False, 'snippet': None, 'score': 0}
    
    # FILTRO SEMANTICO: Blocca conversazionale SOLO se non ci sono prodotti
    conversational_stopwords = [
        'come', 'volevo', 'vorrei', 'voglio', 'posso', 'devo', 'come faccio',
        'buongiorno', 'buonasera', 'ciao', 'salve', 'informazioni', 'aiuto',
        'fare', 'faccio', 'sapere', 'chiedere', 'domanda', 'spiegare'
    ]
    
    has_conversational = any(stopword in user_normalized for stopword in conversational_stopwords)
    
    # Cerca nelle righe
    lines = lista_text.split('\n')
    best_lines = []
    matches_count = 0
    
    for line in lines:
        if not line.strip():
            continue
        line_normalized = normalize_text(line)
        
        # Conta quante parole matchano (ANCHE UNA SOLA BASTA!)
        matched_words = 0
        for w in words:
            if w in line_normalized:
                matched_words += 1
        
        # Se ha almeno UNA parola in comune, includila
        if matched_words > 0:
            best_lines.append(line.strip())
            matches_count += matched_words
    
    # Se ha trovato prodotti, IGNORA il filtro conversazionale
    if best_lines:
        score = matches_count / len(words) if words else 0
        
        # RIMUOVI IL LIMITE DI 5 RIGHE - mostra tutto
        snippet = '\n'.join(best_lines)
        
        # Se il risultato Ã¨ troppo lungo (>4000 caratteri), tronca con messaggio
        if len(snippet) > 3900:
            snippet = snippet[:3900] + "\n\n... (altri prodotti disponibili, scrivi una ricerca piÃ¹ specifica)"
        
        logger.info(f"âœ… Lista: {len(best_lines)} righe trovate, score: {score:.2f}")
        return {'match': True, 'snippet': snippet, 'score': score}
    
    # Se NON ha trovato prodotti E ha parole conversazionali
    if has_conversational and not best_lines:
        logger.info(f"â­ Lista: Blocked (conversational + no products)")
        return {'match': False, 'snippet': None, 'score': 0}
    
    logger.info(f"âŒ Lista: No match")
    return {'match': False, 'snippet': None, 'score': 0}
    
    # Se ha trovato prodotti nella lista, IGNORA il filtro conversazionale
    if best_lines:
        score = matches_count / len(words) if words else 0
        logger.info(f"âœ… Lista: {len(best_lines)} righe, score: {score:.2f}")
        return {'match': True, 'snippet': '\n'.join(best_lines[:5]), 'score': score}
    
    # Se NON ha trovato prodotti E ha parole conversazionali, probabilmente Ã¨ una domanda generica
    if has_conversational and not best_lines:
        logger.info(f"â­ï¸ Lista: Blocked (conversational + no products)")
        return {'match': False, 'snippet': None, 'score': 0}
    
    logger.info(f"âŒ Lista: No match")
    return {'match': False, 'snippet': None, 'score': 0}

def has_payment_method(text: str) -> bool:
    """Verifica se il messaggio contiene un metodo di pagamento noto"""
    if not text:
        return False
    return any(kw in text.lower() for kw in PAYMENT_KEYWORDS)

# ============================================================
# HANDLERS: COMANDI (START, HELP, LISTA)
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user is None:
        return
        
    if context.args and context.args[0] == load_access_code():
        authorize_user(user.id, user.first_name, user.last_name, user.username)
        await update.message.reply_text("âœ… Accesso autorizzato! Ora puoi interagire con il bot e visualizzare i prodotti.")
        if ADMIN_CHAT_ID:
            await context.bot.send_message(ADMIN_CHAT_ID, f"ðŸ†• Utente autorizzato: {user.first_name} (@{user.username})")
        return

    if is_user_authorized(user.id):
        await update.message.reply_text(f"ðŸ‘‹ Ciao {user.first_name}! Sono il tuo assistente. Scrivi 'lista' per vedere i prodotti o chiedimi informazioni su spedizioni e pagamenti. Usa i comandi /help, /lista")
    else:
        await update.message.reply_text("âŒ Accesso negato. Devi utilizzare il link di invito ufficiale per abilitare il bot.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra l'intero regolamento e le FAQ caricate"""
    if not is_user_authorized(update.effective_user.id):
        return
        
    faq_data = load_faq()
    faq_list = faq_data.get("faq", [])
    
    if not faq_list:
        await update.message.reply_text("âš ï¸ Il regolamento non Ã¨ ancora stato configurato.")
        return
        
    full_text = "ðŸ—’ï¸ <b>REGOLAMENTO E INFORMAZIONI</b>\n\n"
    for item in faq_list:
        full_text += f"ðŸ”¹ <b>{item['domanda']}</b>\n{item['risposta']}\n\n"
        
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
        await update.message.reply_text("âŒ Listino non disponibile. Riprova piÃ¹ tardi.")
        return
        
    for i in range(0, len(lista_text), 4000):
        await update.message.reply_text(lista_text[i:i+4000])

# ============================================================
# HANDLERS: AMMINISTRAZIONE (SOLO ADMIN_CHAT_ID)
# ============================================================

async def admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    msg = (
        "ðŸ‘‘ <b>PANNELLO DI CONTROLLO ADMIN</b>\n\n"
        "<b>ðŸ” Comandi Admin:</b>\n"
        "â€¢ /genera_link - Crea il link per autorizzare nuovi utenti\n"
        "â€¢ /cambia_codice - Rigenera il token di sicurezza\n"
        "â€¢ /lista_autorizzati - Vedi chi puÃ² usare il bot\n"
        "â€¢ /revoca ID - Rimuovi un utente dal database\n"
        "â€¢ /aggiorna_faq - Scarica le FAQ da JustPaste\n"
        "â€¢ /aggiorna_lista - Scarica il listino da JustPaste\n"
        "â€¢ /ordini - Visualizza ordini confermati oggi\n\n"
        "<b>ðŸ‘¤ Comandi Utente:</b>\n"
        "â€¢ /start - Avvia il bot\n"
        "â€¢ /help - Visualizza FAQ e regolamento\n"
        "â€¢ /lista - Mostra il listino prodotti"
    )
    await update.message.reply_text(msg, parse_mode='HTML')

async def aggiorna_faq_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID: return
    if update_faq_from_web():
        await update.message.reply_text("âœ… FAQ sincronizzate con successo.")
    else:
        await update.message.reply_text("âŒ Errore durante l'aggiornamento FAQ.")

async def aggiorna_lista_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID: return
    if update_lista_from_web():
        await update.message.reply_text("âœ… Listino prodotti aggiornato.")
    else:
        await update.message.reply_text("âŒ Errore aggiornamento listino.")

async def genera_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID: return
    link = f"https://t.me/{get_bot_username.username}?start={load_access_code()}"
    await update.message.reply_text(f"ðŸ”— <b>Link Autorizzazione:</b>\n<code>{link}</code>", parse_mode='HTML')

async def cambia_codice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID: return
    new_code = secrets.token_urlsafe(12)
    save_access_code(new_code)
    link = f"https://t.me/{get_bot_username.username}?start={new_code}"
    await update.message.reply_text(f"âœ… Nuovo codice generato:\n<code>{link}</code>", parse_mode='HTML')

async def lista_autorizzati_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID: return
    users = load_authorized_users()
    if not users:
        await update.message.reply_text("Nessun utente registrato.")
        return
    msg = "ðŸ‘¥ <b>UTENTI ABILITATI:</b>\n\n"
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
        await update.message.reply_text(f"âœ… Utente {target} rimosso.")
    else:
        await update.message.reply_text("âŒ ID non trovato.")

async def ordini_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra all'admin gli ordini confermati oggi (Solo Admin in Privata)"""
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    
    if update.effective_chat.type != "private":
        await update.message.reply_text("âš ï¸ Questo comando funziona solo in chat privata.")
        return

    ordini_oggi = get_ordini_oggi()
    
    if not ordini_oggi:
        await update.message.reply_text("ðŸ“‹ Nessun ordine confermato oggi.")
        return
    
    msg = f"ðŸ“¦ <b>ORDINI CONFERMATI OGGI ({len(ordini_oggi)})</b>\n\n"
    
    for i, ordine in enumerate(ordini_oggi, 1):
        user_name = ordine.get('user_name', 'N/A')
        username = ordine.get('username', 'N/A')
        user_id = ordine.get('user_id', 'N/A')
        ora = ordine.get('ora', 'N/A')
        message = ordine.get('message', 'N/A')
        chat_id = ordine.get('chat_id', 'N/A')
        msg += f"<b>{i}. {user_name}</b> (@{username})    ðŸ†” ID: <code>{user_id}</code>\n"
        msg += f"   ðŸ• Ora: {ora}    ðŸ’¬ Chat: <code>{chat_id}</code>\n"
        msg += f"   ðŸ“ Messaggio:\n   <code>{message[:100]}...</code>\n\n"
    
    if len(msg) > 4000:
        for i in range(0, len(msg), 4000):
            await update.message.reply_text(msg[i:i+4000], parse_mode='HTML')
    else:
        await update.message.reply_text(msg, parse_mode='HTML')

# ============================================================
# GESTIONE MESSAGGI: LOGICA UNIFICATA (PRIVATI E GRUPPI)
# ============================================================
async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ Gestisce messaggi ricevuti tramite Telegram Business.
    Questo handler viene chiamato SOLO per messaggi Business grazie al filtro custom.
    I messaggi normali vanno in handle_group_message o handle_private_message. """
    message = update.message or update.edited_message
    
    if not message or not message.text:
        return
    
    business_connection_id = message.business_connection_id
    
    logger.info(f"ðŸ“± Business message ricevuto")
    logger.info(f"   Connection ID: {business_connection_id}")
    logger.info(f"   Chat ID: {message.chat.id}")
    logger.info(f"   Testo: {message.text[:50]}...")
    
    text = message.text.strip()
    intent = calcola_intenzione(text)
    
    # ========================================
    # Helper: Invia risposta Business
    # ========================================
    async def send_business_reply(text_reply, parse_mode='HTML', reply_markup=None):
        """
        Invia messaggio usando business_connection_id.
        Questo Ã¨ OBBLIGATORIO per rispondere in Business.
        """
        try:
            await context.bot.send_message(
                business_connection_id=business_connection_id,
                chat_id=message.chat.id,
                text=text_reply,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
            logger.info(f"âœ… Risposta Business inviata")
        except Exception as e:
            logger.error(f"âŒ Errore invio Business: {e}")
    
    # 1. RICHIESTA LISTA
    if intent == "lista":
        lista = load_lista()
        if lista:
            # Dividi in chunk da 4000 caratteri (limite Telegram)
            for i in range(0, len(lista), 4000):
                await send_business_reply(lista[i:i+4000], parse_mode=None)
        return
    
    # 2. ORDINE
    if intent == "ordine":
        keyboard = [[
            InlineKeyboardButton("âœ… SÃ¬", callback_data=f"pay_ok_{message.message_id}"),
            InlineKeyboardButton("âŒ No", callback_data=f"pay_no_{message.message_id}")
        ]]
        await send_business_reply(
            "ðŸ¤” <b>Sembra un ordine!</b>\nC'Ã¨ il metodo di pagamento?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # 3. FAQ
    if intent == "faq":
        faq_data = load_faq()
        res = fuzzy_search_faq(text, faq_data.get("faq", []))
        if res.get("match"):
            await send_business_reply(
                f"âœ… <b>{res['item']['domanda']}</b>\n\n{res['item']['risposta']}"
            )
        return
    
    # 4. RICERCA PRODOTTI
    if intent == "ricerca_prodotti":
        l_res = fuzzy_search_lista(text, load_lista())
        if l_res.get("match"):
            await send_business_reply(
                f"ðŸ“¦ <b>Nel listino ho trovato:</b>\n\n{l_res['snippet']}"
            )
            return
    
    # 5. FALLBACK
    trigger_words = [
        'ordine', 'ordinare', 'lista', 'listino', 'prodotto', 'prodotti',
        'quanto costa', 'prezzo', 'disponibilita', 'ne hai', 'hai',
        'spedizione', 'tracking', 'pacco', 'voglio', 'vorrei', 'avrei bisogno'
    ]
    
    if any(word in text.lower() for word in trigger_words):
        await send_business_reply(
            "â“ Non ho capito bene. Usa /lista per il catalogo o /help per le FAQ."
        )

# ============================================================
# 3. HANDLER BUSINESS CONNECTION (Opzionale)
# ============================================================

async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ Gestisce messaggi ricevuti tramite Telegram Business.
    Questo handler viene chiamato SOLO per messaggi Business grazie al filtro custom.
    I messaggi normali vanno in handle_group_message o handle_private_message. """
    message = update.message or update.edited_message
    
    if not message or not message.text:
        return
    
    business_connection_id = message.business_connection_id
    
    logger.info(f"ðŸ“± Business message ricevuto")
    logger.info(f"   Connection ID: {business_connection_id}")
    logger.info(f"   Chat ID: {message.chat.id}")
    logger.info(f"   Testo: {message.text[:50]}...")
    
    text = message.text.strip()
    intent = calcola_intenzione(text)
    
    # Helper: Invia risposta Business
    async def send_business_reply(text_reply, parse_mode='HTML', reply_markup=None):
        """Invia messaggio usando business_connection_id. Questo Ã¨ OBBLIGATORIO per rispondere in Business."""
        try:
            await context.bot.send_message(
                business_connection_id=business_connection_id,
                chat_id=message.chat.id,
                text=text_reply,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
            logger.info(f"âœ… Risposta Business inviata")
        except Exception as e:
            logger.error(f"âŒ Errore invio Business: {e}")
    
    # 1. RICHIESTA LISTA
    if intent == "lista":
        lista = load_lista()
        if lista:
            # Dividi in chunk da 4000 caratteri (limite Telegram)
            for i in range(0, len(lista), 4000):
                await send_business_reply(lista[i:i+4000], parse_mode=None)
        return
    
    # 2. ORDINE
    if intent == "ordine":
        keyboard = [[
            InlineKeyboardButton("âœ… SÃ¬", callback_data=f"pay_ok_{message.message_id}"),
            InlineKeyboardButton("âŒ No", callback_data=f"pay_no_{message.message_id}")
        ]]
        await send_business_reply(
            "ðŸ¤” <b>Sembra un ordine!</b>\nC'Ã¨ il metodo di pagamento?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # 3. FAQ
    if intent == "faq":
        faq_data = load_faq()
        res = fuzzy_search_faq(text, faq_data.get("faq", []))
        if res.get("match"):
            await send_business_reply(
                f"âœ… <b>{res['item']['domanda']}</b>\n\n{res['item']['risposta']}"
            )
        return
    
    # 4. RICERCA PRODOTTI
    if intent == "ricerca_prodotti":
        l_res = fuzzy_search_lista(text, load_lista())
        if l_res.get("match"):
            await send_business_reply(
                f"ðŸ“¦ <b>Nel listino ho trovato:</b>\n\n{l_res['snippet']}"
            )
            return
    
    # 5. FALLBACK
    trigger_words = [
        'ordine', 'ordinare', 'lista', 'listino', 'prodotto', 'prodotti',
        'quanto costa', 'prezzo', 'disponibilita', 'ne hai', 'hai',
        'spedizione', 'tracking', 'pacco', 'voglio', 'vorrei', 'avrei bisogno'
    ]
    
    if any(word in text.lower() for word in trigger_words):
        await send_business_reply(
            "â“ Non ho capito bene. Usa /lista per il catalogo o /help per le FAQ."
        )

# ============================================================
# 3. HANDLER BUSINESS CONNECTION (Opzionale)
# ============================================================

async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message or update.edited_message
    
    if not message or not message.text:
        return
    
    business_connection_id = message.business_connection_id
    
    logger.info(f"ðŸ“± Business message ricevuto")
    logger.info(f"   Connection ID: {business_connection_id}")
    logger.info(f"   Chat ID: {message.chat.id}")
    logger.info(f"   Testo: {message.text[:50]}...")
    
    text = message.text.strip()
    intent = calcola_intenzione(text)
    
    # Helper: Invia risposta Business
    async def send_business_reply(text_reply, parse_mode='HTML', reply_markup=None):
        """
        Invia messaggio usando business_connection_id.
        Questo Ã¨ OBBLIGATORIO per rispondere in Business.
        """
        try:
            await context.bot.send_message(
                business_connection_id=business_connection_id,
                chat_id=message.chat.id,
                text=text_reply,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
            logger.info(f"âœ… Risposta Business inviata")
        except Exception as e:
            logger.error(f"âŒ Errore invio Business: {e}")
    
    # 1. RICHIESTA LISTA
    if intent == "lista":
        lista = load_lista()
        if lista:
            # Dividi in chunk da 4000 caratteri (limite Telegram)
            for i in range(0, len(lista), 4000):
                await send_business_reply(lista[i:i+4000], parse_mode=None)
        return
    
    # 2. ORDINE
    if intent == "ordine":
        keyboard = [[
            InlineKeyboardButton("âœ… SÃ¬", callback_data=f"pay_ok_{message.message_id}"),
            InlineKeyboardButton("âŒ No", callback_data=f"pay_no_{message.message_id}")
        ]]
        await send_business_reply(
            "ðŸ¤” <b>Sembra un ordine!</b>\nC'Ã¨ il metodo di pagamento?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # 3. FAQ
    if intent == "faq":
        faq_data = load_faq()
        res = fuzzy_search_faq(text, faq_data.get("faq", []))
        if res.get("match"):
            await send_business_reply(
                f"âœ… <b>{res['item']['domanda']}</b>\n\n{res['item']['risposta']}"
            )
        return
    
    # 4. RICERCA PRODOTTI
    if intent == "ricerca_prodotti":
        l_res = fuzzy_search_lista(text, load_lista())
        if l_res.get("match"):
            await send_business_reply(
                f"ðŸ“¦ <b>Nel listino ho trovato:</b>\n\n{l_res['snippet']}"
            )
            return
    
    # 5. FALLBACK
    trigger_words = [
        'ordine', 'ordinare', 'lista', 'listino', 'prodotto', 'prodotti',
        'quanto costa', 'prezzo', 'disponibilita', 'ne hai', 'hai',
        'spedizione', 'tracking', 'pacco', 'voglio', 'vorrei', 'avrei bisogno'
    ]
    
    if any(word in text.lower() for word in trigger_words):
        await send_business_reply(
            "â“ Non ho capito bene. Usa /lista per il catalogo o /help per le FAQ."
        )


# ============================================================
# 3. HANDLER BUSINESS CONNECTION (Opzionale)
# ============================================================

async def handle_business_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Gestisce nuove connessioni Telegram Business.
    Viene chiamato quando un account Business si connette al bot.
    """
    if update.business_connection:
        connection = update.business_connection
        logger.info(f"ðŸ”— Nuova connessione Business")
        logger.info(f"   Connection ID: {connection.id}")
        logger.info(f"   User ID: {connection.user.id}")
        logger.info(f"   Nome: {connection.user.first_name}")
        
        # Opzionale: Salva il connection_id per uso futuro
        context.bot_data.setdefault('business_connections', {})[connection.id] = {
            'user_id': connection.user.id,
            'timestamp': datetime.now().isoformat()
        }

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    text = message.text.strip()
    # OPZIONALE: Analisi dettagliata per debugging
    if intent_classifier:
        result = intent_classifier.classify(text)
        logger.info(f"ðŸ“Š Analisi dettagliata: {result}")
    
    intent = calcola_intenzione(text)
    
    # 1. ROUTER CENTRALE -> Lista
    if intent == "lista":
        lista = load_lista()
        if lista:
            for i in range(0, len(lista), 4000):
                await message.reply_text(lista[i:i+4000])
        return

    # 2. ORDINE
    if intent == "ordine":
        keyboard = [[
            InlineKeyboardButton("âœ… SÃ¬", callback_data=f"pay_ok_{message.message_id}"),
            InlineKeyboardButton("âŒ No", callback_data=f"pay_no_{message.message_id}")
        ]]
        await message.reply_text(
            "ðŸ¤” <b>Sembra un ordine!</b>\nC'Ã¨ il metodo di pagamento?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    # 3. FAQ (solo se conversazionale)
    if intent == "faq":
        faq_data = load_faq()
        res = fuzzy_search_faq(text, faq_data.get("faq", []))
        if res.get("match"):
            await message.reply_text(
                f"âœ… <b>{res['item']['domanda']}</b>\n\n{res['item']['risposta']}",
                parse_mode="HTML"
            )
            return

    # 4. RICERCA PRODOTTI
    if intent == "ricerca_prodotti":
        l_res = fuzzy_search_lista(text, load_lista())
        if l_res.get("match"):
            await message.reply_text(
                f"ðŸ“¦ <b>Nel listino ho trovato:</b>\n\n{l_res['snippet']}",
                parse_mode="HTML"
            )
            return

    # 5. FALLBACK
    await message.reply_text("â“ Non ho capito. Scrivi 'lista' o usa /help.")

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message or update.channel_post
    if not message or not message.text:
        return
    if message.from_user and message.from_user.is_bot:
        return

    text = message.text.strip()
    intent = calcola_intenzione(text)
    chat_id = message.chat.id

    # 1. LISTA COMPLETA
    if intent == "lista":
        lista = load_lista()
        if lista:
            for i in range(0, len(lista), 4000):
                await context.bot.send_message(
                    chat_id=message.chat.id,
                    text=lista[i:i+4000],
                    reply_to_message_id=message.message_id
                )
        return

    # 2. ORDINE
    if intent == "ordine":
        keyboard = [[
            InlineKeyboardButton("âœ… SÃ¬", callback_data=f"pay_ok_{message.message_id}"),
            InlineKeyboardButton("âŒ No", callback_data=f"pay_no_{message.message_id}")
        ]]
        await context.bot.send_message(
            chat_id=message.chat.id,
            text="ðŸ¤” <b>Sembra un ordine!</b>\nC'Ã¨ il metodo di pagamento?",
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
                text=f"âœ… <b>{res['item']['domanda']}</b>\n\n{res['item']['risposta']}",
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
                text=f"ðŸ“¦ <b>Nel listino ho trovato:</b>\n\n{l_res['snippet']}",
                parse_mode="HTML",
                reply_to_message_id=message.message_id
            )
            return
    
    # 5. FALLBACK INTELLIGENTE - Risponde solo se contiene keyword rilevanti
    trigger_words = [
        'ordine', 'ordinare', 'lista', 'listino', 'prodotto', 'prodotti',
        'quanto costa', 'prezzo', 'disponibilita', 'ne hai', 'hai',
        'spedizione', 'tracking', 'pacco', 'voglio', 'vorrei', 'avrei bisogno'
    ]
    
    if any(word in text.lower() for word in trigger_words):
        await context.bot.send_message(
            chat_id=message.chat.id,
            text="â“ Non ho capito bene. Usa /lista per il catalogo o /help per le FAQ.",
            reply_to_message_id=message.message_id
        )
        
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestisce i bottoni Inline e salva gli ordini confermati"""
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("pay_ok_"):
        original_msg = query.message.reply_to_message
        if original_msg:
            user = query.from_user
            add_ordine_confermato(
                user_id=user.id,
                user_name=user.first_name or "Sconosciuto",
                username=user.username or "nessuno",
                message_text=original_msg.text,
                chat_id=original_msg.chat.id,
                message_id=original_msg.message_id
            )
            
            await query.edit_message_text(f"âœ… Ordine confermato da {user.first_name}! ProcederÃ² appena possibile.")
            
            # Notifica admin
            if ADMIN_CHAT_ID:
                try:
                    notifica = (
                    f"ðŸ”” <b>NUOVO ORDINE CONFERMATO</b>\n\n"
                    f"ðŸ‘¤ Utente: {user.first_name} (@{user.username})\n"
                    f"ðŸ†” ID: <code>{user.id}</code>\n"
                    f"ðŸ“ Messaggio:\n<code>{original_msg.text[:200]}</code>"
                    )
                    await context.bot.send_message(ADMIN_CHAT_ID, notifica, parse_mode='HTML')
                except Exception as e:
                    logger.error(f"Errore notifica admin: {e}")
        else:
            await query.edit_message_text("âœ… Ottimo!")
            
    elif query.data.startswith("pay_no_"):
        await query.edit_message_text("ðŸ’¡ Per favore, indica il metodo (Bonifico, Crypto).")

# Benvenuto nuovi membri
async def handle_user_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.new_chat_members:
        return
    
    for member in update.message.new_chat_members:
        welcome_text = (
            f"ðŸ‘‹ Benvenuto {member.first_name}!\n\n"
            "ðŸ—’ï¸ Per favore prima di fare qualsiasi domanda o ordinare leggi interamente il listino "
            "dopo la lista prodotti dove troverai risposta alla maggior parte delle tue domande: "
            "tempi di spedizione, metodi di pagamento, come ordinare ecc. ðŸ—’ï¸\n\n"
            "ðŸ“‹ <b>Comandi disponibili:</b>\n"
            "â€¢ /help - Visualizza tutte le FAQ\n"
            "â€¢ /lista - Visualizza la lista prodotti"
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
        if not bot_application:
            logger.warning("⚠️ Bot non inizializzato al momento del webhook")
            return 'Bot not ready', 503
        
        json_data = request.get_json(force=True)
        
        if not json_data:
            logger.warning("⚠️ Webhook ricevuto senza dati")
            return 'No data', 400
        
        update = Update.de_json(json_data, bot_application.bot)
        
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        loop.run_until_complete(bot_application.process_update(update))
        
        return 'ok', 200
        
    except Exception as e:
        logger.error(f"❌ Errore webhook: {e}", exc_info=True)
        return 'Error', 500

# ============================================================================
# SETUP BOT
# ============================================================================

async def setup_bot():
    global bot_application, initialization_lock, PAROLE_CHIAVE_LISTA, intent_classifier
    
    if initialization_lock:
        return None
    
    initialization_lock = True
    
    try:
        logger.info("ðŸ”¡ Inizializzazione bot...")
        
        # IMPORTANTE: Inizializza il classifier PRIMA di tutto
        try:
            logger.info("🔧 Inizializzazione intent classifier...")
            
            # Aggiorna FAQ/Lista
            update_faq_from_web()
            update_lista_from_web()
            
            # Crea il classifier
            PAROLE_CHIAVE_LISTA = estrai_parole_chiave_lista()
            
            # Verifica che il classifier sia stato creato
            if intent_classifier is None:
                raise RuntimeError("Intent classifier non inizializzato correttamente")
            
            logger.info("✅ Intent classifier inizializzato con successo")
            
        except Exception as e:
            logger.error(f"❌ Errore inizializzazione classifier: {e}")
            try:
                intent_classifier = IntentClassifier(
                    lista_keywords=set(),
                    load_lista_func=load_lista
                )
                logger.warning("⚠️ Classifier inizializzato in modalità fallback")
            except Exception as e2:
                logger.critical(f"💀 FALLIMENTO TOTALE classifier: {e2}")
                initialization_lock = False
                raise
                
        application = Application.builder().token(BOT_TOKEN).updater(None).build()
        bot = await application.bot.get_me()
        get_bot_username.username = bot.username
        logger.info(f"Bot: @{bot.username}")
        
        # ============================================================
        # REGISTRAZIONE HANDLER (ORDINE IMPORTANTE!)
        # ============================================================
        
        # 1. COMANDI (prioritÃ  massima)
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
        
        # 2. STATUS UPDATES
        application.add_handler(MessageHandler(
            filters.StatusUpdate.NEW_CHAT_MEMBERS, 
            handle_user_status
        ))
        application.add_handler(ChatMemberHandler(
            handle_chat_member_update, 
            ChatMemberHandler.CHAT_MEMBER
        ))
        
        # 3. CALLBACK QUERY (bottoni inline)
        application.add_handler(CallbackQueryHandler(handle_callback_query))
        
        # 4. BUSINESS MESSAGES        
        application.add_handler(MessageHandler(
            business_filter & filters.TEXT & ~filters.COMMAND,
            handle_business_message
        ))
        logger.info("âœ… Handler Business registrato")
        
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

        # ============================================================
        # WEBHOOK CONFIGURATION
        # ============================================================
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
            logger.info(f"âœ… Webhook configurato: {WEBHOOK_URL}/webhook")
            logger.info(f"âœ… Business updates abilitati nel webhook")

        await application.initialize()
        await application.start()
        logger.info("ðŸ¤– Bot pronto!")
        logger.info("ðŸ“± Business support: ATTIVO")
        
        return application
        
    except Exception as e:
        logger.error(f"❌ Setup error: {e}")
        initialization_lock = False
        raise 

# ============================================================================
# SHUTDOWN HANDLER
# ============================================================================

async def shutdown_bot():
    """Chiude il bot in modo pulito"""
    global bot_application
    
    if bot_application:
        logger.info("🛑 Shutdown bot in corso...")
        try:
            await bot_application.stop()
            await bot_application.shutdown()
            logger.info("✅ Bot chiuso correttamente")
        except Exception as e:
            logger.error(f"❌ Errore durante shutdown: {e}")
    
# End main.py
