# Native Fremdsoftware und Quellbereitstellung

PapaGUI 0.5.0 verwendet GPL-3.0-or-later für den eigenen Code. Der Projektinhaber
hat bestätigt, dass dieser von ihm oder im Rahmen seiner Codex-gestützten
Entwicklung stammt. Eingebundene Fremdsoftware behält ihre eigenen Rechte.

## Nachvollziehbarer Lieferumfang

Jeder native Build erfasst die von PyInstaller ausgewählten Bibliotheken in
`native-inventory.json`: Produkt, relativer Zielname, Herkunftspaket und SHA-256.
Absolute Pfade des Buildrechners werden nicht veröffentlicht. Linux ergänzt die
verfügbaren Copyright-Dateien der zugeordneten Debian-/Ubuntu-Pakete. Der Bericht
erfasst Dateien, nicht automatisch alle statisch darin eingebetteten Komponenten.

`dependency-inventory.json` enthält die installierten, versionsgeprüften Python-
Abhängigkeiten und ihre Lizenztexte. Dazu kommen die Lizenz des verwendeten
Python-Interpreters und bei Windows die Inno-Setup-Lizenz. Die Qt/PySide-Quellen
ergänzen die Originalhinweise für eingebettete Komponenten, insbesondere PDFium,
Chromium, Bildformate, Schriften und Kompressionsbibliotheken.

## Qt, Qt PDF und PySide6

Die Datei `packaging/upstream-sources.json` legt die offiziellen vollständigen
Qt- und PySide-Quellarchive für 6.11.1 samt SHA-256 fest. Qt PDF/PDFium liegen im
Modul qtwebengine des Qt-Archivs; PySide und Shiboken im pyside-setup-Archiv.
Die Archive werden unverändert aufbewahrt. Lizenz-/Copyright-/Notice-Dateien,
Qt-Attributionen und Chromium-Herkunftsangaben werden mit Originalpfad und Hash
in die Lizenzverzeichnisse der Installer und Updatearchive übernommen.

Vor dem Build:

```sh
python tools/prepare_upstream_sources.py
```

Nach beiden PyInstaller-Builds:

```sh
python tools/collect_native_inventory.py --analysis build/papagui-client/Analysis-00.toc --analysis build/papagui-tray/Analysis-00.toc
```

Fehlende Quellenhinweise oder native Inventare brechen die Paketierung ab.
Die vollständigen `.tar.xz`-Quellarchive müssen neben den jeweiligen öffentlichen
Binärdownloads angeboten werden; ein Link nur auf die Herstellerhomepage genügt
für diese Veröffentlichung nicht. GitHub-Buildartefakte alleine sind noch kein
öffentlicher Download ohne Anmeldung.

## Bauen und Ändern

Die eigenen Buildskripte, Paketlocks und PyInstaller-Specs gehören zum Quelltag.
Die mitgelieferten Qt/PySide-Bibliotheken stammen unverändert aus den in den
Locks angegebenen Upstream-Wheels. Qt-Buildanweisungen und Konfigurationen liegen
in den Quellarchiven; PySide enthält seine Buildskripte und Coin-Konfigurationen.
Für einen eigenen Build zuerst Qt mit dessen `configure`/CMake-Anleitung bauen,
danach PySide/Shiboken gegen diese Qt-Installation bauen/installieren und die
PapaGUI-Specs erneut ausführen. Python 3.11 verwenden und die übrigen Abhängigkeiten
aus den Projektlocks installieren. Die PyInstaller-Onefile-Verpackung kann so
mit einer angepassten Bibliotheksversion neu erzeugt werden; eine SignPath-Signatur
ist zum Ausführen eigener Builds nicht erforderlich. Das Projekt untersagt keine
Änderungen oder Untersuchungen zur Fehlersuche.

## Prüfung je Zielsystem

Microsoft-Systemlaufzeiten und macOS-Systembibliotheken sind von Projektcode zu
unterscheiden. Ein Eintrag „Platform runtime“ ist keine automatisch bestätigte
Lizenzklassifizierung. Vor einer allgemeinen Produktfreigabe die Inventare aller
Zielsysteme einschließlich der in Python/Pillow/lxml und Software-OpenGL
eingebetteten Komponenten prüfen. Die bereitgestellten Archive sind kein Beweis
für bitidentische Reproduktion eines Hersteller-Wheels.

Der Docker-Server enthält zusätzlich Debian-Pakete, darunter Poppler, antiword,
catdoc und Tesseract. `tools/export_server_sources.py` exportiert im Releaseworkflow
deren versionsgenaue Paket-/Quellinventare aus einem Wegwerfcontainer des
ausgelieferten Image-Digests, getrennt für amd64 und arm64. Dazu gehören die
Quellen aller installierten Debian-Pakete, ihre Copyright-Dateien und die
Python-Pakethinweise. Falls die aktuelle Debian-Paketliste eine installierte
Version nicht mehr enthält, wird deren exakter Stand aus Debian Snapshot geladen.
Fehler oder fehlende Quellen brechen den Release-Upload ab. Den Exporthelfer
nicht im produktiven Server ausführen: er verändert vorübergehend die apt-Indizes.

Quellen: [Qt-PDF-Lizenzen](https://doc.qt.io/qt-6/qtpdf-licensing.html),
[Qt-Quellarchiv](https://download.qt.io/official_releases/qt/6.11/6.11.1/single/),
[PySide-Quellarchiv](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.1-src/).
