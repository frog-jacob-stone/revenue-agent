#!/usr/bin/env bash
#
# Build the UI and deploy it to Netlify.
#
#   ./scripts/deploy-ui.sh
#
# The VITE_* values are inlined into the bundle at build time — there is no
# runtime configuration to correct afterwards, which is why a rebuild is needed
# whenever one of them changes.
#
# Reads .env.production.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/_load_env.sh

# ui/vite.config.ts fails the build without these, but its error arrives after
# `npm ci` has run. Checking first keeps the feedback immediate.
require_vars VITE_API_URL VITE_SUPABASE_URL VITE_SUPABASE_PUBLISHABLE_KEY

cd ui

echo "==> Installing dependencies"
npm ci

# Exported by _load_env.sh's `set -a`, so vite's loadEnv picks them up from the
# environment with no .env file on disk.
echo
echo "==> Building"
npm run build

echo
echo "==> Deploying to Netlify"
netlify deploy --prod --dir=dist
