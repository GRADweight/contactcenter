"""Deterministische Mock-Adapter mit fest eingestellter Verzögerung.

Sie ersetzen echte Modelle in Tests, Evals und im Simulator. Die
Verzögerungen sind **fest eingestellte Annahmen** und «verbrauchen» Zeit auf
einer simulierten Uhr. Was der Simulator damit zeigt, ist das Zusammenspiel
der Stufen (z. B. dass Satz-Streaming die erste Antwort nicht an die ganze
Antwort bindet), nicht die Geschwindigkeit echter Modelle. Echte Werte
liefert nur eine Messung (:mod:`gw_contactcenter.latenzmessung`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any, Protocol

from ..audio import TELEFON_RATE, stille, ton
from ..gateway import LLMErgebnis, Nachricht
from ..policy import klassifiziere_regelbasiert

# Fest eingestellte Verzögerungen in ms (Annahmen, keine Messwerte).
MOCK_LATENZ_MS = {
    "asr": 120.0,  # Restzeit einer Streaming-ASR nach Turn-Ende
    "klassifikation": 90.0,  # kleines Modell, JSON-Antwort
    "llm_ttft": 110.0,  # erstes Token mit Prefix-Cache-Treffer
    "llm_pro_token": 15.0,
    "tts": 120.0,  # erstes Audio einer Streaming-TTS
}


class _Uhr(Protocol):
    def vorwaerts(self, ms: float) -> None: ...


def _vorwaerts(uhr: _Uhr | None, ms: float) -> None:
    if uhr is not None:
        uhr.vorwaerts(ms)


def _letzter_user(messages: list[Nachricht]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return re.sub(r"</?kundschaft>", "", m.get("content", ""))
    return ""


def _kontext(messages: list[Nachricht]) -> dict[str, Any]:
    for m in messages:
        c = m.get("content", "")
        if m.get("role") == "system" and c.startswith("KONTEXT:"):
            try:
                d = json.loads(c[len("KONTEXT:") :])
            except json.JSONDecodeError:
                return {}
            return d if isinstance(d, dict) else {}
    return {}


class RegelLLM:
    """Klassifiziert per Stichwort, antwortet per Vorlage aus dem Kontext.

    ``boese=True`` simuliert ein fehlerhaftes Modell (Zusage und Roh-PII),
    um die Antwortprüfung zu testen; ``fehler=True`` einen Ausfall.
    """

    def __init__(
        self,
        uhr: _Uhr | None = None,
        *,
        ttft_ms: float = MOCK_LATENZ_MS["llm_ttft"],
        ms_pro_token: float = MOCK_LATENZ_MS["llm_pro_token"],
        klassifikation_ms: float = MOCK_LATENZ_MS["klassifikation"],
        boese: bool = False,
        fehler: bool = False,
    ) -> None:
        self.uhr, self.ttft_ms, self.ms_pro_token = uhr, ttft_ms, ms_pro_token
        self.klassifikation_ms = klassifikation_ms
        self.boese, self.fehler = boese, fehler
        self.aufrufe = 0

    def _antwort(self, messages: list[Nachricht], json_modus: bool) -> str:
        if json_modus:
            text = _letzter_user(messages)
            anliegen, p = klassifiziere_regelbasiert(text)
            nr = re.search(r"\b\d{6}\b", text)
            return json.dumps(
                {
                    "anliegen": anliegen,
                    "sicherheit": p,
                    "bestellnummer": nr.group(0) if nr else None,
                }
            )
        if self.boese:
            return (
                "Ich garantiere Ihnen die volle Rückerstattung. "
                "Ihre Karte 4111 1111 1111 1111 ist notiert."
            )
        k = _kontext(messages)
        typ = k.get("typ")
        if typ == "bestellstatus":
            d = k.get("daten", {})
            if d.get("gefunden"):
                return (
                    f"Ihre Bestellung {k.get('bestellnummer')} ist {d['status']}. "
                    f"Die Zustellung erfolgt {d['zustellung']}."
                )
            return "Zu dieser Bestellnummer finde ich leider keinen Eintrag."
        if typ == "wissen":
            t = k.get("daten", {}).get("treffer")
            return str(t) if t else "Das kann ich leider nicht sicher beantworten."
        return "Wie kann ich Ihnen weiterhelfen?"

    def complete(
        self, messages: list[Nachricht], max_tokens: int = 200, json_modus: bool = False
    ) -> LLMErgebnis:
        self.aufrufe += 1
        if self.fehler:
            raise ConnectionError("simulierter Ausfall")
        text = self._antwort(messages, json_modus)
        t_aus = max(1, len(text) // 4)
        t_ein = sum(len(m.get("content", "")) for m in messages) // 4
        _vorwaerts(
            self.uhr,
            self.klassifikation_ms if json_modus else self.ttft_ms + t_aus * self.ms_pro_token,
        )
        return {"text": text, "tokens_ein": t_ein, "tokens_aus": t_aus}

    def stream(self, messages: list[Nachricht], max_tokens: int = 200) -> Iterator[str]:
        self.aufrufe += 1
        if self.fehler:
            raise ConnectionError("simulierter Ausfall")
        text = self._antwort(messages, False)
        _vorwaerts(self.uhr, self.ttft_ms)
        for i, wort in enumerate(text.split(" ")):
            if i:
                _vorwaerts(self.uhr, self.ms_pro_token * max(1, len(wort) // 4))
            yield wort if i == 0 else " " + wort


class MockASR:
    """Liefert vorgegebene Texte der Reihe nach (eine Äusserung = ein Text).

    ``zwischenergebnisse=True`` spielt eine Streaming-ASR: ``teiltext()``
    liefert die bisher gesprochenen Wörter. ``False`` entspricht dem
    HTTP-Adapter im Server (keine Zwischenergebnisse).
    """

    def __init__(
        self,
        texte: list[str],
        uhr: _Uhr | None = None,
        latenz_ms: float = MOCK_LATENZ_MS["asr"],
        *,
        zwischenergebnisse: bool = True,
    ) -> None:
        self.texte, self.uhr, self.latenz_ms = list(texte), uhr, latenz_ms
        self.zwischenergebnisse = zwischenergebnisse
        self.index = 0
        self.sichtbar: int | None = None  # Simulator: bisher gesprochene Wörter

    def teiltext(self) -> str:
        if not self.zwischenergebnisse or self.index >= len(self.texte):
            return ""
        t = self.texte[self.index]
        return t if self.sichtbar is None else " ".join(t.split()[: self.sichtbar])

    def transkribieren(self, pcm16: bytes, rate: int) -> str:
        _vorwaerts(self.uhr, self.latenz_ms)
        return self.verwerfen()

    def verwerfen(self) -> str:
        t = self.texte[self.index] if self.index < len(self.texte) else ""
        self.index += 1
        self.sichtbar = None
        return t


class MockTTS:
    """Erzeugt hörbares Testaudio, ``ms_pro_wort`` pro Wort (Sprechtempo als Annahme)."""

    def __init__(
        self,
        uhr: _Uhr | None = None,
        latenz_ms: float = MOCK_LATENZ_MS["tts"],
        ms_pro_wort: int = 330,
    ) -> None:
        self.uhr, self.latenz_ms, self.ms_pro_wort = uhr, latenz_ms, ms_pro_wort

    def synthetisieren(self, text: str) -> bytes:
        _vorwaerts(self.uhr, self.latenz_ms)
        n = max(1, len(text.split()))
        return ton(220.0, n * self.ms_pro_wort - 40, TELEFON_RATE, 0.2) + stille(40)
