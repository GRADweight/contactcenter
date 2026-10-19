# 00 - Das Unterfangen: KI im Kundendienst

> **Stand: Oktober 2026.** Modell-, Plattform- und Toolnamen wandern
> hier schneller als diese Doku (Llama, Qwen, Mistral, vLLM, faster-
> whisper - monatlich Neues). Vor jeder konkreten Entscheidung neu
> pruefen.

## 1. Die Frage, die wir hier beantworten

Der wiederkehrende Kundenwunsch im Schweizer Mittelstand ist konkret,
aber **weit**:

> «Wir wollen KI im Support. Ein Anruf kommt rein, die KI transkribiert,
> antwortet, öffnet ein Ticket, und wenn es schwierig ist, reicht es weiter.
> Wie baut man das? Was kostet es? Wie viel Aufwand? Was brauche ich an
> Skills? Was passiert mit den Aufnahmen (DSG)? Wie skaliere ich das auf
> **200 Anrufe pro Tag** oder **10 Anrufe zugleich**?»

Das ist **ein** Unterfangen. Aber es ist **kein** ein-dimensionales Problem.
Es hat mindestens **14 Facetten**, die ineinandergreifen:

| Facette | Die eine zeile wo sie gelöst wird |
|---|---|
| Projekt & Methodik | `docs/08-projekt-aufwand-kosten.md` (G0-G4, Phasen, Aufwand in FTE) |
| Technologie / Architektur | `docs/01-architektur.md` (ACD - ASR - LLM - TTS - CRM - Handoff) |
| Kosten & Aufwand | `docs/08-...` + `beispiele/skalierungsrechner.py` (Grössenordnungen) |
| Skills | `docs/09-skill-wissen-schulung.md` (Skills-Matrix nach Rolle) |
| Wissen | `docs/09-...` (ASR, RAG, vLLM, Governance, DSG - je Stufen) |
| Schulung | `docs/09-...` + `grad-training` (L1/L2/L3) |
| Betrieb | `docs/10-betrieb-ops.md` (Runbooks, 8h vs 24/7) |
| Technik (Inferenz) | `docs/04-vllm-inferenz.md` (PagedAttention, Quant, vLLM vs Ollama) |
| LLM-Transkribation | `docs/03-asr-transkribation.md` (faster-whisper, Diariz., WER) |
| vLLM & Skalierung | `docs/05-skalierung.md` + `beispiele/skalierungsrechner.py` (Erlang-C + Slots) |
| Daten-Speicherung | `docs/06-daten-speicherung.md` (Kategorien, DSG, Vektor-DB, Lifecycle) |
| Weiterleitung / Handoff | `docs/07-weiterleitung-handoff.md` (V0-V4, R0-R3, SLA, CRM) |
| Governance / Rechte | `grad-governance` (DSG, AI-Act, Gates) - **nicht** hier |
| Autonomie / Freigabe | `grad-autonomy` (Grad 01-05) - **nicht** hier definiert, nur referenziert |

**Das ist der ganze Kern:** ein Kundendienst-Projekt ist ein
Kreuzprodukt aus diesen Facetten. Wer nur eine davon bearbeitet,
baut etwas, das nicht hält.

---

## 2. Das zentrale Problem: «einfach ein Chatbot»

Die naivste, häufigste Vorstellung ist:

> «Wir stellen einen LLM davor, der die Anrufende antwortet. Fertig.»

**Das scheitert in drei Punkten**, und die drei scheitern nicht
technisch, sondern organisatorisch:

1. **Ein Chatbot ohne Quelle halluziniert.** Ein grosses Sprach-
   Modell (GPT, Claude, Qwen, Llama) weisst nicht, welche Bestellung
   Nummer 4471 hat, wer der Kunde ist, was gestern versprochen wurde.
   Ohne **RAG** (Retrieval-Augmented Generation über den Ticket-
   Store und CRM) oder ohne Tool-Zugriff erfährt es sich. Das ist
   nicht ein Fehler, es ist das Grunddesign von LLMs: sie **predikt
   Wahrscheinlichkeiten**, sie **wissen nichts** von Ihrer Firma.

2. **Ein Chatbot ohne Guardrails ist ein Sicherheitsloch.** Prompt-
   Injection (die Anrufende sagt «vergiss alle Anweisungen»),
   Kosten-Spitzen (5.000 Anfragen pro Minute an ein API-Modell),
   oder ein Leck vertraulicher Daten (DSG-Verstoss) - alles
   möglich, wenn man ein grosses Modell ungeschützt an die
   Öffentlichkeit stellt. Ein Guardrail ist kein Nice-to-have.

