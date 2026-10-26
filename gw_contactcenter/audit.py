"""Manipulationserkennendes Audit-Protokoll (HMAC-Hash-Kette, JSON Lines).

Jeder Eintrag enthält den Prüfwert seines Vorgängers. Wird nachträglich ein
Eintrag geändert, gelöscht oder eingefügt, bricht die Kette und
:meth:`AuditLog.pruefen` meldet die Stelle.

Der Prüfwert ist ein HMAC-SHA256 mit geheimem Schlüssel. Ohne Schlüssel
(reines SHA-256) könnte jede Person mit Schreibzugriff die ganze Kette nach
einer Änderung neu berechnen; deshalb ist der Schlüssel Pflicht. Ohne
Schlüssel startet das Protokoll nur, wenn ``unsigniert_erlaubt=True``
ausdrücklich gesetzt ist (Entwicklungsmodus, Tests); solche Einträge sind
als ``"alg": "sha256"`` gekennzeichnet und werden mit Schlüssel nie als
gültig akzeptiert.

Grenzen: Die Kette erkennt Manipulation, sie verhindert sie nicht. Wer den
Schlüssel besitzt, kann eine neue gültige Kette erzeugen, und das
Abschneiden der letzten Einträge fällt ohne externen Anker (z. B. regelmässig
exportierter letzter Prüfwert, WORM-Speicher) nicht auf.

Ins Audit gehören Entscheidungen (Anliegen, Modell, Region, Werkzeug,
Bestätigung, Übergabegrund), keine Rohdaten: Texte nur pseudonymisiert.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable
from typing import Any

__all__ = ["MIN_SCHLUESSEL_BYTE", "AuditFehler", "AuditKetteDefekt", "AuditLog"]

_START = "0" * 64
MIN_SCHLUESSEL_BYTE = 32
_KERN = ("nr", "zeit", "ereignis", "daten", "vorher", "alg")


class AuditFehler(RuntimeError):
    """Konfigurationsfehler: Audit kann nicht sicher betrieben werden."""


class AuditKetteDefekt(AuditFehler):
    """Eine bestehende Audit-Datei ist manipuliert oder beschädigt."""

    def __init__(self, nummer: int, pfad: str | None = None) -> None:
        self.nummer = nummer
        wo = f" in {pfad}" if pfad else ""
        super().__init__(f"Audit-Kette{wo} ab Eintrag {nummer} defekt")


def _kanonisch(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


class AuditLog:
    """Append-only-Protokoll mit Hash-Kette.

    ``schluessel``: mindestens 32 Byte, z. B. ``secrets.token_bytes(32)``.
    """

    def __init__(
        self,
        pfad: str | None = None,
        *,
        schluessel: bytes | None = None,
        unsigniert_erlaubt: bool = False,
        uhr: Callable[[], float] = time.time,
    ) -> None:
        if schluessel:
            if len(schluessel) < MIN_SCHLUESSEL_BYTE:
                raise AuditFehler(
                    f"Audit-Schlüssel zu kurz: {len(schluessel)} Byte, mindestens "
                    f"{MIN_SCHLUESSEL_BYTE} Byte nötig"
                )
            self.alg = "hmac-sha256"
        elif unsigniert_erlaubt:
            self.alg = "sha256"
        else:
            raise AuditFehler(
                "Audit ohne HMAC-Schlüssel nicht erlaubt: AUDIT_HMAC_KEY setzen "
                "oder ausdrücklich den Entwicklungsmodus wählen"
            )
        self.pfad = pfad
        self._schluessel = schluessel or b""
        self.uhr = uhr
        self.eintraege: list[dict[str, Any]] = []
        self._letzter = _START
        if pfad and os.path.exists(pfad):
            with open(pfad, encoding="utf-8") as f:
                try:
                    self.eintraege = [json.loads(z) for z in f if z.strip()]
                except json.JSONDecodeError as e:
                    raise AuditKetteDefekt(len(self.eintraege) + 1, pfad) from e
            defekt = self.pruefen()
            if defekt is not None:
                raise AuditKetteDefekt(defekt, pfad)
            if self.eintraege:
                self._letzter = self.eintraege[-1]["hash"]

    def _pruefwert(self, kern: dict[str, Any]) -> str:
        roh = _kanonisch(kern)
        if self.alg == "hmac-sha256":
            return hmac.new(self._schluessel, roh, hashlib.sha256).hexdigest()
        return hashlib.sha256(roh).hexdigest()

    def schreiben(self, ereignis: str, **daten: Any) -> dict[str, Any]:
        kern: dict[str, Any] = {
            "nr": len(self.eintraege) + 1,
            "zeit": round(self.uhr(), 3),
            "ereignis": ereignis,
            "daten": daten,
            "vorher": self._letzter,
            "alg": self.alg,
        }
        eintrag = dict(kern, hash=self._pruefwert(kern))
        if self.pfad:
            with open(self.pfad, "a", encoding="utf-8") as f:
                f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
        self.eintraege.append(eintrag)
        self._letzter = eintrag["hash"]
        return eintrag

    @property
    def letzter_pruefwert(self) -> str:
        """Für einen externen Anker (z. B. täglicher Export an ein anderes System)."""
        return self._letzter

    def pruefen(self) -> int | None:
        """None = Kette intakt, sonst Nummer des ersten defekten Eintrags."""
        vorher = _START
        for i, e in enumerate(self.eintraege, start=1):
            try:
                kern = {k: e[k] for k in _KERN}
                ok = (
                    e["nr"] == i
                    and e["vorher"] == vorher
                    and e["alg"] == self.alg
                    and hmac.compare_digest(str(e["hash"]), self._pruefwert(kern))
                )
            except (KeyError, TypeError):
                ok = False
            if not ok:
                return i
            vorher = e["hash"]
        return None
