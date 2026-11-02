import runpy
import sys

import dotenv
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent import agents, api, tools
from agent.tests.stubs import BrokenModel, approve_and_pay, call, stub
from backend.main import app

client = TestClient(app)


def claim(claim_id):
    return client.get(f"/api/claims/{claim_id}").json()


def latest_run():
    return client.get("/api/runs/latest").json()


def statuses():
    return {c["claim_id"]: c["status"] for c in client.get("/api/claims").json()}


def work(claim_id):
    return call("work_claim", claim_id=claim_id)


def todos(*items):
    return call(
        "write_todos",
        todos=[{"text": text, "status": status} for text, status in items],
    )


@pytest.fixture
def stand_in(monkeypatch):
    """Replace the claim agent with one that declines each claim, or fails on a
    claim as many times as it's told to."""
    failures: dict[str, int] = {}
    worked: list[str] = []

    def work_claim(claim_id, llm=None):
        worked.append(claim_id)
        client.post(f"/api/claims/{claim_id}/start").raise_for_status()
        try:
            if failures.get(claim_id):
                failures[claim_id] -= 1
                raise TimeoutError("The model timed out.")
            client.post(
                f"/api/claims/{claim_id}/decision",
                json={
                    "decision": "decline",
                    "payout": 0,
                    "reasons": "Not covered.",
                    "policy_section": "Section 9, General exclusions",
                },
            )
        finally:
            client.post(f"/api/claims/{claim_id}/release")
        return "Declined."

    monkeypatch.setattr(agents, "work_claim", work_claim)
    return failures, worked


def run_queue(cap, *replies):
    model = stub(*replies)
    return list(agents.run_queue(cap, model)), model


def test_the_todo_list_is_stored(stand_in):
    run_queue(
        1,
        todos(("CLM-1001 Flight home canceled", "todo"), ("Check the queue", "todo")),
        work("CLM-1001"),
        todos(("CLM-1001 Flight home canceled", "done"), ("Check the queue", "todo")),
        AIMessage(content="Worked one claim."),
    )
    run = latest_run()
    assert run["todos"] == [
        {"text": "CLM-1001 Flight home canceled", "status": "done"},
        {"text": "Check the queue", "status": "todo"},
    ]
    assert run["status"] == "finished"
    assert run["summary"] == "Worked one claim."


def test_the_run_keeps_to_its_cap(stand_in):
    lines, _ = run_queue(
        2,
        work("CLM-1001"),
        work("CLM-1002"),
        work("CLM-1003"),
        AIMessage(content="Worked two claims."),
    )
    assert lines == [
        "CLM-1001: declined.",
        "CLM-1002: declined.",
        "Refused: this run is capped at 2 claims and has worked 2.",
        "Worked two claims.",
    ]
    assert stand_in[1] == ["CLM-1001", "CLM-1002"]
    waiting = [claim_id for claim_id, s in statuses().items() if s == "waiting"]
    assert len(waiting) == 23
    assert "CLM-1003" in waiting


def test_a_failed_claim_does_not_stop_the_run(stand_in):
    stand_in[0]["CLM-1001"] = 2
    lines, _ = run_queue(
        2,
        work("CLM-1001"),
        work("CLM-1002"),
        work("CLM-1001"),
        AIMessage(content="One declined, one left for a handler."),
    )
    assert lines[0] == "CLM-1001: failed (The model timed out), back in the queue."
    assert lines[1] == "CLM-1002: declined."
    assert claim("CLM-1002")["status"] == "decided"
    assert latest_run()["summary"] == "One declined, one left for a handler."


def test_a_failed_claim_is_tried_again_outside_the_cap(stand_in):
    stand_in[0]["CLM-1001"] = 1
    lines, _ = run_queue(
        2,
        work("CLM-1001"),
        work("CLM-1002"),
        work("CLM-1001"),
        AIMessage(content="Two declined."),
    )
    assert lines[2] == "CLM-1001: declined."
    assert claim("CLM-1001")["status"] == "decided"
    assert claim("CLM-1001")["failures"] == 1


