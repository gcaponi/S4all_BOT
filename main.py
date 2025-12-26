import os
import json
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import secrets
import re
import requests
from bs4 import BeautifulSoup
from difflib import SequenceMatcher

# FAQ File
PASTE_URL = "https://justpaste.it/faq_4all"
OUTPUT_FILE = "faq.json"

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

def main_faq_update():
    markdown = fetch_markdown_from_html(PASTE_URL)
    faq = parse_faq(markdown)
    write_faq_json(faq, OUTPUT_FILE)
    print(f"FAQ generate correttamente in '{OUTPUT_FILE}'")

if __name__ == "__main__":
    main_faq_update()

# Configurazione logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)
logger = logging.getLogger(__name__)

# Configurazione da variabili d'ambiente
BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_CHAT_ID = int(os.environ.get('ADMIN_CHAT_ID', 0))

# File per salvare dati persistenti
AUTHORIZED_USERS_FILE = 'authorized_users.json'
ACCESS_CODE_FILE = 'access_code.json'
FAQ_FILE = 'faq.json'

# Soglia per il fuzzy matching (0.0 = nessuna somiglianza, 1.0 = identico)
FUZZY_THRESHOLD = 0.6  # 60% di somiglianza minima

# Funzioni per gestire dati persistenti
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
    return load_json_file(AUTHORIZED_USERS_FILE, default=[])

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
    return user_id in authorized_users

def authorize_user(user_id):
    authorized_users = load_authorized_users()
    if user_id not in authorized_users:
        authorized_users.append(user_id)
        save_authorized_users(authorized_users)
        return True
    return False

def get_bot_username():
    return getattr(get_bot_username, 'username', 'tuobot')

# FUNZIONE FUZZY MATCHING MIGLIORATA
def calculate_similarity(text1: str, text2: str) -> float:
    """Calcola la somiglianza tra due testi (0.0 - 1.0)"""
    return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

def normalize_text(text: str) -> str:
    """Normalizza il testo rimuovendo caratteri speciali e spazi extra"""
    # Rimuove punteggiatura e caratteri speciali
    text = re.sub(r'[^\w\s]', '', text)
    # Rimuove spazi multipli
    text = re.sub(r'\s+', ' ', text)
    return text.strip().lower()

def extract_keywords(text: str) -> list:
    """Estrae le parole chiave dal testo (parole > 3 caratteri)"""
    normalized = normalize_text(text)
    words = normalized.split()
    # Filtra parole comuni e corte
    stop_words = {'che', 'sono', 'come', 'dove', 'quando', 'quale', 'quali', 
                  'del', 'della', 'dei', 'delle', 'con', 'per', 'una', 'uno'}
    keywords = [w for w in words if len(w) > 3 and w not in stop_words]
    return keywords

def fuzzy_search_faq(user_message: str, faq_list: list) -> dict:
    """
    Cerca nelle FAQ usando fuzzy matching avanzato.
    Ritorna: {'match': bool, 'item': dict, 'score': float, 'method': str}
    """
    user_normalized = normalize_text(user_message)
    user_keywords = extract_keywords(user_message)

    best_match = None
    best_score = 0
    match_method = None

    for item in faq_list:
        domanda = item["domanda"]
        domanda_normalized = normalize_text(domanda)

        # METODO 1: Match esatto (priorità massima)
        if domanda_normalized in user_normalized or user_normalized in domanda_normalized:
            return {
                'match': True,
                'item': item,
                'score': 1.0,
                'method': 'exact'
            }

        # METODO 2: Somiglianza globale con SequenceMatcher
        similarity = calculate_similarity(user_normalized, domanda_normalized)
        if similarity > best_score:
            best_score = similarity
            best_match = item
            match_method = 'similarity'

        # METODO 3: Match per keywords (con peso maggiore)
        if user_keywords:
            domanda_keywords = extract_keywords(domanda)
            matched_keywords = sum(1 for kw in user_keywords if any(
                calculate_similarity(kw, dk) > 0.8 for dk in domanda_keywords
            ))

            keyword_score = matched_keywords / len(user_keywords)
            # Bonus se matcha più keywords
            keyword_score = keyword_score * 1.2 if matched_keywords > 1 else keyword_score

            if keyword_score > best_score:
                best_score = keyword_score
                best_match = item
                match_method = 'keywords'

    # Ritorna il miglior match solo se supera la soglia
    if best_score >= FUZZY_THRESHOLD:
        return {
            'match': True,
            'item': best_match,
            'score': best_score,
            'method': match_method
        }

    return {'match': False, 'item': None, 'score': best_score, 'method': None}

