"""The UnderCover database, seeded from the business data in data/: Supabase's
Postgres when DATABASE_URL is set, and a SQLite file otherwise."""

import json
import os
from functools import cache
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    Engine,
    Float,
    Integer,
    MetaData,
    NullPool,
    String,
    Table,
    create_engine,
    insert,
    inspect,
    make_url,
    text,
)

DATA = Path(__file__).parent.parent / "data"
DEFAULT_PATH = Path(__file__).parent / "undercover.db"
SCHEMA = "undercover"
POOL_SIZE = 4

metadata = MetaData()


def record_table(name: str, key: str) -> Table:
    """Define a table that holds one record from data/ per row, as JSON."""
    return Table(
        name,
        metadata,
        Column(key, String, primary_key=True),
        Column("data", JSON, nullable=False),
    )


claims = Table(
    "claims",
    metadata,
    Column("claim_id", String, primary_key=True),
    Column("customer_id", String, nullable=False),
    Column("policy_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("failures", Integer, nullable=False),
    Column("thread_id", String),
    Column("data", JSON, nullable=False),
)
policies = record_table("policies", "policy_id")
customers = record_table("customers", "customer_id")
handlers = record_table("handlers", "handler_id")
steps = Table(
    "steps",
    metadata,
    Column("step_id", Integer, primary_key=True, autoincrement=True),
    Column("claim_id", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("text", String, nullable=False),
    Column("created_at", String, nullable=False),
)
decisions = Table(
    "decisions",
    metadata,
    Column("claim_id", String, primary_key=True),
    Column("decision", String, nullable=False),
    Column("payout", Float, nullable=False),
    Column("reasons", String, nullable=False),
    Column("policy_section", String, nullable=False),
    Column("decided_at", String, nullable=False),
)
payments = Table(
    "payments",
    metadata,
    Column("claim_id", String, primary_key=True),
    Column("amount", Float, nullable=False),
    Column("paid_at", String, nullable=False),
    Column("signed_off_by", String),
    Column("signed_off_at", String),
)
sign_offs = Table(
    "sign_offs",
    metadata,
    Column("claim_id", String, primary_key=True),
    Column("handler_id", String, nullable=False),
    Column("signed_off_at", String, nullable=False),
)
model_calls = Table(
    "model_calls",
    metadata,
    Column("call_id", Integer, primary_key=True, autoincrement=True),
    Column("claim_id", String, nullable=False),
    Column("run_id", String),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("cost", Float, nullable=False),
    Column("seconds", Float, nullable=False),
    Column("created_at", String, nullable=False),
)
work_times = Table(
    "work_times",
    metadata,
    Column("work_id", Integer, primary_key=True, autoincrement=True),
    Column("claim_id", String, nullable=False),
    Column("run_id", String),
    Column("started_at", String, nullable=False),
    Column("finished_at", String, nullable=False),
    Column("seconds", Float, nullable=False),
)
runs = Table(
    "runs",
    metadata,
    Column("run_id", Integer, primary_key=True, autoincrement=True),
    Column("cap", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("summary", String),
    Column("started_at", String, nullable=False),
    Column("finished_at", String),
)
todos = Table(
    "todos",
    metadata,
    Column("run_id", Integer, primary_key=True),
    Column("position", Integer, primary_key=True),
    Column("text", String, nullable=False),
    Column("status", String, nullable=False),
)

RECORDS = {
    "policies.json": (policies, "policy_id"),
    "customers.json": (customers, "customer_id"),
    "handlers.json": (handlers, "handler_id"),
}


def read_data(name: str) -> str:
    """Return the text of one file in data/."""
    return (DATA / name).read_text(encoding="utf-8")


def database_path() -> Path:
    """Return the SQLite database's path, from DATABASE_PATH or the default."""
    return Path(os.getenv("DATABASE_PATH") or DEFAULT_PATH)


def database_url() -> str:
    """Return DATABASE_URL, or the SQLite database's URL when it isn't set."""
    return os.getenv("DATABASE_URL") or f"sqlite:///{database_path()}"


def create(url: str) -> Engine:
    """Create an engine for a database URL without connecting: on Postgres, with
    the psycopg driver, our schema and at most POOL_SIZE connections."""
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        return create_engine(parsed, poolclass=NullPool)
    return create_engine(
        parsed.set(drivername="postgresql+psycopg"),
        pool_size=POOL_SIZE,
        max_overflow=0,
        pool_pre_ping=True,
        connect_args={"options": f"-c search_path={SCHEMA}"},
    )


@cache
def engine_for(url: str) -> Engine:
    """Return the engine for a database URL, creating and seeding the database on
    first use."""
    created = create(url)
    if not inspect(created).has_table("claims"):
        seed(created)
    return created


def engine() -> Engine:
    """Return the engine for this process's database."""
    return engine_for(database_url())


def seed(target: Engine) -> None:
    """Create the tables and load the claims queue and the records behind it."""
    with target.begin() as conn:
        if target.dialect.name == "postgresql":
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        metadata.create_all(conn)
        queue: list[dict[str, Any]] = json.loads(read_data("claims.json"))
        for claim in queue:
            claim.pop("status")
        if queue:
            conn.execute(
                insert(claims),
                [
                    {
                        "claim_id": claim["claim_id"],
                        "customer_id": claim["customer_id"],
                        "policy_id": claim["policy_id"],
                        "status": "waiting",
                        "failures": 0,
                        "data": claim,
                    }
                    for claim in queue
                ],
            )
        for name, (table, key) in RECORDS.items():
            records = json.loads(read_data(name))
            if records:
                conn.execute(
                    insert(table),
                    [{key: record[key], "data": record} for record in records],
                )


def reset() -> str:
    """Delete the database and create it again from data/, returning where."""
    target = create(database_url())
    if target.dialect.name == "postgresql":
        with target.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        where = f"the {SCHEMA} schema on {target.url.host}"
    else:
        database_path().unlink(missing_ok=True)
        where = str(database_path())
    seed(target)
    target.dispose()
    return where
