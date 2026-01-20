""" Sistema di Classificazione Intenti - Versione Finale """
import re
import logging
import json
import os
from typing import Dict, List, Tuple
from dataclasses import dataclass
from enum import Enum
from difflib import SequenceMatcher

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

def calculate_similarity(text1: str, text2: str) -> float:
    """Calcola similarità tra due stringhe (per fuzzy matching)"""
    return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

def load_citta_italiane() -> set:
    """Carica lista città italiane dal file JSON"""
    try:
        # Cerca il file nella stessa directory del classifier
        json_path = os.path.join(os.path.dirname(__file__), 'citta_italiane.json')
        if not os.path.exists(json_path):
            # Fallback: cerca nella directory corrente
            json_path = 'citta_italiane.json'
        
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Combina capoluoghi + città maggiori
                citta = set(data.get('capoluoghi_provincia', []))
                citta.update(data.get('citta_maggiori', []))
                logger.info(f"✅ Caricate {len(citta)} città italiane dal JSON")
                return citta
        else:
            logger.warning(f"⚠️ File citta_italiane.json non trovato, uso lista base")
            # Fallback: lista minima delle città principali
            return {
                'roma', 'milano', 'napoli', 'torino', 'palermo', 'genova', 
                'bologna', 'firenze', 'bari', 'catania', 'venezia', 'verona',
                'messina', 'padova', 'trieste', 'brescia', 'taranto', 'prato'
            }
    except Exception as e:
        logger.error(f"❌ Errore caricamento città: {e}")
        # Ritorna lista base in caso di errore
        return {
            'roma', 'milano', 'napoli', 'torino', 'palermo', 'genova', 
            'bologna', 'firenze', 'bari', 'catania', 'venezia', 'verona'
        }
    
