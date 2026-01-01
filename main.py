import os
import json
import logging
from flask import Flask, request
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import secrets
import re
import requests
from bs4 import BeautifulSoup
from difflib import SequenceMatcher
import asyncio
from threading import Thread

# ====
# LOGGING
# ====
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ====
# CONFIG DA AMBIENTE
# ====
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", 0))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
PORT = int(os.environ.get("PORT", 10000))

# ====
# METODI DI PAGAMENTO
# ====
PAYMENT_KEYWORDS = [
    "contanti",
    "carta",
    "bancomat",
    "bonifico",
    "paypal",
    "satispay",
    "postepay",
    "pos",
    "wallet",
    "ricarica",
    "usdt",
    "crypto",
    "cripto",
    "bitcoin",
    "bit",
    "btc",
    "eth",
    "usdc",
]

# ====
# FILE PERSISTENTI
# ====
AUTHORIZED_USERS_FILE = "authorized_users.json"
ACCESS_CODE_FILE = "access_code.json"
FAQ_FILE = "faq.json"

# ====
# FAQ SOURCE
# ====
PASTE_URL = "https://justpaste.it/faq_4all"

# ====
# PARAMETRI RICERCA FUZZY
# ====
FUZZY_THRESHOLD = 0.6

# ====
# FLASK APP
# ====
app = Flask(__name__)
bot_application = None
bot_initialized = False

# =============================================================================
# FUNZIONI SUPERVISIONE ORDINI
# =============================================================================


def has_payment_method(text: str) -> bool:
    """Ritorna True se nel testo è presente uno dei metodi di pagamento."""
    text_lower = text.lower()
    for keyword in PAYMENT_KEYWORDS:
        if keyword in text_lower:
            logger.info(f"✅ Metodo pagamento trovato: {keyword}")
            return True
    return False


def looks_like_order(text: str) -> bool:
    """
    Ritorna True se il messaggio "sembra" un ordine:
    - contiene numeri (quantità/prezzo)
    - oppure simboli valuta
    - ed è abbastanza lungo
    """
    has_numbers = bool(re.search(r"\d+", text))
    has_currency = bool(re.search(r"[€$£¥₿]", text))
    is_long_enough = len(text) >= 5
    return (has_numbers or has_currency) and is_long_enough


# =============================================================================
# FAQ: DOWNLOAD, PARSING, PERSISTENZA
# =============================================================================


def fetch_markdown_from_html(url: str) -> str:
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    content = soup.select_one("#articleContent")
    if content is None:
        raise RuntimeError("Contenuto principale non trovato")
    text = content.get_text("\n")
    return text.strip()


def parse_faq(markdown: str) -> list:
    pattern = r"^##\s+(.*?)\n(.*?)(?=\n##\s+|\Z)"
    matches = re.findall(pattern, markdown, flags=re.S | re.M)
    if not matches:
        raise RuntimeError("Formato non valido: nessuna FAQ trovata (titoli '##').")

    faq = []
    for domanda, risposta in matches:
        faq.append(
            {
                "domanda": domanda.strip(),
                "risposta": risposta.strip(),
            }
        )
    return faq


def write_faq_json(faq: list, filename: str):
    faq_obj = {"faq": faq}
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(faq_obj, f, ensure_ascii=False, indent=2)


def update_faq_from_web() -> bool:
    try:
        markdown = fetch_markdown_from_html(PASTE_URL)
        faq = parse_faq(markdown)
        write_faq_json(faq, FAQ_FILE)
        logger.info(f"FAQ aggiornate: {len(faq)} domande.")
        return True
    except Exception as e:
        logger.error(f"Errore aggiornamento FAQ: {e}")
        return False


# =============================================================================
# PERSISTENZA GENERICA
# =============================================================================


def load_json_file(filename, default=None):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default if default is not None else {}
    except json.JSONDecodeError:
        logger.error(f"Errore lettura JSON da {filename}")
        return default if default is not None else {}


