# Query safety and private access — Milestone 6

The supported serving path now requires authenticated private access on **every application HTTP route** (only the minimal startup/liveness/readiness probes are anonymous), owns session state on the server, and sends all runtime SQL through the same conservative AST gate. Query results, feedback proposals, recovery, and model attempts have bounded allowances. This is the initial single-process recruiter-demo design, not a public anonymous endpoint.

## SQL gate and database protection

`Database.query()` gates every runtime read before opening a connection. `validate_sql()` is its compatibility adapter for existing CLI/evaluation callers. Generator, Analyzer correction, and explicitly confirmed feedback queries all use this path. Trusted discovery alone uses the separate internal `metadata=True` path; that argument is not a browser/API input. The historical employer-connected exporter and retired unauthenticated UI handlers/entrypoints now stop before imports/connections. Their original bodies remain preserved below the stop, not deleted or silently adapted to an employer database.

`src/sql_gate.py` uses **SQLGlot 30.21.0**, pinned in runtime requirements, with the configured SQLite/T-SQL dialect. Missing parser or missing/invalid/expired snapshot disables runtime reads. It requires one supported SELECT, including nonrecursive CTEs, derived/correlated subqueries, joins, grouping, and aggregations. A positive constant TOP/LIMIT is supported; an AST-generated cap of `DB_MAX_RESULT_ROWS + 1` detects overflow without prefixing text or breaking CTE/aggregate syntax. Smaller explicit limits are retained.

An explicit AST construct/function allowlist rejects uncertain syntax rather than assuming every parsed SELECT is harmless. Writes/DDL, SELECT INTO, stacked statements, procedures/EXEC, permission changes, system objects, external/table functions, multipart database/server references, user functions/types, session variables, locking hints/options, recursive CTEs, UNION/set operations, OFFSET, TOP PERCENT/WITH TIES, and unsupported functions fail closed. The supported grammar is deliberately smaller than either complete SQL dialect. Library warning output is suppressed so unsupported input is not dumped into diagnostics.

Physical sources resolve against the **intersection of the valid discovered scope and the nine approved fixture tables**. Accessible extra discovery tables and views do not become runtime targets automatically. CTE/derived sources are distinguished by scope analysis. Unknown/ambiguous columns are rejected through qualification against discovered column names. Catalog-derived names are quoted and the validated AST is rendered back to the configured dialect; original comments/input fragments are not passed through. Application-controlled values use matching positional `?` parameters; model-authored literals are part of the parsed SQL, not interpolated application strings.

Database enforcement remains the primary backstop. SQLite uses `mode=ro`, `query_only`, fixture markers, and an authorizer restricting runtime reads to fixture objects while refusing writes/attachments/unsafe PRAGMAs/extensions. SQL Server uses the encrypted, certificate-verified connection and effective restricted-principal checks from Milestone 3; setup and runtime identities stay separate. Provision only the object-specific SELECT/VIEW DEFINITION role in `data/synthetic/v1/runtime-permissions.tsql.sql`, with its DENY write/setup/EXECUTE grants. Read-only intent is a connection hint, not a permission boundary. SQL Server permissions and T-SQL execution remain integration-unverified.

## Deadlines, results, and recovery

Defaults remain 15 seconds, 1,000 rows, and 1 MiB per database query, configured through Milestone 3 settings. Column bytes count too; limits fail with a safe narrowing error rather than silently returning partial data. The current UI uses a read-only Dataframe bounded by the transport row/byte limits, with HTML-escaped conversation text and expandable gate-rendered SQL. Original Milestone 6 preformatted previews were replaced in Milestone 7. Credentials, connection markers, URLs/internal host patterns are redacted. Raw driver/provider exceptions are not shown.

Database work runs on a daemon worker with a caller deadline. Cancellation requests SQLite `interrupt()` or SQL Server cursor `cancel()` and discards late results, with no blocking executor-shutdown join. SQLite also checks a progress handler; fetching checks deadline/cancellation. Connections/cursors close in the worker's `finally` path. Connection retries are bounded and never replay SQL; cancelled operations do not begin a new connection retry. An in-progress driver connect or a driver that ignores cancellation can outlive the caller. SQLite interruption was exercised; SQL Server cancellation/cleanup was mocked, not proven against a server.

The private service allows **three query attempts per question**, including the initial read, at most one correction, and confirmed feedback. Recovery happens only after an actual query error; permission/safety/identity/timeout/result-limit errors stop. A successful empty/zero result is a business result, never a reason for another model call. The historical batch runner's empty-result correction behavior was also removed. Question follow-ups are capped at five and share the original model budget/deadline. Model retries consume the server allowance too.

