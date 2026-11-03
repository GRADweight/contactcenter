"""Adapter: austauschbare Anbindungen an Modelle und Dienste.

Jede Stufe hat eine kleine Schnittstelle; Turn-Taking, Dialog und Gateway
kennen nur diese:

    ASR   transkribieren(pcm16, rate) -> str ; teiltext() -> str
    LLM   complete(messages, max_tokens, json_modus) -> LLMErgebnis
          stream(messages, max_tokens) -> Iterator[str]   (optional)
    TTS   synthetisieren(text) -> PCM16 mit 8 kHz

mock.py           deterministisch, offline, mit fest eingestellter Verzögerung
openai_compat.py  OpenAI-kompatible HTTP-APIs (vLLM, Ollama, LiteLLM, speaches, ...)
silero.py         Silero VAD (optional, braucht onnxruntime)

Ein Full-Duplex-Sprachmodell (Speech-to-Speech) würde ASR, LLM und TTS
gemeinsam ersetzen; wo es andocken würde und warum Gateway, Werkzeuge und
Audit trotzdem bleiben, steht in ``docs/14-dessa-und-full-duplex.md``.
"""
