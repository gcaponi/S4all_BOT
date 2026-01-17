"""
Database Module - PostgreSQL con SQLAlchemy
Gestisce: user_tags, authorized_users, ordini_confermati, access_code
"""
import os
import logging
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, scoped_session

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURAZIONE DATABASE
# ============================================================================

DATABASE_URL = os.environ.get('DATABASE_URL')

if not DATABASE_URL:
    logger.error("❌ DATABASE_URL non trovato nelle variabili ambiente!")
    raise RuntimeError("DATABASE_URL non configurato")

# Fix per Render (usa postgresql:// invece di postgres://)
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

# Crea engine
engine = create_engine(DATABASE_URL, pool_pre_ping=True, echo=False)

# Crea session factory
SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))

# Base per i modelli
Base = declarative_base()

# ============================================================================
# MODELLI DATABASE
# ============================================================================

class UserTag(Base):
    """Tabella user_tags - Tag clienti per scontistica"""
    __tablename__ = 'user_tags'
    
    user_id = Column(String(50), primary_key=True, index=True)
    tag = Column(String(20), nullable=False)
    username = Column(String(100))  # Username Telegram (opzionale)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class AuthorizedUser(Base):
    """Tabella authorized_users - Utenti autorizzati bot"""
    __tablename__ = 'authorized_users'
    
    user_id = Column(String(50), primary_key=True, index=True)
    name = Column(String(200))
    username = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)

class OrdineConfermato(Base):
    """Tabella ordini_confermati - Ordini confermati dai clienti"""
    __tablename__ = 'ordini_confermati'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(50), nullable=False, index=True)
    user_name = Column(String(200))
    username = Column(String(100))
    message = Column(Text)
    chat_id = Column(String(50))
    message_id = Column(String(50))
    data = Column(String(20))  # YYYY-MM-DD
    ora = Column(String(20))   # HH:MM:SS
    timestamp = Column(DateTime, default=datetime.utcnow)

class AppConfig(Base):
    """Tabella app_config - Configurazioni app (access_code, ecc.)"""
    __tablename__ = 'app_config'
    
    key = Column(String(100), primary_key=True)
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# ============================================================================
# INIZIALIZZAZIONE DATABASE
# ============================================================================

def init_db():
    """Crea tutte le tabelle se non esistono"""
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Database inizializzato")
        return True
    except Exception as e:
        logger.error(f"❌ Errore inizializzazione database: {e}")
        return False

# ============================================================================
# FUNZIONI USER TAGS
# ============================================================================

def get_user_tag(user_id: int) -> str:
    """Ottieni tag di un user"""
    session = SessionLocal()
    try:
        user = session.query(UserTag).filter_by(user_id=str(user_id)).first()
        return user.tag if user else None
    finally:
        session.close()

def set_user_tag(user_id: int, tag: str, username: str = None):
    """Imposta tag per un user (versione robusta con username opzionale)"""
    session = SessionLocal()
    try:
        user = session.query(UserTag).filter_by(user_id=str(user_id)).first()
        
        if user:
            # Aggiorna esistente
            user.tag = tag
            if username:
                user.username = username
            user.updated_at = datetime.utcnow()
        else:
            # Crea nuovo
            user = UserTag(user_id=str(user_id), tag=tag, username=username)
            session.add(user)
        
        session.commit()
        logger.info(f"✅ User {user_id} registrato con tag: {tag}")
        return True
    except Exception as e:
        session.rollback()
        logger.error(f"❌ Errore set_user_tag: {e}")
        return False
    finally:
        session.close()

def remove_user_tag(user_id: int) -> bool:
    """Rimuovi tag di un user"""
    session = SessionLocal()
    try:
        user = session.query(UserTag).filter_by(user_id=str(user_id)).first()
        if user:
            session.delete(user)
            session.commit()
            logger.info(f"✅ Tag rimosso per user {user_id}")
            return True
        return False
    except Exception as e:
        session.rollback()
        logger.error(f"❌ Errore remove_user_tag: {e}")
        return False
    finally:
        session.close()

def load_user_tags() -> dict:
    """Carica tutti i tag (per compatibilità con vecchio codice)"""
    session = SessionLocal()
    try:
        users = session.query(UserTag).all()
        return {user.user_id: user.tag for user in users}
    finally:
        session.close()

# ============================================================================
# FUNZIONI AUTHORIZED USERS
# ============================================================================

def is_user_authorized(user_id: int) -> bool:
    """Verifica se user è autorizzato"""
    session = SessionLocal()
    try:
        user = session.query(AuthorizedUser).filter_by(user_id=str(user_id)).first()
        return user is not None
    finally:
        session.close()

