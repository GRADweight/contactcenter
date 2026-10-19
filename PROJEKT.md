# PROJEKT.md — grad-contactcenter

**Repo:** `github.com/GRADweight/grad-contactcenter` (privat, bis Launch 25.10.2026 geprüft)
**Thema:** KI im Kundendienst / Voice-AI / Contact-Center für den Schweizer Mittelstand (KMU).
**Stand:** 19.10.2026 — erster Ausarbeitungspass.

## Warum dieses Repo

Ein wiederkehrender Kundenwunsch: «Wir wollen KI im Support: Ton anrufen,
Transkribieren, Antwort, Ticket, Weiterleitung. Wie? Wie teuer? Wie viel
Aufwand? Was brauchen wir an Skills? Was passiert mit den Daten? Wie
skalieren wir auf 200 Anrufe/Tag oder 10 gleichzeitig?»

Das ist **ein** Unterfangen, aber es **hat viele Facetten**: Projekt,
Technologie, Kosten, Aufwand, Skills, Wissen, Betrieb, Schulung, Technik,
LLM-Transkribation, vLLM, Skalierung, Daten-Speicherung, Weiterleitung.
Bisher zerstreut in `grad-platforms` (Infrastruktur/Inferenz),
`grad-governance` (DSG/AI-Act/Gates), `grad-autonomy` (Autonomiegrade),
`grad-training` (Schulung). **Hier werden sie zu einem durchgehenden
Dokument — für den Kundendienst-Fall, nicht abstrakt.**

## Ist-Zustand (19.10.2026)

```
beispiele/   (3 lauffähige Samples, alle selftest grün, nur stdlib)
  transkript_pipeline.py   Ton->Text->Zusammenf->Tickets, ASR/Diariz/PII/LLM
  skalierungsrechner.py    Erlang-C + vLLM-Slots + Kosten, 200/Tag+10 zugleich
  handoff_router.py        V0-V4 / R0-R3 / Autonomiegrad / SLA / Bereich
docs/        (11 Dossiers, siehe README Inhaltsverzeichnis)
  00 Unterfangen · 01 Architektur · 02 Entschiede-Matrix
  03 ASR/Transkribation · 04 vLLM · 05 Skalierung
  06 Daten · 07 Weiterleitung · 08 Projekt/Aufwand
  09 Skills/Schulung · 10 Betrieb
.github/workflows/ci.yml   CI-Gate: py_compile + 3 Selftests + Linkcheck (offline)
OFFENE_PUNKTE.md           Offene Punkte + kritische Fragen
```

## Entschieden

- **Name:** `grad-contactcenter` (grad- Prefix, breit: Sprache + Chat + alle Facetten).
- **Sichtbarkeit:** privat wie alle Org-Repos; für 25.10.2026-Launch-Prüfung freigegeben (nach Edin-Entscheidung).
- **Härte der Technik:** lauffähige Samples mit `selftest` (grad-platforms-Muster).
- **Hausstil:** Schweizer Hochdeutsch, «ss», keine Em-Dashes, Probleme vor Lob,
  am Ende je «Grenzen (ehrlich)» + «Kritische Fragen». Preise nur als
  Grössenordnungen (10/2026). Kein Rechtsrat, keine Kalkulation.
- **Keine Duplikate** der Schwestern-Repos; überall mit «s. grad-…» referenziert.

## Offene Punkte (kurz; voll in OFFENE_PUNKTE.md)

1. Preise/Skalierung sind Grössenordnungen, vor Angebot neu rechnen.
2. ASR für Schweizerdeutsch (de-CH Dialekt) ist der härteste Punkt — Managed-ASR vs On-Prem je nach Datenpflicht.
3. 10-gleichzeitige-Anrufe ist meist der bindende Faktor, nicht 200/Tag; vLLM-Slots pro GPU bestimmen die GPU-Anzahl.
4. DSG/AI-Act/Aufbewahrung gehört nach grad-governance + Rechtsbeistand (kein Rechtsrat hier).
5. 24/7-SLA kostet 2 Köpfe, nicht 1; in der KMU-Default-Konfiguration ist 8h-Betrieb + 1 KI-Owner.

## Verifizierte Code-Quellen

| File | Verifiziert (19.10.2026) |
|---|---|
| `beispiele/transkript_pipeline.py` | `selftest` grün: 5 Segmente, Absicht «Bestellungsstatus», Priorität 3, PII-Masking, JSON-Serbar |
| `beispiele/skalierungsrechner.py` | `selftest` grün; `rechner 200 10` -> 1x A100-80, 800k Token/Tag |
| `beispiele/handoff_router.py` | `selftest` grün: 6 Anfall-Typen, V3->Leitende/R3, R0-R3 streng |

## Sitzungs-Matik (Sitzungsprotokoll, verbindlich)

> Wie bei `grad-platforms`: Kontext-Reinigung ist **verlustbehaftet**;
> Repo + git log ist der Recovery-Weg, NICHT das Gedächtnis.

1. **Anfang:** `PROJEKT.md` + `git log --oneline -10` LESEN — nie aus dem
   Gedächtnis weitermachen.
2. **Jeder abgeschlossene Schritt** wird im selben Turn in `PROJEKT.md`
   notiert und/oder commited — **vor** Kontextbereinigung.
3. **Ende/Blockade/Entscheidung:** Stand + Nächstes + offene Fragen
   aktualisieren, commit mit Datum.
4. **User-Korrektur -> `MEMORY.md`; wiederkehrender Ablauf -> Skill.**
5. **Verifikation** gegen Text/Lauf/Log (`git log`, `python -m py_compile`),
   nicht gegen Gefühl.

## Protokoll

| Datum | Schritt | Verifiziert |
|---|---|---|
| 19.10.2026 | Repo-Scaffold, `git init -b main`, 3 Samples geschrieben + alle `selftest` grün (3 Bugs gefunden und gefixt: Erlang-Einheiten, PII-Regex, V3-Bereich, `--anfall`-Flag) | Ja — 3 Selftests grün, `py_compile` OK |
| 19.10.2026 | 11 Dossiers `docs/` 00-10 (per Subagenten, teils direkt), `LICENSE`, `CLAUDE.md`, `.gitignore`, `ci.yml`, `OFFENE_PUNKTE.md`, `README.md`, `PROJEKT.md` | Ja — alle selftests + `py_compile` + Doku-Linkcheck grün |
| 19.10.2026 | Schreibung-Sanierung (ß→ss, latinisiert→Umlaut) über ganzes Repo; `docs/02-entscheidsmatrix.md` ergänzt (Facetten→Doku) | Ja — Audit: kein ß, keine latinisierten Formen |
| 19.10.2026 | Org-Repo `GRADweight/grad-contactcenter` angelegt, `main` gepusht + per API verifiziert (privat, alle Dateien vorhanden) | Ja — `gh api` bestätigt |

## Nächstes

- [x] 11 Dossiers auf Vollständigkeit verifiziert (read_file, byte-Zähl, kein Truncation).
- [x] `README.md`-Inhaltsverzeichnis + Grenzen + Stand-Zeile.
- [x] `CONTRIBUTING.md` + `CLAUDE.md` + `README.md`.
- [x] Commit + `gh auth setup-git` + `git push` nach `GRADweight/grad-contactcenter`.
- [x] `CODE.md`-Zeile ergänzt.
- [ ] 25.10.2026-Launch-Prüfung: sichtbar machen, wenn Edin entscheidet.
- [ ] (nächster Pass) pro Use-Case ein 20-50-Fall Quality-Testset (s. OFFENE_PUNKTE.md #8).
