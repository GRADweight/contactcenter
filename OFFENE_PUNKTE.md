# Offene Punkte & kritische Fragen  -  contactcenter (Stand 10/2026)

Nicht-exhaustiv. Jeder offene Punkt mit der **Quelle im Repo**, nicht
mit einem Versprechen. Die kritischen Fragen sind **Entscheid-
Checkpunkte**, keine To-dos.

> Dies ist ein **REFERENZWERK**, kein Gutachten. **Kein Rechtsrat,
> keine Kalkulation.** Vor jeder konkreten Entscheidung (Modell,
> Region, Vertrag, Daten, SLA) neu gegen die jeweils aktuelle
> Anbieter- und Rechtslage prüfen  -  Modell- und Plattformnamen
> wandern schneller als diese Doku.

## Offene Punkte

| # | Punkt | Quelle (im Repo) | Status |
|---|---|---|---|
| 1 | **Preise und GPU-Grössen sind Schatzungen** (CHF/Monat, Slots/GPU). Grössenordnungen Stichtag 10/2026, nicht Offerte. GPU-Preise und vLLM-Versionen wandern monatlich. | `beispiele/skalierungsrechner.py` (`GPUS`, `kost_chf_24_7_monat`), `docs/05-skalierung.md` §Kosten | **Offen**  -  vor jedem Angebot neu gegen Exoscale/Infomaniak/vLLM-Releases rechnen |
| 2 | **ASR für Schweizerdeutsch (de-CH Dialekt)** ist der härteste Punkt. faster-whisper/whisper-cpp für de/de-CH brauchen Domänen-Feintuning; kein Tool löst das aus der Dose. | `docs/03-asr-transkribation.md` | **Offen**  -  je Projekt: managed (Deepgram/AssemblyAI) mit CH-Hosting vs On-Prem + Dialekt-Feintuning |
| 3 | **10 zugleich > 200/Tag ist der bindende Faktor.** Die 200 Anrufe/Tag verteilen sich; in der GIPFELSTUNDE (busy-hour 0.25) sind es 5 Anrufe/Stunde, also ~1 Agent reicht für 20 s Wartezeit  -  aber die 10 simultanen Streams bestimmen die vLLM-Slots. Das ist die häufigste Fehleinschätzung. | `beispiele/skalierungsrechner.py` (`szenario`), `docs/05-skalierung.md` | **Offen**  -  pro Kunde die Spitzenlast (nicht das Tagesvolumen) fragen |
| 4 | **DSG / AI-Act / Aufbewahrungsfristen hier nicht beantwortet.** Dieses Repo sagt, WO die Daten physisch liegen und WER sie sieht. Es sagt NICHT, was rechtlich erlaubt ist. | `docs/06-daten-speicherung.md`, alle Dossiers | **Bewusst offen**  -  gehör in `governance` + Rechtsbeistand |
| 5 | **24/7-SLA braucht 2 Köpfe, nicht 1.** Die KMU-Default-Konfiguration ist 8h-Betrieb + 1 KI-Owner (6h/Woche). 24/7 = 2 Personen Pager = grosse Kostenstelle. | `docs/10-betrieb-ops.md`, `beispiele/handoff_router.py` (`SLA`) | **Offen**  -  vor SLA-Versprechen an Kund den Pager-Budget explizit klären |
| 6 | **CRM/Ticket-Anbindung ist pro Projekt unterschiedlich.** HubSpot, Zoho, Salesforce, oder ein einfaches PostgreSQL + Ticket-Warteschlange. Die Weiterleitung-Logik (`handoff_router.py`) ist unabhängig vom CRM; die Anbindung selbst ist ein separater Aufwand. | `beispiele/handoff_router.py`, `docs/07-weiterleitung-handoff.md` | **Offen**  -  pro Projekt: CRM festlegen, Anbindungs-Aufwand kalkulieren |
| 7 | **Modell-Wechsel ist ein Config-Änderung, aber vLLM-Versionen verschieben sich.** vLLM 0.6 → 0.7 → … bricht oft Flags. Die Doku zitiert Stichtag 10/2026. | `docs/04-vllm-inferenz.md`, `beispiele/skalierungsrechner.py` | **Offen**  -  vor jedem Deploy vLLM-Version aktuell prüfen |
| 8 | **Qualitäts-Messung (Antwort-Qualität, nicht nur LOGIK).** Die 4 Samples testen LOGIK (Routing, Skalierung, Pipeline-Ketten) und ROUTING-AUSGABE (Q1-Q4). Für Antwort-QUALITÄT (Halluzinations-Rate, RAG-Treffer-Rate) fehlt ein 20-50-Fall Testset mit Referenz-Antworten. `beispiele/qualitaetstest.py` (11 Fälle) deckt Routing/Pipeline/Sicherheit ab; pro Projekt mit echten Kunden-Daten ergänzen. | `beispiele/qualitaetstest.py`, `docs/00-unterfangen.md` | **Teilweise abge - Q1-Q4 grün (Routing/Skalierung/Sicherheit); Antwort-QUALITÄT (Halluzination/RAG-Treffer) je Projekt ergänzen.** |
| 9 | **Autonomiegrade (Grad 01-05) werden referenziert, nicht neu definiert.** `autonomy` ist der Single-Source. Hier: Verknüpfung der Risiken (R0-R3) zu den Grad-Begrenzungen. | `beispiele/handoff_router.py`, `docs/07-weiterleitung-handoff.md` | **Synchron**  -  bei einer Änderung der Grad-Definition in `autonomy` hier aktualisieren |
| 10 | **TTS/Voice-Reply ist optional** und der am schwierigsten zu betreuende Block (Latenz, Stimmerkennung, Dialekt). Viele KMU-Projekte starten text-basiert und fügen Voice HINZU, nicht umgekehrt. | `docs/01-architektur.md`, `docs/03-asr-transkribation.md` | **Offen**  -  pro Projekt: Text-First-MVP, Voice bei Bedarf |
| 11 | **Kein 24/7-Pager im KMU-Standard.** 24/7-Betrieb mit 2 Köpfen ist eine grosse Entscheidung, die selten die richtige ist für ein KMU-Projekt. Default: 8h/5-Tage + Notfall-Eskalation an KI-Owner. | `docs/10-betrieb-ops.md` | **Offen**  -  vor jedem 24/7-Angebot den Bedarf explizit beweisen |
| 12 | **Keine Kalkulation, nur Grössenordnungen.** Alle CHF-Zahlen sind bewusst grob. Vor jedem Angebot die drei Posten neu rechnen: (1) GPU-Infra, (2) Cloud-Storage/DSG-Aufbewahrung, (3) FTE. | `docs/08-projekt-aufwand-kosten.md`, `beispiele/skalierungsrechner.py` | **Offen**  -  je Angebot |

