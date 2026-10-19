# 07 - Weiterleitung / Handoff in den naechsten Bereich

> **Stand: Oktober 2026.** Kein Rechtsangebot. Die
> Handoff-Logik ist CRM-unabhängig; die physische
> Anbindung ist pro Projekt.

## 1. Warum Handoff der Wert ist

Ein System, das gut antwortet aber schlecht
weiterleitet, ist ein **Sackgasse-System**.
Der Wert liegt im **Über** (s. 01-
architektur.md, Etappe 6):

- **1. Level**: V0-V1 (öffentlich, normal),
  automatisiert.
- **2. Level**: V2-V3 (intern, vertraulich),
  SLA < 2d, mit Freigabe.
- **3. Level**: V4 (streng), SLA < 1d,
  Zwei-Augen-Prüfung.

**Autonomiegrad** (s. `autonomy`):
R0=5, R1=4, R2=3, R3=2. Jde Freigabe
ist eine Menschliche Prüfung
(Human-in-Loop, Grad 03+),
nicht automatisch.


## 2. Die 6 Anfall-Typen

| Anfall | V | R | SLA | Zuständig |
|---|---|---|---|---|
| Bestellung | V2 | R1 | 2d | Fachbereich |
| Konto/Kasse | V3 | R2 | 1d | Leitende |
| Reklamation | V2 | R2 | 3d | Fachbereich |
| Allgemeine Frage | V1 | R0 | 5d | 1. Level |
| Vertrag / Kündig. | V3 | R3 | 1d | GL, Freigabe |
| Preis-Anfrage | V1 | R0 | 7d | 1. Level |

```bash
python beispiele/handoff_router.py selftest
python beispiele/handoff_router.py \
  route --anfall bestellung-ankommen
```

`beispiele/handoff_router.py`


ist die laeffungsfähige
Version: 6 Anfall-Typen,
V0-V4, R0-R3, SLA
und `darf_in_cloud`
pro Anruf. Ohne
Netz, ohne Key.

## 3. CRM / Ticket-Queue

Die Handoff-Logik ist **CRM-
unabhängig**. Die physische
Anbindung ist Aufwand:

| Tool | CH? | SLA | Fuer |
|---|---|---|---|
| HubSpot | Ja (CH) | gut | KMU, < 100 |
| Zoho | Ja | gut | KMU, < 50 |
| n8n + Postgres | Ja | sehr gut | 50-500 |
| Salesforce | Nein (US) | - | Enterprise |
| EMail-Queue | Ja | ok | < 20 |

## 4. Grenzen (ehrlich)

- **Kein Rechtsrat** - DSG/AI-Act
  gehoert in `governance`.
- **Keine Kalkulation** -
  Grössenordnungen, s.
  `OFFENE_PUNKTE.md` #1.
- **SLA ist ein Verhandlung**,
  nicht eine technische
  Konstante.
- **Autonomiegrad**
  (R0-R3 -> Grad)
  ist Single-Source
  `autonomy`;
  hier nur referenziert.


## 5. Kritische Fragen

| # | Frage | Warum |
|---|---|---|
| 1 | Welche V0-V4 + R0-R3 pro Anfall? | Ohne Kat.: kein Routing, keine SLA. |
| 2 | Wer ist 2./3. Level (Name, Rolle)? | Ohne klaren Zuständig: Ticket verliert sich. |
| 3 | Wie lange SLA pro Kat.? | DSG-Aufbeharung = SLA-bindend; 1d / 2d / 5d? |
| 4 | Welches CRM, wer betreibt es? | Ohne CRM-Team: Ticket-Queue faellt in 2 Wo. |
| 5 | Ist 24/7-SLA noetig? | < 500 Nutzer: Nein; > 500: Ja, 2 Köpfe (s. 10-betrieb). |
