"""Kapazitätsrechner: Beispiel aus der README und Speicherrechnung."""

from __future__ import annotations

import pytest

from gw_contactcenter.kapazitaet import AUDIOFORMATE, Lastannahme, berechne, speicherbedarf
from gw_contactcenter.warteschlange import erlang_b


def test_readme_beispiel() -> None:
    b = berechne(Lastannahme(200), ziel_mittlere_wartezeit_s=20)
    assert b.last.anrufe_spitzenstunde == 50
    assert b.last.angebot_erlang == pytest.approx(5 / 3)
    assert b.personal.agenten == 4
    assert round(b.personal.mittlere_wartezeit_s, 1) == 5.3
    drei = next(k for k in b.tabelle if k.agenten == 3)
    assert round(drei.mittlere_wartezeit_s, 1) == 27.0  # nicht 13.5 s wie früher dokumentiert
    assert b.bot_kanaele == 6
    assert b.bot_ueberlauf == pytest.approx(erlang_b(6, 5 / 3))
    assert b.koepfe == 4
    text = b.als_text()
    assert "Annahme" in text
    assert "<- kleinste Zahl" in text


def test_servicelevel_ziel_abwesenheit_und_abbrueche() -> None:
    b = berechne(
        Lastannahme(200, 0.25, 120),
        ziel_mittlere_wartezeit_s=None,
        ziel_servicelevel=0.8,
        abwesenheit=0.3,
        geduld_s=60,
    )
    assert b.personal.agenten == 4
    assert b.koepfe == 6  # ceil(4 / 0.7)
    assert b.abbrueche is not None
    assert 0 < b.abbrueche.p_abbruch < 0.1
    assert "Erlang-A" in b.als_text()
    assert "30 % Abwesenheit" in b.als_text()


@pytest.mark.parametrize(
    "kw",
    [
        {"anrufe_pro_tag": 0},
        {"anrufe_pro_tag": 10, "spitzenstunden_anteil": 0},
        {"anrufe_pro_tag": 10, "spitzenstunden_anteil": 1.5},
        {"anrufe_pro_tag": 10, "aht_s": 0},
    ],
)
def test_lastannahme_prueft(kw: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        Lastannahme(**kw)


def test_abwesenheit_prueft() -> None:
    with pytest.raises(ValueError):
        berechne(Lastannahme(100), abwesenheit=1.0)


def test_speicher_g711_stereo() -> None:
    # 8000 Samples/s x 1 Byte x 2 Kanäle = 16'000 Byte/s; 200 Anrufe x 120 s
    s = speicherbedarf(200, 120)
    assert AUDIOFORMATE["g711-stereo"] == 16_000
    assert s.audio_byte_pro_tag == 384_000_000
    assert s.text_byte_pro_tag == 200 * 4000 * 4
    assert s.gesamt_byte_pro_tag == s.audio_byte_pro_tag + s.text_byte_pro_tag
    assert speicherbedarf(200, 120, format="opus-32kbit").audio_byte_pro_tag == 96_000_000
    with pytest.raises(ValueError):
        speicherbedarf(200, 120, format="mp3")
    with pytest.raises(ValueError):
        speicherbedarf(-1, 120)
