"""
Gestione contesto conversazionale con SQLite
Salva ultimi N messaggi per chat per riferimenti pronominali
"""
import aiosqlite
import json
import os
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get('MEMORY_DB_PATH', '/tmp/chat_memory.db')

class ChatMemory:
    """Gestisce lo storico conversazioni per contesto"""
    
    def __init__(self, max_history: int = 5):
        self.max_history = max_history
        self.db_path = DB_PATH
        logger.info(f"💾 ChatMemory init: DB path = {self.db_path}")
    
    async def init_db(self):
        """Crea tabella se non esiste"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS chat_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chat_id TEXT NOT NULL,
                        user_id TEXT,
                        message_text TEXT,
                        bot_response TEXT,
                        intent TEXT,
                        entities TEXT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Indice per performance
                await db.execute('''
                    CREATE INDEX IF NOT EXISTS idx_chat_time 
                    ON chat_history(chat_id, timestamp DESC)
                ''')
                
                await db.commit()
                logger.info("✅ ChatMemory DB initialized")
        except Exception as e:
            logger.error(f"❌ Error initializing ChatMemory DB: {e}")
    
    async def add_message(self, chat_id: int, user_id: int, text: str, 
                         intent: str = None, response: str = None, 
                         entities: dict = None):
        """Aggiunge messaggio alla history"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    INSERT INTO chat_history 
                    (chat_id, user_id, message_text, bot_response, intent, entities)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    str(chat_id), str(user_id), text, response, intent,
                    json.dumps(entities) if entities else None
                ))
                
                # Cleanup: mantieni solo ultimi N messaggi per questa chat
                await db.execute('''
                    DELETE FROM chat_history 
                    WHERE chat_id = ? AND id NOT IN (
                        SELECT id FROM chat_history 
                        WHERE chat_id = ? 
                        ORDER BY timestamp DESC 
                        LIMIT ?
                    )
                ''', (str(chat_id), str(chat_id), self.max_history))
                
                await db.commit()
        except Exception as e:
            logger.error(f"❌ Error adding message to history: {e}")
    
    async def get_context(self, chat_id: int, n: int = 5) -> List[Dict]:
        """Recupera ultimi N messaggi di contesto"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute('''
                    SELECT * FROM chat_history 
                    WHERE chat_id = ? 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                ''', (str(chat_id), n)) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in reversed(rows)]  # Ordine cronologico
        except Exception as e:
            logger.error(f"❌ Error getting context: {e}")
            return []
    
    async def get_last_entities(self, chat_id: int) -> Optional[Dict]:
        """Recupera entità dall'ultimo messaggio (per referenze: 'quello', 'quella')"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute('''
                    SELECT entities, message_text FROM chat_history 
                    WHERE chat_id = ? AND entities IS NOT NULL
                    ORDER BY timestamp DESC LIMIT 1
                ''', (str(chat_id),)) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        entities = json.loads(row[0]) if row[0] else {}
                        entities['_last_message'] = row[1]  # Per riferimenti testuali
                        return entities
            return None
        except Exception as e:
            logger.error(f"❌ Error getting last entities: {e}")
            return None
    
    async def clear_old_history(self, days: int = 7):
        """Pulizia automatica storico vecchio (GDPR/privacy)"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cutoff = datetime.now() - timedelta(days=days)
                await db.execute('''
                    DELETE FROM chat_history WHERE timestamp < ?
                ''', (cutoff,))
                deleted = db.total_changes
                await db.commit()
                if deleted > 0:
                    logger.info(f"🗑️ Cleaned {deleted} old messages (>7 days)")
        except Exception as e:
            logger.error(f"❌ Error cleaning old history: {e}")
    
    def resolve_references(self, text: str, last_entities: Optional[Dict]) -> str:
        """
        Risolve riferimenti pronominali ('quello', 'quella', 'quel prodotto')
        in riferimenti specifici basati sull'ultimo contesto.
        """
        if not last_entities:
            return text
            
        text_lower = text.lower()
        vague_refs = ['quello', 'quella', 'quel', 'quelli', 'quelle', 'precedente', 'stesso', 'stessa']
        
        # Check se ci sono riferimenti vaghi
        if any(ref in text_lower for ref in vague_refs):
            product = last_entities.get('product')
            if product:
                # Sostituisci riferimento vago con prodotto specifico
                for ref in vague_refs:
                    if ref in text_lower:
                        text = text.lower().replace(ref, product)
                        logger.info(f"🔗 Risolto riferimento: '{ref}' → '{product}'")
                        break
        
        return text

# Istanza globale
chat_memory = ChatMemory(max_history=5)

# End memory_buffer.py