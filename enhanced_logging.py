"""
Enhanced Logging System
- Structured logging con JSON
- Rotation automatica
- Metriche classification errors
- Export per analisi
"""
import logging
import json
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Dict, List

# Path logs
LOGS_DIR = os.environ.get('LOGS_DIR', '/tmp/bot_logs')
os.makedirs(LOGS_DIR, exist_ok=True)

class ClassificationLogger:
    """Logger specializzato per errori di classificazione"""
    
    def __init__(self):
        self.log_file = os.path.join(LOGS_DIR, 'classification_errors.jsonl')
        self.stats_file = os.path.join(LOGS_DIR, 'classification_stats.json')
        
        # Setup logger dedicato
        self.logger = logging.getLogger('classification_errors')
        self.logger.setLevel(logging.INFO)
        
        # File handler con rotazione
        handler = RotatingFileHandler(
            self.log_file,
            maxBytes=5*1024*1024,  # 5MB
            backupCount=3
        )
        handler.setFormatter(logging.Formatter('%(message)s'))
        self.logger.addHandler(handler)
        
        # Stats in memoria
        self.stats = self._load_stats()
    
    def _load_stats(self) -> Dict:
        """Carica stats da file"""
        try:
            if os.path.exists(self.stats_file):
                with open(self.stats_file, 'r') as f:
                    return json.load(f)
        except:
            pass
        
        return {
            'total_classifications': 0,
            'by_intent': {},
            'low_confidence': [],
            'fallback_rate': 0.0
        }
    
    def _save_stats(self):
        """Salva stats su file"""
        try:
            with open(self.stats_file, 'w') as f:
                json.dump(self.stats, f, indent=2)
        except Exception as e:
            logging.error(f"Error saving stats: {e}")
    
    def log_classification(self, text: str, intent: str, confidence: float,
                          method: str = None, user_id: int = None):
        """Log ogni classificazione per analisi"""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'text': text[:100],  # Limita lunghezza per privacy
            'intent': intent,
            'confidence': round(confidence, 3),
            'method': method,
            'user_id': user_id
        }
        
        # Log in file JSON Lines
        self.logger.info(json.dumps(log_entry))
        
        # Log anche in PostgreSQL per persistenza
        try:
            import database as db
            db.log_classification(text, intent, confidence)
        except Exception as e:
            logging.error(f"❌ Errore log PostgreSQL: {e}")
        
        # Aggiorna stats
        self.stats['total_classifications'] += 1
        
        if intent not in self.stats['by_intent']:
            self.stats['by_intent'][intent] = {'count': 0, 'avg_confidence': 0.0}
        
        intent_stats = self.stats['by_intent'][intent]
        prev_count = intent_stats['count']
        intent_stats['count'] += 1
        
        # Media mobile confidence
        intent_stats['avg_confidence'] = (
            (intent_stats['avg_confidence'] * prev_count + confidence) / intent_stats['count']
        )
        
        # Track low confidence
        if confidence < 0.75 and intent != 'fallback':
            self.stats['low_confidence'].append({
                'text': text[:50],
                'intent': intent,
                'confidence': confidence,
                'timestamp': datetime.now().isoformat()
            })
            
            # Mantieni solo ultimi 20
            if len(self.stats['low_confidence']) > 20:
                self.stats['low_confidence'] = self.stats['low_confidence'][-20:]
        
        # Calcola fallback rate
        fallback_count = self.stats['by_intent'].get('fallback', {}).get('count', 0)
        self.stats['fallback_rate'] = fallback_count / max(self.stats['total_classifications'], 1)
        
        # Salva periodicamente (ogni 10 classificazioni)
        if self.stats['total_classifications'] % 10 == 0:
            self._save_stats()
    
    def get_stats(self) -> Dict:
        """Ottieni statistiche correnti"""
        return self.stats
    
    def get_low_confidence_cases(self, limit: int = 10) -> List[Dict]:
        """Ottieni casi con bassa confidence per review"""
        return sorted(
            self.stats['low_confidence'],
            key=lambda x: x['confidence']
        )[:limit]
    
    def get_cases_by_intent(self, intent: str = None, limit: int = 100) -> list:
        """Ritorna tutti i casi per un intent specifico da PostgreSQL"""
        try:
            import database as db
            return db.get_cases_by_intent(intent, limit)
        except Exception as e:
            logging.error(f"❌ get_cases_by_intent: {e}")
            return []
    
    def get_confidence_distribution(self, intent: str) -> dict:
        """Ritorna distribuzione confidence da PostgreSQL"""
        try:
            import database as db
            return db.get_confidence_distribution(intent)
        except Exception as e:
            logging.error(f"❌ get_confidence_distribution: {e}")
            return {}
            
    def export_for_retraining(self, output_file: str = None):
        """Esporta low confidence cases per retraining"""
        if not output_file:
            output_file = os.path.join(LOGS_DIR, 'retraining_candidates.json')
        
        try:
            with open(output_file, 'w') as f:
                json.dump(self.stats['low_confidence'], f, indent=2)
            logging.info(f"✅ Exported {len(self.stats['low_confidence'])} cases to {output_file}")
            return output_file
        except Exception as e:
            logging.error(f"Error exporting: {e}")
            return None

