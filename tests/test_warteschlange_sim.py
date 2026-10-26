"""Unabhängige Gegenprobe: ereignisdiskrete Simulation gegen die Erlang-Formeln.

Die Simulation enthält keine Erlang-Formel. Toleranz: Abweichung höchstens
4.5 Standardfehler (Batch-Means) plus eine kleine absolute Schranke. Die Seeds
sind fest, die Tests also reproduzierbar; die Toleranz ist trotzdem
statistisch begründet und nicht auf einen bestimmten Seed abgestimmt.
"""

from __future__ import annotations

import pytest

from gw_contactcenter.warteschlange import angebot_erlang, erlang_a, kennzahlen
from gw_contactcenter.warteschlange_sim import simuliere

K = 4.5


def _nahe(formel: float, sim: float, se: float, abs_tol: float = 2e-3) -> bool:
    return abs(formel - sim) <= K * se + abs_tol


# (Anrufe pro Stunde, AHT s, Agenten, Seed)
ERLANG_C_FAELLE = [
    (50, 120, 3, 11),  # README-Beispiel, 3 Agenten
    (50, 120, 4, 12),  # README-Beispiel, 4 Agenten
    (200, 180, 13, 13),  # Call Centre Helper
    (200, 180, 14, 14),
    (360, 300, 33, 15),  # hohe Auslastung (91 %)
    (12, 600, 4, 16),  # kleine Last, lange Gespräche
]


@pytest.mark.parametrize(("pro_h", "aht", "n", "seed"), ERLANG_C_FAELLE)
def test_erlang_c_gegen_simulation(pro_h: float, aht: float, n: int, seed: int) -> None:
    a = angebot_erlang(pro_h, aht)
    k = kennzahlen(n, a, aht, 20)
    s = simuliere(pro_h / 3600, aht, n, anrufe=200_000, seed=seed, sl_zeit_s=20)
    se = s.standardfehler
    assert _nahe(k.p_warten, s.p_warten, se["p_warten"])
    assert _nahe(k.mittlere_wartezeit_s, s.mittlere_wartezeit_s, se["mittlere_wartezeit_s"], 0.05)
    assert k.servicelevel is not None and s.servicelevel is not None
    assert _nahe(k.servicelevel, s.servicelevel, se["servicelevel"])
    assert s.p_abbruch == 0.0


@pytest.mark.parametrize(
    ("pro_h", "aht", "n", "geduld", "seed"),
    [(200, 180, 10, 60, 21), (200, 180, 8, 120, 22), (50, 120, 2, 30, 23)],
)
def test_erlang_a_gegen_simulation(
    pro_h: float, aht: float, n: int, geduld: float, seed: int
) -> None:
    a = angebot_erlang(pro_h, aht)
    ea = erlang_a(n, a, aht, geduld, sl_zeit_s=20)
    s = simuliere(pro_h / 3600, aht, n, anrufe=200_000, seed=seed, sl_zeit_s=20, geduld_s=geduld)
    se = s.standardfehler
    assert _nahe(ea.p_warten, s.p_warten, se["p_warten"])
    assert _nahe(ea.p_abbruch, s.p_abbruch, se["p_abbruch"])
    assert _nahe(ea.mittlere_wartezeit_s, s.mittlere_wartezeit_s, se["mittlere_wartezeit_s"], 0.05)
    assert ea.servicelevel is not None and s.servicelevel is not None
    assert _nahe(ea.servicelevel, s.servicelevel, se["servicelevel"])


def test_simulation_reproduzierbar_und_seed_abhaengig() -> None:
    a = simuliere(50 / 3600, 120, 3, anrufe=20_000, einschwingen=1000, seed=5)
    b = simuliere(50 / 3600, 120, 3, anrufe=20_000, einschwingen=1000, seed=5)
    c = simuliere(50 / 3600, 120, 3, anrufe=20_000, einschwingen=1000, seed=6)
    assert a == b
    assert a.p_warten != c.p_warten
    assert a.servicelevel is None  # ohne sl_zeit_s


def test_exponential_annahme_ist_relevant() -> None:
    """M/G/n mit weniger variablen Gesprächen (Lognormal, CV 0.5) wartet kürzer
    als Erlang-C vorhersagt: Die Formel ist dann konservativ. Das zeigt, warum
    die Verteilungsannahme in der Doku steht."""
    a = angebot_erlang(200, 180)
    k = kennzahlen(13, a, 180, 20)
    s = simuliere(200 / 3600, 180, 13, anrufe=200_000, seed=31, bearbeitung_cv=0.5)
    assert (
        s.mittlere_wartezeit_s
        < k.mittlere_wartezeit_s - K * s.standardfehler["mittlere_wartezeit_s"]
    )


@pytest.mark.parametrize(
    "kw",
    [
        {"agenten": 0},
        {"ankunftsrate_pro_s": 0.0},
        {"anrufe": 5, "batches": 20},
        {"batches": 1},
        {"bearbeitung_cv": 0.0},
        {"geduld_s": 0.0},
    ],
)
def test_eingaben_werden_geprueft(kw: dict[str, float]) -> None:
    args = {"ankunftsrate_pro_s": 0.01, "aht_s": 120.0, "agenten": 2}
    args.update(kw)
    with pytest.raises(ValueError):
        simuliere(**args)  # type: ignore[arg-type]
