import runpy
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage

from agent import main
from backend.main import app

client = TestClient(app)


class StubModel(GenericFakeChatModel):
    """A chat model that gives scripted replies in place of a real model."""

    prompts: list[list[BaseMessage]] = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.prompts.append(messages)
        return super()._generate(messages, stop, run_manager, **kwargs)


class BrokenModel(StubModel):
    """A chat model that fails, as a real one can."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError("The model is unavailable.")


def call(name, **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": name}])


def stub(*replies):
    return StubModel(messages=iter(replies))


def decision(claim_id, verdict, payout):
    return call(
        "record_decision",
        claim_id=claim_id,
        decision=verdict,
        payout=payout,
        reasons="The reasons.",
        policy_section="Section 3, Trip Delay",
    )


@pytest.fixture(autouse=True)
def local_backend(monkeypatch):
    monkeypatch.setattr(main, "backend", lambda: TestClient(app))


def test_approve_and_pay():
    model = stub(
        call("get_claim", claim_id="CLM-1001"),
        call("get_policy", policy_id="POL-2001"),
        call("get_claim_history", customer_id="CUS-3001"),
        decision("CLM-1001", "approve", 420.0),
        call("pay_claim", claim_id="CLM-1001", amount=420.0),
        AIMessage(content="Approved and paid $420.00."),
    )
    assert main.run("CLM-1001", "thread-1", model) == "Approved and paid $420.00."
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["status"] == "decided"
    assert claim["payment"]["amount"] == 420.0
    steps = client.get("/api/claims/CLM-1001/steps").json()
    assert [step["kind"] for step in steps] == [
        *["tool_call", "tool_result"] * 5,
        "reply",
    ]
    assert steps[0]["text"] == "get_claim(claim_id='CLM-1001')"
    assert "Meridian Air" in steps[1]["text"]
    assert steps[-1]["text"] == "Approved and paid $420.00."


def test_payment_over_the_limit_is_refused():
    model = stub(
        decision("CLM-1002", "approve", 1150.0),
        call("pay_claim", claim_id="CLM-1002", amount=1150.0),
        AIMessage(content="Approved, waiting for sign-off."),
    )
    main.run("CLM-1002", "thread-1", model)
    claim = client.get("/api/claims/CLM-1002").json()
    assert claim["status"] == "awaiting_sign_off"
    assert claim["payment"] is None
    steps = client.get("/api/claims/CLM-1002/steps").json()
    assert steps[3]["text"] == (
        "Refused: This payout is over the $1,000.00 approval limit "
        "and waits for a handler's sign-off."
    )


def test_decline():
    model = stub(decision("CLM-1001", "decline", 0), AIMessage(content="Declined."))
    main.run("CLM-1001", "thread-1", model)
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["status"] == "decided"
    assert claim["decision"]["decision"] == "decline"
    assert claim["payment"] is None


def test_a_run_without_a_decision_gives_the_claim_back():
    main.run("CLM-1001", "thread-1", stub(AIMessage(content="I can't decide.")))
    assert client.get("/api/claims/CLM-1001").json()["status"] == "waiting"


def test_a_failed_run_gives_the_claim_back():
    with pytest.raises(RuntimeError):
        main.run("CLM-1001", "thread-1", BrokenModel(messages=iter([])))
    assert client.get("/api/claims/CLM-1001").json()["status"] == "waiting"
    steps = client.get("/api/claims/CLM-1001/steps").json()
    assert steps[-1] == {
        **steps[-1],
        "kind": "error",
        "text": "The agent stopped: The model is unavailable.",
    }


def test_system_prompt_is_the_instructions_then_the_wording():
    model = stub(AIMessage(content="Done."))
    main.run("CLM-1001", "thread-1", model)
    instructions = client.get("/api/instructions").text
    wording = client.get("/api/policy-wording").text
    assert model.prompts[0][0].content == f"{instructions}\n\n{wording}"


def test_run_agent_starts_a_run(monkeypatch):
    runs = []
    monkeypatch.setattr(main, "run", lambda *args: runs.append(args))
    response = client.post("/api/claims/CLM-1001/run")
    assert response.status_code == 202
    assert runs[0][0] == "CLM-1001"


def test_run_agent_needs_a_waiting_claim():
    client.post("/api/claims/CLM-1001/start")
    response = client.post("/api/claims/CLM-1001/run")
    assert response.status_code == 409
    assert response.json()["detail"] == "CLM-1001 is in progress, so it can't be run."


def test_the_agent_never_reads_the_data_files():
    for source in Path(main.__file__).parent.glob("*.py"):
        assert "data/" not in source.read_text(encoding="utf-8"), source


def test_model_name(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    assert main.model().model_name == "openai/gpt-6-luna"
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    assert main.model().model_name == "openrouter/free"


def test_waiting_claims():
    assert len(main.waiting_claims()) == 25
    client.post("/api/claims/CLM-1001/start")
    assert "CLM-1001" not in main.waiting_claims()


def test_run_queue_works_each_claim_in_turn():
    model = stub(
        decision("CLM-1001", "decline", 0),
        AIMessage(content="Declined CLM-1001."),
        decision("CLM-1002", "refer", 0),
        AIMessage(content="Referred CLM-1002."),
    )
    assert list(main.run_queue(["CLM-1001", "CLM-1002"], model)) == [
        ("CLM-1001", "Declined CLM-1001."),
        ("CLM-1002", "Referred CLM-1002."),
    ]
    claims = client.get("/api/claims").json()
    assert [claim["decision"] for claim in claims[:2]] == ["decline", "refer"]


def test_run_queue_carries_on_after_a_failure():
    client.post("/api/claims/CLM-1001/start")
    model = stub(decision("CLM-1002", "decline", 0), AIMessage(content="Declined."))
    results = list(main.run_queue(["CLM-1001", "CLM-1002"], model))
    assert results[0][0] == "CLM-1001"
    assert results[0][1].startswith("Failed: Client error '409 Conflict'")
    assert results[1] == ("CLM-1002", "Declined.")


def test_command_runs_the_queue(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["agent"])
    monkeypatch.setattr(main, "waiting_claims", lambda: ["CLM-1001", "CLM-1002"])
    monkeypatch.setattr(
        main, "run_queue", lambda ids: ((claim_id, "Done.") for claim_id in ids)
    )
    runpy.run_module("agent", run_name="__main__")
    assert capsys.readouterr().out == (
        "2 claims waiting\nCLM-1001: Done.\nCLM-1002: Done.\n"
    )


def test_command_runs_one_claim(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["agent", "CLM-1001"])
    monkeypatch.setattr(main, "run", lambda claim_id, thread_id: f"{claim_id} done.")
    runpy.run_module("agent", run_name="__main__")
    assert capsys.readouterr().out == "CLM-1001 done.\n"
