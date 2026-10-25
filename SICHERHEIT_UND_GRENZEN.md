# SICHERHEIT, HAFTUNG UND GRENZEN — GRADweight/contactcenter

> **Kurz:** Offene Referenz von GRADweight GmbH. Kein Rechts- oder Beratungsgutachten. Alle Beispiele verwenden erfundene Referenzdaten, keine echten Kundendaten.

| Feld | Wert |
|---|---|
| Repo | `GRADweight/contactcenter` |
| Stand | 25.10.2026 |
| Lizenz | `MIT` — ein Satz: Code + Designs vor Public; internes PROJEKT.md/OFFENE_PUNKTE lokal entfernt. |
| Status | Intern geprüft, **noch NICHT public** (kein Visibility-Wechsel ohne ausdrückliche Owner-Freigabe). |

## 1. Sicherheitshinweise (Gemeinschaft)
- **Keine echten Kundendaten.** Alle Beispiele verwenden erfundene Referenzdaten (Marken-/Kundenwerte sind Platzhalter). Ueberprueft 25.10.2026: Grep nach AG/GmbH/Kundenamen lieferte nur Fachbegriffe.
- **Kein Rechts- oder Beratungsgutachten.** Dieses Repo ist eine technische Referenz, kein rechtsverbindliches Dokument. Vor einem konkreten Einsatzzweck eigene Pruefung / Fachberatung.
- **Versioniert, aber ohne Garantie.** Schildert den aktuellen Stand; keine SLA oder Aktualisierungspflicht. Vor Public erneut gegen HEAD pruefen.

## 2. Bekannte Fehler / offene Gaps (Repo-spezifisch)
- **PROJEKT.md + OFFENE_PUNKTE.md intern (lokal entfernt)..** Vor Public im Remote ebenfalls löschen.
- **4 Code-Dateien, Design-dominiert (11 tests, 1428 Zeilen)..** OK, aber Antwort-Qualität-Test-Gap (s. OFFENE_PUNKTE) ehrlich deklariern.
- **Keine version (kein pyproject)..** Optional v0.1.0.

## 3. Vor Public (Checkliste)
- [ ] Gegen HEAD pruefen: keine internen Pfade (/Users, ~/, /tmp), keine echten Kundendaten, keine Preise/Marge im Public-Text
- [ ] Interne Docs entfernt (OFFENE_PUNKTE, PROJEKT, ROADMAP, M-Module)
- [ ] Lizenz korrekt + ein-Satz-Erklaerung im README (s. oben)
- [ ] Sichtbarkeit erst nach **ausdruecklicher Owner-Freigabe** (kein automatischer Visibility-Wechsel)

_Grundlage: interner Forensic-Scan 25.10.2026; Werte aus /tmp/gwscan; nichts geschoben._
