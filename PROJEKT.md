# PROJEKT.md  -  contactcenter

**Repo:** `github.com/GRADweight/contactcenter` (privat)
**Thema:** KI im Kundendienst / Voice-AI / Contact-Center für den Schweizer Mittelstand (KMU).
**Stand:** 26.10.2026  -  Version 2 (lauffähiger Sprachagent).

## Warum dieses Repo

Ein wiederkehrender Kundenwunsch: «Wir wollen KI im Support: Ton anrufen,
Transkribieren, Antwort, Ticket, Weiterleitung. Wie? Wie teuer? Wie viel
Aufwand? Was brauchen wir an Skills? Was passiert mit den Daten? Wie
skalieren wir auf 200 Anrufe/Tag oder 10 gleichzeitig?»

Das ist **ein** Unterfangen, aber es **hat viele Facetten**: Projekt,
Technologie, Kosten, Aufwand, Skills, Wissen, Betrieb, Schulung, Technik,
LLM-Transkribation, vLLM, Skalierung, Daten-Speicherung, Weiterleitung.
Bisher zerstreut in `platforms` (Infrastruktur/Inferenz),
`governance` (DSG/AI-Act/Gates), `autonomy` (Autonomiegrade),
`training` (Schulung). **Hier werden sie zu einem durchgehenden
Dokument  -  für den Kundendienst-Fall, nicht abstrakt.**

## Ist-Zustand (26.10.2026, Version 2)

```
voiceagent/    lauffähiger Sprachagent, Kern nur stdlib (s. docs/12)
               Genesys AudioHook, semantisches Turn-Taking, Barge-in,
               LLM-Gateway (Residenz, PII-Tresor, Injection, Wächter),
               Werkzeuge mit Bestätigung, Audit-Hash-Kette, Sizing, Simulator
evals/         Golden-Set 20 Fälle + Gate (Sicherheit 100 %, Übergabe >= 95 %)
tests/         70 Tests (Unit, Integration, E2E inkl. WebSocket-Server)
deploy/        Compose: Caddy, voiceagent, vLLM, speaches, Prometheus/Grafana
beispiele/     v1-Samples, korrigiert (GPU-Wahl, Telefon-Audio, Latenz)
docs/          15 Dossiers 00-14 (neu: 12 Zielarchitektur, 13 Technologien,
               14 Dessa/Full-Duplex)
.github/workflows/ci.yml   8 Schritte: compile, Selftests, Q-Suite, Rechner,
                           Tests, Evals, Simulation, Linkcheck
```

## Entschieden

- **Name:** `contactcenter` (grad- Prefix, breit: Sprache + Chat + alle Facetten).
- **Sichtbarkeit:** privat wie alle Org-Repos; für 25.10.2026-Launch-Prüfung freigegeben (nach Edin-Entscheidung).
- **Härte der Technik:** lauffähige Samples mit `selftest` (grad-platforms-Muster).
- **Hausstil:** Schweizer Hochdeutsch, «ss», keine Em-Dashes, Probleme vor Lob,
  am Ende je «Grenzen (ehrlich)» + «Kritische Fragen». Preise nur als
  Grössenordnungen (10/2026). Kein Rechtsrat, keine Kalkulation.
- **Keine Duplikate** der Schwestern-Repos; überall mit «s. grad-…» referenziert.

## Offene Punkte (kurz; voll in OFFENE_PUNKTE.md)

1. Preise/Skalierung sind Grössenordnungen, vor Angebot neu rechnen.
2. Schweizerdeutsch-ASR ist der härteste Punkt (Doku 03, 13).
3. Nur Genesys als Telefonie; SIP/Asterisk-Adapter fehlt.
4. AudioHook-Signaturprüfung vor Produktion ergänzen.
5. DSG/AI-Act/Aufbewahrung gehört nach governance + Rechtsbeistand.
6. 24/7-SLA kostet 2 Köpfe, nicht 1.

## Korrekturen in Version 2

| v1 | v2 |
|---|---|
| 1x A100 80 GB für 10 Anrufe | 1x L4 24 GB (KV-Cache + Duty Cycle gerechnet) |
| Ton 16 kHz/16 bit (732 MB/Tag) | G.711 Stereo 8 kHz (366 MB/Tag), Archiv Opus |
| Latenz ohne Endpointing, Ziel 1.5 s | mit Endpointing gemessen, Ziel 1 s |
| Gipfelstunde 5/h in OFFENE_PUNKTE #3 | 50/h, konsistent mit Doku 05/11 |
| «14 Facetten» mit 15 Zeilen | 18 Facetten, eine Matrix (Doku 02) |
| Lizenz MIT vs. «Alle Rechte vorbehalten» | Code MIT, Vorbehalte nur für Beratung/Kalkulation |

## Nächstes

- [ ] Golden-Set mit 50-200 echten, anonymisierten Fällen eines Pilotkunden
- [ ] Evals gegen echtes Modell (`--llm-url`) und Ergebnis dokumentieren
- [ ] SIP-Adapter (Asterisk AudioSocket oder LiveKit SIP) für KMU ohne Genesys
- [ ] Streaming-ASR-Adapter (Partials) für ~300 ms Endpointing auch bei Batch-ASR-Kunden
- [ ] RAG mit Postgres + pgvector statt Demo-Wissensbasis
