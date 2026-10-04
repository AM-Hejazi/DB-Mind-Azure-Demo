# Deploy DB-Mind with your Azure resources

This guide publishes the approved synthetic demonstration to Azure Container Apps
using your own Azure SQL database, container registry and identity. For a different
business schema, complete [database adaptation](own-database-setup.md) first;
changing connection settings alone does not remove the fixture contract.

## Prerequisites

- An Azure subscription and permission to create the chosen hosting resources.
- Azure CLI with Container Apps support, Docker, and Python 3.12 with the locked
  dependencies installed. Sign in with `az login` and select your subscription.
- A dedicated Azure SQL test database named `dbmind_synthetic_...`, seeded with the
  [fixture](synthetic-data.md), plus a separate restricted reader. Review SQL
  firewall/network access yourself; hosting deployment does not change SQL rules.
- A private DeepSeek key for the included deployer (also required when deploying
  mock mode), separate application login credentials, and optionally admin access.

Managed identity for pulling an image is separate from SQL data access. For
SQL managed identity authentication, an authorized database owner must provision
the identity as a contained user and add it to `dbmind_demo_reader`; see
[configuration](configuration.md). SQL-password mode needs a restricted contained
user and its password supplied privately.

## Create hosting infrastructure

Choose globally unique registry names, your supported Azure region, and dedicated
resource names. The commands below create billable resources; review your Azure
pricing and budget before running them. Existing resources can be reused instead.

```bash
export DBMIND_GROUP=YOUR_RESOURCE_GROUP
export DBMIND_REGION=YOUR_AZURE_REGION
export DBMIND_REGISTRY=YOUR_UNIQUE_REGISTRY_NAME
export DBMIND_ENVIRONMENT=YOUR_CONTAINER_APPS_ENVIRONMENT
export DBMIND_IDENTITY=YOUR_IMAGE_PULL_IDENTITY
export DBMIND_APP=YOUR_CONTAINER_APP_NAME

az group create --name "$DBMIND_GROUP" --location "$DBMIND_REGION"
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.ContainerRegistry
az provider register --namespace Microsoft.ManagedIdentity
az extension add --name containerapp --upgrade
az acr create --resource-group "$DBMIND_GROUP" --name "$DBMIND_REGISTRY" \
  --location "$DBMIND_REGION" --sku Basic --admin-enabled false
az identity create --resource-group "$DBMIND_GROUP" --name "$DBMIND_IDENTITY" \
  --location "$DBMIND_REGION"
az containerapp env create --resource-group "$DBMIND_GROUP" \
  --name "$DBMIND_ENVIRONMENT" --location "$DBMIND_REGION" \
  --logs-destination none --enable-workload-profiles true

DBMIND_REGISTRY_ID=$(az acr show -g "$DBMIND_GROUP" -n "$DBMIND_REGISTRY" --query id -o tsv)
DBMIND_PULL_PRINCIPAL=$(az identity show -g "$DBMIND_GROUP" -n "$DBMIND_IDENTITY" --query principalId -o tsv)
az role assignment create --assignee-object-id "$DBMIND_PULL_PRINCIPAL" \
  --assignee-principal-type ServicePrincipal --role AcrPull --scope "$DBMIND_REGISTRY_ID"
```

The registry setup uses standard RBAC with registry-scoped `AcrPull`; use the
appropriate repository role if you deliberately choose an ABAC-enabled registry.
No SQL resources or firewall rules are created by these hosting commands.

## Prepare private deployment configuration

Store a private Docker-style `KEY=value` file outside the repository, for example
`$HOME/.config/dbmind/deployment.env`, with file mode 0600. Use `.env.example` and
[database settings](configuration.md) as a reference, replacing the local profile
rather than adding conflicting settings.

Set `DB_PROFILE=azure_sql`, `DB_DIALECT=sqlserver`, your server/database, scope
`demo`, an ignored snapshot path, `DB_DISCOVERY_TIMEOUT_SECONDS=300`, and explicit
`DB_AUTH`. For SQL-password auth, provide restricted `DB_USER`/`DB_PASSWORD`.
For token auth, leave them unset. Leave `DB_SQLITE_PATH` unset for Azure.
Include `APP_USERNAME`/`APP_PASSWORD` or `APP_AUTH`, and `DEEPSEEK_API_KEY`.
Optional `APP_ADMIN_USERNAME`/`APP_ADMIN_PASSWORD` configure a separate operator login.
Do not put values into shell history, screenshots or public parameter files.

The included template deliberately sets fixed safe deployment defaults: one
0.5-vCPU/1-GiB replica, HTTPS on port 7860, one revision, and bounded startup probes.
The deployer reads database/authentication/model validation from the private file;
**it does not forward every env-file option to Azure**. Review
`infra/container-app.json` for supported environment entries before customizing
sample limits, quotas or stage models. Add only public settings or secret references
there, never secret values.

## Test, build and publish

