"""Turn-Taking: semantisches Endpointing, Barge-in, Backchannel."""

from __future__ import annotations

from gw_contactcenter.turn import (
    BACKCHANNEL,
    BARGE_IN,
    SPRACHE_START,
    TURN_ENDE,
    VERWORFEN,
    TurnConfig,
    TurnTaking,
    ist_backchannel,
    satzende_wahrscheinlichkeit,
)


def _fuettern(
    tt: TurnTaking, plan: list[tuple[bool, int]], teiltext: str = ""
) -> list[tuple[str, int, dict[str, object]]]:
    t, ev = 0, []
    for sprache, dauer in plan:
        for _ in range(dauer // 20):
            t += 20
            ev += [(e.typ, t, e.daten) for e in tt.frame(sprache, t, teiltext)]
    return ev


def test_semantik() -> None:
    assert satzende_wahrscheinlichkeit("Wo ist meine Bestellung?") > 0.8
    assert satzende_wahrscheinlichkeit("Ich wollte fragen, ob und") < 0.2
    assert satzende_wahrscheinlichkeit("Meine Nummer ist 44 71") < 0.5
    assert satzende_wahrscheinlichkeit("Wie lange dauert die Lieferung") == 0.75
    assert satzende_wahrscheinlichkeit("") == 0.5
    assert satzende_wahrscheinlichkeit("...!") == 0.9
    assert satzende_wahrscheinlichkeit("--") == 0.5
    assert satzende_wahrscheinlichkeit("Paket gekommen") == 0.6


def test_backchannel_erkennung() -> None:
    assert ist_backchannel("Mhm.")
    assert ist_backchannel("ja genau")
    assert not ist_backchannel("Nein, das stimmt nicht")


def test_fertiger_satz_endet_schnell() -> None:
    ev = _fuettern(TurnTaking(), [(True, 1000), (False, 600)], "Wo ist meine Bestellung?")
    ende = [e for e in ev if e[0] == TURN_ENDE]
    assert len(ende) == 1
    assert int(ende[0][2]["stille_ms"]) <= 400  # type: ignore[call-overload]
    assert ev[0][0] == SPRACHE_START


def _enden(plan: list[tuple[bool, int]], teiltext: str) -> int:
    return sum(e[0] == TURN_ENDE for e in _fuettern(TurnTaking(), plan, teiltext))


def test_unfertiger_satz_wartet() -> None:
    assert _enden([(True, 1000), (False, 700)], "Ich möchte und") == 0, "nicht abschneiden"
    assert _enden([(True, 1000), (False, 1400)], "Ich möchte und") == 1, "irgendwann Schluss"


def test_geraeusch_verworfen() -> None:
    assert VERWORFEN in [e[0] for e in _fuettern(TurnTaking(), [(True, 100), (False, 400)])]


def test_barge_in() -> None:
    tt = TurnTaking()
    tt.agent_beginnt(0)
    ev = _fuettern(tt, [(True, 400)], "Nein, das stimmt nicht")
    assert BARGE_IN in [e[0] for e in ev]
    assert not tt.agent_spricht


def test_backchannel_ist_kein_barge_in() -> None:
    tt = TurnTaking()
    tt.agent_beginnt(0)
    typen = [e[0] for e in _fuettern(tt, [(True, 400), (False, 600)], "mhm")]
    assert BARGE_IN not in typen
    assert BACKCHANNEL in typen
    assert TURN_ENDE not in typen
    assert tt.agent_spricht, "Bot spricht nach 'mhm' weiter"
    tt.agent_endet(1000)
    assert not tt.agent_spricht


def test_max_turn_sicherheitsnetz() -> None:
    tt = TurnTaking(TurnConfig(max_turn_ms=500))
    ende = [e for e in _fuettern(tt, [(True, 600)]) if e[0] == TURN_ENDE]
    assert ende and ende[0][2]["grund"] == "max_turn"


def test_semantik_einsteckbar() -> None:
    tt = TurnTaking(semantik=lambda text: 1.0)
    ende = [e for e in _fuettern(tt, [(True, 600), (False, 300)], "egal") if e[0] == TURN_ENDE]
    assert ende and ende[0][2]["satzende_p"] == 1.0
