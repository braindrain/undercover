import itertools

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent import agents, api, harness
from agent.tests.stubs import (
    BrokenModel,
    StoppingModel,
    StubModel,
    approve,
    call,
    stub,
)
from backend.main import app

client = TestClient(app)


def claim(claim_id):
    return client.get(f"/api/claims/{claim_id}").json()


def saved(claim_id):
    """The claim's thread as the checkpointer has it."""
    config = harness.thread_config(claim(claim_id)["thread_id"])
    return agents.claim_graph().get_state(config)


def sign_off(claim_id, handler_id="HND-01"):
    response = client.post(
        f"/api/claims/{claim_id}/sign-off", json={"handler_id": handler_id}
    )
    response.raise_for_status()


@pytest.fixture
def payments(monkeypatch):
    """Record every payment the graph asks the backend for."""
    made = []
    real = api.pay_claim
    monkeypatch.setattr(
        api, "pay_claim", lambda *args: made.append(args) or real(*args)
    )
    return made


def hold(claim_id="CLM-1002", payout=1150.0):
    """Work a claim to an approval over the limit, which waits for a sign-off."""
    agents.work_claim(claim_id, stub(*approve(claim_id, payout)))


def test_an_approval_over_the_limit_waits_for_a_sign_off(payments):
    hold()
    assert saved("CLM-1002").next == ("sign_off",)
    assert saved("CLM-1002").interrupts[0].value == {
        "claim_id": "CLM-1002",
        "payout": 1150.0,
    }
    assert claim("CLM-1002")["status"] == "awaiting_sign_off"
    assert claim("CLM-1002")["payment"] is None
    assert claim("CLM-1002")["failures"] == 0
    assert payments == []


def test_carrying_on_before_the_sign_off_keeps_waiting(payments):
    hold()
    assert agents.carry_on("CLM-1002") == (
        "CLM-1002 is waiting for a handler's sign-off."
    )
    assert payments == []


def test_a_sign_off_resumes_the_claim_and_pays_it_once(payments):
    hold()
    sign_off("CLM-1002")
    assert claim("CLM-1002")["status"] == "in_progress"
    model = stub()
    agents.carry_on("CLM-1002", model)
    assert model.prompts == []
    paid = claim("CLM-1002")
    assert paid["status"] == "decided"
    assert paid["payment"]["amount"] == 1150.0
    assert paid["payment"]["signed_off_by"] == "HND-01"
    assert paid["payment"]["signed_off_at"] == paid["sign_off"]["signed_off_at"]
    assert saved("CLM-1002").values["signed_off_by"] == "HND-01"
    assert agents.carry_on("CLM-1002", model) == ("CLM-1002 has nothing left to do.")
    assert payments == [("CLM-1002", 1150.0)]


def test_a_claim_stopped_partway_carries_on_from_its_last_step():
    first = StoppingModel(
        messages=iter(
            [
                call("get_claim", claim_id="CLM-1001"),
                call("get_policy", policy_id="POL-2001"),
            ]
        ),
        replies=2,
    )
    with pytest.raises(KeyboardInterrupt):
        agents.work_claim("CLM-1001", first)
    stopped = claim("CLM-1001")
    assert stopped["status"] == "in_progress"
    assert stopped["failures"] == 0
    assert saved("CLM-1001").next == ("agent",)

    second = stub(*approve("CLM-1001", 420.0))
    agents.carry_on("CLM-1001", second)
    prompt = second.prompts[0]
    results = [m for m in prompt if isinstance(m, ToolMessage)]
    assert [m.name for m in results] == ["get_claim", "get_policy"]
    assert "Meridian Air" in str(results[0].content)
    done = claim("CLM-1001")
    assert done["thread_id"] == stopped["thread_id"]
    assert done["status"] == "decided"
    assert done["failures"] == 0
    assert done["decision"]["payout"] == 420.0
    assert done["payment"]["amount"] == 420.0


def test_a_claim_stopped_before_its_first_step_starts_on_its_thread():
    started = client.post("/api/claims/CLM-1001/start").json()
    model = stub(*approve("CLM-1001", 420.0))
    agents.carry_on("CLM-1001", model)
    assert [type(m) for m in model.prompts[0][1:]] == [HumanMessage]
    assert claim("CLM-1001")["status"] == "decided"
    assert claim("CLM-1001")["thread_id"] == started["thread_id"]


def test_a_claim_that_fails_starts_afresh_on_a_new_thread():
    with pytest.raises(RuntimeError):
        agents.work_claim("CLM-1001", BrokenModel(messages=iter([])))
    failed = claim("CLM-1001")
    assert failed["status"] == "waiting"
    assert failed["failures"] == 1
    model = stub(*approve("CLM-1001", 420.0))
    agents.work_claim("CLM-1001", model)
    again = claim("CLM-1001")
    assert again["thread_id"] != failed["thread_id"]
    assert [m.content for m in model.prompts[0][1:]] == ["Decide claim CLM-1001."]
    assert again["status"] == "decided"


def test_a_repeated_payment_counts_as_done(payments):
    agents.work_claim("CLM-1001", stub(*approve("CLM-1001", 420.0)))
    config = harness.thread_config(claim("CLM-1001")["thread_id"])
    graph = agents.claim_graph()
    [before_pay] = [
        snapshot
        for snapshot in graph.get_state_history(config)
        if snapshot.next == ("pay",)
    ]
    graph.invoke(None, before_pay.config, durability="sync")
    assert payments == [("CLM-1001", 420.0), ("CLM-1001", 420.0)]
    assert claim("CLM-1001")["payment"]["amount"] == 420.0
    assert claim("CLM-1001")["status"] == "decided"


def test_the_call_limit_counts_across_a_restart():
    def get_claim_forever():
        return (call("get_claim", claim_id="CLM-1001") for _ in itertools.count())

    first = StoppingModel(messages=get_claim_forever(), replies=10)
    with pytest.raises(KeyboardInterrupt):
        agents.work_claim("CLM-1001", first)
    second = StubModel(messages=get_claim_forever())
    with pytest.raises(harness.CallLimitReached):
        agents.carry_on("CLM-1001", second)
    assert len(first.prompts) + len(second.prompts) == agents.CLAIM_CALL_LIMIT
    assert claim("CLM-1001")["failures"] == 1


def test_a_decline_ends_without_paying(payments):
    model = stub(
        call(
            "record_decision",
            claim_id="CLM-1001",
            decision="decline",
            payout=0,
            reasons="Not covered.",
            policy_section="Section 9, General exclusions",
        ),
        AIMessage(content="Declined."),
    )
    agents.work_claim("CLM-1001", model)
    assert claim("CLM-1001")["status"] == "decided"
    assert saved("CLM-1001").next == ()
    assert payments == []
