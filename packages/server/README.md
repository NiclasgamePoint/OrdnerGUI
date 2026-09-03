# papagui-server

Headless PapaGUI service for catalog generation, content extraction, customer
recognition, the authoritative `customers.db`, immutable component generations,
scheduling and the HTTP API.

The package has no Qt or client dependency. Production operation is documented
in [`docs/server/operations.md`](../../docs/server/operations.md).

Development example:

```bash
python -m papagui_server serve \
  --source ./Bauvorhaben \
  --data ./docker-server-data \
  --config ./docker-config \
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
