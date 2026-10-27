"""LLM-Gateway: der einzige Weg zu einem Sprachmodell.

Kein Dialog, kein Werkzeug und kein Entwickler spricht direkt mit einem
Modell. Dadurch gelten die folgenden Regeln an genau einer Stelle:

1. **Datenresidenz**: Je Datenklasse V0-V4 nur Modelle in erlaubten Regionen
   (lokal / ch / eu / us, s. :mod:`.policy`). V2-V4 bleiben lokal.
2. **Antwortprüfung**: Jeder Satz läuft durch die Antwort-Prüfer
   (:mod:`.leitplanken`): keine prüfsummengültigen Personendaten, keine
   verbotenen Zusagen. Ein beanstandeter Satz wird ersetzt, der Rest der
   Antwort nicht mehr gesprochen.
3. **Satz-Streaming**: Die Sprachausgabe beginnt mit dem ersten geprüften
   Satz, während das Modell weiterschreibt.
4. **Ausfallsicherheit und Kosten**: Fallback-Kette, Circuit Breaker,
   Token-Budget pro Anruf und Tageslimit pro Kalendertag (in der Währung der
   ``preis_pro_1k_token``-Angaben, die der Betreiber setzt). Backends mit
   ihrem Breaker und der :class:`Kostenzaehler` können von mehreren
   Gateways (einem pro Anruf) geteilt werden; beide sind threadsicher.

Ein Modell ist ein Adapter mit ``complete(...)`` und optional ``stream(...)``
(:class:`LLM`). Mock und OpenAI-kompatible Server sind austauschbar.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, TypedDict

from .audit import AuditLog
from .leitplanken import STANDARD_ANTWORT_PRUEFER, Befund, Pruefer, alle_pruefen
from .policy import REGIONEN, region_erlaubt

__all__ = [
    "ERSATZ_SATZ",
    "LLM",
    "Backend",
    "BudgetErschoepft",
    "CircuitBreaker",
    "GatewayAntwort",
    "KeinBackend",
    "Kostenzaehler",
    "LLMErgebnis",
    "LLMGateway",
    "Nachricht",
    "Satz",
    "satzende",
]

Nachricht = dict[str, str]
ERSATZ_SATZ = "Dazu kann ich am Telefon keine verbindliche Auskunft geben."


class LLMErgebnis(TypedDict):
    text: str
    tokens_ein: int
    tokens_aus: int


class LLM(Protocol):
    def complete(
        self, messages: list[Nachricht], max_tokens: int = 200, json_modus: bool = False
    ) -> LLMErgebnis: ...


class StreamingLLM(LLM, Protocol):
    def stream(self, messages: list[Nachricht], max_tokens: int = 200) -> Iterator[str]: ...


class CircuitBreaker:
    """Nach ``schwelle`` Fehlern in Folge wird ein Backend ``abkuehl_s`` lang
    übersprungen (offen), danach ist ein Probeaufruf erlaubt (halb offen)."""

    def __init__(
        self, schwelle: int = 3, abkuehl_s: float = 30.0, uhr: Callable[[], float] = time.monotonic
    ) -> None:
        self.schwelle, self.abkuehl_s, self.uhr = schwelle, abkuehl_s, uhr
        self.fehler = 0
        self.offen_seit: float | None = None
        self._sperre = threading.Lock()

    def darf(self) -> bool:
        with self._sperre:
            if self.offen_seit is None:
                return True
            return self.uhr() - self.offen_seit >= self.abkuehl_s

    def erfolg(self) -> None:
        with self._sperre:
            self.fehler, self.offen_seit = 0, None

    def fehlschlag(self) -> None:
        with self._sperre:
            self.fehler += 1
            if self.fehler >= self.schwelle:
                self.offen_seit = self.uhr()


class Kostenzaehler:
    """Kosten pro Kalendertag, geteilt von allen Anrufen eines Prozesses.

    Beim ersten Zugriff an einem neuen Tag (``tag()``) beginnt die Summe bei 0.
    """

    def __init__(self, limit: float = 50.0, tag: Callable[[], date] = date.today) -> None:
        if limit < 0:
            raise ValueError("limit muss >= 0 sein")
        self.limit = limit
        self._tag = tag
        self._sperre = threading.Lock()
        self._datum = tag()
        self._summe = 0.0

    def _rollen(self) -> None:
        heute = self._tag()
        if heute != self._datum:
            self._datum, self._summe = heute, 0.0

    @property
    def heute(self) -> float:
        with self._sperre:
            self._rollen()
            return self._summe

    def erschoepft(self) -> bool:
        return self.heute >= self.limit

    def buchen(self, betrag: float) -> None:
        if betrag < 0:
            raise ValueError("betrag muss >= 0 sein")
        with self._sperre:
            self._rollen()
            self._summe += betrag


@dataclass
class Backend:
    name: str
    region: str  # lokal | ch | eu | us
    modell: LLM
    preis_pro_1k_token: float = 0.0
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)

    def __post_init__(self) -> None:
        if self.region not in REGIONEN:
            raise ValueError(f"Region {self.region} unbekannt, erlaubt: {REGIONEN}")


class KeinBackend(RuntimeError):
    """Kein zulässiges Modell erreichbar: Der Dialog übergibt an Menschen."""


class BudgetErschoepft(RuntimeError):
    """Token-Budget des Anrufs oder Tageslimit erreicht."""


@dataclass
class GatewayAntwort:
    text: str
    backend: str
    region: str
    tokens_ein: int
    tokens_aus: int
    befunde: list[str]
    kosten: float


@dataclass
class Satz:
    text: str
    index: int
    backend: str
    region: str
    ttft_ms: float | None = None  # nur beim ersten Satz gesetzt


_ABKUERZUNGEN = {"z", "b", "ca", "nr", "bzw", "usw", "fr", "dr", "str", "tel", "evtl", "inkl"}


def satzende(puffer: str) -> int:
    """Index nach dem ersten Satzende im Puffer, sonst -1.

    Satzende = . ! ? gefolgt von Leerzeichen, nicht nach Abkürzungen (z. B.,
    Nr.) und nicht nach Ordinalzahlen (12. März). Sehr lange Sätze werden am
    Komma geteilt, damit die Sprachausgabe früher beginnen kann.
    """
    for i, ch in enumerate(puffer[:-1]):
        if ch in ".!?" and puffer[i + 1] == " ":
            wort = re.findall(r"[\wäöü]+$", puffer[:i].lower())
            if (
                ch == "."
                and wort
                and (wort[0] in _ABKUERZUNGEN or (wort[0].isdigit() and len(wort[0]) <= 2))
            ):
                continue
            return i + 1
    if len(puffer) > 90:
        k = puffer.rfind(", ", 40)
        if k > 0:
            return k + 1
    return -1


def _schaetze_tokens(text: str) -> int:
    """Grobe Schätzung (4 Zeichen pro Token), nur wo der Server keine Zahl liefert."""
    return max(1, len(text) // 4)


class LLMGateway:
    def __init__(
        self,
        backends: list[Backend],
        audit: AuditLog,
        *,
        token_budget_pro_anruf: int = 20000,
        tageslimit: float = 50.0,
        kosten: Kostenzaehler | None = None,
        antwort_pruefer: Sequence[Pruefer] = STANDARD_ANTWORT_PRUEFER,
        uhr_ms: Callable[[], float] | None = None,
    ) -> None:
        """``kosten`` teilt das Tageslimit mit anderen Gateways; ohne Angabe
        erhält das Gateway einen eigenen Zähler mit ``tageslimit``."""
        self.backends = backends  # Reihenfolge = Präferenz
        self.audit = audit
        self.token_budget = token_budget_pro_anruf
        self.kosten = kosten if kosten is not None else Kostenzaehler(tageslimit)
        self.antwort_pruefer = tuple(antwort_pruefer)
        self.verbrauch_tokens: dict[str, int] = {}
        self.uhr_ms: Callable[[], float] = uhr_ms or (lambda: time.monotonic() * 1000.0)

    @property
    def tageslimit(self) -> float:
        return self.kosten.limit

    @property
    def kosten_heute(self) -> float:
        return self.kosten.heute

    def kandidaten(self, klasse: int) -> list[Backend]:
        return [b for b in self.backends if region_erlaubt(b.region, klasse)]

    def _vorpruefung(self, klasse: int, anruf_id: str) -> list[Backend]:
        if self.verbrauch_tokens.get(anruf_id, 0) >= self.token_budget:
            raise BudgetErschoepft(f"Token-Budget für Anruf {anruf_id} erschöpft")
        if self.kosten.erschoepft():
            raise BudgetErschoepft("Tageslimit erreicht")
        kandidaten = self.kandidaten(klasse)
        if not kandidaten:
            self.audit.schreiben("kein_backend", anruf=anruf_id, klasse=klasse)
            raise KeinBackend(f"kein Modell für Datenklasse V{klasse} konfiguriert")
        return kandidaten

    def _verbuchen(self, b: Backend, anruf_id: str, t_ein: int, t_aus: int) -> float:
        kosten = (t_ein + t_aus) / 1000.0 * b.preis_pro_1k_token
        self.verbrauch_tokens[anruf_id] = self.verbrauch_tokens.get(anruf_id, 0) + t_ein + t_aus
        self.kosten.buchen(kosten)
        return kosten

    def _satz_pruefen(self, satz: str, anruf_id: str) -> tuple[str, list[Befund]]:
        befunde = alle_pruefen(self.antwort_pruefer, satz, anruf_id=anruf_id)
        return (ERSATZ_SATZ, befunde) if befunde else (satz, [])

    def saetze(
        self,
        messages: list[Nachricht],
        *,
        klasse: int,
        anruf_id: str,
        max_tokens: int = 200,
        max_woerter: int = 45,
    ) -> Iterator[Satz]:
        """Streamt die Antwort Satz für Satz; jeder Satz ist bereits geprüft."""
        kandidaten = self._vorpruefung(klasse, anruf_id)
        t0 = self.uhr_ms()
        for b in kandidaten:
            if not b.breaker.darf():
                continue
            try:
                stream = getattr(b.modell, "stream", None)
                if callable(stream):
                    quelle: Iterator[str] = iter(stream(messages, max_tokens=max_tokens))
                else:  # ganze Antwort in Wörter zerlegen: gleiche Satzlogik
                    text = b.modell.complete(messages, max_tokens=max_tokens)["text"]
                    quelle = iter([w if i == 0 else " " + w for i, w in enumerate(text.split(" "))])
                erstes = next(quelle, "")
            except Exception as e:  # noqa: BLE001 - Netz, Timeout, 5xx: nächstes Backend
                b.breaker.fehlschlag()
                self.audit.schreiben(
                    "backend_fehler", anruf=anruf_id, backend=b.name, fehler=type(e).__name__
                )
                continue
            ttft = self.uhr_ms() - t0
            befunde: list[str] = []
            idx, woerter = 0, 0
            puffer, gesamt = erstes, erstes

            def pruefen(
                roh: str, b: Backend = b, befunde: list[str] = befunde, ttft: float = ttft
            ) -> Satz | None:
                nonlocal idx, woerter
                satz = roh.strip()
                if not satz:
                    return None
                satz, bef = self._satz_pruefen(satz, anruf_id)
                befunde.extend(str(x) for x in bef)
                woerter += len(satz.split())
                s = Satz(satz, idx, b.name, b.region, ttft if idx == 0 else None)
                idx += 1
                return s

            fertig, stream_fehler = False, None
            try:
                while True:
                    while not fertig:
                        k = satzende(puffer)
                        if k < 0:
                            break
                        s = pruefen(puffer[:k])
                        puffer = puffer[k:]
                        if s:
                            yield s
                            fertig = woerter >= max_woerter or s.text == ERSATZ_SATZ
                    if fertig:
                        break
                    delta = next(quelle, None)
                    if delta is None:
                        break
                    puffer += delta
                    gesamt += delta
            except Exception as e:  # noqa: BLE001 - Abbruch mitten im Stream
                stream_fehler = e
            if stream_fehler is not None:
                b.breaker.fehlschlag()
                self.audit.schreiben(
                    "stream_abbruch",
                    anruf=anruf_id,
                    backend=b.name,
                    fehler=type(stream_fehler).__name__,
                    saetze=idx,
                )
                if idx == 0:
                    continue  # nichts gesagt: nächstes Backend versuchen
                return  # Gesagtes bleibt; ein Satzfragment wird nicht gesprochen
            if not fertig and puffer.strip() and woerter < max_woerter:
                s = pruefen(puffer)
                if s:
                    yield s
            if idx == 0:  # leere Antwort: am Telefon wäre das Stille
                b.breaker.fehlschlag()
                self.audit.schreiben("leere_antwort", anruf=anruf_id, backend=b.name)
                continue
            b.breaker.erfolg()
            t_ein = sum(_schaetze_tokens(m.get("content", "")) for m in messages)
            self._verbuchen(b, anruf_id, t_ein, _schaetze_tokens(gesamt))
            self.audit.schreiben(
                "llm_stream",
                anruf=anruf_id,
                backend=b.name,
                region=b.region,
                klasse=klasse,
                saetze=idx,
                befunde=befunde,
            )
            return
        raise KeinBackend("alle zulässigen Modelle ausgefallen")

    def anfrage(
        self,
        messages: list[Nachricht],
        *,
        klasse: int,
        anruf_id: str,
        max_tokens: int = 200,
        json_modus: bool = False,
    ) -> GatewayAntwort:
        """Eine ganze Antwort (z. B. Klassifikation als JSON)."""
        kandidaten = self._vorpruefung(klasse, anruf_id)
        letzter_fehler = ""
        for b in kandidaten:
            if not b.breaker.darf():
                continue
            try:
                r = b.modell.complete(messages, max_tokens=max_tokens, json_modus=json_modus)
            except Exception as e:  # noqa: BLE001 - Netz, Timeout, 5xx: nächstes Backend
                b.breaker.fehlschlag()
                letzter_fehler = f"{b.name}: {type(e).__name__}"
                self.audit.schreiben(
                    "backend_fehler", anruf=anruf_id, backend=b.name, fehler=type(e).__name__
                )
                continue
            b.breaker.erfolg()
            t_ein, t_aus = int(r.get("tokens_ein", 0)), int(r.get("tokens_aus", 0))
            kosten = self._verbuchen(b, anruf_id, t_ein, t_aus)
            text = r["text"]
            befunde: list[str] = []
            if not json_modus:
                bef = alle_pruefen(self.antwort_pruefer, text, anruf_id=anruf_id)
                if bef:
                    text, befunde = ERSATZ_SATZ, [str(x) for x in bef]
            self.audit.schreiben(
                "llm",
                anruf=anruf_id,
                backend=b.name,
                region=b.region,
                klasse=klasse,
                tokens=t_ein + t_aus,
                befunde=befunde,
            )
            return GatewayAntwort(text, b.name, b.region, t_ein, t_aus, befunde, round(kosten, 5))
        raise KeinBackend(f"alle zulässigen Modelle ausgefallen ({letzter_fehler})")