## Kritische Fragen (Entscheid-Checkpunkte, keine To-dos)

> Diese Fragen sind so gewählt, dass jede Antwort eine nachfolgende
> Entscheidung erst **möglich** macht. Sie sind Reihenfolge-
> unabhängig formuliert, aber haben eine implizite Reihenfolge.

| # | Frage | Warum |
|---|---|---|
| 1 | **Welche Daten sind V3/V4 (vertraulich/streng) für diese Firma, und was ist die Mindest-Aufbewahrungsfrist?** | Ohne diese Antwort ist keine Routing-Entscheidung (`handoff_router.py`) möglich. V4 = Geschäftsleitung, V3 = Leitende, V2 = Fachbereich. |
| 2 | **Wer ist der KI-Owner (Rolle), und wie viele Stunden pro Woche hat er?** | 6h/Woche reicht für 8h-Betrieb. Weniger: das Projekt stirbt. Mehr: es zahlt sich. |
| 3 | **Was ist die Spitzenlast (nicht das Tagesvolumen)? Wieviele Anrufe pro Minute in der GIPFELSTUNDE?** | Erlang-C braucht λ (pro Stunde), nicht das Volumen. 200/Tag ≠ 200/Stunde. |
| 4 | **Welcher vLLM-Parameter (--max-num-seqs, --max-model-len, quant) gibt der Ziel-Latenz (TTFT < 500ms)?** | vLLM-Params sind die einzige echte Konfigurations-Entscheidung. Ein falsches --max-model-len = OOM. |
| 5 | **Kommen Schweizerdeutsch-Dialekt in den Anrufen vor, und wenn ja, mit welcher Häufigkeit?** | de-CH-Dialekt-ASR ist der härteste Punkt. Bei < 5%: managed-ASR oder Dialekt-Erweiterung reicht. Bei > 10%: Domänen-Feintuning nötig. |
| 6 | **Welches CRM, und wer betreut die Ticket-Queue?** | Handoff-Logik ist CRM-unabhängig (`handoff_router.py`), aber die physische Anbindung (API-Key, Webhook, oder n8n-Workflow) ist pro Projekt ein Aufwand. |
| 7 | **Wie geht es weiter nach dem Handoff? An wen? Mit welcher SLA?** | Das ist die eigentliche Entscheidung. Der Wert ist im Handoff, nicht im Chatbot. |
| 9 | **Wie auditierfähig ist der Betrieb  -  wer sieht den Audit-Trail, wie lange?** | Ein Audit-Trail ohne Aufbewahrungsregel ist ein Leichenschrein. `governance` + DSG-Analyse. |
| 10 | **Wer schreibt den 20-50-Fall Quality-Test, und wann wird er nach jedem Modell-Update neu gefahren?** | Ohne Test: Modell-Update → Qualität-Schwindung, niemand merkt es. Mit Test: ein grünes pytest-Signal je Update. |

## Reihenfolge, wenn es eilt

1. Erst die Spitzenlast klären (Frage 3)  -  ohne sie läuft kein
   `skalierungsrechner.py`.
2. Dann die Daten-V0-V4 (Frage 1)  -  ohne sie läuft kein
   `handoff_router.py`.
3. Dann den KI-Owner (Frage 2)  -  ohne ihn stirbt das Projekt nach
   dem dritten Incident.
4. Dann das CRM (Frage 6)  -  ohne es landet das Ticket im
   Nichts.
5. Erst dann vLLM-Params (Frage 4) und Swiss-German (Frage 5)  - 
   das ist die letzte Meile, nicht der Anfang.
