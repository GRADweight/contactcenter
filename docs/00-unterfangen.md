# 00 Das Unterfangen: KI im Kundendienst eines KMU

## Ausgangslage

Die Frage aus der Praxis klingt einfach: «Ein Anruf kommt herein, die KI versteht das
Anliegen, beantwortet einfache Fragen, eröffnet ein Ticket und gibt Schwieriges an die
richtige Person weiter. Wie baut man das, was braucht es an Infrastruktur, und was passiert
mit den Daten?»

Dahinter stecken mehrere Fragen, die sich gegenseitig bedingen:

| Frage | Beantwortet in | Ausführbar mit |
|---|---|---|
| Wie ist das System aufgebaut? | [01](01-architektur.md), [12](12-zielarchitektur.md) | `gw_contactcenter` |
| Wie viele Agenten und Bot-Kanäle braucht es? | [05](05-skalierung.md) | `gw-contactcenter kapazitaet` |
| Welche GPU trägt das Modell? | [11](11-performance-architektur.md) | `gw-contactcenter gpu` |
| Wie schnell antwortet der Bot? | [15](15-latenz.md) | `gw-contactcenter latenz-messen` |
| Wie zuverlässig versteht er Sprache? | [03](03-asr-transkribation.md) | eigene Testaufnahmen |
| Welche Daten gehen wohin? | [06](06-daten-speicherung.md), [07](07-weiterleitung-handoff.md) | `gw_contactcenter.policy` |
| Wie wird Missbrauch begrenzt? | [16](16-leitplanken.md) | `gw_contactcenter.leitplanken` |
| Wie wird es betrieben? | [10](10-betrieb-ops.md) | `deploy/compose.yaml` |
| Wer macht was im Projekt? | [08](08-projekt-aufwand-kosten.md), [09](09-skill-wissen-schulung.md) | - |

## Warum «einfach ein Chatbot» nicht reicht

1. **Ohne Daten weiss das Modell nichts.** Ein Sprachmodell kennt weder Bestellstatus noch
   Öffnungszeiten der Firma. Fakten kommen aus Werkzeugen (Shop, CRM, Wissensbasis); das
   Modell formuliert sie nur.
2. **Ohne Leitplanken ist es eine Angriffsfläche.** Kundentext kann versuchen, Anweisungen
   auszuhebeln. Wirksam ist vor allem, dass das Modell keine Schreibrechte hat: Aktionen
   laufen nur über geprüfte Werkzeuge mit Bestätigung ([16](16-leitplanken.md)).
3. **Ohne Übergabe ist es eine Sackgasse.** Der Nutzen entsteht, wenn heikle oder unklare
   Anliegen schnell und mit Kontext bei der zuständigen Person landen
   ([07](07-weiterleitung-handoff.md)).

## Was dieses Repo ist

- Ein lauffähiger Sprachagent für den Genesys Audio Connector mit Tests, Evals und
  Compose-Beispiel.
- Rechenwerkzeuge für Personal, Kanäle und GPU, deren Formeln offenliegen und geprüft sind.
- Ein Messwerkzeug, das echte Latenzzahlen in der eigenen Umgebung erzeugt.

## Was es nicht ist

- **Kein fertiges Produkt.** Wissensbasis, Shop und CRM sind Demo-Objekte.
- **Kein Rechtsrat.** Ob eine Verarbeitung nach revDSG (in Kraft seit 1. September 2023),
  Branchenrecht oder bei EU-Bezug nach dem EU AI Act zulässig ist, klärt der Betreiber.
- **Keine Offerte.** Das Repo enthält bewusst keine Preise; die Rechenwerkzeuge nehmen eigene
  Preise als Eingabe entgegen.

## Lesereihenfolge

1. README (Überblick, Beispielrechnung)
2. [12 Zielarchitektur](12-zielarchitektur.md)
3. je nach Rolle: [05](05-skalierung.md) und [11](11-performance-architektur.md) für die
   Planung, [16](16-leitplanken.md) und [06](06-daten-speicherung.md) für Sicherheit und
   Datenschutz, [10](10-betrieb-ops.md) für den Betrieb

## Grenzen

- Die Dossiers beschreiben ein Referenzsystem für einen fiktiven Onlinehändler. Katalog,
  Datenklassen und Fristen sind Beispiele.
- Produkt- und Modellnamen ändern sich schnell; genannte Versionen sind ein Stand, keine
  Empfehlung auf Dauer.

## Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Welche Anliegen darf der Bot selbst lösen, welche nie? | Ohne diese Liste gibt es keine Policy. |
| 2 | Wie viele Anrufe kommen in der Spitzenstunde (nicht pro Tag)? | Die Kapazität hängt an der Spitze. |
| 3 | Wie hoch ist der Anteil Schweizerdeutsch? | Bestimmt Modellwahl und Testaufwand der Spracherkennung. |
| 4 | Wer verantwortet das Golden-Set und prüft vor jedem Modellwechsel? | Sonst fallen Verschlechterungen erst bei Beschwerden auf. |
