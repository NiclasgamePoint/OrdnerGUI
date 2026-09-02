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

For containers, keep secrets out of the environment visible through
`docker inspect`: mount UTF-8 files and set `PAPAGUI_API_TOKEN_FILE` plus
`PAPAGUI_ADMIN_PASSWORD_HASH_FILE`. Create the Argon2id password hash without
putting the password in argv:

```bash
printf '%s' 'a-long-admin-password' | papagui-server hash-password --stdin
```

Ohne `--stdin` fragt der Befehl das Passwort ausschließlich verdeckt an einem
interaktiven Terminal ab. Ein Klartextpasswort als Positionsargument wird nicht
akzeptiert, damit es weder in der Prozessliste noch in der Shell-Historie steht.

Non-empty direct values in `PAPAGUI_API_TOKEN` and
`PAPAGUI_ADMIN_PASSWORD_HASH` take precedence over file settings and are meant
for local development only. `PAPAGUI_ADMIN_PASSWORD` is a migration/development
bootstrap fallback and must not be used for production.

On the first non-empty source mount, `--initialize-source-identity` stores a
portable identity in the persistent config directory. Later empty or replaced
mounts are rejected before scanning, so they cannot publish an empty catalog.
Use `--no-initialize-source-identity` after provisioning for strict operation.
