# Database configuration — Milestone 3

Database settings now have one immutable, validated source: `src/settings.py`. It reads an explicitly supplied environment mapping or process environment, with no network/file discovery. `config.py` bridges only nonsensitive database fields to the existing pipeline. Active stage modules no longer implicitly load `.env`; export settings before starting a process or use your operator's environment-file support. The LLM model map remains transitional until Milestone 5.

`src/database.py` provides a new connection/cursor for each query. `src/validator.py:validate_sql` remains a compatibility wrapper returning `(rows, columns, feedback)`. Existing initial, recovery, feedback, and legacy callers of that wrapper now use this transport. The old standalone `src/db_handler.py` export script is preserved as historical source and **must not be run**: its hard-coded internal connection will be replaced by explicit discovery in Milestone 4. It is not imported by the new adapter.

## Offline default and explicit checks

The default is **local_synthetic**, with SQLite at `var/synthetic/maintenance_v1.sqlite3`. It never searches for another file. If missing, the adapter reports the explicit seed command rather than creating or selecting a database. It requires the synthetic application's SQLite ID and schema version, opens `mode=ro`, enables `query_only`, and applies an authorizer preventing writes, attachment, PRAGMA changes, and extension loading. Reads are limited to known fixture tables and SQLite catalog metadata. Markers establish fixture identity, not a complete provenance audit; discovery/snapshot matching is Milestone 4.

```bash
python -B -m src.db_check
python -B -m src.db_check --connect
python -B -m unittest discover -s tests -v
```

The first command validates configuration and prints profile/dialect/auth/scope only. It never connects. `--connect` explicitly reads an equipment count; in the default profile this is local SQLite, but in SQL Server/Azure profiles it triggers network authentication and a connection. Neither command seeds, calls an LLM, or prints credentials/connection strings. Seed first using the [synthetic-data guide](synthetic-data.md) if the fixture is absent.

The default schema snapshot path is `var/synthetic/schema-v1.json`. Milestone 4 now supplies it through the explicit discovery CLI; see [discovery.md](discovery.md). The pipeline helpers consume the validated new snapshot. UI factory/launch side effects, live model configuration, session logging, SQL safety, and obsolete UI callback contracts remain later work; no UI startup is claimed here.

## Environment reference

`.env.example` contains placeholders and local defaults only; no setup credentials.

| Variable | Meaning / default |
| --- | --- |
| `DB_PROFILE` | `local_synthetic` default; explicit `azure_sql` or `local_sqlserver` |
| `DB_DIALECT` | Optional compatibility assertion: `sqlite` locally, `sqlserver` for SQL profiles; a conflict fails |
| `DB_SQLITE_PATH` | Fixed local fixture path; relative paths resolve against repository root |
| `DB_SCHEMA_JSON` | Explicit metadata snapshot path; required in SQL profiles, no schema autodetection |
| `DB_SCHEMA_TEXT` | Optional legacy text reference; no text autodetection (active retrieval uses JSON) |
| `DB_SERVER` | Required SQL host; Azure public-cloud `NAME.database.windows.net`, or loopback `127.0.0.1,14333` / `localhost,14333` for the lab |
| `DB_NAME` | Required dedicated `dbmind_synthetic_...` database; no company/default database |
| `DB_SCHEMA_SCOPE` | Required `demo` in SQL profiles; `main` in SQLite; other scopes not yet supported |
| `DB_AUTH` | Explicit `managed_identity`, `azure_cli`, or `sql_password` for Azure; local SQL Server supports `sql_password` |
| `DB_MANAGED_IDENTITY_CLIENT_ID` | Optional UUID selecting a user-assigned identity; unset for system-assigned identity |
| `DB_USER`, `DB_PASSWORD` | Required only for SQL-password mode; must be absent in token modes. Password whitespace is preserved. Known admin user names are rejected; effective permissions are also checked |
| `DB_DRIVER` | `ODBC Driver 18 for SQL Server`; other drivers currently rejected |
| `DB_TRUST_LOCAL_CERTIFICATE` | Default `false`; `true` only for the loopback local SQL Server lab |
| `DB_CONNECT_TIMEOUT_SECONDS` | Default 10; range 1–60, ODBC login/SQLite lock timeout and token transport/CLI timeout settings |
| `DB_QUERY_TIMEOUT_SECONDS` | Default 15; range 1–120, per-statement ODBC timeout or SQLite progress deadline |
| `DB_CONNECT_RETRIES` | Default 2 retries (3 total attempts), range 0–3; selected transient connect states only |
| `DB_MAX_RESULT_ROWS` | Default 1,000; range 1–10,000; excess fails with an actionable limit error |
| `DB_MAX_RESULT_BYTES` | Default 1 MiB; range 1 KiB–10 MiB; bounded returned values/column text |

