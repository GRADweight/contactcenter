"""AudioHook-Protokoll gemäss Genesys-Referenzimplementierung."""

from __future__ import annotations

import json
from typing import Any

import pytest

from gw_contactcenter import audio
from gw_contactcenter.audiohook import AudioHookSitzung, pruefe_header

SID = "30b0e395-84d3-4570-ac13-9a62d8f514c0"
MONO = [{"type": "audio", "format": "PCMU", "channels": ["external"], "rate": 8000}]
STEREO = [{"type": "audio", "format": "PCMU", "channels": ["external", "internal"], "rate": 8000}]


def nachricht(typ: str, seq: int, serverseq: int = 0, **parameter: Any) -> str:
    return json.dumps(
        {
            "version": "2",
            "id": SID,
            "type": typ,
            "seq": seq,
            "serverseq": serverseq,
            "position": f"PT{seq * 0.5}S",
            "parameters": parameter,
        }
    )


def geoeffnet(
    modus: str = "connector", media: list[dict[str, Any]] | None = None
) -> AudioHookSitzung:
    s = AudioHookSitzung(modus)
    [o] = s.text_empfangen(
        nachricht("open", 1, organizationId="o", conversationId="c", media=media or MONO)
    )
    assert o["type"] == "opened"
    return s


def test_open_opened_ping_close() -> None:
    s = AudioHookSitzung("connector")
    [o] = s.text_empfangen(
        nachricht(
            "open",
            1,
            organizationId="o",
            conversationId="c",
            language="de-CH",
            media=STEREO + MONO,
            inputVariables={"kanal": "hotline"},
            supportedLanguages=True,
        )
    )
    assert o["type"] == "opened"
    assert (o["seq"], o["clientseq"], o["id"]) == (1, 1, SID)
    assert "position" not in o, "Server-Nachrichten tragen keine position"
    assert o["parameters"]["media"] == MONO, "Connector nimmt nur 'external'"
    assert o["parameters"]["supportedLanguages"] == ["de-CH", "de-DE"]
    assert s.input_variables == {"kanal": "hotline"}
    assert s.sprache == "de-CH"
    [pong] = s.text_empfangen(nachricht("ping", 2, 1, rtt="PT0.05S"))
    assert (pong["type"], pong["seq"], pong["clientseq"]) == ("pong", 2, 2)
    [closed] = s.text_empfangen(nachricht("close", 3, 2, reason="end"))
    assert (closed["type"], closed["parameters"]) == ("closed", {})
    assert s.beendet and s.protokoll == []


def test_disconnect_mit_output_variablen_als_strings() -> None:
    s = geoeffnet()
    d = s.beenden("transfer", variablen={"zielQueue": "Fachbereich", "sla": 2.0, "leer": None})
    assert d["type"] == "disconnect"
    assert d["parameters"]["reason"] == "completed"
    assert d["parameters"]["outputVariables"] == {
        "action": "transfer",
        "zielQueue": "Fachbereich",
        "sla": "2.0",
        "leer": "",
    }
    with pytest.raises(ValueError):
        s.beenden(grund="kaputt")
    assert s.beenden(info="x")["parameters"]["info"] == "x"


def test_paused_resumed_kommen_vom_client() -> None:
    s = geoeffnet()
    assert s.audio_empfangen(b"\xff" * 160) is not None
    assert s.text_empfangen(nachricht("paused", 2)) == []
    assert s.pausiert
    assert s.audio_empfangen(b"\xff" * 160) is None
    assert s.audio_senden(b"\x00\x00" * 160) is None
    assert s.text_empfangen(nachricht("resumed", 3, start="PT1S", discarded="PT0.5S")) == []
    assert not s.pausiert
    assert s.audio_senden(b"\x00\x00" * 160) == b"\xff" * 160


def test_monitor_stereo_kanaele_in_angebotener_reihenfolge() -> None:
    s = geoeffnet("monitor", STEREO)
    assert s.kanaele == ["external", "internal"]
    kanaele = s.audio_empfangen(audio.pcm16_to_ulaw(audio.ton(200, 20)) * 2)
    assert kanaele is not None and len(kanaele) == 2
    assert s.kanal(kanaele, "external") is not None
    assert s.kanal(kanaele, "unbekannt") is None
    assert s.audio_senden(b"\x00\x00" * 160) is None, "Monitor sendet nie Audio"
    with pytest.raises(ValueError):
        s.audio_empfangen(b"\xff" * 161)  # nicht durch zwei Kanäle teilbar


