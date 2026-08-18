#!/usr/bin/env python3
"""
Generate 5 seeded synthetic snapshots matched to graphdrift_snapshot_2026-08-12.db.

Reference snapshot: 7,074 txs / 1,060 accounts / 185.0 simulated minutes
(2s loop interval → 5,551 steps).
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.detection.features import WINDOW_MINUTES  # noqa: E402
from app.models import Account, Transaction  # noqa: E402
from app.simulation.constants import SIMULATION_INTERVAL_SECONDS  # noqa: E402
from app.simulation.generator import generate_offline_trace, set_simulation_seed  # noqa: E402
from evaluation.db import init_eval_db  # noqa: E402
from evaluation.snapshot_analysis import classify_attack_types  # noqa: E402

SNAPSHOT_DIR = BACKEND_ROOT / "snapshots"
SEEDS = (42, 123, 7, 2026, 99)
# Match graphdrift_snapshot_2026-08-12.db duration (185.034 min at 2s/loop).
N_STEPS = 5551
START_TIME = datetime(2026, 8, 12, 16, 40, 48)
MIN_FAST_WINDOW = 5
MIN_SLOW_WINDOW = 1


def _summarize(db) -> dict:
    n_tx = db.scalar(select(func.count()).select_from(Transaction))
    n_acct = db.scalar(select(func.count()).select_from(Account))
    tmin = db.scalar(select(func.min(Transaction.timestamp)))
    tmax = db.scalar(select(func.max(Transaction.timestamp)))
    span_min = (tmax - tmin).total_seconds() / 60.0 if tmin and tmax else 0.0
    all_s, fast, slow = classify_attack_types(db, tmax, window_minutes=WINDOW_MINUTES)
    return {
        "transactions": int(n_tx),
        "accounts": int(n_acct),
        "span_minutes": span_min,
        "window_fast_accounts": len(fast),
        "window_slow_accounts": len(slow),
        "window_synth_accounts": len(all_s),
        "as_of": tmax.isoformat() if tmax else None,
    }


def generate_one(seed: int, dest: Path) -> tuple[dict, dict]:
    if dest.exists():
        dest.unlink()
    factory = init_eval_db(dest)
    db = factory()
    try:
        gen_stats = generate_offline_trace(
            db,
            n_steps=N_STEPS,
            seed=seed,
            start_time=START_TIME,
            interval_seconds=SIMULATION_INTERVAL_SECONDS,
            catchup="normals",
        )
        summary = _summarize(db)
        summary.update(gen_stats)
        summary["seed"] = seed
        summary["path"] = str(dest)
        return gen_stats, summary
    finally:
        db.close()
        set_simulation_seed(None)


def is_degenerate(summary: dict) -> bool:
    return (
        summary["window_fast_accounts"] < MIN_FAST_WINDOW
        or summary["window_slow_accounts"] < MIN_SLOW_WINDOW
    )


def main() -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    print(
        f"Generating {len(SEEDS)} snapshots: {N_STEPS} steps "
        f"({N_STEPS * SIMULATION_INTERVAL_SECONDS / 60:.1f} min of sim time)\n"
    )
    print(
        f"{'seed':>6} {'tx':>7} {'accts':>6} {'span_m':>7} "
        f"{'fast_ev':>8} {'slow_ev':>8} {'win_fast':>8} {'win_slow':>8} {'ok':>4}"
    )
    rows = []
    for seed in SEEDS:
        dest = SNAPSHOT_DIR / f"multiseed_seed{seed}.db"
        used_seed = seed
        _, summary = generate_one(used_seed, dest)
        if is_degenerate(summary):
            fallback = seed + 10_000
            print(
                f"{seed:>6} DEGENERATE (win fast={summary['window_fast_accounts']} "
                f"slow={summary['window_slow_accounts']}) → retry seed={fallback}",
                flush=True,
            )
            dest = SNAPSHOT_DIR / f"multiseed_seed{seed}.db"
            used_seed = fallback
            _, summary = generate_one(used_seed, dest)
            summary["requested_seed"] = seed
            summary["seed"] = used_seed
        ok = "ok" if not is_degenerate(summary) else "BAD"
        print(
            f"{seed:>6} {summary['transactions']:7d} {summary['accounts']:6d} "
            f"{summary['span_minutes']:7.1f} {summary['fast_attack_events']:8d} "
            f"{summary['slow_drip_events']:8d} {summary['window_fast_accounts']:8d} "
            f"{summary['window_slow_accounts']:8d} {ok:>4}"
            f"  catchup={summary.get('catchup_steps', 0)}",
            flush=True,
        )
        rows.append(summary)

    print("\nReference graphdrift_snapshot_2026-08-12.db: "
          "7074 tx / 1060 accounts / 185.0 min / window fast=90 slow=1")
    if any(is_degenerate(r) for r in rows):
        raise SystemExit("One or more snapshots still lack both attack types in the 15m window.")


if __name__ == "__main__":
    main()
