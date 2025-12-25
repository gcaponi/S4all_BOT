import os
import json
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from flask import Flask
from threading import Thread

# Configurazione (legge da variabili d'ambiente)
BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_CHAT_ID = int(os.environ.get('ADMIN_CHAT_ID', 0))
FAQ_FILE = "faq.json"

# Flask per mantenere il bot attivo su Replit
app = Flask('')

@app.route('/')
def home():
    return "🤖 Bot Telegram è attivo!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# Carica le FAQ dal file JSON
def load_faq():
    try:
        with open(FAQ_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        # Crea un file FAQ di esempio
        faq_example = {
            "orari": "Siamo aperti dal lunedì al venerdì, 9:00-18:00",
            "contatti": "Puoi contattarci via email a info@esempio.it",
            "servizi": "Offriamo servizi di consulenza e supporto"
        }
        with open(FAQ_FILE, 'w', encoding='utf-8') as f:
            json.dump(faq_example, f, ensure_ascii=False, indent=2)
        return faq_example

FAQ = load_faq()

# Verifica se l'utente è affidabile
def is_trusted_user(user) -> bool:
    """Controlla se 'affidabile' è presente nel nome, cognome o username"""
    if user.first_name and "affidabile" in user.first_name.lower():
        return True
    if user.last_name and "affidabile" in user.last_name.lower():
        return True
    if user.username and "affidabile" in user.username.lower():
        return True
    return False

# Cerca la risposta nelle FAQ
def search_faq(question: str) -> str:
    """Cerca corrispondenze nelle FAQ"""
    question_lower = question.lower()

    for key, answer in FAQ.items():
        if key.lower() in question_lower or any(word in question_lower for word in key.lower().split()):
            return answer

    return None

# Handler per il comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not is_trusted_user(user):
        # Notifica l'amministratore
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"⚠️ TENTATIVO DI ACCESSO - Utente NON affidabile\n\n"
                 f"👤 Nome: {user.first_name} {user.last_name or ''}\n"
                 f"📱 Username: @{user.username or 'N/A'}\n"
                 f"🆔 ID: {user.id}\n"
                 f"💬 Comando: /start\n\n"
                 f"❌ L'utente NON ha ricevuto risposta."
        )
        return

    await update.message.reply_text(
        f"Ciao {user.first_name}! 👋\n\n"
        "Sono il bot FAQ. Puoi farmi domande e cercherò di risponderti.\n\n"
        "📋 Comandi disponibili:\n"
        "/start - Mostra questo messaggio\n"
        "/help - Mostra le categorie FAQ disponibili"
    )

# Handler per il comando /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not is_trusted_user(user):
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"⚠️ TENTATIVO DI ACCESSO - Utente NON affidabile\n\n"
                 f"👤 Nome: {user.first_name} {user.last_name or ''}\n"
                 f"📱 Username: @{user.username or 'N/A'}\n"
                 f"🆔 ID: {user.id}\n"
                 f"💬 Comando: /help\n\n"
                 f"❌ L'utente NON ha ricevuto risposta."
        )
        return

    categories = "\n".join([f"• {key.capitalize()}" for key in FAQ.keys()])
    await update.message.reply_text(
        f"📋 Categorie FAQ disponibili:\n\n{categories}\n\n"
        "Puoi farmi una domanda su uno di questi argomenti!"
    )

# Handler per i messaggi di testo
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message_text = update.message.text

    # Verifica se l'utente è affidabile
    if not is_trusted_user(user):
        # Notifica l'amministratore
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"⚠️ TENTATIVO DI ACCESSO - Utente NON affidabile\n\n"
                 f"👤 Nome: {user.first_name} {user.last_name or ''}\n"
                 f"📱 Username: @{user.username or 'N/A'}\n"
                 f"🆔 ID: {user.id}\n"
                 f"💬 Messaggio:\n\"{message_text}\"\n\n"
                 f"❌ L'utente NON ha ricevuto risposta."
        )
        return

    # Ricarica le FAQ (per aggiornamenti in tempo reale)
    global FAQ
    FAQ = load_faq()

    # Cerca la risposta nelle FAQ
    answer = search_faq(message_text)

    if answer:
        await update.message.reply_text(f"📝 {answer}")
    else:
        await update.message.reply_text(
            "Mi dispiace, non ho trovato una risposta nelle FAQ. 😕\n\n"
            "Usa /help per vedere le categorie disponibili."
        )

# Funzione principale
def main():
    if not BOT_TOKEN or not ADMIN_CHAT_ID:
        print("❌ ERRORE: BOT_TOKEN o ADMIN_CHAT_ID non configurati!")
        print("Configura le variabili d'ambiente nel file .env")
        return

    # Avvia Flask in un thread separato
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Crea l'applicazione Telegram
    application = Application.builder().token(BOT_TOKEN).build()

    # Registra gli handler
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Avvia il bot
    print("🤖 Bot avviato con successo!")
    print(f"👤 Admin Chat ID: {ADMIN_CHAT_ID}")
    print(f"📝 FAQ caricate: {len(FAQ)} categorie")
    print("\n✅ Il bot è pronto a ricevere messaggi!")

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()