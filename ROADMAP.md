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
- **Lasttest über Telefonie** mit der geplanten Kanalzahl. Das Ergebnis ist nur mit einer
  Backpressure-Warteschlange (siehe "Betrieb") verifizierbar: ohne Fristen-Queue ist ein
  503-Ausfall nicht von einer Warteschlange unterscheidbar.
- **Erlang-Stress-Test** (siehe "Betrieb"): Angebotslast `lambda` und AHT in einem
  Szenario erhöhen, um Rukaufwellen nach Stoerungen zu modellieren
  ([docs/05-skalierung.md](docs/05-skalierung.md)).

## Modelle und Qualität

- **Golden-Set aus Pilotdaten**: anonymisierte echte Gespräche, getrennt in Entwicklungs- und
  Holdout-Teil, mit einem echten Modell ausgewertet (`evals/run_evals.py --llm-url ...`).
- **Latenzmessung auf Zielhardware** mit `gw-contactcenter latenz-messen`, Ergebnis mit
  Methode, Hardware und Datum.
- **Modell als zweiter Prüfer** (`ModellPruefer`) auf dem Holdout-Set messen.
- **Erkennung von Namen und Adressen** (NER) als zusätzliche Pseudonymisierung.

## Betrieb

- **Backpressure-Warteschlange** statt harten 503: Anrufe, die ueber `max_sitzungen`
  hinauskommen, gehen in eine Frist-Queue mit `SLA`-Zeit statt abgelehnt zu werden
  ([BOTTLENECKS.md](BOTTLENECKS.md) 2.2). Standardmaessig ist der Cap aus
  (`max_sitzungen=0`, `server.py:79`), so dass ohne diese Massnahme jede Last ungebrochen
  in den Prozess wachst.
- **Gemeinsamer Sitzungszaehler** ueber mehrere Prozesse (z. B. Redis oder eine
  gemeinsame Datenstruktur). `AKTIV` ist prozesslokal (`server.py:61`); ohne einen
  ueber Instanzen geteilten Zaehler ist `max_sitzungen` nur pro Instanz korrekt
  ([BOTTLENECKS.md](BOTTLENECKS.md) 2.2, 6).
- **Externer Audit-Anker** ([BOTTLENECKS.md](BOTTLENECKS.md) 2.7):
  `AuditLog.letzter_pruefwert` (audit.py:127) liefert den Pruefwert *fuer* einen
  externen Anker; es fehlt der Export in einen WORM-Speicher oder eine
  Hash-Registry. Ohne ihn ist jede Instanz ein geschlossenes Audit-System.
- **Skill-Based Routing und Schichtplanung** ([BOTTLENECKS.md](BOTTLENECKS.md) 2.8):
  je Skill eine eigene Warteschlange mit eigenen Agenten, pro Stunde eine eigene
  Erlang-C-Rechnung. Erlang-C (Poisson + exponentiell) ist ein M/M/n-Ansatz; fuer
  Rukaufwellen, Schichten und Skill-Vielfalt ist eine Erweiterung noetig.
- Mehrere Backends (z. B. lokal und EU) über Umgebungsvariablen konfigurierbar.
- Histogramme statt Summaries für Metriken, damit Quantile über Instanzen aggregierbar sind.
- Wissensbasis mit Vektorsuche und Zugriffsrechten (z. B. Postgres mit pgvector) statt
  `DemoBackend`.
