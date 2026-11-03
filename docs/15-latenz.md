# 15 Latenz: Budget, Simulation, Messung

Gemeint ist die Zeit vom Ende der Kundenäusserung bis zum ersten hörbaren Ton des Bots. Im
Repo gibt es drei Arten von Zahlen dazu. Sie beantworten verschiedene Fragen und dürfen nicht
vermischt werden.

## Budget (Annahme)

`ZIELBUDGET_MS` in `gw_contactcenter/telemetry.py`. Die Werte sind Zielvorgaben, keine
Messungen:

| Stufe | Ziel p50 | Was dazugehört |
|---|---|---|
| `endpointing` | 400 ms | Stille, bis das Turn-Taking die Äusserung für fertig hält |
| `asr` | 200 ms | Transkription der Äusserung |
| `llm_ttft` | 250 ms | erstes Token des Modells |
| `erster_satz` | 250 ms | vom ersten Token bis zum ersten vollständigen Satz |
| `tts_erstes_audio` | 200 ms | Sprachausgabe des ersten Satzes bis zum ersten Ton |
| `zeit_bis_erster_ton` | 1000 ms | alles zusammen, vom Sprechende bis zum ersten Ton |

Die Stufen addieren sich nicht genau zum Gesamtwert, weil nicht jeder Wechsel jede Stufe
durchläuft (Vorlagenantworten brauchen kein `llm_ttft`). Netzlaufzeit zu Genesys und
Puffer der Telefonie sind nicht enthalten.

## Simulation

`gw-contactcenter sim` spielt einen Anruf mit vier Kundenäusserungen Frame für Frame durch die
ganze Pipeline, auf einer simulierten Uhr. Der Algorithmus bestimmt nur das Endpointing; ASR,
LLM und TTS sind Mock-Komponenten mit fest eingestellten Verzögerungen
(`MOCK_LATENZ_MS` in `adapters/mock.py`). Die Simulation zeigt, wie sich die Stufen
zusammensetzen und ob die Orchestrierung unnötige Wartezeit einbaut. Über die Geschwindigkeit
echter Modelle sagt sie nichts.

<!-- ausgabe: gw-contactcenter sim -->
```text
Simulierter Anruf (SIMULATION, keine Messung)
  Bot 1: Gerne schaue ich nach. Wie lautet Ihre sechsstellige Bestellnummer?
  Bot 2: Ihre Bestellung 447100 ist unterwegs. Die Zustellung erfolgt morgen bis 12 Uhr.
  Bot 3: Soll ich ein Ticket zum Thema Reklamation, beschädigtes Produkt erfassen? Bitte sagen Sie Ja oder Nein.
  Bot 4: Ich habe das Ticket T-00001 erfasst und verbinde Sie jetzt mit Fachbereich (2. Level).
  Werkzeuge: ['bestellstatus_abfragen', 'ticket_erstellen']
  Übergabe:  {"ziel": "Fachbereich (2. Level)", "anliegen": "reklamation", "sla_tage": 3.0, "grund": "R2: aufnehmen und übergeben", "ticket": "T-00001", "zusammenfassung": "Die Nummer ist 447100. | Danke, das war alles. Ich möchte noch eine Reklamation machen, das Paket war beschädigt. | Ja, bitte."}
  Audit-Kette intakt: True (10 Einträge)
  Latenz in ms auf simulierter Uhr (Ziel = Budgetannahme):
    endpointing          p50    360  p95    360  Ziel   400  algorithmisch, Mock-ASR mit Zwischenergebnissen
    asr                  p50    120  p95    120  Ziel   200  Mock, fest 120 ms
    llm_ttft             p50    110  p95    110  Ziel   250  Mock, fest 110 ms
    erster_satz          p50    105  p95    105  Ziel   250  Mock, 15 ms pro Token
    tts_erstes_audio     p50    120  p95    120  Ziel   200  Mock, fest 120 ms
    zeit_bis_erster_ton  p50    690  p95    873  Ziel  1000  Summe der Stufen in der Simulation
  Derselbe Anruf ohne Zwischenergebnisse der ASR (wie der HTTP-Adapter im Server):
    endpointing          p50    760  p95    760  Ziel   400
    zeit_bis_erster_ton  p50   1090  p95   1273  Ziel  1000
  Echte Werte nur mit Messung auf Zielhardware: gw-contactcenter latenz-messen
```

Lesart:

- Nur einer der vier Wechsel erzeugt eine Antwort mit dem Modell (der Bestellstatus); die
  anderen sind Vorlagen. Deshalb liegt p95 über p50.
