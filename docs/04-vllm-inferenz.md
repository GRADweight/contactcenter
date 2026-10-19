# 04 - vLLM und Inferenz (das Modell lauffähig machen)

> **Stand: Oktober 2026.** vLLM wandert schnell (0.6 > 0.7 > ... bricht oft
> Command-Flags). Vor jedem Deploy die aktuelle Version, die `--flag`-Syntax
> und die Lizenz pruefen.

## 1. Was vLLM ist, und warum es ab 10 Nutzern Pflicht ist

**vLLM** ist ein hoch-performer LLM-Inferenz-Server. Er löst drei Probleme, die
ein Standard `transformers`-Modell nicht löst:

- **PagedAttention:** die KV-Cache (das «Gedächtnis» des Modells) wird in
  Blöcken verwaltet, nicht linear - wie Virtueller Speicher im OS.
  -> **~24x mehr** Durchsatz (Original-Paper, SOSP 2023).
- **Continuous Batching:** Prämifz- und Dekodier-Requests **mischen** sich
  in einer GPU. -> Bei 10+ Nutzern **doppelt** sich der Durchsatz.
- **Open-kompatibler Endpunkt:** `vllm serve MODEL` startet
  `http://localhost:8000/v1/chat/completions` - derselbe Vertrag wie
  OpenAI. -> Die App braucht nur einen `api_base`-Wechsel.

**Warum Pflicht ab ~10 Nutzer:** bei 1 Nutzer reicht Ollama (ein Mac,
einfach). Ab 10 *gleichzeitig* laufenden Requests ist Ollama ein
Queue-System (1 nach dem anderen), vLLM parallelisiert. Das ist der
Grund, warum die **Skalierungsfrage** (10 simultaneous oder 200/Tag)
an vLLM gekoppelt ist.

## 2. Wann vLLM, wann Ollama, wann Sonstiges

| Tool | Fuer | Warum |
|---|---|---|
| **Ollama** | 1-10 Nutzer, dev/test, Mac | `ollama run qwen3` laefft in 1 Min |
| **vLLM** | 10+ Nutzer, Production, GPU | PagedAttention + Continuous Batching |
| **TGI** (HuggingFace) | 10+ Nutzer, HF-Ökosystem | Fast wie vLLM, starker Multi-User |
| **llama.cpp** | CPU / Edge, 1 Nutzer | Kein GPU nötig, langsamer |
| **TensorRT-LLM** | Absolute Max-Perf. | NVIDIA-only, komplex |
| **Cloud-API** | < 10M Token/Tag, keine GPU | Billiger als eine A100 |

**Die faule Formel** (s. `platforms`
`docs/gpu-deployment-entscheid.md`):

```
  < 1M Token    ->  API (Stufe A)
  1M - 10M      ->  CPU / Ollama (Stufe B1)
  10M - 50M     ->  vLLM gemietet, CH-Bare-Metal (Stufe B2)
  50M - 500M    ->  vLLM auf eigner Kiste (Stufe B3, mit Backup)
  500M+         ->  Cluster (Stufe B4, 5-Personen-Team)
```

## 3. Die `vllm serve`-Befehle (Stichtag 10/2026)

### 3.1 7-14B auf einer GPU (der KMU-Standard)

```bash
# vLLM Open-kompat. Server für Qwen3 14B auf 1x A100/H100
vllm serve Qwen/Qwen3-14B \
  --gpu-memory-utilization 0.9 \
  --max-model-len 8192 \
  --max-num-seqs 64 \
  --port 8000
```

- `--gpu-memory-utilization 0.9` = 90% GPU-Speicher fuer Model + KV-Cache.
- `--max-model-len 8192` = Kontext-Window.
  **128k = mehr VRAM, nicht mehr Qualität.** 8192 reicht
  fuer 90% der KMU-Fälle (s. §4).
- `--max-num-seqs 64` = 64 parallele Requests.
  Das ist der **Skalierungs-Parameter** (s. §4, §6).

### 3.2 70B mit AWQ-Quantisierung auf 1x 80GB

```bash
vllm serve TheBloke/Qwen2.5-72B-Instruct-AWQ \
  --quantization awq \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.95 \
  --max-model-len 32768 \
  --port 8000
```

