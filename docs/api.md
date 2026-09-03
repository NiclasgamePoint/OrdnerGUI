# HTTP-API

Die kanonische Schnittstelle ist `/v2`. Paketversion, API-Major und
Generationsschema werden unabhängig versioniert. Das von FastAPI erzeugte
OpenAPI-Dokument ist die maschinenlesbare Referenz; CI prüft es gegen den
getrackten Snapshot.

## Authentifizierung

- `GET /health` ist für Container-Healthchecks ohne Token erreichbar. Der
  Endpunkt bleibt als Liveness-Signal mit HTTP 200 erreichbar, meldet im
  Payload aber `degraded`, wenn Quelle oder Serverzustand nicht bereit sind.
- Lesezugriffe und normale Kundenänderungen benötigen
  `Authorization: Bearer <client-token>`.
- Das Annehmen oder Ablehnen eines Dokumentvorschlags ist eine normale,
  revisionierte Kundenänderung und benötigt nur den Client-Token.
- Einstellungen, Indexwartung, Erkennungsläufe und Entscheidungen über
  mehrdeutige Erkennungsfälle benötigen ebenfalls nur den Client-Token. Ein
  zusätzliches Kennwort oder eine Adminsitzung existiert nicht.
- Im NAS-Betrieb soll `PAPAGUI_API_TOKEN_FILE` auf die geschützte Tokendatei im
  Configvolume zeigen. Nicht-leere direkte Env-Werte haben Vorrang. Eine explizit konfigurierte,
  unlesbare oder leere Secretdatei bricht den Start sicher ab.

Im Synology-Betrieb muss die API hinter einem HTTPS-Reverse-Proxy liegen.

## Endpunktgruppen

| Endpunkt | Zweck |
| --- | --- |
| `GET /health` | Prozess-Healthcheck |
| `GET /v2/system/info` | Serverversion, Fähigkeiten und unterstützte Schemas |
| `GET /v2/server/status` | Server-, Indexjob- und persistenter Retentionstatus |
| `GET /v2/generations/current` | Aktuelle Komponenten und Prüfsummen |
| `GET /v2/generations/{component}/{generation}/manifest` | Metadaten einer unveränderlichen Komponente |
| `GET /v2/generations/{component}/{generation}/archive` | Download einer unveränderlichen Komponente |
| `GET /v2/catalog/search` | Portable Dateisuche mit Quelle, Facetten, Sortierung und Pagination |
| `GET /v2/catalog/facets` | Domänen-, Jahres- und Dateitypfacetten einer Quelle |
| `GET /v2/catalog/folders[/{relative_path}]` | Normalisierte Ordnerliste beziehungsweise Detailbaum |
| `GET /v2/catalog/project-roots[/{id}]` | Erkannte portable Projektwurzeln |
| `GET/POST /v2/customers` | Kunden lesen beziehungsweise anlegen |
| `GET/PUT/DELETE /v2/customers/{id}` | Einzelnen Kunden lesen oder revisioniert ändern |
| `POST /v2/customer-mutations` | DTO-basierte, wiederholbare Kunden-/Journalmutation |
| `GET/POST /v2/customers/{id}/journal` | Journal lesen beziehungsweise Eintrag anlegen |
| `PUT/DELETE /v2/customers/{id}/journal/{entry_id}` | Journaleintrag revisioniert ändern |
| `GET /v2/customers/{id}/suggestions` | Persistente Dokumentvorschläge samt Provenienz lesen |
| `POST /v2/customers/{id}/suggestions/{suggestion_id}/decision` | Vorschlag revisioniert annehmen oder idempotent ablehnen |
| `GET /v2/recognition/cases` | Persistente mehrdeutige Erkennungsfälle lesen |
| `GET /v2/recognition/runs` | Erkennungslaufhistorie lesen |
| `POST /v2/admin/recognition/runs` | Erkennungslauf manuell starten |
| `POST /v2/admin/recognition/cases/{signature}/decision` | Fall annehmen, zuweisen oder ablehnen |
| `GET/PUT /v2/admin/settings` | Serverseitige Indexeinstellungen |
| `POST /v2/admin/index-runs` | Indexlauf starten; `full_rebuild` ist optional |
| `POST /v2/admin/index-runs/current/cancel` | Lauf abbrechen |
| `DELETE /v2/admin/index` | Index löschen, optional ohne direkten Neuaufbau |
| `POST /v2/admin/server/restart` | Kontrollierter Containerneustart |

## Optimistische Änderungen

Eine Mutation enthält `expected_revision` und einen eindeutigen
`idempotency_key`. Der Server verarbeitet denselben Schlüssel höchstens einmal.
Ist die Basisrevision veraltet, antwortet er mit `409 Conflict` und dem
aktuellen Datensatz. Der Client kann anschließend neu laden, Felder bewusst
zusammenführen und mit einer neuen Mutation speichern.

Für Erkennungsentscheidungen gelten dieselben Regeln: `accept` erzeugt einen
neuen Kunden und verlangt `expected_revision: 0` sowie einen Idempotency-Key;
`assign` verlangt Zielkunde, dessen gelesene Revision und einen Key. Ein
Revisionskonflikt liefert `409` mit `current`. Reine `reject`-Entscheidungen sind
ohne Stammdatenänderung wiederholbar. Vorschlags-`accept` verlangt Revision und
Key; `reject` ist ebenfalls idempotent. Manuell gepflegte Felder werden nie
überschrieben.

Alle v2 Request-/Responseformen, Enums sowie 400/401/404/409/422-Fehler sind
als Pydantic-Schemas im OpenAPI-Dokument referenziert. Der getrackte Snapshot
verhindert unbemerkte Wire-Drift.

## Kompatibilität

Der 0.4.2-Server stellt einen begrenzten Übergangsadapter bereit:

- `GET /v1/server/status` und `GET /v1/server/settings`
- token-geschütztes `PUT /v1/server/settings` und `POST /v1/server/actions`
- `GET /v1/index/current` und `GET /v1/index/generations/{archive_name}`
- `GET /v1/customers/{id}`, `POST /v1/customers` sowie
  `PUT/DELETE /v1/customers/{id}`

Es gibt dort keine Kundenliste und keine v1-Journalendpunkte. Das über die
v1-Indexroute des 0.4.2-Servers ausgelieferte Archiv enthält nur die neue
Indexkomponente, nicht die historische kombinierte Index-/Kundengeneration.
Damit ist der Adapter keine Garantie, einen vollständigen 0.4.1-Client gegen
einen 0.4.2-Server weiterzubetreiben. Umgekehrt kann der 0.4.2-Client einen
echten kombinierten v1-Download eines alten Servers übernehmen. Unbekannte
Generationsschemas werden beim Einlesen des Manifests abgewiesen; der bisherige
lokale Stand bleibt aktiv.
