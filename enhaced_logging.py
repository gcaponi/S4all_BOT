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