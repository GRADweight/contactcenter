"""Latenz-Messung: Spans pro Gesprächswechsel, Perzentile, Prometheus-Export.

Bei einem Sprachagenten zählt eine Zahl am meisten: die **Zeit bis zum ersten
Ton** (Kundschaft hört auf zu sprechen -> Bot-Stimme beginnt). Sie setzt sich
aus Stufen zusammen; jede Stufe ist ein Span::

    endpointing -> asr -> dialog -> llm_ttft -> erster_satz -> tts_erstes_audio

Die Spans sind schlicht (Name, Start, Ende, Attribute) und lassen sich 1:1
nach OpenTelemetry abbilden. Die Uhr ist injizierbar: Im Simulator ist sie
simuliert (Ergebnisse sind dann Simulationen), im Server ist sie echt
(Ergebnisse sind Messungen des laufenden Systems).
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

# Zielwerte für den Median in ms. Das sind ANNAHMEN (Planungswerte für einen
# Sprachagenten mit lokaler GPU), keine Messungen. Gesamtziel und Aufteilung
# auf die Stufen sind eine Budgetannahme, die mit Messungen auf der
# Zielhardware überprüft werden muss (gw-contactcenter latenz-messen).
ZIELBUDGET_MS: dict[str, float] = {
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
    attribute: dict[str, object] = field(default_factory=dict)

    @property
    def dauer_ms(self) -> float:
        return round(self.ende_ms - self.start_ms, 1)


def perzentil(werte: list[float], p: float) -> float:
    """Perzentil mit linearer Interpolation (wie numpy.percentile, Methode 'linear')."""
    if not werte:
        raise ValueError("keine Werte")
    if not 0.0 <= p <= 100.0:
        raise ValueError("p muss zwischen 0 und 100 liegen")
    w = sorted(werte)
    k = (len(w) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(w) - 1)
    return w[f] + (w[c] - w[f]) * (k - f)


class Messung:
    def __init__(self, uhr_ms: Callable[[], float] | None = None, max_spans: int = 20_000) -> None:
        self.uhr_ms: Callable[[], float] = uhr_ms or (lambda: time.monotonic() * 1000.0)
        self.spans: deque[Span] = deque(maxlen=max_spans)  # Ringpuffer für Dauerbetrieb

    @contextmanager
    def span(self, name: str, **attribute: object) -> Iterator[Span]:
        s = Span(name, self.uhr_ms(), attribute=dict(attribute))
        try:
            yield s
        finally:
            s.ende_ms = self.uhr_ms()
            self.spans.append(s)

    def erfassen(self, name: str, start_ms: float, ende_ms: float, **attribute: object) -> Span:
        s = Span(name, start_ms, ende_ms, dict(attribute))
        self.spans.append(s)
        return s

    def werte(self, name: str) -> list[float]:
        return [s.dauer_ms for s in list(self.spans) if s.name == name]

    def perzentil(self, name: str, p: float) -> float:
        w = self.werte(name)
        return round(perzentil(w, p), 1) if w else 0.0

    def budget_bericht(self) -> dict[str, dict[str, float | bool]]:
        """p50/p95 je Stufe im Vergleich zum Zielbudget (Annahme)."""
        out: dict[str, dict[str, float | bool]] = {}
        for name, ziel in ZIELBUDGET_MS.items():
            if self.werte(name):
                p50, p95 = self.perzentil(name, 50), self.perzentil(name, 95)
                out[name] = {"p50": p50, "p95": p95, "ziel_p50": ziel, "ok": p50 <= ziel}
        return out

    def prometheus(self, praefix: str = "gw_contactcenter") -> str:
        """Text-Format für Prometheus (/metrics): Summary je Span-Name."""
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
    """Simulierte Uhr in ms: Mock-Adapter «verbrauchen» Zeit mit ``vorwaerts``."""

    def __init__(self, start_ms: float = 0.0) -> None:
        self.t = start_ms

    def __call__(self) -> float:
        return self.t

    def vorwaerts(self, ms: float) -> None:
        self.t += ms

    def vorstellen_auf(self, t_ms: float) -> None:
        """Uhr auf die Frame-Zeit vorstellen (nie zurück)."""
        self.t = max(self.t, t_ms)
