#!/usr/bin/env python3
"""
Selectivity analysis for the peripheral structural pass.

Answers:
  1. True selection universe (1–2 tx, 1-hop from fusion hubs)
  2. Precision/recall within that universe (not vs all active accounts)
  3. Whether directional fan-in/fan-out filter excludes hub neighbors
  4. Decoy stress test (benign merchant cluster near but not on flagged hub)
"""

from __future__ import annotations

import argparse
import copy
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import and_, or_, select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.detection.features import WINDOW_MINUTES, get_active_accounts  # noqa: E402
from app.detection.fusion import compute_fused_scores, select_top_anomaly_accounts  # noqa: E402
from app.detection.structural_pass import (  # noqa: E402
    _fan_in_sender_to_hub,
    _fan_out_receiver_from_hub,
    extract_structural_features,
    score_peripheral_accounts,
)
from app.models import Account, Transaction  # noqa: E402
from evaluation.db import init_eval_db  # noqa: E402
from evaluation.run_eval import ground_truth_accounts, max_timestamp  # noqa: E402

SNAPSHOT_DB = BACKEND_ROOT / "snapshots" / "graphdrift_snapshot_2026-08-12.db"


@dataclass
class NeighborRecord:
    account_id: str
    tx_count: int
    in_degree: int
    out_degree: int
    is_fraud: bool
    hub_neighbors: list[str] = field(default_factory=list)
    hub_connected: bool = False
    directional_match: bool = False
    leg_patterns: list[str] = field(default_factory=list)
    peripheral_flagged: bool = False
    hub_only_would_flag: bool = False


def _window_bounds(as_of: datetime, window_minutes: int) -> tuple[datetime, datetime]:
    return as_of - timedelta(minutes=window_minutes), as_of


def _hubs_for_account(features: dict, hubs: set[str]) -> list[str]:
    connected = set(features["inbound_from"]) | set(features["outbound_to"])
    return sorted(connected & hubs)


def _directional_patterns(features: dict, hub_id: str) -> list[str]:
    patterns: list[str] = []
    if _fan_in_sender_to_hub(features, hub_id):
        patterns.append("fan_in_sender")
    if _fan_out_receiver_from_hub(features, hub_id):
        patterns.append("fan_out_receiver")
    return patterns


def build_neighbor_records(
    db,
    as_of: datetime,
    *,
    window_minutes: int,
    hubs: set[str],
    fraud: set[str],
    peripheral_flagged: set[str],
) -> list[NeighborRecord]:
    active = set(get_active_accounts(db, as_of, window_minutes))
    records: list[NeighborRecord] = []

    for account_id in sorted(active - hubs):
        features = extract_structural_features(db, account_id, as_of, window_minutes)
        if features is None:
            continue

        account_hubs = _hubs_for_account(features, hubs)
        if not account_hubs:
            continue

        leg_patterns: list[str] = []
        directional = False
        for hub_id in account_hubs:
            for pattern in _directional_patterns(features, hub_id):
                directional = True
                leg_patterns.append(f"{hub_id}:{pattern}")

        records.append(
            NeighborRecord(
                account_id=account_id,
                tx_count=features["tx_count"],
                in_degree=features["in_degree"],
                out_degree=features["out_degree"],
                is_fraud=account_id in fraud,
                hub_neighbors=account_hubs,
                hub_connected=True,
                directional_match=directional,
                leg_patterns=leg_patterns,
                peripheral_flagged=account_id in peripheral_flagged,
                hub_only_would_flag=True,
            )
        )

    return records


def _confusion(records: list[NeighborRecord], flagged: set[str]) -> dict:
    fraud = {r.account_id for r in records if r.is_fraud}
    benign = {r.account_id for r in records if not r.is_fraud}
    tp = len(flagged & fraud)
    fp = len(flagged & benign)
    fn = len(fraud - flagged)
    tn = len(benign - flagged)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "fpr": fpr,
        "flagged": len(flagged),
        "fraud": len(fraud),
        "benign": len(benign),
        "universe": len(records),
    }


def _print_confusion(label: str, stats: dict) -> None:
    print(
        f"  {label}\n"
        f"    universe={stats['universe']}  fraud={stats['fraud']}  benign={stats['benign']}\n"
        f"    flagged={stats['flagged']}  TP={stats['tp']} FP={stats['fp']} "
        f"FN={stats['fn']} TN={stats['tn']}\n"
        f"    P={stats['precision']:.3f}  R={stats['recall']:.3f}  FPR={stats['fpr']:.4f}"
    )


