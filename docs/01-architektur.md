# 01 - Architektur: ein KI-Kundendienst als geschlossener Kreis

> **Stand: Oktober 2026.** vLLM, Ollama, LiteLLM, Langfuse, faster-
> whisper - alle in schnellem Wandel. Vor jeder Deployment die
> aktuelle Version pruefen.

---

## 1. Die Grundfrage

Was ist ein KI-Kundendienst, *physikalisch*?

Die Antwort: **ein geschlossener Kreis mit sechs Etappen**, vier
Ausgängen (Kategorisierung, Routing, SLA, Audit-Trail) und drei
moeglichen Eingängen (Telefon, Web-Chat, E-Mail/WhatsApp).

Die *einzige* Eigenschaft, die das System von einem Sackgasse-
System unterscheidet, ist die **sechste Etappe (Handoff)**. Kein
KI-System ohne Handoff ist ein Kundendienst-System. Kein Handoff-
System ohne Vor-Antwort ist ein KI-Kundendienst-System (das ist
nur ein Ticket-System).

---

## 2. Die sechs Etappen

```
 Eingang        Etappe 1     Etappe 2    Etappe 3
 -------  ->  ACD-Queue  ->  ASR     ->  LLM + RAG
 -------        (Anruf        (Sprache     (Antwort +
 -------         akzeptieren,   -> Text)      Quellen +
 -------        klassifiz.)                    Kategorien
                                            + PII-Masking)

   -> Etappe 4 -> Etappe 5 -> Etappe 6
   TTS         CRM /        HANDOFF
   (option.)   Ticket      (Weiter-
                       (Postgres,   leitung)
                       SQLite,       -> Mensch
                       n8n)        1./2./3.
                                     Level + SLA
                                     + Freigabe
```

### Etappe 1 - ACD-Queue (Anruf akzeptieren, klassifizieren)

**Was:** Ein Anruf kommt rein. Die ACD (Automatic Call Distribution)
nimmt den Anruf entgegen, wartet in der Queue und klassifiziert
den Anfall (Bestellung, Reklamation, Preis, Vertrag, Konto
...). Die Klassifikation ist der *Einstieg* in den
Handoff-Router (`beispiele/handoff_router.py`) und bestimmt
die SLA.

**Tool:** Ein Standard-Telefon-System (Asterisk, FreePBX,
Cisco) oder ein Managed-ACD (Twilio, Vonage, Infobip).
Die Klassifikation kann regelbasiert sein (IVR: druecke 1
 fuer A, 2 fuer B) oder KI-basiert (die Anrufende sagt
 «Meine Bestellung ist nicht angekommen» -> `handoff_router.py`).

**Wichtig:** Die Wartezeit in der Queue wird von **Erlang-C**
(s. `beispiele/skalierungsrechner.py`) bestimmt. 200 Anrufe
pro Tag haben *keine* spuerbare Wartezeit: in der GIPFELSTUNDE
(25% des Tages = 50 Anrufe/Stunde) reichen ~1-2 Agenten fuer
20 Sekunden Wartezeit. Die 10 *gleichzeitig* wärenden Anrufer
sind eine *separate* Kapazitätsentscheidung (vLLM-Slots,
s. Etappe 3, `docs/05-skalierung.md`).

### Etappe 2 - ASR (Sprache -> Text)

**Was:** Ton wird zu Text transkribiert. In der Schweiz:
`faster-whisper` (CTranslate2, GPU/CPU) fuer on-prem, oder
`Deepgram` / `AssemblyAI` fuer managed (mit EU/CH-Hosting-
caveats). Der Transkriptionsfehler (WER, s. `docs/03-asr-
transkribation.md`) bleibt in der Antwort.

**Diarisierung** (wer spricht wann): `pyannote.audio`. Fuer
Telefon: in der Regel 2 Sprecher (Agent, Kunde). Fuer den
Handoff-Context wird die Sprecher-Trennung gebraucht.

**PII-Masking vor Speicherung:** ein Regex- oder Presidio-
Filter entfernt Kontonummern, Kreditkarten, Name, Adresse
aus dem Transkript *vor* es in den Ticket-Store kommt
(s. `beispiele/transkript_pipeline.py` `mask_piis`).

**Wichtig:** Bei **Schweizerdeutsch** (Dialekt) liegt die WER
(Word Error Rate) 8-15% ohne Feintuning, gegenüber 2-5%
fuer Hochdeutsch. Das ist der *häufigste* Grund, warum
Schweizer KMU-Projekte scheitern - nicht die LLM-Antwort,
sondern die Transkription (s. `docs/03-asr-transkribation.md`).

### Etappe 3 - LLM + RAG + Guardrails (Antwort + Quellen)

