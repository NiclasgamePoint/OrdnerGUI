# Kundendatenerkennung: Implementierung und Betrieb

Stand: 9. September 2026. Die neue Pipeline arbeitet vollständig lokal auf dem Server. Die Implementierung und ihre Tests verwenden keine externen Sprachmodelle oder Kundendaten im Entwicklungskontext. Die vorhandenen Datenbanken und Kundendokumente wurden für diese Umsetzung nicht geöffnet oder produktiv umgestellt. Fachliche Prozentziele auf realen Unterlagen sind damit noch keine gemessenen Ergebnisse.

## Verhalten

Die Erkennung trennt Dokumentauslesung, Wertprüfung, Parteizuordnung und menschliche Entscheidung. Aus einem Ordnernamen entstehen weiterhin Projektzuordnungen beziehungsweise prüfbare Zuordnungsfälle. Der Projektort wird bei einer Kundenneuanlage nicht mehr als Kundenanschrift gespeichert. Rechtsformen haben bei der Unterscheidung Unternehmen/Person Vorrang vor enthaltenen Vornamen. Bestätigte Projektbesitzer und eindeutige Namen bleiben auch bei verschiedenen Projektorten erhalten.

Die Dokumenterkennung unterstützt folgende Informationen:

| Information | Prüfung und Übernahme |
|---|---|
| Telefon | `phonenumbers` prüft Nummernbereiche und normalisiert internationale Schreibweisen einschließlich Durchwahl. DE ist nur die Vorgabe für Nummern ohne Länderpräfix. Rechnungsnummern, Datumswerte, IBAN, Mengen und Faxangaben werden nicht als Telefon übernommen. Formale Gültigkeit beweist weder Inhaber noch Erreichbarkeit. |
| E-Mail | `email-validator` prüft lokal die Schreibweise; DNS- und Zustellprüfung sind abgeschaltet. Unterschiedliche Postfächer und Plus-Anhänge bleiben verschieden. |
| Firma | Rechtsform und Firmenzeile im Kundenblock; keine automatische Umbenennung eines vorhandenen Kunden. |
| Anschrift | Zusammenhängender Block aus Straße, PLZ und Ort. Projekt-/Baustellenadresse wird ausgeschlossen. Postfach, ausländische Formate und unvollständige Blöcke erhalten Prüfgründe. Unvollständige Anschriften müssen manuell ergänzt werden. |
| Ansprechpartner | Name, Rolle, Telefon und E-Mail aus demselben Abschnitt oder einer Tabellenzeile. Kontakte besitzen dauerhafte IDs. Ergänzungen an einem eindeutig vorhandenen Kontakt erhalten diese ID; widersprüchliche Angaben erfordern manuelle Klärung. |

Eigene Firma, Lieferanten, Behörden und Projektadressen werden von Kundenangaben unterschieden. Eindeutiger Kundenname oder belastbare Kundenrolle im passenden Block ergeben die Qualitätsstufe `strong`. Unklare Zuordnungen bleiben `review`. Niedrige beziehungsweise fehlende OCR-Qualität oder fehlende OCR-Positionen verhindern eine Einstufung als gut belegt. Die Qualitätsstufen sind **keine kalibrierten Wahrscheinlichkeiten**; feste Prozentanzeigen wurden entfernt.

## Dokumente auslesen und auswählen

- DOCX: zusammenhängende Runs, Absätze, Tabellen, Kopf- und Fußzeilen. Textboxen und eingebettete Objekte werden als Grenzen ausgewiesen.
- XLS/XLSX: tatsächliche Zellwerte, Blatt/Zelle, gemeinsame Tabellenzeilen und führende Nullen aus Zahlenformaten. Formeln werden nicht ausgeführt; fehlende gespeicherte Ergebnisse werden gekennzeichnet.
- PDF: native Wörter und Positionen mit `pdfplumber`, getrennte Spalten und Absätze. Nur Seiten mit unzureichendem Text werden zusätzlich mit Tesseract gelesen. Ein lesbarer Anfang verdeckt keine späteren Scanseiten.
- Bilder: kostengünstige Prüfung auf Dokumentcharakter, danach OCR mit Wortpositionen, Qualitätswerten und Rotationserkennung. Nicht als Dokument erkannte Bilder bleiben in der Abdeckungsstatistik erkennbar. Eine Prüfung echter ausgeschlossener Bilder ist Bestandteil der späteren fachlichen Abnahme.
- DOC und einfachere Textformate behalten ihre lokalen Auslesepfade. EML/RTF und eine Postfachanbindung gehören nicht zum Kernumfang.

