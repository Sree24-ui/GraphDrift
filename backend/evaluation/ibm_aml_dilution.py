#!/usr/bin/env python3
"""Fraud-edge dilution on FAN-IN/FAN-OUT hubs vs Louvain community size."""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
from sqlalchemy import func, select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.detection.community import (  # noqa: E402
    MIN_RING_MEMBER_COUNT,
    build_graph,
    compute_community_metrics,
    detect_communities,
)
from app.detection.features import WINDOW_MINUTES  # noqa: E402
from app.models import Transaction  # noqa: E402
from evaluation.db import init_eval_db  # noqa: E402
from evaluation.ibm_aml_patterns import BEGIN_RE, END_RE, parse_pattern_timestamp  # noqa: E402
from evaluation.ibm_aml_patterns import DEFAULT_PATTERNS  # noqa: E402

IBM_DB = BACKEND_ROOT / "evaluation" / "data" / "ibm_aml_eval.db"
SNAPSHOT_DB = BACKEND_ROOT / "snapshots" / "graphdrift_snapshot_2026-08-12.db"

# Densest-48h slice used by load_ibm_aml (source timestamps, pre-compression).
WINDOW_START = datetime(2022, 9, 8, 2, 11)
WINDOW_END = datetime(2022, 9, 10, 2, 11)


def _acct(bank: str, account: str) -> str:
    return f"{int(bank)}:{account.strip()}@ibm"


def _pct(arr: np.ndarray, q: float) -> float:
    return float(np.percentile(arr, q)) if len(arr) else float("nan")


def parse_fan_instances(path: Path) -> list[dict]:
    instances: list[dict] = []
    typology: str | None = None
    txs: list[dict] = []
    for line in path.read_text(errors="replace").splitlines():
        begin = BEGIN_RE.match(line)
        if begin:
            typology = begin.group(1).strip()
            txs = []
            continue
        if END_RE.match(line):
            if typology in {"FAN-OUT", "FAN-IN"} and txs:
                from_counts = Counter(t["sender"] for t in txs)
                to_counts = Counter(t["receiver"] for t in txs)
                hub = (
                    from_counts.most_common(1)[0][0]
                    if typology == "FAN-OUT"
                    else to_counts.most_common(1)[0][0]
                )
                accounts = {t["sender"] for t in txs} | {t["receiver"] for t in txs}
                instances.append(
                    {
                        "typology": typology,
                        "hub": hub,
                        "accounts": accounts,
                        "txs": txs,
                    }
                )
            typology = None
            txs = []
            continue
        if typology in {"FAN-OUT", "FAN-IN"} and line.strip() and line[0].isdigit():
            parts = line.split(",")
            if len(parts) < 6:
                continue
            try:
                ts = parse_pattern_timestamp(parts[0])
            except ValueError:
                continue
            txs.append(
                {
                    "ts": ts,
                    "sender": _acct(parts[1], parts[2]),
                    "receiver": _acct(parts[3], parts[4]),
                    "amount": round(float(parts[5]), 2),
                }
            )
    return instances


def summarize(name: str, values: list[float]) -> None:
    arr = np.asarray(values, dtype=float)
    print(
        f"  {name}: n={len(arr)}  min={arr.min():.3f}  p25={_pct(arr, 25):.3f}  "
        f"median={_pct(arr, 50):.3f}  p75={_pct(arr, 75):.3f}  "
        f"p90={_pct(arr, 90):.3f}  max={arr.max():.3f}  mean={arr.mean():.3f}  "
        f"frac_eq_1={float(np.mean(arr == 1.0)):.1%}  frac_lt_0.5={float(np.mean(arr < 0.5)):.1%}"
    )


def community_sizes(db_path: Path, label: str) -> None:
    print(f"\n========== Louvain community size: {label} ==========", flush=True)
    db = init_eval_db(db_path)()
    try:
        as_of = db.scalar(select(func.max(Transaction.timestamp)))
        graph = build_graph(db, as_of, WINDOW_MINUTES)
        partition = detect_communities(graph)
        metrics = compute_community_metrics(graph, partition, previous_partition={})
        all_sizes = [m["member_count"] for m in metrics]
        ge4 = [s for s in all_sizes if s >= MIN_RING_MEMBER_COUNT]
        print(f"  nodes={graph.number_of_nodes()}  communities={len(all_sizes)}")
        summarize("all communities", all_sizes)
        summarize(f"size≥{MIN_RING_MEMBER_COUNT}", ge4)
    finally:
        db.close()


