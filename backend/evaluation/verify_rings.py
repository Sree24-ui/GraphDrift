#!/usr/bin/env python3
"""Verify ring_id grouping, cycle stability, peripherals, and bulk confirm."""

from __future__ import annotations

import shutil
import sys
import tempfile
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import func, select, text

from app.api.rings import _build_ring_detail, update_ring
from app.api.schemas import RingStatusUpdate
from app.detection.fusion import ensure_db_schema, run_detection_cycle
from app.models import Alert
from evaluation.db import init_eval_db

SNAPSHOT = BACKEND_ROOT / "snapshots" / "graphdrift_snapshot_2026-08-16.db"
LIVE = BACKEND_ROOT / "graphdrift.db"


def _print_groups(db, title: str) -> dict[str, list[Alert]]:
    alerts = list(db.scalars(select(Alert).where(Alert.ring_id.isnot(None))).all())
    groups: dict[str, list[Alert]] = defaultdict(list)
    for alert in alerts:
        groups[alert.ring_id].append(alert)
    print(f"\n=== {title} ===")
    print(f"alerts with ring_id: {len(alerts)} | distinct rings: {len(groups)}")
    for ring_id, members in sorted(
        groups.items(), key=lambda item: -max(a.risk_score for a in item[1])
    )[:8]:
        hub = None
        for alert in members:
            layer2 = (alert.feature_breakdown or {}).get("layer2_detail") or {}
            if layer2.get("hub_account_id"):
                hub = layer2["hub_account_id"]
                break
        detected = sorted({a.detected_at.isoformat() for a in members})
        statuses = {a.account_id: a.status for a in members}
        periph = [
            a.account_id
            for a in members
            if a.pattern_type == "peripheral_structural"
        ]
        print(
            f"  {ring_id} hub={hub} members={len(members)} "
            f"peripherals={len(periph)} timestamps={len(detected)}"
        )
        print(f"    accounts={sorted(statuses)}")
        print(f"    detected_at={detected}")
        if periph:
            print(f"    peripheral={periph}")
    return groups


def main() -> None:
    source = SNAPSHOT if SNAPSHOT.exists() else LIVE
    dest = Path(tempfile.mkdtemp()) / "rings_verify.db"
    shutil.copy2(source, dest)
    print(f"Working copy of {source.name} -> {dest}")

    factory = init_eval_db(dest)
    db = factory()
    try:
        ensure_db_schema(db)
        as_of = db.scalar(select(func.max(Alert.detected_at)))
        from app.models import Transaction

        tx_as_of = db.scalar(select(func.max(Transaction.timestamp)))
        as_of = tx_as_of
        print(f"as_of cycle1={as_of.isoformat()}")

        run_detection_cycle(db, as_of)
        groups1 = _print_groups(db, "After cycle 1")

        as_of2 = as_of + timedelta(seconds=45)
        print(f"as_of cycle2={as_of2.isoformat()}")
        run_detection_cycle(db, as_of2)
        groups2 = _print_groups(db, "After cycle 2")

        stable = []
        for ring_id, members2 in groups2.items():
            members1 = groups1.get(ring_id, [])
            if members1:
                ts = {a.detected_at for a in members2}
                stable.append((ring_id, len(members1), len(members2), len(ts)))
        print("\n=== ring_id stability (present in both cycles) ===")
        for ring_id, n1, n2, n_ts in stable[:10]:
            print(
                f"  {ring_id}: cycle1_alerts={n1} cycle2_alerts={n2} "
                f"distinct detected_at={n_ts}"
            )
        if not stable:
            print("  none — no overlapping ring_ids (unexpected)")

        candidate = None
        for ring_id, members in groups2.items():
            if len(members) >= 2 and ring_id in groups1:
                candidate = ring_id
                break
        if candidate is None and groups2:
            candidate = next(iter(groups2))

        if candidate:
            before = list(
                db.scalars(select(Alert).where(Alert.ring_id == candidate)).all()
            )
            print(f"\n=== Bulk confirm ring {candidate} ===")
            print("before:", {a.account_id: a.status for a in before})
            result = update_ring(
                candidate, RingStatusUpdate(status="confirmed"), db
            )
            after = list(
                db.scalars(select(Alert).where(Alert.ring_id == candidate)).all()
            )
            print("updated_ids:", result.updated_alert_ids)
            print("skipped:", [s.model_dump() for s in result.skipped])
            print("after:", {a.account_id: a.status for a in after})
            detail = _build_ring_detail(candidate, after)
            print(
                f"detail hub={detail.hub_account_id} members={detail.member_count} "
                f"status={detail.status}"
            )
            for member in detail.members:
                print(
                    f"  {member.role:10s} {member.account_id:28s} "
                    f"alert={member.alert_id} status={member.status} "
                    f"conf={member.confidence}"
                )
        else:
            print("No rings to bulk-confirm.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
