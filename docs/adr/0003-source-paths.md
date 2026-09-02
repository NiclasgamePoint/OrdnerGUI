# ADR 0003: Logische Quellpfade

Status: angenommen für 0.4.2

Der Server speichert `source_id` und relative Pfade statt absoluter
Containerpfade. Jeder Client besitzt ein betriebssystemspezifisches Mapping.
Dadurch kann dieselbe Synology-Quelle auf Windows, macOS und Linux geöffnet
werden, ohne serverseitige Pfade zu übernehmen.
