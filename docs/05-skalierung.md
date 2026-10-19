# 05 - Skalierung: 200 Anrufe/Tag ODER 10 gleichzeitig

> **Stand: Oktober 2026.** GPU-Preise und vLLM-Slots wandern.
> Vor jedem Angebot neu rechnen - s. `OFFENE_PUNKTE.md` #1.

## 1. Die zentraelle Falle: ein Wort, zwei Probleme

«200 Anrufe/Tag ODER 10 zugleich» klingt gleich, ist aber **zwei
Probleme** mit **zwei Engpässen**:

- **200 Anrufe/Tag** = **Durchsatz**-Problem -> engpäss an der
  **ACD-Warteschlange** (Erlang-C).
- **10 Anrufe zugleich** = **Parallelismus**-Problem -> engpäss
  an der **vLLM-Inferenz** (Slots pro GPU).

Beide müssen gleichzeitig erfüllt sein. 200/Tag schafft man
*bei wenig Simultan*; 10 zugleich nur mit genug GPU-Slots.

## 2. Problem A - Die Warteschlange (Erlang-C)

```
  lambda = Ankunftsrate (Anrufe/Minute)
  h      = Handhabungszeit/Anruf (Minuten)
  A      = lambda * h      = Angebotslast (Erlangs)
  C(n,A) = P(anruf wartet)
  Wq     = C(n,A)/(n-A)    = mittlere Wartezeit
```

**Der GIPFELSTUNDE-Trick:** 200/Tag ist *nicht* die Last.
Fakt: **0.25** (25% des Tages in 1 GIPFEL-STUNDE). Für 200/Tag
bei 8 GS-Stunden = 25/Stunde, GIPFEL = 25*0.25 = **~6 Anruufe**
in 15 Min. Bei 2 min = A = 0.2 Erlangs. **1 Agent reicht.**
200/Tag ist in den meisten KMU *kein* Problem; der Engpass
ist fast nie die Warteschlange.

## 3. Problem B - Die vLLM-Kapazität (Slots pro GPU)

10 *gleichzeitig* = 10 LLM-Streams parallel. Jde GPU kann X
parallel (die **Slot-Grenze**). `gpus_noetig = ceil(Streams /
slots_pro_GPU)`. Slots pro GPU sind **Grössenordnungen**
(10/2026, aus `beispiele/skalierungsrechner.py`):

| GPU | 7-14B | 30B | 70B (AWQ) |
|---|---|---|---|
| A10-24GB | 90 | 10 | - |
| L4-24GB | 120 | 12 | - |
| A100-40GB | 180 | 25 | - |
| A100-80GB | 250 | 55 | 18 |
| H100-80GB | 400 | 100 | 30 |

**Fazit B:** 10 *gleichzeitig* 7-14B auf einer A100 (250 Slots)
läuft ohne Warte. **10 zugleich** ist der häufigste
*bindender Fall*, weil die GIPFELSTUNDE selten > 10-15
simultane Anruufe bringt, aber die GPU muss alle
*simultan* verarbeiten.

## 4. Beispiel: 200/Tag, 10 zugleich, 2 min, A100-80

```bash
python beispiele/skalierungsrechner.py rechner 200 10
# Queue: 1 Agent (Wq 15.8 s, rho=0.208)
# Infe:  1x A100-80, --max-num-seqs 250, ~2600 CHF/Mo
# => "bestimmt die INFERENZ die Kapazität" (10 > 1)
```

## 5. Skalierungstabelle (Voice, 5 -> 1000)

Nach `grad-platforms` `selbstbau-blaupause.md` §4. Alle
Kosten **Grössenordnungen** (10/2026, CH), keine Offerte:

| Grösse | Inferenz | GPU | ~CHF/Mo |
|---|---|---|---|
| **5** | Ollama Mac, Qwen3-27B | 0 | ~20-40 |
| **20** | 1x A100, vLLM, 14B | 1 | ~250-400 |
| **50** | 1-2x A100, vLLM | 2 | ~600-900 |
| **250** | K3s, 2-4x H100 | 2-4 | 2'000-3'500 |
| **1000** | K8s, 4-8x H100, DR | 4-8 | 8'000-15'000 |

Ab ~500-1000 beginnt der **Cluster-Regime**, wo der Betrieb
(2 Köpfen, Pager) teurer wird als die Hardware.

## 6. Die 3 Kosten-Posten (pro Angebot)

Vor **jeder** Offerte:

1. **GPU-Infrastruktur** (A100/H100, gemietet/eigen, CH).
2. **Cloud / DSG-Aufbewahrung** (Postgres, Vektor, Ton).
3. **FTE-Personal** (KI-Owner + Dev, nach Aufwand).

FTE sind fast nie der billige Teil, aber in einer KMU der
eigentliche **Wert** (die Personen, die es betreuen).

## 7. Grenzen (ehrlich)

- **Grössenordnungen, nicht Offerte.** Vor jedem Angebot neu
  (A100/H100-Liste / Exoscale / Infomaniak).
- **Slots pro GPU sind Erfahrungswerte** (nicht gemessen) -
  eigenen Load-Test (`locust` in `docs/04-vllm-inferenz.md` §5).
- **Gipfelfaktor 0.25** ist Erfahrungswert; pro Kunden
  aktuell messen.
- **24/7-Betrieb** (ab ~500) braucht **2 Köpfen**, nicht 1
  (s. `docs/10-betrieb-ops.md`).

## 8. Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Wieviele Anrufe in der GIPFELSTUNDE? | Bestimmt Erlang-C (Problem A). |
| 2 | Wieviele *gleichzeitig* (simultan)? | Bestimmt vLLM-Slots (Problem B). |
| 3 | Welche GPU (A100-80 / H100)? | A100-80 = KMU-Standard; H100 = 3x teurer. |
| 4 | Sind Token/Tag < 10M (API genug)? | Ja: keine GPU; Nein: vLLM. |
| 5 | Ist 24/7 (> 500) nötig? | Nein: 8h-Betrieb, 1 Kopf; Ja: Cluster, 2+. |
