# Connect your own Azure SQL database

Use `DB_PROFILE=azure_sql_custom` to connect an existing Azure SQL database without
renaming it, seeding maintenance tables or changing the agents. An explicit
installation command extracts your accessible schema and bounded samples into
the same validated snapshot used by Front Desk, Schema Retriever, Candidate
Generator, Validator, Analyzer and Feedback.

The demo profiles (`local_synthetic`, `local_sqlserver`, `azure_sql`) remain separate
and keep their fictional fixture checks. This custom profile supports Azure SQL
Database with T-SQL and ODBC Driver 18. It does not add PostgreSQL/MySQL adapters,
view/procedure execution, unrestricted SQL grammar or an accuracy guarantee.

## 1. Prepare a restricted database identity

Choose an existing database and schemas the app may read. Have an authorized
owner provision a separate runtime user and grant SELECT plus sufficient metadata
visibility (typically object-specific VIEW DEFINITION) on approved tables. A
schema-level SELECT grant approves all accessible tables in that schema; use
object-specific grants or `DB_TABLE_SCOPE` to narrow exposure.

Do not connect as `dbo`, a database owner, the SQL server administrator, or your
setup identity. Existing reader roles are supported: the guard evaluates effective
permissions inherited through roles/groups. Do not apply the synthetic seed or
`dbmind_demo_reader` provisioning script to your business database.

