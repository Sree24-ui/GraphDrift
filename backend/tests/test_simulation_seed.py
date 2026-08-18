"""Seeded simulator: same seed produces the same offline trace."""

from __future__ import annotations

from datetime import datetime

from app.simulation.generator import generate_offline_trace, set_simulation_seed
from evaluation.db import init_eval_db


def test_same_seed_reproduces_trace(tmp_path):
    start = datetime(2026, 1, 1, 12, 0, 0)
    paths = [tmp_path / "a.db", tmp_path / "b.db"]
    stats = []
    first_pairs = []
    for path in paths:
        factory = init_eval_db(path)
        db = factory()
        try:
            stats.append(
                generate_offline_trace(db, n_steps=40, seed=42, start_time=start, catchup="none")
            )
            from app.models import Transaction
            from sqlalchemy import select

            rows = db.execute(
                select(
                    Transaction.sender_id,
                    Transaction.receiver_id,
                    Transaction.amount,
                    Transaction.is_synthetic_attack,
                ).order_by(Transaction.id)
            ).all()
            first_pairs.append(rows)
        finally:
            db.close()
        set_simulation_seed(None)

    assert stats[0] == stats[1]
    assert first_pairs[0] == first_pairs[1]


def test_different_seeds_diverge(tmp_path):
    start = datetime(2026, 1, 1, 12, 0, 0)
    counts = []
    for seed, name in ((42, "x.db"), (99, "y.db")):
        factory = init_eval_db(tmp_path / name)
        db = factory()
        try:
            stats = generate_offline_trace(db, n_steps=80, seed=seed, start_time=start, catchup="none")
            counts.append((stats["fast_attack_events"], stats["slow_drip_events"], stats["transactions"]))
        finally:
            db.close()
    assert counts[0] != counts[1]
