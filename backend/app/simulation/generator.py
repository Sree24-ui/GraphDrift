import asyncio
import random
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import numpy as np
from faker import Faker
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Account, Transaction
from app.simulation.constants import (
    MULE_ATTACK_PROBABILITY,
    SIMULATION_INTERVAL_SECONDS,
    SLOW_DRIP_ATTACK_PROBABILITY,
)

try:
    from app.api.websocket import build_transaction_message, live_feed_manager
except ImportError:  # pragma: no cover - optional during isolated module tests
    live_feed_manager = None
    build_transaction_message = None

fake = Faker("en_IN")

UPI_HANDLES = ("okhdfc", "okicici", "paytm", "ybl")
POOL_SIZE = 150

_account_pool: list[str] = []


def set_simulation_seed(seed: int | None) -> None:
    """Seed Faker, random, and numpy. None leaves RNGs untouched (live/demo)."""
    global fake, _account_pool
    _account_pool = []
    if seed is None:
        return
    random.seed(seed)
    np.random.seed(seed)
    fake = Faker("en_IN")
    fake.seed_instance(seed)


def reset_account_pool() -> None:
    global _account_pool
    _account_pool = []


def _now(clock: datetime | None) -> datetime:
    return clock if clock is not None else datetime.now()


def _generate_upi_id() -> str:
    username = fake.user_name().lower().replace(".", "").replace(" ", "")[:20]
    handle = random.choice(UPI_HANDLES)
    return f"{username}@{handle}"


def _generate_normal_amount() -> float:
    if random.random() < 0.12:
        return round(random.uniform(5_000, 40_000), 2)

    # Log-normal skew: most amounts cluster toward the lower end of ₹50–₹5,000.
    amount = float(np.random.lognormal(mean=6.0, sigma=0.65))
    return round(min(max(amount, 50), 5_000), 2)


def _generate_smurf_amount() -> float:
    return round(random.uniform(40_000, 49_999), 2)


def _clustered_timestamps(
    count: int, window_seconds: float, base_time: datetime | None = None
) -> list[datetime]:
    start = base_time or datetime.now()
    offsets = sorted(random.uniform(0, window_seconds) for _ in range(count))
    return [start + timedelta(seconds=offset) for offset in offsets]


def _distribute_amounts(total: float, count: int) -> list[float]:
    if count == 1:
        return [round(total, 2)]

    weights = np.random.dirichlet(np.ones(count))
    amounts = [round(total * weight, 2) for weight in weights]
    diff = round(total - sum(amounts), 2)
    amounts[-1] = round(amounts[-1] + diff, 2)
    return amounts


def seed_accounts(
    db: Session,
    *,
    created_at: datetime | None = None,
    pool_size: int | None = None,
) -> list[str]:
    global _account_pool

    target = POOL_SIZE if pool_size is None else int(pool_size)
    existing_ids = list(db.scalars(select(Account.id)).all())
    if existing_ids:
        if len(existing_ids) >= target:
            _account_pool = sorted(existing_ids)
            return _account_pool
        seen = set(existing_ids)
    else:
        seen = set()

    stamp = _now(created_at)
    while len(seen) < target:
        seen.add(_generate_upi_id())

    existing_set = set(existing_ids)
    for account_id in seen:
        if account_id in existing_set:
            continue
        db.add(
            Account(
                id=account_id,
                created_at=stamp,
                last_active_at=stamp,
            )
        )

    db.commit()
    _account_pool = sorted(seen)
    return _account_pool


def _ensure_pool(db: Session, *, created_at: datetime | None = None) -> list[str]:
    if not _account_pool:
        return seed_accounts(db, created_at=created_at)
    return _account_pool


def _create_fresh_account(db: Session, base_time: datetime) -> str:
    while True:
        account_id = _generate_upi_id()
        if db.get(Account, account_id) is None:
            db.add(
                Account(
                    id=account_id,
                    created_at=base_time,
                    last_active_at=base_time,
                )
            )
            db.flush()
            return account_id


