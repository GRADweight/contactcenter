#!/usr/bin/env python3
"""Transkript-Pipeline: Ton - Text - Zusammenfassung - Tickets (nur stdlib).

Idee: «LLM-Transkribation» ist in der Praxis fast nie ein einzelner
Schritt. Ein Anruf durchläuft eine KETTE, und jeder Schritt hat seine
eigene Fehlerquelle:

  1. ASR        Ton         -> rohes Transkript        (Sprache - Text)
  2. Diariz.    Wer war wann am Wort?                   (Sprecher-Trennung)
  3. Post-Proz. Normalisierung + PII-Masking            (sauberes Transkript)
  4. LLM-Summ.  Transkript  -> Kernpunkte + Absicht    (Text - Struktur)
  5. Auszug     Absicht + Handoff-Ziel + Priorität     (Ticket/Freigabe)

Die ersten Schritte (ASR/Diarisierung) brauchen echte Modelle und echten
Ton (Whisper / faster-whisper). Für den Offline-Selbsttest liefere ich
einen MOCK-ASR, der deterministisches Transkript zurückgibt, und einen
MOCK-LLM, der aus dem Transkript Struktur zieht. So läuft `selftest`
ohne API-Keys und ohne Ton-Datei.

Der echte Adapter (ASR / LLM) ist bewusst ein offenes Interface: man
schreibt einen `WhisperAdapter` (faster-whisper / whisper.cpp) und einen
`OpenAICompatAdapter` (OpenAI, Ollama, vLLM - alle sprechen
/v1/chat/completions). Man tauscht NICHT die Pipeline-Logik, NUR den
Adapter.

Nutzung:
  python beispiele/transkript_pipeline.py selftest          # ohne Netz/Keys/Ton
  python beispiele/transkript_pipeline.py demo              # mock-Ton
  python beispiele/transkript_pipeline.py --help
"""
from __future__ import annotations

import json
import re
import sys
import types
from dataclasses import dataclass, field


# --------------------------------------------------------------------------
# 1. Datenmodell
# --------------------------------------------------------------------------

@dataclass
class TranskriptSegment:
    sprecher: str         # "agent" | "kundschaft" (Diarisierungs-Ergebnis)
    text: str
    start_s: float = 0.0
    end_s: float = 0.0

    def wort_anzahl(self) -> int:
        return len(re.findall(r"\w+", self.text, re.UNICODE))


@dataclass
class Transkript:
    segmente: list[TranskriptSegment]
    dauer_s: float = 0.0
    sprache: str = "de-CH"
    verwerfungsrate: float = 0.0       # WER (Word Error Rate) geschätzt
    vertraulichkeitsstufe: int = 1     # 0 = intern, 1 = normal

    def text_block(self) -> str:
        """Kontextblock für den LLM in «Sprecher: Text»-Form."""
        return "\n".join(f"{s.sprecher}: {s.text}" for s in self.segmente)

    def sprecher_anteile(self) -> dict:
        counts = {}
        for s in self.segmente:
            counts[s.sprecher] = counts.get(s.sprecher, 0) + s.wort_anzahl()
        return counts


@dataclass
class Zusammenfassung:
    absicht: str              # z. B. "Bestellungsstatus"
    kernaussagen: list[str] = field(default_factory=list)
    handoff_target: str = "kein"   # "technik" | "verkauf" | "kein"
    prioritaet: int = 1          # 1 = niedrig, 3 = hoch
    offen_punkte: list[str] = field(default_factory=list)
    quell_referenzen: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# 2. ASR-Adapter-Interface
# --------------------------------------------------------------------------

class ASRAdapter:
    name = "base"
    def transkribieren(self, audio_quelle: str, sample_rate: int = 16000) -> Transkript:
        raise NotImplementedError


