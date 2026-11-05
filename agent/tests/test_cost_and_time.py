import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from sqlalchemy import select

from agent import agents, harness
from agent.tests.stubs import (
    BrokenModel,
    FailingModel,
    StoppingModel,
    approve,
    call,
    decision,
    stub,
    usage,
)
from backend import db
from backend.main import app

client = TestClient(app)


def rows(table):
    with db.engine().connect() as conn:
        return [dict(row) for row in conn.execute(select(table)).mappings()]


def runs_of(table):
    return [row["run_id"] for row in rows(table)]


def test_each_reply_records_its_tokens_and_cost():
    model = stub(
        call("get_claim", claim_id="CLM-1001"),
        decision("CLM-1001", "decline", 0),
        AIMessage(content="Declined.", response_metadata=usage(2000, 50, 0.01)),
    )
    agents.work_claim("CLM-1001", model)
    calls = rows(db.model_calls)
    assert [(c["input_tokens"], c["output_tokens"], c["cost"]) for c in calls] == [
        (1000, 100, 0.002),
        (1000, 100, 0.002),
        (2000, 50, 0.01),
    ]
    assert {c["claim_id"] for c in calls} == {"CLM-1001"}
    assert all(c["seconds"] >= 0 for c in calls)


def test_a_reply_with_no_usage_counts_as_zero_and_keeps_the_claim_going():
    agents.work_claim(
        "CLM-1001",
        stub(
            AIMessage(
                content="",
                tool_calls=decision("CLM-1001", "decline", 0).tool_calls,
            ),
            AIMessage(content="Declined."),
        ),
    )
    assert [(c["input_tokens"], c["cost"]) for c in rows(db.model_calls)] == [
        (0, 0.0),
        (0, 0.0),
    ]
    assert client.get("/api/claims/CLM-1001").json()["status"] == "decided"


def test_usage_with_nulls_counts_as_zero():
    reply = AIMessage(
        content="",
        response_metadata={"token_usage": {"prompt_tokens": 5, "cost": None}},
    )
    assert harness.usage(reply) == {
        "input_tokens": 5,
        "output_tokens": 0,
        "cost": 0.0,
    }


def test_a_claim_with_a_run_records_under_it():
    agents.work_claim(
        "CLM-1001", stub(*approve("CLM-1001", 420.0)), run_id="dispatch-1"
    )
    assert set(runs_of(db.model_calls)) == {"dispatch-1"}
    assert runs_of(db.work_times) == ["dispatch-1"]


def test_a_claim_with_no_run_records_with_none():
    agents.work_claim("CLM-1001", stub(*approve("CLM-1001", 420.0)))
    assert set(runs_of(db.model_calls)) == {None}
    assert runs_of(db.work_times) == [None]


def test_a_claim_carried_on_in_a_later_run_charges_that_run():
    first = StoppingModel(
        messages=iter([call("get_claim", claim_id="CLM-1001")]), replies=1
    )
    with pytest.raises(KeyboardInterrupt):
        agents.work_claim("CLM-1001", first, run_id="dispatch-1")
    agents.carry_on("CLM-1001", stub(*approve("CLM-1001", 420.0)), run_id="dispatch-2")
    assert runs_of(db.model_calls) == ["dispatch-1", "dispatch-2", "dispatch-2"]
    assert runs_of(db.work_times) == ["dispatch-1", "dispatch-2"]


def test_a_failed_attempt_keeps_its_calls_and_work_in_its_run():
    failing = FailingModel(
        messages=iter([call("get_claim", claim_id="CLM-1001")]), replies=1
    )
    with pytest.raises(RuntimeError):
        agents.work_claim("CLM-1001", failing, run_id="dispatch-1")
    assert runs_of(db.model_calls) == ["dispatch-1"]
    assert runs_of(db.work_times) == ["dispatch-1"]
    agents.work_claim(
        "CLM-1001", stub(*approve("CLM-1001", 420.0)), run_id="dispatch-1"
    )
    assert runs_of(db.model_calls) == ["dispatch-1"] * 3
    assert runs_of(db.work_times) == ["dispatch-1"] * 2


def test_a_claim_whose_model_fails_at_once_still_records_its_work():
    with pytest.raises(RuntimeError):
        agents.work_claim(
            "CLM-1001", BrokenModel(messages=iter([])), run_id="dispatch-1"
        )
    assert rows(db.model_calls) == []
    assert runs_of(db.work_times) == ["dispatch-1"]


def test_a_wait_for_a_sign_off_is_not_counted(monkeypatch):
    clock = {"seconds": 0}

    def tick():
        clock["seconds"] += 1
        return clock["seconds"]

    monkeypatch.setattr(agents, "monotonic", tick)
    agents.work_claim(
        "CLM-1002", stub(*approve("CLM-1002", 1150.0)), run_id="dispatch-1"
    )
    clock["seconds"] += 3600
    client.post(
        "/api/claims/CLM-1002/sign-off", json={"handler_id": "HND-01"}
    ).raise_for_status()
    agents.carry_on("CLM-1002", stub(), run_id="dispatch-2")
    work = rows(db.work_times)
    assert [row["run_id"] for row in work] == ["dispatch-1", "dispatch-2"]
    assert all(0 < row["seconds"] < 60 for row in work)
