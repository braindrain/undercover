"""Evaluations: making an evaluation's copies of a labelled set, and reading the
answer key. This and `db.py` are the only modules that name the answer key, so that
no claim operation can read it."""

from typing import Any
from uuid import uuid4

from sqlalchemy import Connection, insert, select

from backend import db, runs
from backend.models import NewEvaluation, Run

SOURCES = {"evaluation": db.claims, "held_out": db.held_out_claims}


def set_claim_ids(conn: Connection, new_evaluation: NewEvaluation) -> list[str]:
    """Return the ids of the claims a labelled set holds, whole or just its tiny
    ones, in order."""
    query = select(db.handler_decisions.c.claim_id).where(
        db.handler_decisions.c.claim_set == new_evaluation.claim_set
    )
    if new_evaluation.tiny:
        query = query.where(db.handler_decisions.c.tiny)
    return list(conn.execute(query.order_by(db.handler_decisions.c.claim_id)).scalars())


def copy_row(record: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Return a new waiting claim from a record. Its id is in the row and the
    record, and looks like any other, so that the agent can't tell it's a copy."""
    claim_id = f"CLM-{uuid4().hex[:8].upper()}"
    return {
        "claim_id": claim_id,
        "customer_id": record["customer_id"],
        "policy_id": record["policy_id"],
        "status": "waiting",
        "failures": 0,
        "submitted_on": record["submitted_on"],
        "evaluation_id": run_id,
        "source_claim_id": record["claim_id"],
        "data": {**record, "claim_id": claim_id},
    }


def create_evaluation(conn: Connection, new_evaluation: NewEvaluation) -> Run:
    """Record an evaluation and make its own copies of the set's claims, from the
    records alone, which hold no status or decision."""
    source = SOURCES[new_evaluation.claim_set]
    source_ids = set_claim_ids(conn, new_evaluation)
    records = dict(
        conn.execute(
            select(source.c.claim_id, source.c.data).where(
                source.c.claim_id.in_(source_ids)
            )
        ).all()
    )
    run_id = runs.new_id("evaluation")
    copies = [copy_row(records[claim_id], run_id) for claim_id in source_ids]
    conn.execute(insert(db.claims), copies)
    return runs.create_run(
        conn,
        run_id,
        "evaluation",
        [copy["claim_id"] for copy in copies],
        **new_evaluation.model_dump(),
    )
