# papagui-contracts

Dependency-free, immutable data contracts shared by the PapaGUI client and
server, requiring Python 3.11 or newer. The package deliberately contains no
transport, persistence, Qt, or application code.

All public DTOs support `to_dict()`, `from_dict()`, `to_json()`, and
`from_json()`. Parsers accept the documented v1 compatibility shapes while
serialization always emits the canonical shape of the represented contract.

The 0.4.2 contracts cover component generations, portable `SourcePath` catalog
files/folders/project roots, revisioned customers and projects, and persistent
recognition cases, decisions, run summaries, evidence, and customer suggestions.

`SourcePath` carries a source identifier and a portable relative path; resolving
Windows drives, UNC shares or POSIX mount points is the client's responsibility.
System capabilities, server/index status, settings and error payloads are shared
contracts as well. Invalid contract input raises `ContractValidationError`.

The package version is derived from `CONTRACT_VERSION` in `system.py` and
exposed as `papagui_contracts.__version__`. See [API documentation](../../docs/api.md)
and the [versioned compatibility fixtures](../../tests/fixtures/README.md)
when changing a wire shape. Graphify may inspect this package's `src/**/*.py`
files; compatibility fixtures and customer data are outside that graph corpus.
