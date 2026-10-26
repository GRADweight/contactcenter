"""Audio-Grundlagen für Telefonie (nur stdlib).

Telefonie spricht fast immer G.711: 8000 Samples/s, 8 bit pro Sample,
logarithmisch kodiert (mu-law in Nordamerika und bei Genesys "PCMU",
A-law in Europa im klassischen ISDN). Ein Kanal = 64 kbit/s.

Modelle wollen dagegen lineares PCM (16 bit), ASR meist mit 16 kHz.
Dieses Modul übersetzt dazwischen. ``audioop`` wurde in Python 3.13
entfernt, deshalb ist der Codec hier selbst implementiert (ITU-T G.711).

Für Produktion mit hohem Durchsatz: ``soxr`` (Resampling) und numpy;
die Schnittstellen bleiben gleich.
"""
from __future__ import annotations

import math
from array import array

TELEFON_RATE = 8000          # Hz, G.711
ASR_RATE = 16000             # Hz, Whisper & Co.
FRAME_MS = 20                # übliche Paketgrösse in RTP / AudioHook

_BIAS = 0x84
_CLIP = 32635


def _lin2ulaw(sample: int) -> int:
    """Ein lineares 16-bit-Sample -> ein mu-law-Byte (G.711)."""
    sign = 0x80 if sample < 0 else 0x00
    if sample < 0:
        sample = -sample
    if sample > _CLIP:
        sample = _CLIP
    sample += _BIAS
    exponent = max(0, (sample >> 7).bit_length() - 1)
    if exponent > 7:
        exponent = 7
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF


def _ulaw2lin(byte: int) -> int:
    """Ein mu-law-Byte -> lineares 16-bit-Sample."""
    u = (~byte) & 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    sample = (((mantissa << 3) + _BIAS) << exponent) - _BIAS
    return -sample if sign else sample


# Lookup-Tabellen: Dekodieren ist ein Tabellenzugriff pro Byte.
_ULAW_DECODE = [_ulaw2lin(b) for b in range(256)]


def ulaw_to_pcm16(data: bytes) -> bytes:
    """mu-law-Bytes -> PCM16 little-endian."""
    out = array("h", (_ULAW_DECODE[b] for b in data))
    return out.tobytes()


def pcm16_to_ulaw(pcm: bytes) -> bytes:
    """PCM16 little-endian -> mu-law-Bytes."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    return bytes(_lin2ulaw(s) for s in samples)


def deinterleave_stereo(data: bytes) -> tuple:
    """Stereo-mu-law (L R L R ...) -> (links, rechts).

    Bei Genesys AudioHook ist bei zwei Kanälen links = "external"
    (Kundschaft) und rechts = "internal" (Agent/IVR). Getrennte Kanäle
    sind Gold wert: Diarisierung wird trivial, und die eigene Ansage
    landet nie im Ohr der Spracherkennung (kein Echo-Problem).
    """
    return data[0::2], data[1::2]


def resample_linear(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Lineare Interpolation. Reicht für 8 <-> 16 kHz Sprache.

    Hinweis: Beim Heruntertasten fehlt hier ein Tiefpass; für
    Produktion soxr verwenden. Für Telefonie-Sprache (Band 300-3400 Hz)
    ist der Fehler klein.
    """
    if src_rate == dst_rate or not pcm:
        return pcm
    src = array("h")
    src.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
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
    s = array("h")
    s.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not s:
        return 0.0
    return math.sqrt(sum(x * x for x in s) / len(s))


def dbfs(pcm: bytes) -> float:
    """Pegel relativ zum Vollausschlag (0 dBFS = Maximum, Stille ~ -96)."""
    r = rms(pcm)
    return 20 * math.log10(r / 32768.0) if r > 0 else -96.0


def frame_bytes(rate: int, ms: int = FRAME_MS, sample_width: int = 2) -> int:
    return rate * ms // 1000 * sample_width


def frames(pcm: bytes, rate: int, ms: int = FRAME_MS):
    """Zerlegt PCM16 in Frames fester Länge (letzter Rest wird verworfen)."""
    n = frame_bytes(rate, ms)
    for i in range(0, len(pcm) - n + 1, n):
        yield pcm[i: i + n]


def ton(freq_hz: float, ms: int, rate: int = TELEFON_RATE, amp: float = 0.3) -> bytes:
    """Sinuston als PCM16 (Testsignal, ersetzt "Sprache" im Simulator)."""
    n = rate * ms // 1000
    a = int(32767 * amp)
    return array("h", (int(a * math.sin(2 * math.pi * freq_hz * i / rate))
                       for i in range(n))).tobytes()


def stille(ms: int, rate: int = TELEFON_RATE, rauschen: int = 0) -> bytes:
    """Stille (optional mit deterministischem, leisem Rauschen)."""
    n = rate * ms // 1000
    if not rauschen:
        return bytes(2 * n)
    # deterministisches Pseudo-Rauschen (LCG), kein random -> reproduzierbar
    x, out = 12345, array("h")
    for _ in range(n):
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        out.append((x % (2 * rauschen + 1)) - rauschen)
    return out.tobytes()


def wav_bytes(pcm: bytes, rate: int) -> bytes:
    """PCM16 mono -> WAV-Container (für HTTP-ASR-Endpunkte)."""
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_to_pcm(data: bytes) -> tuple:
    """WAV-Container -> (PCM16 mono, rate). Stereo wird auf links reduziert."""
    import io
    import wave
    with wave.open(io.BytesIO(data), "rb") as w:
        rate, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise ValueError(f"nur 16-bit-WAV unterstützt, nicht {8 * sw} bit")
    if ch == 2:
        s = array("h")
        s.frombytes(raw)
        raw = s[0::2].tobytes()
    return raw, rate
