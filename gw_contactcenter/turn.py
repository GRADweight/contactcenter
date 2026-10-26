"""Turn-Taking: Wann ist die Kundschaft fertig, wann unterbricht sie?

Die klassische Lösung wartet eine feste Stille ab (oft 700-800 ms). Dann
wirkt der Bot träge, und wer beim Diktieren einer Nummer kurz Luft holt,
wird abgeschnitten.

Hier ist das Endpointing *semantisch*: Die Wartezeit hängt davon ab, ob der
bisher erkannte Text wie ein fertiger Satz aussieht::

    "Wo ist meine Bestellung?"        -> fertig    -> kurze Wartezeit
    "Meine Nummer ist 44 71 und"      -> unfertig  -> lange Wartezeit

Dieselbe Idee steckt in Pipecat Smart Turn (audiobasiertes Modell) und im
LiveKit Turn Detector (textbasiertes Modell). Die Heuristik unten ist ein
Ersatz ohne Abhängigkeiten; ein Modell steckt man über ``semantik=`` ein.

Zweite Aufgabe: Barge-in. Spricht die Kundschaft in die Ansage hinein,
muss der Bot verstummen. Rückmeldungen wie «mhm» oder «genau» sind aber
Backchannel, keine Unterbrechung.

Die Wartezeiten sind Konfigurationswerte (``TurnConfig``), keine
gemessenen Optima; sie gehören pro Projekt mit echten Aufnahmen kalibriert.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

# Ereignistypen
SPRACHE_START = "sprache_start"
TURN_ENDE = "turn_ende"
BARGE_IN = "barge_in"
BACKCHANNEL = "backchannel"
VERWORFEN = "verworfen"  # zu kurz: Husten, Klick, Leitungsknacken

_FUELLER_UND_BINDEWOERTER = {
    "und",
    "aber",
    "oder",
    "weil",
    "dass",
    "also",
    "wenn",
    "dann",
    "denn",
    "äh",
    "ähm",
    "eh",
    "ehm",
    "hm",
    "der",
    "die",
    "das",
    "den",
    "dem",
    "des",
    "ein",
    "eine",
    "einen",
    "mit",
    "von",
    "für",
    "zu",
    "zum",
    "zur",
    "auf",
    "ich",
    "mein",
    "meine",
    "meinen",
    "ist",
    "sind",
    "habe",
    "hab",
    "wegen",
    "bei",
    "nach",
    "an",
    "im",
    "in",
    "the",
    "and",
}
_FRAGEWOERTER = {
    "wo",
    "wie",
    "was",
    "wann",
    "warum",
    "wieso",
    "welche",
    "welcher",
    "welches",
    "wer",
    "können",
    "kann",
    "könnten",
    "haben",
    "ist",
    "gibt",
}
_BACKCHANNELS = {
    "mhm",
    "mm",
    "hm",
    "ja",
    "jaja",
    "genau",
    "okay",
    "ok",
    "aha",
    "ah",
    "stimmt",
    "klar",
    "gut",
    "super",
    "jo",
    "ja ja",
    "ja genau",
    "okay danke",
}
_NUMMER_KONTEXT = re.compile(r"(nummer|nr|iban|konto|telefon|plz|postleitzahl)", re.IGNORECASE)


def satzende_wahrscheinlichkeit(text: str) -> float:
    """0 = sicher unfertig, 1 = sicher fertig. Deterministische Heuristik."""
    t = text.strip()
    if not t:
        return 0.5
    if t[-1] in ".?!":
        return 0.9
    woerter = re.findall(r"[\wäöüÄÖÜ]+", t.lower())
    if not woerter:
        return 0.5
    letzt = woerter[-1]
    if letzt in _FUELLER_UND_BINDEWOERTER:
        return 0.1
    if letzt.isdigit() and _NUMMER_KONTEXT.search(t):
        return 0.3  # Nummern werden in Gruppen diktiert: Geduld
    if woerter[0] in _FRAGEWOERTER and len(woerter) >= 3:
        return 0.75
    return 0.6


def ist_backchannel(text: str) -> bool:
    t = re.sub(r"[^\wäöü ]", "", text.strip().lower())
    return t in _BACKCHANNELS


@dataclass(frozen=True)
class TurnConfig:
    min_pause_ms: int = 200  # ab hier wird über das Ende nachgedacht
    warte_fertig_ms: int = 200  # Stille, wenn der Satz fertig aussieht
    warte_unfertig_ms: int = 1200  # Stille, wenn der Satz unfertig aussieht
    min_sprache_ms: int = 150  # kürzer = Geräusch, kein Turn
    barge_in_ms: int = 300  # so lange muss jemand hineinreden
    max_turn_ms: int = 30000  # Sicherheitsnetz gegen Endlos-Monolog


@dataclass
class TurnEreignis:
    typ: str
    t_ms: int
    daten: dict[str, object] = field(default_factory=dict)


class TurnTaking:
    """Zustandsmaschine pro Gespräch, gefüttert mit einem Aufruf pro 20-ms-Frame.

    ``frame(ist_sprache, t_ms, teiltext)`` liefert Ereignisse. ``teiltext``
    ist das bisherige Zwischenergebnis einer Streaming-ASR (leer, wenn
    keine Zwischenergebnisse verfügbar sind). Die Pipeline meldet mit
    ``agent_beginnt``/``agent_endet``, wann der Bot selbst spricht.
    """

    def __init__(
        self,
        cfg: TurnConfig | None = None,
        semantik: Callable[[str], float] = satzende_wahrscheinlichkeit,
    ) -> None:
        self.cfg = cfg or TurnConfig()
        self.semantik = semantik
        self.agent_spricht = False
        self._start: int | None = None  # Beginn der aktuellen Äusserung
        self._letzte_sprache: int | None = None
        self._barge_gemeldet = False
        self._war_backchannel = False

    def agent_beginnt(self, t_ms: int) -> None:
        self.agent_spricht = True
        self._barge_gemeldet = False

    def agent_endet(self, t_ms: int) -> None:
        self.agent_spricht = False

    def frame(self, ist_sprache: bool, t_ms: int, teiltext: str = "") -> list[TurnEreignis]:
        ev: list[TurnEreignis] = []
        c = self.cfg
        if ist_sprache:
            if self._start is None:
                self._start = t_ms
                ev.append(TurnEreignis(SPRACHE_START, t_ms))
            self._letzte_sprache = t_ms
            dauer = t_ms - self._start
            if self.agent_spricht and not self._barge_gemeldet and dauer >= c.barge_in_ms:
                self._barge_gemeldet = True
                if teiltext and ist_backchannel(teiltext):
                    self._war_backchannel = True  # Bot spricht weiter
                else:
                    ev.append(TurnEreignis(BARGE_IN, t_ms, {"dauer_ms": dauer}))
                    self.agent_spricht = False
            if dauer >= c.max_turn_ms:
                ev.append(self._ende(t_ms, 0, 1.0, "max_turn"))
            return ev

        if self._start is None or self._letzte_sprache is None:
            return ev  # Stille ohne laufende Äusserung
        stille = t_ms - self._letzte_sprache
        if stille < c.min_pause_ms:
            return ev
        dauer = self._letzte_sprache - self._start
        if dauer < c.min_sprache_ms:
            ev.append(TurnEreignis(VERWORFEN, t_ms, {"dauer_ms": dauer}))
            self._reset()
            return ev
        if self._war_backchannel or (self.agent_spricht and teiltext and ist_backchannel(teiltext)):
            ev.append(TurnEreignis(BACKCHANNEL, t_ms, {"text": teiltext}))
            self._reset()
            return ev
        p = self.semantik(teiltext)
        warte = c.warte_unfertig_ms - p * (c.warte_unfertig_ms - c.warte_fertig_ms)
        if stille >= warte:
            ev.append(self._ende(t_ms, stille, p, "semantisch"))
        return ev

    def _ende(self, t_ms: int, stille: int, p: float, grund: str) -> TurnEreignis:
        e = TurnEreignis(
            TURN_ENDE,
            t_ms,
            {
                "stille_ms": stille,
                "satzende_p": round(p, 2),
                "sprach_start_ms": self._start,
                "sprach_ende_ms": self._letzte_sprache,
                "grund": grund,
            },
        )
        self._reset()
        return e

    def _reset(self) -> None:
        self._start = None
        self._letzte_sprache = None
        self._war_backchannel = False
