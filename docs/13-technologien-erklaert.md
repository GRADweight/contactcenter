# 13 - Technologien erklärt: was es ist und wofür wir es nutzen

> **Stand: Oktober 2026.** Jede Zeile beantwortet drei Fragen: Was ist
> es? Wofür brauchen wir es hier? Wo steckt es im Repo? Versionen und
> Anbieter wandern; die Rolle im System bleibt.

Ein Telefon-Voicebot ist eine Kette. Jede Technologie unten besetzt genau
ein Glied. Wer ein Glied tauscht (z. B. ein anderes ASR-Modell), muss nur
den Adapter tauschen, nicht die Kette.

## 1. Telefonie und Transport

| Technologie | Was es ist | Wofür hier | Wo |
|---|---|---|---|
| **SIP / RTP** | Standardprotokolle der Internet-Telefonie: SIP baut den Anruf auf, RTP transportiert den Ton in 20-ms-Paketen | Läuft zwischen Carrier und Genesys; wir sehen es nicht direkt | - |
| **G.711 (PCMU, mu-law)** | Telefon-Codec: 8'000 Samples/s, 8 bit, logarithmisch, 64 kbit/s pro Richtung | So kommt der Ton bei uns an und so senden wir ihn zurück; Codec selbst implementiert, weil `audioop` in Python 3.13 entfernt wurde | `audio.py` |
| **Genesys Cloud CX** | Cloud-Contact-Center (Routing, IVR, Agent-Desktop); seit 2023 mit Region Zürich | Nimmt den Anruf an, der Architect-Flow ruft unseren Bot | - |
| **Audio Connector** | Genesys-Mechanismus, der Anrufaudio bidirektional an einen externen Bot streamt (nur im IVR, nicht beim Agenten) | Unser Voicebot hängt sich so in den Anruf; Ende per `disconnect` mit Output-Variablen (Ziel-Queue, Ticket) | `audiohook.py` |
| **AudioHook (Monitor)** | Gleiches Protokoll, nur mithören, beide Kanäle getrennt | Live-Transkript und Agent Assist während Mensch-Gesprächen | `audiohook.py` (`modus="monitor"`) |
| **WebSocket (wss://)** | Dauerhafte, beidseitige Verbindung über HTTP(S) | Transport für Steuer-JSON und Audio-Binärframes | `server.py` |
| **Caddy** | Webserver mit automatischem TLS (Let's Encrypt) | Genesys verbindet nur verschlüsselt; Caddy terminiert TLS und leitet weiter | `deploy/Caddyfile` |

## 2. Audio und Gesprächsführung

| Technologie | Was es ist | Wofür hier | Wo |
|---|---|---|---|
| **VAD** (Voice Activity Detection) | Entscheidet pro 20 ms: Sprache oder Stille | Grundsignal für Turn-Taking und Barge-in | `vad.py` (Energie), `adapters/silero.py` |
| **Silero VAD** | Kleines neuronales Netz (MIT), ~1 ms/Frame auf CPU | Robuster als Energie-VAD bei Lärm, Musik, Auto | `adapters/silero.py` |
| **Endpointing / Turn Detection** | Entscheidet, wann jemand *fertig* ist (nicht nur still) | Der grösste Latenzposten; semantisch statt fester Stille | `turn.py` |
| **Pipecat Smart Turn, LiveKit Turn Detector** | Offene Modelle für Turn Detection (Audio- bzw. textbasiert) | Vorbild für unsere Heuristik; als `semantik=`-Funktion einsteckbar | `turn.py` |
| **Barge-in** | Unterbrechen des Bots durch die Kundschaft | Bot verstummt, Genesys verwirft gepufferten Ton, Verlauf wird gekürzt | `pipeline.py`, `audiohook.py` |
| **Backchannel** | "mhm", "genau", "ja" als Zuhör-Signal | Darf den Bot **nicht** unterbrechen | `turn.py` |

## 3. Spracherkennung (ASR)

| Technologie | Was es ist | Wofür hier | Wo |
|---|---|---|---|
| **Whisper** (OpenAI, offen) | Spracherkennungsmodell, mehrsprachig | Ton zu Text | via `adapters/openai_compat.py` |
| **faster-whisper / CTranslate2** | Schnelle Whisper-Laufzeit (int8/fp16) | 4x schneller als Original bei gleicher Qualität | im speaches-Container |
| **speaches** | Server mit OpenAI-kompatibler API für faster-whisper (ASR) und Piper/Kokoro (TTS) | Ein Container für ASR **und** TTS, austauschbar gegen jeden OpenAI-kompatiblen Dienst | `deploy/compose.yaml` |
| **Schweizerdeutsch-Feintuning** | Whisper nachtrainiert auf Dialekt mit hochdeutschen Untertiteln | Pflicht bei hohem Dialektanteil. Vorsicht bei Benchmarks: Eine Studie von 2026 fand für ein Whisper-large-v3-Feintuning (1'367 h) 25.6 % WER, inhaltlich ~13.8 %, und zeigt, dass niedrige WER-Werte oft nur Schreibkonventionen treffen | `docs/03` |
| **WER** (Word Error Rate) | Anteil falsch erkannter Wörter | Qualitätsmass; bei Dialekt nur zusammen mit inhaltlicher Fehlerrate aussagekräftig | `docs/03` |

## 4. Sprachmodell (LLM) und Inferenz

| Technologie | Was es ist | Wofür hier | Wo |
|---|---|---|---|
| **vLLM** | Inferenzserver für LLMs, OpenAI-kompatible API | Lokales Modell mit hoher Parallelität | `deploy/compose.yaml` |
| **PagedAttention / Continuous Batching** | vLLM-Techniken: KV-Cache in Seiten, Anfragen laufend bündeln | Viele Gespräche auf einer Karte ohne Wartezeit | - |
| **Prefix Caching** | Bereits berechneter Gesprächsanfang bleibt im Speicher | Erstes Token ~100 ms statt ~2 s (8 B auf L4); wichtigster Latenzhebel | `--enable-prefix-caching` |
| **KV-Cache** | Zwischenspeicher pro Gespräch (Kurzzeitgedächtnis des Modells) | Bestimmt, wie viele Gespräche auf eine Karte passen | `sizing.py` |
| **Quantisierung (AWQ, FP8)** | Gewichte mit 4 bzw. 8 bit statt 16 | Halbiert/viertelt Speicher und Lesezeit; kleinere, günstigere GPU | `sizing.py` |
| **Qwen3-8B** | Offenes Modell (Apache-2.0), mehrsprachig, gutes Deutsch | Antworten und Klassifikation lokal; **Thinking-Modus aus** (sonst Hunderte Token Vorlauf) | `.env.example`, `LLM_EXTRA_JSON` |
| **JSON-Modus / Structured Output** | Modell muss gültiges JSON liefern | Klassifikation maschinenlesbar, kein Freitext-Parsing | `dialog.py` |
| **Function Calling** | Modell schlägt Werkzeugaufrufe mit Argumenten vor | Wir führen sie nur nach Prüfung aus | `tools.py` |
| **NVIDIA L4 / L40S / H100, Apple M4 Max** | GPUs bzw. Unified-Memory-Chip | Aus Speicher und Bandbreite gerechnet: L4 reicht für 10 Gespräche mit 8 B | `sizing.py` |

## 5. Sprachausgabe (TTS)

| Technologie | Was es ist | Wofür hier | Wo |
|---|---|---|---|
| **Piper** | Schnelle, lokale TTS (VITS), deutsche Stimmen (de_DE) | Standard für lokal; läuft auf CPU | via speaches |
| **Azure Speech de-CH** | Cloud-TTS mit Schweizer Stimmen (Region Schweiz) | Wenn Schweizer Klang wichtig ist; nur für V0/V1-Inhalte | Adapter analog `openai_compat.py` |
| **Kokoro** | Kleine, sehr natürliche TTS (Apache-2.0) | **Keine deutsche Stimme**, deshalb hier nicht Standard | - |
| **Satz-Streaming** | TTS beginnt mit Satz 1, während das LLM weiterschreibt | Spart 0.5-2 s gegenüber "erst alles, dann vorlesen" | `gateway.saetze`, `pipeline.py` |

## 6. Leitplanken (Gateway)

| Technologie | Was es ist | Wofür hier | Wo |
|---|---|---|---|
| **Datenklassen V0-V4** | Vertraulichkeitsstufen | Entscheiden, in welcher Region ein Modell laufen darf | `policy.py` |
| **Pseudonymisierung (PII-Tresor)** | Ersetzt Personendaten durch Platzhalter, Rückweg nur intern | Modell sieht nie IBAN, Karte, AHV-Nummer | `gateway.py` |
| **Luhn, IBAN Mod-97, EAN-13** | Prüfsummen von Karten-, IBAN- und AHV-Nummern | Echte Nummern maskieren, Bestellnummern in Ruhe lassen (wenig Fehlalarme) | `gateway.py` |
| **Microsoft Presidio / NER** | Erkennung von Namen, Adressen per Modell | Ergänzung für Produktion, Regex findet keine Namen | Grenzen |
| **Prompt Injection, Spotlighting** | Angriff per Kundentext; Abwehr durch klares Markieren als Daten | `<kundschaft>`-Tags + Muster; zweiter Versuch = Übergabe | `gateway.py`, `dialog.py` |
| **Circuit Breaker** | Schaltet ein fehlerhaftes Backend vorübergehend ab | Keine Wartezeit auf ein totes Modell, sofort Fallback | `gateway.py` |

## 7. Nachweis, Betrieb, Qualität

| Technologie | Was es ist | Wofür hier | Wo |
|---|---|---|---|
| **Hash-Kette (+ HMAC)** | Jeder Eintrag enthält den Hash des vorherigen | Nachträgliche Änderung oder Löschung fällt auf | `audit.py` |
| **WORM-Speicher** | Write Once Read Many (S3 Object Lock, unveränderliche Snapshots) | Revisionssichere Ablage des Audit-Logs | `deploy/compose.yaml` (Volume) |
| **Spans / OpenTelemetry** | Zeitmessung pro Verarbeitungsschritt | Latenzbudget pro Stufe, p50/p95 | `telemetry.py` |
| **Prometheus + Grafana** | Metrik-Sammlung und Dashboards | `/metrics` vom Agent und von vLLM (KV-Cache, Queue) | `deploy/prometheus.yml` |
| **Golden-Set-Evals** | Feste Testfälle mit erwartetem Verhalten | Gate vor jedem Modell-, Prompt- oder Policy-Wechsel | `evals/` |
| **Docker Compose** | Mehrere Container auf einem Server | KMU-Betrieb ohne Kubernetes | `deploy/compose.yaml` |
| **k3s + KEDA** | Leichtes Kubernetes + Autoscaling nach Metrik | Erst ab Stufe L: skalieren nach aktiven Sitzungen | `docs/12` |

## 8. Grenzen (ehrlich)

- Die Tabellen beschreiben **Rollen**, keine Produktempfehlung mit Garantie.
  Lizenzen (z. B. Modelle mit Nutzungsbeschränkung) pro Projekt prüfen.
- Azure-de-CH-TTS ist als Option genannt, aber nicht als Adapter im Repo.
- Kein Vergleichstest der ASR-Modelle auf Schweizer Telefonaudio im Repo;
  das gehört in jedes Projekt (30-50 echte Aufnahmen, anonymisiert).

## 9. Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Welche Technologie würden wir als erste tauschen, wenn sie ausfällt? | Zeigt, ob der Adapter-Schnitt wirklich trägt. |
| 2 | Wer darf Modelle wechseln, und welcher Test muss vorher grün sein? | Modellwechsel sind Releases, keine Konfiguration. |
| 3 | Brauchen wir Schweizer Klang (Azure de-CH) oder reicht Hochdeutsch lokal (Piper)? | Klang vs. Datenresidenz ist eine Geschäftsentscheidung. |
