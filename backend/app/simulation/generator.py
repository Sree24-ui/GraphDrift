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


def seed_accounts(db: Session) -> list[str]:
    global _account_pool

    existing_ids = list(db.scalars(select(Account.id)).all())
    if existing_ids:
        _account_pool = existing_ids
        return _account_pool

    now = datetime.now()
    seen: set[str] = set()
    while len(seen) < POOL_SIZE:
        seen.add(_generate_upi_id())

    for account_id in seen:
        db.add(
            Account(
                id=account_id,
                created_at=now,
                last_active_at=now,
            )
        )

    db.commit()
    _account_pool = list(seen)
    return _account_pool


def _ensure_pool(db: Session) -> list[str]:
    if not _account_pool:
        return seed_accounts(db)
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
) -> Transaction:
    tx = Transaction(
        sender_id=sender_id,
        receiver_id=receiver_id,
        amount=amount,
        timestamp=timestamp,
        is_synthetic_attack=is_synthetic_attack,
    )
    db.add(tx)

    for account_id in (sender_id, receiver_id):
        account = db.get(Account, account_id)
        if account is not None and account.last_active_at < timestamp:
            account.last_active_at = timestamp

    db.commit()
    db.refresh(tx)
    return tx


def generate_normal_transaction(db: Session) -> Transaction:
    pool = _ensure_pool(db)
    sender_id, receiver_id = random.sample(pool, 2)
    return _persist_transaction(
        db=db,
        sender_id=sender_id,
        receiver_id=receiver_id,
        amount=_generate_normal_amount(),
        timestamp=datetime.now(),
        is_synthetic_attack=False,
    )


def _generate_fan_in_fan_out_attack(
    db: Session,
    window_seconds: float,
) -> list[Transaction]:
    pool = _ensure_pool(db)
    mule_id = random.choice(pool)

    fan_in_count = random.randint(8, 10)
    fan_out_count = random.randint(8, 10)

    available_senders = [account_id for account_id in pool if account_id != mule_id]
    if len(available_senders) < fan_in_count:
        raise ValueError("Not enough accounts in pool for fan-in leg")

    fan_in_senders = random.sample(available_senders, fan_in_count)
    base_time = datetime.now()
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
            )
        )

    return transactions


def generate_mule_attack(db: Session) -> list[Transaction]:
    window_seconds = random.uniform(120, 180)
    return _generate_fan_in_fan_out_attack(db, window_seconds)


def generate_slow_drip_attack(db: Session) -> list[Transaction]:
    window_seconds = random.uniform(720, 900)
    return _generate_fan_in_fan_out_attack(db, window_seconds)


async def run_simulation(
    interval_seconds: float = SIMULATION_INTERVAL_SECONDS,
) -> AsyncIterator[Transaction]:
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
