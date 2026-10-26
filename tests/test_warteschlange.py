"""Erlang-B/-C/-A gegen publizierte Referenzwerte und mathematische Eigenschaften."""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gw_contactcenter.warteschlange import (
    InstabilesSystemFehler,
    angebot_erlang,
    erlang_a,
    erlang_b,
    erlang_c,
    kanaele_fuer_blockierung,
    kennzahlen,
    mittlere_wartezeit_s,
    personalbedarf,
    servicelevel,
)

# Referenz 1: Call Centre Helper, «Erlang C Formula – Made Simple With an Easy
# Worked Example» (https://www.callcentrehelper.com/erlang-c-formula-24911.htm):
# 100 Anrufe in 30 min, AHT 180 s -> A = 10 Erlang, Ziel 20 s.
# Referenz 2 (gleiches Beispiel, 7 Stellen): README des R-Pakets ErlangC auf CRAN
# (https://cran.r-universe.dev/ErlangC/doc/readme).
# (Agenten, P(warten), Servicelevel 20 s, ASA in s, Auslastung)
CRAN_ERLANGC = [
    (11, 0.6821182, 0.3896138, 122.78127684408, 0.9090909),
    (12, 0.4493882, 0.640158, 40.4449401868444, 0.8333333),
    (13, 0.2852705, 0.7955948, 17.1162271821896, 0.7692308),
    (14, 0.1741319, 0.88835, 7.83593701177724, 0.7142857),
]


@pytest.mark.parametrize(("n", "p_w", "sl", "asa", "rho"), CRAN_ERLANGC)
def test_referenzwerte_cran_und_call_centre_helper(
    n: int, p_w: float, sl: float, asa: float, rho: float
) -> None:
    a = angebot_erlang(200, 180)  # 100 Anrufe pro 30 min = 200 pro Stunde
    assert a == pytest.approx(10.0)
    k = kennzahlen(n, a, 180, 20)
    assert k.p_warten == pytest.approx(p_w, abs=1e-7)
    assert k.servicelevel == pytest.approx(sl, abs=1e-6)
    assert k.mittlere_wartezeit_s == pytest.approx(asa, rel=1e-9)
    assert k.auslastung == pytest.approx(rho, abs=1e-7)


def test_call_centre_helper_ergebnis_14_agenten() -> None:
    """Gerundete Werte des Artikels: 14 Agenten für 80/20, ASA 7.8 s, 82.6 % sofort."""
    k = personalbedarf(10.0, 180, ziel_servicelevel=0.8, sl_zeit_s=20)
    assert k.agenten == 14
    assert round(k.mittlere_wartezeit_s, 1) == 7.8
    assert round(k.sofort_beantwortet * 100, 1) == 82.6
    assert [round(100 * servicelevel(n, 10.0, 180, 20), 1) for n in (11, 12, 13)] == [
        39.0,
        64.0,
        79.6,
    ]


def test_erlang_b_grosses_system_erlang_com() -> None:
    """erlang.com, ErlBBlocking(1500, 1000) «should be 0.335»: 1000 Leitungen bei
    1500 Erlang. Mit Fakultäten liefe das über; die Rekursion ist stabil."""
    assert round(erlang_b(1000, 1500.0), 3) == 0.335


def test_bekannter_wert_c_10_8() -> None:
    assert erlang_c(10, 8.0) == pytest.approx(0.409, abs=5e-4)


def test_formel_wartezeit_mit_aht() -> None:
    """Der frühere Fehler (W_q = C/(n−A) ohne AHT) ergab 13.5 s statt 27.0 s."""
    a = angebot_erlang(50, 120)
    c = erlang_c(3, a)
    assert mittlere_wartezeit_s(3, a, 120) == pytest.approx(c * 120 / (3 - a))
    assert round(mittlere_wartezeit_s(3, a, 120), 1) == 27.0
    assert round(c / (3 - a) * 60, 1) == 13.5  # die alte, falsche Formel


def test_instabil_wird_abgelehnt() -> None:
    for n, a in [(3, 3.0), (2, 2.5), (1, 7.0)]:
        with pytest.raises(InstabilesSystemFehler):
            erlang_c(n, a)
        with pytest.raises(InstabilesSystemFehler):
            kennzahlen(n, a, 120)


