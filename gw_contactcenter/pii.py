"""Erkennung und Pseudonymisierung von Personendaten mit Prüfsummen.

Erkannt werden IBAN (alle Länder, Prüfsumme ISO 13616 mod 97),
Zahlungskartennummern (Luhn), Schweizer AHV-Nummern (756..., EAN-13-
Prüfziffer), E-Mail-Adressen, Schweizer Telefonnummern und «Konto»-Angaben.
Die Prüfsummen halten Fehlalarme tief: Eine Bestellnummer 447100 bleibt
sichtbar, eine gültige Kartennummer nicht. Fast-Treffer (Prüfsumme falsch,
z. B. weil die Spracherkennung eine Ziffer falsch gehört hat) werden nicht
maskiert, aber als ``VERDACHT_<TYP>`` gemeldet, damit die Datenklasse
angehoben werden kann.

Namen, Adressen und Freitext-Gesundheitsangaben findet ein Regex nicht;
dafür im Projekt ein NER-Modell ergänzen (z. B. Microsoft Presidio).
"""

from __future__ import annotations

import re
from collections.abc import Callable

__all__ = [
    "PIITresor",
    "ahv_ok",
    "enthaelt_roh_pii",
    "iban_ok",
    "iban_stellen",
    "luhn_ok",
    "platzhalter_sprechbar",
]


def luhn_ok(ziffern: str) -> bool:
    """Luhn-Prüfung (ISO/IEC 7812) für eine reine Ziffernfolge."""
    if not ziffern.isdigit():
        return False
    d = [int(c) for c in ziffern][::-1]
    s = sum(d[0::2]) + sum(sum(divmod(2 * x, 10)) for x in d[1::2])
    return s % 10 == 0


def iban_ok(iban: str) -> bool:
    """IBAN-Prüfsumme nach ISO 13616 (mod 97 = 1); Leerzeichen erlaubt."""
    i = re.sub(r"\s", "", iban).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[0-9A-Z]{11,30}", i):
        return False
    umgestellt = i[4:] + i[:4]
    zahl = "".join(str(int(c, 36)) for c in umgestellt)
    return int(zahl) % 97 == 1


def ahv_ok(ahv: str) -> bool:
    """AHV-Nummer 756.XXXX.XXXX.XX: 13 Ziffern, Präfix 756, EAN-13-Prüfziffer."""
    d = [int(c) for c in re.sub(r"\D", "", ahv)]
    if len(d) != 13 or d[:3] != [7, 5, 6]:
        return False
    s = sum(x * (3 if i % 2 else 1) for i, x in enumerate(d[:12]))
    return (10 - s % 10) % 10 == d[12]


def iban_stellen(text: str) -> list[tuple[int, int, bool]]:
    """IBAN-Kandidaten beliebiger Länder: (start, ende, prüfsumme_ok).

    Ein Regex allein weiss nicht, wo eine IBAN endet («... 0130 00 und CH93
    ...»). Deshalb wird ab jedem Ländercode die längste Zeichenfolge gesucht,
    deren Prüfsumme stimmt; ohne Treffer gilt der Kandidat als Verdacht.
    """
    out: list[tuple[int, int, bool]] = []
    pos = 0
    for m in re.finditer(r"\b[A-Za-z]{2}\d{2}", text):
        if m.start() < pos:
            continue
        j, kompakt, enden = m.start(), 0, []
        while j < len(text) and kompakt < 34:
            ch = text[j]
            if ch.isalnum():
                kompakt += 1
                j += 1
                if kompakt >= 15 and (j == len(text) or not text[j].isalnum()):
                    enden.append(j)
            elif (
                ch.isspace()
                and j + 1 < len(text)
                and text[j + 1].isalnum()
                and not text[j - 1].isspace()
            ):
                j += 1
            else:
                break
        gueltig = [e for e in enden if iban_ok(text[m.start() : e])]
        if gueltig:
            out.append((m.start(), gueltig[-1], True))
            pos = gueltig[-1]
        elif enden and sum(c.isdigit() for c in text[m.start() : enden[0]]) >= 12:
            out.append((m.start(), enden[0], False))
            pos = enden[0]
    return out


