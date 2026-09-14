# Lokale SignPath-Vorbereitung

Prüfdatum: 14. September 2026. Bereinigter Basiscommit `1b1e3d1` plus lokale Änderungen.
Der Projektinhaber hat die GitHub-Support-Anfrage laut eigener Rückmeldung
eingereicht; die Bereinigungsbestätigung steht aus. Ein SignPath-Antrag wurde
noch nicht versendet. Keine Releases veröffentlicht oder Signaturen erzeugt.

## Repository und Historie

GitHub bestätigt das Repository als öffentlich. Der Eigentümer bestätigt aktive
MFA und die Verantwortung von `NiclasgamePoint` für Entwicklung, Reviews und
Signierungsfreigabe. Die Logovorlage wurde laut Inhaber mit ChatGPT ohne weitere
Vorlagen erzeugt und darf öffentlich weitergegeben werden. GitHub meldet keine
Releases; der letzte vollständige grüne Workflowstand vor der Bereinigung war
`c7b5b8d` (inhaltsgleich nun `6dd44af`). Neue lokale Änderungen besitzen noch
keinen GitHub-Buildnachweis.

Gitleaks 8.24.3 lief lokal ohne Netzwerkzugriff auf `.git`, mit `--all` und
vollständiger Redaktion der Trefferwerte. Erfasst wurden 146 erreichbare Commits.
Ein Treffer nach `generic-api-key` gehört zu einem konstanten `idempotency_key`
im Testhelfer `_mutation` in `tests/client/test_offline_journal.py`, Commit
`8bfbd9eb9b`. Der AST-Kontext wurde geprüft; es handelt sich um eine synthetische
Testkennung, nicht um einen bestätigten Zugangsschlüssel. Keine Secretwerte
wurden in diesen Bericht übernommen.

Die Dateipfadprüfung vor der Bereinigung erfasste 644 unterschiedliche Pfade.
Auf ausdrücklichen Wunsch des Eigentümers wurden die zwei historischen PDFs
mit git-filter-repo 2.47.0 entfernt: exakte Pfadfilter und Entfernung beider
Blob-IDs, einschließlich Prüfung auf Kopien unter anderen Pfaden. Ihre Inhalte
wurden nicht geöffnet. Für alle 146 Commit-Bäume wurde geprüft, dass ausschließlich
die freigegebenen Dokumente entfernt wurden; 126 Commit-IDs änderten sich.

Beide öffentlichen Branches und beide Tags wurden atomar mit expliziten
Force-with-lease-Bedingungen aktualisiert. Eine frische GitHub-Kopie mit
145 Commits enthält weder PDF-Pfade noch die beiden Blobs in erreichbarer
Historie. Der lokale Theme-Commit blieb zusätzlich erhalten. Arbeitsdateien und
uncommittete Änderungen wurden per SHA-256 unverändert nachgewiesen; die alten
PDF-Blobs sind nach Reflog-/Objektbereinigung im lokalen Git-Speicher nicht mehr
vorhanden. Der Inhalt des Checkpoints `0.4.3` und seine Beschreibung blieben gleich,
seine Git-Objekt-IDs änderten sich. Neue PDF-Dateien werden über `.gitignore`
ausgeschlossen; vorhandene Test-PDFs werden synthetisch erzeugt.

**Noch offen:** GitHub bewahrt die alten internen Verweise der Pull Requests #1
und #2 auf. Die private Anfrage zur Referenz-, Cache- und Serverbereinigung liegt
unter `build/signpath-audit/github-support-request.md` und wurde laut Kontoinhaber
über GitHub Support eingereicht. Eine Ticketnummer wurde hier nicht hinterlegt.
GitHub meldete
zum Prüfzeitpunkt keine Forks. Andere alte lokale Kopien müssen vor erneutem Push
ebenfalls bereinigt oder frisch geklont werden. Keine alte Historie zurückmergen.

Der Scan ist eine Musterprüfung erreichbarer lokaler Git-Historie, keine Zusage,
dass alle vertraulichen Informationen erkannt wurden. GitHub-Artefakte,
gelöschte/unreferenzierte Objekte und Inhalte außerhalb der lokalen Historie
wurden damit nicht vollständig geprüft. Der Bericht ersetzt insbesondere keine
inhaltliche Prüfung sonstiger Dokumentation oder die noch offene GitHub-Bereinigung.

## Build- und Lizenzvorbereitung

Die beiden PyInstaller-Specs generieren Windows-Versionsressourcen aus der
Client-Paketversion. Client, Tray und Inno-Setup tragen PapaGUI als Produkt und
0.4.4 als Version; die EXEs zusätzlich ihre korrekten Originaldateinamen.
Inno Setup füllt manche Ressourcentexte mit Leerzeichen auf; bei der lokalen
Metadatenprüfung wurden diese Randzeichen entfernt.

