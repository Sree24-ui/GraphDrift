import math
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import (
    AccountDetail,
    ConfidenceLevel,
    PaginationMeta,
    ScoreHistoryPoint,
    ScoreHistoryResponse,
    TransactionItem,
)
from app.detection.features import WINDOW_MINUTES
from app.detection.fusion import compute_fused_scores
from app.models import Account, AccountScoreHistory, Transaction

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def _fused_for_account(
    db: Session, account_id: str, as_of: datetime
) -> tuple[float | None, ConfidenceLevel | None]:
    for row in compute_fused_scores(db, as_of):
        if row["account_id"] == account_id:
            return float(row["fused_score"]), row["confidence"]  # type: ignore[return-value]
    return None, None


def _connected_accounts(db: Session, account_id: str, as_of: datetime) -> list[str]:
    window_start = as_of - timedelta(minutes=WINDOW_MINUTES)
    rows = db.execute(
        select(Transaction.sender_id, Transaction.receiver_id).where(
            and_(
                Transaction.timestamp >= window_start,
                Transaction.timestamp <= as_of,
                or_(
                    Transaction.sender_id == account_id,
                    Transaction.receiver_id == account_id,
                ),
            )
        )
    ).all()

    counterparties: set[str] = set()
    for sender_id, receiver_id in rows:
        if sender_id == account_id:
            counterparties.add(receiver_id)
        else:
            counterparties.add(sender_id)
    return sorted(counterparties)


@router.get("/{account_id}", response_model=AccountDetail)
def get_account(
    account_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> AccountDetail:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    as_of = datetime.now()

    base_filter = or_(
        Transaction.sender_id == account_id,
        Transaction.receiver_id == account_id,
    )
    total = db.scalar(
        select(func.count()).select_from(Transaction).where(base_filter)
    ) or 0
    offset = (page - 1) * page_size

    txs = db.scalars(
        select(Transaction)
        .where(base_filter)
        .order_by(Transaction.timestamp.desc())
        .offset(offset)
        .limit(page_size)
    ).all()

    transactions: list[TransactionItem] = []
    for tx in txs:
        if tx.sender_id == account_id:
            direction = "sent"
            counterparty_id = tx.receiver_id
        else:
            direction = "received"
            counterparty_id = tx.sender_id

        transactions.append(
            TransactionItem(
                id=tx.id,
                direction=direction,  # type: ignore[arg-type]
                counterparty_id=counterparty_id,
                amount=tx.amount,
                timestamp=tx.timestamp,
                is_synthetic_attack=tx.is_synthetic_attack,
            )
        )

    fused_score, confidence = _fused_for_account(db, account_id, as_of)
    total_pages = math.ceil(total / page_size) if total else 0

    return AccountDetail(
        account_id=account.id,
        created_at=account.created_at,
        last_active_at=account.last_active_at,
        fused_score=fused_score,
        confidence=confidence,
        transactions=transactions,
        connected_accounts=_connected_accounts(db, account_id, as_of),
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        ),
    )


@router.get("/{account_id}/score-history", response_model=ScoreHistoryResponse)
def get_score_history(
    account_id: str,
    db: Session = Depends(get_db),
) -> ScoreHistoryResponse:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    rows = db.scalars(
        select(AccountScoreHistory)
        .where(AccountScoreHistory.account_id == account_id)
        .order_by(AccountScoreHistory.recorded_at.asc())
    ).all()

    return ScoreHistoryResponse(
        account_id=account_id,
        points=[
            ScoreHistoryPoint(recorded_at=row.recorded_at, score=row.score)
            for row in rows
        ],
    )
