"""Fixtures condivise per i test"""
import pytest
import json
import os
from unittest.mock import Mock


@pytest.fixture
def sample_citta_italiane():
    """Fixture con lista città italiane di test"""
    return {
        'roma', 'milano', 'napoli', 'torino', 'palermo', 'genova',
        'bologna', 'firenze', 'bari', 'catania', 'venezia', 'verona',
        'messina', 'padova', 'trieste', 'brescia', 'taranto', 'prato'
    }


@pytest.fixture
def sample_lista_prodotti():
    """Fixture con lista prodotti di esempio"""
    return """
🍾 Olio Extra Vergine Bio - €15.00
🧴 Crema Viso Idratante - €25.00
💊 Integratore Multivitaminico - €18.50
🫙 Miele Biologico 500g - €12.00
🧪 Siero Anti-Age - €35.00
🌿 Tisana Rilassante - €8.50
"""


@pytest.fixture
def mock_load_lista(sample_lista_prodotti):
    """Mock della funzione load_lista"""
    def _load():
        return sample_lista_prodotti
    return _load


@pytest.fixture
def classifier_with_lista(mock_load_lista, sample_citta_italiane, monkeypatch):
    """Fixture che crea un IntentClassifier configurato per i test"""
    from intent_classifier import IntentClassifier, load_citta_italiane

    # Mock della funzione load_citta_italiane
    monkeypatch.setattr('intent_classifier.load_citta_italiane',
                        lambda: sample_citta_italiane)

    # Estrai keywords dalla lista prodotti
    lista_keywords = set()
    for line in mock_load_lista().split('\n'):
        if line.strip():
            words = line.lower().split()
            lista_keywords.update([w for w in words if len(w) > 3])

    return IntentClassifier(
        lista_keywords=lista_keywords,
        load_lista_func=mock_load_lista
    )


@pytest.fixture
def classifier_basic(sample_citta_italiane, monkeypatch):
    """Fixture per classifier senza lista prodotti"""
    from intent_classifier import IntentClassifier

    # Mock della funzione load_citta_italiane
    monkeypatch.setattr('intent_classifier.load_citta_italiane',
                        lambda: sample_citta_italiane)

    return IntentClassifier()


@pytest.fixture
def temp_citta_json(tmp_path):
    """Crea un file JSON temporaneo con città italiane"""
    citta_data = {
        "capoluoghi_provincia": [
            "roma", "milano", "napoli", "torino", "palermo"
        ],
        "citta_maggiori": [
            "genova", "bologna", "firenze", "bari", "catania"
        ]
    }

    json_file = tmp_path / "citta_italiane.json"
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(citta_data, f, ensure_ascii=False)

    return json_file
