# wsgi.py - entrypoint per Gunicorn
from main import app, start_bot_thread

# Avvia il bot Telegram in thread separato (prima che Gunicorn serva Flask)
start_bot_thread()

if __name__ == "__main__":
    # Esecuzione locale (sviluppo)
    app.run(host="0.0.0.0", port=8000, debug=False)
