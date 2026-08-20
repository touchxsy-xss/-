# Database migrations

The current deployment has two API entrypoints while the service is being consolidated:

- `app/server.py` applies `sqlite/*.sql` during startup for the Raspberry Pi systemd service.
- `app/main.py` creates the SQLAlchemy base schema, then applies the PostgreSQL migration set during startup.

Each migration is recorded in `schema_migrations` and is applied once. The first migration is additive: it does not delete or rewrite existing residents, tickets, articles, or events. A rollback should restore application code to the previous Git commit; database rollback scripts are intentionally not destructive. If a later migration must remove data or columns, it requires an explicit backup and a separate down migration.
