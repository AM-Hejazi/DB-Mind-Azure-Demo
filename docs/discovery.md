# Schema discovery and context — Milestone 4

Discovery is an explicit read operation against the configured **synthetic** profile. It does not create/reset tables, seed data, start Gradio, or call a model. It replaces the old employer-connected exporter as the supported workflow; the historical `src/db_handler.py` remains preserved and must not be imported/run.

## Commands and network effects

From the repository root, with the Milestone 2 fixture already seeded:

```bash
python -B -m src.discovery refresh
python -B -m src.discovery preflight
python -B -m src.discovery inspect
python -B -m unittest discover -s tests -v
```

`refresh` reads catalogs and new random samples, then atomically writes `DB_SCHEMA_JSON` and its sibling `.index.json`. `preflight` verifies the local cache, checks current catalog metadata, and refreshes if missing, expired, corrupted, misconfigured, or changed. `inspect` validates the local file only and prints counts/statuses/fingerprint, without rows or credentials. Tests are offline. In the default SQLite profile, all these operations are local. **In SQL Server/Azure profiles, refresh/preflight open network connections and can acquire identity tokens**; they are explicit opt-in actions. No SQL Server/Azure operation was executed in this phase.

Use preflight before serving a database session after startup/restart or an operator schema change; periodic/explicit refresh renews samples. Agents only load the valid local cache and never silently start network discovery. If it is absent or expired, they ask for refresh. Automated startup wiring remains the later canonical-entrypoint work; the optional preflight API is `preflight(load_settings())`, called explicitly after configuration in an execution path, never at import.

## Catalog scope and visibility

SQLite enumerates accessible `main` user tables and uses catalog-derived, quoted identifiers for table/index/foreign-key PRAGMAs. It captures ordered columns/types/defaults/nullability/computed flags, PK column order, composite FK mappings/actions, unique/index columns/directions, table DDL, and index DDL. SQLite descriptions and SQL Server-style type lengths/precision are not available from those catalogs; they are explicitly empty/null. Generated-column expressions and partial-index predicates remain in their original DDL rather than being parsed into separate expression fields.

SQL Server/Azure queries `sys.tables`, `sys.schemas`, `sys.columns`, `sys.types`, `sys.default_constraints`, `sys.computed_columns`, `sys.identity_columns`, `sys.foreign_keys`/`sys.foreign_key_columns`, and `sys.indexes`/`sys.index_columns` within the configured `demo` scope. It captures schema-qualified names, column order, native/alias type/schema, byte length (including `-1` for max), precision/scale, nullability, defaults, identity seed/increment, computed expressions, descriptions where `MS_Description` exists, ordered PK/composite FK mappings, unique/index keys/includes/directions/filters/disabled state, and FK trust/actions. The context adapter renders Unicode lengths in characters while retaining raw catalog byte lengths in the snapshot.

System objects are excluded. Views are listed separately as **unsupported** and are not sampled or passed to the agents as tables. Referenced FK objects outside scope remain recorded as metadata but are not pulled into agent relationship closure or treated as approved execution targets.

Visibility is explicitly **`accessible_objects_only`**. Hidden objects cannot be enumerated. Missing expected fixture tables are reported by name; sampling permission/query failures have per-table safe status codes. Discovery's restricted-principal check tolerates missing SELECT on an expected object so it can report incomplete extraction, while still rejecting visible write/setup privileges. Normal generated-query execution continues to require SELECT-only access to all nine fixture tables. Object-scoped SELECT/VIEW DEFINITION grants are prepared in `data/synthetic/v1/runtime-permissions.tsql.sql`; incomplete visibility is not evidence of a complete database schema. New accessible tables are included in discovery, but adding them to generated-query execution permissions/allowlists is separate operator work.

Catalog reads are not a single point-in-time database snapshot: do not make concurrent DDL changes during refresh. Any observed query/permission failure prevents publishing that incomplete snapshot. Restricted-reader Azure SQL catalog extraction, authentication, permissions, and sampling were integration-verified on 3 October 2026; see [results and scope](azure-integration-verification.md). Elevated-identity scenarios remain mock-validated.

## Sampling policy and types