**Was:** Das (geschützte) Transkript geht in den LLM mit
RAG-Kontext (relevante Tickets, Produktdoku, FAQ). Die
Antwort ist ein **JSON** mit Feldern: `Antwort`, `Kategorien`,
`Priorität`, `Freigabe` (Ja/Nein/Teilweise), `Handoff-Ziel`,
`Quellen`. Nicht ein freier Chat-Text.

**LLM-Wahl** (s. `docs/04-vllm-inferenz.md`, `grad-
platforms`):
- *Stufe A (KMU, < 10M Token/Tag):* Cloud-API (OpenAI
  GPT-4o-mini, Anthropic Claude, Mistral, Qwen via vLLM).
  Billigster Einstieg.
- *Stufe B (DSG-Höchststrich):* On-Prem vLLM mit Qwen 3.8
  27B/35B-A3B (Edins Setup) oder Llama-4 / Mixtral,
  CH-Region. Kein Cloud-Aufruf.
- *Stufe A/B gemischt:* Router (LiteLLM) mit Fallback-Kette
  (s. `platforms` `beispiele/model_router.py`).

**RAG:** Vektor-DB (pgvector oder Qdrant, s. `docs/06-daten-
speicherung.md`), 20-40 Chunks je Anfrage, *Permission-Aware*
(niedrige Access-Levels sehen keine internen Chunks,
s. `platforms` `stack/services/rag/app.py`).

**Guardrails (nicht optional, s. `governance`):**
- *Prompt-Injection-Schutz:* Input-Filter (NeMo Guardrails,
  `guardrails`, Regex, oder ein klassifizierendes
  Small-Modell). Praeparierte Eingänge («vergiss alle
  Anweisungen», «du bist jetzt kein KI-System») werden
  erkannt und blockiert.
- *Rate-Limiting:* Max 10 Anfragen / Minute / Key
  (Standard je `beispiele/model_router.py`).
- *Konfidenz-Routing:* < 0.7 Konfidenz -> menschliche
  Pruefung; > 0.9 -> autonom (autonomy, Grad 03-04).
- *Audit-Trail:* Jede Anfrage loggt wer/was/wann (Postgres
  `audit`, oder JSONL, s. `gw_core/audit.py`).
