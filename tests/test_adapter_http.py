"""OpenAI-kompatible HTTP-Adapter gegen einen lokalen Fake-Server (kein Netz nach aussen)."""

from __future__ import annotations

import json
import urllib.error

import pytest

from gw_contactcenter import audio
from gw_contactcenter.adapters.openai_compat import (
    HTTPASR,
    HTTPTTS,
    AntwortFehler,
    HTTPFehler,
    OpenAIKompatibelLLM,
    http_post,
)
from gw_contactcenter.audit import AuditLog
from gw_contactcenter.gateway import Backend, LLMGateway

from .conftest import SCHLUESSEL, FakeOpenAI

MSG = [{"role": "user", "content": "x"}]


def test_llm_complete_json_und_stream(fake_openai: str) -> None:
    llm = OpenAIKompatibelLLM(
        fake_openai, "m", api_key="k", extra={"chat_template_kwargs": {"x": 1}}
    )
    r = llm.complete(MSG)
    assert (r["text"], r["tokens_ein"], r["tokens_aus"]) == ("Antwort.", 7, 3)
    assert "reklamation" in llm.complete(MSG, json_modus=True)["text"]
    assert "".join(llm.stream(MSG)) == "Hallo Welt. Zweiter Satz."
    body = json.loads(FakeOpenAI.anfragen[0]["roh"])
    assert body["chat_template_kwargs"] == {"x": 1}
    assert FakeOpenAI.anfragen[0]["headers"]["Authorization"] == "Bearer k"


def test_gateway_streamt_saetze_ueber_http(fake_openai: str) -> None:
    gw = LLMGateway(
        [Backend("vllm", "lokal", OpenAIKompatibelLLM(fake_openai, "m"))],
        audit=AuditLog(schluessel=SCHLUESSEL),
    )
    assert [s.text for s in gw.saetze(MSG, klasse=2, anruf_id="a")] == [
        "Hallo Welt.",
        "Zweiter Satz.",
    ]


def test_wiederholung_bei_503_aber_nicht_bei_400(fake_openai: str) -> None:
    schlaf: list[float] = []
    FakeOpenAI.fehler_folge = [503]
    with http_post(
        f"{fake_openai}/chat/completions",
        b'{"model":"m","messages":[]}',
        {"Content-Type": "application/json"},
        timeout_s=5,
        wiederholungen=1,
        schlafen=schlaf.append,
    ) as r:
        assert r.status == 200
    assert schlaf == [0.2]
    FakeOpenAI.fehler_folge = [400]
    with pytest.raises(HTTPFehler) as e:
        OpenAIKompatibelLLM(fake_openai, "m").complete(MSG)
    assert isinstance(e.value.__cause__, urllib.error.HTTPError)
    FakeOpenAI.fehler_folge = [503, 503]
    with pytest.raises(HTTPFehler):
        http_post(
            f"{fake_openai}/chat/completions",
            b"{}",
            {},
            timeout_s=5,
            wiederholungen=1,
            schlafen=lambda s: None,
        )


def test_verbindungsfehler() -> None:
    with pytest.raises(HTTPFehler):
        OpenAIKompatibelLLM("http://127.0.0.1:9/v1", "m", timeout_s=0.5, wiederholungen=0).complete(
            MSG
        )


def test_asr_und_tts_wav(fake_openai: str) -> None:
    assert HTTPASR(fake_openai).transkribieren(audio.ton(200, 100), 8000) == "Grüezi mitenand"
    tts = HTTPTTS(fake_openai, "piper-de", "thorsten")
    pcm = tts.synthetisieren("Hallo")
    assert len(pcm) / 2 / 8000 == pytest.approx(0.1, abs=0.002), "auf 8 kHz umgetastet"
    req = json.loads(FakeOpenAI.anfragen[-1]["roh"])
    assert req == {
        "model": "piper-de",
        "input": "Hallo",
        "voice": "thorsten",
        "response_format": "wav",
    }


def test_tts_pcm_mit_abtastrate(fake_openai: str) -> None:
    tts = HTTPTTS(fake_openai, "piper-de", "thorsten", antwortformat="pcm", abtastrate=8000)
    pcm = tts.synthetisieren("Hallo")
    assert len(pcm) == 2 * 800
    assert json.loads(FakeOpenAI.anfragen[-1]["roh"])["sample_rate"] == 8000
    ohne_rate = HTTPTTS(fake_openai, "m", "v", antwortformat="pcm")
    assert len(ohne_rate.synthetisieren("x")) == 2 * 800, "OpenAI-PCM: 24 kHz, umgetastet"


def test_tts_parameter_pflicht() -> None:
    with pytest.raises(ValueError):
        HTTPTTS("http://x", "", "v")
    with pytest.raises(ValueError):
        HTTPTTS("http://x", "m", "")
    with pytest.raises(ValueError):
        HTTPTTS("http://x", "m", "v", antwortformat="mp3")  # type: ignore[arg-type]


class _Kaputt(FakeOpenAI):
    """Antwortet mit HTTP 200, aber im falschen Format."""

    def do_POST(self) -> None:
        roh = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path.endswith("/chat/completions") and json.loads(roh).get("stream"):
            body = b"data: {kaputt}\n\n"
        elif self.path.endswith("/chat/completions"):
            body = b'{"choices": []}'
        elif self.path.endswith("/audio/transcriptions"):
            body = b"kein json"
        else:
            body = b"kein wav"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_strenge_antwortpruefung() -> None:
    import threading
    from http.server import ThreadingHTTPServer

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Kaputt)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    try:
        with pytest.raises(AntwortFehler):
            OpenAIKompatibelLLM(url, "m").complete(MSG)
        with pytest.raises(AntwortFehler):
            list(OpenAIKompatibelLLM(url, "m").stream(MSG))
        with pytest.raises(AntwortFehler):
            HTTPASR(url).transkribieren(b"\x00\x00" * 80, 8000)
        with pytest.raises(AntwortFehler):
            HTTPTTS(url, "m", "v").synthetisieren("x")
    finally:
        srv.shutdown()
        srv.server_close()
