#!/usr/bin/env python3
"""Provision or update a GraphDrift user without exposing registration."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from sqlalchemy import select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_runtime_config  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402

DEV_USERNAME = "local-admin"
DEV_PASSWORD = "local-development-only"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Provision a GraphDrift user")
    parser.add_argument("--username")
    parser.add_argument("--role", choices=("analyst", "admin"), default="analyst")
    parser.add_argument(
        "--dev-seed",
        action="store_true",
        help="create local-admin/local-development-only in non-production only",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_runtime_config()
    if args.dev_seed:
        if config.environment == "production":
            print("Refusing --dev-seed when ENVIRONMENT=production", file=sys.stderr)
            return 2
        username, role, password = DEV_USERNAME, "admin", DEV_PASSWORD
    else:
        username = (args.username or "").strip()
        role = args.role
        if not username:
            print("--username is required unless --dev-seed is used", file=sys.stderr)
            return 2
        password = getpass.getpass("Password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            print("Passwords do not match", file=sys.stderr)
            return 2

    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    init_db()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(username=username, role=role, password_hash=password_hash)
            db.add(user)
            action = "Created"
        else:
            user.role = role
            user.password_hash = password_hash
            action = "Updated"
        db.commit()
    print(f"{action} {role} user '{username}'")
    if args.dev_seed:
        print(f"Local credentials: {DEV_USERNAME} / {DEV_PASSWORD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
