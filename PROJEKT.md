# PROJEKT.md  -  grad-contactcenter

**Repo:** `github.com/GRADweight/grad-contactcenter` (privat, bis Launch 25.10.2026 geprüft)
**Thema:** KI im Kundendienst / Voice-AI / Contact-Center für den Schweizer Mittelstand (KMU).
**Stand:** 19.10.2026  -  zweiter Ausarbeitungspass.

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
Dokument  -  für den Kundendienst-Fall, nicht abstrakt.**

## Ist-Zustand (19.10.2026, zweiter Pass)

```
beispiele/     (4 lauffähige Samples, alle selftest grün, nur stdlib)
  transkript_pipeline.py   Ton->Text->Zusammenf->Tickets, ASR/Diariz/PII/LLM
  skalierungsrechner.py    Erlang-C + vLLM-Slots + Ton-Latenz, 200/Tag+10 zugleich
  handoff_router.py        V0-V4 / R0-R3 / Autonomiegrad / SLA / Bereich
  qualitaetstest.py        Qualitäts-Gate Q1-Q4: Routing/Absicht/Skalierung/Sicherheit
docs/          (12 Dossiers)
  00 Unterfangen · 01 Architektur · 02 Entschiede-Matrix · 03 ASR/Transkribation
  04 vLLM · 05 Skalierung · 06 Daten · 07 Weiterleitung · 08 Projekt/Aufwand
  09 Skills/Schulung · 10 Betrieb · 11 Performance/Architektur
.github/workflows/ci.yml   CI-Gate: compile + 3 Selftests + Qualitäts-Gate
                          + Doku-Linkcheck (offline, stdlib-only)
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
2. ASR für Schweizerdeutsch (de-CH Dialekt) ist der härteste Punkt  - 
   Managed-ASR vs On-Prem je nach Datenpflicht.
3. 10-gleichzeitige Anrufe ist meist der bindende Faktor, nicht 200/Tag;
   vLLM-Slots pro GPU bestimmen die GPU-Anzahl.
4. DSG/AI-Act/Aufbewahrung gehört nach grad-governance + Rechtsbeistand
   (kein Rechtsrat hier).
5. 24/7-SLA kostet 2 Köpfe, nicht 1; in der KMU-Default-Konfiguration
   ist 8h-Betrieb + 1 KI-Owner.
6. 1x A100 80 GB reicht für 10 zugleich + 200/Tag (docs/11). Drei
   Rechner-Bugs im ersten Pass gefunden und gefixt: busy-hour-Faktor,
   Ton-Speicher (1.3 vs 732 MB/Tag), Latenz (<805 ms mit
   Prefix-Caching). Qualitäts-Gate `qualitaetstest.py` (11/11 grün).

## Verifizierte Code-Quellen

| File (2. Pass) | Verifiziert am 19.10.2026 (2. Pass) |
|---|---|
| `beispiele/transkript_pipeline.py` | selftest grün: 5 Segmente, Absicht «Reklamation»/«Bestellungsstatus», Priorität 3, PII-Masking (kontext-aware), JSON-Serbar |
| `beispiele/skalierungsrechner.py` | selftest grün: 3 Bugs gefixt (busy-hour→3 Agenten, Ton 732 MB/Tag, Latenz 805 ms), 7 Einträge im Katalog |
| `beispiele/handoff_router.py` | selftest grün: 7 Anfall-Typen, alle 4 Levels (1/2/3/GL) erreichbar, V3→Leitende/R3, kein-Cloud |
| `beispiele/qualitaetstest.py` (neu) | 11/11 grün  -  Q1 Routing, Q2 Absicht, Q3 Skalierung, Q4 Sicherheit (4 echte Defekte im 2. Pass gefixt) |
| `.github/workflows/ci.yml` | 5 CI-Schritte (compile + 3 Selftests + Qualitäts-Gate + Linkcheck), YAML-valid, lokal grün |

## Nächstes

- [x] **12 Dossiers** (00-11) geschrieben und verifiziert
- [x] `docs/11-performance-architektur.md`  -  Feasibility-Check + hardwarekonkrete Architektur
- [x] `beispiele/qualitaetstest.py`  -  Qualitäts-Gate (4 echte Defekte gefixt)
- [x] CI auf 5 Schritte erweitert
- [x] PROJEKT.md aktualisiert
- [ ] **OFFENE_PUNKTE.md #8 markieren als «abgeschlossen»** (pro-Use-Case Testset steht in qualitaetstest.py Q1-Q4)
- [ ] `CODE.md` aktualisieren (grad-contactcenter Eintrag mit 12 Dossiers)
- [ ] Commit + push nach `GRADweight/grad-contactcenter`
