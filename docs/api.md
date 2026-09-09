# HTTP-API

Die kanonische Schnittstelle ist `/v2`. Paketversion, API-Major und
Generationsschema werden unabhängig versioniert. Das von FastAPI erzeugte
OpenAPI-Dokument ist die maschinenlesbare Referenz; CI prüft es gegen den
getrackten Snapshot unter `tests/server/openapi-v2.json`. Der laufende Server
liefert `/openapi.json` und die interaktive Referenz unter `/docs`.

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
| `GET /v2/server/status` | Server-, Indexjob-, Retentionstatus und optionale Dokumentworkerstatistik |
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
| `GET /v2/customers/{id}/recognition-status` | Abdeckung, Prüfgründe, Herkunft und letzter Kundenauftrag |
| `POST /v2/customers/{id}/recognition` | Dauerhaften Kundenauftrag mit `mode: reassess` oder `extract` starten; HTTP 202 |
| `DELETE /v2/customers/{id}/recognition/{job_id}` | Kundenauftrag abbrechen |
| `GET /v2/recognition/cases` | Persistente mehrdeutige Erkennungsfälle lesen |
| `GET /v2/recognition/runs` | Erkennungslaufhistorie lesen |
| `POST /v2/admin/recognition/runs` | Erkennung synchron unter der gemeinsamen Schreibsperre ausführen |
| `POST /v2/admin/recognition/cases/{signature}/decision` | Fall annehmen, zuweisen oder ablehnen |
| `GET /v2/admin/recognition/blocklist` | Serverweite Sperren und `publication_pending` lesen |
| `POST /v2/admin/recognition/blocklist` | Einzelne Sperre hinzufügen; kompatible bisherige Schnittstelle |
| `DELETE /v2/admin/recognition/blocklist/{id}` | Einzelne Sperre entfernen; unbekannte ID ergibt HTTP 404 |
| `POST /v2/admin/recognition/blocklist/batch` | Ergänzungen und Entfernungen atomar gemeinsam anwenden |
| `GET /v2/admin/recognition/rebuild` | Letzten vollständigen Erkennungsauftrag lesen; `job` ist ohne Auftrag `null` |
| `POST /v2/admin/recognition/rebuild` | Vollständigen Erkennungsneuaufbau samt erneuter Dokumentauslesung starten; HTTP 202 |
| `DELETE /v2/admin/recognition/rebuild/{job_id}` | Vollständigen Erkennungsauftrag abbrechen |
| `GET/PUT /v2/admin/settings` | Serverseitige Indexeinstellungen |
| `POST /v2/admin/index-runs` | Indexlauf starten; `full_rebuild` ist optional |
| `POST /v2/admin/index-runs/current/cancel` | Lauf abbrechen |
| `DELETE /v2/admin/index` | Index löschen, optional ohne direkten Neuaufbau |
| `POST /v2/admin/server/restart` | Kontrollierter Containerneustart |

## Optimistische Änderungen

Eine DTO-basierte Kunden-/Journalmutation enthält `expected_revision` und einen eindeutigen
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

## Atomare Sperrlistenänderungen

Die Fähigkeit `recognition-blocklist-batch` kennzeichnet den Stapelendpunkt.
Beispiel mit ausschließlich erfundenen Werten:

```json
{
  "additions": [
    {"kind": "email_domain", "value": "example.org", "reason": "Synthetisches Beispiel"},
    {"kind": "company", "value": "Synthetische Beispiel GmbH"}
  ],
  "deletions": [42]
}
```

`additions` und `deletions` sind optional, standardmäßig leer und auf jeweils
500 Einträge begrenzt. `kind` ist `email`, `phone`, `email_domain`,
`contact_name` oder `company`. `value` darf 1–320 Zeichen, die optionale
Begründung `reason` höchstens 500 Zeichen enthalten. Lösch-IDs sind positive
Ganzzahlen bis `9223372036854775807`; Booleans und numerische Zeichenketten
werden nicht als IDs akzeptiert.

Alle Werte werden vor der ersten Mutation validiert. Strukturelle Fehler
ergeben HTTP 422, ungültige fachliche Werte HTTP 400 mit der Position
`Sperre N` im Stapel. Es wird dann kein Teil der Änderung gespeichert.
Der Server entfernt zunächst die angegebenen IDs und fügt anschließend die
normalisierten Werte hinzu. Doppelte Ergänzungen werden zusammengefasst;
ein bereits bestehender normalisierter Wert behält ID, Wert und Begründung.
Eine Ergänzung desselben gleichzeitig gelöschten Werts erzeugt einen neuen
Eintrag. Einmal vergebene IDs werden nicht wiederverwendet. Unbekannte
Lösch-IDs sind hier unschädlich, damit Wiederholungen und veraltete
Löschvormerkungen keine später hinzugefügten Sperren treffen.