Feedback `<RUN_SQL>` output is a **proposal**, never execution permission. The service validates and stores a unique proposal on the server, presents it, and requires a separate exact `/execute` message. Execution consumes the pending proposal once and revalidates it through `Database.query()`. Duplicate tags, unknown objects, changed/expired snapshot, absence of a pending proposal, or exhausted budgets fail closed. No client-provided SQL/history/state field can authorize execution.

## Authentication, quotas, and sessions

Configure process environment using either `APP_USERNAME` plus `APP_PASSWORD`, or `APP_AUTH=user1:password1,user2:password2`. Usernames are unique/simple; passwords are 12–256 characters and hidden from configuration repr. Credentials come only from operator settings. The serving factory refuses missing credentials before building a UI. Use TLS for any remotely accessible deployment; HTTP Basic is suitable for the loopback development server and a TLS-protected private demo, not plaintext remote HTTP. Each recruiter should have a separate username: shared accounts share allowances and sessions.

`SecureASGI` validates HTTP Basic before **all** Gradio/FastAPI routes, including config, info, queue, API, file and login paths. Its verified principal feeds Gradio's `auth_dependency`; callback usernames are injected by Gradio, never accepted from a JSON username field. Cross-origin browser requests are refused unless Origin matches Host; missing Origin remains valid for authenticated CLI clients. Untrusted forwarded headers are not used to establish identity. A future reverse proxy must preserve the intended Host and supply TLS.

The server policy owns sessions by authenticated user plus session hash. Reusing another user's hash is refused for request bodies and SSE query parameters before Gradio, and again at callback lookup. History, pending SQL, budget, results, and logger are server-owned; callback inputs are only the new question and the framework-injected Request. Chat history/Gradio state are not inputs and cannot carry forged file payloads. Duplicate/concurrent work uses a per-session lock; a server semaphore admits one application operation across accounts.

| Setting | Default | Meaning |
| --- | ---: | --- |
| `APP_QUESTIONS_PER_HOUR` | 2 | Per authenticated user, rolling hour |
| `APP_QUESTIONS_PER_DAY` | 10 | Per authenticated user, rolling 24 hours |
| `APP_MODEL_CALLS_PER_HOUR` | 24 | Per user; every attempted completion/retry |
| `APP_GLOBAL_MODEL_CALLS_PER_DAY` | 120 | Across users, rolling 24 hours |
| `APP_REQUESTS_PER_HOUR` | 240 | All authenticated HTTP requests per user |
| `APP_MAX_SESSIONS_PER_USER` | 4 | Bounds active server session storage |
| `APP_SESSION_TTL_SECONDS` | 3,600 | Idle session expiry/cancellation |
| `APP_QUEUE_SIZE` | 8 | Maximum queued UI events |

Session resets/new browser tabs do not reset account allowances. Counts use monotonic rolling windows, not a midnight reset. Inactive sessions expire; the user count is limited to 20 configured identities. Request bodies have a 64 KiB cap and a five-second read deadline. WebSockets, unused stream/call/component-server routes, uploads, proxies, and download/file routes are refused. The SSE queue path remains supported. Events share concurrency one, expose no named client API, and `api_open=False` prevents direct `/run`/`/api` requests bypassing the queue. Hiding API docs alone would not provide these protections.

Model calls require a trusted private or explicit local operator context, in addition to Milestone 5's 12-attempt/300-second default pipeline allowance. Private calls charge the policy before network execution. Local CLI evaluation/smoke entrypoints establish an operator context explicitly and retain operation budgets; they are not browser callbacks. The limits constrain attempts, not an exact dollar invoice. Lost responses/remote work continuing after cancellation can have unknown charges. A cancelled physical worker may outlive the single active application operation; no hard remote concurrency/cancellation guarantee is claimed.

**Single-process limitation:** quotas and session ownership are in memory. A process restart clears them; multiple workers/replicas would maintain independent allowances and incompatible session registries. Run one process/replica for the initial demo. Do not enable autoscaling/multiple Uvicorn workers without shared authenticated quotas/session infrastructure. Account limits are a private-demo safeguard, not comprehensive protection against stolen credentials or distributed abuse.

## Diagnostics and files

The old mutable global logger is replaced by a context proxy; each server session has its own logger. Default diagnostics retain bounded event names only, dropping supplied prompts, question/result/error bodies and connection details. No stdout dump of stage content/config occurs. Optional explicit saves are restricted to random filenames in ignored `var/diagnostics/`; legacy arbitrary logger paths are not saved. Existing research logs/data are preserved and remain outside the runtime context.

