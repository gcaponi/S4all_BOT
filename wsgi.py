import logging
import os
from main import app

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info(f"wsgi: Avvio applicazione (pid={os.getpid()})")

# Non serve più start_bot_thread - il bot si inizializza al primo webhook

if __name__ == "__main__":
    app.run()