class MockASR(ASRAdapter):
    """Deterministisches Test-Transkript. Kein Ton, kein Netz."""
    name = "mock"

    _FAKE = [
        ("kundschaft",
         "Grüezi, ich hätte eine Frage zu meiner letzten Bestellung, Nummer 4471.",
         0.0, 3.2),
        ("agent",
         "Selbverständlich, Moment, das hole ich gleich her.", 3.4, 5.0),
        ("agent",
         "Ihre Bestellung 4471 ist am Dienstag versendet worden, "
         "Zustellung wird heute erwartet. Ist noch etwas?", 5.2, 9.8),
        ("kundschaft",
         "Ja, leider nicht angekommen. Was machen wir weiter?", 10.0, 12.5),
        ("agent",
         "Ich reiche das als Reklamation an das Logistik-Team ein, "
         "Sie erhalten eine Rückmeldung per E-Mail in 24 Stunden.",
         12.7, 17.0),
    ]
    _WER = 0.045

    def transkribieren(self, audio_quelle, sample_rate=16000) -> Transkript:
        segs = [TranskriptSegment(s, t, st, end) for (s, t, st, end) in self._FAKE]
        return Transkript(
            segmente=segs,
            dauer_s=segs[-1].end_s if segs else 0.0,
            verwerfungsrate=self._WER,
        )


class WhisperAdapter(ASRAdapter):
    """Stub für fast-Whisper (faster-whisper / whisper.cpp). Im echten
    Einsatz: `from faster_whisper import WhisperModel`; Ton laden,
    `model.transcribe(..., vad_filter=True, word_timestamps=True)`,
    Segmente zu TranskriptSegment mappen, Sprecher via pyannote.

    NICHT Teil des Selftests (braucht Ton-Datei + CPU/GPU).
    Die Struktur ist bewusst dokumentiert, damit man sie ohne
    die Pipeline neu zu schreiben übernehmen kann.
    """
    name = "faster-whisper"

    def transkribieren(self, audio_quelle, sample_rate=16000) -> Transkript:
        raise RuntimeError(
            "WhisperAdapter ist ein Stub - für echten Ton bitte "
            "faster-whisper / whisper.cpp einbauen (s. doku/03-asr.md). "
            "selftest nutzt den MockASR."
        )


# --------------------------------------------------------------------------
# 3. Diarization (Sprecher-Trennung)
# --------------------------------------------------------------------------

def diarisieren_simple(rohe_segmente: list[dict]) -> list[TranskriptSegment]:
    """Einfache Heuristik: abwechselnd agent/kundschaft.
    In der Praxis: pyannote.audio (Sprecher-Embedding-basierend)."""
    out: list[TranskriptSegment] = []
    prev = None
    for i, seg in enumerate(rohe_segmente):
        if i == 0:
            sprecher = "kundschaft"
        else:
            sprecher = "agent" if prev == "kundschaft" else "kundschaft"
        out.append(TranskriptSegment(
            sprecher=sprecher,
            text=seg["text"],
            start_s=seg.get("start_s", 0.0),
            end_s=seg.get("end_s", 0.0),
        ))
        prev = sprecher
    return out


# --------------------------------------------------------------------------
# 4. PII-Masking
# --------------------------------------------------------------------------

# Muster (reguläre Ausdrücke, Schweizer Hochdeutsch).
# Achtung: GROB-Erkennung, kein Rechts-Tool. Für Produktion:
# Presidio + NER-Modell, nicht nur Regex.
_PI_MASK = [
    (re.compile(r"\bKonto\s*[0-9]{5,}", re.IGNORECASE), "KONTO"),
    (re.compile(r"[0-9]{4}\s*[0-9]{4}\s*[0-9]{4}\s*[0-9]{4}"), "KARTE"),
    (re.compile(r"\b\d{6}\b"), "IDENT"),
]

# Bestellnummern (6-stellig) bleiben sichtbar - nicht PII.
_ORDER_NUM = re.compile(r"\b[0-9]{6}\b")  # nur zur Dokumentation; nicht maskiert


def mask_piis(text: str) -> str:
    for muster, replacement in _PI_MASK:
        text = muster.sub(f"[{replacement}]", text)
    return text


# --------------------------------------------------------------------------
# 5. LLM-Zusammenfasser (Adapter-Interface)
# --------------------------------------------------------------------------

