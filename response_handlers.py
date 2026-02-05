"""
Response Handlers Module
Centralizza la logica di risposta per ridurre codice duplicato
tra business messages, private messages e group messages.
"""

import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from typing import Optional, Callable, Dict, Any

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURAZIONE PRODOTTI
# ============================================================================

# Prodotti che richiedono acqua batteriostatica per preparazione
PRODOTTI_ACQUA_NECESSARIA = [
    'retatrutide', 'tirzepatide', 'semaglutide',
    'gh', 'ormone della crescita', 'ormone crescita',
    'pt141', 'pt-141', 'pt 141', 'bpc157', 'bpc 157', 'bpc-157', 'bpc',
    'melatonan', 'melatonan2', 'melanotan', 'melanotan2', 'melanotan 2',
    'cjc', 'cjc dac', 'cjc-dac', 'mgf', 'peg-mgf', 'peg mgf', 'pegmgf',
    'follistatina', 'igf1', 'igf-1', 'igf 1'
]

# Termini per verificare se ha già menzionato acqua
TERMINI_ACQUA = [
    'acqua batteriostatica', 'acqua', 'batteriostatica', 
    'acqua per preparazione', 'solvente'
]

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def check_needs_acqua(text_lower: str) -> tuple[bool, bool]:
    """
    Verifica se il testo contiene prodotti che necessitano acqua batteriostatica
    e se l'utente ha già menzionato l'acqua.
    
    Returns:
        tuple: (needs_acqua, has_acqua)
    """
    needs_acqua = any(prod in text_lower for prod in PRODOTTI_ACQUA_NECESSARIA)
    has_acqua = any(term in text_lower for term in TERMINI_ACQUA)
    return needs_acqua, has_acqua

def build_order_message(text_lower: str) -> str:
    """
    Costruisce il messaggio per un ordine, includendo avviso acqua se necessario.
    """
    needs_acqua, has_acqua = check_needs_acqua(text_lower)
    
    msg_parts = ["🤔 <b>Sembra un ordine!</b>"]
    
    if needs_acqua and not has_acqua:
        msg_parts.append("\n⚠️ <b>Serve anche confezione di acqua batteriostatica per il prodotto ordinato?</b>")
        logger.info("💧 Prodotto richiede acqua batteriostatica - domanda automatica aggiunta")
    
    msg_parts.append("\nC'è il metodo di pagamento?")
    
    return "\n".join(msg_parts)

def build_order_keyboard(message_id: int, user_id: Optional[int] = None) -> InlineKeyboardMarkup:
    """
    Costruisce la tastiera per la conferma ordine.
    """
    if user_id:
        callback_data = f"pay_ok_{user_id}_{message_id}"
    else:
        callback_data = f"pay_ok_{message_id}"
    
    keyboard = [[
        InlineKeyboardButton("✅ Sì", callback_data=callback_data),
        InlineKeyboardButton("❌ No", callback_data=f"pay_no_{message_id}")
    ]]
    
    return InlineKeyboardMarkup(keyboard)

# ============================================================================
# RESPONSE BUILDERS
# ============================================================================

