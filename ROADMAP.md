# Roadmap

Offene Arbeiten, geordnet nach Wirkung. Keine Termine; jede Position gilt erst als erledigt,
wenn Tests oder Messungen sie belegen.

## Telefonie und Latenz

- **Streaming-ASR-Adapter** mit Zwischenergebnissen. Ohne ihn wartet das Endpointing ohne
  Text, und schon die Simulation verfehlt das Latenzbudget ([docs/15](docs/15-latenz.md)).
- **Prüfung gegen die Genesys AudioHook Test Suite und eine Test-Org**, insbesondere für die
  Audio-Connector-Erweiterungen (`inputVariables`, `outputVariables`, `barge_in`, `dtmf`,
  `playback_*`).
- **SIP-Adapter** für Telefonanlagen ohne Genesys.
- **Lasttest über Telefonie** mit der geplanten Kanalzahl.

## Modelle und Qualität

- **Golden-Set aus Pilotdaten**: anonymisierte echte Gespräche, getrennt in Entwicklungs- und
  Holdout-Teil, mit einem echten Modell ausgewertet (`evals/run_evals.py --llm-url ...`).
- **Latenzmessung auf Zielhardware** mit `gw-contactcenter latenz-messen`, Ergebnis mit
  Methode, Hardware und Datum.
- **Modell als zweiter Prüfer** (`ModellPruefer`) auf dem Holdout-Set messen.
- **Erkennung von Namen und Adressen** (NER) als zusätzliche Pseudonymisierung.

## Betrieb

- Mehrere Backends (z. B. lokal und EU) über Umgebungsvariablen konfigurierbar.
- Histogramme statt Summaries für Metriken, damit Quantile über Instanzen aggregierbar sind.
- Wissensbasis mit Vektorsuche und Zugriffsrechten (z. B. Postgres mit pgvector) statt
  `DemoBackend`.
