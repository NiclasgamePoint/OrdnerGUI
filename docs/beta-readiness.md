# Beta-Abnahme und Signierung

Prüfstand: 14. September 2026, bereinigter Quellcommit `1b1e3d1`, Pakete jeweils **0.5.0**.
Dies ist eine Bestandsaufnahme und Arbeitsliste, keine erfolgte Releasefreigabe.

## Gemeinsam festgelegter nächster Schritt

| Listenpunkt | Stand | Nächste Aufgabe |
| --- | --- | --- |
| GitHub-MFA | Vom Projektinhaber als aktiv bestätigt | Erledigt |
| Repositorysichtbarkeit | Öffentlich; zwei PDFs aus Branch-/Tag-Historie entfernt; Support-Anfrage laut Inhaber eingereicht | Bestätigung der GitHub-Bereinigung abwarten |
| Beta-Ziele | Windows x64 und Ubuntu 24.04 x64; macOS zurückgestellt | Frische Testsysteme festlegen |
| Windows-Signierung | Kostenloser SignPath-Foundation-Weg gewählt | Bewerbung nach Projekt-/Lizenzfreigabe einreichen |
| Signierungsanleitung | [Vollständiger Ablauf](windows-signing.md); `NiclasgamePoint` übernimmt alle drei Rollen | Nach Aufnahme Zugang verbinden |
| SignPath-Bewerbung | [Angaben und Text vorbereitet](signpath-application.md); Codeherkunft und Logo bestätigt | Öffentlichen Testrelease und SignPath-Aufnahme abschließen |
| Linux-Installer | DEB und Prüfsummen vorhanden | [Direktdownload abnehmen](linux-distribution.md); OpenPGP-Schlüssel festlegen |
| Beta-Veröffentlichung | Windows-/Linux-Publisher lokal implementiert und geprüft | Geänderten Stand durch GitHub prüfen; noch kein Release veröffentlicht |
| GitHub nach Historienbereinigung | Quality/Server grün; Windowsjob im zweiten Versuch ebenfalls grün | 0.5.0 benötigt seinen eigenen vollständigen Lauf |
| Serverordner und Einrichtung | Weiter offen | Zielhost wählen, Einrichtung und Quellprüfung abnehmen |
| Lizenz und öffentliche Bereitstellung | Weiter offen | Native Lizenz-/Quellinventare und Historie prüfen |
| Reale Installation, Update und Restore | Weiter offen | Synthetische Abnahme auf den Zielsystemen |

Eine SignPath-Zusage wurde noch nicht erteilt. [Code signing policy](../CODE_SIGNING.md)
ist als Entwurf markiert; [Datenschutzhinweise](../PRIVACY.md) sind ergänzt.

## Tatsächlicher Veröffentlichungsstand

- `0.4.3` behält Namen, Beschreibung „PreRelease Testing“ und Quellstand.
  Die Historienbereinigung ändert seine Git-Objekt-IDs; das Commit ist nun `dbf6c9a`.
- GitHub meldet inzwischen ein öffentliches Repository und weiterhin keine Releases.
- Quality, alle vier Clientziele und Serverimage waren vor der Bereinigung für
  `c7b5b8d` erfolgreich (nun inhaltsgleich `6dd44af`).
  Der lokale Theme-Commit `1b1e3d1` liegt einen Commit vor `origin/main`;
  für ihn liegt noch kein entsprechender GitHub-Nachweis vor.
- Installer sind ausdrücklich unsigniert. Eine erfolgreiche Installation auf
  einem Buildrunner prüft weder SmartScreen noch Apples Download-Quarantäne.
- Der vorbereitete `release.yml`-Workflow prüft Pre-releases für Windows und Linux,
  veröffentlicht deren Installer und das Serverimage und lädt `papagui-beta.json`
  mit dem Image-Digest hoch. Er erzeugt kein Aktivierungsmanifest. Der stabile
  Publisher und Clientfeed verlangen weiterhin alle vier Plattformen und
  numerische Versionen; `0.5.0-beta.1` wird nicht unterstützt. Für die erste Beta
  eine numerische Paketversion mit dem GitHub-Pre-release-Flag verwenden.
- Es gibt einen Desktopinstaller mit Client und Server-Verwaltungsoberfläche.
  Der eigentliche Indexserver benötigt ein separates Docker-Deployment.

