# ==============================
# main.py — PRODUCTION GRADE
# ==============================

import os
import json
import logging
import secrets
import re
import requests
from difflib import SequenceMatcher
from bs4 import BeautifulSoup
from flask import Flask, request

import asyncio
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==============================
# LOGGING
# ==============================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ==============================
# ENV VARS (Render)
# ==============================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
PORT = int(os.environ.get("PORT", "10000"))

if not BOT_TOKEN or not ADMIN_CHAT_ID:
    raise RuntimeError("BOT_TOKEN o ADMIN_CHAT_ID mancanti")

# ==============================
# FILES
# ==============================

AUTHORIZED_USERS_FILE = "authorized_users.json"
ACCESS_CODE_FILE = "access_code.json"
FAQ_FILE = "faq.json"

PASTE_URL = "https://justpaste.it/faq_4all"
FUZZY_THRESHOLD = 0.6

# ==============================
# FLASK
# ==============================

app = Flask(__name__)

# ==============================
# TELEGRAM GLOBALS
# ==============================

application: Application | None = None
bot_username: str = "bot"

# ==============================
# UTILS JSON
# ==============================

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ==============================
# AUTH USERS
# ==============================

def load_authorized_users():
    data = load_json(AUTHORIZED_USERS_FILE, {})
    if isinstance(data, list):
        return {str(uid): {"id"}}
