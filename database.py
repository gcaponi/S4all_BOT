"""
Database Module - PostgreSQL con SQLAlchemy
Gestisce: user_tags, authorized_users, ordini_confermati, access_code
"""
import os
import logging
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Float, inspect
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, scoped_session

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURAZIONE DATABASE
# ============================================================================

DATABASE_URL = os.environ.get('DATABASE_URL')

if not DATABASE_URL:
    logger.error("âŒ DATABASE_URL non trovato nelle variabili ambiente!")
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
    user_name = Column(String(200), nullable=True)  # Nome completo
    username = Column(String(100), nullable=True)   # @username
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

def migrate_user_tags_add_profile_columns():
    """Migrazione: Aggiunge user_name e username a user_tags esistente"""
    session = SessionLocal()
    try:
        # Controlla se migrazione è necessaria
        inspector = inspect(session.bind)
        columns = inspector.get_columns('user_tags')
        existing_columns = [col['name'] for col in columns]
        
        if 'user_name' in existing_columns and 'username' in existing_columns:
            logger.info("✅ Tabella user_tags già migrata")
            return True
            
        logger.info("🔄 Inizio migrazione user_tags...")
        
        # Aggiungi nuove colonne
        session.execute("ALTER TABLE user_tags ADD COLUMN IF NOT EXISTS user_name VARCHAR(200)")
        session.execute("ALTER TABLE user_tags ADD COLUMN IF NOT EXISTS username VARCHAR(100)")
        session.commit()
        
        logger.info("✅ Migrazione user_tags completata")
        return True
        
    except Exception as e:
        session.rollback()
        logger.error(f"❌ Errore migrazione user_tags: {e}")
        return False
    finally:
        session.close()

class ChatSession(Base):
    """Tabella chat_sessions - Tracking sessioni e auto-messages"""
    __tablename__ = 'chat_sessions'
    
    chat_id = Column(String(50), primary_key=True, index=True)
    admin_active = Column(Integer, default=0)  # 0=False, 1=True (SQLite compatibility)
    last_admin_time = Column(DateTime, nullable=True)
    last_auto_msg_time = Column(DateTime, default=datetime.utcnow)

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
    """Crea tutte le tabelle se non esistono + migrazione automatica"""
    try:
        # Crea tabelle base
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Database inizializzato")
        
        # MIGRAZIONE AUTOMATICA
        migrate_user_tags_add_profile_columns()
            
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

def set_user_tag(user_id: int, tag: str):
    """Imposta tag per un user"""
    session = SessionLocal()
    try:
        user = session.query(UserTag).filter_by(user_id=str(user_id)).first()
        
        if user:
            user.tag = tag
            user.user_name = user_name  # 🆕 Aggiorna nome
            user.username = username    # 🆕 Aggiorna username
            user.updated_at = datetime.utcnow()
        else:
            user = UserTag(
                user_id=str(user_id), 
                tag=tag,
                user_name=user_name,    # 🆕 Salva nome
                username=username       # 🆕 Salva username
            )

        session.commit()
        logger.info(f"âœ… User {user_id} registrato con tag: {tag}")
    except Exception as e:
        session.rollback()
        logger.error(f"âŒ Errore set_user_tag: {e}")
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
            logger.info(f"âœ… Tag rimosso per user {user_id}")
            return True
        return False
    except Exception as e:
        session.rollback()
        logger.error(f"âŒ Errore remove_user_tag: {e}")
        return False
    finally:
        session.close()

def load_user_tags() -> dict:
    """Carica tutti i tag (per compatibilitÃ  con vecchio codice)"""
    session = SessionLocal()
    try:
        users = session.query(UserTag).all()
        return {
            user.user_id: {
                'tag': user.tag,
                'user_name': user.user_name,
                'username': user.username,
                'created_at': user.created_at,
                'updated_at': user.updated_at
            } 
            for user in users
        }
    finally:
        session.close()
        
def load_user_tags_simple() -> dict:
    """Carica solo {user_id: tag} per retrocompatibilità"""
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
    """Verifica se user Ã¨ autorizzato"""
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
            logger.info(f"âœ… User {user_id} autorizzato")
            return True
        return False
    except Exception as e:
        session.rollback()
        logger.error(f"âŒ Errore authorize_user: {e}")
        return False
    finally:
        session.close()

