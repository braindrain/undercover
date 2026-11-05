import ast
import re
import runpy
import sys
from pathlib import Path

import dotenv
import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError
from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy import update

from agent import agents, api, dispatcher, harness
from agent.tests.stubs import approve, call, decision, swarm_model
from backend import db
from backend.main import app

client = TestClient(app)
AGENT = Path(agents.__file__).parent


def claim(claim_id):
    return client.get(f"/api/claims/{claim_id}").json()


def steps(claim_id):
    return client.get(f"/api/claims/{claim_id}/steps").json()


def latest():
    return client.get("/api/dispatches/latest").json()


def statuses():
    return {c["claim_id"]: c["status"] for c in client.get("/api/claims").json()}


def keep_only(*claim_ids):
    """Leave only these claims waiting, so that a dispatch works just them."""
    with db.engine().begin() as conn:
        conn.execute(
            update(db.claims)
            .where(db.claims.c.claim_id.not_in(claim_ids))
            .values(status="decided")
        )


@pytest.fixture
def swarm(monkeypatch):
    """Give every claim agent one shared stub model, built from scripts."""

    def use(scripts=None, together=1):
        model = swarm_model(scripts, together)
        monkeypatch.setattr(harness, "model", lambda: model)
        return model

    return use


@pytest.fixture
def payments(monkeypatch):
    """Record every payment the graph asks the backend for."""
    made = []
    real = api.pay_claim
    monkeypatch.setattr(
        api, "pay_claim", lambda *args: made.append(args) or real(*args)
    )
    return made


@pytest.mark.parametrize(("agents_at_once", "peak"), [(3, 3), (1, 1)])
def test_never_more_than_n_claims_at_once(swarm, agents_at_once, peak):
    keep_only(*[f"CLM-100{n}" for n in range(1, 9)])
    model = swarm(together=agents_at_once)
    dispatcher.dispatch(agents_at_once)
    assert model.gauge.peak == peak
    assert len(latest()["claims"]) == 8
    assert all(c["status"] == "decided" for c in latest()["claims"])
    assert latest()["status"] == "finished"


def test_a_dispatch_of_the_whole_queue_at_25(swarm):
    model = swarm(together=25)
    dispatcher.dispatch(25)
    assert model.gauge.peak == 25
    assert set(statuses().values()) == {"decided"}


def test_a_dispatch_records_its_model_and_effort(swarm, monkeypatch):
    swarm()
    keep_only("CLM-1001")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    dispatcher.dispatch(3)
    assert latest()["model"] == "openai/gpt-6-luna"
    assert latest()["reasoning_effort"] == "low"
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    dispatcher.dispatch(3)
    assert latest()["model"] == "openrouter/free"
    assert latest()["reasoning_effort"] == "none"


def test_a_dispatch_with_nothing_to_work_finishes_at_once(swarm):
    model = swarm()
    keep_only()
    dispatcher.dispatch(3)
    assert latest()["claims"] == []
    assert latest()["status"] == "finished"
    assert model.prompts == []


def test_a_dispatch_leaves_held_and_decided_claims_alone(swarm):
    keep_only("CLM-1001", "CLM-1002")
    swarm({"CLM-1002": approve("CLM-1002", 1150.0)})
    dispatcher.dispatch(2)
    held = claim("CLM-1002")
    assert held["status"] == "awaiting_sign_off"
    model = swarm()
    dispatcher.dispatch(2)
    assert latest()["claims"] == []
    assert model.prompts == []
    assert claim("CLM-1002")["thread_id"] == held["thread_id"]


@pytest.mark.parametrize("error", [ModelRateLimitError, ModelTimeoutError])
def test_a_rate_limit_carries_on_as_the_same_attempt(swarm, error, payments):
    keep_only("CLM-1001", "CLM-1003", "CLM-1004")
    limits = [error("Slow down.") for _ in range(harness.MODEL_RETRIES + 1)]
    model = swarm(
        {
            "CLM-1001": [
                call("get_claim", claim_id="CLM-1001"),
                *limits,
                *approve("CLM-1001", 420.0),
            ]
        }
    )
    dispatcher.dispatch(3)
    done = claim("CLM-1001")
    assert done["status"] == "decided"
    assert done["failures"] == 0
    assert done["decision"]["payout"] == 420.0
    assert payments == [("CLM-1001", 420.0)]
    assert [s["text"] for s in steps("CLM-1001") if s["kind"] == "error"] == [
        "The agent stopped: Slow down."
    ]
    last_prompt = [p for p in model.prompts if "CLM-1001" in str(p[1].content)][-1]
    results = [m for m in last_prompt if isinstance(m, ToolMessage)]
    assert [m.name for m in results] == ["get_claim", "record_decision"]
    assert latest()["status"] == "finished"


