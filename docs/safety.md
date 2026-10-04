# Security and access controls

Deploy over HTTPS with authenticated access. The supported entrypoint is `app.py`;
`main_UI.py` delegates to it. Health probes expose only minimal readiness/liveness
state and are the only anonymous routes.

## Read-only query boundaries

Runtime SQL passes a dialect-aware AST gate before opening a connection. The gate
requires a validated, unexpired schema snapshot and one supported read statement
against approved objects. Unknown tables, stacked statements, writes/DDL, EXEC,
permission changes, external access and unsupported constructs/functions are
rejected. Parsing alone is not proof of semantic correctness.

SQLite uses a read-only connection, query-only mode and an authorizer blocking
writes, attachment and extension loading. SQL Server/Azure SQL use verified TLS
and an effective restricted-principal check, including inherited write/elevation/
impersonation grants. Never give the runtime identity setup or database-admin roles.
The application admin account uses the same database identity and query guards.

Results are bounded by row/byte limits and query timeouts. Empty results are
successful reads. Feedback revisions and Analyzer repairs are proposals requiring
`/execute`; confirmation revalidates them. Superseded proposals cannot run. Repair
is bounded; connectivity failures and empty reads do not trigger follow-up repair.

## Application credentials

Use either `APP_USERNAME` plus `APP_PASSWORD`, or `APP_AUTH` containing comma-separated
`username:password` pairs. Do not combine the two forms. Usernames are unique and
use letters, digits, underscore or hyphen; passwords must be 12–256 characters.
Keep secrets in an external private environment file or secret store, never Git,
Docker build arguments, URLs or screenshots. Passwords are not supplied by the repo.

HTTP Basic authentication protects every application route. Use TLS for remote
hosting. Visitors cannot choose the database identity, endpoint, model or access
role through chat or request payloads. Server sessions belong to the authenticated
user and opaque browser visit; session hashes are not authorization credentials.

## Demo visits and quotas

An opaque HttpOnly, SameSite=Strict cookie identifies each browser visit; cookies
are Secure for remote hosts. Visits expire after `APP_SESSION_TTL_SECONDS` (default
1,800, supported range 60–1,800). Reloads, activity and new questions do not extend
the deadline. Expiry is checked before queued work, database reads and model attempts.
A shared account cannot enforce permanent per-person expiry: another browser or
cleared cookie can create a new visit.

| Setting | Default |
| --- | ---: |
| `APP_QUESTIONS_PER_HOUR` | 2 per visit |
| `APP_QUESTIONS_PER_DAY` | 10 per visit |
| `APP_MODEL_CALLS_PER_HOUR` | 24 per visit |
| `APP_GLOBAL_MODEL_CALLS_PER_DAY` | 120 across the process |
| `APP_REQUESTS_PER_HOUR` | 240 action/queue requests per visit |
| `APP_MAX_SESSIONS_PER_USER` | 4 active sessions per visit |
| `APP_QUEUE_SIZE` | 8 |

Page/asset loads and deadline polling do not consume action allowances. A single
operation runs at a time. Quotas/sessions are in memory: use one process and one
replica; restart resets counters and invalidates visits. Persistent account-level
limits and distributed state are required before scaling or promising per-person
access limits.

## Optional application admin

Configure a separate `APP_ADMIN_USERNAME` and `APP_ADMIN_PASSWORD` together, with
validated credentials and a username different from demo accounts. There is no
default admin or privilege based solely on the word “admin.”

Admin visits have no fixed deadline and bypass individual question/model allowances.
Global model spending limits, HTTP rate limits, queue/session bounds, per-question
budgets and every SQL/file-route guard still apply. Admin grants no database
administration or management route. Use a separate private browser window and
keep this password separate from shared demo credentials.

## File, error and model controls

All Gradio file/download/upload/proxy routes remain blocked, including encoded
paths. The trusted logo is embedded as a data URI; displaying it does not require
file-serving permissions. Cross-origin requests and oversized bodies are rejected.
Visitors receive safe error messages rather than driver/provider exception dumps,
credentials or internal reasoning. UI callbacks do not return log files.

Model attempts are charged before network calls; retries count. Per-question budgets
and cancellation discard late responses. Remote cancellation is best effort, so a
provider may still bill a cancelled request. Configure provider billing limits
separately. Questions, schema, enabled samples and limited result/feedback context
can leave the database network in live mode. Review data policy before using it.

For secret rotation and restart instructions, see [Azure operations](azure-deployment.md#rotate-an-application-password).