| Setting | Default | Bounds |
| --- | ---: | --- |
| `DB_SAMPLE_ROWS` | 5 | 0–20 per table; 0 explicitly disables row sampling |
| `DB_SAMPLE_VALUE_CHARS` | 160 | 16–1,024; text/typed value size policy |
| `DB_SAMPLE_TOTAL_BYTES` | 65,536 | 1,024–262,144 total encoded sample-row bytes |
| `DB_SAMPLE_SCAN_ROWS` | 10,000 | 1–100,000 eligible table population |
| `DB_DISCOVERY_TIMEOUT_SECONDS` | 30 | 1–300 for one discovery/preflight operation |
| `DB_SNAPSHOT_TTL_SECONDS` | 3,600 | 1–86,400; cache age limit |
| `LLM_SCHEMA_CONTEXT_CHARS` | 16,000 | 2,000–32,000 schema characters per agent |

For high-latency Azure SQL connections, explicitly set `DB_DISCOVERY_TIMEOUT_SECONDS=300`
and allow the same startup grace period in the local container health check. Discovery
opens fresh guarded connections for catalog and sampling reads; real Codespace checks
measured about four seconds per read, exceeding the former 120-second maximum for a
complete nine-table refresh. The default stays 30 seconds, and runtime query deadlines,
permission checks, and sampling limits still apply.

Each table gets a bounded population probe: count rows from an inner `TOP (scan_limit+1)` / `LIMIT scan_limit+1` query. Tables exceeding that threshold get **`skipped_large`**, not an expensive random sort. Eligible small tables use `TOP (n) ... ORDER BY NEWID()` for SQL Server or `ORDER BY random() LIMIT n` for SQLite. Both can scan/sort the table: output row count alone does **not** bound server work. The row threshold and statement timeout constrain this small demo; concurrent growth after the probe can increase work, and driver/server cancellation behavior still requires integration tests. There is no claimed exact uniform sampling, no `TABLESAMPLE` guarantee, and no implicit large-table opt-in. Increase the explicitly bounded threshold only after reviewing the population/workload.

Identifiers are escaped from verified catalog schema/table/column records. UI strings never become sampling table names. Text/binary projections bound transferred values before serialization; typed values are then encoded as JSON. Null remains null; Unicode remains Unicode; decimals, date/datetime/time, UUIDs, nonfinite floats, and binary values use explicit `$decimal`, `$date`, `$datetime`, `$time`, `$uuid`, `$float`, and `$binary_hex` tags. Overlong text/binary is marked `truncated`, with a bounded prefix. SQLite dynamic numeric storage is not proof of SQL Server decimal fidelity; native Decimal/date/UUID fixtures cover encoding separately.

Per-table statuses include `ok`, `empty`, `disabled`, `skipped_large`, `byte_limit`, or safe database/error codes. Global sample-byte exhaustion drops whole subsequent rows with deterministic table ordering and sets `sampling_complete=false`. An empty table is a successful extraction with zero rows, not a database error. Query/permission/unsupported-value failures make the extraction incomplete. Global byte limits count encoded sample rows; metadata has a separate maximum **2 MiB snapshot** size. A database driver can still allocate a value before its own result cap; these policies are not a general hostile-database memory guarantee.

Runtime samples are random, so tests assert bounds, membership, types, relationships, and statuses. Mocked SQL Server fixtures and injected failures provide deterministic behavior; the test suite never asserts a particular randomly sampled equipment row.

## Versioned cache and refresh recovery

The JSON envelope is `format="dbmind.snapshot"`, `version=1`, containing:

- Explicit synthetic-only contract, dialect, database identity without credentials, and configured scope. SQLite identity hashes the resolved file path and records application/schema markers; SQL Server identity records the configured server/database.
- UTC extraction timestamp and the exact sampling policy.
- Complete discovered table metadata and a separate unsupported-views list.
- Bounded sample row dictionaries and per-table status/population-probe information.
- `schema_fingerprint` derived from catalog metadata/relationships/indexes/views, excluding samples and extraction time.
- A content `revision` binding metadata, samples, identity, policy, and time; completeness/visibility/missing-table reports.

`read_snapshot()` validates format, identity/scope/policy, completeness, fingerprint/content integrity, object scope, time/expiry, and storage size. Old list/dictionary schema exports are rejected by runtime loading, including the original warehouse/maintenance exports; there is no fallback to another database or filename. These digests detect accidental mismatch/corruption; they are not signed evidence against a local filesystem attacker. The synthetic name/marker contract likewise does not independently prove data provenance; use the original fictional generator and restrict runtime access.

Writes use a same-directory temporary file, flush/fsync, then atomic replacement. Failed extraction preserves the prior good cache and writes a separate `.failed.json` report where a catalog was successfully obtained. Failed writes also preserve the prior destination and remove temporary files. A bad current schema still makes preflight fail; the previous file is retained for recovery, **not permission to serve known-stale metadata**. An incomplete/expired cache is never consumed by agents. Startup preflight detects schema changes; changes between checks may remain cached until the next preflight/refresh or TTL boundary.

