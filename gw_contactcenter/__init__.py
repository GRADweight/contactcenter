"""gw_contactcenter: Sprachagent und Kapazitätsrechner für den KMU-Kundendienst.

Der Kern läuft nur mit der Python-Standardbibliothek und ist offline
testbar. Echte Modelle und Telefonie hängen über Adapter an
(:mod:`gw_contactcenter.adapters`), ohne dass sich die Logik ändert.

Module (in Datenfluss-Reihenfolge):

    audio            G.711 mu-law, Resampling, Pegel, Testsignale
    vad              Sprachaktivität pro 20-ms-Frame
    turn             Turn-Taking: semantisches Endpointing, Barge-in, Backchannel
    audiohook        Genesys AudioHook / Audio Connector (Protokoll)
    signatur         HTTP-Nachrichtensignatur beim Verbindungsaufbau prüfen
    pipeline         Streaming-Kaskade ASR -> Dialog -> LLM -> TTS mit Abbruch
    dialog           Dialogsteuerung: Policy, Werkzeuge, Bestätigung, Übergabe
    policy           Anliegen-Katalog: Datenklasse V0-V4, Risiko R0-R3, Frist
    pii              Personendaten erkennen und pseudonymisieren (Prüfsummen)
    leitplanken      Eingabe- und Antwortprüfer (Schnittstelle, erste Schicht)
    gateway          LLM-Gateway: Datenresidenz, Antwortprüfung, Fallback, Budget
    tools            Werkzeuge mit Schema, Allowlist, Bestätigung, Idempotenz
    audit            HMAC-Hash-Kette (Manipulation erkennen)
    telemetry        Latenz-Spans, Perzentile, Prometheus-Export
    warteschlange    Erlang-B, Erlang-C, Erlang-A
    warteschlange_sim  ereignisdiskrete Gegenprobe zu den Formeln
    kapazitaet       Kapazitätsrechner (Personal, Sprachagent-Kanäle, Speicher)
    sizing           GPU-Dimensionierung aus Architektur und Datenblatt
    latenzmessung    Latenzmessung gegen echte Endpunkte
    sim              Anrufsimulator (Mock-Komponenten)
    server           asyncio-WebSocket-Server (Extra ``server``)
    cli              Kommandozeile ``gw-contactcenter``
"""

__version__ = "2.1.0"
