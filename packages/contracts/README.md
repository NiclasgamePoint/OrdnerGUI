# papagui-contracts

Dependency-free, immutable data contracts shared by the PapaGUI client and
server. The package deliberately contains no transport, persistence, Qt, or
application code.

All public DTOs support `to_dict()`, `from_dict()`, `to_json()`, and
`from_json()`. Parsers accept the documented v1 compatibility shapes while
serialization always emits the canonical shape of the represented contract.

The 0.4.2 contracts cover component generations, portable `SourcePath` catalog
files/folders/project roots, revisioned customers and projects, and persistent
recognition cases, decisions, run summaries, evidence, and customer suggestions.
