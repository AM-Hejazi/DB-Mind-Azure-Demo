# Azure SQL integration verification

Verified in the Codespace on 3 October 2026 through Docker and the project's
Python database adapter. Credentials were supplied only from the existing
private `~/dbmind-azure.env` file. No secret values are included in this report.

## Results

| Check | Actual result |
| --- | --- |
| Runtime identity | `dbmind_demo_runtime` |
| Database | `dbmind_synthetic_demo` |
| `dbmind_demo_reader` membership | 1 |
| `require_read_principal()` | Passed with all guards enabled |
| `dbo` USER-scope `IMPERSONATE` | 0 |
| All nine demo tables | SELECT=1; INSERT/UPDATE/DELETE/ALTER/CONTROL=0 |
| `python -m src.discovery preflight` | Exit 0; nine tables; complete=true; sampling_complete=true |
| Sampling | Eight tables `ok`; `demo.safety_incidents` `empty` |
| `python -m src.db_check --connect` | Exit 0; connected=true; equipment_count=60 |
| `python -m src.discovery inspect` | Confirmed the complete snapshot |
| Startup logs | `DB-Mind startup ready`; no startup failure in the rebuilt container |
| Host `http://127.0.0.1:7860/health/ready` | HTTP 200, body `ok` |
| Container | `dbmind-azure`, image `dbmind:azure`, healthy, left running |
| Full offline suite | 87 tests passed |
| Focused database suite | 20 tests passed |

Snapshot schema fingerprint:
`99dde6abb3232c514ea61d710f24079225bc39d36b803065b5dc14bd5c19f8fe`.

Verified image ID:
`sha256:54f1cbb5a2ffb47b71a315a7a6a07bdbb493078b1137d2389e888103fd3052f5`.

## Discovery deadline correction

The initial default 30-second preflight timed out. A read-only retry at the
former maximum of 120 seconds found all nine tables but exhausted the deadline
before completing samples; the application correctly refused to publish that
incomplete snapshot. Measured catalog and sample adapter reads took approximately
four seconds each. Discovery uses fresh guarded connections for about 47 reads
during a full refresh.

`src/settings.py` now permits a bounded discovery deadline of 1–300 seconds,
with the default still 30 seconds. A regression verifies both supported boundaries
and rejection of values outside the range. This container explicitly uses 300
seconds, with a matching Docker health startup grace period. Per-query timeouts,
permission guards, result limits, and the default five-row sampling policy remain
enforced. No broader database access was necessary.

## Local rebuild and restart

The verified container already exists and is running. To recreate it later:

```bash
docker build --platform linux/amd64 --target azure -t dbmind:azure .
docker stop dbmind-azure
docker rm dbmind-azure
docker run -d --name dbmind-azure \
  --env-file "$HOME/dbmind-azure.env" \
  -e APP_LLM_MODE=mock \
  -e DB_DISCOVERY_TIMEOUT_SECONDS=300 \
  --health-start-period=300s \
  -p 127.0.0.1:7860:7860 \
  dbmind:azure
docker exec dbmind-azure python -m src.discovery preflight
docker exec dbmind-azure python -m src.db_check --connect
docker exec dbmind-azure python -m src.discovery inspect
curl --fail --silent --show-error http://127.0.0.1:7860/health/ready
```

The snapshot resides in the container's writable filesystem; recreating the
container regenerates it through read-only preflight. The container uses Docker's
default restart policy (`no`). Mock LLM mode was explicitly selected to prevent
DeepSeek calls during verification and remains selected in the running container.

## Scope and remaining verification

All Azure operations were connection, catalog, permission, or table reads. No Azure
users, roles, firewall rules, schema, data, resources, or credentials were changed.
No Azure deployment, DeepSeek call, or MCP connection was performed.

This run proves acceptance of the actual restricted reader, effective permission
checks in its security context, real catalog extraction/sampling, and the guarded
equipment count. Negative guard tests for elevated identities, inherited
impersonation grants, unknown results, and writes remain offline regressions.
Actual elevated-identity rejection and nested-role impersonation scenarios were
not exercised against Azure because only the restricted credentials were used
and no permission changes were authorized.
