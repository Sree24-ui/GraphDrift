import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi import _rate_limit_exceeded_handler

from app.api import accounts, alerts, auth, graph, reports, rings, settings, websocket
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
from app.config import load_runtime_config
from app.db import SessionLocal, init_db
from app.detection.calibration import tick_live_detection_cycle
from app.detection.fusion import run_detection_cycle
from app.logging_config import configure_logging
from app.models import User
from app.rate_limit import limiter
from app.simulation.generator import run_simulation

configure_logging()
logger = logging.getLogger("graphdrift.runtime")


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
                tick_live_detection_cycle(db)
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
        except Exception as exc:
            logger.exception(
                "detection_cycle_failed",
                extra={"event": "detection_cycle_failed", "error": str(exc)},
            )
            await asyncio.sleep(DETECTION_CYCLE_INTERVAL_SECONDS)
            continue

        set_last_cycle_peak_fused_score(float(peak_fused_score))

        for message in alert_messages:
            await live_feed_manager.broadcast(message)

        await live_feed_manager.broadcast(build_metrics_message(metrics))

        logger.info(
            "detection_cycle_completed",
            extra={
                "event": "detection_cycle_completed",
                "alerts": len(alert_messages),
                "peak_fused_score": float(peak_fused_score),
            },
        )

        await asyncio.sleep(DETECTION_CYCLE_INTERVAL_SECONDS)


async def _run_metrics_loop() -> None:
    while True:
        await asyncio.sleep(METRICS_BROADCAST_INTERVAL_SECONDS)
        try:
            await broadcast_metrics()
        except Exception as exc:
            logger.warning(
                "metrics_broadcast_failed",
                extra={"event": "metrics_broadcast_failed", "error": str(exc)},
            )
            continue


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    config = load_runtime_config()
    if config.environment == "production":
        if not config.allowed_origins or "*" in config.allowed_origins:
            logger.warning(
                "unsafe_cors_configuration",
                extra={"event": "unsafe_cors_configuration"},
            )
        db = SessionLocal()
        try:
            if not db.query(User).filter(User.role == "admin").first():
                logger.warning(
                    "no_admin_user_provisioned",
                    extra={"event": "no_admin_user_provisioned"},
                )
        finally:
            db.close()
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


app = FastAPI(title="GraphDrift", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.include_router(graph.router)
app.include_router(alerts.router)
app.include_router(rings.router)
app.include_router(accounts.router)
app.include_router(reports.router)
app.include_router(settings.router)
app.include_router(websocket.router)
app.include_router(auth.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(load_runtime_config().allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
