"""Audio-Grundlagen für Telefonie (nur Standardbibliothek).

Telefonie spricht fast immer G.711: 8000 Samples/s, 8 Bit pro Sample,
logarithmisch kodiert (mu-law, bei Genesys «PCMU»). Ein Kanal = 64 kbit/s.

Modelle wollen dagegen lineares PCM (16 Bit), ASR meist mit 16 kHz.
Dieses Modul übersetzt dazwischen. ``audioop`` wurde mit Python 3.13 aus
der Standardbibliothek entfernt, deshalb ist der Codec hier nach ITU-T
G.711 selbst implementiert (Tests vergleichen mit ``audioop``, wo vorhanden).

Für hohen Durchsatz: ``soxr`` (Resampling) und numpy; die Schnittstellen
bleiben gleich.
"""

from __future__ import annotations

import io
import math
import wave
from array import array
from collections.abc import Iterator

TELEFON_RATE = 8000  # Hz, G.711
ASR_RATE = 16000  # Hz, Whisper und ähnliche
FRAME_MS = 20  # übliche Paketgrösse in RTP und AudioHook

_BIAS = 0x84
_CLIP = 32635


def _lin2ulaw(sample: int) -> int:
    """Ein lineares 16-Bit-Sample -> ein mu-law-Byte (G.711)."""
    sign = 0x80 if sample < 0 else 0x00
    if sample < 0:
        sample = -sample
    sample = min(sample, _CLIP) + _BIAS
    exponent = min(7, max(0, (sample >> 7).bit_length() - 1))
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF


def _ulaw2lin(byte: int) -> int:
    """Ein mu-law-Byte -> lineares 16-Bit-Sample."""
    u = (~byte) & 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    sample = (((mantissa << 3) + _BIAS) << exponent) - _BIAS
    return -sample if sign else sample


# Lookup-Tabellen: Kodieren und Dekodieren sind je ein Tabellenzugriff.
_ULAW_DECODE = array("h", (_ulaw2lin(b) for b in range(256)))
_ULAW_ENCODE = bytes(_lin2ulaw(s) for s in range(-32768, 32768))


def ulaw_to_pcm16(data: bytes) -> bytes:
    """mu-law-Bytes -> PCM16 little-endian."""
    return array("h", (_ULAW_DECODE[b] for b in data)).tobytes()


def _samples(pcm: bytes) -> array[int]:
    s = array("h")
    s.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    return s


def pcm16_to_ulaw(pcm: bytes) -> bytes:
    """PCM16 little-endian -> mu-law-Bytes."""
    return bytes(_ULAW_ENCODE[s + 32768] for s in _samples(pcm))


def deinterleave(data: bytes, kanaele: int) -> list[bytes]:
    """Verschränkte 8-Bit-Kanäle (L R L R ...) -> ein Bytes-Objekt pro Kanal.

    Bei AudioHook folgt die Reihenfolge der Kanäle der Liste ``channels`` im
    gewählten Medienformat (z. B. ``["external", "internal"]``).
    """
    if kanaele < 1:
        raise ValueError("kanaele muss ≥ 1 sein")
    if len(data) % kanaele:
        raise ValueError(f"{len(data)} Byte sind nicht durch {kanaele} Kanäle teilbar")
    return [data[i::kanaele] for i in range(kanaele)]


def resample_linear(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Lineare Interpolation. Reicht für 8 <-> 16 kHz Sprache.

    Beim Heruntertasten fehlt ein Tiefpass; für Produktion soxr verwenden.
    """
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("Abtastraten müssen > 0 sein")
    if src_rate == dst_rate or not pcm:
        return pcm
    src = _samples(pcm)
    if not src:
        return b""
    n_out = int(len(src) * dst_rate / src_rate)
    out = array("h", [0]) * n_out
    ratio = src_rate / dst_rate
    last = len(src) - 1
    for i in range(n_out):
        pos = i * ratio
        j = int(pos)
        frac = pos - j
        a = src[j] if j <= last else src[last]
        b = src[j + 1] if j + 1 <= last else a
        out[i] = int(a + (b - a) * frac)
    return out.tobytes()


def rms(pcm: bytes) -> float:
    s = _samples(pcm)
    if not s:
        return 0.0
    return math.sqrt(math.fsum(x * x for x in s) / len(s))


def dbfs(pcm: bytes) -> float:
    """Pegel relativ zum Vollausschlag (0 dBFS = Maximum, digitale Stille -96)."""
    r = rms(pcm)
    return 20 * math.log10(r / 32768.0) if r > 0 else -96.0


def frame_bytes(rate: int, ms: int = FRAME_MS, sample_width: int = 2) -> int:
    return rate * ms // 1000 * sample_width


def frames(pcm: bytes, rate: int, ms: int = FRAME_MS) -> Iterator[bytes]:
    """Zerlegt PCM16 in Frames fester Länge (ein unvollständiger Rest wird verworfen)."""
    n = frame_bytes(rate, ms)
    for i in range(0, len(pcm) - n + 1, n):
        yield pcm[i : i + n]


def ton(freq_hz: float, ms: int, rate: int = TELEFON_RATE, amp: float = 0.3) -> bytes:
    """Sinuston als PCM16 (Testsignal, ersetzt «Sprache» im Simulator)."""
    n = rate * ms // 1000
    a = int(32767 * amp)
    return array(
        "h", (int(a * math.sin(2 * math.pi * freq_hz * i / rate)) for i in range(n))
    ).tobytes()


def stille(ms: int, rate: int = TELEFON_RATE, rauschen: int = 0) -> bytes:
    """Stille, optional mit deterministischem leisem Rauschen (reproduzierbar)."""
    n = rate * ms // 1000
    if not rauschen:
        return bytes(2 * n)
    x, out = 12345, array("h")
    for _ in range(n):
        x = (1103515245 * x + 12345) & 0x7FFFFFFF  # LCG, kein random-Modul
        out.append((x % (2 * rauschen + 1)) - rauschen)
    return out.tobytes()


def wav_bytes(pcm: bytes, rate: int) -> bytes:
    """PCM16 mono -> WAV-Container (für HTTP-ASR-Endpunkte)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_to_pcm(data: bytes) -> tuple[bytes, int]:
    """WAV-Container -> (PCM16 mono, Rate). Stereo wird auf den linken Kanal reduziert."""
    with wave.open(io.BytesIO(data), "rb") as w:
        rate, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise ValueError(f"nur 16-Bit-WAV unterstützt, nicht {8 * sw} Bit")
    if ch == 2:
        raw = _samples(raw)[0::2].tobytes()
    elif ch != 1:
        raise ValueError(f"nur Mono oder Stereo unterstützt, nicht {ch} Kanäle")
    return raw, rate