def main() -> None:
    instances = parse_fan_instances(DEFAULT_PATTERNS)
    print(
        f"FAN-IN/FAN-OUT instances in Patterns.txt: {len(instances)} "
        f"(FAN-OUT={sum(1 for i in instances if i['typology']=='FAN-OUT')}, "
        f"FAN-IN={sum(1 for i in instances if i['typology']=='FAN-IN')})"
    )

    in_window = []
    for inst in instances:
        txs = [t for t in inst["txs"] if WINDOW_START <= t["ts"] <= WINDOW_END]
        if not txs:
            continue
        accounts = {t["sender"] for t in txs} | {t["receiver"] for t in txs}
        in_window.append({**inst, "txs": txs, "accounts": accounts})
    print(
        f"Instances with ≥1 tx in densest 48h source window: {len(in_window)} "
        f"(FAN-OUT={sum(1 for i in in_window if i['typology']=='FAN-OUT')}, "
        f"FAN-IN={sum(1 for i in in_window if i['typology']=='FAN-IN')})"
    )

    pattern_edges_by_acct: dict[str, Counter] = defaultdict(Counter)
    hubs: set[str] = set()
    spokes: set[str] = set()
    for inst in in_window:
        hubs.add(inst["hub"])
        for t in inst["txs"]:
            edge = (t["sender"], t["receiver"], t["amount"])
            pattern_edges_by_acct[t["sender"]][edge] += 1
            if t["receiver"] != t["sender"]:
                pattern_edges_by_acct[t["receiver"]][edge] += 1
        for acct in inst["accounts"]:
            if acct != inst["hub"]:
                spokes.add(acct)

    involved = set(pattern_edges_by_acct)
    print(f"Accounts on those instance txs: {len(involved)} (hubs={len(hubs)} spokes={len(spokes)})")

    print("Loading ibm_aml_eval.db transactions…", flush=True)
    db = init_eval_db(IBM_DB)()
    try:
        rows = db.execute(
            select(
                Transaction.sender_id,
                Transaction.receiver_id,
                Transaction.amount,
                Transaction.is_labeled_fraud,
            )
        ).all()
    finally:
        db.close()

    txs_by_acct: dict[str, list[tuple[str, str, float, bool]]] = defaultdict(list)
    for sender, receiver, amount, labeled in rows:
        rec = (sender, receiver, round(float(amount), 2), bool(labeled))
        txs_by_acct[sender].append(rec)
        if receiver != sender:
            txs_by_acct[receiver].append(rec)

    def fractions(accounts: set[str], *, require_present: bool) -> tuple[list[float], list[float], int]:
        inst_fracs: list[float] = []
        labeled_fracs: list[float] = []
        missing = 0
        for acct in accounts:
            total = txs_by_acct.get(acct, [])
            if not total:
                missing += 1
                if require_present:
                    continue
                continue
            # Match instance edges without reuse (multiset).
            remaining = pattern_edges_by_acct[acct].copy()
            inst_hits = 0
            labeled_hits = 0
            for sender, receiver, amount, labeled in total:
                if labeled:
                    labeled_hits += 1
                key = (sender, receiver, amount)
                if remaining[key] > 0:
                    remaining[key] -= 1
                    inst_hits += 1
            inst_fracs.append(inst_hits / len(total))
            labeled_fracs.append(labeled_hits / len(total))
        return inst_fracs, labeled_fracs, missing

    print("\n========== Fraud-edge fraction (scored window = entire subsample) ==========")
    print("Numerator: FAN-IN/FAN-OUT Patterns-file edges for that account in the 48h slice.")
    print("Denominator: all eval-DB transactions involving the account (legit + any typology).")

    for label, group in (
        ("FAN-IN/FAN-OUT hubs", hubs),
        ("FAN-IN/FAN-OUT spokes", spokes),
        ("all FAN-IN/FAN-OUT involved accounts", involved),
    ):
        inst_fracs, labeled_fracs, missing = fractions(group, require_present=True)
        print(f"\n--- {label} (present in eval DB: {len(inst_fracs)}; missing from subsample: {missing}) ---")
        if inst_fracs:
            summarize("fraud-instance-edge fraction", inst_fracs)
            summarize("any-Is-Laundering-edge fraction", labeled_fracs)
            totals = [len(txs_by_acct[a]) for a in group if a in txs_by_acct]
            inst_n = []
            for acct in group:
                if acct not in txs_by_acct:
                    continue
                remaining = pattern_edges_by_acct[acct].copy()
                hits = 0
                for sender, receiver, amount, _ in txs_by_acct[acct]:
                    key = (sender, receiver, amount)
                    if remaining[key] > 0:
                        remaining[key] -= 1
                        hits += 1
                inst_n.append(hits)
            summarize("total window txs / account", [float(x) for x in totals])
            summarize("matched instance txs / account", [float(x) for x in inst_n])

    community_sizes(IBM_DB, "ibm_aml_eval.db")
    community_sizes(SNAPSHOT_DB, "snapshot 2026-08-12")


if __name__ == "__main__":
    main()