The derived lexical shortlist index is `format="dbmind.lexical_index"`, `version=1`, bound to exact source revision, metadata fingerprint, and identity. It is rebuilt after refresh and when corrupt/stale. Snapshot and index writes are separate atomic operations; if index publication fails, a subsequent load rebuilds it and never accepts an old source revision. No historical vector CSV is read, deleted, or reused. This lightweight shortlist precedes the **existing LLM Schema Retriever**, which remains responsible for semantic selection; it is not a replacement retrieval framework.

Keep generated files under the ignored `var/synthetic/` default paths. Container local files are disposable; rebuilding a cache after restart is the default. Each replica has its own cache; a local directory is not shared state. Optional operator-mounted persistence must still obey identity/expiry/fingerprint rules and is unnecessary for this small fixture. No cloud storage/service was introduced. Custom cache paths require an operator ignore rule; do not commit generated snapshots/indexes/reports or package them into the future image.

## Agent context and preserved workflow

`src/retrieval.py` now adapts only a validated canonical snapshot to the existing table/column dictionary contract. Column samples remain available for compatibility, while row samples preserve relationships between values. SQLite and SQL Server names are schema-qualified; unique unqualified aliases are resolved for compatibility. Unknown/ambiguous model-selected names or columns fail closed. The query generator's raw-model-schema fallback was removed.

Question-aware Front Desk and Schema Retriever receive a bounded lexical shortlist (at most three initial roots) plus recursive FK **parent** closure. English/German domain aliases aid the shortlist; it is deterministic, imperfect, and not a recall/accuracy guarantee. Generator/Analyzer/feedback/Translator contexts use verified selected roots, parent relationships, and key columns. Requested column subsets retain PK/FK join keys. Full discovered metadata stays on disk; explicit legacy full-schema digest calls receive a bounded full-scope context, not a data dump. Clarifier/Finalizer/legacy feedback use the same snapshot helpers; their still-missing legacy model settings are Milestone 5 work.

Context budgets retain complete metadata blocks/relationships and then add whole sample rows in stable order. Samples are explicitly marked when truncated; metadata that cannot fit raises an actionable narrowing error rather than silently cutting a join key. Template instructions and bounded JSON question/history/schema/sample data are separate system/user messages for migrated stages. Schema fields have the configured character budget; other fields have 6,000-character caps and truncation flags. This is a conservative **character budget**, not exact token accounting; JSON escaping/template text adds overhead. Transport token/call budgets remain Milestone 5.

Metadata, sample values, questions, and history are untrusted data; separating roles is defense in depth, not proof that a model cannot follow injected instructions. The restricted database identity plus Milestone 6's dialect-aware SQL gate remain necessary. In a future live pipeline, these bounded synthetic metadata/sample values leave Azure when sent to the external DeepSeek API. No credentials or unrestricted table dumps are part of context construction. Historical evaluator/log scripts remain preserved and must not be run against research logs: their explicit live transport and evidence-handling changes are later milestones.

## Validation and next step

On 2 October 2026, **29 offline tests passed**, including 18 earlier tests and 11 discovery/context tests. Actual SQLite refresh/preflight/inspect found all nine tables, including the empty safety-incidents table. Tests cover composite key order, unique indexes, Unicode/null/binary/long values, unusual typed serialization, quoted identifiers, view exclusion, population/byte limits, expiry/identity/scope/policy mismatch, changed schema, missing objects, permissions/deadline failures, atomic write recovery, index corruption/mismatch, relationship/context limits, and mocked agent message preparation. SQL Server catalog/default/identity/computed/type/index mappings and random sampling SQL were mocked only. Source compilation and Git whitespace checks passed. No live SQL Server/Azure, Gradio startup, or live LLM check was executed.

Official references checked 2 October 2026: [composite FK catalog ordinals](https://learn.microsoft.com/en-us/sql/relational-databases/system-catalog-views/sys-foreign-key-columns-transact-sql?view=sql-server-ver15) and [TOP ordering semantics](https://learn.microsoft.com/en-us/sql/t-sql/queries/top-transact-sql?view=sql-server-ver15). Documentation checks are design evidence, not SQL Server integration results.

Next is **Milestone 5**: verify current DeepSeek availability/parameters, replace inconsistent model aliases/provider inference, and implement mocked request validation, bounded retries/timeouts/output/call budgets, and opt-in live checks.
