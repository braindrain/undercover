import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.exceptions import ModelRateLimitError
from sqlalchemy import func, select

from agent import agents, api, dispatcher, harness
from agent.tests.stubs import approve, call, swarm_model
from backend import db
from backend.main import app

client = TestClient(app)
BACKEND = Path(db.__file__).parent
AGENT = Path(agents.__file__).parent


def claim(claim_id):
    return client.get(f"/api/claims/{claim_id}").json()


def queue():
    return client.get("/api/claims").json()


def count(table, *where):
    with db.engine().connect() as conn:
        query = select(func.count()).select_from(table)
        return conn.execute(query.where(*where)).scalar()


def latest_evaluation():
    """The latest evaluation's row, and its copies' ids in order."""
    with db.engine().connect() as conn:
        run = (
            conn.execute(
                select(db.runs)
                .where(db.runs.c.kind == "evaluation")
                .order_by(db.runs.c.started_at.desc())
            )
            .mappings()
            .first()
        )
    assert run
    return dict(run), client.get(f"/api/runs/{run['run_id']}/claims").json()


def create(claim_set="evaluation", tiny=False, agents_at_once=5):
    return client.post(
        "/api/evaluations",
        json={
            "agents_at_once": agents_at_once,
            "claim_set": claim_set,
            "tiny": tiny,
            "model": "openai/gpt-6-luna",
            "reasoning_effort": "low",
        },
    )


@pytest.fixture
def swarm(monkeypatch):
    """Give every claim agent one shared stub model."""

    def use(scripts=None, default=None):
        model = swarm_model(scripts, default=default)
        monkeypatch.setattr(harness, "model", lambda: model)
        return model

    return use


def saved(claim_id):
    config = harness.thread_config(claim(claim_id)["thread_id"])
    return agents.claim_graph().get_state(config)


def test_seeding_stores_the_answer_key_apart_from_the_queue():
    assert [c["claim_id"] for c in queue()] == [
        c["claim_id"] for c in json.loads(db.read_data("claims.json"))
    ]
    assert count(db.held_out_claims) == 25
    assert count(db.handler_decisions) == 50
    for claim_set in ["evaluation", "held_out"]:
        assert (
            count(
                db.handler_decisions,
                db.handler_decisions.c.claim_set == claim_set,
                db.handler_decisions.c.tiny,
            )
            == 5
        )


@pytest.mark.parametrize(
    ("claim_set", "tiny", "copies"),
    [
        ("evaluation", False, 25),
        ("evaluation", True, 5),
        ("held_out", False, 25),
        ("held_out", True, 5),
    ],
)
def test_an_evaluation_gets_copies_of_its_set(claim_set, tiny, copies):
    run = create(claim_set, tiny).json()
    assert run["run_id"].startswith("evaluation-")
    assert run["kind"] == "evaluation"
    assert run["claim_set"] == claim_set
    assert run["tiny"] is tiny
    assert run["agents_at_once"] == 5
    assert run["model"] == "openai/gpt-6-luna"
    assert run["reasoning_effort"] == "low"
    ids = client.get(f"/api/runs/{run['run_id']}/claims").json()
    assert len(ids) == len(set(ids)) == copies
    first = claim(ids[0])
    assert first["claim_id"] == ids[0]
    assert first["status"] == "waiting"
    assert first["decision"] is None
    assert re.fullmatch(r"CLM-[0-9A-F]{8}", ids[0])


def test_a_copy_records_where_it_came_from_only_outside_its_record():
    run_id = create().json()["run_id"]
    [first, *_] = client.get(f"/api/runs/{run_id}/claims").json()
    assert client.get(f"/api/claims/{first}/evaluation").json() == {
        "evaluation_id": run_id,
        "source_claim_id": "CLM-1001",
    }
    assert client.get("/api/claims/CLM-1001/evaluation").json() is None


def test_a_queue_claim_already_decided_copies_clean():
    client.post("/api/claims/CLM-1001/start")
    client.post(
        "/api/claims/CLM-1001/decision",
        json={
            "decision": "decline",
            "payout": 0,
            "reasons": "Not covered.",
            "policy_section": "Section 9, General exclusions",
        },
    )
    run_id = create().json()["run_id"]
    [first, *_] = client.get(f"/api/runs/{run_id}/claims").json()
    copy = claim(first)
    assert copy["status"] == "waiting"
    assert copy["decision"] is None
    assert copy["thread_id"] is None


def test_get_claim_on_a_copy_holds_nothing_from_the_answer_key():
    run_id = create("held_out").json()["run_id"]
    [first, *_] = client.get(f"/api/runs/{run_id}/claims").json()
    copy = claim(first)
    assert set(copy) == set(claim("CLM-1001"))
    text = json.dumps(copy)
    labels = json.loads(db.read_data("evals/held_out_decisions.json"))
    [label] = [d for d in labels if d["claim_id"] == "CLM-1101"]
    assert label["reason"] not in text
    assert label["reference"] not in text
    assert label["tests"] not in text
    assert "CLM-1101" not in text
    assert "evaluation" not in text
    assert "held_out" not in text