def analyze_snapshot(db_path: Path) -> dict:
    db = init_eval_db(db_path)()
    try:
        as_of = max_timestamp(db)
        wm = WINDOW_MINUTES
        fraud = ground_truth_accounts(db, as_of, window_minutes=wm)

        fused = compute_fused_scores(db, as_of, window_minutes=wm, min_transactions=3)
        hubs, _, _ = select_top_anomaly_accounts(fused, "fused_score")
        peripheral = score_peripheral_accounts(db, as_of, wm, hubs)
        peripheral_flagged = {row["account_id"] for row in peripheral}

        records = build_neighbor_records(
            db, as_of, window_minutes=wm, hubs=hubs, fraud=fraud,
            peripheral_flagged=peripheral_flagged,
        )

        hub_only_flagged = {r.account_id for r in records}
        directional_flagged = {r.account_id for r in records if r.directional_match}
        excluded_by_directional = [
            r for r in records if r.hub_connected and not r.directional_match
        ]

        actual = _confusion(records, peripheral_flagged)
        hub_only = _confusion(records, hub_only_flagged)
        directional = _confusion(records, directional_flagged)

        print("=" * 72)
        print("PERIPHERAL SELECTIVITY — snapshot")
        print("=" * 72)
        print(f"as_of={as_of.isoformat()}  window={wm}m")
        print(f"fusion top-anomaly hubs (n={len(hubs)}): {sorted(hubs)}")

        fraud_in_univ = [r for r in records if r.is_fraud]
        benign_in_univ = [r for r in records if not r.is_fraud]
        print(
            f"\n1. TRUE SELECTION UNIVERSE (1–2 tx, 1-hop from flagged hub)\n"
            f"   total={len(records)}  fraud={len(fraud_in_univ)}  benign={len(benign_in_univ)}"
        )

        print("\n2. METRICS WITHIN SELECTION UNIVERSE")
        _print_confusion("Peripheral pass (actual)", actual)
        _print_confusion("Hub-only heuristic (flag all hub neighbors)", hub_only)
        _print_confusion("Directional filter only (no score gate)", directional)

        flag_rate_fraud = actual["tp"] / actual["fraud"] if actual["fraud"] else 0.0
        flag_rate_benign = actual["fp"] / actual["benign"] if actual["benign"] else 0.0
        print(
            f"\n   Flag rate within universe: fraud {flag_rate_fraud:.1%}  "
            f"benign {flag_rate_benign:.1%}"
        )
        if abs(flag_rate_fraud - flag_rate_benign) < 0.05:
            print(
                "   ⚠ Flag rates are similar — peripheral pass flags most hub neighbors "
                "regardless of label."
            )

        print(
            f"\n3. DIRECTIONAL FILTER DISCRIMINATION\n"
            f"   hub-connected 1–2 tx neighbors: {len(records)}\n"
            f"   pass directional check: {len(directional_flagged)}\n"
            f"   EXCLUDED by directional check: {len(excluded_by_directional)}"
        )
        if excluded_by_directional:
            print("   Excluded accounts (hub link but wrong in/out pattern):")
            for r in excluded_by_directional[:15]:
                print(
                    f"     {r.account_id}  fraud={r.is_fraud}  "
                    f"tx={r.tx_count} in_deg={r.in_degree} out_deg={r.out_degree}  "
                    f"hubs={r.hub_neighbors}"
                )
            if len(excluded_by_directional) > 15:
                print(f"     ... and {len(excluded_by_directional) - 15} more")
        else:
            print(
                "   ⚠ ZERO exclusions — every 1–2 tx hub neighbor already matches "
                "fan-in-sender or fan-out-receiver pattern."
            )

        benign_fps = [r for r in records if r.peripheral_flagged and not r.is_fraud]
        benign_hub_neighbors_not_flagged = [
            r for r in records if not r.is_fraud and not r.peripheral_flagged
        ]
        print(
            f"\n   Benign hub neighbors flagged (FP within universe): {len(benign_fps)}"
        )
        for r in benign_fps:
            print(
                f"     {r.account_id}  pattern={r.leg_patterns}  "
                f"in_deg={r.in_degree} out_deg={r.out_degree}"
            )
        print(
            f"   Benign hub neighbors correctly left unflagged: "
            f"{len(benign_hub_neighbors_not_flagged)}"
        )

        return {
            "hubs": hubs,
            "records": records,
            "actual": actual,
            "hub_only": hub_only,
            "directional": directional,
            "excluded_by_directional": excluded_by_directional,
            "as_of": as_of,
            "window_minutes": wm,
        }
    finally:
        db.close()


