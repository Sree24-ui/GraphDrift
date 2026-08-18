"""Adversarial attack generators: labels, straddle timing, dilution shape."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from app.models import Transaction
from app.simulation.adversarial_attacks import (
    generate_diluted_hub_attack,
    generate_minimal_ring_attack,
    generate_standard_attack,
    generate_straddle_attack,
)
from app.simulation.generator import seed_accounts, set_simulation_seed
from evaluation.db import init_eval_db


def test_standard_and_minimal_labels(tmp_path):
    factory = init_eval_db(tmp_path / "adv.db")
    db = factory()
    try:
        set_simulation_seed(1)
        seed_accounts(db, created_at=datetime(2026, 1, 1, 12, 0, 0))
        start = datetime(2026, 1, 1, 12, 5, 0)
        std = generate_standard_attack(db, start=start, instance_id="standard:0")
        mini = generate_minimal_ring_attack(db, start=start + timedelta(minutes=1))
        db.commit()
        assert std["variant"] == "standard"
        assert mini["variant"] == "minimal_ring"
        assert len(std["hubs"]) == 1
        variants = set(db.scalars(select(Transaction.attack_variant)).all())
        assert variants == {"standard", "minimal_ring"}
        mini_txs = list(
            db.scalars(
                select(Transaction).where(Transaction.attack_variant == "minimal_ring")
            )
        )
        mule = mini["hubs"][0]
        n_in = sum(1 for t in mini_txs if t.receiver_id == mule)
        n_out = sum(1 for t in mini_txs if t.sender_id == mule)
        assert n_in == 4 and n_out == 4
    finally:
        db.close()
        set_simulation_seed(None)


def test_straddle_splits_boundary(tmp_path):
    factory = init_eval_db(tmp_path / "straddle.db")
    db = factory()
    try:
        set_simulation_seed(2)
        seed_accounts(db, created_at=datetime(2026, 1, 1, 12, 0, 0))
        boundary = datetime(2026, 1, 1, 13, 0, 0)
        inst = generate_straddle_attack(
            db, boundary=boundary, window_minutes=15, instance_id="s15:0"
        )
        db.commit()
        assert inst["variant"] == "straddle_15"
        txs = list(
            db.scalars(
                select(Transaction).where(Transaction.attack_variant == "straddle_15")
            )
        )
        before = [t for t in txs if t.timestamp < boundary]
        after = [t for t in txs if t.timestamp > boundary]
        assert len(before) >= 7 and len(after) >= 7
        assert all(t.timestamp != boundary for t in txs)
        mule = inst["hubs"][0]
        assert any(t.receiver_id == mule and t.timestamp < boundary for t in txs)
        assert any(t.receiver_id == mule and t.timestamp > boundary for t in txs)
        assert any(t.sender_id == mule and t.timestamp < boundary for t in txs)
        assert any(t.sender_id == mule and t.timestamp > boundary for t in txs)
    finally:
        db.close()
        set_simulation_seed(None)


def test_diluted_hub_has_multiple_mules(tmp_path):
    factory = init_eval_db(tmp_path / "dilute.db")
    db = factory()
    try:
        set_simulation_seed(3)
        seed_accounts(db, created_at=datetime(2026, 1, 1, 12, 0, 0))
        inst = generate_diluted_hub_attack(
            db, start=datetime(2026, 1, 1, 12, 10, 0), n_mules=3
        )
        db.commit()
        assert inst["variant"] == "diluted_hub"
        assert len(inst["hubs"]) == 3
        txs = list(
            db.scalars(
                select(Transaction).where(Transaction.attack_variant == "diluted_hub")
            )
        )
        in_counts = {h: 0 for h in inst["hubs"]}
        for t in txs:
            if t.receiver_id in in_counts and t.sender_id not in inst["hubs"]:
                in_counts[t.receiver_id] += 1
        assert all(c == 3 for c in in_counts.values())
    finally:
        db.close()
        set_simulation_seed(None)
