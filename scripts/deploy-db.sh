#!/usr/bin/env bash
#
# Push migrations to the production Supabase project.
#
#   ./scripts/deploy-db.sh
#
# Runs link -> dry-run -> confirm -> push -> unlink, and guarantees the unlink
# even if the push fails or you interrupt it. That guarantee is the reason this
# is a script rather than four commands in DEPLOY.md: a link that outlives its
# deploy is how `supabase db push`, typed from habit weeks later, ships a
# half-finished migration to production. See DEPLOY.md, "Not pushing to the
# wrong database".
#
# Reads .env.production. Nothing here touches the local database — local
# migrations are `supabase db reset` and `supabase migration up`; see README.md.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/_load_env.sh

# A wrong ref applies this app's migrations to whatever project it names, and
# this org has several unrelated ones. No default, deliberately.
require_vars SUPABASE_PROJECT_REF SUPABASE_DB_PASSWORD

linked=0
cleanup() {
  if [[ $linked -eq 1 ]]; then
    echo
    echo "==> Unlinking"
    # Never let a failed unlink mask the real exit status.
    supabase unlink >/dev/null 2>&1 || echo "    WARNING: unlink failed. Run 'supabase unlink' by hand."
  fi
}
trap cleanup EXIT

# --- Preflight -------------------------------------------------------------

# db push applies every file in supabase/migrations/, not the ones you consider
# ready. An uncommitted 0030_*.sql goes out with everything else.
if [[ -n "$(git status --porcelain -- supabase/migrations)" ]]; then
  echo "ERROR: supabase/migrations has uncommitted changes." >&2
  echo "       db push ships every file in that directory, including this one." >&2
  echo "       Commit it or stash it, then re-run." >&2
  exit 1
fi

echo "==> Project:    $SUPABASE_PROJECT_REF"
echo "==> Migrations: $(find supabase/migrations -name '*.sql' | wc -l | tr -d ' ') files on disk"
echo

# --- Link ------------------------------------------------------------------

echo "==> Linking"
supabase link --project-ref "$SUPABASE_PROJECT_REF" --password "$SUPABASE_DB_PASSWORD"
linked=1

# `link` succeeding is not proof the intended project is what got linked --
# confirm against the CLI's own view rather than against the variable we passed.
actual="$(cat supabase/.temp/project-ref 2>/dev/null || true)"
if [[ "$actual" != "$SUPABASE_PROJECT_REF" ]]; then
  echo "ERROR: linked project is '$actual', expected '$SUPABASE_PROJECT_REF'." >&2
  exit 1
fi

# --- Dry run ---------------------------------------------------------------

echo
echo "==> Dry run — what would be applied:"
echo
supabase db push --dry-run --password "$SUPABASE_DB_PASSWORD"

# --- Confirm ---------------------------------------------------------------

echo
read -r -p "Apply the above to '$SUPABASE_PROJECT_REF'? [y/N] " reply
if [[ ! "$reply" =~ ^[Yy]$ ]]; then
  echo "Aborted. Nothing was applied."
  exit 0
fi

# --- Push ------------------------------------------------------------------

echo
echo "==> Pushing"
supabase db push --password "$SUPABASE_DB_PASSWORD" --yes

echo
echo "==> Done. Migrations applied to $SUPABASE_PROJECT_REF."
