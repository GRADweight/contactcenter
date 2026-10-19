#!/usr/bin/env python3
"""Handoff/Weiterleitung-Router (nur Python-stdlib).

Idee:  Der eigentliche Wert eines AI-Contactcenter liegt NICHT darin,
dass der Agent den Anruf beantwortet. Der Wert liegt in der WEITERLEITUNG
zum nächsten Bereich - der «Mensch im Loop», die Eskalation, die SLA,
die Berechtigungen. Dieser Sample modelliert genau das.

Die Logik: Jeder Anruf hat 4 Eigenschaften, die entscheiden:
  1. Vertraulichkeitsstufe (V0-V4, s. doku/05-daten-speicherung.md)
  2. Risiko (R0-R3, s. doku/06-weiterleitung.md)
  3. Autonomiegrad (s. grad-autonomy: Grad 01–05)
  4. SLA (wie lange darf das Ticket offen sein)

Der Router rechnet daraus:
  - WELCHER Bereich zuständig ist (Fachbereich-Zugehörigkeit)
  - WELCHES Autonomiegrad der KI erlaubt ist (grad-autonomy)
  - WENN der Mensch in den Loop kommt (Freigabe-Schwelle)
  - WAS die SLA für dieses Ticket ist

Dies ist bewusst keine «smart» Logik, sondern EINE TABELLE mit
klaren Regeln. So ist das System auditierbar und testbar.

Nutzung:
  python beispiele/handoff_router.py selftest
  python beispiele/handoff_router.py route --anruf bestellung-verzögert
  python beispiele/handoff_router.py --help
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Optional


# --------------------------------------------------------------------------
# 1. Vertraulichkeit (V0-V4, s. doku/05, gekürzt auf die 5 Stufen)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Vertraulichkeitsstufe:
    wert: int       # 0-4
    beschreibung: str
    darf_lokal: bool
    darf_cloud: bool
    aufbewahrung_jahre: float
    verbindung: str   # wer darf lesen


# Die 5 Stufen - bewusst simpel (s. doku/06 für die Vollversion).
# cloud-Flag = "dürfen Roh-Ton + Transkript in Managed Cloud liegen".
# Regel: nur V0 (öffentlich) und V1 (normal) sind cloud-fähig.
# V2 intern / V3 vertraulich / V4 streng bleiben lokal (CH-Region).
V0 = Vertraulichkeitsstufe(0, "öffentlich",       True,  True,  0.0, "alle")
V1 = Vertraulichkeitsstufe(1, "normal",           True,  True,  0.5, "agent")
V2 = Vertraulichkeitsstufe(2, "intern",           True,  False, 1.0, "fachabteil")
V3 = Vertraulichkeitsstufe(3, "vertraulich",      True,  False, 3.0, "leiter")
V4 = Vertraulichkeitsstufe(4, "streng",           True,  False, 10.0, "leitung")

ALL_STUFEN = [V0, V1, V2, V3, V4]


# --------------------------------------------------------------------------
# 2. Risiko-Rahmen (R0-R3)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Risikoklasse:
    wert: int
    beschreibung: str
    maximaler_autonomiegrad: int  # aus grad-autonomy
    freigabe_zwisc_1mensch: bool


# s. doku/06-weiterleitung.md für den vollständigen Rahmen
R0 = Risikoklasse(0, "gering",    maximaler_autonomiegrad=5,
                     freigabe_zwisc_1mensch=False)
R1 = Risikoklasse(1, "mittel",    maximaler_autonomiegrad=4,
                     freigabe_zwisc_1mensch=False)
R2 = Risikoklasse(2, "hoch",      maximaler_autonomiegrad=3,
                     freigabe_zwisc_1mensch=True)
R3 = Risikoklasse(3, "kritisch",  maximaler_autonomiegrad=2,
                     freigabe_zwisc_1mensch=True)

ALL_RISIKEN = [R0, R1, R2, R3]


# --------------------------------------------------------------------------
# 3. Anfall-Kategorien (was kommt aus dem Anruf)
# --------------------------------------------------------------------------

@dataclass
class Anfall:
    id: str
    beschreibung: str
    vertraulichkeitsstufe: int    # 0-4
    risikoklasse: int             # 0-3
    sla_tag: float                # maximal offenes Ticket in Tagen


# Katalog - bewusst klein und erweiterbar
ANFALL_KATALOG: dict[str, Anfall] = {
    "bestellung-ankommen": Anfall(
        "bestellung-ankommen",
        "Bestellung ist angekommen oder nicht angekommen",
        vertraulichkeitsstufe=V2.wert,
        risikoklasse=R1.wert,
        sla_tag=2.0,
    ),
    "konto-stellung": Anfall(
        "konto-stellung",
        "Konto, Guthaben, Zahlung",
        vertraulichkeitsstufe=V3.wert,
        risikoklasse=R2.wert,
        sla_tag=1.0,
    ),
    "reklamation": Anfall(
        "reklamation",
        "Reklamation, beschädigtes Produkt",
        vertraulichkeitsstufe=V2.wert,
        risikoklasse=R2.wert,
        sla_tag=3.0,
    ),
    "allgemeine-frage": Anfall(
        "allgemeine-frage",
        "Allgemeine Frage, Beratung",
        vertraulichkeitsstufe=V1.wert,
        risikoklasse=R0.wert,
        sla_tag=5.0,
    ),
    "vertrag-aenderung": Anfall(
        "vertrag-aenderung",
        "Vertrag ändern, kündigen",
        vertraulichkeitsstufe=V3.wert,
        risikoklasse=R3.wert,
        sla_tag=1.0,
    ),
    "preis-anfrage": Anfall(
        "preis-anfrage",
        "Preis, Offerte, Angebot",
        vertraulichkeitsstufe=V1.wert,
        risikoklasse=R0.wert,
        sla_tag=7.0,
    ),
    # V4/GL-Fall (Coverage-Reparatur, s. qualitaetstest Q1-03): der
    # Katalog deckte frueher nur V0-V3 ab - der strengste Fall
    # (Geschaeftsleitung) war unerreichenbar. DSG-relevant.
    "datenschutz-verstoss": Anfall(
        "datenschutz-verstoss",
        "Meldung eines Datenleck / DSG-Verstoss",
        vertraulichkeitsstufe=V4.wert,
        risikoklasse=R3.wert,
        sla_tag=1.0,
    ),
}


# --------------------------------------------------------------------------
# 4. Der Router: Anfall -> nächste Schritte
# --------------------------------------------------------------------------

@dataclass
class Weiterleitung:
    anfall_id: str
    beschreibung: str
    zuständiger_bereich: str
    autonomiegrad_mit_mensch: int   # aus grad-autonomy
    autonomiegrad_maximal: int
    freigabe_mensch_zwischen: bool
    sla_tag: float
    verbindung: str
    darf_in_cloud: bool
    grund: str


_BEREICH_MAPPE: dict[int, str] = {
    V1.wert: "Kundendienst (1. Level)",
    V2.wert: "Fachbereich (2. Level)",
    V3.wert: "Leitende (3. Level)",
    V4.wert: "Geschäftsleitung",
}


def route_anfall(anfall_id: str, autonomiegrad_i_d: int = 0) -> dict:
    """Routet einen Anfall zum richtigen Bereich + Autonomiegrad + SLA.

    Param
    -----
    anfall_id       : id aus ANFALL_KATALOG
    autonomiegrad_i_d : Grad aus grad-autonomy (1-5). Falls 0,
                       wird der maximale erlaubte Grad
                       aus der Risikoklasse abgeleitet.
    """
    if anfall_id not in ANFALL_KATALOG:
        return {"fehler": f"Anfall '{anfall_id}' nicht im Katalog",
                "katalog": list(ANFALL_KATALOG.keys()),
                "ok": False}
    anfall = ANFALL_KATALOG[anfall_id]
    vst = ALL_STUFEN[anfall.vertraulichkeitsstufe]
    rk = ALL_RISIKEN[anfall.risikoklasse]
    # Autonomiegrad: wenn nicht spezifiziert, nimm maximal möglichen
    if autonomiegrad_i_d <= 0:
        autonomiegrad_i_d = rk.maximaler_autonomiegrad
    autonomiegrad_i_d = min(autonomiegrad_i_d, rk.maximaler_autonomiegrad, 5)
    zuständig = _BEREICH_MAPPE[anfall.vertraulichkeitsstufe]
    grund = (
        f"V{vst.wert}/{vst.beschreibung} -> R{rk.wert}/{rk.beschreibung} "
        f"-> Autonomie {autonomiegrad_i_d}/5 "
        f"{'mit Freigabe' if autonomiegrad_i_d < rk.maximaler_autonomiegrad else 'ohne Freigabe im Zwischen'} "
        f"-> SLA {anfall.sla_tag} Tag(e) -> {vst.verbindung}"
    )
    return {
        "anfall": anfall.beschreibung,
        "vertraulichkeitsstufe": f"V{vst.wert} {vst.beschreibung}",
        "risikoklasse": f"R{rk.wert} {rk.beschreibung}",
        "autonomiegrad_mit_mensch": autonomiegrad_i_d,
        "autonomiegrad_maximal": rk.maximaler_autonomiegrad,
        "freigabe_mensch_zwischen": rk.freigabe_zwisc_1mensch,
        "zuständiger_bereich": zuständig,
        "sla_tag": anfall.sla_tag,
        "verbindend_fuer": vst.verbindung,
        "darf_in_cloud": vst.darf_cloud,
        "grundlage": grund,
        "ok": True,
    }


# --------------------------------------------------------------------------
# 5. Selbsttest
# --------------------------------------------------------------------------

def selftest() -> int:
    print("== selftest: handoff_router ==")

    # 1. Katalog hat 7 Einträge (seit Coverage-Reparatur: +datenschutz-verstoss V4)
    assert len(ANFALL_KATALOG) == 7, ANFALL_KATALOG
    print(f"  Katalog: {len(ANFALL_KATALOG)} Anfall-Typen")

    # 1b. Alle 4 Levels erreichbar (V1..V4), s. qualitaetstest Q1
    levels = {route_anfall(a)["zuständiger_bereich"]
              for a in ANFALL_KATALOG}
    for lv in ("Kundendienst (1. Level)", "Fachbereich (2. Level)",
               "Leitende (3. Level)", "Geschäftsleitung"):
        assert lv in levels, f"Level {lv} unerreichenbar"

    # 2. Routing für den häufigsten Fall (Bestellungsstatus)
    res = route_anfall("bestellung-ankommen")
    assert res["ok"] is True
    assert res["zuständiger_bereich"] == "Fachbereich (2. Level)", res
    assert res["autonomiegrad_maximal"] == R1.maximaler_autonomiegrad, res
    assert res["sla_tag"] == 2.0, res
    print(f"  bestellung-ankommen -> {res['zuständiger_bereich']}, "
          f"Autonomie {res['autonomiegrad_maximal']}/5, "
          f"SLA {res['sla_tag']}d")

    # 3. Vertrag = R3 -> max Autonomiegrad 2
    res3 = route_anfall("vertrag-aenderung")
    assert res3["autonomiegrad_maximal"] == R3.maximaler_autonomiegrad, res3
    assert res3["autonomiegrad_maximal"] == 2, res3
    assert res3["freigabe_mensch_zwischen"] is True, res3
    assert res3["zuständiger_bereich"] == "Leitende (3. Level)", res3
    assert res3["sla_tag"] == 1.0, res3
    assert res3["darf_in_cloud"] is False, res3
    print(f"  vertrag-aenderung -> {res3['zuständiger_bereich']}, "
          f"R3 max Autonomie {res3['autonomiegrad_maximal']}, SLA {res3['sla_tag']}d, "
          f"kein Cloud {res3['darf_in_cloud']}")

    # 4. Preis-Anfrage = R0 -> volle Autonomie
    res0 = route_anfall("preis-anfrage")
    assert res0["autonomiegrad_maximal"] == R0.maximaler_autonomiegrad, res0
    assert res0["autonomiegrad_maximal"] == 5, res0
    assert res0["freigabe_mensch_zwischen"] is False, res0
    assert res0["zuständiger_bereich"] == "Kundendienst (1. Level)", res0
    print(f"  preisanfrage -> {res0['zuständiger_bereich']}, "
          f"Autonomie {res0['autonomiegrad_maximal']}/5, "
          f"SLA {res0['sla_tag']}d")

    # 5. Unbekannter Anfall
    res_unknown = route_anfall("unbekannter-anfall")
    assert res_unknown["ok"] is False, res_unknown
    print(f"  unbekannter Anfall -> ok=False (fehler mit Katalog)")

    # 6. Manueller Autonomiegrad-Override
    res_override = route_anfall("bestellung-ankommen", autonomiegrad_i_d=2)
    assert res_override["autonomiegrad_mit_mensch"] == 2, res_override
    # Aber max bleibt bei R1.maximaler
    assert res_override["autonomiegrad_mit_mensch"] <= res_override["autonomiegrad_maximal"], res_override
    print(f"  Override: bestellung-ankommen @ Grad {res_override['autonomiegrad_mit_mensch']} "
          f"(max {res_override['autonomiegrad_maximal']})")

    # 7. Reihenfolge: R3 hat die strengsten Einschränkungen
    assert R3.maximaler_autonomiegrad < R0.maximaler_autonomiegrad, "R3 muss restriktiver sein"
    assert R3.maximaler_autonomiegrad < R2.maximaler_autonomiegrad, "R3 > R2"
    assert R2.maximaler_autonomiegrad < R1.maximaler_autonomiegrad, "R2 > R1"
    assert R1.maximaler_autonomiegrad < R0.maximaler_autonomiegrad, "R1 > R0"
    print(f"  R0->R1->R2->R3: {R0.maximaler_autonomiegrad} -> "
          f"{R1.maximaler_autonomiegrad} -> "
          f"{R2.maximaler_autonomiegrad} -> "
          f"{R3.maximaler_autonomiegrad} (strenger)")

    print("== selftest PASSED ==")
    return 0


def route_arg(args) -> int:
    if len(args) < 1:
        print("Nutzung: python handoff_router.py route --anfall <id> [grad]")
        print("Katalog:", list(ANFALL_KATALOG.keys()))
        return 0
    anfall_id = args[0]
    grad = int(args[1]) if len(args) > 1 else 0
    res = route_anfall(anfall_id, grad)
    if not res.get("ok"):
        print(f"Fehler: {res.get('fehler')} (Katalog: {res.get('katalog')})")
    else:
        print(f"Anfall:              {res['anfall']}")
        print(f"Vertraulichkeit:     {res['vertraulichkeitsstufe']}")
        print(f"Risikoklasse:        {res['risikoklasse']}")
        print(f"Autonomiegrad:        {res['autonomiegrad_mit_mensch']} von "
              f"{res['autonomiegrad_maximal']} (max)")
        print(f"Zwischenfreigabe:       {'ja' if res['freigabe_mensch_zwischen'] else 'nein'}")
        print(f"Zuständiger Bereich:    {res['zuständiger_bereich']}")
        print(f"SLA:                    {res['sla_tag']} Tag(e)")
        print(f"Verbindend für:         {res['verbindend_fuer']}")
        print(f"In Cloud erlaubt:       {'ja' if res['darf_in_cloud'] else 'NEIN'}")
        print(f"Grundlage:              {res['grundlage']}")
    return 0

if __name__ == "__main__":
    if "selftest" in sys.argv:
        sys.exit(selftest())
    if "route" in sys.argv:
        args = sys.argv[sys.argv.index("route") + 1:]
        # Handle "--anfall <id>" syntax
        if args and args[0] == "--anfall":
            args = args[1:]
        sys.exit(route_arg(args))
    print(__doc__)
    sys.exit(0)
