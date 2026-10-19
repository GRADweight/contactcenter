# 02 - Entscheidungs-Matrix

> **Stand: Oktober 2026.** Kein
> Rechtsangebot. Zeigt,
> in welcher Doku
> (00-10) jede
> Facette gelöst
> wird und welches
> von drei Samples
> lauffähig ist.

## 1. Die 14 Facetten

| # | Facette | Doku |
|---|---|---|
| 1 | Projekt / Methodik | 08 |
| 2 | Technologie / Architektur | 01 |
| 3 | Kosten | 08 |
| 4 | Aufwand (FTE) | 08 |
| 5 | Skills | 09 |
| 6 | Wissen | 09 |
| 7 | Betrieb | 10 |
| 8 | Schulung | 09 + grad-training |
| 9 | Technik (Inferenz) | 04 |
| 10 | LLM-Transkribation | 03 |
| 11 | vLLM | 04 |
| 12 | Skalierung | 05 |
| 13 | Daten / Speicherung | 06 |
| 14 | Weiterleitung | 07 |

## 2. Die 3 Samples

| Sample | Facetten |
|---|---|
| `transkript_pipeline.py` | 10 (ASR), 13 (Daten) |
| `skalierungsrechner.py` | 3, 9, 11, 12 |
| `handoff_router.py` | 1, 14 |

Jedes Sample
hat `python
beispiele/<name>.py
selftest`
für
eine
lokale
Prüfung
ohne
Netz.

## 3. Grenzen

- Kein Rechtsrat
  (DSG/AI-Act:
  `grad-governance`).
- Keine Kalkulation
  (Grössenordnungen,
  s. 08).
- Kein 24/7 im
  KMU-Standard
  (s. 10).

## 4. Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Welche Facette im MVP? | 1 = 4-8 Wochen; alle = nie. |
| 2 | Welche Doku zuerst? | 01 -> 03/04/10 -> Rest. |
| 3 | Welches Sample pro Facette? | Matrix in 1 und 2. |
| 4 | Wer prüft was (G-Gates)? | L2 = Prüfer (s. 09). |
| 5 | Ist 24/7 nötig? | Nur > 500 (s. 10). |
