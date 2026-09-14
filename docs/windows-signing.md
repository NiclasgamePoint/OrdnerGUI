# Windows-Installer mit SignPath Foundation signieren

Stand: 14. September 2026. Gewählter Weg: kostenlose Bewerbung bei SignPath
Foundation. Noch keine Zusage, kein Signierungszugang und keine signierten
PapaGUI-Artefakte. Diese Anleitung trennt die notwendigen Kontoschritte von
der anschließenden Buildintegration. macOS ist für diese Beta zurückgestellt.

GitHub-MFA ist laut Projektinhaber aktiv, das Repository ist inzwischen
öffentlich. Die [vorbereiteten Bewerbungsangaben](signpath-application.md)
halten erledigte Schritte und noch offene Freigaben fest.

## 1. Projekt zur Bewerbung vorbereiten

Der Projektinhaber erledigt folgende Punkte gemeinsam mit der Releasevorbereitung:

1. GitHub-MFA ist erledigt; Wiederherstellungscodes privat sichern.
2. Das Repository ist öffentlich. Die zwei historischen PDFs sind aus allen
   veröffentlichten Branches/Tags und dem lokalen Git-Speicher entfernt.
   Für alte Pull-Request-Verweise und Caches hat der Projektinhaber die
   GitHub-Support-Anfrage eingereicht. Die Erledigungsbestätigung steht aus;
   der private Text liegt unter `build/signpath-audit/github-support-request.md`.
3. GPL-Lizenz, Fremdsoftwarehinweise und erforderliche Quellbereitstellung
   abschließen. Einen nachvollziehbaren, ausdrücklich als unsigniert bezeichneten
   Testinstaller mit Quelltag anbieten, sobald dessen Weitergabe freigegeben ist.
4. Im [Entwurf der Code signing policy](../CODE_SIGNING.md) ist `NiclasgamePoint`
   als Verantwortlicher für Entwicklung, Reviews und Signierungsfreigaben bestätigt.
   Die [Datenschutzhinweise](../PRIVACY.md)
   beschreiben auch automatische Updateabfragen; nicht behaupten, es gebe
   grundsätzlich keinerlei Netzwerkübertragung.
5. README und Releasebeschreibung mit Funktionen, unterstützten Systemen,
   Installationsweg, Lizenz, Quellcode und Signierungsrichtlinie verlinken.

