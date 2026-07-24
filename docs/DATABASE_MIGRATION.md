# Relay database migration

Relay stores its job history in the SQLite database under `RELAY_HOME`.
Database schema changes use SQLite `PRAGMA user_version`.

## Upgrade behavior

- A new database is created at the current schema version.
- A legacy database is backed up before migration.
- Migration runs in a transaction.
- A migration failure rolls back and stops startup.
- A database newer than the installed Relay is rejected.
- Downgrade is not supported; restore the migration backup if needed.

The G0 migration upgrades the Relay 0.5.0 schema to schema version 1 and adds
Job history metadata used by the read API. Schedule tables are added in the
Schedule core release, not in G0.

Backups are written beside the database with a name like:

```text
relay.db.backup-20260723T120000Z.db
```

The committed fixtures used by the migration tests were generated from the
Relay 0.5.0 schema at commit `4b7d710` using
`tests/fixture_builders/build_relay_0_5_0_fixtures.py`.
