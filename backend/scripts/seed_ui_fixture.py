#!/usr/bin/env python3
"""Seed a throwaway database with enough real data to render every UI page.

Used by the frontend accessibility test: empty pages would pass the scan while
proving nothing, since most of what it checks lives in queue rows, ring members
and charts. Runs the real simulator and the real detection cycle, so the shapes
are the ones production serves, and leaves alerts unreviewed so the default
"needs attention" filter has rows.

    DATABASE_URL=sqlite:///./a11y.db python scripts/seed_ui_fixture.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db import SessionLocal, init_db  # noqa: E402
from app.detection.fusion import run_detection_cycle  # noqa: E402
from app.models import Alert  # noqa: E402
from app.simulation.generator import generate_offline_trace  # noqa: E402

CYCLES = 3
STEPS_PER_CYCLE = 200
INTERVAL_SECONDS = 2.0
# Relative to now, not a fixed date: a backend running against this fixture
# auto-expires alerts older than alert_staleness_hours, which would empty the
# queue before the scan reaches it.
START = datetime.now() - timedelta(seconds=CYCLES * STEPS_PER_CYCLE * INTERVAL_SECONDS + 120)


def main() -> int:
    init_db()
    with SessionLocal() as db:
        generate_offline_trace(
            db,
            n_steps=CYCLES * STEPS_PER_CYCLE,
            seed=20260918,
            start_time=START,
            interval_seconds=INTERVAL_SECONDS,
            catchup="normals",
        )
        for cycle in range(1, CYCLES + 1):
            as_of = START + timedelta(
                seconds=cycle * STEPS_PER_CYCLE * INTERVAL_SECONDS
            )
            run_detection_cycle(db, as_of=as_of)
        alerts = db.query(Alert).count()
        rings = db.query(Alert.ring_id).filter(Alert.ring_id.is_not(None)).distinct().count()
        print(f"seeded {alerts} alerts across {rings} rings")
        return 0 if alerts else 1


if __name__ == "__main__":
    raise SystemExit(main())