Dateiname, Inhalt, Dokumenttyp und Extraktionsqualität beeinflussen die Auswahl. Pro Projekt werden zunächst standardmäßig 24 Dokumente geprüft. Solange wichtige Felder fehlen, folgen weitere begrenzte Batches bis zum konfigurierten Höchstwert. Nicht erreichte Dokumente und Seiten bleiben als unvollständige Abdeckung sichtbar. Die Aufzählung der Kunden hat keine frühere Gesamtgrenze von 2.000 Einträgen mehr.

Die Statuswerte unterscheiden `ok`, `partial`, `no_text`, `unsupported`, `too_large`, `encrypted`, `tool_missing`, `timeout` und `error`. Erkannte Textteile bleiben bei einer teilweise gelesenen Datei verwertbar. Ein technischer Fehler bedeutet nicht, dass das Dokument keine Kundendaten enthält.

## Vorschläge, Belege und Entscheidungen

Ein normalisierter Wert beziehungsweise Kontakt-/Adressblock bildet zusammen mit Kunde und Zielpartei eine dauerhafte Entscheidung. Dateipfad und Erkennerversion sind kein Teil dieses Schlüssels. Die Belege werden separat mit Quelle, Textauszug, Seite/Koordinaten oder Blatt/Zelle gespeichert. Identische Binärdateien und exakt gleiche normalisierte Texte verschiedener Exporte zählen nicht als unabhängige Bestätigungen. Eine unscharfe Gleichsetzung ähnlicher Briefvorlagen wird nicht behauptet.

Ein bereits vorhandener gleichwertiger Stammdateneintrag erhält keine offene Karte. Ein abweichender Eintrag wird als Konflikt angezeigt. Annahme prüft die aktuelle Kundenrevision und schützt alle bereits gefüllten Zielfelder; Adressgruppen werden atomar übernommen. Ansprechpartner werden nur bei eindeutiger Identität ergänzt. Der Client übernimmt die neue Revision unmittelbar nach jeder Entscheidung.

Ablehnungsgründe sind `not_a_value`, `wrong_customer`, `outdated`, `already_present` und `other`; die Angabe ist optional. Eine Entscheidung gilt für den Kunden und die normalisierte Information, auch nach Kopieren, Umbenennen oder neuer Parteienauflösung. Verschiedene Ansprechpartner bleiben durch ihren vollständigen Kontaktblock getrennte Zielinformationen. Eine Ablehnung wird nicht auf einen anderen Kunden übertragen.

Gelöschte beziehungsweise umgehängte Quellen verlieren ihre aktive Evidenz. Definitiv leer gelesene Quellen entkräften frühere offene Vorschläge; temporäre Parserfehler löschen keinen bestätigten Wert. Eine offene Information ohne verbleibende Belege wird `stale`. Historische Entscheidungen bleiben erhalten. Die Qualität eines Vorschlags folgt den **aktiven** Belegen und wird bei Wegfall des starken Belegs wieder herabgestuft.

Die Herkunft der Stammdaten liegt in `customer_field_provenance`: `manual`, `confirmed`, `folder` oder `unknown`. Bestehende Werte werden nicht nachträglich als bestätigt ausgegeben. Für einen bisherigen Kundenort ohne bestätigten Adressbeleg erscheint ein Hinweis. Er wird weder gelöscht noch durch einen Projektort ersetzt.

## Oberfläche und Schnittstellen