3. **Ein Chatbot ohne Weiterleitung ist ein Sackgasse-Support.**
   Der grösste Teil des Kundendienst-Werts liegt NICHT in der Antwort,
   sondern in der **Weiterleitung**: wann geht der Fall zum Menschen,
   in welchen Bereich (Technik, Verkauf, Logistik), mit welcher
   Priorität, und mit welcher SLA. Das ist `docs/07-weiterleitung-
   handoff.md` und `beispiele/handoff_router.py`.

**Die Konsequenz:** Was man baut ist **kein Chatbot**. Es ist ein
**geschlossener Kreis aus vier Schichten** (Architektur)
mit fünf Ausgängen (Kategorisierung, Routing, SLA, Ticket,
Audit-Trail).

---

## 3. Die Wertkette: wo liegt der eigentliche Wert

Ein Anruf durchläuft eine **Wertkette**. Jede Etappe hat einen
eigenen Wertbeitrag, eine eigene Fehlerquelle und einen eigenen
Kostentreiber:

```
  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
  │ ACD-       │  │  ASR      │  │ LLM +    │  │ TTS      │  │ CRM/     │  │ HANDOFF  │
  │ Queue      │─►│ (Sprache─ │─►│ RAG +    │─►│ (optional)│─►│ Ticket   │─►│ (Weiter- │
  │ (Anruf    │  │   Text)   │  │ Guardrail│  │          │  │ System   │  │ leitung) │
  │  annehmen)│  │           │  │          │  │          │  │          │  │          │
  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └────┬─────┘
     Queue-         Ton->Text      Antwort+          Text->Ton     ticket      ┌──► Mensch 1. Level
     Management     (Whisper)      Kategorien         (Piper)       (Postgres) │   (Fachgebiet)
     (Erlang-C)   (WER < 5%)      (Quellen, PII)                           └──► Mensch 2./3. Level
                                                                                   (SLA + Freigabe)
```

**Der Wert ist ungleich verteilt.** Die ersten beiden Etappen
(ACD-Queue, ASR) sind **Kosten-zentren** (Infrastruktur). Die
dritte (LLM + RAG) ist das **Innovationszentrum** (das Modell
selbst). Der Rest (TTS, CRM, Handoff) ist der
**Organisationszentrum** - und wo der eigentliche Kundendienst-Wert
liegt: **der Anruf wird zur richtigen Zeit, zur richtigen
Person, mit dem richtigen Kontext weitergegeben**.

**Das ist der Hauptpunkt von `docs/07-weiterleitung-handoff.md`:**
Der Wert liegt im Übergang, NICHT in der Antwort selbst. Ein
KI-System, das gut antwortet, aber schlecht weiterleitet, ist ein
**Sackgasse-System**. Ein KI-System, das wenig gut antwortet,
aber **perfekt weiterleitet**, ist ein **Kundendienst-System** -
das ist der Unterschied.

---

## 4. Warum die Facetten ineinandergreifen

Die Facetten sind nicht unabhängig. Eine Entscheidung in einer
Facette zwingt eine Entscheidung in der nächsten:

- **Daten-Kategorie (V0-V4)** bestimmt die **Region** (CH vs EU
  vs lokal), die **Aufbewahrung** (Jahre), die **Verschlüsselung**
  (at-rest, in-transit), und schlussendlich die **Inferenz-Auswahl**
  (on-prem vs Cloud): `docs/06-daten-speicherung.md`.
- **Region + Daten** bestimmen die **ASR-Wahl** (managed Deepgram/
  AssemblyAI mit EU-Hosting vs On-Prem faster-whisper): `docs/03-
  asr-transkribation.md`.
- **ASR-Ergebnis (WER)** bestimmt, **ob** Text-basiert (Chat, E-
  Mail, WhatsApp) oder **Voice** (TTS) möglich ist. Bei WER > 8%
  (Schweizerdeutsch, laut, schlecht) ist Voice-Reply oft **NICHT**
  machbar, weil der Transkriptionsfehler im Antworttext bleibt.
- **vLLM-Slots** und **GPU-Anzahl** sind durch die **Spitzenlast**
  (nicht das Tagesvolumen) limitiert: `docs/05-skalierung.md`.
