#!/usr/bin/env bash
#
# Build and deploy the API to Azure Container Apps.
#
#   ./scripts/deploy-api.sh              build, deploy, and sync env vars
#   ./scripts/deploy-api.sh --env-only   sync env vars and restart, no rebuild
#
# `--env-only` is the rotate-a-secret path: updating .env.production changes
# nothing about what is running until the values are pushed.
#
# Reads .env.production.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/_load_env.sh

env_only=0
[[ "${1:-}" == "--env-only" ]] && env_only=1

require_vars AZURE_RESOURCE_GROUP AZURE_CONTAINERAPP_NAME

# The set app/config.py refuses to boot without. Catching them here turns a
# failed Azure revision into a message before anything is uploaded.
require_vars ENV DATABASE_URL SUPABASE_URL ALLOWED_ORIGINS OPENAI_API_KEY \
             HARVEST_TOKEN HARVEST_ACCOUNT_ID HARVEST_USER_AGENT_CONTACT

if [[ "$ENV" != "production" ]]; then
  echo "ERROR: ENV is '$ENV', expected 'production'." >&2
  echo "       app/config.py keys its startup guard off that exact string." >&2
  exit 1
fi

# Build the argument list as an array so values containing spaces survive. The
# keys come from .env.production itself, so this stays in sync with the file.
# Values that carry no credential and are useful to read at a glance in the
# portal. Everything NOT on this list is treated as a secret -- the default is
# deliberately the safe one, so a variable added to .env.production later is
# protected without anyone remembering to classify it.
#
# DATABASE_URL is absent on purpose: it embeds the database password.
PLAIN_KEYS='^(ENV|LOG_LEVEL|SUPABASE_URL|ALLOWED_ORIGINS|HARVEST_ACCOUNT_ID|HARVEST_USER_AGENT_CONTACT|HARVEST_BASE_URI|AIRTABLE_BASE_ID|AIRTABLE_CLIENTS_TABLE_ID|AIRTABLE_PROJECTS_TABLE_ID|AIRTABLE_REVENUE_TABLE_ID|FORECAST_ACCOUNT_ID)$'

# Container Apps secret names are lowercase alphanumeric plus dashes.
# OPENAI_API_KEY -> openai-api-key. Two `tr` calls rather than bash's ${x,,},
# which needs bash 4 and macOS still ships 3.2.
secret_name() { printf '%s' "$1" | tr '_' '-' | tr '[:upper:]' '[:lower:]'; }

plain_args=()      # KEY=value, stored as ordinary readable configuration
secret_args=()     # name=value, stored in the Container App secret store
ref_args=()        # KEY=secretref:name, the env var pointing at the secret
skipped=()

while IFS= read -r key; do
  value="${!key}"
  if [[ "$key" =~ $PLAIN_KEYS ]]; then
    # Included even when empty, so clearing a value in .env.production actually
    # clears it on the container rather than leaving the old one in place.
    plain_args+=("$key=$value")
  elif [[ -z "$value" ]]; then
    # Azure rejects an empty secret value. Skipping means an optional credential
    # left blank keeps whatever is already set, so say so rather than imply the
    # sync was complete.
    skipped+=("$key")
  else
    sname="$(secret_name "$key")"
    secret_args+=("$sname=$value")
    ref_args+=("$key=secretref:$sname")
  fi
done < <(container_env_keys)

echo "==> App:            $AZURE_CONTAINERAPP_NAME"
echo "==> Resource group: $AZURE_RESOURCE_GROUP"
echo "==> ${#secret_args[@]} secrets, ${#plain_args[@]} plain variables"
if [[ ${#skipped[@]} -gt 0 ]]; then
  echo "==> Skipped (empty in .env.production, existing value on the container is left as-is):"
  printf '      %s\n' "${skipped[@]}"
fi
echo

if [[ $env_only -eq 0 ]]; then
  echo "==> Building and deploying from source"
  az containerapp up \
    --name "$AZURE_CONTAINERAPP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --source .
  echo
fi

# Secrets first: an env var referencing a secretref that does not exist yet is
# rejected, so the order here is load-bearing.
if [[ ${#secret_args[@]} -gt 0 ]]; then
  echo "==> Storing secrets"
  az containerapp secret set \
    --name "$AZURE_CONTAINERAPP_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --secrets "${secret_args[@]}"
  echo
fi

echo "==> Applying environment variables"
az containerapp update \
  --name "$AZURE_CONTAINERAPP_NAME" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --set-env-vars "${plain_args[@]}" "${ref_args[@]}"

# Not a cost setting. The app has module-level singletons -- the Harvest token
# bucket, the in-memory turn registry, the asyncpg pool, the JWKS cache -- so a
# second replica silently exceeds the Harvest rate limit and hides in-flight
# turns from half the traffic. Reasserted on every deploy so it cannot drift.
echo
echo "==> Pinning to a single replica"
az containerapp update \
  --name "$AZURE_CONTAINERAPP_NAME" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --min-replicas 1 --max-replicas 1

echo
echo "==> Done. Verify with:"
echo "    curl \$API_URL/healthz   # {\"status\":\"ok\"}"
echo "    curl \$API_URL/readyz    # {\"status\":\"ready\"}"
