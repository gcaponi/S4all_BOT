# 🎮 Guida Test Interattivi Intent Classifier

## Come Testare il Classificatore Come un Utente Reale

Ho creato due strumenti per testare l'Intent Classifier in modo realistico:

### 1. 🤖 Test Automatici con Scenari (`test_classifier_scenarios.py`)

Esegue automaticamente 23 scenari conversazionali realistici e genera un report.

#### Come Usare

```bash
# Eseguire i test
python test_classifier_scenarios.py

# Output:
# - Report dettagliato in console
# - File JSON: test_scenarios_report.json
```

#### Cosa Testa

- ✅ Conversazioni complete cliente
- ✅ Ordini diretti e complessi
- ✅ Domande FAQ
- ✅ Messaggi brevi
- ✅ Casi ambigui
- ✅ Typo ed errori

#### Risultati Principali

Il report mostra:
- **Tasso di successo**: 82.6% (19/23 test)
- **Problemi identificati**: 8 problemi (4 critici, 4 parziali)
- **Bug principali**: Saluti, ordini multi-messaggio, typo

---

### 2. 💬 Test Interattivo Manuale (`test_classifier_interactive.py`)

Ti permette di scrivere messaggi come se fossi un cliente e vedere come il bot li interpreta.

#### Come Usare

```bash
# Avviare lo script
python test_classifier_interactive.py

# Apparirà un menu:
# 1. Simulazione conversazione realistica
# 2. Test casi problematici
# 3. Modalità interattiva (scrivi tu)
# 4. Esci
```

#### Modalità Disponibili

**Modalità 1: Simulazione Conversazione**
- Simula 3 conversazioni complete
- Mostra passo-passo come il bot risponde
- Premi INVIO per vedere il messaggio successivo

**Modalità 2: Test Casi Problematici**
- Testa typo, messaggi brevi, ordini ambigui
- Diviso per categoria
- Identifica i punti deboli

**Modalità 3: Modalità Interattiva** ⭐ RACCOMANDATO
- Scrivi tu stesso i messaggi
- Il bot ti dice come li interpreta
- Perfetto per testare casi reali

---

## 🎯 Esempi Pratici

### Esempio: Modalità Interattiva

```bash
$ python test_classifier_interactive.py

# Scegli opzione 3

💬 MODALITÀ INTERATTIVA
Scrivi messaggi come se fossi un cliente.
Scrivi 'exit' per uscire.

👤 Cliente: Ciao!

======================================================================
📩 MESSAGGIO: Ciao!
======================================================================

🔎 INTENT: RICERCA          # ❌ Bug! Dovrebbe essere SALUTO
📊 CONFIDENCE: 0.50 (50%)
💭 REASON: Ricerca prodotto score: 0.50
🔑 KEYWORDS: single_word_query

📝 COSA FAREBBE IL BOT:
   → Cercherebbe il prodotto nella lista

⚠️  ATTENZIONE: Confidence bassa - il bot potrebbe non essere sicuro
======================================================================

👤 Cliente: vorrei la lista

======================================================================
📩 MESSAGGIO: vorrei la lista
======================================================================

📋 INTENT: LISTA            # ✅ Corretto!
📊 CONFIDENCE: 1.00 (100%)
💭 REASON: Richiesta esplicita lista: voglio_lista
🔑 KEYWORDS: voglio_lista

📝 COSA FAREBBE IL BOT:
   → Invierebbe la lista completa dei prodotti
======================================================================

👤 Cliente: Vorrei ordinare 2 oli extra vergine, spedire a Roma, pago con bonifico

======================================================================
📩 MESSAGGIO: Vorrei ordinare 2 oli extra vergine, spedire a Roma, pago con bonifico
======================================================================

📦 INTENT: ORDINE           # ✅ Perfetto!
📊 CONFIDENCE: 0.90 (90%)
💭 REASON: Ordine riconosciuto: 9 punti
🔑 KEYWORDS: quantita, separatori_multipli, prodotto_lista:extra, citta:roma, pagamento

📝 COSA FAREBBE IL BOT:
   → Processerebbe l'ordine e richiederebbe conferma
======================================================================

👤 Cliente: exit

👋 Arrivederci!
```

---

## 🔍 Cosa Guardare Quando Testi

### ✅ Comportamenti Corretti da Verificare

1. **Richieste Lista**
   - "lista", "catalogo", "prezzi" → RICHIESTA_LISTA
   - "cosa vendete?", "che prodotti hai?" → RICHIESTA_LISTA
   - Confidence dovrebbe essere ≥0.90

2. **Ordini Completi**
   - "Vorrei ordinare 2 oli, spedire a Roma, pago con bonifico" → INVIO_ORDINE
   - Confidence dovrebbe aumentare con più indicatori (quantità, indirizzo, pagamento)
   - Prodotti dalla lista dovrebbero essere riconosciuti

3. **Domande FAQ**
   - "Quando arriva?" → DOMANDA_FAQ
   - "Quanto costa la spedizione?" → DOMANDA_FAQ
   - "Come faccio a tracciare?" → DOMANDA_FAQ
   - Confidence ≥0.50

4. **Ricerca Prodotti**
   - "Avete integratori?" → RICERCA_PRODOTTO
   - "Vendete olio?" → RICERCA_PRODOTTO
   - Confidence ≥0.40

### ❌ Bug Noti da Verificare

1. **Saluti Singoli** 🐛
   ```
   "Ciao!" → RICERCA_PRODOTTO ❌ (dovrebbe essere SALUTO)
   "Buongiorno" → RICERCA_PRODOTTO ❌
   "Buonasera" → RICERCA_PRODOTTO ❌
   ```
   **Status**: Bug confermato, fix pianificato

