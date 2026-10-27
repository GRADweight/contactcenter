"""Server: Betriebsmodi (fail-closed), Zugangsprüfung mit Signatur, WebSocket-Sitzung, Metriken."""

from __future__ import annotations

import asyncio
import base64
import json
import time
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

pytest.importorskip("websockets")

from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from gw_contactcenter import audio, server
from gw_contactcenter.audit import AuditLog
from gw_contactcenter.server import (
    KonfigurationsFehler,
    ServerKonfig,
    bedienen,
    konfig_aus,
    metriken_starten,
    zugang_pruefen,
)
from gw_contactcenter.signatur import NonceSpeicher, signieren

SECRET = b"s" * 32
SECRET_B64 = base64.b64encode(SECRET).decode()
HEADER = {
    "Audiohook-Organization-Id": "d7934305-0972-4844-938e-9060eef73d05",
    "Audiohook-Correlation-Id": "e160e428-53e2-487c-977d-96989bf5c99d",
    "Audiohook-Session-Id": "30b0e395-84d3-4570-ac13-9a62d8f514c0",
    "X-API-KEY": "api-key-test",
}


def _prod_env(**zusatz: str) -> dict[str, str]:
    env = {
        "AUDIOHOOK_API_KEY": "api-key-test",
        "AUDIOHOOK_CLIENT_SECRET": SECRET_B64,
        "AUDIT_HMAC_KEY": "a" * 64,
        "AUDIT_DIR": "/tmp/audit-test",  # noqa: S108 - nur Konfigurationswert, kein Zugriff
        "LLM_URL": "http://vllm:8000/v1",
        "ASR_URL": "http://speaches:8000/v1",
        "TTS_URL": "http://speaches:8000/v1",
        "TTS_MODELL": "piper-de",
        "TTS_STIMME": "thorsten",
    }
    env.update(zusatz)
    return env


def test_produktion_vollstaendig_hat_keine_maengel() -> None:
    k = konfig_aus([], _prod_env())
    assert k.modus == "produktion" and k.maengel() == []
    assert k.client_secret == SECRET


@pytest.mark.parametrize(
    ("fehlt", "meldung"),
    [
        ("AUDIOHOOK_API_KEY", "AUDIOHOOK_API_KEY fehlt"),
        ("AUDIOHOOK_CLIENT_SECRET", "Signaturprüfung"),
        ("AUDIT_HMAC_KEY", "AUDIT_HMAC_KEY fehlt"),
        ("AUDIT_DIR", "AUDIT_DIR fehlt"),
        ("LLM_URL", "LLM_URL fehlt"),
        ("TTS_STIMME", "TTS_MODELL und TTS_STIMME"),
    ],
)
def test_produktion_verweigert_start_ohne_pflichtwerte(fehlt: str, meldung: str) -> None:
    env = _prod_env()
    env.pop(fehlt)
    maengel = konfig_aus([], env).maengel()
    assert any(meldung in m for m in maengel), maengel


def test_kurzer_hmac_schluessel_und_falsche_werte() -> None:
    k = konfig_aus(
        ["--llm-region", "mars", "--tts-format", "mp3"], _prod_env(AUDIT_HMAC_KEY="kurz")
    )
    m = " | ".join(k.maengel())
    assert "kürzer als 32 Byte" in m and "LLM_REGION" in m and "TTS_FORMAT" in m
    assert ServerKonfig(modus="test", api_key="x").maengel()[0].startswith("Modus")


def test_ungueltige_konfiguration_bricht_ab() -> None:
    with pytest.raises(KonfigurationsFehler):
        konfig_aus([], _prod_env(AUDIOHOOK_CLIENT_SECRET="kein base64!"))
    with pytest.raises(KonfigurationsFehler):
        konfig_aus([], _prod_env(LLM_EXTRA_JSON="{kaputt"))
    with pytest.raises(KonfigurationsFehler):
        konfig_aus([], _prod_env(LLM_EXTRA_JSON="[1]"))


