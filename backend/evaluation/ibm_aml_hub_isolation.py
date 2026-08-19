#!/usr/bin/env python3
"""Isolate Layer-2 hub_concentration from the formed_recently window artifact."""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from sqlalchemy import func, select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.constants import GDI_MAX, RING_EXTERNAL_WEIGHT, RING_HUB_WEIGHT  # noqa: E402
from app.detection.community import (  # noqa: E402
    MIN_RING_MEMBER_COUNT,
    build_graph,
    compute_community_metrics,
    detect_communities,
)
from app.detection.features import WINDOW_MINUTES, extract_all_features  # noqa: E402
from app.models import Transaction  # noqa: E402
from evaluation.db import init_eval_db  # noqa: E402
from evaluation.run_ibm_aml_eval import (  # noqa: E402
    IBM_DB,
    SNAPSHOT_DB,
    _auc,
    _summarize,
    labeled_fraud_accounts,
    synthetic_accounts,
)


def _structural_score(metrics: dict) -> float:
    hub = float(metrics["hub_concentration"]) * GDI_MAX * RING_HUB_WEIGHT
    capped = min(float(metrics["external_edge_ratio"]), 10.0) / 10.0
    external = capped * GDI_MAX * RING_EXTERNAL_WEIGHT
    return min(hub + external, GDI_MAX)


def _report_feature(name: str, pos: np.ndarray, neg: np.ndarray) -> dict:
    auc = _auc(pos, neg)
    auc_inv = _auc(neg, pos)
    best = max(auc, auc_inv) if np.isfinite(auc) else float("nan")
    direction = "higher=fraud" if auc >= auc_inv else "lower=fraud"
    ps = _summarize(pos)
    ns = _summarize(neg)
    print(
        f"  {name:28s} AUC_best={best:.3f} ({direction})  "
        f"fraud med={ps['median']:.3f} mean={ps['mean']:.3f}  "
        f"legit med={ns['median']:.3f} mean={ns['mean']:.3f}  "
        f"frac0 fraud={ps['frac_zero']:.1%} legit={ns['frac_zero']:.1%}"
    )
    return {
        "name": name,
        "auc_best": best,
        "direction": direction,
        "fraud": ps,
        "legit": ns,
    }


def assign_layer2_features(db, as_of: datetime, window_minutes: int) -> dict[str, dict]:
    print(f"  building graph + Louvain as_of={as_of.isoformat()}…", flush=True)
    graph = build_graph(db, as_of, window_minutes)
    print(
        f"  graph nodes={graph.number_of_nodes()} edges={graph.number_of_edges()}",
        flush=True,
    )
    partition = detect_communities(graph)
    metrics_list = compute_community_metrics(graph, partition, previous_partition={})
    n_ge4 = sum(1 for m in metrics_list if m["member_count"] >= MIN_RING_MEMBER_COUNT)
    print(f"  communities={len(metrics_list)}  size>={MIN_RING_MEMBER_COUNT}: {n_ge4}", flush=True)

    by_account: dict[str, dict] = {}
    for metrics in metrics_list:
        if metrics["member_count"] < MIN_RING_MEMBER_COUNT:
            continue
        hub_c = float(metrics["hub_concentration"])
        ext = float(metrics["external_edge_ratio"])
        score = _structural_score(metrics)
        hub_id = metrics.get("hub_account_id")
        for account_id in metrics["member_accounts"]:
            prev = by_account.get(account_id)
            row = {
                "hub_concentration": hub_c,
                "external_edge_ratio": ext,
                "structural_score": score,
                "is_hub": 1.0 if account_id == hub_id else 0.0,
                "member_count": int(metrics["member_count"]),
            }
            if prev is None or score > prev["structural_score"]:
                by_account[account_id] = row
    return by_account


def evaluate_universe(
    label: str,
    accounts: set[str],
    positives: set[str],
    layer2: dict[str, dict],
) -> dict[str, dict]:
    print(f"\n  --- {label} n={len(accounts)} fraud={len(accounts & positives)} ---")
    keys = ("hub_concentration", "external_edge_ratio", "structural_score", "is_hub")
    out = {}
    for key in keys:
        pos = np.array(
            [layer2.get(a, {}).get(key, 0.0) for a in accounts if a in positives],
            dtype=float,
        )
        neg = np.array(
            [layer2.get(a, {}).get(key, 0.0) for a in accounts if a not in positives],
            dtype=float,
        )
        out[key] = _report_feature(key, pos, neg)
    return out


def run_dataset(path: Path, gt_fn, *, window_minutes: int, min_tx: int, name: str) -> None:
    print(f"\n========== {name} ==========", flush=True)
    db = init_eval_db(path)()
    try:
        as_of = db.scalar(select(func.max(Transaction.timestamp)))
        positives = gt_fn(db, as_of, window_minutes)
        layer2 = assign_layer2_features(db, as_of, window_minutes)
        active_rows = db.execute(
            select(Transaction.sender_id, Transaction.receiver_id).where(
                Transaction.timestamp >= as_of - timedelta(minutes=window_minutes),
                Transaction.timestamp <= as_of,
            )
        ).all()
        active: set[str] = set()
        for s, r in active_rows:
            active.add(s)
            active.add(r)
        scored = {
            row["account_id"]
            for row in extract_all_features(db, as_of, window_minutes, min_transactions=min_tx)
        }
        in_ring = set(layer2)
        evaluate_universe("scored accounts (≥3 tx), missing ring → 0", scored, positives, layer2)
        evaluate_universe("all active accounts, missing ring → 0", active, positives, layer2)
        evaluate_universe(
            "only members of communities size≥4",
            in_ring,
            positives,
            layer2,
        )
        evaluate_universe(
            "scored ∩ size≥4 communities",
            scored & in_ring,
            positives,
            layer2,
        )
    finally:
        db.close()


def main() -> None:
    print("Step 1 — hub_concentration without formed_recently", flush=True)
    run_dataset(
        IBM_DB,
        labeled_fraud_accounts,
        window_minutes=WINDOW_MINUTES,
        min_tx=3,
        name="ibm_aml_eval.db",
    )
    if SNAPSHOT_DB.exists():
        run_dataset(
            SNAPSHOT_DB,
            synthetic_accounts,
            window_minutes=WINDOW_MINUTES,
            min_tx=3,
            name="snapshot 2026-08-12 (reference)",
        )


if __name__ == "__main__":
    main()
