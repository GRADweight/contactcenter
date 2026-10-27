"""End-to-End mit simulierten Anrufen (Audio -> Pipeline -> Audio, AudioHook)."""

from __future__ import annotations

from gw_contactcenter import audio
from gw_contactcenter.adapters.mock import MOCK_LATENZ_MS, MockASR, RegelLLM
from gw_contactcenter.gateway import Backend
from gw_contactcenter.pipeline import NOTFALL_TEXT, Wiedergabe
from gw_contactcenter.sim import DEMO, HERKUNFT, Zug, anruf, baue_pipeline, bericht_text
from gw_contactcenter.telemetry import SimUhr
from gw_contactcenter.turn import TurnConfig


def test_demo_anruf() -> None:
    b = anruf(DEMO)
    assert b.werkzeug_aufrufe == ["bestellstatus_abfragen", "ticket_erstellen"]
    assert "unterwegs" in b.antworten[1]
    assert b.uebergabe is not None and b.uebergabe["ziel"] == "Fachbereich (2. Level)"
    assert b.audit_intakt
    assert b.audiohook[0]["type"] == "opened"
    assert b.audiohook[-1]["type"] == "disconnect"
    assert b.audiohook[-1]["parameters"]["outputVariables"]["ticket"] == "T-00001"
    assert b.budget["zeit_bis_erster_ton"]["ok"]
    assert b.als_dict()["werkzeuge"] == b.werkzeug_aufrufe


def test_simulierte_latenz_ist_summe_der_mock_annahmen_plus_endpointing() -> None:
    """Die Simulation misst keine Modelle: Bei einer Vorlagenantwort ist die
    Zeit bis zum ersten Ton genau Endpointing + ASR + TTS der Mock-Werte (+ Dialog)."""
    b = anruf([Zug("Ich will meinen Vertrag kündigen.")])
    ttfa = b.budget["zeit_bis_erster_ton"]["p50"]
    ep = b.budget["endpointing"]["p50"]
    erwartet = (
        float(ep) + MOCK_LATENZ_MS["asr"] + MOCK_LATENZ_MS["klassifikation"] + MOCK_LATENZ_MS["tts"]
    )
    assert ttfa == erwartet
    text = bericht_text(b)
    assert "SIMULATION" in text and "keine Messung" in text
    assert HERKUNFT["endpointing"] in text


def test_ohne_zwischenergebnisse_wartet_das_endpointing_laenger() -> None:
    """Der HTTP-Adapter liefert keine Zwischenergebnisse. Das Endpointing kennt
    den Text dann nicht und wartet die mittlere Stille aus TurnConfig ab."""
    cfg = TurnConfig()
    mittel = (cfg.warte_fertig_ms + cfg.warte_unfertig_ms) / 2
    mit = anruf(DEMO)
    ohne = anruf(DEMO, zwischenergebnisse=False)
    assert mit.antworten == ohne.antworten
    ep_mit = mit.budget["endpointing"]["p50"]
    ep_ohne = ohne.budget["endpointing"]["p50"]
    assert ep_mit < mittel <= ep_ohne < mittel + 100
    assert not ohne.budget["zeit_bis_erster_ton"]["ok"], "ohne Partials über dem Budget"
    text = bericht_text(mit, ohne=ohne)
    assert "ohne Zwischenergebnisse" in text
    assert MockASR(["Hallo Welt"], zwischenergebnisse=False).teiltext() == ""


def test_denkpause_beim_diktieren_schneidet_nicht_ab() -> None:
    b = anruf(
        [
            Zug("Wo ist meine Bestellung?"),
            Zug("Die Nummer ist 447 100.", denkpause_nach_wort=4, denkpause_ms=700),
        ]
    )
    assert "unterwegs" in b.antworten[1], "Nummer muss komplett ankommen"


