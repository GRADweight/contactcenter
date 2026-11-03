#!/usr/bin/env python3
"""Nachbearbeitung eines Gesprächs: Transkript -> Pseudonymisierung -> Anliegen -> Übergabe.

Typischer Einsatz neben dem Live-Agenten: Nach einem Gespräch (Bot oder
Mensch) wird das Transkript pseudonymisiert, einem Anliegen zugeordnet und als
Ticketvorschlag mit Bereich und Frist aufbereitet. Das Transkript hier ist
erfunden; die Diarisierung (wer spricht) liefert bei Genesys der getrennte
Stereo-Kanal (external/internal).

    python beispiele/transkript_pipeline.py
    python beispiele/transkript_pipeline.py selftest
"""

from __future__ import annotations

import json
import sys
from typing import Any

from gw_contactcenter.pii import PIITresor
from gw_contactcenter.policy import anliegen, klassifiziere_regelbasiert

TRANSKRIPT = [
    ("kundschaft", "Grüezi, meine Bestellung 447100 ist nicht angekommen."),
    ("agent", "Das tut mir leid. Darf ich Ihre Rückrufnummer notieren?"),
    ("kundschaft", "Ja, 079 123 45 67. Und das Geld bitte auf CH93 0076 2011 6238 5295 7."),
    ("agent", "Ich erfasse eine Reklamation und leite sie weiter."),
]


def verarbeiten(transkript: list[tuple[str, str]]) -> dict[str, Any]:
    tresor = PIITresor()
    segmente = []
    gefunden: set[str] = set()
    for sprecher, text in transkript:
        p, typen = tresor.pseudonymisieren(text)
        gefunden.update(typen)
        segmente.append({"sprecher": sprecher, "text": p})
    kundentext = " ".join(s["text"] for s in segmente if s["sprecher"] == "kundschaft")
    a_id, _ = klassifiziere_regelbasiert(kundentext)
    a = anliegen(a_id)
    # Personendaten im Gespräch heben die Datenklasse auf mindestens V2.
    klasse = max(a.klasse, 2 if gefunden else 0)
    return {
        "anliegen": a.id,
        "datenklasse": f"V{klasse}",
        "bereich": a.bereich,
        "frist_tage": a.sla_tage,
        "personendaten": sorted(gefunden),
        "segmente": segmente,
    }


def selftest() -> int:
    r = verarbeiten(TRANSKRIPT)
    text = json.dumps(r, ensure_ascii=False)
    assert "079 123 45 67" not in text, "keine Roh-PII im Ergebnis"
    assert "CH93" not in text, "keine Roh-PII im Ergebnis"
    assert "447100" in text, "Bestellnummern bleiben sichtbar"
    assert r["anliegen"] == "bestellung-ankommen"
    assert r["personendaten"] == ["IBAN", "TELEFON"]
    assert r["datenklasse"] == "V2"
    print("selftest transkript_pipeline: ok")
    return 0


if __name__ == "__main__":
    if "selftest" in sys.argv[1:]:
        sys.exit(selftest())
    print(json.dumps(verarbeiten(TRANSKRIPT), ensure_ascii=False, indent=2))