def test_the_answer_key_is_named_in_two_modules_only():
    for source in BACKEND.glob("*.py"):
        text = source.read_text(encoding="utf-8")
        if source.name not in {"db.py", "evaluations.py"}:
            assert not re.search("handler_decisions|held_out_claims", text), source
    for source in AGENT.glob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert not re.search("handler_decisions|held_out_claims", text), source
        assert not re.search(r"\"GET\",[^\n]*/api/evaluations", text), source


def test_an_evaluation_leaves_the_queue_as_it_was(swarm):
    swarm()
    before = queue()
    dispatcher.evaluate(5, "evaluation", False)
    assert queue() == before
    run, ids = latest_evaluation()
    assert run["status"] == "finished"
    assert {claim(claim_id)["status"] for claim_id in ids} == {"decided"}


def test_a_held_approval_counts_as_finished_with_its_progress_cleared(swarm):
    swarm(default=lambda claim_id: approve(claim_id, 1150.0))
    dispatcher.evaluate(5, "held_out", True)
    run, ids = latest_evaluation()
    assert run["status"] == "finished"
    assert len(ids) == 5
    for claim_id in ids:
        held = claim(claim_id)
        assert held["status"] == "awaiting_sign_off"
        assert held["payment"] is None
        assert held["decision"]["payout"] == 1150.0
        assert not saved(claim_id).values


def test_a_copy_that_finishes_has_its_progress_cleared(swarm):
    swarm()
    dispatcher.evaluate(5, "held_out", True)
    _, ids = latest_evaluation()
    assert not saved(ids[0]).values


def test_a_later_dispatch_never_picks_up_a_copy():
    run_id = create(tiny=True).json()["run_id"]
    ids = client.get(f"/api/runs/{run_id}/claims").json()
    client.post(f"/api/claims/{ids[0]}/start")
    client.post("/api/runs/stop")
    dispatch = api.create_dispatch(3, "openai/gpt-6-luna", "low")
    chosen = api.list_run_claims(dispatch["run_id"])
    assert len(chosen) == 25
    assert not set(ids) & set(chosen)


@pytest.mark.parametrize(
    ("status", "path", "body"),
    [
        ("awaiting_sign_off", "sign-off", {"handler_id": "HND-01"}),
        ("waiting", "run", None),
    ],
)
def test_a_copy_cant_be_signed_off_or_run(swarm, agent_calls, status, path, body):
    swarm(default=lambda claim_id: approve(claim_id, 1150.0))
    if status == "awaiting_sign_off":
        dispatcher.evaluate(5, "held_out", True)
        _, ids = latest_evaluation()
    else:
        run_id = create(tiny=True).json()["run_id"]
        ids = client.get(f"/api/runs/{run_id}/claims").json()
    before = claim(ids[0])
    response = client.post(f"/api/claims/{ids[0]}/{path}", json=body)
    assert response.status_code == 409
    assert "is a copy in an evaluation" in response.json()["detail"]
    assert claim(ids[0]) == before
    assert agent_calls == []


def test_problems_are_put_right_in_an_evaluation_as_in_a_dispatch(swarm):
    run = create("held_out", True, 2).json()
    ids = client.get(f"/api/runs/{run['run_id']}/claims").json()
    tries = (harness.MODEL_RETRIES + 1) * (harness.WORK_RETRIES + 1)
    broken = [RuntimeError("Broken.")] * (harness.MODEL_RETRIES + 1)
    swarm(
        {
            ids[0]: [
                call("get_claim", claim_id=ids[0]),
                ModelRateLimitError("Slow down."),
                *approve(ids[0], 420.0),
            ],
            ids[1]: [ModelRateLimitError("Slow down.")] * tries,
            ids[2]: [*broken, *approve(ids[2], 300.0)],
        }
    )
    dispatcher.work_run(run)
    assert claim(ids[0])["status"] == "decided"
    assert claim(ids[0])["failures"] == 0
    assert claim(ids[1])["status"] == "in_progress"
    assert claim(ids[2])["status"] == "decided"
    assert claim(ids[2])["failures"] == 1
    with db.engine().connect() as conn:
        charged = conn.execute(
            select(db.model_calls.c.run_id).where(db.model_calls.c.claim_id == ids[0])
        ).scalars()
        assert set(charged) == {run["run_id"]}
        worked = conn.execute(
            select(db.work_times.c.run_id).where(db.work_times.c.claim_id == ids[0])
        ).scalars()
        assert set(worked) == {run["run_id"]}