def test_a_claim_that_fails_twice_is_left_for_a_handler(stand_in):
    stand_in[0]["CLM-1001"] = 2
    lines, model = run_queue(
        1, work("CLM-1001"), work("CLM-1001"), AIMessage(content="Left one.")
    )
    assert lines[1] == (
        "CLM-1001: failed twice (The model timed out), left for a handler."
    )
    assert claim("CLM-1001")["status"] == "left_for_handler"
    assert not any(
        isinstance(message, HumanMessage) for message in model.prompts[-1][2:]
    )


def test_the_agent_is_sent_back_for_a_missed_claim(stand_in):
    lines, model = run_queue(
        2,
        work("CLM-1001"),
        AIMessage(content="Finished early."),
        work("CLM-1002"),
        AIMessage(content="Worked two claims."),
    )
    reminder = model.prompts[2][-1]
    assert isinstance(reminder, HumanMessage)
    assert str(reminder.content).startswith(
        "Before you finish: CLM-1002 is still waiting and within this run's cap."
    )
    assert claim("CLM-1002")["status"] == "decided"
    assert lines[-1] == "Worked two claims."


def test_a_run_at_its_cap_finishes_at_once(stand_in):
    lines, model = run_queue(1, work("CLM-1001"), AIMessage(content="Done."))
    assert len(model.prompts) == 2
    assert lines == ["CLM-1001: declined.", "Done."]


def test_the_waiting_claims_are_listed_with_the_cap(stand_in):
    stand_in[0]["CLM-1001"] = 1
    _, model = run_queue(
        2,
        work("CLM-1001"),
        call("list_waiting_claims"),
        work("CLM-1001"),
        work("CLM-1002"),
        AIMessage(content="Done."),
    )
    listing = model.prompts[2][-1]
    assert isinstance(listing, ToolMessage)
    lines = str(listing.content).splitlines()
    assert lines[0] == (
        "25 claims waiting. This run is capped at 2 claims and has worked 1."
    )
    assert lines[1].startswith("CLM-1001, submitted 2026-06-24, trip_delay, $420.00")
    assert lines[1].endswith("(back in the queue after a failure)")
    assert not lines[2].endswith("(back in the queue after a failure)")


@pytest.mark.parametrize(
    ("record", "line"),
    [
        (
            {
                "status": "decided",
                "decision": {"decision": "approve", "payout": 420},
                "payment": {"amount": 420},
            },
            "CLM-1001: approved, $420.00, paid.",
        ),
        (
            {
                "status": "decided",
                "decision": {"decision": "approve", "payout": 420},
                "payment": None,
            },
            "CLM-1001: approved, $420.00, not paid.",
        ),
        (
            {
                "status": "awaiting_sign_off",
                "decision": {"decision": "approve", "payout": 1150},
                "payment": None,
            },
            "CLM-1001: approved, $1,150.00, waiting for sign-off.",
        ),
        (
            {"status": "decided", "decision": {"decision": "decline"}},
            "CLM-1001: declined.",
        ),
        (
            {"status": "decided", "decision": {"decision": "refer"}},
            "CLM-1001: referred.",
        ),
        (
            {"status": "waiting", "decision": None},
            "CLM-1001: failed (the run ended without a decision), back in the queue.",
        ),
    ],
)
def test_report(record, line):
    assert tools.report({"claim_id": "CLM-1001", **record}, None) == line


def test_report_a_claim_left_for_a_handler():
    record = {"claim_id": "CLM-1001", "status": "left_for_handler", "decision": None}
    assert tools.report(record, TimeoutError("The model timed out.")) == (
        "CLM-1001: failed twice (The model timed out), left for a handler."
    )


