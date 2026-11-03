# 03 Spracherkennung (ASR): Ton zu Text

## Wer was macht

Ein Sprachmodell (LLM) liest Text; es hört nicht. Die Umwandlung von Ton in Text macht ein
Spracherkennungsmodell (ASR, z. B. Whisper). Die Kette ist:

```text
Telefon-Audio (G.711, 8 kHz) -> ASR -> Text -> Dialog/LLM -> Text -> TTS -> Audio
```

Fehler der Spracherkennung setzen sich fort: Ein falsch erkanntes Wort führt zu einem falschen
Anliegen oder einer falschen Bestellnummer. Deshalb prüft der Dialog Nummern (Prüfsummen bei
IBAN, Karten, AHV) und fragt bei Unklarheit nach, statt zu raten.

## Telefon-Audio

Genesys liefert G.711 mu-law mit 8000 Samples pro Sekunde. Damit sind Frequenzen nur bis
4 kHz enthalten (halbe Abtastrate). Whisper arbeitet intern mit 16 kHz; der Adapter schickt
das Telefon-Audio mit 8 kHz, der ASR-Dienst tastet hoch. Das macht das Signal kompatibel, fügt
aber keine Information hinzu. Modelle, die auf
Breitband-Audio trainiert sind, erkennen Telefon-Audio deshalb meist schlechter als im
Datenblatt angegeben. Das muss mit eigenen Aufnahmen gemessen werden.

Getrennte Kanäle helfen: Im AudioHook-Monitor-Modus kommen Kundschaft (`external`) und Agent
(`internal`) als eigene Kanäle; die Zuordnung «wer spricht» ist damit ohne Diarisierung
gegeben. Im Audio-Connector-Modus wählt der Agent den Kanal der Kundschaft (`external`).

## Wie die Spracherkennung hier angebunden ist

- Adapter `HTTPASR`: `POST /v1/audio/transcriptions` im OpenAI-Format, eine Äusserung pro
  Request (WAV). Im Compose-Beispiel bedient speaches den Endpunkt mit faster-whisper, einer
  Whisper-Implementierung auf CTranslate2.
- Es gibt **keine Zwischenergebnisse** (Partials). Das semantische Endpointing kennt deshalb
  den Text noch nicht, wenn es über das Turn-Ende entscheidet, und wartet mit den
  Standardwerten 700 ms Stille (Mitte zwischen den Wartezeiten für fertige und unfertige
  Sätze, `TurnConfig`). Mit einer Streaming-Spracherkennung, die Partials liefert, entscheidet
  das Endpointing früher bei fertigen Sätzen und wartet länger bei diktierten Nummern.

## Schweizerdeutsch

Schweizerdeutsch hat keine verbindliche Schreibweise; Systeme geben meist Standarddeutsch aus.
Die Aufgabe ist damit eher Übersetzung als Abschrift, und Fehlerraten hängen stark davon ab,
welche Schreibweise der Referenztext verwendet.

Ein Preprint von 2026 (F. Akeret, arXiv:2606.07608) beschreibt das Feintuning von Whisper
large-v3 mit 1'367 Stunden Rundfunkaudio und hochdeutschen Untertiteln. Das beste Modell
erreicht 25.60 % WER auf dem «All Swiss German Dialects Test Set»; zählt man nur inhaltliche
Erkennungsfehler, 13.8 %. Der Autor zeigt zudem, dass veröffentlichte Bestwerte durch
Überschneidungen mit dem Testset geschönt sind und der Benchmark vor allem
Schreibkonventionen misst. Als Einzelarbeit ohne Begutachtung ist das ein Hinweis, kein
gesicherter Wert; die Konsequenz ist trotzdem klar: Herstellerangaben nicht übernehmen,
sondern auf eigenen Aufnahmen messen.

## Messen

- **WER** (Word Error Rate) = (Ersetzungen + Auslassungen + Einfügungen) / Wörter der Referenz.
- Für den Kundendienst zählen zusätzlich: korrekt erkannte Nummern, korrekt erkanntes
  Anliegen, Anteil Rückfragen.
- Testmaterial: echte, anonymisierte Telefonaufnahmen mit Referenztranskript, in der Mischung
  von Dialekten und Störgeräuschen, die im Betrieb vorkommt. Synthetische oder Studioaufnahmen
  überschätzen die Qualität.
- Die Dauer pro Äusserung misst `gw-contactcenter latenz-messen --asr-url ...` mit einer
  eigenen WAV-Datei ([15](15-latenz.md)).

## Grenzen

- Das Repo enthält keine ASR-Messung auf Schweizer Telefon-Audio.
- Kein Streaming-ASR-Adapter (keine Partials).
- Namen und Adressen erkennt die Pseudonymisierung nicht ([16](16-leitplanken.md)).

## Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Wie hoch ist der Anteil Schweizerdeutsch, und welche Dialekte? | Bestimmt, ob ein Standardmodell reicht. |
| 2 | Gibt es eine rechtliche Grundlage, Testaufnahmen zu erstellen und auszuwerten? | Ohne Aufnahmen keine Messung. |
| 3 | Welche Fehler sind kritisch (Nummern, Namen, Beträge)? | Danach richtet sich, wo der Dialog nachfragen muss. |
