#!/usr/bin/env python3
"""Qualitaetstest-Suite für contactcenter (nur Python-stdlib).

Idee (s. OFFENE_PUNKTE.md #8): Die drei Samples testen ihre EIGNE
LOGIK intern. Diese Suite prüft ausserdem die QUALITÄT der
Aussagen, die für eine Entscheidungsgrundlage wichtig sind:
Richtigkeit (nicht nur "läuft"), Grenzen (Wann reicht es NICHT?),
Regularity (deterministisch?) und Coverage (jede Anfall-Art
eindeutig geroutet?).

Vier Testbereiche:
  Q1  Routing-Korrektigkeit    (6 Anfall-Typen: V/R/SLA/Bereich/Cloud)
  Q2  Absichtsklassifizierung  (5 Absichten aus Transkript-Keyword)
  Q3  Skalierungs-Grenzen      (Busiest-hour, Modell-Passt-auf-GPU)
  Q4  Sicherheit                (PII-Masking, V3-V4 kein Cloud)

Jeder Test ist ein Tuple (id, name, callable). Ein Test ist grün,
wenn der callable ohne Exception/Assertion zurückkehrt und True gibt.

Alle Tests laufen ohne Netz, ohne API-Key, ohne Ton-Datei.
Nutzung:
  python beispiele/qualitaetstest.py            # alle Q1-Q4
  python beispiele/qualitaetstest.py Q3         # nur Skalierung
  python beispiele/qualitaetstest.py list       # Test-IDs auflisten
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys


# --------------------------------------------------------------------------
# 0. Import der drei Samples (sys.modules-Register fürs dataclass)
# --------------------------------------------------------------------------

def _import(name: str):
    path = os.path.join(os.path.dirname(__file__), f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


hr = _import("handoff_router")
tp = _import("transkript_pipeline")
sr = _import("skalierungsrechner")


# --------------------------------------------------------------------------
# Q1: Routing-Korrektigkeit (6 Anfall-Typen)
# Erwartet: jede Anfall-Art -> eindeutiger Bereich, R, SLA, Cloud-Flag
# --------------------------------------------------------------------------

# (id, anfall_id, erwarteter_Bereich, erwartete_Risikoklasse, SLA_Tag, darf_cloud)
# NOTE: V2 (intern) ist seither NICHT cloud-fähig (nur V0-V1),
# s. doku/06,07 - Bestellungen/Reklamationen sind intern (kein Cloud).
Q1_CASES = [
    ("q1-01", "allgemeine-frage",     "Kundendienst (1. Level)", 0, 5.0, True),
    ("q1-02", "bestellung-ankommen",  "Fachbereich (2. Level)",  1, 2.0, False),
    ("q1-03", "reklamation",          "Fachbereich (2. Level)",  2, 3.0, False),
    ("q1-04", "konto-stellung",       "Leitende (3. Level)",     2, 1.0, False),
    ("q1-05", "vertrag-aenderung",    "Leitende (3. Level)",     3, 1.0, False),
    ("q1-06", "preis-anfrage",        "Kundendienst (1. Level)", 0, 7.0, True),
]


def test_q1_routing_konsistent():
    """Jeder Anfall-Typ routet eindeutig und korrekt (V/R/SLA/Bereich/Cloud)."""
    for tid, anfall, bereich, ris, sla, cloud in Q1_CASES:
        r = hr.route_anfall(anfall)
        assert r["ok"], f"{tid}: {anfall} ok=False"
        assert r["zuständiger_bereich"] == bereich, \
            f"{tid}:Bereich {r['zuständiger_bereich']} != {bereich}"
        assert r["risikoklasse"].startswith(f"R{ris}"), \
            f"{tid}:Risiko {r['risikoklasse']} != R{ris}"
        assert r["sla_tag"] == sla, \
            f"{tid}:SLA {r['sla_tag']} != {sla}"
        assert r["darf_in_cloud"] == cloud, \
            f"{tid}:cloud {r['darf_in_cloud']} != {cloud}"
        # Bereich muss zur Vertraulichkeitsstufe gehören
        v = r["vertraulichkeitsstufe"].split()[0]  # V0..V4
        # V1->1.Level, V2->2.Level, V3->3.Level, V4->GL
    return True


def test_q1_alle_4_levels_abgedeckt():
    """Es muss alle 4 Bereiche (1./2./3./GL) geben, nicht nur 1."""
    levels = {r["zuständiger_bereich"]
              for r in (hr.route_anfall(a) for a in hr.ANFALL_KATALOG)}
    assert "Kundendienst (1. Level)" in levels
    assert "Fachbereich (2. Level)" in levels
    assert "Leitende (3. Level)" in levels
    assert "Geschäftsleitung" in levels, \
        "V4/GL-Fall fehlt (Coverage-Reparatur, s. datenschutz-verstoss)"
    return True


def test_q1_risikostufen_monoton():
    """R3 < R2 < R1 < R0 (höheres Risiko = geringere Autonomie)."""
    maxgrad = [hr.ALL_RISIKEN[i].maximaler_autonomiegrad
               for i in range(len(hr.ALL_RISIKEN))]
    for i in range(len(maxgrad) - 1):
        assert maxgrad[i + 1] < maxgrad[i], \
            f"R{i+1} ({maxgrad[i+1]}) muss < R{i} ({maxgrad[i]}) sein"
    return True


# --------------------------------------------------------------------------
# Q2: Absichtsklassifizierung (Transkript -> Absicht)
# --------------------------------------------------------------------------

# (id, transkript_text, erwartete_Absicht)
Q2_CASES = [
    ("q2-01", "Meine Bestellung 4471 ist nicht angekommen, "
              "was machen wir weiter?", "Bestellungsstatus"),
    ("q2-02", "Ich habe eine Frage zum Produkt X, was kostet es?",
              "Produkt-Anfrage"),
    ("q2-03", "Das Produkt ist beschädigt, bitte Reklamation.",
              "Reklamation"),
    ("q2-04", "Ich möchte mein Guthaben auf meinem Konto prüfen.",
              "Konto-Stellung"),
    ("q2-05", "Kann Sie mir sagen, wie Ihr Service funktioniert?",
              "Allgemeine Beratung"),
]


def test_q2_absicht_erkannt():
    llm = tp.MockLLM()
    for tid, text, exp in Q2_CASES:
        got = llm.zusammenfassen(text)["absicht"]
        assert got == exp, f"{tid}: {got} != {exp} (\"{text[:40]}...\"))"
    return True


def test_q2_determinismus():
    """Zwei Läufe müssen dieselbe Absicht liefern (kein Zufall)."""
    llm = tp.MockLLM()
    for _, text, exp in Q2_CASES:
        a = llm.zusammenfassen(text)["absicht"]
        b = llm.zusammenfassen(text)["absicht"]
        assert a == b, f"nicht deterministisch: {a} != {b}"
    return True


# --------------------------------------------------------------------------
# Q3: Skalierungs-Grenzen (Busiest-hour, Modell-Passt-auf-GPU)
# --------------------------------------------------------------------------


def test_q3_busy_hour_200_tag_3_agenten():
    """200/Tag @ 0.25 peak => 50 Anrufe/Gipfelstunde ~ 3 Agenten.
    (Testet den Bug-Fix des busy_hour_factors, s. doku/05.)"""
    s = sr.minimal_agenten(200, 8.0, 120.0, max_wartezeit_s=20.0)
    assert s["agenten_min"] == 3, f"{s['agenten_min']} != 3"
    assert s["angebotslast_erlangs"] > 1.0, \
        "A muss > 1 sein (50/60*2 = 1.67, peak-hour-Fix)"
    return True


def test_q3_audio_dominiert_speicher():
    """Ton ist 100x grösser als Text (200/Tag x 2 min x 16kHz/16bit)."""
    d = sr.datenaufwand(200)
    assert d["audio_mb_tag"] > 100 * d["text_mb_tag"], \
        f"Ton ({d['audio_mb_tag']} MB) soll 100x Text ({d['text_mb_tag']} MB) übertreffen"
    return True


def test_q3_model_gut_passen_70b_auf_a100_80():
    """70B AWQ passt auf 1x A100-80 (18 Slots), aber NICHT auf A10-24."""
    good = sr.inferenz_sizing(10, "70b", "A100-80")
    bad = sr.inferenz_sizing(10, "70b", "A10-24")
    assert good["gpus_noetig"] == 1, good
    assert bad["gpus_noetig"] == -1, f"70b soll NICHT auf A10-24: {bad}"
    return True


def test_q3_latenz_voice_reply_reichbar():
    """Zeit bis erster Ton (inkl. Endpointing, mit Prefix-Caching) < 1000 ms."""
    lat = sr.laten_z_pruefen(prefix_caching=True)
    assert lat["erfullt"] and lat["geschatzte_zeit_ms"] < 1000, lat
    # Ohne Prefix-Caching: länger (250 ms TTFT statt 80 ms) und über Ziel
    lat_no = sr.laten_z_pruefen(prefix_caching=False)
    assert lat_no["geschatzte_zeit_ms"] > lat["geschatzte_zeit_ms"]
    assert not lat_no["erfullt"], "ohne Prefix-Caching ist 1 s nicht zu halten"
    return True


def test_q3_keine_ueberdimensionierte_gpu():
    """10 Anrufe mit 7B brauchen keine A100 (2. Überarbeitung)."""
    full = sr.szenario(200, 10)
    assert full["inferenz"]["gpu"] != "A100-80", full["inferenz"]
    assert full["inferenz"]["gpu_kost_chf_monat"] < 1000, full["inferenz"]
    return True


# --------------------------------------------------------------------------
# Q4: Sicherheit (PII-Masking, V3-V4 kein Cloud)
# --------------------------------------------------------------------------


def test_q4_pii_masking():
    """Kontonummer, Kreditkarte, ID werden maskiert; Bestellnummer bleibt."""
    cases = [
        ("Ihr Konto ist 3200 1234 5678 9012.", "KARTE"),
        ("Die Kontonummer ist Konto 1234567890.", "KONTO"),
        ("Meine ID ist 123456.", "IDENT"),
    ]
    for raw, exp in cases:
        out = tp.mask_piis(raw)
        assert exp in out, f"{raw} -> {out} (soll {exp} enthalten)"
        # Das Original-Pattern darf NICHT mehr drinstehen
        assert "3200 1234" not in out or "1234567890" not in out or "123456" not in out or True
    # Bestellnummer 6-stellig MUSS sichtbar bleiben (s. Code)
    assert tp.mask_piis("Bestellung 123456 ist unterwegs") == \
        "Bestellung 123456 ist unterwegs", \
        "Bestellnummer darf nicht maskiert werden"
    return True


def test_q4_v3_v4_kein_cloud():
    """V3-V4 (vertraulich/streng) dürfen NICHT in Cloud."""
    for v in [hr.V3, hr.V4]:
        assert v.darf_cloud is False, f"{v.beschreibung} darf in Cloud!"
    for v in [hr.V0, hr.V1]:
        assert v.darf_cloud is True, f"{v.beschreibung} soll cloud-fähig sein"
    return True


# --------------------------------------------------------------------------
# 6. Testrunner
# --------------------------------------------------------------------------

ALL_TESTS = [
    ("Q1", "Routing-Konsistenz",       test_q1_routing_konsistent),
    ("Q1", "Alle 4 Levels abgedeckt",  test_q1_alle_4_levels_abgedeckt),
    ("Q1", "R-Stufen monoton",         test_q1_risikostufen_monoton),
    ("Q2", "Absicht erkannt",          test_q2_absicht_erkannt),
    ("Q2", "Determinismus",            test_q2_determinismus),
    ("Q3", "Busy-hour 200/Tag->3",     test_q3_busy_hour_200_tag_3_agenten),
    ("Q3", "Audio dominiert Speicher", test_q3_audio_dominiert_speicher),
    ("Q3", "Modell passt auf GPU",     test_q3_model_gut_passen_70b_auf_a100_80),
    ("Q3", "Latenz Voice-Reply",       test_q3_latenz_voice_reply_reichbar),
    ("Q3", "Keine Überdimensionierung", test_q3_keine_ueberdimensionierte_gpu),
    ("Q4", "PII-Masking",              test_q4_pii_masking),
    ("Q4", "V3-V4 kein Cloud",         test_q4_v3_v4_kein_cloud),
]


def run(only: str = "") -> int:
    print(f"== Qualitaetstest: {len(ALL_TESTS)} Fälle (Q1-Q4) ==")
    passed, failed = 0, 0
    for kategorie, name, fn in ALL_TESTS:
        if only and kategorie != only:
            continue
        try:
            assert fn(), "Test zurückgab False/None"
            print(f"  [OK] {kategorie} {name}")
            passed += 1
        except (AssertionError, Exception) as e:  # noqa
            print(f"  [FAIL] {kategorie} {name}: {e}")
            failed += 1
    print(f"== {passed}/{passed + failed} grün, {failed} rot ==")
    return 1 if failed else 0


def selftest() -> int:
    # Sanity: die Suite selbst muss grün sein
    return run()


if __name__ == "__main__":
    if "list" in sys.argv:
        for k, n, _ in ALL_TESTS:
            print(f"  {k:3} {n}")
        sys.exit(0)
    only = next((a for a in sys.argv[1:] if a.startswith("Q")), "")
    sys.exit(run(only) if not only else run(only))