SignPath entscheidet über die Aufnahme. MFA, veröffentlichte Software,
nachvollziehbare Builds, benannte Rollen und Freigaben sind wesentliche
Bedingungen. Es gibt keinen Anspruch auf Aufnahme. Quelle:
[SignPath-Bedingungen](https://signpath.org/terms.html).

## 2. Bewerbung einreichen

Die Bewerbung wird vom Projektinhaber über die
[offizielle Bewerbungsseite](https://signpath.org/apply.html) eingereicht.
Falls das Formular dort nicht angezeigt wird, einen normalen Browser verwenden;
keine Kontaktdaten an einen aus Suchtreffern übernommenen Drittanbieter schicken.

Folgenden Text kannst du als Grundlage verwenden. Den tatsächlichen öffentlichen
Release-Link nach Veröffentlichung und die private Kontaktadresse ergänzen:

```text
Project: PapaGUI
Repository: https://github.com/NiclasgamePoint/OrdnerGUI
License: GPL-3.0-or-later
Maintainer / reviewer / signing approver: NiclasgamePoint
Contact: [private contact address]
Public test release: [published release URL]

PapaGUI is a free desktop document catalog with a separate, self-hosted
index server. The Windows package contains a PySide6 desktop client and
an independent server-control tray. Document processing is local to the
configured server; no external model service is used.

Windows artifacts are built from source with PyInstaller and packaged
with Inno Setup 6. We want to sign our two application executables,
the generated uninstaller and the final setup executable. We can use
GitHub-hosted Windows runners and manual approval of signing requests.

Please confirm the supported workflow for our Inno Setup uninstaller
and PyInstaller onefile executables, including bundled open-source
dependencies and the required product/version metadata restrictions.

Code signing policy: [public link to CODE_SIGNING.md]
Privacy information: [public link to PRIVACY.md]
Build workflow: [link to the successful Windows workflow run]
```

Den Antrag habe ich nicht versendet. Es werden keine Identitätsangaben erfunden
und keine bestehende Förderung behauptet. SignPath verwahrt den Signierschlüssel;
für diesen Weg kaufen wir kein PFX-Zertifikat und legen keinen privaten
Code-Signing-Schlüssel auf deinem Rechner oder in GitHub ab.
Quelle: [SignPath-Angebot](https://signpath.org/).

## 3. Nach Aufnahme GitHub verbinden

1. In SignPath die Organisation und das PapaGUI-Projekt öffnen; die von SignPath
   vorgegebenen Kennungen verwenden.
2. Die SignPath-GitHub-App gezielt für dieses Repository freischalten und das
   Projekt mit dem Trusted Build System GitHub.com verknüpfen.
3. Einreichungs- und Freigaberollen festlegen. Der automatisierte Einreicher
   soll Signieraufträge einreichen können; Freigaben erfolgen durch die
   verantwortliche Person.
4. Im Repository unter **Settings → Secrets and variables → Actions** den
   Einreichungstoken als Secret `SIGNPATH_API_TOKEN` hinterlegen.
5. Die nicht geheimen Kennungen als Variables hinterlegen:

| Vorgeschlagener Name | Inhalt |
| --- | --- |
| `SIGNPATH_ORGANIZATION_ID` | SignPath-Organisations-ID |
| `SIGNPATH_PROJECT_SLUG` | Projektkennung |
| `SIGNPATH_SIGNING_POLICY_SLUG` | Freigegebene Signierungsrichtlinie |
| `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG` | Zum jeweiligen Artefakt passende Konfiguration |

Diese Namen sind für unsere Integration vorgesehen; sie werden von der aktuellen
Buildpipeline noch nicht ausgewertet. Keine Zugangsdaten in eine Workflowdatei
schreiben. Wiederverwendbare Workflows müssen das Secret ausdrücklich deklarieren
und vom aufrufenden Releasejob gezielt erhalten.

Für Foundation-Builds müssen die Jobs vor dem Signierauftrag laut SignPath auf
**GitHub-hosted Runnern** laufen. Dein PC bleibt für Vorprüfungen geeignet;
ein lokales EXE beliebiger Herkunft hochzuladen ersetzt diesen Herkunftsnachweis
nicht. Quelle: [SignPath-GitHub-Integration](https://docs.signpath.io/trusted-build-systems/github).

## 4. Die Buildintegration vollständig einrichten

Die Windows-Produkt-/Versionsressourcen in den Specs sind lokal ergänzt und
nativ geprüft. Für die Signierung selbst sind noch Änderungen am Installerbuilder
und an den Workflows nötig.
Nur `SIGNPATH_API_TOKEN` einzutragen schaltet die Signierung nicht ein.

1. Beide Windows-EXEs erhalten inzwischen automatisch passende PE-Metadaten:
   PapaGUI als Produkt, die tatsächliche Paketversion und zutreffende Dateinamen.
   Diese Angaben vor dem Signierauftrag gegen die SignPath-Regeln prüfen.
2. Den geprüften Quelltag auf einem Windows-GitHub-Runner bauen. Client und Tray
   als Workflowartefakt hochladen. SignPath-Authenticodeauftrag für beide EXEs
   einreichen, freigeben und die signierten Dateien herunterladen.
3. Den **Uninstaller vor dem Einpacken** signieren. Inno Setup unterstützt
   `SignedUninstaller=yes` und ein dauerhaftes `SignedUninstallerDir`. Ohne
   integriertes SignTool erzeugt der erste Compilerlauf die zu signierende
   Uninstallerdatei und verlangt deren Signierung. Diese Datei über den mit
   SignPath abgestimmten Ablauf signieren, zurücklegen und den Compiler erneut
   ausführen. Bei veränderter Uninstallerdatei ist eine neue Signierung nötig.
   Ein erwarteter Abbruch zur Signierung darf nicht jeden Compilerfehler als
   Erfolg behandeln. Quellen:
   [Inno SignedUninstaller](https://jrsoftware.org/ishelp/topic_setup_signeduninstaller.htm),
   [Inno SignTool](https://jrsoftware.org/ishelp/topic_setup_signtool.htm).
4. Mit den signierten Client-/Tray-EXEs und dem signierten Uninstaller den
   Installer bauen. Den äußeren Setup-EXE als weiteres Workflowartefakt
   einreichen, freigeben und signiert herunterladen. Nicht annehmen, dass eine
   Signatur des äußeren Inno-Installers dessen eingebettete Programme mitsigniert.
5. Jede SignPath-Artefaktkonfiguration auf die tatsächlich benötigten Dateien
   begrenzen. Vorlagen aus einer Beispieldatei prüfen, Metadatenregeln ergänzen
   und Fremdkomponenten nicht pauschal unter einer falschen Urheberschaft signieren.
   Quelle: [Artefaktkonfiguration](https://docs.signpath.io/artifact-configuration/).
6. Die signierte Ausgabe prüfen. Erst danach den endgültigen Dateinamen
   `papagui-client-0.5.0-windows-x64-setup.exe` vergeben und die Prüfsumme erzeugen.
   Verbindlich den zurückgelieferten signierten Build veröffentlichen. Bei
   fehlender Freigabe oder ungültiger Signatur muss ein Signierungsjob abbrechen.

Die offizielle Action heißt `signpath/github-action-submit-signing-request`.
Sie benötigt Token, Organisations-ID, Projekt-/Policy-Slug und die ID eines
zuvor hochgeladenen GitHub-Artefakts. Für den produktiven Workflow eine geprüfte
Revision fest pinnen und eine ausreichende Wartezeit für die Freigabe wählen.
Aktuelle Eingaben stehen in der
[Actiondefinition](https://github.com/SignPath/github-action-submit-signing-request/blob/main/action.yml).

Für stabile Auto-Updates werden zusätzlich die signierten Client-/Tray-Dateien
vor dem Aufruf von `build_update_bundle.py` eingesetzt. Die aktuelle Beta
veröffentlicht bewusst keine Auto-Updatearchive und kein `papagui-update.json`.

## 5. Signaturen auf Windows kontrollieren

Beispiel nach einer tatsächlich erfolgreichen Signierung. Die Pfade sind
Arbeitsverzeichnisse für zurückgelieferte Dateien, keine bereits vorhandenen
Signierungsartefakte:

```powershell
$signedFiles = @(
    'dist/signed/papagui-client.exe',
    'dist/signed/papagui-tray.exe',
    'dist/signed/papagui-client-0.5.0-windows-x64-setup.exe'
)
foreach ($signedFile in $signedFiles) {
    $signature = Get-AuthenticodeSignature -LiteralPath $signedFile
    if ($signature.Status -ne 'Valid') {
        throw "Signaturprüfung fehlgeschlagen: $signedFile"
    }
    if (-not $signature.TimeStamperCertificate) {
        throw "Zeitstempel fehlt: $signedFile"
    }
    $signature | Select-Object Path, Status,
        @{Name='Herausgeber';Expression={$_.SignerCertificate.Subject}}
}
```

Den Herausgeber zusätzlich mit der bestätigten SignPath-Identität vergleichen;
`Valid` allein benennt nicht den erwarteten Herausgeber. Mit SignTool aus dem
Windows SDK zusätzlich `signtool verify /pa /all /v <Datei>` prüfen und bei
nicht erfolgreichem Exitcode abbrechen. Quelle:
[Microsoft SignTool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool).

Die Prüfsumme wird **nach** Signierung und endgültiger Benennung erzeugt:

```powershell
$installerPath = (Resolve-Path 'dist/signed/papagui-client-0.5.0-windows-x64-setup.exe').Path
$installerHash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
$installerName = [System.IO.Path]::GetFileName($installerPath)
[System.IO.File]::WriteAllText(
    $installerPath + '.sha256',
    $installerHash + '  ' + $installerName + [Environment]::NewLine,
    [System.Text.Encoding]::ASCII
)
```

## 6. Tatsächliche Installation abnehmen

Auf einem frischen Windows-Testkonto/VM den finalen Installer über den Browser
herunterladen, Signatur und Herausgeber prüfen, installieren und beide Programme
öffnen. Danach auch die installierten EXEs und `unins*.exe` prüfen. Upgrade und
Deinstallation testen; vorhandene Benutzerdateien müssen erhalten bleiben.

Eine neue signierte Version kann weiter eine SmartScreen-Reputationswarnung
zeigen. Ein Zertifikat und eine erfolgreiche Signaturprüfung sind keine Garantie
für ausbleibende Warnungen auf jedem Rechner.
[Microsoft erklärt die Reputationsprüfung](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation).

## 7. Danach pro Release

Geprüften neuen Quelltag bauen lassen, angezeigte Signieraufträge prüfen und
freigeben, Verifikation/Installation bestehen lassen und genau diese Artefakte
veröffentlichen. Alte signierte Releases nicht überschreiben. Bei geändertem
Inhalt eine neue Versionsnummer verwenden. SignPath ersetzt keine Funktions-
oder Lizenzabnahme und nimmt keine Änderungen an Originaldokumenten vor.
