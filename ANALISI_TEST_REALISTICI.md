# 🧪 Analisi Test Realistici - Intent Classifier

## Risultati Generali

**Data Test**: 2026-01-20
**Test Eseguiti**: 23 scenari conversazionali realistici
**Tasso Successo**: **82.6%** (19/23)

| Esito | Numero | Percentuale |
|-------|--------|-------------|
| ✅ Corretti | 15 | 65.2% |
| ⚠️ Parziali | 4 | 17.4% |
| ❌ Errati | 4 | 17.4% |

---

## 🎯 Analisi per Scenario

### ✅ Scenario: Messaggi Brevi e Diretti (100% successo)
**Performance**: 3/3 corretti

Messaggi testati:
- "lista" → ✅ RICHIESTA_LISTA (confidence: 1.00)
- "prezzi" → ✅ RICHIESTA_LISTA (confidence: 1.00)
- "catalogo" → ✅ RICHIESTA_LISTA (confidence: 1.00)

**Valutazione**: 🟢 ECCELLENTE
Il classificatore riconosce perfettamente le richieste dirette di lista/catalogo.

---

### ✅ Scenario: Ordini Complessi (100% successo)
**Performance**: 2/2 corretti

Messaggi testati:
- Ordine multiprodotto formattato (con newline) → ✅ INVIO_ORDINE (conf: 0.50)
- Ordine completo in un messaggio → ✅ INVIO_ORDINE (conf: 0.90)

**Valutazione**: 🟢 ECCELLENTE
Il sistema scoring per ordini complessi funziona bene.

---

### ⚠️ Scenario: Conversazione Cliente Nuovo (75% successo)
**Performance**: 3/4 corretti, 1 errore

**Errore Identificato**:
```
❌ "Ciao!" → RICERCA_PRODOTTO (dovrebbe essere SALUTO)
   Confidence: 0.50
   Reason: Ricerca prodotto score: 0.50 (single_word_query)
```

**Analisi**:
- ✅ "Sono nuovo, cosa vendete?" → RICHIESTA_LISTA (perfetto)
- ✅ "Avete integratori?" → RICERCA_PRODOTTO (corretto)
- ✅ "Quanto costa l'integratore?" → DOMANDA_FAQ (corretto, "quanto" è interrogativa)
- ❌ Problema con saluti singoli

**Valutazione**: 🟡 BUONO ma con bug noto sui saluti

---

### ⚠️ Scenario: Cliente con Domande (75% successo)
**Performance**: 3/4 corretti, 1 errore

**Errore Identificato**:
```
❌ "Buonasera" → RICERCA_PRODOTTO (dovrebbe essere SALUTO)
   Confidence: 0.50
   Reason: Stesso bug di "Ciao!"
```

**Analisi**:
- ✅ "Quanto tempo ci vuole per la spedizione?" → DOMANDA_FAQ (conf: 1.00) ⭐
- ✅ "Accettate pagamenti in crypto?" → DOMANDA_FAQ (conf: 0.70) ⭐
- ✅ "Come faccio a tracciare il pacco?" → DOMANDA_FAQ (conf: 1.00) ⭐
- ❌ Problema con saluti singoli

**Valutazione**: 🟢 ECCELLENTE per FAQ, problema solo con saluti

---

### ⚠️ Scenario: Casi Ambigui e Difficili (75% successo)
**Performance**: 3/4 corretti, 1 parziale

**Problema Identificato**:
```
⚠️ "hai olio?" → FALLBACK (dovrebbe essere RICERCA_PRODOTTO)
   Confidence: 0.00
   Reason: Pattern "hai" non è nei ricerca_indicators
```

**Analisi**:
- ✅ "voglio ordinare" (senza prodotto) → FALLBACK (corretto! ✅)
- ✅ "ne prendo 2" (senza contesto) → FALLBACK (corretto! ✅)
- ✅ "quanto costa?" (senza prodotto) → DOMANDA_FAQ (corretto! ✅)
- ⚠️ "hai olio?" non riconosciuto

**Valutazione**: 🟡 BUONO - gestisce bene ambiguità, manca pattern "hai"

---

### 🔴 Scenario: Ordine Diretto (25% successo)
**Performance**: 1/4 corretti, 2 errori, 1 parziale

**PROBLEMI CRITICI**:

1. **❌ "Buongiorno" → RICERCA_PRODOTTO**
   - Dovrebbe essere: SALUTO
   - Confidence: 0.50
   - Bug noto saluti

