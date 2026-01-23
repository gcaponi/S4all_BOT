import re
import json
import pickle
import numpy as np
from collections import defaultdict
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline
import os
from datetime import datetime

class EnhancedIntentClassifier:
    def __init__(self, config_path=None):
        # Configurazioni
        self.MIN_CONFIDENCE = 0.65
        self.FALLBACK_THRESHOLD = 0.45
        self.USE_HYBRID = True
        
        # Inizializza componenti
        self._init_patterns()
        self._init_keywords()
        self._init_ml_model()
        
        # Statistiche
        self.stats = defaultdict(int)
        self.confusion_matrix = defaultdict(lambda: defaultdict(int))
        
        # Carica configurazione se fornita
        if config_path and os.path.exists(config_path):
            self.load_config(config_path)
    
    def _init_patterns(self):
        """Inizializza i pattern regex per ogni intent"""
        self.patterns = {
            "saluto": [
                r'^(ciao|hey|yo|salve|buongiorno|buonasera)$',
                r'^(ciao|hey|yo|salve|buongiorno|buonasera)\s*!*$'
            ],
            
            "order": [
                r'^\d+\s+(testo|anavar|deca|tren|susta|winstrol|winny|masteron|boldo|primo|dianabol|clen|hcg|clomid|kamagra|viagra|cialis|levitra)\s+per\s+favore$',
                r'^(ordina|prenota|compra|acquista)\s+\d+\s+\w+$',
                r'\b(\d+)\s+(conf|flacone|fiala|compresso|pillola|busta)\s+di\s+\w+$',
                r'^(testo|anavar|deca|tren|susta|winstrol|winny)\s+subito$',
                r'^\d+\s+\w+\s+e\s+\d+\s+\w+$',
                r'^(pago ora|ok manda|fatto|vado|faccio|si)$',
                r'\b(fattura|ricevuta|scontrino)\b\??',
                r'\b(mandami|invia|spediscimi|consegnami)\s+\d+\s+\w+',
                # NUOVI pattern ordini impliciti
                r'\b(voglio|vorrei|mi\s+serve)\s+\w+',
                r'\b(prendo|prenoto|ordino)\s+\w+',
                r'^\w+\s+(grazie|per\s+favore)$'
            ],
            
            "search": [
                r'\b(hai|avete|ce l\'hai|c\'è|vendete)\b.*\??',
                r'^(hai|avete|ce l\'hai|c\'è|vendete)\??$',
                r'\b(quanto|costa|prezzo)\b.*\??',
                r'^(quanto|costa|prezzo)\??$',
                r'prezzo\s+\w+',
                r'^(orali|sarms|pct|peptidi|ai|sex|viagra|cialis|levitra|cut|bulk|massa|definizione)\??$',
                r'\b(consigli|meglio|confronto|quale|cosa)\b.*\??',
                r'^(che hai|cosa c\'è|novità|disponibile|stock)\??$',
                r'\b(per massa|per forza|per taglio|per definizione)\b',
                r'^(come funziona|info|dettagli)\??$',
                r'^(voglio|vorrei|cerco|cercavo|mi serve)\s+\w+\??$',
                r'^\w+\s+(info|informazioni)\??$'
            ],
            
            "faq": [
                r'\b(quando|dove|spedisci|arriva|consegna|pacco|tracking|corriere)\b.*\??',
                r'^(quando|dove|spedisci|arriva)\??$',
                r'\b(come pago|bonifico|crypto|contrassegno|pagamento|metodo|pago)\b.*\??',
                r'^(bonifico|crypto|contrassegno|pagamento|metodo)\??$',
                r'\b(sconto|minimo|offerta|promozione)\b.*\??',
                r'^(sconto|minimo|offerta|promozione)\??$',
                r'\b(sicuro|discreto|garanzia|privacy|anonimo)\b.*\??',
                r'^(sicuro|discreto|garanzia|privacy)\??$',
                r'\b(problema|help|aiuto|contatto|numero|supporto|assistenza)\b.*\??',
                r'^(problema|help|aiuto|contatto|numero|supporto)\??$',
                r'\b(tempo|giorno|giorni|settimana|settimane|modalità|come funziona)\b.*\??$',
                # NUOVI pattern FAQ specifici
                r'c\'è\s+(un\s+)?minimo',
                r'quanto\s+costa\s+(la\s+)?(spedizione|consegna)',
                r'(ordine\s+)?minimo',
                r'\b(quanto|come)\s+(tempo|giorni|settimane)\b',
                r'\b(posso|si\s+può)\s+(ordinare|pagare)\b'
            ],
            
            "list": [
                r'^(lista|catalogo|listino|prezzi|tutto|mostra|manda|prodotti|offerte|stock)$',
                r'^(che avete|cosa vendete|mostra tutto|manda lista)$',
                r'\b(lista|catalogo|listino|prezzi|prodotti|offerte)\b',
                r'^(fammi vedere|mostrami|visualizza)\s+(cosa|tutto)',
                r'\b(che|cosa)\s+(avete|hai|c\'è)\s+(in\s+)?stock\b',
                r'^(che|cosa)\s+(hai|avete)\??$'
            ],
            
            "contact": [
                r'\b(contatto|numero|telefono|email|whatsapp|telegram|instagram)\b.*\??',
                r'^(contatto|numero|telefono|email|whatsapp)\??$',
                r'\b(scrivi|chiama|messaggio|dm)\b',
                r'numero\s+(di\s+)?(telefono|cellulare)',
                r'hai\s+(whatsapp|telegram|numero)'
            ],
            
            "fallback": [
                r'^(bot|chi\s+sei|cosa|boh|\?+)\??$',
                r'^(non\s+)?ho\s+capito$',
                r'cos\'è\s+questo'
            ]
        }
    
    def _init_keywords(self):
        """Inizializza le liste di parole chiave"""
        self.product_keywords = [
            'testo', 'testosterone', 'anavar', 'deca', 'tren', 'susta', 'sustanon',
            'winstrol', 'winny', 'masteron', 'boldo', 'boldenone', 'primo', 'primobolan',
            'dianabol', 'dbol', 'clen', 'clenbuterolo', 'hcg', 'clomid', 'kamagra',
            'tren ace', 'trenbolone', 'viagra', 'cialis', 'levitra', 'proviron',
            'arimidex', 'nolvadex', 'tamoxifen', 'clenbuterol'
        ]
        
        self.category_keywords = [
            'orali', 'sarms', 'pct', 'peptidi', 'ai', 'sex', 'cut', 'bulk',
            'massa', 'definizione', 'taglio', 'steroidi', 'ormoni', 'integratore'
        ]
        
        self.order_verbs = ['ordina', 'prenota', 'compra', 'acquista', 'mandami', 'invia', 'spediscimi', 'consegnami', 'prendo', 'dammi']
        self.wish_verbs = ['voglio', 'vorrei', 'cerco', 'cercavo', 'mi serve', 'mi servirebbe']
        self.question_words = ['quando', 'dove', 'come', 'perché', 'posso', 'quanto', 'cosa', 'quale']
        self.faq_keywords = ['spedizione', 'consegna', 'pagamento', 'bonifico', 'crypto', 
                            'contrassegno', 'tempo', 'giorni', 'settimane', 'sicuro', 
                            'discreto', 'garanzia', 'minimo', 'sconto', 'offerta']
        self.contact_keywords = ['contatto', 'numero', 'telefono', 'whatsapp', 'telegram', 'email']
        self.list_keywords = ['lista', 'catalogo', 'listino', 'stock', 'prodotti']
    
    def _init_ml_model(self):
        """Inizializza il modello ML"""
        self.ml_pipeline = Pipeline([
            ('vectorizer', CountVectorizer(
                lowercase=True,
                ngram_range=(1, 2),
                max_features=1000
            )),
            ('classifier', MultinomialNB(alpha=0.1))
        ])
        self.is_trained = False
    
    def train_from_json(self, json_path):
        """Addestra il modello da file JSON"""
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            messages = []
            intents = []
            
            for conv in data.get('conversations', []):
                messages.append(conv['message'])
                intents.append(conv['intent'])
            
            if not messages:
                print("⚠️ Nessun dato di training trovato")
                return False
            
            self.ml_pipeline.fit(messages, intents)
            self.is_trained = True
            
            print(f"✅ Modello addestrato con {len(messages)} esempi")
            print(f"   Classi: {set(intents)}")
            return True
            
        except Exception as e:
            print(f"❌ Errore durante il training: {e}")
            return False
    
    def classify(self, message, debug=False):
        """
        Classifica un messaggio usando approccio ibrido
        Returns: (intent, confidence)
        """
        message_lower = message.strip().lower()
        self.stats['total_requests'] += 1
        
        # 1. REGOLE REGEX
        regex_result = self._classify_by_regex(message_lower, debug)
        if regex_result:
            intent, confidence = regex_result
            if confidence >= self.MIN_CONFIDENCE:
                self.stats['regex_classifications'] += 1
                return intent, confidence
        
        # 2. MODELLO ML
        if self.is_trained and self.USE_HYBRID:
            ml_result = self._classify_by_ml(message, debug)
            if ml_result:
                intent, confidence = ml_result
                if confidence >= self.FALLBACK_THRESHOLD:
                    self.stats['ml_classifications'] += 1
                    return intent, confidence
        
        # 3. REGOLE SEMPLICI
        simple_result = self._classify_by_simple_rules(message_lower, debug)
        if simple_result:
            intent, confidence = simple_result
            if confidence >= self.FALLBACK_THRESHOLD:
                self.stats['simple_classifications'] += 1
                return intent, confidence
        
        # 4. FALLBACK
        self.stats['fallback_classifications'] += 1
        return "fallback", 0.0
    
    def _classify_by_regex(self, message, debug=False):
        """Classifica usando regex patterns"""
        best_intent = None
        best_confidence = 0.0
        
        for intent, patterns in self.patterns.items():
            for pattern in patterns:
                if re.search(pattern, message, re.IGNORECASE):
                    confidence = self._calculate_regex_confidence(message, intent, pattern)
                    if confidence > best_confidence:
                        best_confidence = confidence
                        best_intent = intent
        
        if best_intent:
            return best_intent, best_confidence
        return None
    
    def _classify_by_ml(self, message, debug=False):
        """Classifica usando il modello ML"""
        try:
            if not self.is_trained:
                return None
            
            probas = self.ml_pipeline.predict_proba([message])[0]
            classes = self.ml_pipeline.classes_
            
            max_idx = np.argmax(probas)
            intent = classes[max_idx]
            confidence = probas[max_idx]
            
            return intent, confidence
            
        except Exception as e:
            return None
    
    def _classify_by_simple_rules(self, message, debug=False):
        """Classifica usando regole semplici con priorità corrette"""
        words = message.split()
        
        if not words:
            return None
        
        has_product = any(product in message for product in self.product_keywords)
        has_category = any(category in message for category in self.category_keywords)
        is_question = '?' in message
        
        # ============================================
        # ORDINE PRIORITÀ (DAL PIÙ SPECIFICO AL GENERICO)
        # ============================================
        
        # 0. CONTACT KEYWORDS (priorità assoluta)
        if any(kw in message for kw in self.contact_keywords):
            # Se chiede numero/telefono/whatsapp → contact
            if any(w in message for w in ['numero', 'telefono', 'whatsapp', 'telegram', 'email']):
                if 'tracking' not in message:  # Eccezione: "numero tracking" = FAQ
                    return "contact", 0.90
        
        # 1. FAQ KEYWORDS (priorità massima per domande procedurali)
        faq_strong_keywords = ['spedizione', 'consegna', 'pagamento', 'bonifico', 
                               'crypto', 'tempo', 'giorni', 'minimo', 'sconto']
        if any(faq_word in message for faq_word in faq_strong_keywords):
            # ECCEZIONE: "quanto costa PRODOTTO" è search, non FAQ
            if 'quanto' in message and 'costa' in message and has_product:
                if 'spedizione' not in message:
                    return "search", 0.85
            return "faq", 0.85
        
        # 2. PREZZO/QUANTO + PRODOTTO = SEARCH (non order!)
        if any(w in message for w in ['prezzo', 'quanto', 'costa', 'costo']):
            if has_product or has_category:
                return "search", 0.88  # "prezzo deca" = search
        
        # 3. WISH VERBS + PRODOTTO = ORDER (CORRETTO!)
        if any(verb in message for verb in self.wish_verbs):
            if has_product or has_category:
                return "order", 0.90  # "voglio anavar" = ordine
            else:
                # Se ha numeri (es. "voglio 2 di quelle") → probabilmente order vago
                if any(char.isdigit() for char in message):
                    return "order", 0.75  # "voglio 2 di quelle cose"
                return "search", 0.70  # "voglio qualcosa per massa" = ricerca
        
        # 4. ORDER VERBS = ORDER (anche senza prodotto specifico)
        if any(verb in message for verb in self.order_verbs):
            # "prendo quello che hai detto" = order anche senza prodotto
            return "order", 0.85
        
        # 4. PRODOTTI con domande -> SEARCH
        if has_product or has_category:
            if is_question:
                return "search", 0.80  # "hai anavar?"
            elif len(words) <= 2:
                return "search", 0.75  # "testo"
        
        # 5. Singole parole (dictionary lookup)
        if len(words) == 1:
            word_scores = {
                'lista': ("list", 0.90), 'catalogo': ("list", 0.90), 'prezzi': ("list", 0.90),
                'orali': ("search", 0.85), 'sarms': ("search", 0.85), 'pct': ("search", 0.85),
                'ok': ("order", 0.80), 'si': ("order", 0.80), 'fatto': ("order", 0.80),
                'help': ("faq", 0.80), 'supporto': ("faq", 0.80),
                'ciao': ("saluto", 0.95), 'hey': ("saluto", 0.95),
            }
            if words[0] in word_scores:
                return word_scores[words[0]]
        
        # 6. Coppie di parole
        if len(words) == 2:
            first = words[0]
            if first in self.order_verbs:
                return "order", 0.82
            if first in ['hai', 'costa', 'prezzo', 'quanto']:
                return "search", 0.80
            if first in self.question_words:
                return "faq", 0.78
        
        # 7. Domande generiche
        if is_question:
            if any(word in message for word in ['quando', 'dove', 'come']):
                return "faq", 0.75
            else:
                return "search", 0.70
        
        return None
    
    def _calculate_regex_confidence(self, message, intent, pattern):
        """Calcola confidence score per match regex"""
        base_score = 0.7
        
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            matched_text = match.group()
            match_ratio = len(matched_text) / len(message)
            base_score += match_ratio * 0.2
        
        if intent == "saluto" and len(message.split()) <= 2:
            base_score += 0.15
        
        if intent == "order" and any(verb in message for verb in self.order_verbs):
            base_score += 0.1
        
        if intent in ["search", "faq"] and '?' in message:
            base_score += 0.05
        
        return min(max(base_score, 0.3), 0.95)
    
    def batch_classify(self, messages):
        """Classifica una lista di messaggi"""
        results = []
        for msg in messages:
            intent, confidence = self.classify(msg)
            results.append({
                'message': msg,
                'intent': intent,
                'confidence': confidence
            })
        return results
    
    def evaluate_properly(self, json_path, test_split=0.3):
        """Valutazione corretta con split stratificato"""
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            intent_groups = defaultdict(list)
            for conv in data['conversations']:
                intent_groups[conv['intent']].append((conv['message'], conv['intent']))
            
            train_data = []
            test_data = []
            
            print(f"📊 Distribuzione dataset:")
            for intent, samples in intent_groups.items():
                print(f"  {intent}: {len(samples)} esempi")
                split_idx = int(len(samples) * (1 - test_split))
                train_data.extend(samples[:split_idx])
                test_data.extend(samples[split_idx:])
            
            print(f"\n📈 Split {int((1-test_split)*100)}/{int(test_split*100)}:")
            print(f"  Training: {len(train_data)} esempi")
            print(f"  Test: {len(test_data)} esempi")
            
            print("\n🎯 Addestramento su dati training...")
            temp_data = {'conversations': []}
            for msg, intent in train_data:
                temp_data['conversations'].append({'message': msg, 'intent': intent})
            
            temp_path = 'temp_training_split.json'
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(temp_data, f, ensure_ascii=False)
            
            self.train_from_json(temp_path)
            
            if os.path.exists(temp_path):
                os.remove(temp_path)
            
            print("🧪 Valutazione su dati test...")
            results = self._detailed_evaluate(test_data)
            self._print_evaluation_results(results)
            
            return results
            
        except Exception as e:
            print(f"❌ Errore nella valutazione: {e}")
            return None
    
    def _detailed_evaluate(self, test_data):
        """Valutazione dettagliata con confusion matrix"""
        correct = 0
        details = []
        
        self.confusion_matrix = defaultdict(lambda: defaultdict(int))
        
        for message, true_intent in test_data:
            pred_intent, confidence = self.classify(message)
            
            self.confusion_matrix[true_intent][pred_intent] += 1
            
            is_correct = pred_intent == true_intent
            if is_correct:
                correct += 1
            
            details.append({
                'message': message,
                'true_intent': true_intent,
                'pred_intent': pred_intent,
                'confidence': confidence,
                'correct': is_correct
            })
        
        accuracy = (correct / len(test_data)) * 100 if test_data else 0
        
        all_intents = set([true for _, true in test_data] + [pred for _, pred in test_data])
        metrics = {}
        
        for intent in all_intents:
            tp = self.confusion_matrix[intent][intent]
            fp = sum(self.confusion_matrix[other][intent] for other in all_intents if other != intent)
            fn = sum(self.confusion_matrix[intent][other] for other in all_intents if other != intent)
            
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0
            f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
            
            metrics[intent] = {
                'precision': round(precision, 3),
                'recall': round(recall, 3),
                'f1': round(f1, 3),
                'support': tp + fn,
                'true_positives': tp,
                'false_positives': fp,
                'false_negatives': fn
            }
        
        errors = [d for d in details if not d['correct']]
        
        return {
            'accuracy': round(accuracy, 1),
            'total': len(test_data),
            'correct': correct,
            'incorrect': len(test_data) - correct,
            'metrics': metrics,
            'errors': errors,
            'confusion_matrix': dict(self.confusion_matrix)
        }
    
    def _print_evaluation_results(self, results):
        """Stampa i risultati della valutazione"""
        print("\n" + "="*60)
        print("📊 RISULTATI VALUTAZIONE COMPLETA")
        print("="*60)
        
        print(f"\n🎯 ACCURACY TOTALE: {results['accuracy']}%")
        print(f"   Corretti: {results['correct']}/{results['total']}")
        print(f"   Errati: {results['incorrect']}/{results['total']}")
        
        print(f"\n📈 METRICHE PER INTENT:")
        print("-"*40)
        for intent, metrics in results['metrics'].items():
            print(f"\n  {intent.upper()}:")
            print(f"    Precision: {metrics['precision']:.3f}")
            print(f"    Recall:    {metrics['recall']:.3f}")
            print(f"    F1-Score:  {metrics['f1']:.3f}")
            print(f"    Support:   {metrics['support']} esempi")
        
        print(f"\n📊 MATRICE DI CONFUSIONE:")
        print("-"*40)
        all_intents = sorted(results['confusion_matrix'].keys())
        
        header = "True\\Pred | " + " | ".join(f"{i:<8}" for i in all_intents)
        print(header)
        print("-" * len(header))
        
        for true_intent in all_intents:
            row = f"{true_intent:<10} | "
            for pred_intent in all_intents:
                count = results['confusion_matrix'][true_intent].get(pred_intent, 0)
                row += f"{count:<8} | "
            print(row)
        
        if results['errors']:
            print(f"\n❌ ERRORI DETTAGLIATI ({len(results['errors'])}):")
            print("-"*40)
            for i, error in enumerate(results['errors'][:10], 1):
                print(f"{i}. Messaggio: '{error['message']}'")
                print(f"   Atteso: {error['true_intent']}, Rilevato: {error['pred_intent']}")
                print(f"   Confidence: {error['confidence']:.2f}")
            
            if len(results['errors']) > 10:
                print(f"\n   ... e altri {len(results['errors']) - 10} errori")
            
            error_file = 'evaluation_errors.json'
            with open(error_file, 'w', encoding='utf-8') as f:
                json.dump(results['errors'], f, indent=2, ensure_ascii=False)
            print(f"\n💾 Errori salvati in: {error_file}")
    
    def save_model(self, path='intent_classifier_model.pkl'):
        """Salva il modello su disco"""
        try:
            # Converti defaultdict in dict normali per evitare errore pickle
            stats_dict = dict(self.stats)
            confusion_dict = {k: dict(v) for k, v in self.confusion_matrix.items()}
            
            with open(path, 'wb') as f:
                pickle.dump({
                    'ml_pipeline': self.ml_pipeline,
                    'is_trained': self.is_trained,
                    'patterns': self.patterns,
                    'product_keywords': self.product_keywords,
                    'category_keywords': self.category_keywords,
                    'order_verbs': self.order_verbs,
                    'wish_verbs': self.wish_verbs,
                    'question_words': self.question_words,
                    'faq_keywords': self.faq_keywords,
                    'contact_keywords': self.contact_keywords,
                    'list_keywords': self.list_keywords,
                    'stats': stats_dict,
                    'confusion_matrix': confusion_dict
                }, f)
            print(f"✅ Modello salvato in {path}")
            return True
        except Exception as e:
            print(f"❌ Errore nel salvataggio: {e}")
            return False
    
    def load_model(self, path='intent_classifier_model.pkl'):
        """Carica il modello da disco"""
        try:
            with open(path, 'rb') as f:
                data = pickle.load(f)
            
            self.ml_pipeline = data['ml_pipeline']
            self.is_trained = data['is_trained']
            self.patterns = data.get('patterns', self.patterns)
            self.product_keywords = data.get('product_keywords', self.product_keywords)
            self.category_keywords = data.get('category_keywords', self.category_keywords)
            self.order_verbs = data.get('order_verbs', self.order_verbs)
            self.wish_verbs = data.get('wish_verbs', self.wish_verbs)
            self.question_words = data.get('question_words', self.question_words)
            self.faq_keywords = data.get('faq_keywords', self.faq_keywords)
            self.stats = data.get('stats', self.stats)
            self.confusion_matrix = data.get('confusion_matrix', self.confusion_matrix)
            
            print(f"✅ Modello caricato da {path}")
            return True
        except Exception as e:
            print(f"❌ Errore nel caricamento: {e}")
            return False
    
    def save_config(self, path='classifier_config.json'):
        """Salva la configurazione corrente"""
        config = {
            'min_confidence': self.MIN_CONFIDENCE,
            'fallback_threshold': self.FALLBACK_THRESHOLD,
            'use_hybrid': self.USE_HYBRID,
            'patterns': self.patterns,
            'keywords': {
                'products': self.product_keywords,
                'categories': self.category_keywords,
                'order_verbs': self.order_verbs,
                'wish_verbs': self.wish_verbs,
                'question_words': self.question_words,
                'faq_keywords': self.faq_keywords
            },
            'stats': dict(self.stats),
            'last_updated': datetime.now().isoformat()
        }
        
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            print(f"✅ Configurazione salvata in {path}")
            return True
        except Exception as e:
            print(f"❌ Errore nel salvataggio config: {e}")
            return False
    
    def load_config(self, path='classifier_config.json'):
        """Carica configurazione"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            self.MIN_CONFIDENCE = config.get('min_confidence', self.MIN_CONFIDENCE)
            self.FALLBACK_THRESHOLD = config.get('fallback_threshold', self.FALLBACK_THRESHOLD)
            self.USE_HYBRID = config.get('use_hybrid', self.USE_HYBRID)
            
            keywords = config.get('keywords', {})
            self.product_keywords = keywords.get('products', self.product_keywords)
            self.category_keywords = keywords.get('categories', self.category_keywords)
            self.order_verbs = keywords.get('order_verbs', self.order_verbs)
            self.wish_verbs = keywords.get('wish_verbs', self.wish_verbs)
            self.question_words = keywords.get('question_words', self.question_words)
            self.faq_keywords = keywords.get('faq_keywords', self.faq_keywords)
            
            print(f"✅ Configurazione caricata da {path}")
            return True
        except Exception as e:
            print(f"❌ Errore nel caricamento config: {e}")
            return False
    
    def print_stats(self):
        """Stampa statistiche di utilizzo"""
        print("\n📊 STATISTICHE CLASSIFICATORE")
        print("=" * 50)
        print(f"Richieste totali: {self.stats.get('total_requests', 0)}")
        print(f"Classificazioni regex: {self.stats.get('regex_classifications', 0)}")
        print(f"Classificazioni ML: {self.stats.get('ml_classifications', 0)}")
        print(f"Classificazioni semplici: {self.stats.get('simple_classifications', 0)}")
        print(f"Fallback: {self.stats.get('fallback_classifications', 0)}")
        
        if self.confusion_matrix:
            print("\n📈 MATRICE DI CONFUSIONE:")
            for true_intent, pred_counts in self.confusion_matrix.items():
                print(f"  {true_intent}: {dict(pred_counts)}")