# Schema and sample extraction

Discovery reads the configured database catalogs and bounded sample rows. It does
not create tables, seed/reset data, launch the UI or call a model. Configure the
supported database profile and restricted reader first; see
[database setup](own-database-setup.md).

## Commands

```bash
python -m src.discovery refresh
python -m src.discovery inspect
python -m src.discovery preflight
```

`refresh` extracts metadata and new samples, then writes `DB_SCHEMA_JSON` and its
sibling lexical `.index.json`. `inspect` validates the local snapshot and prints
counts/statuses without rows or credentials. `preflight` checks the cache against
current catalog metadata, refreshing when missing, expired, changed or invalid.
Startup runs preflight too. Before each authenticated chat operation, the server
validates the cache and renews expired or invalid context using the restricted
database adapter. Renewal stays within the discovery and question deadlines,
honors cancellation and visit limits, and must produce a complete valid snapshot
before the agents proceed. Agents themselves never start discovery. SQL profiles use network/authentication for refresh
and preflight; inspect is local. Run extraction after schema changes and keep
schema stable during it: catalog reads are not one transactional database snapshot.

## Metadata scope

SQLite inspects accessible `main` user tables; SQL demos inspect `demo`. The
custom Azure profile inspects your explicit schemas and optional table subset.
Metadata includes columns/types, ordered primary and foreign keys, indexes,
defaults/computed/identity fields where available, and descriptions where present.
Views are listed separately as unsupported and are not sampled or query targets.
FK references outside the approved scope do not grant access to those objects.

Visibility is `accessible_objects_only`: hidden objects cannot be enumerated.
Missing expected fixture/explicitly selected tables or read failures produce incomplete extraction;
an incomplete snapshot cannot authorize runtime reads. The nine-table
contract remains confined to demo profiles. Custom snapshots bind the selected
profile and table subset to their identity.

## Sampling policy

| Setting | Default | Supported range |
| --- | ---: | --- |
| `DB_SAMPLE_ROWS` | 5 | 0–20 per table; 0 disables samples |
| `DB_SAMPLE_VALUE_CHARS` | 160 | 16–1,024 |
| `DB_SAMPLE_TOTAL_BYTES` | 65,536 | 1,024–262,144 |
| `DB_SAMPLE_SCAN_ROWS` | 10,000 | 1–100,000 |
| `DB_DISCOVERY_TIMEOUT_SECONDS` | 30 | 1–300; use 300 for distant Azure connections |
| `DB_SNAPSHOT_TTL_SECONDS` | 3,600 | 1–86,400 |
| `LLM_SCHEMA_CONTEXT_CHARS` | 16,000 | 2,000–32,000 |

For custom Azure SQL, samples use bounded `TOP (n)` without a count or random
sort; every selected table is eligible, subject to value/total-byte limits. They
are examples, not uniform statistical samples. The installation command is
`python -m src.setup_database --env-file /PRIVATE/PATH/database.env`.

For demo profiles, a bounded population probe checks eligible table size before sampling. Small tables
use random ordering; tables above the scan threshold are `skipped_large`. Random
ordering can scan/sort the table: row output limits alone do not bound server work.
Review thresholds/timeouts for your workload rather than enabling large-table
sampling blindly.

Inspect statuses such as `ok`, `empty`, `disabled`, `skipped_large`, `byte_limit`
and safe failure codes. Empty tables are successful reads. Sampling status/completeness
is separate from catalog completeness. Total sample-byte limits drop whole rows;
metadata has a separate 2 MiB snapshot limit. Text/binary values are bounded and
marked when truncated. Decimal, date/time, UUID and binary values use typed JSON
encodings; null remains null.

## Snapshot and agent context

Snapshots bind identity, dialect, scope, policy, extraction time, metadata, sample
rows and completeness to a versioned fingerprint/revision. Loading validates those
fields and rejects stale, mismatched, corrupted or incomplete caches. Failed refresh
preserves the previous file for recovery; it does not authorize serving stale metadata.
Writes are atomic per file; a stale/corrupt lexical index is rebuilt from its snapshot.

The lexical shortlist feeds the LLM Schema Retriever. Selected schema context
retains keys and parent relationships; the generator and feedback stages use
verified context rather than accepting unknown table names from a model. Character
budgets retain whole blocks/rows, not arbitrary slices of a relationship.

Store snapshots/indexes/failure reports under ignored `var/`; never commit, publish
or bake them into an image. Cache files are disposable and recreated after container
restart. Replicas do not share local files.

Live mode may send schema, enabled samples, questions and limited result/feedback
context to the external model. Disabling samples does not disable all database
context transmission. Review data policy and use approved sanitized data. Metadata
and sample values are untrusted model inputs; SQL permission/query guards remain
necessary even with a valid snapshot.
