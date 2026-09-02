# Golden fixtures

These files preserve externally relevant 0.4.1/v1 and 0.4.2/v2 wire and data
shapes independently of the implementation modules. Tests materialize the SQL
dump into a temporary SQLite database; no test writes into this directory.

- `api/v1-server-status.json`: nested status shape accepted during transition
- `generations/v1-combined.json`: genuine legacy combined download manifest
- `generations/v2-current.json`: split index/customer generation manifest
- `migration/customers-v0.4.1.sql`: representative pre-split customer database

When a contract changes, add a new versioned fixture instead of silently
rewriting the historical input.
