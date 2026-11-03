# Bottlenecks und Skalierung

Stand: Branch `feat/qualitaet-und-bottlenecks`, basierend auf `feat/qualitaet` (Commit
`75e2a9f`), Paket `gw_contactcenter` Version 2.1.0. Alles Lauffaehige lauft offline
gegen Mocks; die Tests bestehen (386, Abdeckung ueber 90 %), Lint, Typen, Evals und
Container sind gruen.

Dieses Dossier faengt die offenen Flaschenhaelse des Projekts ein und ordnet die
Schwierigkeiten bei der Skalierung von wenigen auf viele gleichzeitige Anrufe. Es
ergaenzt [`docs/05-skalierung.md`](docs/05-skalierung.md) (Erlang-Modell),
[`docs/11-performance-architektur.md`](docs/11-performance-architektur.md) (GPU) und
[`docs/15-latenz.md`](docs/15-latenz.md) (Latenz). Es enthaelt keine neuen Messungen;
alle Zahlen sind Annahmen, Simulationen oder Auszug aus dem Code, und jede Aussage
zeigt auf die Stelle, die sie tragt.

## Wie dieses Dossier zu lesen ist

Jede Position hat drei Teile:

1. **Wo** -- die konkrete Stelle im Code (Modul und Funktion), die den Engpass erzeugt.
2. **Warum** -- der Mechanismus, der bei wachsender Last zum Problem wird.
3. **Hebel** -- der Massnahme im Code oder in der [ROADMAP](ROADMAP.md), die ihn loest.

Die Reihenfolge am Ende sortiert nach Wirkung, nicht nach Prioritaet in der ROADMAP.

## 1. Kausalkette eines Anrufs: wo die Zeit entsteht

Ein Anruf lauft als Kette durch `gw_contactcenter/pipeline.py:Pipeline`:

```text
Frame -> VAD -> Turn-Taking -> ASR -> Dialog -> Gateway (satzweise)
      -> TTS -> Wiedergabeplan -> Audio
```

Die Zeit bis zum ersten Ton ist die Summe dieser Stufen (Budget in
`gw_contactcenter/telemetry.py:ZIELBUDGET_MS`, Ziel 1000 ms insgesamt). Der Engpass
ist nicht eine Stufe, sondern deren Abhaengigkeit: jede Stufe wartet auf die
vorherige, ausser durch Streaming ueberlappt. Drei Techniken in `Pipeline` halten
die Kette kurz -- semantisches Endpointing, Satz-Streaming, Barge-in -- aber nur,
wenn die ASR Zwischenergebnisse liefert. Das ist der zentrale Punkt unten.

## 2. Flaschenhaelse, geordnet nach Wirkung

### 2.1 Fehlender Streaming-ASR-Adapter (hoechste Wirkung)

**Wo:** `gw_contactcenter/adapters/openai_compat.py` liefert nur das fertige
Transkript; `gw_contactcenter/turn.py:TurnTaking` erwartet Zwischenergebnisse
(`ASR.teiltext`). In `docs/15-latenz.md` ist nachzuerhalten: ohne Text wartet das
Endpointing 700 ms, und die Simulation (`gw-contactcenter sim`) liegt bei 1090 ms
ueber dem Budget von 1000 ms -- mit Zwischenergebnisse liegt sie bei 690 ms drunter.

**Warum:** Ohne Streaming-ASR muss das System bis zum Ende der
Kundenaeusserung warten, bevor es ueberhaupt zu schliessen beginnt, dass die
Aeusserung fertig ist.
Die 400 ms Endpointing im Budget setzen voraus, dass waehrend des Sprechens schon
Text einlaeuft. Das ist der einzige Grund, warum die Simulation das Budget ohne
Zwischenergebnisse verausgibt. Kein anderer Engpass im Code kann das loesen.

**Hebel:** Streaming-ASR-Adapter mit Zwischenergebnissen, erste Position in der
[ROADMAP](ROADMAP.md). Ohne ihn bleibt die Zeit bis zum ersten Ton ueber dem Ziel
und der Agent wirkt langsam -- unabhaengig von allen anderen Massnahmen.

### 2.2 Ein Thread pro Anruf, optionales hartes Session-Limit ohne Puffer

