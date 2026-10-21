import itertools
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.messages import AIMessage

import backend.main
from agent import agents, api, harness
from agent.tests.stubs import (
    BrokenModel,
    FlakyModel,
    StubModel,
    approve_and_pay,
    call,
    decision,
    stub,
)
from backend.main import app

client = TestClient(app)


def claim(claim_id):
    return client.get(f"/api/claims/{claim_id}").json()


def steps(claim_id):
    return client.get(f"/api/claims/{claim_id}/steps").json()


def test_approve_and_pay_after_reading_the_wording():
    model = stub(
        call("get_claim", claim_id="CLM-1001"),
        call("get_policy", policy_id="POL-2001"),
        call("get_claim_history", customer_id="CUS-3001"),
        call("list_sections", document="policy_wording"),
        call("read_section", document="policy_wording", number=3),
        *approve_and_pay("CLM-1001", 420.0),
    )
    assert agents.work_claim("CLM-1001", model) == "Approved CLM-1001."
    assert claim("CLM-1001")["status"] == "decided"
    assert claim("CLM-1001")["payment"]["amount"] == 420.0
    recorded = steps("CLM-1001")
    assert [step["kind"] for step in recorded] == [
        *["tool_call", "tool_result"] * 4,
        "tool_call",
        "read",
        *["tool_call", "tool_result"] * 2,
        "reply",
    ]
    assert recorded[0]["text"] == "get_claim(claim_id='CLM-1001')"
    assert "Meridian Air" in recorded[1]["text"]
    assert '"title": "Trip Delay"' in recorded[7]["text"]
    assert recorded[8]["text"] == ("read_section(document='policy_wording', number=3)")
    heading, _, section = recorded[9]["text"].partition("\n\n")
    assert heading == "Policy wording: 3. Trip Delay"
    assert section == client.get("/api/documents/policy_wording/sections/3").text
    assert recorded[-1]["text"] == "Approved CLM-1001."


def test_an_unknown_section_is_a_plain_result():
    model = stub(
        call("read_section", document="claims_manual", number=99),
        AIMessage(content="Done."),
    )
    agents.work_claim("CLM-1001", model)
    assert steps("CLM-1001")[1]["kind"] == "tool_result"
    assert steps("CLM-1001")[1]["text"] == "Refused: There is no section 99."


def test_payment_over_the_limit_is_refused():
    agents.work_claim("CLM-1002", stub(*approve_and_pay("CLM-1002", 1150.0)))
    assert claim("CLM-1002")["status"] == "awaiting_sign_off"
    assert claim("CLM-1002")["payment"] is None
    assert steps("CLM-1002")[3]["text"] == (
        "Refused: This payout is over the $1,000.00 approval limit "
        "and waits for a handler's sign-off."
    )


def test_decline():
    model = stub(decision("CLM-1001", "decline", 0), AIMessage(content="Declined."))
    agents.work_claim("CLM-1001", model)
    assert claim("CLM-1001")["status"] == "decided"
    assert claim("CLM-1001")["decision"]["decision"] == "decline"
    assert claim("CLM-1001")["payment"] is None


def test_a_run_without_a_decision_counts_a_failure():
    agents.work_claim("CLM-1001", stub(AIMessage(content="I can't decide.")))
    assert claim("CLM-1001")["status"] == "waiting"
    assert claim("CLM-1001")["failures"] == 1


def test_the_system_prompt_is_the_claim_instructions_alone():
    model = stub(AIMessage(content="Done."))
    agents.work_claim("CLM-1001", model)
    prompt = model.prompts[0][0].content
    assert prompt == client.get("/api/instructions/claim").text
    assert "Policy Wording" not in prompt


def test_a_model_call_that_fails_once_is_retried():
    model = FlakyModel(
        messages=iter(
            [call("get_claim", claim_id="CLM-1001"), *approve_and_pay("CLM-1001", 420)]
        )
    )
    agents.work_claim("CLM-1001", model)
    assert model.failed
    assert claim("CLM-1001")["status"] == "decided"
    assert [step["kind"] for step in steps("CLM-1001")] == [
        *["tool_call", "tool_result"] * 3,
        "reply",
    ]


