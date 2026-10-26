"""Genesys AudioHook / Audio Connector: Protokoll-Zustandsmaschine.

Genesys Cloud öffnet pro Anruf eine WebSocket-Verbindung zu unserem
Server. Über dieselbe Verbindung laufen:

* Text-Frames (JSON): Steuerung (open, ping, pause, close, ...)
* Binär-Frames: Audio, PCMU (mu-law) 8 kHz, 20-ms-Pakete

Zwei Betriebsarten:

* ``connector`` (Audio Connector): Voicebot im IVR, bidirektional.
  Wir senden Audio zurück und beenden mit ``disconnect`` plus
  ``outputVariables`` (z. B. Weiterleitungsziel für den Architect-Flow).
* ``monitor`` (AudioHook Monitor): Mithören laufender Agent-Gespräche
  (beide Kanäle) für Live-Transkript und Agent Assist. Kein Rückkanal.

Diese Klasse kennt kein Netzwerk. Sie nimmt Nachrichten an und gibt
Antworten zurück. Dadurch ist das Protokoll offline testbar, und der
Server (server.py) bleibt dünn.

Feldnamen nach der öffentlichen AudioHook-Spezifikation (Version "2")
und der Pipecat-Referenzimplementierung. Vor Produktion gegen die
aktuelle Genesys-Spezifikation und die Genesys AudioHook Test Suite
prüfen; insbesondere die Signaturprüfung (HTTP Message Signatures)
ist hier nur als API-Key-Prüfung umgesetzt.
"""
from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field
from typing import List, Optional

from .audio import deinterleave_stereo, pcm16_to_ulaw, ulaw_to_pcm16

PROTOKOLL_VERSION = "2"
PFLICHT_HEADER = (
    "audiohook-organization-id",
    "audiohook-correlation-id",
    "audiohook-session-id",
    "x-api-key",
)


def pruefe_header(headers: dict, api_key: str) -> Optional[str]:
    """Prüft den HTTP-Upgrade-Request. Gibt Fehlertext oder None zurück."""
    h = {k.lower(): v for k, v in headers.items()}
    fehlend = [k for k in PFLICHT_HEADER if k not in h]
    if fehlend:
        return f"fehlende Header: {', '.join(fehlend)}"
    if not hmac.compare_digest(h["x-api-key"].encode(), api_key.encode()):
        return "API-Key ungültig"
    return None


def _position(sekunden: float) -> str:
    return f"PT{sekunden:.3f}S"


def _parse_position(pos: str) -> float:
    try:
        return float(pos.strip().upper().removeprefix("PT").removesuffix("S"))
    except (AttributeError, ValueError):
        try:  # Python 3.8-Fallback ohne removeprefix
            return float(pos.upper().replace("PT", "").replace("S", ""))
        except Exception:
            return 0.0