Die Anfrage ist eine Änderungsliste, keine Ersetzung des Gesamtbestands.
Andere gleichzeitig gespeicherte Einträge bleiben erhalten. Der Server
speichert den Stapel in einer SQLite-Transaktion, gleicht betroffene
Vorschläge einmal ab und veröffentlicht danach einmal die Kundenkomponente.
Indexierung, OCR und Erkennungsneuaufbau werden dabei nicht gestartet.
Sperrlistenänderungen sind auch während laufender Index- und Kundenaufträge
möglich; sie warten gegebenenfalls auf kurze SQLite-Schreibtransaktionen.

HTTP 200 liefert `entries` als vollständige Liste sowie `changed` und
`published`. `changed: false` bedeutet, dass keine Sperre geändert wurde.
Ist keine Veröffentlichung offen, bedeutet `published: true` auch bei einem
unveränderten Stapel, dass keine weitere Arbeit nötig ist; es entsteht dann
keine neue Generation.

Scheitert die Veröffentlichung nach erfolgreichem Speichern, bleibt die
Antwort HTTP 200 mit `published: false`. Die Änderungen gelten bereits auf
dem Server; Offline-Snapshots können noch den vorherigen Stand enthalten.
Der offene Schritt wird dauerhaft gespeichert und ist nach einem Neustart
über `GET /v2/admin/recognition/blocklist` als `publication_pending: true`
sichtbar. `POST /v2/admin/recognition/blocklist/batch` mit `{}` oder zwei
leeren Listen wiederholt nur die offene Veröffentlichung. Nach Erfolg
lautet die Antwort `changed: false, published: true`. Ein neuer Client soll
für diesen Ablauf die Stapelfähigkeit prüfen und nicht auf mehrere einzelne
Schreibanfragen zurückfallen.

## Dokumentprüfung, Aufträge und Workerstatus

Vorschlagslisten unterstützen `status`, `limit`, `offset` und
`include_groups`. Die Antwort enthält unter anderem `revision`, `total`,
`has_more` und `recognition`. `include_groups=true` schaltet neue Adressgruppen
ausdrücklich frei; ältere Clients erhalten diese nicht ungefragt.

`reassess` bewertet vorhandene Texte neu. `extract` liest Dokumente der
zugeordneten Projekte erneut und bewertet sie anschließend. Der globale
`rebuild` erstellt den Dokumentkatalog neu, erzwingt die Auslesung und prüft
alle Kunden ohne das gewöhnliche Suchbudget je Projekt. Eingestellte
Dateitypen, Ausschlüsse und Schutzgrenzen je Dokument gelten weiter.
Der globale Auftrag setzt `recognition_pipeline_enabled=true` und ein
positives `priority_documents_per_project` voraus; andernfalls ergibt die
Anforderung HTTP 400.
Ein normaler Indexlauf mit `full_rebuild: true` baut ebenfalls den Katalog
neu und prüft Inhaltshashes, darf aber passende Extraktionscacheeinträge
einschließlich OCR wiederverwenden.

Gleiche wartende oder laufende Kunden-/Neuaufbauaufträge werden zusammengefasst.
Die gemeinsame Warteschlange ist auf 100 aktive Aufträge begrenzt. Aufträge
überleben einen Serverneustart; unterbrochene Aufträge werden wieder
eingereiht, ausdrücklich abgebrochene bleiben abgebrochen. Neue Aufträge und
ihre Zustände werden unter `job` zurückgegeben. Die Zustände sind `queued`,
`running`, `completed`, `error` und `cancelled`.

Die Fähigkeit `document-workers` kennzeichnet optionale `document_workers`
in `/v2/server/status` und während der Auslesung laufender `extract`-/`rebuild`-
Aufträge. Das Objekt enthält `state`, `worker_limit`, `active_workers`,
`queued_documents`, `discovered_documents`, `processed_documents`,
`reused_documents`, `extracted_documents`, `failed_documents`,
`read_documents`, `verify_content` und `elapsed_seconds`. Es berichtet
Summen, keine Dokumentpfade oder Kundenwerte. Workerzahlen werden pro Lauf
aus Ressourcenprofil, CPU-/Containergrenzen und verfügbarem RAM bestimmt;
sie sind kein separat einstellbarer API-Parameter. Wartende Aufträge
übernehmen keine Statistiken eines anderen Laufs.

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
