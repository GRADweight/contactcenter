"""WebSocket-Server für den Genesys Audio Connector.

Dünne Schicht: Netzwerk rein, :class:`.audiohook.AudioHookSitzung` plus
:class:`.pipeline.Pipeline`, Netzwerk raus. Braucht das Extra ``server``
(``pip install "gradweight-contactcenter[server]"``).

Betriebsmodi (``--modus`` oder ``GW_MODUS``):

``produktion`` (Standard)
    Startet nur, wenn alles für einen sicheren Betrieb gesetzt ist, sonst
    bricht er mit einer Liste aller Mängel ab (fail-closed):
    ``AUDIOHOOK_API_KEY``, ``AUDIOHOOK_CLIENT_SECRET`` (Signaturprüfung),
    ``AUDIT_HMAC_KEY`` (mindestens 32 Byte), ``AUDIT_DIR`` sowie echte
    Endpunkte für LLM, ASR und TTS (keine Mock-Adapter).
``entwicklung``
    Erlaubt Mock-Adapter, unsigniertes Audit und Verbindungen ohne
    Signatur. Nur lokal verwenden.

Der Server gehört hinter einen TLS-Terminator (z. B. Caddy, s. ``deploy/``);
Genesys verbindet nur über ``wss://``. Zusätzlich auf ``--metrics-port``:
``/metrics`` (Prometheus) und ``/healthz``.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import json
import logging
import os
import re
import threading
import time
from collections import deque
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any

from .adapters.mock import MockASR, MockTTS, RegelLLM
from .audio import FRAME_MS, TELEFON_RATE, frame_bytes
from .audiohook import AudioHookSitzung, pruefe_header
from .audit import MIN_SCHLUESSEL_BYTE, AuditLog
from .dialog import Dialog
from .gateway import LLM, Backend, LLMGateway
from .pipeline import ASR, TTS, Pipeline
from .policy import REGIONEN
from .signatur import NonceSpeicher, pruefe_signatur
from .telemetry import Messung
from .tools import DemoBackend, standard_werkzeuge

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection
    from websockets.http11 import Request, Response

log = logging.getLogger("gw_contactcenter")
MESSUNG = Messung()
AKTIV = {"sitzungen": 0}
MIN_FEHLER_VERZOEGERUNG_S = 0.5  # gegen Timing-Seitenkanäle (wie Genesys-Referenz)


class KonfigurationsFehler(SystemExit):
    """Server darf in dieser Konfiguration nicht starten."""


@dataclass
class ServerKonfig:
    modus: str = "produktion"
    host: str = "127.0.0.1"
    port: int = 8080
    metrics_port: int = 9100
    api_key: str = ""
    client_secret: bytes = b""
    audit_schluessel: bytes = b""
    audit_dir: str = ""
    max_sitzungen: int = 0
    llm_url: str = ""
    llm_modell: str = "Qwen/Qwen3-8B-AWQ"
    llm_name: str = "lokal-vllm"
    llm_region: str = "lokal"
    llm_extra: dict[str, Any] = field(default_factory=dict)
    llm_api_key: str = ""
    asr_url: str = ""
    asr_modell: str = "Systran/faster-whisper-large-v3"
    tts_url: str = ""
    tts_modell: str = ""
    tts_stimme: str = ""
    tts_format: str = "wav"
    tts_abtastrate: int = 0

    @property
    def entwicklung(self) -> bool:
        return self.modus == "entwicklung"

    def maengel(self) -> list[str]:
        """Alle Gründe, warum der Server in diesem Modus nicht starten darf."""
        m: list[str] = []
        if self.modus not in ("produktion", "entwicklung"):
            m.append(f"Modus {self.modus!r} unbekannt (produktion | entwicklung)")
        if not self.api_key:
            m.append("AUDIOHOOK_API_KEY fehlt")
        if self.llm_region not in REGIONEN:
            m.append(f"LLM_REGION {self.llm_region!r} unbekannt")
        if self.tts_format not in ("wav", "pcm"):
            m.append("TTS_FORMAT muss wav oder pcm sein")
        if self.audit_schluessel and len(self.audit_schluessel) < MIN_SCHLUESSEL_BYTE:
            m.append(f"AUDIT_HMAC_KEY kürzer als {MIN_SCHLUESSEL_BYTE} Byte")
        if self.entwicklung:
            return m
        if not self.client_secret:
            m.append("AUDIOHOOK_CLIENT_SECRET fehlt (Signaturprüfung ist in Produktion Pflicht)")
        if not self.audit_schluessel:
            m.append("AUDIT_HMAC_KEY fehlt (Audit ohne HMAC nur im Entwicklungsmodus)")
        if not self.audit_dir:
            m.append("AUDIT_DIR fehlt (Audit muss persistent sein)")
        for name, wert in (
            ("LLM_URL", self.llm_url),
            ("ASR_URL", self.asr_url),
            ("TTS_URL", self.tts_url),
        ):
            if not wert:
                m.append(f"{name} fehlt (Mock-Adapter nur im Entwicklungsmodus)")
        if self.tts_url and (not self.tts_modell or not self.tts_stimme):
            m.append("TTS_MODELL und TTS_STIMME setzen (deutsche Stimme)")
        return m


def _b64(name: str, wert: str) -> bytes:
    if not wert:
        return b""
    try:
        return base64.b64decode(wert, validate=True)
    except binascii.Error as e:
        raise KonfigurationsFehler(f"{name} ist kein gültiges Base64") from e


def konfig_aus(
    argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None
) -> ServerKonfig:
    e = os.environ if env is None else env
    p = argparse.ArgumentParser(
        prog="gw-contactcenter server", description="Genesys-Audio-Connector-Server"
    )
    p.add_argument("--modus", default=e.get("GW_MODUS", "produktion"))
    p.add_argument("--host", default=e.get("HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(e.get("PORT", "8080")))
    p.add_argument("--metrics-port", type=int, default=int(e.get("METRICS_PORT", "9100")))
    p.add_argument("--max-sitzungen", type=int, default=int(e.get("MAX_SITZUNGEN", "0")))
    p.add_argument("--audit-dir", default=e.get("AUDIT_DIR", ""))
    p.add_argument("--llm-url", default=e.get("LLM_URL", ""))
    p.add_argument("--llm-modell", default=e.get("LLM_MODELL", "Qwen/Qwen3-8B-AWQ"))
    p.add_argument("--llm-name", default=e.get("LLM_NAME", "lokal-vllm"))
    p.add_argument("--llm-region", default=e.get("LLM_REGION", "lokal"))
    p.add_argument("--asr-url", default=e.get("ASR_URL", ""))
    p.add_argument("--asr-modell", default=e.get("ASR_MODELL", "Systran/faster-whisper-large-v3"))
    p.add_argument("--tts-url", default=e.get("TTS_URL", ""))
    p.add_argument("--tts-modell", default=e.get("TTS_MODELL", ""))
    p.add_argument("--tts-stimme", default=e.get("TTS_STIMME", ""))
    p.add_argument("--tts-format", default=e.get("TTS_FORMAT", "wav"))
    p.add_argument("--tts-abtastrate", type=int, default=int(e.get("TTS_ABTASTRATE", "0")))
    a = p.parse_args(list(argv) if argv is not None else None)
    try:
        extra = json.loads(e.get("LLM_EXTRA_JSON", "") or "{}")
    except json.JSONDecodeError as err:
        raise KonfigurationsFehler("LLM_EXTRA_JSON ist kein gültiges JSON") from err
    if not isinstance(extra, dict):
        raise KonfigurationsFehler("LLM_EXTRA_JSON muss ein JSON-Objekt sein")
    # Geheimnisse nur aus der Umgebung, nie als Kommandozeilenargument (ps, Shell-Historie).
    return ServerKonfig(
        modus=a.modus,
        host=a.host,
        port=a.port,
        metrics_port=a.metrics_port,
        api_key=e.get("AUDIOHOOK_API_KEY", ""),
        client_secret=_b64("AUDIOHOOK_CLIENT_SECRET", e.get("AUDIOHOOK_CLIENT_SECRET", "")),
        audit_schluessel=e.get("AUDIT_HMAC_KEY", "").encode(),
        audit_dir=a.audit_dir,
        max_sitzungen=a.max_sitzungen,
        llm_url=a.llm_url,
        llm_modell=a.llm_modell,
        llm_name=a.llm_name,
        llm_region=a.llm_region,
        llm_extra=extra,
        llm_api_key=e.get("LLM_API_KEY", ""),
        asr_url=a.asr_url,
        asr_modell=a.asr_modell,
        tts_url=a.tts_url,
        tts_modell=a.tts_modell,
        tts_stimme=a.tts_stimme,
        tts_format=a.tts_format,
        tts_abtastrate=a.tts_abtastrate,
    )


def adapter_bauen(k: ServerKonfig) -> tuple[LLM, ASR, TTS]:
    from .adapters.openai_compat import HTTPASR, HTTPTTS, OpenAIKompatibelLLM

    llm: LLM = (
        OpenAIKompatibelLLM(k.llm_url, k.llm_modell, k.llm_api_key, extra=k.llm_extra)
        if k.llm_url
        else RegelLLM()
    )
    asr: ASR = (
        HTTPASR(k.asr_url, k.asr_modell)
        if k.asr_url
        else MockASR(["Wo ist meine Bestellung?"] * 50)
    )
    tts: TTS = (
        HTTPTTS(
            k.tts_url,
            k.tts_modell,
            k.tts_stimme,
            antwortformat="pcm" if k.tts_format == "pcm" else "wav",
            abtastrate=k.tts_abtastrate or None,
        )
        if k.tts_url
        else MockTTS()
    )
    return llm, asr, tts


def _sichere_id(roh: str) -> str:
    """Header-Wert als Dateiname: nur [A-Za-z0-9_-], höchstens 64 Zeichen."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", roh)[:64] or "unbekannt"


