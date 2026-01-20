#!/usr/bin/env python3
"""
Script di Test Interattivo per Intent Classifier

Permette di testare il classificatore come se fossi un utente reale,
vedendo esattamente come risponde a diversi tipi di messaggi.
"""

import sys
import json
from intent_classifier import IntentClassifier, IntentType


def load_lista_prodotti():
    """Carica la lista prodotti reale dal file (se esiste)"""
    try:
        # Prova a caricare da file se esiste
        with open('lista_prodotti.txt', 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        # Usa lista di esempio
        return """
🍾 Olio Extra Vergine Bio - €15.00
🧴 Crema Viso Idratante - €25.00
💊 Integratore Multivitaminico - €18.50
🫙 Miele Biologico 500g - €12.00
🧪 Siero Anti-Age - €35.00
🌿 Tisana Rilassante - €8.50
"""


def print_result(text, result):
    """Stampa il risultato della classificazione in modo leggibile"""
    print("\n" + "="*70)
    print(f"📩 MESSAGGIO: {text}")
    print("="*70)

    # Intent con emoji
    intent_emoji = {
        IntentType.RICHIESTA_LISTA: "📋",
        IntentType.INVIO_ORDINE: "📦",
        IntentType.DOMANDA_FAQ: "❓",
        IntentType.RICERCA_PRODOTTO: "🔎",
        IntentType.SALUTO: "👋",
        IntentType.FALLBACK: "🤷"
    }

    emoji = intent_emoji.get(result.intent, "❔")

    print(f"\n{emoji} INTENT: {result.intent.value.upper()}")
    print(f"📊 CONFIDENCE: {result.confidence:.2f} ({result.confidence*100:.0f}%)")
    print(f"💭 REASON: {result.reason}")

    if result.matched_keywords:
        print(f"🔑 KEYWORDS: {', '.join(result.matched_keywords[:5])}")
        if len(result.matched_keywords) > 5:
            print(f"   ... e altri {len(result.matched_keywords)-5} keywords")

    # Interpretazione per l'utente
    print("\n📝 COSA FAREBBE IL BOT:")
    if result.intent == IntentType.RICHIESTA_LISTA:
        print("   → Invierebbe la lista completa dei prodotti")
    elif result.intent == IntentType.INVIO_ORDINE:
        print("   → Processerebbe l'ordine e richiederebbe conferma")
    elif result.intent == IntentType.DOMANDA_FAQ:
        print("   → Cercherebbe la risposta nelle FAQ")
    elif result.intent == IntentType.RICERCA_PRODOTTO:
        print("   → Cercherebbe il prodotto nella lista")
    elif result.intent == IntentType.SALUTO:
        print("   → Risponderebbe con un saluto")
    else:
        print("   → Chiederrebbe chiarimenti (messaggio non chiaro)")

    # Warning per confidence bassa
    if result.confidence < 0.5 and result.intent != IntentType.FALLBACK:
        print("\n⚠️  ATTENZIONE: Confidence bassa - il bot potrebbe non essere sicuro")

    print("="*70)


def test_conversazione(classifier):
    """Testa una conversazione realistica passo per passo"""
    print("\n" + "🎭 SIMULAZIONE CONVERSAZIONE CLIENTE" + "\n")

    conversazione = [
        # Scenario 1: Cliente che vuole vedere i prodotti
        "Ciao!",
        "Vorrei vedere cosa vendete",
        "Avete olio extra vergine?",
        "Quanto costa?",
        "Ok, ne prendo 2 bottiglie",
        "Spedite a Milano?",

        # Scenario 2: Cliente che fa ordine diretto
        "Buongiorno, vorrei ordinare 3 integratori multivitaminici",
        "Spedire a Via Roma 15, Torino",
        "Pago con bonifico",

        # Scenario 3: Cliente con domande
        "Quando arriva il pacco?",
        "Accettate crypto?",
        "Come faccio a tracciare l'ordine?",

        # Scenario 4: Casi ambigui
        "lista",
        "ordine",
        "voglio ordinare",
        "hai la lista?",
    ]

    for i, msg in enumerate(conversazione, 1):
        result = classifier.classify(msg)
        print(f"\n[Messaggio {i}/{len(conversazione)}]")
        print_result(msg, result)

        # Pausa tra i messaggi per leggibilità
        if i < len(conversazione):
            input("\n↵ Premi INVIO per il prossimo messaggio...")


def test_casi_problematici(classifier):
    """Testa casi che potrebbero essere problematici"""
    print("\n" + "🔍 TEST CASI PROBLEMATICI" + "\n")

    casi = {
        "Typo e errori": [
            "vorrei la lsta",  # typo
            "liste prodotti",  # plurale
            "listta",  # doppia consonante
        ],
        "Messaggi brevi": [
            "lista",
            "ciao",
            "ok",
            "si",
        ],
        "Ordini ambigui": [
            "vorrei ordinare",  # senza prodotto
            "voglio fare un ordine",  # senza dettagli
            "ne prendo 2",  # senza contesto
        ],
        "Domande ambigue": [
            "hai olio?",  # ricerca o disponibilità?
            "quanto costa?",  # senza specificare cosa
            "quando arriva?",  # senza contesto ordine
        ],
        "Mix di intenti": [
            "Ciao, vorrei la lista e anche ordinare 2 oli",
            "Quanto costa la spedizione? Vorrei anche la lista",
        ]
    }

    for categoria, messaggi in casi.items():
        print(f"\n{'='*70}")
        print(f"📁 CATEGORIA: {categoria}")
        print('='*70)

        for msg in messaggi:
            result = classifier.classify(msg)
            print_result(msg, result)

        input("\n↵ Premi INVIO per la prossima categoria...")


def modalita_interattiva(classifier):
    """Modalità interattiva per testare messaggi personalizzati"""
    print("\n" + "💬 MODALITÀ INTERATTIVA" + "\n")
    print("Scrivi messaggi come se fossi un cliente.")
    print("Il bot ti dirà come li interpreta.")
    print("Scrivi 'exit' per uscire.\n")

    while True:
        try:
            msg = input("👤 Cliente: ").strip()

            if msg.lower() in ['exit', 'quit', 'esci']:
                print("\n👋 Arrivederci!")
                break

            if not msg:
                continue

            result = classifier.classify(msg)
            print_result(msg, result)
            print()

        except KeyboardInterrupt:
            print("\n\n👋 Arrivederci!")
            break
        except Exception as e:
            print(f"\n❌ Errore: {e}\n")


def main():
    """Funzione principale"""
    print("="*70)
    print("🤖 TEST INTERATTIVO INTENT CLASSIFIER")
    print("="*70)

    # Inizializza classificatore con dati reali
    print("\n⏳ Caricamento classificatore...")

    lista_text = load_lista_prodotti()

    # Estrai keywords dalla lista
    lista_keywords = set()
    for line in lista_text.split('\n'):
        if line.strip():
            words = line.lower().split()
            lista_keywords.update([w for w in words if len(w) > 3])

    # Crea classifier con funzione load_lista
    classifier = IntentClassifier(
        lista_keywords=lista_keywords,
        load_lista_func=lambda: lista_text
    )

    print("✅ Classificatore pronto!\n")

    # Menu
    while True:
        print("\n" + "="*70)
        print("SCEGLI MODALITÀ DI TEST:")
        print("="*70)
        print("1. 🎭 Simulazione conversazione realistica")
        print("2. 🔍 Test casi problematici")
        print("3. 💬 Modalità interattiva (scrivi tu i messaggi)")
        print("4. 🚪 Esci")
        print("="*70)

        scelta = input("\nScelta (1-4): ").strip()

        if scelta == '1':
            test_conversazione(classifier)
        elif scelta == '2':
            test_casi_problematici(classifier)
        elif scelta == '3':
            modalita_interattiva(classifier)
        elif scelta == '4':
            print("\n👋 Arrivederci!")
            break
        else:
            print("\n❌ Scelta non valida")


if __name__ == "__main__":
    main()
