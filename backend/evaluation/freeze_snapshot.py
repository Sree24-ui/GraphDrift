#!/usr/bin/env python3
"""Copy the live graphdrift.db into a timestamped snapshot for reproducible eval."""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = BACKEND_ROOT / "graphdrift.db"
DEFAULT_SNAPSHOT_DIR = BACKEND_ROOT / "snapshots"


def freeze_snapshot(
    source: Path,
    snapshot_dir: Path,
    *,
    label: str | None = None,
) -> Path:
    if not source.exists():
        raise FileNotFoundError(f"Source database not found: {source}")

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    stamp = label or datetime.now().strftime("%Y-%m-%d")
    dest = snapshot_dir / f"graphdrift_snapshot_{stamp}.db"

    if dest.exists():
        stamp_with_time = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        dest = snapshot_dir / f"graphdrift_snapshot_{stamp_with_time}.db"

    shutil.copy2(source, dest)
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze graphdrift.db snapshot")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR)
    parser.add_argument(
        "--label",
        type=str,
        default=None,
        help="Optional date label (default: today's date)",
    )
    args = parser.parse_args()

    dest = freeze_snapshot(args.source, args.output_dir, label=args.label)
    print(dest)


if __name__ == "__main__":
    main()
