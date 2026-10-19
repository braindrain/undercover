"""The UnderCover backend."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from backend import db
from backend.models import (
    Claim,
    ClaimSummary,
    Customer,
    Handler,
    Policy,
    PreviousClaim,
)

INSTRUCTIONS = Path(__file__).parent / "instructions.md"

app = FastAPI(title="UnderCover")


class MarkdownResponse(PlainTextResponse):
    media_type = "text/markdown"


def database() -> Iterator[sqlite3.Connection]:
    """Open the database for one request, committing if the request succeeds."""
    with closing(db.connect()) as conn, conn:
        yield conn


Database = Annotated[sqlite3.Connection, Depends(database)]


def find(conn: sqlite3.Connection, table: str, key: str, value: str) -> sqlite3.Row:
    """Return one row by its id, or raise a 404."""
    row = conn.execute(
        f"SELECT * FROM {table} WHERE {key} = ?",  # noqa: S608
        (value,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"There is no record {value}.")
    return row


def load_claim(conn: sqlite3.Connection, claim_id: str) -> Claim:
    """Return a claim with its decision, payment and sign-off."""
    row = find(conn, "claims", "claim_id", claim_id)
    claim = {**json.loads(row["data"]), "status": row["status"]}
    for name, table in [
        ("decision", "decisions"),
        ("payment", "payments"),
        ("sign_off", "sign_offs"),
    ]:
        extra = conn.execute(
            f"SELECT * FROM {table} WHERE claim_id = ?",  # noqa: S608
            (claim_id,),
        ).fetchone()
        claim[name] = dict(extra) if extra else None
    return Claim.model_validate(claim)


@app.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the backend is running."""
    return {"status": "ok"}


@app.get("/api/claims", operation_id="list_claims")
def list_claims(conn: Database) -> list[ClaimSummary]:
    """List the claims queue with where each claim stands."""
    rows = conn.execute(
        """
        SELECT claims.data, claims.status, customers.data AS customer,
               decisions.decision, decisions.payout
        FROM claims
        JOIN customers
          ON customers.customer_id = json_extract(claims.data, '$.customer_id')
        LEFT JOIN decisions USING (claim_id)
        ORDER BY claims.claim_id
        """
    ).fetchall()
    return [
        ClaimSummary(
            **json.loads(row["data"]),
            customer_name=json.loads(row["customer"])["name"],
            status=row["status"],
            decision=row["decision"],
            payout=row["payout"],
        )
        for row in rows
    ]


@app.get("/api/claims/{claim_id}", operation_id="get_claim")
def get_claim(claim_id: str, conn: Database) -> Claim:
    """Return one claim in full, with the agent's decision."""
    return load_claim(conn, claim_id)


@app.get("/api/policies/{policy_id}", operation_id="get_policy")
def get_policy(policy_id: str, conn: Database) -> Policy:
    """Return one policy."""
    return Policy(**json.loads(find(conn, "policies", "policy_id", policy_id)["data"]))


@app.get("/api/customers/{customer_id}", operation_id="get_customer")
def get_customer(customer_id: str, conn: Database) -> Customer:
    """Return one customer with their contact details and claim history."""
    row = find(conn, "customers", "customer_id", customer_id)
    return Customer(**json.loads(row["data"]))


@app.get("/api/customers/{customer_id}/claims", operation_id="get_claim_history")
def get_claim_history(customer_id: str, conn: Database) -> list[PreviousClaim]:
    """Return a customer's earlier claims, without their contact details."""
    return get_customer(customer_id, conn).previous_claims


@app.get(
    "/api/policy-wording",
    operation_id="get_policy_wording",
    response_class=MarkdownResponse,
)
def get_policy_wording() -> str:
    """Return the policy wording as Markdown."""
    return db.read_data("policy_wording.md")


@app.get(
    "/api/instructions",
    operation_id="get_instructions",
    response_class=MarkdownResponse,
)
def get_instructions() -> str:
    """Return the agent's instructions as Markdown."""
    return INSTRUCTIONS.read_text(encoding="utf-8")


@app.get("/api/handlers", operation_id="list_handlers")
def list_handlers(conn: Database) -> list[Handler]:
    """List the claims handlers."""
    rows = conn.execute("SELECT data FROM handlers ORDER BY handler_id").fetchall()
    return [Handler(**json.loads(row["data"])) for row in rows]