# Handler per il comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    if context.args:
        provided_code = context.args[0]
        correct_code = load_access_code()

        if provided_code == correct_code:
            was_new = authorize_user(user_id)

            if was_new:
                await update.message.reply_text(
                    "✅ Sei stato autorizzato con successo!\n\n"
                    "Ora puoi usare il bot liberamente. Scrivi la tua domanda o usa /help per vedere le categorie FAQ.")

                if ADMIN_CHAT_ID:
                    admin_msg = (
                        f"✅ <b>Nuovo utente autorizzato tramite link!</b>\n\n"
                        f"👤 Nome: {user.first_name or ''} {user.last_name or ''}\n"
                        f"🆔 Username: @{user.username or 'N/A'}\n"
                        f"🔢 Chat ID: <code>{user_id}</code>")
                    try:
                        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_msg, parse_mode='HTML')
                    except Exception as e:
                        logger.error(f"Errore invio notifica admin: {e}")
            else:
                await update.message.reply_text(
                    "✅ Sei già autorizzato!\n\n"
                    "Scrivi la tua domanda o usa /help per vedere le categorie FAQ.")
            return
        else:
            await update.message.reply_text(
                "❌ Codice di accesso non valido.\n\n"
                "Contatta l'amministratore per ottenere il link corretto.")
            return

    if is_user_authorized(user_id):
        await update.message.reply_text(
            f"👋 Ciao {user.first_name}!\n\n"
            "Sono il bot FAQ con ricerca intelligente. Scrivi la tua domanda anche con errori di battitura!\n\n"
            "💡 Usa /help per vedere tutte le categorie disponibili.")
    else:
        await update.message.reply_text(
            "❌ Non sei autorizzato ad usare questo bot.\n\n"
            "Contatta l'amministratore per ottenere il link di accesso.")

        if ADMIN_CHAT_ID:
            admin_msg = (
                f"⚠️ <b>Tentativo di accesso non autorizzato!</b>\n\n"
                f"👤 Nome: {user.first_name or ''} {user.last_name or ''}\n"
                f"🆔 Username: @{user.username or 'N/A'}\n"
                f"🔢 Chat ID: <code>{user_id}</code>\n"
                f"💬 Messaggio: /start")
            try:
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_msg, parse_mode='HTML')
            except Exception as e:
                logger.error(f"Errore invio notifica admin: {e}")

# Handler per il comando /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_user_authorized(user_id):
        await update.message.reply_text(
            "❌ Non sei autorizzato ad usare questo bot.\n\n"
            "Contatta l'amministratore per ottenere il link di accesso.")
        return

    faq_data = load_faq()
    faq_list = faq_data.get("faq", [])

    if not faq_list:
        await update.message.reply_text("❌ Nessuna FAQ disponibile al momento.")
        return

    help_text = "📚 <b>Domande FAQ disponibili:</b>\n\n"

    for i, item in enumerate(faq_list, 1):
        help_text += f"{i}. {item['domanda']}\n"

    help_text += "\n💡 <b>Ricerca intelligente attiva!</b>\n"
    help_text += "Scrivi anche con errori di battitura, il bot capirà! 🎯"

    await update.message.reply_text(help_text, parse_mode='HTML')

# COMANDI ADMIN
async def genera_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Solo l'amministratore può usare questo comando.")
        return

    access_code = load_access_code()
    bot_username = get_bot_username.username
    link = f"https://t.me/{bot_username}?start={access_code}"
    authorized_count = len(load_authorized_users())

    message = (f"🔗 <b>Link di accesso universale:</b>\n\n"
               f"<code>{link}</code>\n\n"
               f"📋 <b>Istruzioni:</b>\n"
               f"• Condividi questo link con i tuoi contatti fidati\n"
               f"• Chi clicca il link viene autorizzato automaticamente\n"
               f"• Il link è valido per sempre (finché non lo cambi)\n\n"
               f"👥 Utenti già autorizzati: {authorized_count}\n\n"
               f"🔄 Usa /cambia_codice per generare un nuovo link")

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
        f"✅ <b>Nuovo codice generato!</b>\n\n"
        f"🔗 <b>Nuovo link:</b>\n"
        f"<code>{new_link}</code>\n\n"
        f"⚠️ <b>Attenzione:</b> Il vecchio link non funziona più!\n"
        f"Gli utenti già autorizzati possono continuare ad usare il bot.")

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

    message = f"👥 <b>Utenti autorizzati ({len(authorized_users)}):</b>\n\n"

    for i, uid in enumerate(authorized_users, 1):
        message += f"{i}. Chat ID: <code>{uid}</code>\n"

    message += f"\n💡 Usa /revoca seguito dal Chat ID per rimuovere un utente"

    await update.message.reply_text(message, parse_mode='HTML')

