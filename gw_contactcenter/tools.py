"""Werkzeuge (Function Calling) mit Leitplanken.

Ein Sprachmodell schreibt nie direkt in ein System. Ein Werkzeugaufruf wird
nur ausgeführt, wenn er alle Prüfungen besteht:

1. **Erlaubt?** Nur Werkzeuge, die das erkannte Anliegen freigibt (Allowlist
   pro Anliegen, :mod:`.policy`). «Lösche alle Kunden» scheitert hier, nicht
   erst im Modell.
2. **Gültig?** Argumente gegen ein JSON-Schema (Teilmenge) prüfen.
3. **Bestätigt?** Werkzeuge mit Wirkung brauchen ab R1 ein «Ja» der
   Kundschaft; der Bot sagt vorher, was er tun wird.
4. **Einmalig?** Idempotenzschlüssel: Ein wiederholter Aufruf (Netz-Retry,
   zweimal «Ja») erzeugt kein zweites Ticket.
5. **Protokolliert?** Jeder Aufruf landet im Audit.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .audit import AuditLog

JSON = Any  # JSON-Wert aus einem Werkzeugaufruf


class SchemaFehler(ValueError):
    pass


_TYPEN: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
}


def validiere(schema: dict[str, Any], wert: JSON, pfad: str = "$") -> None:
    """Kleiner JSON-Schema-Validator (Teilmenge: type, properties, required,
    additionalProperties, enum, pattern, maxLength, minimum, maximum, items)."""
    typ = schema.get("type")
    if typ:
        if typ not in _TYPEN:
            raise SchemaFehler(f"{pfad}: Schematyp {typ!r} nicht unterstützt")
        if typ in ("integer", "number") and isinstance(wert, bool):
            raise SchemaFehler(f"{pfad}: Zahl erwartet, nicht bool")
        if not isinstance(wert, _TYPEN[typ]):
            raise SchemaFehler(f"{pfad}: {typ} erwartet, {type(wert).__name__} erhalten")
    if "enum" in schema and wert not in schema["enum"]:
        raise SchemaFehler(f"{pfad}: {wert!r} nicht in {schema['enum']}")
    if isinstance(wert, str):
        if "maxLength" in schema and len(wert) > schema["maxLength"]:
            raise SchemaFehler(f"{pfad}: länger als {schema['maxLength']}")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], wert):
            raise SchemaFehler(f"{pfad}: passt nicht auf {schema['pattern']}")
    if isinstance(wert, (int, float)) and not isinstance(wert, bool):
        if "minimum" in schema and wert < schema["minimum"]:
            raise SchemaFehler(f"{pfad}: kleiner als {schema['minimum']}")
        if "maximum" in schema and wert > schema["maximum"]:
            raise SchemaFehler(f"{pfad}: grösser als {schema['maximum']}")
    if isinstance(wert, dict):
        props: dict[str, Any] = schema.get("properties", {})
        for pflicht in schema.get("required", []):
            if pflicht not in wert:
                raise SchemaFehler(f"{pfad}.{pflicht}: Pflichtfeld fehlt")
        if schema.get("additionalProperties") is False:
            extra = set(wert) - set(props)
            if extra:
                raise SchemaFehler(f"{pfad}: unbekannte Felder {sorted(extra)}")
        for k, v in wert.items():
            if k in props:
                validiere(props[k], v, f"{pfad}.{k}")
    if isinstance(wert, list) and "items" in schema:
        for i, v in enumerate(wert):
            validiere(schema["items"], v, f"{pfad}[{i}]")


@dataclass
class Werkzeug:
    name: str
    beschreibung: str
    schema: dict[str, Any]
    risiko: int  # R0-R3
    wirkung: bool  # schreibt oder ändert etwas
    ausfuehren: Callable[[dict[str, Any]], dict[str, Any]]
    bestaetigungstext: str = ""  # Vorlage, z. B. "ein Ticket zum Thema {thema} erfassen"

    @property
    def braucht_bestaetigung(self) -> bool:
        return self.wirkung and self.risiko >= 1

    def openai_definition(self) -> dict[str, Any]:
        """Format für OpenAI-kompatible APIs (vLLM, Ollama, LiteLLM, ...)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.beschreibung,
                "parameters": self.schema,
            },
        }


@dataclass
class Ergebnis:
    status: str  # ok | bestaetigung_noetig | abgelehnt | fehler | duplikat
    daten: dict[str, Any] = field(default_factory=dict)
    text: str = ""