def authorize_user(user_id: int, first_name: str = None, last_name: str = None, username: str = None) -> bool:
    """Autorizza un nuovo user"""
    session = SessionLocal()
    try:
        user = session.query(AuthorizedUser).filter_by(user_id=str(user_id)).first()
        
        if not user:
            full_name = f"{first_name or ''} {last_name or ''}".strip() or "Sconosciuto"
            user = AuthorizedUser(
                user_id=str(user_id),
                name=full_name,
                username=username
            )
            session.add(user)
            session.commit()
            logger.info(f"✅ User {user_id} autorizzato")
            return True
        return False
    except Exception as e:
        session.rollback()
        logger.error(f"❌ Errore authorize_user: {e}")
        return False
    finally:
        session.close()

def load_authorized_users() -> dict:
    """Carica tutti gli utenti autorizzati (per compatibilità)"""
    session = SessionLocal()
    try:
        users = session.query(AuthorizedUser).all()
        return {
            user.user_id: {
                "id": int(user.user_id),
                "name": user.name,
                "username": user.username
            }
            for user in users
        }
    finally:
        session.close()

def revoke_user(user_id: int) -> bool:
    """Revoca autorizzazione user"""
    session = SessionLocal()
    try:
        user = session.query(AuthorizedUser).filter_by(user_id=str(user_id)).first()
        if user:
            session.delete(user)
            session.commit()
            return True
        return False
    finally:
        session.close()

# ============================================================================
# FUNZIONI ORDINI CONFERMATI
# ============================================================================

def add_ordine_confermato(user_id: int, user_name: str, username: str, 
                         message_text: str, chat_id: int, message_id: int):
    """Registra un ordine confermato"""
    session = SessionLocal()
    try:
        ordine = OrdineConfermato(
            user_id=str(user_id),
            user_name=user_name,
            username=username,
            message=message_text,
            chat_id=str(chat_id),
            message_id=str(message_id),
            data=datetime.now().strftime("%Y-%m-%d"),
            ora=datetime.now().strftime("%H:%M:%S")
        )
        session.add(ordine)
        session.commit()
        logger.info(f"✅ Ordine salvato: {user_name} ({user_id})")
    except Exception as e:
        session.rollback()
        logger.error(f"❌ Errore add_ordine: {e}")
    finally:
        session.close()

def get_ordini_oggi() -> list:
    """Recupera ordini confermati oggi"""
    session = SessionLocal()
    try:
        oggi = datetime.now().strftime("%Y-%m-%d")
        ordini = session.query(OrdineConfermato).filter_by(data=oggi).all()
        
        return [
            {
                "user_id": o.user_id,
                "user_name": o.user_name,
                "username": o.username,
                "message": o.message,
                "chat_id": o.chat_id,
                "message_id": o.message_id,
                "data": o.data,
                "ora": o.ora
            }
            for o in ordini
        ]
    finally:
        session.close()

# ============================================================================
# FUNZIONI APP CONFIG (access_code, ecc.)
# ============================================================================

def get_config(key: str, default: str = None) -> str:
    """Ottieni valore configurazione"""
    session = SessionLocal()
    try:
        config = session.query(AppConfig).filter_by(key=key).first()
        return config.value if config else default
    finally:
        session.close()

def set_config(key: str, value: str):
    """Imposta valore configurazione"""
    session = SessionLocal()
    try:
        config = session.query(AppConfig).filter_by(key=key).first()
        
        if config:
            config.value = value
            config.updated_at = datetime.utcnow()
        else:
            config = AppConfig(key=key, value=value)
            session.add(config)
        
        session.commit()
        logger.info(f"✅ Config '{key}' aggiornata")
    except Exception as e:
        session.rollback()
        logger.error(f"❌ Errore set_config: {e}")
    finally:
        session.close()

def load_access_code() -> str:
    """Carica access code (compatibilità)"""
    import secrets
    
    code = get_config('access_code')
    if not code:
        code = secrets.token_urlsafe(12)
        set_config('access_code', code)
    return code

def save_access_code(code: str):
    """Salva access code (compatibilità)"""
    set_config('access_code', code)

# ============================================================================
# COMPATIBILITÃ€ CON JSON (per facilitare migrazione)
# ============================================================================

def save_user_tags(tags_dict):
    """Compatibilità - non fa nulla, già salvato nel DB"""
    pass

def save_authorized_users(users_dict):
    """Compatibilità - non fa nulla, già salvato nel DB"""
    pass

# End database.py
