# Architektur

## Systemgrenze

PapaGUI ist ein Monorepo mit drei installierbaren Python-Paketen und zwei
auslieferbaren Produkten:

```text
                                     ┌────────────────────┐
read-only Quelldaten ───────────────► │  papagui-server   │
                                     │ Index + customers │
                                     └─────────┬──────────┘
                                               │ HTTPS/API v2
                                               ▼
                                     ┌────────────────────┐
                                     │  papagui-client   │
                                     │ Cache + Outbox    │
                                     └────────────────────┘

          papagui-contracts wird von Server und Client importiert.
```

Der Server ist alleiniger Writer für Indexgenerationen und `customers.db`.
Der Client besitzt nur lokale, unveränderliche Snapshots sowie eine separate
Offline-Outbox. Ein Client kann keinen Index aufbauen.

## Paketregeln

### `papagui_contracts`

- Standardbibliothek und reine Datentypen
- API-Fähigkeiten, Status, Einstellungen, Kunden- und Generationsverträge
- keine Qt-, SQLite-, Netzwerk-, Client- oder Serverimporte

### `papagui_server`

- `domain`: servereigene Regeln und Fehler
- `application`: Use Cases und Ports
- `adapters`: Dateisystem, SQLite, Index-Engine und Konfiguration
- `api`: FastAPI-Router, Authentifizierung und v1-Kompatibilität
- `composition`: einziges produktives Verdrahtungsmodul

Der Server darf Contracts importieren, aber niemals Client oder PySide6.

### `papagui_client`

- `application`: Sync-, Such-, Customer- und Tray-Use-Cases sowie Ports
- `adapters`: HTTP, read-only SQLite, lokaler Cache, Outbox und OS-Einstellungen
- `gui`: Qt-Views ohne Infrastrukturentscheidungen
- `entrypoints`: Hauptanwendung und eigenständiges Tray
- `composition`: Verdrahtung der Clientadapter

Der Client darf Contracts importieren, aber weder Servermodule noch einen
Index-Writer oder Kundenerkennungsjob.

Die `gui`-Schicht verwendet wieder die sichtbaren v0.4.1-Komponenten
(`AppHeader`, Karten-Seiten, Einstellungs-Popup, Kunden-/Ordner-Splitter und
Indexserver-Tray). Diese Komponenten sind ausschließlich Views und lokale
Presentermodelle. Datenzugriff und Änderungen laufen weiter über
`SearchCoordinator`, `CustomerCoordinator`, `JournalCoordinator`,
`SyncCoordinator` und den `HttpServerControlGateway`; der Design-Rückbau erzeugt
keine Kompatibilitätsverbindung zum alten Monolithen.

## OOP-Leitlinien

- Views zeigen Zustand an und senden Benutzerabsichten; Coordinatoren führen
  Workflows aus.
- Application Services hängen von kleinen Ports ab, nicht von konkreten
  Datenbank-, HTTP- oder Qt-Klassen.
- SQLite-Transaktionen werden über Unit-of-Work-Grenzen abgeschlossen.
- Generations- und Customer-Snapshots sind immutable. Pending-Änderungen liegen
  für Kundendaten in der Outbox. Noch unbestätigte Änderungen der serverweiten
  Sperrliste hält das Tray dagegen nur im Arbeitsspeicher; sie gehören nicht zur
  Kunden-Outbox.
- Composition Roots sind die einzigen Stellen, die konkrete Adapter erzeugen.
- Vererbung dient nicht zur Wiederverwendung konkreter Repositories;
  Offlineverhalten wird durch Komposition aufgebaut.

## Prozesse

- Der Servercontainer besitzt API, Scheduler und Indexverarbeitung. Die
  Dokumentauslesung für Inhaltsindex und Kundenerkennung nutzt denselben
  begrenzten Threadpool und Extraktionscache. Fachliche Kundenzuordnung und
  SQLite-Schreibvorgänge laufen anschließend seriell.
- Indexläufe und erzwungene Dokumentneuauslesung teilen eine Operationssperre.
  Sperrlistenänderungen benötigen diese lang gehaltene Sperre nicht: Ihre kurze
  SQLite-Transaktion wird mit den übrigen Schreibern serialisiert.
- Hauptfenster und Indexserver-Tray sind getrennte Clientprozesse. Das Tray
  funktioniert ohne Hauptfenster und verwendet denselben Client-Bearer-Token;
  Adminpasswort und gesonderte Adminsitzungen gibt es nicht mehr.
- `start.sh`, `start.command` und `start.bat`/`start.ps1` koordinieren den lokalen
  Entwicklungsstart. Ein Client kann auch einen bereits laufenden entfernten
  Server verwenden und benötigt dann kein Docker.

## Dokumentauslesung und Veröffentlichung

Ein regulärer Folgeabgleich prüft Änderungszeit, Dateigröße und Auslesepolicy.
Unveränderte Dokumente werden dabei nicht erneut geöffnet. Ein fälliger täglicher
Inhaltsabgleich beziehungsweise ein ausdrücklicher Indexneuaufbau prüft zusätzlich
Inhaltshashes, kann aber einen weiterhin gültigen Extraktionscache verwenden.
**Neu auslesen** und der vollständige Neuaufbau der Kundenerkennung
erzwingen dagegen die Auslesung einschließlich erforderlicher OCR.

Neue Textartefakte versorgen sowohl den Inhaltsindex als auch die fachliche
Erkennung. Dokumentbelege werden zu stabilen Vorschlägen zusammengefasst und mit
Stammdaten, Entscheidungen und serverweiten Ausschlüssen abgeglichen. Erst eine
angenommene Kundenentscheidung schreibt den vorgeschlagenen Wert in die
Stammdaten.

Eine bestätigte Sperrlistenänderung gleicht vorhandene Vorschläge ab und
veröffentlicht die Kundenkomponente einmal pro Batch; sie startet keinen
Indexneuaufbau und keine OCR. Schlägt die Veröffentlichung nach dem Datenbank-
Commit fehl, bleibt ein dauerhafter Marker für eine spätere Wiederholung erhalten.
Die Clients verwenden bis dahin ihren letzten gültigen Snapshot. Details stehen
in [API](api.md), [Datenmodell](data-and-migrations.md) und
[Kundenerkennung](kundendatenerkennung.md).

## Entscheidungen

- [Paketgrenzen](adr/0001-package-boundaries.md)
- [Generationen und Offline-Outbox](adr/0002-generations-and-outbox.md)
- [Logische Quellpfade](adr/0003-source-paths.md)
- [Unabhängige Versionierung](adr/0004-versioning.md)
