"""WebSocket-Server für Genesys Audio Connector / AudioHook.

Dünne Schicht: Netzwerk rein, AudioHookSitzung + Pipeline, Netzwerk raus.
Braucht ``pip install websockets`` (>= 13). Alles andere ist stdlib.

    python -m voiceagent.server --port 8080 --api-key geheim \\
        --llm-url http://vllm:8000/v1 --llm-modell Qwen/Qwen3-8B-AWQ \\
        --asr-url http://speaches:8000/v1 --tts-url http://speaches:8000/v1 \\
        --tts-modell <deutsche Piper-Stimme>

Ohne --llm-url/--asr-url/--tts-url laufen die Mock-Adapter (Demo).

Zusätzlich auf --metrics-port (Standard 9100): /metrics (Prometheus)
und /healthz. Hinter einen TLS-Terminator (Caddy, Traefik, nginx)
stellen: Genesys verbindet nur über wss://.

Wiedergabe: Audio wird in 20-ms-Frames gegen feste Fristen gesendet.
Ein Barge-in verwirft sofort alle noch nicht gesendeten Frames; bei
Genesys liegen höchstens wenige Frames im Puffer.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer

from .adapters.mock import MockASR, MockTTS, RegelLLM
from .audio import FRAME_MS, TELEFON_RATE, frame_bytes
from .audiohook import AudioHookSitzung, pruefe_header
from .audit import AuditLog
from .dialog import Dialog
from .gateway import Backend, LLMGateway
from .pipeline import Pipeline
from .telemetry import Messung
from .tools import DemoBackend, standard_werkzeuge

log = logging.getLogger("voiceagent")
MESSUNG = Messung()
AKTIV = {"sitzungen": 0}


def _uhr_ms() -> float:
    return time.monotonic() * 1000.0


def adapter_bauen(args):
    if args.llm_url:
        from .adapters.openai_compat import OpenAIKompatibelLLM
        extra = json.loads(os.getenv("LLM_EXTRA_JSON", "") or "{}")
        llm = OpenAIKompatibelLLM(args.llm_url, args.llm_modell, os.getenv("LLM_API_KEY", ""),
                                  extra=extra)
    else:
        llm = RegelLLM()
    if args.asr_url:
        from .adapters.openai_compat import HTTPASR
        asr = HTTPASR(args.asr_url, args.asr_modell)
    else:
        asr = MockASR(["Wo ist meine Bestellung?"] * 50)
    if args.tts_url:
        from .adapters.openai_compat import HTTPTTS
        tts = HTTPTTS(args.tts_url, args.tts_modell, args.tts_stimme)
    else:
        tts = MockTTS()
    return llm, asr, tts


def _sichere_id(roh: str) -> str:
    """Header-Wert als Dateiname: nur [A-Za-z0-9_-], max. 64 Zeichen (kein ../)."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", roh)[:64] or "unbekannt"


