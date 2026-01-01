# wsgi.py - entrypoint per Gunicorn
# Importa start_bot_thread e avvia il thread del bot una sola volta per processo.
from main import app, start_bot_thread
import logging
import os

logger = logging.getLogger("wsgi")
logger.setLevel(logging.INFO)

# Avvia il bot in background thread (idempotente)
try:
    start_bot_thread()
    logger.info("wsgi: start_bot_thread chiamato (pid=%s)", os.getpid())
except Exception:
    logger.exception("wsgi: errore start_bot_thread")

if __name__ == "__main__":
    # Esecuzione locale (sviluppo)
    app.run(host="0.0.0.0", port=8000, debug=False)
