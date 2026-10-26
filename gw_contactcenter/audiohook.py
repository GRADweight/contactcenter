"""Genesys AudioHook / Audio Connector: Protokoll-Zustandsmaschine (Serverseite).

Genesys Cloud (Client) öffnet pro Anruf eine WebSocket-Verbindung zu diesem
Server. Über dieselbe Verbindung laufen Text-Frames (JSON-Steuernachrichten)
und Binär-Frames (Audio, PCMU 8 kHz).

Zwei Betriebsarten:

* ``connector`` (Audio Connector): Sprachagent im Architect-Flow,
  bidirektional. Der Server sendet Audio zurück und beendet mit
  ``disconnect``; dessen ``outputVariables`` (String-Werte) landen im
  Architect-Flow, z. B. Ziel-Queue und Ticketnummer.
* ``monitor`` (AudioHook Monitor): Mithören laufender Gespräche (beide
  Kanäle getrennt) für Live-Transkript und Agent Assist. Kein Rückkanal.

Protokollregeln nach der Genesys-Referenzimplementierung
(github.com/purecloudlabs/audiohook-reference-implementation,
``app/audiohook/src/protocol/message.ts`` und
``app/audiohook/src/server/serversessionimpl.ts``):

* Client-Nachrichten tragen ``version``, ``id``, ``type``, ``seq``,
  ``serverseq``, ``position``, ``parameters``; Server-Nachrichten
  ``version``, ``id``, ``type``, ``seq``, ``clientseq``, ``parameters``
  (keine ``position``).
* ``seq`` des Clients muss lückenlos steigen; ``serverseq`` darf die eigene
  ``seq`` nicht übersteigen; die Sitzungs-``id`` darf sich nicht ändern.
* Fehler meldet der Server mit ``disconnect`` (``reason``: ``completed``,
  ``unauthorized`` oder ``error``, optional ``info``), nicht mit ``error``.
* ``paused``/``resumed`` sendet der Client (z. B. Halten, sichere Pause);
  ``pause``/``resume`` wären Anfragen des Servers.
* ``close`` beantwortet der Server mit ``closed`` (leere Parameter).

Audio-Connector-spezifisch (in der Monitor-Referenz nicht enthalten, aus
Genesys-Communitybeiträgen und öffentlichen Integrationen übernommen; vor
Produktion mit der Genesys AudioHook Test Suite prüfen): ``inputVariables``
im ``open``, ``outputVariables`` im ``disconnect``, Ereignis-Entität
``barge_in``, Client-Nachrichten ``dtmf``, ``playback_started``,
``playback_completed``.

Die Klasse kennt kein Netzwerk: Nachrichten rein, Antworten raus. Dadurch
ist das Protokoll offline testbar und der Server (:mod:`.server`) dünn.
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field
from typing import Any

from .audio import deinterleave, pcm16_to_ulaw, ulaw_to_pcm16

PROTOKOLL_VERSION = "2"
PFLICHT_HEADER = (
    "audiohook-organization-id",
    "audiohook-correlation-id",
    "audiohook-session-id",
    "x-api-key",
)
MAX_TEXT_BYTE = 65535  # Grenze der Referenzimplementierung
_CLIENT_TYPEN = {
    "open",
    "close",
    "discarded",
    "error",
    "paused",
    "ping",
    "resumed",
    "update",
    # Audio Connector:
    "dtmf",
    "playback_started",
    "playback_completed",
}

Nachricht = dict[str, Any]


def pruefe_header(headers: dict[str, str], api_key: str) -> str | None:
    """Prüft Pflicht-Header und API-Key des Upgrade-Requests.

    Gibt einen Fehlertext oder None zurück. Die Signaturprüfung
    (:func:`.signatur.pruefe_signatur`) kommt im Server zusätzlich dazu.
    """
    h = {k.lower(): v for k, v in headers.items()}
    fehlend = [k for k in PFLICHT_HEADER if not h.get(k)]
    if fehlend:
        return f"fehlende Header: {', '.join(fehlend)}"
    if not api_key or not hmac.compare_digest(h["x-api-key"].encode(), api_key.encode()):
        return "API-Key ungültig"
    return None


def _parse_position(pos: object) -> float | None:
    """ISO-8601-Dauer «PT1.25S» -> Sekunden; None, wenn ungültig."""
    if not isinstance(pos, str) or not pos.startswith("PT") or not pos.endswith("S"):
        return None
    try:
        wert = float(pos[2:-1])
    except ValueError:
        return None
    return wert if wert >= 0 else None


@dataclass
class AudioHookSitzung:
    modus: str = "connector"  # "connector" | "monitor"
    sprachen: list[str] = field(default_factory=lambda: ["de-CH", "de-DE"])

    def __post_init__(self) -> None:
        if self.modus not in ("connector", "monitor"):
            raise ValueError("modus muss 'connector' oder 'monitor' sein")
        self.id = ""
        self.seq = 0
        self.client_seq = 0
        self.position_s = 0.0
        self.zustand = "neu"  # neu | aktiv | pausiert | geschlossen | fehler
        self.kanaele: list[str] = []
        self.conversation_id = ""
        self.sprache = ""
        self.input_variables: dict[str, Any] = {}
        self.dtmf: list[str] = []
        self.protokoll: list[str] = []  # Hinweise und Fehler, für Logs

    # -------------------------------------------------------------- Zustand
    @property
    def offen(self) -> bool:
        return self.zustand in ("aktiv", "pausiert")

    @property
    def pausiert(self) -> bool:
        return self.zustand == "pausiert"

    @property
    def beendet(self) -> bool:
        return self.zustand in ("geschlossen", "fehler")

    # -------------------------------------------------------------- Ausgang
    def _msg(self, typ: str, parameters: dict[str, Any] | None = None) -> Nachricht:
        self.seq += 1
        return {
            "version": PROTOKOLL_VERSION,
            "id": self.id,
            "type": typ,
            "seq": self.seq,
            "clientseq": self.client_seq,
            "parameters": parameters or {},
        }

    def _client_fehler(self, info: str) -> list[Nachricht]:
        self.protokoll.append(f"Client-Fehler: {info}")
        if self.beendet:
            return []
        self.zustand = "fehler"
        return [self._msg("disconnect", {"reason": "error", "info": f"Client Error: {info}"})]

    def unberechtigt(self, info: str = "") -> Nachricht:
        """Sitzung wegen fehlender Berechtigung beenden."""
        self.zustand = "fehler"
        p: dict[str, Any] = {"reason": "unauthorized"}
        if info:
            p["info"] = info
        return self._msg("disconnect", p)

    def barge_in(self) -> Nachricht:
        """Genesys anweisen, bereits gepuffertes Bot-Audio zu verwerfen."""
        return self._msg("event", {"entities": [{"type": "barge_in", "data": {}}]})

    def beenden(
        self,
        aktion: str = "transfer",
        grund: str = "completed",
        variablen: dict[str, object] | None = None,
        info: str = "",
    ) -> Nachricht:
        """Bot beendet die Sitzung. ``aktion`` und ``variablen`` gehen als
        ``outputVariables`` (nur String-Werte) an den Architect-Flow."""
        if grund not in ("completed", "unauthorized", "error"):
            raise ValueError(f"disconnect-Grund {grund!r} unzulässig")
        output = {"action": aktion, **(variablen or {})}
        p: dict[str, Any] = {
            "reason": grund,
            "outputVariables": {str(k): "" if v is None else str(v) for k, v in output.items()},
        }
        if info:
            p["info"] = info
        return self._msg("disconnect", p)

    # -------------------------------------------------------------- Eingang
    def text_empfangen(self, roh: str) -> list[Nachricht]:
        """Ein JSON-Text-Frame von Genesys -> Liste von Antworten."""
        if self.zustand == "fehler":
            return []
        if len(roh.encode()) > MAX_TEXT_BYTE:
            return self._client_fehler("Textnachricht grösser als 64 KiB")
        try:
            m = json.loads(roh)
        except json.JSONDecodeError:
            return self._client_fehler("Textnachricht ist kein gültiges JSON")
        if not isinstance(m, dict):
            return self._client_fehler("Nachricht ist kein JSON-Objekt")
        typ, seq, serverseq = m.get("type"), m.get("seq"), m.get("serverseq")
        if (
            m.get("version") != PROTOKOLL_VERSION
            or not isinstance(m.get("id"), str)
            or not isinstance(typ, str)
            or isinstance(seq, bool)
            or not isinstance(seq, int)
            or isinstance(serverseq, bool)
            or not isinstance(serverseq, int)
            or _parse_position(m.get("position")) is None
            or not isinstance(m.get("parameters"), dict)
        ):
            return self._client_fehler("keine wohlgeformte Client-Nachricht")
        if seq != self.client_seq + 1:
            return self._client_fehler(f"seq {seq} folgt nicht auf {self.client_seq}")
        self.client_seq = seq
        if serverseq > self.seq:
            return self._client_fehler("serverseq grösser als gesendete seq")
        if self.id and m["id"] != self.id:
            return self._client_fehler("Sitzungs-ID hat gewechselt")
        if not self.id:
            self.id = m["id"]
        self.position_s = _parse_position(m["position"]) or self.position_s
        if typ not in _CLIENT_TYPEN:
            return self._client_fehler(f"{typ!r} ist keine unterstützte Client-Nachricht")
        if typ != "open" and self.zustand == "neu":
            return self._client_fehler(f"{typ!r} vor 'open'")
        handler = getattr(self, f"_on_{typ}")
        antwort: list[Nachricht] = handler(m["parameters"])
        return antwort

    def _on_open(self, p: dict[str, Any]) -> list[Nachricht]:
        if self.zustand != "neu":
            self.protokoll.append("'open' in laufender Sitzung ignoriert")
            return []
        self.conversation_id = str(p.get("conversationId", ""))
        iv = p.get("inputVariables")
        self.input_variables = iv if isinstance(iv, dict) else {}
        media = p.get("media")
        wahl = self._medien_waehlen(media if isinstance(media, list) else [])
        if wahl is None:
            return self._client_fehler("kein unterstütztes Medienformat angeboten (PCMU 8000)")
        self.kanaele = list(wahl["channels"])
        gewuenscht = p.get("language", "")
        self.sprache = gewuenscht if gewuenscht in self.sprachen else self.sprachen[0]
        self.zustand = "aktiv"
        opened: dict[str, Any] = {"startPaused": False, "media": [wahl]}
        if p.get("supportedLanguages") is True:
            opened["supportedLanguages"] = list(self.sprachen)
        return [self._msg("opened", opened)]

    def _medien_waehlen(self, angebot: list[Any]) -> dict[str, Any] | None:
        """Monitor: beide Kanäle bevorzugt. Connector: nur 'external'."""
        pcmu = [
            a
            for a in angebot
            if isinstance(a, dict)
            and a.get("type", "audio") == "audio"
            and a.get("format") == "PCMU"
            and a.get("rate") == 8000
            and isinstance(a.get("channels"), list)
        ]
        if self.modus == "monitor":
            for a in pcmu:
                if sorted(a["channels"]) == ["external", "internal"]:
                    return {
                        "type": "audio",
                        "format": "PCMU",
                        "channels": a["channels"],
                        "rate": 8000,
                    }
        for a in pcmu:
            if a["channels"] == ["external"]:
                return {"type": "audio", "format": "PCMU", "channels": ["external"], "rate": 8000}
        return None

    def _on_ping(self, p: dict[str, Any]) -> list[Nachricht]:
        return [self._msg("pong")]

    def _on_paused(self, p: dict[str, Any]) -> list[Nachricht]:
        if self.zustand == "aktiv":
            self.zustand = "pausiert"
        return []

    def _on_resumed(self, p: dict[str, Any]) -> list[Nachricht]:
        if self.zustand == "pausiert":
            self.zustand = "aktiv"
        return []

    def _on_update(self, p: dict[str, Any]) -> list[Nachricht]:
        sprache = p.get("language")
        if isinstance(sprache, str) and sprache in self.sprachen:
            self.sprache = sprache
        return []

    def _on_discarded(self, p: dict[str, Any]) -> list[Nachricht]:
        self.protokoll.append(f"Genesys hat Audio verworfen: {p}")
        return []

    def _on_error(self, p: dict[str, Any]) -> list[Nachricht]:
        self.protokoll.append(f"Genesys meldet Fehler: {p}")
        return []

    def _on_dtmf(self, p: dict[str, Any]) -> list[Nachricht]:
        ziffer = p.get("digit")
        if isinstance(ziffer, str) and len(ziffer) == 1 and ziffer in "0123456789*#":
            self.dtmf.append(ziffer)
        return []

    def _on_playback_started(self, p: dict[str, Any]) -> list[Nachricht]:
        return []

    def _on_playback_completed(self, p: dict[str, Any]) -> list[Nachricht]:
        return []

    def _on_close(self, p: dict[str, Any]) -> list[Nachricht]:
        self.zustand = "geschlossen"
        return [self._msg("closed")]

    # ---------------------------------------------------------------- Audio
    def audio_empfangen(self, daten: bytes) -> list[bytes] | None:
        """Binär-Frame -> PCM16 8 kHz pro Kanal (Reihenfolge wie ``kanaele``).

        Während einer Pause wird Audio verworfen (None). Audio vor ``open``
        oder mit falscher Länge ist ein Protokollfehler (ValueError).
        """
        if self.zustand == "pausiert":
            return None
        if self.zustand != "aktiv":
            raise ValueError(f"Audio im Zustand {self.zustand!r}")
        return [ulaw_to_pcm16(k) for k in deinterleave(daten, len(self.kanaele))]

    def kanal(self, pcm_pro_kanal: list[bytes], name: str) -> bytes | None:
        """PCM eines benannten Kanals ('external' = Kundschaft)."""
        return pcm_pro_kanal[self.kanaele.index(name)] if name in self.kanaele else None

    def audio_senden(self, pcm16_8k: bytes) -> bytes | None:
        """PCM16 8 kHz -> mu-law (nur Connector, aktiv)."""
        if self.modus != "connector" or self.zustand != "aktiv":
            return None
        return pcm16_to_ulaw(pcm16_8k)
