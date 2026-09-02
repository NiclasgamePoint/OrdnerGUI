# ADR 0002: Unveränderliche Generationen und Offline-Outbox

Status: angenommen für 0.4.2

Heruntergeladene Generationen werden niemals lokal verändert. Kundenänderungen
liegen bis zur Serverbestätigung in einem separaten Overlay und einer
persistenten Outbox. Index- und Kundengenerationen wechseln unabhängig und
werden durch ein atomisches Manifest gemeinsam aktiviert. Damit bleiben
Prüfsummen aussagekräftig und Offlineänderungen konfliktfähig.