class IntentClassifier:
    """Classificatore intelligente con debug e fuzzy matching"""
    
    def __init__(self, lista_keywords: set = None, load_lista_func=None):
        self.lista_keywords = lista_keywords or set()
        self.load_lista_func = load_lista_func
        
        # Carica città italiane dal JSON
        self.citta_italiane = load_citta_italiane()
        
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
                r'^\s*prezzi\s*[.!?]?\s*$'
            ],
            'richiesta_prodotti': [
                r'\bche\s+prodotti\s+(hai|avete|vendete)',
                r'\bcosa\s+(hai|avete|vendete)',
                r'\bquali\s+prodotti',
            ]
        }
        
        # ESCLUSIONI per ordine (MIGLIORATE)
        self.ordine_exclusions = [
            r'\bcome\s+(faccio|si\s+fa|posso)\s+.*ordine\b',
            r'\bvoglio\s+(fare|effettuare)\s+(un[ao]?)?\s*ordine\s*$',  # "voglio fare un ordine" senza prodotto
            r'\bvorrei\s+(fare|effettuare)\s+(un[ao]?)?\s*ordine\s*$',  # "vorrei fare un ordine" senza prodotto
            r'\bvorrei\s+ordinar[ei]\s*$',  # "vorrei ordinare" senza prodotto
            r'\bvoglio\s+ordinar[ei]\s*$',  # "voglio ordinare" senza prodotto
            r'\bper\s+ordinar[ei]\b',
            r'\bcome\s+ordino\b',
            r'\bcome\s+si\s+ordina\b',
        ]
        
        # PRIORITÀ 3: Domande FAQ
        self.faq_indicators = {
            'parole_interrogative': [
                'come', 'quando', 'quanto', 'dove', 'perche', 'perché', 
                'cosa', 'chi', 'quale', 'quali', 'che'
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
            r'\bhai\s+(la|il|dello|della|l\'|dei|delle)?\s*\w+\b',  # MIGLIORATO: "hai olio?" funziona
            r'\bce\s+(la|il|dello|della)\s+\w+\b',
            r'\bcosto\s+(di|del|della)\s+\w+\b',
            r'\bprezzo\s+(di|del|della)\s+\w+\b',
            r'\bquanto\s+costa\s+\w+\b',
            r'\bvendete\s+\w+\b',
            r'\bavete\s+\w+\b',
            r'\bce\s*(l\')?avete\s+\w+\b',  # AGGIUNTO: "ce l'avete..."
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
        if self._is_clear_question(text_lower):
            logger.info("⚠️ Rilevata domanda chiara (interrogativa + ?), salto check ordine")
            faq_result = self._check_faq(text_norm, text_lower)
            if faq_result.confidence > 0.4:
                return faq_result
        
        if self._is_domanda_su_ordine(text_norm):
            logger.info("⚠️ Rilevata domanda su ordine, controllo FAQ")
            faq_result = self._check_faq(text_norm, text_lower)
            if faq_result.confidence > 0.5:
                return faq_result
        
        ordine_result = self._check_ordine_reale(text, text_lower)
        logger.info(f"📦 Ordine result: {ordine_result.confidence:.2f} - {ordine_result.reason}")
        logger.info(f"📦 Ordine matched: {ordine_result.matched_keywords}")
        
        if ordine_result.confidence >= 0.3:
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
        
        logger.info(f"📽 Fallback - best candidate: {best.intent.value} ({best.confidence:.2f})")
        
        if best.confidence > 0.2:
            return best
        
        return IntentResult(IntentType.FALLBACK, 0.0, "Nessun intento riconosciuto", [])
    
    def _check_richiesta_lista(self, text_norm: str, text_lower: str) -> IntentResult:
        """Controlla richiesta lista con pattern espliciti migliorati"""
        matched = []
        score = 0.0
    
        # Pattern espliciti ALTA PRIORITÀ (aggiunti nuovi)
        lista_patterns_explicit = [
            r'\blista\s+(prodott|complet|prezz|aggiorn)',
            r'(hai|invia|manda|vorrei)\s+(la\s+)?lista',
            r'\bprodotti\s+(disponibil|che\s+hai|attual)',
            r'(che|quali)\s+prodotti\s+hai',
            r'cosa\s+(hai|vendi|c\'è)\s+disponibil',
            r'^\s*hai\s+la\s+lista\s*\??$',  # "Hai la lista?"
        ]
    
        for pattern in lista_patterns_explicit:
            if re.search(pattern, text_norm, re.I):
                matched.append('pattern_esplicito_lista')
                logger.info(f"   ✓ Match lista esplicita: {pattern[:40]}")
                return IntentResult(
                    IntentType.RICHIESTA_LISTA,
                    0.95,
                    "Richiesta esplicita lista prodotti",
                    matched
                )
    
        # Pattern già esistenti
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

        # FUZZY MATCHING per typo (NUOVO)
        # Se nessun match esatto, prova con similarità
        if score == 0.0:
            PAROLE_CHIAVE_LISTA = ['lista', 'listino', 'catalogo', 'prezzi']
            for parola in parole:
                if len(parola) >= 4:  # Solo parole di almeno 4 caratteri
                    for keyword in PAROLE_CHIAVE_LISTA:
                        similarity = calculate_similarity(parola, keyword)
                        if similarity >= 0.75:  # 75% similarità
                            logger.info(f"   ✓ Fuzzy match: '{parola}' ~ '{keyword}' ({similarity:.2f})")
                            return IntentResult(
                                IntentType.RICHIESTA_LISTA,
                                0.80,  # Confidence leggermente più bassa per fuzzy
                                f"Fuzzy match: {parola} ~ {keyword}",
                                ['fuzzy_match_lista']
                            )

        return IntentResult(IntentType.RICHIESTA_LISTA, score, "Check lista", matched)
    
    def _check_ordine_reale(self, text: str, text_lower: str) -> IntentResult:
        """Controlla ordine con quantità testuali e pattern intelligenti"""
        
        logger.info(f"🔍 CHECK ORDINE - Text: '{text}'")
        
        if len(text.strip()) < 5:
            logger.info(f"  ❌ ESCLUSO: Troppo corto ({len(text.strip())} caratteri)")
            return IntentResult(IntentType.INVIO_ORDINE, 0.0, "Escluso: troppo corto", [])
        
        # Pattern "volevo fare un ordine DI prodotto"
        if re.search(r'\b(volevo|vorrei|voglio)\s+fare\s+un\s+ordine\b', text_lower):
            if re.search(r'\bordine\s+di\s+un[ao]?\s+\w{4,}', text_lower):
                logger.info(f"  ⚠️ 'volevo fare ordine di X' → Continuo check (possibile ordine)")
            elif text_lower.endswith('?') or not re.search(r'\bdi\s+\w{4,}', text_lower):
                logger.info(f"  ❌ ESCLUSO: 'volevo fare ordine' senza prodotto/con ?")
                return IntentResult(IntentType.INVIO_ORDINE, 0.0, "Escluso: domanda su ordini", [])
        
        # ESCLUSIONI FORTI (MIGLIORATE)
        strong_exclusions = [
            r'\bcome\s+(faccio|posso|si\s+fa)\s+(a\s+)?ordinar',
            r'\bcome\s+ordino\b',
            r'\bcome\s+si\s+ordina\b',
            r'\bprocedura\s+per\s+ordinar',
            r'\bper\s+ordinar.*\bcome\b',
            r'\baiuto.*\border',
            r'\bvorrei\s+(fare|effettuare)\s+(un[ao]?)?\s*ordine\s*$',  # "vorrei fare un ordine" SENZA prodotto
            r'\bvoglio\s+(fare|effettuare)\s+(un[ao]?)?\s*ordine\s*$',  # "voglio fare un ordine" SENZA prodotto
            r'\bvorrei\s+ordinar[ei]\s*$',  # "vorrei ordinare" SENZA prodotto
            r'\bvoglio\s+ordinar[ei]\s*$',  # "voglio ordinare" SENZA prodotto
        ]
        
        for pattern in strong_exclusions:
            if re.search(pattern, text_lower, re.I):
                logger.info(f"  ❌ ESCLUSO: Pattern strong exclusion matched")
                return IntentResult(IntentType.INVIO_ORDINE, 0.0, "Escluso: domanda su ordini", [])
        
        # INDICATORI DI ORDINE REALE
        order_indicators = 0
        matched = []
        
        # 1. Simboli di valuta o prezzi
        if re.search(r'[€$£¥₿]|\d+\s*(euro|eur|usd|gbp)', text_lower):
            order_indicators += 3
            matched.append('prezzo')
            logger.info(f"  ✓ Prezzo trovato (+3 punti)")
        
        # 2. Quantità chiare (NUMERICHE + TESTUALI)
        quantita_patterns = [
            r'\d+\s*x\s*\w+',
            r'\d+\s+[a-z]{3,}',
            r'\b\d+\s*pezz[io]',
            r'\b\d+\s*confezioni',
            r'\bun[ao]?\s+(confezione|scatola|pezzo|flacone|boccetta|fiala)',
            r'\bdue\s+(confezioni|scatole|pezzi|fiale)',
            r'\btre\s+(confezioni|scatole|pezzi|fiale)',
        ]
        
        quantita_trovata = False
        for pattern in quantita_patterns:
            if re.search(pattern, text_lower):
                order_indicators += 2
                matched.append('quantita')
                quantita_trovata = True
                logger.info(f"  ✓ Quantità trovata (+2 punti)")
                break
        
        # Pattern "un/una PRODOTTO" (quantità implicita = 1)
        if not quantita_trovata and re.search(r'\bun[ao]?\s+\w{5,}', text_lower):
            match = re.search(r'\bun[ao]?\s+(\w{5,})', text_lower)
            if match:
                word = match.group(1)
                common_words = ['ordine', 'momento', 'attimo', 'secondo', 'minuto', 'tantum']
                if word not in common_words:
                    order_indicators += 2
                    matched.append('quantita_testuale_uno')
                    logger.info(f"  ✓ Quantità testuale 'un/una {word}' (+2 punti)")
        
        # 3. Virgole/separatori
        if text.count(',') >= 2 or text.count(';') >= 1:
            order_indicators += 2
            matched.append('separatori_multipli')
            logger.info(f"  ✓ Separatori multipli (+2 punti)")
        elif text.count(',') == 1:
            order_indicators += 1
            matched.append('separatore_singolo')
            logger.info(f"  ✓ Separatore singolo (+1 punto)")
        
        # 3b. A capo multipli
        if text.count('\n') >= 2:
            order_indicators += 1
            matched.append('righe_multiple')
            logger.info(f"  ✓ Righe multiple (+1 punto)")
        
        # 4. Località/spedizione (VERSIONE MIGLIORATA - NO FALSI POSITIVI)
        location_patterns = [
            r'\b(via|corso|piazza|viale)\s+\w+',  # Indirizzi
            r'\b(cap|c\.a\.p\.?)\s*:?\s*\d{5}',    # CAP
            r'\b\d{5}\s+(roma|milano|napoli|torino)',  # CAP + città
            r'\b(spedizione|spedire|consegna|consegnare)\b',
        ]
        
        # Controlla pattern indirizzo
        has_location = False
        for pattern in location_patterns:
            if re.search(pattern, text_lower):
                order_indicators += 1
                matched.append('localita_pattern')
                has_location = True
                logger.info(f"  ✓ Pattern località/indirizzo (+1 punto)")
                break
        
        # Controlla città (solo parole intere dal JSON)
        if not has_location:
            for city in self.citta_italiane:
                if re.search(r'\b' + re.escape(city) + r'\b', text_lower):
                    order_indicators += 1
                    matched.append(f'citta:{city}')
                    logger.info(f"  ✓ Città rilevata: {city} (+1 punto)")
                    break
        
        # 5. Parole chiave ordine diretto
        order_keywords = [
            'ordino', 'nuovo ordine', 'prendo',
            'disponibilita', 'ne hai', 'assicurazione', 'codice sconto',
            'avrei bisogno'
        ]
        
        has_order_keyword = False
        if any(kw in text_lower for kw in order_keywords):
            has_order_keyword = True
        
        if 'ordine' in text_lower and not has_order_keyword:
            first_word = text_lower.split()[0] if text_lower.split() else ''
            question_words = ['quando', 'dove', 'come', 'perche', 'perché']
            
            if first_word not in question_words:
                has_order_keyword = True
        
        if has_order_keyword:
            order_indicators += 1
            matched.append('keyword_ordine')
            logger.info(f"  ✓ Keyword ordine (+1 punto)")

        # BLACKLIST: Parole comuni che NON sono prodotti
        PRODUCT_EXCLUSIONS = {
            'quando', 'dove', 'come', 'quanto', 'perché', 'chi', 'cosa',
            'prodotti', 'prodotto', 'disponibile', 'disponibili',
            'spedire', 'spedizione', 'pagare', 'pagamento', 'ordine'
        }

        # 6. Prodotti dalla lista (CON FUZZY MATCHING) - ESCLUDI "ordine"
        if self.load_lista_func:
            try:
                lista_text = self.load_lista_func()
                if lista_text:
                    lista_lines = [line.strip().lower() for line in lista_text.split('\n') 
                                   if line.strip() and len(line.strip()) > 3]
                    SKIP_WORDS = {'ordine', 'richiede', 'secondo', 'tantum', 'momento', 'attimo'}
                    text_words = [w for w in text_lower.split() 
                                  if len(w) > 4 and w not in SKIP_WORDS and w not in PRODUCT_EXCLUSIONS]
                    
                    product_found = False
                    for word in text_words:
                        # SKIP "ordine" - non è un prodotto!
                        if word == 'ordine':
                            logger.info(f"  ⚠️ Skipping 'ordine' (non è un prodotto)")
                            continue
                            
                        for line in lista_lines:
                            if word in line and word not in PRODUCT_EXCLUSIONS:
                                order_indicators += 2
                                matched.append(f'prodotto_lista:{word}')
                                product_found = True
                                logger.info(f"  ✓ Prodotto dalla lista (exact): '{word}' (+2 punti)")
                                break
                        
                        if not product_found and len(word) > 6:
                            for line in lista_lines:
                                for word_in_line in line.split():
                                    if len(word_in_line) > 6:
                                        similarity = calculate_similarity(word, word_in_line)
                                        if similarity > 0.85:
                                            order_indicators += 2
                                            matched.append(f'prodotto_fuzzy:{word}~{word_in_line}')
                                            product_found = True
                                            logger.info(f"  ✓ Prodotto fuzzy match: '{word}' ~ '{word_in_line}' ({similarity:.2f}) (+2 punti)")
                                            break
                                if product_found:
                                    break
                        
                        if product_found:
                            break
            except Exception as e:
                logger.warning(f"Errore caricamento lista: {e}")
        
        # 7. Metodi di pagamento (PESO AUMENTATO: 2 → 3)
        payment_keywords = ['bonifico', 'usdt', 'crypto', 'bitcoin', 'btc', 'eth', 'usdc', 'xmr']
        if any(kw in text_lower for kw in payment_keywords):
            order_indicators += 3  # AUMENTATO da 2 a 3
            matched.append('pagamento')
            logger.info(f"  ✓ Metodo pagamento (+3 punti)")

        # 7b. Pattern "pago con..." (NUOVO - per messaggi parziali)
        if re.search(r'\bpag[oa]\s+(con|in|tramite)\b', text_lower):
            order_indicators += 2
            matched.append('pago_con')
            logger.info(f"  ✓ Pattern 'pago con...' (+2 punti)")

        # 7c. Pattern "spedire/consegna a..." (NUOVO - per messaggi parziali)
        partial_order_patterns = [
            r'\bspedir[ei]\s+(a|in)\b',
            r'\bconsegn[ao]\s+(a|in)\b',
            r'\bmandare\s+(a|in)\b',
        ]
        for pattern in partial_order_patterns:
            if re.search(pattern, text_lower):
                order_indicators += 2
                matched.append('ordine_parziale')
                logger.info(f"  ✓ Messaggio parziale ordine (+2 punti)")
                break

        logger.info(f"📊 ORDINE TOTALE: {order_indicators} punti")
        logger.info(f"📊 ORDINE MATCHED: {matched}")
        
        if order_indicators >= 3:
            confidence = min(order_indicators / 10.0, 1.0)
            logger.info(f"✅ ORDINE RICONOSCIUTO (>= 3 punti)")
            return IntentResult(
                IntentType.INVIO_ORDINE,
                confidence,
                f"Ordine riconosciuto: {order_indicators} punti",
                matched
            )
        
        logger.info(f"❌ NON ORDINE (< 3 punti)")
        confidence = order_indicators / 10.0
        return IntentResult(IntentType.INVIO_ORDINE, confidence, f"Score: {order_indicators} punti", matched)
    
    def _check_faq(self, text_norm: str, text_lower: str) -> IntentResult:
        """Controlla FAQ con migliore rilevamento tempi spedizione"""
        score = 0.0
        matched = []
    
        parole = text_lower.split()
    
        # Pattern FORTI per tempi di spedizione (NUOVO)
        spedizione_patterns = [
            r'\bquando\s+(riusci|riesci|riuscivi|puoi|puo|potevi)\s+.*spedi',
            r'\btempi\s+(di\s+)?spedizione',
            r'\bquanto\s+tempo.*spedi',
            r'\bdopo\s+quanto.*spedi',
            r'\bquando\s+parti',
            r'\bquando\s+mandi',
        ]
        
        for pattern in spedizione_patterns:
            if re.search(pattern, text_lower, re.I):
                score += 0.7
                matched.append("spedizione_pattern")
                logger.info(f"   ✓ Pattern spedizione forte: {pattern[:40]}")
                break
        
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
        
        for tema, keywords in self.faq_indicators['temi_faq'].items():
            if any(kw in text_lower for kw in keywords):
                score += 0.5
                matched.append(f"tema:{tema}")
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

        # FIX BUG SALUTI: Escludere saluti comuni dal single_word_query match
        # per evitare che "ciao", "buongiorno" vengano classificati come RICERCA
        SALUTI_COMUNI = {'ciao', 'buongiorno', 'buonasera', 'salve', 'hey', 'hello', 'hi', 'salut', 'hola'}

        if len(parole) == 1 and 3 <= len(text_lower) <= 20:
            # Rimuovi punteggiatura per controllo saluti (fix "Ciao!" vs "ciao")
            text_no_punct = re.sub(r'[^\w\s]', '', text_lower)
            # Solo se non è un saluto
            if text_no_punct not in SALUTI_COMUNI:
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
    
    def _is_clear_question(self, text: str) -> bool:
        """Controlla se è una DOMANDA CHIARA che non può essere un ordine"""
        question_starters = ['quando', 'dove', 'come', 'perche', 'perché', 'quanto', 'cosa', 'chi', 'quale']
        first_word = text.split()[0] if text.split() else ''
        
        if first_word in question_starters:
            logger.info(f"  🔍 Domanda chiara: inizia con '{first_word}'")
            return True
        
        if text.strip().endswith('?'):
            logger.info(f"  🔍 Domanda chiara: finisce con ?")
            return True
        
        question_patterns = [
            r'\bquando\s+(arriva|parte|spedisci|ricevo)',
            r'\bdove\s+(è|e)\s+(il|mio|l)',
            r'\bcome\s+(faccio|posso|si\s+fa)',
            r'\bmi\s+puoi\s+dire',
            r'\bpuoi\s+dirmi',
            r'\bho\s+bisogno\s+di\s+sapere',
        ]
        
        for pattern in question_patterns:
            if re.search(pattern, text, re.I):
                logger.info(f"  🔍 Domanda chiara: pattern '{pattern[:30]}'")
                return True
        
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

        # Rimuovi punteggiatura per controllo
        text_clean = re.sub(r'[^\w\s]', '', text)
        parole = text_clean.split()

        if len(parole) <= 3 and any(s in text_clean for s in saluti):
            return True
        return False
    
    def _normalize(self, text: str) -> str:
        """Normalizza testo"""
        text = re.sub(r'[^\w\s?!.,]', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip().lower()

# End intent_classifier.py
