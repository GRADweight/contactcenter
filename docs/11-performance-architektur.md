# 11 GPU-Dimensionierung: Speicher, Bandbreite, Zeit bis zum ersten Token

## Die Frage

Wie viele und welche GPUs braucht das Sprachmodell für k gleichzeitige Gespräche? Die
Kanalzahl k kommt aus Erlang-B ([05](05-skalierung.md)). `gw-contactcenter gpu` rechnet die
Antwort aus der Modellarchitektur und Datenblattwerten. Das Ergebnis sind **Obergrenzen**;
eine Messung auf der Zielhardware ersetzt es nicht ([15](15-latenz.md)).

## Rechenweg

Alle Formeln stehen in `gw_contactcenter/sizing.py`.

1. **Parameter aus der Architektur**: pro Schicht Q-, K-, V- und O-Projektion (GQA) und drei
   MLP-Matrizen (SwiGLU), dazu Einbettung und LM-Kopf. Die Tests vergleichen das Ergebnis
   mit den veröffentlichten Parameterzahlen.
2. **Gewichtsspeicher je Präzision**: BF16 2 Byte pro Gewicht; FP8 1 Byte für die linearen
   Schichten; INT4 (AWQ/GPTQ, Gruppengrösse 128) 0.5 Byte plus 2.5 Byte pro 128er-Gruppe.
   Annahme: Einbettung und LM-Kopf bleiben in 16 Bit.
3. **KV-Cache pro Token**: 2 (K und V) × Schichten × KV-Köpfe × Kopfdimension × Byte pro
   Wert (2, mit `--kv-cache-dtype fp8` 1).
4. **Gleichzeitig aktive Anfragen**: Ein Gespräch beansprucht das Modell nur, solange eine
   Antwort entsteht (Duty Cycle, Annahme 20 %). Die Zahl aktiver Anfragen ist binomialverteilt;
   gerechnet wird mit dem 99-%-Quantil.
5. **Dekodierrate**: Pro Schritt liest die GPU alle Gewichte und den KV-Cache jeder aktiven
   Anfrage. Obergrenze pro Strom = Speicherbandbreite / (Gewichte + aktive Anfragen ×
   Kontext × KV pro Token). Erreichbar ist ein Teil davon (Annahme 50 %).
6. **Zeit bis zum ersten Token (Prefill)**: 2 × Parameter × Token FLOP bei 40 % Auslastung
   der Tensor-Rechenwerke (Annahme). «Cache» heisst: Mit Prefix Caching sind nur die neuen
   Token des Gesprächswechsels zu rechnen (Annahme 300); «kalt» heisst: der ganze Kontext.

Eine Konfiguration gilt als passend, wenn Gewichte und KV-Cache im nutzbaren Speicher Platz
haben, die angenommene Dekodierrate mindestens 40 Token/s pro Strom erreicht und die Zeit bis
zum ersten Token mit Cache höchstens 300 ms beträgt. Die 40 Token/s folgen aus dem
Latenzbudget: Ein erster Satz von etwa 10 Token soll in 250 ms fertig sein
([15](15-latenz.md)).

## Quellen

| Eingabe | Werte | Quelle |
|---|---|---|
| Qwen3-8B | 36 Schichten, versteckt 4096, MLP 12288, 32 Q- und 8 KV-Köpfe, Kopfdimension 128, Vokabular 151'936 | `config.json` auf Hugging Face (Qwen/Qwen3-8B) |
| Llama 3.1 8B / 70B | 32 / 80 Schichten, versteckt 4096 / 8192, MLP 14336 / 28672, 8 KV-Köpfe | Llama-3-Bericht, arXiv:2407.21783, Tabelle 3 |
| NVIDIA L4 | 24 GB, 300 GB/s, 121 TFLOPS BF16 dicht | Produktseite NVIDIA |
| NVIDIA L40S | 48 GB, 864 GB/s, 362 TFLOPS BF16 dicht | Datenblatt NVIDIA (2023) |
| NVIDIA A100 80 GB SXM | 80 GB, 2039 GB/s, 312 TFLOPS BF16 dicht | Produktseite NVIDIA |
| NVIDIA H100 SXM | 80 GB, 3350 GB/s, 989.5 TFLOPS BF16 dicht | Produktseite NVIDIA |
| vLLM | `--gpu-memory-utilization` Standard 0.92 | `vllm/config/cache.py`, v0.32.0 |

