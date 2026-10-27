"""GPU-Dimensionierung aus Modellarchitektur und Datenblattwerten.

Was gerechnet wird (alle Formeln stehen im Code, alle Eingaben haben eine Quelle
oder sind als Annahme gekennzeichnet):

1. **Parameter** aus der Architektur (``config.json`` bzw. Modellbericht):
   pro Schicht Q-, K-, V-, O-Projektion und SwiGLU-MLP (3 Matrizen), dazu
   Einbettung und LM-Kopf. Normgewichte (< 0.01 %) werden nicht gezählt.
2. **Gewichtsspeicher** je Präzision: BF16 = 2 Byte; FP8 = 1 Byte für lineare
   Schichten; INT4 (AWQ/GPTQ, Gruppengrösse 128) = 0.5 Byte + 2.5 Byte pro
   128er-Gruppe (FP16-Skala, 4-Bit-Nullpunkt). *Annahme:* Einbettung und
   LM-Kopf bleiben bei FP8/INT4 in 16 Bit, wie in gängigen Checkpoints.
3. **KV-Cache** pro Token: ``2 (K und V) × Schichten × KV-Köpfe × Kopfdimension
   × Byte pro Wert``; 2 Byte (Standard) oder 1 Byte mit ``--kv-cache-dtype fp8``.
4. **Dekodier-Obergrenze** (Roofline): Pro Dekodierschritt liest die GPU alle
   Gewichte einmal und den KV-Cache jeder aktiven Anfrage. Tokens/s pro
   Strom ≤ Bandbreite / (Gewichte + aktive Anfragen × Kontext × KV/Token).
   Rechenleistung, Kernel-Overhead und Kommunikation bleiben unberücksichtigt;
   es ist eine **Obergrenze**, keine Messung.
5. **Prefill-Schätzung** (Zeit bis zum ersten Token): 2 × Parameter × Tokens
   FLOP bei angenommener Auslastung der Tensor-Rechenwerke (MFU).
6. **Gleichzeitig aktive LLM-Anfragen**: Jedes Gespräch rechnet nur einen
   Teil der Zeit (Duty Cycle, *Annahme*). Die Anzahl gleichzeitig aktiver
   Anfragen ist binomialverteilt; dimensioniert wird auf das 99-%-Quantil.

Speicher-Einheiten: Datenblatt-Kapazitäten («24 GB») werden als GiB (2^30 Byte)
gelesen, Bandbreiten als dezimale GB/s (10^9 Byte/s).

Diese Rechnung ersetzt keinen Lasttest. Vor einer Beschaffung mit dem
Messwerkzeug (:mod:`gw_contactcenter.latenzmessung`) auf der Zielhardware messen.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from .telemetry import ZIELBUDGET_MS

__all__ = [
    "GPU",
    "GPUS",
    "MIN_TOKENS_S",
    "MODELLE",
    "PRAEZISIONEN",
    "Bedarf",
    "Modell",
    "Option",
    "aktive_anfragen",
    "bericht",
    "dimensionieren",
    "gewichte_byte",
    "kv_byte_pro_token",
]

GIB = 2**30
ERSTER_SATZ_TOKEN = 10  # Annahme: Länge eines kurzen ersten Satzes in Token
# Nötige Dekodierrate, damit der erste Satz im Zielbudget «erster_satz» fertig ist.
MIN_TOKENS_S = ERSTER_SATZ_TOKEN / (ZIELBUDGET_MS["erster_satz"] / 1000.0)


@dataclass(frozen=True)
class Modell:
    """Architektur eines dichten Decoder-Transformers mit GQA und SwiGLU."""

    name: str
    schichten: int
    versteckt: int  # hidden_size
    zwischen: int  # intermediate_size (MLP)
    vokabular: int
    q_koepfe: int
    kv_koepfe: int
    kopf_dim: int
    gebundene_einbettung: bool
    quelle: str

    @property
    def parameter_linear(self) -> int:
        """Parameter der Attention- und MLP-Matrizen über alle Schichten."""
        attn = self.versteckt * self.kopf_dim * (2 * self.q_koepfe + 2 * self.kv_koepfe)
        mlp = 3 * self.versteckt * self.zwischen
        return self.schichten * (attn + mlp)

    @property
    def parameter_einbettung(self) -> int:
        """Einbettung plus LM-Kopf (einmal, falls gebunden)."""
        return self.vokabular * self.versteckt * (1 if self.gebundene_einbettung else 2)

    @property
    def parameter(self) -> int:
        return self.parameter_linear + self.parameter_einbettung


MODELLE: dict[str, Modell] = {
    m.name: m
    for m in [
        Modell(
            "qwen3-8b",
            schichten=36,
            versteckt=4096,
            zwischen=12288,
            vokabular=151936,
            q_koepfe=32,
            kv_koepfe=8,
            kopf_dim=128,
            gebundene_einbettung=False,
            quelle="https://huggingface.co/Qwen/Qwen3-8B/blob/main/config.json",
        ),
        Modell(
            "llama-3.1-8b",
            schichten=32,
            versteckt=4096,
            zwischen=14336,
            vokabular=128000,
            q_koepfe=32,
            kv_koepfe=8,
            kopf_dim=128,
            gebundene_einbettung=False,
            quelle="https://arxiv.org/abs/2407.21783 (Tabelle 3)",
        ),
        Modell(
            "llama-3.1-70b",
            schichten=80,
            versteckt=8192,
            zwischen=28672,
            vokabular=128000,
            q_koepfe=64,
            kv_koepfe=8,
            kopf_dim=128,
            gebundene_einbettung=False,
            quelle="https://arxiv.org/abs/2407.21783 (Tabelle 3)",
        ),
    ]
}


@dataclass(frozen=True)
class GPU:
    """Datenblattwerte; TFLOPS ohne Sparsity (dicht)."""

    name: str
    speicher_gib: float
    bandbreite_gb_s: float
    tflops_bf16: float
    tflops_fp8: float | None  # None = keine FP8-Rechenwerke
    tdp_w: float
    quelle: str


GPUS: dict[str, GPU] = {
    g.name: g
    for g in [
        GPU("L4", 24, 300, 121, 242.5, 72, "https://www.nvidia.com/en-zz/data-center/l4/"),
        GPU(
            "L40S",
            48,
            864,
            362.05,
            733,
            350,
            "NVIDIA L40S Datasheet (2023), https://www.pny.com/File%20Library/Company/Support/"
            "Product%20Brochures/NVIDIA%20Data%20Center%20GPUs/l40s-datasheet.pdf",
        ),
        GPU(
            "A100-80GB-SXM",
            80,
            2039,
            312,
            None,
            400,
            "https://www.nvidia.com/en-sg/data-center/a100",
        ),
        GPU(
            "H100-SXM", 80, 3350, 989.5, 1979, 700, "https://www.nvidia.com/en-us/data-center/h100/"
        ),
    ]
}

PRAEZISIONEN = ("bf16", "fp8", "int4")
_INT4_BYTE_PRO_GEWICHT = 0.5 + 2.5 / 128  # 4 Bit + (FP16-Skala + 4-Bit-Nullpunkt) je 128er-Gruppe


def gewichte_byte(m: Modell, praezision: str) -> float:
    """Gewichtsspeicher in Byte (siehe Modul-Docstring, Punkt 2)."""
    if praezision == "bf16":
        return 2.0 * m.parameter
    if praezision == "fp8":
        return 1.0 * m.parameter_linear + 2.0 * m.parameter_einbettung
    if praezision == "int4":
        return _INT4_BYTE_PRO_GEWICHT * m.parameter_linear + 2.0 * m.parameter_einbettung
    raise ValueError(f"Präzision {praezision!r} unbekannt, erlaubt: {PRAEZISIONEN}")


def kv_byte_pro_token(m: Modell, byte_pro_wert: int = 2) -> int:
    """2 × Schichten × KV-Köpfe × Kopfdimension × Byte pro Wert."""
    if byte_pro_wert not in (1, 2):
        raise ValueError("byte_pro_wert muss 1 (FP8) oder 2 (FP16/BF16) sein")
    return 2 * m.schichten * m.kv_koepfe * m.kopf_dim * byte_pro_wert


def _binom_quantil(n: int, p: float, q: float) -> int:
    """Kleinstes k mit P(X ≤ k) ≥ q für X ~ Binomial(n, p)."""
    kumuliert = 0.0
    for k in range(n + 1):
        kumuliert += math.comb(n, k) * p**k * (1 - p) ** (n - k)
        if kumuliert >= q - 1e-12:
            return k
    return n


def aktive_anfragen(gespraeche: int, duty_cycle: float, quantil: float = 0.99) -> int:
    """Gleichzeitig aktive LLM-Anfragen (Binomial-Quantil, mindestens 1)."""
    if gespraeche < 1:
        raise ValueError("gespraeche muss ≥ 1 sein")
    if not 0.0 < duty_cycle <= 1.0:
        raise ValueError("duty_cycle muss in (0, 1] liegen")
    return max(1, _binom_quantil(gespraeche, duty_cycle, quantil))


@dataclass(frozen=True)
class Bedarf:
    """Lastannahmen. Alle Standardwerte sind Annahmen, keine Messungen."""

    gespraeche: int = 10  # gleichzeitige Gespräche (z. B. Bot-Kanäle aus Erlang-B)
    kontext_tokens: int = 6000  # System-Prompt + Wissen + Verlauf pro Gespräch (Annahme)
    duty_cycle: float = 0.2  # Anteil der Gesprächszeit, in der das LLM rechnet (Annahme)
    neue_tokens_pro_wechsel: int = 300  # zu rechnende Tokens bei Prefix-Cache-Treffer (Annahme)
    min_tokens_s: float = MIN_TOKENS_S  # 10 Token in 250 ms (erster Satz, Zielbudget)
    max_ttft_ms: float = 300.0  # Ziel erstes Token mit Prefix-Cache (Annahme)
    kv_byte_pro_wert: int = 2  # 2 = Standard, 1 = --kv-cache-dtype fp8
    # vLLM --gpu-memory-utilization; 0.92 ist der Standardwert in vLLM 0.32.0 (vllm/config/cache.py)
    gpu_speicheranteil: float = 0.92
    reserve_gib_pro_karte: float = 1.5  # CUDA-Kontext, Aktivierungen, Graphen (Annahme)
    effizienz: float = 0.5  # erreichbarer Anteil der Roofline-Obergrenze (Annahme)
    mfu: float = 0.4  # Auslastung der Tensor-Rechenwerke beim Prefill (Annahme)

    def __post_init__(self) -> None:
        for name in ("effizienz", "mfu", "gpu_speicheranteil", "duty_cycle"):
            wert = getattr(self, name)
            if not 0.0 < wert <= 1.0:
                raise ValueError(f"{name} muss in (0, 1] liegen, ist {wert}")
        if self.gespraeche < 1 or self.kontext_tokens < 1 or self.neue_tokens_pro_wechsel < 1:
            raise ValueError("gespraeche, kontext_tokens, neue_tokens_pro_wechsel: >= 1")
        if self.min_tokens_s <= 0 or self.max_ttft_ms <= 0 or self.reserve_gib_pro_karte < 0:
            raise ValueError("min_tokens_s und max_ttft_ms > 0, reserve_gib_pro_karte >= 0")


@dataclass(frozen=True)
class Option:
    gpu: str
    karten: int
    praezision: str
    gewichte_gib: float
    kv_gib: float
    verfuegbar_gib: float
    aktive_anfragen: int
    tokens_s_obergrenze: float  # pro Strom, Roofline
    tokens_s_annahme: float  # Obergrenze × Effizienz
    ttft_cache_ms: float
    ttft_kalt_ms: float
    preis: float | None = None


def _ttft_ms(m: Modell, g: GPU, karten: int, praezision: str, tokens: int, mfu: float) -> float:
    tflops = g.tflops_fp8 if (praezision == "fp8" and g.tflops_fp8) else g.tflops_bf16
    return 2.0 * m.parameter * tokens / (tflops * 1e12 * karten * mfu) * 1000.0


def dimensionieren(
    modell: str | Modell,
    bedarf: Bedarf | None = None,
    *,
    praezisionen: tuple[str, ...] = PRAEZISIONEN,
    gpus: Mapping[str, GPU] | None = None,
    karten_optionen: tuple[int, ...] = (1, 2, 4, 8),
    preise: Mapping[str, float] | None = None,
) -> list[Option]:
    """Alle passenden Konfigurationen, kleinste zuerst.

    Sortierung: ohne ``preise`` nach Kartenzahl und Gesamtspeicher (kleinste
    ausreichende Konfiguration), mit ``preise`` (eigene Preise je Karte und
    Monat) nach Gesamtpreis. Das Repo enthält bewusst keine Preise.
    """
    m = MODELLE[modell] if isinstance(modell, str) else modell
    b = bedarf or Bedarf()
    katalog = gpus or GPUS
    aktiv = aktive_anfragen(b.gespraeche, b.duty_cycle)
    kv_tok = kv_byte_pro_token(m, b.kv_byte_pro_wert)
    kv = kv_tok * b.kontext_tokens * b.gespraeche
    out: list[Option] = []
    for g in katalog.values():
        for prae in praezisionen:
            if prae == "fp8" and g.tflops_fp8 is None:
                continue  # ohne FP8-Rechenwerke nicht sinnvoll
            gew = gewichte_byte(m, prae)
            for n in karten_optionen:
                if m.kv_koepfe % n:
                    continue  # Tensor-Parallelität teilt die KV-Köpfe
                verfuegbar = n * (g.speicher_gib * b.gpu_speicheranteil - b.reserve_gib_pro_karte)
                if (gew + kv) / GIB > verfuegbar:
                    continue
                gelesen = gew + aktiv * b.kontext_tokens * kv_tok
                obergrenze = n * g.bandbreite_gb_s * 1e9 / gelesen
                if obergrenze * b.effizienz < b.min_tokens_s:
                    continue
                ttft = _ttft_ms(m, g, n, prae, b.neue_tokens_pro_wechsel, b.mfu)
                if ttft > b.max_ttft_ms:
                    continue
                preis = None if preise is None or g.name not in preise else n * preise[g.name]
                out.append(
                    Option(
                        gpu=g.name,
                        karten=n,
                        praezision=prae,
                        gewichte_gib=gew / GIB,
                        kv_gib=kv / GIB,
                        verfuegbar_gib=verfuegbar,
                        aktive_anfragen=aktiv,
                        tokens_s_obergrenze=obergrenze,
                        tokens_s_annahme=obergrenze * b.effizienz,
                        ttft_cache_ms=ttft,
                        ttft_kalt_ms=_ttft_ms(m, g, n, prae, b.kontext_tokens, b.mfu),
                        preis=preis,
                    )
                )
                break  # kleinste Kartenzahl je (GPU, Präzision) genügt
    if preise is not None:
        out.sort(key=lambda o: (o.preis is None, o.preis or 0.0, o.karten))
    else:
        out.sort(
            key=lambda o: (o.karten, o.karten * katalog[o.gpu].speicher_gib, -o.tokens_s_obergrenze)
        )
    return out


def bericht(modell: str, bedarf: Bedarf, optionen: list[Option], max_zeilen: int = 8) -> str:
    """Textbericht mit allen Zwischenschritten (für CLI und Doku)."""
    m = MODELLE[modell]
    kv_tok = kv_byte_pro_token(m, bedarf.kv_byte_pro_wert)
    tokens = bedarf.kontext_tokens * bedarf.gespraeche
    zeilen = [
        "GPU-Dimensionierung (Obergrenzen aus Datenblattwerten, keine Messung)",
        f"Modell {m.name}: {m.parameter / 1e9:.2f} Mrd. Parameter "
        f"({m.parameter_linear / 1e9:.2f} Mrd. in linearen Schichten)",
        f"  Quelle: {m.quelle}",
        f"KV-Cache: 2 x {m.schichten} Schichten x {m.kv_koepfe} KV-Köpfe x {m.kopf_dim} x "
        f"{bedarf.kv_byte_pro_wert} Byte = {kv_tok:,} Byte/Token".replace(",", "'"),
        f"  {bedarf.gespraeche} Gespräche x {bedarf.kontext_tokens:,} Token = {tokens:,} Token "
        f"-> {kv_tok * tokens / GIB:.2f} GiB".replace(",", "'"),
        "Aktive LLM-Anfragen (99-%-Quantil, Duty Cycle "
        f"{100 * bedarf.duty_cycle:.0f} % angenommen): "
        f"{aktive_anfragen(bedarf.gespraeche, bedarf.duty_cycle)}",
        f"Kriterien: Gewichte + KV-Cache passen, >= {bedarf.min_tokens_s:.0f} Tok/s pro Strom "
        f"nach Effizienzannahme, TTFT mit Prefix-Cache <= {bedarf.max_ttft_ms:.0f} ms "
        f"({bedarf.neue_tokens_pro_wechsel} neue Token).",
        "",
        "Karten GPU            Präz.  Gewichte  KV-Cache  verfügbar  Tok/s/Strom   TTFT ms",
        "                              GiB       GiB       GiB     max | Ann.  Cache | kalt",
    ]
    for o in optionen[:max_zeilen]:
        zeilen.append(
            f"{o.karten:>6} {o.gpu:<14} {o.praezision:<5} {o.gewichte_gib:>8.1f} "
            f"{o.kv_gib:>9.1f} {o.verfuegbar_gib:>10.1f} {o.tokens_s_obergrenze:>7.0f} | "
            f"{o.tokens_s_annahme:<4.0f} {o.ttft_cache_ms:>5.0f} | {o.ttft_kalt_ms:<5.0f}"
        )
    if not optionen:
        zeilen.append("  keine Konfiguration im Katalog erfüllt den Bedarf")
    zeilen += [
        "",
        f"Annahmen: Effizienz {100 * bedarf.effizienz:.0f} % der Roofline, "
        f"MFU {100 * bedarf.mfu:.0f} %, "
        f"Reserve {bedarf.reserve_gib_pro_karte} GiB/Karte, "
        f"--gpu-memory-utilization {bedarf.gpu_speicheranteil}.",
        "Vor einer Beschaffung auf der Zielhardware messen (gw-contactcenter latenz-messen).",
    ]
    return "\n".join(z.rstrip() for z in zeilen)
