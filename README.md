# contactcenter

> **Stand: Oktober 2026, Version 2.** KI im Kundendienst / Voice-AI /
> Contact-Center für den Schweizer Mittelstand (KMU).
> Referenzwerk **und** lauffähiger Sprachagent. Kein Rechtsrat, keine
> Kalkulation; alle Preise sind Grössenordnungen (10/2026).

## Warum dieses Repo

Ein wiederkehrender Kundenwunsch: «Wir wollen KI im Support: Anruf
annehmen, verstehen, antworten, Ticket, Weiterleitung. Wie? Wie teuer?
Was passiert mit den Daten? Wie skalieren wir auf 200 Anrufe/Tag oder
10 gleichzeitig?»

Version 1 hat das als Dokumentation beantwortet. **Version 2 baut es**:
einen Sprachagenten, der Genesys-Telefonie spricht, Modelle lokal
betreibt und die Leitplanken einer Schweizer Bank hat, im Massstab eines
KMU (s. [`docs/12`](docs/12-zielarchitektur.md)).

## In 60 Sekunden ausprobieren

Nur Python >= 3.9, keine GPU, kein API-Key:

```bash
python -m voiceagent.sim                    # ganzer Anruf: Audio, Turn-Taking, Tools, Übergabe
python -m voiceagent.sizing qwen3-8b 10     # welche GPU für 10 Gespräche?
python evals/run_evals.py                   # 20 Fälle, Gate: Sicherheit 100 %
python -m unittest discover -s tests        # 70 Tests inkl. WebSocket-Server*
```
\* Der Servertest braucht `pip install websockets`, sonst wird er übersprungen.

Ausschnitt `voiceagent.sim`:

```
Bot 2: Ihre Bestellung 447100 ist unterwegs. Die Zustellung erfolgt morgen bis 12 Uhr.
Bot 4: Ich habe das Ticket T-00001 erfasst und verbinde Sie jetzt mit Fachbereich (2. Level).
Audit-Kette intakt: True
zeit_bis_erster_ton    p50 690 ms   Ziel 1000   ok
```

Produktiv (1 GPU-Server, TLS, vLLM, faster-whisper, Piper):
`deploy/compose.yaml`, Anleitung im Datei-Kopf.

## Architektur in einem Bild

```
Genesys --wss/PCMU--> AudioHook -> VAD -> Turn-Taking -> ASR -> Dialog/Policy
                         ^                                        |       |
                         |                               Werkzeuge   LLM-Gateway
                         +--------- TTS <-- Sätze (gestreamt, geprüft) <--+
                                    Audit-Hash-Kette  ·  Latenz-Spans
```

Fünf Regeln, die das System trägt:

1. **Kein Modell ohne Gateway**: Datenklasse V0-V4 bestimmt die Region;
   V2-V4 verlassen das Haus nie. PII wird vor dem Modell pseudonymisiert.
2. **Kein Schreibzugriff ohne Werkzeug**: Allowlist pro Anliegen, Schema,
   Bestätigung ab R1, Idempotenz.
3. **Der Wert liegt in der Übergabe**: R3 geht sofort an Menschen, mit
   Zusammenfassung und Ticket an den Genesys-Flow.
4. **Gemessen, nicht geschätzt**: Latenz pro Stufe, Evals mit Gate in CI.
5. **Ausfall = Mensch, nie Stille**: Fallback-Kette, Circuit Breaker,
   Notfall-Übergabe.

## Die 18 Facetten (s. [`docs/02`](docs/02-entscheidsmatrix.md))

| # | Facette | Doku |
|---|---|---|
| 1-8 | Projekt, Architektur, Kosten, Aufwand, Skills, Wissen, Betrieb, Schulung | 01, 08, 09, 10 |
| 9-11 | Inferenz, ASR, vLLM | 03, 04 |
| 12, 15 | Skalierung, Performance/Hardware | 05, 11 |
| 13, 14 | Daten/Speicherung, Weiterleitung | 06, 07 |
| 16-18 | Echtzeit-Telefonie, Turn-Taking, Evals | **12** |
| | Alle Technologien erklärt | **13** |
| | Dessa (Block) und Full-Duplex-Modelle | **14** |

## Repo-Struktur

```
voiceagent/                 lauffähiger Agent (Kern nur stdlib)
  audio.py audiohook.py     G.711, Genesys AudioHook / Audio Connector
  vad.py turn.py            Sprachaktivität, Endpointing, Barge-in
  pipeline.py dialog.py     Streaming-Kaskade, Dialogsteuerung
  policy.py gateway.py      Anliegen V0-V4/R0-R3, LLM-Gateway mit Leitplanken
  tools.py audit.py         Werkzeuge, Hash-Ketten-Audit
  telemetry.py sizing.py    Latenz-Spans, GPU-Dimensionierung
  sim.py server.py          Anrufsimulator, WebSocket-Server
  adapters/                 Mock, OpenAI-kompatibel (vLLM, speaches), Silero
evals/                      Golden-Set (20 Fälle) + Gate
tests/                      70 Tests (Unit, Integration, End-to-End)
deploy/                     Compose, Dockerfile, Caddy, Prometheus
beispiele/                  v1-Samples (Erlang-C, Router, Pipeline, Q-Suite)
docs/                       15 Dossiers 00-14
```

## Grenzen (ehrlich, s. [`OFFENE_PUNKTE.md`](OFFENE_PUNKTE.md))

1. **Nur Genesys ist als Telefonie umgesetzt.** SIP-Trunk/Asterisk/Twilio
   brauchen einen weiteren Protokoll-Adapter.
2. **AudioHook-Signaturprüfung** ist nur API-Key; vor Produktion ergänzen
   und mit der Genesys AudioHook Test Suite prüfen.
3. **Schweizerdeutsch** bleibt der härteste Punkt (Doku 03, 13).
4. **Evals offline = Verdrahtung korrekt**, nicht Modellqualität. Vor
   Produktion mit `--llm-url` gegen das echte Modell und echte Fälle.
5. **Preise sind Grössenordnungen** (10/2026); vor jedem Angebot neu rechnen.
6. **DSG/AI-Act gehört nach `governance` + Rechtsbeistand.** Das Repo
   sagt, wo Daten liegen und wer sie sieht, nicht was erlaubt ist.
7. **24/7 ist 2 Köpfe, nicht 1** (Doku 10).

## Kritische Fragen

1. **Welche Anliegen darf der Bot selbst lösen (R0/R1), und welche Daten
   sind V2-V4?** Ohne Antwort keine Policy.
2. **Wer ist der KI-Owner, und wer pflegt das Golden-Set?** Ohne Evals
   merkt niemand, wenn ein Modellwechsel schlechter ist.

## Lizenz und Kontakt

Code unter **MIT**, kommerzielle Vorbehalte für Beratung und
Kalkulationsmaterial (s. [`LICENSE`](LICENSE)). Fragen: mail@gradweight.ch