def test_barge_in() -> None:
    b = anruf(
        [
            Zug("Was sind eure Öffnungszeiten?"),
            Zug("Halt, ich will eigentlich meinen Vertrag kündigen.", unterbricht_nach_ms=800),
        ]
    )
    assert "barge_in" in [e[0] for e in b.ereignisse]
    events = [e for e in b.audiohook if e["type"] == "event"]
    assert events and events[0]["parameters"]["entities"][0]["type"] == "barge_in"
    assert b.uebergabe is not None and b.uebergabe["anliegen"] == "vertrag-aenderung"


def test_backchannel_unterbricht_nicht() -> None:
    b = anruf(
        [Zug("Was sind eure Öffnungszeiten?"), Zug("mhm", unterbricht_nach_ms=600, ms_pro_wort=250)]
    )
    typen = [e[0] for e in b.ereignisse]
    assert "barge_in" not in typen
    assert "backchannel" in typen


def test_ohne_audiohook() -> None:
    b = anruf([Zug("Ich will meinen Vertrag kündigen.")], mit_audiohook=False)
    assert b.audiohook == [] and b.uebergabe is not None


def test_barge_in_verlauf_nur_gesprochener_teil() -> None:
    pipe, _, _ = baue_pipeline([], SimUhr())
    pipe.plan = [Wiedergabe(0, 1000, "Alte Antwort.")]
    pipe.turn_ab = 1
    pipe.plan += [
        Wiedergabe(5000, 6000, "Neu eins zwei drei vier."),
        Wiedergabe(6000, 7000, "Neu fünf."),
    ]
    pipe.dialog.antwort_merken("Neu eins zwei drei vier. Neu fünf.")
    pipe._barge_in(5500)
    assert pipe.dialog.verlauf[-1]["content"] == "Neu eins [unterbrochen]"
    assert [w.ende_ms for w in pipe.plan] == [1000, 5500]


def _sprechen(pipe: object, ms_sprache: int = 800, ms_stille: int = 1500) -> None:
    t = 0
    pcm = list(audio.frames(audio.ton(200, ms_sprache, amp=0.3), 8000)) + list(
        audio.frames(audio.stille(ms_stille, rauschen=30), 8000)
    )
    for f in pcm:
        t += 20
        pipe.frame(f, t)  # type: ignore[attr-defined]


def test_asr_ausfall_fuehrt_zu_uebergabe_mit_ansage() -> None:
    pipe, _, audit = baue_pipeline(["Hallo?"], SimUhr())

    def kaputt(pcm: bytes, rate: int) -> str:
        raise TimeoutError("ASR antwortet nicht")

    pipe.asr.transkribieren = kaputt  # type: ignore[method-assign]
    _sprechen(pipe)
    assert pipe.uebergabe is not None and "technisch" in pipe.uebergabe["grund"]
    assert pipe.plan[-1].text == NOTFALL_TEXT, "nie still auflegen"
    assert [e for e in audit.eintraege if e["ereignis"] == "notfall_uebergabe"]


def test_tts_ausfall_uebergibt_ohne_ansage() -> None:
    pipe, _, audit = baue_pipeline(["Hallo?"], SimUhr())

    def kaputt(text: str) -> bytes:
        raise ConnectionError("TTS weg")

    pipe.tts.synthetisieren = kaputt  # type: ignore[method-assign]
    _sprechen(pipe)
    assert pipe.uebergabe is not None
    assert [e for e in audit.eintraege if e["ereignis"] == "notfall_ohne_ansage"]


def test_alle_modelle_ausgefallen_notfalltext() -> None:
    uhr = SimUhr()
    pipe, _, _ = baue_pipeline(
        ["Was sind eure Öffnungszeiten?"],
        uhr,
        backends=[Backend("tot", "lokal", RegelLLM(uhr, fehler=True))],
    )
    _sprechen(pipe)
    assert pipe.uebergabe is not None
    assert NOTFALL_TEXT in pipe.antworten[-1] or pipe.dialog.beendet


def test_leere_transkription_wird_ignoriert() -> None:
    pipe, _, _ = baue_pipeline([""], SimUhr())
    _sprechen(pipe)
    assert pipe.antworten == [] and pipe.uebergabe is None
