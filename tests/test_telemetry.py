"""Latenz-Spans, Perzentile, Prometheus-Export, Modul-Einstieg."""

from __future__ import annotations

import runpy
import sys

import pytest

from gw_contactcenter.telemetry import ZIELBUDGET_MS, Messung, SimUhr, perzentil


def test_perzentil_lineare_interpolation() -> None:
    assert perzentil([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert perzentil([5.0], 99) == 5.0
    assert perzentil([0.0, 10.0], 95) == pytest.approx(9.5)
    with pytest.raises(ValueError):
        perzentil([], 50)
    with pytest.raises(ValueError):
        perzentil([1.0], 101)


def test_spans_budget_und_prometheus() -> None:
    uhr = SimUhr()
    m = Messung(uhr)
    with m.span("asr", modell="x") as s:
        uhr.vorwaerts(120)
    assert s.dauer_ms == 120.0 and s.attribute == {"modell": "x"}
    m.erfassen("zeit_bis_erster_ton", 0, 900)
    m.erfassen("zeit_bis_erster_ton", 0, 1100)
    b = m.budget_bericht()
    assert b["zeit_bis_erster_ton"]["p50"] == 1000.0
    assert b["zeit_bis_erster_ton"]["ziel_p50"] == ZIELBUDGET_MS["zeit_bis_erster_ton"]
    assert "llm_ttft" not in b, "ohne Werte kein Eintrag"
    text = m.prometheus()
    assert "# TYPE gw_contactcenter_asr_ms summary" in text
    assert 'gw_contactcenter_zeit_bis_erster_ton_ms{quantile="0.5"} 1000.0' in text
    assert "gw_contactcenter_zeit_bis_erster_ton_ms_count 2" in text
    assert m.perzentil("gibt_es_nicht", 50) == 0.0
    uhr.vorstellen_auf(50)
    assert uhr() == 120.0, "Uhr läuft nie rückwärts"


def test_ringpuffer_und_echte_uhr() -> None:
    m = Messung(max_spans=2)
    for i in range(3):
        m.erfassen("x", 0, i)
    assert m.werte("x") == [1.0, 2.0]
    with m.span("y"):
        pass
    assert m.werte("y")[0] >= 0


def test_modul_einstieg(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["gw_contactcenter", "--version"])
    with pytest.raises(SystemExit) as e:
        runpy.run_module("gw_contactcenter", run_name="__main__")
    assert e.value.code == 0
    assert capsys.readouterr().out.strip()
