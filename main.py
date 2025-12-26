import os
import json
import logging
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import secrets
import asyncio

# Configurazione logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configurazione da variabili d'ambiente
BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_CHAT_ID = int(os.environ.get('ADMIN_CHAT_ID', 0))
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '')  # Es: https://tuobot.onrender.com
PORT = int(os.environ.get('PORT', 8080))

# Flask app
app = Flask(__name__)

# File per salvare dati persistenti
AUTHORIZED_USERS_FILE = 'authorized_users.json'
ACCESS_CODE_FILE = 'access_code.json'
FAQ_FILE = 'faq.json'

# Variabile globale per l'application
bot_application = None

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
                    "Ora puoi usare il bot liberamente. Scrivi la tua domanda o usa /help per vedere le categorie FAQ."
                )
                
                if ADMIN_CHAT_ID:
                    admin_msg = (
                        f"✅ <b>Nuovo utente autorizzato tramite link!</b>\n\n"
                        f"👤 Nome: {user.first_name or ''} {user.last_name or ''}\n"
                        f"🆔 Username: @{user.username or 'N/A'}\n"
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
    
    if is_user_authorized(user_id):
        await update.message.reply_text(
            f"👋 Ciao {user.first_name}!\n\n"
            "Sono il bot FAQ. Scrivi la tua domanda o usa /help per vedere le categorie disponibili."
        )
    else:
        await update.message.reply_text(
            "❌ Non sei autorizzato ad usare questo bot.\n\n"
            "Contatta l'amministratore per ottenere il link di accesso."
        )
        
        if ADMIN_CHAT_ID:
            admin_msg = (
                f"⚠️ <b>Tentativo di accesso non autorizzato!</b>\n\n"
                f"👤 Nome: {user.first_name or ''} {user.last_name or ''}\n"
                f"🆔 Username: @{user.username or 'N/A'}\n"
                f"🔢 Chat ID: <code>{user_id}</code>\n"
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
            "❌ Non sei autorizzato ad usare questo bot.\n\n"
            "Contatta l'amministratore per ottenere il link di accesso."
        )
        return
    
    faq = load_faq()
    
    if not faq:
        await update.message.reply_text("❌ Nessuna FAQ disponibile al momento.")
        return
    
    help_text = "📚 <b>Categorie FAQ disponibili:</b>\n\n"
    for categoria in faq.keys():
        help_text += f"• {categoria.title()}\n"
    
    help_text += "\n💡 Scrivi una parola chiave per ottenere informazioni!"
    
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
        f"🔗 <b>Link di accesso universale:</b>\n\n"
        f"<code>{link}</code>\n\n"
        f"📋 <b>Istruzioni:</b>\n"
        f"• Condividi questo link con i tuoi contatti fidati\n"
        f"• Chi clicca il link viene autorizzato automaticamente\n"
        f"• Il link è valido per sempre (finché non lo cambi)\n\n"
        f"👥 Utenti già autorizzati: {authorized_count}\n\n"
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
        f"✅ <b>Nuovo codice generato!</b>\n\n"
        f"🔗 <b>Nuovo link:</b>\n"
        f"<code>{new_link}</code>\n\n"
        f"⚠️ <b>Attenzione:</b> Il vecchio link non funziona più!\n"
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
            "Usa /lista_autorizzati per vedere i Chat ID"
        )
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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    message_text = update.message.text.lower()
    
    if not is_user_authorized(user_id):
        await update.message.reply_text(
            "❌ Non sei autorizzato ad usare questo bot.\n\n"
            "Contatta l'amministratore per ottenere il link di accesso."
        )
        
        if ADMIN_CHAT_ID:
            admin_msg = (
                f"⚠️ <b>Tentativo di accesso non autorizzato!</b>\n\n"
                f"👤 Nome: {user.first_name or ''} {user.last_name or ''}\n"
                f"🆔 Username: @{user.username or 'N/A'}\n"
                f"🔢 Chat ID: <code>{user_id}</code>\n"
                f"💬 Messaggio: {update.message.text[:100]}"
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
            "❌ Nessuna FAQ disponibile al momento.\n\n"
            "Riprova più tardi o contatta l'amministratore."
        )
        return
    
    risposta_trovata = None
    categoria_trovata = None
    
    for categoria, risposta in faq.items():
        if categoria.lower() in message_text:
            risposta_trovata = risposta
            categoria_trovata = categoria
            break
    
    if risposta_trovata:
        await update.message.reply_text(
            f"📌 <b>{categoria_trovata.title()}</b>\n\n{risposta_trovata}",
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(
            "❓ Non ho trovato una risposta per la tua domanda.\n\n"
            "Usa /help per vedere le categorie FAQ disponibili."
        )

# Flask routes
@app.route('/')
def index():
    return "Bot Telegram FAQ attivo! ✅"

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming webhook requests from Telegram"""
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), bot_application.bot)
        asyncio.run(bot_application.process_update(update))
    return "OK"

async def setup_application():
    """Setup the bot application"""
    global bot_application
    
    if not BOT_TOKEN or not ADMIN_CHAT_ID:
        print("\n" + "="*50)
        print("ERRORE: BOT_TOKEN o ADMIN_CHAT_ID non configurati!")
        print("="*50 + "\n")
        return None
    
    # Crea l'applicazione
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Salva il bot username
    bot = await application.bot.get_me()
    get_bot_username.username = bot.username
    logger.info(f"Bot username: @{bot.username}")
    
    # Registra gli handler
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("genera_link", genera_link_command))
    application.add_handler(CommandHandler("cambia_codice", cambia_codice_command))
    application.add_handler(CommandHandler("lista_autorizzati", lista_autorizzati_command))
    application.add_handler(CommandHandler("revoca", revoca_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Setup webhook se WEBHOOK_URL è configurato
    if WEBHOOK_URL:
        webhook_url = f"{WEBHOOK_URL}/webhook"
        await application.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook configurato: {webhook_url}")
    
    await application.initialize()
    await application.start()
    
    print("\n" + "="*50)
    print("🤖 Bot avviato con successo!")
    print(f"👤 Admin Chat ID: {ADMIN_CHAT_ID}")
    
    faq = load_faq()
    if faq:
        print(f"📝 FAQ caricate: {len(faq)} categorie")
    else:
        print("⚠️  Nessuna FAQ trovata - crea il file faq.json")
    
    print(f"🔗 Codice accesso attuale: {load_access_code()}")
    print("\n✅ Il bot è pronto a ricevere messaggi!")
    print("💡 Usa /genera_link per ottenere il link di accesso")
    print("="*50 + "\n")
    
    return application

if __name__ == '__main__':
    # Setup bot
    bot_application = asyncio.run(setup_application())
    
    if bot_application:
        # Avvia Flask
        app.run(host='0.0.0.0', port=PORT, debug=False)
    else:
        print("Errore: impossibile avviare il bot")
