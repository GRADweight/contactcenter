"""HTTP-Nachrichtensignaturen: Testvektoren aus der Genesys-Referenzimplementierung.

Quelle der Vektoren: github.com/purecloudlabs/audiohook-reference-implementation,
``app/audiohook/test/httpsignature/signature-verifier.test.ts`` («Verify AudioHook
documentation example» und RFC-Entwurf draft-ietf-httpbis-message-signatures-11,
Anhang B.2.5).
"""

from __future__ import annotations

import base64

import pytest

from gw_contactcenter.signatur import (
    InnerList,
    Item,
    NonceSpeicher,
    StrukturFehler,
    Token,
    parse_dictionary,
    pruefe_signatur,
    ser_inner_list,
    signieren,
)

API_KEY = "SGVsbG8sIEkgYW0gdGhlIEFQSSBrZXkh"
SECRET = base64.b64decode("TXlTdXBlclNlY3JldEtleVRlbGxOby0xITJAMyM0JDU=")
PFAD = "/api/v1/voicebiometrics/ws"
GENESYS = {
    "host": "audiohook.example.com",
    "audiohook-organization-id": "d7934305-0972-4844-938e-9060eef73d05",
    "audiohook-session-id": "30b0e395-84d3-4570-ac13-9a62d8f514c0",
    "audiohook-correlation-id": "e160e428-53e2-487c-977d-96989bf5c99d",
    "x-api-key": API_KEY,
    "signature": "sig1=:NZBwyBHRRyRoeLqy1IzOa9VYBuI8TgMFt2GRDkDuJh4=:",
    "signature-input": 'sig1=("@request-target" "@authority" "audiohook-organization-id" '
    '"audiohook-session-id" "audiohook-correlation-id" "x-api-key");'
    f'keyid="{API_KEY}";nonce="VGhpc0lzQVVuaXF1ZU5vbmNl";alg="hmac-sha256";'
    "created=1641013200;expires=3282026430",
}
JETZT = 1641013205.0


def _secret(keyid: str) -> bytes | None:
    return SECRET if keyid == API_KEY else None


def test_genesys_dokumentationsbeispiel() -> None:
    assert pruefe_signatur(GENESYS, PFAD, _secret, jetzt=JETZT).ok


def test_header_reihenfolge_und_gross_klein_egal() -> None:
    h = {k.upper(): v for k, v in reversed(GENESYS.items())}
    assert pruefe_signatur(h, PFAD, _secret, jetzt=JETZT).ok


def test_rfc_vektor_b25_ohne_audiohook_pflicht() -> None:
    key = base64.b64decode(
        "uzvJfB4u3N0Jy4T7NZ75MDVcr8zSTInedJtkgcu46YW4XByzNJjxBdtjUkdJPBtbmHhIDi6pcl8jsasjlTMtDQ=="
    )
    h = {
        "date": "Tue, 20 Apr 2021 02:07:55 GMT",
        "host": "example.com",
        "content-type": "application/json",
        "signature-input": 'sig-b25=("date" "@authority" "content-type");created=1618884473;'
        'keyid="test-shared-secret";nonce="eine-genuegend-lange-nonce"',
        "signature": "sig-b25=:pxcQw6G3AjtMBQjwo8XzkZf/bws5LelbaMk5rGIGtE8=:",
    }
    # Die RFC-Signatur deckt keine nonce ab; mit nonce ändert sich die Signaturbasis.
    r = pruefe_signatur(h, "/", lambda k: key, jetzt=1618884475, pflicht=(), api_key_pflicht=False)
    assert r.code == "FAILED"
    h["signature-input"] = (
        'sig-b25=("date" "@authority" "content-type");created=1618884473;keyid="test-shared-secret"'
    )
    r = pruefe_signatur(h, "/", lambda k: key, jetzt=1618884475, pflicht=(), api_key_pflicht=False)
    assert (r.code, r.grund) == ("PRECONDITION", "nonce fehlt oder ist zu kurz")


def test_signieren_reproduziert_genesys() -> None:
    s = signieren(
        GENESYS,
        PFAD,
        keyid=API_KEY,
        client_secret=SECRET,
        created=1641013200,
        nonce="VGhpc0lzQVVuaXF1ZU5vbmNl",
        expires=3282026430,
    )
    assert s == {"signature": GENESYS["signature"], "signature-input": GENESYS["signature-input"]}


