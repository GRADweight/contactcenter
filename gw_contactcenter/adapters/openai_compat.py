"""Adapter für OpenAI-kompatible HTTP-APIs, nur mit der Standardbibliothek (urllib).

Eine Schnittstelle, viele Anbieter:

* LLM: vLLM (``vllm serve``), Ollama, LiteLLM, OpenAI und andere, die
  ``/v1/chat/completions`` anbieten (Streaming über Server-Sent Events)
* ASR: ``/v1/audio/transcriptions`` (z. B. speaches mit faster-whisper)
* TTS: ``/v1/audio/speech`` (z. B. speaches mit Piper)

Fehlerbehandlung: Zeitlimit pro Request; bei Verbindungsfehlern, HTTP 429
und 5xx höchstens ``wiederholungen`` weitere Versuche mit exponentiellem
Backoff (am Telefon zählt jede Sekunde, deshalb standardmässig nur einer).
HTTP 4xx (ausser 429) wird nicht wiederholt. Antworten werden streng
geprüft; Abweichungen ergeben :class:`HTTPFehler` bzw. :class:`AntwortFehler`.

Welche Region und Datenklasse ein Backend bedienen darf, regelt das Gateway,
nicht der Adapter.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
import wave
from collections.abc import Callable, Iterator
from http.client import HTTPResponse
from typing import Any, Literal

from ..audio import TELEFON_RATE, resample_linear, wav_bytes, wav_to_pcm
from ..gateway import LLMErgebnis, Nachricht

__all__ = ["HTTPASR", "HTTPTTS", "AntwortFehler", "HTTPFehler", "OpenAIKompatibelLLM"]


class HTTPFehler(RuntimeError):
    """Netzwerk- oder HTTP-Fehler nach allen Wiederholungen."""


class AntwortFehler(RuntimeError):
    """Der Server hat geantwortet, aber nicht im erwarteten Format."""


def _wiederholbar(e: Exception) -> bool:
    if isinstance(e, urllib.error.HTTPError):
        return e.code == 429 or e.code >= 500
    return isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError))


def http_post(
    url: str,
    daten: bytes,
    headers: dict[str, str],
    *,
    timeout_s: float,
    wiederholungen: int = 1,
    backoff_s: float = 0.2,
    schlafen: Callable[[float], None] = time.sleep,
) -> HTTPResponse:
    """POST mit Zeitlimit und begrenzten Wiederholungen; gibt die offene Antwort zurück."""
    req = urllib.request.Request(url, data=daten, headers=headers, method="POST")  # noqa: S310
    for versuch in range(wiederholungen + 1):
        try:
            antwort: HTTPResponse = urllib.request.urlopen(req, timeout=timeout_s)  # noqa: S310
            return antwort
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if versuch < wiederholungen and _wiederholbar(e):
                schlafen(backoff_s * 2**versuch)
                continue
            raise HTTPFehler(f"{url}: {e}") from e
    raise AssertionError("unerreichbar")  # pragma: no cover


def _header(api_key: str, content_type: str = "application/json") -> dict[str, str]:
    h = {"Content-Type": content_type}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def _json_lesen(antwort: HTTPResponse) -> Any:
    try:
        return json.loads(antwort.read())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise AntwortFehler("Antwort ist kein JSON") from e


class OpenAIKompatibelLLM:
    def __init__(
        self,
        basis_url: str,
        modell: str,
        api_key: str = "",
        *,
        timeout_s: float = 10.0,
        temperatur: float = 0.2,
        extra: dict[str, Any] | None = None,
        wiederholungen: int = 1,
    ) -> None:
        """``extra`` wird in jeden Request gemischt, z. B. für Qwen3 bei vLLM
        ``{"chat_template_kwargs": {"enable_thinking": false}}``: Sonst schreibt
        ein «denkendes» Modell zuerst lange Überlegungen, bevor der erste
        hörbare Satz kommt."""
        self.basis = basis_url.rstrip("/")
        self.modell, self.api_key = modell, api_key
        self.timeout_s, self.temperatur = timeout_s, temperatur
        self.extra = extra or {}
        self.wiederholungen = wiederholungen

    def _body(
        self, messages: list[Nachricht], max_tokens: int, json_modus: bool, stream: bool
    ) -> bytes:
        b: dict[str, Any] = {
            "model": self.modell,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self.temperatur,
            "stream": stream,
        }
        if json_modus:
            b["response_format"] = {"type": "json_object"}
        b.update(self.extra)
        return json.dumps(b).encode()

    def complete(
        self, messages: list[Nachricht], max_tokens: int = 200, json_modus: bool = False
    ) -> LLMErgebnis:
        with http_post(
            f"{self.basis}/chat/completions",
            self._body(messages, max_tokens, json_modus, False),
            _header(self.api_key),
            timeout_s=self.timeout_s,
            wiederholungen=self.wiederholungen,
        ) as r:
            d = _json_lesen(r)
        try:
            inhalt = d["choices"][0]["message"].get("content") or ""
            usage = d.get("usage") or {}
            t_ein, t_aus = (
                int(usage.get("prompt_tokens", 0)),
                int(usage.get("completion_tokens", 0)),
            )
        except (KeyError, IndexError, TypeError, AttributeError, ValueError) as e:
            raise AntwortFehler("unerwartetes Format von /chat/completions") from e
        if not isinstance(inhalt, str):
            raise AntwortFehler("content ist kein Text")
        return {"text": inhalt, "tokens_ein": t_ein, "tokens_aus": t_aus}

    def stream(self, messages: list[Nachricht], max_tokens: int = 200) -> Iterator[str]:
        """Server-Sent Events: Zeilen ``data: {...}`` bis ``data: [DONE]``."""
        with http_post(
            f"{self.basis}/chat/completions",
            self._body(messages, max_tokens, False, True),
            _header(self.api_key),
            timeout_s=self.timeout_s,
            wiederholungen=self.wiederholungen,
        ) as r:
            for roh in r:
                zeile = roh.decode("utf-8").strip()
                if not zeile.startswith("data:"):
                    continue
                nutzlast = zeile[5:].strip()
                if nutzlast == "[DONE]":
                    return
                try:
                    d = json.loads(nutzlast)
                except json.JSONDecodeError as e:
                    raise AntwortFehler("Stream-Zeile ist kein JSON") from e
                wahlen = d.get("choices") if isinstance(d, dict) else None
                if not isinstance(wahlen, list):
                    raise AntwortFehler("Stream-Zeile ohne 'choices'")
                # Leere 'choices' sind erlaubt (z. B. letzte Zeile mit 'usage').
                for wahl in wahlen:
                    delta = wahl.get("delta") if isinstance(wahl, dict) else None
                    inhalt = delta.get("content") if isinstance(delta, dict) else None
                    if isinstance(inhalt, str) and inhalt:
                        yield inhalt


def _multipart(
    felder: dict[str, str], datei_feld: str, datei_name: str, datei: bytes, mime: str
) -> tuple[bytes, str]:
    grenze = uuid.uuid4().hex
    teile = [
        f'--{grenze}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, v in felder.items()
    ]
    teile.append(
        (
            f'--{grenze}\r\nContent-Disposition: form-data; name="{datei_feld}"; '
            f'filename="{datei_name}"\r\nContent-Type: {mime}\r\n\r\n'
        ).encode()
    )
    teile.append(datei + b"\r\n")
    teile.append(f"--{grenze}--\r\n".encode())
    return b"".join(teile), f"multipart/form-data; boundary={grenze}"


class HTTPASR:
    """POST /audio/transcriptions (OpenAI-Format), eine Äusserung pro Request."""

    def __init__(
        self,
        basis_url: str,
        modell: str = "Systran/faster-whisper-large-v3",
        *,
        sprache: str = "de",
        api_key: str = "",
        timeout_s: float = 10.0,
        wiederholungen: int = 1,
    ) -> None:
        self.basis = basis_url.rstrip("/")
        self.modell, self.sprache = modell, sprache
        self.api_key, self.timeout_s, self.wiederholungen = api_key, timeout_s, wiederholungen

    def teiltext(self) -> str:
        return ""  # Batch-ASR hat keine Zwischenergebnisse

    def transkribieren(self, pcm16: bytes, rate: int) -> str:
        body, ctype = _multipart(
            {"model": self.modell, "language": self.sprache, "response_format": "json"},
            "file",
            "turn.wav",
            wav_bytes(pcm16, rate),
            "audio/wav",
        )
        with http_post(
            f"{self.basis}/audio/transcriptions",
            body,
            _header(self.api_key, ctype),
            timeout_s=self.timeout_s,
            wiederholungen=self.wiederholungen,
        ) as r:
            d = _json_lesen(r)
        text = d.get("text") if isinstance(d, dict) else None
        if not isinstance(text, str):
            raise AntwortFehler("Transkription ohne Feld 'text'")
        return text.strip()


class HTTPTTS:
    """POST /audio/speech (OpenAI-Format) -> PCM16 8 kHz.

    ``antwortformat="wav"`` ist das allgemeine OpenAI-Format. Für speaches
    mit Piper ist ``antwortformat="pcm"`` mit ``abtastrate=8000`` empfohlen:
    Dann liefert der Server rohes PCM16 in Telefonrate, und ein Fehler in
    speaches 0.8.3 (WAV-Kopf mit falscher Abtastrate bei Piper-Stimmen ohne
    ``sample_rate``, geprüft im Quellcode ``routers/speech.py``) spielt keine
    Rolle. ``abtastrate`` ist eine speaches-Erweiterung; OpenAI selbst liefert
    PCM immer mit 24 kHz.
    """

    def __init__(
        self,
        basis_url: str,
        modell: str,
        stimme: str,
        *,
        antwortformat: Literal["wav", "pcm"] = "wav",
        abtastrate: int | None = None,
        api_key: str = "",
        timeout_s: float = 10.0,
        wiederholungen: int = 1,
    ) -> None:
        if not modell:
            raise ValueError("TTS-Modell angeben (deutsche Stimme)")
        if not stimme:
            raise ValueError("TTS-Stimme angeben (das Feld 'voice' ist bei /audio/speech Pflicht)")
        if antwortformat not in ("wav", "pcm"):
            raise ValueError("antwortformat muss 'wav' oder 'pcm' sein")
        self.basis = basis_url.rstrip("/")
        self.modell, self.stimme = modell, stimme
        self.antwortformat, self.abtastrate = antwortformat, abtastrate
        self.api_key, self.timeout_s, self.wiederholungen = api_key, timeout_s, wiederholungen

    def anfrage_body(self, text: str) -> bytes:
        req: dict[str, Any] = {
            "model": self.modell,
            "input": text,
            "voice": self.stimme,
            "response_format": self.antwortformat,
        }
        if self.abtastrate:
            req["sample_rate"] = self.abtastrate
        return json.dumps(req).encode()

    def synthetisieren(self, text: str) -> bytes:
        with http_post(
            f"{self.basis}/audio/speech",
            self.anfrage_body(text),
            _header(self.api_key),
            timeout_s=self.timeout_s,
            wiederholungen=self.wiederholungen,
        ) as r:
            roh = r.read()
        if self.antwortformat == "pcm":
            rate = self.abtastrate or 24000
            pcm = roh[: len(roh) - len(roh) % 2]
        else:
            try:
                pcm, rate = wav_to_pcm(roh)
            except (ValueError, EOFError, wave.Error) as e:
                raise AntwortFehler("TTS-Antwort ist kein gültiges 16-Bit-WAV") from e
        return resample_linear(pcm, rate, TELEFON_RATE)
