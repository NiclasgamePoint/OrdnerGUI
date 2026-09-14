# Datenverarbeitung und Netzwerkverbindungen

Stand: Entwicklungsstand 0.5.0. Diese technische Beschreibung ergänzt die
Datenschutzinformationen des jeweiligen Betreibers; sie benennt keinen
unbekannten Betreiber oder erfundene Kontaktadresse.

PapaGUI verarbeitet Dokumente und Kundeninformationen auf dem vom Betreiber
eingerichteten Indexserver. Der Client lädt Katalog- und Kundensnapshots und
überträgt Benutzeränderungen an diesen Server. Eine externe Modell- oder
KI-Schnittstelle ist nicht Teil der ausgelieferten Dokumentverarbeitung.

Zum Öffnen oder Anzeigen von Dokumenten greift der Client auf die konfigurierte
lokale Datei oder Netzfreigabe zu. Betriebssystem und gegebenenfalls verwendete
Officeprogramme sind an diesem Zugriff beteiligt. Server-URL, Token und lokale
Pfadzuordnungen werden im Benutzerprofil beziehungsweise der separat
konfigurierten Clientdatei gespeichert. Logs und Backups können Pfade und
Metadaten enthalten und sind vom Betreiber vor unberechtigtem Zugriff zu schützen.

Installierte Clients/Trays prüfen standardmäßig beim Start und anschließend
alle sechs Stunden GitHub auf stabile Programmupdates. Ein eingerichteter
Server-Updater prüft standardmäßig stündlich und lädt Images von GHCR.
Dabei erhalten die jeweiligen Dienste die für HTTPS-Anfragen üblichen
Verbindungsinformationen, etwa die öffentliche IP-Adresse. Originaldokumente
werden durch diese Updateabfragen nicht übertragen. Für private Releases
konfigurierte Zugangstokens werden zur Authentifizierung verwendet.

`PAPAGUI_AUTO_UPDATE=0` deaktiviert neue automatische Clientdownloads und deren
Aktivierung; `--offline` verhindert den Update-Netzwerkcheck dieses Starts.
Der separate Server-Updater kann durch den Betreiber angehalten werden.
Eine allgemeine grafische Zustimmungseinstellung für Releaseabfragen ist
noch nicht vorhanden. Diese Grenzen müssen in der Ersteinrichtung und bei
der SignPath-Bewerbung berücksichtigt werden.

Anleitungen: [Automatische Updates](docs/automatic-updates.md),
[Sicherheitskonzept](SECURITY.md), [Fremdsoftware](THIRD_PARTY_NOTICES.md).
Für GitHub/GHCR gelten zusätzlich die
[GitHub-Datenschutzhinweise](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement).