Quellen im Repository: [Releaseworkflow](../.github/workflows/release.yml),
[Clientworkflow](../.github/workflows/client-artifacts.yml),
[Installerbuilder](../tools/build_installers.py),
[Assetveröffentlichung](../tools/publish_update_manifest.py).

## Signierung: Aufgaben des Herausgebers

Die Kontoinhaberschaft und gegebenenfalls erforderliche Identitätsprüfung
muss der Herausgeber selbst übernehmen. Für Windows ist die kostenlose Bewerbung
bei SignPath Foundation gewählt; die Aufnahme steht noch aus. Danach können Build, Signierung,
Notarisierung, Verifikation und Veröffentlichung über CI automatisiert werden.
Keine privaten Schlüssel oder Passwörter in Chat, Git oder Buildlogs ablegen.
Eine GitHub-Verbindung ersetzt keine Code-Signing-Identität.

### Windows

1. Den Herausgeber festlegen: Privatperson oder Organisation.
2. Für dieses freie Projekt **SignPath Foundation** prüfen. Bewerbung,
   bereits veröffentlichte Software, nachvollziehbare Builds, MFA, benannte
   Verantwortliche und eine veröffentlichte „Code signing policy“ sind Teil
   der Bedingungen. Die Aufnahme ist nicht garantiert. Der Zertifikatsinhaber
   ist SignPath Foundation; jeder Signierauftrag benötigt eine Freigabe.
   Quellen: [Angebot](https://signpath.org/),
   [Bedingungen](https://signpath.org/terms.html).
3. Als Alternative einen öffentlichen Code-Signing-Dienst mit passender
   Identitätszulassung wählen. Microsoft Artifact Signing erlaubt derzeit
   Organisationen unter anderem in der EU; Privatpersonen müssen in den USA
   oder Kanada ansässig sein. Eine deutsche Privatperson ist somit aktuell
   nicht über diesen Weg zugelassen. Andere Zertifikatsanbieter gesondert auf
   Privatpersonen und eine für CI nutzbare Schlüsselspeicherung prüfen.
   Quelle: [Microsoft-Einrichtung](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart).
4. Dienstzugriff gezielt für Releasejobs einrichten: je nach Anbieter OIDC,
   eingeschränkter Diensttoken oder dessen geschützte Signierumgebung.

Die technische Reihenfolge lautet: eigene Client-/Tray-EXEs mit vollständigen
Produkt- und Versionsinformationen bauen, signieren und zeitstempeln; Installer
einschließlich Deinstallationsprogramm signieren; Signaturen prüfen; erst danach
Prüfsummen und Updatearchive erzeugen. Der aktuelle Inno-Setup-Build enthält
noch keine SignTool-/SignedUninstaller-Konfiguration. Der Publisherfilter
erlaubt Windows-Installer derzeit nur mit `-setup-unsigned.exe` und muss mit
der Einführung signierter Artefaktnamen angepasst werden.

Eine gültige Signatur zeigt den Herausgeber an und schützt die Integrität.
Sie garantiert insbesondere bei neuen Builds keine sofortige Freiheit von
SmartScreen-Warnungen. Selbstsignierte Zertifikate lösen die öffentliche
Vertrauensfrage nicht. Quelle:
[Microsoft SmartScreen](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation).

### macOS Intel und Apple Silicon

Für die erste Windows-/Linux-Beta zurückgestellt. Diese Schritte sind erst für
eine spätere macOS-Freigabe erforderlich.

1. Am [Apple Developer Program](https://developer.apple.com/programs/enroll/)
   teilnehmen; derzeit 99 USD pro Mitgliedschaftsjahr beziehungsweise lokaler Preis.
2. Als Account Holder eine **Developer ID Application** für beide Apps und
   eine **Developer ID Installer** für das PKG erstellen. Das sind unterschiedliche
   Zertifikatstypen. Quelle:
   [Apple-Zertifikate](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/).
3. Zertifikate mit zugehörigen privaten Schlüsseln geschützt für temporäre
   CI-Keychains bereitstellen, außerdem einen passenden Notarisierungszugang
   über API-Key oder Apple-ID mit app-spezifischem Passwort.
4. CI auf beiden Mac-Runnern ausführen: Apps signieren, notarisierten Appzustand
   für Updatearchive bereitstellen, signiertes PKG bauen, notarisiert veröffentlichen.

Besonders wichtig für diesen Build: PyInstaller erzeugt Onefile-Programme.
Die eingebetteten nativen Bibliotheken müssen bereits beim Build über
`codesign_identity` signiert werden; nur nachträglich die äußere App oder das
PKG zu signieren genügt nicht. Hardened Runtime und erforderliche Entitlements
mit den tatsächlichen Qt-/PDF-Bibliotheken testen. Quelle:
[PyInstaller-Signierung](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing).

Nach der Konfiguration verwendet CI `xcrun notarytool submit ... --wait`,
prüft den Status `Accepted` und heftet das Ticket mit `xcrun stapler staple`
an die ausgelieferten Apps/PKGs. `codesign --verify`, `pkgutil --check-signature`,
`spctl` und `stapler validate` gehören zur Abnahme. Auch die Apps in den separaten
Updatearchiven müssen den geprüften, signierten Zustand enthalten.
Quelle: [Apple-Notarisierungsablauf](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow).

### Linux

Der aktuelle Installer ist eine DEB für Ubuntu 24.04 x64, mit glibc >= 2.39.
Das ist keine Zusage für alle Distributionen und Architekturen. Für weitere
Ziele wären zum Beispiel Flatpak oder zusätzliche distributionsspezifische
Pakete separat zu bauen und abzunehmen.

Für einen APT-Vertrieb signiert man die Repository-Metadaten und verteilt den
zugehörigen öffentlichen Schlüssel. Eine beliebige Signatur neben einer DEB
ist kein automatisch vertrauenswürdiges APT-Repository. Für direkte Downloads
können separat verifizierbare Prüfsummensignaturen ergänzt werden.
Quelle: [Debian-Paketsignierung](https://www.debian.org/doc/manuals/securing-debian-manual/deb-pack-sign.en.html).

## Welche Ordner wo eingestellt werden

| Inhalt | Aktuelle Konfiguration | Beim Desktopinstaller auswählbar? |
| --- | --- | --- |
| Originaldokumente auf dem Server | `PAPAGUI_SOURCE_PATH` am Docker-Host → `/source`, read-only; `PAPAGUI_SOURCE_ID` als stabile Kennung | Nein; Servereinrichtung separat |
| Serverdatenbanken und Index | `PAPAGUI_SERVER_DATA_PATH` → `/data` | Nein |
| Serverkonfiguration und Token | `PAPAGUI_SERVER_CONFIG_PATH` → `/config` | Nein |
| Dieselben Originaldokumente am Client | Ersteinrichtung; später Einstellungen → Suche → Pfadzuordnungen je Betriebssystem | Beim ersten Programmstart |
| Lokales Clientprofil mit Cache und Offline-Outbox | OS-Standard oder `PAPAGUI_CLIENT_DATA_ROOT`; optional separate `PAPAGUI_CLIENT_CONFIG_PATH` | Nein; in Einstellungen → Allgemein nur lesbar |

Beispiel für **dieselbe** Quelle `archive`, ohne Rechnerpfade in den Index zu
übernehmen: Serverhost `/srv/archiv`, Windows `\\nas\archiv`, macOS
`/Volumes/archiv`, Linux `/mnt/archiv`. Ein Dokument bleibt im Index beispielsweise
`archive` + `2026/Projekt/Angebot.pdf`. Die Clientzuordnung setzt daraus den
jeweils gültigen Betriebssystempfad zusammen.

Der Server verarbeitet derzeit eine konfigurierte Quelle pro Instanz. Die
Tray-Einstellungen verändern Indexoptionen, aber keinen Docker-Hostmount.
Ein anderer Server-Quellordner erfordert eine Anpassung des Deployments und
das Neuerstellen des Containers. Ein gewöhnlicher Container-Neustart übernimmt
keine veränderte Compose-Mountdefinition.

### Schutz vor falschen Quellen

Der Server prüft Erreichbarkeit und nichtleeren Inhalt, speichert `source_id`
sowie bis zu 32 Namen oberster Einträge in `source-identity.json` und vergleicht
diese vor der Verarbeitung. Bei fehlender oder leerer Quelle wird der vorhandene
Index erhalten. Die Dokumente sind außerdem schreibgeschützt eingebunden.

Das ist eine **Heuristik**: Schon ein übereinstimmender Eintragsname genügt;
ein falscher Ordner mit derselben Jahresstruktur kann deshalb bestehen.
Beim ersten Start ist automatische Initialisierung standardmäßig erlaubt.
Es fehlt die ausdrückliche Bestätigung „Das ist mein Archiv“ anhand einer
prüfbaren Vorschau beziehungsweise stärkeren Quellidentität.

Für ein Endanwender-Serversetup sollten Quell-, Daten- und Konfigurationspfad
explizit gewählt, absolute Pfade und getrennte Verzeichnisse geprüft sowie
Zugriffsrechte unter der tatsächlichen Container-UID getestet werden. Fehlende
Quellpfade sollten die Installation abbrechen, statt angelegt zu werden.
Die Entwicklerdefaults `../../Bauvorhaben`, `../../docker-data` und
`../../docker-config` in Compose sind dafür noch ungeeignet. Sie sind Defaults,
keine Bindung der installierten Clientsoftware an diesen Entwicklungsrechner.

### Verschieben und Netzlaufwerke

- **Originaldokumente verschieben:** Stammordner ändern, relative Struktur und
  `source_id` erhalten und betroffene Clientzuordnungen/Servermounts anpassen.
  Bei Änderungen innerhalb des Archivs muss der Server neu indexieren.
- **Netzfreigabe:** Windows unterstützt UNC und Laufwerksbuchstaben; macOS/Linux
  benötigen eine unter einem Dateisystempfad eingehängte Freigabe. PapaGUI meldet
  keine SMB-Freigabe an und richtet keinen VPN-Zugang ein. Der jeweilige
  Betriebssystembenutzer benötigt Zugriff. Der API-Zugang ersetzt diesen nicht.
- **Außerhalb des Büros:** Für Originaldateien/Vorschau braucht der Client
  zusätzlich zur API den Freigabezugriff, zum Beispiel über ein VPN. Offline
  bleibt der synchronisierte Katalog nutzbar; nicht erreichbare Originaldateien
  werden dadurch nicht automatisch lokal verfügbar.
- **Clientprofil verschieben:** Client und Tray beenden, vollständiges Profil
  sichern/kopieren, beide Programme mit dem neuen `PAPAGUI_CLIENT_DATA_ROOT`
  starten und eine eventuell separate `PAPAGUI_CLIENT_CONFIG_PATH` anpassen.
  Outbox mit noch nicht übertragenen Änderungen vollständig erhalten. Der
  daneben liegende `<Datenordner>-updates`-Bestand gehört in die Umzugsplanung.
  Es gibt bislang weder Umzugsassistent noch automatische Wiederentdeckung.
- **Interne Datenbanken lokal lassen:** Client-Outbox und Server-Kundendatenbank
  verwenden SQLite-WAL. Ein gemeinsames Clientprofil oder die Serverdatenbank
  auf SMB/NFS wird nicht als unterstütztes Deployment freigegeben. Lokaler
  NAS-Speicher für den direkt auf dem NAS betriebenen Server ist davon zu
  unterscheiden. [SQLite dokumentiert diese Netzwerkgrenze](https://sqlite.org/wal.html).

Die Suche in den Paketquellen und Start-/Builddateien fand keine fest eingebauten
persönlichen Archivpfade in der installierten Anwendung. Betriebssystemdefaults,
Containerpfade und Beispieltexte sind vorhanden und absichtlich unterschiedlich.
Nicht abgenommen sind beliebige NAS-Produkte, lange Pfade, Unterbrechungen und
deren Wiederanmeldung. Normale Pfadzuordnungen prüfen bislang nicht vollständig
absolute Pfade, Erreichbarkeit und Übereinstimmung mit der Serverquelle;
die Ersteinrichtung prüft nur den lokal erreichbaren Ordner. Ihre synchrone
Dateisystemprüfung sollte bei langsamen Freigaben ebenfalls geprüft werden.

## Verbindliche Beta-Arbeitsliste

1. **Zielumfang festgelegt:** Windows x64 und Ubuntu 24.04 x64; Server
   Linux/Docker amd64/arm64. macOS ist zurückgestellt. Keine pauschale Zusage
   „alle Betriebssysteme“.
2. **Beta-Veröffentlichung lokal ergänzt:** Pre-releases prüfen, Installer und
   ein eindeutig versioniertes Image veröffentlichen, ohne Aktivierung im
   stabilen Updatekanal. Die GitHub-Ausführung des geänderten Stands bleibt
   abzunehmen. Ein automatischer Beta-Kanal ist weiterhin nicht implementiert.
3. **Signierung integrieren und abnehmen:** Anbieter/Konten bereitstellen,
   wiederverwendbaren Workflows benötigte Secrets gezielt übergeben, bei einer
   angeforderten Signierung fehlende Konfiguration als Fehler behandeln.
   Updatearchive erst aus den fertigen signierten Apps erzeugen. Prüfsummen
   alleine sind keine Herausgebersignatur. Eine separat signierte Update-Metadatei
   mit eigener Vertrauensprüfung ist derzeit nicht implementiert.
4. **Serverinstallation konkret machen:** Einmalige Hosteinrichtung von Docker,
   Ordnern, Identität, Token, Rechten, Autostart, Backup und optional Updater.
   Für eine betreute Beta genügt ein abgenommener dokumentierter Ablauf; ein
   Ein-Klick-Serverinstaller mit Ordnerauswahl existiert noch nicht.
5. **Externen Zugriff abnehmen:** Erreichbarer Hostname, passender Port/Firewall,
   gültiges HTTPS oder VPN, korrektes API-Token und NAS-Zugriff vom Client.
   Compose bindet standardmäßig an `127.0.0.1`; das ist von anderen PCs nicht
   erreichbar. Die rohe HTTP-API nicht als ungeprüften Internetdienst freigeben.
6. **Öffentliche Lieferung prüfen:** Das Repository ist bereits öffentlich;
   GitHub-Caches/alte PR-Verweise der entfernten PDFs bereinigen lassen und
   offene Inhaltsfreigaben klären. GHCR-Paketsichtbarkeit
   separat einrichten. Downloads und Image-Pull ohne Entwickleranmeldung testen. GitHub-
   Freigaben und Signierung werden durch einen lokalen Commit nicht eingerichtet.
7. **Lizenzpaket vervollständigen:** GPL-3.0-or-later und Namensdatenhinweise sind
   vorhanden. Vollständige native Inventare, Lizenztexte und passende Quellstände
   für mitgelieferte Komponenten werden durch native Inventare und den
   automatisierten Quellenexport ergänzt; Plattformprüfung bleibt erforderlich.
   Herkunft des eigenen Projektcodes ist durch den Inhaber bestätigt.
   Die öffentliche Weitergabe der Logovorlage ist durch den Inhaber bestätigt.
   Ein Beta-Label befreit bei Weitergabe nicht von den Lizenzbedingungen.
   [GPLv3, Abschnitt 6](https://www.gnu.org/licenses/gpl.en.html#section6).
8. **Reale Abnahme:** Auf frischen Systemen aus dem tatsächlich heruntergeladenen
   Installer installieren; Client/Tray, Vorschauformate, Pfadwechsel, NAS-Ausfall,
   Offlineänderungen, Wiederverbindung, Upgrade, Deinstallation und Restore testen.
   Update inklusive Rollback und gemischter Client-/Serverversionen prüfen.
   Die aktuelle Kompatibilitätsbasis ist 0.4.3, kein Versprechen für alle künftigen Versionen.
9. **Dokumentation abschließen:** Release Notes, bekannte Einschränkungen,
   unterstützte Systeme, Installations-/Umzugs-/Wiederherstellungsanleitung,
   privater Sicherheitsmeldeweg und Datenschutzhinweise einschließlich automatischer
   GitHub-Abfragen. CONTRIBUTING ist sinnvoll, aber keine technische Installationsvoraussetzung.
10. **Exakten Tag prüfen:** Den vollständigen vorgesehenen Stand committen/pushen,
    alle Workflows für genau diesen Commit bestehen lassen und erst danach
    unveränderlich taggen. Downloads, Signaturnachweise und Image-Digest zuordnen.

## Nachweise dieser Prüfung

`tools/release_metadata.py` bestätigt für alle drei Pakete 0.5.0 und konsistente
GPL-3.0-or-later-Metadaten. Der isolierte lokale Testlauf für Pfadauflösung,
Generationsspeicher, Konfiguration/Entrypoints, Quellschutz und Releasepublisher
ergab **39 bestanden, 1 übersprungen**. Der übersprungene Test erfordert eine
Windows-Berechtigung zum Anlegen von Symlinks. UNC sowie Windows-/macOS-/Linux-
Pfadabbildung sind dabei logisch getestet; das ersetzt keinen realen SMB-Test.

Der anschließende lokale Testlauf der Beta-Veröffentlichung und bisherigen
Release-/Packaging-Verträge ergab **32 bestandene Tests**. Actionlint prüfte
die geänderten Workflows erfolgreich. Das ist noch kein entfernter Release-Lauf.

Es wurden keine Zertifikate beantragt, keine Installer signiert, keine Konten
oder Repositorysichtbarkeit geändert und kein Release veröffentlicht.
