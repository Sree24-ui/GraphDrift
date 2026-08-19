import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.constants import (
    DETECTION_CYCLE_INTERVAL_SECONDS,
    METRICS_BROADCAST_INTERVAL_SECONDS,
)
from app.db import SessionLocal
from app.detection.community import build_graph
from app.detection.fusion import compute_fused_scores
from app.models import Alert, Transaction

router = APIRouter(tags=["websocket"])

_last_cycle_peak_fused_score: float = 0.0


def _iso_timestamp(value: datetime | None = None) -> str:
    return (value or datetime.now()).isoformat()


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    async def broadcast(self, message: dict) -> None:
        if not self._connections:
            return

        payload = json.dumps(message, default=str)
        dead: list[WebSocket] = []

        for websocket in list(self._connections):
            try:
                await websocket.send_text(payload)
            except Exception:
                dead.append(websocket)

        for websocket in dead:
            self._connections.discard(websocket)


live_feed_manager = ConnectionManager()


def set_last_cycle_peak_fused_score(score: float) -> None:
    global _last_cycle_peak_fused_score
    _last_cycle_peak_fused_score = score


def collect_live_metrics(db: Session) -> dict:
    as_of = datetime.now()
    graph = build_graph(db, as_of)
    fused_results = compute_fused_scores(db, as_of)
    current_peak = max(
        (float(row["fused_score"]) for row in fused_results),
        default=0.0,
    )
    peak_fused_score = max(current_peak, _last_cycle_peak_fused_score)

    active_alert_count = (
        db.scalar(
            select(func.count()).select_from(Alert).where(
                Alert.status.in_(["new", "reviewing"])
            )
        )
        or 0
    )

    return {
        "active_node_count": graph.number_of_nodes(),
        "live_edge_count": graph.number_of_edges(),
        "peak_fused_score": peak_fused_score,
        "active_alert_count": active_alert_count,
    }


def build_transaction_message(tx: Transaction) -> dict:
    return {
        "type": "transaction",
        "timestamp": _iso_timestamp(tx.timestamp),
        "data": {
            "id": tx.id,
            "sender": tx.sender_id,
            "receiver": tx.receiver_id,
            "amount": tx.amount,
            "timestamp": tx.timestamp.isoformat(),
            "is_synthetic_attack": tx.is_synthetic_attack,
        },
    }


def build_alert_message(alert: Alert, action: str) -> dict:
    return {
        "type": "alert",
        "timestamp": _iso_timestamp(),
        "data": {
            "action": action,
            "id": alert.id,
            "account_id": alert.account_id,
            "risk_score": alert.risk_score,
            "confidence": alert.confidence,
            "pattern_type": alert.pattern_type,
            "status": alert.status,
            "detected_at": alert.detected_at.isoformat(),
            "ring_id": alert.ring_id,
        },
    }


def build_metrics_message(metrics: dict) -> dict:
    return {
        "type": "metrics_update",
        "timestamp": _iso_timestamp(),
        "data": metrics,
    }


async def broadcast_metrics() -> None:
    def _collect() -> dict:
        db = SessionLocal()
        try:
            return collect_live_metrics(db)
        finally:
            db.close()

    metrics = await asyncio.to_thread(_collect)
    await live_feed_manager.broadcast(build_metrics_message(metrics))


@router.websocket("/ws/live-feed")
async def live_feed(websocket: WebSocket) -> None:
    await live_feed_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        live_feed_manager.disconnect(websocket)