**Wo:** `gw_contactcenter/server.py:sitzung` erzeugt pro Anruf einen
`ThreadPoolExecutor(max_workers=1)` (Zeile 284). `gw_contactcenter/server.py:bedienen`
haelt den Zaehler `AKTIV["sitzungen"]` (Zeile 61, 283) und lehnt ab, sobald
`max_sitzungen` erreicht ist -- aber nur im `zugang`-Handler, noch vor Erzeugung
des Sitzungsthreads, mit HTTP 503 "ausgelastet" (Zeile 434, 438).
`MIN_FEHLER_VERZOGERUNG_S` (0.5 s, Zeile 62) verschiebt den Abweisungszweigs
gegen Timing-Seitenkanale; der 503-Pfad selbst wartet nicht auf ihn.
`max_sitzungen` ist standardmaessig **0** (`ServerKonfig.max_sitzungen`, Zeile 79;
CLI `--max-sitzungen`, Zeile 151), und die Pruefung liest
`if k.max_sitzungen and ...` (Zeile 434): **der Cap ist per Default aus** und greift
nur, wenn er explizit auf >0 gesetzt wird. Ohne ihn wachst die Last ungebremst
in den Prozess.

**Warum:** Das Modell ist bewusst einfach: jeder Anruf bekommt einen eigenen Thread,
damit ein langsamer Modellaufruf weder andere Anrufe noch Pings haelt. Das ist
korrekt fuer Einzellaeufer, aber es skaliert linear mit dem Prozess: ein Prozess
mit N gleichzeitigen Anrufen braucht N Threads. Wenn der Cap aktiv ist, ist
`max_sitzungen` ein Abbruch, keine Warteschlange: ein abgewiesener Anruf bekommt
einen verzoeigerten 503 -- er wartet nicht. Ist der Cap aus (Standard), fehlt jede
Grenze und der Prozess waechst, bis das Betriebssystem oder der Thread-Pool
bricht.

**Hebel:**
- Das `Geteilt`-Objekt (Backends und Kostenzaehler, `server.py:Geteilt`,
  Zeile 226) wird in `bedienen` einmal erzeugt (Zeile 431) und jeder akzeptierten
  Sitzung uebergeben (`sitzung(ws, k, geteilt)`, Zeile 441); die Backends sind
  geteilt. Das ist schon die richtige Richtung fuer Horizontalite. Was fehlt, ist
  eine **Backpressure-Warteschlange** statt des 503: Anrufe, die ueber
  `max_sitzungen` hinauskommen, sollten in eine Warteschlange mit Frist gehen,
  nicht abgelehnt werden.
- Fuer Horizontalite ueber mehrere Prozesse hinweg fehlt ein gemeinsamer
  Sitzungszaehler; `AKTIV` ist prozesslokal (Zeile 61).
- Diese Punkte stehen in der [ROADMAP](ROADMAP.md) unter "Betrieb".

### 2.3 In-Memory Wissensbasis statt Vektorsuche

**Wo:** `gw_contactcenter/tools.py:DemoBackend` ist ein In-Memory-Shop/CRM mit
erfundenen Daten; `wissen_suchen` durchlaeuft ein kleines Dictionary linear.
`standard_werkzeuge` registriert `wissen_suchen` als Werkzeug.

**Warum:** Der lineare Scan skaliert nicht ueber grosse Wissensbasis, und eine
In-Memory-Datenstruktur ist prozesslokal. Zwei Prozesse haben zwei getrennte
Wissensbestaende. Fuer einen echten Betrieb braucht es eine gemeinsame Wissensbasis
mit Zugriffsrechten und Vektorsuche.

**Hebel:** Vektorsuche mit Zugriffsrechten (z. B. Postgres mit pgvector) statt
`DemoBackend`, Position in der [ROADMAP](ROADMAP.md) unter "Betrieb". Bis dahin
ist `DemoBackend` die Grenze, an der die Qualitaet der Antworten bricht, sobald
das Wissen aus der Demo waechst.

### 2.4 Telemetrie prozesslokal, keine Instanz-Aggregation