async def revoca_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Solo l'amministratore può usare questo comando.")
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Uso: /revoca <chat_id>\n\n"
            "Esempio: /revoca 123456789\n"
            "Usa /lista_autorizzati per vedere i Chat ID")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Chat ID non valido. Deve essere un numero.")
        return

    authorized_users = load_authorized_users()

    if target_id in authorized_users:
        authorized_users.remove(target_id)
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
        "👑 <b>Comandi Admin disponibili:</b>\n\n"
        "🔐 <b>Gestione accessi</b>\n"
        "• /genera_link — genera link di accesso\n"
        "• /cambia_codice — cambia il codice di accesso\n"
        "• /lista_autorizzati — lista utenti autorizzati\n"
        "• /revoca &lt;chat_id&gt; — rimuove un utente\n\n"
        "👤 <b>Comandi Utente</b>\n"
        "• /start\n"
        "• /help\n\n"
        "🎯 <b>Ricerca Fuzzy</b>\n"
        "Il bot ora usa ricerca intelligente!\n"
        "Soglia attuale: {:.0%}\n\n"
        "💡 Solo l'ADMIN può vedere questo messaggio".format(FUZZY_THRESHOLD)
    )

    await update.message.reply_text(message, parse_mode='HTML')

# Handler per i messaggi normali CON FUZZY MATCHING
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    message_text = update.message.text

    if not is_user_authorized(user_id):
        await update.message.reply_text(
            "❌ Non sei autorizzato ad usare questo bot.\n\n"
            "Contatta l'amministratore per ottenere il link di accesso.")

        if ADMIN_CHAT_ID:
            admin_msg = (
                f"⚠️ <b>Tentativo di accesso non autorizzato!</b>\n\n"
                f"👤 Nome: {user.first_name or ''} {user.last_name or ''}\n"
                f"🆔 Username: @{user.username or 'N/A'}\n"
                f"🔢 Chat ID: <code>{user_id}</code>\n"
                f"💬 Messaggio: {message_text[:100]}")
            try:
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_msg, parse_mode='HTML')
            except Exception as e:
                logger.error(f"Errore invio notifica admin: {e}")
        return

    faq = load_faq()

    if not faq:
        await update.message.reply_text(
            "❌ Nessuna FAQ disponibile al momento.\n\n"
            "Riprova più tardi o contatta l'amministratore.")
        return

    faq_list = faq.get("faq", [])

    # USA LA RICERCA FUZZY
    result = fuzzy_search_faq(message_text, faq_list)

    if result['match']:
        item = result['item']
        score = result['score']
        method = result['method']

        # Messaggio diverso in base alla confidenza
        confidence_emoji = "🎯" if score > 0.9 else "✅" if score > 0.75 else "💡"

        response = f"{confidence_emoji} <b>{item['domanda']}</b>\n\n{item['risposta']}"

        # Se match non perfetto, aggiungi nota
        if score < 0.9:
            response += f"\n\n<i>💬 Confidenza: {score:.0%}</i>"

        await update.message.reply_text(response, parse_mode='HTML')

        # Log per l'admin (opzionale)
        logger.info(f"Match trovato: {method}, score: {score:.2f}, query: '{message_text}'")
    else:
        await update.message.reply_text(
            f"❓ Non ho trovato una risposta per: <i>\"{message_text}\"</i>\n\n"
            f"🔍 Ho cercato con somiglianza fino a {result['score']:.0%}\n\n"
            f"💡 Prova a:\n"
            f"• Riformulare la domanda\n"
            f"• Usare parole chiave diverse\n"
            f"• Vedere tutte le FAQ con /help",
            parse_mode='HTML')

# Funzione principale
def main():
    if not BOT_TOKEN or not ADMIN_CHAT_ID:
        print("\n" + "=" * 50)
        print("ERRORE: BOT_TOKEN o ADMIN_CHAT_ID non configurati!")
        print("Configura le variabili d'ambiente nei Secrets di Replit:")
        print("1. BOT_TOKEN = il token del tuo bot")
        print("2. ADMIN_CHAT_ID = il tuo Chat ID")
        print("=" * 50 + "\n")
        return

    application = Application.builder().token(BOT_TOKEN).build()

    import asyncio
    async def set_bot_username():
        bot = await application.bot.get_me()
        get_bot_username.username = bot.username
        logger.info(f"Bot username: @{bot.username}")

    asyncio.get_event_loop().run_until_complete(set_bot_username())

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("genera_link", genera_link_command))
    application.add_handler(CommandHandler("cambia_codice", cambia_codice_command))
    application.add_handler(CommandHandler("lista_autorizzati", lista_autorizzati_command))
    application.add_handler(CommandHandler("revoca", revoca_command))
    application.add_handler(CommandHandler("admin_help", admin_help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("\n" + "=" * 50)
    print("🤖 Bot avviato con successo!")
    print(f"👤 Admin Chat ID: {ADMIN_CHAT_ID}")

    faq = load_faq()
    if faq:
        print(f"📝 FAQ caricate: {len(faq.get('faq', []))} categorie")
    else:
        print("⚠️  Nessuna FAQ trovata - crea il file faq.json")

    print(f"🔗 Codice accesso attuale: {load_access_code()}")
    print(f"🎯 Ricerca fuzzy attiva (soglia: {FUZZY_THRESHOLD:.0%})")
    print("\n✅ Il bot è pronto a ricevere messaggi!")
    print("💡 Usa /genera_link per ottenere il link di accesso")
    print("=" * 50 + "\n")

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()