- **Das Budget hängt an einer Streaming-Spracherkennung.** Die erste Tabelle unterstellt,
  dass die ASR während des Sprechens Zwischenergebnisse liefert; dann erkennt das
  Endpointing einen fertigen Satz nach 300 ms Stille. Der mitgelieferte HTTP-Adapter liefert
  keine Zwischenergebnisse. Ohne Text wartet das Endpointing 700 ms (Mitte zwischen den
  Wartezeiten für fertige und unfertige Sätze, `TurnConfig`), und schon die Simulation liegt
  über dem Budget. Ein Streaming-ASR-Adapter steht in der [ROADMAP](../ROADMAP.md).
- Die Mock-Verzögerungen (120 ms ASR, 110 ms erstes Token, 15 ms pro Token, 120 ms TTS) sind
  Annahmen in der Grössenordnung einer lokalen GPU. Echte Werte können deutlich darüber
  liegen.

Die Evals (`python evals/run_evals.py`) prüfen die simulierte Zeit bis zum ersten Ton mit
Zwischenergebnissen gegen das Budget und berichten den Wert ohne Zwischenergebnisse zur
Information.

## Messung

`gw-contactcenter latenz-messen` misst echte, OpenAI-kompatible Endpunkte aus Sicht des
Clients (`time.perf_counter`):

| Kennzahl | Wie gemessen |
|---|---|
| `llm_ttft_ms` | Streaming-Request an `/chat/completions` bis zum ersten Inhalts-Token |
| `llm_erster_satz_ms` | bis zum ersten vollständigen Satz, geschnitten wie im Gateway |
| `llm_tokens_pro_s` | Dekodierrate aus `usage.completion_tokens`, falls der Server sie liefert |
| `asr_ms` | eine Äusserung (WAV) an `/audio/transcriptions` |
| `tts_erstes_byte_ms`, `tts_gesamt_ms` | ein typischer erster Satz an `/audio/speech` |
| `kette_bis_erstes_audio_ms` | ASR, dann LLM bis zum ersten Satz, dann TTS bis zum ersten Byte, im selben Durchlauf |

Methode: Aufwärmdurchläufe werden verworfen, dann eine feste Zahl Wiederholungen pro Worker;
mit `--parallel` laufen mehrere Worker gleichzeitig (Messung unter Last). Der Bericht nennt
n, Minimum, p50, p90, p95, p99 und Maximum je Kennzahl, Datum (UTC), Endpunkte ohne
Zugangsdaten, Client-Umgebung und die Hardware-Beschreibung, die beim Aufruf angegeben wird.
Fehlgeschlagene Durchläufe werden gezählt und aufgeführt, nicht verschwiegen.

Beispielaufruf im Compose-Beispiel, aus dem Agent-Container heraus (vLLM und speaches sind nur
im internen Netz erreichbar; Modell, Stimme und Hardware sind Platzhalter):

```text
docker compose -f deploy/compose.yaml --env-file deploy/.env exec agent \
  gw-contactcenter latenz-messen \
  --llm-url http://vllm:8000/v1 --llm-modell Qwen/Qwen3-8B-AWQ \
  --llm-extra-json '{"chat_template_kwargs": {"enable_thinking": false}}' \
  --asr-url http://speaches:8000/v1 \
  --tts-url http://speaches:8000/v1 --tts-modell MODELL --tts-stimme STIMME \
  --wiederholungen 30 --parallel 5 \
  --hardware "1x GPU-TYP, vLLM 0.32.0, Qwen3-8B-AWQ" --json /tmp/latenz-bericht.json
```

`/tmp` ist im Container flüchtig; den Bericht danach mit `docker compose ... cp
agent:/tmp/latenz-bericht.json .` sichern. Für `--parallel` die Zahl gleichzeitig aktiver Anfragen aus [11](11-performance-architektur.md)
verwenden. Zur Zeit bis zum ersten Ton im Betrieb kommen Endpointing, Netz zu Genesys und
Wiedergabepuffer hinzu; im Betrieb liefert `/metrics` dieselben Spans wie die Simulation
([10](10-betrieb-ops.md)).

**Das Repo enthält keine Messergebnisse.** Sie hängen von Hardware, Modell, Kontextlänge und
Last ab und entstehen erst in der Zielumgebung.

## Grenzen

- Die Messung sieht nur die Client-Seite; die Server-GPU kennt sie nur aus der Angabe
  `--hardware`.
- Kein Lasttest über Telefonie (Genesys) im Repo.
- Die synthetische Standard-WAV ist ein Ton, keine Sprache; für die ASR eine eigene Aufnahme
  mit `--wav` übergeben.

## Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Gibt es eine Streaming-ASR mit Zwischenergebnissen für Deutsch und Schweizerdeutsch? | Ohne sie liegt schon die Simulation über dem Budget. |
| 2 | Welche Zeit bis zum ersten Ton akzeptiert die Kundschaft? | Das Budget von 1000 ms ist eine Annahme. |
| 3 | Wie verhält sich die Latenz bei der geplanten Zahl gleichzeitiger Gespräche? | Messen mit `--parallel`, nicht nur einzeln. |