def load_authorized_users() -> dict:
    """Carica tutti gli utenti autorizzati (per compatibilitÃ )"""
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
# FUNZIONI ORDINI CONFERMATI - CLEAR ORDINI
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
        logger.info(f"âœ… Ordine salvato: {user_name} ({user_id})")
    except Exception as e:
        session.rollback()
        logger.error(f"âŒ Errore add_ordine: {e}")
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
        
# CLEAR ORDINI
def clear_old_orders(days=1):
    """Cancella ordini più vecchi di N giorni"""
    from datetime import timedelta
    
    session = SessionLocal()
    try:
        cutoff_date = datetime.now() - timedelta(days=days)
        
        deleted = session.query(OrdineConfermato).filter(
            OrdineConfermato.timestamp < cutoff_date
        ).delete()
        
        session.commit()
        logger.info(f"🗑️ Cancellati {deleted} ordini più vecchi di {days} giorni")
        return deleted
    except Exception as e:
        session.rollback()
        logger.error(f"❌ Errore clear_old_orders: {e}")
        return 0
    finally:
        session.close()

# ============================================================================
# GESTIONE MULTI-ADMIN - DOPO load_access_code()
# ============================================================================

def init_admins_table():
    """Già gestito da Base.metadata.create_all in init_db()"""
    pass

def add_admin(user_id: int, added_by: int = None, is_super: bool = False) -> bool:
    session = SessionLocal()
    try:
        admin = session.query(Admin).filter_by(user_id=str(user_id)).first()
        if not admin:
            admin = Admin(user_id=str(user_id), added_by=str(added_by) if added_by else None, is_super=1 if is_super else 0)
            session.add(admin)
            session.commit()
            logger.info(f"✅ Admin aggiunto: {user_id}")
            return True
        return False
    except Exception as e:
        session.rollback()
        logger.error(f"❌ add_admin: {e}")
        return False
    finally:
        session.close()

def remove_admin(user_id: int) -> bool:
    session = SessionLocal()
    try:
        admin = session.query(Admin).filter_by(user_id=str(user_id)).first()
        if not admin or admin.is_super:
            return False
        session.delete(admin)
        session.commit()
        return True
    except Exception as e:
        session.rollback()
        return False
    finally:
        session.close()

def is_admin(user_id: int) -> bool:
    session = SessionLocal()
    try:
        return session.query(Admin).filter_by(user_id=str(user_id)).first() is not None
    finally:
        session.close()

def is_super_admin(user_id: int) -> bool:
    session = SessionLocal()
    try:
        admin = session.query(Admin).filter_by(user_id=str(user_id)).first()
        return admin is not None and admin.is_super == 1
    finally:
        session.close()

def get_all_admins() -> list:
    session = SessionLocal()
    try:
        admins = session.query(Admin).order_by(Admin.is_super.desc()).all()
        return [{'user_id': int(a.user_id), 'added_by': int(a.added_by) if a.added_by else None, 'added_at': a.added_at, 'is_super': bool(a.is_super)} for a in admins]
    finally:
        session.close()

# ============================================================================
# LOGGING CLASSIFICAZIONI
# ============================================================================
def log_classification(text: str, intent: str, confidence: float):
    """Salva log classificazione in PostgreSQL"""
    session = SessionLocal()
    try:
        log_entry = Classification(
            text=text,
            intent=intent,
            confidence=str(round(confidence, 2))
        )
        session.add(log_entry)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"❌ log_classification: {e}")
    finally:
        session.close()

def get_classification_stats() -> dict:
    """Ottieni statistiche classificazioni"""
    session = SessionLocal()
    try:
        from sqlalchemy import func
        
        total = session.query(func.count(Classification.id)).scalar()
        
        if total == 0:
            return {'total_classifications': 0, 'fallback_rate': 0.0, 'by_intent': {}}
        
        fallback_count = session.query(func.count(Classification.id)).filter(
            Classification.intent == 'fallback'
        ).scalar()
        
        # Stats per intent
        intent_stats = session.query(
            Classification.intent,
            func.count(Classification.id).label('count'),
            func.avg(func.cast(Classification.confidence, Float)).label('avg_conf')
        ).group_by(Classification.intent).all()
        
        by_intent = {}
        for intent, count, avg_conf in intent_stats:
            by_intent[intent] = {
                'count': count,
                'avg_confidence': float(avg_conf) if avg_conf else 0.0
            }
        
        return {
            'total_classifications': total,
            'fallback_rate': fallback_count / total if total > 0 else 0.0,
            'by_intent': by_intent
        }
    except Exception as e:
        logger.error(f"❌ get_classification_stats: {e}")
        return {'total_classifications': 0, 'fallback_rate': 0.0, 'by_intent': {}}
    finally:
        session.close()

