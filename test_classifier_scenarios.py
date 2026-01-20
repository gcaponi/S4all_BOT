#!/usr/bin/env python3
"""
Test Automatici con Scenari Realistici

Esegue test automatici simulando utenti reali e genera un report
con analisi dei risultati e problemi identificati.
"""

import json
from collections import defaultdict
from intent_classifier import IntentClassifier, IntentType


def load_lista_prodotti():
    """Carica lista prodotti"""
    try:
        with open('lista_prodotti.txt', 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return """
🍾 Olio Extra Vergine Bio - €15.00
🧴 Crema Viso Idratante - €25.00
💊 Integratore Multivitaminico - €18.50
🫙 Miele Biologico 500g - €12.00
🧪 Siero Anti-Age - €35.00
🌿 Tisana Rilassante - €8.50
"""


# SCENARI DI TEST REALISTICI
SCENARI_TEST = {
    "Conversazione Cliente Nuovo": [
        {
            "msg": "Ciao!",
            "expected": IntentType.SALUTO,
            "contesto": "Cliente saluta per iniziare conversazione"
        },
        {
            "msg": "Sono nuovo, cosa vendete?",
            "expected": IntentType.RICHIESTA_LISTA,
            "contesto": "Cliente chiede info sui prodotti"
        },
        {
            "msg": "Avete integratori?",
            "expected": IntentType.RICERCA_PRODOTTO,
            "contesto": "Cliente cerca prodotto specifico"
        },
        {
            "msg": "Quanto costa l'integratore multivitaminico?",
            "expected": IntentType.DOMANDA_FAQ,  # "quanto" è interrogativa
            "contesto": "Cliente chiede prezzo"
        },
    ],

    "Ordine Diretto": [
        {
            "msg": "Buongiorno",
            "expected": IntentType.SALUTO,
            "contesto": "Cliente inizia con saluto formale"
        },
        {
            "msg": "Vorrei ordinare 2 oli extra vergine bio",
            "expected": IntentType.INVIO_ORDINE,
            "contesto": "Ordine con quantità e prodotto"
        },
        {
            "msg": "Spedire a Via Garibaldi 25, Roma",
            "expected": IntentType.INVIO_ORDINE,
            "contesto": "Cliente fornisce indirizzo"
        },
        {
            "msg": "Pago con bonifico bancario",
            "expected": IntentType.INVIO_ORDINE,
            "contesto": "Cliente specifica metodo pagamento"
        },
    ],

    "Cliente con Domande": [
        {
            "msg": "Buonasera",
            "expected": IntentType.SALUTO,
            "contesto": "Saluto serale"
        },
        {
            "msg": "Quanto tempo ci vuole per la spedizione?",
            "expected": IntentType.DOMANDA_FAQ,
            "contesto": "Domanda su tempi spedizione"
        },
        {
            "msg": "Accettate pagamenti in crypto?",
            "expected": IntentType.DOMANDA_FAQ,
            "contesto": "Domanda su metodi pagamento"
        },
        {
            "msg": "Come faccio a tracciare il pacco?",
            "expected": IntentType.DOMANDA_FAQ,
            "contesto": "Domanda su tracking"
        },
    ],

    "Messaggi Brevi e Diretti": [
        {
            "msg": "lista",
            "expected": IntentType.RICHIESTA_LISTA,
            "contesto": "Richiesta diretta lista"
        },
        {
            "msg": "prezzi",
            "expected": IntentType.RICHIESTA_LISTA,
            "contesto": "Richiesta prezzi"
        },
        {
            "msg": "catalogo",
            "expected": IntentType.RICHIESTA_LISTA,
            "contesto": "Richiesta catalogo"
        },
    ],

    "Casi Ambigui e Difficili": [
        {
            "msg": "voglio ordinare",
            "expected": IntentType.FALLBACK,  # Senza prodotto
            "contesto": "Intenzione di ordinare ma senza dettagli"
        },
        {
            "msg": "ne prendo 2",
            "expected": IntentType.FALLBACK,  # Senza contesto
            "contesto": "Quantità senza specificare cosa"
        },
        {
            "msg": "quanto costa?",
            "expected": IntentType.DOMANDA_FAQ,
            "contesto": "Domanda prezzo senza specificare prodotto"
        },
        {
            "msg": "hai olio?",
            "expected": IntentType.RICERCA_PRODOTTO,
            "contesto": "Ricerca disponibilità prodotto"
        },
    ],

    "Ordini Complessi": [
        {
            "msg": "Vorrei ordinare:\n- 2 oli extra vergine\n- 1 miele biologico\n- 3 tisane",
            "expected": IntentType.INVIO_ORDINE,
            "contesto": "Ordine multiprodotto formattato"
        },
        {
            "msg": "Voglio 1 crema viso, spedire a Milano, pago con crypto",
            "expected": IntentType.INVIO_ORDINE,
            "contesto": "Ordine completo in un messaggio"
        },
    ],

    "Typo e Errori Comuni": [
        {
            "msg": "vorrei la lsta",
            "expected": IntentType.RICHIESTA_LISTA,  # Dovrebbe capire nonostante typo
            "contesto": "Typo su 'lista'"
        },
        {
            "msg": "liste prodotti",
            "expected": IntentType.RICHIESTA_LISTA,
            "contesto": "Plurale invece di singolare"
        },
    ],
}


def run_scenario_tests(classifier):
    """Esegue tutti i test degli scenari"""
    risultati = {
        'totali': 0,
        'corretti': 0,
        'parziali': 0,
        'errati': 0,
        'dettagli': defaultdict(list)
    }

    print("\n" + "="*80)
    print("🧪 ESECUZIONE TEST SCENARI REALISTICI")
    print("="*80 + "\n")

    for scenario_nome, messaggi in SCENARI_TEST.items():
        print(f"\n{'='*80}")
        print(f"📋 SCENARIO: {scenario_nome}")
        print('='*80)

        for test_case in messaggi:
            msg = test_case['msg']
            expected = test_case['expected']
            contesto = test_case['contesto']

            result = classifier.classify(msg)
            risultati['totali'] += 1

            # Valuta il risultato
            status = ""
            symbol = ""

            if result.intent == expected:
                risultati['corretti'] += 1
                status = "✅ CORRETTO"
                symbol = "✅"
            elif result.confidence < 0.3:
                # Confidence troppo bassa, ma potrebbe essere accettabile
                risultati['parziali'] += 1
                status = "⚠️  PARZIALE (confidence bassa)"
                symbol = "⚠️"
            else:
                risultati['errati'] += 1
                status = f"❌ ERRORE (atteso {expected.value}, ricevuto {result.intent.value})"
                symbol = "❌"

            # Stampa risultato
            print(f"\n{symbol} Test: {contesto}")
            print(f"   📩 Messaggio: \"{msg}\"")
            print(f"   🎯 Atteso: {expected.value}")
            print(f"   📊 Ricevuto: {result.intent.value} (conf: {result.confidence:.2f})")
            print(f"   💭 Reason: {result.reason}")

            if result.matched_keywords:
                keywords_str = ', '.join(result.matched_keywords[:3])
                if len(result.matched_keywords) > 3:
                    keywords_str += f" ... (+{len(result.matched_keywords)-3})"
                print(f"   🔑 Keywords: {keywords_str}")

            print(f"   {status}")

            # Salva dettagli per report
            risultati['dettagli'][scenario_nome].append({
                'messaggio': msg,
                'contesto': contesto,
                'expected': expected.value,
                'received': result.intent.value,
                'confidence': result.confidence,
                'status': 'OK' if symbol == '✅' else 'WARN' if symbol == '⚠️' else 'FAIL'
            })

    return risultati


def genera_report(risultati):
    """Genera report dettagliato dei risultati"""
    print("\n\n" + "="*80)
    print("📊 REPORT FINALE TEST SCENARI")
    print("="*80)

    # Statistiche generali
    totali = risultati['totali']
    corretti = risultati['corretti']
    parziali = risultati['parziali']
    errati = risultati['errati']

    percentuale_corretti = (corretti / totali * 100) if totali > 0 else 0
    percentuale_ok = ((corretti + parziali) / totali * 100) if totali > 0 else 0

    print(f"\n📈 STATISTICHE GENERALI:")
    print(f"   • Test totali: {totali}")
    print(f"   • ✅ Corretti: {corretti} ({percentuale_corretti:.1f}%)")
    print(f"   • ⚠️  Parziali: {parziali}")
    print(f"   • ❌ Errati: {errati}")
    print(f"   • 📊 Tasso successo: {percentuale_ok:.1f}%")

    # Analisi per scenario
    print(f"\n📋 ANALISI PER SCENARIO:")
    for scenario_nome, tests in risultati['dettagli'].items():
        ok = sum(1 for t in tests if t['status'] == 'OK')
        warn = sum(1 for t in tests if t['status'] == 'WARN')
        fail = sum(1 for t in tests if t['status'] == 'FAIL')
        tot = len(tests)

        print(f"\n   {scenario_nome}:")
        print(f"      ✅ {ok}/{tot} corretti", end="")
        if warn > 0:
            print(f", ⚠️  {warn} parziali", end="")
        if fail > 0:
            print(f", ❌ {fail} errati", end="")
        print()

    # Problemi identificati
    print(f"\n🔍 PROBLEMI IDENTIFICATI:")

    problemi = []
    for scenario_nome, tests in risultati['dettagli'].items():
        for test in tests:
            if test['status'] in ['WARN', 'FAIL']:
                problemi.append({
                    'scenario': scenario_nome,
                    'messaggio': test['messaggio'],
                    'expected': test['expected'],
                    'received': test['received'],
                    'confidence': test['confidence'],
                    'status': test['status']
                })

    if problemi:
        for i, prob in enumerate(problemi, 1):
            symbol = "⚠️" if prob['status'] == 'WARN' else "❌"
            print(f"\n   {symbol} Problema #{i}:")
            print(f"      Scenario: {prob['scenario']}")
            print(f"      Messaggio: \"{prob['messaggio']}\"")
            print(f"      Atteso: {prob['expected']} | Ricevuto: {prob['received']}")
            print(f"      Confidence: {prob['confidence']:.2f}")
    else:
        print("\n   ✅ Nessun problema rilevato!")

    # Raccomandazioni
    print(f"\n💡 RACCOMANDAZIONI:")

    if errati > 0:
        print("\n   🔴 PRIORITÀ ALTA:")
        print(f"      • Correggere {errati} casi di classificazione errata")
        print("      • Rivedere priorità e pattern di classificazione")

    if parziali > 0:
        print("\n   🟡 PRIORITÀ MEDIA:")
        print(f"      • Migliorare confidence per {parziali} casi parziali")
        print("      • Aggiungere più indicatori per casi ambigui")

    # Aree di miglioramento specifiche
    typo_fails = [p for p in problemi if 'typo' in p['scenario'].lower() or 'errori' in p['scenario'].lower()]
    if typo_fails:
        print("\n   📝 Gestione Typo:")
        print("      • Implementare fuzzy matching per pattern comuni")

    ambigui_fails = [p for p in problemi if 'ambig' in p['scenario'].lower()]
    if ambigui_fails:
        print("\n   🤔 Messaggi Ambigui:")
        print("      • Considerare context tracking per messaggi brevi")
        print("      • Migliorare gestione messaggi senza contesto")

    print("\n" + "="*80)


def main():
    """Funzione principale"""
    print("="*80)
    print("🤖 TEST AUTOMATICI SCENARI REALISTICI - INTENT CLASSIFIER")
    print("="*80)

    # Inizializza classifier
    print("\n⏳ Inizializzazione classifier...")
    lista_text = load_lista_prodotti()

    lista_keywords = set()
    for line in lista_text.split('\n'):
        if line.strip():
            words = line.lower().split()
            lista_keywords.update([w for w in words if len(w) > 3])

    classifier = IntentClassifier(
        lista_keywords=lista_keywords,
        load_lista_func=lambda: lista_text
    )
    print("✅ Classifier pronto!\n")

    # Esegue test
    risultati = run_scenario_tests(classifier)

    # Genera report
    genera_report(risultati)

    # Salva report JSON
    report_file = 'test_scenarios_report.json'
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(risultati['dettagli'], f, indent=2, ensure_ascii=False, default=str)

    print(f"\n💾 Report dettagliato salvato in: {report_file}")
    print("\n✅ Test completati!\n")


if __name__ == "__main__":
    main()
