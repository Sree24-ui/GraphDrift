from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('analyst', 'admin')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    reviewed_alerts: Mapped[list["Alert"]] = relationship(
        "Alert", back_populates="reviewed_by"
    )
    ring_review_actions: Mapped[list["RingReviewAction"]] = relationship(
        "RingReviewAction", back_populates="reviewed_by"
    )


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    sent_transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction",
        foreign_keys="Transaction.sender_id",
        back_populates="sender",
    )
    received_transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction",
        foreign_keys="Transaction.receiver_id",
        back_populates="receiver",
    )
    alerts: Mapped[list["Alert"]] = relationship("Alert", back_populates="account")
    score_history: Mapped[list["AccountScoreHistory"]] = relationship(
        "AccountScoreHistory", back_populates="account"
    )


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_timestamp", "timestamp"),
        Index("ix_transactions_sender_id", "sender_id"),
        Index("ix_transactions_receiver_id", "receiver_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sender_id: Mapped[str] = mapped_column(
        String, ForeignKey("accounts.id"), nullable=False
    )
    receiver_id: Mapped[str] = mapped_column(
        String, ForeignKey("accounts.id"), nullable=False
    )
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_synthetic_attack: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    is_labeled_fraud: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    attack_variant: Mapped[str | None] = mapped_column(String, nullable=True)

    sender: Mapped["Account"] = relationship(
        "Account", foreign_keys=[sender_id], back_populates="sent_transactions"
    )
    receiver: Mapped["Account"] = relationship(
        "Account", foreign_keys=[receiver_id], back_populates="received_transactions"
    )


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_status", "status"),
        Index("ix_alerts_ring_id", "ring_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(
        String, ForeignKey("accounts.id"), nullable=False
    )
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    pattern_type: Mapped[str] = mapped_column(String, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(
        String, default="new", server_default="new", nullable=False
    )
    confidence: Mapped[str] = mapped_column(
        String, default="low", server_default="low", nullable=False
    )
    ring_id: Mapped[str | None] = mapped_column(String, nullable=True)
    analyst_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    feature_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewed_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )

    account: Mapped["Account"] = relationship("Account", back_populates="alerts")
    reviewed_by: Mapped["User | None"] = relationship(
        "User", back_populates="reviewed_alerts"
    )


class RingReviewAction(Base):
    """Immutable audit record for a bulk ring status change."""

    __tablename__ = "ring_review_actions"
    __table_args__ = (Index("ix_ring_review_actions_ring_id", "ring_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ring_id: Mapped[str] = mapped_column(String, nullable=False)
    target_status: Mapped[str] = mapped_column(String, nullable=False)
    analyst_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, index=True
    )

    reviewed_by: Mapped["User"] = relationship(
        "User", back_populates="ring_review_actions"
    )


class AccountScoreHistory(Base):
    __tablename__ = "account_score_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(
        String, ForeignKey("accounts.id"), nullable=False
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    account: Mapped["Account"] = relationship("Account", back_populates="score_history")