def _persist_transaction(
    db: Session,
    sender_id: str,
    receiver_id: str,
    amount: float,
    timestamp: datetime,
    is_synthetic_attack: bool,
    *,
    commit: bool = True,
    attack_variant: str | None = None,
) -> Transaction:
    tx = Transaction(
        sender_id=sender_id,
        receiver_id=receiver_id,
        amount=amount,
        timestamp=timestamp,
        is_synthetic_attack=is_synthetic_attack,
        attack_variant=attack_variant,
    )
    db.add(tx)

    for account_id in (sender_id, receiver_id):
        account = db.get(Account, account_id)
        if account is not None and account.last_active_at < timestamp:
            account.last_active_at = timestamp

    if commit:
        db.commit()
        db.refresh(tx)
    else:
        db.flush()
    return tx


def generate_normal_transaction(
    db: Session, *, now: datetime | None = None, commit: bool = True
) -> Transaction:
    pool = _ensure_pool(db, created_at=now)
    sender_id, receiver_id = random.sample(pool, 2)
    return _persist_transaction(
        db=db,
        sender_id=sender_id,
        receiver_id=receiver_id,
        amount=_generate_normal_amount(),
        timestamp=_now(now),
        is_synthetic_attack=False,
        commit=commit,
    )


def _generate_fan_in_fan_out_attack(
    db: Session,
    window_seconds: float,
    *,
    now: datetime | None = None,
    commit: bool = True,
) -> list[Transaction]:
    pool = _ensure_pool(db, created_at=now)
    mule_id = random.choice(pool)

    fan_in_count = random.randint(8, 10)
    fan_out_count = random.randint(8, 10)

    available_senders = [account_id for account_id in pool if account_id != mule_id]
    if len(available_senders) < fan_in_count:
        raise ValueError("Not enough accounts in pool for fan-in leg")

    fan_in_senders = random.sample(available_senders, fan_in_count)
    base_time = _now(now)
    fan_in_timestamps = _clustered_timestamps(fan_in_count, window_seconds, base_time)

    transactions: list[Transaction] = []
    fan_in_amounts = [_generate_smurf_amount() for _ in range(fan_in_count)]

    for sender_id, amount, timestamp in zip(
        fan_in_senders, fan_in_amounts, fan_in_timestamps
    ):
        transactions.append(
            _persist_transaction(
                db=db,
                sender_id=sender_id,
                receiver_id=mule_id,
                amount=amount,
                timestamp=timestamp,
                is_synthetic_attack=True,
                commit=commit,
                attack_variant=None,
            )
        )

    total_in = sum(fan_in_amounts)
    skim_pct = random.uniform(0.02, 0.05)
    total_out = round(total_in * (1 - skim_pct), 2)
    fan_out_amounts = _distribute_amounts(total_out, fan_out_count)

    fan_out_start = fan_in_timestamps[-1] + timedelta(seconds=random.uniform(5, 30))
    fan_out_timestamps = _clustered_timestamps(
        fan_out_count, window_seconds * 0.4, fan_out_start
    )

    for amount, timestamp in zip(fan_out_amounts, fan_out_timestamps):
        receiver_id = _create_fresh_account(db, timestamp)
        transactions.append(
            _persist_transaction(
                db=db,
                sender_id=mule_id,
                receiver_id=receiver_id,
                amount=amount,
                timestamp=timestamp,
                is_synthetic_attack=True,
                commit=commit,
            )
        )

    return transactions


def generate_mule_attack(
    db: Session, *, now: datetime | None = None, commit: bool = True
) -> list[Transaction]:
    window_seconds = random.uniform(120, 180)
    return _generate_fan_in_fan_out_attack(
        db, window_seconds, now=now, commit=commit
    )


def generate_slow_drip_attack(
    db: Session, *, now: datetime | None = None, commit: bool = True
) -> list[Transaction]:
    window_seconds = random.uniform(720, 900)
    return _generate_fan_in_fan_out_attack(
        db, window_seconds, now=now, commit=commit
    )


