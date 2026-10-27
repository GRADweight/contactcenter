"""Kommandozeile: ``gw-contactcenter <befehl>`` bzw. ``python -m gw_contactcenter``.

Befehle:

``kapazitaet``     Personal- und Kanalbedarf aus dem Anrufvolumen (Erlang-C/-B/-A)
``vergleich``      Erlang-Formeln gegen die ereignisdiskrete Simulation
``gpu``            GPU-Dimensionierung aus Modellarchitektur und Datenblatt
``sim``            simulierter Anruf durch die ganze Pipeline (Simulation)
``latenz-messen``  Latenzmessung gegen echte Endpunkte
``audit-pruefen``  Audit-Datei prüfen (Schlüssel aus AUDIT_HMAC_KEY)
``server``         Genesys-Audio-Connector-Server (Extra ``server``)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from . import __version__
from .audit import AuditFehler, AuditKetteDefekt, AuditLog
from .kapazitaet import Lastannahme, berechne, speicherbedarf
from .sizing import MODELLE, Bedarf, bericht, dimensionieren
from .warteschlange import angebot_erlang, erlang_a, kennzahlen
from .warteschlange_sim import simuliere

# Szenarien für ``vergleich`` (deterministisch dank fester Seeds).
VERGLEICH_SZENARIEN: list[tuple[str, float, float, int, float | None, float]] = [
    # (Bezeichnung, Anrufe/h, AHT s, Agenten, Geduld s oder None, CV der Bearbeitungszeit)
    ("README-Beispiel", 50.0, 120.0, 3, None, 1.0),
    ("README-Beispiel", 50.0, 120.0, 4, None, 1.0),
    ("Call Centre Helper", 200.0, 180.0, 13, None, 1.0),
    ("Call Centre Helper", 200.0, 180.0, 14, None, 1.0),
    ("hohe Last", 360.0, 300.0, 33, None, 1.0),
    ("Erlang-A", 200.0, 180.0, 10, 60.0, 1.0),
    # Gegenprobe zur Annahme: lognormale Gespräche (CV 0.5) gegen die Erlang-C-Formel
    ("M/G/n CV 0.5", 200.0, 180.0, 13, None, 0.5),
]


def _tausender(x: int) -> str:
    return f"{x:,}".replace(",", "'")


def _prozent(x: float) -> str:
    return f"{100 * x:.2f} %"


def _sekunden(x: float) -> str:
    return f"{x:.2f}"


def vergleich_text(anrufe: int = 200_000, sl_zeit_s: float = 20.0) -> str:
    z = [
        "Erlang-Formel gegen ereignisdiskrete Simulation (Seed 1, "
        f"{_tausender(anrufe)} Anrufe je Szenario, 20 Batches)",
        "Szenario             A Erl.   n  Kennzahl        Formel   Simulation  ± 1 SE",
    ]
    for name, pro_h, aht, n, geduld, cv in VERGLEICH_SZENARIEN:
        a = angebot_erlang(pro_h, aht)
        s = simuliere(
            pro_h / 3600.0,
            aht,
            n,
            anrufe=anrufe,
            seed=1,
            sl_zeit_s=sl_zeit_s,
            geduld_s=geduld,
            bearbeitung_cv=cv,
        )
        if geduld is None:
            k = kennzahlen(n, a, aht, sl_zeit_s)
            formel = {
                "P(warten)": k.p_warten,
                "ASA s": k.mittlere_wartezeit_s,
                f"SL {sl_zeit_s:g} s": k.servicelevel or 0.0,
            }
        else:
            ea = erlang_a(n, a, aht, geduld, sl_zeit_s)
            formel = {
                "P(warten)": ea.p_warten,
                "P(Abbruch)": ea.p_abbruch,
                "Wartezeit s": ea.mittlere_wartezeit_s,
                f"SL {sl_zeit_s:g} s": ea.servicelevel or 0.0,
            }
        sim = {
            "P(warten)": (s.p_warten, s.standardfehler["p_warten"]),
            "ASA s": (s.mittlere_wartezeit_s, s.standardfehler["mittlere_wartezeit_s"]),
            "Wartezeit s": (s.mittlere_wartezeit_s, s.standardfehler["mittlere_wartezeit_s"]),
            "P(Abbruch)": (s.p_abbruch, s.standardfehler["p_abbruch"]),
            f"SL {sl_zeit_s:g} s": (s.servicelevel or 0.0, s.standardfehler["servicelevel"]),
        }
        for kennzahl, wert in formel.items():
            sim_wert, se = sim[kennzahl]
            f = _sekunden if kennzahl in ("ASA s", "Wartezeit s") else _prozent
            z.append(
                f"{name:18} {a:>7.3f} {n:>3}  {kennzahl:13} {f(wert):>9} {f(sim_wert):>12} "
                f"{f(se):>8}"
            )
    return "\n".join(z)


def _kapazitaet(argv: Sequence[str]) -> int:
    p = argparse.ArgumentParser(
        prog="gw-contactcenter kapazitaet", description="Personal- und Kanalbedarf (Erlang-C/-B/-A)"
    )
    p.add_argument("--anrufe-pro-tag", type=float, required=True)
    p.add_argument(
        "--spitzenanteil",
        type=float,
        default=0.25,
        help="Anteil des Tagesvolumens in der Spitzenstunde (Annahme, Standard 0.25)",
    )
    p.add_argument("--aht-s", type=float, default=120.0, help="mittlere Bearbeitungszeit in s")
    p.add_argument("--ziel-wartezeit-s", type=float, default=None)
    p.add_argument("--ziel-servicelevel", type=float, default=None, help="z. B. 0.8")
    p.add_argument("--sl-zeit-s", type=float, default=20.0)
    p.add_argument("--ueberlauf", type=float, default=0.01, help="Ziel-Überlauf Sprachagent")
    p.add_argument("--abwesenheit", type=float, default=0.0, help="Shrinkage, z. B. 0.3")
    p.add_argument("--geduld-s", type=float, default=None, help="mittlere Geduld (Erlang-A)")
    p.add_argument("--speicher", action="store_true", help="Speicherbedarf für Aufzeichnungen")
    a = p.parse_args(list(argv))
    ziel_w = a.ziel_wartezeit_s
    if ziel_w is None and a.ziel_servicelevel is None:
        ziel_w = 20.0
    b = berechne(
        Lastannahme(a.anrufe_pro_tag, a.spitzenanteil, a.aht_s),
        ziel_mittlere_wartezeit_s=ziel_w,
        ziel_servicelevel=a.ziel_servicelevel,
        sl_zeit_s=a.sl_zeit_s,
        ziel_ueberlauf=a.ueberlauf,
        abwesenheit=a.abwesenheit,
        geduld_s=a.geduld_s,
    )
    print(b.als_text())
    if a.speicher:
        g = speicherbedarf(a.anrufe_pro_tag, a.aht_s)
        o = speicherbedarf(a.anrufe_pro_tag, a.aht_s, format="opus-32kbit")
        print(
            f"Speicher pro Tag: G.711 Stereo {g.audio_byte_pro_tag / 1e6:.0f} MB, "
            f"Opus 32 kbit/s {o.audio_byte_pro_tag / 1e6:.0f} MB, "
            f"Transkripte ca. {g.text_byte_pro_tag / 1e6:.1f} MB (Annahme 4000 Token x 4 Byte)."
        )
    return 0


def _gpu(argv: Sequence[str]) -> int:
    p = argparse.ArgumentParser(
        prog="gw-contactcenter gpu", description="GPU-Dimensionierung (Obergrenzen, keine Messung)"
    )
    p.add_argument("--modell", choices=sorted(MODELLE), default="qwen3-8b")
    p.add_argument("--gespraeche", type=int, default=10)
    p.add_argument("--kontext", type=int, default=6000)
    p.add_argument("--duty-cycle", type=float, default=0.2)
    p.add_argument("--kv-fp8", action="store_true", help="KV-Cache in FP8 (--kv-cache-dtype fp8)")
    p.add_argument("--effizienz", type=float, default=Bedarf.effizienz, help="Anteil der Roofline")
    p.add_argument("--min-tokens-s", type=float, default=Bedarf.min_tokens_s)
    p.add_argument("--max-ttft-ms", type=float, default=Bedarf.max_ttft_ms)
    p.add_argument("--speicheranteil", type=float, default=Bedarf.gpu_speicheranteil)
    p.add_argument("--preise", default="", help="JSON-Datei {GPU-Name: eigener Preis je Karte}")
    a = p.parse_args(list(argv))
    bedarf = Bedarf(
        gespraeche=a.gespraeche,
        kontext_tokens=a.kontext,
        duty_cycle=a.duty_cycle,
        kv_byte_pro_wert=1 if a.kv_fp8 else 2,
        effizienz=a.effizienz,
        min_tokens_s=a.min_tokens_s,
        max_ttft_ms=a.max_ttft_ms,
        gpu_speicheranteil=a.speicheranteil,
    )
    preise = None
    if a.preise:
        with open(a.preise, encoding="utf-8") as f:
            preise = {str(k): float(v) for k, v in json.load(f).items()}
    print(bericht(a.modell, bedarf, dimensionieren(a.modell, bedarf, preise=preise)))
    return 0


def _sim(argv: Sequence[str]) -> int:
    from .sim import DEMO, anruf, bericht_text

    argparse.ArgumentParser(
        prog="gw-contactcenter sim", description="Simulierter Anruf (Mock-Komponenten)"
    ).parse_args(list(argv))
    print(bericht_text(anruf(DEMO), ohne=anruf(DEMO, zwischenergebnisse=False)))
    return 0


def _vergleich(argv: Sequence[str]) -> int:
    p = argparse.ArgumentParser(
        prog="gw-contactcenter vergleich", description="Erlang-Formeln gegen Simulation"
    )
    p.add_argument("--anrufe", type=int, default=200_000)
    a = p.parse_args(list(argv))
    print(vergleich_text(a.anrufe))
    return 0


def _audit_pruefen(argv: Sequence[str]) -> int:
    p = argparse.ArgumentParser(
        prog="gw-contactcenter audit-pruefen",
        description="Hash-Kette einer Audit-Datei prüfen; Schlüssel aus AUDIT_HMAC_KEY",
    )
    p.add_argument("datei")
    p.add_argument("--unsigniert", action="store_true", help="Datei aus dem Entwicklungsmodus")
    a = p.parse_args(list(argv))
    if not os.path.isfile(a.datei):
        print(f"Datei nicht gefunden: {a.datei}", file=sys.stderr)
        return 2
    schluessel = os.environ.get("AUDIT_HMAC_KEY", "").encode()
    try:
        log = AuditLog(a.datei, schluessel=schluessel or None, unsigniert_erlaubt=a.unsigniert)
    except AuditKetteDefekt as e:
        print(f"DEFEKT: Kette ab Eintrag {e.nummer} verletzt ({a.datei})")
        return 1
    except AuditFehler as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
    print(f"intakt: {len(log.eintraege)} Einträge, letzter Prüfwert {log.letzter_pruefwert}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    befehle = {
        "kapazitaet": _kapazitaet,
        "vergleich": _vergleich,
        "gpu": _gpu,
        "sim": _sim,
        "audit-pruefen": _audit_pruefen,
    }
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if args[0] == "--version":
        print(__version__)
        return 0
    befehl, rest = args[0], args[1:]
    try:
        if befehl in befehle:
            return befehle[befehl](rest)
        if befehl == "latenz-messen":
            from .latenzmessung import main as latenz_main

            return latenz_main(rest)
        if befehl == "server":
            from .server import main as server_main

            return server_main(rest)
    except BrokenPipeError:
        # Ausgabe in eine Pipe, die früh geschlossen wurde (z. B. «| head»): still beenden.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 1
    except (ValueError, OSError) as e:
        # Ungültige Eingaben (z. B. n <= A, Anteil ausserhalb (0, 1], fehlende Datei):
        # Meldung statt Traceback, Rückgabewert 2 wie bei argparse-Fehlern.
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
    print(f"unbekannter Befehl {befehl!r}\n{__doc__}", file=sys.stderr)
    return 2
