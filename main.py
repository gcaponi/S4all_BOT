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

# =============================================================================
# CONFIGURAZIONE LOGGING (DETTAGLIATO)
# =============================================================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =============================================================================
# VARIABILI DI AMBIENTE E COSTANTI
# =============================================================================
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

# =============================================================================
# UTILS: WEB FETCH, PARSING, I/O (SISTEMA DI AGGIORNAMENTO)
# =============================================================================
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
        return set()
    
    testo_norm = re.sub(r'[^\w\s]', ' ', testo.lower())
    parole = set(testo_norm.split())
    parole_filtrate = {p for p in parole if len(p) > 2}
    
    # Passa anche la funzione load_lista al classifier
    intent_classifier = IntentClassifier(
        lista_keywords=parole_filtrate,
        load_lista_func=load_lista
    )
    
    return parole_filtrate

def calcola_intenzione(text: str) -> str:
    """ NUOVA VERSIONE: Usa il classificatore intelligente """
    global intent_classifier
    
    # Se il classifier non è inizializzato, crealo
    if intent_classifier is None:
        PAROLE_CHIAVE_LISTA = estrai_parole_chiave_lista()
    
    # Classifica il messaggio
    result = intent_classifier.classify(text)
    
    # Log per debugging
    logger.info(f"🎯 Intento: {result.intent.value} (conf: {result.confidence:.2f})")
    logger.info(f"💡 Ragione: {result.reason}")
    logger.info(f"🔑 Match: {result.matched_keywords}")
    
    # Mappa IntentType ai tuoi valori attuali
    intent_map = {
        IntentType.RICHIESTA_LISTA: "lista",
        IntentType.INVIO_ORDINE: "ordine",
        IntentType.DOMANDA_FAQ: "faq",
        IntentType.RICERCA_PRODOTTO: "ricerca_prodotti",
        IntentType.SALUTO: "saluto",
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

# =============================================================================
# GESTIONE AUTORIZZAZIONI E UTENTI
# =============================================================================
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
    """Verifica se l'ID Telegram è presente tra gli autorizzati"""
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

# =============================================================================
# GESTIONE ORDINI CONFERMATI
# =============================================================================
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

# =============================================================================
# LOGICHE DI RICERCA INTELLIGENTE (CORE)
# =============================================================================
def calculate_similarity(text1: str, text2: str) -> float:
    """Calcola l'indice di somiglianza tra due stringhe (utilizzato per i refusi)"""
    return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

def normalize_text(text: str) -> str:
    """Rimuove simboli, punteggiatura e spazi eccessivi per facilitare il confronto"""
    text = re.sub(r'[^\w\s]', '', text)
    return re.sub(r'\s+', ' ', text).strip().lower()

def fuzzy_search_faq(user_message: str, faq_list: list) -> dict:
    """Cerca la risposta più pertinente nelle FAQ con sinonimi estesi"""
    user_normalized = normalize_text(user_message)
    
    # [SISTEMA SINONIMI ESTESO]
    keywords_map = {
        "spedizione": [
            "spedito", "spedisci", "spedite", "corriere", "pacco", 
            "invio", "mandato", "spedizioni", "arriva", "consegna",
            "inviato", "inviare", "hai spedito", "hai inviato",
            "mio pacco", "il pacco", "ordine spedito", "stato ordine"
        ],
        "tracking": [
            "track", "codice", "tracciabilità", "tracciamento", 
            "tracking", "traccia", "seguire", "dove",
            "numero tracking", "codice spedizione", "dove si trova"
        ],
        "tempi": [
            "quando arriva", "quanto tempo", "giorni", "ricevo", 
            "consegna", "tempistiche", "quanto ci vuole",
            "tempi di spedizione", "quanto tempo ci vuole"
        ],
        "pagamento": [
            "pagare", "metodi", "bonifico", "ricarica", "paypal", 
            "crypto", "pagamenti", "come pago", "pagamento"
        ],
        "ordinare": [
            "ordine", "ordinare", "fare ordine", "come ordino", 
            "voglio ordinare", "fare un ordine", "posso ordinare", 
            "come faccio", "procedura"
        ]
    }

    # [PRIORITÀ ALTA: MATCH CON KEYWORDS]
    for item in faq_list:
        domanda_norm = normalize_text(item["domanda"])
        risposta_norm = normalize_text(item["risposta"])
        
        for root, synonyms in keywords_map.items():
            if any(syn in user_normalized for syn in synonyms):
                # Match in domanda, risposta o keywords
                if (root in domanda_norm or root in risposta_norm or 
                    any(syn in risposta_norm for syn in synonyms)):
                    logger.info(f"✅ FAQ Match (keyword): {root} → score: 1.0")
                    return {'match': True, 'item': item, 'score': 1.0, 'method': 'keyword'}

    # [PRIORITÀ MEDIA: SIMILARITà]
    best_match = None
    best_score = 0
    
    for item in faq_list:
        domanda_norm = normalize_text(item["domanda"])
        
        # Match perfetto
        if user_normalized in domanda_norm or domanda_norm in user_normalized:
            logger.info(f"✅ FAQ Match (exact): score: 1.0")
            return {'match': True, 'item': item, 'score': 1.0, 'method': 'exact'}
        
        # Similarità
        score = calculate_similarity(user_normalized, domanda_norm)
        if score > best_score:
            best_score = score
            best_match = item
    
    # Soglia
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
    
    # ========================================
    # STEP 1: VERIFICA INTENT ESPLICITO
    # ========================================
    
    # Pattern che indicano CHIARAMENTE una richiesta di prodotto
    explicit_request_patterns = [
        r'\bhai\s+(la|il|dello|della|l\'|un[ao]?)\s*\w{4,}',     # "hai la creatina"
        r'\bvendete\s+\w{4,}',                                    # "vendete proteine"
        r'\bavete\s+(la|il|dello|della|l\'|un[ao]?)\s*\w{4,}',   # "avete il testosterone"
        r'\bquanto\s+costa\s+(la|il|dello|della|l\'|un[ao]?)\s*\w{4,}', # "quanto costa la creatina"
        r'\bprezzo\s+(di|del|della|dello)\s+\w{4,}',             # "prezzo del testosterone"
        r'\bcosto\s+(di|del|della|dello)\s+\w{4,}',              # "costo del prodotto"
        r'\bdisponibile\s+\w{4,}',                                # "disponibile creatina"
        r'\bdisponibilità\s+(di|del|della)\s+\w{4,}',            # "disponibilità del prodotto"
        r'\bin\s+stock\s+\w{4,}',                                 # "in stock testosterone"
        r'\bce\s+(la|il|l\'|hai|avete)\s*\w{4,}',                # "c'è la creatina"
        r'\bvorrei\s+(il|la|dello|della|un[ao]?)\s*\w{4,}',      # "vorrei il testosterone"
        r'\bcerco\s+\w{4,}',                                      # "cerco proteine"
        r'\bmi\s+serve\s+(il|la|un[ao]?)\s*\w{4,}',              # "mi serve il cialis"
    ]
    
    has_explicit_intent = False
    for pattern in explicit_request_patterns:
        if re.search(pattern, text_lower):
            has_explicit_intent = True
            logger.info(f"✅ Pattern richiesta esplicita: {pattern[:30]}")
            break
    
    # Query singola parola (solo se >5 caratteri per evitare "hai", "per", ecc.)
    words = user_normalized.split()
    if len(words) == 1 and len(user_normalized) > 5:
        has_explicit_intent = True
        logger.info(f"✅ Query singola: '{user_normalized}'")
    
    # Se NON ha intent esplicito → BLOCCA
    if not has_explicit_intent:
        logger.info(f"❌ Nessun intent esplicito di ricerca prodotto")
        return {'match': False, 'snippet': None, 'score': 0}
    
    # ========================================
    # STEP 2: ESTRAI NOME PRODOTTO
    # ========================================
    
    # Stopwords da ignorare
    stopwords = {
        'hai', 'avete', 'vendete', 'quanto', 'costa', 'prezzo', 'costo',
        'disponibile', 'disponibilità', 'stock', 'vorrei', 'cerco', 'serve',
        'per', 'sono', 'nel', 'con', 'che', 'questa', 'quello', 'tutte',
        'della', 'dello', 'delle', 'degli', 'alla', 'allo', 'alle', 'agli'
    }
    
    # Estrai parole significative (>4 caratteri, non stopwords)
    product_keywords = [
        w for w in words 
        if len(w) > 4 and w not in stopwords
    ]
    
    if not product_keywords:
        logger.info(f"❌ Nessuna keyword prodotto trovata")
        return {'match': False, 'snippet': None, 'score': 0}
    
    logger.info(f"🔍 Cerco prodotti con keywords: {product_keywords}")
    
    # ========================================
    # STEP 3: CERCA NEL LISTINO
    # ========================================
    
    lines = lista_text.split('\n')
    matched_lines = []
    
    for line in lines:
        if not line.strip():
            continue
        
        # Ignora linee che sono solo titoli/separatori
        if line.strip().startswith('_'):
            continue
        if line.strip().startswith('⬛') and line.strip().endswith('⬛'):
            continue
        if line.strip().startswith('🔘') and line.strip().endswith('🔘'):
            continue
        
        line_normalized = normalize_text(line)
        
        # Cerca match esatti (word boundary)
        for keyword in product_keywords:
            # Match con word boundary per evitare substring
            if re.search(r'\b' + re.escape(keyword) + r'\b', line_normalized, re.IGNORECASE):
                # Verifica che la linea contenga effettivamente un prodotto
                # (deve avere emoji 💊 o 💉 oppure formato prezzo)
                if ('💊' in line or '💉' in line or '€' in line):
                    matched_lines.append(line.strip())
                    logger.info(f"  ✅ Match: '{keyword}' in '{line[:50]}'")
                    break
    
    # ========================================
    # STEP 4: RISULTATO
    # ========================================
    
    if matched_lines:
        # Limita a max 15 righe per non sovraccaricare
        snippet = '\n'.join(matched_lines[:15])
        
        if len(snippet) > 3900:
            snippet = snippet[:3900] + "\n\n💡 (Scrivi il nome specifico per una ricerca più precisa)"
        
        score = len(matched_lines) / len(product_keywords) if product_keywords else 0
        
        logger.info(f"✅ Trovate {len(matched_lines)} righe prodotto")
        return {'match': True, 'snippet': snippet, 'score': min(score, 1.0)}
    
    logger.info(f"❌ Nessun prodotto trovato nel listino")
    return {'match': False, 'snippet': None, 'score': 0}

# =============================================================================
# HANDLERS: COMANDI (START, HELP, LISTA)
# =============================================================================
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

# =============================================================================
# HANDLERS: AMMINISTRAZIONE (SOLO ADMIN_CHAT_ID)
# =============================================================================
async def admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    msg = (
        "👑 <b>PANNELLO DI CONTROLLO ADMIN</b>\n\n"
        "<b>🔐 Comandi Admin:</b>\n"
        "• /genera_link - Crea il link per autorizzare nuovi utenti\n"
        "• /cambia_codice - Rigenera il token di sicurezza\n"
        "• /lista_autorizzati - Vedi chi può usare il bot\n"
        "• /revoca ID - Rimuovi un utente dal database\n"
        "• /aggiorna_faq - Scarica le FAQ da JustPaste\n"
        "• /aggiorna_lista - Scarica il listino da JustPaste\n"
        "• /ordini - Visualizza ordini confermati oggi\n\n"
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
    await update.message.reply_text(f"🔗 <b>Link Autorizzazione:</b>\n<code>{link}</code>", parse_mode='HTML')

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
    """Mostra all'admin gli ordini confermati oggi (Solo Admin in Privata)"""
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
        
        msg += f"<b>{i}. {user_name}</b> (@{username}) 🆔 ID: <code>{user_id}</code>\n"
        msg += f"   🕐 Ora: {ora}\n"
        msg += f"   📝 Messaggio:\n <code>{message[:300]}</code>\n\n"
    
    if len(msg) > 4000:
        for i in range(0, len(msg), 4000):
            await update.message.reply_text(msg[i:i+4000], parse_mode='HTML')
    else:
        await update.message.reply_text(msg, parse_mode='HTML')

# =============================================================================
# GESTIONE MESSAGGI: LOGICA UNIFICATA (PRIVATI E GRUPPI)
# =============================================================================
async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    text = message.text.strip()
    # OPZIONALE: Analisi dettagliata per debugging
    if intent_classifier:
        result = intent_classifier.classify(text)
        logger.info(f"📊 Analisi dettagliata: {result}")
    
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
            InlineKeyboardButton("✅ Sì", callback_data=f"pay_ok_{message.message_id}"),
            InlineKeyboardButton("❌ No", callback_data=f"pay_no_{message.message_id}")
        ]]
        await message.reply_text(
            "🤔 <b>Sembra un ordine!</b>\nC'è il metodo di pagamento?",
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
    
    # 5. FALLBACK INTELLIGENTE - Risponde solo se contiene keyword rilevanti
    trigger_words = [
        'ordine', 'ordinare', 'lista', 'listino', 'prodotto', 'prodotti',
        'quanto costa', 'prezzo', 'disponibilita', 'ne hai', 'hai',
        'spedizione', 'tracking', 'pacco', 'voglio', 'vorrei', 'avrei bisogno'
    ]
    
    if any(word in text.lower() for word in trigger_words):
        await context.bot.send_message(
            chat_id=message.chat.id,
            text="❓ Non ho capito bene. Usa /lista per il catalogo o /help per le FAQ.",
            reply_to_message_id=message.message_id
        )
        
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestisce i bottoni Inline e salva gli ordini confermati"""
    query = update.callback_query
    await query.answer()
    
    # ============================================
    # GESTIONE BOTTONI FAQ E LISTA
    # ============================================
    
    if query.data == "show_lista":
        logger.info("📋 Bottone LISTA cliccato")     
        lista = load_lista()
        is_business = (
            hasattr(query.message, 'business_connection_id') and 
            query.message.business_connection_id
        )
        
        if lista:
            try:
                # Elimina messaggio con bottoni
                if is_business:
                    await context.bot.edit_message_text(
                        business_connection_id=query.message.business_connection_id,
                        chat_id=query.message.chat.id,
                        message_id=query.message.message_id,
                        text="📋 Ecco il nostro listino prodotti:"
                    )
                
                # Invia lista
                if is_business:
                    for i in range(0, len(lista), 4000):
                        await context.bot.send_message(
                            business_connection_id=query.message.business_connection_id,
                            chat_id=query.message.chat.id,
                            text=lista[i:i+4000]
                        )
                        logger.info(f"✅ Lista inviata")
            except Exception as e:
                logger.error(f"❌ Errore invio lista: {e}")
        return
    
    elif query.data == "show_faq":
        logger.info("❓ Bottone FAQ cliccato")
        
        faq_data = load_faq()
        faq_list = faq_data.get("faq", [])
        is_business = (
            hasattr(query.message, 'business_connection_id') and 
            query.message.business_connection_id
        )
        
        if faq_list:
            full_text = "🗒️ <b>INFORMAZIONI E FAQ</b>\n\n"
            for item in faq_list:
                full_text += f"🔹 <b>{item['domanda']}</b>\n{item['risposta']}\n\n"
            
            try:
                # Elimina messaggio con bottoni
                if is_business:
                    await context.bot.edit_message_text(
                        business_connection_id=query.message.business_connection_id,
                        chat_id=query.message.chat.id,
                        message_id=query.message.message_id,
                        text="❓ Ecco le nostre FAQ:",
                        parse_mode='HTML'
                    )
                
                # Invia FAQ
                if len(full_text) > 4000:
                    for i in range(0, len(full_text), 4000):
                        if is_business:
                            await context.bot.send_message(
                                business_connection_id=query.message.business_connection_id,
                                chat_id=query.message.chat.id,
                                text=full_text[i:i+4000],
                                parse_mode='HTML'
                            )
                            logger.info(f"✅ FAQ inviate (chunk {i//4000 + 1})")
                else:
                    if is_business:
                        await context.bot.send_message(
                            business_connection_id=query.message.business_connection_id,
                            chat_id=query.message.chat.id,
                            text=full_text,
                            parse_mode='HTML'
                        )
                        logger.info("✅ FAQ inviate")
            except Exception as e:
                logger.error(f"❌ Errore invio FAQ: {e}")
        return
    
    elif query.data == "show_faq":
        faq_data = load_faq()
        faq_list = faq_data.get("faq", [])
        
        # Check se è Business
        is_business = (
            hasattr(query.message, 'business_connection_id') and 
            query.message.business_connection_id
        )
        
        if faq_list:
            full_text = "🗒️ <b>INFORMAZIONI E FAQ</b>\n\n"
            for item in faq_list:
                full_text += f"🔹 <b>{item['domanda']}</b>\n{item['risposta']}\n\n"
            
            try:
                # Elimina il messaggio con i bottoni
                if is_business:
                    await context.bot.delete_message(
                        business_connection_id=query.message.business_connection_id,
                        chat_id=query.message.chat.id,
                        message_id=query.message.message_id
                    )
                else:
                    await query.message.delete()
                
                # Invia FAQ
                if len(full_text) > 4000:
                    for i in range(0, len(full_text), 4000):
                        if is_business:
                            await context.bot.send_message(
                                business_connection_id=query.message.business_connection_id,
                                chat_id=query.message.chat.id,
                                text=full_text[i:i+4000],
                                parse_mode='HTML'
                            )
                        else:
                            await query.message.reply_text(full_text[i:i+4000], parse_mode='HTML')
                else:
                    if is_business:
                        await context.bot.send_message(
                            business_connection_id=query.message.business_connection_id,
                            chat_id=query.message.chat.id,
                            text=full_text,
                            parse_mode='HTML'
                        )
                    else:
                        await query.message.reply_text(full_text, parse_mode='HTML')
            except Exception as e:
                logger.error(f"❌ Errore invio FAQ: {e}")
        return
        
    if query.data.startswith("pay_ok_"):
        user = query.from_user
        
        # Estrai message_id originale dal callback_data
        try:
            original_msg_id = int(query.data.split("_")[-1])
        except:
            original_msg_id = None
        
        # Prova a trovare il messaggio originale
        original_msg = query.message.reply_to_message
        
        # Estrai testo ordine
        if original_msg and hasattr(original_msg, 'text'):
            # Chat normale con reply
            order_text = original_msg.text
        elif original_msg_id and f"order_text_{original_msg_id}" in context.bot_data:
            # Business - recupera dal bot_data
            order_text = context.bot_data[f"order_text_{original_msg_id}"]
            logger.info(f"📝 Testo recuperato da bot_data: {order_text}")
            # Pulisci bot_data
            del context.bot_data[f"order_text_{original_msg_id}"]
        else:
            # Fallback
            order_text = f"Ordine (msg ID: {original_msg_id})"
        
        logger.info(f"📝 Testo ordine finale: {order_text[:100]}")
        
        # SALVA l'ordine con il testo corretto
        add_ordine_confermato(
            user_id=user.id,
            user_name=user.first_name or "Sconosciuto",
            username=user.username or "nessuno",
            message_text=order_text,  # ← Testo originale!
            chat_id=query.message.chat.id,
            message_id=query.message.message_id
        )
        
        logger.info(f"✅ Ordine salvato: {user.first_name} ({user.id})")
        
        # Aggiorna messaggio
        try:
            # Controlla se è Business
            is_business = (
                hasattr(query.message, 'business_connection_id') and 
                query.message.business_connection_id
            )
            
            if is_business:
                await context.bot.edit_message_text(
                    business_connection_id=query.message.business_connection_id,
                    chat_id=query.message.chat.id,
                    message_id=query.message.message_id,
                    text=f"✅ Ordine confermato da {user.first_name}! Procederò appena possibile."
                )
            else:
                await query.edit_message_text(
                    f"✅ Ordine confermato da {user.first_name}! Procederò appena possibile."
                )
        except Exception as e:
            logger.error(f"❌ Errore edit message: {e}")
        
        # Notifica admin
        if ADMIN_CHAT_ID:
            try:
                notifica = (
                    f"📢 <b>NUOVO ORDINE CONFERMATO</b>\n\n"
                    f"👤 Utente: {user.first_name} (@{user.username})\n"
                    f"🆔 ID: <code>{user.id}</code>\n"
                    f"💬 Chat: <code>{query.message.chat.id}</code>\n"
                    f"📝 Testo:\n<code>{order_text[:200]}</code>"
                )
                await context.bot.send_message(ADMIN_CHAT_ID, notifica, parse_mode='HTML')
            except Exception as e:
                logger.error(f"❌ Errore notifica admin: {e}")
                
    elif query.data.startswith("pay_no_"):
        try:
            is_business = (
                hasattr(query.message, 'business_connection_id') and 
                query.message.business_connection_id
            )
            
            if is_business:
                await context.bot.edit_message_text(
                    business_connection_id=query.message.business_connection_id,
                    chat_id=query.message.chat.id,
                    message_id=query.message.message_id,
                    text="💡 Per favore, indica il metodo (Bonifico, Crypto)."
                )
            else:
                await query.edit_message_text("💡 Per favore, indica il metodo (Bonifico, Crypto).")
        except Exception as e:
            logger.error(f"❌ Errore pay_no: {e}")
            
# Benvenuto nuovi membri
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

# =============================================================================
# SETUP BOT
# =============================================================================

# [FILTRO BUSINESS MESSAGES]
async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        update.business_message
        or update.message
        or update.edited_message
    )
    
    if not message or not message.text:
        return
        
    business_connection_id = message.business_connection_id
    text = message.text.strip() 
    text_lower = text.lower()
    
    # [IGNORA MESSAGGI AUTOMATICI/BOT BUSINESS]
    
    # Metodo 1: Check is_bot (per bot veri)    
    if message.from_user and message.from_user.is_bot:
        logger.info(f"🤖 Messaggio da bot - IGNORATO")
        return
        
    # Metodo 2: Rileva messaggi automatici dal testo
    if any(keyword in text.upper() for keyword in [
        'MESSAGGIO AUTOMATICO', 'RISPONDO DAL LUNEDÌ', 'HO REGISTRATO LA TUA RICHIESTA'
    ]):
        logger.info(f"⏭️ Messaggio automatico ignorato")
        return 
    
    # Metodo 3: Ignora messaggi dell'admin (proprietario Business)
    if message.from_user.id == ADMIN_CHAT_ID:
        logger.info(f"⏭️ Messaggio da admin ignorato: {message.from_user.first_name}")
        return
    
    logger.info(f"📱 Business message: '{message.text}'")

    # ========================================
    # SUPER DEBUG - STAMPA JSON RAW COMPLETO
    # ========================================
    import json
    
    logger.info("=" * 70)
    logger.info("🔍 JSON RAW UPDATE COMPLETO:")
    
    try:
        # Converti l'update in dict per vedere TUTTO
        update_dict = update.to_dict()
        logger.info(json.dumps(update_dict, indent=2, ensure_ascii=False))
    except Exception as e:
        logger.error(f"Errore conversione: {e}")
    
    logger.info("=" * 70)
    
    # ========================================
    # DEBUG PRIMA DI TUTTO (SPOSTATO QUI!)
    # ========================================
    logger.info("=" * 70)
    logger.info("🔍 DEBUG COMPLETO MESSAGE:")
    logger.info(f"  from_user.id: {message.from_user.id}")
    logger.info(f"  from_user.first_name: {message.from_user.first_name}")
    logger.info(f"  from_user.last_name: {message.from_user.last_name}")
    logger.info(f"  from_user.username: {message.from_user.username}")
    logger.info(f"  chat.id: {message.chat.id}")
    logger.info(f"  chat.type: {message.chat.type}")
    logger.info(f"  chat.first_name: {getattr(message.chat, 'first_name', 'N/A')}")
    logger.info(f"  chat.last_name: {getattr(message.chat, 'last_name', 'N/A')}")
    logger.info(f"  chat.title: {getattr(message.chat, 'title', 'N/A')}")
    
    if hasattr(message, 'contact'):
        logger.info(f"  HAS CONTACT FIELD!")
        logger.info(f"  contact.first_name: {getattr(message.contact, 'first_name', 'N/A')}")
        logger.info(f"  contact.last_name: {getattr(message.contact, 'last_name', 'N/A')}")
        logger.info(f"  contact.phone_number: {getattr(message.contact, 'phone_number', 'N/A')}")
    else:
        logger.info(f"  NO CONTACT FIELD")
    
    # Stampa TUTTI gli attributi del message (per scoprire campi nascosti)
    logger.info(f"  ALL MESSAGE ATTRS: {[attr for attr in dir(message) if not attr.startswith('_')][:20]}")
    
    logger.info("=" * 70)
    
    # ========================================
    # WHITELIST CHECK
    # ========================================
    
    # ALLOWED_TAGS = ['aff', 'jgor5', 'ig5', 'sp20']
    
    # Prova prima il nome dal contatto Business (se disponibile)
    # contact_name = ""
    
    # Business messages hanno il contact
    # if hasattr(message, 'contact') and message.contact:
    #     contact_name = (message.contact.first_name or "") + " " + (message.contact.last_name or "")
    #     logger.info(f"📇 Nome contatto Business: '{contact_name}'")
    # 
    # Se non c'è contact, usa first_name + last_name standard
    # if not contact_name:
    #     contact_name = (message.from_user.first_name or "") + " " + (message.from_user.last_name or "")
    #     logger.info(f"👤 Nome utente Telegram: '{contact_name}'")
    # 
    # Verifica tag nel nome completo
    # has_tag = any(tag in contact_name.lower() for tag in ALLOWED_TAGS)
    
    # if not has_tag:
    #     logger.info(f"⏭️ Utente senza tag whitelisted: {contact_name.strip()}")
    #     return
    
    # logger.info(f"✅ Utente con tag whitelisted: {contact_name.strip()}")
    
    # [GESTISCI COMANDI IN BUSINESS]
    if text.startswith('/'):
        command = text.split()[0].lower()
        
        logger.info(f"🔧 Comando Business rilevato: {command}")
        
        # Helper per rispondere
        async def send_reply(text_reply, parse_mode='HTML'):
            try:
                await context.bot.send_message(
                    business_connection_id=business_connection_id,
                    chat_id=message.chat.id,
                    text=text_reply,
                    parse_mode=parse_mode
                )
            except Exception as e:
                logger.error(f"❌ Errore reply comando: {e}")
        
        # /help - Mostra FAQ
        if command == '/help':
            faq_data = load_faq()
            faq_list = faq_data.get("faq", [])
            
            if not faq_list:
                await send_reply("⚠️ FAQ non ancora configurate.")
                return
            
            full_text = "🗒️ <b>REGOLAMENTO E INFORMAZIONI</b>\n\n"
            for item in faq_list:
                full_text += f"🔹 <b>{item['domanda']}</b>\n{item['risposta']}\n\n"
            
            # Invia in chunks se troppo lungo
            if len(full_text) > 4000:
                for i in range(0, len(full_text), 4000):
                    await send_reply(full_text[i:i+4000])
            else:
                await send_reply(full_text)
            return
        
        # /lista - Mostra listino
        elif command == '/lista':
            lista = load_lista()
            if lista:
                for i in range(0, len(lista), 4000):
                    await send_reply(lista[i:i+4000], parse_mode=None)
            else:
                await send_reply("❌ Lista non disponibile.")
            return
        
        # /start - Messaggio benvenuto
        elif command == '/start':
            await send_reply(
                "👋 <b>Benvenuto!</b>\n\n"
                "Sono il tuo assistente automatico.\n\n"
                "Comandi disponibili:\n"
                "• /help - Mostra FAQ complete\n"
                "• /lista - Mostra listino prodotti\n"
                "• Scrivi 'lista' per il catalogo\n"
                "• Chiedimi info su spedizioni, pagamenti, etc."
            )
            return
        
        # Altri comandi
        else:
            await send_reply(
                "❓ Comando non riconosciuto.\n\n"
                "Comandi disponibili:\n"
                "• /help\n"
                "• /lista\n"
                "• /start"
            )
            return
            
    # [HELPER PER RISPONDERE IN BUSINESS]
    async def send_business_reply(text_reply, parse_mode='HTML', reply_markup=None):
        try:
            # Costruisci kwargs con tutti i parametri necessari
            kwargs = {
                "chat_id": message.chat.id,
                "business_connection_id": business_connection_id,
                "text": text_reply,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup
            }            
            # Aggiungi message_thread_id se presente
            if getattr(message, "message_thread_id", None):
                kwargs["message_thread_id"] = message.message_thread_id
            
            await context.bot.send_message(**kwargs)
            logger.info(f"✅ Business reply inviata")
        except Exception as e:
            logger.error(f"❌ Errore Business reply: {e}")
            
    intent = calcola_intenzione(text)
    logger.info(f"🔄 Intent ricevuto: '{intent}'")
    
    # 1. LISTA
    if intent == "lista":
        logger.info("➡️ Entrato in blocco LISTA")
        lista = load_lista()
        if lista:
            for i in range(0, len(lista), 4000):
                await send_business_reply(lista[i:i+4000], parse_mode=None)
        else:
            await send_business_reply("❌ Lista non disponibile al momento.")
        return
    
    # 2. ORDINE
    if intent == "ordine":
        logger.info("➡️ Entrato in blocco ORDINE")
        context.bot_data[f"order_text_{message.message_id}"] = text
        keyboard = [[
            InlineKeyboardButton("✅ Sì", callback_data=f"pay_ok_{message.message_id}"),
            InlineKeyboardButton("❌ No", callback_data=f"pay_no_{message.message_id}")
        ]]
        await send_business_reply(
            "🤔 <b>Sembra un ordine!</b>\nC'è il metodo di pagamento?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # 3. FAQ
    if intent == "faq":
        logger.info("➡️ Entrato in blocco FAQ")
        faq_data = load_faq()
        res = fuzzy_search_faq(text, faq_data.get("faq", []))
        if res.get("match"):
            await send_business_reply(
                f"✅ <b>{res['item']['domanda']}</b>\n\n{res['item']['risposta']}"
            )
            return
        else:
            await send_business_reply(
                "❓ Non ho trovato una risposta specifica. Usa /help per tutte le FAQ."
            )
        return
    
    # 4. RICERCA PRODOTTI
    if intent == "ricerca_prodotti":
        logger.info("➡️ Entrato in blocco RICERCA")
        l_res = fuzzy_search_lista(text, load_lista())
        if l_res.get("match"):
            await send_business_reply(
                f"📦 <b>Nel listino ho trovato:</b>\n\n{l_res['snippet']}"
            )
            return
            
    # 5. SALUTO
    if intent == "saluto":
        logger.info("➡️ Entrato in blocco SALUTO")
        # Check se contiene anche "ordinare/ordine"
        if any(word in text.lower() for word in ['ordinare', 'ordine', 'comprare', 'acquistare']):
            keyboard = [
                [InlineKeyboardButton("📋 PRODOTTI", callback_data="show_lista")],
                [InlineKeyboardButton("❓ FAQ", callback_data="show_faq")]
            ]
            await send_business_reply(
                "👋 Buongiorno!\n\n📋 PRODOTTI per vedere cosa abbiamo\n❓ FAQ per domande frequenti",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            # Saluto semplice senza intent di ordinare
            keyboard = [
                [InlineKeyboardButton("📋 PRODOTTI", callback_data="show_lista")],
                [InlineKeyboardButton("❓ FAQ", callback_data="show_faq")]
            ]
            await send_business_reply(
                "👋 Buongiorno! Come posso aiutarla?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        return
        
    # 6. FALLBACK
    trigger_words = [
        'ordine', 'ordinare', 'lista', 'listino', 'prodotto', 'prodotti',
        'quanto costa', 'prezzo', 'hai', 'disponibile'
    ]
    
    if any(word in text.lower() for word in trigger_words):
        keyboard = [
            [InlineKeyboardButton("📋 PRODOTTI", callback_data="show_lista")],
            [InlineKeyboardButton("❓ FAQ", callback_data="show_faq")]
        ]
        await send_business_reply(
            "👋 SALVE!\n\n📋 PRODOTTI per vedere cosa abbiamo\n ❓FAQ per domande frequenti",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

class BusinessMessageFilter(filters.MessageFilter):
    """Filtro custom per identificare messaggi Telegram Business"""
    def filter(self, message):
        return (
            hasattr(message, 'business_connection_id') and 
            message.business_connection_id is not None
        )

business_filter = BusinessMessageFilter()

async def initialize_bot():
    """Inizializza il bot - Ottimizzato con file locali"""
    global bot_application, initialization_lock, PAROLE_CHIAVE_LISTA
    
    if initialization_lock:
        return None
    
    initialization_lock = True
    
    try:
        logger.info("🔡 Inizializzazione bot...")
        
        try:
            # [USA FILE LOCALI SE ESISTONO]
            # FAQ - Verifica e scarica se vuoto
            if os.path.exists(FAQ_FILE):
                faq_data = load_faq()
                if faq_data.get("faq"):
                    logger.info(f"📋 FAQ da file locale ({len(faq_data['faq'])} elementi)")
                else:
                    logger.warning("⚠️ FAQ vuote, scarico da web")
                    update_faq_from_web()
            else:
                logger.info("📥 Download FAQ...")
                update_faq_from_web()
                
            # Lista prodotti
            if os.path.exists(LISTA_FILE):
                logger.info("📦 Lista da file locale")
            else:
                logger.info("📥 Download lista...")
                update_lista_from_web()
                
            # Estrai Keywords
            PAROLE_CHIAVE_LISTA = estrai_parole_chiave_lista()
            logger.info(f"✅ {len(PAROLE_CHIAVE_LISTA)} keywords estratte")
            
        except Exception as e:
            logger.warning(f"⚠️ Prefetch: {e}")
        
        application = Application.builder().token(BOT_TOKEN).updater(None).build()
        bot = await application.bot.get_me()
        get_bot_username.username = bot.username
        logger.info(f"Bot: @{bot.username}")
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
        application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_user_status))
        application.add_handler(ChatMemberHandler(handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
        application.add_handler(CallbackQueryHandler(handle_callback_query))
        
        # BUSINESS MESSAGES HANDLER (group=0 = massima priorità)
        application.add_handler(
            MessageHandler(
                business_filter & filters.TEXT & ~filters.COMMAND,
                handle_business_message
            ),
            group=0
        )
        logger.info("✅ Handler Business Messages registrato (priority group=0)")

        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP | filters.ChatType.CHANNEL), handle_group_message))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_private_message))

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


@app.route('/')
def index():
    return "🤖 Bot attivo! ✅", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    global bot_application, bot_initialized
    
    if not bot_initialized:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            bot_application = loop.run_until_complete(initialize_bot())
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

#End main.py