def always_limited(claim_id, error=ModelRateLimitError):
    attempts = (harness.MODEL_RETRIES + 1) * (harness.WORK_RETRIES + 1)
    return [error("Slow down.") for _ in range(attempts)]


def test_retries_running_out_leave_the_claim_and_never_stop_the_dispatch(swarm):
    # Several claims at once: with one, LangGraph takes a path that hid its bug.
    keep_only("CLM-1001", "CLM-1003", "CLM-1004")
    swarm({"CLM-1001": always_limited("CLM-1001")})
    dispatcher.dispatch(3)
    stuck = claim("CLM-1001")
    assert stuck["status"] == "in_progress"
    assert stuck["failures"] == 0
    assert claim("CLM-1003")["status"] == "decided"
    assert claim("CLM-1004")["status"] == "decided"
    assert latest()["status"] == "finished"


def test_the_next_dispatch_carries_the_claim_on_to_one_decision(swarm, payments):
    keep_only("CLM-1001", "CLM-1003")
    swarm(
        {
            "CLM-1001": [
                call("get_claim", claim_id="CLM-1001"),
                *always_limited("CLM-1001"),
            ]
        }
    )
    dispatcher.dispatch(2)
    stuck = claim("CLM-1001")
    model = swarm({"CLM-1001": approve("CLM-1001", 420.0)})
    dispatcher.dispatch(2)
    done = claim("CLM-1001")
    assert [c["claim_id"] for c in latest()["claims"]] == ["CLM-1001"]
    assert done["thread_id"] == stuck["thread_id"]
    assert done["failures"] == 0
    assert done["status"] == "decided"
    assert payments == [("CLM-1001", 420.0)]
    assert any(isinstance(m, ToolMessage) for m in model.prompts[0])


def broken(claim_id):
    return [RuntimeError("Broken.") for _ in range(harness.MODEL_RETRIES + 1)]


def test_a_claim_that_fails_is_tried_once_more_on_a_new_thread(swarm):
    keep_only("CLM-1001", "CLM-1003")
    swarm({"CLM-1001": [*broken("CLM-1001"), *approve("CLM-1001", 420.0)]})
    threads = []
    real = agents.run_thread

    def run_thread(record, *args):
        if record["claim_id"] == "CLM-1001":
            threads.append(record["thread_id"])
        return real(record, *args)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(agents, "run_thread", run_thread)
        dispatcher.dispatch(2)
    done = claim("CLM-1001")
    assert done["status"] == "decided"
    assert done["failures"] == 1
    assert len(set(threads)) == 2
    assert claim("CLM-1003")["status"] == "decided"
    assert latest()["status"] == "finished"


def test_a_claim_that_fails_twice_is_left_for_a_handler(swarm):
    keep_only("CLM-1001", "CLM-1003")
    swarm({"CLM-1001": [*broken("CLM-1001"), *broken("CLM-1001")]})
    dispatcher.dispatch(2)
    left = claim("CLM-1001")
    assert left["status"] == "left_for_handler"
    assert left["failures"] == 2
    assert claim("CLM-1003")["status"] == "decided"
    assert latest()["status"] == "finished"


def test_a_claim_with_one_failure_is_left_after_its_next(swarm):
    keep_only("CLM-1001")
    client.post("/api/claims/CLM-1001/start")
    client.post("/api/claims/CLM-1001/release")
    model = swarm({"CLM-1001": broken("CLM-1001")})
    dispatcher.dispatch(2)
    assert claim("CLM-1001")["status"] == "left_for_handler"
    assert len(model.prompts) == harness.MODEL_RETRIES + 1


def test_a_claim_that_ends_with_no_decision_is_tried_once_more(swarm):
    keep_only("CLM-1001", "CLM-1003")
    swarm(
        {
            "CLM-1001": [
                AIMessage(content="I can't decide."),
                decision("CLM-1001", "decline", 0),
                AIMessage(content="Declined."),
            ]
        }
    )
    dispatcher.dispatch(2)
    assert claim("CLM-1001")["status"] == "decided"
    assert claim("CLM-1001")["failures"] == 1


def test_a_held_claim_doesnt_pause_the_dispatch_and_pays_once(swarm, payments):
    keep_only("CLM-1002", "CLM-1003")
    swarm({"CLM-1002": approve("CLM-1002", 1150.0)})
    dispatcher.dispatch(2)
    assert claim("CLM-1002")["status"] == "awaiting_sign_off"
    assert claim("CLM-1003")["status"] == "decided"
    assert latest()["status"] == "finished"
    assert payments == []
    client.post(
        "/api/claims/CLM-1002/sign-off", json={"handler_id": "HND-01"}
    ).raise_for_status()
    agents.carry_on("CLM-1002")
    assert claim("CLM-1002")["status"] == "decided"
    assert payments == [("CLM-1002", 1150.0)]