# Istanza globale
classification_logger = ClassificationLogger()

def export_intent_for_correction(self, intent_name: str, limit: int = 1000) -> dict:
        """
        Esporta tutti i messaggi di un intent specifico in formato JSON
        pronto per correzione manuale
        
        Args:
            intent_name: Nome dell'intent da esportare
            limit: Numero massimo di messaggi (default: 1000)
        
        Returns:
            Dict con struttura:
            {
                "intent": "nome_intent",
                "total_cases": 123,
                "cases": [
                    {
                        "text": "messaggio...",
                        "intent": "intent_classificato",
                        "confidence": 0.95,
                        "timestamp": "2026-02-03T12:34:56",
                        "correct_intent": ""  # <-- DA COMPILARE
                    }
                ]
            }
        """
        try:
            cases = self.get_cases_by_intent(intent_name, limit)
            
            if not cases:
                return {
                    "error": f"Nessun caso trovato per intent '{intent_name}'",
                    "intent": intent_name,
                    "total_cases": 0,
                    "cases": []
                }
            
            # Aggiungi campo "correct_intent" vuoto per ogni caso
            for case in cases:
                case['correct_intent'] = ""  # L'utente compilerà questo campo
                case['notes'] = ""  # Campo opzionale per note
            
            export_data = {
                "intent": intent_name,
                "total_cases": len(cases),
                "export_date": datetime.now().isoformat(),
                "instructions": {
                    "it": "Compila il campo 'correct_intent' per ogni messaggio classificato erroneamente. Lascia vuoto se la classificazione è corretta.",
                    "en": "Fill the 'correct_intent' field for each misclassified message. Leave empty if classification is correct."
                },
                "available_intents": [
                    "order",
                    "order_confirmation", 
                    "search",
                    "faq",
                    "list",
                    "saluto",
                    "fallback",
                    "fallback_mute"
                ],
                "cases": cases
            }
            
            return export_data
            
        except Exception as e:
            logging.error(f"❌ export_intent_for_correction error: {e}")
            return {
                "error": str(e),
                "intent": intent_name,
                "total_cases": 0,
                "cases": []
            }

def setup_enhanced_logging():
    """
    Setup logging migliorato con rotazione e struttura
    Chiamare in setup_bot()
    """
    # Main logger con rotazione
    main_log = os.path.join(LOGS_DIR, 'bot_main.log')
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # File handler con rotazione
    file_handler = RotatingFileHandler(
        main_log,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    
    # Rimuovi handler esistenti per evitare duplicati
    root_logger.handlers = []
    root_logger.addHandler(file_handler)
    
    # Console handler per debug
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        '%(levelname)s - %(message)s'
    ))
    root_logger.addHandler(console_handler)
    
    logging.info(f"✅ Enhanced logging setup: {LOGS_DIR}")
    logging.info(f"📊 Classification tracking enabled")

# End enhanced_logging.py