def test_a_model_that_always_fails_counts_a_failure_each_run():
    with pytest.raises(RuntimeError):
        agents.work_claim("CLM-1001", BrokenModel(messages=iter([])))
    assert claim("CLM-1001")["status"] == "waiting"
    assert claim("CLM-1001")["failures"] == 1
    assert steps("CLM-1001")[-1]["kind"] == "error"
    assert steps("CLM-1001")[-1]["text"] == (
        "The agent stopped: The model is unavailable."
    )
    with pytest.raises(RuntimeError):
        agents.work_claim("CLM-1001", BrokenModel(messages=iter([])))
    assert claim("CLM-1001")["status"] == "left_for_handler"
    assert claim("CLM-1001")["failures"] == 2


def test_a_tool_call_that_cant_reach_the_backend_is_retried(monkeypatch):
    failures = iter([True])

    class FlakyBackend(TestClient):
        def request(self, method, url, **kwargs):
            if url == "/api/claims/CLM-1001" and next(failures, False):
                raise httpx.ConnectError("The backend is unreachable.")
            return super().request(method, url, **kwargs)

    monkeypatch.setattr(api, "client", lambda: FlakyBackend(app))
    model = stub(
        call("get_claim", claim_id="CLM-1001"), *approve_and_pay("CLM-1001", 420)
    )
    agents.work_claim("CLM-1001", model)
    assert claim("CLM-1001")["status"] == "decided"
    assert "Meridian Air" in steps("CLM-1001")[1]["text"]


def test_a_tool_call_that_never_reaches_the_backend_tells_the_model(monkeypatch):
    class DeadBackend(TestClient):
        def request(self, method, url, **kwargs):
            if url == "/api/claims/CLM-1001":
                raise httpx.ConnectError("The backend is unreachable.")
            return super().request(method, url, **kwargs)

    monkeypatch.setattr(api, "client", lambda: DeadBackend(app))
    model = stub(call("get_claim", claim_id="CLM-1001"), AIMessage(content="Stuck."))
    agents.work_claim("CLM-1001", model)
    assert steps("CLM-1001")[1]["text"].startswith(
        "Tool 'get_claim' failed after 3 attempts with ConnectError"
    )


def test_a_claim_agent_that_never_decides_stops_at_the_call_limit():
    model = StubModel(
        messages=(call("get_claim", claim_id="CLM-1001") for _ in itertools.count())
    )
    with pytest.raises(ModelCallLimitExceededError):
        agents.work_claim("CLM-1001", model)
    assert len(model.prompts) == agents.CLAIM_CALL_LIMIT
    assert claim("CLM-1001")["status"] == "waiting"
    assert claim("CLM-1001")["failures"] == 1


def test_run_agent_starts_a_run(monkeypatch):
    runs = []
    monkeypatch.setattr(backend.main, "work_claim", lambda *args: runs.append(args))
    response = client.post("/api/claims/CLM-1001/run")
    assert response.status_code == 202
    assert runs[0][0] == "CLM-1001"


def test_run_agent_needs_a_waiting_claim():
    client.post("/api/claims/CLM-1001/start")
    response = client.post("/api/claims/CLM-1001/run")
    assert response.status_code == 409
    assert response.json()["detail"] == "CLM-1001 is in progress, so it can't be run."


def test_the_agent_never_reads_the_data_files():
    for source in Path(agents.__file__).parent.glob("*.py"):
        assert "data/" not in source.read_text(encoding="utf-8"), source


def test_only_the_api_module_calls_the_backend():
    for source in Path(agents.__file__).parent.glob("*.py"):
        if source.name != "api.py":
            assert not re.search(r"[\"']/api/", source.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["claim", "queue"])
def test_the_instructions_name_no_records(name):
    text = client.get(f"/api/instructions/{name}").text
    assert not re.search(r"\b(CLM|CUS|POL)-", text)


def test_model_name(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    default = harness.model()
    assert default.model_name == "deepseek/deepseek-v4.1-flash"
    assert default.reasoning_effort == "low"
    assert default.max_retries == 0
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    assert harness.model().model_name == "openrouter/free"
