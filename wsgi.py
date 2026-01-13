"""
WSGI Entry Point per Render.com - MINIMAL VERSION
Importa semplicemente l'app da main.py (che ora ha tutte le route)
"""
import asyncio
import logging
import signal
import sys

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

logger.info("=" * 70)
logger.info("🚀 WSGI: Importo app e setup_bot da main.py...")
logger.info("=" * 70)

try:
    from main import app, setup_bot
    logger.info("✅ Import riuscito!")
    
    # Log delle route registrate
    logger.info("🔍 Routes Flask registrate:")
    for rule in app.url_map.iter_rules():
        logger.info(f"  - {rule.rule} [{', '.join(rule.methods)}]")
    
except Exception as e:
    logger.critical(f"💀 ERRORE import: {e}", exc_info=True)
    raise

# Variabile globale per il bot
bot_application = None

# ============================================================================
# INIZIALIZZAZIONE BOT AL BOOT
# ============================================================================

if __name__ != '__main__':
    logger.info("=" * 70)
    logger.info("🚀 Inizializzazione bot (Gunicorn context)")
    logger.info("=" * 70)
    
    try:
        # Crea event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        logger.info("✅ Event loop creato")
        
        # Inizializza bot
        logger.info("🔧 Chiamata setup_bot()...")
        bot_application = loop.run_until_complete(setup_bot())
        
        if bot_application:
            logger.info("✅ Bot inizializzato con successo!")
            # Aggiorna la variabile globale in main.py
            import main
            main.bot_application = bot_application
            logger.info("✅ bot_application sincronizzato con main.py")
        else:
            logger.error("❌ setup_bot() ha ritornato None")
            
    except Exception as e:
        logger.error(f"❌ Errore inizializzazione: {e}", exc_info=True)
    
    logger.info("=" * 70)
    logger.info("✅ WSGI pronto")
    logger.info("=" * 70)

# Test locale
if __name__ == '__main__':
    logger.info("🧪 Modalità TEST locale")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot_application = loop.run_until_complete(setup_bot())
    
    import main
    main.bot_application = bot_application
    
    app.run(host='0.0.0.0', port=10000, debug=True)

# ============================================================================
# SIGNAL HANDLER per shutdown pulito
# ============================================================================

def handle_shutdown(signum, frame):
    """Gestisce SIGTERM per shutdown pulito"""
    logger.info("🛑 Ricevuto segnale di shutdown")
    
    if bot_application:
        try:
            loop = asyncio.get_event_loop()
            if not loop.is_closed():
                from main import shutdown_bot
                loop.run_until_complete(shutdown_bot())
        except Exception as e:
            logger.error(f"❌ Errore shutdown: {e}")
    
    sys.exit(0)

# Registra il signal handler
signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)

# End wsgi.py
