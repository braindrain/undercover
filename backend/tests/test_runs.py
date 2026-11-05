from fastapi.testclient import TestClient
from sqlalchemy import event, update

from backend import db
from backend.main import app

client = TestClient(app)


def create(agents_at_once=3):
    return client.post(
        "/api/dispatches",
        json={
            "agents_at_once": agents_at_once,
            "model": "openai/gpt-6-luna",
            "reasoning_effort": "low",
        },
    )


def finish(run_id, status="finished"):
    return client.post(f"/api/runs/{run_id}/finish", json={"status": status})


def claims_of(run_id):
    return client.get(f"/api/runs/{run_id}/claims").json()


def test_create_a_dispatch():
    run = create(7).json()
    assert run["run_id"].startswith("dispatch-")
    assert run["kind"] == "dispatch"
    assert run["agents_at_once"] == 7
    assert run["model"] == "openai/gpt-6-luna"
    assert run["reasoning_effort"] == "low"
    assert run["status"] == "running"
    assert run["finished_at"] is None


def test_a_dispatch_id_is_new_every_time():
    assert create().json()["run_id"] != create().json()["run_id"]


def test_a_dispatch_works_from_1_to_25_at_once():
    assert create(0).status_code == 422
    assert create(26).status_code == 422
    assert create(1).status_code == 200
    assert create(25).status_code == 200


def test_a_dispatch_chooses_claims_oldest_first_with_ties_by_id():
    queue = client.get("/api/claims").json()
    expected = sorted(queue, key=lambda c: (c["submitted_on"], c["claim_id"]))
    run_id = create().json()["run_id"]
    assert claims_of(run_id) == [claim["claim_id"] for claim in expected]


def test_a_dispatch_includes_claims_in_progress_and_leaves_the_rest():
    client.post("/api/claims/CLM-1001/start")
    with db.engine().begin() as conn:
        conn.execute(
            update(db.claims)
            .where(db.claims.c.claim_id.in_(["CLM-1002", "CLM-1003", "CLM-1004"]))
            .values(status="decided")
        )
        conn.execute(
            update(db.claims)
            .where(db.claims.c.claim_id == "CLM-1005")
            .values(status="awaiting_sign_off")
        )
        conn.execute(
            update(db.claims)
            .where(db.claims.c.claim_id == "CLM-1006")
            .values(status="left_for_handler")
        )
    chosen = claims_of(create().json()["run_id"])
    assert "CLM-1001" in chosen
    assert len(chosen) == 20
    assert not {"CLM-1002", "CLM-1005", "CLM-1006"} & set(chosen)


def test_the_claims_of_an_unknown_run():
    assert client.get("/api/runs/dispatch-0/claims").status_code == 404


def test_finish_a_run():
    run_id = create().json()["run_id"]
    run = finish(run_id).json()
    assert run["status"] == "finished"
    assert run["finished_at"] is not None


def test_a_run_can_end_as_stopped_and_nothing_else():
    run_id = create().json()["run_id"]
    assert finish(run_id, "running").status_code == 422
    assert finish(run_id, "stopped").json()["status"] == "stopped"


def test_a_run_that_has_ended_cant_end_again():
    run_id = create().json()["run_id"]
    finish(run_id)
    response = finish(run_id)
    assert response.status_code == 409
    assert response.json()["detail"] == f"Run {run_id} has already ended."


def test_an_unknown_run_cant_finish():
    assert finish("dispatch-0").status_code == 404


def test_stop_running_runs():
    ended = create().json()["run_id"]
    finish(ended)
    running = create().json()["run_id"]
    assert client.post("/api/runs/stop").json() == [running]
    latest = client.get("/api/dispatches/latest").json()
    assert latest["run_id"] == running
    assert latest["status"] == "stopped"
    assert latest["finished_at"] is not None
    assert client.post("/api/runs/stop").json() == []


def test_get_latest_dispatch():
    assert client.get("/api/dispatches/latest").json() is None
    create()
    newest = create().json()
    client.post("/api/claims/CLM-1001/start")
    latest = client.get("/api/dispatches/latest").json()
    assert latest["run_id"] == newest["run_id"]
    assert len(latest["claims"]) == 25
    [first] = [c for c in latest["claims"] if c["claim_id"] == "CLM-1001"]
    assert first["status"] == "in_progress"
    assert first["summary"].startswith("Flight home canceled")
    assert first["decision"] is None


def call(claim_id, run_id, cost, seconds=2.0):
    client.post(
        f"/api/claims/{claim_id}/model-calls",
        json={
            "run_id": run_id,
            "input_tokens": 1000,
            "output_tokens": 100,
            "cost": cost,
            "seconds": seconds,
        },
    ).raise_for_status()


