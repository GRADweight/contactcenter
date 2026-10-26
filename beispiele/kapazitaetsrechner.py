#!/usr/bin/env python3
"""Kapazitätsrechner als Python-Beispiel: 200 Anrufe pro Tag, wie viele Agenten,
wie viele Sprachagent-Kanäle, welche GPU?

    python beispiele/kapazitaetsrechner.py           # Rechnung ausgeben
    python beispiele/kapazitaetsrechner.py selftest  # Werte prüfen (offline)

Die Annahmen (25 % des Tagesvolumens in der Spitzenstunde, 120 s
Bearbeitungszeit, Ziel 20 s mittlere Wartezeit) sind Beispielwerte. Für ein
echtes Projekt durch Daten aus der eigenen Telefonanlage ersetzen.
"""

from __future__ import annotations

import sys

from gw_contactcenter.kapazitaet import Lastannahme, berechne
from gw_contactcenter.sizing import Bedarf, bericht, dimensionieren
from gw_contactcenter.warteschlange import mittlere_wartezeit_s


def rechnung() -> str:
    k = berechne(
        Lastannahme(anrufe_pro_tag=200, spitzenstunden_anteil=0.25, aht_s=120),
        ziel_mittlere_wartezeit_s=20,
    )
    gpu = Bedarf(gespraeche=k.bot_kanaele)
    return k.als_text() + "\n\n" + bericht("qwen3-8b", gpu, dimensionieren("qwen3-8b", gpu), 4)


def selftest() -> int:
    k = berechne(Lastannahme(200), ziel_mittlere_wartezeit_s=20)
    assert k.personal.agenten == 4, k.personal
    # Die frühere Doku nannte 3 Agenten mit 13.5 s; korrekt sind 27.0 s bei 3 Agenten.
    assert round(mittlere_wartezeit_s(3, k.last.angebot_erlang, 120), 1) == 27.0
    assert k.bot_kanaele == 6
    assert dimensionieren("qwen3-8b", Bedarf(gespraeche=k.bot_kanaele))
    print("selftest kapazitaetsrechner: ok")
    return 0


if __name__ == "__main__":
    if "selftest" in sys.argv[1:]:
        sys.exit(selftest())
    print(rechnung())
