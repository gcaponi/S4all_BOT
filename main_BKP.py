import os
import json
import logging
from flask import Flask, request, make_response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ChatMemberHandler, filters, ContextTypes, TypeHandler
import secrets
import re
import requests
import pickle
import asyncio
from intent_classifier import EnhancedIntentClassifier
from bs4 import BeautifulSoup
from difflib import SequenceMatcher
from datetime import datetime
from zoneinfo import ZoneInfo

# Import database module (PostgreSQL)
import database as db
from database import is_admin, is_super_admin, add_admin, remove_admin, get_all_admins, init_admins_table
from memory_buffer import chat_memory
from enhanced_logging import classification_logger, setup_enhanced_logging
from response_handlers import ResponseBuilder, HandlerResponseDispatcher, create_dispatcher
from error_handlers import (
    async_log_errors, async_safe_execute, safe_execute, ErrorContext,
    log_db_error, log_api_error, log_validation_error
)


classifier_instance = None
response_dispatcher = None  # Global dispatcher per risposte

data_ora = datetime.now().strftime("%d-%m-%Y %H:%M")

def get_dispatcher():
    """Ottiene il dispatcher globale, inizializzandolo se necessario."""
    global response_dispatcher
    if response_dispatcher is None:
        response_dispatcher = create_dispatcher()
    return response_dispatcher
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
# FUNZIONI DATABASE (PostgreSQL via database.py)
# ============================================================================

# User tags - usa database.py
get_user_tag = db.get_user_tag
set_user_tag = db.set_user_tag
remove_user_tag = db.remove_user_tag
load_user_tags = db.load_user_tags
load_user_tags_simple = db.load_user_tags_simple

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

@safe_execute(default_return="", operation_name="fetch_markdown_from_html", log_level="error")
def fetch_markdown_from_html(url: str) -> str:
    """Scarica il contenuto HTML da JustPaste e lo converte in testo pulito"""
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    content = soup.select_one("#articleContent")
    if content is None:
        log_api_error(endpoint=url, response="Contenuto non trovato in #articleContent")
        raise RuntimeError("Contenuto non trovato nel selettore #articleContent")
    return content.get_text("\n").strip()

def parse_faq(markdown: str) -> list:
    """Parsa FAQ - versione semplificata robusta"""
    faq_list = []
    
    # Rimuovi line breaks problematici nelle emoji
    markdown = markdown.replace('\n ', ' ')
    
    # STEP 1: Trova sezioni principali con emoji doppie
    sections = re.findall(r'([🤔📨💵⬛])\s*([A-ZÀÈÉÌÒÙ\s]+?)\s*\1(.*?)(?=\n[🤔📨💵⬛]|$)', 
                          markdown, re.DOTALL)
    
    for emoji, title, content in sections:
        title = title.strip()
        content = content.strip()
        
        if not content:
            continue
        
        # Se contiene sottosezioni 📍🔘, parsale
        if '📍' in content:
            qa_pairs = re.findall(r'📍\s*([^\n🔘]+?)\s*🔘\s*([^📍]+?)(?=📍|$)', 
                                  content, re.DOTALL)
            for q, a in qa_pairs:
                faq_list.append({
                    "domanda": q.strip(),
                    "risposta": a.strip()
                })
        else:
            # Sezione senza sottosezioni
            faq_list.append({
                "domanda": title,
                "risposta": content
            })
    
    logger.info(f"✅ Parsate {len(faq_list)} FAQ totali")
    for i, faq in enumerate(faq_list[:5], 1):
        logger.info(f"  FAQ {i}: '{faq['domanda'][:50]}'")
    
    if len(faq_list) == 0:
        logger.error("❌ Nessuna FAQ trovata!")
    
    return faq_list

def write_faq_json(faq: list, filename: str):
    """Salva le FAQ strutturate in un file JSON locale"""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"faq": faq}, f, ensure_ascii=False, indent=2)

async def update_faq_from_web():
    """Sincronizza le FAQ scaricandole dal link JustPaste configurato"""
    logger.info(f"📥 Tentativo download FAQ da: {PASTE_URL}")
    
    # Esegui fetch in thread separato (operazione I/O bloccante)
    import asyncio
    loop = asyncio.get_event_loop()
    markdown = await loop.run_in_executor(None, fetch_markdown_from_html, PASTE_URL)
    
    if not markdown:
        logger.error("❌ Markdown vuoto o errore fetch")
        return False
    
    logger.info(f"✅ Markdown scaricato: {len(markdown)} caratteri")
    
    # DEBUG CRITICO: Mostra EMOJI TROVATE
    logger.info("🔍 CERCO EMOJI NEL TESTO...")
    import re
    
    # Conta emoji
    emoji_count = len(re.findall(r'[🤔📨💵⬛📍🔘]', markdown))
    logger.info(f"🔤 Numero totale emoji trovate: {emoji_count}")
    
    # Mostra posizioni delle prime 5 emoji
    matches = list(re.finditer(r'[🤔📨💵⬛📍🔘]', markdown))
    for i, match in enumerate(matches[:10]):
        start = max(0, match.start() - 20)
        end = min(len(markdown), match.start() + 80)
        context = markdown[start:end].replace('\n', ' ')
        logger.info(f"  Emoji {i+1} '{match.group()}' a pos {match.start()}: ...{context}...")
    
    faq = parse_faq(markdown)
    
    if not faq:
        logger.error(f"❌ Nessuna FAQ trovata!")
        return False
    
    write_faq_json(faq, FAQ_FILE)
    logger.info(f"✅ FAQ sincronizzate: {len(faq)} elementi salvati.")
    return True