@pytest.mark.parametrize(
    ("aufruf", "fehler"),
    [
        (lambda: erlang_b(0, 1.0), ValueError),
        (lambda: erlang_b(2.5, 1.0), TypeError),  # type: ignore[arg-type]
        (lambda: erlang_b(True, 1.0), TypeError),  # type: ignore[arg-type]
        (lambda: erlang_b(3, -1.0), ValueError),
        (lambda: erlang_b(3, math.nan), ValueError),
        (lambda: erlang_b(3, "1"), TypeError),  # type: ignore[arg-type]
        (lambda: mittlere_wartezeit_s(3, 1.0, 0), ValueError),
        (lambda: servicelevel(3, 1.0, 120, -1), ValueError),
        (lambda: angebot_erlang(-1, 120), ValueError),
        (lambda: personalbedarf(1.0, 120), ValueError),
        (lambda: personalbedarf(1.0, 120, ziel_servicelevel=0.8), ValueError),
        (lambda: personalbedarf(1.0, 120, ziel_servicelevel=1.2, sl_zeit_s=20), ValueError),
        (lambda: personalbedarf(1.0, 120, max_auslastung=0.0), ValueError),
        (lambda: personalbedarf(50.0, 120, max_auslastung=0.99, max_agenten=10), ValueError),
        (lambda: kanaele_fuer_blockierung(1.0, 0.0), ValueError),
        (lambda: kanaele_fuer_blockierung(100.0, 1e-9, max_kanaele=5), ValueError),
        (lambda: erlang_a(3, 1.0, 120, 0), ValueError),
        (lambda: erlang_a(3, 1.0, 120, 60, -1), ValueError),
    ],
)
def test_eingaben_werden_geprueft(aufruf: object, fehler: type[Exception]) -> None:
    with pytest.raises(fehler):
        aufruf()  # type: ignore[operator]


def test_null_last() -> None:
    assert erlang_b(3, 0.0) == 0.0
    assert erlang_c(3, 0.0) == 0.0
    assert kennzahlen(1, 0.0, 120, 20).servicelevel == 1.0
    assert personalbedarf(0.0, 120, ziel_mittlere_wartezeit_s=0).agenten == 1
    ea = erlang_a(2, 0.0, 120, 60, 20)
    assert (ea.p_warten, ea.p_abbruch, ea.servicelevel) == (0.0, 0.0, 1.0)


def test_personalbedarf_mehrere_ziele() -> None:
    a = angebot_erlang(50, 120)
    assert personalbedarf(a, 120, ziel_mittlere_wartezeit_s=20).agenten == 4
    assert personalbedarf(a, 120, ziel_servicelevel=0.8, sl_zeit_s=20).agenten == 4
    assert personalbedarf(a, 120, ziel_servicelevel=0.7, sl_zeit_s=20).agenten == 3
    # Auslastungsgrenze kann allein bestimmen
    assert personalbedarf(a, 120, max_auslastung=0.3).agenten == 6
    k = personalbedarf(a, 120, ziel_mittlere_wartezeit_s=20)
    assert k.mittlere_wartezeit_wartender_s == pytest.approx(120 / (4 - a))


def test_kanaele_fuer_blockierung_erlang_b() -> None:
    a = angebot_erlang(50, 120)
    n = kanaele_fuer_blockierung(a, 0.01)
    assert n == 6
    assert erlang_b(n, a) <= 0.01 < erlang_b(n - 1, a)


# ---------------------------------------------------------------- Eigenschaften

lasten = st.floats(min_value=0.01, max_value=60.0, allow_nan=False)


@given(a=lasten, n=st.integers(min_value=1, max_value=120))
def test_erlang_b_ist_wahrscheinlichkeit_und_faellt(a: float, n: int) -> None:
    b = erlang_b(n, a)
    assert 0.0 <= b <= 1.0
    assert erlang_b(n + 1, a) <= b


@given(a=lasten, extra=st.integers(min_value=1, max_value=40))
def test_erlang_c_monoton_und_ueber_b(a: float, extra: int) -> None:
    n = math.floor(a) + extra
    c = erlang_c(n, a)
    assert erlang_b(n, a) <= c + 1e-15 <= 1.0 + 1e-15
    assert erlang_c(n + 1, a) <= c + 1e-15
    assert mittlere_wartezeit_s(n + 1, a, 120) <= mittlere_wartezeit_s(n, a, 120) + 1e-12