def hold_and_sign_off(swarm):
    keep_only("CLM-1002")
    swarm({"CLM-1002": approve("CLM-1002", 1150.0)})
    dispatcher.dispatch(2)
    client.post(
        "/api/claims/CLM-1002/sign-off", json={"handler_id": "HND-01"}
    ).raise_for_status()


def test_a_claim_being_carried_on_after_a_sign_off_is_skipped(swarm, payments):
    hold_and_sign_off(swarm)
    with agents.holding("CLM-1002"):
        dispatcher.dispatch(2)
        assert claim("CLM-1002")["status"] == "in_progress"
        assert payments == []
    agents.carry_on("CLM-1002")
    assert payments == [("CLM-1002", 1150.0)]


def test_a_sign_off_during_a_dispatchs_work_on_the_claim_does_nothing(swarm, payments):
    hold_and_sign_off(swarm)
    with agents.holding("CLM-1002"):
        assert agents.carry_on("CLM-1002") == "CLM-1002 is already being worked."
    assert payments == []
    agents.work("CLM-1002", {"run_id": "dispatch-1", "kind": "dispatch"})
    assert payments == [("CLM-1002", 1150.0)]


def test_a_claim_held_by_someone_else_isnt_started(swarm):
    swarm()
    with agents.holding("CLM-1001"):
        assert agents.work_claim("CLM-1001") == "CLM-1001 is already being worked."
    assert claim("CLM-1001")["status"] == "waiting"


def test_a_claim_signed_off_while_the_service_was_down_is_paid_next(swarm, payments):
    hold_and_sign_off(swarm)
    dispatcher.dispatch(2)
    assert claim("CLM-1002")["status"] == "decided"
    assert payments == [("CLM-1002", 1150.0)]


def test_work_finds_nothing_to_do_on_a_decided_claim(swarm):
    keep_only()
    assert agents.work("CLM-1001", {"run_id": "dispatch-1", "kind": "dispatch"}) == (
        "CLM-1001 is decided, so there's nothing to do."
    )


def test_a_run_that_fails_outright_is_stopped(swarm, monkeypatch):
    swarm()

    def no_claims(run_id):
        raise RuntimeError("The backend failed.")

    monkeypatch.setattr(api, "list_run_claims", no_claims)
    with pytest.raises(RuntimeError):
        dispatcher.dispatch(3)
    assert latest()["status"] == "stopped"


def test_each_claim_is_a_trace_of_its_own_with_its_session_and_tag(swarm, monkeypatch):
    keep_only("CLM-1001", "CLM-1003")
    roots = []

    class Roots(BaseCallbackHandler):
        def on_chain_start(self, serialized, inputs, *, parent_run_id=None, **kw):
            if parent_run_id is None:
                roots.append(kw.get("name"))

    tags = []
    real = harness.thread_config

    def thread_config(thread_id, tag=None):
        tags.append(tag)
        return {**real(thread_id, tag), "callbacks": [Roots()]}

    monkeypatch.setattr(harness, "thread_config", thread_config)
    swarm()
    dispatcher.dispatch(2)
    assert roots == ["LangGraph", "LangGraph"]
    assert {tag for tag in tags if tag} == {latest()["run_id"]}


def test_the_dispatcher_only_uses_the_one_function_from_the_claim_agent():
    source = (AGENT / "dispatcher.py").read_text(encoding="utf-8")
    assert set(re.findall(r"\bagents\.(\w+)", source)) == {"work"}
    imported = {
        str(node.module)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(re.search("openai|language_models", name) for name in imported)


def test_the_dispatcher_has_no_checkpointer():
    assert dispatcher.run_graph().checkpointer is None


def test_the_queue_agent_is_gone():
    gone = r"run_queue|write_todos|create_agent|AgentMiddleware|FinishCheck|Refusals"
    for source in AGENT.glob("*.py"):
        assert not re.search(gone, source.read_text(encoding="utf-8")), source


@pytest.fixture
def command(monkeypatch):
    monkeypatch.setattr(dotenv, "load_dotenv", lambda **kwargs: None)

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["agent", *args])
        runpy.run_module("agent", run_name="__main__")

    return run


def test_command_works_one_claim(command, monkeypatch, capsys):
    monkeypatch.setattr(agents, "work_claim", lambda claim_id: f"{claim_id} done.")
    command("CLM-1001")
    assert capsys.readouterr().out == "CLM-1001 done.\n"


def test_command_needs_a_claim_id(command, capsys):
    with pytest.raises(SystemExit):
        command()
    assert "usage: agent" in capsys.readouterr().err
