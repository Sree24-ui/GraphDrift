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

4. Start the development server from the `backend/` folder:

   ```bash
   cd backend
   uvicorn app.main:app --reload
   ```

The API will be available at `http://localhost:8000`. Check health at `GET /health`.

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
