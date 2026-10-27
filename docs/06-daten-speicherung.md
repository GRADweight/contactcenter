# 06 Daten: Klassen, Verarbeitungsort, Speicherbedarf

Kein Rechtsrat. Das Dossier beschreibt, welche Daten das System verarbeitet und wo; ob und wie
lange das zulässig ist (revDSG, Branchenrecht, Aufbewahrungspflichten, bei EU-Bezug DSGVO und
EU AI Act), klärt der Betreiber mit Fachleuten.

## Welche Daten anfallen

| Daten | Wo im System | Standard im Repo |
|---|---|---|
| Audio des Anrufs | Arbeitsspeicher des Agenten, ASR-Dienst | wird **nicht** gespeichert |
| Transkript und Verlauf | Arbeitsspeicher des Dialogs, Prompts an das LLM | pseudonymisiert, nicht gespeichert |
| Übergabedaten | Genesys (`outputVariables`), Ticket | Ziel, Anliegen, Ticketnummer; Zusammenfassung pseudonymisiert |
| Audit | JSON-Lines-Datei pro Sitzung (`AUDIT_DIR`) | Entscheidungen ohne Rohtext, HMAC-Kette |
| Latenzwerte | Prometheus | nur Zeiten und Stufennamen |

Personendaten, die der PII-Tresor erkennt (IBAN, Karte, AHV-Nummer, E-Mail, Telefon,
Kontoangaben), ersetzt der Dialog vor jedem Modellaufruf durch Platzhalter wie `[IBAN_1]`.
Die Klartexte bleiben im Arbeitsspeicher des Anrufs und gehen nur an Werkzeuge im eigenen
System. Namen, Adressen und Freitextangaben erkennt der Tresor nicht ([16](16-leitplanken.md)).

## Datenklassen und Verarbeitungsort

Jedes Anliegen hat eine Datenklasse V0-V4 ([07](07-weiterleitung-handoff.md)). Die Klasse
bestimmt, in welcher Region ein Modell die Anfrage bearbeiten darf (`policy.py`):

| Datenklasse | Offenste erlaubte Region für das Modell |
|---|---|
| V0 öffentlich | `us` |
| V1 normal | `eu` |
| V2 intern | `lokal` |
| V3 vertraulich | `lokal` |
| V4 streng | `lokal` |

Regeln: Personendaten im Gespräch heben die Klasse auf mindestens V2. Eine einmal erreichte
Klasse sinkt im selben Anruf nicht mehr. Die erste Klassifikation läuft immer auf einem
lokalen Modell. Die Zuordnung ist ein Beispiel; welche Region für welche Klasse vertretbar ist,
entscheidet der Betreiber.

## Speicherbedarf für Aufzeichnungen

Falls Gespräche aufgezeichnet werden (nicht Teil des Repos), rechnet `speicherbedarf()` bzw.
`gw-contactcenter kapazitaet --speicher` den Umfang. Für 200 Anrufe zu 120 s:

- G.711 Stereo (8000 Samples/s × 1 Byte × 2 Kanäle = 16'000 Byte/s): 384 MB pro Tag.
- Opus mit 32 kbit/s (Bitrate als Konfigurationsannahme): 96 MB pro Tag.
- Transkripte (Annahme 4000 Token × 4 Byte pro Anruf): 3.2 MB pro Tag.

Der Ton dominiert. Ob überhaupt aufgezeichnet wird, wie lange und wer Zugriff hat, ist zuerst
eine rechtliche und organisatorische Frage.

## Audit-Daten

Die Audit-Kette enthält pro Ereignis Zeitpunkt, Ereignistyp und Entscheidungsdaten (Anliegen,
Aktion, Datenklasse, Backend, Region, Werkzeug, Übergabegrund), keine Rohtexte. Die Kette
erkennt Änderungen, Löschungen und Einfügungen innerhalb der Datei; gegen das Abschneiden der
letzten Einträge hilft nur ein externer Anker, z. B. der regelmässig exportierte letzte
Prüfwert ([10](10-betrieb-ops.md)).

## Grenzen

- Keine Aufbewahrungs- oder Löschlogik im Code.
- Kein Verzeichnis der Bearbeitungstätigkeiten, keine Datenschutz-Folgenabschätzung.
- Die Pseudonymisierung ist regelbasiert; sie ist keine Anonymisierung.

## Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Werden Gespräche aufgezeichnet, und wenn ja, mit welcher Rechtsgrundlage und Information der Anrufenden? | Bestimmt Speicher, Zugriff und Löschung. |
| 2 | Welche Datenklassen dürfen in eine Cloud-Region, welche nie? | Bestimmt die Backends im Gateway. |
| 3 | Wer darf das Audit lesen, und wie lange wird es aufbewahrt? | Ohne Regel ist ein Audit-Log selbst ein Risiko. |
