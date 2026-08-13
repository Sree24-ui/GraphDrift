#!/usr/bin/env python3
"""Measure open-alert volume and synthetic-attack overlap after tuning."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta

from sqlalchemy import and_, func, select

from app.db import SessionLocal
from app.detection.features import WINDOW_MINUTES
from app.detection.fusion import (
    _account_has_synthetic_activity,
    _synthetic_attack_accounts,
    run_detection_cycle,
)
from app.models import Alert, Transaction


def open_alert_count(db) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Alert)
            .where(Alert.status.in_(["new", "reviewing"]))
        )
        or 0
    )


def status_breakdown(db) -> dict[str, int]:
    rows = db.execute(
        select(Alert.status, func.count()).group_by(Alert.status)
    ).all()
    return {status: count for status, count in rows}


def synthetic_overlap(db, hours: float = 1.0) -> dict:
    as_of = datetime.now()
    window_start = as_of - timedelta(hours=hours)
    recent = db.scalars(
        select(Alert).where(Alert.detected_at >= window_start)
    ).all()
    if not recent:
        return {"recent_alerts": 0, "synthetic_hits": 0, "overlap_pct": 0.0}

    hits = sum(
        1
        for alert in recent
        if _account_has_synthetic_activity(db, alert.account_id, as_of)
    )
    return {
        "recent_alerts": len(recent),
        "synthetic_hits": hits,
        "overlap_pct": hits / len(recent) if recent else 0.0,
    }


def attack_tx_rate(db, minutes: float = 10.0) -> dict:
    since = datetime.now() - timedelta(minutes=minutes)
    total = (
        db.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.timestamp >= since)
        )
        or 0
    )
    attack = (
        db.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.timestamp >= since,
                Transaction.is_synthetic_attack.is_(True),
            )
        )
        or 0
    )
    burst_events = (
        db.scalar(
            select(func.count(func.distinct(Transaction.sender_id)))
            .select_from(Transaction)
            .where(
                Transaction.timestamp >= since,
                Transaction.is_synthetic_attack.is_(True),
            )
        )
        or 0
    )
    return {
        "window_minutes": minutes,
        "total_tx": total,
        "synthetic_tx": attack,
        "synthetic_tx_per_min": attack / minutes if minutes else 0,
        "distinct_synthetic_senders": burst_events,
    }


def print_snapshot(label: str) -> None:
    db = SessionLocal()
    try:
        print(f"\n=== {label} @ {datetime.now().isoformat(timespec='seconds')} ===")
        print(f"Open alerts (new+reviewing): {open_alert_count(db)}")
        print(f"Status breakdown: {status_breakdown(db)}")
        overlap = synthetic_overlap(db, hours=1.0)
        print(
            "Synthetic overlap (last 1h alerts): "
            f"{overlap['synthetic_hits']}/{overlap['recent_alerts']} "
            f"({overlap['overlap_pct']:.0%})"
        )
        rate = attack_tx_rate(db, minutes=10.0)
        print(
            f"Attack tx rate (last {rate['window_minutes']:.0f}m): "
            f"{rate['synthetic_tx']} synthetic / {rate['total_tx']} total "
            f"({rate['synthetic_tx_per_min']:.1f} synth tx/min)"
        )
    finally:
        db.close()


def run_monitor(minutes: int, interval_seconds: int = 60) -> None:
    end = time.time() + minutes * 60
    while time.time() < end:
        print_snapshot("monitor")
        time.sleep(interval_seconds)
    print_snapshot("final")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "monitor":
        mins = int(sys.argv[2]) if len(sys.argv) > 2 else 15
        run_monitor(mins)
    elif len(sys.argv) > 1 and sys.argv[1] == "cycle":
        db = SessionLocal()
        try:
            actions, diag = run_detection_cycle(db, return_diagnostics=True)
            print(
                f"Cycle: created/escalated={len(actions)}, "
                f"auto_closed={diag.get('expired_count', 0)}, "
                f"open_now={open_alert_count(db)}"
            )
        finally:
            db.close()
    else:
        print_snapshot("snapshot")
