"""A run's records: creating a dispatch, loading a run with its claims, totals and
summary, and ending one. A run is a dispatch or an evaluation."""

from datetime import datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import Connection, func, insert, select, update

from backend import db
from backend.models import (
    HeldPayout,
    NewDispatch,
    Run,
    RunClaim,
    RunDetails,
    RunEnd,
    RunKind,
    RunSummary,
)
from backend.records import find, now

NO_TOTALS = {
    "cost": 0.0,
    "model_seconds": 0.0,
    "seconds": 0.0,
    "started_at": None,
    "finished_at": None,
}


def new_id(kind: RunKind) -> str:
    """Return an id for a new run. It's never a count, because it tags traces in
    Langfuse, which a reseed doesn't clear."""
    return f"{kind}-{uuid4().hex[:8]}"


def create_run(
    conn: Connection,
    run_id: str,
    kind: RunKind,
    claim_ids: list[str],
    **settings: object,
) -> Run:
    """Record a run and its claims in order."""
    conn.execute(
        insert(db.runs).values(
            run_id=run_id, kind=kind, status="running", started_at=now(), **settings
        )
    )
    if claim_ids:
        conn.execute(
            insert(db.run_claims),
            [
                {"run_id": run_id, "claim_id": claim_id, "position": position}
                for position, claim_id in enumerate(claim_ids)
            ],
        )
    return Run(**find(conn, db.runs, run_id))


def create_dispatch(conn: Connection, new_dispatch: NewDispatch) -> Run:
    """Record a dispatch of every claim in progress and every waiting claim in the
    queue, oldest submitted first. An evaluation's copies are never in it."""
    chosen = conn.execute(
        select(db.claims.c.claim_id)
        .where(
            db.claims.c.status.in_(["in_progress", "waiting"]),
            db.claims.c.evaluation_id.is_(None),
        )
        .order_by(db.claims.c.submitted_on, db.claims.c.claim_id)
    ).scalars()
    return create_run(
        conn,
        new_id("dispatch"),
        "dispatch",
        list(chosen),
        **new_dispatch.model_dump(),
    )


def claim_ids(conn: Connection, run_id: str) -> list[str]:
    """Return the ids of a run's claims, in order."""
    return list(
        conn.execute(
            select(db.run_claims.c.claim_id)
            .where(db.run_claims.c.run_id == run_id)
            .order_by(db.run_claims.c.position)
        ).scalars()
    )


def model_totals(conn: Connection, run_id: str) -> dict[str, dict]:
    """Return each claim's model cost and seconds in this run, by claim."""
    rows = conn.execute(
        select(
            db.model_calls.c.claim_id,
            func.sum(db.model_calls.c.cost).label("cost"),
            func.sum(db.model_calls.c.seconds).label("model_seconds"),
        )
        .where(db.model_calls.c.run_id == run_id)
        .group_by(db.model_calls.c.claim_id)
    ).mappings()
    return {row["claim_id"]: dict(row) for row in rows}


def work_totals(conn: Connection, run_id: str) -> dict[str, dict]:
    """Return each claim's seconds of work in this run, with its first start and
    last finish, by claim."""
    rows = conn.execute(
        select(
            db.work_times.c.claim_id,
            func.sum(db.work_times.c.seconds).label("seconds"),
            func.min(db.work_times.c.started_at).label("started_at"),
            func.max(db.work_times.c.finished_at).label("finished_at"),
        )
        .where(db.work_times.c.run_id == run_id)
        .group_by(db.work_times.c.claim_id)
    ).mappings()
    return {row["claim_id"]: dict(row) for row in rows}


def load_claims(conn: Connection, run_id: str) -> list[RunClaim]:
    """Return a run's claims in order, with where each one stands and what it took
    in this run. The number of queries doesn't grow with the claims, because the
    run panel polls this on the same connections as the claim agents."""
    rows = conn.execute(
        select(
            db.claims.c.claim_id,
            db.claims.c.status,
            db.claims.c.data,
            db.decisions.c.decision,
            db.decisions.c.payout,
        )
        .join(db.run_claims, db.run_claims.c.claim_id == db.claims.c.claim_id)
        .outerjoin(db.decisions, db.decisions.c.claim_id == db.claims.c.claim_id)
        .where(db.run_claims.c.run_id == run_id)
        .order_by(db.run_claims.c.position)
    ).mappings()
    calls = model_totals(conn, run_id)
    work = work_totals(conn, run_id)
    return [
        RunClaim(
            summary=row["data"]["summary"],
            status=row["status"],
            decision=row["decision"],
            payout=row["payout"],
            **(
                NO_TOTALS
                | {"claim_id": row["claim_id"]}
                | calls.get(row["claim_id"], {})
                | work.get(row["claim_id"], {})
            ),
        )
        for row in rows
    ]


def summarize(claims: list[RunClaim]) -> RunSummary:
    """Build a run's summary from its claims as they stand now, so that a payout
    signed off after the run no longer shows as waiting."""
    decisions = [claim.decision for claim in claims]

    def with_status(status: str) -> list[RunClaim]:
        return [claim for claim in claims if claim.status == status]

    return RunSummary(
        approved=decisions.count("approve"),
        declined=decisions.count("decline"),
        referred=decisions.count("refer"),
        awaiting_sign_off=[
            HeldPayout(claim_id=claim.claim_id, payout=claim.payout or 0.0)
            for claim in with_status("awaiting_sign_off")
        ],
        left_for_handler=[c.claim_id for c in with_status("left_for_handler")],
        in_progress=[c.claim_id for c in with_status("in_progress")],
    )


def run_seconds(claims: list[RunClaim]) -> float:
    """Return the time from the run's first start to its last finish, from the
    work recorded so far."""
    starts = [claim.started_at for claim in claims if claim.started_at]
    finishes = [claim.finished_at for claim in claims if claim.finished_at]
    if not starts:
        return 0.0
    first, last = min(starts), max(finishes)
    elapsed = datetime.fromisoformat(last) - datetime.fromisoformat(first)
    return elapsed.total_seconds()


def load_run(conn: Connection, run_id: str) -> RunDetails:
    """Return a run with its claims, its totals and its summary."""
    claims = load_claims(conn, run_id)
    return RunDetails(
        **find(conn, db.runs, run_id),
        claims=claims,
        model_cost=sum(claim.cost for claim in claims),
        work_seconds=sum(claim.seconds for claim in claims),
        run_seconds=run_seconds(claims),
        summary=summarize(claims),
    )


def latest_dispatch(conn: Connection) -> RunDetails | None:
    """Return the latest dispatch with its claims, or None."""
    run_id = conn.execute(
        select(db.runs.c.run_id)
        .where(db.runs.c.kind == "dispatch")
        .order_by(db.runs.c.started_at.desc())
        .limit(1)
    ).scalar()
    return None if run_id is None else load_run(conn, run_id)


def finish(conn: Connection, run_id: str, status: RunEnd) -> Run:
    """End a running run, or refuse."""
    run = Run(**find(conn, db.runs, run_id))
    if run.status != "running":
        raise HTTPException(409, f"Run {run_id} has already ended.")
    conn.execute(
        update(db.runs)
        .where(db.runs.c.run_id == run_id)
        .values(status=status, finished_at=now())
    )
    return Run(**find(conn, db.runs, run_id))


def stop_running(conn: Connection) -> list[str]:
    """Mark every running run as stopped, returning their ids."""
    stopped = conn.execute(
        update(db.runs)
        .where(db.runs.c.status == "running")
        .values(status="stopped", finished_at=now())
        .returning(db.runs.c.run_id)
    ).scalars()
    return list(stopped)
