"""Prüfung der HTTP-Nachrichtensignatur beim AudioHook-Verbindungsaufbau.

Genesys Cloud signiert den WebSocket-Upgrade-Request nach «HTTP Message
Signatures» (IETF, heute RFC 9421) mit HMAC-SHA256. Schlüssel ist das in der
Genesys-Integration hinterlegte Client Secret (Base64), ``keyid`` ist der
API-Key. Signiert werden mindestens::

    @request-target  @authority  audiohook-organization-id
    audiohook-session-id  audiohook-correlation-id  x-api-key

Umsetzung nach der Referenzimplementierung von Genesys
(github.com/purecloudlabs/audiohook-reference-implementation, Ordner
``app/audiohook/src/httpsignature`` und ``app/src/authenticator.ts``) und
gegen deren Testvektoren geprüft (``tests/test_signatur.py``):

* Signaturbasis aus den gelisteten Komponenten, abgeschlossen mit
  ``"@signature-params"`` (aus den geparsten Werten neu serialisiert).
* ``created`` darf nicht in der Zukunft liegen (3 s Toleranz), Höchstalter
  10 s, ``expires`` wird beachtet.
* ``nonce`` ist Pflicht (mindestens 22 Zeichen); zusätzlich zur Referenz
  wird jede Nonce innerhalb ihres Gültigkeitsfensters nur einmal akzeptiert
  (Schutz gegen Wiedereinspielen).
* ``keyid`` muss dem Header ``X-API-KEY`` entsprechen; bei unbekanntem Key
  wird mit einem Zufallsschlüssel weitergerechnet, damit die Antwortzeit
  nichts verrät.

Der Parser deckt die für Signaturen nötige Teilmenge von RFC 8941
(Structured Field Values) ab: Dictionary, Inner List, Parameter, String,
Token, Integer, Boolean, Byte Sequence.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import string
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

__all__ = [
    "AUDIOHOOK_KOMPONENTEN",
    "NonceSpeicher",
    "Pruefergebnis",
    "StrukturFehler",
    "Token",
    "pruefe_signatur",
    "signieren",
]

AUDIOHOOK_KOMPONENTEN = (
    "@request-target",
    "@authority",
    "audiohook-organization-id",
    "audiohook-session-id",
    "audiohook-correlation-id",
    "x-api-key",
)
MAX_UHRENABWEICHUNG_S = 3
MAX_ALTER_S = 10
MIN_NONCE_LAENGE = 22
_ABGELEITET = {
    "@method",
    "@authority",
    "@scheme",
    "@target-uri",
    "@request-target",
    "@path",
    "@query",
    "@status",
}


class StrukturFehler(ValueError):
    """Header-Wert ist kein gültiges Structured Field."""


class Token(str):
    """RFC-8941-Token (im Unterschied zu einem String in Anführungszeichen)."""


BareItem = int | float | str | bool | bytes
Parameter = list[tuple[str, BareItem]]


@dataclass(frozen=True)
class Item:
    wert: BareItem
    parameter: Parameter = field(default_factory=list)


@dataclass(frozen=True)
class InnerList:
    items: list[Item]
    parameter: Parameter = field(default_factory=list)


# ---------------------------------------------------------------------------
# RFC 8941: Parser und Serialisierer (Teilmenge)
# ---------------------------------------------------------------------------

_LCALPHA = set(string.ascii_lowercase)
_SCHLUESSEL_ZEICHEN = _LCALPHA | set(string.digits) | set("_-.*")
_TCHAR = set("!#$%&'*+-.^_`|~") | set(string.ascii_letters) | set(string.digits)


class _Leser:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0

    def ende(self) -> bool:
        return self.i >= len(self.s)

    def zeichen(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def leerraum(self, nur_sp: bool = False) -> None:
        erlaubt = " " if nur_sp else " \t"
        while not self.ende() and self.s[self.i] in erlaubt:
            self.i += 1

    def erwarte(self, z: str) -> None:
        if self.zeichen() != z:
            raise StrukturFehler(f"erwartet {z!r} an Position {self.i}")
        self.i += 1


def _schluessel(r: _Leser) -> str:
    z = r.zeichen()
    if not (z in _LCALPHA or z == "*"):
        raise StrukturFehler(f"ungültiger Schlüssel an Position {r.i}")
    start = r.i
    while not r.ende() and r.zeichen() in _SCHLUESSEL_ZEICHEN:
        r.i += 1
    return r.s[start : r.i]


def _bare_item(r: _Leser) -> BareItem:
    z = r.zeichen()
    if z == "-" or z.isdigit():
        return _zahl(r)
    if z == '"':
        return _string(r)
    if z == "*" or z.isalpha():
        start = r.i
        r.i += 1
        while not r.ende() and (r.zeichen() in _TCHAR or r.zeichen() in ":/"):
            r.i += 1
        return Token(r.s[start : r.i])
    if z == ":":
        r.i += 1
        ende = r.s.find(":", r.i)
        if ende < 0:
            raise StrukturFehler("Byte Sequence nicht abgeschlossen")
        roh = r.s[r.i : ende]
        r.i = ende + 1
        try:
            return base64.b64decode(roh, validate=True)
        except binascii.Error as e:
            raise StrukturFehler("Byte Sequence ist kein Base64") from e
    if z == "?":
        r.i += 1
        b = r.zeichen()
        if b not in ("0", "1"):
            raise StrukturFehler("Boolean muss ?0 oder ?1 sein")
        r.i += 1
        return b == "1"
    raise StrukturFehler(f"unerwartetes Zeichen {z!r} an Position {r.i}")


def _zahl(r: _Leser) -> int | float:
    start = r.i
    if r.zeichen() == "-":
        r.i += 1
    if not r.zeichen().isdigit():
        raise StrukturFehler("Ziffer erwartet")
    while not r.ende() and (r.zeichen().isdigit() or r.zeichen() == "."):
        r.i += 1
    roh = r.s[start : r.i]
    if "." in roh:
        ganz, _, bruch = roh.lstrip("-").partition(".")
        if roh.count(".") > 1 or len(ganz) > 12 or not 1 <= len(bruch) <= 3:
            raise StrukturFehler("ungültige Dezimalzahl")
        return float(roh)
    if len(roh.lstrip("-")) > 15:
        raise StrukturFehler("Integer zu lang")
    return int(roh)


def _string(r: _Leser) -> str:
    r.erwarte('"')
    teile: list[str] = []
    while True:
        if r.ende():
            raise StrukturFehler("String nicht abgeschlossen")
        z = r.zeichen()
        r.i += 1
        if z == "\\":
            esc = r.zeichen()
            if esc not in ('"', "\\"):
                raise StrukturFehler("ungültige Escape-Sequenz")
            teile.append(esc)
            r.i += 1
        elif z == '"':
            return "".join(teile)
        elif not 0x20 <= ord(z) <= 0x7E:
            raise StrukturFehler("unzulässiges Zeichen in String")
        else:
            teile.append(z)


def _parameter(r: _Leser) -> Parameter:
    params: Parameter = []
    while r.zeichen() == ";":
        r.i += 1
        r.leerraum(nur_sp=True)
        k = _schluessel(r)
        v: BareItem = True
        if r.zeichen() == "=":
            r.i += 1
            v = _bare_item(r)
        params = [(kk, vv) for kk, vv in params if kk != k] + [(k, v)]
    return params


def _item(r: _Leser) -> Item:
    return Item(_bare_item(r), _parameter(r))


def _inner_list(r: _Leser) -> InnerList:
    r.erwarte("(")
    items: list[Item] = []
    while True:
        r.leerraum(nur_sp=True)
        if r.zeichen() == ")":
            r.i += 1
            return InnerList(items, _parameter(r))
        items.append(_item(r))
        if r.zeichen() not in (" ", ")"):
            raise StrukturFehler("Inner List: Leerzeichen oder ')' erwartet")


def parse_dictionary(text: str) -> dict[str, Item | InnerList]:
    """RFC 8941 Dictionary (Reihenfolge bleibt erhalten)."""
    r = _Leser(text.strip(" \t"))
    out: dict[str, Item | InnerList] = {}
    while not r.ende():
        k = _schluessel(r)
        if r.zeichen() == "=":
            r.i += 1
            out[k] = _inner_list(r) if r.zeichen() == "(" else _item(r)
        else:
            out[k] = Item(True, _parameter(r))
        r.leerraum()
        if r.ende():
            break
        r.erwarte(",")
        r.leerraum()
        if r.ende():
            raise StrukturFehler("Komma am Ende")
    return out


def _ser_bare(v: BareItem) -> str:
    if isinstance(v, bool):
        return "?1" if v else "?0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return f"{v:.3f}".rstrip("0").rstrip(".") if v != int(v) else f"{int(v)}.0"
    if isinstance(v, bytes):
        return ":" + base64.b64encode(v).decode() + ":"
    if isinstance(v, Token):
        return str(v)
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _ser_params(p: Parameter) -> str:
    return "".join(f";{k}" if v is True else f";{k}={_ser_bare(v)}" for k, v in p)


def _ser_item(i: Item) -> str:
    return _ser_bare(i.wert) + _ser_params(i.parameter)


def ser_inner_list(liste: InnerList) -> str:
    return "(" + " ".join(_ser_item(i) for i in liste.items) + ")" + _ser_params(liste.parameter)


# ---------------------------------------------------------------------------
# Signaturprüfung
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Pruefergebnis:
    """``code``: VERIFIED, UNSIGNED, INVALID, EXPIRED, PRECONDITION, UNSUPPORTED, FAILED."""

    code: str
    grund: str = ""

    @property
    def ok(self) -> bool:
        return self.code == "VERIFIED"


def _fehler(code: str, grund: str) -> Pruefergebnis:
    return Pruefergebnis(code, grund)


class NonceSpeicher:
    """Merkt sich verwendete Nonces bis zum Ende ihres Gültigkeitsfensters."""

    def __init__(self, uhr: Callable[[], float] = time.time) -> None:
        self._uhr = uhr
        self._bis: dict[str, float] = {}

    def erstmals(self, nonce: str, gueltig_bis: float) -> bool:
        jetzt = self._uhr()
        self._bis = {n: t for n, t in self._bis.items() if t >= jetzt}
        if nonce in self._bis:
            return False
        self._bis[nonce] = gueltig_bis
        return True


def _kanonisch(wert: str) -> str:
    return " ".join(wert.strip().split("\r\n")).strip()


def pruefe_signatur(
    headers: Mapping[str, str],
    request_target: str,
    client_secret_fuer: Callable[[str], bytes | None],
    *,
    jetzt: float | None = None,
    pflicht: tuple[str, ...] = AUDIOHOOK_KOMPONENTEN,
    max_alter_s: float = MAX_ALTER_S,
    nonces: NonceSpeicher | None = None,
    api_key_pflicht: bool = True,
) -> Pruefergebnis:
    """Prüft ``Signature``/``Signature-Input`` eines Upgrade-Requests.

    ``headers`` mit beliebiger Gross-/Kleinschreibung; mehrfach vorkommende
    Header als ein mit ", " verbundener Wert. ``client_secret_fuer(keyid)``
    liefert den rohen Schlüssel (Base64-dekodiertes Client Secret) oder None.
    """
    h = {k.lower(): v for k, v in headers.items()}
    jetzt = time.time() if jetzt is None else jetzt
    try:
        eingabe = parse_dictionary(h.get("signature-input", ""))
    except StrukturFehler as e:
        return _fehler("INVALID", f"Signature-Input nicht lesbar: {e}")
    try:
        signaturen = parse_dictionary(h.get("signature", ""))
    except StrukturFehler as e:
        return _fehler("INVALID", f"Signature nicht lesbar: {e}")
    if not eingabe:
        if not signaturen:
            return _fehler("UNSIGNED", "keine Signatur-Header")
        return _fehler("INVALID", "Signature ohne Signature-Input")
    if not signaturen:
        return _fehler("INVALID", "Signature-Input ohne Signature")

    label, basis = next(iter(eingabe.items()))
    sig = signaturen.get(label)
    if not isinstance(sig, Item) or not isinstance(sig.wert, bytes):
        return _fehler(
            "INVALID", f"Signatur für Label {label!r} fehlt oder ist keine Byte Sequence"
        )
    if not isinstance(basis, InnerList):
        return _fehler("INVALID", "Signature-Input-Wert muss eine Inner List sein")

    params: dict[str, BareItem] = {}
    for k, v in basis.parameter:
        if k in ("created", "expires"):
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                return _fehler("INVALID", f"Parameter {k} muss ein Integer ≥ 0 sein")
        elif k in ("alg", "keyid", "nonce"):
            if not isinstance(v, str) or isinstance(v, Token):
                return _fehler("INVALID", f"Parameter {k} muss ein String sein")
        else:
            return _fehler("INVALID", f"unbekannter Signaturparameter {k!r}")
        params[k] = v

    created = params.get("created")
    expires = params.get("expires")
    if isinstance(created, int) and created > jetzt + MAX_UHRENABWEICHUNG_S:
        return _fehler("PRECONDITION", "created liegt in der Zukunft")
    if isinstance(expires, int) and expires + MAX_UHRENABWEICHUNG_S < jetzt:
        return _fehler("EXPIRED", "Signatur abgelaufen (expires)")
    if not isinstance(created, int):
        return _fehler("PRECONDITION", "created fehlt, Alter nicht bestimmbar")
    if jetzt - created > max_alter_s + MAX_UHRENABWEICHUNG_S:
        return _fehler("EXPIRED", f"Signatur älter als {max_alter_s:g} s")

    zeilen: list[str] = []
    gesehen: set[str] = set()
    for it in basis.items:
        name = it.wert
        if not isinstance(name, str) or isinstance(name, Token):
            return _fehler("INVALID", "Komponenten müssen Strings sein")
        if it.parameter:
            return _fehler("UNSUPPORTED", f"Komponentenparameter nicht unterstützt: {name}")
        if name in gesehen:
            return _fehler("INVALID", f"Komponente {name} doppelt")
        if name == "@signature-params":
            return _fehler("INVALID", "@signature-params darf nicht gelistet sein")
        if name.startswith("@"):
            if name not in _ABGELEITET:
                return _fehler("INVALID", f"unbekannte abgeleitete Komponente {name}")
            if name == "@request-target":
                wert = request_target
            elif name == "@authority":
                wert = _kanonisch(h.get("host", ""))
            else:
                return _fehler("UNSUPPORTED", f"abgeleitete Komponente {name} nicht unterstützt")
        else:
            if name == "signature":
                return _fehler("UNSUPPORTED", "Komponente signature nicht unterstützt")
            if name not in h:
                return _fehler("PRECONDITION", f"Header {name} fehlt")
            wert = _kanonisch(h[name])
        if not wert:
            return _fehler("PRECONDITION", f"Komponente {name} ist leer")
        zeilen.append(f'"{name}": {wert}')
        gesehen.add(name)
    fehlend = [p for p in pflicht if p not in gesehen]
    if fehlend:
        return _fehler("PRECONDITION", f"nicht signiert: {', '.join(fehlend)}")
    zeilen.append(f'"@signature-params": {ser_inner_list(basis)}')
    signaturbasis = "\n".join(zeilen).encode()

    nonce = params.get("nonce")
    if not isinstance(nonce, str) or len(nonce) < MIN_NONCE_LAENGE:
        return _fehler("PRECONDITION", "nonce fehlt oder ist zu kurz")
    keyid = params.get("keyid")
    if not isinstance(keyid, str):
        return _fehler("PRECONDITION", "keyid fehlt")
    if api_key_pflicht and not hmac.compare_digest(
        keyid.encode(), _kanonisch(h.get("x-api-key", "")).encode()
    ):
        return _fehler("PRECONDITION", "keyid passt nicht zum X-API-KEY")
    alg = params.get("alg", "hmac-sha256")

    schluessel = client_secret_fuer(keyid)
    bekannt = schluessel is not None
    berechnet = hmac.new(schluessel or secrets.token_bytes(32), signaturbasis, hashlib.sha256)
    gleich = hmac.compare_digest(sig.wert, berechnet.digest())
    if alg != "hmac-sha256":
        return _fehler("UNSUPPORTED", f"Algorithmus {alg!r} nicht unterstützt")
    if not (gleich and bekannt):
        return _fehler("FAILED", "Signatur stimmt nicht")
    if nonces is not None:
        gueltig_bis = float(created) + max_alter_s + MAX_UHRENABWEICHUNG_S
        if not nonces.erstmals(nonce, gueltig_bis):
            return _fehler("FAILED", "Nonce bereits verwendet (Wiedereinspielung)")
    return Pruefergebnis("VERIFIED")


def signieren(
    headers: Mapping[str, str],
    request_target: str,
    *,
    keyid: str,
    client_secret: bytes,
    created: int,
    nonce: str,
    expires: int | None = None,
    komponenten: tuple[str, ...] = AUDIOHOOK_KOMPONENTEN,
    label: str = "sig1",
) -> dict[str, str]:
    """Erzeugt ``Signature-Input`` und ``Signature`` (für Tests und Testclients)."""
    h = {k.lower(): v for k, v in headers.items()}
    params: Parameter = [
        ("keyid", keyid),
        ("nonce", nonce),
        ("alg", "hmac-sha256"),
        ("created", created),
    ]
    if expires is not None:
        params.append(("expires", expires))
    basis = InnerList([Item(k) for k in komponenten], params)
    zeilen = []
    for k in komponenten:
        if k == "@request-target":
            wert = request_target
        elif k == "@authority":
            wert = _kanonisch(h["host"])
        else:
            wert = _kanonisch(h[k])
        zeilen.append(f'"{k}": {wert}')
    zeilen.append(f'"@signature-params": {ser_inner_list(basis)}')
    digest = hmac.new(client_secret, "\n".join(zeilen).encode(), hashlib.sha256).digest()
    return {
        "signature-input": f"{label}={ser_inner_list(basis)}",
        "signature": f"{label}=:{base64.b64encode(digest).decode()}:",
    }