```bash
python -m scripts.audit_public --private-env "$HOME/.config/dbmind/deployment.env"
GRADIO_ANALYTICS_ENABLED=False HF_HUB_OFFLINE=1 APP_LLM_MODE=mock \
  python -B -m unittest discover -s tests -q
# Commit the reviewed source before building.
DBMIND_COMMIT=$(git rev-parse HEAD)
DBMIND_LOGIN_SERVER=$(az acr show -g "$DBMIND_GROUP" -n "$DBMIND_REGISTRY" --query loginServer -o tsv)
docker build --platform linux/amd64 --target azure \
  --label org.opencontainers.image.revision="$DBMIND_COMMIT" -t dbmind:azure .
az acr login --name "$DBMIND_REGISTRY"
docker tag dbmind:azure "$DBMIND_LOGIN_SERVER/dbmind:$DBMIND_COMMIT"
docker push "$DBMIND_LOGIN_SERVER/dbmind:$DBMIND_COMMIT"
DBMIND_DIGEST=$(az acr repository show -n "$DBMIND_REGISTRY" \
  --image "dbmind:$DBMIND_COMMIT" --query digest -o tsv)

python -m scripts.deploy_azure \
  --env-file "$HOME/.config/dbmind/deployment.env" \
  --resource-group "$DBMIND_GROUP" --registry "$DBMIND_REGISTRY" \
  --environment "$DBMIND_ENVIRONMENT" --identity "$DBMIND_IDENTITY" \
  --app "$DBMIND_APP" --source-commit "$DBMIND_COMMIT" \
  --image "$DBMIND_LOGIN_SERVER/dbmind@$DBMIND_DIGEST" \
  --db-auth sql_password --mode mock
```

Use `--db-auth managed_identity` after provisioning SQL access for the identity.
The deployment script applies the template to the selected existing hosting
resources, including configured secrets; it never seeds SQL or changes its users,
roles or firewall. Securestring parameters are sent over authenticated HTTPS from
memory, without a plaintext parameter file. Do not enable debug HTTP tracing.

Record the source commit and immutable image digest privately for rollback.
For live model access, rerun with `--mode live` only after mock acceptance. Live
questions incur provider charges and send bounded database context externally.

## Startup and acceptance

Startup runs schema preflight; it never seeds the database. Discovery may take
several minutes. The app allows up to three 300-second attempts with two ten-second
retry delays; the Azure startup probe allows about 1,200 seconds. Health endpoints
use in-memory startup state and do not continuously query SQL or the model.

```bash
az containerapp exec -g "$DBMIND_GROUP" -n "$DBMIND_APP" --command /bin/sh
# In the container, using its existing restricted environment:
python -m src.discovery preflight
python -m src.discovery inspect
python -m src.db_check --connect
```

Expect nine tables, a complete snapshot and equipment count 60 for the approved
fixture. Check `/health/ready` returns 200, anonymous `/config` returns 401,
authenticated UI access works, and file routes remain blocked. Test a reference
question and a reviewed follow-up. Optional Playwright acceptance:

```bash
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python -m scripts.hosted_smoke --env-file "$HOME/.config/dbmind/deployment.env" \
  --url https://YOUR_APP_HOST --mode mock
```

Browser system dependencies may also be needed. The live smoke mode submits a
bounded paid reference conversation; it is an explicit opt-in check.

## Rotate an application password

Update the relevant Container App secret in Azure Portal. `private-auth` contains
`username:password` pairs for ordinary users; `admin-password` contains only the
separate admin password. Changing a secret does not itself create a new revision.
Restart each active revision referencing it so its process receives the new value.
No Docker build or image push is needed for a password-only update.

```bash
az containerapp revision list -g "$DBMIND_GROUP" -n "$DBMIND_APP" \
  --query "[?properties.active].name" -o tsv
az containerapp revision restart -g "$DBMIND_GROUP" -n "$DBMIND_APP" \
  --revision YOUR_ACTIVE_REVISION_NAME
```

Alternatively, use Portal → Container App → Revision management → active revision
→ Restart. Restart invalidates existing in-memory visits; use a fresh private
browser window to sign in again. Keep your private deployment env file synchronized:
the full deployer reapplies its secrets and can otherwise restore an older password.
[Azure secret lifecycle](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets)
describes restart/new-revision requirements.

## Code updates, rollback and troubleshooting

For code changes, test and build/push a new immutable image, then deploy it. If only
the image changes and you want to preserve current Portal-managed secrets, use:

```bash
az containerapp update -g "$DBMIND_GROUP" -n "$DBMIND_APP" \
  --image YOUR_REGISTRY/dbmind@sha256:YOUR_IMAGE_DIGEST
```

This creates a revision without reapplying env-file passwords. Roll back by updating
to a previously recorded compatible digest. Documentation-only changes do not
require an app rebuild.

For startup 503, inspect logs privately, SQL availability, ODBC installation,
network reachability and restricted-reader permissions. Do not paste raw logs or
credentials into public issues. Readiness is a startup flag: SQL can become unavailable
later without changing it. The app fails closed rather than selecting another DB.

Run one process/replica because visits and quotas are memory-local. Review Azure
budgets, SQL capacity/free-offer exhaustion and provider billing controls yourself;
application quotas are not dollar spending caps. Teardown only resources you own
and intend to remove; a resource group may also contain a database you need to retain.