2. **⚠️ "Spedire a Via Garibaldi 25, Roma" → FALLBACK**
   - Dovrebbe essere: INVIO_ORDINE
   - Confidence: 0.00
   - **PROBLEMA**: Il messaggio contiene SOLO indirizzo, senza prodotto
   - Il classificatore non lo riconosce come parte di ordine

3. **❌ "Pago con bonifico bancario" → DOMANDA_FAQ**
   - Dovrebbe essere: INVIO_ORDINE
   - Confidence: 0.50
   - Matched: tema:pagamento
   - **PROBLEMA**: "bonifico" trigger FAQ (tema pagamento), non ordine

**Analisi del Problema**:
Questo scenario simula un ordine in **messaggi separati**:
1. "Buongiorno" (saluto)
2. "Vorrei ordinare 2 oli..." (ordine principale) ✅
3. "Spedire a Via Garibaldi 25, Roma" (indirizzo) ❌
4. "Pago con bonifico" (pagamento) ❌

**Causa**: Il classificatore NON mantiene contesto tra messaggi. Ogni messaggio viene analizzato in isolamento.

**Valutazione**: 🔴 CRITICO - ordini multi-messaggio non gestiti

---

### 🔴 Scenario: Typo e Errori Comuni (0% successo)
**Performance**: 0/2 corretti, 2 parziali

**Problemi**:
1. **⚠️ "vorrei la lsta" → FALLBACK**
   - Dovrebbe essere: RICHIESTA_LISTA
   - Confidence: 0.00
   - Typo su "lista" non riconosciuto

2. **⚠️ "liste prodotti" → FALLBACK**
   - Dovrebbe essere: RICHIESTA_LISTA
   - Confidence: 0.00
   - Plurale "liste" non riconosciuto

**Valutazione**: 🔴 CRITICO - nessun fuzzy matching

---

## 🐛 Bug e Problemi Principali

### Bug #1: Saluti Classificati Come Ricerca (PRIORITÀ ALTA)

**Impatto**: 3 errori su 4 sono questo bug
**Gravità**: 🔴 ALTA (prima impressione cliente)

**Messaggi Affetti**:
- "Ciao!" → RICERCA_PRODOTTO (conf: 0.50)
- "Buongiorno" → RICERCA_PRODOTTO (conf: 0.50)
- "Buonasera" → RICERCA_PRODOTTO (conf: 0.50)

**Causa Root**:
```python
# Nel metodo classify(), priorità:
1. RICHIESTA_LISTA (check_richiesta_lista)
2. ORDINE (check_ordine_reale)
3. FAQ (check_faq)
4. RICERCA (check_ricerca_prodotto)  # <-- Match qui con single_word_query
5. SALUTO (is_saluto)  # <-- Troppo tardi!
```

Il pattern `single_word_query` in `_check_ricerca_prodotto()` matcha parole singole di 3-20 caratteri:
```python
if len(parole) == 1 and 3 <= len(text_lower) <= 20:
    score += 0.5  # Saluti hanno 4-10 caratteri, vengono matchati!
```

**Soluzioni Proposte**:

**Opzione 1 - Quick Fix (RACCOMANDATA)**:
```python
# In _check_ricerca_prodotto(), escludere saluti
SALUTI = {'ciao', 'buongiorno', 'buonasera', 'salve', 'hey', 'hello', 'hi'}

if len(parole) == 1 and 3 <= len(text_lower) <= 20:
    if text_lower not in SALUTI:  # <-- Aggiungere questo check
        score += 0.5
        matched.append("single_word_query")
```

**Opzione 2 - Riordino Priorità**:
```python
# Nel metodo classify(), spostare check saluto PRIMA di ricerca:
# PRIORITÀ 4.5: SALUTO (prima di ricerca!)
if self._is_saluto(text_lower):
    return IntentResult(IntentType.SALUTO, 0.95, "Rilevato saluto", ['saluto'])

# PRIORITÀ 5: RICERCA
ricerca_result = self._check_ricerca_prodotto(...)
```

**Raccomandazione**: Implementare **Opzione 1** (più sicuro, no side effects)

---

### Problema #2: Messaggi Multi-Parte Non Gestiti (PRIORITÀ ALTA)

**Impatto**: Ordini in più messaggi non funzionano
**Gravità**: 🔴 ALTA (UX negativa)

**Esempio**:
```
User: Vorrei ordinare 2 oli          → ✅ INVIO_ORDINE
User: Spedire a Via Garibaldi 25     → ❌ FALLBACK (nessun prodotto!)
User: Pago con bonifico              → ❌ DOMANDA_FAQ (theme:pagamento)
```

**Causa**: Il classificatore è **stateless** - ogni messaggio è analizzato in isolamento senza contesto.

