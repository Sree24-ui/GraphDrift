import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import accounts, alerts, graph, reports, rings, settings, websocket
from app.api.websocket import (
    DETECTION_CYCLE_INTERVAL_SECONDS,
    METRICS_BROADCAST_INTERVAL_SECONDS,
    broadcast_metrics,
    build_alert_message,
    build_metrics_message,
    collect_live_metrics,
    live_feed_manager,
    set_last_cycle_peak_fused_score,
)
from app.db import SessionLocal, init_db
from app.detection.fusion import run_detection_cycle
from app.simulation.generator import run_simulation


async def _consume_simulation() -> None:
    async for _ in run_simulation():
        pass


async def _run_detection_loop() -> None:
    while True:
        def _cycle():
            db = SessionLocal()
            try:
                alert_actions, diagnostics = run_detection_cycle(
                    db, return_diagnostics=True
                )
                alert_messages = [
                    build_alert_message(action.alert, action.action)
                    for action in alert_actions
                ]
                metrics = collect_live_metrics(db)
                return alert_messages, metrics, diagnostics["peak_fused_score"]
            finally:
                db.close()

        try:
            alert_messages, metrics, peak_fused_score = await asyncio.to_thread(_cycle)
        except Exception:
            await asyncio.sleep(DETECTION_CYCLE_INTERVAL_SECONDS)
            continue

        set_last_cycle_peak_fused_score(float(peak_fused_score))

        for message in alert_messages:
            await live_feed_manager.broadcast(message)

        await live_feed_manager.broadcast(build_metrics_message(metrics))

        await asyncio.sleep(DETECTION_CYCLE_INTERVAL_SECONDS)


async def _run_metrics_loop() -> None:
    while True:
        await asyncio.sleep(METRICS_BROADCAST_INTERVAL_SECONDS)
        try:
            await broadcast_metrics()
        except Exception:
            continue


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    simulation_task = asyncio.create_task(_consume_simulation())
    detection_task = asyncio.create_task(_run_detection_loop())
    metrics_task = asyncio.create_task(_run_metrics_loop())
    yield
    for task in (simulation_task, detection_task, metrics_task):
        task.cancel()
    for task in (simulation_task, detection_task, metrics_task):
        try:
            await task
        except asyncio.CancelledError:
            pass


DEFAULT_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
    "http://127.0.0.1:3000",
]


def _allowed_origins() -> list[str]:
    raw = os.getenv("ALLOWED_ORIGINS", "").strip()
    if not raw:
        return DEFAULT_ALLOWED_ORIGINS
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app = FastAPI(title="GraphDrift", lifespan=lifespan)

app.include_router(graph.router)
app.include_router(alerts.router)
app.include_router(rings.router)
app.include_router(accounts.router)
app.include_router(reports.router)
app.include_router(settings.router)
app.include_router(websocket.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