def generate_offline_trace(
    db: Session,
    *,
    n_steps: int,
    seed: int,
    start_time: datetime,
    interval_seconds: float = SIMULATION_INTERVAL_SECONDS,
    catchup: str = "none",
    include_attacks: bool = True,
) -> dict[str, int]:
    """Run n_steps of the sim with a virtual clock (no sleep, no websocket).

    catchup:
      none     — stop after n_steps (live loop body, no extra events)
      normals  — inject only legitimate txs until clock >= max(timestamp)
      organic  — keep running the same roll as run_simulation until clock
                 >= max(timestamp) (live-equivalent continuation)

    include_attacks: if False, every step is a legitimate transfer (background
    for overlaying labeled adversarial variants).
    """
    set_simulation_seed(seed)
    seed_accounts(db, created_at=start_time)

    n_normal = 0
    n_fast = 0
    n_slow = 0
    n_tx = 0
    n_catchup = 0
    clock = start_time

    def _step(at: datetime) -> int:
        nonlocal n_normal, n_fast, n_slow
        if not include_attacks:
            txs = [generate_normal_transaction(db, now=at, commit=False)]
            n_normal += 1
            db.commit()
            return len(txs)
        roll = random.random()
        if roll < 1.0 - MULE_ATTACK_PROBABILITY - SLOW_DRIP_ATTACK_PROBABILITY:
            txs = [generate_normal_transaction(db, now=at, commit=False)]
            n_normal += 1
        elif roll < 1.0 - SLOW_DRIP_ATTACK_PROBABILITY:
            txs = generate_mule_attack(db, now=at, commit=False)
            n_fast += 1
        else:
            txs = generate_slow_drip_attack(db, now=at, commit=False)
            n_slow += 1
        db.commit()
        return len(txs)

    for _ in range(n_steps):
        n_tx += _step(clock)
        clock = clock + timedelta(seconds=interval_seconds)

    if catchup not in {"none", "normals", "organic"}:
        raise ValueError(f"unknown catchup mode: {catchup}")

    if catchup != "none":
        from sqlalchemy import func, select

        while True:
            max_ts = db.scalar(select(func.max(Transaction.timestamp)))
            if max_ts is None or clock >= max_ts:
                break
            if catchup == "normals":
                generate_normal_transaction(db, now=clock, commit=False)
                db.commit()
                n_tx += 1
                n_normal += 1
            else:
                n_tx += _step(clock)
            n_catchup += 1
            clock = clock + timedelta(seconds=interval_seconds)

    return {
        "steps": n_steps,
        "catchup_steps": n_catchup,
        "catchup_mode": catchup,
        "transactions": n_tx,
        "normal_events": n_normal,
        "fast_attack_events": n_fast,
        "slow_drip_events": n_slow,
        "last_clock": clock.isoformat(),
    }


async def run_simulation(
    interval_seconds: float = SIMULATION_INTERVAL_SECONDS,
    *,
    seed: int | None = None,
) -> AsyncIterator[Transaction]:
    if seed is not None:
        set_simulation_seed(seed)

    db = SessionLocal()
    try:
        seed_accounts(db)
    finally:
        db.close()

    while True:
        db = SessionLocal()
        try:
            roll = random.random()
            if roll < 1.0 - MULE_ATTACK_PROBABILITY - SLOW_DRIP_ATTACK_PROBABILITY:
                transactions = [generate_normal_transaction(db)]
            elif roll < 1.0 - SLOW_DRIP_ATTACK_PROBABILITY:
                transactions = generate_mule_attack(db)
            else:
                transactions = generate_slow_drip_attack(db)

            for tx in transactions:
                if live_feed_manager is not None and build_transaction_message is not None:
                    await live_feed_manager.broadcast(build_transaction_message(tx))
                yield tx
        finally:
            db.close()

        await asyncio.sleep(interval_seconds)


async def _run_standalone() -> None:
    async for tx in run_simulation():
        attack_flag = "ATTACK" if tx.is_synthetic_attack else "normal"
        print(
            f"[{attack_flag}] tx#{tx.id} {tx.sender_id} -> {tx.receiver_id} "
            f"₹{tx.amount:,.2f} @ {tx.timestamp}"
        )


if __name__ == "__main__":
    asyncio.run(_run_standalone())
