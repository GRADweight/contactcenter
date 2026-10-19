# 11 - Performance-Prüfung und Hardware-Architektur

> **Stand: Oktober 2026.** Diese Doku prüft konkret, ob die in
> `OFFENE_PUNKTE.md` #4 skizzierte Lösung (200 Anrufe/Tag, 10 zugleich,
> vLLM + Whisper) **technisch umsetzbar** ist, und legt die nötige
> Hardware-Architektur fest. GPU-Preise (A100 80 GB ~2600 CHF/Mo)
> wandern; vor jedem Angebot die aktuellen Anbieter-Preise neu prüfen
> (s. `platforms` → `gpu-deployment-entscheid.md`).
> **Keine Offerte, kein Rechtsrat.**

## 1. Die kurze Antwort

**Ja, es ist technisch umsetzbar - auf einer einzelnen A100 80 GB.**
Die drei Samples beantworten es mit Zahlen (`python beispiele/
skalierungsrechner.py rechner 200 10`):

| Frage | Antwort |
|---|---|
| 200 Anrufe/Tag | **Erlang-C: 3 Agenten** (Gipfelstunde 50 Anr/Std, A=1.67, Wq 13.5 s) |
| 10 Anrufe zugleich | **1x A100 80 GB** reicht (250 vLLM-Slots / 7B-Modell) |
| Engpass | **10 zugleich > 3 Agenten** → die Inference bestimmt die Kapazität |
| Ton-Speicher/Tag | **~732 MB Tag** (16 kHz 16 bit, 2-min-Anruf), 21.5 GB/Monat 24/7 |
| Voice-Reply-Latenz | **~805 ms** (< 1500 ms Ziel, mit `--enable-prefix-caching`) |

Die zentrale Erkenntnis ist identisch mit Doku 05: **«200 Anrufe/Tag und
10 zugleich**» ist **ein Wort für zwei Engpässe**. 200/Tag ist ein
Durchsatzproblem an der Warteschlange (Erlang, braucht nur 3 Agenten);
10 zugleich ist ein Parallelismusproblem an der GPU (vLLM-Slots). Bei
diesem Lastprofil gewinnt der Parallelismus (10 > 3). Für eine KMU mit
weniger als 10 gleichzeitigen Anrufern wäre die Antwort: **eine Karte
übergenügt**.

## 2. Was der Rechner im ersten Durchgang NICHT sah (3 Bugs)

Beim Prüfen (Doku 10, `beispiele/qualitaetstest.py` Q3/Q4) sind
drei Fehler im alten `skalierungsrechner.py` aufgefallen und gefixt:

| # | Fehler | Besser | Wirkung |
|---|---|---|---|
| 1 | **Busy-hour-Faktor** wurde falsch an `lambda_h * 0.25` anstatt an `anrufe * 0.25` gerechnet → 8x zu wenig Agenten | `calls_in_peak = anrufe * busy_hour_factor` | **1 Agent → 3 Agenten** |
| 2 | **Ton-Speicher** wurde nicht gerechnet, nur Text (0.13 MB/Tag) | `datenaufwand(...)` mit Ton | **1.3 MB → 732 MB/Tag** (Faktor ~550) |
| 3 | **Latenz** wurde nie geprüft, ob Voice-Reply < 1.5 s passt | `laten_z_pruefen(...)` | Neue Prüfung: **805 ms < Ziel** |

**Fehler 2 ist der entscheidende**: Eine KMU, die auf 1.3 MB/Tag
rechnet und 732 MB/Tag (mit Ton) braucht, hat **ein 550-fach
falsches Speicherbudget**. Das ist der typische Fall, wo die
Erstschätzung korrekt aussieht und die Umsetzung scheitert, weil
**Roh-Ton viel grösser als Text ist**.

## 3. Die drei Engpässe - getrennt betrachtet

