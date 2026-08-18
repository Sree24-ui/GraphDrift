#!/usr/bin/env python3
"""Verify closed-hub remint vs ongoing-ring reuse on a snapshot copy."""

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

from sqlalchemy import func, select

from app.api.rings import update_ring
from app.api.schemas import RingStatusUpdate
from app.detection.fusion import create_alert_if_needed, ensure_db_schema, run_detection_cycle
from app.detection.ring_id import get_or_create_ring_id, make_ring_id
from app.models import Account, Alert, Transaction
from evaluation.db import init_eval_db

SNAPSHOT = BACKEND_ROOT / "snapshots" / "graphdrift_snapshot_2026-08-16.db"
LIVE = BACKEND_ROOT / "graphdrift.db"


def _groups(db) -> dict[str, list[Alert]]:
    groups: dict[str, list[Alert]] = defaultdict(list)
    for alert in db.scalars(select(Alert).where(Alert.ring_id.isnot(None))):
        groups[alert.ring_id].append(alert)
    return groups


def _hub_of(alerts: list[Alert]) -> str | None:
    for alert in sorted(alerts, key=lambda item: item.risk_score, reverse=True):
        layer2 = (alert.feature_breakdown or {}).get("layer2_detail") or {}
        if layer2.get("hub_account_id"):
            return str(layer2["hub_account_id"])
        peri = (alert.feature_breakdown or {}).get("peripheral_detail") or {}
        if peri.get("linked_hub_account_id"):
            return str(peri["linked_hub_account_id"])
    return alerts[0].account_id if alerts else None


def main() -> None:
    source = SNAPSHOT if SNAPSHOT.exists() else LIVE
    dest = Path(tempfile.mkdtemp()) / "ring_lifecycle.db"
    shutil.copy2(source, dest)
    print(f"Copy {source.name} -> {dest}")

    factory = init_eval_db(dest)
    db = factory()
    try:
        ensure_db_schema(db)
        as_of = db.scalar(select(func.max(Transaction.timestamp)))
        run_detection_cycle(db, as_of)
        groups = _groups(db)

        candidate_id = None
        candidate_hub = None
        for ring_id, members in sorted(
            groups.items(), key=lambda item: -len(item[1])
        ):
            hub = _hub_of(members)
            if hub and len(members) >= 2:
                candidate_id = ring_id
                candidate_hub = hub
                break
        if not candidate_id or not candidate_hub:
            raise SystemExit("No multi-member ring found to close")

        print(
            f"\n=== Close ring {candidate_id} hub={candidate_hub} "
            f"members={len(groups[candidate_id])} ==="
        )
        update_ring(candidate_id, RingStatusUpdate(status="confirmed"), db)
        closed_rows = list(
            db.scalars(select(Alert).where(Alert.ring_id == candidate_id)).all()
        )
        print(
            "closed statuses:",
            sorted({row.status for row in closed_rows}),
            "count",
            len(closed_rows),
        )

        minted = get_or_create_ring_id(db, candidate_hub)
        expected = make_ring_id(hub_account_id=candidate_hub, occurrence=2)
        print(f"get_or_create after close: {minted}")
        print(f"expected occurrence-2:     {expected}")
        assert minted != candidate_id, "reused closed ring_id"
        assert minted == expected

        later = as_of + timedelta(minutes=5)
        new_members = [candidate_hub, "newspoke_a@ybl", "newspoke_b@ybl"]
        for acct in new_members:
            if db.get(Account, acct) is None:
                db.add(Account(id=acct, created_at=later, last_active_at=later))
        db.commit()
        for acct in new_members:
            explanation = {
                "confidence": "high",
                "primary_reason": "forced remint",
                "layer2_detail": {
                    "hub_account_id": candidate_hub,
                    "member_accounts": new_members,
                    "is_hub_account": acct == candidate_hub,
                },
            }
            fused = {
                "account_id": acct,
                "fused_score": 4.5,
                "confidence": "high",
                "gdi_score": 3.0,
                "ring_risk_score": 4.0,
                "gdi_percentile": 0.95,
                "ring_percentile": 0.95,
                "community_id": 99,
                "ring_info": {
                    "hub_account_id": candidate_hub,
                    "member_accounts": new_members,
                },
            }
            create_alert_if_needed(
                db, acct, fused, explanation, later, alert_threshold=0.0
            )

        old_after = list(
            db.scalars(select(Alert).where(Alert.ring_id == candidate_id)).all()
        )
        new_after = list(
            db.scalars(select(Alert).where(Alert.ring_id == minted)).all()
        )
        print("old ring still", candidate_id, "accounts", sorted(a.account_id for a in old_after))
        print("new ring", minted, "accounts", sorted(a.account_id for a in new_after))
        assert {a.account_id for a in old_after} == {a.account_id for a in closed_rows}
        assert not {a.account_id for a in old_after} & (
            set(new_members) - {candidate_hub}
        ) or all(
            a.ring_id == candidate_id
            for a in old_after
            if a.account_id != candidate_hub
        )
        assert {a.account_id for a in new_after} == set(new_members)
        assert all(a.status == "confirmed" for a in old_after)
        print("REMINT OK: closed case untouched; new occurrence got a new ring_id")

        as_of2 = as_of + timedelta(seconds=45)
        print(f"\n=== Ongoing reuse: second cycle as_of={as_of2.isoformat()} ===")
        before_open = {
            ring_id
            for ring_id, members in _groups(db).items()
            if any(a.status in {"new", "reviewing"} for a in members)
        }
        run_detection_cycle(db, as_of2)
        after_groups = _groups(db)
        reused = []
        for ring_id in before_open:
            if ring_id == minted:
                continue
            members = after_groups.get(ring_id, [])
            timestamps = {a.detected_at for a in members}
            if members:
                reused.append((ring_id, len(members), len(timestamps)))
        print(f"open rings still present after cycle 2: {len(reused)}")
        for ring_id, n, n_ts in reused[:8]:
            print(f"  {ring_id}: alerts={n} distinct detected_at={n_ts}")
        assert any(n_ts >= 2 for _, n, n_ts in reused), (
            "expected at least one ongoing ring with multiple detection timestamps"
        )
        print("REUSE OK: open rings kept the same ring_id across cycles")
    finally:
        db.close()


if __name__ == "__main__":
    main()
