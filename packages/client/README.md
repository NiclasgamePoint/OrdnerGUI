# papagui-client

Offline-capable PapaGUI desktop client. It downloads immutable catalog and
customer generations, opens catalog databases read-only and stores pending
customer and journal edits in separate outbox components within
`customer-outbox.db`. It contains no server process,
index builder or customer-recognition writer.

Entrypoints:

- `papagui-client`: main desktop process
- `papagui-tray`: independent server status/admin process

The Qt/PySide6 client resolves portable `source_id` mappings on Windows, macOS
and Linux. On macOS, the packaged client and tray are adjacent app bundles.
Repository launchers may start a separate Docker server; opening the Indexserver
window from the client starts the tray process only. Native platform acceptance
is separate from synthetic GUI and launcher tests.

The Indexserver window has **Übersicht**, **Indexeinstellungen**,
**Kundenerkennung** and **Aktivität** tabs. All remote controls use the configured
client API token without a separate admin login. The recognition tab supports:

- Staging exclusions and multiple deletions, undoing deletions, discarding
  drafts, and saving them together in one batch request.
- Preserving drafts across reloads, failed requests and hidden windows. Drafts
  are in memory and disappear when the tray process exits.
- Retrying an outstanding customer-snapshot publication, including after a
  client restart. A server without the batch endpoint must be updated.
- Starting and cancelling a separate full recognition rebuild, with aggregate
  document-worker and processing statistics when supplied by the server.

Local customer and journal edits remain available offline through the durable
outboxes. Recognition decisions and server administration need a connection;
the recognition tab remains accessible to inspect its current state and retry.
Only the server mutates authoritative customer data or runs indexing and OCR.

Configuration and offline behaviour are documented in
[`docs/client.md`](../../docs/client.md).
