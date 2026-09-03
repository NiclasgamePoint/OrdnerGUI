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
`SyncCoordinator` und den `ServerControlGateway`; der Design-Rückbau erzeugt
keine Kompatibilitätsverbindung zum alten Monolithen.

## OOP-Leitlinien

- Views zeigen Zustand an und senden Benutzerabsichten; Coordinatoren führen
  Workflows aus.
- Application Services hängen von kleinen Ports ab, nicht von konkreten
  Datenbank-, HTTP- oder Qt-Klassen.
- SQLite-Transaktionen werden über Unit-of-Work-Grenzen abgeschlossen.
- Generations- und Customer-Snapshots sind immutable. Pending-Änderungen liegen
  ausschließlich in der Outbox.
- Composition Roots sind die einzigen Stellen, die konkrete Adapter erzeugen.
- Vererbung dient nicht zur Wiederverwendung konkreter Repositories;
  Offlineverhalten wird durch Komposition aufgebaut.

## Prozesse

- Der Servercontainer besitzt API, Scheduler und Indexworker.
- Hauptfenster und Admin-Tray sind getrennte Clientprozesse.
- Das Tray kann ohne Hauptfenster laufen und speichert den Adminsitzungstoken
  nur im Prozessspeicher.
- `start.sh` ist ausschließlich ein Entwicklungs-Orchestrator und keine
  Produktkopplung.

## Entscheidungen

- [Paketgrenzen](adr/0001-package-boundaries.md)
- [Generationen und Offline-Outbox](adr/0002-generations-and-outbox.md)
- [Logische Quellpfade](adr/0003-source-paths.md)
- [Unabhängige Versionierung](adr/0004-versioning.md)
