"""Richtlinien für deploy/: feste Versionen, kein root, lokale Ports, Pflicht-Geheimnisse."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"


def _compose() -> dict[str, Any]:
    daten: dict[str, Any] = yaml.safe_load((DEPLOY / "compose.yaml").read_text(encoding="utf-8"))
    return daten


def _standardwert(ausdruck: str) -> str:
    """${VAR:-wert} -> wert; sonst der Ausdruck selbst."""
    m = re.fullmatch(r"\$\{\w+:-([^}]*)\}", ausdruck)
    return m.group(1) if m else ausdruck


def test_images_mit_fester_version() -> None:
    for name, s in _compose()["services"].items():
        if "build" in s:
            continue
        image = _standardwert(s["image"])
        repo, _, tag = image.rpartition(":")
        assert repo and tag, f"{name}: Image ohne Tag"
        assert tag != "latest" and "latest" not in tag, f"{name}: {image}"
        assert re.search(r"\d+\.\d+\.\d+", tag), f"{name}: keine feste Version: {image}"


def test_kein_container_als_root() -> None:
    for name, s in _compose()["services"].items():
        user = str(s.get("user", ""))
        assert user, f"{name}: user fehlt"
        assert user.split(":")[0] not in ("0", "root"), f"{name}: läuft als root"
    for datei in ("Dockerfile", "Caddy.Dockerfile"):
        text = (DEPLOY / datei).read_text(encoding="utf-8")
        users = re.findall(r"^USER\s+(\S+)", text, re.MULTILINE)
        assert users and users[-1].split(":")[0] not in ("0", "root"), datei
        for basis in re.findall(r"^FROM\s+(\S+)", text, re.MULTILINE):
            assert re.search(r":\d+\.\d+\.\d+", basis), f"{datei}: Basis-Image ohne feste Version"


def test_haertung_fuer_alle_dienste() -> None:
    for name, s in _compose()["services"].items():
        assert s.get("cap_drop") == ["ALL"], name
        assert "no-new-privileges:true" in s.get("security_opt", []), name


def test_ports_standardmaessig_nur_lokal() -> None:
    for name, s in _compose()["services"].items():
        for p in s.get("ports", []):
            adresse = _standardwert(str(p).rsplit(":", 2)[0])
            assert adresse == "127.0.0.1", f"{name}: {p}"


def test_geheimnisse_sind_pflicht() -> None:
    agent = _compose()["services"]["agent"]["environment"]
    for k in ("AUDIOHOOK_API_KEY", "AUDIOHOOK_CLIENT_SECRET", "AUDIT_HMAC_KEY"):
        assert re.fullmatch(r"\$\{" + k + r":\?.+\}", agent[k]), k
    assert agent["GW_MODUS"] == "produktion"
    grafana = _compose()["services"]["grafana"]["environment"]
    assert grafana["GF_SECURITY_ADMIN_PASSWORD"].startswith("${GRAFANA_ADMIN_PASSWORD:?")


def test_env_beispiel_ohne_werte() -> None:
    for zeile in (DEPLOY / ".env.example").read_text(encoding="utf-8").splitlines():
        if zeile.strip() and not zeile.startswith("#"):
            schluessel, _, wert = zeile.partition("=")
            assert wert == "", f"{schluessel} hat einen Wert"


def test_caddy_ohne_privilegierte_ports() -> None:
    text = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "http_port 8080" in text and "https_port 8443" in text
    assert "respond @intern 404" in text
