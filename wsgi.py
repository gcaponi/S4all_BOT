"""
WSGI Entry Point per Render.com
Inizializza il bot all'avvio di Gunicorn
"""
import asyncio
import logging
import json
import sys
from flask import request

# FORZA logging su stdout
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    stream=sys.stdout,
    force=True
)
logger = logging.getLogger(__name__)

# Log di test immediato
print("=" * 70, flush=True)
print("🚀 WSGI.PY CARICATO!", flush=True)
print("=" * 70, flush=True)

logger.info("=" * 70)
logger.info("🚀 WSGI: Importo app e setup_bot da main.py...")
logger.info("=" * 70)

try:
    from main import app, setup_bot
    from telegram import Update
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
# RIDEFINIZIONE ROUTE WEBHOOK DOPO INIT BOT
# ============================================================================

@app.route('/webhook', methods=['POST'])
def webhook_handler():
    """
    Webhook handler che usa bot_application di wsgi.py
    Questo override garantisce che usiamo il bot corretto
    """
    global bot_application
    
    try:
        logger.info("=" * 60)
        logger.info("🔔 WEBHOOK RICEVUTO")
        logger.info("=" * 60)
        
        if not bot_application:
            logger.error("❌ bot_application è None!")
            return 'Bot not initialized', 503
        
        json_data = request.get_json(force=True)
        
        if not json_data:
            logger.warning("⚠️ Nessun dato JSON ricevuto")
            return 'No data', 400
        
        # LOG DETTAGLIATO DELL'UPDATE
        logger.info(f"📦 Update ricevuto (primi 500 char):")
        logger.info(json.dumps(json_data, indent=2, ensure_ascii=False)[:500])
        
        # Verifica tipo di update
        if 'message' in json_data:
            msg = json_data['message']
            logger.info(f"💬 Tipo: message")
            logger.info(f"   User: {msg.get('from', {}).get('id')} - Chat: {msg.get('chat', {}).get('id')}")
            logger.info(f"   Text: {msg.get('text', 'N/A')}")
        elif 'business_message' in json_data:
            msg = json_data['business_message']
            logger.info(f"💼 Tipo: business_message")
            logger.info(f"   Connection: {msg.get('business_connection_id')}")
            logger.info(f"   User: {msg.get('from', {}).get('id')} - Chat: {msg.get('chat', {}).get('id')}")
            logger.info(f"   Text: {msg.get('text', 'N/A')}")
        elif 'callback_query' in json_data:
            logger.info(f"🔘 Tipo: callback_query")
        elif 'edited_message' in json_data:
            logger.info(f"✏️ Tipo: edited_message")
        else:
            logger.info(f"❓ Tipo sconosciuto: {list(json_data.keys())}")
        
        # Crea Update object
        update = Update.de_json(json_data, bot_application.bot)
        
        # Gestione event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("Loop chiuso")
        except RuntimeError:
            logger.info("🔄 Creo nuovo event loop")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        # Processa update
        logger.info("⚙️ Invio update al bot...")
        loop.run_until_complete(bot_application.process_update(update))
        logger.info("✅ Update processato con successo")
        
        return 'ok', 200
        
    except Exception as e:
        logger.error(f"❌ ERRORE WEBHOOK: {e}", exc_info=True)
        import traceback
        logger.error(traceback.format_exc())
        return f'Error: {str(e)}', 500


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
            logger.info(f"✅ Bot ID: {bot_application.bot.id}")
            
            # Aggiorna la variabile globale in main.py
            import main
            main.bot_application = bot_application
            logger.info("✅ bot_application sincronizzato con main.py")
        else:
            logger.error("❌ setup_bot() ha ritornato None")
            raise RuntimeError("Bot initialization failed")
            
    except Exception as e:
        logger.error(f"❌ Errore inizializzazione: {e}", exc_info=True)
        raise
    
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

# End wsgi.py
