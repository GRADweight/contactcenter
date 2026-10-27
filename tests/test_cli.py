"""Kommandozeile gw-contactcenter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gw_contactcenter import __version__
from gw_contactcenter.cli import main, vergleich_text


def _lauf(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    rc = main(args)
    return rc, capsys.readouterr().out


def test_hilfe_version_unbekannt(capsys: pytest.CaptureFixture[str]) -> None:
    assert "kapazitaet" in _lauf([], capsys)[1]
    assert _lauf(["--version"], capsys) == (0, __version__ + "\n")
    assert main(["fax"]) == 2


def test_kapazitaet(capsys: pytest.CaptureFixture[str]) -> None:
    rc, out = _lauf(["kapazitaet", "--anrufe-pro-tag", "200", "--speicher"], capsys)
    assert rc == 0
    assert "Ergebnis: 4 Agenten" in out
    assert "G.711 Stereo 384 MB" in out
    rc, out = _lauf(
        [
            "kapazitaet",
            "--anrufe-pro-tag",
            "200",
            "--ziel-servicelevel",
            "0.8",
            "--geduld-s",
            "60",
            "--abwesenheit",
            "0.3",
        ],
        capsys,
    )
    assert "6 mit 30 % Abwesenheit" in out and "Erlang-A" in out


def test_gpu_mit_eigenen_preisen(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, out = _lauf(["gpu", "--modell", "qwen3-8b", "--gespraeche", "10"], capsys)
    assert rc == 0 and "147'456 Byte/Token" in out
    preise = tmp_path / "preise.json"
    preise.write_text(json.dumps({"L40S": 1, "H100-SXM": 2}), encoding="utf-8")
    rc, out = _lauf(["gpu", "--kv-fp8", "--preise", str(preise)], capsys)
    assert rc == 0 and "73'728 Byte/Token" in out


def test_sim_und_vergleich(capsys: pytest.CaptureFixture[str]) -> None:
    rc, out = _lauf(["sim"], capsys)
    assert rc == 0 and "SIMULATION" in out
    rc, out = _lauf(["vergleich", "--anrufe", "20000"], capsys)
    assert rc == 0 and "Erlang-A" in out
    assert vergleich_text(20_000).count("\n") == len(out.strip().splitlines()) - 1


def test_server_und_latenz_werden_weitergereicht(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["server", "--hilfe-gibt-es-nicht"])
    with pytest.raises(ValueError):
        main(["latenz-messen"])


def test_audit_pruefen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from gw_contactcenter.audit import AuditLog

    pfad = tmp_path / "a.jsonl"
    log = AuditLog(str(pfad), schluessel=b"k" * 32)
    log.schreiben("start")
    log.schreiben("ende")
    monkeypatch.setenv("AUDIT_HMAC_KEY", "k" * 32)
    rc, out = _lauf(["audit-pruefen", str(pfad)], capsys)
    assert rc == 0 and out.startswith("intakt: 2 Einträge")
    zeilen = pfad.read_text(encoding="utf-8").splitlines()
    pfad.write_text(zeilen[0].replace("start", "manipuliert") + "\n" + zeilen[1] + "\n", "utf-8")
    rc, out = _lauf(["audit-pruefen", str(pfad)], capsys)
    assert rc == 1 and "ab Eintrag 1" in out
    monkeypatch.delenv("AUDIT_HMAC_KEY")
    assert main(["audit-pruefen", str(pfad)]) == 2, "ohne Schlüssel keine Prüfung"
    assert main(["audit-pruefen", str(tmp_path / "fehlt.jsonl")]) == 2
    dev = tmp_path / "dev.jsonl"
    AuditLog(str(dev), unsigniert_erlaubt=True).schreiben("x")
    assert main(["audit-pruefen", "--unsigniert", str(dev)]) == 0
