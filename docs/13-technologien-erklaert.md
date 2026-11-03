# 13 Technologien: was es ist und wo es im Repo vorkommt

Jede Zeile beantwortet drei Fragen: Was ist es? Wofür dient es hier? Wo steckt es im Repo?
Angaben zu fremden Projekten stammen aus deren Dokumentation und sind als solche gekennzeichnet.

## Telefonie und Transport

| Technologie | Was es ist | Wofür hier | Wo |
|---|---|---|---|
| SIP / RTP | Protokolle der Internet-Telefonie: SIP baut den Anruf auf, RTP transportiert den Ton | läuft zwischen Netzbetreiber und Genesys; der Agent sieht es nicht | - |
| G.711 µ-law (PCMU) | Telefon-Codec: 8000 Samples/s, 8 Bit logarithmisch, 64 kbit/s pro Richtung | Format des Tons von und zu Genesys; selbst implementiert, weil `audioop` ab Python 3.13 fehlt (PEP 594) | `audio.py` |
| Genesys Cloud | Cloud-Contact-Center mit Routing, IVR (Architect) und Agenten-Arbeitsplatz | nimmt den Anruf an; der Architect-Flow ruft den Agenten | - |
| Audio Connector | Genesys-Integration, die Anrufaudio bidirektional an einen externen Bot streamt; im Architect über die Aktion «Call Audio Connector» | der Agent hängt sich so in den Anruf; Ende per `disconnect` mit Ausgabevariablen | `audiohook.py` |
| AudioHook Monitor | dasselbe Protokoll, nur mithören, Kanäle getrennt | Live-Transkript oder Agent Assist während Gesprächen mit Menschen | `audiohook.py` (`modus="monitor"`) |
| HTTP Message Signatures (RFC 9421) | Signatur über ausgewählte Teile einer HTTP-Anfrage | prüft, dass die Verbindung wirklich von der eigenen Genesys-Org kommt | `signatur.py` |
| WebSocket (`wss://`) | dauerhafte, beidseitige Verbindung über HTTPS | Transport für JSON-Steuernachrichten und Audio | `server.py` |
| Caddy | Webserver mit automatischem TLS | terminiert TLS vor dem Agenten | `deploy/Caddyfile` |

## Audio und Gesprächsführung

| Technologie | Was es ist | Wofür hier | Wo |
|---|---|---|---|
| VAD (Voice Activity Detection) | entscheidet pro 20 ms: Sprache oder Stille | Grundsignal für Endpointing und Barge-in | `vad.py` (Energie), `adapters/silero.py` |
| Silero VAD | kleines neuronales Netz (MIT-Lizenz); laut Projekt unter 1 ms pro Audioblock auf einem CPU-Thread | robuster als Energie-VAD bei Störgeräuschen | `adapters/silero.py` (Extra `silero`) |
| Endpointing | entscheidet, wann jemand fertig ist, nicht nur still | grösster Posten im Latenzbudget ([15](15-latenz.md)) | `turn.py` |
| Turn-Detection-Modelle (z. B. Pipecat Smart Turn, LiveKit Turn Detector) | offene Modelle für Satzende-Erkennung | als Funktion `semantik=` statt der eingebauten Heuristik einsteckbar | `turn.py` |
| Barge-in | die Kundschaft unterbricht den Bot | Bot verstummt, nicht gesendeter Ton wird verworfen | `pipeline.py`, `server.py` |
| Rückmeldelaute | «mhm», «genau» als Zuhörsignal | dürfen den Bot nicht unterbrechen | `turn.py` |

## Spracherkennung

| Technologie | Was es ist | Wofür hier | Wo |
|---|---|---|---|
| Whisper | offenes, mehrsprachiges Spracherkennungsmodell | Ton zu Text | über `adapters/openai_compat.py` |
| faster-whisper (CTranslate2) | Whisper-Laufzeit; laut Projekt bis zu 4-mal schneller als die Originalimplementierung bei gleicher Genauigkeit | im speaches-Container | `deploy/compose.yaml` |
| speaches | Server mit OpenAI-kompatibler API für faster-whisper (ASR) sowie Piper und Kokoro (TTS) | ein Container für ASR und TTS, austauschbar | `deploy/compose.yaml` |
| WER (Word Error Rate) | Anteil falsch erkannter Wörter | Qualitätsmass; bei Dialekt nur zusammen mit inhaltlichen Fehlern aussagekräftig ([03](03-asr-transkribation.md)) | - |

## Sprachmodell und Inferenz

