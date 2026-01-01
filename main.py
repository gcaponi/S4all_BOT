# Genero il file main.py COMPLETO con la correzione per i gruppi di discussione

code = '''import os
import json
import logging
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import secrets
import re
import requests
from bs4 import BeautifulSoup
from difflib import SequenceMatcher
import asyncio
from threading import Thread

# ====
# Configurazione logging
# ====

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ====
# Configurazione da variabili d'ambiente
# ====

BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_CHAT_ID = int(os.environ.get('ADMIN_CHAT_ID', 0))
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '')
PORT = int(os.environ.get('PORT', 10000))

# ====
# KEYWORDS METODI DI PAGAMENTO
# ====

PAYMENT_KEYWORDS = [
    'contanti', 'carta', 'bancomat', 'bonifico', 'paypal',
    'satispay', 'postepay', 'pos', 'wallet', 'ricarica',
    'usdt', 'crypto', 'cripto', 'bitcoin', 'bit', 'btc', 'eth', 'usdc'
]

# ====
# File per salvare dati persistenti
# ====

AUTHORIZED_USERS_FILE = 'authorized_users.json'
ACCESS_CODE_FILE = 'access_code.json'
FAQ_FILE = 'faq.json'

# ====
# FAQ URL
# ====

PASTE_URL = "https://justpaste.it/faq_4all"

# ====
# Soglia per il fuzzy matching
# ====

FUZZY_THRESHOLD = 0.6

# ====
# Flask app
# ====

app = Flask(__name__)
bot_application = None
bot_initialized = False

# ====
# FUNZIONI SUPERVISIONE ORDINI
# ====

def has_payment_method(text: str) -> bool:
    """Controlla se il messaggio contiene un metodo di pagamento"""
    text_lower = text.lower()
    for keyword in PAYMENT_KEYWORDS:
        if keyword in text_lower:
            logger.info(f"✅ Metodo pagamento trovato: {keyword}")
            return True
    return False

def looks_like_order(text: str) -> bool:
    """Controlla se il messaggio sembra un ordine (contiene numeri/prezzi)"""
    # Cerca numeri con simboli di valuta o standalone
    has_numbers = bool(re.search(r'\\d+', text))
    has_currency = bool(re.search(r'[€$£¥₿]', text))
    is_long_enough = len(text) >= 10

    return (has_numbers or has_currency) and is_long_enough

async def is_bot_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    """Verifica se il bot è admin nella chat"""
    try:
        bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
        is_admin = bot_member.status in ['administrator', 'creator']
        logger.info(f"🔍 Bot admin check in chat {chat_id}: {is_admin} (status: {bot_member.status})")
        return is_admin
    except Exception as e:
        logger.error(f"Errore controllo admin: {e}")
        return False

# ====
#  FAQ FETCH FUNCTIONS
# ====

def fetch_markdown_from_html(url: str) -> str:
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    content = soup.select_one("#articleContent")
    if content is None:
        raise RuntimeError("Contenuto principale non trovato")
    text = content.get_text("\\n")
    return text.strip()

def parse_faq(markdown: str) -> list:
    pattern = r"^##\\s+(.*?)\\n(.*?)(?=\\n##\\s+|\\Z)"
    matches = re.findall(pattern, markdown, flags=re.S | re.M)
    if not matches:
        raise RuntimeError("Formato non valido: nessuna domanda trovata. Usa solo titoli '##'.")
    faq = []
    for domanda, risposta in matches:
        risposta = risposta.strip()
        faq.append({"domanda": domanda.strip(), "risposta": risposta})
    return faq

def write_faq_json(faq: list, filename: str):
    faq_object = {"faq": faq}
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(faq_object, f, ensure_ascii=False, indent=2)

def update_faq_from_web():
    try:
        markdown = fetch_markdown_from_html(PASTE_URL)
        faq = parse_faq(markdown)
        write_faq_json(faq, FAQ_FILE)
        logger.info(f"FAQ aggiornate correttamente: {len(faq)} domande")
        return True
    except Exception as e:
        logger.error(f"Errore aggiornamento FAQ: {e}")
        return False

# ====
# PERSISTENT DATA FUNCTIONS
# ====

def load_json_file(filename, default=None):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default if default is not None else {}
    except json.JSONDecodeError:
        logger.error(f"Errore nel leggere {filename}")
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
    authorized_users = load_authorized_users()
    return str(user_id) in authorized_users

def authorize_user(user_id, first_name=None, last_name=None, username=None):
    authorized_users = load_authorized_users()
    user_id_str = str(user_id)

    if user_id_str not in authorized_users:
        full_name = f"{first_name or ''} {last_name or ''}".strip()
        if not full_name:
            full_name = "Sconosciuto"

        authorized_users[user_id_str] = {
            "id": user_id,
            "name": full_name,
            "username": username
        }
        save_authorized_users(authorized_users)
        return True
    return False

def get_bot_username():
    return getattr(get_bot_username, 'username', 'tuobot')

# ====
# FUZZY MATCHING FUNCTIONS
# ====

def calculate_similarity(text1: str, text2: str) -> float:
    return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

def normalize_text(text: str) -> str:
    text = re.sub(r'[^\\w\\s]', '', text)
    text = re.sub(r'\\s+', ' ', text)
    return text.strip().lower()

def extract_keywords(text: str) -> list:
    normalized = normalize_text(text)
    words = normalized.split()
    stop_words = {'che', 'sono', 'come', 'dove', 'quando', 'quale', 'quali',
                  'del', 'della', 'dei', 'delle', 'con', 'per', 'una', 'uno'}
    keywords = [w for w in words if len(w) > 3 and w not in stop_words]
    return keywords

def fuzzy_search_faq(user_message: str, faq_list: list) -> dict:
    user_normalized = normalize_text(user_message)
    user_keywords = extract_keywords(user_message)

    best_match = None
    best_score = 0
    match_method = None

    for item in faq_list:
        domanda = item["domanda"]
        domanda_normalized = normalize_text(domanda)

        if domanda_normalized in user_normalized or user_normalized in domanda_normalized:
            return {
                'match': True,
                'item': item,
                'score': 1.0,
                'method': 'exact'
            }

        similarity = calculate_similarity(user_normalized, domanda_normalized)
        if similarity > best_score:
            best_score = similarity
            best_match = item
            match_method = 'similarity'

        if user_keywords:
            domanda_keywords = extract_keywords(domanda)
            matched_keywords = sum(1 for kw in user_keywords if any(
                calculate_similarity(kw, dk) > 0.8 for dk in domanda_keywords
            ))

            keyword_score = matched_keywords / len(user_keywords)
            keyword_score = keyword_score * 1.2 if matched_keywords > 1 else keyword_score

            if keyword_score > best_score:
                best_score = keyword_score
                best_match = item
                match_method = 'keywords'

    if best_score >= FUZZY_THRESHOLD:
        return {
            'match': True,
            'item': best_match,
            'score': best_score,
            'method': match_method
        }

    return {'match': False, 'item': None, 'score': best_score, 'method': None}

# ====
# BOT HANDLERS
# ====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    if context.args:
        provided_code = context.args[0]
        correct_code = load_access_code()

        if provided_code == correct_code:
            was_new = authorize_user(user_id, user.first_name, user.last_name, user.username)

            if was_new:
                await update.message.reply_text(
                    "✅ Sei stato autorizzato con successo!\\n\\n"
                    "Ora puoi usare il bot liberamente. Scrivi la tua domanda o usa /help per vedere le categorie FAQ."
                )

                if ADMIN_CHAT_ID:
                    admin_msg = (
                        f"✅ <b>Nuovo utente autorizzato tramite link!</b>\\n\\n"
                        f"👤 Nome: {user.first_name or ''} {user.last_name or ''}\\n"
                        f"🆔 Username: @{user.username or 'N/A'}\\n"
                        f"🔢 Chat ID: <code>{user_id}</code>"
                    )
                    try:
                        await context.bot.send_message(
                            chat_id=ADMIN_CHAT_ID,
                            text=admin_msg,
                            parse_mode='HTML'
                        )
                    except Exception as e:
                        logger.error(f"Errore invio notifica admin: {e}")
            else:
                await update.message.reply_text(
                    "✅ Sei già autorizzato!\\n\\n"
                    "Scrivi la tua domanda o usa /help per vedere le categorie FAQ."
                )
            return
        else:
            await update.message.reply_text(
                "❌ Codice di accesso non valido.\\n\\n"
                "Contatta l'amministratore per ottenere il link corretto."
            )
            return

    if is_user_authorized(user_id):
        await update.message.reply_text(
            f"👋 Ciao {user.first_name}!\\n\\n"
            "Sono il bot FAQ con ricerca intelligente. Scrivi la tua domanda anche con errori di battitura!\\n\\n"
            "💡 Usa /help per vedere tutte le categorie disponibili."
        )
    else:
        await update.message.reply_text(
            "❌ Non sei autorizzato ad usare questo bot.\\n\\n"
            "Contatta l'amministratore per ottenere il link di accesso."
        )

        if ADMIN_CHAT_ID:
            admin_msg = (
                f"⚠️ <b>Tentativo di accesso non autorizzato!</b>\\n\\n"
                f"👤 Nome: {user.first_name or ''} {user.last_name or ''}\\n"
                f"🆔 Username: @{user.username or 'N/A'}\\n"
                f"🔢 Chat ID: <code>{user_id}</code>\\n"
                f"💬 Messaggio: /start"
            )
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=admin_msg,
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"Errore invio notifica admin: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_user_authorized(user_id):
        await update.message.reply_text(
            "❌ Non sei autorizzato ad usare questo bot.\\n\\n"
            "Contatta l'amministratore per ottenere il link di accesso."
        )
        return

    faq_data = load_faq()
    faq_list = faq_data.get("faq", []) if faq_data else []

    if not faq_list:
        await update.message.reply_text(
            "❌ Nessuna FAQ disponibile al momento.\\n\\n"
            "Contatta l'amministratore."
        )
        return

    help_text = "📚 <b>Domande FAQ disponibili:</b>\\n\\n"

    for i, item in enumerate(faq_list, 1):
        help_text += f"{i}. {item['domanda']}\\n"

    help_text += "\\n💡 <b>Ricerca intelligente attiva!</b>\\n"
    help_text += "Scrivi anche con errori di battitura, il bot capirà! 🎯"

    await update.message.reply_text(help_text, parse_mode='HTML')

async def genera_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Solo l'amministratore può usare questo comando.")
        return

    access_code = load_access_code()
    bot_username = get_bot_username.username
    link = f"https://t.me/{bot_username}?start={access_code}"
    authorized_count = len(load_authorized_users())

    message = (
        f"🔗 <b>Link di accesso universale:</b>\\n\\n"
        f"<code>{link}</code>\\n\\n"
        f"📋 <b>Istruzioni:</b>\\n"
        f"• Condividi questo link con i tuoi contatti fidati\\n"
        f"• Chi clicca il link viene autorizzato automaticamente\\n"
        f"• Il link è valido per sempre (finché non lo cambi)\\n\\n"
        f"👥 Utenti già autorizzati: {authorized_count}\\n\\n"
        f"🔄 Usa /cambia_codice per generare un nuovo link"
    )

    await update.message.reply_text(message, parse_mode='HTML')

async def cambia_codice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Solo l'amministratore può usare questo comando.")
        return

    new_code = secrets.token_urlsafe(12)
    save_access_code(new_code)

    bot_username = get_bot_username.username
    new_link = f"https://t.me/{bot_username}?start={new_code}"

    message = (
        f"✅ <b>Nuovo codice generato!</b>\\n\\n"
        f"🔗 <b>Nuovo link:</b>\\n"
        f"<code>{new_link}</code>\\n\\n"
        f"⚠️ <b>Attenzione:</b> Il vecchio link non funziona più!\\n"
        f"Gli utenti già autorizzati possono continuare ad usare il bot."
    )

    await update.message.reply_text(message, parse_mode='HTML')

async def lista_autorizzati_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Solo l'amministratore può usare questo comando.")
        return

    authorized_users = load_authorized_users()

    if not authorized_users:
        await update.message.reply_text("📋 Nessun utente autorizzato al momento.")
        return

    message = f"👥 <b>Utenti autorizzati ({len(authorized_users)}):</b>\\n\\n"

    for i, (user_id_str, user_data) in enumerate(authorized_users.items(), 1):
        name = user_data.get('name', 'Sconosciuto')
        username = user_data.get('username')
        user_id_display = user_data.get('id', user_id_str)

        username_text = f"@{username}" if username else "N/A"
        message += f"{i}. <b>{name}</b>\\n"
        message += f"   👤 Username: {username_text}\\n"
        message += f"   🔢 ID: <code>{user_id_display}</code>\\n\\n"

    message += f"💡 Usa /revoca seguito dal Chat ID per rimuovere un utente"

    await update.message.reply_text(message, parse_mode='HTML')

async def revoca_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Solo l'amministratore può usare questo comando.")
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Uso: /revoca <chat_id>\\n\\n"
            "Esempio: /revoca 123456789\\n"
            "Usa /lista_autorizzati per vedere i Chat ID"
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Chat ID non valido. Deve essere un numero.")
        return

    authorized_users = load_authorized_users()

    if str(target_id) in authorized_users:
        del authorized_users[str(target_id)]
        save_authorized_users(authorized_users)
        await update.message.reply_text(f"✅ Utente {target_id} rimosso dagli autorizzati.")
    else:
        await update.message.reply_text(f"❌ Utente {target_id} non era autorizzato.")

async def admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Comando riservato all'amministratore.")
        return

    message = (
        "👑 <b>Comandi Admin disponibili:</b>\\n\\n"
        "🔐 <b>Gestione accessi</b>\\n"
        "• /genera_link — genera link di accesso\\n"
        "• /cambia_codice — cambia il codice di accesso\\n"
        "• /lista_autorizzati — lista utenti autorizzati\\n"
        "• /revoca &lt;chat_id&gt; — rimuove un utente\\n\\n"
        "👤 <b>Comandi Utente</b>\\n"
        "• /start\\n"
        "• /help\\n\\n"
        "🎯 <b>Ricerca Fuzzy</b>\\n"
        "Il bot ora usa ricerca intelligente!\\n"
        "Soglia attuale: {:.0%}\\n\\n"
        "💡 Solo l'ADMIN può vedere questo messaggio".format(FUZZY_THRESHOLD)
    )

    await update.message.reply_text(message, parse_mode='HTML')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler per messaggi privati (FAQ)"""
    user = update.effective_user
    user_id = user.id
    message_text = update.message.text

    if not is_user_authorized(user_id):
        await update.message.reply_text(
            "❌ Non sei autorizzato ad usare questo bot.\\n\\n"
            "Contatta l'amministratore per ottenere il link di accesso."
        )

        if ADMIN_CHAT_ID:
            admin_msg = (
                f"⚠️ <b>Tentativo di accesso non autorizzato!</b>\\n\\n"
                f"👤 Nome: {user.first_name or ''} {user.last_name or ''}\\n"
                f"🆔 Username: @{user.username or 'N/A'}\\n"
                f"🔢 Chat ID: <code>{user_id}</code>\\n"
                f"💬 Messaggio: {message_text[:100]}"
            )
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=admin_msg,
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"Errore invio notifica admin: {e}")
        return

    faq = load_faq()

    if not faq:
        await update.message.reply_text(
            "❌ Nessuna FAQ disponibile al momento.\\n\\n"
            "Riprova più tardi o contatta l'amministratore."
        )
        return

    faq_list = faq.get("faq", [])

    result = fuzzy_search_faq(message_text, faq_list)

    if result['match']:
        item = result['item']
        score = result['score']

        confidence_emoji = "🎯" if score > 0.9 else "✅" if score > 0.75 else "💡"

        response = f"{confidence_emoji} <b>{item['domanda']}</b>\\n\\n{item['risposta']}"

        if score < 0.9:
            response += f"\\n\\n<i>💬 Confidenza: {score:.0%}</i>"

        await update.message.reply_text(response, parse_mode='HTML')

        logger.info(f"Match trovato: {result['method']}, score: {score:.2f}, query: '{message_text}'")
    else:
        await update.message.reply_text(
            f"❓ Non ho trovato una risposta per: <i>\\"{message_text}\\"</i>\\n\\n"
            f"🔍 Ho cercato con somiglianza fino a {result['score']:.0%}\\n\\n"
            f"💡 Prova a:\\n"
            f"• Riformulare la domanda\\n"
            f"• Usare parole chiave diverse\\n"
            f"• Vedere tutte le FAQ con /help",
            parse_mode='HTML'
        )

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler per messaggi in gruppi/canali (supervisione ordini)"""
    message = update.message or update.channel_post

    if not message or not message.text:
        return

    chat = message.chat
    sender = message.from_user

    logger.info(f"📢 MESSAGGIO RICEVUTO:")
    logger.info(f"   Chat ID: {chat.id}")
    logger.info(f"   Chat Type: {chat.type}")
    logger.info(f"   Chat Title: {chat.title or 'N/A'}")
    logger.info(f"   Sender: {sender.first_name if sender else 'Channel'}")
    logger.info(f"   Text: {message.text}")

    # NUOVA LOGICA: Accetta SEMPRE messaggi da gruppi/canali
    # Non controlla più se il bot è admin, perché nei gruppi di discussione
    # collegati ai canali, il bot risulta "member" ma i messaggi arrivano comunque
    logger.info(f"✅ Procedo con l'analisi del messaggio (no controllo admin)")

    # Controlla se sembra un ordine
    if not looks_like_order(message.text):
        logger.info("⏭️ Messaggio non sembra un ordine (no numeri o troppo corto)")
        return

    logger.info("🔍 Messaggio sembra un ordine, controllo metodo pagamento...")

    # LOGICA: Se NON ha metodo pagamento, mostra pulsanti
    if has_payment_method(message.text):
        logger.info("✅ Metodo pagamento presente - ordine OK, nessun avviso")
        return

    # ORDINE SENZA METODO PAGAMENTO! Invia pulsanti di avviso
    logger.info("⚠️ ORDINE SENZA METODO PAGAMENTO! Invio pulsanti avviso...")

    keyboard = [
        [
            InlineKeyboardButton("✅ Ho specificato", callback_data=f"specified_{message.message_id}"),
            InlineKeyboardButton("❌ Devo aggiungerlo", callback_data=f"add_{message.message_id}")
        ],
        [
            InlineKeyboardButton("🚫 Non è un ordine", callback_data=f"notorder_{message.message_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        # Prepara i parametri per la risposta
        reply_params = {
            "text": "🤔 <b>Questo sembra un ordine ma non vedo il metodo di pagamento</b>\\n\\n"
                    "Hai specificato come pagherai?",
            "reply_markup": reply_markup,
            "parse_mode": 'HTML'
        }
        
        # Se il messaggio ha un message_thread_id (gruppi con topic/forum), includilo
        if hasattr(message, 'message_thread_id') and message.message_thread_id:
            reply_params['message_thread_id'] = message.message_thread_id
            logger.info(f"📌 Rispondo nel topic {message.message_thread_id}")
        
        await message.reply_text(**reply_params)
        logger.info("✅ Pulsanti avviso inviati con successo")
    except Exception as e:
        logger.error(f"❌ Errore invio pulsanti: {e}")

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler per i pulsanti inline"""
    query = update.callback_query
    await query.answer()

    data = query.data
    user = query.from_user

    if data.startswith("specified_"):
        message_id = data.split("_")[1]
        await query.edit_message_text(
            f"✅ <b>Perfetto!</b>\\n\\n"
            f"Il metodo di pagamento è stato specificato.\\n"
            f"Confermato da: {user.first_name}",
            parse_mode='HTML'
        )
        logger.info(f"✅ Utente {user.first_name} ha confermato metodo pagamento per messaggio {message_id}")

    elif data.startswith("add_"):
        message_id = data.split("_")[1]
        await query.edit_message_text(
            f"⚠️ <b>Ricorda di aggiungere il metodo di pagamento!</b>\\n\\n"
            f"Metodi accettati: carta, contanti, bonifico, PayPal, Satispay, crypto, ecc.\\n"
            f"Segnalato da: {user.first_name}",
            parse_mode='HTML'
        )
        logger.info(f"⚠️ Utente {user.first_name} deve aggiungere metodo pagamento per messaggio {message_id}")

        # Notifica admin
        if ADMIN_CHAT_ID:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"⚠️ Ordine senza metodo pagamento da {user.first_name} (@{user.username or 'N/A'})",
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"Errore notifica admin: {e}")

    elif data.startswith("notorder_"):
        message_id = data.split("_")[1]
        await query.edit_message_text(
            f"👍 <b>Ok, capito!</b>\\n\\n"
            f"Non era un ordine.\\n"
            f"Segnalato da: {user.first_name}",
            parse_mode='HTML'
        )
        logger.info(f"ℹ️ Utente {user.first_name} ha segnalato che messaggio {message_id} non è un ordine")

# ====
# BOT INITIALIZATION
# ====

def initialize_bot_sync():
    """Inizializza il bot in modo sincrono"""
    global bot_application, bot_initialized

    if bot_initialized:
        return

    try:
        logger.info("📡 Inizio inizializzazione bot...")

        # Aggiorna FAQ
        if update_faq_from_web():
            logger.info("✅ FAQ aggiornate")

        async def setup():
            global bot_application

            application = Application.builder().token(BOT_TOKEN).updater(None).build()

            bot = await application.bot.get_me()
            get_bot_username.username = bot.username
            logger.info(f"Bot username: @{bot.username}")

            # Registra handler COMANDI
            application.add_handler(CommandHandler("start", start))
            application.add_handler(CommandHandler("help", help_command))
            application.add_handler(CommandHandler("genera_link", genera_link_command))
            application.add_handler(CommandHandler("cambia_codice", cambia_codice_command))
            application.add_handler(CommandHandler("lista_autorizzati", lista_autorizzati_command))
            application.add_handler(CommandHandler("revoca", revoca_command))
            application.add_handler(CommandHandler("admin_help", admin_help_command))

            # Handler MESSAGGI - ORDINE IMPORTANTE!
            # 1. Messaggi privati (FAQ)
            application.add_handler(MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
                handle_message
            ))

            # 2. Messaggi gruppi/supergroup (supervisione ordini)
            application.add_handler(MessageHandler(
                filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
                handle_group_message
            ))

            # 3. Post nei canali (supervisione ordini)
            application.add_handler(MessageHandler(
                filters.TEXT & filters.ChatType.CHANNEL,
                handle_group_message
            ))

            # 4. Callback query (pulsanti inline)
            application.add_handler(CallbackQueryHandler(handle_callback_query))

            # Setup webhook
            if WEBHOOK_URL:
                webhook_url = f"{WEBHOOK_URL}/webhook"
                await application.bot.set_webhook(url=webhook_url)
                logger.info(f"✅ Webhook: {webhook_url}")

            await application.initialize()
            await application.start()

            logger.info("🤖 Bot pronto!")
            logger.info("📋 CONFIGURAZIONE SUPERVISIONE ORDINI:")
            logger.info(f"   • Metodi pagamento: {len(PAYMENT_KEYWORDS)} keywords")
            logger.info(f"   • Admin chat ID: {ADMIN_CHAT_ID}")
            logger.info("   • LOGICA: Avvisa solo se ordine SENZA metodo pagamento")
            logger.info("   • Il bot supervisionerà TUTTI i messaggi in canali/gruppi (no controllo admin)")

            return application

        # Crea event loop e inizializza
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        bot_application = loop.run_until_complete(setup())
        bot_initialized = True
        logger.info("✅ Inizializzazione completata")

    except Exception as e:
        logger.error(f"❌ Errore inizializzazione: {e}")
        import traceback
        traceback.print_exc()

# ====
# FLASK ROUTES
# ====

@app.route('/')
def index():
    return "🤖 Bot Telegram FAQ + Supervisione Ordini attivo! ✅", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    """Riceve gli update da Telegram"""
    global bot_initialized

    # Inizializza il bot se non già fatto
    if not bot_initialized:
        initialize_bot_sync()

    if not bot_application:
        return "Bot not ready", 503

    try:
        update = Update.de_json(request.get_json(force=True), bot_application.bot)

        # Usa il loop esistente invece di crearne uno nuovo
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Esegui l'update
        loop.run_until_complete(bot_application.process_update(update))

        return "OK", 200

    except Exception as e:
        logger.error(f"Errore webhook: {e}")
        import traceback
        traceback.print_exc()
        return "ERROR", 500

@app.route('/health')
def health():
    """Health check per UptimeRobot"""
    return "OK", 200

# ====
# Inizializza il bot in un thread separato
# ====

def start_bot_thread():
    if BOT_TOKEN and ADMIN_CHAT_ID:
        thread = Thread(target=initialize_bot_sync, daemon=True)
        thread.start()
        logger.info("🚀 Thread inizializzazione bot avviato")
    else:
        logger.error("❌ BOT_TOKEN o ADMIN_CHAT_ID mancanti")

# ====
# Avvia il thread di inizializzazione
# ====

start_bot_thread()

logger.info("🌐 Flask app pronta")

# ====
# Entry point per test locali
# ====

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False)
'''

# Salva il file
with open('main.py', 'w', encoding='utf-8') as f:
    f.write(code)

print("✅ File main.py generato con CORREZIONE per gruppi di discussione!")
print("")
print("🔧 CORREZIONE APPLICATA:")
print("   • RIMOSSO il controllo admin per gruppi/canali")
print("   • Il bot ora analizza TUTTI i messaggi ricevuti")
print("   • Funziona sia nei canali che nei gruppi di discussione collegati")
print("")
print("📋 Come funziona ora:")
print("   1. Messaggio arriva (da canale o gruppo discussione)")
print("   2. Bot analizza SEMPRE (no controllo admin)")
print("   3. Se sembra ordine SENZA metodo pagamento → mostra pulsanti")
print("   4. Se ha metodo pagamento → nessun messaggio")
