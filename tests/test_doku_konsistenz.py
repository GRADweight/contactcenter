"""README und Dossiers gegen den Code.

1. Jeder Block nach ``<!-- ausgabe: BEFEHL -->`` wird neu erzeugt und Zeile für
   Zeile verglichen (``gw-contactcenter ...`` im Prozess, ``python ...`` als
   Unterprozess).
2. Zahlen im Fliesstext, die nicht aus einem solchen Block stammen, werden aus
   dem Code nachgerechnet.
3. Allgemeine Hygiene: relative Links zeigen auf vorhandene Dateien, keine
   lokalen Pfade, E-Mail nur mail@gradweight.ch, GRADweight-Links nur auf
   öffentliche Repos, kein «ß», keine Emojis, keine Testanzahl in der README.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import shlex
import subprocess
import sys
from functools import cache
from pathlib import Path

import pytest

from gw_contactcenter import cli
from gw_contactcenter.adapters.mock import MOCK_LATENZ_MS
from gw_contactcenter.audit import MIN_SCHLUESSEL_BYTE
from gw_contactcenter.gateway import CircuitBreaker
from gw_contactcenter.kapazitaet import Lastannahme, berechne, speicherbedarf
from gw_contactcenter.leitplanken import STANDARD_EINGABE_PRUEFER, auswerten
from gw_contactcenter.server import MIN_FEHLER_VERZOEGERUNG_S
from gw_contactcenter.signatur import MAX_ALTER_S
from gw_contactcenter.sim import DEMO, anruf
from gw_contactcenter.sizing import (
    ERSTER_SATZ_TOKEN,
    GIB,
    GPUS,
    MIN_TOKENS_S,
    MODELLE,
    Bedarf,
    _ttft_ms,
    gewichte_byte,
    kv_byte_pro_token,
)
from gw_contactcenter.telemetry import ZIELBUDGET_MS, Messung
from gw_contactcenter.turn import TurnConfig
from gw_contactcenter.warteschlange import erlang_b, kanaele_fuer_blockierung, kennzahlen

WURZEL = Path(__file__).resolve().parent.parent
DOKUMENTE = sorted([WURZEL / "README.md", *WURZEL.glob("docs/*.md"), *WURZEL.glob("*.md")])
DOKUMENTE = sorted(set(DOKUMENTE))
MARKER = re.compile(r"<!-- ausgabe: (.+?) -->\n```text\n(.*?)\n```", re.DOTALL)


def _text(name: str) -> str:
    return (WURZEL / name).read_text(encoding="utf-8")


@cache
def _ausgabe(befehl: str) -> str:
    teile = shlex.split(befehl)
    if teile[0] == "gw-contactcenter":
        puffer = io.StringIO()
        with contextlib.redirect_stdout(puffer):
            rc = cli.main(teile[1:])
        assert rc == 0, befehl
        return puffer.getvalue()
    if teile[0] == "python":
        r = subprocess.run(  # noqa: S603 - feste Befehle aus den eigenen Dokumenten
            [sys.executable, *teile[1:]],
            cwd=WURZEL,
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        return r.stdout
    raise AssertionError(f"unbekannter Befehl im Marker: {befehl}")


def _bloecke() -> list[tuple[str, str, str]]:
    out = []
    for pfad in DOKUMENTE:
        for m in MARKER.finditer(pfad.read_text(encoding="utf-8")):
            out.append((pfad.relative_to(WURZEL).as_posix(), m.group(1), m.group(2)))
    return out


def test_es_gibt_ausgabebloecke() -> None:
    befehle = {b for _, b, _ in _bloecke()}
    assert "gw-contactcenter kapazitaet --anrufe-pro-tag 200" in befehle
    assert "gw-contactcenter vergleich" in befehle
    assert "gw-contactcenter sim" in befehle
    assert "python evals/run_evals.py" in befehle


@pytest.mark.parametrize(("datei", "befehl", "block"), _bloecke())
def test_ausgabebloecke_entsprechen_dem_code(datei: str, befehl: str, block: str) -> None:
    erwartet = [z.rstrip() for z in _ausgabe(befehl).rstrip("\n").splitlines()]
    im_dokument = [z.rstrip() for z in block.splitlines()]
    assert im_dokument == erwartet, f"{datei}: Ausgabe von «{befehl}» ist veraltet"


# ---------------------------------------------------------------------------
# Zahlen im Fliesstext
# ---------------------------------------------------------------------------


def _enthaelt(datei: str, *teile: str) -> None:
    text = re.sub(r"\s+", " ", _text(datei))
    for t in teile:
        assert t in text, f"{datei}: «{t}» fehlt oder passt nicht mehr zum Code"


def test_readme_kapazitaet_und_korrektur() -> None:
    a = Lastannahme(200).angebot_erlang
    drei, vier = kennzahlen(3, a, 120.0), kennzahlen(4, a, 120.0)
    _enthaelt(
        "README.md",
        f"richtig sind {drei.mittlere_wartezeit_s:.1f} s mittlere Wartezeit bei 3 und "
        f"{vier.mittlere_wartezeit_s:.1f} s bei 4 Agenten",
    )


def test_readme_und_dossiers_golden_set_und_holdout() -> None:
    faelle = json.loads(_text("evals/faelle.json"))["faelle"]
    _enthaelt("README.md", f"{len(faelle)} Fälle")
    kategorien = [f["kategorie"] for f in faelle]
    assert kategorien.count("sicherheit-injection") == 2
    assert kategorien.count("sicherheit-pii") == 2
    _enthaelt("docs/16-leitplanken.md", "zwei Injection- und zwei PII-Fälle")
    holdout = json.loads(_text("evals/injection_holdout.json"))["faelle"]
    h = auswerten(STANDARD_EINGABE_PRUEFER, holdout)
    angriffe, harmlos = h.richtig_positiv + h.falsch_negativ, h.falsch_positiv + h.richtig_negativ
    _enthaelt(
        "README.md",
        f"erkennen sie {h.richtig_positiv} von {angriffe} Angriffen und markieren "
        f"{h.falsch_positiv} von {harmlos} harmlosen Aussagen fälschlich",
    )
    _enthaelt(
        "docs/16-leitplanken.md",
        f"| Angriffe ({angriffe}) | {h.richtig_positiv} | {h.falsch_negativ} |",
        f"| harmlose Aussagen ({harmlos}) | {h.falsch_positiv} Fehlalarme | {h.richtig_negativ} |",
        f"erkennen {h.richtig_positiv} von {angriffe} Angriffen des Holdout-Sets",
    )


def test_latenzbudget_und_simulation() -> None:
    b = ZIELBUDGET_MS
    _enthaelt(
        "README.md",
        f"{b['zeit_bis_erster_ton']:.0f} ms bis zum ersten Ton, davon {b['endpointing']:.0f} ms "
        "Endpointing",
    )
    for stufe, wert in b.items():
        _enthaelt("docs/15-latenz.md", f"| `{stufe}` | {wert:.0f} ms |")
    mit = anruf(DEMO).budget["zeit_bis_erster_ton"]["p50"]
    ohne = anruf(DEMO, zwischenergebnisse=False).budget["zeit_bis_erster_ton"]["p50"]
    _enthaelt("README.md", f"(Zeit bis zum ersten Ton p50 {mit:.0f} ms)", f"p50 {ohne:.0f} ms")
    m = MOCK_LATENZ_MS
    _enthaelt(
        "docs/15-latenz.md",
        f"({m['asr']:.0f} ms ASR, {m['llm_ttft']:.0f} ms erstes Token, "
        f"{m['llm_pro_token']:.0f} ms pro Token, {m['tts']:.0f} ms TTS)",
    )


def test_endpointing_wartezeiten() -> None:
    c = TurnConfig()
    ohne_text = c.warte_unfertig_ms - 0.5 * (c.warte_unfertig_ms - c.warte_fertig_ms)
    fertig = c.warte_unfertig_ms - 0.9 * (c.warte_unfertig_ms - c.warte_fertig_ms)
    _enthaelt("docs/03-asr-transkribation.md", f"{ohne_text:.0f} ms Stille")
    _enthaelt(
        "docs/15-latenz.md",
        f"nach {fertig:.0f} ms Stille",
        f"wartet das Endpointing {ohne_text:.0f} ms",
    )


def test_sensitivitaet_spitzenstunde() -> None:
    for anteil in (0.15, 0.25, 0.35):
        bericht = berechne(Lastannahme(200, anteil, 120.0))
        a = bericht.last.angebot_erlang
        k = bericht.personal
        kanaele = kanaele_fuer_blockierung(a, 0.01)
        zeile = (
            f"| {anteil * 100:.0f} % | {a:.3f} Erlang | {k.agenten} | "
            f"{k.mittlere_wartezeit_s:.1f} s | {kanaele} |"
        )
        _enthaelt("docs/05-skalierung.md", zeile)


def test_cran_referenztabelle() -> None:
    """Die Tabelle in Doku 05 nennt die publizierten Werte; der Code trifft sie."""
    text = _text("docs/05-skalierung.md")
    zeilen = re.findall(r"^\| (\d+) \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) s \|$", text, re.M)
    assert len(zeilen) == 4
    for n, p, sl, asa in zeilen:
        k = kennzahlen(int(n), 10.0, 180.0, 20.0)
        assert k.p_warten == pytest.approx(float(p), abs=5e-7)
        assert k.servicelevel == pytest.approx(float(sl), abs=5e-6)
        assert k.mittlere_wartezeit_s == pytest.approx(float(asa), abs=5e-3)
    assert round(erlang_b(1000, 1500.0), 3) == 0.335
    _enthaelt("docs/05-skalierung.md", "erlang.com nennt 0.335")


def test_speicherbedarf() -> None:
    g711 = speicherbedarf(200, 120)
    opus = speicherbedarf(200, 120, format="opus-32kbit")
    _enthaelt(
        "docs/06-daten-speicherung.md",
        f"{g711.audio_byte_pro_tag / 1e6:.0f} MB pro Tag",
        f"Opus mit 32 kbit/s (Bitrate als Konfigurationsannahme): "
        f"{opus.audio_byte_pro_tag / 1e6:.0f} MB",
        f"{g711.text_byte_pro_tag / 1e6:.1f} MB pro Tag",
    )


def test_gpu_zahlen_im_text() -> None:
    m, l4 = MODELLE["qwen3-8b"], GPUS["L4"]
    b = Bedarf()
    gew, kv = gewichte_byte(m, "int4"), kv_byte_pro_token(m)
    verfuegbar = l4.speicher_gib * b.gpu_speicheranteil - b.reserve_gib_pro_karte
    gelesen = gew + 5 * b.kontext_tokens * kv
    roof = l4.bandbreite_gb_s * 1e9 / gelesen
    _enthaelt(
        "docs/11-performance-architektur.md",
        f"rund {round((gew + 10 * b.kontext_tokens * kv) / GIB)} GiB von {verfuegbar:.1f} GiB",
        f"{roof:.1f} Token/s pro Strom, mit der Effizienzannahme auf {roof * b.effizienz:.1f}",
        f"mindestens {MIN_TOKENS_S:.0f} Token/s pro Strom",
        f"etwa {ERSTER_SATZ_TOKEN} Token soll in {ZIELBUDGET_MS['erster_satz']:.0f} ms",
    )
    kalt = _ttft_ms(m, l4, 1, "int4", b.kontext_tokens, b.mfu)
    cache_ = _ttft_ms(m, l4, 1, "int4", b.neue_tokens_pro_wechsel, b.mfu)
    _enthaelt(
        "docs/13-technologien-erklaert.md",
        f"rund {cache_ / 1000:.1f} s statt {kalt / 1000:.0f} s",
    )


def test_betriebskonstanten() -> None:
    cb = CircuitBreaker()
    _enthaelt(
        "docs/10-betrieb-ops.md",
        f"HTTP 401 nach {MIN_FEHLER_VERZOEGERUNG_S} s Verzögerung",
        f"nach {cb.schwelle} Fehlern in Folge",
        f"{cb.abkuehl_s:.0f} s gesperrt",
        f"mindestens {MIN_SCHLUESSEL_BYTE} Byte",
        f"letzten {Messung().spans.maxlen:,} Messpunkte".replace(",", "'"),
    )
    _enthaelt("docs/16-leitplanken.md", f"höchstens {MAX_ALTER_S} s alt")


def test_uebergabe_zusammenfassung_begrenzt() -> None:
    from .conftest import baue_dialog

    d, _, _ = baue_dialog()
    d.verlauf = [{"role": "user", "content": "x" * 2000}]
    laenge = len(d.uebergabe("Test")["zusammenfassung"])
    _enthaelt("docs/07-weiterleitung-handoff.md", f"höchstens {laenge} Zeichen")


def test_vllm_parameter_wie_im_compose() -> None:
    compose = _text("deploy/compose.yaml")
    for flag, wert in (
        ("max-model-len", "8192"),
        ("max-num-seqs", "32"),
        ("gpu-memory-utilization", "0.60"),
    ):
        assert f"--{flag}={wert}" in compose
        _enthaelt("docs/04-vllm-inferenz.md", f"| `--{flag}` | {wert} |")
    assert "--enable-prefix-caching" in compose


def test_keine_testanzahl_in_der_readme() -> None:
    assert not re.search(r"\b\d+\s+(Tests|tests|Testfälle)\b", _text("README.md"))


# ---------------------------------------------------------------------------
# Hygiene
# ---------------------------------------------------------------------------

_OEFFENTLICHE_REPOS = {
    "guardrails",
    "autonomy",
    "finops",
    "orchestration",
    "rag",
    "shared-lib",
    "gradweight",
    "contactcenter",
    "platforms",
}
_TEXTENDUNGEN = {".md", ".py", ".toml", ".yml", ".yaml", ".json", ".txt", ".cfg", ".example"}
_TEXTNAMEN = {"Dockerfile", "Caddyfile", "Caddy.Dockerfile", "LICENSE", ".gitignore"}
_AUSGENOMMEN = {".git", ".venv", "build", "dist", "__pycache__", ".mypy_cache", ".ruff_cache"}
# Pfade im Container, keine lokalen Pfade eines Rechners
_CONTAINER = ("/home/vllm", "/home/ubuntu")
_CONTAINER_PFADE = {"deploy/compose.yaml": _CONTAINER, "tests/test_doku_konsistenz.py": _CONTAINER}


def _repo_dateien() -> list[Path]:
    out = []
    for p in WURZEL.rglob("*"):
        teile = p.relative_to(WURZEL).parts
        if any(t in _AUSGENOMMEN or t.startswith(".venv") for t in teile):
            continue
        if p.is_file() and (p.suffix in _TEXTENDUNGEN or p.name in _TEXTNAMEN):
            out.append(p)
    return sorted(out)


def test_keine_lokalen_pfade() -> None:
    # Zeichenklassen, damit das Muster nicht sich selbst trifft
    muster = re.compile(r"/[U]sers/|/home/[\w.-]+|~[/]")
    for p in _repo_dateien():
        rel = p.relative_to(WURZEL).as_posix()
        for treffer in muster.findall(p.read_text(encoding="utf-8")):
            erlaubt = _CONTAINER_PFADE.get(rel, ())
            assert treffer in erlaubt, f"{rel}: lokaler Pfad {treffer!r}"


def test_email_nur_kontaktadresse() -> None:
    muster = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
    for p in _repo_dateien():
        rel = p.relative_to(WURZEL).as_posix()
        for adresse in muster.findall(p.read_text(encoding="utf-8")):
            beispiel = adresse.endswith(("@example.com", "@example.org", "@example.net"))
            assert adresse == "mail@gradweight.ch" or (rel.startswith("tests/") and beispiel), (
                f"{rel}: {adresse}"
            )


def test_gradweight_links_nur_auf_oeffentliche_repos() -> None:
    muster = re.compile(r"github\.com/GRADweight/([\w.-]+)", re.IGNORECASE)
    for p in _repo_dateien():
        for repo in muster.findall(p.read_text(encoding="utf-8")):
            assert repo.removesuffix(".git") in _OEFFENTLICHE_REPOS, f"{p.name}: {repo}"


@pytest.mark.parametrize("pfad", DOKUMENTE, ids=lambda p: p.name)
def test_relative_links_existieren(pfad: Path) -> None:
    text = pfad.read_text(encoding="utf-8")
    for ziel in re.findall(r"\]\(([^)\s]+)\)", text):
        if ziel.startswith(("http://", "https://", "mailto:", "#")):
            continue
        datei = (pfad.parent / ziel.split("#")[0]).resolve()
        assert datei.exists(), f"{pfad.name}: Link auf {ziel} ohne Ziel"


@pytest.mark.parametrize("pfad", DOKUMENTE, ids=lambda p: p.name)
def test_schweizer_schreibweise_ohne_emojis(pfad: Path) -> None:
    text = pfad.read_text(encoding="utf-8")
    assert "ß" not in text
    emoji = re.compile("[\U0001f300-\U0001faff☀-➿⭐✅]")
    assert not emoji.search(text), f"{pfad.name}: Emoji"


def test_readme_beginnt_mit_englischem_abstract() -> None:
    zeilen = _text("README.md").splitlines()
    ueberschriften = [z for z in zeilen if z.startswith("## ")]
    assert ueberschriften[0] == "## In English"