class LLMAdapter:
    name = "base"
    def zusammenfassen(self, transcript_text: str, system: str = "") -> dict:
        raise NotImplementedError


class MockLLM(LLMAdapter):
    """Deterministischer Zusammenfasser. Erkennt Absicht anhand von
    Schlüsselwörtern. Der echte LLM-Pfad (OpenAI, Ollama, vLLM)
    liefert das gleiche Datenmodell zurück."""
    name = "mock"

    _ABSICHTEN = [
        ("Bestellungsstatus", ["Bestellung", "nicht angekommen",
                               "Zustellung"]),
        ("Produkt-Anfrage",   ["Frage", "Produkt", "Preis"]),
        ("Reklamation",       ["Reklamation", "beschädigt"]),
        ("Konto-Stellung",    ["Konto", "Guthaben"]),
    ]
    _PRIPAET_WORTER = {
        "leider": 3, "nicht angekommen": 3, "beschädigt": 3,
        "Reklamation": 2,
    }

    def zusammenfassen(self, transcript_text: str, system="") -> dict:
        low = transcript_text.lower()
        absicht = "Allgemeine Beratung"
        for name, keywords in self._ABSICHTEN:
            if any(k.lower() in low for k in keywords):
                absicht = name
                break
        # Priorität aus Schlüsselwörtern ableiten
        prioritaet = 1
        for wort, p in self._PRIPAET_WORTER.items():
            if wort.lower() in low:
                prioritaet = max(prioritaet, p)
        # Handoff-Ziel: bei «nicht angekommen» / «Reklamation» -> Logistik
        handoff = "kein"
        if absicht == "Reklamation" or "nicht" in low:
            handoff = "logistik"
        # Kernpunkte aus den ersten 3 Sätzen (einfach)
        saetze = [s.strip() for s in re.split(r"[.!?]", transcript_text) if s.strip()]
        kernaussagen = saetze[:3]
        # Offene Punkte: ungelöste Fragen
        offen = [s for s in saetze if "?" in s or "Was machen" in s]
        return {
            "absicht": absicht,
            "kernaussagen": kernaussagen,
            "handoff_target": handoff,
            "prioritaet": prioritaet,
            "offen_punkte": offen,
            "quell_referenzen": ["transkript_mock"],
        }


class OpenAICompatLLM(LLMAdapter):
    """Stub für OpenAI-kompatiblen Endpunkt (OpenAI, Ollama, vLLM).
    Man tauscht NUR die URL und den Key. Man tauscht NIE die Pipeline.
    Im echten Einsatz: HTTP-POST `base_url/v1/chat/completions` mit
    dem Transkript im Prompt, JSON-Antwort aus dem `content`-Feld.
    """
    name = "openai-compat-stub"

    def __init__(self, base_url: str, api_key: str = "EMPTY",
                 model: str = "qwen3:27b"):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model

    def zusammenfassen(self, transcript_text: str, system="") -> dict:
        raise NotImplementedError(
            "OpenAICompatLLM ist ein Stub - für echte LLM-Aufrufe "
            "bitte HTTP-POST gegen v1/chat/completions einbauen "
            "(OpenAI, Ollama, vLLM - alle identisch). "
            "selftest nutzt den MockLLM."
        )


# --------------------------------------------------------------------------
# 6. Pipeline
# --------------------------------------------------------------------------

