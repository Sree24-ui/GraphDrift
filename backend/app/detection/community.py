"""
Layer 2 community / ring detection via Louvain partitioning on the UPI transaction graph.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

import networkx as nx
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.detection.features import WINDOW_MINUTES
from app.models import Transaction

try:
    import community.community_louvain as community_louvain
except ImportError:  # pragma: no cover - fallback for alternate package layout
    import community as community_louvain

RISK_THRESHOLD = 2.0
LOUVAIN_RESOLUTION = 2.0
MIN_RING_MEMBER_COUNT = 4
MAX_PARTITION_SNAPSHOTS = 5
COMMUNITY_SIMILARITY_THRESHOLD = 0.7

# In-memory store of recent Louvain partitions keyed by as_of timestamp.
_PARTITION_SNAPSHOTS: list[dict] = []


def _window_bounds(
    as_of: datetime, window_minutes: int
) -> tuple[datetime, datetime]:
    return as_of - timedelta(minutes=window_minutes), as_of


def build_graph(
    db: Session,
    as_of: datetime,
    window_minutes: int = WINDOW_MINUTES,
) -> nx.DiGraph:
    window_start, window_end = _window_bounds(as_of, window_minutes)

    rows = db.execute(
        select(
            Transaction.sender_id,
            Transaction.receiver_id,
            Transaction.amount,
        ).where(
            and_(
                Transaction.timestamp >= window_start,
                Transaction.timestamp <= window_end,
            )
        )
    ).all()

    graph = nx.DiGraph()
    for sender_id, receiver_id, amount in rows:
        if graph.has_edge(sender_id, receiver_id):
            graph[sender_id][receiver_id]["weight"] += 1
            graph[sender_id][receiver_id]["total_amount"] += amount
        else:
            graph.add_edge(
                sender_id,
                receiver_id,
                weight=1,
                total_amount=amount,
            )

    return graph


def build_graph_for_range(
    db: Session,
    window_start: datetime,
    window_end: datetime,
) -> nx.DiGraph:
    rows = db.execute(
        select(
            Transaction.sender_id,
            Transaction.receiver_id,
            Transaction.amount,
        ).where(
            and_(
                Transaction.timestamp >= window_start,
                Transaction.timestamp <= window_end,
            )
        )
    ).all()

    graph = nx.DiGraph()
    for sender_id, receiver_id, amount in rows:
        if graph.has_edge(sender_id, receiver_id):
            graph[sender_id][receiver_id]["weight"] += 1
            graph[sender_id][receiver_id]["total_amount"] += amount
        else:
            graph.add_edge(
                sender_id,
                receiver_id,
                weight=1,
                total_amount=amount,
            )

    return graph


def _to_undirected(graph: nx.DiGraph) -> nx.Graph:
    """Collapse direction, summing weights on reciprocal edge pairs."""
    undirected = nx.Graph()
    for source, target, data in graph.edges(data=True):
        weight = data.get("weight", 1)
        total_amount = data.get("total_amount", 0.0)
        if undirected.has_edge(source, target):
            undirected[source][target]["weight"] += weight
            undirected[source][target]["total_amount"] += total_amount
        else:
            undirected.add_edge(
                source,
                target,
                weight=weight,
                total_amount=total_amount,
            )
    return undirected


def detect_communities(graph: nx.DiGraph) -> dict[str, int]:
    if graph.number_of_nodes() == 0:
        return {}

    undirected = _to_undirected(graph)
    partition = community_louvain.best_partition(
        undirected,
        weight="weight",
        resolution=LOUVAIN_RESOLUTION,
        random_state=42,
    )
    return partition


def _group_partition(partition: dict[str, int]) -> dict[int, set[str]]:
    communities: dict[int, set[str]] = defaultdict(set)
    for account_id, community_id in partition.items():
        communities[community_id].add(account_id)
    return dict(communities)


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _community_existed_before(
    members: set[str], previous_communities: dict[int, set[str]]
) -> bool:
    for previous_members in previous_communities.values():
        if _jaccard(members, previous_members) >= COMMUNITY_SIMILARITY_THRESHOLD:
            return True
    return False


def _store_partition_snapshot(as_of: datetime, partition: dict[str, int]) -> None:
    global _PARTITION_SNAPSHOTS
    _PARTITION_SNAPSHOTS.append({"as_of": as_of, "partition": partition.copy()})
    _PARTITION_SNAPSHOTS.sort(key=lambda item: item["as_of"])
    if len(_PARTITION_SNAPSHOTS) > MAX_PARTITION_SNAPSHOTS:
        _PARTITION_SNAPSHOTS = _PARTITION_SNAPSHOTS[-MAX_PARTITION_SNAPSHOTS:]


def compute_community_metrics(
    graph: nx.DiGraph,
    partition: dict[str, int],
    previous_partition: dict[str, int] | None = None,
) -> list[dict]:
    communities = _group_partition(partition)
    previous_communities = (
        _group_partition(previous_partition) if previous_partition else {}
    )

    internal_edges: dict[int, int] = defaultdict(int)
    external_edges: dict[int, int] = defaultdict(int)
    internal_weight_sum: dict[int, float] = defaultdict(float)
    internal_weight_n: dict[int, int] = defaultdict(int)
    node_incident_edges: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    # One pass over edges. The previous implementation scanned every edge once
    # per community (O(|C| · |E|)), which is unusable on a 200k-node IBM slice.
    for source, target, data in graph.edges(data=True):
        cs = partition.get(source)
        ct = partition.get(target)
        if cs is None or ct is None:
            continue
        weight = float(data.get("weight", 1))
        if cs == ct:
            internal_edges[cs] += 1
            internal_weight_sum[cs] += weight
            internal_weight_n[cs] += 1
            node_incident_edges[cs][source] += 1
            node_incident_edges[cs][target] += 1
        else:
            external_edges[cs] += 1
            external_edges[ct] += 1

    metrics_list: list[dict] = []
    for community_id, members in communities.items():
        member_accounts = sorted(members)
        member_count = len(member_accounts)
        n_internal = internal_edges[community_id]
        n_external = external_edges[community_id]
        incidents = node_incident_edges[community_id]

        if n_internal > 0 and incidents:
            hub_account_id = max(incidents, key=incidents.get)
            max_node_degree = incidents[hub_account_id]
            hub_concentration = max_node_degree / n_internal
        else:
            hub_account_id = None
            hub_concentration = 0.0

        possible_internal = member_count * (member_count - 1)
        internal_density = (
            n_internal / possible_internal if possible_internal > 0 else 0.0
        )
        avg_internal_weight = (
            internal_weight_sum[community_id] / internal_weight_n[community_id]
            if internal_weight_n[community_id]
            else 0.0
        )
        external_edge_ratio = (
            n_external / n_internal if n_internal > 0 else float(n_external)
        )
        formed_recently = not _community_existed_before(members, previous_communities)

        metrics_list.append(
            {
                "community_id": community_id,
                "member_count": member_count,
                "member_accounts": member_accounts,
                "internal_density": internal_density,  # diagnostic only, not used in scoring
                "hub_concentration": hub_concentration,
                "hub_account_id": hub_account_id,
                "avg_internal_weight": avg_internal_weight,
                "external_edge_ratio": external_edge_ratio,
                "formed_recently": formed_recently,
            }
        )

    return metrics_list


def community_risk_score(metrics: dict) -> float:
    """
    Combine structural signals into a 0–5 community risk score.

    Weighting rationale (defensible for paper):
    - hub_concentration (45%): mule rings are star/hub topologies — one central
      mule account handles most fan-in and fan-out edges. Distributed friend
      groups spread edges across many members and score low here.
    - external_edge_ratio (30%): passthrough flow — funds enter from outside,
      touch the hub, then exit to external receivers.
    - formed_recently (15%): ephemeral rings appear only during attack bursts.
      Only applied when member_count >= MIN_RING_MEMBER_COUNT.

    internal_density is retained in metrics for diagnostics only; it is NOT
    used here because star-shaped mule rings have low density by construction.
    """
    member_count = metrics["member_count"]
    hub_component = metrics["hub_concentration"] * 5.0 * 0.45

    capped_external = min(float(metrics["external_edge_ratio"]), 10.0) / 10.0
    external_component = capped_external * 5.0 * 0.30

    recent_component = (
        5.0 * 0.15 if metrics["formed_recently"] and member_count >= MIN_RING_MEMBER_COUNT else 0.0
    )

    return min(hub_component + external_component + recent_component, 5.0)


def _build_reason(metrics: dict) -> str:
    hub_account = metrics.get("hub_account_id") or "unknown"
    hub_pct = metrics["hub_concentration"] * 100.0

    reason = (
        f"hub-and-spoke pattern: account {hub_account} handles "
        f"{hub_pct:.0f}% of this community's transaction edges"
    )

    if metrics["external_edge_ratio"] >= 2.0:
        reason += ", with high external flow-through"
    elif metrics["external_edge_ratio"] >= 1.0:
        reason += ", with elevated external flow-through"

    if metrics["formed_recently"]:
        reason += f" (newly-formed community of {metrics['member_count']} accounts)"

    return reason


def get_ring_alerts(
    db: Session,
    as_of: datetime,
    window_minutes: int = WINDOW_MINUTES,
    risk_threshold: float = RISK_THRESHOLD,
) -> list[dict]:
    graph = build_graph(db, as_of, window_minutes)
    partition = detect_communities(graph)

    previous_as_of = as_of - timedelta(minutes=window_minutes)
    previous_graph = build_graph(db, previous_as_of, window_minutes)
    previous_partition = detect_communities(previous_graph) if previous_graph.number_of_nodes() else {}

    metrics_list = compute_community_metrics(graph, partition, previous_partition)
    _store_partition_snapshot(as_of, partition)

    alerts: list[dict] = []
    for metrics in metrics_list:
        if metrics["member_count"] < MIN_RING_MEMBER_COUNT:
            continue

        risk_score = community_risk_score(metrics)
        if risk_score <= risk_threshold:
            continue

        alerts.append(
            {
                "community_id": metrics["community_id"],
                "risk_score": risk_score,
                "member_accounts": metrics["member_accounts"],
                "member_count": metrics["member_count"],
                "internal_density": metrics["internal_density"],
                "hub_concentration": metrics["hub_concentration"],
                "hub_account_id": metrics["hub_account_id"],
                "avg_internal_weight": metrics["avg_internal_weight"],
                "external_edge_ratio": metrics["external_edge_ratio"],
                "formed_recently": metrics["formed_recently"],
                "reason": _build_reason(metrics),
            }
        )

    alerts.sort(key=lambda item: item["risk_score"], reverse=True)
    return alerts


def _synthetic_attack_accounts(
    db: Session,
    as_of: datetime,
    window_minutes: int = WINDOW_MINUTES,
) -> set[str]:
    window_start, window_end = _window_bounds(as_of, window_minutes)
    rows = db.execute(
        select(Transaction.sender_id, Transaction.receiver_id).where(
            and_(
                Transaction.timestamp >= window_start,
                Transaction.timestamp <= window_end,
                Transaction.is_synthetic_attack.is_(True),
            )
        )
    ).all()

    accounts: set[str] = set()
    for sender_id, receiver_id in rows:
        accounts.add(sender_id)
        accounts.add(receiver_id)
    return accounts


def _size_bucket(member_count: int) -> str:
    if member_count <= 2:
        return "1-2"
    if member_count <= 5:
        return "3-5"
    if member_count <= 10:
        return "6-10"
    if member_count <= 20:
        return "11-20"
    if member_count <= 50:
        return "21-50"
    return "50+"


def _print_size_histogram(metrics_list: list[dict]) -> None:
    buckets = ["1-2", "3-5", "6-10", "11-20", "21-50", "50+"]
    counts = {bucket: 0 for bucket in buckets}
    for metrics in metrics_list:
        counts[_size_bucket(metrics["member_count"])] += 1

    print("Community size histogram:")
    for bucket in buckets:
        print(f"  {bucket:>6} members: {counts[bucket]:4d} communities")
    print()


def _print_overlap_diagnostics(
    metrics_list: list[dict],
    attack_accounts: set[str],
    overlap_threshold: float = 0.5,
) -> None:
    flagged = []
    for metrics in metrics_list:
        members = set(metrics["member_accounts"])
        overlap = members & attack_accounts
        overlap_ratio = len(overlap) / len(members) if members else 0.0
        if overlap_ratio >= overlap_threshold:
            flagged.append((metrics, overlap, overlap_ratio))

    if not flagged:
        print("No communities with >=50% synthetic-attack overlap.")
        print()
        return

    print("Synthetic overlap diagnostics (diagnostic only — not used in scoring):")
    for metrics, overlap, overlap_ratio in sorted(
        flagged, key=lambda item: item[2], reverse=True
    ):
        members = set(metrics["member_accounts"])
        normal_count = len(members - attack_accounts)
        synthetic_count = len(overlap)
        print(
            f"  Community {metrics['community_id']:4d} ({metrics['member_count']} members): "
            f"synthetic={synthetic_count} ({overlap_ratio:.0%}), "
            f"normal={normal_count} ({1 - overlap_ratio:.0%}), "
            f"hub_conc={metrics['hub_concentration']:.3f}, "
            f"risk={community_risk_score(metrics):.2f}"
        )
    print()


def _print_community_table(
    metrics_list: list[dict],
    attack_accounts: set[str],
) -> None:
    if not metrics_list:
        print("No communities detected.")
        return

    sorted_metrics = sorted(
        metrics_list,
        key=lambda item: community_risk_score(item),
        reverse=True,
    )

    print(
        f"{'id':>4} | {'members':>7} | {'hub_conc':>8} | {'density':>7} | "
        f"{'ext_ratio':>9} | {'recent':>6} | {'risk':>5} | members / overlap"
    )
    print("-" * 110)

    for metrics in sorted_metrics:
        members = set(metrics["member_accounts"])
        overlap = members & attack_accounts
        overlap_ratio = len(overlap) / len(members) if members else 0.0
        risk = community_risk_score(metrics)

        member_preview = ", ".join(metrics["member_accounts"][:3])
        if len(metrics["member_accounts"]) > 3:
            member_preview += ", ..."

        overlap_note = ""
        if overlap_ratio >= 0.5:
            overlap_note = f" ⚠ overlaps synthetic attack ({len(overlap)}/{len(members)} members)"
        elif overlap:
            overlap_note = f" (partial synthetic overlap: {len(overlap)} accounts)"

        print(
            f"{metrics['community_id']:4d} | {metrics['member_count']:7d} | "
            f"{metrics['hub_concentration']:8.3f} | {metrics['internal_density']:7.3f} | "
            f"{metrics['external_edge_ratio']:9.3f} | "
            f"{str(metrics['formed_recently']):>6} | {risk:5.2f} | "
            f"{member_preview}{overlap_note}"
        )


def _print_hub_spotlight(metrics_list: list[dict], community_ids: list[int]) -> None:
    by_id = {metrics["community_id"]: metrics for metrics in metrics_list}
    print("Hub concentration spotlight:")
    for community_id in community_ids:
        metrics = by_id.get(community_id)
        if metrics is None:
            print(f"  Community {community_id}: not found in current partition")
            continue
        print(
            f"  Community {community_id}: hub_conc={metrics['hub_concentration']:.3f}, "
            f"hub={metrics['hub_account_id']}, "
            f"density(diag)={metrics['internal_density']:.3f}, "
            f"risk={community_risk_score(metrics):.2f}"
        )
    print()


if __name__ == "__main__":
    import argparse
    import time

    from app.db import SessionLocal

    parser = argparse.ArgumentParser(description="Run GraphDrift community detection demo")
    parser.add_argument(
        "--wait",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Pause before running so the simulator can populate the window",
    )
    args = parser.parse_args()

    if args.wait > 0:
        print(
            f"Waiting {args.wait}s — ensure the FastAPI server is running "
            f"(uvicorn app.main:app --reload)."
        )
        time.sleep(args.wait)

    db = SessionLocal()
    try:
        as_of = datetime.now()
        graph = build_graph(db, as_of)
        partition = detect_communities(graph)

        previous_as_of = as_of - timedelta(minutes=WINDOW_MINUTES)
        previous_graph = build_graph(db, previous_as_of)
        previous_partition = (
            detect_communities(previous_graph)
            if previous_graph.number_of_nodes()
            else {}
        )

        metrics_list = compute_community_metrics(graph, partition, previous_partition)
        attack_accounts = _synthetic_attack_accounts(db, as_of)
        alerts = get_ring_alerts(db, as_of)

        print(f"Community detection as_of={as_of.isoformat()} window={WINDOW_MINUTES}m")
        print(
            f"Louvain resolution={LOUVAIN_RESOLUTION} | "
            f"min ring members={MIN_RING_MEMBER_COUNT} | risk threshold={RISK_THRESHOLD}"
        )
        print(
            f"Graph: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges | "
            f"Communities: {len(metrics_list)} | Synthetic attack accounts: {len(attack_accounts)}"
        )
        print()
        _print_size_histogram(metrics_list)
        _print_overlap_diagnostics(metrics_list, attack_accounts)
        _print_hub_spotlight(metrics_list, [4, 35, 61, 24])
        print("All communities (sorted by risk score):")
        _print_community_table(metrics_list, attack_accounts)
        print()
        print(
            f"Ring alerts (risk > {RISK_THRESHOLD}, members >= {MIN_RING_MEMBER_COUNT}):"
        )
        if not alerts:
            print("  None above threshold.")
        else:
            for alert in alerts:
                overlap = set(alert["member_accounts"]) & attack_accounts
                overlap_ratio = (
                    len(overlap) / len(alert["member_accounts"])
                    if alert["member_accounts"]
                    else 0.0
                )
                overlap_note = (
                    f" | synthetic overlap {len(overlap)}/{alert['member_count']} "
                    f"({overlap_ratio:.0%})"
                )
                print(
                    f"  Community {alert['community_id']}: risk={alert['risk_score']:.2f} | "
                    f"{alert['reason']}{overlap_note}"
                )
    finally:
        db.close()
