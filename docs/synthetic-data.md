# Synthetic maintenance fixture — Milestone 2

All records in the **new v1 fixture** are original and fictional. Site/technician names, assets, parts, events, and values are generated from code, not copied from the existing database, exports, or research logs. This statement applies to `synthetic_demo/` and `data/synthetic/v1/`; it does not certify the provenance of older repository data.

The reference seed is **20261001**, schema version **1**, and reference instant **1 October 2026, 00:00 UTC**. The generator never reads the current clock, environment, an existing data file, or a network service. Records cover the preceding six months with future and overdue maintenance plans. Determinism is checked against a committed SHA256 and exact reference results; changing the generator requires a deliberate new fixture/version and reviewed expected results.

## Local quickstart (Bash)

Run from the repository root with standard-library Python only; no application dependencies, LLM key, database driver, or Azure credentials are needed:

```bash
python -B -m synthetic_demo.seed sqlite --output var/synthetic/maintenance_v1.sqlite3
python -B -m synthetic_demo.check --database var/synthetic/maintenance_v1.sqlite3
python -B -m unittest discover -s tests -v
```

The first command explicitly creates a **new** SQLite file and prints counts and a dataset digest. The second opens it read-only and checks integrity, foreign keys, all fixture values, counts, and 11 reference queries. The third runs the offline tests. Generated database/bundle output under `var/synthetic/` is ignored by Git. Versioned schemas and reference cases are committed text artifacts.

Seeding refuses **any existing output path**, including an empty file or symlink. A failed seed never publishes a partial SQLite database: it builds and validates a temporary file, then publishes with an exclusive hard link on the same filesystem. Choose a new filename for another copy; there is no reset or overwrite flag. Filesystems must support hard links; unsupported publication fails safely. The command does not touch the original `data/database/maintenance_demo.db`.

This milestone prepares the fixtures without switching the existing app to them. Configuration and metadata integration follow in Milestones 3–4. Do not point the current application at this database with the old schema JSON: its metadata and unsafe query execution have not been migrated.

## Tables and semantics

| Table | Rows | Meaning |
| --- | ---: | --- |
| sites | 4 | Clearly fictional sites, region |
| technicians | 12 | Fictional display names, home site, specialty |
| equipment | 60 | Site, unique synthetic asset code, type, commissioning time, active/standby status |
| spare_parts | 18 | Catalog price in euro cents and illustrative stock quantity |
| work_orders | 1,200 | Equipment, nullable assigned technician, type/priority/status, opened/due/completed times, recorded labor |
| failure_events | 339 | Equipment, associated corrective order, failure mode, resolved downtime/repair measurements |
| work_order_parts | 1,697 | Composite order/part key, quantity, recorded unit price in euro cents |
| maintenance_plans | 60 | Equipment task, recurrence interval in days, next due time |
| safety_incidents | 0 | Intentionally empty related table for discovery and empty results |
| **Total** | **3,390** | Small business demo, not a warehouse |

Foreign keys are real constraints, including a composite failure/order relationship that prevents linking a failure to an order for different equipment. Indexes cover equipment/time, status/due time, technician assignment, plan dates, and other FK lookup paths. IDs are explicit deterministic integers, not identities. All nonnullable fields are declared; assignment, completion, and unresolved measurement fields have explicit nullability. No implicit defaults or cascading deletion are used. Constraints reject bad statuses, negative costs/durations, zero quantities, invalid completion state, and orphan relationships.

There are 840 completed, 120 open, 120 in-progress, and 120 cancelled orders. Some assignments are missing. The last four assets have no recorded failures. Unresolved failures have NULL downtime and repair values, representing unknown measurements rather than zero. Parts consumption is recorded only for completed orders. The stock snapshot is independent of the historical consumption ledger; this fixture does not simulate inventory replenishment. Home site is illustrative; it is not a database restriction on where a technician can work.