def test_main_verweigert_produktion_ohne_geheimnisse(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("AUDIOHOOK_API_KEY", "AUDIOHOOK_CLIENT_SECRET", "AUDIT_HMAC_KEY", "GW_MODUS"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(KonfigurationsFehler) as e:
        server.main([])
    assert "verweigert" in str(e.value.code)


def test_entwicklung_erlaubt_mocks() -> None:
    k = konfig_aus(["--modus", "entwicklung"], {"AUDIOHOOK_API_KEY": "x"})
    assert k.maengel() == []
    llm, asr, _ = server.adapter_bauen(k)
    assert type(llm).__name__ == "RegelLLM" and type(asr).__name__ == "MockASR"
    k2 = konfig_aus([], _prod_env(TTS_FORMAT="pcm", TTS_ABTASTRATE="8000"))
    _, _, tts2 = server.adapter_bauen(k2)
    assert type(tts2).__name__ == "HTTPTTS"


def test_breaker_und_tageskosten_gelten_prozessweit() -> None:
    k = konfig_aus(["--modus", "entwicklung"], {"AUDIOHOOK_API_KEY": "x"})
    g = server.Geteilt.aus(k)
    assert [(b.name, b.region) for b in g.backends] == [("lokal-vllm", "lokal")]
    audit = AuditLog(unsigniert_erlaubt=True)
    erster = server.LLMGateway(g.backends, audit, kosten=g.kosten)
    zweiter = server.LLMGateway(g.backends, audit, kosten=g.kosten)
    assert erster.backends[0].breaker is zweiter.backends[0].breaker
    assert erster.kosten is zweiter.kosten


def _header_klein() -> dict[str, str]:
    return {k.lower(): v for k, v in HEADER.items()}


def test_zugang_mit_und_ohne_signatur() -> None:
    k = konfig_aus([], _prod_env())
    h = _header_klein() | {"host": "voice.example.ch"}
    assert zugang_pruefen(k, h, "/", NonceSpeicher()) is not None, "ohne Signatur abgelehnt"
    sig = signieren(
        h,
        "/",
        keyid="api-key-test",
        client_secret=SECRET,
        created=int(time.time()),
        nonce="eine-nonce-mit-genug-zeichen",
    )
    assert zugang_pruefen(k, h | sig, "/", NonceSpeicher()) is None
    assert "API-Key" in (zugang_pruefen(k, h | {"x-api-key": "falsch"}, "/", NonceSpeicher()) or "")
    dev = konfig_aus(["--modus", "entwicklung"], {"AUDIOHOOK_API_KEY": "api-key-test"})
    assert zugang_pruefen(dev, h, "/", NonceSpeicher()) is None
    prod_ohne_secret = ServerKonfig(api_key="api-key-test")
    assert (
        zugang_pruefen(prod_ohne_secret, h, "/", NonceSpeicher())
        == "Signaturprüfung nicht konfiguriert"
    )


def test_metriken() -> None:
    srv = metriken_starten("127.0.0.1", 0)
    try:
        basis = f"http://127.0.0.1:{srv.server_address[1]}"
        assert urllib.request.urlopen(f"{basis}/healthz", timeout=3).read() == b"ok\n"
        assert (
            b"gw_contactcenter_aktive_sitzungen"
            in urllib.request.urlopen(f"{basis}/metrics", timeout=3).read()
        )
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"{basis}/nichts", timeout=3)
    finally:
        srv.shutdown()
        srv.server_close()


@asynccontextmanager
async def _laufender_server(k: ServerKonfig) -> AsyncIterator[int]:
    bereit = asyncio.Event()
    aufgabe = asyncio.create_task(bedienen(k, bereit))
    await asyncio.wait_for(bereit.wait(), 5)
    try:
        yield k.port
    finally:
        aufgabe.cancel()
        with pytest.raises(asyncio.CancelledError):
            await aufgabe


def _open(seq: int = 1) -> str:
    return json.dumps(
        {
            "version": "2",
            "type": "open",
            "seq": seq,
            "serverseq": 0,
            "id": HEADER["Audiohook-Session-Id"],
            "position": "PT0S",
            "parameters": {
                "organizationId": "o",
                "conversationId": "c",
                "media": [
                    {"type": "audio", "format": "PCMU", "channels": ["external"], "rate": 8000}
                ],
            },
        }
    )


async def _gespraech(port: int, header: dict[str, str]) -> set[str]:
    async with connect(f"ws://127.0.0.1:{port}/", additional_headers=header) as ws:
        await ws.send(_open())
        assert json.loads(await ws.recv())["type"] == "opened"
        for pcm in list(audio.frames(audio.ton(200, 1200, amp=0.3), 8000)) + list(
            audio.frames(audio.stille(1500, rauschen=30), 8000)
        ):
            await ws.send(audio.pcm16_to_ulaw(pcm))
        binaer, deadline = 0, time.time() + 8
        while time.time() < deadline and binaer < 5:
            m = await asyncio.wait_for(ws.recv(), timeout=8)
            if isinstance(m, bytes):
                binaer += 1
                assert len(m) == 160, "20 ms PCMU = 160 Byte"
        assert binaer >= 5, "Bot-Audio kommt zurück"
        sid = HEADER["Audiohook-Session-Id"]
        await ws.send(
            json.dumps(
                {
                    "version": "2",
                    "type": "ping",
                    "seq": 2,
                    "serverseq": 1,
                    "id": sid,
                    "position": "PT3S",
                    "parameters": {},
                }
            )
        )
        await ws.send(
            json.dumps(
                {
                    "version": "2",
                    "type": "close",
                    "seq": 3,
                    "serverseq": 1,
                    "id": sid,
                    "position": "PT3S",
                    "parameters": {"reason": "end"},
                }
            )
        )
        typen: set[str] = set()
        while "closed" not in typen:
            m = await asyncio.wait_for(ws.recv(), timeout=5)
            if isinstance(m, str):
                typen.add(json.loads(m)["type"])
        return typen


@pytest.fixture(autouse=True)
def _keine_wartezeit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "MIN_FEHLER_VERZOEGERUNG_S", 0.0)


