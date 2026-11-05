import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from backend import db
from backend.main import app

client = TestClient(app)


def create(claim_set="held_out", tiny=True):
    response = client.post(
        "/api/evaluations",
        json={
            "agents_at_once": 5,
            "claim_set": claim_set,
            "tiny": tiny,
            "model": "openai/gpt-6-luna",
            "reasoning_effort": "low",
        },
    )
    return response.json()["run_id"]


def copies(run_id):
    return client.get(f"/api/runs/{run_id}/claims").json()


def answer_key(run_id):
    """The handler's decision and payout on each copy, in order."""
    with db.engine().connect() as conn:
        rows = conn.execute(
            select(
                db.claims.c.claim_id,
                db.handler_decisions.c.decision,
                db.handler_decisions.c.payout,
            )
            .join(
                db.handler_decisions,
                db.handler_decisions.c.claim_id == db.claims.c.source_claim_id,
            )
            .where(db.claims.c.evaluation_id == run_id)
        ).all()
    return {claim_id: (decision, payout) for claim_id, decision, payout in rows}


def decide(claim_id, decision, payout=0.0):
    client.post(f"/api/claims/{claim_id}/start").raise_for_status()
    client.post(
        f"/api/claims/{claim_id}/decision",
        json={
            "decision": decision,
            "payout": payout,
            "reasons": "The agent's reasons.",
            "policy_section": "Section 3, Trip Delay",
        },
    ).raise_for_status()


def spend(claim_id, run_id, cost, seconds, span):
    started, finished = span
    client.post(
        f"/api/claims/{claim_id}/model-calls",
        json={
            "run_id": run_id,
            "input_tokens": 10,
            "output_tokens": 1,
            "cost": cost,
            "seconds": 1.0,
        },
    ).raise_for_status()
    client.post(
        f"/api/claims/{claim_id}/work",
        json={
            "run_id": run_id,
            "started_at": f"2026-11-05T10:00:{started:02d}+00:00",
            "finished_at": f"2026-11-05T10:00:{finished:02d}+00:00",
            "seconds": seconds,
        },
    ).raise_for_status()


def finish(run_id, status="finished"):
    client.post(f"/api/runs/{run_id}/finish", json={"status": status})


def test_the_agent_agreeing_with_every_handler_is_a_clean_sheet():
    run_id = create()
    for claim_id, (decision, payout) in answer_key(run_id).items():
        decide(claim_id, decision, payout)
    finish(run_id)
    measures = client.get(f"/api/evaluations/{run_id}").json()["measures"]
    assert measures["decision_accuracy"] == 1
    assert measures["accuracy"] == 1
    assert measures["cost_of_mistakes"] == 0


def test_a_claim_with_no_decision_counts_as_a_referral_in_every_measure():
    run_id = create()
    key = answer_key(run_id)
    finish(run_id)
    evaluation = client.get(f"/api/evaluations/{run_id}").json()
    referred = [claim for claim, (decision, _) in key.items() if decision == "refer"]
    others = len(key) - len(referred)
    assert evaluation["measures"]["straight_through_rate"] == 0
    assert evaluation["measures"]["decision_accuracy"] == len(referred) / len(key)
    assert evaluation["measures"]["cost_of_mistakes"] == 50 * others
    assert all(claim["agent_decision"] is None for claim in evaluation["claims"])


def test_cost_and_time_are_worked_out_from_the_records():
    run_id = create()
    first, second, *_ = copies(run_id)
    spend(first, run_id, 0.5, 10.0, (0, 10))
    spend(first, run_id, 0.25, 5.0, (12, 17))
    spend(second, run_id, 0.25, 30.0, (5, 35))
    spend(second, "dispatch-other", 9.0, 90.0, (0, 59))
    spend(second, None, 9.0, 90.0, (0, 59))
    finish(run_id)
    measures = client.get(f"/api/evaluations/{run_id}").json()["measures"]
    assert measures["cost_per_claim"] == pytest.approx(1.0 / 5)
    assert measures["seconds_per_claim"] == pytest.approx(45.0 / 5)
    assert measures["run_seconds"] == 35
    [listed] = client.get("/api/evaluations").json()
    assert listed["measures"] == measures


def test_list_evaluations_is_newest_first_and_a_stopped_one_shows_as_stopped():
    older = create()
    newer = create("evaluation", False)
    client.post("/api/runs/stop")
    listed = client.get("/api/evaluations").json()
    assert [run["run_id"] for run in listed] == [newer, older]
    assert listed[0]["status"] == "stopped"
    assert listed[0]["claim_count"] == 25
    assert listed[1]["claim_count"] == 5
    assert listed[0]["claim_set"] == "evaluation"
    assert listed[0]["tiny"] is False
    assert listed[0]["measures"]["straight_through_rate"] == 0


def count_queries():
    executed = []

    def counted(*details):
        executed.append(details)

    engine = db.engine()
    event.listen(engine, "before_cursor_execute", counted)
    client.get("/api/evaluations").raise_for_status()
    event.remove(engine, "before_cursor_execute", counted)
    return len(executed)


def test_list_evaluations_takes_the_same_queries_for_2_as_for_10():
    for _ in range(2):
        create()
    few = count_queries()
    for _ in range(8):
        create()
    assert count_queries() == few


def test_an_evaluation_has_each_claim_beside_the_handlers_in_order():
    run_id = create()
    key = answer_key(run_id)
    first, second, *_ = copies(run_id)
    decision, payout = key[first]
    decide(first, decision, payout)
    wrong = "decline" if key[second][0] != "decline" else "refer"
    decide(second, wrong)
    spend(first, run_id, 0.5, 10.0, (0, 10))
    evaluation = client.get(f"/api/evaluations/{run_id}").json()
    assert [claim["claim_id"] for claim in evaluation["claims"]] == copies(run_id)
    right, missed = evaluation["claims"][:2]
    assert right["handler_decision"] == decision
    assert right["handler_payout"] == payout
    assert right["agent_decision"] == decision
    assert right["agent_payout"] == payout
    assert right["reason"] == "The agent's reasons."
    assert right["cost"] == 0.5
    assert right["seconds"] == 10
    assert right["miss"] is False
    assert right["mistake_cost"] == 0
    assert right["source_claim_id"].startswith("CLM-11")
    assert missed["agent_decision"] == wrong
    assert missed["miss"] is True
    assert missed["mistake_cost"] > 0
    assert evaluation["model"] == "openai/gpt-6-luna"
    assert evaluation["reasoning_effort"] == "low"


def test_an_unknown_evaluation():
    assert client.get("/api/evaluations/evaluation-0").status_code == 404


def test_a_dispatch_isnt_an_evaluation():
    dispatch = client.post(
        "/api/dispatches",
        json={
            "agents_at_once": 3,
            "model": "openai/gpt-6-luna",
            "reasoning_effort": "low",
        },
    ).json()
    assert client.get(f"/api/evaluations/{dispatch['run_id']}").status_code == 404
