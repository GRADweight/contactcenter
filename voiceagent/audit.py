"""Manipulationssicheres Audit-Protokoll (Hash-Kette, JSON Lines).

Jeder Eintrag enthält den Hash seines Vorgängers. Wird nachträglich ein
Eintrag geändert, gelöscht oder eingefügt, bricht die Kette und
``pruefen()`` zeigt die Stelle. Mit Schlüssel (HMAC) kann auch niemand
ohne Schlüssel eine gültige neue Kette erzeugen.

Ins Audit gehören Entscheidungen (welches Anliegen, welches Modell,
welche Region, welches Werkzeug, wer bestätigt, warum übergeben),
NICHT Rohdaten: Texte nur pseudonymisiert (s. gateway.PIITresor).

Für Revisionssicherheit die Datei zusätzlich auf WORM-Speicher
ablegen (z. B. S3 Object Lock oder unveränderliche Snapshots).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Callable, List, Optional

_START = "0" * 64


def _kanonisch(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


class AuditLog:
    def __init__(self, pfad: Optional[str] = None, schluessel: bytes = b"",
                 uhr: Callable[[], float] = time.time):
        self.pfad = pfad
        self.schluessel = schluessel
        self.uhr = uhr
        self.eintraege: List[dict] = []
        self._letzter = _START
        if pfad and os.path.exists(pfad):
            with open(pfad, encoding="utf-8") as f:
                self.eintraege = [json.loads(z) for z in f if z.strip()]
            if self.eintraege:
                self._letzter = self.eintraege[-1]["hash"]

    def _hash(self, kern: dict) -> str:
        roh = _kanonisch(kern)
        if self.schluessel:
            return hmac.new(self.schluessel, roh, hashlib.sha256).hexdigest()
        return hashlib.sha256(roh).hexdigest()

    def schreiben(self, ereignis: str, **daten) -> dict:
        kern = {"nr": len(self.eintraege) + 1, "zeit": round(self.uhr(), 3),
                "ereignis": ereignis, "daten": daten, "vorher": self._letzter}
        eintrag = dict(kern, hash=self._hash(kern))
        self.eintraege.append(eintrag)
        self._letzter = eintrag["hash"]
        if self.pfad:
            with open(self.pfad, "a", encoding="utf-8") as f:
                f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
        return eintrag

    def pruefen(self) -> Optional[int]:
        """None = Kette intakt, sonst Nummer des ersten defekten Eintrags."""
        vorher = _START
        for i, e in enumerate(self.eintraege, start=1):
            kern = {k: e[k] for k in ("nr", "zeit", "ereignis", "daten", "vorher")}
            if e.get("nr") != i or e["vorher"] != vorher or e["hash"] != self._hash(kern):
                return i
            vorher = e["hash"]
        return None
