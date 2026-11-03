# 07 Weiterleitung: Anliegen, Autonomie, Übergabe

## Anliegen-Katalog

Jedes Anliegen hat eine Datenklasse (V0-V4: Wie vertraulich?) und eine Risikoklasse (R0-R3:
Was passiert, wenn der Bot sich irrt?). Daraus folgen Modellregion, Autonomie, zuständiger
Bereich und Bearbeitungsfrist. Der Katalog ist ein Beispiel für einen fiktiven Onlinehändler
(`gw_contactcenter/policy.py`, einzige Quelle):

<!-- ausgabe: python beispiele/handoff_router.py -->
```text
allgemeine-frage       V1 R0  Modell bis eu     löst selbst              Kundendienst (1. Level)    Frist 5 Tag(e)
preis-anfrage          V1 R0  Modell bis eu     löst selbst              Kundendienst (1. Level)    Frist 7 Tag(e)
bestellung-ankommen    V2 R1  Modell bis lokal  löst mit Bestätigung     Fachbereich (2. Level)     Frist 2 Tag(e)
reklamation            V2 R2  Modell bis lokal  nimmt auf und übergibt   Fachbereich (2. Level)     Frist 3 Tag(e)
konto-stellung         V3 R2  Modell bis lokal  nimmt auf und übergibt   Leitende (3. Level)        Frist 1 Tag(e)
vertrag-aenderung      V3 R3  Modell bis lokal  übergibt sofort          Leitende (3. Level)        Frist 1 Tag(e)
datenschutz-verstoss   V4 R3  Modell bis lokal  übergibt sofort          Geschäftsleitung           Frist 1 Tag(e)
```

| Risikoklasse | Verhalten des Bots |
|---|---|
| R0 | löst selbst (nur mit Quelle aus der Wissensbasis, sonst Angebot zur Übergabe) |
| R1 | löst selbst; Werkzeuge mit Wirkung erst nach «Ja» |
| R2 | nimmt das Anliegen auf (Ticket nach «Ja») und übergibt |
| R3 | übergibt sofort, ohne Werkzeuge |

Passen mehrere Anliegen auf eine Aussage, gewinnt das riskanteste («Wo ist meine Bestellung,
und ich will kündigen» geht an die Leitenden). Wer ausdrücklich einen Menschen verlangt, wird
immer verbunden.

## Was bei der Übergabe mitgeht

Der Dialog erzeugt pro Übergabe:

| Feld | Inhalt |
|---|---|
| `ziel` | zuständiger Bereich |
| `anliegen` | Anliegen-ID |
| `sla_tage` | Bearbeitungsfrist |
| `grund` | z. B. «Risiko R3», «Kundschaft verlangt Menschen», «technisch: TimeoutError» |
| `ticket` | Ticketnummer, falls erstellt |
| `zusammenfassung` | die letzten Kundenäusserungen, pseudonymisiert, höchstens 500 Zeichen |

An Genesys gehen davon `zielQueue`, `anliegen` und `ticket` plus `action` als
`outputVariables` im `disconnect` (alle Werte als Strings). Der Architect-Flow ordnet sie
Flow-Variablen zu und routet den Anruf weiter.

## Grenzen

- Der Katalog ist ein Beispiel; Datenklassen, Risiken und Fristen sind Entscheidungen des
  Betreibers.
- Keine Anbindung an ein echtes CRM oder Ticketsystem (`DemoBackend`).
- Die Fristen werden nur weitergegeben, nicht überwacht.

## Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Welche Anliegen gibt es wirklich, und wie häufig sind sie? | Grundlage für Katalog und Golden-Set. |
| 2 | Wer nimmt Übergaben in welchem Bereich entgegen, auch ausserhalb der Bürozeiten? | Eine Übergabe ins Leere ist schlechter als keine Automatisierung. |
| 3 | Welche Felder braucht das Team bei der Übergabe? | Bestimmt Zusammenfassung und `outputVariables`. |
