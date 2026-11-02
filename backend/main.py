"""The UnderCover backend."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import Connection, Table, delete, func, insert, select, update

from agent.agents import work_claim
from backend import db, documents, payout
from backend.documents import DocumentName
from backend.models import (
    Claim,
    ClaimSummary,
    Contents,
    Customer,
    Handler,
    NewDecision,
    NewPayment,
    NewPayout,
    NewRun,
    NewSignOff,
    NewStep,
    Payout,
    Policy,
    PreviousClaim,
    Run,
    RunSummary,
    Status,
    Step,
    Todo,
    TodoList,
)

INSTRUCTIONS = Path(__file__).parent / "instructions"
MAX_FAILURES = 2

app = FastAPI(title="UnderCover")


class MarkdownResponse(PlainTextResponse):
    media_type = "text/markdown"


def database() -> Iterator[Connection]:
    """Open the database for one request, committing when the operation returns
    and before the response and any background task."""
    with db.engine().begin() as conn:
        yield conn


Database = Annotated[Connection, Depends(database, scope="function")]


def lookup(conn: Connection, table: Table, record_id: str | int) -> dict | None:
    """Return one row by its id, or None."""
    [key] = table.primary_key.columns
    row = conn.execute(select(table).where(key == record_id)).mappings().first()
    return dict(row) if row else None


def find(conn: Connection, table: Table, record_id: str | int) -> dict:
    """Return one row by its id, or raise a 404."""
    row = lookup(conn, table, record_id)
    if row is None:
        raise HTTPException(404, f"There is no record {record_id}.")
    return row


def load_claim(conn: Connection, claim_id: str) -> Claim:
    """Return a claim with its decision, payment and sign-off."""
    row = find(conn, db.claims, claim_id)
    return Claim.model_validate(
        {
            **row["data"],
            "status": row["status"],
            "failures": row["failures"],
            "thread_id": row["thread_id"],
            "decision": lookup(conn, db.decisions, claim_id),
            "payment": lookup(conn, db.payments, claim_id),
            "sign_off": lookup(conn, db.sign_offs, claim_id),
        }
    )


def now() -> str:
    """Return the time now in UTC, to the second."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def money(amount: float) -> str:
    """Format an amount in US dollars, such as $1,250.00."""
    return f"${amount:,.2f}"


def same_amount(first: float, second: float) -> bool:
    """Return whether two amounts are the same to the cent."""
    return round(first, 2) == round(second, 2)


def require_status(claim: Claim, status: Status, action: str) -> None:
    """Refuse an action unless the claim is in the given state."""
    if claim.status != status:
        state = claim.status.replace("_", " ")
        raise HTTPException(
            409, f"{claim.claim_id} is {state}, so it can't be {action}."
        )


def update_claim(conn: Connection, claim_id: str, **values: object) -> None:
    """Change a claim's status, failures or thread."""
    conn.execute(
        update(db.claims).where(db.claims.c.claim_id == claim_id).values(**values)
    )


def load_customer(conn: Connection, customer_id: str) -> Customer:
    """Return a customer with their contact details and claim history."""
    return Customer(**find(conn, db.customers, customer_id)["data"])


def load_run(conn: Connection, run_id: int) -> Run:
    """Return a run with its to-do list, in order."""
    run = find(conn, db.runs, run_id)
    todos = conn.execute(
        select(db.todos.c.text, db.todos.c.status)
        .where(db.todos.c.run_id == run_id)
        .order_by(db.todos.c.position)
    ).mappings()
    return Run(**run, todos=[Todo.model_validate(dict(todo)) for todo in todos])


def running_run(conn: Connection, run_id: int) -> Run:
    """Return a run that is still running, or refuse."""
    run = load_run(conn, run_id)
    if run.status != "running":
        raise HTTPException(409, f"Run {run_id} has finished.")
    return run


@app.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the backend is running."""
    return {"status": "ok"}


@app.get("/api/claims", operation_id="list_claims")
def list_claims(conn: Database) -> list[ClaimSummary]:
    """List the claims queue with where each claim stands."""
    rows = conn.execute(
        select(
            db.claims.c.data,
            db.claims.c.status,
            db.customers.c.data.label("customer"),
            db.decisions.c.decision,
            db.decisions.c.payout,
        )
        .join(db.customers, db.customers.c.customer_id == db.claims.c.customer_id)
        .outerjoin(db.decisions, db.decisions.c.claim_id == db.claims.c.claim_id)
        .order_by(db.claims.c.claim_id)
    ).mappings()
    return [
        ClaimSummary(
            **row["data"],
            customer_name=row["customer"]["name"],
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
    return Policy(**find(conn, db.policies, policy_id)["data"])


@app.post("/api/policies/{policy_id}/payout", operation_id="work_out_payout")
def work_out_payout(policy_id: str, claimed: NewPayout, conn: Database) -> Payout:
    """Work out the payout on an approved claim under a policy, step by step."""
    return payout.work_out_payout(get_policy(policy_id, conn), claimed)


@app.get("/api/customers/{customer_id}", operation_id="get_customer")
def get_customer(customer_id: str, conn: Database) -> Customer:
    """Return one customer with their contact details and claim history."""
    return load_customer(conn, customer_id)


@app.get("/api/customers/{customer_id}/claims", operation_id="get_claim_history")
def get_claim_history(customer_id: str, conn: Database) -> list[PreviousClaim]:
    """Return a customer's earlier claims, without their contact details."""
    return load_customer(conn, customer_id).previous_claims


