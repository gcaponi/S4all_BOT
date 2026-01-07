"""
Sistema di Classificazione Intenti - Versione Umana
Ragiona per priorità come farebbe un essere umano
"""
import re
from typing import Dict, List, Tuple
from dataclasses import dataclass
from enum import Enum

class IntentType(Enum):
    """Tipi di intenzioni possibili"""
    RICHIESTA_LISTA = "lista"           # Vuole vedere il catalogo
    INVIO_ORDINE = "ordine"             # Sta inviando un ordine reale
    DOMANDA_FAQ = "faq"                 # Ha una domanda generica
    RICERCA_PRODOTTO = "ricerca"        # Cerca un prodotto specifico
    SALUTO = "saluto"                   # Solo saluta
    FALLBACK = "fallback"               # Non capito

@dataclass
class IntentResult:
    """Risultato dell'analisi dell'intento"""
    intent: IntentType
    confidence: float  # 0.0 - 1.0
    reason: str        # Perché è stata scelta questa intenzione
    matched_keywords: List[str]
    
class IntentClassifier:
    """
    Classificatore intelligente che ragiona per priorità umane
    
    LOGICA:
    1. Prima controlla COSA VUOLE l'utente (richiesta esplicita)
    2. Poi controlla COSA STA FACENDO (azione)
    3. Infine controlla COSA CHIEDE (domanda)
    """
    
    def __init__(self, lista_keywords: set = None):
        self.lista_keywords = lista_keywords or set()
        
        # PRIORITÀ 1: Richieste esplicite (più forte)
        self.richiesta_lista_patterns = {
            # Frasi che CHIEDONO esplicitamente la lista
            'voglio_lista': [
                r'\bvoglio\s+(la\s+)?lista\b',
                r'\bvoglio\s+(il\s+)?listino\b',
                r'\bvoglio\s+(il\s+)?catalogo\b',
                r'\bvoglio\s+vedere\s+(i\s+)?prodotti\b',
            ],
            'manda_lista': [
                r'\bmanda(mi)?\s+(la\s+)?lista\b',
                r'\binvia(mi)?\s+(la\s+)?lista\b',
                r'\bmanda(mi)?\s+(il\s+)?listino\b',
                r'\bmi\s+mandi\s+(la\s+)?lista\b',
                r'\bpuoi\s+mandar(mi|e)\s+(la\s+)?lista\b',
            ],
            'mostra_lista': [
                r'\bmostra(mi)?\s+(la\s+)?lista\b',
                r'\bfammi\s+vedere\s+(la\s+)?lista\b',
                r'\bfai\s+vedere\s+(la\s+)?lista\b',
            ],
            'dammi_lista': [
                r'\bdammi\s+(la\s+)?lista\b',
                r'\bdai\s+(la\s+)?lista\b',
            ],
            'lista_diretta': [
                r'^\s*lista\s*[.!?]?\s*$',
                r'^\s*listino\s*[.!?]?\s*$',
                r'^\s*catalogo\s*[.!?]?\s*$',
                r'^\s*prezzi\s*[.!?]?\s*$',
            ]
        }
        
        # PRIORITÀ 2: Indicatori di ordine REALE (non domande su ordini)
        self.ordine_real_indicators = {
            # L'utente sta INVIANDO un ordine
            'formato_ordine': lambda text: self._has_order_format(text),
            'ha_quantita': lambda text: bool(re.search(r'\d+\s*x\s+\w+', text, re.I)),
            'ha_prezzo': lambda text: bool(re.search(r'[€$£¥₿]|\d+\s*(euro|eur|usd)', text, re.I)),
            'ha_pagamento': lambda text: any(kw in text.lower() for kw in [
                'bonifico', 'usdt', 'crypto', 'bitcoin', 'btc', 'eth', 'pagherò', 'pago con'
            ]),
            'ha_indirizzo': lambda text: bool(re.search(r'\b(via|piazza|corso|viale)\s+\w+', text, re.I)),
        }
        
        # ESCLUSIONI per ordine (frasi che NON sono ordini)
        self.ordine_exclusions = [
            r'\bcome\s+(faccio|si\s+fa|posso)\s+.*ordine\b',
            r'\bvoglio\s+(fare|effettuare)\s+.*ordine\b',
            r'\bvorrei\s+(fare|effettuare)\s+.*ordine\b',
            r'\bper\s+ordinar[ei]\b',
            r'\bcome\s+ordino\b',
            r'\bcome\s+si\s+ordina\b',
        ]
        
        # PRIORITÀ 3: Domande FAQ (parole interrogative)
        self.faq_indicators = {
            'parole_interrogative': [
                'come', 'quando', 'quanto', 'dove', 'perché', 'perchè', 
                'cosa', 'chi', 'quale', 'quali'
            ],
            'richieste_info': [
                'vorrei sapere', 'voglio sapere', 'mi serve sapere',
                'mi puoi dire', 'puoi dirmi', 'mi dici',
                'ho bisogno di', 'mi serve', 'informazioni su'
            ],
            'temi_faq': {
                'spedizione': ['spedizione', 'spedito', 'spedisci', 'corriere', 'pacco', 'consegna'],
                'tracking': ['tracking', 'traccia', 'tracciamento', 'codice', 'dove', 'arriva'],
                'tempi': ['quando arriva', 'quanto tempo', 'giorni', 'tempistiche'],
                'pagamento': ['pagare', 'pagamento', 'metodo', 'bonifico', 'crypto', 'come pago'],
                'ordini': ['come ordino', 'come si ordina', 'fare ordine', 'procedura'],
            }
        }
        
        # PRIORITÀ 4: Ricerca prodotto specifico
        self.ricerca_indicators = [
            r'\bhai\s+\w+\b',
            r'\bc[\'']è\s+\w+\b',
            r'\bcosto\s+(di|del|della)\s+\w+\b',
            r'\bprezzo\s+(di|del|della)\s+\w+\b',
            r'\bquanto\s+costa\s+\w+\b',
        ]

    def classify(self, text: str) -> IntentResult:
        """
        Classifica l'intento seguendo la logica umana
        
        ORDINE DI PRIORITÀ:
        1. Richiesta lista esplicita (manda/voglio/mostra lista)
        2. Ordine reale (formato ordine + pagamento + quantità)
        3. Domanda FAQ (come/quando/quanto + tema)
        4. Ricerca prodotto (hai X? quanto costa Y?)
        5. Saluto/Fallback
        """
        if not text or len(text.strip()) < 2:
            return IntentResult(IntentType.FALLBACK, 0.0, "Testo vuoto", [])
        
        text_lower = text.lower()
        text_norm = self._normalize(text)
        
        # ========================================
        # PRIORITÀ 1: RICHIESTA LISTA ESPLICITA
        # ========================================
        lista_result = self._check_richiesta_lista(text_norm, text_lower)
        if lista_result.confidence >= 0.9:
            return lista_result
        
        # ========================================
        # PRIORITÀ 2: ORDINE REALE
        # ========================================
        # PRIMA: Escludi domande sugli ordini
        if self._is_domanda_su_ordine(text_norm):
            # È una domanda tipo "come faccio a ordinare?"
            faq_result = self._check_faq(text_norm, text_lower)
            if faq_result.confidence > 0.5:
                return faq_result
        
        # POI: Controlla se è un ordine vero
        ordine_result = self._check_ordine_reale(text, text_lower)
        if ordine_result.confidence >= 0.7:
            return ordine_result
        
        # ========================================
        # PRIORITÀ 3: DOMANDA FAQ
        # ========================================
        faq_result = self._check_faq(text_norm, text_lower)
        if faq_result.confidence >= 0.6:
            return faq_result
        
        # ========================================
        # PRIORITÀ 4: RICERCA PRODOTTO
        # ========================================
        ricerca_result = self._check_ricerca_prodotto(text_norm, text_lower)
        if ricerca_result.confidence >= 0.5:
            return ricerca_result
        
        # ========================================
        # PRIORITÀ 5: SALUTO o FALLBACK
        # ========================================
        if self._is_saluto(text_lower):
            return IntentResult(
                IntentType.SALUTO, 
                0.95, 
                "Rilevato saluto", 
                ['saluto']
            )
        
        # Fallback: scegli il migliore tra quelli trovati
        candidates = [lista_result, ordine_result, faq_result, ricerca_result]
        best = max(candidates, key=lambda x: x.confidence)
        
        if best.confidence > 0.3:
            return best
        
        return IntentResult(
            IntentType.FALLBACK, 
            0.0, 
            "Nessun intento riconosciuto con confidenza sufficiente", 
            []
        )
    
    # ========================================
    # METODI DI ANALISI SPECIFICI
    # ========================================
    
    def _check_richiesta_lista(self, text_norm: str, text_lower: str) -> IntentResult:
        """Controlla se l'utente chiede esplicitamente la lista"""
        matched = []
        score = 0.0
        
        # Controlla tutti i pattern di richiesta lista
        for category, patterns in self.richiesta_lista_patterns.items():
            for pattern in patterns:
                if re.search(pattern, text_norm, re.I):
                    matched.append(category)
                    score = 1.0  # Massima confidenza
                    return IntentResult(
                        IntentType.RICHIESTA_LISTA,
                        score,
                        f"Richiesta esplicita lista: {category}",
                        matched
                    )
        
        # Controlla keyword "lista" anche da sola
        parole = text_lower.split()
        if any(kw in parole for kw in ['lista', 'listino', 'catalogo', 'prezzi']):
            # Se c'è solo "lista" e poche altre parole, è una richiesta
            if len(parole) <= 5:
                return IntentResult(
                    IntentType.RICHIESTA_LISTA,
                    0.85,
                    "Keyword lista in frase breve",
                    ['lista_keyword']
                )
            # Altrimenti potrebbe essere parte di una frase più lunga
            score = 0.5
            matched.append('lista_keyword')
        
        return IntentResult(IntentType.RICHIESTA_LISTA, score, "Check lista", matched)
    
    def _check_ordine_reale(self, text: str, text_lower: str) -> IntentResult:
        """Controlla se è un ordine vero (non una domanda sugli ordini)"""
        score = 0.0
        matched = []
        
        # Calcola score basato su indicatori
        max_score = len(self.ordine_real_indicators)
        for name, check_func in self.ordine_real_indicators.items():
            if check_func(text):
                score += 1.0
                matched.append(name)
        
        confidence = score / max_score
        
        # Se ha almeno 3 indicatori su 5, è probabilmente un ordine
        if score >= 3:
            return IntentResult(
                IntentType.INVIO_ORDINE,
                confidence,
                f"Ordine reale: {score}/{max_score} indicatori",
                matched
            )
        
        return IntentResult(IntentType.INVIO_ORDINE, confidence, "Check ordine", matched)
    
    def _check_faq(self, text_norm: str, text_lower: str) -> IntentResult:
        """Controlla se è una domanda FAQ"""
        score = 0.0
        matched = []
        
        # 1. Parole interrogative (+0.3)
        parole = text_lower.split()
        for interrogativa in self.faq_indicators['parole_interrogative']:
            if interrogativa in parole:
                score += 0.3
                matched.append(f"interrogativa:{interrogativa}")
                break
        
        # 2. Richieste info (+0.3)
        for frase in self.faq_indicators['richieste_info']:
            if frase in text_lower:
                score += 0.3
                matched.append(f"richiesta_info:{frase}")
                break
        
        # 3. Temi FAQ specifici (+0.4)
        for tema, keywords in self.faq_indicators['temi_faq'].items():
            if any(kw in text_lower for kw in keywords):
                score += 0.4
                matched.append(f"tema:{tema}")
                break
        
        # 4. Presenza di "?" (+0.2)
        if '?' in text_norm:
            score += 0.2
            matched.append("punto_interrogativo")
        
        confidence = min(score, 1.0)
        
        return IntentResult(
            IntentType.DOMANDA_FAQ,
            confidence,
            f"FAQ score: {confidence:.2f}",
            matched
        )
    
    def _check_ricerca_prodotto(self, text_norm: str, text_lower: str) -> IntentResult:
        """Controlla se sta cercando un prodotto specifico"""
        score = 0.0
        matched = []
        
        # Pattern di ricerca
        for pattern in self.ricerca_indicators:
            if re.search(pattern, text_norm, re.I):
                score += 0.4
                matched.append(f"pattern:{pattern[:20]}")
        
        # Parole chiave dalla lista prodotti
        parole = text_lower.split()
        prodotti_trovati = [p for p in parole if p in self.lista_keywords and len(p) > 3]
        if prodotti_trovati:
            score += 0.3 * min(len(prodotti_trovati), 2)
            matched.extend([f"prodotto:{p}" for p in prodotti_trovati[:3]])
        
        confidence = min(score, 1.0)
        
        return IntentResult(
            IntentType.RICERCA_PRODOTTO,
            confidence,
            f"Ricerca prodotto score: {confidence:.2f}",
            matched
        )
    
    # ========================================
    # HELPER METHODS
    # ========================================
    
    def _is_domanda_su_ordine(self, text: str) -> bool:
        """Controlla se è una DOMANDA sugli ordini (non un ordine vero)"""
        for pattern in self.ordine_exclusions:
            if re.search(pattern, text, re.I):
                return True
        return False
    
    def _has_order_format(self, text: str) -> bool:
        """Controlla se ha un formato tipico di ordine"""
        # Ha numeri + virgole/punti (lista prodotti)
        has_numbers = bool(re.search(r'\d', text))
        has_separators = text.count(',') >= 2 or text.count(';') >= 1
        
        # Ha righe multiple (ordine strutturato)
        has_lines = text.count('\n') >= 2
        
        # Ha almeno 20 caratteri (non solo "voglio X")
        is_substantial = len(text.strip()) >= 20
        
        return has_numbers and (has_separators or has_lines) and is_substantial
    
    def _is_saluto(self, text: str) -> bool:
        """Controlla se è solo un saluto"""
        saluti = ['ciao', 'buongiorno', 'buonasera', 'salve', 'hey', 'hello', 'hi']
        parole = text.split()
        
        # Se ha solo 1-3 parole e contiene un saluto
        if len(parole) <= 3 and any(s in text for s in saluti):
            return True
        return False
    
    def _normalize(self, text: str) -> str:
        """Normalizza il testo mantenendo spazi"""
        text = re.sub(r'[^\w\s?!.,]', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip().lower()


# ========================================
# ESEMPIO DI UTILIZZO
# ========================================

if __name__ == "__main__":
    # Carica parole chiave dalla lista prodotti (simulato)
    lista_keywords = {'integratori', 'proteine', 'creatina', 'vitamine', 'omega3'}
    
    classifier = IntentClassifier(lista_keywords)
    
    # Test cases
    test_messages = [
        "buonasera vorrei fare un ordine, inviami il listino",
        "ho bisogno del tracking",
        "2x proteine, 1x creatina, bonifico, via roma 10",
        "come faccio a ordinare?",
        "quanto costa la creatina?",
        "lista",
        "ciao",
        "quando arriva il pacco?",
    ]
    
    print("=" * 60)
    print("TEST CLASSIFICATORE INTENTI")
    print("=" * 60)
    
    for msg in test_messages:
        result = classifier.classify(msg)
        print(f"\n📝 Messaggio: '{msg}'")
        print(f"🎯 Intento: {result.intent.value}")
        print(f"📊 Confidenza: {result.confidence:.2f}")
        print(f"💡 Ragione: {result.reason}")
        print(f"🔑 Keywords: {result.matched_keywords}")