**Wo:** `gw_contactcenter/telemetry.py:MESSUNG` ist eine Modul-Instanz, die alle
Sessions eines Prozesses bedient. Der Prometheus-Ausgabe in
`gw_contactcenter/server.py:_Metriken` haengt `gw_contactcenter_aktive_sitzungen`
an, und die Spans sind eine in-Memory-Ring-Buffer mit `maxlen`.

**Warum:** Quantile ueber mehrere Instanzen hinweg koennen nicht aggregiert
werden, weil die Messungen pro Prozess in einem Ring-Buffer leben und bei jedem
Neustart verloren gehen. Fuer die Betriebsoptimierung (welche Stufe ist auf welcher
Instanz der Engpass?) fehlt ein gemeinsames Histogramm, das ueber Instanzen
aggregiert.

**Hebel:** Histogramme statt Summaries, damit Quantile ueber Instanzen aggregierbar
sind. Steht in der [ROADMAP](ROADMAP.md) unter "Betrieb".

### 2.5 Einzel-Backend pro Region, Failover nur ueber Circuit Breaker

**Wo:** `gw_contactcenter/server.py:ServerKonfig` traegt ein `llm_url`, `asr_url`,
`tts_url`. `gw_contactcenter/gateway.py:LLMGateway` faehrt mehrere Backends mit
einem `CircuitBreaker` (Schwelle 3 Fehler in Folge, 30 s gekuehlt) und einem
prozedessweiten `Kostenzaehler` (Tageslimit pro Kalendertag).

**Warum:** Der Circuit Breaker schuetzt vor einem ausgefallenen Backend, aber es
gibt nur ein Backend pro Region. Fuer die Skalierung ueber mehrere Regionen (z. B.
lokal plus EU) braucht es eine Konfiguration, die mehrere Backends pro Datenklasse
auswaehlt und nach Kosten und Auslastung schaltet. Das ist nicht nur ein
Betriebs-Problem, sondern ein Kostenproblem: der `Kostenzaehler` ist prozessweit,
und ein Tageslimit von 50 (in der Waehrung der `preis_pro_1k_token`-Angaben)
gilt fuer alle Anrufe des Prozesses.

**Hebel:** Mehrere Backends pro Region ueber Umgebungsvariablen konfigurierbar,
Position in der [ROADMAP](ROADMAP.md) unter "Betrieb".

### 2.6 GPU: Speicherbandbreite und KV-Cache, nicht Rechenleistung

**Wo:** `gw_contactcenter/sizing.py`. Die Dekodierrate pro Strom ist nach der
Roofline begrenzt durch `Bandbreite / (Gewichte + aktive Anfragen x Kontext x
KV pro Token)`. `MIN_TOKENS_S` in derselben Datei leitet die notwendige Rate aus
dem Latenzbudget ab (10 Token in 250 ms, also 40 Token/s).

**Warum:** Das Beispiel in `docs/11-performance-architektur.md` zeigt: eine L4
reicht im Speicher, kommt aber nicht an die 40 Token/s, weil die Bandbreite nicht
reicht. Der Kontext ist der grosse Hebel: 2000 statt 6000 Token, KV-Cache in
FP8 und 70 % Effizienz bringen dieselbe L4 zurueck. Das bedeutet fuer die
Skalierung: die Anzahl gleichzeitiger Anrufe pro GPU faellt proportional zum
Kontext. Langer Kontext (System-Prompt, Verlauf, Wissen) kostet nicht nur
Speicher, sondern reduziert die gleichzeitige Kapitaet.

**Hebel:**
- Kontext sparsam halten; Prefix Caching aktivieren (`--enable-prefix-caching`
  im Compose, `--neue-token-pro-wechsel` in der CLI).
- Fuer 70 B-Modelle (siehe `docs/11-performance-architektur.md`) sind zwei H100
  mit INT4 oder vier H100 mit BF16 erforderlich.
- Die Rechnung unterstellt ideale Tensor-Parallelitaet; ein Lasttest auf
  Zielhardware ersetzt sie nicht (`gw-contactcenter latenz-messen`).

### 2.7 Audit-Kette pro Datei, kein externer Anker

**Wo:** `gw_contactcenter/audit.py:AuditLog` schreibt pro Anruf eine
`{sid}.jsonl`-Datei mit einer HMAC-Hash-Kette. Der Modul-Text nennt die Grenze:
"Die Kette erkennt Manipulation, sie verhindert sie nicht. Wer den
Schluessel besitzt, kann eine neue gueltige Kette erzeugen, und das Abschneiden
der letzten Eintraege faellt ohne externen Anker nicht auf."

