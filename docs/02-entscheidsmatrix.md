# 02 Entscheidsmatrix: Welche Frage wird wo beantwortet?

| Frage | Dossier | Code | Nachweis |
|---|---|---|---|
| Architektur und Datenfluss | [01](01-architektur.md), [12](12-zielarchitektur.md) | `gw_contactcenter/` | `tests/test_pipeline_sim.py` |
| Telefonie (Genesys) | [12](12-zielarchitektur.md) | `audiohook.py`, `signatur.py`, `server.py` | `tests/test_audiohook.py`, `tests/test_signatur.py`, `tests/test_server.py` |
| Turn-Taking, Barge-in | [12](12-zielarchitektur.md), [15](15-latenz.md) | `turn.py`, `pipeline.py` | `tests/test_turn.py` |
| Spracherkennung | [03](03-asr-transkribation.md) | `adapters/openai_compat.py` | `tests/test_adapter_http.py` |
| Inferenz und Modellbetrieb | [04](04-vllm-inferenz.md) | `deploy/compose.yaml` | `tests/test_deploy.py` |
| Personal und Kanäle | [05](05-skalierung.md) | `warteschlange.py`, `kapazitaet.py` | `tests/test_warteschlange.py`, `tests/test_warteschlange_sim.py` |
| Daten und Speicher | [06](06-daten-speicherung.md) | `policy.py`, `pii.py`, `kapazitaet.py` | `tests/test_pii.py`, `tests/test_kapazitaet.py` |
| Weiterleitung | [07](07-weiterleitung-handoff.md) | `policy.py`, `dialog.py` | `tests/test_dialog.py`, `evals/` |
| Projekt und Kostentreiber | [08](08-projekt-aufwand-kosten.md) | - | - |
| Rollen und Kompetenzen | [09](09-skill-wissen-schulung.md) | - | - |
| Betrieb | [10](10-betrieb-ops.md) | `telemetry.py`, `audit.py`, `deploy/` | `tests/test_telemetry.py`, `tests/test_audit.py` |
| GPU-Dimensionierung | [11](11-performance-architektur.md) | `sizing.py` | `tests/test_sizing.py` |
| Begriffe | [13](13-technologien-erklaert.md) | - | - |
| Speech-to-Speech-Modelle | [14](14-dessa-und-full-duplex.md) | - | - |
| Latenz | [15](15-latenz.md) | `telemetry.py`, `sim.py`, `latenzmessung.py` | `tests/test_latenzmessung.py` |
| Leitplanken | [16](16-leitplanken.md) | `leitplanken.py`, `gateway.py`, `tools.py` | `tests/test_leitplanken.py`, `evals/injection_holdout.json` |

Alle Nachweise laufen offline, ohne GPU und ohne Zugangsdaten (`pytest`,
`python evals/run_evals.py`).

## Grenzen

- Die Matrix zeigt, wo eine Frage behandelt wird, nicht, dass sie für ein konkretes Projekt
  beantwortet ist.

## Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Welche Zeile ist für das eigene Projekt die riskanteste? | Dort zuerst eigene Daten erheben. |
| 2 | Welche Nachweise braucht die Abnahme zusätzlich? | Eigene Testaufnahmen, eigenes Golden-Set, Lasttest auf der Zielhardware. |
