"""
Sistema di Classificazione Intenti - Versione Debug
Con log dettagliati per identificare problemi
"""
import re
import logging
from typing import Dict, List, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

class IntentType(Enum):
    """Tipi di intenzioni possibili"""
    RICHIESTA_LISTA = "lista"
    INVIO_ORDINE = "ordine"
    DOMANDA_FAQ = "faq"
    RICERCA_PRODOTTO = "ricerca"
    SALUTO = "saluto"
    FALLBACK = "fallback"

@dataclass
class IntentResult:
    """Risultato dell'analisi dell'intento"""
    intent: IntentType
    confidence: float
    reason: str
    matched_keywords: List[str]
    
class IntentClassifier:
    """Classificatore intelligente con debug"""
    
    def __init__(self, lista_keywords: set = None):
        self.lista_keywords = lista_keywords or set()
        
        # PRIORITÀ 1: Richieste esplicite
        self.richiesta_lista_patterns = {
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
        
        # PRIORITÀ 2: Indicatori di ordine REALE (SEMPLIFICATI)
        # NON usare lambda per debug
        pass
        
        # ESCLUSIONI per ordine
        self.ordine_exclusions = [
            r'\bcome\s+(faccio|si\s+fa|posso)\s+.*ordine\b',
            r'\bvoglio\s+(fare|effettuare)\s+.*ordine\b',
            r'\bvorrei\s+(fare|effettuare)\s+.*ordine\b',
            r'\bper\s+ordinar[ei]\b',
            r'\bcome\s+ordino\b',
            r'\bcome\s+si\s+ordina\b',
        ]
        
        # PRIORITÀ 3: Domande FAQ
        self.faq_indicators = {
            'parole_interrogative': [
                'come', 'quando', 'quanto', 'dove', 'perche', 'perche', 
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
        
        # PRIORITÀ 4: Ricerca prodotto
        self.ricerca_indicators = [
            r'\bhai\s+(la|il|dello|della)\s+\w+\b',
            r'\bce\s+(la|il|dello|della)\s+\w+\b',
            r'\bcosto\s+(di|del|della)\s+\w+\b',
            r'\bprezzo\s+(di|del|della)\s+\w+\b',
            r'\bquanto\s+costa\s+\w+\b',
            r'\bvendete\s+\w+\b',
            r'\bavete\s+\w+\b',
        ]

    def classify(self, text: str) -> IntentResult:
        """Classifica l'intento con debug completo"""
        if not text or len(text.strip()) < 2:
            return IntentResult(IntentType.FALLBACK, 0.0, "Testo vuoto", [])
        
        text_lower = text.lower()
        text_norm = self._normalize(text)
        
        logger.info(f"🔍 DEBUG classify() - Input: '{text}'")
        logger.info(f"🔍 DEBUG text_lower: '{text_lower}'")
        logger.info(f"🔍 DEBUG text_norm: '{text_norm}'")
        
        # PRIORITÀ 1: RICHIESTA LISTA
        lista_result = self._check_richiesta_lista(text_norm, text_lower)
        logger.info(f"📋 Lista result: {lista_result.confidence:.2f} - {lista_result.reason}")
        if lista_result.confidence >= 0.9:
            return lista_result
        
        # PRIORITÀ 2: ORDINE REALE
        if self._is_domanda_su_ordine(text_norm):
            logger.info("⚠️ Rilevata domanda su ordine, controllo FAQ")
            faq_result = self._check_faq(text_norm, text_lower)
            if faq_result.confidence > 0.5:
                return faq_result
        
        ordine_result = self._check_ordine_reale(text, text_lower)
        logger.info(f"📦 Ordine result: {ordine_result.confidence:.2f} - {ordine_result.reason}")
        logger.info(f"📦 Ordine matched: {ordine_result.matched_keywords}")
        
        if ordine_result.confidence >= 0.4:
            return ordine_result
        
        # PRIORITÀ 3: FAQ
        faq_result = self._check_faq(text_norm, text_lower)
        logger.info(f"❓ FAQ result: {faq_result.confidence:.2f} - {faq_result.reason}")
        if faq_result.confidence >= 0.5:
            return faq_result
        
        # PRIORITÀ 4: RICERCA
        ricerca_result = self._check_ricerca_prodotto(text_norm, text_lower)
        logger.info(f"🔎 Ricerca result: {ricerca_result.confidence:.2f} - {ricerca_result.reason}")
        
        if ricerca_result.confidence >= 0.5:
            if self._has_strong_faq_signals(text_lower):
                return faq_result if faq_result.confidence > 0.3 else IntentResult(
                    IntentType.DOMANDA_FAQ, 0.5, "FAQ signal override", ['faq_override']
                )
            return ricerca_result
        
        # PRIORITÀ 5: SALUTO
        if self._is_saluto(text_lower):
            return IntentResult(IntentType.SALUTO, 0.95, "Rilevato saluto", ['saluto'])
        
        # FALLBACK
        candidates = [lista_result, ordine_result, faq_result, ricerca_result]
        best = max(candidates, key=lambda x: x.confidence)
        
        logger.info(f"🔽 Fallback - best candidate: {best.intent.value} ({best.confidence:.2f})")
        
        if best.confidence > 0.3:
            return best
        
        return IntentResult(IntentType.FALLBACK, 0.0, "Nessun intento riconosciuto", [])
    
    def _check_richiesta_lista(self, text_norm: str, text_lower: str) -> IntentResult:
        """Controlla richiesta lista"""
        matched = []
        score = 0.0
        
        for category, patterns in self.richiesta_lista_patterns.items():
            for pattern in patterns:
                if re.search(pattern, text_norm, re.I):
                    matched.append(category)
                    score = 1.0
                    return IntentResult(
                        IntentType.RICHIESTA_LISTA,
                        score,
                        f"Richiesta esplicita lista: {category}",
                        matched
                    )
        
        parole = text_lower.split()
        if any(kw in parole for kw in ['lista', 'listino', 'catalogo', 'prezzi']):
            if len(parole) <= 5:
                return IntentResult(
                    IntentType.RICHIESTA_LISTA,
                    0.85,
                    "Keyword lista in frase breve",
                    ['lista_keyword']
                )
            score = 0.5
            matched.append('lista_keyword')
        
        return IntentResult(IntentType.RICHIESTA_LISTA, score, "Check lista", matched)
    
    def _check_ordine_reale(self, text: str, text_lower: str) -> IntentResult:
        """Controlla se è un ordine vero - CON DEBUG"""
        score = 0.0
        matched = []
        
        logger.info(f"🔍 CHECK ORDINE - Text: '{text}'")
        
        # Test 1: Ha numeri?
        has_numbers = bool(re.search(r'\d', text))
        logger.info(f"  ✓ Ha numeri: {has_numbers}")
        
        # Test 2: Ha quantità? (PATTERN MIGLIORATO)
        quantita_patterns = [
            r'\b\d+\s*x\s*\w+',          # "2x prodotto"
            r'\b\d+\s+\w+',               # "1 testo"
            r'\w+\s*x\s*\d+',             # "prodotto x2"
        ]
        ha_quantita = False
        for pattern in quantita_patterns:
            match = re.search(pattern, text, re.I)
            if match:
                ha_quantita = True
                logger.info(f"  ✓ Quantità trovata: '{match.group()}' (pattern: {pattern})")
                score += 1.0
                matched.append('ha_quantita')
                break
        
        if not ha_quantita:
            logger.info(f"  ✗ Nessuna quantità trovata")
        
        # Test 3: Ha prezzo?
        prezzo_pattern = r'[€$£¥₿]|\d+\s*(euro|eur|usd|dollar)'
        prezzo_match = re.search(prezzo_pattern, text, re.I)
        if prezzo_match:
            score += 1.0
            matched.append('ha_prezzo')
            logger.info(f"  ✓ Prezzo trovato: '{prezzo_match.group()}'")
        else:
            logger.info(f"  ✗ Nessun prezzo trovato")
        
        # Test 4: Ha pagamento?
        payment_keywords = ['bonifico', 'usdt', 'crypto', 'bitcoin', 'btc', 'eth', 'paghero', 'pago con']
        ha_pagamento = any(kw in text_lower for kw in payment_keywords)
        if ha_pagamento:
            score += 1.0
            matched.append('ha_pagamento')
            logger.info(f"  ✓ Pagamento trovato")
        else:
            logger.info(f"  ✗ Nessun pagamento trovato")
        
        # Test 5: Ha indirizzo?
        indirizzo_match = re.search(r'\b(via|piazza|corso|viale)\s+\w+', text, re.I)
        if indirizzo_match:
            score += 1.0
            matched.append('ha_indirizzo')
            logger.info(f"  ✓ Indirizzo trovato: '{indirizzo_match.group()}'")
        else:
            logger.info(f"  ✗ Nessun indirizzo trovato")
        
        # Test 6: Formato ordine (righe multiple, virgole)
        has_separators = text.count(',') >= 2 or text.count(';') >= 1
        has_lines = text.count('\n') >= 2
        if has_numbers and (has_separators or has_lines):
            score += 1.0
            matched.append('formato_ordine')
            logger.info(f"  ✓ Formato ordine rilevato")
        
        max_score = 5
        confidence = score / max_score
        
        logger.info(f"📊 ORDINE SCORE: {score}/{max_score} = {confidence:.2f}")
        logger.info(f"📊 ORDINE MATCHED: {matched}")
        
        if score >= 2:
            return IntentResult(
                IntentType.INVIO_ORDINE,
                confidence,
                f"Ordine probabile: {score}/{max_score} indicatori",
                matched
            )
        
        return IntentResult(IntentType.INVIO_ORDINE, confidence, "Check ordine", matched)
    
    def _check_faq(self, text_norm: str, text_lower: str) -> IntentResult:
        """Controlla FAQ"""
        score = 0.0
        matched = []
        
        parole = text_lower.split()
        
        for interrogativa in self.faq_indicators['parole_interrogative']:
            if interrogativa in parole:
                score += 0.3
                matched.append(f"interrogativa:{interrogativa}")
                break
        
        for frase in self.faq_indicators['richieste_info']:
            if frase in text_lower:
                score += 0.3
                matched.append(f"richiesta_info:{frase}")
                break
        
        tema_trovato = False
        for tema, keywords in self.faq_indicators['temi_faq'].items():
            if any(kw in text_lower for kw in keywords):
                score += 0.5
                matched.append(f"tema:{tema}")
                tema_trovato = True
                break
        
        if '?' in text_norm:
            score += 0.2
            matched.append("punto_interrogativo")
        
        faq_strong_patterns = [
            r'\b(inviato|spedito|mandato|ricevuto)\b.*\b(ordine|pacco|prodotto)\b',
            r'\b(ordine|pacco|prodotto)\b.*\b(inviato|spedito|mandato|ricevuto)\b',
            r'\bgia\s+(inviato|spedito|mandato)\b',
            r'\bquando\s+(arriva|parte|spedisci)\b',
            r'\bdove\s+(e|è)\s+(il|mio|l)\b.*\b(ordine|pacco)\b',
        ]
        
        for pattern in faq_strong_patterns:
            if re.search(pattern, text_lower, re.I):
                score += 0.6
                matched.append("faq_strong_pattern")
                break
        
        confidence = min(score, 1.0)
        
        return IntentResult(IntentType.DOMANDA_FAQ, confidence, f"FAQ score: {confidence:.2f}", matched)
    
    def _check_ricerca_prodotto(self, text_norm: str, text_lower: str) -> IntentResult:
        """Controlla ricerca prodotto"""
        score = 0.0
        matched = []
        
        for pattern in self.ricerca_indicators:
            if re.search(pattern, text_norm, re.I):
                score += 0.4
                matched.append(f"pattern")
        
        parole = text_lower.split()
        prodotti_trovati = [p for p in parole if p in self.lista_keywords and len(p) > 3]
        if prodotti_trovati:
            score += 0.3 * min(len(prodotti_trovati), 2)
            matched.extend([f"prodotto:{p}" for p in prodotti_trovati[:3]])
        
        if len(parole) == 1 and 3 <= len(text_lower) <= 20:
            score += 0.5
            matched.append("single_word_query")
        
        confidence = min(score, 1.0)
        
        return IntentResult(IntentType.RICERCA_PRODOTTO, confidence, f"Ricerca prodotto score: {confidence:.2f}", matched)
    
    def _has_strong_faq_signals(self, text: str) -> bool:
        """Controlla segnali FAQ forti"""
        faq_blockers = [
            'inviato', 'spedito', 'mandato', 'ricevuto', 'arriva', 
            'quando', 'dove', 'gia', 'ancora', 'stato', 'ordine mio'
        ]
        
        if 'hai' in text:
            return any(blocker in text for blocker in faq_blockers)
        
        return False
    
    def _is_domanda_su_ordine(self, text: str) -> bool:
        """Controlla se è domanda su ordini"""
        for pattern in self.ordine_exclusions:
            if re.search(pattern, text, re.I):
                return True
        return False
    
    def _is_saluto(self, text: str) -> bool:
        """Controlla se è saluto"""
        saluti = ['ciao', 'buongiorno', 'buonasera', 'salve', 'hey', 'hello', 'hi']
        parole = text.split()
        
        if len(parole) <= 3 and any(s in text for s in saluti):
            return True
        return False
    
    def _normalize(self, text: str) -> str:
        """Normalizza testo"""
        text = re.sub(r'[^\w\s?!.,]', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip().lower()