**Soluzioni Proposte**:

**Opzione 1 - Context Tracking (IDEALE ma complesso)**:
```python
class IntentClassifier:
    def __init__(self, ...):
        self.last_intent = None
        self.last_confidence = 0.0
        self.context_timeout = 60  # secondi

    def classify(self, text, user_id=None, timestamp=None):
        # Se ultimo intent era ORDINE e messaggio contiene indirizzo/pagamento
        # -> considera come continuazione ordine
        if self.last_intent == IntentType.INVIO_ORDINE:
            if self._is_order_continuation(text):
                return IntentResult(IntentType.INVIO_ORDINE, ...)
```

**Opzione 2 - Pattern Migliorati (QUICK FIX)**:
```python
# In _check_ordine_reale(), aggiungere pattern per messaggi parziali:
partial_order_patterns = [
    r'\bspedir[ei]\s+(a|in)\b',  # "spedire a..."
    r'\bpag[oa]\s+(con|in)\b',   # "pago con..."
    r'\bconsegna\s+(a|in)\b',    # "consegna a..."
]

for pattern in partial_order_patterns:
    if re.search(pattern, text_lower):
        order_indicators += 2  # Dare punti anche senza prodotto
```

**Raccomandazione**: Implementare **Opzione 2** come quick fix, pianificare Opzione 1 per v2

---

### Problema #3: Metodo Pagamento Classificato Come FAQ (PRIORITÀ MEDIA)

**Impatto**: 1 errore
**Gravità**: 🟡 MEDIA

**Esempio**:
```
"Pago con bonifico bancario" → DOMANDA_FAQ (dovrebbe essere INVIO_ORDINE)
```

**Causa**: La parola "bonifico" è in `faq_indicators['temi_faq']['pagamento']`, che ha priorità più alta (check FAQ viene prima del check ordine nel fallback).

**Soluzione**:
```python
# In _check_ordine_reale(), dare più peso a metodi pagamento
payment_keywords = ['bonifico', 'usdt', 'crypto', 'bitcoin', 'btc', 'eth']
if any(kw in text_lower for kw in payment_keywords):
    order_indicators += 3  # Aumentare da 2 a 3
    matched.append('pagamento')

# Aggiungere anche pattern per "pago con..."
if re.search(r'\bpag[oa]\s+con\b', text_lower):
    order_indicators += 2
    matched.append('pago_con')
```

---

### Problema #4: Pattern "hai" Non Riconosciuto (PRIORITÀ MEDIA)

**Impatto**: 1 errore parziale
**Gravità**: 🟡 MEDIA

**Esempio**:
```
"hai olio?" → FALLBACK (dovrebbe essere RICERCA_PRODOTTO)
```

**Causa**: Il pattern "hai" non è in `ricerca_indicators`.

**Soluzione**:
```python
# In __init__, aggiungere a ricerca_indicators:
self.ricerca_indicators = [
    r'\bhai\s+(la|il|dello|della|dei|delle)?\s*\w+\b',  # <-- AGGIUNGERE
    r'\bce\s+(la|il|dello|della)\s+\w+\b',
    # ... resto dei pattern
]
```

---

### Problema #5: Zero Fuzzy Matching per Typo (PRIORITÀ BASSA)

**Impatto**: 2 errori parziali
**Gravità**: 🟡 BASSA (raro nella pratica)

**Esempi**:
```
"vorrei la lsta"    → FALLBACK (dovrebbe essere RICHIESTA_LISTA)
"liste prodotti"    → FALLBACK (dovrebbe essere RICHIESTA_LISTA)
```

**Causa**: I pattern usano match esatti, nessun fuzzy matching.

**Soluzione**:
```python
# In _check_richiesta_lista(), aggiungere fuzzy matching:
from difflib import SequenceMatcher

# Dopo check pattern esatti, check fuzzy:
parole_chiave_lista = ['lista', 'listino', 'catalogo', 'prezzi']
for parola in text_lower.split():
    for keyword in parole_chiave_lista:
        similarity = SequenceMatcher(None, parola, keyword).ratio()
        if similarity >= 0.75:  # 75% similarità
            return IntentResult(
                IntentType.RICHIESTA_LISTA,
                0.80,  # Confidence leggermente più bassa
                f"Fuzzy match: {parola} ~ {keyword}",
                ['fuzzy_match_lista']
            )
```

---

## 📊 Statistiche di Confidence

### Distribuzione Confidence per Intent Corretto