@given(
    a=lasten,
    extra=st.integers(min_value=1, max_value=20),
    t1=st.floats(min_value=0, max_value=300),
    t2=st.floats(min_value=0, max_value=300),
)
def test_servicelevel_zwischen_0_und_1_und_steigend(
    a: float, extra: int, t1: float, t2: float
) -> None:
    n = math.floor(a) + extra
    lo, hi = sorted((t1, t2))
    s_lo, s_hi = servicelevel(n, a, 180, lo), servicelevel(n, a, 180, hi)
    assert 0.0 <= s_lo <= s_hi + 1e-15 <= 1.0 + 1e-15
    assert servicelevel(n, a, 180, 0) == pytest.approx(1 - erlang_c(n, a))


@given(a=st.floats(min_value=0.1, max_value=40), ziel=st.floats(min_value=1, max_value=120))
def test_personalbedarf_ist_minimal(a: float, ziel: float) -> None:
    k = personalbedarf(a, 180, ziel_mittlere_wartezeit_s=ziel)
    assert k.mittlere_wartezeit_s <= ziel + 1e-9
    if k.agenten - 1 > a:
        assert mittlere_wartezeit_s(k.agenten - 1, a, 180) > ziel


# ------------------------------------------------------------------- Erlang-A


def test_erlang_a_grenzfall_unendliche_geduld_ist_erlang_c() -> None:
    a = angebot_erlang(50, 120)
    ea = erlang_a(4, a, 120, geduld_s=1e9, sl_zeit_s=20)
    k = kennzahlen(4, a, 120, 20)
    assert ea.p_warten == pytest.approx(k.p_warten, rel=1e-5)
    assert ea.mittlere_wartezeit_s == pytest.approx(k.mittlere_wartezeit_s, rel=1e-5)
    assert ea.servicelevel == pytest.approx(k.servicelevel, rel=1e-5)
    assert ea.p_abbruch == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize(("n", "a"), [(1, 0.5), (3, 2.5), (5, 7.0), (12, 10.0)])
def test_erlang_a_geduld_gleich_aht_ist_poisson(n: int, a: float) -> None:
    """Mit θ = μ ist M/M/n+M exakt M/M/∞: Anzahl im System ~ Poisson(A)."""
    ea = erlang_a(n, a, 120, geduld_s=120)

    def pmf(k: int) -> float:
        return math.exp(-a + k * math.log(a) - math.lgamma(k + 1))

    p_kleiner_n = sum(pmf(k) for k in range(n))
    assert ea.p_warten == pytest.approx(1 - p_kleiner_n, abs=1e-12)
    # E[(N - n)^+] / A für Poisson
    erw = sum((k - n) * pmf(k) for k in range(n + 1, 300))
    assert ea.p_abbruch == pytest.approx(erw / a, abs=1e-12)


def test_erlang_a_ist_auch_bei_ueberlast_stabil() -> None:
    ea = erlang_a(5, 8.0, 180, geduld_s=60, sl_zeit_s=20)
    assert 0 < ea.p_abbruch < 1
    assert ea.p_warten > 0.5
    assert ea.servicelevel is not None and 0 < ea.servicelevel < 1
    assert ea.auslastung == pytest.approx((8.0 * (1 - ea.p_abbruch)) / 5, rel=1e-9)


def test_erlang_a_zu_viele_zustaende() -> None:
    with pytest.raises(ValueError, match="Zustände"):
        erlang_a(1, 50.0, 180, geduld_s=1e9, max_zustaende=1000)


@settings(max_examples=40)
@given(
    a=st.floats(min_value=0.2, max_value=20),
    n=st.integers(min_value=1, max_value=25),
    geduld=st.floats(min_value=5, max_value=600),
)
def test_erlang_a_eigenschaften(a: float, n: int, geduld: float) -> None:
    ea = erlang_a(n, a, 180, geduld, sl_zeit_s=20)
    assert 0.0 <= ea.p_abbruch <= ea.p_warten <= 1.0 + 1e-12
    assert ea.servicelevel is not None
    assert 1.0 - ea.p_warten - 1e-9 <= ea.servicelevel <= 1.0 - ea.p_abbruch + 1e-9
    # Durchsatz: bediente Anrufe = besetzte Agenten (Little)
    assert ea.auslastung * n == pytest.approx(a * (1 - ea.p_abbruch), rel=1e-6)
