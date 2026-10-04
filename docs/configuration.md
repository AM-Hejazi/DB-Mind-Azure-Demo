# Database configuration

Settings come from process environment; the app does not automatically load `.env`.
Use an external, protected Docker env file or deployment secret store. See
[database setup](own-database-setup.md) before choosing a profile.

## Supported profiles

| Profile | Dialect | Required scope and identity |
| --- | --- | --- |
| `local_synthetic` (default) | `sqlite` | Dedicated fixture file with application/schema markers; `main` schema |
| `azure_sql` | `sqlserver` | Azure public-cloud SQL FQDN, `dbmind_synthetic_...` database, `demo` schema |
| `local_sqlserver` | `sqlserver` | Loopback `127.0.0.1,14333` or `localhost,14333`, dedicated synthetic database, `demo` schema |

These profiles deliberately restrict the current release to the approved fixture.
A different schema needs the integration work in [the adaptation guide](own-database-setup.md#adapting-to-a-different-schema-or-sql-engine).
No missing-file/connection error falls back to another database.

## Connection settings

| Variable | Default / requirement |
| --- | --- |
| `DB_PROFILE` | `local_synthetic`; select SQL profiles explicitly |
| `DB_DIALECT` | Optional assertion: `sqlite` or `sqlserver`, matching the profile |
| `DB_SQLITE_PATH` | `var/synthetic/maintenance_v1.sqlite3`; leave unset for Azure |
| `DB_SCHEMA_JSON` | `var/synthetic/schema-v1.json`; explicitly required for SQL profiles |
| `DB_SERVER` | Required for SQL profiles; Azure `YOUR_SERVER.database.windows.net` |
| `DB_NAME` | Required dedicated `dbmind_synthetic_...` database |
| `DB_SCHEMA_SCOPE` | `main` for SQLite; explicitly set `demo` for SQL profiles |
| `DB_AUTH` | Azure: `managed_identity`, `azure_cli` or `sql_password`; local SQL Server: `sql_password` |
| `DB_MANAGED_IDENTITY_CLIENT_ID` | Optional UUID selecting a user-assigned identity |
| `DB_USER`, `DB_PASSWORD` | Restricted SQL-password identity only; unset both for token auth |
| `DB_DRIVER` | `ODBC Driver 18 for SQL Server` |
| `DB_TRUST_LOCAL_CERTIFICATE` | `false`; `true` allowed only for the loopback SQL Server lab |
| `DB_CONNECT_TIMEOUT_SECONDS` | 10; range 1–60 |
| `DB_QUERY_TIMEOUT_SECONDS` | 15; range 1–120 |
| `DB_CONNECT_RETRIES` | 2; range 0–3, selected transient connection errors only |
| `DB_MAX_RESULT_ROWS` | 1,000; range 1–10,000 |
| `DB_MAX_RESULT_BYTES` | 1 MiB; range 1 KiB–10 MiB |

Relative file paths resolve against the repository root. Keep snapshots under
ignored `var/`. `DB_TRUSTED_CONNECTION` is unsupported; choose `DB_AUTH` explicitly.
`DB_SCHEMA_TEXT` is a compatibility field; active discovery/retrieval use JSON.
Sampling/cache settings are documented in [discovery](discovery.md).

## Authentication and permission setup

Install the locked Python dependencies and ODBC Driver 18 for SQL profiles; the
Docker `azure` target includes the OS driver. SQL connections use verified TLS.
`ApplicationIntent=ReadOnly` is only a routing hint: effective database permissions
provide the enforcement boundary.

Use a dedicated restricted reader, separate from database provisioning credentials.
For the approved fixture, a database owner applies
`data/synthetic/v1/runtime-permissions.tsql.sql` and adds the runtime user to
`dbmind_demo_reader`. It grants object-specific SELECT/VIEW DEFINITION and denies
write/setup/execute privileges. Provision a contained SQL-password user securely,
or configure an Entra administrator and create a contained Entra user for the
chosen managed identity/developer principal. Azure resource ownership or `AcrPull`
does not grant SQL data access.

```sql
-- In the approved test database, using an authorized setup identity:
CREATE USER [YOUR_MANAGED_IDENTITY_NAME] FROM EXTERNAL PROVIDER;
ALTER ROLE [dbmind_demo_reader] ADD MEMBER [YOUR_MANAGED_IDENTITY_NAME];
```

Resolve the intended identity in your tenant; never copy another deployment's
identity IDs. Supply a SQL user's password through secure setup tooling rather
than a committed SQL file. The local SQL Server lab may need a server login and
mapped database user instead of an Azure contained-user recipe.

Runtime checks reject elevated roles, effective write/control/schema permissions,
and impersonation grants, including inherited role grants. Required SELECT on
fixture tables is checked before query execution. Unknown mandatory permission
results fail closed; do not broaden grants to silence guard failures.

## Validate before serving

```bash
python -m src.db_check
python -m src.discovery preflight
python -m src.db_check --connect
```

The first command validates settings without connecting. Preflight and `--connect`
read the configured database (network/authentication for SQL profiles), never seed
it or call a model. The connection check expects 60 fixture equipment rows.

Each query uses a fresh connection/cursor with bounded output and statement timeout.
Limits do not bound all server work or driver allocation for a single large value.
Raw connection strings, credentials and driver exception bodies are not exposed
in configuration output or visitor errors. See [safety](safety.md) and
[Azure operations](azure-deployment.md).
