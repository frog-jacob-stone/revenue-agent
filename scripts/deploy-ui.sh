#!/usr/bin/env bash
#
# Build the UI and deploy it to Netlify.
#
#   ./scripts/deploy-ui.sh                    type-check, build, deploy
#   ./scripts/deploy-ui.sh --skip-typecheck   skip the type check
#
# The VITE_* values are inlined into the bundle at build time — there is no
# runtime configuration to correct afterwards, which is why a rebuild is needed
# whenever one of them changes.
#
# Reads .env.production.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/_load_env.sh

skip_typecheck=0
for arg in "$@"; do
  case "$arg" in
    --skip-typecheck) skip_typecheck=1 ;;
    *)
      echo "ERROR: unknown argument '$arg'." >&2
      echo "Usage: $(basename "$0") [--skip-typecheck]" >&2
      exit 1
      ;;
  esac
done

# ui/vite.config.ts fails the build without these, but its error arrives after
# `npm ci` has run. Checking first keeps the feedback immediate.
require_vars VITE_API_URL VITE_SUPABASE_URL VITE_SUPABASE_PUBLISHABLE_KEY

cd ui

echo "==> Installing dependencies"
npm ci

# `vite build` transpiles without type-checking -- a type error ships silently as
# a runtime failure in a page nobody opened yet. Runs after npm ci because it
# needs node_modules, and before the build so a failure costs seconds.
if [[ $skip_typecheck -eq 1 ]]; then
  echo
  echo "==> WARNING: --skip-typecheck given. Type errors will ship."
else
  echo
  echo "==> Type check"
  if ! npx tsc --noEmit; then
    echo >&2
    echo "ERROR: tsc failed. Fix it, or skip the check with --skip-typecheck." >&2
    exit 1
  fi
fi

# Exported by _load_env.sh's `set -a`, so vite's loadEnv picks them up from the
# environment with no .env file on disk.
echo
echo "==> Building"
npm run build

echo
echo "==> Deploying to Netlify"
netlify deploy --prod --dir=dist
