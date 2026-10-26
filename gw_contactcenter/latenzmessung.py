"""Latenzmessung gegen echte Endpunkte (LLM, ASR, TTS).

Was gemessen wird (Client-Sicht, ``time.perf_counter``):

* **LLM** (``/chat/completions`` mit Streaming): Zeit bis zum ersten
  Inhalts-Token (TTFT), Zeit bis zum ersten vollständigen Satz (so wie das
  Gateway Sätze schneidet), Gesamtdauer und, falls der Server ``usage``
  liefert, die Dekodierrate.
* **ASR** (``/audio/transcriptions``): Dauer für eine Äusserung (WAV-Datei).
* **TTS** (``/audio/speech``): Zeit bis zum ersten Byte und Gesamtdauer für
  einen typischen ersten Satz.
* **Kette** (wenn alle drei gesetzt sind): ASR -> LLM bis erster Satz ->
  TTS bis erstes Byte, nacheinander im selben Durchlauf. Das ist die
  Verarbeitungszeit nach dem Turn-Ende; Endpointing, Netz zu Genesys und
  Wiedergabepuffer kommen dazu.

Methode: ``aufwaermen`` Durchläufe werden verworfen, dann ``wiederholungen``
Durchläufe je Worker; mit ``parallel`` > 1 laufen mehrere Worker gleichzeitig
(Messung unter Last). Ausgabe: n, Minimum, p50, p90, p95, p99, Maximum je
Kennzahl, dazu Datum (UTC), Umgebung des Clients, Endpunkte (ohne
Zugangsdaten) und die Hardware-Beschreibung, die beim Aufruf angegeben wird
(der Client kennt die Server-GPU nicht).

Dieses Werkzeug erzeugt die einzigen Latenzzahlen, die als Messung gelten
dürfen. Ergebnisse nur mit dem vollständigen JSON-Bericht weitergeben.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .adapters.openai_compat import _header, _json_lesen, _multipart, http_post
from .audio import ton, wav_bytes
from .gateway import satzende
from .telemetry import perzentil

__all__ = ["Endpunkte", "Messbericht", "main", "messen"]

STANDARD_PROMPT = [
    {
        "role": "system",
        "content": "Du bist der telefonische Kundendienst-Assistent eines Schweizer KMU. "
        "Antworte auf Hochdeutsch in höchstens zwei kurzen Sätzen.",
    },
    {
        "role": "system",
        "content": 'KONTEXT:{"typ": "bestellstatus", "bestellnummer": "447100", '
        '"daten": {"gefunden": true, "status": "unterwegs", "zustellung": "morgen bis 12 Uhr"}}',
    },
    {"role": "user", "content": "<kundschaft>Wo ist meine Bestellung 447100?</kundschaft>"},
]
STANDARD_SATZ = "Ihre Bestellung ist unterwegs und wird morgen bis 12 Uhr zugestellt."


@dataclass
class Endpunkte:
    llm_url: str = ""
    llm_modell: str = ""
    llm_api_key: str = ""
    llm_extra: dict[str, Any] = field(default_factory=dict)
    asr_url: str = ""
    asr_modell: str = ""
    tts_url: str = ""
    tts_modell: str = ""
    tts_stimme: str = ""
    tts_format: str = "wav"
    tts_abtastrate: int = 0
    timeout_s: float = 30.0


def _ohne_zugangsdaten(url: str) -> str:
    teile = urllib.parse.urlsplit(url)
    netloc = teile.hostname or ""
    if teile.port:
        netloc += f":{teile.port}"
    return urllib.parse.urlunsplit((teile.scheme, netloc, teile.path, "", ""))


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


def llm_messen(e: Endpunkte, prompt: list[dict[str, str]]) -> dict[str, float]:
    body: dict[str, Any] = {
        "model": e.llm_modell,
        "messages": prompt,
        "max_tokens": 120,
        "temperature": 0.0,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    body.update(e.llm_extra)
    t0 = time.perf_counter()
    ttft = erster_satz = None
    text, tokens_aus = "", 0
    with http_post(
        f"{e.llm_url.rstrip('/')}/chat/completions",
        json.dumps(body).encode(),
        _header(e.llm_api_key),
        timeout_s=e.timeout_s,
        wiederholungen=0,
    ) as r:
        for roh in r:
            zeile = roh.decode("utf-8").strip()
            if not zeile.startswith("data:") or zeile[5:].strip() == "[DONE]":
                continue
            d = json.loads(zeile[5:].strip())
            usage = d.get("usage") or {}
            if isinstance(usage, dict) and usage.get("completion_tokens"):
                tokens_aus = int(usage["completion_tokens"])
            for wahl in d.get("choices") or []:
                inhalt = (wahl.get("delta") or {}).get("content") or ""
                if inhalt and ttft is None:
                    ttft = _ms(t0)
                text += inhalt
                if erster_satz is None and satzende(text + " ") > 0:
                    erster_satz = _ms(t0)
    gesamt = _ms(t0)
    if ttft is None:
        raise RuntimeError("LLM hat keinen Inhalt gestreamt")
    out = {
        "llm_ttft_ms": ttft,
        "llm_erster_satz_ms": erster_satz or gesamt,
        "llm_gesamt_ms": gesamt,
    }
    if tokens_aus > 1 and gesamt > ttft:
        out["llm_tokens_pro_s"] = (tokens_aus - 1) / ((gesamt - ttft) / 1000.0)
    return out


def asr_messen(e: Endpunkte, wav: bytes) -> dict[str, float]:
    body, ctype = _multipart(
        {"model": e.asr_modell, "language": "de", "response_format": "json"},
        "file",
        "messung.wav",
        wav,
        "audio/wav",
    )
    t0 = time.perf_counter()
    with http_post(
        f"{e.asr_url.rstrip('/')}/audio/transcriptions",
        body,
        _header("", ctype),
        timeout_s=e.timeout_s,
        wiederholungen=0,
    ) as r:
        _json_lesen(r)
    return {"asr_ms": _ms(t0)}


def tts_messen(e: Endpunkte, satz: str) -> dict[str, float]:
    req: dict[str, Any] = {
        "model": e.tts_modell,
        "input": satz,
        "voice": e.tts_stimme,
        "response_format": e.tts_format,
    }
    if e.tts_abtastrate:
        req["sample_rate"] = e.tts_abtastrate
    t0 = time.perf_counter()
    with http_post(
        f"{e.tts_url.rstrip('/')}/audio/speech",
        json.dumps(req).encode(),
        _header(""),
        timeout_s=e.timeout_s,
        wiederholungen=0,
    ) as r:
        erstes = r.read(1)
        ttfb = _ms(t0)
        r.read()
    if not erstes:
        raise RuntimeError("TTS hat kein Audio geliefert")
    return {"tts_erstes_byte_ms": ttfb, "tts_gesamt_ms": _ms(t0)}


def kette_messen(e: Endpunkte, wav: bytes, prompt: list[dict[str, str]]) -> dict[str, float]:
    """Summe der im selben Durchlauf gemessenen Stufen, so wie die Pipeline sie
    verkettet: ASR komplett, LLM bis zum ersten Satz, TTS bis zum ersten Byte."""
    asr = asr_messen(e, wav)
    llm = llm_messen(e, prompt)
    tts = tts_messen(e, STANDARD_SATZ)
    return {
        "kette_bis_erstes_audio_ms": asr["asr_ms"]
        + llm["llm_erster_satz_ms"]
        + tts["tts_erstes_byte_ms"]
    }


@dataclass
class Messbericht:
    zeitpunkt_utc: str
    methode: dict[str, Any]
    umgebung: dict[str, Any]
    kennzahlen: dict[str, dict[str, float]]
    fehler: list[str]

    def als_dict(self) -> dict[str, Any]:
        return {
            "zeitpunkt_utc": self.zeitpunkt_utc,
            "methode": self.methode,
            "umgebung": self.umgebung,
            "kennzahlen": self.kennzahlen,
            "fehler": self.fehler,
        }

    def als_text(self) -> str:
        z = [
            f"Latenzmessung {self.zeitpunkt_utc} (Messung, Client-Sicht)",
            f"  Hardware (Angabe): {self.umgebung.get('hardware') or 'nicht angegeben'}",
            f"  Wiederholungen {self.methode['wiederholungen']} x {self.methode['parallel']} "
            f"Worker, Aufwärmen {self.methode['aufwaermen']}",
            "  Kennzahl                    n     min     p50     p90     p95     p99     max",
        ]
        for name, s in self.kennzahlen.items():
            z.append(
                f"  {name:26} {s['n']:>3.0f} {s['min']:>7.1f} {s['p50']:>7.1f} {s['p90']:>7.1f} "
                f"{s['p95']:>7.1f} {s['p99']:>7.1f} {s['max']:>7.1f}"
            )
        if self.fehler:
            z.append(f"  Fehler: {len(self.fehler)} (siehe JSON-Bericht)")
        return "\n".join(z)


def _zusammenfassen(werte: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    out = {}
    for name, w in sorted(werte.items()):
        if w:
            out[name] = {
                "n": float(len(w)),
                "min": min(w),
                "max": max(w),
                **{f"p{p}": perzentil(w, p) for p in (50, 90, 95, 99)},
            }
    return out


def messen(
    e: Endpunkte,
    *,
    wiederholungen: int = 30,
    aufwaermen: int = 3,
    parallel: int = 1,
    wav: bytes | None = None,
    prompt: list[dict[str, str]] | None = None,
    hardware: str = "",
) -> Messbericht:
    if wiederholungen < 1 or aufwaermen < 0 or parallel < 1:
        raise ValueError("wiederholungen ≥ 1, aufwaermen ≥ 0, parallel ≥ 1")
    if not (e.llm_url or e.asr_url or e.tts_url):
        raise ValueError("mindestens einen Endpunkt angeben (LLM, ASR oder TTS)")
    if e.tts_url and not (e.tts_modell and e.tts_stimme):
        raise ValueError("TTS braucht Modell und Stimme")
    wav = wav or wav_bytes(ton(200, 1500, 16000, 0.3), 16000)
    prompt = prompt or STANDARD_PROMPT
    schritte: list[Callable[[], dict[str, float]]] = []
    if e.llm_url:
        schritte.append(lambda: llm_messen(e, prompt))
    if e.asr_url:
        schritte.append(lambda: asr_messen(e, wav))
    if e.tts_url:
        schritte.append(lambda: tts_messen(e, STANDARD_SATZ))
    if e.llm_url and e.asr_url and e.tts_url:
        schritte.append(lambda: kette_messen(e, wav, prompt))

    werte: dict[str, list[float]] = {}
    fehler: list[str] = []
    sperre = threading.Lock()

    def worker() -> None:
        for i in range(aufwaermen + wiederholungen):
            for schritt in schritte:
                try:
                    ergebnis = schritt()
                except Exception as ex:  # noqa: BLE001 - jeder Fehler wird gezählt, nicht verschluckt
                    with sperre:
                        fehler.append(f"{type(ex).__name__}: {ex}"[:200])
                    continue
                if i >= aufwaermen:
                    with sperre:
                        for k, v in ergebnis.items():
                            werte.setdefault(k, []).append(v)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(parallel)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return Messbericht(
        zeitpunkt_utc=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        methode={
            "wiederholungen": wiederholungen,
            "aufwaermen": aufwaermen,
            "parallel": parallel,
            "uhr": "time.perf_counter (Client)",
            "endpunkte": {
                "llm": _ohne_zugangsdaten(e.llm_url) if e.llm_url else None,
                "llm_modell": e.llm_modell or None,
                "asr": _ohne_zugangsdaten(e.asr_url) if e.asr_url else None,
                "asr_modell": e.asr_modell or None,
                "tts": _ohne_zugangsdaten(e.tts_url) if e.tts_url else None,
                "tts_modell": e.tts_modell or None,
                "tts_format": e.tts_format if e.tts_url else None,
            },
        },
        umgebung={
            "hardware": hardware,
            "client_python": sys.version.split()[0],
            "client_plattform": platform.platform(),
            "client_cpus": os.cpu_count(),
        },
        kennzahlen=_zusammenfassen(werte),
        fehler=fehler,
    )


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="gw-contactcenter latenz-messen",
        description="Latenz echter LLM-, ASR- und TTS-Endpunkte messen",
    )
    p.add_argument("--llm-url", default="")
    p.add_argument("--llm-modell", default="")
    p.add_argument("--llm-extra-json", default="", help='z. B. {"chat_template_kwargs": {...}}')
    p.add_argument("--asr-url", default="")
    p.add_argument("--asr-modell", default="Systran/faster-whisper-large-v3")
    p.add_argument("--wav", default="", help="16-Bit-WAV einer typischen Äusserung")
    p.add_argument("--tts-url", default="")
    p.add_argument("--tts-modell", default="")
    p.add_argument("--tts-stimme", default="")
    p.add_argument("--tts-format", choices=["wav", "pcm"], default="wav")
    p.add_argument("--tts-abtastrate", type=int, default=0)
    p.add_argument("--wiederholungen", type=int, default=30)
    p.add_argument("--aufwaermen", type=int, default=3)
    p.add_argument("--parallel", type=int, default=1)
    p.add_argument("--hardware", default="", help="z. B. '1x L4 24 GB, vLLM 0.32.0, Qwen3-8B-AWQ'")
    p.add_argument("--json", default="", help="Bericht als JSON speichern")
    a = p.parse_args(list(argv) if argv is not None else None)
    extra = json.loads(a.llm_extra_json) if a.llm_extra_json else {}
    e = Endpunkte(
        llm_url=a.llm_url,
        llm_modell=a.llm_modell,
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        llm_extra=extra,
        asr_url=a.asr_url,
        asr_modell=a.asr_modell,
        tts_url=a.tts_url,
        tts_modell=a.tts_modell,
        tts_stimme=a.tts_stimme,
        tts_format=a.tts_format,
        tts_abtastrate=a.tts_abtastrate,
    )
    wav = None
    if a.wav:
        with open(a.wav, "rb") as f:
            wav = f.read()
    bericht = messen(
        e,
        wiederholungen=a.wiederholungen,
        aufwaermen=a.aufwaermen,
        parallel=a.parallel,
        wav=wav,
        hardware=a.hardware,
    )
    print(bericht.als_text())
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(bericht.als_dict(), f, ensure_ascii=False, indent=2)
    return 1 if bericht.fehler and not bericht.kennzahlen else 0
