"""A run's records: creating a dispatch, loading a run with its claims, and ending
one. A run is a dispatch or an evaluation."""

from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import Connection, insert, select, update

from backend import db
from backend.models import NewDispatch, Run, RunClaim, RunDetails, RunEnd, RunKind
from backend.records import find, now


def create_run(
    conn: Connection, kind: RunKind, claim_ids: list[str], **settings: object
) -> Run:
    """Record a run and its claims in order. Its id is new on every run, never a
    count, because it tags traces in Langfuse, which a reseed doesn't clear."""
    run_id = f"{kind}-{uuid4().hex[:8]}"
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
    """Record a dispatch of every claim in progress and every waiting claim, oldest
    submitted first."""
    claim_ids = conn.execute(
        select(db.claims.c.claim_id)
        .where(db.claims.c.status.in_(["in_progress", "waiting"]))
        .order_by(db.claims.c.submitted_on, db.claims.c.claim_id)
    ).scalars()
    return create_run(conn, "dispatch", list(claim_ids), **new_dispatch.model_dump())


def claim_ids(conn: Connection, run_id: str) -> list[str]:
    """Return the ids of a run's claims, in order."""
    return list(
        conn.execute(
            select(db.run_claims.c.claim_id)
            .where(db.run_claims.c.run_id == run_id)
            .order_by(db.run_claims.c.position)
        ).scalars()
    )


def load_claims(conn: Connection, run_id: str) -> list[RunClaim]:
    """Return a run's claims in order, with where each one stands."""
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
    return [
        RunClaim(
            claim_id=row["claim_id"],
            summary=row["data"]["summary"],
            status=row["status"],
            decision=row["decision"],
            payout=row["payout"],
        )
        for row in rows
    ]


def load_run(conn: Connection, run_id: str) -> RunDetails:
    """Return a run with its claims."""
    run = find(conn, db.runs, run_id)
    return RunDetails(**run, claims=load_claims(conn, run_id))


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