`DB_SQLITE_PATH`, `DB_SCHEMA_JSON`, `DB_SCHEMA_TEXT`, `DB_DRIVER`, `DB_SERVER`, `DB_NAME`, and `DB_DIALECT` retain their names. `DB_TRUSTED_CONNECTION` is retired and fails with an instruction to choose `DB_AUTH`. Previously automatic file selection and implicit SQL Server fallback are removed. SQL credentials use new runtime-only variables; the seed's `DBMIND_SQLSERVER_SETUP_PASSWORD` is never read by the application adapter.

The public demo profiles intentionally accept only the synthetic database name convention and fixture scope. The naming convention alone does not prove the content is synthetic: use the versioned seed and later snapshot identity checks. Other clouds/custom SQL ports/schemas and arbitrary real databases are not supported by this migration profile.

## SQL Server transport, TLS, and authentication

Retain **pyodbc 5.2.0** from the existing requirements, with Microsoft's **ODBC Driver 18** OS package. Token modes additionally use the selected established **azure-identity 1.25.3** release. These direct pins are in `requirements-azure-sql.txt`; no packages were installed and runtime compatibility on this Python 3.14.2 host was not established. SDK selection is not a claim that it is the newest release. Packaging/locking the complete runtime dependency tree remains Milestone 8.

When explicitly preparing a SQL-capable environment, `python -m pip install -r requirements-azure-sql.txt` downloads Python packages. It does **not** install the OS ODBC driver. On Linux, install the distro-appropriate Microsoft `msodbcsql18` package and unixODBC runtime; builds of pyodbc need development headers/toolchain, and slim Debian images can require `libgssapi-krb5-2`. The future container must include those actual OS dependencies, not just pip packages. Driver installation/EULA/repository setup are operator actions, not performed here. Follow the distribution/architecture-specific [Microsoft installation instructions](https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server), checked 2 October 2026.

All SQL connections explicitly use `Encrypt=yes;TrustServerCertificate=no`, including token modes. Azure cannot bypass certificate verification. Only the loopback synthetic lab can opt into trusting its self-signed certificate. `ApplicationIntent=ReadOnly` is a routing hint, **not** the permission boundary; the restricted database identity is that boundary.

For deployed Azure Container Apps, prefer **`DB_AUTH=managed_identity`**. The adapter explicitly uses `ManagedIdentityCredential`, with an optional client UUID and no credential-chain fallback. For local Azure development, choose **`DB_AUTH=azure_cli`** explicitly and sign in to the intended tenant with `az login` before the opt-in check; that login uses the network. It selects only `AzureCliCredential`, never the deployment/setup identity by a default credential chain. Provision a separate restricted developer principal when the owner login has admin rights: the transport rejects effective setup privileges. [Container Apps managed-identity guidance](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity) describes its identity endpoint and Azure Identity library support.