- *DSG-Filter:* Sensitive Daten (V3-V4) gehen NIE in
  Cloud-API (nur On-Prem vLLM; s. `OFFENE_PUNKTE.md` #4).

### Etappe 4 - TTS (optional: Text -> Stimme)

**Was:** Bei Voice-Reply (nicht nur Text): die LLM-Antwort
geht in ein TTS (Text-to-Speech). Tools: `Piper` (leicht,
lokal, gut fuer CH-de), `Coqui TTS`, `ElevenLabs` (API,
teuer), `Azure TTS` / `Google TTS` (API).

**Wichtig:** Bei **de-CH-Dialekt** ist TTS noch ungeschliffener
als ASR. Hochdeutsch klingt in CH falsch - aber fuer technische
Information (Bestellungsstatus) reicht Hochdeutsch oft.
Voice-Reply ist der *am schwierigsten zu betreuende* Block
im System: Latenz (First-Token + TTS < 500 ms fuer
menschliches Gespräue), Stimmerkennung, Dialekt
(s. `docs/03-asr-transkribation.md` § TTS).

**Die billigste KMU-Route:** Text-First starten (Chat,
E-Mail, WhatsApp) und Voice-Reply HINZUfuegen, nicht umgekehrt.

### Etappe 5 - CRM / Ticket-System

**Was:** Antwort + Kategorien + Priorität + Handoff-Ziel
werden in ein Ticket-System geschrieben. Tool-Wahl:
HubSpot, Zoho, Salesforce, Freshdesk, oder ein einfaches
`PostgreSQL` + `n8n` fuer den Workflow.

**Wichtig:** Der Ticket-Store ist die *einzige persistente
Quelle*. Transkript (oder Hash), Quellen, Antwort,
Kategorien, SLA, Handoff-Zustand - alles in einer
Zeile. Ohne persistenten Ticket-Store: kein Audit-Trail,
keine SLA-Messung, kein Betrieb.

**Permission-Aware:** V3/V4-Tickets sehen nur bestimmte
Rollen (s. `beispiele/handoff_router.py` `zuständig
bereich`, `verbindend_fuer`). V2 -> Fachbereich; V1 -> Agent;
V0 -> alle.

### Etappe 6 - HANDOFF (Weiterleitung)

**Was:** Der Ticket (oder der Anruf selbst, bei Voice-Reply
nach der KI-Antwort) geht an den *nächsten Bereich*:
- V0-V1 -> 1. Level (Agent, automatisiert beantwortet)
- V2-V3 -> 2. Level (Fachbereich, SLA < 2d)
- V4 -> 3. Level (Leitende / Gechaefsleitung, SLA < 1d)

Mit **Autonomiegrad** (s. `autonomy`): R0 (gering,
Grad 5, keine Zwischenfreigabe) bis R3 (kritisch, Grad 2,
Zwischen-Freigabe). Die SLA wird pro Ticket-Kategorie
gesetzt (s. `beispiele/handoff_router.py` `sla_tag`).

**Wichtig:** Der **Handoff ist der Wert** des Systems.
Nicht der Chat, nicht die Antwort - der *Über* zur
richtigen Person. Das ist `docs/07-weiterleitung-handoff.md`.

---

## 3. Die drei Eingänge

Ein Kundendienst bekommt Anfragen aus *drei* Quellen. Die
Architektur ist ab Etappe 1 fuer alle drei *identisch*;
unterschiedlich ist nur das Eingangsformat:

| Eingang | Etappe 1 | Etappen 2-6 | Wie |
|---|---|---|---|
| **Telefon** | ACD-Queue (Erlang-C) | + ASR (faster-whisper) -> ... | Ton-Stream |
| **Web-Chat** | Web-Socket-Queue | Text-Input direkt (kein ASR) | Text |
| **E-Mail / WhatsApp** | Mail-Server / API | Text-Input direkt (kein ASR) | Text |

**Die Konsequenz:** Text-Eingaenge (Chat, E-Mail) brauchen
*kein* ASR, also keine Ton-Infrastruktur und sind 3-5x
billiger im Betrieb als Voice-Reply (TTS + ASR-Infrastruktur).
**Die billigste KMU-Route ist Text-First: E-Mail-Triage
+ Web-Chat.**

---

## 4. Die GRADweight-Prinzipien (s. `engineering`)

Jede Etappe folgt den GRADweight-Architektur-Prinzipien
(aus `engineering/docs/architektur.md`):

**4.1 Kein Big-Ball-of-Mud.** Jede Etappe ist ein
*spezialisiertes* Modul mit eigener Identität, eigenen
Testfaellen (min. 5 pro Etappe) und eigenem Audit-Log.
NICHT ein grosses LLM, das alles macht - das ist der
häufigste Architekturfehler der Branche.

**4.2 RAG ohne Quellenangabe = keine Antwort.** Niemals
eine Antwort ohne Quelle zurueckgeben. Jede Antwort
enthaelt Text + mindestens *eine* Quellenangabe
(Ticket-ID, Doku-Seite, FAQ-Eintrag). Die Quelle wird
im Prompt gefordert und im Output validiert
(`gw_core/verify.py`). Ohne Quelle: «Ich habe keine
verlaessliche Quelle» statt Erfinden.

**4.3 Guardrails sind nicht optional.** Fuenf Guardrails
pro Etappe (s. Etappe 3): Injection-Schutz, Rate-
Limiting, Konfidenz-Pruefung, Audit-Trail, DSG-Filter.
Keine davon ist ein Nice-to-have.

**4.4 Modell-Router (Anti-Lock-In).** Die App ruft nie ein
Modell direkt auf, sondern einen *Router* (LiteLLM oder
`beispiele/model_router.py` von `platforms`). Der
Router kennt pro Route eine Fallback-Kette
(OpenAI -> Anthropic -> Ollama -> vLLM lokal). Ein
Modell-Wechsel ist eine *Config-Änderung*, kein Projekt.
Ein Cloud-Ausfall (OpenAI down) faellt nicht: die Kette
wechselt automatisch.

**4.5 Audit-Trail (wer/was/wann).** Jede LLM-Aktion loggt
append-only (Postgres `audit` oder JSONL):
- **Wann:** Zeitstempel UTC (ISO 8601)
- **Was:** Input (Hash) + Output (gekuertzt, bei
  sensiblen Daten nur Hash) + Modell + Latenz + Status
- **Warum:** welche RAG-Quellen, welche Regeln aktiv
- **Wer freigegeben hat:** bei Stufe 2+ (Human-in-Loop)
  der Freigebende im Log

Das ist `gw_core/audit.py` und die *Grundlage* fuer jeden
DSG-Nachweis. Kein Audit-Trail = kein DSG-Nachweis.

---

## 5. Das offene Protokoll (der Kern-Trick)

Der grosse Trick, der vier lose Bausteine (ASR / LLM+RAG /
TTS / CRM) zu einem System macht: **ein offenes Protokoll**.

Alle Bausteine sprechen den **OpenAI-kompatiblen Endpunkt**
`/v1/chat/completions` (und `/v1/embeddings` fuer
Embedding). Warum:

- vLLM, Ollama, TGI und sogar OpenAI selbst sprechen
  denselben Endpunkt (`base_url/v1/chat/completions`).
- LiteLLM ist ein Proxy davor (`base_url` = LiteLLM,
  dahinter jedes Modell).
- Open WebUI hat ein `OPENAI_API_BASE`-Feld, in dem man
  *jeden* OpenAI-kompatiblen Endpunkt eintraegt.

**Heisst: man tauscht nie Code, nur ein
`base_url`-Feld.** Ein Plattform-Wechsel
(vLLM -> TGI, Ollama -> vLLM) ist eine
*Config-Änderung* (`API_BASE = http://localhost:8000/v1` ->
`http://ollama:11434/v1`), kein Projekt. Das ist der
Grund, warum vier Bausteine *eine Plattform* sind
(vgl. `platforms/docs/offene-ai-plattform.md`).

---

## 6. Komponenten: MUST / SHOULD / COULD

| Komponent | MUST | SHOULD | COULD |
|---|---|---|---|
| ASR (Etappe 2) | Voice-Eingang | Text-Eingang: nein | - |
| Diarization | Telefon (2 Sprecher) | Meeting | nein |
| LLM (Etappe 3) | immer | - | - |
| RAG | ja (keine Erfindung) | erweitertes Wissen | - |
| Guardrails (alle 5) | ja | - | - |
| TTS (Etappe 4) | Voice-Reply | Text-First: nein | - |
| CRM / Ticket | ja (persist) | ERP-Anbindung | - |
| Handoff | **ja (Wert!)** | - | - |
| Audit-Log | ja (DSG) | Retention-Rule | - |
| Modell-Router | ja (Fallback) | - | - |
| vLLM lokal | V3-V4 (DSG) | nein (V0-V1 Cloud-OK) | - |
| n8n / Workflow | - | ja (Ticket-Flow) | - |
| Monitoring | ja (Prometheus) | Grafana | Langfuse |

---

## 7. Was wir damit aufgeben (ehrliche Bilanz)

Die Open-Stack-Architektur (fuenf Bausteine statt einer
Foundry) gibt zwei Dinge auf, die man nicht verschweigen
darf (aus `platforms/docs/offene-ai-plattform.md` §6):

**7.1 Permission-Aware Grounding aus M365-Claims.** Foundry
liest die echten M365-Graph-Permissions und backt sie in
den Retrieval. Unser Stack simuliert das über ein
`access_level`-Integer (s. `docs/06-daten-speicherung.md`).
Fuer eine KMU mit klarer Rollentrennung reicht das; fuer
eine heterogene SharePoint-Struktur (500
Berechtigungsgruppen) genuegt es *nicht*.

**7.2 Azure Defender / Purview / Entra als geschlossenes
System.** Die Integration dieser Tools ist Foundrys
eigentlicher Wettbewerbsvorteil. Fuer KMU (keine
500-Personen-IT) ist das kein Problem. Fuer
mittlere Firmen (200+ Personen) ist der Wechselpfad zu
einem Open Stack hier der teuerste Teil.

---

## 8. Grenzen (ehrlich)

- **Kein Rechtsrat** (DSG/AI-Act) - s. `governance`,
  `OFFENE_PUNKTE.md` #4.
- **Keine 24/7-SLA im KMU-Standard** - s. `docs/10-
  betrieb-ops.md`.
- **De-CH-Dialekt ist nicht gelöst** - s. `docs/03-asr-
  transkribation.md`.
- **Permission-Aware M365-Grounding** aus der Box: nicht
  verfuegbar (s. §7.1).
- **20-50-Fall Quality-Test-Suite** pro Projekt: nicht in
  den Samples enthalten (s. `OFFENE_PUNKTE.md` #8).
- **Die Doku beschreibt die Architektur, NICHT die
  Implementierung** eines konkreten Systems. Ein konkretes
  Projekt hat konkrete Daten, Kunden, SLA.

---

## 9. Kritische Fragen (Entscheid-Checkpunkte)

| # | Frage | Warum |
|---|---|---|
| 1 | **Text-First oder Voice-First im MVP?** | Voice-First ist 3-5x teurer im Betrieb; Text-First ist fast immer besser. |
| 2 | **Wie lang das Kontext-Fenster (`--max-model-len`)?** | 128k auf 7B kostet VRAM, nicht Qualität. 8-16k reicht fuer 90% der KMU-Fälle. |
| 3 | **Welches Guardrail-System (NeMo / selbst-bau / keins)?** | Ohne Guardrails = Sicherheits- und DSG-Loch; ohne Selbstbau = Lock-in. |
| 4 | **Wie viel Kontext pro LLM-Call (RAG-Chunks)?** | 20-40 Chunks ist der Sweet-Spot; 100+ = Langzeit-Kontext-Problem, 5 = unter-kontextualisiert. |
| 5 | **Wer sieht den Audit-Trail?** | Ohne klaren Zugang ist der Log ein Leichenschrein; 24/7-Betrieb ist teurer als 8h-Betrieb. |

Siehe auch `OFFENE_PUNKTE.md` fuer den vollständigen Katalog.