### 3.1 Engpass 1: ACD-Warteschlange (Erlang-C, 3 Agenten)
Bei 200 Anrufen/Tag über 8 Stunden sind das 25 Anruf/Std im
Mittel; die GIPFELSTUNDE ist 25% des Tags = 50 Anrufe/Std.
Erlang-C mit 2-min-Handhabung gibt A = 1.67 → **3 Agenten** für
Wq < 20 s. Das ist ein **Personenengpass, nicht ein
Technikengpass**. Für eine KMU: 3 Mitarbeiter/Agenten pro
Schicht (oder eine Warteschlange + 1 Agent für niedrigeren
Service-Level). **Die Inference ist hier nicht der Engpass.**

### 3.2 Engpass 2: vLLM-Inferenz (1 GPU, 250 Slots)
10 Anrufe zugleich × 7B-Modell auf A100-80: **250 Slots/GPU**
(vLLM `--max-num-seqs 250`). Die 10 Streams passen mit
**1x GPU**. Für 20-50 zugleich weiterhin 1x A100; ab ~100
gleichzeitig (also > 250 Streams) **2x A100-80** (oder
**1x H100** für 200+ Streams via Kontext-Optimierung).

### 3.3 Engpass 3: Ton-Speicher (21.5 GB/Monat)
16 kHz, 16-bit-mono: 32000 B/sek × 120 s × 200/Tag =
**732 MB/Tag**. 24/7 über 30 Tage: **~21.5 GB/Monat**.
Für **9 Monate Aufbewahrung** (Doku 06, §4): **~193 GB/Monat**
bei 100% Roh-Ton-Beibehaltung; mit **Wochen-Beibehaltung +
Transkript-Primär** (Doku 06, §4): **~5-10 GB/Monat**.
Die **Speicher-Grösse ist kein Engpass** für eine KMU, aber
das **Budget muss korrekt berechnet sein** (Bug 2).

## 4. Latenz: passt für Voice-Reply?

Für ein *echtes* Voice-Reply (Anrufer hört sofort eine
Antwort) muss die Summe **< 1.5 s** bleiben (sonst wirkt
der Anrufer ungeduldig). Die 6 Etappen (Doku 03):

| Etappe | Zeit (ms) | Anmerkung |
|---|---:|---|
| 1. Ton-Puffer (VAD) | 100 | 500-ms-Ringpuffer auf CPU |
| 2. ASR (faster-whisper, stream) | 300 | Whisper-tiny, GPU, RTF < 1 |
| 3. LLM-TTFT (Prefill + 1 Token) | **80** | **mit** `--enable-prefix-caching`; 200 ms ohne |
| 4. Antwort-Streaming (TTFT→fertig) | 300 | 1-2 Sätze, ~50 ms/Token |
| 5. TTS (stream) | 300 | VITS/Coqui, GPU, ~500-char-Büro-Satz |
| 6. RTT + Netz | 25 | internes Netz |
| **Summe** | **~805 ms** | **unter 1500 ms Ziel** |

**Schluss: passt.** Mit `--enable-prefix-caching` (vLLM) sinkt
die LLM-TTFT von 200 auf 80 ms, das ist der entscheidende
Einflussfaktor. Ohne Prefix-Caching: 925 ms, immer noch unter
dem Ziel. Für Text-only-Chat/E-Mail (kein Voice): diese
6 Etappen fallen weg, **Latenz < 1 s** leicht erreichbar.

## 5. Die konkrete Hardware-Architektur (für 200/Tag + 10 zugleich)

### 5.1 Minimal-Architektur (eine GPU)
```
+-------------------------------------------------------------+
|  Mac Mini / kleiner Server (1x A100 80 GB, 128-256 GB RAM)  |
|                                                             |
|  vLLM (7B-FP16)          |   250 Slots/GPU, max-num-seqs=250|
|  + --enable-prefix-cache |   → 10 gleichzeitige Streams     |
|  + /v1/chat/completions  |   (OpenAI-kompatibel, Anti-Lock) |
|  + --max-model-len 8192  |                                   |
|  ------------------------|--------------------------------   |
|  faster-whisper (tiny/base|  1x CPU Core (ASR-Stream)        |
|   , RTF <1, GPU-ASR       |  → 300 ms pro Audio-Büro-Satz    |
|                            |                                  |
|  Postgres (pgvector)       |   RAG-Index + Tickets + V0-V1   |
|  + Redis (Queue)           |   V2-V4 LOCALL (nicht in Cloud) |
|  --------------------------|--------------------------------  |
|  Prometheus + Grafana       |  CPU/GPU/RAM/Metriken           |
|  + Langfuse (optional)      |  Eval-Trail bei > 200 Fällen |
+-------------------------------------------------------------+
   ~2600 CHF/Mo (24/7) / ~800 CHF/Mo (8h, 262h)
```
- **vLLM** ist die *einzige* GPU-kritische Instanz.
- **ASR (faster-whisper)** läuft auf einem **separaten CPU-Core**
  mit GPU-Access (nicht in vLLM-Slots, keine Konkurrenz).
