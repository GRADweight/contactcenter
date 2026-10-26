"""Werkzeuge (Schema, Allowlist, Bestätigung, Idempotenz) und Anliegen-Katalog."""

from __future__ import annotations

from typing import Any

import pytest

from gw_contactcenter.audit import AuditLog
from gw_contactcenter.policy import (
    AUTONOMIE,
    KATALOG,
    REGION_JE_KLASSE,
    anliegen,
    klassifiziere_regelbasiert,
    region_erlaubt,
)
from gw_contactcenter.tools import DemoBackend, SchemaFehler, standard_werkzeuge, validiere

from .conftest import SCHLUESSEL


@pytest.fixture
def kasten() -> tuple[Any, DemoBackend]:
    b = DemoBackend()
    return standard_werkzeuge(b, AuditLog(schluessel=SCHLUESSEL)), b


def test_schema() -> None:
    s = {
        "type": "object",
        "required": ["n"],
        "additionalProperties": False,
        "properties": {
            "n": {"type": "string", "pattern": r"\d{6}", "maxLength": 6},
            "z": {"type": "integer", "minimum": 1, "maximum": 3},
            "e": {"enum": ["a", "b"]},
            "l": {"type": "array", "items": {"type": "number"}},
        },
    }
    validiere(s, {"n": "123456", "z": 2, "e": "a", "l": [1, 2.5]})
    falsch: list[Any] = [
        {},
        {"n": 123456},
        {"n": "12"},
        {"n": "1234567"},
        {"n": "123456", "x": 1},
        {"n": "123456", "z": 0},
        {"n": "123456", "z": 4},
        {"n": "123456", "z": True},
        {"n": "123456", "e": "c"},
        {"n": "123456", "l": ["x"]},
        "kein objekt",
    ]
    for f in falsch:
        with pytest.raises(SchemaFehler):
            validiere(s, f)
    with pytest.raises(SchemaFehler):
        validiere({"type": "datum"}, "2026")


def test_nicht_erlaubt(kasten: tuple[Any, DemoBackend]) -> None:
    k, _ = kasten
    e = k.aufrufen(
        "ticket_erstellen", {"thema": "x", "bereich": "y"}, erlaubt=["wissen_suchen"], anruf_id="a"
    )
    assert e.status == "abgelehnt"
    assert (
        k.aufrufen("loesche_alles", {}, erlaubt=["loesche_alles"], anruf_id="a").status
        == "abgelehnt"
    )


def test_bestaetigung_und_idempotenz(kasten: tuple[Any, DemoBackend]) -> None:
    k, b = kasten
    args = {"thema": "Paket beschädigt", "bereich": "Fachbereich"}
    e = k.aufrufen("ticket_erstellen", args, erlaubt=["ticket_erstellen"], anruf_id="a")
    assert e.status == "bestaetigung_noetig"
    assert "Paket beschädigt" in e.text
    assert b.tickets == []
    e1 = k.aufrufen(
        "ticket_erstellen", args, erlaubt=["ticket_erstellen"], anruf_id="a", bestaetigt=True
    )
    e2 = k.aufrufen(
        "ticket_erstellen", args, erlaubt=["ticket_erstellen"], anruf_id="a", bestaetigt=True
    )
    assert (e1.status, e2.status) == ("ok", "duplikat")
    assert len(b.tickets) == 1, "kein zweites Ticket bei Wiederholung"


def test_schemafehler_und_systemfehler(kasten: tuple[Any, DemoBackend]) -> None:
    k, _ = kasten
    assert (
        k.aufrufen(
            "bestellstatus_abfragen",
            {"bestellnummer": "12"},
            erlaubt=["bestellstatus_abfragen"],
            anruf_id="a",
        ).status
        == "fehler"
    )

    def kaputt(a: dict[str, Any]) -> dict[str, Any]:
        raise ZeroDivisionError

    k.werkzeuge["wissen_suchen"].ausfuehren = kaputt
    e = k.aufrufen("wissen_suchen", {"frage": "x"}, erlaubt=["wissen_suchen"], anruf_id="a")
    assert e.status == "fehler"
    assert "Zero" not in e.text


def test_openai_definitionen(kasten: tuple[Any, DemoBackend]) -> None:
    k, _ = kasten
    [d] = k.definitionen(["wissen_suchen", "gibt_es_nicht"])
    assert d["type"] == "function" and d["function"]["name"] == "wissen_suchen"


def test_policy_tabelle_konsistent() -> None:
    for a in KATALOG.values():
        assert a.klasse in REGION_JE_KLASSE and a.risiko in AUTONOMIE
        if a.klasse >= 2:
            assert not region_erlaubt("ch", a.klasse), "V2-V4 nur lokal"
        assert set(a.tools) <= {"wissen_suchen", "bestellstatus_abfragen", "ticket_erstellen"}
    assert anliegen("gibt-es-nicht").id == "unbekannt"


def test_residenz() -> None:
    assert region_erlaubt("lokal", 4)
    assert not region_erlaubt("ch", 2)
    assert region_erlaubt("eu", 1)
    assert not region_erlaubt("us", 1)
    assert region_erlaubt("us", 0)
    with pytest.raises(ValueError):
        region_erlaubt("mars", 1)
    with pytest.raises(ValueError):
        region_erlaubt("lokal", 7)


def test_riskantestes_gewinnt() -> None:
    assert klassifiziere_regelbasiert(
        "Wo ist meine Bestellung, und ich will den Vertrag kündigen"
    ) == (
        "vertrag-aenderung",
        0.6,
    )
    assert klassifiziere_regelbasiert("Was kostet das?") == ("preis-anfrage", 0.8)
    assert klassifiziere_regelbasiert("Hmm") == ("unbekannt", 0.0)