**AWQ** quantisiert ein 70B-Modell auf ~40GB VRAM.
Das passt auf eine **einzelne A100-80** (fuer
~18 parallele Requests, s. `beispiele/skalierungsrechner.py`).

### 3.3 Docker

```bash
docker run --gpus all -p 8000:8000 \
  vllm/vllm-openai:latest \
  --model Qwen/Qwen3-14B \
  --gpu-memory-utilization 0.9 \
  --max-model-len 8192 \
  --enable-prefix-caching
```

`--enable-prefix-caching`: fuer wiederholte
System-Prompts (z. B. 200 Anruufe
mit demselben System-Text) - erst
einmal eingetragen, danach
*gecached*. Fuer Voice (jeder Anruf
hat denselben System-Text) ist das **zwingend**.

## 4. Die 6 vLLM-Parameter, die den Betrieb bestimmen

| Parameter | Was | KMU-Standard |
|---|---|---|
| `--max-num-seqs` | Parallel-Requests | 64-512 (nach Laenzen) |
| `--max-model-len` | Kontext-Window | 8192 (nicht 128k!) |
| `--gpu-memory-utilization` | VRAM-Anteil | 0.85-0.95 |
| `--quantization` | Quant-Methode | AWQ (70B), FP16 (7-14B) |
| `--enable-prefix-caching` | Prompt-Cache | **ja** (Voice/Ton) |
| `--max-seq-len` | Max. Sequenz | 8192-32768 |

**Die häufigste Fehlerquelle:** `--max-model-len 128k`
auf einem 7B-Modell. Das braucht
~128k Token x 2 (KV-Cache) x
Batch = OOM. Der Server
crasht beim Start.
Die Antwort: `--max-model-len 8192`.
Das ist die häufigste Ursache fuer
vLLM-Incidents im KMU-Betrieb.

## 5. Monitoring und Load-Testing

vLLM stellt Prometheus-Metriken
auf `/metrics` (API-Port)
fuer die drei wichtigsten Werte:

| Metrik | Was |
|---|---|
| `vllm_total_tokens_per_second` | Durchsatz (Token/s) |
| `vllm_num_requests_running` | Aktive Requests |
| `vllm_gpu_cache_usage_perc` | KV-Cache-Utilisation |

**Load-Test** (vor jedem Production-Deploy):

```bash
pip install locust
locust -f test_load.py --host http://localhost:8000
```

Zielwerte: TTFT < 500ms (fuer Voice),
> 100 Requests/s (fuer Batch),
GPU-Utilisation > 80%,
keine OOM-Interms.

S. auch `beispiele/skalierungsrechner.py`
fuer die Skala-Tabelle
(GPU-Anzahl nach Slots).

## 6. Grenzen (ehrlich)

- **Keine rechtliche Anleitung** -
  fuer DSG/AI-Act: `governance`.
- **vLLM ist Nvidia-freundlich.**
  AMD (ROCm) ist aktuell, aber
  mittelmässig.
- **Schnelle Aenderungen.**
  Die `--flag`-Syntax aendert sich
  (z. B. `--speculative-model` ->
  `--speculative-config`).
- **GPU ist Pflicht** fuer vLLM.
  Ohne GPU: Ollama / llama.cpp fuer CPU.
- **Keine Kalkulation** - GPU-Preise
  in `beispiele/skalierungsrechner.py`
  und `OFFENE_PUNKTE.md` #1.

## 7. Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Welche GPU (A100-80 / H100 / L4)? | A100-80 = KMU-Standard; H100 = 3x teurer. |
| 2 | `--max-model-len` 8k oder 128k? | 128k = OOM; 8k reicht fuer 90%. |
| 3 | Quantisierung FP16 / AWQ / GPTQ? | 7B: FP16; 70B: AWQ (passt auf 1x 80GB). |
| 4 | Ist TTFT < 500ms (Voice) moeglich? | Nur mit GPU + `--enable-prefix-caching`. |
| 5 | Wann ist Ollama genug, wann vLLM zwingend? | < 10 Nutzer: Ollama; > 10: vLLM. |