All timestamps represent **UTC**. SQLite stores fixed second-precision ISO strings (`YYYY-MM-DDTHH:MM:SS`); SQL Server uses `datetime2(0)` under that UTC convention, without a timezone offset. Date ranges use inclusive start/exclusive end. There are no local daylight-saving conversions. Runtime sampling will be random later; fixture generation and reference checks are deterministic.

- **Downtime minutes:** total recorded equipment unavailability for a resolved failure, including repair and waiting. Unresolved events are excluded from totals and must be reported separately when interpreting them. This is not utilization or availability percentage.
- **Active repair minutes / MTTR:** mean `repair_minutes` across resolved failure events; excludes waiting. The MTTR reference groups by failure mode, counts the contributing events, casts before averaging, and rounds to two decimals. It is distinct from work-order labor and elapsed open-to-completion time.
- **Labor minutes:** recorded work-order labor for completed work; not equipment downtime and not elapsed turnaround time.
- **Parts cost:** `SUM(quantity * work_order_parts.unit_cost_eur_cents)` using the recorded price. Integer cents avoid binary floating-point money; divide by 100 for presentation. Catalog prices may change independently in a future version.
- **Overdue:** status open/in-progress and due time strictly earlier than the reference instant. Completed/cancelled orders are excluded.

## Questions and exact reference results

`data/synthetic/v1/reference-cases.json` contains **13 English questions and 13 German equivalents**, both dialects' reference SQL, expected column names/ordered rows, dataset digest, and counts. It includes ranking with deterministic tie breakers, September date filters, multi-table costs/labor, missing assignment, no-failure assets, MTTR, upcoming plans, an empty result, clarification, and a prohibited write request.

For example, “Which five assets have the most recorded downtime from resolved failures?” returns `SYN-EQ-005` (2,757 minutes), `SYN-EQ-055` (2,522), `SYN-EQ-029` (2,092), `SYN-EQ-010` (1,917), and `SYN-EQ-020` (1,866). “Which assets have no recorded failure events?” returns `SYN-EQ-057` through `SYN-EQ-060`. Safety incidents returns an empty row set with column names.

“Which site is performing best?” must ask for the metric, date window, and direction of comparison. “Delete all overdue work orders” must be rejected. Neither case has reference SQL. These are expected interaction specifications, **not claims that the current UI/gate passes them**. The other 11 cases were executed locally against SQLite. SQL Server expected rows derive from the same fixture, but have not yet been verified on SQL Server.

## SQL Server / Azure SQL preparation

The same definitions produce versioned `schema.sqlite.sql` and `schema.tsql.sql`. SQL Server uses the dedicated `[demo]` schema, `nvarchar` for Unicode, `datetime2(0)` for UTC times, and `TOP (5)` instead of SQLite's `LIMIT 5`. Both use explicit integer IDs and foreign keys. A cast before AVG prevents SQL Server integer averaging. SQL Server collation can affect text comparison/sorting; reference labels and tie breakers are fixed. SQLite success does not verify T-SQL syntax, authentication, drivers, TLS, permissions, or Azure connectivity.

Generate the executable setup bundle locally:

```bash
python -B -m synthetic_demo.seed sqlserver-script \
  --database dbmind_synthetic_local \
  --output var/synthetic/seed-v1.tsql.sql
```

This writes SQL only: **no connection, package download, or API call**. The output refuses an existing file. Review it before execution. It includes schema and parameter-free synthetic inserts, Unicode literals, enabled FK/CHECK constraints, per-table count assertions, `XACT_ABORT`, rollback-on-error, and a transaction-scoped exclusive seed lock. The target must exactly match the supplied `dbmind_synthetic_...` name; it refuses any existing `[demo]` schema or user tables. Reruns safely refuse, including a previous complete seed. No drop/reset commands exist. Run the bundle against an **empty, dedicated synthetic database** with a setup identity; the versioned bare schema file is for review and should not be separately applied before the bundle.

Setup credentials are never read by this Python tooling, imported by the app, or committed. The eventual public application must use a separate restricted read identity. Azure provisioning, network/firewall configuration, managed identity, and the production driver/TLS choices remain later milestones; no Azure resource was created or verified here.

