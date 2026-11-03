# 05 Skalierung: Personal und Kanäle mit Erlang-B, -C und -A

## Zwei verschiedene Fragen

«200 Anrufe pro Tag» und «10 Anrufe gleichzeitig» sind zwei Fragen:

- **Wie viele Menschen** müssen in der Spitzenstunde verfügbar sein, damit die Wartezeit im
  Ziel bleibt? Das ist eine Warteschlange: **Erlang-C**.
- **Wie viele Kanäle** braucht ein Sprachagent, der jeden Anruf sofort annimmt und nur bei
  vollen Kanälen an die normale Warteschlange überlaufen lässt? Das ist ein Verlustsystem:
  **Erlang-B**.

Die GPU-Frage folgt aus der Kanalzahl ([11](11-performance-architektur.md)).

## Modell und Formeln

Annahmen von Erlang-C (M/M/n): Anrufe treffen als Poisson-Prozess mit Rate λ ein, die
Bearbeitungszeit ist exponentialverteilt mit Mittelwert h (AHT), n gleiche Agenten bedienen
in Ankunftsreihenfolge, niemand legt auf.

| Grösse | Formel |
|---|---|
| Angebotslast | A = λ · h (Erlang) |
| Erlang-B (Rekursion) | B(0) = 1, B(k) = A · B(k−1) / (k + A · B(k−1)) |
| Wartewahrscheinlichkeit | C(n, A) = n · B(n, A) / (n − A · (1 − B(n, A))), nur für n > A |
| mittlere Wartezeit (ASA, über alle Anrufe) | W_q = C(n, A) · h / (n − A) |
| Servicelevel | SL(t) = 1 − C(n, A) · e^(−(n − A) · t / h) |
| Auslastung | ρ = A / n |

Die Rekursion vermeidet Fakultäten und bleibt auch bei Tausenden Kanälen stabil. Für n ≤ A
wächst die Warteschlange ohne Grenze; der Code lehnt das ab (`InstabilesSystemFehler`),
statt eine Zahl zu liefern. Quelle der Formeln: M. Zukerman, «Introduction to Queueing
Theory and Stochastic Teletraffic Models», arXiv:1307.2968.

Eine frühere Fassung dieses Repos rechnete W_q = C / (n − A) ohne den Faktor h. Bei
h = 2 Minuten halbierte das die Wartezeit und führte zu 3 statt 4 Agenten im Beispiel der
README.

## Die Spitzenstunde ist eine Annahme

Der Rechner verteilt das Tagesvolumen nicht gleichmässig, sondern setzt einen Anteil in die
geschäftigste Stunde (Parameter `--spitzenanteil`, Standard 25 %). Dieser Wert ist eine
**Annahme**. Er hängt von Branche, Öffnungszeiten und Wochentag ab und muss aus den Daten der
eigenen Telefonanlage bestimmt werden, am besten als höchstes (oder z. B. 90-%-Quantil des)
Stundenvolumen über mehrere typische Wochen.

Wie stark das Ergebnis daran hängt (200 Anrufe pro Tag, AHT 120 s, Ziel ASA ≤ 20 s):

| Anteil Spitzenstunde | Angebotslast | Agenten | ASA bei dieser Zahl | Sprachagent-Kanäle (≤ 1 % Überlauf) |
|---|---|---|---|---|
| 15 % | 1.000 Erlang | 3 | 5.5 s | 5 |
| 25 % | 1.667 Erlang | 4 | 5.3 s | 6 |
| 35 % | 2.333 Erlang | 4 | 19.2 s | 7 |

## Validierung

**Publizierte Referenzwerte.** Das Beispiel von Call Centre Helper («Erlang C Formula – Made
Simple With an Easy Worked Example», 100 Anrufe in 30 Minuten, AHT 180 s, A = 10 Erlang) ist
im README des R-Pakets ErlangC auf CRAN mit sieben Stellen ausgerechnet. Der Code trifft alle
Werte (`tests/test_warteschlange.py`):

| Agenten | P(warten) | Servicelevel 20 s | ASA |
|---|---|---|---|
| 11 | 0.6821182 | 0.3896138 | 122.78 s |
| 12 | 0.4493882 | 0.640158 | 40.44 s |
| 13 | 0.2852705 | 0.7955948 | 17.12 s |
| 14 | 0.1741319 | 0.88835 | 7.84 s |

Dazu Erlang-B mit 1000 Leitungen bei 1500 Erlang: erlang.com nennt 0.335, der Code liefert
denselben Wert auf drei Stellen. Mit Fakultäten liefe diese Rechnung über.

**Unabhängige Simulation.** `gw_contactcenter/warteschlange_sim.py` simuliert Anrufe einzeln
(Poisson-Ankünfte, FCFS, n Agenten, optional Geduld und nicht-exponentielle Gespräche) und
enthält keine Erlang-Formel. Die Tests verlangen Übereinstimmung innerhalb von 4.5
Standardfehlern; die Tabelle zeigt die Szenarien mit Seed 1:

<!-- ausgabe: gw-contactcenter vergleich -->
```text
Erlang-Formel gegen ereignisdiskrete Simulation (Seed 1, 200'000 Anrufe je Szenario, 20 Batches)
Szenario             A Erl.   n  Kennzahl        Formel   Simulation  ± 1 SE
README-Beispiel      1.667   3  P(warten)       29.98 %      29.91 %   0.23 %
README-Beispiel      1.667   3  ASA s             26.98        27.00     0.52
README-Beispiel      1.667   3  SL 20 s         76.00 %      76.08 %   0.22 %
README-Beispiel      1.667   4  P(warten)       10.25 %      10.33 %   0.14 %
README-Beispiel      1.667   4  ASA s              5.27         5.39     0.15
README-Beispiel      1.667   4  SL 20 s         93.05 %      92.99 %   0.14 %
Call Centre Helper  10.000  13  P(warten)       28.53 %      28.14 %   0.66 %
Call Centre Helper  10.000  13  ASA s             17.12        16.86     1.02
Call Centre Helper  10.000  13  SL 20 s         79.56 %      80.04 %   0.63 %
Call Centre Helper  10.000  14  P(warten)       17.41 %      17.03 %   0.49 %
Call Centre Helper  10.000  14  ASA s              7.84         7.58     0.45
Call Centre Helper  10.000  14  SL 20 s         88.84 %      89.26 %   0.41 %
hohe Last           30.000  33  P(warten)       49.05 %      48.95 %   1.69 %
hohe Last           30.000  33  ASA s             49.05        52.77     6.31
hohe Last           30.000  33  SL 20 s         59.84 %      59.96 %   1.91 %
Erlang-A            10.000  10  P(warten)       42.34 %      42.23 %   0.27 %
Erlang-A            10.000  10  P(Abbruch)      15.75 %      15.61 %   0.15 %
Erlang-A            10.000  10  Wartezeit s        9.45         9.42     0.11
Erlang-A            10.000  10  SL 20 s         71.80 %      71.96 %   0.25 %
M/G/n CV 0.5        10.000  13  P(warten)       28.53 %      27.00 %   0.39 %
M/G/n CV 0.5        10.000  13  ASA s             17.12        11.11     0.27
M/G/n CV 0.5        10.000  13  SL 20 s         79.56 %      82.27 %   0.35 %
```

Die letzte Zeilengruppe ist absichtlich keine Übereinstimmung: Mit weniger variablen
Gesprächen (lognormal, Variationskoeffizient 0.5 statt 1) warten Anrufe deutlich kürzer, als
Erlang-C vorhersagt. Die Formel ist dann konservativ. Wer die Verteilung der eigenen
Gesprächsdauern kennt, kann mit `simuliere(..., bearbeitung_cv=...)` gegenrechnen.

## Sprachagent-Kanäle (Erlang-B)

Ein Sprachagent mit k Kanälen nimmt jeden Anruf an, solange ein Kanal frei ist. Die
Wahrscheinlichkeit, dass alle belegt sind, ist B(k, A) (bei Poisson-Ankünften unabhängig von
der Verteilung der Gesprächsdauer). `kanaele_fuer_blockierung(A, 0.01)` liefert die kleinste
Kanalzahl für höchstens 1 % Überlauf. Annahme: Der Bot führt Gespräche ähnlich lang wie
Menschen; ist er schneller oder langsamer, mit einer eigenen AHT rechnen.

## Abbrüche (Erlang-A)

Real legen Wartende auf. Erlang-A (M/M/n+M) nimmt eine exponentiell verteilte Geduld mit
Mittelwert `--geduld-s` an. Berechnet wird die stationäre Verteilung der Anzahl Anrufe im
System direkt (im Log-Raum, abgeschnitten bei vernachlässigbarer Restwahrscheinlichkeit),
daraus Wartewahrscheinlichkeit, Abbruchquote und mittlere Wartezeit; der Servicelevel
(Gesprächsbeginn innert t, Abbrüche zählen als verfehlt) über Uniformisierung der Wartezeit
eines markierten Anrufs. Geprüft gegen die Grenzfälle (unendliche Geduld ergibt Erlang-C;
Geduld gleich AHT ergibt exakt die Poisson-Verteilung) und gegen die Simulation. Quelle des
Modells: O. Garnett, A. Mandelbaum, M. Reiman, «Designing a Call Center with Impatient
Customers», Manufacturing & Service Operations Management 4(3), 2002.

## Grenzen

- Poisson-Ankünfte sind eine Näherung; Rückrufwellen nach Werbeaktionen oder Störungen
  verletzen sie.
- Die Rechnung gilt pro Stunde mit konstanter Last; Schichtplanung über den Tag ist nicht
  Teil des Rechners.
- Abwesenheiten (Pausen, Schulung, Krankheit) nur als pauschaler Faktor `--abwesenheit`.
- Mehrere Skills oder Sprachen pro Agent (Skill-Based Routing) sind nicht modelliert.

## Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Wie viele Anrufe kommen in der geschäftigsten Stunde einer typischen Woche? | Ersetzt die 25-%-Annahme. |
| 2 | Welches Ziel gilt: mittlere Wartezeit oder Servicelevel (z. B. 80 % in 20 s)? | Führt zu unterschiedlichen Zahlen. |
| 3 | Wie lange sind Bot-Gespräche im Vergleich zu menschlichen? | Bestimmt die Kanalzahl und damit die GPU. |