def header_dict(headers: Any) -> dict[str, str]:
    """websockets-Headers -> Kleinbuchstaben-Dict; Mehrfachwerte mit ', ' verbunden."""
    out: dict[str, str] = {}
    for k in {str(k).lower() for k in headers}:
        werte = headers.get_all(k) if hasattr(headers, "get_all") else [headers[k]]
        out[k] = ", ".join(str(w) for w in werte)
    return out


def zugang_pruefen(
    k: ServerKonfig, headers: dict[str, str], pfad: str, nonces: NonceSpeicher
) -> str | None:
    """None = Zugang erlaubt, sonst Grund der Ablehnung."""
    fehler = pruefe_header(headers, k.api_key)
    if fehler:
        return fehler
    if not k.client_secret:
        return None if k.entwicklung else "Signaturprüfung nicht konfiguriert"
    secret = k.client_secret
    ergebnis = pruefe_signatur(
        headers, pfad, lambda keyid: secret if keyid == k.api_key else None, nonces=nonces
    )
    return None if ergebnis.ok else f"Signatur: {ergebnis.code} {ergebnis.grund}"


async def sitzung(ws: Any, k: ServerKonfig) -> None:
    """Ein Anruf. Drei Aufgaben laufen nebeneinander:

    * Empfang: Steuernachrichten sofort beantworten, Audio in eine
      Warteschlange legen. Blockiert nie.
    * Verarbeitung: Pipeline in einem eigenen Thread pro Gespräch, damit ein
      langsamer Modellaufruf weder andere Anrufe noch Pings aufhält.
    * Senden: Bot-Audio in 20-ms-Frames gegen feste Fristen; Barge-in
      verwirft alle noch nicht gesendeten Frames sofort.
    """
    headers = header_dict(ws.request.headers)
    sid = _sichere_id(headers.get("audiohook-session-id", ""))
    AKTIV["sitzungen"] += 1
    arbeiter = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"anruf-{sid[:8]}")
    aufgaben: list[asyncio.Task[None]] = []
    try:
        ah = AudioHookSitzung("connector")
        llm, asr, tts = adapter_bauen(k)
        audit = AuditLog(
            os.path.join(k.audit_dir, f"{sid}.jsonl") if k.audit_dir else None,
            schluessel=k.audit_schluessel or None,
            unsigniert_erlaubt=k.entwicklung,
        )
        gw = LLMGateway([Backend(k.llm_name, k.llm_region, llm)], audit=audit)
        dialog = Dialog(gw, standard_werkzeuge(DemoBackend(), audit), audit, anruf_id=sid)
        loop = asyncio.get_running_loop()
        ton_raus: deque[bytes] = deque()  # 20-ms-Frames, nur im Event-Loop angefasst
        steuer_raus: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        audio_rein: asyncio.Queue[bytes] = asyncio.Queue()
        uhr = {"t": time.monotonic() * 1000.0}

        def jetzt() -> float:
            return max(time.monotonic() * 1000.0, uhr["t"])

        def audio_raus(pcm: bytes, start_ms: float) -> None:
            n = frame_bytes(TELEFON_RATE)
            loop.call_soon_threadsafe(
                ton_raus.extend, [pcm[i : i + n] for i in range(0, len(pcm), n)]
            )

        def _barge() -> None:
            ton_raus.clear()
            steuer_raus.put_nowait(ah.barge_in())

        def _uebergabe(u: dict[str, Any]) -> None:
            steuer_raus.put_nowait(
                ah.beenden(
                    "transfer",
                    variablen={
                        "zielQueue": u["ziel"],
                        "anliegen": u["anliegen"],
                        "ticket": u.get("ticket", ""),
                    },
                )
            )

        def barge_melden(t_ms: float) -> None:
            loop.call_soon_threadsafe(_barge)

        def uebergabe_melden(u: dict[str, Any], t_ms: float) -> None:
            loop.call_soon_threadsafe(_uebergabe, u)

        pipe = Pipeline(
            asr=asr,
            tts=tts,
            dialog=dialog,
            gateway=gw,
            uhr_ms=jetzt,
            messung=MESSUNG,
            audio_raus=audio_raus,
            bei_barge_in=barge_melden,
            bei_uebergabe=uebergabe_melden,
        )

        async def senden() -> None:
            frist = time.monotonic() * 1000.0
            while True:
                while not steuer_raus.empty():
                    await ws.send(json.dumps(steuer_raus.get_nowait()))
                if ton_raus:
                    kodiert = ah.audio_senden(ton_raus.popleft())
                    if kodiert:
                        await ws.send(kodiert)
                    jetzt_ms = time.monotonic() * 1000.0
                    frist = max(frist + FRAME_MS, jetzt_ms - FRAME_MS)
                    await asyncio.sleep(max(0.0, (frist - jetzt_ms) / 1000.0))
                else:
                    frist = time.monotonic() * 1000.0
                    await asyncio.sleep(FRAME_MS / 2000.0)

        async def verarbeiten() -> None:
            n = frame_bytes(TELEFON_RATE)
            while True:
                kunde = await audio_rein.get()
                for i in range(0, len(kunde) - n + 1, n):
                    uhr["t"] = max(time.monotonic() * 1000.0, uhr["t"] + FRAME_MS)
                    await loop.run_in_executor(arbeiter, pipe.frame, kunde[i : i + n], uhr["t"])

        aufgaben = [asyncio.create_task(senden()), asyncio.create_task(verarbeiten())]
        async for nachricht in ws:
            if isinstance(nachricht, str):
                for antwort in ah.text_empfangen(nachricht):
                    await ws.send(json.dumps(antwort))
                if ah.beendet:
                    break
                continue
            try:
                kanaele = ah.audio_empfangen(nachricht)
            except ValueError as e:
                for antwort in ah._client_fehler(str(e)):
                    await ws.send(json.dumps(antwort))
                break
            if kanaele is not None:
                kunde = ah.kanal(kanaele, "external")
                if kunde:
                    audio_rein.put_nowait(kunde)
        for hinweis in ah.protokoll:
            log.info("Sitzung %s: %s", sid, hinweis)
    finally:
        for a in aufgaben:
            a.cancel()
        arbeiter.shutdown(wait=False, cancel_futures=True)
        AKTIV["sitzungen"] -= 1