def get_low_confidence_cases(threshold: float = 0.7, limit: int = 20) -> list:
    """Ottieni casi con bassa confidence"""
    session = SessionLocal()
    try:
        cases = session.query(Classification).filter(
            func.cast(Classification.confidence, Float) < threshold
        ).order_by(Classification.timestamp.desc()).limit(limit).all()
        
        return [
            {
                'text': c.text,
                'intent': c.intent,
                'confidence': float(c.confidence),
                'timestamp': c.timestamp.isoformat()
            }
            for c in cases
        ]
    except Exception as e:
        logger.error(f"❌ get_low_confidence_cases: {e}")
        return []
    finally:
        session.close()

def get_cases_by_intent(intent: str = None, limit: int = 100) -> list:
    """Ottieni tutti i casi per intent specifico"""
    session = SessionLocal()
    try:
        query = session.query(Classification)
        
        if intent:
            query = query.filter(Classification.intent == intent)
        
        cases = query.order_by(Classification.timestamp.desc()).limit(limit).all()
        
        return [
            {
                'text': c.text,
                'intent': c.intent,
                'confidence': float(c.confidence),
                'timestamp': c.timestamp.isoformat()
            }
            for c in cases
        ]
    except Exception as e:
        logger.error(f"❌ get_cases_by_intent: {e}")
        return []
    finally:
        session.close()

def get_confidence_distribution(intent: str) -> dict:
    """Distribuzione confidence per intent"""
    session = SessionLocal()
    try:
        from sqlalchemy import func, case
        
        stats = session.query(
            func.count(Classification.id).label('total'),
            func.avg(func.cast(Classification.confidence, Float)).label('avg_conf'),
            func.min(func.cast(Classification.confidence, Float)).label('min_conf'),
            func.max(func.cast(Classification.confidence, Float)).label('max_conf'),
            func.sum(case((func.cast(Classification.confidence, Float) < 0.5, 1), else_=0)).label('very_low'),
            func.sum(case((func.cast(Classification.confidence, Float).between(0.5, 0.7), 1), else_=0)).label('low'),
            func.sum(case((func.cast(Classification.confidence, Float).between(0.7, 0.85), 1), else_=0)).label('medium'),
            func.sum(case((func.cast(Classification.confidence, Float) >= 0.85, 1), else_=0)).label('high')
        ).filter(Classification.intent == intent).first()
        
        if not stats or stats.total == 0:
            return {}
        
        return {
            'total': stats.total,
            'avg_confidence': float(stats.avg_conf) if stats.avg_conf else 0.0,
            'min_confidence': float(stats.min_conf) if stats.min_conf else 0.0,
            'max_confidence': float(stats.max_conf) if stats.max_conf else 0.0,
            'very_low': stats.very_low or 0,
            'low': stats.low or 0,
            'medium': stats.medium or 0,
            'high': stats.high or 0
        }
    except Exception as e:
        logger.error(f"❌ get_confidence_distribution: {e}")
        return {}
    finally:
        session.close()

