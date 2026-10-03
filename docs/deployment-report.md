# DB-Mind Azure demonstration acceptance

Verification date: 3 October 2026. This is engineering acceptance over entirely
fictional maintenance data, not a thesis accuracy result.

Application: https://dbmind-demo.lemonflower-af0fa12b.southafricanorth.azurecontainerapps.io

Public source: https://github.com/AM-Hejazi/DB-Mind-Azure-Demo

The [release receipt](https://github.com/AM-Hejazi/DB-Mind-Azure-Demo/releases/latest)
records the original acceptance source commit and immutable ACR digest.
The recruiter-session update below supersedes that runtime image.
Recording that receipt outside the source tree avoids a self-referential commit.

## Recruiter-session update — 3 October 2026

The existing application now serves revision `dbmind-demo--0000007`. Its tested
runtime source is `03065026b71674bfad9ec659fe905a2bd1b9ee4c`; the immutable image
and sanitized checks are recorded in [the session deployment receipt](deployment-session-receipt.json).
The image-only update preserved the owner's current login secret. No database
users, roles, firewall, schema or data were changed; no additional resources
were created and no DeepSeek request was made for this update.

Each authenticated browser visit has a fixed 1,800-second deadline. Activity,
reloads and new questions cannot extend it. At expiry the controls are removed
and the message reads: “Your 30-minute demo session has ended. Please contact the
developer to request more access.” Server checks block expired requests, queued
work, new database reads and model attempts. Request/question/model allowances
are separated by browser visit, while the global daily model cap and serialized
operation capacity remain in place. Assets, config, page refreshes and deadline
polling do not consume the action allowance.

The full offline suite passed **99 tests**, and [code CI passed](https://github.com/AM-Hejazi/DB-Mind-Azure-Demo/actions/runs/37146598453).
A local Chromium test with an accelerated server clock verified visible expiry,
blocked post-expiry requests, and a second independent browser visit. Hosted
Chromium verified the current password, active timer, retained deadline after
reload, original logo, mobile layout without overflow and no JavaScript errors.
The hosted cookie is `Secure`, `HttpOnly`, and `SameSite=Strict`, including when
Azure terminates HTTPS before the application's HTTP backend. Readiness returned
200, anonymous config 401, authenticated config 200, and the file route 404.

This is a limit per **browser visit**, not a permanent limit per person. A shared
password cannot identify recruiters: clearing cookies or switching browsers can
start another visit. Strict limits per recruiter require individual accounts
and persistent account expiry. Usage caps may stop a visit before 30 minutes.
Visits are in memory; process restart invalidates old visit cookies. Open a fresh
private browser window once after this update to avoid an obsolete cookie.

## Hosted database evidence

Checks ran inside the deployed Container App through the project's Python/ODBC
adapter, using the existing restricted SQL credentials held in Azure secret
references. No database was recreated or seeded, and no SQL users, roles,
firewall rules, schema, or data were changed.

| Check | Observed result |
| --- | --- |
| Effective profile / dialect | `azure_sql` / `sqlserver` |
| Database | `dbmind_synthetic_demo` |
| Current user | `dbmind_demo_runtime` |
| EngineEdition | `5` (Azure SQL Database) |
| `dbmind_demo_reader` membership | `1` |
| Effective identity and nine-table permission guards | Passed |
| Discovered tables / metadata / bounded sampling | Nine / complete / complete |
| Equipment count | `60` |
| Transport | Driver 18, Encrypt=yes, TrustServerCertificate=no |
| HTTPS readiness / anonymous config | `200` / `401` |

The operator-only diagnostic module also exercises a SQL conversion error, a
write rejected by the visitor SQL gate before execution, and a fresh TLS
connection with an intentionally empty CA trust store in an isolated child
process. Normal configuration and system certificate files remain untouched.
Acceptance requires safe adapter errors and a subsequent successful count of 60;
there is no SQLite fallback.

These failure checks passed in the hosted container. A trusted read-only probe
of `sys.dm_db_resource_stats` additionally produced an actual permission denial
(SQLSTATE 42000). The adapter returned a safe `query` error, then the normal
equipment count remained 60. This view requires
[VIEW DATABASE STATE](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-db-resource-stats-azure-sql-database),
which the demo does not need and was not granted.

## Tests and frontend acceptance

- 91 offline tests passed in the clean checkout.
- 91 tests passed inside the non-root Azure image with outbound networking
  disabled and test-only files mounted read-only.
- The new HTTP regression reproduces rejection with the old 16-connection limit
  and passes with 128 bounded connections and two-second keep-alive retention.
  Query processing remains serialized; authentication, quotas, and file guards
  are preserved.
- The image build installs the hashed runtime lock and passes `pip check`.
- Hosted UI acceptance checks the existing work-order status reference question
  and compares the copied result rows with the synthetic generator: cancelled
  120, completed 840, in_progress 120, open 120.
- Desktop/mobile screenshots show actual hosted synthetic results. The original
  embedded PNG retains its aspect ratio, with 48px/36px heights; mobile has no
  horizontal page overflow. The browser check blocks third-party browser requests.

Mock and live browser acceptance both passed, with no JavaScript errors or
third-party browser requests. The live test used `deepseek-flash`, submitted one
reference question and one clarification follow-up (all recorded dates, no date
filter), then compared all four result rows successfully. No evaluation benchmark
or additional live provider health check was run. Actual billed token cost was
not measured.

The final documentation/screenshots commit uses the same tested runtime files.
The 53-file runtime fingerprint (app, config, src, prompts, generator and trusted
data assets) is `92f6a1720cc2c92d92d12f75af2257c052f9f14ae83abd2a6ca467d4e043bed5`.
Final hosted checks compare that fingerprint and record the final image/commit
in the release receipt; they do not repeat the paid conversation.

## Resources and recurring cost

All resources are in `rg-dbmind-demo`, South Africa North.

| Resource | Configuration | Cost/control |
| --- | --- | --- |
| Existing `amirmohsen` SQL server and `dbmind_synthetic_demo` database | GP_S_Gen5 serverless, capacity 2, minimum 0.5, auto-pause 60 minutes | Existing free-limit offer, AutoPause at exhaustion; owner must check eligibility/balance |
| `dbminddemo659f6e7f` ACR | Basic, administrator access disabled | About $5 per 30 days; excess registry storage can add cost |
| `dbmind-demo-runtime` identity | Registry-scoped AcrPull only | No separate identity service charge |
| `dbmind-demo-env` | Consumption, no retained Log Analytics store | Compute/request metering belongs to the application |
| `dbmind-demo` | Single revision, min=max=1, 0.5 vCPU / 1 GiB, HTTPS port 7860 | About $10–33.48 per 30 days with unused free allowances |

Combined new Azure hosting is approximately **$15–39 per 30 days**, excluding
DeepSeek, taxes, discounts, excess storage and egress. Active/idle metering and
remaining monthly allowances determine the bill; this estimate is not a spending
cap. Paid non-free Gen5 serverless compute would be $0.699156/vCore-hour in this
region, plus applicable storage/backups. No SQL tier/free-offer settings changed.
Rates were obtained from the [official Azure retail-price API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices);
[Container Apps billing](https://learn.microsoft.com/en-us/azure/container-apps/billing)
documents monthly free allowances. No NAT gateway, private endpoint, dedicated
workload profile, VNet, or retained log workspace was added.

The existing Azure-service SQL firewall rule `0.0.0.0`–`0.0.0.0` admits Azure-service
connections, including other tenants, subject to authentication. Existing operator
rules remain intact. No all-internet rule was added. Stricter network isolation
requires a separately reviewed stable-egress/private-network design and budget.

## Cleanup, operation and remaining limits

[Cleanup manifest](cleanup-manifest.md) lists omitted historical exports,
schemas, embeddings, research logs, backups, certificates and migration residue,
and explains retained compatibility/evaluation entrypoints. Fresh history starts
with the clean demo; the original repository's branches, history and visibility
are preserved. The public audit found no high-confidence credential patterns,
forbidden assets, or exact private credential values; no required key rotation
was identified by that scan. This is not an exhaustive secret-format guarantee.

[Deployment guide](azure-deployment.md) gives audit, test, digest build/push,
secure deployment, verification, rollback and cost-control instructions. Startup
permits 40 failures at 30-second intervals (1,200 seconds), exceeding three
300-second discovery attempts plus two ten-second retry waits and initialization
headroom. Actual Azure validation required probe failure thresholds at most 48.

Registry pulls use managed identity. SQL continues to use the existing restricted
reader because no Entra SQL administrator was configured during inspection.
The guide provides exact owner SQL for provisioning the identity into the existing
reader role; switching SQL authentication remains an optional administrator step.
No administrator password or broader database permission is needed for this demo.

Real elevated/nested-role impersonation rejection has offline regression coverage;
no elevated SQL identity was provisioned for live testing. Provider token cost,
billing limits, generalized answer accuracy, load endurance and SQL managed
identity authentication were not measured. The hostname-override TLS experiment
was inconclusive with this Azure/Driver 18 path; the empty-trust-store failure is
the actual certificate-verification acceptance check.

Discovery can be slow, SQL can pause at free-offer exhaustion, readiness records
startup rather than continuous SQL availability, and sessions/quotas reset with
the single process. Keep one process/replica. Invitees need private credentials;
the public repository does not make the application anonymous.

GitHub Codespaces cannot disable its idle timeout. The interrupted Codespace
remained at 30 minutes; the CLI/API did not change it. An owner can set a maximum
four-hour default for new Codespaces in personal settings. Codespace shutdown
does not stop Azure hosting. Missing local Docker layers after a restart required
a fresh isolated BuildKit builder; existing Azure resources remained running.