@app.get(
    "/api/documents/{document}",
    operation_id="get_document",
    response_class=MarkdownResponse,
)
def get_document(document: DocumentName) -> str:
    """Return the policy wording or the claims manual in full as Markdown."""
    return documents.read(document)


@app.get("/api/documents/{document}/sections", operation_id="list_sections")
def list_sections(document: DocumentName) -> Contents:
    """Return a document's title, its opening lines and its numbered sections."""
    return documents.contents(document)


@app.get(
    "/api/documents/{document}/sections/{number}",
    operation_id="read_section",
    response_class=MarkdownResponse,
)
def read_section(document: DocumentName, number: int) -> str:
    """Return one numbered section of a document as Markdown, from its heading."""
    found = documents.sections(document)
    if number not in found:
        raise HTTPException(404, f"There is no section {number}.")
    return found[number]


@app.get(
    "/api/instructions/{name}",
    operation_id="get_instructions",
    response_class=MarkdownResponse,
)
def get_instructions(name: Literal["queue", "claim"]) -> str:
    """Return the agent's instructions for running the queue or working a claim."""
    return (INSTRUCTIONS / f"{name}.md").read_text(encoding="utf-8")


@app.get("/api/handlers", operation_id="list_handlers")
def list_handlers(conn: Database) -> list[Handler]:
    """List the claims handlers."""
    rows = conn.execute(select(db.handlers.c.data).order_by(db.handlers.c.handler_id))
    return [Handler(**data) for data in rows.scalars()]


@app.post("/api/claims/{claim_id}/start", operation_id="start_claim")
def start_claim(claim_id: str, conn: Database) -> Claim:
    """Move a waiting claim to in progress, on a new thread for the agent's work."""
    require_status(load_claim(conn, claim_id), "waiting", "started")
    update_claim(
        conn, claim_id, status="in_progress", thread_id=f"{claim_id}-{uuid4().hex[:8]}"
    )
    return load_claim(conn, claim_id)


@app.post("/api/claims/{claim_id}/release", operation_id="release_claim")
def release_claim(claim_id: str, conn: Database) -> Claim:
    """Count a failure on an undecided claim in progress, leaving others as is."""
    claim = load_claim(conn, claim_id)
    if claim.status == "in_progress" and claim.decision is None:
        failures = claim.failures + 1
        status = "left_for_handler" if failures >= MAX_FAILURES else "waiting"
        update_claim(conn, claim_id, status=status, failures=failures)
    return load_claim(conn, claim_id)


@app.post("/api/claims/{claim_id}/steps", operation_id="record_step")
def record_step(claim_id: str, step: NewStep, conn: Database) -> Step:
    """Record one step the agent took on a claim."""
    find(conn, db.claims, claim_id)
    created_at = now()
    step_id = conn.execute(
        insert(db.steps)
        .values(
            claim_id=claim_id, kind=step.kind, text=step.text, created_at=created_at
        )
        .returning(db.steps.c.step_id)
    ).scalar_one()
    return Step(
        step_id=step_id,
        kind=step.kind,
        text=step.text,
        created_at=created_at,
    )


@app.get("/api/claims/{claim_id}/steps", operation_id="list_steps")
def list_steps(claim_id: str, conn: Database) -> list[Step]:
    """List the steps the agent took on a claim, oldest first."""
    find(conn, db.claims, claim_id)
    rows = conn.execute(
        select(db.steps)
        .where(db.steps.c.claim_id == claim_id)
        .order_by(db.steps.c.step_id)
    ).mappings()
    return [Step.model_validate(dict(row)) for row in rows]


@app.post("/api/claims/{claim_id}/decision", operation_id="record_decision")
def record_decision(claim_id: str, decision: NewDecision, conn: Database) -> Claim:
    """Record the decision on a claim in progress, with its payout and reasons."""
    claim = load_claim(conn, claim_id)
    if claim.decision:
        recorded = claim.decision
        if recorded.decision == decision.decision and same_amount(
            recorded.payout, decision.payout
        ):
            return claim
        raise HTTPException(
            409,
            f"{claim_id} has already been decided: {recorded.decision}, "
            f"{money(recorded.payout)}.",
        )
    require_status(claim, "in_progress", "decided")
    if decision.decision == "approve" and decision.payout == 0:
        raise HTTPException(409, "An approval needs a payout above $0.00.")
    if decision.decision != "approve" and decision.payout != 0:
        raise HTTPException(409, f"A {decision.decision} has a payout of $0.00.")
    conn.execute(
        insert(db.decisions).values(
            claim_id=claim_id, **decision.model_dump(), decided_at=now()
        )
    )
    if decision.decision != "approve":
        update_claim(conn, claim_id, status="decided")
    elif decision.payout > payout.APPROVAL_LIMIT:
        update_claim(conn, claim_id, status="awaiting_sign_off")
    return load_claim(conn, claim_id)


