"""PII-Tresor: Prüfsummen, Pseudonymisierung, Rundreise (inkl. Property-Tests)."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from gw_contactcenter.pii import (
    PIITresor,
    ahv_ok,
    enthaelt_roh_pii,
    iban_ok,
    luhn_ok,
    platzhalter_sprechbar,
)

IBAN_GUELTIG = "CH93 0076 2011 6238 5295 7"
KARTE_GUELTIG = "4111 1111 1111 1111"
AHV_GUELTIG = "756.9217.0769.85"


def _iban_pruefziffern(land: str, bban: str) -> str:
    zahl = int("".join(str(int(c, 36)) for c in bban + land + "00"))
    return f"{98 - zahl % 97:02d}"


def _luhn_ziffer(rumpf: str) -> str:
    for z in "0123456789":
        if luhn_ok(rumpf + z):
            return z
    raise AssertionError


def _ahv_ziffer(zwoelf: str) -> str:
    s = sum(int(x) * (3 if i % 2 else 1) for i, x in enumerate(zwoelf))
    return str((10 - s % 10) % 10)


def test_validatoren_bekannte_werte() -> None:
    assert iban_ok(IBAN_GUELTIG) and not iban_ok("CH93 0076 2011 6238 5295 8")
    assert iban_ok("GB82 WEST 1234 5698 7654 32")
    assert not iban_ok("XX")
    assert luhn_ok("4111111111111111") and not luhn_ok("4111111111111112")
    assert not luhn_ok("41111x")
    assert ahv_ok(AHV_GUELTIG) and not ahv_ok("756.9217.0769.86")
    assert not ahv_ok("123.9217.0769.85")  # Präfix 756 Pflicht


def test_alle_typen_werden_maskiert_und_wiederhergestellt() -> None:
    t = PIITresor()
    text = (
        f"IBAN {IBAN_GUELTIG}, Karte {KARTE_GUELTIG}, AHV {AHV_GUELTIG}, "
        "Mail anna.muster@beispiel.ch, Tel 079 123 45 67, Konto 1234567, Bestellung 447100"
    )
    p, typen = t.pseudonymisieren(text)
    for roh in (
        IBAN_GUELTIG,
        KARTE_GUELTIG,
        AHV_GUELTIG,
        "anna.muster@beispiel.ch",
        "079 123 45 67",
    ):
        assert roh not in p
    assert "447100" in p, "Bestellnummer ist keine PII"
    assert set(typen) == {"IBAN", "KARTE", "AHV", "EMAIL", "TELEFON", "KONTO"}
    assert t.wiederherstellen(p) == text


def test_fast_treffer_werden_verdacht() -> None:
    roh = "Karte 4111 1111 1111 1112, IBAN CH93 0076 2011 6238 5295 8"
    p, typen = PIITresor().pseudonymisieren(roh)
    assert p == roh, "falsche Prüfsumme wird nicht maskiert"
    assert typen and all(t.startswith("VERDACHT_") for t in typen)


def test_iban_mehrerer_laender_und_sonderleerzeichen() -> None:
    p, typen = PIITresor().pseudonymisieren(
        "DE89 3704 0044 0532 0130 00 und CH93\xa00076 2011 6238 5295 7 "
        "und GB82 WEST 1234 5698 7654 32"
    )
    assert p == "[IBAN_1] und [IBAN_2] und [IBAN_3]"
    assert typen == ["IBAN"] * 3


def test_gleicher_wert_gleicher_platzhalter_und_sprechbar() -> None:
    t = PIITresor()
    a, _ = t.pseudonymisieren(f"{IBAN_GUELTIG} und nochmals {IBAN_GUELTIG}")
    assert a.count("[IBAN_1]") == 2
    assert platzhalter_sprechbar("Ist [IBAN_1] korrekt?") == "Ist die genannte IBAN korrekt?"
    assert platzhalter_sprechbar("[NEU_3]") == "die genannte Angabe"


def test_roh_pii_im_antworttext() -> None:
    assert enthaelt_roh_pii(f"Ihre IBAN {IBAN_GUELTIG}") == ["IBAN"]
    assert enthaelt_roh_pii("Bestellung 447100 ist unterwegs") == []


# ------------------------------------------------------------- Property-Tests

LAENDER = {"CH": 17, "DE": 18, "AT": 16, "FR": 23, "IT": 23, "LI": 17, "NL": 14}


@given(
    land=st.sampled_from(sorted(LAENDER)),
    ziffern=st.text(alphabet="0123456789", min_size=40, max_size=40),
)
def test_jede_gueltige_iban_wird_maskiert(land: str, ziffern: str) -> None:
    bban = ziffern[: LAENDER[land]]
    iban = land + _iban_pruefziffern(land, bban) + bban
    assert iban_ok(iban)
    gruppiert = " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))
    for form in (iban, gruppiert):
        t = PIITresor()
        text = f"Meine IBAN ist {form}, danke."
        p, typen = t.pseudonymisieren(text)
        assert "IBAN" in typen
        assert bban[-6:] not in p
        assert t.wiederherstellen(p) == text


@given(rumpf=st.text(alphabet="0123456789", min_size=15, max_size=15).filter(lambda s: s[0] != "0"))
def test_jede_luhn_gueltige_kartennummer_wird_maskiert(rumpf: str) -> None:
    karte = rumpf + _luhn_ziffer(rumpf)
    p, typen = PIITresor().pseudonymisieren(f"Karte {karte} bitte sperren")
    assert karte not in p
    assert "KARTE" in typen


@given(neun=st.text(alphabet="0123456789", min_size=9, max_size=9))
def test_jede_gueltige_ahv_nummer_wird_maskiert(neun: str) -> None:
    zwoelf = "756" + neun
    ahv = zwoelf + _ahv_ziffer(zwoelf)
    formatiert = f"{ahv[:3]}.{ahv[3:7]}.{ahv[7:11]}.{ahv[11:]}"
    p, typen = PIITresor().pseudonymisieren(f"AHV {formatiert}")
    assert formatiert not in p
    assert typen == ["AHV"]


@given(nr=st.integers(min_value=100_000, max_value=999_999))
def test_sechsstellige_bestellnummer_bleibt(nr: int) -> None:
    p, typen = PIITresor().pseudonymisieren(f"Bestellung {nr} ist nicht angekommen")
    assert str(nr) in p
    assert typen == []
