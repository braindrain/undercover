"""The UnderCover backend."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime
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
    NewDecision,
    NewPayment,
    NewSignOff,
    NewStep,
    Policy,
    PreviousClaim,
    Status,
    Step,
)

INSTRUCTIONS = Path(__file__).parent / "instructions.md"
APPROVAL_LIMIT: float = json.loads(db.read_data("company.json"))[
    "payout_approval_limit"
]

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


def now() -> str:
    """Return the time now in UTC, to the second."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def money(amount: float) -> str:
    """Format an amount in US dollars, such as $1,250.00."""
    return f"${amount:,.2f}"


def require_status(claim: Claim, status: Status, action: str) -> None:
    """Refuse an action unless the claim is in the given state."""
    if claim.status != status:
        state = claim.status.replace("_", " ")
        raise HTTPException(
            409, f"{claim.claim_id} is {state}, so it can't be {action}."
        )


def set_status(conn: sqlite3.Connection, claim_id: str, status: Status) -> None:
    """Move a claim to a new state."""
    conn.execute("UPDATE claims SET status = ? WHERE claim_id = ?", (status, claim_id))


def pay(conn: sqlite3.Connection, claim_id: str, amount: float) -> None:
    """Record a payment."""
    conn.execute("INSERT INTO payments VALUES (?, ?, ?)", (claim_id, amount, now()))


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


@app.post("/api/claims/{claim_id}/start", operation_id="start_claim")
def start_claim(claim_id: str, conn: Database) -> Claim:
    """Move a waiting claim to in progress."""
    require_status(load_claim(conn, claim_id), "waiting", "started")
    set_status(conn, claim_id, "in_progress")
    return load_claim(conn, claim_id)


@app.post("/api/claims/{claim_id}/release", operation_id="release_claim")
def release_claim(claim_id: str, conn: Database) -> Claim:
    """Return a claim in progress to waiting, leaving any other claim as it is."""
    if load_claim(conn, claim_id).status == "in_progress":
        set_status(conn, claim_id, "waiting")
    return load_claim(conn, claim_id)


@app.post("/api/claims/{claim_id}/steps", operation_id="record_step")
def record_step(claim_id: str, step: NewStep, conn: Database) -> Step:
    """Record one step the agent took on a claim."""
    find(conn, "claims", "claim_id", claim_id)
    created_at = now()
    cursor = conn.execute(
        "INSERT INTO steps (claim_id, kind, text, created_at) VALUES (?, ?, ?, ?)",
        (claim_id, step.kind, step.text, created_at),
    )
    return Step(
        step_id=cursor.lastrowid or 0,
        kind=step.kind,
        text=step.text,
        created_at=created_at,
    )


@app.get("/api/claims/{claim_id}/steps", operation_id="list_steps")
def list_steps(claim_id: str, conn: Database) -> list[Step]:
    """List the steps the agent took on a claim, oldest first."""
    find(conn, "claims", "claim_id", claim_id)
    rows = conn.execute(
        "SELECT * FROM steps WHERE claim_id = ? ORDER BY step_id", (claim_id,)
    ).fetchall()
    return [Step.model_validate(dict(row)) for row in rows]


@app.post("/api/claims/{claim_id}/decision", operation_id="record_decision")
def record_decision(claim_id: str, decision: NewDecision, conn: Database) -> Claim:
    """Record the decision on a claim in progress, with its payout and reasons."""
    require_status(load_claim(conn, claim_id), "in_progress", "decided")
    if decision.decision == "approve" and decision.payout == 0:
        raise HTTPException(409, "An approval needs a payout above $0.00.")
    if decision.decision != "approve" and decision.payout != 0:
        raise HTTPException(409, f"A {decision.decision} has a payout of $0.00.")
    conn.execute(
        "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?)",
        (
            claim_id,
            decision.decision,
            decision.payout,
            decision.reasons,
            decision.policy_section,
            now(),
        ),
    )
    over_limit = decision.decision == "approve" and decision.payout > APPROVAL_LIMIT
    set_status(conn, claim_id, "awaiting_sign_off" if over_limit else "decided")
    return load_claim(conn, claim_id)


@app.post("/api/claims/{claim_id}/payments", operation_id="pay_claim")
def pay_claim(claim_id: str, payment: NewPayment, conn: Database) -> Claim:
    """Pay an approved claim its payout."""
    claim = load_claim(conn, claim_id)
    if claim.decision is None or claim.decision.decision != "approve":
        raise HTTPException(409, f"{claim_id} has no approval to pay.")
    if claim.payment:
        raise HTTPException(409, f"{claim_id} has already been paid.")
    if claim.decision.payout > APPROVAL_LIMIT and claim.sign_off is None:
        raise HTTPException(
            409,
            f"This payout is over the {money(APPROVAL_LIMIT)} approval limit "
            "and waits for a handler's sign-off.",
        )
    if round(payment.amount, 2) != round(claim.decision.payout, 2):
        raise HTTPException(
            409,
            f"The amount must be the approved payout, {money(claim.decision.payout)}.",
        )
    pay(conn, claim_id, payment.amount)
    return load_claim(conn, claim_id)


@app.post("/api/claims/{claim_id}/sign-off", operation_id="sign_off_claim")
def sign_off_claim(claim_id: str, sign_off: NewSignOff, conn: Database) -> Claim:
    """Sign off an approval over the limit, which pays its payout."""
    require_status(load_claim(conn, claim_id), "awaiting_sign_off", "signed off")
    handler = Handler(
        **json.loads(find(conn, "handlers", "handler_id", sign_off.handler_id)["data"])
    )
    if not handler.can_approve_over_limit:
        raise HTTPException(
            409, f"{handler.name} can't sign off payouts over the limit."
        )
    conn.execute(
        "INSERT INTO sign_offs VALUES (?, ?, ?)", (claim_id, handler.handler_id, now())
    )
    pay(conn, claim_id, find(conn, "decisions", "claim_id", claim_id)["payout"])
    set_status(conn, claim_id, "decided")
    return load_claim(conn, claim_id)