Die serverweite Sperrliste im Indexserver-Tab **Kundenerkennung** unterstützt
gesammelte Änderungen. Neue Sperren und Entfernungen bleiben bis zu
**Änderungen bestätigen** lokale Vormerkungen. Der Server validiert den ganzen
Vorgang, speichert ihn atomar, gleicht vorhandene Vorschläge einmal ab und
veröffentlicht einmal den Kundenstand. Gespeicherte Stammdaten und bisherige
Entscheidungen bleiben erhalten; Dokumentauslesung, OCR und ein vollständiger
Neuaufbau werden dabei nicht gestartet.

Die additive Fähigkeit `recognition-blocklist-batch` kennzeichnet
`POST /v2/admin/recognition/blocklist/batch`. Die Anfrage enthält
`additions: [{kind, value, reason}]` und `deletions: [id]`, jeweils höchstens
500 Einträge. Sie übermittelt Änderungen, keine vollständige Ersetzung der
Sperrliste, damit parallele Bearbeitungen erhalten bleiben. Doppelte Werte
werden normalisiert zusammengefasst; bereits entfernte IDs sind bei einem
erneuten Versuch unschädlich. Einmal vergebene IDs werden nicht erneut
verwendet, sodass alte Löschvormerkungen keine neuen Sperren treffen.
Löschungen werden vor Ergänzungen angewendet. Bestehende normalisierte
Einträge behalten ihre ID und Begründung; eine gleichzeitig gelöschte und
erneut ergänzte Sperre erhält eine neue ID. Bestehende Einzel-Endpunkte
bleiben verfügbar. Werte, Längen und Fehlerantworten stehen in
[der API-Referenz](api.md#atomare-sperrlistenänderungen).

Die Antwort enthält `entries`, `changed` und `published`. Wenn die Speicherung
erfolgreich war, aber die Veröffentlichung scheitert, meldet der Server
`published: false` und merkt den offenen Schritt dauerhaft vor. Ein leerer
Stapel kann dann ausschließlich die Veröffentlichung wiederholen. Ein
unveränderter Stapel ohne offene Veröffentlichung verursacht keine neue
Veröffentlichung.
`GET /v2/admin/recognition/blocklist` liefert zusätzlich `publication_pending`,
damit eine ausstehende Veröffentlichung nach einem Clientneustart sichtbar bleibt.

Die Kundenprüfung zeigt Ergänzungen, Konflikte und Ansprechpartner mit zunächst drei Alternativen je Feld und 30 Entscheidungen je Seite. Weitere bleiben erreichbar. Karten nennen Qualitätsstufe, Rolle, Gründe und anklickbare Fundstellen. Der Zähler zählt offene Entscheidungen. Fehler, fehlende Quellen, noch nicht ausgewertete Quellen und ausbleibende neue Angaben sind verschiedene Zustände.

Zwei Aktionen starten dauerhafte Serverjobs:

- **Texte erneut bewerten** (`reassess`) verwendet bereits extrahierte Texte.
- **Dokumente neu lesen** (`extract`) erzwingt die Auslesung für die zugeordneten Projekte und bewertet sie anschließend neu.

Gleiche wartende oder laufende Aufträge werden zusammengefasst. Die gemeinsame
Warteschlange nimmt höchstens 100 aktive Aufträge auf. Wartende und
unterbrochene Aufträge bleiben über einen Neustart erhalten. Ein Abbruch
stoppt weitere Dokumentaufgaben und überwachte Parser-/OCR-Prozesse; auf
POSIX werden deren Prozessgruppen beendet. Dateileseschleifen prüfen den
Abbruch an Verarbeitungsgrenzen, zusätzliche Zeitlimits bleiben wirksam.
Offline sind gespeicherte Kurzbelege lesbar; Schreibaktionen erfordern die
Serververbindung.

Die `/v2`-Erweiterungen sind additiv:

| Route | Bedeutung |
|---|---|
| `GET /v2/customers/{id}/suggestions?status=pending&limit=30&offset=0&include_groups=true` | Entscheidungen plus `revision`, `total`, `has_more`, `recognition`; neue Adressgruppen sind ausdrücklich angefordert. |
| `POST /v2/customers/{id}/suggestions/{suggestion_id}/decision` | Entscheidung mit optionalem Grund; Revision/Idempotency-Key und bestehende 409-Konfliktbehandlung. |
| `GET /v2/customers/{id}/recognition-status` | Abdeckung, Gründe, Herkunft, Verarbeitungsversion und letzter Job. |
| `POST /v2/customers/{id}/recognition` | `{ "mode": "reassess" }` oder `{ "mode": "extract" }`; HTTP 202. |
| `DELETE /v2/customers/{id}/recognition/{job_id}` | Gezielter Abbruch. |

Neue Fähigkeiten heißen `customer-suggestion-groups`, `customer-recognition-status`,
`customer-recognition-jobs`, `stable-contact-ids`, `document-workers`,
`recognition-blocklist`, `recognition-blocklist-batch` und `recognition-rebuild`.
Ältere Clients erhalten ohne `include_groups` keine unbekannten Adressgruppen.
Der neue Client kann ältere, unpaginierte Antworten lokal aufteilen. Kontakte
ohne ID werden bei alten Client-Schreibzugriffen konservativ eindeutig
zugeordnet; Mehrdeutigkeiten werden abgelehnt.

`GET /v2/server/status` liefert optional `document_workers` mit aktiven Workern,
Workerlimit, Warteschlange, Dokumentzählern, `verify_content` und Laufzeit.
Lese-, Wiederverwendungs- und Extraktionszähler sind getrennt: eine
Hashprüfung kann eine Datei lesen und anschließend ihr Cacheergebnis nutzen.
Laufende Aufträge der Modi `extract` und `rebuild` führen dieselben Summen während ihrer Auslesung
im Jobstatus. Wartende Aufträge übernehmen keine Zahlen eines anderen Laufs.
Die Dokumentkarte und der Tab **Kundenerkennung** zeigen diese Werte live;
bei älteren Servern bleibt der bisherige Jobstatus verfügbar. Die neuen
Workerstatistiken enthalten weder Kundendetails noch Dokumentpfade.

## Speicherung, Konsistenz und Ressourcen

Die bestehende Vorschlagstabelle bleibt als kompatible kanonische Tabelle und Auditspur erhalten. Hinzu kommen `candidate_aliases`, `candidate_evidence`, `candidate_decisions`, `customer_field_provenance` und `customer_recognition_status`. `contacts.uid` erhält eine stabile ID. `candidate_schema_metadata` kennzeichnet die idempotente Umstellung.

Dieselbe Metadatentabelle hält die höchste vergebene Sperrlisten-ID und
das Token einer offenen Veröffentlichung. Das Token wird nach Erfolg nur
gelöscht, wenn es nicht inzwischen durch eine neuere Änderung ersetzt wurde.
So bleibt eine gleichzeitig fehlgeschlagene spätere Veröffentlichung
wiederholbar.

Große Layout-/OCR-Artefakte liegen ausschließlich unter `data/extraction/artifacts.db`, außerhalb der veröffentlichten Index- und Kundensnapshots. Der Kundensnapshot enthält kompakte Entscheidungen und Kurzbelege. Ein unveränderlicher Ergebnis-Hash hält frühere Extraktionsergebnisse auch nach einem Wiederholungsversuch referenzierbar.

Indexierung und einzelne Kundenjobs teilen eine Schreibkoordination. Die Dokumentbewertung verwendet eine konsistente Katalogkopie. Vor jeder kurzen Ergebnistransaktion werden Kundenrevision und Projektbesitzer erneut geprüft. Ein zwischenzeitlich bearbeiteter Kunde ergibt Teilabdeckung; seine neueren Werte werden nicht überschrieben. Die Veröffentlichung erfolgt nach dem zusammengehörigen Verarbeitungslauf.

Dokumente werden für Indexierung und Kundenerkennung über dieselbe begrenzte
parallele Auslesung vorbereitet. Die fachliche Wertprüfung und Zuordnung zu
Kunden erfolgt anschließend weiterhin nacheinander. Höchstens doppelt so
viele Dokumentaufgaben wie Worker bleiben gleichzeitig ausstehend;
ein einzelner Schreiber übernimmt die Katalog- und Cacheergebnisse.
Identische Inhalte und Extraktionsversionen teilen ohne erzwungene Auslesung
ein Ergebnis im begrenzten Laufpuffer. Ein ausdrückliches erneutes Lesen
verarbeitet jeden ausgewählten Dokumentpfad.

Das Ressourcenprofil bestimmt die Workerzahl anhand der verfügbaren CPU-Kapazität:

| Profil | CPU-Anteil für die Workerberechnung |
|---|---:|
| Schonend (`gentle`) | 15 % |
| Ausgewogen (`balanced`) | 25 % |
| Schnell (`fast`) | 60 % |

CPU-Affinität, CPU-/RAM-Grenzen des Containers, verfügbarer RAM, eine
Speicherreserve und der Speicherbedarf je Dokument begrenzen die Auslesung auf
1 bis 20 Worker. Bei unbekanntem oder knappem RAM bleibt ein Worker. Der
Live-Status zeigt die ermittelte Obergrenze und die tatsächlich aktiven Worker.
Die Profile berechnen Parallelitätsgrenzen; sie garantieren keine feste
prozentuale CPU-Auslastung.

Der Extraktionscache verwendet Inhaltshash, Parser-/Werkzeugversionen und
relevante Einstellungen. Ein regulärer Lauf überspringt bereits verarbeitete
Dateien bei unveränderter Größe, Änderungszeit und Auslesekonfiguration ohne
erneutes Öffnen der Quelldatei. Der aktivierte tägliche Abgleich prüft nach
24 Stunden die Inhaltshashes; der Zeitpunkt der letzten erfolgreichen Prüfung
bleibt über einen Serverneustart erhalten. Ein ausdrücklicher Indexneuaufbau
prüft ebenfalls die Inhalte. Passende Cacheergebnisse werden dabei weiter
genutzt, einschließlich vorhandener OCR. `reassess` verwendet vorhandene Texte;
ein geänderter Parserfingerprint oder `extract` löst die Auslesung gezielt aus.
Ein einzelner Kundenauftrag verschiebt den Termin der vollständigen
Inhaltsprüfung nicht. Wiederholbare Fehler haben ein begrenztes Retrybudget.

Der automatische Scheduler berücksichtigt den täglichen Termin auch bei
einem normalen Intervall von 48 Stunden. Bei `--no-run-on-start` führt eine
fehlende oder überfällige Inhaltsprüfung nicht sofort zu einem Lauf, sondern
wartet bis zum kleineren Wert aus Intervall und 24 Stunden. Ein noch
bevorstehender gespeicherter Termin bleibt wirksam. Automatische Läufe und
tägliche Inhaltsprüfung lassen sich getrennt deaktivieren; manuell
angeforderte Läufe bleiben möglich. Der vollständige Erkennungsneuaufbau
erzwingt dagegen die erneute Auslesung unabhängig von vorhandenen
Parser-/OCR-Ergebnissen.

| Einstellung | Standard | Bedeutung |
|---|---:|---|
| `automatic_runs_enabled` | `true` | Automatische Indextermine aktivieren; ausdrückliche Aufträge bleiben möglich. |
| `interval_seconds` | 86.400 | Normales Indexintervall; 900–172.800 Sekunden. |
| `daily_reconciliation_enabled` | `true` | Zusätzliche vollständige Inhaltshashprüfung nach 24 Stunden. |
| `content_indexing_enabled` | `true` | Dokumentinhalte für Katalog und anschließende Erkennung auslesen. |
| `resource_profile` | `balanced` | Workerberechnung, OCR-Auflösung und Pausen über `gentle`, `balanced` oder `fast`. |
| `recognition_pipeline_enabled` | `true` | Dokumenterkennung aktivieren/deaktivieren. |
| `recognition_own_names` | leer | Kommagetrennte Namen des eigenen Büros zur Rollenprüfung. |
| `priority_documents_per_project` | 24 | Erstes Suchbudget; 0 deaktiviert die Dokumentprüfung. |
| `recognition_documents_per_project_max` | 500 | Höchstes Suchbudget je Projekt. |
| `extraction_timeout_seconds` | 90 | Zeitlimit je Dokument. |
| `extraction_memory_mb` | 768 | Linux-Speicherlimit je isoliertem Office-/PDF-Parser; 128–2048 MiB. |
| `pdf_max_pages` | 200 | Grenze nativer PDF-Seiten. |
| `image_max_pixels` | 25.000.000 | Obergrenze für Bildverarbeitung. |
| `extraction_retry_attempts` | 3 | Versuche für vorübergehende Fehler. |
| `extraction_retry_delay_seconds` | 300 | Mindestabstand zur nächsten automatischen Wiederholung. |
| `extraction_store_max_mb` | 1024 | Nutzdatenbudget des privaten Extraktionsstores. |
| `extraction_retention_days` | 30 | Aufbewahrung unreferenzierter Artefakte. |

Zusätzlich gelten die bestehenden OCR-Seiten-/Zeitbudgets, Dateigröße, Zeichenlimit und Ressourcenprofile. Office/PDF werden in getrennten, zeitlich begrenzten Prozessen gelesen; OCR verwendet begrenzte Bildgrößen und einen OpenMP-Thread. Auf Systemen ohne `resource.RLIMIT_AS` bleibt die Zeit-/Seiten-/Zeichenbegrenzung wirksam; ein hartes Speicherlimit setzt den Linux-Server voraus. Aufbewahrung schützt Referenzen aktiver und aufbewahrter Katalog-/Generationsstände. Speicherlimits werden als unvollständige Verarbeitung ausgewiesen. Die bisherige unveränderte Standardliste der Dateitypen wird beim Laden um Bildformate erweitert; eigene Listen bleiben bestehen.

## Migration, Vorschau und Rückfall

Beim nächsten Start der neuen Serverversion erstellt die vorhandene Migrationsfunktion vor Schema 3 eine konsistente Sicherung `customer-backups/customers-pre-v3-…db`. Die Schemaänderung ist additiv und transaktional. Gleiche Altvorschläge werden über Aliasbeziehungen vereinigt; jede alte Entscheidung bleibt mit ursprünglicher Vorschlags-ID vorhanden. Nicht normalisierbare historische Entscheidungen bleiben erhalten. Ungültige offene Telefon-/E-Mail-Altwerte werden `invalid`; ersetzte Dubletten werden `superseded`. Widersprüchliche Altentscheidungen werden zur Prüfung markiert und nicht automatisch wieder geöffnet.

Eine lokale Vorschau arbeitet ausschließlich auf einer temporären SQLite-Kopie:

```sh
.venv/bin/python tools/recognition_migration_preview.py --database /lokaler/pfad/customers.db
```

Der Bericht enthält nur Summen und Prüfsummen. Er enthält weder Kundenkennungen noch Namen, Werte, Pfade oder Auszüge. Der Originalbestand wird nicht verändert. Die Vorschau prüft die strukturelle Migration und den Datenerhalt; sie ist keine Messung der fachlichen Erkennungsqualität. Für die Entwicklung dieses Werkzeugs wurden ausschließlich synthetische Datenbanken benutzt.

Zur Einführung: neue Version bereitstellen, Migrationbericht/Sicherung prüfen, eine begrenzte Kundenauswahl neu bewerten, Ergebnisse lokal fachlich prüfen und anschließend den regulären Indexlauf verwenden. Volltexte müssen für eine fachliche Abnahme nicht in einen Assistenzkontext gelangen; sie bleiben in der lokalen Anwendung. Zielwerte aus dem Umsetzungsplan benötigen weiterhin einen getrennten, fachlich annotierten realen Abnahmebestand und eine Laufzeitmessung auf dem Zielgerät.

Zum Rückfall `recognition_pipeline_enabled=false` setzen. Dies beendet neue automatische Dokumentbewertungen; vorhandene Werte, Entscheidungen und Benutzeränderungen bleiben erhalten. Ein gespeicherter älterer Datenbankstand darf nicht blind über zwischenzeitliche Benutzeränderungen zurückgespielt werden. Die additive Struktur erlaubt den Weiterbetrieb vorhandener Stammdaten. Ein automatisches Rückschreiben alter Regex-Ergebnisse ist nicht vorgesehen.

## Serverweite Sperrliste und vollständiger Neuaufbau

Im Client enthält **Indexserver → Tab „Kundenerkennung“** die serverweite Verwaltung. Eingaben und Vormerkungen bleiben beim Tabwechsel erhalten; Statusabfragen laufen nur im sichtbaren Tab und werden beim erneuten Öffnen aktualisiert. Die bestätigte Sperrliste liegt dauerhaft in der Serverdatenbank. Ergänzungen und Entfernungen werden zunächst lokal gesammelt und mit **Änderungen bestätigen** gemeinsam gespeichert; **Vormerkungen verwerfen** verwirft lokale Änderungen. Die Sperren gelten für alle Kunden. Unterstützt werden E-Mail-Adresse, Telefonnummer, E-Mail-Domain, Kontaktname und Firmenname. Der Vergleich verwendet normalisierte vollständige Werte. Eine Domain sperrt genau diese Domain, keine ähnlichen Domains oder Unterdomains. Eine passende E-Mail-Adresse, Telefonnummer oder ein gesperrter Name sperrt auch einen daraus zusammengesetzten Kontaktvorschlag.

Eine bestätigte neue Sperre blendet passende offene Vorschläge sofort aus und verhindert ihr Wiedererscheinen bei späteren Läufen. Belege bleiben erhalten. Wird eine Sperre entfernt, können Vorschläge mit weiterhin aktiven Belegen wieder erscheinen. Bereits angenommene oder abgelehnte Vorschläge und vorhandene Kundendaten werden dabei nicht geändert. Die Sperrliste kann auch während laufender Index- und Neuaufbauaufträge bearbeitet werden. Änderungen werden in der Kundendatenbank gespeichert und bei weiteren Vorschlägen des laufenden Auftrags berücksichtigt. Nach erfolgreichem Speichern erscheint der Eintrag direkt in der Liste.

**Kundenerkennung vollständig neu aufbauen** startet einen dauerhaften Serverauftrag. Er erstellt den Dokumentkatalog neu, liest die zugelassenen Dokumente einschließlich aktivierter OCR erneut und bewertet die Vorschläge aller Kunden. Der Auftrag durchläuft alle verfügbaren Dokumente je Projekt, auch über das normale Suchbudget hinaus und wenn die Kundendaten bereits vollständig sind. Die eingestellten Dateitypen, Ordnerausschlüsse, OCR-Einstellungen und Schutzgrenzen je Dokument gelten weiterhin. Unlesbare oder nur teilweise lesbare Dokumente bleiben im jeweiligen Kundenstatus erkennbar.

Kunden, manuelle Daten, bestätigte Werte, Projektzuordnungen, frühere Entscheidungen und Sperren bleiben erhalten. Nicht mehr belegte offene Vorschläge werden veraltet. Vorübergehende Lesefehler allein löschen keine alten Belege. Der Auftrag läuft nach dem Schließen des Fensters weiter, zeigt seinen Status an und lässt sich abbrechen. Nach einem Serverneustart wird ein unterbrochener Auftrag erneut gestartet; bewusst abgebrochene Aufträge bleiben abgebrochen. Für den Neuaufbau muss `recognition_pipeline_enabled=true` sein und `priority_documents_per_project` einen positiven Wert besitzen; 0 deaktiviert die Dokumentprüfung.

Die Verwaltung ist außerdem über die mit dem Client-Token authentifizierte API verfügbar:

| Methode | Pfad | Funktion |
| --- | --- | --- |
| GET / POST | `/v2/admin/recognition/blocklist` | Sperren samt `publication_pending` auflisten / einzelne Sperre hinzufügen (`kind`, `value`, optional `reason`). |
| DELETE | `/v2/admin/recognition/blocklist/{id}` | Sperre entfernen. |
| POST | `/v2/admin/recognition/blocklist/batch` | Vormerkungen gemeinsam anwenden (`additions`, `deletions`); Antwort mit `entries`, `changed`, `published`. |
| GET / POST | `/v2/admin/recognition/rebuild` | Letzten Auftrag abfragen / Neuaufbau starten. |
| DELETE | `/v2/admin/recognition/rebuild/{job_id}` | Neuaufbau abbrechen. |

Schema 4 ergänzt die Sperrliste additiv. Eine versionierte Nachprüfung bereinigt auch bereits migrierte offene Altvorschläge: Datumsangaben mit typischen PDF-/OCR-Abständen werden nicht als Telefonnummern behandelt; Kontaktvorschläge benötigen einen plausiblen Personennamen. Dokumentüberschriften und leere Namen liefern keinen Kontaktvorschlag. Die Nachprüfung verändert keine historischen Entscheidungen oder Kundendaten.

## Reproduzierbare Prüfung

```sh
.venv/bin/ruff check packages tests tools
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/run_ci.py --coverage
.venv/bin/python tools/recognition_benchmark.py --output /tmp/recognition-benchmark.json
PAPAGUI_SMOKE_IMAGE=papagui-server:recognition-test bash tools/docker_smoke.sh
docker run --network none --rm --entrypoint python \
  -v "$PWD/tools/recognition_parser_smoke.py:/tmp/parser_smoke.py:ro" \
  papagui-server:recognition-test /tmp/parser_smoke.py
```

Der Benchmark öffnet ausschließlich `tests/fixtures/recognition_synthetic/cases.json`. Er vergleicht die frühere Regexregel mit der neuen fachlichen Pipeline und berichtet Precision/Recall getrennt nach Feld, Strukturformat, Gruppe und Testsplit. Er misst keine OCR-Genauigkeit und keine Produktivqualität. Die echte Parserprüfung erzeugt Office-Dateien, Bilder, einen gedrehten Scan und ein gemischtes PDF selbst und löscht sie anschließend. Weitere Tests erzeugen echtes XLS und mehrspaltige PDFs.

Die Tests decken außerdem Migration, stabile IDs/Ablehnungen, Kopien und Exporte, aktive/veraltete Belege, Adressatomarität, Kontaktkonflikte, zweite Suchstufe, parallele Kundenänderungen, Jobabbruch/Neustart, API-Kompatibilität, Offline-Snapshots und mehrere Entscheidungen im selben Dialog ab. Die bestehende repositoryweite Coveragegrenze bleibt 93 %.

Sperrlistenprüfungen verwenden ausschließlich synthetische SQLite-Bestände
und prüfen atomaren Rollback, kanonische Dubletten, einmaligen Abgleich und
Veröffentlichung, parallele Änderungen, nicht wiederverwendete IDs sowie
Veröffentlichungswiederholung nach Neustart. Kontrolliert angehaltene
Indexläufe prüfen Änderungen während Auslesung und Bewertung.

Parallelität, Ressourcenbudgets, schnelle unveränderte Folgeläufe,
Inhaltsprüfung, Abbruch und Workeranzeige werden mit künstlich erzeugten
Dokumenten und temporären Datenbanken geprüft. Laufzeitvergleiche der
Dokumentauslesung verwenden ebenfalls ausschließlich synthetische Dateien;
sie messen keine Laufzeit oder Erkennungsqualität des produktiven Bestands.
Für eine Freigabe müssen diese Prüfungen am Releasecommit erneut ausgeführt
werden; siehe [Releasecheckliste](releasing.md).

Die optionale Docling-/Modellstufe bleibt deaktiviert: Ein zusätzlicher Nutzen auf einem unabhängigen realen Abnahmebestand und innerhalb des Zielhardwarebudgets wurde nicht nachgewiesen. Das Benchmarkwerkzeug enthält eine ausdrückliche Prüfung dieser Voraussetzungen; es lädt oder ruft kein Modell auf.

Historische Prüfberichte vom 9. September 2026 sind in der Git-Historie erhalten.
Sie sind kein Nachweis für die aktuelle Releasequalität.
