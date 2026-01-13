"""
WSGI Entry Point per Render.com - FIXED VERSION
Gestisce l'inizializzazione del bot Telegram e gli endpoint Flask
Fix: Event loop management robusto per Gunicorn gthread
"""
import asyncio
import logging
from flask import request
from telegram import Update
from main import app, setup_bot

# Configurazione logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Variabili globali
bot_application = None
event_loop = None

def get_or_create_event_loop():
    """
    Ottiene l'event loop esistente o ne crea uno nuovo in modo thread-safe.
    Questo risolve il problema 'Event loop is closed' con Gunicorn.
    """
    global event_loop
    
    try:
        # Prova a ottenere il loop esistente
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("Loop is closed")
        return loop
    except RuntimeError:
        # Se il loop non esiste o è chiuso, creane uno nuovo
        logger.info("🔄 Creazione nuovo event loop...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        event_loop = loop
        return loop

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Endpoint per ricevere gli update da Telegram via webhook.
    Questo viene chiamato da Telegram ogni volta che c'è un nuovo messaggio.
    """
    global bot_application
    
    try:
        # Inizializza bot se necessario
        if not bot_application:
            logger.warning("⚠️ Bot non inizializzato, inizializzo ora...")
            loop = get_or_create_event_loop()
            bot_application = loop.run_until_complete(setup_bot())
            
            if not bot_application:
                logger.error("❌ Setup bot fallito")
                return 'Bot initialization failed', 503
        
        # Processa l'update
        json_data = request.get_json(force=True)
        
        if not json_data:
            logger.warning("⚠️ Webhook ricevuto senza dati")
            return 'No data', 400
        
        update = Update.de_json(json_data, bot_application.bot)
        
        # Usa il loop esistente (NON crearne uno nuovo ogni volta!)
        loop = get_or_create_event_loop()
        loop.run_until_complete(bot_application.process_update(update))
        
        return 'ok', 200
        
    except Exception as e:
        logger.error(f"❌ Errore webhook: {e}", exc_info=True)
        return 'Error', 500

@app.route('/health', methods=['GET'])
def health():
    """
    Health check endpoint per Render.
    Render usa questo per verificare che l'app sia attiva.
    """
    global bot_application
    
    if bot_application:
        return 'OK - Bot active', 200
    else:
        return 'OK - Bot initializing', 200

@app.route('/', methods=['GET'])
def home():
    """Endpoint root per verificare che il server sia online"""
    global bot_application
    
    status = "✅ ATTIVO" if bot_application else "⏳ INIZIALIZZAZIONE"
    
    return f'''
    🤖 Bot Telegram Business - {status}
    
    Endpoint disponibili:
    - GET  /        → Status page
    - GET  /health  → Health check
    - POST /webhook → Telegram webhook
    ''', 200

# ============================================================================
# INIZIALIZZAZIONE AL BOOT (quando Gunicorn carica il modulo)
# ============================================================================

if __name__ != '__main__':
    logger.info("🚀 Avvio inizializzazione bot (Gunicorn context)...")
    logger.info(f"📍 PID: {__import__('os').getpid()}")
    
    try:
        # Crea event loop una sola volta
        loop = get_or_create_event_loop()
        logger.info("✅ Event loop creato")
        
        # Inizializza il bot
        logger.info("🔧 Chiamata setup_bot()...")
        bot_application = loop.run_until_complete(setup_bot())
        
        if bot_application:
            logger.info("✅ Bot inizializzato con successo!")
            logger.info(f"🤖 Bot username: @{bot_application.bot.username if hasattr(bot_application.bot, 'username') else 'unknown'}")
        else:
            logger.error("❌ Bot application è None dopo setup!")
            
    except ImportError as e:
        logger.critical(f"💀 IMPORT ERROR: {e}")
        logger.critical("Verifica che tutti i file siano presenti:")
        logger.critical("  - main.py")
        logger.critical("  - intent_classifier.py")
        logger.critical("  - citta_italiane.json")
    except Exception as e:
        logger.critical(f"💀 ERRORE CRITICO inizializzazione: {e}", exc_info=True)
        # Non fare raise - lascia che Gunicorn continui (il bot si inizializzerà al primo webhook)

# ============================================================================
# TEST LOCALE (non usato in produzione)
# ============================================================================

if __name__ == '__main__':
    logger.info("🧪 Modalità TEST locale")
    
    try:
        loop = get_or_create_event_loop()
        bot_application = loop.run_until_complete(setup_bot())
        
        if bot_application:
            logger.info("✅ Bot inizializzato per test locale")
            app.run(host='0.0.0.0', port=10000, debug=True)
        else:
            logger.error("❌ Inizializzazione fallita")
    except Exception as e:
        logger.error(f"❌ Errore test locale: {e}", exc_info=True)

# End wsgi.py
