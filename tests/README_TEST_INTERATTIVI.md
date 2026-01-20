# 🧪 Test Interattivi per Intent Classifier

## Quick Start

### Test Automatici (Raccomandato per prima analisi)
```bash
python test_classifier_scenarios.py
```

Esegue 23 scenari realistici e mostra:
- ✅ Cosa funziona bene (82.6% dei casi)
- ❌ Bug identificati (saluti, ordini multi-messaggio)
- 📊 Report dettagliato in console + JSON

### Test Interattivo (Per test manuali)
```bash
python test_classifier_interactive.py
```

Scegli modalità:
1. **Simulazione conversazione** - Vedi 16 messaggi passo-passo
2. **Test casi problematici** - Testa typo, messaggi brevi, ecc.
3. **Modalità interattiva** - Scrivi tu i messaggi ⭐

---

## Risultati Test Automatici

**Tasso Successo**: 82.6% (19/23)
- ✅ 15 corretti
- ⚠️ 4 parziali
- ❌ 4 errati

### Bug Principali Identificati

1. **Saluti → RICERCA** (3 errori)
   - "Ciao!", "Buongiorno", "Buonasera" classificati male
   - Fix: Escludere saluti da single_word_query

2. **Ordini Multi-Messaggio** (2 errori)
   - "Spedire a Via Roma" → FALLBACK
   - "Pago con bonifico" → FAQ
   - Serve context tracking

3. **Pattern Mancanti** (2 parziali)
   - "hai olio?" → FALLBACK
   - Fix: Aggiungere pattern "hai"

4. **Typo** (2 parziali)
   - "lsta", "liste prodotti" non riconosciuti
   - Serve fuzzy matching

---

## Documentazione Completa

- **ANALISI_TEST_REALISTICI.md** - Report dettagliato con tutte le analisi
- **GUIDA_TEST_INTERATTIVI.md** - Guida uso script e interpretazione risultati

---

## Prossimi Passi

1. Leggi `ANALISI_TEST_REALISTICI.md` per dettagli bug
2. Prova modalità interattiva per testare casi tuoi
3. Implementa fix prioritari (saluti, pattern mancanti)
4. Ri-esegui test per verificare miglioramenti

---

**Target Post-Fix**: 95%+ accuratezza (da 82.6%)
