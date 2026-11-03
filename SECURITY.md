# Sicherheit

## Schwachstellen melden

Bitte keine öffentlichen Issues für Sicherheitslücken. Meldewege:

- GitHub Private Vulnerability Reporting dieses Repos («Security» > «Report a vulnerability»)
- E-Mail an mail@gradweight.ch

Hilfreich sind betroffene Version oder Commit, Beschreibung, Schritte zur Reproduktion und
die vermutete Auswirkung. Wir bestätigen den Eingang und melden uns mit einer Einschätzung.

## Geltungsbereich

Gemeint sind Fehler im Code dieses Repos, z. B. in der Signaturprüfung (`signatur.py`), im
Protokoll (`audiohook.py`), in der Audit-Kette (`audit.py`), in Gateway und Werkzeugen oder
in der Compose-Konfiguration. Die bekannten Grenzen der Regex-Prüfer sind dokumentiert
([docs/16-leitplanken.md](docs/16-leitplanken.md)); eine Umgehung dieser Schicht allein ist
erwartet und kein neuer Befund.

## Unterstützte Versionen

Sicherheitskorrekturen erfolgen auf der jeweils neuesten Version.