def test_websocket_sitzung_entwicklungsmodus(tmp_path: Any) -> None:
    k = konfig_aus(
        ["--modus", "entwicklung", "--port", "0", "--audit-dir", str(tmp_path)],
        {"AUDIOHOOK_API_KEY": "api-key-test"},
    )

    async def ablauf() -> None:
        async with _laufender_server(k) as port:
            typen = await _gespraech(port, HEADER)
            assert "pong" in typen
            with pytest.raises(InvalidStatus) as e:
                async with connect(
                    f"ws://127.0.0.1:{port}/", additional_headers=HEADER | {"X-API-KEY": "x"}
                ):
                    pass
            assert e.value.response.status_code == 401

    asyncio.run(ablauf())
    dateien = list(tmp_path.iterdir())
    assert dateien and dateien[0].name.endswith(".jsonl"), "Audit pro Sitzung geschrieben"


def test_websocket_produktion_mit_signatur_und_kapazitaetsgrenze(tmp_path: Any) -> None:
    k = konfig_aus(
        ["--port", "0", "--audit-dir", str(tmp_path), "--max-sitzungen", "1"],
        _prod_env(LLM_URL="", ASR_URL="", TTS_URL=""),
    )
    k.modus = "produktion"  # Mock-Adapter nur für den Test; Zugangsprüfung wie in Produktion

    async def ablauf() -> None:
        async with _laufender_server(k) as port:
            h = _header_klein() | {"host": f"127.0.0.1:{port}"}
            sig = signieren(
                h,
                "/",
                keyid="api-key-test",
                client_secret=SECRET,
                created=int(time.time()),
                nonce="nonce-fuer-den-servertest-1",
            )
            with pytest.raises(InvalidStatus):  # ohne Signatur
                async with connect(f"ws://127.0.0.1:{port}/", additional_headers=HEADER):
                    pass
            typen = await _gespraech(port, HEADER | sig)
            assert "closed" in typen
            with pytest.raises(InvalidStatus):  # dieselbe Nonce nochmals: Wiedereinspielung
                async with connect(f"ws://127.0.0.1:{port}/", additional_headers=HEADER | sig):
                    pass
            server.AKTIV["sitzungen"] = 1  # Kapazitätsgrenze erreicht
            try:
                with pytest.raises(InvalidStatus) as e:
                    async with connect(f"ws://127.0.0.1:{port}/", additional_headers=HEADER | sig):
                        pass
                assert e.value.response.status_code == 503
            finally:
                server.AKTIV["sitzungen"] = 0

    asyncio.run(ablauf())


def test_protokollfehler_im_audio_beendet_sitzung() -> None:
    k = konfig_aus(["--modus", "entwicklung", "--port", "0"], {"AUDIOHOOK_API_KEY": "api-key-test"})

    async def ablauf() -> None:
        async with (
            _laufender_server(k) as port,
            connect(f"ws://127.0.0.1:{port}/", additional_headers=HEADER) as ws,
        ):
            await ws.send(b"\xff" * 160)  # Audio vor 'open'
            m = json.loads(await asyncio.wait_for(ws.recv(), 5))
            assert m["type"] == "disconnect" and m["parameters"]["reason"] == "error"

    asyncio.run(ablauf())
