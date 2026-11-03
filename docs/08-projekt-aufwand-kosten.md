# 08 Vorgehen und Aufwandstreiber

Das Dossier nennt keine Preise und keine Personentage: Beides hängt von der Ausgangslage ab
und wäre ohne konkrete Organisation nicht belegbar. Es beschreibt, in welcher Reihenfolge ein
Pilot sinnvoll ist, woran jede Phase abgenommen wird und was den Aufwand treibt.

## Phasen mit prüfbarer Abnahme

| Phase | Ergebnis | Abnahme |
|---|---|---|
| 1 Daten | Anliegen-Katalog mit Häufigkeiten, Spitzenstundenvolumen, AHT aus der ACD | Kapazitätsrechner mit echten Werten statt Annahmen ([05](05-skalierung.md)) |
| 2 Golden-Set | anonymisierte Testgespräche mit erwarteter Aktion, getrennt in Entwicklungs- und Holdout-Teil | Holdout ist vor dem ersten Tuning eingefroren |
| 3 Laborbetrieb | Agent gegen echte Modelle, ohne Genesys | `evals/run_evals.py --llm-url ...` besteht das Gate; Latenzmessung auf der Zielhardware ([15](15-latenz.md)) |
| 4 Genesys-Test | Audio Connector in einer Test-Org, interne Testanrufe | Signaturprüfung, Übergabe mit `outputVariables`, Lasttest mit der geplanten Kanalzahl |
| 5 Pilot | kleiner Anteil echter Anrufe, ein Anliegen | vorher festgelegte Kennzahlen: Übergabequote, Fehlklassifikationen, Zeit bis zum ersten Ton, Beschwerden |
| 6 Ausbau | weitere Anliegen | jedes neue Anliegen durchläuft Phase 2 bis 5 |

Ein Anliegen mit niedrigem Risiko (R0/R1) und hohem Volumen ist der übliche Einstieg. Mehrere
Anliegen gleichzeitig einzuführen erschwert es, Fehler einer Ursache zuzuordnen.

## Was den Aufwand treibt

- **Datenlage**: Ohne Aufnahmen und ACD-Auswertungen bleiben Kapazität, ASR-Qualität und
  Anliegen-Katalog Annahmen. Die rechtliche Klärung für Testaufnahmen dauert oft länger als
  die Technik.
- **Schweizerdeutsch**: Ein Standard-ASR-Modell kann reichen oder nicht; das zeigt erst die
  Messung auf eigenen Aufnahmen ([03](03-asr-transkribation.md)).
- **Anbindung an Fachsysteme**: Bestellstatus, Tickets und Konten liegen in CRM oder ERP.
  Jedes Werkzeug braucht eine Schnittstelle, Berechtigungen und Tests. Im Repo ist das ein
  `DemoBackend`.
- **Hardware**: eigene GPU, gemietete GPU oder gehostetes Modell je Datenklasse
  ([06](06-daten-speicherung.md), [11](11-performance-architektur.md)).
- **Betrieb**: Bereitschaft, Überwachung, Modellwechsel und Audit-Auswertung sind laufender
  Aufwand, nicht einmaliger ([10](10-betrieb-ops.md)).
- **Genesys-Konfiguration**: Integration, Architect-Flow, Queues und Tests in der Test-Org.

## Grenzen

- Keine Aufwands- oder Kostenschätzung; die Punkte oben sind die Fragen, die eine Schätzung
  beantworten muss.
- Kein Rechtsrat zu revDSG, Aufzeichnung oder Branchenregeln.

## Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Welches eine Anliegen kommt zuerst? | Bestimmt Golden-Set, Werkzeuge und Pilot. |
| 2 | Wer nimmt jede Phase ab, und mit welchen Zahlen? | Ohne vorher festgelegte Kennzahlen ist ein Pilot nicht auswertbar. |
| 3 | Wer betreibt das System nach dem Pilot? | Ohne Betrieb kein produktiver Einsatz. |
