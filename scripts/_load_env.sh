# shellcheck shell=bash
#
# Sourced by the deploy scripts. Not executable on its own.
#
# Loads .env.production from the repo root and exports every value in it, so a
# deploy script can read them as ordinary environment variables. Override the
# path with ENV_FILE=... for a one-off (a second environment, a dry run against
# a scratch project).

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env.production}"

if [[ ! -f "$ENV_FILE" ]]; then
  cat >&2 <<EOF
ERROR: $ENV_FILE not found.

  cp .env.production.example .env.production
  chmod 600 .env.production

Then fill it in. See DEPLOY.md.
EOF
  exit 1
fi

# This file holds live production credentials. Group/world-readable is worth a
# word, not a refusal — the deploy is not the moment to fight the filesystem.
_perms="$(stat -f '%OLp' "$ENV_FILE" 2>/dev/null || stat -c '%a' "$ENV_FILE" 2>/dev/null || echo '')"
if [[ -n "$_perms" && "$_perms" != "600" ]]; then
  echo "WARNING: $ENV_FILE is mode $_perms. Consider: chmod 600 $ENV_FILE" >&2
fi

# `set -a` exports everything the file defines; sourcing lets bash handle quoting
# and escapes rather than reimplementing a parser here.
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# Keys defined in the env file, in file order. Used to build the argument list
# for `az containerapp update`, so it stays in sync with the file by
# construction — adding a variable to .env.production is all it takes.
#
# Excluded, because they are not the container's business:
#   SUPABASE_*_REF / _PASSWORD, AZURE_*  deploy targets, used by these scripts
#   VITE_*                               inlined into the UI bundle at build time
#   TEST_*                               test fixtures; must never reach production
#   SUPABASE_PUBLISHABLE_KEY             not a Settings field; nothing in app/ reads it
container_env_keys() {
  grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE" \
    | tr -d '=' \
    | grep -vE '^(SUPABASE_PROJECT_REF|SUPABASE_DB_PASSWORD|SUPABASE_PUBLISHABLE_KEY|AZURE_|VITE_|TEST_)'
}

# Fails with a readable message naming every missing variable at once, rather
# than one per run.
require_vars() {
  local missing=()
  for _var in "$@"; do
    [[ -z "${!_var:-}" ]] && missing+=("$_var")
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "ERROR: these are empty in $ENV_FILE:" >&2
    printf '       %s\n' "${missing[@]}" >&2
    exit 1
  fi
}
