# ADR 0004: Unabhängige Produktversionen

Status: angenommen für 0.4.2

Client- und Serverversion werden unabhängig geführt und über getrennte Tags
markiert. API-Major und Generationsschema sind eigene Kompatibilitätswerte.
`papagui-contracts` ist ein internes Paket und erzeugt kein eigenes
Nutzerrelease.

Ergänzung ab 0.5.0: Der automatische Releasezug verwendet einen gemeinsamen
numerischen Tag (`0.5.0`, später entsprechend höher) und synchronisierte
Paketversionen. Ein Release enthält alle Clientplattformen und das Serverimage.
Die Installationszeitpunkte bleiben unabhängig; die CI prüft deshalb beide
Richtungen gegen den Kompatibilitätsausgangspunkt 0.4.3. Separate Produktreleases
werden vom gemeinsamen automatischen Updatefeed noch nicht veröffentlicht.