async def sitzung(ws, args):
    """Ein Anruf. Drei Aufgaben laufen nebeneinander:

    * Empfang: Steuernachrichten (ping, pause, close) sofort beantworten,
      Audio in eine Warteschlange legen. Blockiert nie.
    * Verarbeitung: Pipeline in einem EIGENEN Thread pro Gespräch, damit
      ein langsamer Modellaufruf weder andere Anrufe noch Pings aufhält.
    * Senden: Bot-Audio in 20-ms-Frames gegen feste Fristen (Echtzeit).
      Barge-in verwirft alle noch nicht gesendeten Frames sofort.

    Alle Genesys-Nachrichten (seq-Zähler) entstehen nur im Event-Loop.
    """
    headers = dict(ws.request.headers) if getattr(ws, "request", None) else {}
    fehler = pruefe_header(headers, args.api_key)
    if fehler:
        log.warning("Verbindung abgelehnt: %s", fehler)
        await ws.close(code=4001, reason=fehler[:100])
        return
    AKTIV["sitzungen"] += 1
    sid = _sichere_id({k.lower(): v for k, v in headers.items()}.get("audiohook-session-id", ""))
    ah = AudioHookSitzung(args.modus)
    llm, asr, tts = adapter_bauen(args)
    audit = AuditLog(os.path.join(args.audit_dir, f"{sid}.jsonl") if args.audit_dir else None,
                     os.getenv("AUDIT_HMAC_KEY", "").encode())
    gw = LLMGateway([Backend(args.llm_name, args.llm_region, llm)], audit=audit)
    dialog = Dialog(gw, standard_werkzeuge(DemoBackend(), audit), audit, anruf_id=sid)
    loop = asyncio.get_running_loop()
    arbeiter = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"anruf-{sid[:8]}")
    ton_raus: deque = deque()              # 20-ms-PCM-Frames, nur im Loop angefasst
    steuer_raus: asyncio.Queue = asyncio.Queue()
    audio_rein: asyncio.Queue = asyncio.Queue()

    # Eine Uhr für alles: Wandzeit, aber nie rückwärts und pro Frame mind. +20 ms
    # (Genesys liefert Pakete manchmal gebündelt).
    uhr = {"t": _uhr_ms()}

    def jetzt() -> float:
        return max(_uhr_ms(), uhr["t"])

    # Rückrufe aus dem Arbeiter-Thread: nur in den Loop weiterreichen.
    def audio_raus(pcm: bytes, start_ms: float):
        n = frame_bytes(TELEFON_RATE)
        frames = [pcm[i: i + n] for i in range(0, len(pcm), n)]
        loop.call_soon_threadsafe(ton_raus.extend, frames)

    def _barge():
        ton_raus.clear()                   # Rest sofort verwerfen
        steuer_raus.put_nowait(ah.barge_in())

    def _uebergabe(u: dict):
        steuer_raus.put_nowait(ah.beenden("transfer", variablen={
            "zielQueue": u["ziel"], "anliegen": u["anliegen"], "ticket": u.get("ticket", "")}))

    pipe = Pipeline(asr=asr, tts=tts, dialog=dialog, gateway=gw, uhr_ms=jetzt,
                    messung=MESSUNG, audio_raus=audio_raus,
                    bei_barge_in=lambda t: loop.call_soon_threadsafe(_barge),
                    bei_uebergabe=lambda u, t: loop.call_soon_threadsafe(_uebergabe, u))

    async def senden():
        frist = _uhr_ms()
        while True:
            while not steuer_raus.empty():
                await ws.send(json.dumps(steuer_raus.get_nowait()))
            if ton_raus:
                kodiert = ah.audio_senden(ton_raus.popleft())
                if kodiert:
                    await ws.send(kodiert)
                frist = max(frist + FRAME_MS, _uhr_ms() - FRAME_MS)
                await asyncio.sleep(max(0.0, (frist - _uhr_ms()) / 1000.0))
            else:
                frist = _uhr_ms()
                await asyncio.sleep(FRAME_MS / 2000.0)

    async def verarbeiten():
        n = frame_bytes(TELEFON_RATE)
        while True:
            kunde = await audio_rein.get()
            for i in range(0, len(kunde) - n + 1, n):
                uhr["t"] = max(_uhr_ms(), uhr["t"] + FRAME_MS)
                await loop.run_in_executor(arbeiter, pipe.frame, kunde[i: i + n], uhr["t"])

    aufgaben = [asyncio.create_task(senden()), asyncio.create_task(verarbeiten())]
    try:
        async for nachricht in ws:
            if isinstance(nachricht, str):
                for antwort in ah.text_empfangen(nachricht):
                    await ws.send(json.dumps(antwort))
                if ah.beendet:
                    break
                continue
            audio = ah.audio_empfangen(nachricht)
            if audio is not None:
                audio_rein.put_nowait(audio[0])
    finally:
        for a in aufgaben:
            a.cancel()
        arbeiter.shutdown(wait=False, cancel_futures=True)
        AKTIV["sitzungen"] -= 1


class _Metriken(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            body, ctype = b"ok\n", "text/plain"
        elif self.path == "/metrics":
            body = (MESSUNG.prometheus() +
                    f"voiceagent_aktive_sitzungen {AKTIV['sitzungen']}\n").encode()
            ctype = "text/plain; version=0.0.4"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    p = argparse.ArgumentParser(description="voiceagent AudioHook-Server")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--metrics-port", type=int, default=9100)
    p.add_argument("--modus", choices=["connector", "monitor"], default="connector")
    p.add_argument("--api-key", default=os.getenv("AUDIOHOOK_API_KEY", ""))
    p.add_argument("--audit-dir", default=os.getenv("AUDIT_DIR", ""))
    p.add_argument("--llm-url", default=os.getenv("LLM_URL", ""))
    p.add_argument("--llm-modell", default=os.getenv("LLM_MODELL", "Qwen/Qwen3-8B-AWQ"))
    p.add_argument("--llm-name", default=os.getenv("LLM_NAME", "lokal-vllm"))
    p.add_argument("--llm-region", default=os.getenv("LLM_REGION", "lokal"))
    p.add_argument("--asr-url", default=os.getenv("ASR_URL", ""))
    p.add_argument("--asr-modell", default=os.getenv("ASR_MODELL", "Systran/faster-whisper-large-v3"))
    p.add_argument("--tts-url", default=os.getenv("TTS_URL", ""))
    p.add_argument("--tts-modell", default=os.getenv("TTS_MODELL", ""))
    p.add_argument("--tts-stimme", default=os.getenv("TTS_STIMME", ""))
    args = p.parse_args()
    if not args.api_key:
        raise SystemExit("--api-key oder AUDIOHOOK_API_KEY ist Pflicht")
    logging.basicConfig(level=logging.INFO)
    try:
        from websockets.asyncio.server import serve
    except ImportError as e:  # pragma: no cover
        raise SystemExit("pip install 'websockets>=13'") from e

    threading.Thread(target=HTTPServer(("0.0.0.0", args.metrics_port), _Metriken).serve_forever,
                     daemon=True).start()

    async def run():
        async with serve(lambda ws: sitzung(ws, args), args.host, args.port, max_size=2**20):
            log.info("AudioHook-Server auf ws://%s:%d, Metriken auf :%d",
                     args.host, args.port, args.metrics_port)
            await asyncio.Future()

    asyncio.run(run())


if __name__ == "__main__":
    main()
