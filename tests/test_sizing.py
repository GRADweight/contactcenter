"""GPU-Dimensionierung: Parameterzahlen gegen veröffentlichte Werte, KV-Formel, Physik."""

from __future__ import annotations

import pytest

from gw_contactcenter.sizing import (
    GIB,
    GPUS,
    MODELLE,
    Bedarf,
    Modell,
    aktive_anfragen,
    bericht,
    dimensionieren,
    gewichte_byte,
    kv_byte_pro_token,
)


@pytest.mark.parametrize(
    ("name", "veroeffentlicht_mrd", "toleranz"),
    [
        # Qwen3-8B Model Card: 8.2B Parameter, 6.95B ohne Einbettung
        ("qwen3-8b", 8.2, 0.01),
        # Llama 3.1: 8B (8.03B) und 70B (70.6B laut Model Card)
        ("llama-3.1-8b", 8.03, 0.01),
        ("llama-3.1-70b", 70.6, 0.01),
    ],
)
def test_parameterzahl_aus_architektur(
    name: str, veroeffentlicht_mrd: float, toleranz: float
) -> None:
    m = MODELLE[name]
    assert m.parameter / 1e9 == pytest.approx(veroeffentlicht_mrd, rel=toleranz)


def test_qwen3_8b_ohne_einbettung() -> None:
    assert MODELLE["qwen3-8b"].parameter_linear / 1e9 == pytest.approx(6.95, abs=0.005)


def test_kv_cache_formel() -> None:
    # 2 x 36 Schichten x 8 KV-Köpfe x 128 x 2 Byte
    assert kv_byte_pro_token(MODELLE["qwen3-8b"]) == 147_456
    assert kv_byte_pro_token(MODELLE["llama-3.1-8b"]) == 131_072
    assert kv_byte_pro_token(MODELLE["qwen3-8b"], 1) == 73_728
    with pytest.raises(ValueError):
        kv_byte_pro_token(MODELLE["qwen3-8b"], 4)


def test_gewichte_je_praezision() -> None:
    m = MODELLE["qwen3-8b"]
    assert gewichte_byte(m, "bf16") == 2 * m.parameter
    assert gewichte_byte(m, "fp8") < gewichte_byte(m, "bf16")
    assert gewichte_byte(m, "int4") < gewichte_byte(m, "fp8")
    # INT4: lineare Schichten 0.5 Byte + Gruppenoverhead, Einbettung 16 Bit
    assert gewichte_byte(m, "int4") == pytest.approx(
        m.parameter_linear * (0.5 + 2.5 / 128) + 2 * m.parameter_einbettung
    )
    with pytest.raises(ValueError):
        gewichte_byte(m, "int2")


def test_gebundene_einbettung_zaehlt_einmal() -> None:
    m = Modell("x", 2, 64, 128, 1000, 4, 2, 16, True, "test")
    assert m.parameter_einbettung == 1000 * 64


def test_gpu_katalog_datenblattwerte() -> None:
    assert (GPUS["L4"].speicher_gib, GPUS["L4"].bandbreite_gb_s) == (24, 300)
    assert GPUS["L4"].tflops_bf16 == 242 / 2  # Datenblatt nennt Wert mit Sparsity
    assert GPUS["A100-80GB-SXM"].tflops_fp8 is None
    for g in GPUS.values():
        assert g.quelle.startswith(("http", "NVIDIA"))


def test_aktive_anfragen_binomial_quantil() -> None:
    # Binomial(10, 0.2): P(X <= 4) = 0.967, P(X <= 5) = 0.994
    assert aktive_anfragen(10, 0.2) == 5
    assert aktive_anfragen(10, 0.2, quantil=0.95) == 4
    assert aktive_anfragen(1, 0.01) == 1
    assert aktive_anfragen(4, 1.0) == 4
    for falsch in ((0, 0.2), (10, 0.0), (10, 1.5)):
        with pytest.raises(ValueError):
            aktive_anfragen(*falsch)


