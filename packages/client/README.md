# papagui-client

Offline-capable PapaGUI desktop client. It downloads immutable catalog and
customer generations, opens catalog databases read-only and stores pending
customer edits in a separate durable outbox. It contains no server process,
index builder or customer-recognition writer.

Entrypoints:

- `papagui-client`: main desktop process
- `papagui-tray`: independent server status/admin process

Configuration and offline behaviour are documented in
[`docs/client.md`](../../docs/client.md).
