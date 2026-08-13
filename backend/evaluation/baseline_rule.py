"""
Fixed-threshold rule-based mule detector (no ML).

Flags accounts in a rolling window when ALL of:
  - in-degree  >= MIN_IN_DEGREE
  - out-degree >= MIN_OUT_DEGREE
  - total inbound amount within BALANCE_TOLERANCE of total outbound amount
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.detection.features import WINDOW_MINUTES
from app.models import Transaction

MIN_IN_DEGREE = 5
MIN_OUT_DEGREE = 5
BALANCE_TOLERANCE = 0.20  # within 20%


def _window_bounds(
    as_of: datetime, window_minutes: int
) -> tuple[datetime, datetime]:
    return as_of - timedelta(minutes=window_minutes), as_of


def detect_baseline_accounts(
    db: Session,
    as_of: datetime,
    *,
    window_minutes: int = WINDOW_MINUTES,
    min_in_degree: int = MIN_IN_DEGREE,
    min_out_degree: int = MIN_OUT_DEGREE,
    balance_tolerance: float = BALANCE_TOLERANCE,
) -> set[str]:
    window_start, window_end = _window_bounds(as_of, window_minutes)

    rows = db.execute(
        select(
            Transaction.sender_id,
            Transaction.receiver_id,
            Transaction.amount,
        ).where(
            and_(
                Transaction.timestamp >= window_start,
                Transaction.timestamp <= window_end,
            )
        )
    ).all()

    inbound_degree: dict[str, set[str]] = {}
    outbound_degree: dict[str, set[str]] = {}
    inbound_amount: dict[str, float] = {}
    outbound_amount: dict[str, float] = {}

    for sender_id, receiver_id, amount in rows:
        amt = float(amount)

        outbound_degree.setdefault(sender_id, set()).add(receiver_id)
        outbound_amount[sender_id] = outbound_amount.get(sender_id, 0.0) + amt

        inbound_degree.setdefault(receiver_id, set()).add(sender_id)
        inbound_amount[receiver_id] = inbound_amount.get(receiver_id, 0.0) + amt

    flagged: set[str] = set()
    candidates = set(inbound_degree) | set(outbound_degree)

    for account_id in candidates:
        in_deg = len(inbound_degree.get(account_id, set()))
        out_deg = len(outbound_degree.get(account_id, set()))
        if in_deg < min_in_degree or out_deg < min_out_degree:
            continue

        total_in = inbound_amount.get(account_id, 0.0)
        total_out = outbound_amount.get(account_id, 0.0)
        denom = max(total_in, total_out, 1.0)
        if abs(total_in - total_out) / denom <= balance_tolerance:
            flagged.add(account_id)

    return flagged
