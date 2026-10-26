"""Echte Adapter für OpenAI-kompatible Server, nur mit der stdlib (urllib).

Eine Schnittstelle, viele Anbieter:

* LLM:  vLLM (``vllm serve``), Ollama, LiteLLM-Proxy, Azure OpenAI
        (Region Switzerland North), OpenAI, Mistral, Infomaniak AI Tools
* ASR:  speaches (faster-whisper), vLLM mit Whisper-Modell
* TTS:  speaches (Kokoro, Piper), OpenAI-TTS

Damit wechselt man Anbieter per Konfiguration, nicht per Code. Die
Region/Datenklasse regelt das Gateway, nicht der Adapter.
"""
from __future__ import annotations

import json
import time
import urllib.request
import uuid
from typing import Iterator, List, Optional

from ..audio import TELEFON_RATE, resample_linear, wav_bytes, wav_to_pcm


class HTTPFehler(RuntimeError):
    pass


def _anfrage(url: str, daten: bytes, headers: dict, timeout_s: float):
    req = urllib.request.Request(url, data=daten, headers=headers, method="POST")
    try:
        return urllib.request.urlopen(req, timeout=timeout_s)
    except Exception as e:  # urllib.error.URLError, HTTPError, socket.timeout
        raise HTTPFehler(f"{url}: {e}") from e


class OpenAIKompatibelLLM:
    def __init__(self, basis_url: str, modell: str, api_key: str = "",
                 timeout_s: float = 10.0, temperatur: float = 0.2, extra: Optional[dict] = None):
        """``extra`` wird in jeden Request gemischt, z. B. für Qwen3 bei vLLM
        ``{"chat_template_kwargs": {"enable_thinking": False}}``: Ein
        "denkendes" Modell schreibt sonst erst Hunderte Token Überlegungen,
        bevor der erste hörbare Satz kommt. Am Telefon tödlich."""
        self.basis = basis_url.rstrip("/")
        self.modell, self.api_key = modell, api_key
        self.timeout_s, self.temperatur = timeout_s, temperatur
        self.extra = extra or {}

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _body(self, messages, max_tokens, json_modus, stream) -> bytes:
        b = {"model": self.modell, "messages": messages, "max_tokens": max_tokens,
             "temperature": self.temperatur, "stream": stream}
        if json_modus:
            b["response_format"] = {"type": "json_object"}
        b.update(self.extra)
        return json.dumps(b).encode()

    def complete(self, messages: List[dict], max_tokens: int = 200,
                 json_modus: bool = False) -> dict:
        with _anfrage(f"{self.basis}/chat/completions",
                      self._body(messages, max_tokens, json_modus, False),
                      self._headers(), self.timeout_s) as r:
            d = json.loads(r.read())
        u = d.get("usage", {})
        return {"text": d["choices"][0]["message"].get("content") or "",
                "tokens_ein": u.get("prompt_tokens", 0),
                "tokens_aus": u.get("completion_tokens", 0)}

    def stream(self, messages: List[dict], max_tokens: int = 200) -> Iterator[str]:
        """Server-Sent Events: Zeilen 'data: {...}' bis 'data: [DONE]'."""
        with _anfrage(f"{self.basis}/chat/completions",
                      self._body(messages, max_tokens, False, True),
                      self._headers(), self.timeout_s) as r:
            for roh in r:
                zeile = roh.decode("utf-8").strip()
                if not zeile.startswith("data:"):
                    continue
                nutzlast = zeile[5:].strip()
                if nutzlast == "[DONE]":
                    break
                try:
                    delta = json.loads(nutzlast)["choices"][0].get("delta", {})
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta.get("content"):
                    yield delta["content"]


def _multipart(felder: dict, datei_feld: str, datei_name: str, datei: bytes,
               mime: str) -> tuple:
    grenze = uuid.uuid4().hex
    teile = []
    for k, v in felder.items():
        teile.append(f"--{grenze}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    teile.append((f"--{grenze}\r\nContent-Disposition: form-data; name=\"{datei_feld}\"; "
                  f"filename=\"{datei_name}\"\r\nContent-Type: {mime}\r\n\r\n").encode())
    teile.append(datei + b"\r\n")
    teile.append(f"--{grenze}--\r\n".encode())
    return b"".join(teile), f"multipart/form-data; boundary={grenze}"


class HTTPASR:
    """POST /audio/transcriptions (OpenAI-Format). Batch pro Äusserung."""

    def __init__(self, basis_url: str, modell: str = "Systran/faster-whisper-large-v3",
                 sprache: str = "de", api_key: str = "", timeout_s: float = 10.0):
        self.basis = basis_url.rstrip("/")
        self.modell, self.sprache = modell, sprache
        self.api_key, self.timeout_s = api_key, timeout_s

    def teiltext(self) -> str:
        return ""            # Batch-ASR hat keine Zwischenergebnisse

    def transkribieren(self, pcm16: bytes, rate: int) -> str:
        body, ctype = _multipart({"model": self.modell, "language": self.sprache,
                                  "response_format": "json"},
                                 "file", "turn.wav", wav_bytes(pcm16, rate), "audio/wav")
        h = {"Content-Type": ctype}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        with _anfrage(f"{self.basis}/audio/transcriptions", body, h, self.timeout_s) as r:
            return json.loads(r.read()).get("text", "").strip()


class HTTPTTS:
    """POST /audio/speech (OpenAI-Format) -> WAV -> PCM16 8 kHz."""

    def __init__(self, basis_url: str, modell: str, stimme: str = "",
                 api_key: str = "", timeout_s: float = 10.0):
        """``modell``/``stimme``: eine DEUTSCHE Stimme wählen, z. B. eine
        Piper-Stimme de_DE (lokal) oder Azure de-CH-LeniNeural (Cloud, CH).
        Kokoro hat (Stand 10/2026) keine deutsche Stimme."""
        if not modell:
            raise ValueError("TTS-Modell angeben (deutsche Stimme)")
        self.basis = basis_url.rstrip("/")
        self.modell, self.stimme = modell, stimme
        self.api_key, self.timeout_s = api_key, timeout_s

    def synthetisieren(self, text: str) -> bytes:
        req = {"model": self.modell, "input": text, "response_format": "wav"}
        if self.stimme:
            req["voice"] = self.stimme
        body = json.dumps(req).encode()
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        with _anfrage(f"{self.basis}/audio/speech", body, h, self.timeout_s) as r:
            pcm, rate = wav_to_pcm(r.read())
        return resample_linear(pcm, rate, TELEFON_RATE)