- **Spitzenlast** (Erlang-C) bestimmt den **Betriebsaufwand** (1 vs
  2 Köpfe) und die **24/7-Entscheidung**: `docs/10-betrieb-ops.md`.
- **Betriebsaufwand** bestimmt, **was machbar** ist: ohne
  KI-Owner (mind. 6h/Woche) ist 24/7 nicht finanzierbar; ohne
  2 Köpfen ist 8h-Backup nicht möglich.

**Deshalb existiert dieses Repo:** nicht um «einen Chatbot» zu
bauen, sondern um **das Zusammenspiel** dieser 14 Facetten in
einem durchgehenden Dokument zu zeigen.

---

## 5. Was dieses Repo **nicht** ist

Ehrlich, denn der grösste Fehler in der Branche ist die
Vollmundigkeit:

- **Kein Rechtsrat.** DSG-/AI-Act/ISO-Fragen sind eine andere
  Disziplin. Dieses Repo sagt, **wo die Daten physisch liegen
  und wer sie sieht**; es sagt NICHT, was rechtlich erlaubt ist
  oder was die Mindest-Aufbewahrungsfrist ist. Dafür:
  `grad-governance` + ein Rechtsbeistand.
- **Keine Kalkulation.** Alle Kostenangaben sind **Grössenordnungen**
  (Stichtag 10/2026, vgl. `OFFENE_PUNKTE.md` #1). Vor jedem Angebot
  die drei Posten neu rechnen: (1) GPU-Infrastruktur, (2) Cloud-
  Storage/DSG-Aufbewahrung, (3) FTE-Personal.
- **Kein Produkt.** Dies ist ein **Referenzwerk** für die Beratung.
  Ein konkretes Projekt hat konkrete Daten, konkrete Kunden,
  konkrete SLA - das muss pro Auftrag neu verhandelt werden.
- **Kein 24/7-SLA-System.** In der KMU-Standard-Konfiguration
  ist 8h-Betrieb (Mo-Fr, Geschäftsstunden) + KI-Owner (6h/Woche)
  die richtige Antwort. 24/7 ist eine **bewusste Entscheidung**
  mit 2 Köpfen, nicht der Default: `docs/10-betrieb-ops.md`.
- **Kein Schweizerdeutsch-Dialekt-ASR aus der Dose.** Der
  Schweizerdeutsch-Dialekt ist der härteste Punkt. Kein Tool
  löst das ohne Domänen-Feintuning: `docs/03-asr-transkribation.md`.
- **Keine CRM-Anbindung pro Projekt.** Die Handoff-Logik ist
  CRM-unabhängig (`beispiele/handoff_router.py`), aber die
  physische Anbindung (HubSpot, Zoho, Salesforce, n8n, oder ein
  SQLite-Ticket-Store) ist ein separater Aufwand.

---

## 6. Die drei lauffähigen Samples

Dieses Repo trägt drei konkrete, **lauffähige** Samples
(s. `beispiele/`). Jedes hat einen `selftest`, der **ohne Netz,
ohne API-Keys, ohne externe Abhängigkeiten** (nur Python-
Standardbibliothek) grün wird - so läuft das `selftest` auch in
CI, ohne dass man ein Konto hat:

```
  beispiele/
    transkript_pipeline.py  Ton - Text - Zusammenfassung - Tickets
                            (ASR/Diariz/PII/LLM, 5 Etappen, 5 Segmente)
                            python beispiele/transkript_pipeline.py selftest
    skalierungsrechner.py   Erlang-C (ACD-Queue) + vLLM-Slots
                            (200 Anr/Tag vs 10 zugleich, 1x A100-80)
                            python beispiele/skalierungsrechner.py selftest
                            python beispiele/skalierungsrechner.py rechner 200 10
    handoff_router.py       V0-V4 / R0-R3 / Autonomiegrad / SLA / Bereich
                            (6 Anfall-Typen, R0-5 ... R3-2, 1./2./3.-Level)
                            python beispiele/handoff_router.py selftest
                            python beispiele/handoff_router.py route --anfall bestellung-ankommen
```

**Die Samples zeigen den Kern der drei Facetten** (ASR/Transkription,
Skalierung, Routing), die in den 10 Dossiers ausformuliert werden.
Die Samples sind bewusst klein (200-300 Zeilen), damit man sie
lesen und modifizieren kann - nicht um «produktionsreif» zu sein,
sondern als **Beispiel dafür, wie es aussehen könnte**.

---

## 7. Die anderen Repos (Cross-Referenzen, nicht Duplikate)

Dieses Repo steht im Netzwerk von GRADweight-Repos. Die
nachfolgenden sind **Single-Source-of-Truth**; hier referenzieren
wir, statt neu zu definieren:

| Repo | Inhalt | Wie hier referenziert |
|---|---|---|
| `grad-governance` | GRADmap - DSG, AI-Act, ISO 42001, Gates G0-G8, KI-Reifegrad | `docs/06-daten-speicherung.md`, `OFFENE_PUNKTE.md` #4 (DSG) |
| `grad-autonomy` | Autonomiegrade Grad 01-05, GB-01-16, Runtime-Bausteine | `beispiele/handoff_router.py` (R0-R3 -> Grad), `docs/07-...</handoff.md` |
| `grad-platforms` | KI-Plattform-Katalog (Foundry/Bedrock/Vertex), 4-Baustein-Ministack, GPU-Entscheid, vLLM, 5-to-1000-Personen table | `docs/04-vllm-inferenz.md`, `docs/05-skalierung.md`, `docs/01-architektur.md` |
| `grad-training` | GRADtrain - Schulung L1-L3 (Foundation, Strategie, Orchestration) | `docs/09-skill-wissen-schulung.md` (Schulung) |
| `grad-engineering` | GRADbuild - Prozess-Automation Stufen 1-4, Gates, RAG, Guardrails | `docs/01-architektur.md` (RAG/Guardrail-Prinzipien) |
| `gw-prototypen` | 25 PoCs (uv workspace), insbesondere `02-rechnungs-
   ekstraktion`, `01-...` (RAG) | `docs/03-asr-transkribation.md` (Transkriptions-Erkenntnis),
   `docs/06-daten-speicherung.md` (Vektor-DB) |

**Regel:** Wenn in der Doku eine Facette (DSG, Autonomiegrad,
Inferenz-Entscheid) erwähnt wird, **linken auf das Schwester-
repo**, statt den Inhalt hier zu wiederholen.

---

## 8. Grenzen (ehrlich)

- **Kein Rechtsrat** (DSG/AI-Act/ISO) - s. `grad-governance`
- **Keine Kalkulation** - nur Grössenordnungen, s. `OFFENE_PUNKTE.md` #1
- **Kein 24/7-SLA** im KMU-Standard - s. `docs/10-betrieb-ops.md`
- **Kein de-CH-Dialekt-ASR aus der Dose** - s. `docs/03-asr-
  transkribation.md`
- **Keine CRM-Anbindung** (physische) - pro Projekt, s. `docs/07-
  weiterleitung-handoff.md`
- **Keine Evaluierungs-Test-Suite für RAG-Antwortqualität** -
  s. `grad-platforms` `stack/evals/run.py`, `OFFENE_PUNKTE.md` #8

---

## 9. Kritische Fragen (Entscheid-Checkpunkte, NICHT To-dos)

| # | Frage | Warum |
|---|---|---|
| 1 | **Ist der erste Use-Case text-basiert oder Voice-basiert?** | Voice ist 3x teurer in Betrieb; text-First-MVP ist fast immer die bessere Wahl. |
| 2 | **Wieviele Anrufe in der GIPFELSTUNDE?** | Erlang-C braucht λ, nicht das Tagesvolumen. 200/Tag ≠ 200/Stunde. |
| 3 | **Sind Dialekt (de-CH) >5% der Anrufe?** | Bei Ja: managed-ASR mit DOMÄNEN-Feintuning oder On-Prem + Feintuning; bei Nein: managed reicht. |
| 4 | **Welche Daten sind V3/V4, und wem gehören sie?** | V4 = Geschäftsleitung; V3 = Leitende. Ohne diese Antwort: kein `handoff_router.py`. |
| 5 | **Wer ist der KI-Owner, und wie viele Stunden pro Woche?** | 6h/Woche ist das Minimum für 8h-Betrieb; weniger: das Projekt stirbt. |
| 6 | **Welches CRM, und wer betreibt den Ticket-Queue?** | Handoff-Logik ist CRM-unabhängig; die physische Anbindung ist Projektarbeit. |
| 7 | **Wie lange aufbewarten wir Transkripte, Ton und Ticket?** | DSG + Branche; Banken/Insurance haben lange Fristen; KMU haben oft 2-3 Jahre Standard. |

Siehe auch `OFFENE_PUNKTE.md` für den vollständigen Katalog.
