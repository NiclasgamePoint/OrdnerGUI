# Synthetischer Erkennungsbenchmark

`cases.json` enthält ausschließlich erfundene Dokumentausschnitte und strukturierte Blöcke. Es wurden keine Kundendateien, echten Dateinamen, Datenbanken, Auditdaten oder Graph-Memories für diese Fälle gelesen. Die festen Eingabefälle sind die einzige Datenquelle des Benchmarkbefehls:

```bash
.venv/bin/python tools/recognition_benchmark.py --output /tmp/recognition-synthetic-report.json
```

Gemessen werden normalisierte fachliche Vorschläge in der Stufe `strong`: Telefon, E-Mail, Firma, atomare Anschrift und benannter Kontakt. Ein Kontaktvergleich enthält den Personennamen sowie die gemeinsam belegten Felder. Wiederholte Belege im selben Fall werden vor dem Vergleich zusammengefasst. `review`-Belege werden separat gezählt. Precision und Recall erscheinen insgesamt, je Feld, Eingabeformat, Fallgruppe und Kundengruppe; zusätzlich wird kundenweise gemittelt. Leere Nenner ergeben `null`, keine behauptete perfekte Qualität.

Die 54 Fälle decken nationale und internationale Telefonnummern, Durchwahlen, Fax, Zahlen-/Datums-/IBAN-Kontexte, ungültige E-Mail-Werte, eigene und fremde Parteien, den Kunden als Absender und Empfänger, Anschriften, Kontakte, Firmenformen und strukturierte Office-/OCR-Eingaben ab. Die nachträglich geschriebenen Regressionfälle tragen getrennte synthetische Kundenidentitäten für `development` und `evaluation`. Diese Bezeichnungen machen sie nicht zu einem unabhängig annotierten Abnahmebestand. Formatlabels prüfen die fachliche Verarbeitung vorbereiteter Blöcke, nicht die tatsächliche Parser- oder OCR-Genauigkeit.

Die zum Implementierungszeitpunkt gemessenen 27 richtigen, 0 falschen und 0 fehlenden Standardvorschläge belegen ausschließlich diese Fälle. Der eingefrorene frühere Regexansatz dient als reproduzierbarer Vergleich auf demselben synthetischen Material. Aus diesen Ergebnissen folgt keine Produktionsgarantie und keine behauptete Verringerung offener Karten im Kundenbestand.

Postfach-, ausländische und unvollständige Adressen bleiben Prüfbelege. Es wird kein Land und kein fehlender Adressteil ergänzt. Unbekannte Parteien bleiben Prüfbelege; eine Darstellungsgrenze löscht keine erkannten Werte. Eine spätere Erweiterung soll Fälle ergänzen, bevor Regeln angepasst werden; neue Kundengruppen dürfen nicht zwischen Entwicklung und Abnahme geteilt werden.

`model_stage_gate` bewertet ausschließlich übergebene Messwerte und ruft keine Modelle, Werkzeuge oder Netzwerkdienste auf. Selbst bei erfüllten Qualitäts-/Ressourcenkriterien bleibt `enabled=False`; das Ergebnis kann lediglich eine menschliche Prüfung vorbereiten. Eine Freigabe braucht unabhängige fachliche Annotationen, gemessenen zusätzlichen Recall bei ausreichender Precision und gemessene Ressourcen innerhalb des Betriebsbudgets.

Die Persistenz unterscheidet Quellenbelege und Dokumentfamilien. Eine Familie darf ausschließlich aus dem vollständigen, nichtleeren Text entstehen: Unicode-NFKC, Groß-/Kleinschreibung vereinheitlichen und Leerraum zusammenfassen, anschließend SHA-256. Identische Exporte mit unterschiedlichen Binärhashes zählen damit als eine unabhängige Stütze; die einzelnen Fundstellen bleiben erhalten. Ohne Textfamilie dienen Binärhash beziehungsweise Quellpfad als Rückfall. Es gibt keine unscharfe Vorlagenheuristik, und eine gemeinsame Telefonzentrale verbindet keine unterschiedlichen benannten Kontakte.

Die ergänzende Migrationvorschau steht in `tools/recognition_migration_preview.py`. Sie hat keinen Datenbankstandardpfad. Die automatisierten Tests bauen ausschließlich temporäre synthetische Legacy-Datenbanken, einschließlich WAL-Fall, Integritätsfehlern und bewusst simuliertem Datenverlust. Eine echte Bestandsprüfung wurde damit weder ausgeführt noch ersetzt.
