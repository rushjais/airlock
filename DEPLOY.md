# Deploying Airlock

The hosted demo is one Railway service (the API serves the built frontend; the payment executor runs as a second
process in the same container, bound to localhost) plus a Railway Postgres database. It boots with
`DEMO_MODE=1` and no secrets: the only variable it needs is `DATABASE_URL`.

Currently deployed: Railway project **airlock**, services **airlock** (app) and **Postgres**, at
https://airlock-production-31d8.up.railway.app.

## What the container does

- `Dockerfile`: builds the frontend (Node 20), installs the backend with `uv` (Python 3.13), runs as a non-root user.
- `scripts/start.sh`: starts the executor on `127.0.0.1:8001`, waits for it, then the API on `$PORT`. If either
  process exits, the container exits and Railway restarts it.
- `railway.json`: Dockerfile builder, health check on `/health` (it also does a database round trip).
- On boot the app creates a few warm per-visitor sandboxes (Postgres schemas) so the first click is fast.

## Redeploy (CLI, what was used)

From the repo root, with the Railway CLI logged in and this folder linked to the project:

```bash
railway up --service airlock --detach
```

Check it:

```bash
curl -s https://airlock-production-31d8.up.railway.app/health
```

Expect `{"ok":true,"demo_mode":true}`.

## From scratch (CLI)

```bash
railway init -n airlock
```

```bash
railway add -d postgres
```

```bash
railway add -s airlock -v 'DATABASE_URL=${{Postgres.DATABASE_URL}}' -v DEMO_MODE=1 -v AIRLOCK_DB_POOL=20
```

```bash
railway up --service airlock --detach
```

```bash
railway domain --service airlock
```

## From scratch (dashboard)

1. railway.com → **New Project** → **Deploy from GitHub repo** → pick `rushjais/airlock`. Railway finds
   `railway.json` and builds the `Dockerfile`.
2. In the project, **+ Create** → **Database** → **PostgreSQL**.
3. Open the app service → **Variables** → add:
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (use the reference picker)
   - `DEMO_MODE` = `1`
   - `AIRLOCK_DB_POOL` = `20`
4. App service → **Settings** → **Networking** → **Generate Domain**.
5. App service → **Settings** → **Source**: set the branch to `main` so every push to main redeploys.
6. Wait for the deploy to turn green (the health check is `/health`), then open the domain.

## Optional variables

| Variable | Default | What it does |
|---|---|---|
| `AIRLOCK_DEMO_WARM` | `2` | Sandboxes kept pre-built |
| `AIRLOCK_DEMO_MAX_SANDBOXES` | `150` | Oldest idle sandboxes are dropped past this |
| `AIRLOCK_DEMO_IDLE_TTL_S` | `10800` | Idle sandboxes are dropped after this |
| `AIRLOCK_DEMO_NEW_PER_IP_HOUR` | `20` | New sessions per IP per hour |
| `AIRLOCK_DEMO_ACTIONS_PER_IP_HOUR` | `150` | Clicks per IP per hour |
| `STRIPE_SECRET_KEY` | unset | A Stripe **test** key (`sk_test_...`) switches the simulated rail to real test-mode transfers. `start.sh` hands it only to the executor. The Stripe test accounts in `fixtures/company.json` belong to the original platform account, so a different key also needs its own connected test accounts. |

Nothing else is needed. The demo never calls a model, never sends email or texts, and never reads `.env`.

## Cost and abuse limits

- No model calls at runtime (the reader's recorded output is replayed), so there is no per-request spend.
- Per-IP rate limits on new sessions and clicks; each session caps at 120 emails; idle sandboxes are dropped.
- Local-only endpoints, the email webhook and the agent API return 404 in demo mode.
- The remaining cost is the Railway service and Postgres (usage-based).

## If hosting is down: record locally

```bash
scripts/demo-local.sh
```

It builds and starts the same container with Docker Compose on http://localhost:8000, waits for `/health`, and
opens the browser. Ctrl-C stops it.

## Recording the GIF for the README

1. Open the live demo (or the local one) in a browser window about 1280 px wide.
2. Record the screen (macOS: Cmd-Shift-5, record selected portion) following the 60-second script below.
3. Convert to a GIF under ~8 MB, for example:

```bash
ffmpeg -i demo.mov -vf "fps=12,scale=1100:-1:flags=lanczos" -loop 0 docs/demo.gif
```

4. In `README.md`, replace the `[FILL: demo.gif]` line with `![Airlock demo](docs/demo.gif)`.

### 60-second script

1. (0-8s) Land on the page. "Airlock is payment authorization for AI agents. Email can inform a payment, never
   redirect one."
2. (8-16s) Click **Legitimate invoice**. "Real vendor, approved order, account on file: paid."
3. (16-32s) Click **Vendor "changed banks"**. "Same real mailbox, new account: held. Airlock texts the vendor's
   phone already on file." Click **Reply NO**. "Paid to the account on file, $0 to the attacker's."
4. (32-46s) Click **Prompt injection in the email**. Point at the model's read. "The model's read can be wrong; the
   payment decision never depends on it." Click **What if the model had been fooled?** "Still held."
5. (46-54s) Click **Lookalike vendor domain**, then **Urgent "CEO" wire**. "Both denied at routing."
6. (54-60s) "Every rule here has a test. Link in the README."