def test_update_dtmf_discarded_error_playback() -> None:
    s = geoeffnet()
    assert s.text_empfangen(nachricht("update", 2, language="de-DE")) == []
    assert s.sprache == "de-DE"
    assert s.text_empfangen(nachricht("dtmf", 3, digit="5")) == []
    assert s.text_empfangen(nachricht("dtmf", 4, digit="x")) == []
    assert s.dtmf == ["5"]
    for i, typ in enumerate(["discarded", "error", "playback_started", "playback_completed"], 5):
        assert s.text_empfangen(nachricht(typ, i)) == []
    assert len(s.protokoll) == 2


def test_barge_in_ereignis() -> None:
    e = geoeffnet().barge_in()
    assert e["type"] == "event"
    assert e["parameters"]["entities"] == [{"type": "barge_in", "data": {}}]


@pytest.mark.parametrize(
    ("roh", "grund"),
    [
        ("{kaputt", "kein gültiges JSON"),
        ("[]", "kein JSON-Objekt"),
        (
            json.dumps(
                {
                    "version": "1",
                    "id": SID,
                    "type": "ping",
                    "seq": 2,
                    "serverseq": 0,
                    "position": "PT1S",
                    "parameters": {},
                }
            ),
            "wohlgeformte",
        ),
        (nachricht("ping", 5), "seq 5 folgt nicht"),
        (nachricht("ping", 2, serverseq=99), "serverseq"),
        (nachricht("fax", 2), "keine unterstützte"),
        (
            json.dumps(
                {
                    "version": "2",
                    "id": "anders",
                    "type": "ping",
                    "seq": 2,
                    "serverseq": 0,
                    "position": "PT1S",
                    "parameters": {},
                }
            ),
            "Sitzungs-ID",
        ),
        (
            json.dumps(
                {
                    "version": "2",
                    "id": SID,
                    "type": "ping",
                    "seq": 2,
                    "serverseq": 0,
                    "position": "1s",
                    "parameters": {},
                }
            ),
            "wohlgeformte",
        ),
        ("x" * 70000, "64 KiB"),
    ],
)
def test_protokollfehler_fuehren_zu_disconnect_error(roh: str, grund: str) -> None:
    s = geoeffnet()
    [d] = s.text_empfangen(roh)
    assert d["type"] == "disconnect"
    assert d["parameters"]["reason"] == "error"
    assert grund in d["parameters"]["info"]
    assert s.beendet
    assert s.text_empfangen(nachricht("ping", 3)) == [], "nach Fehler keine Antworten mehr"


def test_open_ohne_passendes_medium_und_nachricht_vor_open() -> None:
    s = AudioHookSitzung()
    l16 = [{"type": "audio", "format": "L16", "channels": ["external"], "rate": 8000}]
    [d] = s.text_empfangen(nachricht("open", 1, media=l16))
    assert d["parameters"]["reason"] == "error"
    s2 = AudioHookSitzung()
    [d2] = s2.text_empfangen(nachricht("ping", 1))
    assert "vor 'open'" in d2["parameters"]["info"]
    s3 = AudioHookSitzung()
    with pytest.raises(ValueError):
        s3.audio_empfangen(b"\xff" * 160)


def test_zweites_open_wird_ignoriert_und_unberechtigt() -> None:
    s = geoeffnet()
    assert s.text_empfangen(nachricht("open", 2, media=MONO)) == []
    d = s.unberechtigt("falscher Key")
    assert d["parameters"] == {"reason": "unauthorized", "info": "falscher Key"}
    assert s.beendet


def test_modus_pruefung() -> None:
    with pytest.raises(ValueError):
        AudioHookSitzung("bot")


def test_header() -> None:
    h = {
        "Audiohook-Organization-Id": "o",
        "Audiohook-Correlation-Id": "c",
        "Audiohook-Session-Id": "s",
        "X-API-KEY": "geheim",
    }
    assert pruefe_header(h, "geheim") is None
    assert pruefe_header(h, "anders") == "API-Key ungültig"
    assert pruefe_header(h, "") == "API-Key ungültig"
    assert "fehlende" in (pruefe_header({"X-API-KEY": "geheim"}, "geheim") or "")
