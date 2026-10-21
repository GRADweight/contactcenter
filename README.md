# contactcenter

> **Stand: Oktober 2026.** KI im Kundendienst / Voice-AI /
> Contact-Center für den Schweizer Mittelstand (KMU).
> **Referenzwerk, kein Gutachten.** Kein Rechtsrat, keine
> Kalkulation. Alle Zahlen sind Grössenordnungen (10/2026).
> Privat wie alle `GRADweight/*`-Repos; für den 25.10.2026-Launch
> nach Edin-Entscheidung freigegeben.
> **Repo ist CLAUDE.md-unabhängig** (kein Sample, kein CI-Gate
> liest `CLAUDE.md`; alle vier Samples laufen ohne sie  - 
> verifiziert am 19.10.2026).

## Warum dieses Repo

Ein wiederkehrender Kundenwunsch: «Wir wollen KI im Support  - 
Ton anrufen, transkribieren, Antwort, Ticket, Weiterleitung.
Wie? Wie teuer? Wie viel Aufwand? Was brauchen wir an Skills?
Was passiert mit den Daten? Wie skalieren wir auf 200 Anrufe/Tag
oder 10 gleichzeitig?»

Das ist **ein** Unterfangen, aber es hat **14 Facetten**.
Bisher zerstreut in `platforms` (Infrastruktur, Inferenz),
`governance` (DSG, AI-Act), `autonomy` (Autonomiegrade),
`training` (Schulung). **Hier werden sie zu einem
durchgehenden Dokument für den Kundendienst-Fall.**

## Die 14 Facetten (s. `docs/02`)

| # | Facette | Doku |
|---|---|---|
| 1 | Projekt / Methodik | 08 |
| 2 | Technologie / Architektur | 01 |
| 3 | Kosten | 08 |
| 4 | Aufwand (FTE) | 08 |
| 5 | Skills | 09 |
| 6 | Wissen | 09 |
| 7 | Betrieb | 10 |
| 8 | Schulung | 09 · training |
| 9 | Technik (Inferenz) | 04 |
| 10 | LLM-Transkribation (ASR) | 03 |
| 11 | vLLM | 04 |
| 12 | Skalierung | 05 |
| 13 | Daten / Speicherung | 06 |
| 14 | Weiterleitung / Handoff | 07 |
| 15 | Performance / Hardware | 11 |

## Die vier lauffähigen Samples

Alle nur mit Python-Standardbibliothek (≥ 3.9), offline,
ohne API-Key. Ein `selftest` pro Datei, grün ohne externe
Abhängigkeit.

| Sample | Was es zeigt | Facetten |
|---|---|---|
| `beispiele/transkript_pipeline.py` | Ton → Text → PII → LLM → Handoff (5 Etappen, ASR/LLM-Mock) | 10, 13 |
| `beispiele/skalierungsrechner.py` | Erlang-C + vLLM-Slots + Ton-Latenz + Kosten; 200/Tag, 10 zugleich | 3, 9, 11, 12 |
| `beispiele/handoff_router.py` | Routen pro Anfall-Typ, V0-V4, R0-R3, SLA, `darf_in_cloud` | 1, 14 |
| `beispiele/qualitaetstest.py` | Qualitäts-Gate Q1-Q4: Routing/Absicht/Skalierung/Sicherheit | 1, 10, 12 |

```bash
# 1x A100-80 für 10 zugleich (7B, vLLM, ~2600 CHF/Mo)
python3 beispiele/skalierungsrechner.py rechner 200 10
# -> Anruf-Queue:  3 Agenten, Wq 13.5 s
#    Inferenz:     1x A100-80, --max-num-seqs=250, ~2600 CHF/Mo
#    Daten/Tag:    800'000 Token, ~732 MB Ton + ~1.3 MB Text
#    Latenz:       ~805 ms (< 1500 ms Ziel, mit prefix-caching)
#    -> Die INFERENZ bestimmt die Kapazität (10 > 3)

# V3 (vertrag-aenderung) -> Leitende, R3, SLA 1d, kein Cloud
python3 beispiele/handoff_router.py route --anfall vertrag-aenderung

# Qualitäts-Gate (11 Prüfungen über alle drei Kern-Samples)
python3 beispiele/qualitaetstest.py
```

Der CI-Gate (`.github/workflows/ci.yml`) führt ab:
`py_compile` aller vier Samples, drei `selftest`, das
Qualitäts-Gate, das Demo-Szenario und einen Doku-Linkcheck.
Alles offline, ohne API-Key.

## Architektur in einem Satz

Ein **6-Etappen-Kreis** (s. `docs/01`):

