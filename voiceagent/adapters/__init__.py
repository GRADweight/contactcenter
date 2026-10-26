"""Adapter: austauschbare Anbindungen an echte Modelle und Dienste.

Jede Stufe hat eine kleine Schnittstelle. Die Logik (Turn-Taking,
Dialog, Gateway) kennt nur diese Schnittstellen:

    ASR   transkribieren(pcm16, rate) -> str ; teiltext() -> str
    LLM   complete(messages, max_tokens, json_modus) -> dict
          stream(messages, max_tokens) -> Iterator[str]     (optional)
    TTS   synthetisieren(text) -> pcm16 @ 8 kHz

mock.py           deterministisch, offline, mit simulierter Latenz
openai_compat.py  stdlib-HTTP gegen OpenAI-kompatible Server:
                  vLLM, Ollama, LiteLLM, speaches (faster-whisper,
                  Kokoro, Piper), Azure OpenAI, ...
silero.py         Silero-VAD (optional, braucht onnxruntime)

Ein Full-Duplex-Sprachmodell (Speech-to-Speech wie Moshi, PersonaPlex
oder Block Dessa) ersetzt ASR+LLM+TTS gemeinsam; dafür ist in
``docs/14-dessa-und-full-duplex.md`` beschrieben, wo es andocken würde
und warum Gateway, Tools und Audit trotzdem bleiben.
"""