class Werkzeugkasten:
    def __init__(self, audit: AuditLog) -> None:
        self.werkzeuge: dict[str, Werkzeug] = {}
        self.audit = audit
        self._erledigt: dict[str, dict[str, Any]] = {}

    def registrieren(self, w: Werkzeug) -> None:
        self.werkzeuge[w.name] = w

    def definitionen(self, erlaubt: list[str]) -> list[dict[str, Any]]:
        return [self.werkzeuge[n].openai_definition() for n in erlaubt if n in self.werkzeuge]

    @staticmethod
    def _schluessel(anruf_id: str, name: str, args: dict[str, Any]) -> str:
        roh = json.dumps([anruf_id, name, args], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(roh.encode()).hexdigest()[:16]

    def aufrufen(
        self,
        name: str,
        args: dict[str, Any],
        *,
        erlaubt: list[str],
        anruf_id: str,
        bestaetigt: bool = False,
    ) -> Ergebnis:
        if name not in erlaubt or name not in self.werkzeuge:
            self.audit.schreiben("tool_abgelehnt", anruf=anruf_id, tool=name, grund="nicht erlaubt")
            return Ergebnis(
                "abgelehnt", text=f"Werkzeug {name} ist für dieses Anliegen nicht freigegeben"
            )
        w = self.werkzeuge[name]
        try:
            validiere(w.schema, args)
        except SchemaFehler as e:
            self.audit.schreiben("tool_abgelehnt", anruf=anruf_id, tool=name, grund=str(e))
            return Ergebnis("fehler", text=str(e))
        if w.braucht_bestaetigung and not bestaetigt:
            frage = (w.bestaetigungstext or f"{name} ausführen").format(**args)
            return Ergebnis(
                "bestaetigung_noetig",
                {"tool": name, "args": args},
                text=f"Soll ich {frage}? Bitte sagen Sie Ja oder Nein.",
            )
        key = self._schluessel(anruf_id, name, args)
        if w.wirkung and key in self._erledigt:
            return Ergebnis("duplikat", self._erledigt[key], "bereits erledigt")
        try:
            daten = w.ausfuehren(args)
        except Exception as e:  # noqa: BLE001 - Systemfehler nie an die Kundschaft durchreichen
            self.audit.schreiben("tool_fehler", anruf=anruf_id, tool=name, fehler=type(e).__name__)
            return Ergebnis("fehler", text="Das System ist gerade nicht erreichbar")
        if w.wirkung:
            self._erledigt[key] = daten
        self.audit.schreiben(
            "tool_ok",
            anruf=anruf_id,
            tool=name,
            risiko=w.risiko,
            bestaetigt=bestaetigt,
            idempotenz=key,
        )
        return Ergebnis("ok", daten)


# ---------------------------------------------------------------------------
# Beispiel-Backend (ersetzt im Projekt Shop, CRM, Kalender)
# ---------------------------------------------------------------------------


class DemoBackend:
    """In-Memory-Shop/CRM mit erfundenen Daten für Tests und Demos."""

    def __init__(self) -> None:
        self.bestellungen: dict[str, dict[str, str]] = {
            "447100": {"status": "unterwegs", "zustellung": "morgen bis 12 Uhr"},
            "512233": {"status": "zugestellt", "zustellung": "gestern"},
        }
        self.tickets: list[dict[str, Any]] = []
        oeffnung = "Wir sind Montag bis Freitag von 8 bis 18 Uhr erreichbar."
        self.wissen = {
            "öffnungszeit": oeffnung,
            "offen": oeffnung,
            "preis": "Die aktuellen Preise finden Sie im Onlineshop; gerne schicke ich Ihnen "
            "eine Offerte.",
            "offerte": "Für Offerten ab 10 Stück erstellt unser Verkauf innert zwei Arbeitstagen "
            "eine Offerte.",
            "service": "Sie erreichen uns per Telefon, E-Mail oder Chat.",
        }

    def bestellstatus(self, a: dict[str, Any]) -> dict[str, Any]:
        b = self.bestellungen.get(a["bestellnummer"])
        return {"gefunden": bool(b), **(b or {})}

    def ticket(self, a: dict[str, Any]) -> dict[str, Any]:
        t = {"ticket": f"T-{len(self.tickets) + 1:05d}", **a}
        self.tickets.append(t)
        return t

    def wissen_suchen(self, a: dict[str, Any]) -> dict[str, Any]:
        q = str(a["frage"]).lower()
        for k, v in self.wissen.items():
            if k in q:
                return {"treffer": v, "quelle": f"wissensbasis/{k}"}
        return {"treffer": "", "quelle": ""}


def standard_werkzeuge(backend: DemoBackend, audit: AuditLog) -> Werkzeugkasten:
    k = Werkzeugkasten(audit)
    k.registrieren(
        Werkzeug(
            "bestellstatus_abfragen",
            "Status einer Bestellung nachschlagen",
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["bestellnummer"],
                "properties": {"bestellnummer": {"type": "string", "pattern": r"\d{6}"}},
            },
            risiko=0,
            wirkung=False,
            ausfuehren=backend.bestellstatus,
        )
    )
    k.registrieren(
        Werkzeug(
            "ticket_erstellen",
            "Ticket für einen Fachbereich erstellen",
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["thema", "bereich"],
                "properties": {
                    "thema": {"type": "string", "maxLength": 200},
                    "bereich": {"type": "string", "maxLength": 60},
                    "prioritaet": {"type": "integer", "minimum": 1, "maximum": 3},
                },
            },
            risiko=1,
            wirkung=True,
            ausfuehren=backend.ticket,
            bestaetigungstext="ein Ticket zum Thema {thema} erfassen",
        )
    )
    k.registrieren(
        Werkzeug(
            "wissen_suchen",
            "Frage in der freigegebenen Wissensbasis nachschlagen",
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["frage"],
                "properties": {"frage": {"type": "string", "maxLength": 500}},
            },
            risiko=0,
            wirkung=False,
            ausfuehren=backend.wissen_suchen,
        )
    )
    return k