2. **Ordini Multi-Messaggio** 🐛
   ```
   Messaggio 1: "Vorrei ordinare 2 oli" → INVIO_ORDINE ✅
   Messaggio 2: "Spedire a Via Roma 10" → FALLBACK ❌ (dovrebbe essere ORDINE)
   Messaggio 3: "Pago con bonifico" → DOMANDA_FAQ ❌ (dovrebbe essere ORDINE)
   ```
   **Status**: Problema architetturale, serve context tracking

3. **Pattern "hai"** 🐛
   ```
   "hai olio?" → FALLBACK ❌ (dovrebbe essere RICERCA_PRODOTTO)
   ```
   **Status**: Pattern mancante, fix facile

4. **Typo** 🐛
   ```
   "vorrei la lsta" → FALLBACK ❌ (dovrebbe essere RICHIESTA_LISTA)
   "liste prodotti" → FALLBACK ❌
   ```
   **Status**: Nessun fuzzy matching, priorità bassa

---

## 📊 Come Interpretare i Risultati

### Confidence Levels

| Range | Significato | Azione Bot |
|-------|-------------|------------|
| 0.90-1.00 | 🟢 Molto sicuro | Procede direttamente |
| 0.50-0.89 | 🟡 Abbastanza sicuro | Procede ma monitora |
| 0.30-0.49 | 🟠 Incerto | Potrebbe chiedere conferma |
| 0.00-0.29 | 🔴 Molto incerto | Chiede chiarimenti |

### Intent Types

| Intent | Emoji | Cosa Fa il Bot |
|--------|-------|----------------|
| RICHIESTA_LISTA | 📋 | Invia lista completa prodotti |
| INVIO_ORDINE | 📦 | Processa ordine, chiede conferma |
| DOMANDA_FAQ | ❓ | Cerca risposta in FAQ |
| RICERCA_PRODOTTO | 🔎 | Cerca prodotto specifico |
| SALUTO | 👋 | Risponde con saluto |
| FALLBACK | 🤷 | Chiede chiarimenti |

---

## 🧪 Test Suggeriti da Provare

### Test Base (Dovrebbero Funzionare)

```
✅ "lista"
✅ "voglio la lista"
✅ "quanto costa la spedizione?"
✅ "vendete olio?"
✅ "Vorrei ordinare 2 oli extra vergine, spedire a Milano, pago con crypto"
```

### Test Bug Noti (Non Funzionano Ancora)

```
❌ "ciao"                    # Bug saluti
❌ "buongiorno"              # Bug saluti
❌ "pago con bonifico"       # Classificato come FAQ
❌ "hai olio?"               # Pattern mancante
❌ "vorrei la lsta"          # Typo non gestito
```

### Test Edge Cases (Interessanti)

```
🤔 "voglio ordinare"         # Senza prodotto → FALLBACK ✅
🤔 "ne prendo 2"             # Senza contesto → FALLBACK ✅
🤔 "quanto costa?"           # Senza prodotto → FAQ ✅
🤔 "lista!!!!"               # Punteggiatura → RICERCA ⚠️
```

---

## 💡 Tips per Testing Efficace

### 1. Simula Conversazioni Reali
Non testare solo singoli messaggi, simula conversazioni complete:
```
Cliente: Ciao!
Cliente: Vorrei vedere i prodotti
Cliente: Avete olio extra vergine?
Cliente: Quanto costa?
Cliente: Ok, ne prendo 2
Cliente: Spedite a Roma?
```

### 2. Prova Varianti
Testa diverse formulazioni della stessa richiesta:
```
"lista"
"voglio la lista"
"mandami la lista"
"hai la lista?"
"mi mostri i prodotti?"
"cosa vendete?"
```

### 3. Testa Typo Comuni
Gli utenti fanno errori:
```
"lsta"
"liste"
"cattalogo"
"orrdine"
```

### 4. Verifica Confidence
Non guardare solo l'intent, ma anche la confidence:
- Confidence bassa = bot incerto = esperienza utente scarsa
- Target: ≥0.70 per intent principali

### 5. Testa Messaggi Lunghi
Ordini reali sono spesso complessi:
```
"Buongiorno, vorrei ordinare 3 bottiglie di olio extra vergine bio,
2 confezioni di miele biologico e 1 siero anti-age.
Spedire a Via Giuseppe Verdi 42, 20121 Milano.
Pagamento con bonifico bancario.
Grazie!"
```

---

## 🚀 Prossimi Passi

Dopo aver testato:

1. **Annota i Problemi**
   - Quali messaggi non funzionano?
   - Quali hanno confidence troppo bassa?
   - Ci sono pattern ricorrenti?

2. **Leggi l'Analisi**
   - Vedi `ANALISI_TEST_REALISTICI.md`
   - Bug identificati con soluzioni

3. **Proponi Miglioramenti**
   - Quali fix sono prioritari per te?
   - Ci sono casi d'uso specifici del tuo bot?

4. **Implementa Fix**
   - Inizia con bug saluti (più facile)
   - Poi pattern mancanti
   - Infine context tracking

---

## 📞 Supporto

Se trovi comportamenti strani o hai domande:

1. Esegui test automatici: `python test_classifier_scenarios.py`
2. Controlla `ANALISI_TEST_REALISTICI.md`
3. Prova modalità interattiva per debugging
4. Condividi risultati per analisi

---

**Buon Testing! 🧪**