**Warum:** Fuer eine Skalierung ueber mehrere Prozesse und Instanzen hinweg fehlt
ein gemeinsamer Anker (WORM-Speicher, regelmaessig exportierter letzter
Pruefwert). Ohne ihn bleibt jede Instanz ein geschlossenes Audit-System, und
ein Angriff auf die Dateisysteme einer Instanz ist nur fuer die andere
nachvollziehbar, wenn die letzte Instanz den letzten Pruefwert weiterleitet.

**Hebel:** Regelmassiger Export des letzten Pruefwerts in einen WORM-Speicher
oder eine externe Hash-Registry. Nicht in der aktuellen [ROADMAP](ROADMAP.md);
erweitern.

### 2.8 Erlang-Modell: Annahmen, die bei waechsender Last brechen

**Wo:** `gw_contactcenter/warteschlange.py` und `docs/05-skalierung.md`. Erlang-C
nimmt Poisson-Ankuenfte und exponential verteilte AHT an.

**Warum:** Rukaufwellen nach Werbeaktionen oder Stoerungen verletzen Poisson.
Eine konstante AHT ueber den Tag ist eine Annahme; Schichtplanung ist nicht
modelliert. Mehrere Skills oder Sprachen pro Agent (Skill-Based Routing) sind
nicht modelliert. Abwesenheiten (Pausen, Schulung, Krankheit) werden nur als
pauschaler Faktor `--abwesenheit` erfasst.

**Hebel:**
- Fuer Skill-Based Routing: je Skill eine eigene Warteschlange mit eigenen
  Agenten, nicht ein gemeinsamer Pool.
- Fuer Schichtplanung: den Tagesverlauf in Stundenstufen aufteilen, pro Stunde
  eine eigene Erlang-C-Rechnung.
- Fuer Rukaufwellen: ein Szenario mit erhoehtem lambda und erhohter AHT (z. B.
  nach einer Stoerung) als Stress-Test.

## 3. Schwierigkeiten bei der Skalierung (1 -> 10 -> 100 Anrufe)

Die Tabelle weist jedem Engpass den Massstab zu, an dem er auftritt.

| Massstab | Was passiert | Wo |
|---|---|---|
| 1 -> 10 | Streaming-ASR-Adapter fehlt, Simulation ueber Budget | 2.1 |
| 1 -> 10 | In-Memory Wissensbasis reicht nicht, `DemoBackend` bricht | 2.3 |
| 10 -> 50 | Thread pro Anruf, harte Ablehnung statt Warteschlange | 2.2 |
| 10 -> 50 | Einzel-Backend pro Region, Kostenzaehler prozessweit | 2.5 |
| 50 -> 100 | GPU: Kontext und KV-Cache begrenzen gleichzeitige Anrufe | 2.6 |
| 50 -> 100 | Telemetrie prozesslokal, keine Instanz-Aggregation | 2.4 |
| 100+ | Audit-Kette pro Datei, kein externer Anker | 2.7 |
| 100+ | Erlang-Modell: Skill-Based Routing, Schichtplanung | 2.8 |

## 4. Konkrete Hebel, geordnet nach Wirkung

Die Reihenfolge ist die, in der die Massnahmen den groessten Hebel haben. Jede
Position verweist auf die ROADMAP; die ROADMAP sortiert nach Wirkung, dieses
Dossier nach dem Massstab, an dem der Engpass auftritt.

1. **Streaming-ASR-Adapter** (2.1). Loest den Latenzengpass. Ohne ihn ist
   alles andere sekundar.
2. **Warteschlange statt harte Ablehnung** (2.2). Loest den
   Skalierungsengpass von 10 auf 50.
3. **Vektorsuche mit Zugriffsrechten** (2.3). Loest den
   Qualitaetsengpass, wenn das Wissen waechst.
4. **Mehrere Backends pro Region** (2.5). Loest den
   Betriebs- und Kostenengpass.
5. **Histogramme statt Summaries** (2.4). Loest den
   Messengpass fuer Betriebsoptimierung.
