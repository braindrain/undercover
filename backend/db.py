"""The UnderCover database, seeded from the business data in data/."""

import json
import os
import sqlite3
from pathlib import Path

DATA = Path(__file__).parent.parent / "data"
DEFAULT_PATH = Path(__file__).parent / "undercover.db"

SEED_TABLES = {
    "policies": "policy_id",
    "customers": "customer_id",
    "handlers": "handler_id",
}

SCHEMA = """
CREATE TABLE claims (
    claim_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE policies (policy_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE customers (customer_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE handlers (handler_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE steps (
    step_id INTEGER PRIMARY KEY,
    claim_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE decisions (
    claim_id TEXT PRIMARY KEY,
    decision TEXT NOT NULL,
    payout REAL NOT NULL,
    reasons TEXT NOT NULL,
    policy_section TEXT NOT NULL,
    decided_at TEXT NOT NULL
);
CREATE TABLE payments (
    claim_id TEXT PRIMARY KEY,
    amount REAL NOT NULL,
    paid_at TEXT NOT NULL
);
CREATE TABLE sign_offs (
    claim_id TEXT PRIMARY KEY,
    handler_id TEXT NOT NULL,
    signed_off_at TEXT NOT NULL
);
"""


def read_data(name: str) -> str:
    """Return the text of one file in data/."""
    return (DATA / name).read_text(encoding="utf-8")


def database_path() -> Path:
    """Return the database's path, from DATABASE_PATH or the default."""
    return Path(os.getenv("DATABASE_PATH") or DEFAULT_PATH)


def connect() -> sqlite3.Connection:
    """Open the database, creating and seeding it if it doesn't exist yet."""
    path = database_path()
    new = not path.exists()
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if new:
        seed(conn)
    return conn


def seed(conn: sqlite3.Connection) -> None:
    """Create the tables and load the claims queue and the records behind it."""
    conn.executescript(SCHEMA)
    for claim in json.loads(read_data("claims.json")):
        claim.pop("status")
        conn.execute(
            "INSERT INTO claims VALUES (?, 'waiting', ?)",
            (claim["claim_id"], json.dumps(claim)),
        )
    for table, key in SEED_TABLES.items():
        for record in json.loads(read_data(f"{table}.json")):
            conn.execute(
                f"INSERT INTO {table} VALUES (?, ?)",  # noqa: S608
                (record[key], json.dumps(record)),
            )
    conn.commit()


def reset() -> Path:
    """Delete the database and create it again from data/."""
    path = database_path()
    path.unlink(missing_ok=True)
    connect().close()
    return path
