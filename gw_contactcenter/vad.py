"""Sprachaktivitätserkennung (VAD) pro Frame.

VAD beantwortet nur: «Ist in diesen 20 ms Sprache?» Es entscheidet nicht,
ob jemand fertig gesprochen hat; das ist Turn-Taking (:mod:`.turn`).

Diese Energie-VAD ist adaptiv: Sie lernt den Rauschpegel der Leitung und
erkennt Sprache als deutlichen Abstand darüber. Gegen Strassenlärm und
Wartemusik ist ein neuronales Modell (Silero VAD, ``adapters/silero.py``)
robuster; die Schnittstelle ist dieselbe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .audio import dbfs


class VAD(Protocol):
    """Schnittstelle: ein PCM16-Frame rein, Sprache ja/nein raus."""

    nachlauf_frames: int

    def ist_sprache(self, frame_pcm16: bytes) -> bool: ...


@dataclass
class EnergieVAD:
    abstand_db: float = 12.0  # so weit über dem Rauschboden gilt als Sprache
    min_pegel_db: float = -45.0  # darunter nie Sprache
    nachlauf_frames: int = 3  # 60 ms «Hangover» gegen Abhacken
    anpassung: float = 0.05  # wie schnell der Rauschboden folgt
    rauschboden: float = field(default=-60.0, init=False)
    _nachlauf: int = field(default=0, init=False, repr=False)

    def ist_sprache(self, frame_pcm16: bytes) -> bool:
        pegel = dbfs(frame_pcm16)
        if pegel > max(self.min_pegel_db, self.rauschboden + self.abstand_db):
            self._nachlauf = self.nachlauf_frames
            return True
        # Rauschboden nur in Pausen nachführen, nie während Sprache.
        self.rauschboden += self.anpassung * (pegel - self.rauschboden)
        if self._nachlauf > 0:
            self._nachlauf -= 1
            return True
        return False
