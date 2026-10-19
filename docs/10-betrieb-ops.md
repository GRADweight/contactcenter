# 10 - Betrieb und Wartung

> **Stand: Oktober 2026.** 24/7-Betrieb (s. 5) ist
> *kein* Standard für KMU. 8h-Betrieb + 1
> KI-Owner (6h/Woche) ist der Standard.

## 1. 8h vs. 24/7 (die Haupt-Frage)

| Aspekt | 8h (Mo-Fr) | 24/7 |
|---|---|---|
| Köpfe | 1 KI-Owner | 2+ |
| ~CHF/Mo | 1500 | 5000-6000 |
| SLA | < 2h Antwort | < 15 min |
| RTO | 4h | 30 min |

Fuer < 500 Nutzer: 8h-Betrieb, 1 KI-Owner
(6h/Woche). 24/7 nur bei regulierter
Branche ODER vertraglich noetig.


## 2. Runbook (7 Szenarien, kurz)

| # | Szenario | Aktion | RTO |
|---|---|---|---|
| 1 | vLLM-Ausfall | Fallback-Kette (LiteLLM) auf Ollama/local | 60s |
| 2 | ASR-Ausfall | Voice->Text-Umschaltung auf Web-Chat | 30s |
| 3 | GPU OOM | Neustart vLLM mit kleinerem batch; `--max-num-seqs` reduzieren | 5min |
| 4 | Token-Kosten-Spitze | `max_budget` (LiteLLM) -> Abbruch + Log | sofort |
| 5 | Audit-Log Lücke | `audit`-Tabelle prüfen (Postgres); JSONL-Restore | 1h |
| 6 | RAG-Regression | `evals/run.py` (4/4 muss grün sein); Fallback-Antwort | 10min |
| 7 | CRM-Ausfall | Ticket-Queue via `n8n`-Retry; manuelle Notiz | 1h |
| 8 | Datenleck (V3-V4) | Sofort-Alert, `audit`-Scan, Rechtsbeistand | sofort |


## 3. Monitoring (Prometheus + Langfuse)

- **Prometheus** (CPU, GPU, RAM,
  vLLM-Metriken) s.
  `docs/04-vllm-inferenz.md` 5.
- **Langfuse** (Token, Kosten,
  Latenz, RAG-Score) s.
  `platforms/stack/observability/`.
  Fuer < 5 Pers.: Prometheus
  allein reicht.
- **PII-Monitor**: `beispiele/
  transkript_pipeline.py`
  (PII-Masking pro Anruf).
- **Audit-Log** (Postgres/JSONL)
  s. `gw_core/audit.py`.
  10 J Aufbew. (DSG-Nachweis).


## 4. Backup, DR, RTO/RPO

- **RPO** (Recovery Point
  Objekt): 1 Stunde
  (fuer V3-V4,
  Transkrip-Ton
  und Transkript-
  Text).
- **RTO** (Recovery Time
  Objekt): 4 Stunden
  (fuer 8-Betrieb,
  1 KI-Owner,
  ein Backup-
  Restore pro Woche
  getestet).
- **Backup**: `pg_dump`
  Postgres +
  `docker volume`
  + `S3-kompatibel`
  (Exoscale
  Storage).
  1x/Week
  automatisch,
  1x/Month
  Restore
  test.
- **DR**: 1x A100
  Backup
  (2 GPUs)
  bei V3-V4
  (kein SPOF).


## 5. 24/7-Betrieb (ab ~500)

Nicht Standard für KMU.
Nur wenn:

- **Regulierte Branche**
  (DSG-Höchstmuster,
  Finanzdienstleistung)
  oder
- **Vertraglich noetig**
  (SLA-Vertrag mit
  24/7-Klausel).

Kosten: **2 Köpfe**
(~5-6k CHF/Mo)
+ Hardware
(8-15k CHF/Mo,
Cluster) +
Team (80-120k
Jahreslohn,
500+ Pers.).

**Die ehrliche Antwort:**
Unter 500
Nutzern ist
24/7 kein
gültiger
Grund.

## 6. Grenzen (ehrlich)

- **Kein Rechtsrat** -
  Rechtsbeistand +
  `governance`.
- **Kein 24/7** im
  KMU-Standard.
- **Kalkulation**
  sind
  Grössenordnungen
  (10/2026, CH).
- **Langfuse**
  ist
  optional
  bei < 5 Pers.

## 7. Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | 8h oder 24/7? | < 500: 8h; > 500: 24/7 nur bei reguliert/vertraglich. |
| 2 | Wer ist KI-Owner? | 6h/Woche = Minimum; ohne ihn: kein G-Gate, kein G4. |
| 3 | Welche Runbook-Szenarien? | 7-8 abgedeckt (s. 2). |
| 4 | RTO/RPO-Grenzen? | DSG-Aufbeharungsfrist = RPO-bindend. |
| 5 | Wer betreut 24/7? | Muss separat budgetiert sein (~5-6k/Mo). |
