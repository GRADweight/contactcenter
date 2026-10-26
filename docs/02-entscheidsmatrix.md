# 02 - Entscheidungs-Matrix

> **Stand: Oktober 2026.** Kein Rechtsrat. Zeigt, in welcher Doku jede
> Facette gelöst wird und welcher Code sie lauffähig macht.

## 1. Die 18 Facetten

| # | Facette | Doku | Code |
|---|---|---|---|
| 1 | Projekt / Methodik | 08 | - |
| 2 | Technologie / Architektur | 01, **12** | `voiceagent/` |
| 3 | Kosten | 08 | `voiceagent/sizing.py` |
| 4 | Aufwand (FTE) | 08 | - |
| 5 | Skills | 09 | - |
| 6 | Wissen | 09, **13** | - |
| 7 | Betrieb | 10 | `deploy/`, `voiceagent/telemetry.py` |
| 8 | Schulung | 09 + training | - |
| 9 | Technik (Inferenz) | 04 | `voiceagent/gateway.py` |
| 10 | LLM-Transkribation (ASR) | 03 | `voiceagent/adapters/` |
| 11 | vLLM | 04 | `deploy/compose.yaml` |
| 12 | Skalierung | 05, 11 | `voiceagent/sizing.py`, `beispiele/skalierungsrechner.py` |
| 13 | Daten / Speicherung | 06 | `voiceagent/gateway.py` (PII), `voiceagent/audit.py` |
| 14 | Weiterleitung / Handoff | 07 | `voiceagent/policy.py`, `voiceagent/dialog.py` |
| 15 | Performance / Hardware | 11 | `voiceagent/sizing.py` |
| 16 | Echtzeit-Telefonie (Genesys) | **12** | `voiceagent/audiohook.py`, `server.py` |
| 17 | Turn-Taking, Barge-in | **12, 14** | `voiceagent/turn.py`, `pipeline.py` |
| 18 | Qualität (Evals) | **12** | `evals/`, `tests/` |

## 2. Lauffähiger Code

| Code | Facetten | Prüfung |
|---|---|---|
| `voiceagent/` (Paket) | 2, 3, 7, 9-18 | `python -m unittest discover -s tests` |
| `evals/run_evals.py` | 14, 18 | Gate: Sicherheit 100 %, Übergabe >= 95 % |
| `beispiele/transkript_pipeline.py` | 10, 13 | `selftest` |
| `beispiele/skalierungsrechner.py` | 3, 9, 11, 12 | `selftest` |
| `beispiele/handoff_router.py` | 1, 14 | `selftest` |
| `beispiele/qualitaetstest.py` | 1, 10, 12 | 12 Prüfungen |

Alles läuft offline, ohne GPU und ohne API-Key.

## 3. Grenzen

- Kein Rechtsrat (DSG/AI-Act: `governance`).
- Keine Kalkulation (Grössenordnungen, s. 08).
- Kein 24/7 im KMU-Standard (s. 10).

## 4. Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Welche Facette im MVP? | 1 = 4-8 Wochen; alle = nie. |
| 2 | Welche Doku zuerst? | 12 (Überblick) -> 01/03/04 -> Rest. |
| 3 | Welcher Code pro Facette? | Matrix in 1 und 2. |
| 4 | Wer prüft was (G-Gates)? | L2 = Prüfer (s. 09); technisch: Evals-Gate. |
| 5 | Ist 24/7 nötig? | Nur > 500 Anrufe/Tag (s. 10). |
