#!/usr/bin/env python3
"""
Load a stratified IBM HI-Small AML sample into an isolated SQLite database.

Dataset
  Kaggle: ealtman2019/ibm-transactions-for-anti-money-laundering-aml
  File:   HI-Small_Trans.csv  (~5M transactions / ~500K accounts)
  Mirror: HuggingFace OsamaMIT/IBM-AML-HI-Small
          (HI-Small_Trans.csv + HI-Small_Patterns.txt)

Do NOT use HI-Medium / HI-Large.

Manual download (when Kaggle/HF are unreachable)
  1. From Kaggle, download only HI-Small_Trans.csv
     https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml
  2. Place it at:
       graphdrift/backend/evaluation/data/HI-Small_Trans.csv
  3. Run:
       python -m evaluation.load_ibm_aml --csv evaluation/data/HI-Small_Trans.csv

Kaggle CLI (optional, needs ~/.kaggle/kaggle.json):
       pip install kaggle
       kaggle datasets download -d ealtman2019/ibm-transactions-for-anti-money-laundering-aml \\
         -f HI-Small_Trans.csv -p evaluation/data --unzip

Output: evaluation/data/ibm_aml_eval.db
  Isolated from graphdrift.db and paysim_eval.db.

Ground-truth mapping
  Is Laundering → Transaction.is_labeled_fraud
  is_synthetic_attack is left False (demo-simulator semantics, not this corpus).
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
from evaluation.ibm_aml_config import (  # noqa: E402
    IBM_AML_STRATIFY_WINDOW_HOURS,
    IBM_AML_TIME_SCALE,
)

DEFAULT_OUTPUT = BACKEND_ROOT / "evaluation" / "data" / "ibm_aml_eval.db"
DEFAULT_CSV = BACKEND_ROOT / "evaluation" / "data" / "HI-Small_Trans.csv"
HF_REPO = "OsamaMIT/IBM-AML-HI-Small"
HF_FILE = "HI-Small_Trans.csv"
KAGGLE_DATASET = "ealtman2019/ibm-transactions-for-anti-money-laundering-aml"
KAGGLE_FILE = "HI-Small_Trans.csv"
IBM_AML_BASE_TIME = datetime(2022, 9, 1, 0, 0, 0)
SHORT_SPAN_HOURS = 12.0

COLUMN_ALIASES = {
    "timestamp": ("Timestamp", "timestamp", "Time"),
    "from_bank": ("From Bank", "from_bank", "FromBank"),
    "from_account": ("Account", "from_account", "Originator", "From Account"),
    "to_bank": ("To Bank", "to_bank", "ToBank"),
    "to_account": ("Account.1", "to_account", "Beneficiary", "To Account", "Account1"),
    "amount_received": ("Amount Received", "amount_received", "AmountReceived"),
    "amount_paid": ("Amount Paid", "amount_paid", "AmountPaid"),
    "receiving_currency": ("Receiving Currency", "receiving_currency"),
    "payment_format": ("Payment Format", "payment_format", "PaymentFormat"),
    "is_laundering": ("Is Laundering", "is_laundering", "IsLaundering", "label"),
}


def _resolve_column(columns: list[str], aliases: tuple[str, ...]) -> str | None:
    lookup = {name.strip(): name for name in columns}
    for alias in aliases:
        if alias in lookup:
            return lookup[alias]
    lowered = {name.strip().lower(): name for name in columns}
    for alias in aliases:
        if alias.lower() in lowered:
            return lowered[alias.lower()]
    return None


def inspect_schema(df: pd.DataFrame) -> dict[str, str]:
    print("\n=== IBM HI-Small schema (as downloaded) ===")
    print(f"columns ({len(df.columns)}): {list(df.columns)}")
    print("\n--- sample rows ---")
    print(df.head(5).to_string(index=False))
    print("\n--- dtypes ---")
    print(df.dtypes.to_string())

    mapping: dict[str, str] = {}
    missing: list[str] = []
    for key, aliases in COLUMN_ALIASES.items():
        found = _resolve_column(list(df.columns), aliases)
        if found is None:
            missing.append(key)
        else:
            mapping[key] = found
    print("\n--- column mapping ---")
    for key, source in mapping.items():
        print(f"  {key:20s} <- {source}")
    if missing:
        raise RuntimeError(f"Required columns not found: {missing}. Got {list(df.columns)}")
    return mapping


def _account_id(bank: object, account: object) -> str:
    return f"{bank}:{account}@ibm"


def parse_ibm_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return pd.to_datetime(text).to_pydatetime()


def densest_fraud_window(
    fraud_times: pd.Series,
    *,
    window: timedelta,
) -> tuple[datetime, datetime]:
    ts = fraud_times.sort_values().reset_index(drop=True)
    if ts.empty:
        raise RuntimeError("No labeled laundering rows in the source file.")
    best_count = 0
    best_start = ts.iloc[0]
    j = 0
    for i in range(len(ts)):
        start = ts.iloc[i]
        while j < len(ts) and ts.iloc[j] - start <= window:
            j += 1
        count = j - i
        if count > best_count:
            best_count = count
            best_start = start
    return best_start, best_start + window


def choose_time_scale(span: timedelta) -> float:
    span_hours = span.total_seconds() / 3600.0
    if span_hours < SHORT_SPAN_HOURS:
        return 1.0
    return float(IBM_AML_TIME_SCALE)


def compress_timestamps(
    timestamps: pd.Series,
    *,
    time_scale: float,
) -> pd.Series:
    parsed = timestamps.map(parse_ibm_timestamp)
    t_min = parsed.min()
    elapsed = parsed.map(lambda ts: (ts - t_min).total_seconds())
    compressed = elapsed.map(lambda seconds: seconds / time_scale)
    return compressed.map(lambda seconds: IBM_AML_BASE_TIME + timedelta(seconds=float(seconds)))


def stratified_subsample(
    df: pd.DataFrame,
    *,
    mapping: dict[str, str],
    max_rows: int,
    seed: int,
    window_hours: float,
) -> tuple[pd.DataFrame, dict[str, object]]:
    label_col = mapping["is_laundering"]
    ts_col = mapping["timestamp"]
    df = df.copy()
    df["_is_laundering"] = pd.to_numeric(df[label_col], errors="coerce").fillna(0).astype(int)
    df["_ts"] = df[ts_col].map(parse_ibm_timestamp)

    fraud = df[df["_is_laundering"] == 1]
    legit = df[df["_is_laundering"] == 0]
    source_fraud_rate = len(fraud) / len(df) if len(df) else 0.0
    source_span = df["_ts"].max() - df["_ts"].min()

    window = timedelta(hours=window_hours)
    win_start, win_end = densest_fraud_window(fraud["_ts"], window=window)
    in_window = df[(df["_ts"] >= win_start) & (df["_ts"] <= win_end)]
    window_fraud = in_window[in_window["_is_laundering"] == 1]
    window_legit = in_window[in_window["_is_laundering"] == 0]

    target_legit = max(0, max_rows - len(window_fraud))
    if len(window_legit) > target_legit:
        legit_sample = window_legit.sample(n=target_legit, random_state=seed)
    else:
        legit_sample = window_legit

    combined = pd.concat([window_fraud, legit_sample], ignore_index=True)
    combined = combined.sample(frac=1.0, random_state=seed).reset_index(drop=True)

    stats = {
        "source_rows": len(df),
        "source_fraud_rows": len(fraud),
        "source_fraud_rate": source_fraud_rate,
        "source_span": str(source_span),
        "window_start": str(win_start),
        "window_end": str(win_end),
        "window_fraud_rows": len(window_fraud),
        "subsample_rows": len(combined),
        "subsample_fraud_rows": int(combined["_is_laundering"].sum()),
        "subsample_fraud_rate": float(combined["_is_laundering"].mean()) if len(combined) else 0.0,
    }
    return combined, stats


def try_huggingface_download(dest: Path) -> Path | None:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("huggingface_hub not installed; skip HF download.")
        return None

    print(f"Downloading {HF_FILE} from HuggingFace ({HF_REPO})…")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        path = hf_hub_download(
            repo_id=HF_REPO,
            filename=HF_FILE,
            repo_type="dataset",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"HuggingFace download failed: {exc}")
        return None

    downloaded = Path(path)
    if not downloaded.exists():
        return None
    if downloaded.resolve() != dest.resolve():
        import shutil

        shutil.copy2(downloaded, dest)
        return dest
    return downloaded


def try_kaggle_download(dest: Path) -> Path | None:
    try:
        from kaggle.api.kaggle_api_extended import KaggleApi
    except ImportError:
        print("kaggle package not installed; skip Kaggle download.")
        return None

    creds = Path.home() / ".kaggle" / "kaggle.json"
    if not creds.exists():
        print("No ~/.kaggle/kaggle.json; skip Kaggle download.")
        return None

    print(f"Downloading {KAGGLE_FILE} from Kaggle ({KAGGLE_DATASET})…")
    dest.parent.mkdir(parents=True, exist_ok=True)
    api = KaggleApi()
    api.authenticate()
    api.dataset_download_file(
        KAGGLE_DATASET,
        KAGGLE_FILE,
        path=str(dest.parent),
        quiet=False,
    )
    zip_path = dest.parent / f"{KAGGLE_FILE}.zip"
    if zip_path.exists():
        import zipfile

        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(dest.parent)
        zip_path.unlink()
    return dest if dest.exists() else None


def resolve_csv_path(explicit: Path | None) -> Path:
    if explicit is not None:
        if not explicit.exists():
            raise FileNotFoundError(f"CSV not found: {explicit}")
        return explicit

    if DEFAULT_CSV.exists():
        print(f"Using local CSV: {DEFAULT_CSV}")
        return DEFAULT_CSV

    hf_path = try_huggingface_download(DEFAULT_CSV)
    if hf_path is not None and hf_path.exists():
        return hf_path

    kaggle_path = try_kaggle_download(DEFAULT_CSV)
    if kaggle_path is not None and kaggle_path.exists():
        return kaggle_path

    raise FileNotFoundError(
        "HI-Small_Trans.csv not found and automatic download failed.\n"
        "Manual step: download HI-Small_Trans.csv from\n"
        f"  https://www.kaggle.com/datasets/{KAGGLE_DATASET}\n"
        f"and place it at:\n  {DEFAULT_CSV}"
    )


def populate_database(
    db: Session,
    df: pd.DataFrame,
    mapping: dict[str, str],
    *,
    time_scale: float,
) -> dict[str, object]:
    df = df.copy()
    df["eval_timestamp"] = compress_timestamps(df["_ts"], time_scale=time_scale)

    from_acct = mapping["from_account"]
    to_acct = mapping["to_account"]
    from_bank = mapping["from_bank"]
    to_bank = mapping["to_bank"]
    amount_col = mapping["amount_received"]

    account_ids: set[str] = set()
    for _, row in df.iterrows():
        account_ids.add(_account_id(row[from_bank], row[from_acct]))
        account_ids.add(_account_id(row[to_bank], row[to_acct]))

    existing = {acc.id for acc in db.query(Account.id).all()}
    for account_id in sorted(account_ids - existing):
        db.add(Account(id=account_id))
    db.flush()

    for _, row in df.iterrows():
        db.add(
            Transaction(
                sender_id=_account_id(row[from_bank], row[from_acct]),
                receiver_id=_account_id(row[to_bank], row[to_acct]),
                amount=float(row[amount_col]),
                timestamp=row["eval_timestamp"],
                is_synthetic_attack=False,
                is_labeled_fraud=bool(int(row["_is_laundering"])),
            )
        )
    db.commit()

    fraud_tx = int(df["_is_laundering"].sum())
    return {
        "transactions": len(df),
        "accounts": len(account_ids),
        "labeled_fraud_transactions": fraud_tx,
        "labeled_fraud_rate": fraud_tx / len(df) if len(df) else 0.0,
        "time_scale": time_scale,
        "min_eval_timestamp": str(df["eval_timestamp"].min()),
        "max_eval_timestamp": str(df["eval_timestamp"].max()),
        "min_source_timestamp": str(df["_ts"].min()),
        "max_source_timestamp": str(df["_ts"].max()),
    }


def spot_check(db: Session, n: int = 5) -> None:
    rows = db.query(Transaction).order_by(Transaction.id.asc()).limit(n).all()
    print("\n=== spot-check ibm_aml_eval.db ===")
    for tx in rows:
        print(
            f"  id={tx.id} {tx.timestamp.isoformat()} "
            f"{tx.sender_id} -> {tx.receiver_id} amount={tx.amount:.2f} "
            f"is_labeled_fraud={tx.is_labeled_fraud} "
            f"is_synthetic_attack={tx.is_synthetic_attack}"
        )
    fraud_count = (
        db.query(Transaction).filter(Transaction.is_labeled_fraud.is_(True)).count()
    )
    total = db.query(Transaction).count()
    print(f"  db totals: transactions={total} labeled_fraud={fraud_count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load IBM HI-Small AML into ibm_aml_eval.db")
    parser.add_argument("--csv", type=Path, default=None, help="Path to HI-Small_Trans.csv")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--max-rows",
        type=int,
        default=250_000,
        help="Target subsample size (all laundering in the window is kept)",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--window-hours",
        type=float,
        default=IBM_AML_STRATIFY_WINDOW_HOURS,
        help="Contiguous IBM-time window for stratified subsample (before compression)",
    )
    args = parser.parse_args()

    csv_path = resolve_csv_path(args.csv)
    print(f"Reading {csv_path} (this can take a minute)…")
    preview = pd.read_csv(csv_path, nrows=5)
    mapping = inspect_schema(preview)

    usecols = list(dict.fromkeys(mapping.values()))
    df = pd.read_csv(csv_path, usecols=usecols)
    print(f"Loaded {len(df):,} rows from HI-Small.")

    sampled, sample_stats = stratified_subsample(
        df,
        mapping=mapping,
        max_rows=args.max_rows,
        seed=args.seed,
        window_hours=args.window_hours,
    )
    source_span = sampled["_ts"].max() - sampled["_ts"].min()
    time_scale = choose_time_scale(source_span)

    print("\n=== subsample ===")
    for key, value in sample_stats.items():
        if isinstance(value, float):
            print(f"  {key}: {value:.6f}" if value < 0.01 else f"  {key}: {value:.4f}")
        else:
            print(f"  {key}: {value}")

    print("\n=== time handling ===")
    print(f"  source window span: {source_span}")
    print(f"  compression scale:  {time_scale} (1 IBM hour → {60 / time_scale:.2f} eval minutes)")
    eval_minutes = source_span.total_seconds() / 60.0 / time_scale
    print(f"  compressed eval span: {eval_minutes:.2f} minutes")
    print(
        "  rationale: scale is derived from HI-Small_Patterns.txt ring durations "
        "(median ~75h → ÷332 so the median compressed ring is ~13.5 min, the same "
        "relative slot in the 60-min slow window as simulator slow-drip). "
        "PaySim ÷20 is not used. Scale=1 if the source span is already <12h."
    )
    print(
        "  LIMITATION: the 48h source window is the densest laundering slice, not a "
        "random sample. Fraud rate here is inflated vs full HI-Small (~0.1%). "
        "Do not generalize metrics to the corpus base rate."
    )

    if args.output.exists():
        args.output.unlink()

    session_factory = init_eval_db(args.output)
    db = session_factory()
    try:
        stats = populate_database(db, sampled, mapping, time_scale=time_scale)
        spot_check(db)
    finally:
        db.close()

    print(f"\nWrote {args.output}")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
