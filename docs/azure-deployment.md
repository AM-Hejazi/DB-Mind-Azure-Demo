# Azure deployment and operations

This demo reuses the existing synthetic Azure SQL database. Do not run a seed,
recreate the database, overwrite existing firewall rules, or publish private
configuration. The original repository remains independent of this clean checkout.

## Reviewed architecture

- Resource group: `rg-dbmind-demo`; region: `southafricanorth`.
- Existing SQL: `amirmohsen.database.windows.net`, `dbmind_synthetic_demo`.
- Basic ACR: `dbminddemo659f6e7f`, admin access disabled.
- User-assigned identity: `dbmind-demo-runtime`, registry-scoped `AcrPull`.
- Consumption environment: `dbmind-demo-env`; app: `dbmind-demo`.
- One 0.5-vCPU/1-GiB replica, HTTPS ingress port 7860, single revision mode.
- No paid VNet/NAT/private endpoint or retained Log Analytics workspace.

The existing `AllowAllWindowsAzureIps` SQL rule is `0.0.0.0`–`0.0.0.0`, meaning
Azure-service connections from other tenants are network-admitted too. It is not
an all-internet `0.0.0.0`–`255.255.255.255` rule. This deployment preserves it and
all existing operator rules. Database authentication and effective read-only
permission checks remain required. For stricter network isolation, separately
budget and design a stable NAT egress allowlist or private endpoint; do not use
the Container Apps inbound IP as an outbound address.

## Tools and initial infrastructure

Install the official Azure CLI, Docker, and Python 3.12. Sign in with `az login`,
verify `az account show`, and select the intended subscription. Device flow can
be blocked by Entra security defaults; use browser authentication and MFA.
Review existing resources/names before creating anything. For a new demo group
configuration, adjust the names/region deliberately; never overwrite unrelated apps.

```bash
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.ContainerRegistry
az provider register --namespace Microsoft.ManagedIdentity
az extension add --name containerapp
az acr create -g rg-dbmind-demo -n dbminddemo659f6e7f -l southafricanorth --sku Basic --admin-enabled false
az identity create -g rg-dbmind-demo -n dbmind-demo-runtime -l southafricanorth
az containerapp env create -g rg-dbmind-demo -n dbmind-demo-env -l southafricanorth --logs-destination none --enable-workload-profiles true
```

Assign only `AcrPull`, scoped to this registry, to the identity's principal ID.
Existing infrastructure does not need to be recreated for updates.

## Build, audit, push, deploy

Keep `~/dbmind-azure.env` private and outside the checkout. It uses Docker
`KEY=value` syntax and contains the restricted SQL credentials, APP_AUTH (or
APP_USERNAME/APP_PASSWORD), and DEEPSEEK_API_KEY. No credentials belong in build
arguments, Dockerfiles, public parameters, screenshots, source, or shell history.

```bash
python -m scripts.audit_public --private-env "$HOME/dbmind-azure.env"
GRADIO_ANALYTICS_ENABLED=False HF_HUB_OFFLINE=1 APP_LLM_MODE=mock python -B -m unittest discover -s tests -q
git status --short
# Commit the audited source before building.
DBMIND_COMMIT=$(git rev-parse HEAD)
docker build --platform linux/amd64 --target azure --label org.opencontainers.image.revision="$DBMIND_COMMIT" -t dbmind:azure-demo .
az acr login --name dbminddemo659f6e7f
docker tag dbmind:azure-demo "dbminddemo659f6e7f.azurecr.io/dbmind:$DBMIND_COMMIT"
docker push "dbminddemo659f6e7f.azurecr.io/dbmind:$DBMIND_COMMIT"
DBMIND_DIGEST=$(az acr repository show -n dbminddemo659f6e7f --image "dbmind:$DBMIND_COMMIT" --query digest -o tsv)
python -m scripts.deploy_azure --resource-group rg-dbmind-demo \
  --registry dbminddemo659f6e7f --environment dbmind-demo-env \
  --identity dbmind-demo-runtime --source-commit "$DBMIND_COMMIT" \
  --image "dbminddemo659f6e7f.azurecr.io/dbmind@$DBMIND_DIGEST" --mode mock
```

The checked-in ARM template declares credentials as `securestring` parameters.
The Python deployer reads the private file in memory and submits it over HTTPS
with the signed-in CLI token; no plaintext parameter file is created. Azure
stores application authentication, DeepSeek key, and optional restricted SQL
password as app secrets referenced by environment entries. Registry access uses
managed identity. Do not run CLI/debug HTTP tracing on this secret-bearing request.

Azure settings are explicit: APP_HOST=0.0.0.0; DB_PROFILE=azure_sql;
DB_DIALECT=sqlserver; DB_SCHEMA_SCOPE=demo; DB_DISCOVERY_TIMEOUT_SECONDS=300;
DB_SQLITE_PATH absent. The same image supports mock and live mode.

