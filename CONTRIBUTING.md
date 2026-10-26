# Mitwirken an contactcenter

Danke, dass du mitwirkst. Dies ist ein **Referenzwerk** der GRADweight
GmbH für KI im Kundendienst / Voice-AI im Schweizer Mittelstand. Der
Stil ist bewusst: Schweizer Hochdeutsch, Probleme vor Lob, keine Preise
und kein Rechtsrat.

## Was gehört in ein PR

- **Korrektur in einem Dossier** (veralteter Modell- oder Toolname,
  fehlende Quellenangabe, falscher Wert in einer Tabelle):
  willkommen, bitte `git blame`-Quelle im Commit-Message angeben.
- **Neues Beispiel-Script in `beispiele/`**:
  **Bedingung**: jedes neue Sample MUSS einen `python beispiele/<name>.py
  selftest` enthalten, der
  - ohne Netz, ohne API-Keys, ohne externe Abhängigkeiten
  - mit Python-Standardbibliothek allein
  - und ohne Zufalls- oder Zeit-abhängigen Wert
  grün wird.
  Das Sample wird in `.github/workflows/ci.yml` eingetragen. Ohne Selftest
  wird das PR nicht gemergt.
- **Neues Dossier in `docs/`**: mit einer `## Grenzen (ehrlich)`-Sektion
  und einer `## Kritische Fragen` (Entscheid-Checkpunkte, NICHT To-dos).
  Preiskalkulation gehört NICHT in ein Dossier, s. nur
  `OFFENE_PUNKTE.md` für Grössenordnungen.

## Was NICHT in ein PR gehört

- Konkrete Preisangaben oder Kalkulation
- Kundennamen oder echte Kundendaten
- Em-Dashes in Text (benutz `-` oder `–` korrekt)
- Lob-Reden über das Projekt, ohne die Grenzen mit anzugeben

## Änderungen am Paket `voiceagent/`

- Kern bleibt **nur Standardbibliothek**; Fremdpakete nur in `adapters/`
  oder `server.py`, und dort optional importiert.
- Jede Änderung braucht grüne Tests **und** ein bestandenes Eval-Gate:
  `python -m unittest discover -s tests` und `python evals/run_evals.py`.
- Neues Verhalten (Anliegen, Werkzeug, Leitplanke) bekommt mindestens
  einen Eval-Fall in `evals/faelle.json`.
- Policy-Werte (V0-V4, R0-R3, SLA) nur zusammen mit
  `beispiele/handoff_router.py` ändern; `tests/test_kern.py` prüft das.

## Referenz-Nutzung

Code in `voiceagent/`, `beispiele/`, `evals/` und `tests/` steht unter
**MIT** (s. `LICENSE`) und darf frei verwendet werden. Attribution
«GRADweight GmbH, contactcenter, v2» ist erwünscht. Die kommerziellen
Vorbehalte in der `LICENSE` betreffen Beratung und Kalkulationsmaterial,
nicht den Code.

## Sitzungs-Matik (für interne Arbeit)

Siehe `PROJEKT.md` § Sitzungs-Matik (5 Regeln). Kurz: Kontext-Reinigung ist
verlustbehaftet; Repo + Git-Log ist der Recovery-Wege, NICHT das
Gedächtnis.
