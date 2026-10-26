#!/usr/bin/env python3
"""Weiterleitungstabelle: Welches Anliegen geht wohin, wie selbständig, wie schnell?

Zeigt den Anliegen-Katalog aus :mod:`gw_contactcenter.policy` (einzige Quelle,
keine Kopie): Datenklasse V0-V4, Risikoklasse R0-R3, zulässige Modellregion,
Autonomie des Bots, zuständiger Bereich und Bearbeitungsfrist.

    python beispiele/handoff_router.py                      # ganze Tabelle
    python beispiele/handoff_router.py route reklamation     # ein Anliegen
    python beispiele/handoff_router.py selftest
"""

from __future__ import annotations

import sys

from gw_contactcenter.policy import (
    KATALOG,
    REGION_JE_KLASSE,
    SOFORT_UEBERGEBEN,
    Anliegen,
    anliegen,
    region_erlaubt,
)

AUTONOMIE_TEXT = {
    "loest_selbst": "löst selbst",
    "mit_bestaetigung": "löst mit Bestätigung",
    "aufnehmen_und_uebergeben": "nimmt auf und übergibt",
    "sofort_uebergeben": "übergibt sofort",
}


def zeile(a: Anliegen) -> str:
    return (
        f"{a.id:22} V{a.klasse} R{a.risiko}  Modell bis {REGION_JE_KLASSE[a.klasse]:5}  "
        f"{AUTONOMIE_TEXT[a.autonomie]:24} {a.bereich:26} Frist {a.sla_tage:g} Tag(e)"
    )


def tabelle() -> str:
    return "\n".join(zeile(a) for a in KATALOG.values())


def selftest() -> int:
    for a in KATALOG.values():
        # Heikle Daten (V2-V4) verlassen die eigene Infrastruktur nie.
        assert region_erlaubt("lokal", a.klasse)
        assert not region_erlaubt("ch", a.klasse) or a.klasse <= 1
        # R3 übergibt immer sofort und hat keine Werkzeuge.
        if a.risiko == 3:
            assert a.autonomie == SOFORT_UEBERGEBEN
            assert not a.tools
    assert anliegen("datenschutz-verstoss").bereich == "Geschäftsleitung"
    assert {a.bereich for a in KATALOG.values()} == {
        "Kundendienst (1. Level)",
        "Fachbereich (2. Level)",
        "Leitende (3. Level)",
        "Geschäftsleitung",
    }
    print("selftest handoff_router: ok")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["selftest"]:
        sys.exit(selftest())
    if args[:1] == ["route"] and len(args) == 2:
        if args[1] not in KATALOG:
            print(f"unbekannt: {args[1]}; Katalog: {', '.join(KATALOG)}", file=sys.stderr)
            sys.exit(2)
        print(zeile(KATALOG[args[1]]))
        sys.exit(0)
    print(tabelle())
