"""
WSGI Entry Point - Versione Moderna
Bot Telegram Business con supporto nativo
Data: 09 Gennaio 2026
"""
import asyncio
import logging
import threading
from flask import request
from telegram import Update
from main import app, initialize_bot

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================================
# GESTIONE EVENT LOOP DEDICATO
# ============================================================

bot_application = None
event_loop = None
loop_thread = None

def run_event_loop(loop):
    """Esegue l'event loop in un thread dedicato"""
    asyncio.set_event_loop(loop)
    loop.run_forever()

def get_or_create_event_loop():
    """Ottiene o crea l'event loop dedicato"""
    global event_loop, loop_thread
    
    if event_loop is None or event_loop.is_closed():
        event_loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(
            target=run_event_loop,
            args=(event_loop,),
            daemon=True
        )
        loop_thread.start()
        logger.info("✅ Event loop creato")
    
    return event_loop

# ============================================================
# ENDPOINT FLASK
# ============================================================

@app.route('/webhook', methods=['POST'])
def webhook():
    """Gestisce i webhook di Telegram"""
    global bot_application
    
    try:
        # Ottieni dati
        json_data = request.get_json(force=True)
        
        # Ignora richieste vuote
        if not json_data or 'update_id' not in json_data:
            return 'ok', 200
        
        # Ottieni event loop
        loop = get_or_create_event_loop()
        
        # Inizializza bot se necessario
        if not bot_application:
            logger.info("⏳ Inizializzazione bot...")
            future = asyncio.run_coroutine_threadsafe(initialize_bot(), loop)
            bot_application = future.result(timeout=60)
            logger.info("✅ Bot inizializzato")
        
        # Processa update
        if bot_application:
            update = Update.de_json(json_data, bot_application.bot)
            future = asyncio.run_coroutine_threadsafe(
                bot_application.process_update(update),
                loop
            )
            future.result(timeout=60)
            return 'ok', 200
        else:
            return 'Bot not ready', 503
            
    except asyncio.TimeoutError:
        logger.error("⏱️ Timeout (60s)")
        return 'Timeout', 504
    except Exception as e:
        logger.error(f"❌ Errore: {e}", exc_info=True)
        return 'Error', 500

@app.route('/health', methods=['GET'])
def health():
    """Health check"""
    return 'OK', 200

@app.route('/', methods=['GET'])
def home():
    """Home"""
    status = "Running" if bot_application else "Initializing"
    return f'🤖 Bot: {status}', 200

# ============================================================
# INIZIALIZZAZIONE AL BOOT
# ============================================================

if __name__ != '__main__':
    logger.info("🚀 Avvio bot...")
    
    try:
        loop = get_or_create_event_loop()
        future = asyncio.run_coroutine_threadsafe(initialize_bot(), loop)
        bot_application = future.result(timeout=90)
        logger.info("✅ Bot pronto!")
    except Exception as e:
        logger.warning(f"⚠️ Init fallito: {e}")
        logger.info("Bot si inizializzerà al primo webhook")

# Test locale
if __name__ == '__main__':
    logger.info("🧪 Test locale")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot_application = loop.run_until_complete(initialize_bot())
    app.run(host='0.0.0.0', port=10000, debug=True)
    
# End wsgi.py