```
Eintritt -> ASR/Ton->Text -> LLM+RAG -> (TTS)
          -> Ticket/CRM -> Weiterleitung -> Handoff
```

Die Etappen-Trennung ist **nicht optional**; sie ist die
eigentliche Architektur. **Der Wert liegt in der
Weiterleitung, nicht im Chat.** Jede Etappe ist ein
lauffähiges Modul, nicht ein grosses, das alles kann.
Die konkrete Hardware-Architektur für 200/Tag + 10 zugleich
steht in `docs/11`: **1x A100-80** (vLLM) + CPU-ASR +
Postgres/pgvector (V2-V4 lokal), ~800-2600 CHF/Mo.

## Grenzen (ehrlich, s. `OFFENE_PUNKTE.md`)

1. **Preise sind Grössenordnungen** (10/2026), vor jedem
   Angebot neu gegen Exoscale / Infomaniak / vLLM-Releases
   rechnen.
2. **ASR für Schweizerdeutsch (de-CH)** ist der härteste
   Punkt  -  kein Tool löst das aus der Dose; Managed-ASR mit
   CH-Hosting oder On-Prem plus Dialekt-Feintuning, je nach
   Datenpflicht.
3. **10-gleichzeitig ist der bindende Faktor, nicht 200/Tag**
    -  in der Gipfelstunde (busy-hour 0.25) braucht die
   Warteschlange nur 3 Agenten; die 10 simultanen Streams
   bestimmen die vLLM-Slots (1x A100).
4. **Ton-Speicher ist 550× grösser als Text**  - 
   ~732 MB/Tag Roh-Ton, nicht ~1.3 MB (Doku 11, §3).
   Falsch geschätzt → 550-fach zu kleines Budget.
5. **DSG/AI-Act gehört nach `governance` +
   Rechtsbeistand**  -  dieses Repo sagt WO die Daten liegen
   und WER sie sieht, nicht was rechtlich erlaubt ist.
6. **24/7 ist 2 Köpfe, nicht 1**  -  8h-Betrieb + 1 KI-Owner
   (6h/Woche) ist der KMU-Default.
7. **Autonomiegrade (Grad 01-05)** werden aus
   `autonomy` referenziert, nicht hier neu definiert.

## Kritische Fragen

Die zwei wichtigsten (voll in `OFFENE_PUNKTE.md`):

1. **Welche Daten sind V3/V4 für diese Firma, und was ist die
   Mindest-Aufbewahrungfrist?**
2. **Wer ist der KI-Owner und wie viele Stunden pro Woche hat
   er?**

Ohne Antwort laufen `handoff_router.py` bzw.
``skalierungsrechner.py`` nicht.

## Repo-Struktur

```
beispiele/
  transkript_pipeline.py     ASR/Diariz/PII/LLM/Handoff (5 Etappen)
  skalierungsrechner.py      Erlang-C + vLLM-Slots + Ton + Latenz
  handoff_router.py          V0-V4 / R0-R3 / SLA / Bereich
  qualitaetstest.py          Qualitäts-Gate Q1-Q4 (4 echte Defekte gefixt)
docs/                        12 Dossiers 00-11 (s. 02-entscheidsmatrix.md)
.github/workflows/ci.yml      compile + 3 selftest + Qualität + Linkcheck
LICENSE                       MIT (Code) + kommerzielle Vorbehalte, GRADweight GmbH
PROJEKT.md · OFFENE_PUNKTE.md · CONTRIBUTING.md · CLAUDE.md · README.md
(CLAUDE.md ist optional  -  kein Sample/CI-Gate hängt daran)
```

## Verifizierte Code-Quellen (alle am 19.10.2026 grün, 2. Pass)

| File | Status |
|---|---|
| `beispiele/transkript_pipeline.py` | selftest: 5 Segmente, Absicht erkannt, Priorität 3, PII-Masking (kontext-aware), JSON-Serbar |
| `beispiele/skalierungsrechner.py` | selftest + `rechner 200 10` → 3 Agenten, 1x A100-80, 732 MB Ton, 805 ms Latenz |
| `beispiele/handoff_router.py` | selftest: 7 Anfall-Typen, alle 4 Levels, V3 → Leitende/R3, kein-Cloud |
| `beispiele/qualitaetstest.py` (neu) | 11/11 grün  -  Q1 Routing, Q2 Absicht, Q3 Skalierung, Q4 Sicherheit |

## Kontakt

**MIT** für den Code (frei nutzbar) + **kommerzielle Vorbehalte**
(GRADweight GmbH): Kalkulationsmaterial liegt intern (`GRADweight/
preismaterial`).
Fragen: mail@gradweight.ch
