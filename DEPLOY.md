# Deploying Revenue Agents

Manual deploys from a laptop. No CI/CD, no infrastructure-as-code — three
commands, run in order, by one person. That is a deliberate choice for a
single-operator system; see [Why there is no pipeline](#why-there-is-no-pipeline)
at the bottom.

**This document does not affect local development.** Running and testing on
localhost is unchanged and is still described in [README.md](README.md#setup).
Nothing here needs to happen until you actually deploy.

---

## The three pieces

| Piece | Runs on | Deployed with |
|---|---|---|
| Database + auth | Supabase (hosted project) | `supabase db push` |
| API (FastAPI) | Azure Container Apps | `az containerapp up` |
| UI (Vite/React) | Netlify | `netlify deploy` |

They are independent. A UI deploy cannot break the API, and vice versa. The one
ordering rule is that **migrations go first** — see [Routine deploy](#routine-deploy).

---

## Fill these in first

Replace every placeholder below with your real value once you create the
resources. Keep this table current; it is the only record of where things live.

| Placeholder | Value | Where to find it |
|---|---|---|
| `<SUPABASE_PROJECT_REF>` | | Supabase dashboard → Project Settings → General |
| `<SUPABASE_URL>` | | Project Settings → API → Project URL (`https://xxx.supabase.co`) |
| `<SUPABASE_PUBLISHABLE_KEY>` | | Project Settings → API → Project API keys → `anon` / publishable |
| `<SUPABASE_DB_PASSWORD>` | | Set when you create the project. Store in a password manager |
| `<AZURE_RESOURCE_GROUP>` | | You choose it, e.g. `revenue-agents-rg` |
| `<AZURE_LOCATION>` | | e.g. `centralus` |
| `<API_URL>` | | Azure gives you this after the first deploy |
| `<NETLIFY_SITE_URL>` | | Netlify gives you this after the first deploy |

There is a chicken-and-egg here: the API needs the UI's URL for CORS, and the UI
needs the API's URL to build against. [First-time setup](#first-time-setup)
resolves it by deploying the API first with a placeholder origin, then correcting
it in step 4.

---

## One-time prerequisites

Install the three CLIs and log in. You only ever do this once per machine.

```bash
# Azure
brew install azure-cli
az login

# Netlify
npm install -g netlify-cli
netlify login

# Supabase
brew install supabase/tap/supabase
supabase login
```

---

## First-time setup

### 1. Create the Supabase project and push the schema

Create a new project in the [Supabase dashboard](https://supabase.com/dashboard).
Choose a region near your users and save the database password somewhere safe —
it is shown once.

Then link this repo to it and push all migrations. Read
[Not pushing to the wrong database](#not-pushing-to-the-wrong-database) before
running the first line — this org has several unrelated Supabase projects, and
`link` takes whichever ref you give it:

```bash
./scripts/deploy-db.sh
```

That script is the whole sequence — link, dry-run, confirm, push, unlink — with
the unlink guaranteed by a `trap` even if the push fails or you interrupt it. It
refuses to run if `supabase/migrations/` has uncommitted changes, and aborts if
the project that actually got linked is not the one you asked for. The equivalent
by hand, if you prefer to watch each step:

```bash
supabase link --project-ref <SUPABASE_PROJECT_REF>
supabase db push --dry-run    # confirm the list, and that it is the right project
supabase db push
supabase unlink               # return to the unlinked default
```

`db push` reads `supabase/migrations/`, compares against what the remote has
already applied, and runs only what is missing. On a fresh project that is all 29
files, in filename order.

> **Why the filenames look like `20250101000001_`:** Supabase requires 14-digit
> version prefixes. Mixing those with short prefixes (`0001_`) permanently breaks
> `db push` ([supabase/cli#6036](https://github.com/supabase/cli/issues/6036)).
> The dates are synthetic — only the ordering is real.

Finally, create your login user: **Authentication → Users → Add user**. The app
has no signup flow; users are created here by hand.

### 2. Get the production database connection string

Supabase dashboard → **Project Settings → Database → Connection string**.

> ⚠️ **Use the Session pooler, not the Transaction pooler.**
>
> `app/db.py` creates an asyncpg pool without disabling prepared statements
> (`statement_cache_size` is unset, so caching is on). The transaction pooler
> (port `6543`) multiplexes connections and will fail with
> `prepared statement "__asyncpg_stmt_1__" already exists` under load — an error
> that appears only intermittently, which makes it miserable to diagnose.
>
> Use the **Session pooler** connection string (port `5432` on a
> `pooler.supabase.com` host). Direct connection also works but is IPv6-only
> unless you have purchased the IPv4 add-on.

The result looks like:

```
postgresql://postgres.<SUPABASE_PROJECT_REF>:<SUPABASE_DB_PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres
```

That is your production `DATABASE_URL`.

### 3. Create and deploy the API

One command builds the Dockerfile in Azure (no local Docker needed), pushes the
image, and creates everything it needs — resource group, registry, Container Apps
environment:

```bash
az containerapp up \
  --name revenue-agents-api \
  --resource-group <AZURE_RESOURCE_GROUP> \
  --location <AZURE_LOCATION> \
  --source . \
  --ingress external \
  --target-port 8000
```

Note the URL it prints — that is your `<API_URL>`.

**Then immediately pin it to a single replica:**

```bash
az containerapp update \
  --name revenue-agents-api \
  --resource-group <AZURE_RESOURCE_GROUP> \
  --min-replicas 1 --max-replicas 1
```

> ⚠️ **This is not optional and not a cost optimization.** The app has
> module-level singletons — the Harvest rate limiter's token bucket, the
> in-memory turn registry, the asyncpg pool, the JWKS cache. A second replica
> gets its own copy of each, so the Harvest rate limit would be silently
> exceeded and in-flight chat turns would be invisible to half of the traffic.
> This is the same reason the Dockerfile pins `--workers 1`.
>
> `--min-replicas 1` (not `0`) also disables scale-to-zero. A cold start would
> otherwise kill every in-flight chat turn and add a multi-second delay to the
> first request after any idle period.

Now set the environment variables. Fill in `.env.production` first (see
[Secrets](#secrets)), then push them:

```bash
./scripts/deploy-api.sh --env-only
```

The script reads `.env.production`, stores credentials in the Container App
secret store and the rest as plain environment variables (see
[Where the values end up](#where-the-values-end-up-after-a-deploy)), and re-pins
the replica count. Set `ALLOWED_ORIGINS` to a placeholder for now — step 5
corrects it once Netlify has given you a URL.

From here on, `./scripts/deploy-api.sh` (without `--env-only`) does the build,
the deploy, and the variable sync in one command.

**Configure the health probes.** Azure does not read the `HEALTHCHECK` line from
a Dockerfile, and this image deliberately does not define one. In the Azure
portal: **Container App → Containers → Health probes → Edit and deploy**.

| Probe | Path | Port | Notes |
|---|---|---|---|
| Liveness | `/healthz` | 8000 | Touches nothing. Restarts the container if the process is wedged |
| Readiness | `/readyz` | 8000 | `select 1` under a 2s timeout; 503 if the DB is unreachable |
| Startup | `/readyz` | 8000 | Same endpoint; gives the pool time to open before traffic arrives |

The split matters: `/healthz` must never check the database. A liveness probe
that touches Postgres turns a ten-second blip into a container restart, and at
one replica a restart is a full outage.

### 4. Build and deploy the UI

Set the three `VITE_*` values in `.env.production` first — `VITE_API_URL` is the
URL Azure printed in step 3 — then:

```bash
./scripts/deploy-ui.sh
```

That runs `npm ci`, builds with the `VITE_*` values exported from
`.env.production`, and deploys `dist/` to Netlify. The first `netlify deploy`
prompts you to create or link a site. Note the URL it returns — that is your
`<NETLIFY_SITE_URL>`.

> **Why the variables go on the build command:** Vite inlines `VITE_*` at build
> time. There is no runtime configuration to fix afterwards — a bundle built
> without them is broken the moment it is served. `ui/vite.config.ts` fails the
> production build if any is missing, or if any points at localhost. Do not work
> around that guard; it is the thing standing between you and a production bundle
> that talks to your laptop.

Netlify needs one redirect rule so client-side routing works — without it,
loading `/invoices` directly returns a 404. Create `ui/public/_redirects`:

```
/*  /index.html  200
```

### 5. Close the CORS loop

Now that you know the real UI URL, correct the placeholder from step 3:

```bash
az containerapp update \
  --name revenue-agents-api \
  --resource-group <AZURE_RESOURCE_GROUP> \
  --set-env-vars ALLOWED_ORIGINS='<NETLIFY_SITE_URL>'
```

Must be `https://`, no trailing slash. The startup guard rejects plain http.

Add the same URL to Supabase: **Authentication → URL Configuration → Site URL and
Redirect URLs**. Login will not complete without it.

### 6. Verify

```bash
curl <API_URL>/healthz    # {"status":"ok"}
curl <API_URL>/readyz     # {"status":"ready"}  — 503 means the DB is unreachable
```

Then open `<NETLIFY_SITE_URL>`, log in, and load the Invoices page. That exercises
auth, the database, and a Harvest read in one go.

---

## Routine deploy

Every deploy after the first. Skip any piece you did not change.

```bash
# 1. Database — always first; the API expects the schema to exist
./scripts/deploy-db.sh

# 2. API
./scripts/deploy-api.sh

# 3. UI — last, because it is the only piece users see
./scripts/deploy-ui.sh
```

All three read `.env.production`. Environment variables persist across deploys;
you only touch them when adding or rotating one — see [Secrets](#secrets).

**Run the tests first.** This is the one thing a pipeline would do for you that
nothing else will:

```bash
pytest && ruff check . && (cd ui && npx tsc --noEmit)
```

A destructive migration (dropping a column the running API still selects) needs
the usual two-step: deploy a migration that only adds, deploy the API that stops
using the old column, then deploy the migration that drops it. Most migrations
are not destructive and need no ceremony.

---

## Not pushing to the wrong database

**Local development never runs `db push`.** Local migrations are applied by
`supabase db reset` and `supabase migration up`, both of which target the local
Postgres by default ([README](README.md#3-run-migrations)). `db push` without
`--local` always means the remote linked project. There is no mode to be in and
no per-command `--project-ref` to forget — the link is one piece of ambient
state, stored in `supabase/.temp/project-ref`, which `supabase/.gitignore`
excludes so it never travels with the repo.

That gives one clean rule, which `scripts/deploy-db.sh` exists to enforce rather
than leave to memory:

> **Stay unlinked. Link only for the seconds it takes to deploy, then unlink.**

Unlinked is the repo's default state today. While unlinked, `db push` fails with
`Cannot find project ref. Have you run supabase link?` — that is an error, not a
convention, so no amount of muscle memory can push migrations to production
during ordinary development.

### The three ways this actually goes wrong

**Linking to the wrong project.** This org has several unrelated Supabase
projects. `supabase link` accepts whatever ref it is given, and `db push` would
apply all 29 of this app's migrations to it. This is the expensive mistake, and
the only defence is reading the ref before pressing enter. Confirm which project
is linked at any time:

```bash
supabase projects list    # the LINKED column marks the current one
```

**Staying linked after a deploy.** The link never expires. Weeks later, mid-
development, `supabase db push` typed from habit ships whatever half-finished
migration is sitting in `supabase/migrations/`. This is why `supabase unlink` is
part of the deploy sequence rather than a suggestion — it makes "linked" a
condition that exists only during a deploy.

**`supabase db reset --linked`.** Drops and recreates the *remote* database.
It is one flag away from the local `supabase db reset` you type constantly.
Being unlinked defuses this one too.

### Deploy from a clean checkout

`db push` pushes every migration in the folder, not the ones you consider ready.
A work-in-progress `0030_*.sql` on your working tree goes out with everything
else. Deploy from a committed, tested state — `git status` clean — rather than
from whatever the tree happens to hold.

`--dry-run` prints the exact list that would be applied and writes nothing. It
costs two seconds and is the last chance to notice either mistake above.

---

## Secrets

Production values live in **`.env.production`** at the repo root. It is
gitignored; `.env.production.example` is the tracked template.

```bash
cp .env.production.example .env.production
chmod 600 .env.production
```

Then fill it in. Every field is documented in the template itself, and
[Environment variables](#environment-variables) explains what breaks without
each one.

This file is read **only** by the scripts in `scripts/`, which copy the values to
Azure and Netlify at deploy time. No application code reads it — locally the app
reads `app/.env`, and in production it reads real environment variables set on
the container. The two files are separate on purpose: nothing you do to
`.env.production` can affect local development.

> **Quoting.** The file is sourced by bash, so a value containing a space, `$`,
> `!`, `&`, or `#` must be wrapped in **single** quotes (double quotes still
> interpolate `$`). Supabase generates database passwords with special
> characters, so the fields most likely to need it ship with the quotes already
> in place. Get this wrong and the load fails with `command not found` naming
> part of your password.

### Where the values end up after a deploy

`.env.production` is the source; deploying copies values into three places, each
with different visibility. Worth knowing which is which.

**Azure — split between secrets and plain configuration.** Container Apps has
two stores, and `deploy-api.sh` uses both:

| | Where it goes | Who can read it |
|---|---|---|
| Credentials — `DATABASE_URL`, `OPENAI_API_KEY`, `HARVEST_TOKEN`, `AIRTABLE_API_KEY` | The Container App **secret store**, referenced by env vars as `secretref:openai-api-key` | Not shown by `az containerapp show` or the portal's env var list. Reading a value takes a separate `az containerapp secret show` and the permission to run it |
| Everything else — `ENV`, `SUPABASE_URL`, `ALLOWED_ORIGINS`, account IDs | Plain environment variables | Anyone with **Reader** on the resource group, via the portal or `az containerapp show` |

The classification lives in `PLAIN_KEYS` in `scripts/deploy-api.sh`, and it is an
allowlist: anything not named there is treated as a secret. A variable added to
`.env.production` later is protected by default rather than by remembering to
classify it. `DATABASE_URL` is a secret because it embeds the database password.

Read a stored secret back when you need to confirm what is live:

```bash
az containerapp secret show \
  --name revenue-agents-api \
  --resource-group <AZURE_RESOURCE_GROUP> \
  --secret-name openai-api-key
```

**Netlify — the `VITE_*` values are public.** They are inlined into the JavaScript
bundle at build time and served to every visitor. That is fine for what is there:
an API URL, a Supabase URL, and the anon key, which is designed to be published
and is backed by RLS. The rule that follows is absolute — **never give a `VITE_`
prefix to anything sensitive.** A `VITE_HARVEST_TOKEN` would be readable by
anyone who opens devtools. The deploy scripts never send `VITE_*` to the
container, and never send anything else to the build.

**Supabase — nothing is stored.** It is a target, not a holder of config.

### What this trades away

`.env.production` is plaintext production credentials sitting on one laptop.
That is an acceptable trade for a single operator, and it is the honest version
of what was already happening — but it is worth being deliberate about:

- `chmod 600`, so other accounts on the machine cannot read it.
- Keep it out of any directory that syncs to iCloud, Dropbox, or a backup that
  leaves the machine.
- It is the *only* copy. If the laptop dies, the values are recoverable only
  from Azure, Netlify, and the source systems. Consider a password manager entry
  as a second copy of the irreplaceable ones.
- Revisit if a second person ever needs to deploy. At that point a shared secret
  manager stops being ceremony and starts being the thing that prevents four
  divergent copies of production config.

### Rotating a secret

Two steps, and both are required — editing the file changes nothing about what
is running:

```bash
# 1. Edit the value in .env.production
# 2. Push it to Azure and restart, without rebuilding the image
./scripts/deploy-api.sh --env-only
```

A `VITE_*` change instead needs a UI rebuild, since those are inlined at build
time and the running bundle keeps the old value until it is replaced:

```bash
./scripts/deploy-ui.sh
```

---

## Environment variables

Everything below lives in `.env.production`. The tables describe what each value
does and what breaks without it.

### API — required in production

The container **refuses to start** without these. `guard_production_config` in
`app/config.py` raises at import with a list of what is missing, so a bad deploy
is a failed revision rather than a silent outage. Values are never echoed in the
error.

| Variable | Notes |
|---|---|
| `ENV` | Must be exactly `production`. It is a `Literal` — `prod` fails at startup rather than silently disabling every check below |
| `DATABASE_URL` | Session pooler string. Rejected if it is the dev default or points at a local host |
| `SUPABASE_URL` | Must be `https://`. The JWKS endpoint is built from it, so every authenticated request 500s if it is wrong |
| `ALLOWED_ORIGINS` | Comma-separated. Rejected if plain http or still the dev default |
| `OPENAI_API_KEY` | Chat fails on the first message without it |
| `HARVEST_TOKEN` | |
| `HARVEST_ACCOUNT_ID` | |
| `HARVEST_USER_AGENT_CONTACT` | Harvest rejects requests without a contact email in the User-Agent |

### API — optional (warns at startup, does not block)

Each breaks one feature rather than the app. A deploy that refuses to start over
a parked integration is its own outage.

| Variable | If unset |
|---|---|
| `AIRTABLE_API_KEY`, `AIRTABLE_BASE_ID` | The revenue-ops agent's `get_revenue_data` tool fails when asked |
| `AIRTABLE_*_TABLE_ID` | Same |
| `HARVEST_BASE_URI` | Invoice screens render without links back to Harvest — deliberate degradation |
| `FORECAST_ACCOUNT_ID` | Forecast reads fail |
| `LOG_LEVEL` | Defaults to `INFO` |

### API — never set in production

| Variable | Why |
|---|---|
| `SUPABASE_PUBLISHABLE_KEY` | Not a `Settings` field. Only `tests/conftest.py` reads it, straight from the environment |
| `TEST_USER_EMAIL`, `TEST_USER_PASSWORD` | Test fixtures only |
| `TEST_DATABASE_URL` | Set by `pytest-env`. Pointing it at production would be catastrophic |

> `Settings` uses `extra="ignore"`, so **a misspelled variable is silently
> ignored** — no error, no warning, just a default. If something behaves as
> though a value is unset, check the spelling against `app/config.py` first.

### UI — required at build time

All three are validated by `ui/vite.config.ts` on a production build.

| Variable | Notes |
|---|---|
| `VITE_API_URL` | Build fails if missing or pointing at localhost |
| `VITE_SUPABASE_URL` | Same |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | The anon key. Safe to ship in a bundle; RLS is what protects the data |

### Deploy-time only — never set on the container

Read by `scripts/deploy-db.sh` on your machine. No app code reads either, and
neither belongs in the Container App's environment.

| Variable | Notes |
|---|---|
| `SUPABASE_PROJECT_REF` | Which project `db push` targets. Deliberately has no default in the script |
| `SUPABASE_DB_PASSWORD` | Lets `link` and `push` run without an interactive prompt |

---

## When it breaks

| Symptom | Cause | Fix |
|---|---|---|
| Revision fails, logs show `ENV=production but the configuration is incomplete` | A required variable is missing | The log lists exactly which. Set it and redeploy |
| `/readyz` returns 503, `/healthz` returns 200 | The process is fine; Postgres is unreachable | Check `DATABASE_URL`, and that Supabase is not paused (free tier pauses after inactivity) |
| Every browser request is CORS-blocked | `ALLOWED_ORIGINS` does not exactly match the Netlify origin | Must be `https://`, no trailing slash |
| Every authenticated request 500s | `SUPABASE_URL` wrong — JWKS fetch is failing | Verify it against Project Settings → API |
| Intermittent `prepared statement "__asyncpg_stmt_N__" already exists` | You are on the transaction pooler (port 6543) | Switch to the session pooler (port 5432). See [step 2](#2-get-the-production-database-connection-string) |
| UI loads, every API call goes to `localhost` | Built without `VITE_API_URL` | Should be impossible — `vite.config.ts` guards it. Check you are not deploying a stale `dist/` |
| Direct navigation to `/invoices` 404s | Missing SPA redirect | Add `ui/public/_redirects`. See [step 4](#4-build-and-deploy-the-ui) |
| `db push` errors about migration versions | A migration was added with a short prefix | All 29 use 14-digit versions. Match that format — `supabase migration new` does it automatically |
| `Cannot find project ref. Have you run supabase link?` | Not linked | Working as intended — see [Not pushing to the wrong database](#not-pushing-to-the-wrong-database). Link, push, unlink |
| Harvest calls 401 or rate-limit | Token expired, or two replicas | Confirm `--max-replicas 1`; the rate limiter is per-process |

**Logs:**

```bash
az containerapp logs show \
  --name revenue-agents-api \
  --resource-group <AZURE_RESOURCE_GROUP> \
  --follow
```

**Rollback.** Container Apps keeps previous revisions:

```bash
az containerapp revision list \
  --name revenue-agents-api \
  --resource-group <AZURE_RESOURCE_GROUP> -o table

az containerapp revision activate \
  --revision <previous-revision-name> \
  --resource-group <AZURE_RESOURCE_GROUP>
```

Netlify rolls back from the dashboard: **Deploys → pick a previous one → Publish
deploy**. Migrations do not roll back — write a new one that reverses the change.

---

## Why there is no pipeline

No GitHub Actions, no bicep, no Terraform. That is a decision, not an omission.

A pipeline buys you: tests gating deploys, deploys from any machine, and a
versioned record of infrastructure. At one operator deploying every few weeks,
the first is a shell command, the second is not needed, and the third is this
file.

What it costs you, stated plainly so the tradeoff is visible:

- **Nothing forces the tests to run.** The `pytest && ruff check .` line in
  [Routine deploy](#routine-deploy) is a habit, not a gate.
- **Only this laptop can deploy.** Make sure the Azure, Netlify, and Supabase
  credentials are recoverable from a password manager, not just this keychain.
- **Infrastructure is not reproducible from code.** If the resource group is
  deleted, you rebuild by re-running [First-time setup](#first-time-setup).

Revisit when a second person needs to deploy, or when a second environment
(staging) appears. Both change the arithmetic; neither is true today.
