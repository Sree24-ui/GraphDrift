#!/usr/bin/env python3
"""
Reset GraphDrift demo data to a clean slate.

Deletes all rows from Transaction, Alert, AccountScoreHistory, and Account.
Intended to run before a live demo or recording so the simulator builds a
small, legible alert set over the first 15–20 minutes instead of inheriting
a long-session backlog.

DESTRUCTIVE — requires interactive confirmation.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `python scripts/reset_demo_data.py` from the backend/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select, text

from app.db import SessionLocal, engine
from app.models import Account, AccountScoreHistory, Alert, Transaction

TABLES_IN_DELETE_ORDER = (
    ("transactions", Transaction),
    ("alerts", Alert),
    ("account_score_history", AccountScoreHistory),
    ("accounts", Account),
)

SQLITE_SEQUENCE_TABLES = (
    "transactions",
    "alerts",
    "account_score_history",
)


def _row_counts(db) -> dict[str, int]:
    return {
        label: db.scalar(select(func.count()).select_from(model)) or 0
        for label, model in TABLES_IN_DELETE_ORDER
    }


def _print_counts(label: str, counts: dict[str, int]) -> None:
    print(f"\n{label}")
    for table, count in counts.items():
        print(f"  {table}: {count:,}")


def reset_demo_data(*, assume_yes: bool = False) -> None:
    counts_before = None
    with SessionLocal() as db:
        counts_before = _row_counts(db)
        _print_counts("Current row counts:", counts_before)

        if all(count == 0 for count in counts_before.values()):
            print("\nAll target tables are already empty. Nothing to do.")
            return

    print(
        "\nThis will permanently delete ALL demo data in:\n"
        "  - transactions\n"
        "  - alerts\n"
        "  - account_score_history\n"
        "  - accounts\n"
    )
    if not assume_yes:
        answer = input("Type 'yes' to confirm: ").strip().lower()
        if answer != "yes":
            print("Aborted.")
            sys.exit(1)
    else:
        print("Skipping confirmation (--yes flag).")

    with SessionLocal() as db:
        if engine.dialect.name == "sqlite":
            db.execute(text("PRAGMA foreign_keys = OFF"))

        for table_name, _model in TABLES_IN_DELETE_ORDER:
            db.execute(text(f"DELETE FROM {table_name}"))

        if engine.dialect.name == "sqlite":
            sequence_exists = db.scalar(
                text(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type='table' AND name='sqlite_sequence'"
                )
            )
            if sequence_exists:
                for table_name in SQLITE_SEQUENCE_TABLES:
                    db.execute(
                        text("DELETE FROM sqlite_sequence WHERE name = :name"),
                        {"name": table_name},
                    )
            db.execute(text("PRAGMA foreign_keys = ON"))

        db.commit()
        counts_after = _row_counts(db)

    _print_counts("After reset:", counts_after)

    if any(count != 0 for count in counts_after.values()):
        print("\nERROR: one or more tables still contain rows.", file=sys.stderr)
        sys.exit(1)

    print("\nDemo data reset complete.")
    print(
        "Restart the backend server so the in-memory simulation account pool "
        "is re-seeded from an empty database."
    )


if __name__ == "__main__":
    assume_yes = "--yes" in sys.argv
    reset_demo_data(assume_yes=assume_yes)
