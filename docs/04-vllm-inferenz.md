# 04 Inferenz mit vLLM

## Was vLLM leistet

vLLM ist ein Inferenzserver für Sprachmodelle mit OpenAI-kompatibler API. Für einen
Sprachagenten zählen drei Eigenschaften:

- **PagedAttention**: Der KV-Cache wird in Blöcken verwaltet statt zusammenhängend
  reserviert. Laut dem Originalbeitrag (Kwon et al., SOSP 2023, arXiv:2309.06180) steigt der
  Durchsatz gegenüber FasterTransformer und Orca bei gleicher Latenz um das 2- bis 4-Fache.
- **Continuous Batching**: Neue Anfragen kommen in laufende Dekodierschritte hinzu; mehrere
  Gespräche teilen sich eine GPU, ohne aufeinander zu warten.
- **Prefix Caching** (`--enable-prefix-caching`): Bereits berechnete Präfixe (System-Prompt,
  bisheriger Verlauf) werden wiederverwendet. Für die Zeit bis zum ersten Token zählt dann
  nur der neue Teil des Gesprächs ([11](11-performance-architektur.md)).

## Konfiguration im Compose-Beispiel

Geprüft gegen den Quellcode von vLLM v0.32.0 (`vllm/engine/arg_utils.py`,
`vllm/config/cache.py`, `docker/Dockerfile`):

| Parameter | Wert im Beispiel | Bedeutung |
|---|---|---|
| `--model` | `${LLM_MODELL}` | Hugging-Face-ID, z. B. ein AWQ-quantisiertes 8-B-Modell |
| `--max-model-len` | 8192 | Höchste Kontextlänge pro Anfrage; bestimmt den KV-Bedarf pro Gespräch |
| `--max-num-seqs` | 32 | Höchstzahl gleichzeitig bearbeiteter Anfragen (Standard in v0.32.0: 128) |
| `--gpu-memory-utilization` | 0.60 | Anteil des GPU-Speichers für vLLM (Standard: 0.92); der Rest bleibt für ASR und TTS auf derselben Karte |
| `--enable-prefix-caching` | gesetzt | siehe oben |
| Benutzer | `2000:0` | vom vLLM-Image vorgesehener Benutzer ohne root |

Ob 60 % des Speichers reichen, zeigt `gw-contactcenter gpu --speicheranteil 0.6` und danach
ein Lasttest.

## Denkmodus abschalten

Modelle mit «Denkmodus» (z. B. die Qwen3-Familie) schreiben ohne Anweisung zuerst
Überlegungen, bevor die hörbare Antwort beginnt. Der Agent setzt deshalb pro Request
`{"chat_template_kwargs": {"enable_thinking": false}}` (`LLM_EXTRA_JSON`). Ob ein anderes
Modell diesen Schalter kennt, steht in dessen Modellkarte.

## Alternativen

- **Ollama** oder **llama.cpp** für Prototypen auf einem Arbeitsplatzrechner.
- **Gehostete APIs** für Datenklassen, deren Region das zulässt (V0/V1, [06](06-daten-speicherung.md)).
  Das Gateway behandelt sie wie jedes andere Backend; die Region bestimmt, für welche
  Anfragen sie zulässig sind.

## Grenzen

- Die Compose-Konfiguration ist nicht auf GPU-Hardware gelaufen.
- Flags und Standardwerte ändern sich zwischen vLLM-Versionen; bei einem Versionswechsel neu
  prüfen.

## Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Laufen ASR und TTS auf derselben Karte wie das LLM? | Bestimmt `--gpu-memory-utilization`. |
| 2 | Welche Kontextlänge braucht ein Gespräch wirklich? | Längerer Kontext kostet KV-Speicher und Prefill-Zeit. |
| 3 | Wie wird ein Modellwechsel abgenommen? | Evals mit `--llm-url` gegen das neue Modell, dann Lasttest. |
