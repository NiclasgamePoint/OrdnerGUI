# ADR 0001: Paketgrenzen

Status: angenommen für 0.4.2

PapaGUI verwendet drei interne Pakete. Contracts enthalten ausschließlich
gemeinsame Datenverträge. Server und Client besitzen eigene Domain-,
Application- und Adaptergrenzen. Diese Aufteilung verhindert, dass Qt in das
Serverimage oder ein Index-Writer in den Client gelangt. Importregeln und
Artefaktinhalte werden in CI geprüft.
