"""Sprachaktivitätserkennung (VAD) pro Frame.

VAD beantwortet nur: "Ist in diesen 20 ms Sprache?" Es entscheidet
NICHT, ob jemand fertig gesprochen hat; das ist Turn-Taking (turn.py).

Diese Energie-VAD ist adaptiv: sie lernt den Rauschpegel der Leitung
und erkennt Sprache als deutlichen Abstand darüber. Für Produktion ist
Silero VAD (kleines neuronales Netz, ~1 ms pro Frame auf CPU) robuster
gegen Strassenlärm und Musik; Adapter in ``adapters/silero.py``.
"""
from __future__ import annotations

from dataclasses import dataclass

from .audio import dbfs


@dataclass
class EnergieVAD:
    abstand_db: float = 12.0     # so weit über Rauschboden gilt als Sprache
    min_pegel_db: float = -45.0  # darunter nie Sprache
    nachlauf_frames: int = 3     # 60 ms "Hangover" gegen Abhacken
    anpassung: float = 0.05      # wie schnell der Rauschboden folgt

    def __post_init__(self):
        self.rauschboden = -60.0
        self._nachlauf = 0

    def ist_sprache(self, frame_pcm16: bytes) -> bool:
        pegel = dbfs(frame_pcm16)
        sprache = pegel > max(self.min_pegel_db, self.rauschboden + self.abstand_db)
        if not sprache:
            # Rauschboden nur in Pausen nachführen, nie während Sprache
            self.rauschboden += self.anpassung * (pegel - self.rauschboden)
        if sprache:
            self._nachlauf = self.nachlauf_frames
            return True
        if self._nachlauf > 0:
            self._nachlauf -= 1
            return True
        return False