The requested SQL token scope is `https://database.windows.net/.default`. The adapter encodes the token as length-prefixed UTF-16LE for `SQL_COPT_SS_ACCESS_TOKEN` (1256). Token connection strings contain **none** of UID, PWD, Authentication, or Trusted_Connection, as required by [Microsoft's ODBC token documentation](https://github.com/MicrosoftDocs/sql-docs/blob/live/docs/connect/odbc/using-azure-active-directory.md). Credentials are closed after acquisition and a token is acquired when opening each logical connection. Tokens and provider exception messages are never returned to callers.

Optional SQL-password mode uses a restricted contained user and `DB_PASSWORD` supplied by the operator's secret store. ODBC values are brace-escaped, including semicolons/closing braces; secrets are excluded from settings repr, the compatibility dictionary, check output, and mapped errors. Do not reuse admin/seed credentials. User/session controls cannot choose the endpoint or credentials through the database API.

## Provisioning the restricted principal (owner action, not executed)

Authentication, database authorization, and firewall reachability are **separate prerequisites**. Attaching a managed identity or assigning an Azure resource-management role does not grant SQL SELECT permissions. An owner/setup identity first seeds the dedicated database, then runs `data/synthetic/v1/runtime-permissions.tsql.sql` there. The script creates a reader role and grants SELECT/VIEW DEFINITION on exactly the nine fixture tables, with write/DDL/procedure restrictions. It has a synthetic-name guard; no application startup runs it. Object-scoped metadata permissions intentionally do not reveal the rest of a database.

With the SQL logical server's Entra administrator configured and the setup session able to resolve the target principal in Entra, execute in the target synthetic database:

```sql
-- Replace this display name with the app's actual attached managed identity.
CREATE USER [YOUR_MANAGED_IDENTITY_DISPLAY_NAME] FROM EXTERNAL PROVIDER;
-- After applying runtime-permissions.tsql.sql:
ALTER ROLE [dbmind_demo_reader] ADD MEMBER [YOUR_MANAGED_IDENTITY_DISPLAY_NAME];
```

Use the same mechanism for a separate restricted Entra developer user/group. Directory resolution/permissions for the setup identity can be an additional prerequisite; see [Microsoft's Entra provisioning guidance](https://learn.microsoft.com/azure/azure-sql/database/authentication-aad-configure?tabs=azure-powershell&view=azuresql). Actual identity creation, assignment, server administration, and firewall commands belong in the later owner deployment runbook and were not run here.

For the SQL-authentication alternative in Azure SQL, a setup session uses a **new contained user**, inserting a secret only through secure operator tooling, never a committed script:

```sql
CREATE USER [dbmind_demo_runtime] WITH PASSWORD = 'REPLACE_IN_SECURE_SETUP_SESSION';
ALTER ROLE [dbmind_demo_reader] ADD MEMBER [dbmind_demo_runtime];
```

For the optional local SQL Server container, instead create a dedicated server login and its database user as setup, then add that user to the same reader role. Azure's contained-password-user recipe is not assumed to work in a local server with contained authentication disabled. Example setup SQL, not executed:

```sql
-- In master, as local setup only:
CREATE LOGIN [dbmind_demo_runtime] WITH PASSWORD = 'REPLACE_IN_SECURE_SETUP_SESSION';
-- In dbmind_synthetic_local, after seed and runtime-permissions.tsql.sql:
CREATE USER [dbmind_demo_runtime] FOR LOGIN [dbmind_demo_runtime];
ALTER ROLE [dbmind_demo_reader] ADD MEMBER [dbmind_demo_runtime];
```

Connect the application with that runtime user; never with `sa`. For the local lab use `DB_PROFILE=local_sqlserver`, `DB_DIALECT=sqlserver`, `DB_SERVER=127.0.0.1,14333`, `DB_NAME=dbmind_synthetic_local`, `DB_SCHEMA_SCOPE=demo`, an explicit `DB_SCHEMA_JSON` path, `DB_AUTH=sql_password`, and the new user's secret. Enable `DB_TRUST_LOCAL_CERTIFICATE=true` only for that loopback self-signed lab.

On every SQL query connection, preflight rejects dbo/admin/write roles, database control/table-creation rights, demo-schema alteration/control, and effective INSERT/UPDATE/DELETE/ALTER/CONTROL on the nine tables. It requires SELECT on them all; missing/invisible objects fail closed. Checks cover inherited/custom grants, not just username spelling. They add ten short metadata queries per connection. SQL principal provisioning and the permission checks are **mock-tested only** here; actual T-SQL/Entra behavior remains unverified.

## Limits, errors, and evidence

Connections/cursors close on success and failure and are not stored in visitor state. No shared mutable cursor exists. Application-controlled values are bound parameters. Selected transient connection failures retry with delays of 0.25, 0.5, then 1 second up to the configured attempt cap. SQL and permission checks are not replayed. Authentication failures are not retried by the adapter; managed-identity HTTP retry configuration is disabled and CLI subprocess timeout is set. These are transport/per-statement limits, **not a hard total pipeline deadline**; driver/identity-provider behavior still needs integration testing.

Results are fetched in small batches and row/byte excess is reported instead of silently truncating. A single returned cell can still be allocated by the driver before its byte limit is checked; this output cap is not a server-work/memory bound. SQLite interrupts long-running VM queries through a progress callback; SQL Server uses the connection's statement timeout. Full dialect parsing, generated SQL allowlists, disallowed functions/external access, cancellation/recovery budgets, and public access/spending controls remain **Milestone 6**. Do not publish the current app on the strength of these connection checks.

On 2 October 2026, 18 offline tests passed: the previous eight fixture tests plus ten database/settings tests. Real SQLite tests cover parameter binding, read-only/attachment protections, wrong/missing database refusal, row/byte limits, timeout, and the compatibility wrapper. Mock tests cover token encoding/TLS fields, explicit identity selection, restricted/custom permissions, retry counts, cleanup, fresh connections, and safe errors. Both configuration-only and local connection CLIs passed (60 equipment rows). No SQL Server connection, live token request, driver installation, LLM call, Azure operation, or container startup was performed. Official documentation was read online; it is design evidence, not cloud validation.

Next: **Milestone 4**, explicit metadata discovery and bounded samples, the versioned snapshot, mismatch/expiry/atomic refresh rules, and the existing agents' canonical schema adapter.
