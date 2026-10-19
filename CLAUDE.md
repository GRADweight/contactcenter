# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working
with code in this repository.

Die uebergeordneten Regeln stehen in `../CLAUDE.md` (Schweizer
Hochdeutsch mit ss, ein Repo pro Sitzung, nie auf main pushen, nie
Geheime, nie irreversibel selbst) und gelten hier zusaetzlich.

## Was dieses Repo ist

Ein **Referenz-Repo** fuer KI im Kundendienst / Voice-AI /
Contact-Center (Schweizer KMU). Zwei Dinge:

1. **Drei lauffaehige Samples** in `beispiele/` (nur
   Python-Standardbibliothek, >= 3.9, offline, ohne API-Key):
   `transkript_pipeline.py`, `skalierungsrechner.py`,
   `handoff_router.py`.
2. **Elf Dossiers** in `docs/` (00-10): das komplette
   Unterfangen (Facetten 1-14, s. `docs/02`).

**Kein** produktionsreifes System, **kein** Rechtsrat,
**keine** Kalkulation. Alle CHF-Zahlen sind
Groessenordnungen, Stichtag 10/2026.

## Befehle

```bash
# Die drei Samples (offline, selftest pro Datei)
python3 beispiele/transkript_pipeline.py selftest
python3 beispiele/skalierungsrechner.py selftest
python3 beispiele/skalierungsrechner.py rechner 200 10
python3 beispiele/handoff_router.py selftest
python3 beispiele/handoff_router.py route --anfall vertrag-aenderung

# CI-Gate (lokal entspricht GitHub Actions ci.yml, ohne Netz)
python3 -m py_compile beispiele/*.py
python3 beispiele/transkript_pipeline.py selftest
python3 beispiele/skalierungsrechner.py selftest
python3 beispiele/handoff_router.py selftest
```

## Architektur

- `beispiele/skalierungsrechner.py` —
  **Erlang-C-Queue** (lambda, Handhabung,
  Agenten, Wartezeit) plus **vLLM-Slots
  pro GPU** (7-14B / 30B / 70B AWQ) plus
  **Kosten-Groessenordnung** (CHF/Monat,
  24/7). CLI-Unterbefehle: `rechner`,
  `slots`, `kost`, `selftest`.
- `beispiele/transkript_pipeline.py`
  — 5-Etapen-Kette
  Ton -> ASR (Mock
  faster-whisper) -> Diariz.
  -> PII-Mask -> LLM
  (Mock, RAG-Antwort
  mit Quellen) ->
  Handoff (V0-V4
  plus R0-R3).
  Pseudocode-Adapter,
  kein Netz.
- `beispiele/handoff_router.py`
  — V0-V4
  Vertraulichkeit
  (V0 oeffentlich
  bis V4 streng)
  plus R0-R3
  Risikoklasse,
  mit
  `darf_in_cloud`
  pro
  Kategorie,
  SLA,
  zustaendiger
  Bereich und
  Verbindlichkeit.

## Zahlen, die an mehreren Stellen stehen

Preise (GPU, CHF/Monat) und Slots pro GPU stehen in
`beispiele/skalierungsrechner.py`, `docs/05-skalierung.md`,
`README.md` und `OFFENE_PUNKTE.md` #1. Bei einer Aenderung an
einem Ort muessen alle vier angepasst werden, sonst driften
sie auseinander.

## Doku-Konventionen (siehe auch `CONTRIBUTING.md`)

- Jede Dossier endet mit `## Grenzen (ehrlich)` und
  `## Kritische Fragen` (s. `OFFENE_PUNKTE.md`).
- **Keine Preise, keine Kundennamen, keine Kalkulation.**
- **Kein Rechtsrat**: DSG / AI-Act gehoeren zu
  `grad-governance` + Rechtsbeistand.
- Modell- und Plattformnamen altern monatlich; vor jedem
  Angebot neu pruefen.
- Ein neues Sample braucht einen `selftest` (ohne Netz,
  ohne API-Key), sonst greift der CI-Gate ihn nicht auf.

## Rahmen

- **Nie auf main pushen** (Org-Repo), **nie Force-Push**,
  **nie Hooks umgehen**. Nicht umkehrbare Schritte
  (Repo loeschen, Sichtbarkeit aendern, Historie umschreiben,
  Secrets) nie selbst ausfuehren — immer drucken/fragen.
- **Geheime** nie in Dateien, Logs, Commits oder Ausgaengen.
- Nur erfundene oder oeffentliche Daten; nie echte
  Kundendaten.
- Kontakt in Texten nur `mail@gradweight.ch`.