UI callbacks always return no log file. Native Gradio allowed paths are empty and the workspace is blocked; the outer middleware also denies all file/download/upload/proxy routes, including encoded paths. Authentication applies to static/UI routes too. The old file-based logo/avatar paths therefore need the Milestone 7 asset/UX cleanup; they are not an exception granting workspace access. Do not restore broad file permissions to repair an icon.

## Run and test locally

Imports no longer build/launch the app, and `main_UI.py` delegates to the same guarded entrypoint. A minimal factory refactor happened here because authentication must wrap the entire app; the fuller UX/settings/services factory work is still Milestone 7.

```bash
# Explicit seed/preflight are separate; never reset automatically.
python -B -m src.discovery preflight

# With private credentials already supplied securely in process environment:
python -B app.py
# Same guarded launch:
python -B main_UI.py
```

Startup preflight reads the chosen database. It is local in the default SQLite profile; SQL Server/Azure startup has network/authentication effects. Launch binds loopback only, uses Uvicorn, creates no public Gradio share tunnel, and disables access-log dumps. No live server listener/public tunnel/deployment was started during this milestone. Remote hosting/TLS/proxy configuration remains later deployment work. The current UI still needs Milestone 7 copy/assets/progress/clarification acceptance; it is not yet declared ready for publication.

To reproduce the focused test environment (package setup contacts PyPI; tests do not incur model charges):

```bash
python3.12 -m venv var/milestone6-test-env
var/milestone6-test-env/bin/python -m pip install -r requirements-test-ui.txt
GRADIO_ANALYTICS_ENABLED=False HF_HUB_OFFLINE=1 \
  var/milestone6-test-env/bin/python -B -m unittest discover -s tests -v
```

If that Python lacks ensurepip, create the environment with `--without-pip` and use the already installed pip's `--python var/milestone6-test-env/bin/python` option. Avoid OS/package-manager changes. In this environment socket restrictions stalled even the minimal FastAPI thread/event-loop control; in-memory HTTP tests ran under approved sandbox escalation. This allowed local event-loop wakeups, not a live external-service check.

On **2 October 2026, 72 tests passed** on Python 3.12.3: 49 prior tests, 16 gate/transport/policy/service tests, and seven actual Gradio 5.31.0 HTTP/queue tests. They cover the 11 grounded reference reads, an attack matrix, CTE/bracket/comment/parameter/column handling, SQLite deadlines and empty results, mocked SQL Server TOP/cancellation, quotas/reset/replay, isolated diagnostics, proposal confirmation, anonymous/config/queue/API/file/upload/proxy routes, body limits, cross-origin rejection, and trusted-user injection despite forged JSON username. Queue work used mocked model stages; provider/SQL Server calls were patched to fail. Source compilation and whitespace checks passed.

The isolated test environment uses the repository's Gradio/client/FastAPI/Pydantic/AnyIO/Hugging Face pins plus SQLGlot. Transitive dependencies were resolved separately, not a successful installation of the original 143-pin production requirements or a final lockfile. Milestone 8 will resolve/package the supported runtime. SQL Server/Azure/ODBC permission and physical cancellation behavior, live LLM accuracy/cost, browser visual interaction, and production hosting remain unverified. Tests are evidence for the supported subset, not a full security audit.

Primary references used: [SQLGlot parser/scope framework](https://github.com/tobymao/sqlglot), [Gradio 5.31 route authentication/queue/file implementation](https://github.com/gradio-app/gradio/blob/gradio%405.31.0/gradio/routes.py), and [Gradio 5.31 Blocks queue/API behavior](https://github.com/gradio-app/gradio/blob/gradio%405.31.0/gradio/blocks.py). Implementation tests, rather than documentation alone, establish the route behavior above.

Next: **Milestone 7 — focused Gradio UX, correct synthetic/dialect copy, progress and clarification, safe assets, configured labels, and the canonical injected factory.**


## Final migration update — 3 October 2026

The final runtime is Gradio 6.29.1/client 2.7.2 with native private API visibility, tested HTTP/queue boundaries and no schema monkeypatch. Anonymous health probes expose only readiness memory and never contact SQL/LLM. Frozen injected settings, bounded stage streaming, compact Dataframe results and real desktop/mobile checks replace the original Milestone6 UI. See [migration-report.md](migration-report.md) for the current 77-test evidence, hashed dependency audit and Docker results; earlier milestone version/install descriptions are historical.
