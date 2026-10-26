"""GPU-Dimensionierung aus Physik statt aus Daumenwerten.

Ein Sprachmodell auf einer GPU braucht Speicher für zwei Dinge:

1. **Gewichte**: Parameter x Bytes pro Parameter
   (BF16 = 2 Byte, FP8 = 1 Byte, INT4/AWQ ~ 0.55 Byte inkl. Skalen)
2. **KV-Cache**: das "Kurzzeitgedächtnis" jedes laufenden Gesprächs.
   Pro Token: 2 (K und V) x Schichten x KV-Köpfe x Kopfdimension x Bytes

Und es ist beim Erzeugen fast immer **speicherbandbreiten-gebunden**:
Für jedes neue Token müssen alle Gewichte einmal aus dem Speicher
gelesen werden. Obergrenze für einen einzelnen Strom:

    Tokens/s  ~  Bandbreite / Gewichtsgrösse  x  Effizienz

Zwei Einsichten, die der alte Daumenwert-Rechner nicht hatte:

* **10 gleichzeitige Anrufe != 10 gleichzeitige LLM-Anfragen.** Ein
  Bot spricht, hört zu, wartet. Das LLM arbeitet nur 10-25 % der
  Gesprächszeit (Duty Cycle). Speicher (KV-Cache) braucht man aber für
  alle Gespräche, wenn ihre Kontexte im Prefix-Cache bleiben sollen.
* **Die günstigste passende Karte gewinnt.** Für ein 8-B-Modell und
  10 Gespräche ist eine A100 80 GB massiv überdimensioniert; eine L4
  oder L40S (oder ein Mac Studio) reicht.

Alle Preise sind Grössenordnungen (Stichtag 10/2026, CHF/Monat, 24/7
Miete in CH bzw. Abschreibung über 36 Monate), keine Offerte.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Modell:
    name: str
    params_mrd: float       # Milliarden Parameter
    schichten: int
    kv_koepfe: int
    kopf_dim: int


# Architekturwerte aus den öffentlichen Modellkarten (config.json)
MODELLE: Dict[str, Modell] = {m.name: m for m in [
    Modell("qwen3-1.7b", 1.7, 28, 8, 128),
    Modell("llama-3.1-8b", 8.0, 32, 8, 128),
    Modell("qwen3-8b", 8.2, 36, 8, 128),
    Modell("mistral-small-24b", 24.0, 40, 8, 128),
    Modell("qwen3-32b", 32.8, 64, 8, 128),
    Modell("llama-3.3-70b", 70.6, 80, 8, 128),
]}

BYTES_PRO_PARAM = {"bf16": 2.0, "fp8": 1.0, "int4": 0.55}


@dataclass(frozen=True)
class GPU:
    name: str
    speicher_gb: float
    bandbreite_gbs: float
    tflops_bf16: float      # dichte Tensor-Leistung, Richtwert aus Datenblatt
    fp8: bool               # native FP8-Rechenwerke (Ada, Hopper, Blackwell)
    chf_monat: int
    notiz: str
    max_karten: int = 8     # Tensor-Parallel über mehrere Karten im selben Server
    produktion: bool = True


GPUS: Dict[str, GPU] = {g.name: g for g in [
    GPU("Mac-M4-Max-128", 96, 546, 34, False, 200,
        "Kauf ~5'000 CHF / 36 Mt; MLX oder llama.cpp, für PoC und bis ~4 Gespräche",
        max_karten=1, produktion=False),
    GPU("L4-24", 24, 300, 121, True, 700, "sparsam (72 W), passt in jeden Server"),
    GPU("L40S-48", 48, 864, 362, True, 1400, "Allrounder für 8-32 B, ASR/TTS passen daneben"),
    GPU("RTX-PRO-6000-96", 96, 1790, 500, True, 1900, "Blackwell, viel Speicher pro Karte"),
    GPU("A100-80", 80, 2039, 312, False, 2600, "Ampere, kein natives FP8"),
    GPU("H100-80", 80, 3350, 989, True, 3500, "für grosse Modelle oder viele Anrufe"),
]}


def kv_bytes_pro_token(m: Modell, kv_bytes: float = 2.0) -> float:
    return 2 * m.schichten * m.kv_koepfe * m.kopf_dim * kv_bytes


@dataclass
class Bedarf:
    gleichzeitige_anrufe: int = 10
    kontext_tokens: int = 6000      # System + Wissen + Verlauf pro Gespräch
    duty_cycle: float = 0.2         # Anteil Gesprächszeit, in der das LLM rechnet
    min_tokens_s: float = 25.0      # pro Strom: 1. Satz (~12 Token) in < 0.5 s
    neue_tokens_pro_wechsel: int = 300   # bei Prefix-Cache-Treffer neu zu rechnen
    max_ttft_ms: float = 300.0      # Zeit bis erstes Token bei Cache-Treffer
    redundanz: bool = False         # N+1: eine Karte darf ausfallen


@dataclass
class Ergebnis:
    gpu: str
    anzahl: int
    praezision: str
    gewichte_gb: float
    kv_gb: float
    belegt_gb: float
    tokens_s_pro_strom: float
    ttft_ms_cache: float
    ttft_ms_ohne_cache: float
    llm_parallel: int
    chf_monat: int
    begruendung: str


def _ttft_ms(m: Modell, g: GPU, n: int, prae: str, tokens: int, eff: float = 0.4) -> float:
    """Prefill ist rechengebunden: ~2 x Parameter x Tokens FLOP."""
    tflops = g.tflops_bf16 * (2 if prae == "fp8" and g.fp8 else 1) * n
    return 2 * m.params_mrd * 1e9 * tokens / (tflops * 1e12 * eff) * 1000


def dimensionieren(modell: str, bedarf: Bedarf, praezision: Optional[str] = None,
                   effizienz: float = 0.6, reserve: float = 0.10,
                   nur_produktion: bool = True) -> List[Ergebnis]:
    """Alle passenden (GPU, Anzahl, Präzision), günstigste zuerst."""
    m = MODELLE[modell]
    out: List[Ergebnis] = []
    for g in GPUS.values():
        if nur_produktion and not g.produktion:
            continue
        for prae in ([praezision] if praezision else ["fp8", "int4", "bf16"]):
            if prae == "fp8" and not g.fp8:
                continue        # FP8 ohne Hardware-Support: langsam, nicht empfohlen
            gew_gb = m.params_mrd * BYTES_PRO_PARAM[prae]
            kv_b = 1.0 if prae == "fp8" else 2.0
            kv_gb = (kv_bytes_pro_token(m, kv_b) * bedarf.kontext_tokens
                     * bedarf.gleichzeitige_anrufe) / 1e9
            parallel = max(1, math.ceil(bedarf.gleichzeitige_anrufe * bedarf.duty_cycle))
            for n in range(1, g.max_karten + 1):
                nutzbar = n * g.speicher_gb * (1 - reserve) - 1.5 * n   # CUDA-Kontext
                if gew_gb + kv_gb > nutzbar:
                    continue
                # Tensor-Parallel über n Karten: Bandbreite addiert sich (ideal)
                tps = n * g.bandbreite_gbs / gew_gb * effizienz
                # Batching teilt die Bandbreite nicht linear; konservativ ~ 1/sqrt(parallel)
                tps_strom = tps / math.sqrt(parallel)
                if tps_strom < bedarf.min_tokens_s:
                    continue
                ttft = _ttft_ms(m, g, n, prae, bedarf.neue_tokens_pro_wechsel)
                if ttft > bedarf.max_ttft_ms:
                    continue
                ttft_kalt = _ttft_ms(m, g, n, prae, bedarf.kontext_tokens)
                anzahl = n + (1 if bedarf.redundanz else 0)
                out.append(Ergebnis(
                    g.name, anzahl, prae, round(gew_gb, 1), round(kv_gb, 1),
                    round(gew_gb + kv_gb, 1), round(tps_strom, 1), round(ttft),
                    round(ttft_kalt), parallel,
                    anzahl * g.chf_monat,
                    f"{m.name} {prae}: {gew_gb:.1f} GB Gewichte + {kv_gb:.1f} GB KV "
                    f"({bedarf.gleichzeitige_anrufe} Gespräche x {bedarf.kontext_tokens} Token) "
                    f"auf {n}x {g.name}; ~{tps_strom:.0f} Token/s pro Strom bei "
                    f"{parallel} parallelen LLM-Anfragen; erstes Token ~{ttft:.0f} ms mit "
                    f"Prefix-Cache, ~{ttft_kalt:.0f} ms ohne. {g.notiz}."))
                break
    out.sort(key=lambda e: (e.chf_monat, -e.tokens_s_pro_strom))
    return out


def empfehlung(modell: str = "qwen3-8b", nur_produktion: bool = True, **bedarf) -> Ergebnis:
    r = dimensionieren(modell, Bedarf(**bedarf), nur_produktion=nur_produktion)
    if not r:
        raise ValueError(f"{modell} passt mit diesem Bedarf auf keine Karte im Katalog")
    return r[0]


def main(argv: List[str]) -> int:
    pos = [a for a in argv if not a.startswith("--")]
    modell = pos[0] if pos else "qwen3-8b"
    anrufe = int(pos[1]) if len(pos) > 1 else 10
    print(f"== GPU-Dimensionierung: {modell}, {anrufe} gleichzeitige Anrufe ==")
    poc = "--poc" in argv
    for e in dimensionieren(modell, Bedarf(gleichzeitige_anrufe=anrufe), nur_produktion=not poc)[:6]:
        print(f"  {e.anzahl}x {e.gpu:16} {e.praezision:5} {e.belegt_gb:6.1f} GB  "
              f"{e.tokens_s_pro_strom:6.1f} Tok/s/Strom  TTFT {e.ttft_ms_cache:>4.0f}/"
              f"{e.ttft_ms_ohne_cache:>5.0f} ms  ~{e.chf_monat:>6} CHF/Mo")
    print("  (TTFT = erstes Token mit / ohne Prefix-Cache-Treffer)")
    e = empfehlung(modell, nur_produktion=not poc, gleichzeitige_anrufe=anrufe)
    print(f"\nEmpfehlung: {e.begruendung}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
