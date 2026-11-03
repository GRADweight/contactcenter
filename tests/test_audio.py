"""G.711, Resampling, WAV, VAD."""

from __future__ import annotations

import hashlib
import importlib.util
import warnings
from array import array

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gw_contactcenter import audio
from gw_contactcenter.vad import EnergieVAD


def test_ulaw_bekannte_codepunkte() -> None:
    """ITU-T G.711: 0xFF = +0, 0x7F = -0, 0x80 = +32124, 0x00 = -32124."""
    dek = array("h")
    dek.frombytes(audio.ulaw_to_pcm16(bytes([0xFF, 0x7F, 0x80, 0x00])))
    assert list(dek) == [0, 0, 32124, -32124]
    assert audio.pcm16_to_ulaw(b"\x00\x00") == b"\xff"


def test_ulaw_tabellen_wie_audioop_auch_ohne_audioop() -> None:
    """SHA-256 der vollständigen Tabellen, einmal mit audioop (Python 3.12)
    berechnet. Damit ist die Gleichheit auch ab 3.13 geprüft."""
    werte = array("h", range(-32768, 32768)).tobytes()
    kodiert = hashlib.sha256(audio.pcm16_to_ulaw(werte)).hexdigest()
    dekodiert = hashlib.sha256(audio.ulaw_to_pcm16(bytes(range(256)))).hexdigest()
    assert kodiert == "81d633c9e6972a18c74a58720b96cb8ca0bdd096d4060b646dd708c3b846019a"
    assert dekodiert == "3dab54339e520bb2c924826e3b72a917a2b612e9fd12fc867500f1d983a75827"


@pytest.mark.skipif(importlib.util.find_spec("audioop") is None, reason="audioop ab 3.13 entfernt")
def test_ulaw_gleich_wie_audioop() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import audioop

    alle = bytes(range(256))
    assert audio.ulaw_to_pcm16(alle) == audioop.ulaw2lin(alle, 2)
    werte = array("h", range(-32768, 32768)).tobytes()  # jedes 16-Bit-Sample
    assert audio.pcm16_to_ulaw(werte) == audioop.lin2ulaw(werte, 2)


@given(st.lists(st.integers(min_value=-32768, max_value=32767), min_size=1, max_size=200))
def test_ulaw_rundreise_relativer_fehler_klein(samples: list[int]) -> None:
    pcm = array("h", samples).tobytes()
    zurueck = array("h")
    zurueck.frombytes(audio.ulaw_to_pcm16(audio.pcm16_to_ulaw(pcm)))
    for x, y in zip(samples, zurueck, strict=True):
        assert abs(x - y) <= max(16, abs(x) * 0.07)


def test_deinterleave() -> None:
    assert audio.deinterleave(bytes([1, 2, 3, 4, 5, 6]), 2) == [bytes([1, 3, 5]), bytes([2, 4, 6])]
    assert audio.deinterleave(b"abc", 1) == [b"abc"]
    with pytest.raises(ValueError):
        audio.deinterleave(b"abc", 2)
    with pytest.raises(ValueError):
        audio.deinterleave(b"abc", 0)


def test_resampling() -> None:
    pcm = audio.ton(300, 200, rate=8000)
    hoch = audio.resample_linear(pcm, 8000, 16000)
    assert len(hoch) == 2 * len(pcm)
    assert len(audio.resample_linear(hoch, 16000, 8000)) == len(pcm)
    assert audio.resample_linear(pcm, 8000, 8000) is pcm
    assert audio.resample_linear(b"\x01", 8000, 16000) == b""
    with pytest.raises(ValueError):
        audio.resample_linear(pcm, 0, 8000)


def test_wav_rundreise_und_stereo() -> None:
    pcm = audio.ton(500, 50, rate=16000)
    assert audio.wav_to_pcm(audio.wav_bytes(pcm, 16000)) == (pcm, 16000)
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(array("h", [1, 2, 3, 4]).tobytes())
    links, rate = audio.wav_to_pcm(buf.getvalue())
    assert (array("h", links).tolist(), rate) == ([1, 3], 8000)
    buf8 = io.BytesIO()
    with wave.open(buf8, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(8000)
        w.writeframes(b"\x80\x80")
    with pytest.raises(ValueError):
        audio.wav_to_pcm(buf8.getvalue())


def test_pegel_und_frames() -> None:
    assert audio.dbfs(audio.stille(20)) == -96.0
    assert audio.rms(b"") == 0.0
    assert -14 < audio.dbfs(audio.ton(440, 100, amp=0.3)) < -13  # 0.3/sqrt(2) -> -13.5 dBFS
    assert len(list(audio.frames(audio.stille(100), 8000))) == 5
    assert audio.stille(20, rauschen=30) == audio.stille(20, rauschen=30)  # deterministisch


def test_vad() -> None:
    v = EnergieVAD()
    for _ in range(20):
        assert not v.ist_sprache(audio.stille(20, rauschen=30))
    assert v.ist_sprache(audio.ton(200, 20, amp=0.3))
    # Nachlauf: drei Frames Stille gelten noch als Sprache
    assert [v.ist_sprache(audio.stille(20)) for _ in range(4)] == [True, True, True, False]
