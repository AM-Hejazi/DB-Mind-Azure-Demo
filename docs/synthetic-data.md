# Fictional maintenance dataset

Use this deterministic fixture to try DB-Mind without importing a business database.
The data represents no real people, sites or events. The reference date is
1 October 2026 UTC; timestamps use inclusive-start/exclusive-end ranges. SQLite
stores ISO strings; SQL Server uses `datetime2(0)` under the UTC convention.

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


Primary/foreign keys and constraints enforce relationships and valid values.
The intentionally empty safety table tests empty results. The last four assets
have no recorded failures. Unresolved downtime/repair values are null, not zero.
Reference questions and expected results are in `data/synthetic/v1/reference-cases.json`.

## Local SQLite

```bash
python -m synthetic_demo.seed sqlite --output var/synthetic/maintenance_v1.sqlite3
python -m synthetic_demo.check --database var/synthetic/maintenance_v1.sqlite3
python -m src.discovery preflight
```

Seeding refuses an existing target. Check an existing fixture rather than deleting
or overwriting it. These operations are local and do not call a model.

## Your dedicated SQL Server/Azure SQL test database

First create an empty database named `dbmind_synthetic_YOUR_DEMO` using authorized
owner tooling. Generating its seed script is local and does not connect:

```bash
python -m synthetic_demo.seed sqlserver-script \
  --database dbmind_synthetic_YOUR_DEMO --output var/synthetic/seed-v1.tsql.sql
```

Review the generated script, then execute it against that exact empty target using
your preferred authenticated SQL client. It checks the database name, takes a
seed lock and refuses an existing demo schema or user tables. It does not create,
drop or recreate a database. Script execution creates the fictional tables/data
and is a separate explicit setup operation, never part of app startup/discovery.
Run `data/synthetic/v1/verify.tsql.sql` against the fixture and compare reference
results. Use a setup identity only for provisioning.

Next apply `data/synthetic/v1/runtime-permissions.tsql.sql`, provision a separate
restricted runtime user securely and add it to `dbmind_demo_reader` as described
in [configuration](configuration.md). Keep setup credentials out of the application.
Configure the reader environment and run discovery/preflight before serving.

For an optional local SQL Server lab, `compose.synthetic.yml` requires an explicitly
chosen SQL Server image and setup password; it binds port 14333 to loopback and
uses a persistent volume. Review image/platform requirements and licensing before
starting it. Only that loopback lab can opt into trusting a self-signed certificate;
Azure connections retain verified TLS. Do not use `sa` for runtime queries.

For independent hosting, follow [Azure deployment](azure-deployment.md). To use
another schema, follow [the adaptation guide](own-database-setup.md); fixture setup
is not a migration path for a production database.
