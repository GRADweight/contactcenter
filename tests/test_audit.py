"""Audit-Kette: HMAC Pflicht, Manipulation erkennen, Datei prüfen beim Öffnen."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gw_contactcenter.audit import AuditFehler, AuditKetteDefekt, AuditLog

K = b"s" * 32


def test_ohne_schluessel_nur_im_entwicklungsmodus() -> None:
    with pytest.raises(AuditFehler, match="AUDIT_HMAC_KEY"):
        AuditLog()
    with pytest.raises(AuditFehler, match="zu kurz"):
        AuditLog(schluessel=b"kurz")
    dev = AuditLog(unsigniert_erlaubt=True)
    assert dev.alg == "sha256"
    assert AuditLog(schluessel=K).alg == "hmac-sha256"


def test_kette_und_manipulation() -> None:
    a = AuditLog(schluessel=K, uhr=lambda: 1.0)
    for i in range(5):
        a.schreiben("ereignis", i=i)
    assert a.pruefen() is None
    a.eintraege[2]["daten"]["i"] = 99
    assert a.pruefen() == 3


def test_loeschen_und_umsortieren_erkannt() -> None:
    a = AuditLog(schluessel=K, uhr=lambda: 1.0)
    for i in range(4):
        a.schreiben("e", i=i)
    kopie = list(a.eintraege)
    del a.eintraege[1]
    assert a.pruefen() == 2
    a.eintraege = [kopie[1], kopie[0], *kopie[2:]]
    assert a.pruefen() == 1


def test_ohne_schluessel_neu_berechnete_kette_faellt_auf() -> None:
    """Wer den HMAC-Schlüssel nicht kennt, kann eine geänderte Kette nicht neu
    «gültig rechnen»: Eine SHA-256-Kette wird mit Schlüssel nie akzeptiert."""
    echt = AuditLog(schluessel=K, uhr=lambda: 1.0)
    echt.schreiben("e", betrag=10)
    faelschung = AuditLog(unsigniert_erlaubt=True, uhr=lambda: 1.0)
    faelschung.schreiben("e", betrag=1000)
    echt.eintraege = faelschung.eintraege
    assert echt.pruefen() == 1
    anderer = AuditLog(schluessel=b"x" * 32, uhr=lambda: 1.0)
    anderer.schreiben("e", betrag=1000)
    echt.eintraege = anderer.eintraege
    assert echt.pruefen() == 1


def test_datei_wird_beim_oeffnen_geprueft(tmp_path: Path) -> None:
    pfad = str(tmp_path / "audit.jsonl")
    a = AuditLog(pfad, schluessel=K, uhr=lambda: 1.0)
    a.schreiben("start")
    a.schreiben("ende")
    b = AuditLog(pfad, schluessel=K, uhr=lambda: 2.0)  # Kette wird fortgesetzt
    b.schreiben("weiter")
    assert len(b.eintraege) == 3 and b.pruefen() is None
    assert b.letzter_pruefwert == b.eintraege[-1]["hash"]

    zeilen = Path(pfad).read_text(encoding="utf-8").splitlines()
    eintrag = json.loads(zeilen[1])
    eintrag["daten"] = {"manipuliert": True}
    zeilen[1] = json.dumps(eintrag, ensure_ascii=False)
    Path(pfad).write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    with pytest.raises(AuditKetteDefekt) as e:
        AuditLog(pfad, schluessel=K)
    assert e.value.nummer == 2

    with pytest.raises(AuditKetteDefekt):
        AuditLog(pfad, schluessel=b"y" * 32)  # falscher Schlüssel


def test_kaputte_datei(tmp_path: Path) -> None:
    pfad = tmp_path / "audit.jsonl"
    pfad.write_text("{kein json\n", encoding="utf-8")
    with pytest.raises(AuditKetteDefekt):
        AuditLog(str(pfad), schluessel=K)
    pfad.write_text('{"nr": 1}\n', encoding="utf-8")
    with pytest.raises(AuditKetteDefekt):
        AuditLog(str(pfad), schluessel=K)
