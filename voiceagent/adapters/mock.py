"""Deterministische Mock-Adapter mit simulierter Latenz.

Sie ersetzen echte Modelle in Tests, Evals und im Simulator. Die
Latenzen sind realistische Grössenordnungen für eine lokale GPU
(Stand 10/2026) und "verbrauchen" Zeit auf einer SimUhr. So lässt sich
die ganze Kette, inklusive Latenzbudget, ohne Hardware messen.
"""
from __future__ import annotations

import json
import re
from typing import Iterator, List, Optional

from ..audio import TELEFON_RATE, stille, ton
from ..policy import klassifiziere_regelbasiert


def _vorwaerts(uhr, ms: float) -> None:
    if uhr is not None and hasattr(uhr, "vorwaerts"):
        uhr.vorwaerts(ms)


def _letzter_user(messages: List[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return re.sub(r"</?kundschaft>", "", m.get("content", ""))
    return ""


def _kontext(messages: List[dict]) -> dict:
    for m in messages:
        c = m.get("content", "")
        if m.get("role") == "system" and c.startswith("KONTEXT:"):
            try:
                return json.loads(c[len("KONTEXT:"):])
            except json.JSONDecodeError:
                return {}
    return {}


class RegelLLM:
    """Klassifiziert per Stichwort, antwortet per Vorlage aus dem Kontext.

    ``boese=True`` simuliert ein fehlerhaftes Modell (Zusage + Roh-PII),
    um die Leitplanken im Gateway zu testen.
    """

    def __init__(self, uhr=None, ttft_ms: float = 110, ms_pro_token: float = 15,
                 klassifikation_ms: float = 90, boese: bool = False, fehler: bool = False):
        # Klassifikation: kleines Modell (0.6-2 B) oder Encoder, ~50-100 ms.
        # Antwort: 8-B-Modell mit Prefix-Caching auf lokaler GPU.
        self.uhr, self.ttft_ms, self.ms_pro_token = uhr, ttft_ms, ms_pro_token
        self.klassifikation_ms = klassifikation_ms
        self.boese, self.fehler = boese, fehler
        self.aufrufe = 0

    def _antwort(self, messages: List[dict], json_modus: bool) -> str:
        if json_modus:
            text = _letzter_user(messages)
            anliegen, p = klassifiziere_regelbasiert(text)
            nr = re.search(r"\b\d{6}\b", text)
            return json.dumps({"anliegen": anliegen, "sicherheit": p,
                               "bestellnummer": nr.group(0) if nr else None})
        if self.boese:
            return ("Ich garantiere Ihnen die volle Rückerstattung. "
                    "Ihre Karte 4111 1111 1111 1111 ist notiert.")
        k = _kontext(messages)
        typ = k.get("typ")
        if typ == "bestellstatus":
            d = k.get("daten", {})
            if d.get("gefunden"):
                return (f"Ihre Bestellung {k.get('bestellnummer')} ist {d['status']}. "
                        f"Die Zustellung erfolgt {d['zustellung']}.")
            return "Zu dieser Bestellnummer finde ich leider keinen Eintrag."
        if typ == "wissen":
            t = k.get("daten", {}).get("treffer")
            return t or "Das kann ich leider nicht sicher beantworten."
        return "Wie kann ich Ihnen weiterhelfen?"

    def complete(self, messages: List[dict], max_tokens: int = 200,
                 json_modus: bool = False) -> dict:
        self.aufrufe += 1
        if self.fehler:
            raise ConnectionError("simulierter Ausfall")
        text = self._antwort(messages, json_modus)
        t_aus = max(1, len(text) // 4)
        t_ein = sum(len(m.get("content", "")) for m in messages) // 4
        _vorwaerts(self.uhr, self.klassifikation_ms if json_modus
                   else self.ttft_ms + t_aus * self.ms_pro_token)
        return {"text": text, "tokens_ein": t_ein, "tokens_aus": t_aus}

    def stream(self, messages: List[dict], max_tokens: int = 200) -> Iterator[str]:
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
    """Liefert vorgegebene Texte der Reihe nach (eine Äusserung = ein Text)."""

    def __init__(self, texte: List[str], uhr=None, latenz_ms: float = 120):
        # 120 ms: Streaming-ASR hat beim Turn-Ende den Grossteil schon erkannt
        self.texte, self.uhr, self.latenz_ms = list(texte), uhr, latenz_ms
        self.index = 0
        self.sichtbar: Optional[int] = None   # Simulator: bisher gesprochene Wörter

    def teiltext(self) -> str:
        if self.index >= len(self.texte):
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
    """Erzeugt hörbares Testaudio: ~330 ms pro Wort (Sprechtempo ~180 Wörter/min)."""

    def __init__(self, uhr=None, latenz_ms: float = 120, ms_pro_wort: int = 330):
        self.uhr, self.latenz_ms, self.ms_pro_wort = uhr, latenz_ms, ms_pro_wort

    def synthetisieren(self, text: str) -> bytes:
        _vorwaerts(self.uhr, self.latenz_ms)
        n = max(1, len(text.split()))
        return ton(220.0, n * self.ms_pro_wort - 40, TELEFON_RATE, 0.2) + stille(40)
