"""Evaluations: making an evaluation's copies of a labelled set, and scoring them
against the answer key. This and `db.py` are the only modules that name the answer
key, so that no claim operation can read it."""

from collections import defaultdict
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import ColumnElement, Connection, RowMapping, func, insert, select

from backend import db, runs, scoring
from backend.models import (
    Evaluation,
    EvaluationClaim,
    EvaluationDetails,
    NewEvaluation,
    Run,
    RunClaim,
)
from backend.scoring import Answer

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


def scored_claims(conn: Connection, condition: ColumnElement[bool]) -> list[RowMapping]:
    """Return each copy with the agent's decision and the handler's."""
    rows = conn.execute(
        select(
            db.claims.c.claim_id,
            db.claims.c.evaluation_id,
            db.claims.c.source_claim_id,
            db.decisions.c.decision,
            db.decisions.c.payout,
            db.decisions.c.reasons,
            db.handler_decisions.c.decision.label("handler_decision"),
            db.handler_decisions.c.payout.label("handler_payout"),
        )
        .join(
            db.handler_decisions,
            db.handler_decisions.c.claim_id == db.claims.c.source_claim_id,
        )
        .outerjoin(db.decisions, db.decisions.c.claim_id == db.claims.c.claim_id)
        .where(condition)
    ).mappings()
    return list(rows)


def answers(row: RowMapping) -> tuple[Answer, Answer]:
    """Return a copy's agent and handler answers."""
    return (
        scoring.agent_answer(row["decision"], row["payout"]),
        Answer(row["handler_decision"], row["handler_payout"]),
    )


def model_costs(conn: Connection) -> dict[str, float]:
    """Return each evaluation's model cost, by run."""
    rows = conn.execute(
        select(db.model_calls.c.run_id, func.sum(db.model_calls.c.cost))
        .join(db.runs, db.runs.c.run_id == db.model_calls.c.run_id)
        .where(db.runs.c.kind == "evaluation")
        .group_by(db.model_calls.c.run_id)
    )
    return {run_id: cost for run_id, cost in rows}


def work_times(conn: Connection) -> dict[str, tuple[float, float]]:
    """Return each evaluation's seconds of work and whole-run time, by run."""
    rows = conn.execute(
        select(
            db.work_times.c.run_id,
            func.sum(db.work_times.c.seconds),
            func.min(db.work_times.c.started_at),
            func.max(db.work_times.c.finished_at),
        )
        .join(db.runs, db.runs.c.run_id == db.work_times.c.run_id)
        .where(db.runs.c.kind == "evaluation")
        .group_by(db.work_times.c.run_id)
    )
    return {
        run_id: (seconds, runs.elapsed(first, last))
        for run_id, seconds, first, last in rows
    }


def list_evaluations(conn: Connection) -> list[Evaluation]:
    """Return the evaluations, newest first, with every measure. The number of
    queries doesn't grow with the evaluations."""
    rows = conn.execute(
        select(db.runs)
        .where(db.runs.c.kind == "evaluation")
        .order_by(db.runs.c.started_at.desc())
    ).mappings()
    by_run: dict[str, list[tuple[Answer, Answer]]] = defaultdict(list)
    for row in scored_claims(conn, db.claims.c.evaluation_id.is_not(None)):
        by_run[row["evaluation_id"]].append(answers(row))
    costs = model_costs(conn)
    times = work_times(conn)
    return [
        Evaluation(
            **run,
            claim_count=len(by_run[run["run_id"]]),
            measures=scoring.measures(
                by_run[run["run_id"]],
                costs.get(run["run_id"], 0.0),
                *times.get(run["run_id"], (0.0, 0.0)),
            ),
        )
        for run in rows
    ]


def evaluation_claim(claim: RunClaim, row: RowMapping) -> EvaluationClaim:
    """Return a claim of a run beside the handler's decision."""
    return EvaluationClaim(
        claim_id=claim.claim_id,
        source_claim_id=row["source_claim_id"],
        summary=claim.summary,
        status=claim.status,
        handler_decision=row["handler_decision"],
        handler_payout=row["handler_payout"],
        agent_decision=claim.decision,
        agent_payout=claim.payout,
        reason=row["reasons"],
        cost=claim.cost,
        seconds=claim.seconds,
        mistake_cost=scoring.mistake_cost(*answers(row)),
        miss=not scoring.is_accurate(*answers(row)),
    )


def get_evaluation(conn: Connection, run_id: str) -> EvaluationDetails:
    """Return an evaluation with its measures, and each claim beside the handler's
    decision, in the set's order."""
    run = runs.load_run(conn, run_id)
    if run.kind != "evaluation":
        raise HTTPException(404, f"There is no record {run_id}.")
    scored = {
        row["claim_id"]: row
        for row in scored_claims(conn, db.claims.c.evaluation_id == run_id)
    }
    claims = [evaluation_claim(claim, scored[claim.claim_id]) for claim in run.claims]
    return EvaluationDetails(
        **run.model_dump(exclude={"claims", "summary"}),
        claim_count=len(claims),
        measures=scoring.measures(
            [answers(row) for row in scored.values()],
            run.model_cost,
            run.work_seconds,
            run.run_seconds,
        ),
        claims=claims,
    )
