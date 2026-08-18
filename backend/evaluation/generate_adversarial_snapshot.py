#!/usr/bin/env python3
"""Build a labeled adversarial snapshot on a seeded normals-only background."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models import Account, Transaction  # noqa: E402
from app.simulation.adversarial_attacks import (  # noqa: E402
    generate_diluted_hub_attack,
    generate_minimal_ring_attack,
    generate_standard_attack,
    generate_straddle_attack,
)
from app.simulation.constants import SIMULATION_INTERVAL_SECONDS  # noqa: E402
from app.simulation.generator import (  # noqa: E402
    generate_normal_transaction,
    generate_offline_trace,
    set_simulation_seed,
)
from evaluation.db import init_eval_db  # noqa: E402

SEED = 20260816
START_TIME = datetime(2026, 8, 16, 12, 0, 0)
SPAN_MINUTES = 75
N_STEPS = int(SPAN_MINUTES * 60 / SIMULATION_INTERVAL_SECONDS)  # 2250
AS_OF = START_TIME + timedelta(minutes=SPAN_MINUTES)
N_PER_VARIANT = 5

SNAPSHOT_DIR = BACKEND_ROOT / "snapshots"
SNAPSHOT_PATH = SNAPSHOT_DIR / "adversarial_eval.db"
CATALOG_PATH = BACKEND_ROOT / "evaluation" / "data" / "adversarial_instances.json"


def _jsonable(inst: dict) -> dict:
    out = {}
    for key, value in inst.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def generate_snapshot(dest: Path = SNAPSHOT_PATH) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()

    factory = init_eval_db(dest)
    db = factory()
    instances: list[dict] = []
    try:
        gen = generate_offline_trace(
            db,
            n_steps=N_STEPS,
            seed=SEED,
            start_time=START_TIME,
            interval_seconds=SIMULATION_INTERVAL_SECONDS,
            catchup="none",
            include_attacks=False,
        )
        boundary_15 = AS_OF - timedelta(minutes=15)
        boundary_60 = AS_OF - timedelta(minutes=60)

        for i in range(N_PER_VARIANT):
            instances.append(
                generate_standard_attack(
                    db,
                    start=AS_OF - timedelta(minutes=9) + timedelta(seconds=25 * i),
                    instance_id=f"standard:{i}",
                )
            )
            instances.append(
                generate_straddle_attack(
                    db,
                    boundary=boundary_15 + timedelta(seconds=4 * i),
                    window_minutes=15,
                    instance_id=f"straddle_15:{i}",
                )
            )
            instances.append(
                generate_straddle_attack(
                    db,
                    boundary=boundary_60 + timedelta(seconds=4 * i),
                    window_minutes=60,
                    instance_id=f"straddle_60:{i}",
                )
            )
            instances.append(
                generate_diluted_hub_attack(
                    db,
                    start=AS_OF - timedelta(minutes=8) + timedelta(seconds=20 * i),
                    instance_id=f"diluted_hub:{i}",
                )
            )
            instances.append(
                generate_minimal_ring_attack(
                    db,
                    start=AS_OF - timedelta(minutes=7) + timedelta(seconds=18 * i),
                    instance_id=f"minimal_ring:{i}",
                )
            )

        generate_normal_transaction(db, now=AS_OF, commit=False)
        db.commit()

        n_tx = db.scalar(select(func.count()).select_from(Transaction))
        n_acct = db.scalar(select(func.count()).select_from(Account))
        tmax = db.scalar(select(func.max(Transaction.timestamp)))
        n_labeled = db.scalar(
            select(func.count()).select_from(Transaction).where(
                Transaction.is_synthetic_attack.is_(True)
            )
        )
        catalog = {
            "seed": SEED,
            "as_of": AS_OF.isoformat(),
            "max_timestamp": tmax.isoformat() if tmax else None,
            "path": str(dest),
            "background": gen,
            "n_transactions": int(n_tx),
            "n_accounts": int(n_acct),
            "n_attack_transactions": int(n_labeled or 0),
            "n_per_variant": N_PER_VARIANT,
            "instances": [_jsonable(inst) for inst in instances],
        }
        CATALOG_PATH.write_text(json.dumps(catalog, indent=2))
        return catalog
    finally:
        db.close()
        set_simulation_seed(None)


def main() -> None:
    catalog = generate_snapshot()
    print(
        f"Wrote {catalog['path']}\n"
        f"  as_of={catalog['as_of']}  max_ts={catalog['max_timestamp']}\n"
        f"  txs={catalog['n_transactions']} accounts={catalog['n_accounts']} "
        f"attack_txs={catalog['n_attack_transactions']}\n"
        f"  instances={len(catalog['instances'])} "
        f"({catalog['n_per_variant']} × 5 variants)\n"
        f"  catalog={CATALOG_PATH}"
    )
    counts: dict[str, int] = {}
    for inst in catalog["instances"]:
        counts[inst["variant"]] = counts.get(inst["variant"], 0) + 1
    print("  by variant:", counts)


if __name__ == "__main__":
    main()
