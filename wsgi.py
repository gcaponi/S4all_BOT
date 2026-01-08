"""
WSGI Entry Point per Render.com - FIXED
Gestisce l'inizializzazione del bot Telegram e gli endpoint Flask
"""
import asyncio
import logging
import threading
from flask import request
from telegram import Update
from main import app, setup_bot

logger = logging.getLogger(__name__)

# Variabile globale per il bot e l'event loop
bot_application = None
event_loop = None
loop_thread = None

def run_event_loop(loop):
    """Esegue l'event loop in un thread dedicato"""
    asyncio.set_event_loop(loop)
    loop.run_forever()

def get_or_create_event_loop():
    """Ottiene l'event loop esistente o ne crea uno nuovo in un thread dedicato"""
    global event_loop, loop_thread
    
    if event_loop is None or event_loop.is_closed():
        event_loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=run_event_loop, args=(event_loop,), daemon=True)
        loop_thread.start()
        logger.info("✅ Nuovo event loop creato in thread dedicato")
    
    return event_loop

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Endpoint per ricevere gli update da Telegram via webhook.
    Questo viene chiamato da Telegram ogni volta che c'è un nuovo messaggio.
    """
    global bot_application
    
    try:
        # Ottieni o crea l'event loop
        loop = get_or_create_event_loop()
        
        # Se il bot non è ancora inizializzato, inizializzalo
        if not bot_application:
            logger.info("🔄 Bot non inizializzato, inizializzo...")
            future = asyncio.run_coroutine_threadsafe(setup_bot(), loop)
            bot_application = future.result(timeout=30)
        
        # Processa l'update da Telegram
        if bot_application:
            json_data = request.get_json(force=True)
            update = Update.de_json(json_data, bot_application.bot)
            
            # Esegui nel loop thread-safe
            future = asyncio.run_coroutine_threadsafe(
                bot_application.process_update(update), 
                loop
            )
            future.result(timeout=30)
            
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
        loop = get_or_create_event_loop()
        future = asyncio.run_coroutine_threadsafe(setup_bot(), loop)
        bot_application = future.result(timeout=60)
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

# End wsgi.py
