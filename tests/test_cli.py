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


def test_gpu_annahmen_aenderbar(capsys: pytest.CaptureFixture[str]) -> None:
    basis = ["gpu", "--kontext", "2000", "--kv-fp8", "--effizienz", "0.7"]
    _, standard = _lauf(basis, capsys)
    _, guenstig = _lauf([*basis, "--min-tokens-s", "25"], capsys)
    assert " 1 L4 " not in standard and " 1 L4 " in guenstig
    assert "Effizienz 70 %" in guenstig and ">= 25 Tok/s" in guenstig


def test_sim_und_vergleich(capsys: pytest.CaptureFixture[str]) -> None:
    rc, out = _lauf(["sim"], capsys)
    assert rc == 0 and "SIMULATION" in out
    rc, out = _lauf(["vergleich", "--anrufe", "20000"], capsys)
    assert rc == 0 and "Erlang-A" in out
    assert vergleich_text(20_000).count("\n") == len(out.strip().splitlines()) - 1


def test_server_und_latenz_werden_weitergereicht(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["server", "--hilfe-gibt-es-nicht"])
    assert main(["latenz-messen"]) == 2
    assert "mindestens einen Endpunkt" in capsys.readouterr().err


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


@pytest.mark.parametrize(
    "args",
    [
        ["gpu", "--effizienz", "0"],
        ["gpu", "--preise", "/nicht/vorhanden.json"],
        ["kapazitaet", "--anrufe-pro-tag", "-5"],
        ["kapazitaet", "--anrufe-pro-tag", "200", "--spitzenanteil", "1.5"],
    ],
)
def test_eingabefehler_ergeben_rueckgabewert_2(
    args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(args) == 2
    assert capsys.readouterr().err.startswith("Fehler: ")


def test_argparse_fehler_ergeben_rueckgabewert_2() -> None:
    with pytest.raises(SystemExit) as e:
        main(["gpu", "--modell", "gibt-es-nicht"])
    assert e.value.code == 2


@pytest.mark.parametrize("ungepuffert", [True, False])
def test_geschlossene_pipe_ohne_fehlermeldung(ungepuffert: bool) -> None:
    """Wie «gw-contactcenter vergleich | head -0»: Der Leser schliesst die Pipe,
    bevor die Ausgabe kommt (die Rechnung dauert länger als das Schliessen).
    Gepuffert tritt der Fehler erst beim Leeren des Puffers auf."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
    if ungepuffert:
        env["PYTHONUNBUFFERED"] = "1"
    p = subprocess.Popen(
        [sys.executable, "-m", "gw_contactcenter", "vergleich"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert p.stdout is not None and p.stderr is not None
    p.stdout.close()
    fehler = p.stderr.read()
    assert p.wait(timeout=60) == 1
    assert not any(w in fehler for w in ("BrokenPipe", "Traceback", "Fehler")), fehler