## Startup allowance and acceptance

`app.py` performs at most three 300-second discovery attempts with two ten-second
retry delays: **920 seconds**, plus initialization overhead. The Azure HTTP Startup
probe uses period 30 seconds and failureThreshold 40, providing approximately
**1,200 seconds**. This is a Container Apps probe configuration, independent of
Docker's HEALTHCHECK grace period. Readiness uses `/health/ready`; liveness uses
`/health/live`. Neither recurring endpoint calls SQL or a model.

Run inside the deployed Container App (CLI opens an authenticated terminal):

```bash
az containerapp exec -g rg-dbmind-demo -n dbmind-demo --command /bin/sh
# In the container:
python -m src.discovery preflight
python -m src.db_check --connect
python -m src.runtime_diagnostics --negative-checks
```

Diagnostics are an explicit trusted read-only operator path, not a visitor API.
They retain the runtime guard and verify EngineEdition=5, the current database,
reader membership, nine tables, complete metadata/sampling, and equipment count60.
Do not weaken the visitor SQL gate to run identity diagnostics.

Verify HTTPS readiness200, anonymous application/config401, protected file routes,
and an authenticated synthetic reference answer in mock mode. Then redeploy the
same digest using `--mode live` and run one small authorized UI reference question.
Compare its result with `data/synthetic/v1/reference-cases.json`. Live mode incurs
DeepSeek charges; do not run historical evaluation loops as smoke tests.

For optional browser acceptance, install Playwright separately from the runtime:

```bash
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python -m scripts.hosted_smoke --url https://YOUR-APP.azurecontainerapps.io --mode mock
```

On Linux, install the browser's system dependencies if prompted. After switching
to live mode, `--mode live` submits one reference
question and at most two clarification/confirmation follow-ups. It compares the
actual CSV result export and captures desktop/mobile screenshots. Credentials are
read from the private environment file in memory; never put them in the URL.

## Optional managed-identity SQL transition

The initial deployment uses the existing restricted `dbmind_demo_runtime` SQL
user via a secret. Azure management ownership alone does not grant SQL access.
The server initially has no Entra administrator. An owner must configure an
appropriate Entra administrator and execute this as that administrator in
`dbmind_synthetic_demo`:

```sql
CREATE USER [dbmind-demo-runtime] FROM EXTERNAL PROVIDER
  WITH OBJECT_ID = 'f6d0de6c-5ebe-4540-b10c-d558528ac7f6';
ALTER ROLE [dbmind_demo_reader] ADD MEMBER [dbmind-demo-runtime];
```

Identity client ID: `76644597-f9bc-4718-bbd6-2f18c8c557c0`. Grant no broader roles.
After provisioning, repeat deployment with `--db-auth managed_identity`; the
container omits SQL credentials and explicitly requests this managed identity.
Verify inside the deployed app before declaring the transition complete. Do not
send an administrator password in chat or put it in the application environment.

## Cost and troubleshooting

Current South Africa North USD retail estimates, 3 October 2026, before taxes,
account discounts, and DeepSeek: Basic ACR $0.1666/day (~$5 per 30 days); a constant
0.5-vCPU/1-GiB Consumption replica roughly $10 idle to $33.48 active per 30 days
when monthly free allowances remain available. Combined hosting is approximately
$15–39/month. Actual active/idle metering determines the bill; these are estimates,
not a spending cap. [Official retail-price API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices)
and [Container Apps billing](https://learn.microsoft.com/en-us/azure/container-apps/billing)
explain rates/allowances. Additional registry storage and internet egress can add cost.

Existing SQL has useFreeLimit=true and freeLimitExhaustionBehavior=AutoPause. Its
free offer avoids compute charges within that allowance but pauses at exhaustion;
confirm the owner's actual eligibility/balance. Non-free Gen5 serverless retail
compute is $0.699156/vCore-hour in this region, plus storage/backup if applicable.
We do not change SQL's tier, pause, or free-offer configuration. No paid log store,
NAT, private endpoint, or dedicated workload profile is added.

503 startup: inspect safe console logs, SQL pause/allowance, Driver18, permissions,
and discovery deadline. Anonymous401 is expected. SQL/TLS/auth/permission errors
fail closed; there is no SQLite fallback. Readiness remains a startup flag after
initialization, so SQL can pause later without changing that endpoint. Quotas and
sessions are memory-local; replicas/processes must remain one.

For updates, rerun audit/tests, commit, build/push a new immutable digest, deploy,
and verify. Rollback redeploys a previously recorded digest with matching source.
Do not delete the resource group to stop hosting: it contains the preserved SQL
server/database. Review and remove only the new app/environment/registry/identity
if teardown is required. Credentials and provider billing caps remain owner controls.
