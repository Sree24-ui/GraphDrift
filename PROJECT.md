# GraphDrift — Project Reference

GraphDrift (also called FraudGuard) is a real-time detection system for
**UPI money-mule fraud**. It watches a stream of payments, scores every account
on two independent layers — how anomalous its own behaviour is, and whether it
sits at the centre of a hub-and-spoke ring — fuses those scores, and hands the
highest-risk accounts and rings to human analysts for review.

This document is the single entry point to the project: what it is, how it
works, how to run and test it, what it has been measured to achieve, and what
it cannot yet do. Evaluation numbers are summarised here; the full working log
with every per-seed table lives in
[`backend/evaluation/RESULTS.md`](backend/evaluation/RESULTS.md), which is the
source of truth for any figure you intend to cite.

---

## Contents

1. [Status](#1-status)
2. [Architecture](#2-architecture)
3. [Detection pipeline](#3-detection-pipeline)
4. [Analyst-feedback calibration](#4-analyst-feedback-calibration)
5. [Authentication and security](#5-authentication-and-security)
6. [API reference](#6-api-reference)
7. [Frontend](#7-frontend)
8. [Configuration](#8-configuration)
9. [Repository layout](#9-repository-layout)
10. [Running locally](#10-running-locally)
11. [Testing and CI](#11-testing-and-ci)
12. [Evaluation results](#12-evaluation-results)
13. [Known limitations and open evasion vectors](#13-known-limitations-and-open-evasion-vectors)
14. [Key design decisions](#14-key-design-decisions)
15. [Known tooling issues and follow-ups](#15-known-tooling-issues-and-follow-ups)
16. [Deployment](#16-deployment)
17. [Working norms](#17-working-norms)

---

## 1. Status

| | |
|---|---|
| Repository | https://github.com/Sree24-ui/GraphDrift (branch `main`) |
| Backend tests | **117 passing** |
| Frontend | `npm run build`, `tsc --noEmit`, `oxlint` all clean; 8 axe scans green |
| CI | GitHub Actions — backend (pytest), frontend (build) and accessibility (axe) jobs |
| Python | **3.14** (pinned in CI to match development) |
| Node | **24.20.0** — Active LTS "Krypton" line (pinned in CI) |
| Backend deps | 22 direct pins in `requirements.txt`; full transitive lock in `requirements.lock` (CI installs from it) |
| Detection default | single-node hub concentration; co-hub scoring **off**; learned signal **off** |
| Frontend bundle | initial load 294 KB (96 KB gzip); every authenticated page is lazy-loaded |

---

## 2. Architecture

```
                     ┌──────────────────────────────────────────┐
  simulator  ──tx──▶ │  SQLite (accounts, transactions, alerts) │
  (every ~2s)        └───────────────┬──────────────────────────┘
                                     │ every 45s
                     ┌───────────────▼──────────────────────────┐
                     │  run_detection_cycle                      │
                     │   Layer 1  Mahalanobis GDI  (15m + 60m)   │
                     │   Layer 2  Louvain rings    (15m + 60m)   │
                     │   Fusion   percentile 50/50, union top-k  │
                     │   Peripheral cascade (15m)                │
                     │   Persist  alerts + score history         │
                     └───────────────┬──────────────────────────┘
                                     │  every 10th cycle (opt-in)
                     ┌───────────────▼──────────────────────────┐
                     │  calibration tick → alert top-share       │
                     └───────────────┬──────────────────────────┘
                                     │
          REST (JWT bearer) ◀────────┼────────▶ WebSocket /ws/live-feed (?token=)
                                     │
                     ┌───────────────▼──────────────────────────┐
                     │  React SPA — Live Monitor, Alert Queue,   │
                     │  Account Detail, Reports, Settings        │
                     └──────────────────────────────────────────┘
```

**Backend** — FastAPI, SQLAlchemy 2, SQLite, NetworkX + python-louvain,
NumPy/SciPy, PyJWT + argon2-cffi for auth, slowapi for rate limiting,
structured JSON logging. scikit-learn is used only for the Isolation Forest
evaluation baseline.

**Frontend** — React 19, React Router 7, Vite 8, TypeScript 6, Tailwind 3,
Recharts, react-force-graph-2d, axios, oxlint.

**Shared** — `shared/detection_knobs.json` is imported by *both* the backend
(`app/constants.py`) and the frontend (`frontend/src/knobs.ts`), so a detection
number is defined exactly once.

Three background tasks run in the FastAPI lifespan (`app/main.py`): the
transaction simulator, the detection loop (45 s), and a metrics broadcaster
(10 s).

---

## 3. Detection pipeline

### 3.1 Layer 1 — node anomaly (GDI)

`app/detection/node_anomaly.py`. For each account with **≥ 3 transactions** in
the window, eight features are extracted (`app/detection/features.py`):

`in_degree`, `out_degree`, `in_count`, `out_count`, `amount_entropy`,
`counterparty_diversity`, `fan_ratio`, `burstiness`

(`velocity` is computed for display but not scored.)

The account's **Mahalanobis distance** from the population baseline is taken
with a shrinkage-regularised covariance (α = 0.1, variance floor 1e-6), clipped
at 12, and mapped linearly to a **GDI display score on [0.5, 5.0]**.
`compute_gdi_scores` attaches the shared baseline to every row so explanation
building reuses it — this "explanation cache" is what fixed an O(N²) cycle-time
blow-up (see §12.6).

### 3.2 Layer 2 — ring detection

`app/detection/community.py`. Builds a directed transaction graph for the
window, runs **Louvain** (resolution 2.0, fixed `random_state=42`), and scores
each community:

| Component | Weight | Signal |
|---|---|---|
| `hub_concentration` | 0.45 | share of internal edges touching the busiest node — mule rings are stars |
| `external_edge_ratio` | 0.30 | pass-through flow (capped at 10) |
| `formed_recently` | 0.15 | community did not exist in recent partitions (only for ≥ 4 members) |

Each component is scaled by `gdi_max` and the total capped at 5.0. Communities
clearing `risk_threshold` (2.0) with ≥ 4 members become ring alerts.

`internal_density` is computed for diagnostics only and **deliberately not
scored**: star-shaped mule rings have low density by construction, so a
density score misses exactly the rings this system exists to find.

### 3.3 Fusion

`app/detection/fusion.py`. Layer 1 and Layer 2 scores are converted to
**percentile ranks within the cycle** and combined 50/50, so neither layer's
raw scale dominates.

**Confidence** — `high` if both GDI and ring percentiles exceed 0.9, `medium`
if either does, otherwise `low`. Confidence is a two-layer judgement and is not
affected by the optional learned signal (§3.8).

### 3.4 Multi-scale: union, not max-merge

Fusion runs independently at **15 minutes** and **60 minutes**. Each scale
takes its own **top 5%** (the alert top-share), and the two sets are
**unioned** (`select_top_anomaly_accounts_multiscale`).

An earlier version took `max(15m, 60m)` per account and applied one global
cut. That was wrong — percentile ranks from different windows are not
comparable, and it let a merely-high 60m account displace a genuine 15m star.
`compute_fused_scores_multiscale_max_merge` now **raises** so it cannot be used
by accident. Never re-cut a union result with `select_top_anomaly_accounts`.

All windows are **sliding**, `[as_of − window, as_of]`, recomputed each cycle.
Nothing is aligned to clock boundaries.

The top-k budget is `ceil(n × top-share)`, rounded to 9 decimals first: in
floating point `1 − 0.95 = 0.05000000000000004`, which used to flag one extra
account whenever `n` was a multiple of 20.

### 3.5 Peripheral structural cascade

`app/detection/structural_pass.py`. Accounts with only 1–2 transactions cannot
be scored by Layer 1 (their rate features are degenerate). The cascade flags
such accounts when they are **1 hop from a hub already in the union set** and
show a fan-in-sender or fan-out-receiver leg shape. It runs at the 15-minute
scale.

Honest characterisation (from RESULTS.md): this is *guilt by 1-hop association
with a flagged hub, conditioned on leg shape* — not independent pattern
discovery. It fires only when the hub is already selected, so a wrong hub
propagates and a missed hub leaves its spokes invisible.

### 3.6 Alerts and rings

- **Statuses:** `new` → `reviewing` | `confirmed` | `false_positive`;
  `reviewing` → `confirmed` | `false_positive`. `confirmed`,
  `false_positive` and `auto_closed` are terminal.
- **Auto-close:** `new` alerts older than 2 hours become `auto_closed`.
- **Escalation:** an open alert is re-raised when its score rises by more than
  10% or its confidence increases.
- **Ring grouping:** every alert carries a stable `ring_id`; peripheral
  accounts inherit their hub's ring. Analysts can review a whole ring in one
  action (`PATCH /api/rings/{ring_id}`), which cascades to open members and
  skips terminal ones.
- **Audit:** reviews record `reviewed_by_user_id`; ring actions are logged in
  `ring_review_actions`.

### 3.7 Co-hub scoring (optional, off by default)

`_detect_co_hub` in `community.py` treats 2–4 coordinated, similar-degree nodes
as one logical hub, closing the `diluted_hub` evasion. It is gated on
`enable_cohub_scoring` (default `false`) because enabling it lowers accuracy on
every other measure. See §12.5 and §14.

### 3.8 Analyst-feedback learned signal (optional, off by default)

`learned_signal.py`. A LightGBM classifier trained on judged alerts
(`confirmed` vs `false_positive`) predicts `P(confirmed)` for every scored
account; its percentile rank joins fusion as a third input (equal thirds).
Inputs are only what the two layers already compute — the eight Layer-1
z-scores, `gdi_score`, `ring_risk_score`, `hub_concentration` — read from each
alert's persisted explanation, so training and serving inputs match by
construction. Peripheral-cascade alerts are excluded: fusion never scored them.

Gated on `enable_learned_signal` (default `false`). Even when enabled it stays
inactive, and logs why, until there are **50 labels of each class from at least
5 accounts** and account-grouped cross-validation beats both trivial baselines
(always-"confirmed" for F1, majority for accuracy). On every database in this
project it refuses: the real review history is 3–13 labels per class. See
§12.10.

---

## 4. Analyst-feedback calibration

`app/detection/calibration.py` — an opt-in **control loop on the alert
top-share**, not a filter. Default **off**.

- **Measured quantity:** confirmed rate = confirmed / (confirmed +
  false_positive) over the last 50 judged alerts. `new`, `reviewing` and
  `auto_closed` are excluded, matching Reports.
- **Target band:** 60–85%. Below → tighten (smaller top-%); above → loosen
  (larger top-%, a proxy for missed fraud).
- **Step:** fixed ±0.5 percentage points, never proportional.
- **Damping:** two consecutive ticks on the same side are required before a
  step.
- **Clamp:** automatic moves stay within **2–15%**. The manual slider allows
  **1–25%**; manual values are never snap-corrected.
- **Manual wins:** an operator's Apply resets the damping state; automatic
  writes are tagged so they never look like a manual override.
- **Cadence:** ticked from the live loop in `app/main.py`, acting every 10th
  detection cycle (~7.5 min). It is deliberately **not** inside
  `run_detection_cycle`, so evaluations and benchmarks stay unaffected.

Settings are **in memory** and reset when the process restarts.

---

## 5. Authentication and security

- **Accounts:** per-user, provisioned with `scripts/create_user.py` (no public
  registration). Roles are `analyst` and `admin`.
- **Passwords:** Argon2, minimum 12 characters.
- **Sessions:** HS256 JWTs, 12-hour default lifetime, each carrying a `jti`.
  Logout revokes that `jti` server-side (`revoked_tokens`), so the token is
  refused by every endpoint and by the WebSocket handshake from then on, not
  merely dropped by the client. Rotating `SESSION_SECRET` still invalidates all
  sessions at once.
- **API schema:** `/docs`, `/redoc` and `/openapi.json` are served only outside
  production; a production process 404s all three.
- **Every endpoint requires a token — reads as well as writes** — except
  `/health`, `POST /api/auth/login` and `POST /api/auth/logout`. Guards are
  applied at router level so new routes are covered automatically.
- **Admin-only:** `PATCH /api/settings` and
  `POST /api/settings/calibration/tick`.
- **WebSocket:** browsers cannot set headers on a WebSocket handshake, so the
  same JWT is passed as `?token=`. HTTP and WebSocket validate through one
  shared function, `user_from_token`, so they cannot drift apart. A missing or
  invalid token is refused before the connection is accepted (the client sees
  `HTTP 403`). The check runs off the event loop and releases its DB
  connection immediately, so open sockets never hold pool connections (40
  concurrent sockets verified). **Session expiry is enforced on open sockets
  too:** the server closes with code 1008 when the token expires, and the
  client signs out.
- **No ground truth on the wire:** `is_synthetic_attack` is never sent by the
  REST API or the WebSocket, so analysts reviewing alerts cannot see labels.
- **Rate limiting:** login is limited per client IP (`LOGIN_RATE_LIMIT`,
  default `10/minute`). Behind a reverse proxy, set `FORWARDED_ALLOW_IPS` so
  uvicorn sees real client IPs; otherwise every user shares one bucket and a
  few failed logins lock out the whole team.
- **Headers:** `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: strict-origin-when-cross-origin`.
- **Production guards:** with `ENVIRONMENT=production`, startup refuses a
  missing `SESSION_SECRET`, the published example secret, a secret shorter than
  32 characters, and `*` in `ALLOWED_ORIGINS` (with credentials enabled,
  Starlette would echo any origin). It warns on unset CORS (valid for
  same-origin hosting) and on a missing admin. `--dev-seed` refuses to run in
  production. `.env.example` ships an **empty** secret: copying it verbatim
  previously gave production a publicly known signing key, which let anyone
  forge an admin session without a password.

Regression tests assert that every read endpoint returns `401` without a token
and `200` for both roles, that the WebSocket rejects absent and malformed
tokens and closes on expiry, that labels never reach the API, and that each
unsafe production configuration is refused.

---

## 6. API reference

All paths except those marked **public** require `Authorization: Bearer <jwt>`.

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/health` | public | liveness |
| POST | `/api/auth/login` | public, rate-limited | returns JWT, username, role |
| POST | `/api/auth/logout` | public | revokes the presented token; idempotent |
| GET | `/api/auth/me` | user | current user |
| GET | `/api/alerts` | user | paginated, filterable alert list |
| GET | `/api/alerts/{alert_id}` | user | alert detail with explanation and escalation history |
| PATCH | `/api/alerts/{alert_id}` | user | change status / notes |
| GET | `/api/rings` | user | ring-grouped queue |
| GET | `/api/rings/{ring_id}` | user | ring detail and members |
| PATCH | `/api/rings/{ring_id}` | user | bulk-review a ring |
| GET | `/api/accounts/{account_id}` | user | account detail and transactions |
| GET | `/api/accounts/{account_id}/score-history` | user | score over time |
| GET | `/api/graph/current` | user | live graph snapshot |
| GET | `/api/graph/replay` | user | historical graph (≤ 24 h window) |
| GET | `/api/reports/summary` | user | volumes, outcomes, review time (`24h`/`7d`/`30d`) |
| GET | `/api/reports/export` | user | CSV of alerts for the period |
| GET | `/api/settings` | user | top-share, calibration state, frozen system knobs |
| PATCH | `/api/settings` | **admin** | set top-share / enable calibration |
| POST | `/api/settings/calibration/tick` | **admin** | force a calibration tick (debug) |
| WS | `/ws/live-feed?token=` | user | `transaction`, `alert`, `metrics_update` messages |

Interactive docs are served at `/docs` when the backend is running.

---

## 7. Frontend

`frontend/src/`

| Page | Route | What it does |
|---|---|---|
| Login | `/login` | sign-in; every other route redirects here without a session |
| Live Monitor | `/` | live force-directed graph, metrics, streaming alert feed with explainability, 15-minute replay scrubber |
| Alert Queue | `/alerts` | filter/sort alerts; **By Account** or **By Ring** view; single and bulk review |
| Account Detail | `/accounts/:accountId` | features, score history, transactions, connected accounts |
| Reports | `/reports` | alert volumes, outcomes, confirmed rate with a small-sample warning (< 20 reviewed), CSV export |
| Settings | `/settings` | alert top-share slider, calibration toggle and status (admin-only writes) |

Notable implementation details:

- The axios client attaches the stored token to every request and broadcasts
  a `graphdrift:unauthorized` event on `401`, which clears the session.
- Tokens live in `sessionStorage`.
- `useLiveFeed` reconnects with exponential backoff (1 s → 30 s). Sockets it
  closes on purpose are tracked in a `WeakSet` and never trigger a reconnect,
  which fixed a dev-mode reconnect loop caused by React StrictMode's double
  mount. Real errors still reconnect.
- The Settings page's 20-second quiet poll does not overwrite the slider while
  the operator is dragging it.
- Risk colour bands are expressed as fractions of `gdi_max`, so they track the
  knobs file.
- Every authenticated page is lazy-loaded, so the login screen ships only the
  294 KB shell (was 753 KB); `recharts` (333 KB) loads with Reports or Account
  Detail, and the force graph with Live Monitor. Splitting the force graph into
  its own chunk was measured and rejected: it is used by one page and is that
  page's main content, so it costs an extra request and +4 KB gzip overall to
  take 2.6 KB gzip off the initial load.
- Every WebSocket URL is built by one function, `liveFeedUrl()`, so no socket
  can omit the token. A 401 or a WebSocket close with 1008 both fire the same
  sign-out event.
- Below 1280 px Live Monitor stacks and scrolls, with the graph kept at a
  usable height (it previously collapsed to 2 px on laptop and tablet widths).
  Queue action columns are sticky, so review buttons stay visible.
- Account Detail gets "which counterparties have open alerts" from the server
  rather than paging through every open alert, counts only open alerts, and
  shows no threshold line: alerts come from a population-relative top-k cut,
  so no fixed score threshold exists.
- `useAuth` lives in its own module so `AuthContext.tsx` exports only a
  component (keeps Fast Refresh working).
- **Accessibility.** axe-core 4.10 reports **0 violations** on all six pages
  (Live Monitor, both Alert Queue views, Account Detail, Reports, Settings);
  the baseline had 5 distinct violations, including a critical unlabelled
  slider and `nested-interactive` on every queue row. Queue rows are no longer
  `role="button"`; each row has a real expand button carrying `aria-expanded`
  and `aria-controls`, so the table is operable with Tab alone. One global
  `:focus-visible` ring covers every control. Badge contrast was measured by
  compositing the translucent layers axe cannot resolve: **5.97-9.24**, all
  above 4.5, and risk is never colour-only (each pill carries an icon and the
  numeric score, each badge its text).
- **Toasts.** `ToastProvider` renders `role="status"` for successes and
  `role="alert"` for failures, and every review, ring action, notes save and
  settings write reports through it. `errorMessage()` surfaces the server's
  own `detail` text, so a rejected transition or a role refusal is shown
  rather than logged to the console.
- **Queue shortcuts.** `useQueueShortcuts` binds `j`/`k` (or arrows) to move
  between rows, `Enter` to expand, `c` to confirm, `x` for false positive and
  `?` for the legend, and lands on the next still-unreviewed row after a
  decision. It ignores keys while typing or with a modifier held, so mouse
  and Tab behaviour is unchanged for anyone who never uses it.

---

## 8. Configuration

### 8.1 Detection knobs — `shared/detection_knobs.json`

The **only** place a detection number may be defined. The backend re-exports
them from `app/constants.py`; the frontend imports the same file. Change the
JSON first, never a literal in a module or page.

| Group | Knobs (default) |
|---|---|
| Windows | `window_minutes` 15, `secondary_window_minutes` 60, `min_transactions_for_scoring` 3 |
| Layer 1 | `gdi_min` 0.5, `gdi_max` 5.0, `raw_distance_clip` 12, `shrinkage_alpha` 0.1, `variance_floor` 1e-6, `min_accounts_for_full_cov` 10, `amount_entropy_bins` 10 |
| Fusion | `fusion_gdi_weight` 0.5, `fusion_ring_weight` 0.5, `confidence_strong_percentile` 0.9, `score_escalation_relative_threshold` 0.1 |
| Alert share | `default_alert_top_percent` 5, manual range 1–25 |
| Layer 2 | `louvain_resolution` 2.0, `risk_threshold` 2.0, `min_ring_member_count` 4, `ring_hub_weight` 0.45, `ring_external_weight` 0.30, `ring_recent_weight` 0.15, `community_similarity_threshold` 0.7 |
| Peripheral | `peripheral_hub_connection_base` 3.5, `peripheral_pattern_consistency_bonus` 1.0, `peripheral_min_qualifying_score` 3.5 |
| Learned signal | `enable_learned_signal` **false**, `learned_signal_min_labels_per_class` 50, `learned_signal_fusion_weight` 0.5 |
| Co-hub | `enable_cohub_scoring` **false**, `co_hub_max_set_size` 4, `co_hub_similarity_ratio` 0.6, `co_hub_separation_ratio` 2.0 |
| Calibration | window 50, band 0.60–0.85, step 0.5 pp, clamp 2–15%, 2 consecutive ticks, every 10 cycles, `min_reviewed_sample` 20 |
| Runtime | detection 45 s, metrics 10 s, simulation 2 s, `alert_staleness_hours` 2, `replay_step_seconds` 30 |
| Simulator | `pool_size` 150, `mule_attack_probability` 0.015, `slow_drip_attack_probability` 0.005 |

`test_knobs.py` asserts that the detection modules read these values rather
than local literals.

### 8.2 Environment — backend (`backend/.env`)

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./graphdrift.db` | |
| `ENVIRONMENT` | `development` | `production` enables startup guards |
| `SESSION_SECRET` | random per process in dev | **required** in production, ≥ 32 chars, not the example value |
| `SESSION_TTL_SECONDS` | 43200 | 12 h; must be > 0 |
| `ALLOWED_ORIGINS` | *(empty)* | comma-separated CORS origins; `*` refused in production |
| `LOGIN_RATE_LIMIT` | `10/minute` | per client IP; invalid syntax fails at startup |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | read by uvicorn; set to `*` behind a proxy that is the only way in (e.g. Render) |
| `LIVE_FEED_WS_URL` | — | used only by `test_ws_client.py` |

### 8.3 Environment — frontend

| Variable | Where | Purpose |
|---|---|---|
| `VITE_DEV_BACKEND_URL` | `.env` / `.env.development` | **required for local dev** — the Vite proxy for `/api`, `/health`, `/ws` only exists when this is set |
| `VITE_API_BASE_URL` | production | backend origin when not proxied |
| `VITE_WS_BASE_URL` | production | `wss://` origin for the live feed |

### 8.4 Deliberately hardcoded

Kept as literals on purpose: CSS/layout values and chart colours, page sizes
(`PAGE_SIZE` 25, `TX_PAGE_SIZE` 15), WebSocket buffer and backoff, the HS256
algorithm name (configurable algorithms invite alg-confusion), the 12-character
password floor, Argon2 library defaults (they track RFC 9106), the 256-character
password cap (bounds hashing work per request), UPI handle suffixes and the Faker
locale, simulator attack shapes (e.g. the 2–5% mule skim), evaluation-corpus
compression factors (PaySim ÷20, IBM ÷332, IBM 48 h slice), the learned
signal's small-data LightGBM hyperparameters and its 5×5 CV shape (tune with
nested CV once labels number in the thousands), and the frozen
clock in the calibration stress harness — which is what keeps
`bench_calibration` byte-reproducible.

---

## 9. Repository layout

```
graphdrift/
├── .github/workflows/ci.yml        CI: pytest + frontend build
├── PROJECT.md                      this document
├── README.md                       setup and deployment quick-start
├── shared/
│   └── detection_knobs.json        single source of detection constants
├── backend/
│   ├── app/
│   │   ├── main.py                 app, lifespan loops, middleware
│   │   ├── config.py               env-driven runtime config
│   │   ├── constants.py            re-exports detection knobs
│   │   ├── security.py             Argon2 + JWT
│   │   ├── settings_store.py       in-memory top-share / calibration state
│   │   ├── models.py               User, RevokedToken, Account, Transaction,
│   │   │                           Alert, RingReviewAction, AccountScoreHistory
│   │   ├── api/                    auth, alerts, rings, accounts, graph,
│   │   │                           reports, settings, websocket, deps, schemas
│   │   ├── detection/              features, node_anomaly, community, fusion,
│   │   │                           structural_pass, calibration, learned_signal,
│   │   │                           lifecycle, ring_id, cycle_timing
│   │   └── simulation/             generator, adversarial_attacks
│   ├── evaluation/                 benchmarks, eval harnesses, RESULTS.md, data/
│   ├── scripts/                    create_user, reset_demo_data, measure_alert_volume
│   ├── tests/                      117 tests
│   ├── requirements.txt            exact direct pins
│   └── requirements.lock           full transitive lock (CI installs this)
└── frontend/
    └── src/
        ├── api/                    axios client, types
        ├── auth/                   AuthContext, useAuth
        ├── components/             graph, feed, queue, badges, scrubber …
        ├── hooks/useLiveFeed.ts
        ├── pages/                  Login, LiveMonitor, AlertQueue,
        │                           AccountDetail, Reports, Settings
        ├── knobs.ts                imports shared/detection_knobs.json
        └── utils/
```

`backend/snapshots/` (frozen evaluation databases) and `backend/*.db` are
gitignored. The small evaluation corpora in `backend/evaluation/data/` are
tracked.

---

## 10. Running locally

### Backend

```bash
cd graphdrift/backend
python3.14 -m venv venv
source venv/bin/activate
pip install -r requirements.lock      # exact environment behind the cited results
cp .env.example .env                  # leaves SESSION_SECRET empty: random per process
python scripts/create_user.py --dev-seed      # local-admin / local-development-only
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd graphdrift/frontend
npm ci
cp .env.development.example .env.development   # sets VITE_DEV_BACKEND_URL
npm run dev -- --port 5173
```

Open http://localhost:5173 and sign in. Without `VITE_DEV_BACKEND_URL`, Vite
starts with no proxy and every API call returns 404 — it prints a warning when
this happens.

**Expect a fresh login after each backend restart** unless `SESSION_SECRET` is
set: development generates a new secret per process, so old tokens are
rejected.

**Stopping servers.** Stop them by port rather than by name: a `--reload`
worker's command line doesn't mention uvicorn, so `pkill -f uvicorn` can leave
it running (one survived seven days during development).

```bash
lsof -tiTCP:8000 -sTCP:LISTEN | xargs kill; lsof -tiTCP:5173 -sTCP:LISTEN | xargs kill
```

### Resetting demo data

```bash
cd graphdrift/backend
python scripts/reset_demo_data.py   # type "yes"; permanently deletes data
```

Then restart the backend so the simulator re-seeds its account pool.

---

## 11. Testing and CI

```bash
# backend
cd graphdrift/backend && source venv/bin/activate
python -m pytest -q

# frontend
cd graphdrift/frontend
npx tsc --noEmit && npm run lint && npm run build
```

| Test file | Covers |
|---|---|
| `test_auth.py` | login, `/me`, expiry, rate limit (and its override), role gates, **read endpoints require auth**, WebSocket token and **expiry close**, **logout revokes the token for HTTP and WebSocket**, **docs and schema gated in production**, **no labels on the wire**, **production config refusals**, review audit |
| `test_scoring.py` | Mahalanobis, GDI ordering, ring risk (hub vs distributed), fusion, multi-scale union, explanation cache, co-hub off-by-default and gate cases, **exact top-k budget**, **cross-process determinism of tie-breaking** |
| `test_learned_signal.py` | feature parity between live rows and persisted explanations, trivial-baseline comparison, account-grouped folds, cold-start and no-signal refusals, **flag-off fused scores pinned to pre-change values** |
| `test_eval_tooling.py` | tie diagnostics, `run_eval` patches only its own rows, **every RESULTS.md section still exists** |
| `test_rings.py` | stable ring IDs, peripheral inheritance, bulk ring review |
| `test_calibration.py` | stress streams, damping, clamps, status exclusion, manual override, live tick cadence |
| `test_closed_loop_calibration.py` | per-alert window scoping of ground truth; short-horizon closed loop |
| `test_knobs.py` | knobs file contract, modules read knobs not literals |
| `test_adversarial_attacks.py` | attack generator shapes |
| `test_isolation_forest.py` | IF baseline feature parity and degeneracy |
| `test_ibm_aml_loader.py` | IBM schema, windowing, compression |
| `test_simulation_seed.py` | seeded reproducibility |
| `frontend/tests/a11y.spec.ts` | axe-core scan of all six pages plus login and an expanded ring row, against a seeded backend; asserts zero violations |

CI (`.github/workflows/ci.yml`) runs on every push and pull request: Python
3.14 → `pip install -r requirements.lock` → `pytest -q`; Node 24.20.0 →
`npm ci` → `npm run build`; and an accessibility job that seeds a fixture
database (`scripts/seed_ui_fixture.py`), starts the API, and runs the axe scan
in `frontend/tests/a11y.spec.ts` against the real pages.

---

## 12. Evaluation results

Everything below is the **default configuration** (`enable_cohub_scoring:
false`, `enable_learned_signal: false`). Full tables, per-seed breakdowns and methodology notes are in
[`RESULTS.md`](backend/evaluation/RESULTS.md).

### 12.1 Headline — multi-seed synthetic (n = 5 seeds, mean ± std)

| Detector | Universe | F1 | Precision | Recall | FPR |
|---|---|---|---|---|---|
| Layer 1 (GDI) | ≥ 3 tx | 0.253 ± 0.029 | 0.689 | 0.158 | 0.022 |
| Fusion, 15 min | ≥ 3 tx | **0.284 ± 0.051** | 0.620 | 0.188 | 0.035 |
| Fusion, multi-scale union | ≥ 3 tx | **0.336 ± 0.072** | 0.368 | 0.328 | 0.180 |
| Hybrid (union + peripheral) | all active | **0.691 ± 0.065** | 0.710 | 0.683 | 0.191 |

The multi-scale and hybrid detectors trade precision for recall: flagging a
top-share at two scales roughly doubles the alert budget. Layer 1 was 0.251
before the top-k rounding fix (§3.4); nothing else in this table moved.

None of these figures are tie artifacts: at every cutoff the k-th score is
unique.

### 12.2 Baselines — Isolation Forest (sklearn defaults, same 5 seeds)

| | F1 | Compared with |
|---|---|---|
| IF on the 8 Layer-1 features | 0.223 ± 0.031 | GDI 0.253 |
| IF on Layer-1 + structural features | 0.237 ± 0.061 | fusion 0.284 |

GraphDrift is ahead on every configuration of a sensitivity sweep
(IF-L1 0.216–0.223, IF-all 0.237–0.244). The margin is modest — about one
standard deviation — but GDI never loses a seed to IF-L1 (wins 3, ties 2).
These comparison numbers are now read from `multiseed_eval.json` rather than
typed into the scripts.

### 12.3 External datasets (weak, reported honestly)

| Dataset | Fusion F1 | Notes |
|---|---|---|
| IBM AML HI-Small (dense slice, single window) | 0.033 | P 0.019, R 0.117, FP 4,163 of 64,605 accounts |
| PaySim | 0.032 | **no detection signal** — see below |

On IBM, `hub_concentration` barely separates fraud from legitimate accounts
(AUC ≈ 0.53). On the synthetic snapshot it does (AUC 0.809 on scored accounts,
0.876 on ring-sized communities). The synthetic gap should be read accordingly.

**PaySim carries no signal.** At its `min_transactions=1` setting, 1,040 of
2,079 scored accounts share one GDI score at the cutoff, and 103 of the 104
alert slots are filled from that tie. The one account ranked above it is not
fraud. Every true positive is a tie-break artifact, and a random tie-break
would do better (expected F1 ≈ 0.058). Report PaySim as a structural
limitation, not a weak positive result. The corpus was reloaded (the old one
predated the current schema and cannot be regenerated); the loader is now
verified deterministic.

### 12.4 Slow-drip recall (corrected)

On `graphdrift_snapshot_2026-08-12.db`: **60-minute scale alone 7/33
(21.2%)**, **multi-scale union 11/33 (33.3%)**. Seven accounts are caught only
by the 60-minute scale — two of them score exactly 0.000 at 15 minutes. An
earlier "1/1 (100%)" figure was a single-account run and is not representative.

### 12.5 Adversarial evasion (5 instances per variant)

| Variant | Hybrid caught | Notes |
|---|---|---|
| `standard` | 5/5 | |
| `straddle_15` | 5/5 | half the burst is still inside 15 min |
| `minimal_ring` | 5/5 | 4 + 4 ring still clears `min_ring_member_count` |
| `straddle_60` | **0/5** | open — see §13 |
| `diluted_hub` | **0/5** | open by default; **5/5 with co-hub enabled** |

Cost of enabling co-hub scoring, measured:

| Measure | Default | Co-hub on |
|---|---|---|
| `diluted_hub` recall | 0/5 | **5/5** |
| hybrid F1 | **0.691** | 0.670 |
| multi-scale fusion F1 | **0.336** | 0.312 |
| 15-min fusion F1 | **0.284** | 0.244 |
| hub-concentration AUC (ring-sized) | **0.876** | 0.796 |
| IBM false positives | **4,163** | 4,345 |

### 12.6 Performance

Measured with `run_detection_cycle(profile=True)`:

Re-measured 2026-09-18 on an idle machine; the figures this table carried
before were 34-49% slower on an identical workload, and RESULTS.md explains why
they moved.

| Active accounts (15 min) | Mean cycle | p95 |
|---|---|---|
| 372 | 248 ms | 276 ms |
| 684 | 552 ms | 574 ms |
| 1,602 | 1,545 ms | 1,559 ms |
| 3,162 | **4,366 ms** | 4,460 ms |

- Scaling exponent (log-log): **1.32**. Layer 2 (Louvain) is the largest term
  at 5k (2.3 s of 4.4 s, 52%).
- Before the explanation-cache fix the exponent was 2.18 and a 5k cycle took
  107 s — the cause was recomputing the population baseline once per alert,
  not SQLite. **Do not cite 2.18 or 107 s** as the architecture's scaling.
- At 5k, a cycle uses 10% of the 45-second loop. 10k accounts is untimed.
- Ingest (write path only): 833 tx/s committing per transaction, 1,874 tx/s
  batched — far above the demo rate, far below national UPI volumes. SQLite is
  a prototype ceiling.
- Analyst-visible alert delay is interval-dominated: roughly 1 s best case,
  46 s worst case, ~24 s expected.

### 12.7 Calibration

- **Synthetic stress (40 cycles, 5 streams):** all pass, zero oscillation
  reversals, zero clamp overshoot. `bench_calibration` reproduces its committed
  output byte-for-byte.
- **Closed loop against the real detector — negative result:** the loop does
  not settle. It loosens on every evaluation (confirmed rate 0.96–1.00) and
  rides the 15% clamp from cycle 84 of 120. The cause is the synthetic trace,
  not the confirmation logic: attacks are injected at a constant per-step
  probability with no quiet periods, so widening the top-share keeps landing on
  real fraud and never produces the false positives that would pull it back.
  Window-scoped confirmation is separately verified by a regression test.
- **Live:** with real judgments below the band, the running system held on its
  first tick (damping) and lowered the top-share 5.0% → 4.5% on the next.

### 12.8 Live end-to-end run

29,932-transaction seeded trace growing to 38,065 transactions and 3,540 alerts
during a live run with a real browser. One alert was traced through every stage
and matched an independent recomputation exactly. **Live alert precision
67.9%** (in line with the multi-seed 0.710), but **78% of alerts come from the
peripheral cascade**, `fan_in_fan_out` alerts are right only 37.5% of the time,
and the simulator's attack density is far above real base rates. The traced
alert was itself a false positive.

### 12.9 Figures not to cite

| Don't cite | Why | Cite instead |
|---|---|---|
| fusion F1 0.625 | single historical snapshot | 0.284 ± 0.051 |
| hybrid F1 0.860 | single historical snapshot | 0.691 ± 0.065 |
| 0.304 / 0.644 | retired max-merge | 0.336 / 0.691 |
| IF F1 0.249 / 0.244 | best-of-sweep | 0.237 (defaults) |
| Layer 1 F1 0.251 | pre-rounding-fix | 0.253 |
| PaySim F1 0.041 / 0.032 | tie-break artifact | "no signal" |
| slope 2.18, 107 s | pre-fix bug | 1.32, 4.4 s |
| slow-drip 1/1 | n = 1 | 7/33 (60 m), 11/33 (union) |
| slope 1.39, 8.0 s at 5k | measured beside a leaked worker | 1.32, 4.4 s |

### 12.10 Analyst-feedback learned signal (off by default)

**Real labels: nowhere near enough.** The accumulated database holds 4 judged
alerts (all `confirmed`, **no negative class**); the live end-to-end database
holds 51, of which 50 were written by a script from simulator ground truth and
one by a person. After excluding peripheral alerts, which fusion never scored,
the largest usable set is **3 confirmed / 13 false positive** against a
50-per-class minimum. The cold-start guard refuses on every database, which is
the honest result: the mechanism is built and validated, and there is not yet
enough analyst feedback to show it beats a trivial baseline.

**Mechanism on oracle labels** (two fresh seeded traces, ground-truth
judgements, *not* analyst decisions): grouped-CV F1 **0.946 ± 0.005** vs 0.920
for always-"confirmed" (p 7.7e-10), accuracy 0.905 vs 0.852 (p 9.0e-11);
held-out trace F1 0.942 vs 0.910, bootstrap gain CI **[+0.025, +0.039]**, ROC
AUC **0.908** where today's fused score scores 0.681. An earlier
`class_weight="balanced"` configuration **failed** the same gate at identical
AUC; both are recorded in RESULTS.md.

**Default path proven untouched**: 99 MB of fused-score dumps and a 60-cycle
closed-loop run are byte-identical to the pre-change pipeline under two hash
seeds, and every committed evaluation artifact re-ran unchanged.

**Reproducibility.** Detection output is now identical across processes, not
just within one: tied candidates and peripheral hub links are ordered by
account id rather than by `set` iteration, which Python's per-process hash
randomisation made unstable. Dumps and a 60-cycle closed-loop run are
byte-identical across three `PYTHONHASHSEED` values, no measured quantity
changed, and a subprocess test pins it.

---

## 13. Known limitations and open evasion vectors

1. **Burst attacks older than ~15 minutes are invisible (`straddle_60`).**
   This is not a window-alignment problem — windows already slide. A 3-minute
   burst of ~10 transactions is simply unremarkable on a 60-minute scale: the
   attack hubs rank 160–165 of 185 by velocity, below ordinary accounts making
   35–39 payments an hour. Extending the peripheral cascade to 60 minutes was
   tried and reverted: it cannot help, because the hub is never flagged at
   either scale, so there is nothing to cascade from. **Future work:**
   scale-invariant burst features (score velocity over a short sub-window even
   inside the 60-minute scale).
2. **Hub dilution (`diluted_hub`) is open by default.** Closed by the optional
   co-hub mode at the accuracy cost in §12.5.
3. **The 60-minute scale only detects slow activity.** It contributes unique
   slow-drip detections but catches none of the burst-shaped adversarial
   attacks.
4. **The peripheral cascade is not selective.** Within its real candidate set
   it flags every benign hub neighbour; its low global FPR reflects how few
   benign neighbours exist, not discrimination.
5. **Weak external validation.** IBM F1 0.033; **PaySim shows no detection
   signal at all** (every true positive is a tie-break artifact, §12.3). PaySim
   fraud is mostly single-hop pairs, which neither layer can represent.
6. **Prototype storage.** SQLite ingest tops out in the hundreds of
   transactions per second.
7. **Coverage gap.** Accounts with < 3 transactions are only reachable through
   the cascade — 69 of 91 fraud accounts in the historical snapshot were below
   the scoring threshold.
8. **In-memory settings.** The top-share and calibration state reset on
   restart.
9. **Closed-loop calibration is unproven on realistic traffic** (§12.7).
10. **Most live alerts are guilt-by-association.** 78% of alerts in the live
    run came from the peripheral cascade, and fan-in/fan-out-labelled alerts
    were right 37.5% of the time (§12.8).
11. **Fusion inflates weak Layer-1 scores.** Ring members without enough
    transactions enter fusion with GDI 0, so a scored account with an
    unremarkable GDI still ranks high on the Layer-1 percentile.
12. **The learned signal has no real data to learn from.** Every database in
    the project is an order of magnitude below the 50-labels-per-class
    minimum, and the accumulated one has no `false_positive` labels at all, so
    the signal cannot be shown to help on anything but oracle labels (§12.10).
    Its training set is also alerted accounts only, while it scores every
    account — a selection bias more labels of the same kind cannot fix.
13. **Session revocation covers logout only.** Logging out records the token's
    `jti` in `revoked_tokens`, and `user_from_token` - the one validator behind
    both HTTP and the WebSocket handshake - refuses it from then on. There is
    still no way for an admin to end *someone else's* session: nothing records
    which tokens are outstanding, so the lever for that remains rotating
    `SESSION_SECRET`, which ends every session at once. Tokens issued before
    this existed carry no `jti` and simply expire.

---

## 14. Key design decisions

| Decision | Reason |
|---|---|
| Hub concentration, not density, for ring scoring | stars have low density by construction |
| Percentile fusion, 50/50 | the layers have unrelated raw scales |
| Union of per-scale top-k, not max-merge | percentiles from different windows are not comparable |
| Explanation reuses the scoring baseline | removed an O(N²) recomputation (107 s → 8 s at 5k) |
| Calibration is off by default, fixed-step, damped | stability over responsiveness; operators must opt in |
| Calibration tick outside `run_detection_cycle` | keeps evaluation and benchmarks comparable |
| All endpoints authenticated at router level | the UI already gated every page; the API did not |
| WebSocket token via query string, shared validator | browsers cannot set handshake headers; one validator prevents drift |
| Co-hub scoring shipped but off | closes one evasion at a cost to every other measure |
| Learned signal shipped but off, and self-disabling | it must not activate on a handful of labels, or on a model no better than "always confirmed" |
| Learned signal trained unweighted | `class_weight="balanced"` moves the decision threshold and fails the gate at identical ranking quality |
| Learned-signal CV folds grouped by account | an account judged in several cycles would otherwise be memorised across folds |
| 60-minute cascade reverted | closed nothing, since the hub is never flagged |
| One knobs file shared by backend and frontend | numbers cannot silently diverge |
| Tied candidates and hub links ordered by account id | set iteration varies with Python's per-process hash seed, which made alert ids and hub links irreproducible |
| Exact dependency and runtime pins | cited benchmarks must be reproducible |

---

## 15. Known tooling issues and follow-ups

- **Fixed:** `bench_perf.py` printed conclusions it had not measured ("at
  5,000 accounts mean cycle already exceeds the 45s live interval" while its
  own table said 7.1 s; a steady-state pair of "79.6 s / 74.3 s") and rewrote
  the whole performance section on every run, deleting the hand-written pre-fix
  analysis. It now owns only the text between `<!-- perf-bench -->` markers,
  refuses to run if they are missing, and computes every claim - including
  which phase dominates and whether the cycle fits the live interval - from the
  run. The benchmark was re-measured on 2026-09-18; see RESULTS.md for the
  delta and why the older figures were slow.
- **Fixed:** `run_eval` rebuilt the whole RESULTS.md from stale template prose
  (following RESULTS.md's own reproduce steps would have erased most of it);
  it now patches only its own table rows. `run_ibm_aml_eval` deleted the
  Layer-2 section; it now owns a marked block and refuses to run without the
  marker. The Isolation Forest scripts hardcoded GraphDrift's comparison
  figures; they now read `multiseed_eval.json`. `test_eval_tooling.py` fails CI
  if any RESULTS.md section disappears.
- **Fixed:** `community.py` maintained `_PARTITION_SNAPSHOTS`, a list nothing
  ever read; it and its `max_partition_snapshots` knob are gone. An earlier
  note here also claimed a stray `return [], 0.0` in that function — that was
  a misreading of two concatenated `sed` ranges; all three such returns are
  legitimate exits of `_detect_co_hub`.
- **Fixed:** the PaySim corpus was reloaded with the current schema.
- **Fixed:** `/docs`, `/redoc` and `/openapi.json` are served only when
  `ENVIRONMENT` is not `production`; a production process returns 404 for all
  three while `/health` and the API keep working.
- **`eval_multi_seed` only patches RESULTS.md with
  `--force-patch-results`** — intentional, to protect methodology notes.
- **GitHub Actions warns** that `actions/checkout@v4` and
  `actions/setup-node@v4` target the deprecated Node 20 runtime.
- **Dev console shows one WebSocket warning** on load — the browser logs React
  StrictMode's discarded first socket. It does not recur.
- **Suggested next work:** persist settings across restarts; expose the
  calibration band/step/clamp in the UI; scale-invariant burst features;
  a fraud-density-varying trace for closed-loop calibration; record each
  cycle's actual top-k cutoff so the account chart can show a real threshold.

---

## 16. Deployment

**Backend (Render)** — root `backend`; build `pip install -r
requirements.lock`; start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
Then run `python scripts/create_user.py --username <name> --role admin` from a
Render shell.

| Variable | Required | If missing or wrong |
|---|---|---|
| `ENVIRONMENT=production` | **yes** | none of the production guards below run; a random per-process secret silently logs everyone out on every restart |
| `SESSION_SECRET` | **yes** | startup refuses. Must be ≥ 32 chars and not the example value — otherwise anyone can forge an admin token |
| `DATABASE_URL` | **yes** | defaults to SQLite in the container's working directory; on an ephemeral disk every alert, review and user is lost on redeploy |
| `ALLOWED_ORIGINS` | yes, for a separate frontend origin | unset: startup warns and the browser blocks every cross-origin API call. `*` is refused |
| `FORWARDED_ALLOW_IPS=*` | **yes, behind Render's proxy** | every user shares one login rate-limit bucket; a few failed logins lock out the whole team (verified) |
| `SESSION_TTL_SECONDS` | no (12 h) | must be a positive integer or startup refuses |
| `LOGIN_RATE_LIMIT` | no (`10/minute`) | invalid syntax: startup refuses |

Only set `FORWARDED_ALLOW_IPS=*` when the app can be reached solely through the
proxy, as on Render; otherwise clients could spoof their IP.

**Frontend (Vercel)** — root `frontend`; build `npm run build`; output
`dist`. Set `VITE_API_BASE_URL` and `VITE_WS_BASE_URL` (`wss://`); both are baked
in at build time, so a missing value makes the app call its own origin and
every request fails. After both
are live, set the backend's `ALLOWED_ORIGINS` to the real Vercel URL and
restart it.

---

## 17. Working norms

These have guided the project throughout and still apply:

- **Verify, don't assert.** "Works" means it was run — server booted,
  endpoint hit, test executed.
- **Report negative results plainly**, including your own mistakes.
- **Check anything suspiciously clean** before trusting it.
- **Explain findings by mechanism**, and say which components are *not*
  implicated.
- **Commit and push, then confirm the push against the remote.** Fetch first;
  `git status` alone can be stale.
- **Don't widen scope silently** — flag what you find, then ask.
- **Change detection numbers in `detection_knobs.json`**, never in a module
  or page.
- **Keep RESULTS.md current and unambiguous** — replace superseded figures and
  say what changed and why.
