# 10 Betrieb: Start, Überwachung, Störungen

## Start nur mit vollständiger Konfiguration

`gw-contactcenter server` startet standardmässig im Modus `produktion` und verweigert den
Start, solange eine dieser Bedingungen fehlt (`ServerKonfig.maengel()`):

- `AUDIOHOOK_API_KEY` und `AUDIOHOOK_CLIENT_SECRET` (Base64) für die Signaturprüfung,
- `AUDIT_HMAC_KEY` mit mindestens 32 Byte und `AUDIT_DIR` für ein persistentes Audit,
- `LLM_URL`, `ASR_URL`, `TTS_URL` sowie `TTS_MODELL` und `TTS_STIMME`.

Der Modus `entwicklung` (`GW_MODUS=entwicklung`) erlaubt Mock-Adapter, ein Audit ohne HMAC und
Verbindungen ohne Signatur, aber nie ohne API-Key. Geheimnisse liest der Server nur aus der
Umgebung, nie von der Kommandozeile. Das Compose-Beispiel bricht schon beim Start ab, wenn
eine Pflichtvariable fehlt (`${VAR:?}`).

## Endpunkte

| Endpunkt | Inhalt |
|---|---|
| `ws://HOST:PORT` | Audio Connector (WebSocket); im Compose-Beispiel hinter Caddy mit TLS |
| `http://HOST:METRICS_PORT/healthz` | `ok`, sobald der Prozess läuft |
| `http://HOST:METRICS_PORT/metrics` | Prometheus-Textformat |

Caddy leitet `/metrics` und `/healthz` nicht nach aussen weiter; Prometheus liest sie im
internen Netz.

## Metriken

Pro Stufe eine Summary `gw_contactcenter_<stufe>_ms` mit p50, p95, p99, `_sum` und `_count`:
`endpointing`, `asr`, `dialog`, `llm_ttft`, `erster_satz`, `tts_erstes_audio`,
`zeit_bis_erster_ton`, `barge_in`. Dazu `gw_contactcenter_aktive_sitzungen`. Die Quantile
beziehen sich auf die letzten 20'000 Messpunkte des Prozesses, nicht auf ein Zeitfenster.
vLLM liefert eigene Metriken (Warteschlange, KV-Cache-Belegung, TTFT); das Compose-Beispiel
liest beide.

Sinnvolle Alarme, als Ausgangspunkt:

| Signal | Bedeutung |
|---|---|
| p95 `zeit_bis_erster_ton` über dem Zielwert | Modell, ASR oder TTS zu langsam, oder GPU ausgelastet |
| `aktive_sitzungen` nahe `MAX_SITZUNGEN` | Kanäle reichen nicht; Genesys erhält 503 |
| vLLM-Warteschlange > 0 über längere Zeit | GPU ausgelastet ([11](11-performance-architektur.md)) |
| Anteil `notfall_uebergabe` im Audit steigt | ein Dienst fällt aus oder läuft in Zeitüberschreitungen |

## Verhalten bei Störungen

| Störung | Verhalten im Code | Nachweis |
|---|---|---|
| Ungültige oder fehlende Signatur | HTTP 401 nach 0.5 s Verzögerung, keine Sitzung | `tests/test_server.py` |
| Zu viele gleichzeitige Anrufe (`MAX_SITZUNGEN`) | HTTP 503; der Architect-Flow muss in die normale Queue weiterleiten | `tests/test_server.py` |
| LLM-Fehler oder Zeitüberschreitung | Wiederholung mit Backoff, dann nächstes zulässiges Backend; nach 3 Fehlern in Folge ist ein Backend für alle Anrufe des Prozesses 30 s gesperrt | `tests/test_gateway.py`, `tests/test_adapter_http.py`, `tests/test_server.py` |
| Kein zulässiges Backend, Token-Budget erschöpft | Ansage und Übergabe an einen Menschen, Audit `notfall_uebergabe` | `tests/test_gateway.py`, `tests/test_pipeline_sim.py` |
| ASR- oder TTS-Fehler | Übergabe statt Auflegen; ohne TTS ohne Ansage (`notfall_ohne_ansage`) | `tests/test_pipeline_sim.py` |
| Antwortfilter schlägt an | Ersatzsatz statt der Modellantwort, Audit mit Befund | `tests/test_gateway.py` |

## Audit prüfen

Jede Sitzung schreibt `AUDIT_DIR/<session-id>.jsonl`. Prüfen:

```text
AUDIT_HMAC_KEY=... gw-contactcenter audit-pruefen /pfad/zur/sitzung.jsonl
```

Rückgabewert 0: Kette intakt, die Ausgabe nennt den letzten Prüfwert. 1: Kette ab einem
bestimmten Eintrag verletzt. 2: Datei fehlt, Schlüssel falsch oder Format ungültig. Den letzten
Prüfwert regelmässig an einen getrennten Ort exportieren (z. B. Ticketsystem oder WORM-Speicher):
Nur so wird auch das Abschneiden am Ende der Datei erkennbar.

Den HMAC-Schlüssel wie ein Passwort verwalten. Wer ihn kennt, kann eine gültige Kette neu
schreiben.

## Grenzen

- Circuit Breaker und Tageskosten gelten pro Prozess (`server.Geteilt`), nicht über mehrere
  Instanzen hinweg; bei mehreren Instanzen hat jede ihren eigenen Zähler.
- Kein Runbook für Hardware, Backups oder Notfallwiederherstellung; das hängt von der
  Plattform des Betreibers ab.
- Die Metriken sind Summaries ohne Histogramm; Quantile lassen sich über mehrere Instanzen
  nicht korrekt mitteln.

## Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Wer hat Bereitschaft in den Öffnungszeiten des Kundendiensts? | Ein ausgefallener Agent leitet alles an Menschen weiter; die müssen dann verfügbar sein. |
| 2 | Wo liegen die Schlüssel (API-Key, Client-Secret, HMAC), und wer rotiert sie? | Schlüssel im Klartext auf dem Host sind ein häufiger Schwachpunkt. |
| 3 | Wohin wird der letzte Audit-Prüfwert exportiert? | Ohne externen Anker bleibt Abschneiden unentdeckt. |