@pytest.mark.parametrize(
    ("aenderung", "pfad", "jetzt", "code"),
    [
        ({}, "/anderer/pfad", JETZT, "FAILED"),
        ({"audiohook-session-id": "manipuliert"}, PFAD, JETZT, "FAILED"),
        ({}, PFAD, 1641013300.0, "EXPIRED"),
        ({}, PFAD, 1641013190.0, "PRECONDITION"),  # created in der Zukunft
        ({"x-api-key": "anderer"}, PFAD, JETZT, "PRECONDITION"),
        ({"signature": "", "signature-input": ""}, PFAD, JETZT, "UNSIGNED"),
        ({"signature": ""}, PFAD, JETZT, "INVALID"),
        ({"signature-input": ""}, PFAD, JETZT, "INVALID"),
        ({"signature-input": "sig1=(kaputt"}, PFAD, JETZT, "INVALID"),
        ({"signature": "sig1=:kein base64!:"}, PFAD, JETZT, "INVALID"),
        ({"signature": "sig2=:AAAA:"}, PFAD, JETZT, "INVALID"),
        ({"signature-input": 'sig1="kein-inner-list"'}, PFAD, JETZT, "INVALID"),
    ],
)
def test_abgelehnt(aenderung: dict[str, str], pfad: str, jetzt: float, code: str) -> None:
    h = dict(GENESYS, **aenderung)
    h = {k: v for k, v in h.items() if v != ""}
    assert pruefe_signatur(h, pfad, _secret, jetzt=jetzt).code == code


def test_unbekannter_key_und_falscher_algorithmus() -> None:
    assert pruefe_signatur(GENESYS, PFAD, lambda k: None, jetzt=JETZT).code == "FAILED"
    h = dict(GENESYS)
    h["signature-input"] = h["signature-input"].replace('alg="hmac-sha256"', 'alg="rsa-pss-sha512"')
    assert pruefe_signatur(h, PFAD, _secret, jetzt=JETZT).code == "UNSUPPORTED"


@pytest.mark.parametrize(
    ("ersetzen", "code"),
    [
        (('"x-api-key");', '"x-api-key" "x-api-key");'), "INVALID"),
        (('"x-api-key");', '"x-api-key" "@signature-params");'), "INVALID"),
        (('"x-api-key");', '"x-api-key" "@unbekannt");'), "INVALID"),
        (('"x-api-key");', '"x-api-key" "@method");'), "UNSUPPORTED"),
        (('"x-api-key");', '"x-api-key" "signature");'), "UNSUPPORTED"),
        (('"x-api-key");', '"x-api-key";sf);'), "UNSUPPORTED"),
        (('"x-api-key");', '"x-api-key" "fehlt");'), "PRECONDITION"),
        (('"x-api-key");', '"x-api-key" token);'), "INVALID"),
        (('"@request-target" ', ""), "PRECONDITION"),  # Pflichtkomponente fehlt
        (("created=1641013200", "created=-1"), "INVALID"),
        (("created=1641013200;", ""), "PRECONDITION"),
        (('keyid="', "keyid=tok;x="), "INVALID"),
        (('alg="hmac-sha256"', "zusatz=1"), "INVALID"),
        (('nonce="VGhpc0lzQVVuaXF1ZU5vbmNl"', 'nonce="kurz"'), "PRECONDITION"),
    ],
)
def test_ungueltige_signature_input(ersetzen: tuple[str, str], code: str) -> None:
    h = dict(GENESYS)
    h["signature-input"] = h["signature-input"].replace(*ersetzen, 1)
    assert pruefe_signatur(h, PFAD, _secret, jetzt=JETZT).code == code


def test_nonce_nur_einmal() -> None:
    nonces = NonceSpeicher(uhr=lambda: JETZT)
    assert pruefe_signatur(GENESYS, PFAD, _secret, jetzt=JETZT, nonces=nonces).ok
    r = pruefe_signatur(GENESYS, PFAD, _secret, jetzt=JETZT, nonces=nonces)
    assert (r.code, "Wiedereinspielung" in r.grund) == ("FAILED", True)
    # nach Ablauf des Fensters wird der Eintrag aufgeräumt
    spaeter = NonceSpeicher(uhr=lambda: JETZT + 100)
    assert spaeter.erstmals("n", JETZT + 200)


def test_structured_fields_parser_und_serialisierer() -> None:
    d = parse_dictionary('a=1, b="x\\"y", c=?0, d=tok/en;p=1.5, e=:AAE=:, f, g=("x" 2);q')
    assert isinstance(d["a"], Item) and d["a"].wert == 1
    assert isinstance(d["b"], Item) and d["b"].wert == 'x"y'
    assert isinstance(d["c"], Item) and d["c"].wert is False
    assert isinstance(d["d"], Item) and isinstance(d["d"].wert, Token)
    assert d["d"].parameter == [("p", 1.5)]
    assert isinstance(d["e"], Item) and d["e"].wert == b"\x00\x01"
    assert isinstance(d["f"], Item) and d["f"].wert is True
    g = d["g"]
    assert isinstance(g, InnerList) and g.parameter == [("q", True)]
    assert ser_inner_list(g) == '("x" 2);q'
    assert ser_inner_list(InnerList([Item(b"\x01"), Item(True), Item(-3), Item(2.5)])) == (
        "(:AQ==: ?1 -3 2.5)"
    )
    for kaputt in (
        "A=1",
        "a=1,",
        "a=",
        'a="offen',
        "a=?2",
        "a=1.2345",
        "a=" + "9" * 16,
        "a=:abc",
        "a=(1 2",
        'a="\\x"',
        "a=1 b=2",
        "a=-x",
        "a=1.2.3",
        'a="ä"',
    ):
        with pytest.raises(StrukturFehler):
            parse_dictionary(kaputt)
