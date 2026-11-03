"""Anrufsimulator: ganze Telefonate als End-to-End-Test, ohne Hardware.

Ein Skript aus Kundenzügen wird zu 8-kHz-Audio (Sinustöne als «Sprache»,
leises Rauschen als Stille) und läuft Frame für Frame durch die ganze
Pipeline: VAD, Turn-Taking, ASR, Dialog, Gateway, TTS, Barge-in, Übergabe,
Audit, Latenz-Spans. Optional läuft das Audio zusätzlich durch das
AudioHook-Protokoll (mu-law, open/opened, disconnect).

**Was die Latenzwerte bedeuten:** Es ist eine Simulation auf simulierter
Uhr. ASR, LLM und TTS sind Mock-Komponenten mit fest eingestellten
Verzögerungen (:data:`gw_contactcenter.adapters.mock.MOCK_LATENZ_MS`). Nur
das Endpointing wird vom Algorithmus selbst bestimmt (Turn-Taking auf dem
synthetischen Audio). Die Simulation zeigt also, wie sich die Stufen
zusammensetzen und ob die Orchestrierung unnötige Wartezeit einbaut; sie
misst keine echten Modelle. Echte Werte: ``gw-contactcenter latenz-messen``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .adapters.mock import MOCK_LATENZ_MS, MockASR, MockTTS, RegelLLM
from .audio import FRAME_MS, TELEFON_RATE, frames, pcm16_to_ulaw, stille, ton
from .audiohook import AudioHookSitzung
from .audit import AuditLog
from .dialog import Dialog
from .gateway import Backend, LLMGateway
from .pipeline import Pipeline
from .telemetry import Messung, SimUhr
from .tools import DemoBackend, standard_werkzeuge

HERKUNFT = {
    "endpointing": "algorithmisch, Mock-ASR mit Zwischenergebnissen",
    "asr": f"Mock, fest {MOCK_LATENZ_MS['asr']:.0f} ms",
    "llm_ttft": f"Mock, fest {MOCK_LATENZ_MS['llm_ttft']:.0f} ms",
    "erster_satz": f"Mock, {MOCK_LATENZ_MS['llm_pro_token']:.0f} ms pro Token",
    "tts_erstes_audio": f"Mock, fest {MOCK_LATENZ_MS['tts']:.0f} ms",
    "zeit_bis_erster_ton": "Summe der Stufen in der Simulation",
}


@dataclass
class Zug:
    text: str
    pause_ms: int = 700  # Pause nach Ende der Bot-Ansage
    unterbricht_nach_ms: int | None = None  # redet X ms nach Ansagebeginn hinein
    ms_pro_wort: int = 280
    denkpause_nach_wort: int | None = None  # z. B. beim Diktieren einer Nummer
    denkpause_ms: int = 0


@dataclass
class Bericht:
    antworten: list[str]
    ereignisse: list[tuple[str, int, dict[str, object]]]
    uebergabe: dict[str, Any] | None
    budget: dict[str, dict[str, float | bool]]
    audit_intakt: bool
    audit: list[dict[str, Any]]
    audiohook: list[dict[str, Any]]
    werkzeug_aufrufe: list[str]
    dauer_ms: float

    def als_dict(self) -> dict[str, Any]:
        return {
            "antworten": self.antworten,
            "uebergabe": self.uebergabe,
            "budget": self.budget,
            "audit_intakt": self.audit_intakt,
            "werkzeuge": self.werkzeug_aufrufe,
            "dauer_s": round(self.dauer_ms / 1000, 1),
            "ereignisse": [e[0] for e in self.ereignisse],
        }


def baue_pipeline(
    texte: list[str],
    uhr: SimUhr,
    backends: list[Backend] | None = None,
    audit: AuditLog | None = None,
    *,
    zwischenergebnisse: bool = True,
) -> tuple[Pipeline, DemoBackend, AuditLog]:
    audit = audit or AuditLog(unsigniert_erlaubt=True, uhr=lambda: uhr() / 1000.0)
    if backends is None:
        backends = [
            Backend("lokal", "lokal", RegelLLM(uhr)),
            Backend("cloud-eu", "eu", RegelLLM(uhr), preis_pro_1k_token=0.002),
        ]
    gw = LLMGateway(backends, audit=audit, uhr_ms=uhr)
    backend = DemoBackend()
    dialog = Dialog(gw, standard_werkzeuge(backend, audit), audit, anruf_id="sim-1")
    pipe = Pipeline(
        asr=MockASR(texte, uhr, zwischenergebnisse=zwischenergebnisse),
        tts=MockTTS(uhr),
        dialog=dialog,
        gateway=gw,
        uhr_ms=uhr,
        messung=Messung(uhr),
        uhr_vorstellen=uhr.vorstellen_auf,
    )
    return pipe, backend, audit


def _open_nachricht() -> str:
    return json.dumps(
        {
            "version": "2",
            "type": "open",
            "seq": 1,
            "serverseq": 0,
            "id": "00000000-0000-4000-8000-000000000001",
            "position": "PT0S",
            "parameters": {
                "organizationId": "00000000-0000-4000-8000-0000000000aa",
                "conversationId": "00000000-0000-4000-8000-0000000000bb",
                "language": "de-CH",
                "inputVariables": {"kanal": "hotline"},
                "media": [
                    {"type": "audio", "format": "PCMU", "channels": ["external"], "rate": 8000}
                ],
            },
        }
    )


def anruf(
    zuege: list[Zug],
    backends: list[Backend] | None = None,
    mit_audiohook: bool = True,
    max_ms: int = 120_000,
    *,
    zwischenergebnisse: bool = True,
) -> Bericht:
    """Spielt ``zuege`` ab. ``zwischenergebnisse=False`` simuliert eine ASR ohne
    Zwischenergebnisse wie den HTTP-Adapter im Server: Das Endpointing kennt den
    Text dann nicht und wartet länger."""
    uhr = SimUhr()
    pipe, _backend, audit = baue_pipeline(
        [z.text for z in zuege], uhr, backends, zwischenergebnisse=zwischenergebnisse
    )
    asr = pipe.asr
    if not isinstance(asr, MockASR):  # pragma: no cover - baue_pipeline setzt immer MockASR
        raise TypeError("Simulator braucht MockASR")
    ah = AudioHookSitzung("connector")
    ah_log: list[dict[str, Any]] = []
    t = 0.0

    if mit_audiohook:
        ah_log += ah.text_empfangen(_open_nachricht())

        def barge(tm: float) -> None:
            ah_log.append(ah.barge_in())

        def uebergeben(u: dict[str, Any], tm: float) -> None:
            ah_log.append(
                ah.beenden(
                    "transfer",
                    variablen={
                        "zielQueue": u["ziel"],
                        "anliegen": u["anliegen"],
                        "ticket": u.get("ticket", ""),
                    },
                )
            )

        pipe.bei_barge_in, pipe.bei_uebergabe = barge, uebergeben

    def senden(pcm: bytes) -> None:
        nonlocal t
        for f in frames(pcm, TELEFON_RATE):
            if mit_audiohook:  # über die Leitung: mu-law und zurück
                kanaele = ah.audio_empfangen(pcm16_to_ulaw(f))
                f = kanaele[0] if kanaele else f
            t += FRAME_MS
            pipe.frame(f, t)

    def warten_bis(bedingung: Callable[[], bool], grenze_ms: float) -> None:
        while not bedingung() and t < grenze_ms:
            senden(stille(FRAME_MS, rauschen=30))

    def warten_bis_zeit(ziel_ms: float, grenze_ms: float) -> None:
        while t < ziel_ms and t < grenze_ms:
            senden(stille(FRAME_MS, rauschen=30))

    def warten_bis_turn(index: int, grenze_ms: float) -> None:
        """Warten, bis die ASR die Äusserung ``index`` verarbeitet hat."""
        while asr.index <= index and t < grenze_ms:
            senden(stille(FRAME_MS, rauschen=30))

    for z in zuege:
        if pipe.dialog.beendet:
            break
        if z.unterbricht_nach_ms is not None and pipe.plan:
            laufend = [w.start_ms for w in pipe.plan if w.ende_ms > t]
            start_bot = min(laufend) if laufend else t
            warten_bis_zeit(start_bot + z.unterbricht_nach_ms, t + 20_000)
        else:
            warten_bis_zeit(pipe.plan_ende() + z.pause_ms, t + 20_000)
        for n, _ in enumerate(z.text.split(), start=1):
            asr.sichtbar = n
            senden(
                ton(180 + 20 * (n % 5), z.ms_pro_wort - 40, TELEFON_RATE, 0.3)
                + stille(40, rauschen=30)
            )
            if z.denkpause_nach_wort == n:
                senden(stille(z.denkpause_ms, rauschen=30))
        warten_bis_turn(asr.index, t + 5_000)
        if t > max_ms:
            break
    warten_bis(
        lambda: (
            pipe.uebergabe_ab_ms is not None
            or (t >= pipe.plan_ende() + 1000 and not pipe.uebergabe)
        ),
        t + 60_000,
    )

    tools = [e["daten"]["tool"] for e in audit.eintraege if e["ereignis"] == "tool_ok"]
    return Bericht(
        pipe.antworten,
        pipe.ereignisse,
        pipe.uebergabe,
        pipe.messung.budget_bericht(),
        audit.pruefen() is None,
        audit.eintraege,
        ah_log,
        tools,
        t,
    )


DEMO = [
    Zug("Grüezi, wo ist meine Bestellung?"),
    Zug("Die Nummer ist 447 100.", denkpause_nach_wort=4, denkpause_ms=600),
    Zug("Danke, das war alles. Ich möchte noch eine Reklamation machen, das Paket war beschädigt."),
    Zug("Ja, bitte."),
]


def bericht_text(b: Bericht, ohne: Bericht | None = None) -> str:
    """Textbericht; ``ohne`` ist derselbe Anruf ohne Zwischenergebnisse der ASR."""
    z = ["Simulierter Anruf (SIMULATION, keine Messung)"]
    z += [f"  Bot {i}: {a}" for i, a in enumerate(b.antworten, 1)]
    z.append(f"  Werkzeuge: {b.werkzeug_aufrufe}")
    u = json.dumps(b.uebergabe, ensure_ascii=False) if b.uebergabe else "-"
    z.append(f"  Übergabe:  {u}")
    z.append(f"  Audit-Kette intakt: {b.audit_intakt} ({len(b.audit)} Einträge)")
    z.append("  Latenz in ms auf simulierter Uhr (Ziel = Budgetannahme):")
    for k, v in b.budget.items():
        z.append(
            f"    {k:20} p50 {v['p50']:>6.0f}  p95 {v['p95']:>6.0f}  Ziel {v['ziel_p50']:>5.0f}  "
            f"{HERKUNFT.get(k, '')}"
        )
    if ohne is not None:
        z.append(
            "  Derselbe Anruf ohne Zwischenergebnisse der ASR (wie der HTTP-Adapter im Server):"
        )
        for k in ("endpointing", "zeit_bis_erster_ton"):
            if k in ohne.budget:
                v = ohne.budget[k]
                z.append(
                    f"    {k:20} p50 {v['p50']:>6.0f}  p95 {v['p95']:>6.0f}  "
                    f"Ziel {v['ziel_p50']:>5.0f}"
                )
    z.append("  Echte Werte nur mit Messung auf Zielhardware: gw-contactcenter latenz-messen")
    return "\n".join(z)