class ResponseBuilder:
    """
    Classe per costruire risposte standardizzate.
    Centralizza la logica di formattazione dei messaggi.
    """
    
    @staticmethod
    def lista() -> str:
        """Messaggio per richiesta lista prodotti."""
        return "Ciao clicca qui per visualizzare il listino sempre aggiornato https://t.me/+uepM4qLBCrM0YTRk"
    
    @staticmethod
    def ordine(text_lower: str, message_id: int, user_id: Optional[int] = None) -> tuple[str, InlineKeyboardMarkup]:
        """
        Messaggio e tastiera per ordine.
        
        Returns:
            tuple: (message_text, reply_markup)
        """
        message_text = build_order_message(text_lower)
        keyboard = build_order_keyboard(message_id, user_id)
        return message_text, keyboard
    
    @staticmethod
    def conferma_ordine() -> str:
        """Messaggio per conferma ordine."""
        return "✅ Ricevuto! I tempi di spedizione trovi nel nostro FAQ"
    
    @staticmethod
    def faq(domanda: str, risposta: str) -> str:
        """Messaggio per FAQ."""
        return f"✅ <b>{domanda}</b>\n\n{risposta}"
    
    @staticmethod
    def ricerca_prodotti(snippet: str) -> str:
        """Messaggio per ricerca prodotti."""
        return f"📦 <b>Nel listino ho trovato:</b>\n\n{snippet}"
    
    @staticmethod
    def fallback_suggestion(text_lower: str) -> Optional[str]:
        """
        Suggerimento intelligente basato su parole chiave nel fallback.
        Returns None se nessun suggerimento applicabile.
        """
        if any(word in text_lower for word in ['listino', 'catalogo', 'prezzi', 'prodotti']):
            return "📋 Vuoi vedere il listino completo? Scrivi 'lista'"
        
        if any(word in text_lower for word in ['ordina', 'compra', 'acquista', 'voglio']):
            return "🛒 Per fare un ordine, scrivi cosa vorresti acquistare, es: 'voglio 2 fiale di susta'"
        
        if any(word in text_lower for word in ['costa', 'prezzo', 'quanto']):
            return "💰 Per sapere il prezzo di un prodotto, scrivi ad esempio: 'quanto costa testo?'"
        
        if any(word in text_lower for word in ['spedizione', 'consegna', 'tempo', 'giorni']):
            return "🚚 Per info sulle spedizioni, scrivi 'spedizione'"
        
        return None
    
    @staticmethod
    def fallback_default() -> str:
        """Messaggio fallback di default."""
        return (
            "❓ Non ho capito. Prova con:\n"
            "• 'lista' per il catalogo\n"
            "• 'quanto costa X' per un prodotto\n"
            "• Info su spedizioni e pagamenti\n"
            "• Scrivi direttamente cosa vorresti"
        )

# ============================================================================
# HANDLER RESPONSE DISPATCHER
# ============================================================================

class HandlerResponseDispatcher:
    """
    Dispatcher per gestire le risposte in modo uniforme
    tra diversi tipi di handler (business, private, group).
    """
    
    def __init__(self, response_builder: ResponseBuilder = None):
        self.builder = response_builder or ResponseBuilder()
    
    async def send_lista(
        self,
        send_func: Callable,
        parse_mode: Optional[str] = None
    ) -> None:
        """Invia risposta lista prodotti."""
        text = self.builder.lista()
        await send_func(text=text, parse_mode=parse_mode)
    
    async def send_ordine(
        self,
        send_func: Callable,
        text_lower: str,
        message_id: int,
        user_id: Optional[int] = None,
        parse_mode: str = "HTML"
    ) -> None:
        """Invia risposta ordine con tastiera."""
        message_text, keyboard = self.builder.ordine(text_lower, message_id, user_id)
        await send_func(
            text=message_text,
            reply_markup=keyboard,
            parse_mode=parse_mode
        )
    
    async def send_conferma_ordine(
        self,
        send_func: Callable,
        parse_mode: Optional[str] = None
    ) -> None:
        """Invia risposta conferma ordine."""
        text = self.builder.conferma_ordine()
        await send_func(text=text, parse_mode=parse_mode)
    
    async def send_faq(
        self,
        send_func: Callable,
        domanda: str,
        risposta: str,
        parse_mode: str = "HTML"
    ) -> None:
        """Invia risposta FAQ."""
        text = self.builder.faq(domanda, risposta)
        await send_func(text=text, parse_mode=parse_mode)
    
    async def send_ricerca_prodotti(
        self,
        send_func: Callable,
        snippet: str,
        parse_mode: str = "HTML"
    ) -> None:
        """Invia risposta ricerca prodotti."""
        text = self.builder.ricerca_prodotti(snippet)
        await send_func(text=text, parse_mode=parse_mode)
    
    async def send_fallback(
        self,
        send_func: Callable,
        text_lower: str,
        parse_mode: Optional[str] = 'HTML'
    ) -> None:
        """Invia risposta fallback con suggerimento intelligente."""
        suggestion = self.builder.fallback_suggestion(text_lower)
        text = suggestion if suggestion else self.builder.fallback_default()
        # Chiama la funzione con la firma corretta (text_reply, parse_mode, reply_markup)
        await send_func(text, parse_mode)

# ============================================================================
# FACTORY FUNCTIONS
# ============================================================================

def create_dispatcher() -> HandlerResponseDispatcher:
    """Factory function per creare il dispatcher di default."""
    return HandlerResponseDispatcher()