def save_json_file(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_authorized_users():
    data = load_json_file(AUTHORIZED_USERS_FILE, default={})
    if isinstance(data, list):
        # compat vecchio formato
        return {
            str(uid): {"id": uid, "name": "Sconosciuto", "username": None} for uid in data
        }
    return data


def save_authorized_users(users):
    save_json_file(AUTHORIZED_USERS_FILE, users)


def load_access_code():
    data = load_json_file(ACCESS_CODE_FILE, default={})
    if not data.get("code"):
        code = secrets.token_urlsafe(12)
        data = {"code": code}
        save_json_file(ACCESS_CODE_FILE, data)
    return data["code"]


def save_access_code(code):
    save_json_file(ACCESS_CODE_FILE, {"code": code})


def load_faq():
    return load_json_file(FAQ_FILE)


def is_user_authorized(user_id: int) -> bool:
    authorized_users = load_authorized_users()
    return str(user_id) in authorized_users


def authorize_user(user_id, first_name=None, last_name=None, username=None) -> bool:
    authorized_users = load_authorized_users()
    user_id_str = str(user_id)

    if user_id_str not in authorized_users:
        full_name = f"{first_name or ''} {last_name or ''}".strip() or "Sconosciuto"
        authorized_users[user_id_str] = {
            "id": user_id,
            "name": full_name,
            "username": username,
        }
        save_authorized_users(authorized_users)
        return True
    return False


def get_bot_username():
    # viene impostato in initialize_bot_sync
    return getattr(get_bot_username, "username", "bot")


# =============================================================================
# RICERCA FUZZY FAQ
# =============================================================================


def calculate_similarity(text1: str, text2: str) -> float:
    return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()


def normalize_text(text: str) -> str:
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def extract_keywords(text: str) -> list:
    normalized = normalize_text(text)
    words = normalized.split()
    stop_words = {
        "che",
        "sono",
        "come",
        "dove",
        "quando",
        "quale",
        "quali",
        "del",
        "della",
        "dei",
        "delle",
        "con",
        "per",
        "una",
        "uno",
    }
    return [w for w in words if len(w) > 3 and w not in stop_words]


def fuzzy_search_faq(user_message: str, faq_list: list) -> dict:
    user_normalized = normalize_text(user_message)
    user_keywords = extract_keywords(user_message)

    best_match = None
    best_score = 0
    match_method = None

    for item in faq_list:
        domanda = item["domanda"]
        domanda_norm = normalize_text(domanda)

        # match quasi-esatto
        if domanda_norm in user_normalized or user_normalized in domanda_norm:
            return {
                "match": True,
                "item": item,
                "score": 1.0,
                "method": "exact",
            }

        # similarità globale
        sim = calculate_similarity(user_normalized, domanda_norm)
        if sim > best_score:
            best_score = sim
            best_match = item
            match_method = "similarity"

        # matching per keywords
        if user_keywords:
            domanda_keywords = extract_keywords(domanda)
            matched_keywords = sum(
                1
                for kw in user_keywords
                if any(calculate_similarity(kw, dk) > 0.8 for dk in domanda_keywords)
            )
            keyword_score = matched_keywords / len(user_keywords)
            if matched_keywords > 1:
                keyword_score *= 1.2

            if keyword_score > best_score:
                best_score = keyword_score
                best_match = item
                match_method = "keywords"

    if best_score >= FUZZY_THRESHOLD:
        return {
            "match": True,
            "item": best_match,
            "score": best_score,
            "method": match_method,
        }

    return {
        "match": False,
        "item": None,
        "score": best_score,
        "method": None,
    }


# =============================================================================
# HANDLER BOT
# =============================================================================


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # /start con codice
    if context.args:
        provided_code = context.args[0]
        correct_code = load_access_code()

        if provided_code == correct_code:
            was_new = authorize_user(
                user_id, user.first_name, user.last_name, user.username
            )
            if was_new:
                await update.message.reply_text(
                    "✅ Sei stato autorizzato con successo!\n\n"
                    "Ora puoi usare il bot liberamente. Scrivi la tua domanda o usa /help per vedere le categorie FAQ."
                )
                if ADMIN_CHAT_ID:
                    admin_msg = (
                        "✅ <b>Nuovo utente autorizzato tramite link!</b>\n\n"
                        f"👤 Nome: {user.first_name or ''} {user.last_name or ''}\n"
                        f"🆔 Username: @{user.username or 'N/A'}\n"
                        f"🔢 Chat ID: <code>{user_id}</code>"
                    )
                    try:
                        await context.bot.send_message(
                            chat_id=ADMIN_CHAT_ID,
                            text=admin_msg,
                            parse_mode="HTML",
                        )
                    except Exception as e:
                        logger.error(f"Errore notifica admin: {e}")
            else:
                await update.message.reply_text(
                    "✅ Sei già autorizzato!\n\n"
                    "Scrivi la tua domanda o usa /help per vedere le categorie FAQ."
                )
            return
        else:
            await update.message.reply_text(
                "❌ Codice di accesso non valido.\n\n"
                "Contatta l'amministratore per ottenere il link corretto."
            )
            return

    # /start normale
    if is_user_authorized(user_id):
        await update.message.reply_text(
            f"👋 Ciao {user.first_name}!\n\n"
            "Sono il bot FAQ con ricerca intelligente. Scrivi la tua domanda anche con errori di battitura!\n\n"
            "💡 Usa /help per vedere tutte le categorie disponibili."
        )
    else:
        await update.message.reply_text(
            "❌ Non sei autorizzato ad usare questo bot.\n\n"
            "Contatta l'amministratore per ottenere il link di accesso."
        )
        if ADMIN_CHAT_ID:
            admin_msg = (
                "⚠️ <b>Tentativo di accesso non autorizzato!</b>\n\n"
                f"👤 Nome: {user.first_name or ''} {user.last_name or ''}\n"
                f"🆔 Username: @{user.username or 'N/A'}\n"
                f"🔢 Chat ID: <code>{user_id}</code>\n"
                "💬 Messaggio: /start"
            )
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=admin_msg,
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Errore notifica admin: {e}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_user_authorized(user_id):
        await update.message.reply_text(
            "❌ Non sei autorizzato ad usare questo bot.\n\n"
            "Contatta l'amministratore per ottenere il link di accesso."
        )
        return

    faq_data = load_faq()
    faq_list = faq_data.get("faq", []) if faq_data else []
    if not faq_list:
        await update.message.reply_text(
            "❌ Nessuna FAQ disponibile al momento.\n\n"
            "Contatta l'amministratore."
        )
        return

    text = "📚 <b>Domande FAQ disponibili:</b>\n\n"
    for i, item in enumerate(faq_list, 1):
        text += f"{i}. {item['domanda']}\n"

    text += "\n💡 <b>Ricerca intelligente attiva!</b>\n"
    text += "Scrivi anche con errori di battitura, il bot capirà! 🎯"

    await update.message.reply_text(text, parse_mode="HTML")


async def genera_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Solo l'amministratore può usare questo comando.")
        return

    access_code = load_access_code()
    bot_username = get_bot_username.username
    link = f"https://t.me/{bot_username}?start={access_code}"
    authorized_count = len(load_authorized_users())

    msg = (
        "🔗 <b>Link di accesso universale:</b>\n\n"
        f"<code>{link}</code>\n\n"
        "📋 <b>Istruzioni:</b>\n"
        "• Condividi questo link con i tuoi contatti fidati\n"
        "• Chi clicca il link viene autorizzato automaticamente\n"
        "• Il link è valido finché non lo cambi\n\n"
        f"👥 Utenti già autorizzati: {authorized_count}\n\n"
        "🔄 Usa /cambia_codice per generare un nuovo link"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def cambia_codice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Solo l'amministratore può usare questo comando.")
        return

    new_code = secrets.token_urlsafe(12)
    save_access_code(new_code)
    bot_username = get_bot_username.username
    new_link = f"https://t.me/{bot_username}?start={new_code}"

    msg = (
        "✅ <b>Nuovo codice generato!</b>\n\n"
        "🔗 <b>Nuovo link:</b>\n"
        f"<code>{new_link}</code>\n\n"
        "⚠️ <b>Attenzione:</b> Il vecchio link non funziona più.\n"
        "Gli utenti già autorizzati possono continuare ad usare il bot."
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def lista_autorizzati_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Solo l'amministratore può usare questo comando.")
        return

    authorized_users = load_authorized_users()
    if not authorized_users:
        await update.message.reply_text("📋 Nessun utente autorizzato al momento.")
        return

    msg = f"👥 <b>Utenti autorizzati ({len(authorized_users)}):</b>\n\n"
    for i, (uid_str, data) in enumerate(authorized_users.items(), 1):
        name = data.get("name", "Sconosciuto")
        username = data.get("username")
        uid_real = data.get("id", uid_str)
        user_str = f"@{username}" if username else "N/A"
        msg += f"{i}. <b>{name}</b>\n"
        msg += f"   👤 Username: {user_str}\n"
        msg += f"   🔢 ID: <code>{uid_real}</code>\n\n"

    msg += "💡 Usa /revoca <code>chat_id</code> per rimuovere un utente."
    await update.message.reply_text(msg, parse_mode="HTML")


async def revoca_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Solo l'amministratore può usare questo comando.")
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Uso: /revoca <chat_id>\n\n"
            "Esempio: /revoca 123456789\n"
            "Usa /lista_autorizzati per vedere i Chat ID."
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Chat ID non valido (deve essere un numero).")
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

    msg = (
        "👑 <b>Comandi Admin disponibili:</b>\n\n"
        "🔐 <b>Gestione accessi</b>\n"
        "• /genera_link — genera link di accesso\n"
        "• /cambia_codice — rigenera codice\n"
        "• /lista_autorizzati — elenca utenti\n"
        "• /revoca &lt;chat_id&gt; — revoca accesso\n\n"
        "👤 <b>Comandi utente</b>\n"
        "• /start\n"
        "• /help\n\n"
        f"🎯 <b>Ricerca fuzzy</b>\n"
        f"Soglia attuale: {FUZZY_THRESHOLD:.0%}\n\n"
        "💡 Solo tu (ADMIN) vedi questo messaggio."
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Messaggi privati → FAQ."""
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    user_id = user.id
    text = update.message.text

    if not is_user_authorized(user_id):
        await update.message.reply_text(
            "❌ Non sei autorizzato ad usare questo bot.\n\n"
            "Contatta l'amministratore per ottenere il link di accesso."
        )
        if ADMIN_CHAT_ID:
            msg = (
                "⚠️ <b>Tentativo di accesso non autorizzato!</b>\n\n"
                f"👤 Nome: {user.first_name or ''} {user.last_name or ''}\n"
                f"🆔 Username: @{user.username or 'N/A'}\n"
                f"🔢 Chat ID: <code>{user_id}</code>\n"
                f"💬 Messaggio: {text[:100]}"
            )
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID, text=msg, parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Errore notifica admin: {e}")
        return

    faq_data = load_faq()
    if not faq_data:
        await update.message.reply_text(
            "❌ Nessuna FAQ disponibile al momento.\n\n"
            "Riprova più tardi o contatta l'amministratore."
        )
        return

    faq_list = faq_data.get("faq", [])
    result = fuzzy_search_faq(text, faq_list)

    if result["match"]:
        item = result["item"]
        score = result["score"]
        emoji = "🎯" if score > 0.9 else "✅" if score > 0.75 else "💡"

        resp = f"{emoji} <b>{item['domanda']}</b>\n\n{item['risposta']}"
        if score < 0.9:
            resp += f"\n\n<i>💬 Confidenza: {score:.0%}</i>"

        await update.message.reply_text(resp, parse_mode="HTML")
        logger.info(
            f"Match FAQ: method={result['method']} score={score:.2f} text='{text}'"
        )
    else:
        await update.message.reply_text(
            "❓ Non ho trovato una risposta precisa alla tua domanda.\n\n"
            f"🔍 Miglior somiglianza trovata: {result['score']:.0%}\n\n"
            "💡 Prova a:\n"
            "• Riformulare la domanda\n"
            "• Usare parole chiave diverse\n"
            "• Vedere tutte le FAQ con /help",
            parse_mode="HTML",
        )


async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Messaggi in gruppi/supergruppi/canali → supervisione ordini."""
    message = update.message or update.channel_post
    if not message or not message.text:
        return

    chat = message.chat
    sender = message.from_user

    logger.info("📢 MESSAGGIO RICEVUTO:")
    logger.info(f"   Chat ID: {chat.id}")
    logger.info(f"   Chat Type: {chat.type}")
    logger.info(f"   Chat Title: {chat.title or 'N/A'}")
    logger.info(f"   Sender: {sender.first_name if sender else 'Channel'}")
    logger.info(f"   Text: {message.text}")

    logger.info("✅ Procedo con l'analisi del messaggio (no controllo admin)")

    if not looks_like_order(message.text):
        logger.info("⏭️ Messaggio non sembra un ordine (no numeri o troppo corto).")
        return

    logger.info("🔍 Messaggio sembra un ordine, controllo metodo pagamento...")

    if has_payment_method(message.text):
        logger.info("✅ Metodo pagamento presente: nessun avviso.")
        return

    logger.info("⚠️ ORDINE SENZA METODO PAGAMENTO! Invio pulsanti avviso...")

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ Ho specificato", callback_data=f"specified_{message.message_id}"
            ),
            InlineKeyboardButton(
                "❌ Devo aggiungerlo", callback_data=f"add_{message.message_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                "🚫 Non è un ordine", callback_data=f"notorder_{message.message_id}"
            )
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        # Preparo i parametri base
        send_kwargs = {
            "chat_id": chat.id,
            "text": (
                "🤔 <b>Questo sembra un ordine ma non vedo il metodo di pagamento</b>\n\n"
                "Hai specificato come pagherai?"
            ),
            "reply_markup": reply_markup,
            "parse_mode": "HTML",
        }

        thread_id = getattr(message, "message_thread_id", None)

        # Per i canali con commenti, se thread_id è None, usiamo il message_id
        # Questo è necessario perché Telegram richiede sempre un thread nei canali
        if thread_id is None and chat.type == "supergroup":
            # Potrebbe essere un canale con commenti mascherato da supergroup
            # Proviamo a usare il message_id come thread_id
            thread_id = message.message_id
            logger.info(f"📌 Canale/supergroup senza thread_id: uso message_id come thread: {thread_id}")

        if thread_id is not None:
            send_kwargs["message_thread_id"] = thread_id
            send_kwargs["reply_to_message_id"] = message.message_id
            logger.info(f"📌 Invio con message_thread_id: {thread_id}")
        else:
            logger.info("📌 Invio senza thread_id")

        await context.bot.send_message(**send_kwargs)
        logger.info("✅ Pulsanti avviso inviati con successo.")
    except Exception as e:
        logger.error(f"❌ Errore invio pulsanti: {e}")


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user = query.from_user

    if data.startswith("specified_"):
        msg_id = data.split("_", 1)[1]
        await query.edit_message_text(
            "✅ <b>Perfetto!</b>\n\n"
            "Il metodo di pagamento è stato specificato.\n"
            f"Confermato da: {user.first_name}",
            parse_mode="HTML",
        )
        logger.info(
            f"✅ Utente {user.first_name} ha confermato metodo pagamento per messaggio {msg_id}"
        )

    elif data.startswith("add_"):
        msg_id = data.split("_", 1)[1]
        await query.edit_message_text(
            "⚠️ <b>Ricorda di aggiungere il metodo di pagamento!</b>\n\n"
            "Metodi accettati: carta, contanti, bonifico, PayPal, Satispay, crypto, ecc.\n"
            f"Segnalato da: {user.first_name}",
            parse_mode="HTML",
        )
        logger.info(
            f"⚠️ Utente {user.first_name} deve aggiungere metodo pagamento per messaggio {msg_id}"
        )

        if ADMIN_CHAT_ID:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=(
                        "⚠️ Ordine senza metodo pagamento.\n\n"
                        f"Segnalato da: {user.first_name} (@{user.username or 'N/A'})"
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Errore notifica admin: {e}")

    elif data.startswith("notorder_"):
        msg_id = data.split("_", 1)[1]
        await query.edit_message_text(
            "👍 <b>Ok, capito!</b>\n\n"
            "Non era un ordine.\n"
            f"Segnalato da: {user.first_name}",
            parse_mode="HTML",
        )
        logger.info(
            f"ℹ️ Utente {user.first_name} ha segnalato che il messaggio {msg_id} non è un ordine."
        )


# =============================================================================
# INIZIALIZZAZIONE BOT
# =============================================================================


def initialize_bot_sync():
    global bot_application, bot_initialized
    if bot_initialized:
        return

    try:
        logger.info("📡 Inizio inizializzazione bot...")

        # aggiorna FAQ da web (se possibile)
        update_faq_from_web()

        async def setup():
            global bot_application
            application = (
                Application.builder()
                .token(BOT_TOKEN)
                .updater(None)  # usiamo webhook, non polling
                .build()
            )

            bot = await application.bot.get_me()
            get_bot_username.username = bot.username
            logger.info(f"🤖 Bot con username: @{bot.username}")

            # COMANDI
            application.add_handler(CommandHandler("start", start))
            application.add_handler(CommandHandler("help", help_command))
            application.add_handler(CommandHandler("genera_link", genera_link_command))
            application.add_handler(CommandHandler("cambia_codice", cambia_codice_command))
            application.add_handler(CommandHandler("lista_autorizzati", lista_autorizzati_command))
            application.add_handler(CommandHandler("revoca", revoca_command))
            application.add_handler(CommandHandler("admin_help", admin_help_command))

            # MESSAGGI TESTUALI → FAQ (privati + gruppo discussione)
            application.add_handler(
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND
                    & (filters.ChatType.PRIVATE | filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
                    handle_message,
                )
            )

            # GRUPPI/SUPERGRUPPI → supervisione ordini
            application.add_handler(
                MessageHandler(
                    filters.TEXT
                    & ~filters.COMMAND
                    & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
                    handle_group_message,
                )
            )

            # CANALI → supervisione ordini
            application.add_handler(
                MessageHandler(
                    filters.TEXT & filters.ChatType.CHANNEL,
                    handle_group_message,
                )
            )

            # CALLBACK QUERY
            application.add_handler(CallbackQueryHandler(handle_callback_query))

            # WEBHOOK
            if WEBHOOK_URL:
                wh_url = f"{WEBHOOK_URL}/webhook"
                await application.bot.set_webhook(url=wh_url)
                logger.info(f"✅ Webhook impostato: {wh_url}")

            await application.initialize()
            await application.start()

            logger.info("🤖 Bot pronto e in esecuzione.")
            logger.info("📋 Config supervisione ordini:")
            logger.info(f"   • Keywords pagamento: {len(PAYMENT_KEYWORDS)}")
            logger.info("   • Avvisa solo se ordine SENZA metodo di pagamento")
            logger.info("   • Supervisiona TUTTI i messaggi in gruppi/canali")

            return application

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        bot_application = loop.run_until_complete(setup())
        bot_initialized = True
        logger.info("✅ Inizializzazione bot completata.")
    except Exception as e:
        logger.error(f"❌ Errore durante l'inizializzazione del bot: {e}")
        import traceback

        traceback.print_exc()


# =============================================================================
# ROUTE FLASK
# =============================================================================


@app.route("/")
def index():
    return "🤖 Bot Telegram FAQ + Supervisione Ordini attivo! ✅", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    global bot_initialized

    if not bot_initialized:
        initialize_bot_sync()

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
        logger.error(f"Errore webhook: {e}")
        import traceback

        traceback.print_exc()
        return "ERROR", 500


@app.route("/health")
def health():
    return "OK", 200


# =============================================================================
# AVVIO THREAD BOT ALL'IMPORT (PER GUNICORN)
# =============================================================================


def start_bot_thread():
    if BOT_TOKEN and ADMIN_CHAT_ID:
        t = Thread(target=initialize_bot_sync, daemon=True)
        t.start()
        logger.info("🚀 Thread inizializzazione bot avviato.")
    else:
        logger.error("❌ BOT_TOKEN o ADMIN_CHAT_ID mancanti, impossibile avviare il bot.")


start_bot_thread()
logger.info("🌐 Flask app pronta")


# =============================================================================
# ENTRY POINT LOCALE
# =============================================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
