# GraphDrift

Real-time UPI fraud detection system.

## Backend Setup

1. Create and activate a virtual environment:

   ```bash
   python -m venv venv
   source venv/bin/activate   # macOS/Linux
   # venv\Scripts\activate    # Windows
   ```

2. Install dependencies:

   ```bash
   pip install -r backend/requirements.txt
   ```

3. Copy the example env file and adjust if needed:

   ```bash
   cp backend/.env.example backend/.env
   ```

   `DATABASE_URL`, `ALLOWED_ORIGINS`, and the optional `LIVE_FEED_WS_URL` are
   environment-configured; do not put deployment URLs in Python code.

4. Start the development server from the `backend/` folder:

   ```bash
   cd backend
   uvicorn app.main:app --reload
   ```

The API will be available at `http://localhost:8000`. Check health at `GET /health`.

### Provision users

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

Sessions are signed JWTs with a 12-hour default lifetime. Logout is stateless:
the frontend discards its token from `sessionStorage`; no refresh token or
server-side blacklist is used in this scope. Changing `SESSION_SECRET`
invalidates all outstanding sessions.

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
cp .env.development.example .env.development
npm install
npm run dev
```

Set `VITE_DEV_BACKEND_URL` in `.env.development` to the backend URL. The Vite app
will proxy `/api`, `/health`, and `/ws` to that URL during local development.

## Deployment

### Backend (Render)

- **Root directory:** `backend`
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

Set `ALLOWED_ORIGINS` to the live frontend URL once it is known (comma-separated if you have more than one origin).
Also set `ENVIRONMENT=production`, a persistent production `DATABASE_URL`, and
a long random `SESSION_SECRET`. After the first deployment, open a Render shell
and run `python scripts/create_user.py --username your-admin --role admin`.
Startup logs warn if CORS is unset/wildcard or no admin exists.

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