6. **Kontext sparsam halten** (2.6). Loest den
   GPU-Engpass; Prefix Caching und KV-Cache in FP8.
7. **Externer Audit-Anker** (2.7). Loest den
   Skalierungsengpass des Audits.
8. **Skill-Based Routing und Schichtplanung** (2.8). Loest den
   Modellengpass.

## 5. Was fehlt, um es zu skalieren

Diese Punkte sind nicht in der [ROADMAP](ROADMAP.md) und sollten dort
ergaenzt werden:

- **Backpressure-Warteschlange** mit Fristen fuer Anrufe ueber `max_sitzungen`.
- **Gemeinsamer Sitzungszaehler** ueber mehrere Prozesse.
- **Externer Audit-Anker** (WORM-Speicher, Hash-Registry).
- **Lasttest ueber Telefonie** mit der geplanten Kanalzahl (steht in der ROADMAP,
  aber ohne diesen Punkt kann kein Ergebnis verifiziert werden).
- **Mehrere Backends pro Region** ueber Umgebungsvariablen (steht in der ROADMAP,
  aber ohne die Warteschlange ist der Failover nicht nutzlich).

## 6. Kritische Hinterfrage der Skalierungsanalyse

Jede obige Position ist eine These; dieser Abschnitt prueft sie an den Voraussetzungen,
unter denen sie gilt, und benennt, was nicht bewiesen ist. Ziel: keine These uebernehmen,
die aus dem Code nicht folgt.

- **Zahlen sind Simulation, keine Messung.** Die Werte `690 ms` / `1090 ms`
  stammen aus `docs/15-latenz.md` (Simulationsausgabe, `gw-contactcenter sim`)
  mit festen Mock-Verzoegern (120 ms ASR, 110 ms erstes Token, 15 ms pro Token,
  120 ms TTS). Sie belegen die *Richtung* der Abhaengigkeit vom Streaming-ASR,
  nicht einen auf Zielhardware gemessenen Wert. Vor jeder Beschaffung muss die
  Simulation durch `gw-contactcenter latenz-messen` ersetzt werden.

- **"Ein Thread pro Anruf" ueberzeichnet den Engpass (2.2).** Der eigentliche
  Flaschenhals ist nicht der Thread selbst, sondern die LLM/ASR-Backend-Anbindung,
  die pro Thread blockiert. Ein Thread-Modell skaliert, solange die Backends
  skalieren; ohne gemeinsame Grenze (Cap per Default **aus**, `max_sitzungen=0`,
  `server.py:79/434`) waechst die Last ungebremst. Die Backpressure-Warteschlange
  loest daher zwei Probleme auf einmal: sie ersetzt den 503 *und* setzt eine
  Grenze, wo jetzt keine ist.

- **Gemeinsamer Sitzungszaehler ist kein Optimierungs-Vorschlag, sondern eine
  Voraussetzung fuer Horizontalite.** `AKTIV` ist prozesslokal (`server.py:61`);
  ohne einen ueber Prozesse geteilten Zaehler ist `max_sitzungen` nur pro Instanz
  korrekt und der 2.2-Hebel unvollstaendig. Der gemeinsame Zaehler gehoert daher
  zum selben Ticket wie die Backpressure-Warteschlange, nicht nebenbei.

- **Kostenzaehler teilt das Tageslimit.** `Kostenzaehler(tageslimit=50.0)`
  (`gateway.py:109`, Default 50.0; `LLMGateway` Default ebenfalls 50.0,
  Zeile 220) ist pro Gateway, aber wird in
  `LLMGateway` mit anderen Gateways geteilt (`gateway.py:225`). Das Tageslimit ist
  also kein pro-Backend-Budget, sondern ein gemeinsames; bei mehreren Backends pro
  Region muss klargestellt werden, ob je Region oder je Instanz gezaehlt wird.

- **Erlang-Modell ist ein M/M/n-Ansatz mit bekannten Grenzen (2.8).**
  Poisson-Ankuenfte und exponentiell verteilte AHT sind die Modellannahmen
  (`warteschlange.py`, arXiv:1307.2968, Garnett/Mandelbaum 2002). Sie sind fuer
  gleichmaessige Last brauchbar, brechen aber bei Rukaufwellen, Schichten und
  Skill-Based Routing. Das ist keine Kritik am Code, sondern an der
  Modellvalidierung: ein Stress-Test mit erhoehtem `lambda` und erhohter AHT ist
  noetig, bevor auf die Ergebnisse gebaut wird.

