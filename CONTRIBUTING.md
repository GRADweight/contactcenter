# Mitwirken

## Einrichten

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

## Vor jedem Pull Request

```bash
ruff check . && ruff format --check . && mypy
pytest                         # mit Abdeckung, Schwelle in pyproject.toml
python evals/run_evals.py      # Golden-Set mit Gate
```

Alles läuft offline, ohne GPU und ohne Zugangsdaten. Tests greifen nie auf das Netz zu;
HTTP-Dienste werden mit lokalen Testservern nachgebildet (`tests/conftest.py`).

## Regeln

- Kern ohne Laufzeit-Abhängigkeiten; Fremdpakete nur als Extra (`server`, `silero`).
- Neues Verhalten braucht Tests; neue Anliegen, Werkzeuge oder Leitplanken zusätzlich einen
  Fall in `evals/faelle.json`. Das Holdout-Set `evals/injection_holdout.json` wird nie zum
  Anpassen von Mustern verwendet.
- Zahlen in README und `docs/` stammen aus dem Code: entweder als Ausgabeblock nach
  `<!-- ausgabe: BEFEHL -->` oder geprüft in `tests/test_doku_konsistenz.py`. Annahmen,
  Simulationen und Messungen sind als solche gekennzeichnet; Messungen nennen Methode,
  Hardware und Datum.
- Dossiers enden mit «Grenzen» und «Kritische Fragen».
- Schweizer Hochdeutsch (immer «ss», kein Eszett), sachlich, ohne Preise und ohne Rechtsrat.
- Nur erfundene oder öffentliche Daten, nie echte Gesprächs- oder Kundendaten.
- Beispiele in `beispiele/` haben einen `selftest`, der in der CI läuft.

## Sicherheitslücken

Nicht als Issue, sondern wie in [SECURITY.md](SECURITY.md) beschrieben.