class _Metriken(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/healthz":
            body, ctype = b"ok\n", "text/plain"
        elif self.path == "/metrics":
            body = (
                MESSUNG.prometheus() + f"gw_contactcenter_aktive_sitzungen {AKTIV['sitzungen']}\n"
            ).encode()
            ctype = "text/plain; version=0.0.4"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def metriken_starten(host: str, port: int) -> HTTPServer:
    srv = HTTPServer((host, port), _Metriken)
    threading.Thread(target=srv.serve_forever, daemon=True, name="metriken").start()
    return srv


async def bedienen(k: ServerKonfig, bereit: asyncio.Event | None = None) -> None:
    """Startet den WebSocket-Server und läuft bis zum Abbruch."""
    from websockets.asyncio.server import serve

    nonces = NonceSpeicher()

    async def zugang(verbindung: ServerConnection, request: Request) -> Response | None:
        if k.max_sitzungen and AKTIV["sitzungen"] >= k.max_sitzungen:
            return verbindung.respond(503, "ausgelastet\n")
        grund = zugang_pruefen(k, header_dict(request.headers), request.path, nonces)
        if grund is None:
            return None
        log.warning("Verbindung abgelehnt: %s", grund)
        await asyncio.sleep(MIN_FEHLER_VERZOEGERUNG_S)
        return verbindung.respond(401, "unauthorized\n")

    async def handler(ws: Any) -> None:
        await sitzung(ws, k)

    async with serve(handler, k.host, k.port, process_request=zugang, max_size=2**20) as srv:
        k.port = srv.sockets[0].getsockname()[1] if srv.sockets else k.port
        log.info("AudioHook-Server auf ws://%s:%d (Modus %s)", k.host, k.port, k.modus)
        if bereit is not None:
            bereit.set()
        await asyncio.Future()


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    k = konfig_aus(argv)
    maengel = k.maengel()
    if maengel:
        raise KonfigurationsFehler(
            f"Start im Modus {k.modus!r} verweigert:\n  - " + "\n  - ".join(maengel)
        )
    if k.entwicklung:
        log.warning(
            "ENTWICKLUNGSMODUS: Mock-Adapter, unsigniertes Audit und fehlende "
            "Signaturen sind erlaubt. Nicht produktiv verwenden."
        )
    try:
        import websockets  # noqa: F401
    except ImportError as e:  # pragma: no cover
        raise KonfigurationsFehler('pip install "gradweight-contactcenter[server]"') from e
    metriken_starten(k.host, k.metrics_port)
    asyncio.run(bedienen(k))
    return 0  # pragma: no cover