def test_70b_passt_nicht_auf_eine_l4() -> None:
    optionen = dimensionieren("llama-3.1-70b", Bedarf(gespraeche=10))
    assert not [o for o in optionen if o.gpu == "L4" and o.karten == 1]
    assert optionen, "auf grösseren Karten passt es"


def test_ergebnisse_halten_ihre_bedingungen_ein() -> None:
    b = Bedarf(gespraeche=10)
    m = MODELLE["qwen3-8b"]
    for o in dimensionieren("qwen3-8b", b):
        assert o.gewichte_gib + o.kv_gib <= o.verfuegbar_gib
        assert o.tokens_s_annahme >= b.min_tokens_s
        assert o.ttft_cache_ms <= b.max_ttft_ms
        assert o.tokens_s_annahme == pytest.approx(o.tokens_s_obergrenze * b.effizienz)
        # Roofline nachrechnen
        gpu = GPUS[o.gpu]
        gelesen = gewichte_byte(m, o.praezision) + o.aktive_anfragen * 6000 * kv_byte_pro_token(m)
        assert o.tokens_s_obergrenze == pytest.approx(
            o.karten * gpu.bandbreite_gb_s * 1e9 / gelesen
        )
        assert o.kv_gib == pytest.approx(10 * 6000 * kv_byte_pro_token(m) / GIB)


def test_l4_einzeln_hat_speicher_aber_zu_wenig_bandbreite_bei_standardannahmen() -> None:
    """Ehrliches Ergebnis: Für 10 Gespräche reicht der Speicher einer L4, die
    Roofline-Obergrenze bei 5 aktiven Anfragen liegt aber unter dem, was mit
    50 % Effizienz für 25 Token/s pro Strom nötig wäre."""
    b = Bedarf(gespraeche=10)
    m = MODELLE["qwen3-8b"]
    verfuegbar = GPUS["L4"].speicher_gib * b.gpu_speicheranteil - b.reserve_gib_pro_karte
    assert (gewichte_byte(m, "int4") + 10 * 6000 * kv_byte_pro_token(m)) / GIB < verfuegbar
    assert not [o for o in dimensionieren("qwen3-8b", b) if o.gpu == "L4" and o.karten == 1]
    # Das Urteil hängt an der Effizienz-Annahme: Schon die Gewichte allein begrenzen
    # eine L4 auf 300 GB/s / 6.1 GB = rund 49 Token/s pro Strom. Mit gemessenen 70 %
    # statt angenommenen 50 % und kürzerem Kontext reicht eine Karte.
    obergrenze_einzeln = GPUS["L4"].bandbreite_gb_s * 1e9 / gewichte_byte(m, "int4")
    assert 45 < obergrenze_einzeln < 50
    gemessen = Bedarf(gespraeche=10, kontext_tokens=2000, kv_byte_pro_wert=1, effizienz=0.7)
    assert [o for o in dimensionieren("qwen3-8b", gemessen) if o.gpu == "L4" and o.karten == 1]


def test_sortierung_und_preise() -> None:
    b = Bedarf(gespraeche=10)
    ohne = dimensionieren("qwen3-8b", b)
    assert [o.karten for o in ohne] == sorted(o.karten for o in ohne)
    preise = {"L4": 1.0, "L40S": 10.0, "A100-80GB-SXM": 20.0, "H100-SXM": 30.0}
    mit = dimensionieren("qwen3-8b", b, preise=preise)
    assert all(o.preis is not None for o in mit)
    assert [o.preis for o in mit] == sorted(o.preis or 0 for o in mit)


def test_bericht_zeigt_rechenweg() -> None:
    b = Bedarf()
    text = bericht("qwen3-8b", b, dimensionieren("qwen3-8b", b))
    assert "147'456 Byte/Token" in text
    assert "keine Messung" in text
    assert "Annahmen" in text
    leer = bericht("qwen3-8b", b, [])
    assert "keine Konfiguration" in leer
