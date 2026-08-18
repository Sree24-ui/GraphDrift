#!/usr/bin/env python3
"""A/B catch-up vs last-clock eval, plus a freeze_snapshot-style 6th trace."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.detection.features import (  # noqa: E402
    MIN_TRANSACTIONS_FOR_SCORING,
    WINDOW_MINUTES,
    extract_all_features,
    get_active_accounts,
)
from app.models import Account, Transaction  # noqa: E402
from app.simulation.constants import SIMULATION_INTERVAL_SECONDS  # noqa: E402
from app.simulation.generator import generate_offline_trace, set_simulation_seed  # noqa: E402
from evaluation.db import init_eval_db  # noqa: E402
from evaluation.freeze_snapshot import freeze_snapshot  # noqa: E402
from evaluation.generate_multi_seed_snapshots import N_STEPS, START_TIME, SNAPSHOT_DIR  # noqa: E402
from evaluation.metrics import compute_metrics  # noqa: E402
from evaluation.run_eval import ground_truth_accounts, predict_fusion  # noqa: E402
from evaluation.snapshot_analysis import classify_attack_types  # noqa: E402


def _window_mix(db, as_of: datetime) -> dict:
    w0 = as_of - timedelta(minutes=WINDOW_MINUTES)
    rows = db.execute(
        select(Transaction.is_synthetic_attack).where(
            Transaction.timestamp >= w0,
            Transaction.timestamp <= as_of,
        )
    ).all()
    n = len(rows)
    ns = sum(1 for (flag,) in rows if flag)
    scored = extract_all_features(
        db, as_of, WINDOW_MINUTES, min_transactions=MIN_TRANSACTIONS_FOR_SCORING
    )
    active = get_active_accounts(db, as_of, WINDOW_MINUTES)
    all_s, fast, slow = classify_attack_types(db, as_of, window_minutes=WINDOW_MINUTES)
    universe = {row["account_id"] for row in scored}
    positives = ground_truth_accounts(db, as_of, window_minutes=WINDOW_MINUTES) & universe
    pred = predict_fusion(
        db,
        as_of,
        window_minutes=WINDOW_MINUTES,
        min_transactions=MIN_TRANSACTIONS_FOR_SCORING,
    )
    metrics = compute_metrics(positives, pred, universe)
    return {
        "as_of": as_of.isoformat(),
        "window_txs": n,
        "window_synth": ns,
        "window_legit": n - ns,
        "active": len(active),
        "scored": len(universe),
        "fraud_scored": len(positives),
        "fast": len(fast),
        "slow": len(slow),
        "fusion_f1": metrics.f1,
        "fusion_p": metrics.precision,
        "fusion_r": metrics.recall,
        "tp": metrics.tp,
        "fp": metrics.fp,
        "fn": metrics.fn,
        "tn": metrics.tn,
    }


def _print(label: str, stats: dict) -> None:
    print(f"\n=== {label} ===")
    for k, v in stats.items():
        if isinstance(v, float) and k.startswith("fusion"):
            print(f"  {k}: {v:.3f}")
        else:
            print(f"  {k}: {v}")


def generate(path: Path, *, catchup: str) -> dict:
    if path.exists():
        path.unlink()
    db = init_eval_db(path)()
    try:
        gen = generate_offline_trace(
            db,
            n_steps=N_STEPS,
            seed=42,
            start_time=START_TIME,
            interval_seconds=SIMULATION_INTERVAL_SECONDS,
            catchup=catchup,
        )
        last_clock = datetime.fromisoformat(gen["last_clock"])
        tmax = db.scalar(select(func.max(Transaction.timestamp)))
        n_acct = db.scalar(select(func.count()).select_from(Account))
        n_tx = db.scalar(select(func.count()).select_from(Transaction))
        return {
            "gen": gen,
            "last_clock": last_clock,
            "tmax": tmax,
            "n_tx": n_tx,
            "n_acct": n_acct,
            "db": db,
            "path": path,
        }
    except Exception:
        db.close()
        set_simulation_seed(None)
        raise


def main() -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    orig = init_eval_db(SNAPSHOT_DIR / "graphdrift_snapshot_2026-08-12.db")()
    try:
        tmax = orig.scalar(select(func.max(Transaction.timestamp)))
        orig_stats = _window_mix(orig, tmax)
        orig_stats["n_tx"] = orig.scalar(select(func.count()).select_from(Transaction))
        orig_stats["n_acct"] = orig.scalar(select(func.count()).select_from(Account))
        _print("ORIGINAL freeze (eval at max(ts))", orig_stats)
    finally:
        orig.close()

    # A: current catch-up normals, eval at max(ts)
    a = generate(SNAPSHOT_DIR / "ab_seed42_catchup_normals.db", catchup="normals")
    try:
        a_at_max = _window_mix(a["db"], a["tmax"])
        a_at_max["n_tx"] = a["n_tx"]
        a_at_max["n_acct"] = a["n_acct"]
        a_at_max["catchup_steps"] = a["gen"]["catchup_steps"]
        _print("A seed42 catchup=normals, as_of=max(ts)  [current multi-seed]", a_at_max)
    finally:
        a["db"].close()
        set_simulation_seed(None)

    # B: no extra txs; eval at last loop clock so the last 15 min of the
    # 185-min run is the window (drip legs after clock are excluded).
    b = generate(SNAPSHOT_DIR / "ab_seed42_no_catchup.db", catchup="none")
    try:
        b_clock = _window_mix(b["db"], b["last_clock"])
        b_clock["n_tx"] = b["n_tx"]
        b_clock["n_acct"] = b["n_acct"]
        b_clock["last_clock"] = b["last_clock"].isoformat()
        b_clock["tmax"] = b["tmax"].isoformat()
        _print("B seed42 catchup=none, as_of=last_loop_clock  [no extra txs]", b_clock)

        b_max = _window_mix(b["db"], b["tmax"])
        _print("B' seed42 catchup=none, as_of=max(ts)  [empty-window bug]", b_max)
    finally:
        b["db"].close()
        set_simulation_seed(None)

    # Organic continuation: same roll as run_simulation until clock catches max(ts)
    c = generate(SNAPSHOT_DIR / "ab_seed42_organic.db", catchup="organic")
    try:
        c_stats = _window_mix(c["db"], c["tmax"])
        c_stats["n_tx"] = c["n_tx"]
        c_stats["n_acct"] = c["n_acct"]
        c_stats["catchup_steps"] = c["gen"]["catchup_steps"]
        _print("C seed42 catchup=organic (live-loop continuation), as_of=max(ts)", c_stats)
    finally:
        c["db"].close()
        set_simulation_seed(None)

    print("\n=== A vs B (same seed 42) ===")
    print(
        f"  scored  A={a_at_max['scored']}  B(last_clock)={b_clock['scored']}  "
        f"orig={orig_stats['scored']}"
    )
    print(
        f"  fusion F1  A={a_at_max['fusion_f1']:.3f}  B={b_clock['fusion_f1']:.3f}  "
        f"orig={orig_stats['fusion_f1']:.3f}"
    )
    print(
        f"  window legit txs  A={a_at_max['window_legit']}  B={b_clock['window_legit']}  "
        f"orig={orig_stats['window_legit']}"
    )

    # Step 4: freeze_snapshot copy of an organic continuation with a new seed
    # (live pathway = same loop as run_simulation, then freeze_snapshot).
    live_src = SNAPSHOT_DIR / "_live_session_tmp.db"
    if live_src.exists():
        live_src.unlink()
    live_db = init_eval_db(live_src)()
    try:
        gen = generate_offline_trace(
            live_db,
            n_steps=N_STEPS,
            seed=314,
            start_time=START_TIME,
            interval_seconds=SIMULATION_INTERVAL_SECONDS,
            catchup="organic",
        )
        live_tmax = live_db.scalar(select(func.max(Transaction.timestamp)))
        live_stats = _window_mix(live_db, live_tmax)
        live_stats["n_tx"] = live_db.scalar(select(func.count()).select_from(Transaction))
        live_stats["catchup_steps"] = gen["catchup_steps"]
        live_stats["seed"] = 314
        _print("STEP4 seed=314 organic continuation then freeze_snapshot", live_stats)
    finally:
        live_db.close()
        set_simulation_seed(None)

    dest = freeze_snapshot(live_src, SNAPSHOT_DIR, label="multiseed_live_equiv")
    print(f"\nFroze {dest}")
    live_src.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
