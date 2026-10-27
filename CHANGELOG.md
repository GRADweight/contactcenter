# Changelog

Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionen nach
[Semantic Versioning](https://semver.org/lang/de/).

## [2.1.0] - 2026-10-27

### Geändert

- Paket heisst `gw_contactcenter` (Distribution `gradweight-contactcenter`) statt
  `voiceagent`; der alte Name ist auf PyPI vergeben. Kommandozeile `gw-contactcenter`.
- Mindestversion Python 3.11.
- Tests mit pytest und hypothesis, Abdeckung mit Schwelle; mypy strict, ruff.
- Dossiers 00-14 überarbeitet: jede Zahl aus dem Code erzeugt oder belegt
  (`tests/test_doku_konsistenz.py`), Annahmen und Simulationen gekennzeichnet.
- Server startet im Modus `produktion` nur mit API-Key, Client Secret, Audit-Schlüssel,
  Audit-Verzeichnis und echten Modell-Endpunkten.
- Circuit Breaker und Tageskosten gelten prozessweit; das Tageslimit beginnt jeden
  Kalendertag neu.
- GPU-Dimensionierung aus der Modellarchitektur (Quellen im Code), mit KV-Cache-Rechnung,
  Roofline-Obergrenze und Kriterien aus dem Latenzbudget.
- Simulation zeigt den Anruf mit und ohne Zwischenergebnisse der ASR.
- Docker Compose: kein Container als root, Ports an 127.0.0.1, Pflicht-Geheimnisse,
  feste Image-Versionen.

### Hinzugefügt

- Prüfung der HTTP-Nachrichtensignatur (RFC 9421) beim Verbindungsaufbau, mit Testvektoren
  der Genesys-Referenzimplementierung.
- Ereignisdiskrete Simulation einer M/M/n-Warteschlange (`warteschlange_sim.py`) und
  `gw-contactcenter vergleich`; Erlang-A mit Abbrüchen.
- Messwerkzeug für echte Endpunkte (`gw-contactcenter latenz-messen`).
- Prüfer-Schnittstelle (`Pruefer`), `ModellPruefer`, Holdout-Set für die Eingabeprüfer.
- `gw-contactcenter audit-pruefen`.
- Dossiers 15 (Latenz) und 16 (Leitplanken), `ROADMAP.md`, `SECURITY.md`.
- CI mit Lint, Typen, Testmatrix, Evals, Beispielen, Paket, Container und Sicherheit.

### Behoben

- Erlang-C: Die mittlere Wartezeit ist C · h / (n − A); der Faktor h fehlte. Im Beispiel der
  README führte das zu 3 statt 4 Agenten (13.5 s statt 27.0 s mittlere Wartezeit bei 3
  Agenten).
- Erlang-B über eine stabile Rekursion statt Fakultäten.
- AudioHook: Server-Nachrichten ohne `position`, Fehler als `disconnect` statt `error`,
  `paused`/`resumed` als Client-Nachrichten, Werte in `outputVariables` als Strings.
- Audit: HMAC ausserhalb des Entwicklungsmodus Pflicht, Prüfung der Kette beim Öffnen.
- G.711-Kodierer bitgleich mit `audioop.lin2ulaw`.
- Stream-Parser des LLM-Adapters akzeptiert Zeilen mit leerem `choices` (nur `usage`).

### Entfernt

- Unbelegte Zahlen (u. a. eine geschätzte Gesamtlatenz ohne Endpointing und eine
  Gesprächszahl pro GPU ohne Rechnung), interne Arbeitsnotizen, ein Lizenzanhang.

## [2.0.0] - 2026-10-26

### Hinzugefügt

- Lauffähiger Sprachagent für den Genesys Audio Connector: Turn-Taking mit Barge-in,
  Dialog mit Policy, LLM-Gateway, Werkzeuge, Audit, Telemetrie, Anrufsimulator, Evals,
  Compose-Beispiel.

## [1.0.0] - 2026-10-19

### Hinzugefügt

- Dossiers zu KI im Kundendienst und drei Beispielskripte.

