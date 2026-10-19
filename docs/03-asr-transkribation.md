# 03 - ASR und LLM-Transkribation (Sprache -> Text)

> **Stand: Oktober 2026.** ASR-Tools wandern schnell (faster-whisper,
> whisper.cpp, WhisperX, Deepgram, AssemblyAI). Vor jeder Entscheidung
> die aktuelle Doku pruefen.

## 1. Das Missverstaendnis: «LLM transkribiert»

Die hauefigste Fehlstellung: «Stell ein LLM vor den Anruf, es laesst und
antwortet.» Das ist falsch auf zwei Ebenen. Gross-Sprachmodelle (GPT,
Claude, Qwen) haben *kein Ohr* - sie lesen Text, sie hoeren nichts. Und
die eigentliche Transkribation macht ein **ASR-Modell** (Automatic Speech
Recognition, vor allem Whisper), *nicht* das LLM. Die Kette ist:

```
  Ton  ->  ASR (Whisper)  ->  Text + Diariz.  ->  LLM + RAG  ->  Struktur
 ~16kHz     WER < 5%          Sprecher-Trennung   Quellen       Antwort,
 (Stereo)                                                   Kategorien, SLA
```

Das **LLM** kommt erst *nach* dem ASR. Die **Transkribation** ist also
ein **ASR-Problem**; das LLM ist danach ein **Text-Problem**. Das ist der
Unterschied zwischen «KI-System, das hoeren kann» (nicht moeglich) und
«KI-System, das hoeren *laesst*» (moeglich, aber teuer).

## 2. ASR-Tools 2026

| Tool | Typ | Plattform | Fuer | Nachteil |
|---|---|---|---|---|
| **faster-whisper** | CTranslate2 | GPU/CPU, lokal | On-Prem, KMU | de-CH-Dialekt mittelaessig |
| **whisper.cpp** | C-Optimierung | CPU, Mac | Kiste-Mac, < 20 Pers. | langsamer als GPU |
| **WhisperX** | Python | GPU | Transkript + WER | komplexe Abhaengigkeiten |
| **Deepgram** | API | Cloud, EU-DC | managed-ASR | CH-Region? EU ja, CH evtl. |
| **AssemblyAI** | API | Cloud, EU-DC | Enterprise | CH-Region? EU ja |
| **Azure Speech** | API | Cloud, EU/CH-SWISS | Enterprise | CH-Datacenter (Azure) |
| **OpenAI Whisper API** | API | Cloud, US | Global | CH-Region? US |

**Die ehrliche Antwort:** kein perfektes Tool. Bei **de-CH-Dialekt** liegt
die WER 8-15% gegenüber 2-5% fuer Hochdeutsch. Die Wahl hängt vom
**DSG-Gepraech** (CH vs EU vs On-Prem) ab.

### 2.1 Die Wahl in 5 Fragen

1. *Daten müssen in CH bleiben (DSG-Höchststrich)?* -> On-Prem
   (faster-whisper) oder CH-Cloud (Azure CH-SWISS).
2. *Wieviele Anrufe/Tag?* -> < 100: CPU (whisper.cpp); > 100: GPU.
3. *Wie viel Dialekt?* -> < 5%: kein Feintuning; > 10%: Feintuning (10-50 h).
4. *Latenz < 500ms (Voice-Reply)?* -> streaming-ASR (faster-whisper + GPU) oder Deepgram.
5. *Wer betreibt es?* -> KMU o. IT: managed (Deepgram/Azure); KMU m. IT: On-Prem.

### 2.2 De-CH-Dialekt - das Haertest

Schweizerdeutsch (Zueri, Bern, Basel, Lugano) ist *keine* Hochdeutsche
Spraecheweise, sondern ein eigenes System: Dialekt-Vokabular (kein
Hochd-Modell kennt die Buerger), Phonem-Unterschiede, Grammatik.
Die einzige Moeglichkeit: ein **Domain-Speech-Modell** (10-50 Stunden
transkrib, feingetunt). Ohne das: WER 10-15%, zu hoch fuer Voice-Reply.

## 3. Diarization (wer spricht wann)

Ein Telefon-Transkript hat meist **zwei Sprecher** (Agent, Kunde).
Ohne Trennung verschmelzen beide Stimmen zu einem Block.

- *Telefon (1-2 Sprecher):* einfache Heuristik (Wechsel bei > 0.5s Stille).
- *Meeting (3+ Sprecher):* `pyannote.audio` (Sprecher-Embeddings, kllustering).

Die Diarisierung laeuft *nach* der Transkription, nicht parallel.
Bei *simultanen* 3+ Sprechern (Konferenz) versagt jede Heuristik.

## 4. Post-Prozess

Nach der Transkription verwandelt der Post-Prozess das rohe Text in
ein saubereres:

1. *Normalisierung:* Ziffern zu Woertern (`12:30` -> `zwölf Uhr dreissig`).
2. *Füllwort-Filter:* `äh`, `eh`, `hm`, `also` entfernen.
3. *Fehler-Korrektur:* ein kleiner LLM-Call (Qwen-7B) korrigiert
   offensichtlich Fehler (`ich gah es hii` -> `ich habe den Koffer bestellt`).
4. *PII-Masking:* Name, Adresse, Konto, Karte vor der Speicherung
   entfernen (s. `beispiele/transkript_pipeline.py` `mask_piis`;
   fuer Produktion: Presidio + NER, nicht nur Regex).

Die hauefgueste Fehlquelle ist die **PII-Masking-Auslassung**. Ein
einzelnes unmaskiertes `Konto 1234567890` ist ein DSG-Verstoss.

## 5. Laenzen-Mathematik (fuer Voice-Reply)

Fuer Voice-Reply muss die Gesamtlatenz < ~1.5s sein (menschliches
Gespäch: 0-1.5s Pause). Die Laenzen verteilt sich auf 5 Etappen:

| Etappe | Laenzen |
|---|---|
| ASR (Ton -> Text, streaming) | 200-500 ms |
| LLM First-Token (vLLM, 7B, GPU) | 100-300 ms |
| TTS (Text -> Ton, Piper, GPU) | 200-500 ms |
| Netzwerk (RTT) | 20-50 ms |
| Wartezeit + Puffer | 100-200 ms |
| **Gesamt** | **~720-1750 ms** |

Fuer < 1.5s muss **jede der 5 unter 150ms** sein. Das ist der Grund,
warum Voice-Reply teuer ist: alle 5 Etappen brauchen GPU und laufen
parallel. **Text-Start (Chat, E-Mail) umgeht das komplett.**

## 6. WER und Zielwerte

**WER** (Word Error Rate) = (Substitution + Delektion + Insektion)
/ Referenz-Woerter.

| Kontext | WER-Ziel | Beurteil |
|---|---|---|
| Studio, einziger Sprecher | < 3% | Ausgezeichnet (nicht realistisch) |
| Hochdeutsch, sauber | < 5% | Gut (Voice-Reply moeglich) |
| Hochdeutsch, Alltag | < 8% | Akzetabel (Text-Antwort) |
| Schweizerdeutsch, ohne Feintuning | 10-15% | **nicht** akzetabel fuer Voice |
| Schweizerdeutsch + Feintuning | < 8% | Akzetabel, Voice moeglich |

**Die einzige ehrliche Antwort auf «kann ich de-CH mit ASR?」:**
nur mit Feintuning (10-50 h,  Domain-Model) oder managed (Deepgram,
Azure, AssemblyAI) - kein Open-Source-Tool liefert das aus der Dose.

## 7. Die 5-Etapen-Pipeline (lauffähig)

`beispiele/transkript_pipeline.py` zeigt die volle Kette
(ASR / Diariz / PII / LLM / Auszug) mit Mock-Adapter (kein Netz,
kein API-Key):

```bash
python beispiele/transkript_pipeline.py selftest
# -> 5 Segmente, Absicht «Bestellungsstatus», Priorität 3,
#    PII-Masking funktioniert, JSON-serbar
```

Der echte Adapter (faster-whisper, OpenAI-Compat) ist als Stub
dokumentiert (s. `docs/00-unterfangen.md` §6 und das Sample).

## 8. Grenzen (ehrlich)

- **Kein Rechtsrat** (DSG, Ton-Aufnahme, Nach-Erlaubnis) -
  `governance`, `OFFENE_PUNKTE.md` #4.
- **De-CH-Dialekt ist nicht gelöst, nur «mit Feintuning moeglich»**
  (§2.2, §6).
- **Keine 20-50-Fall-WER-Test-Suite** im Repo (pro Projekt,
  mit eigener Referenz-Aufzeichnung).
- **Das Sample ist Code** (300 Zeilen), kein produktionsreifes Tool.
- **faster-whisper braucht GPU** fuer schnelle Transkribation
  (CPU = 10x mehr Zeit).

## 9. Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Wie viel de-CH-Dialekt (< 5%, 5-10%, > 10%)? | < 5%: kein Feintuning; > 10%: zwingend. |
| 2 | Voice-Reply oder Text-Antwort im MVP? | Text-Antwort 3-5x billiger; Voice = 5-Etappe-Latenz. |
| 3 | Wieviele Stunden Aufzeichnung fuer Feintuning? | 10-50 h: gutes Modell; < 5 h: kein Modell |
| 4 | Weniger Sprecher (Telefon 1-2) vs. vielen (Meeting 3+)? | Telefon: Heuristik; Meeting: pyannote. |
| 5 | DSG-Gepraech fuer Ton (Aufnahme erlaubt, informiert)? | Nein: keine Aufnahme; ja: mit Rechtsgrund. |