def test_work_claim_refuses_a_claim_that_is_not_waiting(stand_in):
    client.post("/api/claims/CLM-1001/start")
    _, model = run_queue(
        5, work("CLM-1001"), work("CLM-9999"), AIMessage(content="Done.")
    )
    replies = [m.content for m in model.prompts[2] if isinstance(m, ToolMessage)]
    assert replies == [
        "Refused: CLM-1001 is in progress, so it can't be worked.",
        "Refused: There is no record CLM-9999.",
    ]
    assert "CLM-1001" not in stand_in[1]


def test_both_agents_on_one_claim():
    model = stub(
        call("list_waiting_claims"),
        todos(("CLM-1001", "in_progress")),
        work("CLM-1001"),
        call("get_claim", claim_id="CLM-1001"),
        *approve_and_pay("CLM-1001", 420.0),
        todos(("CLM-1001", "done")),
        AIMessage(content="Approved and paid CLM-1001."),
    )
    lines = list(agents.run_queue(1, model))
    assert lines == [
        "CLM-1001: approved, $420.00, paid.",
        "Approved and paid CLM-1001.",
    ]
    steps = client.get("/api/claims/CLM-1001/steps").json()
    assert steps[0]["text"] == "get_claim(claim_id='CLM-1001')"
    assert steps[-1]["text"] == "Approved CLM-1001."
    instructions = client.get("/api/instructions/queue").text
    queue_prompts = [p for p in model.prompts if p[0].content == instructions]
    assert len(queue_prompts) == 5
    last = queue_prompts[-1]
    reports = [m for m in last if isinstance(m, ToolMessage) and m.name == "work_claim"]
    assert [m.content for m in reports] == ["CLM-1001: approved, $420.00, paid."]
    assert not any("Meridian Air" in str(m.content) for m in last)
    run = latest_run()
    assert run["status"] == "finished"
    assert run["summary"] == "Approved and paid CLM-1001."
    assert run["todos"] == [{"text": "CLM-1001", "status": "done"}]


def test_a_queue_agent_whose_model_fails_still_finishes_the_run():
    lines = list(agents.run_queue(5, BrokenModel(messages=iter([]))))
    assert lines == ["The run stopped: The model is unavailable."]
    run = latest_run()
    assert run["status"] == "finished"
    assert run["summary"] == "The run stopped: The model is unavailable."
    assert "in_progress" not in statuses().values()


def test_a_queue_agent_that_never_finishes_stops_at_its_limit(stand_in):
    model = stub(
        *(call("list_waiting_claims") for _ in range(agents.queue_call_limit(1) + 1))
    )
    lines = list(agents.run_queue(1, model))
    assert lines[-1].startswith("The run stopped: Model call limits exceeded")
    assert latest_run()["status"] == "finished"


@pytest.fixture
def command(monkeypatch):
    runs = []

    def run_queue(cap):
        runs.append(cap)
        yield "CLM-1001: declined."
        yield "Worked one claim."

    monkeypatch.setattr(agents, "run_queue", run_queue)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda **kwargs: None)

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["agent", *args])
        runpy.run_module("agent", run_name="__main__")

    return run, runs


def test_command_caps_the_run_at_the_waiting_claims(command, capsys):
    run, runs = command
    run()
    assert runs == [25]
    assert capsys.readouterr().out == "CLM-1001: declined.\nWorked one claim.\n"


def test_command_with_a_cap(command):
    run, runs = command
    run("--cap", "5")
    assert runs == [5]


def test_command_with_nothing_waiting(command, monkeypatch, capsys):
    run, runs = command
    monkeypatch.setattr(api, "waiting_claims", list)
    run()
    assert runs == []
    assert capsys.readouterr().out == "No claims are waiting.\n"


def test_command_runs_one_claim(command, monkeypatch, capsys):
    run, runs = command
    monkeypatch.setattr(agents, "work_claim", lambda claim_id: f"{claim_id} done.")
    run("CLM-1001")
    assert runs == []
    assert capsys.readouterr().out == "CLM-1001 done.\n"
