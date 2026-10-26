"""Ereignisdiskrete Simulation einer Contact-Center-Warteschlange (M/G/n+M).

Unabhängige Gegenprobe zu :mod:`gw_contactcenter.warteschlange`: Hier steht
keine Erlang-Formel, nur Zufallszahlen und die Bedienregel. Stimmen
Simulation und Formel innerhalb der statistischen Unsicherheit überein, ist
das ein starkes Indiz, dass beide richtig sind.

Bedienregel (FCFS, n gleichartige Agenten): Ein Anruf beginnt sein
Gespräch, sobald er an der Reihe ist und eine Agentin frei wird. Weil
Gespräche in Ankunftsreihenfolge beginnen, genügt ein Heap mit den
Zeitpunkten, zu denen die Agenten frei werden: Der Anruf nimmt die
früheste freie Agentin; dauert die nötige Wartezeit länger als seine
Geduld, legt er auf und belegt niemanden.

Zusätzlich zur exponentiellen Bearbeitungszeit (M/M/n) lässt sich eine
lognormale mit gleichem Mittelwert und wählbarem Variationskoeffizienten
simulieren (M/G/n). Damit sieht man, wie empfindlich Erlang-C auf seine
Exponential-Annahme reagiert.

Statistische Unsicherheit: Batch-Means über ``batches`` gleich grosse Blöcke
nach der Einschwingphase; ``standardfehler`` ist der Standardfehler des
Mittelwerts je Kennzahl.
"""

from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass, field

__all__ = ["SimErgebnis", "simuliere"]


@dataclass(frozen=True)
class SimErgebnis:
    """Geschätzte Kennzahlen pro angebotenem Anruf."""

    anrufe: int
    p_warten: float
    mittlere_wartezeit_s: float
    servicelevel: float | None
    p_abbruch: float
    standardfehler: dict[str, float] = field(default_factory=dict)


def _bearbeitungszeit_ziehen(rng: random.Random, aht_s: float, cv: float) -> float:
    if cv == 1.0:
        return rng.expovariate(1.0 / aht_s)
    # Lognormal mit Mittelwert aht_s und Variationskoeffizient cv.
    sigma2 = math.log(1.0 + cv * cv)
    mu = math.log(aht_s) - sigma2 / 2.0
    return rng.lognormvariate(mu, math.sqrt(sigma2))


def simuliere(
    ankunftsrate_pro_s: float,
    aht_s: float,
    agenten: int,
    *,
    anrufe: int = 200_000,
    einschwingen: int = 20_000,
    seed: int = 1,
    sl_zeit_s: float | None = None,
    geduld_s: float | None = None,
    bearbeitung_cv: float = 1.0,
    batches: int = 20,
) -> SimErgebnis:
    """Simuliert ``anrufe`` Anrufe (nach ``einschwingen`` verworfenen Anrufen).

    ``geduld_s`` = None bedeutet: niemand legt auf (Erlang-C-Fall).
    """
    if agenten < 1:
        raise ValueError("agenten muss ≥ 1 sein")
    if ankunftsrate_pro_s <= 0 or aht_s <= 0:
        raise ValueError("Ankunftsrate und AHT müssen > 0 sein")
    if anrufe < batches or batches < 2:
        raise ValueError("anrufe muss ≥ batches ≥ 2 sein")
    if bearbeitung_cv <= 0:
        raise ValueError("bearbeitung_cv muss > 0 sein")
    if geduld_s is not None and geduld_s <= 0:
        raise ValueError("geduld_s muss > 0 sein")

    rng = random.Random(seed)  # noqa: S311 - Simulation, keine Kryptografie
    frei: list[float] = [0.0] * agenten  # Zeitpunkte, ab denen die Agenten frei sind
    heapq.heapify(frei)
    t = 0.0
    pro_batch = anrufe // batches
    gesamt = einschwingen + pro_batch * batches

    # Summen je Batch: [gewartet, wartezeit, innert_sl, abgebrochen]
    summen = [[0.0, 0.0, 0.0, 0.0] for _ in range(batches)]
    for i in range(gesamt):
        t += rng.expovariate(ankunftsrate_pro_s)
        frueheste = frei[0]
        start = frueheste if frueheste > t else t
        warten = start - t
        geduld = rng.expovariate(1.0 / geduld_s) if geduld_s is not None else math.inf
        abbruch = warten > geduld
        if not abbruch:
            heapq.heapreplace(frei, start + _bearbeitungszeit_ziehen(rng, aht_s, bearbeitung_cv))
        if i < einschwingen:
            continue
        s = summen[(i - einschwingen) // pro_batch]
        s[0] += warten > 0.0
        s[1] += geduld if abbruch else warten
        if sl_zeit_s is not None and not abbruch and warten <= sl_zeit_s:
            s[2] += 1.0
        s[3] += abbruch

    def schaetzung(index: int) -> tuple[float, float]:
        werte = [s[index] / pro_batch for s in summen]
        mittel = math.fsum(werte) / batches
        var = math.fsum((w - mittel) ** 2 for w in werte) / (batches - 1)
        return mittel, math.sqrt(var / batches)

    p_w, se_w = schaetzung(0)
    wq, se_q = schaetzung(1)
    sl, se_sl = schaetzung(2)
    p_ab, se_ab = schaetzung(3)
    return SimErgebnis(
        anrufe=pro_batch * batches,
        p_warten=p_w,
        mittlere_wartezeit_s=wq,
        servicelevel=sl if sl_zeit_s is not None else None,
        p_abbruch=p_ab,
        standardfehler={
            "p_warten": se_w,
            "mittlere_wartezeit_s": se_q,
            "servicelevel": se_sl,
            "p_abbruch": se_ab,
        },
    )
