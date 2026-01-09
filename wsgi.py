"""
WSGI Entry Point - Versione SEMPLICE (come l'originale funzionante)
Aggiornato solo per python-telegram-bot 21.7
"""
import logging
import os
from main import app

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info(f"wsgi: Avvio applicazione (pid={os.getpid()})")

# Il bot si inizializza al primo webhook (LAZY loading come prima)

if __name__ == "__main__":
    app.run()

# End wsgi.py