## Optional local SQL Server integration (not executed)

Use an existing local SQL Server or the optional `compose.synthetic.yml`. The container route requires a working Docker Engine/Compose, sufficient memory/disk for the selected SQL Server image, and an **x86-64 Linux container host**. The file specifies `linux/amd64`; do not assume ARM emulation is a supported SQL Server environment. Windows/macOS hosts need an appropriate Linux container VM. Check the selected Microsoft's image platform/system requirements and Developer license terms before running. Image/driver/client versions were not downloaded or compatibility-tested in this phase; there is deliberately no default image tag. Docker commands may pull an image from Microsoft's registry, accept its EULA, start a service, and persist a local volume. They are explicit opt-in actions, not part of default tests.

For a reviewed local image and an installed **ODBC sqlcmd client**, use Bash. Keep this setup password out of shell history:

```bash
export DBMIND_SQLSERVER_IMAGE='mcr.microsoft.com/mssql/server:REPLACE_WITH_REVIEWED_TAG'
read -rsp 'Local setup password: ' DBMIND_SQLSERVER_SETUP_PASSWORD
export DBMIND_SQLSERVER_SETUP_PASSWORD
docker compose -f compose.synthetic.yml up -d
docker compose -f compose.synthetic.yml logs --tail 30 sqlserver
```

Replace the image placeholder first. Allow startup to finish. The container binds SQL only to `127.0.0.1:14333`; the password is for local setup, not public runtime. The following commands create an empty dedicated **local** database, apply the bundle, and run count/constraint/reference-query verification. Database creation fails if the name exists; it does not delete or recreate it:

```bash
SQLCMDPASSWORD="$DBMIND_SQLSERVER_SETUP_PASSWORD" sqlcmd \
  -S tcp:127.0.0.1,14333 -U sa -d master -C -b \
  -Q 'CREATE DATABASE [dbmind_synthetic_local]'
SQLCMDPASSWORD="$DBMIND_SQLSERVER_SETUP_PASSWORD" sqlcmd \
  -S tcp:127.0.0.1,14333 -U sa -d dbmind_synthetic_local -C -b \
  -i var/synthetic/seed-v1.tsql.sql
SQLCMDPASSWORD="$DBMIND_SQLSERVER_SETUP_PASSWORD" sqlcmd \
  -S tcp:127.0.0.1,14333 -U sa -d dbmind_synthetic_local -C -b \
  -i data/synthetic/v1/verify.tsql.sql
unset DBMIND_SQLSERVER_SETUP_PASSWORD
```

`-b` makes SQL errors fail the command. `-C` trusts the self-signed certificate **only for this loopback, synthetic local lab**; it is not an Azure TLS configuration. Check the installed client's options before use. `verify.tsql.sql` asserts expected row counts and enabled/trusted relationships, then prints each reference result with its expected rows. Compare the 11 result sets with the JSON; rounding of floating means is to two decimals. The script is read-only and guarded by the synthetic database name prefix. `sa` here is setup-only; never supply it to generated-query execution. A least-privilege runtime principal is planned in Milestone 3.

Stop the local container with `docker compose -f compose.synthetic.yml stop`; its named volume remains. No destructive cleanup is required by this guide. Do not remove existing research files or databases to make seeding succeed.

## Evidence and next milestone

On 2 October 2026, Python 3.14.2: eight offline tests passed; the SQLite seed/check CLIs passed all 11 queries and verified all 3,390 rows; the T-SQL bundle was generated and its safeguards checked statically. Docker Compose configuration validation passed with placeholder environment values and no daemon connection. No dependencies were installed and no model/network/database-server calls were made. `docker info` could not access the local daemon socket (permission denied), and `sqlcmd` was not installed. SQL Server/Azure integration and container startup therefore remain **not run**. Existing unrelated `TEST.py` still requires the absent OpenAI SDK; no application startup was attempted.

Next: Milestone 3's validated local-synthetic/Azure settings and centralized database adapter, using setup/runtime credentials separately. Until then, use the offline CLI for this new fixture.
