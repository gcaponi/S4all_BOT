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
import logging

logger = logging.getLogger(__name__)

class EnhancedIntentClassifier:
    def __init__(self, config_path=None, dynamic_product_keywords=None):
        # Configurazioni
        self.MIN_CONFIDENCE = 0.65
        self.FALLBACK_THRESHOLD = 0.45
        
        # Soglie specifiche per intent (hybrid confidence system)
        self.INTENT_CONFIDENCE_THRESHOLDS = {
            'order': 0.85,           # Ordini: alta confidenza richiesta
            'order_confirmation': 0.80,  # Conferme ordine: alta confidenza
            'search': 0.75,          # Ricerca prodotti: media-alta
            'faq': 0.70,             # FAQ: media confidenza
            'list': 0.70,            # Lista: media confidenza
            'contact': 0.75,         # Contatti: media-alta
            'saluto': 0.60,          # Saluti: bassa (spesso ignorati)
            'fallback': 0.0          # Fallback: sempre accettato
        }
        self.USE_HYBRID = True
        
        # Inizializza componenti
        self._init_patterns()
        self._init_keywords(dynamic_product_keywords)
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
                r'^\w+\s+(grazie|per\s+favore)$',
                # FIX: Pattern per ordini vaghi con quantità
                r'\b(voglio|vorrei|mi\s+serve)\s+\d+',  # "voglio 2 ..." → order
                r'\b(prendo|dammi|ordino)\s+(quello|quella|quelli|quelle)',  # "prendo quello" → order
                r'\b(voglio|vorrei)\s+(quello|quella|quelli|quelle|quel|quella\s+roba)',  # "voglio quella roba"
                # FIX: Pattern per ordini CON metodo pagamento
                r'\b(tramite|con|via|in)\s+(bonifico|crypto|bitcoin|usdt|contrassegno)',  # "tramite bonifico"
                r'\w+.*\b(tramite|con|via)\s+(bonifico|crypto|bitcoin)',  # Prodotto + metodo pagamento
            ],
            
            "search": [
                # FIX: Pattern più specifici per evitare conflitti
                r'^(hai|avete|ce l\'hai|c\'è|vendete)\b(?!.*(stock|lista|catalogo|listino)).*\??',  # Solo all'inizio
                r'\b(che|cosa)\s+(hai|avete)\b(?!.*(stock|lista|detto|disse|menzionato)).*\??$',  # "che hai" ma non "che hai detto"
                r'\b(quanto|costa|prezzo|prezzzo)\b.*\??$',  # typo prezzzo
                r'^(quanto|costa|prezzo|prezzzo)\??$',      # typo prezzzo
                r'prezz[zo]+\s+\w+',                         # typo prezzzo

                r'^(orali|sarms|pct|peptidi|ai|sex|viagra|cialis|levitra|cut|bulk|massa|definizione)\??$',
                r'\b(consigli|meglio|confronto|quale)\b.*\??',  # Rimosso "cosa" per evitare conflitti
                r'^(che hai|cosa c\'è|novità|disponibile)\??$',  # Rimosso "stock" (ora solo in list)
                # FIX #2d: Pattern "per massa" solo con contesto di domanda
                r'^(che|cosa|quale).*(per massa|per forza|per taglio|per definizione)',  # "che hai per massa?"
                r'\b(consigli|suggerimenti).*(per massa|per forza|per taglio|per definizione)',  # "consigli per massa?"
                r'^(come funziona|info|dettagli)\??$',
                r'^(voglio|vorrei|cerco|cercavo|mi serve)\s+\w+\??$',
                r'^\w+\s+(info|informazioni)\??$'
            ],
            
            "faq": [
                r'\b(quando|dove|spedisci|arriva|consegna|pacco|tracking|corriere)\b.*\??',
                r'^(quando|dove|spedisci|arriva)\??$',
                r'\b(come pago|come si paga|metodi di pagamento|metodo di pagamento)\b.*\??',  # Solo domande sul pagamento
                r'^(bonifico|crypto|contrassegno|pagamento|metodo)\??$',  # Solo domande singole
                r'\b(sconto|minimo|offerta|promozione)\b.*\??',
                r'^(sconto|minimo|offerta|promozione)\??$',
                r'\b(sicuro|discreto|garanzia|privacy|anonimo)\b.*\??',
                r'^(sicuro|discreto|garanzia|privacy)\??$',
                r'\b(problema|help|aiuto|contatto|numero|supporto|assistenza)\b.*\??',
                r'^(problema|help|aiuto|contatto|numero|supporto)\??$',
                r'\b(tempo|giorno|giorni|settimana|settimane|modalità|come funziona)\b.*\??$',
                # NUOVI pattern FAQ specifici
                r'c\'è\s+(un\s+)?minimo',
                r'\b(quanto|quando)\s+(ci\s+mette|ci\s+vuole|tempo|giorni)\b',
                r'(ordine\s+)?minimo',
                r'\b(quanto|come)\s+(tempo|giorni|settimane)\b',
                r'\b(posso|si\s+può)\s+(ordinare|pagare)\b'
            ],
            
            "list": [
                r'^(lista|catalogo|listino|prezzi|tutto|mostra|manda|prodotti|offerte|stock|disponibile)$',
                r'^(che avete|cosa vendete|mostra tutto|manda lista)$',
                r'\b(lista|catalogo|listino|prezzi|prodotti|offerte)\b',
                r'^(fammi vedere|mostrami|visualizza)\s+(cosa|tutto)',
                r'\b(che|cosa)\s+(avete|hai|c\'è)\s+(in\s+)?stock\b',
                r'^(che|cosa)\s+(hai|avete)\??$',
                r'\b(disponibilit[àa])\b',
                # FIX #3c: Pattern aggiuntivi per stock
                r'\bstock\??$',  # "stock?"
                r'\b(cosa|che)\s+avete\b',  # "cosa avete?" generico
            ],
            
            "contact": [
                r'\b(contatto|numero|telefono|email|whatsapp|telegram|instagram)\b.*\??',
                r'^(contatto|numero|telefono|email|whatsapp)\??$',
                r'\b(scrivi|chiama|messaggio|dm|parlare|umano)\b',
                r'numero\s+(di\s+)?(telefono|cellulare)',
                r'hai\s+(whatsapp|telegram|numero)'
            ],
            
            "order_confirmation": [
                r'\b(bonifico|pagamento)\s+(effettuat|fatt|completat)',  # "bonifico effettuato"
                r'\b(ho|abbiamo)\s+(pagat|effettuat)',  # "ho pagato"
                r'\bpagat[oa]\b',  # "pagato", "pagata"
                r'\bF_\d+',  # Codice ordine "F_21"
                r'\b(via|viale|piazza|corso)\s+[A-Z][a-zA-Z\s]+,?\s*numero\s+\d+',  # Indirizzo completo
                r'\bCAP\s+\d{5}',  # CAP italiano
                r'\bindirizzo\s+di\s+consegna',  # "indirizzo di consegna"
                r'\b(nome|intestat)[oa]?\s+(a|di)\s+[A-Z]+',  # "a nome di MARIO"
                r'^(farei|farò|faremo|userò|useremo|pagherò|pagheremo)\s+(bonifico|crypto|bitcoin|usdt)',
                r'^(pago|pagherò)\s+(con|tramite|via)\s+(bonifico|crypto|bitcoin|revolut|postepay)',
                r'\b(revolut|postepay|paypal)\b',
            ],

            "fallback": [
                r'^(bot|chi\s+sei|cosa|boh|\?+)\??$',
                r'^(non\s+)?ho\s+capito$',
                r'cos\'è\s+questo'
            ]
        }
    
    def _init_keywords(self, dynamic_product_keywords=None):
        """Inizializza le liste di parole chiave"""
        # Se sono fornite keywords dinamiche, usale
        if dynamic_product_keywords:
            # Converti set in list se necessario
            if isinstance(dynamic_product_keywords, set):
                self.product_keywords = list(dynamic_product_keywords)
            else:
                self.product_keywords = dynamic_product_keywords
            logger.info(f"✅ Usate {len(self.product_keywords)} product keywords DINAMICHE dalla lista")
        else:
            # Fallback: keywords statiche (base minima)
            self.product_keywords = [
                'testo', 'testosterone', 'anavar', 'deca', 'tren', 'susta', 'sustanon',
                'winstrol', 'winny', 'masteron', 'boldo', 'boldenone', 'primo', 'primobolan',
                'dianabol', 'dbol', 'clen', 'clenbuterolo', 'hcg', 'clomid', 'kamagra',
                'tren ace', 'trenbolone', 'viagra', 'cialis', 'levitra', 'proviron',
                'arimidex', 'nolvadex', 'tamoxifen', 'clenbuterol'
            ]
            logger.info(f"⚠️ Usate {len(self.product_keywords)} product keywords STATICHE (fallback)")
        
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
        self.contact_keywords = ['contatto', 'parlare', 'umano', 'assistenza', 'supporto', 'admin', 'numero', 'telefono', 'whatsapp']
        self.list_keywords = ['lista', 'catalogo', 'listino', 'stock', 'disponibile', 'disponibili']
    
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
        Classifica un messaggio usando approccio ibrido BEST MATCH
        Returns: (intent, confidence)
        """
        message_lower = message.strip().lower()
        self.stats['total_requests'] += 1
        
        # ========================================
        # EARLY CHECK: "VORREI/Voglio ORDINARE" senza prodotto = FAQ (priorità assoluta)
        # ========================================
        if re.search(r'\b(vorrei|voglio)\s+ordinare\b', message_lower):
            # Verifica se è specificato un prodotto
            has_product = any(product in message_lower for product in self.product_keywords)
            
            if not has_product:
                # "ciao vorrei ordinare" -> FAQ (come si ordina?)
                if debug:
                    print(f"⏭️ 'Vorrei ordinare' senza prodotto specifico -> FAQ")
                return "faq", 1.0  # Confidence 1.0 per sovrascrivere ML
        
        # ========================================
        # EARLY CHECK: COME SI ORDINA/FA A ORDINARE = FAQ
        # ========================================
        if re.search(r'\bcome\s+(faccio|posso|si\s+fa)\s+a\s+ordinare\b', message_lower) or \
        re.search(r'\bcome\s+si\s+ordina\b', message_lower) or \
        re.search(r'\bprocedura\s+per\s+ordinare\b', message_lower):
            if debug:
                print(f"⏭️ Domanda su procedura d'ordine -> FAQ")
            return "faq", 1.0
        
        # ========================================
        # EARLY CHECK: COURTESY PATTERNS (priorità assoluta)
        # ========================================
        courtesy_patterns = [
            r'\bgrazie\b.*\battendo\b',
            r'\bok\b.*\bgrazie\b',
            r'\battendo\b.*\baggiornamenti\b',
            r'\bperfetto\b.*\bgrazie\b',
            r'\bva bene\b.*\bgrazie\b'
        ]
        
        # ========================================
        # EARLY CHECK: CONVERSAZIONI POST-ACQUISTO (richiedono umano) = FALLBACK MUTO
        # ========================================
        human_required_patterns = [
            # Domande su preparazione/prodotti ricevuti
            r'\bcome\s+va\s+preparato\b',
            r'\bquanta\s+acqua\b',
            r'\bdosi\b',
            r'\bpreparare\b',
            # Problemi consegna
            r'\bnon\s+sono\s+stato\b',
            r'\bnon\s+sono\s+a\s+casa\b',
            r'\bconsegnato\b.*\bnon\b',
            r'\britirato\b',
            r'\bmi\s+dice\s+che\b',
            r'\bmi\s+è\s+arrivato\b',
            # Espressioni conversazionali di chiusura/seguimento
            r'\bsperiamo\s+bene\b',
            r'\btra\s+l\'altro\b',
            r'\bah[, ]?\s*ok\b',
            r'\bscusa\s+(il|il)\s+disturbo\b',
            r'\bmi\s+serve\b.*\baiuto\b',
            # Pattern "ok perfetto grazie" (fallback cortese)
            r'^(ok|perfetto|bene|ottimo)\s+(grazie|perfetto)$',
            r'\bok\b.*\bperfetto\b.*\bgrazie\b'
        ]
        
        for pattern in human_required_patterns:
            if re.search(pattern, message_lower, re.I):
                if debug:
                    print(f"⏭️ Conversazione umana/assistenza richiesta - fallback muto")
                return "fallback_mute", 1.0  # Intent speciale per non rispondere
        
        # ========================================
        # EARLY CHECK: SALUTI DI CHIUSURA/CORTESIA
        # ========================================
        goodbye_patterns = [
            r'^(ok|va bene|perfetto|bene|ottimo)\s*(grazie)?$',
            r'^(grazie)\s*(mille)?$',
            r'\bgrazie\b.*\btutto\b',
            r'^(ciao|salve|buongiorno|buonasera)\s*(grazie)?$'
        ]
        
        for pattern in goodbye_patterns:
            if re.search(pattern, message_lower, re.I):
                if debug:
                    print(f"⏭️ Saluto/cortesia detected")
                return "fallback_mute", 1.0

        numeric_stopwords = ['uno', 'due', 'tre', 'quattro', 'cinque', 'sei', 'sette', 
                     'otto', 'nove', 'dieci', 'confezioni', 'confezione', 
                     'flaconi', 'flacone', 'pezzi', 'pezzo', 'scatole', 'scatola']

        for pattern in courtesy_patterns:
            if re.search(pattern, message_lower, re.I):
                if debug:
                    print(f"⏭️ Courtesy pattern detected - skip classification")
                return "fallback", 0.0

        # ========================================
        # EARLY CHECK: QUANTO COSTA SERVIZIO FAQ = FAQ (priorità assoluta)
        # ========================================
        # Intercetta "quanto costa spedizione/consegna/pagamento/bonifico" PRIMA del ML
        # perché il ML tende a classificare come SEARCH (pensando siano prodotti)
        servizi_faq_keywords = ['spedizion', 'consegn', 'pagament', 'bonific']
        prezzo_keywords = ['quanto', 'prezzo', 'costo', 'costa', 'costano']
        
        if any(servizio in message_lower for servizio in servizi_faq_keywords):
            if any(prezzo in message_lower for prezzo in prezzo_keywords):
                if debug:
                    print(f"⏭️ Domanda su costo servizio FAQ detected -> FAQ")
                return "faq", 1.0  # Confidence 1.0 per sovrascrivere ML

        # RACCOLTA TUTTI I RISULTATI
        all_results = []
        
        # 1. REGOLE REGEX (priorità alta)
        regex_result = self._classify_by_regex(message_lower, debug)
        if regex_result:
            intent, confidence = regex_result
            if confidence >= self.MIN_CONFIDENCE:
                all_results.append(("regex", intent, confidence))
                if debug:
                    print(f"🔍 Regex match: {intent} ({confidence:.2f})")
        
        # 2. MODELLO ML
        if self.is_trained and self.USE_HYBRID:
            ml_result = self._classify_by_ml(message, debug)
            if ml_result:
                intent, confidence = ml_result
                if confidence >= self.FALLBACK_THRESHOLD:
                    all_results.append(("ml", intent, confidence))
                    if debug:
                        print(f"🔍 ML prediction: {intent} ({confidence:.2f})")
        
        # 3. REGOLE SEMPLICI
        simple_result = self._classify_by_simple_rules(message_lower, debug)
        if simple_result:
            intent, confidence = simple_result
            if confidence >= self.FALLBACK_THRESHOLD:
                all_results.append(("simple", intent, confidence))
                if debug:
                    print(f"🔍 Simple rules: {intent} ({confidence:.2f})")
        
        # SELEZIONE BEST MATCH
        if all_results:
            # Ordina per confidence (decrescente)
            all_results.sort(key=lambda x: x[2], reverse=True)
            
            best_method, best_intent, best_confidence = all_results[0]
            
            # Log per debug
            if debug and len(all_results) > 1:
                print(f"🏆 Best Match Comparison:")
                for i, (method, intent, conf) in enumerate(all_results, 1):
                    indicator = "✅" if i == 1 else "  "
                    print(f"   {indicator} {method}: {intent} ({conf:.2f})")
            
            # Aggiorna statistiche
            if best_method == "regex":
                self.stats['regex_classifications'] += 1
            elif best_method == "ml":
                self.stats['ml_classifications'] += 1
            elif best_method == "simple":
                self.stats['simple_classifications'] += 1
            
            return best_intent, best_confidence
    
        # 4. FALLBACK
        self.stats['fallback_classifications'] += 1
        if debug:
            print(f"🔍 No match found → fallback")
        return "fallback", 0.0
    
    def _apply_fallback_rules(self, message: str, debug=False) -> tuple:
        """
        Fallback rules ultraleggere per casi ambigui.
        Usate solo quando ML confidence è bassa.
        Returns: (intent, confidence) o None
        """
        message_lower = message.lower()
        
        # Ordini con quantità esplicita
        if re.search(r'\b(voglio|ordino|prenoto|vorrei)\s+\d', message_lower):
            if debug:
                print("🔧 Fallback rule: ordine con quantità")
            return "order", 0.90
        
        # Ricerca prezzo/costo con prodotto
        if re.search(r'\b(quanto|prezzo|costo)\s+(costa|è|per|del|della)\s+\w{3,}', message_lower):
            if debug:
                print("🔧 Fallback rule: richiesta prezzo")
            return "search", 0.88
        
        # Domande FAQ chiare
        if re.search(r'\b(come|quando)\s+(pago|spedisci|arriva|ordino)\b', message_lower):
            if debug:
                print("🔧 Fallback rule: domanda procedurale")
            return "faq", 0.85
        
        # Lista prodotti
        if re.search(r'\b(lista|catalogo|tutto|mostra|prodotti)\b', message_lower):
            if debug:
                print("🔧 Fallback rule: richiesta lista")
            return "list", 0.87
        
        return None
    
    def classify_with_threshold(self, message: str, debug=False) -> tuple:
        """
        Classifica con controllo soglie specifiche per intent.
        Applica fallback rules se confidence troppo bassa.
        Returns: (intent, confidence)
        """
        # Classifica normalmente
        intent, confidence = self.classify(message, debug)
        
        # Ottieni soglia specifica per questo intent
        required_threshold = self.INTENT_CONFIDENCE_THRESHOLDS.get(intent, self.MIN_CONFIDENCE)
        
        if debug:
            print(f"🎯 Intent: {intent} (conf: {confidence:.2f}, required: {required_threshold:.2f})")
        
        # Se confidence insufficiente, prova fallback rules
        if confidence < required_threshold and intent != "fallback":
            if debug:
                print(f"⚠️ Confidence troppo bassa, applico fallback rules...")
            
            fallback_result = self._apply_fallback_rules(message, debug)
            
            if fallback_result:
                fallback_intent, fallback_conf = fallback_result
                if debug:
                    print(f"✅ Fallback rules: {fallback_intent} ({fallback_conf:.2f})")
                return fallback_intent, fallback_conf
            
            # Nessuna fallback rule matchata → ritorna fallback
            if debug:
                print(f"❌ Nessuna fallback rule matchata → fallback")
            return "fallback", 0.0
        
        return intent, confidence
            
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
        
        courtesy_patterns = [
            r'\bgrazie\b.*\battendo\b',
            r'\bok\b.*\bgrazie\b',
            r'\battendo\b.*\baggiornamenti\b',
            r'\bperfetto\b.*\bgrazie\b',
            r'\bva bene\b.*\bgrazie\b'
        ]

        for pattern in courtesy_patterns:
            if re.search(pattern, message, re.I):
                return None  # Non classificare come order

        if not words:
            return None
        
        has_product = any(product in message for product in self.product_keywords)
        has_category = any(category in message for category in self.category_keywords)
        is_question = '?' in message
        
        # Fuzzy matching per errori battitura (solo se non ha match esatto)
        if not has_product and not has_category:
            from difflib import SequenceMatcher
            for word in words:
                if len(word) >= 4:  # Solo parole >= 4 caratteri
                    for product in self.product_keywords:
                        if len(product) >= 4:
                            similarity = SequenceMatcher(None, word, product).ratio()
                            if similarity >= 0.85:  # 85% similarità
                                has_product = True
                                if debug:
                                    print(f"🔍 Fuzzy match: '{word}' ~ '{product}' ({similarity:.2f})")
                                break
                    if has_product:
                        break
        
        # ============================================
        # ORDINE PRIORITÀ (DAL PIÙ SPECIFICO AL GENERICO)
        # ============================================
        
        # 0. CONTACT KEYWORDS (priorità assoluta)
        if any(kw in message for kw in self.contact_keywords):
            # Se chiede numero/telefono/whatsapp → contact
            if any(w in message for w in ['numero', 'telefono', 'whatsapp', 'telegram', 'email']):
                if 'tracking' not in message:  # Eccezione: "numero tracking" = FAQ
                    return "contact", 0.98
        
        # 1. FAQ KEYWORDS (priorità massima per domande procedurali)
        # Include forme singolari E plurali
        faq_strong_keywords = [
            'spedizione', 'spedizioni', 'consegna', 'consegne', 
            'pagamento', 'pagamenti', 'bonifico', 'bonifici',
            'crypto', 'tempo', 'giorni', 'giorno', 
            'minimo', 'sconto', 'sconti'
        ]
        if any(faq_word in message for faq_word in faq_strong_keywords):
            # PRIORITÀ ASSOLUTA: domande su costo servizi = FAQ (non search prodotti!)
            # Es: "quanto costa spedizione", "quanto costano consegne", "costo bonifico"
            servizi_faq = ['spedizion', 'consegn', 'pagament', 'bonific']  # match parziale per plurali
            if any(servizio in message for servizio in servizi_faq):
                if any(w in message for w in ['quanto', 'prezzo', 'costo', 'costa', 'costano']):
                    return "faq", 0.90
            
            # ECCEZIONE: "quanto costa PRODOTTO" è search, non FAQ
            if any(w in message for w in ['quanto', 'prezzo', 'costo']) and any(w in message for w in ['costa', 'costano', 'coste', 'costi']):
                if has_product:
                    return "search", 0.85
            
            # Default: tutti i messaggi con FAQ keywords → FAQ
            return "faq", 0.85
        
        # 2. PREZZO/QUANTO + PRODOTTO = SEARCH (non order!)
        if any(w in message for w in ['prezzo', 'quanto', 'costa', 'costo']):
            if has_product or has_category:
                return "search", 0.88  # "prezzo deca" = search
        
        # 2.5 ANALISI ORDINE IMPLICITO (Sistema a punteggio avanzato)
        # Sostituisce la vecchia logica semplice con _analyze_implicit_order
        implicit_order_confidence = self._analyze_implicit_order(message, message.lower())
        if implicit_order_confidence > 0:
            return "order", max(0.98, implicit_order_confidence)
        
        # 2.5 NUMERI SCRITTI + PRODOTTO = ORDER
        # "quattro anavar", "tre testosterone", "due deca"
        numeri_scritti = ['uno', 'due', 'tre', 'quattro', 'cinque', 'sei', 'sette', 
                         'otto', 'nove', 'dieci', 'undici', 'dodici']
        for numero in numeri_scritti:
            if numero in message and (has_product or has_category):
                return "order", 0.95
        
        # 2.6 DIALETTO "ME SERVE" + PRODOTTO = ORDER
        # "me serve testo", "me servono anavar"
        if re.search(r'\bme\s+serv[eo]', message) and (has_product or has_category):
            return "order", 0.93
        
        # 2.7 COURTESY "PERFETTO/OK ATTENDO" = FALLBACK
        courtesy_attendo = [
            r'\b(perfetto|ok|va bene|bene)\s+(attendo|aspetto)',
            r'\battendo\s+(aggiornamenti|notizie|risposta)',
            r'\baspetto\s+(notizie|aggiornamenti)'
        ]
        for pattern in courtesy_attendo:
            if re.search(pattern, message, re.I):
                return "fallback", 0.95
        
        # 3. WISH VERBS + PRODOTTO = ORDER (CORRETTO!)
        if any(verb in message for verb in self.wish_verbs):
            if has_product or has_category:
                return "order", 0.90  # "voglio anavar" = ordine
            else:
                # FIX: Riferimenti vaghi comuni negli ordini
                vague_refs = ['quello', 'quella', 'quelli', 'quelle', 'cose', 'roba', 
                             'quella roba', 'quelle cose', 'questi', 'queste']
                
                # Se ha numeri (es. "voglio 2 di quelle") → probabilmente order vago
                if any(char.isdigit() for char in message):
                    return "order", 0.82  # "voglio 2 di quelle cose"
                # Se ha riferimenti vaghi → probabilmente order contestuale
                elif any(vague in message for vague in vague_refs):
                    return "order", 0.80  # "voglio quella roba", "prendo quelle"
                # Altrimenti è una ricerca generica
                return "search", 0.70  # "voglio qualcosa per massa" = ricerca
        
        # 4. ORDER VERBS = ORDER (anche senza prodotto specifico)
        if any(verb in message for verb in self.order_verbs):
            # "prendo quello che hai detto" = order anche senza prodotto
            return "order", 0.85
        
        # 5. PRODOTTI con domande -> SEARCH
        if has_product or has_category:
            if is_question:
                return "search", 0.80  # "hai anavar?"
            elif len(words) <= 2:
                return "search", 0.75  # "testo"
        
        # 6. Singole parole (dictionary lookup)
        if len(words) == 1:
            word_scores = {
                'lista': ("list", 0.90), 'catalogo': ("list", 0.90), 'prezzi': ("list", 0.90),
                'stock': ("list", 0.90), 'disponibilità': ("list", 0.90), 'listino': ("list", 0.90),  # ← FIX #3
                'orali': ("search", 0.85), 'sarms': ("search", 0.85), 'pct': ("search", 0.85),
                'ok': ("order", 0.80), 'si': ("order", 0.80), 'fatto': ("order", 0.80),
                'help': ("faq", 0.80), 'supporto': ("faq", 0.80),
                'ciao': ("saluto", 0.95), 'hey': ("saluto", 0.95),
            }
            if words[0] in word_scores:
                return word_scores[words[0]]
        
        # 7. Coppie di parole
        if len(words) == 2:
            first = words[0]
            if first in self.order_verbs:
                return "order", 0.82
            if first in ['hai', 'costa', 'prezzo', 'quanto']:
                return "search", 0.80
            if first in self.question_words:
                return "faq", 0.78
        
        # 8. Domande generiche
        if is_question:
            if any(word in message for word in ['quando', 'dove', 'come']):
                return "faq", 0.75
            else:
                return "search", 0.70
        
        # 8.5 SALUTI CON SLANG (prima della regola #9)
        # Cattura: "ciao bro", "hey fra", "yo zi"
        if len(words) == 2:
            first_word = words[0]
            second_word = words[1]
            saluto_words = ['ciao', 'hey', 'yo', 'ehi', 'salve']
            slang_words = ['bro', 'fra', 'zi', 'bello', 'amico', 'boss', 'capo']
            
            if first_word in saluto_words and second_word in slang_words:
                return "saluto", 0.90
            # Anche inverso: "bro ciao"
            if first_word in slang_words and second_word in saluto_words:
                return "saluto", 0.90
        
        # 9. FALLBACK INTELLIGENTE: query brevi (probabilmente nomi prodotti)
        # Es: "trembo", "bpc 157", "gh", "tb500"
        if len(words) <= 3 and len(message) >= 3 and len(message) <= 25:
            # Escludi stopwords comuni + slang saluti
            stopwords_comuni = {
                'ciao', 'buongiorno', 'sera', 'grazie', 'ok', 'si', 'no', 
                'cosa', 'come', 'quando',
                'bro', 'fra', 'zi', 'bello', 'amico', 'boss', 'capo'  # ← SLANG AGGIUNTO
            }
            clean_words = [w for w in words if w not in stopwords_comuni]
            
            if clean_words:  # Se rimane qualcosa dopo aver tolto le stopwords
                return "search", 0.72  # Probabilmente cerca un prodotto
        
        return None
            
    def _analyze_implicit_order(self, text: str, text_lower: str) -> float:
        """
        Analizza se il testo è un ordine implicito usando un sistema a punteggio.
        Adattato dalla vecchia funzione _check_ordine_reale.
        Returns: confidence score (0.0 - 1.0)
        """
        # Filtro lunghezza minima
        if len(text.strip()) < 5:
            return 0.0
            
        # ESCLUSIONI FORTI
        strong_exclusions = [
            r'\bcome\s+(faccio|posso|si\s+fa)\s+(a\s+)?ordinar',
            r'\bcome\s+ordino\b',
            r'\bcome\s+si\s+ordina\b',
            r'\bprocedura\s+per\s+ordinar',
            r'\bper\s+ordinar.*\bcome\b',
            r'\baiuto.*\border',
            r'\bvorrei\s+(fare|effettuare)\s+(un[ao]?)?\s*ordine\s*$',
            r'\bvoglio\s+(fare|effettuare)\s+(un[ao]?)?\s*ordine\s*$',
            r'\bvorrei\s+ordinar[ei]\s*$',
            r'\bvoglio\s+ordinar[ei]\s*$',
        ]
        
        for pattern in strong_exclusions:
            if re.search(pattern, text_lower, re.I):
                return 0.0

        score = 0
        matched_indicators = []
        
        # 1. Simboli valuta o prezzi (Es: "25$")
        if re.search(r'[€$£¥₿]|\d+\s*(euro|eur|usd|gbp)', text_lower):
            score += 3
            matched_indicators.append('prezzo')
        
        # 2. Quantità chiare (Es: "2 x testo", "3 pezzi", "testo 2", "quattro anavar")
        quantita_patterns = [
            r'\d+\s*x\s*\w+',        # "2 x testo"
            r'\d+\s+[a-z]{3,}',      # "1 testo"
            r'[a-z]{3,}\s+\d+',      # "testo 2" ← NUOVO
            r'\b\d+\s*pezz[io]',
            r'\b\d+\s*confezioni',
            r'\bun[ao]?\s+(confezione|scatola|pezzo|flacone|fiala|boccetta)',
            # Numeri scritti + prodotto/unità
            r'\b(uno|due|tre|quattro|cinque|sei|sette|otto|nove|dieci)\s+[a-z]{4,}',  # "quattro anavar" ← NUOVO
            r'\b(uno|due|tre|quattro|cinque)\s+(confezioni|scatole|pezzi|fiale)',      # "tre confezioni"
        ]
        
        for pattern in quantita_patterns:
            if re.search(pattern, text_lower):
                score += 2
                matched_indicators.append('quantita')
                break
                
        # 3. Separatori di lista (Es: ",", ";", a capo)
        if text.count(',') >= 1 or text.count(';') >= 1 or text.count('\n') >= 1:
            score += 1
            matched_indicators.append('separatori')
            
        # 4. Spedizione/Indirizzo
        if re.search(r'\b(via|piazza|spedizione|consegna|cap)\b', text_lower):
            score += 1
            matched_indicators.append('spedizione')
            
        # 5. Keyword ordine implicito
        if any(kw in text_lower for kw in ['prendo', 'voglio', 'mi serve', 'aggiungi']):
            score += 1
            matched_indicators.append('keyword_implicit')
            
        # CALCOLO CONFIDENZA
        # Soglia minima: 2 punti (es: "1 testo" = 2pt) -> confidence 0.85
        # 3 punti (es: "1 testo 25$") -> confidence 0.90
        # 4+ punti -> confidence 0.95
        
        if score >= 4:
            return 0.95
        elif score >= 3:
            return 0.90
        elif score >= 2:
            # Se ha solo 2 punti, deve avere almeno un prodotto valido per essere sicuro
            has_prod = any(p in text_lower for p in self.product_keywords)
            if has_prod:
                return 0.88
            return 0.75 # Meno sicuro senza prodotto noto
            
        return 0.0
    
    def _calculate_regex_confidence(self, message, intent, pattern):
        """Calcola confidence score per match regex"""
        # Aumentata base score per garantire priorità su ML
        # Se c'è un match regex, vogliamo che vinca quasi sempre (0.95 - 1.0)
        base_score = 0.95
        
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            matched_text = match.group()
            match_ratio = len(matched_text) / len(message)
            # Bonus per match più lunghi, max 1.0
            bonus = match_ratio * 0.05
            return min(1.0, base_score + bonus)
        
        return base_score
    
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
            self.contact_keywords = data.get('contact_keywords', self.contact_keywords)
            self.list_keywords = data.get('list_keywords', self.list_keywords)
            
            # Converti dict in defaultdict per evitare KeyError
            stats_data = data.get('stats', {})
            self.stats = defaultdict(int, stats_data)
            
            confusion_data = data.get('confusion_matrix', {})
            self.confusion_matrix = defaultdict(lambda: defaultdict(int))
            for key, value in confusion_data.items():
                self.confusion_matrix[key] = defaultdict(int, value)
            
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

# End intent_classifier.py