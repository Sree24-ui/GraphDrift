"""Purpose-built evasive attack generators (not the existing slow-drip variant).

Attackers are assumed to know: 15/60-minute detection windows, hub-concentration
ring scoring, MIN_RING_MEMBER_COUNT=4, and a top-5% alert budget.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Transaction
from app.simulation.generator import (
    _create_fresh_account,
    _distribute_amounts,
    _generate_smurf_amount,
    _persist_transaction,
)

VARIANTS = (
    "standard",
    "straddle_15",
    "straddle_60",
    "diluted_hub",
    "minimal_ring",
)


def _tx(
    db: Session,
    sender_id: str,
    receiver_id: str,
    amount: float,
    timestamp: datetime,
    variant: str,
) -> Transaction:
    return _persist_transaction(
        db,
        sender_id,
        receiver_id,
        amount,
        timestamp,
        True,
        commit=False,
        attack_variant=variant,
    )


def _cluster(center: datetime, n: int, half_width: timedelta) -> list[datetime]:
    stamps = []
    for i in range(n):
        frac = (i / max(n - 1, 1)) * 2 - 1  # [-1, 1]
        stamps.append(center + timedelta(seconds=frac * half_width.total_seconds()))
    return sorted(stamps)


def _fresh_ids(db: Session, n: int, at: datetime) -> list[str]:
    return [_create_fresh_account(db, at) for _ in range(n)]


def _instance_dict(
    *,
    variant: str,
    instance_id: str,
    hubs: list[str],
    members: set[str],
    txs: list[Transaction],
    extra: dict | None = None,
) -> dict:
    payload = {
        "instance_id": instance_id,
        "variant": variant,
        "hubs": list(hubs),
        "members": sorted(members),
        "n_tx": len(txs),
        "first_ts": min(t.timestamp for t in txs),
        "last_ts": max(t.timestamp for t in txs),
    }
    if extra:
        payload.update(extra)
    return payload


def generate_standard_attack(
    db: Session,
    *,
    start: datetime,
    fan_in_count: int = 9,
    fan_out_count: int = 9,
    span_seconds: float = 150,
    variant: str = "standard",
    instance_id: str | None = None,
) -> dict:
    """Control: classic single-hub fan-in/fan-out entirely inside a 15-min window."""
    mule_id = _create_fresh_account(db, start)
    senders = _fresh_ids(db, fan_in_count, start)
    fan_in_ts = _cluster(
        start + timedelta(seconds=span_seconds * 0.35),
        fan_in_count,
        timedelta(seconds=40),
    )
    fan_in_amts = [_generate_smurf_amount() for _ in range(fan_in_count)]
    txs: list[Transaction] = []
    members = {mule_id}
    for sender_id, amount, ts in zip(senders, fan_in_amts, fan_in_ts):
        txs.append(_tx(db, sender_id, mule_id, amount, ts, variant))
        members.add(sender_id)
    total_out = round(sum(fan_in_amts) * (1 - random.uniform(0.02, 0.05)), 2)
    out_amts = _distribute_amounts(total_out, fan_out_count)
    fan_out_ts = _cluster(
        fan_in_ts[-1] + timedelta(seconds=20),
        fan_out_count,
        timedelta(seconds=25),
    )
    for amount, ts in zip(out_amts, fan_out_ts):
        rid = _create_fresh_account(db, ts)
        members.add(rid)
        txs.append(_tx(db, mule_id, rid, amount, ts, variant))
    db.flush()
    return _instance_dict(
        variant=variant,
        instance_id=instance_id or f"{variant}:{mule_id}",
        hubs=[mule_id],
        members=members,
        txs=txs,
    )


def generate_straddle_attack(
    db: Session,
    *,
    boundary: datetime,
    window_minutes: int,
    fan_in_count: int = 9,
    fan_out_count: int = 9,
    instance_id: str | None = None,
) -> dict:
    """Split fan-in and fan-out across a window boundary.

    window_minutes=15 → variant straddle_15 (60-min scale should still see it).
    window_minutes=60 → variant straddle_60 (full evasion attempt).
    """
    if window_minutes not in (15, 60):
        raise ValueError("window_minutes must be 15 or 60")
    variant = f"straddle_{window_minutes}"
    mule_id = _create_fresh_account(db, boundary)
    senders = _fresh_ids(db, fan_in_count, boundary)
    n_before = fan_in_count // 2
    n_after = fan_in_count - n_before
    in_before = _cluster(boundary - timedelta(seconds=50), n_before, timedelta(seconds=35))
    in_after = _cluster(boundary + timedelta(seconds=50), n_after, timedelta(seconds=35))
    fan_in_ts = in_before + in_after
    fan_in_amts = [_generate_smurf_amount() for _ in range(fan_in_count)]
    txs: list[Transaction] = []
    members = {mule_id}
    for sender_id, amount, ts in zip(senders, fan_in_amts, fan_in_ts):
        txs.append(_tx(db, sender_id, mule_id, amount, ts, variant))
        members.add(sender_id)

    total_out = round(sum(fan_in_amts) * (1 - random.uniform(0.02, 0.05)), 2)
    n_out_before = fan_out_count // 2
    n_out_after = fan_out_count - n_out_before
    out_before = _cluster(boundary - timedelta(seconds=40), n_out_before, timedelta(seconds=25))
    out_after = _cluster(boundary + timedelta(seconds=70), n_out_after, timedelta(seconds=25))
    fan_out_ts = out_before + out_after
    out_amts = _distribute_amounts(total_out, fan_out_count)
    for amount, ts in zip(out_amts, fan_out_ts):
        rid = _create_fresh_account(db, ts)
        members.add(rid)
        txs.append(_tx(db, mule_id, rid, amount, ts, variant))
    db.flush()
    return _instance_dict(
        variant=variant,
        instance_id=instance_id or f"{variant}:{mule_id}",
        hubs=[mule_id],
        members=members,
        txs=txs,
        extra={
            "boundary": boundary,
            "n_fan_in_before": n_before,
            "n_fan_in_after": n_after,
            "n_fan_out_before": n_out_before,
            "n_fan_out_after": n_out_after,
        },
    )


def generate_diluted_hub_attack(
    db: Session,
    *,
    start: datetime,
    n_mules: int = 3,
    senders_per_mule: int = 3,
    receivers_per_mule: int = 3,
    instance_id: str | None = None,
) -> dict:
    """Split the hub role across co-mules so no single node dominates edges."""
    variant = "diluted_hub"
    mules = _fresh_ids(db, n_mules, start)
    n_senders = n_mules * senders_per_mule
    senders = _fresh_ids(db, n_senders, start)
    txs: list[Transaction] = []
    members = set(mules)
    for i, sender_id in enumerate(senders):
        mule = mules[i % n_mules]
        ts = start + timedelta(seconds=8 * i)
        txs.append(_tx(db, sender_id, mule, _generate_smurf_amount(), ts, variant))
        members.add(sender_id)
    for i, mule in enumerate(mules):
        nxt = mules[(i + 1) % n_mules]
        ts = start + timedelta(seconds=8 * n_senders + 5 + 4 * i)
        txs.append(_tx(db, mule, nxt, round(random.uniform(8_000, 12_000), 2), ts, variant))
    out_base = start + timedelta(seconds=8 * n_senders + 25)
    for mi, mule in enumerate(mules):
        for j in range(receivers_per_mule):
            ts = out_base + timedelta(seconds=6 * (mi * receivers_per_mule + j))
            rid = _create_fresh_account(db, ts)
            members.add(rid)
            txs.append(_tx(db, mule, rid, _generate_smurf_amount(), ts, variant))
    db.flush()
    return _instance_dict(
        variant=variant,
        instance_id=instance_id or f"{variant}:{mules[0]}",
        hubs=list(mules),
        members=members,
        txs=txs,
        extra={"n_mules": n_mules},
    )


def generate_minimal_ring_attack(
    db: Session,
    *,
    start: datetime,
    fan_in_count: int = 4,
    fan_out_count: int = 4,
    instance_id: str | None = None,
) -> dict:
    """Same star shape as the control, but 4+4 legs instead of 8–10.

    mule + 4 senders + 4 receivers = 9 nodes, still above MIN_RING_MEMBER_COUNT=4.
    The test is whether the lower structural bound still ranks in the top 5%.
    """
    return generate_standard_attack(
        db,
        start=start,
        fan_in_count=fan_in_count,
        fan_out_count=fan_out_count,
        span_seconds=90,
        variant="minimal_ring",
        instance_id=instance_id,
    )
