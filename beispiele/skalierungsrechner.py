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
    # ACD-Dekfin: 25% der TAGESaufkommens liegt in der GIPFELSTUNDE
    # (nicht 25% des Stundenmittelwerts). Bug-Fix: lambda_min
    # = 200*0.25/60 = 0.833 (nicht 25*0.25/60=0.104).
    calls_in_peak_hour = anrufe_pro_tag * busy_hour_factor
    lambda_min = calls_in_peak_hour / 60.0
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
# 2b. DATEN-SPEICHERUNG (Text + Ton)  --  s. doku/05, 06
# --------------------------------------------------------------------------
# Text: ~10'000 Token ~= ~17 KB (0.0017 MB). 1 Token ~ 0.17 Byte nach
# dieser Groessenschätzung (UTF-8 Deutsch, ~6 char/Token).
def datenaufwand(
    anrufe_pro_tag: int,
    token_pro_gespraech: int = 4000,
    dauer_s_pro_gespraech: float = 120.0,
    sample_rate: int = 16000,
    bits_pro_sample: int = 16,
    kanale: int = 1,
) -> dict:
    """Schätzt den täglichen Speicherbedarf für Text UND Ton.
    (Bug-Fix: der erste Pass zählte nur Text, nicht den Ton.
    Bei 200 Anrufen x 2 Min x 16 kHz/16 bit = ~23 GB/Tag Roh-Ton,
    nicht ~1.3 MB.)"""
    tokens = anrufe_pro_tag * token_pro_gespraech
    text_mb = tokens * 0.0017 / 1024.0  # 10'000 Token ~ 17 KB
    bytes_pro_s = sample_rate * bits_pro_sample * kanale / 8.0
    audio_mb_per_tag = bytes_pro_s * dauer_s_pro_gespraech * anrufe_pro_tag / (1024.0 ** 2)
    audio_gb_24_7 = audio_mb_per_tag / 1024.0 * 30  # 24/7 im Monat
    return {
        "tokens_tag": tokens,
        "text_mb_tag": round(text_mb, 3),
        "audio_mb_tag": round(audio_mb_per_tag, 1),
        "audio_gb_monat_24_7": round(audio_gb_24_7, 1),
        "gesamt_mb_tag": round(text_mb + audio_mb_per_tag, 1),
        "notiz": "Ton dominiert: 16 kHz/16 bit mono, 120 s/Anruf",
    }