Die vollständigen Links stehen in `sizing.py`. Datenblattangaben mit Sparsity sind halbiert,
weil Sprachmodelle in der Inferenz dicht rechnen.

## Beispiel: 8-B-Modell, 10 Gespräche

<!-- ausgabe: gw-contactcenter gpu --modell qwen3-8b --gespraeche 10 -->
```text
GPU-Dimensionierung (Obergrenzen aus Datenblattwerten, keine Messung)
Modell qwen3-8b: 8.19 Mrd. Parameter (6.95 Mrd. in linearen Schichten)
  Quelle: https://huggingface.co/Qwen/Qwen3-8B/blob/main/config.json
KV-Cache: 2 x 36 Schichten x 8 KV-Köpfe x 128 x 2 Byte = 147'456 Byte/Token
  10 Gespräche x 6'000 Token = 60'000 Token -> 8.24 GiB
Aktive LLM-Anfragen (99-%-Quantil, Duty Cycle 20 % angenommen): 5
Kriterien: Gewichte + KV-Cache passen, >= 40 Tok/s pro Strom nach Effizienzannahme, TTFT mit Prefix-Cache <= 300 ms (300 neue Token).

Karten GPU            Präz.  Gewichte  KV-Cache  verfügbar  Tok/s/Strom   TTFT ms
                              GiB       GiB       GiB     max | Ann.  Cache | kalt
     1 L40S           int4       5.7       8.2       42.7      82 | 41      34 | 679
     1 H100-SXM       int4       5.7       8.2       72.1     318 | 159     12 | 248
     1 H100-SXM       fp8        8.8       8.2       72.1     242 | 121      6 | 124
     1 A100-80GB-SXM  int4       5.7       8.2       72.1     194 | 97      39 | 788
     1 H100-SXM       bf16      15.3       8.2       72.1     161 | 81      12 | 248
     1 A100-80GB-SXM  bf16      15.3       8.2       72.1      98 | 49      39 | 788
     2 L40S           fp8        8.8       8.2       85.3     125 | 62       8 | 168
     2 L40S           bf16      15.3       8.2       85.3      83 | 42      17 | 339

Annahmen: Effizienz 50 % der Roofline, MFU 40 %, Reserve 1.5 GiB/Karte, --gpu-memory-utilization 0.92.
Vor einer Beschaffung auf der Zielhardware messen (gw-contactcenter latenz-messen).
```

Lesart:

- **Eine L4 fehlt in der Tabelle**, obwohl der Speicher reicht (INT4-Gewichte und KV-Cache
  zusammen rund 14 GiB von 20.6 GiB nutzbar). Bei 5 aktiven Anfragen mit je 6000 Token liest
  jeder Dekodierschritt die Gewichte und gut 4 GiB KV-Cache; das begrenzt die Karte auf
  28.5 Token/s pro Strom, mit der Effizienzannahme auf 14.3. «Passt in den
  Speicher» heisst nicht «schnell genug».
- Die L40S mit INT4 erreicht die Schwelle nur knapp. Ob sie im Betrieb reicht, hängt an der
  tatsächlich erreichten Effizienz; das zeigt nur eine Messung.
- Der Kontext ist der grösste Hebel. Mit 2000 statt 6000 Token, KV-Cache in FP8 und 70 %
  Effizienz lässt sich die Rechnung wiederholen:
  `gw-contactcenter gpu --kontext 2000 --kv-fp8 --effizienz 0.7`. Eine einzelne L4 erscheint
  erst, wenn zusätzlich die Schwelle auf 25 Token/s sinkt (`--min-tokens-s 25`).