def aggregate_monthly_stats(year_month: str = None):
    """
    Aggrega stats mensili prima di cleanup
    year_month formato: "2026-02" (se None, aggrega mese precedente)
    """
    from calendar import monthrange
    
    session = SessionLocal()
    try:
        # Se non specificato, aggrega il mese precedente
        if not year_month:
            now = datetime.utcnow()
            if now.month == 1:
                year = now.year - 1
                month = 12
            else:
                year = now.year
                month = now.month - 1
            year_month = f"{year}-{month:02d}"
        
        # Verifica se già aggregato
        existing = session.query(ClassificationMonthlyStat).filter_by(year_month=year_month).first()
        if existing:
            logger.info(f"⚠️ Stats per {year_month} già aggregate")
            return
        
        # Calcola date range per il mese
        year, month = map(int, year_month.split('-'))
        start_date = datetime(year, month, 1)
        last_day = monthrange(year, month)[1]
        end_date = datetime(year, month, last_day, 23, 59, 59)
        
        # Query dati del mese
        from sqlalchemy import func
        
        total = session.query(func.count(Classification.id)).filter(
            Classification.timestamp >= start_date,
            Classification.timestamp <= end_date
        ).scalar()
        
        if total == 0:
            logger.info(f"📊 Nessun dato per {year_month}, skip aggregazione")
            return
        
        fallback_count = session.query(func.count(Classification.id)).filter(
            Classification.timestamp >= start_date,
            Classification.timestamp <= end_date,
            Classification.intent == 'fallback'
        ).scalar()
        
        fallback_rate = (fallback_count / total * 100) if total > 0 else 0.0
        
        # Stats per intent
        intent_stats = session.query(
            Classification.intent,
            func.count(Classification.id).label('count'),
            func.avg(func.cast(Classification.confidence, Float)).label('avg_conf')
        ).filter(
            Classification.timestamp >= start_date,
            Classification.timestamp <= end_date
        ).group_by(Classification.intent).all()
        
        by_intent = {}
        for intent, count, avg_conf in intent_stats:
            by_intent[intent] = {
                'count': count,
                'avg_confidence': round(float(avg_conf) if avg_conf else 0.0, 2)
            }
        
        # Salva stats aggregate
        monthly_stat = ClassificationMonthlyStat(
            year_month=year_month,
            total_classifications=total,
            fallback_count=fallback_count,
            fallback_rate=f"{fallback_rate:.1f}",
            by_intent_json=json.dumps(by_intent, ensure_ascii=False)
        )
        
        session.add(monthly_stat)
        session.commit()
        
        logger.info(f"📊 Stats aggregate per {year_month}: {total} classificazioni, {fallback_rate:.1f}% fallback")
        
    except Exception as e:
        session.rollback()
        logger.error(f"❌ aggregate_monthly_stats: {e}")
    finally:
        session.close()
        
def cleanup_old_classifications(days: int = 30) -> int:
    """
    Cancella classificazioni più vecchie di N giorni
    Prima aggrega stats mensili per i mesi che verranno cancellati
    """
    from datetime import timedelta
    from calendar import monthrange
    
    session = SessionLocal()
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        
        # STEP 1: Identifica i mesi che verranno cancellati
        oldest = session.query(func.min(Classification.timestamp)).filter(
            Classification.timestamp < cutoff_date
        ).scalar()
        
        if oldest:
            # Aggrega ogni mese che verrà cancellato
            current_date = oldest.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            while current_date < cutoff_date:
                year_month = current_date.strftime("%Y-%m")
                
                # Aggrega solo se ci sono dati e non già aggregato
                existing = session.query(ClassificationMonthlyStat).filter_by(year_month=year_month).first()
                if not existing:
                    logger.info(f"📊 Aggregazione pre-cleanup per {year_month}")
                    session.close()  # Chiudi sessione corrente
                    aggregate_monthly_stats(year_month)
                    session = SessionLocal()  # Riapri nuova sessione
                
                # Passa al mese successivo
                if current_date.month == 12:
                    current_date = current_date.replace(year=current_date.year + 1, month=1)
                else:
                    current_date = current_date.replace(month=current_date.month + 1)
        
        # STEP 2: Cancella i dettagli
        deleted = session.query(Classification).filter(
            Classification.timestamp < cutoff_date
        ).delete()
        
        session.commit()
        logger.info(f"🗑️ Cancellate {deleted} classificazioni più vecchie di {days} giorni")
        return deleted
        
    except Exception as e:
        session.rollback()
        logger.error(f"❌ cleanup_old_classifications: {e}")
        return 0
    finally:
        session.close()
def get_monthly_trends(months: int = 6) -> list:
    """
    Ottieni trend storici ultimi N mesi
    Ritorna lista ordinata dal più recente al più vecchio
    """
    session = SessionLocal()
    try:
        trends = session.query(ClassificationMonthlyStat).order_by(
            ClassificationMonthlyStat.year_month.desc()
        ).limit(months).all()
        
        result = []
        for trend in trends:
            by_intent = json.loads(trend.by_intent_json) if trend.by_intent_json else {}
            result.append({
                'year_month': trend.year_month,
                'total': trend.total_classifications,
                'fallback_count': trend.fallback_count,
                'fallback_rate': trend.fallback_rate,
                'by_intent': by_intent,
                'created_at': trend.created_at.isoformat()
            })
        
        return result
    except Exception as e:
        logger.error(f"❌ get_monthly_trends: {e}")
        return []
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
        logger.info(f"âœ… Config '{key}' aggiornata")
    except Exception as e:
        session.rollback()
        logger.error(f"âŒ Errore set_config: {e}")
    finally:
        session.close()

