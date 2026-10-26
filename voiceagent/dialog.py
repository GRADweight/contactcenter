"""Dialogsteuerung: ein Gesprächswechsel von Text zu Antwortplan.

Das Modell entscheidet nicht allein. Die Reihenfolge pro Wechsel:

1. Offene Rückfrage auflösen (Ja/Nein zu einer Aktion)
2. "Ich will einen Menschen" -> sofort übergeben (immer erlaubt)
3. Prompt-Injection prüfen
4. PII pseudonymisieren (PII hebt die Datenklasse auf mind. V2)
5. Anliegen klassifizieren: immer auf einem LOKALEN Modell (V2),
   weil vor der Klassifikation niemand weiss, wie heikel der Inhalt ist
6. Policy anwenden: selbst lösen / mit Bestätigung / aufnehmen und
   übergeben / sofort übergeben
7. Antwort: Vorlage (deterministisch) oder LLM mit Kontext aus
   Werkzeugen (gestreamt, satzweise geprüft, s. gateway.saetze)

Ergebnis ist ein ``Antwortplan``. Die Pipeline spricht ihn aus.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

from .audit import AuditLog
from .gateway import (SYSTEM_GRUNDREGELN, BudgetErschoepft, KeinBackend, LLMGateway,
                      PIITresor, injection_pruefen, spotlight)
from .policy import (AUFNEHMEN_UND_UEBERGEBEN, MIT_BESTAETIGUNG, SOFORT_UEBERGEBEN,
                     Anliegen, anliegen, klassifiziere_regelbasiert)
from .tools import Werkzeugkasten

_JA = re.compile(r"\b(ja|jawohl|gerne|genau|richtig|korrekt|okay|ok|bitte|mach das|einverstanden)\b", re.I)
_NEIN = re.compile(r"\b(nein|nicht|lieber nicht|stopp|abbrechen|falsch)\b", re.I)
_MENSCH = re.compile(r"\b(mensch\w*|mitarbeit\w*|person|berater\w*|agent\w*|jemand)\b.*\b(sprechen|reden|verbinden|haben)\b"
                     r"|\b(verbinden sie mich|echten menschen)\b", re.I)
_BESTELLNR = re.compile(r"\b\d{6}\b")

_ZIFFERNGRUPPEN = re.compile(r"\b\d{2,4}(?: \d{2,4})+\b")


def ziffern_zusammenziehen(text: str) -> str:
    """ASR schreibt diktierte Nummern oft in Gruppen ("447 100").
    Gruppen mit zusammen 6-8 Ziffern werden zu einer Nummer verbunden;
    längere Folgen (IBAN, Karte) bleiben für den PII-Tresor unverändert."""
    def verbinden(m):
        z = m.group(0).replace(" ", "")
        return z if 6 <= len(z) <= 8 else m.group(0)
    return _ZIFFERNGRUPPEN.sub(verbinden, text)


KLASSIFIKATION_PROMPT = (
    "ANLIEGEN-KLASSIFIKATION. Ordne die Kundenaussage genau einem Anliegen zu: "
    "allgemeine-frage, preis-anfrage, bestellung-ankommen, reklamation, konto-stellung, "
    "vertrag-aenderung, datenschutz-verstoss, unbekannt. Antworte nur mit JSON: "
    '{"anliegen": "...", "sicherheit": 0.0-1.0, "bestellnummer": "123456" oder null}.'
)


@dataclass
class Antwortplan:
    text: str = ""                         # fester Text (Vorlage)
    llm_messages: Optional[List[dict]] = None
    klasse: int = 2
    uebergabe: Optional[dict] = None       # gesetzt = Gespräch geht an Menschen
    anliegen: str = ""
    aktion: str = ""                       # für Audit und Evals
    werkzeuge: List[str] = field(default_factory=list)


class Dialog:
    def __init__(self, gateway: LLMGateway, werkzeuge: Werkzeugkasten,
                 audit: Optional[AuditLog] = None, anruf_id: str = "anruf-1"):
        self.gateway, self.werkzeuge = gateway, werkzeuge
        self.audit = audit or gateway.audit
        self.anruf_id = anruf_id
        self.tresor = PIITresor()
        self.verlauf: List[dict] = []          # pseudonymisiert
        self.anliegen: Optional[Anliegen] = None
        self.offen: Optional[dict] = None      # wartende Ja/Nein-Rückfrage
        self.bestellnummer: Optional[str] = None
        self.injektionen = 0
        self.unklar = 0
        self.min_klasse = 1
        self.beendet = False

    # ------------------------------------------------------------ Hilfen
    def _plan(self, **kw) -> Antwortplan:
        p = Antwortplan(**kw)
        if not p.anliegen and self.anliegen:
            p.anliegen = self.anliegen.id
        self.audit.schreiben("dialog", anruf=self.anruf_id, anliegen=p.anliegen,
                             aktion=p.aktion, klasse=p.klasse,
                             uebergabe=bool(p.uebergabe), werkzeuge=p.werkzeuge)
        if p.uebergabe:
            self.beendet = True
        return p

    def _uebergabe(self, grund: str, ticket: str = "") -> dict:
        a = self.anliegen
        return {
            "ziel": a.bereich if a else "Kundendienst (1. Level)",
            "anliegen": a.id if a else "unbekannt",
            "sla_tage": a.sla_tage if a else 2.0,
            "grund": grund,
            "ticket": ticket,
            # Kontext für den Menschen: pseudonymisierter Verlauf, keine Roh-PII
            "zusammenfassung": " | ".join(m["content"] for m in self.verlauf[-6:]
                                          if m["role"] == "user")[:500],
        }

    def kuerzen_auf_gesprochen(self, gesprochen: str) -> None:
        """Nach Barge-in: Im Verlauf steht nur, was die Kundschaft gehört hat."""
        for m in reversed(self.verlauf):
            if m["role"] == "assistant":
                m["content"] = gesprochen + " [unterbrochen]"
                break

    def _klassifizieren(self, text_p: str) -> tuple:
        """Modell UND Regeln; das riskantere Ergebnis gewinnt.

        So kann Kundentext das Modell nicht auf ein harmloses Anliegen
        "überreden" (z. B. V1/R0 -> Cloud, keine Übergabe), wenn die
        Stichworte etwas Heikleres zeigen.
        """
        r_id, r_p = klassifiziere_regelbasiert(text_p)
        nr_m = _BESTELLNR.search(text_p)
        nr = nr_m.group(0) if nr_m else None
        msgs = [{"role": "system", "content": KLASSIFIKATION_PROMPT},
                {"role": "user", "content": spotlight(text_p)}]
        try:
            r = self.gateway.anfrage(msgs, klasse=max(2, self.min_klasse),
                                     anruf_id=self.anruf_id, max_tokens=60, json_modus=True)
            d = json.loads(r.text)
            a_id = d.get("anliegen", "unbekannt")
            if a_id != "unbekannt" and anliegen(a_id).id == "unbekannt":
                raise ValueError("unbekanntes Anliegen vom Modell")
            p = float(d.get("sicherheit", 0.5))
            nr = d.get("bestellnummer") or nr
        except (KeinBackend, BudgetErschoepft, ValueError, json.JSONDecodeError,
                TypeError, AttributeError):
            return r_id, r_p, nr
        if a_id == "unbekannt" or (r_id != "unbekannt" and
                                   (anliegen(r_id).risiko, anliegen(r_id).klasse) >
                                   (anliegen(a_id).risiko, anliegen(a_id).klasse)):
            if r_id != "unbekannt":
                self.audit.schreiben("klassifikation_angehoben", anruf=self.anruf_id,
                                     modell=a_id, regel=r_id)
                return r_id, r_p, nr
        return a_id, p, nr

    def _llm_plan(self, text_p: str, kontext: dict, aktion: str, werkzeuge: List[str]) -> Antwortplan:
        a = self.anliegen
        klasse = max(a.klasse, self.min_klasse)
        msgs = [{"role": "system", "content": SYSTEM_GRUNDREGELN},
                {"role": "system", "content": "KONTEXT:" + json.dumps(kontext, ensure_ascii=False)}]
        msgs += self.verlauf[-6:]
        msgs.append({"role": "user", "content": spotlight(text_p)})
        return self._plan(llm_messages=msgs, klasse=klasse, aktion=aktion, werkzeuge=werkzeuge)

    def antwort_merken(self, text: str) -> None:
        self.verlauf.append({"role": "assistant", "content": text})

    # -------------------------------------------------------------- Kern
    def wechsel(self, text: str) -> Antwortplan:
        text = ziffern_zusammenziehen(text.strip())
        text_p, pii = self.tresor.pseudonymisieren(text)
        if pii:
            self.min_klasse = max(self.min_klasse, 2)
        self.verlauf.append({"role": "user", "content": text_p})

        # 1. Offene Rückfrage
        if self.offen:
            return self._rueckfrage(text_p)

        # 2. Mensch verlangt
        if _MENSCH.search(text):
            return self._plan(text="Gerne, ich verbinde Sie mit einer Mitarbeiterin oder "
                                   "einem Mitarbeiter. Einen Moment bitte.",
                              uebergabe=self._uebergabe("Kundschaft verlangt Menschen"),
                              aktion="uebergabe_auf_wunsch")

        # 3. Prompt-Injection
        if injection_pruefen(text):
            self.injektionen += 1
            self.audit.schreiben("injection", anruf=self.anruf_id, anzahl=self.injektionen)
            if self.injektionen >= 2:
                return self._plan(text="Ich verbinde Sie mit unserem Team.",
                                  uebergabe=self._uebergabe("wiederholte Manipulationsversuche"),
                                  aktion="uebergabe_injection")
            return self._plan(text="Dabei kann ich nicht helfen. Worum geht es bei Ihrem Anliegen?",
                              aktion="injection_abgewehrt")

        # 4./5. Klassifizieren
        a_id, sicherheit, nr = self._klassifizieren(text_p)
        if nr:
            self.bestellnummer = nr
        if a_id == "unbekannt":
            if self.anliegen and self.offen is None and _BESTELLNR.search(text_p):
                a_id = self.anliegen.id            # Nummer nachgeliefert
            else:
                self.unklar += 1
                if self.unklar >= 2:
                    return self._plan(text="Ich verbinde Sie mit unserem Kundendienst.",
                                      uebergabe=self._uebergabe("Anliegen unklar"),
                                      aktion="uebergabe_unklar")
                return self._plan(text="Damit ich Sie richtig verbinde: Geht es um eine Bestellung, "
                                       "eine Reklamation, eine Rechnung oder etwas anderes?",
                                  aktion="nachfragen")
        self.anliegen = a = anliegen(a_id)
        # Hochwassermarke: Einmal heikel, bleibt der Anruf heikel (V nie sinkend)
        self.min_klasse = max(self.min_klasse, a.klasse)

        # 6. Policy
        if a.autonomie == SOFORT_UEBERGEBEN:
            return self._plan(text=f"Dafür ist unser Bereich {a.bereich} zuständig. Ich verbinde Sie "
                                   f"und gebe Ihr Anliegen bereits weiter.",
                              uebergabe=self._uebergabe(f"Risiko R{a.risiko}"),
                              klasse=a.klasse, aktion="sofort_uebergeben")

        if a.autonomie == AUFNEHMEN_UND_UEBERGEBEN:
            return self._ticket_vorschlagen(f"{a.beschreibung}", prio=1)

        if a.id == "bestellung-ankommen":
            if not self.bestellnummer:
                return self._plan(text="Gerne schaue ich nach. Wie lautet Ihre sechsstellige "
                                       "Bestellnummer?", klasse=a.klasse, aktion="slot_bestellnummer")
            e = self.werkzeuge.aufrufen("bestellstatus_abfragen",
                                        {"bestellnummer": self.bestellnummer},
                                        erlaubt=list(a.tools), anruf_id=self.anruf_id)
            if e.status != "ok":
                return self._ticket_vorschlagen("Bestellstatus nicht abrufbar", prio=2)
            if not e.daten.get("gefunden"):
                return self._ticket_vorschlagen(f"Bestellung {self.bestellnummer} nicht gefunden", prio=2)
            return self._llm_plan(text_p, {"typ": "bestellstatus", "bestellnummer": self.bestellnummer,
                                           "daten": e.daten},
                                  "bestellstatus", ["bestellstatus_abfragen"])

        # R0: Wissensfrage, nur mit Quelle beantworten
        e = self.werkzeuge.aufrufen("wissen_suchen", {"frage": text_p[:500]},
                                    erlaubt=list(a.tools), anruf_id=self.anruf_id)
        if e.status == "ok" and e.daten.get("treffer"):
            return self._llm_plan(text_p, {"typ": "wissen", "daten": e.daten},
                                  "wissen_beantwortet", ["wissen_suchen"])
        self.offen = {"art": "uebergabe"}
        return self._plan(text="Das kann ich Ihnen leider nicht sicher beantworten. Soll ich Sie "
                               "mit einer Mitarbeiterin verbinden?", klasse=a.klasse,
                          aktion="keine_quelle", werkzeuge=["wissen_suchen"])

    def _ticket_vorschlagen(self, thema: str, prio: int) -> Antwortplan:
        a = self.anliegen
        args = {"thema": thema, "bereich": a.bereich, "prioritaet": prio}
        e = self.werkzeuge.aufrufen("ticket_erstellen", args, erlaubt=list(a.tools) or
                                    ["ticket_erstellen"], anruf_id=self.anruf_id)
        if e.status == "bestaetigung_noetig":
            self.offen = {"art": "tool", "tool": "ticket_erstellen", "args": args}
            return self._plan(text=e.text, klasse=a.klasse, aktion="bestaetigung_angefragt",
                              werkzeuge=["ticket_erstellen"])
        return self._plan(text="Ich verbinde Sie mit unserem Team.",
                          uebergabe=self._uebergabe("Ticket nicht möglich"), aktion="uebergabe_fehler")

    def _rueckfrage(self, text_p: str) -> Antwortplan:
        offen, self.offen = self.offen, None
        ja, nein = bool(_JA.search(text_p)), bool(_NEIN.search(text_p))
        if ja == nein:      # weder noch, oder beides ("ja, nein, doch nicht")
            if offen.get("nachgefragt"):
                return self._plan(text="Ich verbinde Sie mit unserem Team.",
                                  uebergabe=self._uebergabe("Bestätigung unklar"),
                                  aktion="uebergabe_unklar")
            self.offen = dict(offen, nachgefragt=True)
            return self._plan(text="Entschuldigung, bitte antworten Sie mit Ja oder Nein.",
                              aktion="bestaetigung_wiederholt")
        if offen["art"] == "uebergabe":
            if ja:
                return self._plan(text="Gerne, ich verbinde Sie.",
                                  uebergabe=self._uebergabe("keine Antwort in Wissensbasis"),
                                  aktion="uebergabe_angenommen")
            return self._plan(text="In Ordnung. Kann ich sonst etwas für Sie tun?",
                              aktion="uebergabe_abgelehnt")
        if nein:
            return self._plan(text="In Ordnung, ich habe nichts erfasst. Kann ich sonst helfen?",
                              aktion="aktion_abgelehnt")
        e = self.werkzeuge.aufrufen(offen["tool"], offen["args"], erlaubt=[offen["tool"]],
                                    anruf_id=self.anruf_id, bestaetigt=True)
        if e.status not in ("ok", "duplikat"):
            return self._plan(text="Das hat leider nicht geklappt. Ich verbinde Sie mit unserem Team.",
                              uebergabe=self._uebergabe("Werkzeugfehler"), aktion="uebergabe_fehler")
        ticket = e.daten.get("ticket", "")
        a = self.anliegen
        if a and a.autonomie == AUFNEHMEN_UND_UEBERGEBEN:
            return self._plan(text=f"Ich habe das Ticket {ticket} erfasst und verbinde Sie jetzt "
                                   f"mit {a.bereich}.",
                              uebergabe=self._uebergabe(f"R{a.risiko}: aufnehmen und übergeben", ticket),
                              aktion="ticket_und_uebergabe", werkzeuge=[offen["tool"]])
        return self._plan(text=f"Erledigt, Ihre Ticketnummer ist {ticket}. Kann ich sonst helfen?",
                          aktion="ticket_erstellt", werkzeuge=[offen["tool"]])