# (Typ, Muster, Prüffunktion oder None). Reihenfolge zählt: spezifisch zuerst.
_PII_MUSTER: list[tuple[str, re.Pattern[str], Callable[[str], bool] | None]] = [
    ("AHV", re.compile(r"\b756[.\s]?\d{4}[.\s]?\d{4}[.\s]?\d{2}\b"), ahv_ok),
    ("KARTE", re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), lambda s: luhn_ok(re.sub(r"\D", "", s))),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), None),
    ("TELEFON", re.compile(r"(?:\+41|0041|\b0)\s?\d{2}\s?\d{3}\s?\d{2}\s?\d{2}\b"), None),
    ("KONTO", re.compile(r"\bKonto(?:nummer)?\s*(?:ist\s*)?\d{5,}\b", re.IGNORECASE), None),
]


class PIITresor:
    """Pseudonymisierung pro Anruf: gleicher Wert -> gleicher Platzhalter."""

    def __init__(self) -> None:
        self._wert_zu_token: dict[str, str] = {}
        self._token_zu_wert: dict[str, str] = {}
        self._zaehler: dict[str, int] = {}

    def _token(self, typ: str, wert: str) -> str:
        if wert not in self._wert_zu_token:
            self._zaehler[typ] = self._zaehler.get(typ, 0) + 1
            tok = f"[{typ}_{self._zaehler[typ]}]"
            self._wert_zu_token[wert] = tok
            self._token_zu_wert[tok] = wert
        return self._wert_zu_token[wert]

    def pseudonymisieren(self, text: str) -> tuple[str, list[str]]:
        """Gibt (Text mit Platzhaltern, gefundene Typen) zurück."""
        gefunden: list[str] = []
        stellen = iban_stellen(text)
        tokens = [self._token("IBAN", text[a:e]) if ok else None for a, e, ok in stellen]
        for (start, ende, ok), tok in reversed(list(zip(stellen, tokens, strict=True))):
            if ok and tok:
                text = text[:start] + tok + text[ende:]
        gefunden += ["IBAN" if ok else "VERDACHT_IBAN" for _, _, ok in stellen]
        for typ, muster, pruef in _PII_MUSTER:

            def ersetzen(
                m: re.Match[str], typ: str = typ, pruef: Callable[[str], bool] | None = pruef
            ) -> str:
                wert = m.group(0)
                if pruef and not pruef(wert):
                    gefunden.append(f"VERDACHT_{typ}")
                    return wert
                gefunden.append(typ)
                return self._token(typ, wert)

            text = muster.sub(ersetzen, text)
        return text, gefunden

    def wiederherstellen(self, text: str) -> str:
        """Klartext nur für Werkzeuge im eigenen System, nie für TTS."""
        for tok, wert in self._token_zu_wert.items():
            text = text.replace(tok, wert)
        return text


_SPRECHBAR = {
    "IBAN": "die genannte IBAN",
    "AHV": "die genannte AHV-Nummer",
    "KARTE": "die genannte Karte",
    "EMAIL": "die genannte E-Mail-Adresse",
    "TELEFON": "die genannte Telefonnummer",
    "KONTO": "das genannte Konto",
}


def platzhalter_sprechbar(text: str) -> str:
    """[IBAN_1] -> «die genannte IBAN»: TTS liest nie Platzhalter oder Nummern vor."""
    return re.sub(
        r"\[([A-Z]+)_\d+\]", lambda m: _SPRECHBAR.get(m.group(1), "die genannte Angabe"), text
    )


def enthaelt_roh_pii(text: str) -> list[str]:
    """Für den Antwort-Wächter: echte (prüfsummengültige) PII im Text?"""
    treffer = ["IBAN" for _, _, ok in iban_stellen(text) if ok]
    for typ, muster, pruef in _PII_MUSTER:
        treffer.extend(typ for m in muster.finditer(text) if pruef is None or pruef(m.group(0)))
    return treffer
