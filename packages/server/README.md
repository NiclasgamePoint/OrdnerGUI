# papagui-server

Headless PapaGUI service for catalog generation, content extraction, customer
recognition, the authoritative `customers.db`, immutable component generations,
scheduling and the HTTP API.

The package has no Qt or client dependency. Production operation is documented
in [`docs/server/operations.md`](../../docs/server/operations.md), the wire
contract in [`docs/api.md`](../../docs/api.md), and the implementation in
[`ARCHITECTURE.md`](ARCHITECTURE.md).

For local development, install the server and contracts packages, prepare a
non-empty synthetic source directory, and point `PAPAGUI_API_TOKEN_FILE` at
a protected UTF-8 token file before starting:

```bash
python -m papagui_server serve \
  --source ./synthetic-source \
  --data ./synthetic-server-data \
  --config ./synthetic-server-config \
  --initialize-source-identity
```

For containers, keep the client token out of the environment visible through
`docker inspect`: mount an UTF-8 file and set `PAPAGUI_API_TOKEN_FILE`.
`PAPAGUI_API_TOKEN` takes precedence and is intended for local development
only. Settings and maintenance operations require no additional password or
administrative session.

On the first non-empty source mount, `--initialize-source-identity` stores a
portable identity in the persistent config directory. Later empty or replaced
mounts are rejected before scanning, so they cannot publish an empty catalog.
Use `--no-initialize-source-identity` after provisioning for strict operation.

## Scheduling and extraction

`serve` starts the API, index scheduler and durable recognition-job worker.
It requests an initial index run by default. `--no-run-on-start` skips that
request and defers an already overdue daily verification until the smaller
of the normal interval and 24 hours. A future persisted verification
deadline is still respected. Automatic runs can be disabled through the
server settings independently of explicit run requests.

Regular scans reconcile all paths and reuse successfully processed documents
with unchanged metadata and extraction configuration without rereading their
source bytes. The enabled daily check verifies content hashes; its completed
full-catalog timestamp survives restarts. `full_rebuild` recreates the
catalog and verifies hashes while retaining valid extraction-cache reuse.

Indexing and customer rereads share a bounded document pipeline with 1–20
workers, selected from the resource profile, effective CPU limits, available
RAM and per-document budget. One writer persists catalog/cache results;
customer interpretation follows sequentially. Structured PDF/Office parsers
run in isolated bounded processes, with optional OCR. Large extraction
artifacts stay in the private `extraction/artifacts.db` store. Aggregate
worker status is exposed through the additive `document-workers` capability.

The CLI also supports `once [--full-rebuild]` for a synchronous run and
`openapi [--output PATH]` for schema export; both accept the same runtime
path and source-identity arguments as `serve`. The repository's deterministic
OpenAPI snapshot is maintained by `tools/export_openapi.py`.

## Recognition and administration

Per-customer durable jobs support `reassess` using existing text and `extract`
to force document rereads for assigned projects. The global recognition
`rebuild` forces catalog/document preparation and reviews all customers
beyond the usual project search budget. File filters and per-document
safety limits remain effective. Interrupted jobs are requeued after restart;
explicit cancellations remain cancelled.

The global blocklist supports atomic batches of additions and deletions at
`POST /v2/admin/recognition/blocklist/batch`, advertised by
`recognition-blocklist-batch`. A changed batch reconciles suggestions once
and publishes the customer component once, without running extraction or
OCR. It can be edited during indexing. Validation failures roll back the
whole delta; canonical duplicates and missing deletion IDs are retryable.
Blocklist IDs are never reused.

The batch response contains `entries`, `changed` and `published`. If
publication fails after commit, saved changes remain effective and a durable
retry marker is returned as `published: false`. The list endpoint exposes
`publication_pending`; an empty batch retries the pending publication even
after restart. An unchanged batch without pending publication creates no
new generation. Legacy single-entry endpoints remain available.

Only one server instance may write a data directory. Index and customer
generations are activated atomically and retain three predecessors each.
Customer/blocklist writes use short SQLite transactions and publish after
commit. See the architecture document for those separate consistency
boundaries and the operations guide for backup and recovery.