| Technologie | Was es ist | Wofür hier | Wo |
|---|---|---|---|
| vLLM | Inferenzserver mit OpenAI-kompatibler API | lokales Modell für mehrere Gespräche gleichzeitig ([04](04-vllm-inferenz.md)) | `deploy/compose.yaml` |
| PagedAttention, Continuous Batching | KV-Cache in Blöcken, laufendes Bündeln von Anfragen | mehrere Gespräche teilen sich eine GPU | - |
| Prefix Caching | bereits berechneter Gesprächsanfang bleibt im Speicher | grösster Hebel für die Zeit bis zum ersten Token; für ein 8-B-Modell auf einer L4 geschätzt rund 0.1 s statt 2 s ([11](11-performance-architektur.md)) | `--enable-prefix-caching` |
| KV-Cache | Zwischenspeicher pro Gespräch | bestimmt, wie viele Gespräche auf eine Karte passen | `sizing.py` |
| Quantisierung (AWQ, FP8) | Gewichte mit 4 bzw. 8 Bit statt 16 | weniger Speicher und weniger zu lesende Byte pro Token | `sizing.py` |
| Qwen3-8B | offenes Modell (Apache-2.0) | Beispielmodell im Compose; Denkmodus per Request ausgeschaltet | `deploy/.env.example`, `LLM_EXTRA_JSON` |
| JSON-Modus | das Modell soll gültiges JSON liefern | Klassifikation; der Dialog prüft das Schema streng und übergibt bei Fehlern | `dialog.py` |

## Sprachausgabe

| Technologie | Was es ist | Wofür hier | Wo |
|---|---|---|---|
| Piper | schnelle lokale TTS mit deutschen Stimmen | Beispiel für lokale Sprachausgabe über speaches | `deploy/.env.example` |
| Kokoro | kleine TTS (Apache-2.0); laut Modellkarte ohne deutsche Stimme | deshalb nicht Standard | - |
| Cloud-TTS mit de-CH-Stimmen | z. B. Azure Speech | wenn Schweizer Klang wichtig ist und die Datenklasse es zulässt; kein Adapter im Repo | - |
| Satz-Streaming | TTS beginnt mit dem ersten Satz, während das Modell weiterschreibt | verkürzt die Zeit bis zum ersten Ton | `gateway.py` (`saetze`), `pipeline.py` |

## Leitplanken

| Technologie | Was es ist | Wofür hier | Wo |
|---|---|---|---|
| Datenklassen V0-V4 | Vertraulichkeitsstufen | bestimmen die zulässige Modellregion | `policy.py` |
| Pseudonymisierung | ersetzt erkannte Personendaten durch Platzhalter | das Modell sieht prüfsummengültige IBAN, Karten- und AHV-Nummern nicht | `pii.py` |
| Luhn, IBAN Mod-97, EAN-13 | Prüfsummen von Karten-, IBAN- und AHV-Nummern | echte Nummern erkennen, Bestellnummern in Ruhe lassen | `pii.py` |
| NER-Werkzeuge (z. B. Microsoft Presidio) | Erkennung von Namen und Adressen per Modell | nicht im Repo; als `Pruefer` oder im Tresor ergänzbar | [16](16-leitplanken.md) |
| Spotlighting | Kundentext im Prompt klar als Daten markieren | erschwert Prompt-Injection, verhindert sie nicht | `leitplanken.py` |
| Eingabe- und Antwortprüfer | austauschbare Prüfer hinter einer Schnittstelle | Regex als erste Schicht; beim zweiten Befund Übergabe | `leitplanken.py`, `dialog.py` |
| Circuit Breaker | sperrt ein fehlerhaftes Backend vorübergehend | kein Warten auf ein ausgefallenes Modell | `gateway.py` |

## Nachweis und Betrieb

| Technologie | Was es ist | Wofür hier | Wo |
|---|---|---|---|
| Hash-Kette mit HMAC | jeder Eintrag enthält den HMAC über Inhalt und Vorgänger | Änderungen, Löschungen und Einfügungen werden erkennbar | `audit.py` |
| WORM-Speicher | einmal schreiben, nicht mehr ändern (z. B. Object Lock) | Ziel für den Export des letzten Prüfwerts; nicht im Repo | [10](10-betrieb-ops.md) |
| Spans | Zeitmessung pro Verarbeitungsschritt (eigene Implementierung, kein OpenTelemetry) | p50/p95 je Stufe | `telemetry.py` |
| Prometheus, Grafana | Metrik-Sammlung und Dashboards | `/metrics` des Agenten und von vLLM | `deploy/prometheus.yml` |
| Golden-Set | feste Testfälle mit erwartetem Verhalten | Gate vor Modell-, Prompt- oder Policy-Wechseln | `evals/` |
| Docker Compose | mehrere Container auf einem Server | Beispielbetrieb ohne Kubernetes | `deploy/compose.yaml` |

## Grenzen

- Die Tabellen beschreiben Rollen im System, keine Produktempfehlung. Lizenzen von Modellen
  und Stimmen pro Projekt prüfen.
- Kein Vergleich von ASR- oder TTS-Modellen auf Schweizer Telefon-Audio im Repo.

## Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Welche Komponente würde man zuerst tauschen, und was muss danach grün sein? | Zeigt, ob der Adapter-Schnitt trägt; Evals und Latenzmessung sind die Abnahme. |
| 2 | Braucht es Schweizer Klang in der Sprachausgabe? | Klang und Datenresidenz können sich widersprechen. |
| 3 | Welche Lizenzbedingungen gelten für Modell und Stimme? | Offene Gewichte heisst nicht immer freie kommerzielle Nutzung. |
