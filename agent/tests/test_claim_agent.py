import itertools
import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agent import agents, api, harness
from agent.tests.stubs import (
    BrokenModel,
    FlakyModel,
    StubModel,
    approve,
    call,
    decision,
    stub,
)
from agent.tools import CLAIM_TOOLS
from backend.main import app

client = TestClient(app)


def claim(claim_id):
    return client.get(f"/api/claims/{claim_id}").json()


def steps(claim_id):
    return client.get(f"/api/claims/{claim_id}/steps").json()


def test_approve_and_pay_straight_after_reading_the_wording():
    model = stub(
        call("get_claim", claim_id="CLM-1001"),
        call("get_policy", policy_id="POL-2001"),
        call("get_claim_history", customer_id="CUS-3001"),
        call("list_sections", document="policy_wording"),
        call("read_section", document="policy_wording", number=3),
        *approve("CLM-1001", 420.0),
    )
    assert agents.work_claim("CLM-1001", model) == "Approved CLM-1001."
    assert claim("CLM-1001")["status"] == "decided"
    assert claim("CLM-1001")["payment"]["amount"] == 420.0
    recorded = steps("CLM-1001")
    assert [step["kind"] for step in recorded] == [
        *["tool_call", "tool_result"] * 4,
        "tool_call",
        "read",
        "tool_call",
        "tool_result",
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


def test_the_payout_tool_uses_the_backends_rules():
    lines = [
        {
            "description": "Hotel",
            "amount": 180.0,
            "quantity": 1,
            "kind": "expense",
            "nights": 0,
            "hours": 0,
        },
        {
            "description": "Meals",
            "amount": 60.0,
            "quantity": 1,
            "kind": "expense",
            "nights": 0,
            "hours": 0,
        },
    ]
    model = stub(
        call(
            "calculate_payout",
            policy_id="POL-2001",
            benefit="trip_delay",
            lines=lines,
            paid_by_others=0,
        ),
        decision("CLM-1001", "decline", 0),
        AIMessage(content="Declined."),
    )
    agents.work_claim("CLM-1001", model)
    recorded = steps("CLM-1001")
    assert recorded[0]["text"].startswith("calculate_payout(policy_id='POL-2001'")
    assert json.loads(recorded[1]["text"]) == {
        "calculation": [
            {"step": "Hotel", "amount": 180.0},
            {"step": "Meals", "amount": 60.0},
        ],
        "payout": 240.0,
        "needs_sign_off": False,
    }
    assert claim("CLM-1001")["status"] == "decided"


def test_an_unknown_section_is_a_plain_result():
    model = stub(
        call("read_section", document="claims_manual", number=99),
        AIMessage(content="Done."),
    )
    agents.work_claim("CLM-1001", model)
    assert steps("CLM-1001")[1]["kind"] == "tool_result"
    assert steps("CLM-1001")[1]["text"] == "Refused: There is no section 99."


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
            [call("get_claim", claim_id="CLM-1001"), *approve("CLM-1001", 420)]
        )
    )
    agents.work_claim("CLM-1001", model)
    assert model.failed
    assert claim("CLM-1001")["status"] == "decided"
    assert [step["kind"] for step in steps("CLM-1001")] == [
        *["tool_call", "tool_result"] * 2,
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
    model = stub(call("get_claim", claim_id="CLM-1001"), *approve("CLM-1001", 420))
    agents.work_claim("CLM-1001", model)
    assert claim("CLM-1001")["status"] == "decided"
    assert "Meridian Air" in steps("CLM-1001")[1]["text"]


def test_a_tool_call_that_never_reaches_the_backend_fails_the_claim(monkeypatch):
    class DeadBackend(TestClient):
        def request(self, method, url, **kwargs):
            if url == "/api/claims/CLM-1001":
                raise httpx.ConnectError("The backend is unreachable.")
            return super().request(method, url, **kwargs)

    monkeypatch.setattr(api, "client", lambda: DeadBackend(app))
    model = stub(call("get_claim", claim_id="CLM-1001"), AIMessage(content="Stuck."))
    with pytest.raises(httpx.ConnectError):
        agents.work_claim("CLM-1001", model)
    assert claim("CLM-1001")["failures"] == 1
    assert steps("CLM-1001")[-1]["text"] == (
        "The agent stopped: The backend is unreachable."
    )


def test_a_claim_agent_that_never_decides_stops_at_the_call_limit():
    model = StubModel(
        messages=(call("get_claim", claim_id="CLM-1001") for _ in itertools.count())
    )
    with pytest.raises(harness.CallLimitReached):
        agents.work_claim("CLM-1001", model)
    assert len(model.prompts) == agents.CLAIM_CALL_LIMIT
    assert claim("CLM-1001")["status"] == "waiting"
    assert claim("CLM-1001")["failures"] == 1


def test_the_agent_never_reads_the_data_files():
    for source in Path(agents.__file__).parent.glob("*.py"):
        assert "data/" not in source.read_text(encoding="utf-8"), source


def test_only_the_api_module_calls_the_backend():
    for source in Path(agents.__file__).parent.glob("*.py"):
        if source.name != "api.py":
            assert not re.search(r"[\"']/api/", source.read_text(encoding="utf-8"))


def test_the_claim_agent_has_no_payment_tool_and_its_instructions_no_payment():
    assert "pay_claim" not in [tool.name for tool in CLAIM_TOOLS]
    text = client.get("/api/instructions/claim").text
    assert not re.search(r"\bpa(y|id|ying|yment)s?\b", text, re.IGNORECASE)


@pytest.mark.parametrize("name", ["claim", "queue"])
def test_the_instructions_name_no_records(name):
    text = client.get(f"/api/instructions/{name}").text
    assert not re.search(r"\b(CLM|CUS|POL)-", text)


def test_model_name(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    default = harness.model()
    assert default.model_name == "openai/gpt-6-luna"
    assert default.reasoning_effort == "low"
    assert default.max_retries == 0
    assert harness.model_settings() == ("openai/gpt-6-luna", "low")
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    chosen = harness.model()
    assert chosen.model_name == "openrouter/free"
    assert chosen.reasoning_effort is None
    assert harness.model_settings() == ("openrouter/free", "none")
