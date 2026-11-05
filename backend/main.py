"""The UnderCover backend."""

import logging
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import Connection, insert, select, update

from backend import agent_client, db, documents, payout, runs
from backend.documents import DocumentName
from backend.models import (
    FEWEST_AGENTS,
    MOST_AGENTS,
    STARTING_AGENTS,
    Claim,
    ClaimSummary,
    Contents,
    Customer,
    Handler,
    NewDecision,
    NewDispatch,
    NewModelCall,
    NewPayment,
    NewPayout,
    NewSignOff,
    NewStep,
    NewWork,
    Payout,
    Policy,
    PreviousClaim,
    Run,
    RunDetails,
    RunOptions,
    RunResult,
    StartDispatch,
    Started,
    Status,
    Step,
)
from backend.records import find, lookup, now

INSTRUCTIONS = Path(__file__).parent / "instructions" / "claim.md"
MAX_FAILURES = 2

app = FastAPI(title="UnderCover")
log = logging.getLogger("uvicorn.error")


class MarkdownResponse(PlainTextResponse):
    media_type = "text/markdown"


def database() -> Iterator[Connection]:
    """Open the database for one request, committing when the operation returns
    and before the response and any background task."""
    with db.engine().begin() as conn:
        yield conn


Database = Annotated[Connection, Depends(database, scope="function")]


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
    "/api/instructions",
    operation_id="get_instructions",
    response_class=MarkdownResponse,
)
def get_instructions() -> str:
    """Return the claim agent's instructions."""
    return INSTRUCTIONS.read_text(encoding="utf-8")


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


@app.post(
    "/api/claims/{claim_id}/model-calls",
    operation_id="record_model_call",
    status_code=204,
)
def record_model_call(claim_id: str, call: NewModelCall, conn: Database) -> None:
    """Record one model call on a claim, with its run, tokens, cost and seconds."""
    find(conn, db.claims, claim_id)
    conn.execute(
        insert(db.model_calls).values(
            claim_id=claim_id, **call.model_dump(), created_at=now()
        )
    )


@app.post("/api/claims/{claim_id}/work", operation_id="record_work", status_code=204)
def record_work(claim_id: str, work: NewWork, conn: Database) -> None:
    """Record one stretch of the agent's work on a claim, with its run."""
    find(conn, db.claims, claim_id)
    conn.execute(insert(db.work_times).values(claim_id=claim_id, **work.model_dump()))


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
def sign_off_claim(
    claim_id: str, sign_off: NewSignOff, background: BackgroundTasks, conn: Database
) -> Claim:
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
    background.add_task(ask_agent_to_carry_on, claim_id)
    return load_claim(conn, claim_id)


def ask_agent(call: Callable[[], None]) -> None:
    """Make a call to the agent's service that a handler is waiting on, passing on
    its refusal and saying so if it can't be reached."""
    try:
        call()
    except agent_client.Refused as error:
        raise HTTPException(409, str(error)) from error
    except httpx.HTTPError as error:
        raise HTTPException(503, "The agent's service isn't running.") from error


def ask_agent_to_carry_on(claim_id: str) -> None:
    """Ask the agent to pay a claim that was signed off. If it can't be reached,
    the sign-off stands and the agent's next run carries the claim on."""
    try:
        agent_client.carry_on(claim_id)
    except httpx.HTTPError as error:
        log.warning("The agent can't carry on %s yet: %s", claim_id, error)


@app.post("/api/claims/{claim_id}/run", operation_id="run_agent", status_code=202)
def run_agent(claim_id: str, conn: Database) -> Claim:
    """Ask the agent's service to work a waiting claim, in the background."""
    claim = load_claim(conn, claim_id)
    require_status(claim, "waiting", "run")
    ask_agent(partial(agent_client.work, claim_id))
    return claim


@app.get("/api/run-options", operation_id="get_run_options")
def get_run_options() -> RunOptions:
    """Return the fewest, the most and the starting number of agents at once."""
    return RunOptions(
        fewest_agents=FEWEST_AGENTS,
        most_agents=MOST_AGENTS,
        starting_agents=STARTING_AGENTS,
    )


@app.post("/api/dispatches/start", operation_id="start_dispatch", status_code=202)
def start_dispatch(start: StartDispatch) -> Started:
    """Ask the agent's service to start a dispatch, writing nothing here first."""
    ask_agent(partial(agent_client.start_dispatch, start.agents_at_once))
    return Started(status="accepted")


@app.post("/api/dispatches", operation_id="create_dispatch")
def create_dispatch(new_dispatch: NewDispatch, conn: Database) -> Run:
    """Record a dispatch and choose its claims, for the agent's service."""
    return runs.create_dispatch(conn, new_dispatch)


@app.get("/api/dispatches/latest", operation_id="get_latest_dispatch")
def get_latest_dispatch(conn: Database) -> RunDetails | None:
    """Return the latest dispatch with its claims, or null before the first."""
    return runs.latest_dispatch(conn)


@app.get("/api/runs/{run_id}/claims", operation_id="list_run_claims")
def list_run_claims(run_id: str, conn: Database) -> list[str]:
    """List the ids of a run's claims, in the order they're worked."""
    find(conn, db.runs, run_id)
    return runs.claim_ids(conn, run_id)


@app.post("/api/runs/stop", operation_id="stop_running_runs")
def stop_running_runs(conn: Database) -> list[str]:
    """Mark every running run as stopped, and list their ids."""
    return runs.stop_running(conn)


@app.post("/api/runs/{run_id}/finish", operation_id="finish_run")
def finish_run(run_id: str, result: RunResult, conn: Database) -> Run:
    """End a running run as finished or stopped."""
    return runs.finish(conn, run_id, result.status)
