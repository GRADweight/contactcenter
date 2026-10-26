"""Kapazitätsrechner: vom Tagesvolumen zur Personal- und Kanalzahl.

Schritte (alle Eingaben sind Parameter; die Standardwerte sind Annahmen):

1. Spitzenstunde: ``Anrufe pro Tag × Anteil der Spitzenstunde``. Der Anteil
   ist eine **Annahme** (Standard 25 %) und muss durch eigene ACD-Daten
   ersetzt werden; er hängt stark von Branche und Öffnungszeiten ab.
2. Angebotslast ``A = λ · AHT`` in Erlang.
3. Menschliche Agenten: Erlang-C (M/M/n), kleinstes n für die Ziele
   (mittlere Wartezeit und/oder Servicelevel).
4. Sprachagent: Ein Bot nimmt jeden Anruf sofort an, solange ein Kanal frei
   ist; sonst läuft der Anruf über (z. B. in die normale Warteschlange).
   Das ist ein Verlustsystem: Erlang-B, kleinste Kanalzahl für die Ziel-
   Überlaufquote.
5. Optional Erlang-A (Abbrüche) und Speicherbedarf für Aufzeichnungen.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .warteschlange import (
    ErlangAErgebnis,
    ErlangCErgebnis,
    angebot_erlang,
    erlang_a,
    erlang_b,
    kanaele_fuer_blockierung,
    kennzahlen,
    personalbedarf,
)

__all__ = [
    "AUDIOFORMATE",
    "Kapazitaetsbericht",
    "Lastannahme",
    "Speicherbedarf",
    "berechne",
    "speicherbedarf",
]


@dataclass(frozen=True)
class Lastannahme:
    anrufe_pro_tag: float
    spitzenstunden_anteil: float = 0.25  # ANNAHME, durch eigene ACD-Daten ersetzen
    aht_s: float = 120.0

    def __post_init__(self) -> None:
        if self.anrufe_pro_tag <= 0:
            raise ValueError("anrufe_pro_tag muss > 0 sein")
        if not 0.0 < self.spitzenstunden_anteil <= 1.0:
            raise ValueError("spitzenstunden_anteil muss in (0, 1] liegen")
        if self.aht_s <= 0:
            raise ValueError("aht_s muss > 0 sein")

    @property
    def anrufe_spitzenstunde(self) -> float:
        return self.anrufe_pro_tag * self.spitzenstunden_anteil

    @property
    def angebot_erlang(self) -> float:
        return angebot_erlang(self.anrufe_spitzenstunde, self.aht_s)


@dataclass(frozen=True)
class Kapazitaetsbericht:
    last: Lastannahme
    sl_zeit_s: float
    ziel_mittlere_wartezeit_s: float | None
    ziel_servicelevel: float | None
    personal: ErlangCErgebnis
    tabelle: list[ErlangCErgebnis]
    ziel_ueberlauf: float
    bot_kanaele: int
    bot_ueberlauf: float
    abwesenheit: float = 0.0
    abbrueche: ErlangAErgebnis | None = None
    hinweise: list[str] = field(default_factory=list)

    @property
    def koepfe(self) -> int:
        """Agenten inklusive Abwesenheiten (Pausen, Schulung, Krankheit)."""
        return math.ceil(self.personal.agenten / (1.0 - self.abwesenheit) - 1e-9)

    def als_text(self) -> str:
        last = self.last
        z = [
            "Kapazitätsrechner (Erlang-C, M/M/n; Sprachagent: Erlang-B)",
            "Annahmen",
            f"  Anrufe pro Tag              {_zahl(last.anrufe_pro_tag)}",
            f"  Anteil Spitzenstunde        {_pct(last.spitzenstunden_anteil, 0)}"
            "  (Annahme, durch eigene ACD-Daten ersetzen)",
            f"  Anrufe in der Spitzenstunde {last.anrufe_spitzenstunde:.1f}",
            f"  Mittlere Bearbeitungszeit   {_zahl(last.aht_s)} s",
            f"  Angebotslast A              {last.angebot_erlang:.3f} Erlang",
            "Ziele",
        ]
        if self.ziel_mittlere_wartezeit_s is not None:
            z.append(f"  mittlere Wartezeit (ASA) <= {_zahl(self.ziel_mittlere_wartezeit_s)} s")
        if self.ziel_servicelevel is not None:
            z.append(
                f"  Servicelevel >= {_pct(self.ziel_servicelevel, 0)} "
                f"innert {_zahl(self.sl_zeit_s)} s"
            )
        z += [
            "",
            f"Agenten  P(warten)  ASA s   SL {_zahl(self.sl_zeit_s)} s  Auslastung",
        ]
        for k in self.tabelle:
            markierung = "  <- kleinste Zahl, die alle Ziele erfüllt" if k is self.personal else ""
            sl = k.servicelevel if k.servicelevel is not None else float("nan")
            z.append(
                f"{k.agenten:>7}  {_pct(k.p_warten):>9}  {k.mittlere_wartezeit_s:>5.1f}  "
                f"{_pct(sl):>8}  {_pct(k.auslastung):>11}{markierung}"
            )
        z += [
            "",
            f"Ergebnis: {self.personal.agenten} Agenten gleichzeitig in der Spitzenstunde"
            + (
                f", {self.koepfe} mit {_pct(self.abwesenheit, 0)} Abwesenheit."
                if self.abwesenheit
                else " (ohne Pausen und Abwesenheiten)."
            ),
            f"Sprachagent (Erlang-B): {self.bot_kanaele} Kanäle für "
            f"<= {_pct(self.ziel_ueberlauf, 0)} "
            f"Überlauf (berechnet {_pct(self.bot_ueberlauf, 2)}).",
        ]
        if self.abbrueche is not None:
            a = self.abbrueche
            sl = a.servicelevel if a.servicelevel is not None else float("nan")
            z.append(
                f"Mit Abbrüchen (Erlang-A, Geduld {_zahl(a.geduld_s)} s) bei {a.agenten} Agenten: "
                f"{_pct(a.p_abbruch)} legen auf, SL {_zahl(self.sl_zeit_s)} s {_pct(sl)}."
            )
        z += self.hinweise
        return "\n".join(z)


def _pct(x: float, stellen: int = 1) -> str:
    """Prozent in Schweizer Schreibweise (mit Leerzeichen)."""
    return f"{100 * x:.{stellen}f} %"


def _zahl(x: float) -> str:
    return f"{x:,.0f}".replace(",", "'") if float(x).is_integer() else f"{x:g}"


def berechne(
    last: Lastannahme,
    *,
    ziel_mittlere_wartezeit_s: float | None = 20.0,
    ziel_servicelevel: float | None = None,
    sl_zeit_s: float = 20.0,
    ziel_ueberlauf: float = 0.01,
    abwesenheit: float = 0.0,
    geduld_s: float | None = None,
) -> Kapazitaetsbericht:
    if not 0.0 <= abwesenheit < 1.0:
        raise ValueError("abwesenheit muss in [0, 1) liegen")
    a = last.angebot_erlang
    personal = personalbedarf(
        a,
        last.aht_s,
        ziel_mittlere_wartezeit_s=ziel_mittlere_wartezeit_s,
        ziel_servicelevel=ziel_servicelevel,
        sl_zeit_s=sl_zeit_s,
    )
    start = max(1, math.floor(a) + 1)
    tabelle = [
        personal if n == personal.agenten else kennzahlen(n, a, last.aht_s, sl_zeit_s)
        for n in range(start, max(personal.agenten + 2, start + 3))
    ]
    kanaele = kanaele_fuer_blockierung(a, ziel_ueberlauf)
    abbrueche = (
        erlang_a(personal.agenten, a, last.aht_s, geduld_s, sl_zeit_s)
        if geduld_s is not None
        else None
    )
    return Kapazitaetsbericht(
        last=last,
        sl_zeit_s=sl_zeit_s,
        ziel_mittlere_wartezeit_s=ziel_mittlere_wartezeit_s,
        ziel_servicelevel=ziel_servicelevel,
        personal=personal,
        tabelle=tabelle,
        ziel_ueberlauf=ziel_ueberlauf,
        bot_kanaele=kanaele,
        bot_ueberlauf=erlang_b(kanaele, a),
        abwesenheit=abwesenheit,
        abbrueche=abbrueche,
    )


# ---------------------------------------------------------------------------
# Speicherbedarf für Aufzeichnungen und Transkripte
# ---------------------------------------------------------------------------

# Byte pro Sekunde Gesprächsdauer.
AUDIOFORMATE: dict[str, float] = {
    # G.711: 8000 Samples/s x 1 Byte; Stereo = Kundschaft und Agent getrennt.
    "g711-stereo": 8000 * 1 * 2,
    "g711-mono": 8000 * 1,
    # Opus mit 32 kbit/s gesamt (Bitrate ist eine Konfigurationsentscheidung).
    "opus-32kbit": 32_000 / 8,
}


@dataclass(frozen=True)
class Speicherbedarf:
    audio_byte_pro_tag: float
    text_byte_pro_tag: float
    format: str

    @property
    def gesamt_byte_pro_tag(self) -> float:
        return self.audio_byte_pro_tag + self.text_byte_pro_tag


def speicherbedarf(
    anrufe_pro_tag: float,
    dauer_s_pro_anruf: float,
    *,
    format: str = "g711-stereo",
    tokens_pro_anruf: int = 4000,
    byte_pro_token: float = 4.0,
) -> Speicherbedarf:
    """Täglicher Speicherbedarf für Ton und Transkript.

    ``tokens_pro_anruf`` und ``byte_pro_token`` (UTF-8, deutscher Text) sind
    Annahmen; der Ton dominiert ohnehin um Grössenordnungen.
    """
    if format not in AUDIOFORMATE:
        raise ValueError(f"Format {format!r} unbekannt, erlaubt: {sorted(AUDIOFORMATE)}")
    if anrufe_pro_tag < 0 or dauer_s_pro_anruf < 0:
        raise ValueError("anrufe_pro_tag und dauer_s_pro_anruf müssen ≥ 0 sein")
    return Speicherbedarf(
        audio_byte_pro_tag=AUDIOFORMATE[format] * dauer_s_pro_anruf * anrufe_pro_tag,
        text_byte_pro_tag=tokens_pro_anruf * byte_pro_token * anrufe_pro_tag,
        format=format,
    )
