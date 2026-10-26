"""Streaming-Kaskade: Audio rein, Audio raus, mit Unterbrechung.

    Frame -> VAD -> Turn-Taking --(Turn-Ende)--> ASR -> Dialog
          -> Gateway (satzweise) -> TTS -> Wiedergabeplan -> Audio raus
                     ^                                   |
                     +----------(Barge-in: abbrechen)----+

Drei Techniken machen die Kaskade schnell genug fürs Telefon:

1. **Semantisches Endpointing** (turn.py) statt fester 800 ms Stille.
2. **Satz-Streaming**: TTS beginnt mit Satz 1, während das LLM Satz 2
   schreibt. Die Zeit bis zum ersten Ton hängt am ersten Satz, nicht
   an der ganzen Antwort.
3. **Barge-in**: Wiedergabeplan wird ab dem Unterbrechungszeitpunkt
   verworfen, Genesys bekommt ein barge_in-Event, und im Verlauf steht
   nur, was tatsächlich gesprochen wurde.

Die Klasse ist synchron und zeitgesteuert über ``t_ms`` der Frames.
Im Simulator laufen Adapter-Latenzen auf einer SimUhr; im Server ist
die Uhr echt. Dieselbe Logik, zwei Uhren.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from .audio import FRAME_MS, TELEFON_RATE
from .dialog import Antwortplan, Dialog
from .gateway import BudgetErschoepft, KeinBackend, LLMGateway, platzhalter_sprechbar
from .telemetry import Messung
from .turn import BACKCHANNEL, BARGE_IN, TURN_ENDE, VERWORFEN, TurnTaking
from .vad import EnergieVAD

VORLAUF_MS = 200            # Audio vor Sprachbeginn mitschicken (erste Silbe)

NOTFALL_TEXT = ("Entschuldigung, ich habe gerade ein technisches Problem. "
                "Ich verbinde Sie mit unserem Team.")


@dataclass
class Wiedergabe:
    start_ms: float
    ende_ms: float
    text: str


def _dauer_ms(pcm16: bytes, rate: int = TELEFON_RATE) -> float:
    return len(pcm16) / 2 / rate * 1000.0


class Pipeline:
    def __init__(self, *, asr, tts, dialog: Dialog, gateway: LLMGateway,
                 uhr_ms: Callable[[], float], messung: Optional[Messung] = None,
                 vad=None, turn: Optional[TurnTaking] = None,
                 audio_raus: Callable[[bytes, float], None] = lambda pcm, t: None,
                 bei_barge_in: Callable[[float], None] = lambda t: None,
                 bei_uebergabe: Callable[[dict, float], None] = lambda u, t: None):
        self.asr, self.tts, self.dialog, self.gateway = asr, tts, dialog, gateway
        self.uhr_ms = uhr_ms
        self.messung = messung or Messung(uhr_ms)
        self.vad = vad or EnergieVAD()
        self.turn = turn or TurnTaking()
        self.audio_raus, self.bei_barge_in, self.bei_uebergabe = audio_raus, bei_barge_in, bei_uebergabe
        self.plan: List[Wiedergabe] = []
        self.puffer = bytearray()
        self.vorlauf = bytearray()
        self.spricht_kunde = False
        self.ereignisse: List[tuple] = []
        self.uebergabe: Optional[dict] = None
        self.uebergabe_ab_ms: Optional[float] = None
        self.antworten: List[str] = []
        self.turn_ab = 0            # Index im Plan, ab dem der aktuelle Turn beginnt

    # ---------------------------------------------------------- Zustand
    def agent_spricht(self, t_ms: float) -> bool:
        return any(w.start_ms <= t_ms < w.ende_ms for w in self.plan)

    def _sync_uhr(self, t_ms: float) -> None:
        if hasattr(self.uhr_ms, "t") and self.uhr_ms.t < t_ms:   # nur SimUhr
            self.uhr_ms.t = t_ms

    # -------------------------------------------------------------- Kern
    def frame(self, pcm16_8k: bytes, t_ms: float) -> List[str]:
        """Ein 20-ms-Frame der Kundschaft zum Zeitpunkt t_ms (Frame-Ende)."""
        self._sync_uhr(t_ms)
        spricht = self.agent_spricht(t_ms)
        if spricht and not self.turn.agent_spricht:
            self.turn.agent_beginnt(t_ms)
        elif not spricht and self.turn.agent_spricht:
            self.turn.agent_endet(t_ms)
        if self.uebergabe and self.uebergabe_ab_ms is None and not spricht and self.plan_ende() <= t_ms:
            self.uebergabe_ab_ms = t_ms
            self.bei_uebergabe(self.uebergabe, t_ms)

        sprache = self.vad.ist_sprache(pcm16_8k)
        if sprache:
            if not self.spricht_kunde:
                self.puffer = bytearray(self.vorlauf)
            self.spricht_kunde = True
            self.puffer += pcm16_8k
        elif self.spricht_kunde:
            self.puffer += pcm16_8k               # Pausen gehören zur Äusserung
        self.vorlauf += pcm16_8k
        del self.vorlauf[: max(0, len(self.vorlauf) - TELEFON_RATE * 2 * VORLAUF_MS // 1000)]

        typen = []
        for e in self.turn.frame(sprache, int(t_ms), self.asr.teiltext()):
            typen.append(e.typ)
            self.ereignisse.append((e.typ, e.t_ms, e.daten))
            if e.typ == BARGE_IN:
                self._barge_in(t_ms)
            elif e.typ in (BACKCHANNEL, VERWORFEN):
                self.spricht_kunde = False
                if hasattr(self.asr, "verwerfen"):
                    self.asr.verwerfen()
            elif e.typ == TURN_ENDE:
                self.spricht_kunde = False
                if not self.dialog.beendet:
                    self._turn_verarbeiten(e.daten, t_ms)
        return typen

    def plan_ende(self) -> float:
        return max((w.ende_ms for w in self.plan), default=0.0)

    # ------------------------------------------------------- Barge-in
    def _barge_in(self, t_ms: float) -> None:
        frueher, aktuell = self.plan[: self.turn_ab], self.plan[self.turn_ab:]
        gesprochen, behalten = [], []
        for w in aktuell:                       # nur die Antwort dieses Turns
            if w.ende_ms <= t_ms:
                gesprochen.append(w.text)
                behalten.append(w)
            elif w.start_ms < t_ms:
                anteil = (t_ms - w.start_ms) / max(1.0, w.ende_ms - w.start_ms)
                woerter = w.text.split()
                gesprochen.append(" ".join(woerter[: int(len(woerter) * anteil)]))
                behalten.append(Wiedergabe(w.start_ms, t_ms, w.text))
            # Sätze, die noch nicht begonnen haben, fallen weg
        self.plan = frueher + behalten
        self.dialog.kuerzen_auf_gesprochen(" ".join(s for s in gesprochen if s))
        self.messung.erfassen("barge_in", t_ms, t_ms)
        self.bei_barge_in(t_ms)

    # ------------------------------------------------- Turn verarbeiten
    def _turn_verarbeiten(self, d: dict, t_ende: float) -> None:
        # Die VAD meldet nach dem letzten Laut noch einige Frames "Sprache"
        # (Nachlauf). Echte Stille begann früher: ehrlich zurückrechnen.
        nachlauf = getattr(self.vad, "nachlauf_frames", 0) * FRAME_MS
        sprach_ende = d["sprach_ende_ms"] - nachlauf
        self.messung.erfassen("endpointing", sprach_ende, t_ende,
                              satzende_p=d["satzende_p"])
        audio = bytes(self.puffer)
        self.puffer = bytearray()
        self.turn_ab = len(self.plan)
        try:
            self._turn_kern(audio, sprach_ende)
        except Exception as e:      # ASR/TTS-Timeout, Budget, Bug: nie still auflegen
            self._notfall(type(e).__name__)

    def _notfall(self, grund: str) -> None:
        self.dialog.audit.schreiben("notfall_uebergabe", anruf=self.dialog.anruf_id, grund=grund)
        self.uebergabe = self.dialog._uebergabe(f"technisch: {grund}")
        self.dialog.beendet = True
        try:
            pcm = self.tts.synthetisieren(NOTFALL_TEXT)
            start = max(self.uhr_ms(), self.plan_ende())
            self.plan.append(Wiedergabe(start, start + _dauer_ms(pcm), NOTFALL_TEXT))
            self.audio_raus(pcm, start)
        except Exception:
            pass                    # ohne Ansage übergeben ist besser als auflegen

    def _turn_kern(self, audio: bytes, sprach_ende: float) -> None:
        with self.messung.span("asr"):
            text = self.asr.transkribieren(audio, TELEFON_RATE)
        if not text.strip():
            return
        with self.messung.span("dialog"):
            plan = self.dialog.wechsel(text)

        saetze = self._saetze(plan)
        erster = True
        gesamt = []
        for satz in saetze:
            t_tts = self.uhr_ms()
            pcm = self.tts.synthetisieren(platzhalter_sprechbar(satz))
            jetzt = self.uhr_ms()
            if erster:
                self.messung.erfassen("tts_erstes_audio", t_tts, jetzt)
            start = max(jetzt, self.plan_ende())
            ende = start + _dauer_ms(pcm)
            self.plan.append(Wiedergabe(start, ende, satz))
            self.audio_raus(pcm, start)
            if erster:
                self.messung.erfassen("zeit_bis_erster_ton", sprach_ende, start,
                                      anliegen=plan.anliegen, aktion=plan.aktion)
                erster = False
            gesamt.append(satz)
        antwort = " ".join(gesamt)
        self.antworten.append(antwort)
        self.dialog.antwort_merken(antwort)
        if plan.uebergabe:
            self.uebergabe = plan.uebergabe

    def _saetze(self, plan: Antwortplan):
        if plan.text or not plan.llm_messages:
            yield plan.text
            return
        t0 = self.uhr_ms()
        try:
            for s in self.gateway.saetze(plan.llm_messages, klasse=plan.klasse,
                                         anruf_id=self.dialog.anruf_id):
                if s.index == 0:
                    self.messung.erfassen("llm_ttft", t0, t0 + (s.ttft_ms or 0.0),
                                          backend=s.backend, region=s.region)
                    self.messung.erfassen("erster_satz", t0 + (s.ttft_ms or 0.0), self.uhr_ms())
                yield s.text
        except (KeinBackend, BudgetErschoepft) as e:
            self.dialog.audit.schreiben("notfall_uebergabe", anruf=self.dialog.anruf_id,
                                        grund=type(e).__name__)
            self.uebergabe = self.dialog._uebergabe(f"technisch: {type(e).__name__}")
            self.dialog.beendet = True
            yield NOTFALL_TEXT
