import math
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.constants import (  # noqa: F401 — re-exported
    AMOUNT_ENTROPY_BINS,
    MIN_TRANSACTIONS_FOR_SCORING,
    WINDOW_MINUTES,
)
from app.models import Transaction


def _window_bounds(
    as_of: datetime, window_minutes: int
) -> tuple[datetime, datetime]:
    return as_of - timedelta(minutes=window_minutes), as_of


def get_active_accounts(
    db: Session,
    as_of: datetime,
    window_minutes: int = WINDOW_MINUTES,
) -> list[str]:
    window_start, window_end = _window_bounds(as_of, window_minutes)

    stmt = (
        select(Transaction.sender_id, Transaction.receiver_id)
        .where(
            and_(
                Transaction.timestamp >= window_start,
                Transaction.timestamp <= window_end,
            )
        )
    )
    rows = db.execute(stmt).all()

    active: set[str] = set()
    for sender_id, receiver_id in rows:
        active.add(sender_id)
        active.add(receiver_id)

    return sorted(active)


def _get_account_transactions(
    db: Session,
    account_id: str,
    window_start: datetime,
    window_end: datetime,
) -> list[Transaction]:
    stmt = (
        select(Transaction)
        .where(
            and_(
                Transaction.timestamp >= window_start,
                Transaction.timestamp <= window_end,
                or_(
                    Transaction.sender_id == account_id,
                    Transaction.receiver_id == account_id,
                ),
            )
        )
        .order_by(Transaction.timestamp.asc(), Transaction.id.asc())
    )
    return list(db.scalars(stmt).all())


def _amount_entropy(amounts: list[float], bins: int = AMOUNT_ENTROPY_BINS) -> float:
    """
    Shannon entropy of transaction amount distribution.

    Lower entropy means amounts cluster in fewer bins — a signal for mule/smurf
    activity where payments are suspiciously uniform (e.g. many ~₹45k transfers).
    Higher entropy indicates more varied amounts typical of normal spending.
    """
    if not amounts:
        return 0.0

    if len(amounts) == 1:
        return 0.0

    counts, _ = np.histogram(amounts, bins=bins)
    total = counts.sum()
    if total == 0:
        return 0.0

    probabilities = counts[counts > 0] / total
    return float(-np.sum(probabilities * np.log2(probabilities)))


def _burstiness(timestamps: list[datetime]) -> float:
    if len(timestamps) < 2:
        return 0.0

    gaps = [
        (timestamps[i] - timestamps[i - 1]).total_seconds()
        for i in range(1, len(timestamps))
    ]
    mean_gap = float(np.mean(gaps))
    if mean_gap == 0.0:
        return 0.0

    std_gap = float(np.std(gaps))
    return std_gap / mean_gap


def _features_from_transactions(
    account_id: str,
    transactions: list[Transaction],
    window_minutes: int,
    *,
    min_transactions: int = MIN_TRANSACTIONS_FOR_SCORING,
) -> dict | None:
    if not transactions:
        return None

    total_count = len(transactions)
    if total_count < min_transactions:
        return None

    inbound = [tx for tx in transactions if tx.receiver_id == account_id]
    outbound = [tx for tx in transactions if tx.sender_id == account_id]

    in_count = len(inbound)
    out_count = len(outbound)

    in_degree = len({tx.sender_id for tx in inbound})
    out_degree = len({tx.receiver_id for tx in outbound})

    velocity = total_count / window_minutes

    amounts = [tx.amount for tx in transactions]
    amount_entropy = _amount_entropy(amounts)

    counterparties: set[str] = set()
    for tx in inbound:
        counterparties.add(tx.sender_id)
    for tx in outbound:
        counterparties.add(tx.receiver_id)

    counterparty_diversity = len(counterparties) / total_count
    fan_ratio = in_degree / (out_degree + 1)
    burstiness = _burstiness([tx.timestamp for tx in transactions])

    return {
        "account_id": account_id,
        "in_degree": in_degree,
        "out_degree": out_degree,
        "in_count": in_count,
        "out_count": out_count,
        "velocity": velocity,
        "amount_entropy": amount_entropy,
        "counterparty_diversity": counterparty_diversity,
        "fan_ratio": fan_ratio,
        "burstiness": burstiness,
    }


def extract_features(
    db: Session,
    account_id: str,
    as_of: datetime,
    window_minutes: int = WINDOW_MINUTES,
    *,
    min_transactions: int = MIN_TRANSACTIONS_FOR_SCORING,
) -> dict | None:
    window_start, window_end = _window_bounds(as_of, window_minutes)
    transactions = _get_account_transactions(db, account_id, window_start, window_end)
    return _features_from_transactions(
        account_id,
        transactions,
        window_minutes,
        min_transactions=min_transactions,
    )


def extract_all_features(
    db: Session,
    as_of: datetime,
    window_minutes: int = WINDOW_MINUTES,
    *,
    min_transactions: int = MIN_TRANSACTIONS_FOR_SCORING,
) -> list[dict]:
    """Score every active account in the window.

    Loads the window once instead of issuing one query per account (required
    for dense IBM-AML slices with 10^5 accounts).
    """
    window_start, window_end = _window_bounds(as_of, window_minutes)
    stmt = (
        select(Transaction)
        .where(
            and_(
                Transaction.timestamp >= window_start,
                Transaction.timestamp <= window_end,
            )
        )
        .order_by(Transaction.timestamp.asc(), Transaction.id.asc())
    )
    rows = list(db.scalars(stmt).all())

    by_account: dict[str, list[Transaction]] = {}
    for tx in rows:
        by_account.setdefault(tx.sender_id, []).append(tx)
        if tx.receiver_id != tx.sender_id:
            by_account.setdefault(tx.receiver_id, []).append(tx)

    features: list[dict] = []
    for account_id, transactions in by_account.items():
        account_features = _features_from_transactions(
            account_id,
            transactions,
            window_minutes,
            min_transactions=min_transactions,
        )
        if account_features is not None:
            features.append(account_features)
    return features


def _print_feature_table(features: list[dict]) -> None:
    if not features:
        print("No accounts met the minimum transaction threshold in the window.")
        return

    sorted_features = sorted(features, key=lambda row: row["velocity"], reverse=True)

    columns = [
        "account_id",
        "in_degree",
        "out_degree",
        "in_count",
        "out_count",
        "velocity",
        "amount_entropy",
        "counterparty_diversity",
        "fan_ratio",
        "burstiness",
    ]

    widths = {col: max(len(col), *(len(str(row[col])) for row in sorted_features)) for col in columns}

    header = " | ".join(col.ljust(widths[col]) for col in columns)
    separator = "-+-".join("-" * widths[col] for col in columns)
    print(header)
    print(separator)

    for row in sorted_features:
        values = []
        for col in columns:
            value = row[col]
            if isinstance(value, float):
                text = f"{value:.4f}"
            else:
                text = str(value)
            values.append(text.ljust(widths[col]))
        print(" | ".join(values))


if __name__ == "__main__":
    from datetime import datetime

    from app.db import SessionLocal

    db = SessionLocal()
    try:
        as_of = datetime.now()
        features = extract_all_features(db, as_of)
        print(f"Feature extraction as_of={as_of.isoformat()} window={WINDOW_MINUTES}m")
        print(f"Accounts scored: {len(features)}")
        print()
        _print_feature_table(features)
    finally:
        db.close()
