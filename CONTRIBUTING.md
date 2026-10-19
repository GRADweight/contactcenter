# Mitwirken an contactcenter

Danke, dass du mitwirkst. Dies ist ein **Referenzwerk** der GRADweight
GmbH für KI im Kundendienst / Voice-AI im Schweizer Mittelstand. Der
Stil ist bewusst: Schweizer Hochdeutsch, Probleme vor Lob, keine Preise
und kein Rechtsrat.

## Was gehört in ein PR

- **Korrektur in einem Dossier** (veralteter Modell- oder Toolname,
  fehlende Quellenangabe, falscher Wert in einer Tabelle):
  willkommen, bitte `git blame`-Quelle im Commit-Message angeben.
- **Neues Beispiels-Script in `beispiele/`**:
  **Bedingung**: jedes neue Sample MUSS einen `python beispiele/<name>.py
  selftest` enthalten, der
  - ohne Netz, ohne API-Keys, ohne externe Abhängigkeiten
  - mit Python-Standardbibliothek allein
  - und ohne Zufalls- oder Zeit-abhängigen Wert
  grün wird.
  Das Sample wird in `.github/workflows/ci.yml` eingetragen. Ohne Selftest
  wird das PR nicht gemergt.
- **Neues Dossier in `docs/`**: mit einer `## Grenzen (ehrlich)`-Sektion
  und einer `## Kritische Fragen` (Entscheid-Checkpunkte, NICHT To-dos).
  Preiskalkulation gehört NICHT in ein Dossier, s. nur
  `OFFENE_PUNKTE.md` für Grössenordnungen.

## Was NICHT in ein PR gehört

- Konkrete Preisangaben oder Kalkulation
- Kundennamen oder echte Kundendaten
- Em-Dashes in Text (benutz `-` oder `–` korrekt)
- Lob-Reden über das Projekt, ohne die Grenzen mit anzugeben

## Referenz-Nutzung

Die drei lauffähigen Samples in `beispiele/` sind als Ausgangspunkt gedacht.
Du darfst sie in deinem eigenen Code verwenden, mit Attribution
`"GRADweight GmbH, beispiele/<name>, v0.1.0"`. Die Lizenz ist
«Alle Rechte vorbehalten»; die Referenz-Nutzung ist in der LICENSE
explizitamente erlaubt.

## Sitzungs-Matik (für interne Arbeit)

Siehe `PROJEKT.md` § Sitzungs-Matik (5 Regeln). Kurz: Kontext-Reinigung ist
verlustbehaftet; Repo + Git-Log ist der Recovery-Wege, NICHT das
Gedächtnis.
