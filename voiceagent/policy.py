"""Anliegen-Katalog: was darf der Bot selbst, was geht an Menschen?

Jedes Anliegen hat eine Datenklasse (V0-V4: wie vertraulich sind die
Daten?) und eine Risikoklasse (R0-R3: was passiert, wenn der Bot sich
irrt?). Daraus folgen drei Dinge:

1. **Wo das Modell laufen darf** (Datenresidenz, s. gateway.py):
   V0 überall, V1 bis EU-Cloud, V2-V4 nur auf eigener Infrastruktur.
2. **Wie selbständig der Bot handelt** (Autonomie):
   R0 löst selbst, R1 löst mit Bestätigung, R2 nimmt auf und übergibt,
   R3 übergibt sofort ("warm transfer" mit Zusammenfassung).
3. **Wohin und wie schnell** (Bereich, SLA).

Die Werte sind identisch mit ``beispiele/handoff_router.py``; ein Test
(tests/test_policy.py) stellt sicher, dass beide nicht auseinanderlaufen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

REGIONEN = ("lokal", "ch", "eu", "us")      # von streng nach offen

# Datenklasse -> offenste erlaubte Region für Modell-Inferenz
REGION_JE_KLASSE = {0: "us", 1: "eu", 2: "lokal", 3: "lokal", 4: "lokal"}

BEREICHE = {
    0: "Kundendienst (1. Level)",
    1: "Kundendienst (1. Level)",
    2: "Fachbereich (2. Level)",
    3: "Leitende (3. Level)",
    4: "Geschäftsleitung",
}

# Autonomie je Risikoklasse
LOEST_SELBST = "loest_selbst"
MIT_BESTAETIGUNG = "mit_bestaetigung"
AUFNEHMEN_UND_UEBERGEBEN = "aufnehmen_und_uebergeben"
SOFORT_UEBERGEBEN = "sofort_uebergeben"
AUTONOMIE = {0: LOEST_SELBST, 1: MIT_BESTAETIGUNG,
             2: AUFNEHMEN_UND_UEBERGEBEN, 3: SOFORT_UEBERGEBEN}


def region_erlaubt(region: str, klasse: int) -> bool:
    return REGIONEN.index(region) <= REGIONEN.index(REGION_JE_KLASSE[klasse])


@dataclass(frozen=True)
class Anliegen:
    id: str
    beschreibung: str
    klasse: int          # V0-V4
    risiko: int          # R0-R3
    sla_tage: float
    tools: Tuple[str, ...] = ()
    stichwoerter: Tuple[str, ...] = field(default=(), compare=False)

    @property
    def bereich(self) -> str:
        return BEREICHE[self.klasse]

    @property
    def autonomie(self) -> str:
        return AUTONOMIE[self.risiko]


KATALOG: Dict[str, Anliegen] = {a.id: a for a in [
    Anliegen("allgemeine-frage", "Allgemeine Frage, Öffnungszeiten, Beratung",
             1, 0, 5.0, ("wissen_suchen",),
             ("öffnungszeit", "wie funktioniert", "frage", "service", "wann habt")),
    Anliegen("preis-anfrage", "Preis, Offerte, Angebot", 1, 0, 7.0,
             ("wissen_suchen",), ("preis", "kostet", "offerte", "angebot")),
    Anliegen("bestellung-ankommen", "Bestellstatus, Lieferung", 2, 1, 2.0,
             ("bestellstatus_abfragen", "ticket_erstellen"),
             ("bestellung", "lieferung", "paket", "nicht angekommen", "zustellung")),
    Anliegen("reklamation", "Reklamation, beschädigtes Produkt", 2, 2, 3.0,
             ("ticket_erstellen",),
             ("reklamation", "beschädigt", "kaputt", "defekt", "falsch geliefert")),
    Anliegen("konto-stellung", "Konto, Guthaben, Zahlung", 3, 2, 1.0,
             ("ticket_erstellen",),
             ("konto", "guthaben", "zahlung", "rechnung", "rückerstattung", "belastet",
              "abgebucht", "abbuchung", "kreditkarte", "karte")),
    Anliegen("vertrag-aenderung", "Vertrag ändern oder kündigen", 3, 3, 1.0, (),
             ("vertrag", "kündigen", "kündigung", "abo beenden")),
    Anliegen("datenschutz-verstoss", "Meldung Datenleck / DSG-Verstoss", 4, 3, 1.0, (),
             ("datenschutz", "datenleck", "meine daten", "dsg")),
]}

UNBEKANNT = Anliegen("unbekannt", "Nicht erkannt", 2, 1, 2.0, ("ticket_erstellen",))


def klassifiziere_regelbasiert(text: str) -> Tuple[str, float]:
    """Stichwort-Klassifikation als Fallback und für Offline-Tests.

    Reihenfolge = Priorität: das riskanteste passende Anliegen gewinnt,
    damit "Ich will kündigen, und wo ist meine Bestellung?" nicht beim
    Bot hängen bleibt.
    """
    t = text.lower()
    treffer: List[Anliegen] = [a for a in KATALOG.values()
                               if any(s in t for s in a.stichwoerter)]
    if not treffer:
        return UNBEKANNT.id, 0.0
    bestes = max(treffer, key=lambda a: (a.risiko, a.klasse))
    return bestes.id, 0.8 if len(treffer) == 1 else 0.6


def anliegen(id_: str) -> Anliegen:
    return KATALOG.get(id_, UNBEKANNT)