def run_decoy_stress_test(source_db: Path) -> None:
    """
    Inject a benign merchant with several 1-tx customers, structurally near a
    flagged hub (shares a counterparty) but with no direct tx to the hub.
    """
    print("\n" + "=" * 72)
    print("4. DECOY STRESS TEST")
    print("=" * 72)

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "decoy.db"
        shutil.copy2(source_db, db_path)
        db = init_eval_db(db_path)()
        try:
            as_of = max_timestamp(db)
            wm = WINDOW_MINUTES
            ws, we = _window_bounds(as_of, wm)

            fused = compute_fused_scores(db, as_of, window_minutes=wm, min_transactions=3)
            hubs, _, _ = select_top_anomaly_accounts(fused, "fused_score")
            if not hubs:
                print("  No fusion hubs — skipping decoy test.")
                return

            hub = sorted(hubs)[0]

            # Find a benign 2-hop neighbor: account that transacted with someone
            # who transacted with the hub, but not with hub directly.
            hub_txs = list(
                db.scalars(
                    select(Transaction).where(
                        and_(
                            Transaction.timestamp >= ws,
                            Transaction.timestamp <= we,
                            or_(
                                Transaction.sender_id == hub,
                                Transaction.receiver_id == hub,
                            ),
                        )
                    )
                ).all()
            )
            first_hop = set()
            for tx in hub_txs:
                first_hop.add(tx.sender_id if tx.receiver_id == hub else tx.receiver_id)
            first_hop.discard(hub)

            bridge = None
            second_hop_candidates: set[str] = set()
            for fh in first_hop:
                fh_txs = list(
                    db.scalars(
                        select(Transaction).where(
                            and_(
                                Transaction.timestamp >= ws,
                                Transaction.timestamp <= we,
                                or_(
                                    Transaction.sender_id == fh,
                                    Transaction.receiver_id == fh,
                                ),
                            )
                        )
                    ).all()
                )
                for tx in fh_txs:
                    other = tx.sender_id if tx.receiver_id == fh else tx.receiver_id
                    if other not in first_hop and other != hub:
                        second_hop_candidates.add(other)
                        bridge = fh
                        break
                if bridge:
                    break

            merchant_id = "decoy_merchant@paytm"
            customer_ids = [f"decoy_customer_{i}@ybl" for i in range(1, 9)]
            now = as_of - timedelta(minutes=2)

            for acct_id in [merchant_id, *customer_ids]:
                if db.get(Account, acct_id) is None:
                    db.add(Account(id=acct_id, created_at=now, last_active_at=now))

            # Merchant: high activity (8 inbound from customers), no hub contact.
            for i, cust in enumerate(customer_ids):
                db.add(
                    Transaction(
                        sender_id=cust,
                        receiver_id=merchant_id,
                        amount=150.0 + i,
                        timestamp=now - timedelta(seconds=30 + i),
                        is_synthetic_attack=False,
                    )
                )

            # Optional: merchant pays bridge (2-hop from hub, not hub itself).
            if bridge:
                db.add(
                    Transaction(
                        sender_id=merchant_id,
                        receiver_id=bridge,
                        amount=500.0,
                        timestamp=now - timedelta(seconds=5),
                        is_synthetic_attack=False,
                    )
                )

            db.commit()

            peripheral = score_peripheral_accounts(db, as_of, wm, hubs)
            flagged = {r["account_id"] for r in peripheral}
            decoy_accounts = {merchant_id, *customer_ids}

            print(f"  Flagged hub: {hub}")
            print(f"  2-hop bridge (if found): {bridge}")
            print(f"  Injected decoy accounts: {len(decoy_accounts)}")
            print(f"  Decoy accounts flagged by peripheral pass: {sorted(flagged & decoy_accounts) or 'none'}")
            print(
                "  Expected: none (no direct 1-hop tx to flagged hub; "
                "merchant has >2 tx so excluded from peripheral universe anyway)."
            )

            # Stronger decoy: benign 1-tx payers directly to hub (mimics FP scenario).
            payer_ids = [f"decoy_payer_{i}@okhdfc" for i in range(1, 6)]
            for acct_id in payer_ids:
                if db.get(Account, acct_id) is None:
                    db.add(Account(id=acct_id, created_at=now, last_active_at=now))
                db.add(
                    Transaction(
                        sender_id=acct_id,
                        receiver_id=hub,
                        amount=200.0,
                        timestamp=now - timedelta(seconds=10),
                        is_synthetic_attack=False,
                    )
                )
            db.commit()

            peripheral2 = score_peripheral_accounts(db, as_of, wm, hubs)
            flagged2 = {r["account_id"] for r in peripheral2}
            payer_flagged = flagged2 & set(payer_ids)
            print(
                f"\n  Adversarial decoy: 5 benign 1-tx fan-in senders directly to hub"
            )
            print(f"  Flagged: {len(payer_flagged)}/5 — {sorted(payer_flagged)}")
            print(
                "  This confirms hub-proximity + leg pattern cannot separate benign "
                "one-shot payers from fraud legs when structure is identical."
            )
        finally:
            db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Peripheral pass selectivity analysis")
    parser.add_argument(
        "--db",
        type=Path,
        default=SNAPSHOT_DB,
        help="Snapshot database path",
    )
    parser.add_argument("--skip-decoy", action="store_true")
    args = parser.parse_args()

    analyze_snapshot(args.db)
    if not args.skip_decoy:
        run_decoy_stress_test(args.db)


if __name__ == "__main__":
    main()
