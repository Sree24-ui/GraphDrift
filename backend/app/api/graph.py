from datetime import datetime, timedelta

import networkx as nx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import GraphEdgeSnapshot, GraphNodeSnapshot, GraphSnapshotResponse
from app.detection.community import build_graph, build_graph_for_range
from app.detection.features import WINDOW_MINUTES
from app.detection.fusion import compute_fused_scores

router = APIRouter(prefix="/api/graph", tags=["graph"])

MAX_REPLAY_WINDOW = timedelta(hours=24)


def _graph_to_snapshot(
    graph: nx.DiGraph,
    fused_lookup: dict[str, dict],
    window_start: datetime,
    window_end: datetime,
) -> GraphSnapshotResponse:
    nodes = [
        GraphNodeSnapshot(
            account_id=account_id,
            fused_score=fused_lookup.get(account_id, {}).get("fused_score"),
            confidence=fused_lookup.get(account_id, {}).get("confidence"),
        )
        for account_id in sorted(graph.nodes())
    ]

    edges = [
        GraphEdgeSnapshot(
            sender=sender,
            receiver=receiver,
            weight=int(data["weight"]),
            total_amount=float(data["total_amount"]),
        )
        for sender, receiver, data in graph.edges(data=True)
    ]

    return GraphSnapshotResponse(
        window_start=window_start,
        window_end=window_end,
        nodes=nodes,
        edges=edges,
    )


def _fused_lookup(db: Session, as_of: datetime) -> dict[str, dict]:
    return {row["account_id"]: row for row in compute_fused_scores(db, as_of)}


@router.get("/current", response_model=GraphSnapshotResponse)
def get_current_graph(db: Session = Depends(get_db)) -> GraphSnapshotResponse:
    as_of = datetime.now()
    window_start = as_of - timedelta(minutes=WINDOW_MINUTES)
    graph = build_graph(db, as_of)
    lookup = _fused_lookup(db, as_of)
    return _graph_to_snapshot(graph, lookup, window_start, as_of)


@router.get("/replay", response_model=GraphSnapshotResponse)
def replay_graph(
    start: datetime = Query(..., description="ISO datetime for window start"),
    end: datetime = Query(..., description="ISO datetime for window end"),
    db: Session = Depends(get_db),
) -> GraphSnapshotResponse:
    if end <= start:
        raise HTTPException(
            status_code=400,
            detail="end must be after start",
        )

    if end - start > MAX_REPLAY_WINDOW:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Replay window cannot exceed {int(MAX_REPLAY_WINDOW.total_seconds() // 3600)} "
                "hours. Narrow the start/end range."
            ),
        )

    graph = build_graph_for_range(db, start, end)
    lookup = _fused_lookup(db, end)
    return _graph_to_snapshot(graph, lookup, start, end)
