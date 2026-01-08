"""
WSGI Entry Point per Render.com
Gestisce l'inizializzazione del bot Telegram e gli endpoint Flask
"""
import asyncio
import logging
from flask import request
from telegram import Update
from main import app, setup_bot

logger = logging.getLogger(__name__)

# Variabile globale per il bot
bot_application = None

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Endpoint per ricevere gli update da Telegram via webhook.
    Questo viene chiamato da Telegram ogni volta che c'è un nuovo messaggio.
    """
    global bot_application
    
    try:
        # Se il bot non è ancora inizializzato, inizializzalo
        if not bot_application:
            logger.info("🔄 Bot non inizializzato, inizializzo...")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            bot_application = loop.run_until_complete(setup_bot())
        
        # Processa l'update da Telegram
        if bot_application:
            json_data = request.get_json(force=True)
            update = Update.de_json(json_data, bot_application.bot)
            
            # Esegui in modo sincrono per Flask
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(bot_application.process_update(update))
            
            return 'ok', 200
        else:
            logger.error("❌ Bot application non disponibile")
            return 'Bot not initialized', 503
            
    except Exception as e:
        logger.error(f"❌ Errore webhook: {e}", exc_info=True)
        return 'Error', 500

@app.route('/health', methods=['GET'])
def health():
    """
    Health check endpoint per Render.
    Render usa questo per verificare che l'app sia attiva.
    """
    return 'OK', 200

@app.route('/', methods=['GET'])
def home():
    """Endpoint root per verificare che il server sia online"""
    return '''
    🤖 Bot Telegram Business - ATTIVO
    
    Endpoint disponibili:
    - GET  /health  → Health check
    - POST /webhook → Telegram webhook
    ''', 200

# Inizializzazione al boot (quando Gunicorn carica il modulo)
if __name__ != '__main__':
    logger.info("🚀 Avvio inizializzazione bot (Gunicorn context)...")
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        bot_application = loop.run_until_complete(setup_bot())
        logger.info("✅ Bot inizializzato con successo")
    except Exception as e:
        logger.error(f"❌ Errore inizializzazione bot: {e}", exc_info=True)

# Per test in locale
if __name__ == '__main__':
    logger.info("🧪 Modalità TEST locale")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot_application = loop.run_until_complete(setup_bot())
    app.run(host='0.0.0.0', port=10000, debug=True)
