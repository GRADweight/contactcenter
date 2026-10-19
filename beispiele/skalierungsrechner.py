#!/usr/bin/env python3
"""Skalierungsrechner für Voice-/Kundendienst-AI (nur Python-stdlib).

Idee: Die Frage «reicht es für 200 Anrufe am Tag ODER 10 Anrufe zugleich?»
ist NICHT eine reine Durchsatz-Frage. Sie ist eine QUEUEING-Frage, bei der
zwei Dinge unabhängig voneinander überlasten können:

  (1) Die ANRUF-WARTESCHLANGE (ACD-Queue): Wieviele parallele
      Ansprechpartner / Agenten-Slots brauche ich, damit bei dem erwarteten
      Anrufaufkommen niemand länger als X Sekunden wartet? -> Erlang-C.

  (2) Die INFERENZ-KAPAZITÄT (vLLM): Wieviele parallele LLM-Streams
      schafft meine GPU? -> Lookup je Modellkategorie (Slots pro GPU).

Beides muss zusammenpassen. Dieser Rechner rechnet beides aus, sagt,
wie viele GPUs nötig sind, welche vLLM-Parameter sinnvoll sind, und
gibt eine Kosten-Grössenordnung (Stichtag 10/2026, CH).

Nutzung:
  python beispiele/skalierungsrechner.py selftest      # ohne Netz
  python beispiele/skalierungsrechner.py rechner 200 10
     -> 200 Anrufe/Tag, 10 gleichzeitig gewünscht
  python beispiele/skalierungsrechner.py --help

Alle Zahlen sind Grössenordnungen (Stichtag 10/2026), keine Offerte.
GPU-Preise und Modellgrössen wandern schneller als eine Doku.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass


# --------------------------------------------------------------------------
# 1. QUEUEING: Erlang-C  (M/M/n, Ankünfte Poisson, Service exponential)
# --------------------------------------------------------------------------
# A = Angebotslast in Erlangs = lambda * h
#   lambda = Ankunftsrate (Ankünfte pro Zeiteinheit, z. B. pro Minute)
#   h      = mittlere Handhabungszeit pro Anruf (in derselben Zeiteinheit)
# C(n, A) = Wahrscheinlichkeit, dass ein Anruf WARTEN muss
# Wq      = mittlere Wartezeit in der Warteschlange (ohne Service)


def erlang_C(n: int, A: float) -> float:
    """Wahrscheinlichkeit, dass ein Anruf warten muss (M/M/n).

    Robust gegen Überlauf: die Summe läuft in Logarithmen.
    Braucht n > A, sonst ist das System unbedient (rho >= 1).
    """
    if n <= A:
        # rho = A/n >= 1 -> Warteschlange wächst, keine stabile Lösung
        return 1.0
    log_terms = []
    for k in range(n):
        log_terms.append(k * math.log(A + 1e-12) - math.lgamma(k + 1))
    m = max(log_terms)
    sum_k = math.exp(m) * sum(math.exp(t - m) for t in log_terms)
    last = math.exp(n * math.log(A + 1e-12) - math.lgamma(n + 1))
    numer = last * (n / (n - A))
    return numer / (sum_k + numer)


def minimal_agenten(
    anrufe_pro_tag: int,
    geschaeftsstunden: float,
    mittlere_handhabung_s: float,
    busy_hour_factor: float = 0.25,
    max_wartezeit_s: float = 20.0,
) -> dict:
    """Minimale Anzahl paralleler Ansprechpartner, so dass die mittlere
    Wartezeit in der GIPFELSTUNDE unter max_wartezeit_s bleibt.

    busy_hour_factor = Anteil des Tagesaufkommens in der geschäftigsten
    Stunde (25 % ist der übliche ACD-Standard, s. doku/04-skalierung.md).
    """
    lambda_h = anrufe_pro_tag / geschaeftsstunden
    lambda_min = lambda_h * busy_hour_factor / 60.0
    A = lambda_min * (mittlere_handhabung_s / 60.0)
    for n in range(1, 200):
        if n <= A:
            continue
        C = erlang_C(n, A)
        wq_min = C / (n - A)
        wq_s = wq_min * 60.0
        if wq_s <= max_wartezeit_s:
            return {
                "angebotslast_erlangs": round(A, 3),
                "ankunfte_pro_stunde": round(lambda_h, 1),
                "agenten_min": n,
                "wartewahrscheinlichkeit_C": round(C, 3),
                "mittlere_wartezeit_s": round(wq_s, 1),
                "auslastung_rho": round(A / n, 3),
            }
    return {"agenten_min": 199, "war": "keine stabile Lösung beim Ziel"}


# --------------------------------------------------------------------------
# 2. INFERENZ: vLLM-Slots pro GPU (Referenzwerte, Stichtag 10/2026)
# --------------------------------------------------------------------------
# "Slots" = maximal gleichzeitig bearbeitete Dekodier-Streams, bei denen
# vLLM die Ziel-TTFT (first token) und -TPS (Tokens/Sekunde) einhält.
# Werte sind Grössenordnungen aus vLLM-Erfahrung (Continuous Batching),
# NICHT Garantien. Kontextlänge und Modellklasse bestimmen den Wert.


@dataclass
class GPUKategorie:
    name: str
    slots_7b: int
    slots_30b: int
    slots_70b: int
    kost_chf_24_7_monat: int
    notiz: str


GPUS = {
    "A100-40": GPUKategorie("A100 40 GB", slots_7b=180, slots_30b=25,
                            slots_70b=0, kost_chf_24_7_monat=1600,
                            notiz="ein gängiger Alltags-GPU im CH-Miete-Markt"),
    "A100-80": GPUKategorie("A100 80 GB", slots_7b=250, slots_30b=55,
                            slots_70b=18, kost_chf_24_7_monat=2600,
                            notiz="viel Spielraum, 70B möglich mit Quantisierung"),
    "H100-80": GPUKategorie("H100 80 GB", slots_7b=400, slots_30b=100,
                            slots_70b=30, kost_chf_24_7_monat=3500,
                            notiz="aktueller High-Throughput-Standard"),
    "L4-24": GPUKategorie("L4 24 GB", slots_7b=120, slots_30b=12,
                            slots_70b=0, kost_chf_24_7_monat=900,
                            notiz="klein, günstig, für 7-14B mit Quantisierung gut"),
    "A10-24": GPUKategorie("A10 24 GB", slots_7b=90, slots_30b=10,
                            slots_70b=0, kost_chf_24_7_monat=700,
                            notiz="kleinste brauchbare NVIDIA-GPU für 7B"),
}


def inferenz_sizing(
    parallele_streams: int,
    modell_klasse: str = "7b",
    gpu: str = "A100-80",
) -> dict:
    g = GPUS[gpu]
    slots = {"7b": g.slots_7b, "30b": g.slots_30b, "70b": g.slots_70b}[modell_klasse]
    if slots < 1:
        return {"gpus_noetig": -1, "war": f"{modell_klasse} passt NICHT auf {gpu}"}
    return {
        "gpu": gpu,
        "modell_klasse": modell_klasse,
        "slots_pro_gpu": slots,
        "gpus_noetig": math.ceil(parallele_streams / slots),
        "vllm_max_num_seqs": min(512, max(1, int(slots))),
        "gpu_kost_chf_monat": g.kost_chf_24_7_monat * math.ceil(parallele_streams / slots),
        "notiz": f"{slots} Slots pro {g.name} ({g.notiz})",
    }


# --------------------------------------------------------------------------
# 3. Gesamt-Szenario: 200 Anrufe/Tag, 10 zugleich
# --------------------------------------------------------------------------


def szenario(
    anrufe_pro_tag: int,
    parallele_streams_ziel: int,
    geschaeftsstunden: float = 8.0,
    mittlere_handhabung_s: float = 120.0,
    token_pro_gespraech: int = 4000,
    max_wartezeit_s: float = 20.0,
    modell_klasse: str = "7b",
    gpu: str = "A100-80",
) -> dict:
    q = minimal_agenten(
        anrufe_pro_tag, geschaeftsstunden,
        mittlere_handhabung_s, max_wartezeit_s=max_wartezeit_s,
    )
    parallel_ziel = max(q["agenten_min"], parallele_streams_ziel)
    inf = inferenz_sizing(parallel_ziel, modell_klasse, gpu)
    tokens_pro_tag = anrufe_pro_tag * token_pro_gespraech
    return {
        "eingang": {
            "anrufe_pro_tag": anrufe_pro_tag,
            "geschaeftsstunden": geschaeftsstunden,
            "mittlere_handhabung_min": round(mittlere_handhabung_s / 60, 1),
        },
        "queueing": q,
        "inferenz": inf,
        "daten_pro_tag": {
            "tokens_gesamt": tokens_pro_tag,
            "transkripte_megabytes_groesse": round(tokens_pro_tag * 0.0017 / 1024, 3),
        },
        "empfehlung": _empfehlung(
            anrufe_pro_tag, parallel_ziel, q, inf
        ),
    }


def _empfehlung(anrufe, parallel, q, inf) -> str:
    out = []
    if q["agenten_min"] <= parallel:
        out.append(
            f"Die anrufseitige Warteschlange erfordert {q['agenten_min']} parallele "
            f"Agenten (Erlang-C), und Sie wünschen {parallel} gleichzeitig. Da {parallel} "
            f"mehr ist, bestimmt die INFERENZ die Kapazität.")
    else:
        out.append(
            f"Die Warteschlange erfordert {q['agenten_min']} Agenten, was mehr ist als "
            f"{parallel} - dort bestimmt die ANRUF-SEITE den Bedarf.")
    if inf.get("war"):
        out.append(f"MODELL NICHT PASSEND: {inf['war']} - kleineres Modell oder grössere GPU.")
    else:
        out.append(
            f"Notwendig sind {inf['gpus_noetig']}x {inf['gpu']} für bis zu "
            f"{inf['slots_pro_gpu']} Slots/GPU (vLLM --max-num-seqs {inf['vllm_max_num_seqs']}).")
    return " ".join(out)


# --------------------------------------------------------------------------
# 4. Selbsttest (deterministisch, kein Netz)
# --------------------------------------------------------------------------


def selftest() -> int:
    print("== selftest: skalierungsrechner ==")

    C = erlang_C(10, 8.0)
    assert 0.0 < C < 0.6, f"Erlang-C unerwartet: {C}"
    assert erlang_C(8, 8.0) == 1.0, "rho=1 muss 1.0 sein (instabil)"
    print(f"  Erlang-C(10, 8.0) = {C:.3f} (ok)")

    s = minimal_agenten(200, 8.0, 120.0, max_wartezeit_s=20.0)
    assert 1 <= s["agenten_min"] <= 6, s
    print(f"  200 Anr/Tag -> {s['agenten_min']} Agenten, "
          f"Wq {s['mittlere_wartezeit_s']:.1f} s, rho={s['auslastung_rho']}")

    i = inferenz_sizing(10, "7b", "A100-80")
    assert i["gpus_noetig"] == 1, i
    assert i["vllm_max_num_seqs"] >= 10, i
    print(f"  10 x 7B auf A100-80 -> {i['gpus_noetig']} GPU, "
          f"--max-num-seqs={i['vllm_max_num_seqs']} "
          f"({i['gpu_kost_chf_monat']} CHF/Mo)")

    i70 = inferenz_sizing(10, "70b", "A100-80")
    assert i70["gpus_noetig"] == 1, i70
    print(f"  10 x 70B (AWQ) auf A100-80 -> {i70['gpus_noetig']} GPU")

    i_bad = inferenz_sizing(10, "70b", "A10-24")
    assert i_bad["gpus_noetig"] == -1, i_bad
    print(f"  10 x 70B auf A10-24 -> {i_bad['war']}")

    full = szenario(200, 10)
    assert full["inferenz"]["gpus_noetig"] == 1, full
    assert full["daten_pro_tag"]["tokens_gesamt"] == 800_000, full
    print(f"  Szenario: {full['empfehlung']}")

    print("== selftest PASSED ==")
    return 0


def rechner(args) -> int:
    anrufe = int(args[0]) if len(args) > 0 and args[0].isdigit() else 200
    parallel = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
    res = szenario(anrufe, parallel)
    print("== Skalierungsergebnis (Grössenordnung, 10/2026) ==")
    print(f"Eingang:      {res['eingang']['anrufe_pro_tag']} Anrufe/Tag, "
          f"{res['eingang']['geschaeftsstunden']} GS-Stunden, "
          f"Ø {res['eingang']['mittlere_handhabung_min']} min pro Anruf")
    print(f"Anruf-Queue:  {res['queueing']['agenten_min']} Agenten, "
          f"Wq {res['queueing']['mittlere_wartezeit_s']} s, "
          f"rho={res['queueing']['auslastung_rho']}")
    i = res["inferenz"]
    print(f"Inferenz:     {i.get('gpus_noetig','?')}x {i.get('gpu',i.get('war','?'))}, "
          f"vLLM --max-num-seqs {i.get('vllm_max_num_seqs', '-')}, "
          f"~{i.get('gpu_kost_chf_monat','?')} CHF/Mo")
    print(f"Daten/Tag:    {res['daten_pro_tag']['tokens_gesamt']:,} Token/Tag, "
          f"~{res['daten_pro_tag']['transkripte_megabytes_groesse']} MB Transkript/Tag")
    print(f"Empfehlung:   {res['empfehlung']}")
    return 0


if __name__ == "__main__":
    if "selftest" in sys.argv:
        sys.exit(selftest())
    if "rechner" in sys.argv:
        args = sys.argv[sys.argv.index("rechner") + 1:]
        sys.exit(rechner(args))
    print(__doc__)
    sys.exit(0)
