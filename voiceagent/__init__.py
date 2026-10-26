"""voiceagent: Echtzeit-Sprachagent für den Schweizer KMU-Kundendienst.

Der Kern läuft nur mit der Python-Standardbibliothek (>= 3.9) und ist
vollständig offline testbar. Echte Modelle und Telefonie hängen über
Adapter an (s. ``voiceagent.adapters``), ohne dass sich die Logik ändert.

Module (in Datenfluss-Reihenfolge):

    audio       G.711 mu-law, Resampling, Pegel, Testsignale
    vad         Sprachaktivität pro 20-ms-Frame (Energie, adaptiv)
    turn        Turn-Taking: semantisches Endpointing, Barge-in, Backchannel
    audiohook   Genesys AudioHook / Audio Connector Protokoll (WebSocket)
    pipeline    Streaming-Kaskade ASR -> Dialog -> LLM -> TTS mit Abbruch
    dialog      Dialogsteuerung: Policy, Tools, Bestätigung, Handoff
    policy      Anliegen-Katalog: Datenklasse V0-V4, Risiko R0-R3, SLA
    gateway     LLM-Gateway: Datenresidenz, PII-Tresor, Injection-Schutz,
                Budget, Circuit Breaker, Fallback
    tools       Werkzeuge mit Schema-Validierung, Risiko und Idempotenz
    audit       Manipulationssicheres Protokoll (Hash-Kette)
    telemetry   Latenz-Spans, Perzentile, Prometheus-Export
    sizing      GPU-Dimensionierung aus KV-Cache und Bandbreite
    sim         Anrufsimulator für End-to-End-Tests
    server      asyncio-WebSocket-Server (optional: ``websockets``)
"""

__version__ = "2.0.0"