@dataclass
class AudioHookSitzung:
    modus: str = "connector"              # "connector" | "monitor"
    sprachen: List[str] = field(default_factory=lambda: ["de-CH", "de-DE"])

    def __post_init__(self):
        self.id = ""
        self.seq = 0
        self.client_seq = 0
        self.position_s = 0.0
        self.offen = False
        self.pausiert = False
        self.beendet = False
        self.stereo = False
        self.conversation_id = ""
        self.sprache = ""
        self.input_variables: dict = {}
        self.output_variables: dict = {}
        self.protokollfehler: List[str] = []

    # -------------------------------------------------------------- Hilfen
    def _msg(self, typ: str, parameters: dict = None, position: bool = True) -> dict:
        self.seq += 1
        m = {
            "version": PROTOKOLL_VERSION,
            "type": typ,
            "seq": self.seq,
            "clientseq": self.client_seq,
            "id": self.id,
            "parameters": parameters or {},
        }
        if position:
            m["position"] = _position(self.position_s)
        return m

    def _fehler(self, code: int, text: str) -> dict:
        self.protokollfehler.append(text)
        return self._msg("error", {"code": code, "message": text, "retryable": False})

    # ------------------------------------------------------------ Eingang
    def text_empfangen(self, roh: str) -> List[dict]:
        """Ein JSON-Text-Frame von Genesys -> Liste von Antworten."""
        try:
            m = json.loads(roh)
        except json.JSONDecodeError:
            return [self._fehler(400, "kein gültiges JSON")]
        typ = m.get("type", "")
        seq = int(m.get("seq", 0))
        if self.client_seq and seq != self.client_seq + 1:
            # Lücke oder Duplikat: protokollieren, aber weiterarbeiten
            self.protokollfehler.append(f"seq-Sprung {self.client_seq} -> {seq}")
        self.client_seq = seq
        if "position" in m:
            self.position_s = _parse_position(m["position"])
        handler = getattr(self, f"_on_{typ}", None)
        if handler is None:
            return [self._fehler(405, f"unbekannter Nachrichtentyp: {typ}")]
        return handler(m)

    def _on_open(self, m: dict) -> List[dict]:
        if m.get("version") != PROTOKOLL_VERSION:
            return [self._fehler(400, f"Version {m.get('version')} nicht unterstützt")]
        self.id = m.get("id", "")
        p = m.get("parameters", {})
        self.conversation_id = p.get("conversationId", "")
        self.input_variables = p.get("inputVariables", {}) or {}
        angebot = p.get("media", [])
        wahl = self._medien_waehlen(angebot)
        if wahl is None:
            return [self._fehler(415, "kein unterstütztes Medienformat (PCMU 8000)")]
        self.stereo = len(wahl["channels"]) == 2
        gewuenscht = p.get("language", "")
        self.sprache = gewuenscht if gewuenscht in self.sprachen else self.sprachen[0]
        self.offen = True
        return [self._msg("opened", {
            "startPaused": False,
            "media": [wahl],
            "supportedLanguages": self.sprachen,
            "selectedLanguage": self.sprache,
        }, position=False)]

    def _medien_waehlen(self, angebot: list) -> Optional[dict]:
        """Monitor: beide Kanäle bevorzugt. Connector: nur 'external'."""
        pcmu = [a for a in angebot if a.get("format") == "PCMU" and a.get("rate") == 8000]
        if not pcmu:
            return None
        if self.modus == "monitor":
            for a in pcmu:
                if sorted(a.get("channels", [])) == ["external", "internal"]:
                    return a
        for a in pcmu:
            if a.get("channels") == ["external"]:
                return a
        a = pcmu[0]
        return {"type": "audio", "format": "PCMU", "channels": ["external"], "rate": 8000} \
            if "external" in a.get("channels", []) else None

    def _on_ping(self, m: dict) -> List[dict]:
        return [self._msg("pong")]

    def _on_pause(self, m: dict) -> List[dict]:
        self.pausiert = True
        return [self._msg("paused")]

    def _on_resume(self, m: dict) -> List[dict]:
        self.pausiert = False
        return [self._msg("resumed")]

    def _on_update(self, m: dict) -> List[dict]:
        lang = m.get("parameters", {}).get("language")
        if lang in self.sprachen:
            self.sprache = lang
        return []

    def _on_dtmf(self, m: dict) -> List[dict]:
        return []           # Ziffern holt die Pipeline über letzte_dtmf

    def _on_playback_started(self, m: dict) -> List[dict]:
        return []

    def _on_playback_completed(self, m: dict) -> List[dict]:
        return []

    def _on_error(self, m: dict) -> List[dict]:
        self.protokollfehler.append(f"Genesys meldet Fehler: {m.get('parameters')}")
        return []

    def _on_close(self, m: dict) -> List[dict]:
        self.offen = False
        self.beendet = True
        p = {"outputVariables": self.output_variables} if self.output_variables else {}
        return [self._msg("closed", p)]

    # ------------------------------------------------------------- Audio
    def audio_empfangen(self, daten: bytes) -> Optional[tuple]:
        """Binär-Frame -> (kunde_pcm16_8k, agent_pcm16_8k oder None)."""
        if not self.offen or self.pausiert:
            return None
        if self.stereo:
            ext, intern = deinterleave_stereo(daten)
            return ulaw_to_pcm16(ext), ulaw_to_pcm16(intern)
        return ulaw_to_pcm16(daten), None

    def audio_senden(self, pcm16_8k: bytes) -> Optional[bytes]:
        """PCM16 8 kHz -> mu-law-Binär-Frame (nur Connector, offen, nicht pausiert)."""
        if self.modus != "connector" or not self.offen or self.pausiert:
            return None
        return pcm16_to_ulaw(pcm16_8k)

    # ------------------------------------------------------------ Ausgang
    def barge_in(self) -> dict:
        """Genesys anweisen, bereits gepuffertes Bot-Audio zu verwerfen."""
        return self._msg("event", {"entities": [{"type": "barge_in", "data": {}}]})

    def beenden(self, aktion: str = "transfer", grund: str = "completed",
                variablen: dict = None, info: str = "") -> dict:
        """Bot beendet die Sitzung. ``aktion`` und ``variablen`` landen im
        Architect-Flow als Output-Variablen (z. B. Ziel-Queue, Anliegen)."""
        self.output_variables = {"action": aktion, **(variablen or {})}
        p = {"reason": grund, "outputVariables": self.output_variables}
        if info:
            p["info"] = info
        return self._msg("disconnect", p)
