"""Latenz-Messung: Spans pro Gesprächswechsel, Perzentile, Prometheus.

Bei Voice zählt eine einzige Zahl am meisten: **Zeit bis zum ersten
Ton** (Kundschaft hört auf zu sprechen -> Bot-Stimme beginnt). Sie
setzt sich aus Stufen zusammen; jede Stufe ist ein Span:

    endpointing -> asr -> dialog -> llm_ttft -> erster_satz -> tts_erstes_audio

Spans sind hier bewusst schlicht (Name, Start, Ende, Attribute) und
lassen sich 1:1 nach OpenTelemetry exportieren. Die Uhr ist
injizierbar, damit Tests mit simulierter Zeit deterministisch sind.
"""
from __future__ import annotations

import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Dict, List

# Zielwerte p50 in ms (KMU-Voicebot, Kaskade). Mensch-zu-Mensch-Pause ~200 ms.
BUDGET_MS = {
    "endpointing": 400,
    "asr": 200,
    "llm_ttft": 250,
    "erster_satz": 250,
    "tts_erstes_audio": 200,
    "zeit_bis_erster_ton": 1000,
}


@dataclass
class Span:
    name: str
    start_ms: float
    ende_ms: float = 0.0
    attribute: Dict[str, object] = field(default_factory=dict)

    @property
    def dauer_ms(self) -> float:
        return round(self.ende_ms - self.start_ms, 1)


class Messung:
    def __init__(self, uhr_ms: Callable[[], float] = None, max_spans: int = 20_000):
        self.uhr_ms = uhr_ms or (lambda: time.monotonic() * 1000.0)
        self.spans = deque(maxlen=max_spans)    # Ringpuffer: Server läuft wochenlang

    @contextmanager
    def span(self, name: str, **attribute):
        s = Span(name, self.uhr_ms(), attribute=dict(attribute))
        try:
            yield s
        finally:
            s.ende_ms = self.uhr_ms()
            self.spans.append(s)

    def erfassen(self, name: str, start_ms: float, ende_ms: float, **attribute) -> Span:
        s = Span(name, start_ms, ende_ms, dict(attribute))
        self.spans.append(s)
        return s

    def werte(self, name: str) -> List[float]:
        return [s.dauer_ms for s in list(self.spans) if s.name == name]   # Schnappschuss

    def perzentil(self, name: str, p: float) -> float:
        w = sorted(self.werte(name))
        if not w:
            return 0.0
        k = (len(w) - 1) * p / 100.0
        f = int(k)
        c = min(f + 1, len(w) - 1)
        return round(w[f] + (w[c] - w[f]) * (k - f), 1)

    def budget_bericht(self) -> Dict[str, dict]:
        out = {}
        for name, ziel in BUDGET_MS.items():
            if self.werte(name):
                p50, p95 = self.perzentil(name, 50), self.perzentil(name, 95)
                out[name] = {"p50": p50, "p95": p95, "ziel_p50": ziel, "ok": p50 <= ziel}
        return out

    def prometheus(self, praefix: str = "voiceagent") -> str:
        """Text-Format für Prometheus (/metrics). Summary je Span-Name."""
        zeilen = []
        for name in sorted({s.name for s in list(self.spans)}):
            m = f"{praefix}_{name}_ms"
            zeilen.append(f"# TYPE {m} summary")
            for q in (50, 95, 99):
                zeilen.append(f'{m}{{quantile="{q / 100}"}} {self.perzentil(name, q)}')
            w = self.werte(name)
            zeilen.append(f"{m}_sum {round(sum(w), 1)}")
            zeilen.append(f"{m}_count {len(w)}")
        return "\n".join(zeilen) + "\n"


class SimUhr:
    """Simulierte Uhr in ms: Adapter "verbrauchen" Zeit mit ``vorwaerts``."""

    def __init__(self, start_ms: float = 0.0):
        self.t = start_ms

    def __call__(self) -> float:
        return self.t

    def vorwaerts(self, ms: float) -> None:
        self.t += ms