# --------------------------------------------------------------------------
# 2c. LATENZ-FEASIBILITY (Voice-Reply)  --  s. doku/05, 11
# --------------------------------------------------------------------------
# Für Voice-Reply muss die End-to-End-Latenz < ~1.5 s (menschliches
# Gespräch). Die 6 Etappen (ASR + LLM-TTFT + TTS + RTT + Buffer) werden
# hier additiv geschätzt. vLLM-Slots bestimmen die LLM-TTFT nur
# indirekt (bei 10 parallel auf 1x A100: Continuous Batching hält TTFT).
def laten_z_pruefen(
    asr_ms: int = 300,
    llm_ttft_ms: int = 200,
    tts_ms: int = 300,
    rtt_ms: int = 25,
    buffer_ms: int = 100,
    ziel_ms: int = 1500,
    prefix_caching: bool = True,
) -> dict:
    """Schätzt den 6-Etappen-End-to-End-Spalten und prüft das Ziel.
    Bei prefix-caching sinkt LLM-TTFT auf ~80 ms."""
    llm = 80 if prefix_caching else llm_ttft_ms
    gesamt = asr_ms + llm + tts_ms + rtt_ms + buffer_ms
    return {
        "geschatzte_zeit_ms": gesamt,
        "ziel_ms": ziel_ms,
        "erfullt": gesamt <= ziel_ms,
        "komponenten": {
            "asr_strengam": asr_ms,
            "llm_ttft_mit_pref": llm,
            "tts": tts_ms,
            "rtt": rtt_ms,
            "buffer": buffer_ms,
        },
        "hinweis": ("LLM-TTFT sinkt mit --enable-prefix-caching von "
                    f"{llm_ttft_ms} auf {llm} ms; ASR/TTS laufen "
                    "parallel nicht in vLLM-Slots (separate Pipeline)"),
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
    data = datenaufwand(
        anrufe_pro_tag, token_pro_gespraech,
        dauer_s_pro_gespraech=mittlere_handhabung_s,
    )
    lat = laten_z_pruefen()
    return {
        "eingang": {
            "anrufe_pro_tag": anrufe_pro_tag,
            "geschaeftsstunden": geschaeftsstunden,
            "mittlere_handhabung_min": round(mittlere_handhabung_s / 60, 1),
        },
        "queueing": q,
        "inferenz": inf,
        "latenz": lat,
        "daten": data,
        "empfehlung": _empfehlung(
            anrufe_pro_tag, parallel_ziel, q, inf, lat
        ),
    }


def _empfehlung(anrufe, parallel, q, inf, lat) -> str:
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
    out.append(
        f"Voice-Reply-Latenz: ~{lat['geschatzte_zeit_ms']} ms "
        f"({'unter' if lat['erfullt'] else 'UEBER'} Ziel {lat['ziel_ms']} ms)")
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
    # busy_hour_factor=0.25 => 50 Anrufe in der Gipfelstunde
    # (200*0.25), A = 50/60 * 2 = 1.67 => 3 Agenten (n>A, Wq 13.5s < 20s)
    assert 1 <= s["agenten_min"] <= 4, s
    assert s["agenten_min"] == 3, s
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

    # --- Bug-Fix-Regressionstests (3 gefundene Fehler) ---
    # (1) busy-hour: 200/Tag * 0.25 = 50 Anrufe in der Gipfelstunde,
    #     nicht 25*0.25=6.25. A = 50/60 * 2 = 1.67, > 1 Agent.
    assert s["angebotslast_erlangs"] > 1.0, f"A muss > 1 (peak-hour-Fix): {s}"
    # (2) Ton-Storage: 200 Anrufe x 120 s x 16 kHz/16 bit ~ 768 MB/Tag
    da = datenaufwand(200)
    assert da["audio_mb_tag"] > 700, f"Ton muss > 700 MB/Tag sein: {da}"
    print(f"  Daten/Tag: {da['audio_mb_tag']} MB Ton + {da['text_mb_tag']} MB Text "
          f"(~{da['audio_gb_monat_24_7']} GB/Monat 24/7)")
    # (3) Voice-Reply-Latenz < 1500 ms Ziel (mit Prefix-Caching)
    lat = laten_z_pruefen()
    assert lat["erfullt"] and lat["geschatzte_zeit_ms"] < 1500, lat
    lat_no = laten_z_pruefen(prefix_caching=False)
    assert lat_no["geschatzte_zeit_ms"] > lat["geschatzte_zeit_ms"], lat_no
    print(f"  Latenz: {lat['geschatzte_zeit_ms']} ms mit/ "
          f"{lat_no['geschatzte_zeit_ms']} ms ohne Prefix-Caching")

    full = szenario(200, 10)
    assert full["inferenz"]["gpus_noetig"] == 1, full
    assert full["daten"]["tokens_tag"] == 800_000, full
    # Ton dominiert den Speicher (Bug-Fix: früher nur Text gezählt)
    assert full["daten"]["audio_mb_tag"] > 700, full
    # Voice-Reply-Latenz: ~880 ms < 1500 ms Ziel (mit Prefix-Caching)
    assert full["latenz"]["erfullt"] is True, full
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
    d = res["daten"]
    print(f"Daten/Tag:    {d['tokens_tag']:,} Token/Tag, "
          f"~{d['text_mb_tag']} MB Text, ~{d['audio_mb_tag']} MB Ton "
          f"(~{d['audio_gb_monat_24_7']} GB/Monat 24/7)")
    lat = res["latenz"]
    print(f"Latenz:       ~{lat['geschatzte_zeit_ms']} ms "
          f"({'unter' if lat['erfullt'] else 'UEBER'} Ziel {lat['ziel_ms']} ms) "
          f"Voice-Reply")
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
