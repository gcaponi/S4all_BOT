"""
Feedback Handler Module
Gestione feedback e retraining automatico del modello ML.
"""

import os
import json
import logging
import pickle
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from intent_classifier import EnhancedIntentClassifier
import database as db
from error_handlers import log_db_error, safe_execute

logger = logging.getLogger(__name__)

# Configurazione
MIN_FEEDBACK_FOR_RETRAIN = 10      # Minimo feedback per riaddestrare
MIN_ACCURACY_IMPROVEMENT = 0.05    # Miglioramento minimo richiesto (5%)
MODEL_BACKUP_DIR = "training/backups"

class ModelRetrainer:
    """Gestisce il retraining automatico del modello ML."""
    
    def __init__(self, model_path: str = 'intent_classifier_model.pkl'):
        self.model_path = model_path
        self.backup_dir = MODEL_BACKUP_DIR
        os.makedirs(self.backup_dir, exist_ok=True)
    
    def backup_current_model(self) -> str:
        """Crea backup del modello attuale."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(self.backup_dir, f"model_backup_{timestamp}.pkl")
        
        if os.path.exists(self.model_path):
            import shutil
            shutil.copy2(self.model_path, backup_path)
            logger.info(f"💾 Backup modello creato: {backup_path}")
            return backup_path
        
        logger.warning("⚠️ Nessun modello da backuppare")
        return None
    
    def load_training_data(self) -> List[Dict]:
        """Carica dati di training originali + feedback."""
        # 1. Carica dataset originale
        original_data = []
        dataset_path = 'training/datasets/training_dataset.json'
        
        if os.path.exists(dataset_path):
            with open(dataset_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                original_data = [
                    {'text': conv['message'], 'intent': conv['intent']}
                    for conv in data.get('conversations', [])
                ]
            logger.info(f"📚 Dataset originale: {len(original_data)} esempi")
        
        # 2. Aggiungi feedback admin
        feedback_data = db.get_pending_feedback(limit=500)
        feedback_examples = [
            {'text': f['text'], 'intent': f['correct']}
            for f in feedback_data
        ]
        
        logger.info(f"💬 Feedback admin: {len(feedback_examples)} esempi")
        
        # Combina
        all_data = original_data + feedback_examples
        
        # Rimuovi duplicati (stesso testo)
        seen = set()
        unique_data = []
        for item in all_data:
            text_lower = item['text'].lower().strip()
            if text_lower not in seen:
                seen.add(text_lower)
                unique_data.append(item)
        
        logger.info(f"📊 Totale unico: {len(unique_data)} esempi")
        return unique_data, feedback_data
    
    def evaluate_model(self, classifier, test_data: List[Dict]) -> float:
        """Valuta accuracy del modello su dati di test."""
        if not test_data:
            return 0.0
        
        correct = 0
        for item in test_data:
            predicted, _ = classifier.classify(item['text'])
            if predicted == item['intent']:
                correct += 1
        
        accuracy = correct / len(test_data)
        logger.info(f"🎯 Accuracy: {accuracy:.2%} ({correct}/{len(test_data)})")
        return accuracy
    
    def retrain(self) -> Dict:
        """
        Esegue retraining completo.
        Returns:
            Dict con risultati: {'success': bool, 'accuracy': float, 'message': str}
        """
        logger.info("🚀 Inizio retraining modello...")
        
        # 1. Verifica minimo feedback
        stats = db.get_feedback_stats()
        if stats['pending'] < MIN_FEEDBACK_FOR_RETRAIN:
            return {
                'success': False,
                'accuracy': 0.0,
                'message': f"Solo {stats['pending']} feedback pending (min: {MIN_FEEDBACK_FOR_RETRAIN})"
            }
        
        # 2. Backup modello corrente
        backup_path = self.backup_current_model()
        
        # 3. Carica dati
        all_data, feedback_data = self.load_training_data()
        
        if len(all_data) < 10:
            return {
                'success': False,
                'accuracy': 0.0,
                'message': f"Dati insufficienti: {len(all_data)} esempi (min: 10)"
            }
        
        # 4. Split train/test (80/20) - minimo 2 esempi per test
        import random
        random.shuffle(all_data)
        
        if len(all_data) >= 10:
            split_idx = max(int(len(all_data) * 0.8), len(all_data) - 2)  # Almeno 2 per test
        else:
            split_idx = len(all_data) - 1  # Solo 1 per test se pochi dati
        
        train_data = all_data[:split_idx]
        test_data = all_data[split_idx:]
        
        logger.info(f"📊 Split: {len(train_data)} train, {len(test_data)} test")
        
        # 5. Crea e addestra nuovo classificatore
        try:
            classifier = EnhancedIntentClassifier()
            
            # Prepara dati per training
            texts = [item['text'] for item in train_data]
            intents = [item['intent'] for item in train_data]
            
            # Fit
            classifier.ml_pipeline.fit(texts, intents)
            classifier.is_trained = True
            
            # 6. Valuta
            accuracy = self.evaluate_model(classifier, test_data)
            
            # 7. Confronta con modello precedente (se esiste)
            old_accuracy = None
            if os.path.exists(self.model_path):
                old_classifier = EnhancedIntentClassifier()
                old_classifier.load_model(self.model_path)
                old_accuracy = self.evaluate_model(old_classifier, test_data)
                
                # Verifica miglioramento
                model_improved = old_accuracy and accuracy >= old_accuracy + MIN_ACCURACY_IMPROVEMENT
                
                if not model_improved and old_accuracy:
                    logger.warning(
                        f"⚠️ Nuovo modello non migliora abbastanza: "
                        f"{accuracy:.2%} vs {old_accuracy:.2%} - "
                        f"Salvo su Supabase solo per aggiornare i pattern"
                    )
                    # Salva su Supabase anche senza miglioramento per aggiornare i pattern
                    try:
                        classifier.save_to_supabase()
                        logger.info(f"✅ Pattern aggiornati su Supabase (accuracy: {accuracy:.2%})")
                    except Exception as e:
                        logger.warning(f"⚠️ Errore salvataggio Supabase: {e}")
                    
                    # Marca feedback come usati anche senza miglioramento ML
                    feedback_ids = [f['id'] for f in feedback_data]
                    db.mark_feedback_as_used(feedback_ids)
                    logger.info(f"✅ Feedback marcati come usati ({len(feedback_ids)} esempi)")
                    
                    return {
                        'success': True,  # Consideriamo successo perche' i pattern sono aggiornati
                        'accuracy': accuracy,
                        'old_accuracy': old_accuracy,
                        'pattern_only': True,
                        'message': f"Pattern aggiornati su Supabase. Accuracy: {accuracy:.2%} (vs {old_accuracy:.2%})"
                    }
            
            # 8. Salva nuovo modello (solo se migliorato o non esisteva modello precedente)
            classifier.save_model(self.model_path)
            # Salva anche su Supabase
            try:
                classifier.save_to_supabase()
                logger.info(f"✅ Modello salvato su Supabase (accuracy: {accuracy:.2%})")
            except Exception as e:
                logger.warning(f"⚠️ Errore salvataggio Supabase: {e}")
                
            # 9. Marca feedback come usati
            feedback_ids = [f['id'] for f in feedback_data]
            db.mark_feedback_as_used(feedback_ids)
            
            logger.info(f"✅ Retraining completato! Accuracy: {accuracy:.2%}")
            
            return {
                'success': True,
                'accuracy': accuracy,
                'old_accuracy': old_accuracy,
                'train_size': len(train_data),
                'test_size': len(test_data),
                'message': f"Modello riaddestrato con successo! Accuracy: {accuracy:.2%}"
            }
            
        except Exception as e:
            logger.error(f"❌ Errore durante retraining: {e}", exc_info=True)
            
            # Ripristina backup
            if backup_path and os.path.exists(backup_path):
                import shutil
                shutil.copy2(backup_path, self.model_path)
                logger.info("🔄 Modello precedente ripristinato")
            
            return {
                'success': False,
                'accuracy': 0.0,
                'message': f"Errore: {str(e)}"
            }

# ============================================================================
# FUNZIONI DI UTILITÀ
# ============================================================================

def get_retraining_status() -> Dict:
    """Stato attuale del retraining."""
    stats = db.get_feedback_stats()
    
    return {
        'feedback_pending': stats['pending'],
        'feedback_used': stats['used'],
        'feedback_total': stats['total'],
        'can_retrain': stats['pending'] >= MIN_FEEDBACK_FOR_RETRAIN,
        'min_required': MIN_FEEDBACK_FOR_RETRAIN,
        'by_intent': stats['by_intent']
    }

def trigger_retraining() -> Dict:
    """Avvia retraining manuale."""
    retrainer = ModelRetrainer()
    return retrainer.retrain()

@safe_execute(default_return=None, operation_name="schedule_retraining")
def schedule_automatic_retraining():
    """
    Da chiamare periodicamente (es. ogni notte) per retraining automatico.
    """
    status = get_retraining_status()
    
    if not status['can_retrain']:
        logger.info(f"⏭️ Retraining saltato: solo {status['feedback_pending']} feedback")
        return None
    
    # Verifica se è già stato fatto oggi
    last_retrain_file = 'training/.last_retrain'
    if os.path.exists(last_retrain_file):
        with open(last_retrain_file, 'r') as f:
            last_date = f.read().strip()
        today = datetime.now().strftime("%Y-%m-%d")
        if last_date == today:
            logger.info("⏭️ Retraining già fatto oggi")
            return None
    
    # Esegui retraining
    result = trigger_retraining()
    
    if result['success']:
        # Salva data
        with open(last_retrain_file, 'w') as f:
            f.write(datetime.now().strftime("%Y-%m-%d"))
    
    return result
