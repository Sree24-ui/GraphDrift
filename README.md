# GraphDrift

Real-time UPI fraud detection system.

## Backend Setup

1. Create and activate a virtual environment:

   ```bash
   python3.14 -m venv venv   # the version CI and the cited results use
   source venv/bin/activate   # macOS/Linux
   # venv\Scripts\activate    # Windows
   ```

2. Install dependencies:

   ```bash
   pip install -r backend/requirements.lock
   ```

   `requirements.lock` pins every transitive dependency of the environment that
   produced the cited evaluation results; `requirements.txt` lists the direct
   dependencies you edit.

   LightGBM is installed for the optional analyst-feedback learned signal
   (`enable_learned_signal`, off by default). It is imported only when that
   signal is enabled; on macOS that needs the OpenMP runtime
   (`brew install libomp`).

3. Copy the example env file and adjust if needed:

   ```bash
   cp backend/.env.example backend/.env
   ```

   `DATABASE_URL`, `ALLOWED_ORIGINS`, `LOGIN_RATE_LIMIT` and the optional
   `LIVE_FEED_WS_URL` are environment-configured; do not put deployment URLs in
   Python code. `SESSION_SECRET` is deliberately empty in the example: local
   development then generates a random secret per process.

4. Start the development server from the `backend/` folder:

   ```bash
   cd backend
   uvicorn app.main:app --reload
   ```

The API will be available at `http://localhost:8000`. Check health at `GET /health`.

### Provision users

**Every API endpoint requires authentication — reads as well as writes.** A
request without a valid bearer token gets `401`, including `GET /api/alerts`,
`/api/rings`, `/api/settings`, `/api/accounts/{id}`, `/api/graph/current` and
the CSV at `/api/reports/export`. Both `analyst` and `admin` can read; only
`admin` may change settings or run a calibration tick. `/health` is the sole
unauthenticated route, so deployment health checks keep working.

The `/ws/live-feed` WebSocket is authenticated too. A browser cannot set an
`Authorization` header on a WebSocket handshake, so the same JWT is passed as
a `?token=` query parameter and validated identically. An absent or invalid
token is refused during the handshake, before the connection is accepted, so
the client sees the upgrade rejected with `HTTP 403` rather than a connected
socket that closes. Because the token is only presented at the handshake, the
server closes an open socket with code 1008 when the session expires, and the
frontend treats that exactly like a REST `401` and signs the user out.

There is no public registration endpoint. Create the first administrator from
the backend directory after the database is configured:

```bash
python scripts/create_user.py --username your-admin --role admin
```

The command securely prompts twice and stores an Argon2 password hash. Create
analysts the same way with `--role analyst`. For one-command local setup only:

```bash
python scripts/create_user.py --dev-seed
```

This creates `local-admin` with password `local-development-only` and refuses
to run when `ENVIRONMENT=production`.

Sessions are signed JWTs with a 12-hour default lifetime. Each token carries a
`jti`, and logging out records it in `revoked_tokens`: the token is refused by
every endpoint and by the WebSocket handshake from then on, so logout does not
depend on the client discarding it. Expired entries are cleared on the next
logout. There is no refresh token, and no way to end another user's session
short of changing `SESSION_SECRET`, which invalidates all of them.

In production (`ENVIRONMENT=production`) the interactive API docs and the
OpenAPI schema are not served: `/docs`, `/redoc` and `/openapi.json` return 404.
They remain available in development.

## Pre-demo reset (recommended)

Before a live demo or recording, reset accumulated simulation data so alerts
start from zero and build up a small, legible queue over the first 15–20
minutes instead of inheriting a long-session backlog (hundreds of open alerts).

From the `backend/` folder with your virtualenv active:

```bash
python scripts/reset_demo_data.py
```

Type `yes` when prompted — this **permanently deletes** all rows in
`transactions`, `alerts`, `account_score_history`, and `accounts`.

Then **restart the backend server** so the in-memory simulation account pool
is re-seeded from the empty database:

```bash
uvicorn app.main:app --reload
```

After restart, open the Alert Queue (`/alerts`) and let the simulator run for
15–20 minutes; you should see a fresh, manageable set of cases accumulate.

## Frontend Setup

From the `frontend/` folder:

```bash
cd frontend
npm install
```

> **Required:** Configure the local API proxy before starting Vite. Without this
> file, every `/api/*` request will return Vite's own 404 response.
>
> ```bash
> cp .env.development.example .env.development
> ```
>
> Double-check that `VITE_DEV_BACKEND_URL` in `.env.development` points to your
> running backend (the example uses `http://localhost:8000`).

Then start the frontend:

```bash
npm run dev
```

The Vite app will proxy `/api`, `/health`, and `/ws` to that URL during local
development. If the env file or value is missing, Vite prints a prominent startup
warning with the exact copy command above.

## Deployment

### Backend (Render)

- **Root directory:** `backend`
- **Build command:** `pip install -r requirements.lock`
- **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

Required environment variables:

- `ENVIRONMENT=production`
- `SESSION_SECRET` — at least 32 characters, generated with
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Startup refuses
  a missing, short, or example secret: anyone who knows the signing secret can
  forge an admin session.
- `DATABASE_URL` — persistent storage; the SQLite default is lost on an
  ephemeral disk.
- `ALLOWED_ORIGINS` — the live frontend URL (comma-separated for several).
  `*` is refused in production.
- `FORWARDED_ALLOW_IPS=*` — Render terminates traffic at its proxy. Without
  this, uvicorn sees the proxy's IP for every user, so all logins share one
  rate-limit bucket. Only set it when the app is reachable solely through the
  proxy.

Optional: `SESSION_TTL_SECONDS` (default 43200) and `LOGIN_RATE_LIMIT`
(default `10/minute`). After the first deployment, open a Render shell and run
`python scripts/create_user.py --username your-admin --role admin`. Startup
logs warn if CORS is unset or no admin exists.

### Frontend (Vercel)

- **Root directory:** `frontend`
- **Build command:** `npm run build`
- **Output directory:** `dist`

Set `VITE_API_BASE_URL` and `VITE_WS_BASE_URL` in the Vercel project dashboard as shown in `frontend/.env.production.example`.

After both services are deployed, update `ALLOWED_ORIGINS` on the Render backend to the real Vercel URL and redeploy or restart the backend so CORS allows the live frontend to connect.

## Closed-loop calibration verification

The full real-pipeline harness uses the seeded simulator, detector, actual
per-alert scoring windows, ground-truth judgments, and live calibration tick:

```bash
cd backend
python evaluation/closed_loop_calibration.py --cycles 60
```

It reports the threshold and confirmed-rate trajectory plus whether the run
settled inside the control band, remained unsettled, or rode a clamp.
