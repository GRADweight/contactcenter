"""Messwerkzeug gegen einen lokalen Fake-Server mit bekannter Verzögerung."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gw_contactcenter.latenzmessung import Endpunkte, main, messen

from .conftest import FakeOpenAI


def _endpunkte(url: str) -> Endpunkte:
    return Endpunkte(
        llm_url=url,
        llm_modell="m",
        asr_url=url,
        asr_modell="a",
        tts_url=url,
        tts_modell="t",
        tts_stimme="s",
        tts_format="pcm",
        tts_abtastrate=8000,
    )


def test_messung_misst_eingestellte_verzoegerung(fake_openai: str) -> None:
    FakeOpenAI.verzoegerung_s = 0.05
    b = messen(_endpunkte(fake_openai), wiederholungen=3, aufwaermen=1, hardware="Fake-Server")
    k = b.kennzahlen
    assert set(k) == {
        "asr_ms",
        "llm_ttft_ms",
        "llm_erster_satz_ms",
        "llm_gesamt_ms",
        "llm_tokens_pro_s",
        "tts_erstes_byte_ms",
        "tts_gesamt_ms",
        "kette_bis_erstes_audio_ms",
    }
    assert k["llm_ttft_ms"]["n"] == 3  # die Kette meldet nur ihren Gesamtwert
    assert k["asr_ms"]["min"] >= 50
    assert k["llm_ttft_ms"]["min"] >= 50
    assert k["kette_bis_erstes_audio_ms"]["min"] >= 150  # drei Stufen à 50 ms
    for s in k.values():
        assert s["min"] <= s["p50"] <= s["p90"] <= s["p95"] <= s["p99"] <= s["max"]
    assert b.umgebung["hardware"] == "Fake-Server"
    assert b.methode["endpunkte"]["llm"].startswith("http://127.0.0.1")
    assert b.fehler == []
    text = b.als_text()
    assert "Messung" in text and "Fake-Server" in text


def test_parallel_und_fehler_werden_gezaehlt(fake_openai: str) -> None:
    FakeOpenAI.fehler_folge = [500]
    e = Endpunkte(llm_url=fake_openai, llm_modell="m")
    b = messen(e, wiederholungen=2, aufwaermen=0, parallel=2)
    assert b.kennzahlen["llm_ttft_ms"]["n"] == 3
    assert len(b.fehler) == 1 and "HTTPFehler" in b.fehler[0]
    assert "Fehler: 1" in b.als_text()


def test_zugangsdaten_erscheinen_nicht_im_bericht(fake_openai: str) -> None:
    mit = fake_openai.replace("http://", "http://nutzer:geheim@")
    b = messen(Endpunkte(llm_url=fake_openai, llm_modell="m"), wiederholungen=1, aufwaermen=0)
    assert "geheim" not in json.dumps(b.als_dict())
    from gw_contactcenter.latenzmessung import _ohne_zugangsdaten

    assert "geheim" not in _ohne_zugangsdaten(mit + "?token=abc")


def test_eingaben_werden_geprueft() -> None:
    with pytest.raises(ValueError):
        messen(Endpunkte(), wiederholungen=1)
    with pytest.raises(ValueError):
        messen(Endpunkte(llm_url="http://x"), wiederholungen=0)
    with pytest.raises(ValueError):
        messen(Endpunkte(tts_url="http://x"), wiederholungen=1)


def test_kommandozeile_schreibt_json(
    fake_openai: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wav = tmp_path / "turn.wav"
    from gw_contactcenter import audio

    wav.write_bytes(audio.wav_bytes(audio.ton(200, 300, 16000), 16000))
    ziel = tmp_path / "bericht.json"
    rc = main(
        [
            "--llm-url",
            fake_openai,
            "--llm-modell",
            "m",
            "--asr-url",
            fake_openai,
            "--wav",
            str(wav),
            "--wiederholungen",
            "2",
            "--aufwaermen",
            "0",
            "--hardware",
            "Test",
            "--json",
            str(ziel),
            "--llm-extra-json",
            '{"chat_template_kwargs": {"enable_thinking": false}}',
        ]
    )
    assert rc == 0
    d = json.loads(ziel.read_text(encoding="utf-8"))
    assert d["methode"]["wiederholungen"] == 2 and "llm_ttft_ms" in d["kennzahlen"]
    assert "Latenzmessung" in capsys.readouterr().out
