"""
WSGI Entry Point per Render.com
Inizializza il bot all'avvio di Gunicorn
"""
import asyncio
import logging
import json
from flask import request

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

logger.info("=" * 70)
logger.info("ðŸš€ WSGI: Importo app e setup_bot da main.py...")
logger.info("=" * 70)

try:
    from main import app, setup_bot
    from telegram import Update
    logger.info("âœ… Import riuscito!")
    
    # Log delle route registrate
    logger.info("ðŸ” Routes Flask registrate:")
    for rule in app.url_map.iter_rules():
        logger.info(f"  - {rule.rule} [{', '.join(rule.methods)}]")
    
except Exception as e:
    logger.critical(f"ðŸ’€ ERRORE import: {e}", exc_info=True)
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
        logger.info("ðŸ”” WEBHOOK RICEVUTO")
        logger.info("=" * 60)
        
        if not bot_application:
            logger.error("âŒ bot_application Ã¨ None!")
            return 'Bot not initialized', 503
        
        json_data = request.get_json(force=True)
        
        if not json_data:
            logger.warning("âš ï¸ Nessun dato JSON ricevuto")
            return 'No data', 400
        
        # LOG DETTAGLIATO DELL'UPDATE
        logger.info(f"ðŸ“¦ Update ricevuto (primi 500 char):")
        logger.info(json.dumps(json_data, indent=2, ensure_ascii=False)[:500])
        
        # Verifica tipo di update
        if 'message' in json_data:
            msg = json_data['message']
            logger.info(f"ðŸ’¬ Tipo: message")
            logger.info(f"   User: {msg.get('from', {}).get('id')} - Chat: {msg.get('chat', {}).get('id')}")
            logger.info(f"   Text: {msg.get('text', 'N/A')}")
        elif 'business_message' in json_data:
            msg = json_data['business_message']
            logger.info(f"ðŸ’¼ Tipo: business_message")
            logger.info(f"   Connection: {msg.get('business_connection_id')}")
            logger.info(f"   User: {msg.get('from', {}).get('id')} - Chat: {msg.get('chat', {}).get('id')}")
            logger.info(f"   Text: {msg.get('text', 'N/A')}")
        elif 'callback_query' in json_data:
            logger.info(f"ðŸ”˜ Tipo: callback_query")
        elif 'edited_message' in json_data:
            logger.info(f"âœï¸ Tipo: edited_message")
        else:
            logger.info(f"â“ Tipo sconosciuto: {list(json_data.keys())}")
        
        # Crea Update object
        update = Update.de_json(json_data, bot_application.bot)
        
        # Gestione event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("Loop chiuso")
        except RuntimeError:
            logger.info("ðŸ”„ Creo nuovo event loop")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        # Processa update
        logger.info("âš™ï¸ Invio update al bot...")
        loop.run_until_complete(bot_application.process_update(update))
        logger.info("âœ… Update processato con successo")
        
        return 'ok', 200
        
    except Exception as e:
        logger.error(f"âŒ ERRORE WEBHOOK: {e}", exc_info=True)
        import traceback
        logger.error(traceback.format_exc())
        return f'Error: {str(e)}', 500


# ============================================================================
# INIZIALIZZAZIONE BOT AL BOOT
# ============================================================================

if __name__ != '__main__':
    logger.info("=" * 70)
    logger.info("ðŸš€ Inizializzazione bot (Gunicorn context)")
    logger.info("=" * 70)
    
    try:
        # Crea event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        logger.info("âœ… Event loop creato")
        
        # Inizializza bot
        logger.info("ðŸ”§ Chiamata setup_bot()...")
        bot_application = loop.run_until_complete(setup_bot())
        
        if bot_application:
            logger.info("âœ… Bot inizializzato con successo!")
            logger.info(f"âœ… Bot ID: {bot_application.bot.id}")
            
            # Aggiorna la variabile globale in main.py
            import main
            main.bot_application = bot_application
            logger.info("âœ… bot_application sincronizzato con main.py")
        else:
            logger.error("âŒ setup_bot() ha ritornato None")
            raise RuntimeError("Bot initialization failed")
            
    except Exception as e:
        logger.error(f"âŒ Errore inizializzazione: {e}", exc_info=True)
        raise
    
    logger.info("=" * 70)
    logger.info("âœ… WSGI pronto")
    logger.info("=" * 70)

# Test locale
if __name__ == '__main__':
    logger.info("ðŸ§ª ModalitÃ  TEST locale")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot_application = loop.run_until_complete(setup_bot())
    
    import main
    main.bot_application = bot_application
    
    app.run(host='0.0.0.0', port=10000, debug=True)

# End wsgi.py