@app.post("/api/claims/{claim_id}/payments", operation_id="pay_claim")
def pay_claim(claim_id: str, payment: NewPayment, conn: Database) -> Claim:
    """Pay an approved claim its payout, which decides it."""
    claim = load_claim(conn, claim_id)
    if claim.decision is None or claim.decision.decision != "approve":
        raise HTTPException(409, f"{claim_id} has no approval to pay.")
    if claim.payment:
        if same_amount(claim.payment.amount, payment.amount):
            return claim
        raise HTTPException(
            409, f"{claim_id} has already been paid {money(claim.payment.amount)}."
        )
    if claim.decision.payout > payout.APPROVAL_LIMIT and claim.sign_off is None:
        raise HTTPException(
            409,
            f"This payout is over the {money(payout.APPROVAL_LIMIT)} approval limit "
            "and waits for a handler's sign-off.",
        )
    if not same_amount(payment.amount, claim.decision.payout):
        raise HTTPException(
            409,
            f"The amount must be the approved payout, {money(claim.decision.payout)}.",
        )
    sign_off = claim.sign_off
    conn.execute(
        insert(db.payments).values(
            claim_id=claim_id,
            amount=payment.amount,
            paid_at=now(),
            signed_off_by=sign_off.handler_id if sign_off else None,
            signed_off_at=sign_off.signed_off_at if sign_off else None,
        )
    )
    update_claim(conn, claim_id, status="decided")
    return load_claim(conn, claim_id)


@app.post("/api/claims/{claim_id}/sign-off", operation_id="sign_off_claim")
def sign_off_claim(claim_id: str, sign_off: NewSignOff, conn: Database) -> Claim:
    """Sign off an approval over the limit, for the agent to pay."""
    require_status(load_claim(conn, claim_id), "awaiting_sign_off", "signed off")
    handler = Handler(**find(conn, db.handlers, sign_off.handler_id)["data"])
    if not handler.can_approve_over_limit:
        raise HTTPException(
            409, f"{handler.name} can't sign off payouts over the limit."
        )
    conn.execute(
        insert(db.sign_offs).values(
            claim_id=claim_id, handler_id=handler.handler_id, signed_off_at=now()
        )
    )
    update_claim(conn, claim_id, status="in_progress")
    return load_claim(conn, claim_id)


@app.post("/api/claims/{claim_id}/run", operation_id="run_agent", status_code=202)
def run_agent(claim_id: str, background: BackgroundTasks, conn: Database) -> Claim:
    """Start the agent on a waiting claim, in the background."""
    require_status(load_claim(conn, claim_id), "waiting", "run")
    background.add_task(work_claim, claim_id)
    return load_claim(conn, claim_id)


@app.post("/api/runs", operation_id="start_run")
def start_run(new_run: NewRun, conn: Database) -> Run:
    """Start a run of the agent over the queue, capped at a number of claims."""
    run_id = conn.execute(
        insert(db.runs)
        .values(cap=new_run.cap, status="running", started_at=now())
        .returning(db.runs.c.run_id)
    ).scalar_one()
    return load_run(conn, run_id)


@app.get("/api/runs/latest", operation_id="get_latest_run")
def get_latest_run(conn: Database) -> Run | None:
    """Return the latest run with its to-do list, or null before the first run."""
    run_id = conn.execute(select(func.max(db.runs.c.run_id))).scalar()
    return None if run_id is None else load_run(conn, run_id)


@app.put("/api/runs/{run_id}/todos", operation_id="set_todos")
def set_todos(run_id: int, todo_list: TodoList, conn: Database) -> Run:
    """Replace a running run's to-do list."""
    running_run(conn, run_id)
    conn.execute(delete(db.todos).where(db.todos.c.run_id == run_id))
    if todo_list.todos:
        conn.execute(
            insert(db.todos),
            [
                {"run_id": run_id, "position": position, **todo.model_dump()}
                for position, todo in enumerate(todo_list.todos)
            ],
        )
    return load_run(conn, run_id)


@app.post("/api/runs/{run_id}/finish", operation_id="finish_run")
def finish_run(run_id: int, run_summary: RunSummary, conn: Database) -> Run:
    """Finish a running run with its summary for the handlers."""
    running_run(conn, run_id)
    conn.execute(
        update(db.runs)
        .where(db.runs.c.run_id == run_id)
        .values(status="finished", summary=run_summary.summary, finished_at=now())
    )
    return load_run(conn, run_id)
