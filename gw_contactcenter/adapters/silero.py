"""Silero VAD (optional): neuronale Sprachaktivitätserkennung.

Robuster als die Energie-VAD bei Strassenlärm, Wartemusik oder
Freisprechen. Installation: ``pip install "gradweight-contactcenter[silero]"``;
Modell ``silero_vad.onnx`` aus github.com/snakers4/silero-vad (MIT).

Gleiche Schnittstelle wie :class:`gw_contactcenter.vad.EnergieVAD`.
Silero erwartet 16 kHz und Blöcke zu 512 Samples (32 ms); Telefonaudio wird
deshalb hochgetastet und gepuffert. Nicht Teil der automatischen Tests
(braucht onnxruntime und die Modelldatei).
"""

from __future__ import annotations

from typing import Any

from ..audio import ASR_RATE, TELEFON_RATE, resample_linear


class SileroVAD:  # pragma: no cover - optionale Abhängigkeit
    BLOCK = 512
    nachlauf_frames = 0

    def __init__(self, modell_pfad: str, schwelle: float = 0.5) -> None:
        try:
            import numpy as np
            import onnxruntime as ort
        except ImportError as e:
            raise ImportError("SileroVAD braucht 'onnxruntime' und 'numpy'") from e
        self.np: Any = np
        self.sess: Any = ort.InferenceSession(modell_pfad, providers=["CPUExecutionProvider"])
        self.schwelle = schwelle
        self.state: Any = np.zeros((2, 1, 128), dtype=np.float32)
        self.puffer: Any = np.zeros(0, dtype=np.float32)
        self.letzte = False

    def ist_sprache(self, frame_pcm16: bytes) -> bool:
        np = self.np
        pcm = resample_linear(frame_pcm16, TELEFON_RATE, ASR_RATE)
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        self.puffer = np.concatenate([self.puffer, x])
        while len(self.puffer) >= self.BLOCK:
            block, self.puffer = self.puffer[: self.BLOCK], self.puffer[self.BLOCK :]
            out, self.state = self.sess.run(
                None,
                {
                    "input": block[None, :],
                    "state": self.state,
                    "sr": np.array(ASR_RATE, dtype=np.int64),
                },
            )
            self.letzte = float(out[0][0]) >= self.schwelle
        return bool(self.letzte)