The custom guard rejects elevated roles, database/schema setup/control permissions,
impersonation, table/view writes (including column UPDATE grants), and user-module
execution/control. It checks visible tables/views across the database, including
outside the selected schemas, and refuses unknown mandatory permission results.
An existing identity that also writes elsewhere needs a separate reader account;
changing the query scope does not make that writer a restricted runtime identity.
[SQL effective permission checks](https://learn.microsoft.com/en-us/sql/t-sql/functions/has-perms-by-name-transact-sql)
include inherited grants. Application admin login is separate from SQL identity.

Network/firewall reachability, database authentication and SQL authorization are
separate prerequisites. The setup command does not create users/roles, grant
permissions, change firewall rules, or write to database tables.

## 2. Configure the connection privately

Install the Python 3.12 locked dependencies and ODBC Driver 18, or build the Docker
`azure` target. Create a private Docker-style `KEY=value` file outside Git (mode
0600), for example `$HOME/.config/dbmind/database.env`. Use these public settings:

```text
DB_PROFILE=azure_sql_custom
DB_DIALECT=sqlserver
DB_SERVER=YOUR_SERVER.database.windows.net
DB_NAME=YOUR_EXISTING_DATABASE
DB_SCHEMA_SCOPE=sales,dbo
DB_SCHEMA_JSON=var/custom/schema-v1.json
DB_AUTH=sql_password
DB_USER=YOUR_RESTRICTED_READER
DB_DISCOVERY_TIMEOUT_SECONDS=300
DB_SAMPLE_ROWS=5
DB_SAMPLE_VALUE_CHARS=160
DB_SAMPLE_TOTAL_BYTES=65536
APP_LLM_MODE=live
```

Add `DB_PASSWORD` through your private file/secret store, not an example or Git.
Alternatively use `DB_AUTH=managed_identity` or `azure_cli` and leave `DB_USER` and
`DB_PASSWORD` unset. Managed identity requires SQL user provisioning for the chosen
identity; CLI development also requires a restricted SQL principal, not an owner.

`DB_SCHEMA_SCOPE` is required: comma-separated user schema names (up to 16 simple
ASCII identifiers). Optional `DB_TABLE_SCOPE=sales.orders,sales.customers` limits
extraction and query targets to exactly those schema-qualified tables. Otherwise
all accessible user tables in the selected schemas are approved. Missing explicitly
listed tables or missing SELECT fail closed. Use fully qualified names in questions
when the same table name appears in multiple schemas.

Add private application credentials (`APP_USERNAME`/`APP_PASSWORD` or `APP_AUTH`),
and a private `DEEPSEEK_API_KEY` before running the UI. Custom databases require
live mode; fixture mock answers/examples are disabled. The app never loads `.env`
automatically. The setup CLI's `--env-file` reads literal Docker-format values; it
does not execute shell expressions or call the model.

## 3. Run schema and sample extraction

```bash
python -m src.setup_database --env-file "$HOME/.config/dbmind/database.env"
```

This explicit read-only step checks the runtime identity, discovers accessible user
tables, columns, primary/foreign keys, indexes and available descriptions, then
fetches up to `DB_SAMPLE_ROWS` bounded rows from each selected table. It writes the
versioned JSON snapshot and lexical index under the configured ignored `var/` path.
It prints table counts, completeness and sample status counts, not credentials or
sample values. Failed extraction preserves the previous cache for recovery; agents
will not use an invalid, expired or mismatched snapshot.

Custom samples use `TOP (n)` without random ordering or a population count, so large
tables are eligible without an expensive random sort. These are contextual examples,
not a uniform statistical sample. Read execution/output limits do not guarantee
zero server work, especially with row-level security or complex storage. Set
`DB_SAMPLE_ROWS=0` to disable row extraction. Empty tables are successful samples;
aggregate byte exhaustion is reported as incomplete sampling rather than hidden.
Unsupported driver values/read failures stop cache publication; narrow the selected
objects or disable samples after reviewing the cause.

Visibility is **accessible objects only**, not proof of a complete hidden database.
Compare the selected inventory with the database owner's expected tables; specify
`DB_TABLE_SCOPE` for an exact expected contract. Views are reported as unsupported,
not approved execution targets. Out-of-scope FK targets are recorded but not exposed
for generated queries. Limits: 500 selected tables, 1,000 visible tables/views and
1,000 visible user modules for permission inventory, and a 2 MiB snapshot. Narrow
scope/metadata grants for databases exceeding those limits.

## 4. Run the existing pipeline

For an independent local container:

```bash
docker build --platform linux/amd64 --target azure -t dbmind:azure .
docker run -d --name dbmind-custom -p 127.0.0.1:7860:7860 \
  --env-file "$HOME/.config/dbmind/database.env" dbmind:azure
```

Open `http://127.0.0.1:7860` and sign in with your application credentials. The UI
shows your configured database context instead of maintenance examples/reference
dates. Use your domain's questions; generated reads still pass the SQL gate and
effective permission guard. Follow-up revisions still require `/execute`.

Container files are disposable. The image contains no user schema/data/credentials;
startup preflight extracts/validates the container's own cache with the same settings.
For explicit extraction/checks inside a running container:

```bash
docker exec dbmind-custom python -m src.setup_database
docker exec dbmind-custom python -m src.discovery inspect
docker exec dbmind-custom python -m src.db_check --connect
```

The custom connection check probes one approved table; it has no equipment-count
or maintenance-table assumption. Refresh/preflight after schema or sampling-policy
changes. The schema shortlist and prompts consume the validated context without
replacing the multi-agent workflow. Model-generated reads can still be semantically
wrong; test representative questions against known results.

For hosting, follow [Azure deployment](azure-deployment.md) with your own resources
and this profile. Use `--mode live`; the deployer carries the custom database/schema/
table scope and sample limits into the revision. It does not seed or modify SQL.

## Data handling

Generated snapshots/indexes/failure reports stay private under ignored `var/`;
never commit or package them into an image. Live mode can send schema, descriptions,
enabled sample values, questions and limited result/feedback context to DeepSeek.
Disabling samples does not disable all context transmission. Review data policy,
provider terms, least privilege and sanitization before using sensitive data.

SQL authentication, TLS and permission failures stop safely without a local fallback.
The same authentication, admin/demo access policy, query budgets, file-route blocks
and confirmation flow apply. See [discovery](discovery.md), [configuration](configuration.md)
and [security](safety.md) for further controls.