@safe_execute(default_return=False, operation_name="update_lista_from_web")
def update_lista_from_web():
    """Scarica il listino prodotti e lo salva nel file locale lista.txt"""
    r = requests.get(LISTA_URL, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    content = soup.select_one("#articleContent")
    if content:
        text = content.get_text("\n").strip()
        with open(LISTA_FILE, "w", encoding="utf-8") as f:
            f.write(text)
        logger.info("✅ Listino prodotti aggiornato con successo.")
        return True
    log_api_error(endpoint=LISTA_URL, response="Contenuto non trovato")
    return False

def load_lista():
    """Carica il contenuto testuale del listino dal file locale"""
    if os.path.exists(LISTA_FILE):
        with open(LISTA_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return ""

@safe_execute(default_return={}, operation_name="load_json_file")
def load_json_file(filename, default=None):
    """Carica in sicurezza file JSON evitando crash se il file è corrotto o assente"""
    if os.path.exists(filename):
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
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
    """Cerca FAQ con pattern specifici per le tue domande"""
    user_normalized = normalize_text(user_message)
    text_lower = user_message.lower()
    
    # Pattern specifici basati sulle FAQ reali
    faq_patterns = {
        "tracking": {
            "keywords": ["tracking", "tracciamento", "codice", "numero", "traccia", "dove", "pacco"],
            "match_in": ["dopo quanto ricevo", "quando spedisci", "tracking"]
        },
        "spedizione": {
            "keywords": ["spedizione", "spedito", "spedire", "corriere", "consegna", "arriva", "giorni"],
            "match_in": ["dopo quanto ricevo", "quando spedisci", "costo spedizione"]
        },
        "tempi": {
            "keywords": ["quanto tempo", "quando arriva", "dopo quanto", "tempistiche", "giorni"],
            "match_in": ["dopo quanto ricevo", "quando spedisci"]
        },
        "pagamento": {
            "keywords": ["pagamento", "pagare", "bonifico", "crypto", "bitcoin", "usdt", "metodi"],
            "match_in": ["metodi di pagamento"]
        },
        "sconto": {
            "keywords": ["sconto", "sconti", "promozione", "offerta", "riduzione"],
            "match_in": ["sconto"]
        },
        "ordine": {
            "keywords": ["ordinare", "ordine", "come ordino", "procedura"],
            "match_in": ["come ordinare"]
        },
        "minimo": {
            "keywords": ["minimo", "ordine minimo", "quanto minimo"],
            "match_in": ["minimo"]
        },
        "rimborso": {
            "keywords": ["rimborso", "rimborsi", "garanzia", "restituire"],
            "match_in": ["rimborsi"]
        }
    }
    
    # STEP 1: Match esatto su pattern
    for tema, config in faq_patterns.items():
        if any(kw in text_lower for kw in config["keywords"]):
            for faq in faq_list:
                domanda_norm = normalize_text(faq["domanda"])
                if any(phrase in domanda_norm for phrase in config["match_in"]):
                    logger.info(f"✅ FAQ Match (pattern {tema}): score 1.0")
                    return {'match': True, 'item': faq, 'score': 1.0, 'method': 'pattern'}
    
    # STEP 2: Similarity search (fallback)
    best_match = None
    best_score = 0
    
    for faq in faq_list:
        domanda_norm = normalize_text(faq["domanda"])
        
        if user_normalized in domanda_norm or domanda_norm in user_normalized:
            logger.info(f"✅ FAQ Match (substring): score 1.0")
            return {'match': True, 'item': faq, 'score': 1.0, 'method': 'substring'}
        
        score = calculate_similarity(user_normalized, domanda_norm)
        if score > best_score:
            best_score = score
            best_match = faq
    
    if best_match and best_score >= 0.50:
        logger.info(f"✅ FAQ Match (similarity): score {best_score:.2f}")
        return {'match': True, 'item': best_match, 'score': best_score, 'method': 'similarity'}
    
    logger.info(f"❌ FAQ: No match (best score: {best_score:.2f})")
    return {'match': False, 'item': None, 'score': best_score, 'method': None}

def fuzzy_search_lista(user_message: str, lista_text: str) -> dict:
    """
    Cerca prodotti nel listino con pattern FUZZY (ricerca intelligente).
    Non usa dizionari hardcoded ma confronta le parole chiave con il testo.
    """
    if not lista_text:
        return {'match': False, 'snippet': None, 'score': 0}
    
    text_lower = user_message.lower()
    # Normalizzazione base (trattini e spazi)
    text_lower = text_lower.replace("-", " ") 
    user_normalized = normalize_text(text_lower)
    
    # Escludi domande conversazioni generiche
    conversational_questions = [
        r'^(manca|serve|vuoi|ti\s+serve|altro)\s*(altro|qualcosa)?\??$',
        r'^(tutto\s+)?(ok|bene|perfetto)\??$',
        r'^(e\s+)?(poi|dopo|ancora)\??$',
        r'^(grazie|ok)\b',
    ]

    for pattern in conversational_questions:
        if re.search(pattern, user_normalized, re.I):
            logger.info(f"⏭️ Domanda conversazione: '{user_normalized}' - skip search")
            return {'match': False, 'snippet': None, 'score': 0}
            
    # STEP 1: VERIFICA INTENT ESPLICITO (Pattern forti)
    explicit_request_patterns = [
        r'\bhai\s+(la|il|dello|della|l\'|un[ao]?)\s*\w{3,}',
        r'\bvendete\s+\w{3,}',
        r'\bavete\s+(la|il|dello|della|l\'|un[ao]?)\s*\w{3,}',
        r'\bquanto\s+costa\s+(la|il|dello|della|l\'|un[ao]?)\s*\w{3,}',
        r'\bprezzo\s+(di|del|della|dello)\s+\w{3,}',
        r'\bcosto\s+(di|del|della|dello)\s+\w{3,}',
        r'\bdisponibile\s+\w{3,}',
        r'\bdisponibilità\s+(di|del|della)\s+\w{3,}',
        r'\bin\s+stock\s+\w{3,}',
        r'\bce\s+(la|il|l\'|hai|avete)\s*\w{3,}',
        r'\bvorrei\s+(il|la|dello|della|un[ao]?)\s*\w{3,}',
        r'\bcerco\s+\w{3,}',
        r'\bmi\s+serve\s+(il|la|un[ao]?)\s*\w{3,}',
    ]
    
    has_explicit_intent = False
    for pattern in explicit_request_patterns:
        if re.search(pattern, text_lower):
            has_explicit_intent = True
            logger.info(f"✅ Pattern richiesta esplicita: {pattern[:30]}")
            break
    
    words = user_normalized.split()
    
    # LOGICA "IMPLICIT SEARCH" per query brevi (es "bpc 157", "trembo")
    # Se il messaggio è breve e sembra una lista di prodotti, lo trattiamo come search
    if not has_explicit_intent:
        if len(user_normalized) < 25 and len(words) <= 3 and len(user_normalized) >= 3:
            has_explicit_intent = True
            logger.info(f"✅ Query breve implicita detected: '{user_normalized}'")
            
    # Fix per singola parola (es "trembo")
    if len(words) == 1 and len(user_normalized) >= 3:
        has_explicit_intent = True
    
    if not has_explicit_intent:
        logger.info(f"❌ Nessun intent esplicito di ricerca prodotto")
        return {'match': False, 'snippet': None, 'score': 0}
    
    # STEP 2: ESTRAI KEYWORDS VALIDE
    stopwords = {
        'hai', 'avete', 'vendete', 'quanto', 'costa', 'prezzo', 'costo',
        'disponibile', 'disponibilità', 'stock', 'vorrei', 'cerco', 'serve',
        'per', 'sono', 'nel', 'con', 'che', 'questa', 'quello', 'tutte',
        'della', 'dello', 'delle', 'degli', 'alla', 'allo', 'alle', 'agli',
        'info', 'ciao', 'buongiorno', 'sera', 'salve'
    }
    
    # Escludi numeri, stopwords quantità e preposizioni/articoli comuni
    numeric_stopwords = [
        # Numeri
        'uno', 'due', 'tre', 'quattro', 'cinque', 'sei', 'sette', 
        'otto', 'nove', 'dieci', 'undici', 'dodici',
        # Quantità
        'confezioni', 'confezione', 'flaconi', 'flacone', 
        'pezzi', 'pezzo', 'scatole', 'scatola', 'bottiglie', 'bottiglia',
        # Preposizioni e articoli (causano falsi match)
        'per', 'con', 'senza', 'da', 'su', 'in', 'di',
        'del', 'della', 'dello', 'dei', 'delle', 'degli',
        'al', 'alla', 'allo', 'ai', 'alle', 'agli',
        'nel', 'nella', 'nello', 'nei', 'nelle', 'negli'
    ]

    product_keywords = [
    word for word in user_normalized.split() 
    if len(word) >= 3 
    and word not in numeric_stopwords
    and not word.isdigit()  # Escludi anche "3", "10", etc.
]
    
    # Recupera parole di 2 lettere solo se significative (es "gh", "tb")
    special_short_keywords = {'gh', 'tb', 't3', 't4'}
    for w in words:
        if w in special_short_keywords and w not in product_keywords:
             product_keywords.append(w)

    if not product_keywords:
        logger.info(f"❌ Nessuna keyword prodotto trovata")
        return {'match': False, 'snippet': None, 'score': 0}
    
    logger.info(f"🔍 Cerco prodotti con keywords: {product_keywords}")
    
    # STEP 3: CERCA NEL LISTINO (Use Fuzzy logic)
    lines = lista_text.split('\n')
    matched_lines = []
    
    for line in lines:
        if not line.strip(): continue
        
        # Skip sezioni header/footer
        if line.strip().startswith('_'): continue
        if line.strip().startswith('⬛') and line.strip().endswith('⬛'): continue
        if line.strip().startswith('🔘') and line.strip().endswith('🔘'): continue
        
        # Normalizza riga per confronto
        line_clean = line.lower().replace("-", " ").replace("/", " ")
        line_words = normalize_text(line_clean).split()
        
        match_found = False
        
        # Controlla ogni keyword dell'utente contro ogni parola della riga
        for keyword in product_keywords:
            for line_word in line_words:
                
                # Check 1: Strict Substring (es "bpc" in "bpc 157" o "bpc157")
                if keyword in line_word:
                    # Verifica che sia riga prodotto
                    if ('💊' in line or '💉' in line or '€' in line):
                        match_found = True
                        break
                
                # Check 2: Fuzzy Prefix (es "trembo" vs "trenbo"lone)
                # Se la keyword è lunga almeno 4 chars, controlliamo se somiglia all'inizio della parola
                if len(keyword) >= 4 and len(line_word) >= 4:
                    # Prendi il prefisso della parola del listino lungo quanto la keyword
                    prefix = line_word[:len(keyword)]
                    similarity = calculate_similarity(keyword, prefix)
                    
                    if similarity >= 0.90: # Soglia alta per prefissi
                        if ('💊' in line or '💉' in line or '€' in line):
                            logger.info(f"  ⚡ Fuzzy prefix match: '{keyword}' ~ '{prefix}' (in {line_word}) -> {similarity:.2f}")
                            match_found = True
                            break
                            
                # Check 3: Fuzzy Full Word (es "tren" vs "trenbolone" NO, ma "winstrol" vs "winstro" SI)
                # Questo serve più per typo (es "testoterone")
                sim_full = calculate_similarity(keyword, line_word)
                if sim_full > 0.85:
                    if ('💊' in line or '💉' in line or '€' in line):
                        match_found = True
                        break
            
            if match_found: 
                break
        
        if match_found:
            matched_lines.append(line.strip())
            
    # STEP 4: RISULTATO
    if matched_lines:
        snippet = '\n'.join(matched_lines[:15])
        
        if len(snippet) > 3900:
            snippet = snippet[:3900] + "\n\n💡 (Scrivi il nome specifico per una ricerca più precisa)"
        
        score = 1.0
        
        logger.info(f"✅ Trovate {len(matched_lines)} righe prodotto")
        return {'match': True, 'snippet': snippet, 'score': score}
    
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
        logger.info(f"✅ {len(PAROLE_CHIAVE_LISTA)} keywords estratte")
    
    return PAROLE_CHIAVE_LISTA


def init_classifier():
    """Inizializza il classificatore una sola volta con keywords dinamiche dalla lista"""
    global classifier_instance, PAROLE_CHIAVE_LISTA
    if classifier_instance is None:
        # Assicuriamoci di avere le keywords aggiornate
        if not PAROLE_CHIAVE_LISTA:
            PAROLE_CHIAVE_LISTA = estrai_parole_chiave_lista()
        
        # Crea classificatore con keywords dinamiche
        classifier_instance = EnhancedIntentClassifier(dynamic_product_keywords=PAROLE_CHIAVE_LISTA)
        
        # Carica il modello addestrato se esiste
        try:
            if os.path.exists('intent_classifier_model.pkl'):
                classifier_instance.load_model('intent_classifier_model.pkl')
                logger.info("✅ Classificatore caricato da file")
            else:
                logger.info("⚠️  Nessun modello pre-addestrato, uso classificatore di base")
        except Exception as e:
            logger.error(f"❌ Errore nel caricamento modello: {e}")
            classifier_instance = EnhancedIntentClassifier(dynamic_product_keywords=PAROLE_CHIAVE_LISTA)
    return classifier_instance

def calcola_intenzione(text):
    """
    Versione migliorata che usa EnhancedIntentClassifier
    Mantiene compatibilità con gli intent esistenti nel codice
    """
    try:
        # Inizializza se necessario
        classifier = init_classifier()
        
        # Classifica il messaggio con threshold checking
        intent_classificato, confidence = classifier.classify_with_threshold(text)
        
        logger.info(f"🔍 Classificazione: '{text}' -> {intent_classificato} ({confidence:.2f})")
        
        # Log per analisi e metriche
        classification_logger.log_classification(
            text=text,
            intent=intent_classificato,
            confidence=confidence,
            method='hybrid_threshold'
        )
        
        # Mappa gli intent del nuovo classificatore agli intent del vecchio sistema
        intent_map = {
            "list": "lista",           # list -> lista
            "order": "ordine",         # order -> ordine
            "faq": "faq",              # faq -> faq
            "search": "ricerca_prodotti",  # search -> ricerca_prodotti
            "saluto": "saluto",        # saluto -> saluto
            "contact": "contact",      # contact -> contact (se necessario)
            "order_confirmation": "conferma_ordine",
            "fallback_mute": "fallback_mute",
            "fallback": "fallback"     # fallback -> fallback
        }
        
        # Converti l'intent
        intent_finale = intent_map.get(intent_classificato, "fallback")
        
        # Se confidence è troppo bassa, forza fallback
        if confidence < 0.4:
            intent_finale = "fallback"
        
        # Log dettagliato per debug
        if intent_finale == "fallback":
            logger.warning(f"⚠️  Fallback per: '{text}' (confidence: {confidence:.2f})")
        else:
            logger.info(f"✅ Intent riconosciuto: {intent_finale}")
        
        # Restituisci l'intent (solo stringa, per compatibilità)
        return intent_finale
        
    except Exception as e:
        logger.error(f"❌ Errore in calcola_intenzione: {e}")
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
    if not is_admin(update.effective_user.id):
        return
    msg = (
        "👑 <b>PANNELLO DI CONTROLLO ADMIN</b>\n\n"
        "<b>👑 Comandi SUPER ADMIN:</b>\n"
        "• /addadmin ID - Aggiungi nuovo admin\n"
        "• /removeadmin ID - Rimuovi admin\n\n"
        "<b>📝 Comandi Admin:</b>\n"
        "• /aggiorna_faq - Scarica le FAQ da JustPaste\n"
        "• /aggiorna_lista - Scarica il listino da JustPaste\n"
        "• /cambia_codice - Rigenera il token di sicurezza\n"
        "• /clearordini [giorni] - Cancella ordini vecchi\n"
        "• /cleanlogs [giorni] - Cancella log classificazioni vecchi (default: 30)\n"
        "• /genera_link - Crea link autorizzazione utenti\n"
        "• /lista_autorizzati - Vedi utenti autorizzati\n"
        "• /listadmins - Vedi lista admin\n"
        "• /listtags - Vedi clienti con tag\n"
        "• /ordini - Visualizza ordini oggi\n"
        "• /revoca ID - Rimuovi utente\n"
        "• /removetag ID - Rimuovi tag cliente\n\n"
        "<b>👤 Comandi Utente:</b>\n"
        "• /start - Avvia il bot\n"
        "• /help - FAQ e regolamento\n"
        "• /lista - Listino prodotti"
    )
    await update.message.reply_text(msg, parse_mode='HTML')

async def aggiorna_faq_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if await update_faq_from_web():
        await update.message.reply_text("✅ FAQ sincronizzate con successo.")
    else:
        await update.message.reply_text("❌ Errore durante l'aggiornamento FAQ.")

async def aggiorna_lista_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if update_lista_from_web():
        # Aggiorna anche le parole chiave del classificatore
        global PAROLE_CHIAVE_LISTA, classifier_instance
        PAROLE_CHIAVE_LISTA = estrai_parole_chiave_lista()
        
        # Se il classificatore esiste già, aggiorna le sue keywords
        if classifier_instance:
            classifier_instance.product_keywords = list(PAROLE_CHIAVE_LISTA)
            logger.info(f"✅ Classificatore aggiornato con {len(PAROLE_CHIAVE_LISTA)} nuove keywords")
        
        await update.message.reply_text(f"✅ Listino prodotti aggiornato.\n📊 {len(PAROLE_CHIAVE_LISTA)} keywords estratte.")
    else:
        await update.message.reply_text("❌ Errore aggiornamento listino.")

async def genera_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    link = f"https://t.me/{get_bot_username.username}?start={load_access_code()}"
    await update.message.reply_text(
        f"🔗 <b>Link Autorizzazione:</b>\n<a href='{link}'>{link}</a>",
        parse_mode='HTML'
    )

async def cambia_codice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    new_code = secrets.token_urlsafe(12)
    save_access_code(new_code)
    link = f"https://t.me/{get_bot_username.username}?start={new_code}"
    await update.message.reply_text(f"✅ Nuovo codice generato:\n<code>{link}</code>", parse_mode='HTML')

async def lista_autorizzati_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    users = load_authorized_users()
    if not users:
        await update.message.reply_text("Nessun utente registrato.")
        return
    msg = "👥 <b>UTENTI ABILITATI:</b>\n\n"
    for uid, info in users.items():
        msg += f"- {info['name']} (@{info.get('username', 'N/A')}) [<code>{uid}</code>]\n"
    await update.message.reply_text(msg, parse_mode='HTML')

async def revoca_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not context.args: return
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
    if not is_admin(update.effective_user.id):
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
        data = ordine.get('data', 'N/A')
        message = ordine.get('message', 'N/A')
        chat_id = ordine.get('chat_id', 'N/A')
        msg += f"<b>{i}. {user_name}</b> (@{username}) 🕐 {data}\n"
        msg += f"  📝 Messaggio:\n  <code>{message[:100]}...</code>\n\n"
    
    if len(msg) > 4000:
        for i in range(0, len(msg), 4000):
            await update.message.reply_text(msg[i:i+4000], parse_mode='HTML')
    else:
        await update.message.reply_text(msg, parse_mode='HTML')

async def list_tags_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra tutti i clienti registrati con tag - /listtags"""
    if not is_admin(update.effective_user.id):
        return
    
    # USA LA FUNZIONE COMPATIBILE CHE NON USA LE NUOVE COLONNE
    tags = load_user_tags_simple()  # ✅ Questa funzione esiste già!
    
    if not tags:
        await update.message.reply_text("Nessun cliente registrato con tag")
        return
    
    msg = "📋 <b>CLIENTI REGISTRATI CON TAG</b>\n\n"
    
    for user_id, tag in tags.items():
        try:
            user = await context.bot.get_chat(int(user_id))
            nome = user.first_name or "Sconosciuto"
            username = f"@{user.username}" if user.username else "nessuno"
            msg += f"• {nome} ({username}) ID <code>{user_id}</code> → <b>{tag}</b>\n"
        except:
            msg += f"• ID <code>{user_id}</code> → <b>{tag}</b>\n"
    
    if len(msg) > 4000:
        for i in range(0, len(msg), 4000):
            await update.message.reply_text(msg[i:i+4000], parse_mode='HTML')
    else:
        await update.message.reply_text(msg, parse_mode='HTML')

async def remove_tag_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rimuovi tag cliente - /removetag USER_ID"""
    if not is_admin(update.effective_user.id):
        return
    
    if not context.args:
        await update.message.reply_text("Uso: /removetag USER_ID")
        return
    
    user_id = context.args[0]
    if remove_user_tag(user_id):
        await update.message.reply_text(f"✅ Tag rimosso per user {user_id}")
    else:
        await update.message.reply_text(f"❌ User {user_id} non trovato")

async def clear_ordini_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancella ordini più vecchi di N giorni - /clearordini [giorni]"""
    if not is_admin(update.effective_user.id):
        return
    
    giorni = 1
    
    if context.args:
        try:
            giorni = int(context.args[0])
        except:
            await update.message.reply_text("❌ Uso: /clearordini [giorni]\nEsempio: /clearordini 7")
            return
    
    deleted = db.clear_old_orders(giorni)
    await update.message.reply_text(
        f"🗑️ Cancellati {deleted} ordini più vecchi di {giorni} giorn{'o' if giorni == 1 else 'i'}"
    )

async def cleanlogs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancella log classificazioni vecchi - /cleanlogs [giorni]"""
    if not is_admin(update.effective_user.id):
        return
    
    giorni = 30
    
    if context.args:
        try:
            giorni = int(context.args[0])
        except:
            await update.message.reply_text("❌ Uso: /cleanlogs [giorni]\nEsempio: /cleanlogs 30")
            return
    
    deleted = db.cleanup_old_classifications(giorni)
    await update.message.reply_text(
        f"🗑️ Cancellati {deleted} log di classificazione più vecchi di {giorni} giorn{'o' if giorni == 1 else 'i'}"
    )

async def addadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Aggiunge un nuovo admin - Solo SUPER ADMIN - /addadmin USER_ID"""
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Solo il SUPER ADMIN può aggiungere altri admin.")
        return
    
    if not context.args:
        await update.message.reply_text("Uso: /addadmin USER_ID")
        return
    
    try:
        new_admin_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID non valido")
        return
    
    if is_admin(new_admin_id):
        await update.message.reply_text(f"⚠️ User {new_admin_id} è già admin")
        return
    
    if add_admin(new_admin_id, added_by=update.effective_user.id, is_super=False):
        await update.message.reply_text(f"✅ Admin aggiunto: {new_admin_id}")
    else:
        await update.message.reply_text("❌ Errore aggiunta admin")

async def removeadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rimuove un admin - Solo SUPER ADMIN - /removeadmin USER_ID"""
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Solo il SUPER ADMIN può rimuovere admin.")
        return
    
    if not context.args:
        await update.message.reply_text("Uso: /removeadmin USER_ID")
        return
    
    try:
        target_admin_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID non valido")
        return
    
    if target_admin_id == update.effective_user.id:
        await update.message.reply_text("⛔ Non puoi rimuovere te stesso")
        return
    
    if not is_admin(target_admin_id):
        await update.message.reply_text(f"⚠️ User {target_admin_id} non è admin")
        return
    
    if remove_admin(target_admin_id):
        await update.message.reply_text(f"✅ Admin rimosso: {target_admin_id}")
    else:
        await update.message.reply_text("❌ Errore: non puoi rimuovere il SUPER ADMIN")

async def listadmins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra lista di tutti gli admin - /listadmins"""
    if not is_admin(update.effective_user.id):
        return
    
    admins = get_all_admins()
    
    if not admins:
        await update.message.reply_text("Nessun admin configurato")
        return
    
    msg = "👑 <b>LISTA ADMIN</b>\n\n"
    
    for admin in admins:
        user_id = admin['user_id']
        is_super = admin['is_super']
        added_at = admin['added_at']
        
        try:
            user = await context.bot.get_chat(user_id)
            nome = user.first_name or "Sconosciuto"
            username = f"@{user.username}" if user.username else "nessuno"
            
            if is_super:
                msg += f"👑 <b>{nome}</b> ({username}) [SUPER ADMIN]\n"
            else:
                msg += f"• {nome} ({username})\n"
                msg += f"  Aggiunto: {added_at.strftime('%d/%m/%Y')}\n"
        except:
            if is_super:
                msg += f"👑 ID <code>{user_id}</code> [SUPER ADMIN]\n"
            else:
                msg += f"• ID <code>{user_id}</code>\n"
        
        msg += "\n"
    
    await update.message.reply_text(msg, parse_mode='HTML')

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
    
    #   [IGNORA BOT]
    
    if message.from_user and message.from_user.is_bot:
        logger.info(f"🤖 Bot ignorato")
        return
    
    #   [HELPER INVIO RISPOSTE]
    
    async def send_business_reply(text_reply, parse_mode='HTML', reply_markup=None):
        try:
            await context.bot.send_message(
                business_connection_id=business_connection_id,
                chat_id=chat_id,
                text=text_reply,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
          #  logger.info(f"✅ Reply inviata")
        except Exception as e:
            logger.error(f"❌ Errore invio: {e}")

    #   [RILEVA ADMIN AUTOMATICAMENTE]
    
    # Se from_user.id != chat.id → Admin sta scrivendo al cliente
    if user_id != chat_id:
        logger.info(f"⏭️ Admin (user={user_id}) scrive a cliente (chat={chat_id})")
        # Attiva pausa bot per questa chat
        db.set_admin_active(chat_id, active=True)
        logger.info(f"⏸️ Bot messo in PAUSA per chat {chat_id}")

        # ECCEZIONE: Comando /reg
        if text_lower.startswith('/reg'):
            logger.info(f"✅ Comando /reg dal admin - ESEGUO")
            
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
            try:
                # Nel Business Message, il cliente è quello della chat
                client_user = update.business_message.chat
                client_name = getattr(client_user, 'first_name', None) or "Sconosciuto"
                client_username = getattr(client_user, 'username', None)
                
                # Registra il cliente con tutti i dati
                set_user_tag(chat_id, tag, client_name, client_username)
                
                logger.info(f"👤 Cliente registrato: {client_name} (@{client_username}) con tag {tag}")
            except Exception as e:
                logger.error(f"❌ Errore estrazione dati cliente: {e}")
                # Fallback: registra solo con tag
                set_user_tag(chat_id, tag)
        
        # Ignora tutti gli altri messaggi dell'admin (inclusi automatici!)
        logger.info(f"⏭️ Messaggio admin ignorato")
        return

    #   [MESSAGGIO DAL CLIENTE]
    
    logger.info(f"📱 Messaggio da cliente {user_id}: '{text}'")
    
    #   [CHECK PAUSA BOT (admin attivo)]
    
    session = db.get_chat_session(chat_id)
    
    if session and session[0]:  # admin_active = True
        last_admin_time = session[1]
        inactive_seconds = (datetime.now() - last_admin_time).total_seconds()
        
        if inactive_seconds < 900:  # 15 minuti
            logger.info(f"⏸️ Bot in PAUSA - admin attivo (ultimo msg {inactive_seconds/60:.0f} min fa)")
            return
        else:
            # Timeout - riattiva bot
            db.set_admin_active(chat_id, active=False)
            logger.info(f"▶️ Bot RIATTIVATO - timeout admin (30 min)")
    
    #   [CHECK AUTO-MESSAGE (ogni 30 min)]
    
    should_send_auto = True
    
    if session and session[2]:  # last_auto_msg_time esiste
        last_auto = session[2]
        elapsed = (datetime.now() - last_auto).total_seconds()
        
        if elapsed < 1800:  # Meno di 30 min
            should_send_auto = False
            logger.info(f"⏭️ Auto-msg skip (inviato {elapsed/60:.0f} min fa)")
    
    #   [CHECK FASCIA ORARIA AUTO-MESSAGE]

    now = datetime.now(ZoneInfo("Europe/Rome"))
    weekday = now.weekday()  # 0=Lun, 4=Ven, 5=Sab, 6=Dom
    hour = now.hour
    
    # Sabato o Domenica → sempre auto-message
    if weekday >= 5:
        should_send_auto_by_time = True
        logger.info(f"⏰ Weekend - auto-message abilitato")
    # Lunedì-Venerdì
    else:
        # Fuori orario lavorativo (17:00-07:00)
        if hour >= 17 or hour < 7:
            should_send_auto_by_time = True
            logger.info(f"⏰ Fuori orario lavorativo ({hour}:00) - auto-message abilitato")
        else:
            should_send_auto_by_time = False
            logger.info(f"⏰ Orario lavorativo ({hour}:00) - auto-message disabilitato")
    
    if should_send_auto and should_send_auto_by_time:
        auto_msg = (
            "Ciao grazie per avermi contattato.\n\n"
            "Rispondo dal *lunedì al venerdì* (ESCLUSI GIORNI FESTIVITÀ) "
            "dalle ore *07:00* alle ore *17:00*\n\n"
            "Ho registrato la tua richiesta, risponderò non appena sarò disponibile. "
            "Grazie per la pazienza (lascia scritto tutto, a volte rispondo anche fuori orario)\n\n"
            "_I messaggi inviati dopo le ore 17:00 del venerdì, verranno risposti di LUNEDI'_"
        )
        
        await send_business_reply(auto_msg, parse_mode='Markdown')
        db.update_auto_message_time(chat_id)
        logger.info(f"📨 Auto-message inviato a {chat_id}")

    #   [CHECK WHITELIST TAG]   
    
    user_tag = get_user_tag(user_id)
    
    if not user_tag:
        logger.info(f"⛔ Cliente {user_id} non registrato - ignoro")
        return
    
    logger.info(f"✅ Cliente con tag: {user_tag}")
    
    #   [MEMORIA CONVERSAZIONALE]       
    
    # Recupera contesto conversazionale
    last_entities = await chat_memory.get_last_entities(chat_id)
    
    # Risolvi riferimenti pronominali ("quello", "quella", etc.)
    text_enriched = chat_memory.resolve_references(text, last_entities)
    
    if text_enriched != text:
        logger.info(f"🔗 Testo arricchito: '{text}' → '{text_enriched}'")
        text_to_classify = text_enriched
    else:
        text_to_classify = text
    
    #   [CALCOLA INTENTO E RISPONDI]        
    
    intent = calcola_intenzione(text_to_classify)
    logger.info(f"🔄 Intent ricevuto: '{intent}'")
    
    # 0. FALLBACK MUTO (priorità assoluta - silenzio)
    if intent == "fallback_mute":
        logger.info(f"➡️ Entrato in blocco FALLBACK MUTO - nessuna risposta, esco silenziosamente")
        return  # 🔇 NON invia nulla, esci immediatamente

    dispatcher = get_dispatcher()
    text_lower = text.lower()

    # 1. LISTA
    if intent == "lista":
        logger.info(f"➡️ Entrato in blocco LISTA")
        await dispatcher.send_lista(
            send_func=lambda **kwargs: send_business_reply(**{**kwargs, 'parse_mode': None}),
            parse_mode=None
        )
        return
    
    # 2. ORDINE
    if intent == "ordine":
        logger.info(f"➡️ Entrato in blocco ORDINE")
        
        # Salva l'ordine temporaneamente
        callback_data = f"pay_ok_{user_id}_{message.message_id}"
        order_data = {
            'text': text,
            'user_id': user_id,
            'chat_id': chat_id,
            'message_id': message.message_id
        }
        
        if not hasattr(context, 'bot_data'):
            context.bot_data = {}
        if 'pending_orders' not in context.bot_data:
            context.bot_data['pending_orders'] = {}
        context.bot_data['pending_orders'][callback_data] = order_data
        logger.info(f"💾 Ordine temporaneo salvato: {callback_data}")
        
        await dispatcher.send_ordine(
            send_func=send_business_reply,
            text_lower=text_lower,
            message_id=message.message_id,
            user_id=user_id,
            parse_mode="HTML"
        )
        return
    
    # 2.5 CONFERMA ORDINE
    if intent == "conferma_ordine":
        logger.info(f"➡️ Entrato in blocco CONFERMA ORDINE")
        await dispatcher.send_conferma_ordine(send_func=send_business_reply)
        return
    
    # 3. FAQ
    if intent == "faq":
        logger.info(f"➡️ Entrato in blocco FAQ")
        faq_data = load_faq()
        res = fuzzy_search_faq(text, faq_data.get("faq", []))
        if res.get("match"):
            await dispatcher.send_faq(
                send_func=send_business_reply,
                domanda=res['item']['domanda'],
                risposta=res['item']['risposta']
            )
        return
    
    # 4. RICERCA PRODOTTI
    if intent == "ricerca_prodotti":
        logger.info(f"➡️ Entrato in blocco RICERCA")
        l_res = fuzzy_search_lista(text, load_lista())
        if l_res.get("match"):
            await dispatcher.send_ricerca_prodotti(
                send_func=send_business_reply,
                snippet=l_res['snippet']
            )
            return
    
    # 5. FALLBACK
    if intent == "fallback":
        logger.info(f"➡️ Entrato in blocco FALLBACK")

        # Controlla se è una conversazione che richiede umano (parole chiave)
        human_keywords = ['preparato', 'acqua', 'dosi', 'consegnato', 'ritirato', 
                         'disturbo', 'speriamo', 'tra l\'altro', 'non sono stato']
        
        if any(kw in text_lower for kw in human_keywords):
            logger.info(f"⏸️ Fallback silenzioso: conversazione umana rilevata")
            return  # NON invia nulla
    
    await dispatcher.send_fallback(send_func=send_business_reply, text_lower=text_lower)
    return

# ============================================================================
# HANDLER MESSAGGI PRIVATI
# ============================================================================

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    text = message.text.strip()
    intent = calcola_intenzione(text)
    
    dispatcher = get_dispatcher()
    text_lower = text.lower()
    
    # 0. FALLBACK MUTO (priorità assoluta - silenzio)
    if intent == "fallback_mute":
        logger.info(f"➡️ Entrato in blocco FALLBACK MUTO - nessuna risposta, esco silenziosamente")
        return  # 🔇 NON invia nulla, esci immediatamente

    # 1. LISTA
    if intent == "lista":
        await dispatcher.send_lista(send_func=message.reply_text)
        return

    # 2. ORDINE
    if intent == "ordine":
        await dispatcher.send_ordine(
            send_func=message.reply_text,
            text_lower=text_lower,
            message_id=message.message_id,
            parse_mode="HTML"
        )
        return

    # 2.5 CONFERMA ORDINE
    if intent == "conferma_ordine":
        logger.info(f"➡️ Entrato in blocco CONFERMA ORDINE")
        await dispatcher.send_conferma_ordine(send_func=message.reply_text)
        return

    # 3. FAQ
    if intent == "faq":
        faq_data = load_faq()
        res = fuzzy_search_faq(text, faq_data.get("faq", []))
        if res.get("match"):
            await dispatcher.send_faq(
                send_func=message.reply_text,
                domanda=res['item']['domanda'],
                risposta=res['item']['risposta']
            )
            return

    # 4. RICERCA PRODOTTI
    if intent == "ricerca_prodotti":
        l_res = fuzzy_search_lista(text, load_lista())
        if l_res.get("match"):
            await dispatcher.send_ricerca_prodotti(
                send_func=message.reply_text,
                snippet=l_res['snippet']
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
    
    dispatcher = get_dispatcher()

    # Helper per inviare messaggi in gruppo con reply
    async def send_group_reply(**kwargs):
        await context.bot.send_message(
            chat_id=message.chat.id,
            reply_to_message_id=message.message_id,
            **kwargs
        )

    # 0. FALLBACK MUTO (priorità assoluta - silenzio)
    if intent == "fallback_mute":
        logger.info(f"➡️ Entrato in blocco FALLBACK MUTO - nessuna risposta, esco silenziosamente")
        return  # 🔇 NON invia nulla, esci immediatamente

    # 1. LISTA
    if intent == "lista":
        await dispatcher.send_lista(send_func=send_group_reply)
        return

    # 2. ORDINE (semplificato per gruppi - senza check acqua)
    if intent == "ordine":
        keyboard = [[
            InlineKeyboardButton("✅ Sì", callback_data=f"pay_ok_{message.message_id}"),
            InlineKeyboardButton("❌ No", callback_data=f"pay_no_{message.message_id}")
        ]]
        await send_group_reply(
            text="🤔 <b>Sembra un ordine!</b>\nC'è il metodo di pagamento?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    # 3. FAQ
    if intent == "faq":
        faq_data = load_faq()
        res = fuzzy_search_faq(text, faq_data.get("faq", []))
        if res.get("match"):
            await dispatcher.send_faq(
                send_func=send_group_reply,
                domanda=res['item']['domanda'],
                risposta=res['item']['risposta']
            )
        return

    # 4. RICERCA PRODOTTI
    if intent == "ricerca_prodotti":
        l_res = fuzzy_search_lista(text, load_lista())
        if l_res.get("match"):
            await dispatcher.send_ricerca_prodotti(
                send_func=send_group_reply,
                snippet=l_res['snippet']
            )
            return
    
    # 5. FALLBACK
    trigger_words = [
        'ordine', 'lista', 'listino', 'prodotto', 'quanto costa',
        'spedizione', 'tracking', 'voglio', 'vorrei'
    ]
    
    if any(word in text.lower() for word in trigger_words):
        await send_group_reply(text="❓ Non ho capito. Usa /lista o /help.")

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
                    f"👤 Utente: {user.first_name} (@{user.username})  🕐 {data_ora}\n"
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
        
        # Setup enhanced logging
        setup_enhanced_logging()
        
        # ========================================
        # INIZIALIZZA DATABASE POSTGRESQL
        # ========================================
        logger.info("🗄️ Inizializzazione database...")
        if db.init_db():
            db.init_chat_sessions_table()
            logger.info("✅ Tabella chat_sessions pronta")
            
            logger.info("🔍 DEBUG: Checking user_tags columns...")
        try:
            session = db.SessionLocal()
            inspector = inspect(session.bind)
            columns = inspector.get_columns('user_tags')
            logger.info(f"🗂️ user_tags columns: {[col['name'] for col in columns]}")
            session.close()
        except Exception as e:
            logger.error(f"❌ Column check failed: {e}")

            # Inizializza tabella admins
            init_admins_table()
            
            # Aggiungi SUPER ADMIN da variabile ambiente (immutabile)
            if ADMIN_CHAT_ID and ADMIN_CHAT_ID != 0:
                add_admin(ADMIN_CHAT_ID, added_by=None, is_super=True)
                logger.info(f"✅ SUPER ADMIN configurato: {ADMIN_CHAT_ID}")
            
            # Auto-cleanup classificazioni vecchie (retention: 30 giorni)
            deleted = db.cleanup_old_classifications(days=30)
            if deleted > 0:
                logger.info(f"🗑️ Auto-cleanup: {deleted} log rimossi")
            
            # Inizializza memoria conversazionale
            await chat_memory.init_db()
            logger.info("✅ Memoria conversazionale pronta")
            
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
            
            logger.info("🔧 Inizializzazione classificatore...")
            classifier = init_classifier()
            logger.info("✅ Classificatore pronto")
            
        except Exception as e:
            logger.error(f"❌ Errore init: {e}")
        
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
        application.add_handler(CommandHandler("clearordini", clear_ordini_command))
        application.add_handler(CommandHandler("cleanlogs", cleanlogs_command))
        application.add_handler(CommandHandler("addadmin", addadmin_command))
        application.add_handler(CommandHandler("removeadmin", removeadmin_command))
        application.add_handler(CommandHandler("listadmins", listadmins_command))

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
        
        # ========================================
        # SCHEDULER RETRAINING AUTOMATICO
        # ========================================
        async def scheduled_retraining():
            """Controlla ogni ora se è necessario retraining"""
            while True:
                try:
                    await asyncio.sleep(3600)  # Ogni 1 ora
                    
                    from feedback_handler import get_retraining_status, trigger_retraining
                    
                    status = get_retraining_status()
                    if status['can_retrain']:
                        logger.info(f"🔄 Avvio retraining automatico ({status['feedback_pending']} feedback pending)")
                        result = trigger_retraining()
                        
                        if result['success']:
                            logger.info(f"✅ Retraining auto completato: {result['accuracy']:.2%}")
                            # Notifica admin
                            if ADMIN_CHAT_ID:
                                try:
                                    await application.bot.send_message(
                                        ADMIN_CHAT_ID,
                                        f"🤖 <b>Retraining Automatico Completato</b>\n\n"
                                        f"🎯 Accuracy: {result['accuracy']:.2%}\n"
                                        f"📚 Train: {result['train_size']} esempi\n"
                                        f"🧪 Test: {result['test_size']} esempi\n\n"
                                        f"⚠️ <b>IMPORTANTE:</b> Scarica il modello aggiornato dalla dashboard per non perderlo in caso di riavvio!\n"
                                        f"🔗 https://s4all-bot-nsf6.onrender.com/admin/stats?token=S4all",
                                        parse_mode='HTML'
                                    )
                                except Exception as e:
                                    logger.error(f"❌ Errore notifica admin: {e}")
                        else:
                            logger.warning(f"⚠️ Retraining auto fallito: {result['message']}")
                    else:
                        logger.debug(f"⏭️ Retraining auto: solo {status['feedback_pending']} feedback")
                        
                except Exception as e:
                    logger.error(f"❌ Errore scheduler retraining: {e}")
        
        # Avvia scheduler in background
        asyncio.create_task(scheduled_retraining())
        logger.info("⏰ Scheduler retraining avviato (ogni 1 ora)")

        await application.bot.set_my_commands([
            ("start", "Avvia il bot"),
            ("help", "Mostra FAQ e regolamento"),
            ("lista", "Visualizza il listino prodotti"),
            ("admin_help", "Pannello comandi admin"),
            ("genera_link", "Genera link autorizzazione"),
            ("cambia_codice", "Rigenera codice accesso"),
            ("lista_autorizzati", "Lista utenti abilitati"),
            ("revoca", "Rimuovi utente"),
            ("aggiorna_faq", "Aggiorna FAQ da web"),
            ("aggiorna_lista", "Aggiorna listino da web"),
            ("ordini", "Visualizza ordini oggi"),
            ("listtags", "Lista clienti con tag"),
            ("removetag", "Rimuovi tag cliente"),
            ("clearordini", "Cancella ordini vecchi"),
            ("cleanlogs", "Cancella log classificazioni vecchi"),
            ("addadmin", "Aggiungi admin"),
            ("removeadmin", "Rimuovi admin"),
            ("listadmins", "Lista admin")
        ])
        
        return application
        
    except Exception as e:
        logger.error(f"❌ Setup error: {e}")
        initialization_lock = False
        raise

# ========================================
# ENDPOINT ADMIN STATS
# ========================================

@app.route('/admin/stats', methods=['GET'])
def admin_stats():
    """Dashboard interattiva per correzione classificazioni"""
    auth_token = request.args.get('token')
    if auth_token != os.environ.get('ADMIN_TOKEN', 'S4all'):
        return {"error": "Unauthorized"}, 401
    
    from enhanced_logging import classification_logger
    
    # Recupera tutti i messaggi classificati (ultimi 100) dal database
    cases = db.get_recent_classifications(limit=100)
    stats = classification_logger.get_stats()
    feedback_stats = db.get_feedback_stats()
    
    # Lista intent disponibili
    available_intents = ['order', 'search', 'faq', 'list', 'contact', 'saluto', 'order_confirmation', 'fallback', 'fallback_mute']
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bot ML Training Dashboard</title>
        <meta charset="UTF-8">
        <style>
            * {{ box-sizing: border-box; }}
            body {{ 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; 
                margin: 0; 
                padding: 20px; 
                background: #f0f2f5; 
            }}
            .header {{ 
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                color: white; 
                padding: 30px; 
                border-radius: 12px; 
                margin-bottom: 20px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            }}
            .header h1 {{ margin: 0; font-size: 28px; }}
            .stats-bar {{ 
                display: flex; 
                gap: 20px; 
                margin-top: 15px;
                flex-wrap: wrap;
            }}
            .stat-box {{ 
                background: rgba(255,255,255,0.2); 
                padding: 15px 25px; 
                border-radius: 8px;
                backdrop-filter: blur(10px);
            }}
            .stat-value {{ font-size: 24px; font-weight: bold; }}
            .stat-label {{ font-size: 12px; opacity: 0.9; }}
            
            .container {{ max-width: 1400px; margin: 0 auto; }}
            
            .filters {{ 
                background: white; 
                padding: 20px; 
                border-radius: 12px; 
                margin-bottom: 20px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.05);
                display: flex;
                gap: 15px;
                align-items: center;
                flex-wrap: wrap;
            }}
            .filters label {{ font-weight: 600; color: #555; }}
            .filters select, .filters input {{
                padding: 10px 15px;
                border: 2px solid #e0e0e0;
                border-radius: 8px;
                font-size: 14px;
                min-width: 150px;
            }}
            .filters select:focus, .filters input:focus {{
                outline: none;
                border-color: #667eea;
            }}
            
            .messages-table {{
                background: white;
                border-radius: 12px;
                overflow: hidden;
                box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            }}
            table {{ 
                width: 100%; 
                border-collapse: collapse; 
            }}
            th {{ 
                background: #f8f9fa; 
                padding: 15px; 
                text-align: left; 
                font-weight: 600; 
                color: #555;
                border-bottom: 2px solid #e0e0e0;
                position: sticky;
                top: 0;
            }}
            td {{ 
                padding: 15px; 
                border-bottom: 1px solid #f0f0f0;
                vertical-align: middle;
            }}
            tr:hover {{ background: #f8f9fa; }}
            
            .message-text {{ 
                max-width: 400px; 
                word-break: break-word;
                font-family: monospace;
                font-size: 13px;
                background: #f5f5f5;
                padding: 8px 12px;
                border-radius: 6px;
            }}
            
            .intent-badge {{
                display: inline-block;
                padding: 6px 12px;
                border-radius: 20px;
                font-size: 12px;
                font-weight: 600;
                text-transform: uppercase;
            }}
            .intent-order {{ background: #e3f2fd; color: #1565c0; }}
            .intent-search {{ background: #f3e5f5; color: #7b1fa2; }}
            .intent-faq {{ background: #e8f5e9; color: #2e7d32; }}
            .intent-list {{ background: #fff3e0; color: #ef6c00; }}
            .intent-fallback {{ background: #ffebee; color: #c62828; }}
            .intent-saluto {{ background: #e0f7fa; color: #00838f; }}
            
            .confidence {{
                font-weight: 600;
                padding: 4px 8px;
                border-radius: 4px;
            }}
            .conf-high {{ color: #2e7d32; background: #e8f5e9; }}
            .conf-medium {{ color: #f57c00; background: #fff3e0; }}
            .conf-low {{ color: #c62828; background: #ffebee; }}
            
            .correction-select {{
                padding: 8px 12px;
                border: 2px solid #e0e0e0;
                border-radius: 6px;
                font-size: 13px;
                cursor: pointer;
                min-width: 140px;
            }}
            .correction-select:hover {{ border-color: #667eea; }}
            .correction-select.corrected {{ 
                border-color: #4caf50; 
                background: #e8f5e9;
            }}
            
            .save-btn {{
                background: #667eea;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 6px;
                cursor: pointer;
                font-size: 13px;
                font-weight: 600;
            }}
            .save-btn:hover {{ background: #5a6fd6; }}
            .save-btn:disabled {{ 
                background: #ccc; 
                cursor: not-allowed;
            }}
            
            .saved-badge {{
                display: inline-block;
                background: #4caf50;
                color: white;
                padding: 4px 10px;
                border-radius: 4px;
                font-size: 11px;
                font-weight: 600;
            }}
            
            .toast {{
                position: fixed;
                bottom: 20px;
                right: 20px;
                background: #333;
                color: white;
                padding: 15px 25px;
                border-radius: 8px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.3);
                display: none;
                z-index: 1000;
            }}
            .toast.success {{ background: #4caf50; }}
            .toast.error {{ background: #f44336; }}
            
            .feedback-info {{
                background: #e3f2fd;
                padding: 15px 20px;
                border-radius: 8px;
                margin-bottom: 20px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            .feedback-info.pending {{
                background: #fff3e0;
            }}
            .feedback-info.ready {{
                background: #e8f5e9;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🤖 Bot ML Training Dashboard</h1>
                <div class="stats-bar">
                    <div class="stat-box">
                        <div class="stat-value">{stats['total_classifications']}</div>
                        <div class="stat-label">Classificazioni Totali</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-value">{stats['fallback_rate']*100:.1f}%</div>
                        <div class="stat-label">Fallback Rate</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-value">{feedback_stats['pending']}</div>
                        <div class="stat-label">Feedback Pending</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-value">{feedback_stats['used']}</div>
                        <div class="stat-label">Feedback Usati</div>
                    </div>
                </div>
            </div>
            
            <!-- Info Modello -->
            <div class="card" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; margin-bottom: 20px;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <h3 style="margin: 0 0 5px 0;">🤖 Modello ML Attuale</h3>
                        <p style="margin: 0; opacity: 0.9;">
                            {'📅 Ultimo aggiornamento: ' + datetime.fromtimestamp(os.path.getmtime('intent_classifier_model.pkl')).strftime('%d/%m/%Y %H:%M') if os.path.exists('intent_classifier_model.pkl') else '⚠️ Modello non trovato'}
                        </p>
                    </div>
                    <div style="text-align: right;">
                        <div style="font-size: 24px; font-weight: bold;">
                            {len([f for f in os.listdir('training/backups') if f.startswith('model_backup_')]) if os.path.exists('training/backups') else 0}
                        </div>
                        <div style="font-size: 12px; opacity: 0.8;">Backup disponibili</div>
                    </div>
                </div>
            </div>
            
            <div class="feedback-info {{'ready' if feedback_stats['pending'] >= 10 else 'pending'}}">
                <div>
                    <strong>🔄 Retraining Automatico:</strong>
                    {'Pronto per retraining!' if feedback_stats['pending'] >= 10 else f'Reservi altri {10 - feedback_stats["pending"]} feedback per il retraining automatico'}
                    <br><small style="opacity: 0.7;">Controllato ogni 1 ora automaticamente</small>
                </div>
                <div>
                    <span style="font-size: 12px; opacity: 0.8; margin-right: 15px;">
                        Min: 10 | Attuali: {feedback_stats['pending']}
                    </span>
                    {'<button class="save-btn" onclick="forceRetrain()">🚀 Forza Retraining</button>' if feedback_stats['pending'] >= 10 else ''}
                </div>
            </div>
            
            <div class="filters">
                <label>🔍 Filtra per intent:</label>
                <select id="intentFilter" onchange="filterTable()">
                    <option value="">Tutti</option>
                    {''.join([f'<option value="{intent}">{intent}</option>' for intent in available_intents])}
                </select>
                
                <label>📊 Confidence:</label>
                <select id="confFilter" onchange="filterTable()">
                    <option value="">Tutte</option>
                    <option value="high">Alta (≥0.85)</option>
                    <option value="medium">Media (0.70-0.85)</option>
                    <option value="low">Bassa (<0.70)</option>
                </select>
                
                <label>🔎 Cerca:</label>
                <input type="text" id="searchFilter" placeholder="Cerca nel testo..." onkeyup="filterTable()">
            </div>
            
            <div class="messages-table">
                <table id="messagesTable">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Messaggio</th>
                            <th>Intent Predetto</th>
                            <th>Confidence</th>
                            <th>Correggi a...</th>
                            <th>Azione</th>
                        </tr>
                    </thead>
                    <tbody>
    """
    
    for case in cases:
        conf = case['confidence']
        conf_class = 'conf-high' if conf >= 0.85 else 'conf-medium' if conf >= 0.70 else 'conf-low'
        intent_class = f"intent-{case['intent']}"
        
        html += f"""
                        <tr data-intent="{case['intent']}" data-confidence="{conf}">
                            <td>#{case['id']}</td>
                            <td class="message-text">{case['text'][:100]}{'...' if len(case['text']) > 100 else ''}</td>
                            <td><span class="intent-badge {intent_class}">{case['intent']}</span></td>
                            <td><span class="confidence {conf_class}">{conf:.2f}</span></td>
                            <td>
                                <select class="correction-select" id="select-{case['id']}" onchange="enableSave({case['id']})">
                                    <option value="">-- Seleziona --</option>
                                    {''.join([f'<option value="{intent}">{intent}</option>' for intent in available_intents if intent != case['intent']])}
                                </select>
                            </td>
                            <td>
                                <button class="save-btn" id="btn-{case['id']}" onclick='saveCorrection({case['id']}, {json.dumps(case["text"], ensure_ascii=False)}, {json.dumps(case["intent"])})' disabled>
                                    Salva
                                </button>
                            </td>
                        </tr>
        """
    
    html += f"""
                    </tbody>
                </table>
            </div>
            
            <div class="card" style="margin-top: 20px; background: #f8f9fa;">
                <h3>💾 Modello ML</h3>
                <p>
                    <a href="/admin/download-model?token={auth_token}" class="save-btn" style="text-decoration: none; display: inline-block;">
                        📥 Scarica Modello Aggiornato (.pkl)
                    </a>
                </p>
                <small style="color: #666;">
                    Scarica il file <code>intent_classifier_model.pkl</code> per backup locale o test.
                </small>
            </div>
        </div>
        
        <div class="toast" id="toast"></div>
        
        <script>
            function enableSave(id) {{
                const select = document.getElementById('select-' + id);
                const btn = document.getElementById('btn-' + id);
                btn.disabled = select.value === '';
                if (select.value !== '') {{
                    select.classList.add('corrected');
                }} else {{
                    select.classList.remove('corrected');
                }}
            }}
            
            // Estrai token dall'URL
            const urlParams = new URLSearchParams(window.location.search);
            const authToken = urlParams.get('token');
            
            async function saveCorrection(id, text, predictedIntent) {{
                const select = document.getElementById('select-' + id);
                const correctIntent = select.value;
                const btn = document.getElementById('btn-' + id);
                
                if (!correctIntent) return;
                
                btn.disabled = true;
                btn.textContent = 'Salvataggio...';
                
                try {{
                    const response = await fetch('/admin/api/correct?token=' + authToken, {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{
                            text: text,
                            predicted_intent: predictedIntent,
                            correct_intent: correctIntent
                        }})
                    }});
                    
                    const result = await response.json();
                    
                    if (result.success) {{
                        showToast('✅ Correzione salvata!', 'success');
                        btn.outerHTML = '<span class="saved-badge">✓ Salvato</span>';
                        select.disabled = true;
                        // Aggiorna contatore feedback
                        updateFeedbackCounter(1);
                    }} else {{
                        showToast('❌ Errore: ' + result.message, 'error');
                        btn.disabled = false;
                        btn.textContent = 'Salva';
                    }}
                }} catch (e) {{
                    showToast('❌ Errore di rete', 'error');
                    btn.disabled = false;
                    btn.textContent = 'Salva';
                }}
            }}
            
            function showToast(message, type) {{
                const toast = document.getElementById('toast');
                toast.textContent = message;
                toast.className = 'toast ' + type;
                toast.style.display = 'block';
                setTimeout(() => {{ toast.style.display = 'none'; }}, 3000);
            }}
            
            function updateFeedbackCounter(increment) {{
                // Aggiorna contatore pending
                const pendingEl = document.querySelector('.stat-box:nth-child(3) .stat-value');
                let newCount = 0;
                if (pendingEl) {{
                    const current = parseInt(pendingEl.textContent);
                    newCount = current + increment;
                    pendingEl.textContent = newCount;
                }}
                
                // Aggiorna testo retraining
                const retrainInfo = document.querySelector('.feedback-info div:first-child');
                if (retrainInfo && newCount > 0) {{
                    if (newCount >= 10) {{
                        retrainInfo.innerHTML = '<strong>🔄 Retraining Automatico:</strong> Pronto per retraining!<br><small style="opacity: 0.7;">Controllato ogni 1 ora automaticamente</small>';
                        document.querySelector('.feedback-info').className = 'feedback-info ready';
                        // Aggiungi bottone se non c'è
                        if (!document.querySelector('.feedback-info button')) {{
                            const btnDiv = document.querySelector('.feedback-info div:last-child');
                            const span = btnDiv.querySelector('span');
                            if (span) span.insertAdjacentHTML('beforebegin', '<button class="save-btn" onclick="forceRetrain()">🚀 Forza Retraining</button>');
                        }}
                    }} else {{
                        retrainInfo.innerHTML = '<strong>🔄 Retraining Automatico:</strong> Reservi altri ' + (10 - newCount) + ' feedback per il retraining automatico<br><small style="opacity: 0.7;">Controllato ogni 1 ora automaticamente</small>';
                    }}
                }}
            }}
            
            function filterTable() {{
                const intentFilter = document.getElementById('intentFilter').value;
                const confFilter = document.getElementById('confFilter').value;
                const searchFilter = document.getElementById('searchFilter').value.toLowerCase();
                
                const rows = document.querySelectorAll('#messagesTable tbody tr');
                
                rows.forEach(row => {{
                    const intent = row.getAttribute('data-intent');
                    const confidence = parseFloat(row.getAttribute('data-confidence'));
                    const text = row.querySelector('.message-text').textContent.toLowerCase();
                    
                    let show = true;
                    
                    if (intentFilter && intent !== intentFilter) show = false;
                    
                    if (confFilter) {{
                        if (confFilter === 'high' && confidence < 0.85) show = false;
                        if (confFilter === 'medium' && (confidence < 0.70 || confidence >= 0.85)) show = false;
                        if (confFilter === 'low' && confidence >= 0.70) show = false;
                    }}
                    
                    if (searchFilter && !text.includes(searchFilter)) show = false;
                    
                    row.style.display = show ? '' : 'none';
                }});
            }}
            
            async function forceRetrain() {{
                if (!confirm('🚀 Vuoi forzare il retraining ora?\\n\\nQuesto può richiedere alcuni secondi.')) return;
                
                showToast('🔄 Avvio retraining...', 'success');
                
                try {{
                    const response = await fetch('/admin/api/retrain?token=' + authToken, {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}}
                    }});
                    
                    const result = await response.json();
                    
                    if (result.success) {{
                        showToast(`✅ Retraining completato! Accuracy: ${{(result.accuracy * 100).toFixed(1)}}%`, 'success');
                        setTimeout(() => location.reload(), 2000);
                    }} else {{
                        showToast('❌ ' + result.message, 'error');
                    }}
                }} catch (e) {{
                    showToast('❌ Errore di rete', 'error');
                }}
            }}
        </script>
    </body>
    </html>
    """
    
    return html

@app.route('/admin/api/correct', methods=['POST'])
def admin_api_correct():
    """API per salvare correzione da dashboard"""
    auth_token = request.args.get('token')
    if auth_token != os.environ.get('ADMIN_TOKEN', 'S4all'):
        return {"success": False, "message": "Unauthorized"}, 401
    
    try:
        data = request.get_json()
        text = data.get('text')
        predicted_intent = data.get('predicted_intent')
        correct_intent = data.get('correct_intent')
        
        if not all([text, predicted_intent, correct_intent]):
            return {"success": False, "message": "Dati mancanti"}, 400
        
        success = db.save_classification_feedback(
            original_text=text,
            predicted_intent=predicted_intent,
            correct_intent=correct_intent
        )
        
        if success:
            return {"success": True, "message": "Correzione salvata"}
        else:
            return {"success": False, "message": "Errore database"}, 500
            
    except Exception as e:
        logger.error(f"❌ Errore API correct: {e}")
        return {"success": False, "message": str(e)}, 500

@app.route('/admin/download-model', methods=['GET'])
def admin_download_model():
    """Scarica il modello ML aggiornato"""
    auth_token = request.args.get('token')
    if auth_token != os.environ.get('ADMIN_TOKEN', 'S4all'):
        return {"error": "Unauthorized"}, 401
    
    model_path = 'intent_classifier_model.pkl'
    
    if not os.path.exists(model_path):
        return {"error": "Modello non trovato"}, 404
    
    from flask import send_file
    return send_file(
        model_path,
        mimetype='application/octet-stream',
        as_attachment=True,
        download_name=f'intent_classifier_model_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pkl'
    )

@app.route('/admin/api/retrain', methods=['POST'])
def admin_api_retrain():
    """API per forzare retraining manuale dalla dashboard"""
    auth_token = request.args.get('token')
    if auth_token != os.environ.get('ADMIN_TOKEN', 'S4all'):
        return {"success": False, "message": "Unauthorized"}, 401
    
    try:
        from feedback_handler import get_retraining_status, trigger_retraining
        
        status = get_retraining_status()
        if not status['can_retrain']:
            return {
                "success": False, 
                "message": f"Feedback insufficienti. Hai {status['feedback_pending']}, servono 10."
            }, 400
        
        result = trigger_retraining()
        return result
        
    except Exception as e:
        logger.error(f"❌ Errore API retrain: {e}")
        return {"success": False, "message": str(e)}, 500

@app.route('/admin/export', methods=['GET'])
def admin_export():
    """Export low confidence cases per retraining"""
    auth_token = request.args.get('token')
    if auth_token != os.environ.get('ADMIN_TOKEN', 'S4all'):
        return {"error": "Unauthorized"}, 401
    
    from enhanced_logging import classification_logger
    
    output_file = classification_logger.export_for_retraining()
    
    if output_file and os.path.exists(output_file):
        with open(output_file, 'r') as f:
            data = json.load(f)
        return {"exported": len(data), "cases": data}, 200
    
    return {"error": "Export failed"}, 500

@app.route('/admin/intent/<intent_name>', methods=['GET'])
def admin_intent_detail(intent_name):
    """Analisi dettagliata di un intent specifico"""
    auth_token = request.args.get('token')
    if auth_token != os.environ.get('ADMIN_TOKEN', 'S4all'):
        return {"error": "Unauthorized"}, 401
    
    from enhanced_logging import classification_logger
    
    # Ottieni distribuzione confidence
    distribution = classification_logger.get_confidence_distribution(intent_name)
    
    if not distribution:
        return f"<h1>Intent '{intent_name}' non trovato</h1>", 404
    
    # Ottieni tutti i casi per questo intent
    cases = classification_logger.get_cases_by_intent(intent_name, limit=100)
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Intent: {intent_name}</title>
        <style>
            body {{ font-family: Arial; margin: 20px; background: #f5f5f5; }}
            .card {{ background: white; padding: 20px; margin: 10px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            .metric {{ display: inline-block; margin: 10px 20px; }}
            .metric-value {{ font-size: 32px; font-weight: bold; color: #2196F3; }}
            .metric-label {{ font-size: 14px; color: #666; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }}
            th {{ background: #2196F3; color: white; }}
            .very-low {{ background: #ffebee; color: #c62828; }}
            .low {{ background: #fff3e0; color: #e65100; }}
            .medium {{ background: #fff9c4; color: #f57f17; }}
            .high {{ background: #e8f5e9; color: #2e7d32; }}
            .back-btn {{ display: inline-block; padding: 10px 20px; background: #2196F3; color: white; text-decoration: none; border-radius: 5px; margin-bottom: 20px; }}
        </style>
    </head>
    <body>
        <a href="/admin/stats?token={auth_token}" class="back-btn">← Torna al Dashboard</a>
        
        <h1>📊 Intent: <code>{intent_name}</code></h1>
        
        <div class="card">
            <h2>📈 Distribuzione Confidence</h2>
            <div class="metric">
                <div class="metric-value">{distribution['total']}</div>
                <div class="metric-label">Totale Casi</div>
            </div>
            <div class="metric">
                <div class="metric-value">{distribution['avg_confidence']:.2f}</div>
                <div class="metric-label">Media Confidence</div>
            </div>
            <div class="metric">
                <div class="metric-value">{distribution['min_confidence']:.2f}</div>
                <div class="metric-label">Min</div>
            </div>
            <div class="metric">
                <div class="metric-value">{distribution['max_confidence']:.2f}</div>
                <div class="metric-label">Max</div>
            </div>
        </div>
        
        <div class="card">
            <h2>📊 Breakdown per Livello</h2>
            <table>
                <tr>
                    <th>Livello</th>
                    <th>Range Confidence</th>
                    <th>Conteggio</th>
                    <th>Percentuale</th>
                </tr>
                <tr class="high">
                    <td><strong>Alta</strong></td>
                    <td>≥ 0.85</td>
                    <td>{distribution['high']}</td>
                    <td>{(distribution['high']/distribution['total']*100):.1f}%</td>
                </tr>
                <tr class="medium">
                    <td><strong>Media</strong></td>
                    <td>0.70 - 0.85</td>
                    <td>{distribution['medium']}</td>
                    <td>{(distribution['medium']/distribution['total']*100):.1f}%</td>
                </tr>
                <tr class="low">
                    <td><strong>Bassa</strong></td>
                    <td>0.50 - 0.70</td>
                    <td>{distribution['low']}</td>
                    <td>{(distribution['low']/distribution['total']*100):.1f}%</td>
                </tr>
                <tr class="very-low">
                    <td><strong>Molto Bassa</strong></td>
                    <td>&lt; 0.50</td>
                    <td>{distribution['very_low']}</td>
                    <td>{(distribution['very_low']/distribution['total']*100):.1f}%</td>
                </tr>
            </table>
        </div>
        
        <div class="card">
            <h2>💬 Tutti i Messaggi ({len(cases)} totali)</h2>
            <table>
                <tr>
                    <th>Messaggio</th>
                    <th>Confidence</th>
                    <th>Timestamp</th>
                </tr>
    """
    
    for case in cases:
        conf = case['confidence']
        conf_class = 'high' if conf >= 0.85 else 'medium' if conf >= 0.70 else 'low' if conf >= 0.50 else 'very-low'
        
        html += f"""
                <tr class="{conf_class}">
                    <td>{case['text']}</td>
                    <td><strong>{conf:.2f}</strong></td>
                    <td>{case['timestamp'][:19]}</td>
                </tr>
        """
    
    html += f"""
            </table>
        </div>
        
        <div class="card">
            <h3>📥 Export Dati</h3>
            <p><a href="/admin/export_intent/{intent_name}?token={auth_token}">📥 Download JSON per questo intent</a></p>
        </div>
    </body>
    </html>
    """
    
    return html

@app.route('/admin/export_intent/<intent_name>', methods=['GET'])
@app.route('/admin/export_intent/<intent_name>', methods=['GET'])
def admin_export_intent(intent_name):
    """
    Export JSON completo per un intent specifico
    Include campo 'correct_intent' vuoto per correzioni manuali
    """
    auth_token = request.args.get('token')
    if auth_token != os.environ.get('ADMIN_TOKEN', 'S4all'):
        return {"error": "Unauthorized"}, 401
    
    from enhanced_logging import classification_logger
    
    # Usa il nuovo metodo che include correct_intent
    export_data = classification_logger.export_intent_for_correction(intent_name, limit=1000)
    
    if "error" in export_data:
        return export_data, 404
    
    # Ritorna JSON con headers per download
    response = make_response(json.dumps(export_data, indent=2, ensure_ascii=False))
    response.headers['Content-Type'] = 'application/json; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename=intent_{intent_name}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    
    return response

@app.route('/admin/trends', methods=['GET'])
def admin_trends():
    """Dashboard trend storici mensili"""
    auth_token = request.args.get('token')
    if auth_token != os.environ.get('ADMIN_TOKEN', 'S4all'):
        return {"error": "Unauthorized"}, 401
    
    months = int(request.args.get('months', 6))
    trends = db.get_monthly_trends(months)
    
    if not trends:
        return "<h1>Nessun trend storico disponibile</h1>", 404
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Trend Storici</title>
        <style>
            body {{ font-family: Arial; margin: 20px; background: #f5f5f5; }}
            .card {{ background: white; padding: 20px; margin: 10px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            .back-btn {{ display: inline-block; padding: 10px 20px; background: #2196F3; color: white; text-decoration: none; border-radius: 5px; margin-bottom: 20px; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }}
            th {{ background: #2196F3; color: white; }}
            .chart {{ margin: 20px 0; }}
        </style>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    </head>
    <body>
        <a href="/admin/stats?token={auth_token}" class="back-btn">← Torna al Dashboard</a>
        
        <h1>📈 Trend Storici - Ultimi {months} Mesi</h1>
        
        <div class="card">
            <canvas id="trendChart" width="400" height="150"></canvas>
        </div>
        
        <div class="card">
            <h2>📊 Dettaglio Mensile</h2>
            <table>
                <tr>
                    <th>Mese</th>
                    <th>Totale</th>
                    <th>Fallback</th>
                    <th>Fallback Rate</th>
                    <th>Top 3 Intent</th>
                </tr>
    """
    
    # Dati per il grafico
    labels = []
    fallback_rates = []
    totals = []
    
    for trend in reversed(trends):  # Ordine cronologico per grafico
        labels.append(trend['year_month'])
        fallback_rates.append(float(trend['fallback_rate']))
        totals.append(trend['total'])
        
        # Top 3 intent per questo mese
        by_intent = trend['by_intent']
        top_intents = sorted(by_intent.items(), key=lambda x: x[1]['count'], reverse=True)[:3]
        top_intents_str = ", ".join([f"{intent} ({data['count']})" for intent, data in top_intents])
        
        html += f"""
                <tr>
                    <td><strong>{trend['year_month']}</strong></td>
                    <td>{trend['total']}</td>
                    <td>{trend['fallback_count']}</td>
                    <td>{trend['fallback_rate']}%</td>
                    <td>{top_intents_str}</td>
                </tr>
        """
    
    html += f"""
            </table>
        </div>
        
        <script>
            const ctx = document.getElementById('trendChart').getContext('2d');
            const chart = new Chart(ctx, {{
                type: 'line',
                data: {{
                    labels: {json.dumps(labels)},
                    datasets: [
                        {{
                            label: 'Fallback Rate (%)',
                            data: {json.dumps(fallback_rates)},
                            borderColor: 'rgb(255, 99, 132)',
                            backgroundColor: 'rgba(255, 99, 132, 0.1)',
                            yAxisID: 'y'
                        }},
                        {{
                            label: 'Totale Classificazioni',
                            data: {json.dumps(totals)},
                            borderColor: 'rgb(54, 162, 235)',
                            backgroundColor: 'rgba(54, 162, 235, 0.1)',
                            yAxisID: 'y1'
                        }}
                    ]
                }},
                options: {{
                    responsive: true,
                    interaction: {{
                        mode: 'index',
                        intersect: false
                    }},
                    scales: {{
                        y: {{
                            type: 'linear',
                            display: true,
                            position: 'left',
                            title: {{
                                display: true,
                                text: 'Fallback Rate (%)'
                            }}
                        }},
                        y1: {{
                            type: 'linear',
                            display: true,
                            position: 'right',
                            title: {{
                                display: true,
                                text: 'Totale'
                            }},
                            grid: {{
                                drawOnChartArea: false
                            }}
                        }}
                    }}
                }}
            }});
        </script>
    </body>
    </html>
    """
    
    return html

# End main.py