def load_access_code() -> str:
    """Carica access code (compatibilitÃ )"""
    import secrets
    
    code = get_config('access_code')
    if not code:
        code = secrets.token_urlsafe(12)
        set_config('access_code', code)
    return code

def save_access_code(code: str):
    """Salva access code (compatibilitÃ )"""
    set_config('access_code', code)

# ============================================================================
# MODELLO ADMIN
# ============================================================================
class Admin(Base):
    """Tabella admins - Gestione multi-admin"""
    __tablename__ = 'admins'
    
    user_id = Column(String(50), primary_key=True, index=True)
    added_by = Column(String(50), nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow)
    is_super = Column(Integer, default=0)  # 0=False, 1=True (SQLite compatibility)

# ============================================================================
# DASHBOARD LOGS
# ============================================================================
class Classification(Base):
    """Tabella classifications - Log classificazioni intent"""
    __tablename__ = 'classifications'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    text = Column(Text, nullable=False)
    intent = Column(String(50), nullable=False, index=True)
    confidence = Column(String(10), nullable=False)  # Salviamo come string per compatibilità
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

class ClassificationMonthlyStat(Base):
    """Tabella classification_monthly_stats - Stats aggregate mensili"""
    __tablename__ = 'classification_monthly_stats'
    
    year_month = Column(String(7), primary_key=True)  # Formato: "2026-02"
    total_classifications = Column(Integer, default=0)
    fallback_count = Column(Integer, default=0)
    fallback_rate = Column(String(10))  # Percentuale come string (es. "27.6")
    by_intent_json = Column(Text)  # JSON con stats per intent
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
# ============================================================================
# COMPATIBILITÀ CON JSON (per facilitare migrazione)
# ============================================================================
def save_user_tags(tags_dict):
    """CompatibilitÃ  - non fa nulla, giÃ  salvato nel DB"""
    pass

def save_authorized_users(users_dict):
    """CompatibilitÃ  - non fa nulla, giÃ  salvato nel DB"""
    pass

def init_chat_sessions_table():
    """Crea tabella tracking sessioni chat - già gestito da Base.metadata.create_all"""
    pass  # La tabella viene creata automaticamente da init_db()

def set_admin_active(chat_id, active=True):
    """Imposta admin attivo/inattivo in chat"""
    session = SessionLocal()
    try:
        chat_session = session.query(ChatSession).filter_by(chat_id=str(chat_id)).first()
        
        if chat_session:
            chat_session.admin_active = 1 if active else 0
            chat_session.last_admin_time = datetime.utcnow()
        else:
            chat_session = ChatSession(
                chat_id=str(chat_id),
                admin_active=1 if active else 0,
                last_admin_time=datetime.utcnow()
            )
            session.add(chat_session)
        
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"❌ Errore set_admin_active: {e}")
    finally:
        session.close()

def get_chat_session(chat_id):
    """Ottieni stato sessione: (admin_active, last_admin_time, last_auto_msg_time)"""
    session = SessionLocal()
    try:
        chat_session = session.query(ChatSession).filter_by(chat_id=str(chat_id)).first()
        
        if not chat_session:
            return None
        
        # Ritorna tupla come prima (per compatibilità)
        return (
            bool(chat_session.admin_active),  # Convert 0/1 to False/True
            chat_session.last_admin_time,
            chat_session.last_auto_msg_time
        )
    finally:
        session.close()

def update_auto_message_time(chat_id):
    """Aggiorna timestamp ultimo auto-message"""
    session = SessionLocal()
    try:
        chat_session = session.query(ChatSession).filter_by(chat_id=str(chat_id)).first()
        
        if chat_session:
            chat_session.last_auto_msg_time = datetime.utcnow()
        else:
            chat_session = ChatSession(
                chat_id=str(chat_id),
                last_auto_msg_time=datetime.utcnow()
            )
            session.add(chat_session)
        
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"❌ Errore update_auto_message_time: {e}")
    finally:
        session.close()

# End database.py
