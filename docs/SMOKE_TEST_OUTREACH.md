# Smoke Test — Outreach chain end-to-end

A 5-minute manual run of the full Outreach chain — from "Reach out" click through to the Gmail-send stub log line. Use this when verifying changes that touch the orchestrator, the chain handlers, or the inbox/trace UI.

> Hits no real Gmail or HubSpot. Safe to run any time.

## Prerequisites

1. **Local Supabase** — `supabase start` (Postgres on `127.0.0.1:54322`).
2. **API container** — `docker compose up -d --build api`. Wait for `curl -sf http://localhost:8000/healthz` to return 200. The API auto-seeds agents and the voice profile memory on startup.
3. **UI dev server** — `cd ui && npm run dev` (defaults to port 3000; ignore the others if `PORT` is set).
4. *Optional*: set `ANTHROPIC_API_KEY` in `app/.env` to exercise real LLM calls. With it unset, the chain uses the deterministic stub responses defined in [`app/orchestrator/chains/outreach.py`](../app/orchestrator/chains/outreach.py).

## 1. Trigger the chain from the dashboard

1. Open `http://localhost:3000`.
2. On the **Outreach Agent** card, click the cyan **Reach out** button.
3. Enter any HubSpot contact id when prompted (e.g. `demo-contact-001`). With `HUBSPOT_TOKEN` empty the chain uses placeholder contact data; with the token set it would call HubSpot for real.
4. The page navigates to `/inbox` after a short delay.

Expected: a new "send_email" inbox row appears within 2–6 seconds (depends on whether real LLM calls run).

## 2. Inspect the trace

1. Click the new inbox row.
2. The detail page shows:
   - **Header** — `SEND_EMAIL · sequence #N · PROPOSED`, the email subject, timestamp.
   - **Workflow Trace panel** — collapsed by default with a one-line summary like `8 steps · awaiting approval`. Click to expand.
3. Expand the trace. Confirm:
   - Steps 1–4 are completed (HubSpot, web search, consolidate, knowledge base).
   - Step 5 is the LLM draft (completed).
   - Steps 6–7 are critique steps with collapsed `critique passed · score …` lines. Click to expand and see the feedback paragraph plus any issues.
   - Step 8 is the execution gate — the only `proposed` row.

If you trigger with a real Anthropic key and the model produces an off-voice draft, you should also see retry rows indented under step 5, with the original strikethrough and the latest attempt highlighted.

## 3. Approve and verify the send

1. Scroll past the trace to the **Action Payload** block — it shows the draft as JSON (subject, body, to, to_name).
2. Click **Approve**.
3. The page stays on the inbox detail; the trace status updates from `proposed` to `completed` within a second or two.

Server-side check:

```sh
docker logs revenue-agents-api-1 --tail 20 | grep gmail-stub
```

Expect a line like:

```
INFO:app.orchestrator.chains.outreach:[gmail-stub] would send subject='Quick thought after your Series B' to='schen@acmecorp.example'
```

The workflow row should now have `status='completed'` and the execution action `result.stub=True` with the would-send recipient and subject.

## 4. Reject path

To verify the cancel-on-rejection branch:

1. Trigger another outreach run.
2. On the inbox detail, click **Reject**, type a reason ("off voice"), and confirm.
3. The execution action moves to `rejected`; the workflow moves to `cancelled`. No `gmail-stub` log line is emitted. The audit log records `workflow.cancelled` with the rejection reason.

## 5. Critique loop (real LLM only)

This step requires a real `ANTHROPIC_API_KEY` because the stub critic always passes.

1. Edit `app/orchestrator/chains/outreach.py` and tighten the voice profile prompt's DO-NOT list with something the default draft model is likely to do (e.g. add "must not start with the recipient's name").
2. Restart the API: `docker compose up -d --build api`.
3. Trigger an outreach run. Watch the trace expand to show retried draft attempts indented under the original.
4. Confirm `attempt 2/3` and `attempt 3/3` appear on the retry rows.
5. If all three voice attempts fail, the workflow ends in `failed` and the audit log records `workflow.failed` with `max_attempts` in the error message.

## What to fix if it fails

| Symptom | Likely cause |
|---|---|
| 502/CORS in the browser, but `curl http://localhost:8000/...` works | UI dev port not in `ALLOWED_ORIGINS` (defaults to 3000). |
| `Reach out` returns "Could not resolve authentication method" | `ANTHROPIC_API_KEY` is set but empty. Either fully set it or unset it (the chain falls back to stubs only when fully unset). |
| Trace shows steps 1–4 then stops | LLM step raised — check `docker logs revenue-agents-api-1` for a stack trace. |
| Inbox shows multiple rows for one workflow | A new chain step besides `checkpoint`/`execution` was given a pausing step kind. The inbox query in [`app/routers/actions.py`](../app/routers/actions.py) only lets those two through. |
| "agent slug 'voice-critic' not found or inactive" 500s | The agent registry didn't seed. Restart the API; lifespan startup runs `seed_agents()` then `seed_voice_profile()`. |
