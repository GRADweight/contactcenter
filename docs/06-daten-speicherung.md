# 06 - Daten-Speicherung und Aufbewahrung

> **Stand: Oktober 2026.** Kein Rechtsangebot. Vor jeder
> Entscheidung Rechtsbeistand + `grad-governance` pruefen.

## 1. Die 5 Daten-Kategorien

Ein KI-Kundendienst speichert 5 Dinge - jede mit
anderer Frist, anderem Ort, anderem Zugang:

| Kat. | Beispiel | DSG-Stufe | Aufbew. | Lager |
|---|---|---|---|---|
| 1. Ton (16 kHz) | Wave-Datei | Hoechste | 10-30 J | lokal / CH-Cloud |
| 2. Transkript | Text | Hoch | 3-10 J | Postgres / CH |
| 3. Ticket | Antwort, SLA | Mitt | 5-7 J | CRM / Postgres |
| 4. RAG-Index | Chunks, Embed | Niedr | 1-3 J | Qdrant / pgvector |
| 5. Audit | Wer/Was/Wann | Hoechst | 10 J | append-only |

**Der häufigste Fehler:** alle 5 *gemeinsam* aufbewahren.
Ton-File mit Name/Adresse/Konto (Kat. 1+2+3) darf
NICHT im selben Bucket wie eine FAQ (Kat. 4) liegen.
Jede Kategorie hat ein eigenes Verschuersselungs- und
Loesch-Profil.


## 2. DSG / DSGVO (Kurz, nicht rechtlich)

- **DSG (CH, 2023):** Keine
  Pflichtfrist. Art. 8 verlangt
  Verantwortlichkeit, also eine
  **Aufbewarungs-Regel** pro
  Kategorie. Banken 10 J, Insurance
  30 J (DSG + Branchenrecht).
- **DSGVO (EU):** Keine Pflichtfrist,
  aber Verhaeltnismaessigkeit
  (nicht laenger als noetig).
- **AI-Act (EU, 2024):** Kat-1
  (Hoch-Risiko) = 10 J Audit.
- **CH-Spezifik:** DSG verlangt
  keine Pflicht-Aufbeharung, aber
  jede Kat. muss geregelt sein.
  Ohne Regel = DSG-Verstoss Art. 8.


## 3. Wo speichern (CH-Region)

| Option | CH? | DSG | Fuer |
|---|---|---|---|
| On-Prem (Mac/Server) | Ja | Hoechste | V3-V4, klein |
| Exoscale CH | Ja | Hoch | 50-500 Pers. |
| Infomaniak (Lutry) | Ja | Hoch | KMU |
| Azure CH-SWISS | Ja | Hoch | Enterprise |
| OpenAI/Anthropic | Nein (US) | Nein | V0-V1 nur |

**Die Regel:** V3-V4 (vertraulich/streng)
müssen **in CH** (On-Prem oder
CH-Cloud). V0-V1 (öffentlich/normal)
können in EU/US. Der Router
(`beispiele/handoff_router.py`,
`darf_in_cloud`) entscheidet
pro Ticket:

```
V0 -> alle (inlc. Cloud)
V1 -> Cloud ok
V2 -> CH-Cloud / On-Prem
V3 -> On-Prem / CH-Bare-Metal
V4 -> On-Prem, 2x Backup
```


## 4. Vektor-DB: pgvector vs. Qdrant

| | pgvector | Qdrant |
|---|---|---|
| Daten | < 1M Chunks | > 1M |
| p95 | < 100ms (1M) | < 100ms (10M) |
| Permission | `access_level` Int | `access_level` Int |
| Fuer | < 250 Pers. | > 250 |
| ~CHF/Mo | 50-80 | 300-500 |
| Wechsl | < 250: nicht noetig | 250+: zwingend |

**Der Wechselpunkt** (~1M Chunks,
< 100ms p95; s.
`grad-platforms/selbstbau-
blupause.md`): pgvector ist
der Flaschenhals, Qdrant
zwingend. Pfad: 2-zeiliger
Adapter, ein Nachmittag.


## 5. Datenlebenszyklus

```
Ton -> Transkript (ASR)
    -> PII-Mask (Name/Konto)
    -> Chunking -> Embedding
    -> VektorDB (Qdrant/pgvector)
    -> Retrieval (RAG)
    -> Antwort (LLM + Quellen)
    -> Ticket (Postgres/CRM)
    -> Audit (JSONL, append-only)
    -> Rotation (Jahre pro Kat., Loeschprotokoll)
```

Jede Etappe loggt ihre Quelle
(s. `gw_core/audit.py`).
Ohne Quelle = keine Antwort
(s. `docs/01-architektur.md` §4.2).


## 6. Grenzen (ehrlich)

- **Kein Rechtsrat** - DSG/
  DSGVO/AI-Act: Rechtsbeistand
  + `grad-governance`.
- **PII-Masking** im Sample ist
  Regex (grob); Produktion:
  Presidio + NER.
- **Kalkulation** (CHF) sind
  Groessenordnungen,
  s. `OFFENE_PUNKTE.md` #1.
- **DSG-Aufbeharungsfristen**
  sind pro Branche; die
  Tabelle ist Anfallskompakt.

## 7. Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Welche Kat. (V0-V4) pro Datentyp? | Ohne Kat. = kein routing. |
| 2 | Wo speichern (On-Prem/CH/EU)? | Bei DSG-Gepraech: V3-V4 in CH. |
| 3 | Wie lang Aufbew. (Branche)? | BANK 10 J, INSUR 30 J, KMU 2-3 J. |
| 4 | pgvector oder Qdrant? | <1M Chunks: pgvector; >1M: Qdrant. |
| 5 | Wer sieht den Audit-Log? | Ohne klaren Zugang = Leichenschrein. |
