"""
Error Handlers Module
Gestione centralizzata degli errori con logging specifico.
"""

import logging
import functools
import traceback
from typing import Callable, Any

logger = logging.getLogger(__name__)

# ============================================================================
# DECORATORS
# ============================================================================

def log_errors(operation_name: str = None, reraise: bool = True):
    """
    Decorator per logging dettagliato degli errori.
    
    Args:
        operation_name: Nome dell'operazione (se None, usa il nome della funzione)
        reraise: Se True, rilancia l'eccezione dopo averla loggata
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            op_name = operation_name or func.__name__
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(
                    f"❌ Errore in '{op_name}': {type(e).__name__}: {str(e)}",
                    exc_info=True
                )
                if reraise:
                    raise
                return None
        return wrapper
    return decorator

def async_log_errors(operation_name: str = None, reraise: bool = True):
    """
    Decorator async per logging dettagliato degli errori.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            op_name = operation_name or func.__name__
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error(
                    f"❌ Errore in '{op_name}': {type(e).__name__}: {str(e)}",
                    exc_info=True
                )
                if reraise:
                    raise
                return None
        return wrapper
    return decorator

def safe_execute(default_return=None, operation_name: str = None, log_level: str = "error"):
    """
    Esegue una funzione in modo sicuro, ritornando un valore di default in caso di errore.
    
    Args:
        default_return: Valore da ritornare in caso di errore
        operation_name: Nome dell'operazione per il logging
        log_level: Livello di logging (debug, info, warning, error)
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            op_name = operation_name or func.__name__
            try:
                return func(*args, **kwargs)
            except Exception as e:
                log_msg = f"⚠️ Errore in '{op_name}': {type(e).__name__}: {str(e)}"
                if log_level == "debug":
                    logger.debug(log_msg, exc_info=True)
                elif log_level == "info":
                    logger.info(log_msg, exc_info=True)
                elif log_level == "warning":
                    logger.warning(log_msg, exc_info=True)
                else:
                    logger.error(log_msg, exc_info=True)
                return default_return
        return wrapper
    return decorator

def async_safe_execute(default_return=None, operation_name: str = None, log_level: str = "error"):
    """
    Versione async di safe_execute.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            op_name = operation_name or func.__name__
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                log_msg = f"⚠️ Errore in '{op_name}': {type(e).__name__}: {str(e)}"
                if log_level == "debug":
                    logger.debug(log_msg, exc_info=True)
                elif log_level == "info":
                    logger.info(log_msg, exc_info=True)
                elif log_level == "warning":
                    logger.warning(log_msg, exc_info=True)
                else:
                    logger.error(log_msg, exc_info=True)
                return default_return
        return wrapper
    return decorator

# ============================================================================
# CONTEXT MANAGERS
# ============================================================================

class ErrorContext:
    """
    Context manager per gestire errori con logging contestuale.
    
    Usage:
        with ErrorContext("operazione_database", reraise=False) as ctx:
            result = db.query()
            ctx.success = True
    """
    
    def __init__(self, operation_name: str, reraise: bool = True, default_return=None):
        self.operation_name = operation_name
        self.reraise = reraise
        self.default_return = default_return
        self.success = False
        self.error = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_val:
            self.error = exc_val
            logger.error(
                f"❌ Errore in '{self.operation_name}': {exc_type.__name__}: {str(exc_val)}",
                exc_info=True
            )
            if self.reraise:
                return False  # Rilancia l'eccezione
            return True  # Sopprime l'eccezione
        elif self.success:
            logger.debug(f"✅ '{self.operation_name}' completato con successo")
        return True

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def format_exception(e: Exception, include_traceback: bool = False) -> str:
    """
    Formatta un'eccezione in una stringa leggibile.
    
    Args:
        e: L'eccezione da formattare
        include_traceback: Se includere il traceback completo
    
    Returns:
        Stringa formattata
    """
    msg = f"{type(e).__name__}: {str(e)}"
    if include_traceback:
        tb = traceback.format_exc()
        msg += f"\nTraceback:\n{tb}"
    return msg

def log_db_error(operation: str, table: str = None, details: dict = None):
    """
    Log specifico per errori database.
    
    Args:
        operation: Operazione che ha causato l'errore (es. 'insert', 'update')
        table: Nome della tabella coinvolta
        details: Dettagli aggiuntivi
    """
    context = f" [{table}]" if table else ""
    detail_str = f" | Dettagli: {details}" if details else ""
    logger.error(f"🗄️ Errore DB{context} in operazione '{operation}'{detail_str}")

def log_api_error(endpoint: str, status_code: int = None, response: str = None):
    """
    Log specifico per errori API/chiamate esterne.
    
    Args:
        endpoint: Endpoint chiamato
        status_code: Codice HTTP di risposta
        response: Risposta dell'API
    """
    status_str = f" (HTTP {status_code})" if status_code else ""
    response_str = f" | Risposta: {response[:200]}..." if response else ""
    logger.error(f"🌐 Errore API{status_str} per endpoint: {endpoint}{response_str}")

def log_validation_error(field: str, value: Any, expected_type: str = None):
    """
    Log specifico per errori di validazione.
    
    Args:
        field: Campo che non ha passato la validazione
        value: Valore ricevuto
        expected_type: Tipo atteso
    """
    expected_str = f" (atteso: {expected_type})" if expected_type else ""
    logger.warning(f"📝 Errore validazione campo '{field}'{expected_str}: valore={value}")

# ============================================================================
# ERROR CLASSES
# ============================================================================

class BotError(Exception):
    """Eccezione base per errori del bot."""
    pass

class DatabaseError(BotError):
    """Errore database."""
    pass

class ValidationError(BotError):
    """Errore di validazione input."""
    pass

class ClassificationError(BotError):
    """Errore durante la classificazione intent."""
    pass

class ExternalAPIError(BotError):
    """Errore chiamata API esterna."""
    pass
