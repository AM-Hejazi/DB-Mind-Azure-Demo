# DB-Mind beyond the maintenance demonstration

DB-Mind's multi-agent design is independent of the maintenance business domain.
The schema snapshot supplies tables, columns, keys, relationships and bounded
sample values to Front Desk, Schema Retriever, Candidate Generator, Analyzer and
Feedback. A different database requires an adapter, correct dialect handling,
explicit scope and a least-privilege runtime identity; it does not require making
the chatbot a database administrator.

## What this release can run today

The supported profiles are `local_synthetic` (SQLite), `local_sqlserver` (a
loopback SQL Server lab) and `azure_sql` (Azure SQL). They intentionally enforce
an approved synthetic contract: nine maintenance tables, SQLite fixture markers,
SQL Server schema `demo`, and database names beginning `dbmind_synthetic_`.
Changing only the hostname/password cannot connect this release to an arbitrary
production database. Do not rename a real database or remove the guards to bypass
that restriction. This page distinguishes working fixture setup from the
engineering required for a new domain. No general-purpose production profile is
included yet.

## Initial setup for a supported database

1. Install Python 3.12 and the locked dependencies as in the README. SQL Server
   also needs Microsoft ODBC Driver 18; the Docker `azure` target includes it.
2. For a local disposable database, seed the SQLite fixture using the README
   quick start. For SQL Server/Azure SQL, have the database owner prepare a
   dedicated fictional fixture and restricted reader following
   [the synthetic fixture guide](synthetic-data.md) and
   [Azure setup](azure-deployment.md). Seeding is a separate, explicit operation;
   discovery and app startup do not create or replace database data.
3. Supply configuration privately. The app reads process environment and does
   not automatically load `.env`. Use a protected Docker env file or secret
   bindings. For your own dedicated Azure demonstration database, the connection
   settings have this shape (placeholders only):

   ```text
   DB_PROFILE=azure_sql
   DB_DIALECT=sqlserver
   DB_SERVER=YOUR_SERVER.database.windows.net
   DB_NAME=dbmind_synthetic_YOUR_DEMO
   DB_SCHEMA_SCOPE=demo
   DB_SCHEMA_JSON=var/synthetic/your-demo-schema-v1.json
   DB_AUTH=sql_password
   DB_USER=YOUR_RESTRICTED_READER
   DB_PASSWORD=SUPPLY_PRIVATELY
   DB_DISCOVERY_TIMEOUT_SECONDS=300
   DB_SAMPLE_ROWS=5
   ```

   Leave `DB_SQLITE_PATH` unset for Azure. Managed identity and Azure CLI token
   authentication are also supported; see `.env.example` and configuration docs.
   Never use a setup/admin SQL identity for the application. Application admin
   login credentials are independent of database credentials.
4. Extract schema and samples explicitly from the configured environment:

   ```bash
   python -m src.discovery refresh
   python -m src.discovery inspect
   python -m src.discovery preflight
   python -m src.db_check --connect
   ```

   `refresh` reads catalogs and bounded samples and writes the versioned JSON
   snapshot plus lexical index. `inspect` checks local metadata/status counts
   without printing sample rows. `preflight` validates identity, age, scope,
   integrity, permissions and catalog changes, refreshing when needed. The
   fixture-specific connection check confirms equipment count 60.
   SQL Server/Azure `refresh`, `preflight` and `--connect` use network/database
   authentication; they make read operations, never model calls. `inspect` is local.
5. Confirm a complete valid snapshot before serving. Configure private app
   authentication, choose `APP_LLM_MODE=live` with a private provider key (mock
   mode only recognizes the reference fixture questions), then run `python app.py`
   or the Docker target. Startup also runs preflight and refuses invalid state.
   Run preflight/refresh again after schema changes or cache expiry.

In an already configured running container, use the same adapter and environment:

```bash
docker exec YOUR_CONTAINER python -m src.discovery refresh
docker exec YOUR_CONTAINER python -m src.discovery inspect
docker exec YOUR_CONTAINER python -m src.discovery preflight
docker exec YOUR_CONTAINER python -m src.db_check --connect
```

## What extraction includes and how to limit samples

Discovery captures accessible user tables, columns/types, primary/foreign keys,
indexes and available descriptions. It does not export a full database. Views are
reported as unsupported and are not query targets. Metadata visibility is limited
to accessible objects, so visible catalogs cannot prove that hidden objects do not
exist. Incomplete snapshots are not accepted for use by agents.

`DB_SAMPLE_ROWS=5` requests at most five rows per eligible table; use `0` to disable
row samples before extraction. Sampling is random for small tables, may scan/sort,
and skips tables beyond `DB_SAMPLE_SCAN_ROWS` (default 10,000). Value length and
aggregate bytes are bounded. Check sampling status/completeness instead of assuming
every table was sampled. See [discovery policy](discovery.md) for exact limits.
Generated snapshots, indexes and failure reports belong under ignored `var/`;
never commit them or include them in an image. For sensitive databases, use
approved sanitized test copies and review what context may leave your network:
live mode sends questions, schema, enabled sample values and limited result/feedback
context to the configured external model provider. Disabling samples does not
remove questions, schema or result context from live processing.

## Adapting to a different schema or SQL engine

This is implementation work, not an environment-only switch:

- Add a separate, explicitly selected profile in `src/settings.py` rather than
  loosening the synthetic demo profile. Define allowed database identity/schemas
  and expected objects for that profile.
- Implement/reuse the transport in `src/database.py` and catalog/sample extraction
  in `src/discovery.py`; extend snapshot identity/contract validation and retrieval.
  PostgreSQL/MySQL require their own connectors, catalogs and dialect tests.
- Replace fixture-specific table expectations with an operator-approved contract
  in the new profile. Implement effective read-only principal checks for all
  approved objects, including inherited write/elevation/impersonation permissions.
  Keep `src/db_connection.py` protections intact for the existing demo.
- Extend `src/sql_gate.py` dialect parsing, allowed objects and forbidden operation/
  function rules. Generated SQL must remain a single bounded read against verified
  objects; unknown tables and writes must fail closed.
- Review domain-specific prompt/examples, lexical aliases, fixed demo date and
  fixture-specific `src/db_check.py` count check. Build regression tests for keys,
  joins, samples, permissions, unknown objects, dialect syntax and follow-ups.
- Have the database owner provision a restricted reader and approved metadata
  visibility, then perform read-only integration acceptance on a test database.
  Keep operator provisioning credentials separate from runtime and app admin.

The maintenance dataset demonstrates the architecture; it is not a claim that
this release already supports every SQL database or achieves thesis accuracy on
new domains. Schema extraction supplies context, not proof of semantic correctness.