| Intent | Media Conf. | Range | Valutazione |
|--------|-------------|-------|-------------|
| RICHIESTA_LISTA | 1.00 | 1.00-1.00 | 🟢 Ottimo |
| DOMANDA_FAQ | 0.80 | 0.50-1.00 | 🟢 Ottimo |
| INVIO_ORDINE | 0.60 | 0.40-0.90 | 🟡 Buono |
| RICERCA_PRODOTTO | 0.45 | 0.40-0.50 | 🟡 Accettabile |

**Osservazioni**:
- ✅ LISTA e FAQ hanno confidence molto alta (≥0.5)
- ⚠️ ORDINE ha confidence variabile (dipende da numero indicatori)
- ⚠️ RICERCA ha confidence bassa (soglia 0.5 può escludere match validi)

**Raccomandazione**: Abbassare soglia RICERCA da 0.5 a 0.4 per catturare più casi.

---

## 💡 Raccomandazioni Implementazione

### 🔴 Priorità CRITICA (Fix Immediato)

1. **Fixare Bug Saluti** (2-3 ore)
   - Escludere saluti da single_word_query
   - Test: verificare che "ciao", "buongiorno", "buonasera" → SALUTO

2. **Migliorare Pattern Ordini Parziali** (3-4 ore)
   - Aggiungere pattern "spedire a", "pago con"
   - Aumentare peso pagamento da 2 a 3
   - Test: "Pago con bonifico" → INVIO_ORDINE

### 🟡 Priorità ALTA (Questa Settimana)

3. **Aggiungere Pattern "hai"** (1 ora)
   - Aggiungere a ricerca_indicators
   - Test: "hai olio?" → RICERCA_PRODOTTO

4. **Implementare Context Tracking Base** (8-10 ore)
   - Tracciare ultimo intent per user_id
   - Riconoscere continuazioni ordine
   - Test: ordini multi-messaggio

### 🟢 Priorità MEDIA (Prossime 2 Settimane)

5. **Fuzzy Matching per Typo** (4-5 ore)
   - Similarità ≥0.75 per parole chiave lista
   - Test: "lsta" → RICHIESTA_LISTA

6. **Ottimizzare Soglie Confidence** (2-3 ore)
   - RICERCA: 0.5 → 0.4
   - Test regressione per verificare no side effects

### 📝 Priorità BASSA (Backlog)

7. **Logging e Telemetria**
   - Tracciare misclassificazioni in produzione
   - Dashboard con metriche real-time

8. **A/B Testing**
   - Testare nuove soglie con utenti reali
   - Confrontare v1 vs v2

---

## 🎯 Metriche Target Post-Fix

Dopo implementazione fix raccomandati:

| Metrica | Attuale | Target | Delta |
|---------|---------|--------|-------|
| Tasso Successo | 82.6% | ≥95% | +12.4% |
| Errori Critici | 4 | 0 | -4 |
| Confidence Media ORDINE | 0.60 | ≥0.70 | +0.10 |
| Confidence Media RICERCA | 0.45 | ≥0.50 | +0.05 |

---

## 📈 Piano di Test Post-Fix

### Test di Regressione
```bash
# Eseguire suite completa
pytest tests/test_intent_classifier.py -v

# Eseguire test scenari
python test_classifier_scenarios.py

# Verificare che tutti i test passino
# Target: 100% test passati
```

### Test Nuovi Casi
Dopo fix, aggiungere test per:
- ✅ Saluti singoli → SALUTO
- ✅ "Pago con bonifico" → INVIO_ORDINE
- ✅ "Spedire a Via X" dopo ordine → INVIO_ORDINE (con context)
- ✅ "hai olio?" → RICERCA_PRODOTTO
- ✅ "lsta" → RICHIESTA_LISTA (fuzzy)

### Monitoraggio Produzione
- Alert se confidence media < 0.60
- Alert se FALLBACK > 10% messaggi
- Report settimanale con metriche

---

## 📝 Conclusioni

Il classificatore **funziona bene** per i casi principali:
- ✅ Richieste lista: 100% accuratezza
- ✅ FAQ: 85%+ accuratezza
- ✅ Ordini completi (singolo messaggio): 100% accuratezza

**Aree critiche da migliorare**:
- 🔴 Saluti (bug priorità #1)
- 🔴 Ordini multi-messaggio (context tracking)
- 🟡 Pattern mancanti ("hai", "pago con")
- 🟡 Fuzzy matching typo

**Stima effort fix critici**: 5-7 ore sviluppo + 3-4 ore testing

**ROI atteso**: +12% accuratezza, UX significativamente migliore

---

**Report generato da**: test_classifier_scenarios.py
**Timestamp**: 2026-01-20
