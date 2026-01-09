"""
WSGI Entry Point per Render.com - CON PROTEZIONE MESSAGGI TEST
Gestisce l'inizializzazione del bot Telegram e gli endpoint Flask
"""
import asyncio
import logging
import threading
import signal
import sys
from flask import request
from telegram import Update
from main import app, setup_bot

logger = logging.getLogger(__name__)

# Variabile globale per il bot e l'event loop
bot_application = None
event_loop = None
loop_thread = None
shutdown_event = threading.Event()

def run_event_loop(loop):
    """Esegue l'event loop in un thread dedicato"""
    asyncio.set_event_loop(loop)
    try:
        loop.run_forever()
    except Exception as e:
        logger.error(f"❌ Event loop error: {e}")
    finally:
        logger.info("🔄 Event loop terminato")

def get_or_create_event_loop():
    """Ottiene l'event loop esistente o ne crea uno nuovo in un thread dedicato"""
    global event_loop, loop_thread
    
    if event_loop is None or event_loop.is_closed():
        event_loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=run_event_loop, args=(event_loop,), daemon=True)
        loop_thread.start()
        logger.info("✅ Nuovo event loop creato in thread dedicato")
    
    return event_loop

def shutdown_handler(signum, frame):
    """Gestisce lo shutdown pulito del bot"""
    global bot_application, event_loop
    
    logger.info("🛑 Ricevuto segnale di shutdown")
    shutdown_event.set()
    
    if bot_application:
        try:
            future = asyncio.run_coroutine_threadsafe(
                bot_application.stop(), 
                event_loop
            )
            future.result(timeout=5)
            logger.info("✅ Bot fermato correttamente")
        except Exception as e:
            logger.error(f"❌ Errore durante shutdown bot: {e}")
    
    if event_loop and not event_loop.is_closed():
        try:
            event_loop.call_soon_threadsafe(event_loop.stop)
            logger.info("✅ Event loop fermato")
        except Exception as e:
            logger.error(f"❌ Errore fermata event loop: {e}")
    
    sys.exit(0)

# Registra handler per shutdown pulito
signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Endpoint per ricevere gli update da Telegram via webhook.
    Questo viene chiamato da Telegram ogni volta che c'è un nuovo messaggio.
    """
    global bot_application
    
    try:
        # Ottieni JSON
        json_data = request.get_json(force=True)
        
        # 🛡️ PROTEZIONE: Ignora richieste vuote o di test
        if not json_data or json_data == {}:
            logger.info("📭 Webhook vuoto ignorato (probabilmente test)")
            return 'ok', 200
        
        # 🛡️ PROTEZIONE: Verifica che ci sia un update valido
        if 'update_id' not in json_data:
            logger.info("📭 Webhook senza update_id ignorato")
            return 'ok', 200
        
        # Ottieni o crea l'event loop
        loop = get_or_create_event_loop()
        
        # Se il bot non è ancora inizializzato, inizializzalo
        if not bot_application:
            logger.info("🔄 Bot non inizializzato, inizializzo...")
            future = asyncio.run_coroutine_threadsafe(setup_bot(), loop)
            bot_application = future.result(timeout=180)
            logger.info("✅ Bot inizializzato dal webhook")
        
        # Processa l'update da Telegram
        if bot_application:
            update = Update.de_json(json_data, bot_application.bot)
            
            # 🛡️ PROTEZIONE: Ignora update senza messaggio/callback
            if not update.message and not update.callback_query and not update.business_message:
                logger.info("📭 Update senza contenuto ignorato")
                return 'ok', 200
            
            logger.info(f"📨 Processing update {update.update_id}")
            
            # Esegui nel loop thread-safe con timeout
            future = asyncio.run_coroutine_threadsafe(
                bot_application.process_update(update), 
                loop
            )
            future.result(timeout=180)  # 3 minuti
            
            logger.info(f"✅ Update {update.update_id} processato")
            return 'ok', 200
        else:
            logger.error("❌ Bot application non disponibile")
            return 'Bot not initialized', 503
            
    except asyncio.TimeoutError:
        logger.error("⏱️ TIMEOUT 180s durante elaborazione webhook")
        logger.error(f"   JSON ricevuto: {json_data if 'json_data' in locals() else 'N/A'}")
        return 'Timeout', 504
    except Exception as e:
        logger.error(f"❌ Errore webhook: {e}", exc_info=True)
        logger.error(f"   JSON ricevuto: {json_data if 'json_data' in locals() else 'N/A'}")
        return 'Error', 500

@app.route('/health', methods=['GET'])
def health():
    """
    Health check endpoint per Render.
    Render usa questo per verificare che l'app sia attiva.
    """
    if bot_application:
        return 'OK - Bot running', 200
    else:
        return 'OK - Bot initializing', 200

@app.route('/', methods=['GET'])
def home():
    """Endpoint root per verificare che il server sia online"""
    status = "✅ Running" if bot_application else "⏳ Initializing"
    return f'''
    🤖 Bot Telegram Business - {status}
    
    Endpoint disponibili:
    - GET  /health  → Health check
    - POST /webhook → Telegram webhook
    ''', 200

# Inizializzazione al boot (quando Gunicorn carica il modulo)
if __name__ != '__main__':
    logger.info("🚀 Avvio inizializzazione bot (Gunicorn context)...")
    try:
        loop = get_or_create_event_loop()
        
        # Inizializzazione con timeout esteso
        logger.info("⏳ Inizializzazione bot in corso (può richiedere fino a 3 minuti)...")
        future = asyncio.run_coroutine_threadsafe(setup_bot(), loop)
        bot_application = future.result(timeout=180)  # 3 minuti
        
        logger.info("✅ Bot inizializzato con successo al boot")
        logger.info(f"🌐 Webhook URL: {bot_application.bot.base_url if bot_application else 'N/A'}")
        
    except asyncio.TimeoutError:
        logger.error("⏱️ TIMEOUT durante inizializzazione bot (3 minuti superati)")
        logger.error("   Il bot verrà inizializzato al primo webhook valido")
    except Exception as e:
        logger.error(f"❌ Errore inizializzazione bot: {e}", exc_info=True)
        logger.error("   Il bot verrà inizializzato al primo webhook valido")

# Per test in locale
if __name__ == '__main__':
    logger.info("🧪 Modalità TEST locale")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot_application = loop.run_until_complete(setup_bot())
    app.run(host='0.0.0.0', port=10000, debug=True)
