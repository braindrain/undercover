"""The UnderCover database, seeded from the business data in data/: Supabase's
Postgres when DATABASE_URL is set, and a SQLite file otherwise."""

import json
import os
from functools import cache
from pathlib import Path
from threading import Lock
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Connection,
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
POOL_WAIT = 120

metadata = MetaData()
engine_lock = Lock()


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
    Column("submitted_on", String, nullable=False),
    Column("evaluation_id", String),
    Column("source_claim_id", String),
    Column("data", JSON, nullable=False),
)
policies = record_table("policies", "policy_id")
customers = record_table("customers", "customer_id")
handlers = record_table("handlers", "handler_id")
held_out_claims = record_table("held_out_claims", "claim_id")
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
    Column("run_id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("agents_at_once", Integer, nullable=False),
    Column("claim_set", String),
    Column("tiny", Boolean),
    Column("model", String, nullable=False),
    Column("reasoning_effort", String, nullable=False),
    Column("status", String, nullable=False),
    Column("started_at", String, nullable=False),
    Column("finished_at", String),
)
run_claims = Table(
    "run_claims",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("claim_id", String, primary_key=True),
    Column("position", Integer, nullable=False),
)
handler_decisions = Table(
    "handler_decisions",
    metadata,
    Column("claim_id", String, primary_key=True),
    Column("claim_set", String, nullable=False),
    Column("decision", String, nullable=False),
    Column("payout", Float, nullable=False),
    Column("tiny", Boolean, nullable=False),
)

RECORDS = {
    "policies.json": (policies, "policy_id"),
    "customers.json": (customers, "customer_id"),
    "handlers.json": (handlers, "handler_id"),
}
ANSWER_KEY = {
    "evaluation": "evals/decisions.json",
    "held_out": "evals/held_out_decisions.json",
}
HELD_OUT_CLAIMS = "evals/held_out_claims.json"


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
        pool_timeout=POOL_WAIT,
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
    """Return the engine for this process's database. The lock stops threads racing
    to create it, as `cache` alone wouldn't."""
    with engine_lock:
        return engine_for(database_url())


def seed_evaluations(conn: Connection) -> None:
    """Load the held-out claims, out of the queue, and the handlers' decisions, in
    tables of their own that no claim operation reads."""
    held_out = json.loads(read_data(HELD_OUT_CLAIMS))
    for claim in held_out:
        claim.pop("status")
    conn.execute(
        insert(held_out_claims),
        [{"claim_id": claim["claim_id"], "data": claim} for claim in held_out],
    )
    for claim_set, name in ANSWER_KEY.items():
        conn.execute(
            insert(handler_decisions),
            [
                {
                    "claim_id": decision["claim_id"],
                    "claim_set": claim_set,
                    "decision": decision["decision"],
                    "payout": decision["payout"],
                    "tiny": decision["tiny"],
                }
                for decision in json.loads(read_data(name))
            ],
        )


def seed(target: Engine) -> None:
    """Create the tables and load the claims queue, the records behind it and the
    evaluation sets."""
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
                        "submitted_on": claim["submitted_on"],
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
        seed_evaluations(conn)


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