- Die Spalte «kalt» zeigt, warum Prefix Caching nötig ist: Ohne Cache dauert das erste Token
  bei 6000 Token Kontext auf einer L40S rund 0.7 s statt 34 ms.

## Grösseres Modell: 70 B, 10 Gespräche

<!-- ausgabe: gw-contactcenter gpu --modell llama-3.1-70b --gespraeche 10 -->
```text
GPU-Dimensionierung (Obergrenzen aus Datenblattwerten, keine Messung)
Modell llama-3.1-70b: 70.55 Mrd. Parameter (68.45 Mrd. in linearen Schichten)
  Quelle: https://arxiv.org/abs/2407.21783 (Tabelle 3)
KV-Cache: 2 x 80 Schichten x 8 KV-Köpfe x 128 x 2 Byte = 327'680 Byte/Token
  10 Gespräche x 6'000 Token = 60'000 Token -> 18.31 GiB
Aktive LLM-Anfragen (99-%-Quantil, Duty Cycle 20 % angenommen): 5
Kriterien: Gewichte + KV-Cache passen, >= 40 Tok/s pro Strom nach Effizienzannahme, TTFT mit Prefix-Cache <= 300 ms (300 neue Token).

Karten GPU            Präz.  Gewichte  KV-Cache  verfügbar  Tok/s/Strom   TTFT ms
                              GiB       GiB       GiB     max | Ann.  Cache | kalt
     2 H100-SXM       int4      37.0      18.3      144.2     135 | 68      53 | 1069
     2 A100-80GB-SXM  int4      37.0      18.3      144.2      82 | 41     170 | 3392
     2 H100-SXM       fp8       67.7      18.3      144.2      81 | 41      27 | 535
     4 H100-SXM       bf16     131.4      18.3      288.4      89 | 44      27 | 535
     8 L40S           int4      37.0      18.3      341.3     139 | 70      37 | 731
     8 L40S           fp8       67.7      18.3      341.3      84 | 42      18 | 361
     8 A100-80GB-SXM  bf16     131.4      18.3      576.8     108 | 54      42 | 848

Annahmen: Effizienz 50 % der Roofline, MFU 40 %, Reserve 1.5 GiB/Karte, --gpu-memory-utilization 0.92.
Vor einer Beschaffung auf der Zielhardware messen (gw-contactcenter latenz-messen).
```

Bei mehreren Karten unterstellt die Rechnung ideale Tensor-Parallelität (Bandbreite und
Rechenleistung addieren sich, keine Kommunikationskosten). Die Zahlen sind damit noch
optimistischer als bei einer Karte.

## Was die Rechnung nicht enthält

- ASR und TTS auf derselben Karte: Ihr Speicher- und Rechenbedarf ist nicht modelliert; mit
  `--speicheranteil` lässt sich der Anteil für vLLM verkleinern ([04](04-vllm-inferenz.md)).
- Rechenengpässe beim Dekodieren grosser Batches, Kernel-Overhead, Scheduling von vLLM.
- Preise: Das Repo enthält keine. Mit `--preise preise.json` (eigene Preise je Karte)
  sortiert der Rechner nach Gesamtpreis.

## Grenzen

- Keine der Zahlen ist gemessen. Die Annahmen (Effizienz, MFU, Duty Cycle, Kontext) können
  das Ergebnis um Faktoren verschieben.
- Nur dichte Decoder-Modelle mit GQA und SwiGLU; Mixture-of-Experts-Modelle brauchen eine
  andere Rechnung.

## Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Wie lang wird der Kontext pro Gespräch wirklich (System-Prompt, Wissen, Verlauf)? | Bestimmt KV-Cache und Dekodierrate. |
| 2 | Welche Effizienz erreicht vLLM auf der Zielkarte mit dem gewählten Modell? | Messen mit `gw-contactcenter latenz-messen --parallel k`. |
| 3 | Laufen ASR und TTS auf derselben GPU? | Verkleinert den Speicher für das Sprachmodell. |
