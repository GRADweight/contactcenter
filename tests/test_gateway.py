"""LLM-Gateway: Residenz, Fallback, Circuit Breaker, Budget, Satz-Streaming, Antwortprüfung."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest

from gw_contactcenter.adapters.mock import RegelLLM
from gw_contactcenter.audit import AuditLog
from gw_contactcenter.gateway import (
    ERSATZ_SATZ,
    Backend,
    BudgetErschoepft,
    CircuitBreaker,
    KeinBackend,
    Kostenzaehler,
    LLMErgebnis,
    LLMGateway,
    Nachricht,
    satzende,
)

from .conftest import SCHLUESSEL

MSG = [{"role": "user", "content": "hallo"}]


def _gw(*backends: Backend, **kw: float) -> LLMGateway:
    return LLMGateway(list(backends), audit=AuditLog(schluessel=SCHLUESSEL), **kw)  # type: ignore[arg-type]


def test_satzende() -> None:
    assert satzende("Hallo. Wie") == 6
    assert satzende("Das gilt z. B. heute") == -1
    assert satzende("Am 12. März kommt es") == -1
    assert satzende("Wirklich? Ja") == 9
    lang = "Ein sehr langer Satz ohne Punkt, " * 4
    assert satzende(lang) > 0, "lange Sätze werden am Komma geteilt"


def test_v3_nie_in_cloud_v1_darf_eu() -> None:
    gw = _gw(Backend("cloud", "eu", RegelLLM()))
    with pytest.raises(KeinBackend):
        gw.anfrage(MSG, klasse=3, anruf_id="a")
    assert gw.anfrage(MSG, klasse=1, anruf_id="a").region == "eu"
    with pytest.raises(ValueError):
        Backend("x", "mars", RegelLLM())


def test_fallback_und_breaker() -> None:
    kaputt = RegelLLM(fehler=True)
    gw = _gw(
        Backend("primaer", "lokal", kaputt, breaker=CircuitBreaker(schwelle=2, uhr=lambda: 0)),
        Backend("ersatz", "lokal", RegelLLM()),
    )
    for _ in range(3):
        assert gw.anfrage(MSG, klasse=2, anruf_id="a").backend == "ersatz"
    assert kaputt.aufrufe == 2, "nach 2 Fehlern wird das kaputte Backend übersprungen"


def test_breaker_halb_offen() -> None:
    zeit = [0.0]
    b = CircuitBreaker(schwelle=1, abkuehl_s=10, uhr=lambda: zeit[0])
    b.fehlschlag()
    assert not b.darf()
    zeit[0] = 10.0
    assert b.darf()
    b.erfolg()
    assert b.fehler == 0 and b.offen_seit is None


def test_alle_ausgefallen() -> None:
    gw = _gw(Backend("a", "lokal", RegelLLM(fehler=True)))
    with pytest.raises(KeinBackend):
        gw.anfrage(MSG, klasse=2, anruf_id="a")
    with pytest.raises(KeinBackend):
        list(gw.saetze(MSG, klasse=2, anruf_id="a"))


def test_stream_waechter_ersetzt_saetze() -> None:
    gw = _gw(Backend("boese", "lokal", RegelLLM(boese=True)))
    alles = " ".join(s.text for s in gw.saetze(MSG, klasse=2, anruf_id="a"))
    assert "garantiere" not in alles
    assert "4111" not in alles
    assert alles == ERSATZ_SATZ
    llm = [e for e in gw.audit.eintraege if e["ereignis"] == "llm_stream"]
    assert llm and llm[0]["daten"]["befunde"]


def test_anfrage_prueft_auch_ganze_antworten() -> None:
    gw = _gw(Backend("boese", "lokal", RegelLLM(boese=True)))
    r = gw.anfrage(MSG, klasse=2, anruf_id="a")
    assert r.text == ERSATZ_SATZ and r.befunde
    roh = gw.anfrage(MSG, klasse=2, anruf_id="a", json_modus=True)
    assert roh.befunde == [], "JSON-Antworten (Klassifikation) werden nicht ersetzt"


class _OhneStream:
    def __init__(self, text: str) -> None:
        self.text = text

    def complete(
        self, messages: list[Nachricht], max_tokens: int = 200, json_modus: bool = False
    ) -> LLMErgebnis:
        return {"text": self.text, "tokens_ein": 1, "tokens_aus": 1}


def test_ohne_stream_satzweise_und_wortlimit() -> None:
    gw = _gw(Backend("lang", "lokal", _OhneStream("Das ist ein Satz. " * 40)))
    saetze = list(gw.saetze(MSG, klasse=2, anruf_id="a"))
    assert len(saetze) > 1
    assert sum(len(s.text.split()) for s in saetze) <= 48
    assert saetze[0].ttft_ms is not None and saetze[1].ttft_ms is None


def test_letzter_satz_ohne_punkt_wird_gesprochen() -> None:
    gw = _gw(Backend("x", "lokal", _OhneStream("Erster Satz. Und der Rest")))
    assert [s.text for s in gw.saetze(MSG, klasse=2, anruf_id="a")] == [
        "Erster Satz.",
        "Und der Rest",
    ]


def test_leere_antwort_gilt_als_ausfall() -> None:
    gw = _gw(Backend("leer", "lokal", _OhneStream("")), Backend("ersatz", "lokal", RegelLLM()))
    saetze = list(gw.saetze(MSG, klasse=2, anruf_id="a"))
    assert saetze and saetze[0].backend == "ersatz"
    assert [e for e in gw.audit.eintraege if e["ereignis"] == "leere_antwort"]


class _Bricht:
    def __init__(self, nach: int) -> None:
        self.nach = nach

    def complete(
        self, messages: list[Nachricht], max_tokens: int = 200, json_modus: bool = False
    ) -> LLMErgebnis:
        raise NotImplementedError

    def stream(self, messages: list[Nachricht], max_tokens: int = 200) -> Iterator[str]:
        teile = ["Erster", " Satz.", " Zweiter", " Satz"]
        for i, t in enumerate(teile):
            if i == self.nach:
                raise ConnectionError("weg")
            yield t


def test_abbruch_vor_erstem_satz_nimmt_naechstes_backend() -> None:
    bricht = Backend("bricht", "lokal", _Bricht(1))
    gw = _gw(bricht, Backend("ersatz", "lokal", RegelLLM()))
    texte = [s.text for s in gw.saetze(MSG, klasse=2, anruf_id="a")]
    assert texte and "Erster" not in " ".join(texte), "kein Satzfragment"
    assert bricht.breaker.fehler == 1


def test_abbruch_nach_erstem_satz_behaelt_gesagtes() -> None:
    gw = _gw(Backend("bricht", "lokal", _Bricht(3)), Backend("ersatz", "lokal", RegelLLM()))
    assert [s.text for s in gw.saetze(MSG, klasse=2, anruf_id="a")] == ["Erster Satz."]
    assert [e for e in gw.audit.eintraege if e["ereignis"] == "stream_abbruch"]


def test_stream_fehler_beim_start() -> None:
    gw = _gw(Backend("tot", "lokal", _Bricht(0)), Backend("ersatz", "lokal", RegelLLM()))
    assert next(iter(gw.saetze(MSG, klasse=2, anruf_id="a"))).backend == "ersatz"


def test_budget_und_tageslimit() -> None:
    gw = _gw(Backend("l", "lokal", RegelLLM()), token_budget_pro_anruf=1)
    gw.anfrage(MSG, klasse=2, anruf_id="a")
    with pytest.raises(BudgetErschoepft):
        gw.anfrage(MSG, klasse=2, anruf_id="a")
    assert gw.anfrage(MSG, klasse=2, anruf_id="b").backend == "l", "Budget gilt pro Anruf"
    teuer = _gw(Backend("t", "lokal", RegelLLM(), preis_pro_1k_token=1000.0), tageslimit=1.0)
    teuer.anfrage(MSG, klasse=2, anruf_id="a")
    assert teuer.kosten_heute > 1.0
    with pytest.raises(BudgetErschoepft):
        teuer.anfrage(MSG, klasse=2, anruf_id="z")


def test_kostenzaehler_beginnt_jeden_tag_neu() -> None:
    tag = [date(2026, 10, 27)]
    z = Kostenzaehler(limit=1.0, tag=lambda: tag[0])
    z.buchen(1.0)
    assert z.erschoepft()
    tag[0] = date(2026, 10, 28)
    assert z.heute == 0.0 and not z.erschoepft()
    with pytest.raises(ValueError):
        z.buchen(-1.0)
    with pytest.raises(ValueError):
        Kostenzaehler(limit=-1.0)


def test_geteilter_kostenzaehler_gilt_ueber_alle_gateways(audit: AuditLog) -> None:
    z = Kostenzaehler(limit=1.0)
    b = Backend("t", "lokal", RegelLLM(), preis_pro_1k_token=1000.0)
    erster = LLMGateway([b], audit, kosten=z)
    zweiter = LLMGateway([b], audit, kosten=z)
    erster.anfrage(MSG, klasse=0, anruf_id="a")
    assert zweiter.kosten_heute == erster.kosten_heute > 1.0
    with pytest.raises(BudgetErschoepft):
        zweiter.anfrage(MSG, klasse=0, anruf_id="b")
