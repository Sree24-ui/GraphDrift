"""Layer 2 (ring) diagnostics for evaluation reporting."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.detection.community import (
    MIN_RING_MEMBER_COUNT,
    RISK_THRESHOLD,
    build_graph,
    community_risk_score,
    compute_community_metrics,
    detect_communities,
    get_ring_alerts,
)


def inspect_ring_detection(
    db: Session,
    as_of: datetime,
    *,
    window_minutes: int,
) -> dict:
    graph = build_graph(db, as_of, window_minutes)
    partition = detect_communities(graph)
    metrics_list = compute_community_metrics(graph, partition, previous_partition={})

    total_communities = len({cid for cid in partition.values()})
    members_ge_floor = [
        m for m in metrics_list if m["member_count"] >= MIN_RING_MEMBER_COUNT
    ]

    scored_communities = []
    for metrics in metrics_list:
        if metrics["member_count"] < MIN_RING_MEMBER_COUNT:
            continue
        risk = community_risk_score(metrics)
        if risk > RISK_THRESHOLD:
            scored_communities.append({**metrics, "risk_score": risk})

    ring_alerts = get_ring_alerts(db, as_of, window_minutes)
    accounts_with_ring_score: set[str] = set()
    for alert in ring_alerts:
        for account_id in alert["member_accounts"]:
            accounts_with_ring_score.add(account_id)

    fused_ring_nonzero = len(accounts_with_ring_score)

    return {
        "graph_nodes": graph.number_of_nodes(),
        "graph_edges": graph.number_of_edges(),
        "louvain_communities": total_communities,
        "communities_member_count_ge_4": len(members_ge_floor),
        "communities_above_risk_threshold": len(scored_communities),
        "ring_alerts_emitted": len(ring_alerts),
        "accounts_with_nonzero_ring_risk": fused_ring_nonzero,
    }


def format_ring_inspection(label: str, stats: dict) -> str:
    lines = [
        f"### Layer 2 ring detection — {label}",
        "",
        f"- Transaction graph: **{stats['graph_nodes']}** nodes, **{stats['graph_edges']}** edges",
        f"- Louvain communities detected: **{stats['louvain_communities']}**",
        f"- Communities with member_count ≥ {MIN_RING_MEMBER_COUNT}: "
        f"**{stats['communities_member_count_ge_4']}**",
        f"- Communities clearing risk threshold ({RISK_THRESHOLD}): "
        f"**{stats['communities_above_risk_threshold']}**",
        f"- Ring alerts emitted: **{stats['ring_alerts_emitted']}**",
        f"- Accounts with non-zero ring risk: **{stats['accounts_with_nonzero_ring_risk']}**",
    ]
    return "\n".join(lines)