class Pipeline:
    """Die eigentliche Kette: 1 - 5 (s. Docstring oben)."""
    def __init__(self, asr: ASRAdapter, llm: LLMAdapter = None):
        self.asr = asr
        self.llm = llm or MockLLM()

    def laufen(self, audio_quelle: str) -> dict:
        # 1. ASR
        transkript = self.asr.transkribieren(audio_quelle)
        # 2. Diariz. (im Mock-ASR schon geschehen, im echten ASR hier)
        # segmente = diarisieren_simple(...)
        # 3. PII-Masking
        for seg in transkript.segmente:
            seg.text = mask_piis(seg.text)
        # 4. LLM-Zusammenfassung
        block = transkript.text_block()
        summary_raw = self.llm.zusammenfassen(
            block,
            system=("Zusammenfassen Sie das Telefonat in die Kernpunkte "
                    "und die Absicht."),
        )
        summary = Zusammenfassung(
            absicht=summary_raw["absicht"],
            kernaussagen=summary_raw.get("kernaussagen", []),
            handoff_target=summary_raw.get("handoff_target", "kein"),
            prioritaet=summary_raw.get("prioritaet", 1),
            offen_punkte=summary_raw.get("offen_punkte", []),
            quell_referenzen=summary_raw.get("quell_referenzen", []),
        )
        # 5. Auszug
        return {
            "transkript": transkript,
            "zusammenfassung": summary,
            "stats": {
                "dauer_s": transkript.dauer_s,
                "segmente": len(transkript.segmente),
                "wer": round(transkript.verwerfungsrate, 3),
                "sprecher_anteile": transkript.sprecher_anteile(),
            },
        }


# --------------------------------------------------------------------------
# 7. Selbsttest
# --------------------------------------------------------------------------

def selftest() -> int:
    print("== selftest: transkript_pipeline ==")
    pipe = Pipeline(MockASR())
    res = pipe.laufen("mock_audio.wav")
    t = res["transkript"]
    s = res["zusammenfassung"]

    # 5 Segmente
    assert len(t.segmente) == 5, t
    # WER im akzeptablen Bereich
    assert 0.0 < t.verwerfungsrate < 0.5, t
    print(f"  Dauer {t.dauer_s}s, {len(t.segmente)} Segmente, "
          f"WER {t.verwerfungsrate}")

    # Diarisierung
    anteile = t.sprecher_anteile()
    assert "agent" in anteile and "kundschaft" in anteile
    print(f"  Diariz.: {anteile}")

    # Absicht erkannt
    assert s.absicht == "Bestellungsstatus", s
    print(f"  Absicht: {s.absicht}")

    # Priorität hoch (leider + nicht angekommen)
    assert 1 <= s.prioritaet <= 3, s
    assert s.prioritaet == 3, f"expected 3, got {s.prioritaet}"
    print(f"  Priorität: {s.prioritaet}")

    # Handoff
    assert s.handoff_target in ("logistik", "kein"), s
    assert s.handoff_target == "logistik", s
    print(f"  Handoff: {s.handoff_target}")

    # Offene Punkte
    assert any("Warum weiter" in o or "Was machen" in o for o in s.offen_punkte), s
    print(f"  Offene: {s.offen_punkte}")

    # PII-Masking: Kontonummer würde maskiert werden
    raw = "Bitte, ich möchte das Konto 12345678901234 auflösen."
    gemaskt = mask_piis(raw)
    assert "KONTO" in gemaskt, gemaskt
    assert "1234567" not in gemaskt, gemaskt
    print(f"  PII-Masking: {gemaskt}")

    # Pipeline lauft deterministisch (2x)
    pipe2 = Pipeline(MockASR())
    res2 = pipe2.laufen("mock_audio.wav")
    assert res2["zusammenfassung"].absicht == res["zusammenfassung"].absicht
    print("  Determinismus: 2x dieselbe Absicht (ok)")

    # JSON-Serbarisierbar
    json.dumps({
        "absicht": s.absicht,
        "kernaussagen": s.kernaussagen,
        "handoff": s.handoff_target,
        "prioritaet": s.prioritaet,
    })
    print("  JSON-Serbar: ok")

    print("== selftest PASSED ==")
    return 0


def demo() -> int:
    pipe = Pipeline(MockASR())
    res = pipe.laufen("MOCK_TON.wav")
    print(json.dumps(res, ensure_ascii=False, indent=2,
                      default=lambda o: asdict(o) if hasattr(o, "__dataclass_fields__") else o))
    return 0


# asdict import for demo
from dataclasses import asdict  # noqa: E402

if __name__ == "__main__":
    if "selftest" in sys.argv:
        sys.exit(selftest())
    if "demo" in sys.argv:
        sys.exit(demo())
    print(__doc__)
    sys.exit(0)