def work(claim_id, run_id, started_at, finished_at, seconds):
    client.post(
        f"/api/claims/{claim_id}/work",
        json={
            "run_id": run_id,
            "started_at": f"2026-11-05T10:{started_at}+00:00",
            "finished_at": f"2026-11-05T10:{finished_at}+00:00",
            "seconds": seconds,
        },
    ).raise_for_status()


def decide(claim_id, decision="approve", payout=420.0):
    client.post(f"/api/claims/{claim_id}/start").raise_for_status()
    client.post(
        f"/api/claims/{claim_id}/decision",
        json={
            "decision": decision,
            "payout": payout,
            "reasons": "The reasons.",
            "policy_section": "Section 3, Trip Delay",
        },
    ).raise_for_status()


def test_the_summary_is_built_from_the_records():
    run_id = create().json()["run_id"]
    decide("CLM-1001")
    client.post("/api/claims/CLM-1001/payments", json={"amount": 420.0})
    decide("CLM-1002", payout=1150.0)
    decide("CLM-1003", "decline", 0)
    decide("CLM-1004", "refer", 0)
    client.post("/api/claims/CLM-1005/start")
    for _ in range(2):
        client.post("/api/claims/CLM-1006/start")
        client.post("/api/claims/CLM-1006/release")
    finish(run_id)
    assert client.get("/api/dispatches/latest").json()["summary"] == {
        "approved": 2,
        "declined": 1,
        "referred": 1,
        "awaiting_sign_off": [{"claim_id": "CLM-1002", "payout": 1150.0}],
        "left_for_handler": ["CLM-1006"],
        "in_progress": ["CLM-1005"],
    }


def test_a_payout_signed_off_after_the_run_no_longer_shows_as_waiting():
    run_id = create().json()["run_id"]
    decide("CLM-1002", payout=1150.0)
    finish(run_id)
    client.post("/api/claims/CLM-1002/sign-off", json={"handler_id": "HND-01"})
    client.post("/api/claims/CLM-1002/payments", json={"amount": 1150.0})
    summary = client.get("/api/dispatches/latest").json()["summary"]
    assert summary["awaiting_sign_off"] == []
    assert summary["approved"] == 1


def test_a_runs_totals_are_its_own():
    first = create().json()["run_id"]
    second = create().json()["run_id"]
    call("CLM-1001", first, 0.25)
    call("CLM-1001", first, 0.5)
    work("CLM-1001", first, "00:00", "00:30", 30)
    call("CLM-1001", None, 8.0)
    work("CLM-1001", None, "05:00", "06:00", 60)
    call("CLM-1001", second, 2.0, seconds=3.0)
    call("CLM-1002", second, 1.0)
    work("CLM-1001", second, "10:00", "10:20", 20)
    work("CLM-1001", second, "11:00", "11:10", 10)
    work("CLM-1002", second, "10:10", "10:50", 40)

    latest = client.get("/api/dispatches/latest").json()
    assert latest["run_id"] == second
    assert latest["model_cost"] == 3.0
    assert latest["work_seconds"] == 70
    assert latest["run_seconds"] == 70
    claims = {claim["claim_id"]: claim for claim in latest["claims"]}
    assert claims["CLM-1001"]["cost"] == 2.0
    assert claims["CLM-1001"]["seconds"] == 30
    assert claims["CLM-1001"]["model_seconds"] == 3.0
    assert claims["CLM-1001"]["started_at"] == "2026-11-05T10:10:00+00:00"
    assert claims["CLM-1001"]["finished_at"] == "2026-11-05T10:11:10+00:00"
    assert claims["CLM-1003"]["cost"] == 0
    assert claims["CLM-1003"]["started_at"] is None


def test_a_run_with_no_work_yet_has_no_time():
    create()
    latest = client.get("/api/dispatches/latest").json()
    assert latest["run_seconds"] == 0
    assert latest["model_cost"] == 0


def count_queries():
    """Count the statements one read of the latest dispatch makes."""
    executed = []

    def counted(*details):
        executed.append(details)

    engine = db.engine()
    event.listen(engine, "before_cursor_execute", counted)
    client.get("/api/dispatches/latest").raise_for_status()
    event.remove(engine, "before_cursor_execute", counted)
    return len(executed)


def test_a_poll_takes_the_same_queries_for_25_claims_as_for_2():
    with db.engine().begin() as conn:
        conn.execute(
            update(db.claims)
            .where(db.claims.c.claim_id.not_in(["CLM-1001", "CLM-1002"]))
            .values(status="decided")
        )
    create()
    few = count_queries()
    with db.engine().begin() as conn:
        conn.execute(update(db.claims).values(status="waiting"))
    run_id = create().json()["run_id"]
    assert len(claims_of(run_id)) == 25
    assert count_queries() == few