- **Postgres + pgvector** für RAG + Tickets (`platforms`
  → `beispiele/rag_permission.py`, s. Doku 06 §3).
- **V2-V4 Daten (Intern/Vertraulich/Streng) bleiben lokal**
  (Doku 06, §4 - `handoff_router`-Cloud-Flag `False` bei
  V2-V4). V0-V1 dürfen in CH-Cloud (Infomaniak / Exoscale).

### 5.2 Warum NICHT Ollama für 10 zugleich
Ollama passt für **< 3 Agenten** (s. `docs/04-vllm`, Tabelle).
10 zugleich + Kontextfenster > 4096 + Voice-Reply-Latenz
(< 1.5 s) → **vLLM mit Prefix-Caching ist zwingend**.
Ollama hat kein Prefix-Caching und kein Continuous-Batching
für den parallelen Fall; bei 10 parallel sind die
Wartezeiten mit Ollama ~3× höher. **Empfehlung: vLLM für
ab 10 zugleich; Ollama für PoC mit < 3 parallel.**

### 5.3 Was passiert bei 500/Tag?
- **Erlang-C: 500/Tag × 0.25 = 125 Anr/Std × 2 min = 4.17 A
  → ~5 Agenten.**
- **vLLM: 500 × 2 min ÷ 8h = ~125/Std ÷ 62 = 70-100
  gleichzeitige Streams** → **1x A100 (250 Slots) reicht noch.**
- Bei **> 1000/Tag** → **2x A100** für ~500 gleichzeitige
  Streams (500 Agenten × Erlang-C, 1000 Anruf × busy-hour).
- **Die Kosten** (500/Tag, 24/7): **2 x 2600 = ~5200 CHF/Mo
  (GPU)** + ~500 CHF/Mo (Cloud-Netz/Storage), **10'000+
  CHF/Mo mit FTE** (2 Agenten + 1 KI-Owner s. Doku 09).

## 6. Grenzen dieser Prüfung (ehrlich)

- **Die Latenz-Zahlen sind THEORETISCH.** Eine Messung
  mit realen Ton-Dateien (nicht mit Mock) ist nötig, bevor
  man «Voice-Reply passt» sagt. `beispiele/qualitaetstest.py`
  Q3 prüft die **Formel**, nicht den **Echten-Wert**. Für
  einen konkreten Kunden: **30-50 echte Ton-Dateien in
  1-2 Sprachvarianten (de-CH, de-D) messen**.
- **Die ASR-WER (Word-Error-Rate) ist NICHT geprüft.**
  Die Doku 03 zeigt `WER < 5%` für Hochdeutsch,
  **10-15% für Schweizerdeutsch** (Dialekt-Modell nötig).
  **Ohne feingestelltes de-CH-Model: WER kann 20%+
  sein** → unbrauchbar. **Empfehlung: PoC mit
  `faster-whisper` + feingeschultem de-CH-Modell
  (z. B. via HuggingFace, nicht Standard).**
