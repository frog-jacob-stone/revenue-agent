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
| API credentials | Azure Key Vault | `az keyvault secret set` — set once, read at runtime |

They are independent. A UI deploy cannot break the API, and vice versa. The one
ordering rule is that **migrations go first** — see [Routine deploy](#routine-deploy).

---

## Fill these in first

Replace every placeholder below with your real value once you create the
resources. Keep this table current; it is the only record of where things live.

| Placeholder | Value | Where to find it |
|---|---|---|
| `<SUPABASE_PROJECT_REF>` | `rwahbqkiomewdeplnyhz` | Supabase dashboard → Project Settings → General |
| `<SUPABASE_URL>` | `https://rwahbqkiomewdeplnyhz.supabase.co` | Project Settings → API → Project URL |
| `<SUPABASE_PUBLISHABLE_KEY>` | *(not recorded here — it is public, but this file is not the place)* | Project Settings → API → Project API keys → `anon` / publishable |
| `<SUPABASE_DB_PASSWORD>` | *(password manager only)* | Set when you create the project |
| `<AZURE_SUBSCRIPTION>` | `Frogslayer Revenue` | `az account show -o table` |
| `<AZURE_RESOURCE_GROUP>` | `revenue-agents-rg` | You choose it |
| `<AZURE_LOCATION>` | `eastus` | Match the Supabase region — see below |
| `<AZURE_KEYVAULT_NAME>` | `fs-revops-kv` | Holds the four API credentials; see [Secrets](#secrets) |
| `<API_URL>` | `https://revenue-agents-api.bluepebble-5dde0989.eastus.azurecontainerapps.io` | Azure prints it after the first deploy |
| `<NETLIFY_SITE_URL>` | `https://revops.frogslayer.com` (custom domain)<br>`https://fs-revenue-ops.netlify.app` (Netlify default, still live) | Netlify prints the default after the first deploy; the custom domain is a CNAME to it |

> **Region.** Supabase is in East US (North Virginia), so the Container App is in
> `eastus`. Every request makes several Postgres round trips, so co-locating the
> API with the database matters more than either being near the operator. If the
> Supabase project ever moves, move the Container App with it.

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

A fresh Azure subscription has no resource providers registered, and
`az containerapp up` fails partway through with `MissingSubscriptionRegistration`
naming one of them. Register all four up front — they are asynchronous, so wait
until each reports `Registered` before deploying:

```bash
for ns in Microsoft.App Microsoft.ContainerRegistry \
          Microsoft.OperationalInsights Microsoft.KeyVault; do
  az provider register --namespace $ns
done

# Poll until all four say Registered, not Registering
for ns in Microsoft.App Microsoft.ContainerRegistry \
          Microsoft.OperationalInsights Microsoft.KeyVault; do
  printf '%-35s %s\n' "$ns" "$(az provider show -n $ns --query registrationState -o tsv)"
done
```

Confirm you are pointed at the right subscription before anything else.
`deploy-db.sh` verifies the Supabase project it linked; nothing verifies the
Azure subscription, so `deploy-api.sh` silently targets whatever `az` currently
has selected:

```bash
az account show -o table              # expect: Frogslayer Revenue
az account set --subscription "Frogslayer Revenue"
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
already applied, and runs only what is missing. On a fresh project that is all 35
files, in filename order.

> **Why the filenames look like `20250101000001_`:** Supabase requires 14-digit
> version prefixes. Mixing those with short prefixes (`0001_`) permanently breaks
> `db push` ([supabase/cli#6036](https://github.com/supabase/cli/issues/6036)).
> The dates are synthetic — only the ordering is real.

Finally, create your login user: **Authentication → Users → Add user**. The app
has no signup flow; users are created here by hand.

### 1b. Leave the Data API disabled, and silence its log spam

This app never touches Supabase's Data API (PostgREST). The backend talks to
Postgres directly over asyncpg; `supabase-js` in the UI is used only for
`supabase.auth.*`, which is a separate service. So the Data API stays **off**
under Project Settings → Data API — that keeps `public` off the public internet,
where RLS would be the only thing guarding it.

Disabling it has a known Supabase bug, though: PostgREST is not actually stopped,
it is handed an empty schema list. It substitutes a placeholder name,
`pg_pgrst_no_exposed_schemas`, fails to load a schema cache for it, and retries
every ~32s forever — about 2,700 `3F000` errors a day in the Postgres logs. Give
it a real but empty schema to point at instead. In the **SQL Editor**:

```sql
create schema pgrst_no_exposed_schemas;
alter role authenticator set pgrst.db_schemas = 'pgrst_no_exposed_schemas';
notify pgrst;
```

The name deliberately drops the `pg_` prefix — that namespace is reserved, which
is why you cannot simply create the schema PostgREST asks for. Nothing is
exposed: the schema is empty and neither `anon` nor `authenticated` is granted
`USAGE` on it. If the Data API is ever re-enabled, revert this first:

```sql
alter role authenticator reset pgrst.db_schemas;
notify pgrst;
```

> **Not a migration, on purpose.** This is remote platform configuration, not
> schema. In `supabase/migrations/` it would re-run on every `db reset` and
> override `supabase/config.toml`, which exposes `public` locally so the local
> stack works normally. Run it by hand, once per project. It is the one piece of
> production database state this repo does not carry — see
> [supabase/supabase#40617](https://github.com/supabase/supabase/issues/40617)
> for the upstream bug.

### 2. Get the production database connection string

Click **Connect** in the dashboard's top bar — the strings are there, not on the
Database settings page (which now shows only pooler *configuration*, the
Shared/Dedicated toggle; leave it Shared, and note that Dedicated is
transaction-mode only, which this app cannot use).

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

That is your production `DATABASE_URL`. It does **not** go in `.env.production` —
it lives in Key Vault as `database-url` (step 3b). If the password contains `@`,
`/`, `:`, `?`, or `#`, percent-encode it inside the URL; those are URL delimiters
and asyncpg will mis-parse the string.

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

Note the URL it prints — that is your `<API_URL>`. It prints as `http://`; the
same hostname serves `https`, and that is the form to use everywhere.

> **The Dockerfile must stay BuildKit-free.** ACR Tasks builds with the classic
> Docker builder, which fails on `RUN --mount=type=cache` with *"the --mount
> option requires BuildKit"*. Nothing is gained by the mount anyway — each ACR
> Tasks run starts on a fresh volume, so there is no cache to hit. Verify a
> change locally the way ACR will build it:
>
> ```bash
> DOCKER_BUILDKIT=0 docker build -t revagents-classic-test .
> ```

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

### 3b. Create the Key Vault and grant the container access

The four real credentials — `DATABASE_URL`, `OPENAI_API_KEY`, `HARVEST_TOKEN`,
`AIRTABLE_API_KEY` — live in Key Vault, not in `.env.production`. The container
resolves them at runtime through its own managed identity, so no plaintext copy
exists on the deploying machine and rotation needs no redeploy.

Key Vault names are globally unique across Azure; check before creating.

```bash
az keyvault check-name --name <AZURE_KEYVAULT_NAME>

az keyvault create \
  --name <AZURE_KEYVAULT_NAME> \
  --resource-group <AZURE_RESOURCE_GROUP> \
  --location <AZURE_LOCATION> \
  --enable-rbac-authorization true \
  --retention-days 7
```

`--enable-rbac-authorization` selects Azure RBAC over the legacy access-policy
model. `--retention-days 7` sets the minimum soft-delete window: a deleted
vault's *name stays reserved* for that period, and the default is 90 days.

**Creating a vault does not grant you access to its contents.** Control plane and
data plane are separate; you need an explicit data-plane role:

```bash
az role assignment create \
  --role "Key Vault Secrets Officer" \
  --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --scope "$(az keyvault show --name <AZURE_KEYVAULT_NAME> --query id -o tsv)"
```

Wait ~60 seconds — RBAC propagates asynchronously, and running too soon returns
`Forbidden` on a vault you just created. Then load the four secrets. Their names
are the kebab-case form of the variable names, which is the convention
`deploy-api.sh` relies on:

```bash
az keyvault secret set --vault-name <AZURE_KEYVAULT_NAME> --name database-url     --value '<session pooler string>'
az keyvault secret set --vault-name <AZURE_KEYVAULT_NAME> --name openai-api-key   --value '<key>'
az keyvault secret set --vault-name <AZURE_KEYVAULT_NAME> --name harvest-token    --value '<token>'
az keyvault secret set --vault-name <AZURE_KEYVAULT_NAME> --name airtable-api-key --value '<key>'
```

Give the Container App a system-assigned identity and grant it **read-only**
access. It needs to get secrets, never to write them — `Secrets User`, not
`Secrets Officer`:

```bash
az containerapp identity assign \
  --name revenue-agents-api \
  --resource-group <AZURE_RESOURCE_GROUP> \
  --system-assigned

az role assignment create \
  --role "Key Vault Secrets User" \
  --assignee-object-id "$(az containerapp identity show \
      --name revenue-agents-api \
      --resource-group <AZURE_RESOURCE_GROUP> \
      --query principalId -o tsv)" \
  --assignee-principal-type ServicePrincipal \
  --scope "$(az keyvault show --name <AZURE_KEYVAULT_NAME> --query id -o tsv)"
```

`--assignee-object-id` with an explicit principal type, rather than plain
`--assignee`: the short form resolves the ID through Microsoft Graph, and a
just-created identity often has not replicated there yet, producing
`Cannot find principal in the directory` for a principal that plainly exists.

### 3c. Push the environment variables

Fill in `.env.production` (see [Secrets](#secrets)), leaving the four
vault-backed values empty, then:

```bash
./scripts/deploy-api.sh --env-only
```

The script reads `.env.production`, verifies every vault secret exists before
uploading anything, stores Key Vault *pointers* in the Container App secret store
and the rest as plain environment variables (see
[Where the values end up](#where-the-values-end-up-after-a-deploy)), and re-pins
the replica count. Set `ALLOWED_ORIGINS` to a placeholder for now — step 5
corrects it once Netlify has given you a URL.

From here on, `./scripts/deploy-api.sh` (without `--env-only`) does the build,
the deploy, and the variable sync in one command.

**Configure the health probes.** Azure does not read the `HEALTHCHECK` line from
a Dockerfile, and this image deliberately does not define one. In the Azure
portal: **Container App → Containers → Health probes → Edit and deploy**.

| Probe | Transport | Path | Port | Notes |
|---|---|---|---|---|
| Liveness | HTTP | `/healthz` | 8000 | Touches nothing. Restarts the container if the process is wedged |
| Readiness | HTTP | `/readyz` | 8000 | `select 1` under a 2s timeout; 503 if the DB is unreachable |
| Startup | HTTP | `/readyz` | 8000 | Same endpoint; gives the pool time to open before traffic arrives |

> ⚠️ **Set Transport to HTTP.** The portal defaults every probe to **TCP**, and
> if you fill in a path without changing the dropdown the path is silently
> ignored. TCP probes are worse than none here: a wedged uvicorn still holds the
> socket open, so liveness never fires, and readiness cannot notice an
> unreachable database. Verify what actually got saved — `httpGet`, not
> `tcpSocket`:
>
> ```bash
> az containerapp show -n revenue-agents-api -g <AZURE_RESOURCE_GROUP> \
>   --query "properties.template.containers[0].probes[].{type:type,path:httpGet.path}" -o table
> ```

The split matters: `/healthz` must never check the database. A liveness probe
that touches Postgres turns a ten-second blip into a container restart, and at
one replica a restart is a full outage.

Readiness `failureThreshold` is deliberately generous (22 × 10s). At one replica
there is no healthy peer to shift traffic to, so pulling the only replica out of
rotation *is* the outage — riding out a transient Supabase blip beats reacting
quickly to it.

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
loading `/invoices` directly returns a 404. `ui/public/_redirects` is committed
and contains it; Vite copies everything in `ui/public/` into `dist/` verbatim:

```
/*  /index.html  200
```

Confirm it survived the build with `ls ui/dist/_redirects` before deploying.

The first run links the directory to a Netlify site interactively and writes
`ui/.netlify/state.json` (gitignored), so later deploys are non-interactive.
Netlify prints two URLs: the **unique deploy URL** changes every deploy, while
the **project URL** is permanent. `ALLOWED_ORIGINS` must match the permanent one.

### 5. Close the CORS loop

Now that you know the real UI URL, correct the placeholder from step 3c. Change
it in `.env.production` and re-push — **not** with a direct
`az containerapp update --set-env-vars`:

```bash
# .env.production
ALLOWED_ORIGINS=<NETLIFY_SITE_URL>
```

```bash
./scripts/deploy-api.sh --env-only
```

`deploy-api.sh` rewrites *every* environment variable from `.env.production` on
each run, so a value set directly on the container is silently reverted by the
next deploy — weeks later, with no obvious cause. The file is the source of
truth; keep it that way.

Must be `https://`, no trailing slash. The startup guard rejects plain http.

**Both origins are listed**, comma-separated — `app/config.py` splits on commas
and strips each entry. The `.netlify.app` address serves the site directly rather
than redirecting to the custom domain, so dropping it would break any bookmark
still pointing there: the app would load and then fail every request, which looks
like a broken backend rather than a CORS problem. Trim it only after setting
`revops.frogslayer.com` as the primary domain in Netlify, which makes the old
address 301 instead of serve.

Add the same URLs to Supabase: **Authentication → URL Configuration**. Site URL
is the custom domain (it builds the links in password-reset emails); Redirect
URLs should list both. Login will not complete without it.

> **A custom domain on the UI needs no rebuild** — nothing in the bundle
> references the UI's own origin. A custom domain on the *API* would, since
> `VITE_API_URL` is inlined at build time.

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

**The scripts run the static checks themselves.** `deploy-api.sh` runs
`ruff check .` before it builds; `deploy-ui.sh` runs `npx tsc --noEmit` before it
builds. Either failing stops the deploy. Both need no infrastructure and take
about a second, and they catch the import errors, syntax mistakes, and type
errors that would otherwise surface as a failed revision ten minutes into a
build — or as a runtime error on a page nobody opened yet.

`--skip-lint` and `--skip-typecheck` are the respective escape hatches, each
printing a warning. `--env-only` skips the lint entirely: that path ships no
code.

**The test suite is not gated, and running it stays your job:**

```bash
supabase start && pytest
```

That is a deliberate trade, not an oversight. `pytest` runs against the local
Supabase instance on port 54322, so gating on it would mean starting Docker and
Supabase before every deploy — too much friction for one operator deploying by
hand. The cost is real and worth naming: `tests/test_no_agent_approval_tools.py`
structurally enforces Unbreakable Rule 3 (no executor in any agent's
`allowed_tools`), and nothing in the deploy path checks it. Run the suite after
any change that touches agents, tools, or executors.

> ⚠️ **Do not deploy the API while a billing run is executing.** Container Apps
> overlaps revisions during a swap — the outgoing one is still `Deprovisioning`
> while the new one is already serving — so there are briefly **two** replicas,
> and therefore two Harvest token buckets. Single-replica pinning does not cover
> the swap window. Everything else in the [replica warning](#3-create-and-deploy-the-api)
> holds; this is the one gap in it.

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
apply all 35 of this app's migrations to it. This is the expensive mistake, and
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

Production config is split across two places, by sensitivity:

| | Lives in | Which values |
|---|---|---|
| **Real credentials** | **Azure Key Vault** (`fs-revops-kv`) | `DATABASE_URL`, `OPENAI_API_KEY`, `HARVEST_TOKEN`, `AIRTABLE_API_KEY` |
| **Everything else** | `.env.production` at the repo root | Deploy targets, plain config, the `VITE_*` build values, and `SUPABASE_DB_PASSWORD` |

The four in Key Vault are **empty in `.env.production` on purpose.** The
container resolves them at runtime via its managed identity, so there is no
plaintext copy of them on this laptop, and rotating one needs no redeploy at all.
`deploy-api.sh` names them in `KEYVAULT_KEYS` and checks each exists in the vault
before uploading anything.

`SUPABASE_DB_PASSWORD` is the one credential still in the file: `deploy-db.sh`
runs on your machine and needs it to `link`. `chmod 600` still earns its keep.

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
| Credentials — `DATABASE_URL`, `OPENAI_API_KEY`, `HARVEST_TOKEN`, `AIRTABLE_API_KEY` | **Key Vault.** The Container App secret holds only a pointer: `keyvaultref:https://fs-revops-kv.vault.azure.net/secrets/openai-api-key,identityref:system` | Requires a Key Vault data-plane role on the vault. Every access is logged there |
| Anything secret *not* in `KEYVAULT_KEYS` | The Container App **secret store**, as a literal value, referenced as `secretref:name` | Not shown by `az containerapp show`. Reading it takes `az containerapp secret show` |
| Everything else — `ENV`, `SUPABASE_URL`, `ALLOWED_ORIGINS`, account IDs | Plain environment variables | Anyone with **Reader** on the resource group |

Two classifications in `scripts/deploy-api.sh`, both worth understanding:

- `PLAIN_KEYS` is an **allowlist** of the non-sensitive. Anything not named there
  is treated as a secret, so a variable added to `.env.production` later is
  protected by default rather than by remembering to classify it.
- `KEYVAULT_KEYS` names the values sourced from the vault. To add one: put the
  secret in the vault under its kebab-case name (`SOME_KEY` → `some-key`), add
  the key to `.env.production` with an **empty** value so `container_env_keys`
  picks it up, and add it to the array.

The `keyvaultref` URI is **versionless on purpose** — Container Apps re-reads it
periodically, which is what makes rotation-without-redeploy work. Pinning a
version would defeat that.

Read a value back when you need to confirm what is live:

```bash
# Vault-backed
az keyvault secret show --vault-name <AZURE_KEYVAULT_NAME> \
  --name openai-api-key --query value -o tsv

# Confirm the container is pointing at the vault, not holding a literal
az containerapp secret list -n revenue-agents-api -g <AZURE_RESOURCE_GROUP> -o table
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

Key Vault removed the four API credentials from the laptop. What remains:

- **`SUPABASE_DB_PASSWORD` is still plaintext in `.env.production`**, because
  `deploy-db.sh` needs it locally to `link`. `chmod 600` it and keep it out of
  any directory that syncs to iCloud, Dropbox, or an off-machine backup.
- **Every deploy now depends on a live `az` session** with the right roles. A
  vault that is unreachable is a deploy that cannot complete.
- **A new failure mode:** if the container's identity loses `Key Vault Secrets
  User`, the revision fails to start and the reason is buried in the system log,
  not in the deploy output.
- **The vault is the only copy** of the four credentials outside their source
  systems. All four are re-issuable (Supabase, OpenAI, Harvest, Airtable), so
  this is recoverable — but it is a restore, not a lookup.
- Revisit if a second person needs to deploy. At that point, move
  `SUPABASE_DB_PASSWORD` into the vault too and have `deploy-db.sh` fetch it.

### Rotating a secret

**Vault-backed values need no deploy at all.** Container Apps re-reads the
versionless URI periodically, so a new version propagates on its own:

```bash
az keyvault secret set --vault-name <AZURE_KEYVAULT_NAME> \
  --name harvest-token --value '<new token>'
```

To pick it up immediately rather than waiting, restart the current revision:

```bash
az containerapp revision restart -n revenue-agents-api \
  -g <AZURE_RESOURCE_GROUP> \
  --revision "$(az containerapp show -n revenue-agents-api \
      -g <AZURE_RESOURCE_GROUP> --query properties.latestRevisionName -o tsv)"
```

**Everything else** still takes two steps — editing the file changes nothing
about what is running:

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

🔐 = sourced from Key Vault; leave **empty** in `.env.production`.

| Variable | Notes |
|---|---|
| `ENV` | Must be exactly `production`. It is a `Literal` — `prod` fails at startup rather than silently disabling every check below |
| 🔐 `DATABASE_URL` | Session pooler string. Rejected if it is the dev default or points at a local host |
| `SUPABASE_URL` | Must be `https://`. The JWKS endpoint is built from it, so every authenticated request 500s if it is wrong |
| `ALLOWED_ORIGINS` | Comma-separated. Rejected if plain http or still the dev default |
| 🔐 `OPENAI_API_KEY` | Chat fails on the first message without it |
| 🔐 `HARVEST_TOKEN` | |
| `HARVEST_ACCOUNT_ID` | |
| `HARVEST_USER_AGENT_CONTACT` | Harvest rejects requests without a contact email in the User-Agent |

### API — optional (warns at startup, does not block)

Each breaks one feature rather than the app. A deploy that refuses to start over
a parked integration is its own outage.

| Variable | If unset |
|---|---|
| 🔐 `AIRTABLE_API_KEY`, `AIRTABLE_BASE_ID` | The revenue-ops agent's `get_revenue_data` tool fails when asked |
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
| `AZURE_RESOURCE_GROUP`, `AZURE_CONTAINERAPP_NAME` | Which app `deploy-api.sh` targets |
| `AZURE_KEYVAULT_NAME` | Which vault the `keyvaultref` URIs point at. The `AZURE_` prefix is load-bearing: `container_env_keys` filters it out, so it never reaches the container |

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
| `db push` errors about migration versions | A migration was added with a short prefix | All 35 use 14-digit versions. Match that format — `supabase migration new` does it automatically |
| `Cannot find project ref. Have you run supabase link?` | Not linked | Working as intended — see [Not pushing to the wrong database](#not-pushing-to-the-wrong-database). Link, push, unlink |
| Harvest calls 401 or rate-limit | Token expired, or two replicas | Confirm `--max-replicas 1`; the rate limiter is per-process |
| Thousands of `schema "pg_pgrst_no_exposed_schemas" does not exist` (`3F000`) a day | Supabase bug: disabling the Data API does not stop PostgREST, it just empties its schema list | Harmless, but noisy. Point it at an empty schema — see [step 1b](#1b-leave-the-data-api-disabled-and-silence-its-log-spam) |
| `the --mount option requires BuildKit` during `containerapp up` | A BuildKit-only instruction reached ACR Tasks, which uses the classic builder | Remove it. Test with `DOCKER_BUILDKIT=0 docker build .` — see [step 3](#3-create-and-deploy-the-api) |
| `MissingSubscriptionRegistration` naming a namespace | Fresh subscription, provider not registered | `az provider register --namespace <name>`, wait for `Registered`, retry. See [One-time prerequisites](#one-time-prerequisites) |
| `deploy-api.sh` fails listing missing vault secrets | The secret is absent, or you lack a data-plane role | Add it, or grant yourself `Key Vault Secrets Officer`. RBAC takes ~60s to propagate |
| Revision won't start, logs mention the secret reference | The container's identity lost `Key Vault Secrets User` | Re-run the role assignment in [step 3b](#3b-create-the-key-vault-and-grant-the-container-access) |
| An env var reverts to an old value after a deploy | It was set with `az containerapp update --set-env-vars` instead of via `.env.production` | `deploy-api.sh` rewrites every variable from the file each run. Change it in the file |
| Probes configured but never firing | Portal saved them as `tcpSocket` instead of `httpGet` | Set Transport to HTTP. See [step 3](#3-create-and-deploy-the-api) |

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

- **Nothing forces the test suite to run.** The deploy scripts gate on `ruff` and
  `tsc`, which need no infrastructure — but `pytest` needs a running local
  Supabase, so it stays a habit rather than a gate (see
  [Routine deploy](#routine-deploy)). Even the checks that do run work against
  your working tree, not the committed state, and are one flag from being
  skipped. That is the honest limit of a gate you own and can wave through.
- **Nothing checks the Azure subscription.** `deploy-db.sh` verifies which
  Supabase project it linked; `deploy-api.sh` deploys wherever `az` currently
  points. Confirm with `az account show -o table` if you have switched contexts.
- **Only this laptop can deploy.** Make sure the Azure, Netlify, and Supabase
  credentials are recoverable from a password manager, not just this keychain.
- **Infrastructure is not reproducible from code.** If the resource group is
  deleted, you rebuild by re-running [First-time setup](#first-time-setup).

Revisit when a second person needs to deploy, or when a second environment
(staging) appears. Both change the arithmetic; neither is true today.
