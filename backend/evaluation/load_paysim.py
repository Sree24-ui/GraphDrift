#!/usr/bin/env python3
"""
Load a stratified PaySim sample into an isolated SQLite database.

Data sources (first match wins):
  1. Local CSV via --csv (Kaggle PS_20174392719_1491204439457_log.csv)
  2. HuggingFace mirror: purulalwani/Synthetic-Financial-Datasets-For-Fraud-Detection

Output: evaluation/data/paysim_eval.db (separate from graphdrift.db)
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models import Account, Transaction  # noqa: E402
from evaluation.db import init_eval_db  # noqa: E402
from evaluation.paysim_config import PAYSIM_COMPRESSED_MINUTES_PER_STEP  # noqa: E402

DEFAULT_OUTPUT = BACKEND_ROOT / "evaluation" / "data" / "paysim_eval.db"
PAYSIM_BASE_TIME = datetime(2018, 1, 1, 0, 0, 0)
HF_DATASET = "purulalwani/Synthetic-Financial-Datasets-For-Fraud-Detection"
# Fraud in PaySim appears only in TRANSFER / CASH_OUT; keep PAYMENT for legit density.
RELEVANT_TYPES = {"TRANSFER", "CASH_OUT", "PAYMENT"}


def _account_id(raw: str) -> str:
    return f"{raw}@paysim"


def cap_transactions_preserve_fraud(
    df: pd.DataFrame,
    *,
    max_transactions: int,
    seed: int,
) -> pd.DataFrame:
    if len(df) <= max_transactions:
        return df

    fraud = df[df["isFraud"] == 1]
    legit = df[df["isFraud"] == 0]
    remaining = max_transactions - len(fraud)
    if remaining <= 0:
        return fraud.head(max_transactions).reset_index(drop=True)

    legit_sample = legit.sample(n=min(remaining, len(legit)), random_state=seed)
    combined = pd.concat([fraud, legit_sample], ignore_index=True)
    return combined.sample(frac=1.0, random_state=seed).reset_index(drop=True)


def step_to_timestamp(step: int, sequence_in_step: int) -> datetime:
    """
    Map PaySim step to compressed wall-clock time.

    1 step = 1 simulated hour → 3 wall-clock minutes (÷20 scale factor).
  Intra-step ordering is preserved via sequence_in_step offsets.
    """
    step_minutes = int(step) * PAYSIM_COMPRESSED_MINUTES_PER_STEP
    span_seconds = max(int(PAYSIM_COMPRESSED_MINUTES_PER_STEP * 60) - 1, 1)
    offset_seconds = int(sequence_in_step % span_seconds)
    return PAYSIM_BASE_TIME + timedelta(minutes=step_minutes, seconds=offset_seconds)


def assign_timestamps_from_steps(df: pd.DataFrame) -> pd.DataFrame:
    """Convert PaySim step column to real timestamps (1 step = 1 simulated hour)."""
    df = df.sort_values(["step", "amount"], kind="stable").reset_index(drop=True)
    step_sequences: dict[int, int] = {}
    timestamps: list[datetime] = []
    for step in df["step"]:
        step_int = int(step)
        seq = step_sequences.get(step_int, 0)
        step_sequences[step_int] = seq + 1
        timestamps.append(step_to_timestamp(step_int, seq))
    df = df.copy()
    df["eval_timestamp"] = timestamps
    return df


def stratified_sample_dataframe(
    df: pd.DataFrame,
    *,
    max_rows: int,
    target_fraud_ratio: float,
    seed: int,
) -> pd.DataFrame:
    rng = random.Random(seed)
    fraud = df[df["isFraud"] == 1]
    legit = df[df["isFraud"] == 0]

    target_fraud = min(len(fraud), max(1, int(max_rows * target_fraud_ratio)))
    target_legit = min(len(legit), max_rows - target_fraud)

    if len(fraud) > target_fraud:
        fraud_sample = fraud.sample(n=target_fraud, random_state=seed)
    else:
        fraud_sample = fraud

    if len(legit) > target_legit:
        legit_sample = legit.sample(n=target_legit, random_state=seed)
    else:
        legit_sample = legit

    combined = pd.concat([fraud_sample, legit_sample], ignore_index=True)
    combined = combined.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    return restrict_contiguous_hours(combined)


def restrict_contiguous_hours(df: pd.DataFrame, hours: int = 48) -> pd.DataFrame:
    """Keep a contiguous PaySim step window for temporal coherence."""
    min_step = int(df["step"].min())
    max_step = min_step + hours
    trimmed = df[df["step"] <= max_step].copy()
    if trimmed.empty:
        return df
    return trimmed.reset_index(drop=True)


def stream_stratified_sample(
    *,
    max_rows: int,
    target_fraud_ratio: float,
    seed: int,
) -> pd.DataFrame:
    from datasets import load_dataset

    rng = random.Random(seed)
    target_fraud = max(1, int(max_rows * target_fraud_ratio))
    target_legit = max_rows - target_fraud

    fraud_rows: list[dict] = []
    legit_rows: list[dict] = []
    legit_seen = 0

    ds = load_dataset(HF_DATASET, split="train", streaming=True)
    for row in ds:
        if str(row["type"]) not in RELEVANT_TYPES:
            continue
        is_fraud = int(row["isFraud"]) == 1
        if is_fraud:
            if len(fraud_rows) < target_fraud:
                fraud_rows.append(dict(row))
        else:
            legit_seen += 1
            if len(legit_rows) < target_legit:
                legit_rows.append(dict(row))
            else:
                # Reservoir sampling for non-fraud rows.
                j = rng.randint(1, legit_seen)
                if j <= target_legit:
                    legit_rows[j - 1] = dict(row)

        if len(fraud_rows) >= target_fraud and len(legit_rows) >= target_legit:
            break

    if not fraud_rows:
        raise RuntimeError("No fraud rows collected — check dataset source.")

    combined = fraud_rows + legit_rows
    rng.shuffle(combined)
    df = pd.DataFrame(combined)
    return restrict_contiguous_hours(df)


def load_paysim_frame(
    *,
    csv_path: Path | None,
    max_rows: int,
    target_fraud_ratio: float,
    seed: int,
) -> pd.DataFrame:
    if csv_path is not None:
        print(f"Reading PaySim CSV: {csv_path}")
        df = pd.read_csv(csv_path)
        if "type" in df.columns:
            df = df[df["type"].isin(RELEVANT_TYPES)]
        return stratified_sample_dataframe(
            df,
            max_rows=max_rows,
            target_fraud_ratio=target_fraud_ratio,
            seed=seed,
        )

    print(f"Streaming stratified sample from HuggingFace ({HF_DATASET})…")
    return stream_stratified_sample(
        max_rows=max_rows,
        target_fraud_ratio=target_fraud_ratio,
        seed=seed,
    )


def populate_database(db: Session, df: pd.DataFrame) -> dict[str, int]:
    df = assign_timestamps_from_steps(df)

    account_ids: set[str] = set()
    for _, row in df.iterrows():
        account_ids.add(_account_id(str(row["nameOrig"])))
        account_ids.add(_account_id(str(row["nameDest"])))

    existing = {acc.id for acc in db.query(Account.id).all()}
    for account_id in sorted(account_ids - existing):
        db.add(Account(id=account_id))

    db.flush()

    for _, row in df.iterrows():
        db.add(
            Transaction(
                sender_id=_account_id(str(row["nameOrig"])),
                receiver_id=_account_id(str(row["nameDest"])),
                amount=float(row["amount"]),
                timestamp=row["eval_timestamp"],
                is_synthetic_attack=bool(int(row["isFraud"])),
            )
        )

    db.commit()

    fraud_tx = int(df["isFraud"].sum())
    ts_min = df["eval_timestamp"].min()
    ts_max = df["eval_timestamp"].max()
    return {
        "transactions": len(df),
        "accounts": len(account_ids),
        "fraud_transactions": fraud_tx,
        "fraud_ratio": fraud_tx / len(df) if len(df) else 0.0,
        "min_timestamp": str(ts_min),
        "max_timestamp": str(ts_max),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Load PaySim sample into paysim_eval.db")
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Optional local PaySim CSV (Kaggle download)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output SQLite path",
    )
    parser.add_argument("--max-rows", type=int, default=250_000)
    parser.add_argument(
        "--cap-transactions",
        type=int,
        default=12_000,
        help="Cap final transaction count after sampling (keeps all fraud rows)",
    )
    parser.add_argument(
        "--fraud-ratio",
        type=float,
        default=0.02,
        help="Target fraud share in sample (default 2%%)",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if args.output.exists():
        args.output.unlink()

    df = load_paysim_frame(
        csv_path=args.csv,
        max_rows=args.max_rows,
        target_fraud_ratio=args.fraud_ratio,
        seed=args.seed,
    )
    df = cap_transactions_preserve_fraud(
        df,
        max_transactions=args.cap_transactions,
        seed=args.seed,
    )

    session_factory = init_eval_db(args.output)
    db = session_factory()
    try:
        stats = populate_database(db, df)
    finally:
        db.close()

    print(f"Wrote {args.output}")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