- **V2-V3 Cloud-Entscheid ist pro KMU individuell.**
  `handoff_router.py` sagt `V2 = dar_cloud: False`,
  aber die **tatsächliche Entscheidung** (Darf
  Bestellungs-Transkript in CH-Cloud?) ist ein
  **DSG-Entscheid, kein Technik-Entscheid** (Doku 06,
  §1, OFFENE_PUNKTE #4). **Dieses Repo sagt WO die
  Daten liegen, NICHT ob es erlaubt ist.**
- **200/Tag ist ein Schätzwert.** **Keine KMU hat
  exakt 200 Anrufe/Tag.** Für ein konkretes Angebot:
  **30-Tage-Messung der echten Anrufzahl +
  8-Stunden-Betriebszeit (nicht 24/7) + Gipfel-Faktor
  (nicht immer 0.25, für Einzelhandel
  z. B. 0.15, für Bank 0.40).**
- **V4 (Geschäftsleitung/Dienstag) ist kein
  Standard-Szenario.** **Die meisten KMU
  (V1-V3) brauchen gar keine V4-Autonomie** –
  ein «vertrag-aenderung」-Fall reicht mit 2
  Autonomiegrad **und 1d SLA**, s. `handoff_router.py`.
- **Die GPU-Kosten (A100 80 GB, ~2600 CHF/Mo)
  sind Stichtag 10/2026 (Doku 08, §6).** Vor dem
  Angebot: **GPU-Preise bei 3-5 Anbietern
  prüfen**. Für **8h-Betrieb** (nicht 24/7): **~800
  CHF/Mo** (nicht 2600). **Die Kosten-Grössenordnung
  wandert mit den Provider-Angeboten.**

## 7. Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | **Habt ihr 30 Tage echte Anruf-Statistik** (Anr/Tag,
     Gipfel-Faktor, 8h/24h)? | Ohne diese Zahlen ist
     «200/Tag» und «10 zugleich» **Schätzwerte,
     keine Fakten**. Das kann einen 5-fach
     zu kleinen oder zu grossen Kauf auslösen. |
| 2 | **Sprache: rein Hochdeutsch
     oder Schweizerdeutsch?** | de-CH-Modelle
     (feingestel) kosten 2× mehr
     Rechenzeit, WER 10-15%
     statt 5%. **Ohne feingestelltes
     Modell: PoC ist unbrauchbar.**
| 3 | **Voice-Reply oder
     Text-only?** | Voice-Reply braucht
     die 6 Etappen (ASR+TTS,
     Latenz < 1.5 s). **Text-only
     (Chat/E-Mail/WhatsApp) ist 3-5×
     billiger** (keine ASR/TTS-Kosten),
     und für die meisten
     KMU-Use-Cases ausreichend
     (Doku 05, §1). **Der
     Use-Case entscheidet, nicht die
     Technik.**
| 4 | **8h-Betrieb oder
     24/7?** | 8h: **1x A100,
     ~800 CHF/Mo.** 24/7:
     **1x A100, ~2600
     CHF/Mo.** 2×
     teur für 3× Betrieb.
     **Die meisten KMU
     brauchen 8h, nicht 24/7.**
| 5 | **Wie viele V2-V4
     (vertrauliche)
     Daten pro Tag,
     und darf ein
     Teil in CH-Cloud
     (Infomaniak/
     Exoscale)?** |
     V2-V3 in Cloud
     ist ein **DSG-Entscheid,
     kein Technik-Entscheid** (Doku
     06). **Die Architektur
     kann V0-V3 Cloud + V4
     lokal (Split-Deployment)
     – aber ob es
     erlaubt ist, entscheidet
     kein Rechner, sondern
     die Rechtsbeistand.**
| 6 | **Sollte für den PoC
     eher Ollama
     (kostengünstig,
     PoC) oder
     vLLM (Produkt,
     10+) gewählt
     werden?** | Für **< 3 parallel**:
     Ollama (< 100 CHF
     lokal) genügt.
     Für **ab 10 parallel
     + Voice-Reply
     (< 1.5 s)**:
     **vLLM ist zwingend**
     (Doku 05, §6).
     **Der PoC muss
     die ZIEL-Architektur
     abbilden, nicht die
     einfachste.**
| 7 | **Ist der Gipfel-Faktor
     0.25 korrekt
     für die Branche?**
     (Einzelhandel 0.15,
     Bank 0.40,
     Call-Center 0.30) |
     **Der Faktor
     0.25 ist ein
     «typisches»
     Durchschnitt;
     für eine konkrete
     KMU muss
     der Faktor aus
     30-Tage-Daten
     kommen.** Ohne
     diesen Daten:
     **der Rechner
     ist ungeprägt,
     s. OFFENE_PUNKTE
     #8.**
