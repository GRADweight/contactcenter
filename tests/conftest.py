"""Gemeinsame Hilfen: Audit, Dialog-Aufbau und ein lokaler Fake-Server für
OpenAI-kompatible APIs (kein Netzwerkzugriff nach aussen)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from gw_contactcenter import audio
from gw_contactcenter.adapters.mock import RegelLLM
from gw_contactcenter.audit import AuditLog
from gw_contactcenter.dialog import Dialog
from gw_contactcenter.gateway import Backend, LLMGateway
from gw_contactcenter.tools import DemoBackend, standard_werkzeuge

SCHLUESSEL = b"k" * 32


@pytest.fixture
def audit() -> AuditLog:
    return AuditLog(schluessel=SCHLUESSEL, uhr=lambda: 1.0)


def baue_dialog(
    backends: list[Backend] | None = None, **kw: Any
) -> tuple[Dialog, DemoBackend, AuditLog]:
    a = AuditLog(schluessel=SCHLUESSEL, uhr=lambda: 1.0)
    backends = backends or [Backend("lokal", "lokal", RegelLLM()), Backend("eu", "eu", RegelLLM())]
    gw = LLMGateway(backends, audit=a)
    be = DemoBackend()
    return Dialog(gw, standard_werkzeuge(be, a), a, **kw), be, a


def antwort(d: Dialog, plan: Any) -> str:
    if plan.text:
        return str(plan.text)
    return " ".join(
        s.text for s in d.gateway.saetze(plan.llm_messages, klasse=plan.klasse, anruf_id=d.anruf_id)
    )


class FakeOpenAI(BaseHTTPRequestHandler):
    """Antwortet wie vLLM/speaches; Verhalten über Klassenattribute steuerbar."""

    verzoegerung_s = 0.0
    fehler_folge: list[int] = []  # noqa: RUF012 - HTTP-Codes, die nacheinander kommen
    anfragen: list[dict[str, Any]] = []  # noqa: RUF012

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(self, obj: Any, code: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        roh = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).anfragen.append({"pfad": self.path, "headers": dict(self.headers), "roh": roh})
        if type(self).fehler_folge:
            code = type(self).fehler_folge.pop(0)
            return self._json({"error": "simuliert"}, code)
        if self.verzoegerung_s:
            time.sleep(self.verzoegerung_s)
        if self.path.endswith("/chat/completions"):
            req = json.loads(roh)
            if req.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for teil in ["Hallo", " Welt.", " Zweiter", " Satz."]:
                    d = {"choices": [{"delta": {"content": teil}}]}
                    self.wfile.write(f"data: {json.dumps(d)}\n\n".encode())
                    self.wfile.flush()
                usage = {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 4}}
                self.wfile.write(f"data: {json.dumps(usage)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                return None
            text = '{"anliegen": "reklamation"}' if req.get("response_format") else "Antwort."
            return self._json(
                {
                    "choices": [{"message": {"content": text}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3},
                }
            )
        if self.path.endswith("/audio/transcriptions"):
            assert b'name="file"; filename=' in roh
            assert b"RIFF" in roh
            return self._json({"text": " Grüezi mitenand "})
        if self.path.endswith("/audio/speech"):
            req = json.loads(roh)
            if req.get("response_format") == "pcm":
                rate = int(req.get("sample_rate") or 24000)
                daten = audio.ton(300, 100, rate=rate)
                ctype = "audio/pcm"
            else:
                daten = audio.wav_bytes(audio.ton(300, 100, rate=24000), 24000)
                ctype = "audio/wav"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(daten)))
            self.end_headers()
            self.wfile.write(daten)
            return None
        return self._json({"error": "unbekannt"}, 404)


@pytest.fixture
def fake_openai() -> Iterator[str]:
    FakeOpenAI.verzoegerung_s = 0.0
    FakeOpenAI.fehler_folge = []
    FakeOpenAI.anfragen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAI)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    finally:
        srv.shutdown()
        srv.server_close()
