"""Warteschlangen-Mathematik für Contact-Center: Erlang-B, Erlang-C, Erlang-A.

Modell und Annahmen
-------------------
Erlang-C beschreibt eine M/M/n-Warteschlange: Anrufe kommen als
Poisson-Prozess mit Rate λ, die Bearbeitungszeiten sind exponentialverteilt
mit Mittelwert ``h`` (AHT, Average Handling Time), ``n`` gleichartige
Agentinnen und Agenten bedienen in Ankunftsreihenfolge, niemand legt auf.

Mit der Angebotslast ``A = λ · h`` (in Erlang) gelten:

* Erlang-B (Verlustsystem M/M/n/n) über die numerisch stabile Rekursion
  ``B(0) = 1``, ``B(k) = A·B(k-1) / (k + A·B(k-1))``.
* Wartewahrscheinlichkeit ``C(n, A) = n·B / (n − A·(1 − B))``, nur für ``n > A``.
* Mittlere Wartezeit über alle Anrufe (ASA) ``W_q = C · h / (n − A)``.
* Servicelevel ``SL(t) = P(W ≤ t) = 1 − C · exp(−(n − A) · t / h)``.
* Auslastung (Occupancy) ``ρ = A / n``.

Für ``n ≤ A`` ist das System instabil (die Warteschlange wächst ohne
Grenze); die Funktionen lehnen das mit :class:`InstabilesSystemFehler` ab,
statt eine scheinbar gültige Zahl zu liefern.

Erlang-A (M/M/n+M) ergänzt exponentialverteilte Geduld mit Mittelwert
``geduld_s``: Wartende legen mit Rate ``θ = 1/geduld_s`` auf. Das System ist
dann für jedes ``n ≥ 1`` stabil. Berechnet wird die stationäre Verteilung
der Geburts-Todes-Kette direkt (im Log-Raum, abgeschnitten, sobald die
Restwahrscheinlichkeit vernachlässigbar ist); der Servicelevel über
Uniformisierung der Wartezeit eines markierten Anrufs. Beides ist exakt bis
auf die angegebene Abschneidetoleranz.

Validierung: ``tests/test_warteschlange.py`` vergleicht mit publizierten
Referenzwerten und ``tests/test_warteschlange_sim.py`` mit einer
unabhängigen ereignisdiskreten Simulation (:mod:`gw_contactcenter.warteschlange_sim`).
Quellen der Formeln: M. Zukerman, «Introduction to Queueing Theory and
Stochastic Teletraffic Models», arXiv:1307.2968 (Erlang-B-Rekursion,
Erlang-C, M/M/k-Wartezeitverteilung); O. Garnett, A. Mandelbaum,
M. Reiman, «Designing a Call Center with Impatient Customers», M&SOM 4(3),
2002 (Erlang-A).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "ErlangAErgebnis",
    "ErlangCErgebnis",
    "InstabilesSystemFehler",
    "angebot_erlang",
    "erlang_a",
    "erlang_b",
    "erlang_c",
    "kanaele_fuer_blockierung",
    "kennzahlen",
    "mittlere_wartezeit_s",
    "personalbedarf",
    "servicelevel",
]


class InstabilesSystemFehler(ValueError):
    """Angebotslast ≥ Anzahl Agenten: Erlang-C hat keine stationäre Lösung."""


def _pruefe_agenten(n: int) -> None:
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError(f"Anzahl Agenten muss eine ganze Zahl sein, nicht {type(n).__name__}")
    if n < 1:
        raise ValueError(f"Anzahl Agenten muss ≥ 1 sein, nicht {n}")


def _pruefe_positiv(name: str, wert: float, *, null_erlaubt: bool = False) -> None:
    if isinstance(wert, bool) or not isinstance(wert, (int, float)):
        raise TypeError(f"{name} muss eine Zahl sein, nicht {type(wert).__name__}")
    if not math.isfinite(wert):
        raise ValueError(f"{name} muss endlich sein, nicht {wert}")
    if wert < 0 or (wert == 0 and not null_erlaubt):
        grenze = "≥ 0" if null_erlaubt else "> 0"
        raise ValueError(f"{name} muss {grenze} sein, nicht {wert}")


def _pruefe_stabil(n: int, a: float) -> None:
    if n <= a:
        raise InstabilesSystemFehler(
            f"instabil: {n} Agenten bei {a:.4g} Erlang Angebotslast (nötig: n > A); "
            "die Warteschlange wächst ohne Grenze"
        )


def angebot_erlang(anrufe_pro_stunde: float, aht_s: float) -> float:
    """Angebotslast A = λ · h in Erlang (λ in Anrufen/Stunde, h in Sekunden)."""
    _pruefe_positiv("anrufe_pro_stunde", anrufe_pro_stunde, null_erlaubt=True)
    _pruefe_positiv("aht_s", aht_s)
    return anrufe_pro_stunde * aht_s / 3600.0


def erlang_b(n: int, a: float) -> float:
    """Blockierwahrscheinlichkeit des Verlustsystems M/M/n/n (Erlang-B).

    Iterative Rekursion statt Fakultäten: stabil auch für n in den Tausenden.
    """
    _pruefe_agenten(n)
    _pruefe_positiv("Angebotslast A", a, null_erlaubt=True)
    b = 1.0
    for k in range(1, n + 1):
        b = a * b / (k + a * b)
    return b


def erlang_c(n: int, a: float) -> float:
    """Wahrscheinlichkeit, dass ein Anruf warten muss (M/M/n, Erlang-C)."""
    _pruefe_agenten(n)
    _pruefe_positiv("Angebotslast A", a, null_erlaubt=True)
    _pruefe_stabil(n, a)
    b = erlang_b(n, a)
    return n * b / (n - a * (1.0 - b))


def mittlere_wartezeit_s(n: int, a: float, aht_s: float) -> float:
    """Mittlere Wartezeit über alle Anrufe (ASA): W_q = C(n, A) · h / (n − A)."""
    _pruefe_positiv("aht_s", aht_s)
    return erlang_c(n, a) * aht_s / (n - a)


def servicelevel(n: int, a: float, aht_s: float, ziel_s: float) -> float:
    """Anteil der Anrufe mit Wartezeit ≤ ziel_s: 1 − C · exp(−(n − A) · t / h)."""
    _pruefe_positiv("aht_s", aht_s)
    _pruefe_positiv("ziel_s", ziel_s, null_erlaubt=True)
    c = erlang_c(n, a)
    return 1.0 - c * math.exp(-(n - a) * ziel_s / aht_s)


@dataclass(frozen=True)
class ErlangCErgebnis:
    """Kennzahlen einer M/M/n-Warteschlange (Erlang-C)."""

    agenten: int
    angebot_erlang: float
    aht_s: float
    p_warten: float
    mittlere_wartezeit_s: float
    auslastung: float
    sl_zeit_s: float | None = None
    servicelevel: float | None = None

    @property
    def sofort_beantwortet(self) -> float:
        """Anteil der Anrufe ohne Wartezeit (1 − C)."""
        return 1.0 - self.p_warten

    @property
    def mittlere_wartezeit_wartender_s(self) -> float:
        """Mittlere Wartezeit derjenigen, die überhaupt warten: h / (n − A)."""
        return self.aht_s / (self.agenten - self.angebot_erlang)


def kennzahlen(n: int, a: float, aht_s: float, sl_zeit_s: float | None = None) -> ErlangCErgebnis:
    """Alle Erlang-C-Kennzahlen für n Agenten auf einmal."""
    c = erlang_c(n, a)
    _pruefe_positiv("aht_s", aht_s)
    sl: float | None = None
    if sl_zeit_s is not None:
        _pruefe_positiv("sl_zeit_s", sl_zeit_s, null_erlaubt=True)
        sl = 1.0 - c * math.exp(-(n - a) * sl_zeit_s / aht_s)
    return ErlangCErgebnis(
        agenten=n,
        angebot_erlang=a,
        aht_s=aht_s,
        p_warten=c,
        mittlere_wartezeit_s=c * aht_s / (n - a),
        auslastung=a / n,
        sl_zeit_s=sl_zeit_s,
        servicelevel=sl,
    )


_EPS = 1e-12


def personalbedarf(
    a: float,
    aht_s: float,
    *,
    ziel_mittlere_wartezeit_s: float | None = None,
    ziel_servicelevel: float | None = None,
    sl_zeit_s: float | None = None,
    max_auslastung: float | None = None,
    max_agenten: int = 100_000,
) -> ErlangCErgebnis:
    """Kleinstes n, das alle angegebenen Ziele erfüllt (Erlang-C).

    Ziele (mindestens eines angeben):

    * ``ziel_mittlere_wartezeit_s``: W_q ≤ Ziel
    * ``ziel_servicelevel`` zusammen mit ``sl_zeit_s``: SL(t) ≥ Ziel, z. B. 0.8 / 20 s
    * ``max_auslastung``: A / n ≤ Grenze
    """
    _pruefe_positiv("Angebotslast A", a, null_erlaubt=True)
    _pruefe_positiv("aht_s", aht_s)
    if ziel_mittlere_wartezeit_s is None and ziel_servicelevel is None and max_auslastung is None:
        raise ValueError("mindestens ein Ziel angeben (Wartezeit, Servicelevel oder Auslastung)")
    if ziel_servicelevel is not None:
        if sl_zeit_s is None:
            raise ValueError("ziel_servicelevel braucht sl_zeit_s (z. B. 20 s)")
        if not 0.0 < ziel_servicelevel < 1.0:
            raise ValueError("ziel_servicelevel muss zwischen 0 und 1 liegen (exklusiv)")
    if ziel_mittlere_wartezeit_s is not None:
        _pruefe_positiv("ziel_mittlere_wartezeit_s", ziel_mittlere_wartezeit_s, null_erlaubt=True)
    if max_auslastung is not None and not 0.0 < max_auslastung <= 1.0:
        raise ValueError("max_auslastung muss in (0, 1] liegen")

    n = max(1, math.floor(a) + 1)
    while n <= max_agenten:
        k = kennzahlen(n, a, aht_s, sl_zeit_s)
        ok = True
        if ziel_mittlere_wartezeit_s is not None:
            ok &= k.mittlere_wartezeit_s <= ziel_mittlere_wartezeit_s + _EPS
        if ziel_servicelevel is not None:
            ok &= k.servicelevel is not None and k.servicelevel >= ziel_servicelevel - _EPS
        if max_auslastung is not None:
            ok &= k.auslastung <= max_auslastung + _EPS
        if ok:
            return k
        n += 1
    raise ValueError(f"kein n ≤ {max_agenten} erfüllt die Ziele")


def kanaele_fuer_blockierung(a: float, ziel_blockierung: float, max_kanaele: int = 100_000) -> int:
    """Kleinste Kanalzahl n mit Erlang-B(n, A) ≤ ziel_blockierung.

    Passt für einen Sprachagenten, der jeden Anruf sofort selbst annimmt:
    Sind alle Kanäle belegt, geht der Anruf an die normale Warteschlange
    (Überlauf) statt zu warten. Das ist ein Verlustsystem, kein Erlang-C.
    """
    _pruefe_positiv("Angebotslast A", a, null_erlaubt=True)
    if not 0.0 < ziel_blockierung < 1.0:
        raise ValueError("ziel_blockierung muss zwischen 0 und 1 liegen (exklusiv)")
    b = 1.0
    for n in range(1, max_kanaele + 1):
        b = a * b / (n + a * b)
        if b <= ziel_blockierung + _EPS:
            return n
    raise ValueError(f"kein n ≤ {max_kanaele} erreicht Blockierung ≤ {ziel_blockierung}")


# ---------------------------------------------------------------------------
# Erlang-A (M/M/n+M): Warteschlange mit Abbrüchen
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ErlangAErgebnis:
    """Kennzahlen einer M/M/n+M-Warteschlange (Erlang-A)."""

    agenten: int
    angebot_erlang: float
    aht_s: float
    geduld_s: float
    p_warten: float
    p_abbruch: float
    mittlere_wartezeit_s: float
    mittlere_warteschlange: float
    auslastung: float
    sl_zeit_s: float | None = None
    servicelevel: float | None = None
    zustaende: int = 0


def _logsumexp(werte: list[float]) -> float:
    m = max(werte)
    return m + math.log(math.fsum(math.exp(w - m) for w in werte))


def erlang_a(
    n: int,
    a: float,
    aht_s: float,
    geduld_s: float,
    sl_zeit_s: float | None = None,
    *,
    abschneiden: float = 1e-15,
    max_zustaende: int = 2_000_000,
) -> ErlangAErgebnis:
    """Erlang-A: M/M/n mit exponentieller Geduld (Mittelwert ``geduld_s``).

    Kennzahlen pro angebotenem Anruf:

    * ``p_warten``: findet alle Agenten besetzt vor (PASTA)
    * ``p_abbruch``: legt vor Gesprächsbeginn auf (θ · E[Q] / λ)
    * ``mittlere_wartezeit_s``: mittlere Zeit in der Warteschlange inklusive
      der Abbrechenden (Little: E[Q] / λ)
    * ``servicelevel``: Anteil der Anrufe, die innert ``sl_zeit_s`` ein
      Gespräch beginnen (Abbrüche zählen als nicht erreicht)
    * ``auslastung``: Anteil der Zeit, in der Agenten sprechen
    """
    _pruefe_agenten(n)
    _pruefe_positiv("Angebotslast A", a, null_erlaubt=True)
    _pruefe_positiv("aht_s", aht_s)
    _pruefe_positiv("geduld_s", geduld_s)
    if sl_zeit_s is not None:
        _pruefe_positiv("sl_zeit_s", sl_zeit_s, null_erlaubt=True)
    if a == 0:
        return ErlangAErgebnis(
            n,
            a,
            aht_s,
            geduld_s,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            sl_zeit_s,
            None if sl_zeit_s is None else 1.0,
            1,
        )

    mu = 1.0 / aht_s
    theta = 1.0 / geduld_s
    lam = a * mu
    log_lam = math.log(lam)

    # Stationäre Verteilung π_k ∝ Π_{j≤k} λ / Tod_j, im Log-Raum.
    log_pi = [0.0]
    lp, spitze, k = 0.0, 0.0, 0
    while True:
        k += 1
        tod = min(k, n) * mu + max(k - n, 0) * theta
        lp += log_lam - math.log(tod)
        log_pi.append(lp)
        spitze = max(spitze, lp)
        # Ab hier fallen die Terme monoton, sobald λ < Tod_k gilt.
        if k > n and lam < tod and lp < spitze + math.log(abschneiden):
            break
        if k >= max_zustaende:
            raise ValueError(
                f"Erlang-A: mehr als {max_zustaende} Zustände nötig "
                "(sehr hohe Überlast bei sehr grosser Geduld); Parameter prüfen"
            )
    log_norm = _logsumexp(log_pi)
    pi = [math.exp(x - log_norm) for x in log_pi]

    p_frei = math.fsum(pi[:n])
    p_warten = math.fsum(pi[n:])  # direkt summiert: genau auch bei sehr kleinen Werten
    erw_q = math.fsum((i - n) * p for i, p in enumerate(pi) if i > n)
    besetzt = math.fsum(min(i, n) * p for i, p in enumerate(pi))

    sl: float | None = None
    if sl_zeit_s is not None:
        sl = p_frei + _erlang_a_bedient_innert(pi[n:], n, mu, theta, sl_zeit_s, abschneiden)

    return ErlangAErgebnis(
        agenten=n,
        angebot_erlang=a,
        aht_s=aht_s,
        geduld_s=geduld_s,
        p_warten=p_warten,
        p_abbruch=theta * erw_q / lam,
        mittlere_wartezeit_s=erw_q / lam,
        mittlere_warteschlange=erw_q,
        auslastung=besetzt / n,
        sl_zeit_s=sl_zeit_s,
        servicelevel=sl,
        zustaende=len(pi),
    )


def _erlang_a_bedient_innert(
    vor: list[float], n: int, mu: float, theta: float, t: float, abschneiden: float
) -> float:
    """P(Anruf trifft auf volle Agenten UND wird innert t bedient).

    ``vor[i]`` = Wahrscheinlichkeit, dass ein Anruf i Wartende vor sich
    vorfindet. Mit i Wartenden vor sich rückt der markierte Anruf mit Rate
    n·μ + i·θ vor (Gesprächsende oder Abbruch eines Vorderen) und bricht
    selbst mit Rate θ ab. Die Übergangszeit bis «bedient» wird per
    Uniformisierung berechnet: exakt bis auf die Poisson-Abschneidung.
    """
    letzte = max((i for i, p in enumerate(vor) if p > abschneiden), default=-1)
    if letzte < 0 or t == 0:
        return 0.0
    v = vor[: letzte + 1]
    rate = n * mu + (letzte + 1) * theta  # grösste Austrittsrate
    vorruecken = [(n * mu + i * theta) / rate for i in range(len(v))]
    bleiben = [1.0 - (n * mu + (i + 1) * theta) / rate for i in range(len(v))]
    lt = rate * t
    m_max = int(lt + 12.0 * math.sqrt(lt) + 50)
    bedient = 0.0
    summe = 0.0
    log_lt = math.log(lt)
    for m in range(m_max + 1):
        # Poisson-Gewicht P(M = m) mit Parameter λt, über die Zählverteilung
        # der Uniformisierung: bedient innert t ⇔ bedient nach höchstens M Sprüngen.
        gewicht = math.exp(m * log_lt - lt - math.lgamma(m + 1))
        summe += gewicht * bedient
        if m == m_max:
            break
        neu = [0.0] * len(v)
        bedient += v[0] * vorruecken[0]
        for i in range(len(v)):
            neu[i] += v[i] * bleiben[i]
            if i > 0:
                neu[i - 1] += v[i] * vorruecken[i]
        v = neu
    return summe