- **GPU-Sizing unterstellt ideale Tensor-Parallelitaet (2.6).** `sizing.py`
  teilt die KV-Koepfe (Zeile 299) und rechnet pro Strom `<= Bandbreite /
  (Gewichte + aktive x Kontext x KV/Token)`. Die Rechnung ist eine obere Grenze;
  reale Effizienz (Kommunikation, Graphen, Reserve 1.5 GiB pro Karte, Zeile 234)
  senkt sie. Der Lasttest auf Zielhardware ersetzt die Rechnung, er bestaetigt
  sie nicht.

- **`Geteilt`-Objekt ist pro `bedienen`-Aufruf, nicht global.** Es wird pro
  Server-Start erzeugt (`server.py:431`) und allen Sitzungen dieses Starts
  uebergeben (Zeile 441). Fuer Horizontalite ueber Prozesse bleibt es trotzdem
  prozesslokal; das ist korrekt fuer einen Prozess, aber es ist *nicht* der
  ueber Instanzen gemeinsame Zustand, den 2.2 fuer Horizontalite braucht.

- **Audit-Anker ist bereits angelegt, aber nicht aktiv (2.7).**
  `audit.py:letzter_pruefwert` (Zeile 127) liefert den Wert *fuer* einen externen
  Anker, es fehlt aber der Export (WORM/Registry). Die Skalierung des Audits ist
  also ein Betriebs-Problem, kein Code-Defizit; das muss in der ROADMAP als
  Betriebs-Punkt stehen.

Diese kritischen Nachfragen begruenden, warum die in Section 5 genannten
Betriebsmassnahmen nicht als lose To-Dos, sondern als zusammenhoerende Punkte in
der [ROADMAP](ROADMAP.md) stehen -- konkret: Backpressure-Warteschlange,
gemeinsamer Sitzungszaehler, externer Audit-Anker und Skill-Based Routing mit
Schichtplanung.

## 7. Offene Fragen (Entscheid-Checkpunkte)

| # | Frage | Warum |
|---|---|---|
| 1 | Welche Kanalzahl soll der Betrieb erreichen (10, 50, 100)? | Bestimmt die GPU-Anzahl und den Thread-Pool. |
| 2 | Wie laeng wird der Kontext pro Anruf (System-Prompt, Wissen, Verlauf)? | Bestimmt KV-Cache und gleichzeitige Anrufe pro GPU. |
| 3 | Wie viele Backends pro Region, und wie soll der Failover schalten? | Bestimmt den `Kostenzaehler` und die Kosten. |
| 4 | Welche Wissensbasis soll `DemoBackend` ersetzen, und welche Zugriffsrechte? | Bestimmt `wissen_suchen` und die Antwortqualitaet. |
| 5 | Wo liegt der externe Audit-Anker (WORM, Hash-Registry)? | Bestimmt die Skalierung des Audits. |
| 6 | Wie viele Skills oder Sprachen pro Agent? | Bestimmt, ob Skill-Based Routing notig ist. |
| 7 | Welche Schichtplanung (Stundenstufen, Pausen, Schulung)? | Bestimmt die Erlang-C-Rechnung ueber den Tag. |
| 8 | Welche Rukaufwellen sind zu erwarten (Werbung, Stoerung)? | Bestimmt den Stress-Test. |

## 8. Lesart

Dieses Dossier ist keine To-Do-Liste; es ist eine Analyse. Jede Position zeigt
auf die Stelle im Code, die den Engpass erzeugt, und auf den Hebel in der
ROADMAP, der ihn loest. Die Reihenfolge am Ende sortiert nach Wirkung, nicht nach
der Reihenfolge in der ROADMAP. Vor einer Beschaffung oder einem Lasttest soll
jede Position mit `gw-contactcenter latenz-messen` auf der Zielhardware geprueft
werden; das Repo enthaelt keine Messergebnisse, sie entstehen erst in der
Zielumgebung (siehe [`docs/15-latenz.md`](docs/15-latenz.md)).
