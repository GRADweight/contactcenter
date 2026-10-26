"""Anrufsimulator: ganze Telefonate als End-to-End-Test, ohne Hardware.

Ein Skript aus Kundenzügen wird zu echtem 8-kHz-Audio (Testtöne als
"Sprache", leises Rauschen als Stille) und läuft Frame für Frame durch
die komplette Pipeline: VAD, Turn-Taking, ASR, Dialog, Gateway, TTS,
Barge-in, Übergabe, Audit, Latenzmessung.

Optional läuft das Audio zusätzlich durch das Genesys-AudioHook-
Protokoll (mu-law kodieren, open/opened, disconnect), damit auch die
Telefonie-Schnittstelle im selben Test steckt.

    python -m voiceagent.sim            # Demo-Anruf mit Bericht
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import List, Optional

from .adapters.mock import MockASR, MockTTS, RegelLLM
from .audio import FRAME_MS, TELEFON_RATE, frames, stille, ton
from .audiohook import AudioHookSitzung
from .audit import AuditLog
from .dialog import Dialog
from .gateway import Backend, LLMGateway
from .pipeline import Pipeline
from .telemetry import Messung, SimUhr
from .tools import DemoBackend, standard_werkzeuge


@dataclass
class Zug:
    text: str
    pause_ms: int = 700                     # Pause nach Ende der Bot-Ansage
    unterbricht_nach_ms: Optional[int] = None   # redet X ms nach Ansagebeginn rein
    ms_pro_wort: int = 280
    denkpause_nach_wort: Optional[int] = None   # z. B. beim Nummer-Diktieren
    denkpause_ms: int = 0


@dataclass
class Bericht:
    antworten: List[str]
    ereignisse: List[tuple]
    uebergabe: Optional[dict]
    budget: dict
    audit_intakt: bool
    audit: List[dict]
    audiohook: List[dict]
    werkzeug_aufrufe: List[str]
    dauer_ms: float

    def als_dict(self) -> dict:
        return {"antworten": self.antworten, "uebergabe": self.uebergabe,
                "budget": self.budget, "audit_intakt": self.audit_intakt,
                "werkzeuge": self.werkzeug_aufrufe, "dauer_s": round(self.dauer_ms / 1000, 1),
                "ereignisse": [e[0] for e in self.ereignisse]}


def baue_pipeline(texte: List[str], uhr: SimUhr, backends: Optional[List[Backend]] = None,
                  audit: Optional[AuditLog] = None):
    audit = audit or AuditLog(uhr=lambda: uhr() / 1000.0)
    if backends is None:
        backends = [
            Backend("lokal-qwen3-8b", "lokal", RegelLLM(uhr), chf_pro_1k_token=0.0),
            Backend("cloud-eu", "eu", RegelLLM(uhr), chf_pro_1k_token=0.002),
        ]
    gw = LLMGateway(backends, audit=audit, uhr_ms=uhr)
    backend = DemoBackend()
    dialog = Dialog(gw, standard_werkzeuge(backend, audit), audit, anruf_id="sim-1")
    asr = MockASR(texte, uhr)
    return Pipeline(asr=asr, tts=MockTTS(uhr), dialog=dialog, gateway=gw,
                    uhr_ms=uhr, messung=Messung(uhr)), backend, audit


def anruf(zuege: List[Zug], backends: Optional[List[Backend]] = None,
          mit_audiohook: bool = True, max_ms: int = 120_000) -> Bericht:
    uhr = SimUhr()
    pipe, backend, audit = baue_pipeline([z.text for z in zuege], uhr, backends)
    ah = AudioHookSitzung("connector")
    ah_log: List[dict] = []
    t = 0.0

    if mit_audiohook:
        ah_log += ah.text_empfangen(json.dumps({
            "version": "2", "type": "open", "seq": 1, "id": "sim-sitzung",
            "position": "PT0S", "parameters": {
                "organizationId": "org", "conversationId": "conv-sim",
                "language": "de-CH", "inputVariables": {"kanal": "hotline"},
                "media": [{"type": "audio", "format": "PCMU",
                           "channels": ["external"], "rate": 8000}]}}))
        pipe.bei_barge_in = lambda tm: ah_log.append(ah.barge_in())
        pipe.bei_uebergabe = lambda u, tm: ah_log.append(ah.beenden(
            "transfer", variablen={"zielQueue": u["ziel"], "anliegen": u["anliegen"],
                                   "ticket": u.get("ticket", "")}))

    def senden(pcm: bytes):
        nonlocal t
        for f in frames(pcm, TELEFON_RATE):
            if mit_audiohook:            # über die Leitung: mu-law und zurück
                f = ah.audio_empfangen(ah_audio_kodieren(f))[0]
            t += FRAME_MS
            pipe.frame(f, t)

    def warten_bis(bedingung, grenze_ms: float):
        nonlocal t
        while not bedingung() and t < grenze_ms:
            senden(stille(FRAME_MS, rauschen=30))

    for i, z in enumerate(zuege):
        if pipe.dialog.beendet:
            break
        if z.unterbricht_nach_ms is not None and pipe.plan:
            laufend = [w.start_ms for w in pipe.plan if w.ende_ms > t]
            start_bot = min(laufend) if laufend else t
            warten_bis(lambda: t >= start_bot + z.unterbricht_nach_ms, t + 20_000)
        else:
            warten_bis(lambda: t >= pipe.plan_ende() + z.pause_ms, t + 20_000)
        woerter = z.text.split()
        for n, _ in enumerate(woerter, start=1):
            pipe.asr.sichtbar = n
            senden(ton(180 + 20 * (n % 5), z.ms_pro_wort - 40, TELEFON_RATE, 0.3) + stille(40, rauschen=30))
            if z.denkpause_nach_wort == n:
                senden(stille(z.denkpause_ms, rauschen=30))
        # warten, bis der Turn verarbeitet ist
        idx = pipe.asr.index
        warten_bis(lambda: pipe.asr.index > idx, t + 5_000)
        if t > max_ms:
            break
    warten_bis(lambda: pipe.uebergabe_ab_ms is not None or
               (t >= pipe.plan_ende() + 1000 and not pipe.uebergabe), t + 60_000)

    tools = [e["daten"]["tool"] for e in audit.eintraege if e["ereignis"] == "tool_ok"]
    return Bericht(pipe.antworten, pipe.ereignisse, pipe.uebergabe,
                   pipe.messung.budget_bericht(), audit.pruefen() is None,
                   audit.eintraege, ah_log, tools, t)


def ah_audio_kodieren(pcm16: bytes) -> bytes:
    from .audio import pcm16_to_ulaw
    return pcm16_to_ulaw(pcm16)


DEMO = [
    Zug("Grüezi, wo ist meine Bestellung?"),
    Zug("Die Nummer ist 447 100.", denkpause_nach_wort=4, denkpause_ms=600),
    Zug("Danke, das war alles. Ich möchte noch eine Reklamation machen, das Paket war beschädigt."),
    Zug("Ja, bitte."),
]


def main() -> int:
    b = anruf(DEMO)
    print("== Simulierter Anruf ==")
    for i, a in enumerate(b.antworten, 1):
        print(f"  Bot {i}: {a}")
    print(f"  Werkzeuge: {b.werkzeug_aufrufe}")
    print(f"  Übergabe:  {json.dumps(b.uebergabe, ensure_ascii=False) if b.uebergabe else '-'}")
    print(f"  Audit-Kette intakt: {b.audit_intakt} ({len(b.audit)} Einträge)")
    print("  Latenz (ms):")
    for k, v in b.budget.items():
        print(f"    {k:22} p50 {v['p50']:>7} p95 {v['p95']:>7}  Ziel {v['ziel_p50']:>5}  "
              f"{'ok' if v['ok'] else 'ÜBER BUDGET'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
