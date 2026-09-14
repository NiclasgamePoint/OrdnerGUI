# Linux-Beta installieren und verifizieren

Für die erste Beta ist **Ubuntu 24.04 x64** vorgesehen. Der Installer ist eine
`.deb` und enthält Client, Tray, Python und Qt. Die Installation ergänzt die
benötigten Systembibliotheken. Ein eigener Python- oder pip-Aufruf ist beim
Endanwender nicht erforderlich. macOS wird in dieser Beta nicht veröffentlicht.
Andere Linuxdistributionen und Linux ARM64 sind noch nicht freigegeben.

## Direkter Download für die Beta

Release-Dateien nach erfolgreichem Windows-/Linux-Build:

```text
papagui-client-0.5.0-linux-x64.deb
papagui-client-0.5.0-linux-x64.deb.sha256
papagui-client-0.5.0-linux-x64.deb.sha256.asc  (erst nach Signierung vorhanden)
```

Mit vorhandenem Download im aktuellen Verzeichnis:

```sh
sha256sum --check papagui-client-0.5.0-linux-x64.deb.sha256
# Nur bei erfolgreicher Prüfung fortfahren:
sudo apt install ./papagui-client-0.5.0-linux-x64.deb
```

Eine SHA-256-Datei erkennt Übertragungsfehler, bestätigt alleine aber keinen
Herausgeber. Die aktuelle Pipeline erzeugt Prüfsummen, noch keine GPG-Signatur.
Zum Entfernen dient `sudo apt remove papagui-client`; das Benutzerprofil bleibt
erhalten. Der eigentliche Docker-Indexserver wird separat eingerichtet.

## Kostenloser Signierschlüssel für direkte Downloads

Für Linux ist kein Apple- oder Windows-Zertifikat notwendig. Der Herausgeber
kann einen eigenen OpenPGP-Schlüssel verwenden. Dessen öffentlicher Fingerprint
muss verlässlich bekannt gemacht werden; dadurch entsteht nicht automatisch
ein im Betriebssystem vorinstalliertes Vertrauen.

Einmalig auf dem administrierten Rechner des Herausgebers mit installiertem GnuPG:

```sh
gpg --full-generate-key
gpg --list-secret-keys --keyid-format long
gpg --fingerprint
```

Im interaktiven Dialog eine geeignete Signieridentität, Ablaufzeit und starke
Passphrase festlegen. Nur Kontaktdaten verwenden, die öffentlich erscheinen
dürfen. Schlüssel und Widerrufszertifikat sicher außerhalb des Repositories
sichern. Der Schlüssel wurde durch diese Anleitung nicht erzeugt.

Im Folgenden `DEIN_VOLLSTAENDIGER_FINGERPRINT` durch den tatsächlich geprüften
Fingerprint ersetzen. Die Prüfsumme im Downloadverzeichnis nach dem finalen
Build erzeugen und signieren:

```sh
gpg --armor --export DEIN_VOLLSTAENDIGER_FINGERPRINT > papagui-release-key.asc
sha256sum papagui-client-0.5.0-linux-x64.deb > papagui-client-0.5.0-linux-x64.deb.sha256
gpg --local-user DEIN_VOLLSTAENDIGER_FINGERPRINT --armor --detach-sign \
    papagui-client-0.5.0-linux-x64.deb.sha256
```

Veröffentlicht werden Paket, Prüfsumme, Prüfsummensignatur und öffentlicher
Schlüssel. Eine neben der Prüfsumme liegende `.sha256.asc` berücksichtigt der
Beta-Publisher bereits; automatisches Signieren und Verteilen des öffentlichen
Schlüssels sind noch nicht eingerichtet. Bestehende Release-Dateien werden
nicht überschrieben.

Der Empfänger prüft zuerst den Fingerprint über den bekannten Projektkanal,
dann die Signatur und anschließend die Paketprüfsumme:

```sh
gpg --show-keys --fingerprint papagui-release-key.asc
gpg --import papagui-release-key.asc
gpg --verify papagui-client-0.5.0-linux-x64.deb.sha256.asc \
    papagui-client-0.5.0-linux-x64.deb.sha256
# Nur bei erfolgreicher Signaturprüfung und erwartetem Schlüssel:
sha256sum --check papagui-client-0.5.0-linux-x64.deb.sha256
# Nur bei erfolgreicher Prüfsumme:
sudo apt install ./papagui-client-0.5.0-linux-x64.deb
```

GnuPG kann trotz kryptographisch korrekter Signatur melden, dass die Identität
des Schlüssels nicht anderweitig bestätigt wurde. Dann den erwarteten Fingerprint
prüfen, nicht pauschal allen importierten Schlüsseln vertrauen.
Quelle: [GnuPG-Handbuch](https://www.gnupg.org/documentation/manuals/gnupg/GPG-Commands.html).

## Später: eigenes APT-Repository oder weitere Linuxziele

Für regelmäßige Installationen über `apt` kann später ein HTTPS-Paketrepository
mit `Packages`, `Release` und signierter `InRelease` eingerichtet werden.
Der öffentliche Schlüssel wird dafür mit einem auf dieses Repository begrenzten
`Signed-By` eingebunden. Kein globales `apt-key`, kein `trusted=yes` und kein
Abschalten der APT-Signaturprüfung als Installationslösung verwenden.

APT prüft die signierten Repository-Metadaten und daraus die Paketprüfsummen.
Eine neben einer DEB liegende `.asc` macht aus einem Downloadordner noch kein
APT-Repository. Quelle: [apt-secure](https://manpages.debian.org/bookworm/apt/apt-secure.8.en.html).

Flatpak oder zusätzliche RPM-/DEB-Ziele sind spätere Erweiterungen mit eigenen
Tests. Für die betreute Beta genügt der dokumentierte Ubuntu-Downloadweg.
