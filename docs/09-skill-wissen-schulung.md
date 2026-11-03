# 09 Rollen und Kompetenzen

Ein Sprachagent verbindet Telefonie, Spracherkennung, Sprachmodelle, Datenschutz und Betrieb.
Selten deckt eine Person alles ab. Die Tabelle zeigt, welche Kompetenz für welchen Teil dieses
Repos nötig ist.

## Rollen

| Rolle | Aufgabe | Berührt im Repo |
|---|---|---|
| Fachverantwortung Kundendienst | Anliegen-Katalog, Risikoklassen, Übergabeziele, Golden-Set | `policy.py`, `evals/faelle.json` |
| Genesys-Administration | Audio Connector, Architect-Flow, Queues, Test-Org | `audiohook.py`, [12](12-zielarchitektur.md) |
| Entwicklung | Werkzeuge an CRM/ERP, Adapter, Tests | `tools.py`, `adapters/`, `tests/` |
| ML- und Plattformbetrieb | vLLM, ASR, TTS, GPU, Modellwechsel, Lasttest | `deploy/`, `sizing.py`, `latenzmessung.py` |
| Betrieb | Überwachung, Störungen, Schlüsselverwaltung, Audit-Prüfung | [10](10-betrieb-ops.md) |
| Datenschutz und Recht | Aufzeichnung, Verarbeitungsorte, Aufbewahrung, Information der Anrufenden | [06](06-daten-speicherung.md) |

## Wissen, das oft fehlt

- **Warteschlangen**: Unterschied zwischen Personalbedarf (Erlang-C) und Kanalzahl (Erlang-B);
  Spitzenstunde statt Tagesdurchschnitt ([05](05-skalierung.md)).
- **Latenz**: Was zur Zeit bis zum ersten Ton gehört, warum Endpointing dominiert, wie man
  misst statt schätzt ([15](15-latenz.md)).
- **Messen von Modellen**: Golden-Set, Holdout, Fehlalarme und verpasste Fälle getrennt
  berichten ([16](16-leitplanken.md)).
- **GPU-Dimensionierung**: Gewichte, KV-Cache, Speicherbandbreite; warum «passt in den
  Speicher» nicht «schnell genug» heisst ([11](11-performance-architektur.md)).
- **Prompt-Injection**: Regex-Filter sind eine erste Schicht, kein Schutz; wirksam sind
  Berechtigungen, Bestätigungen und Übergabe ([16](16-leitplanken.md)).

## Grenzen

- Keine Aussage zu Teamgrösse oder Pensen; das hängt von Volumen, Öffnungszeiten und
  Betriebsmodell ab.

## Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Wer verantwortet den Anliegen-Katalog fachlich? | Ohne diese Rolle entscheidet die Technik über Risiken. |
| 2 | Wer kann vLLM, ASR und TTS betreiben und aktualisieren? | Selbst betriebene Modelle brauchen laufende Pflege. |
| 3 | Wer prüft das Audit und reagiert auf Auffälligkeiten? | Ein Audit, das niemand liest, schützt nicht. |