Ein Fehler im Lizenzsammler ließ Dateien mit der britischen Schreibweise
`LICENCE` aus. Nach Korrektur enthält das Windows-Inventar 17 Lizenzdateien
für zwölf erfasste Python-/Buildpakete; keines davon hat eine leere Notice-Liste.
Ein Regressionstest sichert diese Schreibweise. Das ist weiterhin kein
vollständiges Inventar der eingebetteten nativen Bibliotheken und stellt nicht
deren erforderliche Quellarchive bereit.

## Prüfungen

Nachtrag zum GitHub-Lauf für `6dd44af`: Quality (`34859714570`) und Serverimage
(`34859714418`) sind erfolgreich. Im Clientlauf (`34859714410`) bestehen Linux
und beide macOS-Ziele; Windows scheitert ausschließlich am Test
`test_private_directory_removes_explicit_access_for_other_users`, weil der
PowerShell-Prozess das 15-Sekunden-Limit überschreitet. Ein lokaler Gegenlauf
von `tests/client/test_release_updates.py` ergibt 45 bestandene Tests. Das
belegt noch keine Ursache für die Verzögerung auf dem GitHub-Runner. Der
fehlgeschlagene Windowsjob wurde gezielt erneut gestartet und besteht in Versuch 2.
Die Prüfung der Zugriffsrechte wurde nicht abgeschwächt.

- 32 Packaging-, Lizenzsammler- und Releasepublisher-Tests bestanden.
- Ruff für die geänderten Pythondateien bestanden.
- Client und Tray nativ mit PyInstaller gebaut.
- Beide Programme bestehen die isolierten Archiv-/Startprüfungen mit Wegwerfprofil.
- Windows-Inno-Installer erfolgreich gebaut.
- Produkt-/Versionsressourcen von Client, Tray und Setup über Windows geprüft.
- Authenticode-Status aller drei Dateien: `NotSigned`, wie vor Aufnahme erwartet.

Lokale Dateien liegen unter `build/signpath-native`, `build/signpath-installers`
und `build/signpath-audit`. Sie sind keine öffentliche Releasefreigabe. Der
Testinstaller darf nicht als SignPath-signiert beworben werden.

## Fortschreibung für 0.5.0

Der Inhaber bestätigt den eigenen Code als eigene beziehungsweise Codex-gestützte
Entwicklung. Die drei Paketversionen, OpenAPI-Metadaten, Deployment-Tags und
Windows-Versionsressourcen stehen auf 0.5.0; die Kompatibilitätsbasis bleibt 0.4.3.

Die Prüfung gegen 0.4.3 besteht in beiden Richtungen über echtes HTTP. Client
und Tray wurden unter Windows neu gebaut und isoliert gestartet; der neue
Installer besteht Installation, Upgrade, Start und Deinstallation im
Wegwerfverzeichnis. Qt/PySide-Quellarchive (1.035.686.512 Bytes) sind gegen die
offiziellen SHA-256-Werte geprüft. Daraus wurden 3.211 Hinweise mit Herkunftspfad
übernommen. Das native Windows-Inventar erfasst 243 Dateieinträge beider Produkte.

Der lokale Export aus dem separaten Server-Testimage erfasst 183 installierte
Debian-Pakete einschließlich genauer Quellversionen. Aus aktuellen Paketlisten
entfernte Versionen werden über Debian Snapshot beschafft und geprüft. Der
Releaseworkflow wiederholt den Export für amd64 und arm64 aus seinem Image-Digest.
Die Publisher verlangen beide Quellpakete und die Qt/PySide-Archive vor Upload.

Die lokalen 0.5.0-Ausgaben liegen unter `build/native-050`, `build/installers-050`
und `build/server-sources-050`. Ein neuer vollständiger GitHub-Lauf muss den
veröffentlichten Commit prüfen; frühere grüne Läufe ersetzen diesen Nachweis nicht.

Der vollständige lokale 0.5.0-Lauf besteht mit 1.244 Tests und 94 % Coverage.
Einzelne Symlink-Prüfungen werden mangels Windows-Berechtigung übersprungen.
Der erste Sandbox-Lauf zeigte erwartete Zugriffsfehler nach ACL-Härtung sowie
zwei korrigierte Versionsannahmen. Der bestätigte Lauf erfolgte mit synthetischen
Testdaten außerhalb der Sandbox. Ruff, OpenAPI, Release-Metadaten und Actionlint
bestehen. Die finalen Lizenzpakete ergänzen den Mesa/llvmpipe-Hinweis.
