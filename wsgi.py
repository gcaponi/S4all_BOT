"""
WSGI Entry Point - Versione Moderna con Business Messages
Creato: 09 Gennaio 2026
Architettura: Async pulita + Event loop dedicato + Business support nativo
"""
import asyncio
import logging
import threading
import signal
import sys
from flask import request
from telegram import Update
from main import app, initialize_bot

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================================
# GESTIONE EVENT LOOP ASINCRONO
# ============================================================

# Variabili globali
bot_application = None
event_loop = None
loop_thread = None
shutdown_requested = threading.Event()

def run_event_loop(loop):
    """Esegue l'event loop in un thread dedicato"""
    asyncio.set_event_loop(loop)
    try:
        loop.run_forever()
        logger.info("✅ Event loop terminato normalmente")
    except Exception as e:
        logger.error(f"❌ Event loop error: {e}", exc_info=True)

def get_or_create_event_loop():
    """Ottiene l'event loop esistente o ne crea uno nuovo"""
    global event_loop, loop_thread
    
    if event_loop is None or event_loop.is_closed():
        event_loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(
            target=run_event_loop, 
            args=(event_loop,), 
            daemon=True,
            name="AsyncEventLoop"
        )
        loop_thread.start()
        logger.info("✅ Event loop creato e avviato")
    
    return event_loop

def shutdown_handler(signum, frame):
    """Gestisce lo shutdown pulito del sistema"""
    global bot_application, event_loop
    
    logger.info("🛑 Shutdown richiesto...")
    shutdown_requested.set()
    
    # Ferma il bot
    if bot_application:
        try:
            loop = get_or_create_event_loop()
            future = asyncio.run_coroutine_threadsafe(
                bot_application.stop(),
                loop
            )
            future.result(timeout=5)
            logger.info("✅ Bot fermato correttamente")
        except Exception as e:
            logger.error(f"❌ Errore fermata bot: {e}")
    
    # Ferma l'event loop
    if event_loop and not event_loop.is_closed():
        try:
            event_loop.call_soon_threadsafe(event_loop.stop)
            logger.info("✅ Event loop fermato")
        except Exception as e:
            logger.error(f"❌ Errore fermata loop: {e}")
    
    sys.exit(0)

# Registra signal handlers
signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)

# ============================================================
# ENDPOINT FLASK
# ============================================================

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Endpoint webhook per ricevere gli update da Telegram.
    Supporta messaggi normali, Business Messages, e tutti i tipi di update.
    """
    global bot_application
    
    try:
        # Ottieni i dati
        json_data = request.get_json(force=True)
        
        # Ignora richieste vuote (test di Telegram)
        if not json_data or 'update_id' not in json_data:
            logger.debug("📭 Webhook vuoto ignorato (probabilmente test)")
            return 'ok', 200
        
        update_id = json_data.get('update_id')
        
        # Ottieni/crea event loop
        loop = get_or_create_event_loop()
        
        # Inizializza bot se necessario
        if not bot_application:
            logger.info("⏳ Bot non inizializzato, inizializzo...")
            future = asyncio.run_coroutine_threadsafe(initialize_bot(), loop)
            bot_application = future.result(timeout=60)
            logger.info("✅ Bot inizializzato dal webhook")
        
        # Processa l'update
        if bot_application:
            logger.info(f"📨 Processing update {update_id}")
            
            # Deserializza update
            update = Update.de_json(json_data, bot_application.bot)
            
            # Processa in modo asincrono
            future = asyncio.run_coroutine_threadsafe(
                bot_application.process_update(update),
                loop
            )
            future.result(timeout=60)  # Timeout 60 secondi
            
            logger.info(f"✅ Update {update_id} processato")
            return 'ok', 200
        else:
            logger.error("❌ Bot non disponibile")
            return 'Bot not initialized', 503
            
    except asyncio.TimeoutError:
        logger.error(f"⏱️ Timeout elaborazione webhook (60s)")
        return 'Timeout', 504
    except Exception as e:
        logger.error(f"❌ Errore webhook: {e}", exc_info=True)
        return 'Error', 500

@app.route('/health', methods=['GET'])
def health():
    """Health check per Render"""
    status = "running" if bot_application else "initializing"
    return {'status': status}, 200

@app.route('/', methods=['GET'])
def home():
    """Endpoint root"""
    status = "✅ Running" if bot_application else "⏳ Initializing"
    return f'''
    🤖 Bot Telegram Business - {status}
    
    Endpoints:
    - GET  /health  → Health check
    - POST /webhook → Telegram webhook
    ''', 200

# ============================================================
# INIZIALIZZAZIONE AL BOOT
# ============================================================

if __name__ != '__main__':
    logger.info("🚀 Avvio inizializzazione bot (Gunicorn)...")
    
    try:
        loop = get_or_create_event_loop()
        
        logger.info("⏳ Inizializzazione bot...")
        future = asyncio.run_coroutine_threadsafe(initialize_bot(), loop)
        bot_application = future.result(timeout=90)  # 90 secondi per init
        
        logger.info("✅ Bot inizializzato con successo")
        logger.info(f"🌐 Webhook: {bot_application.bot.base_url}")
        
    except asyncio.TimeoutError:
        logger.warning("⚠️ Timeout init (90s) - bot si init al primo webhook")
    except Exception as e:
        logger.error(f"❌ Errore init: {e}", exc_info=True)
        logger.warning("⚠️ Bot si inizializzerà al primo webhook")

# Test locale
if __name__ == '__main__':
    logger.info("🧪 Modalità TEST locale")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot_application = loop.run_until_complete(initialize_bot())
    app.run(host='0.0.0.0', port=10000, debug=True)

# End wsgi.py
